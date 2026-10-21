import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { injectStyle, el, svg, timecode } from "./bruxos_grimorio.js";

// 🧙 Load Video Pro (Bruxos) -- Grimorio de ENTRADA.
// Mostra o arquivo fonte antes de rodar (proxy all-intra gerado no servidor),
// frame a frame. Na mesma tela: IN/OUT na regua, crop com box e trava de
// proporcao, girar/espelhar ao vivo (canvas), vista do resultado, grade de
// tercos, contagem 4n+1 (Wan) / 8n+1 (LTX), galeria do input e upload.

const NODE = "BruxosLoadVideoPro";
const STYLE_ID = "bruxos-load-pro-style";
const HANDLE = 9; // raio de captura das alcas (px css)
// grades dos modelos (iguais ao Python): validos sao n % passo == resto
const PAD_GRIDS = { "Wan 4n+1": [4, 1, "Wan"], "LTX 8n+1": [8, 1, "LTX"], "H3 17n+5": [17, 5, "H3"] };
const gridUp = (n, g) => (g ? Math.max(g[1], n + ((((g[1] - n) % g[0]) + g[0]) % g[0])) : n);
const MIN_BOX = 0.02;

const CSS = `
.bxl-screen{cursor:default;max-height:min(560px,70vh)}
.bxl-screen canvas{position:absolute;inset:0;width:100%;height:100%}
.bxl-screen video{display:none}
.bxl-prog{position:absolute;left:12%;right:12%;top:50%;margin-top:18px;height:4px;border-radius:2px;background:#ffffff14;overflow:hidden;display:none}
.bxl-prog i{display:block;height:100%;width:0;background:var(--amber);transition:width .2s}
.bxl-badge{position:absolute;top:10px;right:10px;font:700 9px/1 Inter,sans-serif;letter-spacing:1.3px;padding:4px 6px;border-radius:3px;pointer-events:none;display:none;z-index:2}
.bxl-badge.out{display:block;background:#ff6b8a33;color:#ffc2cf;border:1px solid #ff6b8a88}
.bxl-badge.res{display:block;background:#8b5cf633;color:#e4d9ff;border:1px solid #8b5cf6aa}
.bxg.drop .bxg-screen{outline:2px dashed var(--amber);outline-offset:-6px}
.bxl-ok{color:#86efac}.bxl-no{color:#8d84a0}
.bxl-snap{margin-left:6px;border:1px solid #f2b54466;background:#f2b54414;color:var(--amber2);border-radius:3px;font:700 9px/1 Inter,sans-serif;padding:3px 5px;cursor:pointer;letter-spacing:.6px}
.bxl-snap:hover{background:#f2b54430}
.bxl-pads{display:flex;flex-wrap:wrap;gap:4px;margin-top:5px}
.bxl-pad{height:20px;padding:0 7px;border:1px solid var(--line);background:none;color:var(--mute);border-radius:4px;cursor:pointer;
  font:700 9px/1 Inter,sans-serif;letter-spacing:.7px;text-transform:uppercase;display:inline-flex;align-items:center;gap:5px}
.bxl-pad:hover{color:var(--ink);border-color:#4a4258}
.bxl-pad b{font:700 10px/1 Consolas,monospace;color:#c9c2d8}
.bxl-pad.ok b{color:#86efac}
.bxl-pad.on{background:var(--amber);border-color:var(--amber);color:#1a1205}
.bxl-pad.on b{color:#1a1205}
.bxl-badge.frz{display:block;background:#7dd3fc22;color:#bae6fd;border:1px solid #7dd3fc88}
.bxg-cell.out .v{font-size:14px;padding-top:3px}
.bxg-cell.out .v small{margin-left:6px}
.bxl-sub{font:600 9px/1.2 Inter,sans-serif;letter-spacing:.6px;color:var(--mute);margin-top:2px}
.bxl-gal{position:fixed;inset:0;z-index:100000;background:#07050bcc;display:flex;align-items:center;justify-content:center;padding:24px}
.bxl-gal .pnl{width:min(1000px,96vw);max-height:88vh;display:flex;flex-direction:column;gap:10px;background:#120f18;border:1px solid #2e2838;border-radius:12px;padding:14px;box-shadow:0 20px 60px #000c;font-family:Inter,system-ui,sans-serif;color:#e9e4f2}
.bxl-gal .top{display:flex;gap:10px;align-items:center}
.bxl-gal .top b{font:700 11px/1 Inter,sans-serif;letter-spacing:1.6px;text-transform:uppercase;color:#f2b544;white-space:nowrap}
.bxl-gal input{flex:1;height:32px;background:#1a1622;border:1px solid #2e2838;border-radius:6px;color:#e9e4f2;padding:0 10px;outline:none}
.bxl-gal input:focus{border-color:#8b5cf6}
.bxl-gal .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:10px;overflow:auto;padding:2px}
.bxl-gal .card{background:#1a1622;border:1px solid #2e2838;border-radius:8px;overflow:hidden;cursor:pointer;display:flex;flex-direction:column}
.bxl-gal .card:hover{border-color:#f2b544}
.bxl-gal .card.on{border-color:#8b5cf6;box-shadow:0 0 0 1px #8b5cf6}
.bxl-gal .card .th{aspect-ratio:16/9;background:#0c0a10 center/contain no-repeat}
.bxl-gal .card .nm{font:500 11px/1.3 "JetBrains Mono",Consolas,monospace;padding:6px 8px;color:#c9c2d8;word-break:break-all}
`;

function injectLoadStyle() {
  injectStyle();
  if (document.getElementById(STYLE_ID)) return;
  const s = document.createElement("style");
  s.id = STYLE_ID;
  s.textContent = CSS;
  document.head.appendChild(s);
}

// ---- widgets -------------------------------------------------------------
const W = (node, n) => node.widgets?.find((w) => w.name === n);
const V = (node, n, d) => { const w = W(node, n); return w ? w.value : d; };
function setW(node, n, v) {
  const w = W(node, n);
  if (!w || w.value === v) return;
  w.value = v;
  try { w.callback?.(v); } catch (_) {}
  node.setDirtyCanvas?.(true, true);
  app.graph?.setDirtyCanvas?.(true, true);
}
function aspectRatio(node) { // proporcao em PIXELS (w/h) ou null = livre
  const a = String(V(node, "aspect", "livre"));
  const m = a.match(/^(\d+):(\d+)/);
  return m ? +m[1] / +m[2] : null;
}
const fitOf = (node) => String(V(node, "fit_mode", "off")).split(" ")[0];
const rotOf = (node) => { const g = String(V(node, "girar", "off")); return g.startsWith("90") ? 90 : g.startsWith("-90") ? -90 : g.startsWith("180") ? 180 : 0; };
function targetFromOne(w, h, tw, th) {
  if (tw > 0 && th > 0) return [tw, th];
  if (tw > 0) return [tw, Math.max(1, Math.round(h * tw / w))];
  if (th > 0) return [Math.max(1, Math.round(w * th / h)), th];
  return [w, h];
}

