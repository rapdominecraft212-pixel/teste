import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


LEVELS = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}


# === Catálogo central de limiares de anomalia (agente6.md, seção 6) ===
# Usado por log.anomalia() e por código que prefere comparar manualmente.
# Valores calibrados para i3-2120 (2c/4t, 8GB RAM) — alvo: 2min por vídeo.
ANOMALIA_POOL_ACQUIRE = 5.0          # worker.py: acquire de conta do pool
ANOMALIA_QWEN_UPLOAD_VIDEO = 30.0    # simple.py/qwen: upload de vídeo mp4
ANOMALIA_QWEN_UPLOAD_JPG = 15.0      # simple.py/qwen: upload de grid JPG
ANOMALIA_QWEN_ENVIO = 20.0           # qwen: prompt enviado + stop-button aparecer
ANOMALIA_QWEN_GERACAO_CAPA = 60.0    # qwen: inferência capa+titulo
ANOMALIA_QWEN_GERACAO_LINHA = 45.0   # qwen: inferência linha
ANOMALIA_CORTE_VIDEO = 30.0          # cortar_video.py: FFmpeg crop+encode
ANOMALIA_CORTE_VIDEO_CRITICO = 100.0  # cortar_video.py: próximo do timeout 120s
ANOMALIA_PRERENDER_BG = 30.0         # video_popup_linear.py: Fase 1
ANOMALIA_PRERENDER_POPUP = 20.0      # video_popup_linear.py: Fase 2
ANOMALIA_COMPOSITE = 90.0            # video_popup_linear.py: Fase 3
ANOMALIA_COMPOSITE_CRITICO = 180.0   # video_popup_linear.py: perto do timeout 300s
ANOMALIA_RENDER_TOTAL = 90.0         # simple.py: renderizar_video total
ANOMALIA_PREP_TOTAL = 120.0          # simple.py: preparar_video total
ANOMALIA_BACKOFF_MAX_READY = 10.0    # worker.py: esteira A pausada esperando B
ANOMALIA_DOWNLOAD = 30.0             # worker.py: baixar_video
ANOMALIA_TELEGRAM_SEND = 3.0         # worker.py: send_telegram_message
ANOMALIA_FFMPEG_STALL = 30.0         # video_popup_linear.py: stderr silencioso
ANOMALIA_JOB_TOTAL = 180.0           # alvo: 120s, anomalia se > 180s


