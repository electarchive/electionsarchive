from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "ch.zh_statistik"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
OGD = "https://daten.statistik.zh.ch/ogd/daten/ressourcen/KTZH_00000693_{}.csv"
FILES = {
    "units": "00004704",
    "candidates": "00004703",
    "panachage": "00004705",
    "lists": "00004944",
    "apparentements": "00004523",
    "attribution": "00004844",
}
PUBLISHER = "Amt für Statistik und Daten Kanton Zürich"
TITLE = "Archiv Wahlresultate Nationalratswahlen"
ELECTION = date(2023, 10, 22)
MAGNITUDE = 36
ABROAD = "9010"
UNALLOCATED = 32
UNIT_MEASURES = {
    "StimBerTotal": 2,
    "WZEingegangen": 3,
    "WZGueltig": 4,
    "WZUngueltig": 5,
    "WZLeer": 6,
    "WZUnveraendert": 22,
    "WZVeraendert": 23,
}
LIST_MEASURES = {
    "Liste_ParteistimmenTotal": 8,
    "Liste_Zusatzstimmen": 9,
    "Liste_WZUnveraendert": 22,
    "Liste_WZVeraendert": 23,
}
PARTIES = {
    "SVP": ("Schweizerische Volkspartei", "SVP", "Swiss People's Party"),
    "SP": ("Sozialdemokratische Partei der Schweiz", "SP", "Social Democratic Party of Switzerland"),
    "GP": ("GRÜNE Schweiz", "GRÜNE", "Green Party of Switzerland"),
    "GLP": ("Grünliberale Partei der Schweiz", "GLP", "Green Liberal Party of Switzerland"),
    "FDP": ("FDP.Die Liberalen", "FDP", "FDP.The Liberals"),
    "Mitte": ("Die Mitte", "Mitte", "The Centre"),
    "EVP": ("Evangelische Volkspartei der Schweiz", "EVP", "Evangelical People's Party of Switzerland"),
    "EDU": ("Eidgenössisch-Demokratische Union", "EDU", "Federal Democratic Union of Switzerland"),
    "SD": ("Schweizer Demokraten", "SD", "Swiss Democrats"),
}
INDEPENDENT = ("SansPaps", "EUF", "PfL", "WIM")
SOURCE_NOTE = (
    "The canton of Zurich counts its National Council election and publishes results for every commune. The cities "
    "of Zurich and Winterthur are split into counting districts. For every candidate, it also publishes votes by the "
    "list named on the ballots they were written on."
)
METHODOLOGY = (
    "Each candidate written on a ballot gives one candidate vote to that candidate and one party vote to the "
    "candidate's list. Blank lines count as additional party votes for the list named on the ballot. On a ballot "
    "without a list name, they are lost. Seats go to lists by party votes, using the Hagenbach-Bischoff method, which "
    "gives the same result as D'Hondt. Lists in an apparentement, and in a sub-apparentement within it, first take "
    "seats together as one list, then share them out by the same method. Each list's seats go to its candidates with "
    "the most candidate votes."
)
FLOWS = (
    "Candidate vote flows split each candidate's votes by the list named on the ballot they were written on. The flow "
    "from the candidate's own list includes unmodified ballots. Ballots without a list name count as their own "
    "origin. Zero flows are not stored."
)


def fetch() -> None:
    for number in FILES.values():
        raw.fetch(SOURCE, OGD.format(number), suffix=".csv")


