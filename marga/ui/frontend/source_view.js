// Marga · visualização do fonte (Streamlit components v2, sem build).
// Mostra as linhas com diff e anotações e devolve seleções com precisão de caractere.
// Eventos enviados ao Python (trigger values):
//   select -> {sl, sc, el, ec, nonce}   linhas 1-based, colunas 0-based em code points
//   focus  -> {id, nonce}               clique numa caixa de comentário ou trecho anotado

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" };
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ESC[c]);
let counter = 0;
const nonce = () => `${Date.now().toString(36)}-${(counter++).toString(36)}`;

function push(map, key, value) {
  const list = map.get(key);
  if (list) list.push(value);
  else map.set(key, [value]);
}

function segments(text, marks, spans) {
  const cps = Array.from(text);
  const L = cps.length;
  const iv = [];
  for (const m of marks || []) {
    const s = Math.max(0, Math.min(L, m.s));
    const e = m.e === Infinity ? L : Math.max(0, Math.min(L, m.e));
    if (e > s) iv.push({ s, e, cls: m.cls, id: m.id });
  }
  for (const [s0, e0] of spans || []) {
    const s = Math.max(0, s0);
    const e = Math.min(L, e0);
    if (e > s) iv.push({ s, e, cls: "mg-chg" });
  }
  if (!iv.length) return esc(text);
  const cuts = new Set([0, L]);
  for (const x of iv) {
    cuts.add(x.s);
    cuts.add(x.e);
  }
  const bounds = [...cuts].sort((p, q) => p - q);
  let out = "";
  for (let i = 0; i < bounds.length - 1; i++) {
    const s = bounds[i];
    const e = bounds[i + 1];
    if (e <= s) continue;
    const seg = esc(cps.slice(s, e).join(""));
    const cover = iv.filter((x) => x.s <= s && x.e >= e);
    if (!cover.length) {
      out += seg;
      continue;
    }
    const cls = [...new Set(cover.map((x) => x.cls))].join(" ");
    const ids = [...new Set(cover.filter((x) => x.id).map((x) => x.id))];
    out += `<mark class="${cls}"${ids.length ? ` data-ids="${esc(ids.join(" "))}"` : ""}>${seg}</mark>`;
  }
  return out;
}

function threadHtml(a, focused) {
  const kind = a.kind === "mark" ? "m" : "c";
  const meta = [a.label, a.status !== "aberto" ? a.status : null, a.state].filter(Boolean).map(esc).join(" · ");
  const body = a.body ? `<div class="mg-body">${esc(a.body)}</div>` : "";
  const links = (a.links || []).map((l) => `<span class="mg-link">↔ ${esc(l)}</span>`).join("");
  const agent = a.agent ? `<div class="mg-agent">agente: ${esc(a.agent)}</div>` : "";
  return (
    `<div class="mg-thread mg-t-${kind}${focused ? " mg-focused" : ""}" data-id="${esc(a.id)}">` +
    `<span class="mg-badge">${esc(a.id)}</span><span class="mg-meta">${meta}</span>${body}${links}${agent}</div>`
  );
}

function render(root) {
  const st = root._mg;
  const d = st.data;
  const scroll = root.querySelector(".mg-scroll");
  const code = root.querySelector(".mg-code");
  const keep = scroll.scrollTop;
  scroll.style.height = `${d.height || 600}px`;
  st.lens = new Map();
  const marks = new Map();
  const threads = new Map();
  const gutter = new Map();
  for (const a of d.anns || []) {
    const cls = a.kind === "mark" ? "mg-m" : a.status === "aberto" ? "mg-a" : "mg-a-closed";
    for (let ln = a.sl; ln <= a.el; ln++) {
      push(marks, ln, { s: ln === a.sl ? a.sc : 0, e: ln === a.el ? a.ec : Infinity, cls, id: a.id });
      if (!gutter.has(ln)) gutter.set(ln, a.kind === "mark" ? "mg-gm" : "mg-ga");
    }
    push(threads, a.el, a);
  }
  const sel = d.sel;
  if (sel) {
    for (let ln = sel.sl; ln <= sel.el; ln++) {
      push(marks, ln, { s: ln === sel.sl ? sel.sc : 0, e: ln === sel.el ? sel.ec : Infinity, cls: "mg-sel" });
      gutter.set(ln, "mg-gs");
    }
  }
  const parts = [];
  for (const [kind, n, text, spans] of d.rows || []) {
    if (n) st.lens.set(n, Array.from(text).length);
    const rowCls = kind === "a" ? " mg-add" : kind === "d" ? " mg-del" : "";
    const g = n && gutter.has(n) ? ` ${gutter.get(n)}` : "";
    parts.push(
      `<div class="mg-row${rowCls}${g}"${n ? ` data-line="${n}"` : ""}>` +
        `<span class="mg-no">${n ?? ""}</span>` +
        `<span class="mg-tx">${segments(text, n ? marks.get(n) : null, spans)}</span></div>`
    );
    if (n && d.threads && threads.has(n)) {
      for (const a of threads.get(n)) parts.push(threadHtml(a, a.id === d.focused));
    }
  }
  if (!parts.length) parts.push(`<div class="mg-empty">${esc(d.empty || "Arquivo vazio.")}</div>`);
  code.innerHTML = parts.join("");
  scroll.scrollTop = keep;
}

