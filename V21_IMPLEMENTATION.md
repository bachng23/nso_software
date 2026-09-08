# NSO AI-PC V2.1 implementation map

This file maps the September 2026 optimization brief to executable code and
acceptance tests. It deliberately describes boundaries and behavior, not the
contents of the restricted design vault.

## P0 — security and immutability

- Public Design IDs use a 96-bit opaque digest (`NSO-<24 hex>`).
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
- The only management outputs are `MAINTAIN CURRENT DESIGN`,
  `DESIGN OPTIMIZATION RECOMMENDED`, and `CLINICAL REVIEW RECOMMENDED`.
- The persisted lineage is patient → clinical dataset → design revision →
  manufacturing instance → exposure → outcome.

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

The focused V2.1 acceptance suite is `src/backend/test_v21_acceptance.py`.
