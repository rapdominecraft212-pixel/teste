#!/usr/bin/env python3
"""
=============================================================
 GLM Command Sender — Script para enviar comandos via GitHub
=============================================================

Este script é usado PELO GLM para enviar comandos ao PC do 
usuário através do GitHub.

Uso:
    python send-command.py "ls -la /home"
    python send-command.py "cat /etc/os-release"
    python send-command.py "python3 --version"
=============================================================
"""

import json
import os
import subprocess
import tempfile
import shutil
import sys
import time
from datetime import datetime

# Configuração
TOKEN_FILE = "/home/z/my-project/.github-token"
REPO = "rapdominecraft212-pixel/teste"
BRANCH = "main"
CLONE_DIR = os.path.join(tempfile.gettempdir(), "glm-cmd-repo")

def get_token():
    with open(TOKEN_FILE, 'r') as f:
        return f.read().strip()

def run_git(args, cwd=None):
    env = os.environ.copy()
    env["GIT_ASKPASS"] = "echo"
    env["GIT_TERMINAL_PROMPT"] = "0"
    effective_cwd = cwd if cwd is not None else (CLONE_DIR if os.path.isdir(CLONE_DIR) else None)
    result = subprocess.run(["git"] + args, capture_output=True, text=True, cwd=effective_cwd, env=env)
    return result

def clone_or_pull():
    token = get_token()
    if os.path.isdir(os.path.join(CLONE_DIR, ".git")):
        run_git(["fetch", "origin"])
        run_git(["reset", "--hard", f"origin/{BRANCH}"])
    else:
        if os.path.exists(CLONE_DIR):
            shutil.rmtree(CLONE_DIR)
        os.makedirs(CLONE_DIR, exist_ok=True)
        repo_url = f"https://{token}@github.com/{REPO}.git"
        run_git(["clone", repo_url, CLONE_DIR], cwd=None)
    run_git(["config", "user.name", "GLM-Agent"])
    run_git(["config", "user.email", "glm@agent.local"])

def send_command(command, timeout=30, cmd_id=None):
    """Envia um comando para o PC do usuário via GitHub."""
    if not cmd_id:
        cmd_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    clone_or_pull()
    
    # Criar diretório de comandos pendentes
    pending_dir = os.path.join(CLONE_DIR, "commands", "pending")
    os.makedirs(pending_dir, exist_ok=True)
    
    # Criar arquivo de comando
    cmd_data = {
        "id": cmd_id,
        "command": command,
        "timeout": timeout,
        "sent_at": datetime.now().isoformat(),
        "sent_by": "glm-agent"
    }
    
    cmd_filename = f"cmd_{cmd_id}.json"
    cmd_path = os.path.join(pending_dir, cmd_filename)
    
    with open(cmd_path, 'w') as f:
        json.dump(cmd_data, f, indent=2)
    
    # Commit e push
    run_git(["add", "-A"])
    result = run_git(["commit", "-m", f"Command: {command[:50]}"])
    
    if result.returncode == 0:
        token = get_token()
        repo_url = f"https://{token}@github.com/{REPO}.git"
        push_result = run_git(["push", repo_url, BRANCH])
        
        if push_result.returncode == 0:
            print(f"✅ Comando enviado: {cmd_id}")
            print(f"   Comando: {command}")
            return cmd_id
        else:
            print(f"❌ Erro no push: {push_result.stderr}")
            return None
    else:
        print("ℹ️ Nenhuma mudança para commitar.")
        return cmd_id

def read_results(cmd_id=None, wait=False, max_wait=60):
    """Lê resultados de comandos executados."""
    if wait:
        print(f"⏳ Aguardando resultado...")
        start = time.time()
        while time.time() - start < max_wait:
            clone_or_pull()
            results_dir = os.path.join(CLONE_DIR, "commands", "results")
            
            if os.path.isdir(results_dir):
                for filename in os.listdir(results_dir):
                    if filename.endswith(".json"):
                        filepath = os.path.join(results_dir, filename)
                        with open(filepath, 'r') as f:
                            result = json.load(f)
                        
                        if cmd_id is None or result.get("id") == cmd_id:
                            print(f"\n📋 Resultado [{result.get('id')}]:")
                            print(f"   Comando: {result.get('command')}")
                            print(f"   Hostname: {result.get('hostname')}")
                            print(f"   Executado em: {result.get('executed_at')}")
                            print(f"   Sucesso: {result.get('result', {}).get('success')}")
                            
                            stdout = result.get('result', {}).get('stdout', '')
                            stderr = result.get('result', {}).get('stderr', '')
                            
                            if stdout:
                                print(f"\n   📤 STDOUT:\n{stdout}")
                            if stderr:
                                print(f"\n   📥 STDERR:\n{stderr}")
                            
                            return result
            
            time.sleep(5)
        
        print("⏰ Timeout aguardando resultado.")
        return None
    else:
        clone_or_pull()
        results_dir = os.path.join(CLONE_DIR, "commands", "results")
        
        if not os.path.isdir(results_dir):
            print("Nenhum resultado encontrado.")
            return []
        
        results = []
        for filename in os.listdir(results_dir):
            if filename.endswith(".json"):
                filepath = os.path.join(results_dir, filename)
                with open(filepath, 'r') as f:
                    result = json.load(f)
                results.append(result)
                print(f"📋 [{result.get('id')}] {result.get('command')[:40]}... → {'✅' if result.get('result', {}).get('success') else '❌'}")
        
        return results

def cleanup_results():
    """Limpa resultados antigos do GitHub."""
    clone_or_pull()
    results_dir = os.path.join(CLONE_DIR, "commands", "results")
    done_dir = os.path.join(CLONE_DIR, "commands", "done")
    
    import shutil
    if os.path.exists(results_dir):
        shutil.rmtree(results_dir)
    if os.path.exists(done_dir):
        shutil.rmtree(done_dir)
    
    run_git(["add", "-A"])
    result = run_git(["commit", "-m", "Cleanup old results"])
    if result.returncode == 0:
        token = get_token()
        repo_url = f"https://{token}@github.com/{REPO}.git"
        run_git(["push", repo_url, BRANCH])
        print("🧹 Resultados antigos removidos.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso:")
        print("  python send-command.py 'comando'           # Enviar comando")
        print("  python send-command.py --results           # Ver resultados")
        print("  python send-command.py --wait <cmd_id>     # Aguardar resultado")
        print("  python send-command.py --cleanup           # Limpar resultados")
        sys.exit(1)
    
    if sys.argv[1] == "--results":
        read_results()
    elif sys.argv[1] == "--wait":
        cmd_id = sys.argv[2] if len(sys.argv) > 2 else None
        read_results(cmd_id=cmd_id, wait=True)
    elif sys.argv[1] == "--cleanup":
        cleanup_results()
    else:
        command = " ".join(sys.argv[1:])
        cmd_id = send_command(command)
        if cmd_id:
            print(f"\n💡 Para aguardar o resultado, peça ao GLM para rodar:")
            print(f"   python send-command.py --wait {cmd_id}")
