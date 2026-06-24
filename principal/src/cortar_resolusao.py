import os
import sys
import subprocess
from pathlib import Path

from bot.log_utils import truncar_stderr

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RESOLVIDO_DIR = DATA_DIR / "resolvido"


def analisar(video_path, y1, y2, chat_id=None):
    video_path = str(video_path)
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Arquivo nao encontrado: {video_path}")
    if y1 >= y2:
        raise ValueError(f"y1 ({y1}) deve ser menor que y2 ({y2})")

    cid = str(chat_id) if chat_id else "default"

    # Obter altura original do video
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height",
         "-of", "default=noprint_wrappers=1", video_path],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe falhou: {result.stderr.strip()}")

    H = 0
    for line in result.stdout.strip().split("\n"):
        if line.startswith("height="):
            H = int(line.split("=")[1])
            break
    if H == 0:
        raise RuntimeError("Nao foi possivel ler a altura do video via ffprobe")

    if y1 < 0:
        y1 = 0
    if y2 > H:
        y2 = H

    new_h = y2 - y1
    if new_h <= 0:
        raise ValueError(f"Altura de corte invalida: {new_h}px (y1={y1}, y2={y2}, H={H})")

    out_dir = RESOLVIDO_DIR / cid
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(video_path).stem
    ext = Path(video_path).suffix
    output_path = str(out_dir / f"{stem}_resolvido{ext}")

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", f"crop=iw:{new_h}:0:{y1}",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-c:a", "copy",
        output_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg codigo {r.returncode}: {truncar_stderr(r.stderr)}")

    if not os.path.exists(output_path):
        raise RuntimeError(f"Arquivo resolvido nao gerado: {output_path}")

    return output_path


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Uso: python cortar_resolusao.py <video> <y1> <y2>")
        sys.exit(1)
    saida = analisar(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]))
    print(f"Resolvido: {saida}")
