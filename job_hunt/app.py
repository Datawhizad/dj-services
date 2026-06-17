import json
import os
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
from .pdf.docx_render import render_cover_letter_docx, render_resume_docx
from .pdf.render import _sender_from_master_resume, render_cover_letter, render_resume
from .scheduler import start_scheduler
from .scrapers.ashby import AshbyScraper
from .scrapers.greenhouse import GreenhouseScraper
from .scrapers.himalayas import HimalayasScraper
from .scrapers.hn_who_is_hiring import HNWhoIsHiringScraper
from .scrapers.jobspresso import JobspressoScraper
from .scrapers.lever import LeverScraper
from .scrapers.remoteok import RemoteOKScraper
from .scrapers.remotive import RemotiveScraper
from .scrapers.smartrecruiters import SmartRecruitersScraper
from .scrapers.working_nomads import WorkingNomadsScraper
from .scrapers.wwr import WeWorkRemotelyScraper
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

SCRAPERS = [
    # Aggregators with keyword search
    RemotiveScraper(),
    WorkingNomadsScraper(),
    RemoteOKScraper(),
    HimalayasScraper(),
    # Bulk-mode (no keyword search — fetched once per refresh)
    JobspressoScraper(),
    WeWorkRemotelyScraper(),
    HNWhoIsHiringScraper(),
    GreenhouseScraper(),
    LeverScraper(),
    AshbyScraper(),
    SmartRecruitersScraper(),
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    sched = start_scheduler(_do_refresh) if os.environ.get("DISABLE_SCHEDULER") != "1" else None
    try:
        yield
    finally:
        if sched is not None:
            sched.shutdown(wait=False)


app = FastAPI(title="Anmol Job Hunt 2026", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


VALID_LOCATIONS = {"austin", "remote", "us", "anywhere"}

# Default seniority filter: hide senior+ roles since Anmol has ~14 months exp.
# User can flip to {"all"} via the UI.
DEFAULT_SENIORITY = ["intern", "entry", "mid"]


def _parse_seniority(raw: str | None) -> list[str] | None:
    """Accepts a comma-separated string ('intern,entry,mid'), the magic value
    'all', or None (which means use DEFAULT_SENIORITY)."""
    if raw is None:
        return list(DEFAULT_SENIORITY)
    if raw == "all" or raw == "":
        return None
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return parts or list(DEFAULT_SENIORITY)


def _filters_from_query(
    status: str | None,
    source: str | None,
    min_score: str | None,
    q: str | None,
    location: str | None = None,
    archived: str | None = None,
    seniority: str | None = None,
) -> dict:
    """Normalize raw query strings into list_jobs kwargs."""
    return {
        "status": status or None,
        "source": source or None,
        "min_score": int(min_score) if (min_score and min_score.isdigit()) else None,
        "query": (q or "").strip() or None,
        "location": location if location in VALID_LOCATIONS else None,
        "seniority": _parse_seniority(seniority),
        "include_archived": (archived == "1"),
    }


def _render_dashboard(request: Request, conn, *, flash: str | None = None, filters: dict | None = None):
    if filters is None:
        filters = {
            "status": None, "source": None, "min_score": None, "query": None,
            "location": None, "seniority": list(DEFAULT_SENIORITY), "include_archived": False,
        }
    rows = db.list_jobs(conn, **filters)
    sources = db.distinct_sources(conn)
    health = db.latest_per_source(conn)
    seniority_value = filters.get("seniority")
    if seniority_value is None:
        seniority_str = "all"
    else:
        seniority_str = ",".join(seniority_value)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "rows": rows,
            "statuses": sorted(db.VALID_STATUSES),
            "sources": sources,
            "roles": TARGET_ROLES,
            "filters": {
                "status": filters.get("status") or "",
                "source": filters.get("source") or "",
                "min_score": filters.get("min_score") if filters.get("min_score") is not None else "",
                "q": filters.get("query") or "",
                "location": filters.get("location") or "",
                "seniority": seniority_str,
                "archived": "1" if filters.get("include_archived") else "",
            },
            "health": health,
            "flash": flash,
        },
    )


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    status: str | None = None,
    source: str | None = None,
    min_score: str | None = None,
    q: str | None = None,
    location: str | None = None,
    archived: str | None = None,
    seniority: str | None = None,
):
    filters = _filters_from_query(status, source, min_score, q, location, archived, seniority)
    with db.connect() as conn:
        return _render_dashboard(request, conn, filters=filters)


