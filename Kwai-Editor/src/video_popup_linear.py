import os
import proglog
import subprocess
import tempfile
import hashlib
import time as time_module
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from moviepy import VideoFileClip, ImageClip, CompositeVideoClip, ColorClip, VideoClip


FINAL_SIZE = (720, 1274)
DURATION_FALLBACK = 180
MAX_DURATION = 180
FPS = 30
VIDEO_QUALITY = 22  # CRF: menor = maior qualidade (22 = bom equilíbrio velocidade/qualidade)

VIDEO_X, VIDEO_Y, VIDEO_W, VIDEO_H = 0, 108, 720, 540

USE_BLURRED_BACKGROUND = True
BLUR_RADIUS = 30
BLUR_DOWNSAMPLE_WIDTH = 160
ENCODER_PRESET = "veryfast"  # muito mais rapido que medium, qualidade quase identica a olho nu

# === Controle de threads FFmpeg para renders paralelos ===
# Quando N renders rodam em paralelo, cada FFmpeg deve usar menos threads
# para evitar contenção de CPU. Valor 0 = automático (usa todos os núcleos).
# Configurável via FFMPEG_THREADS_PER_RENDER (ex: "2" para limitar).
import os as _os
_FFMPEG_THREADS_PER_RENDER = int(_os.environ.get("FFMPEG_THREADS_PER_RENDER", "0"))


def _ffmpeg_threads():
    """Retorna o número de threads FFmpeg para este render.

    Se FFMPEG_THREADS_PER_RENDER=0 (default), usa todos os núcleos (comportamento original).
    Se configurado manualmente (ex: 2), limita para evitar contenção em renders paralelos.
    """
    if _FFMPEG_THREADS_PER_RENDER > 0:
        return str(_FFMPEG_THREADS_PER_RENDER)
    return "0"

# === Fase 1: pre-render do background blur ===
USE_PRERENDERED_BG = True
PRERENDER_BG_SIGMA = 30  # Gaussian blur sigma (gblur) — vidro embaçado suave

# === Fase 2: pre-render do popup com canal alpha ===
USE_PRERENDERED_POPUP = True
POPUP_PRERENDER_CODEC = "qtrle"
POPUP_PRERENDER_PIXFMT = "argb"

# === Fase 3: composite final via ffmpeg direto ===
USE_FFMPEG_COMPOSITE = True

POPUP_X, POPUP_Y, POPUP_W = 100, 647, 519
PADDING_X, PADDING_Y = 37, 38
FONT_SIZE = 30
COR_FUNDO = (255, 255, 255, 255)
COR_TEXTO = (0, 0, 0, 255)


def load_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "arial.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            pass
    return ImageFont.load_default()


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            trial = f"{current} {word}"
            bbox = font.getbbox(trial)
            width = bbox[2] - bbox[0]
            if width <= max_width:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def build_popup_image(text: str, popup_w: int, padding_x: int, padding_y: int,
                      font_size: int, cor_fundo: tuple, cor_texto: tuple):
    font = load_font(font_size)
    max_text_width = popup_w - (padding_x * 2)
    lines = wrap_text(text, font, max_text_width)

    line_bbox = font.getbbox("Ag")
    line_h = (line_bbox[3] - line_bbox[1]) + 8
    text_h = max(1, len(lines)) * line_h
    popup_h = text_h + (padding_y * 2)

    img = Image.new("RGBA", (popup_w, popup_h), (0, 0, 0, 0))
    fundo = ImageDraw.Draw(img)
    fundo.rounded_rectangle(
        [12, 0, popup_w - 12, popup_h],
        radius=12,
        fill=(255, 255, 255, 255)
    )

    draw = ImageDraw.Draw(img)
    y = padding_y
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_w = bbox[2] - bbox[0]
        x = (popup_w - line_w) // 2
        draw.text((x, y), line, font=font, fill=(0, 0, 0, 255))
        y += line_h

    bg = Image.new("RGBA", (popup_w, popup_h), (0, 0, 0, 0))
    bg_draw = ImageDraw.Draw(bg)
    bg_draw.rounded_rectangle(
        [12, 0, popup_w - 12, popup_h],
        radius=12,
        fill=(255, 255, 255, 255)
    )

    return img, bg


