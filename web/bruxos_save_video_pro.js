import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { injectStyle, el, svg, viewUrl, timecode } from "./bruxos_grimorio.js";

// 🧙 Save Video Pro (Bruxos) -- "Grimorio": flipbook de revisao frame a frame.
// Visual proprio dos Bruxos: tela com cantos de safe-frame, claquete (slate)
// com frame/timecode/seed embaixo da imagem, regua de frames (1 tick por frame)
// com miniatura no hover, marcadores de frame (M) e zoom 1:1 pra ver o grao.
// O preview e um proxy H.264 ALL-INTRA: o numero no slate e o frame na tela.

const NODE = "BruxosSaveVideoPro";
const STYLE_ID = "bruxos-grimorio-style";

// --------------------------------------------------------------------------
// Grimorio: o player
// --------------------------------------------------------------------------
class ProPlayer {
  constructor(node) {
    this.node = node;
    this.info = null;
    this.frame = 0;
    this.loop = true;
    this.speed = 1;
    this.marks = new Set();
    this.pan = { x: 50, y: 50 };

    this.root = el("div", "bxg");
    this.root.tabIndex = 0;

    // tela
    this.screen = el("div", "bxg-screen");
    this.video = el("video");
    this.video.muted = true;
    this.video.playsInline = true;
    this.video.preload = "auto";
    this.msg = el("div", "bxg-msg", '<div class="rune">✦</div><div>Rode o workflow — o render aparece aqui.</div>');
    this.flashEl = el("div", "bxg-flash");
    this.video.className = "render";
    this.vb = el("video", "bruto"); // plate bruto (comparativo)
    this.vb.muted = true;
    this.vb.playsInline = true;
    this.vb.preload = "auto";
    this.layers = el("div", "bxg-layers");
    this.layers.append(this.video, this.vb);
    this.divEl = el("div", "bxg-div");
    this.grip = el("div", "bxg-grip", "⇔");
    this.divEl.appendChild(this.grip);
    this.labB = el("div", "bxg-lab b", "BRUTO");
    this.labR = el("div", "bxg-lab r", "RENDER");
    this.screen.append(this.layers, ...["a", "b", "c", "d"].map((k) => el("div", `bxg-corner ${k}`)),
      this.divEl, this.labB, this.labR, el("div", "bxg-zoomtag", "1:1 PIXEL"), this.msg, this.flashEl);
    this.cmp = Object.assign({ mode: "off", split: 0.5, swap: false, gain: 1 }, node.properties?.bruxos_pro_cmp || {});
    this.holdB = false;

    // slate
    this.slate = el("div", "bxg-slate");
    const cells = el("div", "bxg-cells");
    const fCell = el("div", "bxg-cell frame", "<label>Frame</label>");
    const fRow = el("div", "v");
    this.frameIn = el("input");
    this.frameIn.type = "number";
    this.frameIn.title = "Digite um frame e Enter";
    this.frameOf = el("small", "", "");
    fRow.append(this.frameIn, this.frameOf);
    fCell.appendChild(fRow);
    const tCell = el("div", "bxg-cell tc", "<label>Timecode</label>");
    this.tcEl = el("div", "v", "--:--:--:--");
    tCell.appendChild(this.tcEl);
    const rCell = el("div", "bxg-cell tc", "<label>Rate</label>");
    this.rateEl = el("div", "v", "-");
    rCell.appendChild(this.rateEl);
    cells.append(fCell, tCell, rCell);
    this.seedBtn = el("button", "bxg-seed", "<label>Seed · copiar</label>");
    this.seedV = el("div", "v", "—");
    this.seedBtn.appendChild(this.seedV);
    this.slate.append(el("div", "bxg-clap"), cells, this.seedBtn);

    // regua
    this.ruler = el("div", "bxg-ruler");
    this.canvas = el("canvas");
    this.ph = el("div", "bxg-ph");
    this.peek = el("div", "bxg-peek");
    this.peekImg = el("div", "img");
    this.peekN = el("div", "n");
    this.peek.append(this.peekImg, this.peekN);
    this.ruler.append(this.canvas, this.ph, this.peek);

    // deck
    const deck = el("div", "bxg-deck");
    const trans = el("div", "bxg-trans");
    const T = (icon, title, fn, cls) => {
      const b = el("button", cls || "", svg(icon));
      b.title = title;
      b.addEventListener("click", (e) => { e.stopPropagation(); fn(); this.root.focus({ preventScroll: true }); });
      trans.appendChild(b);
      return b;
    };
    T("first", "Inicio (Home)", () => this.seek(0));
    T("prev", "-1 frame (← ou ,)  ·  Shift: -10", () => this.step(-1));
    this.playBtn = T("play", "Play / pause (Espaco ou K)", () => this.toggle(), "play");
    T("next", "+1 frame (→ ou .)  ·  Shift: +10", () => this.step(1));
    T("last", "Fim (End)", () => this.seek(this.count() - 1));

    const tools = el("div", "bxg-tools");
    const C = (label, title, fn, icon) => {
      const b = el("button", "bxg-chip", (icon ? svg(icon) : "") + `<span>${label}</span>`);
      b.title = title;
      b.addEventListener("click", (e) => { e.stopPropagation(); fn(b); this.root.focus({ preventScroll: true }); });
      tools.appendChild(b);
      return b;
    };
    this.loopChip = C("Loop", "Repetir", (b) => { this.loop = !this.loop; b.classList.toggle("on", this.loop); });
    this.loopChip.classList.add("on");
    this.speedChip = C("1×", "Velocidade (clique p/ trocar)", (b) => {
      const opts = [1, 0.5, 0.25, 2];
      this.speed = opts[(opts.indexOf(this.speed) + 1) % opts.length];
      this.video.playbackRate = this.speed;
      b.querySelector("span").textContent = `${this.speed}×`;
      b.classList.toggle("on", this.speed !== 1);
    });
    this.zoomChip = C("1:1", "Zoom pixel 1:1 do proxy (Z) -- arraste para mover", () => this.toggleZoom());
    this.audioChip = C("Som", "Ligar/desligar audio", (b) => { this.video.muted = !this.video.muted; b.classList.toggle("on", !this.video.muted); });
    this.markChip = C("Marcar", "Marcar/desmarcar este frame (M). Shift+clique: copiar lista",
      () => (this._shiftClick ? this.copyMarks() : this.toggleMark()), "flag");
    this.markChip.addEventListener("click", (e) => { this._shiftClick = e.shiftKey; }, true);
    C("", "Salvar este frame em PNG", () => this.snapshot(), "png");
    C("", "Tela cheia (F)", () => this.fullscreen(), "full");
    C("", "Abrir a pasta no Explorer", () => this.openFolder(), "folder");
    this.pinChip = C("Fixar", "Fixar ESTE render como referência (P): rode de novo e compare a versão nova com esta. Clique de novo para soltar.",
      () => (this._shiftClick || this.pinnedIsCurrent() ? this.unpin() : this.pin()), "flag");
    this.pinChip.addEventListener("click", (e) => { this._shiftClick = e.shiftKey; }, true);
    deck.append(trans, tools);

    // comparar com o bruto (so aparece quando a entrada 'bruto' esta ligada)
    this.cmpRow = el("div", "bxg-cmp", "<label>Comparar ×</label>");
    // fonte da comparacao: plate bruto ligado no node OU um render anterior fixado
    this.srcSeg = el("div", "bxg-seg");
    this.srcBtns = {};
    [["bruto", "Bruto"], ["pin", "Fixado"]].forEach(([k, label]) => {
      const b = el("button", "", label);
      b.addEventListener("click", (e) => { e.stopPropagation(); this.cmp.src = k; this.setRef(); this.applyCmp(); this.root.focus({ preventScroll: true }); });
      this.srcBtns[k] = b;
      this.srcSeg.appendChild(b);
    });
    this.cmpRow.appendChild(this.srcSeg);
    const seg = el("div", "bxg-seg");
    this.cmpBtns = {};
    [["off", "Off"], ["wipe", "Cortina"], ["side", "Lado a lado"], ["diff", "Diferença"], ["flip", "Alternar"]].forEach(([k, label]) => {
      const b = el("button", "", label);
      b.title = { off: "So o render", wipe: "Cortina: arraste a alca (ou a imagem) para mover", side: "Bruto | render lado a lado",
        diff: "Diferenca absoluta -- preto = igual", flip: "Clique na imagem para alternar bruto/render" }[k] + "  ·  C cicla os modos";
      b.addEventListener("click", (e) => { e.stopPropagation(); this.setCmp(k); this.root.focus({ preventScroll: true }); });
      this.cmpBtns[k] = b;
      seg.appendChild(b);
    });
    const CC = (label, title, fn) => {
      const b = el("button", "bxg-chip", `<span>${label}</span>`);
      b.title = title;
      b.addEventListener("click", (e) => { e.stopPropagation(); fn(b); this.root.focus({ preventScroll: true }); });
      return b;
    };
    this.swapChip = CC("⇄ Inverter", "Troca os lados (cortina / lado a lado)", () => { this.cmp.swap = !this.cmp.swap; this.applyCmp(); });
    this.gainChip = CC("Ganho ×1", "Amplifica a diferenca (so no modo Diferenca)", () => {
      const g = [1, 4, 10, 25];
      this.cmp.gain = g[(g.indexOf(this.cmp.gain) + 1) % g.length];
      this.applyCmp();
    });
    this.modeSeg = seg;
    this.cmpTip = el("span", "", '<span style="font:10px Consolas,monospace;color:#8d84a0">segure B = referência inteira · P fixa</span>');
    this.cmpHint = el("span", "", "");
    this.cmpHint.style.cssText = "font:11px Inter,sans-serif;color:#c9c2d8";
    this.cmpRow.append(seg, this.swapChip, this.gainChip, this.cmpTip, this.cmpHint);

    // rodape
    this.foot = el("div", "bxg-foot");

    this.root.append(this.screen, this.slate, this.ruler, this.cmpRow, deck, this.foot);
    this.bindEvents();
    this.render();
  }

