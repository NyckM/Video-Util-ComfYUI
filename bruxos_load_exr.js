// Bruxos do VFX — Load EXR: preview + crop-box + upload num widget HTML (DOM).
//
// Por que DOM e nao canvas: o renderer Vue (Nodes 2.0) ignora node.imgs e o
// onDrawForeground do LiteGraph. Um DOM widget (node.addDOMWidget) funciona
// nos DOIS renderers, e o box vira HTML comum com pointer events.
//
// O navegador nao abre EXR: o backend (/bruxos/exr/preview) le o arquivo (do
// seletor OU do exr_path), gira/flipa, aplica o OCIO e devolve um PNG do quadro
// INTEIRO. O box desenha em cima, em coordenadas 0..1 -- exatamente o que o
// Python usa no crop (depois do giro/flip).

// Resolve app/api sem depender da profundidade da pasta do arquivo.
const app =
    window.comfyAPI?.app?.app ?? (await import("../../scripts/app.js")).app;
const api =
    window.comfyAPI?.api?.api ?? (await import("../../scripts/api.js")).api;

const NODE_TYPE = "BruxosLoadEXR";
const LOG = "[Bruxos Load EXR]";
console.log(LOG, "extensao carregada");

// ---------------------------------------------------------------------------
// widgets (DynamicCombo pode prefixar: "color_management.view")
// ---------------------------------------------------------------------------
function findW(node, name) {
    return (node.widgets || []).find(
        (x) => x.name === name || x.name?.endsWith("." + name)
    );
}
function wv(node, name, fallback = "") {
    const w = findW(node, name);
    return w && w.value !== undefined && w.value !== null ? w.value : fallback;
}
function setW(node, name, value) {
    const w = findW(node, name);
    if (!w || w.value === value) return;
    w.value = value;
    try { w.callback?.(value); } catch (_) { /* ok */ }
}

function exrFile(node) {
    const f = wv(node, "file", wv(node, "exr"));
    return f && !String(f).startsWith("(") ? f : "";
}

function previewQuery(node) {
    return new URLSearchParams({
        file: exrFile(node),
        exr_path: String(wv(node, "exr_path")).trim(),
        layer: wv(node, "layer"),
        window: wv(node, "exr_window", "display window (Nuke)"),
        exposure: String(wv(node, "exposure", 0)),
        girar: wv(node, "girar", "off"),
        fh: wv(node, "flip_horizontal", false) ? "1" : "0",
        fv: wv(node, "flip_vertical", false) ? "1" : "0",
        cm: wv(node, "color_management", "off (raw / linear)"),
        cfg: wv(node, "ocio_config"),
        in: wv(node, "input_colorspace"),
        out: wv(node, "output_colorspace"),
        display: wv(node, "display"),
        view: wv(node, "view"),
        frame: String(wv(node, "frame_start", -1)),
        max: "1024",
    }).toString();
}

