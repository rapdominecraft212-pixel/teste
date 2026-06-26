#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║  Kwai-Editor GitHub Bridge — Script Único de Acesso        ║
║                                                              ║
║  Tudo que o GLM precisa para acessar, visualizar e          ║
║  modificar o repositório Kwai-Editor no GitHub.             ║
║                                                              ║
║  Basta enviar este arquivo para o GLM. Ele saberá usá-lo.   ║
╚══════════════════════════════════════════════════════════════╝

USO:
    python3 kwai_bridge.py setup     # Tudo: SSH + fetch + sincroniza + mostra estrutura
    python3 kwai_bridge.py tree      # Mostra árvore completa do repo
    python3 kwai_bridge.py read PATH # Lê arquivo (ex: read Kwai-Editor/pipeline/simple.py)
    python3 kwai_bridge.py info      # Informações completas + estrutura documentada
    python3 kwai_bridge.py status    # Status do git
    python3 kwai_bridge.py diff      # Mostra diff das mudanças
    python3 kwai_bridge.py push MSG  # Commit + push (ex: push "fix: corrige bug")
    python3 kwai_bridge.py shell CMD # Comando git qualquer (ex: shell "log --oneline -5")
    python3 kwai_bridge.py ssh       # Só configura SSH (sem fetch)
"""

import os, sys, subprocess, textwrap

# ═══════════════════════════════════════════════════════════════
#  CONFIGURAÇÃO EMBUTIDA — NÃO PRECISA DE NADA EXTERNO
# ═══════════════════════════════════════════════════════════════

REPO_OWNER = "rapdominecraft212-pixel"
REPO_NAME = "teste"
REPO_BRANCH = "main"
REPO_URL_SSH = f"git@github.com:{REPO_OWNER}/{REPO_NAME}.git"
KEY_INSTALL_PATH = "/home/z/.ssh/id_ed25519_glm"
GIT_USER_NAME = "GLM-Agent"
GIT_USER_EMAIL = "glm@agent.local"

# Chave SSH privada (formato OpenSSH) — o marcador [REDACTED] é substituído pelo header
_SSH_KEY_RAW = (
    "[REDACTED:ssh_private_key]\n"
    "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZWQyNTUx\n"
    "OQAAACDn3MVcF0NHrvWceUH24K93wKjv1xW6t5r64tJInoB+qAAAAIiYJGE1mCRhNQAAAAtzc2gt\n"
    "ZWQyNTUxOQAAACDn3MVcF0NHrvWceUH24K93wKjv1xW6t5r64tJInoB+qAAAAEDZVqBFzFIHDWpt\n"
    "kKJF2voKdXOajCQDUHEWT7iFPhfS6ufcxVwXQ0eu9Zx5Qfbgr3fAqO/XFbq3mvri0kiegH6oAAAA\n"
    "AAECAwQF\n"
    "-----END OPENSSH PRIVATE KEY-----\n"
)

# ESTRUTURA DO REPOSITÓRIO — referência para nunca se perder
REPO_STRUCTURE_DOC = """
╔══════════════════════════════════════════════════════════════════╗
║  ESTRUTURA DO REPOSITÓRIO (rapdominecraft212-pixel/teste)       ║
║  Atualizada: 2026-06-26                                         ║
╚══════════════════════════════════════════════════════════════════╝

