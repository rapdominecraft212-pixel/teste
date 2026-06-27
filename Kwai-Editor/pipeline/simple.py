import os
import sys
import shutil
import asyncio
import time as time_module
from pathlib import Path
from Playwright import qwen_linha
from Playwright import qwen_capa_titulo
from Playwright.qwen_reply_async import QwenReplyAsync
from Playwright.qwen_account_pool import AccountPool, QwenAccount
import src.cortar_video as cortar_video
import src.video_popup_linear as video_popup_linear
from bot.log_utils import log


BASE_DIR = Path(__file__).resolve().parent.parent

TIMINGS_PADRAO = {
    "popup_1_in": 0.0,
    "popup_1_out": 7.0,
    "transition_dur": 1.0,
    "popup_fade_in": 1.5,
    "text_fade_dur": 0.5,
}


# === Grid helpers (extraidos de _exec_linha_ask para reuso) ===

def _preparar_grid(video_path):
    """Extrai frame do meio do video e cria imagem grid com linhas numeradas.
    Retorna (grid_path, cell_h, mid_frame_path).
    """
    from src.grid_utils import criar_grid_imagem
    import tempfile
    from PIL import Image
    from moviepy import VideoFileClip

    with VideoFileClip(video_path) as clip:
        mid_frame = clip.duration / 2
        frame_np = clip.get_frame(mid_frame)
        frame = Image.fromarray(frame_np)

    tmp_dir = Path(tempfile.gettempdir())
    uid = os.path.basename(video_path).replace(".", "_")
    mid_frame_path = str(tmp_dir / f"frame_linha_{uid}.jpg")
    grid_path = str(tmp_dir / f"grid_linha_{uid}.jpg")
    frame.save(mid_frame_path, quality=95)
    _, _, cell_h = criar_grid_imagem(mid_frame_path, grid_path)
    return grid_path, cell_h, mid_frame_path


def _limpar_grid_temp(grid_info):
    """Remove arquivos temporarios criados por _preparar_grid."""
    grid_path, cell_h, mid_frame_path = grid_info
    for p in [mid_frame_path, grid_path]:
        if p:
            try:
                os.remove(p)
            except:
                pass


# === Preparar video — versao async com AccountPool (novo) ===