async function uploadVideo(file) {
  const body = new FormData();
  body.append("image", file);
  const r = await api.fetchApi("/upload/image", { method: "POST", body });
  if (r.status !== 200) {
    alert(`[Bruxos] upload falhou: ${r.status} ${r.statusText}` + (r.status === 413 ? " (limite de upload do ComfyUI -- use video_path)" : ""));
    return null;
  }
  const d = await r.json();
  return d.subfolder ? `${d.subfolder}/${d.name}` : d.name;
}

// --------------------------------------------------------------------------
// Player de entrada
// --------------------------------------------------------------------------
class LoadPlayer {
  constructor(node) {
    this.node = node;
    this.src = null;      // info do proxy do fonte
    this.frame = 0;
    this.loop = true;
    this.view = "edit";   // edit | result
    this.grid = false;
    this.token = 0;
    this.lastRun = null;

    this.root = el("div", "bxg");
    this.root.tabIndex = 0;

    // ---- tela (canvas desenha o frame ja girado/espelhado + o box) ----
    this.screen = el("div", "bxg-screen bxl-screen");
    this.video = el("video");
    this.video.muted = true;
    this.video.playsInline = true;
    this.video.preload = "auto";
    this.canvas = el("canvas");
    this.msg = el("div", "bxg-msg", '<div class="rune">✦</div><div>Escolha um vídeo — galeria, enviar ou arraste aqui.</div>');
    this.prog = el("div", "bxl-prog", "<i></i>");
    this.badge = el("div", "bxl-badge");
    this.flashEl = el("div", "bxg-flash");
    this.screen.append(this.video, this.canvas, ...["a", "b", "c", "d"].map((k) => el("div", `bxg-corner ${k}`)),
      this.badge, this.msg, this.prog, this.flashEl);

    // ---- slate ----
    const slate = el("div", "bxg-slate");
    const cells = el("div", "bxg-cells");
    const fCell = el("div", "bxg-cell frame", "<label>Frame</label>");
    const fRow = el("div", "v");
    this.frameIn = el("input");
    this.frameIn.type = "number";
    this.frameIn.title = "Frame do arquivo (0 = primeiro). Digite e Enter.";
    this.frameOf = el("small");
    fRow.append(this.frameIn, this.frameOf);
    fCell.appendChild(fRow);
    const tCell = el("div", "bxg-cell tc", "<label>Timecode</label>");
    this.tcEl = el("div", "v", "--:--:--:--");
    tCell.appendChild(this.tcEl);
    const oCell = el("div", "bxg-cell out", "<label>Saída</label>");
    this.outEl = el("div", "v", "-");
    this.outSub = el("div", "bxl-sub", "");
    oCell.append(this.outEl, this.outSub);
    cells.append(fCell, tCell, oCell);
    slate.append(el("div", "bxg-clap"), cells);

    // ---- regua ----
    this.ruler = el("div", "bxg-ruler");
    this.rc = el("canvas");
    this.ph = el("div", "bxg-ph");
    this.peek = el("div", "bxg-peek");
    this.peekImg = el("div", "img");
    this.peekN = el("div", "n");
    this.peek.append(this.peekImg, this.peekN);
    this.ruler.append(this.rc, this.ph, this.peek);

    // ---- deck ----
    const deck = el("div", "bxg-deck");
    const trans = el("div", "bxg-trans");
    const T = (icon, title, fn, cls) => {
      const b = el("button", cls || "", svg(icon));
      b.title = title;
      b.addEventListener("click", (e) => { e.stopPropagation(); fn(); this.focus(); });
      trans.appendChild(b);
      return b;
    };
    T("first", "Ir pro IN (Home) · Shift: frame 0", () => this.seek(this.trim().a));
    T("prev", "-1 frame (← ou ,) · Shift: -10", () => this.step(-1));
    this.playBtn = T("play", "Play / pause (Espaco ou K)", () => this.toggle(), "play");
    T("next", "+1 frame (→ ou .) · Shift: +10", () => this.step(1));
    T("last", "Ir pro OUT (End)", () => this.seek(this.trim().b));

    const tools = el("div", "bxg-tools");
    const C = (label, title, fn, icon) => {
      const b = el("button", "bxg-chip", (icon ? svg(icon) : "") + (label ? `<span>${label}</span>` : ""));
      b.title = title;
      b.addEventListener("click", (e) => { e.stopPropagation(); this._shift = e.shiftKey; fn(b); this.focus(); });
      tools.appendChild(b);
      return b;
    };
    this.inChip = C("In", "Marca o IN neste frame (I) -> skip_first_frames", () => this.setIn(this.frame), "in");
    this.outChip = C("Out", "Marca o OUT neste frame (O) -> frame_load_cap", () => this.setOut(this.frame), "out");
    C("", "Limpar IN/OUT (X)", () => this.clearTrim(), "x");
    this.cropChip = C("Crop", "Liga/desliga o crop (C). Arraste na imagem para desenhar o box; Shift solta a proporcao; duplo clique = box maximo", () => this.toggleCrop(), "crop");
    this.resChip = C("Resultado", "Ver so o que sai do node (R)", () => this.toggleView(), "eye");
    this.gridChip = C("", "Grade de tercos (G)", (b) => { this.grid = !this.grid; b.classList.toggle("on", this.grid); this.draw(); }, "grid");
    this.loopChip = C("Loop", "Repetir dentro do IN/OUT", (b) => { this.loop = !this.loop; b.classList.toggle("on", this.loop); });
    this.loopChip.classList.add("on");
    C("Galeria", "Escolher video da pasta input", () => this.openGallery(), "lib");
    C("Enviar", "Enviar um video (ou arraste o arquivo no player)", () => this.pickFile(), "up");
    deck.append(trans, tools);

    this.foot = el("div", "bxg-foot");
    this.root.append(this.screen, slate, this.ruler, deck, this.foot);
    this.bind();
    this.render();
  }

  focus() { this.root.focus({ preventScroll: true }); }
  count() { return this.src?.frames || 0; }
  fps() { return this.src?.fps || 24; }
  flash(t) {
    this.flashEl.textContent = t;
    this.flashEl.classList.add("on");
    clearTimeout(this._ft);
    this._ft = setTimeout(() => this.flashEl.classList.remove("on"), 650);
  }

