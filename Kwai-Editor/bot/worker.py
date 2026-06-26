import os
import sys
import time
import threading
import traceback
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from db import (
    init_db, get_job, get_next_queued_job, set_job_processing, set_job_ready, set_job_failed,
    count_processing, recover_processing_jobs,
    # Fase 2: novos estados e funções do pipeline paralelo
    set_job_preparing, set_job_ready_to_render, set_job_rendering,
    get_preparation_data, get_next_ready_to_render_job,
    count_ready_to_render, validate_preparation_data, recover_pipeline_jobs,
)
from checar_link import validar as validar_link
from baixar_video import baixar as baixar_video
from log_utils import log
from telegram_utils import send_telegram_message, escape_html

load_dotenv()

BASE_DIR = PROJECT_ROOT

# === Fase 3: pipeline paralelo com 2 esteiras concorrentes ===
# USE_PIPELINE=True: roda 2 threads (esteira A + esteira B) em paralelo
# USE_PIPELINE=False: roda worker antigo sequencial (process_job)
# Configurável via .env (USE_PIPELINE), default True
USE_PIPELINE = os.environ.get("USE_PIPELINE", "True").lower() in ("true", "1", "yes")

# MAX_READY_TO_RENDER: limite de jobs que podem estar prontos para render
# (na fila B) ao mesmo tempo. Quando atinge, esteira A pausa.
# Isso evita que o Qwen produza mais rápido que o render consegue consumir,
# acumulando arquivos temporários em disco.
# Configurável via .env (MAX_READY_TO_RENDER), default 3
try:
    MAX_READY_TO_RENDER = int(os.environ.get("MAX_READY_TO_RENDER", "3"))
except ValueError:
    MAX_READY_TO_RENDER = 3

TIMINGS_PADRAO = {
    "popup_1_in": 0.0,
    "popup_1_out": 7.0,
    "transition_dur": 1.0,
    "popup_fade_in": 1.5,
    "text_fade_dur": 0.5,
}


def get_queued_jobs_round_robin():
    import sqlite3
    from db import DB_PATH, get_conn

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status = 'queued' ORDER BY chat_id, created_at ASC"
        ).fetchall()
    groups = {}
    cid_order = []
    for r in rows:
        d = dict(r)
        cid = d["chat_id"]
        if cid not in groups:
            groups[cid] = []
            cid_order.append(cid)
        groups[cid].append(d)
    result = []
    while any(groups.values()):
        for cid in cid_order:
            if groups[cid]:
                result.append(groups[cid].pop(0))
    return result


def _make_progress_callback(chat_id, url_display):
    THROTTLE_SEC = 10
    last_time = [0.0]
    last_pct = [-1]

    def callback(pct):
        now = time.monotonic()
        if pct == 100 or (pct != last_pct[0] and now - last_time[0] >= THROTTLE_SEC):
            last_time[0] = now
            last_pct[0] = pct
            send_telegram_message(
                chat_id,
                f"\U0001f3ac <b>Editando...</b> ({escape_html(str(pct))}%)\n\n"
                f"Link: <code>{escape_html(url_display)}</code>",
                parse_mode="HTML"
            )
    return callback


def reap_zombies():
    try:
        while True:
            pid, _ = os.waitpid(-1, os.WNOHANG)
            if pid == 0:
                break
    except ChildProcessError:
        pass
    except Exception:
        pass


