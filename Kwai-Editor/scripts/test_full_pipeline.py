#!/usr/bin/env python3
"""
Teste completo do pipeline Kwai-Editor com AccountPool.

Roda o pipeline completo com 2 vídeos reais:
1. Aquece o pool (login em todas as contas)
2. Valida e baixa os vídeos
3. Prepara via Qwen (capa+titulo + linha)
4. Renderiza o vídeo final
5. Reporta bugs e problemas encontrados
"""

import os
import sys
import time
import traceback
import asyncio
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "bot"))
os.chdir(PROJECT_ROOT)

from bot.log_utils import log

# URLs dos vídeos para teste
VIDEOS = [
    {"url": "https://k.kwai.com/p/lxC9lHFc", "desc": "Clipe - assistir novamente"},
    {"url": "https://k.kwai.com/p/A59eUCNx", "desc": "Dramas TV populares"},
]


def test_pool_warmup():
    """Etapa 1: Aquecer pool de contas."""
    print("\n" + "="*60)
    print("ETAPA 1: Aquecendo pool de contas Qwen")
    print("="*60)

    from Playwright.qwen_account_pool import AccountPool, load_accounts_config

    config = load_accounts_config()
    print(f"Contas carregadas: {len(config)}")

    t0 = time.time()
    pool = AccountPool.initialize(config, headless=True)
    dt = time.time() - t0

    ready = pool.ready_count
    total = pool.total_accounts
    max_jobs = pool.max_concurrent_jobs
    print(f"Pool pronto em {dt:.1f}s: {ready}/{total} contas, {max_jobs} jobs simultaneos")

    if ready < 2:
        print("ERRO: Menos de 2 contas prontas — nao pode paralelizar!")
        pool.shutdown()
        return None

    return pool


def test_download(video_url, chat_id=9999):
    """Etapa 2: Validar URL e baixar vídeo."""
    print(f"\n{'='*60}")
    print(f"ETAPA 2: Download — {video_url}")
    print("="*60)

    from checar_link import validar as validar_link
    from baixar_video import baixar as baixar_video

    # Validar URL
    t0 = time.time()
    try:
        resultado = validar_link(video_url)
        dt = time.time() - t0
        print(f"URL validada em {dt:.1f}s: {resultado.get('title', 'sem titulo')}")
    except Exception as e:
        print(f"FALHA na validacao: {e}")
        return None

    # Download
    t0 = time.time()
    try:
        result = baixar_video(resultado["clean_url"], chat_id)
        dt = time.time() - t0
        saved_path = result["saved_path"]
        size_mb = Path(saved_path).stat().st_size / 1024 / 1024
        print(f"Download OK em {dt:.1f}s: {saved_path} ({size_mb:.1f}MB)")
        return saved_path
    except Exception as e:
        print(f"FALHA no download: {e}")
        traceback.print_exc()
        return None


def test_prepare_with_pool(pool, job_id, video_path, chat_id=9999):
    """Etapa 3: Preparar vídeo via Qwen usando o pool."""
    print(f"\n{'='*60}")
    print(f"ETAPA 3: Prepare (Qwen) — {Path(video_path).name}")
    print("="*60)

    from bot.worker import _prepare_with_pool

    t0 = time.time()
    try:
        prep_data = _prepare_with_pool(pool, job_id, video_path, chat_id)
        dt = time.time() - t0
        print(f"Prepare OK em {dt:.1f}s:")
        print(f"  capa:     \"{prep_data.get('texto_capa', 'N/A')}\"")
        print(f"  titulo:   \"{prep_data.get('texto_titulo', 'N/A')}\"")
        print(f"  corte_y:  {prep_data.get('y1', 'N/A')}-{prep_data.get('y2', 'N/A')}")
        return prep_data
    except Exception as e:
        print(f"FALHA no prepare: {e}")
        traceback.print_exc()
        return None


def test_render(prep_data):
    """Etapa 4: Renderizar vídeo final."""
    print(f"\n{'='*60}")
    print(f"ETAPA 4: Render — {prep_data.get('video_name', 'N/A')}")
    print("="*60)

    from pipeline.simple import renderizar_video

    t0 = time.time()
    try:
        final_path = renderizar_video(prep_data)
        dt = time.time() - t0
        size_mb = Path(final_path).stat().st_size / 1024 / 1024
        print(f"Render OK em {dt:.1f}s: {final_path} ({size_mb:.1f}MB)")
        return final_path
    except Exception as e:
        print(f"FALHA no render: {e}")
        traceback.print_exc()
        return None


def main():
    bugs = []
    results = []

    print("="*60)
    print("TESTE COMPLETO DO PIPELINE KWAI-EDITOR")
    print("="*60)

    # ─── Etapa 1: Pool warmup ─────────────────────────────────────
    pool = test_pool_warmup()
    if pool is None:
        print("\nFALHA CRITICA: Pool nao disponivel. Abortando.")
        return
    results.append(("Pool warmup", "OK", f"{pool.ready_count}/{pool.total_accounts} contas"))

    # ─── Etapa 2+3+4: Pipeline por vídeo ──────────────────────────
    for i, video_info in enumerate(VIDEOS):
        video_url = video_info["url"]
        video_desc = video_info["desc"]
        job_id = f"test_video_{i+1}_{int(time.time())}"

        print(f"\n{'#'*60}")
        print(f"# VIDEO {i+1}/{len(VIDEOS)}: {video_desc}")
        print(f"# URL: {video_url}")
        print(f"# Job ID: {job_id}")
        print(f"{'#'*60}")

        # Download
        saved_path = test_download(video_url)
        if not saved_path:
            results.append((f"Video {i+1} download", "FALHA", video_url))
            bugs.append(f"Video {i+1}: download falhou para {video_url}")
            continue
        results.append((f"Video {i+1} download", "OK", Path(saved_path).name))

        # Prepare (Qwen)
        prep_data = test_prepare_with_pool(pool, job_id, saved_path)
        if not prep_data:
            results.append((f"Video {i+1} prepare", "FALHA", ""))
            bugs.append(f"Video {i+1}: prepare (Qwen) falhou")
            continue
        results.append((f"Video {i+1} prepare", "OK",
                        f"capa=\"{prep_data.get('texto_capa', '')}\""))

        # Render
        final_path = test_render(prep_data)
        if not final_path:
            results.append((f"Video {i+1} render", "FALHA", ""))
            bugs.append(f"Video {i+1}: render falhou")
            continue
        results.append((f"Video {i+1} render", "OK", Path(final_path).name))

    # ─── Shutdown ──────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("Desligando pool...")
    pool.shutdown()
    print("Pool desligado.")

    # ─── Relatório ─────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("RELATORIO FINAL")
    print("="*60)

    for name, status, detail in results:
        icon = "OK" if status == "OK" else "FALHA"
        print(f"  [{icon}] {name}: {detail}")

    if bugs:
        print(f"\nBUGS ENCONTRADOS ({len(bugs)}):")
        for bug in bugs:
            print(f"  - {bug}")
    else:
        print("\nNenhum bug encontrado!")

    # ─── Pool stats ────────────────────────────────────────────────
    print(f"\nPool final stats:")
    print(f"  Total contas: {pool.total_accounts}")
    print(f"  Max jobs simultaneos: {pool.max_concurrent_jobs}")


if __name__ == "__main__":
    main()
