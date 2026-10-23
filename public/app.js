const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const SAMPLES = [
  "Namaskara anna 🙏\n2 kg tamatar\nnandini haalu 2\ntogari bele 1kg\nAmul 500\nkothambari soppu 1\nhasi menasinakai 100g\neerulli 2kg\nmosaru 1",
  "Bhaiya ye bhej dena\naata 5kg\ncheeni 1 kilo\nchai patti red label\nmaggi 2 packet\nande 1 dozen\nsurf 1kg\nharpic",
  "hi pls send by 6\nrice 10kg sona\noil 2 pouch\njeera 250g, rai 100g, haldi\ncoconut 2\nparle g 3\nvim bar\nbournvita 500g\nthanks",
];

const state = {
  catalogue: [],
  order: null,
  startedAt: null,
  photo: null, // {base64, mediaType, url}
  timer: null,
  health: null,
};

const FLAG_INFO = {
  low_confidence: { label: "Check item", cls: "warn" },
  unmatched: { label: "Not in catalogue", cls: "danger" },
  out_of_stock: { label: "Out of stock", cls: "danger" },
  low_stock: { label: "Low stock", cls: "warn" },
};

// ---------- helpers ----------

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: options.body ? { "Content-Type": "application/json" } : {},
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) throw new Error((data && data.detail) || `Request failed (${res.status})`);
  return data;
}

function rupees(n) {
  const v = Number(n) || 0;
  return "₹" + v.toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

function fmtQty(q) {
  return Number.isInteger(q) ? String(q) : String(+q.toFixed(2));
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), 2600);
}

function timeAgo(iso) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

