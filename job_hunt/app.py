from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db
from .ai.match_score import score_job
from .scrapers.remotive import RemotiveScraper

ROOT = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=ROOT / "templates")
templates.env.cache = None  # workaround: Jinja2 LRUCache fails on Python 3.14

TARGET_ROLES = [
    "Data Analyst",
    "Product Analyst",
    "Business Intelligence Analyst",
    "Insights Analyst",
    "Marketing Analyst",
    "Reporting Analyst",
    "Operations Analyst",
    "Strategy Analyst",
    "Financial Data Analyst",
    "Decision Support Analyst",
    "Growth Analyst",
    "MIS Analyst",
    "Analytics Consultant",
]

SCRAPERS = [RemotiveScraper()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Anmol Job Hunt 2026", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    with db.connect() as conn:
        rows = db.list_jobs(conn)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "rows": rows,
            "statuses": sorted(db.VALID_STATUSES),
            "roles": TARGET_ROLES,
        },
    )


@app.post("/refresh", response_class=HTMLResponse)
def refresh(request: Request):
    new_count = 0
    seen_count = 0
    errors: list[str] = []
    with db.connect() as conn:
        for scraper in SCRAPERS:
            for role in TARGET_ROLES:
                try:
                    jobs = scraper.fetch(role)
                except Exception as e:
                    errors.append(f"{scraper.name}/{role}: {e}")
                    continue
                for job in jobs:
                    _, is_new = db.upsert_job(conn, job)
                    if is_new:
                        new_count += 1
                    else:
                        seen_count += 1
        rows = db.list_jobs(conn)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "rows": rows,
            "statuses": sorted(db.VALID_STATUSES),
            "roles": TARGET_ROLES,
            "flash": f"Refreshed: {new_count} new, {seen_count} already seen"
            + (f" ({len(errors)} errors)" if errors else ""),
        },
    )


@app.post("/jobs/{job_id}/status", response_class=HTMLResponse)
def set_status(job_id: int, status: str = Form(...)):
    if status not in db.VALID_STATUSES:
        raise HTTPException(400, f"invalid status: {status}")
    with db.connect() as conn:
        db.update_status(conn, job_id, status)
    return HTMLResponse(f'<span class="status status-{status}">{status}</span>')


@app.post("/score-all", response_class=HTMLResponse)
def score_all(request: Request):
    """Score all unscored jobs with Claude. Returns refreshed dashboard."""
    scored = 0
    errors: list[str] = []
    with db.connect() as conn:
        unscored = db.get_unscored_jobs(conn)
        for row in unscored:
            try:
                score, reason = score_job(dict(row))
                db.set_match_score(conn, row["id"], score, reason)
                scored += 1
            except Exception as e:
                errors.append(f"job {row['id']}: {e}")
                if len(errors) >= 3:
                    break  # bail early on repeated failures (likely API key/quota)
        rows = db.list_jobs(conn)

    flash = f"Scored {scored} jobs"
    if errors:
        flash += f" — stopped after {len(errors)} errors. First: {errors[0]}"
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "rows": rows,
            "statuses": sorted(db.VALID_STATUSES),
            "roles": TARGET_ROLES,
            "flash": flash,
        },
    )
