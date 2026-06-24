"""Run this ONCE to log in to Qwen.
The Chrome profile will be saved with your login cookies."""
from pathlib import Path
from playwright.sync_api import sync_playwright
import time

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILE_DIR = BASE_DIR / "Playwright" / "chrome_profile"
PROFILE_DIR.mkdir(parents=True, exist_ok=True)

print("=== LOGIN SETUP ===")
print("O browser vai abrir.")
print("1. Faca login no Qwen.ai")
print("2. FECHE o browser quando terminar")

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        channel="chrome",
        headless=False,
    )
    page = context.pages[0] if context.pages else context.new_page()
    page.goto("https://chat.qwen.ai/")

    # Wait until browser is closed by user
    while context.pages:
        time.sleep(1)

    context.close()

print("\nPerfil salvo. Agora pode rodar qwen_validacao.py.")
