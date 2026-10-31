import difflib
import itertools
import re
import subprocess
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "gb.ni_assembly"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
VOTES, REGISTERED, BALLOTS, VALID, INVALID, EXHAUSTED, QUOTA = 1, 2, 3, 4, 5, 21, 26
DERIVED = 1
DAYS = {
    1998: date(1998, 6, 25),
    2003: date(2003, 11, 26),
    2007: date(2007, 3, 7),
    2011: date(2011, 5, 5),
    2016: date(2016, 5, 5),
    2017: date(2017, 3, 2),
    2022: date(2022, 5, 5),
}
ARK = {
    year: f"https://web.archive.org/web/2020if_/https://www.ark.ac.uk/elections/xls/asby/{year}.rar"
    for year in (1998, 2003, 2007)
}
NICVA = {
    year: f"https://raw.githubusercontent.com/NICVA/electionsni/master/{year}/{year}_archive_datapackage.zip"
    for year in (2011, 2016, 2017)
}
EONI_PAGE = "https://eoni.org.uk/results-data/ni-assembly-election-2022-results/"
EONI = [
    f"https://eoni.org.uk/media/{path}"
    for path in (
        "omtlpqow/ni-assembly-election-2022-result-sheet-belfast-east-xls.xlsx",
        "bqrbtk4a/ni-assembly-election-2022-result-sheet-belfast-north-xls.xlsx",
        "pkkjci4k/ni-assembly-election-2022-result-sheet-belfast-south-xls.xlsx",
        "huglkl0f/ni-assembly-election-results-belfast-west.xlsx",
        "qbyhzdfl/ni-assembly-election-2022-result-sheet-east-antrim.xlsx",
        "doll4nhx/ni-assembly-election-2022-result-sheet-east-londonderry.xlsx",
        "ohlpljon/ni-assembly-election-2022-result-sheet-fermanagh-south-tyrone-xls.xlsx",
        "vzoenf3x/ni-assembly-election-2022-result-sheet-foyle-xls.xlsx",
        "iv2fdbsu/ni-assembly-election-2022-result-sheet-lagan-valley-xls.xlsx",
        "qqblt0jk/ni-assembly-election-results-mid-ulster.xlsx",
        "g5fdrfyd/ni-assembly-election-2022-result-sheet-newry-and-armagh.xlsx",
        "bsfixocn/ni-assembly-election-results-north-antrim.xlsx",
        "vm5kqxjv/ni-assembly-election-results-north-down.xlsx",
        "ye2os1yn/ni-assembly-election-2022-result-sheet-south-antrim-xls.xlsx",
        "chabp3os/ni-assembly-election-results-south-down.xlsx",
        "00jdc0l2/ni-assembly-election-2022-result-sheet-strangford-xls.xlsx",
        "0trd4nsy/ni-assembly-election-results-upper-bann.xlsx",
        "1q3b5g2u/ni-assembly-election-2022-result-sheet-west-tyrone-xls.xlsx",
    )
]
SHEET = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
FACTS = {
    "eligible electorate": "electorate",
    "votes polled": "polled",
    "invalid votes": "invalid",
    "invaild votes": "invalid",
    "total valid votes": "valid",
}


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", plain.replace("&", " and ")))


WIKIPEDIA = "https://en.wikipedia.org/w/index.php?title={}_(Assembly_constituency)&action=raw"
CONSTITUENCIES = (
    "Belfast East",
    "Belfast North",
    "Belfast South",
    "Belfast West",
    "East Antrim",
    "East Londonderry",
    "Fermanagh and South Tyrone",
    "Foyle",
    "Lagan Valley",
    "Mid Ulster",
    "Newry and Armagh",
    "North Antrim",
    "North Down",
    "South Antrim",
    "South Down",
    "Strangford",
    "Upper Bann",
    "West Tyrone",
)
ARK_SOUTH_ANTRIM_2007 = "https://www.ark.ac.uk/elections/07sa.htm"
SOUTH_ANTRIM_2007_COUNT_8 = {
    "McLaughlin": 5454,
    "McCrea": 5454,
    "Ford": 5495.04,
    "Burnside": 5454,
    "Clarke": 5543.71,
    "Burns": 5395.94,
    "Lucas": 4228.79,
}
INDEPENDENT_UNIONISTS_1998 = {
    ("Belfast North", "Agnew"),
    ("Upper Bann", "Watson"),
    ("North Antrim", "Wright"),
    ("Strangford", "Beattie"),
}


