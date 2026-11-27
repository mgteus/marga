"""Alterações sem pedido: o que o agente mudou, da vN para a vN+1, fora das linhas pedidas na rodada.

Só se verificam as versões que respondem a uma rodada. **Linhas pedidas** são as linhas inteiras, na vN, dos
comentários da rodada que pedem edição e das pontas dos links de edição. Uma mudança é pedida quando todas as linhas
da vN que ela toca são pedidas; uma inserção, quando se prende a uma linha pedida (a mesma linha de um texto que já
existia ou, em linhas próprias, a linha vizinha, pulando linhas em branco). O trecho exato de um *não alterar*
continua protegido mesmo numa linha pedida. A comparação é palavra a palavra, dentro dos
blocos alterados, e ignora espaços e quebras de linha: reformatar um parágrafo não gera alerta.

Os alertas de cada versão ficam em ``.marga/docs/<doc>/unrequested.json``, gravados no registro da versão (ou na
primeira vez que se compara uma versão registrada antes deste recurso), junto com as decisões do usuário.
"""

from __future__ import annotations

import bisect
import difflib
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .anchors import anchor_from_offsets, anchor_offsets
from .diffing import changed_spans, line_opcodes
from .export import get_round
from .models import (
    ANNOTATION_TYPES,
    UNREQUESTED_STATUSES,
    Anchor,
    Document,
    RoundInfo,
    UnrequestedChange,
    UnrequestedCheck,
    Version,
    now_iso,
)
from .textmap import line_starts
from .versions import previous_version, version_texts
from .workspace import MargaError, Workspace, read_json, write_json

REQUESTING_TYPES = frozenset({"corrigir", "reescrever", "remover", "inserir", "erro_compilacao"})
PROTECTING_TYPE = "nao_alterar"
CONTEXT_RELATIONS = frozenset({"ver_tambem"})  # links que só dão contexto, sem pedir edição
MERGE_GAP = 2  # blocos alterados separados por até 2 linhas iguais viram um só (parágrafo reformatado)
MAX_TOKENS = 3000  # bloco maior que isso, num dos lados, vira uma mudança só, sem a comparação por palavra

_TOKEN = re.compile(r"\w+|[^\w\s]")
_LOCK = threading.RLock()


# ------------------------------------------------------------ regiões da rodada
@dataclass
class Region:
    """Trecho de uma anotação da rodada na vN e o papel dele na verificação."""

    ann: str
    type: str
    role: str  # pedido | protegido | contexto
    anchor: Anchor


def round_regions(ws: Workspace, doc_id: str, info: RoundInfo, n: int) -> list[Region]:
    """Regiões das anotações da rodada no documento, com as âncoras da vN (sem âncora na vN, a anotação não conta).

    Uma anotação pode ter dois papéis: o *não alterar* que é ponta de um link de edição pede as linhas dele, mas o
    trecho exato continua protegido.
    """
    included = set(info.annotations)
    linked: set[str] = set()
    for link in ws.links():
        if link.id in info.links and link.relation not in CONTEXT_RELATIONS:
            linked.update((link.source, link.target))
    regions: list[Region] = []
    for ann in ws.annotations(doc_id):
        anchor = ann.anchor_at(n)
        if ann.id not in included or anchor is None:
            continue
        protects = not ann.is_mark and ann.type == PROTECTING_TYPE
        if protects:
            regions.append(Region(ann.id, ann.type, "protegido", anchor))
        if ann.id in linked or (not ann.is_mark and ann.type in REQUESTING_TYPES):
            regions.append(Region(ann.id, ann.type, "pedido", anchor))
        elif not protects:
            regions.append(Region(ann.id, ann.type, "contexto", anchor))
    return regions