def _do_refresh(*, scrapers=SCRAPERS) -> dict:
    """Run all scrapers, write per-source refresh_log rows, return summary dict.
    Pure function — no Request, callable from both the HTTP route and the
    APScheduler background job."""
    totals = {"new": 0, "seen": 0, "filtered": 0, "pruned": 0, "errors": 0}
    with db.connect() as conn:
        totals["pruned"] = db.prune_stale_unmatched(conn, matches_target)
        for scraper in scrapers:
            queries = [""] if scraper.bulk_mode else TARGET_ROLES
            log_id = db.start_refresh(conn, scraper.name)
            new_c = seen_c = filt_c = err_c = 0
            err_first: str | None = None
            for role in queries:
                try:
                    jobs = scraper.fetch(role)
                except Exception as e:
                    err_c += 1
                    if err_first is None:
                        err_first = f"{role or 'all'}: {e}"[:300]
                    continue
                for job in jobs:
                    if not matches_target(job.get("title")):
                        filt_c += 1
                        continue
                    _, is_new = db.upsert_job(conn, job)
                    if is_new:
                        new_c += 1
                    else:
                        seen_c += 1
            db.finish_refresh(
                conn, log_id,
                new_count=new_c, seen_count=seen_c,
                filtered_count=filt_c, error_count=err_c,
                error_first=err_first,
            )
            totals["new"] += new_c
            totals["seen"] += seen_c
            totals["filtered"] += filt_c
            totals["errors"] += err_c
        # opportunistic auto-archive after each refresh
        totals["archived"] = db.auto_archive(conn)
    return totals


@app.post("/refresh", response_class=HTMLResponse)
def refresh(request: Request):
    t = _do_refresh()
    flash = (
        f"Refreshed: {t['new']} new, {t['seen']} already seen, "
        f"{t['filtered']} filtered out by title, {t['pruned']} stale rows pruned, "
        f"{t.get('archived', 0)} auto-archived"
    )
    if t["errors"]:
        flash += f" ({t['errors']} scraper errors — see Source health panel)"
    with db.connect() as conn:
        return _render_dashboard(request, conn, flash=flash)


@app.get("/source-health", response_class=HTMLResponse)
def source_health(request: Request):
    """Returns just the source-health fragment, for HTMX polling."""
    with db.connect() as conn:
        health = db.latest_per_source(conn)
    return templates.TemplateResponse(
        request, "_source_health.html", {"health": health}
    )


@app.get("/jobs/{job_id}/jd", response_class=HTMLResponse)
def job_drawer(request: Request, job_id: int):
    """Returns the right-side drawer with the full JD for one job."""
    with db.connect() as conn:
        job = db.get_job_full(conn, job_id)
    if not job:
        raise HTTPException(404, f"job {job_id} not found")
    return templates.TemplateResponse(request, "_drawer.html", {"job": job})


@app.post("/jobs/{job_id}/archive", response_class=HTMLResponse)
def archive_job(job_id: int, archived: str = Form("1")):
    flag = archived == "1"
    with db.connect() as conn:
        db.set_archived(conn, job_id, flag)
    return HTMLResponse("")  # row will be removed from view by HTMX swap


@app.post("/jobs/{job_id}/status", response_class=HTMLResponse)
def set_status(job_id: int, status: str = Form(...)):
    if status not in db.VALID_STATUSES:
        raise HTTPException(400, f"invalid status: {status}")
    with db.connect() as conn:
        db.update_status(conn, job_id, status)
    return HTMLResponse(f'<span class="status status-{status}">{status}</span>')


