"""Fitted web server. Run from backend/:  .venv/bin/uvicorn app.main:app --port 8000"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent.parent


def _load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()

from . import ai  # noqa: E402
from .design import FILAMENTS, MOTIFS  # noqa: E402
from .hand import PALM_BASE  # noqa: E402
from .photo import marker_sheet_pdf, marker_sheet_png  # noqa: E402
from .pipeline import JOBS, SAMPLES_DIR, InputError, Inputs, sample_inputs, submit  # noqa: E402
from .sizing import ansur_summary, population_check  # noqa: E402

app = FastAPI(title="Fitted")
MAX_UPLOAD = 60 * 1024 * 1024


@lru_cache(maxsize=1)
def _meta() -> dict:
    return {
        "filaments": FILAMENTS,
        "motifs": MOTIFS,
        "population": population_check(),
        "ansur": ansur_summary(),
        "base": PALM_BASE,
        "ai_available": ai.available(),
    }


@app.get("/api/meta")
def meta():
    return _meta()


@app.get("/api/marker-sheet")
def sheet(paper: str = "letter", fmt: str = "pdf"):
    paper = "a4" if paper.lower() == "a4" else "letter"
    if fmt == "png":
        return Response(marker_sheet_png(paper), media_type="image/png")
    return Response(
        marker_sheet_pdf(paper),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="fitted-marker-sheet-{paper}.pdf"'},
    )


@app.get("/api/samples/{sample}/{name}")
def sample_file(sample: str, name: str):
    if not re.fullmatch(r"[a-z_]+", sample) or not re.fullmatch(r"[A-Za-z_]+\.(jpg|ply)", name):
        raise HTTPException(404)
    p = SAMPLES_DIR / sample / name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


async def _read(f: Optional[UploadFile]) -> Optional[bytes]:
    if f is None:
        return None
    data = await f.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "File too large (60 MB max).")
    return data or None


@app.post("/api/jobs")
async def create_job(
    payload: str = Form(...),
    top: Optional[UploadFile] = File(None),
    side: Optional[UploadFile] = File(None),
    sound: Optional[UploadFile] = File(None),
    scan: Optional[UploadFile] = File(None),
    refs: list[UploadFile] = File(default=[]),
):
    try:
        p = json.loads(payload)
    except json.JSONDecodeError:
        raise HTTPException(400, "Bad payload")
    mode = "lidar" if p.get("mode") == "lidar" else "photo"
    photos = {}
    for key, f in (("top", top), ("side", side), ("sound", sound)):
        data = await _read(f)
        if data:
            photos[key] = data
    scan_data = None
    if scan is not None:
        data = await _read(scan)
        if data:
            scan_data = (data, scan.filename or "scan.obj")
    if p.get("sample"):
        try:
            s = sample_inputs(p["sample"])
        except InputError as e:
            raise HTTPException(400, str(e))
        photos = {**s["photos"], **photos}
        if mode == "lidar":
            scan_data = scan_data or s["scan"]
            photos = {k: v for k, v in photos.items() if k == "sound"}
    ref_list = []
    for f in refs[:4]:
        data = await _read(f)
        if data:
            mt = f.content_type if f.content_type in ("image/jpeg", "image/png", "image/webp", "image/gif") else "image/jpeg"
            ref_list.append((data, mt))
    num = lambda v: float(v) if v not in (None, "") else None  # noqa: E731
    bed = p.get("bed") or {}
    inp = Inputs(
        mode=mode,
        photos=photos,
        scan=scan_data,
        refs=ref_list,
        recipient={k: p.get("recipient", {}).get(k) for k in ("name", "age", "sex", "side", "goals", "language")},
        tape={"wrist_circ": num(p.get("tape", {}).get("wrist_circ")), "forearm_circ": num(p.get("tape", {}).get("forearm_circ"))},
        manual={"sound_length": num(p.get("manual", {}).get("sound_length")), "sound_breadth": num(p.get("manual", {}).get("sound_breadth"))},
        design_text=(p.get("design_text") or "")[:600],
        bed=(num(bed.get("x")) or 220.0, num(bed.get("y")) or 220.0, num(bed.get("z")) or 250.0),
    )
    job = submit(inp)
    return {"id": job.id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404)
    return JSONResponse(job.view())


@app.get("/api/jobs/{job_id}/files/{name}")
def job_file(job_id: str, name: str):
    job = JOBS.get(job_id)
    if not job or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise HTTPException(404)
    p = job.dir / name
    if not p.is_file():
        raise HTTPException(404)
    download = name.endswith((".zip", ".3mf", ".md", ".json")) or (name.endswith(".stl") and not name.startswith("view_"))
    return FileResponse(p, filename=name if download else None)


@app.post("/api/jobs/{job_id}/approve")
def approve(job_id: str, body: dict):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404)
    name = str(body.get("name") or "").strip()[:80]
    if not name:
        raise HTTPException(400, "Name required")
    import time

    job.approved = {"name": name, "at": time.time()}
    return {"ok": True}


FRONTEND = ROOT / "frontend"
if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
