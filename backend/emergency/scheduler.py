"""In-process background scheduler: keeps every request's alert schedule, the AI takeover, proactive check-ins and
the virtual IoT devices moving even when NO dashboard is open (Render has no separate worker on this plan).

* Runs only inside a web server process (daphne / gunicorn / uvicorn / runserver), never in tests or manage.py
  commands. Disable with ALERT_SCHEDULER=0 (e.g. when `python manage.py run_alert_monitor` runs as a worker).
* Holds no state: everything is in the database, so a restart simply continues where it stopped.
* Safe next to operator polls and other processes: each step is a conditional UPDATE (no duplicates).
"""
import logging
import os
import sys
import threading
import time

log = logging.getLogger(__name__)
_started = False
_lock = threading.Lock()


def run_once():
    from django.db import close_old_connections
    from patient_monitoring import proactive, simulation
    from . import alert_workflow, journey
    close_old_connections()
    n = 0
    for step in (alert_workflow.tick, proactive.tick, simulation.tick_all, journey.tick):
        try:
            n += step() or 0
        except Exception:
            log.exception("scheduler step %s failed", step.__name__)
    close_old_connections()
    return n


def _loop(every):
    time.sleep(3)  # let the server finish starting
    while True:
        run_once()
        time.sleep(every)


def is_server_process(argv=None):
    argv = argv or sys.argv
    prog = os.path.basename(argv[0]).lower() if argv else ""
    if any(s in prog for s in ("daphne", "gunicorn", "uvicorn")):
        return True
    # runserver: only the child that serves requests (the auto-reloader parent sets RUN_MAIN in the child)
    return "runserver" in argv and (os.environ.get("RUN_MAIN") == "true" or "--noreload" in argv)


def start_background():
    global _started
    if os.environ.get("ALERT_SCHEDULER", "1") == "0" or not is_server_process():
        return False
    with _lock:
        if _started:
            return False
        _started = True
    every = int(os.environ.get("ALERT_TICK_SECONDS", "5"))
    threading.Thread(target=_loop, args=(every,), name="alert-scheduler", daemon=True).start()
    log.info("alert scheduler started (every %ss)", every)
    return True
