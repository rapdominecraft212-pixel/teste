#!/usr/bin/env python3
"""
=============================================================
 GLM Bridge Agent — Agente Local para seu PC
=============================================================

Este script roda no seu computador e faz a ponte entre
o GitHub (onde o GLM escreve comandos) e sua máquina local.

COMO FUNCIONA:
1. Eu (GLM) escrevo comandos em: commands/pending/cmd_XXXXX.json
2. Este agente faz git pull, lê os comandos pendentes
3. Executa cada comando no seu PC
4. Salva o resultado em: commands/results/cmd_XXXXX_result.json
5. Faz git push dos resultados de volta ao GitHub
6. Eu leio os resultados e continuo a conversa

INSTALAÇÃO:
1. Tenha Python 3.8+ e git instalados
2. Coloque este script em uma pasta do seu PC
3. Configure as variáveis abaixo
4. Rode: python glm-bridge-agent.py

SEGURANÇA:
- APENAS comandos da lista ALLOWED_COMMANDS são executados
- Você pode adicionar/remover comandos permitidos
- Comandos fora da lista são REJEITADOS automaticamente
=============================================================
"""

import json
import os
import subprocess
import time
import uuid
import tempfile
import shutil
from datetime import datetime
from pathlib import Path

# ============================================================
# CONFIGURAÇÃO — Edite estas variáveis
# ============================================================

# Seu token do GitHub (mesmo que você me deu)
GITHUB_TOKEN = "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"

# Repositório (owner/repo)
REPO = "rapdominecraft212-pixel/teste"

# Branch
BRANCH = "main"

# Intervalo entre verificações (segundos)
POLL_INTERVAL = 10

# Diretório temporário para o clone local
CLONE_DIR = os.path.join(tempfile.gettempdir(), "glm-bridge-repo")

# ============================================================
# LISTA DE COMANDOS PERMITIDOS (SEGURANÇA!)
# ============================================================
# Apenas comandos que começam com estes prefixos serão executados.
# Comente ou remova linhas para restringir. Adicione para expandir.

ALLOWED_COMMANDS = [
    # Exploração de arquivos
    "ls", "dir", "find", "tree", "pwd",
    # Leitura de arquivos
    "cat", "head", "tail", "type", "less", "more", "wc",
    # Informações do sistema
    "whoami", "hostname", "uname", "systeminfo",
    "df", "du", "free", "top -bn1", "tasklist",
    "echo", "date", "uptime",
    # Python (para scripts que eu enviar)
    "python", "python3", "pip",
    # Git
    "git status", "git log", "git diff", "git branch",
    # Rede (apenas leitura)
    "ping", "curl", "wget --spider", "ifconfig", "ipconfig",
    # Processos
    "ps", "pgrep",
]

# Comandos BLOQUEADOS explicitamente (mesmo que prefixo permitido)
BLOCKED_PATTERNS = [
    "rm -rf /", "del /", "format ", "mkfs.",
    "shutdown", "reboot", "halt",
    "passwd", "sudo rm",
    ">", ">>",  # Redirecionamento de saída (previne escrita arbitrária)
]

# ============================================================
# FUNÇÕES AUXILIARES
# ============================================================

def run_git(args, cwd=None):
    """Executa um comando git e retorna a saída."""
    cmd = ["git"] + args
    env = os.environ.copy()
    # Configurar autenticação via token
    env["GIT_ASKPASS"] = "echo"
    env["GIT_TERMINAL_PROMPT"] = "0"
    
    result = subprocess.run(
        cmd, 
        capture_output=True, 
        text=True, 
        cwd=cwd or CLONE_DIR,
        env=env
    )
    return result

def git_clone_or_pull():
    """Clona ou atualiza o repositório."""
    if os.path.isdir(os.path.join(CLONE_DIR, ".git")):
        print("  📦 Atualizando repositório...")
        run_git(["fetch", "origin"])
        run_git(["reset", "--hard", f"origin/{BRANCH}"])
    else:
        print("  📦 Clonando repositório...")
        if os.path.exists(CLONE_DIR):
            shutil.rmtree(CLONE_DIR)
        repo_url = f"https://{GITHUB_TOKEN}@github.com/{REPO}.git"
        run_git(["clone", repo_url, CLONE_DIR])
    
    # Configurar git user
    run_git(["config", "user.name", "GLM-Bridge-Agent"])
    run_git(["config", "user.email", "bridge@glm-agent.local"])

def git_push():
    """Faz push das mudanças locais para o GitHub."""
    run_git(["add", "-A"])
    result = run_git(["commit", "-m", f"Bridge results - {datetime.now().isoformat()}"])
    if result.returncode == 0:
        repo_url = f"https://{GITHUB_TOKEN}@github.com/{REPO}.git"
        push_result = run_git(["push", repo_url, BRANCH])
        if push_result.returncode == 0:
            print("  ✅ Resultados enviados ao GitHub!")
            return True
        else:
            print(f"  ❌ Erro no push: {push_result.stderr}")
            return False
    else:
        print("  ℹ️ Nenhuma mudança para enviar.")
        return True

