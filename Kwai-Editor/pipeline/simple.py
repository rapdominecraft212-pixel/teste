import os
import sys
import shutil
import asyncio
import time as time_module
from pathlib import Path
from Playwright import qwen_capa, qwen_titulo, qwen_linha
from Playwright import qwen_capa_titulo
from Playwright.qwen_reply import QwenReply
from Playwright.qwen_reply_async import QwenReplyAsync
from Playwright.qwen_account_pool import AccountPool, QwenAccount
from src import colocar_linha
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


# === Async: wrappers com logging detalhado por aba ===

async def _ask_capa_titulo_log(qr, page, video_path, timeout=120):
    """Envia o prompt unificado de capa+titulo e extrai os dois textos."""
    t0 = time_module.time()
    log.info(f"  [capa+titulo] Enviando pergunta unificada + video...")
    try:
        resultado = await qr.ask_on_page(
            page, qwen_capa_titulo.PROMPT_CAPA_TITULO,
            arquivo=video_path, timeout=timeout, tag='capa+titulo'
        )
        texto_capa, texto_titulo = qwen_capa_titulo._extrair_capa_titulo(resultado)
        dt = time_module.time() - t0
        log.info(f"  [capa+titulo] OK em {dt:.0f}s — Capa=\"{texto_capa}\" Titulo=\"{texto_titulo}\"")
        return texto_capa, texto_titulo
    except Exception as e:
        dt = time_module.time() - t0
        log.error(f"  [capa+titulo] FALHOU em {dt:.0f}s — {e}")
        raise


async def _perguntar_linha_async_log(qr, page, grid_path, cell_h, timeout=120, total_linhas=80):
    """Versao com logging de _perguntar_linha_async."""
    t0 = time_module.time()
    log.info(f"  [linha] Enviando pergunta + imagem grid...")
    try:
        # ask_on_page ja retorna o texto extraido (com retry interno)
        texto = await qr.ask_on_page(page, qwen_linha.PROMPT_LINHA, arquivo=grid_path, timeout=timeout, tag='linha')

        row_start, row_end = qwen_linha._extrair_linhas(texto, total_linhas=total_linhas)
        y_start = int((row_start - 1) * cell_h)
        y_end = int(row_end * cell_h)
        dt = time_module.time() - t0
        log.info(f"  [linha] OK em {dt:.0f}s — Linha_inicial={row_start} Linha_final={row_end} (y={y_start}-{y_end})")
        return y_start, y_end
    except Exception as e:
        dt = time_module.time() - t0
        log.error(f"  [linha] FALHOU em {dt:.0f}s — {e}")
        raise


# === Preparar video — versao async com AccountPool (novo) ===

async def _ask_capa_titulo_direct(page, video_path, tag='capa+titulo', timeout=120):
    """Envia prompt de capa+titulo diretamente via pagina do pool (sem QwenReplyAsync)."""
    t0 = time_module.time()
    log.info(f"  [{tag}] Enviando pergunta unificada + video...")
    try:
        # Upload
        await QwenReplyAsync._upload_page(page, video_path, tag=tag)
        # Enviar prompt
        await QwenReplyAsync._enviar_page(page, qwen_capa_titulo.PROMPT_CAPA_TITULO, timeout, tag=tag)
        # Esperar e extrair resposta
        await QwenReplyAsync._esperar_e_extrair_resposta(page, tag=tag)
        resultado = await QwenReplyAsync._ultima_resposta_page(page)
        texto_capa, texto_titulo = qwen_capa_titulo._extrair_capa_titulo(resultado)
        dt = time_module.time() - t0
        log.info(f"  [{tag}] OK em {dt:.0f}s — Capa=\"{texto_capa}\" Titulo=\"{texto_titulo}\"")
        return texto_capa, texto_titulo
    except Exception as e:
        dt = time_module.time() - t0
        log.error(f"  [{tag}] FALHOU em {dt:.0f}s — {e}")
        raise


