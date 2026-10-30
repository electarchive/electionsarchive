import html
import re
from datetime import date
from itertools import pairwise
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.gb.ni_assembly import extract, fold, number
from ea.sources.gb.ni_assembly import party_of as assembly_party
from ea.sources.gb.ni_council import display
from ea.store import DATA, connect

SOURCE = "gb.ni_bodies"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
ARK = "https://web.archive.org/web/2020if_/https://www.ark.ac.uk/elections"
FILES = {1973: "xls/asby/1973.rar", 1975: "xls/conv.rar", 1982: "xls/asby/1982.rar", 1996: "xls/1996.rar"}
PAGES = {1973: "fa73.htm", 1975: "fc75.htm", 1982: "fa82.htm", 1996: "ff96.htm"}
UUP, DUP, VUP = "Ulster Unionist Party", "Democratic Unionist Party", "Vanguard"
WIKIPEDIA = "https://en.wikipedia.org/w/index.php?title={}&action=raw"
MEMBERS = {
    1973: "List_of_members_of_the_1973_Northern_Ireland_Assembly",
    1975: "List_of_members_of_the_Northern_Ireland_Constitutional_Convention",
    1982: "List_of_members_of_the_1982_Northern_Ireland_Assembly",
}
WIKIPEDIA_PARTIES = {
    "Vanguard Unionist Progressive Party": VUP,
    "Alliance Party of Northern Ireland": "Alliance Party",
    "Ulster Popular Unionist Party": "Ulster Popular Unionist",
    "Independent Unionist": None,
}
BODIES = {
    1973: (
        "ni-assembly-1973",
        "Northern Ireland Assembly (1973)",
        date(1975, 3, 28),
        "Northern Ireland Assembly election",
        "general",
    ),
    1975: (
        "ni-convention",
        "Northern Ireland Constitutional Convention",
        date(1976, 3, 6),
        "Northern Ireland Constitutional Convention election",
        "convention",
    ),
    1982: (
        "ni-assembly-1982",
        "Northern Ireland Assembly (1982)",
        date(1986, 6, 23),
        "Northern Ireland Assembly election",
        "general",
    ),
}
NAMES = {
    "Bradford, Rev": "Roy Bradford",
    "Glass, John BC": "John B. C. Glass",
    "Smith, WilliamWoolsey": "William Woolsey Smith",
    "Gault, William, HS": "William H. S. Gault",
    "McMAhon, Mary": "Mary McMahon",
}
FORUM = {
    "Belfast East": "96eb",
    "Belfast North": "96nb",
    "Belfast South": "96sb",
    "Belfast West": "96wb",
    "East Antrim": "96ea",
    "North Antrim": "96na",
    "South Antrim": "96sa",
    "North Down": "96nd",
    "South Down": "96sd",
    "Fermanagh and South Tyrone": "96fst",
    "Foyle": "96foy",
    "Lagan Valley": "96lv",
    "East Londonderry": "96el",
    "Mid Ulster": "96mu",
    "Newry and Armagh": "96nanda",
    "Strangford": "96str",
    "West Tyrone": "96wt",
    "Upper Bann": "96ub",
}
FORUM_PARTIES = {
    "independent democratic unionist party": "Independent Democratic Unionist Party",
    "independent chambers": "Independent Chambers",
    "independent mcmullan": "Independent McMullan",
    "independent templeton": "Independent Templeton",
    "arthur templeton independent": "Independent Templeton",
    "ulster's independent voice": "Ulster's Independent Voice",
    "democratic partnership": "Democratic Partnership",
    "democratic left": "Democratic Left",
    "communist party of ireland": "Communist Party of Ireland",
    "communist party": "Communist Party of Ireland",
    "ulster christian democrats": "Ulster Christian Democrats",
    "ulster christian democratic party": "Ulster Christian Democrats",
    "ulster independence movement": "Ulster Independence",
}
VOTES, REGISTERED, BALLOTS, VALID, INVALID, EXHAUSTED, QUOTA = 1, 2, 3, 4, 5, 21, 26
DAYS = {1973: date(1973, 6, 28), 1975: date(1975, 5, 1), 1982: date(1982, 10, 20), 1996: date(1996, 5, 30)}
FACTS = {
    (1975, "Armagh"): {"electorate": 90640.0, "polled": 61399.0, "invalid": 2011.0, "seats": 7.0},
    (1982, "Belfast South"): {"invalid": 1243.0},
}
LABELS = {
    "offun": UUP,
    "offfun": UUP,
    "un": UUP,
    "unantiwpaper": UUP,
    "vuloycoal": VUP,
    "vanun": VUP,
    "duloycoal": DUP,
    "demun": DUP,
    "allparty": "Alliance Party",
    "sdlp": "Social Democratic and Labour Party",
    "sldp": "Social Democratic and Labour Party",
    "nilp": "Northern Ireland Labour Party",
    "repclubs": "Republican Clubs",
    "repc": "Republican Clubs",
    "replabparty": "Republican Labour",
    "natparty": "Nationalist Party",
    "lib": "Ulster Liberal",
    "natfrontloy": "National Front",
    "commparty": "Communist Party of Ireland",
    "cpi": "Communist Party of Ireland",
    "cp": "Communist Party of Ireland",
    "comartyofireland": "Communist Party of Ireland",
    "unvwbelfastloycoal": "West Belfast Loyalist Coalition",
    "unwbelfastloycoal": "West Belfast Loyalist Coalition",
    "unindploycoal": "West Belfast Loyalist Coalition",
    "uuudup": DUP,
    "dup": DUP,
    "uuuvup": VUP,
    "uuuuvp": VUP,
    "uuuoup": UUP,
    "uuoup": UUP,
    "oup": UUP,
    "ap": "Alliance Party",
    "upni": "Unionist Party of Northern Ireland",
    "uuup": "United Ulster Unionist",
    "unitedulsterunionist": "United Ulster Unionist",
    "memberofparliament": "United Ulster Unionist",
    "democraticuniondup": DUP,
    "democraticunionistdup": DUP,
    "workersparty": "Workers' Party",
    "theworkersparty": "Workers' Party",
    "wp": "Workers' Party",
    "allianceparty": "Alliance Party",
    "alliance": "Alliance Party",
    "sinnfein": "Sinn Féin",
    "ou": UUP,
    "ouup": "United Ulster Unionist",
    "offulsterunionist": UUP,
    "officialulsterunionist": UUP,
    "theulsterunionistparty": UUP,
    "ulsterunionist": UUP,
    "vanguardunionist": VUP,
    "ulloyalistdemparty": "Ulster Democratic",
    "ecologist": "Ecology Party",
    "ep": "Ecology Party",
    "peoplesdemocracy": "People's Democracy",
    "prounionist": "Progressive Unionist Party",
    "upup": "Ulster Popular Unionist",
}
INDEPENDENT = {
    "indp",
    "indpnat",
    "indploy",
    "indpprowpaper",
    "loy",
    "ulsterconloy",
    "ind",
    "indu",
    "indloy",
    "uuuc",
    "independent",
    "unionist",
    "i",
    "ui",
    "uu",
    "nlp",
    "onehonestmansteadfast",
    "thepeacestate",
    "",
}
LOSERS_1975 = {
    "Robinson, Peter": DUP,
    "Calvert, David N": DUP,
    "McNeice, Robert J": DUP,
    "Goligher, Edith R": DUP,
    "Smith, James": DUP,
    "Herron, David S.": DUP,
    "Lavery, Bill": VUP,
    "Jordan, Raymond": VUP,
    "Crawford, Randall": VUP,
    "Erwin, Adam H.": UUP,
    "Oliver, Neil C": UUP,
    "McNarry, David M.": UUP,
    "Williamson, John": UUP,
    "Rainey, William A": UUP,
    "Logan, John S": UUP,
    "Semple, Samuel": UUP,
}


