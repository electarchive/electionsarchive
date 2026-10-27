import difflib
import io
import re
import unicodedata
import zipfile
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.it.interno import proper
from ea.sources.it.storico import EXISTING, NEW, NEW_OVERRIDES, NORTH, OVERRIDES, SOUTH, SYMBOLS
from ea.store import DATA, connect

SOURCE = "it.senato"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
ARCHIVE = "https://dait.interno.gov.it/documenti/opendata/senato/senato-{day:%Y%m%d}.zip"
ELIGENDO = (
    "https://elezionistorico.interno.gov.it/index.php?tpel=S&dtel={day:%d/%m/%Y}&tpa=I&tpe=R&lev0=0&levsut0=0"
    "&levsut1=1&es0=S&es1=S&ms=S&ne1={n}&lev1={n}"
)
NATIONAL = "https://elezionistorico.interno.gov.it/index.php?tpel=S&dtel={day:%d/%m/%Y}&tpa=I&tpe=A&lev0=0&levsut0=0&es0=S&ms=S"
QUERY = """PREFIX osr: <http://dati.senato.it/osr/>
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
SELECT DISTINCT ?s ?m ?cognome ?nome ?nascita ?genere ?leg ?regione ?collegio ?tipo ?inizio ?fine ?fineTipo WHERE {
 ?s a osr:Senatore ; foaf:lastName ?cognome ; foaf:firstName ?nome ; osr:mandato ?m .
 ?m osr:legislatura ?leg ; osr:tipoMandato ?tm ; osr:inizio ?inizio . FILTER(str(?leg) = "%d" && str(?tm) = "elettivo")
 OPTIONAL { ?m osr:regioneElezione ?regione } OPTIONAL { ?m osr:collegioElezione ?collegio }
 OPTIONAL { ?m osr:tipoElezione ?tipo } OPTIONAL { ?m osr:fine ?fine } OPTIONAL { ?m osr:tipoFineMandato ?fineTipo }
 OPTIONAL { ?s osr:dataNascita ?nascita } OPTIONAL { ?s foaf:gender ?genere }
}"""
DAYS = [
    date(1948, 4, 18),
    date(1953, 6, 7),
    date(1958, 5, 25),
    date(1963, 4, 28),
    date(1968, 5, 19),
    date(1972, 5, 7),
    date(1976, 6, 20),
    date(1979, 6, 3),
    date(1983, 6, 26),
    date(1987, 6, 14),
    date(1992, 4, 5),
    date(1994, 3, 27),
    date(1996, 4, 21),
    date(2001, 5, 13),
]
MATTARELLA = date(1994, 3, 27)
CALDEROLI = date(2006, 4, 9)
SIZES = [
    (237, date(1948, 5, 8)),
    (246, date(1958, 6, 12)),
    (315, date(1963, 5, 16)),
    (200, date(2022, 10, 13)),
]
REGIONS = {
    "PIEMONTE": "01",
    "VALLE D AOSTA": "02",
    "LOMBARDIA": "03",
    "TRENTINO ALTO ADIGE": "04",
    "VENETO": "05",
    "FRIULI VENEZIA GIULIA": "06",
    "LIGURIA": "07",
    "EMILIA ROMAGNA": "08",
    "TOSCANA": "09",
    "UMBRIA": "10",
    "MARCHE": "11",
    "LAZIO": "12",
    "ABRUZZO": "13",
    "ABRUZZI": "13",
    "MOLISE": "14",
    "ABRUZZI E MOLISE": "13-14",
    "CAMPANIA": "15",
    "PUGLIA": "16",
    "BASILICATA": "17",
    "CALABRIA": "18",
    "SICILIA": "19",
    "SARDEGNA": "20",
}
INDEPENDENT = {"IND", "INDIPENDENTE"}
REGISTERED, VOTERS, BLANK, VOTES = 2, 14, 6, 1
SYSTEMS = {
    "senato-1948": (
        {"formula": "dhondt", "lists": {"order": "open", "constituencies": "skip", "vacancy": "next_by_votes"}},
        "Senate, single-member collegi with regional groups, 1948–1993",
        (
            "Under law 29 of 1948, each region was divided into single-member collegi. A candidate who won at least 65% "
            "of the valid votes in a collegio was elected outright. The region's other seats went to groups of "
            "candidates who ran under the same symbol, shared by the D'Hondt method on each group's votes across the "
            "region, less the votes of its candidates already elected outright. Within a group, seats went to the "
            "candidates with the highest share of the vote in their own collegio, so a collegio could elect several "
            "senators or none. The schema stores the D'Hondt step; the 65% rule and the ranking by share are described "
            "here only."
        ),
    ),
    "senato-single-member-1993": (
        {"formula": "fptp"},
        "Senate, single-member collegi, 1993–2005",
        None,
    ),
    "senato-recovery-1993": (
        {"formula": "dhondt", "lists": {"order": "open", "constituencies": "skip", "vacancy": "next_by_votes"}},
        "Senate, regional proportional recovery, 1993–2005",
        (
            "Under law 276 of 1993, three quarters of each region's senators were elected by plurality in single-member "
            "collegi. The rest were shared out in each region by the D'Hondt method among groups of candidates who ran "
            "under the same symbol. A group's total was the votes of its candidates who had not won their collegio "
            "(scorporo), and its seats went to those candidates in order of their share of the vote in their own "
            "collegio. The schema stores the D'Hondt step; the scorporo and the ranking by share are described here only."
        ),
    ),
    "senato-valle-d-aosta": ({"formula": "fptp"}, "Senate, Valle d'Aosta single-member seat", None),
}
LICENSE = (
    "No license stated. Data that Italian public bodies publish without a license are open by default (art. 52 of "
    "the Codice dell'amministrazione digitale)."
)
BODY_NOTE = (
    "Seats count the elected senators only. Senators for life, former presidents, and in the first legislature "
    "(1948–1953) the 107 senators by right under the Constitution's third transitional provision sat in addition."
)
GROUP_NOTE = (
    "Seats are each group's seats in the region as Eligendo's historical results give them. Who took them is the "
    "Interior Ministry's archive, whose candidates carry an elected flag."
)
REPLACED_NOTE = (
    "The Interior Ministry's archive flags {flagged} elected candidates in this region, but the region elected "
    "{declared} senators. The elected senators here are the ones the Senate's own record lists at the legislature's "
    "first sitting, matched to the archive's candidates by name."
)
UNPLACED_NOTE = (
    "The archive has no candidate names for {label} in some of this region's collegi, so {names} appear only in "
    "this regional allocation, from the Senate's record of its members; their votes are the party's votes in those "
    "collegi."
)
UNNAMED_NOTE = (
    "The archive gives this party's votes in the collegio, summed from its municipal returns, but not its "
    "candidate's name."
)
ROMAN = re.compile(r"\b(I|Ii|Iii|Iv|V|Vi|Vii|Viii|Ix|X|Xi|Xii)\b")


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().upper()
    return " ".join(re.sub(r"[^A-Z0-9 ]", " ", text).split())