async def _perguntar_linha_direct(page, grid_path, cell_h, tag='linha', timeout=120, total_linhas=80):
    """Envia prompt de linha diretamente via pagina do pool (sem QwenReplyAsync)."""
    t0 = time_module.time()
    log.info(f"  [{tag}] Enviando pergunta + imagem grid...")
    try:
        # Upload
        await QwenReplyAsync._upload_page(page, grid_path, tag=tag)
        # Enviar prompt
        await QwenReplyAsync._enviar_page(page, qwen_linha.PROMPT_LINHA, timeout, tag=tag)
        # Esperar e extrair resposta
        await QwenReplyAsync._esperar_e_extrair_resposta(page, tag=tag)
        texto = await QwenReplyAsync._ultima_resposta_page(page)
        row_start, row_end = qwen_linha._extrair_linhas(texto, total_linhas=total_linhas)
        y_start = int((row_start - 1) * cell_h)
        y_end = int(row_end * cell_h)
        dt = time_module.time() - t0
        log.info(f"  [{tag}] OK em {dt:.0f}s — Linha_inicial={row_start} Linha_final={row_end} (y={y_start}-{y_end})")
        return y_start, y_end
    except Exception as e:
        dt = time_module.time() - t0
        log.error(f"  [{tag}] FALHOU em {dt:.0f}s — {e}")
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


# === Preparar video — versao async LEGADA (sem pool) ===

async def preparar_video_async(job_id: str, video_path: str, chat_id: int,
                                parallel: bool = True) -> dict:
    """
    Versao LEGADA: 1 Chrome + 2 abas, chamadas ao Qwen em PARALELO.
    Nao usa o pool — cria e destrói browser a cada chamada.
    Mantida para compatibilidade e modo standalone.
    """
    video_name = Path(video_path).name
    video_size = os.path.getsize(video_path)

    texto_capa = texto_titulo = None
    y1 = y2 = None

    # Prepara grid da linha ANTES do gather (sync, rapido ~1s)
    grid_info = _preparar_grid(video_path)
    grid_path, cell_h, mid_frame_path = grid_info

    qr = QwenReplyAsync(headless=True)
    try:
        await qr.abrir_context()
        page_capa_titulo = await qr.new_page(tag='capa+titulo')
        page_linha = await qr.new_page(tag='linha')

        if parallel:
            log.info(f"[prep {job_id[:12]}] 1 Chrome + 2 abas PARALELO — capa+titulo + linha...")
            # return_exceptions=True: se uma aba falhar, nao derruba a outra
            resultados = await asyncio.gather(
                _ask_capa_titulo_log(qr, page_capa_titulo, video_path, timeout=120),
                _perguntar_linha_async_log(qr, page_linha, grid_path, cell_h, timeout=120, total_linhas=80),
                return_exceptions=True,
            )
            # Processar resultados individualmente
            resultado_ct = resultados[0]  # capa+titulo
            resultado_linha = resultados[1]  # linha

            if isinstance(resultado_ct, Exception):
                log.error(f"  [capa+titulo] Excecao capturada: {resultado_ct}")
                raise resultado_ct
            if isinstance(resultado_linha, Exception):
                log.error(f"  [linha] Excecao capturada: {resultado_linha}")
                raise resultado_linha

            (texto_capa, texto_titulo) = resultado_ct
            (y1, y2) = resultado_linha
        else:
            log.info(f"[prep {job_id[:12]}] 1 Chrome + 2 abas SEQUENCIAL")
            texto_capa, texto_titulo = await _ask_capa_titulo_log(qr, page_capa_titulo, video_path, timeout=120)
            y1, y2 = await _perguntar_linha_async_log(qr, page_linha, grid_path, cell_h, timeout=120, total_linhas=80)
    finally:
        await qr.close()
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


# === Preparar video — wrapper sync (interface compativel) ===

