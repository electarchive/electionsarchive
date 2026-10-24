import io
import re
import unicodedata
from datetime import date
from pathlib import Path
from uuid import UUID

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.ie import dail_pdf
from ea.store import DATA, connect

SOURCE = "ie.dail"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DERIVED = 1
VOTES, REGISTERED, BALLOTS, VALID, INVALID, EXHAUSTED, QUOTA = 1, 2, 3, 4, 5, 21, 26
DATASETS = "https://opendata.housing.gov.ie/dataset"
ELECTIONS = {
    2011: (date(2011, 2, 25), 2009, date(2009, 2, 24), 166),
    2016: (date(2016, 2, 26), 2013, date(2013, 3, 20), 158),
    2020: (date(2020, 2, 8), 2017, date(2017, 12, 23), 160),
    2024: (date(2024, 11, 29), 2023, date(2023, 12, 19), 174),
}
ACTS = {
    2009: "Electoral (Amendment) Act 2009",
    2013: "Electoral (Amendment) (Dáil Constituencies) Act 2013",
    2017: "Electoral (Amendment) (Dáil Constituencies) Act 2017",
    2023: "Electoral (Amendment) Act 2023",
}
CSVS = {
    2016: {
        "counts": f"{DATASETS}/71dad45b-ce3c-4779-9eb2-775de1b290f6/resource/6bc45750-652d-4e82-b422-0bdcfc2a4c53/download/generalelection2016countdetails.csv",
        "units": f"{DATASETS}/2a474e8c-dd6e-40c2-878e-f3255ed1b0cf/resource/104441db-9b9d-4ef8-a118-99634cad335e/download/generalelection2016constituencydetails.csv",
        "people": f"{DATASETS}/35127ba1-eee8-48c5-b587-d6a37ba6b663/resource/2fd77c4f-14e1-4f8b-952c-cbd2f1d23cdf/download/generalelection2016candidatedetails.csv",
    },
    2020: {
        "counts": f"{DATASETS}/a9d1a550-fbdb-46ce-84ee-24ae9199c406/resource/b2161eee-9fcd-43f7-9407-5e4f2c279039/download/2020-05-27_general-election-2020-countdetails-csv_en.csv",
        "units": f"{DATASETS}/1cb33a8b-6dd3-4948-a08d-22569abd3b72/resource/48704c3a-ecd8-49e9-9d3b-58b2fba6ae65/download/2020-05-27_general-election-2020-constituency-details-csv_en.csv",
        "people": f"{DATASETS}/9c55ef5e-5c2f-4f7e-9d26-2adc37dc3077/resource/927a8c56-f32e-4d62-9ac4-3f5c91bb0b4c/download/2020-05-27_general-election-2020-candidate_details-csv_en.csv",
    },
}
PDFS = {
    2011: "https://data.oireachtas.ie/ie/oireachtas/electoralProcess/electionResults/dail/2011/2011-05-23_dail-general-election-2011-results_en.pdf",
}
XLSX_2024 = f"{DATASETS}/7ca52e7b-dd4d-4edd-b159-6ae111bbe538/resource/fb8a1f97-2683-4515-bac4-e2617222b20d/download/2024-dail-general-election-results.xlsx"
PAGES = {
    2016: "https://www.gov.ie/en/department-of-housing-local-government-and-heritage/publications/32nd-d%C3%A1il-general-election-february-2016-full-results-book/",
    2020: "https://www.gov.ie/en/department-of-housing-local-government-and-heritage/publications/33rd-d%C3%A1il-election-february-2020-election-results/",
    2024: "https://data.gov.ie/dataset/34th-dail-general-election-29-november-2024-election-results",
}
PARTY_CODES = {
    "Fianna Fáil": "FF",
    "Fine Gael": "FG",
    "Sinn Féin": "SF",
    "The Labour Party": "LAB",
    "Green Party/ Comhaontas Glas": "GRN",
    "Green Party/An Comhaontas Glas": "GRN",
    "Green Party/ An Comhaontas Glas": "GRN",
    "Social Democrats": "SD",
    "People Before Profit": "PBP",
    "People Before Profit–Solidarity": "PBP",
    "Aontú": "ANT",
    "Independent Ireland": "INDI",
    "Irish Freedom Party": "IFP",
    "Irish Freedom Party (I.F.P.)": "IFP",
    "The National Party - An Páirtí Náisiúnta": "TNP",
    "The National Party": "TNP",
    "Ireland First": "IF",
    "The Irish People": "TIP",
    "Independents 4 Change": "I4C",
}
NON_PARTY = "Non-Party"
STATUTORY = {(2020, "Kildare South"): 4}
CEANN_COMHAIRLE = {(2011, "Dún Laoghaire"): 4}
HYPHENS = {
    "Laois Offaly": "Laois-Offaly",
    "Roscommon - Galway": "Roscommon-Galway",
    "Dun Laoghaire": "Dún Laoghaire",
} | {
    f"{a} {b}": f"{a}-{b}"
    for a, b in (
        ("Carlow", "Kilkenny"),
        ("Cavan", "Monaghan"),
        ("Cork North", "Central"),
        ("Cork North", "West"),
        ("Cork South", "Central"),
        ("Cork South", "West"),
        ("Dublin Mid", "West"),
        ("Dublin North", "West"),
        ("Dublin South", "Central"),
        ("Dublin South", "West"),
        ("Longford", "Westmeath"),
        ("Roscommon", "Galway"),
        ("Sligo", "Leitrim"),
    )
}
METHODOLOGY = (
    "Dáil Éireann is elected by the single transferable vote in multi-member constituencies. The quota is the Droop "
    "quota: valid votes divided by one more than the number of seats to be filled, plus one, with any fraction "
    "ignored. A candidate who reaches the quota is elected, and their surplus passes to the next preference. A surplus "
    "from the first count is distributed from all of the candidate's ballots; a later surplus is distributed from the "
    "last parcel received. When no surplus is left to distribute, the lowest candidate is eliminated and their ballots "
    "are transferred. Ballots with no further usable preference become non-transferable. The last seats can be filled "
    "without the quota once the continuing candidates equal the seats left. The outgoing Ceann Comhairle is deemed "
    "elected for their constituency without a contest, so the count there fills one seat fewer."
)
CEANN = (
    "The outgoing Ceann Comhairle was deemed elected for this constituency without a contest. The count fills the "
    "other {contested} of its {total} seats, and the quota is the Droop quota for {contested} seats."
)
STATUTORY_NOTE = (
    "The source's constituency details print 3 seats in Kildare South for 2020; the Electoral (Amendment) (Dáil "
    "Constituencies) Act 2017 (schedule) gives it 4, and one was filled without a contest, as the 3 seats in the count show."
)
REPEATED = (
    "The results book prints the name {name} for two candidates in this constituency, one for each of two parties, "
    "with their own counts. Both rows are stored as printed, as separate candidates; the archive has not changed either name."
)
EXHAUSTED_NOTE = (
    "The source's CSV files give each candidate's running total at every count but not the non-transferable papers. "
    "The archive stores them as the valid poll minus the candidates' running totals."
)


