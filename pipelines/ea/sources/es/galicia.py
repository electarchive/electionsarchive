import html
import re
import subprocess
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import slug
from ea.package import Draft
from ea.store import DATA, connect

SOURCE = "es.galicia"
VERSION = "1"
PUBLISHER = "Xunta de Galicia"
TITLE = "Eleccións ao Parlamento de Galicia: resultados (Xunta de Galicia, datos abertos)"
PORTAL = "https://abertos.xunta.gal/catalogo/administracion-publica"
DOG_PUBLISHER = "Xunta Electoral de Galicia"
DOG_TITLE = "Acordos da Xunta Electoral de Galicia sobre os resultados e os deputados proclamados electos (DOG)"
DOG_PORTAL = "https://www.xunta.gal/diario-oficial-galicia"
STATUTE = "Lei orgánica 1/1981, do 6 de abril, de Estatuto de autonomía de Galicia"
LAW = "Lei 8/1985, do 13 de agosto, de eleccións ao Parlamento de Galicia"
BASE = "https://abertos.xunta.gal/catalogo/administracion-publica/-/dataset/"
PARSED = DATA / "parsed" / SOURCE
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
ABROAD = 4
THRESHOLD = 0.05
PROVINCES = {"15": "A Coruña", "27": "Lugo", "32": "Ourense", "36": "Pontevedra"}
FOUR = ("001", "005", "009", "013"), ("003", "007", "011", "015")
ELECTIONS = {
    2001: ("0263", date(2001, 10, 21), (("001",), ())),
    2005: ("0261", date(2005, 6, 19), (("001",), ("003",))),
    2009: ("0260", date(2009, 3, 1), (("001",), ("003",))),
    2012: ("0048", date(2012, 10, 21), (("001",), ("003",))),
    2016: ("0371", date(2016, 9, 25), (("001",), ("003",))),
    2020: ("0426", date(2020, 7, 12), FOUR),
    2024: ("0656", date(2024, 2, 18), FOUR),
}
DOG = {
    2001: ("https://www.xunta.gal/dog/Publicados/2001/20011113/AnuncioF762_es.html", "DOG 219, 13 November 2001"),
    2005: (
        "https://www.juntaelectoralcentral.es/cs/jec/documentos/GALICIA_2005_Resultados.pdf",
        "DOG 134, 13 July 2005",
    ),
    2009: ("https://www.xunta.gal/dog/Publicados/2009/20090320/Anuncio10A4A_es.html", "DOG 55, 20 March 2009"),
    2012: (
        "https://www.xunta.gal/dog/Publicados/2012/20121107/AnuncioG2453-061112-1_es.html",
        "DOG 212, 7 November 2012",
    ),
    2016: (
        "https://www.xunta.gal/dog/Publicados/2016/20161011/AnuncioG2453-101016-0004_es.html",
        "DOG 194, 11 October 2016",
    ),
    2020: (
        "https://www.xunta.gal/dog/Publicados/2020/20200729/AnuncioG2453-280720-0003_gl.html",
        "DOG 151, 29 July 2020",
    ),
    2024: ("https://www.xunta.gal/dog/Publicados/2024/20240306/AnuncioG2453-050324-1_gl.html", "DOG 47, 6 March 2024"),
}
# The elections before the open-data files, from the Xunta Electoral de Galicia's acordos as the Junta Electoral
# Central keeps them: (election day, file, citation, two columns a page, threshold). The 1981 file is the Parliament
# bulletin's list of members, a scan without text, and that election has no acordo with results.
JEC = "https://www.juntaelectoralcentral.es/cs/jec/documentos/"
ACORDOS = {
    1985: (date(1985, 11, 24), "GALICIA_1985_ResultadosyElectos.pdf", "DOG 12, 17 January 1986", True, 0.03),
    1989: (date(1989, 12, 17), "GALICIA_1989_ResultadosyElectos.pdf", "DOG 28, 8 February 1990", False, 0.03),
    1993: (date(1993, 10, 17), "GALICIA_1993_ResultadosyElectos.pdf", "DOG 217, 11 November 1993", True, 0.05),
    1997: (date(1997, 10, 19), "GALICIA_1997_ResultadosyElectos.pdf", "DOG 218, 11 November 1997", True, 0.05),
}
ERRATA = {
    1997: (
        "GALICIA_1997_ResultadosErr.pdf",
        "DOG 222, 17 November 1997",
        {
            "Alberte Xullo Rodríguez Feixoo": "Alberte Xullo Rodríguez Feixóo",
            "Xosé Francisco Ferreira Abelleira": "Xosé Francisco Ferreiro Abelleira",
        },
    )
}
# The acordos of 1985 to 1993 are scans with a text layer; these are the words that layer misreads, the members' names
# checked against the page image and the list labels against the same list's label in the general results. In 1985
# the A Coruña list of the Partido Socialista Galego-Esquerda Galega reads "Partido Socialista de Galicia", and the
# general results count its votes under the Partido Socialista Galego.
OCR: dict[int, tuple[tuple[str, str], ...]] = {
    1985: (
        ("Partido Socialista de Galicia-", "Partido Socialista Galego-"),
        ("Movimiento Comunista", "Movimento Comunista"),
    ),
    1989: (
        ("Part id o Popu l ar", "Partido Popular"),
        ("Par tid o Popu l ar", "Partido Popular"),
        ("Fren t e Popu l ar G a l ega", "Frente Popular Galega"),
        ("dos ' Socialistas", "dos Socialistas"),
        ("Socialista -Galego", "Socialista Galego"),
        ("Trabaliadores", "Traballadores"),
        ("Partído", "Partido"),
        ("SociaGstas", "Socialistas"),
        ("González I..axe", "González Laxe"),
        ("Doeí Francisco", "Don Francisco"),
    ),
    1993: (
        ("TJnidade", "Unidade"),
        ("Esqueída", "Esquerda"),
        ("Ql. Júan", "01. Juan"),
        ("Antolin-Inis-Mariano", "Antolín-Luis-Mariano"),
        ("Francisco Julio Cerviño", "Francisco-Julio Cerviño"),
        ("Antonió Carro Fernándezvalmayor", "Antonio Carro Fernández-Valmayor"),
        ("Garcfa", "García"),
        ("Manuel, Vega", "Manuel Vega"),
        ("Pilar . Pedrosa", "Pilar Pedrosa"),
        ("1Vlaría Antonia Álvanez", "María Antonia Álvarez"),
    ),
}
SKIP_LINE = re.compile(
    r"(?i)d[il1]ar[il1t]o\s+oficial|oficial\s+de\s+galic|\bN[.\s]*[°ºoO0]\s*\d+\s*[-,L]|^[-–]?\s*[1IVX]+\s*[-–_]*$"
)
COUNTS = (
    ("electores", "electors"),
    ("votantes", "voters"),
    ("candidatura", "cand"),
    ("validos", "valid"),
    ("branco", "blank"),
    ("blanco", "blank"),
    ("nulos", "invalid"),
)
# Lists of the acordos that are parties the open-data files also name, by folded label; year 0 is every year.
KEYS = {
    (0, "partidopopular"): "pp",
    (0, "bloquenacionalistagalego"): "bng",
    (0, "coalicionbloquenacionalistagalego"): "bng",
    (0, "partidodossocialistasdegaliciapartidosocialistaobreroespanol"): "psdegpsoe",
    (0, "centrodemocraticoysocial"): "cds",
    (0, "partidocentrodemocraticoysocial"): "cds",
    (0, "partidohumanista"): "ph",
    (0, "frentepopulargalega"): "fpg",
    (0, "falangeespanoladelasjons"): "fedelasjons",
    (0, "partidodelosautonomosyprofesionales"): "autonomo",
    (0, "partidosocialydemocraticodederecho"): "sdd",
    (0, "izquierdaunida"): "iueu",
    (0, "esquerdaunida"): "iueu",
    (0, "partidocoaliciongalega"): "coaliciongalega",
    (1985, "partidodesocialistasdegaliciapartidosocialistaobreroespanol"): "psdegpsoe",
    (1989, "partidopopularencoalicionconcentristasdegalicia"): "ppcdg",
    (
        1989,
        "partidopopularinclueacoalicionelectoraldestepartidoconcentristasdegalicianacircunscricionprovincialdeourense",
    ): "pp",
}
# A general result that the acordo says includes another list's votes in one province.
GENERAL_INCLUDES = {(1989, "pp"): "ppcdg"}
FIXED = {
    "codprovincia": "province",
    "codcir": "province",
    "codmunicipio": "mun",
    "codcon": "mun",
    "municipio": "name",
    "concello": "name",
    "distrito": "district",
    "seccion": "section",
    "mesa": "mesa",
    "censototal": "electors",
    "totalvotos": "voters",
    "votostotais": "voters",
    "votosnulos": "invalid",
    "votosenbranco": "blank",
    "votosbrancos": "blank",
    "votosacandidaturas": "cand",
    "votoscandidaturas": "cand",
    "votosvalidos": "valid",
}
SKIP = {"provincia", "censo", "certifalta", "certifcorrec", "totalabstencion", "abstencion", "votoselectores"}
SKIP |= {"votosinterventores"}
ALIASES = {"esquerdaunidaanova": "age"}
FIELDS = ("electors", "voters", "invalid", "blank", "cand", "valid")


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return "".join(char for char in plain.lower() if char.isalnum())


