# NSO AI-PC V2.1 — Clinical Decision Support

A knowledge-guided personalization engine exposed through a FastAPI backend
and a Next.js clinical frontend. It is a clinical-pilot prototype, not an
autonomous diagnostic or treatment-efficacy prediction system.

```
src/
  backend/    FastAPI API + engine (nso_core, nso_v2) + design console + PDF reports
  frontend/   Next.js web UI (Seline design)
```

## Architecture: simple clinical UI, hidden design engine, controlled manufacturing API

The guiding principle is **UI complexity ≠ algorithm complexity**. The backend
may be arbitrarily complex; the clinician's screen stays simple, and the
valuable part — the NSO design recipe — never leaves the server.

Three IP layers:

| Layer | Contents | Who can see it |
|---|---|---|
| **Clinical** | Patient data → visual phenotype → clinical recommendation → predicted outcomes | Clinician, browser, PDF report |
| **Design IP** | Visual phenotype → optimization → SA/HOA → microstructure → spatial statistics → OD/OS asymmetry | Server only |
| **Manufacturing IP** | Design ID → surface map → microstructure map → toolpath → instructions | Authorized vendors, one segment each |

```
Browser
  Clinical input (3 tiers)
      │ HTTPS
      ▼
AI-PC Server
  Visual Phenotype Engine → Optimization Engine → Proprietary Design Engine
      │ returns ONLY
      ▼
  Design ID + clinical results + explainable summary
      │
      ▼  encrypted API, segmented per vendor
  Authorized manufacturers
```

The browser receives an opaque `NSO-<24 HEX>` Design ID and clinical `/100`
compatibility scores. It never
receives SA strength, microstructure geometry, fill factor, spatial density,
jitter or temporal asymmetry — not in the UI, and not in the JSON behind it, so
opening DevTools reveals nothing. `nso_v2.assert_no_design_leak` screens every
response, and `test_nso_v2.py` asserts the boundary holds.

There is deliberately **no** endpoint that returns a design recipe or a
manufacturing CSV. Manufacturing is submit-only.

### Three input tiers

1. **Quick Fitting** (default, 13 clinical data groups) — enough on its own to generate a design.
2. **Advanced Clinical Data** — full binocular, accommodative and neurovisual workup.
3. **Research Mode** — CSF curve, VEP/ERG, eye tracking, wavefront.

Optional measurements raise prediction confidence; none of them are required,
and none of them are design parameters — the client cannot steer the design.

The score disclaimer shown in both the UI and PDF is: “This score estimates
design–phenotype compatibility and does not predict treatment efficacy or
axial-length reduction.”

For production, `DESIGN_ID_SECRET` must be a stable server-only secret. The
Render blueprint generates it automatically; never expose it through a
`NEXT_PUBLIC_*` variable.

### Package layout

The engine is the `nso` package. Each layer imports only from the ones below it,
so the IP boundary is enforced by the dependency graph and not only by the guard.

```
nso/
  clinical.py         what a clinician sees — the only layer that crosses the network
  design/             phenotype → recipe → candidates → chosen OD/OS pair
    synthesis.py        the phenotype → optical design mapping
    candidates.py       candidate generation + constrained multi-objective loss
    joint.py            binocular optimization over the pair cross product
    identity.py         Design ID
  manufacturing/      Manufacturing IP layer
    geometry.py         surface map + microstructure placement (stops before toolpath)
    registry.py         Design ID store + segmented, per-vendor release
    verification.py     as-manufactured geometric conformance
  predictors/         ← the swap point for a trained model
    base.py             Predictor protocol, OutcomePrediction, registry
    rule_based.py       today's deterministic rules
  features.py         ← the ML seam: versioned feature vectors + training rows
  phenotype.py        R/B/S/N/T grading
  patient.py          clinical input (six sections)
  recipe.py           the design recipe (Design IP) — dependency-free
  config.py           every tunable constant, JSON-loadable
  ip.py               the leak guard
```

Where to make a change:

| Change | Where |
|---|---|
| A coefficient or threshold | `config.py` — never a literal in engine code |
| A new clinical input | `patient.py`, then `features.py` |
| A new model | `predictors/`, register it, activate it |
| How a design is synthesized | `design/synthesis.py` |
| What a vendor receives | `manufacturing/geometry.py` |
| What the clinician sees | `clinical.py` |

Other modules:

- `nso_core.py` — the legacy computation kernel and the design-strength profiles.
- `api.py` — FastAPI: `/api/predict`, `/api/followup`, `/api/refit`,
  `/api/manufacturing/*`, `/api/report/*`, `/api/health`.
- `report.py` — clinical-layer PDF report. Carries the Design ID, not the recipe.
- `nso_v2.py` — deprecated shim re-exporting `nso`, kept so the pre-package test
  suite runs unchanged and proves the split was behaviour-preserving.