  // ---- geometria ------------------------------------------------------------
  dispDims() { // dimensoes do fonte ja girado
    const w = this.src?.width || 16, h = this.src?.height || 9;
    return Math.abs(rotOf(this.node)) === 90 ? [h, w] : [w, h];
  }
  crop() {
    const n = this.node;
    return { x: +V(n, "crop_x", 0), y: +V(n, "crop_y", 0), w: +V(n, "crop_w", 1), h: +V(n, "crop_h", 1) };
  }
  setCrop(c) {
    const r = (v) => Math.round(v * 10000) / 10000;
    setW(this.node, "crop_x", r(c.x)); setW(this.node, "crop_y", r(c.y));
    setW(this.node, "crop_w", r(c.w)); setW(this.node, "crop_h", r(c.h));
  }
  ratioN() { // proporcao do box em coords normalizadas (w/h)
    const a = aspectRatio(this.node);
    if (!a) return null;
    const [dw, dh] = this.dispDims();
    return a * dh / dw;
  }
  outDims() {
    const n = this.node;
    let [w, h] = this.dispDims();
    let cw = +V(n, "custom_width", 0), ch = +V(n, "custom_height", 0);
    if (cw > 0 || ch > 0) {
      if (cw <= 0) cw = Math.max(1, Math.round(w * ch / h));
      if (ch <= 0) ch = Math.max(1, Math.round(h * cw / w));
      w = cw; h = ch;
    }
    const tw = +V(n, "target_width", 0), th = +V(n, "target_height", 0);
    const fit = fitOf(n);
    if (fit === "crop") {
      const c = this.crop();
      let x0 = Math.round(c.x * w), y0 = Math.round(c.y * h);
      let pw = Math.round(c.w * w), phh = Math.round(c.h * h);
      pw = Math.max(1, Math.min(w - x0, pw)); phh = Math.max(1, Math.min(h - y0, phh));
      [w, h] = (tw > 0 || th > 0) ? targetFromOne(pw, phh, tw, th) : [pw, phh];
    } else if (fit === "stretch" || fit === "pad") {
      if (tw > 0 || th > 0) [w, h] = targetFromOne(w, h, tw, th);
    }
    return [w, h];
  }
  // trecho em frames do ARQUIVO: a = IN, b = OUT (inclusive)
  trim() {
    const n = this.node, N = this.count();
    const a = Math.min(Math.max(0, +V(n, "skip_first_frames", 0)), Math.max(0, N - 1));
    const cap = +V(n, "frame_load_cap", 0);
    const nth = Math.max(1, +V(n, "select_every_nth", 1));
    const fr = +V(n, "force_rate", 0);
    let b = N - 1;
    if (cap > 0) {
      b = fr > 0 ? a + Math.ceil(cap * this.fps() / fr) - 1 : a + (cap - 1) * nth;
      b = Math.min(N - 1, b);
    }
    return { a, b, cap, nth, fr };
  }
  padGrid() { return PAD_GRIDS[String(V(this.node, "pad_grade", "off"))] || null; }
  outFrames() { return gridUp(this.baseFrames(), this.padGrid()); }
  padFrames() { const b = this.baseFrames(); return b ? gridUp(b, this.padGrid()) - b : 0; }
  setPad(name) {
    const cur = String(V(this.node, "pad_grade", "off"));
    const next = cur === name ? "off" : name;
    setW(this.node, "pad_grade", next);
    const p = this.padFrames();
    this.flash(next === "off" ? "PAD OFF" : p ? `${this.outFrames()} fr · +${p} freeze` : `${this.outFrames()} fr · já encaixa`);
    this.render(true);
  }
  baseFrames() {
    const N = this.count();
    if (!N) return 0;
    const { a, cap, nth, fr } = this.trim();
    const avail = fr > 0 ? Math.ceil((N - a) * fr / this.fps() - 1e-9) : Math.ceil((N - a) / nth);
    return cap > 0 ? Math.min(cap, avail) : avail;
  }
  capFor(a, b) {
    const { nth, fr } = this.trim();
    return fr > 0 ? Math.floor((b - a) * fr / this.fps() + 1e-9) + 1 : Math.floor((b - a) / nth) + 1;
  }

  // ---- IN / OUT --------------------------------------------------------------
  setIn(f) {
    if (!this.count()) return;
    const t = this.trim();
    const hadOut = t.cap > 0;
    setW(this.node, "skip_first_frames", f);
    if (hadOut) setW(this.node, "frame_load_cap", t.b >= f ? this.capFor(f, t.b) : 0);
    this.flash(`IN ${f}`);
    this.render(true);
  }
  setOut(f) {
    if (!this.count()) return;
    let { a } = this.trim();
    if (f < a) { setW(this.node, "skip_first_frames", f); a = f; }
    setW(this.node, "frame_load_cap", f >= this.count() - 1 && a === 0 ? 0 : this.capFor(a, f));
    this.flash(`OUT ${f}`);
    this.render(true);
  }
  clearTrim() {
    setW(this.node, "skip_first_frames", 0);
    setW(this.node, "frame_load_cap", 0);
    this.flash("TRECHO INTEIRO");
    this.render(true);
  }
  snap(mult) { // reduz o cap pro maior k*mult+1 <= saida atual
    const n = this.outFrames();
    const target = Math.floor((n - 1) / mult) * mult + 1;
    if (target < 1) return;
    setW(this.node, "frame_load_cap", target);
    this.flash(`${target} frames (${mult}n+1)`);
    this.render(true);
  }

  // ---- crop -------------------------------------------------------------------
  toggleCrop() {
    const on = fitOf(this.node) === "crop";
    setW(this.node, "fit_mode", on ? "off (original)" : "crop");
    if (!on) this.conformAspect();
    this.flash(on ? "CROP OFF" : "CROP");
    refreshVisibility(this.node);
    this.render(true);
  }
  conformAspect(maxBox = false) {
    const r = this.ratioN();
    let c = this.crop();
    if (maxBox) c = { x: 0, y: 0, w: 1, h: 1 };
    if (!r) { if (maxBox) this.setCrop(c); return; }
    const cx = c.x + c.w / 2, cy = c.y + c.h / 2;
    let w = c.w, h = w / r;
    if (h > c.h && !maxBox) { h = c.h; w = h * r; }
    if (h > 1) { h = 1; w = r; }
    if (w > 1) { w = 1; h = 1 / r; }
    let x = Math.min(Math.max(0, cx - w / 2), 1 - w), y = Math.min(Math.max(0, cy - h / 2), 1 - h);
    if (maxBox) { x = (1 - w) / 2; y = (1 - h) / 2; }
    this.setCrop({ x, y, w, h });
  }
  toggleView() {
    this.view = this.view === "edit" ? "result" : "edit";
    this.resChip.classList.toggle("on", this.view === "result");
    this.flash(this.view === "result" ? "RESULTADO" : "EDIÇÃO");
    this.layout();
  }