class Logger:
    def __init__(self, name: str = "", min_level: str = "DEBUG", log_file: str | None = None):
        self.name = name
        self.min_level = LEVELS.get(min_level, 1)
        self._timers: dict[str, float] = {}
        self._log_file = Path(log_file) if log_file else None
        if self._log_file:
            self._log_file.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, level: str, msg: str):
        if LEVELS.get(level, 0) < self.min_level:
            return
        ts = datetime.now().strftime("%H:%M:%S")
        prefix = f"[{ts}]" if not self.name else f"[{ts}][{self.name}]"
        line = f"{prefix} [{level}] {msg}"
        # flush=True garante que apareça imediatamente no executar_terminal.bat
        print(line, file=sys.stderr if level in ("ERROR", "WARN") else sys.stdout, flush=True)
        if self._log_file:
            with open(self._log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def debug(self, msg: str):
        self._write("DEBUG", msg)

    def info(self, msg: str):
        self._write("INFO", msg)

    def warn(self, msg: str):
        self._write("WARN", msg)

    # Alias para warning() — compatibilidade com biblioteca logging do Python.
    # O pool (qwen_account_pool.py) e possivelmente outros modulos usam
    # log.warning() em vez de log.warn(). Sem este alias, qualquer chamada
    # dispara AttributeError: 'Logger' object has no attribute 'warning'
    # que e engolido pelo keep-alive loop (vide log 28/06 20:26:41).
    def warning(self, msg: str):
        self._write("WARN", msg)

    def error(self, msg: str):
        self._write("ERROR", msg)

    def start_timer(self, label: str):
        self._timers[label] = time.perf_counter()

    def elapsed(self, label: str, fmt: bool = True) -> float | str:
        secs = time.perf_counter() - self._timers.get(label, 0)
        if fmt:
            if secs < 60:
                return f"{secs:.1f}s"
            return f"{secs//60:.0f}m {secs%60:.0f}s"
        return secs

    def timer_info(self, label: str) -> str:
        return f"[{label} {self.elapsed(label)}]"

    # === DEBUG INSTRUMENTATION (agente6.md, extensão 1) ===
    # Helper central para anomaly detectors — padroniza o padrão
    # 'if dt > X: log.warn("ANOMALIA ...")' espalhado pelos outros 5 arquivos.
    def anomalia(self, nome: str, dt: float, limiar: float,
                 contexto: dict | None = None, job_id: str | None = None) -> bool:
        """Verifica se dt excede limiar e loga adequadamente.

        Retorna True se anomalia detectada (dt > limiar), False caso contrário.
        - Se exceder: loga WARN com prefixo 'ANOMALIA:' (vai p/ stderr, destaca-se).
        - Se não exceder: loga DEBUG silencioso (invisível em produção).

        Uso:
            log.anomalia("qwen_upload", dt_upload, ANOMALIA_QWEN_UPLOAD_VIDEO,
                         job_id=job_id, contexto={"size_mb": 15.2})
        """
        ctx_str = ""
        if job_id:
            ctx_str += f" job={job_id[:12]}"
        if contexto:
            for k, v in contexto.items():
                if isinstance(v, float):
                    ctx_str += f" {k}={v:.2f}"
                else:
                    ctx_str += f" {k}={v}"

        if dt > limiar:
            self.warn(f"ANOMALIA: [{nome}] dt={dt:.2f}s limiar={limiar:.1f}s{ctx_str}")
            return True
        else:
            self.debug(f"ok: [{nome}] dt={dt:.2f}s limiar={limiar:.1f}s{ctx_str}")
            return False

    # === DEBUG INSTRUMENTATION (agente6.md, extensão 2) ===
    # Context manager que mede dt automaticamente e dispara anomalia() ao sair.
    # Elimina a repetição start_timer → elapsed → info em dezenas de pontos.
    @contextmanager
    def etapa(self, nome: str, limiar: float | None = None,
              job_id: str | None = None, contexto: dict | None = None):
        """Context manager para medir etapa com timer + anomaly detector.

        Uso:
            with log.etapa("qwen_upload", ANOMALIA_QWEN_UPLOAD_VIDEO, job_id=job_id):
                await qwen_enviar_video(video_path)
        """
        t0 = time.perf_counter()
        self.info(f"[{nome}] START{f' job={job_id[:12]}' if job_id else ''}")
        try:
            yield
        except Exception as e:
            dt = time.perf_counter() - t0
            self.error(f"[{nome}] FAIL dt={dt:.2f}s{f' job={job_id[:12]}' if job_id else ''} "
                       f"exc={type(e).__name__}: {e}")
            raise
        else:
            dt = time.perf_counter() - t0
            if limiar is not None:
                if self.anomalia(nome, dt, limiar, contexto, job_id):
                    pass  # anomalia() já logou WARN
                else:
                    self.info(f"[{nome}] OK dt={dt:.2f}s{f' job={job_id[:12]}' if job_id else ''}")
            else:
                self.info(f"[{nome}] OK dt={dt:.2f}s{f' job={job_id[:12]}' if job_id else ''}")


log = Logger("editor")

LOG_FILE = Path(__file__).resolve().parent.parent / "logs" / "editor.log"
file_log = Logger("editor", log_file=str(LOG_FILE))


def truncar_erro(erro: str, max_chars: int = 1500) -> str:
    erro = erro.strip()
    if len(erro) <= max_chars:
        return erro
    metade = max_chars // 2
    omitidos = len(erro) - max_chars
    return f"{erro[:metade]}\n\n... [{omitidos} chars omitidos no meio] ...\n\n{erro[-metade:]}"


def truncar_stderr(stderr: str, head: int = 500, tail: int = 500) -> str:
    stderr = stderr.strip()
    if len(stderr) <= (head + tail):
        return stderr
    omitidos = len(stderr) - head - tail
    return f"{stderr[:head]}\n\n... [{omitidos} chars de frames omitidos] ...\n\n{stderr[-tail:]}"
