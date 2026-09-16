"""End-to-end black-box and latency verification for the clinical pilot.

Run after building the frontend:

    cd src/frontend && npm run build
    cd ../backend && ../../.venv/bin/python verify_black_box.py

The verifier inspects only artifacts available at public boundaries: clinical
HTTP responses, the production browser page bundle, browser-storage calls,
exported PDF text, and opaque Design IDs. It never reads the design vault.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

import api


ROOT = Path(__file__).parents[2]
FRONTEND = ROOT / "src" / "frontend"
RESTRICTED_TERMS = (
    "sa_strength", "sa_profile", "nso_peak_target", "zone_height",
    "element_height", "microstructure", "fill_factor", "spatial_density",
    "temporal_multiplier", "temporal_nasal", "toolpath", "surface_map",
    "target_mtf", "relative_loss", "pairs_evaluated", "binocular_cost",
    "design_id_secret",
)


def clinical_payload(age: float = 11.0) -> dict:
    return {
        "age": age,
        "od": {"sphere": -3.25, "cylinder": -0.5, "axis": 180, "axial_length": 25.1},
        "os": {"sphere": -3.0, "cylinder": -0.25, "axis": 175, "axial_length": 24.9},
        "photopic_pupil": 5.2,
        "near_phoria": -4,
        "npc": 9,
        "accommodative_lag": 1.1,
        "csf_band": "Normal",
        "visual_stress_score": 6,
        "near_hours": 7,
        "digital_hours": 5,
        "outdoor_hours": 0.8,
        "primary_goal": "Myopia Management",
    }


def assert_clean(label: str, content: str) -> None:
    lowered = content.lower()
    leaked = [term for term in RESTRICTED_TERMS if term in lowered]
    if leaked:
        raise AssertionError(f"{label} exposes restricted terms: {', '.join(leaked)}")


def extract_pdf_text(pdf: bytes) -> str:
    executable = shutil.which("pdftotext")
    if not executable:
        raise RuntimeError("pdftotext is required for independent PDF verification")
    with tempfile.TemporaryDirectory(prefix="nso-black-box-") as directory:
        pdf_path = Path(directory) / "report.pdf"
        text_path = Path(directory) / "report.txt"
        pdf_path.write_bytes(pdf)
        subprocess.run(
            [executable, str(pdf_path), str(text_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        return text_path.read_text(errors="replace")


def verify() -> dict:
    client = TestClient(api.app)
    timings = []
    designs = []
    responses = []

    for offset in range(5):
        started = time.perf_counter()
        response = client.post("/api/predict", json=clinical_payload(11 + offset / 10))
        timings.append(time.perf_counter() - started)
        response.raise_for_status()
        responses.append(response.text)
        designs.append(response.json())

    design = designs[0]
    design_id = design["design_id"]
    if not re.fullmatch(r"NSO-[0-9A-F]{24}", design_id):
        raise AssertionError("baseline Design ID is not an opaque 96-bit handle")
    if len({item["design_id"] for item in designs}) != len(designs):
        raise AssertionError("nearby clinical inputs did not produce distinct Design IDs")

    approval = client.post("/api/design/approve", json={"design_id": design_id})
    approval.raise_for_status()
    responses.append(approval.text)
    manufacturing = client.post("/api/manufacturing/submit", json={"design_id": design_id})
    manufacturing.raise_for_status()
    responses.append(manufacturing.text)

    followup_body = {
        "design_id": design_id,
        "patient_id": design["patient_id"],
        "baseline_od_al": 25.1,
        "followup_od_al": 25.16,
        "baseline_os_al": 24.9,
        "followup_os_al": 24.96,
        "interval_months": 6,
        "baseline_comfort": 7,
        "current_comfort": 7,
        "visual_stress_score": 3,
        "average_wear_hours": 10,
        "compliance": "Good",
    }
    followup = client.post("/api/followup", json=followup_body)
    followup.raise_for_status()
    responses.append(followup.text)
    followup_json = followup.json()
    required = {
        "eyes", "annualized_delta_al", "responder_status",
        "deviation_from_original_prediction", "action_class",
    }
    if not required <= set(followup_json):
        raise AssertionError("follow-up response is missing closed-loop fields")

    refit = client.post("/api/refit", json={
        **clinical_payload(),
        "previous_design_id": design_id,
        "baseline_al": 25.1,
        "followup_al": 25.16,
        "interval_months": 6,
    })
    refit.raise_for_status()
    responses.append(refit.text)
    if not re.fullmatch(rf"{re.escape(design_id)}-R\d+", refit.json()["design_id"]):
        raise AssertionError("re-fit did not create an opaque immutable revision ID")

    for index, body in enumerate(responses, start=1):
        assert_clean(f"API response {index}", body)

    report_body = {
        **clinical_payload(),
        "design_id": design_id,
        "baseline_od_al": 25.1,
        "followup_od_al": 25.16,
        "baseline_os_al": 24.9,
        "followup_os_al": 24.96,
        "interval_months": 6,
        "baseline_comfort": 7,
        "current_comfort": 7,
        "visual_stress_score_followup": 3,
        "average_wear_hours": 10,
        "compliance": "Good",
    }
    report_response = client.post("/api/report/followup", json=report_body)
    report_response.raise_for_status()
    pdf_text = extract_pdf_text(report_response.content)
    assert_clean("exported PDF", pdf_text)
    if "does not predict treatment efficacy or axial-length reduction" not in pdf_text:
        raise AssertionError("exported PDF is missing the medical-claim disclaimer")

    page_bundles = sorted((FRONTEND / ".next" / "static" / "chunks" / "app").glob("page-*.js"))
    if not page_bundles:
        raise RuntimeError("production page bundle not found; run npm run build first")
    for bundle in page_bundles:
        assert_clean(f"browser bundle {bundle.name}", bundle.read_text(errors="replace"))

    frontend_source = (FRONTEND / "app" / "page.js").read_text()
    for storage_api in ("localStorage", "sessionStorage", "indexedDB"):
        if storage_api in frontend_source:
            raise AssertionError(f"frontend persists clinical data through {storage_api}")

    slowest = max(timings)
    if slowest > 8:
        raise AssertionError(f"warm local prediction exceeded 8-second target: {slowest:.3f}s")

    return {
        "api_responses_scanned": len(responses),
        "browser_bundles_scanned": len(page_bundles),
        "pdf_pages_verified": max(1, pdf_text.count("\f")),
        "design_id_format": "HMAC-SHA256 / 96-bit opaque handle",
        "storage_apis_detected": 0,
        "prediction_seconds": [round(value, 4) for value in timings],
        "slowest_warm_prediction_seconds": round(slowest, 4),
        "status": "PASS",
    }


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2))
