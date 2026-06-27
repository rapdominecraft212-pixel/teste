#!/usr/bin/env python3
"""
Setup Manual de Contas Qwen — Captura sessões para uso persistente.

Abre TODAS as contas do accounts.json em Chormes visíveis EM PARALELO.
Você resolve captcha + faz login manualmente em cada uma (na ordem que quiser).
O script detecta automaticamente quando cada uma está logada (textarea visível)
e salva a sessão mínima (cookies + localStorage + IndexedDB) em disco.

USO:
    cd Kwai-Editor/Playwright
    python setup_accounts.py                    # setup de todas as contas
    python setup_accounts.py --conta 3          # setup só da conta 3
    python setup_accounts.py --conta 3,5,7      # setup das contas 3, 5 e 7
    python setup_accounts.py --listar           # lista sessões já salvas
    python setup_accounts.py --remover conta_3  # remove sessão da conta 3

FLUXO:
    1. Script abre 7 janelas Chrome (uma por conta) navegando para /auth
    2. Você resolve captcha + digita email/senha em cada uma (paralelo, ordem livre)
    3. Quando você termina uma, o script detecta (textarea visível) e:
       - Extrai cookies + localStorage + IndexedDB
       - Salva em Playwright/sessions/conta_N.json (~20-150KB)
       - Fecha aquela janela Chrome
    4. Quando todas terminam, script encerra

APÓS SETUP:
    Próximas runs do worker.py / terminal_bot.py usarão as sessões salvas
    automaticamente — SEM login, SEM captcha, em ~5s.

SE SESSÃO EXPIRAR (após 7-30 dias):
    Rode novamente: python setup_accounts.py --conta N
    Onde N é o número da conta expirada.
"""
import asyncio
import json
import sys
import argparse
import shutil
import time
import os
from pathlib import Path
from datetime import datetime

# Setup path
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from playwright.async_api import async_playwright

# Importar módulo de sessão
try:
    from qwen_session import (
        extrair_sessao, salvar_sessao, sessao_existe, carregar_sessao,
        listar_sessoes, remover_sessao, get_session_path
    )
except ImportError:
    # Fallback: importar direto do arquivo
    sys.path.insert(0, str(SCRIPT_DIR))
    from qwen_session import (
        extrair_sessao, salvar_sessao, sessao_existe, carregar_sessao,
        listar_sessoes, remover_sessao, get_session_path
    )


ACCOUNTS_FILE = SCRIPT_DIR / "accounts.json"