def load_source_clip(path: str):
    ext = Path(path).suffix.lower()
    if ext in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        clip = ImageClip(path).with_duration(DURATION_FALLBACK)
        return clip, DURATION_FALLBACK, True
    clip = VideoFileClip(path)
    return clip, clip.duration, False


def _prerender_background(input_path: str, duration: float, output_path: str) -> str:
    """Pre-renderiza o background blur UMA UNICA vez com ffmpeg direto (C puro)."""
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-t", str(duration),
        "-vf", f"scale={FINAL_SIZE[0]}:{FINAL_SIZE[1]}:force_original_aspect_ratio=increase,setsar=1:1,gblur=sigma={PRERENDER_BG_SIGMA},crop={FINAL_SIZE[0]}:{FINAL_SIZE[1]}",
        "-threads", _ffmpeg_threads(),  # Limitado em renders paralelos
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "28",  # BG blur nao precisa de alta qualidade
        "-an",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg prerender bg falhou: {result.stderr[-500:]}")
    return output_path


def make_background(source_clip, duration: float, is_image: bool, input_path: str,
                    bg_cache_path: str | None = None):
    """Cria o clip de background.
    Se USE_PRERENDERED_BG=True e bg_cache_path for passado, usa o video de bg
    pre-renderizado (rapido). Caso contrario, cai no comportamento original."""
    if not USE_BLURRED_BACKGROUND:
        return ColorClip(FINAL_SIZE, color=(0, 0, 0)).with_duration(duration)

    if is_image:
        pil = Image.open(input_path).convert("RGB").resize(FINAL_SIZE)
        pil = pil.filter(ImageFilter.GaussianBlur(radius=BLUR_RADIUS))
        return ImageClip(np.array(pil)).with_duration(duration)

    if USE_PRERENDERED_BG and bg_cache_path and os.path.exists(bg_cache_path):
        bg_clip = VideoFileClip(bg_cache_path).without_audio()
        if bg_clip.duration >= duration:
            return bg_clip.subclipped(0, duration)
        return bg_clip

    # Fallback: blur frame a frame em Python (comportamento original)
    def blur_frame(frame):
        pil = Image.fromarray(frame)
        w, h = pil.size
        new_w = BLUR_DOWNSAMPLE_WIDTH
        new_h = max(1, int(h * new_w / w))
        small = pil.resize((new_w, new_h), Image.Resampling.BILINEAR)
        scale = new_w / w
        radius = max(1, int(BLUR_RADIUS * scale))
        small = small.filter(ImageFilter.GaussianBlur(radius=radius))
        return np.array(small.resize((w, h), Image.Resampling.BILINEAR))

    bg = source_clip.image_transform(blur_frame)
    return bg.resized(FINAL_SIZE)


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


# === Fase 2: pre-render do popup ===

def _popup_frame_at(t: float, popup1_full, popup1_bg, popup2_full, popup2_bg,
                    max_popup_h: int, popup_1_in: float, popup_1_out: float,
                    transition_dur: float, popup_fade_in: float, text_fade_dur: float):
    """REPLICA EXATA da funcao popup_frame, isolada para pre-render."""
    canvas = Image.new("RGBA", (POPUP_W, max_popup_h), (0, 0, 0, 0))

    if t < popup_1_in:
        return np.array(canvas).astype(np.uint8)

    if t < popup_1_in + popup_fade_in:
        p = (t - popup_1_in) / popup_fade_in
        alpha = max(0.0, min(1.0, p))
        canvas.paste(popup1_full, (0, 0), popup1_full)
        arr = np.array(canvas).astype(np.uint8)
        arr[..., 3] = (arr[..., 3].astype(np.float32) * alpha).astype(np.uint8)
        return arr

    text_fade_start = popup_1_out - text_fade_dur
    if t <= text_fade_start:
        canvas.paste(popup1_full, (0, 0), popup1_full)
        return np.array(canvas).astype(np.uint8)

    if t <= popup_1_out:
        p = (t - text_fade_start) / text_fade_dur
        blended = Image.blend(popup1_full, popup1_bg, p)
        canvas.paste(blended, (0, 0), blended)
        return np.array(canvas).astype(np.uint8)

    if t < popup_1_out + transition_dur:
        p = (t - popup_1_out) / transition_dur
        current_h = max(1, int(lerp(popup1_bg.height, popup2_bg.height, p)))
        img1 = popup1_bg.resize((POPUP_W, current_h), Image.Resampling.LANCZOS)
        img2 = popup2_bg.resize((POPUP_W, current_h), Image.Resampling.LANCZOS)
        blended = Image.blend(img1, img2, p)
        canvas.paste(blended, (0, 0), blended)
        return np.array(canvas).astype(np.uint8)

    text_appear_start = popup_1_out + transition_dur
    if t < text_appear_start + text_fade_dur:
        p = (t - text_appear_start) / text_fade_dur
        blended = Image.blend(popup2_bg, popup2_full, p)
        canvas.paste(blended, (0, 0), blended)
        return np.array(canvas).astype(np.uint8)

    canvas.paste(popup2_full, (0, 0), popup2_full)
    return np.array(canvas).astype(np.uint8)


