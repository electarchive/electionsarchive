"""Text of PDFs whose ToUnicode maps are broken, read from glyph ids through each embedded font's own tables.

The Election Commission's local result books map many Devanagari glyphs to the wrong characters, or to none, in
their ToUnicode CMaps, so a text extractor drops or swaps conjuncts. This reader walks each page's content stream,
keeps the glyph id and position of every glyph drawn, and turns glyph ids back into characters with the embedded
TrueType font's cmap and the ligature and single substitutions in its GSUB table. A subset font that carries no
GSUB borrows the tables of the same font, matched by family and glyph count, from another file. Devanagari is
drawn in visual order, so the reader moves each short i vowel sign after its consonant cluster and each reph
before the cluster it sits on.
"""

import io
import re
import zlib
from collections.abc import Iterator
from dataclasses import dataclass

from fontTools.ttLib import TTFont

type Value = (
    int | float | bool | str | bytes | None | list["Value"] | dict[str, "Value"] | "Ref" | "Name" | "Stream" | "Op"
)


@dataclass(frozen=True)
class Ref:
    number: int


@dataclass(frozen=True)
class Name:
    text: str


@dataclass(frozen=True)
class Op:
    text: str


@dataclass
class Stream:
    head: dict[str, Value]
    raw: bytes


DELIM = b"()<>[]{}/%"
WHITE = b" \t\r\n\f\x00"
NUMBER = re.compile(rb"[+-]?(?:\d+\.?\d*|\.\d+)")


class Lexer:
    def __init__(self, data: bytes, pos: int = 0) -> None:
        self.data = data
        self.pos = pos

    def skip(self) -> None:
        data, n = self.data, len(self.data)
        while self.pos < n:
            c = data[self.pos]
            if c in WHITE:
                self.pos += 1
            elif c == 0x25:  # %
                while self.pos < n and data[self.pos] not in b"\r\n":
                    self.pos += 1
            else:
                break

    def value(self) -> Value:
        """The next object, or an Op for a bare keyword."""
        self.skip()
        data = self.data
        if self.pos >= len(data):
            raise EOFError
        c = data[self.pos : self.pos + 1]
        if c == b"/":
            start = self.pos + 1
            self.pos = start
            while self.pos < len(data) and data[self.pos] not in WHITE and data[self.pos] not in DELIM:
                self.pos += 1
            raw = data[start : self.pos]
            return Name(re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), raw).decode("latin1"))
        if c == b"<":
            if data[self.pos + 1 : self.pos + 2] == b"<":
                self.pos += 2
                out: dict[str, Value] = {}
                while True:
                    self.skip()
                    if data[self.pos : self.pos + 2] == b">>":
                        self.pos += 2
                        break
                    key = self.value()
                    if not isinstance(key, Name):
                        raise TypeError(f"dictionary key {key!r} at {self.pos}")
                    out[key.text] = self.value()
                if self._keyword_ahead(b"stream"):
                    return self._stream(out)
                return out
            end = data.index(b">", self.pos)
            hexa = re.sub(rb"\s", b"", data[self.pos + 1 : end])
            self.pos = end + 1
            if len(hexa) % 2:
                hexa += b"0"
            return bytes.fromhex(hexa.decode())
        if c == b"(":
            return self._literal()
        if c == b"[":
            self.pos += 1
            items: list[Value] = []
            while True:
                self.skip()
                if data[self.pos : self.pos + 1] == b"]":
                    self.pos += 1
                    return items
                items.append(self.value())
        if c in (b"]", b">", b")", b"{", b"}"):
            self.pos += 1
            return Op(c.decode())
        m = NUMBER.match(data, self.pos)
        if m:
            self.pos = m.end()
            text = m.group(0)
            if b"." not in text:
                number = int(text)
                save = self.pos
                after = re.compile(rb"\s+(\d+)\s+R(?![A-Za-z])").match(data, self.pos)
                if after and number >= 0:
                    self.pos = after.end()
                    return Ref(number)
                self.pos = save
                return number
            return float(text)
        start = self.pos
        while self.pos < len(data) and data[self.pos] not in WHITE and data[self.pos] not in DELIM:
            self.pos += 1
        if self.pos == start:
            self.pos += 1
        word = data[start : self.pos].decode("latin1")
        if word == "true":
            return True
        if word == "false":
            return False
        if word == "null":
            return None
        return Op(word)

    def _keyword_ahead(self, word: bytes) -> bool:
        save = self.pos
        self.skip()
        hit = self.data.startswith(word, self.pos)
        if not hit:
            self.pos = save
        return hit

    def _stream(self, head: dict[str, Value]) -> Stream:
        self.pos += len(b"stream")
        if self.data[self.pos : self.pos + 2] == b"\r\n":
            self.pos += 2
        elif self.data[self.pos : self.pos + 1] in (b"\n", b"\r"):
            self.pos += 1
        length = head.get("Length")
        if isinstance(length, int):
            raw = self.data[self.pos : self.pos + length]
            if not self.data[self.pos + length : self.pos + length + 20].lstrip().startswith(b"endstream"):
                length = None
        if not isinstance(length, int):
            end = self.data.index(b"endstream", self.pos)
            raw = self.data[self.pos : end].rstrip(b"\r\n")
            self.pos = end + len(b"endstream")
        else:
            self.pos += length
            self.pos = self.data.index(b"endstream", self.pos) + len(b"endstream")
        return Stream(head, raw)

    def _literal(self) -> bytes:
        data = self.data
        self.pos += 1
        depth = 1
        out = bytearray()
        escapes = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
        while True:
            c = data[self.pos]
            self.pos += 1
            if c == 0x5C:  # backslash
                e = data[self.pos]
                self.pos += 1
                if e in escapes:
                    out.append(escapes[e])
                elif 0x30 <= e <= 0x37:
                    digits = bytes([e])
                    while len(digits) < 3 and 0x30 <= data[self.pos] <= 0x37:
                        digits += bytes([data[self.pos]])
                        self.pos += 1
                    out.append(int(digits, 8) & 0xFF)
                elif e in b"\r\n":
                    if e == 13 and data[self.pos] == 10:
                        self.pos += 1
                else:
                    out.append(e)
            elif c == 0x28:
                depth += 1
                out.append(c)
            elif c == 0x29:
                depth -= 1
                if depth == 0:
                    return bytes(out)
                out.append(c)
            else:
                out.append(c)


