import csv
import io
import unicodedata
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.br.tse import UFS, proper
from ea.sources.br.tse_history import party_key
from ea.store import DATA, connect

SOURCE = "br.tse_early"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
TSE = "https://cdn.tse.jus.br/estatistica/sead/odsele/"
DAYS = {
    1945: (date(1945, 12, 2),),
    1947: (date(1947, 1, 19),),
    1950: (date(1950, 10, 3),),
    1954: (date(1954, 10, 3),),
    1955: (date(1955, 10, 3),),
    1958: (date(1958, 10, 3),),
    1960: (date(1960, 10, 3),),
    1962: (date(1962, 10, 7),),
    1966: (date(1966, 11, 15),),
    1970: (date(1970, 11, 15),),
    1974: (date(1974, 11, 15),),
    1978: (date(1978, 11, 15),),
    1982: (date(1982, 11, 15),),
    1986: (date(1986, 11, 15),),
    1989: (date(1989, 11, 15), date(1989, 12, 17)),
    1990: (date(1990, 10, 3),),
}
FOLDERS = {
    "candidates": "votacao_candidato_uf",
    "parties": "votacao_partido_uf",
    "detail": "detalhe_votacao_uf",
    "seats": "consulta_vagas",
}
FILES = {
    (year, kind): f"{TSE}{folder}/{folder}_{year}.zip"
    for year in DAYS
    for kind, folder in FOLDERS.items()
    if year != 1989 or kind in ("candidates", "detail")
}
COLUMNS = {
    "candidates": [
        "generated",
        "hour",
        "year",
        "round",
        "election",
        "uf",
        "ue",
        "cargo",
        "number",
        "sequence",
        "name",
        "ballot",
        "office",
        "superior_code",
        "superior",
        "registration_code",
        "registration",
        "status_code",
        "status",
        "party_number",
        "party",
        "party_label",
        "legend_sequence",
        "coalition_label",
        "composition",
        "votes",
    ],
    "parties": [
        "generated",
        "hour",
        "year",
        "round",
        "election",
        "uf",
        "ue",
        "cargo",
        "office",
        "kind",
        "coalition_label",
        "composition",
        "party",
        "party_number",
        "party_label",
        "nominal",
        "legend",
    ],
    "detail": [
        "generated",
        "hour",
        "year",
        "round",
        "election",
        "uf",
        "ue",
        "cargo",
        "office",
        "registered",
        "voters",
        "abstentions",
        "nominal",
        "blank",
        "null",
        "legend",
    ],
    "seats": ["generated", "hour", "year", "election", "uf", "ue", "ue_label", "cargo", "office", "seats"],
}
OFFICES = {"1": "president", "2": "vice-president", "5": "senate", "6": "chamber"}
SPELLED = {"Fernando de Noronha": "FN", "Iguaçu": "IG", "Ponta Porã": "PP"}
LABELS = {
    "FN": "Fernando de Noronha",
    "IG": "Iguaçu",
    "PP": "Ponta Porã",
    "GP": "Guaporé",
    "RB": "Rio Branco",
    "GB": "Guanabara",
    "ZZ": "Voters abroad",
}
TERRITORIES_1945 = {
    "DF": ("Federal District (1891–1960)", "Distrito Federal"),
    "FN": ("Territory of Fernando de Noronha", "Território de Fernando de Noronha"),
    "IG": ("Territory of Iguaçu", "Território do Iguaçu"),
    "PP": ("Territory of Ponta Porã", "Território de Ponta Porã"),
}
SUBLEGENDA = {1966, 1978, 1982, 1986}
CONTINUING = {"PDS": "11", "PDT": "12", "PT": "13", "PTB": "14", "PMDB": "15", "PRN": "36"}
SOURCE_NOTE = (
    "The electoral court's Archive Section keyed the results of 1945 to 1990 from the official canvass records into "
    "its open-data repository: each candidate's and each party's votes by state or territory, with registered "
    "voters, turnout, blank, and null votes. The files are corrected when errors are found."
)
PRESIDENT = (
    "The president and, from 1950, the vice president were elected separately, each by a plurality of the votes "
    "cast nationwide, with no runoff. Blank votes counted as valid."
)
SENATE = (
    "Each state elects one or two senators, alternately, for eight-year terms. Voters cast as many votes as there are "
    "seats, and the candidates with the most votes win."
)
SUBLEGENDAS = (
    "Each state elects one or two senators. A party could run up to three candidates in sublegendas, and their votes "
    "counted for the party: the party with the most votes takes the seats, which go to its most-voted candidates. "
    "In a few contests the declared winners differ from this reading of the votes; the winners are stored as the "
    "electoral court declared them."
)
QUOTIENT = (
    "Each state elects its deputies at large from open lists. Voters choose a candidate or a party (a legend vote). "
    "Parties may join a coalition, which runs as one list. The electoral quotient is the valid votes divided by the "
    "seats, and only lists that reach it take seats: one per full quotient, then the remaining seats one at a time "
    "to the list with the highest average, its votes divided by its seats plus one. Each list's seats go to its "
    "candidates in order of personal votes. Blank votes counted as valid for the quotient, and so did null votes "
    "until the Electoral Code of 1965."
)
UNNAMED = "Unnamed candidate"
NO_NAME = "The electoral court's file gives no name for {count} candidates; their votes are kept."
REPEATED = (
    "The electoral court's file lists {name} more than once in {where}, with {votes} votes; they are added together."
)
SEATS_NOTE = "The electoral court's file lists {elected} elected for {seats} seats."
SHORT = "Candidate votes add up to {votes:,}, short of the {nominal:,} valid nominal votes in the state totals."


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".zip")


