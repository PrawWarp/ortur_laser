const $ = (sel) => document.querySelector(sel);
const BED_W = window.BED_W || 400;
const BED_H = window.BED_H || 430;

let currentJobId = null;
let currentJob = null;
let lastStatus = null;
let pollTimer = null;
let uploadImage = null; // HTMLImageElement
let drag = null; // { mode: 'move'|'resize', ox, oy, startBox }
/** Current locked W/H ratio (width ÷ height). Updated when unlocked edits change size. */
let aspectRatio = 1;
let headTrail = []; // recent MPos points for path visualization
let lastJobRunning = false;

function lockAspectOn() {
  return Boolean($("#lockAspect")?.checked);
}

function refreshAspectFromInputs() {
  const w = Math.max(1, parseFloat($("#width").value) || 1);
  const h = Math.max(1, parseFloat($("#height").value) || 1);
  aspectRatio = w / h;
}

const canvas = $("#bedPreview");
const ctx = canvas.getContext("2d");
const HANDLE = 10; // px hit size for resize corner

let modalResolver = null;

function closeModal(result) {
  const modal = $("#appModal");
  if (!modal) return;
  modal.classList.add("hidden");
  const resolve = modalResolver;
  modalResolver = null;
  if (resolve) resolve(result);
}

function openModal({ title, bodyHtml, okText = "OK", cancelText = "Cancel", danger = false, requireChecks = false }) {
  return new Promise((resolve) => {
    const modal = $("#appModal");
    const okBtn = $("#modalOk");
    const cancelBtn = $("#modalCancel");
    if (!modal || !okBtn || !cancelBtn) {
      resolve(false);
      return;
    }
    if (modalResolver) closeModal(false);
    modalResolver = resolve;
    $("#modalTitle").textContent = title;
    $("#modalBody").innerHTML = bodyHtml;
    okBtn.textContent = okText;
    okBtn.className = danger ? "danger" : "";
    if (cancelText) {
      cancelBtn.textContent = cancelText;
      cancelBtn.classList.remove("hidden");
    } else {
      cancelBtn.classList.add("hidden");
    }

    const syncOk = () => {
      if (!requireChecks) {
        okBtn.disabled = false;
        return;
      }
      const boxes = [...modal.querySelectorAll(".modal-check input[type=checkbox]")];
      okBtn.disabled = boxes.length > 0 && !boxes.every((b) => b.checked);
    };
    modal.querySelectorAll(".modal-check input[type=checkbox]").forEach((box) => {
      box.addEventListener("change", syncOk);
    });
    syncOk();

    okBtn.onclick = () => closeModal(true);
    cancelBtn.onclick = () => closeModal(false);
    modal.querySelector("[data-modal-dismiss]")?.addEventListener("click", () => closeModal(false), { once: true });
    modal.classList.remove("hidden");
    (requireChecks ? modal.querySelector(".modal-check input") : okBtn)?.focus();
  });
}

function showAlert(message, title = "Notice") {
  const text = String(message || "").replace(/</g, "&lt;");
  return openModal({
    title,
    bodyHtml: `<p>${text}</p>`,
    okText: "OK",
    cancelText: null,
  });
}

function askConfirm(message, { title = "Confirm", okText = "Continue", cancelText = "Cancel", danger = false } = {}) {
  const text = String(message || "").replace(/</g, "&lt;");
  return openModal({
    title,
    bodyHtml: `<p>${text}</p>`,
    okText,
    cancelText,
    danger,
  });
}

function askArmConfirm({ alsoSend = false } = {}) {
  const title = alsoSend ? "Arm & send live job" : "Arm laser";
  const okText = alsoSend ? "ARM & Send" : "ARM";
  const bodyHtml = `
    <p>This unlocks live laser output. Check each item before continuing:</p>
    <div class="modal-check">
      <label><input type="checkbox" /> Workspace is clear of flammables and hands</label>
      <label><input type="checkbox" /> Eye protection is on</label>
      <label><input type="checkbox" /> Exhaust / ventilation is ready</label>
    </div>
  `;
  return openModal({
    title,
    bodyHtml,
    okText,
    cancelText: "Cancel",
    danger: true,
    requireChecks: true,
  });
}

function updateArmUi(armed) {
  const label = $("#armStateLabel");
  const panel = $("#armPanel");
  if (label) {
    label.textContent = armed ? "ARMED" : "DISARMED";
    label.classList.toggle("armed", !!armed);
    label.classList.toggle("disarmed", !armed);
  }
  panel?.classList.toggle("is-armed", !!armed);
  document.querySelectorAll("#jobArmChip, #runArm").forEach((el) => {
    el.textContent = armed ? "ARMED" : "DISARMED";
    el.classList.toggle("armed", !!armed);
    el.classList.toggle("disarmed", !armed);
  });
  if ($("#btnArm")) $("#btnArm").disabled = !lastStatus?.connected || !!armed;
  if ($("#btnDisarm")) $("#btnDisarm").disabled = !lastStatus?.connected || !armed;
}

function updateConnectionUi(connected) {
  document.querySelectorAll(".need-conn").forEach((el) => {
    if (el.id === "btnArm" || el.id === "btnDisarm") return;
    if (typeof el.disabled === "boolean") el.disabled = !connected;
  });
  updateArmUi(!!lastStatus?.armed);
}

function runDisableReason() {
  if (!currentJobId) return "Create a job first";
  if (!lastStatus?.connected) return "Connect the machine first";
  if (lastStatus?.job_running) return "Job running — Cancel to stop";
  return "";
}

function updateRunUi() {
  const running = !!lastStatus?.job_running;
  const connected = !!lastStatus?.connected;
  const hasJob = !!currentJobId;
  const reason = runDisableReason();

  const dry = $("#btnDry");
  const send = $("#btnSend");
  const cancel = $("#btnCancel");
  if (dry) {
    dry.disabled = !hasJob || !connected || running;
    dry.title = dry.disabled ? (reason || "Unavailable") : "Motion only, laser off";
  }
  if (send) {
    send.disabled = !hasJob || !connected || running;
    send.title = send.disabled
      ? (reason || "Unavailable")
      : (lastStatus?.armed ? "Live laser send" : "Will prompt to ARM, then send");
  }
  if (cancel) cancel.disabled = !running;

  const hint = $("#runHint");
  if (hint) {
    if (running) hint.textContent = "Running — Cancel stops immediately";
    else if (!hasJob) hint.textContent = "Create a job first";
    else if (!connected) hint.textContent = "Connect first to Dry run or Send";
    else if (!lastStatus?.armed) hint.textContent = "Ready — Send will ask to ARM";
    else hint.textContent = "Ready — armed for live send";
  }

  const estEl = $("#runEst");
  if (estEl) {
    const est = formatDuration(currentJob?.est_seconds);
    if (running && lastStatus?.job_remaining_seconds != null) {
      estEl.textContent = `~${formatDuration(lastStatus.job_remaining_seconds)} left`;
      estEl.classList.remove("muted");
    } else if (est) {
      estEl.textContent = `est ~${est}`;
      estEl.classList.remove("muted");
    } else {
      estEl.textContent = "No est";
      estEl.classList.add("muted");
    }
  }
}

function setJogStep(mm) {
  const v = String(mm);
  if ($("#jogStep")) $("#jogStep").value = v;
  document.querySelectorAll("#jogStepChips .chip").forEach((chip) => {
    chip.classList.toggle("active", chip.dataset.step === v);
  });
}

function isTypingTarget(el) {
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
}

async function api(path, opts = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  const text = await res.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text }; }
  if (!res.ok) {
    const detail = data.detail;
    throw new Error(typeof detail === "string" ? detail : (detail ? JSON.stringify(detail) : res.statusText));
  }
  return data;
}