// ---------------------------------------------------------------------------
// estilo (uma vez)
// ---------------------------------------------------------------------------
if (!document.getElementById("bx-exr-style")) {
    const st = document.createElement("style");
    st.id = "bx-exr-style";
    st.textContent = `
.bx-exr{display:flex;flex-direction:column;gap:4px;width:100%;height:100%;box-sizing:border-box;
  font:11px sans-serif;color:#bbb;min-height:0}
.bx-exr .bar{display:flex;gap:6px;align-items:center;flex:0 0 auto}
.bx-exr button{background:#333;color:#ddd;border:1px solid #555;border-radius:6px;padding:3px 8px;
  cursor:pointer;font:11px sans-serif}
.bx-exr button:hover{background:#444}
.bx-exr .status{flex:1;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;opacity:.8}
.bx-exr .status.err{color:#f77;opacity:1}
.bx-exr .stage{flex:1 1 auto;min-height:120px;display:flex;align-items:center;justify-content:center;
  background:#161616;border-radius:6px;overflow:hidden;position:relative}
.bx-exr .stage.drag{outline:2px dashed #6af;outline-offset:-4px}
.bx-exr .wrap{position:relative;display:inline-block;max-width:100%;max-height:100%;line-height:0}
.bx-exr .wrap img{display:block;max-width:100%;max-height:100%;object-fit:contain;
  user-select:none;-webkit-user-drag:none;pointer-events:none}
.bx-exr .hint{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
  text-align:center;padding:12px;opacity:.6;line-height:1.4}
.bx-exr .ov{position:absolute;inset:0;cursor:crosshair;touch-action:none}
.bx-exr .box{position:absolute;box-sizing:border-box;border:1px solid #fff;
  box-shadow:0 0 0 9999px rgba(0,0,0,.55);cursor:move}
.bx-exr .box.off{border-style:dashed;box-shadow:0 0 0 9999px rgba(0,0,0,.25)}
.bx-exr .box .g{position:absolute;inset:0;pointer-events:none;
  background:linear-gradient(#fff5,#fff5) 33.33% 0/1px 100% no-repeat,
             linear-gradient(#fff5,#fff5) 66.66% 0/1px 100% no-repeat,
             linear-gradient(#fff5,#fff5) 0 33.33%/100% 1px no-repeat,
             linear-gradient(#fff5,#fff5) 0 66.66%/100% 1px no-repeat}
.bx-exr .h{position:absolute;width:10px;height:10px;background:#fff;border:1px solid #000;
  box-sizing:border-box;margin:-5px 0 0 -5px}
.bx-exr .lbl{position:absolute;left:0;top:-16px;line-height:14px;font:10px monospace;color:#fff;
  background:#000a;padding:0 3px;border-radius:3px;white-space:nowrap;pointer-events:none}
`;
    document.head.appendChild(st);
}

const HANDLES = {
    nw: [0, 0, "nwse-resize"], n: [0.5, 0, "ns-resize"], ne: [1, 0, "nesw-resize"],
    e: [1, 0.5, "ew-resize"], se: [1, 1, "nwse-resize"], s: [0.5, 1, "ns-resize"],
    sw: [0, 1, "nesw-resize"], w: [0, 0.5, "ew-resize"],
};

const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const r3 = (v) => Math.round(v * 1000) / 1000;

function aspectRatio(node) {
    const a = String(wv(node, "aspect", "livre"));
    const m = a.match(/^(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)/);
    return m ? parseFloat(m[1]) / parseFloat(m[2]) : 0; // 0 = livre
}

// ---------------------------------------------------------------------------
// o widget
// ---------------------------------------------------------------------------
function buildWidget(node) {
    const root = document.createElement("div");
    root.className = "bx-exr";
    root.innerHTML = `
      <div class="bar">
        <button class="up" title="Envia um .exr para ComfyUI/input">📁 upload EXR</button>
        <button class="reset" title="Box = quadro inteiro">⟲ crop</button>
        <span class="status"></span>
      </div>
      <div class="stage">
        <div class="hint">Escolha um EXR, cole um caminho em <b>exr_path</b>,<br>ou arraste um .exr aqui.</div>
        <div class="wrap" style="display:none">
          <img>
          <div class="ov"><div class="box"><div class="g"></div><div class="lbl"></div></div></div>
        </div>
      </div>`;
    const $ = (s) => root.querySelector(s);
    const ui = {
        root, stage: $(".stage"), wrap: $(".wrap"), img: $("img"), ov: $(".ov"),
        box: $(".box"), lbl: $(".lbl"), hint: $(".hint"), status: $(".status"),
    };
    for (const [k, [fx, fy, cur]] of Object.entries(HANDLES)) {
        const h = document.createElement("div");
        h.className = "h";
        h.dataset.h = k;
        h.style.left = fx * 100 + "%";
        h.style.top = fy * 100 + "%";
        h.style.cursor = cur;
        ui.box.appendChild(h);
    }

    // impede que o canvas do LiteGraph roube o arrasto/scroll
    for (const ev of ["pointerdown", "mousedown", "wheel", "contextmenu"]) {
        root.addEventListener(ev, (e) => e.stopPropagation());
    }

    $(".up").onclick = () => pickExr(node);
    $(".reset").onclick = () => writeCrop(node, { x: 0, y: 0, w: 1, h: 1 });

    // drag & drop de arquivo
    ui.stage.addEventListener("dragover", (e) => {
        if ([...(e.dataTransfer?.items || [])].some((i) => i.kind === "file")) {
            e.preventDefault(); e.stopPropagation(); ui.stage.classList.add("drag");
        }
    });
    ui.stage.addEventListener("dragleave", () => ui.stage.classList.remove("drag"));
    ui.stage.addEventListener("drop", (e) => {
        ui.stage.classList.remove("drag");
        const f = [...(e.dataTransfer?.files || [])].find((x) => /\.exr$/i.test(x.name));
        if (f) { e.preventDefault(); e.stopPropagation(); uploadExr(node, f); }
    });

    ui.ov.addEventListener("pointerdown", (e) => startDrag(node, e));
    return ui;
}

