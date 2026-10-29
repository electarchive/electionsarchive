import json
import os
import re
import subprocess
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import polars as pl

from ea.sources.us import nyc_recap
from ea.store import DATA

SOURCE = "us.nyc_recap_wayback"
PARSED = DATA / "parsed" / SOURCE
VERSION = "1"
NOTE = (
    "Returns from the Board of Elections' certified Statement and Return recaps, read from archived copies of the Board's "
    "own results pages. Assembly district returns are one line per ballot line as New York's fusion ballot prints them, "
    "with scattered write-ins as one line; where the Board printed election districts, they are added up to assembly "
    "districts, and the 2001 returns, which follow the 1992 districts, are by county. Ballots cast are the public "
    "counter, absentee and military, federal, emergency, and affidavit ballots the recap prints. A report's assembly "
    "district lines sum to its total."
)
COUNTIES = ("NEW YORK", "BRONX", "KINGS", "QUEENS", "RICHMOND")
LINES = {
    "YORK RIGHT TO LIFE": "RIGHT TO LIFE",
    "RIGHT TO": "RIGHT TO LIFE",
    "LIFE": "RIGHT TO LIFE",
    "TO": "RIGHT TO LIFE",
    "GREEN LIFE": "GREEN",
    "YORK GREEN": "GREEN",
    "YORK FUSION": "FUSION",
    "NEW MARIJUANA": "MARIJUANA REFORM",
    "FAMILIES": "WORKING FAMILIES",
    "REFORM": "BUCHANAN REFORM",
    "UNITY": "UNITY PARTY",
    "PEOPLES": "PEOPLES VOICE",
    "MOST": "MOST QUALIFIED",
    "NO": "NO NEW TAXES",
    "NEIGHBRHOOD": "NEIGHBORHOOD FIRST",
    "COMMUNITY": "COMMUNITY FIRST",
    "UNITED": "UNITED DISTRICT",
    "BX": "BX ALLIANCE",
}
ROW = re.compile(rf"^\s*(?P<label>\d+(?:ST|ND|RD|TH)|{'|'.join(COUNTIES)}|TOTAL)\b(?P<rest>.*\d.*)$")
KIND = re.compile(r"(GENERAL|RUNOFF|RUN-OFF|PRIMARY|PRESIDENTIAL PRIMARY|SPECIAL)[A-Z ]*ELECTION")
RECAP = re.compile(r"R E C A P I T U L A T I O N")
CODE = re.compile(r"(?<![\w-])\d{1,3}[A-Z](?![\w-])")
PARTY_WORDS = re.compile(
    r"\b(DEMOCRATIC|REPUBLICAN|CONSERVATIVE|WORKING FAMILIES|INDEPENDENCE|LIBERAL|GREEN|LIBERTARIAN)(?: PARTY)?\b"
)
TICKETS = re.compile(r"GOVERNOR|PRESIDENT AND VICE")
GENERAL_DAYS = {
    1999: "1999-11-02",
    2000: "2000-11-07",
    2001: "2001-11-06",
    2002: "2002-11-05",
    2003: "2003-11-04",
    2004: "2004-11-02",
}


def words(line: str, left: float) -> list[tuple[float, str]]:
    return [
        ((m.start() + m.end()) / 2, m.group()) for m in re.finditer(r"\S+", line) if (m.start() + m.end()) / 2 >= left
    ]


def align(found: list[tuple[int, int, str]], centers: list[float]) -> list[list[str]]:
    """Split the words of one header line into one run of neighbors per column, so that each run sits centered on its column."""
    count = len(found)
    inf = float("inf")

    def cost(first: int, last: int, column: int) -> float:
        if first == last:
            return 8
        return sum(abs((a + b) / 2 - centers[column]) for a, b, _ in found[first:last])

    best = [[inf] * (count + 1) for _ in range(len(centers) + 1)]
    back = [[0] * (count + 1) for _ in range(len(centers) + 1)]
    best[0][0] = 0
    for column in range(1, len(centers) + 1):
        for end in range(count + 1):
            for start in range(end + 1):
                if best[column - 1][start] == inf:
                    continue
                total = best[column - 1][start] + cost(start, end, column - 1)
                if total < best[column][end]:
                    best[column][end] = total
                    back[column][end] = start
    runs: list[list[str]] = [[] for _ in centers]
    end = count
    for column in range(len(centers), 0, -1):
        start = back[column][end]
        runs[column - 1] = [text for _, _, text in found[start:end]]
        end = start
    return runs


