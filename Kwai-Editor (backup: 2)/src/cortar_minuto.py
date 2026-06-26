import os
import sys
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
MINUTADO_DIR = DATA_DIR / "minutado"

MAX_DURATION = 180


def analisar(video_path, chat_id=None):
    video_path = str(video_path)
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Arquivo nao encontrado: {video_path}")

    cid = str(chat_id) if chat_id else "default"

    # Obter duracao
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe falhou: {result.stderr.strip()}")
    duration = float(result.stdout.strip())

    # Se ja for <= MAX_DURATION, copiar para minutado
    out_dir = MINUTADO_DIR / cid
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(video_path).stem
    ext = Path(video_path).suffix
    output_path = str(out_dir / f"{stem}_minutado{ext}")

    if duration <= MAX_DURATION:
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-c", "copy", output_path],
            capture_output=True, text=True, timeout=120
        )
    else:
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path,
             "-t", str(MAX_DURATION),
             "-c", "copy", output_path],
            capture_output=True, text=True, timeout=120
        )

    if not os.path.exists(output_path):
        raise RuntimeError(f"Arquivo minutado nao gerado: {output_path}")

    return output_path


if __name__ == "__main__":
    upload_dir = BASE_DIR / "data" / "upload"
    videos = sorted(upload_dir.glob("**/*.mp4"))
    if not videos:
        print("Nenhum video encontrado em data/upload/")
        sys.exit(1)
    saida = analisar(str(videos[0]))
    print(f"Minutado: {saida}")