function jobBox() {
  return {
    x: parseFloat($("#ox").value) || 0,
    y: parseFloat($("#oy").value) || 0,
    w: Math.max(1, parseFloat($("#width").value) || 1),
    h: Math.max(1, parseFloat($("#height").value) || 1),
  };
}

function scale() {
  // Fit bed into canvas with padding
  const pad = 24;
  const sx = (canvas.width - pad * 2) / BED_W;
  const sy = (canvas.height - pad * 2) / BED_H;
  const s = Math.min(sx, sy);
  const ox = (canvas.width - BED_W * s) / 2;
  const oy = (canvas.height - BED_H * s) / 2;
  return { s, ox, oy, pad };
}

function mmToPx(x, y) {
  const { s, ox, oy } = scale();
  // Canvas Y increases down; machine Y increases "up" on bed — flip for display
  return {
    px: ox + x * s,
    py: oy + (BED_H - y) * s,
  };
}

function pxToMm(px, py) {
  const { s, ox, oy } = scale();
  return {
    x: (px - ox) / s,
    y: BED_H - (py - oy) / s,
  };
}

const PRESET_DEFAULTS = {
  cardboard: { power: 25, feed: 1000 },
};

function syncPowerLabels() {
  const p = $("#powerPct");
  const f = $("#feedRate");
  if (p) $("#powerPctLabel").textContent = `${p.value}%`;
  if (f) $("#feedLabel").textContent = f.value;
}

function applyPresetToSliders() {
  const key = $("#preset")?.value || "cardboard";
  const d = PRESET_DEFAULTS[key] || PRESET_DEFAULTS.cardboard;
  $("#powerPct").value = d.power;
  $("#feedRate").value = d.feed;
  syncPowerLabels();
}

async function loadPresets() {
  try {
    const data = await api("/presets");
    const sel = $("#preset");
    if (!sel) return;
    sel.innerHTML = "";
    const list = data.presets || [];
    const def = data.default || "cardboard";
    for (const p of list) {
      PRESET_DEFAULTS[p.id] = {
        power: Math.round(p.power_pct),
        feed: Math.round(p.feed),
      };
      const opt = document.createElement("option");
      opt.value = p.id;
      opt.textContent = `${p.label} · ${Math.round(p.power_pct)}% · F${Math.round(p.feed)}`;
      if (p.id === def) opt.selected = true;
      sel.appendChild(opt);
    }
    if (!list.length) {
      const opt = document.createElement("option");
      opt.value = "cardboard";
      opt.textContent = "Cardboard (recommended) · 25% · F1000";
      sel.appendChild(opt);
    }
    applyPresetToSliders();
  } catch {
    const sel = $("#preset");
    if (sel && !sel.options.length) {
      sel.innerHTML = '<option value="cardboard" selected>Cardboard (recommended)</option>';
    }
  }
}

/** Mean luminance 0..255 of an ImageData (RGB). */
function meanLuma(imageData) {
  const d = imageData.data;
  let sum = 0;
  const n = d.length / 4;
  for (let i = 0; i < d.length; i += 4) {
    sum += 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
  }
  return n ? sum / n : 255;
}

/**
 * Resolve burn polarity to match server:
 * invert=true → burn light parts (after invert, dark = burn).
 */
function resolveInvert(imageData) {
  const pol = $("#burnPolarity")?.value || "auto";
  if (pol === "light") return true;
  if (pol === "dark") return false;
  return meanLuma(imageData) < 140;
}

/** Build orange burn mask ImageData from grayscale luminance. */
function toBurnMask(src, invert, outline) {
  const { width: w, height: h, data } = src;
  const gray = new Uint8Array(w * h);
  for (let i = 0, p = 0; i < data.length; i += 4, p++) {
    let v = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
    if (invert) v = 255 - v;
    gray[p] = v < 200 ? 0 : 255; // 0 = burn
  }
  let burn = gray;
  if (outline) {
    burn = new Uint8Array(w * h);
    burn.fill(255);
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const i = y * w + x;
        if (gray[i] >= 200) continue;
        let edge = false;
        for (let dy = -1; dy <= 1 && !edge; dy++) {
          for (let dx = -1; dx <= 1; dx++) {
            if (dx === 0 && dy === 0) continue;
            const nx = x + dx;
            const ny = y + dy;
            if (nx < 0 || ny < 0 || nx >= w || ny >= h || gray[ny * w + nx] >= 200) {
              edge = true;
              break;
            }
          }
        }
        if (edge) burn[i] = 0;
      }
    }
  }
  const out = new ImageData(w, h);
  const od = out.data;
  for (let p = 0, i = 0; p < burn.length; p++, i += 4) {
    if (burn[p] < 200) {
      od[i] = 255;
      od[i + 1] = 138;
      od[i + 2] = 61;
      od[i + 3] = 230;
    } else {
      od[i + 3] = 0;
    }
  }
  return out;
}

function drawBurnFromImage(img, dx, dy, dw, dh, invertHint) {
  const preview = $("#previewMode")?.value || "overlay";
  // Sample at modest resolution for speed
  const sw = Math.max(8, Math.min(320, Math.round(Math.abs(dw))));
  const sh = Math.max(8, Math.min(320, Math.round(Math.abs(dh))));
  const off = document.createElement("canvas");
  off.width = sw;
  off.height = sh;
  const octx = off.getContext("2d", { willReadFrequently: true });
  octx.fillStyle = "#ffffff";
  octx.fillRect(0, 0, sw, sh);
  octx.drawImage(img, 0, 0, sw, sh);
  const src = octx.getImageData(0, 0, sw, sh);
  const invert = invertHint != null ? invertHint : resolveInvert(src);
  const mode = $("#engraveMode")?.value || "fill";

  if (preview === "image") {
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(img, dx, dy, dw, dh);
    return;
  }

  if (preview === "overlay") {
    ctx.imageSmoothingEnabled = true;
    ctx.globalAlpha = 0.55;
    ctx.drawImage(img, dx, dy, dw, dh);
    ctx.globalAlpha = 1;
  }

  const mask = toBurnMask(src, invert, mode === "outline");
  octx.putImageData(mask, 0, 0);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(off, dx, dy, dw, dh);
}

function drawBurnText(text, fontName, rw, rh, tl) {
  const preview = $("#previewMode")?.value || "overlay";
  const sw = Math.max(32, Math.min(400, Math.round(Math.abs(rw))));
  const sh = Math.max(32, Math.min(400, Math.round(Math.abs(rh))));
  const off = document.createElement("canvas");
  off.width = sw;
  off.height = sh;
  const octx = off.getContext("2d", { willReadFrequently: true });
  octx.fillStyle = "#ffffff";
  octx.fillRect(0, 0, sw, sh);
  octx.fillStyle = "#000000";
  octx.textAlign = "center";
  octx.textBaseline = "middle";
  let fs = Math.max(10, sh * 0.7);
  octx.font = `${fs}px "${fontName}"`;
  const tw = octx.measureText(text).width;
  if (tw > sw * 0.92 && tw > 0) {
    fs = fs * ((sw * 0.92) / tw);
    octx.font = `${fs}px "${fontName}"`;
  }
  octx.fillText(text, sw / 2, sh / 2);
  const src = octx.getImageData(0, 0, sw, sh);
  const mode = $("#engraveMode")?.value || "fill";

  if (preview === "image") {
    ctx.fillStyle = "#e8eef4";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.font = `${Math.max(10, rh * 0.7)}px "${fontName}"`;
    let size = Math.max(10, rh * 0.7);
    ctx.font = `${size}px "${fontName}"`;
    const m = ctx.measureText(text);
    if (m.width > rw * 0.92 && m.width > 0) {
      size = size * ((rw * 0.92) / m.width);
      ctx.font = `${size}px "${fontName}"`;
    }
    ctx.fillText(text, tl.px + rw / 2, tl.py + rh / 2);
    return;
  }

  if (preview === "overlay") {
    ctx.fillStyle = "rgba(232, 238, 244, 0.45)";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    let size = Math.max(10, rh * 0.7);
    ctx.font = `${size}px "${fontName}"`;
    const m = ctx.measureText(text);
    if (m.width > rw * 0.92 && m.width > 0) {
      size = size * ((rw * 0.92) / m.width);
      ctx.font = `${size}px "${fontName}"`;
    }
    ctx.fillText(text, tl.px + rw / 2, tl.py + rh / 2);
  }

  const mask = toBurnMask(src, false, mode === "outline");
  octx.putImageData(mask, 0, 0);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(off, tl.px, tl.py, rw, rh);
}

