#!/usr/bin/env python3
"""
Login Debug com Screenshots — Kwai-Editor

Faz login no Qwen passo-a-passo tirando screenshot + dump HTML + URL + erros
visíveis em CADA etapa. Útil para diagnosticar por que o login falha em modo
headless mas funciona manualmente.

USO:
    cd Kwai-Editor/Playwright
    python login_debug_screenshots.py [--email EMAIL] [--password SENHA] [--conta N]

Se não passar --email/--password, usa a conta N do accounts.json (default: 1).

SAÍDA:
    Cria pasta ../login_debug/ com:
      - 01_pagina_inicial.png + .html
      - 02_apos_navegar_auth.png + .html
      - 03_email_preenchido.png + .html
      - 04_senha_preenchida.png + .html
      - 05_apos_click_signin.png + .html
      - 06_esperando_30s.png + .html (estado após timeout)
      - 07_estado_final.png + .html
      - debug.log (timeline com timestamps)
"""
import asyncio
import os
import sys
import json
import argparse
import time
from pathlib import Path
from datetime import datetime

# Adicionar raiz do projeto ao path
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from playwright.async_api import async_playwright

# === Config ===
DEBUG_DIR = PROJECT_ROOT / "login_debug"
ACCOUNTS_FILE = SCRIPT_DIR / "accounts.json"
LOGIN_TIMEOUT_SEC = 60  # mais generoso que o código de produção (30s)


def log_msg(msg, log_file=None):
    """Loga com timestamp no stdout e no arquivo."""
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    if log_file:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def load_account(n, email=None, password=None):
    """Carrega credenciais da conta N (1-indexed) ou usa email/senha passados."""
    if email and password:
        return email, password, "manual"
    if not ACCOUNTS_FILE.exists():
        print(f"❌ {ACCOUNTS_FILE} não encontrado")
        sys.exit(1)
    accounts = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    if n < 1 or n > len(accounts):
        print(f"❌ Conta {n} inválida (tem {len(accounts)} contas)")
        sys.exit(1)
    acc = accounts[n - 1]
    return acc["email"], acc["password"], f"conta_{n}"


