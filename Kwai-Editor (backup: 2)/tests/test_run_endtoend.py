"""
Testa o editar.py em background, capturando tudo que aparece no terminal
exatamente como o usuario veria.

Uso: python test_run_endtoend.py
"""
import subprocess
import sys
import os
import time
import threading

SCRIPT = os.path.join(os.path.dirname(__file__), os.pardir, "pipeline", "editor.py")
TIMEOUT = 300  # 5 min max


def reader(stream, label, output):
    for line in iter(stream.readline, ""):
        output.append(f"[{label}] {line}")
        print(f"[{label}] {line}", end="")


proc = subprocess.Popen(
    [sys.executable, SCRIPT],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    cwd=os.path.join(os.path.dirname(__file__), os.pardir),
)

stdout_lines = []
stderr_lines = []

t_out = threading.Thread(target=reader, args=(proc.stdout, "OUT", stdout_lines), daemon=True)
t_err = threading.Thread(target=reader, args=(proc.stderr, "ERR", stderr_lines), daemon=True)
t_out.start()
t_err.start()

# Aguarda splash carregar
time.sleep(2)

# Envia ENTER para iniciar processamento
proc.stdin.write("\n")
proc.stdin.flush()

try:
    proc.wait(timeout=TIMEOUT)
except subprocess.TimeoutExpired:
    proc.kill()
    print(f"\n[FATAL] Processo excedeu {TIMEOUT}s e foi morto")
    sys.exit(1)

print(f"\n=== EXIT CODE: {proc.returncode} ===")
print(f"=== STDOUT: {len(stdout_lines)} lines, STDERR: {len(stderr_lines)} lines ===")
sys.exit(proc.returncode)
