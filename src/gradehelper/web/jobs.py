"""One background grading job at a time, with a log the page can poll."""

from __future__ import annotations

import logging
import threading
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

log = logging.getLogger(__name__)


@dataclass
class Job:
    title: str
    lines: list[str] = field(default_factory=list)
    done: bool = False
    error: str = ""
    started: datetime = field(default_factory=datetime.now)

    def say(self, message: str) -> None:
        self.lines.append(f"{datetime.now():%H:%M:%S}  {message}")


class JobRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.current: Job | None = None

    @property
    def busy(self) -> bool:
        return self.current is not None and not self.current.done

    def start(self, title: str, work: Callable[[Job], None]) -> Job:
        with self._lock:
            if self.busy:
                raise RuntimeError(f"already running: {self.current.title}")
            job = Job(title)
            self.current = job

        def target() -> None:
            try:
                work(job)
                job.say("finished")
            except Exception as e:
                log.exception("job %s failed", title)
                job.error = f"{type(e).__name__}: {e}"
                job.say(traceback.format_exc().strip().splitlines()[-1])
            finally:
                job.done = True

        threading.Thread(target=target, daemon=True).start()
        return job
