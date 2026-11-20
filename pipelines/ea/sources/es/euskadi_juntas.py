import html
import re
from collections import Counter, defaultdict
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import slug
from ea.package import Draft
from ea.sources.es import euskadi
from ea.sources.es.euskadi import (
    BALLOTS,
    BLANK,
    INVALID,
    PORTAL,
    PUBLISHER,
    REGISTERED,
    VALID,
    VOTES,
    dhondt,
    fold,
    results,
    sheets,
    tidy,
)
from ea.store import DATA, Connection, connect

SOURCE = "es.euskadi_juntas"
PARSED = DATA / "parsed" / SOURCE
ELECTIONS = {
    1979: date(1979, 4, 3),
    1983: date(1983, 5, 8),
    1987: date(1987, 6, 10),
    1991: date(1991, 5, 26),
    1995: date(1995, 5, 28),
    1999: date(1999, 6, 13),
    2003: date(2003, 5, 25),
    2007: date(2007, 5, 27),
    2011: date(2011, 5, 22),
    2015: date(2015, 5, 24),
    2019: date(2019, 5, 26),
    2023: date(2023, 5, 28),
}
TERRITORIES = {"01": "Álava", "20": "Gipuzkoa", "48": "Bizkaia"}
NATIVE = {"01": "Araba/Álava", "20": "Gipuzkoa", "48": "Bizkaia"}
TITLE = "Resultados de las elecciones a Juntas Generales (euskadi.eus)"
JUNTAS_BASE = "https://www.bngipuzkoa.eus/WAS/CORP/DJGPortalWEB/"
JUNTAS_PUBLISHER = "Juntas Generales de Gipuzkoa"
JUNTAS_TITLE = "Junteras y junteros por legislatura (bngipuzkoa.eus)"
BOG_PUBLISHER = "Diputación Foral de Gipuzkoa"
BOG_TITLE = "Boletín Oficial de Gipuzkoa"
BOG_PORTAL = "https://egoitza.gipuzkoa.eus/es/bog"
BOG = "https://egoitza.gipuzkoa.eus/gao-bog/castell/bog/"
# Notices of the Juntas Electorales de Zona in the Boletín Oficial de Gipuzkoa that bear on a circunscripción's
# members: the titulares proclaimed elected, or the candidacies proclaimed for the election.
PROCLAIMED = {(2007, "20", "debaurola"): (f"{BOG}2007/06/21/c0707047.htm", date(2007, 6, 21), "Bergara")}
CANDIDACIES = {
    (2011, "20", "bidasoaoiartzun"): (f"{BOG}2011/04/26/c1104967.htm", date(2011, 4, 26), "Donostia-San Sebastián")
}
KEYWORDS = (
    "ayala",
    "aiara",
    "vitoria",
    "zuya",
    "cuadrillas",
    "bilbo",
    "bilbao",
    "busturia",
    "durango",
    "encartaciones",
    "enkarterriak",
    "bidasoa",
    "deba",
    "donostialdea",
    "oria",
)
SAME = {"aiara": "ayala", "bilbo": "bilbao", "enkarterriak": "encartaciones", "cuadrillas": "zuya"}


def url(year: int) -> str:
    return f"{euskadi.BASE}J{year % 100:02d}_c.zip"


def roster_url(term: int) -> str:
    return f"{JUNTAS_BASE}composicion_junteros_listado.jsp?idioma=es&legislatura={term}"


def roster_links(term: int) -> list[str]:
    """The personal pages that the Juntas Generales de Gipuzkoa's list of a term's junteros links to."""
    text = raw.cached(SOURCE, roster_url(term)).path.read_bytes().decode("latin-1")
    codes = re.findall(rf"composicion_junteros_ficha\.jsp\?legislatura={term}&amp;codigo=(\d+)", text)
    return [
        f"{JUNTAS_BASE}composicion_junteros_ficha.jsp?legislatura={term}&codigo={code}" for code in dict.fromkeys(codes)
    ]


def fetch() -> None:
    for year in ELECTIONS:
        raw.fetch(SOURCE, url(year), suffix=".zip")
    for term in range(1, len(ELECTIONS) + 1):
        raw.fetch(SOURCE, roster_url(term), suffix=".html")
        links = roster_links(term)
        got = raw.fetch_many(SOURCE, links, suffix=".html", workers=4)
        if len(got) != len(links):
            raise ValueError(f"term {term}: {len(links) - len(got)} junteros' pages could not be fetched")
    for notice, _, _ in [*PROCLAIMED.values(), *CANDIDACIES.values()]:
        raw.fetch(SOURCE, notice, suffix=".html")


def page_text(link: str) -> str:
    text = raw.cached(SOURCE, link).path.read_bytes().decode("latin-1").replace("\xad", "")
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</tr>|</h\d>", "\n", text)
    return html.unescape(re.sub(r"<[^>]+>", " ", text))


def roster(term: int, year: int) -> list[dict]:
    """The junteros of a term in Gipuzkoa as the Juntas Generales list them: the circunscripción, the date each took
    a seat, and whom each replaced when they came in as a substitute. The pages come in Basque or Spanish."""
    out = []
    for link in roster_links(term):
        text = re.sub(r"\s+", " ", page_text(link))
        name = re.search(r"(?:Ficha personal|Fitxa pertsonala) (.+?) (?:Candidatura electoral|Hauteskundetako)", text)
        circ = re.search(
            r"(?:Circunscripción electoral|Hauteskunde barrutia) ?: ?(.*?) ?(?:Grupo Juntero|Batzarkide)", text
        )
        seated = re.search(r"(?:Fecha alta|Alta) ?: ?(\d[\d-]+)( \([^)]*\))?", text)
        if name is None or circ is None or seated is None:
            raise ValueError(f"unexpected juntero page {link}")
        replaced = re.match(r" \(\s*[^:]*:\s*(.*?)\s*\)", seated.group(2) or "")
        day = seated.group(1)
        out.append(
            {
                "year": year,
                "th": "20",
                "name": name.group(1).strip(),
                "circ": circ.group(1).strip(),
                "seated": date.fromisoformat(day if day[4] == "-" else f"{day[6:]}-{day[3:5]}-{day[:2]}"),
                "replaced": replaced.group(1) if replaced else None,
                "url": link,
            }
        )
    return out


