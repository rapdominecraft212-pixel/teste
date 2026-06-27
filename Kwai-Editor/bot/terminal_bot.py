import os
import sys
import secrets
import subprocess
import time as time_module
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from db import (
    init_db, create_job, get_user_jobs,
    count_ready, count_processing, count_failed
)
from checar_link import validar as validar_link
from log_utils import log, truncar_erro

CHAT_ID = 123456


def ts():
    return datetime.now().strftime("%H:%M:%S")


def limpar():
    os.system("cls" if os.name == "nt" else "clear")


def cabecalho():
    ready = count_ready(CHAT_ID)
    processing = count_processing(CHAT_ID)
    failed = count_failed(CHAT_ID)
    print(f"╔{'═'*58}╗")
    print(f"║  Kwai Editor — Terminal Bot (modo teste)                  ║")
    print(f"║  chat_id: {CHAT_ID}  |  \u2705 {ready}  \U0001f4e4 {processing}  \u26a0 {failed}            ║")
    print(f"╚{'═'*58}╝")


def menu():
    print()
    print("  [1] Enviar link do Kwai")
    print("  [2] Ver status detalhado")
    print("  [3] Processar jobs na fila")
    print("  [4] Abrir pasta data/editado/")
    print("  [5] Sair")
    print()


def op_enviar_link():
    print(f"\n[{ts()}] --- Enviar link ---")
    print("Cole o link do Kwai (ou 'sair' para voltar):")
    while True:
        linha = input("> ").strip()
        if not linha or linha.lower() in ("sair", "exit", "q"):
            break
        try:
            resultado = validar_link(linha)
        except RuntimeError as e:
            print(f"  [{ts()}] {e}")
            continue
        job_id = secrets.token_urlsafe(16)
        create_job(job_id, CHAT_ID, resultado["clean_url"])
        jobs = get_user_jobs(CHAT_ID)
        qtd = sum(1 for j in jobs if j["status"] == "queued")
        print(f"  [{ts()}] Link adicionado! job={job_id[:12]}  fila={qtd}")
        log.info(f"TERMINAL: link adicionado job={job_id[:12]} url={resultado['clean_url']}")


def op_ver_status():
    print(f"\n[{ts()}] --- Status detalhado ---")
    ready = count_ready(CHAT_ID)
    processing = count_processing(CHAT_ID)
    failed = count_failed(CHAT_ID)
    total = ready + processing + failed

    if total == 0:
        print("  Nenhum job encontrado.")
        return

    print(f"  Pronto(s):  {ready}")
    print(f"  Editando:   {processing}")
    print(f"  Com erro:   {failed}")
    print(f"  Total:      {total}")
    print()

    jobs = get_user_jobs(CHAT_ID)
    if not jobs:
        return

    print(f"  {'Criado':<10} {'ID':<14} {'Status':<10} {'Tam':<8} {'Detalhe'}")
    print(f"  {'-'*10} {'-'*14} {'-'*10} {'-'*8} {'-'*50}")

    failed_jobs = []
    for j in jobs:
        criado = j.get("created_at", "")[11:19] if j.get("created_at") else "?"
        jid = j["job_id"][:12]
        st = j["status"]
        out = ""
        size_str = ""
        if st == "ready":
            out = j.get("output_path") or "?"
            if out != "?":
                p = Path(out)
                out = p.name[:48]
                if p.exists():
                    size_str = f"{p.stat().st_size/1024/1024:.0f}MB"
        elif st == "failed":
            err = j.get("error_message") or "motivo desconhecido"
            first_line = err.split('\n')[0]
            out = (first_line[:47] + "...") if len(first_line) > 50 else first_line
            failed_jobs.append(j)
        elif st == "queued":
            out = "aguardando..."
        elif st == "processing":
            out = "em andamento..."
        print(f"  {criado:<10} {jid:<14} {st:<10} {size_str:<8} {out}")

    if failed_jobs:
        print()
        print("  " + "=" * 56)
        print("  \u2551 ERROS COMPLETOS" + " " * 37 + "\u2551")
        print("  " + "=" * 56)
        for j in failed_jobs:
            jid = j["job_id"][:12]
            err = j.get("error_message") or "(sem mensagem)"
            url = j.get("input_url", "")
            print(f"\n  --- Job {jid} ---")
            print(f"  URL: {url}")
            print(f"  Erro:")
            for line in truncar_erro(err).split('\n'):
                print(f"    {line}")
            print()


