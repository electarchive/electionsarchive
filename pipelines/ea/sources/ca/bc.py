import html
import re
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "ca.bc"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 10, 24)
FIRST = date(2024, 10, 19)
PAGE = "https://elections.bc.ca/2026-provincial-election/candidate-list/"
CANDIDATES = "https://elections.bc.ca/2026election/2026-provincial-general-election-candidates.csv"
RESULTS = "https://results.elections.bc.ca/Results_11684_GE-2026-10-24_Candidate.html"
DECLARED = "https://elections.bc.ca/news/final-results-for-the-2026-provincial-general-election-now-available/"
RESULTS_TITLE = "2026 Provincial General Election final count results"
UNAFFILIATED = {"Independent", "Other"}
# The results page's own summary of the final count: votes by affiliation, the ballots considered, and the seats
# each party leads. The parse checks the candidate rows against these figures. Affiliations are spelled as in the
# nomination list (the summary writes "Christian Heritage Party of B.C." and "Unaffiliated" for "Other").
PARTY_VOTES = {
    "BC Green Party": 216_808,
    "BC NDP": 730_952,
    "Conservative Party": 928_027,
    "CWP": 488,
    "CentreBC": 31_673,
    "Christian Heritage Party of BC": 478,
    "Communist Party of BC": 621,
    "Freedom Party of BC": 131,
    "Libertarian": 1_178,
    "OneBC": 28_482,
    "Independent": 3_313,
    "Other": 2_838,
}
CONSIDERED = 1_950_193
SEATS = {"BC Green Party": 2, "BC NDP": 27, "Conservative Party": 64}
# Districts whose final-count margin fell within the automatic judicial recount threshold (1/500 of the ballots
# considered), with the threshold and the margin the declaration gives.
RECOUNTS = {"Kelowna Centre": (48, 43), "Ladysmith-Oceanside": (64, 50)}
PENDING = (
    "Elections BC counts mail-in and absentee ballots at the final count, planned for 6 to 10 November 2026, and "
    "the writs are returned on 18 November. Results are recorded here once they are official."
)
NOTE = (
    "Results are Elections BC's final count, completed on 9 November 2026, which adds mail-in, absentee, and other "
    "certification-envelope ballots to the initial count of 24 October. The Chief Electoral Officer declared the "
    "winners at its close. Elections BC put registered voters at 3,738,182 as of 14 October 2026 but does not give "
    "a figure for each district with the final count."
)
RECOUNT = (
    "The final-count margin between the top two candidates, {margin} votes, is within the {threshold}-vote "
    "threshold (1/500 of the ballots considered) at which the Election Act requires a judicial recount, so the "
    "Supreme Court of British Columbia recounts this district. These are the final-count figures and the winner "
    "declared at the close of the final count."
)


def fetch() -> None:
    raw.fetch(SOURCE, PAGE, suffix=".html")
    raw.fetch(SOURCE, CANDIDATES, suffix=".csv")
    raw.fetch(SOURCE, RESULTS, suffix=".html")
    raw.fetch(SOURCE, DECLARED, suffix=".html")


def text(cell: str) -> str:
    """A cell's text: tags dropped, entities decoded (the results page encodes some names twice), and spaces
    collapsed."""
    plain = re.sub(r"<[^>]+>", "", cell)
    while (decoded := html.unescape(plain)) != plain:
        plain = decoded
    return unicodedata.normalize("NFC", re.sub(r"\s+", " ", plain)).strip()


def folded(name: str) -> str:
    return re.sub(r"[\s.]+", " ", name.replace("’", "'")).strip().casefold()


def number(cell: str) -> int:
    if not re.fullmatch(r"\d{1,3}(,\d{3})*", cell):
        raise ValueError(f"not a count: {cell!r}")
    return int(cell.replace(",", ""))


