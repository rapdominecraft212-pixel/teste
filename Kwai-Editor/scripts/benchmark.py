#!/usr/bin/env python3
"""Benchmark: mede CADA etapa do pipeline com timers precisos."""
import time, sys, os
sys.path.insert(0, '/home/z/my-project/Kwai-Editor')
sys.path.insert(0, '/home/z/my-project/Kwai-Editor/bot')
os.chdir('/home/z/my-project/Kwai-Editor')

TIMINGS = {}

def timer(name):
    class T:
        def __enter__(self):
            self.t0 = time.time()
            return self
        def __exit__(self, *a):
            dt = time.time() - self.t0
            TIMINGS[name] = dt
            print(f"  ⏱ {name}: {dt:.1f}s")
    return T()

# Find latest video
import glob
videos = sorted(glob.glob('/home/z/my-project/Kwai-Editor/data/upload/9999/*.mp4'), key=os.path.getmtime, reverse=True)
video = videos[0]
print(f"Video: {os.path.basename(video)} ({os.path.getsize(video)/1024/1024:.1f}MB)")

# === STEP 1: Grid preparation ===
print("\n[1] Grid prep...")
with timer("grid_prep"):
    from pipeline.simple import _preparar_grid
    grid_info = _preparar_grid(video)
    grid_path, cell_h, _ = grid_info

# === STEP 2: Pool warmup (one-time cost, amortized) ===
print("\n[2] Pool warmup...")
with timer("pool_warmup"):
    from Playwright.qwen_account_pool import AccountPool
    config = [
        {"email": "fodef30851@luxudata.com", "password": "Qwen_senha1"},
        {"email": "bafepad314@luxudata.com", "password": "Qwen_senha1"},
    ]
    pool = AccountPool.initialize(config, headless=True)

# === STEP 3: Qwen - capa+titulo ===
print("\n[3] Qwen capa+titulo...")
conta_capa = pool.acquire(timeout=60)
with timer("qwen_capa_titulo"):
    from Playwright.qwen_reply_async import QwenReplyAsync
    from Playwright import qwen_capa_titulo
    page_capa = pool.run_async(conta_capa.new_page(tag='capa+titulo'))
    pool.run_async(QwenReplyAsync._upload_page(page_capa, video, tag='capa+titulo'))
    pool.run_async(QwenReplyAsync._enviar_page(page_capa, qwen_capa_titulo.PROMPT_CAPA_TITULO, 300, tag='capa+titulo'))
    pool.run_async(QwenReplyAsync._esperar_e_extrair_resposta(page_capa, tag='capa+titulo'))
    resultado_capa = pool.run_async(QwenReplyAsync._ultima_resposta_page(page_capa))
    texto_capa, texto_titulo = qwen_capa_titulo._extrair_capa_titulo(resultado_capa)
    pool.run_async(conta_capa.close_page(page_capa))
pool.release(conta_capa)

# === STEP 4: Qwen - linha ===
print("\n[4] Qwen linha...")
conta_linha = pool.acquire(timeout=60)
with timer("qwen_linha"):
    from Playwright import qwen_linha
    page_linha = pool.run_async(conta_linha.new_page(tag='linha'))
    pool.run_async(QwenReplyAsync._upload_page(page_linha, grid_path, tag='linha'))
    pool.run_async(QwenReplyAsync._enviar_page(page_linha, qwen_linha.PROMPT_LINHA, 300, tag='linha'))
    pool.run_async(QwenReplyAsync._esperar_e_extrair_resposta(page_linha, tag='linha'))
    resultado_linha = pool.run_async(QwenReplyAsync._ultima_resposta_page(page_linha))
    row_start, row_end = qwen_linha._extrair_linhas(resultado_linha, total_linhas=80)
    y1 = int((row_start - 1) * cell_h)
    y2 = int(row_end * cell_h)
    pool.run_async(conta_linha.close_page(page_linha))
pool.release(conta_linha)

print(f'\n  Capa="{texto_capa}" Titulo="{texto_titulo}" y={y1}-{y2}')

# === STEP 5: Video cut ===
print("\n[5] Video cut...")
with timer("video_cut"):
    import src.cortar_video as cortar_video
    cortado_dir = f'/home/z/my-project/Kwai-Editor/data/cortado/bench_{int(time.time())}'
    cortado_path = cortar_video.cortar_video(video, y1, y2, output_dir=cortado_dir)

# === STEP 6: Popup pre-render ===
print("\n[6] Popup pre-render...")
with timer("popup_prerender"):
    import src.video_popup_linear as vpl
    # Just measure the pre-render step
    popup_hash = __import__('hashlib').md5(f"bench|sub|180.00".encode()).hexdigest()[:12]
    popup_cache = f'/tmp/vpl_popup_bench_{popup_hash}.mov'
    vpl._prerender_popup("bench", "sub", 180.0, 0, 7, 1, 1.5, 0.5, popup_cache)

# === STEP 7: Background pre-render ===
print("\n[7] BG pre-render...")
with timer("bg_prerender"):
    bg_hash = __import__('hashlib').md5(f"{cortado_path}_180.00".encode()).hexdigest()[:12]
    bg_cache = f'/tmp/vpl_bg_bench_{bg_hash}.mp4'
    vpl._prerender_background(cortado_path, 180.0, bg_cache)

# === STEP 8: FFmpeg composite ===
print("\n[8] FFmpeg composite...")
with timer("ffmpeg_composite"):
    import tempfile
    output_dir = '/home/z/my-project/Kwai-Editor/data/editado/9999'
    os.makedirs(output_dir, exist_ok=True)
    output_path = f'{output_dir}/bench_{int(time.time())}.mp4'
    vpl._composite_with_ffmpeg(
        video_path=cortado_path, bg_path=bg_cache, popup_path=popup_cache,
        output_path=output_path, duration=180.0
    )

# === CLEANUP ===
pool.shutdown()
from pipeline.simple import _limpar_grid_temp
_limpar_grid_temp(grid_info)
for p in [popup_cache, bg_cache]:
    if os.path.exists(p): os.unlink(p)
try: __import__('shutil').rmtree(cortado_dir, ignore_errors=True)
except: pass

# === REPORT ===
print(f"\n{'='*50}")
print("BENCHMARK RESULTS")
print(f"{'='*50}")
total_no_warmup = sum(v for k, v in TIMINGS.items() if k != 'pool_warmup')
for name, dt in sorted(TIMINGS.items(), key=lambda x: -x[1]):
    pct = dt / total_no_warmup * 100 if total_no_warmup else 0
    print(f"  {name:25s} {dt:6.1f}s  ({pct:5.1f}%)")
print(f"  {'TOTAL (no warmup)':25s} {total_no_warmup:6.1f}s")
print(f"  {'+ pool warmup':25s} {TIMINGS.get('pool_warmup',0):6.1f}s")
print(f"  {'GRAND TOTAL':25s} {sum(TIMINGS.values()):6.1f}s")

# Identify bottleneck
worst = max(TIMINGS, key=TIMINGS.get)
print(f"\n  🔥 BOTTLENECK: {worst} ({TIMINGS[worst]:.1f}s)")
