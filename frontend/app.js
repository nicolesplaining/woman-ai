import { marked } from "marked";
import DOMPurify from "dompurify";
import { Viewer } from "./viewer.js";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n, d = 1) => (n == null || isNaN(n) ? "–" : Number(n).toFixed(d));

const state = { mode: "photo", side: "left", files: {}, refs: [], sample: null, job: null, poll: null, meta: null, viewer: null, result: null };

// ---------------------------------------------------------------- setup
async function init() {
  try {
    state.meta = await (await fetch("/api/meta")).json();
    if (!state.meta.ai_available) {
      $("#aiPill").textContent = "Claude offline";
      $("#aiPill").classList.add("off");
    }
    const demo = await (await fetch("demo/demo.json")).json();
    state.hero = new Viewer($("#heroViewer"));
    state.hero.showDemo(demo);
  } catch (e) {
    console.error(e);
  }

  $("#sheetBtn").onclick = (e) => { e.stopPropagation(); $("#sheetMenu").hidden = !$("#sheetMenu").hidden; };
  $("#exportBtn").onclick = (e) => { e.stopPropagation(); $("#exportMenu").hidden = !$("#exportMenu").hidden; };
  $$("#exportMenu a").forEach((a) => (a.onclick = (e) => { e.preventDefault(); exportTo(a.dataset.app, a.dataset.dl); }));
  document.addEventListener("click", () => { $("#sheetMenu").hidden = true; $("#exportMenu").hidden = true; });

  $$(".seg[data-name=side] button").forEach((b) =>
    (b.onclick = () => { $$(".seg[data-name=side] button").forEach((x) => x.classList.toggle("on", x === b)); state.side = b.dataset.v; })
  );
  $$("#modeTabs button").forEach((b) =>
    (b.onclick = () => {
      state.mode = b.dataset.mode;
      $$("#modeTabs button").forEach((x) => x.classList.toggle("on", x === b));
      $$("[data-pane]").forEach((p) => (p.hidden = p.dataset.pane !== state.mode));
    })
  );
  $$(".drop").forEach(setupDrop);
  $$("#chips button").forEach((b) => (b.onclick = () => { $("textarea[name=design]").value = b.textContent; }));
  $("#refsInput").onchange = (e) => {
    for (const f of e.target.files) if (state.refs.length < 4) state.refs.push(f);
    e.target.value = "";
    drawRefs();
  };
  $("#form").onsubmit = (e) => { e.preventDefault(); start(); };
  $("#sampleBtn").onclick = loadSample;
  $("#errBack").onclick = () => show("empty");
  $$("#resTabs button").forEach((b) => (b.onclick = () => openTab(b.dataset.tab)));
  $$("#viewSeg button").forEach((b) => (b.onclick = () => setView(b.dataset.v)));
  $("#ringToggle input").onchange = (e) => state.viewer?.setRing(e.target.checked);
  const m = location.hash.match(/job=([a-f0-9]+)/);
  if (m) {
    state.job = m[1];
    state.t0 = Date.now();
    show("run");
    poll().catch(() => show("empty"));
  }
  document.addEventListener("click", (e) => {
    const img = e.target.closest(".overlays img");
    if (!img) return;
    const lb = document.createElement("div");
    lb.className = "lightbox";
    lb.innerHTML = `<img src="${img.src}" alt="">`;
    lb.onclick = () => lb.remove();
    document.body.appendChild(lb);
  });
}

function setupDrop(drop) {
  const input = $("input", drop);
  const key = drop.dataset.key;
  input.onchange = () => input.files[0] && setFile(drop, key, input.files[0]);
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("drag"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("drag"));
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    drop.classList.remove("drag");
    if (e.dataTransfer.files[0]) setFile(drop, key, e.dataTransfer.files[0]);
  });
}

function setFile(drop, key, file, url) {
  state.files[key] = file;
  state.sample = null;
  paintDrop(drop, file?.name || "sample", url || (file && file.type.startsWith("image/") ? URL.createObjectURL(file) : null));
}

