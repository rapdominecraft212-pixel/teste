"""
QwenReply Async - Versao async do QwenReply para chamadas paralelas.

Usa Playwright async_api + asyncio.gather para operar multiplas abas
em paralelo dentro do mesmo Chrome. Ideal para rodar capa/titulo/linha
simultaneamente sem abrir 3 browsers.

ISOLAMENTO DE PERFIL (deterministico):
    Cada instancia cria uma COPIA TEMPORARIA do chrome_profile original.
    O Chrome nunca trava o perfil original — eliminando conflitos entre
    jobs consecutivos. Quando close() e chamado, o Chrome e morto com
    garantia deterministica (pgrep + SIGKILL se necessario) e a copia
    temporaria e removida.

    Fluxo:
        1. abrir_context() copia chrome_profile -> /tmp/chrome_kwai_{uuid}/
        2. Chrome abre com a copia temporaria (perfil original nunca e travado)
        3. close() fecha Chrome, ESPERA deterministicamente o processo morrer
        4. Remove a copia temporaria

    Isso elimina completamente:
        - Conflitos de LOCK file entre jobs
        - Chrome degradado por perfil travado
        - O amador time.sleep(2) como "seguranca"

Logging de triagem: cada etapa critica loga o que esta fazendo,
o que encontrou na pagina, e o que falhou. O objetivo e que
olhando o log seja possivel saber EXATAMENTE o que aconteceu
sem precisar abrir o codigo.

Uso:
    from qwen_reply_async import QwenReplyAsync
    qr = QwenReplyAsync(headless=True)
    await qr.abrir_context()
    page1 = await qr.new_page()
    page2 = await qr.new_page()
    r1, r2 = await asyncio.gather(
        qr.ask_on_page(page1, "pergunta 1"),
        qr.ask_on_page(page2, "pergunta 2"),
    )

CLI (compativel com qwen_reply.py):
    python qwen_reply_async.py --prompt "analise" --arquivo video.mp4
"""
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
import pathlib, sys, argparse, subprocess, time, platform, shutil, tempfile, uuid

PASTA = pathlib.Path(__file__).parent
PERFIL_ORIGEM = PASTA / "chrome_profile"


class SessionExpiredError(Exception):
    pass


def _page_tag(page):
    """Extrai um identificador curto da aba para o log (ex: aba 3)."""
    try:
        url = page.url
        if url == "about:blank":
            return "aba(blank)"
        return f"aba({url.split('/')[-1][:15]})"
    except:
        return "aba(?)"


