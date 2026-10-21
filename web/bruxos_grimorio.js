import { api } from "../../scripts/api.js";

// Grimorio -- base visual compartilhada dos players Bruxos (Save Video Pro e
// Load Video Pro): CSS, icones e helpers. Nao registra extensao nenhuma.

const STYLE_ID = "bruxos-grimorio-style";

const CSS = `
.bxg{--bg:#120f18;--panel:#1a1622;--line:#2e2838;--ink:#e9e4f2;--mute:#8d84a0;--amber:#f2b544;--amber2:#ffcf70;--violet:#8b5cf6;--mark:#ff6b8a;
  display:flex;flex-direction:column;width:100%;box-sizing:border-box;background:var(--bg);border-radius:10px;overflow:hidden;
  font-family:Inter,system-ui,sans-serif;color:var(--ink);outline:none;box-shadow:inset 0 0 0 1px var(--line)}
.bxg *{box-sizing:border-box}
.bxg:focus{box-shadow:inset 0 0 0 1px var(--violet)}

/* ---- tela ---- */
.bxg-screen{position:relative;width:100%;aspect-ratio:16/9;background:
  repeating-linear-gradient(45deg,#0c0a10 0 10px,#0e0c13 10px 20px);overflow:hidden;cursor:pointer}
.bxg-screen video{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;image-rendering:auto}
.bxg-screen.z11 video{object-fit:none;image-rendering:pixelated;cursor:grab}
.bxg-screen.z11.drag video{cursor:grabbing}
.bxg-corner{position:absolute;width:14px;height:14px;border-color:#ffffff55;border-style:solid;pointer-events:none}
.bxg-corner.a{top:8px;left:8px;border-width:2px 0 0 2px}.bxg-corner.b{top:8px;right:8px;border-width:2px 2px 0 0}
.bxg-corner.c{bottom:8px;left:8px;border-width:0 0 2px 2px}.bxg-corner.d{bottom:8px;right:8px;border-width:0 2px 2px 0}
.bxg-msg{position:absolute;inset:0;display:flex;flex-direction:column;gap:6px;align-items:center;justify-content:center;color:var(--mute);font-size:12px;text-align:center;padding:16px}
.bxg-msg .rune{font-size:26px;opacity:.6}
.bxg-flash{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);background:#000b;color:var(--amber2);
  font:700 13px/1 "JetBrains Mono",Consolas,monospace;padding:8px 12px;border-radius:999px;opacity:0;transition:opacity .25s;pointer-events:none}
.bxg-flash.on{opacity:1;transition:none}
.bxg-zoomtag{position:absolute;top:10px;left:50%;transform:translateX(-50%);background:var(--amber);color:#1a1205;
  font:700 10px/1 Consolas,monospace;padding:4px 7px;border-radius:3px;letter-spacing:1px;display:none}
.bxg-screen.z11 .bxg-zoomtag{display:block}

/* ---- slate (claquete) ---- */
.bxg-slate{display:grid;grid-template-columns:auto 1fr auto;align-items:stretch;border-top:1px solid var(--line);border-bottom:1px solid var(--line);background:var(--panel)}
.bxg-clap{width:10px;background:repeating-linear-gradient(-45deg,var(--amber) 0 6px,#1a1205 6px 12px)}
.bxg-cells{display:flex;flex-wrap:wrap;min-width:0}
.bxg-cell{flex:0 0 auto;padding:6px 12px;border-right:1px solid var(--line);display:flex;flex-direction:column;gap:3px;min-width:0}
.bxg-cell label{font:600 9px/1 Inter,sans-serif;letter-spacing:1.4px;text-transform:uppercase;color:var(--mute)}
.bxg-cell .v{font:700 18px/1 "JetBrains Mono",Consolas,monospace;color:var(--ink);white-space:nowrap}
.bxg-cell .v small{font-size:11px;color:var(--mute);font-weight:500;margin-left:4px}
.bxg-cell.frame .v{color:var(--amber2)}
.bxg-cell.frame input{width:78px;background:transparent;border:0;border-bottom:1px dashed #ffffff22;color:var(--amber2);
  font:700 18px/1 "JetBrains Mono",Consolas,monospace;padding:0;outline:none;-moz-appearance:textfield}
.bxg-cell.frame input:focus{border-bottom-color:var(--amber)}
.bxg-cell.frame input::-webkit-inner-spin-button{display:none}
.bxg-cell.tc .v{font-size:14px;padding-top:3px}
.bxg-seed{align-self:center;margin:0 10px;display:flex;flex-direction:column;align-items:flex-end;gap:3px;cursor:pointer;background:none;border:0;color:inherit;padding:4px 0}
.bxg-seed label{font:600 9px/1 Inter,sans-serif;letter-spacing:1.4px;text-transform:uppercase;color:var(--mute);cursor:pointer}
.bxg-seed .v{font:700 13px/1 "JetBrains Mono",Consolas,monospace;color:var(--ink);background:#8b5cf622;border:1px solid #8b5cf666;padding:4px 7px;border-radius:4px}
.bxg-seed:hover .v{background:#8b5cf644;border-color:var(--violet)}
.bxg-seed.ok .v{background:#22c55e33;border-color:#22c55e;color:#bbf7d0}

/* ---- regua de frames ---- */
.bxg-ruler{position:relative;height:38px;background:var(--bg);cursor:crosshair;touch-action:none;user-select:none;margin:0 10px}
.bxg-ruler canvas{position:absolute;inset:0;width:100%;height:100%}
.bxg-ph{position:absolute;top:0;bottom:0;width:0;pointer-events:none}
.bxg-ph::before{content:"";position:absolute;top:0;left:-6px;border:6px solid transparent;border-top:8px solid var(--amber)}
.bxg-ph::after{content:"";position:absolute;top:6px;bottom:0;left:-1px;width:2px;background:var(--amber);box-shadow:0 0 8px #f2b54488}
.bxg-peek{position:absolute;bottom:42px;transform:translateX(-50%);display:none;flex-direction:column;align-items:center;gap:3px;pointer-events:none;z-index:3}
.bxg-peek .img{border:1px solid var(--amber);border-radius:3px;background-repeat:no-repeat;background-color:#000;box-shadow:0 4px 14px #000a}
.bxg-peek .n{font:700 10px/1 Consolas,monospace;background:var(--amber);color:#1a1205;padding:3px 5px;border-radius:3px}
.bxg-ruler:hover .bxg-peek{display:flex}

/* ---- deck (controles) ---- */
.bxg-deck{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:6px 10px 8px;flex-wrap:wrap}
.bxg-trans{display:flex;align-items:center;background:var(--panel);border:1px solid var(--line);border-radius:999px;padding:3px}
.bxg-trans button{width:30px;height:28px;border:0;background:none;color:var(--ink);border-radius:999px;cursor:pointer;display:inline-flex;align-items:center;justify-content:center}
.bxg-trans button:hover{background:#ffffff12}
.bxg-trans button.play{width:40px;background:var(--amber);color:#1a1205}
.bxg-trans button.play:hover{background:var(--amber2)}
.bxg-trans svg{width:15px;height:15px;stroke:currentColor;fill:none;stroke-width:2.2;stroke-linecap:round;stroke-linejoin:round}
.bxg-trans button.play svg{fill:currentColor;stroke-width:1.5}
.bxg-tools{display:flex;gap:4px;flex-wrap:wrap}
.bxg-chip{height:26px;padding:0 9px;border:1px solid var(--line);background:none;color:var(--mute);border-radius:5px;cursor:pointer;
  font:700 10px/1 Inter,sans-serif;letter-spacing:1px;text-transform:uppercase;display:inline-flex;align-items:center;gap:5px}
.bxg-chip:hover{color:var(--ink);border-color:#4a4258}
.bxg-chip.on{color:var(--amber2);border-color:#f2b54488;background:#f2b54414}
.bxg-chip.mk{color:var(--mark)}
.bxg-chip svg{width:13px;height:13px;stroke:currentColor;fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}

/* ---- comparar com o bruto ---- */
.bxg-layers{position:absolute;inset:0;isolation:isolate}
.bxg-layers video{position:absolute;top:0;bottom:0;left:0;width:100%;height:100%;object-fit:contain}
.bxg-layers video.bruto{display:none;pointer-events:none}
.bxg-screen[data-cmp="wipe"] video.bruto,.bxg-screen[data-cmp="diff"] video.bruto,.bxg-screen[data-cmp="side"] video.bruto,.bxg-screen.showb video.bruto{display:block}
.bxg-screen[data-cmp="diff"] video.bruto{mix-blend-mode:difference}
.bxg-screen.showb video.bruto{clip-path:none!important;mix-blend-mode:normal!important;left:0!important;width:100%!important}
.bxg-screen[data-cmp="side"] video{width:50%}
.bxg-screen[data-cmp="side"].showb video.render{visibility:hidden}
.bxg-div{position:absolute;top:0;bottom:0;width:0;display:none;z-index:2}
.bxg-screen[data-cmp="wipe"]:not(.showb) .bxg-div{display:block}
.bxg-div::before{content:"";position:absolute;top:0;bottom:0;left:-1px;width:2px;background:var(--amber);box-shadow:0 0 0 1px #0006}
.bxg-grip{position:absolute;top:50%;left:-11px;width:22px;height:34px;margin-top:-17px;border-radius:11px;background:var(--amber);
  box-shadow:0 2px 8px #000a;cursor:ew-resize;display:flex;align-items:center;justify-content:center;color:#1a1205;font:700 11px/1 Inter,sans-serif}
.bxg-lab{position:absolute;top:10px;font:700 9px/1 Inter,sans-serif;letter-spacing:1.4px;padding:4px 6px;border-radius:3px;display:none;pointer-events:none;z-index:2}
.bxg-lab.b{background:#000b;color:#d8c8ff;border:1px solid #8b5cf6aa}
.bxg-lab.r{background:#000b;color:var(--amber2);border:1px solid #f2b54499}
.bxg-screen[data-cmp="wipe"] .bxg-lab,.bxg-screen[data-cmp="side"] .bxg-lab{display:block}
.bxg-screen.showb .bxg-lab.b{display:block;left:50%!important;right:auto!important;transform:translateX(-50%)}
.bxg-screen.showb .bxg-lab.r{display:none}
.bxg-screen[data-cmp="diff"] .bxg-lab.b{display:block;left:50%;transform:translateX(-50%)}
.bxg-cmp{display:none;align-items:center;gap:8px;padding:0 10px 2px;flex-wrap:wrap}
.bxg-cmp.has{display:flex}
.bxg-cmp > label{font:600 9px/1 Inter,sans-serif;letter-spacing:1.4px;text-transform:uppercase;color:var(--mute)}
.bxg-seg{display:inline-flex;border:1px solid var(--line);border-radius:6px;overflow:hidden}
.bxg-seg button{height:24px;padding:0 9px;border:0;border-right:1px solid var(--line);background:none;color:var(--mute);cursor:pointer;
  font:700 10px/1 Inter,sans-serif;letter-spacing:.8px;text-transform:uppercase}
.bxg-seg button:last-child{border-right:0}
.bxg-seg button:hover{color:var(--ink);background:#ffffff08}
.bxg-seg button.on{background:#8b5cf633;color:#e4d9ff}
.bxg-cmp .bxg-chip{height:24px}

/* ---- rodape ---- */
.bxg-foot{padding:7px 12px 9px;border-top:1px solid var(--line);font:11px/1.5 "JetBrains Mono",Consolas,monospace;color:var(--mute);word-break:break-all}
.bxg-tags{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:5px}
.bxg-tag{font:600 10px/1 Inter,sans-serif;padding:3px 6px;border-radius:3px;background:#ffffff0d;color:#c9c2d8}
.bxg-tag.cs{background:#8b5cf622;color:#d8c8ff}
.bxg-tag.gr{background:#f2b5441a;color:var(--amber2)}
.bxg-tag.warn{background:#ff6b8a22;color:#ffb3c2}
.bxg-foot .marks{color:var(--mark);margin-top:3px}
`;

