# NSO AI-PC V2.1 implementation map

This file maps the September 2026 optimization brief to executable code and
acceptance tests. It deliberately describes boundaries and behavior, not the
contents of the restricted design vault.

## P0 — security and immutability

- Public Design IDs use a 96-bit opaque digest (`NSO-<24 hex>`).
- The digest is keyed with the server-only `DESIGN_ID_SECRET`, preventing an
  offline input-to-ID lookup table when source code is available.
- Baselines are revision 0; closed-loop changes create `-R1`, `-R2`, and so on.
- Design metadata and restricted vault records are stored separately and are
  insert-only. Related jobs, approvals, QC, exposure, and audits append records
  instead of updating the baseline.
- `/api/predict` is governed by an explicit Pydantic response model and a
  field allowlist. Internal candidate/search/provenance fields never cross the
  clinical API boundary.
- Audit events contain timestamp, actor, action, object type/ID, version, and
  result. Restricted design content is not included.

## Clinical workflow

- Near phoria is entered as Exo / Ortho / Eso plus a non-negative magnitude.
- Tier-1 CSF uses the five requested labels. Full measurement data remains
  optional.
- Near exposure is total near load; screen time is a fraction of that total and
  is not added a second time.
- The UI offers only the six approved goals.
- Results show phenotype, patient-factor explanation, four `/100` fit scores,
  confidence, recommendation, and the opaque Design ID.
- Approval is a distinct action and locks the referenced immutable clinical
  dataset before a manufacturing submission can be created.

## Closed loop and digital thread

- Follow-up accepts OD/OS axial length, OD/OS refraction, comfort/stress,
  average wear, and compliance.
- Raw follow-up measurements and derived results are stored as separate outcome
  records. Derived values include per-eye delta/annualized delta and comfort
  change.
- The follow-up response immediately presents OD/OS ΔAL, per-eye and
  conservative annualized ΔAL, responder classification, deviation from the
  original server-stored compatibility signal, and one of three management
  actions: `CONTINUE`, `ADJUST`, or `RE-FIT`.
- The persisted lineage is patient → clinical dataset → design revision →
  manufacturing instance → exposure → outcome.

## Clinical-pilot presentation and latency

- The product title is `V2.1 · Clinical Decision Support — Knowledge-guided
  personalization engine`; the former Research Prototype / Rule-based labels
  are not shown to clinicians.
- Tier 1 is described as `13 clinical data groups`, not 13 individual fields.
- Variables that do not affect the current fit are marked `Recorded for
  longitudinal modeling` or `Research-only variable`.
- A data-free `/api/health` request warms the backend while the form is being
  completed. The target is 3–8 seconds; after 10 seconds the UI shows named
  processing stages and elapsed time.
- API responses include coarse `Server-Timing` and `X-NSO-Processing-Ms`
  headers for latency monitoring without exposing pipeline parameters.
- The exact medical-claim disclaimer is shown alongside scores in the UI and
  PDF report.

## Black-box verification

- Clinical API responses are schema-allowlisted and scanned recursively for
  restricted design terms.
- The production browser bundle and browser storage usage are checked for
  restricted terms and server-only secrets.
- Exported PDF text is scanned independently because reports leave the browser.
- Design IDs are checked for the keyed 96-bit opaque format and immutable
  revision suffixes.
- Interactive API documentation and the OpenAPI schema are disabled when
  `NSO_ENV=production`, reducing unnecessary public endpoint metadata.

## Manufacturing projection

- OEM access is tied to the authenticated role and an immutable capability
  version.
- Packages identify the design reference, OEM, projection version, issue time,
  expiry, and one authorized segment only.
- QC uploads can reference the manufacturing instance and append an audit event.

## Verification

Run from `src/backend`:

```bash
PYTHONDONTWRITEBYTECODE=1 ../../.venv/bin/pytest -q
```

Run from `src/frontend`:

```bash
npm run build
```

After the frontend build, run the public-boundary verifier from `src/backend`:

```bash
PYTHONDONTWRITEBYTECODE=1 ../../.venv/bin/python verify_black_box.py
```

The focused V2.1 acceptance suite is `src/backend/test_v21_acceptance.py`.
