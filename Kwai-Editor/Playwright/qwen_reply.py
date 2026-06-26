"""
Qwen Reply - Template base para interagir com Qwen AI
Uso:
    from qwen_reply import QwenReply
    qr = QwenReply()
    resp = qr.ask("sua pergunta", arquivo="video.mp4")
    print(resp)

CLI:
    python qwen_reply.py --prompt "analise" --arquivo video.mp4
"""
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
import pathlib, sys, argparse, subprocess, atexit, signal, time, platform

PASTA = pathlib.Path(__file__).parent
PERFIL = PASTA / "chrome_profile"


class SessionExpiredError(Exception):
    pass


class QwenReply:
    def __init__(self, perfil=None, headless=False):
        self._perfil = str(perfil or PERFIL.resolve())
        self._headless = headless
        self._ctx = None
        self._page = None
        self._playwright = None
        self._abriu = False
        atexit.register(self.close)
        try:
            signal.signal(signal.SIGINT, lambda s, f: self.close())
            signal.signal(signal.SIGTERM, lambda s, f: self.close())
        except (ValueError, RuntimeError):
            pass

    def ask(self, prompt, arquivo=None, timeout=180):
        return self.ask_on_page(self._page, prompt, arquivo=arquivo, timeout=timeout)

    def close(self):
        if self._ctx:
            try:
                self._ctx.close()
            except:
                pass
            self._ctx = None
            self._page = None
        if self._playwright:
            try:
                self._playwright.stop()
            except:
                pass
            self._playwright = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # === API pública para modo multi-aba ===

    def abrir_context(self, snapshot=False):
        """Abre o browser + context (1 Chrome, 1 perfil)."""
        if self._ctx is not None:
            return
        self._limpar()
        self._playwright = sync_playwright().start()
        self._ctx = self._playwright.chromium.launch_persistent_context(
            user_data_dir=self._perfil,
            channel="chrome",
            headless=self._headless,
        )
        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self._page.goto("https://chat.qwen.ai/", wait_until="domcontentloaded", timeout=120000)
        self._page.wait_for_selector("textarea", timeout=120000)
        self._page.wait_for_timeout(1500)
        self._verificar_sessao(self._page)
        if snapshot:
            html = self._page.evaluate("document.getElementById('qwen-chat-header-right')?.innerHTML || ''")
            pathlib.Path(PASTA / "_snapshot_header.html").write_text(html, encoding="utf-8")
        self._ativar_temp(self._page)

    def new_page(self):
        """Cria uma nova aba no mesmo context e navega até o Qwen."""
        if self._ctx is None:
            self.abrir_context()
        page = self._ctx.new_page()
        page.goto("https://chat.qwen.ai/", wait_until="domcontentloaded", timeout=120000)
        page.wait_for_selector("textarea", timeout=120000)
        page.wait_for_timeout(1500)
        self._verificar_sessao(page)
        self._ativar_temp(page)
        return page

    def ask_on_page(self, page, prompt, arquivo=None, timeout=180):
        """Envia prompt numa aba específica. Retorna o texto da resposta."""
        if page is None:
            page = self._page
        try:
            if arquivo:
                self._upload_page(page, arquivo)
            self._enviar_page(page, prompt, timeout)
            return self._ultima_resposta_page(page)
        except:
            raise

    # === Estáticos (operações numa Page qualquer) ===

    @staticmethod
    def _verificar_sessao(page):
        login_selectors = [
            "button:has-text(\"Login\")",
            "button:has-text(\"Log in\")",
            "button:has-text(\"Entrar\")",
            ".login-entry",
            "[class*=\"login\"]",
            "[class*=\"sign-in\"]",
            "[class*=\"signin\"]",
        ]
        for sel in login_selectors:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    raise SessionExpiredError(
                        "Sessao Qwen expirada — elemento de login detectado na pagina. "
                        "Execute Playwright/login_setup.py para refazer o login."
                    )
            except SessionExpiredError:
                raise
            except:
                pass

    @staticmethod
    def _ativar_temp(page):
        cls = page.evaluate("document.querySelector('.temporary-chat-entry')?.className || ''")
        if not cls or "temporary-chat-entry-out" in cls:
            return
        page.locator(".temporary-chat-entry").click()
        page.wait_for_timeout(500)

    @staticmethod
    def _upload_page(page, caminho):
        page.locator(".mode-select-open").click()
        page.wait_for_selector(".mode-select-dropdown", timeout=10000)
        with page.expect_file_chooser() as fc:
            page.evaluate("""
                () => {
                    const items = document.querySelectorAll(
                        '.mode-select-dropdown .ant-dropdown-menu-item'
                    );
                    if (items.length > 0) items[0].click();
                }
            """)
        fc.value.set_files(str(caminho))
        try:
            page.wait_for_selector(".fileitem-btn", timeout=15000)
            page.wait_for_function("() => !document.querySelector('.fileitem-btn')", timeout=60000)
        except Exception:
            page.wait_for_timeout(20000)

    @staticmethod
    def _checar_erro_qwen(page):
        """Verifica se o Qwen mostrou uma mensagem de erro ou rate limit na página."""
        error_indicators = [
            ".error-message",
            ".rate-limit",
            "[class*='error']",
            "[class*='rate-limit']",
            "[class*='limit']",
        ]
        for sel in error_indicators:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    text = el.inner_text().strip()
                    if text:
                        return text
            except:
                pass
        return None

    @staticmethod
    def _enviar_page(page, prompt, timeout):
        page.locator("textarea").fill(prompt)
        page.locator(".send-button").click()

        # Espera o Qwen começar a gerar (stop-button aparece)
        try:
            page.wait_for_selector(".stop-button", timeout=min(timeout * 1000, 30000))
        except PlaywrightTimeout:
            # Qwen não começou a gerar — verificar erros
            erro = QwenReply._checar_erro_qwen(page)
            if erro:
                raise RuntimeError(f"Qwen erro na página: {erro}")
            # Pode ser que o botão send não funcionou — tentar novamente
            try:
                page.locator(".send-button").click()
                page.wait_for_selector(".stop-button", timeout=15000)
            except:
                raise RuntimeError("Qwen não começou a gerar após enviar o prompt. Possível rate limit ou erro de sessão.")

        # Espera o Qwen terminar de gerar (stop-button desaparece)
        # Usa polling com checagem de erros a cada 30s
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                page.wait_for_function(
                    "() => !document.querySelector('.stop-button')",
                    timeout=min(30000, int((deadline - time.time()) * 1000))
                )
                # stop-button desapareceu — Qwen terminou
                page.wait_for_timeout(2000)
                return
            except PlaywrightTimeout:
                # Ainda gerando — checar se há erro
                erro = QwenReply._checar_erro_qwen(page)
                if erro:
                    raise RuntimeError(f"Qwen erro durante geração: {erro}")
                # Checar se o stop-button ainda existe (ainda gerando)
                still_generating = page.evaluate("!!document.querySelector('.stop-button')")
                if not still_generating:
                    page.wait_for_timeout(2000)
                    return
                # Continuar esperando

        # Timeout total excedido
        erro = QwenReply._checar_erro_qwen(page)
        if erro:
            raise RuntimeError(f"Qwen timeout + erro: {erro}")
        raise RuntimeError(f"Qwen não terminou de gerar em {timeout}s. Possível rate limit.")

    @staticmethod
    def _ultima_resposta_page(page):
        return page.evaluate("""
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

    # ---- Interno (legado) ----

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

    def _abrir(self, snapshot=False):
        """Legado: usado pelo ask() antigo."""
        if not self._abriu:
            self._limpar()
            self._abriu = True
        self.abrir_context(snapshot=snapshot)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Envia pergunta para o Qwen AI")
    parser.add_argument("--prompt", "-p", default=None, help="Texto da pergunta")
    parser.add_argument("--arquivo", "-a", default=None, help="Caminho do arquivo para anexar")
    parser.add_argument("--timeout", "-t", type=int, default=180, help="Timeout em segundos")
    parser.add_argument("--headless", action="store_true", help="Rodar Chrome sem janela")
    args = parser.parse_args()

    prompt = args.prompt or sys.stdin.read().strip()

    with QwenReply(headless=args.headless) as qr:
        resp = qr.ask(prompt, arquivo=args.arquivo, timeout=args.timeout)
        print(resp)