def parse() -> None:
    units, candidates, panachage, lists, apparentements, attribution = (
        pl.read_csv(raw.cached(SOURCE, OGD.format(number)).path, infer_schema=False) for number in FILES.values()
    )
    origins = [column for column in panachage.columns if column[:2].isdigit() and column[2] == "."]
    units = units.with_columns(pl.col(*UNIT_MEASURES, "StmWZVeraendertLeerAmtlLeer").cast(pl.Int64))
    panachage = panachage.with_columns(
        pl.col(*LIST_MEASURES, "Kand_StimmenUnveraendert", "Kand_StimmenTotal", *origins).cast(pl.Int64)
    )
    lists = lists.with_columns(pl.col("Liste_ParteistimmenTotal", "Liste_Kandstimmen").cast(pl.Int64))
    candidates = candidates.with_columns(pl.col("Kand_Stimmentotal").cast(pl.Int64))

    flows = (
        panachage.unpivot(
            on=origins,
            index=["Einheit_Code", "Liste_KandID", "Liste_ID", "Kand_StimmenUnveraendert"],
            variable_name="origin",
            value_name="value",
        )
        .with_columns(pl.col("origin").str.slice(0, 2))
        .with_columns(
            pl.col("value")
            + pl.when(pl.col("origin") == pl.col("Liste_ID")).then("Kand_StimmenUnveraendert").otherwise(0)
        )
        .filter(pl.col("value") != 0)
        .select(
            unit="Einheit_Code",
            candidate="Liste_KandID",
            origin=pl.when(pl.col("origin") != "99").then("origin"),
            value="value",
        )
    )
    candidate_votes = panachage.select(unit="Einheit_Code", candidate="Liste_KandID", value="Kand_StimmenTotal")
    by_list = panachage.unique(["Einheit_Code", "Liste_ID"]).select("Einheit_Code", "Liste_ID", *LIST_MEASURES)
    cumulated = set(
        panachage.filter(
            (pl.col("Liste_WZUnveraendert") > 0)
            & (pl.col("Kand_StimmenUnveraendert") == 2 * pl.col("Liste_WZUnveraendert"))
        )["Liste_KandID"]
    )
    elected = candidates.filter(pl.col("Kand_Gewaehlt") == "1")
    declared = Counter(elected["Liste_ID"])

    party_votes = dict(lists.group_by("Liste_ID").agg(pl.col("Liste_ParteistimmenTotal").sum()).iter_rows())
    tree: dict[str, set[str]] = defaultdict(set)
    for number, lv, luv in apparentements.select("Liste", "LV", "LUV").iter_rows():
        if number not in party_votes:
            continue
        if luv is not None:
            tree[f"LUV{luv}"].add(number)
            tree[f"LV{lv}"].add(f"LUV{luv}")
        elif lv is not None:
            tree[f"LV{lv}"].add(number)
        tree[""].add(number if lv is None else f"LV{lv}")

    def total(member: str) -> int:
        return party_votes[member] if member in party_votes else sum(total(part) for part in tree[member])

    engine: Counter[str] = Counter()
    queue = [("", MAGNITUDE)]
    while queue:
        group, seats = queue.pop()
        split: Counter[str] = Counter()
        for _ in range(seats):
            split[max(sorted(tree[group]), key=lambda member: total(member) / (split[member] + 1))] += 1
        for member, count in split.items():
            if member in tree:
                queue.append((member, count))
            else:
                engine[member] = count

    failures = {
        "flows that do not sum to candidate votes": candidate_votes.join(
            flows.group_by("unit", "candidate").agg(flowed=pl.col("value").sum()), on=["unit", "candidate"], how="left"
        ).filter(pl.col("value") != pl.col("flowed").fill_null(0)),
        "unmodified-ballot votes not explained by printed lines": panachage.filter(
            pl.col("Kand_StimmenUnveraendert")
            != pl.when(pl.col("Liste_KandID").is_in(list(cumulated))).then(2).otherwise(1)
            * pl.col("Liste_WZUnveraendert")
        ),
        "party votes that differ between files or from candidate plus additional votes": lists.join(
            by_list, on=["Einheit_Code", "Liste_ID"], how="full", coalesce=True, suffix="_panachage"
        ).filter(
            pl.col("Liste_ParteistimmenTotal").ne_missing(pl.col("Liste_ParteistimmenTotal_panachage"))
            | (
                pl.col("Liste_ParteistimmenTotal")
                != pl.col("Liste_Kandstimmen") + pl.col("Liste_Zusatzstimmen").cast(pl.Int64)
            )
        ),
        "candidate votes that differ from the sum over units": candidates.join(
            candidate_votes.group_by("candidate").agg(pl.col("value").sum()),
            left_on="Liste_KandID",
            right_on="candidate",
            how="left",
        ).filter(pl.col("Kand_Stimmentotal").ne_missing(pl.col("value"))),
        "units whose lost blank lines do not balance": units.join(
            lists.group_by("Einheit_Code").agg(pl.col("Liste_ParteistimmenTotal").sum()), on="Einheit_Code", how="left"
        ).filter(
            MAGNITUDE * pl.col("WZGueltig") - pl.col("Liste_ParteistimmenTotal")
            != pl.col("StmWZVeraendertLeerAmtlLeer")
        ),
        "lists whose elected candidates are not their top vote-getters": candidates.with_columns(
            rank=pl.col("Kand_Stimmentotal").rank("ordinal", descending=True).over("Liste_ID")
        ).filter(
            (pl.col("Kand_Gewaehlt") == "1")
            != (pl.col("rank") <= pl.col("Liste_ID").replace_strict(declared, default=0, return_dtype=pl.Int64))
        ),
    }
    problems = [f"{label}: {frame.head(3)}" for label, frame in failures.items() if frame.height]
    if engine != declared:
        problems.append(f"nested Hagenbach-Bischoff gives {dict(engine)}, declared {dict(declared)}")
    if problems:
        raise ValueError("\n".join(problems))

    PARSED.mkdir(parents=True, exist_ok=True)
    units.select(
        "Einheit_Code",
        "Einheit_Name",
        commune=pl.col("Einheit_BFS").str.zfill(4),
        district=pl.col("Einheit_BFS") != pl.col("Einheit_BFSSub"),
    ).write_parquet(PARSED / "units.parquet")
    attribution.filter(pl.col("Jahr") == str(ELECTION.year)).join(
        apparentements.select(Liste_ID="Liste", lv="LV", luv="LUV"), on="Liste_ID", how="left"
    ).select(
        "Liste_ID",
        "Liste_Code",
        "Liste_Bez",
        "Partei",
        "lv",
        "luv",
        seats=pl.col("Liste_ID").replace_strict(declared, default=0, return_dtype=pl.Int64),
    ).write_parquet(PARSED / "lists.parquet")
    candidates.select(
        "Liste_ID",
        "Liste_KandID",
        "Kand_Nachname",
        "Kand_Vorname",
        pl.col("Kand_Jahrgang").cast(pl.Int32),
        "Kand_Geschlecht",
        incumbent=pl.col("Kand_Bisher") == "1",
        elected=pl.col("Kand_Gewaehlt") == "1",
        cumulated=pl.col("Liste_KandID").is_in(list(cumulated)),
    ).write_parquet(PARSED / "candidates.parquet")
    pl.concat(
        [
            units.unpivot(on=list(UNIT_MEASURES), index="Einheit_Code", variable_name="field").select(
                unit="Einheit_Code", option=pl.lit(None, pl.String), field="field", value="value"
            ),
            by_list.unpivot(on=list(LIST_MEASURES), index=["Einheit_Code", "Liste_ID"], variable_name="field").select(
                unit="Einheit_Code", option="Liste_ID", field="field", value="value"
            ),
            candidate_votes.select("unit", option="candidate", field=pl.lit("Kand_StimmenTotal"), value="value"),
        ]
    ).write_parquet(PARSED / "values.parquet")
    flows.write_parquet(PARSED / "flows.parquet")