  // ---- fonte ------------------------------------------------------------------
  sourceQuery() {
    const vp = String(V(this.node, "video_path", "") || "").trim();
    if (vp) return { path: vp };
    const v = String(V(this.node, "video", "") || "");
    if (!v || v.startsWith("(")) return null;
    return { filename: v };
  }
  async loadSource() {
    const q = this.sourceQuery();
    const tok = ++this.token;
    this.video.pause();
    if (!q) { this.src = null; this.showMsg("✦", "Escolha um vídeo — galeria, enviar ou arraste aqui."); this.render(true); return; }
    const key = JSON.stringify(q);
    if (this.src && this._srcKey === key) return;
    this.src = null;
    this.render(true);
    this.showMsg("✦", "Lendo o vídeo…");
    for (;;) {
      let d;
      try {
        const r = await api.fetchApi(`/bruxos/pro/src?${new URLSearchParams(q)}`);
        d = await r.json();
      } catch (e) { d = { status: "error", error: String(e) }; }
      if (tok !== this.token) return;
      if (d.status === "ready") {
        this._srcKey = key;
        this.src = d;
        this.prog.style.display = "none";
        this.frame = this.trim().a;
        this.video.src = api.apiURL(`/bruxos/pro/src_file?key=${d.key}&kind=mp4`);
        this.stripUrl = api.apiURL(`/bruxos/pro/src_file?key=${d.key}&kind=strip`);
        this.video.load();
        this.layout();
        return;
      }
      if (d.status === "error") {
        this.prog.style.display = "none";
        this.showMsg("✧", `Não consegui ler o vídeo: ${d.error || "?"}`);
        return;
      }
      const pct = Math.min(100, (100 * (d.done || 0)) / Math.max(1, d.total || 1));
      this.showMsg("✦", `Preparando o preview frame a frame… ${d.done || 0}/${d.total || "?"}`);
      this.prog.style.display = "block";
      this.prog.firstChild.style.width = `${pct}%`;
      await new Promise((r) => setTimeout(r, 400));
      if (tok !== this.token) return;
    }
  }
  showMsg(rune, text) {
    this.msg.style.display = "flex";
    this.msg.innerHTML = `<div class="rune">${rune}</div><div>${text}</div>`;
  }