function fmtSeconds(s) {
  if (s == null) return "–";
  if (s < 60) return `${Math.round(s)}s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

// ---------- tabs ----------

function showView(name) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === name));
  $$(".view").forEach((v) => (v.hidden = v.id !== `view-${name}`));
  if (name === "orders") loadOrders();
  if (name === "catalogue") loadCatalogue().then(renderCatalogue);
  window.scrollTo({ top: 0 });
}

$$(".tab").forEach((t) => t.addEventListener("click", () => showView(t.dataset.view)));

// ---------- composer ----------

function markStarted() {
  if (!state.startedAt) state.startedAt = new Date().toISOString();
}

$("#listText").addEventListener("input", markStarted);
$("#listText").addEventListener("paste", markStarted);

$$("[data-sample]").forEach((b) =>
  b.addEventListener("click", () => {
    $("#listText").value = SAMPLES[+b.dataset.sample];
    state.startedAt = new Date().toISOString();
    $("#listText").focus();
  })
);

$("#photoInput").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  markStarted();
  try {
    state.photo = await shrinkImage(file);
    $("#photoImg").src = state.photo.url;
    $("#photoPreview").hidden = false;
  } catch {
    showComposeError("That photo could not be opened. Try another one.");
  }
  e.target.value = "";
});

$("#photoClear").addEventListener("click", () => {
  state.photo = null;
  $("#photoPreview").hidden = true;
});

// Phone photos are large; resize so the upload stays well under Vercel's 4.5 MB limit.
function shrinkImage(file, maxSide = 1600) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const scale = Math.min(1, maxSide / Math.max(img.width, img.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(img.width * scale);
      canvas.height = Math.round(img.height * scale);
      canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
      const url = canvas.toDataURL("image/jpeg", 0.85);
      URL.revokeObjectURL(img.src);
      resolve({ url, base64: url.split(",")[1], mediaType: "image/jpeg" });
    };
    img.onerror = reject;
    img.src = URL.createObjectURL(file);
  });
}

function showComposeError(msg) {
  const el = $("#composeError");
  el.textContent = msg;
  el.hidden = !msg;
}

$("#makeQuote").addEventListener("click", async () => {
  const text = $("#listText").value.trim();
  if (!text && !state.photo) return showComposeError("Paste a list or add a photo first.");
  showComposeError("");
  markStarted();
  $("#composer").hidden = true;
  $("#working").hidden = false;
  try {
    const [order] = await Promise.all([
      api("/api/quote", {
        method: "POST",
        body: {
          text,
          image_base64: state.photo?.base64 || null,
          media_type: state.photo?.mediaType || null,
          customer_name: $("#custName").value,
          customer_phone: $("#custPhone").value,
          started_at: state.startedAt,
        },
      }),
      state.catalogue.length ? null : loadCatalogue(),
    ]);
    openOrder(order);
  } catch (err) {
    $("#composer").hidden = false;
    showComposeError(err.message);
  } finally {
    $("#working").hidden = true;
  }
});

function resetComposer() {
  stopTimer();
  state.order = null;
  state.startedAt = null;
  state.photo = null;
  $("#listText").value = "";
  $("#custName").value = "";
  $("#custPhone").value = "";
  $("#photoPreview").hidden = true;
  $("#review").hidden = true;
  $("#composer").hidden = false;
  showComposeError("");
}

// ---------- review ----------

function startTimer() {
  stopTimer();
  const tick = () => {
    const el = $("#timer");
    if (!el || !state.order) return;
    const s = (Date.now() - new Date(state.order.started_at).getTime()) / 1000;
    el.textContent = fmtSeconds(Math.max(0, s));
  };
  tick();
  state.timer = setInterval(tick, 1000);
}

function stopTimer() {
  clearInterval(state.timer);
  state.timer = null;
}

async function openOrder(order) {
  if (!state.catalogue.length) await loadCatalogue();
  state.order = order;
  showView("quote");
  $("#composer").hidden = true;
  $("#review").hidden = false;
  if (order.status === "approved") {
    stopTimer();
    renderApproved();
  } else {
    renderReview();
    startTimer();
  }
}

function catalogueOptions(selected) {
  const groups = {};
  for (const c of state.catalogue) (groups[c.category || "Other"] ||= []).push(c);
  let html = `<option value="">— Not in catalogue —</option>`;
  for (const [cat, items] of Object.entries(groups)) {
    html += `<optgroup label="${esc(cat)}">`;
    for (const c of items) {
      html += `<option value="${esc(c.id)}" ${c.id === selected ? "selected" : ""}>${esc(c.name)} · ${esc(c.unit)} · ${rupees(c.price)}</option>`;
    }
    html += `</optgroup>`;
  }
  return html;
}

function renderReview() {
  const o = state.order;
  const flagged = o.lines.filter((l) => l.flags.length);
  const problems = o.lines.filter((l) => l.flags.some((f) => FLAG_INFO[f]?.cls === "danger"));
  const engine = o.engine === "claude"
    ? `<span class="role ai">Claude</span> read this list`
    : `Read by the basic offline matcher (Claude not connected)`;
  const lang = o.reply_language && o.reply_language !== "english"
    ? ` · reply drafted in ${o.reply_language[0].toUpperCase()}${o.reply_language.slice(1)}` : "";

  $("#review").innerHTML = `
    <div class="card">
      <div class="review-head">
        <div>
          <h1>Quote for ${esc(o.customer_name || "walk-in customer")}</h1>
          <div class="muted small">${engine} · ${o.source_type === "photo" ? "from photo" : "from pasted list"}${esc(lang)} · <span class="timer" id="timer">0s</span> since the list came in</div>
        </div>
        <button class="ghost small-btn" id="startOver">Start over</button>
      </div>
      <div class="summary">
        <span class="pill neutral">${o.lines.length} item${o.lines.length === 1 ? "" : "s"}</span>
        ${flagged.length
          ? `<span class="pill warn">⚑ ${flagged.length} need${flagged.length === 1 ? "s" : ""} a look</span>`
          : `<span class="pill ok">✓ All items matched</span>`}
        ${problems.length ? `<span class="pill danger">${problems.length} can't be supplied as is</span>` : ""}
      </div>
      <div class="lines">
        <div class="line-head"><span>Item</span><span>Qty</span><span>Price (₹)</span><span>Amount</span><span></span></div>
        ${o.lines.map(renderLine).join("")}
      </div>
      <button class="link" id="addLine" style="margin-top:12px">+ Add an item</button>
      <div class="totals">
        <div>
          <div class="muted small">Total</div>
          <div class="grand" id="grandTotal">${rupees(o.total)}</div>
        </div>
        <div class="actions">
          <button class="primary" id="approveBtn">Approve quote</button>
        </div>
      </div>
      ${o.raw_text ? `<details style="margin-top:14px"><summary class="muted small">Original list</summary><div class="message-box">${esc(o.raw_text)}</div></details>` : ""}
    </div>`;

  $("#startOver").onclick = resetComposer;
  $("#addLine").onclick = () => {
    o.lines.push({ line_id: null, raw: "", catalogue_id: "", quantity: 1, unit_price: null, confidence: 1, note: "", reviewed: true, flags: ["unmatched"] });
    renderReview();
    const selects = $$(".line select");
    selects[selects.length - 1].focus();
  };
  $("#approveBtn").onclick = approve;

  $$(".line").forEach((row) => {
    const idx = +row.dataset.idx;
    const line = o.lines[idx];
    row.querySelector("select").onchange = (e) => {
      line.catalogue_id = e.target.value;
      line.unit_price = null; // new item, use its catalogue price
      line.reviewed = true;
      saveLines();
    };
    row.querySelector(".qty").onchange = (e) => { line.quantity = parseFloat(e.target.value) || 0; saveLines(); };
    row.querySelector(".price").onchange = (e) => {
      const v = e.target.value.trim();
      line.unit_price = v === "" ? null : parseFloat(v);
      saveLines();
    };
    row.querySelector(".remove").onclick = () => { o.lines.splice(idx, 1); saveLines(); };
    const ok = row.querySelector(".ok-btn");
    if (ok) ok.onclick = () => { line.reviewed = true; saveLines(); };
  });
}

function renderLine(l, idx) {
  const isProblem = l.flags.some((f) => FLAG_INFO[f]?.cls === "danger");
  const cls = isProblem ? "problem" : l.flags.length ? "flagged" : "";
  const flags = l.flags.map((f) => `<span class="pill ${FLAG_INFO[f].cls}">${FLAG_INFO[f].label}</span>`).join("");
  const priceChanged = l.catalogue_price != null && l.unit_price !== l.catalogue_price;
  return `
    <div class="line ${cls}" data-idx="${idx}">
      <div class="item-cell">
        ${l.raw ? `<div class="raw">Asked: <b>${esc(l.raw)}</b></div>` : ""}
        <select aria-label="Item">${catalogueOptions(l.catalogue_id)}</select>
        ${flags || l.note || l.stock != null ? `<div class="flags">
          ${l.raw && l.catalogue_id ? confidenceTag(l.confidence) : ""}
          ${flags}
          ${l.note ? `<span class="muted small">${esc(l.note)}</span>` : ""}
          ${l.flags.includes("low_confidence") ? `<button class="ok-btn">Looks right</button>` : ""}
          ${l.flags.includes("low_stock") ? `<span class="muted small">only ${fmtQty(l.stock)} left</span>` : ""}
        </div>` : ""}
      </div>
      <div><span class="field-label">Qty</span><input class="qty" type="number" min="0" step="any" value="${fmtQty(l.quantity)}" aria-label="Quantity"></div>
      <div><span class="field-label">Price ₹${priceChanged ? " (edited)" : ""}</span><input class="price" type="number" min="0" step="any" value="${l.catalogue_id ? l.unit_price : ""}" placeholder="–" aria-label="Unit price" ${l.catalogue_id ? "" : "disabled"}></div>
      <div class="total">${l.catalogue_id ? rupees(l.line_total) : "–"}</div>
      <button class="remove" title="Remove line" aria-label="Remove line">×</button>
    </div>`;
}

function confidenceTag(c) {
  const pct = Math.round((c ?? 1) * 100);
  const cls = pct >= 90 ? "high" : pct < 75 ? "low" : "";
  return `<span class="conf ${cls}" title="How sure Claude is about this match">${pct}% sure</span>`;
}

function linesPayload() {
  return state.order.lines.map((l) => ({
    line_id: l.line_id, raw: l.raw, catalogue_id: l.catalogue_id, quantity: l.quantity,
    // Only send a price when the owner changed it, so catalogue updates flow through.
    unit_price: l.unit_price != null && l.unit_price !== l.catalogue_price ? l.unit_price : null,
    confidence: l.confidence, note: l.note, reviewed: l.reviewed,
  }));
}

// Every edit goes back to the server, where the pricing script recomputes totals and flags.
let saving = Promise.resolve();
function saveLines() {
  const o = state.order;
  saving = saving.then(async () => {
    try {
      const updated = await api(`/api/orders/${o.id}`, { method: "PUT", body: { lines: linesPayload() } });
      state.order = updated;
      renderReview();
    } catch (err) {
      toast(err.message);
    }
  });
  return saving;
}

async function approve() {
  const o = state.order;
  const problems = o.lines.filter((l) => l.flags.includes("low_confidence") || l.flags.includes("out_of_stock"));
  if (problems.length && !confirm(`${problems.length} flagged item${problems.length === 1 ? " is" : "s are"} still in the quote. Approve anyway?`)) return;
  $("#approveBtn").disabled = true;
  await saving;
  try {
    state.order = await api(`/api/orders/${o.id}/approve`, { method: "POST", body: { lines: linesPayload() } });
    stopTimer();
    renderApproved();
    loadCatalogue(); // stock changed
  } catch (err) {
    toast(err.message);
    $("#approveBtn").disabled = false;
  }
}

function waLink(o) {
  let phone = (o.customer_phone || "").replace(/\D/g, "");
  if (phone.length === 10) phone = "91" + phone;
  return `https://wa.me/${phone}?text=${encodeURIComponent(o.message)}`;
}

