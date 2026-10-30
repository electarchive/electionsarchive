import csv
import html
import json
import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.gb import leh, london
from ea.store import DATA, connect

SOURCE = "gb.cauth"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
SERVICES = "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services"
ERAS = {
    "2017": (
        "Combined_Authorities_March_2017_GCB_in_England_2022",
        "LAD17_CAUTH17_EN_LU_db0ce93885064b5181fb325b7752de85",
        "17",
        date(2017, 5, 4),
        date(2019, 5, 2),
    ),
    "2018": (
        "Combined_Authorities_December_2018_GCB_EN_2022",
        "LAD18_CAUTH18_EN_LU_v2_0e5ff75a9ac6484ab9164d493e459c88",
        "18",
        date(2019, 5, 2),
        date(2021, 5, 6),
    ),
    "2021": (
        "Combined_Authorities_December_2021_EN_BGC_2022",
        "LAD22_CAUTH22_EN_LU",
        "21",
        date(2021, 5, 6),
        date(2024, 5, 2),
    ),
    "2024": ("CAUTH_MAY_2025_EN_BGC", "LAD24_CAUTH24_EN_LU", "25", date(2024, 5, 2), date(2025, 5, 1)),
    "2025": ("CAUTH_MAY_2025_EN_BGC", "LAD25_CAUTH25_EN_LU", "25", date(2025, 5, 1), None),
}
TITLES = {
    "Combined_Authorities_March_2017_GCB_in_England_2022": "Combined Authorities (March 2017) Boundaries EN BGC",
    "Combined_Authorities_December_2018_GCB_EN_2022": "Combined Authorities (December 2018) Boundaries EN BGC",
    "Combined_Authorities_December_2021_EN_BGC_2022": "Combined Authorities (December 2021) Boundaries EN BGC",
    "CAUTH_MAY_2025_EN_BGC": "Combined Authorities (May 2025) Boundaries EN BGC",
    "LAD17_CAUTH17_EN_LU_db0ce93885064b5181fb325b7752de85": "Local Authority District to Combined Authority "
    "(March 2017) Lookup in England",
    "LAD18_CAUTH18_EN_LU_v2_0e5ff75a9ac6484ab9164d493e459c88": "Local Authority District to Combined Authority "
    "(December 2018) Lookup in England",
    "LAD22_CAUTH22_EN_LU": "Local Authority District to Combined Authority (December 2022) Lookup in England",
    "LAD24_CAUTH24_EN_LU": "Local Authority District to Combined Authority (May 2024) Lookup in England",
    "LAD25_CAUTH25_EN_LU": "Local Authority District to Combined Authority (May 2025) Lookup in England",
}
PUBLISHER = "Office for National Statistics"
LICENSE = "Open Government Licence v3.0"
LICENSE_URL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
ATTRIBUTION = (
    "Source: Office for National Statistics licensed under the Open Government Licence v.3.0. "
    "Contains OS data © Crown copyright and database right"
)
AUTHORITIES = {
    "E47000001": ("Greater Manchester Combined Authority", "greater-manchester", "Mayor of Greater Manchester"),
    "E47000002": ("South Yorkshire Mayoral Combined Authority", "south-yorkshire", "Mayor of South Yorkshire"),
    "E47000003": ("West Yorkshire Combined Authority", "west-yorkshire", "Mayor of West Yorkshire"),
    "E47000004": (
        "Liverpool City Region Combined Authority",
        "liverpool-city-region",
        "Mayor of the Liverpool City Region",
    ),
    "E47000006": ("Tees Valley Combined Authority", "tees-valley", "Mayor of the Tees Valley"),
    "E47000007": ("West Midlands Combined Authority", "west-midlands", "Mayor of the West Midlands"),
    "E47000008": (
        "Cambridgeshire and Peterborough Combined Authority",
        "cambridgeshire-and-peterborough",
        "Mayor of Cambridgeshire and Peterborough",
    ),
    "E47000009": ("West of England Combined Authority", "west-of-england", "Mayor of the West of England"),
    "E47000011": ("North of Tyne Combined Authority", "north-of-tyne", "North of Tyne Mayor"),
    "E47000012": (
        "York and North Yorkshire Combined Authority",
        "york-and-north-yorkshire",
        "Mayor of York and North Yorkshire",
    ),
    "E47000013": ("East Midlands Combined County Authority", "east-midlands", "Mayor of the East Midlands"),
    "E47000014": ("North East Combined Authority", "north-east", "Mayor of the North East"),
    "E47000016": (
        "Hull and East Yorkshire Combined Authority",
        "hull-and-east-yorkshire",
        "Mayor of Hull and East Yorkshire",
    ),
    "E47000017": (
        "Greater Lincolnshire Combined County Authority",
        "greater-lincolnshire",
        "Mayor of Greater Lincolnshire",
    ),
}
FORMER = {
    "E47000002": [("Sheffield City Region Combined Authority", None, date(2021, 5, 6))],
}
OFFICE_FORMER = {"E47000002": [("Mayor of the Sheffield City Region", None, date(2021, 5, 6))]}