function readCrop(node) {
    return {
        x: +wv(node, "crop_x", 0), y: +wv(node, "crop_y", 0),
        w: +wv(node, "crop_w", 1), h: +wv(node, "crop_h", 1),
    };
}

function writeCrop(node, c, autoCrop = true) {
    setW(node, "crop_x", r3(clamp(c.x, 0, 1)));
    setW(node, "crop_y", r3(clamp(c.y, 0, 1)));
    setW(node, "crop_w", r3(clamp(c.w, 0.01, 1)));
    setW(node, "crop_h", r3(clamp(c.h, 0.01, 1)));
    // mexeu no box -> liga o modo crop (senao o box nao faz nada)
    if (autoCrop && !String(wv(node, "fit_mode", "")).startsWith("crop")) {
        const isFull = c.x <= 0 && c.y <= 0 && c.w >= 1 && c.h >= 1;
        if (!isFull) setW(node, "fit_mode", "crop");
    }
    syncBox(node);
    node.setDirtyCanvas?.(true, true);
}

// forca a proporcao em pixels: (w*W)/(h*H) = ar
function enforceAspect(c, ar, W, H, anchor = "center") {
    if (!ar || !W || !H) return c;
    const k = (ar * H) / W; // w = k*h em normalizado
    let { x, y, w, h } = c;
    if (w / h > k) w = k * h; else h = w / k;
    if (w > 1) { w = 1; h = w / k; }
    if (h > 1) { h = 1; w = k * h; }
    if (anchor === "center") {
        x = c.x + (c.w - w) / 2; y = c.y + (c.h - h) / 2;
    }
    x = clamp(x, 0, 1 - w); y = clamp(y, 0, 1 - h);
    return { x, y, w, h };
}

function startDrag(node, e) {
    const ui = node.__bxExr;
    if (!ui || e.button !== 0) return;
    e.preventDefault(); e.stopPropagation();
    const rect = ui.ov.getBoundingClientRect();
    const pt = (ev) => ({
        x: clamp((ev.clientX - rect.left) / rect.width, 0, 1),
        y: clamp((ev.clientY - rect.top) / rect.height, 0, 1),
    });
    const p0 = pt(e);
    const c0 = readCrop(node);
    const ar = aspectRatio(node);
    const W = ui.img.naturalWidth, H = ui.img.naturalHeight;
    const handle = e.target.dataset?.h;
    const onBox = !handle && ui.box.contains(e.target);
    const mode = handle ? "resize" : onBox ? "move" : "draw";

    const move = (ev) => {
        const p = pt(ev);
        let c;
        if (mode === "move") {
            c = { ...c0,
                x: clamp(c0.x + p.x - p0.x, 0, 1 - c0.w),
                y: clamp(c0.y + p.y - p0.y, 0, 1 - c0.h) };
        } else if (mode === "draw") {
            let x0 = p0.x, y0 = p0.y, x1 = p.x, y1 = p.y;
            let w = Math.abs(x1 - x0), h = Math.abs(y1 - y0);
            if (ar && W && H) {
                const k = (ar * H) / W;
                if (w / Math.max(h, 1e-6) > k) h = w / k; else w = k * h;
                w = Math.min(w, x1 >= x0 ? 1 - x0 : x0);
                h = w / k;
                if (h > (y1 >= y0 ? 1 - y0 : y0)) { h = y1 >= y0 ? 1 - y0 : y0; w = k * h; }
            }
            c = { x: x1 >= x0 ? x0 : x0 - w, y: y1 >= y0 ? y0 : y0 - h,
                  w: Math.max(0.01, w), h: Math.max(0.01, h) };
        } else {
            // resize pelas alcas; lado oposto fica fixo
            let L = c0.x, T = c0.y, R = c0.x + c0.w, B = c0.y + c0.h;
            if (handle.includes("w")) L = clamp(p.x, 0, R - 0.01);
            if (handle.includes("e")) R = clamp(p.x, L + 0.01, 1);
            if (handle.includes("n")) T = clamp(p.y, 0, B - 0.01);
            if (handle.includes("s")) B = clamp(p.y, T + 0.01, 1);
            c = { x: L, y: T, w: R - L, h: B - T };
            if (ar && W && H) {
                const k = (ar * H) / W;
                const horiz = handle === "e" || handle === "w";
                const vert = handle === "n" || handle === "s";
                let w = c.w, h = c.h;
                if (horiz) h = w / k;
                else if (vert) w = k * h;
                else if (w / h > k) h = w / k; else w = k * h;
                // ancora no lado/canto oposto; eixo sem alca fica centrado
                const R0 = c0.x + c0.w, B0 = c0.y + c0.h;
                const x = handle.includes("w") ? R0 - w
                        : handle.includes("e") ? c0.x : c0.x + (c0.w - w) / 2;
                const y = handle.includes("n") ? B0 - h
                        : handle.includes("s") ? c0.y : c0.y + (c0.h - h) / 2;
                if (w > 1 || h > 1 || x < -1e-6 || y < -1e-6 || x + w > 1 + 1e-6 || y + h > 1 + 1e-6) return; // bateu na borda
                c = { x, y, w, h };
            }
        }
        writeCrop(node, c);
    };
    const up = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    if (mode === "draw") move(e);
}

