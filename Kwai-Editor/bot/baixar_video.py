import sys
import subprocess
import time
from pathlib import Path

TIMEOUT_DOWNLOAD = 300


def _detectar_yt_dlp():
    try:
        r = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--version"],
            capture_output=True, text=True, timeout=30
        )
        if r.returncode == 0:
            return [sys.executable, "-m", "yt_dlp"]
    except Exception:
        pass
    raise RuntimeError(
        "yt-dlp não está instalado.\n"
        "Instale com: pip install yt-dlp"
    )


def baixar(url, usuario_id, basedir=None):
    if basedir is None:
        basedir = Path(__file__).resolve().parent.parent

    yt_dlp_cmd = _detectar_yt_dlp()

    upload_dir = Path(basedir) / "data" / "upload" / str(usuario_id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    cmd = yt_dlp_cmd + [
        "--no-warnings",
        "--no-playlist",
        "-P", str(upload_dir),
        "-o", "kwai_%(epoch)s.%(ext)s",
        "--print", "after_move:%(filepath)s",
        "--restrict-filenames",
        url,
    ]

    # Retry: Windows Defender pode bloquear o rename do .temp (WinError 32)
    last_err = ""
    for attempt in range(3):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_DOWNLOAD)
        if r.returncode != 0:
            last_err = r.stderr.strip() or 'erro desconhecido'
            if "WinError 32" in last_err or "being used by another process" in last_err:
                time.sleep(3)
                continue
            raise RuntimeError(f"Falha ao baixar vídeo: {last_err}")
        break

    saved_path = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    if not saved_path or not Path(saved_path).exists():
        raise RuntimeError("Arquivo baixado não encontrado após download.")

    saved_path = str(Path(saved_path).resolve())
    size_mb = Path(saved_path).stat().st_size / (1024 * 1024)

    return {
        "ok": True,
        "saved_path": saved_path,
        "size_mb": round(size_mb, 1),
    }