VOTES, REGISTERED, BALLOTS, VALID, INVALID, EXHAUSTED = 1, 2, 3, 4, 5, 21
MEASURES = {"REJECTED": INVALID, "ELECTORATE": REGISTERED, "BALLOTS": BALLOTS}
SV_UNTIL = date(2023, 5, 4)
RESULTS = Path(__file__).with_name("cauth_results.tsv")
with Path(__file__).with_name("cauth_sources.tsv").open() as handle:
    SOURCES = {row["url"]: (row["publisher"], row["title"]) for row in csv.DictReader(handle, delimiter="\t")}
CBP_FILE = (
    "https://web.archive.org/web/20240418001038/"
    "https://researchbriefings.files.parliament.uk/documents/CBP-9237/results-mayoral.xlsx"
)
CBP_PAGE = "https://commonslibrary.parliament.uk/research-briefings/cbp-9237/"
CBP_TITLE = "Combined authority mayoral elections 2021 (CBP-9237): full results by district"
COMMONS_DAY = "2021-05-06"
COMMONS_ONLY = ["E47000004"]
COMPLETE = {"2024-05-02": {"E47000001", "E47000007", "E47000012", "E47000013"}}
PLACES = {
    "Cambirdge City": "E07000008",
    "Bath & NE Somerset": "E06000022",
    "S Gloucestershire": "E06000025",
    "City of York": "E06000014",
}
PARTIES: dict[str, str | None] = {
    "The Conservative Party": "Conservative Party",
    "Local Conservatives": "Conservative Party",
    'English Democrats - "A Parliament for England!"': "English Democrats",
    'English Democrats - "Putting England First!"': "English Democrats",
    "Green Party Candidate": "Green Party",
    "The Green Party candidate": "Green Party",
    "Greens": "Green Party",
    "Labour and Co operative Party": "Labour Party",
    "Liberal Democrats Party": "Liberal Democrats",
    "Womens Equality Party": "Women's Equality Party",
    "Yorkshire": "Yorkshire Party",
    "The Yorkshire Party": "Yorkshire Party",
    "Reform ": "Reform UK",
    "Independent": None,
}
PARTY_KEYS = {"Social Democratic Party": ("gb.party", "social-democratic-party-1990")}
NEW_PARTIES = {"Get the Coppers off the Jury", "South Yorkshire Save Our NHS"}
PEOPLE = {
    "Andy Burnham": "Q269909",
    "Steve Rotheram": "Q480068",
    "Ben Houchen": "Q29788057",
    "James Palmer": "Q30122660",
    "Andy Street": "Q27662279",
    "Dan Jarvis": "Q727587",
    "Jamie Driscoll": "Q63456224",
    "Nik Johnson": "Q98968372",
    "Dan Norris": "Q5214105",
    "Tracy Brabin": "Q7831699",
    "Oliver Coppard": "Q112061649",
    "Claire Ward": "Q4990196",
    "Kim McGuinness": "Q66309716",
    "Richard Parker": "Q125779067",
    "David Skaith": "Q125763459",
    "Paul Bristow": "Q76364834",
    "Andrea Jenkyns": "Q19874642",
    "Luke Campbell": "Q957135",
    "Helen Godwin": "Q133809333",
    "Liam Byrne": "Q269697",
    "Siôn Simon": "Q7533492",
    "Ben Bradley": "Q30223599",
    "Stephen Williams": "Q266548",
}
ALIASES = {
    "James Philip Palmer": "James Palmer",
    "James Phillip Palmer": "James Palmer",
    "Andrew John Street": "Andy Street",
    "Daniel Owen Woolgar Jarvis": "Dan Jarvis",
    "Tracy Lynn Brabin": "Tracy Brabin",
    "Oliver James Coppard": "Oliver Coppard",
    "Claire Margaret Ward": "Claire Ward",
    "David Robert Skaith": "David Skaith",
    "Andrea Marie Jenkyns": "Andrea Jenkyns",
    "Siôn Llewelyn Simon": "Siôn Simon",
    "Benjamin David Bradley": "Ben Bradley",
    "Stephen Roy Williams": "Stephen Williams",
    "Laura Kathryn Evans": "Laura Evans",
    "Hannah Ruth Kitching": "Hannah Kitching",
    "Andrew Varah Cooper": "Andrew Cooper",
    "Peter Charles George Durnell": "Pete Durnell",
    "Ben Houchen": "Ben Houchen",
    "Arron Fraser Andrew Banks": "Arron Banks",
}