def decode(stream: Stream) -> bytes:
    filters = stream.head.get("Filter")
    names = [filters] if isinstance(filters, Name) else filters if isinstance(filters, list) else []
    data = stream.raw
    for f in names:
        if isinstance(f, Name) and f.text == "FlateDecode":
            data = zlib.decompressobj().decompress(data)
        elif isinstance(f, Name):
            raise ValueError(f"unsupported filter {f.text}")
    return data


class Document:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offsets: dict[int, int] = {}
        for m in re.finditer(rb"(?<![0-9])(\d+)\s+(\d+)\s+obj\b", data):
            self.offsets[int(m.group(1))] = m.end()
        self.cache: dict[int, Value] = {}
        tail = data.rfind(b"trailer")
        trailer = Lexer(data, tail + len(b"trailer")).value()
        if not isinstance(trailer, dict):
            raise TypeError("no trailer dictionary")
        self.trailer = trailer

    def get(self, value: Value) -> Value:
        if isinstance(value, Ref):
            if value.number not in self.cache:
                self.cache[value.number] = Lexer(self.data, self.offsets[value.number]).value()
            return self.cache[value.number]
        return value

    def mapping(self, value: Value) -> dict[str, Value]:
        got = self.get(value)
        if isinstance(got, Stream):
            return got.head
        if not isinstance(got, dict):
            raise TypeError(f"expected a dictionary, got {type(got).__name__}")
        return got

    def pages(self) -> list[dict[str, Value]]:
        root = self.mapping(self.trailer["Root"])
        out: list[dict[str, Value]] = []

        def walk(node: dict[str, Value], inherited: dict[str, Value]) -> None:
            here = {**inherited, **{k: node[k] for k in ("Resources", "MediaBox") if k in node}}
            kind = node.get("Type")
            if isinstance(kind, Name) and kind.text == "Pages":
                kids = self.get(node["Kids"])
                if not isinstance(kids, list):
                    raise ValueError("Kids is not an array")
                for kid in kids:
                    walk(self.mapping(kid), here)
            else:
                out.append({**here, **node})

        walk(self.mapping(root["Pages"]), {})
        return out


