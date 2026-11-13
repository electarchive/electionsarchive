import csv
import io
import re
import zipfile
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.it import interno, regionali
from ea.sources.it.interno import BLANK, REGISTERED, VOTERS, VOTES, born, proper
from ea.sources.it.storico import EXISTING, NEW, cased
from ea.store import DATA, connect

SOURCE = "it.provinciali"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
ARCHIVE = "https://dait.interno.gov.it/documenti/opendata/provinciali"
DAYS = [
    date(2004, 6, 12),
    date(2005, 4, 3),
    date(2005, 5, 8),
    date(2006, 5, 28),
    date(2007, 5, 27),
    date(2008, 4, 13),
    date(2009, 6, 7),
    date(2010, 3, 28),
    date(2010, 5, 30),
    date(2011, 5, 15),
]
SCHEME = "it.provincia"
REGIONS = {**regionali.REGIONS, "SARDEGNA": "20"}
REGION_SLUGS = {code: info[1] for code, info in regionali.REGION_INFO.items()} | {"20": "sardegna"}
SARDINIA = ("IT-88", "Sardegna", "Sardinia")
SARDINIAN = {
    "CAGLIARI": ("092", "CA", "Cagliari"),
    "CARBONIA-IGLESIAS": ("107", "CI", "Carbonia-Iglesias"),
    "MEDIO CAMPIDANO": ("106", "VS", "Medio Campidano"),
    "NUORO": ("091", "NU", "Nuoro"),
    "OGLIASTRA": ("105", "OG", "Ogliastra"),
    "OLBIA-TEMPIO": ("104", "OT", "Olbia-Tempio"),
    "ORISTANO": ("095", "OR", "Oristano"),
    "SASSARI": ("090", "SS", "Sassari"),
}
ENGLISH = {
    "Firenze": "Florence",
    "Genova": "Genoa",
    "Mantova": "Mantua",
    "Milano": "Milan",
    "Napoli": "Naples",
    "Padova": "Padua",
    "Roma": "Rome",
    "Torino": "Turin",
    "Venezia": "Venice",
}
ARTICLED = {"L'Aquila": "dell'Aquila", "La Spezia": "della Spezia"}
ALIASES = {
    ("01", "CERRETO LANGHE"): "Cerretto Langhe",
    ("01", "MONASTEROLO DI S."): "Monasterolo di Savigliano",
    ("01", "MONBARCARO"): "Mombarcaro",
    ("01", "PEZZO VALLE UZZONE"): "Pezzolo Valle Uzzone",
    ("01", "SALE LANGHE"): "Sale delle Langhe",
    ("01", "SALE S. GIOVANNI"): "Sale San Giovanni",
    ("01", "SALICETTO"): "Saliceto",
    ("05", "RIVOLI"): "Rivoli Veronese",
    ("07", "CASTELVECCHIO DI ROCCA B."): "Castelvecchio di Rocca Barbena",
    ("12", "MONTE S. GIOVANNI IN SABINA"): "Monte San Giovanni in Sabina",
    ("12", "MONTE S.GIOVANNI CAMPANO"): "Monte San Giovanni Campano",
    ("13", "S. BENEDETTO DEI MARSI"): "San Benedetto dei Marsi",
    ("13", "S. MARTINO SULLA MARRUCINA"): "San Martino sulla Marrucina",
    ("16", "S. DONATO DI LECCE"): "San Donato di Lecce",
    ("16", "SAN MARZANO DI S.GIUSEPPE"): "San Marzano di San Giuseppe",
    ("16", "TORRE S. SUSANNA"): "Torre Santa Susanna",
    ("18", "S. MAURO MARCHESATO"): "San Mauro Marchesato",
    ("18", "S. NICOLA DELL'ALTO"): "San Nicola dell'Alto",
}
PART = re.compile(r"^PARTE DI (COMUNE DI )?| \(PARTE\)$| - .*$| [IVX]+\??$| (NORD|SUD)( (EST|OVEST))?$| (EST|OVEST)$")
AGGREGATED = (
    "Results are by municipality, from the Interior Ministry's open data file for this election; a municipality "
    "split between council colleges is summed."
)
SEATS = (
    "Seats are the councillors the ministry's college candidate file marks as elected, counted by list. The council "
    "has {size} seats besides the president, one per college; seats that went to losing candidates for president are "
    "not counted, so the lists add up to {won}."
)
NO_SEATS = "Seats are not shown: the ministry's college candidate file does not cover every college of this province."
FIRST = (
    "A candidate with more than half of the valid votes is elected; otherwise the two leading candidates go to a "
    "runoff two weeks later. Candidates' votes include those cast for them alone, without a list."
)
SECOND = "The runoff between the two leading candidates of the first round; the one with more votes is elected."