function realRow(row, forward) {
  let cur = row;
  while (cur) {
    cur = forward ? cur.nextElementSibling : cur.previousElementSibling;
    if (cur && cur.classList.contains("mg-row") && cur.dataset.line) return cur;
  }
  return null;
}

function locate(root, node, offset, isStart) {
  const st = root._mg;
  const code = root.querySelector(".mg-code");
  const lenOf = (line) => st.lens.get(line) ?? 0;
  let el = node.nodeType === 3 ? node.parentElement : node;
  if (el === code) {
    const child = code.children[Math.min(offset, code.children.length - 1)];
    if (!child) return null;
    el = isStart ? child : realRow(child, false) || child;
    if (!isStart && el.dataset.line) return { line: +el.dataset.line, col: lenOf(+el.dataset.line) };
  }
  const thread = el.closest(".mg-thread");
  if (thread) {
    const row = realRow(thread, false);
    if (!row) return null;
    const line = +row.dataset.line;
    return { line, col: lenOf(line) };
  }
  const row = el.closest(".mg-row");
  if (!row) return null;
  if (!row.dataset.line) {
    const real = realRow(row, isStart);
    if (!real) return null;
    const line = +real.dataset.line;
    return { line, col: isStart ? 0 : lenOf(line) };
  }
  const line = +row.dataset.line;
  const tx = row.querySelector(".mg-tx");
  if (node === row) return { line, col: offset === 0 ? 0 : lenOf(line) };
  if (!tx.contains(node)) return { line, col: isStart ? 0 : lenOf(line) };
  const range = document.createRange();
  range.setStart(tx, 0);
  try {
    range.setEnd(node, offset);
  } catch {
    return { line, col: 0 };
  }
  return { line, col: Array.from(range.toString()).length };
}

function emitSelect(root, a, b) {
  const st = root._mg;
  if (a.line > b.line || (a.line === b.line && a.col > b.col)) [a, b] = [b, a];
  if (a.line === b.line && a.col === b.col) return;
  st.justSelected = true;
  const sel = { sl: a.line, sc: a.col, el: b.line, ec: b.col };
  st.data = { ...st.data, sel };
  render(root);
  st.rev = null;
  st.trigger("select", { ...sel, nonce: nonce() });
}

function bind(root) {
  const st = root._mg;
  const code = root.querySelector(".mg-code");
  code.addEventListener("mousedown", (ev) => {
    if (ev.button !== 0) return;
    const no = ev.target.closest(".mg-no");
    if (!no || !no.parentElement.dataset.line) return;
    ev.preventDefault();
    st.gutterStart = +no.parentElement.dataset.line;
    st.gutterShift = ev.shiftKey;
  });
  code.addEventListener("mouseup", (ev) => {
    if (ev.button !== 0) return;
    if (st.gutterStart != null) {
      let end = st.gutterStart;
      const row = ev.target.closest(".mg-row");
      if (row && row.dataset.line) end = +row.dataset.line;
      let a = Math.min(st.gutterStart, end);
      let b = Math.max(st.gutterStart, end);
      if (st.gutterShift && st.data.sel) {
        a = Math.min(a, st.data.sel.sl);
        b = Math.max(b, st.data.sel.el);
      }
      st.gutterStart = null;
      emitSelect(root, { line: a, col: 0 }, { line: b, col: st.lens.get(b) ?? 0 });
      return;
    }
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return;
    const range = sel.getRangeAt(0);
    if (!code.contains(range.startContainer) || !code.contains(range.endContainer)) return;
    const a = locate(root, range.startContainer, range.startOffset, true);
    const b = locate(root, range.endContainer, range.endOffset, false);
    if (!a || !b) return;
    sel.removeAllRanges();
    emitSelect(root, a, b);
  });
  code.addEventListener("click", (ev) => {
    if (st.justSelected) {
      st.justSelected = false;
      return;
    }
    const thread = ev.target.closest(".mg-thread");
    const mark = thread ? null : ev.target.closest("mark[data-ids]");
    const id = thread ? thread.dataset.id : mark ? mark.dataset.ids.split(" ")[0] : null;
    if (id) st.trigger("focus", { id, nonce: nonce() });
  });
}

function applyFocus(root) {
  const st = root._mg;
  const f = st.data.focus;
  if (!f || !f.nonce || f.nonce === st.focusNonce) return;
  st.focusNonce = f.nonce;
  const row = root.querySelector(`.mg-row[data-line="${Number(f.line)}"]`);
  if (!row) return;
  const scroll = root.querySelector(".mg-scroll");
  scroll.scrollTo({ top: Math.max(0, row.offsetTop - scroll.clientHeight / 3), behavior: "smooth" });
  row.classList.add("mg-flash");
  setTimeout(() => row.classList.remove("mg-flash"), 1600);
}

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  let root = parentElement.querySelector(":scope > .mg-src");
  if (!root) {
    root = document.createElement("div");
    root.className = "mg-src";
    root.innerHTML = '<div class="mg-scroll"><div class="mg-code"></div></div>';
    parentElement.appendChild(root);
    root._mg = { rev: null, focusNonce: null, gutterStart: null, justSelected: false, lens: new Map() };
    bind(root);
  }
  const st = root._mg;
  st.data = data || {};
  st.trigger = setTriggerValue;
  if (st.rev !== st.data.rev) {
    render(root);
    st.rev = st.data.rev;
  }
  applyFocus(root);
}