def label_of(text: str) -> str:
    return ROMAN.sub(lambda match: match[1].upper(), proper(text).replace(" - ", "–"))


def region_of(text: str) -> str:
    folded = fold(text)
    return REGIONS.get(folded) or REGIONS[next(name for name in REGIONS if folded.startswith(name))]


def mandates(legislature: int) -> str:
    return "https://dati.senato.it/sparql?" + urlencode({"query": QUERY % legislature, "format": "text/csv"})


def fetch() -> None:
    for legislature, day in enumerate(DAYS, 1):
        raw.fetch(SOURCE, ARCHIVE.format(day=day), suffix=".zip")
        raw.fetch(SOURCE, NATIONAL.format(day=day), suffix=".html")
        for n in range(1, 21):
            raw.fetch(SOURCE, ELIGENDO.format(day=day, n=n), suffix=".html")
        raw.fetch(SOURCE, mandates(legislature), suffix=".csv")


def eligendo(day: date) -> pl.DataFrame:
    rows = []
    for n in range(1, 21):
        page = raw.cached(SOURCE, ELIGENDO.format(day=day, n=n)).path.read_text(encoding="utf-8", errors="replace")
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>", "", page, flags=re.DOTALL)))
        found = re.search(r"Area ITALIA Regione (.+?) Affluenza", text)
        if found is None:
            continue
        for label, votes, seats in re.findall(
            r"class='candidato'>([^<]+)</th><td[^>]*>([\d.]+)</td><td[^>]*>[\d,]+</td><td[^>]*>(\d+)</td>", page
        ):
            label = re.sub(r"\s*\(ELETTO\)$", "", label.replace("&#039;", "'").replace("&amp;", "&").strip())
            rows.append((day, region_of(found[1]), label, int(votes.replace(".", "")), int(seats)))
    frame = pl.DataFrame(rows, schema=["day", "region", "label", "votes", "seats"], orient="row")
    return frame.group_by("day", "region", "label").agg(pl.col("votes", "seats").sum())


def national(day: date) -> int:
    page = raw.cached(SOURCE, NATIONAL.format(day=day)).path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))
    return int(re.search(r"TOTALI [\d.]+ (\d+)", text)[1])