function syncBox(node) {
    const ui = node.__bxExr;
    if (!ui) return;
    const c = readCrop(node);
    const s = ui.box.style;
    s.left = c.x * 100 + "%"; s.top = c.y * 100 + "%";
    s.width = c.w * 100 + "%"; s.height = c.h * 100 + "%";
    const on = String(wv(node, "fit_mode", "")).startsWith("crop");
    ui.box.classList.toggle("off", !on);
    const W = ui.img.naturalWidth, H = ui.img.naturalHeight;
    const srcW = node.__bxExrFullW || W, srcH = node.__bxExrFullH || H;
    ui.lbl.textContent = (on ? "" : "fit_mode ≠ crop · ") +
        (srcW ? `${Math.round(c.w * srcW)}×${Math.round(c.h * srcH)}` : "");
}

// quando o aspect muda, encaixa o maior box daquela proporcao no box atual
function applyAspectChange(node) {
    const a = String(wv(node, "aspect", "livre"));
    if (a === node.__bxExrAspect) return;
    const first = node.__bxExrAspect === undefined;
    node.__bxExrAspect = a;
    if (first) return;
    const ar = aspectRatio(node);
    const ui = node.__bxExr;
    if (!ar || !ui?.img.naturalWidth) return;
    writeCrop(node, enforceAspect(readCrop(node), ar, ui.img.naturalWidth, ui.img.naturalHeight));
}

// ---------------------------------------------------------------------------
// preview
// ---------------------------------------------------------------------------
function setStatus(node, text, err = false) {
    const ui = node.__bxExr;
    if (!ui) return;
    ui.status.textContent = text || "";
    ui.status.title = text || "";
    ui.status.classList.toggle("err", !!err);
}