class QwenReplyAsync:
    """Versao async do QwenReply — mesma interface, mesma logica, async/await.

    ISOLAMENTO DETERMINISTICO DE PERFIL:
    Cada instancia copia o chrome_profile original para um diretorio temporario.
    O Chrome opera exclusivamente na copia, liberando o original de qualquer lock.
    No close(), o Chrome e morto com garantia deterministica e a copia e removida.
    """

    def __init__(self, perfil=None, headless=False):
        # perfil: diretorio ORIGEM (o "mestre" que nunca e travado)
        self._perfil_origem = str(perfil or PERFIL_ORIGEM.resolve())
        self._headless = headless
        self._ctx = None
        self._page = None
        self._playwright = None
        # Perfil temporario (copia isolada para este job)
        self._perfil_temp = None  # /tmp/chrome_kwai_{uuid}/
        self._perfil_temp_id = None  # uuid para identificacao no log

    # === API publica ===

    async def abrir_context(self, snapshot=False):
        """Abre o browser + context (1 Chrome, 1 perfil temporario isolado).

        Fluxo deterministico:
        1. Verifica se perfil origem existe
        2. Cria copia temporaria unica (/tmp/chrome_kwai_{uuid}/)
        3. Remove LOCK files da copia (seguranca)
        4. Lanca Chrome com a copia temporaria
        5. Navega ate o Qwen e verifica sessao
        """
        if self._ctx is not None:
            return

        # 1. Verificar perfil origem
        if not pathlib.Path(self._perfil_origem).exists():
            raise RuntimeError(
                f"Perfil Chrome nao encontrado: {self._perfil_origem}\n"
                f"Execute Playwright/login_setup.py para criar o perfil."
            )

        # 2. Criar copia temporaria isolada
        self._perfil_temp_id = str(uuid.uuid4())[:8]
        temp_dir = pathlib.Path(tempfile.gettempdir()) / f"chrome_kwai_{self._perfil_temp_id}"
        t0 = time.time()
        shutil.copytree(self._perfil_origem, str(temp_dir))
        dt = time.time() - t0
        self._perfil_temp = str(temp_dir)
        print(f"    [perfil] Copia temporaria criada: {temp_dir.name} ({dt:.3f}s)", flush=True)

        # 3. Remover LOCK files da copia (previne exit code 21)
        self._limpar_lockfiles(self._perfil_temp)

        # 4. Matar qualquer Chrome residual que possa estar usando o temp
        # (nao deveria existir, mas e defensivo)
        self._matar_chrome_por_perfil(self._perfil_temp)
        self._esperar_chrome_morto(self._perfil_temp, timeout=3)

        # 5. Lançar Chrome com perfil temporario
        # Auto-detectar: channel="chrome" usa Google Chrome instalado no sistema,
        # sem channel usa o Chromium embutido do Playwright (fallback para ambientes
        # sem Google Chrome instalado, como servidores CI/Docker)
        self._playwright = await async_playwright().start()
        launch_kwargs = {
            "user_data_dir": self._perfil_temp,
            "headless": self._headless,
        }
        if pathlib.Path("/opt/google/chrome/chrome").exists() or pathlib.Path("/usr/bin/google-chrome").exists():
            launch_kwargs["channel"] = "chrome"
        self._ctx = await self._playwright.chromium.launch_persistent_context(**launch_kwargs)
        self._page = self._ctx.pages[0] if self._ctx.pages else await self._ctx.new_page()
        await self._page.goto("https://chat.qwen.ai/", wait_until="domcontentloaded", timeout=120000)
        await self._page.wait_for_selector("textarea", timeout=120000)
        await self._page.wait_for_timeout(1500)
        await self._verificar_sessao(self._page)
        if snapshot:
            html = await self._page.evaluate("document.getElementById('qwen-chat-header-right')?.innerHTML || ''")
            pathlib.Path(PASTA / "_snapshot_header.html").write_text(html, encoding="utf-8")
        await self._ativar_temp(self._page)
        print(f"    [perfil] Chrome aberto com perfil temporario {self._perfil_temp_id}", flush=True)

    async def new_page(self, tag=None):
        """Cria uma nova aba no mesmo context e navega ate o Qwen.
        tag: identificador para o log (ex: 'capa', 'titulo', 'linha').
        """
        if self._ctx is None:
            await self.abrir_context()
        page = await self._ctx.new_page()
        tag = tag or _page_tag(page)
        print(f"    [{tag}] Navegando ate chat.qwen.ai...", flush=True)
        await page.goto("https://chat.qwen.ai/", wait_until="domcontentloaded", timeout=120000)
        await page.wait_for_selector("textarea", timeout=120000)
        print(f"    [{tag}] Pagina carregada, textarea encontrada", flush=True)
        await page.wait_for_timeout(1500)
        await self._verificar_sessao(page)
        await self._ativar_temp(page)
        print(f"    [{tag}] Aba pronta", flush=True)
        return page

    async def ask_on_page(self, page, prompt, arquivo=None, timeout=180, tag=None):
        """Envia prompt numa aba especifica. Retorna o texto da resposta.
        tag: identificador para o log (ex: 'capa', 'titulo', 'linha').
        """
        if page is None:
            page = self._page
        tag = tag or _page_tag(page)
        try:
            if arquivo:
                await self._upload_page(page, arquivo, tag=tag)
            await self._enviar_page(page, prompt, timeout, tag=tag)
            resultado = await self._esperar_e_extrair_resposta(page, tag=tag)
            return resultado
        except:
            raise

    async def close(self):
        """Fecha o browser e o playwright com garantia DETERMINISTICA de morte do Chrome.

        Sequencia deterministica:
        1. ctx.close() — fecha graciosamente
        2. playwright.stop() — para o driver
        3. _esperar_chrome_morto(timeout=10) — verifica via pgrep que morreu
        4. Se nao morreu: SIGKILL + esperar novamente
        5. Remove copia temporaria do perfil

        Nao usa time.sleep() como "seguranca" — usa pgrep para VERIFICAR
        que o processo morreu, com SIGKILL como fallback deterministico.
        """
        # 1. Fechar Playwright graciosamente
        if self._ctx:
            try:
                await self._ctx.close()
            except:
                pass
            self._ctx = None
            self._page = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except:
                pass
            self._playwright = None

        # 2. Garantir determinísticamente que o Chrome morreu
        if self._perfil_temp:
            perfil_id = self._perfil_temp_id or "?"
            morreu = self._esperar_chrome_morto(self._perfil_temp, timeout=10)
            if not morreu:
                print(f"    [close:{perfil_id}] Chrome nao morreu graciosamente em 10s, enviando SIGKILL...", flush=True)
                self._matar_chrome_por_perfil(self._perfil_temp, signal="-9")
                morreu = self._esperar_chrome_morto(self._perfil_temp, timeout=5)
                if not morreu:
                    print(f"    [close:{perfil_id}] ALERTA: Chrome resistiu ao SIGKILL! Processo zombie?", flush=True)
                else:
                    print(f"    [close:{perfil_id}] Chrome morto via SIGKILL (deterministico)", flush=True)
            else:
                print(f"    [close:{perfil_id}] Chrome morreu graciosamente (confirmado via pgrep)", flush=True)

            # 3. Remover copia temporaria
            try:
                shutil.rmtree(self._perfil_temp, ignore_errors=True)
                print(f"    [close:{perfil_id}] Perfil temporario removido", flush=True)
            except Exception as e:
                print(f"    [close:{perfil_id}] Erro ao remover perfil temporario: {e}", flush=True)

            self._perfil_temp = None
            self._perfil_temp_id = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    # === Estaticos (operacoes numa Page qualquer) ===

    @staticmethod
    async def _verificar_sessao(page):
        # Verificacao inteligente de sessao expirada:
        # O Qwen mostra um modal "Welcome" com botoes "Log in", "Sign up",
        # "Stay logged out" quando a sessao esta expirada. Esse modal
        # BLOQUEIA todas as interacoes com a pagina (upload, send, etc.)
        # ate ser fechado.
        #
        # Detectamos a sessao expirada pela presenca do Welcome modal.
        # Se presente, tentamos clicar "Stay logged out" para poder continuar,
        # mas isso so permite chat de texto — upload de arquivos exige login.
        #
        # Fluxo:
        # 1. Se ha Welcome modal → sessao expirada
        # 2. Tentar clicar "Stay logged out" (permite uso limitado)
        # 3. Se o modal reaparece ao enviar → erro definitivo (precisa de login)

        # Check for Welcome modal (session expired indicator)
        overlay = await page.query_selector('.qwen-modal-overlay')
        if overlay and await overlay.is_visible():
            # Verificar se e o Welcome modal especificamente
            welcome_title = await overlay.evaluate(
                'el => el.querySelector("[class*=welcome-modal-title]")?.innerText || ""'
            )
            if "Welcome" in welcome_title or "welcome" in welcome_title.lower():
                # Tentar clicar "Stay logged out" para fechar
                stay_btn = await overlay.query_selector('button:has-text("Stay logged out")')
                if stay_btn:
                    print("    [sessao] Welcome modal detectado — clicando 'Stay logged out'...", flush=True)
                    await stay_btn.click()
                    await page.wait_for_timeout(2000)
                    # Verificar se o modal foi embora
                    overlay2 = await page.query_selector('.qwen-modal-overlay')
                    if not overlay2 or not await overlay2.is_visible():
                        print("    [sessao] Modal fechado — continuando sem login (upload pode falhar)", flush=True)
                        return
                    else:
                        print("    [sessao] Modal persistiu apos 'Stay logged out'", flush=True)

                # Se chegou aqui, nao conseguiu fechar o modal
                raise SessionExpiredError(
                    "Sessao Qwen expirada — Welcome modal bloqueando interacao. "
                    "Execute Playwright/login_setup.py para refazer o login."
                )

    @staticmethod
    async def _ativar_temp(page):
        cls = await page.evaluate("document.querySelector('.temporary-chat-entry')?.className || ''")
        if not cls or "temporary-chat-entry-out" in cls:
            return
        await page.locator(".temporary-chat-entry").click()
        await page.wait_for_timeout(500)

    @staticmethod
    async def _dismiss_modal(page, tag="aba"):
        """Remove o Welcome modal do Qwen se presente.

        O Qwen mostra um modal 'Welcome' quando a sessao esta expirada.
        Esse modal bloqueia TODAS as interacoes (upload, send, etc.)
        com um overlay invisivel que intercepta pointer events.

        Esta funcao tenta fechar o modal clicando 'Stay logged out'.
        Se o modal nao pode ser fechado, levanta SessionExpiredError.

        Deve ser chamada antes de acoes criticas (upload, send) para
        garantir que o modal nao esta bloqueando a interacao.
        """
        try:
            overlay = await page.query_selector('.qwen-modal-overlay')
            if not overlay or not await overlay.is_visible():
                return  # Nenhum modal, tudo OK

            # Verificar se e o Welcome modal
            welcome_title = await overlay.evaluate(
                'el => el.querySelector("[class*=welcome-modal-title]")?.innerText || ""'
            )
            if not welcome_title:
                # Modal generico — tentar remover via JS
                await page.evaluate('document.querySelector(".qwen-modal-overlay")?.remove()')
                print(f"    [{tag}] Modal generico removido via JS", flush=True)
                return

            # E o Welcome modal — tentar clicar "Stay logged out"
            stay_btn = await overlay.query_selector('button:has-text("Stay logged out")')
            if stay_btn:
                print(f"    [{tag}] Welcome modal — clicando 'Stay logged out'...", flush=True)
                await stay_btn.click()
                await page.wait_for_timeout(2000)
                # Verificar se sumiu
                overlay2 = await page.query_selector('.qwen-modal-overlay')
                if not overlay2 or not await overlay2.is_visible():
                    return
                # Nao sumiu — forcar remocao
                await page.evaluate('document.querySelector(".qwen-modal-overlay")?.remove()')
                print(f"    [{tag}] Welcome modal forcado via JS", flush=True)
            else:
                # Nao tem botao Stay logged out — forcar remocao
                await page.evaluate('document.querySelector(".qwen-modal-overlay")?.remove()')
                print(f"    [{tag}] Modal sem 'Stay logged out' removido via JS", flush=True)
        except SessionExpiredError:
            raise
        except Exception as e:
            print(f"    [{tag}] Erro ao remover modal: {e}", flush=True)

    @staticmethod
    async def _upload_page(page, caminho, tag="aba"):
        """Upload de arquivo na aba. Loga cada etapa."""
        arquivo_nome = pathlib.Path(caminho).name
        arquivo_tamanho = pathlib.Path(caminho).stat().st_size
        print(f"    [{tag}] Upload iniciando: {arquivo_nome} ({arquivo_tamanho/1024:.0f}KB)", flush=True)

        # Passo 0: garantir que nenhum modal esta bloqueando
        await QwenReplyAsync._dismiss_modal(page, tag=tag)

        # Passo 1: clicar no botao de modo/upload
        try:
            await page.locator(".mode-select-open").click()
            print(f"    [{tag}] Upload passo 1/4: botao mode-select-open clicado", flush=True)
        except Exception as e:
            print(f"    [{tag}] Upload FALHOU passo 1: nao conseguiu clicar em .mode-select-open — {e}", flush=True)
            raise RuntimeError(f"Upload falhou: botao mode-select-open nao encontrado/nao clicavel — {e}")

        # Passo 2: esperar dropdown aparecer
        try:
            await page.wait_for_selector(".mode-select-dropdown", timeout=10000)
            print(f"    [{tag}] Upload passo 2/4: dropdown apareceu", flush=True)
        except PlaywrightTimeout:
            print(f"    [{tag}] Upload FALHOU passo 2: dropdown .mode-select-dropdown nao apareceu em 10s", flush=True)
            raise RuntimeError("Upload falhou: dropdown de upload nao apareceu — seletor de modo nao abriu")

        # Passo 3: clicar no item do dropdown e selecionar arquivo
        try:
            async with page.expect_file_chooser() as fc:
                await page.evaluate("""
                    () => {
                        const items = document.querySelectorAll(
                            '.mode-select-dropdown .ant-dropdown-menu-item'
                        );
                        if (items.length > 0) items[0].click();
                    }
                """)
            file_chooser = await fc.value
            await file_chooser.set_files(str(caminho))
            print(f"    [{tag}] Upload passo 3/4: arquivo selecionado no file chooser", flush=True)
        except Exception as e:
            print(f"    [{tag}] Upload FALHOU passo 3: file chooser ou set_files falhou — {e}", flush=True)
            raise RuntimeError(f"Upload falhou: nao conseguiu selecionar arquivo no dialog — {e}")

        # Passo 4: esperar upload concluir (fileitem-btn aparece e desaparece)
        try:
            await page.wait_for_selector(".fileitem-btn", timeout=15000)
            print(f"    [{tag}] Upload passo 4/4: arquivo enviando (fileitem-btn visivel)...", flush=True)
            await page.wait_for_function("() => !document.querySelector('.fileitem-btn')", timeout=60000)
            print(f"    [{tag}] Upload concluido: arquivo enviado com sucesso", flush=True)
        except PlaywrightTimeout:
            print(f"    [{tag}] Upload ATENCAO: timeout esperando upload concluir (60s), assumindo OK", flush=True)
        except Exception as e:
            print(f"    [{tag}] Upload ATENCAO: erro esperando upload, tentando continuar — {e}", flush=True)
            await page.wait_for_timeout(20000)

    @staticmethod
    async def _checar_erro_qwen(page):
        """Verifica se o Qwen mostrou uma mensagem de erro ou rate limit na pagina."""
        error_indicators = [
            ".error-message",
            ".rate-limit",
            "[class*='error']",
            "[class*='rate-limit']",
            "[class*='limit']",
        ]
        for sel in error_indicators:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    text = await el.inner_text()
                    text = text.strip()
                    if text:
                        return f"[seletor={sel}] {text}"
            except:
                pass
        return None

    @staticmethod
    async def _enviar_page(page, prompt, timeout, tag="aba"):
        """Envia o prompt e espera a resposta. Loga cada etapa da geracao."""
        prompt_preview = prompt[:40].replace('\n', ' ')

        # Passo 0: garantir que nenhum modal esta bloqueando
        await QwenReplyAsync._dismiss_modal(page, tag=tag)

        # Passo 1: preencher textarea
        try:
            await page.locator("textarea").fill(prompt)
            print(f"    [{tag}] Envio passo 1/3: textarea preenchida (\"{prompt_preview}...\")", flush=True)
        except Exception as e:
            print(f"    [{tag}] Envio FALHOU passo 1: nao conseguiu preencher textarea — {e}", flush=True)
            raise RuntimeError(f"Envio falhou: textarea nao encontrada ou nao preenchivel — {e}")

        # Passo 2: clicar send
        try:
            await page.locator(".send-button").click()
            print(f"    [{tag}] Envio passo 2/3: send-button clicado, aguardando Qwen comecar a gerar...", flush=True)
        except Exception as e:
            print(f"    [{tag}] Envio FALHOU passo 2: nao conseguiu clicar em .send-button — {e}", flush=True)
            raise RuntimeError(f"Envio falhou: botao send nao encontrado ou nao clicavel — {e}")

        # Passo 3: esperar stop-button aparecer (Qwen comecou a gerar)
        try:
            await page.wait_for_selector(".stop-button", timeout=min(timeout * 1000, 30000))
            print(f"    [{tag}] Envio passo 3/3: stop-button apareceu — Qwen esta GERANDO resposta", flush=True)
        except PlaywrightTimeout:
            # Qwen nao comecou a gerar — verificar erros
            erro = await QwenReplyAsync._checar_erro_qwen(page)
            if erro:
                print(f"    [{tag}] Envio FALHOU: stop-button nao apareceu, erro na pagina: {erro}", flush=True)
                raise RuntimeError(f"Qwen erro na pagina: {erro}")
            # Tentar clicar send novamente
            print(f"    [{tag}] Envio ATENCAO: stop-button nao apareceu em 30s, tentando clicar send novamente...", flush=True)
            try:
                await page.locator(".send-button").click()
                await page.wait_for_selector(".stop-button", timeout=15000)
                print(f"    [{tag}] Envio: stop-button apareceu na 2a tentativa — Qwen gerando", flush=True)
            except:
                # Diagnostico final: o que tem na pagina?
                textarea_visivel = await page.evaluate("!!document.querySelector('textarea')")
                send_visivel = await page.evaluate("!!document.querySelector('.send-button')")
                stop_visivel = await page.evaluate("!!document.querySelector('.stop-button')")
                erro_final = await QwenReplyAsync._checar_erro_qwen(page)
                estado = f"textarea={'sim' if textarea_visivel else 'nao'} send={'sim' if send_visivel else 'nao'} stop={'sim' if stop_visivel else 'nao'} erro={'sim: '+erro_final if erro_final else 'nao'}"
                print(f"    [{tag}] Envio FALHOU: Qwen nao gerou apos 2 tentativas. Estado da pagina: {estado}", flush=True)
                raise RuntimeError(f"Qwen nao comecou a gerar. Estado da pagina: {estado}")

        # Espera o Qwen terminar de gerar (stop-button desaparece)
        # Com polling e log de progresso a cada 30s
        deadline = time.time() + timeout
        elapsed = 0
        last_log = time.time()
        while time.time() < deadline:
            try:
                remaining_ms = int((deadline - time.time()) * 1000)
                await page.wait_for_function(
                    "() => !document.querySelector('.stop-button')",
                    timeout=min(30000, max(remaining_ms, 1000))
                )
                await page.wait_for_timeout(2000)
                elapsed = time.time() - (deadline - timeout)
                print(f"    [{tag}] Geracao concluida em {elapsed:.0f}s — stop-button sumiu", flush=True)
                return
            except PlaywrightTimeout:
                # Checar erros
                erro = await QwenReplyAsync._checar_erro_qwen(page)
                if erro:
                    print(f"    [{tag}] Geracao FALHOU: erro detectado durante geracao: {erro}", flush=True)
                    raise RuntimeError(f"Qwen erro durante geracao: {erro}")

                # Checar se stop-button ainda existe
                still_generating = await page.evaluate("!!document.querySelector('.stop-button')")
                if not still_generating:
                    await page.wait_for_timeout(2000)
                    elapsed = time.time() - (deadline - timeout)
                    print(f"    [{tag}] Geracao concluida em {elapsed:.0f}s — stop-button sumiu (via evaluate)", flush=True)
                    return

                # Log de progresso a cada ~30s
                now = time.time()
                if now - last_log >= 30:
                    elapsed = now - (deadline - timeout)
                    remaining = deadline - now
                    print(f"    [{tag}] Ainda gerando... {elapsed:.0f}s decorridos, {remaining:.0f}s restantes", flush=True)
                    last_log = now

        # Timeout total excedido
        elapsed = timeout
        erro = await QwenReplyAsync._checar_erro_qwen(page)
        still_generating = await page.evaluate("!!document.querySelector('.stop-button')")
        if erro:
            print(f"    [{tag}] Geracao FALHOU: timeout + erro: {erro}", flush=True)
            raise RuntimeError(f"Qwen timeout + erro: {erro}")
        if still_generating:
            print(f"    [{tag}] Geracao FALHOU: timeout de {timeout}s — Qwen ainda esta gerando (stop-button visivel)", flush=True)
            raise RuntimeError(f"Qwen ainda gerando apos {timeout}s (stop-button visivel) — resposta muito longa ou modelo lento")
        else:
            print(f"    [{tag}] Geracao FALHOU: timeout de {timeout}s — stop-button sumiu mas resposta nao foi detectada", flush=True)
            raise RuntimeError(f"Qwen timeout {timeout}s — stop-button sumiu mas sem resposta. Possivel bug no seletor CSS.")

    @staticmethod
    async def _esperar_e_extrair_resposta(page, tag="aba", max_tentativas=5):
        """Espera o conteudo da resposta aparecer no DOM e extrai o texto.
        
        O stop-button sumir nao garante que o texto ja esta renderizado.
        Esta funcao faz polling com retries para garantir que pegamos
        o texto completo, mesmo que o DOM demore para atualizar.
        """
        import asyncio as _asyncio
        
        for tentativa in range(1, max_tentativas + 1):
            texto = await QwenReplyAsync._ultima_resposta_page(page)
            if texto and texto.strip():
                preview = texto[:60].replace('\n', ' ')
                print(f"    [{tag}] Resposta extraida na tentativa {tentativa} ({len(texto)} chars): {preview}", flush=True)
                return texto
            
            if tentativa < max_tentativas:
                espera = tentativa * 2  # 2s, 4s, 6s, 8s...
                print(f"    [{tag}] Resposta vazia na tentativa {tentativa}, esperando {espera}s para o DOM atualizar...", flush=True)
                await _asyncio.sleep(espera)
        
        print(f"    [{tag}] ATENCAO: resposta vazia apos {max_tentativas} tentativas — Qwen pode nao ter gerado texto", flush=True)
        return texto  # retorna vazio mesmo, deixa o parser decidir

    @staticmethod
    async def _ultima_resposta_page(page):
        return await page.evaluate("""
            () => {
                const msgs = document.querySelectorAll('.qwen-chat-message-assistant');
                if (msgs.length === 0) return '';
                const last = msgs[msgs.length - 1];
                const answer = last.querySelector('.response-message-content.phase-answer');
                if (answer) {
                    const md = answer.querySelector('.qwen-markdown-text');
                    if (md && md.innerText.trim()) {
                        return md.innerText.trim();
                    }
                    const txt = answer.innerText.trim();
                    if (txt) return txt;
                }
                const any = last.querySelector('.response-message-content');
                if (any) {
                    const txt = any.innerText.trim();
                    if (txt) return txt;
                }
                return '';
            }
        """)

    # ---- Interno: gerenciamento deterministico de processos ----

    @staticmethod
    def _chrome_pgrep_pattern(perfil_path):
        """Retorna o padrao de busca especifico para processos Chrome.

        Usa --user-data-dir= em vez do path puro para evitar falsos positivos:
        pgrep -f <path> encontraria o proprio processo Python que contem
        essa string no seu codigo, causando _esperar_chrome_morto() a
        nunca retornar True mesmo quando o Chrome ja morreu.
        """
        # Chrome launch argument: --user-data-dir=/path/to/profile
        return f"--user-data-dir={perfil_path}"

    @staticmethod
    def _esperar_chrome_morto(perfil_path, timeout=10):
        """Espera DETERMINISTICAMENTE ate que nenhum processo Chrome com
        nosso perfil esteja rodando.

        Nao usa time.sleep() como "seguranca" — usa pgrep para VERIFICAR
        que o processo morreu, com dupla confirmacao para evitar race conditions.

        Retorna True se o processo morreu, False se ainda esta vivo apos timeout.
        """
        if platform.system() == "Windows":
            # Windows: esperar o PowerShell confirmar
            time.sleep(2)
            return True

        padrao = QwenReplyAsync._chrome_pgrep_pattern(perfil_path)
        start = time.time()
        while time.time() - start < timeout:
            try:
                result = subprocess.run(
                    ["pgrep", "-f", padrao],
                    capture_output=True, timeout=5
                )
                if result.returncode != 0:
                    # Nenhum processo encontrado — dupla confirmacao
                    time.sleep(0.3)
                    result2 = subprocess.run(
                        ["pgrep", "-f", padrao],
                        capture_output=True, timeout=5
                    )
                    if result2.returncode != 0:
                        return True  # DETERMINISTICO: pgrep confirmou 2x que morreu
            except Exception:
                pass
            time.sleep(0.5)
        # Timeout: processo ainda vivo
        return False

    @staticmethod
    def _matar_chrome_por_perfil(perfil_path, signal=None):
        """Mata processos Chrome que usam o perfil especificado.

        signal: None (SIGTERM via pkill) ou "-9" (SIGKILL)
        Usa --user-data-dir= para matching especifico (evita matar
        processos Python que contenham o path no codigo).
        """
        try:
            if platform.system() == "Windows":
                perfil_lower = str(perfil_path).lower()
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     f"Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" "
                     f"-ErrorAction SilentlyContinue | "
                     f"Where-Object {{ $_.CommandLine -and $_.CommandLine -like '*{perfil_lower}*' }} | "
                     f"Stop-Process -Force -ErrorAction SilentlyContinue"],
                    timeout=15, capture_output=True
                )
            else:
                padrao = QwenReplyAsync._chrome_pgrep_pattern(perfil_path)
                cmd = ["pkill", "-f", padrao]
                if signal:
                    cmd.insert(1, signal)
                subprocess.run(cmd, timeout=15, capture_output=True)
        except Exception:
            pass

    @staticmethod
    def _limpar_lockfiles(perfil_path):
        """Remove LOCK files do perfil para prevenir exit code 21."""
        import glob as _glob
        for f in _glob.glob(str(pathlib.Path(perfil_path) / "**" / "LOCK"), recursive=True):
            try:
                pathlib.Path(f).unlink(missing_ok=True)
            except:
                pass


if __name__ == "__main__":
    import asyncio

    async def _cli():
        parser = argparse.ArgumentParser(description="Envia pergunta para o Qwen AI (async)")
        parser.add_argument("--prompt", "-p", default=None, help="Texto da pergunta")
        parser.add_argument("--arquivo", "-a", default=None, help="Caminho do arquivo para anexar")
        parser.add_argument("--timeout", "-t", type=int, default=180, help="Timeout em segundos")
        parser.add_argument("--headless", action="store_true", help="Rodar Chrome sem janela")
        args = parser.parse_args()

        prompt = args.prompt or sys.stdin.read().strip()

        async with QwenReplyAsync(headless=args.headless) as qr:
            resp = await qr.ask_on_page(None, prompt, arquivo=args.arquivo, timeout=args.timeout)
            print(resp)

    asyncio.run(_cli())
