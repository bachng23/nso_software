"use client";

import { useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000";

const num = (v) => {
  const n = Number(v);
  return Number.isNaN(n) ? 0 : n;
};

const RESULT_TIERS = {
  ready: { bg: "#ecfdf5", border: "#86efac", lbBg: "#dcfce7", lbBorder: "#22c55e", lbColor: "#166534", title: "#14532d", body: "#166534", label: "READY" },
  caution: { bg: "#fffbeb", border: "#fcd34d", lbBg: "#fef3c7", lbBorder: "#f59e0b", lbColor: "#92400e", title: "#78350f", body: "#92400e", label: "CAUTION" },
  review: { bg: "#fef2f2", border: "#fca5a5", lbBg: "#fee2e2", lbBorder: "#ef4444", lbColor: "#991b1b", title: "#7f1d1d", body: "#991b1b", label: "REVIEW" },
};

function Crumbs({ step, go }) {
  const items = ["Patient input", "Prediction results", "Follow-up visit"];
  const active = step === 1 ? 0 : step === 2 ? 1 : 2;
  return (
    <div className="crumbs">
      {items.map((label, i) => (
        <span key={label} style={{ display: "flex", alignItems: "center", gap: 6 }}>
          {i > 0 && <span className="crumb-sep">→</span>}
          {i === active ? (
            <span className="crumb active">{label}</span>
          ) : (
            <button className="crumb" onClick={() => go(i)}>{label}</button>
          )}
        </span>
      ))}
    </div>
  );
}

function Bar({ pct }) {
  return (
    <div className="bar-track" style={{ marginBottom: 12 }}>
      <div className="bar-fill" style={{ width: `${pct}%` }} />
    </div>
  );
}

export default function Page() {
  const [step, setStep] = useState(1);
  const [inp, setInp] = useState({ age: 10, al: 24.6, se: -3.25, pupil: 5.2, near: 6, outdoor: 1.5, comfort: 65, csf: 72 });
  const [adv, setAdv] = useState({ open: false, sa: "", density: "" });
  const [pred, setPred] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);
  const [fu, setFu] = useState({ baseline: 24.6, followup: 24.66, interval: 6 });
  const [fuRes, setFuRes] = useState(null);

  const set = (k) => (e) => setInp({ ...inp, [k]: e.target.value });

  function predictBody() {
    const body = {
      age: num(inp.age), al: num(inp.al), se: num(inp.se), pupil: num(inp.pupil),
      near_hours: num(inp.near), outdoor_hours: num(inp.outdoor),
      comfort: num(inp.comfort), csf: num(inp.csf),
    };
    if (adv.open) {
      if (adv.sa !== "") body.sa_strength = num(adv.sa);
      if (adv.density !== "") body.density = num(adv.density);
    }
    return body;
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
      baseline_al: num(fu.baseline), followup_al: num(fu.followup),
      interval_months: Math.round(num(fu.interval)),
    }, "nso-report.pdf");

  async function runPredict() {
    setLoading(true);
    setErr(null);
    try {
      const r = await fetch(`${API}/api/predict`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(predictBody()),
      });
      if (!r.ok) throw new Error(`API error ${r.status}`);
      setPred(await r.json());
      setStep(2);
      window.scrollTo(0, 0);
    } catch (e) {
      setErr(`${e}. Is the backend running on ${API}?`);
    } finally {
      setLoading(false);
    }
  }

  async function runFollowup(next) {
    const s = next || fu;
    try {
      const r = await fetch(`${API}/api/followup`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          baseline_al: num(s.baseline), followup_al: num(s.followup),
          interval_months: Math.round(num(s.interval)), current_profile: pred ? pred.profile : "Medium",
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
          <span className="brand-name">NSO AI-PC Fitting</span>
          <span className="brand-ver">v0.3</span>
        </div>
        <div className="badge">
          <span className="badge-dot" />
          <span className="badge-text">Rule-based</span>
        </div>
      </div>

      {err && <div className="err">{err}</div>}

      {step === 1 && (
        <Screen1 inp={inp} set={set} adv={adv} setAdv={setAdv} loading={loading}
          run={runPredict} go={(i) => (i === 2 ? goFollowup() : i === 1 && pred && setStep(2))} />
      )}
      {step === 2 && pred && (
        <Screen2 pred={pred} go={(i) => (i === 0 ? setStep(1) : i === 2 ? goFollowup() : null)}
          onFollowup={goFollowup} onExport={exportPrediction} />
      )}
      {step === 3 && (
        <Screen3 fu={fu} setFuVal={setFuVal} res={fuRes} onDownload={downloadFollowupReport}
          go={(i) => (i === 0 ? setStep(1) : i === 1 ? setStep(2) : null)} />
      )}
    </div>
  );
}

function Screen1({ inp, set, adv, setAdv, loading, run, go }) {
  return (
    <div>
      <Crumbs step={1} go={go} />
      <h1 className="title">Patient input</h1>
      <p className="sub">Enter clinical measurements and lifestyle factors. The engine evaluates them with fixed deterministic formulas — no learned parameters.</p>

      <div className="card lift" style={{ marginTop: 20 }}>
        <div className="cap" style={{ marginBottom: 16 }}>CLINICAL MEASUREMENTS</div>
        <div className="grid2">
          <NumField label="Age" unit="years" step="1" value={inp.age} onChange={set("age")} />
          <NumField label="Axial length" unit="mm" step="0.01" value={inp.al} onChange={set("al")} />
          <NumField label="Spherical equivalent" unit="D" step="0.25" value={inp.se} onChange={set("se")} />
          <NumField label="Photopic pupil" unit="mm" step="0.1" value={inp.pupil} onChange={set("pupil")} />
        </div>

        <div className="divider" style={{ margin: "24px 0" }} />
        <div className="cap" style={{ marginBottom: 18 }}>LIFESTYLE &amp; TOLERANCE</div>
        <div className="grid2" style={{ gap: "24px 32px" }}>
          <Slider label="Near work" unit="h/day" min="0" max="14" step="0.5" value={inp.near} onChange={set("near")} />
          <Slider label="Outdoor time" unit="h/day" min="0" max="8" step="0.5" value={inp.outdoor} onChange={set("outdoor")} />
          <Slider label="Comfort tolerance" unit="/ 100" min="0" max="100" step="1" value={inp.comfort} onChange={set("comfort")} />
          <Slider label="CSF quality" unit="/ 100" min="0" max="100" step="1" value={inp.csf} onChange={set("csf")} />
        </div>

        <div className="divider" style={{ margin: "24px 0 16px" }} />
        <button className="crumb" style={{ font: "500 12px Inter", color: "#78716c" }}
          onClick={() => setAdv({ ...adv, open: !adv.open })}>
          {adv.open ? "▾" : "▸"} Advanced: lens design tuning (optional)
        </button>
        {adv.open && (
          <div className="grid2" style={{ marginTop: 14 }}>
            <NumField label="SA strength override" unit="D · blank = profile default" step="0.5"
              value={adv.sa} onChange={(e) => setAdv({ ...adv, sa: e.target.value })} />
            <NumField label="Microstructure density" unit="0–100 · blank = default" step="1"
              value={adv.density} onChange={(e) => setAdv({ ...adv, density: e.target.value })} />
          </div>
        )}

        <div className="divider" style={{ margin: "20px 0" }} />
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16 }}>
          <span className="note">Deterministic engine · no patient data leaves this screen.</span>
          <button className="btn btn-primary" disabled={loading} onClick={run}>
            {loading ? "Running…" : "Run prediction"}
          </button>
        </div>
      </div>
    </div>
  );
}