def read(year: int, kind: str) -> pl.DataFrame:
    rows: list[list[str]] = []
    with zipfile.ZipFile(raw.cached(SOURCE, FILES[(year, kind)]).path) as archive:
        for member in archive.namelist():
            if member.lower().endswith(".txt"):
                text = archive.read(member).decode("latin-1")
                rows.extend(row for row in csv.reader(io.StringIO(text), delimiter=";") if row)
    columns = COLUMNS[kind]
    return pl.DataFrame(
        [(row + [""] * len(columns))[: len(columns)] for row in rows], schema=columns, orient="row"
    ).with_columns(
        year=pl.lit(year),
        uf=pl.col("uf").replace(SPELLED),
        cargo=pl.col("cargo"),
    )


def count(column: str) -> pl.Expr:
    return pl.col(column).cast(pl.Int64, strict=False)


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    composition = pl.col("composition").str.replace_all(r"\s*/\s*", "/").str.strip_chars()
    pool = (
        pl.when(composition.str.starts_with("#") | (composition == ""))
        .then(pl.when(~pl.col("party").str.starts_with("#")).then(pl.col("party")))
        .otherwise(composition)
    )
    frames: dict[str, list[pl.DataFrame]] = {kind: [] for kind in FOLDERS}
    for year in DAYS:
        frames["candidates"].append(
            read(year, "candidates")
            .filter(pl.col("cargo").is_in(list(OFFICES)))
            .select(
                "year",
                "round",
                "cargo",
                "uf",
                name=pl.col("name").str.strip_chars(),
                ballot=pl.when(~pl.col("ballot").str.starts_with("#")).then(pl.col("ballot").str.strip_chars()),
                party=pl.when(~pl.col("party").str.starts_with("#")).then(pl.col("party").str.strip_chars()),
                party_number=pl.when(~pl.col("party_number").str.starts_with("-")).then(pl.col("party_number")),
                party_label=pl.when(~pl.col("party_label").str.starts_with("#")).then(pl.col("party_label")),
                pool=pool,
                coalition_label=pl.when(~pl.col("coalition_label").str.starts_with("#")).then(
                    pl.col("coalition_label")
                ),
                status="status",
                votes=count("votes"),
            )
        )
        if (year, "parties") in FILES:
            frames["parties"].append(
                read(year, "parties")
                .filter(pl.col("cargo") == "6")
                .select(
                    "year",
                    "uf",
                    party=pl.when(~pl.col("party").str.starts_with("#")).then(pl.col("party").str.strip_chars()),
                    party_number=pl.when(~pl.col("party_number").str.starts_with("-")).then(pl.col("party_number")),
                    party_label=pl.when(~pl.col("party_label").str.starts_with("#")).then(pl.col("party_label")),
                    pool=pool,
                    legend=count("legend"),
                )
            )
            frames["seats"].append(
                read(year, "seats")
                .filter(pl.col("cargo").is_in(["5", "6"]))
                .select("year", "uf", "cargo", seats=count("seats"))
            )
        frames["detail"].append(
            read(year, "detail")
            .filter(pl.col("cargo").is_in(list(OFFICES)))
            .select(
                "year",
                "round",
                "cargo",
                "uf",
                registered=count("registered"),
                voters=count("voters"),
                nominal=count("nominal"),
                blank=count("blank"),
                null=count("null"),
            )
        )
    candidates = pl.concat(frames["candidates"])
    unnamed = candidates.filter(pl.col("name").str.starts_with("#"))
    named = (
        candidates.filter(~pl.col("name").str.starts_with("#"))
        .group_by("year", "round", "cargo", "uf", "name", "pool", maintain_order=True)
        .agg(
            pl.col("ballot", "party", "party_number", "party_label", "coalition_label").drop_nulls().first(),
            status=pl.when((pl.col("status") == "ELEITO").any())
            .then(pl.lit("ELEITO"))
            .otherwise(pl.col("status").first()),
            listed=pl.col("votes").filter(pl.col("votes") >= 0).cast(pl.String).str.join(", "),
            votes=pl.when((pl.col("votes") >= 0).any()).then(pl.col("votes").filter(pl.col("votes") >= 0).sum()),
            repeated=pl.len(),
        )
    )
    candidates = pl.concat(
        [
            named,
            unnamed.with_columns(
                name=pl.lit(None, pl.String), listed=pl.lit(None, pl.String), repeated=pl.lit(1, pl.UInt32)
            ),
        ],
        how="diagonal_relaxed",
    ).with_columns(
        entry=pl.int_range(pl.len()).over("year", "round", "cargo", "uf", "name", "pool"),
        lists=pl.col("pool").n_unique().over("year", "round", "cargo", "uf", "name"),
    )
    candidates.write_parquet(PARSED / "candidates.parquet")
    pl.concat(frames["parties"]).write_parquet(PARSED / "parties.parquet")
    pl.concat(frames["detail"]).write_parquet(PARSED / "detail.parquet")
    pl.concat(frames["seats"]).write_parquet(PARSED / "seats.parquet")


