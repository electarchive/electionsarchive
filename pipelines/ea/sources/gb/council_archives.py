import html
import re
import subprocess
from collections import Counter
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.names import display, foreign_preferred
from ea.store import DATA, connect

SOURCE = "gb.council_archives"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
VOTES, REGISTERED, BALLOTS, INVALID = 1, 2, 3, 5
OGL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
DOVER, KENT, EPSOM, SURREY, BEDFORD, CALDERDALE = (
    "E07000108",
    "E10000016",
    "E07000208",
    "E10000030",
    "E06000055",
    "E08000033",
)
DOVER_SITE = "https://www.dover.gov.uk/asset-library"
EPSOM_SITE = "https://www.epsom-ewell.gov.uk/sites/default/files/documents/council/elections-and-voting"
CALDERDALE_SITE = "https://www.calderdale.gov.uk/council/democracy/electionresults/results.jsp"
CALDERDALE_WARDS = 18
DOVER_NOTES = (
    "Dover District Council's typed summary of the district council election, ward by ward, with each candidate's "
    "name, description, and votes, the elected candidates, the seats, and the turnout as a percentage. It prints no "
    "electorate or ballot count, so turnout is not stored."
)
COLUMNS = {
    "dover-old": (135, 298, 470, 505),
    "dover-1999": (160, 325, 462, 505),
    "kent-1997": (155, 298, 466, 502),
    "epsom": (136, 280, 425, 500),
    "bedford": (158, 302, 470, 518),
}
FILES: list[dict] = [
    {
        "file": f"{DOVER_SITE}/1987-district-council-election-results.pdf",
        "parser": "dover-old",
        "code": DOVER,
        "day": date(1987, 5, 7),
        "publisher": "Dover District Council",
        "title": "District Council Election, 7 May 1987: results",
        "notes": DOVER_NOTES,
    },
    {
        "file": f"{DOVER_SITE}/1991-district-council-election-results.pdf",
        "parser": "dover-old",
        "code": DOVER,
        "day": date(1991, 5, 2),
        "publisher": "Dover District Council",
        "title": "District Council Election, 2 May 1991: results",
        "notes": DOVER_NOTES,
    },
    {
        "file": f"{DOVER_SITE}/1995-district-council-election-results.pdf",
        "parser": "dover-old",
        "code": DOVER,
        "day": date(1995, 5, 4),
        "publisher": "Dover District Council",
        "title": "District Council Election, 4 May 1995: results",
        "notes": DOVER_NOTES,
    },
    {
        "file": f"{DOVER_SITE}/1999-district-council-election-results.pdf",
        "parser": "dover-1999",
        "code": DOVER,
        "day": date(1999, 5, 6),
        "publisher": "Dover District Council",
        "title": "District Council Election, 6 May 1999: results",
        "notes": DOVER_NOTES + " A table of the new council's seats by party closes the file.",
    },
    {
        "file": f"{DOVER_SITE}/1997-kent-county-council-elections-all-dover-district-areas.pdf",
        "parser": "kent-1997",
        "code": KENT,
        "day": date(1997, 5, 1),
        "publisher": "Dover District Council",
        "title": "County Council Election, 1 May 1997: results in Dover district",
        "notes": "Dover District Council's typed summary of the Kent County Council election in the district's seven "
        "divisions, with each candidate's name, description, and votes, the elected candidate, the electorate, "
        "the ballot papers, and the percentage poll.",
    },
    {
        "file": f"{DOVER_SITE}/2001-kent-county-council-elections-all-dover-district-areas.pdf",
        "parser": "kent-2001",
        "code": KENT,
        "day": date(2001, 6, 7),
        "publisher": "Dover District Council",
        "title": "Kent County Council Election, 7 June 2001: declarations of result of poll in Dover district",
        "notes": "The deputy returning officer's declaration of result for each of the seven Kent County Council "
        "divisions in Dover district, one page each, with candidates, descriptions, votes, the elected candidate, "
        "rejected ballots, electorate, and ballot papers issued.",
    },
    {
        "file": f"{EPSOM_SITE}/BoroughCouncilElectionResults6May1999.pdf",
        "parser": "epsom",
        "code": EPSOM,
        "day": date(1999, 5, 6),
        "publisher": "Epsom and Ewell Borough Council",
        "title": "Borough Council Election Results, 6 May 1999",
        "notes": "The borough council's summary, ward by ward, with each candidate's name, description, and votes, the "
        "elected candidates, the electorate, the seats, the ballot papers, and the percentage poll.",
    },
    {
        "file": f"{EPSOM_SITE}/SCCElectionResults1965to2005.pdf",
        "parser": "surrey",
        "code": SURREY,
        "day": None,
        "publisher": "Epsom and Ewell Borough Council",
        "title": "Surrey County Council Election Results, 1965 to 2005",
        "notes": "The borough council's history of the Surrey County Council elections in its five county divisions, "
        "one page each, from 1965 to 2005, with each candidate's name, party from 1981 on, and votes, the elected "
        "candidate, and the turnout as a percentage. It gives the month and year of each poll, not the day; the "
        "county elections from 1973 on are dated to the day England's county councils polled that month. Seats won "
        "without a poll print Uncontested.",
    },
    {
        "file": "https://www.bedford.gov.uk/files/boroughelection7may1998.pdf/download",
        "parser": "bedford",
        "code": BEDFORD,
        "day": date(1998, 5, 7),
        "publisher": "Bedford Borough Council",
        "title": "Borough Council Election, 7 May 1998: results",
        "notes": "The borough council's summary, ward by ward, with each candidate's name, description, and votes, the "
        "elected candidate, the electorate, the seats, the ballot papers, and the percentage poll.",
    },
]
CALDERDALE_ELECTIONS = {
    2: (date(1999, 5, 6), "Election of Local Councillors 1999"),
    4: (date(1999, 10, 29), "Elland by-election, 29 October 1999"),
    5: (date(2000, 5, 4), "Election of Local Councillors 2000"),
}
CALDERDALE_NOTES = (
    "Calderdale Council's election results pages, one per ward, with each candidate's surname and forenames, party, "
    "votes, and the elected candidate, and the ward's eligible voters, total turnout, and rejected papers."
)
SURREY_DAYS = {
    1973: date(1973, 4, 12),
    1977: date(1977, 5, 5),
    1981: date(1981, 5, 7),
    1985: date(1985, 5, 2),
    1989: date(1989, 5, 4),
    1993: date(1993, 5, 6),
    1997: date(1997, 5, 1),
    2001: date(2001, 6, 7),
}
HELD = {(SURREY, 2005)}
MONTHS = {
    month: index
    for index, month in enumerate(
        ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October"]
        + ["November", "December"],
        1,
    )
}
FOOTER = re.compile(r"Page \d+ of|\.doc|ELECT\\|Total % Turnout|Composition of New Council")
LABEL = re.compile(
    r"(Electorate|Seats|Ballot Papers|% Poll|% Turnout|Uncontested)\s*([\d.]*)|([\d.]+)%\s*Poll", re.IGNORECASE
)
FIXES = {"Marita Helga lsabella Gerda Gerrard": "Marita Helga Isabella Gerda Gerrard"}
LABOUR = ("wikidata", "Q9630")
CONSERVATIVE = ("wikidata", "Q9626")
LIBERAL_DEMOCRATS = ("wikidata", "Q9624")
ALLIANCE = ("wikidata", "Q7389478")
PARTIES = {
    **dict.fromkeys(
        (
            "Labour Party",
            "Lab",
            "The Labour Party Candidate",
            "Labour Party Candidate",
            "Labour and Co-Operative Party",
            "The Labour and Co-Operative Party Candidate",
            "Labour & Co-operative Party",
            "The Labour & Co-Operative Party Candidate",
            "Labour Co-Op Candidate",
            "Labour-Co-Op",
            "Labour/Co-Op",
            "Labour Party Candidate",
        ),
        LABOUR,
    ),
    **dict.fromkeys(
        (
            "Conservative Party",
            "Conservative",
            "Con",
            "The Conservative Party Candidate",
            "Conservative Party Candidate",
            "Official Conservative Party",
            "Official Conservative Candidate",
        ),
        CONSERVATIVE,
    ),
    **dict.fromkeys(
        (
            "Liberal Democrat",
            "Liberal Democrats",
            "Lib Dem",
            "LibDem",
            "Lib.Dem",
            "Liberal Democrat Focus",
            "Liberal Democrat Focus Team",
            "Liberal Democrat and Focus Team",
            "Liberal Democrat & Focus Team",
            "SLD",
        ),
        LIBERAL_DEMOCRATS,
    ),
    "SDP/Lib All": ALLIANCE,
    "Lib/SDP": ALLIANCE,
    "Green Party": ("gb.party", "green-party"),
    "Green": ("gb.party", "green-party"),
    "UK Independence Party": ("wikidata", "Q10647"),
    "UK Ind Party": ("wikidata", "Q10647"),
    "British National Party": ("gb.party", "british-national-party"),
    "Communist": ("wikidata", "Q592068"),
}
INDEPENDENT = {"Independent"}