function NumField({ label, unit, step, value, onChange }) {
  return (
    <div className="field">
      <label>{label} <span className="unit">· {unit}</span></label>
      <input type="number" step={step} value={value} onChange={onChange} />
    </div>
  );
}

function Slider({ label, unit, min, max, step, value, onChange }) {
  return (
    <div className="slider">
      <div className="slider-head">
        <label>{label}</label>
        <span className="slider-val">{value} <span className="unit">{unit}</span></span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value} onChange={onChange} />
    </div>
  );
}

function Screen2({ pred, go, onFollowup, onExport }) {
  const pct = (x) => Math.round(x * 100);
  const cases = ["A", "B", "C", "D"].map((k) => {
    const p = pred.quadrant_probabilities[k];
    return { k, prob: pct(p), alpha: (0.05 + p * 0.34).toFixed(3), likely: k === pred.recommended_case };
  });
  const cellStyle = (c) => ({
    background: `rgba(59,166,241,${c.alpha})`,
    border: c.likely ? "1.5px solid #3398e1" : "1px solid #e8e6e5",
  });

  const tierKey = pred.need_more_data || pred.prediction_confidence < 55
    ? "review"
    : pred.prediction_confidence >= 70 && (pred.recommended_case === "A" || pred.recommended_case === "B")
      ? "ready" : "caution";
  const t = RESULT_TIERS[tierKey];
  const bannerTitle = tierKey === "ready"
    ? `Proceed with ${pred.profile.toLowerCase()} myopia-control fitting`
    : tierKey === "review" ? "Hold — review before committing to a fit"
      : "Fit with a shortened review interval";
  const bannerBody = `Case ${pred.recommended_case} most likely at ${Math.round(pred.prediction_confidence)}% confidence. ${pred.recommended_action}`;

  const contribs = pred.top_contributors;
  const maxAbs = Math.max(...contribs.map((c) => Math.abs(c.percent)), 1);
  const gated = pred.need_more_data || pred.entropy_robustness_probability < 0.5;

  return (
    <div>
      <Crumbs step={2} go={go} />
      <h1 className="title" style={{ fontSize: 28, marginBottom: 2 }}>Prediction results</h1>
      <p className="sub" style={{ fontSize: 13, marginBottom: 14 }}>Deterministic output — probabilities are model estimates, not a clinically validated outcome.</p>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12, marginBottom: 12 }}>
        <Metric compact cap="RECOMMENDED PROFILE" big={pred.profile} sub="Myopia-control add power" />
        <Metric compact cap="EXPECTED AL REDUCTION" big={`${pred.expected_al_reduction_mm_per_year.toFixed(2)} mm/yr`} sub="vs. untreated projection" />
        <Metric compact cap="RECOMMENDED FOLLOW-UP" big={pred.recommended_follow_up} sub="Next axial-length check" />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr", gap: 12, marginBottom: 12 }}>
        <div className="card" style={{ padding: 20 }}>
          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 14 }}>
            <div className="cap cap-sm">OUTCOME PROBABILITY MATRIX</div>
            <div className="note">Σ = 100%</div>
          </div>
          <div className="matrix-grid">
            <div />
            <div className="axis-col">High adaptation<br /><span className="pct">{pct(pred.adaptation_probability)}%</span></div>
            <div className="axis-col">Low adaptation<br /><span className="pct">{pct(1 - pred.adaptation_probability)}%</span></div>

            <div className="axis-row">High<br />control<br /><span className="pct">{pct(pred.control_probability)}%</span></div>
            <Cell c={cases[0]} style={cellStyle(cases[0])} />
            <Cell c={cases[1]} style={cellStyle(cases[1])} />

            <div className="axis-row">Low<br />control<br /><span className="pct">{pct(1 - pred.control_probability)}%</span></div>
            <Cell c={cases[2]} style={cellStyle(cases[2])} />
            <Cell c={cases[3]} style={cellStyle(cases[3])} />
          </div>
          <div className="note" style={{ marginTop: 12 }}>Control probability × Adaptation probability. Cells shade with likelihood.</div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div className="card" style={{ padding: 16 }}>
            <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 10 }}>
              <div className="cap cap-sm">ENTROPY / ROBUSTNESS</div>
              <span className="metric-big" style={{ fontSize: 24 }}>{pct(pred.entropy_robustness_probability)}%</span>
            </div>
            <Bar pct={pct(pred.entropy_robustness_probability)} />
            <div className="note">Design-quality gate, not a matrix axis. Weak robustness lowers confidence.</div>
          </div>
          <div className="card" style={{ padding: 16 }}>
            <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 10 }}>
              <div className="cap cap-sm">PREDICTION CONFIDENCE</div>
              <span className="metric-big" style={{ fontSize: 24 }}>{Math.round(pred.prediction_confidence)}%</span>
            </div>
            <Bar pct={Math.round(pred.prediction_confidence)} />
            <div className="note">{gated ? "Robustness-gated · distribution near-uniform." : "One case dominates — robustness supports this."}</div>
          </div>
          <div className="banner" style={{ background: t.bg, border: `1px solid ${t.border}`, padding: "12px 14px", flex: 1 }}>
            <span className="banner-label" style={{ color: t.lbColor, background: t.lbBg, border: `1px solid ${t.lbBorder}` }}>{t.label}</span>
            <div>
              <div className="banner-title" style={{ color: t.title, fontSize: 13 }}>{bannerTitle}</div>
              <div className="banner-body" style={{ color: t.body, fontSize: 12 }}>{bannerBody}</div>
            </div>
          </div>
        </div>
      </div>

      <div className="card" style={{ padding: 20 }}>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 12 }}>
          <div className="cap cap-sm">TOP CONTRIBUTORS</div>
          <div className="note">Signed share of decision weight</div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px 28px" }}>
          {contribs.map((c) => {
            const pos = c.percent >= 0;
            return (
              <div key={c.factor} style={{ display: "grid", gridTemplateColumns: "96px 1fr 50px", alignItems: "center", gap: 12 }}>
                <span className="contrib-name" style={{ fontSize: 12 }}>{c.factor}</span>
                <div className="contrib-track">
                  <div className="contrib-fill" style={{ width: `${(Math.abs(c.percent) / maxAbs) * 100}%`, background: pos ? "#3ba6f1" : "#d6d3d1" }} />
                </div>
                <span className="contrib-val" style={{ color: pos ? "#0c0a09" : "#78716c", fontSize: 12 }}>
                  <span style={{ color: pos ? "#3398e1" : "#a8a29e", marginRight: 3 }}>{pos ? "▲" : "▼"}</span>
                  {pos ? "+" : "−"}{Math.abs(c.percent)}%
                </span>
              </div>
            );
          })}
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 10, marginTop: 12 }}>
        <button className="btn btn-ghost" onClick={onExport}>Export report ⤓</button>
        <button className="btn btn-primary" onClick={onFollowup}>Track follow-up →</button>
      </div>
    </div>
  );
}