def emit() -> Path:
    units = pl.read_parquet(PARSED / "units.parquet")
    lists = pl.read_parquet(PARSED / "lists.parquet")
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    values = pl.read_parquet(PARSED / "values.parquet")
    flows = pl.read_parquet(PARSED / "flows.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="2023 National Council election in the canton of Zurich, by commune and counting district",
            polity="CH",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://opendata.swiss/de/dataset/wahlarchiv-nationalratswahlen",
            license="Open use; must provide the source",
            license_url="https://opendata.swiss/en/terms-of-use#terms_by",
            attribution=PUBLISHER,
            notes=SOURCE_NOTE,
        )
        for number in FILES.values():
            cs.artifact(raw.cached(SOURCE, OGD.format(number)), source)

        switzerland = cs.polity("CH")
        zurich = cs.polity("CH-ZH")
        canton = cs.area("ch.canton", "ZH")
        if cs.minted & {switzerland, zurich, canton}:
            raise ValueError("run ch.swisstopo first: Switzerland and the canton of Zurich must exist")

        cs.add(
            "scheme",
            code="ch-zh.counting_district",
            polity_id=zurich,
            kind="precinct",
            rank=3,
            noun="counting district",
        )
        unit_ids: dict[str, UUID] = {}
        for code, label, commune, district in units.iter_rows():
            if not district:
                unit_ids[code] = cs.unit("ch.commune", commune)
                if commune == ABROAD:
                    cs.add(
                        "reporting_unit",
                        id=unit_ids[code],
                        polity_id=switzerland,
                        scheme_code="ch.commune",
                        code=commune,
                        area_relation="within",
                        area_id=canton,
                        kind="aggregate",
                        valid_from=None,
                    )
                    name(unit_ids[code], label, "de")
                elif unit_ids[code] in cs.minted:
                    raise ValueError(f"run ch.bfs first: commune {commune} ({label}) has no reporting unit")
                continue
            parent = cs.unit("ch.commune", commune)
            area = cs.area("ch.commune", commune)
            if cs.minted & {parent, area}:
                raise ValueError(f"commune {commune} must exist before its counting district {code}")
            unit_ids[code] = cs.unit("ch-zh.counting_district", code)
            cs.add(
                "reporting_unit",
                id=unit_ids[code],
                polity_id=switzerland,
                scheme_code="ch-zh.counting_district",
                code=code,
                parent_id=parent,
                area_relation="within",
                area_id=area,
                kind="standard",
                valid_from=None,
            )
            name(unit_ids[code], label, "de")

        body = cs.key("ch.body", "national-council")
        cs.add("body", id=body, polity_id=switzerland, kind="lower_house")
        name(body, "National Council")
        for text, lang in (
            ("Nationalrat", "de"),
            ("Conseil national", "fr"),
            ("Consiglio nazionale", "it"),
            ("Cussegl naziunal", "rm"),
        ):
            name(body, text, lang, "native")
        cs.add("body_size", body_id=body, seats=200, valid_from=date(1963, 1, 1), valid_from_precision="year")
        system = cs.key("ch.electoral_system", "national-council")
        cs.add(
            "electoral_system",
            id=system,
            polity_id=switzerland,
            rules={"formula": "hagenbach_bischoff", "lists": {"order": "open", "vacancy": "next_by_votes"}},
            rules_version=1,
        )
        name(system, "Swiss National Council proportional representation")
        election = cs.key("ch.election", str(ELECTION))
        cs.add(
            "election",
            id=election,
            polity_id=switzerland,
            kind="general",
            date_from=ELECTION,
            date_to=ELECTION,
            status="held",
        )
        name(election, "2023 Swiss federal election")
        name(election, "Eidgenössische Wahlen 2023", "de", "native")
        cs.add("slug", path=f"ch/{ELECTION}-general", entity_id=election)
        contest_key = f"{ELECTION}:national-council:ZH"
        contest = cs.key("ch.contest", contest_key)
        cs.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=body,
            district_area_id=canton,
            magnitude=MAGNITUDE,
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        name(contest, "2023 National Council election in Zurich")
        name(contest, "Nationalratswahl 2023 im Kanton Zürich", "de", "native")
        cs.add("slug", path=f"ch/{ELECTION}-general/national-council-zurich", entity_id=contest)
        result_set = cs.key(f"{SOURCE}.result_set", contest_key)
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
        cs.add("note", entity_id=system, kind="methodology", body_md=METHODOLOGY)
        cs.add("note", entity_id=result_set, kind="methodology", body_md=FLOWS)

        alliances: dict[str, UUID] = {}
        for prefix, column, english, german in (
            ("LV", "lv", "Zurich apparentement", "Listenverbindung"),
            ("LUV", "luv", "Zurich sub-apparentement", "Unterlistenverbindung"),
        ):
            for number, parent in (
                lists.filter(pl.col(column).is_not_null())
                .select(pl.col(column).alias("number"), "lv")
                .unique()
                .sort(pl.col("number").cast(pl.Int64))
                .iter_rows()
            ):
                alliances[f"{prefix}{number}"] = cs.key("ch.alliance", f"{contest_key}:{prefix}{number}")
                cs.add(
                    "alliance",
                    id=alliances[f"{prefix}{number}"],
                    kind="apparentement",
                    election_id=election,
                    parent_alliance_id=alliances[f"LV{parent}"] if prefix == "LUV" else None,
                )
                name(alliances[f"{prefix}{number}"], f"{english} {number}")
                name(alliances[f"{prefix}{number}"], f"{german} {number}", "de", "native")

        parties: dict[str, UUID] = {}
        for key, (full, short, english) in PARTIES.items():
            parties[key] = cs.key("ch.party", key)
            cs.add("party", id=parties[key], kind="party", polity_id=switzerland)
            name(parties[key], full, "de")
            name(parties[key], english, "en", "english")
            name(parties[key], short, "de", "abbreviation")
        options: dict[str, UUID] = {}
        for number, code, label, party, lv, luv, seats in lists.iter_rows():
            if party not in PARTIES:
                party = f"ZH:{code}"
                parties[party] = cs.key("ch.party", party)
                cs.add(
                    "party",
                    id=parties[party],
                    kind="independent_group" if code in INDEPENDENT else "party",
                    polity_id=zurich,
                )
                name(parties[party], label, "de")
                name(parties[party], code, "de", "abbreviation")
            options[number] = cs.key("ch.option", f"{contest_key}:{number}")
            cs.add(
                "contest_option",
                id=options[number],
                contest_id=contest,
                kind="list",
                party_id=parties[party],
                alliance_id=alliances[f"LUV{luv}"] if luv is not None else None if lv is None else alliances[f"LV{lv}"],
                ballot_position=int(number),
                ballot_label=label,
            )
            if seats:
                cs.add("outcome", result_set_id=result_set, option_id=options[number], kind="elected", seats=seats)

        for row in candidates.iter_rows(named=True):
            candidate = row["Liste_KandID"]
            display = f"{row['Kand_Vorname']} {row['Kand_Nachname']}"
            options[candidate] = cs.key("ch.option", f"{contest_key}:{candidate}")
            cs.add(
                "contest_option",
                id=options[candidate],
                contest_id=contest,
                kind="candidate",
                parent_option_id=options[row["Liste_ID"]],
                ballot_position=int(candidate[2:]),
                ballot_label=display,
            )
            person = cs.key(f"{SOURCE}.person", f"{ELECTION}:{candidate}")
            cs.add(
                "person",
                id=person,
                birth_date=date(row["Kand_Jahrgang"], 1, 1),
                birth_precision="year",
                gender={"M": "male", "W": "female"}[row["Kand_Geschlecht"]],
            )
            name(person, display, "de")
            cs.add(
                "candidacy",
                id=cs.key("ch.candidacy", f"{contest_key}:{candidate}"),
                person_id=person,
                option_id=options[candidate],
                role="list_member",
                list_position=int(candidate[2:]),
                ballot_name=f"{row['Kand_Nachname']} {row['Kand_Vorname']}",
                is_independent=False,
                is_incumbent=row["incumbent"],
                ballot_lines=2 if row["cumulated"] else 1,
            )
            if row["elected"]:
                cs.add("outcome", result_set_id=result_set, option_id=options[candidate], kind="elected")

        if result_set in cs.minted:
            abroad = next(code for code, _, commune, _ in units.iter_rows() if commune == ABROAD)
            ids = {key: str(id) for key, id in options.items()}
            located = {
                "result_set_id": pl.lit(str(result_set)),
                "unit_id": pl.col("unit").replace_strict({code: str(id) for code, id in unit_ids.items()}),
                "channel": pl.when(pl.col("unit") == abroad).then(4).otherwise(0).cast(pl.Int16),
                "count_round": pl.lit(0, pl.Int16),
                "flags": pl.when(pl.col("unit") == abroad).then(UNALLOCATED).otherwise(0).cast(pl.Int16),
            }
            cs.frame(
                "result_value",
                values.select(
                    **located,
                    option_id=pl.col("option").replace_strict(ids, default=None),
                    measure=pl.col("field").replace_strict(
                        {**UNIT_MEASURES, **LIST_MEASURES, "Kand_StimmenTotal": 10}, return_dtype=pl.Int16
                    ),
                    value="value",
                ),
            )
            cs.frame(
                "result_flow",
                flows.select(
                    **located,
                    from_option_id=pl.col("origin").replace_strict(ids, default=None),
                    option_id=pl.col("candidate").replace_strict(ids),
                    measure=pl.lit(10, pl.Int16),
                    value="value",
                ),
            )
        return cs.write()