SURNAMES = {
    "Louge, Hugh": "Logue",
    "Turnly, John F": "Turnley",
    "Brookebrough, Jonh W": "Brooke",
    "Dunleath, Charles E. H.J": "Mulholland",
    "Dunleath, Charles EHJ": "Mulholland",
    "O'Donaghue, Paddy": "O'Donoghue",
    "Dunleath, C. E. H. J.": "Mulholland",
}
UNLISTED = {
    (1973, "South Antrim", "Crothers, D. S. F."): ("South Antrim", "kinahan"),
    (1982, "Armagh", "McAllister, James"): ("Armagh", "allister"),
    (1973, "North Antrim", "McCarthy, D."): ("North Antrim", "smyth"),
    (1982, "Armagh", "Mallon, Seamus"): ("Armagh", "speers"),
    (1982, "Belfast South", "Graham, Edgar S D"): ("Belfast South", "jr"),
    (1982, "South Down", "McCullough, Raymond"): ("South Down", "donaldson"),
}


def squash(label: str) -> str:
    return re.sub(r"[^a-z]", "", label.lower().replace("é", "e"))


def party_of(year: int, name: str, label: str) -> str | None:
    key = squash(label)
    if year == 1975 and (key.startswith("uu") or key == "dup"):
        return LOSERS_1975[name]
    if key in INDEPENDENT or (year == 1975 and key == "un"):
        return None
    return LABELS[key]


def fetch() -> None:
    for year, path in FILES.items():
        raw.fetch(SOURCE, f"{ARK}/{path}", suffix=".rar")
        raw.fetch(SOURCE, f"{ARK}/{PAGES[year]}", suffix=".html")
    for title in MEMBERS.values():
        raw.fetch(SOURCE, WIKIPEDIA.format(title), suffix=".txt")
    for page in [*FORUM.values(), "96cands"]:
        raw.fetch(SOURCE, f"{ARK}/{page}.htm", suffix=".html")