def fetch() -> None:
    for entry in FILES:
        raw.fetch(SOURCE, entry["file"], suffix=".pdf")
    for election in CALDERDALE_ELECTIONS:
        for area in range(1, CALDERDALE_WARDS + 1):
            raw.fetch(SOURCE, f"{CALDERDALE_SITE}?election={election}&area={area}", suffix=".html")


def found(pattern: str, content: str, flags: int = 0) -> re.Match[str]:
    match = re.search(pattern, content, flags)
    if match is None:
        raise ValueError(f"no match for {pattern}")
    return match


def pages(path: str) -> int:
    info = subprocess.run(["pdfinfo", path], capture_output=True, text=True, check=True).stdout
    return int(found(r"Pages:\s+(\d+)", info)[1])


def words(path: str, page: int) -> list[tuple[float, float, str]]:
    out = subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), "-bbox", path, "-"], capture_output=True, text=True, check=True
    ).stdout
    return [
        (float(x), float(y), html.unescape(word))
        for x, y, word in re.findall(r'<word xMin="([\d.]+)" yMin="([\d.]+)"[^>]*>([^<]*)</word>', out)
    ]


def lines(page_words: list[tuple[float, float, str]]) -> list[list[tuple[float, float, str]]]:
    grouped: list[list[tuple[float, float, str]]] = []
    for word in sorted(page_words, key=lambda w: (w[1], w[0])):
        if grouped and word[1] - grouped[-1][0][1] < 4:
            grouped[-1].append(word)
        else:
            grouped.append([word])
    for line in grouped:
        line.sort()
    return grouped