def fold_place(text: str) -> str:
    words = leh.fold(text).split()
    return " ".join(
        w for w in words if w not in {"district", "city", "borough", "council", "metropolitan", "of", "the"}
    )


def match(options: dict[str, UUID], text: str) -> str:
    words = leh.fold(text).split()
    found = [full for full in options if leh.fold(full) == " ".join(words)]
    found = found or [
        full for full in options if leh.fold(full).split()[-1] in words and leh.fold(full).split()[0] == words[0]
    ]
    found = found or [
        full for full in options if leh.fold(full).split()[-1] in words and leh.fold(full)[0] == words[0][0]
    ]
    found = found or [full for full in options if leh.fold(full).split()[-1] in words]
    if len(found) != 1:
        raise ValueError(f"{text!r} matches {found}")
    return found[0]


def pages(service: str) -> list[str]:
    count = json.loads(
        raw.fetch(
            SOURCE,
            f"{SERVICES}/{service}/FeatureServer/0/query?where=1%3D1&returnCountOnly=true&f=json",
            suffix=".json",
        ).path.read_text()
    )["count"]
    return [
        f"{SERVICES}/{service}/FeatureServer/0/query?where=1%3D1&outFields=*&outSR=27700&f=geojson"
        + (f"&orderByFields=FID&resultOffset={offset}&resultRecordCount=1000" if count > 1000 else "")
        for offset in range(0, count, 1000)
    ]


def features(service: str) -> list[dict[str, object]]:
    return [
        {key.upper(): value for key, value in feature["properties"].items()} | {"geometry": feature["geometry"]}
        for url in pages(service)
        for feature in json.loads(raw.cached(SOURCE, url).path.read_text())["features"]
    ]


def fetch() -> None:
    for url in [*SOURCES, CBP_FILE]:
        raw.cached(SOURCE, url)
    for service in TITLES:
        for url in pages(service):
            if "features" not in json.loads(raw.fetch(SOURCE, url, suffix=".geojson").path.read_text()):
                raise ValueError(f"{url} returned no features")


