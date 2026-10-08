# NSO AI-PC V2.2 implementation map

V2.2 keeps the V2.1 clinical workflow and adds the governance and longitudinal
records required for a controlled clinical pilot.

## Clinical workflow

- Tier 1 is presented as **13 core clinical data groups sufficient for an
  initial recommendation**. Tier 2 and Tier 3 are optional confidence and
  research assessments.
- `Primary visual goal` is now `Optimization objective`, with six governed
  objective labels.
- The recommendation response separates a pre-treatment model estimate from a
  later observed clinical outcome. It exposes an opaque Design ID, design
  version, confidence category, structured clinical contributors, guidance and
  safety state; optical/manufacturing parameters remain server-only.
- Every Generate action creates a new opaque Design ID. Report export reuses
  the displayed design snapshot and does not create another design record.
- The rule-based engine is not treated as a calibrated clinical probability
  model. Precise response distributions are suppressed until an in-range,
  validated production model explicitly supports them.
- Follow-up accepts visit dates, per-eye AL/refraction, comfort, CSF, exposure,
  compliance and safety observations. It calculates the interval, per-eye and
  annualized delta AL, observed response, deviation and next action.
- A next recommendation creates an immutable `V2` design linked to the original
  `V1`; it never overwrites the original design.

## Governed records

The immutable domain store now carries:

`Patient -> Case -> Eye -> Visit -> Measurement -> Feature Vector -> Inference -> Design Version -> Recipe Version -> Manufacturing Lot -> QC -> Product Exposure -> Clinical Outcome`

Every prediction also creates an Execution Record containing software, clinical
schema, feature schema, model, dataset, configuration, design and product-family
versions. Measurement provenance is recorded as objective, clinician-assessed,
patient-reported or behavioral/environmental.

## Model governance

- Models are immutable registry records with Development, Candidate, Validated,
  Production, Retired and Rejected status transitions.
- Candidate validation requires data quality, internal performance, clinical
  performance, subgroup, robustness, manufacturing-feasibility and safety
  gates. Human approval is a separate record after validation.
- Retraining triggers only permit candidate creation. They never deploy a model
  automatically.
- Promotion to production is a separate explicit human action.
- Rollback creates an immutable deployment event and restores a previously
  validated/retired model; it never rewrites history.
- Clinician overrides preserve both the system recommendation and clinician
  selection, require a reason and are eligible for later learning analysis.

## Clinical Digital Thread

`GET /api/v2/trace/{case_id}` reconstructs the clinical, design, physical
product, outcome and learning lifecycle. Recipe parameters and tolerances stay
in the restricted backend record and are redacted from the trace response.

The production PostgreSQL target schema is defined in
`src/backend/schema_v22.sql`. The portable immutable store remains the runtime
adapter for SQLite and test deployments.

## Verification

The V2.2 acceptance suites are `src/backend/test_v22_acceptance.py` and
`src/backend/test_engineering_spec.py`. They cover
version chains, probability suppression, OOD behavior, date-derived follow-up,
insufficient-data handling, all model gates, controlled promotion, retraining,
override audit, lineage, Recipe/Lot/QC/Exposure records, dataset freezing,
training, validation, approval, deployment, rollback and black-box safety.
