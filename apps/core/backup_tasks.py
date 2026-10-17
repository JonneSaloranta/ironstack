"""Runs "Create backup" and "Restore" from Profile → Administration →
Backups outside the web request.

Both used to run inside the request, under gunicorn's 30-second
timeout: on a real-sized instance the worker could be killed partway
through a restore, leaving the database or media half-replaced. Now the
view starts `manage.py run_backup_task` as a detached process (its own
session, so it outlives the worker that started it) and the page polls
for the result.

The task's state lives in a small JSON file in BACKUP_DIR, not in the
database: a restore replaces the database, so a status row would vanish
along with everything else halfway through. One task runs at a time.
"""

import json
import os
import subprocess
import sys
import threading

from django.conf import settings
from django.utils import timezone

from apps.core import backups

CREATE = "create"
RESTORE = "restore"
ACTIONS = (CREATE, RESTORE)

RUNNING = "running"
DONE = "done"
FAILED = "failed"


class TaskAlreadyRunning(Exception):
    pass


def _status_path():
    return backups.BACKUP_DIR / ".backup-task.json"


def _log_path():
    return backups.BACKUP_DIR / ".backup-task.log"


def _write(status):
    backups.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    path = _status_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(status))
    tmp.replace(path)


def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_status():
    """The current or most recent task, or None if there never was one.

    A task still marked running whose process is gone (the container
    restarted mid-task) is reported as failed rather than running
    forever."""
    try:
        status = json.loads(_status_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if status.get("state") == RUNNING and status.get("pid") and not _pid_alive(status["pid"]):
        status.update(state=FAILED, error="interrupted", finished_at=timezone.now().isoformat())
        _write(status)
    return status


def is_running():
    status = read_status()
    return bool(status and status["state"] == RUNNING)


def start(action, name=None):
    """Start `action` ("create", or "restore" with a backup `name`) in
    the background. Raises TaskAlreadyRunning if one still is."""
    if action not in ACTIONS:
        raise ValueError(action)
    if action == RESTORE:
        backups.safe_archive_path(name)  # InvalidBackupName before anything starts
    if is_running():
        raise TaskAlreadyRunning
    _write(
        {
            "action": action,
            "name": name,
            "state": RUNNING,
            "pid": None,
            "started_at": timezone.now().isoformat(),
        }
    )
    if settings.BACKUP_TASKS_RUN_INLINE:
        run(action, name)
        return
    command = [sys.executable, "manage.py", "run_backup_task", action]
    if name:
        command.append(name)
    with open(_log_path(), "ab") as log:
        process = subprocess.Popen(
            command,
            cwd=settings.BASE_DIR,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    status = read_status()
    if status and status["state"] == RUNNING and not status.get("pid"):
        status["pid"] = process.pid
        _write(status)
    # Reaps the child once it exits, so a finished task doesn't linger
    # as a zombie that _pid_alive() would still see as alive.
    threading.Thread(target=process.wait, daemon=True).start()


def run(action, name=None):
    """Do the work and record how it went - what `run_backup_task` runs.
    Never raises: the outcome is the status file."""
    status = read_status() or {"action": action, "name": name}
    status.update(action=action, name=name, state=RUNNING, pid=os.getpid())
    status.setdefault("started_at", timezone.now().isoformat())
    _write(status)
    try:
        if action == CREATE:
            status["name"] = backups.create_backup()
        else:
            backups.restore_backup(name)
    except backups.BackupDecryptionError as exc:
        status.update(state=FAILED, error="decryption", detail=str(exc))
    except Exception as exc:
        status.update(state=FAILED, error="failed", detail=f"{type(exc).__name__}: {exc}")
    else:
        status.update(state=DONE)
    status["finished_at"] = timezone.now().isoformat()
    _write(status)
    return status