function renderApproved() {
  const o = state.order;
  const secs = o.seconds_to_approve;
  $("#review").innerHTML = `
    <div class="card">
      <div class="done-hero">
        <div class="tick">✓</div>
        <div>
          <h1>Quote approved · ${rupees(o.total)}</h1>
          <div class="muted small">${secs != null ? `List to approved quote in <b>${fmtSeconds(secs)}</b>${secs <= 60 ? " — under a minute 🎉" : ""}` : "Approved"} · ${esc(o.customer_name || "walk-in customer")}</div>
        </div>
      </div>
      <div class="message-box" id="msgBox">${esc(o.message)}</div>
      <div class="actions" style="margin-top:14px">
        <button class="primary" id="copyMsg">Copy for WhatsApp</button>
        <a class="ghost" id="openWa" href="${waLink(o)}" target="_blank" rel="noopener" style="text-align:center">Open WhatsApp</a>
        <button class="ghost" id="nextOrder">Next order</button>
      </div>
      ${fieldTestHtml(o)}
    </div>`;
  bindFieldTest(o);
  $("#copyMsg").onclick = async () => {
    try {
      await navigator.clipboard.writeText(o.message);
      toast("Copied. Paste it in the WhatsApp chat.");
    } catch {
      const r = document.createRange();
      r.selectNodeContents($("#msgBox"));
      getSelection().removeAllRanges();
      getSelection().addRange(r);
      toast("Select all and copy");
    }
  };
  $("#nextOrder").onclick = resetComposer;
}