def process_job(job):
    """LEGADO: processa 1 job sequencialmente (download + Qwen + render + cleanup).
    Usado apenas quando USE_PIPELINE=False (modo fallback).
    Em modo pipeline (default), worker_prepare e worker_render substituem esta função."""
    job_id = job["job_id"]
    chat_id = job["chat_id"]
    raw_input = job["input_url"]
    url_display = raw_input if len(raw_input) <= 60 else raw_input[:57] + "..."

    log.start_timer("job_total")
    log.info(f"[{job_id[:12]}] Iniciando job — chat={chat_id}")
    log.info(f"[{job_id[:12]}] URL: {raw_input}")

    log.info(f"[{job_id[:12]}] [1/5] Validando URL com probe...")
    log.start_timer("probe")
    for attempt in range(3):
        try:
            resultado = validar_link(raw_input)
            log.info(f"[{job_id[:12]}] Probe OK: \"{resultado.get('title', '')}\" {log.timer_info('probe')}")
            break
        except Exception as e:
            if attempt == 2:
                log.error(f"[{job_id[:12]}] URL invalida apos 3 tentativas: {e}")
                traceback.print_exc()
                set_job_failed(job_id, f"url_invalid: {e}")
                send_telegram_message(
                    chat_id,
                    f"\u26a0\ufe0f Link inv\u00e1lido\n\n"
                    f"Link: {url_display}\n"
                    f"Motivo: {str(e)[:200]}\n\n"
                    f"N\u00e3o foi poss\u00edvel validar o link enviado.\n"
                    f"Verifique se \u00e9 um link do Kwai v\u00e1lido e tente novamente."
                )
                return
            log.warn(f"[{job_id[:12]}] Probe tentativa {attempt+1} falhou, retentando... ({e})")
            time.sleep(5)

    send_telegram_message(
        chat_id,
        f"\U0001f504 <b>Processando seu v\u00eddeo...</b>\n\n"
        f"Link: <code>{escape_html(url_display)}</code>\n\n"
        "Link validado. Iniciando download e edi\u00e7\u00e3o.",
        parse_mode="HTML"
    )

    log.info(f"[{job_id[:12]}] [2/5] Baixando video...")
    log.start_timer("download")
    for attempt in range(3):
        try:
            result = baixar_video(resultado["clean_url"], chat_id)
            break
        except Exception as e:
            if attempt == 2:
                log.error(f"[{job_id[:12]}] Download falhou apos 3 tentativas: {e}")
                traceback.print_exc()
                set_job_failed(job_id, f"download_failed: {e}")
                send_telegram_message(
                    chat_id,
                    f"\u26a0\ufe0f Falha no download\n\n"
                    f"Link: {url_display}\n"
                    f"Motivo: {str(e)[:200]}\n\n"
                    f"N\u00e3o foi poss\u00edvel baixar o v\u00eddeo do link enviado.\n"
                    f"Tente novamente mais tarde."
                )
                return
            log.warn(f"[{job_id[:12]}] Download tentativa {attempt+1} falhou, retentando... ({e})")
            time.sleep(5)

    saved_path = result["saved_path"]
    download_size = Path(saved_path).stat().st_size
    log.info(f"[{job_id[:12]}] Video baixado: {saved_path} ({download_size/1024/1024:.1f}MB) {log.timer_info('download')}")

    send_telegram_message(
        chat_id,
        f"\U0001f4e4 <b>Download conclu\u00eddo!</b>\n\n"
        f"Link: <code>{escape_html(url_display)}</code>\n\n"
        "V\u00eddeo baixado. Agora vou analisar e editar.\n"
        "Isso pode levar alguns minutos.",
        parse_mode="HTML"
    )

    set_job_processing(job_id)

    log.info(f"[{job_id[:12]}] [3/5] Editando video...")
    log.start_timer("editing")
    try:
        from pipeline.simple import processar_video as pipeline_processar

        progress_cb = _make_progress_callback(chat_id, url_display)
        final_path = str(Path(pipeline_processar(
            str(saved_path), chat_id=chat_id, timings=TIMINGS_PADRAO,
            on_render_progress=progress_cb,
        )).resolve())
        log.info(f"[{job_id[:12]}] Video editado: {final_path} {log.timer_info('editing')}")

        if not Path(final_path).exists():
            raise RuntimeError(f"arquivo editado nao encontrado em disco: {final_path}")

        final_size = Path(final_path).stat().st_size
        ratio = final_size / download_size if download_size else 0
        log.info(f"[{job_id[:12]}] [4/5] Final: {final_size/1024/1024:.1f}MB (compressao {ratio:.1%})")

        set_job_ready(job_id, final_path)
        log.info(f"[{job_id[:12]}] [5/5] Concluido! {log.timer_info('job_total')}")

    except Exception as e:
        log.error(f"[{job_id[:12]}] Erro na edicao: {e}")
        traceback.print_exc()
        set_job_failed(job_id, f"processing_failed: {e}")
        send_telegram_message(
            chat_id,
            f"\u26a0\ufe0f Erro ao editar v\u00eddeo\n\n"
            f"Link: {url_display}\n"
            f"Motivo: {str(e)[:200]}\n\n"
            f"Ocorreu um erro ao processar seu v\u00eddeo.\n"
            f"Toque em \U0001f4e5 Meus v\u00eddeos para mais detalhes."
        )
        return

    if not send_telegram_message(
        chat_id,
        f"\u2705 <b>Video pronto!</b>\n\n"
        f"Link: <code>{escape_html(url_display)}</code>\n\n"
        f"Seu v\u00eddeo foi editado e est\u00e1 no servidor.\n\n"
        f"Toque em \U0001f4e5 <b>Meus v\u00eddeos</b> para ver o status.",
        parse_mode="HTML"
    ):
        log.warn(f"[{job_id[:12]}] Notificacao de video pronto falhou (video salvo em disco)")

    remaining = count_processing(chat_id)
    if remaining == 0:
        send_telegram_message(
            chat_id,
            "\U0001f389 <b>Fila conclu\u00edda!</b>\n\n"
            "Todos os seus v\u00eddeos foram processados.\n"
            "Toque em \U0001f4e5 <b>Meus v\u00eddeos</b> para ver o status completo.",
            parse_mode="HTML"
        )


