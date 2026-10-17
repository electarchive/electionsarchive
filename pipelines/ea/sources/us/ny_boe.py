import re
from datetime import date
from pathlib import Path
from uuid import UUID

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.ny_boe"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
ORIGIN = "www.elections.ny.gov/NYSBOE/elections/"
WAYBACK = "https://web.archive.org/web/{}id_/https://" + ORIGIN + "{}"
DAYS = {
    2006: date(2006, 11, 7),
    2010: date(2010, 11, 2),
    2014: date(2014, 11, 4),
    2018: date(2018, 11, 6),
    2022: date(2022, 11, 8),
}
PARTIES = {
    "Republican": "REP",
    "Democratic": "DEM",
    "Independence": "IND",
    "Conservative": "CON",
    "Working Families": "WOR",
    "Green": "GRN",
    "Libertarian": "LBT",
    "Rent Is Too High": "RDH",
    "Socialist Workers": "SWP",
    "GRE": "GRN",
}
LINES = {
    "DEM": ("democratic-party", "Democratic"),
    "REP": ("republican-party", "Republican"),
    "CON": ("NY:conservative-party", "Conservative"),
    "CON + SCC": ("NY:conservative-party", "Conservative and Stop Common Core"),
    "WOR": ("NY:working-families-party", "Working Families"),
    "IND": ("NY:independence-party", "Independence"),
    "IND + WEP": ("NY:independence-party", "Independence and Women's Equality"),
    "WEP": ("NY:women-s-equality-party", "Women's Equality"),
    "REF": ("NY:reform-party", "Reform"),
    "SAM": ("NY:serve-america-movement-party", "Serve America Movement"),
    "GRN": ("green-party", "Green"),
    "GRE": ("green-party", "Green"),
    "LBT": ("libertarian-party", "Libertarian"),
    "FDM": ("NY:freedom-party", "Freedom"),
    "SWP": ("socialist-workers-party", "Socialist Workers"),
    "RDH": ("NY:rent-is-2-damn-high", "Rent Is Too Damn High"),
    "APP": ("NY:anti-prohibition", "Anti-Prohibition"),
    "TXP": ("NY:taxpayers", "Taxpayers"),
    "SAP": (None, "Sapient"),
    "SCC": (None, "Stop Common Core"),
}
TITLES = {"governor": "Governor", "attorney-general": "Attorney General", "comptroller": "Comptroller"}
PUBLISHER = "New York State Board of Elections"
TITLE = "Certified statewide election returns by county"
NYC = (
    "The Board's 2006 file combines blank, void, and scattered votes in one column, so this set has no write-in option."
)
COUNTERS = {"Blank", "Void", "BVS Subtotal", "Total", "Total Votes by County", "Blk.,Void"}
FILES = {
    (2006, "governor"): ("20120822221216", "2006/general/2006_gov.xls"),
    (2006, "attorney-general"): ("20120822221201", "2006/general/2006_ag.xls"),
    (2006, "comptroller"): ("20120822221057", "2006/general/2006_comp.xls"),
    (2010, "governor"): ("20121004080554", "2010/general/2010GovernorRecertified09122012.xls"),
    (2010, "attorney-general"): ("20121004081144", "2010/general/2010AttorneyGeneralRecertified09122012.xls"),
    (2010, "comptroller"): ("20121004080544", "2010/general/2010ComptrollerRecertified09122012.xls"),
    (2014, "governor"): ("20150410011028", "2014/general/2014Governor.xlsx"),
    (2014, "attorney-general"): ("20160322054810", "2014/general/2014AttorneyGeneral.xlsx"),
    (2014, "comptroller"): ("20160322055002", "2014/general/2014Comptroller.xlsx"),
    (2018, "governor"): ("20200218182246", "2018/general/2018Governor.xlsx"),
    (2018, "attorney-general"): ("20200218182317", "2018/general/2018AttorneyGeneral.xlsx"),
    (2018, "comptroller"): ("20200218182313", "2018/general/2018Comptroller.xlsx"),
    (2022, "governor"): ("20230110220907", "2022/General/2022GovernorResults.xlsx"),
    (2022, "attorney-general"): ("20230110220932", "2022/General/2022AttorneyGenResults.xlsx"),
    (2022, "comptroller"): ("20230110220925", "2022/General/2022ComptrollerResults.xlsx"),
}


def fetch() -> None:
    for stamp, path in FILES.values():
        url = WAYBACK.format(stamp, path)
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            raw.fetch(SOURCE, url, suffix="." + path.rsplit(".", 1)[1])