def read_page(page: str) -> dict[str, object] | None:
    lines = page.splitlines()
    head = [i for i, line in enumerate(lines) if "OF THE VOTES FOR THE OFFICE OF" in line]
    if (
        not head
        or not any(ROW.match(line) for line in lines)
        or "TOTALS FOR ALL CANDIDATES" in page
        or "CANDIDATE NAME" in page
    ):
        return None
    start = head[0]
    title = " ".join(lines[:3])
    election = KIND.search(title.upper())
    scope = next(
        (
            m[0]
            for x in lines[:3]
            if "ELECTION" not in x
            and (m := re.search(r"(?:NEW YORK|BRONX|KINGS|QUEENS|RICHMOND) COUNTY|CITY OF NEW YORK", x))
        ),
        "",
    )
    tag = re.search(r"PAGE:\s*(\w+)", title)
    office = []
    party = None
    end = start + 1
    for end in range(start + 1, len(lines)):
        line = lines[end]
        if "NO. OF CANDIDATES" in line or RECAP.search(line) or not line.strip():
            break
        found = PARTY_WORDS.fullmatch(line.strip())
        if found:
            party = found[1]
            continue
        office.append(re.sub(r"_+", "", line).strip())
    seats = re.search(r"CANDIDATES TO BE ELECTED:\s*(\d+)", page)
    for line in lines[end : end + 6]:
        if RECAP.search(line):
            found = PARTY_WORDS.search(line.split("R E C")[0])
            if found:
                party = found[1]
    block = None
    for line in lines[end:]:
        if ROW.match(line):
            break
        if (found := re.fullmatch(r"\s*(\d+)(?:ST|ND|RD|TH) ASSEMBLY DISTRICT\s*", line)) is not None:
            block = int(found[1])
        if (found := PARTY_WORDS.fullmatch(line.strip())) is not None:
            party = found[1]
    data = [(i, ROW.match(line)) for i, line in enumerate(lines) if i > end and ROW.match(line)]
    first = next((i for i, m in data if m["label"] != "TOTAL"), data[0][0])
    data = [(i, m) for i, m in data if i >= first]
    header = [
        line
        for line in lines[end:first]
        if not RECAP.search(line)
        and "NO. OF CANDIDATES" not in line
        and not re.fullmatch(r"\s*(?:BOROUGH OF [A-Z ]+|[A-Z0-9 ]*(?:COUNTY|DISTRICT))\s*", line)
    ]
    header_text = "\n".join(header).upper()
    stars = bool(re.search(r"\*", lines[first]))
    rows = []
    for i, m in data:
        line = lines[i]
        rest = line[m.end("label") :]
        base = m.end("label")
        groups: list[list[tuple[int, int, int]]] = [[]]
        for token in re.finditer(r"\*|\d[\d,]*", rest):
            if token.group() == "*":
                groups.append([])
            else:
                groups[-1].append((base + token.start(), base + token.end(), int(token.group().replace(",", ""))))
        groups = [g for g in groups if g]
        if (
            m["label"] == "TOTAL"
            and line.rstrip().endswith("*")
            and i > 0
            and (above := re.findall(r"\d[\d,]*", lines[i - 1].rsplit("*", 1)[-1]))
        ):
            offset = lines[i - 1].rfind("*") + 1
            groups.append(
                [
                    (offset + t.start(), offset + t.end(), int(t.group().replace(",", "")))
                    for t in re.finditer(r"\d[\d,]*", lines[i - 1][offset:])
                ]
            )
        rows.append((m["label"], groups))
    unrec = "UNRE" in header_text or "CORD" in header_text
    tail = ("SCAT" in header_text) + (unrec if "VALID" in header_text or not stars else ("SCAT" in header_text))
    first_groups = rows[0][1]
    if stars:
        counters, valid, candidates = (
            ([], [], [])
            if len(first_groups) < 2
            else (first_groups[0], first_groups[1] if len(first_groups) == 3 else [], first_groups[-1])
        )
    else:
        counters, valid, candidates = [], [], first_groups[0]
    if not candidates:
        return None
    ncand = len(candidates) - tail
    left = candidates[0][1] - 13 if candidates else 0
    code_row = next((i for i, line in enumerate(header) if len(CODE.findall(line)) >= max(ncand, 1)), None)
    columns = defaultdict(lambda: [[] for _ in range(len(header))])
    spacing = (candidates[-1][1] - candidates[0][1]) / max(len(candidates) - 1, 1) if len(candidates) > 1 else 13
    centers = [(c[1] - spacing / 2, k) for k, c in enumerate(candidates)]
    if code_row is not None:
        found = [m.end() for m in CODE.finditer(header[code_row])]
        if len(found) == ncand:
            centers = [(x - 4.0, k) for k, x in enumerate(found)] + [
                (c[1] - 6, ncand + j) for j, c in enumerate(candidates[ncand:])
            ]
    for row_index, line in enumerate(header):
        if row_index == code_row:
            for k, found_code in enumerate(CODE.findall(line)[:ncand]):
                columns[k][row_index].append(found_code)
            continue
        if code_row is None:
            for m in re.finditer(r"\S+(?: \S+)*", line):
                if m.end() >= candidates[0][1] - 8:
                    columns[min(range(len(candidates)), key=lambda k: abs(candidates[k][1] + 1.5 - m.end()))][
                        row_index
                    ].append(m.group())
            continue
        for k, run in enumerate(
            align(
                [
                    (m.start(), m.end(), m.group())
                    for m in re.finditer(r"\S+", line)
                    if (m.start() + m.end()) / 2 >= left and m.group() != "OFFICE"
                ],
                [x for x, _ in centers],
            )
        ):
            if k < len(centers):
                columns[centers[k][1]][row_index].extend(run)
    labels = []
    for k in range(ncand):
        cells = [" ".join(w) for w in columns[k]]
        if code_row is not None:
            above = " ".join(c for c in cells[:code_row] if c)
            below = [c for c in cells[code_row + 1 :] if c]
            code = cells[code_row]
        else:
            above, below, code = "", [c for c in cells if c and not set(c) <= set("-*")], ""
        labels.append(
            {
                "party": (above or party or "").strip(),
                "name": (" / ".join(below) if TICKETS.search(" ".join(office)) else " ".join(below)).replace("*", "").strip(),
                "code": code,
            }
        )
    result = {
        "kind": election[1] if election else None,
        "scope": scope,
        "tag": tag[1] if tag else "1",
        "office": " | ".join(office),
        "seats": int(seats[1]) if seats else 1,
        "party": party,
        "block": block,
        "grain": "ed" if re.search(r"\bELEC", header_text) else "county" if "COUNTIES" in header_text else "ad",
        "unrec": unrec,
        "scat": "SCAT" in header_text,
        "labels": labels,
        "rows": [],
    }
    for label, groups in rows:
        if not groups:
            continue
        if stars:
            if len(groups) == 3:
                cells = [x[2] for x in groups[2]]
                counted = groups[1][0][2]
            elif len(groups) == 2:
                cells = [x[2] for x in groups[1]]
                counted = None
            else:
                continue
        else:
            cells = [x[2] for x in groups[0]]
            counted = None
        result["rows"].append(
            {
                "label": label,
                "cands": cells[:ncand],
                "tail": cells[ncand:] if tail else [],
                "valid": counted,
                "ballots": sum(x[2] for x in groups[0]) if len(groups) == 3 else None,
            }
        )
    return result