function drawPreview() {
  const { s, ox, oy } = scale();
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  // bed
  ctx.fillStyle = "#161b22";
  ctx.fillRect(ox, oy, BED_W * s, BED_H * s);
  ctx.strokeStyle = "#3d4a58";
  ctx.lineWidth = 2;
  ctx.strokeRect(ox, oy, BED_W * s, BED_H * s);

  // grid every 50mm (major every 100)
  for (let g = 50; g < BED_W; g += 50) {
    const a = mmToPx(g, 0);
    const b = mmToPx(g, BED_H);
    ctx.strokeStyle = g % 100 === 0 ? "#2e3c4a" : "#1f2a35";
    ctx.lineWidth = g % 100 === 0 ? 1.25 : 1;
    ctx.beginPath(); ctx.moveTo(a.px, a.py); ctx.lineTo(b.px, b.py); ctx.stroke();
  }
  for (let g = 50; g < BED_H; g += 50) {
    const a = mmToPx(0, g);
    const b = mmToPx(BED_W, g);
    ctx.strokeStyle = g % 100 === 0 ? "#2e3c4a" : "#1f2a35";
    ctx.lineWidth = g % 100 === 0 ? 1.25 : 1;
    ctx.beginPath(); ctx.moveTo(a.px, a.py); ctx.lineTo(b.px, b.py); ctx.stroke();
  }

  // origin label
  ctx.fillStyle = "#8b9aab";
  ctx.font = "12px \"DM Sans\", sans-serif";
  const o0 = mmToPx(0, 0);
  ctx.fillText("(0,0)", o0.px + 4, o0.py - 6);
  ctx.fillText(`${BED_W}×${BED_H} mm`, ox + 6, oy + 16);

  const box = jobBox();
  // job rect corners in mm (bottom-left origin)
  const tl = mmToPx(box.x, box.y + box.h);
  const br = mmToPx(box.x + box.w, box.y);
  const rw = br.px - tl.px;
  const rh = br.py - tl.py;

  // burn preview — orange = laser fires
  ctx.save();
  ctx.beginPath();
  ctx.rect(tl.px, tl.py, rw, rh);
  ctx.clip();

  const active = document.querySelector(".src-tab.active")?.dataset.tab || "upload";
  if (active === "upload" && uploadImage) {
    const fit = $("#imageFit")?.value || "fill";
    const ir = uploadImage.width / uploadImage.height;
    const bratio = rw / rh;
    let dw, dh, dx, dy;
    if (fit === "contain") {
      if (ir > bratio) { dw = rw; dh = rw / ir; }
      else { dh = rh; dw = rh * ir; }
      dx = tl.px + (rw - dw) / 2;
      dy = tl.py + (rh - dh) / 2;
    } else if (fit === "cover") {
      if (ir > bratio) { dh = rh; dw = rh * ir; }
      else { dw = rw; dh = rw / ir; }
      dx = tl.px + (rw - dw) / 2;
      dy = tl.py + (rh - dh) / 2;
    } else {
      dw = rw; dh = rh; dx = tl.px; dy = tl.py;
    }
    drawBurnFromImage(uploadImage, dx, dy, dw, dh);
  } else {
    const text = $("#canvasText").value || "";
    if (text) {
      const fontName = $("#fontName").value || "Arial";
      drawBurnText(text, fontName, rw, rh, tl);
    } else {
      ctx.fillStyle = "#6a7a8a";
      ctx.font = "13px \"DM Sans\", sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText("Type a name to engrave", tl.px + rw / 2, tl.py + rh / 2);
    }
  }
  ctx.restore();

  // job border + drag affordance
  ctx.strokeStyle = "#3d9bfd";
  ctx.lineWidth = 2;
  ctx.strokeRect(tl.px, tl.py, rw, rh);
  // resize handle (bottom-right in screen = +X -Y in machine... br corner)
  ctx.fillStyle = "#3d9bfd";
  ctx.fillRect(br.px - 6, br.py - 6, 12, 12);
  ctx.strokeStyle = "#9fd0ff";
  ctx.lineWidth = 1;
  ctx.strokeRect(br.px - 6, br.py - 6, 12, 12);
  ctx.font = "11px \"DM Sans\", sans-serif";
  ctx.textAlign = "left";
  ctx.fillStyle = "#3d9bfd";
  ctx.fillText(
    `${box.w.toFixed(0)}×${box.h.toFixed(0)} @ (${box.x.toFixed(0)}, ${box.y.toFixed(0)})`,
    tl.px,
    tl.py - 6
  );

  // live head trail + crosshair (only while connected)
  if (lastStatus?.connected && headTrail.length > 1) {
    ctx.strokeStyle = "rgba(62, 207, 142, 0.45)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    headTrail.forEach((pt, i) => {
      const p = mmToPx(pt.x, pt.y);
      if (i === 0) ctx.moveTo(p.px, p.py);
      else ctx.lineTo(p.px, p.py);
    });
    ctx.stroke();
  }
  const mposEl = $("#mpos").textContent;
  if (lastStatus?.connected && mposEl && mposEl.includes(",")) {
    const [mx, my] = mposEl.split(",").map((v) => parseFloat(v.trim()));
    if (!Number.isNaN(mx) && !Number.isNaN(my)) {
      const p = mmToPx(mx, my);
      const running = lastJobRunning;
      // outer glow
      ctx.beginPath();
      ctx.arc(p.px, p.py, running ? 10 : 7, 0, Math.PI * 2);
      ctx.fillStyle = running ? "rgba(255, 138, 61, 0.35)" : "rgba(62, 207, 142, 0.25)";
      ctx.fill();
      // crosshair
      ctx.strokeStyle = running ? "#ff8a3d" : "#3ecf8e";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(p.px - 12, p.py); ctx.lineTo(p.px + 12, p.py);
      ctx.moveTo(p.px, p.py - 12); ctx.lineTo(p.px, p.py + 12);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(p.px, p.py, 3, 0, Math.PI * 2);
      ctx.fillStyle = running ? "#ff8a3d" : "#3ecf8e";
      ctx.fill();
      ctx.fillStyle = running ? "#ff8a3d" : "#3ecf8e";
      ctx.font = "11px \"DM Sans\", sans-serif";
      ctx.fillText(`head ${mx.toFixed(1)}, ${my.toFixed(1)}`, p.px + 14, p.py - 8);
    }
  }
}

