import html
import re
from dataclasses import dataclass
from datetime import date
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import slug
from ea.package import Draft
from ea.sources.it.interno import BLANK, INVALID, REGISTERED, VOTERS, VOTES
from ea.sources.it.storico import EXISTING, NEW, cased
from ea.store import DATA, connect

SOURCE = "it.speciali"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
VDA = "https://www.regione.vda.it/amministrazione/Elezioni/Dati_e_risultati/elezioni/VotiLista_i.aspx?idele={}&ord=1&setcar=n"


@dataclass(frozen=True)
class Region:
    iso: str
    slug: str
    native: str
    english: str
    council_it: str
    council_en: str
    publisher: str
    title: str
    home: str
    counted: str
    allocated: str


REGIONS = {
    "02": Region(
        iso="IT-23",
        slug="valle-d-aosta",
        native="Valle d'Aosta",
        english="Aosta Valley",
        council_it="Consiglio regionale della Valle d'Aosta",
        council_en="Regional Council of the Aosta Valley",
        publisher="Regione autonoma Valle d'Aosta",
        title="Elezioni: dati e risultati, elezioni regionali",
        home="https://www.regione.vda.it/amministrazione/Elezioni/Dati_e_risultati/Risultati/Elezioni_regionali/default_i.aspx",
        counted=(
            "Results are the Aosta Valley Region's regional summary of list votes and seats, from its election "
            "results pages."
        ),
        allocated=(
            "The council's 35 seats are allocated by list across the whole region, with a majority bonus for a list "
            "or group of lists with a common program, and the council elects the President of the Region."
        ),
    ),
}
VDA_HELD = {
    (date(2003, 6, 8), date(2003, 6, 8)): 62,
    (date(2008, 5, 25), date(2008, 5, 25)): 92,
    (date(2013, 5, 26), date(2013, 5, 26)): 113,
    (date(2018, 5, 20), date(2018, 5, 20)): 137,
    (date(2020, 9, 20), date(2020, 9, 21)): 142,
    (date(2025, 9, 28), date(2025, 9, 28)): 190,
}
# The region's results pages start in 2003 and its printed volumes for earlier councils are not online, so
# 1949-1998 comes from two sources that agree on every total: the Istituto Cattaneo's table of special-statute
# regional elections (ISTAT's 1990 compendium through 1988, the Interior Ministry for 1993 and 1998) for turnout,
# and the Italian Wikipedia's article on each election for votes and seats by list.
VDA_HISTORY = [
    date(1949, 4, 24),
    date(1954, 11, 14),
    date(1959, 5, 17),
    date(1963, 10, 27),
    date(1968, 4, 21),
    date(1973, 6, 10),
    date(1978, 6, 25),
    date(1983, 6, 26),
    date(1988, 6, 26),
    date(1993, 5, 30),
    date(1998, 5, 31),
]
CATTANEO = "https://web.archive.org/web/20110722091126id_/http://www.cattaneo.org/archivi/adele/regioni/speciale.xls"
CATTANEO_SHEETS = ["Valle d'Aosta 1949-1988", "Valle d'Aosta 1993-2003"]
CATTANEO_LABELS = {
    "Totale voti": "listed",
    "Elettori": "registered",
    "Votanti": "voters",
    "Voti non validi": "invalid",
    "di cui schede bianche": "blank",
}
WIKIPEDIA = "https://it.wikipedia.org/w/index.php?title=Elezioni_regionali_in_Valle_d%27Aosta_del_{}&action=raw"
HISTORY_SOURCES = {
    "totals": (
        "Istituto Carlo Cattaneo",
        "Elezioni regionali nelle regioni a statuto speciale",
        "academic",
        "http://www.cattaneo.org/archivi/adele/regioni/speciale.xls",
        None,
    ),
    "lists": (
        "Wikipedia",
        "Elezioni regionali in Valle d'Aosta",
        "community",
        "https://it.wikipedia.org/wiki/Elezioni_regionali_in_Valle_d%27Aosta",
        "CC BY-SA 4.0",
    ),
}
PANACHAGE = date(1959, 12, 31)
# Lists that stand for one party, by the Wikipedia article they link to (or their name when the link is piped to
# another name), mapped to the labels `party_of` resolves; any other list is its own list vehicle.
HISTORY_PARTIES = {
    "Democrazia Cristiana": "DC",
    "Partito Comunista Italiano": "PCI",
    "Partito Socialista Italiano": "PSI",
    "Partito Socialista Democratico Italiano": "PSDI",
    "Partito Liberale Italiano": "PLI",
    "Partito Repubblicano Italiano": "PRI",
    "Movimento Sociale Italiano": "MSI",
    "Movimento Sociale Italiano - Destra Nazionale": "MSI-DN",
    "PSI-PSDI Unificati": "PSU",
    "Partito Socialista Italiano di Unità Proletaria (1964)": "PSIUP",
    "Democrazia Proletaria": "DEM.PROL",
    "Democrazia Nazionale - Costituente di Destra": "DN-CD",
    "Union Valdôtaine": "UV",
    "Lega Nord": "LEGA NORD",
    "Lega Nord - Val d'Aohta Libra": "LEGA NORD",
    "Forza Italia (1994)": "FORZA ITALIA",
    "Partito della Rifondazione Comunista": "RIFONDAZIONE COMUNISTA",
    "Alleanza Nazionale": "ALLEANZA NAZIONALE",
    "Union Valdôtaine Progressiste (1972)": "UVP-1972",
    "Democratici Popolari": "DEMOCRATICI POPOLARI",
    "Rassemblement Valdôtain (1963)": "RASSEMBLEMENT VALDÔTAIN",
    "Rassemblement Indépendant Valdôtain": "RASSEMBLEMENT VALDÔTAIN",
    "Autonomistes Démocrates Progressistes": "AUTONOMISTES DÉMOCRATES PROGRESSISTES",
}
# Regional parties of the earlier councils, keyed apart from later parties that took the same name.
FORMER_PARTIES = {
    "UVP-1972": ("union-valdotaine-progressiste-1972", "Union Valdôtaine Progressiste", "UVP"),
    "DEMOCRATICI POPOLARI": ("democratici-popolari", "Democratici Popolari", None),
    "RASSEMBLEMENT VALDÔTAIN": ("rassemblement-valdotain", "Rassemblement Valdôtain", "RV"),
    "AUTONOMISTES DÉMOCRATES PROGRESSISTES": (
        "autonomistes-democrates-progressistes",
        "Autonomistes Démocrates Progressistes",
        "ADP",
    ),
}
PARTIES = {
    "PARTITO DEMOCRATICO": "PARTITO DEMOCRATICO",
    "MOVIMENTO 5 STELLE": "MOVIMENTO 5 STELLE",
    "MOVIMENTO CINQUE STELLE": "MOVIMENTO 5 STELLE",
    "IL POPOLO DELLA LIBERTÀ": "IL POPOLO DELLA LIBERTA'",
    "POPOLO DELLA LIBERTA'": "IL POPOLO DELLA LIBERTA'",
    "LEGA SALVINI VALLÉE D'AOSTE": "LEGA",
    "LEGA VALLÉE D'AOSTE": "LEGA",
    "FRATELLI D'ITALIA - GIORGIA MELONI": "FRATELLI D'ITALIA",
}
REGIONAL_PARTIES = {
    "UNION VALDÔTAINE": ("Union Valdôtaine", "UV"),
    "STELLA ALPINA": ("Stella Alpina", "SA"),
    "UNION VALDÔTAINE PROGRESSISTE": ("Union Valdôtaine Progressiste", "UVP"),
    "FÉDÉRATION AUTONOMISTE": ("Fédération Autonomiste", "FA"),
    "FEDERAZIONE AUTONOMISTA - FEDERATION AUTONOMISTE": ("Fédération Autonomiste", "FA"),
    "ALPE": ("Autonomie Liberté Participation Écologie", "ALPE"),
}
NOTES = {
    ("02", date(2025, 9, 28)): (
        "Seats are as the region shows them after the Council of State's ruling of May 7, 2026, which upheld "
        "Cristina Machet's removal from the council; her seat went to Andrea Campotaro of Alleanza Verdi Sinistra, "
        "the list with the lower vote total among those tied on remainders."
    ),
}
HISTORY_COUNTED = (
    "Turnout (registered voters, voters, and blank and invalid ballots) is from the Istituto Cattaneo's table of "
    "special-statute regional elections, which cites ISTAT's 45 anni di elezioni in Italia 1946-1990 through 1988 and "
    "the Interior Ministry for 1993 and 1998. Votes and seats by list are from the Italian Wikipedia's article on the "
    "election, and their total matches the table's."
)
HISTORY_ALLOCATED = {
    True: (
        "The council's 35 seats were filled by a majoritarian limited vote with panachage, in which voters could choose "
        "candidates from more than one list and the leading list took most of the seats. Each list's votes are its "
        "theoretical head votes, the average vote of its candidates, so they do not add up to the valid ballots."
    ),
    False: (
        "The council's 35 seats were allocated by proportional representation among lists across the whole region, "
        "and the council elected the President of the Region."
    ),
}
COUNT_LABELS = {"Elettori": "registered", "Votanti": "voters", "Schede bianche": "blank", "Schede nulle": "null"}


