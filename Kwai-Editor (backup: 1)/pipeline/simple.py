import os
import sys
import shutil
import time as time_module
from pathlib import Path
from Playwright import qwen_capa, qwen_titulo
from Playwright.qwen_reply import QwenReply
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


def preparar_video(job_id: str, video_path: str, chat_id: int,
                   parallel: bool = True) -> dict:
    """
    Cria 1 browser + 3 abas. Roda capa/titulo/linha SEQUENCIALMENTE
    (Playwright sync exige mesma thread), usando 1 aba por tarefa.
    Perfil original chrome_profile — sem clones, Qwen aceita upload.
    """
    video_name = Path(video_path).name
    video_size = os.path.getsize(video_path)

    texto_capa = texto_titulo = None
    y1 = y2 = None

    qr = QwenReply(headless=True)
    try:
        qr.abrir_context()
        page_capa = qr.new_page()
        page_titulo = qr.new_page()
        page_linha = qr.new_page()

        log.info(f"[prep {job_id[:12]}] 1 Chrome + 3 abas (sequencial, Playwright sync)")
        texto_capa = qr.ask_on_page(page_capa, qwen_capa.PROMPT_CAPA, arquivo=video_path, timeout=300)
        texto_titulo = qr.ask_on_page(page_titulo, qwen_titulo.PROMPT_TITULO, arquivo=video_path, timeout=300)
        y1, y2 = _exec_linha_ask(qr, page_linha, video_path)
    finally:
        qr.close()

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


def _exec_linha_ask(qr, page, video_path):
    from Playwright import qwen_linha
    from Playwright.qwen_reply import QwenReply
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

        qr.ask_on_page(page, qwen_linha.PROMPT_LINHA, arquivo=grid_path, timeout=180)
        texto = QwenReply._ultima_resposta_page(page)

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
