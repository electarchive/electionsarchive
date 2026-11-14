import json
import re
import unicodedata
import zipfile
from collections import defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.names import display
from ea.sources.es.infoelectoral import APLIEXTR, PAGE, archive, processes, records
from ea.store import DATA, connect

SOURCE = "es.europeas"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Ministerio del Interior"
TITLE = "Resultados electorales: elecciones al Parlamento Europeo (ficheros de datos)"
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
ABROAD = 4
FIRST = "198706"
ELECTION_DAYS = {1987: date(1987, 6, 10)}
FIRST_NOTE = (
    "Spain's first election to the European Parliament, held on Wednesday 10 June 1987 together with municipal and "
    "regional elections, after its accession on 1 January 1986. The ministry's control file prints 13 June 1987; "
    "the election day here is the date of the vote."
)
PROVINCE_METHOD = (
    "List votes, registered voters, blank ballots, and null ballots by province from the Ministry of the Interior's "
    "data files, with the votes of residents abroad (CERA) counted in their province. Spain elects its members of the "
    "European Parliament in one national constituency by closed lists, so the national row is the contest result and "
    "equals the sum of the provinces. Valid votes here are the votes to candidacies; blank ballots, which the "
    "ministry counts as valid, are stored apart. Seats are the candidates the ministry flags as elected, counted "
    "for each list."
)
MESA_METHOD = (
    "List votes, registered voters, blank ballots, and null ballots by polling station (mesa) from the Ministry of "
    "the Interior's data files, with the votes of residents abroad (CERA) as one unit per province counted on the "
    "abroad channel. A mesa sits on its census section where the section code is in the 2025 INE section map, "
    "otherwise on its municipality. Only lists with votes at a mesa are stored."
)
MESA_EXACT = " The mesa figures add up to the official province totals exactly."
MESA_DIFFERS = (
    " The ministry flags the mesa figures {flag}: they add up to {mesa:,} list votes, against {official:,} in the "
    "official province totals, so the official province set is the default."
)
SOLE_SOURCE = " The mesa figures are the ministry's only breakdown below the province."
SEATS_NOTE = (
    " The ministry flags {flagged} candidates as elected where the contest record holds {magnitude} seats: the "
    "four seats that Spain gained under the Treaty of Lisbon went to the next candidates on the lists, and the file "
    "flags them with the rest."
)


SPARQL = "https://query.wikidata.org/sparql?format=json&query="
MEMBERS = quote(
    "SELECT ?item ?term ?start ?end ?district ?label ?alt ?ep ?gender ?birth WHERE {\n"
    "?item p:P39 ?s . ?s ps:P39 wd:Q27169 .\n"
    "OPTIONAL { ?s pq:P2937 ?term } OPTIONAL { ?s pq:P580 ?start } OPTIONAL { ?s pq:P582 ?end } "
    "OPTIONAL { ?s pq:P768 ?district }\n"
    "{ ?s pq:P768 wd:Q29 } UNION { ?item wdt:P27 wd:Q29 }\n"
    "OPTIONAL { ?item rdfs:label ?label FILTER(lang(?label) = 'es') }\n"
    "OPTIONAL { ?item skos:altLabel ?alt FILTER(lang(?alt) = 'es') }\n"
    "OPTIONAL { ?item wdt:P1186 ?ep } OPTIONAL { ?item wdt:P21 ?gender } OPTIONAL { ?item wdt:P569 ?birth } }"
)
EVERY_MEMBER = quote(
    "SELECT ?item ?start ?term ?label WHERE {\n"
    "?item p:P39 ?s . ?s ps:P39 wd:Q27169 .\n"
    "OPTIONAL { ?s pq:P580 ?start } OPTIONAL { ?s pq:P2937 ?term }\n"
    "?item rdfs:label|skos:altLabel ?label FILTER(lang(?label) IN ('es', 'ca', 'en', 'gl', 'eu')) }"
)
NAME_STOP = {"de", "del", "la", "las", "los", "y", "i", "e", "el"}
GENDERS = {"M": "male", "F": "female", "Q6581097": "male", "Q6581072": "female"}
CANDIDATE = (
    ("list", 16, 21),
    ("position", 22, 24),
    ("kind", 25, 25),
    ("name", 26, 100),
    ("sex", 101, 101),
    ("elected", 120, 120),
)

