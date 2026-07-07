# NSO AI-PC Fitting v0.3 — Rule-based Probabilistic Responder Engine

A myopia-control lens fitting demo. Deterministic, rule-based engine (no trained
model) exposed through a FastAPI backend and a Next.js frontend.

```
src/
  backend/    FastAPI API + engine (nso_core) + Streamlit app + PDF reports
  frontend/   Next.js web UI (Seline design)
```

```
Browser ─▶ Next.js UI (src/frontend) ─HTTP▶ FastAPI (src/backend/api.py) ─▶ nso_core.py
```

- `nso_core.py` — all the computation logic (Streamlit-free, importable anywhere).
- `nso_mvp.py` — the original Streamlit app (imports the same engine).
- `api.py` — FastAPI: `/api/predict`, `/api/followup`, `/api/report/*`, `/api/health`.
- `report.py` — PDF report generation (reportlab).

## Run locally

### 1. Backend (FastAPI)

```bash
cd src/backend
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn api:app --reload --port 8000
```

API docs: http://127.0.0.1:8000/docs · health: http://127.0.0.1:8000/api/health

Tests: `python -m pytest -q`  ·  Streamlit app: `streamlit run nso_mvp.py`

### 2. Frontend (Next.js)

In a second terminal:

```bash
cd src/frontend
cp .env.local.example .env.local     # NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000
npm install
npm run dev                          # http://localhost:3000
```

## Deploy (Vercel + Render)

Push this repo to GitHub, then:

**Backend → Render.** New → Blueprint (reads `render.yaml`), or a Web Service with
Root Directory `src/backend`, build `pip install -r requirements.txt`, start
`uvicorn api:app --host 0.0.0.0 --port $PORT`. Add custom domain `api.yourdomain.com`.
Set env `ALLOWED_ORIGINS=https://nso.yourdomain.com`.

**Frontend → Vercel.** New Project → Root Directory `src/frontend`, env
`NEXT_PUBLIC_API_BASE=https://api.yourdomain.com`. Add custom domain
`nso.yourdomain.com`.

Both give free HTTPS. Render's free tier cold-starts after idle (~30 s) — fine for demos.

## Note

MVP/demo only. All coefficients are simplified and not clinically calibrated —
not for diagnosis or prescription.