def vda_url(number: int) -> str:
    return VDA.format(number)


def fetch() -> None:
    for number in VDA_HELD.values():
        raw.fetch(SOURCE, vda_url(number), suffix=".html")
    raw.fetch(SOURCE, CATTANEO, suffix=".xls")
    for day in VDA_HISTORY:
        raw.fetch(SOURCE, WIKIPEDIA.format(day.year), suffix=".wiki")


def number(text: str) -> int:
    return int(text.replace(".", "").strip())


def cattaneo(path: str) -> dict[date, dict[str, int]]:
    """Turnout by election day from the Cattaneo table's absolute-values block for the Aosta Valley."""
    totals: dict[date, dict[str, int]] = {}
    for sheet in CATTANEO_SHEETS:
        frame = pl.read_excel(path, sheet_name=sheet, has_header=False, infer_schema_length=0)
        rows = [[None if cell is None else str(cell).strip() for cell in row] for row in frame.iter_rows()]
        header = next(row for row in rows if row[0] == "Liste")
        days: dict[int, date] = {}
        for column, cell in enumerate(header[1:], start=1):
            if cell and (found := re.match(r"(\d{2})/(\d{2})/(\d{4})|(\d{4})-(\d{2})-(\d{2})", cell)):
                day, month, year = (found[1], found[2], found[3]) if found[1] else (found[6], found[5], found[4])
                days[column] = date(int(year), int(month), int(day))
        seen: set[str] = set()
        for row in rows[rows.index(header) :]:
            key = CATTANEO_LABELS.get(row[0] or "")
            if key is None or key in seen:
                continue
            seen.add(key)
            for column, day in days.items():
                totals.setdefault(day, {})[key] = int(float(str(row[column])))
    return totals