def results() -> tuple[pl.DataFrame, pl.DataFrame]:
    """The candidate rows and district totals of the final count, from the results page's candidate table."""
    page = raw.cached(SOURCE, RESULTS).path.read_text(encoding="utf-8")
    if "Final count is complete." not in page:
        raise ValueError("the results page does not say the final count is complete")
    votes: list[dict[str, object]] = []
    totals: dict[str, dict[str, object]] = {}
    for district, row in re.findall(r'<tr[^>]*\bdata-ed="([^"]+)"[^>]*>(.*?)</tr>', page, re.DOTALL):
        district = text(district)
        cells = [text(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)]
        total = totals.setdefault(district, {"district_name": district})
        if len(cells) == 1 and cells[0].startswith("Status |"):
            total["status"] = cells[0].removeprefix("Status |").strip()
            continue
        if len(cells) != 5:
            raise ValueError(f"{district}: a row of {len(cells)} cells")
        label = {"Total Valid Votes": "valid", "Rejected Ballots*": "rejected", "Total Considered Votes": "considered"}
        if cells[1] in label:
            total[label[cells[1]]] = number(cells[3])
            continue
        if "data-canname" not in row:
            raise ValueError(f"{district}: an unlabeled row {cells}")
        votes.append(
            {"district_name": district, "ballot_name": cells[1], "affiliation": cells[2], "votes": number(cells[3])}
        )
    return pl.DataFrame(votes), pl.DataFrame(list(totals.values()))


def declared() -> pl.DataFrame:
    """The winners the Chief Electoral Officer declared at the close of the final count, from Elections BC's
    announcement."""
    page = raw.cached(SOURCE, DECLARED).path.read_text(encoding="utf-8")
    tables = {
        tuple(rows[0]): rows[1:]
        for table in re.findall(r"<table[^>]*>(.*?)</table>", page, re.DOTALL)
        if (
            rows := [
                [text(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)]
                for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.DOTALL)
            ]
        )
    }
    seats = {party: int(count) for party, count in tables[("Political Party", "Candidates Elected")]}
    if seats != SEATS:
        raise ValueError(f"the declaration's seats {seats} differ from {SEATS}")
    return pl.DataFrame(
        [
            {"district_name": r[0].rstrip("*").strip(), "winner": r[1], "winner_party": r[2]}
            for r in tables[("Electoral District", "Candidate Elected", "Political Party")]
        ]
    )


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    candidates = pl.read_csv(
        raw.cached(SOURCE, CANDIDATES).path, encoding="windows-1252", infer_schema_length=0, null_values=[""]
    ).select(
        district=pl.col("ED Code"),
        district_name=pl.col("Electoral District"),
        ballot_name=pl.col("Candidate Ballot Name").str.strip_chars(),
        affiliation=pl.col("Affiliation").str.strip_chars(),
    )
    candidates.write_parquet(PARSED / "candidates.parquet")

    votes, districts = results()
    codes = candidates.select("district", "district_name").unique()
    if codes.height != 93 or districts.height != 93:
        raise ValueError(f"{codes.height} districts in the candidate list, {districts.height} on the results page")
    districts = districts.join(codes, on="district_name", how="left")
    if (missing := districts.filter(pl.col("district").is_null())).height:
        raise ValueError(f"results page districts not in the candidate list: {missing['district_name'].to_list()}")
    if (unfinished := districts.filter(pl.col("status") != "Complete")).height:
        raise ValueError(f"districts not complete: {unfinished['district_name'].to_list()}")
    if (off := districts.filter(pl.col("valid") + pl.col("rejected") != pl.col("considered"))).height:
        raise ValueError(f"valid and rejected ballots do not add up: {off['district_name'].to_list()}")
    if districts["considered"].sum() != CONSIDERED:
        raise ValueError(f"{districts['considered'].sum():,} ballots considered, the page says {CONSIDERED:,}")

    # Every candidate on the results page is one candidate of the nomination list, matched within the district by
    # the ballot name after folding case, spaces, periods, and the curly apostrophe.
    listed = candidates.with_columns(key=pl.col("ballot_name").map_elements(folded, return_dtype=pl.String))
    votes = votes.with_columns(key=pl.col("ballot_name").map_elements(folded, return_dtype=pl.String)).rename(
        {"ballot_name": "page_name", "affiliation": "page_affiliation"}
    )
    matched = votes.join(listed, on=["district_name", "key"], how="full", coalesce=True)
    unmatched = matched.filter(pl.col("votes").is_null() | pl.col("district").is_null())
    if unmatched.height:
        raise ValueError(f"candidates that do not match the nomination list:\n{unmatched}")
    if matched.group_by("district_name", "key").len()["len"].max() != 1:
        raise ValueError("two candidates of one district share a folded ballot name")
    # The results page leaves the affiliation blank where the nomination list says "Other" (no affiliation).
    moved = matched.filter(
        pl.col("affiliation").fill_null("") != pl.col("page_affiliation").replace("", "Other").fill_null("Other")
    )
    if moved.height:
        raise ValueError(f"affiliations differ between the nomination list and the results page:\n{moved}")
    party = matched.group_by("affiliation").agg(pl.col("votes").sum())
    if dict(party.iter_rows()) != PARTY_VOTES:
        raise ValueError(f"votes by affiliation {dict(party.iter_rows())} differ from the page's {PARTY_VOTES}")
    sums = matched.group_by("district_name").agg(pl.col("votes").sum().alias("sum"))
    if (off := districts.join(sums, on="district_name").filter(pl.col("sum") != pl.col("valid"))).height:
        raise ValueError(f"candidate votes do not sum to the valid votes: {off['district_name'].to_list()}")

    winners = declared()
    if winners.height != 93 or winners["district_name"].n_unique() != 93:
        raise ValueError(f"{winners.height} declared winners")
    leaders = matched.filter(pl.col("votes") == pl.col("votes").max().over("district_name"))
    if leaders.height != 93:
        raise ValueError("a district has a tie for first place")
    leaders = leaders.join(winners, on="district_name", how="left")
    wrong = leaders.filter(
        pl.col("winner").is_null()
        | (pl.col("winner").map_elements(folded, return_dtype=pl.String) != pl.col("key"))
        | (pl.col("winner_party").str.to_lowercase() != pl.col("affiliation").str.to_lowercase())
    )
    if wrong.height:
        raise ValueError(f"declared winners who do not lead their district's final count:\n{wrong}")
    seats = dict(leaders.group_by("affiliation").len().iter_rows())
    if seats != SEATS:
        raise ValueError(f"seats {seats} differ from the declaration's {SEATS}")
    for district, (threshold, margin) in RECOUNTS.items():
        top = matched.filter(pl.col("district_name") == district)["votes"].sort(descending=True)
        considered = districts.filter(pl.col("district_name") == district)["considered"][0]
        if top[0] - top[1] != margin or considered // 500 != threshold:
            raise ValueError(f"{district}: margin {top[0] - top[1]}, threshold {considered // 500}")

    matched.join(
        leaders.select("district_name", "key", elected=pl.lit(True)), on=["district_name", "key"], how="left"
    ).select(
        "district", "district_name", "ballot_name", "affiliation", "votes", pl.col("elected").fill_null(False)
    ).write_parquet(PARSED / "votes.parquet")
    districts.select("district", "district_name", "valid", "rejected", "considered").write_parquet(
        PARSED / "districts.parquet"
    )
    print(
        f"{districts.height} districts, {matched.height} candidates, {districts['considered'].sum():,} ballots "
        f"considered; seats {seats}"
    )


