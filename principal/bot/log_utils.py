import sys
import time
from datetime import datetime
from pathlib import Path


LEVELS = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}


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
        print(line, file=sys.stderr if level in ("ERROR", "WARN") else sys.stdout)
        if self._log_file:
            with open(self._log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def debug(self, msg: str):
        self._write("DEBUG", msg)

    def info(self, msg: str):
        self._write("INFO", msg)

    def warn(self, msg: str):
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