RAIZ DO REPO (/home/z/my-project/)
│
├── Kwai-Editor/                        ← EDITOR ATIVO (código principal)
│   ├── .env                            ← tokens Telegram (exposto intencionalmente)
│   ├── .env.example                    ← template do .env
│   ├── .gitignore
│   ├── AGENTS.md
│   ├── Playwright/                     ← automação Qwen AI
│   │   ├── chrome_profile/             ← sessão logada do Chrome (grande, binário)
│   │   ├── chrome_profile_{1,2,3}/     ← perfis extras
│   │   ├── login_setup.py              ← login manual no Qwen
│   │   ├── qwen_reply.py               ← SYNC (legado, 3 abas sequenciais)
│   │   ├── qwen_reply_async.py         ← ASYNC (atual, paralelo, com retry)
│   │   ├── qwen_capa.py                ← prompt capa (legado)
│   │   ├── qwen_titulo.py              ← prompt titulo (legado)
│   │   ├── qwen_capa_titulo.py         ← prompt UNIFICADO (atual, Observe→Infer→Create)
│   │   └── qwen_linha.py               ← prompt linha de corte (parser tolerante)
│   ├── bot/                            ← bot Telegram
│   │   ├── worker.py                   ← processa jobs (chama pipeline)
│   │   ├── listener.py                 ← ouve mensagens Telegram
│   │   ├── terminal_bot.py             ← modo terminal interativo
│   │   ├── db.py                       ← SQLite
│   │   ├── baixar_video.py             ← download vídeos Kwai
│   │   ├── checar_link.py              ← validação URL
│   │   ├── telegram_utils.py           ← utilitários Telegram
│   │   ├── log_utils.py                ← logging formatado
│   │   └── scripts/                    ← scripts auxiliares do bot
│   ├── pipeline/                       ← pipeline de edição
│   │   ├── simple.py                   ← PRINCIPAL (async, 2 abas paralelas)
│   │   ├── editor.py                   ← interface do editor
│   │   └── runner.py                   ← runner de pipeline
│   ├── src/                            ← processamento de vídeo
│   │   ├── video_popup_linear.py       ← renderização com popups
│   │   ├── cortar_video.py             ← corte por coordenadas
│   │   ├── grid_utils.py               ← imagem grid numerada (ROWS=80)
│   │   ├── renderizar.py               ← renderização geral
│   │   ├── colocar_linha.py            ← overlay de linha
│   │   ├── cortar_minuto.py            ← corte por minuto
│   │   ├── cortar_resolusao.py         ← ajuste resolução
│   │   ├── gemini_analyzer.py          ← analyzer Gemini (legado)
│   │   ├── display.py                  ← display progresso
│   │   └── key_manager.py              ← gerenciamento API keys
│   ├── captura_qwen/                   ← captura de tela (debug)
│   ├── tests/                          ← testes
│   ├── scripts/                        ← scripts de inicialização
│   ├── logs/                           ← logs de execução
│   ├── *.bat                           ← atalhos Windows
│   └── requirements.txt
│
├── Kwai-Editor (backup: 2)/            ← BACKUP ANTIGO (pré-async)
│
├── armazém/                            ← BACKUPS EM ZIP (FORA do editor!)
│   ├── git_backup1.zip                 ← versão original (142KB)
│   └── git_backup2.zip                 ← checkpoint com async (340KB)
│
├── scripts/                            ← scripts do ambiente
│   └── threaded-ssh-proxy.py           ← proxy SSH para git push via paramiko
│
├── skills/                             ← skills do agente GLM
│
├── upload/                             ← arquivos enviados pelo usuário
│
├── .env                                ← variáveis de ambiente global
└── .gitignore

BRANCHES:
  main                                ← branch de trabalho (Kwai-Editor ativo)
  glm                                 ← lixo histórico (bridge agent, FPS, petshop)
  fps-em-vista-superior-com-ia-*     ← jogo FPS em Canvas
  petsship-file-inquiry-*            ← site petshop

FLUXO DO PIPELINE:
  1. Bot recebe link Kwai via Telegram
  2. Baixa vídeo
  3. Qwen em 2 abas PARALELAS (async):
     - Aba 1: capa+titulo (prompt unificado Observe→Infer→Create)
     - Aba 2: linha de corte (grid numerado, 80 linhas)
  4. Corta vídeo na área útil (sem artefatos de edição)
  5. Renderiza com popups (capa + título)
  6. Envia vídeo editado de volta ao Telegram

ARQUIVOS-CHAVE (modificar com cuidado):
  Kwai-Editor/pipeline/simple.py              ← pipeline principal (async)
  Kwai-Editor/Playwright/qwen_reply_async.py  ← QwenReply async (retry)
  Kwai-Editor/Playwright/qwen_capa_titulo.py  ← prompt unificado
  Kwai-Editor/Playwright/qwen_linha.py        ← prompt de linha
  Kwai-Editor/bot/worker.py                   ← entrada do bot

IMPORTANTE:
  - O git root é /home/z/my-project/ (NÃO é Kwai-Editor/)
  - O armazém fica NA RAIZ, fora do Kwai-Editor
  - Para push, usar este script ou o proxy SSH em scripts/