def join(line: list[tuple[float, float, str]], low: float, high: float) -> str:
    return " ".join(word for x, y, word in line if low <= x < high)


def candidate(entry: dict, ward: dict, page: int, name: str, description: str, votes: int | None, elected: bool):
    return {
        "code": entry["code"],
        "file": entry["file"],
        "at": ward,
        "page": page,
        "name": name,
        "description": description or None,
        "votes": votes,
        "elected": elected,
    }


def table(entry: dict) -> tuple[list[dict], list[dict]]:
    path = str(raw.cached(SOURCE, entry["file"]).path)
    name_x, description_x, votes_x, result_x = COLUMNS[entry["parser"]]
    wards: list[dict] = []
    rows: list[dict] = []
    for page in range(1, pages(path) + 1):
        grouped = lines(words(path, page))
        top = next(i for i, line in enumerate(grouped) if any(w[2] in ("Candidates", "Candidate") for w in line))
        last_ward = False
        pending = ""
        for line in grouped[top + 1 :]:
            text = " ".join(w[2] for w in line)
            if FOOTER.search(text):
                break
            left = join(line, 0, name_x)
            if left:
                labels = [m for m in LABEL.finditer(left)]
                if labels:
                    for label in labels:
                        key = (label[1] or "% Poll").lower()
                        value = label[2] if label[1] else label[3]
                        if key == "uncontested":
                            wards[-1]["tacit"] = True
                        elif key == "seats":
                            wards[-1]["seats"] = int(value)
                        elif key == "electorate":
                            wards[-1]["registered"] = int(value)
                        elif key == "ballot papers":
                            wards[-1]["ballots"] = int(value)
                        else:
                            wards[-1]["percent"] = float(value)
                    last_ward = False
                elif last_ward:
                    wards[-1]["name"] += f" {left}"
                else:
                    wards.append({"name": left, "code": entry["code"]})
                    last_ward = True
            name = join(line, name_x, description_x)
            description = join(line, description_x, votes_x)
            votes = join(line, votes_x, result_x).replace(",", "")
            result = join(line, result_x, 10_000)
            if not (name or description or votes or result):
                continue
            last_ward = False
            if not (name or votes or result) and description.endswith("-"):
                pending = description
                continue
            if votes.isdigit() or result == "Elected":
                if pending:
                    description = f"{pending}{description}" if description else pending
                    pending = ""
                rows.append(
                    candidate(entry, wards[-1], page, name, description, int(votes) if votes else None, bool(result))
                )
                if result not in ("", "Elected"):
                    raise ValueError(f"{entry['file']} page {page}: result {result}")
            elif votes:
                raise ValueError(f"{entry['file']} page {page}: votes {votes}")
            else:
                rows[-1]["name"] = f"{rows[-1]['name']} {name}".strip()
                previous = rows[-1]["description"] or ""
                glue = "" if previous.endswith("-") else " "
                rows[-1]["description"] = f"{previous}{glue}{description}".strip() or None
    for ward in wards:
        ward["name"] = ward["name"].replace("’", "'")
    return rows, wards


