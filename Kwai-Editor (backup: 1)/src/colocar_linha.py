import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from moviepy import VideoFileClip
from PIL import Image

from src.grid_utils import criar_grid_imagem
from Playwright import qwen_linha


def analisar(video_path):
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Arquivo nao encontrado: {video_path}")

    frame_path, grid_path, cell_h = _preparar_grid(video_path)
    try:
        return qwen_linha.analisar(grid_path, cell_h)
    finally:
        for p in [frame_path, grid_path]:
            try:
                os.remove(p)
            except Exception:
                pass


def analisar_com_instancia(qr, video_path):
    frame_path, grid_path, cell_h = _preparar_grid(video_path)
    try:
        return qwen_linha.analisar_com_instancia(qr, grid_path, cell_h)
    finally:
        for p in [frame_path, grid_path]:
            try:
                os.remove(p)
            except Exception:
                pass


def _preparar_grid(video_path):
    with VideoFileClip(video_path) as clip:
        mid_frame = clip.duration / 2
        frame_np = clip.get_frame(mid_frame)
        frame = Image.fromarray(frame_np)

    tmp_dir = Path(tempfile.gettempdir())
    job_uid = os.path.basename(video_path).replace(".", "_")
    frame_path = str(tmp_dir / f"frame_{job_uid}.jpg")
    grid_path = str(tmp_dir / f"grid_{job_uid}.jpg")
    frame.save(frame_path, quality=95)

    _, _, cell_h = criar_grid_imagem(frame_path, grid_path)
    return frame_path, grid_path, cell_h


if __name__ == "__main__":
    upload_dir = Path(__file__).resolve().parent.parent / "data" / "upload"
    videos = sorted(upload_dir.glob("**/*.mp4"))
    if not videos:
        print("Nenhum video encontrado em data/upload/")
        sys.exit(1)
    y1, y2 = analisar(str(videos[0]))
    print(f"Linhas: y1={y1}, y2={y2}")
