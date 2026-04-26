import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db
from .ai.cover_letter import write_cover_letter
from .ai.match_score import score_job
from .ai.tailor_resume import tailor_resume
from .pdf.render import render_cover_letter, render_resume
from .scrapers.remotive import RemotiveScraper
from .scrapers.working_nomads import WorkingNomadsScraper
from .title_filter import matches_target

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT.parent / "data"
GENERATED_DIR = DATA_DIR / "generated"
templates = Jinja2Templates(directory=ROOT / "templates")
templates.env.cache = None  # workaround: Jinja2 LRUCache fails on Python 3.14

TARGET_ROLES = [
    "Data Analyst",
    "Data Scientist",
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
    # Internships and entry-level
    "Data Analyst Intern",
    "Data Science Intern",
    "Analytics Intern",
    "Business Intelligence Intern",
]

SCRAPERS = [RemotiveScraper(), WorkingNomadsScraper()]


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
    filtered_count = 0
    errors: list[str] = []
    with db.connect() as conn:
        pruned_count = db.prune_stale_unmatched(conn, matches_target)
        for scraper in SCRAPERS:
            for role in TARGET_ROLES:
                try:
                    jobs = scraper.fetch(role)
                except Exception as e:
                    errors.append(f"{scraper.name}/{role}: {e}")
                    continue
                for job in jobs:
                    if not matches_target(job.get("title")):
                        filtered_count += 1
                        continue
                    _, is_new = db.upsert_job(conn, job)
                    if is_new:
                        new_count += 1
                    else:
                        seen_count += 1
        rows = db.list_jobs(conn)

    flash = (
        f"Refreshed: {new_count} new, {seen_count} already seen, "
        f"{filtered_count} filtered out by title, "
        f"{pruned_count} stale rows pruned"
    )
    if errors:
        flash += f" ({len(errors)} errors)"
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


@app.post("/jobs/{job_id}/status", response_class=HTMLResponse)
def set_status(job_id: int, status: str = Form(...)):
    if status not in db.VALID_STATUSES:
        raise HTTPException(400, f"invalid status: {status}")
    with db.connect() as conn:
        db.update_status(conn, job_id, status)
    return HTMLResponse(f'<span class="status status-{status}">{status}</span>')


@app.post("/jobs/{job_id}/resume", response_class=HTMLResponse)
def generate_resume(job_id: int):
    """Tailor + render a resume PDF for one job. Returns updated cell HTML."""
    with db.connect() as conn:
        job_row = db.get_job_for_tailoring(conn, job_id)
        if not job_row:
            raise HTTPException(404, f"job {job_id} not found")
        job = dict(job_row)

    try:
        tailored = tailor_resume(job)
    except Exception as e:
        return HTMLResponse(
            f'<span class="placeholder" style="color:#a00">error: {str(e)[:120]}</span>'
        )

    out_dir = GENERATED_DIR / str(job_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / "resume.pdf"
    json_path = out_dir / "resume.json"
    render_resume(tailored, pdf_path)
    json_path.write_text(json.dumps(tailored, indent=2), encoding="utf-8")
    summary = tailored.get("tailoring_notes", "")

    with db.connect() as conn:
        db.save_resume_version(conn, job_id, str(pdf_path), str(json_path), summary)

    return HTMLResponse(_resume_cell_html(job_id, summary))


@app.get("/jobs/{job_id}/resume.pdf")
def download_resume(job_id: int):
    pdf_path = GENERATED_DIR / str(job_id) / "resume.pdf"
    if not pdf_path.exists():
        raise HTTPException(404, "resume not generated yet")
    return FileResponse(pdf_path, media_type="application/pdf", filename=f"anmol_resume_{job_id}.pdf")


def _resume_cell_html(job_id: int, summary: str) -> str:
    """Cell content for a job that has a generated resume."""
    summary_html = (
        f'<div class="reason">{summary}</div>' if summary else ""
    )
    return (
        f'<a href="/jobs/{job_id}/resume.pdf" target="_blank" '
        f'class="pdf-link">Download PDF</a> '
        f'<button class="btn-link" hx-post="/jobs/{job_id}/resume" '
        f'hx-target="closest .resume-cell" hx-swap="innerHTML" '
        f'hx-indicator="#spin">Regenerate</button>'
        f"{summary_html}"
    )


def _cover_letter_cell_html(job_id: int, summary: str) -> str:
    summary_html = f'<div class="reason">{summary}</div>' if summary else ""
    return (
        f'<a href="/jobs/{job_id}/cover-letter.pdf" target="_blank" '
        f'class="pdf-link">Download PDF</a> '
        f'<button class="btn-link" hx-post="/jobs/{job_id}/cover-letter" '
        f'hx-target="closest .cover-cell" hx-swap="innerHTML" '
        f'hx-indicator="#spin">Regenerate</button>'
        f"{summary_html}"
    )


@app.post("/jobs/{job_id}/cover-letter", response_class=HTMLResponse)
def generate_cover_letter(job_id: int):
    """Tailor + render a cover letter PDF for one job."""
    with db.connect() as conn:
        job_row = db.get_job_for_tailoring(conn, job_id)
        if not job_row:
            raise HTTPException(404, f"job {job_id} not found")
        job = dict(job_row)

    try:
        letter = write_cover_letter(job)
    except Exception as e:
        return HTMLResponse(
            f'<span class="placeholder" style="color:#a00">error: {str(e)[:120]}</span>'
        )

    out_dir = GENERATED_DIR / str(job_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / "cover_letter.pdf"
    json_path = out_dir / "cover_letter.json"
    render_cover_letter(letter, pdf_path)
    json_path.write_text(json.dumps(letter, indent=2), encoding="utf-8")
    summary = letter.get("tailoring_notes", "")

    with db.connect() as conn:
        db.save_cover_letter(conn, job_id, str(pdf_path), str(json_path), summary)

    return HTMLResponse(_cover_letter_cell_html(job_id, summary))


@app.get("/jobs/{job_id}/cover-letter.pdf")
def download_cover_letter(job_id: int):
    pdf_path = GENERATED_DIR / str(job_id) / "cover_letter.pdf"
    if not pdf_path.exists():
        raise HTTPException(404, "cover letter not generated yet")
    return FileResponse(
        pdf_path, media_type="application/pdf", filename=f"anmol_cover_letter_{job_id}.pdf"
    )


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
