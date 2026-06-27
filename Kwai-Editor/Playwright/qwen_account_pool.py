"""
QwenAccountPool — Pool de contas Qwen pre-aquecidas com login automatico.

DESIGN:
    Cada conta tem seu PROPRIO browser Playwright persistente que fica aberto
    durante toda a sessao do worker. O login acontece UMA UNICA VEZ no startup
    (warm_up), e o browser/contexto fica disponivel para jobs subsequentes
    sem precisar re-logar.

    Fluxo:
        1. pool.initialize() — lanca todos os browsers em paralelo, faz login
        2. pool.acquire()    — worker thread pega uma conta disponivel (bloqueia se nao ha)
        3. conta.new_page()  — cria nova aba no browser da conta
        4. ... trabalho ...
        5. conta.close_page(page) — fecha aba (browser continua aberto)
        6. pool.release(conta) — devolve conta ao pool

    Keep-alive:
        Um background task navega a pagina principal de cada conta idle
        a cada 3 minutos, impedindo que a sessao expire. Se expirar mesmo
        assim, o login e refeito automaticamente usando as credenciais salvas.

THREAD-SAFETY:
    O pool roda seu proprio event loop em uma thread dedicada.
    Worker threads chamam acquire()/release() (thread-safe via queue.Queue).
    Operacoes Playwright (new_page, login, etc.) rodam no event loop do pool
    via pool.run_async(coro).

USO:
    from qwen_account_pool import AccountPool

    pool = AccountPool.initialize(accounts_config, headless=True)

    # Em worker thread:
    conta = pool.acquire(timeout=300)
    try:
        page = pool.run_async(conta.new_page(tag='capa+titulo'))
        # ... usar page ...
        pool.run_async(conta.close_page(page))
    finally:
        pool.release(conta)

    # Shutdown:
    pool.shutdown()
"""

import asyncio
import json
import os
import shutil
import threading
import time
import traceback
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, BrowserContext, Page

# Importar log do projeto
import sys
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
try:
    from bot.log_utils import log
except ImportError:
    import logging
    log = logging.getLogger("qwen_pool")


# ─── Constantes ───────────────────────────────────────────────────────────────

ACCOUNTS_FILE = Path(__file__).parent / "accounts.json"
KEEP_ALIVE_INTERVAL = 180  # segundos (3 min)
WARM_UP_TIMEOUT = 120      # segundos para aquecer todas as contas
LOGIN_TIMEOUT = 30         # segundos para login individual
PAGE_READY_TIMEOUT = 15    # segundos para pagina pronta


# ─── Utilitarios ───────────────────────────────────────────────────────────────