def senators(legislature: int) -> pl.DataFrame:
    frame = pl.read_csv(raw.cached(SOURCE, mandates(legislature)).path, infer_schema=False)
    return frame.select(
        pl.lit(legislature).alias("legislature"),
        pl.col("s").str.extract(r"(\d+)$").alias("senator"),
        pl.col("cognome").fill_null("").alias("surname"),
        pl.col("nome").alias("forename"),
        pl.col("nascita").str.to_date(strict=False).alias("birth"),
        pl.col("genere").alias("gender"),
        pl.col("regione").fill_null("").alias("region_name"),
        pl.col("collegio").alias("collegio"),
        pl.col("tipo").alias("kind"),
        pl.col("inizio").str.to_date().alias("start"),
    ).unique(["senator", "start"])


def match(candidates: list[dict], surname: str, forename: str) -> dict | None:
    target, first = fold(f"{surname} {forename}"), (fold(surname).split() or [""])[0]
    letters = {word[0] for word in fold(forename).split()}
    best, score = None, 0.0
    for row in candidates:
        words = fold(row["surname"]).split()
        ratio = difflib.SequenceMatcher(None, target, fold(f"{row['surname']} {row['forename']}")).ratio()
        if (
            words
            and difflib.SequenceMatcher(None, first, words[0]).ratio() >= 0.85
            and (len(first) >= 4 or difflib.SequenceMatcher(None, fold(surname), fold(row["surname"])).ratio() >= 0.85)
        ):
            initials = {word[0] for word in fold(row["forename"]).split()}
            ratio += 1 + (0.5 if initials and initials <= letters | {word[0] for word in words[1:]} else 0)
        if ratio > score:
            best, score = row, ratio
    return best if score >= 0.8 and (score < 1 or score >= 1.4) else None


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    all_candidates, all_turnout, all_lists, all_seats, all_senators, all_unplaced, all_declared = (
        [],
        [],
        [],
        [],
        [],
        [],
        [],
    )
    for legislature, day in enumerate(DAYS, 1):
        with zipfile.ZipFile(raw.cached(SOURCE, ARCHIVE.format(day=day)).path) as archive:
            names = archive.namelist()
            candlista = next(name for name in names if "candlista" in name.lower())
            communes = next(name for name in names if re.search(r"senato-\d{8}\.txt$", name, re.IGNORECASE))

            def read(member: str) -> pl.DataFrame:
                frame = pl.read_csv(io.BytesIO(archive.read(member)), separator=";", infer_schema=False)
                return frame.with_columns(pl.col(c).str.strip_chars() for c in frame.columns)

            cand = read(candlista)
            comm = read(communes)
        cand = cand.select(
            pl.lit(day).alias("day"),
            pl.col("REGIONE").map_elements(region_of, return_dtype=pl.String).alias("region"),
            pl.col("COLLEGIO").alias("collegio"),
            pl.col("descrlista").alias("label"),
            pl.col("votiLista").cast(pl.Int64).alias("votes"),
            pl.col("cognome").fill_null("").alias("surname"),
            pl.col("nome").fill_null("").alias("forename"),
            pl.col("datanascita").str.to_date("%d/%m/%Y", strict=False).alias("birth"),
            pl.col("sesso").alias("sex"),
            (pl.col("CODTIPOELETTO").fill_null("") == "E").alias("flagged"),
        ).with_columns(pl.col("collegio").map_elements(fold, return_dtype=pl.String).alias("key"))
        comm = comm.select(
            pl.col("REGIONE").map_elements(region_of, return_dtype=pl.String).alias("region"),
            pl.col("COLLEGIO").map_elements(fold, return_dtype=pl.String).alias("key"),
            pl.col("COMUNE").alias("commune"),
            pl.col("ELETTORI").cast(pl.Int64).alias("registered"),
            pl.col("VOTANTI").cast(pl.Int64).alias("voters"),
            pl.col("SCHEDE_BIANCHE").cast(pl.Int64).alias("blank"),
            pl.col("LISTA").alias("label"),
            pl.col("VOTI_LISTA").cast(pl.Int64).alias("votes"),
        )
        all_turnout.append(
            comm.unique(["region", "key", "commune"])
            .group_by("region", "key")
            .agg(pl.col("registered", "voters", "blank").sum())
            .with_columns(pl.lit(day).alias("day"))
        )
        all_lists.append(
            comm.group_by("region", "key", "label").agg(pl.col("votes").sum()).with_columns(pl.lit(day).alias("day"))
        )
        seats = eligendo(day)
        all_seats.append(seats)
        roll = senators(legislature)
        all_senators.append(roll)
        first = roll["start"].min()
        opening = roll.filter((pl.col("region_name") != "") & (pl.col("start") <= first + timedelta(days=7))).unique(
            "senator"
        )
        opening = opening.with_columns(
            pl.col("region_name").map_elements(region_of, return_dtype=pl.String).alias("region")
        ).with_columns(
            pl.when(pl.col("region").is_in(["13", "14"]) & (day < date(1963, 1, 1)) & (day > date(1949, 1, 1)))
            .then(pl.lit("13-14"))
            .otherwise("region")
            .alias("region")
        )
        consistent = seats.filter(pl.col("region") != "02")["seats"].sum() + 1 == national(day)
        if consistent:
            declared = dict(seats.group_by("region").agg(pl.col("seats").sum()).iter_rows())
        else:
            declared = dict(opening.group_by("region").len().iter_rows())
        elected: dict[tuple[str, str, str, str], str | None] = {}
        replaced = []
        for region in sorted(cand["region"].unique()):
            pool = cand.filter(pl.col("region") == region).to_dicts()
            flagged = [row for row in pool if row["flagged"]]
            members = [
                row
                for row in roll.filter(pl.col("region_name") != "").to_dicts()
                if region_of(row["region_name"]) in {region, *region.split("-")}
            ]
            if region == "02" or len(flagged) == declared[region]:
                for row in flagged:
                    senator = match(members, row["surname"], row["forename"]) if row["surname"] else None
                    elected[region, row["key"], row["surname"], row["forename"]] = senator and senator["senator"]
                nameless = [row for row in flagged if not row["surname"] and not row["forename"]]
                left = set(opening.filter(pl.col("region") == region)["senator"]) - set(elected.values())
                if len(nameless) == 1 and len(left) == 1:
                    row = nameless[0]
                    elected[region, row["key"], row["surname"], row["forename"]] = left.pop()
                continue
            replaced.append(region)
            for senator in opening.filter(pl.col("region") == region).to_dicts():
                row = match(pool, senator["surname"], senator["forename"]) or match(
                    [row for row in cand.to_dicts() if row["flagged"] and row["region"] != region],
                    senator["surname"],
                    senator["forename"],
                )
                if row is None:
                    all_unplaced.append({"day": day, "region": region, "senator": senator["senator"]})
                else:
                    elected[row["region"], row["key"], row["surname"], row["forename"]] = senator["senator"]
        all_declared.append(
            pl.DataFrame(
                {
                    "day": day,
                    "region": list(declared),
                    "seats": list(declared.values()),
                    "replaced": [r in replaced for r in declared],
                    "consistent": consistent,
                }
            )
        )
        cand = cand.with_columns(
            pl.struct("region", "key", "surname", "forename")
            .map_elements(
                lambda s, elected=elected: (s["region"], s["key"], s["surname"], s["forename"]) in elected,
                return_dtype=pl.Boolean,
            )
            .alias("elected"),
            pl.struct("region", "key", "surname", "forename")
            .map_elements(
                lambda s, elected=elected: elected.get((s["region"], s["key"], s["surname"], s["forename"])),
                return_dtype=pl.String,
            )
            .alias("senator"),
        )
        all_candidates.append(cand)
    pl.concat(all_candidates).write_parquet(PARSED / "candidates.parquet")
    pl.concat(all_turnout).write_parquet(PARSED / "turnout.parquet")
    pl.concat(all_lists).write_parquet(PARSED / "lists.parquet")
    pl.concat(all_seats).write_parquet(PARSED / "seats.parquet")
    pl.concat(all_senators).write_parquet(PARSED / "senators.parquet")
    pl.concat(all_declared).write_parquet(PARSED / "declared.parquet")
    pl.DataFrame(all_unplaced, schema={"day": pl.Date, "region": pl.String, "senator": pl.String}).write_parquet(
        PARSED / "unplaced.parquet"
    )


