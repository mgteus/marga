"""Rodadas: pacote para o agente (JSON, Markdown, HTML) e importação da resposta dele."""

from __future__ import annotations

import html
import json
import re
import textwrap
from pathlib import Path

from .diffing import changes, unified
from .latex import load_result
from .models import AGENT_STATUSES, REGION_STATES, Annotation, Document, Link, Reply, RoundInfo, now_iso
from .textmap import excerpt
from .versions import doc_status, latest_version, previous_version, version_texts
from .workspace import MargaError, Workspace, read_json, write_json, write_text

BUNDLE_SCHEMA = "marga.bundle/v1"
RESPONSE_SCHEMA = "marga.response/v1"
MAX_DIFF_LINES = 400

RULES = [
    "Edite os arquivos direto no disco, nos caminhos listados em Onde editar; os caminhos do pacote são "
    "relativos ao workspace. O Marga só enxerga mudanças nesses caminhos: não gere .zip, não edite outra cópia "
    "do projeto (Overleaf, Downloads, um clone) e não renomeie nem mova arquivos.",
    "Mude só o necessário para atender cada comentário: não reformate, não reordene e não reescreva o resto.",
    "Os números de linha valem para a versão indicada, que é exatamente o que está no disco agora. Se as "
    "linhas mudarem enquanto você edita, localize o trecho pela citação exata.",
    "Tipos: corrigir, reescrever, remover e inserir pedem edição; pergunta pede uma resposta na nota, sem "
    "editar; não alterar protege o trecho; nota é contexto; erro de compilação pede a correção que faz o "
    "documento compilar.",
    "Quando houver sugestão, aplique-a literalmente, ajustando só o necessário para o documento continuar válido.",
    "Links rastreiam trechos entre arquivos. consistente: no fim, os dois trechos afirmam a mesma coisa. "
    "propagar: aplique no destino a mesma mudança feita na origem. origem: o destino deriva da origem; "
    "revise-o se a origem mudar. mover: leve o conteúdo da origem para o destino. contradiz: resolva a "
    "contradição. ver também: só contexto.",
    "Mantenha o LaTeX compilável: ambientes balanceados, comandos e referências existentes.",
    "PDF avulso não tem fonte para editar: devolva o texto corrigido no campo substituicao da resposta.",
    "Não altere nada dentro de .marga/ além do response.json desta rodada.",
]

AGENT_GUIDE = """# Marga · guia para agentes

Este workspace é revisado com o Marga. Cada rodada de revisão fica em `.marga/rounds/NNN/`:

- `bundle.md`: instruções em Markdown (comece por aqui);
- `bundle.json`: os mesmos dados, estruturados (schema `marga.bundle/v1`);
- `bundle.html`: relatório para leitura humana;
- `response.json`: escrito por você ao terminar a rodada.

A rodada mais recente também está em `.marga/rounds/latest.md` e `.marga/rounds/latest.json`.

Fluxo: leia o pacote, edite os arquivos direto no disco e escreva o `response.json`. O Marga detecta a
mudança no disco, registra a nova versão, re-ancora os comentários e compara com a versão anterior.

Onde editar: nos caminhos da seção *Onde editar* do pacote, dentro deste workspace. Projetos importados de um
`.zip` (do Overleaf, por exemplo) viram uma cópia em `uploads/<nome>/`, e é essa cópia que se edita. Não gere
`.zip` nem aplique as mudanças em outra cópia do projeto, como o Overleaf: o Marga só enxerga o disco.
"""


# ---------------------------------------------------------------- rodadas
def rounds_dir(ws: Workspace) -> Path:
    return ws.dir / "rounds"


def round_dir(ws: Workspace, n: int) -> Path:
    return rounds_dir(ws) / f"{n:03d}"


def response_path(ws: Workspace, n: int) -> Path:
    return round_dir(ws, n) / "response.json"


def load_rounds(ws: Workspace) -> list[RoundInfo]:
    base = rounds_dir(ws)
    if not base.exists():
        return []
    rounds = [RoundInfo.from_dict(read_json(p, {})) for p in base.glob("[0-9][0-9][0-9]/round.json")]
    return sorted(rounds, key=lambda r: r.n)


def get_round(ws: Workspace, n: int) -> RoundInfo:
    data = read_json(round_dir(ws, n) / "round.json", None)
    if not data:
        raise MargaError(f"A rodada {n} não existe.")
    return RoundInfo.from_dict(data)


def save_round(ws: Workspace, info: RoundInfo) -> None:
    write_json(round_dir(ws, info.n) / "round.json", info.to_dict())


def pending_round_for(ws: Workspace, doc_id: str, version_n: int) -> RoundInfo | None:
    """A rodada mais recente que exportou esta versão do documento (a próxima versão é a resposta a ela)."""
    for info in reversed(load_rounds(ws)):
        if info.docs.get(doc_id) == version_n:
            return info
    return None


