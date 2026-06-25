"""
QwenReply Async - Versao async do QwenReply para chamadas paralelas.

Usa Playwright async_api + asyncio.gather para operar multiplas abas
em paralelo dentro do mesmo Chrome. Ideal para rodar capa/titulo/linha
simultaneamente sem abrir 3 browsers.

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
import pathlib, sys, argparse, subprocess, time, platform

PASTA = pathlib.Path(__file__).parent
PERFIL = PASTA / "chrome_profile"


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
    """Versao async do QwenReply — mesma interface, mesma logica, async/await."""

    def __init__(self, perfil=None, headless=False):
        self._perfil = str(perfil or PERFIL.resolve())
        self._headless = headless
        self._ctx = None
        self._page = None
        self._playwright = None

    # === API publica ===

    async def abrir_context(self, snapshot=False):
        """Abre o browser + context (1 Chrome, 1 perfil)."""
        if self._ctx is not None:
            return
        self._limpar()
        self._playwright = await async_playwright().start()
        self._ctx = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=self._perfil,
            channel="chrome",
            headless=self._headless,
        )
        self._page = self._ctx.pages[0] if self._ctx.pages else await self._ctx.new_page()
        await self._page.goto("https://chat.qwen.ai/", wait_until="domcontentloaded", timeout=120000)
        await self._page.wait_for_selector("textarea", timeout=120000)
        await self._page.wait_for_timeout(1500)
        await self._verificar_sessao(self._page)
        if snapshot:
            html = await self._page.evaluate("document.getElementById('qwen-chat-header-right')?.innerHTML || ''")
            pathlib.Path(PASTA / "_snapshot_header.html").write_text(html, encoding="utf-8")
        await self._ativar_temp(self._page)

    async def new_page(self):
        """Cria uma nova aba no mesmo context e navega ate o Qwen."""
        if self._ctx is None:
            await self.abrir_context()
        page = await self._ctx.new_page()
        tag = _page_tag(page)
        print(f"    [{tag}] Navegando ate chat.qwen.ai...", flush=True)
        await page.goto("https://chat.qwen.ai/", wait_until="domcontentloaded", timeout=120000)
        await page.wait_for_selector("textarea", timeout=120000)
        print(f"    [{tag}] Pagina carregada, textarea encontrada", flush=True)
        await page.wait_for_timeout(1500)
        await self._verificar_sessao(page)
        await self._ativar_temp(page)
        print(f"    [{tag}] Aba pronta", flush=True)
        return page

    async def ask_on_page(self, page, prompt, arquivo=None, timeout=180):
        """Envia prompt numa aba especifica. Retorna o texto da resposta."""
        if page is None:
            page = self._page
        tag = _page_tag(page)
        try:
            if arquivo:
                await self._upload_page(page, arquivo, tag=tag)
            await self._enviar_page(page, prompt, timeout, tag=tag)
            resultado = await self._ultima_resposta_page(page)
            if resultado:
                preview = resultado[:60].replace('\n', ' ')
                print(f"    [{tag}] Resposta extraida ({len(resultado)} chars): {preview}", flush=True)
            else:
                print(f"    [{tag}] ATENCAO: resposta vazia — Qwen pode nao ter gerado texto", flush=True)
            return resultado
        except:
            raise

    async def close(self):
        """Fecha o browser e o playwright."""
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

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    # === Estaticos (operacoes numa Page qualquer) ===

    @staticmethod
    async def _verificar_sessao(page):
        login_selectors = [
            'button:has-text("Login")',
            'button:has-text("Log in")',
            'button:has-text("Entrar")',
            ".login-entry",
            '[class*="login"]',
            '[class*="sign-in"]',
            '[class*="signin"]',
        ]
        for sel in login_selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    raise SessionExpiredError(
                        "Sessao Qwen expirada — elemento de login detectado na pagina. "
                        "Execute Playwright/login_setup.py para refazer o login."
                    )
            except SessionExpiredError:
                raise
            except:
                pass

    @staticmethod
    async def _ativar_temp(page):
        cls = await page.evaluate("document.querySelector('.temporary-chat-entry')?.className || ''")
        if not cls or "temporary-chat-entry-out" in cls:
            return
        await page.locator(".temporary-chat-entry").click()
        await page.wait_for_timeout(500)

    @staticmethod
    async def _upload_page(page, caminho, tag="aba"):
        """Upload de arquivo na aba. Loga cada etapa."""
        arquivo_nome = pathlib.Path(caminho).name
        arquivo_tamanho = pathlib.Path(caminho).stat().st_size
        print(f"    [{tag}] Upload iniciando: {arquivo_nome} ({arquivo_tamanho/1024:.0f}KB)", flush=True)

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

    # ---- Interno ----

    @staticmethod
    def _limpar_lockfiles(perfil_path):
        import glob as _glob
        for f in _glob.glob(str(pathlib.Path(perfil_path) / "**" / "LOCK"), recursive=True):
            try:
                pathlib.Path(f).unlink(missing_ok=True)
            except:
                pass

    def _limpar(self):
        perfil_path = self._perfil.lower()
        self._limpar_lockfiles(self._perfil)
        try:
            if platform.system() == "Windows":
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     f"Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" "
                     f"-ErrorAction SilentlyContinue | "
                     f"Where-Object {{ $_.CommandLine -and $_.CommandLine -like '*{perfil_path}*' }} | "
                     f"Stop-Process -Force -ErrorAction SilentlyContinue"],
                    timeout=15, capture_output=True
                )
            else:
                subprocess.run(
                    ["pkill", "-f", perfil_path],
                    timeout=15, capture_output=True
                )
            time.sleep(0.5)
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