const ICON = {
  in: '<path d="M6 4v16"/><path d="M10 12h10"/><path d="M16 8l4 4-4 4"/>',
  out: '<path d="M18 4v16"/><path d="M14 12H4"/><path d="M8 8l-4 4 4 4"/>',
  crop: '<path d="M6 2v14a2 2 0 0 0 2 2h14"/><path d="M18 22V8a2 2 0 0 0-2-2H2"/>',
  grid: '<rect x="3" y="3" width="18" height="18" rx="1"/><path d="M9 3v18M15 3v18M3 9h18M3 15h18"/>',
  eye: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  up: '<path d="M12 16V4"/><path d="M7 9l5-5 5 5"/><path d="M5 20h14"/>',
  lib: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  first: '<path d="M6 5v14"/><path d="M18 6l-8 6 8 6z"/>',
  prev: '<path d="M15 6l-6 6 6 6"/>',
  play: '<path d="M8 5l11 7-11 7z"/>',
  pause: '<path d="M8 5h3v14H8zM13 5h3v14h-3z"/>',
  next: '<path d="M9 6l6 6-6 6"/>',
  last: '<path d="M18 5v14"/><path d="M6 6l8 6-8 6z"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  png: '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-8 8"/>',
  full: '<path d="M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3"/>',
  flag: '<path d="M5 21V4"/><path d="M5 4h11l-2 4 2 4H5"/>',
};

function injectStyle() {
  if (document.getElementById(STYLE_ID)) return;
  const s = document.createElement("style");
  s.id = STYLE_ID;
  s.textContent = CSS;
  document.head.appendChild(s);
}

const el = (tag, cls, html) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
};
const svg = (name) => `<svg viewBox="0 0 24 24">${ICON[name]}</svg>`;

function viewUrl(ref) {
  if (!ref) return "";
  const q = new URLSearchParams({ filename: ref.filename, subfolder: ref.subfolder || "", type: ref.type || "temp" });
  return api.apiURL(`/view?${q.toString()}`);
}

function timecode(frame, fps) {
  const nom = Math.max(1, Math.round(fps));
  const p = (n) => String(n).padStart(2, "0");
  const s = Math.floor(frame / nom);
  return `${p(Math.floor(s / 3600))}:${p(Math.floor(s / 60) % 60)}:${p(s % 60)}:${p(frame % nom)}`;
}


export { CSS, ICON, injectStyle, el, svg, viewUrl, timecode };
