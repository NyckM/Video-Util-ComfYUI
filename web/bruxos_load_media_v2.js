import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

// Um unico controlador para cada loader 2.0. Nao reordena node.widgets e nao
// usa primeiro o mesmo /view direto do Load Video nativo. Somente quando o
// navegador realmente rejeita o codec, troca para um proxy H.264 de preview.
const CONTROLLERS = new Set();
const COLORS = { crop: "#a855f7", text: "#d6d6dc", muted: "#92929c" };

function widget(node, name) {
  return node.widgets?.find((item) => item.name === name);
}

function value(node, name, fallback) {
  const item = widget(node, name);
  return item == null || item.value == null ? fallback : item.value;
}

function setValue(node, name, next) {
  const item = widget(node, name);
  if (!item) return;
  item.value = next;
  item.callback?.(next);
}

function mediaRef(name) {
  if (!name || String(name).startsWith("(")) return null;
  const normalized = String(name).replaceAll("\\", "/");
  const slash = normalized.lastIndexOf("/");
  return {
    filename: slash >= 0 ? normalized.slice(slash + 1) : normalized,
    subfolder: slash >= 0 ? normalized.slice(0, slash) : "",
    type: "input",
  };
}

function viewURL(name, kind) {
  const ref = mediaRef(name);
  if (!ref) return "";
  const params = new URLSearchParams(ref);
  // preview/channel sao parametros do pipeline de IMAGEM. Passa-los para MP4
  // faz /view tentar tratar o video como bitmap e, em alguns arquivos, responder
  // 500. O Load Video oficial usa o arquivo de video diretamente.
  if (kind !== "video") {
    params.set("preview", "");
    params.set("channel", "rgba");
  }
  return api.apiURL(`/view?${params}`);
}

function proxyURL(name) {
  const ref = mediaRef(name);
  if (!ref) return "";
  const params = new URLSearchParams(ref);
  params.set("maxside", "960");
  return api.apiURL(`/bruxos/video_preview?${params}`);
}

function markNonSerializable(item) {
  if (!item) return item;
  item.serialize = false;
  item.options = { ...(item.options || {}), serialize: false };
  item.serializeValue = () => undefined;
  return item;
}

async function upload(file) {
  const sizeMB = file?.size ? (file.size / (1024 * 1024)).toFixed(1) : "?";
  const body = new FormData();
  body.append("image", file);
  let response;
  try {
    response = await api.fetchApi("/upload/image", { method: "POST", body });
  } catch (error) {
    throw new Error(
      `falha de rede ao enviar ${sizeMB} MB (${error}). ` +
      "Reinicie pelo BAT atualizado ou use video_path."
    );
  }
  if (!response.ok) {
    const hint = response.status === 413
      ? " Arquivo acima do limite do servidor; reinicie pelo BAT atualizado ou use video_path."
      : "";
    throw new Error(`${response.status} ${response.statusText} (${sizeMB} MB).${hint}`);
  }
  const data = await response.json();
  return data.subfolder ? `${data.subfolder}/${data.name}` : data.name;
}

function addUpload(node, kind, refresh, selectorName = kind) {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = kind === "video"
    ? "video/*,.mkv,.avi,.m4v,.wmv,.flv"
    : "image/*,.tif,.tiff";
  input.style.display = "none";
  document.body.append(input);

  const choose = async (file) => {
    if (!file) return false;
    try {
      const name = await upload(file);
      const selector = widget(node, selectorName);
      selector.options ||= {};
      selector.options.values ||= [];
      if (!selector.options.values.includes(name)) selector.options.values.push(name);
      selector.value = name;
      selector.callback?.(name);
      refresh();
      node.setDirtyCanvas?.(true, true);
      return true;
    } catch (error) {
      alert(`[Bruxos 2.0] upload falhou: ${error}`);
      return false;
    }
  };

  input.onchange = async () => {
    await choose(input.files?.[0]);
    input.value = "";
  };
  markNonSerializable(node.addWidget(
    "button",
    kind === "video" ? "📁 escolher vídeo (upload)" : "📁 escolher imagem (upload)",
    null,
    () => input.click(),
    { serialize: false },
  ));

  const oldDragOver = node.onDragOver;
  const oldDragDrop = node.onDragDrop;
  node.onDragOver = function (event) {
    return event?.dataTransfer?.types?.includes?.("Files") || oldDragOver?.apply(this, arguments);
  };
  node.onDragDrop = async function (event) {
    const file = event?.dataTransfer?.files?.[0];
    if (file) return choose(file);
    return oldDragDrop?.apply(this, arguments) ?? false;
  };
  return () => input.remove();
}