@dataclass
class Glyph:
    font: Font
    gid: int
    x: float
    y: float
    width: float
    size: float


def _num(v: Value) -> float:
    if isinstance(v, bool) or not isinstance(v, int | float):
        raise TypeError(f"expected a number, got {v!r}")
    return float(v)


@dataclass
class Font:
    key: str
    family: str
    two_byte: bool
    widths: dict[int, float]
    default_width: float
    cid_to_gid: dict[int, int] | None
    tt: TTFont | None
    tounicode: dict[int, str]
    glyphs: int
    own: Tables | None = None

    def gid(self, code: int) -> int:
        if self.cid_to_gid is None:
            return code
        return self.cid_to_gid.get(code, code)


def _tounicode(data: bytes) -> dict[int, str]:
    out: dict[int, str] = {}

    def text(h: bytes) -> str:
        raw = bytes.fromhex(h.decode())
        return raw.decode("utf-16-be", errors="replace")

    for block in re.findall(rb"beginbfchar(.*?)endbfchar", data, re.DOTALL):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", block):
            out[int(src, 16)] = text(dst)
    for block in re.findall(rb"beginbfrange(.*?)endbfrange", data, re.DOTALL):
        for lo, hi, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]*>|\[[^\]]*\])", block):
            a, b = int(lo, 16), int(hi, 16)
            if dst.startswith(b"["):
                for i, item in enumerate(re.findall(rb"<([0-9A-Fa-f]*)>", dst)):
                    out[a + i] = text(item)
            else:
                first = text(dst[1:-1])
                for i in range(b - a + 1):
                    out[a + i] = first[:-1] + chr(ord(first[-1]) + i) if first else ""
    return out


def _family(tt: TTFont) -> str:
    name = tt.get("name")
    if name is None:
        return ""
    for nid in (4, 1):
        record = name.getName(nid, 3, 1) or name.getName(nid, 1, 0)
        if record is not None:
            return str(record.toUnicode())
    return ""