def forum_lists(page: str) -> list[tuple[str, float, list[tuple[str, bool]]]]:
    text = raw.cached(SOURCE, f"{ARK}/{page}.htm").path.read_text(encoding="cp1252", errors="replace")
    text = re.sub(r"(?is)<!--.*?-->|<(script|style)\b.*?</\1>", " ", text)
    text = re.sub(r"(?i)<b\b[^>]*>", "\x01", text)
    text = re.sub(r"(?i)</b>", "\x02", text)
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text)))
    start = text.index("elected by their parties." if page == "96cands" else "spreadsheet )") + 1
    text = text[text.index(" ", start) : text.index("Electorate:" if page != "96cands" else "Full list of 1996", start)]
    state = []
    bold = False
    for char in text:
        bold = True if char == "\x01" else False if char == "\x02" else bold
        state.append(bold)
    headers = list(re.finditer(r"([A-Z][^:\x01\x02,]*?):\s*\x02?\s*(\d[\d,\s]*?)\s*\(\s*[\d.]+\s*%\)\s*\x02?:?", text))
    found = []
    for header, after in zip(headers, [*headers[1:], None], strict=True):
        names = []
        start = header.end()
        for chunk in text[start : after.start() if after else len(text)].split(","):
            name_ = re.sub(r"\s+", " ", re.sub(r"[\x01\x02]", " ", chunk)).strip()
            if name_:
                offset = start + chunk.index(name_[0])
                names.append((name_, any(state[offset : start + len(chunk)]) and state[offset + len(name_) // 2]))
            start += len(chunk) + 1
        found.append((header.group(1).strip(), float(re.sub(r"\D", "", header.group(2))), names))
    return found


def forum_party(label: str) -> str | None:
    key = re.sub(r"\s+", " ", label.strip().lower())
    key = re.sub(r"^the ", "", key)
    if key in FORUM_PARTIES:
        return FORUM_PARTIES[key]
    return assembly_party(label)


def parse_forum() -> None:
    folder = PARSED / "x1996"
    extract(raw.cached(SOURCE, f"{ARK}/{FILES[1996]}").path, folder)
    units = []
    rows = []
    for constituency, page in FORUM.items():
        sheet = next(folder.rglob(f"fo96-{constituency.replace(' ', '-')}.xls"))
        cells = pl.read_excel(sheet, has_header=False, infer_schema_length=0).rows()
        seats = {}
        labels = {}
        facts = {}
        for row in cells:
            if row[0] is not None and row[0].strip().isdigit() and row[2] is not None and row[2].strip().isdigit():
                seats[float(row[2])] = 0
                labels[float(row[2])] = row[1].strip()
            label = (row[2] or "").lower()
            for key in ("polled", "electorate", "invalid", "valid"):
                if re.search(rf"\b{key}", label) and key not in facts and row[3] is not None:
                    facts[key] = float(row[3])
        if len(seats) != len(labels) or facts["polled"] - facts["invalid"] != facts["valid"]:
            raise ValueError(f"{constituency}: spreadsheet totals do not add up")
        listed = forum_lists(page)
        spare = [votes for votes in seats if votes not in {votes for _, votes, _ in listed}]
        lists = []
        added = []
        for label, votes, names in listed:
            if votes not in seats:
                match = [other for other in spare if forum_party(labels[other]) == forum_party(label)]
                if len(match) == 1:
                    votes = match[0]
                    spare.remove(votes)
                else:
                    seats[votes] = 0
                    added.append((label, votes))
            lists.append((label, votes, names))
        lists += [(labels[votes], votes, []) for votes in spare]
        if sum(votes for _, votes, _ in lists) != facts["valid"]:
            raise ValueError(f"{constituency}: lists do not add up to the valid votes")
        for _ in range(5):
            seats[max(seats, key=lambda votes: votes / (seats[votes] + 1))] += 1
        for label, votes, names in lists:
            if sum(bold for _, bold in names) != seats[votes]:
                raise ValueError(
                    f"{constituency} {label}: {sum(bold for _, bold in names)} bold names, {seats[votes]} seats"
                )
            for position, (person, bold) in enumerate(names or [(None, False)], start=1):
                rows.append(
                    {
                        "constituency": constituency,
                        "label": label,
                        "party": forum_party(label),
                        "votes": votes,
                        "seats": seats[votes],
                        "position": position,
                        "name": person,
                        "elected": bold,
                    }
                )
        units.append(
            {"constituency": constituency, **facts, "added": [f"{label}|{votes:.0f}" for label, votes in added]}
        )
    for label, votes, names in forum_lists("96cands"):
        won = sum(bold for _, bold in names)
        if won not in (0, 2):
            raise ValueError(f"regional {label}: {won} top-up members")
        for position, (person, bold) in enumerate(names, start=1):
            rows.append(
                {
                    "constituency": None,
                    "label": label,
                    "party": forum_party(label),
                    "votes": votes,
                    "seats": won,
                    "position": position,
                    "name": person,
                    "elected": bold,
                }
            )
    lists_frame = pl.DataFrame(rows)
    regional = lists_frame.filter(pl.col("constituency").is_null() & pl.col("elected"))
    if regional.height != 20:
        raise ValueError(f"{regional.height} top-up members")
    summed = (
        lists_frame.filter(pl.col("constituency").is_not_null() & (pl.col("position") == 1))
        .group_by("party")
        .agg(pl.col("votes").sum(), pl.col("seats").sum())
    )
    with pl.Config(tbl_rows=-1):
        print(summed.sort("votes", descending=True))
    print(regional.select("label", "name"))
    pl.DataFrame(units).write_parquet(PARSED / "forum_units.parquet")
    lists_frame.write_parquet(PARSED / "forum_lists.parquet")


def members(year: int) -> dict[tuple[str, str], list[tuple[str, str | None]]]:
    text = raw.cached(SOURCE, WIKIPEDIA.format(MEMBERS[year])).path.read_text(encoding="utf-8")
    found: dict[tuple[str, str], list[tuple[str, str | None]]] = {}
    place = name = None
    styled = False
    for line in text.split("==Members by constituency==", 1)[1].split("|}", 1)[0].splitlines():
        cell = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]+)\]\]", r"\1", line.partition("|")[2]).strip(" *♭")
        if line.startswith("|rowspan"):
            place = cell.partition("|")[2].strip()
        elif line.startswith('! style="background'):
            styled = True
        elif line.startswith("|") and not line.startswith("|-") and styled:
            found.setdefault((place, re.sub(r"[^a-z]", "", name.split()[-1].lower())), []).append(
                (name, WIKIPEDIA_PARTIES.get(cell, cell))
            )
            styled = False
        elif line.startswith("|[["):
            name = cell
    return found