# --------------------------------------------------------------------- detecção
class _Text:
    """Um arquivo numa versão: os tokens sem espaço (palavras e pontuação) e o início de cada linha."""

    def __init__(self, text: str):
        self.text = text
        self.tokens = [m.span() for m in _TOKEN.finditer(text)]
        self._token_starts = [s for s, _ in self.tokens]
        self.starts = line_starts(text)

    def line(self, offset: int) -> int:
        """Linha (1-based) de um offset."""
        return bisect.bisect_right(self.starts, offset)

    def token_line(self, i: int) -> int:
        return self.line(self.tokens[i][0])

    def word(self, i: int) -> str:
        s, e = self.tokens[i]
        return self.text[s:e]

    def token_range(self, first: int, last: int) -> tuple[int, int]:
        """Tokens das linhas ``[first, last)``, contadas a partir de 0 (como em ``line_opcodes``)."""
        lo = self.starts[first] if first < len(self.starts) else len(self.text)
        hi = self.starts[last] if last < len(self.starts) else len(self.text)
        return bisect.bisect_left(self._token_starts, lo), bisect.bisect_left(self._token_starts, hi)

    def gap(self, i: int) -> tuple[int, int]:
        """O intervalo entre o token ``i - 1`` e o token ``i``: onde acontece uma inserção antes do token ``i``."""
        g0 = self.tokens[i - 1][1] if i > 0 else 0
        g1 = self.tokens[i][0] if i < len(self.tokens) else len(self.text)
        return g0, g1


@dataclass
class _Change:
    """Uma mudança: tokens ``[o1, o2)`` da vN viraram os tokens ``[n1, n2)`` da vN+1 (vazio = inserção/remoção)."""

    o1: int
    o2: int
    n1: int
    n2: int


@dataclass
class _Group:
    kind: str
    last: int  # última linha da vN tocada
    changes: list[_Change]
    related: list[Region]
    hits: list[Region] = field(default_factory=list)  # trechos protegidos atingidos


def _changes(old: _Text, new: _Text) -> list[_Change]:
    """Mudanças palavra a palavra dentro dos blocos alterados por linha (espaços e quebras de linha não contam)."""
    blocks: list[list[int]] = []
    for tag, i1, i2, j1, j2 in line_opcodes(old.text, new.text):
        if tag == "equal":
            continue
        if blocks and i1 - blocks[-1][1] <= MERGE_GAP:
            blocks[-1][1], blocks[-1][3] = i2, j2
        else:
            blocks.append([i1, i2, j1, j2])
    out: list[_Change] = []
    for i1, i2, j1, j2 in blocks:
        oa, ob = old.token_range(i1, i2)
        na, nb = new.token_range(j1, j2)
        a = [old.word(i) for i in range(oa, ob)]
        b = [new.word(j) for j in range(na, nb)]
        if a == b:
            continue  # só espaços e quebras de linha mudaram
        if max(len(a), len(b)) > MAX_TOKENS:
            out.append(_Change(oa, ob, na, nb))
            continue
        for tag, x1, x2, y1, y2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            if tag != "equal":
                out.append(_Change(oa + x1, oa + x2, na + y1, na + y2))
    return out


def _protected_spans(old: _Text, regions: list[Region]) -> list[tuple[int, int, Region]]:
    """Trechos exatos dos *não alterar* na vN, sem os espaços das pontas."""
    spans = []
    for region in regions:
        if region.role != "protegido":
            continue
        s, e = anchor_offsets(old.text, region.anchor)
        quote = old.text[s:e]
        s, e = s + len(quote) - len(quote.lstrip()), e - (len(quote) - len(quote.rstrip()))
        if s < e:
            spans.append((s, e, region))
    return spans


def _requested_lines(regions: list[Region]) -> set[int]:
    lines: set[int] = set()
    for region in regions:
        if region.role == "pedido":
            lines.update(range(region.anchor.start_line, region.anchor.end_line + 1))
    return lines


def _sort_key(ann_id: str) -> tuple[str, int]:
    return ann_id[:1], int(ann_id[1:]) if ann_id[1:].isdigit() else 0


def _join(ids: list[str]) -> str:
    return ids[0] if len(ids) == 1 else ", ".join(ids[:-1]) + " e " + ids[-1]


def _unique(regions: list[Region], role: str | None = None) -> list[Region]:
    seen: dict[str, Region] = {}
    for region in regions:
        if role is None or region.role == role:
            seen.setdefault(region.ann, region)
    return sorted(seen.values(), key=lambda r: _sort_key(r.ann))