def log(msg):
    """Log com timestamp."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def carregar_contas():
    """Carrega contas do accounts.json."""
    if not ACCOUNTS_FILE.exists():
        print(f"❌ {ACCOUNTS_FILE} não encontrado")
        sys.exit(1)
    contas = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    return contas


def detectar_chrome_channel():
    """Detecta Google Chrome do sistema (Windows ou Linux)."""
    # Windows
    win_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for p in win_paths:
        if os.path.isfile(p):
            return "chrome"

    # Linux
    linux_paths = [
        "/opt/google/chrome/chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ]
    for p in linux_paths:
        if os.path.isfile(p):
            return "chrome"

    return None  # usa Chromium embutido do Playwright


async def esperar_login(page, conta_id, email):
    """Espera até que textarea apareça (login bem-sucedido)."""
    while True:
        try:
            textarea = await page.query_selector("textarea")
            if textarea and await textarea.is_visible():
                return True
        except Exception:
            pass
        await asyncio.sleep(2)


async def setup_uma_conta(playwright, conta_idx, conta):
    """Setup de uma conta: abre Chrome visível, aguarda login manual, salva sessão."""
    conta_id = f"conta_{conta_idx}"
    email = conta["email"]
    senha = conta["password"]

    log(f"[{conta_id}] Abrindo Chrome visível para {email}...")

    # Perfil temporário (não persistente — só para setup)
    profile_dir = Path(tempfile.gettempdir()) / f"kwai_setup_{conta_id}_{int(time.time())}"
    if profile_dir.exists():
        shutil.rmtree(profile_dir, ignore_errors=True)

    try:
        launch_kwargs = {
            "user_data_dir": str(profile_dir),
            "headless": False,  # VISÍVEL — usuário precisa resolver captcha
            "no_viewport": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
            ],
        }
        channel = detectar_chrome_channel()
        if channel:
            launch_kwargs["channel"] = channel

        ctx = await playwright.chromium.launch_persistent_context(**launch_kwargs)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        # Navegar para página de login
        log(f"[{conta_id}] Navegando para https://chat.qwen.ai/auth...")
        try:
            await page.goto("https://chat.qwen.ai/auth", wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log(f"[{conta_id}] ⚠️ goto lento: {e}")

        # Pré-preencher email e senha (opcional — usuário ainda precisa clicar Sign In + captcha)
        try:
            await page.wait_for_selector('input[name="email"]', timeout=15000)
            await page.fill('input[name="email"]', email)
            await page.fill('input[name="password"]', senha)
            log(f"[{conta_id}] Email e senha pré-preenchidos — "
                f"resolva o captcha e clique em Sign In")
        except Exception as e:
            log(f"[{conta_id}] ⚠️ Não conseguiu preencher form: {e}")
            log(f"[{conta_id}] Faça login manualmente")

        # Esperar login manual (textarea visível)
        login_ok = await esperar_login(page, conta_id, email)
        if login_ok:
            log(f"[{conta_id}] 🎉 Login detectado! Aguardando 3s para estabilizar...")
            await asyncio.sleep(3)

            # Capturar sessão
            log(f"[{conta_id}] Extraindo sessão...")
            sessao = await extrair_sessao(ctx, page)

            # Salvar
            path = salvar_sessao(conta_id, sessao)
            log(f"[{conta_id}] ✅ Sessão salva em {path.name}")
        else:
            log(f"[{conta_id}] ❌ Login não detectado (timeout)")

        # Fechar este Chrome
        try:
            await ctx.close()
        except Exception:
            pass

    except Exception as e:
        log(f"[{conta_id}] ❌ Erro: {e}")
    finally:
        # Limpar perfil temporário
        try:
            if profile_dir.exists():
                shutil.rmtree(profile_dir, ignore_errors=True)
        except Exception:
            pass


async def setup_varias_contas(contas_indices: list[int], contas: list[dict]):
    """Setup de várias contas em paralelo."""
    log(f"Iniciando setup de {len(contas_indices)} conta(s): {contas_indices}")
    log(f"Serão abertas {len(contas_indices)} janelas Chrome em paralelo.")
    log(f"Resolva o captcha e faça login em cada uma (ordem livre).")
    log(f"Cada janela fecha automaticamente após login detectado.")
    log("")

    async with async_playwright() as p:
        # Subir todas em paralelo
        tasks = [
            setup_uma_conta(p, idx, contas[idx - 1])
            for idx in contas_indices
            if 1 <= idx <= len(contas)
        ]
        await asyncio.gather(*tasks)

    log("")
    log("=" * 60)
    log("Setup concluído!")
    log("=" * 60)
    log("")
    log("Sessões salvas:")
    sessoes = listar_sessoes()
    if not sessoes:
        log("  (nenhuma)")
    else:
        for s in sessoes:
            size_kb = s.get("size_bytes", 0) / 1024
            log(f"  - {s['account_id']}: {size_kb:.1f}KB "
                f"({s.get('cookies_count', '?')} cookies, "
                f"{s.get('local_storage_keys', '?')} LS keys, "
                f"{s.get('indexeddb_dbs', '?')} IDB dbs)")
    log("")
    log("Próximas runs do worker.py / terminal_bot.py usarão estas sessões automaticamente.")


def cmd_listar():
    """Lista sessões já salvas."""
    print("\n📁 Sessões salvas em Playwright/sessions/:")
    print("")
    sessoes = listar_sessoes()
    if not sessoes:
        print("  (nenhuma sessão salva)")
        print(f"\n  Para criar: python {Path(__file__).name}")
        return

    print(f"  {'Conta':<15} {'Tamanho':<10} {'Cookies':<10} {'LS':<8} {'IDB':<8} {'Salva em'}")
    print(f"  {'-'*15} {'-'*10} {'-'*10} {'-'*8} {'-'*8} {'-'*30}")
    for s in sessoes:
        size_kb = s.get("size_bytes", 0) / 1024
        print(f"  {s['account_id']:<15} {size_kb:>6.1f}KB   "
              f"{s.get('cookies_count', '?'):<10} "
              f"{s.get('local_storage_keys', '?'):<8} "
              f"{s.get('indexeddb_dbs', '?'):<8} "
              f"{s.get('saved_at', '?')[:19]}")
    print("")


def cmd_remover(conta_id: str):
    """Remove uma sessão."""
    if not conta_id.startswith("conta_"):
        conta_id = f"conta_{conta_id}"
    if remover_sessao(conta_id):
        print(f"✅ Sessão removida: {conta_id}")
    else:
        print(f"❌ Sessão não encontrada: {conta_id}")


def main():
    parser = argparse.ArgumentParser(
        description="Setup manual de contas Qwen — captura sessões persistentes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXEMPLOS:
    python setup_accounts.py                # setup de todas as contas
    python setup_accounts.py --conta 3      # setup só da conta 3
    python setup_accounts.py --conta 3,5,7  # setup das contas 3, 5 e 7
    python setup_accounts.py --listar       # lista sessões já salvas
    python setup_accounts.py --remover 3    # remove sessão da conta 3
        """,
    )
    parser.add_argument("--conta", type=str,
                        help="Número da conta (1-indexed) ou lista (ex: 3,5,7)")
    parser.add_argument("--listar", action="store_true",
                        help="Lista sessões já salvas")
    parser.add_argument("--remover", type=str,
                        help="Remove sessão de uma conta (ex: 3 ou conta_3)")

    args = parser.parse_args()

    if args.listar:
        cmd_listar()
        return

    if args.remover:
        cmd_remover(args.remover)
        return

    # Setup normal
    contas = carregar_contas()
    log(f"Carregadas {len(contas)} contas de {ACCOUNTS_FILE.name}")

    if args.conta:
        try:
            indices = [int(x.strip()) for x in args.conta.split(",")]
        except ValueError:
            print(f"❌ --conta inválido: {args.conta}")
            sys.exit(1)
    else:
        indices = list(range(1, len(contas) + 1))

    # Validar indices
    for idx in indices:
        if idx < 1 or idx > len(contas):
            print(f"❌ Conta {idx} inválida (tem {len(contas)} contas)")
            sys.exit(1)

    asyncio.run(setup_varias_contas(indices, contas))


if __name__ == "__main__":
    import tempfile
    main()