def _prerender_popup(titulo: str, subtitulo: str, duration: float,
                     popup_1_in: float, popup_1_out: float,
                     transition_dur: float, popup_fade_in: float,
                     text_fade_dur: float, output_path: str) -> tuple[str, int]:
    """Pre-renderiza o popup como MOV qtrle alpha usando KEYFRAMES + concat demuxer.

    Em vez de gerar 5400 PNGs (um por frame), gera APENAS os frames
    onde o popup muda visualmente (~107 frames). Frames estáticos são
    representados por 1 PNG com duration longo no concat file.

    O popup tem este timeline (valores default):
      0.0s - 1.5s: Popup1 FADE IN       (45 frames animados)
      1.5s - 6.5s: Popup1 ESTÁTICO       (1 frame, segura 5s)
      6.5s - 7.0s: Text FADE OUT          (15 frames animados)
      7.0s - 8.0s: TRANSIÇÃO popup1→2     (30 frames animados)
      8.0s - 8.5s: Popup2 text FADE IN    (15 frames animados)
      8.5s - 180s: Popup2 ESTÁTICO        (1 frame, segura 171.5s!)

    Resultado: ~107 frames em vez de 5400 (50x menos).
    Arquivo final: 12x menor que o método antigo.
    """
    import shutil

    popup1_full, popup1_bg = build_popup_image(
        titulo, POPUP_W, PADDING_X, PADDING_Y, FONT_SIZE, COR_FUNDO, COR_TEXTO
    )
    popup2_full, popup2_bg = build_popup_image(
        subtitulo, POPUP_W, PADDING_X, PADDING_Y, FONT_SIZE, COR_FUNDO, COR_TEXTO
    )
    max_popup_h = max(popup1_full.height, popup2_full.height)

    # === Passo 1: identificar segmentos (keyframe intervals) ===
    segments = _compute_popup_segments(
        duration, popup_1_in, popup_1_out, transition_dur,
        popup_fade_in, text_fade_dur
    )

    # === Passo 2: gerar PNGs só dos frames únicos + concat file ===
    work_dir = Path(tempfile.mkdtemp(prefix="vpl_kf_"))

    try:
        concat_lines = []
        frame_idx = 0

        for t_start, t_end, is_animated in segments:
            if t_start >= t_end:
                continue

            if is_animated:
                # Gerar cada frame individualmente com duration de 1 frame
                frame_start = int(t_start * FPS)
                frame_end = int(t_end * FPS)
                for fi in range(frame_start, frame_end):
                    t = fi / FPS
                    arr = _popup_frame_at(
                        t, popup1_full, popup1_bg, popup2_full, popup2_bg,
                        max_popup_h, popup_1_in, popup_1_out, transition_dur,
                        popup_fade_in, text_fade_dur
                    )
                    fname = f"f_{frame_idx:04d}.png"
                    Image.fromarray(arr, mode="RGBA").save(work_dir / fname)
                    concat_lines.append(f"file '{fname}'")
                    concat_lines.append(f"duration {1.0 / FPS:.6f}")
                    frame_idx += 1
            else:
                # Frame estático: 1 PNG com duration = t_end - t_start
                seg_duration = t_end - t_start
                t = t_start
                arr = _popup_frame_at(
                    t, popup1_full, popup1_bg, popup2_full, popup2_bg,
                    max_popup_h, popup_1_in, popup_1_out, transition_dur,
                    popup_fade_in, text_fade_dur
                )
                fname = f"f_{frame_idx:04d}.png"
                Image.fromarray(arr, mode="RGBA").save(work_dir / fname)
                concat_lines.append(f"file '{fname}'")
                concat_lines.append(f"duration {seg_duration:.6f}")
                frame_idx += 1

        # Última linha: repetir o último frame (FFmpeg concat exige)
        concat_lines.append(f"file 'f_{frame_idx - 1:04d}.png'")

        # Escrever concat file
        concat_path = work_dir / "concat.txt"
        concat_path.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")

        # === Passo 3: FFmpeg concat demuxer → MOV qtrle ===
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_path),
            "-c:v", POPUP_PRERENDER_CODEC,
            "-pix_fmt", POPUP_PRERENDER_PIXFMT,
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg concat popup falhou: {result.stderr[-500:]}")

        return output_path, max_popup_h

    except Exception as e:
        # Fallback: se o concat falhou, tentar o método antigo (5400 PNGs)
        print(f"WARN: popup keyframe falhou ({e}), tentando fallback PNG...")
        try:
            return _prerender_popup_fallback_png(
                titulo, subtitulo, duration,
                popup_1_in, popup_1_out, transition_dur,
                popup_fade_in, text_fade_dur, output_path,
                popup1_full, popup1_bg, popup2_full, popup2_bg, max_popup_h
            )
        except:
            raise e
    finally:
        try:
            shutil.rmtree(work_dir)
        except Exception:
            pass