function clampJobInputs() {
  let x = parseFloat($("#ox").value) || 0;
  let y = parseFloat($("#oy").value) || 0;
  let w = Math.max(1, parseFloat($("#width").value) || 1);
  let h = Math.max(1, parseFloat($("#height").value) || 1);
  if (lockAspectOn() && aspectRatio > 0) {
    if (w > BED_W) {
      w = BED_W;
      h = w / aspectRatio;
    }
    if (h > BED_H) {
      h = BED_H;
      w = h * aspectRatio;
    }
    if (w > BED_W) {
      w = BED_W;
      h = w / aspectRatio;
    }
    w = Math.max(1, w);
    h = Math.max(1, h);
  } else {
    w = Math.min(w, BED_W);
    h = Math.min(h, BED_H);
  }
  x = Math.min(Math.max(0, x), BED_W - w);
  y = Math.min(Math.max(0, y), BED_H - h);
  $("#ox").value = Math.round(x * 10) / 10;
  $("#oy").value = Math.round(y * 10) / 10;
  $("#width").value = Math.round(w * 10) / 10;
  $("#height").value = Math.round(h * 10) / 10;
}

function formatDuration(seconds) {
  if (seconds == null || Number.isNaN(seconds)) return null;
  const s = Math.max(0, Math.round(Number(seconds)));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const sec = s % 60;
  if (m < 60) return `${m}m ${String(sec).padStart(2, "0")}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${String(m % 60).padStart(2, "0")}m`;
}

function applyStatus(s) {
  lastStatus = s;
  const badge = $("#badge");
  badge.className = "badge";
  if (!s.connected) {
    badge.classList.add("disconnected");
    badge.textContent = "Disconnected";
  } else if (s.job_running) {
    badge.classList.add("running");
    badge.textContent = "Running";
  } else if (s.armed) {
    badge.classList.add("armed");
    badge.textContent = "ARMED";
  } else {
    badge.classList.add("connected");
    badge.textContent = "Connected";
  }
  $("#state").textContent = s.state || "—";
  if (s.connected && s.mpos) {
    $("#mpos").textContent = `${s.mpos.x.toFixed(2)}, ${s.mpos.y.toFixed(2)}`;
    if (s.job_running) {
      const last = headTrail[headTrail.length - 1];
      if (!last || Math.abs(last.x - s.mpos.x) > 0.05 || Math.abs(last.y - s.mpos.y) > 0.05) {
        headTrail.push({ x: s.mpos.x, y: s.mpos.y });
        if (headTrail.length > 400) headTrail.shift();
      }
    }
  } else {
    $("#mpos").textContent = "—";
    headTrail = [];
  }
  if (s.job_running !== lastJobRunning) {
    if (s.job_running) {
      headTrail = [];
      showStudioPane("run");
    }
    lastJobRunning = !!s.job_running;
    startPoll(); // retune interval
  }
  updateConnectionUi(!!s.connected);
  updateArmUi(!!s.armed);
  updateRunUi();
  $("#identity").textContent = s.identity || "—";
  $("#message").textContent = s.last_message || "";
  const alarm = (s.state || "").toLowerCase() === "alarm" || (s.job_error || "").includes("error:9");
  $("#alarmBanner").classList.toggle("hidden", !alarm);

  if (s.job_running && s.job_lines_total > 0) {
    const pct = Math.round((s.job_lines_sent / s.job_lines_total) * 100);
    $("#progressBar").style.width = `${pct}%`;
    const parts = [`${s.job_lines_sent} / ${s.job_lines_total} lines (${pct}%)`];
    const elapsed = formatDuration(s.job_elapsed_seconds);
    const remain = formatDuration(s.job_remaining_seconds);
    const est = formatDuration(s.job_est_seconds);
    if (elapsed) parts.push(`elapsed ${elapsed}`);
    if (remain != null) parts.push(`~${remain} left`);
    else if (est) parts.push(`est ${est}`);
    $("#progressText").textContent = parts.join(" · ");
  } else if (s.job_error) {
    $("#progressText").textContent = `Error: ${s.job_error}`;
  } else if (!s.job_running) {
    if (s.last_message && s.last_message.toLowerCase().includes("cancel")) {
      $("#progressText").textContent = s.last_message;
    } else if (s.job_lines_total && s.job_lines_sent >= s.job_lines_total) {
      $("#progressBar").style.width = "100%";
      const elapsed = formatDuration(s.job_elapsed_seconds);
      $("#progressText").textContent = elapsed ? `Complete · ${elapsed}` : "Complete";
    }
  }
  drawPreview();
}

async function refreshStatus() {
  try {
    const s = await api("/device/status");
    applyStatus(s);
  } catch (e) {
    $("#message").textContent = e.message;
  }
}

function selectPortValue(sel, value) {
  if (!value) return false;
  const match = [...sel.options].find((o) => o.value === value);
  if (match) {
    sel.value = value;
    return true;
  }
  return false;
}

async function loadPorts(preferDevice) {
  const data = await api("/device/ports");
  const sel = $("#port");
  const prev = preferDevice || sel.value;
  sel.innerHTML = "";

  const autoOpt = document.createElement("option");
  autoOpt.value = "auto";
  autoOpt.textContent = "Auto — find laser";
  sel.appendChild(autoOpt);

  for (const p of data.ports) {
    const opt = document.createElement("option");
    opt.value = p.device;
    const mark = (p.score || 0) >= 30 ? "★ " : "";
    opt.textContent = `${mark}${p.device} — ${p.description || "serial"}`;
    sel.appendChild(opt);
  }

  const configured = (window.DEFAULT_PORT || data.default || "auto").trim();
  const hint = data.hint || configured;

  if (preferDevice && selectPortValue(sel, preferDevice)) {
    /* keep found port */
  } else if (prev && prev !== "auto" && selectPortValue(sel, prev)) {
    /* keep prior selection */
  } else if (configured.toLowerCase() !== "auto" && selectPortValue(sel, configured)) {
    /* configured SERIAL_PORT */
  } else if (data.ports.length) {
    // Prefer highest-scored USB candidate; fall back to Auto
    const best = data.ports.find((p) => (p.score || 0) >= 30) || data.ports[0];
    if (best && (best.score || 0) >= 10) selectPortValue(sel, best.device);
    else sel.value = "auto";
  } else {
    sel.value = "auto";
    if (hint && hint.toLowerCase() !== "auto") {
      const opt = document.createElement("option");
      opt.value = hint;
      opt.textContent = hint;
      sel.appendChild(opt);
    }
  }
  return data;
}

async function loadFonts() {
  try {
    const data = await api("/fonts");
    const sel = $("#fontName");
    sel.innerHTML = "";
    for (const f of data.fonts) {
      const opt = document.createElement("option");
      opt.value = f;
      opt.textContent = f;
      if (f === "Arial") opt.selected = true;
      sel.appendChild(opt);
    }
  } catch {
    $("#fontName").innerHTML = "<option>Arial</option>";
  }
}

function startPoll() {
  stopPoll();
  const ms = lastJobRunning ? 200 : 500;
  pollTimer = setInterval(refreshStatus, ms);
}

function stopPoll() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
}

$("#btnFind").onclick = async () => {
  const btn = $("#btnFind");
  const prev = btn.textContent;
  btn.disabled = true;
  btn.textContent = "…";
  try {
    $("#message").textContent = "Scanning serial ports…";
    const preferred = $("#port").value;
    const result = await api("/device/find", {
      method: "POST",
      body: JSON.stringify({
        port: preferred === "auto" ? "auto" : preferred,
      }),
    });
    await loadPorts(result.device || undefined);
    if (result.found && result.device) {
      selectPortValue($("#port"), result.device);
      $("#message").textContent = result.message + (result.identity ? ` · ${result.identity}` : "");
    } else {
      $("#message").textContent = result.message || "No laser found";
      showAlert(
        (result.message || "No GRBL/Ortur laser found.") +
          " Close LaserGRBL/LightBurn, check the USB cable, and on Raspberry Pi ensure your user is in the dialout group.",
        "Find laser"
      );
    }
  } catch (e) {
    showAlert(e.message, "Find laser failed");
  } finally {
    btn.disabled = false;
    btn.textContent = prev;
  }
};