def numbered(text: str) -> list[tuple[str, str]]:
    """The numbered names of a candidacies or proclamation notice, each with the heading it falls under: titulares,
    or suplentes after an 'Ordezkoak/Suplentes' or 'Suplentes' heading until the next list starts again at 1."""
    out: list[tuple[str, str]] = []
    mode, fresh = "titular", False
    flat = re.sub(r"\s+", " ", text)
    for marker, number, person in re.findall(
        r"(Suplentes\s*:)|(\d+)\.\s*(?:D\.ª|D\.|Dña\.|Doña|Don)\s+(.+?)\s+(?:jn|and)\b", flat
    ):
        if marker:
            mode, fresh = "suplente", True
            continue
        if number == "1":
            if fresh:
                fresh = False
            elif mode == "suplente":
                mode = "titular"
        out.append((mode, person))
    return out


PARTICLES = {"de", "del", "la", "las", "los", "y", "e", "san"}


def surnames(name: str) -> list[str]:
    part = name.split(",", 1)[0]
    return [fold(word) for word in part.split() if fold(word) and fold(word) not in PARTICLES]


def alike(word: str, words: list[str]) -> bool:
    return any(SequenceMatcher(None, word, other).ratio() >= 0.75 for other in words)


def names_match(member: str, text: str, given: bool = False) -> bool:
    """Whether a 'SURNAMES, GIVEN' name names the person in a notice's 'Given Surnames', allowing the spelling of a
    second surname to differ (Arteaga and Urteaga, Aranceta and Aranzeta). With `given`, the first given name must
    match too; the notices sometimes give another of a person's given names (Miren for María Soledad)."""
    words = [fold(word) for word in text.split() if fold(word)]
    own = surnames(member)
    if not own or own[0] not in words:
        return False
    rest = words[: words.index(own[0])] + words[words.index(own[0]) + 1 :]
    if not all(alike(word, rest) for word in own[1:]):
        return False
    first = [fold(word) for word in member.partition(",")[2].split() if fold(word)]
    return not given or not first or alike(first[0], rest)


def name_score(a: str, b: str) -> float:
    """How alike two 'Surnames, Given' names are, the surnames weighing double."""
    (sa, _, ga), (sb, _, gb) = a.partition(","), b.partition(",")
    return 2 * SequenceMatcher(None, fold(sa), fold(sb)).ratio() + SequenceMatcher(None, fold(ga), fold(gb)).ratio()


def circ_key(text: str) -> str:
    return fold(re.sub(r"\(?\bJ\d\d\)?\s*$", "", text.strip()))


def family(text: str) -> str:
    key = fold(text)
    for word in KEYWORDS:
        if word in key:
            return SAME.get(word, word)
    return key


def elected_rows(rows: list[list[str]], year: int) -> list[dict]:
    start = next(index for index, row in enumerate(rows) if "partido" in map(fold, row))
    header = [fold(cell) for cell in rows[start]]
    th, party = header.index("th"), header.index("partido")
    circ = next(index for index, key in enumerate(header) if key.startswith("circ"))
    code = next((index for index, key in enumerate(header) if key in ("codcom", "codigocomarca")), None)
    out = []
    for row in rows[start + 1 :]:
        if len(row) <= party + 1 or not row[th]:
            continue
        name = ", ".join(part.strip() for part in row[-1].strip("'\" ").split(","))
        out.append(
            {
                "year": year,
                "th": euskadi.territory(row[th]),
                "circ": circ_key(row[circ]),
                "comarca": int(row[code]) if code is not None and row[code].strip() else None,
                "party": row[party],
                "name": name,
            }
        )
    return out


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    tables: dict[str, list[dict]] = defaultdict(list)
    placed: dict[tuple[str, str], str] = {}
    for year in ELECTIONS:
        files = sheets(year, SOURCE, url(year))
        units, votes = tidy(results(files["cir"]), year, ("name",))
        for unit in units:
            unit["circ"] = circ_key(unit["name"])
            unit["label"] = re.sub(r"\s*\(?\bJ\d\d\)?\s*$", "", unit.pop("name")).strip()
        for row in votes:
            row["circ"] = circ_key(row.pop("name"))
        tables["circ"].extend(units)
        tables["circ_votes"].extend(votes)
        known = {(unit["th"], unit["circ"]) for unit in units}
        ran = defaultdict(set)
        for row in votes:
            ran[row["th"], row["circ"]].add(row["party"])
        members = elected_rows(files["elec"], year)
        for member in members:
            key = (member["th"], member["circ"])
            if key not in known:
                if (year, member["th"]) != (1979, "01"):
                    raise ValueError((year, key))
            elif member["party"] not in ran[key]:
                (member["party"],) = [
                    p for p in ran[key] if p.startswith(member["party"]) or member["party"].startswith(p)
                ]
        tables["elected"].extend(members)
        codes = {
            (member["th"], member["comarca"]): member["circ"] for member in members if member["comarca"] is not None
        }
        mun = results(files["mun"])
        has_comarca = any(row.get("comarca") for row in mun)
        mun_units, mun_votes = tidy(mun, year, ("mun", "name") + (("comarca",) if has_comarca else ()))
        by_family = {(th, family(circ)): circ for th, circ in known}
        for unit in mun_units:
            if has_comarca:
                unit["circ"] = codes[unit["th"], int(unit.pop("comarca"))]
            else:
                unit["circ"] = by_family[unit["th"], family(placed[unit["th"], unit["mun"]])]
            placed[unit["th"], unit["mun"]] = unit["circ"]
        tables["mun"].extend(mun_units)
        tables["mun_votes"].extend(mun_votes)
        mesa_units, mesa_votes = tidy(results(files["mes"]), year, ("mun", "district", "section", "mesa", "name"))
        tables["mesa"].extend(mesa_units)
        tables["mesa_votes"].extend(mesa_votes)
    for term, year in enumerate(ELECTIONS, 1):
        tables["roster"].extend(roster(term, year))
    for name, rows in tables.items():
        frame = pl.DataFrame(rows, infer_schema_length=None)
        frame.write_parquet(PARSED / f"{name}.parquet")
        print(f"{name}: {frame.height} rows")
    check()