function paintDrop(drop, label, url) {
  $$(".thumb, .thumb-label, .x", drop).forEach((n) => n.remove());
  drop.classList.add("filled");
  const t = document.createElement("div");
  t.className = "thumb";
  if (url) t.style.backgroundImage = `url(${url})`;
  else t.style.background = "var(--teal-soft)";
  const l = document.createElement("div");
  l.className = "thumb-label";
  l.textContent = label;
  const x = document.createElement("button");
  x.className = "x";
  x.type = "button";
  x.textContent = "×";
  x.onclick = (e) => {
    e.preventDefault();
    e.stopPropagation();
    delete state.files[drop.dataset.key];
    $$(".thumb, .thumb-label, .x", drop).forEach((n) => n.remove());
    drop.classList.remove("filled");
    $("input", drop).value = "";
  };
  drop.append(t, l, x);
}

function drawRefs() {
  const list = $("#refList");
  list.innerHTML = "";
  state.refs.forEach((f, i) => {
    const d = document.createElement("div");
    d.className = "ref";
    d.style.backgroundImage = `url(${URL.createObjectURL(f)})`;
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = "×";
    b.onclick = () => { state.refs.splice(i, 1); drawRefs(); };
    d.appendChild(b);
    list.appendChild(d);
  });
}

function loadSample() {
  state.sample = "partial_hand";
  state.files = {};
  const f = $("#form");
  f.name.value = "Maya";
  f.age.value = 10;
  f.sex.value = "female";
  if (!f.design.value) f.design.value = "Purple and teal with stars, and my name on it";
  const base = "/api/samples/partial_hand/";
  if (state.mode === "photo") {
    paintDrop($(".drop[data-key=top]"), "Sample · from above", base + "A_top.jpg");
    paintDrop($(".drop[data-key=side]"), "Sample · from the side", base + "B_side.jpg");
    paintDrop($('[data-pane=photo] .drop[data-key=sound]'), "Sample · other hand", base + "C_sound.jpg");
  } else {
    paintDrop($(".drop[data-key=scan]"), "Sample · scan.ply", null);
    paintDrop($('[data-pane=lidar] .drop[data-key=sound]'), "Sample · other hand", base + "C_sound.jpg");
  }
  $("#goBtn").scrollIntoView({ behavior: "smooth", block: "center" });
}

// ---------------------------------------------------------------- submit + poll
async function start() {
  const f = $("#form");
  const payload = {
    mode: state.mode,
    sample: state.sample,
    recipient: { name: f.name.value, age: f.age.value, sex: f.sex.value, side: state.side, language: f.language.value },
    tape: { wrist_circ: f.wrist_circ.value, forearm_circ: f.forearm_circ.value },
    manual: { sound_length: f.sound_length.value, sound_breadth: f.sound_breadth.value },
    design_text: f.design.value,
    bed: { x: f.bed_x.value, y: f.bed_y.value, z: f.bed_z.value },
  };
  if (!state.sample) {
    if (state.mode === "photo" && !state.files.top) return flashDrop(".drop[data-key=top]");
    if (state.mode === "lidar" && !state.files.scan) return flashDrop(".drop[data-key=scan]");
  }
  const fd = new FormData();
  fd.append("payload", JSON.stringify(payload));
  for (const [k, file] of Object.entries(state.files)) fd.append(k, file);
  state.refs.forEach((r) => fd.append("refs", r));
  $("#goBtn").disabled = true;
  $("#goBtn").textContent = "Starting…";
  try {
    const res = await fetch("/api/jobs", { method: "POST", body: fd });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    const { id } = await res.json();
    state.job = id;
    state.t0 = Date.now();
    history.replaceState(null, "", `#job=${id}`);
    show("run");
    $("#runTitle").textContent = "Building the kit…";
    poll();
  } catch (e) {
    showError({ message: String(e.message || e) });
  } finally {
    $("#goBtn").disabled = false;
    $("#goBtn").textContent = "Generate print kit";
  }
}

function flashDrop(sel) {
  const d = $(sel);
  d.scrollIntoView({ behavior: "smooth", block: "center" });
  d.animate([{ borderColor: "var(--bad)" }, { borderColor: "var(--line)" }], { duration: 1200 });
}