function rotation(node) {
  const text = String(value(node, "girar", "off"));
  if (text.startsWith("-90")) return -90;
  if (text.startsWith("90")) return 90;
  if (text.startsWith("180")) return 180;
  return 0;
}

function mediaDimensions(controller) {
  const source = controller.source;
  const sw = source.videoWidth || source.naturalWidth || 0;
  const sh = source.videoHeight || source.naturalHeight || 0;
  const rot = rotation(controller.node);
  return rot === 90 || rot === -90 ? [sh, sw] : [sw, sh];
}

function drawOriented(controller) {
  const { canvas, context: ctx, source, node } = controller;
  const sw = source.videoWidth || source.naturalWidth || 0;
  const sh = source.videoHeight || source.naturalHeight || 0;
  if (!sw || !sh) return false;
  const rot = rotation(node);
  const [rw, rh] = rot === 90 || rot === -90 ? [sh, sw] : [sw, sh];
  const width = Math.max(160, Math.min(900, Math.round(canvas.clientWidth || 320)));
  const height = Math.max(80, Math.round(width * rh / rw));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }

  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.fillStyle = "#09090b";
  ctx.fillRect(0, 0, width, height);
  ctx.save();
  if (value(node, "flip_horizontal", false)) {
    ctx.translate(width, 0);
    ctx.scale(-1, 1);
  }
  if (value(node, "flip_vertical", false)) {
    ctx.translate(0, height);
    ctx.scale(1, -1);
  }
  if (rot === 90) {
    ctx.translate(width, 0);
    ctx.rotate(Math.PI / 2);
    ctx.drawImage(source, 0, 0, height, width);
  } else if (rot === -90) {
    ctx.translate(0, height);
    ctx.rotate(-Math.PI / 2);
    ctx.drawImage(source, 0, 0, height, width);
  } else if (rot === 180) {
    ctx.translate(width, height);
    ctx.rotate(Math.PI);
    ctx.drawImage(source, 0, 0, width, height);
  } else {
    ctx.drawImage(source, 0, 0, width, height);
  }
  ctx.restore();
  return true;
}

function cropBox(node) {
  let x = Number(value(node, "crop_x", 0));
  let y = Number(value(node, "crop_y", 0));
  let w = Number(value(node, "crop_w", 1));
  let h = Number(value(node, "crop_h", 1));
  w = Math.max(0.01, Math.min(1, w));
  h = Math.max(0.01, Math.min(1, h));
  x = Math.max(0, Math.min(1 - w, x));
  y = Math.max(0, Math.min(1 - h, y));
  return { x, y, w, h };
}

function drawCrop(controller) {
  const { canvas, context: ctx, node } = controller;
  const box = cropBox(node);
  const x = box.x * canvas.width;
  const y = box.y * canvas.height;
  const w = box.w * canvas.width;
  const h = box.h * canvas.height;
  ctx.save();
  ctx.fillStyle = "rgba(0,0,0,.42)";
  ctx.beginPath();
  ctx.rect(0, 0, canvas.width, canvas.height);
  ctx.rect(x, y, w, h);
  ctx.fill("evenodd");
  ctx.strokeStyle = COLORS.crop;
  ctx.lineWidth = 3;
  ctx.strokeRect(x, y, w, h);
  const size = 9;
  ctx.fillStyle = "#fff";
  ctx.strokeStyle = COLORS.crop;
  for (const [hx, hy] of [[x, y], [x + w, y], [x, y + h], [x + w, y + h]]) {
    ctx.fillRect(hx - size / 2, hy - size / 2, size, size);
    ctx.strokeRect(hx - size / 2, hy - size / 2, size, size);
  }
  ctx.restore();
}

function render(controller) {
  if (!controller.wrap.isConnected) return;
  if (!drawOriented(controller)) return;
  drawCrop(controller);
  const [width, height] = mediaDimensions(controller);
  const box = cropBox(controller.node);
  const ext = String(value(controller.node, controller.selectorName, "")).split(".").pop()?.toUpperCase() || "";
  controller.info.textContent = width && height
    ? `${width} × ${height} px    ${ext}    crop ${Math.round(width * box.w)} × ${Math.round(height * box.h)}${controller.usingProxy ? "    preview proxy H.264" : ""}`
    : "Carregando preview…";
}

