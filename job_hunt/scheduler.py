"""Lightweight APScheduler wrapper that runs the same _do_refresh logic the
HTTP /refresh route uses, on a configurable interval. Started in the FastAPI
lifespan so it shares the worker process."""

import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler

log = logging.getLogger("job_hunt.scheduler")

DEFAULT_INTERVAL_HOURS = 4.0


def _interval_hours() -> float:
    raw = os.environ.get("REFRESH_INTERVAL_HOURS")
    if not raw:
        return DEFAULT_INTERVAL_HOURS
    try:
        return max(0.05, float(raw))  # floor at 3 minutes
    except ValueError:
        return DEFAULT_INTERVAL_HOURS


def start_scheduler(refresh_callable) -> BackgroundScheduler:
    """Start a background scheduler that runs `refresh_callable` every
    REFRESH_INTERVAL_HOURS hours (default 4). The callable is invoked with no
    args; pass a closure if it needs arguments. Returns the scheduler so the
    caller can shut it down on app exit."""
    interval = _interval_hours()
    sched = BackgroundScheduler(daemon=True)

    def _job():
        try:
            t = refresh_callable()
            log.info(
                "scheduled refresh: new=%s seen=%s filtered=%s archived=%s errors=%s",
                t.get("new", 0), t.get("seen", 0), t.get("filtered", 0),
                t.get("archived", 0), t.get("errors", 0),
            )
        except Exception:
            log.exception("scheduled refresh failed")

    sched.add_job(_job, "interval", hours=interval, id="periodic_refresh", replace_existing=True)
    sched.start()
    log.info("scheduler started (interval %.2fh)", interval)
    return sched