async function poll() {
  clearTimeout(state.poll);
  const res = await fetch(`/api/jobs/${state.job}`);
  if (!res.ok) { history.replaceState(null, "", location.pathname); return show("empty"); }
  const v = await res.json();
  drawSteps(v.steps);
  $("#timer").textContent = `${Math.round((Date.now() - state.t0) / 1000)}s`;
  if (v.status === "done") return showResult(v.result, v);
  if (v.status === "error") return showError(v.error);
  state.poll = setTimeout(poll, 900);
}

function drawSteps(steps) {
  const icon = { done: "✓", warn: "!", error: "×", running: "", pending: "" };
  $("#steps").innerHTML = steps
    .map(
      (s) => `<li class="${s.status}"><span class="st-ic">${icon[s.status]}</span>
      <div><div class="st-l">${esc(s.label)}</div>${s.detail ? `<div class="st-d">${esc(s.detail)}</div>` : ""}${s.ai ? `<div class="st-ai">${esc(s.ai)}</div>` : ""}</div>
      <span class="st-ms">${s.ms ? (s.ms / 1000).toFixed(1) + "s" : ""}</span></li>`
    )
    .join("");
}

function show(which) {
  $("#emptyState").hidden = which !== "empty";
  $("#runState").hidden = which !== "run" && which !== "done";
  $("#errorState").hidden = which !== "error";
  $("#doneState").hidden = which !== "done";
  if (which === "done") $("#runState").hidden = true;
}

function showError(err) {
  show("error");
  $("#errMsg").textContent = err?.message || "Something went wrong.";
  $("#errRetake").textContent = err?.retake || "";
  if (err?.photo) {
    const d = $(`.drop[data-key=${err.photo}]`);
    if (d) d.style.borderColor = "var(--bad)";
  }
}