async def _ask_capa_titulo_direct(page, video_path, tag='capa+titulo', timeout=120):
    """Envia prompt de capa+titulo diretamente via pagina do pool (sem QwenReplyAsync).

    DEBUG INSTRUMENTATION (agente2.md, ponto cego #3):
    Adicionados sub-timers para as 4 sub-etapas (upload, envio, espera, extracao)
    para distinguir 'upload lento' de 'inferencia Qwen lenta'.
    Sem isso, o tempo total (~30-130s) era uma caixa preta.
    """
    t0 = time_module.time()
    video_size_mb = os.path.getsize(video_path) / 1024 / 1024
    log.info(f"  [{tag}] Enviando pergunta unificada + video (size={video_size_mb:.1f}MB)...")
    try:
        # Sub-etapa 1/4: Upload do video
        t_upload = time_module.time()
        await QwenReplyAsync._upload_page(page, video_path, tag=tag)
        dt_upload = time_module.time() - t_upload
        log.info(f"  [{tag}] [sub 1/4 upload] dt={dt_upload:.2f}s size={video_size_mb:.1f}MB "
                 f"throughput={video_size_mb/dt_upload if dt_upload > 0 else 0:.2f}MB/s")
        if dt_upload > 30:
            log.warn(f"  [{tag}] [sub 1/4 upload] ANOMALIA upload demorou {dt_upload:.2f}s "
                     f"— rede lenta ou Qwen processando upload")

        # Sub-etapa 2/4: Enviar prompt (ate stop-button aparecer = Qwen comecou a gerar)
        t_envio = time_module.time()
        await QwenReplyAsync._enviar_page(page, qwen_capa_titulo.PROMPT_CAPA_TITULO, timeout, tag=tag)
        dt_envio = time_module.time() - t_envio
        log.info(f"  [{tag}] [sub 2/4 envio] dt={dt_envio:.2f}s (prompt enviado, Qwen comecou a gerar)")
        if dt_envio > 20:
            log.warn(f"  [{tag}] [sub 2/4 envio] ANOMALIA envio+start geracao demorou {dt_envio:.2f}s "
                     f"— possivel rate limit do Qwen")

        # Sub-etapa 3/4: Esperar geracao concluir (ate stop-button sumir)
        t_geracao = time_module.time()
        await QwenReplyAsync._esperar_e_extrair_resposta(page, tag=tag)
        dt_geracao = time_module.time() - t_geracao
        log.info(f"  [{tag}] [sub 3/4 geracao] dt={dt_geracao:.2f}s (Qwen gerando resposta)")
        if dt_geracao > 60:
            log.warn(f"  [{tag}] [sub 3/4 geracao] ANOMALIA inferencia Qwen demorou {dt_geracao:.2f}s "
                     f"— modelo lento ou resposta longa")

        # Sub-etapa 4/4: Extracao do texto do DOM
        t_extr = time_module.time()
        resultado = await QwenReplyAsync._ultima_resposta_page(page)
        dt_extr = time_module.time() - t_extr
        log.info(f"  [{tag}] [sub 4/4 extracao] dt={dt_extr:.2f}s ({len(resultado) if resultado else 0} chars)")

        texto_capa, texto_titulo = qwen_capa_titulo._extrair_capa_titulo(resultado)
        dt = time_module.time() - t0
        log.info(f"  [{tag}] OK em {dt:.2f}s breakdown=upload:{dt_upload:.1f}s,envio:{dt_envio:.1f}s,"
                 f"geracao:{dt_geracao:.1f}s,extr:{dt_extr:.1f}s — "
                 f"Capa=\"{texto_capa}\" Titulo=\"{texto_titulo}\"")
        return texto_capa, texto_titulo
    except Exception as e:
        dt = time_module.time() - t0
        log.error(f"  [{tag}] FALHOU em {dt:.2f}s — {e}")
        raise


async def _perguntar_linha_direct(page, grid_path, cell_h, tag='linha', timeout=120, total_linhas=80):
    """Envia prompt de linha diretamente via pagina do pool (sem QwenReplyAsync).

    DEBUG INSTRUMENTATION (agente2.md, ponto cego #4):
    Mesma instrumentacao de sub-timers do _ask_capa_titulo_direct,
    aplicada as 4 sub-etapas do fluxo de linha.
    """
    t0 = time_module.time()
    grid_size_kb = os.path.getsize(grid_path) / 1024
    log.info(f"  [{tag}] Enviando pergunta + imagem grid (size={grid_size_kb:.0f}KB)...")
    try:
        # Sub-etapa 1/4: Upload do JPG (deveria ser rapido ~0.5-3s)
        t_upload = time_module.time()
        await QwenReplyAsync._upload_page(page, grid_path, tag=tag)
        dt_upload = time_module.time() - t_upload
        log.info(f"  [{tag}] [sub 1/4 upload] dt={dt_upload:.2f}s size={grid_size_kb:.0f}KB")
        if dt_upload > 15:
            log.warn(f"  [{tag}] [sub 1/4 upload] ANOMALIA upload JPG demorou {dt_upload:.2f}s "
                     f"— deveria ser <3s, possivel problema de rede")

        # Sub-etapa 2/4: Enviar prompt
        t_envio = time_module.time()
        await QwenReplyAsync._enviar_page(page, qwen_linha.PROMPT_LINHA, timeout, tag=tag)
        dt_envio = time_module.time() - t_envio
        log.info(f"  [{tag}] [sub 2/4 envio] dt={dt_envio:.2f}s")
        if dt_envio > 20:
            log.warn(f"  [{tag}] [sub 2/4 envio] ANOMALIA envio demorou {dt_envio:.2f}s")

        # Sub-etapa 3/4: Esperar geracao
        t_geracao = time_module.time()
        await QwenReplyAsync._esperar_e_extrair_resposta(page, tag=tag)
        dt_geracao = time_module.time() - t_geracao
        log.info(f"  [{tag}] [sub 3/4 geracao] dt={dt_geracao:.2f}s")
        if dt_geracao > 45:
            log.warn(f"  [{tag}] [sub 3/4 geracao] ANOMALIA inferencia Qwen (linha) demorou "
                     f"{dt_geracao:.2f}s — modelo lento")

        # Sub-etapa 4/4: Extracao
        t_extr = time_module.time()
        texto = await QwenReplyAsync._ultima_resposta_page(page)
        dt_extr = time_module.time() - t_extr
        log.info(f"  [{tag}] [sub 4/4 extracao] dt={dt_extr:.2f}s ({len(texto) if texto else 0} chars)")

        row_start, row_end = qwen_linha._extrair_linhas(texto, total_linhas=total_linhas)
        y_start = int((row_start - 1) * cell_h)
        y_end = int(row_end * cell_h)
        dt = time_module.time() - t0
        log.info(f"  [{tag}] OK em {dt:.2f}s breakdown=upload:{dt_upload:.1f}s,envio:{dt_envio:.1f}s,"
                 f"geracao:{dt_geracao:.1f}s,extr:{dt_extr:.1f}s — "
                 f"Linha_inicial={row_start} Linha_final={row_end} (y={y_start}-{y_end})")
        return y_start, y_end
    except Exception as e:
        dt = time_module.time() - t0
        log.error(f"  [{tag}] FALHOU em {dt:.2f}s — {e}")
        raise