def url(day: date) -> str:
    return f"{ARCHIVE}/provinciali-{day:%Y%m%d}.zip"


def fetch() -> None:
    for day in DAYS:
        raw.fetch(SOURCE, url(day), suffix=".zip")


def provinces() -> dict[str, tuple[str, str, str]]:
    with raw.cached(interno.SOURCE, interno.ISTAT).path.open(encoding="latin-1", newline="") as file:
        rows = list(csv.reader(file, delimiter=";"))[1:]
    found: dict[str, tuple[str, str, str]] = {}
    for row in rows:
        found.setdefault(interno.key(row[11]), (row[2], row[14], row[11]))
    return found


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    named = provinces()
    resolve, _, _ = regionali.municipalities()
    frames: list[pl.DataFrame] = []
    seats: list[pl.DataFrame] = []
    for day in DAYS:
        archive = zipfile.ZipFile(raw.cached(SOURCE, url(day)).path)
        text = archive.read(f"provinciali-{day:%Y%m%d}.txt").decode("cp1252")
        frame = pl.read_csv(io.StringIO(text), separator=";", infer_schema_length=0).with_columns(
            pl.all().str.strip_chars().fill_null("")
        )
        member = next(m for m in archive.namelist() if m.endswith("_CandidCollegio.xlsx"))
        colleges = pl.read_excel(io.BytesIO(archive.read(member)), infer_schema_length=0).select(
            pl.col("PROVINCIA", "COLLEGIO", "LISTA", "ELETTO").str.strip_chars(),
            pl.col("VOTILISTA").cast(pl.Float64).cast(pl.Int64),
        )
        first = frame.filter(pl.col("TURNO") == "1")
        sizes = first.group_by("PROVINCIA").agg(size=pl.col("COLLEGIO").n_unique())
        covered = colleges.group_by("PROVINCIA").agg(covered=pl.col("COLLEGIO").n_unique())
        partial = sizes.join(covered, on="PROVINCIA").filter(pl.col("size") < pl.col("covered"))["PROVINCIA"]
        frame = frame.filter(~pl.col("PROVINCIA").is_in(partial.implode()))
        frames.append(frame.with_columns(day=pl.lit(day)))
        complete = sizes.join(covered, on="PROVINCIA").filter(pl.col("size") == pl.col("covered"))["PROVINCIA"]
        votes = (
            first.filter(pl.col("DESCRLISTA") != "")
            .group_by("PROVINCIA", "COLLEGIO", "DESCRLISTA")
            .agg(VOTILISTA=pl.col("VOTILISTA").cast(pl.Int64).sum())
        )
        elected = colleges.filter(pl.col("ELETTO") == "E", pl.col("PROVINCIA").is_in(complete.implode()))
        labels = votes.select("PROVINCIA", LISTA="DESCRLISTA").unique()
        named_ = elected.join(labels, on=["PROVINCIA", "LISTA"])
        unnamed = (
            elected.join(labels, on=["PROVINCIA", "LISTA"], how="anti")
            .join(votes, on=["PROVINCIA", "COLLEGIO", "VOTILISTA"])
            .join(colleges.select("PROVINCIA", DESCRLISTA="LISTA").unique(), on=["PROVINCIA", "DESCRLISTA"], how="anti")
            .filter(pl.len().over("PROVINCIA", "COLLEGIO", "LISTA", "VOTILISTA") == 1)
        )
        found = (
            named_.group_by("PROVINCIA")
            .len()
            .join(unnamed.group_by("PROVINCIA").len(), on="PROVINCIA", how="full", coalesce=True)
        )
        whole = (
            elected.group_by("PROVINCIA")
            .agg(elected=pl.len())
            .join(found, on="PROVINCIA", how="left")
            .filter(pl.col("elected") == pl.col("len").fill_null(0) + pl.col("len_right").fill_null(0))["PROVINCIA"]
        )
        named_ = named_.filter(pl.col("PROVINCIA").is_in(whole.implode()))
        unnamed = unnamed.filter(pl.col("PROVINCIA").is_in(whole.implode()))
        seats.append(
            pl.concat([named_.select("PROVINCIA", "LISTA"), unnamed.select("PROVINCIA", LISTA="DESCRLISTA")])
            .group_by("PROVINCIA", "LISTA")
            .agg(seats=pl.len())
            .with_columns(day=pl.lit(day))
        )
    rows = pl.concat(frames).with_columns(
        region=pl.col("REGIONE").replace_strict(REGIONS),
        turno=pl.col("TURNO").cast(pl.Int16),
        **{c: pl.col(c.upper()).cast(pl.Int64) for c in ("elettori", "votanti", "skbianche", "voticand")},
        votilista=pl.when(pl.col("VOTILISTA") == "").then(None).otherwise(pl.col("VOTILISTA")).cast(pl.Int64),
    )
    places = rows.select("day", "region", "PROVINCIA").unique()
    codes = []
    for day, region, label in places.iter_rows():
        code, sigla, native = SARDINIAN.get(label) or named[interno.key(label)]
        codes.append((day, label, code, sigla, native, region))
    places = pl.DataFrame(
        codes, schema=["day", "PROVINCIA", "province", "sigla", "native", "region"], orient="row"
    ).with_columns(pl.col("day").cast(pl.Date))
    clashes = places.filter(pl.len().over("day", "province") > 1)
    if clashes.height:
        raise ValueError(f"provinces named twice: {clashes.rows()}")
    labels = rows.select("day", "region", "COMUNE").unique()

    def locate(region: str, label: str, day: date) -> str | None:
        label = re.sub(r"\s+", " ", label)
        label = ALIASES.get((region, label), label)
        for text in (label, PART.sub("", label.upper()).strip()):
            try:
                return resolve(region, text, day)
            except ValueError:
                continue
        return None

    located = labels.with_columns(
        code=pl.struct("region", "COMUNE", "day").map_elements(
            lambda row: locate(row["region"], row["COMUNE"], row["day"]), return_dtype=pl.String
        )
    )
    missing = located.filter(pl.col("code").is_null())
    if missing.height:
        raise ValueError(f"unresolved municipalities: {missing.sort('day', 'region').rows()}")
    rows = rows.join(located, on=["day", "region", "COMUNE"]).join(
        places.select("day", "PROVINCIA", "province"), on=["day", "PROVINCIA"]
    )
    place = ["day", "province", "COLLEGIO", "code", "turno"]
    accounting = rows.select(*place, "elettori", "votanti", "skbianche").unique()
    if accounting.select(place).is_duplicated().any():
        raise ValueError("accounting differs within a college and municipality")
    candidates = rows.select(*place, "COGNOME", "NOME", "DATANASCITA", "voticand").unique()
    if candidates.select(*place, "COGNOME", "NOME", "DATANASCITA").is_duplicated().any():
        raise ValueError("candidate votes differ within a college and municipality")
    lists = rows.filter(pl.col("turno") == 1, pl.col("DESCRLISTA") != "")
    if lists.select(*place, "DESCRLISTA").is_duplicated().any():
        raise ValueError("a list appears twice in a college and municipality")
    size = rows.filter(pl.col("turno") == 1).group_by("day", "province").agg(size=pl.col("COLLEGIO").n_unique())
    accounting.group_by("day", "province", "code", "turno").agg(
        registered=pl.col("elettori").sum(), voters=pl.col("votanti").sum(), blank=pl.col("skbianche").sum()
    ).write_parquet(PARSED / "accounting.parquet")
    candidates.group_by("day", "province", "code", "turno", "COGNOME", "NOME", "DATANASCITA").agg(
        votes=pl.col("voticand").sum()
    ).with_columns(
        birth=pl.when(pl.col("DATANASCITA") != "").then(pl.col("DATANASCITA")).map_elements(born, return_dtype=pl.Date)
    ).rename({"COGNOME": "surname", "NOME": "forename"}).drop("DATANASCITA").write_parquet(
        PARSED / "candidates.parquet"
    )
    lists.group_by("day", "province", "code", "DESCRLISTA", "COGNOME").agg(votes=pl.col("votilista").sum()).rename(
        {"DESCRLISTA": "list", "COGNOME": "leader"}
    ).write_parquet(PARSED / "lists.parquet")
    pl.concat(seats).join(places.select("day", "PROVINCIA", "province"), on=["day", "PROVINCIA"]).rename(
        {"LISTA": "list"}
    ).drop("PROVINCIA").write_parquet(PARSED / "seats.parquet")
    places.join(size, on=["day", "province"]).drop("PROVINCIA").write_parquet(PARSED / "places.parquet")