def stv_sheet(
    year: int, path
) -> tuple[dict[str, float], list[dict[str, object]], dict[int, float], list[tuple[int, str, float, float]]]:
    rows = [list(row) for row in pl.read_excel(path, has_header=False, infer_schema_length=0).iter_rows()]
    labels = [(row[3] or "").strip().lower() for row in rows]
    non_transferable = next(index for index, text in enumerate(labels) if text.startswith("non"))
    totals = next(index for index, text in enumerate(labels) if text in ("total", "totals"))

    def cell(row: list[str | None], column: int) -> str | None:
        return row[column] if column < len(row) else None

    picked = [
        index for index in range(5, len(rows[totals])) if re.fullmatch(r"\s*\d+\s*", cell(rows[totals], index) or "")
    ]
    result = {1: 4} | {stage: index for stage, index in enumerate(picked, start=2)}
    stages = sorted(
        stage
        for stage, column in result.items()
        if any(cell(row, column) not in (None, "") for row in rows[2:non_transferable])
    )
    candidates = []
    for row in rows[2:non_transferable]:
        if row[2] is None or not (row[1] or "").strip().isdigit():
            continue
        candidates.append(
            {
                "number": int(row[1]),
                "name": re.sub(r"\s+", " ", row[2]).strip(),
                "party": re.sub(r"\s+", " ", (row[3] or "").strip()),
                "flag": (row[0] or "").strip().upper() == "E",
                "votes": {stage: number((cell(row, result[stage]) or "").replace("\xad", "")) for stage in stages},
            }
        )
    facts: dict[str, float] = {}
    for row in rows[totals + 1 :]:
        position = next(
            (index for index in (3, 2, 4) if row[index] is not None and not re.match(r"[\d-]", row[index])), None
        )
        if position is None:
            continue
        label = row[position].strip().lower()
        value = next((text.strip().replace(",", "") for text in row[position + 1 :] if text is not None), "")
        if not re.fullmatch(r"[\d.]+", value):
            continue
        if re.fullmatch(r"\d{1,3}\.\d{3}", value):
            value = value.replace(".", "")
        for key, word in (
            ("quota", "quota"),
            ("invalid", "invalid"),
            ("valid", "valid"),
            ("polled", "polled"),
            ("seats", "elected"),
            ("electorate", "elector"),
        ):
            if word in label:
                facts.setdefault(key, float(value))
                break
    exhausted = {stage: number(cell(rows[non_transferable], result[stage])) for stage in stages}
    total = number(cell(rows[totals], 4))
    if abs(sum(candidate["votes"][1] for candidate in candidates) - total) > 1:
        raise ValueError(f"{path.name}: first preferences do not sum to {total}")
    repairs = []
    for before, stage in pairwise(stages):
        counted = sum(candidate["votes"][stage] for candidate in candidates) + exhausted[stage]
        if abs(counted - total) <= 1:
            continue
        wrong = []
        for candidate, row in zip(
            candidates,
            (row for row in rows[2:non_transferable] if row[2] is not None and (row[1] or "").strip().isdigit()),
        ):
            expected = round(
                candidate["votes"][before] + number((cell(row, result[stage] - 1) or "").replace("\xad", "")), 2
            )
            if abs(expected - candidate["votes"][stage]) > 0.01:
                wrong.append((candidate, expected))
        fixed = counted + sum(expected - candidate["votes"][stage] for candidate, expected in wrong)
        spare = round(exhausted[before] + number(cell(rows[non_transferable], result[stage] - 1)), 2)
        if abs(fixed - total) > 1 and abs(spare - exhausted[stage]) > 0.01:
            fixed += spare - exhausted[stage]
            repairs.append((stage, "Non-transferable", exhausted[stage], spare))
            exhausted[stage] = spare
        if abs(fixed - total) > 1:
            raise ValueError(
                f"{path.name}: stage {stage} sums to {counted} against {total}; {[(c['name'], e) for c, e in wrong]}"
            )
        for candidate, expected in wrong:
            repairs.append((stage, candidate["name"], candidate["votes"][stage], expected))
            candidate["votes"][stage] = expected
    return facts, candidates, exhausted, repairs


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    units, lines, problems = [], [], []
    for year in (1973, 1975, 1982):
        folder = PARSED / f"x{year}"
        extract(raw.cached(SOURCE, f"{ARK}/{FILES[year]}").path, folder)
        elected = members(year)
        for path in sorted(folder.rglob("*.xls")):
            constituency = re.sub(r"[-_ ]+", " ", path.stem.split("-", 1)[1]).removesuffix(" corrected")
            facts, candidates, exhausted, repairs = stv_sheet(year, path)
            valid = sum(candidate["votes"][1] for candidate in candidates)
            facts |= FACTS.get((year, constituency), {})
            if facts.get("electorate", 0) < facts.get("polled", 0):
                facts.pop("electorate")
            if abs(facts.get("polled", 0) - facts.get("invalid", 0) - valid) > 1:
                print(
                    f"{year} {constituency}: polled {facts.get('polled')} invalid {facts.get('invalid')} valid {valid}"
                )
            facts["valid"] = valid
            seats = int(facts.setdefault("seats", round(valid / facts["quota"]) - 1))
            quota = valid // (seats + 1) + 1
            if facts["quota"] != quota:
                print(f"{year} {constituency}: printed quota {facts['quota']} against {quota}")
                facts["printed_quota"] = facts["quota"]
                facts["quota"] = quota
            for candidate in candidates:
                candidate["elected"] = max(candidate["votes"].values()) >= quota - 0.01
                candidate["key"] = (
                    constituency,
                    re.sub(
                        r"[^a-z]",
                        "",
                        re.split(r"[,.\s]", SURNAMES.get(candidate["name"], candidate["name"]))[0].lower(),
                    ),
                )
            last = max(candidates[0]["votes"])
            standing = sorted(
                (candidate for candidate in candidates if not candidate["elected"] and candidate["votes"][last] > 0),
                key=lambda candidate: -candidate["votes"][last],
            )
            open_seats = seats - sum(candidate["elected"] for candidate in candidates)
            named = [candidate for candidate in standing if candidate["key"] in elected]
            winners = named if len(named) == open_seats else standing[:open_seats]
            for candidate in winners:
                candidate["elected"] = True
            facts["declared"] = [candidate["name"] for candidate in winners if candidate not in standing[:open_seats]]
            facts["repairs"] = [f"{stage}|{name}|{printed}|{fixed}" for stage, name, printed, fixed in repairs]
            if len([candidate for candidate in candidates if candidate["elected"]]) != seats:
                raise ValueError(f"{year} {constituency}: wrong number elected")
            units.append({"year": year, "constituency": constituency, **facts})
            for candidate in candidates:
                options = elected.get(candidate["key"], []) if candidate["elected"] else []
                given = candidate["name"].partition(",")[2].strip()[:1].lower()
                found = (
                    next((option for option in options if option[0][:1].lower() == given), options[0])
                    if options
                    else None
                )
                if found is not None:
                    options.remove(found)
                    if not options:
                        elected.pop(candidate["key"])
                elif candidate["elected"] and (year, constituency, candidate["name"]) not in UNLISTED:
                    problems.append(f"{year} {constituency}: {candidate['name']} is not among the members")
                party = found[1] if found else party_of(year, candidate["name"], candidate["party"])
                candidate["full"] = found[0] if found else None
                for stage, votes in candidate["votes"].items():
                    lines.append(
                        {
                            "year": year,
                            "constituency": constituency,
                            "number": candidate["number"],
                            "name": candidate["name"],
                            "label": candidate["party"],
                            "party": party,
                            "full": candidate["full"],
                            "uuuc": year == 1975 and squash(candidate["party"]).startswith("uu"),
                            "elected": candidate["elected"],
                            "stage": stage,
                            "votes": votes,
                            "exhausted": exhausted[stage],
                        }
                    )
        problems += [
            f"{year}: {key} {elected[key]} not elected in the count" for key in set(elected) - set(UNLISTED.values())
        ]
    if problems:
        raise ValueError("\n".join(problems))
    units_frame = pl.DataFrame(units)
    lines_frame = pl.DataFrame(lines)
    for year, frame in units_frame.group_by("year"):
        print(year[0], len(frame), "constituencies", int(frame["seats"].sum()), "seats")
    first = lines_frame.filter(pl.col("stage") == 1)
    with pl.Config(tbl_rows=-1):
        print(
            first.group_by("year", "party")
            .agg(pl.col("votes").sum().round(0), pl.col("elected").sum())
            .sort("year", "votes", descending=[False, True])
        )
    units_frame.write_parquet(PARSED / "units.parquet")
    lines_frame.write_parquet(PARSED / "lines.parquet")
    parse_forum()


