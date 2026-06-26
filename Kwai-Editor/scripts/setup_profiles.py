"""
One-time setup: copy chrome_profile to 3 variants for parallel Qwen access.
Run once after Playwright/login_setup.py (after logging into Qwen).
"""
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SOURCE = BASE_DIR / "Playwright" / "chrome_profile"
COUNT = 3

if not SOURCE.exists():
    print(f"ERRO: Perfil padrao nao encontrado em: {SOURCE}")
    print("Execute Playwright/login_setup.py primeiro para criar o perfil.")
    exit(1)

for i in range(1, COUNT + 1):
    dest = SOURCE.parent / f"chrome_profile_{i}"
    if dest.exists():
        print(f"[{i}/{COUNT}] {dest.name} ja existe — ignorando")
        continue
    print(f"[{i}/{COUNT}] Copiando {SOURCE.name} -> {dest.name} ...")
    shutil.copytree(str(SOURCE), str(dest))
    # Limpar lockfiles do Chrome para evitar exit code 21 em perfis clonados
    for f in dest.rglob("LOCK"):
        try:
            f.unlink()
        except:
            pass
    print(f"  OK ({sum(f.stat().st_size for f in dest.rglob('*')) / 1024 / 1024:.0f}MB)")

print()
print("Pronto! Agora o pipeline paralelo pode usar os 3 perfis separadamente.")