def emit() -> Path:
    accounting = pl.read_parquet(PARSED / "accounting.parquet")
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    lists = pl.read_parquet(PARSED / "lists.parquet")
    seats = pl.read_parquet(PARSED / "seats.parquet")
    places = pl.read_parquet(PARSED / "places.parquet").sort("province", "day")
    unknown = seats.join(lists.select("day", "province", "list").unique(), on=["day", "province", "list"], how="anti")
    if unknown.height:
        raise ValueError(f"seats for lists without votes: {unknown.rows()}")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Italian provincial council and presidential elections 2004–2011 from the Interior Ministry's open data, by municipality",
            polity="IT",
        )
        cs.manifest["scope"] = {"polity": "IT"}

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        ministry = cs.source("Ministero dell'Interno", "Eligendo: archivio storico delle elezioni, open data")
        italy = cs.polity("IT")
        if ministry in cs.minted or italy in cs.minted:
            raise ValueError("publish it.interno before it.provinciali")
        for day in DAYS:
            cs.artifact(raw.cached(SOURCE, url(day)), ministry)
        if conn.execute("select 1 from scheme where code = %s", [SCHEME]).fetchone() is None:
            cs.add("scheme", code=SCHEME, polity_id=italy, kind="administrative", rank=2, noun="province")

        regions: dict[str, UUID] = {}

        def region_of(code: str) -> UUID:
            if code not in regions:
                iso = regionali.REGION_INFO[code][0] if code in regionali.REGION_INFO else SARDINIA[0]
                regions[code] = cs.polity(iso)
                if regions[code] in cs.minted:
                    if code != "20":
                        raise ValueError(f"publish it.regionali before it.provinciali ({iso})")
                    _, native, english = SARDINIA
                    cs.add("polity", id=regions[code], kind="region", iso_code=iso)
                    cs.add("slug", path="it/sardegna", entity_id=regions[code])
                    cs.add("polity_parent", polity_id=regions[code], parent_id=italy)
                    cs.add("polity_territory", polity_id=regions[code], area_id=cs.area("it.regione", code))
                    name(regions[code], native, "it")
                    name(regions[code], english, "en", "english")
            return regions[code]

        parties: dict[tuple[str, str], UUID] = {}

        def party_of(label: str) -> UUID:
            spec = EXISTING.get(label)
            if spec is not None:
                if spec not in parties:
                    parties[spec] = cs.key(*spec)
                    if parties[spec] in cs.minted:
                        raise ValueError(f"{spec} is not in the database")
                return parties[spec]
            full, abbreviation, kind = NEW.get(label) or (label, None, "list_vehicle")
            known = label in NEW
            key = ("it.party", slug(full)) if known else ("it.storico.list", slug(label))
            if key not in parties:
                parties[key] = cs.key(*key)
                if parties[key] in cs.minted:
                    cs.add("party", id=parties[key], kind=kind, polity_id=italy)
                    if abbreviation is not None:
                        name(parties[key], abbreviation, "it", "abbreviation")
                    name(parties[key], full if known else cased(label), "it")
            return parties[key]

        births = sorted(set(candidates["birth"].drop_nulls().to_list()))
        existing: dict[tuple[str, date], set[UUID]] = {}
        for id, birth, text in conn.execute(
            """select p.id, p.birth_date, n.text from person p join name n on n.entity_id = p.id
               join changeset c on c.id = p.changeset_id
               where p.birth_date = any(%s) and c.pipeline <> %s""",
            [births, SOURCE],
        ).fetchall():
            existing.setdefault((interno.key(text), birth), set()).add(id)
        persons: dict[str, UUID] = {}

        def person_of(surname: str, forename: str, birth: date | None, where: str) -> UUID:
            key = f"{surname}|{forename}|{birth or where}"
            if key in persons:
                return persons[key]
            given, family = proper(forename), proper(surname)
            matches = existing.get((interno.key(given + family), birth), set()) | existing.get(
                (interno.key(family + given), birth), set()
            )
            if birth is not None and len(matches) == 1:
                persons[key] = matches.pop()
                return persons[key]
            persons[key] = cs.key(f"{SOURCE}.person", key)
            if persons[key] in cs.minted:
                if birth is None:
                    cs.add("person", id=persons[key])
                else:
                    cs.add("person", id=persons[key], birth_date=birth, birth_precision="day")
                name(persons[key], f"{given} {family}", "it")
            return persons[key]

        units: dict[str, str] = {}
        for code in sorted({*accounting["code"], *candidates["code"], *lists["code"]}):
            unit = cs.unit("it.comune", code)
            if unit in cs.minted:
                raise ValueError(f"comune {code} has no reporting unit; publish it.regionali first")
            units[code] = str(unit)

        def result_set(key: str, election: UUID, contest: UUID | None) -> UUID:
            id = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=id,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=ministry,
                is_default=True,
                completeness=1,
            )
            return id

        links: list[tuple[date, str, int, str, str, str | None]] = []
        held: dict[str, tuple[UUID, UUID, UUID, UUID, str, str, str]] = {}
        for day, code, sigla, native, region, size in places.iter_rows():
            if code not in held:
                polity = cs.polity(f"IT-{sigla}")
                area = cs.area(SCHEME, code)
                english = ENGLISH.get(native, native)
                article = ARTICLED.get(native, f"di {native}")
                path = f"it/{REGION_SLUGS[region]}/{slug(f'Provincia {article}')}"
                if area in cs.minted:
                    cs.add("area", id=area, scheme_code=SCHEME)
                    cs.add("area_code", area_id=area, code=code, valid_from=None)
                    cs.add("area_parent", area_id=area, parent_id=cs.area("it.regione", region), valid_from=None)
                    name(area, native, "it")
                if polity in cs.minted:
                    cs.add("polity", id=polity, kind="county", iso_code=f"IT-{sigla}")
                    cs.add("slug", path=path, entity_id=polity)
                    cs.add("polity_parent", polity_id=polity, parent_id=region_of(region))
                    cs.add("polity_territory", polity_id=polity, area_id=area)
                    name(polity, f"Provincia {article}", "it")
                    name(polity, f"Province of {english}", "en", "english")
                body = cs.key(f"{SOURCE}.body", code)
                if body in cs.minted:
                    cs.add("body", id=body, polity_id=polity, kind="unicameral")
                    name(body, f"Provincial Council of {english}")
                    name(body, f"Consiglio provinciale {article}", "it", "native")
                office = cs.key(f"{SOURCE}.office", code)
                if office in cs.minted:
                    cs.add("office", id=office, polity_id=polity, kind="head_of_government")
                    name(office, f"President of the Province of {english}")
                    name(office, f"Presidente della Provincia {article}", "it", "native")
                held[code] = (polity, area, body, office, english, article, path)
            polity, area, body, office, english, article, path = held[code]
            where = f"{code}:{day}"
            running = candidates.filter(pl.col("day") == day, pl.col("province") == code)
            first = running.filter(pl.col("turno") == 1)
            second = running.filter(pl.col("turno") == 2)
            election = cs.key(f"{SOURCE}.election", where)
            cs.add("election", id=election, polity_id=polity, kind="general", date_from=day, date_to=day, status="held")
            name(election, f"{day.year} {english} provincial election")
            name(election, f"Elezioni provinciali {article} del {day.year}", "it", "native")
            cs.add("slug", path=f"{path}/{day}-general", entity_id=election)
            links.append((day, code, 1, "election", str(result_set(f"{where}:election", election, None)), None))
            contest = cs.key(f"{SOURCE}.contest", f"{where}:council")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="list_allocation",
                body_id=body,
                district_area_id=area,
                magnitude=size,
                default_result_kind="counted",
            )
            name(contest, f"{day.year} {english} provincial election, council lists")
            cs.add("slug", path=f"{path}/{day}-general/consiglio", entity_id=contest)
            won = dict(
                seats.filter(pl.col("day") == day, pl.col("province") == code).select("list", "seats").iter_rows()
            )
            total = sum(won.values())
            if total > size:
                raise ValueError(f"{where}: {total} seats for a council of {size}")
            cs.add(
                "note",
                entity_id=contest,
                kind="context",
                body_md=" ".join([AGGREGATED, SEATS.format(size=size, won=total) if won else NO_SEATS]),
            )
            list_set = result_set(f"{where}:council", election, contest)
            counted = (
                lists.filter(pl.col("day") == day, pl.col("province") == code)
                .group_by("list")
                .agg(pl.col("votes").sum())
                .sort("votes", "list", descending=[True, False])
            )
            for label, _ in counted.iter_rows():
                option = cs.key(f"{SOURCE}.option", f"{where}:list:{label}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="list",
                    party_id=party_of(label),
                    ballot_label=label,
                )
                links.append((day, code, 1, f"list:{label}", str(list_set), str(option)))
                if won.get(label):
                    cs.add("outcome", result_set_id=list_set, option_id=option, kind="won", seats=won[label])
            previous: UUID | None = None
            for round, standing, ballot_day, parent in (
                (1, first, day, None),
                (2, second, day + timedelta(14), election),
            ):
                if standing.height == 0:
                    continue
                race_election = election
                if parent is not None:
                    race_election = cs.key(f"{SOURCE}.election", f"{where}:runoff")
                    cs.add(
                        "election",
                        id=race_election,
                        polity_id=polity,
                        parent_id=parent,
                        kind="runoff",
                        date_from=ballot_day,
                        date_to=ballot_day,
                        status="held",
                    )
                    name(race_election, f"{day.year} {english} provincial election, runoff")
                    name(race_election, f"Elezioni provinciali {article} del {day.year}, ballottaggio", "it", "native")
                    cs.add("slug", path=f"{path}/{ballot_day}-runoff", entity_id=race_election)
                    links.append(
                        (day, code, 2, "election", str(result_set(f"{where}:runoff", race_election, None)), None)
                    )
                totals = (
                    standing.group_by("surname", "forename", "birth")
                    .agg(pl.col("votes").sum())
                    .sort("votes", "surname", descending=[True, False])
                )
                valid = int(totals["votes"].sum())
                race = cs.key(f"{SOURCE}.contest", f"{where}:president:{round}")
                runoff = second.height > 0
                suffix = f", {'second' if round == 2 else 'first'} round" if runoff else ""
                cs.add(
                    "contest",
                    id=race,
                    election_id=race_election,
                    kind="office",
                    office_id=office,
                    district_area_id=area,
                    magnitude=1,
                    default_result_kind="counted",
                    round_number=round,
                    previous_round_id=previous,
                )
                name(race, f"{day.year} {english} provincial election, President{suffix}")
                cs.add(
                    "slug",
                    path=f"{path}/{ballot_day}-{'general' if round == 1 else 'runoff'}/presidente",
                    entity_id=race,
                )
                cs.add(
                    "note",
                    entity_id=race,
                    kind="context",
                    body_md=" ".join([AGGREGATED, FIRST if round == 1 else SECOND]),
                )
                race_set = result_set(f"{where}:president:{round}", race_election, race)
                top = int(totals["votes"][0])
                if round == 1 and (top * 2 > valid) == runoff:
                    raise ValueError(f"{where}: runoff {runoff} with a leader at {top} of {valid}")
                for rank, (surname, forename, birth, votes) in enumerate(totals.iter_rows()):
                    label = f"{surname} {forename}"
                    option = cs.key(f"{SOURCE}.option", f"{where}:president:{round}:{label}:{birth}")
                    cs.add("contest_option", id=option, contest_id=race, kind="candidate", ballot_label=label)
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", f"{where}:president:{round}:{label}:{birth}"),
                        person_id=person_of(surname, forename, birth, where),
                        option_id=option,
                        role="head",
                        ballot_name=label,
                    )
                    links.append(
                        (day, code, round, f"president:{surname}|{forename}|{birth}", str(race_set), str(option))
                    )
                    if rank == 0 and votes == top and (round == 2 or not runoff):
                        cs.add("outcome", result_set_id=race_set, option_id=option, kind="elected")
                    elif round == 1 and runoff and rank < 2:
                        cs.add("outcome", result_set_id=race_set, option_id=option, kind="advanced")
                if round == 2 and int(totals["votes"][1]) == top:
                    raise ValueError(f"{where}: tied runoff")
                previous = race
        keys = pl.DataFrame(
            links,
            schema={
                "day": pl.Date,
                "province": pl.String,
                "turno": pl.Int16,
                "label": pl.String,
                "result_set_id": pl.String,
                "option_id": pl.String,
            },
            orient="row",
        )
        measures = {"registered": REGISTERED, "voters": VOTERS, "blank": BLANK}
        values = pl.concat(
            [
                accounting.unpivot(list(measures), index=["day", "province", "code", "turno"], variable_name="measure")
                .with_columns(label=pl.lit("election"), measure=pl.col("measure").replace_strict(measures))
                .with_columns(pl.col("measure").cast(pl.Int64)),
                lists.group_by("day", "province", "code", "list")
                .agg(pl.col("votes").sum())
                .select(
                    "day",
                    "province",
                    "code",
                    turno=pl.lit(1, pl.Int16),
                    label=pl.lit("list:") + pl.col("list"),
                    measure=pl.lit(VOTES, pl.Int64),
                    value="votes",
                ),
                candidates.select(
                    "day",
                    "province",
                    "code",
                    "turno",
                    label=pl.concat_str(
                        pl.lit("president:"),
                        "surname",
                        pl.lit("|"),
                        "forename",
                        pl.lit("|"),
                        pl.col("birth").cast(pl.String).fill_null("None"),
                    ),
                    measure=pl.lit(VOTES, pl.Int64),
                    value="votes",
                ),
            ],
            how="diagonal_relaxed",
        ).join(keys, on=["day", "province", "turno", "label"], how="left")
        if values["result_set_id"].null_count():
            raise ValueError(f"values without a result set: {values.filter(pl.col('result_set_id').is_null()).head()}")
        cs.frame(
            "result_value",
            values.select(
                "result_set_id",
                pl.col("code").replace_strict(units).alias("unit_id"),
                "option_id",
                channel=pl.lit(0, pl.Int16),
                measure=pl.col("measure").cast(pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                value=pl.col("value").cast(pl.Int64),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        return cs.write()