  // ---- eventos -----------------------------------------------------------------
  bind() {
    const v = this.video;
    const onFrame = (_n, meta) => {
      if (!v.paused && this.count()) {
        const mt = meta?.mediaTime ?? v.currentTime;
        const f = Math.min(this.count() - 1, Math.floor(mt * this.fps() + 1e-3));
        const t = this.trim();
        const pad = this.padFrames();
        if (this.loop && f > t.b && pad > 0 && !this._hold) {
          // mostra o freeze: para no OUT pelo tempo dos frames de pad, depois repete
          this._hold = true; v.pause(); this.seek(t.b);
          this._holdT = setTimeout(() => { this._hold = false; this.seek(t.a); v.play().catch(() => {}); this.render(); },
            (pad / this.fps()) * 1000);
        }
        else if (this.loop && !this._hold && (f > t.b || f < t.a)) { this.seek(t.a); v.play().catch(() => {}); }
        else if (f !== this.frame) { this.frame = Math.max(0, f); this.render(); }
        this.draw();
      }
      v.requestVideoFrameCallback?.(onFrame);
    };
    v.requestVideoFrameCallback?.(onFrame);
    if (!v.requestVideoFrameCallback) v.addEventListener("timeupdate", () => onFrame(0, null));
    v.addEventListener("seeked", () => this.draw());
    v.addEventListener("loadeddata", () => { this.msg.style.display = "none"; this.seek(this.frame); });
    v.addEventListener("ended", () => { if (this.loop) { this.seek(this.trim().a); v.play().catch(() => {}); } else this.render(); });
    v.addEventListener("play", () => this.render());
    v.addEventListener("pause", () => this.render());
    v.addEventListener("error", () => { if (this.src) this.showMsg("✧", "O preview do fonte sumiu (temp limpo?). Escolha o vídeo de novo."); });
    new ResizeObserver(() => { this.sizeCanvas(); this.drawRuler(); }).observe(this.screen);
    new ResizeObserver(() => this.drawRuler()).observe(this.ruler);

    // ---- tela: crop / play ----
    const cv = this.canvas;
    let drag = null;
    cv.addEventListener("pointermove", (e) => {
      if (drag) return this.dragCrop(drag, e);
      cv.style.cursor = this.cropEditable() ? ({ move: "move", n: "ns-resize", s: "ns-resize", e: "ew-resize", w: "ew-resize",
        nw: "nwse-resize", se: "nwse-resize", ne: "nesw-resize", sw: "nesw-resize", new: "crosshair" }[this.hit(e)] || "default") : "pointer";
    });
    cv.addEventListener("pointerdown", (e) => {
      if (!this.src) return;
      e.stopPropagation();
      this.focus();
      if (!this.cropEditable()) { drag = { play: true, x: e.clientX }; return; }
      const kind = this.hit(e);
      if (!kind) return;
      const p = this.toNorm(e);
      drag = { kind, p0: p, c0: this.crop(), moved: false };
      if (kind === "new") drag.c0 = { x: p.u, y: p.v, w: 0, h: 0 };
      cv.setPointerCapture(e.pointerId);
    });
    const end = (e) => {
      if (drag?.play) { if (Math.abs(e.clientX - drag.x) < 4) this.toggle(); }
      else if (drag?.moved) this.flash(this.outDims().join("×"));
      drag = null;
    };
    cv.addEventListener("pointerup", end);
    cv.addEventListener("pointercancel", () => { drag = null; });
    cv.addEventListener("dblclick", (e) => {
      e.stopPropagation();
      if (this.cropEditable()) { this.conformAspect(true); this.flash("BOX MÁXIMO"); this.render(true); }
    });

    // ---- regua: scrub + IN/OUT por arrasto com Alt ----
    const frameAt = (e) => {
      const r = this.ruler.getBoundingClientRect();
      return Math.min(this.count() - 1, Math.max(0, Math.floor(((e.clientX - r.left) / r.width) * this.count())));
    };
    let scrub = false;
    this.ruler.addEventListener("pointerdown", (e) => {
      if (!this.count()) return;
      e.stopPropagation(); e.preventDefault();
      scrub = true; this.ruler.setPointerCapture(e.pointerId);
      v.pause(); this.seek(frameAt(e));
    });
    this.ruler.addEventListener("pointermove", (e) => {
      if (!this.count()) return;
      const f = frameAt(e);
      this.peekAt(f, e);
      if (scrub) this.seek(f);
    });
    this.ruler.addEventListener("pointerup", () => { scrub = false; });
    this.ruler.addEventListener("pointercancel", () => { scrub = false; });
    this.ruler.addEventListener("wheel", (e) => {
      if (!this.count()) return;
      e.preventDefault(); e.stopPropagation();
      this.step(e.deltaY > 0 ? 1 : -1);
    }, { passive: false });

    this.frameIn.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Enter") { const n = parseInt(this.frameIn.value, 10); if (!isNaN(n)) this.seek(n); this.focus(); }
      else if (e.key === "Escape") this.focus();
    });

    // ---- teclado ----
    this.root.addEventListener("keydown", (e) => {
      if (e.target === this.frameIn || !this.src) return;
      const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      const n = e.shiftKey ? 10 : 1;
      const t = this.trim();
      const act = {
        ArrowRight: () => this.step(n), ".": () => this.step(n),
        ArrowLeft: () => this.step(-n), ",": () => this.step(-n),
        " ": () => this.toggle(), k: () => this.toggle(),
        l: () => v.play().catch(() => {}), j: () => { v.pause(); this.step(-n); },
        Home: () => this.seek(e.shiftKey ? 0 : t.a), End: () => this.seek(e.shiftKey ? this.count() - 1 : t.b),
        i: () => this.setIn(this.frame), o: () => this.setOut(this.frame), x: () => this.clearTrim(),
        c: () => this.toggleCrop(), r: () => this.toggleView(),
        g: () => { this.grid = !this.grid; this.gridChip.classList.toggle("on", this.grid); this.draw(); },
        f: () => (document.fullscreenElement ? document.exitFullscreen() : this.root.requestFullscreen?.().catch(() => {})),
      }[k];
      if (act) { act(); e.preventDefault(); e.stopPropagation(); }
    });

    // ---- arrastar arquivo no player ----
    this.root.addEventListener("dragover", (e) => {
      if ([...(e.dataTransfer?.items || [])].some((i) => i.kind === "file")) { e.preventDefault(); e.stopPropagation(); this.root.classList.add("drop"); }
    });
    this.root.addEventListener("dragleave", () => this.root.classList.remove("drop"));
    this.root.addEventListener("drop", (e) => {
      this.root.classList.remove("drop");
      const f = [...(e.dataTransfer?.files || [])].find((x) => x.type.startsWith("video/") || /\.(mp4|mov|mkv|webm|avi|m4v|gif)$/i.test(x.name));
      if (!f) return;
      e.preventDefault(); e.stopPropagation();
      this.doUpload(f);
    });
  }

  // ---- crop: hit test e arrasto ----------------------------------------------
  cropEditable() { return this.view === "edit" && fitOf(this.node) === "crop" && !!this.src; }
  toNorm(e) {
    const r = this.canvas.getBoundingClientRect();
    const im = this.imgRect;
    const x = (e.clientX - r.left), y = (e.clientY - r.top);
    return { u: Math.min(1, Math.max(0, (x - im.x) / im.w)), v: Math.min(1, Math.max(0, (y - im.y) / im.h)), x, y };
  }
  hit(e) {
    if (!this.imgRect) return null;
    const p = this.toNorm(e);
    const im = this.imgRect, c = this.crop();
    const X = (u) => im.x + u * im.w, Y = (v) => im.y + v * im.h;
    const L = X(c.x), R = X(c.x + c.w), Tp = Y(c.y), B = Y(c.y + c.h);
    const nx = Math.abs(p.x - L) <= HANDLE ? "w" : Math.abs(p.x - R) <= HANDLE ? "e" : "";
    const ny = Math.abs(p.y - Tp) <= HANDLE ? "n" : Math.abs(p.y - B) <= HANDLE ? "s" : "";
    const inX = p.x > L - HANDLE && p.x < R + HANDLE, inY = p.y > Tp - HANDLE && p.y < B + HANDLE;
    if (nx && ny) return ny + nx;
    if (nx && inY) return nx;
    if (ny && inX) return ny;
    if (p.x > L && p.x < R && p.y > Tp && p.y < B) return "move";
    if (p.x >= im.x && p.x <= im.x + im.w && p.y >= im.y && p.y <= im.y + im.h) return "new";
    return null;
  }
  dragCrop(d, e) {
    const p = this.toNorm(e);
    const du = p.u - d.p0.u, dv = p.v - d.p0.v;
    if (Math.abs(du) + Math.abs(dv) > 0.002) d.moved = true;
    if (!d.moved) return;
    const c0 = d.c0;
    const r = e.shiftKey ? null : this.ratioN();
    let c;
    if (d.kind === "move") {
      c = { ...c0, x: Math.min(Math.max(0, c0.x + du), 1 - c0.w), y: Math.min(Math.max(0, c0.y + dv), 1 - c0.h) };
    } else {
      // ancora = lado/canto oposto
      const k = d.kind === "new" ? (p.u >= c0.x ? "e" : "w") + "" + (p.v >= c0.y ? "s" : "n") : d.kind;
      const hasH = /[ew]/.test(k), hasV = /[ns]/.test(k);
      let ax = k.includes("w") ? c0.x + c0.w : c0.x, ay = k.includes("n") ? c0.y + c0.h : c0.y;
      if (d.kind === "new") { ax = c0.x; ay = c0.y; }
      let w = hasH ? Math.abs(p.u - ax) : c0.w, h = hasV ? Math.abs(p.v - ay) : c0.h;
      if (r) {
        if (hasH && hasV) { if (w / h > r) h = w / r; else w = h * r; }
        else if (hasH) h = w / r;
        else w = h * r;
      }
      w = Math.max(MIN_BOX, w); h = Math.max(MIN_BOX, h);
      const sx = k.includes("w") ? -1 : 1, sy = k.includes("n") ? -1 : 1;
      const cx0 = c0.x + c0.w / 2, cy0 = c0.y + c0.h / 2;
      // espaco disponivel a partir da ancora (ou do centro, no eixo que nao mexe)
      const availW = hasH ? (sx > 0 ? 1 - ax : ax) : 2 * Math.min(cx0, 1 - cx0);
      const availH = hasV ? (sy > 0 ? 1 - ay : ay) : 2 * Math.min(cy0, 1 - cy0);
      if (r) { const sc = Math.min(1, availW / w, availH / h); w *= sc; h *= sc; }
      else { w = Math.min(w, availW); h = Math.min(h, availH); }
      let x = hasH ? (sx > 0 ? ax : ax - w) : cx0 - w / 2;
      let y = hasV ? (sy > 0 ? ay : ay - h) : cy0 - h / 2;
      w = Math.min(1, w); h = Math.min(1, h);
      x = Math.min(Math.max(0, x), 1 - w); y = Math.min(Math.max(0, y), 1 - h);
      c = { x, y, w, h };
    }
    this.setCrop(c);
    this.render(true);
  }

  // ---- transporte ---------------------------------------------------------------
  seek(f) {
    const n = this.count();
    if (!n) return;
    f = Math.max(0, Math.min(n - 1, Math.round(f)));
    this.frame = f;
    const t = (f + 0.5) / this.fps();
    try { this.video.currentTime = Math.min(t, Math.max(0, (this.video.duration || t) - 1e-3)); } catch (_) {}
    this.render();
  }
  step(d) {
    this.video.pause();
    const t = this.trim();
    let f = this.frame + d;
    if (this.loop && this.frame >= t.a && this.frame <= t.b) {
      const len = t.b - t.a + 1;
      f = t.a + ((((f - t.a) % len) + len) % len);
    }
    this.seek(f);
  }
  toggle() {
    if (!this.count()) return;
    const v = this.video, t = this.trim();
    if (this._hold) { clearTimeout(this._holdT); this._hold = false; this.render(); return; }
    if (v.paused) {
      if (this.frame >= t.b || this.frame < t.a) this.seek(t.a);
      v.play().catch(() => {});
    } else { v.pause(); this.seek(this.frame); }
  }

  // ---- desenho ----------------------------------------------------------------
  layout() {
    let ratio;
    if (!this.src) ratio = 16 / 9;
    else if (this.view === "result") { const [w, h] = this.outDims(); ratio = w / h; }
    else { const [w, h] = this.dispDims(); ratio = w / h; }
    this.screen.style.aspectRatio = `${ratio}`;
    this.sizeCanvas();
    this.render(true);
  }
  sizeCanvas() {
    const dpr = window.devicePixelRatio || 1;
    const w = this.screen.clientWidth, h = this.screen.clientHeight;
    if (!w || !h) return;
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round(h * dpr);
    this.draw();
  }
  draw() {
    const cv = this.canvas, g = cv.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const CW = cv.width / dpr, CH = cv.height / dpr;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, CW, CH);
    if (!this.src || !this.video.videoWidth) { this.imgRect = null; return; }
    const [dw, dh] = this.dispDims();
    const fit = fitOf(this.node);
    const c = this.crop();
    // retangulo onde a imagem INTEIRA (girada) e desenhada
    let im, clipR = { x: 0, y: 0, w: CW, h: CH };
    const fitBox = (aw, ah) => { // retangulo de proporcao aw:ah centrado no canvas
      const s = Math.min(CW / aw, CH / ah);
      return { w: aw * s, h: ah * s, x: (CW - aw * s) / 2, y: (CH - ah * s) / 2 };
    };
    if (this.view === "result" && fit === "crop") {
      const F = fitBox(c.w * dw, c.h * dh);
      const W0 = F.w / c.w, H0 = F.h / c.h;
      im = { x: F.x - c.x * W0, y: F.y - c.y * H0, w: W0, h: H0 };
      clipR = F;
    } else if (this.view === "result" && fit === "pad" && (+V(this.node, "target_width", 0) || +V(this.node, "target_height", 0))) {
      const [ow, oh] = this.outDims();
      const O = fitBox(ow, oh);
      const s = Math.min(O.w / dw, O.h / dh);
      im = { w: dw * s, h: dh * s }; im.x = O.x + (O.w - im.w) / 2; im.y = O.y + (O.h - im.h) / 2;
      g.fillStyle = "#000"; g.fillRect(0, 0, CW, CH);
    } else if (this.view === "result") {
      const [ow, oh] = this.outDims();
      im = fitBox(ow, oh); // stretch / off
      g.fillStyle = "#000"; g.fillRect(0, 0, CW, CH);
    } else {
      const s = Math.min(CW / dw, CH / dh);
      im = { w: dw * s, h: dh * s }; im.x = (CW - im.w) / 2; im.y = (CH - im.h) / 2;
    }
    this.imgRect = im;
    const rot = rotOf(this.node);
    const fh = !!V(this.node, "flip_horizontal", false), fv = !!V(this.node, "flip_vertical", false);
    g.save();
    g.beginPath(); g.rect(clipR.x, clipR.y, clipR.w, clipR.h); g.clip();
    g.translate(im.x + im.w / 2, im.y + im.h / 2);
    g.scale(fh ? -1 : 1, fv ? -1 : 1);
    g.rotate((rot * Math.PI) / 180);
    const iw = Math.abs(rot) === 90 ? im.h : im.w, ih = Math.abs(rot) === 90 ? im.w : im.h;
    g.imageSmoothingQuality = "high";
    g.drawImage(this.video, -iw / 2, -ih / 2, iw, ih);
    g.restore();

    const thirds = (x, y, w, h) => {
      g.strokeStyle = "#ffffff40"; g.lineWidth = 1;
      g.beginPath();
      for (const k of [1, 2]) {
        g.moveTo(x + (w * k) / 3, y); g.lineTo(x + (w * k) / 3, y + h);
        g.moveTo(x, y + (h * k) / 3); g.lineTo(x + w, y + (h * k) / 3);
      }
      g.stroke();
    };
    if (this.view === "edit" && fit === "crop") {
      const L = im.x + c.x * im.w, T = im.y + c.y * im.h, Wd = c.w * im.w, Hd = c.h * im.h;
      g.fillStyle = "#07050bb0";
      g.beginPath(); g.rect(im.x, im.y, im.w, im.h); g.rect(L, T, Wd, Hd); g.fill("evenodd");
      if (this.grid) thirds(L, T, Wd, Hd);
      g.strokeStyle = "#f2b544"; g.lineWidth = 1.5; g.strokeRect(L + 0.5, T + 0.5, Wd - 1, Hd - 1);
      g.fillStyle = "#f2b544";
      const hs = 7;
      for (const [hx, hy] of [[L, T], [L + Wd, T], [L, T + Hd], [L + Wd, T + Hd]]) g.fillRect(hx - hs / 2, hy - hs / 2, hs, hs);
      for (const [hx, hy] of [[L + Wd / 2, T], [L + Wd / 2, T + Hd], [L, T + Hd / 2], [L + Wd, T + Hd / 2]]) g.fillRect(hx - 3, hy - 3, 6, 6);
      // etiqueta com o tamanho de saida
      const [ow, oh] = this.outDims();
      const txt = `${ow}×${oh}${aspectRatio(this.node) ? "  " + V(this.node, "aspect") : ""}`;
      g.font = "700 11px Consolas, monospace";
      const tw = g.measureText(txt).width + 10;
      const ly = T > 22 ? T - 20 : T + 4;
      g.fillStyle = "#f2b544"; g.fillRect(L, ly, tw, 16);
      g.fillStyle = "#1a1205"; g.fillText(txt, L + 5, ly + 12);
    } else if (this.grid) thirds(im.x, im.y, im.w, im.h);
  }
  drawRuler() {
    const c = this.rc, dpr = window.devicePixelRatio || 1;
    const w = this.ruler.clientWidth, h = this.ruler.clientHeight;
    if (!w || !h) return;
    c.width = Math.round(w * dpr); c.height = Math.round(h * dpr);
    const g = c.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    g.fillStyle = "#1a1622"; g.fillRect(0, 14, w, h - 14);
    const n = this.count();
    if (!n) return;
    const px = w / n, t = this.trim();
    // trecho que sai do node
    g.fillStyle = "#8b5cf62e"; g.fillRect(t.a * px, 14, (t.b - t.a + 1) * px, h - 14);
    g.fillStyle = "#07050b99";
    g.fillRect(0, 14, t.a * px, h - 14); g.fillRect((t.b + 1) * px, 14, w - (t.b + 1) * px, h - 14);
    const steps = [1, 2, 5, 10, 12, 24, 25, 48, 50, 100, 250, 500, 1000, 2500, 5000];
    const labelEvery = steps.find((s) => s * px >= 34) || 10000;
    const tickEvery = steps.find((s) => s * px >= 4) || 10000;
    g.font = "600 9px Consolas, monospace"; g.textBaseline = "top";
    for (let i = 0; i < n; i++) {
      const major = i % labelEvery === 0;
      if (!major && i % tickEvery) continue;
      const x = Math.round(i * px + px / 2);
      g.fillStyle = major ? "#8d84a0" : "#3d3549";
      const th = major ? 12 : 6;
      g.fillRect(x, h - th, 1, th);
      if (major) g.fillText(String(i), x + 3, 2);
    }
    // colchetes IN / OUT
    g.fillStyle = "#b89cff";
    const bx = (x, dir) => { g.fillRect(x, 14, 2, h - 14); g.fillRect(dir > 0 ? x : x - 5, 14, 7, 2); g.fillRect(dir > 0 ? x : x - 5, h - 2, 7, 2); };
    if (t.a > 0 || t.cap > 0) { bx(t.a * px, 1); bx((t.b + 1) * px - 2, -1); }
    const pad = this.padFrames();
    if (pad > 0) {
      const x = (t.b + 1) * px, txt = `❄+${pad}`;
      g.font = "700 9px Consolas, monospace";
      const tw = g.measureText(txt).width + 6;
      const bxp = Math.min(w - tw, x + 1);
      g.fillStyle = "#7dd3fc"; g.fillRect(bxp, 16, tw, 12);
      g.fillStyle = "#08131a"; g.fillText(txt, bxp + 3, 17);
    }
  }
  peekAt(f, e) {
    const s = this.src;
    if (s?.thumbs && this.stripUrl) {
      const k = s.thumbs, j = k > 1 ? Math.round((f / Math.max(1, this.count() - 1)) * (k - 1)) : 0;
      const th = 72, tw = Math.round(th * (s.width / s.height));
      Object.assign(this.peekImg.style, {
        display: "block", width: `${tw}px`, height: `${th}px`, backgroundImage: `url("${this.stripUrl}")`,
        backgroundSize: `${k * 100}% 100%`, backgroundPosition: `${k > 1 ? (j / (k - 1)) * 100 : 0}% 0`,
      });
    } else this.peekImg.style.display = "none";
    this.peekN.textContent = String(f);
    const r = this.ruler.getBoundingClientRect(), half = (this.peekImg.offsetWidth || 40) / 2;
    this.peek.style.left = `${Math.min(r.width - half, Math.max(half, e.clientX - r.left))}px`;
  }

  render(full = false) {
    const s = this.src, n = this.count();
    this.playBtn.innerHTML = svg(this.video.paused ? "play" : "pause");
    const fit = fitOf(this.node);
    this.cropChip.classList.toggle("on", fit === "crop");
    if (!s || !n) {
      this.frameIn.value = ""; this.frameOf.textContent = "";
      this.tcEl.textContent = "--:--:--:--"; this.outEl.textContent = "-"; this.outSub.textContent = "";
      this.foot.innerHTML = "sem vídeo";
      this.ph.style.display = "none";
      this.badge.className = "bxl-badge";
      this.drawRuler(); this.draw();
      return;
    }
    const t = this.trim();
    if (document.activeElement !== this.frameIn) this.frameIn.value = this.frame;
    this.frameOf.textContent = `/ ${n - 1}`;
    this.tcEl.textContent = timecode(this.frame, this.fps());
    const of = this.outFrames(), [ow, oh] = this.outDims();
    const ofps = t.fr > 0 ? t.fr : this.fps() / t.nth;
    const base = this.baseFrames(), pad = of - base;
    this.outEl.innerHTML = `${of}<small>fr${pad ? ` (${base} + ${pad} ❄)` : ""} · ${ow}×${oh} · ${(+ofps).toFixed(3).replace(/\.?0+$/, "")}fps</small>`;
    const cur = String(V(this.node, "pad_grade", "off"));
    const pads = Object.entries(PAD_GRIDS).map(([name, g]) => {
      const tgt = gridUp(base, g), ok = tgt === base;
      return `<button class="bxl-pad${cur === name ? " on" : ""}${ok ? " ok" : ""}" data-g="${name}" `
        + `title="${ok ? `${base} frames já encaixa na grade ${name}` : `Completa ${base} → ${tgt} frames congelando o último (+${tgt - base})`}">`
        + `pad ${name.replace(" ", " ")} <b>${ok ? "✓" : "→ " + tgt}</b></button>`;
    }).join("");
    this.outSub.innerHTML = `<div class="bxl-pads">${pads}</div>`;
    this.outSub.querySelectorAll(".bxl-pad").forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); this.setPad(b.dataset.g); this.focus(); }));

    const outside = this.frame < t.a || this.frame > t.b;
    this.badge.className = "bxl-badge" + (this._hold ? " frz" : this.view === "result" ? " res" : outside ? " out" : "");
    this.badge.textContent = this._hold ? `❄ FREEZE +${this.padFrames()}` : this.view === "result" ? "RESULTADO" : outside ? "FORA DO TRECHO" : "";
    if (this._hold) this.playBtn.innerHTML = svg("pause");
    this.ph.style.display = "";
    this.ph.style.left = `${((this.frame + 0.5) / n) * 100}%`;
    const key = `${n}|${t.a}|${t.b}|${t.cap}|${this.padFrames()}`;
    if (full || this._rk !== key) { this._rk = key; this.drawRuler(); }
    if (full) this.draw();

    const name = (V(this.node, "video_path", "") || V(this.node, "video", "")).replace(/\\/g, "/").split("/").pop();
    const tags = [
      `<span class="bxg-tag">${s.width}×${s.height}</span>`,
      `<span class="bxg-tag">${(+s.fps).toFixed(3).replace(/\.?0+$/, "")} fps</span>`,
      `<span class="bxg-tag">${n} frames · ${(n / this.fps()).toFixed(2)}s</span>`,
      `<span class="bxg-tag cs">${s.codec} ${s.bits}-bit</span>`,
      s.has_audio ? `<span class="bxg-tag">♪ áudio</span>` : "",
      `<span class="bxg-tag">${s.size_mb} MB</span>`,
      (t.a > 0 || t.cap > 0) ? `<span class="bxg-tag gr">IN ${t.a} → OUT ${t.b}</span>` : "",
      rotOf(this.node) ? `<span class="bxg-tag gr">girar ${rotOf(this.node)}°</span>` : "",
      V(this.node, "reverse", false) ? `<span class="bxg-tag gr">reverso</span>` : "",
    ].join("");
    const run = this.lastRun ? `<div style="margin-top:3px;color:#86efac">última carga: ${this.lastRun.frames} fr · ${this.lastRun.width}×${this.lastRun.height} · ${this.lastRun.seconds}s</div>` : "";
    this.foot.innerHTML = `<div class="bxg-tags">${tags}</div>${name}${run}`;
  }

  // ---- galeria / upload ---------------------------------------------------------
  async doUpload(file) {
    this.showMsg("✦", `Enviando ${file.name}…`);
    const name = await uploadVideo(file);
    if (!name) { this.loadSource(); return; }
    const w = W(this.node, "video");
    if (w?.options?.values && !w.options.values.includes(name)) w.options.values.push(name);
    setW(this.node, "video_path", "");
    setW(this.node, "video", name);
    this.loadSource();
  }
  pickFile() {
    const inp = el("input");
    inp.type = "file";
    inp.accept = "video/*,.mkv,.mov";
    inp.onchange = () => inp.files?.[0] && this.doUpload(inp.files[0]);
    inp.click();
  }
  async openGallery() {
    document.querySelector(".bxl-gal")?.remove();
    let list = W(this.node, "video")?.options?.values || [];
    try { list = await (await api.fetchApi("/bruxos/pro/videos")).json(); } catch (_) {}
    const w = W(this.node, "video");
    if (w?.options) w.options.values = list.length ? list : w.options.values;
    const ov = el("div", "bxl-gal");
    const pnl = el("div", "pnl");
    const top = el("div", "top", "<b>✦ Grimório · input</b>");
    const q = el("input"); q.placeholder = "Filtrar…";
    const close = el("button", "bxg-chip", "<span>Fechar</span>");
    top.append(q, close);
    const grid = el("div", "grid");
    pnl.append(top, grid);
    ov.appendChild(pnl);
    const cur = V(this.node, "video", "");
    const cards = list.map((name) => {
      const card = el("div", "card" + (name === cur ? " on" : ""));
      const th = el("div", "th");
      th.style.backgroundImage = `url("${api.apiURL(`/bruxos/video_thumbnail?filename=${encodeURIComponent(name)}`)}")`;
      card.append(th, el("div", "nm", name));
      card.onclick = () => { setW(this.node, "video_path", ""); setW(this.node, "video", name); ov.remove(); this.loadSource(); };
      grid.appendChild(card);
      return [name.toLowerCase(), card];
    });
    if (!list.length) grid.appendChild(el("div", "nm", "Nenhum vídeo em ComfyUI/input. Use ENVIAR."));
    q.oninput = () => { const s = q.value.toLowerCase(); cards.forEach(([n, c]) => { c.style.display = n.includes(s) ? "" : "none"; }); };
    const dismiss = () => ov.remove();
    close.onclick = dismiss;
    ov.addEventListener("pointerdown", (e) => { e.stopPropagation(); if (e.target === ov) dismiss(); });
    ov.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Escape") dismiss(); });
    document.body.appendChild(ov);
    q.focus();
  }
}

