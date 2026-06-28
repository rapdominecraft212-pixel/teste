import os
import sys
import time
import psutil
import threading
import traceback
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from db import (
    init_db, get_job, get_next_queued_job, set_job_processing, set_job_ready, set_job_failed,
    count_processing, count_active, recover_processing_jobs,
    # Fase 2: novos estados e funções do pipeline paralelo
    set_job_preparing, set_job_ready_to_render, set_job_rendering,
    get_preparation_data, get_next_ready_to_render_job, acquire_ready_to_render_job,
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

# MAX_PARALLEL_JOBS: número máximo de jobs simultâneos.
# Cada job usa 2 contas do pool (capa+titulo + linha) + 1 thread de render.
# O cálculo automático é: total_contas // 2, MAS pode ser limitado aqui.
# Para i3-2120 (2 cores/4 threads, 8GB RAM): RECOMENDADO = 2
# Configurável via .env (MAX_PARALLEL_JOBS), default 0 = auto (contas//2)
try:
    MAX_PARALLEL_JOBS = int(os.environ.get("MAX_PARALLEL_JOBS", "2"))
except ValueError:
    MAX_PARALLEL_JOBS = 2

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
    THROTTLE_SEC = 30  # Aumentado de 10s para 30s — menos spam
    last_time = [0.0]
    last_pct = [-1]

    def callback(pct):
        now = time.monotonic()
        # Só enviar progresso a cada 30s E quando completar 100%
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

    remaining = count_active(chat_id)
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

    # === KEEP AWAKE: impedir computador de dormir enquanto worker roda ===
    # Se rodando via launcher, o launcher ja ativou KeepAwake.
    # Se rodando standalone, ativamos aqui.
    keep_awake = None
    try:
        sys.path.insert(0, str(BASE_DIR / "scripts"))
        from keep_awake import KeepAwake, set_high_priority
        keep_awake = KeepAwake(prevent_display_sleep=False)
        keep_awake.enable()
        log.info("KeepAwake: computador nao vai dormir enquanto worker rodar")
        set_high_priority()
        log.info("Prioridade do processo: ALTA")
    except Exception as e:
        log.warn(f"KeepAwake falhou (nao critico): {e}")

    # === ACCOUNT POOL: aquecer contas Qwen no startup ===
    # CRÍTICO: AccountPool é OBRIGATÓRIO em modo pipeline. Se falhar, aborta
    # startup em vez de cair silenciosamente para legado (que causava 30-60s/job
    # invisíveis). Bug identificado pela auditoria de legado (P1.4).
    pool = None
    if USE_PIPELINE:
        try:
            from Playwright.qwen_account_pool import AccountPool, load_accounts_config
            accounts_config = load_accounts_config()
            if len(accounts_config) < 2:
                log.error(f"CRÍTICO: Apenas {len(accounts_config)} conta(s) em accounts.json")
                log.error("AccountPool requer 2+ contas para paralelizar capa+linha.")
                log.error("Adicione contas em Playwright/accounts.json OU rode worker.py com USE_PIPELINE=False")
                log.error("Abortando startup — não há fallback legado.")
                sys.exit(1)
            headless = os.environ.get("QWEN_HEADLESS", "True").lower() in ("true", "1", "yes")
            log.info(f"Aquecendo {len(accounts_config)} contas Qwen (headless={headless})...")
            pool = AccountPool.initialize(accounts_config, headless=headless)
            log.info(f"Pool pronto! {pool.ready_count}/{pool.total_accounts} contas — "
                     f"max {pool.max_concurrent_jobs} jobs simultaneos")
        except FileNotFoundError as e:
            log.error(f"CRÍTICO: accounts.json não encontrado: {e}")
            log.error("Crie Playwright/accounts.json com 2+ contas Qwen.")
            log.error("Abortando startup — não há fallback legado.")
            sys.exit(1)
        except Exception as e:
            log.error(f"CRÍTICO: AccountPool falhou ao inicializar: {e}")
            log.error("Abortando startup — não há fallback legado.")
            traceback.print_exc()
            sys.exit(1)

    if USE_PIPELINE:
        log.info(f"Worker iniciado em MODO PIPELINE (MAX_READY={MAX_READY_TO_RENDER})")
        run_pipeline_workers(pool=pool)
    else:
        log.info("Worker iniciado em MODO SEQUENCIAL (legado)")
        run_sequential_worker()

    # Desligar pool e KeepAwake ao sair
    if pool:
        log.info("Desligando pool de contas...")
        pool.shutdown()
    if keep_awake:
        keep_awake.disable()


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


def run_pipeline_workers(pool=None):
    """Modo pipeline: N threads prepare + N threads render (PARALELO).

    - Esteiras Prepare (N threads): pega jobs queued, faz download + Qwen,
      marca ready_to_render com prep_data.
      Se pool disponível, cada job usa 2 contas do pool (login já feito).
      N = max_concurrent_jobs do pool, ou 1 se sem pool.
    - Esteiras Render (N threads): pega jobs ready_to_render, faz corte +
      render + cleanup, marca ready.
      N render threads = N prepare threads = jobs simultâneos.

    A esteira Prepare respeita MAX_READY_TO_RENDER: se fila render estiver
    cheia, pausa.
    """
    stop_event = threading.Event()

    # Determinar numero de threads prepare
    # Precisa de PELO MENOS 2 contas para paralelizar (1 job = 2 contas).
    # Se nao tem contas suficientes, cai para modo legado (1 thread, sem pool).
    # MAX_PARALLEL_JOBS limita o número de jobs simultâneos (mesmo com mais contas).
    if pool and pool.max_concurrent_jobs >= 1:
        num_prep = pool.max_concurrent_jobs
        if MAX_PARALLEL_JOBS > 0 and num_prep > MAX_PARALLEL_JOBS:
            log.info(f"MAX_PARALLEL_JOBS={MAX_PARALLEL_JOBS} limitando de {num_prep} para {MAX_PARALLEL_JOBS} jobs")
            num_prep = MAX_PARALLEL_JOBS
        log.info(f"Pool ativo: {pool.total_accounts} contas, {num_prep} jobs simultâneos")
    else:
        if pool and pool.total_accounts < 2:
            log.warn(f"Pool tem {pool.total_accounts} contas — precisa de 2+ para paralelizar")
            log.warn("Caindo para modo legado (1 thread, sem pool)")
            pool = None  # Nao usar pool com contas insuficientes
        num_prep = 1
        log.info("Sem pool: 1 esteira prepare (modo legado)")

    # N render threads = mesmo número de prepare threads
    # Cada render é independente (FFmpeg subprocess), roda em paralelo
    num_render = num_prep
    log.info(f"{num_render} esteiras render (PARALELO — 1 render por prepare)")

    # Ajustar FFmpeg threads por render para evitar contenção de CPU
    # Com N renders paralelos, cada FFmpeg deve usar ~cores/N threads
    if num_render > 1 and "FFMPEG_THREADS_PER_RENDER" not in os.environ:
        cpu_count = psutil.cpu_count(logical=False) or 2
        threads_per_render = max(1, cpu_count // num_render)
        os.environ["FFMPEG_THREADS_PER_RENDER"] = str(threads_per_render)
        log.info(f"CPU: {cpu_count} nuc físicos, {num_render} renders -> "
                 f"FFmpeg threads/render={threads_per_render}")
    elif "FFMPEG_THREADS_PER_RENDER" not in os.environ:
        os.environ["FFMPEG_THREADS_PER_RENDER"] = "0"  # Usa todos os cores (1 render)
        log.info("FFmpeg threads/render=0 (1 render — usa todos os cores)")

    # Criar N threads prepare
    prep_threads = []
    for i in range(num_prep):
        t = threading.Thread(
            target=worker_prepare,
            args=(stop_event, pool),
            name=f"esteira_prep_{i}",
            daemon=True,
        )
        prep_threads.append(t)

    # N threads render
    render_threads = []
    for i in range(num_render):
        t = threading.Thread(
            target=worker_render,
            args=(stop_event, i),
            name=f"esteira_render_{i}",
            daemon=True,
        )
        render_threads.append(t)

    # Iniciar todas
    for t in prep_threads:
        t.start()
        log.info(f"{t.name} iniciada — TID={t.native_id}")
    for t in render_threads:
        t.start()
        log.info(f"{t.name} iniciada — TID={t.native_id}")

    try:
        # Main thread apenas aguarda Ctrl+C
        while not stop_event.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Encerrando worker (aguardando esteiras terminarem jobs em andamento)...")
        stop_event.set()

    # Aguardar esteiras terminarem (com timeout)
    for t in prep_threads:
        t.join(timeout=10)
    for t in render_threads:
        t.join(timeout=10)
    log.info("Worker encerrado.")


def worker_prepare(stop_event, pool=None):
    """Esteira Prepare: pega jobs queued, faz download + Qwen, marca ready_to_render.

    OBRIGATORIAMENTE usa AccountPool (login feito 1x no startup). Não há fallback
    legado — se pool=None, main() deveria ter abortado o startup.

    Respeita MAX_READY_TO_RENDER: se fila render esta cheia, pausa.
    """
    from pipeline.simple import preparar_video_async_with_accounts

    # === DEBUG INSTRUMENTATION: backoff_max_ready ===
    # Ponto cego #7 (agente1.md): o loop dorme 0.5s sem logar nada,
    # podendo acumular 60s+ invisível por job quando render está lento.
    # Agora loga timestamp de entrada, duração acumulada e dispara
    # anomaly detector se backoff > 10s.
    _backoff_start = None
    _backoff_iters = 0

    while not stop_event.is_set():
        try:
            # Respeitar MAX_READY_TO_RENDER
            if count_ready_to_render() >= MAX_READY_TO_RENDER:
                if _backoff_start is None:
                    _backoff_start = time.perf_counter()
                    _backoff_iters = 0
                    log.info(f"[A] [backoff_max_ready] ENTER ready_count={count_ready_to_render()} "
                             f"limit={MAX_READY_TO_RENDER} — esteira A pausada aguardando render consumir")
                _backoff_iters += 1
                time.sleep(0.5)
                continue

            # Saiu do backoff (se estava em backoff) — loga duração total
            if _backoff_start is not None:
                _backoff_dt = time.perf_counter() - _backoff_start
                log.info(f"[A] [backoff_max_ready] EXIT waited={_backoff_dt:.2f}s iterations={_backoff_iters}")
                if _backoff_dt > 10.0:
                    log.warn(f"[A] [backoff_max_ready] ANOMALIA esteira A pausada {_backoff_dt:.2f}s "
                             f"esperando render consumir — render é gargalo "
                             f"(considere +threads render, +MAX_READY_TO_RENDER, ou perfil de FFmpeg)")
                _backoff_start = None
                _backoff_iters = 0

            job = get_next_queued_job()
            if not job:
                time.sleep(1)
                continue

            job_id = job["job_id"]
            chat_id = job["chat_id"]
            raw_input = job["input_url"]
            url_display = raw_input if len(raw_input) <= 60 else raw_input[:57] + "..."

            log.info(f"[A {job_id[:12]}] Pegou job — chat={chat_id}")

            # get_next_queued_job() já marcou como 'preparing' atomicamente
            # Não precisamos chamar set_job_preparing() separadamente

            # Notificar usuário que o processamento começou (1 mensagem só!)
            send_telegram_message(
                chat_id,
                f"\U0001f504 <b>Processando seu v\u00eddeo...</b>\n\n"
                f"Link: <code>{escape_html(url_display)}</code>\n\n"
                "Baixando e analisando com IA.",
                parse_mode="HTML"
            )

            # [1/3] Download — o link JÁ foi validado pelo listener,
            # raw_input JÁ é o clean_url. Não precisamos re-validar.
            # Se o link estiver quebrado, o download vai falhar naturalmente.
            try:
                result = baixar_video(raw_input, chat_id)
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

            # [2/3] Qwen — OBRIGATORIAMENTE via pool (não há fallback legado)
            # Se pool=None aqui, é bug: main() deveria ter abortado o startup.
            try:
                if pool is None:
                    raise RuntimeError(
                        "pool=None em worker_prepare — isto não deveria acontecer. "
                        "AccountPool falhou no startup mas worker não abortou. "
                        "Verifique USE_PIPELINE no .env e accounts.json."
                    )
                prep_data = _prepare_with_pool(pool, job_id, saved_path, chat_id)
            except Exception as e:
                set_job_failed(job_id, f"prepare_failed: {e}")
                traceback.print_exc()
                send_telegram_message(
                    chat_id,
                    f"\u26a0\ufe0f Erro ao analisar v\u00eddeo\n\n"
                    f"Link: {url_display}\nMotivo: {str(e)[:200]}",
                )
                continue

            # Salvar prep_data no DB e marcar ready_to_render
            set_job_ready_to_render(job_id, prep_data)
            log.info(f"[A {job_id[:12]}] Pronto para render — prep_data salvo")

        except Exception as exc:
            log.error(f"[A] Erro na esteira prepare: {exc}")
            traceback.print_exc()
            time.sleep(5)


def _prepare_with_pool(pool, job_id, saved_path, chat_id):
    """Prepara video usando 2 contas do pool. Thread-safe.

    1. Adquire 2 contas (bloqueia ate ter disponivel)
    2. Submete trabalho async no event loop do pool
    3. Devolve contas ao pool quando termina

    Login ja foi feito no startup — zero overhead aqui.
    """
    from pipeline.simple import preparar_video_async_with_accounts
    conta_capa = None
    conta_linha = None

    try:
        # Adquirir 2 contas do pool (bloqueia se nao houver)
        log.info(f"[A {job_id[:12]}] Adquirindo 2 contas do pool...")
        conta_capa = pool.acquire(timeout=60)
        conta_linha = pool.acquire(timeout=60)
        log.info(f"[A {job_id[:12]}] Contas adquiridas: "
                 f"capa={conta_capa.id} linha={conta_linha.id}")

        # Rodar trabalho async no event loop do pool
        prep_data = pool.run_async(
            preparar_video_async_with_accounts(
                job_id, saved_path, chat_id,
                conta_capa=conta_capa,
                conta_linha=conta_linha,
            )
        )
        return prep_data

    finally:
        # SEMPRE devolver contas ao pool
        if conta_capa:
            pool.release(conta_capa)
            log.info(f"[A {job_id[:12]}] Conta {conta_capa.id} devolvida ao pool")
        if conta_linha:
            pool.release(conta_linha)
            log.info(f"[A {job_id[:12]}] Conta {conta_linha.id} devolvida ao pool")


def worker_render(stop_event, render_idx=0):
    """Esteira Render: pega jobs ready_to_render, faz corte + render + cleanup.

    Com N threads render, múltiplos jobs são renderizados em PARALELO.
    Cada thread pega o próximo job disponível — sem dependência entre threads.

    Trata erros: se render falha, marca job como failed e continua.
    """
    from pipeline.simple import renderizar_video

    while not stop_event.is_set():
        try:
            # Usar função atômica para evitar race condition entre threads render
            job = acquire_ready_to_render_job()
            if not job:
                time.sleep(1)
                continue

            job_id = job["job_id"]
            chat_id = job["chat_id"]
            log.info(f"[B{render_idx} {job_id[:12]}] Pegou job para render")

            # Job já está marcado como 'rendering' por acquire_ready_to_render_job()

            # Recuperar prep_data (código de barras do chassis)
            prep_data = get_preparation_data(job_id)
            ok, motivo = validate_preparation_data(prep_data)
            if not ok:
                set_job_failed(job_id, f"prep_data_invalid: {motivo}")
                log.error(f"[B{render_idx} {job_id[:12]}] prep_data inválido: {motivo}")
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
            log.info(f"[B{render_idx} {job_id[:12]}] Render concluído: {final_path}")

            # Notificar usuário
            send_telegram_message(
                chat_id,
                f"\u2705 Vídeo pronto!\n\n"
                f"Link: <code>{escape_html(url_display)}</code>\n\n"
                f"Toque em \U0001f4e5 Meus vídeos para ver o status.",
                parse_mode="HTML"
            )

            # Verificar se fila do chat está concluída
            # count_active() conta apenas jobs que ainda estão sendo processados
            # (queued, preparing, ready_to_render, rendering) — NÃO conta 'pending'
            # que ainda nem foram confirmados pelo usuário.
            remaining = count_active(chat_id)
            if remaining == 0:
                send_telegram_message(
                    chat_id,
                    "\U0001f389 Fila concluída! Todos os seus vídeos foram processados.",
                    parse_mode="HTML"
                )

        except Exception as exc:
            log.error(f"[B{render_idx}] Erro na esteira render: {exc}")
            traceback.print_exc()
            time.sleep(5)


if __name__ == "__main__":
    main()