def wikipedia(text: str) -> list[dict[str, str | int | None]]:
    """Lists, votes, and seats from an article's {{Elezione}} results template."""
    block = re.search(r"(?s)\{\{Elezione\s*\n(.*?)\n\}\}", text)
    if block is None:
        raise ValueError("no {{Elezione}} template")
    cells = [re.sub(r"^\|\s*", "", cell.strip()) for cell in block[1].split("\n|")]
    cells = [cell for cell in cells if cell and not re.match(r"(votanti|elettori)\s*=", cell)]
    if len(cells) % 3:
        raise ValueError("the results template is not in label, votes, seats triples")
    lists = []
    for shown, votes, seats in zip(cells[::3], cells[1::3], cells[2::3], strict=True):
        shown = re.sub(r"(?s)<ref[^>]*/>|<ref.*?</ref>|<small>.*?</small>", "", shown)
        shown = re.sub(r"<br\s*/?>|\s\([A-Z][A-Z-]+\)", " ", shown).strip()
        links = re.findall(r"\[\[([^|\]]+)(?:\|([^\]]+))?\]\]", shown)
        label = re.sub(r"\s+", " ", re.sub(r"\[\[(?:[^|\]]+\|)?([^\]]+)\]\]", r"\1", shown)).strip()
        label = label[1:-1] if re.fullmatch(r'"[^"]+"', label) else label
        whole = len(links) == 1 and re.fullmatch(r"\[\[[^\]]+\]\]", shown)
        target = links[0][0] if whole and re.sub(r" \([^)]*\)$", "", links[0][0]) == label else label
        lists.append(
            {
                "list": label,
                "group": None,
                "votes": number(votes),
                "seats": 0 if seats == "-" else int(seats),
                "party": HISTORY_PARTIES.get(target),
            }
        )
    return lists