def person_name(sheet: str) -> str:
    text = NAMES.get(sheet) or display(re.sub(r"\(.*?\)", "", sheet))
    return re.sub(r"\s+", " ", re.sub(r"\b([A-Z])(?=\s|$)", r"\1.", text)).strip()


def emit() -> Path:
    units = pl.read_parquet(PARSED / "units.parquet")
    lines = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Northern Ireland Assembly elections of 1973 and 1982, the 1975 Constitutional Convention, and the 1996 Forum by constituency, from ARK",
            polity="GB-NIR",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        nir = cs.polity("GB-NIR")
        gb = cs.polity("GB")
        system = cs.key("gb.electoral_system", "stv")
        if system in cs.minted:
            raise ValueError("publish the stv electoral system first")
        known = {
            fold(text): party
            for text, party in conn.execute(
                "select n.text, p.id from party p join polity y on y.id = p.polity_id"
                " join name n on n.entity_id = p.id where y.iso_code = 'GB' order by p.id desc"
            ).fetchall()
        }
        found = conn.execute(
            "select p.id from party p join polity y on y.id = p.polity_id join name n on n.entity_id = p.id"
            " where y.iso_code = 'IE' and n.text = 'Sinn Féin' order by p.id limit 1"
        ).fetchone()
        if found is not None:
            known[fold("Sinn Féin")] = found[0]
        parties: dict[str, UUID] = {}

        def party_id(canonical: str | None) -> UUID | None:
            if canonical is None:
                return None
            if canonical not in parties:
                parties[canonical] = known.get(fold(canonical)) or cs.key("gb.party", slug(canonical))
                if parties[canonical] in cs.minted:
                    cs.add("party", id=parties[canonical], kind="party", polity_id=gb)
                    name(parties[canonical], canonical)
            return parties[canonical]

        sources = {}
        for year, (_, body_name, _, election_name, _) in BODIES.items():
            sources[year] = cs.source("ARK Northern Ireland Elections", f"{year} {election_name}")
            cs.add(
                "source",
                id=sources[year],
                kind="academic",
                publisher="ARK Northern Ireland Elections",
                title=f"{year} {election_name}: count by count results",
                url=f"https://www.ark.ac.uk/elections/{PAGES[year]}",
                attribution="Count-by-count spreadsheets from ARK Northern Ireland Elections (ark.ac.uk/elections).",
            )
            cs.artifact(raw.cached(SOURCE, f"{ARK}/{FILES[year]}"), sources[year])
            cs.artifact(raw.cached(SOURCE, f"{ARK}/{PAGES[year]}"), sources[year])
        members_source = cs.source(
            "Wikipedia", "Members of the Northern Ireland Assemblies of 1973 and 1982 and the Constitutional Convention"
        )
        cs.add(
            "source",
            id=members_source,
            kind="community",
            publisher="Wikipedia",
            title="Lists of members of the Northern Ireland Assemblies of 1973 and 1982 and the Constitutional Convention",
            url="https://en.wikipedia.org/wiki/List_of_members_of_the_Northern_Ireland_Constitutional_Convention",
            license="CC BY-SA 4.0",
            license_url="https://creativecommons.org/licenses/by-sa/4.0/",
            attribution="Elected members' parties and full names from Wikipedia's lists of members, CC BY-SA 4.0.",
        )
        for title in MEMBERS.values():
            cs.artifact(raw.cached(SOURCE, WIKIPEDIA.format(title)), members_source)

        areas = {
            (name_, valid_from): (code, area, valid_to)
            for code, name_, area, valid_from, valid_to in conn.execute(
                """select ac.code, n.text, a.id, ac.valid_from, ac.valid_to from area a
                   join area_code ac on ac.area_id = a.id
                   join name n on n.entity_id = a.id and n.is_preferred
                   join area_parent ap on ap.area_id = a.id
                   join area_code pc on pc.area_id = ap.parent_id and pc.code = 'NIR'
                   where a.scheme_code = 'gb.pcon'"""
            ).fetchall()
        }
        made: set[UUID] = set()
        for year, (body_key, body_name, dissolved, election_name, kind) in BODIES.items():
            day = DAYS[year]
            body = cs.key("gb.body", body_key)
            if body in cs.minted:
                cs.add("body", id=body, polity_id=nir, kind="unicameral")
                name(body, body_name)
            cs.add(
                "body_size",
                body_id=body,
                seats=78,
                valid_from=day,
                valid_from_precision="day",
                valid_to=dissolved,
                valid_to_precision="day",
            )
            election = cs.key("gb.election", f"{day}:{body_key}")
            cs.add("election", id=election, polity_id=nir, kind="general", date_from=day, date_to=day, status="held")
            name(election, f"{year} {election_name}")
            path = f"gb/northern-ireland/{day}-{kind}"
            cs.add("slug", path=path, entity_id=election)
            coalition = None
            if year == 1975:
                coalition = cs.key("gb.ni_bodies.alliance", f"{day}:uuuc")
                cs.add("alliance", id=coalition, kind="electoral_alliance", election_id=election)
                name(coalition, "United Ulster Unionist Coalition")
                for member in (UUP, DUP, VUP):
                    cs.add("alliance_member", alliance_id=coalition, party_id=party_id(member), role=None)
                cs.add("citation", entity_id=coalition, source_id=sources[year])
            for unit_row in units.filter(pl.col("year") == year).sort("constituency").iter_rows(named=True):
                label = unit_row["constituency"]
                match = [
                    (code, area)
                    for (text, begin), (code, area, end) in areas.items()
                    if fold(text) == fold(label) and (begin is None or begin <= day) and (end is None or day < end)
                ]
                if len(match) != 1:
                    raise ValueError(f"{year} {label}: {len(match)} Westminster areas")
                code, area = match[0]
                unit = cs.unit("gb.nia", code)
                if unit in cs.minted and unit not in made:
                    made.add(unit)
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=nir,
                        scheme_code="gb.nia",
                        code=code,
                        area_relation="equals",
                        area_id=area,
                        kind="standard",
                        valid_from=None,
                    )
                    name(unit, label)
                seats = int(unit_row["seats"])
                contest_key = f"{day}:{body_key}:{slug(label)}"
                contest = cs.key("gb.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=body,
                    district_area_id=area,
                    magnitude=seats,
                    round_number=1,
                    electoral_system_id=system,
                    default_result_kind="counted",
                )
                name(contest, f"{year} {election_name} in {label}")
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
                    source_id=sources[year],
                    is_default=True,
                    completeness=1,
                )
                cs.add("citation", entity_id=result_set, source_id=members_source)
                rows = lines.filter((pl.col("year") == year) & (pl.col("constituency") == label))
                stages = sorted(rows["stage"].unique().to_list())
                valid, polled, invalid = unit_row["valid"], unit_row["polled"], unit_row["invalid"]
                caveats = []
                if polled is not None and invalid is not None and abs(polled - invalid - valid) > 1:
                    caveats.append(
                        f"The spreadsheet prints {polled:,.0f} votes polled and {invalid:,.0f} invalid, which do not add up to the "
                        f"{valid:,.0f} valid votes in the count. The poll and invalid figures are left out."
                    )
                    polled = invalid = None
                if unit_row["printed_quota"] is not None:
                    caveats.append(
                        f"The spreadsheet prints a quota of {unit_row['printed_quota']:,.0f}, but the count runs on "
                        f"{unit_row['quota']:,.0f}, the Droop quota of the valid votes, which is stored."
                    )
                for repair in unit_row["repairs"]:
                    stage, who, printed, fixed = repair.split("|")
                    who = "the non-transferable votes" if who == "Non-transferable" else person_name(who)
                    caveats.append(
                        f"At stage {stage} the spreadsheet prints {float(printed):,.2f} for {who}; the stage's transfers give "
                        f"{float(fixed):,.2f}, which is stored."
                    )
                if unit_row["declared"]:
                    caveats.append(
                        "The spreadsheet ends before every seat is filled; the declared result gives the last seat to "
                        + " and ".join(person_name(who) for who in unit_row["declared"])
                        + "."
                    )
                if year == 1975 and rows["uuuc"].any():
                    caveats.append(
                        "ARK's spreadsheet labels most United Ulster Unionist Coalition candidates by the coalition only. Each is stored "
                        "under their own party: from Wikipedia's list of members for those elected, and for the rest from the only split "
                        "that matches ARK's published party totals."
                    )
                unpledged = [
                    row["name"]
                    for row in rows.filter(pl.col("stage") == 1).iter_rows(named=True)
                    if squash(row["label"]) == "un" and row["full"] is None
                ]
                if year == 1973 and unpledged:
                    who = [person_name(text) for text in unpledged]
                    caveats.append(
                        f"ARK's spreadsheet describes {who[0]} as a Unionist, not as an Official Unionist pledged to the 1973 White Paper; stored under the Ulster Unionist Party."
                        if len(who) == 1
                        else f"ARK's spreadsheet describes {', '.join(who[:-1])} and {who[-1]} as Unionists, not as Official Unionists pledged to the 1973 White Paper; they are stored under the Ulster Unionist Party."
                    )
                for text in caveats:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=text)
                for measure, amount in (
                    (REGISTERED, unit_row["electorate"]),
                    (BALLOTS, polled),
                    (VALID, valid),
                    (INVALID, invalid),
                    (QUOTA, unit_row["quota"]),
                ):
                    if amount is not None:
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
                for candidate in (
                    rows.select("number", "name", "party", "full", "uuuc", "elected")
                    .unique(maintain_order=True, subset=["number"])
                    .iter_rows(named=True)
                ):
                    series = {
                        row["stage"]: row["votes"]
                        for row in rows.filter(pl.col("number") == candidate["number"]).iter_rows(named=True)
                    }
                    owner = party_id(candidate["party"])
                    full = candidate["full"] or person_name(candidate["name"])
                    option_key = f"{contest_key}:{candidate['number']}"
                    option = cs.key("gb.ni_bodies.option", option_key)
                    person = cs.key("gb.ni_bodies.person", option_key)
                    cs.add("person", id=person)
                    name(person, full)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=owner,
                        alliance_id=coalition if candidate["uuuc"] else None,
                        ballot_label=full,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("gb.ni_bodies.candidacy", option_key),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=candidate["name"],
                        member_of_party_id=owner,
                        is_independent=owner is None,
                    )
                    for stage in stages:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=VOTES,
                            count_round=stage,
                            flags=0,
                            value=round(series[stage], 2),
                        )
                        if series[stage] == 0 and stage > 1 and not candidate["elected"]:
                            break
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="elected"
                        if candidate["elected"]
                        else ("lost" if series[stages[-1]] > 0 else "eliminated"),
                    )
                for row in rows.filter(pl.col("number") == rows["number"][0]).iter_rows(named=True):
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=EXHAUSTED,
                        count_round=row["stage"],
                        flags=0,
                        value=round(row["exhausted"], 2),
                    )
        day = DAYS[1996]
        forum_units = pl.read_parquet(PARSED / "forum_units.parquet")
        forum_lists_frame = pl.read_parquet(PARSED / "forum_lists.parquet")
        forum_source = cs.source("ARK Northern Ireland Elections", "1996 Northern Ireland Forum election")
        cs.add(
            "source",
            id=forum_source,
            kind="academic",
            publisher="ARK Northern Ireland Elections",
            title="1996 Northern Ireland Forum election: results and candidate lists",
            url=f"https://www.ark.ac.uk/elections/{PAGES[1996]}",
            attribution="Results spreadsheets and candidate lists from ARK Northern Ireland Elections (ark.ac.uk/elections).",
        )
        cs.artifact(raw.cached(SOURCE, f"{ARK}/{FILES[1996]}"), forum_source)
        cs.artifact(raw.cached(SOURCE, f"{ARK}/{PAGES[1996]}"), forum_source)
        for page in [*FORUM.values(), "96cands"]:
            cs.artifact(raw.cached(SOURCE, f"{ARK}/{page}.htm"), forum_source)
        body = cs.key("gb.body", "ni-forum")
        if body in cs.minted:
            cs.add("body", id=body, polity_id=nir, kind="unicameral")
            name(body, "Northern Ireland Forum for Political Dialogue")
        cs.add(
            "body_size",
            body_id=body,
            seats=110,
            valid_from=day,
            valid_from_precision="day",
            valid_to=date(1998, 4, 25),
            valid_to_precision="day",
        )
        list_system = cs.key("gb.electoral_system", "ni-forum-list")
        cs.add("electoral_system", id=list_system, polity_id=nir, rules={"formula": "dhondt"}, rules_version=1)
        if list_system in cs.minted:
            name(list_system, "Closed party lists by d'Hondt (1996 Northern Ireland Forum constituencies)")
        election = cs.key("gb.election", f"{day}:ni-forum")
        cs.add("election", id=election, polity_id=nir, kind="general", date_from=day, date_to=day, status="held")
        name(election, "1996 Northern Ireland Forum election")
        path = f"gb/northern-ireland/{day}-forum"
        cs.add("slug", path=path, entity_id=election)
        people: dict[str, UUID] = {}
        regions = [(None, cs.area("gb.country", "NIR"), None)]
        for unit_row in forum_units.iter_rows(named=True):
            match = [
                (code, area)
                for (text, begin), (code, area, _) in areas.items()
                if fold(text) == fold(unit_row["constituency"]) and begin == date(1997, 5, 1)
            ]
            if len(match) != 1:
                raise ValueError(f"1996 {unit_row['constituency']}: {len(match)} Westminster areas")
            regions.append((unit_row, match[0][1], match[0][0]))
        for unit_row, area, code in regions:
            label = "Northern Ireland" if unit_row is None else unit_row["constituency"]
            contest_key = f"{day}:ni-forum:{slug(label)}"
            contest = cs.key("gb.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="list_allocation",
                body_id=body,
                district_area_id=area,
                magnitude=20 if unit_row is None else 5,
                round_number=1,
                electoral_system_id=None if unit_row is None else list_system,
                default_result_kind="counted",
            )
            if unit_row is None:
                name(contest, "1996 Northern Ireland Forum election, regional top-up seats")
                cs.add("slug", path=f"{path}/regional-list", entity_id=contest)
            else:
                name(contest, f"1996 Northern Ireland Forum election in {label}")
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
                source_id=forum_source,
                is_default=True,
                completeness=0 if unit_row is None else 1,
            )
            rows = forum_lists_frame.filter(
                pl.col("constituency").is_null() if unit_row is None else pl.col("constituency") == label
            )
            if unit_row is None:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md="The ten parties with the most votes across the 18 constituencies each took two regional seats, "
                    "filled from their regional lists. The votes are counted in the constituency contests; this contest "
                    "holds only the regional seats and the members elected to them.",
                )
            else:
                unit = cs.unit("gb.nia", code)
                for added in unit_row["added"]:
                    list_label, votes = added.split("|")
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md=f"ARK's spreadsheet leaves out the {re.sub(r'^The ', '', list_label)} list. Its {int(votes):,} votes come from "
                        "ARK's candidate page, and with them the lists add up to the valid votes.",
                    )
                for measure, amount in (
                    (REGISTERED, unit_row["electorate"]),
                    (BALLOTS, unit_row["polled"]),
                    (VALID, unit_row["valid"]),
                    (INVALID, unit_row["invalid"]),
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
            for list_row in rows.filter(pl.col("position") == 1).sort("votes", descending=True).iter_rows(named=True):
                owner = party_id(list_row["party"])
                option_key = f"{contest_key}:{slug(list_row['label'])}"
                option = cs.key("gb.ni_bodies.option", option_key)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="list",
                    party_id=owner,
                    ballot_label=list_row["label"],
                )
                if unit_row is not None:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=VOTES,
                        count_round=0,
                        flags=0,
                        value=list_row["votes"],
                    )
                if list_row["seats"]:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=list_row["seats"])
                for member in rows.filter(
                    (pl.col("label") == list_row["label"]) & pl.col("name").is_not_null()
                ).iter_rows(named=True):
                    person_key = f"1996:{slug(list_row['party'] or list_row['label'])}:{slug(member['name'])}"
                    if person_key not in people:
                        people[person_key] = cs.key("gb.ni_bodies.person", person_key)
                        cs.add("person", id=people[person_key])
                        name(people[person_key], member["name"])
                    candidacy = cs.key("gb.ni_bodies.candidacy", f"{option_key}:{member['position']}")
                    cs.add(
                        "candidacy",
                        id=candidacy,
                        person_id=people[person_key],
                        option_id=option,
                        role="list_member",
                        list_position=member["position"],
                        ballot_name=member["name"],
                        member_of_party_id=owner,
                        is_independent=owner is None,
                    )
                    if member["elected"]:
                        cs.add(
                            "outcome",
                            result_set_id=result_set,
                            option_id=option,
                            candidacy_id=candidacy,
                            kind="elected",
                        )
        return cs.write()