def parse() -> None:
    shapes: list[tuple[str, str, str, str]] = []
    members: list[tuple[str, str, str]] = []
    for era, (layer, lookup, year, _, _) in ERAS.items():
        lookup_year = lookup[3:5]
        found = {(str(row[f"LAD{lookup_year}CD"]), str(row[f"CAUTH{lookup_year}CD"])) for row in features(lookup)}
        members += [(era, lad, code) for lad, code in sorted(found)]
        codes = {code for _, code in found}
        drawn = {str(row[f"CAUTH{year}CD"]): row for row in features(layer)}
        if missing := codes - set(drawn):
            raise ValueError(f"{era}: no shapes for {sorted(missing)}")
        shapes += [
            (era, code, str(drawn[code][f"CAUTH{year}NM"]), json.dumps(drawn[code]["geometry"]))
            for code in sorted(codes)
        ]
    with connect() as conn:
        conn.execute("create temp table feature (era text, code text, name text, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy feature from stdin") as copy:
            for row in shapes:
                copy.write_row(row)
        rows = conn.execute(
            """select era, code, name,
                      encode(st_asewkb(st_multi(st_transform(geom, 4326))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
               from (select era, code, name, st_multi(st_collectionextract(st_makevalid(st_force2d(
                       st_setsrid(st_geomfromgeojson(geojson), 27700))), 3)) as geom from feature) shape"""
        ).fetchall()
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={"era": pl.String, "code": pl.String, "name": pl.String, "geom": pl.String, "label_point": pl.String},
        orient="row",
    ).write_parquet(PARSED / "authorities.parquet")
    pl.DataFrame(
        members, schema={"era": pl.String, "lad": pl.String, "authority": pl.String}, orient="row"
    ).write_parquet(PARSED / "members.parquet")