UNLISTED = (
    " The candidate file lists {candidates} candidates on lists that have no votes in the results files ({lists}); "
    "they are not stored."
)
BLANK_NAMES = " The candidate file holds {count} list positions with no name, which are not stored."


def normalize(text: str) -> str:
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return re.sub(r"[\W_]+", " ", text.casefold()).strip()


def section_of(field: str) -> str:
    if (found := re.fullmatch(r"(\d+)([A-Z]?)", field)) is None:
        raise ValueError(f"section {field!r}")
    return f"{int(found[1]):03d}{found[2]}"


def tokens(text: str) -> set[str]:
    return set(normalize(text).split()) - NAME_STOP


def members() -> dict[str, dict]:
    found: dict[str, dict] = defaultdict(lambda: {"names": set(), "years": set(), "gender": None})
    for query in (MEMBERS, EVERY_MEMBER):
        for row in json.loads(raw.cached(SOURCE, SPARQL + query).path.read_text())["results"]["bindings"]:
            item = found[row["item"]["value"].rsplit("/", 1)[1]]
            for field in ("label", "alt"):
                if field in row:
                    item["names"].add(row[field]["value"])
            if "start" in row and re.match(r"\d{4}", row["start"]["value"]):
                item["years"].add(int(row["start"]["value"][:4]))
            if "gender" in row:
                item["gender"] = GENDERS.get(row["gender"]["value"].rsplit("/", 1)[1])
    return found


def resolve(found: dict[str, dict], year: int, printed: str) -> str | None:
    words = tokens(printed)
    hits = [item for item, entry in found.items() if any(words >= tokens(text) > set() for text in entry["names"])]
    near = [item for item in hits if any(year - 1 <= start <= year + 5 for start in found[item]["years"])]
    ranked = sorted(
        (
            (max(len(tokens(text)) for text in found[item]["names"] if words >= tokens(text)), item)
            for item in near or hits
        ),
        reverse=True,
    )
    if ranked and (len(ranked) == 1 or ranked[1][0] < ranked[0][0]):
        return ranked[0][1]
    if hits:
        return None
    scores = sorted(
        (
            (max((len(words & tokens(text)) for text in entry["names"]), default=0), item)
            for item, entry in found.items()
            if any(year - 1 <= start <= year + 5 for start in entry["years"])
        ),
        reverse=True,
    )
    if scores and scores[0][0] >= 2 and (len(scores) == 1 or scores[1][0] < scores[0][0]):
        return scores[0][1]
    return None


def fetch() -> None:
    for query in (MEMBERS, EVERY_MEMBER):
        try:
            raw.cached(SOURCE, SPARQL + query)
        except LookupError:
            raw.fetch(SOURCE, SPARQL + query, suffix=".json")