def check_ready(ws: Workspace) -> list[str]:
    problems: list[str] = []
    docs = ws.docs()
    if not docs:
        problems.append("Nenhum documento registrado.")
    for doc in docs:
        status = doc_status(ws, doc)
        if status.latest is None:
            problems.append(f"{doc.label}: nenhuma versão registrada.")
        elif status.dirty:
            problems.append(
                f"{doc.label}: {len(status.changed)} arquivo(s) mudaram no disco desde a v{status.latest.n}; "
                f"registre a v{status.latest.n + 1} antes de exportar."
            )
    return problems


# ----------------------------------------------------------------- pacote
def _short(text: str, limit: int = 120) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _link_refs(ann: Annotation, links: list[Link], by_id: dict[str, Annotation]) -> list[dict]:
    refs = []
    for link in links:
        if ann.id not in (link.source, link.target):
            continue
        outgoing = link.source == ann.id
        other = by_id.get(link.target if outgoing else link.source)
        if other is None:
            continue
        refs.append({
            "id": link.id,
            "direction": "saida" if outgoing else "entrada",
            "other": other.id,
            "other_doc": other.doc,
            "other_location": other.anchor.location,
            "other_quote": _short(other.anchor.quote),
            "relation": link.relation,
            "relation_label": link.relation_label,
            "note": link.note,
        })
    return refs


def _annotation_entry(ann: Annotation, texts: dict[str, str], latest_n: int | None, links: list[Link],
                      by_id: dict[str, Annotation], doc: Document) -> dict:
    anchor = ann.anchor
    stale = latest_n is not None and anchor.version != latest_n
    text = texts.get(anchor.file)
    rows = []
    if text is not None and not stale:
        rows = [{"n": n, "text": t, "anchored": inside}
                for n, t, inside in excerpt(text, anchor.start_line, anchor.end_line, context=2)]
    return {
        "id": ann.id,
        "kind": ann.kind,
        "type": ann.type,
        "type_label": ann.type_label,
        "doc": ann.doc,
        "file": anchor.file,
        "version": anchor.version,
        "lines": [anchor.start_line, anchor.end_line],
        "start": {"line": anchor.start_line, "col": anchor.start_col},
        "end": {"line": anchor.end_line, "col": anchor.end_col},
        "page": anchor.page,
        "quote": anchor.quote,
        "excerpt": rows,
        "body": ann.body,
        "suggestion": ann.suggestion,
        "status": ann.status,
        "region_state": "orfao" if stale else ann.region_state,
        "stale": stale,
        "rounds": ann.rounds,
        "editable": doc.kind != "pdf",
        "history": [
            {"author": r.author, "round": r.round, "status": r.agent_status, "text": r.text,
             "replacement": r.replacement, "at": r.created_at}
            for r in ann.thread
        ],
        "links": _link_refs(ann, links, by_id),
    }


def response_template(round_n: int, comment_ids: list[str]) -> dict:
    sample = {cid: {"status": "feito", "nota": "o que foi feito (ou a resposta, para perguntas)"}
              for cid in (comment_ids[:3] or ["c1"])}
    return {"schema": RESPONSE_SCHEMA, "round": round_n, "resumo": "o que mudou, em poucas linhas", "comments": sample}