def emit() -> Path:
    authorities = pl.read_parquet(PARSED / "authorities.parquet")
    members = pl.read_parquet(PARSED / "members.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="England's combined authorities from the Office for National Statistics, 2017-2025, as areas and "
            "as polities for those with an elected mayor, with every mayoral election 2017-2025 from the returning "
            "officers' declarations, and results by council where declared",
            polity="GB",
        )
        gb = cs.polity("GB")
        england = cs.polity("GB-ENG")

        def name(
            entity: UUID,
            text: str,
            valid_from: date | None = None,
            valid_to: date | None = None,
            preferred: bool = True,
        ) -> None:
            cs.add(
                "name",
                entity_id=entity,
                text=text,
                lang="en",
                kind="official",
                is_preferred=preferred,
                valid_from=valid_from,
                valid_to=valid_to,
            )

        if conn.execute("select 1 from scheme where code = 'gb.cauth'").fetchone() is None:
            cs.add("scheme", code="gb.cauth", polity_id=gb, kind="administrative", rank=2, noun="combined authority")
        sources = {}
        for service, title in TITLES.items():
            sources[service] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[service],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=f"{SERVICES}/{service}/FeatureServer",
                license=LICENSE,
                license_url=LICENSE_URL,
                attribution=ATTRIBUTION,
            )
            for url in pages(service):
                cs.artifact(raw.cached(SOURCE, url), sources[service])
        spans: dict[str, list[tuple[date, date | None, str]]] = {}
        for era, (layer, _, _, start, end) in ERAS.items():
            for code, label, geom, point in (
                authorities.filter(pl.col("era") == era)
                .select("code", "name", "geom", "label_point")
                .sort("code")
                .rows()
            ):
                spans.setdefault(code, []).append((start, end, label))
                cs.add(
                    "area_geometry",
                    area_id=cs.area("gb.cauth", code),
                    valid_from=start,
                    valid_to=end,
                    geom=geom,
                    label_point=point,
                    quality="official",
                    source_id=sources[layer],
                )
            cs.add("boundary_set", scheme_code="gb.cauth", vintage=era, valid_from=start, valid_to=end, as_of=start)
        for code, eras in spans.items():
            area = cs.area("gb.cauth", code)
            if area not in cs.minted:
                continue
            cs.add("area", id=area, scheme_code="gb.cauth")
            cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=None)
            cs.add("area_parent", area_id=area, parent_id=cs.area("gb.country", "ENG"), valid_from=None, valid_to=None)
            runs: list[tuple[date, date | None, str]] = []
            for start, end, label in eras:
                if runs and runs[-1][2] == label and runs[-1][1] == start:
                    runs[-1] = (runs[-1][0], end, label)
                else:
                    runs.append((start, end, label))
            for start, end, label in runs:
                name(area, label, None if start == eras[0][0] else start, end, end is None or end == eras[-1][1])
        for code, (label, path, office_label) in AUTHORITIES.items():
            if code not in spans:
                raise ValueError(f"{code} is in no ONS layer")
            polity = cs.key("gb.polity", code)
            if polity not in cs.minted:
                continue
            area = cs.area("gb.cauth", code)
            cs.add("polity", id=polity, kind="region", iso_code=None)
            cs.add("polity_parent", polity_id=polity, parent_id=england)
            cs.add("polity_territory", polity_id=polity, area_id=area)
            name(polity, label)
            for former, valid_from, valid_to in FORMER.get(code, []):
                name(polity, former, valid_from, valid_to, False)
            cs.add("slug", path=f"gb/{path}", entity_id=polity)
            office = cs.key("gb.office", f"mayor:{code}")
            cs.add("office", id=office, polity_id=polity, body_id=None, kind="mayor")
            name(office, office_label)
            for former, valid_from, valid_to in OFFICE_FORMER.get(code, []):
                name(office, former, valid_from, valid_to, False)
        if members.filter(~pl.col("authority").is_in(list(spans))).height:
            raise ValueError("lookup authority without shapes")

        known: dict[str, UUID] = {}
        for text, id in conn.execute(
            "select n.text, p.id from party p join polity y on y.id = p.polity_id"
            " join name n on n.entity_id = p.id where y.iso_code = 'GB' order by p.id desc"
        ).fetchall():
            known[leh.fold(text)] = id
        parties: dict[str, UUID | None] = {}

        def party(label: str) -> UUID | None:
            label = " ".join(label.split())
            if label not in parties:
                resolved = PARTIES.get(label, london.RESOLVED.get(label, label))
                if resolved is None or resolved.startswith("Independent"):
                    parties[label] = None
                elif resolved in PARTY_KEYS:
                    parties[label] = cs.key(*PARTY_KEYS[resolved])
                elif resolved in NEW_PARTIES:
                    minted = parties[label] = cs.key("gb.party", slug(resolved))
                    if minted in cs.minted:
                        cs.add("party", id=minted, kind="party", polity_id=gb)
                        name(minted, resolved)
                elif resolved in leh.RESOLVED:
                    parties[label] = cs.key(*leh.RESOLVED[resolved])
                elif (found := known.get(leh.fold(resolved))) is not None:
                    parties[label] = found
                else:
                    raise ValueError(f"party label {label!r} ({resolved!r}) does not resolve")
            return parties[label]

        persons: dict[str, UUID] = {}

        def person(full: str) -> UUID:
            full = ALIASES.get(full, full)
            if full not in persons:
                qid = PEOPLE.get(full)
                persons[full] = cs.key("wikidata", qid) if qid is not None else cs.key("gb.cauth.person", full)
                if persons[full] in cs.minted:
                    cs.add("person", id=persons[full])
                    cs.add("name", entity_id=persons[full], text=full, lang="en", kind="official", is_preferred=True)
            return persons[full]

        printed_cache: dict[str, set[str]] = {}

        def printed(url: str) -> set[str]:
            if url not in printed_cache:
                path = raw.cached(SOURCE, url).path
                if path.suffix == ".pdf":
                    text = subprocess.run(
                        ["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True
                    ).stdout.decode(errors="replace")
                else:
                    text = html.unescape(re.sub(r"<[^>]+>", " ", path.read_text(errors="replace")))
                printed_cache[url] = set(re.findall(r"\d+", re.sub(r"(?<=\d)[,.](?=\d{3}\b)", "", text)))
            return printed_cache[url]

        lad_names: dict[str, dict[str, str]] = {}
        for era, (_, lookup, _, _, _) in ERAS.items():
            year = lookup[3:5]
            lad_names[era] = {
                fold_place(str(row[f"LAD{year}NM"])): str(row[f"LAD{year}CD"]) for row in features(lookup)
            }

        def lad_of(era: str, place: str) -> str:
            code = PLACES.get(place) or lad_names[era].get(fold_place(place))
            if code is None:
                raise ValueError(f"{era}: no council named {place!r}")
            return code

        units: dict[str, UUID] = {}

        def unit(scheme: str, code: str, polity: UUID) -> UUID:
            if code not in units:
                units[code] = cs.unit(scheme, code)
                if units[code] in cs.minted:
                    area = cs.area(scheme, code)
                    if area in cs.minted:
                        raise ValueError(f"{scheme} {code} is not a published area")
                    cs.add(
                        "reporting_unit",
                        id=units[code],
                        polity_id=polity,
                        scheme_code=scheme,
                        code=code,
                        area_relation="equals",
                        area_id=area,
                        kind="standard",
                        valid_from=None,
                    )
                    label = conn.execute(
                        "select n.text from name n where n.entity_id = %s and n.is_preferred"
                        " order by n.valid_from desc nulls last limit 1",
                        [area],
                    ).fetchone()
                    if label is not None:
                        name(units[code], label[0])
            return units[code]

        commons = cs.source("House of Commons Library", CBP_TITLE)
        cs.add(
            "source",
            id=commons,
            kind="official_body",
            publisher="House of Commons Library",
            title=CBP_TITLE,
            url=CBP_PAGE,
            license=LICENSE,
            license_url=LICENSE_URL,
        )
        cs.artifact(raw.cached(SOURCE, CBP_FILE), commons)
        book = fastexcel.read_excel(raw.cached(SOURCE, CBP_FILE).path)
        codes = {
            str(row[0]): str(row[1]) for row in book.load_sheet("Party_key", header_row=None).to_polars().rows()[2:]
        }
        commons_rows: dict[str, list[tuple[str, str, str, int, int | None, int, int]]] = {}
        for row in book.load_sheet("Results", header_row=None).to_polars().rows()[3:]:
            if row[0] is None:
                continue
            commons_rows.setdefault(str(row[0]), []).append(
                (
                    lad_of("2021", str(row[2]).strip()),
                    " ".join(str(row[3]).split()),
                    codes[str(row[4])],
                    int(row[6]),
                    None if row[7] is None else int(row[7]),
                    int(row[8]),
                    int(row[9]),
                )
            )

        table = pl.read_csv(RESULTS, separator="\t", infer_schema=False)
        contests = sorted({(day, code) for day, code in table.select("day", "authority").rows()})
        contests += [(COMMONS_DAY, code) for code in COMMONS_ONLY]
        fptp = cs.key("gb.electoral_system", "fptp")
        sv = cs.key("gb.electoral_system", "sv")
        if fptp in cs.minted or sv in cs.minted:
            raise ValueError("publish gb.parliament and gb.mayors first")
        sources: dict[str, UUID] = {}

        def cite(url: str) -> UUID:
            if url not in sources:
                publisher, title = SOURCES[url]
                sources[url] = cs.source(publisher, title)
                cs.add("source", id=sources[url], kind="official_body", publisher=publisher, title=title, url=url)
                cs.artifact(raw.cached(SOURCE, url), sources[url])
            return sources[url]

        for day_text, code in contests:
            day = date.fromisoformat(day_text)
            polity = cs.key("gb.polity", code)
            label, path, office_label = AUTHORITIES[code]
            office_label = next(
                (
                    former
                    for former, _, valid_to in OFFICE_FORMER.get(code, [])
                    if valid_to is not None and day < valid_to
                ),
                office_label,
            )
            supplementary = day < SV_UNTIL
            first_round = 1 if supplementary else 0
            rows = table.filter((pl.col("day") == day_text) & (pl.col("authority") == code))
            whole = rows.filter(pl.col("council") == "TOTAL")
            candidates: list[tuple[str, str, str, int, int | None]] = []
            figures: dict[int, tuple[int, str]] = {}
            shown = set().union(*(printed(url) for url in set(whole["source"])))
            for row in whole.iter_rows(named=True):
                value = int(row["round1"])
                if value_missing := str(value) not in printed(row["source"]):
                    raise ValueError(
                        f"{day} {code}: {row['printed']} {value} not printed in {row['source']} {value_missing}"
                    )
                if row["printed"] in MEASURES:
                    figures[MEASURES[row["printed"]]] = (value, row["source"])
                    continue
                final = None if not row["round2"] else int(row["round2"])
                if final is not None and str(final) not in shown:
                    raise ValueError(f"{day} {code}: {row['printed']} {final} not printed in its sources")
                candidates.append((row["name"], row["printed"], row["party"], value, final))
            local = commons_rows.get(code, []) if day_text == COMMONS_DAY else []
            source = cite(whole["source"][0]) if whole.height else commons
            if not candidates:
                labels = {full: label_text for _, full, label_text, *_ in local}
                firsts = {full: sum(row[3] for row in local if row[1] == full) for full in labels}
                seconds = {full: sum(row[4] or 0 for row in local if row[1] == full) for full in labels}
                transfers = {row[1] for row in local if row[4] is not None}
                candidates = [
                    (
                        full,
                        full,
                        labels[full],
                        firsts[full],
                        firsts[full] + seconds[full] if full in transfers else None,
                    )
                    for full in labels
                ]
            if (
                supplementary
                and not any(final is not None for *_, final in candidates)
                and max(first for *_, first, _ in candidates) * 2 <= sum(first for *_, first, _ in candidates)
            ):
                raise ValueError(f"{day} {code}: no first-round majority and no second round")
            national = conn.execute(
                "select entity_id from external_id where scheme = 'gb.election' and value = %s", [f"{day}:local:gb"]
            ).fetchone()
            election = cs.key("gb.election", f"{day}:mayor:{code}")
            cs.add(
                "election",
                id=election,
                polity_id=polity,
                parent_id=None if national is None else national[0],
                kind="general",
                date_from=day,
                date_to=day,
                status="held",
            )
            name(election, f"{day.year} {office_label} election")
            cs.add("slug", path=f"gb/{path}/{day}-mayoral", entity_id=election)
            contest_key = f"{day}:mayor:{code}"
            contest = cs.key("gb.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=cs.key("gb.office", f"mayor:{code}"),
                body_id=None,
                district_area_id=cs.area("gb.cauth", code),
                magnitude=1,
                round_number=1,
                electoral_system_id=sv if supplementary else fptp,
                default_result_kind="counted",
                term="full",
            )
            name(contest, f"{office_label}, {day.year}")
            cs.add("slug", path=f"gb/{path}/{day}-mayoral/mayor", entity_id=contest)
            result_set = cs.key("gb.cauth.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("citation", entity_id=result_set, source_id=source)
            for url in ({url for _, url in figures.values()} | set(whole["source"])) - set(whole["source"][:1]):
                cs.add("citation", entity_id=result_set, source_id=cite(url))
            whole_unit = unit("gb.cauth", code, polity)
            valid = sum(first for *_, first, _ in candidates)
            cells = [(VALID, first_round, valid, 0)] + [
                (measure, 0, value, 0) for measure, (value, _) in figures.items()
            ]
            if supplementary and any(final is not None for *_, final in candidates):
                cells.append((EXHAUSTED, 2, valid - sum(final or 0 for *_, final in candidates), 0))
            for measure, number, value, flags in cells:
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=whole_unit,
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=number,
                    flags=flags,
                    value=value,
                )
            ranked = sorted(candidates, key=lambda row: (row[4] or 0, row[3]), reverse=True)
            winner = ranked[0][0]
            options: dict[str, UUID] = {}
            for full, ballot, label_text, first, final in candidates:
                owner = party(label_text) if label_text else None
                option_key = f"{contest_key}:{slug(full)}"
                option = options[full] = cs.key("gb.cauth.option", option_key)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=owner,
                    ballot_label=full,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("gb.cauth.candidacy", option_key),
                    person_id=person(full),
                    option_id=option,
                    role="head",
                    ballot_name=ballot,
                    member_of_party_id=owner,
                    is_independent=owner is None,
                )
                rounds = {first_round: first}
                if supplementary and final is not None:
                    rounds[2] = final
                for number, votes in rounds.items():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=whole_unit,
                        option_id=option,
                        channel=0,
                        measure=VOTES,
                        count_round=number,
                        flags=0,
                        value=votes,
                    )
                if full == winner:
                    kinds = ("won", "elected")
                elif supplementary and any(other is not None for *_, other in candidates) and final is None:
                    kinds = ("eliminated",)
                else:
                    kinds = ("lost",)
                for kind in kinds:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind)

            grains: dict[str, dict[str, int]] = {}
            council_rows = rows.filter(pl.col("council") != "TOTAL")
            era = next(
                era for era, (_, _, _, start, end) in ERAS.items() if start <= day and (end is None or day < end)
            )
            if council_rows.height and code in COMPLETE.get(day_text, ()):
                for row in council_rows.iter_rows(named=True):
                    value = int(row["round1"])
                    if str(value) not in printed(row["source"]):
                        raise ValueError(f"{day} {code} {row['council']}: {row['printed']} {value} not printed")
                    entry = grains.setdefault(lad_of(era, row["council"]), {})
                    key = row["printed"] if row["printed"] in MEASURES else match(options, row["name"])
                    entry[key] = value
                grain_source = cite(council_rows["source"][0])
                for url in set(council_rows["source"]) - {council_rows["source"][0]}:
                    cite(url)
            elif local:
                for lad, full, _, first, second, district_valid, electorate in local:
                    entry = grains.setdefault(lad, {})
                    entry[match(options, full)] = first
                    if second is not None:
                        entry[f"{match(options, full)}\x1f2"] = first + second
                    entry["VALID"], entry["ELECTORATE"] = district_valid, electorate
                grain_source = commons
            if not grains:
                continue
            members_now = set(members.filter((pl.col("era") == era) & (pl.col("authority") == code))["lad"])
            if set(grains) != members_now:
                raise ValueError(f"{day} {code}: councils {sorted(grains)} against {sorted(members_now)}")
            for full, _, _, first, _ in candidates:
                if (summed := sum(entry.get(full, 0) for entry in grains.values())) != first:
                    raise ValueError(f"{day} {code}: {full} councils sum to {summed}, declared {first}")
            grain_set = cs.key("gb.cauth.result_set", f"{contest_key}:lad")
            cs.add(
                "result_set",
                id=grain_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=grain_source,
                is_default=False,
                completeness=1,
            )
            cs.add("citation", entity_id=grain_set, source_id=grain_source)
            for lad, entry in sorted(grains.items()):
                lad_unit = unit("gb.lad", lad, polity)
                district_valid = entry.get("VALID", sum(entry.get(full, 0) for full in options))
                cs.add(
                    "result_value",
                    result_set_id=grain_set,
                    unit_id=lad_unit,
                    option_id=None,
                    channel=0,
                    measure=VALID,
                    count_round=first_round,
                    flags=0 if "VALID" in entry else 1,
                    value=district_valid,
                )
                for key, measure in MEASURES.items():
                    if key in entry:
                        cs.add(
                            "result_value",
                            result_set_id=grain_set,
                            unit_id=lad_unit,
                            option_id=None,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            flags=0,
                            value=entry[key],
                        )
                for full, option in options.items():
                    for key, number in ((full, first_round), (f"{full}\x1f2", 2)):
                        if key in entry:
                            cs.add(
                                "result_value",
                                result_set_id=grain_set,
                                unit_id=lad_unit,
                                option_id=option,
                                channel=0,
                                measure=VOTES,
                                count_round=number,
                                flags=0,
                                value=entry[key],
                            )
        return cs.write()