def threshold_fit(votes: dict[str, int], valid: int, seats: Counter[str], seat_count: int) -> list[float]:
    return [
        share
        for share in (0.0, 0.03, 0.05)
        if all(dhondt(votes, valid, seat_count, share)[party] == seats[party] for party in set(votes) | set(seats))
    ]


def check() -> None:
    circ = pl.read_parquet(PARSED / "circ.parquet")
    cir_votes = pl.read_parquet(PARSED / "circ_votes.parquet")
    members = pl.read_parquet(PARSED / "elected.parquet")
    mun = pl.read_parquet(PARSED / "mun.parquet")
    mun_votes = pl.read_parquet(PARSED / "mun_votes.parquet")
    mesa_votes = pl.read_parquet(PARSED / "mesa_votes.parquet")
    for row in circ.iter_rows(named=True):
        where = (pl.col("year") == row["year"]) & (pl.col("th") == row["th"]) & (pl.col("circ") == row["circ"])
        part = {r["party"]: r["votes"] for r in cir_votes.filter(where).iter_rows(named=True)}
        seats = Counter(members.filter(where)["party"].to_list())
        total = sum(seats.values())
        fit = threshold_fit(part, row["cand"] + row["blank"], seats, total)
        mine = mun.filter(where)
        got = int(mun_votes.join(mine.select("year", "th", "mun"), on=["year", "th", "mun"])["votes"].sum())
        print(
            row["year"],
            row["th"],
            row["circ"][:30],
            f"seats={total}",
            f"lists={sum(part.values()) == row['cand']}",
            f"fit={fit}",
            f"mun={got - sum(part.values())}",
        )
    for year in ELECTIONS:
        print(
            year,
            "mesa",
            mesa_votes.filter(pl.col("year") == year)["votes"].sum(),
            cir_votes.filter(pl.col("year") == year)["votes"].sum(),
        )


def threshold(year: int) -> float:
    return 0.05 if year <= 1987 else 0.03


LAW = (
    "Ley 1/1987, de 27 de marzo, de Elecciones para las Juntas Generales de los Territorios Históricos de Álava, "
    "Bizkaia y Gipuzkoa"
)
SYSTEM = (
    "Closed party lists in the circunscripciones of each historic territory, which elect the Juntas Generales of "
    "Álava, Bizkaia, and Gipuzkoa. Seats go by the D'Hondt method within each circunscripción to the lists with at "
    "least {share}% of its valid votes, and blank ballots count as valid votes. The threshold is the one that "
    "reproduces the declared seats of the circunscripciones in the elections it covers; the publisher's files do not "
    "state it. The Basque Government publishes the elected members of each list, and those are the declared seats "
    "stored here (" + LAW + ")."
)
CIRC_METHOD = (
    "List votes, seats, registered voters, ballots cast, blank ballots, and null ballots by circunscripción from the "
    "Basque Government's results files. Valid ballots here are the votes to candidacies, with blank ballots stored "
    "apart. The seats are the number of members the Elec file names for each list, less any that this note shows "
    "were not elected (a substitute who took a seat later in the term, or a suplente)."
)
MISMATCH = (
    " The file names {declared} members for this circunscripción, and a D'Hondt allocation of {declared} seats at the "
    "{share}% threshold gives a different split ({computed}); the declared members are stored as published."
)
MUNICIPALITY_METHOD = (
    "List votes and ballot counts by municipality from the Basque Government's results files, for the municipalities "
    "the file assigns to this circunscripción. The municipal figures add up to {votes:,} list votes, against "
    "{official:,} in the circunscripción results."
)
MESA_METHOD = (
    "List votes and ballot counts by polling station (mesa) from the Basque Government's results files, each mesa on "
    "its census section where the 2025 INE section map has the same code, and otherwise on its municipality. The mesa "
    "figures add up to {votes:,} list votes, against {official:,} in the circunscripción results."
)
LISTS_DIFFER = (
    " In the source, the list votes of this circunscripción add up to {lists:,}, against {candidacies:,} votes to "
    "candidacies in its own totals."
)
SEATED = (
    " The Juntas Generales de Gipuzkoa's list of the term's junteros gives this circunscripción {seats} seats (its "
    "junteros less those another later replaced), the same number of members the file names."
)
SETTLED = (
    " The file names {listed} members for this circunscripción, against the {seats} seats the Juntas Generales de "
    "Gipuzkoa's list of the term's junteros gives it (its junteros less those another later replaced). {why} The "
    "elected members stored here are the other {seats}, and the others are stored as list candidates."
)
SUBSTITUTE = "{name} ({party}) took a seat on {day} in place of {replaced}"
SUPLENTE = (
    "{name} is a suplente of the {party} list, not one of its titulares, in the candidacies the Junta Electoral de "
    "Zona de {zone} proclaimed (Boletín Oficial de Gipuzkoa, {day})"
)
PROCLAMATION = (
    " They are the titulares the Junta Electoral de Zona de {zone} proclaimed elected (Boletín Oficial de Gipuzkoa, "
    "{day})."
)