def fetch() -> None:
    for url in (*ARK.values(), *NICVA.values(), *EONI):
        raw.fetch(SOURCE, url, suffix=".rar" if "ark.ac.uk" in url else ".xlsx" if url.endswith(".xlsx") else ".zip")
    raw.fetch(SOURCE, ARK_SOUTH_ANTRIM_2007, suffix=".html")
    for constituency in CONSTITUENCIES:
        raw.fetch(SOURCE, WIKIPEDIA.format(constituency.replace(" ", "_")), suffix=".txt")


def workbook(path: Path) -> dict[int, dict[str, str | float]]:
    with zipfile.ZipFile(path) as z:
        strings = [
            "".join(t.text or "" for t in si.iter(f"{SHEET}t"))
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(f"{SHEET}si")
        ]
        rows: dict[int, dict[str, str | float]] = {}
        for cell in ET.fromstring(z.read("xl/worksheets/sheet1.xml")).iter(f"{SHEET}c"):
            value = cell.find(f"{SHEET}v")
            if value is None or value.text is None or cell.get("t") == "e":
                continue
            column = re.match(r"[A-Z]+", cell.get("r")).group()
            rows.setdefault(int(cell.get("r")[len(column) :]), {})[column] = (
                strings[int(value.text)]
                if cell.get("t") == "s"
                else value.text
                if cell.get("t") in ("str", "b")
                else float(value.text)
            )
    return rows


def number(cell: str | None) -> float:
    if cell is None:
        return 0.0
    text = cell.strip().replace(",", "")
    text = re.sub(r"\s+", "", text)
    return 0.0 if re.fullmatch(r"[-—–_.]*", text) else float(text)