// ---------- field test: same order timed by hand ----------

function fieldTestHtml(o) {
  if (o.manual_seconds) {
    const saved = o.manual_seconds - (o.seconds_to_approve || 0);
    return `
      <div class="field-test">
        <b>Field test</b> <span class="muted small">same list, by hand vs with PADOSI</span>
        <div class="compare">
          <div><div class="muted small">By hand</div><div class="v">${fmtSeconds(o.manual_seconds)}</div></div>
          <div><div class="muted small">With PADOSI</div><div class="v">${fmtSeconds(o.seconds_to_approve)}</div></div>
        </div>
        <div class="muted small" style="margin-top:8px">${saved > 0 ? `Saved ${fmtSeconds(saved)} on this order.` : "No time saved on this order."} <button class="link" id="ftRedo">Redo</button></div>
      </div>`;
  }
  return `
    <div class="field-test">
      <b>Field test</b> <span class="muted small">Ask the owner to quote the same list by hand (read, find prices, add up, type the reply). Time it here to compare.</span>
      <div class="ft-row">
        <span class="stopwatch" id="ftClock">0:00</span>
        <button class="ghost small-btn" id="ftStart">Start stopwatch</button>
        <span class="muted small">or type seconds</span>
        <input id="ftSeconds" type="number" min="1" step="1" placeholder="e.g. 360" inputmode="numeric">
        <button class="primary small-btn" id="ftSave" style="color:var(--brand-ink)">Save</button>
      </div>
    </div>`;
}