def fold(text: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower())


def tidy(text: str) -> str:
    text = re.sub(r"\s+", " ", text.replace(",,", ",")).strip()
    if text != text.upper():
        return text

    def word(token: str) -> str:
        token = token.capitalize()
        if re.match(r"^Mc[a-z]{2,}", token):
            token = "Mc" + token[2:].capitalize()
        return token

    return re.sub(r"[^\s'’\-]+", lambda m: word(m.group()), text)


def read(url: str) -> pl.DataFrame:
    text = raw.cached(SOURCE, url).path.read_bytes().decode("cp1252")
    return pl.read_csv(io.BytesIO(text.encode()), infer_schema_length=0)


def fetch() -> None:
    for urls in CSVS.values():
        for url in urls.values():
            raw.fetch(SOURCE, url, suffix=".csv")
    raw.fetch(SOURCE, XLSX_2024, suffix=".xlsx")
    for url in PDFS.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def number(cell: object) -> int:
    return int(round(float(str(cell).replace(",", ""))))


def parse() -> None:
    units: list[dict[str, object]] = []
    candidates: list[dict[str, object]] = []
    counts: list[dict[str, object]] = []
    printed: list[dict[str, object]] = []

    for year, urls in CSVS.items():
        count_rows = read(urls["counts"])
        people = read(urls["people"])
        unit_rows = read(urls["units"])
        party_key = next(c for c in people.columns if c.strip().lower() in ("party", "party id"))
        gender_key = next(c for c in people.columns if c.lower().startswith("gender"))
        by_candidate = {(r["Constituency Number"], r["Candidate Id"]): r for r in people.iter_rows(named=True)}
        labels = {
            r["Constituency Number"]: HYPHENS.get(re.sub(r"\s+", " ", r["Constituency Name"]).strip(), re.sub(r"\s+", " ", r["Constituency Name"]).strip())
            for r in count_rows.iter_rows(named=True)
        }
        for row in unit_rows.iter_rows(named=True):
            label = labels[row["Constituency Number"]]
            seats_in = number(row["Seats in Constituency" if year == 2016 else "SeatsinConstit"])
            seats_in = STATUTORY.get((year, label), seats_in)
            contested = number(row["Number of Seats"])
            valid, poll, spoiled, quota = number(row["Valid Poll"]), number(row["Total Poll"]), number(row["Spoiled"]), number(row["Quota"])
            if poll != valid + spoiled or quota != valid // (contested + 1) + 1:
                raise ValueError(f"{year} {label}: poll {poll}, valid {valid}, spoiled {spoiled}, quota {quota} do not tie")
            units.append(
                {
                    "year": year,
                    "constituency": label,
                    "irish": row["Constituency Ainm"].strip(),
                    "seats": contested,
                    "total_seats": seats_in,
                    "electorate": number(row["Total Electorate"]),
                    "poll": poll,
                    "valid": valid,
                    "invalid": spoiled,
                    "quota": quota,
                    "rounds": number(row["Count Number"]),
                }
            )
        for number_, label in labels.items():
            group = count_rows.filter(pl.col("Constituency Number") == number_)
            for candidate_id in group["Candidate Id"].unique(maintain_order=True):
                lines = group.filter(pl.col("Candidate Id") == candidate_id).sort(pl.col("Count Number").cast(pl.Int32))
                person = by_candidate[(number_, candidate_id)]
                surname = tidy(lines["Candidate surname"][0])
                forename = tidy(lines["Candidate First Name"][0])
                result = lines["Result"].drop_nulls().unique().to_list()
                party = re.sub(r"\s+", " ", person[party_key]).strip()
                candidates.append(
                    {
                        "year": year,
                        "constituency": label,
                        "candidate": candidate_id,
                        "name": f"{forename} {surname}",
                        "ballot": f"{lines['Candidate surname'][0].strip()}, {lines['Candidate First Name'][0].strip()}",
                        "party": party,
                        "gender": person[gender_key],
                        "elected": result == ["Elected"],
                    }
                )
                for line in lines.iter_rows(named=True):
                    counts.append(
                        {
                            "year": year,
                            "constituency": label,
                            "candidate": candidate_id,
                            "count": number(line["Count Number"]),
                            "total": number(line["Total Votes"]),
                        }
                    )

    workbook = fastexcel.read_excel(str(raw.cached(SOURCE, XLSX_2024).path))
    sheets = workbook.sheet_names
    codes = {
        str(r[0]).strip(): re.sub(r"\s+", " ", str(r[1])).strip()
        for r in workbook.load_sheet_by_name("Party_Codes", header_row=None, dtype_coercion="coerce").to_polars().iter_rows()
        if r[0] not in (None, "")
    }
    stats = workbook.load_sheet_by_name("General Statistics 2024", header_row=None, dtype_coercion="coerce").to_polars()
    for stat in stats.iter_rows():
        label = (stat[0] or "").strip()
        if label not in sheets or label == "Totals":
            continue
        sheet = workbook.load_sheet_by_name(label, header_row=None, dtype_coercion="coerce").to_polars()
        rows = [list(r) for r in sheet.iter_rows()]
        header = next(i for i, r in enumerate(rows) if str(r[0]).startswith("NAMES OF CANDIDATES"))
        rounds = 0
        while rounds + 2 < len(rows[header]) and rows[header][rounds + 2] not in (None, "") and "Count" in str(rows[header][rounds + 2]):
            rounds += 1
        quota = number(next(r[27] for r in rows[:8] if r[25] and "Quota" in str(r[25])))
        cursor = header + 2
        elected = 0
        while not str(rows[cursor][0]).lower().startswith("non-transferable"):
            transfer, total = rows[cursor], rows[cursor + 1]
            match = re.match(r"^\*?\s*(?P<name>.+?)\s*\((?P<code>[^()]+)\)\s*$", str(transfer[0]).strip())
            if match is None:
                raise ValueError(f"2024 {label}: cannot read the candidate {transfer[0]!r}")
            surname, _, forename = re.sub(r"\s+", " ", match["name"].replace(",,", ",")).partition(",")
            code = match["code"].strip().upper()
            winner = str(transfer[39]).strip() == "Elected"
            elected += winner
            candidates.append(
                {
                    "year": 2024,
                    "constituency": label,
                    "candidate": str(cursor),
                    "name": f"{tidy(forename.strip())} {tidy(surname.strip())}",
                    "ballot": f"{surname.strip()}, {forename.strip()}",
                    "party": NON_PARTY if code == "NON-P" else codes[code],
                    "gender": transfer[1],
                    "elected": winner,
                }
            )
            for count in range(1, rounds + 1):
                counts.append(
                    {"year": 2024, "constituency": label, "candidate": str(cursor), "count": count, "total": number(total[1 + count])}
                )
            cursor += 2
        nontransferable = rows[cursor + 1]
        for count in range(1, rounds + 1):
            printed.append({"year": 2024, "constituency": label, "count": count, "value": number(nontransferable[1 + count])})
        valid = number(stat[7])
        if number(rows[cursor + 2][2]) != valid:
            raise ValueError(f"2024 {label}: printed totals differ from the valid poll")
        seats = elected
        units.append(
            {
                "year": 2024,
                "constituency": label,
                "irish": None,
                "seats": seats,
                "total_seats": number(stat[1]),
                "electorate": number(stat[2]),
                "poll": number(stat[3]),
                "valid": valid,
                "invalid": number(stat[5]),
                "quota": quota,
                "rounds": rounds,
            }
        )
        if number(stat[3]) != valid + number(stat[5]) or quota != valid // (seats + 1) + 1:
            raise ValueError(f"2024 {label}: poll, valid, invalid, or quota do not tie")

    for year, url in PDFS.items():
        book = dail_pdf.rows(str(raw.cached(SOURCE, url).path), year)
        for unit in book["units"]:
            unit["total_seats"] = CEANN_COMHAIRLE.get((year, unit["constituency"]), unit["total_seats"])
        units.extend(book["units"])
        candidates.extend(book["candidates"])
        counts.extend(book["counts"])
        printed.extend(book["printed"])

    unit_frame = pl.DataFrame(units)
    count_frame = pl.DataFrame(counts)
    cand_frame = pl.DataFrame(candidates, schema_overrides={"candidate": pl.String})
    count_frame = count_frame.with_columns(pl.col("candidate").cast(pl.String))
    for u in unit_frame.iter_rows(named=True):
        key = (pl.col("year") == u["year"]) & (pl.col("constituency") == u["constituency"])
        lines = count_frame.filter(key)
        winners = cand_frame.filter(key & pl.col("elected"))
        if len(winners) != u["seats"]:
            raise ValueError(f"{u['year']} {u['constituency']}: {len(winners)} elected for {u['seats']} seats")
        sums = lines.group_by("count").agg(pl.col("total").sum()).sort("count")
        first = lines.filter(pl.col("count") == 1)["total"].sum()
        if first != u["valid"]:
            raise ValueError(f"{u['year']} {u['constituency']}: first preferences {first} != valid poll {u['valid']}")
        rest = [u["valid"] - s for s in sums["total"].to_list()]
        if any(b < a or a < 0 for a, b in zip(rest, rest[1:])) or rest[0] != 0:
            raise ValueError(f"{u['year']} {u['constituency']}: non-transferable papers {rest} are not cumulative")
        for row in printed:
            if row["year"] == u["year"] and row["constituency"] == u["constituency"] and row["value"] != rest[row["count"] - 1]:
                raise ValueError(f"{u['year']} {u['constituency']}: printed non-transferable differs at count {row['count']}")
        for winner in winners.iter_rows(named=True):
            series = lines.filter(pl.col("candidate") == winner["candidate"]).sort("count")["total"].to_list()
            if max(series) < u["quota"] and series[-1] == 0:
                raise ValueError(f"{u['year']} {u['constituency']}: {winner['name']} elected below the quota and excluded")
    PARSED.mkdir(parents=True, exist_ok=True)
    unit_frame.write_parquet(PARSED / "units.parquet")
    cand_frame.write_parquet(PARSED / "candidates.parquet")
    count_frame.write_parquet(PARSED / "counts.parquet")
    pl.DataFrame(printed, schema={"year": pl.Int32, "constituency": pl.String, "count": pl.Int32, "value": pl.Int64}).write_parquet(
        PARSED / "printed.parquet"
    )