async def capture_state(page, step_name, debug_dir, log_file):
    """Captura screenshot + HTML + URL + erros visíveis da página atual."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"{step_name}_{ts}"

    # Screenshot full page
    png_path = debug_dir / f"{base}.png"
    try:
        await page.screenshot(path=str(png_path), full_page=True)
        log_msg(f"  📸 screenshot salvo: {png_path.name}", log_file)
    except Exception as e:
        log_msg(f"  ❌ screenshot falhou: {e}", log_file)

    # Dump HTML
    html_path = debug_dir / f"{base}.html"
    try:
        html = await page.content()
        html_path.write_text(html, encoding="utf-8")
        log_msg(f"  📄 HTML salvo: {html_path.name} ({len(html)} bytes)", log_file)
    except Exception as e:
        log_msg(f"  ❌ HTML falhou: {e}", log_file)

    # URL atual
    url = page.url
    log_msg(f"  🔗 URL: {url}", log_file)

    # Title
    try:
        title = await page.title()
        log_msg(f"  📑 title: {title}", log_file)
    except Exception as e:
        log_msg(f"  ⚠️ title falhou: {e}", log_file)

    # Erros visíveis — procura por vários seletores comuns de erro
    erro_selectors = [
        ".ant-message-error",
        ".qwenchat-auth-pc-error",
        "[class*='error']",
        "[class*='Error']",
        ".ant-form-item-explain-error",
        "[role='alert']",
        ".toast-error",
        ".notification-error",
    ]
    erros_encontrados = []
    for sel in erro_selectors:
        try:
            elements = await page.query_selector_all(sel)
            for el in elements:
                if await el.is_visible():
                    text = await el.inner_text()
                    if text.strip():
                        erros_encontrados.append(f"{sel}: {text.strip()}")
        except Exception:
            pass

    if erros_encontrados:
        log_msg(f"  ⚠️ ERROS VISÍVEIS na página:", log_file)
        for err in erros_encontrados:
            log_msg(f"     - {err}", log_file)
    else:
        log_msg(f"  ✅ Nenhum erro visível na página", log_file)

    # Verificar se há captcha
    captcha_indicators = [
        "iframe[src*='recaptcha']",
        "iframe[src*='cloudflare']",
        ".g-recaptcha",
        "#cf-challenge-running",
        ".cf-turnstile",
        "[class*='captcha']",
        "[class*='Captcha']",
    ]
    captchas = []
    for sel in captcha_indicators:
        try:
            el = await page.query_selector(sel)
            if el:
                captchas.append(sel)
        except Exception:
            pass

    if captchas:
        log_msg(f"  🤖 CAPTCHA detectado: {captchas}", log_file)
    else:
        log_msg(f"  ✅ Nenhum captcha detectado", log_file)

    # Verificar se textarea está visível (login OK)
    try:
        textarea = await page.query_selector("textarea")
        if textarea and await textarea.is_visible():
            log_msg(f"  🎉 TEXTAREA VISÍVEL — login bem-sucedido!", log_file)
            return True
    except Exception:
        pass

    return False


async def run_debug_login(email, password, account_label, headless=True):
    """Executa login passo-a-passo com screenshots."""
    # Preparar pasta
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = DEBUG_DIR / "debug.log"

    # Limpar prints antigos
    for f in DEBUG_DIR.glob("*.png"):
        f.unlink()
    for f in DEBUG_DIR.glob("*.html"):
        f.unlink()
    if log_file.exists():
        log_file.unlink()

    log_msg("=" * 60, log_file)
    log_msg(f"LOGIN DEBUG — {account_label}", log_file)
    log_msg(f"Email: {email}", log_file)
    log_msg(f"Headless: {headless}", log_file)
    log_msg(f"Pasta: {DEBUG_DIR}", log_file)
    log_msg("=" * 60, log_file)

    # Perfil temporário limpo
    profile_dir = f"/tmp/chrome_login_debug_{int(time.time())}"
    if os.path.exists(profile_dir):
        import shutil
        shutil.rmtree(profile_dir, ignore_errors=True)

    async with async_playwright() as p:
        log_msg("\n=== ETAPA 0: Inicialização do Chrome ===", log_file)
        launch_kwargs = {
            "user_data_dir": profile_dir,
            "headless": headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                "--disable-gpu",
                "--no-sandbox",
            ],
            "no_viewport": True,
        }
        # Detectar Chrome
        chrome_paths = ["/opt/google/chrome/chrome", "/usr/bin/google-chrome"]
        if any(os.path.isfile(p) for p in chrome_paths):
            launch_kwargs["channel"] = "chrome"
            log_msg("  Usando Google Chrome do sistema", log_file)

        ctx = await p.chromium.launch_persistent_context(**launch_kwargs)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        try:
            # ETAPA 1: Navegar para Qwen
            log_msg("\n=== ETAPA 1: Navegar para https://chat.qwen.ai/ ===", log_file)
            try:
                await page.goto("https://chat.qwen.ai/", wait_until="domcontentloaded", timeout=60000)
                log_msg("  ✅ Página carregou (domcontentloaded)", log_file)
            except Exception as e:
                log_msg(f"  ❌ goto falhou: {e}", log_file)
            await page.wait_for_timeout(2000)
            await capture_state(page, "01_pagina_inicial", DEBUG_DIR, log_file)

            # ETAPA 2: Navegar para /auth
            log_msg("\n=== ETAPA 2: Navegar para https://chat.qwen.ai/auth ===", log_file)
            try:
                await page.goto("https://chat.qwen.ai/auth", wait_until="domcontentloaded", timeout=60000)
                log_msg("  ✅ Página de auth carregou", log_file)
            except Exception as e:
                log_msg(f"  ❌ goto /auth falhou: {e}", log_file)
            await page.wait_for_timeout(2000)
            await capture_state(page, "02_apos_navegar_auth", DEBUG_DIR, log_file)

            # ETAPA 3: Preencher email
            log_msg("\n=== ETAPA 3: Preencher email ===", log_file)
            email_input = page.locator('input[name="email"]')
            try:
                await email_input.wait_for(state="visible", timeout=15000)
                await email_input.fill("")
                await email_input.fill(email)
                await page.wait_for_timeout(500)
                log_msg(f"  ✅ Email preenchido: {email}", log_file)
            except Exception as e:
                log_msg(f"  ❌ Falha ao preencher email: {e}", log_file)
            await capture_state(page, "03_email_preenchido", DEBUG_DIR, log_file)

            # ETAPA 4: Preencher senha
            log_msg("\n=== ETAPA 4: Preencher senha ===", log_file)
            password_input = page.locator('input[name="password"]')
            try:
                await password_input.wait_for(state="visible", timeout=15000)
                await password_input.fill("")
                await password_input.fill(password)
                await page.wait_for_timeout(500)
                log_msg(f"  ✅ Senha preenchida ({len(password)} chars)", log_file)
            except Exception as e:
                log_msg(f"  ❌ Falha ao preencher senha: {e}", log_file)
            await capture_state(page, "04_senha_preenchida", DEBUG_DIR, log_file)

            # ETAPA 5: Clicar Sign In
            log_msg("\n=== ETAPA 5: Clicar Sign In ===", log_file)
            submit_btn = page.locator('button[type="submit"]')
            try:
                await submit_btn.wait_for(state="visible", timeout=10000)
                await page.wait_for_timeout(500)
                await submit_btn.click()
                log_msg("  ✅ Botão Sign In clicado", log_file)
            except Exception as e:
                log_msg(f"  ❌ Falha ao clicar Sign In: {e}", log_file)
            await page.wait_for_timeout(1000)
            await capture_state(page, "05_apos_click_signin", DEBUG_DIR, log_file)

            # ETAPA 6: Esperar resultado (até LOGIN_TIMEOUT_SEC)
            log_msg(f"\n=== ETAPA 6: Aguardar resultado do login ({LOGIN_TIMEOUT_SEC}s max) ===", log_file)
            start_wait = time.time()
            login_ok = False
            last_url = page.url

            while time.time() - start_wait < LOGIN_TIMEOUT_SEC:
                elapsed = int(time.time() - start_wait)
                current_url = page.url

                # Detectar mudança de URL
                if current_url != last_url:
                    log_msg(f"  🔄 URL mudou: {last_url} → {current_url}", log_file)
                    last_url = current_url

                # Verificar se textarea apareceu (login OK)
                try:
                    textarea = await page.query_selector("textarea")
                    if textarea and await textarea.is_visible():
                        log_msg(f"  🎉 LOGIN OK em {elapsed}s — textarea visível!", log_file)
                        login_ok = True
                        break
                except Exception:
                    pass

                # Screenshot a cada 10s
                if elapsed > 0 and elapsed % 10 == 0 and elapsed != int(time.time() - start_wait - 1):
                    pass  # evita duplo log

                await page.wait_for_timeout(500)

            # Screenshot final do estado após espera
            if not login_ok:
                log_msg(f"  ⏰ Timeout de {LOGIN_TIMEOUT_SEC}s — login não concluiu", log_file)
            await capture_state(page, "06_estado_final", DEBUG_DIR, log_file)

            # ETAPA 7: Verificação final
            log_msg("\n=== ETAPA 7: Verificação final ===", log_file)
            log_msg(f"  URL final: {page.url}", log_file)
            try:
                title = await page.title()
                log_msg(f"  Title final: {title}", log_file)
            except Exception:
                pass

            if login_ok:
                log_msg("\n" + "=" * 60, log_file)
                log_msg("✅ LOGIN BEM-SUCEDIDO", log_file)
                log_msg("=" * 60, log_file)
            else:
                log_msg("\n" + "=" * 60, log_file)
                log_msg("❌ LOGIN FALHOU", log_file)
                log_msg("Analise os screenshots em:", log_file)
                log_msg(f"  {DEBUG_DIR}", log_file)
                log_msg("E o debug.log para timeline completa.", log_file)
                log_msg("=" * 60, log_file)

        finally:
            try:
                await ctx.close()
            except Exception:
                pass
            # Limpar perfil temporário
            try:
                import shutil
                shutil.rmtree(profile_dir, ignore_errors=True)
            except Exception:
                pass

    return login_ok


def main():
    parser = argparse.ArgumentParser(description="Login debug com screenshots")
    parser.add_argument("--email", help="Email (sobrescreve accounts.json)")
    parser.add_argument("--password", help="Senha (sobrescreve accounts.json)")
    parser.add_argument("--conta", type=int, default=1,
                        help="Número da conta no accounts.json (1-indexed, default: 1)")
    parser.add_argument("--no-headless", action="store_true",
                        help="Mostrar Chrome (não usar headless)")
    args = parser.parse_args()

    email, password, label = load_account(
        args.conta, args.email, args.password
    )
    headless = not args.no_headless

    print(f"\n🔐 Login Debug — {label}")
    print(f"   Email: {email}")
    print(f"   Headless: {headless}")
    print(f"   Pasta: {DEBUG_DIR}\n")

    sucesso = asyncio.run(run_debug_login(email, password, label, headless=headless))
    sys.exit(0 if sucesso else 1)


if __name__ == "__main__":
    main()