def _reason(kind: str, related: list[Region], hits: list[Region], round_n: int) -> str:
    if kind == "protegido":
        return f"trecho protegido por {_join([r.ann for r in _unique(hits)])} (não alterar)"
    if kind == "arquivo_novo":
        return f"arquivo criado na rodada {round_n} sem comentário que o peça"
    if kind == "arquivo_removido":
        return f"arquivo apagado na rodada {round_n} sem comentário que o peça"
    parts = []
    asked = _unique(related, "pedido")
    if asked:
        parts.append(f"vai além das linhas de {_join([r.ann for r in asked])}")
    near = _unique(related, "protegido")
    if near:
        parts.append(f"perto do trecho protegido por {_join([r.ann for r in near])}")
    for region in _unique(related, "contexto"):
        if region.type == "pergunta":
            parts.append(f"{region.ann} era uma pergunta: responder sem editar")
        elif region.type == "marca":
            parts.append(f"{region.ann} é só uma marca")
        else:
            parts.append(f"{region.ann} é só contexto ({ANNOTATION_TYPES.get(region.type, region.type).lower()})")
    return "; ".join(parts) or f"fora dos comentários da rodada {round_n}"


def _related_ids(*groups: list[Region]) -> list[str]:
    return sorted({r.ann for regions in groups for r in regions}, key=_sort_key)


def _widen(old: _Text, new: _Text, o1: int, o2: int, n1: int) -> tuple[int, int, int, int]:
    """Remoção pura: estende os dois lados até um token vizinho igual, para o comentário ter onde se ancorar.

    Usa o token anterior se ele estiver na mesma linha do trecho removido; senão, o seguinte.
    """
    has_prev = o1 > 0 and n1 > 0 and old.word(o1 - 1) == new.word(n1 - 1)
    has_next = o2 < len(old.tokens) and n1 < len(new.tokens) and old.word(o2) == new.word(n1)
    same_line = has_prev and old.token_line(o1 - 1) == old.token_line(o1)
    if has_prev and (same_line or not has_next):
        return o1 - 1, o2, n1 - 1, n1
    if has_next:
        return o1, o2 + 1, n1, n1 + 1
    return o1, o2, n1, n1


def _insertion_lines(old: _Text, new: _Text, ch: _Change) -> set[int]:
    """Linhas da vN a que uma inserção se prende.

    Texto inserido na mesma linha de um token que já existia é mudança dessa linha (a do token anterior ou a do
    seguinte). Texto em linhas próprias se prende às linhas vizinhas, atravessando as linhas em branco: um parágrafo
    novo logo depois do trecho de um *inserir* é pedido.
    """
    g0, g1 = old.gap(ch.o1)
    lines = set()
    if ch.n1 > 0 and new.token_line(ch.n1 - 1) == new.token_line(ch.n1):
        lines.add(old.line(g0))
    if ch.n2 < len(new.tokens) and new.token_line(ch.n2 - 1) == new.token_line(ch.n2):
        lines.add(old.line(g1))
    return lines or set(range(old.line(g0), old.line(g1) + 1))


def _anchor(t: _Text, path: str, version: int, i1: int, i2: int) -> Anchor:
    """Âncora dos tokens ``[i1, i2)``; vazios, um ponto logo depois do token anterior."""
    if i2 > i1:
        return anchor_from_offsets(t.text, path, version, t.tokens[i1][0], t.tokens[i2 - 1][1])
    point = t.tokens[i1 - 1][1] if i1 > 0 else 0
    return anchor_from_offsets(t.text, path, version, point, point)