// ---------------------------------------------------------------- results
function showResult(r, v) {
  state.result = r;
  show("done");
  const w = r.warnings || [];
  $("#warnings").innerHTML = w.length ? `<b>Check before printing</b><ul>${w.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "";
  if (!state.viewer) state.viewer = new Viewer($("#viewer"));
  setView("arm");
  const plateSeg = $("#plateSeg");
  plateSeg.innerHTML = r.plates.map((_, i) => `<button type="button" data-p="${i}" class="${i === 0 ? "on" : ""}">Plate ${i + 1}</button>`).join("");
  $$("button", plateSeg).forEach((b) =>
    (b.onclick = () => { $$("button", plateSeg).forEach((x) => x.classList.toggle("on", x === b)); state.viewer.showPlate(r.plates[+b.dataset.p], bed()); })
  );

  const tot = r.printability.reduce((a, p) => ({ g: a.g + p.grams_each * p.count, m: a.m + p.minutes_each * p.count }), { g: 0, m: 0 });
  $("#tiles").innerHTML = [
    tile("Hand size", `${Math.round(r.sizing.width_scale * 100)}<small>% wide</small>`, `${Math.round(r.sizing.length_scale * 100)}% long`),
    tile("Wrist", `${fmt(r.measurements.wrist_circumference, 0)}<small>mm</small>`, `${fmt(r.measurements.wrist_width)} × ${fmt(r.measurements.wrist_depth)} mm`),
    tile("Gauntlet", `${fmt(r.gauntlet.length_mm, 0)}<small>mm</small>`, `${fmt(r.plan.gap_mm)} mm padding gap`),
    tile("Print", `${(tot.m / 60).toFixed(1)}<small>h</small>`, `~${Math.round(tot.g)} g PLA · ${r.plates.length} plates`),
  ].join("");

  renderFit(r);
  renderSizing(r);
  renderLook(r);
  renderPrint(r);
  renderGuide(r);
  renderFiles(r, v);
  openTab("fit");
}

const bed = () => { const f = $("#form"); return [+f.bed_x.value || 220, +f.bed_y.value || 220]; };
const tile = (k, v, s) => `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`;

function setView(v) {
  $$("#viewSeg button").forEach((b) => b.classList.toggle("on", b.dataset.v === v));
  $("#plateSeg").hidden = v !== "plates";
  $("#ringToggle").hidden = v !== "arm";
  $("#gridNote").textContent = v === "arm" ? "grid: 10 mm" : `bed: ${bed()[0]} × ${bed()[1]} mm · grid 20 mm`;
  const r = state.result;
  if (!r) return;
  if (v === "arm") {
    const color = state.meta.filaments.find((f) => f.id === r.design.gauntlet_color)?.hex || "#7b2cbf";
    state.viewer.showArm(r.viewer, color);
    $("#ringToggle input").checked = false;
  } else {
    state.viewer.showPlate(r.plates[0], bed());
    $$("#plateSeg button").forEach((b, i) => b.classList.toggle("on", i === 0));
  }
}

function openTab(t) {
  $$("#resTabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === t));
  $$("[data-panel]").forEach((p) => (p.hidden = p.dataset.panel !== t));
}

function renderFit(r) {
  const m = r.measurements, s = m.sound_hand || {};
  const rows = [
    ["Wrist width × depth", `${fmt(m.wrist_width)} × ${fmt(m.wrist_depth)} mm`],
    ["Wrist circumference", `${fmt(m.wrist_circumference)} mm`],
    ["Widest forearm circumference", `${fmt(m.forearm_max_circumference)} mm`],
    ["Forearm measured", `${fmt(m.forearm_length_measured, 0)} mm`],
    m.residual_palm_width ? ["Residual hand width × length", `${fmt(m.residual_palm_width)} × ${fmt(m.residual_length)} mm`] : null,
    s.hand_length ? ["Other hand length × breadth", `${fmt(s.hand_length)} × ${fmt(s.hand_breadth)} mm`] : null,
    ["Expected accuracy", `±${fmt(m.uncertainty_mm)} mm (${r.mode === "lidar" ? "LiDAR" : "photos"})`],
    m.camera_height_cm ? ["Camera height", Object.entries(m.camera_height_cm).map(([k, v]) => `${k} ${v} cm`).join(" · ")] : null,
  ].filter(Boolean);
  const c = r.check || {};
  $("#pFit").innerHTML = `
    <div class="panel-grid">
      <div class="box"><h3>Measurements</h3><table class="kv">${rows.map(([a, b]) => `<tr><td>${a}</td><td>${b}</td></tr>`).join("")}</table></div>
      <div class="box"><h3>Forearm profile</h3><div id="profileChart"></div></div>
    </div>
    ${c.summary ? `<div class="box claude" style="margin-top:12px"><h3>Claude's check</h3><p>${esc(c.summary)}</p>${c.limb_difference_notes ? `<p>${esc(c.limb_difference_notes)}</p>` : ""}</div>` : ""}
    ${r.overlays.length ? `<div class="box" style="margin-top:12px"><h3>What we measured</h3><div class="overlays">${r.overlays.map((o) => `<figure><img src="${o.url}" alt="${esc(o.key)}"><figcaption>${esc(o.key)}</figcaption></figure>`).join("")}</div></div>` : ""}`;
  drawProfile($("#profileChart"), m.profile, r.gauntlet);
}

function renderSizing(r) {
  const s = r.sizing, c = s.comparison;
  const pctRow = (label, p) => `<div class="pct"><span>${label}</span><div class="bar"><i style="left:${Math.max(0, Math.min(100, p))}%"></i></div><span>${Math.round(p)}th</span></div>`;
  const ansur = s.ansur
    ? `<div class="box"><h3>Compared with ANSUR II adults</h3><p class="muted" style="margin:0 0 6px">Percentile of the other hand. Ticks mark the 5th and 95th.</p>
        <b style="font-size:13px">Women</b>${pctRow("Length", s.ansur.female.hand_length_pct)}${pctRow("Breadth", s.ansur.female.hand_breadth_pct)}
        <b style="font-size:13px;display:block;margin-top:8px">Men</b>${pctRow("Length", s.ansur.male.hand_length_pct)}${pctRow("Breadth", s.ansur.male.hand_breadth_pct)}</div>`
    : `<div class="box"><h3>Compared with ANSUR II adults</h3><p>${esc(s.notes.find((n) => n.includes("ANSUR")) || "Add a photo of the other hand to compare against ANSUR II.")}</p></div>`;
  const err = (x) => `${x > 0 ? "+" : ""}${fmt(x)}`;
  $("#pSizing").innerHTML = `
    <div class="box"><h3>Width and length, scaled separately</h3>
      <p>e-NABLE sizing today picks one scale from knuckle width. Fitted sets width from what the palm has to fit over and length from the other hand, so the device follows this person's proportions.</p>
      <div class="cmp">
        <div class="c"><div class="t">One uniform scale (${Math.round(s.uniform.scale * 100)}%)</div><div class="n">${err(c.uniform_length_error_mm)}<small> mm length vs. her hand</small></div></div>
        <div class="c good"><div class="t">Fitted (${Math.round(s.width_scale * 100)}% wide · ${Math.round(s.length_scale * 100)}% long)</div><div class="n">${err(c.ours_length_error_mm)}<small> mm</small></div></div>
      </div>
      <table class="kv">
        <tr><td>Width scale (fit)</td><td>${fmt(s.fit_width_scale * 100, 0)}%</td></tr>
        <tr><td>Width scale (match other hand)</td><td>${s.look_width_scale ? fmt(s.look_width_scale * 100, 0) + "%" : "–"}</td></tr>
        <tr><td>Length scale</td><td>${fmt(s.length_scale * 100, 0)}% · ${esc(s.length_source)}</td></tr>
        <tr><td>Device hand length × knuckle breadth</td><td>${fmt(s.ours.length_mm)} × ${fmt(s.ours.breadth_mm)} mm</td></tr>
        <tr><td>Padding inside the palm</td><td>${fmt(s.padding_per_side_mm)} mm each side</td></tr>
      </table>
      ${[...s.flags, ...s.notes].map((n) => `<p class="part-notes">• ${esc(n)}</p>`).join("")}
    </div>
    <div class="panel-grid" style="margin-top:12px">
      ${ansur}
      ${r.plan.sizing_explanation ? `<div class="box claude"><h3>Why this size</h3><p>${esc(r.plan.sizing_explanation)}</p></div>` : ""}
    </div>
    <div class="box" style="margin-top:12px"><h3>Across ANSUR II</h3><div id="popChart2"></div></div>`;
  drawPopulation($("#popChart2"), state.meta.population);
}

function renderLook(r) {
  const d = r.design;
  const fil = (id) => state.meta.filaments.find((f) => f.id === id) || { hex: "#ccc", name: id };
  const sw = (label, id) => `<div class="sw"><i style="background:${fil(id).hex}"></i><div><b>${label}</b><span>${esc(fil(id).name)}</span></div></div>`;
  const motif = d.motif === "custom" ? "Custom motif" : state.meta.motifs[d.motif] || d.motif;
  $("#pLook").innerHTML = `
    <div class="panel-grid">
      <div class="box"><h3>Gauntlet pattern</h3><img class="swatch-img" src="${d.swatch}" alt="pattern preview">
        <table class="kv"><tr><td>Motif</td><td>${esc(motif)}</td></tr><tr><td>Style</td><td>${esc(d.pattern_style)} 0.8 mm</td></tr>
        ${d.name_text ? `<tr><td>Text</td><td>${esc(d.name_text)}</td></tr>` : ""}</table></div>
      <div class="box"><h3>Colors</h3><div class="swatches">${sw("Palm", d.palm_color)}${sw("Fingertips", d.fingers_color)}${sw("Gauntlet", d.gauntlet_color)}${sw("Joints & tensioner", d.accent_color)}</div></div>
    </div>
    ${d.summary ? `<div class="box claude" style="margin-top:12px"><h3>The look</h3><p>${esc(d.summary)}</p>${d.ip_note ? `<p class="part-notes">${esc(d.ip_note)}</p>` : ""}</div>` : ""}`;
}

function renderPrint(r) {
  $("#pPrint").innerHTML = `<div class="box"><table class="parts">
    <tr><th>Part</th><th>Qty</th><th>Size (mm)</th><th>Each</th><th></th></tr>
    ${r.printability
      .map(
        (p) => `<tr><td><span class="dot" style="background:${p.color}"></span>${esc(p.label)}<div class="part-notes">${esc(p.color_name)}${p.notes.length ? " · " + p.notes.map(esc).join(" ") : ""}</div></td>
      <td class="n">${p.count}</td><td class="n">${p.size_mm.map((x) => Math.round(x)).join("×")}</td><td class="n">${p.minutes_each} min · ${p.grams_each} g</td>
      <td><span class="status ${p.status}">${p.status}</span></td></tr>`
      )
      .join("")}
  </table><p class="part-notes" style="margin-top:10px">Times and weights are rough estimates without a slicer. PLA or PETG, 0.4 mm nozzle, 3 walls, 30% infill. The gauntlet stands on its elbow end.</p></div>
  ${r.plan.volunteer_checks?.length ? `<div class="box claude" style="margin-top:12px"><h3>Volunteer checks</h3><ul class="md">${r.plan.volunteer_checks.map((c) => `<li>${esc(c)}</li>`).join("")}</ul></div>` : ""}`;
}

function renderGuide(r) {
  const p = r.packet || {};
  $("#pGuide").innerHTML = `
    ${p.recipient_summary ? `<div class="recipient"><b>For ${esc(r.recipient.name || "the recipient")} · ${esc(p.recipient_language || "")}</b>${esc(p.recipient_summary)}</div>` : ""}
    <div class="box md">${DOMPurify.sanitize(marked.parse(p.volunteer_guide_markdown || ""))}</div>`;
}

function renderFiles(r, v) {
  const approved = v.approved;
  $("#pFiles").innerHTML = `
    <div class="box">
      ${approved ? `<div class="approved">✓ Reviewed by ${esc(approved.name)}.</div>` : `
      <h3>Volunteer sign-off</h3>
      <label class="check-row"><input type="checkbox" id="apCheck"> I've read the warnings and will print the fit-test ring first.</label>
      <div class="approve"><label>Your name<input id="apName" placeholder="Volunteer name"></label><button class="btn primary" id="apBtn" disabled>Sign off</button></div>`}
      <ul class="files">${r.files.map((f) => `<li><span>${esc(f.label)}</span><a class="dl" href="${f.url}" download>${f.kind.toUpperCase()}</a></li>`).join("")}</ul>
      <p class="part-notes" style="margin-top:12px">Hand parts: Paraglider by Marcus Mendenhall (CC BY 3.0), from the e-NABLE Phoenix Reborn hand.</p>
    </div>`;
  if (!approved) {
    const upd = () => ($("#apBtn").disabled = !($("#apCheck").checked && $("#apName").value.trim()));
    $("#apCheck").onchange = upd;
    $("#apName").oninput = upd;
    $("#apBtn").onclick = async () => {
      await fetch(`/api/jobs/${state.job}/approve`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: $("#apName").value }) });
      const nv = await (await fetch(`/api/jobs/${state.job}`)).json();
      renderFiles(r, nv);
    };
  }
}

// ---------------------------------------------------------------- charts (inline SVG)
function drawPopulation(el, pop) {
  const rows = [
    ["Women", pop.by_sex.female],
    ["Men", pop.by_sex.male],
  ];
  const W = 560, H = 190, padL = 70, padR = 135, top = 24, rowH = 70;
  const max = Math.max(...rows.map(([, d]) => d.uniform_mean_abs_mm)) * 1.15;
  const x = (v) => padL + (v / max) * (W - padL - padR);
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Mean length misfit by sex">`;
  for (let t = 0; t <= max; t += 5) {
    svg += `<line x1="${x(t)}" x2="${x(t)}" y1="${top - 6}" y2="${top + rowH * 2 - 10}" stroke="var(--line)" />`;
    svg += `<text x="${x(t)}" y="${top + rowH * 2 + 6}" font-size="11" fill="var(--muted)" text-anchor="middle">${t} mm</text>`;
  }
  rows.forEach(([label, d], i) => {
    const y = top + i * rowH;
    svg += `<text x="0" y="${y + 24}" font-size="13" font-weight="600" fill="var(--ink)">${label}</text>`;
    svg += `<rect x="${padL}" y="${y + 4}" width="${x(d.uniform_mean_abs_mm) - padL}" height="18" rx="5" fill="var(--muted)" opacity=".35" />`;
    svg += `<text x="${x(d.uniform_mean_abs_mm) + 6}" y="${y + 17}" font-size="12" fill="var(--ink-2)">${d.uniform_mean_abs_mm.toFixed(1)} mm · one scale</text>`;
    svg += `<rect x="${padL}" y="${y + 27}" width="${Math.max(x(d.ours_mean_abs_mm) - padL, 3)}" height="18" rx="5" fill="var(--accent)" />`;
    svg += `<text x="${x(d.ours_mean_abs_mm) + 8}" y="${y + 40}" font-size="12" fill="var(--accent)" font-weight="600">${d.ours_mean_abs_mm.toFixed(1)} mm · Fitted</text>`;
  });
  svg += `</svg>`;
  el.innerHTML = svg;
}