def _compute_popup_segments(duration: float, popup_1_in: float, popup_1_out: float,
                            transition_dur: float, popup_fade_in: float,
                            text_fade_dur: float) -> list[tuple[float, float, bool]]:
    """Computa os segmentos do popup: (t_start, t_end, is_animated).

    Segmentos animados são onde o visual muda frame a frame (fade, transition).
    Segmentos estáticos são onde o popup fica idêntico por N frames.
    """
    text_fade_start = popup_1_out - text_fade_dur
    transition_end = popup_1_out + transition_dur
    text2_fade_end = transition_end + text_fade_dur

    segments = []

    # Segmento 1: Antes do popup (vazio/transparente)
    if popup_1_in > 0:
        segments.append((0.0, popup_1_in, False))

    # Segmento 2: Fade in do popup1 (animado)
    segments.append((popup_1_in, popup_1_in + popup_fade_in, True))

    # Segmento 3: Popup1 estático com texto
    segments.append((popup_1_in + popup_fade_in, text_fade_start, False))

    # Segmento 4: Fade out do texto (animado)
    if text_fade_dur > 0:
        segments.append((text_fade_start, popup_1_out, True))

    # Segmento 5: Transição popup1 → popup2 (animado)
    if transition_dur > 0:
        segments.append((popup_1_out, transition_end, True))

    # Segmento 6: Fade in do texto do popup2 (animado)
    if text_fade_dur > 0:
        segments.append((transition_end, text2_fade_end, True))

    # Segmento 7: Popup2 estático (o MAIOR segmento — 95% do vídeo!)
    segments.append((text2_fade_end, duration, False))

    return segments


