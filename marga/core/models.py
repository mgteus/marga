"""Modelos de dados do Marga (dataclasses serializáveis em JSON)."""

from __future__ import annotations

import datetime as _dt
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any

DOC_KINDS: dict[str, str] = {"latex": "LaTeX", "text": "Texto", "pdf": "PDF avulso"}

COMMENT_TYPES: dict[str, str] = {
    "corrigir": "Corrigir",
    "reescrever": "Reescrever",
    "remover": "Remover",
    "inserir": "Inserir",
    "pergunta": "Pergunta",
    "nao_alterar": "Não alterar",
    "nota": "Nota",
    "erro_compilacao": "Erro de compilação",
}
MARK_TYPE = "marca"
ANNOTATION_TYPES: dict[str, str] = {**COMMENT_TYPES, MARK_TYPE: "Marca"}

LINK_RELATIONS: dict[str, str] = {
    "consistente": "manter consistente com",
    "propagar": "aplicar a mesma mudança em",
    "origem": "é a origem de",
    "mover": "mover para",
    "contradiz": "contradiz",
    "ver_tambem": "ver também",
}

STATUSES: dict[str, str] = {"aberto": "Aberto", "resolvido": "Resolvido", "descartado": "Descartado"}

REGION_STATES: dict[str, str] = {
    "inalterado": "trecho inalterado",
    "alterado": "trecho alterado",
    "movido": "trecho movido",
    "orfao": "órfão: trecho não encontrado",
}

AGENT_STATUSES: dict[str, str] = {
    "feito": "feito",
    "parcial": "parcial",
    "nao_feito": "não feito",
    "resposta": "resposta",
}

VERSION_ORIGINS: dict[str, str] = {"inicial": "inicial", "agente": "agente", "manual": "manual"}


def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _from_dict(cls, data: dict[str, Any]):
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


@dataclass
class Anchor:
    """Trecho de um arquivo numa versão: linhas 1-based, colunas 0-based (code points), fim exclusivo."""

    file: str
    version: int
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    quote: str
    prefix: str = ""
    suffix: str = ""
    page: int | None = None

    @property
    def lines_label(self) -> str:
        if self.start_line == self.end_line:
            return str(self.start_line)
        return f"{self.start_line}–{self.end_line}"

    @property
    def location(self) -> str:
        return f"{self.file}:{self.lines_label}"

    def with_version(self, version: int) -> Anchor:
        return replace(self, version=version)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Anchor:
        return _from_dict(cls, data)


@dataclass
class Reply:
    author: str  # "voce" | "agente"
    text: str
    created_at: str = field(default_factory=now_iso)
    round: int | None = None
    agent_status: str | None = None
    replacement: str | None = None

    @property
    def author_label(self) -> str:
        return {"voce": "você", "agente": "agente"}.get(self.author, self.author)


@dataclass
class Annotation:
    id: str
    doc: str
    kind: str  # "comment" | "mark"
    type: str
    body: str
    anchor: Anchor
    suggestion: str | None = None
    status: str = "aberto"
    region_state: str | None = None
    anchor_history: list[Anchor] = field(default_factory=list)
    thread: list[Reply] = field(default_factory=list)
    rounds: list[int] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    @property
    def is_open(self) -> bool:
        return self.status == "aberto"

    @property
    def is_mark(self) -> bool:
        return self.kind == "mark"

    @property
    def type_label(self) -> str:
        return ANNOTATION_TYPES.get(self.type, self.type)

    def anchor_at(self, version: int) -> Anchor | None:
        """Âncora válida numa versão (a atual ou uma do histórico)."""
        if self.anchor.version == version:
            return self.anchor
        for anchor in reversed(self.anchor_history):
            if anchor.version == version:
                return anchor
        return None

    def last_agent_reply(self) -> Reply | None:
        for reply in reversed(self.thread):
            if reply.author == "agente":
                return reply
        return None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Annotation:
        data = dict(data)
        data["anchor"] = Anchor.from_dict(data["anchor"])
        data["anchor_history"] = [Anchor.from_dict(a) for a in data.get("anchor_history", [])]
        data["thread"] = [_from_dict(Reply, r) for r in data.get("thread", [])]
        return _from_dict(cls, data)


@dataclass
class Link:
    id: str
    source: str
    target: str
    relation: str
    note: str = ""
    created_at: str = field(default_factory=now_iso)

    @property
    def relation_label(self) -> str:
        return LINK_RELATIONS.get(self.relation, self.relation)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Link:
        return _from_dict(cls, data)


@dataclass
class Version:
    n: int
    created_at: str
    origin: str
    files: dict[str, str] = field(default_factory=dict)  # caminho relativo ao workspace -> sha256
    encodings: dict[str, str] = field(default_factory=dict)  # só arquivos que não são UTF-8
    note: str = ""
    round: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Version:
        return _from_dict(cls, data)


@dataclass
class Document:
    id: str
    name: str
    kind: str  # "latex" | "text" | "pdf"
    root: str  # pasta (ou arquivo único), relativo ao workspace
    main: str | None = None  # .tex principal (latex) ou o próprio arquivo, relativo ao workspace
    engine: str = "pdflatex"
    single: bool = False  # True quando root é um arquivo e não uma pasta
    created_at: str = field(default_factory=now_iso)

    @property
    def kind_label(self) -> str:
        return DOC_KINDS.get(self.kind, self.kind)

    @property
    def label(self) -> str:
        return f"{self.id} · {self.name}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Document:
        return _from_dict(cls, data)


@dataclass
class RoundInfo:
    n: int
    created_at: str
    docs: dict[str, int] = field(default_factory=dict)  # doc -> versão exportada
    annotations: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    instructions: str = ""
    response_imported: bool = False
    summary: str = ""

    @property
    def dirname(self) -> str:
        return f"{self.n:03d}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RoundInfo:
        return _from_dict(cls, data)