def processar_jobs_com_pool():
    """Modo moderno: inicializa AccountPool + sobe threads prepare/render,
    polla count_active até zerar, deslige graciosamente.

    Isto substitui o legado process_job() que criava Chrome novo por job
    (30-60s de overhead invisível). Agora usa contas persistentes do pool.
    """
    import threading
    from db import count_active, recover_processing_jobs, recover_pipeline_jobs
    from worker import (
        worker_prepare, worker_render,
        USE_PIPELINE, MAX_PARALLEL_JOBS, MAX_READY_TO_RENDER,
    )

    if not USE_PIPELINE:
        print(f"  [{ts()}] USE_PIPELINE=False no .env — modo legado não suportado.")
        print(f"  Habilite USE_PIPELINE=True para usar AccountPool.")
        return False

    # Recovery: recupera jobs de execuções anteriores que ficaram em estado
    # intermediário (preparing/rendering). Igual worker.py:main()
    try:
        recover_processing_jobs()
        recover_pipeline_jobs()
        log.info("TERMINAL: recovery de jobs executado")
    except Exception as e:
        log.warn(f"TERMINAL: recovery falhou (não crítico): {e}")

    # Inicializar AccountPool (CRÍTICO — sem fallback para legado)
    try:
        from Playwright.qwen_account_pool import AccountPool, load_accounts_config
        accounts_config = load_accounts_config()
        if len(accounts_config) < 2:
            print(f"  [{ts()}] CRÍTICO: Apenas {len(accounts_config)} conta(s) em accounts.json")
            print(f"  AccountPool requer 2+ contas. Adicione em Playwright/accounts.json.")
            return False
        headless = os.environ.get("QWEN_HEADLESS", "True").lower() in ("true", "1", "yes")
        print(f"  [{ts()}] Aquecendo {len(accounts_config)} contas Qwen (headless={headless})...")
        pool = AccountPool.initialize(accounts_config, headless=headless)
        print(f"  [{ts()}] Pool pronto! {pool.ready_count}/{pool.total_accounts} contas")
        log.info(f"TERMINAL: pool inicializado com {pool.ready_count} contas")
    except FileNotFoundError as e:
        print(f"  [{ts()}] CRÍTICO: accounts.json não encontrado: {e}")
        return False
    except Exception as e:
        print(f"  [{ts()}] CRÍTICO: AccountPool falhou: {e}")
        log.error(f"TERMINAL: AccountPool falhou: {e}")
        return False

    # Configurar FFMPEG_THREADS_PER_RENDER (igual worker.py:run_pipeline_workers)
    num_prep = min(pool.max_concurrent_jobs, MAX_PARALLEL_JOBS) if MAX_PARALLEL_JOBS > 0 else pool.max_concurrent_jobs
    num_render = num_prep
    if num_render > 1 and "FFMPEG_THREADS_PER_RENDER" not in os.environ:
        cpu_count = os.cpu_count() or 4
        threads_per_render = max(1, cpu_count // num_render)
        os.environ["FFMPEG_THREADS_PER_RENDER"] = str(threads_per_render)
        print(f"  [{ts()}] CPU: {cpu_count} cores, {num_render} renders -> "
              f"FFmpeg threads/render={threads_per_render}")
    elif "FFMPEG_THREADS_PER_RENDER" not in os.environ:
        os.environ["FFMPEG_THREADS_PER_RENDER"] = "0"
        print(f"  [{ts()}] FFmpeg threads/render=0 (1 render — usa todos os cores)")

    # Subir threads prepare + render
    stop_event = threading.Event()
    prep_threads = []
    for i in range(num_prep):
        t = threading.Thread(
            target=worker_prepare,
            args=(stop_event, pool),
            name=f"terminal_prep_{i}",
            daemon=True,
        )
        prep_threads.append(t)

    render_threads = []
    for i in range(num_render):
        t = threading.Thread(
            target=worker_render,
            args=(stop_event, i),
            name=f"terminal_render_{i}",
            daemon=True,
        )
        render_threads.append(t)

    for t in prep_threads + render_threads:
        t.start()
        print(f"  [{ts()}] {t.name} iniciada")

    # Pollar até todos os jobs terminarem (count_active == 0)
    print(f"  [{ts()}] Aguardando jobs serem processados...")
    log.info(f"TERMINAL: {num_prep} threads prep + {num_render} threads render ativas")

    idle_iterations = 0
    last_active_count = -1
    while True:
        active = count_active(CHAT_ID)
        if active != last_active_count:
            print(f"  [{ts()}] Jobs ativos: {active}")
            last_active_count = active
            idle_iterations = 0
        else:
            idle_iterations += 1

        if active == 0:
            print(f"  [{ts()}] Todos os jobs concluídos!")
            break

        # Safety: se não houve mudança em 5 minutos (300 iterações de 1s), abortar
        if idle_iterations > 300:
            print(f"  [{ts()}] TIMEOUT: jobs ainda ativos após 5min sem progresso")
            log.error(f"TERMINAL: timeout — {active} jobs ainda ativos")
            break

        time_module.sleep(1)

    # Sinal de parada
    stop_event.set()
    print(f"  [{ts()}] Sinal de parada enviado, aguardando threads...")

    # Aguardar threads terminarem (com timeout)
    for t in prep_threads + render_threads:
        t.join(timeout=10)
        if t.is_alive():
            print(f"  [{ts()}] WARN: {t.name} ainda rodando após 10s")

    # Desligar pool
    print(f"  [{ts()}] Desligando pool de contas...")
    pool.shutdown()
    print(f"  [{ts()}] Pool desligado.")
    log.info("TERMINAL: pool desligado")

    return True


def op_processar():
    """Modernizado: usa AccountPool + worker_prepare/worker_render em vez de process_job legado."""
    from db import count_active, get_next_queued_job
    from worker import get_queued_jobs_round_robin

    # Verifica se há jobs na fila (queued = aguardando processamento)
    jobs = get_queued_jobs_round_robin()
    if not jobs:
        # Também pode haver jobs em andamento de execução anterior
        active = count_active(CHAT_ID)
        if active == 0:
            print(f"\n  [{ts()}] Nenhum job na fila.")
            return
        print(f"\n  [{ts()}] {active} job(s) em andamento de execução anterior.")
        print(f"  Continuando processamento...")
    else:
        print(f"\n  [{ts()}] {len(jobs)} job(s) na fila, iniciando com AccountPool (modo moderno)...")

    log.info(f"TERMINAL: processando {len(jobs)} job(s) via AccountPool")
    t0 = time_module.time()

    sucesso = processar_jobs_com_pool()

    elapsed = time_module.time() - t0
    editado = PROJECT_ROOT / "data" / "editado" / str(CHAT_ID)

    print(f"\n  [{ts()}] Processamento concluido em {elapsed:.0f}s")

    if editado.exists():
        arquivos = list(editado.glob("*.mp4"))
        if arquivos:
            print(f"  Videos gerados ({len(arquivos)}):")
            for f in arquivos:
                size = f.stat().st_size
                print(f"    \u2705 {f.name}  ({size/1024/1024:.1f}MB)")
            print(f"\n  Pasta: {editado}")
        else:
            print("  Nenhum video .mp4 encontrado na pasta editado.")
            arquivos = []
    else:
        print("  Nenhum video foi gerado.")
        arquivos = []

    log.info(f"TERMINAL: lote concluido em {elapsed:.0f}s, "
             f"{len(arquivos) if editado.exists() else 0} videos, "
             f"pool={'OK' if sucesso else 'FALHOU'}")


def op_abrir_pasta():
    editado = PROJECT_ROOT / "data" / "editado" / str(CHAT_ID)
    if not editado.exists():
        os.makedirs(str(editado), exist_ok=True)
    print(f"\n  [{ts()}] Abrindo: {editado}")
    if editado.exists() and any(editado.iterdir()):
        print(f"  Videos na pasta:")
        for f in sorted(editado.iterdir()):
            size = f.stat().st_size if f.is_file() else 0
            print(f"    {f.name}  ({size/1024/1024:.1f}MB)" if f.is_file() else f"    {f.name}/")
    else:
        print("  (pasta vazia)")
    if os.name == "nt":
        subprocess.Popen(["explorer", str(editado)])
    else:
        subprocess.Popen(["xdg-open", str(editado)])


def main():
    init_db()
    log.info("TERMINAL: bot de terminal iniciado")
    while True:
        limpar()
        cabecalho()
        menu()
        op = input("Escolha: ").strip()
        if op == "1":
            op_enviar_link()
            input("\nEnter para continuar...")
        elif op == "2":
            op_ver_status()
            input("\nEnter para continuar...")
        elif op == "3":
            op_processar()
            input("\nEnter para continuar...")
        elif op == "4":
            op_abrir_pasta()
            input("\nEnter para continuar...")
        elif op == "5":
            print(f"\n  [{datetime.now().strftime('%H:%M:%S')}] Encerrando.")
            log.info("TERMINAL: encerrado")
            break
        else:
            print("  Opcao invalida.")
            input("Enter para continuar...")


if __name__ == "__main__":
    main()