def _prerender_popup_fallback_png(titulo: str, subtitulo: str, duration: float,
                                   popup_1_in: float, popup_1_out: float,
                                   transition_dur: float, popup_fade_in: float,
                                   text_fade_dur: float, output_path: str,
                                   popup1_full, popup1_bg, popup2_full, popup2_bg,
                                   max_popup_h: int) -> tuple[str, int]:
    """Fallback: método antigo com PNGs em disco (caso o pipe falhe)."""
    import tempfile
    import shutil
    frames_dir = tempfile.mkdtemp(prefix="vpl_popup_frames_")

    try:
        n_frames = int(duration * FPS)
        for i in range(n_frames):
            t = i / FPS
            arr = _popup_frame_at(
                t, popup1_full, popup1_bg, popup2_full, popup2_bg,
                max_popup_h, popup_1_in, popup_1_out, transition_dur,
                popup_fade_in, text_fade_dur
            )
            Image.fromarray(arr, mode="RGBA").save(
                Path(frames_dir) / f"frame_{i:05d}.png"
            )

        cmd = [
            "ffmpeg", "-y",
            "-framerate", str(FPS),
            "-i", str(Path(frames_dir) / "frame_%05d.png"),
            "-c:v", POPUP_PRERENDER_CODEC,
            "-pix_fmt", POPUP_PRERENDER_PIXFMT,
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg prerender popup falhou: {result.stderr[-500:]}")

        return output_path, max_popup_h
    finally:
        try:
            shutil.rmtree(frames_dir)
        except Exception:
            pass


# === Fase 3: composite final via ffmpeg direto ===

def _composite_with_ffmpeg(video_path: str, bg_path: str, popup_path: str | None,
                            output_path: str, duration: float,
                            on_render_progress: Callable | None = None) -> str:
    """Composite final via ffmpeg filter_complex (Fase 3)."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height",
         "-of", "default=noprint_wrappers=1", video_path],
        capture_output=True, text=True, timeout=30
    )
    src_w = src_h = 0
    for line in probe.stdout.strip().split("\n"):
        if line.startswith("width="):
            src_w = int(line.split("=")[1])
        elif line.startswith("height="):
            src_h = int(line.split("=")[1])

    if src_w == 0 or src_h == 0:
        raise RuntimeError(f"nao consegui ler dimensoes de {video_path}")

    scale_factor = VIDEO_W / src_w
    new_w = int(src_w * scale_factor)
    new_h_after_scale = int(src_h * scale_factor)
    y_crop = 0
    final_h = new_h_after_scale
    if new_h_after_scale > VIDEO_H:
        y_crop = (new_h_after_scale - VIDEO_H) // 2
        final_h = VIDEO_H

    if popup_path:
        filter_complex = (
            f"[0:v]scale={new_w}:{new_h_after_scale}:flags=lanczos,"
            f"crop={VIDEO_W}:{final_h}:0:{y_crop},setsar=1:1[scaled];"
            f"[1:v][scaled]overlay={VIDEO_X}:{VIDEO_Y}[bg+vid];"
            f"[bg+vid][2:v]overlay={POPUP_X}:{POPUP_Y}[out]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", bg_path,
            "-i", popup_path,
            "-t", str(duration),
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-map", "0:a?",
        ]
    else:
        filter_complex = (
            f"[0:v]scale={new_w}:{new_h_after_scale}:flags=lanczos,"
            f"crop={VIDEO_W}:{final_h}:0:{y_crop},setsar=1:1[scaled];"
            f"[1:v][scaled]overlay={VIDEO_X}:{VIDEO_Y}[out]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", bg_path,
            "-t", str(duration),
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-map", "0:a?",
        ]

    cmd += [
        "-r", str(FPS),
        "-c:v", "libx264",
        "-preset", ENCODER_PRESET,
        "-crf", str(VIDEO_QUALITY),
        "-threads", _ffmpeg_threads(),  # Limitado em renders paralelos
        "-pix_fmt", "yuv420p",
        "-movflags", "faststart",
        "-c:a", "aac",
        "-shortest",
        "-progress", "pipe:2",  # Escrever progresso em stderr
        output_path,
    ]

    # Usar Popen para capturar progresso em tempo real via stderr
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Monitorar progresso via stderr (FFmpeg -progress escreve "out_time_ms=NNN")
    last_reported_pct = -1

    def _try_parse_progress(text):
        """Tenta extrair o tempo processado do texto stderr."""
        nonlocal last_reported_pct
        if not on_render_progress:
            return
        # -progress mode escreve: out_time_ms=12345678 (microseconds)
        for key in ("out_time_ms=", "out_time="):
            idx = text.rfind(key)
            if idx >= 0:
                try:
                    val_str = text[idx + len(key):].split("\n")[0].strip()
                    if key == "out_time_ms=":
                        current_time = int(val_str) / 1_000_000  # microseconds -> seconds
                    else:
                        # out_time=HH:MM:SS.UUUUUU
                        parts = val_str.split(":")
                        current_time = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
                    pct = min(100, int(current_time / duration * 100))
                    if pct != last_reported_pct and pct >= 0:
                        last_reported_pct = pct
                        on_render_progress(pct)
                except (ValueError, IndexError):
                    pass
                break

    # DEBUG INSTRUMENTATION (agente4.md, ponto cego #4):
    # Deadline de 300s sem log intermediário — se FFmpeg travar, desperdiça
    # 300s silenciosamente antes de falhar e cair no fallback MoviePy.
    # Agora: log de heartbeat a cada 15s com % do deadline, % do output,
    # e detecção de "stderr silencioso" (FFmpeg parou de emitir progresso).
    try:
        # Timeout total para o processo
        deadline = time_module.time() + 300  # 5 min max
        composite_start = time_module.time()
        last_heartbeat_log = time_module.time()
        last_stderr_chunk_time = time_module.time()
        silent_iterations = 0
        print(f"[render] [composite] START deadline=300s duration={duration:.1f}s "
              f"cmd_filter_complex={filter_complex[:80]}...", flush=True)
        while process.poll() is None and time_module.time() < deadline:
            # Ler stderr disponível sem bloquear
            try:
                chunk = process.stderr.read1(4096) if hasattr(process.stderr, 'read1') else b""
                if chunk:
                    text = chunk.decode("utf-8", errors="replace")
                    last_stderr_chunk_time = time_module.time()
                    silent_iterations = 0
                    if "out_time" in text:
                        _try_parse_progress(text)
                else:
                    silent_iterations += 1
                    time_module.sleep(0.5)
            except Exception:
                silent_iterations += 1
                time_module.sleep(0.5)

            # Heartbeat a cada 15s: mostra tempo decorrido, % do deadline,
            # % do output, e se stderr está silencioso há muito tempo
            now = time_module.time()
            if now - last_heartbeat_log >= 15:
                elapsed_total = now - composite_start
                deadline_pct = (elapsed_total / 300.0) * 100
                output_pct = last_reported_pct if last_reported_pct >= 0 else -1
                silence_sec = now - last_stderr_chunk_time
                print(f"[render] [composite] HEARTBEAT elapsed={elapsed_total:.0f}s "
                      f"deadline={deadline_pct:.0f}% output_pct={output_pct}% "
                      f"silent_for={silence_sec:.1f}s iter={silent_iterations}", flush=True)
                # Anomalia: stderr silencioso por mais de 30s — FFmpeg pode ter travado
                if silence_sec > 30:
                    print(f"[render] [composite] ANOMALIA stderr silencioso por {silence_sec:.1f}s "
                          f"— FFmpeg pode estar travado ou processando sem emitir progresso", flush=True)
                # Anomalia: já consumiu 75% do deadline mas output < 50%
                if deadline_pct > 75 and output_pct >= 0 and output_pct < 50:
                    print(f"[render] [composite] ANOMALIA deadline {deadline_pct:.0f}% consumido "
                          f"mas output só {output_pct}% — vai estourar timeout 300s", flush=True)
                last_heartbeat_log = now

        # Se ainda rodando, matar
        final_elapsed = time_module.time() - composite_start
        if process.poll() is None:
            print(f"[render] [composite] TIMEOUT após {final_elapsed:.0f}s — matando FFmpeg "
                  f"(output_pct={last_reported_pct}%)", flush=True)
            process.kill()
            process.wait(timeout=10)
            raise RuntimeError(f"ffmpeg composite timeout (300s) — last output_pct={last_reported_pct}%")
        else:
            print(f"[render] [composite] DONE em {final_elapsed:.1f}s "
                  f"returncode={process.returncode} output_pct={last_reported_pct}%", flush=True)
            if final_elapsed > 180:
                print(f"[render] [composite] ANOMALIA composite demorou {final_elapsed:.1f}s "
                      f"— CPU bound, considerar reduzir FFMPEG_THREADS_PER_RENDER", flush=True)

    except Exception as e:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        raise

    # Ler stderr restante
    remaining_err = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    if process.returncode != 0:
        raise RuntimeError(f"ffmpeg composite falhou: {remaining_err[-500:]}")

    return output_path


class _RenderProgressLogger(proglog.ProgressBarLogger):
    def __init__(self, callback):
        super().__init__()
        self._on_progress = callback
        self._total = 0
        self._last_pct = -1

    def bars_callback(self, bar, attr, value, old_value):
        if attr == "total":
            self._total = value
        if attr == "index":
            total = self.bars[bar].get("total", self._total)
            if total:
                pct = int(value / total * 100)
                if pct != self._last_pct:
                    self._last_pct = pct
                    self._on_progress(pct)


def criar_video(
    input_path: str,
    output_dir: str = "editado",
    titulo: str = "Título",
    subtitulo: str = "Subtítulo",
    popup_1_in: float = 0.0,
    popup_1_out: float = 7.0,
    transition_dur: float = 1.0,
    popup_fade_in: float = 1.5,
    text_fade_dur: float = 0.5,
    test_duration: float | None = None,
    on_render_progress: Callable | None = None,
) -> str:
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Arquivo de entrada não encontrado: {input_path}")

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    safe_title = "".join(c if c.isalnum() or c in " _-" else "_" for c in titulo).strip()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_name = f"{safe_title[:40]}_{timestamp}.mp4"
    output_path = str(Path(output_dir) / output_name)

    source_clip, source_duration, is_image = load_source_clip(input_path)
    duration = test_duration if test_duration is not None else min(source_duration, MAX_DURATION)

    if is_image:
        source_clip = source_clip.with_duration(duration)
    else:
        source_clip = source_clip.subclipped(0, duration)
        duration = source_clip.duration

    # === Fase 1+2: pre-render BG blur + popup em PARALELO ===
    # BG pre-render precisa: input_path, duration
    # Popup pre-render precisa: titulo, subtitulo, duration, timings
    # São INDEPENDENTES — podem rodar ao mesmo tempo!
    bg_cache_path = None
    popup_cache_path = None
    popup_prerendered_ok = False

    # Preparar paths dos caches
    if USE_PRERENDERED_BG and not is_image:
        input_hash = hashlib.md5(f"{input_path}_{duration:.2f}".encode()).hexdigest()[:12]
        bg_cache_path = str(Path(tempfile.gettempdir()) / f"vpl_bg_{input_hash}.mp4")
    if USE_PRERENDERED_POPUP:
        popup_hash = hashlib.md5(
            f"{titulo}|{subtitulo}|{duration:.2f}".encode()
        ).hexdigest()[:12]
        popup_cache_path = str(Path(tempfile.gettempdir()) / f"vpl_popup_{popup_hash}.mov")

    # Executar ambos em paralelo com ThreadPoolExecutor
    if bg_cache_path and popup_cache_path:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _do_bg():
            try:
                _prerender_background(input_path, duration, bg_cache_path)
                return ('bg', True, None)
            except Exception as e:
                return ('bg', False, e)

        def _do_popup():
            try:
                _prerender_popup(
                    titulo, subtitulo, duration,
                    popup_1_in, popup_1_out, transition_dur,
                    popup_fade_in, text_fade_dur, popup_cache_path
                )
                return ('popup', True, None)
            except Exception as e:
                return ('popup', False, e)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(_do_bg), executor.submit(_do_popup)]
            for future in as_completed(futures):
                which, ok, err = future.result()
                if which == 'bg' and not ok:
                    print(f"WARN: pre-render bg falhou ({err}), usando fallback Python")
                    bg_cache_path = None
                elif which == 'popup' and not ok:
                    print(f"WARN: pre-render popup falhou ({err}), usando fallback Python")
                    popup_cache_path = None

        if popup_cache_path and os.path.exists(popup_cache_path):
            popup_prerendered_ok = True
    else:
        # Fallback: sequencial (se um dos dois estiver desabilitado)
        if bg_cache_path:
            try:
                _prerender_background(input_path, duration, bg_cache_path)
            except Exception as e:
                print(f"WARN: pre-render bg falhou ({e}), usando fallback Python")
                bg_cache_path = None
        if popup_cache_path:
            try:
                _prerender_popup(
                    titulo, subtitulo, duration,
                    popup_1_in, popup_1_out, transition_dur,
                    popup_fade_in, text_fade_dur, popup_cache_path
                )
                popup_prerendered_ok = True
            except Exception as e:
                print(f"WARN: pre-render popup falhou ({e}), usando fallback Python")
                popup_cache_path = None

    background = make_background(source_clip, duration, is_image, input_path,
                                  bg_cache_path=bg_cache_path)

    src_w, src_h = source_clip.w, source_clip.h
    scale = VIDEO_W / src_w
    new_w, new_h = int(src_w * scale), int(src_h * scale)
    main_video = source_clip.resized((new_w, new_h))
    if new_h > VIDEO_H:
        y_crop = (new_h - VIDEO_H) // 2
        main_video = main_video.cropped(y1=y_crop, height=VIDEO_H)
        new_h = VIDEO_H
    x_off = VIDEO_X + (VIDEO_W - new_w) // 2
    y_off = VIDEO_Y + (VIDEO_H - new_h) // 2
    main_video = main_video.with_position((x_off, y_off))

    # Popup: usar pre-renderizado ou fallback
    if popup_prerendered_ok and popup_cache_path and os.path.exists(popup_cache_path):
        try:
            popup = VideoFileClip(popup_cache_path, has_mask=True)
            popup = popup.with_position((POPUP_X, POPUP_Y))
        except Exception as e:
            print(f"WARN: carregar popup pre-renderizado falhou ({e}), usando fallback Python")
            popup_prerendered_ok = False

    # Fallback: popup calculado frame a frame em Python (comportamento original)
    if not popup_prerendered_ok:
        popup1_full, popup1_bg = build_popup_image(
            titulo, POPUP_W, PADDING_X, PADDING_Y, FONT_SIZE, COR_FUNDO, COR_TEXTO
        )
        popup2_full, popup2_bg = build_popup_image(
            subtitulo, POPUP_W, PADDING_X, PADDING_Y, FONT_SIZE, COR_FUNDO, COR_TEXTO
        )
        max_popup_h = max(popup1_full.height, popup2_full.height)

        def popup_frame(t: float):
            return _popup_frame_at(
                t, popup1_full, popup1_bg, popup2_full, popup2_bg,
                max_popup_h, popup_1_in, popup_1_out, transition_dur,
                popup_fade_in, text_fade_dur
            )

        popup = VideoClip(frame_function=popup_frame, duration=duration)
        popup = popup.with_position((POPUP_X, POPUP_Y))

    # === Fase 3: composite final via ffmpeg direto ===
    composite_ffmpeg_ok = False
    if (USE_FFMPEG_COMPOSITE and bg_cache_path and os.path.exists(bg_cache_path)
            and popup_prerendered_ok and popup_cache_path and os.path.exists(popup_cache_path)):
        try:
            _composite_with_ffmpeg(
                video_path=input_path,
                bg_path=bg_cache_path,
                popup_path=popup_cache_path,
                output_path=output_path,
                duration=duration,
                on_render_progress=on_render_progress,
            )
            composite_ffmpeg_ok = True
        except Exception as e:
            print(f"WARN: composite ffmpeg falhou ({e}), usando fallback MoviePy")
            composite_ffmpeg_ok = False

    # Fallback: composite via MoviePy (comportamento original das Fases 1+2)
    if not composite_ffmpeg_ok:
        final = CompositeVideoClip(
            [background, main_video, popup],
            size=FINAL_SIZE,
        ).with_duration(duration)

        render_logger = _RenderProgressLogger(on_render_progress) if on_render_progress else None
        final.write_videofile(
            output_path,
            fps=FPS,
            codec="libx264",
            preset=ENCODER_PRESET,
            audio_codec="aac",
            threads=int(_ffmpeg_threads()) if _ffmpeg_threads() != "0" else 4,
            logger=render_logger,
            ffmpeg_params=["-pix_fmt", "yuv420p", "-movflags", "faststart", "-crf", str(VIDEO_QUALITY), "-vf", "setsar=1:1"],
        )

    # === Fase 1 + 2: cleanup dos caches temporarios ===
    try:
        if popup is not None:
            popup.close()
    except Exception:
        pass
    try:
        if background is not None:
            background.close()
    except Exception:
        pass
    try:
        if main_video is not None:
            main_video.close()
    except Exception:
        pass
    try:
        if source_clip is not None:
            source_clip.close()
    except Exception:
        pass
    try:
        if 'final' in locals() and final is not None:
            final.close()
    except Exception:
        pass

    import time as _time
    for cache_path in (bg_cache_path, popup_cache_path):
        if cache_path and os.path.exists(cache_path):
            for attempt in range(5):
                try:
                    os.unlink(cache_path)
                    break
                except Exception:
                    _time.sleep(0.1)

    return output_path


def main():
    import sys as _sys
    upload_dir = Path(__file__).parent / "upload"
    videos = sorted(upload_dir.glob("*.mp4"))
    if not videos:
        print("Nenhum vídeo encontrado em upload/")
        _sys.exit(1)

    input_path = str(videos[0])
    output_path = criar_video(
        input_path=input_path,
        titulo="Título de Teste",
        subtitulo="Subtítulo de teste para o vídeo",
        test_duration=10,
    )
    print(f"Concluído: {output_path}")


if __name__ == "__main__":
    main()