def kent_2001(entry: dict) -> tuple[list[dict], list[dict]]:
    path = str(raw.cached(SOURCE, entry["file"]).path)
    rows: list[dict] = []
    wards: list[dict] = []
    for page in range(1, pages(path) + 1):
        layout = subprocess.run(
            ["pdftotext", "-layout", "-f", str(page), "-l", str(page), path, "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        division = display(found(r"^\s*(.+?) ELECTORAL DIVISION", layout, re.MULTILINE)[1].strip())
        figures = found(r"Vacant Seats:\s*(\d+)\s+Electorate:\s*(\d+)\s+Ballot Papers Issued:\s*(\d+)", layout)
        rejected = re.search(r"TOTAL REJECTED\s+(\d+)", layout)
        ward = {
            "name": division,
            "code": entry["code"],
            "seats": int(figures[1]),
            "registered": int(figures[2]),
            "ballots": int(figures[3]),
            "rejected": int(rejected[1]) if rejected else None,
        }
        wards.append(ward)
        page_words = words(path, page)
        top = max(y for x, y, word in page_words if word == "votes") + 6
        bottom = min(y for x, y, word in page_words if word == "And" and y > top)
        for line in lines([w for w in page_words if top < w[1] < bottom]):
            surname = join(line, 0, 158)
            forenames = join(line, 158, 258)
            description = join(line, 258, 440)
            votes = join(line, 440, 478)
            result = join(line, 478, 10_000)
            if not (surname or votes or description) and forenames:
                rows[-1]["extra"] = f"{rows[-1].get('extra', '')} {forenames}".strip()
                continue
            if not votes.isdigit() or result not in ("", "ELECTED"):
                raise ValueError(f"Kent 2001 page {page}: line {line}")
            rows.append(candidate(entry, ward, page, forenames, description, int(votes), bool(result)))
            rows[-1]["surname"] = display(surname)
    for row in rows:
        extra = row.pop("extra", None)
        surname = row.pop("surname")
        if extra and extra.startswith("(commonly"):
            known = display(found(r"known as (\w+)\)", extra)[1])
            row["name"] = f"{row['name']} {surname} “{known}”"
        else:
            row["name"] = f"{row['name']} {extra} {surname}" if extra else f"{row['name']} {surname}"
    return rows, wards


def surrey(entry: dict) -> tuple[list[dict], list[dict]]:
    path = str(raw.cached(SOURCE, entry["file"]).path)
    rows: list[dict] = []
    wards: list[dict] = []
    for page in range(1, pages(path) + 1):
        grouped = lines(words(path, page))
        division = next(
            " ".join(w[2] for w in line) for line in grouped if any(w[2] == "Division" for w in line)
        ).removesuffix(" Division")
        division = division.replace("&", "and")
        header = next(line for line in grouped if any(w[2] == "Candidates" for w in line))
        at = {word: x - 4 for x, y, word in header}
        bounds = (at["Candidates"], at["Party"], at["Votes"], at["Result"], at["Turnout"])
        for line in grouped[grouped.index(header) + 1 :]:
            text = " ".join(w[2] for w in line)
            if FOOTER.search(text):
                break
            left = join(line, 0, bounds[0])
            when = re.fullmatch(r"(\w+) (\d{4})", left)
            if when:
                year = int(when[2])
                ward = {"name": division, "code": entry["code"], "year": year}
                if year in SURREY_DAYS:
                    ward["day"] = SURREY_DAYS[year]
                else:
                    ward["day"] = date(year, MONTHS[when[1]], 1)
                    ward["precision"] = "month"
                wards.append(ward)
            elif left == "(By election)":
                wards[-1]["by"] = True
                wards[-1]["precision"] = "month"
                wards[-1]["day"] = date(wards[-1]["year"], wards[-1]["day"].month, 1)
            elif left:
                raise ValueError(f"Surrey page {page}: {left}")
            name = join(line, bounds[0], bounds[1])
            party = join(line, bounds[1], bounds[2])
            votes = join(line, bounds[2], bounds[3]).replace(",", "")
            result = join(line, bounds[3], bounds[4])
            if not name:
                continue
            if votes not in ("Uncontested",) and not votes.isdigit():
                raise ValueError(f"Surrey page {page}: votes {votes}")
            if result not in ("", "Elected"):
                raise ValueError(f"Surrey page {page}: result {result}")
            ward = wards[-1]
            if votes == "Uncontested":
                ward["tacit"] = True
            rows.append(
                candidate(
                    entry,
                    ward,
                    page,
                    name,
                    "" if party == "-" else party,
                    None if votes == "Uncontested" else int(votes),
                    bool(result),
                )
            )
    for ward in wards:
        ward["seats"] = 1
    kept = [ward for ward in wards if (ward["code"], ward["day"].year) not in HELD]
    return [row for row in rows if any(row["at"] is ward for ward in kept)], kept


def text(cell: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()


def calderdale() -> tuple[list[dict], list[dict]]:
    rows: list[dict] = []
    wards: list[dict] = []
    for election, (day, title) in CALDERDALE_ELECTIONS.items():
        for area in [3] if election == 4 else range(1, CALDERDALE_WARDS + 1):
            url = f"{CALDERDALE_SITE}?election={election}&area={area}"
            content = Path(raw.cached(SOURCE, url).path).read_text(encoding="utf-8", errors="replace")
            match = re.search(r'summary="candidates name[^"]*">(.*?)</table>', content, re.DOTALL)
            if match is None:
                continue
            ward_name = found(r"\d{2}/\d{2}/\d{4} (.+?) Menu :", text(content))[1]
            figures = found(
                r"(?:eligible voters (\d+) )?Total turnout (\d+) (?:\([\d.]+%\) )?Papers rejected (\d+)",
                text(content),
            )
            ward = {
                "name": ward_name,
                "code": CALDERDALE,
                "day": day,
                "by": election == 4,
                "registered": int(figures[1]) if figures[1] else None,
                "ballots": int(figures[2]),
                "rejected": int(figures[3]),
                "url": url,
            }
            wards.append(ward)
            entry = {"code": CALDERDALE, "file": url, "day": day}
            for line in re.findall(r"<tr>(.*?)</tr>", match[1], re.DOTALL)[1:]:
                cells = re.findall(r"<td[^>]*>(.*?)</td>", line, re.DOTALL)
                surname = text(found(r"<strong>(.*?)</strong>", cells[0], re.DOTALL)[1])
                forenames = text(cells[0].split("<br />", 1)[1]) if "<br />" in cells[0] else ""
                rows.append(
                    candidate(
                        entry,
                        ward,
                        0,
                        f"{forenames} {surname}".strip(),
                        text(cells[1]),
                        int(text(cells[2])),
                        text(cells[3]) == "Elected",
                    )
                )
    return rows, wards


def parse() -> None:
    rows: list[dict] = []
    wards: list[dict] = []
    for entry in FILES:
        parser = {"kent-2001": kent_2001, "surrey": surrey}.get(entry["parser"], table)
        found_rows, found_wards = parser(entry)
        for ward in found_wards:
            ward.setdefault("day", entry["day"])
        rows += found_rows
        wards += found_wards
    found_rows, found_wards = calderdale()
    rows += found_rows
    wards += found_wards
    for row in rows:
        ward = row.pop("at")
        row["name"] = FIXES.get(row["name"], re.sub(r"(\w)- (\w)", r"\1-\2", row["name"]))
        row.update(day=ward["day"], precision=ward.get("precision", "day"), by=ward.get("by", False), ward=ward["name"])
    frame = pl.DataFrame(rows, infer_schema_length=None).with_row_index("number")
    figures = {(w["code"], w["day"], w["name"]): w for w in wards}
    extra = []
    for (code, day, ward), group in frame.group_by("code", "day", "ward", maintain_order=True):
        info = figures[(code, day, ward)]
        votes = group["votes"].drop_nulls()
        elected = int(group["elected"].sum())
        tacit = len(votes) == 0
        seats = info.get("seats", group.height if tacit else max(elected, 1))
        if len(votes) not in (0, group.height):
            raise ValueError(f"{code} {day} {ward}: votes for some candidates only")
        if tacit and (group.height != seats or elected != seats):
            raise ValueError(f"{code} {day} {ward}: {group.height} unopposed candidates for {seats} seats")
        if elected == 0 and code == CALDERDALE:
            print(f"{code} {day} {ward}: no candidate marked elected")
        elif elected != seats:
            raise ValueError(f"{code} {day} {ward}: {elected} elected for {seats} seats")
        if not tacit and elected:
            floor = min(group.filter(pl.col("elected"))["votes"].to_list())
            if max(group.filter(~pl.col("elected"))["votes"].to_list(), default=0) > floor:
                print(f"{code} {day} {ward}: a candidate not elected outpolls one elected")
        registered, ballots, rejected = info.get("registered"), info.get("ballots"), info.get("rejected")
        over = None
        if registered and ballots and "percent" in info and abs(100 * ballots / registered - info["percent"]) > 0.02:
            raise ValueError(f"{code} {day} {ward}: {ballots}/{registered} is not {info['percent']}%")
        if ballots and seats == 1 and not tacit:
            counted = int(votes.sum()) + (rejected or 0)
            if code == CALDERDALE and counted != ballots:
                raise ValueError(f"{code} {day} {ward}: {counted} votes and rejected papers, {ballots} ballots")
            if counted > ballots:
                print(f"{code} {day} {ward}: {counted} votes and rejected papers, {ballots} ballots")
                over = counted
        extra.append(
            {
                "code": code,
                "day": day,
                "ward": ward,
                "seats": seats,
                "registered": registered,
                "ballots": ballots,
                "rejected": rejected,
                "over": over,
            }
        )
    frame = frame.join(pl.DataFrame(extra, infer_schema_length=None), on=["code", "day", "ward"], how="left")
    dover_1999 = Counter(
        next(
            (group for group in ("Labour", "Conservative", "Liberal Democrat") if group in row["description"]), "Other"
        )
        for row in frame.filter(
            (pl.col("code") == DOVER) & (pl.col("day") == date(1999, 5, 6)) & pl.col("elected")
        ).iter_rows(named=True)
    )
    if dover_1999 != {"Labour": 28, "Conservative": 26, "Liberal Democrat": 1, "Other": 1}:
        raise ValueError(f"Dover 1999 seats by party {dict(dover_1999)} differ from the composition table")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "candidates.parquet")
    for (code, day), group in frame.group_by("code", "day", maintain_order=True):
        print(
            f"{code} {day}: {group['ward'].n_unique()} wards, {group.height} candidates, {int(group['elected'].sum())} elected"
        )
    for description, count in Counter(frame["description"].to_list()).most_common():
        print(f"{count:4} {description}")


def ward_keys(frame: pl.DataFrame) -> dict[tuple[str, date, str], str]:
    regular = frame.filter(~pl.col("by")).select("code", "day", "ward").unique()
    keys = {}
    for code, day, ward, by in frame.select("code", "day", "ward", "by").unique().iter_rows():
        year = day.year
        if by:
            earlier = regular.filter((pl.col("code") == code) & (pl.col("ward") == ward) & (pl.col("day") <= day))
            if earlier.height:
                year = max(earlier["day"].to_list()).year
        keys[(code, day, ward)] = f"{code}:{slug(ward)}:{year}"
    return keys


def emit() -> Path:
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    keys = ward_keys(candidates)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="English council elections from 1965 to 2001 by ward, from council-published results in Dover, "
            "Kent, Epsom and Ewell, Surrey, Bedford, and Calderdale",
            polity="GB",
        )
        outranked = foreign_preferred(conn, cs, SOURCE)

        def name(entity: UUID, label: str) -> None:
            if entity in outranked and entity not in cs.minted:
                return
            cs.add("name", entity_id=entity, text=label, lang="en", kind="official", is_preferred=True)

        def caveat(entity: UUID, text: str) -> None:
            cs.add("note", entity_id=entity, kind="caveat", body_md=text)

        gb = cs.polity("GB")
        systems = {system: cs.key("gb.electoral_system", system) for system in ("fptp", "block")}
        if cs.minted & set(systems.values()):
            raise ValueError("publish gb.umit and gb.parliament first")
        parties = {key: cs.key(*key) for key in set(PARTIES.values())}
        if cs.minted & set(parties.values()):
            raise ValueError("a party this pipeline links is not in the archive")

        sources: dict[str, UUID] = {}
        for entry in FILES:
            sources[entry["file"]] = cs.source(entry["publisher"], entry["title"])
            cs.add(
                "source",
                id=sources[entry["file"]],
                kind="official_body",
                publisher=entry["publisher"],
                title=entry["title"],
                url=entry["file"],
                license="Open Government Licence v3.0",
                license_url=OGL,
                attribution="Contains public sector information licensed under the Open Government Licence v3.0.",
                notes=entry["notes"],
            )
            cs.artifact(raw.cached(SOURCE, entry["file"]), sources[entry["file"]])
        calderdale_source = cs.source("Calderdale Council", "Election results, 1999 and 2000")
        cs.add(
            "source",
            id=calderdale_source,
            kind="official_body",
            publisher="Calderdale Council",
            title="Election results, 1999 and 2000",
            url=CALDERDALE_SITE,
            license="Open Government Licence v3.0",
            license_url=OGL,
            attribution="Contains public sector information licensed under the Open Government Licence v3.0.",
            notes=CALDERDALE_NOTES,
        )
        for url in candidates.filter(pl.col("code") == CALDERDALE)["file"].unique().sort():
            sources[url] = calderdale_source
            cs.artifact(raw.cached(SOURCE, url), calderdale_source)

        councils: dict[str, tuple[UUID, UUID, UUID, str, str]] = {}
        for code in candidates["code"].unique(maintain_order=True):
            area = cs.area("gb.lad", code)
            polity = cs.key("gb.polity", code)
            body = cs.key("gb.body", code)
            if cs.minted & {polity, body}:
                raise ValueError(f"{code}: council not in the archive")
            path, council_name = conn.execute(
                "select (select path from slug where entity_id = %s order by is_current desc limit 1),"
                " (select text from name where entity_id = %s and is_preferred limit 1)",
                [polity, body],
            ).fetchall()[0]
            councils[code] = (area, polity, body, path, council_name)

        made: set[UUID] = set()
        made_wards: set[str] = set()
        for (code, day, by, precision), council_lines in candidates.group_by(
            "code", "day", "by", "precision", maintain_order=True
        ):
            area, polity, body, path, council_name = councils[code]
            stamp = f"{day:%Y-%m}" if precision == "month" else str(day)
            national = None
            if not by and precision == "day":
                national = cs.key("gb.election", f"{day}:local:gb")
                if national in cs.minted and national not in made:
                    made.add(national)
                    cs.add(
                        "election",
                        id=national,
                        polity_id=gb,
                        kind="local",
                        date_from=day,
                        date_to=day,
                        status="held",
                    )
                    wales = day.year in (1985, 1987, 1989, 1991, 1993, 1995)
                    name(national, f"{day.year} local elections in England{' and Wales' if wales else ''}")
                    cs.add("slug", path=f"gb/{day}-local", entity_id=national)
            election = cs.key("gb.election", f"{stamp}:local:{code}")
            if election in cs.minted:
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=national,
                    kind="by_election" if by else "local",
                    date_from=day,
                    date_to=day,
                    date_precision=precision,
                    status="held",
                )
                when = f"{day:%B %Y}" if precision == "month" else f"{day.day} {day:%B %Y}"
                name(election, f"{council_name} by-election, {when}" if by else f"{day.year} {council_name} election")
                cs.add("slug", path=f"{path}/{stamp}-local", entity_id=election)
                wards = council_lines["ward"].n_unique()
                if code == KENT:
                    caveat(
                        election,
                        f"Only the {wards} divisions in Dover district are loaded, from Dover District Council's "
                        "results; the county's other divisions are not in the archive yet.",
                    )
                if code == SURREY and not by:
                    caveat(
                        election,
                        f"Only the {wards} divisions in Epsom and Ewell are loaded, from the borough council's "
                        "history of the county elections; the county's other divisions are not in the archive yet.",
                    )
                if precision == "month" and not by:
                    caveat(election, f"The source gives the month of the poll, {day:%B %Y}, not the day.")
            noun = "division" if code[:3] == "E10" else "ward"
            for (ward,), lines in council_lines.group_by("ward", maintain_order=True):
                lines = lines.sort("number")
                first = lines.row(0, named=True)
                ward_key = keys[(code, day, ward)]
                ward_area = cs.area("gb.ward", ward_key)
                unit = cs.unit("gb.ward", ward_key)
                fresh = ward_key not in made_wards
                made_wards.add(ward_key)
                if ward_area in cs.minted and fresh:
                    cs.add("area", id=ward_area, scheme_code="gb.ward")
                    cs.add("area_code", area_id=ward_area, code=ward_key, valid_from=None, valid_to=None)
                    cs.add("area_parent", area_id=ward_area, parent_id=area, valid_from=None, valid_to=None)
                    name(ward_area, ward)
                if unit in cs.minted and fresh:
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=polity,
                        scheme_code="gb.ward",
                        code=ward_key,
                        area_relation="equals",
                        area_id=ward_area,
                        kind="standard",
                        valid_from=None,
                    )
                    name(unit, ward)
                seats = first["seats"]
                tacit = lines["votes"].is_null().all()
                contest_key = f"{stamp}:local:{code}:{slug(ward)}{':by' if by else ''}"
                contest = cs.key("gb.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=body,
                    district_area_id=ward_area,
                    magnitude=seats,
                    round_number=1,
                    electoral_system_id=systems["fptp" if seats == 1 else "block"],
                    default_result_kind="tacit" if tacit else "counted",
                )
                name(contest, f"{ward} {noun}, {council_name}, {day.year}")
                cs.add("slug", path=f"{path}/{stamp}-local/{slug(ward)}", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                source = sources[first["file"]]
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="tacit" if tacit else "counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
                locator: dict[str, object] = {"ward": ward}
                if first["page"]:
                    locator["page"] = first["page"]
                else:
                    locator["url"] = first["file"]
                cs.add("citation", entity_id=result_set, source_id=source, locator=locator)
                if not tacit and not lines["elected"].any():
                    caveat(result_set, "The council's page marks no candidate elected, so no winner is recorded.")
                if first["over"] is not None:
                    caveat(
                        result_set,
                        f"The declaration's votes and rejected papers add up to {first['over']:,}, more than the "
                        f"{first['ballots']:,} ballot papers it says were issued; both are stored as printed.",
                    )
                for line in lines.iter_rows(named=True):
                    option_key = f"{contest_key}:{line['number']}"
                    description = line["description"]
                    party = parties[PARTIES[description]] if description in PARTIES else None
                    label = line["name"]
                    if description is not None and party is None and description not in INDEPENDENT:
                        caveat(result_set, f"{label} stood as “{description}”; no party is linked.")
                    option = cs.key(f"{SOURCE}.option", option_key)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party,
                        ballot_label=label,
                    )
                    person = cs.key(f"{SOURCE}.person", option_key)
                    cs.add("person", id=person)
                    name(person, label)
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", option_key),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=label,
                        member_of_party_id=party,
                        is_independent=description in INDEPENDENT,
                    )
                    if not tacit:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=VOTES,
                            count_round=0,
                            flags=0,
                            value=line["votes"],
                        )
                    if line["elected"] or tacit:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
                if tacit:
                    continue
                elected = lines.filter(pl.col("elected"))
                if elected.height:
                    floor = elected.sort("votes").row(0, named=True)
                    above = lines.filter(~pl.col("elected") & (pl.col("votes") > floor["votes"]))
                    for line in above.iter_rows(named=True):
                        caveat(
                            result_set,
                            f"The source marks {floor['name']} elected with {floor['votes']:,} votes and "
                            f"{line['name']} not elected with {line['votes']:,}; both are shown as published.",
                        )
                for measure, field in ((REGISTERED, "registered"), (BALLOTS, "ballots"), (INVALID, "rejected")):
                    if first[field] is None:
                        continue
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=first[field],
                    )
        return cs.write()