def emit() -> Path:
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    votes = pl.read_parquet(PARSED / "votes.parquet")
    districts = {row["district"]: row for row in pl.read_parquet(PARSED / "districts.parquet").iter_rows(named=True)}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="2026 British Columbia general election: the final count in the 93 districts, with declared winners",
            polity="CA-BC",
        )

        def name(entity: UUID, text: str, kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind=kind, is_preferred=kind == "official")

        bc = cs.polity("CA-BC")
        province = cs.area("ca.province", "59")
        if {bc, province} & cs.minted:
            raise ValueError("run ca.elections first: British Columbia and its province area must exist")
        source = cs.source("Elections BC", "2026 Provincial General Election candidate list")
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher="Elections BC",
            title="2026 Provincial General Election candidate list",
            url=PAGE,
        )
        for url in (PAGE, CANDIDATES):
            cs.artifact(raw.cached(SOURCE, url), source)
        counted = cs.source("Elections BC", RESULTS_TITLE)
        cs.add("source", id=counted, kind="official_body", publisher="Elections BC", title=RESULTS_TITLE, url=RESULTS)
        for url in (RESULTS, DECLARED):
            cs.artifact(raw.cached(SOURCE, url), counted)
        cs.add("scheme", code="ca.bc", polity_id=bc, kind="electoral_district", rank=2, noun="electoral district")
        cs.add("boundary_set", scheme_code="ca.bc", vintage="2023", valid_from=FIRST, valid_to=None, as_of=FIRST)
        assembly = cs.key(f"{SOURCE}.body", "legislative_assembly")
        cs.add("body", id=assembly, polity_id=bc, kind="unicameral")
        name(assembly, "Legislative Assembly of British Columbia")
        cs.add("body_size", body_id=assembly, seats=93, valid_from=FIRST, valid_to=None)
        fptp = cs.key(f"{SOURCE}.electoral_system", "fptp")
        cs.add("electoral_system", id=fptp, polity_id=bc, rules={"formula": "fptp"}, rules_version=1)
        name(fptp, "First past the post")
        parties: dict[str, UUID] = {}
        for affiliation in sorted(set(candidates["affiliation"].drop_nulls()) - UNAFFILIATED):
            parties[affiliation] = cs.key(f"{SOURCE}.party", affiliation)
            cs.add("party", id=parties[affiliation], kind="party", polity_id=bc)
            name(parties[affiliation], affiliation)
        election = cs.key(f"{SOURCE}.election", str(DAY))
        cs.add("election", id=election, polity_id=bc, kind="general", date_from=DAY, date_to=DAY, status="held")
        name(election, "2026 British Columbia general election")
        cs.add("slug", path=f"ca/bc/{DAY}-general", entity_id=election)
        cs.add("note", entity_id=election, kind="context", body_md=NOTE)
        for note, body in conn.execute(
            """select n.id, n.body_md from note n join changeset c on c.id = n.changeset_id
               where n.entity_id = %s and n.kind = 'context' and n.body_md = %s and c.pipeline = %s""",
            [election, PENDING, SOURCE],
        ).fetchall():
            cs.add("note_drop", note_id=note, entity_id=election, body_md=body, reason="the final count is loaded")
        values: list[tuple[UUID, UUID, UUID | None, int, int]] = []
        counts = {(row["district"], row["ballot_name"]): row for row in votes.iter_rows(named=True)}
        for (code, district_name), rows in sorted(candidates.group_by("district", "district_name")):
            area = cs.area("ca.bc", code)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code="ca.bc")
                cs.add("area_code", area_id=area, code=code, valid_from=FIRST, valid_to=None)
                cs.add("area_parent", area_id=area, parent_id=province)
                name(area, district_name)
            contest = cs.key(f"{SOURCE}.contest", f"{DAY}:{code}")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=assembly,
                district_area_id=area,
                magnitude=1,
                round_number=1,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            name(contest, f"2026 election in {district_name}")
            cs.add("slug", path=f"ca/bc/{DAY}-general/{slug(district_name)}", entity_id=contest)
            unit = cs.unit("ca.bc", f"{DAY}:{code}", DAY)
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=bc,
                scheme_code="ca.bc",
                code=f"{DAY}:{code}",
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=DAY,
                valid_to=DAY + timedelta(days=1),
            )
            result_set = cs.key(f"{SOURCE}.result_set", f"{DAY}:{code}")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=counted,
                status="active",
                is_default=True,
                completeness=1,
            )
            if district_name in RECOUNTS:
                threshold, margin = RECOUNTS[district_name]
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=RECOUNT.format(margin=margin, threshold=threshold),
                )
            district = districts[code]
            for measure, value in ((3, district["considered"]), (4, district["valid"]), (5, district["rejected"])):
                values.append((result_set, unit, None, measure, value))
            for position, (ballot_name, affiliation) in enumerate(
                rows.select("ballot_name", "affiliation").iter_rows(), 1
            ):
                person = cs.key(f"{SOURCE}.person", f"{DAY}:{code}:{ballot_name}")
                if person in cs.minted:
                    cs.add("person", id=person)
                    name(person, ballot_name)
                option = cs.key(f"{SOURCE}.option", f"{DAY}:{code}:{ballot_name}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=parties.get(affiliation),
                    ballot_position=position,
                    ballot_label=ballot_name,
                )
                cs.add(
                    "candidacy",
                    id=cs.key(f"{SOURCE}.candidacy", f"{DAY}:{code}:{ballot_name}"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=ballot_name,
                    is_independent=affiliation == "Independent",
                )
                count = counts[(code, ballot_name)]
                values.append((result_set, unit, option, 1, count["votes"]))
                if count["elected"]:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
        cs.frame(
            "result_value",
            pl.DataFrame(
                {
                    "result_set_id": [str(v[0]) for v in values],
                    "unit_id": [str(v[1]) for v in values],
                    "option_id": [None if v[2] is None else str(v[2]) for v in values],
                    "channel": [0] * len(values),
                    "measure": [v[3] for v in values],
                    "count_round": [0] * len(values),
                    "flags": [0] * len(values),
                    "value": [v[4] for v in values],
                },
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "channel": pl.Int16,
                    "measure": pl.Int16,
                    "count_round": pl.Int16,
                    "flags": pl.Int16,
                    "value": pl.Int64,
                },
            ),
        )
        return cs.write()