$("#btnConnect").onclick = async () => {
  try {
    const port = $("#port").value || "auto";
    $("#message").textContent = port === "auto" ? "Finding laser…" : `Connecting ${port}…`;
    const s = await api("/device/connect", {
      method: "POST",
      body: JSON.stringify({ port }),
    });
    if (s.port) await loadPorts(s.port);
    applyStatus(s);
    startPoll();
  } catch (e) {
    showAlert(e.message, "Connect failed");
  }
};

$("#btnDisconnect").onclick = async () => {
  const s = await api("/device/disconnect", { method: "POST", body: "{}" });
  applyStatus(s);
};

async function postAct(path, body = {}) {
  try {
    const s = await api(path, { method: "POST", body: JSON.stringify(body) });
    applyStatus(s);
  } catch (e) {
    showAlert(e.message);
  }
};

document.querySelectorAll("[data-act]").forEach((btn) => {
  btn.onclick = () => postAct(`/device/${btn.dataset.act}`);
});

document.querySelectorAll(".jog-btn").forEach((btn) => {
  btn.onclick = () => {
    if (!lastStatus?.connected) return;
    const step = parseFloat($("#jogStep").value);
    const dist = step * parseFloat(btn.dataset.dir);
    postAct("/device/jog", { axis: btn.dataset.jog, distance_mm: dist, feed: 2000 });
  };
});

document.querySelectorAll("#jogStepChips .chip").forEach((chip) => {
  chip.onclick = () => setJogStep(chip.dataset.step);
});

$("#btnFrame").onclick = () => {
  if (!lastStatus?.connected) return;
  clampJobInputs();
  const box = jobBox();
  postAct("/device/frame", {
    width_mm: box.w,
    height_mm: box.h,
    origin_x: box.x,
    origin_y: box.y,
  });
};

$("#btnArm").onclick = async () => {
  if (!lastStatus?.connected) return;
  if (!(await askArmConfirm())) return;
  await postAct("/device/arm");
};

$("#btnDisarm").onclick = () => {
  if (!lastStatus?.connected) return;
  postAct("/device/disarm");
};

$("#btnCancel").onclick = async () => {
  try {
    const s = await api("/device/abort", { method: "POST", body: "{}" });
    applyStatus(s);
    $("#progressText").textContent = "Cancel requested…";
  } catch (e) {
    showAlert(e.message);
  }
};

function showStudioPane(name) {
  document.querySelectorAll(".studio-nav-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.studio === name);
  });
  document.querySelectorAll(".studio-pane").forEach((pane) => {
    pane.classList.toggle("active", pane.id === `studio-${name}`);
  });
}

document.querySelectorAll(".studio-nav-btn").forEach((btn) => {
  btn.onclick = () => showStudioPane(btn.dataset.studio);
});