def parse() -> None:
    rows: list[dict[str, object]] = []
    for (year, office), (stamp, path) in FILES.items():
        sheet = fastexcel.read_excel(raw.cached(SOURCE, WAYBACK.format(stamp, path)).path).load_sheet(
            0, header_row=None
        )
        originals = [[cell or "" for cell in row] for row in sheet.to_polars().cast(pl.String).iter_rows()]
        cells = [[re.sub(r"\s+", " ", cell).strip() for cell in row] for row in originals]
        top = next(n for n, row in enumerate(cells) if row[0] == "County")
        header = cells[top]
        columns: dict[int, tuple[str, str | None, str]] = {}
        for column in range(1, len(header)):
            text = header[column]
            if text in COUNTERS or text in ("County", ""):
                continue
            if text == "Scattering":
                columns[column] = ("Scattering", None, "")
            elif found := re.fullmatch(r"(.+?) ?\(([A-Z]+)\)", text):
                parts = re.split(r" and |/", found[1].replace("/ ", "/"), maxsplit=1)
                columns[column] = (parts[0].strip(), parts[1].strip() if len(parts) > 1 else None, found[2])
            elif text in PARTIES or cells[top - 1][column]:
                party = PARTIES[f"{cells[top - 1][column]} {text}".strip()]
                first, second = cells[top + 1][column], cells[top + 2][column]
                if second == "Scattering":
                    continue
                if office == "governor":
                    columns[column] = (first, None if second == "N/A" else second, party)
                else:
                    columns[column] = (f"{first} {second}", None, party)
            else:
                parts = re.split(r"\s{2,}", originals[top][column].strip())
                columns[column] = (
                    parts[0],
                    parts[1] if len(parts) > 1 else None,
                    PARTIES.get(cells[top + 1][column], cells[top + 1][column]),
                )
        printed: dict[int, int] = {}
        counted = 0
        for row in cells[top + 1 :]:
            label = row[0]
            if re.match(r"(?i)statewide total|total votes by party", label):
                printed = {column: int(float(row[column])) for column in columns if row[column]}
                break
            if not label or not row[1] or not re.fullmatch(r"[\d.]+", row[1]) or label.startswith("Total"):
                continue
            county = re.sub(r" County$", "", label)
            counted += 1
            for column, (head, mate, party) in columns.items():
                rows.append(
                    {
                        "year": year,
                        "office": office,
                        "head": head,
                        "mate": mate,
                        "party": party,
                        "county": county,
                        "votes": int(float(row[column] or 0)),
                    }
                )
        if counted != 62:
            raise ValueError(f"{year} {office}: {counted} counties")
        frame = pl.DataFrame([row for row in rows if row["year"] == year and row["office"] == office])
        for column, (head, _, party) in columns.items():
            total = int(frame.filter(pl.col("head") == head, pl.col("party") == party)["votes"].sum())
            if column in printed and printed[column] != total:
                raise ValueError(
                    f"{year} {office} {head} ({party}): counties sum to {total}, printed {printed[column]}"
                )
        if not printed:
            raise ValueError(f"{year} {office}: no statewide total")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows)
    frame.write_parquet(PARSED / "lines.parquet")
    print(
        frame.group_by("year", "office", "head", "party")
        .agg(pl.col("votes").sum())
        .sort("year", "office", "votes", descending=[False, False, True])
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet").with_columns(party=pl.col("party").replace({"GRE": "GRN"}))
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York's governor, attorney general, and comptroller elections 2006–2022 by county and "
            "ballot line, from the State Board of Elections",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def surname(text: str) -> str:
            return slug(re.sub(r",? (Jr|Sr|II|III|IV)\.?$", "", text)).split("-")[-1]

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://" + ORIGIN,
            license="Public record",
        )
        for stamp, path in FILES.values():
            cs.artifact(raw.cached(SOURCE, WAYBACK.format(stamp, path)), source)
        counties: dict[str, str] = {
            re.sub(r"[^a-z]", "", text.lower()): str(unit)
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '36%%'"""
            ).fetchall()
        }
        missing = sorted(
            {county for county in frame["county"] if re.sub(r"[^a-z]", "", county.lower()) not in counties}
        )
        if missing:
            raise ValueError(f"unmatched counties: {missing}")
        ((area,),) = conn.execute(
            """select u.area_id from reporting_unit u join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code = 'US-NY' where u.scheme_code = 'us.state'"""
        ).fetchall()
        parties = {code: cs.key("us.party", key) for code, (key, _) in LINES.items() if key is not None}
        fptp = cs.key("us.electoral_system", "fptp")
        if cs.minted & {fptp, *parties.values()}:
            raise ValueError("the parties and the fptp system must already exist")
        contests: dict[str, UUID] = dict(
            conn.execute(
                "select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':NY:(governor|attorney-general|comptroller)$'"
            ).fetchall()
        )
        values: list[pl.DataFrame] = []
        for (year, office), lines in frame.group_by("year", "office", maintain_order=True):
            day = DAYS[year]
            key = f"{day}:NY:{office}"
            result_set = cs.key(f"{SOURCE}.result_set", key)
            if result_set not in cs.minted:
                continue
            election = cs.key("us.election", f"{day}:NY")
            if election in cs.minted:
                raise ValueError(f"no election {day}:NY")
            contest = cs.key("us.contest", key)
            roots: list[tuple[UUID, str, str]] = []
            children: dict[tuple[UUID, UUID | None], UUID] = {}
            write_in = None
            if key in contests:
                for option, kind, label, parent, party in conn.execute(
                    """select id, kind, ballot_label, parent_option_id, party_id from contest_option
                       where contest_id = %s""",
                    [contest],
                ).fetchall():
                    if kind == "write_in" and parent is None:
                        write_in = option
                    elif parent is None and kind in ("candidate", "ticket"):
                        roots.append((option, label, surname(label)))
                    elif parent is not None:
                        children.setdefault((parent, party), option)
                for row in conn.execute(
                    """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness,
                         supersedes_id from result_set where contest_id = %s and is_default""",
                    [contest],
                ).fetchall():
                    cs.add(
                        "result_set",
                        **dict(
                            zip(
                                (
                                    "id",
                                    "election_id",
                                    "contest_id",
                                    "stage",
                                    "kind",
                                    "status",
                                    "source_id",
                                    "as_of",
                                    "completeness",
                                    "supersedes_id",
                                ),
                                row,
                                strict=True,
                            )
                        ),
                        is_default=False,
                    )
            else:
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=cs.key("us.office", f"{office}:NY"),
                    district_area_id=area,
                    term="full",
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=fptp,
                    default_result_kind="counted",
                )
                name(contest, f"{year} New York {TITLES[office]} election")
                cs.add("slug", path=f"us/ny/{day}-general/{office}", entity_id=contest)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            if year == 2006:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=NYC)
            totals: dict[UUID, int] = {}
            targets: dict[tuple[str, str], str] = {}
            for (head,), cells in lines.group_by("head", maintain_order=True):
                if head == "Scattering":
                    if write_in is None:
                        write_in = cs.key("us.option", f"{key}:write_in:scattered")
                        cs.add(
                            "contest_option",
                            id=write_in,
                            contest_id=contest,
                            kind="write_in",
                            ballot_label="Scattering",
                        )
                    targets[(head, "")] = str(write_in)
                    continue
                found = [option for option, _, last in roots if last == surname(head)]
                if len(found) > 1:
                    raise ValueError(f"{key}: {len(found)} options for {head}")
                codes = cells["party"].unique(maintain_order=True).to_list()
                if found:
                    root = found[0]
                    label = " ".join(next(text for option, text, _ in roots if option == root).split())
                else:
                    label = head
                    person = cs.key(f"{SOURCE}.person", f"NY:{slug(head)}")
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, head)
                    root = cs.key("us.option", f"{key}:{SOURCE}:{slug(head)}")
                    cs.add(
                        "contest_option",
                        id=root,
                        contest_id=contest,
                        kind="candidate",
                        party_id=parties.get(codes[0]) if len(codes) == 1 else None,
                        ballot_label=head,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{SOURCE}:{slug(head)}:head"),
                        person_id=person,
                        option_id=root,
                        role="head",
                        ballot_name=head,
                        is_independent=False,
                    )
                    roots.append((root, head, surname(head)))
                totals[root] = int(cells["votes"].sum())
                for code in codes:
                    if len(codes) == 1:
                        targets[(head, code)] = str(root)
                        continue
                    party = parties.get(code)
                    child = children.get((root, party)) if party is not None else None
                    if child is None:
                        child = cs.key("us.option", f"{key}:{SOURCE}:{slug(head)}:{slug(code)}")
                        cs.add(
                            "contest_option",
                            id=child,
                            contest_id=contest,
                            parent_option_id=root,
                            kind="candidate",
                            party_id=party,
                            ballot_label=f"{label} ({LINES[code][1]})",
                        )
                        children[(root, party)] = child
                    targets[(head, code)] = str(child)
            winner = max(totals, key=totals.__getitem__)
            cs.add("outcome", result_set_id=result_set, option_id=winner, kind="elected", seats=1)
            values.append(
                lines.select(
                    result_set_id=pl.lit(str(result_set)),
                    unit_id=pl.col("county").str.to_lowercase().str.replace_all(r"[^a-z]", "").replace_strict(counties),
                    option_id=pl.struct("head", "party").map_elements(
                        lambda row, targets=targets: targets[
                            (row["head"], row["party"] if row["head"] != "Scattering" else "")
                        ],
                        return_dtype=pl.String,
                    ),
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.lit(1, pl.Int16),
                    count_round=pl.lit(0, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    value=pl.col("votes"),
                )
            )
        if values:
            cs.frame("result_value", pl.concat(values))
        return cs.write()