def preparar_video(job_id: str, video_path: str, chat_id: int,
                   parallel: bool = True) -> dict:
    """
    Wrapper sync — mesma interface de sempre.
    Por dentro, roda async com Playwright async_api para paralelizar
    as 3 chamadas ao Qwen.

    Chamadas existentes (worker.py, main) nao precisam mudar nada:
        prep_data = preparar_video(job_id, path, chat_id)
    """
    return asyncio.run(preparar_video_async(job_id, video_path, chat_id, parallel=parallel))


# === Legado: versao sync pura (backup / debug) ===

def _exec_linha_ask(qr, page, video_path):
    """Versao sync legada — mantida para compatibilidade."""
    from Playwright import qwen_linha as _ql
    from Playwright.qwen_reply import QwenReply as _QR
    from src.grid_utils import criar_grid_imagem
    import tempfile
    from PIL import Image
    from moviepy import VideoFileClip
    import os as _os

    mid_frame_path = None
    grid_path = None
    try:
        with VideoFileClip(video_path) as clip:
            mid_frame = clip.duration / 2
            frame_np = clip.get_frame(mid_frame)
            frame = Image.fromarray(frame_np)

        tmp_dir = Path(tempfile.gettempdir())
        uid = _os.path.basename(video_path).replace(".", "_")
        mid_frame_path = str(tmp_dir / f"frame_linha_{uid}.jpg")
        grid_path = str(tmp_dir / f"grid_linha_{uid}.jpg")
        frame.save(mid_frame_path, quality=95)
        _, _, cell_h = criar_grid_imagem(mid_frame_path, grid_path)

        qr.ask_on_page(page, _ql.PROMPT_LINHA, arquivo=grid_path, timeout=180)
        texto = _QR._ultima_resposta_page(page)

        import re
        match = re.search(r"Linha_inicial\s*=\s*(\d+)[\s\S]*?Linha_final\s*=\s*(\d+)", texto.strip(), re.IGNORECASE)
        if not match:
            raise ValueError(f"Nao foi possivel interpretar a resposta do Qwen: {texto[:500]}")
        row_start = int(match.group(1))
        row_end = int(match.group(2))
        y_start = int((row_start - 1) * cell_h)
        y_end = int(row_end * cell_h)
        return y_start, y_end
    finally:
        for p in [mid_frame_path, grid_path]:
            if p:
                try:
                    _os.remove(p)
                except:
                    pass


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


# === Pipeline principal (nao muda) ===

def processar_video(video_path: str, chat_id: int, timings: dict | None = None,
                    on_render_progress=None, parallel=True,
                    job_id: str = "standalone") -> str:
    video_name = Path(video_path).name
    video_size = os.path.getsize(video_path)
    log.info(f"Pipeline iniciado — {video_name} ({video_size/1024/1024:.1f}MB) chat={chat_id} job={job_id[:12]}")
    log.start_timer("pipeline_total")

    log.info(f"[1/2] Preparando (Qwen)...")
    log.start_timer("prep")
    prep_data = preparar_video(job_id, video_path, chat_id, parallel=parallel)
    log.info(f"[1/2] Preparacao OK {log.timer_info('prep')}")

    log.info(f"[2/2] Renderizando...")
    log.start_timer("render")
    final_path = renderizar_video(prep_data, timings=timings,
                                   on_render_progress=on_render_progress)
    log.info(f"[2/2] Render OK {log.timer_info('render')}")

    log.info(f"Pipeline concluido! {log.timer_info('pipeline_total')}")
    return final_path


def main():
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
    sucessos = 0
    falhas = 0
    for v in videos:
        chat_id = int(v.parent.name)
        try:
            processar_video(str(v), chat_id=chat_id)
            sucessos += 1
        except Exception as e:
            print(f"ERRO ao processar {v.name}: {e}")
            falhas += 1

    print(f"Resumo: {sucessos} sucesso(s), {falhas} falha(s) em {len(videos)} video(s)")


if __name__ == "__main__":
    main()