  count() { return this.info?.frames || 0; }
  fps() { return this.info?.fps || 24; }
  sf() { return this.info?.start_frame || 0; }

  flash(text) {
    this.flashEl.textContent = text;
    this.flashEl.classList.add("on");
    clearTimeout(this._flashT);
    this._flashT = setTimeout(() => this.flashEl.classList.remove("on"), 600);
  }

  bindEvents() {
    const v = this.video;
    const onFrame = (_now, meta) => {
      if (!v.paused) {
        const mt = meta?.mediaTime ?? v.currentTime;
        const f = Math.min(this.count() - 1, Math.floor(mt * this.fps() + 1e-3));
        if (f !== this.frame) { this.frame = Math.max(0, f); this.render(); }
        // bruto anda junto: corrige se escorregar mais de meio frame
        if (this.hasBruto() && Math.abs(this.vb.currentTime - mt) > 0.5 / this.fps()) {
          try { this.vb.currentTime = mt; } catch (_) {}
        }
      }
      if (v.requestVideoFrameCallback) v.requestVideoFrameCallback(onFrame);
    };
    if (v.requestVideoFrameCallback) v.requestVideoFrameCallback(onFrame);
    else v.addEventListener("timeupdate", () => onFrame(0, null));

    v.addEventListener("ended", () => {
      if (this.loop) { this.seek(0); v.play().catch(() => {}); }
      else { this.frame = this.count() - 1; this.render(); }
    });
    v.addEventListener("play", () => {
      if (this.hasBruto()) { this.vb.playbackRate = v.playbackRate; try { this.vb.currentTime = v.currentTime; } catch (_) {} this.vb.play().catch(() => {}); }
      this.render();
    });
    v.addEventListener("pause", () => { if (this.hasBruto()) this.vb.pause(); this.render(); });
    v.addEventListener("ratechange", () => { this.vb.playbackRate = v.playbackRate; });
    this.vb.addEventListener("error", () => { if (this.refNow) this.flash("referência indisponível (temp limpo?)"); });
    v.addEventListener("loadedmetadata", () => { this.msg.style.display = "none"; this.seek(this.frame); });
    v.addEventListener("error", () => {
      this.msg.style.display = "flex";
      this.msg.innerHTML = '<div class="rune">✧</div><div>Preview sumiu (a pasta temp e limpa ao reiniciar o ComfyUI). Rode de novo.</div>';
    });

    // tela: clique = play/pause; no 1:1, arrastar move a imagem
    let panDrag = null, moved = false, wipeDrag = null;
    const splitAt = (e) => {
      const r = this.screen.getBoundingClientRect();
      return Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    };
    this.grip.addEventListener("pointerdown", (e) => {
      e.stopPropagation(); e.preventDefault();
      wipeDrag = { grip: true };
      this.grip.setPointerCapture(e.pointerId);
    });
    this.grip.addEventListener("pointermove", (e) => { if (wipeDrag) { this.cmp.split = splitAt(e); this.applyCmp(false); } });
    const endGrip = () => { if (wipeDrag) { wipeDrag = null; this.saveCmp(); } };
    this.grip.addEventListener("pointerup", endGrip);
    this.grip.addEventListener("click", (e) => e.stopPropagation());
    this.grip.addEventListener("pointercancel", endGrip);
    this.screen.addEventListener("pointerdown", (e) => {
      moved = false;
      if (this.cmp.mode === "wipe" && this.hasBruto() && !this.screen.classList.contains("z11")) {
        wipeDrag = { x: e.clientX };
        this.screen.setPointerCapture(e.pointerId);
        e.stopPropagation();
        return;
      }
      if (!this.screen.classList.contains("z11")) return;
      panDrag = { x: e.clientX, y: e.clientY, px: this.pan.x, py: this.pan.y };
      this.screen.classList.add("drag");
      this.screen.setPointerCapture(e.pointerId);
      e.stopPropagation();
    });
    this.screen.addEventListener("pointermove", (e) => {
      if (wipeDrag && !wipeDrag.grip) {
        if (Math.abs(e.clientX - wipeDrag.x) > 3) moved = true;
        if (moved) { this.cmp.split = splitAt(e); this.applyCmp(false); }
        return;
      }
      if (!panDrag) return;
      const dx = e.clientX - panDrag.x, dy = e.clientY - panDrag.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
      const r = this.screen.getBoundingClientRect();
      this.pan.x = Math.min(100, Math.max(0, panDrag.px - (dx / r.width) * 100));
      this.pan.y = Math.min(100, Math.max(0, panDrag.py - (dy / r.height) * 100));
      this.applyPan();
    });
    const endPan = () => {
      if (wipeDrag && !wipeDrag.grip) { wipeDrag = null; if (moved) this.saveCmp(); }
      panDrag = null; this.screen.classList.remove("drag");
    };
    this.screen.addEventListener("pointerup", endPan);
    this.screen.addEventListener("pointercancel", endPan);
    this.screen.addEventListener("click", () => {
      if (moved) return;
      if (this.cmp.mode === "flip" && this.hasBruto()) { this.flipB = !this.flipB; this.applyCmp(false); this.flash(this.flipB ? this.refNow.label : "RENDER"); }
      else this.toggle();
    });
    this.screen.addEventListener("dblclick", (e) => { e.preventDefault(); this.fullscreen(); });

    // regua: arrastar = scrub, hover = miniatura, roda = passo
    const frameAt = (e) => {
      const r = this.ruler.getBoundingClientRect();
      const t = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
      return Math.min(this.count() - 1, Math.floor(t * this.count()));
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
      this.updatePeek(f, e);
      if (scrub) this.seek(f);
    });
    const endScrub = () => { scrub = false; };
    this.ruler.addEventListener("pointerup", endScrub);
    this.ruler.addEventListener("pointercancel", endScrub);
    this.ruler.addEventListener("wheel", (e) => {
      if (!this.count()) return;
      e.preventDefault(); e.stopPropagation();
      this.step(e.deltaY > 0 ? 1 : -1);
    }, { passive: false });
    new ResizeObserver(() => this.drawRuler()).observe(this.ruler);

