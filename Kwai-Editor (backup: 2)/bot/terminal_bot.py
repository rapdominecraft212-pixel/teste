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


def op_processar():
    from worker import get_queued_jobs_round_robin, process_job

    jobs = get_queued_jobs_round_robin()
    if not jobs:
        print(f"\n  [{ts()}] Nenhum job na fila.")
        return

    print(f"\n  [{ts()}] Processando {len(jobs)} job(s)...")
    log.info(f"TERMINAL: processando {len(jobs)} job(s)")
    t0 = time_module.time()

    for idx, j in enumerate(jobs, 1):
        jid = j["job_id"][:12]
        print(f"\n  ─── Job {idx}/{len(jobs)} [{jid}] ───")
        log.info(f"TERMINAL: job {jid} iniciando ({idx}/{len(jobs)})")
        process_job(j)

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
    else:
        print("  Nenhum video foi gerado.")

    log.info(f"TERMINAL: lote concluido em {elapsed:.0f}s, {len(arquivos) if editado.exists() else 0} videos")


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