def url(year: int, number: str) -> str:
    code = ELECTIONS[year][0]
    return f"{BASE}{code}/eleccions-parlamento-galicia-resultados-{year}/{number}/descarga-directa-ficheiro.csv"


def files(year: int) -> list[tuple[str, str]]:
    concello, mesa = ELECTIONS[year][2]
    return [("concello", number) for number in concello] + [("mesa", number) for number in mesa]


def fetch() -> None:
    for year in ELECTIONS:
        for _, number in files(year):
            raw.fetch(SOURCE, url(year, number), suffix=".csv")
        link = DOG[year][0]
        raw.fetch(SOURCE, link, suffix=".pdf" if link.endswith(".pdf") else ".html")
    for file in [entry[1] for entry in ACORDOS.values()] + [entry[0] for entry in ERRATA.values()]:
        raw.fetch(SOURCE, JEC + file, suffix=".pdf")


def number(text: str) -> int | None:
    text = text.strip()
    if not text:
        return None
    return int(text.removesuffix(",00").replace(".", ""))


def abbreviation(label: str) -> str:
    return label.split("(")[0].strip() or label


def full_name(label: str) -> str | None:
    match = re.match(r"^[^(]+\((.+)\)$", label)
    if match is None:
        return None
    inner = match.group(1)
    if ")(" in inner:
        inner = inner.split(")(", 1)[1]
    return " ".join(inner.split())


def read(year: int, number_: str) -> tuple[list[dict], list[dict]]:
    content = raw.cached(SOURCE, url(year, number_)).path.read_bytes()
    text = content.decode("cp850" if b"C\xa2d" in content[:80] else "latin-1")
    lines = [line.split(";") for line in text.splitlines()]
    header = [fold(cell) for cell in lines[0]]
    columns: dict[str, int] = {}
    last = 0
    for index, key in enumerate(header):
        if key in FIXED and FIXED[key] not in columns:
            columns[FIXED[key]] = index
            last = index
        elif key in SKIP or (key in FIXED and FIXED[key] in columns):
            last = index
        elif not key and index < 6:
            columns.setdefault("mesa", index)
    if "electors" not in columns:
        columns["electors"] = header.index("censo")
    parties = [
        (index, " ".join(lines[0][index].split())) for index in range(last + 1, len(header)) if lines[0][index].strip()
    ]
    units: list[dict] = []
    votes: list[dict] = []
    for cells in lines[1:]:
        cells = [cell.strip() for cell in cells]
        if len(cells) <= last or not cells[columns["province"]].isdigit():
            continue
        province = cells[columns["province"]].zfill(2)
        mun = cells[columns["mun"]].zfill(3)
        unit: dict = {"year": year, "province": province, "mun": mun, "name": cells[columns["name"]]}
        if "district" in columns:
            unit["district"] = cells[columns["district"]].zfill(2)
            unit["section"] = cells[columns["section"]].zfill(3)
            unit["mesa"] = cells[columns["mesa"]]
        elif "mesa" in columns and cells[columns["mesa"]]:
            match = re.fullmatch(r"(\d+)-(\d+)\s*-\s*([A-Z])", cells[columns["mesa"]])
            if match is None:
                raise ValueError(f"{year} {number_}: mesa {cells[columns['mesa']]!r}")
            unit["district"], unit["section"], unit["mesa"] = match[1].zfill(2), match[2].zfill(3), match[3]
        for field in FIELDS:
            unit[field] = number(cells[columns[field]]) if field in columns else None
        unit["column"] = unit["cand"]
        unit["cand"] = sum(number(cells[index]) or 0 for index, _ in parties)
        if unit["valid"] is None and unit["blank"] is not None:
            unit["valid"] = unit["cand"] + unit["blank"]
        if mun.startswith("99") and not any(unit[field] for field in FIELDS):
            continue
        units.append(unit)
        keys = {key: unit[key] for key in ("year", "province", "mun", "district", "section", "mesa") if key in unit}
        for index, label in parties:
            if (value := number(cells[index])) is not None:
                votes.append({**keys, "party": label, "key": fold(abbreviation(label)), "votes": value})
    return units, votes