    this.seedBtn.addEventListener("click", (e) => { e.stopPropagation(); this.copySeed(); });

    this.frameIn.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Enter") {
        const n = parseInt(this.frameIn.value, 10);
        if (!isNaN(n)) this.seek(n - this.sf());
        this.root.focus({ preventScroll: true });
      } else if (e.key === "Escape") this.root.focus({ preventScroll: true });
    });

    this.root.addEventListener("keydown", (e) => {
      if (!this.count() || e.target === this.frameIn) return;
      const k = e.key;
      const n = e.shiftKey ? 10 : 1;
      let used = true;
      if (k === "ArrowRight" || k === ".") this.step(n);
      else if (k === "ArrowLeft" || k === ",") this.step(-n);
      else if (k === " " || k === "k" || k === "K") this.toggle();
      else if (k === "Home") this.seek(0);
      else if (k === "End") this.seek(this.count() - 1);
      else if (k === "l" || k === "L") { v.playbackRate = this.speed; v.play().catch(() => {}); }
      else if (k === "j" || k === "J") { v.pause(); this.step(-n); }
      else if (k === "m" || k === "M") this.toggleMark();
      else if (k === "]") this.jumpMark(1);
      else if (k === "[") this.jumpMark(-1);
      else if (k === "z" || k === "Z") this.toggleZoom();
      else if ((k === "c" || k === "C") && this.hasBruto()) {
        const order = ["off", "wipe", "side", "diff", "flip"];
        this.setCmp(order[(order.indexOf(this.cmp.mode) + 1) % order.length]);
      }
      else if (k === "p" || k === "P") { if (e.shiftKey || this.pinnedIsCurrent()) this.unpin(); else this.pin(); }
      else if ((k === "b" || k === "B") && this.hasBruto()) { if (!this.holdB) { this.holdB = true; this.applyCmp(false); } }
      else if (k === "f" || k === "F") this.fullscreen();
      else used = false;
      if (used) { e.preventDefault(); e.stopPropagation(); }
    });
    this.root.addEventListener("keyup", (e) => {
      if ((e.key === "b" || e.key === "B") && this.holdB) { this.holdB = false; this.applyCmp(false); }
    });
    this.root.addEventListener("blur", () => { if (this.holdB) { this.holdB = false; this.applyCmp(false); } });
  }

  // ---- comparar -----------------------------------------------------------
  hasBruto() { return !!this.refNow; }

  // ---- referencia: bruto ligado OU render fixado ---------------------------
  pinned() { return this.node.properties?.bruxos_pro_pin || globalThis.__bruxosProPin || null; }
  pinnedIsCurrent() { const p = this.pinned(); return !!p && p.preview?.filename === this.info?.preview?.filename; }
  refOptions() {
    const i = this.info, o = {};
    if (i?.bruto) o.bruto = { ref: i.bruto, label: "BRUTO", key: "b:" + i.bruto.filename };
    const p = this.pinned();
    if (p?.preview && p.preview.filename !== i?.preview?.filename) {
      const v = p.version ? `v${String(p.version).padStart(3, "0")}` : "FIXADO";
      o.pin = { ref: p.preview, label: v + (p.seed != null ? ` · s${p.seed}` : ""), key: "p:" + p.preview.filename, info: p };
    }
    return o;
  }
  setRef() {
    const o = this.refOptions();
    const want = this.cmp.src === "pin" ? (o.pin || o.bruto) : (o.bruto || o.pin);
    const prev = this.refNow?.key;
    this.refNow = want || null;
    if (!want) { if (prev) { this.vb.removeAttribute("src"); this.vb.load(); } return; }
    if (want.key !== prev) {
      this.vb.src = viewUrl(want.ref) + `&t=${Date.now()}`;
      this.vb.load();
      this.vb.addEventListener("loadeddata", () => this.seek(this.frame), { once: true });
    }
  }
  pin() {
    const i = this.info;
    if (!i?.preview) return;
    const p = { preview: i.preview, version: i.version, seed: i.seed, frames: i.frames, fps: i.fps,
                width: i.width, height: i.height, path: i.path, t: Date.now() };
    this.node.properties = this.node.properties || {};
    this.node.properties.bruxos_pro_pin = p;
    globalThis.__bruxosProPin = p; // outros Save Video Pro podem comparar com ele tambem
    this.cmp.src = "pin";
    this.flash(`REFERÊNCIA ← v${String(i.version || 0).padStart(3, "0")}`);
    this.setRef(); this.applyCmp();
  }
  unpin() {
    if (this.node.properties) delete this.node.properties.bruxos_pro_pin;
    if (globalThis.__bruxosProPin && globalThis.__bruxosProPin.preview?.filename === this.pinned()?.preview?.filename) delete globalThis.__bruxosProPin;
    globalThis.__bruxosProPin = undefined;
    this.cmp.src = "bruto";
    this.flash("REFERÊNCIA SOLTA");
    this.setRef(); this.applyCmp();
  }

  setCmp(mode) {
    this.cmp.mode = mode;
    this.flipB = false;
    this.applyCmp();
    const names = { off: "SO RENDER", wipe: "CORTINA", side: "LADO A LADO", diff: "DIFERENÇA", flip: "ALTERNAR" };
    this.flash(names[mode]);
  }

  saveCmp() {
    this.node.properties = this.node.properties || {};
    this.node.properties.bruxos_pro_cmp = { mode: this.cmp.mode, split: this.cmp.split, swap: this.cmp.swap, gain: this.cmp.gain, src: this.cmp.src };
  }

  applyPan() {
    const pos = this.screen.classList.contains("z11") ? `${this.pan.x}% ${this.pan.y}%` : "";
    this.video.style.objectPosition = pos;
    this.vb.style.objectPosition = pos;
  }

  applyCmp(save = true) {
    const has = this.hasBruto();
    const c = this.cmp;
    const mode = has ? c.mode : "off";
    // a linha aparece sempre que ha render: sem referencia, explica como conseguir uma
    this.cmpRow.classList.toggle("has", !!this.info);
    for (const x of [this.modeSeg, this.cmpTip]) x.style.display = has ? "" : "none";
    this.cmpHint.style.display = has ? "none" : "";
    if (!has) {
      const p = this.pinned();
      this.cmpHint.innerHTML = this.pinnedIsCurrent()
        ? `<b style="color:#ffcf70">Este render está fixado.</b> Rode de novo: a versão nova abre comparando com ele.`
        : p ? `Referência fixada v${String(p.version || 0).padStart(3, "0")} indisponível neste render.`
        : `Sem referência: ligue um vídeo na entrada <b>bruto</b>, ou clique <b>FIXAR</b> e rode de novo.`;
    }
    const opts = this.refOptions();
    this.srcSeg.style.display = opts.bruto && opts.pin ? "" : "none";
    this.srcBtns.bruto.classList.toggle("on", this.refNow?.label === "BRUTO");
    this.srcBtns.pin.classList.toggle("on", !!this.refNow && this.refNow.label !== "BRUTO");
    if (opts.pin) this.srcBtns.pin.textContent = `Fixado ${opts.pin.label}`;
    const pinnedHere = this.pinned()?.preview?.filename === this.info?.preview?.filename;
    this.pinChip.classList.toggle("on", !!this.pinned());
    this.pinChip.querySelector("span").textContent = pinnedHere ? "Fixado ✓" : this.pinned() ? "Fixar (troca)" : "Fixar";
    for (const [k, b] of Object.entries(this.cmpBtns)) b.classList.toggle("on", k === c.mode);
    this.swapChip.style.display = mode === "wipe" || mode === "side" ? "" : "none";
    this.gainChip.style.display = mode === "diff" ? "" : "none";
    this.gainChip.querySelector("span").textContent = `Ganho ×${c.gain}`;
    this.swapChip.classList.toggle("on", c.swap);

    const sc = this.screen;
    sc.dataset.cmp = mode === "flip" ? "off" : mode;
    sc.classList.toggle("showb", has && (this.holdB || (mode === "flip" && this.flipB)));
    const v = this.video, vb = this.vb;
    // reset
    for (const x of [v, vb]) { x.style.left = ""; x.style.width = ""; x.style.clipPath = ""; }
    this.layers.style.filter = "";
    const ratio = this.info?.width && this.info?.height ? this.info.width / this.info.height : 16 / 9;
    sc.style.aspectRatio = mode === "side" && !sc.classList.contains("showb") ? `${ratio * 2}` : `${ratio}`;

    if (mode === "wipe") {
      const pct = (c.split * 100).toFixed(2);
      // bruto de um lado, render do outro
      vb.style.clipPath = c.swap ? `inset(0 0 0 ${pct}%)` : `inset(0 ${100 - pct}% 0 0)`;
      this.divEl.style.left = `${pct}%`;
      this.labB.style.left = c.swap ? "auto" : "10px"; this.labB.style.right = c.swap ? "10px" : "auto";
      this.labR.style.left = c.swap ? "10px" : "auto"; this.labR.style.right = c.swap ? "auto" : "10px";
    } else if (mode === "side") {
      const bl = c.swap ? "50%" : "0", rl = c.swap ? "0" : "50%";
      vb.style.left = bl; v.style.left = rl;
      this.labB.style.left = c.swap ? "calc(50% + 10px)" : "10px"; this.labB.style.right = "auto";
      this.labR.style.left = c.swap ? "10px" : "calc(50% + 10px)"; this.labR.style.right = "auto";
    } else if (mode === "diff") {
      this.labB.style.left = "50%"; this.labB.style.right = "auto";
    }
    const showb = sc.classList.contains("showb");
    if (mode === "diff" && !showb) {
      if (c.gain > 1) this.layers.style.filter = `brightness(${c.gain})`;
      this.labB.textContent = `|RENDER − ${this.refNow.label}|${c.gain > 1 ? " ×" + c.gain : ""}`;
    } else this.labB.textContent = this.refNow?.label || "BRUTO";
    if (save) this.saveCmp();
    this.render();
  }

  // ---- regua ------------------------------------------------------------
  drawRuler() {
    const c = this.canvas;
    const dpr = window.devicePixelRatio || 1;
    const w = this.ruler.clientWidth, h = this.ruler.clientHeight;
    if (!w || !h) return;
    c.width = Math.round(w * dpr); c.height = Math.round(h * dpr);
    const g = c.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    const n = this.count();
    // trilho
    g.fillStyle = "#1a1622";
    g.fillRect(0, 14, w, h - 14);
    if (!n) return;
    const px = w / n;
    const sf = this.sf();
    // passo dos numeros: so onde cabem
    const steps = [1, 2, 5, 10, 12, 24, 25, 48, 50, 100, 250, 500, 1000];
    const labelEvery = steps.find((s) => s * px >= 34) || 1000;
    const tickEvery = steps.find((s) => s * px >= 4) || 1;
    g.font = "600 9px Consolas, monospace";
    g.textBaseline = "top";
    for (let i = 0; i < n; i++) {
      const fnum = sf + i;
      const x = i * px;
      const major = fnum % labelEvery === 0;
      if (!major && i % tickEvery !== 0) continue;
      g.fillStyle = major ? "#8d84a0" : "#3d3549";
      const th = major ? 12 : 6;
      g.fillRect(Math.round(x + px / 2), h - th, 1, th);
      if (major) {
        g.fillStyle = "#8d84a0";
        g.fillText(String(fnum), Math.round(x + px / 2) + 3, 2);
      }
    }
    // marcadores
    g.fillStyle = "#ff6b8a";
    for (const m of this.marks) {
      const x = (m + 0.5) * px;
      g.beginPath(); g.moveTo(x, 15); g.lineTo(x + 4, 20); g.lineTo(x, 25); g.lineTo(x - 4, 20); g.closePath(); g.fill();
    }
  }

  updatePeek(f, e) {
    const i = this.info;
    if (!i?.filmstrip || !i.thumbs) { this.peekImg.style.display = "none"; }
    else {
      const k = i.thumbs;
      const j = k > 1 ? Math.round((f / Math.max(1, this.count() - 1)) * (k - 1)) : 0;
      const th = 72, tw = Math.round(th * (i.width / i.height));
      Object.assign(this.peekImg.style, {
        display: "block", width: `${tw}px`, height: `${th}px`,
        backgroundImage: `url("${viewUrl(i.filmstrip)}")`,
        backgroundSize: `${k * 100}% 100%`,
        backgroundPosition: `${k > 1 ? (j / (k - 1)) * 100 : 0}% 0`,
      });
    }
    this.peekN.textContent = String(f + this.sf());
    const r = this.ruler.getBoundingClientRect();
    const half = (this.peekImg.offsetWidth || 40) / 2;
    this.peek.style.left = `${Math.min(r.width - half, Math.max(half, e.clientX - r.left))}px`;
  }

  // ---- acoes -------------------------------------------------------------
  load(info) {
    this.info = info;
    this.frame = 0;
    this.marks = new Set(this.node.properties?.bruxos_pro_marks?.[info?.path] || []);
    this.msg.style.display = "flex";
    this.msg.innerHTML = '<div class="rune">✦</div><div>Invocando o preview…</div>';
    const t = Date.now();
    this.video.src = viewUrl(info.preview) + `&t=${t}`;
    this.video.load();
    this.refNow = null;
    this.setRef();
    // render novo com uma versao fixada esperando: ja abre comparando (cortina)
    if (this.refNow && this.refNow.label !== "BRUTO" && this.cmp.mode === "off") this.cmp.mode = "wipe";
    this.flipB = false;
    this.applyCmp(false);
  }

  seek(f) {
    const n = this.count();
    if (!n) return;
    f = Math.max(0, Math.min(n - 1, Math.round(f)));
    this.frame = f;
    const t = (f + 0.5) / this.fps(); // centro do frame -> cai exato no proxy all-intra
    try { this.video.currentTime = Math.min(t, Math.max(0, (this.video.duration || t) - 1e-3)); } catch (_) {}
    if (this.hasBruto()) {
      try { this.vb.currentTime = Math.min(t, Math.max(0, (this.vb.duration || t) - 1e-3)); } catch (_) {}
    }
    this.render();
  }

  step(d) {
    this.video.pause();
    const n = this.count();
    let f = this.frame + d;
    if (this.loop) f = ((f % n) + n) % n;
    this.seek(f);
  }

  toggle() {
    if (!this.count()) return;
    if (this.video.paused) {
      if (this.frame >= this.count() - 1) this.seek(0);
      this.video.playbackRate = this.speed;
      this.video.play().catch(() => {});
    } else {
      this.video.pause();
      this.seek(this.frame);
    }
  }

  toggleZoom() {
    const on = this.screen.classList.toggle("z11");
    this.zoomChip.classList.toggle("on", on);
    this.applyPan();
    this.flash(on ? "1:1" : "FIT");
  }

  saveMarks() {
    const p = this.info?.path;
    if (!p) return;
    this.node.properties = this.node.properties || {};
    const all = this.node.properties.bruxos_pro_marks || {};
    all[p] = [...this.marks].sort((a, b) => a - b);
    this.node.properties.bruxos_pro_marks = all;
  }

  toggleMark() {
    if (!this.count()) return;
    const f = this.frame;
    if (this.marks.has(f)) { this.marks.delete(f); this.flash(`− ${f + this.sf()}`); }
    else { this.marks.add(f); this.flash(`⚑ ${f + this.sf()}`); }
    this.saveMarks();
    this.render();
  }

  jumpMark(dir) {
    const list = [...this.marks].sort((a, b) => a - b);
    if (!list.length) return;
    this.video.pause();
    const target = dir > 0 ? list.find((m) => m > this.frame) ?? list[0]
                           : [...list].reverse().find((m) => m < this.frame) ?? list[list.length - 1];
    this.seek(target);
  }

  async copyMarks() {
    const list = [...this.marks].sort((a, b) => a - b).map((m) => m + this.sf());
    if (!list.length) return;
    try { await navigator.clipboard.writeText(list.join(", ")); } catch (_) {}
    this.flash("frames copiados");
  }

  fullscreen() {
    if (document.fullscreenElement) document.exitFullscreen();
    else this.root.requestFullscreen?.().catch(() => {});
  }

  snapshot() {
    const v = this.video;
    if (!v.videoWidth) return;
    const c = document.createElement("canvas");
    c.width = v.videoWidth; c.height = v.videoHeight;
    c.getContext("2d").drawImage(v, 0, 0);
    const base = (this.info?.path || "frame").split(/[\\/]/).pop().replace(/\.#+\.\w+$|\.\w+$/, "");
    const a = el("a");
    a.download = `${base}.${this.frame + this.sf()}.png`;
    a.href = c.toDataURL("image/png");
    a.click();
    this.flash("PNG salvo");
  }

  async copySeed() {
    const s = this.info?.seed;
    if (s == null) return;
    try { await navigator.clipboard.writeText(String(s)); } catch (_) {}
    this.seedBtn.classList.add("ok");
    this.seedV.textContent = "copiado ✓";
    setTimeout(() => { this.seedBtn.classList.remove("ok"); this.render(); }, 900);
  }

  async openFolder() {
    const p = this.info?.path;
    if (!p) return;
    try {
      await api.fetchApi("/bruxos/pro/open_folder", { method: "POST", body: JSON.stringify({ path: p }) });
    } catch (_) {}
  }

  render() {
    const i = this.info;
    const n = this.count();
    const sf = this.sf();
    this.playBtn.innerHTML = svg(this.video.paused ? "play" : "pause");
    if (!i || !n) {
      this.frameIn.value = "";
      this.frameOf.textContent = "";
      this.tcEl.textContent = "--:--:--:--";
      this.rateEl.textContent = "-";
      this.seedV.textContent = "—";
      this.foot.innerHTML = "sem render ainda";
      this.ph.style.display = "none";
      this.drawRuler();
      return;
    }
    if (document.activeElement !== this.frameIn) this.frameIn.value = sf + this.frame;
    this.frameOf.textContent = `${this.frame + 1}/${n}`;
    this.tcEl.textContent = timecode(this.frame, this.fps());
    this.rateEl.innerHTML = `${(+this.fps()).toFixed(3).replace(/\.?0+$/, "")}<small>fps</small>`;

    if (i.seed != null) {
      this.seedBtn.style.display = "";
      if (!this.seedBtn.classList.contains("ok")) this.seedV.textContent = String(i.seed);
      const extra = (i.seeds || []).slice(1).map((s) => `${s.class}#${s.node}: ${s.seed}`).join("\n");
      this.seedBtn.title = "Clique para copiar" + (extra ? `\nOutros samplers:\n${extra}` : "");
    } else this.seedBtn.style.display = "none";

    this.ph.style.display = "";
    this.ph.style.left = `${((this.frame + 0.5) / n) * 100}%`;
    this.markChip.classList.toggle("on", this.marks.has(this.frame));
    if (this._drawnN !== n || this._drawnMarks !== this.marks.size) {
      this._drawnN = n; this._drawnMarks = this.marks.size;
      this.drawRuler();
    }

    const cs = (i.color_space || "").replace(" (display, sem conversão)", "");
    const tags = [
      `<span class="bxg-tag">${i.width}×${i.height}</span>`,
      `<span class="bxg-tag">${n} frames</span>`,
      `<span class="bxg-tag">${i.format}</span>`,
      `<span class="bxg-tag cs">${cs}</span>`,
      i.hdr ? `<span class="bxg-tag gr">HDR · ${i.hdr}</span>` : "",
      i.grain ? `<span class="bxg-tag gr">grão · ${i.grain}</span>` : "",
      i.has_audio ? `<span class="bxg-tag">♪ áudio</span>` : "",
      i.saved ? "" : `<span class="bxg-tag warn">só preview</span>`,
      (() => { const p = this.pinned(); return p && p.preview?.filename !== i.preview?.filename
        ? `<span class="bxg-tag gr">ref fixada · v${String(p.version || 0).padStart(3, "0")}</span>` : ""; })(),
      i.bruto ? `<span class="bxg-tag cs">bruto · ${i.bruto_frames} fr${i.bruto_frames < n ? " (segura o último)" : i.bruto_frames > n ? " (cortado)" : ""}</span>` : "",
    ].join("");
    const rel = (i.path || "").replace(/\\/g, "/").replace(/^.*?\/(output|temp)\//, "$1/");
    const marks = [...this.marks].sort((a, b) => a - b).map((m) => m + sf);
    this.foot.innerHTML = `<div class="bxg-tags">${tags}</div>${rel}`
      + (i.review ? `<br>review → ${i.review.replace(/\\/g, "/").split("/").pop()}` : "")
      + (marks.length ? `<div class="marks">⚑ ${marks.join(", ")}  <span style="color:#8d84a0">([ ] navega · Shift+Marcar copia)</span></div>` : "");
  }
}

// --------------------------------------------------------------------------
// Mostrar so os widgets que importam pro formato / modo escolhido
// --------------------------------------------------------------------------
function findW(node, name) { return node.widgets?.find((w) => w.name === name); }

function setVisible(node, name, visible) {
  const w = findW(node, name);
  if (!w) return;
  if (!!w.hidden === !visible) return;
  w.hidden = !visible;
  if (w.options) w.options.hidden = !visible;
  if (visible) {
    if (w._bxpCS) { w.computeSize = w._bxpCS; delete w._bxpCS; } else delete w.computeSize;
  } else {
    if (w.computeSize && !w._bxpCS) w._bxpCS = w.computeSize;
    w.computeSize = () => [0, -4];
  }
}

let GRAIN_PRESETS = null;
async function loadPresets() {
  if (GRAIN_PRESETS) return GRAIN_PRESETS;
  try { GRAIN_PRESETS = await (await api.fetchApi("/bruxos/pro/grain_presets")).json(); }
  catch (_) { GRAIN_PRESETS = {}; }
  return GRAIN_PRESETS;
}

function refreshVisibility(node) {
  const v = (n) => findW(node, n)?.value;
  const fmt = String(v("format") || "");
  const isVid = fmt.startsWith("mp4") || fmt.startsWith("mov");
  const isX = fmt.includes("h264") || fmt.includes("h265");
  setVisible(node, "crf", isX);
  setVisible(node, "preset", isX);
  setVisible(node, "exr_compression", fmt.startsWith("EXR"));
  setVisible(node, "review_mp4", !isVid);
  const hdr = !String(v("hdr_input") || "off").startsWith("off");
  setVisible(node, "input_interpretation", !hdr && !String(v("color_space") || "").startsWith("sRGB"));
  setVisible(node, "hdr_view", hdr);
  setVisible(node, "hdr_exposure", hdr);
  setVisible(node, "seed", v("seed_mode") === "manual");
  const gp = String(v("grain_preset") || "off");
  const on = gp !== "off";
  const custom = gp.startsWith("Personalizado");
  setVisible(node, "grain_strength", on && !custom);
  for (const n of ["grain_amount", "grain_size", "grain_roughness", "grain_color"]) setVisible(node, n, custom);
  setVisible(node, "grain_animate", on);
  setVisible(node, "grain_seed", on);
  node.setSize?.([node.size[0], node.computeSize()[1]]);
  node.setDirtyCanvas?.(true, true);
}

function hookWidget(node, name, fn) {
  const w = findW(node, name);
  if (!w) return;
  const orig = w.callback;
  w.callback = function () {
    const r = orig?.apply(this, arguments);
    try { fn(w.value); } catch (e) { console.warn("[Bruxos Pro]", e); }
    return r;
  };
}

// --------------------------------------------------------------------------
// Extensao
// --------------------------------------------------------------------------
app.registerExtension({
  name: "BruxosDoVFX.SaveVideoPro",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== NODE) return;
    injectStyle();
    loadPresets();

    const onCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onCreated?.apply(this, arguments);
      const node = this;
      const player = new ProPlayer(node);
      node._bxpPlayer = player;

      let measured = 0;
      const heightFor = () => (measured > 0 ? measured : 420) + 4;
      node.addDOMWidget("bruxos_pro_player", "BRUXOS_PRO_PLAYER", player.root, {
        serialize: false,
        hideOnZoom: false,
        getValue: () => "",
        setValue: () => {},
        getMinHeight: heightFor,
        getMaxHeight: heightFor,
        getHeight: heightFor,
      });
      const ro = new ResizeObserver(() => {
        if (document.fullscreenElement) return;
        const h = player.root.offsetHeight;
        if (h > 1 && Math.abs(h - measured) > 1) {
          measured = h;
          if (!window.LiteGraph?.vueNodesMode) node.setSize([node.size[0], node.computeSize()[1]]);
          node.setDirtyCanvas?.(true, true);
        }
      });
      ro.observe(player.root);
      if (node.size[0] < 460) node.setSize([460, node.size[1]]);

      for (const n of ["format", "color_space", "seed_mode", "hdr_input"]) hookWidget(node, n, () => refreshVisibility(node));
      hookWidget(node, "grain_preset", async (val) => {
        const p = (await loadPresets())[val];
        if (Array.isArray(p)) {
          // o preset "semeia" os sliders: trocar pra Personalizado parte daqui
          ["grain_amount", "grain_size", "grain_roughness", "grain_color"].forEach((n, i) => {
            const w = findW(node, n); if (w) w.value = p[i];
          });
        }
        refreshVisibility(node);
      });
      requestAnimationFrame(() => refreshVisibility(node));
      return r;
    };

    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const r = onConfigure?.apply(this, arguments);
      requestAnimationFrame(() => {
        refreshVisibility(this);
        const last = this.properties?.bruxos_pro_last;
        if (last && this._bxpPlayer && !this._bxpPlayer.info) this._bxpPlayer.load(last);
      });
      return r;
    };

    const onExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
      const r = onExecuted?.apply(this, arguments);
      const info = message?.bruxos_pro?.[0];
      if (info && this._bxpPlayer) {
        this._bxpPlayer.load(info);
        this.properties = this.properties || {};
        this.properties.bruxos_pro_last = info; // sobrevive a recarregar a pagina
      }
      return r;
    };
  },
});
