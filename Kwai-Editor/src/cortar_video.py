import sys
import os
import subprocess
import traceback
from pathlib import Path

from bot.log_utils import log, truncar_stderr


def _fail(orig_exc: BaseException, context: str, ffmpeg_stderr: str = ""):
    if ffmpeg_stderr:
        log.error(f"[FFMPEG] {truncar_stderr(ffmpeg_stderr)}")
    log.error(f"[FAIL] {context}: {orig_exc}")
    traceback.print_exc()
    raise RuntimeError(f"[{context}] {orig_exc}") from None


def cortar_video(input_path: str, y1: int, y2: int, output_dir: str = "cortado") -> str:
    try:
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"Arquivo nao encontrado: {input_path}")

        if y1 >= y2:
            raise ValueError(f"y1 ({y1}) deve ser menor que y2 ({y2})")

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        stem = Path(input_path).stem
        ext = Path(input_path).suffix
        output_path = str(out_dir / f"{stem}_cortado{ext}")

        # Probe video dimensions
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "default=noprint_wrappers=1", input_path],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffprobe falhou: {result.stderr.strip()}")

        info = result.stdout.strip().split("\n")
        H = 0
        for line in info:
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

        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-vf", f"crop=iw:{new_h}:0:{y1}",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-threads", "0",
            "-c:a", "copy",
            output_path,
        ]

        result = None
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg codigo {result.returncode}: {truncar_stderr(result.stderr)}")
        return output_path
    except Exception as e:
        ffmpeg_err = result.stderr.strip() if result and result.stderr else ""
        _fail(e, f"cortar_video({os.path.basename(input_path)}, y1={y1}, y2={y2})", ffmpeg_err)


def main():
    if len(sys.argv) != 4:
        print("Uso: python cortar_video.py <video> <linha_inicial> <linha_final>")
        print("Ex:  python cortar_video.py video.mp4 200 1000")
        sys.exit(1)

    input_path = sys.argv[1]
    y1 = int(sys.argv[2])
    y2 = int(sys.argv[3])

    try:
        output_path = cortar_video(input_path, y1, y2)
        print(f"Concluído: {output_path}")
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"Erro: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