function drawProfile(el, profile, g) {
  if (!profile?.length) return;
  const W = 520, H = 200, pl = 34, pr = 10, pt = 12, pb = 26;
  const zs = profile.map((p) => p.z), vals = profile.flatMap((p) => [p.width, p.depth]);
  const zx = (z) => pl + ((z - zs[0]) / (zs[zs.length - 1] - zs[0])) * (W - pl - pr);
  const vmin = Math.min(...vals) * 0.9, vmax = Math.max(...vals) * 1.05;
  const vy = (v) => pt + (1 - (v - vmin) / (vmax - vmin)) * (H - pt - pb);
  const line = (k) => profile.map((p, i) => `${i ? "L" : "M"}${zx(p.z).toFixed(1)},${vy(p[k]).toFixed(1)}`).join("");
  let svg = `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto">`;
  if (g) svg += `<rect x="${zx(g.z_start)}" y="${pt}" width="${zx(g.z_end) - zx(g.z_start)}" height="${H - pt - pb}" fill="var(--accent-soft)" rx="6" />
    <text x="${(zx(g.z_start) + zx(g.z_end)) / 2}" y="${pt + 14}" font-size="11" fill="var(--accent)" text-anchor="middle" font-weight="600">gauntlet</text>`;
  svg += `<line x1="${zx(0)}" x2="${zx(0)}" y1="${pt}" y2="${H - pb}" stroke="var(--muted)" stroke-dasharray="3 3" />
    <text x="${zx(0) + 4}" y="${H - pb - 4}" font-size="11" fill="var(--muted)">wrist</text>`;
  for (let v = Math.ceil(vmin / 10) * 10; v <= vmax; v += 10) svg += `<text x="${pl - 6}" y="${vy(v) + 4}" font-size="10.5" fill="var(--muted)" text-anchor="end">${v}</text>`;
  svg += `<path d="${line("width")}" fill="none" stroke="var(--accent)" stroke-width="2.2" />
    <path d="${line("depth")}" fill="none" stroke="var(--teal)" stroke-width="2.2" />
    <text x="${W - pr}" y="${H - 8}" font-size="11" fill="var(--muted)" text-anchor="end">mm from wrist → elbow</text>
    <text x="${pl}" y="${H - 8}" font-size="11.5" fill="var(--accent)" font-weight="600">width</text>
    <text x="${pl + 44}" y="${H - 8}" font-size="11.5" fill="var(--teal)" font-weight="600">depth</text></svg>`;
  el.innerHTML = svg;
}