def emit() -> Path:
    units = pl.read_parquet(PARSED / "units.parquet")
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    counts = pl.read_parquet(PARSED / "counts.parquet")
    printed = pl.read_parquet(PARSED / "printed.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Dáil Éireann general elections 2011, 2016, 2020, and 2024 by constituency, with every STV count, from the Oireachtas results book and the Department of Housing's open data",
            polity="IE",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official" and lang == "en")

        ireland = cs.polity("IE")
        nation = cs.area("ie.nation", "IE")
        if nation in cs.minted:
            raise ValueError("publish ie.returning_officer before the Dáil")
        if conn.execute("select 1 from scheme where code = 'ie.dail'").fetchone() is None:
            cs.add("scheme", code="ie.dail", polity_id=ireland, kind="electoral_district", rank=1, noun="Dáil constituency")
        system = cs.key("ie.electoral_system", "dail-eireann")
        cs.add(
            "electoral_system",
            id=system,
            polity_id=ireland,
            rules={"formula": "stv", "quota": "droop", "surplus": "last_parcel"},
            rules_version=1,
        )
        name(system, "Dáil Éireann elections")
        cs.add("note", entity_id=system, kind="methodology", body_md=METHODOLOGY)
        body = cs.key("ie.body", "dail-eireann")
        cs.add("body", id=body, polity_id=ireland, kind="lower_house")
        name(body, "Dáil Éireann")
        name(body, "Dáil Éireann", "ga", "native")
        days = sorted(d for d, *_ in ELECTIONS.values())
        for (day, _, _, seats), after in zip(ELECTIONS.values(), [*days[1:], None], strict=True):
            cs.add(
                "body_size",
                body_id=body,
                seats=seats,
                valid_from=day,
                valid_from_precision="day",
                valid_to=after,
                valid_to_precision="day",
            )

        known: dict[str, list[UUID]] = {}
        for text, party in conn.execute(
            "select n.text, p.id from party p join polity y on y.id = p.polity_id"
            " join name n on n.entity_id = p.id where y.iso_code = 'IE'"
        ).fetchall():
            known.setdefault(fold(text), []).append(party)
        strays = {
            str(value): entity
            for value, entity in conn.execute("select value, entity_id from external_id where scheme = 'ie.dail.party'").fetchall()
        }
        parties: dict[str, UUID | None] = {}

        def party_id(label: str) -> UUID | None:
            if label == NON_PARTY or not label:
                return None
            if label not in parties:
                code = PARTY_CODES.get(label)
                if code is not None:
                    parties[label] = cs.key("ie.party", code)
                    if (earlier := strays.get(slug(label))) is not None and earlier != parties[label]:
                        cs.merge(earlier, parties[label])
                elif len(known.get(fold(label), ())) == 1:
                    parties[label] = known[fold(label)][0]
                else:
                    parties[label] = cs.key("ie.dail.party", slug(label))
                    if parties[label] in cs.minted:
                        cs.add("party", id=parties[label], kind="party", polity_id=ireland)
                        name(parties[label], label)
            return parties[label]

        done_areas: set[UUID] = set()
        for year, (day, act, signed, _) in ELECTIONS.items():
            title = f"Dáil general election {year}: constituency results and transfers of votes"
            if year in PDFS:
                publisher = "Houses of the Oireachtas"
                source = cs.source(publisher, title)
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=PDFS[year],
                    license="Oireachtas (Open Data) PSI Licence, CC BY 4.0 terms",
                    license_url="https://creativecommons.org/licenses/by/4.0/",
                    attribution=(
                        "Election results and transfer of votes, compiled from returning officers' public notices "
                        "(data.oireachtas.ie, Oireachtas (Open Data) PSI Licence with CC BY 4.0 terms)."
                    ),
                )
                cs.artifact(raw.cached(SOURCE, PDFS[year]), source)
            else:
                source = cs.source("Department of Housing, Local Government and Heritage", title)
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher="Department of Housing, Local Government and Heritage",
                    title=title,
                    url=PAGES[year],
                    license="CC BY-SA 4.0",
                    license_url="https://creativecommons.org/licenses/by-sa/4.0/",
                    attribution=(
                        "Election results and transfer of votes, compiled by the Department of Housing, Local Government "
                        "and Heritage from returning officers' public notices (opendata.housing.gov.ie, CC BY-SA 4.0)."
                    ),
                )
                for url in [*CSVS[year].values()] if year in CSVS else [XLSX_2024]:
                    cs.artifact(raw.cached(SOURCE, url), source)

            election = cs.key("ie.election", f"{day}:dail-eireann")
            cs.add("election", id=election, polity_id=ireland, kind="general", date_from=day, date_to=day, status="held")
            name(election, f"{year} Irish general election")
            path = f"ie/{day}-general"
            cs.add("slug", path=path, entity_id=election)
            for unit_row in units.filter(pl.col("year") == year).sort("constituency").iter_rows(named=True):
                label = unit_row["constituency"]
                code = f"{act}:{slug(label)}"
                area = cs.area("ie.dail", code)
                if area not in done_areas:
                    done_areas.add(area)
                    cs.add("area", id=area, scheme_code="ie.dail")
                    cs.add("area_code", area_id=area, code=code, valid_from=signed)
                    cs.add("area_parent", area_id=area, parent_id=nation, valid_from=None)
                    name(area, label)
                    if unit_row["irish"]:
                        name(area, unit_row["irish"], "ga", "native")
                unit = cs.unit("ie.dail", code)
                if unit in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=ireland,
                        scheme_code="ie.dail",
                        code=code,
                        area_relation="equals",
                        area_id=area,
                        kind="standard",
                        valid_from=None,
                    )
                    name(unit, label)
                contest_key = f"{day}:dail-eireann:{slug(label)}"
                contest = cs.key("ie.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=body,
                    district_area_id=area,
                    magnitude=unit_row["seats"],
                    round_number=1,
                    electoral_system_id=system,
                    default_result_kind="counted",
                )
                name(contest, f"{year} Irish general election in {label}")
                cs.add("slug", path=f"{path}/{slug(label)}", entity_id=contest)
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
                if unit_row["total_seats"] != unit_row["seats"]:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md=CEANN.format(contested=unit_row["seats"], total=unit_row["total_seats"]),
                    )
                if (year, label) in STATUTORY:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=STATUTORY_NOTE)
                if year in CSVS:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=EXHAUSTED_NOTE)
                for measure, amount in (
                    (REGISTERED, unit_row["electorate"]),
                    (BALLOTS, unit_row["poll"]),
                    (VALID, unit_row["valid"]),
                    (INVALID, unit_row["invalid"]),
                    (QUOTA, unit_row["quota"]),
                ):
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=amount,
                    )
                key = (pl.col("year") == year) & (pl.col("constituency") == label)
                lines = counts.filter(key)
                running = dict.fromkeys(range(1, unit_row["rounds"] + 1), 0)
                field = candidates.filter(key)
                repeated = set(field.filter(pl.col("name").is_duplicated())["name"])
                for repeated_name in repeated:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=REPEATED.format(name=repeated_name))
                for candidate in field.iter_rows(named=True):
                    series = lines.filter(pl.col("candidate") == candidate["candidate"]).sort("count")
                    owner = party_id(candidate["party"])
                    suffix = f":{candidate['candidate']}" if candidate["name"] in repeated else ""
                    option_key = f"{contest_key}:{fold(candidate['name'])}{suffix}"
                    option = cs.key("ie.option", option_key)
                    person = cs.key(f"{SOURCE}.person", f"{year}:{slug(label)}:{fold(candidate['name'])}{suffix}")
                    cs.add("person", id=person, gender={"M": "male", "F": "female"}.get(str(candidate["gender"]).strip()[:1].upper()))
                    name(person, candidate["name"])
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=owner,
                        ballot_label=candidate["name"],
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("ie.candidacy", option_key),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=candidate["ballot"],
                        member_of_party_id=owner,
                        is_independent=owner is None,
                    )
                    stopped = False
                    final = 0
                    for count, total in series.select("count", "total").iter_rows():
                        running[count] += total
                        final = total
                        if stopped:
                            continue
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=VOTES,
                            count_round=count,
                            flags=0,
                            value=total,
                        )
                        stopped = total == 0 and count > 1 and not candidate["elected"]
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="elected" if candidate["elected"] else ("lost" if final > 0 else "eliminated"),
                    )
                shown = printed.filter(key)
                for count, total in running.items():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=EXHAUSTED,
                        count_round=count,
                        flags=0 if len(shown) else DERIVED,
                        value=unit_row["valid"] - total,
                    )
        return cs.write()