def _detect_chrome_channel() -> Optional[str]:
    """Detecta se Google Chrome esta instalado. Retorna channel ou None."""
    chrome_paths = [
        "/opt/google/chrome/chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ]
    for p in chrome_paths:
        if os.path.isfile(p):
            return "chrome"
    return None


# ─── QwenAccount ──────────────────────────────────────────────────────────────

class QwenAccount:
    """Uma conta Qwen com seu proprio browser persistente e login automatico.

    Estados:
        cold    — recem-criada, nenhum browser
        warming — browser aberto, fazendo login
        ready   — logada e disponivel para jobs
        busy    — em uso por um job
        error   — falha no login ou browser morreu
        closed  — shutdown completo
    """

    def __init__(self, account_id: str, email: str, password: str,
                 headless: bool = True, id_num: int = 0):
        self.id = account_id          # ex: "conta_3"
        self.id_num = id_num          # ex: 3 (para mensagens de erro amigáveis)
        self.email = email
        self.password = password
        self.headless = headless
        self.state = "cold"
        self._last_activity = time.time()

        # Playwright
        self._playwright = None
        self._ctx: Optional[BrowserContext] = None
        self._main_page: Optional[Page] = None  # pagina de keep-alive

        # Perfil
        self._profile_dir: Optional[str] = None

    # ─── Ciclo de vida ────────────────────────────────────────────────

    async def warm_up(self):
        """Abre browser + restaura sessão persistida (sem login, sem captcha).

        NOVO FLUXO (pós-auditoria de legado):
        - Carrega sessão de Playwright/sessions/{id}.json (salva por setup_accounts.py)
        - Abre Chrome com perfil temporário limpo
        - Injeta cookies + localStorage + IndexedDB
        - Recarrega página e verifica se sessão é válida (textarea visível)
        - Se válida: state="ready" em ~5s, sem login, sem captcha
        - Se expirada: state="error" com mensagem clara para re-rode setup_accounts.py

        Antes: fazia login completo a cada startup → captcha em modo headless falhava.
        """
        self.state = "warming"
        tag = f"conta {self.id}"
        log.info(f"[{tag}] Aquecendo — restaurando sessão persistida...")

        # Importar módulo de sessão (lazy import para evitar dependência circular)
        try:
            from qwen_session import (
                carregar_sessao, sessao_existe, restaurar_sessao,
                sessao_eh_valida, bem_vindo_modal_visivel, get_session_path
            )
        except ImportError:
            log.error(f"[{tag}] Módulo qwen_session não encontrado!")
            self.state = "error"
            raise RuntimeError("qwen_session.py não encontrado em Playwright/")

        # Verificar que sessão existe
        if not sessao_existe(self.id):
            log.error(f"[{tag}] ❌ Sessão não encontrada em {get_session_path(self.id)}")
            log.error(f"[{tag}]    Rode: python Playwright/setup_accounts.py --conta {self.id_num}")
            self.state = "error"
            raise RuntimeError(
                f"Sessão da conta {self.id} não existe. "
                f"Rode: python Playwright/setup_accounts.py --conta {self.id_num}"
            )

        # Carregar sessão
        sessao = carregar_sessao(self.id)
        if not sessao:
            log.error(f"[{tag}] ❌ Sessão corrompida ou vazia")
            self.state = "error"
            raise RuntimeError(f"Sessão de {self.id} está corrompida")

        try:
            # Criar diretorio de perfil temporário (não persistente — sessão vem do JSON)
            self._profile_dir = f"/tmp/chrome_kwai_conta_{self.id}"
            if os.path.exists(self._profile_dir):
                shutil.rmtree(self._profile_dir, ignore_errors=True)
            os.makedirs(self._profile_dir, exist_ok=True)
            self._limpar_lockfiles(self._profile_dir)

            # Matar Chrome residual que use esse perfil
            self._matar_chrome_por_perfil(self._profile_dir)
            await asyncio.sleep(0.5)

            # Lançar Playwright
            self._playwright = await async_playwright().start()
            channel = _detect_chrome_channel()
            launch_kwargs = dict(
                user_data_dir=self._profile_dir,
                headless=self.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-extensions",
                    "--disable-gpu",
                    "--no-sandbox",
                ],
                no_viewport=True,
            )
            if channel:
                launch_kwargs["channel"] = channel

            self._ctx = await self._playwright.chromium.launch_persistent_context(
                **launch_kwargs
            )

            # Pagina principal (sera usada para keep-alive)
            self._main_page = self._ctx.pages[0] if self._ctx.pages else await self._ctx.new_page()

            # === Restaurar sessão (NOVO) ===
            log.info(f"[{tag}] Navegando para Qwen e injetando sessão...")
            await self._main_page.goto("https://chat.qwen.ai/", wait_until="domcontentloaded", timeout=30000)
            await self._main_page.wait_for_timeout(1000)

            # Injetar cookies + localStorage + IndexedDB
            ok = await restaurar_sessao(self._ctx, self._main_page, sessao)
            if not ok:
                log.error(f"[{tag}] ❌ Falha ao restaurar sessão")
                self.state = "error"
                raise RuntimeError(f"Falha ao restaurar sessão de {self.id}")

            # Recarregar para aplicar cookies
            await self._main_page.reload(wait_until="domcontentloaded", timeout=30000)
            await self._main_page.wait_for_timeout(2000)

            # Verificar se sessão é válida
            if await sessao_eh_valida(self._main_page, timeout_sec=10):
                self.state = "ready"
                self._last_activity = time.time()
                log.info(f"[{tag}] ✅ Sessão restaurada — logado sem captcha!")
                return

            # Sessão inválida — verificar se é Welcome modal (expirou)
            if await bem_vindo_modal_visivel(self._main_page):
                log.error(f"[{tag}] ❌ Sessão EXPIROU (Welcome modal visível)")
                log.error(f"[{tag}]    Rode: python Playwright/setup_accounts.py --conta {self.id_num}")
                self.state = "error"
                raise RuntimeError(
                    f"Sessão de {self.id} expirou. "
                    f"Rode: python Playwright/setup_accounts.py --conta {self.id_num}"
                )

            # Outro motivo de falha
            log.error(f"[{tag}] ❌ Sessão restaurada mas textarea não apareceu")
            self.state = "error"
            raise RuntimeError(
                f"Sessão de {self.id} não foi aceita pelo Qwen. "
                f"Pode ser mudança de DOM ou sessão invalidada server-side. "
                f"Rode: python Playwright/setup_accounts.py --conta {self.id_num}"
            )

        except Exception as e:
            log.error(f"[{tag}] Falha no warm_up: {e}")
            traceback.print_exc()
            self.state = "error"
            # Tentar limpar
            await self._cleanup_browser()
            raise

    async def _login(self):
        """Faz login no Qwen usando email/senha via pagina de auth.

        DEPRECATED: Não é mais chamado em produção (warm_up usa sessão persistida).
        Mantido para compatibilidade com testes standalone via setup_accounts.py.
        """
        tag = f"conta {self.id}"
        page = self._main_page

        log.info(f"[{tag}] Navegando para pagina de login...")
        await page.goto("https://chat.qwen.ai/auth", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(1500)

        # Verificar se ja esta logado (a pagina pode redirecionar)
        current_url = page.url
        if "chat.qwen.ai/" == current_url or ("/chat.qwen.ai/" in current_url and "/auth" not in current_url):
            try:
                await page.wait_for_selector("textarea", timeout=5000)
                log.info(f"[{tag}] Ja estava logado! Pulando login.")
                return
            except:
                pass  # Nao esta logado, continuar com login

        # Preencher email
        log.info(f"[{tag}] Preenchendo email...")
        email_input = page.locator('input[name="email"]')
        await email_input.fill("")
        await email_input.fill(self.email)
        await page.wait_for_timeout(300)

        # Preencher senha
        log.info(f"[{tag}] Preenchendo senha...")
        password_input = page.locator('input[name="password"]')
        await password_input.fill("")
        await password_input.fill(self.password)
        await page.wait_for_timeout(300)

        # Clicar em Sign In
        log.info(f"[{tag}] Clicando Sign In...")
        submit_btn = page.locator('button[type="submit"]')
        # Esperar o botao ficar habilitado (após preencher os campos)
        await submit_btn.wait_for(state="visible", timeout=5000)
        await page.wait_for_timeout(200)
        await submit_btn.click()

        # Esperar resultado do login — sem depender de navigation events
        log.info(f"[{tag}] Aguardando resultado do login...")
        try:
            # Abordagem simples: esperar o textarea aparecer (login bem sucedido)
            # O Qwen redireciona via SPA — wait_for_url pode nao detectar
            await page.wait_for_selector("textarea", timeout=LOGIN_TIMEOUT * 1000)
            log.info(f"[{tag}] Login OK! Sessao estabelecida.")
        except Exception as e:
            # Diagnostico: coletar informacoes da pagina para entender o que aconteceu
            current_url = page.url
            page_title = await page.title()
            # Verificar se ha mensagem de erro na pagina
            error_text = None
            try:
                error_el = await page.query_selector(
                    ".ant-message-error, .qwenchat-auth-pc-error, "
                    "[class*='error'], [class*='Error'], .ant-form-item-explain-error"
                )
                if error_el:
                    error_text = await error_el.inner_text()
            except:
                pass

            # Verificar se permaneceu na pagina de login
            is_auth_page = "/auth" in current_url

            if error_text:
                msg = f"Login falhou: {error_text}"
            elif is_auth_page:
                msg = (f"Login falhou: permaneceu na pagina de auth. "
                       f"URL={current_url}, title={page_title}. "
                       f"Possivel causa: email/senha incorretos ou conta nao registrada")
            else:
                msg = (f"Login falhou: redirecionou mas textarea nao encontrada. "
                       f"URL={current_url}, title={page_title}")

            log.error(f"[{tag}] {msg}")
            raise RuntimeError(msg) from e

    # ─── Paginas de trabalho ──────────────────────────────────────────

    async def new_page(self, tag: str = None) -> Page:
        """Cria uma nova aba no browser da conta para um job.

        Verifica sessao e refaz login se necessario.
        A pagina DEVE ser fechada com close_page() depois do uso.
        """
        tag = tag or self.id
        if self.state not in ("ready", "busy"):
            raise RuntimeError(f"Conta {self.id} em estado {self.state}, nao pode criar pagina")

        page = await self._ctx.new_page()

        try:
            # Navegar para Qwen
            await page.goto("https://chat.qwen.ai/", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_selector("textarea", timeout=PAGE_READY_TIMEOUT * 1000)

            # Verificar sessao (Welcome modal?)
            await self._check_and_fix_session(page, tag)

            self._last_activity = time.time()
            return page

        except Exception as e:
            # Se sessao expirou, tentar re-login
            if "SessionExpiredError" in type(e).__name__ or "welcome" in str(e).lower():
                log.warning(f"[{tag}] Sessao expirou, re-fazendo login...")
                await self._relogin()
                # Tentar novamente
                await page.goto("https://chat.qwen.ai/", wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_selector("textarea", timeout=PAGE_READY_TIMEOUT * 1000)
                return page
            raise

    async def close_page(self, page: Page):
        """Fecha uma aba de trabalho (sem fechar o browser)."""
        try:
            if page and not page.is_closed():
                await page.close()
        except Exception as e:
            log.warning(f"[conta {self.id}] Erro ao fechar pagina: {e}")
        self._last_activity = time.time()

    # ─── Sessao ───────────────────────────────────────────────────────

    async def _check_and_fix_session(self, page: Page, tag: str = "aba"):
        """Verifica se a sessao esta ativa e corrige se necessario."""
        try:
            # Verificar Welcome modal
            overlay = await page.query_selector(".qwen-modal-overlay")
            if not overlay:
                return  # Sessao OK

            # Verificar se e Welcome modal
            welcome_title = await overlay.query_selector("[class*='welcome-modal-title']")
            if not welcome_title:
                # Modal generico, remover via JS
                await page.evaluate("document.querySelector('.qwen-modal-overlay')?.remove()")
                await page.wait_for_timeout(500)
                return

            # E o Welcome modal — sessao expirou
            log.warning(f"[{tag}] Welcome modal detectado — sessao expirou!")

            # Tentar clicar "Stay logged out" para fechar o modal
            stay_btn = await overlay.query_selector("button:has-text('Stay logged out'), button:has-text('logado')")
            if stay_btn:
                try:
                    await stay_btn.click()
                    await page.wait_for_timeout(1000)
                    # Verificar se modal sumiu
                    overlay2 = await page.query_selector(".qwen-modal-overlay")
                    if not overlay2:
                        log.info(f"[{tag}] Modal fechado via 'Stay logged out'")
                        return
                except:
                    pass

            # Remover via JS como ultimo recurso
            await page.evaluate("document.querySelector('.qwen-modal-overlay')?.remove()")
            await page.wait_for_timeout(500)

        except Exception as e:
            log.warning(f"[{tag}] Erro ao verificar sessao: {e}")

    async def keep_alive(self):
        """Verifica e mantem a sessao ativa. Chamado periodicamente."""
        if self.state != "ready":
            return

        tag = f"conta {self.id}"
        try:
            if self._main_page and not self._main_page.is_closed():
                # Reload suave para renovar sessao
                await self._main_page.reload(wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(0.5)

                # Verificar se sessao ainda esta ativa
                try:
                    await self._main_page.wait_for_selector("textarea", timeout=5000)
                    log.debug(f"[{tag}] Keep-alive OK")
                except:
                    # Sessao pode ter expirado — verificar modal
                    overlay = await self._main_page.query_selector(".qwen-modal-overlay")
                    if overlay:
                        log.warning(f"[{tag}] Sessao expirou durante keep-alive, re-fazendo login...")
                        await self._relogin()
                    else:
                        log.debug(f"[{tag}] Keep-alive: textarea nao encontrada, mas sem modal")
        except Exception as e:
            log.warning(f"[{tag}] Keep-alive falhou: {e}")
            # Se o browser morreu, marcar como erro
            if self._main_page and self._main_page.is_closed():
                log.error(f"[{tag}] Pagina principal foi fechada — tentando re-login...")
                try:
                    await self._relogin()
                except:
                    self.state = "error"

    async def _relogin(self):
        """Refaz o login na conta usando as credenciais salvas."""
        tag = f"conta {self.id}"
        log.info(f"[{tag}] Re-fazendo login...")

        try:
            # Navegar para pagina de auth na main page
            await self._main_page.goto("https://chat.qwen.ai/auth", wait_until="networkidle", timeout=30000)
            await self._login()
            log.info(f"[{tag}] Re-login OK!")
        except Exception as e:
            log.error(f"[{tag}] Re-login falhou: {e}")
            self.state = "error"
            raise

    # ─── Cleanup ──────────────────────────────────────────────────────

    async def _cleanup_browser(self):
        """Fecha browser e limpa perfil."""
        tag = f"conta {self.id}"
        try:
            if self._ctx:
                try:
                    await self._ctx.close()
                except:
                    pass
                self._ctx = None
                self._main_page = None
        except:
            pass
        try:
            if self._playwright:
                try:
                    await self._playwright.stop()
                except:
                    pass
                self._playwright = None
        except:
            pass

        # Matar Chrome residual
        if self._profile_dir:
            self._matar_chrome_por_perfil(self._profile_dir)
            try:
                if os.path.exists(self._profile_dir):
                    shutil.rmtree(self._profile_dir, ignore_errors=True)
            except:
                pass
            self._profile_dir = None

    async def shutdown(self):
        """Desliga a conta completamente."""
        tag = f"conta {self.id}"
        log.info(f"[{tag}] Desligando...")
        await self._cleanup_browser()
        self.state = "closed"
        log.info(f"[{tag}] Desligada.")

    # ─── Chrome management (borrowed from qwen_reply_async) ───────────

    @staticmethod
    def _chrome_pgrep_pattern(perfil_path: str) -> str:
        """Retorna o padrao pgrep preciso para buscar Chrome por perfil."""
        return f"--user-data-dir={perfil_path}"

    @staticmethod
    def _matar_chrome_por_perfil(perfil_path: str, signal: str = None):
        """Mata processo Chrome que usa o perfil especificado."""
        import subprocess
        pattern = QwenAccount._chrome_pgrep_pattern(perfil_path)
        try:
            cmd = ["pkill"]
            if signal:
                cmd.append(f"-{signal}")
            cmd.extend(["-f", pattern])
            subprocess.run(cmd, capture_output=True, timeout=5)
        except Exception:
            pass

    @staticmethod
    def _limpar_lockfiles(perfil_path: str):
        """Remove arquivos LOCK do perfil Chrome."""
        for root, dirs, files in os.walk(perfil_path):
            for f in files:
                if f.upper() == "LOCK" or f.upper() == "LOCKFILE":
                    try:
                        os.remove(os.path.join(root, f))
                    except:
                        pass


# ─── AccountPool ──────────────────────────────────────────────────────────────

class AccountPool:
    """Pool thread-safe de contas Qwen pre-aquecidas.

    O pool roda um event loop dedicado em uma thread propria.
    Todas as operacoes Playwright rodam nesse event loop.
    Worker threads chamam acquire()/release() e run_async().

    Uso:
        pool = AccountPool.initialize(config, headless=True)
        conta = pool.acquire()
        page = pool.run_async(conta.new_page(tag='job'))
        # ... usar page ...
        pool.run_async(conta.close_page(page))
        pool.release(conta)
    """

    _instance: Optional['AccountPool'] = None

    def __init__(self, accounts_config: list, headless: bool = True):
        self._accounts = [
            QwenAccount(
                account_id=f"conta_{i+1}",
                email=acc["email"],
                password=acc["password"],
                headless=headless,
                id_num=i + 1,
            )
            for i, acc in enumerate(accounts_config)
        ]
        self._headless = headless
        self._available = __import__("queue").Queue()  # thread-safe
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready_event = threading.Event()
        self._keep_alive_task: Optional[asyncio.Task] = None
        self._shutting_down = False

    # ─── Singleton ────────────────────────────────────────────────────

    @classmethod
    def get_instance(cls) -> Optional['AccountPool']:
        """Retorna a instancia ativa do pool (ou None)."""
        return cls._instance

    @classmethod
    def initialize(cls, accounts_config: list, headless: bool = True) -> 'AccountPool':
        """Cria e inicializa o pool. Chamado UMA VEZ no startup."""
        pool = cls(accounts_config, headless)
        pool._start()
        cls._instance = pool
        return pool

    # ─── Thread do event loop ─────────────────────────────────────────

    def _start(self):
        """Inicia a thread dedicada com o event loop."""
        self._thread = threading.Thread(
            target=self._run_loop,
            name="qwen_pool_event_loop",
            daemon=True,
        )
        self._thread.start()

        # Esperar todas as contas ficarem prontas
        log.info(f"[pool] Aguardando {len(self._accounts)} contas fazerem login...")
        ok = self._ready_event.wait(timeout=WARM_UP_TIMEOUT)
        if not ok:
            ready = sum(1 for a in self._accounts if a.state == "ready")
            log.error(f"[pool] TIMEOUT! Apenas {ready}/{len(self._accounts)} contas ficaram prontas")
            if ready == 0:
                raise RuntimeError("Nenhuma conta conseguiu fazer login")
        else:
            ready = sum(1 for a in self._accounts if a.state == "ready")
            log.info(f"[pool] {ready}/{len(self._accounts)} contas prontas!")

    def _run_loop(self):
        """Thread: roda o event loop infinito."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        # Warm up todas as contas
        self._loop.run_until_complete(self._warm_all())

        # Sinalizar que esta pronto
        self._ready_event.set()

        # Iniciar keep-alive
        self._keep_alive_task = self._loop.create_task(self._keep_alive_loop())

        # Rodar event loop infinito
        self._loop.run_forever()

    async def _warm_all(self):
        """Faz login em todas as contas com stagger para evitar rate-limiting.

        Em vez de 7 logins simultaneos (que pode trigger anti-bot),
        lança em batches de 3 com 2s de pausa entre batches.
        """
        BATCH_SIZE = 3
        STAGGER_DELAY = 2  # segundos entre batches

        log.info(f"[pool] Aquecendo {len(self._accounts)} contas "
                 f"(batches de {BATCH_SIZE}, {STAGGER_DELAY}s entre batches)...")

        results_map = {}

        for batch_start in range(0, len(self._accounts), BATCH_SIZE):
            batch = self._accounts[batch_start:batch_start + BATCH_SIZE]
            batch_num = batch_start // BATCH_SIZE + 1

            if batch_start > 0:
                log.info(f"[pool] Pausa de {STAGGER_DELAY}s antes do batch {batch_num}...")
                await asyncio.sleep(STAGGER_DELAY)

            log.info(f"[pool] Batch {batch_num}: {', '.join(a.id for a in batch)}")
            batch_results = await asyncio.gather(
                *[acc.warm_up() for acc in batch],
                return_exceptions=True,
            )

            for acc, result in zip(batch, batch_results):
                results_map[acc.id] = result

        # Colocar contas prontas na fila disponivel
        for acc in self._accounts:
            result = results_map.get(acc.id)
            if isinstance(result, Exception):
                log.error(f"[pool] Conta {acc.id} falhou: {result}")
                acc.state = "error"
            elif acc.state == "ready":
                self._available.put(acc)

    async def _keep_alive_loop(self):
        """Background task: mantem sessoes ativas verificando periodicamente."""
        while not self._shutting_down:
            try:
                await asyncio.sleep(KEEP_ALIVE_INTERVAL)
                for acc in self._accounts:
                    if acc.state == "ready":
                        try:
                            await acc.keep_alive()
                        except Exception as e:
                            log.warning(f"[pool] Keep-alive falhou para {acc.id}: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"[pool] Erro no keep-alive loop: {e}")

    # ─── API thread-safe ──────────────────────────────────────────────

    def acquire(self, timeout: float = 300) -> QwenAccount:
        """Pega uma conta disponivel do pool. Bloqueia ate ter uma.

        Chamado de worker threads (thread-safe).
        """
        try:
            acc = self._available.get(timeout=timeout)
            acc.state = "busy"
            return acc
        except __import__("queue").Empty:
            raise RuntimeError(
                f"Timeout ({timeout}s) esperando conta disponivel no pool. "
                f"Contas: {[(a.id, a.state) for a in self._accounts]}"
            )

    def release(self, acc: QwenAccount):
        """Devolve uma conta ao pool.

        Chamado de worker threads (thread-safe).
        A conta volta como 'ready' imediatamente — o browser continua aberto.
        """
        if acc.state == "error":
            # Conta com erro — tentar recuperar
            log.warning(f"[pool] Conta {acc.id} devolvida com estado 'error', tentando recuperar...")
            try:
                self.run_async(acc._relogin())
                if acc.state != "error":
                    acc.state = "ready"
                    self._available.put(acc)
                    log.info(f"[pool] Conta {acc.id} recuperada!")
                    return
            except:
                pass
            log.error(f"[pool] Conta {acc.id} nao pode ser recuperada — removida do pool")
            return

        acc.state = "ready"
        self._available.put(acc)
        log.debug(f"[pool] Conta {acc.id} devolvida — pronta para uso")

    def run_async(self, coro):
        """Roda uma coroutine no event loop do pool. Retorna o resultado.

        Chamado de worker threads. Bloqueia ate a coroutine completar.
        """
        if self._loop is None or self._loop.is_closed():
            raise RuntimeError("Pool nao esta rodando")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    @property
    def total_accounts(self) -> int:
        return len(self._accounts)

    @property
    def ready_count(self) -> int:
        return sum(1 for a in self._accounts if a.state == "ready")

    @property
    def busy_count(self) -> int:
        return sum(1 for a in self._accounts if a.state == "busy")

    @property
    def available_count(self) -> int:
        return self._available.qsize()

    @property
    def max_concurrent_jobs(self) -> int:
        """Numero maximo de jobs simultaneos (cada job usa 2 contas)."""
        return len(self._accounts) // 2

    # ─── Shutdown ─────────────────────────────────────────────────────

    def shutdown(self):
        """Desliga o pool e todas as contas."""
        log.info("[pool] Desligando...")
        self._shutting_down = True

        if self._loop and not self._loop.is_closed():
            try:
                future = asyncio.run_coroutine_threadsafe(self._shutdown_async(), self._loop)
                future.result(timeout=30)
            except Exception as e:
                log.error(f"[pool] Erro no shutdown async: {e}")

        # Matar event loop
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)

        log.info("[pool] Desligado.")

    async def _shutdown_async(self):
        """Fecha todas as contas e cancela keep-alive."""
        if self._keep_alive_task:
            self._keep_alive_task.cancel()
            try:
                await self._keep_alive_task
            except asyncio.CancelledError:
                pass

        await asyncio.gather(
            *[acc.shutdown() for acc in self._accounts],
            return_exceptions=True,
        )


# ─── Carregamento de config ───────────────────────────────────────────────────

def load_accounts_config(path: str = None) -> list:
    """Carrega configuracao de contas de arquivo JSON.

    Formato do arquivo:
    [
        {"email": "conta1@gmail.com", "password": "senha1"},
        {"email": "conta2@gmail.com", "password": "senha2"},
        ...
    ]
    """
    path = Path(path or ACCOUNTS_FILE)
    if not path.exists():
        raise FileNotFoundError(
            f"Arquivo de contas nao encontrado: {path}\n"
            f"Crie o arquivo com o formato:\n"
            f'  [{{"email": "x@gmail.com", "password": "xxx"}}, ...]'
        )

    with open(path, "r", encoding="utf-8") as f:
        accounts = json.load(f)

    if not isinstance(accounts, list) or len(accounts) == 0:
        raise ValueError(f"Arquivo de contas vazio ou formato invalido: {path}")

    for i, acc in enumerate(accounts):
        if "email" not in acc or "password" not in acc:
            raise ValueError(f"Conta {i+1} sem 'email' ou 'password': {acc}")

    log.info(f"Carregadas {len(accounts)} contas de {path}")
    return accounts
