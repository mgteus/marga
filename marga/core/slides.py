"""PowerPoint sem conversão: cada slide desenhado em HTML direto do XML do .pptx.

É um leitor aproximado, sem LibreOffice nem PowerPoint. Desenha:

- posição, tamanho e rotação das formas; preenchimento (cor, degradê, imagem), borda e algumas formas
  geométricas; o fundo e as formas do layout e do mestre;
- textos com os estilos herdados (tema, mestre, layout, placeholder e o próprio parágrafo): fonte, tamanho,
  cor, negrito, itálico, alinhamento, recuos, espaçamento, marcadores e numeração; o ajuste automático de
  tamanho que o PowerPoint gravou;
- imagens (PNG, JPEG, GIF, SVG…), com recorte; tabelas; grupos; linhas e conectores.

Gráficos, SmartArt e objetos incorporados viram uma caixa com o nome, e imagens EMF/WMF também. As fontes são
as que o navegador tiver. Para ver o slide exatamente como no PowerPoint, use o modo PDF (PowerPoint ou
LibreOffice).

Cada parágrafo de texto do slide (e cada linha de tabela) leva ``data-key`` com a mesma chave que
``formats.extract_pptx`` grava no texto extraído; ``bind_lines`` troca a chave pela linha do texto. É assim que
uma seleção no slide vira uma seleção no texto, e um comentário no texto vira uma marca no slide.
"""

from __future__ import annotations

import base64
import colorsys
import html
import math
import posixpath
import re
from dataclasses import dataclass, field, replace
from xml.etree import ElementTree as ET

from . import ooxml
from .formats import LABELS, Extracted, a_text, extract, placeholder, shape_id
from .ooxml import A, MC, P, R, R_ID, local
from .workspace import MargaError

RENDER_VERSION = 4  # muda quando o HTML gerado muda (a interface usa no cache)
EMU_PER_PX = 9525  # 914 400 EMU por polegada, 96 px por polegada
PT_PX = 96 / 72
_SVG_BLIP = "{http://schemas.microsoft.com/office/drawing/2016/SVG/main}svgBlip"
_IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
                ".svg": "image/svg+xml", ".bmp": "image/bmp", ".webp": "image/webp"}
_SHRINK_BYTES = 500_000  # imagens maiores que isso são reduzidas para no máximo _MAX_SIDE px
_MAX_SIDE = 1600
_IGNORED_PH = {"dt", "ftr", "sldNum", "hdr", "sldImg"}  # fora do texto extraído (formats._IGNORED)
_PH_NORM = {"ctrTitle": "title", "subTitle": "body", "obj": "body"}
_ANCHOR = {"t": "flex-start", "ctr": "center", "b": "flex-end", "just": "space-between", "dist": "space-between"}
_ALIGN = {"l": "left", "ctr": "center", "r": "right", "just": "justify", "dist": "justify"}
_SYS_COLORS = {"windowText": "000000", "window": "FFFFFF"}
_PRESET_COLORS = {"black": "000000", "white": "FFFFFF", "red": "FF0000", "green": "008000", "blue": "0000FF",
                  "yellow": "FFFF00", "gray": "808080", "grey": "808080", "orange": "FFA500"}
_CLIP = {
    "triangle": "polygon(50% 0,100% 100%,0 100%)",
    "rtTriangle": "polygon(0 0,100% 100%,0 100%)",
    "diamond": "polygon(50% 0,100% 50%,50% 100%,0 50%)",
    "parallelogram": "polygon(25% 0,100% 0,75% 100%,0 100%)",
    "trapezoid": "polygon(25% 0,75% 0,100% 100%,0 100%)",
    "pentagon": "polygon(50% 0,100% 38%,82% 100%,18% 100%,0 38%)",
    "hexagon": "polygon(25% 0,75% 0,100% 50%,75% 100%,25% 100%,0 50%)",
    "octagon": "polygon(30% 0,70% 0,100% 30%,100% 70%,70% 100%,30% 100%,0 70%,0 30%)",
    "rightArrow": "polygon(0 25%,60% 25%,60% 0,100% 50%,60% 100%,60% 75%,0 75%)",
    "leftArrow": "polygon(40% 0,40% 25%,100% 25%,100% 75%,40% 75%,40% 100%,0 50%)",
    "upArrow": "polygon(50% 0,100% 40%,75% 40%,75% 100%,25% 100%,25% 40%,0 40%)",
    "downArrow": "polygon(25% 0,75% 0,75% 60%,100% 60%,50% 100%,0 60%,25% 60%)",
    "chevron": "polygon(0 0,75% 0,100% 50%,75% 100%,0 100%,25% 50%)",
    "homePlate": "polygon(0 0,80% 0,100% 50%,80% 100%,0 100%)",
    "star5": "polygon(50% 0,61% 35%,98% 35%,68% 57%,79% 91%,50% 70%,21% 91%,32% 57%,2% 35%,39% 35%)",
}
_LINES = {"line", "straightConnector1", "bentConnector2", "bentConnector3", "bentConnector4", "bentConnector5",
          "curvedConnector2", "curvedConnector3", "curvedConnector4", "curvedConnector5"}
_GRAPHIC = {"chart": "📊 Gráfico", "diagram": "🔷 SmartArt", "ole": "📎 Objeto incorporado"}  # sem desenho: veja no PDF


