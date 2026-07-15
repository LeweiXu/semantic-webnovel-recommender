"""Detached, admin-only maintenance jobs and live log streaming."""
from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from scripts.repo_paths import DATA_DIR, LOGS_DIR, ROOT_DIR

from auth import require_admin

JOBS_PATH = DATA_DIR / "admin_jobs.json"
HISTORY_PATH = DATA_DIR / "admin_command_history.json"
HISTORY_MAX = 5
JOB_LOG_DIR = LOGS_DIR / "admin_jobs"
SCRIPT_PATHS = {
    "scrape_metadata": ROOT_DIR / "scrape_metadata.py",
    "download": ROOT_DIR / "download.py",
    "recommend": ROOT_DIR / "recommend.py",
}
_SHELL_METACHARS = frozenset(";&|><`\n\r")

router = APIRouter(prefix="/api/admin/jobs", tags=["admin"])
_lock = threading.Lock()
_processes: dict[str, subprocess.Popen] = {}


def build_argv(command: str, *, python: str | None = None) -> list[str]:
    """Turn an allowlisted command line into a shell-free Popen argv."""
    if not isinstance(command, str) or not command.strip():
        raise ValueError("Enter a command")
    if any(char in command for char in _SHELL_METACHARS) or "$(" in command:
        raise ValueError("Shell metacharacters are not allowed")
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        raise ValueError(f"Invalid quoting: {exc}") from exc
    if not parts or parts[0] not in SCRIPT_PATHS:
        allowed = ", ".join(SCRIPT_PATHS)
        raise ValueError(f"Script must be one of: {allowed}")
    return [python or sys.executable, str(SCRIPT_PATHS[parts[0]]), *parts[1:]]


def _load() -> dict[str, dict]:
    try:
        data = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict[str, dict]) -> None:
    JOBS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=JOBS_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, JOBS_PATH)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# ── Command history (last few commands, so they're quick to re-run) ──────────

def _load_history() -> list[dict]:
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_history(items: list[dict]) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=HISTORY_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(items, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, HISTORY_PATH)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _remember_command(command: str) -> None:
    command = command.strip()
    if not command:
        return
    with _lock:
        items = [it for it in _load_history() if it.get("command") != command]
        items.insert(0, {"id": uuid.uuid4().hex, "command": command})
        _save_history(items[:HISTORY_MAX])


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _reconcile(jobs: dict[str, dict]) -> bool:
    changed = False
    for job_id, job in jobs.items():
        if job.get("status") not in {"running", "stopping"}:
            continue
        process = _processes.get(job_id)
        returncode = process.poll() if process is not None else None
        if process is not None and returncode is not None:
            job["returncode"] = returncode
            job["status"] = "stopped" if job.get("status") == "stopping" else (
                "succeeded" if returncode == 0 else "failed"
            )
            _processes.pop(job_id, None)
            changed = True
        elif process is None and not _pid_alive(int(job.get("pid", -1))):
            job["status"] = "stopped" if job.get("status") == "stopping" else "finished"
            changed = True
    return changed


def _jobs_snapshot() -> dict[str, dict]:
    with _lock:
        jobs = _load()
        if _reconcile(jobs):
            _save(jobs)
        return jobs


def _public_job(job_id: str, job: dict) -> dict:
    return {"id": job_id, **job}


@router.post("")
def start_job(body: dict, _admin: str = Depends(require_admin)) -> dict:
    command = str(body.get("command", ""))
    try:
        argv = build_argv(command)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    job_id = uuid.uuid4().hex
    JOB_LOG_DIR.mkdir(parents=True, exist_ok=True)
    logfile = JOB_LOG_DIR / f"{job_id}.log"
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    try:
        with logfile.open("ab", buffering=0) as output:
            process = subprocess.Popen(
                argv,
                cwd=ROOT_DIR,
                start_new_session=True,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not start job: {exc}") from exc

    record = {
        "script": shlex.split(command)[0],
        "args": argv[2:],
        "pid": process.pid,
        "status": "running",
        "started": started,
        "logfile": str(logfile),
        "returncode": None,
    }
    with _lock:
        jobs = _load()
        jobs[job_id] = record
        _processes[job_id] = process
        _save(jobs)
    _remember_command(command)
    return _public_job(job_id, record)


@router.get("/history")
def list_history(_admin: str = Depends(require_admin)) -> list[dict]:
    with _lock:
        return _load_history()


@router.delete("/history/{hid}")
def delete_history(hid: str, _admin: str = Depends(require_admin)) -> list[dict]:
    with _lock:
        items = [it for it in _load_history() if it.get("id") != hid]
        _save_history(items)
        return items


@router.get("")
def list_jobs(_admin: str = Depends(require_admin)) -> list[dict]:
    jobs = _jobs_snapshot()
    ordered = sorted(jobs.items(), key=lambda item: item[1].get("started", ""), reverse=True)
    return [_public_job(job_id, job) for job_id, job in ordered]


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _tail(job_id: str, logfile: Path):
    offset = 0
    idle_ticks = 0
    while True:
        try:
            with logfile.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(offset)
                chunk = handle.read()
                offset = handle.tell()
        except FileNotFoundError:
            chunk = ""
        if chunk:
            idle_ticks = 0
            yield _sse("log", {"text": chunk})
        else:
            idle_ticks += 1

        job = _jobs_snapshot().get(job_id)
        if job is None:
            yield _sse("error", {"message": "Job no longer exists"})
            return
        if job.get("status") not in {"running", "stopping"} and idle_ticks >= 2:
            yield _sse("done", {"status": job.get("status"), "returncode": job.get("returncode")})
            return
        if idle_ticks % 20 == 0:
            yield ": keepalive\n\n"
        time.sleep(0.5)


@router.get("/{job_id}/log")
def job_log(job_id: str, _admin: str = Depends(require_admin)) -> StreamingResponse:
    job = _jobs_snapshot().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return StreamingResponse(
        _tail(job_id, Path(job["logfile"])),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{job_id}/stop")
def stop_job(job_id: str, _admin: str = Depends(require_admin)) -> dict:
    with _lock:
        jobs = _load()
        _reconcile(jobs)
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.get("status") not in {"running", "stopping"}:
            _save(jobs)
            return _public_job(job_id, job)
        pid = int(job.get("pid", -1))
        if pid <= 0:
            job["status"] = "finished"
            _save(jobs)
            return _public_job(job_id, job)
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            job["status"] = "finished"
        else:
            job["status"] = "stopping"
        _save(jobs)
        return _public_job(job_id, job)