async def preparar_video_async_with_accounts(job_id: str, video_path: str, chat_id: int,
                                               conta_capa: QwenAccount,
                                               conta_linha: QwenAccount) -> dict:
    """Versao async que usa contas do pool (browsers ja abertos e logados).

    Cada chamada (capa+titulo e linha) usa uma conta diferente,
    rodando em PARALELO via asyncio.gather.
    Login ja foi feito no warm_up do pool — zero overhead de login aqui.
    """
    video_name = Path(video_path).name
    video_size = os.path.getsize(video_path)

    # Prepara grid da linha (sync, rapido ~1s)
    grid_info = _preparar_grid(video_path)
    grid_path, cell_h, mid_frame_path = grid_info

    page_capa = None
    page_linha = None

    try:
        # Criar abas nos browsers das contas
        page_capa = await conta_capa.new_page(tag=f'capa+titulo [{conta_capa.id}]')
        page_linha = await conta_linha.new_page(tag=f'linha [{conta_linha.id}]')

        log.info(f"[prep {job_id[:12]}] 2 contas + 2 abas PARALELO — "
                 f"capa+titulo[{conta_capa.id}] + linha[{conta_linha.id}]")

        resultados = await asyncio.gather(
            _ask_capa_titulo_direct(page_capa, video_path, tag=f'capa+titulo [{conta_capa.id}]'),
            _perguntar_linha_direct(page_linha, grid_path, cell_h, tag=f'linha [{conta_linha.id}]'),
            return_exceptions=True,
        )

        resultado_ct = resultados[0]
        resultado_linha = resultados[1]

        if isinstance(resultado_ct, Exception):
            log.error(f"  [capa+titulo] Excecao capturada: {resultado_ct}")
            raise resultado_ct
        if isinstance(resultado_linha, Exception):
            log.error(f"  [linha] Excecao capturada: {resultado_linha}")
            raise resultado_linha

        (texto_capa, texto_titulo) = resultado_ct
        (y1, y2) = resultado_linha

    finally:
        # Fechar abas (NAO fechar o browser/contexto!)
        if page_capa:
            try:
                await conta_capa.close_page(page_capa)
            except:
                pass
        if page_linha:
            try:
                await conta_linha.close_page(page_linha)
            except:
                pass
        _limpar_grid_temp(grid_info)

    log.info(f"[prep {job_id[:12]}] OK — capa=\"{texto_capa}\" corte_y={y1}-{y2}")

    prep_data = {
        "job_id": job_id,
        "video_path": video_path,
        "chat_id": chat_id,
        "texto_capa": texto_capa,
        "texto_titulo": texto_titulo,
        "y1": int(y1),
        "y2": int(y2),
        "video_size": video_size,
        "video_name": video_name,
    }
    return prep_data