def emit() -> Path:
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    turnout = pl.read_parquet(PARSED / "turnout.parquet")
    lists = pl.read_parquet(PARSED / "lists.parquet")
    seats = pl.read_parquet(PARSED / "seats.parquet")
    declared = pl.read_parquet(PARSED / "declared.parquet")
    roll = pl.read_parquet(PARSED / "senators.parquet").sort("start").unique("senator", keep="first")
    unplaced = pl.read_parquet(PARSED / "unplaced.parquet")
    record = {row["senator"]: row for row in roll.to_dicts()}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Italian Senate elections 1948–2001: candidates and votes by collegio, regional group seats, and the elected senators",
            polity="IT",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        def existing(scheme: str, value: str) -> UUID:
            entity = cs.key(scheme, value)
            if entity in cs.minted:
                raise ValueError(f"{scheme}:{value} is not in the database")
            return entity

        interno = cs.source("Ministero dell'Interno", "Eligendo: archivio storico delle elezioni, open data")
        if interno in cs.minted:
            raise ValueError("publish it.interno before it.senato")
        senate_record = cs.source("Senato della Repubblica", "dati.senato.it: mandati dei senatori")
        if senate_record in cs.minted:
            cs.add(
                "source",
                id=senate_record,
                kind="official_body",
                publisher="Senato della Repubblica",
                title="dati.senato.it: mandati dei senatori",
                url="https://dati.senato.it/",
                license="CC BY 3.0",
            )
        for legislature, day in enumerate(DAYS, 1):
            cs.artifact(raw.cached(SOURCE, ARCHIVE.format(day=day)), interno)
            cs.artifact(raw.cached(SOURCE, NATIONAL.format(day=day)), interno)
            for n in range(1, 21):
                cs.artifact(raw.cached(SOURCE, ELIGENDO.format(day=day, n=n)), interno)
            cs.artifact(raw.cached(SOURCE, mandates(legislature)), senate_record)

        italy = cs.polity("IT")
        nation = cs.area("it.nation", "IT")
        region_areas = {code: cs.area("it.regione", code) for code in set(REGIONS.values()) if "-" not in code}
        if nation in cs.minted or any(area in cs.minted for area in region_areas.values()):
            raise ValueError("publish it.istat before it.senato")
        region_names: dict[str, str] = dict(
            conn.execute(
                """select c.code, n.text from area a join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id where a.scheme_code = 'it.regione' and n.is_preferred"""
            ).fetchall()
        )
        region_names["13-14"] = "Abruzzi e Molise"
        schemes = {code for (code,) in conn.execute("select code from scheme where code like 'it.%'").fetchall()}
        for code, rank, noun in (
            ("it.regione_senato", 1, "Senate region"),
            ("it.collegio_senato", 3, "Senate college"),
            ("it.collegio_senato_1993", 3, "Senate college"),
        ):
            if code not in schemes:
                cs.add("scheme", code=code, polity_id=italy, kind="electoral_district", rank=rank, noun=noun)
        region_areas["13-14"] = cs.area("it.regione_senato", "13-14")
        if region_areas["13-14"] in cs.minted:
            cs.add("area", id=region_areas["13-14"], scheme_code="it.regione_senato")
            cs.add(
                "area_code",
                area_id=region_areas["13-14"],
                code="13-14",
                valid_from=date(1948, 4, 18),
                valid_to=date(1963, 4, 28),
            )
            cs.add(
                "area_parent",
                area_id=region_areas["13-14"],
                parent_id=nation,
                valid_from=date(1948, 4, 18),
                valid_to=date(1963, 4, 28),
            )
            name(region_areas["13-14"], "Abruzzi e Molise", "it")

        senate = cs.key("it.body", "senate")
        if senate in cs.minted:
            cs.add("body", id=senate, polity_id=italy, kind="upper_house")
            name(senate, "Senate of the Republic")
            name(senate, "Senato della Repubblica", "it", "native")
            cs.add("note", entity_id=senate, kind="methodology", body_md=BODY_NOTE)
        sized = {row[0] for row in conn.execute("select valid_from from body_size where body_id = %s", [senate])}
        for (size, start), end in zip(SIZES, [start for _, start in SIZES[1:]] + [None], strict=True):
            if start not in sized:
                cs.add("body_size", body_id=senate, seats=size, valid_from=start, valid_to=end)

        systems: dict[str, UUID] = {}
        for code, (rules, label, methodology) in SYSTEMS.items():
            systems[code] = cs.key("it.electoral_system", code)
            if systems[code] in cs.minted:
                cs.add("electoral_system", id=systems[code], polity_id=italy, rules=rules, rules_version=1)
                name(systems[code], label)
                if methodology is not None:
                    cs.add("note", entity_id=systems[code], kind="methodology", body_md=methodology)

        parties: dict[tuple[str, str], UUID] = {}

        def party_of(label: str, year: int) -> UUID | None:
            if label in INDEPENDENT:
                return None
            spec = OVERRIDES.get((label, year)) or EXISTING.get(label)
            if spec is not None:
                if spec not in parties:
                    parties[spec] = existing(*spec)
                return parties[spec]
            full, abbreviation, kind = (
                NEW_OVERRIDES.get((label, year)) or NEW.get(label) or (label, None, "list_vehicle")
            )
            known = (label, year) in NEW_OVERRIDES or label in NEW
            key = ("it.party", slug(full)) if known else ("it.storico.list", slug(label))
            if key not in parties:
                parties[key] = cs.key(*key)
                if parties[key] in cs.minted:
                    cs.add("party", id=parties[key], kind=kind, polity_id=italy)
                    name(parties[key], full, "it")
                    if abbreviation is not None:
                        name(parties[key], abbreviation, "it", "abbreviation")
            return parties[key]

        coalitions = {**SYMBOLS, "POLO DELLE LIBERTA'": NORTH, "POLO BUON GOVERNO": SOUTH}
        alliances: dict[tuple[date, str], UUID] = {}

        def alliance_of(label: str, day: date, election: UUID) -> UUID | None:
            if label not in coalitions or day < MATTARELLA:
                return None
            english, italian = coalitions[label]
            if (day, english) not in alliances:
                alliances[day, english] = cs.key("it.alliance", f"{day}:{slug(english)}")
                if alliances[day, english] in cs.minted:
                    cs.add("alliance", id=alliances[day, english], kind="electoral_alliance", election_id=election)
                    name(alliances[day, english], english)
                    name(alliances[day, english], italian, "it", "native")
            return alliances[day, english]

        def value_row(result_set: UUID, unit: UUID, option: UUID | None, measure: int, value: int) -> None:
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=value,
            )

        def result_set(key: str, election: UUID, contest: UUID, completeness: float = 1) -> UUID:
            id = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=id,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=interno,
                is_default=True,
                completeness=completeness,
            )
            return id

        known_people = {
            scheme: {value for (value,) in conn.execute("select value from external_id where scheme = %s", [scheme])}
            for scheme in ("it.senato.senatore", "it.interno.person")
        }
        bound: dict[tuple[str, str], UUID] = {}

        def person_of(row: dict, day: date, region: str) -> UUID:
            senator = record.get(row.get("senator") or "")
            keys = []
            if senator is not None:
                keys.append(("it.senato.senatore", senator["senator"]))
            if row.get("birth") is not None:
                keys.append(("it.interno.person", f"{row['surname']}:{row['forename']}:{row['birth']}"))
            if not keys:
                keys.append((f"{SOURCE}.person", f"{day}:{region}:{row['key']}:{row['surname']}:{row['forename']}"))
            entity = next((bound[key] for key in keys if key in bound), None)
            if entity is None:
                entity = next((cs.key(*key) for key in keys if key[1] in known_people.get(key[0], ())), None)
            fresh = None
            if entity is None:
                fresh = keys[0]
                entity = cs.key(*fresh)
                birth = senator["birth"] if senator is not None and senator["birth"] is not None else row.get("birth")
                sex = row.get("sex") or (senator and {"male": "M", "female": "F"}.get(senator["gender"] or ""))
                cs.add(
                    "person",
                    id=entity,
                    birth_date=birth,
                    birth_precision=None if birth is None else "day",
                    gender={"M": "male", "F": "female"}.get(sex or ""),
                )
                display = (
                    f"{senator['forename']} {senator['surname']}"
                    if senator is not None
                    else f"{proper(row['forename'])} {proper(row['surname'])}".strip()
                )
                name(entity, display, "it")
            for key in keys:
                if key not in bound and key != fresh and key[1] not in known_people.get(key[0], ()):
                    cs.add("external_id", entity_id=entity, scheme=key[0], value=key[1])
                bound[key] = entity
            return entity

        colleges: dict[tuple[str, str], tuple[UUID, UUID]] = {}

        def college(region: str, key: str, label: str, day: date) -> tuple[UUID, UUID]:
            scheme, start, end = (
                ("it.collegio_senato", DAYS[0], MATTARELLA)
                if day < MATTARELLA
                else ("it.collegio_senato_1993", MATTARELLA, CALDEROLI)
            )
            code = f"{region}-{slug(key)}"
            if (scheme, code) in colleges:
                return colleges[scheme, code]
            area = cs.area(scheme, code, on=start)
            unit = cs.unit(scheme, code, on=start)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code=scheme)
                cs.add("area_code", area_id=area, code=code, valid_from=start, valid_to=end)
                cs.add("area_parent", area_id=area, parent_id=region_areas[region], valid_from=start, valid_to=end)
                name(area, label, "it")
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=italy,
                    scheme_code=scheme,
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=start,
                    valid_to=end,
                )
                name(unit, label, "it")
            colleges[scheme, code] = (area, unit)
            return colleges[scheme, code]

        def collegio_contest(day, election, path, region, key, rows, parent, system) -> list[dict]:
            label = label_of(rows[0]["collegio"])
            area, unit = college(region, key, label, day)
            contest_key = f"{day}:senate:{region}:{slug(key)}"
            contest = cs.key("it.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=senate,
                district_area_id=area,
                magnitude=None if parent is not None else 1,
                allocation_parent_id=parent,
                electoral_system_id=systems[system],
                default_result_kind="counted",
            )
            name(contest, f"Senate, {label}")
            cs.add("slug", path=f"{path}/senate/{slug(region_names[region])}/{slug(key)}", entity_id=contest)
            counted = result_set(contest_key, election, contest)
            accounts = (
                turnout.filter((pl.col("day") == day) & (pl.col("region") == region) & (pl.col("key") == key))
                .select("registered", "voters", "blank")
                .rows()
            )
            cap = accounts[0][1] - accounts[0][2] if accounts else None
            named = sum(row["votes"] for row in rows)
            labels = {row["label"] for row in rows}
            regional = set(candidates.filter((pl.col("day") == day) & (pl.col("region") == region))["label"])
            unnamed = [
                (label, votes)
                for label, votes in lists.filter(
                    (pl.col("day") == day)
                    & (pl.col("region") == region)
                    & (pl.col("key") == key)
                    & (pl.col("votes") > 0)
                )
                .select("label", "votes")
                .iter_rows()
                if label not in labels and label in regional
            ]
            if cap is None or named + sum(votes for _, votes in unnamed) > cap:
                unnamed = []
            if cap is not None and named + sum(votes for _, votes in unnamed) <= cap:
                registered, voters, blank = accounts[0]
                if registered >= voters:
                    value_row(counted, unit, None, REGISTERED, registered)
                value_row(counted, unit, None, VOTERS, voters)
                value_row(counted, unit, None, BLANK, blank)
            top = max(row["votes"] for row in rows)
            placed = []
            for row in sorted(rows, key=lambda row: (-row["votes"], row["surname"], row["forename"])):
                option_key = f"{contest_key}:{row['surname']}:{row['forename']}:{row['label']}"
                option = cs.key("it.option", option_key)
                party = party_of(row["label"], day.year)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party if alliance_of(row["label"], day, election) is None else None,
                    alliance_id=alliance_of(row["label"], day, election),
                    ballot_label=row["label"],
                )
                person = None
                if row["surname"] or row["forename"] or row["senator"]:
                    person = person_of(row, day, region)
                    candidacy = cs.key("it.candidacy", option_key)
                    cs.add(
                        "candidacy",
                        id=candidacy,
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=f"{row['surname']} {row['forename']}".strip() or None,
                        is_independent=row["label"] in INDEPENDENT,
                    )
                    if party is not None and alliance_of(row["label"], day, election) is None:
                        cs.add("candidacy_nomination", candidacy_id=candidacy, party_id=party, kind="nominated")
                value_row(counted, unit, option, VOTES, row["votes"])
                wins_here = row["elected"] and (parent is not None or row["votes"] == top)
                if wins_here:
                    cs.add("outcome", result_set_id=counted, option_id=option, kind="elected")
                placed.append(
                    row | {"person": person, "single": parent is None and row["elected"] and row["votes"] == top}
                )
            for missing, votes in unnamed:
                option = cs.key("it.option", f"{contest_key}::{missing}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party_of(missing, day.year),
                    ballot_label=missing,
                )
                value_row(counted, unit, option, VOTES, votes)
                cs.add("note", entity_id=option, kind="caveat", body_md=UNNAMED_NOTE)
            return placed

        def group_contest(day, election, path, region, magnitude, system, title, members, won, note) -> UUID:
            contest_key = f"{day}:senate:{region}"
            contest = cs.key("it.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="list_allocation",
                body_id=senate,
                district_area_id=region_areas[region],
                magnitude=magnitude,
                electoral_system_id=systems[system],
                default_result_kind="counted",
            )
            name(contest, title)
            cs.add("slug", path=f"{path}/senate/{slug(region_names[region])}", entity_id=contest)
            counted = result_set(contest_key, election, contest, 0)
            for body in note:
                cs.add("note", entity_id=counted, kind="caveat", body_md=body)
            for label in sorted(set(won) | {row["label"] for row in members}):
                option = cs.key("it.option", f"{contest_key}:{label}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="list",
                    party_id=party_of(label, day.year) if alliance_of(label, day, election) is None else None,
                    alliance_id=alliance_of(label, day, election),
                    ballot_label=label,
                )
                if won.get(label):
                    cs.add("outcome", result_set_id=counted, option_id=option, kind="won", seats=won[label])
                for row in {
                    row["person"]: row for row in members if row["label"] == label and row["person"] is not None
                }.values():
                    candidacy = cs.key("it.candidacy", f"{contest_key}:{label}:{row['person']}")
                    cs.add(
                        "candidacy",
                        id=candidacy,
                        person_id=row["person"],
                        option_id=option,
                        role="list_member",
                        ballot_name=f"{row['surname']} {row['forename']}".strip() or None,
                        is_independent=label in INDEPENDENT,
                    )
                    cs.add("outcome", result_set_id=counted, option_id=option, candidacy_id=candidacy, kind="elected")
            return contest

        def reserve(day: date, region: str) -> list[dict]:
            members = []
            for (senator,) in (
                unplaced.filter((pl.col("day") == day) & (pl.col("region") == region)).select("senator").iter_rows()
            ):
                row = record[senator]
                members.append(
                    {
                        "senator": senator,
                        "surname": row["surname"].upper(),
                        "forename": row["forename"].upper(),
                        "key": "",
                        "birth": None,
                        "sex": None,
                    }
                )
            return members

        for day in DAYS:
            election = existing("it.election", f"{day}:general")
            path = f"it/{day}-general"
            day_seats = seats.filter(pl.col("day") == day)
            day_declared = {
                r: (n, c)
                for r, n, c in declared.filter(pl.col("day") == day).select("region", "seats", "consistent").iter_rows()
            }
            day_candidates = candidates.filter(pl.col("day") == day)
            for region in sorted(day_candidates["region"].unique()):
                rows = day_candidates.filter(pl.col("region") == region).to_dicts()
                by_key: dict[str, list[dict]] = {}
                for row in rows:
                    by_key.setdefault(row["key"], []).append(row)
                if region == "02":
                    for key, group in by_key.items():
                        collegio_contest(day, election, path, region, key, group, None, "senato-valle-d-aosta")
                    continue
                magnitude, consistent = day_declared[region]
                eligendo = {
                    label: n
                    for label, n in day_seats.filter(pl.col("region") == region).select("label", "seats").iter_rows()
                    if n
                }
                extra = reserve(day, region)
                if day < MATTARELLA:
                    parent = cs.key("it.contest", f"{day}:senate:{region}")
                    placed = [
                        row
                        for key, group in sorted(by_key.items())
                        for row in collegio_contest(day, election, path, region, key, group, parent, "senato-1948")
                    ]
                    members = [row for row in placed if row["elected"]]
                    counts: dict[str, int] = {}
                    for row in members:
                        counts[row["label"]] = counts.get(row["label"], 0) + 1
                    deficit = [label for label, n in eligendo.items() if n > counts.get(label, 0)]
                    for row in extra:
                        if len(deficit) != 1:
                            raise ValueError(
                                f"{day} {region}: cannot place senator {row['senator']} in a group ({deficit})"
                            )
                        members.append(row | {"label": deficit[0], "person": person_of(row, day, region)})
                    counts = {}
                    for row in members:
                        counts[row["label"]] = counts.get(row["label"], 0) + 1
                    won = eligendo if consistent else counts
                    note = [GROUP_NOTE] if consistent else []
                    flagged = sum(1 for row in rows if row["flagged"])
                    if flagged != sum(counts.values()) or extra:
                        note.append(REPLACED_NOTE.format(flagged=flagged, declared=magnitude))
                    if extra:
                        note.append(
                            UNPLACED_NOTE.format(
                                label=extra and members[-1]["label"],
                                names=", ".join(
                                    f"{record[r['senator']]['forename']} {record[r['senator']]['surname']}"
                                    for r in extra
                                ),
                            )
                        )
                    if consistent and counts != eligendo:
                        differ = sorted(set(counts) | set(eligendo))
                        note.append(
                            "Eligendo and the archive differ on the group of some senators here: "
                            + "; ".join(
                                f"{label} {eligendo.get(label, 0)} seats, {counts.get(label, 0)} senators"
                                for label in differ
                                if eligendo.get(label, 0) != counts.get(label, 0)
                            )
                            + ". Seats follow Eligendo."
                        )
                    group_contest(
                        day,
                        election,
                        path,
                        region,
                        magnitude,
                        "senato-1948",
                        f"Senate, {region_names[region]}",
                        members,
                        won,
                        note,
                    )
                    continue
                placed = [
                    row
                    for key, group in sorted(by_key.items())
                    for row in collegio_contest(
                        day, election, path, region, key, group, None, "senato-single-member-1993"
                    )
                ]
                singles = sum(1 for row in placed if row["single"])
                if singles != len(by_key):
                    raise ValueError(f"{day} {region}: {singles} collegio winners in {len(by_key)} collegi")
                members = [row for row in placed if row["elected"] and not row["single"]]
                if magnitude - singles == 0:
                    continue
                counts = {}
                for row in members:
                    counts[row["label"]] = counts.get(row["label"], 0) + 1
                wins = {}
                for row in placed:
                    if row["single"]:
                        wins[row["label"]] = wins.get(row["label"], 0) + 1
                recovered = {
                    label: n - wins.get(label, 0) for label, n in eligendo.items() if n - wins.get(label, 0) > 0
                }
                note = (
                    []
                    if recovered == counts
                    else [
                        "Eligendo's seats less the collegio winners differ from the archive's recovered senators here; seats follow the archive."
                    ]
                )
                group_contest(
                    day,
                    election,
                    path,
                    region,
                    magnitude - singles,
                    "senato-recovery-1993",
                    f"Senate, {region_names[region]} proportional seats",
                    members,
                    counts,
                    note,
                )
        return cs.write()
