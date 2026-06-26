#!/usr/bin/env python3
"""Pipeline end-to-end test — uses latest downloaded videos."""
import time, sys, os, glob, traceback
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "bot"))
os.chdir(PROJECT_ROOT)

def main():
    # Find 2 most recent videos
    upload_dir = PROJECT_ROOT / "data" / "upload" / "9999"
    videos = sorted(glob.glob(str(upload_dir / "*.mp4")), key=os.path.getmtime, reverse=True)[:2]
    if len(videos) < 2:
        print("Need 2 videos, downloading...")
        from checar_link import validar as validar_link
        from baixar_video import baixar as baixar_video
        urls = ['https://k.kwai.com/p/lxC9lHFc', 'https://k.kwai.com/p/A59eUCNx']
        for url in urls:
            r = validar_link(url)
            res = baixar_video(r['clean_url'], 9999)
            videos.append(res['saved_path'])

    print(f"\nVideos: {[Path(v).name for v in videos]}")

    # Warm pool
    print("\n[1] Warming pool...")
    from Playwright.qwen_account_pool import AccountPool
    config = [
        {"email": "fodef30851@luxudata.com", "password": "Qwen_senha1"},
        {"email": "bafepad314@luxudata.com", "password": "Qwen_senha1"},
    ]
    pool = AccountPool.initialize(config, headless=True)
    print(f"  {pool.ready_count}/2 ready")
    if pool.ready_count < 2:
        pool.shutdown(); return False

    results = []
    for i, video in enumerate(videos):
        print(f"\n{'='*50}\nVIDEO {i+1}: {Path(video).name}\n{'='*50}")

        # Prepare via pool
        job_id = f"v{i+1}_{int(time.time())}"
        from bot.worker import _prepare_with_pool
        t0 = time.time()
        try:
            prep = _prepare_with_pool(pool, job_id, video, 9999)
            dt = time.time() - t0
            print(f"  Prepare OK {dt:.0f}s: capa=\"{prep['texto_capa']}\" y={prep['y1']}-{prep['y2']}")
        except Exception as e:
            print(f"  Prepare FAIL: {e}")
            results.append(False); continue

        # Render
        from pipeline.simple import renderizar_video
        t0 = time.time()
        try:
            final = renderizar_video(prep)
            dt = time.time() - t0
            sz = os.path.getsize(final)/1024/1024
            print(f"  Render OK {dt:.0f}s: {Path(final).name} ({sz:.1f}MB)")
            results.append(True)
        except Exception as e:
            print(f"  Render FAIL: {e}")
            results.append(False)

    pool.shutdown()
    ok = sum(results)
    print(f"\n{'='*50}\nRESULT: {ok}/{len(results)} videos OK\n{'='*50}")
    return all(results)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