def vda(page: str) -> tuple[dict[str, int], list[dict[str, object]]]:
    text = re.sub(r"(?s)<script.*?</script>|<style.*?</style>", "", page)
    text = re.sub(r"</t[dh]>", "|", text)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"\s+", " ", text)
    start = text.index("Riepilogo regionale")
    end = text.find("Riepilogo per programma", start)
    end = text.index("Elenco ", start) if end < 0 else end
    summary = text[start:end]
    counts = {
        key: number(match[1])
        for label, key in COUNT_LABELS.items()
        if (match := re.search(rf"{label}\s*\|\s*([\d.]+)", summary))
    }
    body = summary[summary.index("Seggi|") + len("Seggi|") :]
    lists = []
    for match in re.finditer(
        r"\|\s*\|\s*([^|]+?)\s*\|\s*(?:(\d*)\s*\|)?\s*([\d.]+)\s*\|\s*[\d,]+%\s*\|\s*\d+\s*\|\s*(\d+|-)\s*\|", body
    ):
        lists.append(
            {
                "list": match[1].strip(),
                "group": int(match[2]) if match[2] else None,
                "votes": number(match[3]),
                "seats": 0 if match[4] == "-" else int(match[4]),
            }
        )
    return counts, lists


def parse() -> None:
    elections = []
    lists = []
    for (day, last), number_ in VDA_HELD.items():
        counts, rows = vda(raw.cached(SOURCE, vda_url(number_)).path.read_text(encoding="utf-8", errors="replace"))
        frame = pl.DataFrame(rows, schema_overrides={"group": pl.Int64})
        if frame["votes"].sum() != counts["voters"] - counts["blank"] - counts["null"]:
            raise ValueError(f"02 {day}: list votes do not add up to valid votes")
        if frame["seats"].sum() != 35:
            raise ValueError(f"02 {day}: seats do not add up to 35")
        url = vda_url(number_)
        elections.append(
            {"region": "02", "day": day, "last": last, "url": url, "counts_url": url, "size": 35, **counts}
        )
        lists.extend({"region": "02", "day": day, **row, "party": None} for row in rows)
    totals = cattaneo(str(raw.cached(SOURCE, CATTANEO).path))
    for day in VDA_HISTORY:
        url = WIKIPEDIA.format(day.year)
        rows = wikipedia(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))
        total = totals[day]
        listed = sum(int(str(row["votes"])) for row in rows)
        valid = total["voters"] - total["invalid"]
        # Under the panachage of 1949-1959 list votes are theoretical head votes, which add up to the table's
        # total but not to the valid ballots; from 1963 they add up to the valid ballots. (The table's own 1963
        # total leaves out the Union Valdôtaine's 12,930 votes, which the valid ballots include.)
        if listed != (total["listed"] if day <= PANACHAGE else valid):
            raise ValueError(f"02 {day}: list votes {listed:,} do not add up")
        if sum(int(str(row["seats"])) for row in rows) != 35:
            raise ValueError(f"02 {day}: seats do not add up to 35")
        elections.append(
            {
                "region": "02",
                "day": day,
                "last": day,
                "url": url,
                "counts_url": CATTANEO,
                "size": 35,
                "registered": total["registered"],
                "voters": total["voters"],
                "blank": total["blank"],
                "null": total["invalid"] - total["blank"],
            }
        )
        lists.extend({"region": "02", "day": day, **row} for row in rows)
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(elections).write_parquet(PARSED / "elections.parquet")
    pl.DataFrame(lists, schema_overrides={"group": pl.Int64, "party": pl.String}).write_parquet(
        PARSED / "lists.parquet"
    )