# === Renderizar video (nao muda) ===

def renderizar_video(prep_data: dict,
                     timings: dict | None = None,
                     on_render_progress=None) -> str:
    timings = timings or TIMINGS_PADRAO

    job_id = prep_data["job_id"]
    video_path = prep_data["video_path"]
    chat_id = prep_data["chat_id"]
    texto_capa = prep_data["texto_capa"]
    texto_titulo = prep_data["texto_titulo"]
    y1 = prep_data["y1"]
    y2 = prep_data["y2"]
    video_size = prep_data["video_size"]
    video_name = prep_data["video_name"]

    cid = str(chat_id)
    jid = job_id
    cortado_dir = BASE_DIR / "data" / "cortado" / jid
    editado_dir = BASE_DIR / "data" / "editado" / cid
    biblioteca_dir = BASE_DIR / "data" / "biblioteca" / cid

    log.info(f"[render {job_id[:12]}] Cortando...")
    cortado_path = cortar_video.cortar_video(
        video_path, y1, y2,
        output_dir=str(cortado_dir)
    )
    cortado_size = os.path.getsize(cortado_path)
    log.info(f"[render {job_id[:12]}] Corte OK — {Path(cortado_path).name} ({cortado_size/1024/1024:.1f}MB)")

    log.info(f"[render {job_id[:12]}] Renderizando video final com popups...")
    final_path = str(Path(video_popup_linear.criar_video(
        input_path=cortado_path,
        output_dir=str(editado_dir),
        titulo=texto_capa,
        subtitulo=texto_titulo,
        on_render_progress=on_render_progress,
        **timings,
    )).resolve())

    if not os.path.exists(final_path):
        raise RuntimeError(f"arquivo final nao gerado: {final_path}")

    final_size = os.path.getsize(final_path)
    ratio = final_size / video_size if video_size else 0
    log.info(f"[render {job_id[:12]}] Render OK — {Path(final_path).name} ({final_size/1024/1024:.1f}MB) (compressao {ratio:.1%})")

    biblioteca_dir.mkdir(parents=True, exist_ok=True)
    biblioteca_path = biblioteca_dir / video_name
    shutil.move(video_path, str(biblioteca_path))
    log.debug(f"[render {job_id[:12]}] Original movido para: {biblioteca_path}")

    try:
        if cortado_dir.exists():
            shutil.rmtree(cortado_dir)
            log.debug(f"[render {job_id[:12]}] Cortado dir removido: {cortado_dir}")
    except Exception as e:
        log.warn(f"[render {job_id[:12]}] Erro ao remover cortado dir: {e}")

    return final_path


# === Pipeline principal (moderno — usa AccountPool) ===