def build_bundle(ws: Workspace, round_n: int, instructions: str = "", include_sources: bool = False) -> dict:
    docs = ws.docs()
    docs_by_id = {d.id: d for d in docs}
    anns = ws.annotations()
    by_id = {a.id: a for a in anns}
    links = ws.links()
    open_anns = [a for a in anns if a.is_open and a.doc in docs_by_id]
    included = {a.id for a in open_anns}

    texts: dict[str, dict[str, str]] = {}
    latest_n: dict[str, int] = {}
    doc_entries = []
    for doc in docs:
        latest = latest_version(ws, doc.id)
        if latest is None:
            continue
        current = version_texts(ws, doc, latest)
        texts[doc.id] = current
        latest_n[doc.id] = latest.n
        prev = previous_version(ws, doc.id, latest.n)
        entry = {
            "id": doc.id, "name": doc.name, "kind": doc.kind, "kind_label": doc.kind_label,
            "root": doc.root, "abs_root": str(ws.root / doc.root), "main": doc.main,
            "engine": doc.engine if doc.kind == "latex" else None,
            "version": latest.n, "version_origin": latest.origin,
            "previous_version": prev.n if prev else None,
            "files": sorted(latest.files), "editable": doc.kind != "pdf",
        }
        if doc.kind == "latex":
            res = load_result(ws, doc.id, latest.n)
            entry["compile"] = None if res is None else {
                "ok": res.ok,
                "errors": [{"file": e.file, "line": e.line, "message": e.message} for e in res.errors[:20]],
                "warnings": len(res.warnings),
                "finished_at": res.finished_at,
            }
        if prev is not None:
            old = version_texts(ws, doc, prev)
            file_changes = changes(old, current)
            diff = "\n".join(
                unified(old.get(c.path, ""), current.get(c.path, ""), f"{c.path} (v{prev.n})", f"{c.path} (v{latest.n})")
                for c in file_changes
            )
            lines = diff.split("\n") if diff else []
            entry["changes_since_previous"] = {
                "from": prev.n,
                "to": latest.n,
                "files": [vars(c) for c in file_changes],
                "diff": "\n".join(lines[:MAX_DIFF_LINES]),
                "truncated": len(lines) > MAX_DIFF_LINES,
            }
        if include_sources and doc.kind != "pdf":
            entry["sources"] = current
        doc_entries.append(entry)

    ann_entries = [
        _annotation_entry(a, texts.get(a.doc, {}), latest_n.get(a.doc), links, by_id, docs_by_id[a.doc])
        for a in open_anns
    ]
    link_entries = []
    for link in links:
        if link.source not in included or link.target not in included:
            continue
        src, dst = by_id[link.source], by_id[link.target]
        link_entries.append({
            "id": link.id, "source": src.id, "target": dst.id,
            "relation": link.relation, "relation_label": link.relation_label, "note": link.note,
            "source_doc": src.doc, "source_location": src.anchor.location, "source_quote": _short(src.anchor.quote),
            "target_doc": dst.doc, "target_location": dst.anchor.location, "target_quote": _short(dst.anchor.quote),
        })
    resolved = [
        {"id": a.id, "doc": a.doc, "location": a.anchor.location, "type_label": a.type_label, "body": a.body}
        for a in anns
        if a.status == "resolvido" and a.rounds and a.rounds[-1] == round_n - 1
    ]
    comment_ids = [a["id"] for a in ann_entries if a["kind"] == "comment"]
    return {
        "schema": BUNDLE_SCHEMA,
        "round": round_n,
        "created_at": now_iso(),
        "workspace": str(ws.root),
        "instructions": instructions.strip(),
        "rules": RULES,
        "response": {
            "path": f".marga/rounds/{round_n:03d}/response.json",
            "schema": RESPONSE_SCHEMA,
            "statuses": AGENT_STATUSES,
            "template": response_template(round_n, comment_ids),
        },
        "documents": doc_entries,
        "annotations": ann_entries,
        "links": link_entries,
        "recently_resolved": resolved,
        "counts": {
            "comments": len(comment_ids),
            "marks": sum(1 for a in ann_entries if a["kind"] == "mark"),
            "links": len(link_entries),
        },
    }


# --------------------------------------------------------------- Markdown
_LANGS = {".tex": "tex", ".sty": "tex", ".cls": "tex", ".bib": "bibtex", ".md": "markdown", ".markdown": "markdown"}


def _lang(path: str) -> str:
    return _LANGS.get(Path(path).suffix.lower(), "")


def _fence(body: str, lang: str = "") -> str:
    longest = max((len(m) for m in re.findall(r"`+", body)), default=0)
    tick = "`" * max(3, longest + 1)
    return f"{tick}{lang}\n{body}\n{tick}"