function Cell({ c, style }) {
  return (
    <div className="cell" style={style}>
      <div className="cell-head">
        <span className="cell-label">Case {c.k}</span>
        {c.likely && <span className="pill-likely">MOST LIKELY</span>}
      </div>
      <span className="cell-prob">{c.prob}%</span>
    </div>
  );
}

function Metric({ cap, big, sub, compact }) {
  return (
    <div className="card" style={{ padding: compact ? 16 : 20 }}>
      <div className="metric-cap" style={compact ? { marginBottom: 8 } : undefined}>{cap}</div>
      <div className="metric-big" style={compact ? { fontSize: 26 } : undefined}>{big}</div>
      <div className="metric-sub" style={compact ? { marginTop: 6 } : undefined}>{sub}</div>
    </div>
  );
}

const FU_TIERS = {
  "Maintain current NSO profile": { key: "STABLE", ...RESULT_TIERS.ready,
    body: (a) => `Annualized progression is ${a} mm/year — within the controlled band. Continue the current NSO strength and re-check at the next scheduled interval.` },
  "Consider increasing one level": { key: "MONITOR", ...RESULT_TIERS.caution,
    body: (a) => `Annualized progression is ${a} mm/year — mild. Step the NSO profile up one level and shorten the review interval to confirm the response.` },
  "Escalate NSO strength and schedule clinical review": { key: "ESCALATE", ...RESULT_TIERS.review,
    body: (a) => `Annualized progression is ${a} mm/year — fast. Move to the strongest profile and book a clinical review to rule out contributing factors.` },
};