def natural(name: str) -> str:
    """'SURNAMES, GIVEN' as 'Given Surnames'."""
    surname, _, given = name.partition(",")
    return " ".join(part.strip() for part in (given, surname) if part.strip())


def long_date(day: date) -> str:
    return f"{day:%B} {day.day}, {day.year}"


def joined(items: list[str]) -> str:
    """A series whose items hold commas of their own, so separated by semicolons."""
    return (
        items[0] if len(items) == 1 else "; ".join(items[:-1]) + ("; and " if len(items) > 2 else " and ") + items[-1]
    )


def section(text: str, circ: str) -> str:
    """The part of a Junta Electoral de Zona notice that covers one circunscripción."""
    parts = re.split(r"Circunscripción\s*«", text)
    (part,) = [part for part in parts[1:] if fold(part.split("»", 1)[0]).startswith(circ)]
    return part


def settle(members: pl.DataFrame, rosters: pl.DataFrame) -> tuple[pl.DataFrame, dict[tuple[int, str, str], dict]]:
    """Mark which of the Elec file's members were elected. In some Gipuzkoa circunscripciones the file also names
    those who took a seat later in the term, so it names more members than there are seats. There the members are
    settled against the Juntas Generales de Gipuzkoa's own list of each term's junteros: a member it shows taking a
    seat in place of someone is a substitute, and a member that the candidacies proclaimed in the Boletín Oficial de
    Gipuzkoa list as a suplente is one too. The seats of a circunscripción are its junteros less those whom another
    later replaced."""
    rows = [{**row, "status": "elected"} for row in members.iter_rows(named=True)]
    found: dict[tuple[int, str, str], dict] = {}
    for year in ELECTIONS:
        elec = [row for row in rows if row["year"] == year and row["th"] == "20"]
        pages = rosters.filter(pl.col("year") == year).to_dicts()
        pairs = sorted(
            (
                (name_score(page["name"], row["name"]), i, j)
                for i, page in enumerate(pages)
                for j, row in enumerate(elec)
            ),
            reverse=True,
        )
        taken_pages: set[int] = set()
        match: dict[int, dict] = {}
        for score, i, j in pairs:
            if score >= 2 and i not in taken_pages and j not in match:
                taken_pages.add(i)
                match[j] = pages[i]
        votes = Counter((page["circ"], elec[j]["circ"]) for j, page in match.items())
        label: dict[str, str] = {}
        for (text, circ), _ in votes.most_common():
            label.setdefault(text, circ)
        seats: Counter[str] = Counter()
        for text, circ in label.items():
            group = [page for page in pages if page["circ"] == text]
            replaced = [page["replaced"] for page in group if page["replaced"]]
            gone = [page for page in group if any(names_match(page["name"], other, given=True) for other in replaced)]
            seats[circ] += len(group) - len(gone)
        for circ in dict.fromkeys(row["circ"] for row in elec):
            group = [j for j, row in enumerate(elec) if row["circ"] == circ]
            key = (year, "20", circ)
            found[key] = {"listed": len(group), "seats": seats[circ], "why": [], "urls": []}
            if len(group) <= seats[circ]:
                continue
            for j in group:
                page = match.get(j)
                if page is not None and page["replaced"] is not None:
                    elec[j]["status"] = "substitute"
                    found[key]["why"].append(
                        SUBSTITUTE.format(
                            name=title(natural(elec[j]["name"])),
                            party=elec[j]["party"],
                            day=long_date(page["seated"]),
                            replaced=natural(page["replaced"]),
                        )
                    )
                    found[key]["urls"].append(page["url"])
            left = [j for j in group if elec[j]["status"] == "elected"]
            if len(left) > seats[circ] and key in CANDIDACIES:
                notice, day, zone = CANDIDACIES[key]
                entries = numbered(section(page_text(notice), circ))
                for j in left:
                    roles = {mode for mode, person in entries if names_match(elec[j]["name"], person)}
                    if roles == {"suplente"}:
                        elec[j]["status"] = "suplente"
                        found[key]["why"].append(
                            SUPLENTE.format(
                                name=title(natural(elec[j]["name"])),
                                party=elec[j]["party"],
                                zone=zone,
                                day=long_date(day),
                            )
                        )
                found[key]["notice"] = notice
            left = [j for j in group if elec[j]["status"] == "elected"]
            if len(left) != seats[circ]:
                raise ValueError(f"{key}: {len(left)} members left against {seats[circ]} seats")
            if key in PROCLAIMED:
                notice, day, zone = PROCLAIMED[key]
                text = re.split(r"Suplentes\s*:", page_text(notice))[0]
                titulares = [person for mode, person in numbered(text) if mode == "titular"]
                matched = [j for j in left if any(names_match(elec[j]["name"], person) for person in titulares)]
                if len(titulares) != len(left) or len(matched) != len(left):
                    raise ValueError(f"{key}: the members left differ from the proclaimed titulares")
                found[key]["proclaimed"] = PROCLAMATION.format(zone=zone, day=long_date(day))
                found[key]["notice"] = notice
    return pl.DataFrame(rows), found


SMALL = {"de", "del", "la", "las", "los", "y", "e"}


def title(text: str) -> str:
    text = re.sub(r"\s*;\s*|\s{2,}", ", ", text.strip())
    words = text.lower().split(" ")
    return " ".join(w if w in SMALL else w[:1].upper() + w[1:] for w in words)