def ark_sheet(year: int, path: Path) -> dict[str, object]:
    sheet = pl.read_excel(path, has_header=False, infer_schema_length=0)
    rows = [list(row) for row in sheet.iter_rows()]
    stage_cols: dict[int, int] = {1: 4}
    for index, label in enumerate(rows[0]):
        found = re.match(r"stage\s*(?:no\.?)?\s*(\d+)", (label or "").strip().lower())
        if index > 4 and found is not None and int(found.group(1)) > 1:
            stage_cols[int(found.group(1))] = index
    names = [(row[3] or "").strip().lower() for row in rows]
    non_transferable = next(index for index, name in enumerate(names) if name.startswith("non"))
    totals = next(index for index, name in enumerate(names) if name in ("total", "totals"))
    blank = (None, "", " ")

    def cell(row: list[str | None], column: int) -> str | None:
        return row[column] if column < len(row) else None

    for stage, column in stage_cols.items():
        if stage > 1 and (
            (cell(rows[1], column) or "").strip().lower() == "recvd"
            or (cell(rows[totals], column) in blank and cell(rows[totals], column + 1) not in blank)
        ):
            stage_cols[stage] = column + 1
    body = rows[2:non_transferable]
    stages = sorted(
        stage
        for stage, column in stage_cols.items()
        if cell(rows[totals], column) not in blank
        or any(cell(row, column) not in blank + ("-",) and (cell(row, 1) or "").strip().isdigit() for row in body)
    )
    candidates = []
    for row in rows[2:non_transferable]:
        if row[2] is None or not (row[1] or "").strip().isdigit():
            continue
        candidates.append(
            {
                "name": row[2].strip(),
                "party": re.sub(r"\s+", " ", (row[3] or "").strip()),
                "elected": False,
                "row": row,
                "totals": {stage: number(cell(row, stage_cols[stage])) for stage in stages},
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
        value = next((cell.strip().replace(",", "") for cell in row[position + 1 :] if cell is not None), "")
        if re.fullmatch(r"\d{1,3}\.\d{3}", value):
            value = value.replace(".", "")
        if not re.fullmatch(r"[\d.]+", value):
            continue
        if "electorate" in label:
            facts["electorate"] = float(value)
        elif "polled" in label:
            facts["polled"] = float(value)
        elif "invalid" in label or "invaild" in label:
            facts["invalid"] = float(value)
        elif "valid" in label:
            facts["valid"] = float(value)
        elif "members" in label or "to be elected" in label:
            facts["seats"] = float(value)
        elif "quota" in label:
            facts["quota"] = float(value)
    seats = int(facts["seats"])
    valid = facts.get("valid") or sum(candidate["totals"][1] for candidate in candidates)
    quota = (
        facts.get("quota") if facts.get("quota") and abs(facts["quota"] - (valid // (seats + 1) + 1)) < 1 else None
    ) or valid // (seats + 1) + 1
    for candidate in candidates:
        held = None
        for stage in stages:
            amount = candidate["totals"][stage]
            if held is not None and amount < quota - 0.01:
                candidate["totals"][stage] = held
            elif held is None and amount >= quota - 0.01 or held is not None:
                held = amount
    for before, stage in itertools.pairwise(stages):
        column = stage_cols[stage]
        gap = [
            candidate
            for candidate in candidates
            if candidate["totals"][before] > 0
            and cell(candidate["row"], column) in blank
            and cell(candidate["row"], column - 1) not in blank + ("-",)
        ]
        previous = cell(rows[non_transferable], stage_cols[before])
        spare = (
            number(cell(rows[non_transferable], column)) if cell(rows[non_transferable], column) not in blank else None
        )
        if len(gap) == 1 and spare is None and previous not in blank:
            spare = number(previous) + number(cell(rows[non_transferable], column - 1))
        if len(gap) == 1 and spare is not None:
            others = sum(candidate["totals"][stage] for candidate in candidates if candidate is not gap[0])
            gap[0]["totals"][stage] = round(valid - others - spare, 2)
    for candidate in candidates:
        flag = cell(next(row for row in body if (cell(row, 2) or "").strip() == candidate["name"]), 0) or ""
        candidate["flagged"] = flag.strip() not in ("", "Excluded")
        candidate["elected"] = max(candidate["totals"].values()) >= quota - 0.01
    standing = sorted(
        (candidate for candidate in candidates if not candidate["elected"] and candidate["totals"][stages[-1]] > 0),
        key=lambda candidate: -candidate["totals"][stages[-1]],
    )
    for candidate in standing[: max(seats - sum(candidate["elected"] for candidate in candidates), 0)]:
        candidate["elected"] = True
    chosen = {candidate["name"] for candidate in candidates if candidate["elected"]}
    printed = {candidate["name"] for candidate in candidates if candidate["flagged"]}
    if len(chosen) != seats or not printed <= chosen:
        raise ValueError(
            f"{year} {path.stem}: derived {sorted(chosen)} against printed {sorted(printed)} for {seats} seats"
        )
    exhausted = {stage: number(cell(rows[non_transferable], stage_cols[stage])) for stage in stages}
    return {"facts": facts, "candidates": candidates, "exhausted": exhausted, "stages": stages}


def extract(archive: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".rar":
        subprocess.run(["unar", "-q", "-f", "-o", str(target), str(archive)], check=True)
    else:
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(target)


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    units, totals = [], []
    for year, url in ARK.items():
        folder = PARSED / f"x{year}"
        extract(raw.cached(SOURCE, url).path, folder)
        for path in sorted(folder.rglob(f"as{str(year)[2:]}-*.xls*")):
            if "overall" in path.stem.lower():
                continue
            constituency = re.sub(r"[-_]+", " ", path.stem.split("-", 1)[1]).removesuffix(" corrected")
            parsed = ark_sheet(year, path)
            if (year, constituency) == (2007, "South Antrim"):
                for candidate in parsed["candidates"]:
                    candidate["totals"][8] = SOUTH_ANTRIM_2007_COUNT_8.get(candidate["name"], 0.0)
            facts = parsed["facts"]
            units.append({"year": year, "constituency": constituency, **facts})
            full_names: dict[int, str] = {}
            if year == 2007:
                section = raw.cached(SOURCE, WIKIPEDIA.format(constituency.replace(" ", "_"))).path.read_text(
                    encoding="utf-8"
                )
                section = section.split("====2007====", 1)[1].split("\n====", 1)[0]
                for block in section.split("STV Election box candidate2")[1:]:
                    line = re.search(r"\|\s*candidate\s*=\s*([^\n]*)", block)
                    link = (
                        None
                        if line is None
                        else re.sub(
                            r"\'{2,}", "", re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", line.group(1))
                        ).strip()
                    )
                    first = re.search(r"count1\s*=\s*\'*([\d,]+)", block)
                    if link and first is not None:
                        full_names[int(first.group(1).replace(",", ""))] = link
            for position, candidate in enumerate(parsed["candidates"], start=1):
                full = full_names.get(int(candidate["totals"][1]))
                if full is not None and (
                    fold(candidate["name"]) in fold(full)
                    or difflib.SequenceMatcher(None, fold(candidate["name"]), fold(full.split()[-1])).ratio() >= 0.6
                ):
                    candidate["name"] = full
                if (year, constituency, candidate["name"].split(",")[0].strip()) in {
                    (1998, *key) for key in INDEPENDENT_UNIONISTS_1998
                }:
                    candidate["party"] = "Independent Unionist"
                for stage, value in candidate["totals"].items():
                    totals.append(
                        {
                            "year": year,
                            "constituency": constituency,
                            "number": position,
                            "name": candidate["name"],
                            "party": candidate["party"],
                            "elected": candidate["elected"],
                            "incumbent": None,
                            "gender": None,
                            "stage": stage,
                            "votes": value,
                        }
                    )
    for year, url in NICVA.items():
        folder = PARSED / f"x{year}"
        extract(raw.cached(SOURCE, url).path, folder)
        for facts_path in sorted(folder.rglob("ConstituencyCount.csv")):
            facts = pl.read_csv(facts_path).row(0, named=True)
            constituency = facts["Constituency_Name"]
            units.append(
                {
                    "year": year,
                    "constituency": constituency,
                    "electorate": facts["Total_Electorate"],
                    "polled": facts["Total_Poll"],
                    "invalid": facts["Spoiled"],
                    "valid": facts["Valid_Poll"],
                    "quota": facts["Quota"],
                    "seats": facts["Number_Of_Seats"],
                }
            )
            people = pl.read_csv(
                facts_path.with_name("Candidate.csv"),
                infer_schema_length=0,
                schema_overrides={"Candidate_Id": pl.Int64},
            )
            people = people.with_columns(
                *(
                    pl.lit(None).alias(column)
                    for column in ("Outgoing_Member", "Gender")
                    if column not in people.columns
                )
            ).select("Candidate_Id", "Outgoing_Member", "Gender")
            for line in (
                pl.read_csv(
                    facts_path.with_name("Count.csv"),
                    infer_schema_length=0,
                    schema_overrides={"Candidate_Id": pl.Int64},
                )
                .join(people, on="Candidate_Id", how="left")
                .iter_rows(named=True)
            ):
                totals.append(
                    {
                        "year": year,
                        "constituency": constituency,
                        "number": line["Candidate_Id"],
                        "name": f"{line['Surname']}, {line['Firstname']}",
                        "party": line["Party_Name"],
                        "elected": line["Status"] == "Elected",
                        "incumbent": None if line["Outgoing_Member"] is None else line["Outgoing_Member"] == 1,
                        "gender": line["Gender"],
                        "stage": int(line["Count_Number"]),
                        "votes": float(line["Total_Votes"] or 0),
                    }
                )
    first = {}
    for line in totals:
        if line["stage"] == 1:
            first[(line["year"], line["constituency"])] = (
                first.get((line["year"], line["constituency"]), 0.0) + line["votes"]
            )
    for unit in units:
        if not 2003 <= unit["year"] <= 2017:
            continue
        page = raw.cached(SOURCE, WIKIPEDIA.format(unit["constituency"].replace(" ", "_"))).path.read_text(
            encoding="utf-8"
        )
        found = re.search(rf"====\s*{unit['year']}\s*====(.*?)STV Election box end2(.*?)\n\}}\}}", page, re.DOTALL)
        box = {}
        if found is not None:
            for key, value in re.findall(r"\|\s*(electorate|valid|spoilt|turnout)\s*=\s*([\d,]+)", found.group(2)):
                box[key] = float(value.replace(",", ""))
        total = first[(unit["year"], unit["constituency"])]
        printed = unit.get("valid")
        confirmed = box.get("valid") is not None and abs(box["valid"] - total) <= 0.5
        if confirmed and (printed is None or abs(printed - total) > 25):
            unit["valid"], unit["polled"], unit["invalid"] = box["valid"], box.get("turnout"), box.get("spoilt")
        for key, field in (("electorate", "electorate"), ("turnout", "polled"), ("spoilt", "invalid")):
            if unit.get(field) is None and box.get(key) is not None:
                unit[field] = box[key]
        if unit.get("valid") is None and confirmed:
            unit["valid"] = box["valid"]
    skipped = []
    for url in EONI:
        rows = workbook(raw.cached(SOURCE, url).path)
        header = next(number for number, row in rows.items() if row.get("C") == "Candidate")
        constituency = str(rows[2]["D"]).strip()
        candidates = []
        for number in range(header + 1, header + 40):
            if not isinstance(rows.get(number, {}).get("C"), str) or not rows[number]["C"]:
                break
            candidates.append(rows[number])
        tail = {}
        for number in range(header + len(candidates) + 1, header + len(candidates) + 40):
            if (row := rows.get(number, {})).get("D") in ("Non transferable", "Totals"):
                tail.setdefault(row["D"], row)
        columns = [
            column
            for column in ("E", "G", "I", "K", "M", "O", "Q", "S", "U", "W")
            if (tail["Totals"].get(column) or 0) > 0
        ]
        valid = rows[4]["G"]
        tying = []
        for column in columns:
            if (
                abs(sum(row.get(column, 0) for row in candidates) + tail["Non transferable"].get(column, 0) - valid)
                > 0.5
            ):
                break
            tying.append(column)
        cut = None if len(tying) == len(columns) else len(tying) + 1
        columns = tying
        ties = len(columns) > 0
        flagged = [row for row in candidates if row.get("A") == "Elected"]
        seats = int(rows[3]["G"])
        if not ties or len(flagged) not in (seats, seats - 1):
            skipped.append(constituency)
            continue
        last_seat = None
        if len(flagged) == seats - 1:
            last_seat = max((row for row in candidates if row.get("A") != "Elected"), key=lambda row: row[columns[-1]])
            last_seat["A"] = "Elected"
            last_seat = last_seat["C"]
        units.append(
            {
                "year": 2022,
                "constituency": constituency,
                "electorate": rows[3]["D"],
                "polled": rows[4]["D"],
                "invalid": rows[3]["J"],
                "valid": valid,
                "quota": rows[3]["M"],
                "seats": seats,
                "last_seat": last_seat,
                "cut_stage": cut,
            }
        )
        for position, row in enumerate(candidates, start=1):
            for stage, column in enumerate(columns, start=1):
                totals.append(
                    {
                        "year": 2022,
                        "constituency": constituency,
                        "number": position,
                        "name": row["C"],
                        "party": row["D"],
                        "elected": row.get("A") == "Elected",
                        "incumbent": None,
                        "gender": None,
                        "stage": stage,
                        "votes": float(row.get(column, 0)),
                    }
                )
    print("not loaded, stages do not tie or elected not declared:", skipped)
    quotas = {
        (unit["year"], unit["constituency"]): unit.get("quota") or unit["valid"] // (unit["seats"] + 1) + 1
        for unit in units
    }
    reached: set[tuple[int, str, int]] = set()
    for line in sorted(totals, key=lambda line: line["stage"]):
        key = (line["year"], line["constituency"], line["number"])
        if not line["elected"]:
            continue
        if line["votes"] >= quotas[key[:2]] - 0.5:
            reached.add(key)
        elif key in reached and line["votes"] > 0:
            raise ValueError(f"{key[0]} {key[1]}: {line['name']} falls below the quota after reaching it")
    pl.DataFrame(units, infer_schema_length=None).write_parquet(PARSED / "units.parquet")
    pl.DataFrame(totals, infer_schema_length=None).with_columns(
        pl.col("elected").any().over("year", "constituency", "number")
    ).write_parquet(PARSED / "totals.parquet")
    print(pl.DataFrame(units, infer_schema_length=None).group_by("year").len().sort("year"))


PARTIES = (
    (r"women", "NI Women's Coalition"),
    (r"d\.?u\.?p|demo?cratic unionist|demoratic unionist", "Democratic Unionist Party"),
    (r"united unionist coalition", "United Unionist Assembly Party"),
    (r"ulster unionist|^u\.?u\.?p\b|^uup$|ufster unionist|pariy", "Ulster Unionist Party"),
    (r"s\.?d\.?l\.?p|social democratic and labour", "Social Democratic and Labour Party"),
    (r"sinn f", "Sinn Féin"),
    (r"alliance party|^alliance", "Alliance Party"),
    (r"p\.?u\.?p\b|progressive unionist", "Progressive Unionist Party"),
    (r"worker", "Workers' Party"),
    (r"u\.?d\.?p\b|ulster democratic", "Ulster Democratic"),
    (r"uk unionist|u\.k\.? unionist|u\.?k\.?u\.?p|united kingdom unionist|untied kingdom", "UK Unionist Party"),
    (r"people before profit", "People Before Profit Alliance"),
    (r"traditional unionist|^tuv\b", "Traditional Unionist Voice"),
    (r"aont", "Aontú"),
    (r"^heritage party", "Heritage Party"),
    (r"^resume", "Resume NI"),
    (r"^irsp|irish republican socialist", "Irish Republican Socialist Party"),
    (r"uk independence", "UK Independence Party"),
    (r"conserv", "Conservative"),
    (r"^labour alternative|cross-community labour", "Cross-Community Labour Alternative"),
    (r"labour representation", "Northern Ireland Labour Representation Committee"),
    (r"^labour( party)?$", "Labour"),
    (r"green", "Green Party Northern Ireland"),
    (r"natural law", "Natural Law Party"),
    (r"energy 106", "Energy 106 Party"),
    (r"socialist environmental|social environmental|^sea$", "Socialist Environmental"),
    (r"socialist party", "Socialist Party"),
    (r"making politicians history|make politicians history", "Make Politicians History"),
    (r"procapitalism", "Procapitalism"),
    (r"british nationalist", "British National Party"),
    (r"animal welfare", "Animal Welfare Party"),
    (r"cannabis", "Cannabis Is Safer Than Alcohol"),
    (r"democracy first", "Democracy First"),
    (r"northern ireland first", "Northern Ireland First"),
    (r"south belfast unionists", "South Belfast Unionists"),
    (r"citizens independent", "Citizens Independent Social Thought Alliance"),
    (r"vote for yourself", "Vote For Yourself"),
    (r"ulster third way", "Ulster Third Way"),
    (r"anti-agreement|n\.i\. unionist", "Northern Ireland Unionist Party"),
    (r"ulster independence", "Ulster Independence"),
    (r"community and enviro", "Community and Environmental Conservation Campaign"),
    (r"independ|ind\. |^unionist$|^$|economics|ulster's ind|nationalist", None),
)


RELABELED = {
    (2003, "Hamilton, Mary Scott"): (
        "Ulster Unionist Party",
        (
            "Her ballot paper describes her as an independent; ARK and the Electoral Office for Northern Ireland "
            "count her as the Ulster Unionist candidate, the party she represented in the Assembly from 1998."
        ),
    ),
}


def party_of(description: str) -> str | None:
    text = re.sub(r"\s+", " ", description.strip().lower())
    for pattern, canonical in PARTIES:
        if re.search(pattern, text):
            return canonical
    raise ValueError(f"no party for {description!r}")


def display(name: str) -> str:
    name = re.sub(r"\s+", " ", re.sub(r"##\s*unsure\s*(?:##)?|##", "", name, flags=re.IGNORECASE)).strip()
    surname, _, given = name.partition(",")
    words = []
    for word in given.replace(",", " ").split():
        words.append(word.capitalize() if word.isupper() and len(word) > 3 and "." not in word else word)
    for word in surname.split():
        if word.isupper() and len(word) > 1 and "." not in word:
            word = re.sub(
                r"\b(Mc|O['’])([a-z])", lambda found: found.group(1) + found.group(2).upper(), word.capitalize()
            )
        words.append(word)
    return re.sub(
        r"\bO(Dowd|Neill|Connor|Hara|Loan|Brien|Donnell|Rourke|Kane|Meara|Reilly|Rawe|Fachina|Callaghan|Driscoll|Hagan)\b",
        lambda found: "O'" + found.group(1).capitalize(),
        " ".join(words),
        flags=re.IGNORECASE,
    )


def emit() -> Path:
    units = pl.read_parquet(PARSED / "units.parquet")
    totals = pl.read_parquet(PARSED / "totals.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Northern Ireland Assembly elections 1998 to 2022 by constituency with every STV count, from ARK, Elections NI, and the Electoral Office",
            polity="GB-NIR",
        )

        def name(entity: UUID, text: str, kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind=kind, is_preferred=kind == "official")

        nir = cs.polity("GB-NIR")
        gb = cs.polity("GB")
        if conn.execute("select 1 from scheme where code = 'gb.nia'").fetchone() is None:
            cs.add("scheme", code="gb.nia", polity_id=nir, kind="electoral_district", rank=1, noun="constituency")
        system = cs.key("gb.electoral_system", "stv")
        if system in cs.minted:
            raise ValueError("publish the stv electoral system first")
        body = cs.key("gb.body", "ni-assembly")
        if body in cs.minted:
            cs.add("body", id=body, polity_id=nir, kind="unicameral")
            name(body, "Northern Ireland Assembly")
        for seats, begin, end in ((108, DAYS[1998], DAYS[2017]), (90, DAYS[2017], None)):
            cs.add(
                "body_size",
                body_id=body,
                seats=seats,
                valid_from=begin,
                valid_from_precision="day",
                valid_to=end,
                valid_to_precision="day",
            )
        known: dict[str, UUID] = {}
        for text, party in conn.execute(
            "select n.text, p.id from party p join polity y on y.id = p.polity_id"
            " join name n on n.entity_id = p.id where y.iso_code = 'GB' order by p.id desc"
        ).fetchall():
            known[fold(text)] = party
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
                if fold(canonical) in known:
                    parties[canonical] = known[fold(canonical)]
                else:
                    parties[canonical] = cs.key("gb.party", slug(canonical))
                    if parties[canonical] in cs.minted:
                        cs.add("party", id=parties[canonical], kind="party", polity_id=gb)
                        name(parties[canonical], canonical)
            return parties[canonical]

        sources = {}
        for year in DAYS:
            ark, official = year in ARK, year == 2022
            publisher = (
                "ARK Northern Ireland Elections"
                if ark
                else "Electoral Office for Northern Ireland"
                if official
                else "Elections NI (NICVA)"
            )
            sources[year] = cs.source(publisher, f"Northern Ireland Assembly election {year}")
            cs.add(
                "source",
                id=sources[year],
                kind="academic" if ark else "official_body" if official else "compendium",
                publisher=publisher,
                title=f"Northern Ireland Assembly election {year}: count by count results",
                url=EONI_PAGE
                if official
                else "https://www.ark.ac.uk/elections/gexcel.htm"
                if ark
                else "https://www.electionsni.org/data/",
                license="Open Government Licence v3.0" if official else None if ark else "CC BY-SA 4.0",
                license_url=(
                    "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
                    if official
                    else None
                    if ark
                    else "https://creativecommons.org/licenses/by-sa/4.0/"
                ),
                attribution=(
                    "Result sheets from the Electoral Office for Northern Ireland, under the Open Government Licence v3.0."
                    if official
                    else "Count-by-count spreadsheets from ARK Northern Ireland Elections (ark.ac.uk/elections)."
                    if ark
                    else "Contains data from the Electoral Office for Northern Ireland (OGL) and Democracy Club, via electionsni.org, CC BY-SA 4.0."
                ),
            )
            for url in EONI if official else [(ARK if ark else NICVA)[year]]:
                cs.artifact(raw.cached(SOURCE, url), sources[year])
            if year == 2007:
                cs.artifact(raw.cached(SOURCE, ARK_SOUTH_ANTRIM_2007), sources[year])

        names_source = cs.source("Wikipedia", "Northern Ireland Assembly constituencies")
        cs.add(
            "source",
            id=names_source,
            kind="community",
            publisher="Wikipedia",
            title="Northern Ireland Assembly constituency articles",
            url="https://en.wikipedia.org/wiki/Category:Northern_Ireland_Assembly_constituencies",
            license="CC BY-SA 4.0",
            license_url="https://creativecommons.org/licenses/by-sa/4.0/",
            attribution="Candidates' full names from Wikipedia's Northern Ireland Assembly constituency articles, CC BY-SA 4.0.",
        )
        for constituency in CONSTITUENCIES:
            cs.artifact(raw.cached(SOURCE, WIKIPEDIA.format(constituency.replace(" ", "_"))), names_source)

        areas = {
            (code, name_): (area, valid_from, valid_to)
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
        for year, day in DAYS.items():
            election = cs.key("gb.election", f"{day}:ni-assembly")
            cs.add("election", id=election, polity_id=nir, kind="general", date_from=day, date_to=day, status="held")
            name(election, f"{year} Northern Ireland Assembly election")
            path = f"gb/northern-ireland/{day}-general"
            cs.add("slug", path=path, entity_id=election)
            for unit_row in units.filter(pl.col("year") == year).sort("constituency").iter_rows(named=True):
                label = unit_row["constituency"]
                match = [
                    (code, area)
                    for (code, text), (area, begin, end) in areas.items()
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
                contest_key = f"{day}:ni-assembly:{slug(label)}"
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
                name(contest, f"{year} Northern Ireland Assembly election in {label}")
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
                lines = totals.filter((pl.col("year") == year) & (pl.col("constituency") == label))
                stages = sorted(lines["stage"].unique().to_list())
                sums = [lines.filter(pl.col("stage") == stage)["votes"].sum() for stage in stages]
                valid = unit_row["valid"]
                if valid is None and unit_row["polled"] is not None and unit_row["invalid"] is not None:
                    valid = unit_row["polled"] - unit_row["invalid"]
                polled = unit_row["polled"]
                quota = unit_row["quota"]
                caveats = []
                if valid is not None and abs(sums[0] - valid) > 0.5:
                    if abs(sums[0] - valid) <= 25:
                        caveats.append(
                            f"The declaration prints {valid:,.0f} valid votes, but the candidates' first preferences sum to "
                            f"{sums[0]:,.0f}. Both are stored as published."
                        )
                    else:
                        caveats.append(
                            f"The source prints {valid:,.0f} valid votes, but the candidates' first preferences sum to "
                            f"{sums[0]:,.0f}. The printed valid and total poll figures are left out because they contradict the count."
                        )
                        valid = polled = None
                if quota is not None and quota != int(sums[0] // (seats + 1)) + 1:
                    caveats.append(
                        f"The source prints a quota of {quota:,.0f}, but the first preferences give "
                        f"{int(sums[0] // (seats + 1)) + 1:,}. The printed quota is left out."
                    )
                    quota = None
                if any(after > before + 0.5 for before, after in itertools.pairwise(sums)):
                    caveats.append(
                        "The candidates' totals rise between two stages in the source; the stage totals are stored as published."
                    )
                if (year, label) == (2007, "South Antrim"):
                    caveats.append(
                        "The spreadsheet ends at count 7; count 8 (Burnside's surplus, which elected Clarke and Burns) is from ARK's "
                        "web page for the constituency."
                    )
                if year == 2007:
                    caveats.append(
                        "The source gives surnames only; the candidates' full names are from Wikipedia's constituency article, "
                        "matched by surname and first-preference votes."
                    )
                if unit_row.get("cut_stage"):
                    caveats.append(
                        f"The Electoral Office's result sheet does not distribute the votes of the candidate excluded at stage "
                        f"{unit_row['cut_stage']}, so its counts end after stage {unit_row['cut_stage'] - 1}. The elected candidates are as "
                        "declared; their final totals are not in the source."
                    )
                if unit_row.get("last_seat"):
                    caveats.append(
                        f"The result sheet flags {seats - 1} candidates as elected; the last seat went to {display(unit_row['last_seat'])}, "
                        "the highest remaining candidate at the final count, as the Electoral Office declared."
                    )
                for text in caveats:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=text)
                for measure, amount in (
                    (REGISTERED, unit_row["electorate"]),
                    (BALLOTS, polled),
                    (VALID, valid),
                    (INVALID, unit_row["invalid"]),
                    (QUOTA, quota),
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
                running = dict.fromkeys(stages, 0.0)
                for candidate in (
                    lines.select("number", "name", "party", "elected", "incumbent", "gender")
                    .unique(maintain_order=True, subset=["number"])
                    .iter_rows(named=True)
                ):
                    series = {
                        row["stage"]: row["votes"]
                        for row in lines.filter(pl.col("number") == candidate["number"]).iter_rows(named=True)
                    }
                    relabeled = RELABELED.get((year, candidate["name"]))
                    canonical = relabeled[0] if relabeled else party_of(candidate["party"])
                    owner = party_id(canonical)
                    independent = canonical is None and (
                        "independ" in candidate["party"].lower() or candidate["party"].strip() in ("", "Unionist")
                    )
                    full = display(candidate["name"]) if "," in candidate["name"] else candidate["name"]
                    option_key = f"{contest_key}:{candidate['number']}"
                    option = cs.key("gb.ni_assembly.option", option_key)
                    person = cs.key("gb.ni_assembly.person", option_key)
                    if person not in made:
                        made.add(person)
                        cs.add("person", id=person, gender={"M": "male", "F": "female"}.get(candidate["gender"]))
                        name(person, full)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=owner,
                        ballot_label=full,
                    )
                    if relabeled:
                        cs.add("note", entity_id=option, kind="context", body_md=relabeled[1])
                    cs.add(
                        "candidacy",
                        id=cs.key("gb.ni_assembly.candidacy", option_key),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=candidate["name"],
                        member_of_party_id=owner,
                        is_independent=independent,
                        is_incumbent=candidate["incumbent"],
                    )
                    eliminated = False
                    for stage in stages:
                        if eliminated:
                            break
                        amount = round(series[stage], 2)
                        running[stage] += amount
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=VOTES,
                            count_round=stage,
                            flags=0,
                            value=amount,
                        )
                        eliminated = amount == 0 and stage > 1 and not candidate["elected"]
                    final = series[stages[-1]]
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="elected" if candidate["elected"] else ("lost" if final > 0 else "eliminated"),
                    )
                for stage in stages if valid is not None else ():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=EXHAUSTED,
                        count_round=stage,
                        flags=DERIVED,
                        value=round(valid - running[stage], 2),
                    )
        return cs.write()