function Screen3({ fu, setFuVal, res, go, onDownload }) {
  const fmt = (x) => `${x >= 0 ? "+" : "−"}${Math.abs(x).toFixed(2)}`;
  const tier = res ? FU_TIERS[res.advice] : null;

  return (
    <div>
      <Crumbs step={3} go={go} />
      <h1 className="title">Follow-up visit</h1>
      <p className="sub">Re-measure axial length after a period of lens wear. Progression is derived deterministically from the interval — no learned parameters.</p>

      <div className="card lift" style={{ marginTop: 20, marginBottom: 16 }}>
        <div className="cap" style={{ marginBottom: 16 }}>AXIAL-LENGTH READINGS</div>
        <div className="grid2" style={{ marginBottom: 24 }}>
          <NumField label="Baseline AL" unit="mm" step="0.01" value={fu.baseline} onChange={setFuVal("baseline")} />
          <NumField label="Follow-up AL" unit="mm" step="0.01" value={fu.followup} onChange={setFuVal("followup")} />
        </div>
        <div className="slider" style={{ maxWidth: "50%" }}>
          <div className="slider-head">
            <label>Follow-up interval</label>
            <span className="slider-val">{fu.interval} <span className="unit">months</span></span>
          </div>
          <input type="range" min="1" max="12" step="1" value={fu.interval} onChange={setFuVal("interval")} />
        </div>
      </div>

      <div className="grid2" style={{ marginBottom: 16 }}>
        <Metric cap="DELTA AL" big={res ? `${fmt(res.delta_al)} mm` : "—"} sub="Baseline → follow-up change" />
        <Metric cap="ANNUALIZED DELTA AL" big={res ? `${fmt(res.annualized_delta_al)} mm/yr` : "—"} sub="Progression rate, interval-normalized" />
      </div>

      {res && tier && (
        <div className="banner" style={{ background: tier.bg, border: `1px solid ${tier.border}`, marginBottom: 16 }}>
          <span className="banner-label" style={{ color: tier.lbColor, background: tier.lbBg, border: `1px solid ${tier.lbBorder}` }}>{tier.key}</span>
          <div>
            <div className="banner-title" style={{ color: tier.title }}>{res.advice}</div>
            <div className="banner-body" style={{ color: tier.body }}>{tier.body(res.annualized_delta_al.toFixed(2))}</div>
          </div>
        </div>
      )}

      <div className="card" style={{ padding: "18px 20px", marginBottom: 20, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16 }}>
        <div>
          <div className="cap cap-sm" style={{ marginBottom: 4 }}>NEXT RECOMMENDED PROFILE</div>
          <div className="note">Applied at the next dispense or adjustment</div>
        </div>
        <div className="metric-big" style={{ fontSize: 28 }}>{res ? res.next_profile : "—"}</div>
      </div>

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16 }}>
        <span className="note">Report appends this visit to the patient&apos;s progression record.</span>
        <button className="btn btn-primary" onClick={onDownload}>Download updated follow-up report ⤓</button>
      </div>
    </div>
  );
}
