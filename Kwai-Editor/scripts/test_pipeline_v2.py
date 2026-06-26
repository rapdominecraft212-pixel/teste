#!/usr/bin/env python3
"""Teste completo do pipeline — versao sequencial com logging detalhado."""

import os, sys, time, traceback, asyncio
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "bot"))
os.chdir(PROJECT_ROOT)

from bot.log_utils import log

VIDEOS = [
    "https://k.kwai.com/p/lxC9lHFc",
    "https://k.kwai.com/p/A59eUCNx",
]


def main():
    # 1. Warmup pool
    print("\n[1] Aquecendo pool...")
    from Playwright.qwen_account_pool import AccountPool, load_accounts_config
    config = load_accounts_config()
    pool = AccountPool.initialize(config, headless=True)
    print(f"  {pool.ready_count}/{pool.total_accounts} contas prontas")
    if pool.ready_count < 2:
        print("  ERRO: precisa de 2+ contas")
        pool.shutdown()
        return

    # 2. For each video: download + prepare + render
    for i, url in enumerate(VIDEOS):
        print(f"\n{'='*50}")
        print(f"VIDEO {i+1}: {url}")
        print(f"{'='*50}")

        # Download
        print(f"  [2] Download...")
        from checar_link import validar as validar_link
        from baixar_video import baixar as baixar_video
        try:
            resultado = validar_link(url)
            result = baixar_video(resultado["clean_url"], 9999)
            saved_path = result["saved_path"]
            size_mb = Path(saved_path).stat().st_size / 1024 / 1024
            print(f"  Download OK: {Path(saved_path).name} ({size_mb:.1f}MB)")
        except Exception as e:
            print(f"  Download FALHOU: {e}")
            continue

        # Prepare via pool
        print(f"  [3] Prepare (Qwen via pool)...")
        job_id = f"test_{i+1}_{int(time.time())}"
        from bot.worker import _prepare_with_pool
        try:
            t0 = time.time()
            prep_data = _prepare_with_pool(pool, job_id, saved_path, 9999)
            dt = time.time() - t0
            print(f"  Prepare OK em {dt:.0f}s")
            print(f"    capa:   \"{prep_data.get('texto_capa', '')}\"")
            print(f"    titulo: \"{prep_data.get('texto_titulo', '')}\"")
            print(f"    y:      {prep_data.get('y1', '')}-{prep_data.get('y2', '')}")
        except Exception as e:
            print(f"  Prepare FALHOU: {e}")
            traceback.print_exc()
            continue

        # Render
        print(f"  [4] Render...")
        from pipeline.simple import renderizar_video
        try:
            t0 = time.time()
            final_path = renderizar_video(prep_data)
            dt = time.time() - t0
            size_mb = Path(final_path).stat().st_size / 1024 / 1024
            print(f"  Render OK em {dt:.0f}s: {Path(final_path).name} ({size_mb:.1f}MB)")
        except Exception as e:
            print(f"  Render FALHOU: {e}")
            traceback.print_exc()
            continue

        print(f"  VIDEO {i+1} COMPLETO!")

    # Shutdown
    print(f"\n[5] Desligando pool...")
    pool.shutdown()
    print("DONE")


if __name__ == "__main__":
    main()