def _file_alerts(path: str, old: _Text, new: _Text, regions: list[Region], previous: int, version: int,
                 round_n: int) -> list[UnrequestedChange]:
    requested = _requested_lines(regions)
    protected = _protected_spans(old, regions)
    out: list[UnrequestedChange] = []
    group: _Group | None = None

    def close() -> None:
        nonlocal group
        if group is None:
            return
        o1, o2 = group.changes[0].o1, group.changes[-1].o2
        n1, n2 = group.changes[0].n1, group.changes[-1].n2
        if n2 == n1 and o2 > o1:
            o1, o2, n1, n2 = _widen(old, new, o1, o2, n1)
        out.append(UnrequestedChange(
            n=0, kind=group.kind, file=path,
            old=_anchor(old, path, previous, o1, o2), new=_anchor(new, path, version, n1, n2),
            reason=_reason(group.kind, group.related, group.hits, round_n),
            related=_related_ids(group.related, group.hits),
        ))
        group = None

    for ch in _changes(old, new):
        if ch.o2 > ch.o1:  # troca ou remoção: pedida se todas as linhas com as palavras dela são pedidas
            lines = {old.token_line(i) for i in range(ch.o1, ch.o2)}
            s, e = old.tokens[ch.o1][0], old.tokens[ch.o2 - 1][1]
            covered = lines <= requested
        else:  # inserção: pedida se se prende a uma linha pedida
            s, e = old.gap(ch.o1)
            lines = _insertion_lines(old, new, ch)
            covered = bool(lines & requested)
        hits = [region for ps, pe, region in protected if ps < e and s < pe]
        if not hits and covered:
            close()
            continue
        kind = "protegido" if hits else "sem_pedido"
        first, last = min(lines), max(lines)
        related = [r for r in regions if r.anchor.start_line <= last and first <= r.anchor.end_line]
        if group is not None and group.kind == kind and first <= group.last + 1:
            group.changes.append(ch)
            group.last = max(group.last, last)
            group.related += related
            group.hits += hits
        else:
            close()
            group = _Group(kind, last, [ch], related, hits)
    close()
    return out


def detect(old_texts: dict[str, str], new_texts: dict[str, str], regions: list[Region], *, previous: int,
           version: int, round_n: int) -> list[UnrequestedChange]:
    """Alertas da vN (``previous``) para a vN+1 (``version``), numerados na ordem dos arquivos e das posições."""
    items: list[UnrequestedChange] = []
    for path in sorted(set(old_texts) | set(new_texts)):
        old_text, new_text = old_texts.get(path), new_texts.get(path)
        if old_text == new_text:
            continue
        mine = [r for r in regions if r.anchor.file == path]
        if old_text is None:
            items.append(UnrequestedChange(
                n=0, kind="arquivo_novo", file=path, old=None,
                new=anchor_from_offsets(new_text, path, version, 0, len(new_text)),
                reason=_reason("arquivo_novo", [], [], round_n),
            ))
            continue
        old = _Text(old_text)
        if new_text is None:
            if {old.token_line(i) for i in range(len(old.tokens))} <= _requested_lines(mine):
                continue  # um *remover* cobria o arquivo inteiro
            items.append(UnrequestedChange(
                n=0, kind="arquivo_removido", file=path,
                old=anchor_from_offsets(old_text, path, previous, 0, len(old_text)), new=None,
                reason=_reason("arquivo_removido", [], [], round_n), related=_related_ids(mine),
            ))
            continue
        items.extend(_file_alerts(path, old, _Text(new_text), mine, previous, version, round_n))
    for i, item in enumerate(items, 1):
        item.n = i
    return items


# -------------------------------------------------------------------- gravação
def _path(ws: Workspace, doc_id: str) -> Path:
    return ws.dir / "docs" / doc_id / "unrequested.json"


def load_checks(ws: Workspace, doc_id: str) -> dict[int, UnrequestedCheck]:
    """Verificações gravadas do documento, por versão verificada (vN+1)."""
    return {int(k): UnrequestedCheck.from_dict(v) for k, v in read_json(_path(ws, doc_id), {}).items()}


def get_check(ws: Workspace, doc_id: str, version_n: int) -> UnrequestedCheck | None:
    return load_checks(ws, doc_id).get(version_n)


def _store(ws: Workspace, doc_id: str, check: UnrequestedCheck, replace: bool = False) -> UnrequestedCheck:
    with _LOCK:
        path = _path(ws, doc_id)
        data = read_json(path, {})
        key = str(check.version)
        if key in data and not replace:
            return UnrequestedCheck.from_dict(data[key])  # outra execução gravou antes: vale a dela
        data[key] = check.to_dict()
        write_json(path, data)
    return check