def _inline_code(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    tick = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{tick}{pad}{text}{pad}{tick}"


def _item(label: str, body: str) -> str:
    """Item de lista com corpo possivelmente multilinha (continuação indentada)."""
    lines = body.split("\n")
    first, rest = lines[0], lines[1:]
    out = f"- **{label}:** {first}"
    if rest:
        out += "\n" + textwrap.indent("\n".join(rest), "  ")
    return out


def _block_item(label: str, block: str) -> str:
    return f"- **{label}:**\n\n" + textwrap.indent(block, "  ")


def _lines_label(a: dict) -> str:
    first, last = a["lines"]
    return f"linha {first}" if first == last else f"linhas {first}–{last}"


def _md_annotation(a: dict) -> list[str]:
    head = f"##### {a['id']} · {a['type_label']} · {_lines_label(a)}"
    if a.get("page"):
        head += f" · página {a['page']}"
    parts = [head, ""]
    if a["stale"]:
        parts += ["> ⚠️ Trecho não encontrado na versão atual; a citação abaixo é de uma versão anterior.", ""]
    if a["excerpt"]:
        rows = [f"{'>' if r['anchored'] else ' '} {r['n']:>4} | {r['text']}" for r in a["excerpt"]]
        parts += [_fence("\n".join(rows), _lang(a["file"])), ""]
    items = []
    quote = a["quote"]
    if quote:
        if "\n" in quote or len(quote) > 160:
            items.append(_block_item("Trecho exato", _fence(quote, _lang(a["file"]))))
        else:
            items.append(f"- **Trecho exato:** {_inline_code(quote)}")
    if a["body"]:
        items.append(_item("Pedido" if a["kind"] == "comment" else "Nota", a["body"]))
    if a["suggestion"] is not None:
        diff = "\n".join([f"- {line}" for line in quote.split("\n")] + [f"+ {line}" for line in a["suggestion"].split("\n")])
        items.append(_block_item("Sugestão (substitui o trecho exato)", _fence(diff, "diff")))
    for lk in a["links"]:
        other = f"`{lk['other']}` ({lk['other_doc']} · `{lk['other_location']}`)"
        arrow = (f"este trecho → *{lk['relation_label']}* → {other}" if lk["direction"] == "saida"
                 else f"{other} → *{lk['relation_label']}* → este trecho")
        note = f" — {lk['note']}" if lk["note"] else ""
        items.append(f"- **Link {lk['id']}:** {arrow}{note}")
    if a["region_state"] in ("alterado", "movido"):
        items.append(f"- **Desde a versão anterior:** {REGION_STATES[a['region_state']]}")
    for h in a["history"]:
        who = "agente" if h["author"] == "agente" else "você"
        status = f" ({AGENT_STATUSES.get(h['status'], h['status'])})" if h.get("status") else ""
        rnd = f"rodada {h['round']} · " if h.get("round") else ""
        replacement = f" Substituição proposta: {_inline_code(h['replacement'])}" if h.get("replacement") else ""
        items.append(_item("Histórico", f"{rnd}{who}{status}: {h['text']}{replacement}"))
    return parts + items


def _mm(text: str) -> str:
    return text.replace('"', "'").replace("[", "(").replace("]", ")")


def _mermaid(b: dict) -> str:
    anns = {a["id"]: a for a in b["annotations"]}
    used = {lk["source"] for lk in b["links"]} | {lk["target"] for lk in b["links"]}
    lines = ["flowchart LR"]
    for doc in b["documents"]:
        members = [anns[i] for i in sorted(used) if i in anns and anns[i]["doc"] == doc["id"]]
        if not members:
            continue
        lines.append(f'  subgraph doc_{doc["id"]}["{doc["id"]} · {_mm(doc["name"])}"]')
        for a in members:
            lines.append(f'    {a["id"]}["{a["id"]} · {_mm(Path(a["file"]).name)}:{a["lines"][0]}"]')
        lines.append("  end")
    for lk in b["links"]:
        lines.append(f'  {lk["source"]} -->|{lk["relation"]}| {lk["target"]}')
    return "\n".join(lines)


PDF_NO_SOURCE = "PDF avulso, sem fonte para editar: devolva o texto corrigido em substituicao"


def _edit_targets(b: dict) -> list[tuple[dict, str]]:
    """Onde editar cada documento: o caminho absoluto (pasta com / no fim) ou o aviso do PDF avulso."""
    out = []
    for d in b["documents"]:
        where = d.get("abs_root") or f"{b['workspace']}/{d['root']}"
        if not d["editable"]:
            where = PDF_NO_SOURCE
        elif d["files"] != [d["root"]]:  # documento de pasta (os de um arquivo só têm o próprio arquivo)
            where = where.rstrip("/") + "/"
        out.append((d, where))
    return out


def render_markdown(b: dict) -> str:
    docs = {d["id"]: d for d in b["documents"]}
    comments = [a for a in b["annotations"] if a["kind"] == "comment"]
    marks = [a for a in b["annotations"] if a["kind"] == "mark"]
    out = [
        f"# Marga · rodada {b['round']}",
        "",
        f"Pacote de revisão gerado em {b['created_at']} para o workspace `{b['workspace']}`.",
        "",
        "Documentos: " + " · ".join(f"**{d['id']}** {d['name']} (v{d['version']})" for d in b["documents"]),
        "",
        f"Comentários abertos: {len(comments)} · marcas: {len(marks)} · links: {len(b['links'])}",
        "",
        "## Onde editar",
        "",
        "Edite os arquivos direto no disco. O Marga só enxerga mudanças nestes caminhos:",
        "",
    ]
    for d, where in _edit_targets(b):
        out.append(f"- **{d['id']}** · {d['name']} · {d['kind_label']}: " + (f"`{where}`" if d["editable"] else where))
    out += [
        "",
        "Não gere `.zip`, não edite outra cópia do projeto (Overleaf, Downloads, um clone) e não renomeie nem "
        "mova arquivos.",
        "",
        "## Como agir",
        "",
    ]
    out += [f"{i}. {rule}" for i, rule in enumerate(b["rules"], start=1)]
    out.append(f"{len(b['rules']) + 1}. Ao terminar, escreva `{b['response']['path']}` no formato da seção *Resposta*.")
    if b["instructions"]:
        out += ["", "## Instruções gerais", "", b["instructions"]]

    out += ["", "## Documentos", "", "| Doc | Nome | Tipo | Raiz | Principal | Versão | Compilação |",
            "|---|---|---|---|---|---|---|"]
    for d in b["documents"]:
        comp = "—"
        if d.get("compile"):
            comp = "✓ sem erros" if d["compile"]["ok"] else f"✗ {len(d['compile']['errors'])} erro(s)"
        elif d["kind"] == "latex":
            comp = "não compilado"
        version = f"v{d['version']}" + (f" (anterior: v{d['previous_version']})" if d["previous_version"] else "")
        main = f"`{d['main']}`" if d["main"] else "—"
        out.append(f"| {d['id']} | {d['name']} | {d['kind_label']} | `{d['root']}` | {main} | {version} | {comp} |")

    out += ["", "## Comentários"]
    if not comments:
        out += ["", "_Nenhum comentário aberto._"]
    for doc_id, d in docs.items():
        items = [a for a in comments if a["doc"] == doc_id]
        if not items:
            continue
        out += ["", f"### Documento {doc_id} · {d['name']} (v{d['version']})"]
        if not d["editable"]:
            out += ["", "_PDF avulso: não há fonte para editar; devolva o texto corrigido em `substituicao`._"]
        for file in sorted({a["file"] for a in items}):
            out += ["", f"#### `{file}`"]
            for a in sorted((x for x in items if x["file"] == file), key=lambda x: (x["lines"][0], x["id"])):
                out += ["", *_md_annotation(a)]

    if marks:
        out += ["", "## Marcas (trechos referenciados por links)", ""]
        for a in sorted(marks, key=lambda x: (x["doc"], x["file"], x["lines"][0])):
            note = f" — {a['body']}" if a["body"] else ""
            out.append(f"- `{a['id']}` · {a['doc']} · `{a['file']}:{a['lines'][0]}` · "
                       f"{_inline_code(_short(a['quote'], 100))}{note}")

    if b["links"]:
        out += ["", "## Rastreio entre arquivos", "", "| Link | Origem | Relação | Destino | Nota |", "|---|---|---|---|---|"]
        for lk in b["links"]:
            out.append(f"| {lk['id']} | `{lk['source']}` · {lk['source_doc']} `{lk['source_location']}` | "
                       f"{lk['relation_label']} | `{lk['target']}` · {lk['target_doc']} `{lk['target_location']}` | "
                       f"{lk['note'] or '—'} |")
        out += ["", _fence(_mermaid(b), "mermaid")]

    changed_docs = [d for d in b["documents"] if d.get("changes_since_previous") and d["changes_since_previous"]["files"]]
    if changed_docs:
        out += ["", "## Mudanças desde a versão anterior"]
        for d in changed_docs:
            ch = d["changes_since_previous"]
            added = sum(f["added"] for f in ch["files"])
            removed = sum(f["removed"] for f in ch["files"])
            out += ["", f"<details><summary>{d['id']} · {d['name']}: v{ch['from']} → v{ch['to']} · "
                        f"{len(ch['files'])} arquivo(s), +{added} −{removed}</summary>", "",
                    _fence(ch["diff"] + ("\n… (diff truncado)" if ch["truncated"] else ""), "diff"), "", "</details>"]

    if b["recently_resolved"]:
        out += ["", "## Resolvidos na rodada anterior (não desfazer)", ""]
        out += [f"- `{r['id']}` · {r['doc']} · `{r['location']}` · {r['type_label']}: {_short(r['body'], 140)}"
                for r in b["recently_resolved"]]

    sources = [d for d in b["documents"] if d.get("sources")]
    if sources:
        out += ["", "## Conteúdo completo dos arquivos"]
        for d in sources:
            for path, text in sorted(d["sources"].items()):
                out += ["", f"### `{path}` ({d['id']} v{d['version']})", "", _fence(text, _lang(path))]

    template = json.dumps(b["response"]["template"], ensure_ascii=False, indent=2)
    out += ["", "## Resposta", "",
            f"Ao terminar, escreva `{b['response']['path']}` (um item por comentário):", "",
            _fence(template, "json"), "",
            "Status aceitos: " + ", ".join(f"`{s}`" for s in b["response"]["statuses"]) + ". "
            "Use a nota para explicar o que mudou, responder perguntas ou dizer por que algo não foi feito. "
            "Para PDF avulso, inclua `\"substituicao\": \"texto corrigido\"`."]
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ HTML
_CSS = """
:root{--bg:#fff;--fg:#1f2328;--muted:#59636e;--border:#d1d9e0;--code:#f6f8fa;--hit:#fff1b8;--add:#dafbe1;--del:#ffebe9;--accent:#0969da}
@media (prefers-color-scheme: dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#9198a1;--border:#3d444d;--code:#151b23;--hit:#463a0f;--add:#12261e;--del:#3a1518;--accent:#4493f8}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1000px;margin:0 auto;padding:24px 16px 72px}
h1{font-size:1.7rem;margin:.2rem 0 .6rem}
h2{font-size:1.25rem;border-bottom:1px solid var(--border);padding-bottom:.3rem;margin-top:2.2rem}
h3{font-size:1.08rem;margin-top:1.6rem}
.meta{color:var(--muted);font-size:13.5px}
.card{border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin:12px 0}
.card h4{margin:0 0 .4rem;font-size:15px}
.badge{display:inline-block;font-size:12px;padding:1px 8px;border-radius:999px;border:1px solid var(--border);color:var(--muted);margin:0 4px 0 0}
pre{background:var(--code);border-radius:8px;padding:8px 10px;overflow:auto;font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;margin:.5rem 0}
pre > span{display:block;white-space:pre-wrap}
a{color:var(--accent)}
.ln{color:var(--muted)} .hit{background:var(--hit)} .add{background:var(--add)} .del{background:var(--del)}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.92em;background:var(--code);padding:0 4px;border-radius:4px}
table{border-collapse:collapse;width:100%;font-size:14px;margin:.6rem 0}
th,td{border:1px solid var(--border);padding:6px 8px;text-align:left;vertical-align:top}
dl{margin:.4rem 0} dt{font-weight:600;margin-top:.4rem} dd{margin:0 0 0 1rem}
ol li{margin:.25rem 0}
details summary{cursor:pointer;margin:.6rem 0}
"""


def _e(text) -> str:
    return html.escape(str(text if text is not None else ""))


def _html_lines(rows: list[tuple[str, str]]) -> str:
    return "<pre>" + "".join(f'<span class="{cls}">{body}</span>' for cls, body in rows) + "</pre>"


def _html_annotation(a: dict) -> str:
    badges = [a["type_label"], _lines_label(a)]
    if a.get("page"):
        badges.append(f"página {a['page']}")
    if a["region_state"]:
        badges.append(REGION_STATES.get(a["region_state"], a["region_state"]))
    out = [f'<div class="card" id="{_e(a["id"])}"><h4>{_e(a["id"])} · <code>{_e(a["file"])}</code></h4>',
           "<div>" + "".join(f'<span class="badge">{_e(x)}</span>' for x in badges) + "</div>"]
    if a["excerpt"]:
        out.append(_html_lines([
            ("hit" if r["anchored"] else "", f'<span class="ln">{r["n"]:>4} │ </span>{_e(r["text"])}')
            for r in a["excerpt"]
        ]))
    out.append("<dl>")
    if a["quote"]:
        out.append(f"<dt>Trecho exato</dt><dd><code>{_e(a['quote'])}</code></dd>")
    if a["body"]:
        label = "Pedido" if a["kind"] == "comment" else "Nota"
        out.append(f"<dt>{label}</dt><dd>{_e(a['body']).replace(chr(10), '<br>')}</dd>")
    if a["suggestion"] is not None:
        rows = [("del", "- " + _e(line)) for line in a["quote"].split("\n")]
        rows += [("add", "+ " + _e(line)) for line in a["suggestion"].split("\n")]
        out.append(f"<dt>Sugestão</dt><dd>{_html_lines(rows)}</dd>")
    for lk in a["links"]:
        other = f"<a href='#{_e(lk['other'])}'>{_e(lk['other'])}</a> ({_e(lk['other_doc'])} · <code>{_e(lk['other_location'])}</code>)"
        rel = f"<em>{_e(lk['relation_label'])}</em>"
        text = f"este trecho → {rel} → {other}" if lk["direction"] == "saida" else f"{other} → {rel} → este trecho"
        note = f" — {_e(lk['note'])}" if lk["note"] else ""
        out.append(f"<dt>Link {_e(lk['id'])}</dt><dd>{text}{note}</dd>")
    for h in a["history"]:
        who = "agente" if h["author"] == "agente" else "você"
        status = f" ({_e(AGENT_STATUSES.get(h['status'], h['status']))})" if h.get("status") else ""
        rnd = f"rodada {h['round']} · " if h.get("round") else ""
        out.append(f"<dt>Histórico</dt><dd>{rnd}{who}{status}: {_e(h['text'])}</dd>")
    out.append("</dl></div>")
    return "".join(out)


def render_html(b: dict) -> str:
    docs = {d["id"]: d for d in b["documents"]}
    comments = [a for a in b["annotations"] if a["kind"] == "comment"]
    marks = [a for a in b["annotations"] if a["kind"] == "mark"]
    out = [
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>Marga · rodada {b['round']}</title><style>{_CSS}</style></head><body><main>",
        f"<h1>Marga · rodada {b['round']}</h1>",
        f"<p class='meta'>Gerado em {_e(b['created_at'])} · workspace <code>{_e(b['workspace'])}</code><br>",
        f"{len(comments)} comentário(s) aberto(s) · {len(marks)} marca(s) · {len(b['links'])} link(s)</p>",
        "<h2>Onde editar</h2><p>Edite os arquivos direto no disco. O Marga só enxerga mudanças nestes caminhos:</p>"
        "<ul>" + "".join(
            f"<li><b>{_e(d['id'])}</b> · {_e(d['name'])} · {_e(d['kind_label'])}: "
            + (f"<code>{_e(where)}</code>" if d["editable"] else _e(where)) + "</li>"
            for d, where in _edit_targets(b)
        ) + "</ul><p>Não gere <code>.zip</code>, não edite outra cópia do projeto (Overleaf, Downloads, um clone) "
        "e não renomeie nem mova arquivos.</p>",
        "<h2>Como agir</h2><ol>" + "".join(f"<li>{_e(r)}</li>" for r in b["rules"]) + "</ol>",
    ]
    if b["instructions"]:
        out.append(f"<h2>Instruções gerais</h2><p>{_e(b['instructions']).replace(chr(10), '<br>')}</p>")
    out.append("<h2>Documentos</h2><table><tr><th>Doc</th><th>Nome</th><th>Tipo</th><th>Raiz</th>"
               "<th>Versão</th><th>Compilação</th></tr>")
    for d in b["documents"]:
        comp = "—"
        if d.get("compile"):
            comp = "✓ sem erros" if d["compile"]["ok"] else f"✗ {len(d['compile']['errors'])} erro(s)"
        elif d["kind"] == "latex":
            comp = "não compilado"
        prev = f" (anterior v{d['previous_version']})" if d["previous_version"] else ""
        out.append(f"<tr><td>{_e(d['id'])}</td><td>{_e(d['name'])}</td><td>{_e(d['kind_label'])}</td>"
                   f"<td><code>{_e(d['root'])}</code></td><td>v{d['version']}{prev}</td><td>{comp}</td></tr>")
    out.append("</table><h2>Comentários</h2>")
    if not comments:
        out.append("<p class='meta'>Nenhum comentário aberto.</p>")
    for doc_id, d in docs.items():
        items = sorted((a for a in comments if a["doc"] == doc_id), key=lambda x: (x["file"], x["lines"][0]))
        if items:
            out.append(f"<h3>Documento {_e(doc_id)} · {_e(d['name'])} (v{d['version']})</h3>")
            out += [_html_annotation(a) for a in items]
    if marks:
        out.append("<h2>Marcas</h2>")
        out += [_html_annotation(a) for a in marks]
    if b["links"]:
        out.append("<h2>Rastreio entre arquivos</h2><table><tr><th>Link</th><th>Origem</th><th>Relação</th>"
                   "<th>Destino</th><th>Nota</th></tr>")
        for lk in b["links"]:
            out.append(
                f"<tr><td>{_e(lk['id'])}</td>"
                f"<td><a href='#{_e(lk['source'])}'>{_e(lk['source'])}</a> · {_e(lk['source_doc'])} "
                f"<code>{_e(lk['source_location'])}</code><br><span class='meta'>{_e(lk['source_quote'])}</span></td>"
                f"<td>{_e(lk['relation_label'])}</td>"
                f"<td><a href='#{_e(lk['target'])}'>{_e(lk['target'])}</a> · {_e(lk['target_doc'])} "
                f"<code>{_e(lk['target_location'])}</code><br><span class='meta'>{_e(lk['target_quote'])}</span></td>"
                f"<td>{_e(lk['note']) or '—'}</td></tr>"
            )
        out.append("</table>")
    changed_docs = [d for d in b["documents"] if d.get("changes_since_previous") and d["changes_since_previous"]["files"]]
    if changed_docs:
        out.append("<h2>Mudanças desde a versão anterior</h2>")
        for d in changed_docs:
            ch = d["changes_since_previous"]
            rows = []
            for line in ch["diff"].split("\n"):
                cls = "add" if line.startswith("+") and not line.startswith("+++") else (
                    "del" if line.startswith("-") and not line.startswith("---") else "")
                rows.append((cls, _e(line)))
            out.append(f"<details><summary>{_e(d['id'])} · {_e(d['name'])}: v{ch['from']} → v{ch['to']}</summary>"
                       f"{_html_lines(rows)}</details>")
    if b["recently_resolved"]:
        out.append("<h2>Resolvidos na rodada anterior</h2><ul>")
        out += [f"<li><code>{_e(r['id'])}</code> · {_e(r['doc'])} · <code>{_e(r['location'])}</code> · {_e(r['body'])}</li>"
                for r in b["recently_resolved"]]
        out.append("</ul>")
    out.append(f"<h2>Resposta</h2><p>O agente escreve <code>{_e(b['response']['path'])}</code>:</p>")
    out.append(f"<pre>{_e(json.dumps(b['response']['template'], ensure_ascii=False, indent=2))}</pre>")
    out.append("</main></body></html>")
    return "\n".join(out)


# --------------------------------------------------------------- exportar
def agent_prompt(ws: Workspace, info: RoundInfo) -> str:
    """Prompt para colar no agente: onde está o pacote, onde editar e onde deixar a resposta."""
    rel = f".marga/rounds/{info.dirname}"
    docs = {d.id: d for d in ws.docs()}
    where = [f"- {docs[i].label}: {ws.root / docs[i].root}" for i in info.docs if i in docs and docs[i].kind != "pdf"]
    lines = [f"Estou revisando os documentos do workspace {ws.root} com o Marga. Leia {rel}/bundle.md e aplique os "
             "comentários seguindo as regras do pacote."]
    if where:
        lines += ["Edite os arquivos direto no disco, nestes caminhos:", *where,
                  "O Marga só enxerga mudanças nesses caminhos: não gere .zip, não edite outra cópia do projeto "
                  "(Overleaf, Downloads, um clone) e não renomeie arquivos."]
    lines.append(f"Ao terminar, escreva {rel}/response.json no formato descrito na seção Resposta do pacote.")
    return "\n".join(lines)


def export_round(ws: Workspace, instructions: str = "", include_sources: bool = False) -> RoundInfo:
    problems = check_ready(ws)
    if problems:
        raise MargaError("\n".join(problems))
    n = ws.next_counter("round")
    bundle = build_bundle(ws, n, instructions, include_sources)
    rdir = round_dir(ws, n)
    markdown, page = render_markdown(bundle), render_html(bundle)
    write_json(rdir / "bundle.json", bundle)
    write_text(rdir / "bundle.md", markdown)
    write_text(rdir / "bundle.html", page)
    write_json(rounds_dir(ws) / "latest.json", bundle)
    write_text(rounds_dir(ws) / "latest.md", markdown)
    write_text(ws.dir / "AGENT.md", AGENT_GUIDE)
    info = RoundInfo(
        n=n,
        created_at=bundle["created_at"],
        docs={d["id"]: d["version"] for d in bundle["documents"]},
        annotations=[a["id"] for a in bundle["annotations"]],
        links=[lk["id"] for lk in bundle["links"]],
        instructions=instructions.strip(),
    )
    save_round(ws, info)
    changed = []
    for ann in ws.annotations():
        if ann.id in info.annotations and n not in ann.rounds:
            ann.rounds.append(n)
            changed.append(ann)
    ws.save_annotations(changed)
    return info


def read_response(ws: Workspace, n: int) -> dict | None:
    path = response_path(ws, n)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MargaError(f"response.json da rodada {n} não é JSON válido: {exc}") from exc
    comments = data.get("comments", data.get("comentarios")) if isinstance(data, dict) else None
    if not isinstance(comments, dict):
        raise MargaError(f"response.json da rodada {n} precisa de um objeto 'comments' com um item por comentário.")
    return data


def import_response(ws: Workspace, n: int, only_if_present: bool = False) -> int:
    """Leva as notas do agente para o histórico dos comentários. Devolve quantas foram importadas."""
    info = get_round(ws, n)
    if info.response_imported:
        return 0
    data = read_response(ws, n)
    if data is None:
        if only_if_present:
            return 0
        raise MargaError(f"A rodada {n} ainda não tem response.json.")
    comments = data.get("comments", data.get("comentarios"))
    changed: list[Annotation] = []
    for ann_id, entry in comments.items():
        ann = ws.get_annotation(str(ann_id))
        if ann is None:
            continue
        if not isinstance(entry, dict):
            entry = {"nota": str(entry)}
        status = entry.get("status")
        ann.thread.append(Reply(
            author="agente",
            text=str(entry.get("nota") or entry.get("note") or ""),
            round=n,
            agent_status=status if status in AGENT_STATUSES else None,
            replacement=entry.get("substituicao") or entry.get("replacement"),
        ))
        changed.append(ann)
    ws.save_annotations(changed)
    info.response_imported = True
    info.summary = str(data.get("resumo") or data.get("summary") or "")
    save_round(ws, info)
    return len(changed)