// --------------------------------------------------------------------------
// Widgets: esconde o que o player edita e o que nao vale pro modo atual
// --------------------------------------------------------------------------
function setVisible(node, name, visible) {
  const w = W(node, name);
  if (!w || !!w.hidden === !visible) return;
  w.hidden = !visible;
  if (w.options) w.options.hidden = !visible;
  if (visible) { if (w._bxlCS) { w.computeSize = w._bxlCS; delete w._bxlCS; } else delete w.computeSize; }
  else { if (w.computeSize && !w._bxlCS) w._bxlCS = w.computeSize; w.computeSize = () => [0, -4]; }
}
function refreshVisibility(node) {
  for (const n of ["crop_x", "crop_y", "crop_w", "crop_h"]) setVisible(node, n, false);
  const fit = fitOf(node);
  setVisible(node, "aspect", fit === "crop");
  setVisible(node, "target_width", fit !== "off");
  setVisible(node, "target_height", fit !== "off");
  node.setSize?.([node.size[0], node.computeSize()[1]]);
  node.setDirtyCanvas?.(true, true);
}
function hook(node, name, fn) {
  const w = W(node, name);
  if (!w) return;
  const orig = w.callback;
  w.callback = function () {
    const r = orig?.apply(this, arguments);
    try { fn(w.value); } catch (e) { console.warn("[Bruxos Load Pro]", e); }
    return r;
  };
}

