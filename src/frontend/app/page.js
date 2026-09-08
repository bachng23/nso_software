"use client";

import { useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000";

const num = (v) => {
  const n = Number(v);
  return Number.isNaN(n) ? 0 : n;
};
const orNull = (v) => (v === "" || v === undefined || v === null ? null : num(v));

const RESULT_TIERS = {
  ready: { bg: "#ecfdf5", border: "#86efac", lbBg: "#dcfce7", lbBorder: "#22c55e", lbColor: "#166534", title: "#14532d", body: "#166534", label: "READY" },
  caution: { bg: "#fffbeb", border: "#fcd34d", lbBg: "#fef3c7", lbBorder: "#f59e0b", lbColor: "#92400e", title: "#78350f", body: "#92400e", label: "CAUTION" },
  review: { bg: "#fef2f2", border: "#fca5a5", lbBg: "#fee2e2", lbBorder: "#ef4444", lbColor: "#991b1b", title: "#7f1d1d", body: "#991b1b", label: "REVIEW" },
};

const PRIMARY_GOALS = [
  "Myopia Management", "Visual Comfort", "Digital / Near-work Comfort",
  "Binocular Visual Support", "Contrast Optimization", "Balanced Optimization",
];

const INDEX_LABELS = [
  ["refractive_risk", "Refractive Risk"],
  ["binocular_load", "Binocular Load"],
  ["accommodative_stress", "Accommodative Stress"],
  ["spatial_frequency_sensitivity", "Spatial Freq. Sensitivity"],
  ["visual_stress", "Visual Stress"],
  ["neural_adaptation", "Neural Adaptation"],
  ["dynamic_robustness", "Dynamic Robustness"],
  ["interocular_image_balance", "Interocular Image Balance"],
];

const PREDICTED_LABELS = [
  ["myopia_management_fit", "MYOPIA MANAGEMENT FIT"],
  ["visual_comfort", "VISUAL COMFORT"],
  ["adaptation", "ADAPTATION"],
  ["binocular_compatibility", "BINOCULAR COMPAT."],
];

// Kept next to the marked fields so the two cannot drift apart.
const PENDING_HINT =
  "Recorded for research review; it does not affect the current recommendation.";

const scoreLabel = (x) => (x >= 80 ? "Excellent" : x >= 65 ? "Good" : x >= 50 ? "Fair" : "Low");

// --------------------------------------------------------------------------
// Default clinical profile (Tier 1 values are the ones the form starts with).
// --------------------------------------------------------------------------

const DEFAULTS = {
  age: 11,
  odSphere: -3.25, odCyl: -0.5, odAxis: 180, odAl: 25.1,
  osSphere: -3.0, osCyl: -0.25, osAxis: 175, osAl: 24.9,
  photopic_pupil: 5.2,
  near_phoria_direction: "Exo", near_phoria_magnitude: 4, npc: 9, accommodative_lag: 1.1,
  csf_band: "Normal", visual_stress_score: 6,
  near_hours: 7, digital_hours: 5, outdoor_hours: 0.8,
  primary_goal: "Myopia Management",
};

const ADV_DEFAULTS = {
  odBcva: "", osBcva: "", mesopic_pupil: "",
  distance_phoria: "", pfv: "", nfv: "", ac_a: "", stereoacuity: "", ocular_dominance: "Balanced",
  binocular_balance: "Normal",
  amplitude_of_accommodation: "", accommodative_facility: "",
  nra: "", pra: "", bcc: "", mem: "", fixation_disparity: "",
  symptom_questionnaire_score: "",
  near_working_distance: "", computer_working_distance: "",
  visual_comfort_score: "", neural_adaptation_score: "", dynamic_visual_stability: "",
  typical_working_distance: "", night_driving: false, low_light_demand: "Moderate",
};

const RESEARCH_DEFAULTS = {
  csf_low: "", csf_mid: "", csf_high: "",
  // Protocol, so the engine reads the curve at the frequencies the instrument
  // actually used instead of ones it assumed on the clinic's behalf.
  csf_device: "", csf_test_protocol: "", csf_freq_low: "", csf_freq_mid: "",
  csf_freq_high: "", csf_scale: "index_0_100",
  // Structured VEP. A bare number carries no stimulus, unit or normative
  // reference, so it is recorded but cannot raise prediction confidence.
  vep: "", vep_stimulus: "", vep_amplitude_uv: "", vep_latency_ms: "",
  vep_interocular_difference_ms: "", vep_z_score: "",
  erg: "", erg_protocol: "", erg_z_score: "",
  eye_tracking: "", fixation_stability: "", blink_rate: "",
  vergence_stability: "", pupil_dynamics: "", gaze_distribution: "",
  hoa_rms: "", corneal_sa: "", coma: "", trefoil: "",
  corneal_astigmatism: "", corneal_eccentricity: "",
};

const CSF_SCALES = ["index_0_100", "log_cs"];

export default function Page() {
  const [step, setStep] = useState(1);
  const [inp, setInp] = useState(DEFAULTS);
  const [advOpen, setAdvOpen] = useState(false);
  const [resOpen, setResOpen] = useState(false);
  const [adv, setAdv] = useState(ADV_DEFAULTS);
  const [res, setRes] = useState(RESEARCH_DEFAULTS);
  const [pred, setPred] = useState(null);
  const [job, setJob] = useState(null);
  const [approval, setApproval] = useState(null);
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState(null);
  const [fu, setFu] = useState({
    baselineOd: 25.1, followupOd: 25.16, baselineOs: 24.9, followupOs: 24.96,
    baselineOdSphere: -3.25, currentOdSphere: -3.25,
    baselineOsSphere: -3.0, currentOsSphere: -3.0,
    interval: 6, baselineComfort: 7, currentComfort: 7,
    stress: 3, wear: 10, compliance: "Good",
  });
  const [fuRes, setFuRes] = useState(null);

  const set = (k) => (e) => setInp({ ...inp, [k]: e.target.value });
  const setA = (k) => (e) => setAdv({ ...adv, [k]: e.target.value });
  const setR = (k) => (e) => setRes({ ...res, [k]: e.target.value });

  function predictBody() {
    return {
      patient_id: pred?.patient_id || null,
      age: num(inp.age),
      od: {
        sphere: num(inp.odSphere), cylinder: num(inp.odCyl), axis: num(inp.odAxis),
        axial_length: num(inp.odAl), bcva_logmar: orNull(adv.odBcva),
      },
      os: {
        sphere: num(inp.osSphere), cylinder: num(inp.osCyl), axis: num(inp.osAxis),
        axial_length: num(inp.osAl), bcva_logmar: orNull(adv.osBcva),
      },
      photopic_pupil: num(inp.photopic_pupil),
      near_phoria: inp.near_phoria_direction === "Exo"
        ? -Math.abs(num(inp.near_phoria_magnitude))
        : inp.near_phoria_direction === "Eso"
          ? Math.abs(num(inp.near_phoria_magnitude)) : 0,
      npc: num(inp.npc),
      accommodative_lag: num(inp.accommodative_lag),
      csf_band: inp.csf_band,
      visual_stress_score: num(inp.visual_stress_score),
      near_hours: num(inp.near_hours),
      digital_hours: Math.min(num(inp.digital_hours), num(inp.near_hours)),
      outdoor_hours: num(inp.outdoor_hours),
      primary_goal: inp.primary_goal,

      mesopic_pupil: orNull(adv.mesopic_pupil),
      distance_phoria: orNull(adv.distance_phoria),
      pfv: orNull(adv.pfv), nfv: orNull(adv.nfv), ac_a: orNull(adv.ac_a),
      stereoacuity: orNull(adv.stereoacuity),
      ocular_dominance: adv.ocular_dominance,
      binocular_balance: adv.binocular_balance,
      amplitude_of_accommodation: orNull(adv.amplitude_of_accommodation),
      accommodative_facility: orNull(adv.accommodative_facility),
      nra: orNull(adv.nra), pra: orNull(adv.pra), bcc: orNull(adv.bcc), mem: orNull(adv.mem),
      fixation_disparity: orNull(adv.fixation_disparity),
      symptom_questionnaire_score: orNull(adv.symptom_questionnaire_score),
      near_working_distance: orNull(adv.near_working_distance),
      computer_working_distance: orNull(adv.computer_working_distance),
      visual_comfort_score: orNull(adv.visual_comfort_score),
      neural_adaptation_score: orNull(adv.neural_adaptation_score),
      dynamic_visual_stability: orNull(adv.dynamic_visual_stability),
      typical_working_distance: orNull(adv.typical_working_distance),
      night_driving: !!adv.night_driving,
      low_light_demand: adv.low_light_demand,

      csf_low: orNull(res.csf_low), csf_mid: orNull(res.csf_mid), csf_high: orNull(res.csf_high),
      csf_device: res.csf_device || "unspecified",
      csf_test_protocol: res.csf_test_protocol || "unspecified",
      csf_scale: res.csf_scale || "index_0_100",
      // Only send frequencies when all three are given: a partial list would
      // silently misalign the curve.
      csf_frequencies_cpd:
        res.csf_freq_low !== "" && res.csf_freq_mid !== "" && res.csf_freq_high !== ""
          ? [num(res.csf_freq_low), num(res.csf_freq_mid), num(res.csf_freq_high)]
          : null,

      vep: orNull(res.vep),
      vep_stimulus: res.vep_stimulus || "unspecified",
      vep_amplitude_uv: orNull(res.vep_amplitude_uv),
      vep_latency_ms: orNull(res.vep_latency_ms),
      vep_interocular_difference_ms: orNull(res.vep_interocular_difference_ms),
      vep_z_score: orNull(res.vep_z_score),
      erg: orNull(res.erg),
      erg_protocol: res.erg_protocol || "unspecified",
      erg_z_score: orNull(res.erg_z_score),
      eye_tracking: orNull(res.eye_tracking),
      fixation_stability: orNull(res.fixation_stability),
      blink_rate: orNull(res.blink_rate),
      vergence_stability: orNull(res.vergence_stability),
      pupil_dynamics: orNull(res.pupil_dynamics),
      gaze_distribution: orNull(res.gaze_distribution),
      hoa_rms: orNull(res.hoa_rms), corneal_sa: orNull(res.corneal_sa),
      coma: orNull(res.coma), trefoil: orNull(res.trefoil),
      corneal_astigmatism: orNull(res.corneal_astigmatism),
      corneal_eccentricity: orNull(res.corneal_eccentricity),
    };
  }

  async function downloadPdf(path, body, filename) {
    try {
      const r = await fetch(`${API}${path}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(`API error ${r.status}`);
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = filename;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setErr(`Report download failed: ${e}. Is the backend running on ${API}?`);
    }
  }

  const exportPrediction = () =>
    downloadPdf("/api/report/prediction", predictBody(), "nso-report.pdf");

  const downloadFollowupReport = () =>
    downloadPdf("/api/report/followup", {
      ...predictBody(),
      baseline_al: num(fu.baselineOd), followup_al: num(fu.followupOd),
      interval_months: Math.round(num(fu.interval)),
    }, "nso-report.pdf");

  async function runPredict() {
    setLoading(true);
    setProgress("Validating clinical inputs");
    setErr(null);
    setJob(null);
    setApproval(null);
    const stages = ["Validating clinical inputs", "Evaluating patient phenotype", "Preparing recommendation"];
    let stage = 0;
    const progressTimer = window.setInterval(() => {
      stage = Math.min(stage + 1, stages.length - 1);
      setProgress(stages[stage]);
    }, 450);
    try {
      const r = await fetch(`${API}/api/predict`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(predictBody()),
      });
      if (!r.ok) throw new Error(`API error ${r.status}`);
      setPred(await r.json());
      setStep(2);
      window.scrollTo(0, 0);
    } catch (e) {
      setErr(`${e}. Is the backend running on ${API}?`);
    } finally {
      window.clearInterval(progressTimer);
      setProgress("");
      setLoading(false);
    }
  }

  async function approveDesign() {
    if (!pred) return;
    setSubmitting(true);
    setErr(null);
    try {
      const r = await fetch(`${API}/api/design/approve`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ design_id: pred.design_id }),
      });
      if (!r.ok) throw new Error(`API error ${r.status}`);
      setApproval(await r.json());
    } catch (e) {
      setErr(`Approval failed: ${e}.`);
    } finally {
      setSubmitting(false);
    }
  }

  async function submitToManufacturing() {
    if (!pred) return;
    setSubmitting(true);
    setErr(null);
    try {
      const r = await fetch(`${API}/api/manufacturing/submit`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ design_id: pred.design_id }),
      });
      if (!r.ok) throw new Error(`API error ${r.status}`);
      setJob(await r.json());
    } catch (e) {
      setErr(`Manufacturing submission failed: ${e}.`);
    } finally {
      setSubmitting(false);
    }
  }

  async function runRefit() {
    if (!pred) return;
    setSubmitting(true);
    setErr(null);
    try {
      const r = await fetch(`${API}/api/refit`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...predictBody(),
          previous_design_id: pred.design_id,
          baseline_al: Math.max(num(fu.baselineOd), num(fu.baselineOs)),
          followup_al: Math.max(num(fu.followupOd), num(fu.followupOs)),
          interval_months: Math.round(num(fu.interval)),
        }),
      });
      if (!r.ok) throw new Error(`API error ${r.status}`);
      setPred(await r.json());
      setJob(null);
      setStep(2);
      window.scrollTo(0, 0);
    } catch (e) {
      setErr(`Re-fit failed: ${e}.`);
    } finally {
      setSubmitting(false);
    }
  }

  async function runFollowup(next) {
    const s = next || fu;
    try {
      const r = await fetch(`${API}/api/followup`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          design_id: pred?.design_id,
          patient_id: pred?.patient_id,
          baseline_od_al: num(s.baselineOd), followup_od_al: num(s.followupOd),
          baseline_os_al: num(s.baselineOs), followup_os_al: num(s.followupOs),
          interval_months: Math.round(num(s.interval)),
          current_support_level: "Level 2",
          baseline_comfort: num(s.baselineComfort), current_comfort: num(s.currentComfort),
          visual_stress_score: num(s.stress), average_wear_hours: num(s.wear),
          compliance: s.compliance,
          baseline_od_refraction: { sphere: num(s.baselineOdSphere) },
          followup_od_refraction: { sphere: num(s.currentOdSphere) },
          baseline_os_refraction: { sphere: num(s.baselineOsSphere) },
          followup_os_refraction: { sphere: num(s.currentOsSphere) },
        }),
      });
      if (r.ok) setFuRes(await r.json());
    } catch (e) { /* ignore transient */ }
  }

  function goFollowup() {
    setStep(3);
    window.scrollTo(0, 0);
    runFollowup();
  }

  const setFuVal = (k) => (e) => {
    const nextFu = { ...fu, [k]: e.target.value };
    setFu(nextFu);
    runFollowup(nextFu);
  };

  return (
    <div className="wrap">
      <div className="topbar">
        <div className="brand">
          <span className="brand-mark" />
          <span className="brand-name">NSO AI-PC Fitting Platform</span>
          <span className="brand-ver">V2 · Research Prototype</span>
        </div>
        <div className="badge">
          <span className="badge-dot" />
          <span className="badge-text">Rule-based</span>
        </div>
      </div>

      {err && <div className="err">{err}</div>}

      {step === 1 && (
        <Screen1
          inp={inp} set={set} setInp={setInp}
          adv={adv} setA={setA} setAdv={setAdv} advOpen={advOpen} setAdvOpen={setAdvOpen}
          res={res} setR={setR} resOpen={resOpen} setResOpen={setResOpen}
          loading={loading} progress={progress} run={runPredict}
          go={(i) => (i === 2 ? goFollowup() : i === 1 && pred && setStep(2))} />
      )}
      {step === 2 && pred && (
        <Screen2 pred={pred} job={job} approval={approval} submitting={submitting}
          onApprove={approveDesign}
          onSubmit={submitToManufacturing}
          go={(i) => (i === 0 ? setStep(1) : i === 2 ? goFollowup() : null)}
          onFollowup={goFollowup} onExport={exportPrediction} />
      )}
      {step === 3 && (
        <Screen3 fu={fu} setFuVal={setFuVal} res={fuRes} designId={pred?.design_id} onDownload={downloadFollowupReport}
          onRefit={runRefit} canRefit={!!pred} refitting={submitting}
          go={(i) => (i === 0 ? setStep(1) : i === 1 ? setStep(2) : null)} />
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// Screen 1 — three tiers.
//
// Tier 1 (Quick Fitting) is the default view and stays at ~13 inputs. Tiers 2
// and 3 are collapsed; nothing in any tier is a design parameter, because the
// design engine is not steerable from the client.
// --------------------------------------------------------------------------

function Crumbs({ step, go }) {
  const items = ["Clinical input", "Personalized design", "Follow-up visit"];
  const active = step - 1;
  return (
    <div className="crumbs">
      {items.map((label, i) => (
        <span key={label} style={{ display: "flex", alignItems: "center", gap: 6 }}>
          {i > 0 && <span className="crumb-sep">→</span>}
          {i === active ? <span className="crumb active">{label}</span>
            : <button className="crumb" onClick={() => go(i)}>{label}</button>}
        </span>
      ))}
    </div>
  );
}

function Screen1({ inp, set, setInp, adv, setA, setAdv, advOpen, setAdvOpen,
  res, setR, resOpen, setResOpen, loading, progress, run, go }) {
  return (
    <div>
      <Crumbs step={1} go={go} />
      <h1 className="title">Clinical Decision Support</h1>
      <p className="sub">
        Quick Fitting needs the core clinical profile only. Extended measurements are
        optional — they raise prediction confidence but are not required.
      </p>

      <div className="card lift" style={{ marginTop: 20 }}>
        <TierHead n="1" title="QUICK FITTING" note="Default — everything the engine needs" />

        <div className="cap cap-sm" style={{ margin: "18px 0 12px" }}>PATIENT &amp; REFRACTION</div>
        <div className="grid2">
          <NumField label="Age" unit="years" step="1" value={inp.age} onChange={set("age")} />
          <NumField label="Photopic pupil" unit="mm" step="0.1" value={inp.photopic_pupil} onChange={set("photopic_pupil")} />
        </div>

        <div className="eye-grid" style={{ marginTop: 14 }}>
          <EyeBlock eye="OD — Right eye" prefix="od" inp={inp} set={set} />
          <EyeBlock eye="OS — Left eye" prefix="os" inp={inp} set={set} />
        </div>

        <div className="divider" style={{ margin: "22px 0" }} />
        <div className="cap cap-sm" style={{ marginBottom: 12 }}>BINOCULAR &amp; ACCOMMODATION</div>
        <div className="grid2">
          <SelectField label="Near phoria direction" unit="" value={inp.near_phoria_direction}
            onChange={set("near_phoria_direction")} options={["Exo", "Ortho", "Eso"]} />
          <NumField label="Near phoria magnitude" unit="Δ" step="0.5"
            value={inp.near_phoria_magnitude} onChange={set("near_phoria_magnitude")} />
          <NumField label="NPC" unit="cm" step="0.5" value={inp.npc} onChange={set("npc")} />
          <NumField label="Accommodative lag" unit="D" step="0.05" value={inp.accommodative_lag} onChange={set("accommodative_lag")} />
          <SelectField label="Contrast sensitivity (CSF)" unit="band" value={inp.csf_band}
            onChange={set("csf_band")} options={[
              "Normal", "Mildly Reduced", "Moderately Reduced",
              "Significantly Reduced", "Not Tested",
            ]} />
        </div>

        <div className="divider" style={{ margin: "22px 0" }} />
        <div className="cap cap-sm" style={{ marginBottom: 18 }}>VISUAL TASK &amp; ENVIRONMENT</div>
        <div className="grid2" style={{ gap: "24px 32px" }}>
          <Slider label="Visual stress" unit="/ 10" min="0" max="10" step="1"
            value={inp.visual_stress_score} onChange={set("visual_stress_score")} hint="Patient-reported symptom load" />
          <Slider label="Total near load" unit="h/day" min="0" max="14" step="0.5"
            value={inp.near_hours} onChange={set("near_hours")} hint="All close work, including screens" />
          <Slider label="Digital portion of near load" unit="h/day" min="0" max={inp.near_hours || 0} step="0.5"
            value={Math.min(num(inp.digital_hours), num(inp.near_hours))} onChange={set("digital_hours")} />
          <Slider label="Outdoor activity" unit="h/day" min="0" max="8" step="0.5"
            value={inp.outdoor_hours} onChange={set("outdoor_hours")} hint="Target ≥ 2 h/day" />
        </div>
        <div style={{ marginTop: 20, maxWidth: "50%" }}>
          <SelectField label="Primary visual goal" unit="optimization target"
            value={inp.primary_goal} onChange={set("primary_goal")} options={PRIMARY_GOALS} />
        </div>

        <div className="divider" style={{ margin: "24px 0 16px" }} />

        <Disclosure open={advOpen} toggle={() => setAdvOpen(!advOpen)}
          label="Tier 2 · Advanced Clinical Data"
          note="Full binocular, accommodative and neurovisual workup">
          <div className="cap cap-sm" style={{ margin: "6px 0 12px" }}>BINOCULAR VISION</div>
          <div className="grid2">
            <NumField label="Distance phoria" unit="Δ · optional" step="0.5"
              value={adv.distance_phoria} onChange={setA("distance_phoria")} />
            <NumField label="Stereoacuity" unit="arc sec · optional" step="5" value={adv.stereoacuity} onChange={setA("stereoacuity")} />
            <NumField label="PFV" unit="Δ · optional" step="1" value={adv.pfv} onChange={setA("pfv")} />
            <NumField label="NFV" unit="Δ · optional" step="1" value={adv.nfv} onChange={setA("nfv")} />
            <NumField label="AC/A ratio" unit="Δ/D · optional" step="0.5" value={adv.ac_a} onChange={setA("ac_a")} />
            <SelectField label="Ocular dominance" unit="" pending value={adv.ocular_dominance}
              onChange={setA("ocular_dominance")} options={["Balanced", "OD", "OS"]} />
            <SelectField label="Binocular balance" unit="" value={adv.binocular_balance}
              onChange={setA("binocular_balance")} options={["Normal", "Mild", "Significant"]} />
            <NumField label="Fixation disparity" unit="Δ · optional" step="0.25"
              value={adv.fixation_disparity} onChange={setA("fixation_disparity")} />
            <NumField label="Symptom questionnaire" unit="score · optional" step="1"
              value={adv.symptom_questionnaire_score} onChange={setA("symptom_questionnaire_score")} />
            <NumField label="BCVA OD" unit="logMAR · optional" step="0.05" value={adv.odBcva} onChange={setA("odBcva")} />
            <NumField label="BCVA OS" unit="logMAR · optional" step="0.05" value={adv.osBcva} onChange={setA("osBcva")} />
          </div>

          <div className="cap cap-sm" style={{ margin: "20px 0 12px" }}>ACCOMMODATION</div>
          <div className="grid2">
            <NumField label="Amplitude of accommodation" unit="D · optional" step="0.5"
              value={adv.amplitude_of_accommodation} onChange={setA("amplitude_of_accommodation")} />
            <NumField label="Accommodative facility" unit="cpm · optional" step="1"
              value={adv.accommodative_facility} onChange={setA("accommodative_facility")} />
            <NumField label="NRA" unit="D · optional" step="0.25" value={adv.nra} onChange={setA("nra")} />
            <NumField label="PRA" unit="D · optional" step="0.25" value={adv.pra} onChange={setA("pra")} />
            <NumField label="BCC" unit="D · optional" step="0.25" value={adv.bcc} onChange={setA("bcc")} />
            <NumField label="MEM" unit="D · optional" step="0.25" value={adv.mem} onChange={setA("mem")} />
            <NumField label="Near working distance" unit="cm · optional" step="1"
              value={adv.near_working_distance} onChange={setA("near_working_distance")} />
            <NumField label="Computer working distance" unit="cm · optional" step="1"
              value={adv.computer_working_distance} onChange={setA("computer_working_distance")} />
          </div>

          <div className="cap cap-sm" style={{ margin: "20px 0 12px" }}>NEUROVISUAL &amp; ENVIRONMENT</div>
          <div className="grid2">
            <NumField label="Visual comfort score" unit="0–10 · optional" step="1"
              value={adv.visual_comfort_score} onChange={setA("visual_comfort_score")} />
            <NumField label="Neural adaptation score" unit="0–10 · optional" step="1"
              value={adv.neural_adaptation_score} onChange={setA("neural_adaptation_score")} />
            <NumField label="Dynamic visual stability" unit="0–10 · optional" step="1"
              value={adv.dynamic_visual_stability} onChange={setA("dynamic_visual_stability")} />
            <NumField label="Mesopic pupil" unit="mm · optional" step="0.1"
              value={adv.mesopic_pupil} onChange={setA("mesopic_pupil")} />
            <NumField label="Typical working distance" unit="cm · optional" step="1"
              value={adv.typical_working_distance} onChange={setA("typical_working_distance")} />
            <SelectField label="Low-light visual demand" unit="" value={adv.low_light_demand}
              onChange={setA("low_light_demand")} options={["Low", "Moderate", "High"]} />
          </div>
          <label className="check">
            <input type="checkbox" checked={adv.night_driving}
              onChange={(e) => setAdv({ ...adv, night_driving: e.target.checked })} />
            <span>Night driving</span>
          </label>
        </Disclosure>

        <Disclosure open={resOpen} toggle={() => setResOpen(!resOpen)}
          label="Tier 3 · Research Mode"
          note="CSF protocol, electrophysiology, eye tracking, wavefront">
          <div className="cap cap-sm" style={{ margin: "6px 0 12px" }}>SPATIAL FREQUENCY CURVE</div>
          <div className="grid2">
            <NumField label="Low spatial frequency CSF" unit="optional" step="1" value={res.csf_low} onChange={setR("csf_low")} />
            <NumField label="Mid spatial frequency CSF" unit="optional" step="1" value={res.csf_mid} onChange={setR("csf_mid")} />
            <NumField label="High spatial frequency CSF" unit="optional" step="1" value={res.csf_high} onChange={setR("csf_high")} />
            <SelectField label="Value scale" unit="what the three numbers are"
              value={res.csf_scale} onChange={setR("csf_scale")} options={CSF_SCALES} />
          </div>

          <div className="cap cap-sm" style={{ margin: "18px 0 10px" }}>CSF TEST PROTOCOL</div>
          <div className="grid2">
            <TextField label="Device" unit="e.g. CSV-1000, qCSF" value={res.csf_device} onChange={setR("csf_device")} />
            <TextField label="Test protocol" unit="e.g. photopic, mesopic" value={res.csf_test_protocol} onChange={setR("csf_test_protocol")} />
            <NumField label="Low frequency" unit="cycles/degree" step="0.1" value={res.csf_freq_low} onChange={setR("csf_freq_low")} />
            <NumField label="Mid frequency" unit="cycles/degree" step="0.1" value={res.csf_freq_mid} onChange={setR("csf_freq_mid")} />
            <NumField label="High frequency" unit="cycles/degree" step="0.1" value={res.csf_freq_high} onChange={setR("csf_freq_high")} />
          </div>
          <span className="note" style={{ fontSize: 11, display: "block", marginTop: 8 }}>
            A measured curve overrides the Quick Fitting CSF band. Give the three
            frequencies if you know them — AUC and slope are computed on the
            frequency axis, so without them the engine has to assume 1.5 / 6 / 18 cpd
            and the result is flagged as assumed.
          </span>

          <div className="cap cap-sm" style={{ margin: "20px 0 12px" }}>VISUAL EVOKED POTENTIAL</div>
          <div className="grid2">
            <TextField label="Stimulus" unit="e.g. pattern-reversal 1°" value={res.vep_stimulus} onChange={setR("vep_stimulus")} />
            <NumField label="Amplitude" unit="µV · optional" step="0.1" value={res.vep_amplitude_uv} onChange={setR("vep_amplitude_uv")} />
            <NumField label="Latency" unit="ms · optional" step="0.1" value={res.vep_latency_ms} onChange={setR("vep_latency_ms")} />
            <NumField label="Interocular difference" unit="ms · optional" step="0.1"
              value={res.vep_interocular_difference_ms} onChange={setR("vep_interocular_difference_ms")} />
            <NumField label="Normative Z-score" unit="vs. lab norms" step="0.1" value={res.vep_z_score} onChange={setR("vep_z_score")} />
            <NumField label="VEP (unstructured)" unit="legacy · recorded only" step="0.1" pending
              value={res.vep} onChange={setR("vep")} />
          </div>
          <span className="note" style={{ fontSize: 11, display: "block", marginTop: 8 }}>
            Only the Z-score is interpretable on its own, so only it raises prediction
            confidence. The rest is recorded for the future dataset.
          </span>

          <div className="cap cap-sm" style={{ margin: "20px 0 12px" }}>ERG &amp; EYE TRACKING</div>
          <div className="grid2">
            <TextField label="ERG protocol" unit="e.g. ISCEV standard" value={res.erg_protocol} onChange={setR("erg_protocol")} />
            <NumField label="ERG Z-score" unit="vs. lab norms" step="0.1" value={res.erg_z_score} onChange={setR("erg_z_score")} />
            <NumField label="Fixation stability" unit="arcmin · optional" step="0.5" value={res.fixation_stability} onChange={setR("fixation_stability")} />
            <NumField label="Blink rate" unit="blinks/min · optional" step="1" value={res.blink_rate} onChange={setR("blink_rate")} />
            <NumField label="Vergence stability" unit="0–10 · optional" step="0.5" value={res.vergence_stability} onChange={setR("vergence_stability")} />
            <NumField label="Pupil dynamics" unit="0–10 · optional" step="0.5" pending
              value={res.pupil_dynamics} onChange={setR("pupil_dynamics")} />
            <NumField label="Gaze distribution" unit="0–10 · optional" step="0.5" pending
              value={res.gaze_distribution} onChange={setR("gaze_distribution")} />
            <NumField label="ERG (unstructured)" unit="legacy · recorded only" step="0.1" pending
              value={res.erg} onChange={setR("erg")} />
            <NumField label="Eye tracking (unstructured)" unit="legacy · recorded only" step="0.1" pending
              value={res.eye_tracking} onChange={setR("eye_tracking")} />
          </div>

          <div className="cap cap-sm" style={{ margin: "20px 0 12px" }}>WAVEFRONT</div>
          <div className="grid2">
            <NumField label="HOA RMS" unit="µm · optional" step="0.01" value={res.hoa_rms} onChange={setR("hoa_rms")} />
            <NumField label="Corneal SA" unit="µm · optional" step="0.01" value={res.corneal_sa} onChange={setR("corneal_sa")} />
            <NumField label="Coma" unit="µm · optional" step="0.01" value={res.coma} onChange={setR("coma")} />
            <NumField label="Trefoil" unit="µm · optional" step="0.01" value={res.trefoil} onChange={setR("trefoil")} />
            <NumField label="Corneal astigmatism" unit="D · optional" step="0.25"
              value={res.corneal_astigmatism} onChange={setR("corneal_astigmatism")} />
            <NumField label="Corneal eccentricity" unit="e · optional" step="0.01" pending
              value={res.corneal_eccentricity} onChange={setR("corneal_eccentricity")} />
          </div>
        </Disclosure>

        <div className="divider" style={{ margin: "20px 0" }} />
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16 }}>
          <span className="note">{loading ? progress : "The recommendation is prepared securely from the clinical dataset."}</span>
          <button className="btn btn-primary" disabled={loading} onClick={run}>
            {loading ? "Working…" : "Generate Personalized Design"}
          </button>
        </div>
      </div>
    </div>
  );
}

function TierHead({ n, title, note }) {
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
      <span className="tier-chip">TIER {n}</span>
      <span className="cap">{title}</span>
      <span className="note" style={{ marginLeft: "auto" }}>{note}</span>
    </div>
  );
}

function Disclosure({ open, toggle, label, note, children }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <button className="crumb" style={{ font: "500 12px Inter", color: "#78716c" }} onClick={toggle}>
        {open ? "▾" : "▸"} {label}
      </button>
      <span className="note" style={{ fontSize: 11, marginLeft: 8 }}>{note}</span>
      {open && <div style={{ marginTop: 14, paddingLeft: 2 }}>{children}</div>}
    </div>
  );
}

function EyeBlock({ eye, prefix, inp, set }) {
  const k = (s) => `${prefix}${s}`;
  return (
    <div className="eye-card">
      <div className="cap cap-sm" style={{ marginBottom: 12 }}>{eye}</div>
      <div className="grid2">
        <NumField label="Sphere" unit="D" step="0.25" value={inp[k("Sphere")]} onChange={set(k("Sphere"))} />
        <NumField label="Cylinder" unit="D" step="0.25" value={inp[k("Cyl")]} onChange={set(k("Cyl"))} />
        <NumField label="Axis" unit="°" step="1" value={inp[k("Axis")]} onChange={set(k("Axis"))} />
        <NumField label="Axial length" unit="mm" step="0.01" value={inp[k("Al")]} onChange={set(k("Al"))} />
      </div>
    </div>
  );
}

// `pending` marks an input the engine accepts and stores but does not yet use.
// Showing that is not cosmetic: an unmarked dead field makes a clinician
// believe a measurement influenced the design when it did not.
function NumField({ label, unit, step, value, onChange, pending }) {
  return (
    <div className={pending ? "field field-pending" : "field"}>
      <label>
        {label}{unit ? <span className="unit"> · {unit}</span> : null}
        {pending && <span className="pending-tag" title={PENDING_HINT}>not yet used</span>}
      </label>
      <input type="number" step={step} value={value} onChange={onChange} />
    </div>
  );
}

function TextField({ label, unit, value, onChange, placeholder }) {
  return (
    <div className="field">
      <label>{label}{unit ? <span className="unit"> · {unit}</span> : null}</label>
      <input type="text" value={value} onChange={onChange} placeholder={placeholder} />
    </div>
  );
}

function SelectField({ label, unit, value, onChange, options, pending }) {
  return (
    <div className={pending ? "field field-pending" : "field"}>
      <label>
        {label}{unit ? <span className="unit"> · {unit}</span> : null}
        {pending && <span className="pending-tag" title={PENDING_HINT}>not yet used</span>}
      </label>
      <select value={value} onChange={onChange}>
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </div>
  );
}

function ReadRow({ label, value }) {
  return (
    <div style={{
      display: "flex", justifyContent: "space-between", alignItems: "baseline",
      padding: "7px 0", borderBottom: "1px solid var(--border)",
    }}>
      <span style={{ font: "400 12px Inter", color: "var(--warm)" }}>{label}</span>
      <span style={{ font: "500 12px Inter", color: "var(--ink)" }}>{value}</span>
    </div>
  );
}

function Slider({ label, unit, min, max, step, value, onChange, hint }) {
  return (
    <div className="slider">
      <div className="slider-head">
        <label>{label}</label>
        <span className="slider-val">{value} <span className="unit">{unit}</span></span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value} onChange={onChange} />
      {hint && <span className="note" style={{ fontSize: 11 }}>{hint}</span>}
    </div>
  );
}

// --------------------------------------------------------------------------
// Screen 2 — clinical results.
//
// The results screen follows the explicit clinical response contract.
// --------------------------------------------------------------------------

function Screen2({ pred, job, approval, submitting, onApprove, onSubmit, go, onFollowup, onExport }) {
  const conf = pred.prediction_confidence;
  const tierKey = conf < 70 ? "review" : conf < 82 ? "caution" : "ready";
  const t = RESULT_TIERS[tierKey];

  return (
    <div>
      <Crumbs step={2} go={go} />
      <h1 className="title" style={{ fontSize: 28, marginBottom: 2 }}>Individual Visual Phenotype</h1>
      <p className="sub" style={{ fontSize: 13, marginBottom: 14 }}>
        Decision-support scores are relative fit indicators, not efficacy probabilities.
      </p>

      <div className="design-hero">
        <div>
          <div className="metric-cap">RECOMMENDED PERSONALIZED OPTICAL DESIGN</div>
          <div className="design-id">{pred.design_id}</div>
          <div className="metric-sub">
            Phenotype {pred.phenotype.code} · {pred.binocular_pair} pair · optimized for {pred.primary_goal.toLowerCase()}
          </div>
          {pred.refit && (
            <div className="refit-note">
              Revision {pred.refit.revision} · re-fitted from {pred.refit.previous_design_id} after{" "}
              {pred.refit.annualized_delta_al.toFixed(2)} mm/yr ({pred.refit.progression_band.toLowerCase()})
            </div>
          )}
        </div>
        <div className="design-eyes">
          <div><span className="eye-tag">OD</span> {pred.eyes.OD.profile_label}</div>
          <div><span className="eye-tag">OS</span> {pred.eyes.OS.profile_label}</div>
        </div>
      </div>

      <div className="cap cap-sm" style={{ margin: "20px 0 10px" }}>VISUAL PHENOTYPE — FIVE DOMAINS</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 12, marginBottom: 20 }}>
        {pred.phenotype.domains.map((d) => (
          <div key={d.key} className="card" style={{ padding: "14px 16px" }}>
            <div className="metric-cap" style={{ marginBottom: 6 }}>{d.key} — {d.name}</div>
            <div className="metric-big" style={{ fontSize: 26, lineHeight: 1 }}>{d.score}</div>
            <div className="metric-sub" style={{ marginTop: 6 }}>{d.grade_label} load</div>
          </div>
        ))}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 16 }}>
        <div className="card" style={{ padding: 20 }}>
          <div className="cap cap-sm" style={{ marginBottom: 14 }}>AI-DERIVED VISUAL INDICES</div>
          {INDEX_LABELS.map(([key, label]) => (
            <div key={key} style={{ display: "grid", gridTemplateColumns: "150px 1fr 38px", alignItems: "center", gap: 10, marginBottom: 9 }}>
              <span style={{ font: "400 12px Inter", color: "var(--warm)" }}>{label}</span>
              <div className="bar-track" style={{ marginBottom: 0 }}>
                <div className="bar-fill" style={{ width: `${pred.indices[key]}%` }} />
              </div>
              <span style={{ font: "500 12px Inter", textAlign: "right" }}>{Math.round(pred.indices[key])}</span>
            </div>
          ))}
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div className="card" style={{ padding: 20 }}>
            <div className="cap cap-sm" style={{ marginBottom: 14 }}>PREDICTED PERFORMANCE</div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 12 }}>
              {PREDICTED_LABELS.map(([key, label]) => (
                <div key={key}>
                  <div className="metric-cap" style={{ marginBottom: 4 }}>{label}</div>
                  <div className="metric-big" style={{ fontSize: 24, lineHeight: 1 }}>{pred.predicted[key]}/100</div>
                  <div className="metric-sub" style={{ marginTop: 3 }}>{scoreLabel(pred.predicted[key])}</div>
                </div>
              ))}
            </div>
          </div>
          <div className="banner" style={{ background: t.bg, border: `1px solid ${t.border}`, padding: "12px 14px" }}>
            <span className="banner-label" style={{ color: t.lbColor, background: t.lbBg, border: `1px solid ${t.lbBorder}` }}>{t.label}</span>
            <div>
              <div className="banner-title" style={{ color: t.title, fontSize: 13 }}>
                Prediction confidence {conf}% · review in {pred.recommended_follow_up}
              </div>
              <div className="banner-body" style={{ color: t.body, fontSize: 12 }}>
                {tierKey === "ready"
                  ? "Extended measurements support this fit. Proceed and re-check at the scheduled interval."
                  : "Adding Tier 2 / Tier 3 measurements would raise confidence before committing to this fit."}
              </div>
            </div>
          </div>
        </div>
      </div>

      {pred.spatial_frequency_descriptors.csf_auc !== null && (
        <div className="card" style={{ padding: 20, marginBottom: 16 }}>
          <div className="cap cap-sm" style={{ marginBottom: 12 }}>
            SPATIAL FREQUENCY PROFILE · AUTO-CALCULATED
          </div>
          <div className="grid2">
            <ReadRow label="CSF AUC" value={pred.spatial_frequency_descriptors.csf_auc} />
            <ReadRow label="CSF slope" value={`${pred.spatial_frequency_descriptors.csf_slope} / decade`} />
            <ReadRow label="Sensitivity centroid" value={`${pred.spatial_frequency_descriptors.csf_centroid_cpd} cpd`} />
            {pred.interocular_acuity_difference !== null && (
              <ReadRow label="Interocular VA difference" value={`${pred.interocular_acuity_difference} logMAR`} />
            )}
          </div>
        </div>
      )}

      <div className="card" style={{ padding: 20, marginBottom: 16 }}>
        <div className="cap cap-sm" style={{ marginBottom: 12 }}>WHY THIS RECOMMENDATION</div>
        <ul className="reasons">
          {pred.explainable_summary.map((line, i) => <li key={i}>{line}</li>)}
        </ul>
        <div className="ip-note">Authorized record <strong>{pred.design_id}</strong> is held in the secure vault.</div>
      </div>

      {job && (
        <div className="card" style={{ padding: 20, marginBottom: 16 }}>
          <div className="cap cap-sm" style={{ marginBottom: 12 }}>MANUFACTURING JOB</div>
          <div className="grid2" style={{ marginBottom: 12 }}>
            <div>
              <div className="metric-cap" style={{ marginBottom: 4 }}>JOB ID</div>
              <div className="metric-big" style={{ fontSize: 20 }}>{job.job_id}</div>
            </div>
            <div>
              <div className="metric-cap" style={{ marginBottom: 4 }}>STATUS</div>
              <div className="metric-big" style={{ fontSize: 20 }}>{job.status}</div>
            </div>
          </div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            {job.segments.map((s) => (
              <div key={s.segment} className="segment-chip">
                <span className="eye-tag">{s.segment}</span> {s.package} · {s.status}
              </div>
            ))}
          </div>
          <div className="note" style={{ marginTop: 10 }}>
            Released to authorized manufacturers over an encrypted API, one segment each.
            No single vendor receives the complete design.
          </div>
        </div>
      )}

      <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 10, marginTop: 12 }}>
        <button className="btn btn-ghost" onClick={onExport}>Export clinical report ⤓</button>
        <button className="btn btn-ghost" disabled={submitting || !!approval} onClick={onApprove}>
          {approval ? "Approved ✓" : submitting ? "Working…" : "Approve Design"}
        </button>
        <button className="btn btn-ghost" disabled={submitting || !!job || !approval} onClick={onSubmit}>
          {job ? "Submitted ✓" : submitting ? "Submitting…" : "Send Approved Design"}
        </button>
        <button className="btn btn-primary" onClick={onFollowup}>Track follow-up →</button>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------
// Screen 3 — follow-up. Support levels, not design tiers.
// --------------------------------------------------------------------------

const FlowStep = ({ label, value, highlight }) => (
  <div style={{
    flex: "1 1 0", minWidth: 110, background: highlight ? "#e6f1fb" : "var(--track)",
    border: highlight ? "1px solid #3398e1" : "1px solid var(--border)",
    borderRadius: 8, padding: "10px 12px",
  }}>
    <div style={{ font: "600 10px Inter", letterSpacing: "0.06em", color: "var(--warm)", marginBottom: 4 }}>
      {label.toUpperCase()}
    </div>
    <div style={{ font: "500 16px Inter", color: highlight ? "#185fa5" : "var(--ink)" }}>{value}</div>
  </div>
);

const Arrow = () => <span style={{ color: "var(--ash)", fontSize: 18 }}>→</span>;

const FU_TIERS = {
  Controlled: { key: "STABLE", ...RESULT_TIERS.ready,
    body: (a) => `Annualized progression is ${a} mm/year — within the controlled band. Keep the current design and re-check at the next scheduled interval.` },
  Borderline: { key: "MONITOR", ...RESULT_TIERS.caution,
    body: (a) => `Annualized progression is ${a} mm/year — mild. Step the optical support up one level and shorten the review interval to confirm the response.` },
  Progressing: { key: "ESCALATE", ...RESULT_TIERS.review,
    body: (a) => `Annualized progression is ${a} mm/year — fast. Move to maximum optical support and book a clinical review to rule out contributing factors.` },
};

function Metric({ cap, big, sub }) {
  return (
    <div className="card" style={{ padding: 20 }}>
      <div className="metric-cap">{cap}</div>
      <div className="metric-big">{big}</div>
      <div className="metric-sub">{sub}</div>
    </div>
  );
}

function Screen3({ fu, setFuVal, res, designId, go, onDownload, onRefit, canRefit, refitting }) {
  const fmt = (x) => `${x >= 0 ? "+" : "−"}${Math.abs(x).toFixed(2)}`;
  const tier = res ? FU_TIERS[res.progression_band] : null;

  return (
    <div>
      <Crumbs step={3} go={go} />
      <h1 className="title">Follow-up visit</h1>
      <p className="sub">
        Record binocular change, comfort and actual exposure. The system supports the
        clinician&apos;s decision and does not make an autonomous diagnosis.
      </p>
      {designId && <div className="note" style={{ marginTop: 8 }}>Original design: {designId}</div>}

      <div className="card lift" style={{ marginTop: 20, marginBottom: 16 }}>
        <div className="cap" style={{ marginBottom: 16 }}>AXIAL-LENGTH READINGS · OD / OS</div>
        <div className="grid2" style={{ marginBottom: 24 }}>
          <NumField label="OD baseline AL" unit="mm" step="0.01" value={fu.baselineOd} onChange={setFuVal("baselineOd")} />
          <NumField label="OD follow-up AL" unit="mm" step="0.01" value={fu.followupOd} onChange={setFuVal("followupOd")} />
          <NumField label="OS baseline AL" unit="mm" step="0.01" value={fu.baselineOs} onChange={setFuVal("baselineOs")} />
          <NumField label="OS follow-up AL" unit="mm" step="0.01" value={fu.followupOs} onChange={setFuVal("followupOs")} />
        </div>
        <div className="slider" style={{ maxWidth: "50%" }}>
          <div className="slider-head">
            <label>Follow-up interval</label>
            <span className="slider-val">{fu.interval} <span className="unit">months</span></span>
          </div>
          <input type="range" min="1" max="12" step="1" value={fu.interval} onChange={setFuVal("interval")} />
        </div>
        <div className="divider" style={{ margin: "22px 0" }} />
        <div className="cap cap-sm" style={{ marginBottom: 12 }}>REFRACTION</div>
        <div className="grid2">
          <NumField label="OD baseline sphere" unit="D" step="0.25" value={fu.baselineOdSphere} onChange={setFuVal("baselineOdSphere")} />
          <NumField label="OD current sphere" unit="D" step="0.25" value={fu.currentOdSphere} onChange={setFuVal("currentOdSphere")} />
          <NumField label="OS baseline sphere" unit="D" step="0.25" value={fu.baselineOsSphere} onChange={setFuVal("baselineOsSphere")} />
          <NumField label="OS current sphere" unit="D" step="0.25" value={fu.currentOsSphere} onChange={setFuVal("currentOsSphere")} />
        </div>
        <div className="divider" style={{ margin: "22px 0" }} />
        <div className="cap cap-sm" style={{ marginBottom: 12 }}>COMFORT &amp; EXPOSURE</div>
        <div className="grid2">
          <NumField label="Baseline comfort" unit="/ 10" step="1" value={fu.baselineComfort} onChange={setFuVal("baselineComfort")} />
          <NumField label="Current comfort" unit="/ 10" step="1" value={fu.currentComfort} onChange={setFuVal("currentComfort")} />
          <NumField label="Visual stress" unit="/ 10" step="1" value={fu.stress} onChange={setFuVal("stress")} />
          <NumField label="Average wear" unit="h/day" step="0.5" value={fu.wear} onChange={setFuVal("wear")} />
          <SelectField label="Compliance" unit="" value={fu.compliance}
            onChange={setFuVal("compliance")} options={["Good", "Partial", "Poor", "Unknown"]} />
        </div>
      </div>

      <div className="grid2" style={{ marginBottom: 16 }}>
        <Metric cap="DELTA AL" big={res ? `${fmt(res.delta_al)} mm` : "—"} sub="Baseline → follow-up change" />
        <Metric cap="ANNUALIZED DELTA AL" big={res ? `${fmt(res.annualized_delta_al)} mm/yr` : "—"} sub="Progression rate, interval-normalized" />
      </div>

      {res && (
        <div className="card" style={{ padding: 16, marginBottom: 16 }}>
          <div className="cap cap-sm" style={{ marginBottom: 12 }}>CLOSED-LOOP MANAGEMENT</div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <FlowStep label="OD baseline" value={`${num(fu.baselineOd).toFixed(2)} mm`} />
            <Arrow />
            <FlowStep label={`${fu.interval} months`} value={`${num(fu.followupOd).toFixed(2)} mm`} />
            <Arrow />
            <FlowStep label="Annualized" value={`${res.annualized_delta_al.toFixed(2)} mm/yr`} />
            <Arrow />
            <FlowStep label="Action" value={res.action_class} highlight />
          </div>
          <div className="note" style={{ marginTop: 10 }}>
            {res.refit_required
              ? "Optimization can create a new immutable revision after clinical review."
              : "The current approved revision remains the active recommendation."}
          </div>
        </div>
      )}

      {res && tier && (
        <div className="banner" style={{ background: tier.bg, border: `1px solid ${tier.border}`, marginBottom: 16 }}>
          <span className="banner-label" style={{ color: tier.lbColor, background: tier.lbBg, border: `1px solid ${tier.lbBorder}` }}>{tier.key}</span>
          <div>
            <div className="banner-title" style={{ color: tier.title }}>{res.advice}</div>
            <div className="banner-body" style={{ color: tier.body }}>{tier.body(res.annualized_delta_al.toFixed(2))}</div>
          </div>
        </div>
      )}

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16, marginTop: 20 }}>
        <span className="note">Report appends this visit to the patient&apos;s progression record.</span>
        <div style={{ display: "flex", gap: 10 }}>
          <button className="btn btn-ghost" onClick={onDownload}>Download follow-up report ⤓</button>
          <button className="btn btn-primary" disabled={!canRefit || refitting} onClick={onRefit}>
            {refitting ? "Re-fitting…" : "Re-fit from this visit →"}
          </button>
        </div>
      </div>
    </div>
  );
}
