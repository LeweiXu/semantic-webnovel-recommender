from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable


EventCallback = Callable[[str], None]


class JobState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSE_REQUESTED = "pause requested"
    PAUSED = "paused"
    STOPPING = "stopping"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass(frozen=True)
class JobSnapshot:
    name: str | None
    state: JobState
    started_at: float | None
    error: str | None = None


class FetchControls:
    """Cooperative controls shared by one foreground fetch job and its workers."""

    def __init__(self, emit: EventCallback | None = None) -> None:
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.paused_event = threading.Event()
        self.network_lock = threading.RLock()
        self._emit = emit or (lambda _message: None)
        self._condition = threading.Condition()
        self._workers = 0
        self._paused_workers = 0

    def register_worker(self) -> None:
        with self._condition:
            self._workers += 1

    def unregister_worker(self) -> None:
        with self._condition:
            self._workers = max(0, self._workers - 1)
            self._refresh_paused_state()
            self._condition.notify_all()

    def safe_point(self, checkpoint: Callable[[], None] | None = None) -> bool:
        """Pause only between requests/work units, checkpointing before waiting."""
        if self.stop_event.is_set():
            return False
        if not self.pause_event.is_set():
            return True
        if checkpoint is not None:
            checkpoint()
        with self._condition:
            self._paused_workers += 1
            self._refresh_paused_state()
            try:
                while self.pause_event.is_set() and not self.stop_event.is_set():
                    self._condition.wait(0.2)
            finally:
                self._paused_workers = max(0, self._paused_workers - 1)
                self._refresh_paused_state()
        return not self.stop_event.is_set()

    def request_pause(self) -> None:
        self.pause_event.set()

    def resume(self) -> None:
        self.pause_event.clear()
        with self._condition:
            self.paused_event.clear()
            self._condition.notify_all()

    def request_stop(self) -> None:
        self.stop_event.set()
        self.resume()

    def wait_until_paused(self, timeout: float | None = None) -> bool:
        return self.paused_event.wait(timeout)

    def interruptible_wait(self, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while not self.stop_event.is_set():
            if self.pause_event.is_set():
                return self.safe_point()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            self.stop_event.wait(min(0.2, remaining))
        return False

    def _refresh_paused_state(self) -> None:
        if self._workers and self._paused_workers >= self._workers:
            if not self.paused_event.is_set():
                self.paused_event.set()
                self._emit("Fetch job paused at a safe checkpoint.")
        else:
            self.paused_event.clear()


class JobManager:
    """Owns the single site-fetch job allowed by the application."""

    def __init__(
        self,
        emit: EventCallback | None = None,
        state_changed: Callable[[JobSnapshot], None] | None = None,
    ) -> None:
        self._emit = emit or (lambda _message: None)
        self._state_changed = state_changed or (lambda _snapshot: None)
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._exclusive_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._controls: FetchControls | None = None
        self._interactive_active = False
        self._interactive_waiters = 0
        self._name: str | None = None
        self._state = JobState.IDLE
        self._started_at: float | None = None
        self._error: str | None = None

    @property
    def snapshot(self) -> JobSnapshot:
        with self._lock:
            return JobSnapshot(self._name, self._state, self._started_at, self._error)

    @property
    def active(self) -> bool:
        with self._lock:
            return bool(self._thread and self._thread.is_alive())

    @property
    def busy(self) -> bool:
        with self._lock:
            return (
                bool(self._thread and self._thread.is_alive())
                or self._interactive_active
                or self._interactive_waiters > 0
            )

    def start(self, name: str, target: Callable[[FetchControls], object]) -> bool:
        with self._lock:
            if (
                (self._thread and self._thread.is_alive())
                or self._interactive_active
                or self._interactive_waiters
            ):
                return False
            controls = FetchControls(self._emit)
            self._controls = controls
            self._name = name
            self._state = JobState.RUNNING
            self._started_at = time.monotonic()
            self._error = None
            self._notify()

            def run() -> None:
                try:
                    self._emit(f"Started: {name}")
                    target(controls)
                    with self._lock:
                        self._state = JobState.COMPLETE
                    self._emit(f"Finished: {name}")
                except Exception as exc:
                    with self._lock:
                        self._state = JobState.FAILED
                        self._error = str(exc)
                    self._emit(f"Failed: {name}: {exc}")
                finally:
                    with self._lock:
                        self._notify()
                        self._condition.notify_all()

            self._thread = threading.Thread(
                target=run,
                name="webnovel-fetch-job",
                daemon=True,
            )
            self._thread.start()
            return True

    def pause(self) -> bool:
        with self._lock:
            if not self.active or self._controls is None:
                return False
            if self._state == JobState.PAUSED:
                return True
            self._state = JobState.PAUSE_REQUESTED
            self._controls.request_pause()
            self._notify()
            self._emit("Pause requested; waiting for the current request to finish.")
            return True

    def resume(self) -> bool:
        with self._lock:
            if not self.active or self._controls is None:
                return False
            self._controls.resume()
            self._state = JobState.RUNNING
            self._notify()
            self._emit("Fetch job resumed.")
            return True

    def stop(self) -> bool:
        with self._lock:
            if self._interactive_active and not self.active:
                self._emit(
                    "The interactive fetch cannot stop mid-novel; it will finish before exit."
                )
                return True
            if not self.active or self._controls is None:
                return False
            self._state = JobState.STOPPING
            self._controls.request_stop()
            self._notify()
            self._emit("Stop requested; checkpointing after the current request.")
            return True

    def join(self, timeout: float | None = None) -> bool:
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def wait_all(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while self.busy:
                if deadline is None:
                    self._condition.wait(0.2)
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(min(0.2, remaining))
        return True

    def run_exclusive(self, label: str, target: Callable[[], object]) -> object:
        claimed = False
        with self._condition:
            self._interactive_waiters += 1
        try:
            with self._exclusive_lock:
                with self._condition:
                    self._interactive_waiters -= 1
                    self._interactive_active = True
                    claimed = True
                    controls = self._controls
                    background_active = bool(self._thread and self._thread.is_alive())
                    if not background_active:
                        self._name = f"interactive: {label}"
                        self._state = JobState.RUNNING
                        self._started_at = time.monotonic()
                        self._error = None
                        self._notify()

                resume_after = False
                if background_active and controls is not None:
                    already_paused = controls.pause_event.is_set()
                    if not already_paused:
                        self.pause()
                        resume_after = True
                    deadline = time.monotonic() + 300
                    while self.active and not controls.paused_event.is_set():
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError(
                                "Timed out waiting for the active fetch job to pause"
                            )
                        controls.wait_until_paused(min(0.2, remaining))
                    network_lock = (
                        controls.network_lock if self.active else threading.RLock()
                    )
                else:
                    network_lock = threading.RLock()

                self._emit(f"Interactive fetch: {label}")
                try:
                    with network_lock:
                        return target()
                finally:
                    if resume_after and self.active:
                        self.resume()
        finally:
            with self._condition:
                if not claimed:
                    self._interactive_waiters -= 1
                else:
                    self._interactive_active = False
                if not (self._thread and self._thread.is_alive()):
                    self._state = JobState.COMPLETE
                    self._notify()
                self._condition.notify_all()

    def sync_state(self) -> JobSnapshot:
        with self._lock:
            controls = self._controls
            if (
                controls is not None
                and controls.paused_event.is_set()
                and self._state == JobState.PAUSE_REQUESTED
            ):
                self._state = JobState.PAUSED
                self._notify()
            return self.snapshot

    def _notify(self) -> None:
        self._state_changed(
            JobSnapshot(self._name, self._state, self._started_at, self._error)
        )