app.registerExtension({
  name: "BruxosDoVFX.LoadVideoPro",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== NODE) return;
    injectLoadStyle();

    const onCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onCreated?.apply(this, arguments);
      const node = this;
      const p = new LoadPlayer(node);
      node._bxlPlayer = p;
      let measured = 0;
      const heightFor = () => (measured > 0 ? measured : 460) + 4;
      node.addDOMWidget("bruxos_load_pro_player", "BRUXOS_LOAD_PRO", p.root, {
        serialize: false, hideOnZoom: false, getValue: () => "", setValue: () => {},
        getMinHeight: heightFor, getMaxHeight: heightFor, getHeight: heightFor,
      });
      new ResizeObserver(() => {
        if (document.fullscreenElement) return;
        const h = p.root.offsetHeight;
        if (h > 1 && Math.abs(h - measured) > 1) {
          measured = h;
          if (!window.LiteGraph?.vueNodesMode) node.setSize([node.size[0], node.computeSize()[1]]);
          node.setDirtyCanvas?.(true, true);
        }
      }).observe(p.root);
      if (node.size[0] < 480) node.setSize([480, node.size[1]]);

      hook(node, "video", () => { setW(node, "skip_first_frames", 0); setW(node, "frame_load_cap", 0); p.loadSource(); });
      hook(node, "video_path", () => p.loadSource());
      hook(node, "fit_mode", () => { if (fitOf(node) === "crop") p.conformAspect(); refreshVisibility(node); p.layout(); });
      hook(node, "aspect", () => { p.conformAspect(); p.render(true); });
      for (const n of ["girar", "flip_horizontal", "flip_vertical", "custom_width", "custom_height", "target_width", "target_height"])
        hook(node, n, () => p.layout());
      for (const n of ["skip_first_frames", "frame_load_cap", "select_every_nth", "force_rate", "reverse", "pad_grade"])
        hook(node, n, () => p.render(true));

      // arrastar arquivo em cima do node (canvas classico)
      node.onDragOver = (e) => [...(e.dataTransfer?.items || [])].some((i) => i.kind === "file");
      node.onDragDrop = (e) => {
        const f = [...(e.dataTransfer?.files || [])].find((x) => x.type.startsWith("video/"));
        if (!f) return false;
        p.doUpload(f);
        return true;
      };
      requestAnimationFrame(() => { refreshVisibility(node); p.loadSource(); });
      return r;
    };

    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const r = onConfigure?.apply(this, arguments);
      requestAnimationFrame(() => { refreshVisibility(this); this._bxlPlayer?.loadSource(); });
      return r;
    };

    const onExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (msg) {
      const r = onExecuted?.apply(this, arguments);
      const info = msg?.bruxos_load_pro?.[0];
      if (info && this._bxlPlayer) { this._bxlPlayer.lastRun = info; this._bxlPlayer.render(); }
      return r;
    };
  },
});