function bindFieldTest(o) {
  const redo = $("#ftRedo");
  if (redo) {
    redo.onclick = () => { o.manual_seconds = null; renderApproved(); };
    return;
  }
  let started = null, tick = null;
  $("#ftStart").onclick = () => {
    if (!started) {
      started = Date.now();
      $("#ftStart").textContent = "Stop";
      tick = setInterval(() => {
        const s = Math.floor((Date.now() - started) / 1000);
        $("#ftClock").textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
      }, 250);
    } else {
      clearInterval(tick);
      $("#ftSeconds").value = Math.round((Date.now() - started) / 1000);
      started = null;
      $("#ftStart").textContent = "Start again";
    }
  };
  $("#ftSave").onclick = async () => {
    clearInterval(tick);
    const seconds = parseFloat($("#ftSeconds").value);
    if (!(seconds > 0)) return toast("Time the order first, or type the seconds.");
    try {
      state.order = await api(`/api/orders/${o.id}/manual`, { method: "POST", body: { seconds } });
      renderApproved();
    } catch (err) {
      toast(err.message);
    }
  };
}

// ---------- orders ----------

async function loadOrders() {
  try {
    const [orders, stats] = await Promise.all([api("/api/orders"), api("/api/stats")]);
    renderStats(stats);
    renderOrders(orders);
  } catch (err) {
    toast(err.message);
  }
}

$("#refreshOrders").onclick = loadOrders;

function renderStats(s) {
  const tiles = [
    ["Avg. list → quote", fmtSeconds(s.avg_seconds_to_approve)],
    ["Under a minute", s.orders_approved ? `${s.under_a_minute} / ${s.orders_approved}` : "–"],
    ["Approved today", s.approved_today],
    ["Value approved", rupees(s.revenue_approved)],
  ].map(([k, v]) => `<div class="stat"><div class="v">${v}</div><div class="k">${k}</div></div>`);
  if (s.paired_orders) {
    const n = s.paired_orders;
    tiles.unshift(`
      <div class="stat wide">
        <div class="v">${fmtSeconds(s.avg_manual_seconds)} by hand → ${fmtSeconds(s.avg_padosi_seconds_paired)} with PADOSI</div>
        <div class="k">Field test: ${n} order${n === 1 ? "" : "s"} timed both ways (average)</div>
      </div>`);
  }
  $("#stats").innerHTML = tiles.join("");
}

function renderOrders(orders) {
  if (!orders.length) {
    $("#ordersList").innerHTML = `<div class="empty">No quotes yet. Paste a list in <b>New quote</b> to make the first one.</div>`;
    return;
  }
  $("#ordersList").innerHTML = orders.map((o) => `
    <div class="order-row" data-id="${o.id}">
      <div>
        <div class="who">${esc(o.customer_name || "Walk-in customer")}</div>
        <div class="muted small">${o.lines.length} items · ${timeAgo(o.created_at)}${o.seconds_to_approve != null ? ` · quoted in ${fmtSeconds(o.seconds_to_approve)}` : ""}</div>
      </div>
      <span class="pill ${o.status === "approved" ? "ok" : o.flagged_count ? "warn" : "neutral"}">${o.status === "approved" ? "Approved" : o.flagged_count ? `Draft · ${o.flagged_count} flagged` : "Draft"}</span>
      <div class="amt">${rupees(o.total)}</div>
    </div>`).join("");
  $$(".order-row").forEach((row) =>
    row.addEventListener("click", async () => {
      try {
        openOrder(await api(`/api/orders/${row.dataset.id}`));
      } catch (err) {
        toast(err.message);
      }
    })
  );
}

// ---------- catalogue ----------

async function loadCatalogue() {
  state.catalogue = await api("/api/catalogue");
  return state.catalogue;
}

