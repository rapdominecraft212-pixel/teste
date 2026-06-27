import sys
import os
import re
import time
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


def _extrair_metricas_ffmpeg(stderr: str) -> dict:
    """Extrai metricas do stderr do FFmpeg (speed=, frame=, time=, fps=).

    DEBUG INSTRUMENTATION (agente5.md, ponto cego #2):
    Antes: stderr era capturado mas descartado em sucesso.
    Agora: extrai 'speed=2.5x' e similares para correlacionar com tempo total.
    """
    metricas = {}
    if not stderr:
        return metricas
    # FFmpeg escreve linhas tipo: "frame= 5400 fps= 30 q=24.0 size=   51200kB time=00:03:00.00 bitrate=2333.3kbits/s speed=2.5x"
    # Pegar o ULTIMO valor de cada metrica (estado final)
    for key in ("speed", "fps", "frame", "bitrate"):
        # Padrão: key=Númerox (speed) ou key=Número (outros)
        padrao = rf"{key}=\s*([\d.]+)" + (r"x" if key == "speed" else r"")
        matches = re.findall(padrao, stderr)
        if matches:
            try:
                metricas[key] = float(matches[-1])
            except ValueError:
                pass
    # time=HH:MM:SS.UU — converter para segundos
    time_matches = re.findall(r"time=(\d+):(\d+):(\d+\.\d+)", stderr)
    if time_matches:
        h, m, s = time_matches[-1]
        try:
            metricas["time_sec"] = int(h) * 3600 + int(m) * 60 + float(s)
        except ValueError:
            pass
    return metricas


def cortar_video(input_path: str, y1: int, y2: int, output_dir: str = "cortado") -> str:
    """
    DEBUG INSTRUMENTATION (agente5.md):
    - Adicionado timing total da funcao (start_timer + elapsed)
    - Adicionado log de clamp silencioso de y1/y2 (ponto cego #3)
    - Captura e loga metricas do stderr do FFmpeg (speed=, fps=, time=)
    - Anomaly detectors: speed < 1.0x (mais lento que realtime),
      dt > 30s, clamp ativado que vira no-op
    """
    t_start = time.perf_counter()
    input_name = os.path.basename(input_path)
    input_size_mb = os.path.getsize(input_path) / (1024 * 1024) if os.path.exists(input_path) else 0
    log.info(f"[cortar] [start] input={input_name} size={input_size_mb:.1f}MB y1={y1} y2={y2}")

    result = None
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

        # Probe video dimensions — timer individual
        t_probe = time.perf_counter()
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "default=noprint_wrappers=1", input_path],
            capture_output=True, text=True, timeout=30
        )
        dt_probe = time.perf_counter() - t_probe
        if result.returncode != 0:
            raise RuntimeError(f"ffprobe falhou: {result.stderr.strip()}")
        if dt_probe > 5:
            log.warn(f"[cortar] [probe] ANOMALIA ffprobe demorou {dt_probe:.2f}s — disco lento?")

        info = result.stdout.strip().split("\n")
        H = 0
        W = 0
        for line in info:
            if line.startswith("height="):
                H = int(line.split("=")[1])
            elif line.startswith("width="):
                W = int(line.split("=")[1])

        if H == 0:
            raise RuntimeError("Nao foi possivel ler a altura do video via ffprobe")

        # Clamp silencioso de y1/y2 — agora logado (ponto cego #3)
        y1_orig, y2_orig = y1, y2
        clamp_ocorreu = False
        if y1 < 0:
            y1 = 0
            clamp_ocorreu = True
        if y2 > H:
            y2 = H
            clamp_ocorreu = True
        if clamp_ocorreu:
            log.warn(f"[cortar] [clamp] ANOMALIA y1={y1_orig}->{y1} y2={y2_orig}->{y2} "
                     f"(H={H}) — coordenadas Qwen fora do range, corte truncado")
            # Detectar no-op: se y1=0 e y2=H, o crop nao faz nada mas re-encodeia tudo
            if y1 == 0 and y2 == H:
                log.warn(f"[cortar] [clamp] ANOMALIA CRITICA corte virou NO-OP "
                         f"(y1=0, y2=H=altura original) — re-encode completo sem beneficio visual")

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
            "-threads", "2",  # Limitado para renders paralelos
            "-c:a", "copy",
            output_path,
        ]

        log.info(f"[cortar] [ffmpeg] START {W}x{H}->{W}x{new_h} "
                 f"(crop y={y1}-{y2}) cmd={' '.join(cmd[:6])}...")

        # FFmpeg com timer + captura de stderr para metricas
        t_ffmpeg = time.perf_counter()
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        dt_ffmpeg = time.perf_counter() - t_ffmpeg

        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg codigo {result.returncode}: {truncar_stderr(result.stderr)}")

        # Extrair metricas do stderr (ponto cego #2 corrigido)
        metricas = _extrair_metricas_ffmpeg(result.stderr)
        output_size_mb = os.path.getsize(output_path) / (1024 * 1024) if os.path.exists(output_path) else 0

        log.info(f"[cortar] [ffmpeg] DONE dt={dt_ffmpeg:.2f}s "
                 f"speed={metricas.get('speed', '?')}x "
                 f"fps={metricas.get('fps', '?')} "
                 f"time={metricas.get('time_sec', '?')}s "
                 f"in={input_size_mb:.1f}MB out={output_size_mb:.1f}MB")

        # Anomaly detectors
        speed = metricas.get("speed")
        if speed is not None and speed < 1.0:
            log.warn(f"[cortar] [ffmpeg] ANOMALIA speed={speed}x < 1.0x "
                     f"(mais lento que realtime) — CPU bound em i3-2120? "
                     f"considere -preset ultrafast ou -threads 4")
        if dt_ffmpeg > 30:
            log.warn(f"[cortar] [ffmpeg] ANOMALIA corte demorou {dt_ffmpeg:.2f}s "
                     f"para video de {input_size_mb:.1f}MB — acima do esperado (30s)")
        if dt_ffmpeg > 100:
            log.warn(f"[cortar] [ffmpeg] ANOMALIA CRITICA dt={dt_ffmpeg:.2f}s "
                     f"proximo do timeout 120s — reduzir paralelismo ou video muito longo")

        dt_total = time.perf_counter() - t_start
        log.info(f"[cortar] [done] dt_total={dt_total:.2f}s "
                 f"breakdown=probe:{dt_probe:.2f}s,ffmpeg:{dt_ffmpeg:.2f}s "
                 f"output={output_path}")
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
