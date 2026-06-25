import os
import sys
import time
import subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = [
    ("listener", BASE / "bot" / "listener.py"),
    ("worker",   BASE / "bot" / "worker.py"),
]


def setup_job():
    import win32job
    job = win32job.CreateJobObject(None, "KwaiEditor_Job")
    info = win32job.QueryInformationJobObject(
        job, win32job.JobObjectExtendedLimitInformation
    )
    info["BasicLimitInformation"]["LimitFlags"] |= (
        win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    )
    win32job.SetInformationJobObject(
        job, win32job.JobObjectExtendedLimitInformation, info
    )
    return job


def assign_to_job(job, pid):
    import win32api, win32con, win32job
    try:
        handle = win32api.OpenProcess(
            win32con.PROCESS_TERMINATE | win32con.PROCESS_SET_QUOTA,
            False, pid
        )
        win32job.AssignProcessToJobObject(job, handle)
        win32api.CloseHandle(handle)
        return True
    except Exception:
        return False


def main():
    os.chdir(str(BASE))

    # Garantir estrutura de diretorios
    for d in ["upload", "cortado", "editado", "biblioteca"]:
        (BASE / "data" / d).mkdir(parents=True, exist_ok=True)

    pid_path = BASE / "launcher.pid"
    pid_path.write_text(str(os.getpid()))

    job = setup_job()
    self_in_job = assign_to_job(job, os.getpid())

    if not self_in_job:
        print("[launcher] Aviso: launcher nao pode entrar no Job Object")

    procs = []
    for name, script in SCRIPTS:
        p = subprocess.Popen(
            [sys.executable, str(script)],
            cwd=str(BASE),
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        procs.append((name, p))
        print(f"[launcher] {name} iniciado (PID {p.pid})")

        if not self_in_job:
            assign_to_job(job, p.pid)

    print()
    print("[launcher] Servidores rodando. Pressione Ctrl+C para parar.")
    print()

    try:
        while True:
            for name, p in list(procs):
                if p.poll() is not None:
                    print(f"[launcher] ERRO: {name} morreu inesperadamente (codigo {p.returncode})")
                    return 1
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[launcher] Encerrando servidores...")
    finally:
        # Parar processos
        for name, p in procs:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()

        # Remover PID file
        pid_path.unlink(missing_ok=True)

        print("[launcher] Servidores encerrados. Use parar_servidores.bat para reset completo.")


if __name__ == "__main__":
    sys.exit(main())