def main():
    init_db()
    # Recovery: recupera jobs antigos (processing) E novos (preparing/rendering)
    recover_processing_jobs()
    recover_pipeline_jobs()

    if USE_PIPELINE:
        log.info(f"Worker iniciado em MODO PIPELINE (2 esteiras concorrentes, MAX_READY={MAX_READY_TO_RENDER})")
        run_pipeline_workers()
    else:
        log.info("Worker iniciado em MODO SEQUENCIAL (legado)")
        run_sequential_worker()


def run_sequential_worker():
    """Modo legado: 1 loop pegando jobs queued e processando um por vez."""
    while True:
        try:
            reap_zombies()
            job = get_next_queued_job()
            if job:
                process_job(job)
                reap_zombies()
            else:
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("Worker encerrado.")
            break
        except Exception as exc:
            log.error(f"Erro no worker sequencial: {exc}")
            reap_zombies()
            time.sleep(30)


def run_pipeline_workers():
    """Modo pipeline: 2 threads concorrentes (esteira A + esteira B).

    - Esteira A (worker_prepare): pega jobs queued, faz download + Qwen,
      marca ready_to_render com prep_data
    - Esteira B (worker_render): pega jobs ready_to_render, faz corte +
      render + cleanup, marca ready

    A esteira A respeita MAX_READY_TO_RENDER: se fila B estiver cheia,
    A pausa até B consumir.
    """
    stop_event = threading.Event()

    thread_a = threading.Thread(
        target=worker_prepare,
        args=(stop_event,),
        name="esteira_A",
        daemon=True
    )
    thread_b = threading.Thread(
        target=worker_render,
        args=(stop_event,),
        name="esteira_B",
        daemon=True
    )

    thread_a.start()
    thread_b.start()

    log.info(f"Esteira A (prepare) iniciada — TID={thread_a.native_id}")
    log.info(f"Esteira B (render) iniciada — TID={thread_b.native_id}")

    try:
        # Main thread apenas aguarda Ctrl+C
        while not stop_event.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Encerrando worker (aguardando esteiras terminarem jobs em andamento)...")
        stop_event.set()

    # Aguardar esteiras terminarem (com timeout)
    thread_a.join(timeout=10)
    thread_b.join(timeout=10)
    log.info("Worker encerrado.")