def _int(value: str | None, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


def _n(value: float) -> str:
    """Número curto para CSS."""
    return f"{value:.2f}".rstrip("0").rstrip(".") or "0"


_WEIGHTS = {"thin": 100, "hairline": 100, "extralight": 200, "ultralight": 200, "light": 300, "regular": 400,
            "normal": 400, "book": 400, "medium": 500, "semibold": 600, "demibold": 600, "bold": 700,
            "extrabold": 800, "ultrabold": 800, "black": 900, "heavy": 900}


def _font_parts(name: str) -> tuple[str, int | None, bool]:
    """O PowerPoint chama variações pelo nome ("Bai Jamjuree Bold"); o CSS quer a família, o peso e o itálico."""
    words, weight, italic = (name or "").split(), None, False
    while len(words) > 1:
        word = words[-1].lower().replace("-", "")
        if word in ("italic", "italics", "oblique"):
            italic = True
        elif word in _WEIGHTS and weight is None:
            weight = _WEIGHTS[word]
        else:
            break
        words.pop()
    return " ".join(words), weight, italic


def _font(name: str) -> str:
    """Pilha de fontes CSS (aspas simples: o valor vai dentro de ``style="…"``): o nome, a família e as reservas."""
    names = dict.fromkeys(re.sub(r"[^\w \-.]", "", n)[:60] for n in (name or "", _font_parts(name)[0]))
    stack = "'Aptos', 'Calibri', 'Helvetica Neue', Arial, sans-serif"
    return ", ".join([f"'{n}'" for n in names if n] + [stack])


@dataclass
class Slide:
    n: int
    width: float  # px (96 dpi)
    height: float
    html: str
    hidden: bool = False


@dataclass
class _Part:
    path: str
    root: ET.Element
    rels: dict[str, tuple[str, str]]


@dataclass
class _Theme:
    colors: dict[str, str] = field(default_factory=dict)
    major: str = "Calibri Light"
    minor: str = "Calibri"
    fills: list[ET.Element] = field(default_factory=list)
    lines: list[ET.Element] = field(default_factory=list)
    backgrounds: list[ET.Element] = field(default_factory=list)


@dataclass
class _Run:
    size: float = 18.0  # pt
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    baseline: float = 0.0  # % (positivo = sobrescrito)
    caps: bool = False
    color: str | None = None
    font: str | None = None


@dataclass
class _Para:
    align: str = "l"
    mar_l: int = 0  # EMU
    indent: int = 0
    bullet: tuple | None = None  # ("char", "•") | ("auto", "arabicPeriod", início)
    bu_color: str | None = None
    bu_size: float = 1.0
    bu_font: str | None = None
    before: tuple | None = None  # ("pts", 6.0) | ("pct", 0.2)
    after: tuple | None = None
    line: tuple | None = None
    run: _Run = field(default_factory=_Run)


@dataclass(frozen=True)
class _Tf:
    """EMU (no sistema de coordenadas de um grupo) → px no slide."""

    ox: float = 0.0
    oy: float = 0.0
    sx: float = 1 / EMU_PER_PX
    sy: float = 1 / EMU_PER_PX

    def box(self, x: int, y: int, w: int, h: int) -> tuple[float, float, float, float]:
        return self.ox + x * self.sx, self.oy + y * self.sy, w * self.sx, h * self.sy

    def child(self, xfrm: ET.Element) -> _Tf:
        off, ext = xfrm.find(A + "off"), xfrm.find(A + "ext")
        ch_off, ch_ext = xfrm.find(A + "chOff"), xfrm.find(A + "chExt")
        if off is None or ext is None or ch_off is None or ch_ext is None:
            return self
        kx = _int(ext.get("cx")) / (_int(ch_ext.get("cx")) or 1)
        ky = _int(ext.get("cy")) / (_int(ch_ext.get("cy")) or 1)
        return _Tf(self.ox + (_int(off.get("x")) - _int(ch_off.get("x")) * kx) * self.sx,
                   self.oy + (_int(off.get("y")) - _int(ch_off.get("y")) * ky) * self.sy,
                   self.sx * kx, self.sy * ky)


# ================================================================ cores
def _hex(value: str | None) -> tuple[float, float, float] | None:
    if not value or not re.fullmatch(r"[0-9A-Fa-f]{6}", value):
        return None
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _gamma(c: float) -> float:
    c = min(max(c, 0.0), 1.0)
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _tint(rgb: tuple[float, float, float], val: float) -> tuple[float, float, float]:
    """``a:tint``: mistura com branco, como o Office faz, em RGB linear."""
    return tuple(_gamma(_linear(c) * val + (1 - val)) for c in rgb)


def _modify(rgb: tuple[float, float, float], alpha: float, mod: ET.Element) -> tuple[tuple, float]:
    name, raw = local(mod.tag), _int(mod.get("val"), 100000)
    val = raw / 100000
    if name == "alpha":
        return rgb, val
    if name in ("lumMod", "lumOff", "satMod", "satOff", "hueMod", "hueOff"):
        h, lum, s = colorsys.rgb_to_hls(*rgb)
        if name == "lumMod":
            lum *= val
        elif name == "lumOff":
            lum += val
        elif name == "satMod":
            s *= val
        elif name == "satOff":
            s += val
        elif name == "hueMod":
            h = (h * val) % 1.0
        else:
            h = (h + raw / 21600000) % 1.0  # hueOff vem em 1/60000 de grau
        return colorsys.hls_to_rgb(h, min(max(lum, 0), 1), min(max(s, 0), 1)), alpha
    if name == "tint":
        return _tint(rgb, val), alpha
    if name == "shade":
        return tuple(_gamma(_linear(c) * val) for c in rgb), alpha
    if name == "inv":
        return tuple(1 - c for c in rgb), alpha
    if name == "gray":
        g = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
        return (g, g, g), alpha
    return rgb, alpha


def _css(rgb: tuple[float, float, float], alpha: float = 1.0) -> str:
    r, g, b = (round(min(max(c, 0), 1) * 255) for c in rgb)
    return f"rgb({r},{g},{b})" if alpha >= 0.999 else f"rgba({r},{g},{b},{_n(alpha)})"


# ============================================================= o deck
class Deck:
    """Uma apresentação aberta para desenhar slides (partes, temas e imagens ficam em cache)."""

    def __init__(self, data: bytes) -> None:
        self.zf = ooxml.open_package(data, LABELS["pptx"])
        main, self.pres = ooxml.main_part(self.zf, "ppt/presentation.xml", P + "presentation", LABELS["pptx"])
        rels = ooxml.rels(self.zf, main)
        self.slides = [rels[s.get(R_ID)][1] for s in self.pres.iter(P + "sldId") if s.get(R_ID) in rels]
        size = self.pres.find(P + "sldSz")
        self.cx = _int(None if size is None else size.get("cx"), 9144000)
        self.cy = _int(None if size is None else size.get("cy"), 6858000)
        self.default_style = self.pres.find(P + "defaultTextStyle")
        self._parts: dict[str, _Part] = {}
        self._themes: dict[str, _Theme] = {}
        self._media: dict[str, str | None] = {}

    def close(self) -> None:
        self.zf.close()

    def __enter__(self) -> Deck:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def count(self) -> int:
        return len(self.slides)

    def part(self, path: str | None) -> _Part | None:
        if not path:
            return None
        if path not in self._parts:
            root = ooxml.read_xml(self.zf, path, required=False)
            if root is None:
                return None
            self._parts[path] = _Part(path, root, ooxml.rels(self.zf, path))
        return self._parts[path]

    def related(self, part: _Part | None, rel_type: str) -> _Part | None:
        return None if part is None else self.part(ooxml.related(part.rels, rel_type))

    def theme(self, master: _Part | None) -> _Theme:
        theme_part = self.related(master, "theme")
        if theme_part is None:
            return _Theme()
        if theme_part.path not in self._themes:
            root, theme = theme_part.root, _Theme()
            scheme = root.find(f".//{A}clrScheme")
            for entry in [] if scheme is None else scheme:
                color = entry[0] if len(entry) else None
                if color is not None:
                    value = color.get("val") if local(color.tag) == "srgbClr" else color.get("lastClr")
                    theme.colors[local(entry.tag)] = value or _SYS_COLORS.get(color.get("val", ""), "000000")
            for attr, path in (("major", "majorFont"), ("minor", "minorFont")):
                latin = root.find(f".//{A}{path}/{A}latin")
                if latin is not None and latin.get("typeface"):
                    setattr(theme, attr, latin.get("typeface"))
            fmt = root.find(f".//{A}fmtScheme")
            if fmt is not None:
                for attr, tag in (("fills", "fillStyleLst"), ("lines", "lnStyleLst"), ("backgrounds", "bgFillStyleLst")):
                    found = fmt.find(A + tag)
                    setattr(theme, attr, [] if found is None else list(found))
            self._themes[theme_part.path] = theme
        return self._themes[theme_part.path]

    def media(self, part: _Part, rid: str | None) -> str | None:
        """Imagem ``rid`` da parte como data URI; ``None`` se não existe ou o navegador não a mostra (EMF…)."""
        target = part.rels.get(rid or "")
        if target is None:
            return None
        path = target[1]
        if path not in self._media:
            mime = _IMAGE_TYPES.get(posixpath.splitext(path)[1].lower())
            data = ooxml.read_bytes(self.zf, path, ooxml.MAX_MEDIA_BYTES) if mime else None
            uri = None
            if data:
                data, mime = _shrink(data, mime)
                uri = f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
            self._media[path] = uri
        return self._media[path]

    def render(self, n: int) -> Slide:
        if not 1 <= n <= len(self.slides):
            raise MargaError(f"A apresentação não tem o slide {n}.")
        slide = self.part(self.slides[n - 1])
        if slide is None:
            raise MargaError(f"Não consegui ler o slide {n}.")
        layout = self.related(slide, "slideLayout")
        master = self.related(layout, "slideMaster")
        ctx = _Ctx(self, slide, layout, master, self.theme(master))
        shown = slide.root.get("showMasterSp", "1") != "0"
        out: list[str] = []
        if master is not None and shown and (layout is None or layout.root.get("showMasterSp", "1") != "0"):
            out += ctx.shapes(_tree(master), master, keys=False, template=True)
        if layout is not None and shown:
            out += ctx.shapes(_tree(layout), layout, keys=False, template=True)
        out += ctx.shapes(_tree(slide), slide, keys=True, template=False)
        w, h = self.cx / EMU_PER_PX, self.cy / EMU_PER_PX
        body = (f'<div class="mg-slide" style="width:{_n(w)}px;height:{_n(h)}px;background:{ctx.background()}">'
                + "".join(out) + "</div>")
        return Slide(n, w, h, body, hidden=slide.root.get("show") == "0")


def render_slide(data: bytes, n: int) -> Slide:
    with Deck(data) as deck:
        return deck.render(n)


def _tree(part: _Part) -> ET.Element | None:
    return part.root.find(f"{P}cSld/{P}spTree")


def _shrink(data: bytes, mime: str) -> tuple[bytes, str]:
    """Imagens grandes (fotos) ficam com no máximo _MAX_SIDE px: o slide trafega a cada clique."""
    if len(data) <= _SHRINK_BYTES or mime not in ("image/png", "image/jpeg"):
        return data, mime
    try:
        import pymupdf

        pix = pymupdf.Pixmap(data)
        side = max(pix.width, pix.height)
        if side > _MAX_SIDE:
            k = _MAX_SIDE / side
            pix = pymupdf.Pixmap(pix, max(1, int(pix.width * k)), max(1, int(pix.height * k)), None)
        if pix.alpha:
            return pix.tobytes("png"), "image/png"
        if pix.n > 3:  # CMYK
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        return pix.tobytes("jpeg", jpg_quality=85), "image/jpeg"
    except Exception:  # noqa: BLE001 - imagem que o PyMuPDF não lê vai como veio
        return data, mime


def _num(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None  # nome de guia (fórmula do DrawingML): a forma não é desenhada


def _custom_paths(geom: ET.Element, width: float, height: float) -> list[tuple[str, bool, bool]] | None:
    """Caminhos de uma forma livre (``a:custGeom``) em SVG, já na escala da forma: (d, preenche?, contorna?)."""
    out = []
    for path in geom.iter(A + "path"):
        pw, ph = _num(path.get("w")), _num(path.get("h"))
        kx = width / pw if pw else 1 / EMU_PER_PX
        ky = height / ph if ph else 1 / EMU_PER_PX
        d: list[str] = []
        cur = (0.0, 0.0)
        for cmd in path:
            name = local(cmd.tag)
            pts = []
            for pt in cmd.findall(A + "pt"):
                x, y = _num(pt.get("x")), _num(pt.get("y"))
                if x is None or y is None:
                    return None
                pts.append((x * kx, y * ky))
            if name in ("moveTo", "lnTo") and pts:
                d.append(f"{'M' if name == 'moveTo' else 'L'}{_n(pts[0][0])} {_n(pts[0][1])}")
                cur = pts[0]
            elif name == "cubicBezTo" and len(pts) == 3:
                d.append("C" + " ".join(f"{_n(x)} {_n(y)}" for x, y in pts))
                cur = pts[2]
            elif name == "quadBezTo" and len(pts) == 2:
                d.append("Q" + " ".join(f"{_n(x)} {_n(y)}" for x, y in pts))
                cur = pts[1]
            elif name == "arcTo":
                vals = [_num(cmd.get(k)) for k in ("wR", "hR", "stAng", "swAng")]
                if any(v is None for v in vals):
                    return None
                wr, hr = vals[0] * kx, vals[1] * ky
                st, sw = math.radians(vals[2] / 60000), math.radians(vals[3] / 60000)
                cx, cy = cur[0] - wr * math.cos(st), cur[1] - hr * math.sin(st)
                end = (cx + wr * math.cos(st + sw), cy + hr * math.sin(st + sw))
                large = int(abs(sw) > math.pi)
                d.append(f"A{_n(wr)} {_n(hr)} 0 {large} {int(sw > 0)} {_n(end[0])} {_n(end[1])}")
                cur = end
            elif name == "close":
                d.append("Z")
        if d:
            out.append(("".join(d), path.get("fill", "norm") != "none", path.get("stroke", "1") not in ("0", "false")))
    return out or None


def _find_ph(root: ET.Element, kind: str, idx: str | None, by_index: bool) -> ET.Element | None:
    """Placeholder do layout (pelo índice) ou do mestre (pelo tipo) que corresponde ao do slide."""
    found = [(sp, ph) for sp in root.iter(P + "sp") if (ph := placeholder(sp))]
    if by_index and idx is not None:
        for sp, (_, i) in found:
            if i == idx:
                return sp
    for sp, (k, _) in found:
        if k == kind:
            return sp
    norm = _PH_NORM.get(kind, kind)
    return next((sp for sp, (k, _) in found if _PH_NORM.get(k, k) == norm), None)


def _xfrm(el: ET.Element) -> ET.Element | None:
    if el.tag == P + "graphicFrame":
        return el.find(P + "xfrm")
    if el.tag == P + "grpSp":
        return el.find(f"{P}grpSpPr/{A}xfrm")
    return el.find(f"{P}spPr/{A}xfrm")


def _level(lst: ET.Element | None, level: int) -> ET.Element | None:
    return None if lst is None else lst.find(f"{A}lvl{level + 1}pPr")


def _spacing(el: ET.Element) -> tuple | None:
    pct, pts = el.find(A + "spcPct"), el.find(A + "spcPts")
    if pct is not None:
        return "pct", _int(pct.get("val"), 100000) / 100000
    if pts is not None:
        return "pts", _int(pts.get("val")) / 100
    return None


def _autonum(kind: str, n: int) -> str:
    if kind.startswith(("alphaLc", "alphaUc")):
        s, k = "", n
        while k > 0:
            k, rem = divmod(k - 1, 26)
            s = chr(65 + rem) + s
        s = s.lower() if kind.startswith("alphaLc") else s
    elif kind.startswith(("romanLc", "romanUc")):
        s, k = "", n
        for value, sym in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"),
                           (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
            while k >= value:
                s, k = s + sym, k - value
        s = s.lower() if kind.startswith("romanLc") else s
    else:
        s = str(n)
    if kind.endswith("ParenBoth"):
        return f"({s})"
    if kind.endswith("ParenR"):
        return f"{s})"
    return s if kind.endswith("Plain") else f"{s}."


# ======================================================= desenho
@dataclass
class _Ctx:
    deck: Deck
    slide: _Part
    layout: _Part | None
    master: _Part | None
    theme: _Theme
    clr_map: dict[str, str] = field(default_factory=dict)
    theme_part: _Part | None = None  # imagens dos estilos do tema (fundos com textura) estão nas relações dele
    groups: list[tuple[ET.Element | None, tuple]] = field(default_factory=list)  # (preenchimento, caixa) do grupo

    def __post_init__(self) -> None:
        self.theme_part = self.deck.related(self.master, "theme")
        base = {} if self.master is None else self.master.root.find(P + "clrMap")
        self.clr_map = dict(base.attrib) if base is not None and len(base.attrib) else {
            "bg1": "lt1", "tx1": "dk1", "bg2": "lt2", "tx2": "dk2"}
        for part in (self.layout, self.slide):
            over = None if part is None else part.root.find(f"{P}clrMapOvr/{A}overrideClrMapping")
            if over is not None:
                self.clr_map.update(over.attrib)

    # ------------------------------------------------------------ cores
    def _rgb(self, el: ET.Element, ph_color: str | None) -> tuple[float, float, float] | None:
        name = local(el.tag)
        if name == "srgbClr":
            return _hex(el.get("val"))
        if name == "schemeClr":
            val = el.get("val", "")
            if val == "phClr":
                return _hex(ph_color)
            return _hex(self.theme.colors.get(self.clr_map.get(val, val)))
        if name == "sysClr":
            return _hex(el.get("lastClr") or _SYS_COLORS.get(el.get("val", ""), "000000"))
        if name == "prstClr":
            return _hex(_PRESET_COLORS.get(el.get("val", ""), "000000"))
        if name == "scrgbClr":
            return tuple(min(max(_int(el.get(c)) / 100000, 0), 1) for c in ("r", "g", "b"))
        return None

    def rgba(self, holder, ph_color: str | None = None) -> tuple[tuple[float, float, float], float] | None:
        """(RGB 0–1, opacidade) do primeiro elemento de cor dentro de ``holder``, com os modificadores."""
        for el in [] if holder is None else holder:
            rgb = self._rgb(el, ph_color)
            if rgb is None:
                continue
            alpha = 1.0
            for mod in el:
                rgb, alpha = _modify(rgb, alpha, mod)
            return rgb, alpha
        return None

    def color(self, holder, ph_color: str | None = None) -> str | None:
        """Cor CSS do primeiro elemento de cor dentro de ``holder`` (solidFill, buClr, fillRef…)."""
        found = self.rgba(holder, ph_color)
        return None if found is None else _css(*found)

    def _group_fill(self, box: tuple | None) -> str | None:
        """``a:grpFill``: a cor do preenchimento do grupo no ponto em que a forma está (degradê → cor sólida)."""
        if not self.groups or self.groups[-1][0] is None:
            return None
        el, gbox = self.groups[-1]
        if local(el.tag) != "gradFill" or box is None:
            return self._fill_el(el, self.slide)
        stops = sorted((_int(gs.get("pos")) / 100000, found) for gs in el.iter(A + "gs")
                       if (found := self.rgba([c for c in gs])) is not None)
        if not stops:
            return None
        lin = el.find(A + "lin")
        ang = math.radians(_int(None if lin is None else lin.get("ang"), 5400000) / 60000)
        dx, dy = math.cos(ang), math.sin(ang)
        cx, cy = box[0] + box[2] / 2 - gbox[0], box[1] + box[3] / 2 - gbox[1]
        span = abs(gbox[2] * dx) + abs(gbox[3] * dy) or 1
        t = min(max((cx * dx + cy * dy + (gbox[2] * max(-dx, 0) + gbox[3] * max(-dy, 0))) / span, 0), 1)
        for (p0, (c0, a0)), (p1, (c1, a1)) in zip(stops, stops[1:]):
            if p0 <= t <= p1:
                k = (t - p0) / ((p1 - p0) or 1)
                return _css(tuple(x + (y - x) * k for x, y in zip(c0, c1)), a0 + (a1 - a0) * k)
        pos, (rgb, alpha) = stops[0] if t < stops[0][0] else stops[-1]
        return _css(rgb, alpha)

    def _hex_of(self, holder: ET.Element | None) -> str | None:
        """A mesma cor, em hexadecimal (para substituir ``phClr`` nos estilos do tema)."""
        css = self.color(holder)
        if css is None:
            return None
        nums = [int(x) for x in re.findall(r"\d+", css)[:3]]
        return "".join(f"{v:02X}" for v in nums)

    # ---------------------------------------------- preenchimento e borda
    def _fill_el(self, el: ET.Element, part: _Part, ph_color: str | None = None) -> str | None:
        name = local(el.tag)
        if name == "noFill":
            return "transparent"
        if name == "solidFill":
            return self.color(el, ph_color) or "transparent"
        if name == "gradFill":
            stops = sorted(((_int(gs.get("pos")) / 1000, self.color(gs, ph_color)) for gs in el.iter(A + "gs")),
                           key=lambda s: s[0])
            stops = [(pos, c) for pos, c in stops if c]
            if not stops:
                return None
            if len(stops) == 1:
                return stops[0][1]
            path, lin = el.find(A + "path"), el.find(A + "lin")
            if path is not None:  # degradê radial: o foco é o fillToRect (em % da forma, pode ficar fora dela)
                ftr = path.find(A + "fillToRect")
                l, t, r, b = ((_int(ftr.get(k)) / 1000 for k in "ltrb") if ftr is not None else (50, 50, 50, 50))
                shape = "circle" if path.get("path") == "circle" else "ellipse"
                kind = f"radial-gradient({shape} farthest-corner at {_n((l + 100 - r) / 2)}% {_n((t + 100 - b) / 2)}%"
            else:
                angle = (_int(lin.get("ang")) / 60000 + 90) % 360 if lin is not None else 180
                kind = f"linear-gradient({_n(angle)}deg"
            return f"{kind}, " + ", ".join(f"{c} {_n(pos)}%" for pos, c in stops) + ")"
        if name == "blipFill":
            blip = el.find(A + "blip")
            svg = None if blip is None else blip.find(f".//{_SVG_BLIP}")  # há preenchimentos só em SVG, sem PNG
            uri = next((u for c in (svg, blip) if c is not None and (u := self.deck.media(part, c.get(R + "embed")))),
                       None)
            if uri is None:
                return None
            tile = el.find(A + "tile") is not None
            css = f"url('{uri}') center / {'auto' if tile else '100% 100%'} {'repeat' if tile else 'no-repeat'}"
            if not tile:  # fillRect (margens, às vezes negativas: a imagem passa da forma) e srcRect (recorte da imagem)
                fill_rect, src = el.find(f"{A}stretch/{A}fillRect"), el.find(A + "srcRect")
                fl, ft, fr, fb = ((_int(fill_rect.get(k)) / 100000 for k in "ltrb") if fill_rect is not None
                                  else (0.0, 0.0, 0.0, 0.0))
                sl, st, sr, sb = ((_int(src.get(k)) / 100000 for k in "ltrb") if src is not None else (0.0, 0.0, 0.0, 0.0))
                if any((fl, ft, fr, fb, sl, st, sr, sb)):
                    iw = (1 - fl - fr) / max(1 - sl - sr, 0.01)  # tamanho da imagem inteira, em frações da forma
                    ih = (1 - ft - fb) / max(1 - st - sb, 0.01)
                    x0, y0 = fl - sl * iw, ft - st * ih

                    def pos(start: float, size: float) -> float:  # background-position em % do espaço livre
                        return 0.0 if abs(1 - size) < 1e-6 else start / (1 - size) * 100

                    css = (f"url('{uri}') {_n(pos(x0, iw))}% {_n(pos(y0, ih))}% / {_n(iw * 100)}% {_n(ih * 100)}% "
                           "no-repeat")
            duo = None if blip is None else blip.find(A + "duotone")
            colors = [] if duo is None else [c for c in (self.color([x], ph_color) for x in duo) if c]
            if len(colors) == 2:  # duotone: a luminosidade da imagem com as duas cores do tema
                return f"{css}, linear-gradient({colors[1]}, {colors[0]});background-blend-mode:multiply"
            return css
        if name == "pattFill":
            return self.color(el.find(A + "fgClr"), ph_color) or "transparent"
        return None

    def fill(self, holder: ET.Element | None, part: _Part, style: ET.Element | None = None,
             box: tuple | None = None) -> str | None:
        """CSS ``background`` da forma: o que o spPr diz ou, sem nada lá, o estilo de tema (fillRef)."""
        for el in [] if holder is None else holder:
            css = self._group_fill(box) if local(el.tag) == "grpFill" else self._fill_el(el, part)
            if css is not None:
                return css
        ref = None if style is None else style.find(A + "fillRef")
        idx = _int(None if ref is None else ref.get("idx"))
        if idx < 1:
            return None
        color = self._hex_of(ref)
        if 1 <= idx <= len(self.theme.fills):
            css = self._fill_el(self.theme.fills[idx - 1], self.theme_part or part, color)
            if css is not None:
                return css
        return self.color(ref)

    def line(self, sp_pr: ET.Element | None, style: ET.Element | None = None) -> tuple[float, str, str] | None:
        """(largura em px, cor, estilo CSS) do contorno, ou ``None`` sem contorno."""
        ln = None if sp_pr is None else sp_pr.find(A + "ln")
        ref = None if style is None else style.find(A + "lnRef")
        ref_idx = _int(None if ref is None else ref.get("idx"))
        width = _int(None if ln is None else ln.get("w"), 0)
        if ln is not None and ln.find(A + "noFill") is not None:
            return None
        color = self.color(None if ln is None else ln.find(A + "solidFill"))
        if color is None and ref_idx > 0:
            color = self.color(ref)
            if not width and ref_idx <= len(self.theme.lines):
                width = _int(self.theme.lines[ref_idx - 1].get("w"), 9525)
        if color is None:
            return None
        dash = None if ln is None else ln.find(A + "prstDash")
        val = "" if dash is None else dash.get("val", "")
        kind = "dotted" if "Dot" in val or val == "dot" else ("dashed" if "ash" in val else "solid")
        return max((width or 9525) / EMU_PER_PX, 0.75), color, kind

    def background(self) -> str:
        for part in (self.slide, self.layout, self.master):
            bg = None if part is None else part.root.find(f"{P}cSld/{P}bg")
            if bg is None:
                continue
            bg_pr = bg.find(P + "bgPr")
            if bg_pr is not None:
                css = self.fill(bg_pr, part)
                return "#fff" if css in (None, "transparent") else css
            ref = bg.find(P + "bgRef")
            if ref is not None:
                idx = _int(ref.get("idx"))
                if 1001 <= idx < 1001 + len(self.theme.backgrounds):
                    css = self._fill_el(self.theme.backgrounds[idx - 1001], self.theme_part or part, self._hex_of(ref))
                    if css:
                        return css
                return self.color(ref) or "#fff"
        return "#fff"

    # ------------------------------------------------------ placeholders
    def inherited(self, el: ET.Element) -> list[ET.Element]:
        """O placeholder correspondente no layout e no mestre (nessa ordem), de onde herdam posição e estilos."""
        ph = placeholder(el)
        if ph is None:
            return []
        out = []
        for part, by_index in ((self.layout, True), (self.master, False)):
            found = None if part is None else _find_ph(part.root, ph[0], ph[1], by_index)
            if found is not None:
                out.append(found)
        return out

    def place(self, el: ET.Element, tf: _Tf) -> tuple[tuple[float, float, float, float], ET.Element] | None:
        for cand in [el, *self.inherited(el)]:
            xf = _xfrm(cand)
            off = None if xf is None else xf.find(A + "off")
            if off is not None:
                ext = xf.find(A + "ext")
                box = tf.box(_int(off.get("x")), _int(off.get("y")),
                             _int(None if ext is None else ext.get("cx")), _int(None if ext is None else ext.get("cy")))
                return box, xf
        return None

    # ------------------------------------------------------------ formas
    def shapes(self, tree: ET.Element | None, part: _Part, keys: bool, template: bool, tf: _Tf = _Tf()) -> list[str]:
        out: list[str] = []
        for el in [] if tree is None else tree:
            if el.tag == MC + "AlternateContent":
                choice = el.find(MC + "Choice")
                out += self.shapes(choice, part, keys, template, tf)
                continue
            if el.tag not in (P + "sp", P + "pic", P + "graphicFrame", P + "grpSp", P + "cxnSp"):
                continue
            c_nv = el[0].find(P + "cNvPr") if len(el) else None
            if c_nv is not None and c_nv.get("hidden") in ("1", "true"):
                continue
            if template and placeholder(el) is not None:
                continue  # placeholders do layout/mestre são moldes, não aparecem no slide
            try:
                if el.tag == P + "grpSp":
                    xf = _xfrm(el)
                    placed = self.place(el, tf)
                    grp_pr = el.find(P + "grpSpPr")
                    fill = next((c for c in ([] if grp_pr is None else grp_pr) if local(c.tag).endswith("Fill")), None)
                    self.groups.append((fill, placed[0] if placed else (0, 0, 0, 0)))
                    try:
                        out += self.shapes(el, part, keys, template, tf.child(xf) if xf is not None else tf)
                    finally:
                        self.groups.pop()
                elif el.tag == P + "pic":
                    out.append(self.picture(el, part, tf))
                elif el.tag == P + "graphicFrame":
                    out.append(self.frame(el, part, keys, tf))
                else:
                    out.append(self.shape(el, part, keys, tf))
            except Exception:  # noqa: BLE001 - uma forma estranha não derruba o slide
                continue
        return out

    def _box_style(self, box: tuple[float, float, float, float], xf: ET.Element | None, flips: bool) -> str:
        x, y, w, h = box
        style = f"left:{_n(x)}px;top:{_n(y)}px;width:{_n(w)}px;height:{_n(h)}px"
        parts = []
        rot = _int(None if xf is None else xf.get("rot"))
        if rot:
            parts.append(f"rotate({_n(rot / 60000)}deg)")
        if flips and xf is not None:
            if xf.get("flipH") in ("1", "true"):
                parts.append("scaleX(-1)")
            if xf.get("flipV") in ("1", "true"):
                parts.append("scaleY(-1)")
        return style + (f";transform:{' '.join(parts)}" if parts else "")

    def shape(self, sp: ET.Element, part: _Part, keys: bool, tf: _Tf) -> str:
        placed = self.place(sp, tf)
        if placed is None:
            return ""
        box, xf = placed
        sp_pr, style = sp.find(P + "spPr"), sp.find(P + "style")
        geom = None if sp_pr is None else sp_pr.find(A + "prstGeom")
        prst = "rect" if geom is None else geom.get("prst", "rect")
        if sp.tag == P + "cxnSp" or prst in _LINES:
            return self.connector(sp_pr, style, box, xf)
        css = [self._box_style(box, xf, flips=False)]
        bg = self.fill(sp_pr, part, style, box)
        body = sp.find(P + "txBody")
        ph = placeholder(sp)
        key = keys and not (ph and ph[0] in _IGNORED_PH)
        inner = self.text(body, sp, part, shape_id(sp) if key else None) if body is not None else ""
        cust = None if sp_pr is None else sp_pr.find(A + "custGeom")
        paths = None if cust is None else _custom_paths(cust, box[2], box[3])
        if paths is not None:  # forma livre: o desenho vai num SVG, com o texto (se houver) por cima
            svg = self._paths_svg(paths, bg, self.line(sp_pr, style), box, xf)
            return f'<div class="mg-sh" style="{css[0]}">{svg}{inner}</div>'
        if bg and bg != "transparent":
            css.append(f"background:{bg}")
        if prst in _CLIP:
            css.append(f"clip-path:{_CLIP[prst]}")
        else:
            border = self.line(sp_pr, style)
            if border:
                css.append(f"border:{_n(border[0])}px {border[2]} {border[1]}")
            if prst == "ellipse":
                css.append("border-radius:50%")
            elif prst in ("roundRect", "round2SameRect", "snipRoundRect"):
                css.append(f"border-radius:{_n(min(box[2], box[3]) * 0.1667)}px")
        return f'<div class="mg-sh" style="{";".join(css)}">{inner}</div>'

    def _paths_svg(self, paths: list[tuple[str, bool, bool]], bg: str | None, line: tuple | None, box: tuple,
                   xf: ET.Element | None) -> str:
        solid = bg if bg and re.fullmatch(r"rgba?\([^)]*\)", bg) else None
        fill_div = ""
        if bg and bg != "transparent" and solid is None:
            # Imagem ou degradê: o fundo de um div recortado no formato da forma (clip-path: path()).
            d = " ".join(p for p, fills, _ in paths if fills)
            flips = [t for attr, t in (("flipH", "scaleX(-1)"), ("flipV", "scaleY(-1)"))
                     if xf is not None and xf.get(attr) in ("1", "true")]
            flip_css = f";transform:{' '.join(flips)}" if flips else ""
            fill_div = f"<div class=\"mg-fill\" style=\"background:{bg};clip-path:path(evenodd,'{d}'){flip_css}\"></div>"
        flip = []
        if xf is not None and xf.get("flipH") in ("1", "true"):
            flip.append(f"translate({_n(box[2])} 0) scale(-1 1)")
        if xf is not None and xf.get("flipV") in ("1", "true"):
            flip.append(f"translate(0 {_n(box[3])}) scale(1 -1)")
        group = f' transform="{" ".join(flip)}"' if flip else ""
        out = []
        for d, fills, strokes in paths:
            stroke = f' stroke="{line[1]}" stroke-width="{_n(line[0])}"' if strokes and line else ""
            out.append(f'<path d="{d}" fill="{solid if fills and solid else "none"}" fill-rule="evenodd"{stroke}/>')
        return (f'{fill_div}<svg class="mg-geo" width="{_n(max(box[2], 1))}" height="{_n(max(box[3], 1))}" '
                f'overflow="visible"><g{group}>{"".join(out)}</g></svg>')

    def connector(self, sp_pr: ET.Element | None, style: ET.Element | None, box: tuple, xf: ET.Element | None) -> str:
        line = self.line(sp_pr, style) or (1.0, "rgb(0,0,0)", "solid")
        x, y, w, h = box
        x1, y1, x2, y2 = 0.0, 0.0, w, h
        if xf is not None and xf.get("flipH") in ("1", "true"):
            x1, x2 = x2, x1
        if xf is not None and xf.get("flipV") in ("1", "true"):
            y1, y2 = y2, y1
        dash = ' stroke-dasharray="6 4"' if line[2] == "dashed" else (' stroke-dasharray="2 3"' if line[2] == "dotted" else "")
        ln = None if sp_pr is None else sp_pr.find(A + "ln")
        head = ln is not None and any(e is not None and e.get("type", "none") != "none"
                                      for e in (ln.find(A + "tailEnd"), ln.find(A + "headEnd")))
        marker = ""
        if head:
            marker = (f'<defs><marker id="mg-ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" '
                      f'markerHeight="5" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" fill="{line[1]}"/>'
                      "</marker></defs>")
        end = ' marker-end="url(#mg-ar)"' if head else ""
        return (f'<svg class="mg-sh mg-ln" style="left:{_n(x)}px;top:{_n(y)}px;width:{_n(max(w, 1))}px;'
                f'height:{_n(max(h, 1))}px" overflow="visible">{marker}<line x1="{_n(x1)}" y1="{_n(y1)}" '
                f'x2="{_n(x2)}" y2="{_n(y2)}" stroke="{line[1]}" stroke-width="{_n(line[0])}"{dash}{end}/></svg>')

    def picture(self, pic: ET.Element, part: _Part, tf: _Tf) -> str:
        placed = self.place(pic, tf)
        if placed is None:
            return ""
        box, xf = placed
        blip_fill = pic.find(P + "blipFill")
        blip = None if blip_fill is None else blip_fill.find(A + "blip")
        svg = None if blip is None else blip.find(f".//{_SVG_BLIP}")
        uri = None
        for cand in (svg, blip):
            if cand is not None and uri is None:
                uri = self.deck.media(part, cand.get(R + "embed"))
        style = self._box_style(box, xf, flips=True)
        if uri is None:
            return f'<div class="mg-sh mg-obj" style="{style}"><span>🖼 Imagem que o navegador não mostra</span></div>'
        crop = None if blip_fill is None else blip_fill.find(A + "srcRect")
        l, t, r, b = ((_int(crop.get(k)) / 100000 for k in ("l", "t", "r", "b")) if crop is not None
                      else (0.0, 0.0, 0.0, 0.0))
        w, h = box[2], box[3]
        img_w, img_h = w / max(1 - l - r, 0.01), h / max(1 - t - b, 0.01)
        effects = []  # transparência e ajustes de imagem (texturas do tema costumam ter 10–30% de opacidade)
        alpha = None if blip is None else blip.find(A + "alphaModFix")
        if alpha is not None:
            effects.append(f"opacity:{_n(_int(alpha.get('amt'), 100000) / 100000)}")
        filters = []
        if blip is not None and blip.find(A + "grayscl") is not None:
            filters.append("grayscale(1)")
        lum = None if blip is None else blip.find(A + "lum")
        if lum is not None:
            if lum.get("bright"):
                filters.append(f"brightness({_n(1 + _int(lum.get('bright')) / 100000)})")
            if lum.get("contrast"):
                filters.append(f"contrast({_n(1 + _int(lum.get('contrast')) / 100000)})")
        if filters:
            effects.append(f"filter:{' '.join(filters)}")
        extra = "".join(f";{e}" for e in effects)
        return (f'<div class="mg-sh mg-pic" style="{style}"><img alt="" src="{uri}" style="left:{_n(-l * img_w)}px;'
                f'top:{_n(-t * img_h)}px;width:{_n(img_w)}px;height:{_n(img_h)}px{extra}"></div>')

    def frame(self, frame: ET.Element, part: _Part, keys: bool, tf: _Tf) -> str:
        placed = self.place(frame, tf)
        if placed is None:
            return ""
        box, xf = placed
        data = frame.find(f"{A}graphic/{A}graphicData")
        tbl = None if data is None else data.find(A + "tbl")
        if tbl is not None:
            return self.table(frame, tbl, part, keys, box, tf)
        uri = "" if data is None else data.get("uri", "")
        fallback = None if data is None else data.find(f".//{P}pic")
        if fallback is not None:
            return self.picture(fallback, part, tf) if self.place(fallback, tf) else ""
        label = next((v for k, v in _GRAPHIC.items() if uri.rstrip("/").endswith(k)), "📎 Objeto")
        size = _n(min(max(min(box[2], box[3]) / 9, 12), 28))
        return (f'<div class="mg-sh mg-obj" style="{self._box_style(box, xf, flips=False)};font-size:{size}px">'
                f"<span>{label} · veja no modo PDF</span></div>")

    def table(self, frame: ET.Element, tbl: ET.Element, part: _Part, keys: bool, box: tuple, tf: _Tf) -> str:
        grid = tbl.find(A + "tblGrid")
        cols = [_int(gc.get("w")) * tf.sx for gc in ([] if grid is None else grid.findall(A + "gridCol"))]
        props = tbl.find(A + "tblPr")
        first_row = props is not None and props.get("firstRow") in ("1", "true")
        band_row = props is not None and props.get("bandRow") in ("1", "true")
        # Estilo de tabela: o padrão do PowerPoint (Medium Style 2 · Accent 1) quando a tabela tem algum estilo.
        styled = props is not None and (props.find(A + "tableStyleId") is not None or first_row or band_row)
        rgb = _hex(self.theme.colors.get("accent1", "4472C4")) or (0.27, 0.45, 0.77)
        dark = _css(_hex(self.theme.colors.get("dk1")) or (0, 0, 0))
        light = _css(_hex(self.theme.colors.get("lt1")) or (1, 1, 1))
        sid = shape_id(frame)
        rows = []
        for r, tr in enumerate(tbl.findall(A + "tr")):
            header = first_row and r == 0
            band = band_row and not header and (r - (1 if first_row else 0)) % 2 == 0
            cells = []
            for tc in tr.findall(A + "tc"):
                if tc.get("hMerge") in ("1", "true") or tc.get("vMerge") in ("1", "true"):
                    continue
                tc_pr = tc.find(A + "tcPr")
                css = []
                bg = self.fill(tc_pr, part)
                if bg is None and styled:
                    bg = _css(rgb) if header else _css(_tint(rgb, 0.4 if band else 0.2))
                if bg and bg != "transparent":
                    css.append(f"background:{bg}")
                for side, tag in (("left", "lnL"), ("right", "lnR"), ("top", "lnT"), ("bottom", "lnB")):
                    ln = None if tc_pr is None else tc_pr.find(A + tag)
                    border = None
                    if ln is not None and ln.find(A + "noFill") is None:
                        color = self.color(ln.find(A + "solidFill"))
                        if color:
                            border = f"{_n(max(_int(ln.get('w'), 12700) / EMU_PER_PX, 0.75))}px solid {color}"
                    css.append(f"border-{side}:{border or '1px solid #fff'}")
                pad = [_int(None if tc_pr is None else tc_pr.get(k), d) / EMU_PER_PX
                       for k, d in (("marT", 45720), ("marR", 91440), ("marB", 45720), ("marL", 91440))]
                css.append("padding:" + " ".join(f"{_n(v)}px" for v in pad))
                anchor = None if tc_pr is None else tc_pr.get("anchor")
                css.append("vertical-align:" + {"ctr": "middle", "b": "bottom"}.get(anchor or "", "top"))
                span = "".join(f' {attr}="{_int(tc.get(k), 1)}"' for attr, k in (("colspan", "gridSpan"),
                                                                                 ("rowspan", "rowSpan"))
                               if _int(tc.get(k), 1) > 1)
                override = None
                if styled:
                    override = {"color": light, "bold": True} if header else {"color": dark}
                body = tc.find(A + "txBody")
                inner = self.text(body, None, part, None, cell=True, override=override) if body is not None else ""
                cells.append(f'<td{span} style="{";".join(css)}">{inner}</td>')
            has_text = any(a_text(p) for p in tr.iter(A + "p"))
            key = f' data-key="{sid}:r{r}" data-row="1"' if keys and has_text else ""
            rows.append(f'<tr style="height:{_n(_int(tr.get("h")) * tf.sy)}px"{key}>{"".join(cells)}</tr>')
        colgroup = "".join(f'<col style="width:{_n(w)}px">' for w in cols)
        x, y, w, _ = box
        return (f'<div class="mg-sh mg-tblw" style="left:{_n(x)}px;top:{_n(y)}px;width:{_n(w)}px">'
                f'<table class="mg-tbl"><colgroup>{colgroup}</colgroup>{"".join(rows)}</table></div>')

    # ------------------------------------------------------------- texto
    def _level_chain(self, sp: ET.Element | None, level: int, cell: bool) -> list[ET.Element]:
        """Estilos de parágrafo do nível, do mais geral ao mais específico."""
        chain = [_level(self.deck.default_style, level)]
        ph = None if sp is None else placeholder(sp)
        styles = None if self.master is None else self.master.root.find(P + "txStyles")
        if ph is None or cell:
            name = "otherStyle"
        elif ph[0] in ("title", "ctrTitle"):
            name = "titleStyle"
        elif ph[0] in ("body", "obj", "subTitle"):
            name = "bodyStyle"
        else:
            name = "otherStyle"
        chain.append(_level(None if styles is None else styles.find(P + name), level))
        if sp is not None:
            for inherited in reversed(self.inherited(sp)):  # mestre, depois layout
                chain.append(_level(inherited.find(f"{P}txBody/{A}lstStyle"), level))
        return [el for el in chain if el is not None]

    def _font_name(self, typeface: str | None) -> str | None:
        if not typeface:
            return None
        if typeface.startswith("+mj"):
            return self.theme.major
        if typeface.startswith("+mn"):
            return self.theme.minor
        return typeface

    def run_props(self, run: _Run, el: ET.Element) -> _Run:
        run = replace(run)
        if el.get("sz"):
            run.size = _int(el.get("sz"), 1800) / 100
        for attr, name in (("b", "bold"), ("i", "italic")):
            if el.get(attr) is not None:
                setattr(run, name, el.get(attr) in ("1", "true"))
        if el.get("u") is not None:
            run.underline = el.get("u") != "none"
        if el.get("strike") is not None:
            run.strike = el.get("strike") != "noStrike"
        if el.get("baseline") is not None:
            run.baseline = _int(el.get("baseline")) / 1000
        if el.get("cap") is not None:
            run.caps = el.get("cap") == "all"
        fill = el.find(A + "solidFill")
        if fill is not None:
            run.color = self.color(fill) or run.color
        latin = el.find(A + "latin")
        if latin is not None and latin.get("typeface"):
            run.font = self._font_name(latin.get("typeface"))
        return run

    def para_props(self, para: _Para, el: ET.Element) -> _Para:
        para = replace(para, run=replace(para.run))
        if el.get("algn"):
            para.align = el.get("algn")
        if el.get("marL") is not None:
            para.mar_l = _int(el.get("marL"))
        if el.get("indent") is not None:
            para.indent = _int(el.get("indent"))
        for child in el:
            name = local(child.tag)
            if name == "buNone":
                para.bullet = None
            elif name == "buChar":
                para.bullet = ("char", child.get("char") or "•")
            elif name == "buAutoNum":
                para.bullet = ("auto", child.get("type", "arabicPeriod"), _int(child.get("startAt"), 1))
            elif name == "buClr":
                para.bu_color = self.color(child)
            elif name == "buClrTx":
                para.bu_color = None
            elif name == "buSzPct":
                para.bu_size = _int(child.get("val"), 100000) / 100000
            elif name == "buFont":
                para.bu_font = self._font_name(child.get("typeface"))
            elif name in ("spcBef", "spcAft", "lnSpc"):
                setattr(para, {"spcBef": "before", "spcAft": "after", "lnSpc": "line"}[name], _spacing(child))
            elif name == "defRPr":
                para.run = self.run_props(para.run, child)
        return para

    def _body_props(self, sp: ET.Element | None, body: ET.Element) -> dict:
        props = {"lIns": 91440, "tIns": 45720, "rIns": 91440, "bIns": 45720, "anchor": "t", "wrap": "square"}
        sources = [] if sp is None else [i.find(f"{P}txBody/{A}bodyPr") for i in reversed(self.inherited(sp))]
        for bp in [*sources, body.find(A + "bodyPr")]:
            if bp is None:
                continue
            for key in ("lIns", "tIns", "rIns", "bIns"):
                if bp.get(key) is not None:
                    props[key] = _int(bp.get(key))
            for key in ("anchor", "wrap"):
                if bp.get(key):
                    props[key] = bp.get(key)
        own = body.find(A + "bodyPr")
        fit = None if own is None else own.find(A + "normAutofit")
        props["scale"] = _int(None if fit is None else fit.get("fontScale"), 100000) / 100000
        props["reduce"] = _int(None if fit is None else fit.get("lnSpcReduction"), 0) / 100000
        return props

    def text(self, body: ET.Element, sp: ET.Element | None, part: _Part, sid: str | None, cell: bool = False,
             override: dict | None = None) -> str:
        bp = self._body_props(sp, body)
        scale, reduce = bp["scale"], bp["reduce"]
        own_lst = body.find(A + "lstStyle")
        counters: dict[int, int] = {}
        paras = []
        for i, p in enumerate(body.findall(A + "p")):
            ppr = p.find(A + "pPr")
            level = min(max(_int(None if ppr is None else ppr.get("lvl")), 0), 8)
            style = _Para()
            for el in [*self._level_chain(sp, level, cell), _level(own_lst, level), ppr]:
                if el is not None:
                    style = self.para_props(style, el)
            if override:
                style.run = replace(style.run, **override)
            runs, raw = [], []
            for child in p:
                name = local(child.tag)
                if name in ("r", "fld"):
                    rpr, t = child.find(A + "rPr"), child.find(A + "t")
                    value = "" if t is None or t.text is None else t.text
                    run = self.run_props(style.run, rpr) if rpr is not None else style.run
                    runs.append(f'<span style="{self._run_css(run, scale)}">{html.escape(value)}</span>')
                    raw.append(value)
                elif name == "br":
                    runs.append('<span class="mg-nl"> </span>')
                    raw.append(" ")
            end = p.find(A + "endParaRPr")
            size_run = self.run_props(style.run, end) if end is not None and not raw else style.run
            size = size_run.size * scale * PT_PX
            text = "".join(raw)
            stripped = text.strip()
            line_h = 1.2 * (style.line[1] if style.line and style.line[0] == "pct" else 1.0) * (1 - reduce)
            css = [f"text-align:{_ALIGN.get(style.align, 'left')}", f"font-size:{_n(size)}px"]
            if style.line and style.line[0] == "pts":
                css.append(f"line-height:{_n(style.line[1] * PT_PX * scale)}px")
            else:
                css.append(f"line-height:{_n(line_h)}")
            if style.mar_l or style.indent:
                css.append(f"padding-left:{_n(style.mar_l / EMU_PER_PX)}px;text-indent:{_n(style.indent / EMU_PER_PX)}px")
            for attr, spacing in (("margin-top", style.before), ("margin-bottom", style.after)):
                if spacing:
                    value = spacing[1] * PT_PX * scale if spacing[0] == "pts" else spacing[1] * size * 1.2
                    css.append(f"{attr}:{_n(value)}px")
            bullet = ""
            if style.bullet and stripped:
                if style.bullet[0] == "auto":
                    counters[level] = counters.get(level, style.bullet[2] - 1) + 1
                    symbol = _autonum(style.bullet[1], counters[level])
                else:
                    symbol = style.bullet[1]
                bu_css = [f"font-size:{_n(size * style.bu_size)}px"]
                if style.bu_color or style.run.color:
                    bu_css.append(f"color:{style.bu_color or style.run.color}")
                if style.bu_font:
                    bu_css.append(f"font-family:{_font(style.bu_font)}")
                width = -style.indent / EMU_PER_PX
                if width > 0:
                    bu_css.append(f"min-width:{_n(width)}px")
                bullet = f'<span class="mg-bu" style="{";".join(bu_css)}">{html.escape(symbol)}</span>'
            else:
                counters.pop(level, None)
            attrs = ""
            if sid is not None and stripped:
                attrs = (f' data-key="{html.escape(sid)}:{i}" data-len="{len(stripped)}" '
                         f'data-lead="{len(text) - len(text.lstrip())}"')
            content = "".join(runs) if raw else "<br>"
            paras.append(f'<p class="mg-p"{attrs} style="{";".join(css)}">{bullet}<span class="mg-t">{content}'
                         "</span></p>")
        ins = [0.0] * 4 if cell else [bp[k] / EMU_PER_PX for k in ("tIns", "rIns", "bIns", "lIns")]  # célula: margem no td
        tb_css = [f"padding:{' '.join(_n(v) + 'px' for v in ins)}",
                  f"justify-content:{_ANCHOR.get(bp['anchor'], 'flex-start')}"]
        cls = ("mg-tc" if cell else "mg-tb") + (" mg-nowrap" if bp["wrap"] == "none" else "")
        return f'<div class="{cls}" style="{";".join(tb_css)}">{"".join(paras)}</div>'

    def _run_css(self, run: _Run, scale: float) -> str:
        size = run.size * scale * PT_PX
        css = [f"font-size:{_n(size * (0.7 if run.baseline else 1))}px"]
        if run.font:
            css.append(f"font-family:{_font(run.font)}")
            _, weight, italic = _font_parts(run.font)
            if weight and not run.bold:
                css.append(f"font-weight:{weight}")
            if italic and not run.italic:
                css.append("font-style:italic")
        if run.bold:
            css.append("font-weight:700")
        if run.italic:
            css.append("font-style:italic")
        deco = [d for d, on in (("underline", run.underline), ("line-through", run.strike)) if on]
        if deco:
            css.append(f"text-decoration:{' '.join(deco)}")
        if run.color:
            css.append(f"color:{run.color}")
        if run.caps:
            css.append("text-transform:uppercase")
        if run.baseline:
            css.append(f"vertical-align:{'super' if run.baseline > 0 else 'sub'}")
        return ";".join(css)


# ================================================ ligação com o texto
_PARA_KEY = re.compile(r'data-key="([^"]*)" data-len="(\d+)" data-lead="(\d+)"')
_ROW_KEY = re.compile(r'data-key="([^"]*)" data-row="1"')


def key_lines(stored: Extracted, slide: int, data: bytes | None = None) -> dict[str, int]:
    """Chave de cada parágrafo do slide → linha do texto extraído que está guardado na versão.

    As chaves (id da forma) só valem dentro de um slide. Versões registradas antes de o texto guardar as chaves:
    extrai de novo do arquivo e só usa as chaves novas se o texto for o mesmo.
    """

    def of(ext: Extracted) -> dict[str, int]:
        return {info["key"]: n for n, info in enumerate(ext.lines, start=1)
                if info.get("key") and info.get("page") == slide}

    keys = of(stored)
    if keys or data is None or any(info.get("key") for info in stored.lines):
        return keys
    fresh = extract("pptx", data)
    return of(fresh) if fresh.text == stored.text else {}


def bind_lines(slide_html: str, keys: dict[str, int], text: str) -> str:
    """Troca as chaves do slide pela linha do texto (``data-line``) e o tamanho do prefixo dela (``data-pre``)."""
    lines = text.split("\n")

    def para(match: re.Match) -> str:
        line = keys.get(html.unescape(match.group(1)))
        if not line or line > len(lines):
            return ""
        pre = max(0, len(lines[line - 1]) - int(match.group(2)))
        return f'data-line="{line}" data-pre="{pre}" data-lead="{match.group(3)}"'

    def row(match: re.Match) -> str:
        line = keys.get(html.unescape(match.group(1)))
        return f'data-line="{line}" data-row="1"' if line else ""

    return _ROW_KEY.sub(row, _PARA_KEY.sub(para, slide_html))
