// Marga · página do PDF (Streamlit components v2, sem build).
// Desenha a página (PNG) com os trechos anotados e devolve cliques e retângulos em pontos PDF.
// Eventos (trigger values):
//   click -> {x, y, nonce}               um clique: ir à linha do fonte
//   rect  -> {x0, y0, x1, y1, nonce}     arrastar: selecionar o trecho dentro do retângulo
//   focus -> {id, nonce}                 clique na etiqueta de uma anotação

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" };
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ESC[c]);
let counter = 0;
const nonce = () => `${Date.now().toString(36)}-${(counter++).toString(36)}`;

function drawRects(root) {
  const st = root._mg;
  const d = st.data;
  const layer = root.querySelector(".mg-layer");
  const W = d.w || 1;
  const H = d.h || 1;
  // O servidor já recorta as marcas à página; o recorte aqui é só uma segunda garantia.
  const clamp = (value, max) => Math.min(Math.max(value, 0), max);
  layer.innerHTML = (d.rects || [])
    .filter((r) => clamp(r.x1, W) > clamp(r.x0, W) && clamp(r.y1, H) > clamp(r.y0, H))
    .map((r) => {
      const left = (clamp(r.x0, W) / W) * 100;
      const top = (clamp(r.y0, H) / H) * 100;
      const width = ((clamp(r.x1, W) - clamp(r.x0, W)) / W) * 100;
      const height = ((clamp(r.y1, H) - clamp(r.y0, H)) / H) * 100;
      const tag = r.label ? `<span class="mg-tag" data-id="${esc(r.id)}">${esc(r.label)}</span>` : "";
      return `<div class="mg-rect mg-${esc(r.kind || "a")}" style="left:${left}%;top:${top}%;width:${width}%;height:${height}%">${tag}</div>`;
    })
    .join("");
  root.querySelector(".mg-drag").style.display = "none";
}

// Tamanho da página: "width" ocupa a coluna; "page" cabe inteira na altura da tela; um número é a porcentagem do
// tamanho real (1 pt = 96/72 px), com rolagem própria quando a página passa da coluna. As marcas e os cliques
// usam o tamanho desenhado, então valem em qualquer tamanho.
function applySize(root) {
  const d = root._mg.data;
  const page = root.querySelector(".mg-page");
  const size = String(d.size || "width");
  const ratio = (d.w || 1) / (d.h || 1);
  const percent = /^\d+$/.test(size);
  root.classList.toggle("mg-zoomed", percent);
  if (size === "page") {
    page.style.width = `min(100%, calc((100vh - 9rem) * ${ratio}))`;
  } else if (percent) {
    page.style.width = `${(((d.w || 1) * 96) / 72) * (Number(size) / 100)}px`;
  } else {
    page.style.width = "100%";
  }
}

function bind(root) {
  const st = root._mg;
  const page = root.querySelector(".mg-page");
  const drag = root.querySelector(".mg-drag");
  // Arrastar para fora da página não leva a seleção junto: o ponto fica preso à borda.
  const toPoint = (ev) => {
    const box = page.getBoundingClientRect();
    const px = Math.min(Math.max(ev.clientX - box.left, 0), box.width);
    const py = Math.min(Math.max(ev.clientY - box.top, 0), box.height);
    return { px, py, x: (px / box.width) * st.data.w, y: (py / box.height) * st.data.h };
  };
  page.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0) return;
    const tag = ev.target.closest(".mg-tag");
    if (tag) {
      st.trigger("focus", { id: tag.dataset.id, nonce: nonce() });
      return;
    }
    ev.preventDefault();
    try {
      page.setPointerCapture(ev.pointerId);
    } catch {
      /* ponteiro sintético ou já capturado */
    }
    st.start = toPoint(ev);
    drag.style.display = "none";
  });
  page.addEventListener("pointermove", (ev) => {
    if (!st.start) return;
    const p = toPoint(ev);
    const s = st.start;
    const w = Math.abs(p.px - s.px);
    const h = Math.abs(p.py - s.py);
    if (w + h < 6) return;
    Object.assign(drag.style, {
      display: "block",
      left: `${Math.min(p.px, s.px)}px`,
      top: `${Math.min(p.py, s.py)}px`,
      width: `${w}px`,
      height: `${h}px`,
    });
  });
  const finish = (ev) => {
    if (!st.start) return;
    const p = toPoint(ev);
    const s = st.start;
    st.start = null;
    try {
      page.releasePointerCapture(ev.pointerId);
    } catch {
      /* já liberado */
    }
    if (Math.abs(p.px - s.px) + Math.abs(p.py - s.py) < 6) {
      drag.style.display = "none";
      st.trigger("click", { x: p.x, y: p.y, nonce: nonce() });
    } else {
      st.trigger("rect", {
        x0: Math.min(p.x, s.x),
        y0: Math.min(p.y, s.y),
        x1: Math.max(p.x, s.x),
        y1: Math.max(p.y, s.y),
        nonce: nonce(),
      });
    }
  };
  page.addEventListener("pointerup", finish);
  page.addEventListener("pointercancel", () => {
    st.start = null;
    drag.style.display = "none";
  });
}

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  let root = parentElement.querySelector(":scope > .mg-pdf");
  if (!root) {
    root = document.createElement("div");
    root.className = "mg-pdf";
    root.innerHTML =
      '<div class="mg-page"><img class="mg-img" alt="Página do PDF" draggable="false">' +
      '<div class="mg-layer"></div><div class="mg-drag"></div></div>';
    parentElement.appendChild(root);
    root._mg = { rev: null, imgKey: null, start: null };
    bind(root);
  }
  const st = root._mg;
  st.data = data || {};
  st.trigger = setTriggerValue;
  applySize(root);
  if (st.data.img_key !== st.imgKey) {
    root.querySelector(".mg-img").src = st.data.img || "";
    st.imgKey = st.data.img_key;
  }
  if (st.rev !== st.data.rev) {
    drawRects(root);
    st.rev = st.data.rev;
  }
}