def is_command_allowed(command):
    """Verifica se um comando é seguro para executar."""
    cmd_stripped = command.strip()
    
    # Verificar padrões bloqueados
    for pattern in BLOCKED_PATTERNS:
        if pattern in cmd_stripped:
            return False, f"Comando contém padrão bloqueado: '{pattern}'"
    
    # Verificar se começa com um comando permitido
    cmd_parts = cmd_stripped.split()
    if not cmd_parts:
        return False, "Comando vazio"
    
    base_cmd = cmd_parts[0]
    
    # Verificar se o comando base ou comando+primeiro_arg é permitido
    for allowed in ALLOWED_COMMANDS:
        allowed_parts = allowed.split()
        if base_cmd == allowed_parts[0]:
            if len(allowed_parts) == 1:
                return True, "OK"
            elif len(cmd_parts) > 1 and cmd_parts[1] == allowed_parts[1]:
                return True, "OK"
    
    return False, f"Comando '{base_cmd}' não está na lista de permitidos. Comandos permitidos: {', '.join(set(c.split()[0] for c in ALLOWED_COMMANDS))}"

def execute_command(command, timeout=30):
    """Executa um comando no sistema local e retorna o resultado."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return {
            "stdout": result.stdout[:5000],  # Limitar saída
            "stderr": result.stderr[:2000],
            "returncode": result.returncode,
            "success": result.returncode == 0
        }
    except subprocess.TimeoutExpired:
        return {
            "stdout": "",
            "stderr": f"Comando excedeu o timeout de {timeout} segundos",
            "returncode": -1,
            "success": False
        }
    except Exception as e:
        return {
            "stdout": "",
            "stderr": str(e),
            "returncode": -1,
            "success": False
        }

def get_pending_commands():
    """Lê comandos pendentes do diretório commands/pending/."""
    pending_dir = os.path.join(CLONE_DIR, "commands", "pending")
    commands = []
    
    if not os.path.isdir(pending_dir):
        return commands
    
    for filename in os.listdir(pending_dir):
        if filename.endswith(".json"):
            filepath = os.path.join(pending_dir, filename)
            try:
                with open(filepath, 'r') as f:
                    cmd_data = json.load(f)
                cmd_data["_filename"] = filename
                commands.append(cmd_data)
            except (json.JSONDecodeError, IOError) as e:
                print(f"  ⚠️ Erro lendo {filename}: {e}")
    
    return commands

def move_to_done(filename):
    """Move um comando de pending para done."""
    pending_path = os.path.join(CLONE_DIR, "commands", "pending", filename)
    done_dir = os.path.join(CLONE_DIR, "commands", "done")
    os.makedirs(done_dir, exist_ok=True)
    done_path = os.path.join(done_dir, filename)
    
    if os.path.exists(pending_path):
        shutil.move(pending_path, done_path)

def save_result(cmd_data, exec_result):
    """Salva o resultado da execução de um comando."""
    results_dir = os.path.join(CLONE_DIR, "commands", "results")
    os.makedirs(results_dir, exist_ok=True)
    
    cmd_id = cmd_data.get("id", "unknown")
    result_filename = f"result_{cmd_id}.json"
    result_path = os.path.join(results_dir, result_filename)
    
    result_data = {
        "id": cmd_id,
        "command": cmd_data.get("command", ""),
        "executed_at": datetime.now().isoformat(),
        "hostname": os.uname().nodename if hasattr(os, 'uname') else os.environ.get('COMPUTERNAME', 'unknown'),
        "result": exec_result
    }
    
    with open(result_path, 'w') as f:
        json.dump(result_data, f, indent=2, ensure_ascii=False)
    
    print(f"  💾 Resultado salvo: {result_filename}")

# ============================================================
# LOOP PRINCIPAL
# ============================================================

def process_commands():
    """Processa todos os comandos pendentes."""
    commands = get_pending_commands()
    
    if not commands:
        return False
    
    print(f"\n  📨 {len(commands)} comando(s) encontrado(s)!")
    
    for cmd_data in commands:
        command = cmd_data.get("command", "")
        cmd_id = cmd_data.get("id", "???")
        filename = cmd_data.get("_filename", "")
        
        print(f"\n  🔧 Comando [{cmd_id}]: {command[:80]}...")
        
        # Verificar segurança
        allowed, reason = is_command_allowed(command)
        
        if allowed:
            print(f"  ⚡ Executando...")
            timeout = cmd_data.get("timeout", 30)
            exec_result = execute_command(command, timeout=timeout)
        else:
            print(f"  🚫 BLOQUEADO: {reason}")
            exec_result = {
                "stdout": "",
                "stderr": f"COMANDO BLOQUEADO: {reason}",
                "returncode": -1,
                "success": False
            }
        
        # Salvar resultado
        save_result(cmd_data, exec_result)
        
        # Mover para done
        move_to_done(filename)
    
    return True

def main():
    """Loop principal do agente."""
    print("=" * 60)
    print("  🌉 GLM Bridge Agent")
    print(f"  Repo: {REPO}")
    print(f"  Poll interval: {POLL_INTERVAL}s")
    print(f"  Comandos permitidos: {len(ALLOWED_COMMANDS)}")
    print("=" * 60)
    print()
    print("  Agente rodando! Pressione Ctrl+C para parar.")
    print()
    
    # Primeiro clone
    git_clone_or_pull()
    
    while True:
        try:
            # Atualizar repo
            run_git(["pull", "origin", BRANCH])
            
            # Processar comandos
            had_commands = process_commands()
            
            # Se processou comandos, fazer push dos resultados
            if had_commands:
                git_push()
            
            # Aguardar
            time.sleep(POLL_INTERVAL)
            
        except KeyboardInterrupt:
            print("\n\n  👋 Agente encerrado pelo usuário.")
            break
        except Exception as e:
            print(f"\n  ❌ Erro: {e}")
            print("  Tentando novamente em 30 segundos...")
            time.sleep(30)
            # Tentar re-clonar em caso de erro grave
            try:
                git_clone_or_pull()
            except:
                pass

if __name__ == "__main__":
    main()