def _generate_resume_for_job(job: dict) -> str:
    """Tailor + render + persist resume for one job dict. Returns tailoring summary.
    Raises on API/render failure."""
    tailored = tailor_resume(job)
    out_dir = GENERATED_DIR / str(job["id"])
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / "resume.pdf"
    docx_path = out_dir / "resume.docx"
    json_path = out_dir / "resume.json"
    render_resume(tailored, pdf_path)
    render_resume_docx(tailored, docx_path)
    json_path.write_text(json.dumps(tailored, indent=2), encoding="utf-8")
    summary = tailored.get("tailoring_notes", "")
    with db.connect() as conn:
        db.save_resume_version(conn, job["id"], str(pdf_path), str(json_path), summary)
    return summary


def _generate_cover_letter_for_job(job: dict) -> str:
    letter = write_cover_letter(job)
    out_dir = GENERATED_DIR / str(job["id"])
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / "cover_letter.pdf"
    docx_path = out_dir / "cover_letter.docx"
    json_path = out_dir / "cover_letter.json"
    render_cover_letter(letter, pdf_path)
    sender_name, sender_contact = _sender_from_master_resume()
    render_cover_letter_docx(letter, docx_path, sender_name, sender_contact)
    json_path.write_text(json.dumps(letter, indent=2), encoding="utf-8")
    summary = letter.get("tailoring_notes", "")
    with db.connect() as conn:
        db.save_cover_letter(conn, job["id"], str(pdf_path), str(json_path), summary)
    return summary


@app.post("/jobs/{job_id}/resume", response_class=HTMLResponse)
def generate_resume(job_id: int):
    """Tailor + render a resume PDF for one job. Returns updated cell HTML."""
    with db.connect() as conn:
        job_row = db.get_job_for_tailoring(conn, job_id)
        if not job_row:
            raise HTTPException(404, f"job {job_id} not found")
        job = dict(job_row)

    try:
        summary = _generate_resume_for_job(job)
    except Exception as e:
        return HTMLResponse(
            f'<span class="placeholder" style="color:#a00">error: {str(e)[:120]}</span>'
        )
    return HTMLResponse(_resume_cell_html(job_id, summary))


@app.get("/jobs/{job_id}/resume.pdf")
def download_resume(job_id: int):
    pdf_path = GENERATED_DIR / str(job_id) / "resume.pdf"
    if not pdf_path.exists():
        raise HTTPException(404, "resume not generated yet")
    return FileResponse(pdf_path, media_type="application/pdf", filename=f"anmol_resume_{job_id}.pdf")


@app.get("/jobs/{job_id}/resume.docx")
def download_resume_docx(job_id: int):
    docx_path = GENERATED_DIR / str(job_id) / "resume.docx"
    if not docx_path.exists():
        raise HTTPException(404, "resume DOCX not generated yet")
    return FileResponse(
        docx_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=f"anmol_resume_{job_id}.docx",
    )


def _resume_cell_html(job_id: int, summary: str) -> str:
    """Cell content for a job that has a generated resume."""
    summary_html = f'<div class="reason">{summary}</div>' if summary else ""
    return (
        f'<a href="/jobs/{job_id}/resume.pdf" target="_blank" class="pdf-link">PDF</a> '
        f'<a href="/jobs/{job_id}/resume.docx" target="_blank" class="docx-link">DOCX</a> '
        f'<button class="btn-link" hx-post="/jobs/{job_id}/resume" '
        f'hx-target="closest .resume-cell" hx-swap="innerHTML" '
        f'hx-indicator="#spin">Regen</button>'
        f"{summary_html}"
    )


