// PADOSI landing: live neighbourhood grid, scroll-printed bill, speed meter, trust checks.
// Scroll is read inside one rAF loop (no scroll listener), like the rest of the motion here.
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const clamp = (v, a = 0, b = 1) => Math.max(a, Math.min(b, v));
  const ease = (t) => 1 - Math.pow(1 - t, 3);
  const smooth = (t) => t * t * (3 - 2 * t);
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const mobile = () => innerWidth <= 900;

  /* ---------- background: the neighbourhood as a circuit board ----------
     Dots are homes, glowing squares are kiranas, and each travelling packet is
     a WhatsApp order finding its way to the nearest store along the grid. */
  const canvas = $("#grid"), ctx = canvas.getContext("2d");
  const GAP = 30;
  let W = 0, H = 0, cols = 0, rows = 0, stores = [], packets = [], mouse = { x: -999, y: -999 }, lastSpawn = 0;
  let seed = 11;
  const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;

  function resize() {
    const dpr = Math.min(devicePixelRatio || 1, 2);
    W = innerWidth; H = innerHeight;
    canvas.width = W * dpr; canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    cols = Math.ceil(W / GAP) + 1; rows = Math.ceil(H / GAP) + 2;
    seed = 11; stores = [];
    const n = Math.max(5, Math.round((cols * rows) / 90));
    for (let i = 0; i < n; i++) stores.push({ c: 1 + Math.floor(rnd() * (cols - 2)), r: 1 + Math.floor(rnd() * (rows - 2)), glow: 0 });
    packets = [];
  }

  function spawn() {
    const c = Math.floor(Math.random() * cols), r = Math.floor(Math.random() * rows);
    let best = stores[0], bd = Infinity;
    stores.forEach((s) => { const d = Math.abs(s.c - c) + Math.abs(s.r - r); if (d < bd && d > 2) { bd = d; best = s; } });
    // Manhattan route: along the row, then down the column, like a trace on a board.
    packets.push({ path: [[c, r], [best.c, r], [best.c, best.r]], t: 0, len: Math.abs(best.c - c) + Math.abs(best.r - r), store: best });
  }

  function pointAt(p, dist) {
    const [a, b, c] = p.path, l1 = Math.abs(b[0] - a[0]);
    if (dist <= l1) return [a[0] + Math.sign(b[0] - a[0]) * dist, a[1]];
    return [b[0], b[1] + Math.sign(c[1] - b[1]) * (dist - l1)];
  }

  function draw(now, scrollShift) {
    ctx.clearRect(0, 0, W, H);
    const oy = -(scrollShift % GAP);
    // dots, brighter near the pointer
    for (let r = 0; r < rows; r++) {
      const y = r * GAP + oy;
      for (let c = 0; c < cols; c++) {
        const x = c * GAP, d = Math.hypot(x - mouse.x, y - mouse.y);
        const near = d < 160 ? 1 - d / 160 : 0;
        ctx.fillStyle = `rgba(150, 220, 190, ${0.09 + near * 0.45})`;
        ctx.fillRect(x - 1, y - 1, 2 + near * 1.5, 2 + near * 1.5);
      }
    }
    // stores
    stores.forEach((s) => {
      const x = s.c * GAP, y = s.r * GAP + oy;
      s.glow *= 0.94;
      ctx.fillStyle = `rgba(244, 185, 66, ${0.35 + s.glow * 0.65})`;
      ctx.shadowColor = "rgba(244, 185, 66, .9)"; ctx.shadowBlur = 6 + s.glow * 18;
      ctx.fillRect(x - 4, y - 4, 8, 8);
      ctx.shadowBlur = 0;
    });
    // order packets with fading trails
    packets.forEach((p) => {
      p.t += 0.09;
      const head = Math.min(p.t, p.len), tail = Math.max(0, head - 4);
      const steps = 14;
      for (let i = 0; i < steps; i++) {
        const k = i / (steps - 1), [gc, gr] = pointAt(p, tail + (head - tail) * k);
        ctx.fillStyle = `rgba(79, 220, 154, ${k * 0.8})`;
        ctx.fillRect(gc * GAP - 1.5, gr * GAP + oy - 1.5, 3, 3);
      }
      const [hc, hr] = pointAt(p, head);
      ctx.fillStyle = "#bff5d9"; ctx.shadowColor = "rgba(79, 220, 154, 1)"; ctx.shadowBlur = 12;
      ctx.fillRect(hc * GAP - 2.5, hr * GAP + oy - 2.5, 5, 5);
      ctx.shadowBlur = 0;
      if (p.t >= p.len && !p.done) { p.done = true; p.store.glow = 1; }
    });
    packets = packets.filter((p) => p.t < p.len + 5);
    if (!reduced && now - lastSpawn > (mobile() ? 520 : 300) && packets.length < 28) { spawn(); lastSpawn = now; }
  }

  resize();
  addEventListener("resize", resize);
  addEventListener("pointermove", (e) => { mouse.x = e.clientX; mouse.y = e.clientY; });
  addEventListener("pointerleave", () => { mouse.x = mouse.y = -999; });

  /* ---------- entrance reveals ---------- */
  const rv = $$(".hero .eyebrow, .hero .mega, .hero .lede, .hero .cta-row, .facts, .meter, .checks, .tiles .tile, .final .cta-row, .why .eyebrow, .checksec .eyebrow, .checksec .lede");
  rv.forEach((el, i) => { el.classList.add("rv"); if (el.classList.contains("tile")) el.style.transitionDelay = `${(i % 4) * 80}ms`; });
  const io = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } }), { threshold: 0.15 });
  rv.forEach((el) => io.observe(el));

  /* ---------- hero numbers count up once ---------- */
  const counter = new IntersectionObserver((es) => es.forEach((e) => {
    if (!e.isIntersecting) return;
    counter.unobserve(e.target);
    const el = e.target, n = +el.dataset.count;
    if (reduced || !n) { el.textContent = n; return; }
    const s0 = performance.now();
    const step = (now) => { const k = clamp((now - s0) / 1400); el.textContent = Math.round(n * ease(k)); if (k < 1) requestAnimationFrame(step); };
    requestAnimationFrame(step);
  }), { threshold: 0.6 });
  $$("[data-count]").forEach((el) => counter.observe(el));

  /* ---------- trust checks tick in sequence ---------- */
  let checked = false;
  new IntersectionObserver(([e]) => {
    if (!e.isIntersecting || checked) return;
    checked = true;
    const lis = $$("#checks li");
    lis.forEach((li, i) => setTimeout(() => li.classList.add("on"), reduced ? 0 : 400 + i * 600));
    setTimeout(() => $("#verdict").classList.add("on"), reduced ? 0 : 400 + lis.length * 600);
  }, { threshold: 0.5 }).observe($("#checks"));

  /* ---------- pointer spotlight ---------- */
  $$(".spotlit").forEach((el) => el.addEventListener("pointermove", (e) => {
    const r = el.getBoundingClientRect();
    el.style.setProperty("--mx", `${e.clientX - r.left}px`);
    el.style.setProperty("--my", `${e.clientY - r.top}px`);
  }));

  /* ---------- headline words light up as they scroll in ---------- */
  const scrubs = $$(".printsec .big, .pathsec .big, .checksec .big, .why .big, .final .mega").map((h) => {
    const words = [];
    const wrap = (node) => [...node.childNodes].forEach((c) => {
      if (c.nodeType !== 3) return wrap(c);
      const f = document.createDocumentFragment();
      c.textContent.split(/(\s+)/).forEach((t) => {
        if (!t.trim()) return f.append(t);
        const s = document.createElement("span"); s.className = "sw"; s.textContent = t; words.push(s); f.append(s);
      });
      c.replaceWith(f);
    });
    wrap(h);
    return { h, words };
  });

  /* ---------- ribbons drift both ways, faster while scrolling ---------- */
  const ribbons = $$(".rrow").map((r) => {
    const t = r.firstElementChild;
    [...t.children].forEach((c) => { const k = c.cloneNode(true); k.setAttribute("aria-hidden", "true"); t.append(k); });
    return { t, dir: +r.dataset.dir, x: 0, w: 0 };
  });
  const measure = () => ribbons.forEach((b) => { b.w = b.t.scrollWidth / 2; });
  measure(); addEventListener("resize", measure);
  if (document.fonts) document.fonts.ready.then(measure);

  /* ---------- footer wordmark rises letter by letter ---------- */
  const fw = $(".foot-word");
  fw.innerHTML = [...fw.textContent].map((ch, i) => `<span style="--i:${i}">${ch}</span>`).join("");
  new IntersectionObserver(([e], o) => { if (e.isIntersecting) { fw.classList.add("in"); o.disconnect(); } }, { threshold: 0.3 }).observe(fw);

  /* ---------- main loop ---------- */
  const progressBar = $(".progress i");
  const spy = $$('.nav nav a[href^="#"]').map((a) => ({ a, s: $(a.getAttribute("href")) }));
  const prs = $(".printsec"), printer = $("#printer"), receipt = $("#receipt"), chat = $(".chat"), flow = $$(".flow li");
  const ps = $(".pathsec"), hops = $$("#hops li");
  const TICKS = 30;
  let lastTick = 0, heat = 0, lastY = scrollY, sv = 0;
  const fmtClock = (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
  const sectionProgress = (el, vh) => clamp(-el.getBoundingClientRect().top / (el.offsetHeight - vh));

  function loop(now) {
    const vh = innerHeight, doc = document.documentElement;
    const prog = clamp(scrollY / Math.max(1, doc.scrollHeight - vh));
    progressBar.style.setProperty("--p", prog.toFixed(4));

    if (!reduced || !loop.drawn) { draw(now, scrollY * 0.25); loop.drawn = true; }

    let cur = null;
    spy.forEach(({ a, s }) => { if (s.getBoundingClientRect().top < vh * 0.45) cur = a; });
    spy.forEach(({ a }) => a.classList.toggle("on", a === cur));

    scrubs.forEach(({ h, words }) => {
      const r = h.getBoundingClientRect();
      if (r.bottom < -40 || r.top > vh) return;
      const p = reduced ? 1 : clamp((vh * 0.9 - r.top) / (vh * 0.45));
      words.forEach((s, i) => { s.style.opacity = (0.16 + 0.84 * clamp(p * words.length * 1.15 - i)).toFixed(2); });
    });

    sv += (Math.abs(scrollY - lastY) - sv) * 0.12; lastY = scrollY;
    ribbons.forEach((b) => {
      if (!b.w) return;
      b.x += b.dir * (reduced ? 0 : 0.4 + sv * 0.5);
      b.t.style.transform = `translate3d(${-(((b.x % b.w) + b.w) % b.w)}px,0,0)`;
    });

    // How it works: scrolling scans the WhatsApp list, then feeds the bill out of the
    // printer in line-feed steps, with a heat flash on every step, then stamps it.
    const pp = sectionProgress(prs, vh);
    const scanP = clamp(pp / 0.3), raw = reduced ? 1 : clamp((pp - 0.25) / 0.6);
    chat.style.setProperty("--scan", (scanP < 1 ? (Math.sin(scanP * Math.PI * 3) + 1) / 2 : 1).toFixed(3));
    chat.style.setProperty("--scan-on", pp > 0.02 && scanP < 1 ? 1 : 0);
    const sc = raw * TICKS, tick = Math.floor(sc), f = sc - tick;
    const q = raw >= 1 ? 1 : (tick + smooth(f)) / TICKS;
    if (tick !== lastTick) { heat = 1; lastTick = tick; } else heat *= 0.88;
    receipt.style.setProperty("--q", q.toFixed(4));
    printer.style.setProperty("--heat", raw > 0 && raw < 1 ? heat.toFixed(3) : 0);
    printer.classList.toggle("done", raw >= 1);
    flow.forEach((li) => li.classList.toggle("on", pp >= +li.dataset.at || reduced));

    // Speed: the clock counts down from 6:00 by hand to about 1:00 with PADOSI.
    const sp = mobile() ? clamp(1 - ps.getBoundingClientRect().top / vh * 0.6) : sectionProgress(ps, vh);
    const t = reduced ? 1 : clamp((sp - 0.06) / 0.8), k = smooth(t);
    const secs = 360 - 300 * k;
    $("#clock").textContent = fmtClock(secs);
    $("#bar").style.transform = `scaleX(${secs / 360})`;
    $("#saved").textContent = fmtClock(360 - secs);
    $("#save").classList.toggle("on", k > 0.97);
    const lit = Math.floor(t * (hops.length + 0.999));
    hops.forEach((li, i) => li.classList.toggle("on", i < lit));

    requestAnimationFrame(loop);
  }
  requestAnimationFrame(loop);
})();