# Municipalities of today that an earlier election's files don't list because their territory still belonged to
# another municipality, keyed to the municipalities it came from where its neighbors lie in more than one
# circunscripción. The files bear each one out: at the first election that lists the new municipality, its parents'
# electors fall by about its own (Bilbao by 30,387 in 1983, against 28,053 in Derio, Erandio, Loiu, Sondika, and
# Zamudio; Hernani and Urnieta by 11,939 in 1987, against 15,346 in Lasarte-Oria and Astigarraga; Ikaztegieta by 218
# in 1991, against 240 in Baliarrain and Orendain; Elgoibar by 704 in 1987, against 1,224 in Mendaro; Durango by
# 2,638 in 1991, against 3,947 in Iurreta; Barakaldo by 1,085 in 1991, against 2,523 in Alonsotegi; Markina-Xemein by
# 317 in 2007, against 350 in Ziortza-Bolibar). Usansolo left Galdakao in 2022 and no file lists it. Every other
# such municipality borders only municipalities of one circunscripción, and joins that one.
PARENTS = {
    "20901": ("20032",),
    "20902": ("20040", "20072"),
    "20903": ("20040",),
    "20904": ("20044",),
    "20905": ("20044",),
    "48901": ("48020",),
    "48902": ("48020",),
    "48903": ("48020",),
    "48904": ("48020",),
    "48905": ("48020",),
    "48910": ("48027",),
    "48912": ("48013",),
    "48915": ("48060",),
    "48916": ("48036",),
}
# Interior rings smaller than this are slivers between the INE municipal outlines, which don't share vertices; the
# smallest real hole in a circunscripción (inside Bilbao's own outline) is 3,349 m2.
SLIVER = 2000
AREA_METHOD = (
    "The union of the present-day INE outlines of the {count} municipalities the Basque Government's results file "
    "places in this circunscripción for the {year} election."
)
AREA_LATER = " It also takes in the present-day municipalities the file does not list: {places}."


def circ_members(conn: Connection, muns: pl.DataFrame) -> dict[tuple[int, str, str], tuple[list[str], list[str]]]:
    """Each circunscripción's municipalities as the file lists them, and the present-day municipalities whose
    territory it then held without listing them, by election, territory, and circunscripción."""
    current = {
        str(code)
        for (code,) in conn.execute(
            """select c.code from area a join area_code c on c.area_id = a.id
               where a.scheme_code = 'es.municipio' and left(c.code, 2) = any(%s)
                 and exists (select from area_geometry g where g.area_id = a.id)""",
            [list(TERRITORIES)],
        )
    }
    listed = {(year, th + mun) for year, th, mun in muns.select("year", "th", "mun").iter_rows()}
    years = {year for year, _ in listed}
    later = sorted(code for code in current if any((year, code) not in listed for year in years))
    neighbors: dict[str, list[str]] = {
        str(code): [str(other) for other in others]
        for code, others in conn.execute(
            """with m as (select c.code, g.geom from area a join area_code c on c.area_id = a.id
                          join area_geometry g on g.area_id = a.id and upper_inf(g.valid)
                          where a.scheme_code = 'es.municipio' and left(c.code, 2) = any(%s))
               select x.code, array_agg(y.code order by y.code) from m x join m y
                 on x.code <> y.code and x.geom && st_expand(y.geom, 0.01)
                 and st_dwithin(x.geom::geography, y.geom::geography, 100)
               where x.code = any(%s) group by x.code""",
            [list(TERRITORIES), later],
        )
    }
    out: dict[tuple[int, str, str], tuple[list[str], list[str]]] = {}
    for (year, th), group in muns.group_by("year", "th", maintain_order=True):
        placed = {th + mun: circ for mun, circ in group.select("mun", "circ").iter_rows()}
        for code, circ in placed.items():
            out.setdefault((year, th, circ), ([], []))[0].append(code)
        missing = sorted(code for code in current if code[:2] == th and code not in placed)
        while missing:
            settled: dict[str, str] = {}
            for code in missing:
                around = {placed[p] for p in PARENTS.get(code, neighbors.get(code, [])) if p in placed}
                if len(around) > 1:
                    raise ValueError(f"{year} {code}: lies between circunscripciones {sorted(around)}")
                if around:
                    settled[code] = around.pop()
            if not settled:
                raise ValueError(f"{year}: no circunscripción for {missing}")
            for code, circ in settled.items():
                placed[code] = circ
                out[year, th, circ][1].append(code)
            missing = [code for code in missing if code not in settled]
    return {key: (sorted(members), sorted(extra)) for key, (members, extra) in out.items()}


def union(conn: Connection, codes: list[str]) -> tuple[str, str, UUID]:
    """The union of the municipalities' current INE outlines, without the slivers between them, its label point, and
    the outlines' source."""
    row = conn.execute(
        """with whole as (
               select st_collectionextract(st_makevalid(st_union(g.geom)), 3) geom, min(g.source_id::text) source
               from area_code c join area a on a.id = c.area_id and a.scheme_code = 'es.municipio'
               join area_geometry g on g.area_id = a.id and upper_inf(g.valid)
               where c.code = any(%s)),
           parts as (select (st_dump(geom)).geom part, source from whole),
           filled as (
               select st_makepolygon(
                        st_exteriorring(part),
                        array(select st_interiorringn(part, i) from generate_series(1, st_numinteriorrings(part)) i
                              where st_area(st_makepolygon(st_interiorringn(part, i))::geography) >= %s)) part,
                      source
               from parts)
           select encode(st_asewkb(st_multi(st_union(part))), 'hex'),
                  encode(st_asewkb(st_pointonsurface(st_union(part))), 'hex'), min(source)
           from filled""",
        [codes, SLIVER],
    ).fetchone()
    if row is None or row[0] is None:
        raise ValueError(f"no INE outlines for {codes}")
    return str(row[0]), str(row[1]), UUID(row[2])


