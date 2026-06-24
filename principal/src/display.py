import time
import threading
from rich.live import Live
from rich.text import Text


class Display:
    def __init__(self, console, start_time=None):
        self.console = console
        self.start_time = start_time or time.monotonic()
        self.total_tasks = 7
        self.completed_tasks = 0
        self.current_log = ""
        self.file_current = 0
        self.file_total = 0
        self._manual_pct = None
        self._running = False
        self.live = Live(self._render(), console=console, refresh_per_second=10, transient=True)

    def __enter__(self):
        self.live.start()
        self._running = True
        threading.Thread(target=self._timer_loop, daemon=True).start()
        return self

    def __exit__(self, *args):
        self._running = False
        self.live.stop()

    def _timer_loop(self):
        while self._running:
            self.live.update(self._render())
            time.sleep(1)

    def set_file(self, current, total):
        self.file_current = current
        self.file_total = total
        self.live.update(self._render())

    def set_log(self, msg):
        self.current_log = msg.splitlines()[0][:self.console.width]
        self.live.update(self._render())

    def set_pct(self, pct):
        self._manual_pct = pct
        self.live.update(self._render())

    def advance(self, n=1):
        self.completed_tasks = min(self.completed_tasks + n, self.total_tasks)
        self._manual_pct = None
        self.live.update(self._render())

    def reset_tasks(self):
        self.completed_tasks = 0
        self._manual_pct = None
        self.live.update(self._render())

    def _get_pct(self):
        if self._manual_pct is not None:
            base = (self.completed_tasks / self.total_tasks) * 100
            task_share = self._manual_pct / 100 * (100 / self.total_tasks)
            return base + task_share
        return (self.completed_tasks / self.total_tasks) * 100

    def _get_timer(self):
        elapsed = time.monotonic() - self.start_time
        m, s = divmod(int(elapsed), 60)
        return f"{m:02d}:{s:02d}"

    def _bar_width(self):
        return max(10, self.console.width - 16)

    def _render(self):
        pct = self._get_pct()
        timer = self._get_timer()
        w = self._bar_width()
        filled = int(pct / 100 * w)
        bar = "█" * filled + "░" * (w - filled)

        file_str = ""
        if self.file_total > 0:
            file_str = f"{self.file_current}/{self.file_total} "
        line1 = file_str + self.current_log
        line2 = f"{pct:>3.0f}% {bar} {timer}"
        pad1 = max(0, (self.console.width - len(line1)) // 2)
        pad2 = max(0, (self.console.width - len(line2)) // 2)

        t = Text()
        t.append(" " * pad1)
        t.append(line1, style="white")
        t.append("\n")
        t.append(" " * pad2)
        t.append(line2, style="orange3")
        return t