def read_file(path: Path) -> list[dict[str, object]]:
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout
    groups: list[dict[str, object]] = []
    for number, page in enumerate(text.split("\f"), start=1):
        found = read_page(page)
        if found is None:
            continue
        found["page"] = number
        found["letters"] = set()
        last = groups[-1] if groups else None
        same = last is not None and (last["office"], last["scope"], last["block"]) == (
            found["office"],
            found["scope"],
            found["block"],
        )
        if same and re.search(r"[A-Z]$", str(found["tag"])):
            seen: dict[str, int] = defaultdict(int)
            known = {}
            for row in last["rows"]:
                known[(row["label"], seen[row["label"]])] = row
                seen[row["label"]] += 1
            seen = defaultdict(int)
            for row in found["rows"]:
                if (row["label"], seen[row["label"]]) in known:
                    target = known[(row["label"], seen[row["label"]])]
                    target["cands"] += row["cands"]
                    target["tail"] += row["tail"]
                seen[row["label"]] += 1
            letter = str(found["tag"])[-1]
            if letter not in last["letters"]:
                last["letters"].add(letter)
                last["labels"] += found["labels"]
            last["unrec"] = last["unrec"] or found["unrec"]
            last["scat"] = last["scat"] or found["scat"]
            continue
        if same and not any(r["label"] == "TOTAL" for r in last["rows"]):
            last["rows"] += found["rows"]
            continue
        groups.append(found)
    return groups