"""


# ═══════════════════════════════════════════════════════════════
#  SETUP SSH
# ═══════════════════════════════════════════════════════════════

def _ensure_paramiko():
    try:
        import paramiko
        return paramiko
    except ImportError:
        print("  Instalando paramiko...")
        subprocess.run([sys.executable, "-m", "pip", "install", "paramiko",
                        "-q", "--break-system-packages"], timeout=60)
        import paramiko
        return paramiko


def setup_ssh(repo_dir=None):
    """Configura chave SSH, git config e remote."""
    _ensure_paramiko()
    ssh_dir = os.path.dirname(KEY_INSTALL_PATH)
    os.makedirs(ssh_dir, mode=0o700, exist_ok=True)

    # Escrever chave privada
    key_content = _SSH_KEY_RAW.replace("[REDACTED:ssh_private_key]",
                                        "-----BEGIN OPENSSH PRIVATE KEY-----")
    with open(KEY_INSTALL_PATH, "w") as f:
        f.write(key_content)
    os.chmod(KEY_INSTALL_PATH, 0o600)

    # Escrever SSH config
    with open(os.path.join(ssh_dir, "config"), "w") as f:
        f.write(f"Host github.com\n    HostName github.com\n    User git\n"
                f"    IdentityFile {KEY_INSTALL_PATH}\n    StrictHostKeyChecking no\n")
    os.chmod(os.path.join(ssh_dir, "config"), 0o600)

    # Garantir proxy SSH
    proxy_path = _ensure_proxy(repo_dir)

    # Git config
    if not repo_dir:
        repo_dir = _find_repo_dir()
    subprocess.run(["git", "config", "user.name", GIT_USER_NAME], cwd=repo_dir, capture_output=True)
    subprocess.run(["git", "config", "user.email", GIT_USER_EMAIL], cwd=repo_dir, capture_output=True)
    subprocess.run(["git", "config", "core.sshCommand", f"{sys.executable} {proxy_path}"], cwd=repo_dir, capture_output=True)

    # Remote
    result = subprocess.run(["git", "remote", "-v"], cwd=repo_dir, capture_output=True, text=True)
    if REPO_OWNER not in result.stdout:
        subprocess.run(["git", "remote", "add", "origin", REPO_URL_SSH], cwd=repo_dir, capture_output=True)

    print("  ✅ SSH configurado!")
    return repo_dir


def _ensure_proxy(repo_dir=None):
    """Garante que o proxy SSH existe."""
    candidates = [
        "/home/z/my-project/scripts/threaded-ssh-proxy.py",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "threaded-ssh-proxy.py"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c

    proxy_dir = "/home/z/my-project/scripts"
    os.makedirs(proxy_dir, exist_ok=True)
    path = os.path.join(proxy_dir, "threaded-ssh-proxy.py")
    with open(path, "w") as f:
        f.write(PROXY_SCRIPT)
    os.chmod(path, 0o755)
    return path


# ═══════════════════════════════════════════════════════════════
#  PROXY SSH (embutido)
# ═══════════════════════════════════════════════════════════════

PROXY_SCRIPT = r'''#!/usr/bin/env python3
import paramiko, sys, os, threading, fcntl, time

SSH_KEY_PATH = "/home/z/.ssh/id_ed25519_glm"

def fwd_in(ch, stop):
    try:
        while not stop.is_set():
            try:
                d = os.read(sys.stdin.fileno(), 65536)
                if not d: break
                ch.sendall(d)
            except (BlockingIOError, OSError): stop.wait(0.01)
            except: break
    except: pass
    finally:
        try: ch.shutdown_write()
        except: pass

def fwd_out(ch, stop):
    try:
        while not stop.is_set():
            if ch.recv_ready():
                d = ch.recv(65536)
                if not d: break
                sys.stdout.buffer.write(d)
                sys.stdout.buffer.flush()
            elif ch.exit_status_ready(): break
            else: stop.wait(0.01)
    except: pass

def main():
    args = sys.argv[1:]
    hostname = username = git_cmd = None
    port = 22
    i = 0
    while i < len(args):
        if args[i] == "-o" and i+1 < len(args): i += 2; continue
        elif args[i] == "-p" and i+1 < len(args): port = int(args[i+1]); i += 2; continue
        elif "@" in args[i] and not hostname:
            username, hostname = args[i].split("@", 1)
        elif not git_cmd: git_cmd = args[i]
        i += 1
    if not hostname or not git_cmd: sys.exit(1)
    try:
        key = paramiko.Ed25519Key.from_private_key_file(SSH_KEY_PATH)
    except Exception as e:
        print(f"SSH key error: {e}", file=sys.stderr); sys.exit(1)
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(hostname, port=port, username=username, pkey=key,
                      timeout=15, allow_agent=False, look_for_keys=False)
        ch = client.get_transport().open_session()
        ch.exec_command(git_cmd)
        try:
            fl = fcntl.fcntl(sys.stdin.fileno(), fcntl.F_GETFL)
            fcntl.fcntl(sys.stdin.fileno(), fcntl.F_SETFL, fl | os.O_NONBLOCK)
        except: pass
        stop = threading.Event()
        threading.Thread(target=fwd_in, args=(ch, stop), daemon=True).start()
        threading.Thread(target=fwd_out, args=(ch, stop), daemon=True).start()
        while not ch.exit_status_ready(): stop.wait(0.1)
        time.sleep(0.2); stop.set()
        rc = ch.recv_exit_status()
        ch.close(); client.close()
        sys.exit(rc)
    except Exception as e:
        print(f"SSH error: {e}", file=sys.stderr); sys.exit(1)

if __name__ == "__main__": main()
'''


# ═══════════════════════════════════════════════════════════════
#  UTILIDADES
# ═══════════════════════════════════════════════════════════════

def _find_repo_dir():
    """Encontra o diretório raiz do repo git."""
    current = os.path.dirname(os.path.abspath(__file__))
    for _ in range(10):
        if os.path.exists(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return "/home/z/my-project"


def _human_size(size):
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.0f}{unit}"
        size /= 1024
    return f"{size:.0f}TB"


# ═══════════════════════════════════════════════════════════════
#  VISUALIZAÇÃO
# ═══════════════════════════════════════════════════════════════

def show_tree(repo_dir=None, max_depth=4):
    repo_dir = repo_dir or _find_repo_dir()
    print(f"\n{repo_dir}/")
    _tree(repo_dir, "", max_depth,
          {".git", "__pycache__", "node_modules",
           "chrome_profile", "chrome_profile_1", "chrome_profile_2", "chrome_profile_3"})
    print()


def _tree(path, prefix, max_depth, skip, depth=0):
    if depth >= max_depth:
        print(f"{prefix}... (max depth)")
        return
    try:
        entries = sorted(os.listdir(path))
    except PermissionError:
        return
    items = []
    for e in entries:
        if e in skip:
            continue
        full = os.path.join(path, e)
        items.append((e, os.path.isdir(full), full))
    for i, (name, is_dir, full) in enumerate(items):
        last = (i == len(items) - 1)
        conn = "└── " if last else "├── "
        child = "    " if last else "│   "
        if is_dir:
            try:
                count = sum(1 for _ in os.walk(full) for _ in _[2])
                print(f"{prefix}{conn}{name}/ ({count} files)")
            except:
                print(f"{prefix}{conn}{name}/")
            _tree(full, prefix + child, max_depth, skip, depth + 1)
        else:
            sz = _human_size(os.path.getsize(full))
            print(f"{prefix}{conn}{name}  ({sz})")


def show_info():
    repo_dir = _find_repo_dir()
    print("\n" + "=" * 60)
    print("  KWAII-EDITOR GITHUB BRIDGE")
    print("=" * 60)
    print(f"\n  GitHub: github.com/{REPO_OWNER}/{REPO_NAME}")
    print(f"  Branch: {REPO_BRANCH}")
    print(f"  Local:  {repo_dir}")

    print("\n--- Git Status ---")
    subprocess.run(["git", "status", "--short"], cwd=repo_dir)

    print("\n--- Last 5 Commits ---")
    subprocess.run(["git", "log", "--oneline", "-5"], cwd=repo_dir)

    print(REPO_STRUCTURE_DOC)

    print("\n--- Real Structure ---")
    show_tree(repo_dir, max_depth=3)


# ═══════════════════════════════════════════════════════════════
#  LEITURA
# ═══════════════════════════════════════════════════════════════

def read_file(path, repo_dir=None):
    repo_dir = repo_dir or _find_repo_dir()
    full = os.path.join(repo_dir, path)
    if not os.path.exists(full):
        alt = os.path.join(repo_dir, "Kwai-Editor", path)
        if os.path.exists(alt):
            full = alt
        else:
            print(f"Not found: {path}")
            return
    print(f"\n--- {full} ---")
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            print(f.read())
    except Exception as e:
        print(f"Error: {e}")


# ═══════════════════════════════════════════════════════════════
#  GIT
# ═══════════════════════════════════════════════════════════════

def _with_ssh(repo_dir):
    proxy = _ensure_proxy(repo_dir)
    subprocess.run(["git", "config", "core.sshCommand", f"{sys.executable} {proxy}"],
                   cwd=repo_dir, capture_output=True)


def git_status(repo_dir=None):
    repo_dir = repo_dir or _find_repo_dir()
    subprocess.run(["git", "status"], cwd=repo_dir)


def git_diff(repo_dir=None):
    repo_dir = repo_dir or _find_repo_dir()
    subprocess.run(["git", "diff"], cwd=repo_dir)
    subprocess.run(["git", "diff", "--cached"], cwd=repo_dir)


def git_push(message, repo_dir=None):
    repo_dir = repo_dir or _find_repo_dir()
    _with_ssh(repo_dir)
    print(f'\nPush: "{message}"')
    subprocess.run(["git", "add", "-A"], cwd=repo_dir)
    r = subprocess.run(["git", "commit", "-m", message], cwd=repo_dir, capture_output=True, text=True)
    print(r.stdout)
    if r.stderr and "nothing to commit" not in r.stderr:
        print(r.stderr)
    r = subprocess.run(["git", "push", "origin", REPO_BRANCH], cwd=repo_dir, capture_output=True, text=True)
    print(r.stdout)
    if r.returncode != 0:
        print(f"Push failed: {r.stderr}")
    else:
        print("Push OK!")


def git_fetch(repo_dir=None):
    repo_dir = repo_dir or _find_repo_dir()
    _with_ssh(repo_dir)
    print("Fetching...")
    subprocess.run(["git", "fetch", "origin"], cwd=repo_dir)


def git_shell(cmd, repo_dir=None):
    repo_dir = repo_dir or _find_repo_dir()
    parts = ["git"] + cmd.split()
    print(f"$ {' '.join(parts)}")
    subprocess.run(parts, cwd=repo_dir)


# ═══════════════════════════════════════════════════════════════
#  SETUP COMPLETO
# ═══════════════════════════════════════════════════════════════

def full_setup():
    print("\n=== Full Setup ===")
    print("1. SSH...")
    repo_dir = setup_ssh()

    print("2. Fetch...")
    _with_ssh(repo_dir)
    r = subprocess.run(["git", "fetch", "origin"], cwd=repo_dir, capture_output=True, text=True)
    print(f"  {'OK' if r.returncode == 0 else 'WARN'}: {r.stderr[:80] if r.stderr else ''}")

    print("3. Sync with remote...")
    subprocess.run(["git", "reset", "--hard", f"origin/{REPO_BRANCH}"],
                   cwd=repo_dir, capture_output=True)

    print("4. Structure:")
    show_tree(repo_dir, max_depth=3)
    print("Done! Use tree/read/push/info to work.")


# ═══════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1].lower()
    args = sys.argv[2:]

    if cmd == "setup":
        full_setup()
    elif cmd == "tree":
        show_tree(max_depth=int(args[0]) if args else 4)
    elif cmd == "read":
        if not args:
            print("Usage: kwai_bridge.py read PATH")
            print("Ex:    kwai_bridge.py read Kwai-Editor/pipeline/simple.py")
            return
        read_file(args[0])
    elif cmd == "info":
        show_info()
    elif cmd == "status":
        git_status()
    elif cmd == "diff":
        git_diff()
    elif cmd == "push":
        if not args:
            print('Usage: kwai_bridge.py push "commit message"')
            return
        git_push(" ".join(args))
    elif cmd == "fetch":
        git_fetch()
    elif cmd == "shell":
        if not args:
            print('Usage: kwai_bridge.py shell "git command"')
            return
        git_shell(" ".join(args))
    elif cmd == "ssh":
        setup_ssh()
        print("SSH configured!")
    else:
        print(f"Unknown: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