document.querySelectorAll(".src-tab").forEach((tab) => {
  tab.onclick = () => {
    document.querySelectorAll(".src-tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".src-pane").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    $(`#tab-${tab.dataset.tab}`).classList.add("active");
    drawPreview();
  };
});

function setJob(job) {
  currentJobId = job.id;
  currentJob = job;
  const card = $("#jobCard");
  card?.classList.remove("empty");
  card?.classList.add("ready");
  if ($("#jobName")) $("#jobName").textContent = job.name || "Job";
  if ($("#jobMeta")) {
    const bits = [];
    if (job.mode) bits.push(job.mode);
    if (job.invert != null) bits.push(job.invert ? "burn light" : "burn dark");
    if (job.passes != null && job.passes > 1) bits.push(`${job.passes}× pass`);
    bits.push(`id ${job.id.slice(0, 8)}…`);
    $("#jobMeta").textContent = bits.join(" · ");
  }
  const stats = $("#jobStats");
  if (stats) {
    stats.classList.remove("hidden");
    if ($("#jobLines")) $("#jobLines").textContent = `${job.lines} lines`;
    if ($("#jobEst")) $("#jobEst").textContent = formatDuration(job.est_seconds) ? `~${formatDuration(job.est_seconds)}` : "est —";
    if ($("#jobPower")) $("#jobPower").textContent = job.power_pct != null ? `${job.power_pct}%` : "—";
    if ($("#jobFeed")) $("#jobFeed").textContent = job.feed != null ? `F${Math.round(job.feed)}` : "—";
  }
  const a = $("#btnDownload");
  a.href = `/api/jobs/${job.id}/gcode`;
  a.download = `${job.name || "job"}.nc`;
  a.classList.remove("disabled");
  $("#progressBar").style.width = "0%";
  const est = formatDuration(job.est_seconds);
  $("#progressText").textContent = est ? `Job ready · est ~${est}` : "Job ready";
  showStudioPane("run");
  updateRunUi();
  updateArmUi(!!lastStatus?.armed);
}

$("#btnCreate").onclick = async () => {
  clampJobInputs();
  const active = document.querySelector(".src-tab.active").dataset.tab;
  const fd = new FormData();
  fd.append("width_mm", $("#width").value);
  fd.append("height_mm", $("#height").value);
  fd.append("origin_x", $("#ox").value);
  fd.append("origin_y", $("#oy").value);
  fd.append("preset", $("#preset").value);
  fd.append("mode", $("#engraveMode").value);
  fd.append("power_pct", $("#powerPct").value);
  fd.append("feed", $("#feedRate").value);
  fd.append("passes", $("#doublePass")?.checked ? "2" : "1");
  try {
    let res;
    if (active === "upload") {
      const file = $("#file").files[0];
      if (!file) return showAlert("Choose an image first", "Upload");
      fd.append("file", file);
      fd.append("fit", $("#imageFit").value || "fill");
      const pol = $("#burnPolarity")?.value || "auto";
      if (pol === "auto") fd.append("invert", "auto");
      else if (pol === "light") fd.append("invert", "true");
      else fd.append("invert", "false");
      res = await fetch("/api/jobs/from-upload", { method: "POST", body: fd });
    } else {
      fd.append("text", $("#canvasText").value || "TEST");
      fd.append("font_name", $("#fontName").value || "Arial");
      res = await fetch("/api/jobs/from-canvas", { method: "POST", body: fd });
    }
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    setJob(data);
  } catch (e) {
    showAlert(e.message, "Create failed");
  }
};

function syncGridLabels() {
  if ($("#gridPowerPctLabel") && $("#gridPowerPct")) {
    $("#gridPowerPctLabel").textContent = `${$("#gridPowerPct").value}%`;
  }
  if ($("#gridFeedLabel") && $("#gridFeedRate")) {
    $("#gridFeedLabel").textContent = $("#gridFeedRate").value;
  }
}
$("#gridPowerPct")?.addEventListener("input", syncGridLabels);
$("#gridFeedRate")?.addEventListener("input", syncGridLabels);
syncGridLabels();

function renderNetwork(info) {
  const toggle = $("#lanAccess");
  const status = $("#lanStatus");
  const urls = $("#lanUrls");
  if (!toggle || !status || !urls) return;
  toggle.checked = !!info.lan_access;
  if (info.restarting) {
    status.textContent = "Restarting server…";
    return;
  }
  if (info.active) {
    status.textContent = "Listening on all interfaces — LAN devices can connect.";
  } else if (info.restart_required && info.lan_access) {
    status.textContent = "Saved ON — restart server to apply (python run.py).";
  } else if (info.restart_required && !info.lan_access) {
    status.textContent = "Saved OFF — restart server to apply (python run.py).";
  } else {
    status.textContent = "Localhost only (127.0.0.1).";
  }
  const parts = [];
  if (info.local_urls?.length) {
    parts.push(`<div class="lan-label">This PC</div>`);
    for (const u of info.local_urls) {
      parts.push(`<a href="${u}">${u}</a>`);
    }
  }
  if (info.lan_urls?.length) {
    parts.push(`<div class="lan-label">Other devices on Wi‑Fi</div>`);
    for (const u of info.lan_urls) {
      parts.push(`<a href="${u}">${u}</a>`);
    }
  }
  urls.innerHTML = parts.join("");
  urls.classList.toggle("hidden", parts.length === 0);
}

async function loadNetwork() {
  try {
    renderNetwork(await api("/server/network"));
  } catch (e) {
    const status = $("#lanStatus");
    if (status) status.textContent = "Could not load network status.";
  }
}

async function waitForServer(timeoutMs = 20000, origin = "") {
  const start = Date.now();
  const base = origin || "";
  while (Date.now() - start < timeoutMs) {
    try {
      const r = await fetch(`${base}/api/server/network`, { cache: "no-store" });
      if (r.ok) return true;
    } catch (_) { /* still down */ }
    await new Promise((r) => setTimeout(r, 400));
  }
  return false;
}

function currentBrowserPort() {
  if (location.port) return Number(location.port);
  return location.protocol === "https:" ? 443 : 80;
}

function originForPort(port) {
  return `${location.protocol}//${location.hostname}:${port}`;
}

async function reloadAfterRestart(nextPort) {
  const port = Number(nextPort);
  const samePort = !Number.isFinite(port) || port === currentBrowserPort();
  const origin = samePort ? "" : originForPort(port);
  const up = await waitForServer(25000, origin);
  if (up) {
    if (samePort) location.reload();
    else location.href = `${origin}/`;
    return true;
  }
  return false;
}

function fillSettingsForm(values) {
  const v = values || {};
  const portEl = $("#cfgSerialPort");
  const baudEl = $("#cfgSerialBaud");
  const bedW = $("#cfgBedW");
  const bedH = $("#cfgBedH");
  const httpPort = $("#cfgHttpPort");
  const lan = $("#cfgLanAccess");
  if (portEl) portEl.value = v.serial_port ?? "auto";
  if (baudEl) baudEl.value = v.serial_baud ?? 115200;
  if (bedW) bedW.value = v.bed_width_mm ?? 400;
  if (bedH) bedH.value = v.bed_height_mm ?? 430;
  if (httpPort) httpPort.value = v.port ?? 8000;
  if (lan) lan.checked = !!v.lan_access;
}

function readSettingsForm() {
  return {
    serial_port: ($("#cfgSerialPort")?.value || "auto").trim() || "auto",
    serial_baud: Number($("#cfgSerialBaud")?.value || 115200),
    bed_width_mm: Number($("#cfgBedW")?.value || 400),
    bed_height_mm: Number($("#cfgBedH")?.value || 430),
    port: Number($("#cfgHttpPort")?.value || 8000),
    lan_access: !!$("#cfgLanAccess")?.checked,
    restart: true,
  };
}

function setSettingsStatus(text) {
  const el = $("#settingsStatus");
  if (el) el.textContent = text;
}

async function loadSettings() {
  try {
    const data = await api("/server/settings");
    fillSettingsForm(data.values);
    setSettingsStatus("Loaded from server/.env");
  } catch (e) {
    setSettingsStatus("Could not load settings.");
  }
}

function setUpdateStatus(text) {
  const el = $("#updateStatus");
  if (el) el.textContent = text || "";
}

function formatCommit(info) {
  if (!info) return "—";
  const short = info.short || (info.sha || "").slice(0, 7) || "?";
  const subject = (info.subject || "").trim();
  return subject ? `${short} · ${subject}` : short;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderUpdate(info) {
  const cur = $("#updateCurrent");
  const lat = $("#updateLatest");
  const commits = $("#updateCommits");
  const applyBtn = $("#btnApplyUpdate");
  if (!info) return;

  if (cur) {
    cur.textContent = formatCommit(info.current);
    cur.classList.remove("ok", "warn", "muted");
  }
  const ver = $("#updateVersion");
  if (ver && info.version) ver.textContent = `v${info.version}`;
  if (lat) {
    lat.classList.remove("ok", "warn", "muted");
    if (info.error && !info.latest) {
      lat.textContent = info.error;
      lat.classList.add("warn");
    } else if (info.update_available) {
      const n = info.commits_behind || 1;
      lat.textContent = `${formatCommit(info.latest)} (${n} behind)`;
      lat.classList.add("warn");
    } else if (info.latest) {
      lat.textContent = `${formatCommit(info.latest)} · up to date`;
      lat.classList.add("ok");
    } else {
      lat.textContent = "—";
      lat.classList.add("muted");
    }
  }
  if (commits) {
    const lines = info.commits || [];
    if (lines.length) {
      commits.innerHTML = lines.map((ln) => `<div>${escapeHtml(ln)}</div>`).join("");
      commits.classList.remove("hidden");
    } else {
      commits.innerHTML = "";
      commits.classList.add("hidden");
    }
  }
  if (applyBtn) {
    applyBtn.disabled = !info.update_available || !info.git;
  }
  if (info.dirty) {
    const files = (info.dirty_files || []).slice(0, 6).join(", ");
    setUpdateStatus(
      files
        ? `Local changes will be discarded on update (keeps .env): ${files}`
        : "Local changes will be discarded on update (keeps .env).",
    );
  } else if (info.update_available) {
    setUpdateStatus("Update available from GitHub.");
  } else if (info.error) {
    setUpdateStatus(info.error);
  } else if (info.git) {
    setUpdateStatus("You're on the latest main.");
  } else {
    setUpdateStatus(info.error || "Updates unavailable.");
  }
}

async function checkUpdate({ fetch = true, quiet = false } = {}) {
  const checkBtn = $("#btnCheckUpdate");
  const applyBtn = $("#btnApplyUpdate");
  if (!quiet) setUpdateStatus(fetch ? "Checking GitHub…" : "Loading…");
  if (checkBtn) checkBtn.disabled = true;
  if (applyBtn) applyBtn.disabled = true;
  try {
    const q = fetch ? "?fetch=1" : "?fetch=0";
    const data = await api(`/server/update${q}`);
    renderUpdate(data);
    return data;
  } catch (e) {
    setUpdateStatus(e.message || String(e));
    const lat = $("#updateLatest");
    if (lat) {
      lat.textContent = "Check failed";
      lat.classList.remove("ok", "muted");
      lat.classList.add("warn");
    }
    return null;
  } finally {
    if (checkBtn) checkBtn.disabled = false;
  }
}

$("#btnCheckUpdate")?.addEventListener("click", () => {
  checkUpdate({ fetch: true });
});

$("#btnApplyUpdate")?.addEventListener("click", async () => {
  const ok = await askConfirm(
    "Sync to GitHub main, reinstall Python deps, and restart. Local code changes are discarded; your .env is kept.",
    { title: "Update from GitHub", okText: "Update & restart" },
  );
  if (!ok) return;
  const checkBtn = $("#btnCheckUpdate");
  const applyBtn = $("#btnApplyUpdate");
  if (checkBtn) checkBtn.disabled = true;
  if (applyBtn) applyBtn.disabled = true;
  setUpdateStatus("Updating from GitHub… (may take a minute)");
  try {
    let data;
    try {
      data = await api("/server/update", {
        method: "POST",
        body: JSON.stringify({ restart: true }),
      });
    } catch (_) {
      data = { restarting: true };
    }
    if (data && data.ok === false) {
      throw new Error(data.error || "Update failed");
    }
    setUpdateStatus("Restarting server…");
    const nextPort = readSettingsForm().port;
    const up = await reloadAfterRestart(nextPort);
    if (!up) {
      setUpdateStatus("Restart timed out. Run: python run.py");
      await checkUpdate({ fetch: false, quiet: true });
    }
  } catch (err) {
    showAlert(err.message || String(err), "Update");
    setUpdateStatus(err.message || "Update failed.");
    await checkUpdate({ fetch: false, quiet: true });
  } finally {
    if (checkBtn) checkBtn.disabled = false;
  }
});

loadSettings();
checkUpdate({ fetch: true, quiet: true });

async function applySettingsRestart(request) {
  setSettingsStatus("Restarting server…");
  let data;
  try {
    data = await request();
  } catch (_) {
    // Server exits during restart — expected.
    data = { values: readSettingsForm(), restarting: true };
  }
  if (data?.values) fillSettingsForm(data.values);
  const nextPort = data?.values?.port ?? readSettingsForm().port;
  const up = await reloadAfterRestart(nextPort);
  if (!up) {
    setSettingsStatus(
      `Restart timed out. Open http://${location.hostname}:${nextPort}/ or run: python run.py`,
    );
    await loadSettings();
  }
}

$("#btnSaveSettings")?.addEventListener("click", async () => {
  const body = readSettingsForm();
  if (!Number.isFinite(body.serial_baud) || body.serial_baud < 1200) {
    showAlert("Baud rate must be a number ≥ 1200.", "Settings");
    return;
  }
  if (!Number.isFinite(body.bed_width_mm) || body.bed_width_mm <= 0) {
    showAlert("Bed width must be greater than 0.", "Settings");
    return;
  }
  if (!Number.isFinite(body.bed_height_mm) || body.bed_height_mm <= 0) {
    showAlert("Bed height must be greater than 0.", "Settings");
    return;
  }
  if (!Number.isFinite(body.port) || body.port < 1 || body.port > 65535) {
    showAlert("HTTP port must be between 1 and 65535.", "Settings");
    return;
  }
  const ok = await askConfirm(
    "Writes server/.env and restarts the app so bed size, baud, port, and LAN bind take effect.",
    { title: "Save settings", okText: "Save & restart" },
  );
  if (!ok) return;
  const btn = $("#btnSaveSettings");
  const resetBtn = $("#btnResetSettings");
  if (btn) btn.disabled = true;
  if (resetBtn) resetBtn.disabled = true;
  try {
    await applySettingsRestart(() =>
      api("/server/settings", { method: "PUT", body: JSON.stringify(body) }),
    );
  } catch (err) {
    showAlert(err.message || String(err), "Settings");
    setSettingsStatus("Save failed.");
  } finally {
    if (btn) btn.disabled = false;
    if (resetBtn) resetBtn.disabled = false;
  }
});

$("#btnResetSettings")?.addEventListener("click", async () => {
  const ok = await askConfirm(
    "Restore factory defaults (auto serial, 115200 baud, 400×430 bed, port 8000, LAN off) and restart.",
    { title: "Reset settings", okText: "Reset & restart", danger: true },
  );
  if (!ok) return;
  const btn = $("#btnSaveSettings");
  const resetBtn = $("#btnResetSettings");
  if (btn) btn.disabled = true;
  if (resetBtn) resetBtn.disabled = true;
  try {
    await applySettingsRestart(() =>
      api("/server/settings/reset", {
        method: "POST",
        body: JSON.stringify({ restart: true }),
      }),
    );
  } catch (err) {
    showAlert(err.message || String(err), "Settings");
    setSettingsStatus("Reset failed.");
  } finally {
    if (btn) btn.disabled = false;
    if (resetBtn) resetBtn.disabled = false;
  }
});

$("#lanAccess")?.addEventListener("change", async (e) => {
  const toggle = e.target;
  const want = !!toggle.checked;
  toggle.disabled = true;
  try {
    if (want) {
      const ok = await askConfirm(
        "Other devices on your Wi‑Fi will be able to open this UI and control the laser. Only enable on a trusted network.",
        { title: "Enable LAN access", okText: "Enable & restart", danger: true },
      );
      if (!ok) {
        toggle.checked = false;
        return;
      }
    } else {
      const ok = await askConfirm(
        "Server will restart and bind to localhost only. Phones/tablets on Wi‑Fi will lose access.",
        { title: "Disable LAN access", okText: "Disable & restart" },
      );
      if (!ok) {
        toggle.checked = true;
        return;
      }
    }
    const status = $("#lanStatus");
    if (status) status.textContent = "Restarting server…";
    let data;
    try {
      data = await api("/server/network", {
        method: "POST",
        body: JSON.stringify({ lan_access: want, restart: true }),
      });
    } catch (_) {
      // Server exits during restart — expected.
      data = { lan_access: want, restarting: true, local_urls: [], lan_urls: [] };
    }
    renderNetwork({ ...data, restarting: true });
    const up = await waitForServer();
    if (up) {
      location.reload();
    } else {
      if (status) {
        status.textContent = "Restart timed out. Run: python run.py";
      }
      await loadNetwork();
    }
  } catch (err) {
    showAlert(err.message || String(err), "Network");
    toggle.checked = !want;
  } finally {
    toggle.disabled = false;
  }
});

loadNetwork();

$("#btnCreateGrid")?.addEventListener("click", async () => {
  const minor = Number($("#gridMinor")?.value || 50);
  const major = Number($("#gridMajor")?.value || 100);
  try {
    const data = await api("/jobs/from-grid", {
      method: "POST",
      body: JSON.stringify({
        minor_mm: minor,
        major_mm: major,
        power_pct: Number($("#gridPowerPct")?.value || 30),
        feed: Number($("#gridFeedRate")?.value || 900),
        home_first: !!$("#gridHomeFirst")?.checked,
        inset_mm: Number($("#gridInset")?.value || 0),
      }),
    });
    setJob(data);
  } catch (e) {
    showAlert(e.message, "Grid create failed");
  }
});

$("#btnDry").onclick = async () => {
  if (!currentJobId) return;
  const homeFirst = !!$("#homeBeforeRun")?.checked;
  const homeNote = homeFirst ? " Machine will HOME first (~30–60s)." : "";
  const ok = await askConfirm(
    `Dry-run: motion only — laser stays OFF (M5/S0 forced).${homeNote}`,
    { title: "Start dry run", okText: "Start dry run" },
  );
  if (!ok) return;
  try {
    const s = await api(`/jobs/${currentJobId}/send-dry`, {
      method: "POST",
      body: JSON.stringify({ home_first: homeFirst }),
    });
    applyStatus(s);
    startPoll();
  } catch (e) {
    showAlert(e.message);
  }
};

$("#btnSend").onclick = async () => {
  if (!currentJobId) return;
  const homeFirst = !!$("#homeBeforeRun")?.checked;
  const homeNote = homeFirst ? " Machine will HOME first (~30–60s)." : "";
  try {
    const st = await api("/device/status");
    if (!st.armed) {
      if (!(await askArmConfirm({ alsoSend: true }))) return;
      await api("/device/arm", { method: "POST", body: "{}" });
    } else {
      const ok = await askConfirm(
        `LIVE laser job. Wear eye protection. Machine is ARMED.${homeNote}`,
        { title: "Send live job", okText: "Send", danger: true },
      );
      if (!ok) return;
    }
    const s = await api(`/jobs/${currentJobId}/send`, {
      method: "POST",
      body: JSON.stringify({ armed_confirm: true, home_first: homeFirst }),
    });
    applyStatus(s);
    startPoll();
  } catch (e) {
    showAlert(e.message);
  }
};

$("#file").onchange = () => {
  const file = $("#file").files[0];
  if (!file) { uploadImage = null; drawPreview(); return; }
  const url = URL.createObjectURL(file);
  const img = new Image();
  img.onload = () => { uploadImage = img; drawPreview(); };
  img.src = url;
  // switch to upload tab visually already active
};

["canvasText", "fontName", "ox", "oy", "engraveMode", "imageFit", "burnPolarity", "previewMode"].forEach((id) => {
  const el = $(`#${id}`);
  if (!el) return;
  el.addEventListener("input", () => { clampJobInputs(); drawPreview(); });
  el.addEventListener("change", () => { clampJobInputs(); drawPreview(); });
});

$("#lockAspect")?.addEventListener("change", () => {
  if (lockAspectOn()) refreshAspectFromInputs();
});

$("#width")?.addEventListener("input", () => {
  if (lockAspectOn() && aspectRatio > 0) {
    const w = Math.max(1, parseFloat($("#width").value) || 1);
    $("#height").value = Math.round((w / aspectRatio) * 10) / 10;
  } else {
    refreshAspectFromInputs();
  }
  clampJobInputs();
  drawPreview();
});

$("#height")?.addEventListener("input", () => {
  if (lockAspectOn() && aspectRatio > 0) {
    const h = Math.max(1, parseFloat($("#height").value) || 1);
    $("#width").value = Math.round(h * aspectRatio * 10) / 10;
  } else {
    refreshAspectFromInputs();
  }
  clampJobInputs();
  drawPreview();
});

["powerPct", "feedRate"].forEach((id) => {
  const el = $(`#${id}`);
  if (!el) return;
  el.addEventListener("input", syncPowerLabels);
  el.addEventListener("change", syncPowerLabels);
});

$("#preset")?.addEventListener("change", () => {
  applyPresetToSliders();
});

syncPowerLabels();
refreshAspectFromInputs();

$("#btnClearTrail").onclick = () => {
  headTrail = [];
  drawPreview();
};

$("#btnFitCanvas").onclick = async () => {
  if (!uploadImage) return showAlert("Upload an image first", "Size canvas");
  const w = parseFloat($("#width").value) || 40;
  const aspect = uploadImage.height / uploadImage.width;
  let h = Math.round(w * aspect * 10) / 10;
  if (h > BED_H) {
    h = BED_H;
    $("#width").value = Math.round((h / aspect) * 10) / 10;
  }
  $("#height").value = h;
  refreshAspectFromInputs();
  if ($("#lockAspect")) $("#lockAspect").checked = true;
  clampJobInputs();
  // default to fill so the image occupies the whole box
  $("#imageFit").value = "fill";
  drawPreview();
};

function canvasCoords(e) {
  const rect = canvas.getBoundingClientRect();
  return {
    px: (e.clientX - rect.left) * (canvas.width / rect.width),
    py: (e.clientY - rect.top) * (canvas.height / rect.height),
  };
}

function hitTest(px, py) {
  const box = jobBox();
  const tl = mmToPx(box.x, box.y + box.h);
  const br = mmToPx(box.x + box.w, box.y);
  const inBox =
    px >= Math.min(tl.px, br.px) - 2 &&
    px <= Math.max(tl.px, br.px) + 2 &&
    py >= Math.min(tl.py, br.py) - 2 &&
    py <= Math.max(tl.py, br.py) + 2;
  const nearHandle = Math.hypot(px - br.px, py - br.py) <= HANDLE;
  if (nearHandle) return "resize";
  if (inBox) return "move";
  return null;
}

canvas.addEventListener("pointerdown", (e) => {
  const { px, py } = canvasCoords(e);
  const mode = hitTest(px, py);
  if (!mode) return;
  const mm = pxToMm(px, py);
  const box = jobBox();
  drag = {
    mode,
    ox: mm.x - box.x,
    oy: mm.y - box.y,
    startBox: { ...box },
    startMm: mm,
    aspect: box.w / Math.max(0.001, box.h),
  };
  canvas.setPointerCapture(e.pointerId);
  e.preventDefault();
});

canvas.addEventListener("pointermove", (e) => {
  const { px, py } = canvasCoords(e);
  if (!drag) {
    const mode = hitTest(px, py);
    canvas.style.cursor = mode === "resize" ? "nwse-resize" : mode === "move" ? "grab" : "default";
    return;
  }
  const mm = pxToMm(px, py);
  if (drag.mode === "move") {
    $("#ox").value = Math.round((mm.x - drag.ox) * 10) / 10;
    $("#oy").value = Math.round((mm.y - drag.oy) * 10) / 10;
  } else {
    // resize from origin corner — optionally keep W:H
    let w = Math.max(5, mm.x - drag.startBox.x);
    let h = Math.max(5, mm.y - drag.startBox.y);
    if (lockAspectOn() && drag.aspect > 0) {
      if (w / drag.aspect >= h) {
        h = w / drag.aspect;
      } else {
        w = h * drag.aspect;
      }
      w = Math.max(5, w);
      h = Math.max(5, h);
      aspectRatio = drag.aspect;
    } else {
      aspectRatio = w / h;
    }
    $("#width").value = Math.round(w * 10) / 10;
    $("#height").value = Math.round(h * 10) / 10;
  }
  clampJobInputs();
  drawPreview();
});

canvas.addEventListener("pointerup", () => { drag = null; });
canvas.addEventListener("pointercancel", () => { drag = null; });

canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  const factor = e.deltaY < 0 ? 1.05 : 0.95;
  let w = parseFloat($("#width").value) || 40;
  let h = parseFloat($("#height").value) || 40;
  if (lockAspectOn() && aspectRatio > 0) {
    w = Math.max(5, w * factor);
    h = w / aspectRatio;
  } else {
    w = Math.max(5, w * factor);
    h = Math.max(5, h * factor);
    aspectRatio = w / h;
  }
  $("#width").value = Math.round(w * 10) / 10;
  $("#height").value = Math.round(h * 10) / 10;
  clampJobInputs();
  drawPreview();
}, { passive: false });