def slug(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return "-".join("".join(c if c.isalnum() else " " for c in folded).split())


def place(year: int, uf: str) -> tuple[str, str]:
    if uf in ("FN", "IG", "PP") or (uf == "DF" and year < 1960):
        return "br.uf_1945", uf
    if uf in ("GP", "RB"):
        return "br.uf", {"GP": "RO", "RB": "RR"}[uf]
    if uf == "GB" or (uf == "RJ" and year < 1975) or (uf == "MT" and year < 1978) or (uf == "GO" and year < 1988):
        return "br.uf_1963", uf
    return "br.uf", uf


def label(year: int, uf: str) -> str:
    if uf == "DF":
        return "the Federal District"
    return LABELS.get(uf) or UFS[uf]


def party_of(year: int, abbreviation: str, number: str | None) -> str:
    if year < 1966:
        return f"{abbreviation}:1945"
    if year < 1980:
        return f"{abbreviation}:1966"
    return party_key(CONTINUING.get(abbreviation) or number or "0", abbreviation, year)


def emit() -> Path:
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    parties_file = pl.read_parquet(PARSED / "parties.parquet")
    detail = pl.read_parquet(PARSED / "detail.parquet")
    seats_file = {
        (year, uf, cargo): seats for year, uf, cargo, seats in pl.read_parquet(PARSED / "seats.parquet").iter_rows()
    }
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Brazil's presidential elections of 1945–1960 and 1989 and its Chamber of Deputies and Senate "
                "elections of 1945–1990, by state and territory"
            ),
            polity="BR",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        results: dict[int, UUID] = {}
        for year in DAYS:
            title = f"Resultados - {year}"
            results[year] = cs.source("Tribunal Superior Eleitoral", title)
            cs.add(
                "source",
                id=results[year],
                kind="official_body",
                publisher="Tribunal Superior Eleitoral",
                title=title,
                url=f"https://dadosabertos.tse.jus.br/dataset/resultados-{year}",
                license="Creative Commons Attribution",
                license_url="http://www.opendefinition.org/licenses/cc-by",
                notes=SOURCE_NOTE,
            )
            for kind in FOLDERS:
                if (year, kind) in FILES:
                    cs.artifact(raw.cached(SOURCE, FILES[(year, kind)]), results[year])

        brazil = cs.polity("BR")
        nation = cs.area("br.nation", "BR")
        if conn.execute("select 1 from scheme where code = 'br.uf_1945'").fetchone() is None:
            cs.add(
                "scheme",
                code="br.uf_1945",
                polity_id=brazil,
                kind="administrative",
                rank=1,
                noun="state or territory",
            )
        areas = {
            (scheme, code): area
            for scheme, code, area in conn.execute(
                "select scheme_code, code, area_id from reporting_unit where scheme_code in ('br.uf', 'br.uf_1963')"
            ).fetchall()
        }
        for code, (english, native) in TERRITORIES_1945.items():
            if code == "DF":
                area = areas[("br.uf_1963", "GB")]
            else:
                area = cs.area("br.uf_1945", code)
                if area in cs.minted:
                    cs.add("area", id=area, scheme_code="br.uf_1945")
                    cs.add("area_code", area_id=area, code=code, valid_from=None)
                    cs.add("area_parent", area_id=area, parent_id=nation, valid_from=None)
                    name(area, english)
                    name(area, native, "pt", "native")
            areas[("br.uf_1945", code)] = area
            unit = cs.unit("br.uf_1945", code)
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=brazil,
                    scheme_code="br.uf_1945",
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
                name(unit, english)
        units = {(year, uf): cs.unit(*place(year, uf)) for year, uf in candidates.select("year", "uf").unique().rows()}

        president = cs.key("br.office", "president")
        vice = cs.key("br.office", "vice-president")
        if vice in cs.minted:
            cs.add("office", id=vice, polity_id=brazil, kind="other")
            name(vice, "Vice President of Brazil")
            name(vice, "Vice-Presidente da República", "pt", "native")
        chamber = cs.key("br.body", "chamber-of-deputies")
        senate = cs.key("br.body", "senate")

        systems: dict[str, UUID] = {}
        for key, rules, title, methodology in (
            ("presidential-two-round", None, None, None),
            ("senate-plurality", None, None, None),
            (
                "presidential-plurality",
                {"formula": "fptp"},
                "Brazilian presidential plurality system, 1945–1960",
                PRESIDENT,
            ),
            (
                "senate-sublegenda",
                {"formula": "winner_take_all"},
                "Brazilian Senate sublegenda system, 1966–1986",
                SUBLEGENDAS,
            ),
            (
                "open-list-quotient-1950",
                {"formula": "dhondt", "lists": {"order": "open", "vacancy": "next_by_votes"}},
                "Brazilian open-list proportional representation, 1950–1990",
                QUOTIENT,
            ),
        ):
            systems[key] = cs.key("br.electoral_system", key)
            if systems[key] in cs.minted and rules is not None and title is not None:
                cs.add("electoral_system", id=systems[key], polity_id=brazil, rules=rules, rules_version=1)
                name(systems[key], title)
                cs.add("note", entity_id=systems[key], kind="methodology", body_md=methodology)
        if systems["senate-plurality"] in cs.minted:
            raise ValueError("load br.tse_history before the early elections")

        elections: dict[tuple[int, str], UUID] = {}
        paths: dict[tuple[int, str], str] = {}
        for year, days in DAYS.items():
            if len(days) == 2:
                cycle = cs.key("br.election", f"{days[0]}:presidential-election")
                cs.add(
                    "election",
                    id=cycle,
                    polity_id=brazil,
                    kind="general",
                    date_from=days[0],
                    date_to=days[1],
                    status="held",
                )
                name(cycle, f"{year} Brazilian presidential election")
                name(cycle, f"Eleição presidencial de {year}", "pt", "native")
                cs.add("slug", path=f"br/{days[0]}-presidential-election", entity_id=cycle)
                for round, day, ordinal, stage in (
                    ("1", days[0], "first", "general"),
                    ("2", days[1], "second", "runoff"),
                ):
                    elections[(year, round)] = cs.key("br.election", str(day))
                    paths[(year, round)] = f"br/{day}-{stage}"
                    cs.add(
                        "election",
                        id=elections[(year, round)],
                        polity_id=brazil,
                        parent_id=cycle,
                        kind=stage,
                        date_from=day,
                        date_to=day,
                        status="held",
                    )
                    name(elections[(year, round)], f"{year} Brazilian presidential election, {ordinal} round")
                    cs.add("slug", path=paths[(year, round)], entity_id=elections[(year, round)])
                continue
            day = days[0]
            elections[(year, "1")] = cs.key("br.election", f"{day}:general-elections")
            paths[(year, "1")] = f"br/{day}-general-elections"
            cs.add(
                "election",
                id=elections[(year, "1")],
                polity_id=brazil,
                kind="general",
                date_from=day,
                date_to=day,
                status="held",
            )
            name(elections[(year, "1")], f"{year} Brazilian general election")
            name(elections[(year, "1")], f"Eleições de {year}", "pt", "native")
            cs.add("slug", path=paths[(year, "1")], entity_id=elections[(year, "1")])

        parties: dict[str, UUID] = {}
        by_abbreviation: dict[tuple[int, str], UUID] = {}
        for year, abbreviation, number, text in (
            pl.concat(
                [
                    candidates.select("year", "party", "party_number", "party_label"),
                    parties_file.select("year", "party", "party_number", "party_label"),
                ]
            )
            .filter(pl.col("party").is_not_null())
            .sort("year", "party_label", nulls_last=True)
            .unique(["year", "party"], keep="first")
            .sort("year", "party")
            .iter_rows()
        ):
            key = party_of(year, abbreviation, number)
            if key not in parties:
                parties[key] = cs.key("br.tse.party", key)
                if parties[key] in cs.minted:
                    cs.add("party", id=parties[key], kind="party", polity_id=brazil)
                    name(parties[key], proper(text or abbreviation), "pt")
                    name(parties[key], abbreviation, "pt", "abbreviation")
            by_abbreviation[(year, abbreviation.replace(" ", "").upper())] = parties[key]

        alliances: dict[tuple[int, str, str, str], UUID] = {}
        for year, cargo, uf, pool, text in (
            candidates.filter(pl.col("pool").str.contains("/"))
            .sort("coalition_label", nulls_last=True)
            .unique(["year", "cargo", "uf", "pool"], keep="first")
            .select("year", "cargo", "uf", "pool", "coalition_label")
            .sort("year", "cargo", "uf", "pool")
            .iter_rows()
        ):
            alliance = alliances[(year, cargo, uf, pool)] = cs.key(f"{SOURCE}.coalition", f"{year}:{cargo}:{uf}:{pool}")
            cs.add("alliance", id=alliance, kind="electoral_alliance", election_id=elections[(year, "1")])
            name(alliance, proper(text) if text else pool.replace("/", " / "), "pt")
            for member in {part.replace(" ", "").upper() for part in pool.split("/")}:
                if (year, member) in by_abbreviation:
                    cs.add("alliance_member", alliance_id=alliance, party_id=by_abbreviation[(year, member)])

        presidential = {
            slug(text): entity
            for entity, text in conn.execute(
                """
                select distinct p.id, n.text from person p
                join name n on n.entity_id = p.id and n.kind = 'official'
                join candidacy y on y.person_id = p.id
                join contest_option o on o.id = y.option_id
                join contest c on c.id = o.contest_id
                where c.office_id = %s and not exists (select from entity_merge m where m.entity_id = p.id)
                """,
                [president],
            ).fetchall()
        }
        persons: dict[str, UUID] = {}
        totals = {
            (year, round, cargo, uf): row
            for year, round, cargo, uf, *row in detail.select(
                "year", "round", "cargo", "uf", "registered", "voters", "nominal", "blank", "null"
            ).iter_rows()
        }
        legends = parties_file.filter(pl.col("legend") > 0)
        values: list[tuple[str, UUID, UUID | None, UUID, int]] = []
        for (year, round, cargo), contest_rows in sorted(
            candidates.group_by("year", "round", "cargo"), key=lambda item: str(item[0])
        ):
            year, round, cargo = int(str(year)), str(round), str(cargo)
            office = OFFICES[cargo]
            day = DAYS[year][int(round) - 1]
            national = office in ("president", "vice-president")
            groups = (
                [("BR", contest_rows)]
                if national
                else sorted(
                    ((str(uf), frame) for (uf,), frame in contest_rows.group_by("uf")), key=lambda item: item[0]
                )
            )
            runoff = len(DAYS[year]) == 2
            suffix = f", {'second' if round == '2' else 'first'} round" if runoff else ""
            for uf, rows in groups:
                rows = rows.sort("votes", "name", descending=[True, False], nulls_last=True)
                where = "" if national else label(year, uf)
                place_slug = slug(where.removeprefix("the "))
                if office == "president":
                    key, path, title = (
                        f"{day}:president",
                        "president",
                        f"{year} Brazilian presidential election{suffix}",
                    )
                elif office == "vice-president":
                    key, path, title = (
                        f"{day}:vice-president",
                        "vice-president",
                        f"{year} Brazilian vice-presidential election",
                    )
                elif office == "senate":
                    key, path, title = (
                        f"{day}:senate:{uf}",
                        f"senate-{place_slug}",
                        f"{year} Senate election in {where}",
                    )
                else:
                    key, path, title = (
                        f"{day}:chamber-of-deputies:{uf}",
                        f"chamber-of-deputies-{place_slug}",
                        f"{year} Chamber of Deputies election in {where}",
                    )
                elected = rows.filter(pl.col("status") == "ELEITO")
                if national:
                    elected = elected.unique("name")
                magnitude = 1 if national else elected.height
                declared = seats_file.get((year, uf, cargo))
                if office == "chamber" and declared is not None and declared > 0:
                    magnitude = declared
                listed = office == "chamber" or (office == "senate" and year in SUBLEGENDA)
                system = (
                    "presidential-two-round"
                    if national and year == 1989
                    else "presidential-plurality"
                    if national
                    else "senate-sublegenda"
                    if office == "senate" and year in SUBLEGENDA
                    else "senate-plurality"
                    if office == "senate"
                    else "open-list-quotient-1950"
                    if year >= 1950
                    else None
                )
                if system is None:
                    continue
                if national:
                    values_rows = rows
                    rows = (
                        rows.group_by("name")
                        .agg(
                            pl.col("ballot").drop_nulls().first(),
                            pl.col("party").drop_nulls().first(),
                            pl.col("party_number").drop_nulls().first(),
                            pl.col("pool").drop_nulls().first(),
                            pl.col("status").first(),
                            pl.col("votes").sum(),
                        )
                        .sort("votes", descending=True)
                    )
                else:
                    values_rows = rows
                override = None
                if office == "chamber":
                    counted = int(rows["votes"].fill_null(0).clip(0).sum()) + int(
                        legends.filter((pl.col("year") == year) & (pl.col("uf") == uf))["legend"].sum()
                    )
                    _, _, _, blank, null = totals.get((year, round, cargo, uf), (None,) * 5)
                    extra = max(blank or 0, 0) + (max(null or 0, 0) if year < 1966 else 0)
                    if counted:
                        override = {
                            "threshold": {
                                "percent": float(f"{100 * (counted + extra) / counted / magnitude:.6f}"),
                                "level": "list",
                            }
                        }
                contest = cs.key("br.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=elections[(year, round)],
                    kind="office" if national else "body_seats" if office == "senate" else "list_allocation",
                    office_id=president if office == "president" else vice if office == "vice-president" else None,
                    body_id=senate if office == "senate" else chamber if office == "chamber" else None,
                    district_area_id=nation if national else areas[place(year, uf)],
                    magnitude=magnitude,
                    round_number=int(round),
                    previous_round_id=cs.key("br.contest", f"{DAYS[year][0]}:president") if round == "2" else None,
                    electoral_system_id=systems[system],
                    rules_override=override,
                    default_result_kind="counted",
                )
                name(contest, title)
                cs.add("slug", path=f"{paths[(year, round)]}/{path}", entity_id=contest)
                if office == "chamber" and declared is not None and declared != elected.height:
                    cs.add(
                        "note",
                        entity_id=contest,
                        kind="caveat",
                        body_md=SEATS_NOTE.format(elected=elected.height, seats=declared),
                    )
                result_set = cs.key(f"{SOURCE}.result_set", key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=elections[(year, round)],
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=results[year],
                    is_default=True,
                    completeness=1,
                )

                lists: dict[str, UUID] = {}
                legend_options: dict[tuple[str, str], UUID] = {}
                if listed:
                    pools = set(rows["pool"].drop_nulls())
                    if office == "chamber":
                        pools |= set(
                            legends.filter((pl.col("year") == year) & (pl.col("uf") == uf))["pool"].drop_nulls()
                        )
                    for group in sorted(pools):
                        coalition = alliances.get((year, cargo, uf, group)) if "/" in group else None
                        lists[group] = cs.key("br.option", f"{key}:list:{group}")
                        cs.add(
                            "contest_option",
                            id=lists[group],
                            contest_id=contest,
                            kind="list",
                            party_id=None if "/" in group else by_abbreviation.get((year, group.upper())),
                            alliance_id=coalition,
                            ballot_label=group.replace("/", " / "),
                        )
                    if office == "chamber":
                        for group, abbreviation, legend in (
                            legends.filter((pl.col("year") == year) & (pl.col("uf") == uf))
                            .group_by("pool", "party")
                            .agg(pl.col("legend").sum())
                            .sort("pool", "party", nulls_last=True)
                            .iter_rows()
                        ):
                            if group is None:
                                continue
                            option = legend_options[(group, abbreviation or "")] = cs.key(
                                "br.option", f"{key}:legend:{group}:{abbreviation or ''}"
                            )
                            cs.add(
                                "contest_option",
                                id=option,
                                contest_id=contest,
                                kind="legend",
                                parent_option_id=lists[group],
                                party_id=by_abbreviation.get((year, abbreviation.upper())) if abbreviation else None,
                                alliance_id=alliances.get((year, cargo, uf, group))
                                if abbreviation is None and "/" in group
                                else None,
                                ballot_label=abbreviation or group.replace("/", " / "),
                            )
                            values.append(("legend", result_set, option, units[(year, uf)], legend))

                options: dict[tuple[str | None, str | None, int], UUID] = {}
                won: dict[str, int] = {}
                for row in rows.iter_rows(named=True):
                    group = row["pool"]
                    entry = (row["name"], None if national else group, 0 if national else row["entry"])
                    if row["name"] is None:
                        suffix = f"unnamed:{group}:{row['entry']}"
                    else:
                        suffix = slug(row["name"]) + (f":{group}" if not national and row["lists"] > 1 else "")
                    party = by_abbreviation.get((year, row["party"].upper())) if row["party"] else None
                    option = options[entry] = cs.key("br.option", f"{key}:{suffix}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="ticket" if national and year == 1989 else "candidate",
                        parent_option_id=lists.get(group) if listed and group else None,
                        party_id=party,
                        alliance_id=alliances.get((year, cargo, uf, group))
                        if not listed and group and "/" in group
                        else None,
                        ballot_label=proper(row["ballot"] or row["name"]) if row["name"] else UNNAMED,
                    )
                    if row["name"] is not None:
                        person_key = slug(row["name"]) if national else f"{slug(row['name'])}|{uf}"
                        if person_key not in persons:
                            found = presidential.get(slug(row["name"])) if national else None
                            persons[person_key] = found or cs.key(f"{SOURCE}.person", person_key)
                            if persons[person_key] in cs.minted:
                                cs.add("person", id=persons[person_key])
                                name(persons[person_key], proper(row["name"]), "pt")
                        cs.add(
                            "candidacy",
                            id=cs.key("br.candidacy", f"{key}:{suffix}"),
                            person_id=persons[person_key],
                            option_id=option,
                            role="list_member" if listed else "head",
                            ballot_name=row["ballot"] or row["name"],
                            member_of_party_id=party,
                        )
                    outcome = (
                        "elected" if row["status"] == "ELEITO" else "advanced" if row["status"] == "2º TURNO" else None
                    )
                    if outcome is not None:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind=outcome)
                        if listed and outcome == "elected" and group:
                            won[group] = won.get(group, 0) + 1
                unnamed = values_rows.filter(pl.col("name").is_null()).height
                if unnamed:
                    cs.add("note", entity_id=contest, kind="caveat", body_md=NO_NAME.format(count=unnamed))
                for row in values_rows.filter(pl.col("repeated") > 1).iter_rows(named=True):
                    cs.add(
                        "note",
                        entity_id=contest,
                        kind="caveat",
                        body_md=REPEATED.format(
                            name=proper(row["name"]), where=label(year, row["uf"]), votes=row["listed"]
                        ),
                    )
                for group, seats in won.items():
                    cs.add("outcome", result_set_id=result_set, option_id=lists[group], kind="won", seats=seats)

                for row in values_rows.iter_rows(named=True):
                    if row["votes"] is not None and row["votes"] >= 0:
                        values.append(
                            (
                                "votes",
                                result_set,
                                options[
                                    (row["name"], None, 0) if national else (row["name"], row["pool"], row["entry"])
                                ],
                                units[(year, row["uf"])],
                                row["votes"],
                            )
                        )
                for state in sorted(set(values_rows["uf"])):
                    registered, voters, nominal, blank, null = totals.get((year, round, cargo, state), (None,) * 5)
                    for measure, value in ((2, registered), (14, voters), (6, blank), (5, null)):
                        if value is not None and value >= 0:
                            values.append((str(measure), result_set, None, units[(year, state)], value))
                    counted = int(values_rows.filter(pl.col("uf") == state)["votes"].fill_null(0).clip(0).sum())
                    if not national and nominal is not None and 0 <= counted < nominal:
                        cs.add(
                            "note",
                            entity_id=contest,
                            kind="caveat",
                            body_md=SHORT.format(votes=counted, nominal=nominal),
                        )

        cs.frame(
            "result_value",
            pl.DataFrame(
                [
                    {
                        "result_set_id": str(result_set),
                        "unit_id": str(unit),
                        "option_id": None if option is None else str(option),
                        "measure": 1 if kind in ("votes", "legend") else int(kind),
                        "value": value,
                    }
                    for kind, result_set, option, unit, value in values
                ],
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "measure": pl.Int16,
                    "value": pl.Int64,
                },
            ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
        )
        return cs.write()