def check(group: dict[str, object]) -> list[str]:
    problems = []
    rows = group["rows"]
    total = next((r for r in rows if r["label"] == "TOTAL"), None)
    body = [r for r in rows if r["label"] != "TOTAL"]
    if total is None:
        return ["no total row"]
    if len(total["cands"]) != len(group["labels"]):
        problems.append(f"{len(total['cands'])} columns for {len(group['labels'])} headers")
    for r in rows:
        spent = sum(r["cands"]) + sum(r["tail"]) if not group["unrec"] else sum(r["cands"]) + sum(r["tail"])
        if r["valid"] is not None and spent != r["valid"]:
            problems.append(f"row {r['label']} columns sum to {spent}, valid {r['valid']}")
    for r in rows:
        if not group["unrec"] and len(r["tail"]) == 2 and r["tail"][1] != sum(r["cands"]) + r["tail"][0]:
            problems.append(f"row {r['label']} total column {r['tail'][1]} against {sum(r['cands']) + r['tail'][0]}")
    if body:
        for k in range(len(total["cands"])):
            if sum(r["cands"][k] for r in body if len(r["cands"]) > k) != total["cands"][k]:
                problems.append(f"column {k} rows do not add to total")
    return problems


FILES = (
    ("2000/generalElection/allg2000.pdf", "2000-11-07", "General"),
    ("2001/primaryElection/2001p.pdf", "2001-09-25", "Primary"),
    ("2001/primaryElection/2001RunOff.pdf", "2001-10-11", "Primary Runoff"),
    ("2001/generalElection/General2001.pdf", "2001-11-06", "General"),
    ("2002/primaryElection/2002PrimaryRecapsAll.pdf", "2002-09-10", "Primary"),
    ("2002/general/G2002GeneralRecapsAll.pdf", "2002-11-05", "General"),
    ("2003/primary/2003PrimaryRecapsAll.pdf", "2003-09-09", "Primary"),
    ("2003/general/G2003Recaps.pdf", "2003-11-04", "General"),
    ("2004/primary/p2004bronx.pdf", "2004-09-14", "Primary"),
    ("2004/primary/p2004kings.pdf", "2004-09-14", "Primary"),
    ("2004/primary/p2004newyork.pdf", "2004-09-14", "Primary"),
    ("2004/primary/p2004queens.pdf", "2004-09-14", "Primary"),
    ("2004/primary/p2004richmond.pdf", "2004-09-14", "Primary"),
    ("2004/general/g2004recaps.pdf", "2004-11-02", "General"),
    ("2004/general/g2004aded.pdf", "2004-11-02", "General"),
    ("2001/special/bxcon15.pdf", "2001-02-20", "Special"),
    ("2002/special/S2002RESULTS20S.pdf", "2002-03-21", "Special"),
    ("2002/special/S2002RESULTS26S.pdf", "2002-03-21", "Special"),
    ("2002/special/S2002RESULTS56S.pdf", "2002-03-21", "Special"),
    ("2002/special/S2002RESULTS60S.pdf", "2002-03-21", "Special"),
    ("2002/special/queens_assembly_31st_dist.pdf", "2002-04-30", "Special"),
    ("2003/special/S2003ResultsBk55AD.pdf", "2003-02-25", "Special"),
    ("2003/special/S2003ResultsBx79AD.pdf", "2003-02-25", "Special"),
    ("2003/special/S2003ResultsBx18Council.pdf", "2003-02-25", "Special"),
    ("2003/special/S2003ResultsBk43Council.pdf", "2003-02-25", "Special"),
)
COUNTY_NAMES = {
    "NEW YORK COUNTY": "New York",
    "BRONX COUNTY": "Bronx",
    "KINGS COUNTY": "Kings",
    "QUEENS COUNTY": "Queens",
    "RICHMOND COUNTY": "Richmond",
}
OUT_OF_SCOPE = re.compile(r"DELEGATE|COMMITTEE|LEADER|CONVENTION|DEPUTY|ALTERNATE|\*\*\*")