document.addEventListener("keydown", async (e) => {
  const modalOpen = !$("#appModal")?.classList.contains("hidden");
  if (e.key === "Escape") {
    if (modalOpen) {
      closeModal(false);
      return;
    }
    if (lastStatus?.job_running) {
      e.preventDefault();
      const ok = await askConfirm("Cancel the running job now?", {
        title: "Cancel job",
        okText: "Cancel job",
        danger: true,
      });
      if (ok) $("#btnCancel")?.click();
    }
    return;
  }

  if (modalOpen || isTypingTarget(document.activeElement)) return;
  if (!lastStatus?.connected || lastStatus?.job_running) return;

  const step = parseFloat($("#jogStep")?.value) || 10;
  const map = {
    ArrowUp: { axis: "Y", dir: 1 },
    ArrowDown: { axis: "Y", dir: -1 },
    ArrowLeft: { axis: "X", dir: -1 },
    ArrowRight: { axis: "X", dir: 1 },
  };
  const move = map[e.key];
  if (!move) return;
  e.preventDefault();
  postAct("/device/jog", {
    axis: move.axis,
    distance_mm: step * move.dir,
    feed: 2000,
  });
});

loadPorts()
  .then(loadFonts)
  .then(loadPresets)
  .then(refreshStatus)
  .then(startPoll)
  .then(() => {
    setJogStep($("#jogStep")?.value || 10);
    updateConnectionUi(false);
    updateArmUi(false);
    updateRunUi();
    drawPreview();
  });