def _cover_letter_cell_html(job_id: int, summary: str) -> str:
    summary_html = f'<div class="reason">{summary}</div>' if summary else ""
    return (
        f'<a href="/jobs/{job_id}/cover-letter.pdf" target="_blank" class="pdf-link">PDF</a> '
        f'<a href="/jobs/{job_id}/cover-letter.docx" target="_blank" class="docx-link">DOCX</a> '
        f'<button class="btn-link" hx-post="/jobs/{job_id}/cover-letter" '
        f'hx-target="closest .cover-cell" hx-swap="innerHTML" '
        f'hx-indicator="#spin">Regen</button>'
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
        summary = _generate_cover_letter_for_job(job)
    except Exception as e:
        return HTMLResponse(
            f'<span class="placeholder" style="color:#a00">error: {str(e)[:120]}</span>'
        )
    return HTMLResponse(_cover_letter_cell_html(job_id, summary))


@app.get("/jobs/{job_id}/cover-letter.pdf")
def download_cover_letter(job_id: int):
    pdf_path = GENERATED_DIR / str(job_id) / "cover_letter.pdf"
    if not pdf_path.exists():
        raise HTTPException(404, "cover letter not generated yet")
    return FileResponse(
        pdf_path, media_type="application/pdf", filename=f"anmol_cover_letter_{job_id}.pdf"
    )


@app.get("/jobs/{job_id}/cover-letter.docx")
def download_cover_letter_docx(job_id: int):
    docx_path = GENERATED_DIR / str(job_id) / "cover_letter.docx"
    if not docx_path.exists():
        raise HTTPException(404, "cover letter DOCX not generated yet")
    return FileResponse(
        docx_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=f"anmol_cover_letter_{job_id}.docx",
    )


BULK_MAX = 15  # cap per single sync request to avoid HTTP timeout


def _bulk_generate(
    request: Request,
    *,
    kind: str,
    status: str | None,
    source: str | None,
    min_score: str | None,
    q: str | None,
    location: str | None = None,
    archived: str | None = None,
    seniority: str | None = None,
):
    """Iterate filtered rows, generate the missing artifact (kind='resume' or
    'cover-letter'), respect BULK_MAX cap. Returns refreshed dashboard."""
    filters = _filters_from_query(status, source, min_score, q, location, archived, seniority)
    with db.connect() as conn:
        rows = db.list_jobs(conn, **filters)

    if kind == "resume":
        candidates = [r for r in rows if not r["resume_pdf"]]
        gen_one = _generate_resume_for_job
        kind_label = "resumes"
    else:
        candidates = [r for r in rows if not r["cover_letter_pdf"]]
        gen_one = _generate_cover_letter_for_job
        kind_label = "cover letters"

    skipped_already_done = len(rows) - len(candidates)
    batch = candidates[:BULK_MAX]
    deferred = max(0, len(candidates) - BULK_MAX)

    generated = 0
    errors: list[str] = []
    for r in batch:
        try:
            with db.connect() as conn:
                job_row = db.get_job_for_tailoring(conn, r["id"])
            gen_one(dict(job_row))
            generated += 1
        except Exception as e:
            errors.append(f"job {r['id']}: {e}")
            if len(errors) >= 3:
                break

    flash = f"Generated {generated} {kind_label} ({skipped_already_done} already done)"
    if deferred:
        flash += f"; {deferred} more matched but capped at {BULK_MAX} per click — click again for the rest"
    if errors:
        flash += f" — stopped after {len(errors)} errors. First: {errors[0]}"

    with db.connect() as conn:
        return _render_dashboard(request, conn, flash=flash, filters=filters)


@app.post("/bulk/resumes", response_class=HTMLResponse)
def bulk_resumes(
    request: Request,
    status: str | None = None,
    source: str | None = None,
    min_score: str | None = None,
    q: str | None = None,
    location: str | None = None,
    archived: str | None = None,
    seniority: str | None = None,
):
    return _bulk_generate(
        request, kind="resume", status=status, source=source, min_score=min_score, q=q,
        location=location, archived=archived, seniority=seniority,
    )


@app.post("/bulk/cover-letters", response_class=HTMLResponse)
def bulk_cover_letters(
    request: Request,
    status: str | None = None,
    source: str | None = None,
    min_score: str | None = None,
    q: str | None = None,
    location: str | None = None,
    archived: str | None = None,
    seniority: str | None = None,
):
    return _bulk_generate(
        request, kind="cover-letter", status=status, source=source, min_score=min_score, q=q,
        location=location, archived=archived, seniority=seniority,
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
        flash = f"Scored {scored} jobs"
        if errors:
            flash += f" — stopped after {len(errors)} errors. First: {errors[0]}"
        return _render_dashboard(request, conn, flash=flash)
