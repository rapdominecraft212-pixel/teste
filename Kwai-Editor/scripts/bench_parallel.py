#!/usr/bin/env python3
"""Benchmark PARALELO — mede cada etapa como o pipeline real roda."""
import time, sys, os, asyncio
sys.path.insert(0, '/home/z/my-project/Kwai-Editor')
sys.path.insert(0, '/home/z/my-project/Kwai-Editor/bot')
os.chdir('/home/z/my-project/Kwai-Editor')

T = {}

def t(name):
    class TT:
        def __enter__(self):
            self.t0 = time.time(); return self
        def __exit__(self, *a):
            dt = time.time() - self.t0; T[name] = dt
            print(f"  ⏱ {name}: {dt:.1f}s", flush=True)
    return TT()

import glob
video = sorted(glob.glob('/home/z/my-project/Kwai-Editor/data/upload/9999/*.mp4'), key=os.path.getmtime)[-1]
print(f"Video: {os.path.basename(video)} ({os.path.getsize(video)/1024/1024:.1f}MB)", flush=True)

# Grid
print("\n[1] Grid prep...", flush=True)
with t("grid_prep"):
    from pipeline.simple import _preparar_grid
    grid_info = _preparar_grid(video)
    grid_path, cell_h, _ = grid_info

# Pool warmup
print("\n[2] Pool warmup...", flush=True)
with t("pool_warmup"):
    from Playwright.qwen_account_pool import AccountPool
    config = [{"email": "fodef30851@luxudata.com", "password": "Qwen_senha1"},
              {"email": "bafepad314@luxudata.com", "password": "Qwen_senha1"}]
    pool = AccountPool.initialize(config, headless=True)

# PARALLEL Qwen (like the real pipeline)
print("\n[3] Qwen PARALELO (2 contas)...", flush=True)
from Playwright.qwen_reply_async import QwenReplyAsync
from Playwright import qwen_capa_titulo, qwen_linha

with t("qwen_parallel"):
    conta_capa = pool.acquire(timeout=60)
    conta_linha = pool.acquire(timeout=60)

    async def _capa():
        t0 = time.time()
        page = await conta_capa.new_page(tag='capa+titulo')
        await QwenReplyAsync._upload_page(page, video, tag='capa+titulo')
        t_up = time.time() - t0
        print(f"    capa upload: {t_up:.1f}s", flush=True)
        t0i = time.time()
        await QwenReplyAsync._enviar_page(page, qwen_capa_titulo.PROMPT_CAPA_TITULO, 300, tag='capa+titulo')
        t_inf = time.time() - t0i
        print(f"    capa inference: {t_inf:.1f}s", flush=True)
        await QwenReplyAsync._esperar_e_extrair_resposta(page, tag='capa+titulo')
        resultado = await QwenReplyAsync._ultima_resposta_page(page)
        await conta_capa.close_page(page)
        texto_capa, texto_titulo = qwen_capa_titulo._extrair_capa_titulo(resultado)
        return texto_capa, texto_titulo

    async def _linha():
        t0 = time.time()
        page = await conta_linha.new_page(tag='linha')
        await QwenReplyAsync._upload_page(page, grid_path, tag='linha')
        t_up = time.time() - t0
        print(f"    linha upload: {t_up:.1f}s", flush=True)
        t0i = time.time()
        await QwenReplyAsync._enviar_page(page, qwen_linha.PROMPT_LINHA, 300, tag='linha')
        t_inf = time.time() - t0i
        print(f"    linha inference: {t_inf:.1f}s", flush=True)
        await QwenReplyAsync._esperar_e_extrair_resposta(page, tag='linha')
        texto = await QwenReplyAsync._ultima_resposta_page(page)
        row_start, row_end = qwen_linha._extrair_linhas(texto, total_linhas=80)
        y1 = int((row_start - 1) * cell_h)
        y2 = int(row_end * cell_h)
        await conta_linha.close_page(page)
        return y1, y2

    # Run in parallel on pool's event loop
    async def _both():
        return await asyncio.gather(_capa(), _linha(), return_exceptions=True)
    results = pool.run_async(_both())
    pool.release(conta_capa)
    pool.release(conta_linha)

    if isinstance(results[0], Exception): raise results[0]
    if isinstance(results[1], Exception): raise results[1]
    (texto_capa, texto_titulo) = results[0]
    (y1, y2) = results[1]

print(f'  Capa="{texto_capa}" y={y1}-{y2}', flush=True)

# Video cut
print("\n[4] Video cut...", flush=True)
with t("video_cut"):
    import src.cortar_video as cortar_video
    cortado_dir = f'/home/z/my-project/Kwai-Editor/data/cortado/bench_{int(time.time())}'
    cortado_path = cortar_video.cortar_video(video, y1, y2, output_dir=cortado_dir)

# BG pre-render
print("\n[5] BG pre-render...", flush=True)
with t("bg_prerender"):
    import src.video_popup_linear as vpl
    import hashlib, tempfile
    input_hash = hashlib.md5(f"{cortado_path}_180.00".encode()).hexdigest()[:12]
    bg_cache = f'{tempfile.gettempdir()}/vpl_bg_bench_{input_hash}.mp4'
    vpl._prerender_background(cortado_path, 180.0, bg_cache)

# Popup pre-render
print("\n[6] Popup pre-render...", flush=True)
with t("popup_prerender"):
    popup_hash = hashlib.md5(f"{texto_capa}|{texto_titulo}|180.00".encode()).hexdigest()[:12]
    popup_cache = f'{tempfile.gettempdir()}/vpl_popup_bench_{popup_hash}.mov'
    vpl._prerender_popup(texto_capa, texto_titulo, 180.0, 0, 7, 1, 1.5, 0.5, popup_cache)

# FFmpeg composite
print("\n[7] FFmpeg composite...", flush=True)
with t("ffmpeg_composite"):
    output_dir = '/home/z/my-project/Kwai-Editor/data/editado/9999'
    os.makedirs(output_dir, exist_ok=True)
    output_path = f'{output_dir}/bench_{int(time.time())}.mp4'
    vpl._composite_with_ffmpeg(cortado_path, bg_cache, popup_cache, output_path, 180.0)

# Cleanup
pool.shutdown()
from pipeline.simple import _limpar_grid_temp
_limpar_grid_temp(grid_info)
for p in [popup_cache, bg_cache]:
    if os.path.exists(p): os.unlink(p)
try: __import__('shutil').rmtree(cortado_dir, ignore_errors=True)
except: pass

# Report
total_no_warmup = sum(v for k, v in T.items() if k != 'pool_warmup')
print(f"\n{'='*50}")
print("BENCHMARK PARALELO — RESULTADOS")
print(f"{'='*50}")
for name, dt in sorted(T.items(), key=lambda x: -x[1]):
    pct = dt / total_no_warmup * 100
    print(f"  {name:25s} {dt:6.1f}s  ({pct:5.1f}%)")
print(f"  {'TOTAL (no warmup)':25s} {total_no_warmup:6.1f}s")
print(f"  {'+ pool warmup (1x)':25s} {T.get('pool_warmup',0):6.1f}s")
print(f"  {'GRAND TOTAL':25s} {sum(T.values()):6.1f}s")