function renderCatalogue() {
  const q = $("#catSearch").value.trim().toLowerCase();
  const items = state.catalogue.filter((c) => !q || `${c.name} ${c.aliases} ${c.id} ${c.category}`.toLowerCase().includes(q));
  const outOfStock = state.catalogue.filter((c) => c.stock <= 0).length;
  $("#catCount").textContent = `${state.catalogue.length} items${outOfStock ? ` · ${outOfStock} out of stock` : ""}`;
  $("#catTable").innerHTML = `
    <table>
      <thead><tr><th>Item</th><th>Category</th><th>Unit</th><th class="num">Price</th><th class="num">Stock</th></tr></thead>
      <tbody>
        ${items.map((c) => `
          <tr data-id="${esc(c.id)}">
            <td><div>${esc(c.name)}</div><div class="aliases">${esc(c.aliases.split(";").join(", "))}</div></td>
            <td>${esc(c.category)}</td>
            <td>${esc(c.unit)}</td>
            <td class="num editable" data-field="price">${rupees(c.price)}</td>
            <td class="num editable ${c.stock <= 0 ? "stock-zero" : ""}" data-field="stock">${fmtQty(c.stock)}</td>
          </tr>`).join("")}
      </tbody>
    </table>
    ${items.length ? "" : `<div class="empty">No items match “${esc(q)}”.</div>`}`;

  $$("td.editable").forEach((td) => td.addEventListener("click", () => editCell(td)));
}

function editCell(td) {
  if (td.querySelector("input")) return;
  const id = td.parentElement.dataset.id;
  const field = td.dataset.field;
  const item = state.catalogue.find((c) => c.id === id);
  td.innerHTML = `<input type="number" min="0" step="any" value="${item[field]}">`;
  const input = td.querySelector("input");
  input.focus();
  input.select();
  let done = false;
  const commit = async (save) => {
    if (done) return;
    done = true;
    const value = parseFloat(input.value);
    if (save && !Number.isNaN(value) && value !== item[field]) {
      try {
        await api(`/api/catalogue/${encodeURIComponent(id)}`, { method: "PATCH", body: { [field]: value } });
        item[field] = value;
        toast(`${item.name}: ${field} updated`);
      } catch (err) {
        toast(err.message);
      }
    }
    renderCatalogue();
  };
  input.addEventListener("blur", () => commit(true));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") commit(true);
    if (e.key === "Escape") commit(false);
  });
}

$("#catSearch").addEventListener("input", renderCatalogue);

$("#csvInput").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  const replace = confirm("Replace the whole catalogue with this file?\n\nOK = replace everything\nCancel = add / update items only");
  try {
    const res = await api("/api/catalogue/upload", { method: "POST", body: { csv: await file.text(), replace } });
    state.catalogue = res.catalogue;
    renderCatalogue();
    toast(`Imported ${res.imported} items`);
  } catch (err) {
    toast(err.message);
  }
});

$("#resetCat").addEventListener("click", async () => {
  if (!confirm("Replace the catalogue with the sample Bengaluru kirana catalogue?")) return;
  try {
    state.catalogue = await api("/api/catalogue/reset", { method: "POST" });
    renderCatalogue();
    toast("Sample catalogue loaded");
  } catch (err) {
    toast(err.message);
  }
});

// ---------- supplier bill → catalogue (Claude) ----------

const bill = { photo: null, rows: [] };
const CATEGORIES = ["Staples", "Pulses", "Oils", "Dairy", "Vegetables", "Fruits", "Masala", "Beverages",
  "Snacks", "Household", "Personal care", "Pooja", "Bakery", "Other"];

function showBillError(msg) {
  $("#billError").textContent = msg;
  $("#billError").hidden = !msg;
}

$("#billPhoto").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  try {
    bill.photo = await shrinkImage(file, 2000);
    $("#billImg").src = bill.photo.url;
    $("#billPreview").hidden = false;
  } catch {
    showBillError("That photo could not be opened. Try another one.");
  }
});

$("#billClear").addEventListener("click", () => {
  bill.photo = null;
  $("#billPreview").hidden = true;
});

$("#readBill").addEventListener("click", async () => {
  const text = $("#billText").value.trim();
  if (!text && !bill.photo) return showBillError("Paste a price list or add a photo of a bill first.");
  showBillError("");
  $("#billResult").hidden = true;
  $("#billWorking").hidden = false;
  $("#readBill").disabled = true;
  try {
    const res = await api("/api/catalogue/extract", {
      method: "POST",
      body: { text, image_base64: bill.photo?.base64 || null, media_type: bill.photo?.mediaType || null },
    });
    bill.rows = res.items.map((r) => ({ ...r, keep: true }));
    if (!bill.rows.length) showBillError("Claude didn't find any products in that. Try a clearer photo.");
    else renderBillRows();
  } catch (err) {
    showBillError(err.message);
  } finally {
    $("#billWorking").hidden = true;
    $("#readBill").disabled = false;
  }
});