def emit() -> Path:
    circ = pl.read_parquet(PARSED / "circ.parquet")
    circ_votes = pl.read_parquet(PARSED / "circ_votes.parquet")
    muns = pl.read_parquet(PARSED / "mun.parquet")
    mun_votes = pl.read_parquet(PARSED / "mun_votes.parquet")
    mesas = pl.read_parquet(PARSED / "mesa.parquet")
    mesa_votes = pl.read_parquet(PARSED / "mesa_votes.parquet")
    members, settled = settle(pl.read_parquet(PARSED / "elected.parquet"), pl.read_parquet(PARSED / "roster.parquet"))
    with connect() as conn:
        cs = Draft(
            conn,
            pipeline=SOURCE,
            version="1",
            summary=(
                "Juntas Generales of Álava, Bizkaia, and Gipuzkoa, 1979 to 2023, by circunscripción, municipality, "
                "and polling station, with the elected members, from the Basque Government's results files"
            ),
            polity="ES",
        )
        cs.accept(
            "accounting.ballots_sum",
            "the Basque Government's circunscripción, municipality, and mesa files give ballots cast that differ from "
            "the votes to candidacies, blank, and null ballots in some units; the figures are stored as published",
        )
        cs.accept(
            "accounting.votes_valid",
            "in some units the files' list votes add up to other than their votes to candidacies, as the result sets' "
            "notes say for the circunscripciones; the figures are stored as published",
        )
        cs.accept(
            "accounting.turnout",
            "two mesas in the files report more ballots cast than registered voters; the figures are stored as "
            "published",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        spain, basque = cs.polity("ES"), cs.polity("ES-PV")
        for scheme in ("es.provincia", "es.municipio", "es.seccion", "es.mesa"):
            if conn.execute("select 1 from scheme where code = %s", (scheme,)).fetchone() is None:
                raise ValueError("publish es.ine and es.referendums before the Juntas Generales")
        scheme = "es.junta_circ"
        cs.add(
            "scheme",
            code=scheme,
            polity_id=spain,
            kind="electoral_district",
            rank=3,
            noun="Juntas Generales circunscripción",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PORTAL,
            license=None,
            license_url=None,
            attribution="Fuente: Gobierno Vasco, euskadi.eus.",
        )
        for year in ELECTIONS:
            cs.artifact(raw.cached(SOURCE, url(year)), source)
        juntas = cs.source(JUNTAS_PUBLISHER, JUNTAS_TITLE)
        cs.add(
            "source",
            id=juntas,
            kind="official_body",
            publisher=JUNTAS_PUBLISHER,
            title=JUNTAS_TITLE,
            url=f"{JUNTAS_BASE}composicion_junteros_listado.jsp",
            license=None,
            license_url=None,
            attribution="Fuente: Juntas Generales de Gipuzkoa, bngipuzkoa.eus.",
        )
        for term in range(1, len(ELECTIONS) + 1):
            for link in [roster_url(term), *roster_links(term)]:
                cs.artifact(raw.cached(SOURCE, link), juntas)
        bog = cs.source(BOG_PUBLISHER, BOG_TITLE)
        cs.add(
            "source",
            id=bog,
            kind="official_body",
            publisher=BOG_PUBLISHER,
            title=BOG_TITLE,
            url=BOG_PORTAL,
            license=None,
            license_url=None,
            attribution="Fuente: Boletín Oficial de Gipuzkoa.",
        )
        for notice, _, _ in [*PROCLAIMED.values(), *CANDIDACIES.values()]:
            cs.artifact(raw.cached(SOURCE, notice), bog)

        parties: dict[str, UUID] = {}
        for label in sorted(circ_votes["party"].unique()):
            parties[label] = cs.key(f"{euskadi.SOURCE}.party", label)
            if parties[label] in cs.minted:
                cs.add("party", id=parties[label], kind="party", polity_id=basque)
                name(parties[label], label, "es")

        bodies: dict[str, UUID] = {}
        for code, label in TERRITORIES.items():
            body = bodies[code] = cs.key("es.body", f"juntas-generales-{slug(label)}")
            cs.add("body", id=body, polity_id=basque, kind="council")
            name(body, f"Juntas Generales of {label}")
            name(body, f"Juntas Generales de {NATIVE[code]}", "es", "native")
        systems: dict[float, UUID] = {}
        for share in sorted({threshold(year) for year in ELECTIONS}):
            percent = int(share * 100)
            system = systems[share] = cs.key("es.electoral_system", f"juntas-generales-{percent}")
            cs.add(
                "electoral_system",
                id=system,
                polity_id=basque,
                rules={
                    "formula": "dhondt",
                    "lists": {"order": "closed", "vacancy": "next_on_list"},
                    "threshold": {"level": "list", "scope": "contest", "percent": percent},
                },
                rules_version=1,
            )
            name(system, f"Juntas Generales closed-list D'Hondt system with a {percent}% threshold")
            cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM.format(share=percent))

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
        town_names = dict(
            conn.execute(
                """select distinct on (c.code) c.code, n.text from area a join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id and n.is_preferred
                   where a.scheme_code = 'es.municipio' and left(c.code, 2) = any(%s)
                   order by c.code, n.lang = 'es' desc, n.kind = 'official' desc""",
                [list(TERRITORIES)],
            ).fetchall()
        )
        circs = circ_members(conn, muns)
        days = list(ELECTIONS.values())
        until = {day: days[index + 1] if index + 1 < len(days) else None for index, day in enumerate(days)}
        for year, day in ELECTIONS.items():
            if circ.filter(pl.col("year") == year).height:
                cs.add(
                    "boundary_set",
                    scheme_code=scheme,
                    vintage=day.isoformat(),
                    valid_from=day,
                    valid_to=until[day],
                    as_of=day,
                )

        def circ_area(year: int, province: str, key_circ: str, code: str, label: str) -> UUID:
            day, end = ELECTIONS[year], until[ELECTIONS[year]]
            area = cs.area(scheme, code)
            towns, later = circs[year, province, key_circ]
            cs.add("area", id=area, scheme_code=scheme)
            cs.add("area_code", area_id=area, code=code, valid_from=day, valid_to=end)
            cs.add(
                "area_parent", area_id=area, parent_id=cs.area("es.provincia", province), valid_from=day, valid_to=end
            )
            name(area, label, "es")
            geom, point, ine = union(conn, towns + later)
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=day,
                valid_to=end,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=ine,
            )
            for town in towns:
                cs.add(
                    "area_membership",
                    area_id=municipality_areas[town],
                    parent_id=area,
                    partial=False,
                    valid_from=day,
                    valid_to=end,
                )
            note = AREA_METHOD.format(count=len(towns), year=year)
            if later:
                places = [
                    f"{town_names[town]} (then part of "
                    + (
                        " and ".join(town_names[parent] for parent in PARENTS[town] if parent in towns)
                        if town in PARENTS
                        else "a neighbor; every municipality around it lies in this circunscripción"
                    )
                    + ")"
                    for town in later
                ]
                note += AREA_LATER.format(places="; ".join(places))
            cs.add("note", entity_id=area, kind="methodology", body_md=note)
            return area

        unit_rows: dict[UUID, tuple[UUID, str, str, UUID | None, str, UUID | None, str]] = {}
        province_units: dict[str, UUID] = {}
        for code in TERRITORIES:
            unit = province_units[code] = cs.unit("es.provincia", code)
            unit_rows[unit] = (unit, "es.provincia", code, None, "equals", cs.area("es.provincia", code), "standard")
        municipality_units: dict[str, UUID] = {}

        def municipality_unit(province: str, mun: str) -> UUID:
            code = province + mun
            if code not in municipality_units:
                unit = municipality_units[code] = cs.unit("es.municipio", code)
                area = municipality_areas.get(code)
                relation = "equals" if area is not None else "unlocated"
                unit_rows[unit] = (unit, "es.municipio", code, province_units[province], relation, area, "standard")
            return municipality_units[code]

        values: list[tuple[str, str, str | None, int, int, int]] = []

        def accounting(result_set: UUID, unit: UUID, row: dict[str, int | None]) -> None:
            for measure, field in (
                (REGISTERED, "electors"),
                (BALLOTS, "voters"),
                (VALID, "cand"),
                (INVALID, "invalid"),
                (BLANK, "blank"),
            ):
                if (value := row[field]) is not None:
                    values.append((str(result_set), str(unit), None, measure, value, 0))

        sizes = (
            members.filter(~((pl.col("year") == 1979) & (pl.col("th") == "01")) & (pl.col("status") == "elected"))
            .group_by("year", "th")
            .agg(seats=pl.len())
        )
        for code, body in bodies.items():
            previous: tuple[date, int] | None = None
            for year, _, seats in sizes.filter(pl.col("th") == code).sort("year").iter_rows():
                day = ELECTIONS[year]
                if previous is not None and previous[1] != seats:
                    cs.add("body_size", body_id=body, seats=previous[1], valid_from=previous[0], valid_to=day)
                    previous = None
                if previous is None:
                    previous = (day, seats)
            if previous is not None:
                cs.add("body_size", body_id=body, seats=previous[1], valid_from=previous[0], valid_to=None)

        for year, day in ELECTIONS.items():
            election = cs.key("es.election", f"juntas:{day}")
            cs.add("election", id=election, polity_id=basque, kind="local", date_from=day, date_to=day, status="held")
            name(election, f"{year} Juntas Generales elections")
            name(election, f"Elecciones a Juntas Generales de {year}", "es", "native")
            path = f"es/pv/{day}-juntas-generales"
            cs.add("slug", path=path, entity_id=election)
            stamp = f"{year}{day.month:02d}"
            for entry in circ.filter(pl.col("year") == year).iter_rows(named=True):
                province, key_circ = entry["th"], entry["circ"]
                where = (pl.col("year") == year) & (pl.col("th") == province) & (pl.col("circ") == key_circ)
                rows = circ_votes.filter(where).sort("votes", descending=True)
                declared = members.filter(where)
                elected = declared.filter(pl.col("status") == "elected")
                seats_by_party = Counter(elected["party"].to_list())
                key = f"{day}:{province}:{key_circ}"
                label = title(entry["label"])
                circ_code = f"{stamp}:{province}:{key_circ}"
                district = circ_area(year, province, key_circ, circ_code, label)
                contest = cs.key(f"{SOURCE}.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="list_allocation",
                    body_id=bodies[province],
                    district_area_id=district,
                    magnitude=elected.height,
                    electoral_system_id=systems[threshold(year)],
                    default_result_kind="counted",
                )
                name(contest, f"{TERRITORIES[province]}: {label}")
                name(contest, f"{NATIVE[province]}: {entry['label']}", "es", "native")
                cs.add("slug", path=f"{path}/{slug(TERRITORIES[province])}/{slug(label)}", entity_id=contest)
                options: dict[str, UUID] = {}
                for vote in rows.iter_rows(named=True):
                    option = options[vote["party"]] = cs.key(f"{SOURCE}.option", f"{key}:{vote['party']}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        party_id=parties[vote["party"]],
                        ballot_label=vote["party"],
                    )
                official = int(rows["votes"].sum())

                def make_set(label: str, default: bool, completeness: float, note: str) -> UUID:
                    result_set = cs.key(f"{SOURCE}.result_set", f"{key}:{label}")
                    cs.add(
                        "result_set",
                        id=result_set,
                        election_id=election,
                        contest_id=contest,
                        stage="official",
                        kind="counted",
                        status="active",
                        source_id=source,
                        is_default=default,
                        completeness=completeness,
                    )
                    cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
                    return result_set

                note = CIRC_METHOD
                if official != entry["cand"]:
                    note += LISTS_DIFFER.format(lists=official, candidacies=entry["cand"])
                part = {r["party"]: r["votes"] for r in rows.iter_rows(named=True)}
                computed = dhondt(part, entry["cand"] + entry["blank"], elected.height, threshold(year))
                check = settled.get((year, province, key_circ))
                if check is not None and check["why"]:
                    note += SETTLED.format(
                        listed=check["listed"], seats=check["seats"], why=joined(check["why"]) + "."
                    ) + check.get("proclaimed", "")
                elif check is not None and check["listed"] == check["seats"]:
                    note += SEATED.format(seats=check["seats"])
                if computed != seats_by_party:
                    split = ", ".join(f"{p} {n}" for p, n in sorted(computed.items()))
                    note += MISMATCH.format(declared=elected.height, share=int(threshold(year) * 100), computed=split)
                circ_set = make_set("circ", True, 1, note)
                if check is not None and (check["why"] or check["listed"] == check["seats"]):
                    term = list(ELECTIONS).index(year) + 1
                    cs.add("citation", entity_id=circ_set, source_id=juntas, locator={"url": roster_url(term)})
                    if "notice" in check:
                        cs.add("citation", entity_id=circ_set, source_id=bog, locator={"url": check["notice"]})
                circ_unit = cs.unit(scheme, circ_code)
                unit_rows[circ_unit] = (
                    circ_unit,
                    scheme,
                    circ_code,
                    province_units[province],
                    "equals",
                    district,
                    "standard",
                )
                accounting(circ_set, circ_unit, entry)
                for vote in rows.iter_rows(named=True):
                    values.append((str(circ_set), str(circ_unit), str(options[vote["party"]]), VOTES, vote["votes"], 0))
                    if count := seats_by_party.get(vote["party"], 0):
                        cs.add(
                            "outcome",
                            result_set_id=circ_set,
                            option_id=options[vote["party"]],
                            kind="won",
                            seats=count,
                        )
                seen: Counter[str] = Counter()
                for member in declared.iter_rows(named=True):
                    person_key = f"{key}:{member['party']}:{fold(member['name'])}"
                    seen[person_key] += 1
                    if seen[person_key] > 1:
                        person_key += f":{seen[person_key]}"
                    person = cs.key(f"{SOURCE}.person", person_key)
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, member["name"], "es")
                    party = parties[member["party"]]
                    option = cs.key(f"{SOURCE}.option", person_key)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        parent_option_id=options[member["party"]],
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
                    if member["status"] == "elected":
                        cs.add("outcome", result_set_id=circ_set, option_id=option, kind="elected")

                inside = (pl.col("year") == year) & (pl.col("th") == province)
                places = muns.filter(inside & (pl.col("circ") == key_circ))
                place_codes = places["mun"].to_list()
                place_votes = mun_votes.filter(inside & pl.col("mun").is_in(place_codes))
                got = int(place_votes["votes"].sum())
                municipality_set = make_set(
                    "municipality",
                    False,
                    min(got / official, 1),
                    MUNICIPALITY_METHOD.format(votes=got, official=official),
                )
                for row in places.iter_rows(named=True):
                    accounting(municipality_set, municipality_unit(province, row["mun"]), row)
                for row in place_votes.iter_rows(named=True):
                    values.append(
                        (
                            str(municipality_set),
                            str(municipality_unit(province, row["mun"])),
                            str(options[row["party"]]),
                            VOTES,
                            row["votes"],
                            0,
                        )
                    )

                units = mesas.filter(inside & pl.col("mun").is_in(place_codes))
                unit_votes = mesa_votes.filter(inside & pl.col("mun").is_in(place_codes))
                got = int(unit_votes["votes"].sum())
                mesa_set = make_set(
                    "mesa", False, min(got / official, 1), MESA_METHOD.format(votes=got, official=official)
                )
                mesa_units: dict[tuple[str, str, str, str], UUID] = {}
                for row in units.iter_rows(named=True):
                    section = f"{province}{row['mun']}{row['district']}{row['section']}"
                    code = f"{stamp}:{section}{row['mesa']}"
                    unit = cs.unit("es.mesa", code)
                    area = (section_areas.get(section) if year >= 2020 else None) or municipality_areas.get(
                        province + row["mun"]
                    )
                    unit_rows[unit] = (
                        unit,
                        "es.mesa",
                        code,
                        municipality_unit(province, row["mun"]),
                        "within" if area is not None else "unlocated",
                        area,
                        "standard",
                    )
                    mesa_units[row["mun"], row["district"], row["section"], row["mesa"]] = unit
                    accounting(mesa_set, unit, row)
                for row in unit_votes.iter_rows(named=True):
                    unit = mesa_units[row["mun"], row["district"], row["section"], row["mesa"]]
                    values.append((str(mesa_set), str(unit), str(options[row["party"]]), VOTES, row["votes"], 0))

        cs.frame(
            "reporting_unit",
            pl.DataFrame(
                [
                    {
                        "id": str(unit),
                        "polity_id": str(spain),
                        "scheme_code": scheme_code,
                        "code": code,
                        "parent_id": None if parent is None else str(parent),
                        "area_relation": relation,
                        "area_id": None if area is None else str(area),
                        "kind": kind,
                    }
                    for unit, scheme_code, code, parent, relation, area, kind in unit_rows.values()
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
