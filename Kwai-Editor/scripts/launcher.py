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

    # === KEEP AWAKE: impedir computador de dormir enquanto servidor roda ===
    # Isso e CRITICO porque:
    # - Qwen pode demorar minutos para responder
    # - FFmpeg pode demorar minutos para renderizar
    # - Se o computador dormir no meio, o processamento pausa
    # - Quando acorda, a sessao do Qwen pode ter expirado
    sys.path.insert(0, str(BASE / "scripts"))
    try:
        from keep_awake import KeepAwake, set_high_priority
        keep_awake = KeepAwake(prevent_display_sleep=False)
        keep_awake.enable()
        print("[launcher] KeepAwake: computador nao vai dormir enquanto servidor rodar")
    except Exception as e:
        print(f"[launcher] Aviso: KeepAwake falhou: {e}")
        keep_awake = None

    # === PRIORIDADE ALTA: focar poder computacional no servidor ===
    try:
        set_high_priority()
        print("[launcher] Prioridade do processo: ALTA")
    except Exception as e:
        print(f"[launcher] Aviso: alteracao de prioridade falhou: {e}")

    # Garantir estrutura de diretorios
    for d in ["upload", "cortado", "editado", "biblioteca"]:
        (BASE / "data" / d).mkdir(parents=True, exist_ok=True)

    pid_path = BASE / "launcher.pid"
    pid_path.write_text(str(os.getpid()))

    # Windows-specific: Job Object para matar processos filhos ao fechar
    job = None
    try:
        job = setup_job()
    except ImportError:
        print("[launcher] Aviso: win32job nao disponivel (nao e Windows)")

    if job:
        self_in_job = assign_to_job(job, os.getpid())
        if not self_in_job:
            print("[launcher] Aviso: launcher nao pode entrar no Job Object")

    procs = []
    for name, script in SCRIPTS:
        p = subprocess.Popen(
            [sys.executable, str(script)],
            cwd=str(BASE),
            creationflags=subprocess.CREATE_NO_WINDOW if platform_is_windows() else 0,
        )
        procs.append((name, p))
        print(f"[launcher] {name} iniciado (PID {p.pid})")

        if job and not self_in_job:
            assign_to_job(job, p.pid)

    print()
    print("[launcher] Servidores rodando. Pressione Ctrl+C para parar.")
    print("[launcher] KeepAwake ativo — computador permanecera acordado.")
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

        # Desativar KeepAwake — computador pode dormir novamente
        if keep_awake:
            keep_awake.disable()
            print("[launcher] KeepAwake desativado — computador pode dormir novamente")

        # Remover PID file
        pid_path.unlink(missing_ok=True)

        print("[launcher] Servidores encerrados. Use parar_servidores.bat para reset completo.")


def platform_is_windows():
    import platform
    return platform.system() == "Windows"


if __name__ == "__main__":
    sys.exit(main())