function renderBillRows() {
  const box = $("#billResult");
  const kept = bill.rows.filter((r) => r.keep).length;
  box.hidden = false;
  box.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead><tr><th></th><th>Item</th><th>Shopper names</th><th>Category</th><th>Unit</th><th class="num">Price ₹</th><th class="num">+ Stock</th><th></th></tr></thead>
        <tbody>
          ${bill.rows.map((r, i) => {
            const priceChange = r.existing_id && r.current_price !== r.price;
            const tag = r.existing_id
              ? `<span class="pill neutral tag">Update${priceChange ? ` · was ${rupees(r.current_price)}` : ""}</span>`
              : `<span class="pill ok tag">New</span>`;
            const unsure = r.confidence < 0.75 || !r.price
              ? `<span class="pill warn tag">${r.price ? "Check" : "Add price"}</span>` : "";
            return `
            <tr data-i="${i}">
              <td><input type="checkbox" data-f="keep" ${r.keep ? "checked" : ""} aria-label="Include"></td>
              <td><input data-f="name" value="${esc(r.name)}"></td>
              <td><input data-f="aliases" value="${esc(r.aliases)}"></td>
              <td><select data-f="category">${CATEGORIES.map((c) => `<option ${c === r.category ? "selected" : ""}>${c}</option>`).join("")}</select></td>
              <td><input data-f="unit" value="${esc(r.unit)}"></td>
              <td><input class="n" data-f="price" type="number" min="0" step="any" value="${r.price}"></td>
              <td><input class="n" data-f="stock" type="number" min="0" step="any" value="${r.stock}"></td>
              <td>${tag} ${unsure}</td>
            </tr>`;
          }).join("")}
        </tbody>
      </table>
    </div>
    <div class="bill-actions">
      <span class="muted small">Prices are copied from the bill, not calculated. Check them, then save.</span>
      <div class="btn-group">
        <button class="ghost small-btn" id="billCancel">Discard</button>
        <button class="primary" id="billSave" ${kept ? "" : "disabled"}>Save ${kept} item${kept === 1 ? "" : "s"}</button>
      </div>
    </div>`;

  box.querySelectorAll("tr[data-i]").forEach((tr) => {
    const row = bill.rows[+tr.dataset.i];
    tr.querySelectorAll("[data-f]").forEach((el) => {
      el.addEventListener("change", () => {
        const f = el.dataset.f;
        row[f] = f === "keep" ? el.checked : ["price", "stock"].includes(f) ? parseFloat(el.value) || 0 : el.value;
        if (f === "keep") renderBillRows();
      });
    });
  });
  $("#billCancel").onclick = () => { bill.rows = []; box.hidden = true; };
  $("#billSave").onclick = async () => {
    try {
      const res = await api("/api/catalogue/import", { method: "POST", body: { items: bill.rows.filter((r) => r.keep) } });
      state.catalogue = res.catalogue;
      bill.rows = [];
      bill.photo = null;
      box.hidden = true;
      $("#billText").value = "";
      $("#billPreview").hidden = true;
      renderCatalogue();
      toast(`Saved ${res.imported} items to the catalogue`);
    } catch (err) {
      toast(err.message);
    }
  };
}

// ---------- boot ----------

(async function boot() {
  try {
    const h = await api("/api/health");
    state.health = h;
    $("#storeName").textContent = h.store_name;
    const notes = [];
    if (h.ai !== "claude") notes.push("Demo mode: no Claude API key set, so typed lists use a basic matcher and photos are off.");
    if (!h.storage.persistent) notes.push("Data is temporary on this deployment. Connect a Postgres database to keep it.");
    if (notes.length) {
      $("#banner").textContent = notes.join(" ");
      $("#banner").hidden = false;
    }
  } catch {
    $("#banner").textContent = "Can't reach the PADOSI server.";
    $("#banner").hidden = false;
  }
  loadCatalogue().catch(() => {});
})();