def check_version(ws: Workspace, doc: Document, version: Version, old_texts: dict[str, str] | None = None,
                  new_texts: dict[str, str] | None = None) -> UnrequestedCheck | None:
    """Alertas da versão: os gravados ou, na primeira vez, verificados agora e gravados.

    ``None`` numa versão que não responde a uma rodada (a inicial e as manuais), ou cuja rodada não existe mais.
    Os textos da vN e da vN+1 podem vir prontos (``register_version`` já os tem).
    """
    if version.round is None:
        return None
    stored = get_check(ws, doc.id, version.n)
    if stored is not None:
        return stored
    prev = previous_version(ws, doc.id, version.n)
    if prev is None:
        return None
    try:
        info = get_round(ws, version.round)
    except MargaError:
        return None
    items = detect(
        old_texts if old_texts is not None else version_texts(ws, doc, prev),
        new_texts if new_texts is not None else version_texts(ws, doc, version),
        round_regions(ws, doc.id, info, prev.n), previous=prev.n, version=version.n, round_n=version.round,
    )
    return _store(ws, doc.id, UnrequestedCheck(version=version.n, previous=prev.n, round=version.round, items=items))


def decide(ws: Workspace, doc_id: str, version_n: int, ns: list[int], status: str,
           comment: str | None = None) -> list[UnrequestedChange]:
    """Grava a decisão sobre alertas da versão: ``aceita``, ``ignorada``, ``comentada`` ou ``pendente`` (reabrir)."""
    if status not in UNREQUESTED_STATUSES:
        raise MargaError(f"Decisão desconhecida: {status}")
    with _LOCK:
        check = get_check(ws, doc_id, version_n)
        if check is None:
            raise MargaError(f"A v{version_n} de {doc_id} não tem verificação de alterações sem pedido.")
        changed = []
        for n in ns:
            item = check.get(n)
            if item is None:
                raise MargaError(f"O alerta {n} da v{version_n} de {doc_id} não existe.")
            item.status = status
            item.comment = comment if status == "comentada" else None
            item.decided_at = None if status == "pendente" else now_iso()
            changed.append(item)
        _store(ws, doc_id, check, replace=True)
    return changed


# ------------------------------------------------------------ pedir para refazer
@dataclass
class CommentDraft:
    type: str
    body: str
    suggestion: str | None = None


HIGHLIGHT_LIMIT = 20_000  # caracteres das duas citações; acima disso, o cartão mostra o trecho sem destaque


def highlights(item: UnrequestedChange) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Palavras que mudaram dentro das citações da vN e da vN+1 (offsets em cada citação), sem os espaços."""
    if item.old is None or item.new is None or len(item.old.quote) + len(item.new.quote) > HIGHLIGHT_LIMIT:
        return [], []
    spans_old, spans_new = changed_spans(item.old.quote, item.new.quote)

    def words(text: str, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
        out = []
        for s, e in spans:
            chunk = text[s:e]
            s, e = s + len(chunk) - len(chunk.lstrip()), e - (len(chunk) - len(chunk.rstrip()))
            if s < e:
                out.append((s, e))
        return out

    return words(item.old.quote, spans_old), words(item.new.quote, spans_new)


def _is_removal(item: UnrequestedChange) -> bool:
    """Remoção pura: as palavras da vN+1 são só o começo ou o fim das da vN (o vizinho da extensão, ou nada)."""
    a = _TOKEN.findall(item.old.quote) if item.old else []
    b = _TOKEN.findall(item.new.quote) if item.new else []
    return len(b) < len(a) and (a[: len(b)] == b or a[len(a) - len(b):] == b)


def draft_comment(check: UnrequestedCheck, item: UnrequestedChange) -> CommentDraft | None:
    """Rascunho do "Pedir para refazer": tipo, instrução e sugestão (o texto da vN). ``None`` sem onde ancorar."""
    if item.new is None:
        return None  # arquivo apagado: não há trecho na versão nova
    step = f"na rodada {check.round} (v{check.previous} → v{check.version})"
    if item.old is None:
        return CommentDraft("remover", f"O arquivo {item.file} foi criado sem pedido {step}: apague-o e desfaça as "
                                       "referências a ele.")
    if not item.old.quote:
        draft = CommentDraft("remover", f"Trecho inserido sem pedido {step}: remova-o.")
    elif _is_removal(item):
        draft = CommentDraft("corrigir", f"Trecho removido sem pedido {step}: restaure-o.", item.old.quote)
    else:
        draft = CommentDraft("corrigir", f"Alteração sem pedido {step}: volte ao texto da v{check.previous}.",
                             item.old.quote)
    if item.kind == "protegido":
        draft.body = f"Este trecho estava {item.reason.removeprefix('trecho ')}. {draft.body}"
    return draft