def emit() -> None:
    elections = pl.read_parquet(PARSED / "elections.parquet")
    lists = pl.read_parquet(PARSED / "lists.parquet")
    with connect() as conn:
        cs = Draft(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Regional council elections in Italy's special-statute regions from the regions' own results, "
                "and from historical compilations before them, by region"
            ),
            polity="IT",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        italy = cs.polity("IT")
        if italy in cs.minted:
            raise ValueError("publish it.interno before it.speciali")
        parties: dict[tuple[str, str], UUID] = {}

        def party_of(shown: str, party: str | None) -> UUID:
            label = party or PARTIES.get(shown.upper(), shown.upper())
            spec = EXISTING.get(label)
            if spec is not None:
                if spec not in parties:
                    parties[spec] = cs.key(*spec)
                    if parties[spec] in cs.minted:
                        raise ValueError(f"{spec} is not in the database")
                return parties[spec]
            if label in FORMER_PARTIES:
                value, full, abbreviation = FORMER_PARTIES[label]
                key, kind = ("it.party", value), "party"
            elif label in REGIONAL_PARTIES:
                full, abbreviation = REGIONAL_PARTIES[label]
                key, kind = ("it.party", slug(full)), "party"
            elif label in NEW:
                full, abbreviation, kind = NEW[label]
                key = ("it.party", slug(full))
            else:
                full, abbreviation, kind = cased(shown) if shown.isupper() else shown, None, "list_vehicle"
                key = ("it.storico.list", slug(label))
            if key not in parties:
                parties[key] = cs.key(*key)
                if parties[key] in cs.minted:
                    cs.add("party", id=parties[key], kind=kind, polity_id=italy)
                    if abbreviation is not None:
                        name(parties[key], abbreviation, "it", "abbreviation")
                    name(parties[key], full, "it")
            return parties[key]

        rows: list[dict[str, object]] = []

        def value(result_set: UUID, unit: UUID, option: UUID | None, measure: int, count: int | None) -> None:
            if count is not None:
                rows.append(
                    {
                        "result_set_id": str(result_set),
                        "unit_id": str(unit),
                        "option_id": None if option is None else str(option),
                        "measure": measure,
                        "value": count,
                    }
                )

        def result_set(key: str, election: UUID, contest: UUID | None, source: UUID) -> UUID:
            id = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=id,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            return id

        for code in sorted(elections["region"].unique()):
            info = REGIONS[code]
            source = cs.source(info.publisher, info.title)
            if source in cs.minted:
                cs.add(
                    "source", id=source, kind="official_body", publisher=info.publisher, title=info.title, url=info.home
                )
            region = cs.polity(info.iso)
            area = cs.area("it.regione", code)
            unit = cs.unit("it.regione", code)
            if area in cs.minted or unit in cs.minted:
                raise ValueError("publish it.istat before it.speciali")
            if region in cs.minted or region in cs.own_entities:
                cs.add("polity", id=region, kind="region", iso_code=info.iso)
                cs.add("slug", path=f"it/{info.slug}", entity_id=region)
                cs.add("polity_parent", polity_id=region, parent_id=italy)
                cs.add("polity_territory", polity_id=region, area_id=area)
                name(region, info.native, "it")
                if info.english != info.native:
                    name(region, info.english, "en", "english")
            body = cs.key(f"{SOURCE}.body", code)
            if body in cs.minted:
                cs.add("body", id=body, polity_id=region, kind="unicameral")
                name(body, info.council_en)
                name(body, info.council_it, "it", "native")
            held = elections.filter(pl.col("region") == code).sort("day")
            days = held["day"].to_list()
            history: dict[str, UUID] = {}
            if (held["counts_url"] != held["url"]).any():
                for role, (publisher, title, kind, home, license) in HISTORY_SOURCES.items():
                    history[role] = cs.source(publisher, title)
                    if history[role] in cs.minted or history[role] in cs.own_entities:
                        cs.add(
                            "source",
                            id=history[role],
                            kind=kind,
                            publisher=publisher,
                            title=title,
                            url=home,
                            license=license,
                        )
            # The region's own elections go first, so a list vehicle that also ran before 2003 keeps their name for it.
            for total in held.sort(pl.col("counts_url") != pl.col("url"), "day").iter_rows(named=True):
                day = total["day"]
                earlier = total["counts_url"] != total["url"]
                counted = history["totals"] if earlier else source
                listed = history["lists"] if earlier else source
                cs.artifact(raw.cached(SOURCE, total["counts_url"]), counted)
                cs.artifact(raw.cached(SOURCE, total["url"]), listed)
                label = str(day.year) if sum(other.year == day.year for other in days) == 1 else f"{day:%B} {day.year}"
                election = cs.key(f"{SOURCE}.election", f"{code}:{day}")
                cs.add(
                    "election",
                    id=election,
                    polity_id=region,
                    kind="general",
                    date_from=day,
                    date_to=total["last"],
                    status="held",
                )
                name(election, f"{label} {info.english} regional election")
                name(election, f"Elezioni regionali in {info.native} del {day.year}", "it", "native")
                path = f"it/{info.slug}/{day}-general"
                cs.add("slug", path=path, entity_id=election)
                accounting = result_set(f"{code}:{day}:election", election, None, counted)
                value(accounting, unit, None, REGISTERED, total["registered"])
                value(accounting, unit, None, VOTERS, total["voters"])
                value(accounting, unit, None, BLANK, total["blank"])
                value(accounting, unit, None, INVALID, total["null"])
                contest = cs.key(f"{SOURCE}.contest", f"{code}:{day}:list")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="list_allocation",
                    body_id=body,
                    district_area_id=area,
                    magnitude=total["size"],
                    default_result_kind="counted",
                )
                name(contest, f"{label} {info.english} regional election, council lists")
                cs.add("slug", path=f"{path}/consiglio", entity_id=contest)
                cs.add(
                    "note",
                    entity_id=contest,
                    kind="context",
                    body_md=" ".join(
                        [HISTORY_COUNTED, HISTORY_ALLOCATED[day <= PANACHAGE]]
                        if earlier
                        else [info.counted, info.allocated, *filter(None, [NOTES.get((code, day))])]
                    ),
                )
                list_set = result_set(f"{code}:{day}:list", election, contest, listed)
                for row in (
                    lists.filter(pl.col("region") == code, pl.col("day") == day)
                    .sort("votes", "list", descending=[True, False])
                    .iter_rows(named=True)
                ):
                    option = cs.key(f"{SOURCE}.option", f"{code}:{day}:list:{row['list']}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        party_id=party_of(row["list"], row["party"]),
                        ballot_label=row["list"],
                    )
                    value(list_set, unit, option, VOTES, row["votes"])
                    if row["seats"]:
                        cs.add("outcome", result_set_id=list_set, option_id=option, kind="won", seats=row["seats"])
        cs.frame(
            "result_value",
            pl.DataFrame(rows, schema_overrides={"value": pl.Int64}).with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.col("measure").cast(pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        cs.write()