async function refresh(node) {
    const ui = node.__bxExr;
    if (!ui) return;
    const query = previewQuery(node);
    if (query === node.__bxExrQuery) return;
    node.__bxExrQuery = query;
    if (!exrFile(node) && !String(wv(node, "exr_path")).trim()) {
        ui.wrap.style.display = "none"; ui.hint.style.display = "";
        setStatus(node, "");
        return;
    }
    setStatus(node, "carregando…");
    try {
        const res = await api.fetchApi("/bruxos/exr/preview?" + query, { cache: "no-store" });
        if (node.__bxExrQuery !== query) return;
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            setStatus(node, err.error || `erro ${res.status}`, true);
            console.warn(LOG, "preview:", err.error || res.status);
            return;
        }
        node.__bxExrFullW = +res.headers.get("X-Bruxos-Width") || 0;
        node.__bxExrFullH = +res.headers.get("X-Bruxos-Height") || 0;
        const layers = res.headers.get("X-Bruxos-Layers");
        const seq = res.headers.get("X-Bruxos-Seq") || "";
        const url = URL.createObjectURL(await res.blob());
        ui.img.onload = () => {
            if (node.__bxExrUrl) URL.revokeObjectURL(node.__bxExrUrl);
            node.__bxExrUrl = url;
            ui.hint.style.display = "none";
            ui.wrap.style.display = "";
            const dims = node.__bxExrFullW ? `${node.__bxExrFullW}×${node.__bxExrFullH}` : "";
            let lay = "";
            try { lay = JSON.parse(layers || "[]").join("  "); } catch (_) { /* ok */ }
            setStatus(node, [dims, seq ? "seq " + seq : "", lay].filter(Boolean).join("  ·  "));
            syncBox(node);
            node.setDirtyCanvas?.(true, true);
        };
        ui.img.src = url;
    } catch (e) {
        setStatus(node, "preview falhou: " + e.message, true);
        console.warn(LOG, "preview falhou:", e);
    }
}

// ---------------------------------------------------------------------------
// upload (o /upload/image do servidor aceita qualquer arquivo)
// ---------------------------------------------------------------------------
async function uploadExr(node, file) {
    if (!file || !/\.exr$/i.test(file.name)) {
        setStatus(node, "escolha um arquivo .exr", true);
        return;
    }
    const body = new FormData();
    body.append("image", file, file.name);
    body.append("type", "input");
    body.append("subfolder", "");
    setStatus(node, `enviando ${file.name}…`);
    try {
        const res = await api.fetchApi("/upload/image", { method: "POST", body });
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        const data = await res.json();
        const name = data.subfolder ? `${data.subfolder}/${data.name}` : data.name;
        const w = findW(node, "file") || findW(node, "exr");
        if (w) {
            const values = w.options?.values;
            if (Array.isArray(values)) {
                for (let i = values.length - 1; i >= 0; i--) {
                    if (String(values[i]).startsWith("(")) values.splice(i, 1);
                }
                if (!values.includes(name)) { values.push(name); values.sort(); }
            }
        }
        setW(node, "file", name);
        setW(node, "exr_path", ""); // o upload passa a valer (exr_path tem prioridade)
        node.__bxExrQuery = null;
        refresh(node);
    } catch (e) {
        setStatus(node, "upload falhou: " + e.message, true);
        console.error(LOG, "upload falhou:", e);
    }
}

function pickExr(node) {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".exr,image/x-exr";
    input.style.display = "none";
    input.onchange = () => {
        const f = input.files?.[0];
        input.remove();
        if (f) uploadExr(node, f);
    };
    document.body.appendChild(input);
    input.click();
}

// ---------------------------------------------------------------------------
app.registerExtension({
    name: "bruxos.load_exr.preview",
    async nodeCreated(node) {
        if (node.comfyClass !== NODE_TYPE) return;

        const ui = buildWidget(node);
        node.__bxExr = ui;
        node.addDOMWidget("exr_preview", "bruxos_exr_preview", ui.root, {
            serialize: false,        // nao entra em widgets_values
            hideOnZoom: false,
            getMinHeight: () => 260,
        });
        // garante espaco pro preview no LiteGraph
        requestAnimationFrame(() => {
            try {
                const sz = node.computeSize?.();
                if (sz) node.setSize([Math.max(node.size[0], 340), Math.max(node.size[1], sz[1])]);
            } catch (_) { /* ok */ }
        });

        // O DynamicCombo cria/remove widgets quando o modo muda, entao comparamos
        // a query a cada 300 ms (so faz request se algo que afeta o preview mudou)
        // e re-sincronizamos o box com os campos crop_* (edicao manual).
        const timer = setInterval(() => {
            if (!node.graph) return;
            applyAspectChange(node);
            refresh(node);
            syncBox(node);
        }, 300);

        const onRemoved = node.onRemoved;
        node.onRemoved = function () {
            clearInterval(timer);
            if (node.__bxExrUrl) URL.revokeObjectURL(node.__bxExrUrl);
            return onRemoved?.apply(this, arguments);
        };
    },
});