def load_font(doc: Document, ref: Value) -> Font:
    font = doc.mapping(ref)
    subtype = font.get("Subtype")
    two_byte = isinstance(subtype, Name) and subtype.text == "Type0"
    descriptor: dict[str, Value] = {}
    widths: dict[int, float] = {}
    default_width = 1000.0
    cid_to_gid: dict[int, int] | None = None
    if two_byte:
        descendants = doc.get(font["DescendantFonts"])
        if not isinstance(descendants, list):
            raise ValueError("DescendantFonts is not an array")
        cid = doc.mapping(descendants[0])
        descriptor = doc.mapping(cid["FontDescriptor"])
        default_width = _num(doc.get(cid.get("DW", 1000)))
        w = doc.get(cid.get("W", []))
        if isinstance(w, list):
            items = [doc.get(i) for i in w]
            i = 0
            while i < len(items):
                first = int(_num(items[i]))
                nxt = items[i + 1]
                if isinstance(nxt, list):
                    for j, width in enumerate(nxt):
                        widths[first + j] = _num(doc.get(width))
                    i += 2
                else:
                    last = int(_num(nxt))
                    width = _num(items[i + 2])
                    for code in range(first, last + 1):
                        widths[code] = width
                    i += 3
        mapping = doc.get(cid.get("CIDToGIDMap", Name("Identity")))
        if isinstance(mapping, Stream):
            raw = decode(mapping)
            cid_to_gid = {i: int.from_bytes(raw[2 * i : 2 * i + 2]) for i in range(len(raw) // 2)}
    else:
        descriptor = doc.mapping(font["FontDescriptor"]) if "FontDescriptor" in font else {}
        first = int(_num(doc.get(font.get("FirstChar", 0))))
        w = doc.get(font.get("Widths", []))
        if isinstance(w, list):
            for j, width in enumerate(w):
                widths[first + j] = _num(doc.get(width))
        default_width = 0.0
    tt: TTFont | None = None
    if "FontFile2" in descriptor:
        stream = doc.get(descriptor["FontFile2"])
        if isinstance(stream, Stream):
            try:
                tt = TTFont(io.BytesIO(decode(stream)), lazy=True)
            except Exception:
                tt = None
    tounicode: dict[int, str] = {}
    if "ToUnicode" in font:
        stream = doc.get(font["ToUnicode"])
        if isinstance(stream, Stream):
            tounicode = _tounicode(decode(stream))
    base = font.get("BaseFont")
    key = base.text if isinstance(base, Name) else ""
    family = _family(tt) if tt is not None else ""
    glyphs = len(tt.getGlyphOrder()) if tt is not None else 0
    return Font(key, family, two_byte, widths, default_width, cid_to_gid, tt, tounicode, glyphs)


def _mul(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, ...]:
    a0, a1, a2, a3, a4, a5 = a
    b0, b1, b2, b3, b4, b5 = b
    return (
        a0 * b0 + a1 * b2,
        a0 * b1 + a1 * b3,
        a2 * b0 + a3 * b2,
        a2 * b1 + a3 * b3,
        a4 * b0 + a5 * b2 + b4,
        a4 * b1 + a5 * b3 + b5,
    )


IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def glyphs(doc: Document, page: dict[str, Value], fonts: dict[int, Font]) -> Iterator[Glyph]:
    """Every glyph a page draws, with its origin in top-down page coordinates."""
    resources = doc.mapping(page.get("Resources", {}))
    font_refs = doc.mapping(resources.get("Font", {})) if "Font" in resources else {}
    box = doc.get(page.get("MediaBox", [0, 0, 612, 792]))
    height = _num(doc.get(box[3])) if isinstance(box, list) else 792.0
    named: dict[str, Font] = {}
    for name, ref in font_refs.items():
        number = ref.number if isinstance(ref, Ref) else id(ref)
        if number not in fonts:
            fonts[number] = load_font(doc, ref)
        named[name] = fonts[number]
    contents = doc.get(page.get("Contents"))
    parts = contents if isinstance(contents, list) else [contents]
    data = b"\n".join(decode(s) for s in (doc.get(p) for p in parts) if isinstance(s, Stream))
    lexer = Lexer(data)
    stack: list[Value] = []
    ctm = IDENTITY
    saved: list[tuple[tuple[float, ...], Font | None, float, float, float, float, float]] = []
    font: Font | None = None
    size = 1.0
    tm = IDENTITY
    tlm = IDENTITY
    tc = tw = 0.0
    th = 1.0
    leading = 0.0

    def show(text: bytes) -> Iterator[Glyph]:
        nonlocal tm
        if font is None:
            return
        step = 2 if font.two_byte else 1
        for i in range(0, len(text) - step + 1, step):
            code = int.from_bytes(text[i : i + step])
            w = font.widths.get(code, font.default_width) / 1000.0
            trm = _mul((size * th, 0.0, 0.0, size, 0.0, 0.0), _mul(tm, ctm))
            x, y = trm[4], trm[5]
            scale = (trm[0] ** 2 + trm[1] ** 2) ** 0.5 / max(size * th, 1e-9)
            yield Glyph(font, font.gid(code) if font.two_byte else code, x, height - y, w * size * scale, size)
            adv = (w * size + tc + (tw if step == 1 and code == 32 else 0.0)) * th
            tm = _mul((1.0, 0.0, 0.0, 1.0, adv, 0.0), tm)

    while True:
        try:
            v = lexer.value()
        except EOFError:
            break
        if not isinstance(v, Op):
            stack.append(v)
            continue
        op = v.text
        if op == "BI":
            end = re.compile(rb"\sEI(?=[\s])").search(data, lexer.pos)
            lexer.pos = end.end() if end else len(data)
        elif op == "q":
            saved.append((ctm, font, size, tc, tw, th, leading))
        elif op == "Q":
            if saved:
                ctm, font, size, tc, tw, th, leading = saved.pop()
        elif op == "cm":
            m = tuple(_num(x) for x in stack[-6:])
            ctm = _mul(m, ctm)
        elif op == "BT":
            tm = tlm = IDENTITY
        elif op == "Tf":
            ref = stack[-2]
            font = named.get(ref.text) if isinstance(ref, Name) else None
            size = _num(stack[-1])
        elif op == "Tm":
            tm = tlm = tuple(_num(x) for x in stack[-6:])
        elif op in ("Td", "TD"):
            tx, ty = _num(stack[-2]), _num(stack[-1])
            if op == "TD":
                leading = -ty
            tm = tlm = _mul((1.0, 0.0, 0.0, 1.0, tx, ty), tlm)
        elif op == "T*":
            tm = tlm = _mul((1.0, 0.0, 0.0, 1.0, 0.0, -leading), tlm)
        elif op == "TL":
            leading = _num(stack[-1])
        elif op == "Tc":
            tc = _num(stack[-1])
        elif op == "Tw":
            tw = _num(stack[-1])
        elif op == "Tz":
            th = _num(stack[-1]) / 100.0
        elif op == "Tj" and stack and isinstance(stack[-1], bytes):
            yield from show(stack[-1])
        elif op in ("'", '"') and stack and isinstance(stack[-1], bytes):
            tm = tlm = _mul((1.0, 0.0, 0.0, 1.0, 0.0, -leading), tlm)
            yield from show(stack[-1])
        elif op == "TJ" and stack and isinstance(stack[-1], list):
            for item in stack[-1]:
                if isinstance(item, bytes):
                    yield from show(item)
                elif isinstance(item, int | float) and not isinstance(item, bool):
                    tm = _mul((1.0, 0.0, 0.0, 1.0, -float(item) / 1000.0 * size * th, 0.0), tm)
        stack.clear()


# Devanagari shaping, undone.
VIRAMA = "\u094d"
I_SIGN = "\u093f"
RA = "\u0930"
REPH = "\x01"


def _is_sign(c: str) -> bool:
    return "\u0900" <= c <= "\u0903" or "\u093a" <= c <= "\u094f" or c in "\u0962\u0963"


def _is_consonant(c: str) -> bool:
    return "क" <= c <= "ह" or "क़" <= c <= "य़" or c == "ळ"


class Tables:
    """Glyph id to text for one font program, from its cmap and the substitutions in its GSUB."""

    def __init__(self, tt: TTFont, patch: dict[int, str] | None = None) -> None:
        order = tt.getGlyphOrder()
        index = {name: i for i, name in enumerate(order)}
        base: dict[int, str] = dict(patch or {})
        cmap = tt.get("cmap")
        if cmap is not None:
            for table in cmap.tables:
                if not table.isUnicode():
                    continue
                for cp, gname in sorted(table.cmap.items()):
                    gid = index.get(gname)
                    if gid is None or 0xE000 <= cp <= 0xF8FF:
                        continue
                    if gid not in base or (not ("ऀ" <= base[gid] <= "ॿ") and "ऀ" <= chr(cp) <= "ॿ"):
                        base[gid] = chr(cp)
        for gid, gname in enumerate(order):
            named = re.fullmatch(r"uni([0-9A-F]{4})", gname)
            if gid not in base and named:
                base[gid] = chr(int(named.group(1), 16))
        self.base = base
        index_of = {c: g for g, c in base.items()}
        self.derived: dict[int, list[int]] = {}
        self.reph: set[int] = set()
        below: set[int] = set()
        if "GSUB" in tt:
            gsub = tt["GSUB"].table
            features: dict[int, set[str]] = {}
            if gsub.FeatureList is not None:
                for record in gsub.FeatureList.FeatureRecord:
                    for li in record.Feature.LookupListIndex:
                        features.setdefault(li, set()).add(record.FeatureTag)
            lookups = gsub.LookupList.Lookup if gsub.LookupList is not None else []
            single: dict[int, list[int]] = {}
            for li, lookup in enumerate(lookups):
                tags = features.get(li, set())
                for sub in lookup.SubTable:
                    kind = lookup.LookupType
                    if kind == 7:
                        kind = sub.ExtSubTable.LookupType
                        sub = sub.ExtSubTable
                    if kind == 1:
                        for src, dst in sub.mapping.items():
                            if src in index and dst in index:
                                g = index[dst]
                                if g not in base and g not in self.derived and g not in single:
                                    single[g] = [index[src]]
                                if "rphf" in tags:
                                    self.reph.add(g)
                    elif kind == 4:
                        for first, ligs in sub.ligatures.items():
                            for lig in ligs:
                                parts = [first, *lig.Component]
                                if lig.LigGlyph in index and all(p in index for p in parts):
                                    g = index[lig.LigGlyph]
                                    if g not in base and (g not in self.derived or len(parts) < len(self.derived[g])):
                                        self.derived[g] = [index[p] for p in parts]
                                    if "rphf" in tags:
                                        self.reph.add(g)
                                    if tags & {"blwf", "vatu", "pstf"}:
                                        below.add(g)
            for g, parts in single.items():
                self.derived.setdefault(g, parts)
        # Fonts form the below-base ra from ra and virama after the shaper has moved it past the base; its
        # logical order is virama, then ra.
        for g in below:
            if [base.get(p) for p in self.derived.get(g, [])] == [RA, VIRAMA]:
                self.derived[g] = [index_of[VIRAMA], index_of[RA]]

    def text(self, gid: int, depth: int = 0) -> str | None:
        """A glyph's characters, with REPH standing for a reph."""
        if gid in self.base:
            return self.base[gid]
        if gid in self.reph:
            return REPH
        if depth > 8 or gid not in self.derived:
            return None
        parts = [self.text(p, depth + 1) for p in self.derived[gid]]
        if any(p is None for p in parts):
            return None
        return "".join(p for p in parts if p is not None)


# Glyphs a font draws for characters its cmap leaves out, read from the ToUnicode maps of the result books that
# carry them: Kalimati's nya (ञ) and three ASCII glyphs.
PATCHES = {("Kalimati", 696): {137: "\u091e", 35: "[", 37: "]", 45: "\u2013"}}


class Reader:
    """Glyph text across documents, sharing GSUB tables between subsets of one font program."""

    def __init__(self) -> None:
        self.tables: dict[tuple[str, int], Tables] = {}
        self.missing: dict[str, int] = {}

    def remember(self, font: Font) -> None:
        if font.tt is None or "GSUB" not in font.tt:
            return
        key = (font.family, font.glyphs)
        if key not in self.tables:
            self.tables[key] = Tables(font.tt, PATCHES.get(key, {}))

    def char(self, font: Font, gid: int) -> str:
        """A glyph's text, with REPH standing for a reph."""
        shared = self.tables.get((font.family, font.glyphs))
        if shared is not None:
            got = shared.text(gid)
            if got is not None:
                return got
        if font.tt is not None:
            if font.own is None:
                font.own = Tables(font.tt, PATCHES.get((font.family, font.glyphs), {}))
            got = font.own.text(gid)
            if got is not None:
                return got
        key = f"{font.family}:{gid}"
        self.missing[key] = self.missing.get(key, 0) + 1
        return font.tounicode.get(gid, "")


def logical(pieces: list[str]) -> str:
    """Devanagari text from glyph texts in visual order."""
    out: list[str] = []
    pending_i = False
    for text in pieces:
        if text == I_SIGN:
            pending_i = True
            continue
        for ch in text:
            if ch == REPH:
                # A reph is drawn after the syllable it opens: move it before that syllable's base, which is the
                # last consonant cluster or independent vowel before it, past any vowel signs.
                j = len(out)
                while j > 0 and _is_sign(out[j - 1]):
                    j -= 1
                if j > 0:
                    j -= 1
                    while j >= 2 and out[j - 1] == VIRAMA and _is_consonant(out[j - 2]):
                        j -= 2
                out[j:j] = [RA, VIRAMA]
                continue
            out.append(ch)
        if pending_i and out and _is_consonant(out[-1]):
            pending_i = False
            out.append(I_SIGN)
    if pending_i:
        out.append(I_SIGN)
    return "".join(out)