def worker_prepare(stop_event):
    """Esteira A: pega jobs queued, faz download + Qwen, marca ready_to_render.

    Respeita MAX_READY_TO_RENDER: se fila B está cheia, pausa.
    Trata erros: se Qwen falha, marca job como failed e continua.
    """
    from pipeline.simple import preparar_video

    while not stop_event.is_set():
        try:
            # Respeitar MAX_READY_TO_RENDER
            if count_ready_to_render() >= MAX_READY_TO_RENDER:
                time.sleep(0.5)
                continue

            job = get_next_queued_job()
            if not job:
                time.sleep(1)
                continue

            job_id = job["job_id"]
            chat_id = job["chat_id"]
            raw_input = job["input_url"]
            url_display = raw_input if len(raw_input) <= 60 else raw_input[:57] + "..."

            log.info(f"[A {job_id[:12]}] Pegou job — chat={chat_id}")

            # Marcar como preparing
            set_job_preparing(job_id)

            # [1/3] Validar URL
            try:
                resultado = validar_link(raw_input)
            except Exception as e:
                set_job_failed(job_id, f"url_invalid: {e}")
                send_telegram_message(
                    chat_id,
                    f"\u26a0\ufe0f Link inválido\n\n"
                    f"Link: {url_display}\nMotivo: {str(e)[:200]}",
                )
                continue

            # [2/3] Download
            try:
                result = baixar_video(resultado["clean_url"], chat_id)
            except Exception as e:
                set_job_failed(job_id, f"download_failed: {e}")
                send_telegram_message(
                    chat_id,
                    f"\u26a0\ufe0f Falha no download\n\n"
                    f"Link: {url_display}\nMotivo: {str(e)[:200]}",
                )
                continue

            saved_path = result["saved_path"]
            log.info(f"[A {job_id[:12]}] Download OK: {saved_path}")

            # [3/3] Qwen (preparar_video faz capa + título + linha)
            try:
                prep_data = preparar_video(job_id, saved_path, chat_id)
            except Exception as e:
                set_job_failed(job_id, f"prepare_failed: {e}")
                traceback.print_exc()
                send_telegram_message(
                    chat_id,
                    f"\u26a0\ufe0f Erro ao analisar vídeo\n\n"
                    f"Link: {url_display}\nMotivo: {str(e)[:200]}",
                )
                continue

            # Salvar prep_data no DB e marcar ready_to_render
            set_job_ready_to_render(job_id, prep_data)
            log.info(f"[A {job_id[:12]}] Pronto para render — prep_data salvo")

            # NOTA: Nao precisa mais de time.sleep() aqui.
            # Cada job agora usa uma COPIA TEMPORARIA do chrome_profile,
            # eliminando completamente conflitos de LOCK/perfil entre jobs.
            # O close() do QwenReplyAsync garante deterministicamente que o
            # Chrome morreu (via pgrep + SIGKILL) antes de remover a copia.

        except Exception as exc:
            log.error(f"[A] Erro na esteira prepare: {exc}")
            traceback.print_exc()
            time.sleep(5)


def worker_render(stop_event):
    """Esteira B: pega jobs ready_to_render, faz corte + render + cleanup.

    Trata erros: se render falha, marca job como failed e continua.
    """
    from pipeline.simple import renderizar_video

    while not stop_event.is_set():
        try:
            job = get_next_ready_to_render_job()
            if not job:
                time.sleep(1)
                continue

            job_id = job["job_id"]
            chat_id = job["chat_id"]
            log.info(f"[B {job_id[:12]}] Pegou job para render")

            # Marcar como rendering
            set_job_rendering(job_id)

            # Recuperar prep_data (código de barras do chassis)
            prep_data = get_preparation_data(job_id)
            ok, motivo = validate_preparation_data(prep_data)
            if not ok:
                set_job_failed(job_id, f"prep_data_invalid: {motivo}")
                log.error(f"[B {job_id[:12]}] prep_data inválido: {motivo}")
                continue

            # Renderizar (corte + render + cleanup)
            url_display = job["input_url"] if len(job["input_url"]) <= 60 else job["input_url"][:57] + "..."
            progress_cb = _make_progress_callback(chat_id, url_display)

            try:
                final_path = renderizar_video(
                    prep_data,
                    timings=TIMINGS_PADRAO,
                    on_render_progress=progress_cb,
                )
            except Exception as e:
                set_job_failed(job_id, f"render_failed: {e}")
                traceback.print_exc()
                send_telegram_message(
                    chat_id,
                    f"\u26a0\ufe0f Erro ao editar vídeo\n\n"
                    f"Link: {url_display}\nMotivo: {str(e)[:200]}",
                )
                continue

            # Marcar como ready
            set_job_ready(job_id, final_path)
            log.info(f"[B {job_id[:12]}] Render concluído: {final_path}")

            # Notificar usuário
            send_telegram_message(
                chat_id,
                f"\u2705 Vídeo pronto!\n\n"
                f"Link: <code>{escape_html(url_display)}</code>\n\n"
                f"Toque em \U0001f4e5 Meus vídeos para ver o status.",
                parse_mode="HTML"
            )

            # Verificar se fila do chat está concluída
            remaining = count_processing(chat_id)
            if remaining == 0:
                send_telegram_message(
                    chat_id,
                    "\U0001f389 Fila concluída! Todos os seus vídeos foram processados.",
                    parse_mode="HTML"
                )

        except Exception as exc:
            log.error(f"[B] Erro na esteira render: {exc}")
            traceback.print_exc()
            time.sleep(5)


if __name__ == "__main__":
    main()
