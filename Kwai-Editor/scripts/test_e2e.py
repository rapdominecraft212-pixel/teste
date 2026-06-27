#!/usr/bin/env python3
"""Pipeline end-to-end test — runs as standalone script."""

import time, sys, os, traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "bot"))
os.chdir(PROJECT_ROOT)

from bot.log_utils import log

VIDEOS = [
    "/home/z/my-project/Kwai-Editor/data/upload/9999/kwai_1782485690.mp4",
    "/home/z/my-project/Kwai-Editor/data/upload/9999/kwai_1782485702.mp4",
]

def main():
    # Step 1: Warm pool
    print("\n[1] Warming pool with 2 accounts...")
    from Playwright.qwen_account_pool import AccountPool
    config = [
        {"email": "fodef30851@luxudata.com", "password": "Qwen_senha1"},
        {"email": "bafepad314@luxudata.com", "password": "Qwen_senha1"},
    ]
    pool = AccountPool.initialize(config, headless=True)
    print(f"  {pool.ready_count}/2 ready")

    if pool.ready_count < 2:
        print("  FAIL: need 2 accounts")
        pool.shutdown()
        return False

    results = []
    for i, video in enumerate(VIDEOS):
        if not os.path.exists(video):
            print(f"\n  Video {i+1} not found: {video}")
            continue

        print(f"\n{'='*50}")
        print(f"VIDEO {i+1}: {Path(video).name}")
        print(f"{'='*50}")

        # Step 2: Prepare via pool
        print(f"  [2] Prepare (Qwen via pool)...")
        job_id = f"test_v{i+1}_{int(time.time())}"
        from bot.worker import _prepare_with_pool
        t0 = time.time()
        try:
            prep_data = _prepare_with_pool(pool, job_id, video, 9999)
            dt = time.time() - t0
            print(f"  Prepare OK {dt:.0f}s")
            print(f'    capa="{prep_data["texto_capa"]}"')
            print(f'    titulo="{prep_data["texto_titulo"]}"')
            print(f'    y={prep_data["y1"]}-{prep_data["y2"]}')
        except Exception as e:
            print(f"  Prepare FAIL: {e}")
            traceback.print_exc()
            results.append(False)
            continue

        # Step 3: Render
        print(f"  [3] Render...")
        from pipeline.simple import renderizar_video
        t0 = time.time()
        try:
            final_path = renderizar_video(prep_data)
            dt = time.time() - t0
            sz = os.path.getsize(final_path) / 1024 / 1024
            print(f"  Render OK {dt:.0f}s: {Path(final_path).name} ({sz:.1f}MB)")
            results.append(True)
        except Exception as e:
            print(f"  Render FAIL: {e}")
            traceback.print_exc()
            results.append(False)

    # Shutdown
    print(f"\n[4] Shutting down pool...")
    pool.shutdown()

    # Report
    print(f"\n{'='*50}")
    print(f"RESULTS: {sum(results)}/{len(results)} videos completed")
    for i, ok in enumerate(results):
        print(f"  Video {i+1}: {'OK' if ok else 'FAIL'}")
    print(f"{'='*50}")

    return all(results) if results else False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
