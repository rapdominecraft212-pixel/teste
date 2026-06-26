import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.video_popup_linear import criar_video

BITRATE = None
RESOLUTION = None


def analisar(input_path, titulo, subtitulo, chat_id=None, on_render_progress=None, timings=None):
    if timings is None:
        timings = {
            "popup_1_in": 0.0,
            "popup_1_out": 7.0,
            "transition_dur": 1.0,
            "popup_fade_in": 1.5,
            "text_fade_dur": 0.5,
        }

    output_dir = str(Path(__file__).resolve().parent.parent / "data" / "editado" / str(chat_id or "default"))
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Se BITRATE ou RESOLUTION forem configurados, podemos passar para criar_video
    # Por enquanto, manter o comportamento padrao de video_popup_linear

    ffmpeg_params = ["-pix_fmt", "yuv420p", "-movflags", "faststart", "-crf", "18"]
    if BITRATE:
        ffmpeg_params.extend(["-b:v", BITRATE])

    final_path = criar_video(
        input_path=input_path,
        output_dir=output_dir,
        titulo=titulo,
        subtitulo=subtitulo,
        on_render_progress=on_render_progress,
        **timings,
    )
    return final_path