function applyAspect(controller) {
  const text = String(value(controller.node, "aspect", "livre"));
  if (!text.includes(":")) return;
  const [aw, ah] = text.split(":").map(Number);
  const [mw, mh] = mediaDimensions(controller);
  if (!aw || !ah || !mw || !mh) return;
  const old = cropBox(controller.node);
  const centerX = old.x + old.w / 2;
  const centerY = old.y + old.h / 2;
  let w = old.w;
  let h = (w * mw * ah) / (aw * mh);
  if (h > 1) {
    h = 1;
    w = (h * aw * mh) / (mw * ah);
  }
  setValue(controller.node, "crop_w", w);
  setValue(controller.node, "crop_h", h);
  setValue(controller.node, "crop_x", Math.max(0, Math.min(1 - w, centerX - w / 2)));
  setValue(controller.node, "crop_y", Math.max(0, Math.min(1 - h, centerY - h / 2)));
  render(controller);
}

function installCropInteraction(controller) {
  const { canvas, node } = controller;
  let drag = null;
  const point = (event) => {
    const rect = canvas.getBoundingClientRect();
    return [(event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height];
  };
  canvas.onpointerdown = (event) => {
    const [px, py] = point(event);
    const box = cropBox(node);
    const tolerance = 14 / Math.max(80, canvas.clientWidth);
    const right = box.x + box.w;
    const bottom = box.y + box.h;
    const resize = Math.abs(px - right) < tolerance && Math.abs(py - bottom) < tolerance;
    const inside = px >= box.x && px <= right && py >= box.y && py <= bottom;
    if (!resize && !inside) return;
    drag = { px, py, box, resize };
    canvas.setPointerCapture(event.pointerId);
    event.preventDefault();
  };
  canvas.onpointermove = (event) => {
    if (!drag) return;
    const [px, py] = point(event);
    const dx = px - drag.px;
    const dy = py - drag.py;
    let box = { ...drag.box };
    if (drag.resize) {
      box.w = Math.max(0.01, Math.min(1 - box.x, box.w + dx));
      box.h = Math.max(0.01, Math.min(1 - box.y, box.h + dy));
    } else {
      box.x = Math.max(0, Math.min(1 - box.w, box.x + dx));
      box.y = Math.max(0, Math.min(1 - box.h, box.y + dy));
    }
    setValue(node, "crop_x", box.x);
    setValue(node, "crop_y", box.y);
    setValue(node, "crop_w", box.w);
    setValue(node, "crop_h", box.h);
    render(controller);
  };
  const release = (event) => {
    if (!drag) return;
    drag = null;
    try { canvas.releasePointerCapture(event.pointerId); } catch (_) {}
  };
  canvas.onpointerup = release;
  canvas.onpointercancel = release;
}

function trimWindow(controller) {
  const fps = Number(controller.probe?.fps || 0);
  const duration = Number(controller.source.duration || controller.probe?.duration || 0);
  if (!fps || !duration) return [0, duration];
  const skip = Math.max(0, Number(value(controller.node, "skip_first_frames", 0)));
  const cap = Math.max(0, Number(value(controller.node, "frame_load_cap", 0)));
  const nth = Math.max(1, Number(value(controller.node, "select_every_nth", 1)));
  const forced = Math.max(0, Number(value(controller.node, "force_rate", 0)));
  const start = Math.min(duration, skip / fps);
  let end = duration;
  if (cap) end = Math.min(duration, start + (forced ? cap / forced : cap * nth / fps));
  return [start, Math.max(start, end)];
}

function groupMembers(controller) {
  const group = String(value(controller.node, "sync_group", "")).trim();
  if (!group) return [controller];
  return [...CONTROLLERS].filter((item) => item.kind === "video" && item.wrap.isConnected &&
    String(value(item.node, "sync_group", "")).trim() === group);
}

function syncFrom(controller, play) {
  const [start, end] = trimWindow(controller);
  const span = Math.max(0.001, end - start);
  const progress = Math.max(0, Math.min(1, (controller.source.currentTime - start) / span));
  for (const item of groupMembers(controller)) {
    const [otherStart, otherEnd] = trimWindow(item);
    item.source.currentTime = otherStart + progress * Math.max(0, otherEnd - otherStart);
    if (play) item.source.play().catch(() => {});
  }
}

function addVideoControls(controller) {
  const controls = document.createElement("div");
  controls.style.cssText = "display:flex;align-items:center;gap:6px;padding:5px 0 1px";
  const play = document.createElement("button");
  play.textContent = "▶";
  const stop = document.createElement("button");
  stop.textContent = "■";
  const seek = document.createElement("input");
  seek.type = "range";
  seek.min = "0";
  seek.max = "1";
  seek.step = "0.001";
  seek.value = "0";
  seek.style.cssText = "flex:1;min-width:60px;accent-color:#a855f7";
  const clock = document.createElement("span");
  clock.style.cssText = "font:10px monospace;color:#aaa";
  controls.append(play, stop, seek, clock);
  controller.wrap.append(controls);

  play.onclick = () => {
    if (controller.source.paused) {
      const [start, end] = trimWindow(controller);
      if (controller.source.currentTime < start || controller.source.currentTime >= end) controller.source.currentTime = start;
      syncFrom(controller, true);
    } else {
      for (const item of groupMembers(controller)) item.source.pause();
    }
  };
  stop.onclick = () => {
    for (const item of groupMembers(controller)) {
      item.source.pause();
      item.source.currentTime = trimWindow(item)[0];
    }
  };
  seek.oninput = () => {
    const [start, end] = trimWindow(controller);
    controller.source.currentTime = start + Number(seek.value) * Math.max(0, end - start);
    syncFrom(controller, false);
    render(controller);
  };

  const tick = () => {
    if (!controller.wrap.isConnected) return;
    const [start, end] = trimWindow(controller);
    if (!controller.source.paused && end > start && controller.source.currentTime >= end) {
      controller.source.currentTime = start;
      syncFrom(controller, true);
    }
    const progress = end > start ? (controller.source.currentTime - start) / (end - start) : 0;
    seek.value = String(Math.max(0, Math.min(1, progress)));
    clock.textContent = `${Math.max(0, controller.source.currentTime - start).toFixed(2)} / ${Math.max(0, end - start).toFixed(2)}s`;
    play.textContent = controller.source.paused ? "▶" : "⏸";
    render(controller);
    controller.raf = requestAnimationFrame(tick);
  };
  controller.raf = requestAnimationFrame(tick);
}

async function probeVideo(controller, name) {
  const ref = mediaRef(name);
  if (!ref) return;
  const params = new URLSearchParams(ref);
  try {
    const response = await api.fetchApi(`/bruxos/v2/video_info?${params}`);
    if (response.ok) controller.probe = await response.json();
  } catch (_) {}
}

function loadSource(controller) {
  const name = value(controller.node, controller.selectorName, "");
  const url = viewURL(name, controller.kind);
  controller.probe = null;
  controller.currentName = String(name || "");
  controller.usingProxy = false;
  if (!url || String(value(controller.node, `${controller.kind}_path`, "")).trim()) {
    controller.info.textContent = "Preview disponível para arquivos da pasta ComfyUI/input.";
    if (controller.kind === "video") controller.source.pause();
    controller.source.removeAttribute("src");
    if (controller.kind === "video") controller.source.load();
    return;
  }
  if (controller.kind === "video") {
    probeVideo(controller, name);
    controller.source.pause();
    controller.source.removeAttribute("src");
    controller.source.load();
    controller.source.src = url;
    controller.source.load();
  } else {
    controller.source.src = url;
  }
}

function hookCallbacks(controller) {
  const redraw = new Set([
    "fit_mode", "target_width", "target_height", "crop_x", "crop_y", "crop_w", "crop_h",
    "girar", "flip_horizontal", "flip_vertical", "skip_first_frames", "frame_load_cap",
    "select_every_nth", "force_rate",
  ]);
  for (const item of controller.node.widgets || []) {
    if (item.name === controller.selectorName) {
      const original = item.callback;
      item.callback = function () {
        const result = original?.apply(this, arguments);
        loadSource(controller);
        return result;
      };
    } else if (item.name === "aspect") {
      const original = item.callback;
      item.callback = function () {
        const result = original?.apply(this, arguments);
        applyAspect(controller);
        return result;
      };
    } else if (redraw.has(item.name)) {
      const original = item.callback;
      item.callback = function () {
        const result = original?.apply(this, arguments);
        render(controller);
        return result;
      };
    }
  }
}

function createController(node, kind, selectorName = kind) {
  if (node._bruxosLoaderV2) return node._bruxosLoaderV2;
  const wrap = document.createElement("div");
  wrap.style.cssText = "width:100%;box-sizing:border-box;overflow:hidden;padding:2px 1px;color:#ddd";
  const canvas = document.createElement("canvas");
  canvas.style.cssText = "display:block;width:100%;height:auto;max-height:360px;background:#09090b;border-radius:6px;touch-action:none";
  const info = document.createElement("div");
  info.style.cssText = `padding-top:4px;font:10px monospace;color:${COLORS.muted};white-space:pre-wrap`;
  const source = document.createElement(kind === "video" ? "video" : "img");
  source.style.display = "none";
  if (kind === "video") {
    source.muted = true;
    source.playsInline = true;
    source.preload = "auto";
  }
  wrap.append(canvas, source);

  const controller = {
    node, kind, selectorName, wrap, canvas, source, info,
    context: canvas.getContext("2d"), probe: null, raf: 0,
    currentName: "", usingProxy: false,
  };
  node._bruxosLoaderV2 = controller;
  CONTROLLERS.add(controller);
  if (kind === "video") addVideoControls(controller);
  wrap.append(info);

  const height = () => kind === "video" ? 285 : 245;
  const dom = markNonSerializable(node.addDOMWidget("bruxos_loader_v2_preview", "preview", wrap, {
    serialize: false,
    hideOnZoom: false,
    getMinHeight: height,
    getMaxHeight: height,
  }));
  dom.computeSize = (width) => [width, height()];
  dom.computeLayoutSize = () => ({ minWidth: 1, minHeight: height(), maxHeight: height() });

  source.addEventListener(kind === "video" ? "loadedmetadata" : "load", () => {
    render(controller);
    node.setDirtyCanvas?.(true, true);
  });
  if (kind === "video") {
    // loadedmetadata so conhece tamanho/duracao; loadeddata confirma que existe
    // um frame decodificado para desenhar no canvas.
    source.addEventListener("loadeddata", () => {
      render(controller);
      node.setDirtyCanvas?.(true, true);
    });
    source.addEventListener("seeked", () => render(controller));
  }
  source.addEventListener("error", () => {
    if (kind === "video" && !controller.usingProxy && controller.currentName) {
      const fallback = proxyURL(controller.currentName);
      if (fallback) {
        controller.usingProxy = true;
        info.textContent = "Preview direto indisponível; preparando proxy H.264 compatível…";
        source.pause();
        source.removeAttribute("src");
        source.load();
        source.src = fallback;
        source.load();
        return;
      }
    }
    const mediaError = source.error;
    const detail = mediaError ? ` (código ${mediaError.code}: ${mediaError.message || "sem detalhe"})` : "";
    info.textContent = `Não foi possível abrir o preview${detail}.`;
  });
  installCropInteraction(controller);
  hookCallbacks(controller);
  const removeUpload = addUpload(node, kind, () => loadSource(controller), selectorName);

  const oldRemoved = node.onRemoved;
  node.onRemoved = function () {
    cancelAnimationFrame(controller.raf);
    CONTROLLERS.delete(controller);
    removeUpload();
    return oldRemoved?.apply(this, arguments);
  };
  loadSource(controller);
  return controller;
}

app.registerExtension({
  name: "BruxosDoVFX.LoadersV2.StablePreview.CropView2",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    // O componente nativo continua sendo a fonte principal do Video 2.0,
    // mas ele nao conhece os widgets crop_x/y/w/h. O canvas Bruxos fica por
    // cima dessa mesma midia para mostrar e editar o recorte antes da fila.
    const kind = nodeData?.name === "BruxosLoadImageV2"
      ? "image"
      : nodeData?.name === "BruxosLoadVideoV2"
        ? "video"
        : null;
    if (!kind) return;
    const selectorName = kind === "video" ? "file" : "image";
    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const result = created?.apply(this, arguments);
      createController(this, kind, selectorName);
      return result;
    };
    const configured = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const result = configured?.apply(this, arguments);
      const controller = this._bruxosLoaderV2;
      if (controller) queueMicrotask(() => loadSource(controller));
      return result;
    };
  },
});
