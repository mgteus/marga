// Marga · slide do PowerPoint desenhado em HTML (Streamlit components v2, sem build).
// O HTML do slide vem pronto do servidor (marga/core/slides.py); cada parágrafo tem data-line (linha do texto
// extraído), data-pre (tamanho do prefixo "- ", "# "… da linha) e data-lead (espaços do começo que o texto
// extraído não tem). Linhas de tabela têm data-row: valem a linha inteira.
// Eventos (trigger values):
//   select -> {sl, sc, el, ec, nonce}   trecho selecionado (ou o parágrafo clicado), em linha/coluna do texto
//   focus  -> {id, nonce}               clique na etiqueta de uma anotação

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" };
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ESC[c]);
let counter = 0;
const nonce = () => `${Date.now().toString(36)}-${(counter++).toString(36)}`;
const BIG = 1_000_000;

function textNodes(el) {
  const out = [];
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) out.push(walker.currentNode);
  return out;
}

// Posição (nó de texto, offset) do caractere k dentro do .mg-t.
function locate(tx, k) {
  let left = Math.max(0, k);
  const nodes = textNodes(tx);
  for (const node of nodes) {
    if (left <= node.length) return [node, left];
    left -= node.length;
  }
  const last = nodes[nodes.length - 1];
  return last ? [last, last.length] : [tx, 0];
}

// Ponto do DOM → {line, col} no texto extraído.
function linePos(node, offset) {
  const el = (node.nodeType === 1 ? node : node.parentElement)?.closest("[data-line]");
  if (!el) return null;
  const line = Number(el.dataset.line);
  if (el.dataset.row) return { line, col: 0, row: true };
  const pre = Number(el.dataset.pre || 0);
  const tx = el.querySelector(".mg-t");
  if (!tx || !tx.contains(node)) return { line, col: pre };
  const range = document.createRange();
  range.setStart(tx, 0);
  range.setEnd(node, offset);
  const k = range.toString().length - Number(el.dataset.lead || 0);
  return { line, col: pre + Math.max(0, k) };
}

function fit(root) {
  const st = root._mg;
  const frame = root.querySelector(".mg-frame");
  const stage = root.querySelector(".mg-stage");
  const w = st.data.w || 960;
  const h = st.data.h || 540;
  const scale = (frame.clientWidth || w) / w;
  stage.style.width = `${w}px`;
  stage.style.height = `${h}px`;
  stage.style.transform = `scale(${scale})`;
  frame.style.height = `${h * scale}px`;
  drawMarks(root);
}

function drawMarks(root) {
  const st = root._mg;
  const layer = root.querySelector(".mg-marks");
  const box = root.getBoundingClientRect();
  const html = [];
  for (const ann of st.data.anns || []) {
    const rects = [];
    for (let line = ann.sl; line <= ann.el; line++) {
      for (const el of root.querySelectorAll(`[data-line="${line}"]`)) {
        if (el.dataset.row) {
          rects.push(el.getBoundingClientRect());
          continue;
        }
        const tx = el.querySelector(".mg-t");
        if (!tx) continue;
        const pre = Number(el.dataset.pre || 0);
        const lead = Number(el.dataset.lead || 0);
        const total = tx.textContent.length;
        const a = line === ann.sl ? Math.max(0, ann.sc - pre) + lead : lead;
        const b = line === ann.el ? Math.max(0, ann.ec - pre) + lead : total;
        if (Math.min(b, total) <= Math.min(a, total)) continue;
        const range = document.createRange();
        range.setStart(...locate(tx, a));
        range.setEnd(...locate(tx, Math.min(b, total)));
        for (const r of range.getClientRects()) if (r.width > 0.5) rects.push(r);
      }
    }
    rects.forEach((r, i) => {
      const tag = i === 0 && ann.label ? `<span class="mg-tag" data-id="${esc(ann.id)}">${esc(ann.label)}</span>` : "";
      html.push(
        `<div class="mg-rect mg-${esc(ann.kind || "a")}" style="left:${r.left - box.left}px;top:${r.top - box.top}px;` +
          `width:${r.width}px;height:${r.height}px">${tag}</div>`,
      );
    });
  }
  layer.innerHTML = html.join("");
}

function bind(root) {
  const st = root._mg;
  root.addEventListener("pointerdown", (ev) => {
    const tag = ev.target.closest(".mg-tag");
    if (!tag) return;
    st.tagged = true;
    ev.preventDefault();
    st.trigger("focus", { id: tag.dataset.id, nonce: nonce() });
  });
  root.addEventListener("mouseup", (ev) => {
    if (st.tagged) {
      st.tagged = false;
      return;
    }
    const sel = window.getSelection();
    if (sel && !sel.isCollapsed && root.contains(sel.anchorNode) && root.contains(sel.focusNode)) {
      let a = linePos(sel.anchorNode, sel.anchorOffset);
      let b = linePos(sel.focusNode, sel.focusOffset);
      if (a && b) {
        if (a.line > b.line || (a.line === b.line && a.col > b.col)) [a, b] = [b, a];
        const sc = a.row ? 0 : a.col;
        const ec = b.row ? BIG : b.col;
        sel.removeAllRanges();
        if (a.line !== b.line || ec > sc) {
          st.trigger("select", { sl: a.line, sc, el: b.line, ec, nonce: nonce() });
          return;
        }
      }
    }
    const el = ev.target.closest("[data-line]");
    if (el) {
      const line = Number(el.dataset.line);
      const sc = el.dataset.row ? 0 : Number(el.dataset.pre || 0);
      st.trigger("select", { sl: line, sc, el: line, ec: BIG, nonce: nonce() });
    }
  });
}

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  let root = parentElement.querySelector(":scope > .mg-slides");
  if (!root) {
    root = document.createElement("div");
    root.className = "mg-slides";
    root.innerHTML =
      '<div class="mg-frame"><div class="mg-stage"></div></div><div class="mg-notes"></div><div class="mg-marks"></div>';
    parentElement.appendChild(root);
    root._mg = { rev: null, htmlKey: null, tagged: false };
    bind(root);
    new ResizeObserver(() => fit(root)).observe(root.querySelector(".mg-frame"));
    document.fonts?.ready?.then(() => drawMarks(root));
  }
  const st = root._mg;
  st.data = data || {};
  st.trigger = setTriggerValue;
  if (st.data.html_key !== st.htmlKey) {
    root.querySelector(".mg-stage").innerHTML = st.data.html || "";
    root.querySelector(".mg-notes").innerHTML = st.data.notes || "";
    root.querySelector(".mg-notes").style.display = st.data.notes ? "block" : "none";
    st.htmlKey = st.data.html_key;
    st.rev = null;
    fit(root);
  }
  if (st.rev !== st.data.rev) {
    drawMarks(root);
    st.rev = st.data.rev;
  }
}
