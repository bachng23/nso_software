-- NSO AI-PC V2.2 normalized PostgreSQL schema
-- Domain payloads used by the portable SQLite adapter map to these production
-- tables. Clinical and lifecycle history is append-only; corrections create a
-- new row/version and an audit event.

CREATE SCHEMA IF NOT EXISTS nso_v22;
SET search_path TO nso_v22, public;

CREATE TABLE IF NOT EXISTS patients (
    patient_id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL,
    external_reference TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(patient_id),
    indication_code TEXT NOT NULL,
    clinician_id TEXT NOT NULL,
    site_id TEXT NOT NULL,
    status TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS eyes (
    eye_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(patient_id),
    laterality TEXT NOT NULL CHECK (laterality IN ('OD', 'OS')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (patient_id, laterality)
);

CREATE TABLE IF NOT EXISTS visits (
    visit_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(case_id),
    visit_code TEXT NOT NULL,
    visit_type TEXT NOT NULL,
    visit_date DATE NOT NULL,
    clinician_id TEXT NOT NULL,
    site_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS clinical_measurements (
    measurement_id TEXT PRIMARY KEY,
    visit_id TEXT NOT NULL REFERENCES visits(visit_id),
    eye_id TEXT REFERENCES eyes(eye_id),
    measurement_type TEXT NOT NULL,
    numeric_value DOUBLE PRECISION,
    text_value TEXT,
    unit TEXT NOT NULL,
    data_source TEXT NOT NULL,
    device_id TEXT,
    quality_flag TEXT NOT NULL,
    missing_data_flag BOOLEAN NOT NULL,
    validation_warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    schema_version TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS feature_vectors (
    feature_vector_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(case_id),
    visit_id TEXT NOT NULL REFERENCES visits(visit_id),
    feature_set_version TEXT NOT NULL,
    raw_input_hash TEXT NOT NULL,
    features JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS models (
    model_id TEXT PRIMARY KEY,
    model_family TEXT NOT NULL,
    algorithm TEXT NOT NULL,
    indication TEXT NOT NULL,
    deployment_context TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS model_versions (
    model_version_id TEXT PRIMARY KEY,
    model_id TEXT NOT NULL REFERENCES models(model_id),
    version TEXT NOT NULL,
    dataset_version_id TEXT,
    feature_set_version TEXT NOT NULL,
    objective_function_version TEXT NOT NULL,
    artifact_uri TEXT,
    artifact_hash TEXT,
    code_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'Development', 'Candidate', 'Validated', 'Production', 'Retired', 'Rejected'
    )),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (model_id, version)
);

CREATE TABLE IF NOT EXISTS inferences (
    inference_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(case_id),
    visit_id TEXT NOT NULL REFERENCES visits(visit_id),
    feature_vector_id TEXT NOT NULL REFERENCES feature_vectors(feature_vector_id),
    model_version_id TEXT NOT NULL REFERENCES model_versions(model_version_id),
    prediction JSONB NOT NULL,
    explanation JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS designs (
    design_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(case_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS design_versions (
    design_version_id TEXT PRIMARY KEY,
    design_id TEXT NOT NULL REFERENCES designs(design_id),
    version INTEGER NOT NULL,
    inference_id TEXT NOT NULL REFERENCES inferences(inference_id),
    previous_design_version_id TEXT REFERENCES design_versions(design_version_id),
    change_reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (design_id, version)
);

CREATE TABLE IF NOT EXISTS recipes (
    recipe_id TEXT PRIMARY KEY,
    design_id TEXT NOT NULL REFERENCES designs(design_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS recipe_versions (
    recipe_version_id TEXT PRIMARY KEY,
    recipe_id TEXT NOT NULL REFERENCES recipes(recipe_id),
    version INTEGER NOT NULL,
    design_version_id TEXT NOT NULL REFERENCES design_versions(design_version_id),
    parameters JSONB NOT NULL,
    tolerances JSONB NOT NULL,
    parameter_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (recipe_id, version)
);

CREATE TABLE IF NOT EXISTS manufacturing_lots (
    lot_id TEXT PRIMARY KEY,
    lot_code TEXT NOT NULL UNIQUE,
    recipe_version_id TEXT NOT NULL REFERENCES recipe_versions(recipe_version_id),
    material_code TEXT NOT NULL,
    process_version TEXT NOT NULL,
    manufacturer_id TEXT NOT NULL,
    facility_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS qc_measurements (
    qc_id TEXT PRIMARY KEY,
    lot_id TEXT NOT NULL REFERENCES manufacturing_lots(lot_id),
    measurement_type TEXT NOT NULL,
    target DOUBLE PRECISION,
    actual DOUBLE PRECISION,
    unit TEXT NOT NULL,
    measurement_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    passed BOOLEAN NOT NULL,
    measured_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS product_exposures (
    exposure_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(case_id),
    eye_id TEXT NOT NULL REFERENCES eyes(eye_id),
    lot_id TEXT NOT NULL REFERENCES manufacturing_lots(lot_id),
    exposure_start DATE NOT NULL,
    exposure_end DATE,
    compliance TEXT,
    replacement_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS clinical_outcomes (
    outcome_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(case_id),
    visit_id TEXT NOT NULL REFERENCES visits(visit_id),
    baseline_visit_id TEXT NOT NULL REFERENCES visits(visit_id),
    eye_id TEXT NOT NULL REFERENCES eyes(eye_id),
    exposure_id TEXT NOT NULL REFERENCES product_exposures(exposure_id),
    followup_days INTEGER NOT NULL CHECK (followup_days > 0),
    baseline_al_mm DOUBLE PRECISION NOT NULL,
    followup_al_mm DOUBLE PRECISION NOT NULL,
    delta_al_mm DOUBLE PRECISION NOT NULL,
    annualized_delta_al_mm DOUBLE PRECISION NOT NULL,
    raw_outcome JSONB NOT NULL,
    derived_outcome JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dataset_versions (
    dataset_version_id TEXT PRIMARY KEY,
    version TEXT NOT NULL UNIQUE,
    inclusion_rules JSONB NOT NULL,
    schema_version TEXT NOT NULL,
    feature_set_version TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    case_count INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dataset_cases (
    dataset_version_id TEXT NOT NULL REFERENCES dataset_versions(dataset_version_id),
    case_id TEXT NOT NULL REFERENCES cases(case_id),
    PRIMARY KEY (dataset_version_id, case_id)
);

CREATE TABLE IF NOT EXISTS training_eligibility (
    eligibility_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(case_id),
    status TEXT NOT NULL CHECK (status IN (
        'ELIGIBLE', 'PENDING', 'INCOMPLETE', 'QC_FAILURE',
        'INSUFFICIENT_FOLLOWUP', 'PROTOCOL_DEVIATION', 'EXCLUDED'
    )),
    reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    minimum_followup_days INTEGER NOT NULL,
    evaluated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS training_runs (
    training_run_id TEXT PRIMARY KEY,
    dataset_version_id TEXT NOT NULL REFERENCES dataset_versions(dataset_version_id),
    model_version_id TEXT NOT NULL REFERENCES model_versions(model_version_id),
    algorithm TEXT NOT NULL,
    hyperparameters JSONB NOT NULL,
    random_seed INTEGER NOT NULL,
    objective_function_version TEXT NOT NULL,
    code_version TEXT NOT NULL,
    environment_version TEXT NOT NULL,
    metrics JSONB NOT NULL,
    artifact_uri TEXT NOT NULL,
    artifact_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS validation_runs (
    validation_run_id TEXT PRIMARY KEY,
    model_version_id TEXT NOT NULL REFERENCES model_versions(model_version_id),
    dataset_version_id TEXT NOT NULL REFERENCES dataset_versions(dataset_version_id),
    gate_results JSONB NOT NULL,
    passed BOOLEAN NOT NULL,
    actor_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS model_approvals (
    approval_id TEXT PRIMARY KEY,
    model_version_id TEXT NOT NULL REFERENCES model_versions(model_version_id),
    actor_id TEXT NOT NULL,
    comment TEXT NOT NULL,
    approved_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS model_deployments (
    deployment_id TEXT PRIMARY KEY,
    model_version_id TEXT NOT NULL REFERENCES model_versions(model_version_id),
    previous_model_version_id TEXT REFERENCES model_versions(model_version_id),
    action TEXT NOT NULL CHECK (action IN ('PROMOTE', 'ROLLBACK', 'RETIRE')),
    actor_id TEXT NOT NULL,
    reason TEXT,
    deployed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    parent_event_id TEXT REFERENCES audit_events(event_id),
    previous_version TEXT,
    new_version TEXT,
    reason TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_measurements_visit ON clinical_measurements(visit_id);
CREATE INDEX IF NOT EXISTS idx_visits_case ON visits(case_id, visit_date);
CREATE INDEX IF NOT EXISTS idx_exposures_case_eye ON product_exposures(case_id, eye_id);
CREATE INDEX IF NOT EXISTS idx_outcomes_case_eye ON clinical_outcomes(case_id, eye_id);
CREATE INDEX IF NOT EXISTS idx_audit_object ON audit_events(object_type, object_id, created_at);