def synth(office: str, county: str, seats: int, party: str | None) -> str | None:
    text = re.sub(r"\s+", " ", office.upper().replace("|", " "))
    found = re.search(r"(\d+)(?:ST|ND|RD|TH)\b", text)
    n = found[1] if found else None
    upper = county.upper()
    if text.startswith("GOVERNOR AND"):
        out = "GOVERNOR/LIEUTENANT GOVERNOR"
    elif text.startswith("PRESIDENT AND VICE"):
        out = "PRESIDENT/VICE PRESIDENT"
    elif text.startswith("UNITED STATE"):
        out = "UNITED STATES SENATOR"
    elif text.startswith(("STATE COMPTROLLER", "ATTORNEY GENERAL")):
        out = text.split(" OF THE")[0]
    elif text.startswith("REPRESENTATIVE IN CONGRESS") and n:
        out = f"REPRESENTATIVE IN CONGRESS ({n})"
    elif text.startswith("STATE SENATOR") and n:
        out = f"STATE SENATOR ({n})"
    elif text.startswith("MEMBER OF THE STATE ASSEMBLY") and n:
        out = f"MEMBER OF THE ASSEMBLY ({n})"
    elif text.startswith("MEMBER OF THE CITY COUNCIL") and n:
        out = f"MEMBER OF THE CITY COUNCIL ({n})"
    elif text.startswith("JUSTICES OF THE SUPREME COURT") and n:
        out = f"JUSTICE OF THE SUPREME COURT ({n}TH JUDICIAL DISTRICT)"
    elif text.startswith("JUDGE OF THE CIVIL COURT"):
        out = (
            f"JUDGE OF THE CIVIL COURT - COUNTY ({upper})"
            if "COUNTYWIDE" in text
            else f"JUDGE OF THE CIVIL COURT - DISTRICT ({n}TH MUNICIPAL COURT DISTRICT - {upper})"
        )
    elif re.match(r"(MAYOR|PUBLIC ADVOCATE|COMPTROLLER) OF THE CITY", text):
        out = f"{text.split(' OF THE CITY')[0]} (CITYWIDE)"
    elif text.startswith("BOROUGH PRESIDENT"):
        out = f"BOROUGH PRESIDENT ({upper})"
    elif text.startswith(("DISTRICT ATTORNEY", "SURROGATE")):
        out = f"{text.split(' ')[0] + (' ATTORNEY' if text.startswith('DISTRICT') else '')} ({upper})"
    else:
        return None
    return f"{party + ' ' if party else ''}{out}, vote for {seats}"


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    index = json.loads((DATA / "raw" / SOURCE / "index.json").read_text())
    entries = []
    totals = []
    ballots = []
    skipped = []
    for fragment, day, kind in FILES:
        url, entry = next((u, e) for u, e in sorted(index.items()) if fragment in u)
        accumulated: dict[tuple[str, str], dict[str, object]] = {}
        county_only = day < "2002-01-01" and day != "2000-11-07" and kind != "Special"
        groups = read_file(DATA / "raw" / SOURCE / str(entry["file"]))
        if kind == "Special" and any(g["grain"] == "ed" for g in groups):
            groups = [g for g in groups if g["grain"] == "ed"]
        for g in groups:
            if county_only and g["scope"] == "CITY OF NEW YORK":
                parts = [(str(r["label"]).title(), r) for r in g["rows"] if r["label"] in COUNTIES]
                if not parts:
                    continue
            else:
                county = COUNTY_NAMES.get(str(g["scope"]))
                if county is None or g["grain"] == "county" or (county_only and g["grain"] == "ed"):
                    continue
                parts = [(county, None)]
            if OUT_OF_SCOPE.search(str(g["office"])):
                continue
            if problems := check(g):
                skipped.append(f"{fragment} page {g['page']} {g['office'][:50]}: {problems[0]}")
                continue
            party = None if kind == "General" else (str(g["party"]) if g["party"] else None)
            labels = g["labels"]
            for county, city_row in parts:
                office = synth(str(g["office"]), county, int(g["seats"]), party)
                if office is None:
                    continue
                if any(not label["name"] for label in labels):
                    skipped.append(f"{fragment} page {g['page']} {g['office'][:50]}: a column has no name")
                    continue
                slot = accumulated.setdefault((county, office), {"rows": [], "total": 0, "labels": labels, "seen": []})
                signature = (g["block"], repr(city_row or g["rows"]))
                if signature in slot["seen"]:
                    continue
                slot["seen"].append(signature)
                if city_row is None and [(x["code"], x["party"].rpartition(" ")[2]) for x in slot["labels"]] != [(x["code"], x["party"].rpartition(" ")[2]) for x in labels]:
                    skipped.append(f"{fragment} page {g['page']} {g['office'][:50]}: columns differ between blocks")
                    continue
                total = city_row or next(r for r in g["rows"] if r["label"] == "TOTAL")
                scat = total["tail"][0] if g["scat"] and total["tail"] else 0
                slot["total"] += sum(total["cands"]) + scat
                if county_only:
                    units = [("total", "TOTAL", total)]
                elif g["grain"] == "ed":
                    ad = g["block"] or int(re.search(r"(\d+)(?:ST|ND|RD|TH) ASSEMBLY DISTRICT", str(g["office"]))[1])
                    units = [("ad", str(ad), total)]
                else:
                    units = [
                        ("ad", str(int(re.match(r"\d+", r["label"])[0])), r) for r in g["rows"] if r["label"] != "TOTAL"
                    ]
                for bkind, bid, row in units:
                    slot["rows"].append((bkind, bid, {**row, "labels": labels} if city_row else row, g["scat"]))
        names: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
        for (county, office), slot in accumulated.items():
            for label in slot["labels"]:
                if label["code"]:
                    names[(office, label["code"])][label["name"]] += 1
        for (county, office), slot in accumulated.items():
            for label in slot["labels"]:
                if label["code"]:
                    label["name"] = names[(office, label["code"])].most_common(1)[0][0]
        for (county, office), slot in accumulated.items():
            head = (f"{url}#{county}", day, kind, county, f"{county} County")
            for bkind, bid, row, has_scat in slot["rows"]:
                for label, votes in zip(row.get("labels", slot["labels"]), row["cands"], strict=True):
                    line = re.sub(r" PARTY$", "", label["party"]) or None
                    line = LINES.get(line, line)
                    entries.append((*head, office, bkind, bid, label["name"], line, votes))
                if has_scat and row["tail"]:
                    entries.append((*head, office, bkind, bid, "WRITE-INS", "WRITE-IN", row["tail"][0]))
                if bkind == "ad" and row["ballots"] is not None:
                    ballots.append((*head, office, bkind, bid, row["ballots"]))
            totals.append((*head, office, "total", "TOTAL", slot["total"]))
    url, entry = next((u, e) for u, e in sorted(index.items()) if "1999/generalElection/1999G%20Results.pdf" in u)
    text = subprocess.run(
        ["pdftotext", "-layout", str(DATA / "raw" / SOURCE / str(entry["file"])), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    judicial = {"New York": 1, "Bronx": 12, "Kings": 2, "Queens": 11, "Richmond": 13}
    lines_1999 = {"INDEPENDENT": "INDEPENDENCE", "RIGHT-TO-LIFE": "RIGHT TO LIFE", "GREEN PARTY": "GREEN"}
    recanvass: dict[tuple[str, str], dict[tuple[str, str, str], tuple[str, int, str]]] = defaultdict(dict)
    boro = office = ""
    for line in text.splitlines():
        if found := re.match(r"BORO:\s+(\w+(?: \w+)?) COUNTY", line):
            boro = found[1].title()
        elif found := re.match(r"OFFICE:\s+(.+?)\s{2,}\(", line):
            office = found[1]
        elif (
            found := re.match(r"^(.{17})\s*(?:\*\s+)?(?:(\d{2})\s+)?(?:\*\s+)?(\D+?)\s+(\d+)\s+([A-Z]+)\s*$", line)
        ) and found[5] in ("ELECTED", "DEFEATED"):
            party = lines_1999.get(found[1].strip(), found[1].strip())
            key = (found[2] or "", party, found[3].strip())
            if recanvass[(boro, office)].setdefault(key, (found[3].strip(), int(found[4]), found[5])) != (
                found[3].strip(),
                int(found[4]),
                found[5],
            ):
                raise ValueError(f"1999 recanvass {boro} {office} {key} appears twice with different tallies")
    for (county, office), rows in recanvass.items():
        districts = {key[0] for key in rows}
        for district in sorted(districts):
            chosen = {key: row for key, row in rows.items() if key[0] == district}
            elected = len({row[0] for row in chosen.values() if row[2] == "ELECTED"})
            if office.startswith("JUSTICES OF THE SUPREME COURT"):
                title = f"JUSTICE OF THE SUPREME COURT ({judicial[county]}TH JUDICIAL DISTRICT), vote for {max(elected, 1)}"
            elif office.startswith("CIVIL COURT (COUNTY"):
                title = f"JUDGE OF THE CIVIL COURT - COUNTY ({county.upper()}), vote for {max(elected, 1)}"
            elif office.startswith("CIVIL COURT BY"):
                title = f"JUDGE OF THE CIVIL COURT - DISTRICT ({int(district)}TH MUNICIPAL COURT DISTRICT - {county.upper()}), vote for 1"
            elif office.startswith("COUNCIL"):
                title = f"MEMBER OF THE CITY COUNCIL ({int(district)}), vote for 1"
            elif office.startswith("DISTRICT ATTORNEY"):
                title = f"DISTRICT ATTORNEY ({county.upper()}), vote for 1"
            elif office.startswith("STATE SENATE"):
                title = f"STATE SENATOR ({int(district)}), vote for 1"
            else:
                skipped.append(f"{url} {county} {office}: not an office this reader knows")
                continue
            head = (f"{url}#{county}", "1999-11-02", "General", county, f"{county} County")
            for (_, party, _), (name, votes, _) in chosen.items():
                entries.append((*head, title, "total", "TOTAL", name, party, votes))
            totals.append((*head, title, "total", "TOTAL", sum(row[1] for row in chosen.values())))
    head_schema = {"url": pl.String, "day": pl.String, "kind": pl.String, "county": pl.String, "scope": pl.String}
    pl.DataFrame(
        entries,
        schema={
            **head_schema,
            "office": pl.String,
            "bkind": pl.String,
            "bid": pl.String,
            "name": pl.String,
            "party": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    ).write_parquet(PARSED / "entries.parquet")
    for name, rows in (("totals", totals), ("ballots", ballots)):
        pl.DataFrame(
            rows,
            schema={**head_schema, "office": pl.String, "bkind": pl.String, "bid": pl.String, "votes": pl.Int64},
            orient="row",
        ).write_parquet(PARSED / f"{name}.parquet")
    print(f"{len(skipped)} groups not read:", *skipped, sep="\n  ")


def emit() -> Path:
    years = tuple(os.environ.get("NYC_YEARS", "1999,2000,2001,2002,2003,2004").split(","))
    folder = PARSED / "slice"
    folder.mkdir(exist_ok=True)
    offices = os.environ.get("NYC_OFFICES", "")
    for name in ("entries", "totals", "ballots"):
        pl.read_parquet(PARSED / f"{name}.parquet").filter(
            pl.col("day").str.slice(0, 4).is_in(years), pl.col("office").str.contains(offices)
        ).write_parquet(folder / f"{name}.parquet")
    return nyc_recap.emit(
        pipeline=SOURCE,
        parsed=folder,
        version=VERSION,
        summary=f"New York City's {', '.join(years)} contests by assembly district from the Board of Elections' certified recaps",
        note=NOTE,
        strict=not offices,
        questions=False,
        fuller=bool(offices),
    )