init();

// ---------------------------------------------------------------- export
function exportTo(app, dl) {
  if (!state.job) return;
  const fileUrl = (name) => `${location.origin}/api/jobs/${state.job}/files/${name}`;
  if (dl) {
    const a = document.createElement("a");
    a.href = fileUrl(dl);
    a.download = dl;
    document.body.appendChild(a);
    a.click();
    a.remove();
    return;
  }
  // Slicer URL schemes download the file themselves; fall back to a normal download if nothing handles the link.
  const url = `${app}://open?file=${encodeURIComponent(fileUrl("kit.3mf"))}`;
  let left = false;
  const onBlur = () => (left = true);
  window.addEventListener("blur", onBlur, { once: true });
  window.location.href = url;
  setTimeout(() => {
    window.removeEventListener("blur", onBlur);
    if (!left) {
      exportTo(null, "kit.3mf");
      hint("Downloaded kit.3mf. Double-click it to open in your slicer. Print the fit-test ring first.");
    } else {
      hint("Opening in your slicer. Print the fit-test ring first.");
    }
  }, 1200);
}

function hint(text) {
  const h = document.createElement("div");
  h.className = "export-hint";
  h.textContent = text;
  document.body.appendChild(h);
  setTimeout(() => h.remove(), 4500);
}
