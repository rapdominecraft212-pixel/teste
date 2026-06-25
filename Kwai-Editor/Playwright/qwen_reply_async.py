"""
QwenReply Async - Versao async do QwenReply para chamadas paralelas.

Usa Playwright async_api + asyncio.gather para operar multiplas abas
em paralelo dentro do mesmo Chrome. Ideal para rodar capa/titulo/linha
simultaneamente sem abrir 3 browsers.

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
        await page.goto("https://chat.qwen.ai/", wait_until="domcontentloaded", timeout=120000)
        await page.wait_for_selector("textarea", timeout=120000)
        await page.wait_for_timeout(1500)
        await self._verificar_sessao(page)
        await self._ativar_temp(page)
        return page

    async def ask_on_page(self, page, prompt, arquivo=None, timeout=180):
        """Envia prompt numa aba especifica. Retorna o texto da resposta."""
        if page is None:
            page = self._page
        try:
            if arquivo:
                await self._upload_page(page, arquivo)
            await self._enviar_page(page, prompt, timeout)
            return await self._ultima_resposta_page(page)
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
    async def _upload_page(page, caminho):
        await page.locator(".mode-select-open").click()
        await page.wait_for_selector(".mode-select-dropdown", timeout=10000)
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
        try:
            await page.wait_for_selector(".fileitem-btn", timeout=15000)
            await page.wait_for_function("() => !document.querySelector('.fileitem-btn')", timeout=60000)
        except Exception:
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
                        return text
            except:
                pass
        return None

    @staticmethod
    async def _enviar_page(page, prompt, timeout):
        await page.locator("textarea").fill(prompt)
        await page.locator(".send-button").click()

        # Espera o Qwen comecar a gerar (stop-button aparece)
        try:
            await page.wait_for_selector(".stop-button", timeout=min(timeout * 1000, 30000))
        except PlaywrightTimeout:
            erro = await QwenReplyAsync._checar_erro_qwen(page)
            if erro:
                raise RuntimeError(f"Qwen erro na pagina: {erro}")
            try:
                await page.locator(".send-button").click()
                await page.wait_for_selector(".stop-button", timeout=15000)
            except:
                raise RuntimeError("Qwen nao comecou a gerar apos enviar o prompt. Possivel rate limit ou erro de sessao.")

        # Espera o Qwen terminar de gerar (stop-button desaparece)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                await page.wait_for_function(
                    "() => !document.querySelector('.stop-button')",
                    timeout=min(30000, int((deadline - time.time()) * 1000))
                )
                await page.wait_for_timeout(2000)
                return
            except PlaywrightTimeout:
                erro = await QwenReplyAsync._checar_erro_qwen(page)
                if erro:
                    raise RuntimeError(f"Qwen erro durante geracao: {erro}")
                still_generating = await page.evaluate("!!document.querySelector('.stop-button')")
                if not still_generating:
                    await page.wait_for_timeout(2000)
                    return

        erro = await QwenReplyAsync._checar_erro_qwen(page)
        if erro:
            raise RuntimeError(f"Qwen timeout + erro: {erro}")
        raise RuntimeError(f"Qwen nao terminou de gerar em {timeout}s. Possivel rate limit.")

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