def processar_video(video_path: str, chat_id: int, timings: dict | None = None,
                    on_render_progress=None,
                    job_id: str = "standalone",
                    pool=None) -> str:
    """Pipeline principal moderno.

    Se `pool` (AccountPool) for passado: usa preparar_video_async_with_accounts.
    Se `pool` for None: cria um pool temporário para este vídeo (overhead de
    login, mas mantém compatibilidade com chamadas standalone).

    O parâmetro antigo `parallel` foi removido — paralelização é responsabilidade
    do AccountPool (que mantém múltiplas contas logadas em paralelo).
    """
    video_name = Path(video_path).name
    video_size = os.path.getsize(video_path)
    log.info(f"Pipeline iniciado — {video_name} ({video_size/1024/1024:.1f}MB) chat={chat_id} job={job_id[:12]}")
    log.start_timer("pipeline_total")

    log.info(f"[1/2] Preparando (Qwen)...")
    log.start_timer("prep")
    if pool is not None:
        # Pool externo (worker.py em modo pipeline) — usa contas persistentes
        conta_capa = pool.acquire(timeout=60)
        try:
            conta_linha = pool.acquire(timeout=60)
        except Exception:
            pool.release(conta_capa)
            raise
        try:
            prep_data = pool.run_async(
                preparar_video_async_with_accounts(
                    job_id, video_path, chat_id,
                    conta_capa=conta_capa,
                    conta_linha=conta_linha,
                )
            )
        finally:
            pool.release(conta_capa)
            pool.release(conta_linha)
    else:
        # Modo standalone: cria pool temporário para este vídeo
        log.warn(f"[prep {job_id[:12]}] pool=None — criando pool temporário "
                 f"(overhead de login ~30-60s esperado)")
        from Playwright.qwen_account_pool import AccountPool, load_accounts_config
        accounts_config = load_accounts_config()
        if len(accounts_config) < 2:
            raise RuntimeError(
                f"AccountPool requer 2+ contas em Playwright/accounts.json. "
                f"Encontrado: {len(accounts_config)}. "
                f"Não há fallback legado — adicione contas ou use worker.py."
            )
        temp_pool = AccountPool.initialize(accounts_config, headless=True)
        try:
            conta_capa = temp_pool.acquire(timeout=60)
            conta_linha = temp_pool.acquire(timeout=60)
            try:
                prep_data = temp_pool.run_async(
                    preparar_video_async_with_accounts(
                        job_id, video_path, chat_id,
                        conta_capa=conta_capa,
                        conta_linha=conta_linha,
                    )
                )
            finally:
                temp_pool.release(conta_capa)
                temp_pool.release(conta_linha)
        finally:
            temp_pool.shutdown()
    log.info(f"[1/2] Preparacao OK {log.timer_info('prep')}")

    log.info(f"[2/2] Renderizando...")
    log.start_timer("render")
    final_path = renderizar_video(prep_data, timings=timings,
                                   on_render_progress=on_render_progress)
    log.info(f"[2/2] Render OK {log.timer_info('render')}")

    log.info(f"Pipeline concluido! {log.timer_info('pipeline_total')}")
    return final_path


def main():
    """Entry point CLI — usa AccountPool (moderno)."""
    upload_base = BASE_DIR / "data" / "upload"
    if not upload_base.exists():
        print("Nenhum video encontrado (data/upload/ nao existe)")
        sys.exit(1)

    user_dirs = sorted(d for d in upload_base.iterdir() if d.is_dir())
    if not user_dirs:
        print("Nenhum video encontrado em data/upload/")
        sys.exit(1)

    videos = []
    for user_dir in user_dirs:
        videos.extend(sorted(user_dir.glob("*.mp4")))

    if not videos:
        print("Nenhum video encontrado em data/upload/")
        sys.exit(1)

    print(f"Videos encontrados: {len(videos)}")

    # Inicializa pool uma única vez (login todas as contas)
    from Playwright.qwen_account_pool import AccountPool, load_accounts_config
    accounts_config = load_accounts_config()
    if len(accounts_config) < 2:
        print(f"ERRO: AccountPool requer 2+ contas em Playwright/accounts.json. Encontrado: {len(accounts_config)}")
        sys.exit(1)
    print(f"Aquecendo {len(accounts_config)} contas Qwen...")
    pool = AccountPool.initialize(accounts_config, headless=True)
    print(f"Pool pronto! {pool.ready_count}/{pool.total_accounts} contas")

    sucessos = 0
    falhas = 0
    for v in videos:
        chat_id = int(v.parent.name)
        try:
            processar_video(str(v), chat_id=chat_id, pool=pool)
            sucessos += 1
        except Exception as e:
            print(f"ERRO ao processar {v.name}: {e}")
            falhas += 1

    pool.shutdown()
    print(f"Resumo: {sucessos} sucesso(s), {falhas} falha(s) em {len(videos)} video(s)")


if __name__ == "__main__":
    main()