def parse() -> None:
    lists, totals, unit_votes, elected, mesas, mesa_votes, days = [], [], [], [], [], [], {}
    candidates = []
    found = members()
    for process in processes("07"):
        year = int(process[:4])
        with zipfile.ZipFile(archive("07", process, "TOTA")) as bundle:
            text = bundle.read(f"0207{process[2:]}.DAT").decode("latin-1")
        days[year] = ELECTION_DAYS.get(year) or date(int(text[16:20]), int(text[14:16]), int(text[12:14]))
        for row in records("07", process, "TOTA", "03"):
            lists.append((year, row["list"], row["abbrev"], row["name"]))
        for row in records("07", process, "TOTA", "07"):
            if row["community"] != "99" and row["province"] == "99":
                continue
            unit = "ES" if row["province"] == "99" else row["province"]
            totals.append(
                (
                    year,
                    unit,
                    row["census"],
                    row["blank"],
                    row["invalid"],
                    row["list_votes"],
                    row["seats"],
                    row["official"] == "S",
                )
            )
        for row in records("07", process, "TOTA", "08"):
            if row["community"] != "99" and row["province"] == "99":
                continue
            if row["votes"]:
                unit_votes.append(
                    (year, "ES" if row["province"] == "99" else row["province"], row["list"], row["votes"])
                )
        seats: dict[int, int] = defaultdict(int)
        for row in records("07", process, "TOTA", "04", CANDIDATE):
            printed = " ".join(str(row["name"]).split())
            item = resolve(found, year, printed) if row["elected"] == "S" else None
            if row["elected"] == "S":
                seats[int(row["list"])] += 1
            candidates.append(
                (
                    year,
                    row["list"],
                    row["kind"],
                    row["position"],
                    printed,
                    GENDERS.get(str(row["sex"])) or (found[item]["gender"] if item else None),
                    row["elected"] == "S",
                    item,
                )
            )
        elected += [(year, number, count) for number, count in seats.items()]
        for row in records("07", process, "MESA", "09"):
            if row["province"] == "99":
                continue
            mesas.append(
                (
                    year,
                    row["province"],
                    row["municipality"],
                    row["municipal_district"],
                    row["section"],
                    row["mesa"],
                    row["census"],
                    row["blank"],
                    row["invalid"],
                    row["list_votes"],
                    row["official"] == "S",
                )
            )
        for row in records("07", process, "MESA", "10"):
            if row["province"] != "99" and row["votes"]:
                mesa_votes.append(
                    (
                        year,
                        row["province"],
                        row["municipality"],
                        row["municipal_district"],
                        row["section"],
                        row["mesa"],
                        row["list"],
                        row["votes"],
                    )
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    key = ["year", "province", "municipality", "district", "section", "mesa"]
    for name, rows, schema in (
        ("lists", lists, ["year", "list", "abbrev", "name"]),
        ("totals", totals, ["year", "unit", "registered", "blank", "invalid", "list_votes", "seats", "official"]),
        ("unit_votes", unit_votes, ["year", "unit", "list", "votes"]),
        ("elected", elected, ["year", "list", "seats"]),
        (
            "candidates",
            candidates,
            ["year", "list", "kind", "position", "name", "gender", "elected", "wikidata"],
        ),
        (
            "mesas",
            mesas,
            [*key, "registered", "blank", "invalid", "list_votes", "official"],
        ),
        ("mesa_votes", mesa_votes, [*key, "list", "votes"]),
    ):
        pl.DataFrame(rows, schema=schema, orient="row", infer_schema_length=None).write_parquet(
            PARSED / f"{name}.parquet"
        )
    pl.DataFrame({"year": list(days), "day": list(days.values())}).write_parquet(PARSED / "days.parquet")
    duplicates = pl.read_parquet(PARSED / "mesas.parquet").group_by(key).len().filter(pl.col("len") > 1).height
    if duplicates:
        raise ValueError(f"{duplicates} mesas appear twice")


def emit() -> Path:
    lists = pl.read_parquet(PARSED / "lists.parquet")
    totals = pl.read_parquet(PARSED / "totals.parquet")
    unit_votes = pl.read_parquet(PARSED / "unit_votes.parquet")
    elected = pl.read_parquet(PARSED / "elected.parquet")
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    mesas = pl.read_parquet(PARSED / "mesas.parquet")
    mesa_votes = pl.read_parquet(PARSED / "mesa_votes.parquet")
    days = dict(pl.read_parquet(PARSED / "days.parquet").iter_rows())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Spain's European Parliament elections of 1987 to 2024 by province and polling station (mesa) with "
                "every list, its candidates, and those declared elected, from the Ministry of the Interior's data files"
            ),
            polity="ES",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        spain = cs.polity("ES")
        for scheme in ("es.provincia", "es.seccion", "es.mesa"):
            if conn.execute("select 1 from scheme where code = %s", (scheme,)).fetchone() is None:
                raise ValueError("publish es.ine and es.referendums before the European Parliament elections")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PAGE,
            license="Reuse under Ley 37/2007 with the source cited",
            attribution="Origen de los datos: Ministerio del Interior.",
        )
        for process in processes("07"):
            for extent in ("TOTA", "MESA"):
                cs.artifact(raw.cached("es.infoelectoral", f"{APLIEXTR}07{process}_{extent}.zip"), source)

        sections = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'es.seccion'"
            ).fetchall()
        )
        municipalities = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'es.municipio'"
            ).fetchall()
        )
        nation = cs.unit("es.nation", "ES")
        province_units = {code: cs.unit("es.provincia", code) for code in sorted(set(totals["unit"]) - {"ES"})}
        parliament = cs.key("eu.body", "european-parliament")
        member_state = cs.area("eu.member_state", "ES")
        if {parliament, member_state} & cs.minted:
            raise ValueError("publish eu.members and the European Parliament body before es.europeas")

        known = dict(
            conn.execute(
                """select x.value, x.entity_id from external_id x join person p on p.id = x.entity_id
                   where x.scheme = 'wikidata' and x.value = any(%s)""",
                [[item for item in candidates["wikidata"].unique() if item]],
            ).fetchall()
        )
        persons: dict[str, UUID] = {}
        latest = dict(
            candidates.filter(pl.col("wikidata").is_not_null())
            .sort("year")
            .group_by("wikidata")
            .agg(pl.col("name").last())
            .iter_rows()
        )
        unit_rows: dict[UUID, dict[str, str | None]] = {}
        parties: dict[str, UUID] = {}
        value_frames: list[pl.DataFrame] = []

        for year, day in sorted(days.items()):
            stamp = f"{year}{day.month:02d}"
            year_lists = lists.filter(pl.col("year") == year)
            year_totals = totals.filter(pl.col("year") == year)
            year_votes = unit_votes.filter(pl.col("year") == year)
            national = year_totals.filter(pl.col("unit") == "ES").row(0, named=True)
            seats = dict(elected.filter(pl.col("year") == year).select("list", "seats").iter_rows())
            sum_of_provinces = int(year_votes.filter(pl.col("unit") != "ES")["votes"].sum())
            if sum_of_provinces != national["list_votes"] or sum(seats.values()) != national["seats"]:
                raise ValueError(f"{year}: provinces, seats, and the national row disagree")
            votes_by_list = dict(year_votes.filter(pl.col("unit") == "ES").select("list", "votes").iter_rows())
            old = conn.execute(
                """select c.id, c.election_id, c.magnitude from contest c join election e on e.id = c.election_id
                   join polity p on p.id = e.polity_id and p.iso_code = 'ES'
                   where e.parent_id is not null and extract(year from e.date_from) = %s and c.body_id = %s""",
                [year, parliament],
            ).fetchall()
            if year == int(FIRST[:4]):
                election = cs.key("es.election", f"{day}:european-parliament")
                cs.add(
                    "election",
                    id=election,
                    polity_id=spain,
                    kind="general",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{year} European Parliament election in Spain")
                name(election, f"Elecciones al Parlamento Europeo de {year}", "es", "native")
                cs.add("slug", path=f"es/{day}-european-parliament", entity_id=election)
                cs.add("note", entity_id=election, kind="context", body_md=FIRST_NOTE)
                contest = cs.key("es.contest", f"{day}:european-parliament")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=parliament,
                    district_area_id=member_state,
                    magnitude=national["seats"],
                    default_result_kind="counted",
                )
                name(contest, f"{year} European Parliament election in Spain")
                cs.add("slug", path=f"es/{day}-european-parliament/european-parliament", entity_id=contest)
                superseded = None
                magnitude = national["seats"]
            else:
                if len(old) != 1:
                    raise ValueError(f"{year}: found {len(old)} European Parliament contests")
                contest, election, magnitude = old[0]
                superseded = conn.execute(
                    """select id, k, stage, kind, source_id, as_of, completeness from result_set
                       where contest_id = %s and is_default and status = 'active'""",
                    [contest],
                ).fetchone()
                if (
                    superseded is not None
                    and conn.execute(
                        "select 1 from result_set where id = %s and changeset_id in (select id from changeset where pipeline = %s)",
                        [superseded[0], SOURCE],
                    ).fetchone()
                ):
                    superseded = conn.execute(
                        """select p.id, p.k, p.stage, p.kind, p.source_id, p.as_of, p.completeness
                           from result_set p join result_set s on s.supersedes_id = p.id where s.id = %s""",
                        [cs.key(f"{SOURCE}.result_set", f"{day}:province")],
                    ).fetchone()
                if superseded is None:
                    raise ValueError(f"{year}: no default set to supersede")

            earlier: dict[int, UUID | None] = {}
            if superseded is not None:
                held = {
                    votes: party
                    for votes, party in conn.execute(
                        """select v.value::bigint, co.party_id from result_value v
                           join contest_option co on co.k = v.option_k
                           where v.result_set_k = %s and v.measure = %s and co.party_id is not null""",
                        [superseded[1], VOTES],
                    ).fetchall()
                }
                for number, votes in votes_by_list.items():
                    earlier[number] = held.get(votes)
            options: dict[int, UUID] = {}
            for entry in year_lists.sort("list").iter_rows(named=True):
                number = entry["list"]
                if number not in votes_by_list:
                    continue
                label = " ".join(entry["name"].split())
                if (party := earlier.get(number)) is None:
                    key = normalize(label)
                    if key not in parties:
                        parties[key] = cs.key("es.europeas.party", key)
                        if parties[key] in cs.minted:
                            cs.add("party", id=parties[key], kind="party", polity_id=spain)
                            name(parties[key], label, "es")
                            if abbreviation := " ".join(entry["abbrev"].split()):
                                name(parties[key], abbreviation, "es", "abbreviation")
                    party = parties[key]
                option = options[number] = cs.key("es.europeas.option", f"{day}:{number}")
                cs.add("contest_option", id=option, contest_id=contest, kind="list", party_id=party, ballot_label=label)

            year_candidates = candidates.filter(pl.col("year") == year).sort("list", "kind", "position")
            blank_names = year_candidates.filter(pl.col("name") == "").height
            year_candidates = year_candidates.filter(pl.col("name") != "")
            unlisted = year_candidates.filter(~pl.col("list").is_in(list(options)))
            year_candidates = year_candidates.filter(pl.col("list").is_in(list(options)))
            elected_candidacies: list[tuple[int, UUID]] = []
            for entry in year_candidates.iter_rows(named=True):
                tag = f"{day}:{entry['list']}:{entry['kind']}{entry['position']}"
                item = entry["wikidata"] or (f"elected:{normalize(entry['name'])}" if entry["elected"] else None)
                if item in known:
                    person = known[item]
                elif (person := persons.get(item or tag)) is None:
                    person = persons[item or tag] = cs.key(f"{SOURCE}.person", item or tag)
                    cs.add("person", id=person, gender=entry["gender"])
                    name(person, display(latest.get(item, entry["name"]), "es"), "es")
                    if entry["wikidata"]:
                        cs.add("external_id", entity_id=person, scheme="wikidata", value=item)
                candidacy = cs.key(f"{SOURCE}.candidacy", tag)
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=options[entry["list"]],
                    role="list_member" if entry["kind"] == "T" else "substitute",
                    list_position=entry["position"],
                    ballot_name=entry["name"],
                    is_independent=False,
                )
                if entry["elected"]:
                    elected_candidacies.append((entry["list"], candidacy))

            def make_set(label: str, default: bool, stage: str, completeness: float, notes: list[str]) -> UUID:
                result_set = cs.key(f"{SOURCE}.result_set", f"{day}:{label}")
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage=stage,
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=default,
                    completeness=completeness,
                    supersedes_id=superseded[0] if default and superseded is not None else None,
                )
                for body_md in notes:
                    cs.add("note", entity_id=result_set, kind="methodology", body_md=body_md)
                for number, count in seats.items():
                    cs.add("outcome", result_set_id=result_set, option_id=options[number], kind="won", seats=count)
                return result_set

            if superseded is not None:
                cs.add(
                    "result_set",
                    id=superseded[0],
                    election_id=election,
                    contest_id=contest,
                    stage=superseded[2],
                    kind=superseded[3],
                    status="superseded",
                    source_id=superseded[4],
                    is_default=False,
                    as_of=superseded[5],
                    completeness=superseded[6],
                )
            note = PROVINCE_METHOD
            if magnitude != national["seats"]:
                note += SEATS_NOTE.format(flagged=national["seats"], magnitude=magnitude)
            if unlisted.height:
                note += UNLISTED.format(
                    candidates=unlisted.height,
                    lists="; ".join(
                        " ".join(label.split())
                        for label in year_lists.filter(pl.col("list").is_in(unlisted["list"].unique().to_list()))[
                            "name"
                        ]
                    ),
                )
            if blank_names:
                note += BLANK_NAMES.format(count=blank_names)
            province_set = make_set("province", True, "official", 1, [note])
            for number, candidacy in elected_candidacies:
                cs.add(
                    "outcome",
                    result_set_id=province_set,
                    option_id=options[number],
                    candidacy_id=candidacy,
                    kind="elected",
                )
            option_ids = pl.DataFrame(
                {"list": list(options), "option_id": [str(option) for option in options.values()]}
            )
            unit_ids = pl.DataFrame(
                {
                    "unit": ["ES", *province_units],
                    "unit_id": [str(nation), *[str(unit) for unit in province_units.values()]],
                }
            )
            value_frames.append(
                year_votes.join(option_ids, on="list")
                .join(unit_ids, on="unit")
                .select(
                    result_set_id=pl.lit(str(province_set)),
                    unit_id="unit_id",
                    option_id="option_id",
                    measure=pl.lit(VOTES, pl.Int16),
                    value=pl.col("votes").cast(pl.Int64),
                    channel=pl.lit(0, pl.Int16),
                )
            )
            account = year_totals.join(unit_ids, on="unit").with_columns(
                ballots=pl.col("list_votes") + pl.col("blank") + pl.col("invalid")
            )
            for measure, column in (
                (REGISTERED, "registered"),
                (BALLOTS, "ballots"),
                (VALID, "list_votes"),
                (INVALID, "invalid"),
                (BLANK, "blank"),
            ):
                value_frames.append(
                    account.select(
                        result_set_id=pl.lit(str(province_set)),
                        unit_id="unit_id",
                        option_id=pl.lit(None, pl.String),
                        measure=pl.lit(measure, pl.Int16),
                        value=pl.col(column).cast(pl.Int64),
                        channel=pl.lit(0, pl.Int16),
                    )
                )

            year_mesas = mesas.filter(pl.col("year") == year)
            year_mesa_votes = mesa_votes.filter(pl.col("year") == year)
            official = bool(year_mesas["official"].all())
            mesa_total = int(year_mesas["list_votes"].sum())
            note = MESA_METHOD
            if mesa_total == national["list_votes"]:
                note += MESA_EXACT
            else:
                note += MESA_DIFFERS.format(
                    flag="as official" if official else "as not official",
                    mesa=mesa_total,
                    official=national["list_votes"],
                )
            over = year_mesas.filter(
                pl.col("list_votes") + pl.col("blank") + pl.col("invalid") > pl.col("registered")
            ).height
            if over:
                note += f" In {over} units the ballots cast exceed the registered voters, as the file prints them."
            mesa_set = make_set(
                "mesa",
                False,
                "official" if official else "preliminary",
                min(mesa_total / national["list_votes"], 1),
                [note],
            )
            codes, unit_list = [], []
            for row in year_mesas.iter_rows(named=True):
                cera = row["municipality"] == "999"
                section = "0000" if cera else section_of(row["section"])
                code = f"{stamp}:{row['province']}{row['municipality']}{row['district']}{section}{row['mesa']}"
                unit = cs.unit("es.mesa", code)
                codes.append(code)
                unit_list.append(str(unit))
                if unit in cs.minted and unit not in unit_rows:
                    place = f"{row['province']}{row['municipality']}{row['district']}{section}"
                    if cera:
                        area, relation = cs.area("es.provincia", row["province"]), "within"
                    elif place in sections:
                        area, relation = sections[place], "within"
                    elif row["province"] + row["municipality"] in municipalities:
                        area, relation = municipalities[row["province"] + row["municipality"]], "within"
                    else:
                        area, relation = None, "unlocated"
                    unit_rows[unit] = {
                        "id": str(unit),
                        "polity_id": str(spain),
                        "scheme_code": "es.mesa",
                        "code": code,
                        "parent_id": str(province_units[row["province"]]),
                        "area_relation": relation,
                        "area_id": None if area is None else str(area),
                        "kind": "aggregate" if cera else "standard",
                    }
            keyed = year_mesas.with_columns(unit_id=pl.Series(unit_list, dtype=pl.String)).with_columns(
                channel=pl.when(pl.col("municipality") == "999").then(ABROAD).otherwise(0).cast(pl.Int16),
                ballots=pl.col("list_votes") + pl.col("blank") + pl.col("invalid"),
            )
            key = ["province", "municipality", "district", "section", "mesa"]
            value_frames.append(
                year_mesa_votes.join(keyed.select(*key, "unit_id", "channel"), on=key)
                .join(option_ids, on="list")
                .select(
                    result_set_id=pl.lit(str(mesa_set)),
                    unit_id="unit_id",
                    option_id="option_id",
                    measure=pl.lit(VOTES, pl.Int16),
                    value=pl.col("votes").cast(pl.Int64),
                    channel="channel",
                )
            )
            for measure, column in (
                (REGISTERED, "registered"),
                (BALLOTS, "ballots"),
                (VALID, "list_votes"),
                (INVALID, "invalid"),
                (BLANK, "blank"),
            ):
                value_frames.append(
                    keyed.select(
                        result_set_id=pl.lit(str(mesa_set)),
                        unit_id="unit_id",
                        option_id=pl.lit(None, pl.String),
                        measure=pl.lit(measure, pl.Int16),
                        value=pl.col(column).cast(pl.Int64),
                        channel="channel",
                    )
                )
            print(
                f"{year}: {len(options)} lists, {year_candidates.height} candidates, {len(elected_candidacies)} elected "
                f"({year_candidates.filter(pl.col('elected'))['wikidata'].drop_nulls().len()} on Wikidata), "
                f"{sum(1 for n in options if earlier.get(n))} on parties of the earlier set, seats {sum(seats.values())} (magnitude {magnitude}), mesa votes {mesa_total} of "
                f"{national['list_votes']}",
                flush=True,
            )

        cs.frame(
            "reporting_unit",
            pl.DataFrame(
                list(unit_rows.values()),
                schema_overrides={"parent_id": pl.String, "area_id": pl.String},
            ),
        )
        cs.frame(
            "result_value",
            pl.concat(value_frames).with_columns(count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
        )
        return cs.write()
