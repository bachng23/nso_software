"""
FastAPI backend for the NSO AI-PC Fitting web UI (v0.3).

It is a thin wrapper around the deterministic engine in ``nso_mvp.py`` — the
web UI shows exactly the same rule-based results as the Streamlit app; only the
presentation layer differs. No model is trained; this is "rule-based, ML-ready".

Run (API only — the Next.js frontend calls it):
    python -m pip install -r api_requirements.txt
    python -m uvicorn api:app --reload --port 8000
API docs at http://127.0.0.1:8000/docs
"""

import os
from typing import Optional

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import nso_core as engine
import report

app = FastAPI(title="NSO AI-PC Fitting API", version="0.3")

# CORS: always allow localhost (dev); in production set ALLOWED_ORIGINS to a
# comma-separated list of exact frontend origins, e.g.
#   ALLOWED_ORIGINS=https://nso.example.com
_prod_origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_prod_origins,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "0.3", "engine": "rule-based"}


class PredictIn(BaseModel):
    age: float
    al: float
    se: float  # spherical equivalent (myopia, negative)
    pupil: float
    near_hours: float
    outdoor_hours: float
    comfort: float
    csf: float
    sa_strength: Optional[float] = None  # advanced design tuning (optional)
    density: Optional[float] = None


class FollowupIn(BaseModel):
    baseline_al: float
    followup_al: float
    interval_months: int
    current_profile: str = "Medium"


class FollowupReportIn(PredictIn):
    """Full patient inputs (for the prediction body) + the follow-up readings,
    so the follow-up report is the prediction report with a follow-up section."""
    baseline_al: float
    followup_al: float
    interval_months: int


def _run_predict(inp: "PredictIn"):
    return engine.run_prediction(
        age=inp.age, al=inp.al, myopia=inp.se, pupil=inp.pupil,
        near_hours=inp.near_hours, outdoor_hours=inp.outdoor_hours,
        csf_score=inp.csf, comfort_score=inp.comfort,
        sa_strength=inp.sa_strength, density=inp.density,
    )


def _pdf_response(pdf_bytes: bytes, filename: str) -> Response:
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/predict")
def predict(inp: PredictIn):
    return _run_predict(inp)


@app.post("/api/followup")
def followup(inp: FollowupIn):
    return engine.run_followup(
        inp.baseline_al, inp.followup_al, inp.interval_months, inp.current_profile
    )


@app.post("/api/report/prediction")
def report_prediction(inp: PredictIn):
    result = _run_predict(inp)
    pdf = report.build_report(inp.model_dump(), result)
    return _pdf_response(pdf, "nso-report.pdf")


@app.post("/api/report/followup")
def report_followup(inp: FollowupReportIn):
    pred = _run_predict(inp)
    fu = engine.run_followup(
        inp.baseline_al, inp.followup_al, inp.interval_months, pred["profile"]
    )
    followup = {
        "ctx": {
            "baseline_al": inp.baseline_al,
            "followup_al": inp.followup_al,
            "interval_months": inp.interval_months,
        },
        "result": fu,
    }
    pdf = report.build_report(inp.model_dump(), pred, followup=followup)
    return _pdf_response(pdf, "nso-report.pdf")