def dog_lines(year: int) -> list[str]:
    path = raw.cached(SOURCE, DOG[year][0]).path
    if path.suffix == ".pdf":
        parts = []
        for page, left in ((2, 298), (3, 0), (3, 298)):
            parts.append(
                subprocess.run(
                    ["pdftotext", "-f", str(page), "-l", str(page), "-x", str(left), "-y", "0", "-W", "298"]
                    + ["-H", "819", str(path), "-"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout
            )
        text = "\n".join(parts)
    else:
        content = path.read_bytes()
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("latin-1")
        text = html.unescape(re.sub(r"<[^>]+>", "\n", text))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return [line for line in lines if line]


def elected(year: int) -> tuple[list[dict], dict[str, str]]:
    lines = dog_lines(year)
    names: dict[str, str] = {}
    for line in lines:
        if match := re.fullmatch(r"(?:– |-)?(?:Po(?:r|lo) (?:el |la )?)?(.+?) \(([^()]+)\):?", line):
            names.setdefault(fold(match[2]), match[1].strip())
    start = next(index for index, line in enumerate(lines) if re.match(r"(?i)relaci[oó]n de (diputad|deputad)", line))
    out: list[dict] = []
    province: str | None = None
    party: str | None = None
    pending = ""
    for line in lines[start + 1 :]:
        if re.match(r"(?i)^(\* )?provincia d", line) or re.match(r"(?i)^• provincia", line):
            province = next(code for code, label in PROVINCES.items() if fold(label.removeprefix("A ")) in fold(line))
            party = None
            continue
        if re.match(r"^(– |-)?Po(r|lo) ", line) or pending:
            pending = f"{pending} {line}".strip()
            if not pending.endswith(":"):
                continue
            match = re.search(r"\(([^()]+)\):$", pending)
            if match is None:
                raise ValueError(f"{year}: list header {pending!r}")
            party, pending = match[1], ""
            continue
        if match := re.fullmatch(r"(\d+)\. ?[–-]?([^\d].+?)\.?", line):
            if province is None or party is None:
                raise ValueError(f"{year}: member {line!r} outside a list")
            name = re.sub(r"^(Don|Doña|D\.|Dña\.) ", "", match[2].strip())
            out.append(
                {"year": year, "province": province, "dog_party": party, "position": int(match[1]), "name": name}
            )
            continue
        if re.match(r"^(A Coruña|Santiago de Compostela), \d", line) or line.startswith("IV."):
            break
    return out, names


def acordo_lines(year: int) -> list[tuple[int, str]]:
    """The acordo's lines from its results heading to its signature, each with its indent, read column by column
    where the page sets the acordo in two columns."""
    path = raw.cached(SOURCE, JEC + ACORDOS[year][1]).path
    info = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, check=True).stdout
    pages = int(next(line.split()[-1] for line in info.splitlines() if line.startswith("Pages:")))
    out: list[tuple[int, str]] = []
    for page in range(1, pages + 1):
        split = gutter(path, page) if ACORDOS[year][3] else 600
        for left, width in ((0, split), (split, 600 - split)):
            if width <= 0:
                continue
            text = subprocess.run(
                ["pdftotext", "-layout", "-f", str(page), "-l", str(page), "-x", str(left), "-y", "0", "-W", str(width)]
                + ["-H", "846", str(path), "-"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            for line in text.splitlines():
                plain = " ".join(line.split())
                for wrong, right in OCR.get(year, ()):
                    plain = plain.replace(wrong, right)
                if plain and not SKIP_LINE.search(plain):
                    out.append((len(line) - len(line.lstrip()), plain))
    start = next(index for index, (_, line) in enumerate(out) if re.search(r"RESULTADOS (XERAIS|GENERALES)$", line))
    end = next(
        index
        for index, (_, line) in enumerate(out)
        if index > start and re.match(r"^(A Coruña|Santiago de Compostela)[,.]+ \d+ de ", line)
    )
    return out[start:end]


def gutter(path: Path, page: int) -> int:
    """The x between a page's two columns: the point near the middle that the fewest words cross."""
    text = subprocess.run(
        ["pdftotext", "-bbox", "-f", str(page), "-l", str(page), str(path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    spans = [(float(a), float(b)) for a, b in re.findall(r'<word xMin="([\d.]+)" yMin="[\d.]+" xMax="([\d.]+)"', text)]
    crossing = {x: sum(a < x < b for a, b in spans) for x in range(250, 340)}
    least = min(crossing.values())
    quiet = [x for x, count in crossing.items() if count == least]
    return round(sum(quiet) / len(quiet))


def province_of(line: str) -> str | None:
    match = re.search(r"(?i)provincia\s+d[ae]\s+(?:a\s+)?(coruña|lugo|ourense|pontevedra)", line)
    if match is None:
        return None
    return next(code for code, label in PROVINCES.items() if fold(label.removeprefix("A ")) == fold(match[1]))


def acordo(year: int) -> tuple[list[dict], list[dict]]:
    """The results and the members proclaimed elected in one acordo, as (figures, members). A figure is a ballot count
    (`field`) or a list's votes (`label`) for a province, or for all Galicia (province "00")."""
    lines = acordo_lines(year)
    split = next(
        index for index, (_, line) in enumerate(lines) if re.match(r"(?i)^RELACI[OÓ]N DE (DEPUTADOS|DIPUTADOS)", line)
    )
    figures: list[dict] = []
    province = "00"
    pending: list[tuple[int, str]] = []
    for indent, line in lines[:split]:
        if line.startswith("NOTAS"):
            break
        if (code := province_of(line)) is not None:
            province, pending = code, []
            continue
        match = re.fullmatch(r"(.*?)[\s.:…-]*((?<!\w)\d{1,3}(?:\s*\.\s*\d{3})*)\s*\.?\s*[-–(]?", line)
        if match is None:
            pending = [*pending[-1:], (indent, line)]
            continue
        label = match[1].strip(" *.:")
        if pending:
            last_indent, last = pending[-1]
            if (
                not label
                or label[0].islower()
                or label[0] in "(/-–"
                or last.endswith(("-", "–"))
                or indent > last_indent
            ):
                label = join(last, label)
                if len(pending) > 1 and pending[-1][0] > pending[0][0]:
                    label = join(pending[0][1], label)
        pending = []
        value = int(re.sub(r"\D", "", match[2]))
        label = label.strip(" *.:")
        key = fold(label)
        field = next((field for pattern, field in COUNTS if pattern in key), None)
        if field is not None:
            figures.append({"year": year, "province": province, "field": field, "label": None, "votes": value})
        else:
            figures.append({"year": year, "province": province, "field": None, "label": label, "votes": value})
    members: list[dict] = []
    province, party, header = "00", None, ""
    for _, line in lines[split + 1 :]:
        if (code := province_of(line)) is not None:
            province, party, header = code, None, ""
            continue
        match = re.fullmatch(r"([0-9Q]{1,2})\s*[.:_=,]+\s*[-–_=]*\s*(?:(?:Don|Dona|Doña)\s+)?(.+?)[.`+]?", line)
        starts = re.match(r"(?i)^(?:[A-D1-9][.)]?\s*[-–.,]*\s*)?[-–]?\s*Po(?:r|lo|la)\b", line)
        if starts or (header and match is None):
            header = join(header, line)
            if not header.endswith(":"):
                continue
        if header:
            party = re.sub(r"(?i)^.*?\bPo(?:r|lo|la)\b[\s,]*", "", header).strip(" :")
            header = ""
        if match is None or starts:
            continue
        if party is None or province == "00":
            raise ValueError(f"{year}: member {line!r} outside a list")
        name = " ".join(match[2].replace(" ,", ",").split()).rstrip(".")
        name = ERRATA[year][2].get(name, name) if year in ERRATA else name
        members.append(
            {
                "year": year,
                "province": province,
                "dog_party": party,
                "position": int(match[1].replace("Q", "0")),
                "name": name,
            }
        )
    return figures, members


def join(head: str, tail: str) -> str:
    if not head:
        return tail
    if not tail:
        return head
    if head.endswith("-") and tail[0].islower():
        return head[:-1] + tail
    if head.endswith(("-", "–", "/")) or tail[0] in "/-–":
        return head + tail
    return f"{head} {tail}"


def acordo_key(year: int, label: str) -> str:
    key = fold(label)
    return KEYS.get((year, key)) or KEYS.get((0, key)) or key


def header_key(year: int, header: str, keys: set[str]) -> str:
    """The list a heading of the members' relation names, among the province's lists."""
    text = re.sub(r"(?i)^(el|la|a|o)\s+", "", header)
    for candidate in (text, re.sub(r"(?i)^coalición(\s+electoral)?\s+", "", text)):
        if (key := acordo_key(year, candidate)) in keys:
            return key
    raise ValueError(f"{year}: no list for the heading {header!r} among {sorted(keys)}")


def acordos(tables: dict[str, list[dict]], full: dict[str, str]) -> None:
    """Parse each acordo and check it: the lists add up to the votes to candidacies, the counts add up, the provinces
    add up to the general results, and D'Hondt over the province's lists gives the members proclaimed."""
    for year, (_, _, _, _, threshold) in ACORDOS.items():
        figures, members = acordo(year)
        counts: dict[str, dict[str, int]] = defaultdict(dict)
        lists: dict[str, Counter[str]] = defaultdict(Counter)
        for row in figures:
            if row["field"] is not None:
                if row["field"] in counts[row["province"]]:
                    raise ValueError(f"{year} {row['province']}: {row['field']} twice")
                counts[row["province"]][row["field"]] = row["votes"]
                tables["acordo_count"].append(row)
                continue
            row["key"] = acordo_key(year, row["label"])
            if row["key"] in lists[row["province"]]:
                raise ValueError(f"{year} {row['province']}: {row['label']} twice")
            lists[row["province"]][row["key"]] = row["votes"]
            if row["province"] != "00":
                full.setdefault(row["key"], row["label"])
                tables["acordo_votes"].append(row)
        for field in ("electors", "voters", "valid", "cand", "blank", "invalid"):
            if (
                counts["00"].get(field) != sum(counts[code].get(field) or 0 for code in PROVINCES)
                and field in counts["00"]
            ):
                raise ValueError(f"{year}: the provinces' {field} differ from the general results")
        for key, votes in lists["00"].items():
            parts = [key, *([GENERAL_INCLUDES[year, key]] if (year, key) in GENERAL_INCLUDES else [])]
            if votes != sum(lists[code][part] for code in PROVINCES for part in parts):
                raise ValueError(f"{year}: the provinces' votes for {key} differ from the general results")
        if sum(lists["00"].values()) != sum(sum(lists[code].values()) for code in PROVINCES):
            raise ValueError(f"{year}: the provinces' list votes differ from the general results")
        for member in members:
            member["key"] = header_key(year, member["dog_party"], set(lists[member["province"]]))
        tables["acordo_elected"].extend(members)
        seats = Counter((row["province"], row["key"]) for row in members)
        for code in PROVINCES:
            total, part = counts[code], lists[code]
            listed = sum(part.values())
            won = dhondt(dict(part), total["valid"], sum(n for (p, _), n in seats.items() if p == code), threshold)
            wrong = sorted(key for key in part if won[key] != seats[code, key])
            if wrong:
                raise ValueError(f"{year} {code}: D'Hondt differs from the members proclaimed for {wrong}")
            print(
                year,
                code,
                f"seats={sum(won.values())}",
                f"voters={total['voters']}",
                f"lists={listed} cand={total.get('cand')}",
                f"ballots_off={total['voters'] - total['valid'] - total['invalid']}" if "invalid" in total else "",
                "dhondt_differs=[]",
            )


def dhondt(votes: dict[str, int], valid: int, seats: int, threshold: float = THRESHOLD) -> Counter[str]:
    eligible = {code: count for code, count in votes.items() if count >= threshold * valid}
    quotients = sorted(
        ((count / divisor, count, code) for code, count in eligible.items() for divisor in range(1, seats + 1)),
        reverse=True,
    )
    return Counter(code for *_, code in quotients[:seats])


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    tables: dict[str, list[dict]] = defaultdict(list)
    full: dict[str, str] = {}
    for year in ELECTIONS:
        for kind, number_ in files(year):
            units, votes = read(year, number_)
            tables[kind].extend(units)
            tables[f"{kind}_votes"].extend(votes)
        totals: Counter[tuple[str, str]] = Counter()
        for row in tables["concello_votes"]:
            if row["year"] == year:
                totals[row["province"], row["key"]] += row["votes"]
        tallies: Counter[tuple[str, str]] = Counter()
        for row in tables["mesa_votes"]:
            if row["year"] == year:
                tallies[row["province"], row["key"]] += row["votes"]
        for province, key in sorted(tallies.keys() | totals.keys()):
            if tallies and tallies[province, key] != totals[province, key]:
                tables["mesa_dropped"].append(
                    {
                        "year": year,
                        "province": province,
                        "key": key,
                        "mesa": tallies[province, key],
                        "concello": totals[province, key],
                    }
                )
        dropped = {(row["province"], row["key"]) for row in tables["mesa_dropped"] if row["year"] == year}
        tables["concello_votes"] = [
            row for row in tables["concello_votes"] if row["year"] != year or totals[row["province"], row["key"]] > 0
        ]
        tables["mesa_votes"] = [
            row
            for row in tables["mesa_votes"]
            if row["year"] != year
            or (totals[row["province"], row["key"]] > 0 and (row["province"], row["key"]) not in dropped)
        ]
        kept: Counter[tuple[str, str, str, str, str]] = Counter()
        for row in tables["mesa_votes"]:
            if row["year"] == year:
                kept[row["province"], row["mun"], row["district"], row["section"], row["mesa"]] += row["votes"]
        for row in tables["mesa"]:
            if row["year"] == year:
                row["cand"] = kept[row["province"], row["mun"], row["district"], row["section"], row["mesa"]]
        labels = {row["party"] for row in tables["concello_votes"] if row["year"] == year}
        for label in labels:
            if name := full_name(label):
                full[fold(abbreviation(label))] = name
        members, dog_names = elected(year)
        for key, name in dog_names.items():
            full.setdefault(key, name)
        by_key = {
            (row["province"], row["key"]): row["party"] for row in tables["concello_votes"] if row["year"] == year
        }
        for member in members:
            key = fold(member["dog_party"])
            member["key"] = ALIASES.get(key, key)
            member["party"] = by_key[member["province"], member["key"]]
        tables["elected"].extend(members)
        counts = Counter((row["province"], row["party"]) for row in members)
        place_votes = [row for row in tables["concello_votes"] if row["year"] == year]
        print(year, "seats", sum(counts.values()))
        for province in PROVINCES:
            part: Counter[str] = Counter()
            for row in place_votes:
                if row["province"] == province:
                    part[row["party"]] += row["votes"]
            places = [row for row in tables["concello"] if row["year"] == year and row["province"] == province]
            valid = sum(row["valid"] for row in places)
            cand = sum(row["column"] for row in places)
            seats = sum(count for (code, _), count in counts.items() if code == province)
            won = dhondt(dict(part), valid, seats)
            wrong = sorted(p for p in set(part) if won[p] != counts[province, p])
            mesa = sum(
                row["votes"] for row in tables["mesa_votes"] if row["year"] == year and row["province"] == province
            )
            print(
                year,
                province,
                f"seats={seats}",
                f"voters={sum(row['voters'] for row in places)}",
                f"lists={sum(part.values())} cand={cand}",
                f"mesa={mesa}",
                f"dhondt_differs={wrong}",
            )
    acordos(tables, full)
    tables["party"] = [{"key": key, "name": name} for key, name in sorted(full.items())]
    for name, rows in tables.items():
        frame = pl.DataFrame(rows, infer_schema_length=None)
        frame.write_parquet(PARSED / f"{name}.parquet")
        print(f"{name}: {frame.height} rows")


SYSTEM = (
    "Closed party lists in four constituencies, the provinces of A Coruña, Lugo, Ourense, and Pontevedra, which elect "
    "75 members: ten each, and the rest shared by population. Seats go by the D'Hondt method to the lists with at "
    "least 5% of the valid votes in the province, blank ballots counted as valid (" + LAW + "; " + STATUTE + "). "
    "The Xunta Electoral de Galicia publishes the members the provincial boards proclaimed in the Diario Oficial de "
    "Galicia, and those are the declared seats stored here."
)
SYSTEM_1985 = (
    "Closed party lists in four constituencies, the provinces of A Coruña, Lugo, Ourense, and Pontevedra. Seats go by "
    "the D'Hondt method to the lists with at least 3% of the valid votes in the province, blank ballots counted as "
    "valid (" + LAW + ", as first enacted; " + STATUTE + "). The Parliament had 71 members at the 1985 election and 75 "
    "from 1989, and the threshold became 5% from the 1993 election. The Xunta Electoral de Galicia publishes the "
    "members the provincial boards proclaimed in the Diario Oficial de Galicia, and those are the declared seats stored "
    "here."
)
ACORDO_METHOD = (
    "Ballot counts and list votes by province as the Xunta Electoral de Galicia published them in its acordo on the "
    "results and the members proclaimed elected ({dog}), from the copy the Junta Electoral Central keeps; the "
    "acordo's figures by province add up to its general results. Valid ballots here are the votes to the lists, with "
    "blank ballots stored apart. The seats and members are the acordo's, and the D'Hondt method over its list votes "
    "gives the same seats.{extra}"
)
ACORDO_NO_BLANK = (
    " The acordo gives electors, voters, and valid votes but no blank or null ballots, so blank ballots here are its "
    "valid votes less its list votes, and null ballots its voters less its valid votes."
)
ACORDO_CANDIDACIES = (
    " The acordo gives {cand:,} votes to candidacies in this province against {lists:,} in its lists, a difference its "
    "notes say the provincial board's records carry; the list votes are stored."
)
ACORDO_BALLOTS = (
    " The acordo's voters exceed its valid and null ballots by {off:,} in this province; the figures are stored as "
    "published."
)
ACORDO_COURT = (
    " The acordo's notes say the Ourense figures include the changes of the Tribunal Superior de Xustiza de Galicia's "
    "judgment of 15 January 1990 in an electoral appeal, which affect only valid votes of two lists."
)
ACORDO_ERRATA = " Members' names follow the correction of the acordo ({errata})."
PROVINCE_METHOD = (
    "Ballot counts and list votes by province as the Xunta Electoral de Galicia published them in its acordo on the "
    "results and the members proclaimed elected ({dog}). The figures are the sums of the Xunta de Galicia's results "
    "by concello, votes of residents abroad (CERA) included, which equal the acordo's voters and votes to candidacies "
    "for every province. Valid ballots here are the votes to candidacies, with blank ballots stored apart. The seats "
    "and members are the acordo's."
)
CANDIDACIES_DIFFER = (
    " The file's column of votes to candidacies adds up to {column:,} in this province, against {lists:,} in its list "
    "columns and in the acordo; the list votes are stored."
)
CONCELLO_METHOD = (
    "List votes and ballot counts by concello from the Xunta de Galicia's open-data results files, with the votes of "
    "residents abroad (CERA) on a separate row of the province. The concello figures add up to {votes:,} list votes, "
    "against {official:,} in the province results."
)
MESA_METHOD = (
    "List votes and ballot counts by polling station (mesa) from the Xunta de Galicia's open-data results files, each "
    "mesa on its census section where the 2025 INE section map has the same code, and otherwise on its concello; the "
    "votes of residents abroad (CERA) are one row per province. The mesa figures add up to {votes:,} list votes, "
    "against {official:,} in the province results{gap}.{inconsistent}"
)
MESA_INCONSISTENT = (
    " In {count:,} mesas the file's ballots cast differ from its valid, null, and blank ballots by up to {most} "
    "ballots; the figures are stored as published."
)
MESA_GAP = ", and the files give no unit for the difference"
MESA_DROPPED = (
    " The mesa file's column for {label} adds up to {mesa:,} votes in this province, against {concello:,} in the "
    "concello file, so its mesa figures are not stored."
)


def emit() -> Path:
    places = pl.read_parquet(PARSED / "concello.parquet")
    place_votes = pl.read_parquet(PARSED / "concello_votes.parquet")
    mesas = pl.read_parquet(PARSED / "mesa.parquet")
    mesa_votes = pl.read_parquet(PARSED / "mesa_votes.parquet")
    members = pl.read_parquet(PARSED / "elected.parquet")
    names = dict(pl.read_parquet(PARSED / "party.parquet").iter_rows())
    dropped = pl.read_parquet(PARSED / "mesa_dropped.parquet")
    acordo_counts = pl.read_parquet(PARSED / "acordo_count.parquet")
    acordo_votes = pl.read_parquet(PARSED / "acordo_votes.parquet")
    acordo_members = pl.read_parquet(PARSED / "acordo_elected.parquet")
    with connect() as conn:
        cs = Draft(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Parlamento de Galicia elections of 2001 to 2024 by province, concello, and polling station, with the "
                "members proclaimed elected, from the Xunta de Galicia's results files and the Xunta Electoral de "
                "Galicia's acordos in the Diario Oficial de Galicia"
            ),
            polity="ES",
        )
        cs.accept(
            "accounting.ballots_sum",
            "the Xunta's mesa files give ballots cast that differ from the valid, null, and blank ballots in some mesas, "
            "and the 1989 and 1993 acordos do so in Ourense and Pontevedra, as each result set's note says; the "
            "figures are stored as published",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        spain, galicia = cs.polity("ES"), cs.polity("ES-GA")
        for scheme in ("es.provincia", "es.municipio", "es.seccion", "es.mesa"):
            if conn.execute("select 1 from scheme where code = %s", (scheme,)).fetchone() is None:
                raise ValueError("publish es.ine and es.referendums before the Parlamento de Galicia")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PORTAL,
            license="Creative Commons Attribution-ShareAlike 4.0",
            license_url="https://creativecommons.org/licenses/by-sa/4.0/",
            attribution="Fonte: Xunta de Galicia, abertos.xunta.gal (CC BY-SA 4.0).",
        )
        dog = cs.source(DOG_PUBLISHER, DOG_TITLE)
        cs.add(
            "source",
            id=dog,
            kind="official_body",
            publisher=DOG_PUBLISHER,
            title=DOG_TITLE,
            url=DOG_PORTAL,
            license=None,
            license_url=None,
            attribution="Fonte: Xunta Electoral de Galicia, Diario Oficial de Galicia.",
        )
        for year in ELECTIONS:
            for _, number_ in files(year):
                cs.artifact(raw.cached(SOURCE, url(year, number_)), source)
            cs.artifact(raw.cached(SOURCE, DOG[year][0]), dog)
        for file in [entry[1] for entry in ACORDOS.values()] + [entry[0] for entry in ERRATA.values()]:
            cs.artifact(raw.cached(SOURCE, JEC + file), dog)

        parties: dict[str, UUID] = {}
        labels = sorted(place_votes.select("key", "party").unique(subset="key", keep="last").iter_rows())
        known = {key for key, _ in labels}
        labels += sorted(
            (key, label)
            for key, label in acordo_votes.select("key", "label").unique(subset="key", keep="first").iter_rows()
            if key not in known
        )
        for key, label in labels:
            parties[key] = cs.key(f"{SOURCE}.party", key)
            # A party merged into another pipeline's party is that pipeline's row; only the pipeline's own are written.
            if parties[key] in cs.minted:
                cs.add("party", id=parties[key], kind="party", polity_id=galicia)
                name(parties[key], names.get(key) or abbreviation(label), "es")

        body = cs.key("es.body", "galician-parliament")
        cs.add("body", id=body, polity_id=galicia, kind="unicameral")
        name(body, "Parliament of Galicia")
        name(body, "Parlamento de Galicia", "gl", "native")
        system = cs.key("es.electoral_system", "galician-parliament")
        cs.add(
            "electoral_system",
            id=system,
            polity_id=galicia,
            rules={
                "formula": "dhondt",
                "lists": {"order": "closed", "vacancy": "next_on_list"},
                "threshold": {"level": "list", "scope": "contest", "percent": 5},
            },
            rules_version=1,
        )
        name(system, "Parliament of Galicia closed-list D'Hondt system with a 5% threshold")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)
        cs.add("body_size", body_id=body, seats=71, valid_from=ACORDOS[1985][0], valid_to=ACORDOS[1989][0])
        cs.add("body_size", body_id=body, seats=75, valid_from=ACORDOS[1989][0], valid_to=None)
        system_1985 = cs.key("es.electoral_system", "galician-parliament-1985")
        cs.add(
            "electoral_system",
            id=system_1985,
            polity_id=galicia,
            rules={
                "formula": "dhondt",
                "lists": {"order": "closed", "vacancy": "next_on_list"},
                "threshold": {"level": "list", "scope": "contest", "percent": 3},
            },
            rules_version=1,
        )
        name(system_1985, "Parliament of Galicia closed-list D'Hondt system with a 3% threshold")
        cs.add("note", entity_id=system_1985, kind="methodology", body_md=SYSTEM_1985)

        municipality_areas = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'es.municipio'"
            ).fetchall()
        )
        section_areas = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'es.seccion'"
            ).fetchall()
        )
        unit_rows: dict[UUID, tuple[UUID, str, str, UUID | None, str, UUID | None, str]] = {}
        province_units: dict[str, UUID] = {}
        for code in PROVINCES:
            unit = province_units[code] = cs.unit("es.provincia", code)
            unit_rows[unit] = (unit, "es.provincia", code, None, "equals", cs.area("es.provincia", code), "standard")
        municipality_units: dict[str, UUID] = {}

        def municipality_unit(province: str, mun: str) -> UUID:
            code = province + ("999" if mun.startswith("99") else mun)
            if code not in municipality_units:
                unit = municipality_units[code] = cs.unit("es.municipio", code)
                if mun.startswith("99"):
                    area, relation, kind = cs.area("es.provincia", province), "within", "aggregate"
                else:
                    area = municipality_areas.get(code)
                    relation, kind = ("equals" if area is not None else "unlocated"), "standard"
                unit_rows[unit] = (unit, "es.municipio", code, province_units[province], relation, area, kind)
            return municipality_units[code]

        values: list[tuple[str, str, str | None, int, int, int]] = []

        def accounting(result_set: UUID, unit: UUID, row: dict[str, int | None], channel: int = 0) -> None:
            for measure, field in (
                (REGISTERED, "electors"),
                (BALLOTS, "voters"),
                (VALID, "cand"),
                (INVALID, "invalid"),
                (BLANK, "blank"),
            ):
                if (value := row[field]) is not None:
                    values.append((str(result_set), str(unit), None, measure, value, channel))

        for year, (_, day, _) in ELECTIONS.items():
            election = cs.key("es.election", f"galicia:{day}")
            cs.add(
                "election", id=election, polity_id=galicia, kind="general", date_from=day, date_to=day, status="held"
            )
            name(election, f"{year} Galician regional election")
            name(election, f"Eleccións ao Parlamento de Galicia de {year}", "gl", "native")
            path = f"es/ga/{day}-general"
            cs.add("slug", path=path, entity_id=election)
            stamp = f"{year}{day.month:02d}"
            for province, province_name in PROVINCES.items():
                where = (pl.col("year") == year) & (pl.col("province") == province)
                rows = (
                    place_votes.filter(where)
                    .group_by("party", "key")
                    .agg(pl.col("votes").sum())
                    .sort("votes", "party", descending=[True, False])
                )
                provincial = places.filter(where)
                declared = members.filter(where)
                seats_by_party = Counter(declared["key"].to_list())
                key = f"{day}:{province}"
                contest = cs.key(f"{SOURCE}.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="list_allocation",
                    body_id=body,
                    district_area_id=cs.area("es.provincia", province),
                    magnitude=declared.height,
                    electoral_system_id=system,
                    default_result_kind="counted",
                )
                name(contest, province_name)
                cs.add("slug", path=f"{path}/{slug(province_name)}", entity_id=contest)
                options: dict[str, UUID] = {}
                for entry in rows.iter_rows(named=True):
                    option = options[entry["key"]] = cs.key(f"{SOURCE}.option", f"{key}:{entry['key']}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        party_id=parties[entry["key"]],
                        ballot_label=entry["party"],
                    )
                official = int(rows["votes"].sum())

                def make_set(label: str, default: bool, completeness: float, note: str, origin: UUID) -> UUID:
                    result_set = cs.key(f"{SOURCE}.result_set", f"{key}:{label}")
                    cs.add(
                        "result_set",
                        id=result_set,
                        election_id=election,
                        contest_id=contest,
                        stage="official",
                        kind="counted",
                        status="active",
                        source_id=origin,
                        is_default=default,
                        completeness=completeness,
                    )
                    cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
                    return result_set

                note = PROVINCE_METHOD.format(dog=DOG[year][1])
                column = int(provincial["column"].fill_null(provincial["cand"]).sum())
                if column != official:
                    note += CANDIDACIES_DIFFER.format(column=column, lists=official)
                province_set = make_set("province", True, 1, note, dog)
                unit = province_units[province]
                total: dict[str, int | None] = {field: int(provincial[field].sum()) for field in FIELDS}
                total["cand"] = official
                accounting(province_set, unit, total)
                for entry in rows.iter_rows(named=True):
                    values.append((str(province_set), str(unit), str(options[entry["key"]]), VOTES, entry["votes"], 0))
                    if seats := seats_by_party.get(entry["key"], 0):
                        cs.add(
                            "outcome",
                            result_set_id=province_set,
                            option_id=options[entry["key"]],
                            kind="won",
                            seats=seats,
                        )
                seen: Counter[str] = Counter()
                for member in declared.iter_rows(named=True):
                    person_key = f"{key}:{member['key']}:{fold(member['name'])}"
                    seen[person_key] += 1
                    if seen[person_key] > 1:
                        person_key += f":{seen[person_key]}"
                    person = cs.key(f"{SOURCE}.person", person_key)
                    plain = re.sub(r"\s*\([^)]*\)$", "", member["name"])
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, plain, "es")
                    party = parties[member["key"]]
                    option = cs.key(f"{SOURCE}.option", person_key)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        parent_option_id=options[member["key"]],
                        party_id=party,
                        ballot_label=member["name"],
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", person_key),
                        person_id=person,
                        option_id=option,
                        role="list_member",
                        ballot_name=member["name"],
                        member_of_party_id=party,
                        is_independent=False,
                    )
                    cs.add("outcome", result_set_id=province_set, option_id=option, kind="elected")

                vote_rows = place_votes.filter(where)
                got = int(vote_rows["votes"].sum())
                concello_set = make_set(
                    "concello",
                    False,
                    min(got / official, 1),
                    CONCELLO_METHOD.format(votes=got, official=official),
                    source,
                )
                for row in provincial.iter_rows(named=True):
                    abroad = ABROAD if row["mun"].startswith("99") else 0
                    accounting(concello_set, municipality_unit(province, row["mun"]), row, abroad)
                for row in vote_rows.iter_rows(named=True):
                    values.append(
                        (
                            str(concello_set),
                            str(municipality_unit(province, row["mun"])),
                            str(options[row["key"]]),
                            VOTES,
                            row["votes"],
                            ABROAD if row["mun"].startswith("99") else 0,
                        )
                    )

                units = mesas.filter(where)
                if units.is_empty():
                    continue
                unit_votes = mesa_votes.filter(where)
                got = int(unit_votes["votes"].sum())
                off = units.with_columns(
                    off=pl.col("voters") - pl.col("cand") - pl.col("invalid") - pl.col("blank")
                ).filter(pl.col("off") != 0)
                mesa_set = make_set(
                    "mesa",
                    False,
                    min(got / official, 1),
                    MESA_METHOD.format(
                        votes=got,
                        official=official,
                        gap=MESA_GAP if got != official else "",
                        inconsistent=MESA_INCONSISTENT.format(
                            count=off.height, most=max(abs(value) for value in off["off"])
                        )
                        if off.height
                        else "",
                    )
                    + "".join(
                        MESA_DROPPED.format(
                            label=names.get(row["key"], row["key"]), mesa=row["mesa"], concello=row["concello"]
                        )
                        for row in dropped.filter(where).iter_rows(named=True)
                    ),
                    source,
                )
                mesa_units: dict[tuple[str, str, str, str], tuple[UUID, int]] = {}
                for row in units.iter_rows(named=True):
                    abroad = row["mun"].startswith("99")
                    section = f"{province}{row['mun']}{row['district']}{row['section']}"
                    code = f"{stamp}:{section}{row['mesa']}"
                    unit = cs.unit("es.mesa", code)
                    channel = 0
                    if abroad:
                        parent, area, relation, kind, channel = (
                            municipality_unit(province, row["mun"]),
                            cs.area("es.provincia", province),
                            "within",
                            "aggregate",
                            ABROAD,
                        )
                    else:
                        parent = municipality_unit(province, row["mun"])
                        area = (section_areas.get(section) if year >= 2020 else None) or municipality_areas.get(
                            province + row["mun"]
                        )
                        relation, kind = ("within" if area is not None else "unlocated"), "standard"
                    unit_rows[unit] = (unit, "es.mesa", code, parent, relation, area, kind)
                    mesa_units[row["mun"], row["district"], row["section"], row["mesa"]] = (unit, channel)
                    accounting(mesa_set, unit, row, channel)
                for row in unit_votes.iter_rows(named=True):
                    unit, channel = mesa_units[row["mun"], row["district"], row["section"], row["mesa"]]
                    values.append((str(mesa_set), str(unit), str(options[row["key"]]), VOTES, row["votes"], channel))

        corrected = {right for entry in ERRATA.values() for right in entry[2].values()}
        for year, (day, _, citation, _, threshold) in ACORDOS.items():
            election = cs.key("es.election", f"galicia:{day}")
            cs.add(
                "election", id=election, polity_id=galicia, kind="general", date_from=day, date_to=day, status="held"
            )
            name(election, f"{year} Galician regional election")
            name(election, f"Eleccións ao Parlamento de Galicia de {year}", "gl", "native")
            path = f"es/ga/{day}-general"
            cs.add("slug", path=path, entity_id=election)
            for province, province_name in PROVINCES.items():
                where = (pl.col("year") == year) & (pl.col("province") == province)
                rows = acordo_votes.filter(where).sort("votes", "label", descending=[True, False])
                counted = dict(acordo_counts.filter(where).select("field", "votes").iter_rows())
                declared = acordo_members.filter(where)
                seats_by_party = Counter(declared["key"].to_list())
                key = f"{day}:{province}"
                contest = cs.key(f"{SOURCE}.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="list_allocation",
                    body_id=body,
                    district_area_id=cs.area("es.provincia", province),
                    magnitude=declared.height,
                    electoral_system_id=system if threshold == THRESHOLD else system_1985,
                    default_result_kind="counted",
                )
                name(contest, province_name)
                cs.add("slug", path=f"{path}/{slug(province_name)}", entity_id=contest)
                options = {}
                for entry in rows.iter_rows(named=True):
                    option = options[entry["key"]] = cs.key(f"{SOURCE}.option", f"{key}:{entry['key']}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        party_id=parties[entry["key"]],
                        ballot_label=entry["label"],
                    )
                lists = int(rows["votes"].sum())
                extra = ""
                if "blank" not in counted:
                    extra += ACORDO_NO_BLANK
                    counted["blank"] = counted["valid"] - lists
                    counted["invalid"] = counted["voters"] - counted["valid"]
                if counted.get("cand", lists) != lists:
                    extra += ACORDO_CANDIDACIES.format(cand=counted["cand"], lists=lists)
                if off := counted["voters"] - counted["valid"] - counted["invalid"]:
                    extra += ACORDO_BALLOTS.format(off=off)
                if year == 1989 and province == "32":
                    extra += ACORDO_COURT
                if year in ERRATA and corrected & set(declared["name"].to_list()):
                    extra += ACORDO_ERRATA.format(errata=ERRATA[year][1])
                result_set = cs.key(f"{SOURCE}.result_set", f"{key}:province")
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=dog,
                    is_default=True,
                    completeness=1,
                )
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="methodology",
                    body_md=ACORDO_METHOD.format(dog=citation, extra=extra),
                )
                unit = province_units[province]
                accounting(result_set, unit, {**counted, "cand": lists})
                for entry in rows.iter_rows(named=True):
                    values.append((str(result_set), str(unit), str(options[entry["key"]]), VOTES, entry["votes"], 0))
                    if seats := seats_by_party.get(entry["key"], 0):
                        cs.add(
                            "outcome",
                            result_set_id=result_set,
                            option_id=options[entry["key"]],
                            kind="won",
                            seats=seats,
                        )
                seen: Counter[str] = Counter()
                for member in declared.sort("key", "position").iter_rows(named=True):
                    person_key = f"{key}:{member['key']}:{fold(member['name'])}"
                    seen[person_key] += 1
                    if seen[person_key] > 1:
                        person_key += f":{seen[person_key]}"
                    person = cs.key(f"{SOURCE}.person", person_key)
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, member["name"], "es")
                    party = parties[member["key"]]
                    option = cs.key(f"{SOURCE}.option", person_key)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        parent_option_id=options[member["key"]],
                        party_id=party,
                        ballot_label=member["name"],
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", person_key),
                        person_id=person,
                        option_id=option,
                        role="list_member",
                        ballot_name=member["name"],
                        member_of_party_id=party,
                        is_independent=False,
                    )
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")

        cs.frame(
            "reporting_unit",
            pl.DataFrame(
                [
                    {
                        "id": str(unit),
                        "polity_id": str(spain),
                        "scheme_code": scheme,
                        "code": code,
                        "parent_id": None if parent is None else str(parent),
                        "area_relation": relation,
                        "area_id": None if area is None else str(area),
                        "kind": kind,
                    }
                    for unit, scheme, code, parent, relation, area, kind in unit_rows.values()
                    if unit in cs.minted
                ],
                schema_overrides={"parent_id": pl.String, "area_id": pl.String},
            ),
        )
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "measure": pl.Int16,
                    "value": pl.Int64,
                    "channel": pl.Int16,
                },
                orient="row",
            ).with_columns(count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
        )
        return cs.write()