The Streamlit design console has been removed. It rendered the full design
recipe — SA, density, manufacturing export — which is exactly what the IP
layering exists to keep off a screen, and it had fallen behind the V2.1
three-zone model besides. The Next.js interface is the only UI.

## Replacing the rules with a trained model

The engine is rule-based today. Three seams exist so that swapping in a model is
a contained change rather than a rewrite.

**1. Features.** Everything a predictor may see goes through a `FeatureVector`
with a `SCHEMA_VERSION`. `patient_features()` and `design_features()` build the
same vectors for training and for serving, so the two cannot drift apart. A
predictor given a vector from a schema it was not built for raises
`SchemaMismatchError` rather than reading a reordered array as if nothing
happened.

Optional measurements are imputed to a neutral value **and** flagged with a
companion `*_measured` indicator, so a model can tell "average" from "not
measured" — collapsing those is a classic silent bias.

**2. Training data.** `training_row(patient_fv, design_fv, outcome, **meta)`
emits one flat, JSON-serializable row per dispensed design. Logging these from
today is what makes a model possible later; features invented at training time
are the usual reason an ML project stalls.

```python
row = nso.training_row(
    nso.patient_features(patient),
    nso.design_features(recipe),
    outcome={"annualized_delta_al": 0.12, "comfort_reported": 8, "dropped_out": 0},
    design_id=design_id, dispensed_at=date.today().isoformat(),
)
```

**3. The predictor.** Implement the protocol and register it:

```python
class GradientBoostedPredictor:
    name = "gbm"
    version = "1.0.0"
    schema_version = "1.0.0"        # what it was trained against

    def predict(self, patient, design):
        nso.predictors.check_schema(self, patient, design)
        control, comfort, adaptation, acuity, manufacturability = self._model.predict(
            [patient.to_array() + design.to_array()]
        )[0]
        return nso.OutcomePrediction(..., predictor_name=self.name, ...)

nso.register(GradientBoostedPredictor(), activate=True)
```

Nothing else changes. The design space, the loss weights and the clinical
payload are untouched, because the predictor answers *what will happen* while
`design/candidates.py` decides *what we value* — a model can change the forecast
without silently changing clinical priorities. Every result is stamped with the
predictor name, its version, the feature schema and the config version, so a
stored prediction can always be traced to what produced it.

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
Set env `ALLOWED_ORIGINS=https://nso.yourdomain.com`. Leave the
`MANUFACTURING_KEY_*` variables unset unless a vendor integration is live —
without any of them `/api/manufacturing/package` stays disabled (503).

**Frontend → Vercel.** New Project → Root Directory `src/frontend`, env
`NEXT_PUBLIC_API_BASE=https://api.yourdomain.com`. Add custom domain
`nso.yourdomain.com`.

Both give free HTTPS. Render's free tier cold-starts after idle (~30 s) — fine for demos.

## Note

MVP/demo only. All coefficients are simplified and not clinically calibrated —
not for diagnosis or prescription.

### Documentation

- **[INPUTS.md](INPUTS.md)** — every input field: what it is, what the engine does
  with it, and which coefficients on that path are placeholders. Written from the
  clinician's side of the screen. Read this to know what a field actually does
  before trusting it — six fields are currently accepted and unused, and nine
  only raise the confidence figure.
- **[ASSUMPTIONS.md](ASSUMPTIONS.md)** — every number that is a placeholder rather
  than data, graded P0 (blocks real use) through P3 (cleanup).

### Unvalidated constants and open questions

Every number in the engine that is a placeholder rather than data — plus the
known technical gaps — is catalogued in **[ASSUMPTIONS.md](ASSUMPTIONS.md)**,
graded P0 (blocks real use) through P3 (cleanup). Read it before trusting any
output or sending any package to a manufacturer.

## Persistence

Design IDs must outlive the process: a clinician who returns tomorrow needs to
submit, a vendor needs to pull, a follow-up needs to refit. `NSO_DATABASE_URL`
selects the store, and `/api/health` reports which one is live.

```
sqlite:////var/data/nso.db        file-backed; what runs today
postgresql://user:pw@host/db      the intended production backend
(unset)                           in-memory — tests only, lost on restart
```

Both SQL backends share one schema and one set of statements, so the SQLite
behaviour the tests cover is the behaviour Postgres reproduces. Moving to
Postgres is a URL change plus `psycopg`.

Two caveats worth knowing before deploying:

- **Render's free tier has an ephemeral filesystem**, so a SQLite file does not
  survive a redeploy there. Free tier needs an external Postgres; a paid Disk or
  managed Postgres is the real answer.
- `PostgresDesignStore` has **not been exercised against a real server** — no
  instance was available. Run the store tests against a live instance before
  trusting a manufacturing serial to it.

Job serials come from the database (`UPDATE ... RETURNING`, atomic on both
backends), so two processes cannot be handed the same manufacturing serial.
