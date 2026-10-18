import html
import json
import re
import struct
import subprocess
import unicodedata
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw, scan
from ea.changeset import Changeset, slug
from ea.sources.us import medsl, medsl_state
from ea.sources.us.medsl_state_precincts import FILES, TITLES
from ea.store import DATA, connect

SOURCE = "us.retention"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
KANSAS = "https://sos.ks.gov/elections/"
KANSAS_FILES = {
    "2004-11-02": "04elec/2004GeneralOfficialResults.pdf",
    "2006-11-07": "06elec/2006GeneralElectionOfficialResults.pdf",
    "2008-11-04": "08elec/2008_General_Official_Results.pdf",
    "2012-11-06": "12elec/2012_General_Election_Results.pdf",
    "2014-11-04": "14elec/2014%20General%20Election%20Official%20Results.pdf",
    "2016-11-08": "16elec/2016_General_Election_Official_Results.pdf",
    "2018-11-06": "18elec/2018_General_Election_Official_Votes_Cast.pdf",
    "2020-11-03": "20elec/2020_General_Official_Vote_Totals.pdf",
    "2022-11-08": "22elec/2022-General-Official-Vote-Totals.pdf",
    "2024-11-05": "24elec/2024-General-Election-Official-Vote-Totals.pdf",
}
OKLAHOMA = "https://results.okelections.gov/OKER/?elecDate="
ARIZONA_CANVASSES = {
    "2016-11-08": "https://web.archive.org/web/20161220192915id_/"
    "http://apps.azsos.gov/election/2016/General/Official%20Signed%20State%20Canvass.pdf",
    "2022-11-08": "https://web.archive.org/web/20221205195258id_/"
    "https://azsos.gov/sites/default/files/2022Dec05_General_Election_Canvass_Web.pdf",
    "2024-11-05": "https://web.archive.org/web/20241201183644id_/"
    "https://apps.azsos.gov/election/2024/ge/canvass/20241105_GeneralCanvass_Signed.pdf",
}
ARIZONA_COLUMNS = {
    "Apache": "04001",
    "Cochise": "04003",
    "Coconino": "04005",
    "Gila": "04007",
    "Graham": "04009",
    "Greenlee": "04011",
    "Paz": "04012",
    "LaPaz": "04012",
    "Maricopa": "04013",
    "Mohave": "04015",
    "Navajo": "04017",
    "Pima": "04019",
    "Pinal": "04021",
    "Cruz": "04023",
    "Yavapai": "04025",
    "Yuma": "04027",
    "TOTAL": "",
}
OKLAHOMA_DAYS = ["2016-11-08", "2018-11-06", "2020-11-03", "2022-11-08", "2024-11-05"]
ANSWERS = {"YES": "yes", "NO": "no", "YES/SI": "yes", "NO/NO": "no", "RETAIN": "yes", "REJECT": "no"}
COURTS = [
    (r"COURT OF CRIMINAL APPEALS|CRIMINAL APPEALS", "court-of-criminal-appeals"),
    (r"COURT OF CIVIL APPEALS|CIVIL APPEALS", "court-of-civil-appeals"),
    (r"SPECIAL APPEALS", "court-of-special-appeals"),
    (r"DISTRICT COURT OF APPEAL|COURT OF APPEAL\b", "district-court-of-appeal"),
    (r"APPELLATE", "appellate-court"),
    (r"SUPREME", "supreme-court"),
    (r"COURT OF APPEALS|APPEALS COURT|APPEALS CRT|CRT OF APPEALS|APPEALS JUDGE", "court-of-appeals"),
]
NAMED = [
    r"^TO RETAIN (?:JUDGE |JUSTICE )?(?P<name>.+?) OF THE .*$",
    r"WYOMING SUPREME COURT (?P<name>[A-Z][A-Z. ]+)$",
    r"\bRETAIN (?:JUDGE |JUSTICE )?(?P<name>[A-Z][A-Z.,' -]+)$",
    r"^RETENTION OF (?P<name>.+)$",
    r"SHALL (?:CHIEF )?(?:JUSTICE |JUDGE )?(?P<name>.+?) (?:REPRESENTING|OF THE|BE RETAINED)",
    r"- RETENTION - (?P<name>.+)$",
    r"\bRETENTION (?P<name>(?!.*\b(?:COURT|JUSTICE|JUDGE|APPEALS)\b)[A-Z.' -]+)$",
    r"\((?P<name>[A-Z][A-Z.' -]+)\)$",
    r"^(?P<name>[A-Z][A-Z'-]+, [A-Z][A-Z.' ]+), SUPREME COURT JUSTICE$",
    r"^(?P<name>[A-Z][A-Z'-]+) - (?:JUDGE|JUSTICE) OF",
    r"^(?:SUPREME COURT|APPEALS COURT) (?P<name>[A-Z][A-Z'-]+, [A-Z][A-Z.\" ]+)$",
    r"(?:SUPREME COURT ?|COURT OF APPEALS )(?P<name>(?!JUSTICE|JUDGE)[A-Z]{3,})$",
    r" - (?P<name>(?!OFFICE|DIVISION|DISTRICT|RETENTION)[A-Z][A-Z.,' \"-]+)$",
    r"-(?P<name>[A-Z]{3,})$",
]
ORDINALS = ["First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh", "Eighth", "Ninth"]
SUFFIXES = {"JR", "SR", "II", "III", "IV"}
NICKNAMES = {"BRAD": "BRADFORD"}
DISTRICTED = {
    ("FL", "district-court-of-appeal"),
    ("IL", "appellate-court"),
    ("IL", "supreme-court"),
    ("NE", "supreme-court"),
    ("NE", "court-of-appeals"),
    ("MD", "court-of-appeals"),
    ("MD", "court-of-special-appeals"),
    ("AZ", "court-of-appeals"),
}
ARIZONA = {
    "AZ:coa-1-outside-maricopa": (
        "Court of Appeals Division One outside Maricopa County",
        {"04001", "04005", "04012", "04015", "04017", "04025", "04027"},
    ),
    "AZ:coa-2-outside-pima": (
        "Court of Appeals Division Two outside Pima County",
        {"04003", "04007", "04009", "04011", "04021", "04023"},
    ),
}
THRESHOLDS = {"IL": 0.6, "NM": 0.57}
METHOD = (
    "A retention election asks whether a sitting judge should stay in office, with Yes and No as the only answers. "
    "The judge keeps the seat when Yes reaches the state's threshold of the Yes and No votes: three-fifths in "
    "Illinois, 57 percent in New Mexico, and a simple majority elsewhere."
)
MEDSL_METHOD = (
    "Votes by precinct from MEDSL's precinct returns, summed over vote modes. Rows for undervotes and overvotes are "
    "left out, so the set holds only the Yes and No votes."
)
MISSING = (
    "MEDSL's precinct file has no rows for this question in {counties}, so these sums fall short of the official "
    "total and no outcome is recorded."
)
KANSAS_METHOD = "Statewide Yes and No votes from the Kansas Secretary of State's official vote totals."
ARIZONA_METHOD = (
    "Yes and No votes by county from the Arizona Secretary of State's official canvass, with each row checked "
    "against its printed total."
)
OKLAHOMA_METHOD = (
    "Statewide Yes and No votes from the Oklahoma State Election Board's official results site, captured from the "
    "page for the election day."
)
TAKEN_OVER = (
    "A corrected copy of a set that `us.medsl_statewide` or `us.medsl_state_precincts` published for this retention "
    "question as a race between four candidates named for the judge and the answers. Its Yes and No lines count for "
    "the question's Yes and No answers, told apart by matching their totals to the Kansas Secretary of State's, and "
    "the undervote and overvote lines stay as `others`."
)
SUPERSEDED = (
    "This set loaded the retention question as a race between four candidates named for the judge and the answers, "
    "and its Yes and No lines lost their options when the question was rebuilt. The `us.retention` copy that "
    "supersedes it carries every line."
)


def plain(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def ordered(text: str) -> str:
    text = re.sub(r"\s+", " ", text.replace("“", '"').replace("”", '"')).strip(' ,."')
    found = re.fullmatch(r"([A-Za-z'-]+), (.+)", text)
    if found and found[2].upper().strip(".") not in SUFFIXES:
        return f"{found[2]} {found[1]}"
    return text


def tokens(text: str) -> list[str]:
    text = re.sub(r'"[^"]*"', " ", plain(ordered(text)).upper())
    return [word for word in re.sub(r"[^A-Z' -]", " ", text.replace(".", " ")).split() if word]


def identity(text: str) -> tuple[str, str]:
    words = tokens(text)
    while len(words) > 1 and words[-1] in SUFFIXES:
        words.pop()
    if len(words) < 2:
        return ("", words[-1] if words else "")
    first = next((word for word in words[:-1] if len(word) > 1), words[0])
    return (NICKNAMES.get(first, first), words[-1])


def display(text: str) -> str:
    text = ordered(text)
    if text.upper() == text:
        text = re.sub(r"\bMc([a-z])", lambda match: "Mc" + match[1].upper(), text.title())
        text = re.sub(r"\b(Ii|Iii|Iv)\b", lambda match: match[0].upper(), text)
        text = re.sub(r"\b([A-Z])\b(?!\.)", r"\1.", text)
    return re.sub(r"\b(Jr|Sr)$", r"\1.", text)


def fetch() -> None:
    for path in KANSAS_FILES.values():
        raw.fetch(SOURCE, KANSAS + path, suffix=".pdf")
    for url in ARIZONA_CANVASSES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def arizona() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    (PARSED / "ocr").mkdir(parents=True, exist_ok=True)
    for day, url in ARIZONA_CANVASSES.items():
        pdf = raw.cached(SOURCE, url)
        info = subprocess.run(["pdfinfo", str(pdf.path)], capture_output=True, text=True, check=True).stdout
        for page in range(1, int(info.split("Pages:")[1].split()[0]) + 1):
            lines: list[list[tuple[str, scan.Box]]] = []
            layer = scan.layer(pdf.path, page)
            if layer:
                for word, box in sorted(layer, key=lambda item: (item[1][1], item[1][0])):
                    if lines and abs(lines[-1][0][1][1] - box[1]) < 15:
                        lines[-1].append((word, box))
                    else:
                        lines.append([(word, box)])
            else:
                grouped: dict[tuple[int, int, int], list[tuple[str, scan.Box]]] = {}
                for word, box, line, _ in scan.words(pdf.path, page, PARSED / "ocr" / f"{pdf.sha256}-{page}.tsv"):
                    grouped.setdefault(line, []).append((word, box))
                lines = list(grouped.values())
            columns: list[tuple[float, str]] = []
            court: tuple[str, int | None] | None = None
            name = ""
            for words in lines:
                words.sort(key=lambda item: item[1][0])
                text = " ".join(word for word, _ in words)
                if "Apache" in text and "TOTAL" in text:
                    columns = [
                        ((box[0] + box[2]) / 2, ARIZONA_COLUMNS[word]) for word, box in words if word in ARIZONA_COLUMNS
                    ]
                    continue
                if heading := re.match(
                    r"(Justice of the Arizona Supreme Court|Judge of the Court of Appeals Division (I+))", text
                ):
                    court = ("supreme-court", None) if heading[2] is None else ("court-of-appeals", len(heading[2]))
                    continue
                said = re.match(r"Be retained (YES|NO)\b", text, re.IGNORECASE)
                if court is None or said is None:
                    if court is not None and not re.search(r"\d", text):
                        name = text.rstrip(" :")
                    continue
                cells: list[tuple[str, int, scan.Box, bool]] = []
                merged: list[tuple[str, scan.Box]] = []
                for word, box in words[3:]:
                    if word == "*":
                        continue
                    if (
                        merged
                        and (word.startswith(",") or merged[-1][0].endswith(","))
                        and box[0] - merged[-1][1][2] < 40
                    ):
                        last, at = merged[-1]
                        merged[-1] = (last + word, (at[0], min(at[1], box[1]), box[2], max(at[3], box[3])))
                    else:
                        merged.append((word, box))
                for word, box in merged:
                    if not re.search(r"\d", word):
                        continue
                    center = (box[0] + box[2]) / 2
                    column = min(columns, key=lambda item: abs(item[0] - center))[1]
                    cells.append((column, int(re.sub(r"\D", "", word)), box, bool(re.search(r"[^\d,]", word))))
                if not any(column == "" for column, _, _, _ in cells):
                    raise ValueError(f"Arizona {day} page {page} {name}: no total in {text}")
                total = next(votes for column, votes, _, _ in cells if column == "")
                garbled = [cell for cell in cells if cell[0] != "" and cell[3]]
                counted = [(column, votes, box, False) for column, votes, box, _ in cells if column != ""]
                gap = total - sum(votes for _, votes, _, _ in counted)
                if gap:
                    if len(garbled) != 1:
                        raise ValueError(f"Arizona {day} {name} {said[1]}: counties miss the total by {gap}")
                    column, votes, box, _ = garbled[0]
                    print(f"Arizona {day} {name} {said[1]}: read {votes} in {column} as {votes + gap}")
                    counted[[cell[0] for cell in counted].index(column)] = (column, votes + gap, box, True)
                for column, votes, box, repaired in counted:
                    rows.append(
                        {
                            "day": day,
                            "state": "AZ",
                            "court": court[0],
                            "number": court[1],
                            "post": None,
                            "name": name,
                            "answer": said[1].lower(),
                            "votes": votes,
                            "page": page,
                            "x0": box[0],
                            "y0": box[1],
                            "x1": box[2],
                            "y1": box[3],
                            "origin": url,
                            "county": column,
                            "flags": (0 if layer else 8) | (1 if repaired else 0),
                        }
                    )
    return rows


def kansas() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for day, path in KANSAS_FILES.items():
        pdf = raw.cached(SOURCE, KANSAS + path).path
        info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True).stdout
        race: tuple[str, int] | None = None
        pending: list[tuple[str, str | None, int, int, scan.Box]] = []
        for page in range(1, int(info.split("Pages:")[1].split()[0]) + 1):
            lines: list[list[tuple[str, scan.Box]]] = []
            for word, box in sorted(
                ((html.unescape(word), box) for word, box in scan.layer(pdf, page)),
                key=lambda item: (item[1][1], item[1][0]),
            ):
                if lines and abs(lines[-1][0][1][1] - box[1]) < 12:
                    lines[-1].append((word, box))
                else:
                    lines.append([(word, box)])
            for words in lines:
                words.sort(key=lambda item: item[1][0])
                text = " ".join(word for word, _ in words)
                if found := re.fullmatch(r"(Supreme Court Justice|Court of Appeals Judge) ?-?0*(\d+)", text):
                    race = ("supreme-court" if found[1].startswith("Supreme") else "court-of-appeals", int(found[2]))
                    pending = []
                    continue
                if race is None:
                    continue
                found = re.fullmatch(r"(?P<label>.+?)\s+(?P<votes>[\d,]+)\s+[\d.]+ ?%", text)
                if found is None:
                    race = None
                    continue
                said = re.search(r"\s*-?\s*['\"“]*\b(YES|NO)\b['\"”]*", found["label"])
                label = (
                    found["label"] if said is None else found["label"][: said.start()] + found["label"][said.end() :]
                )
                box = next(box for word, box in words if word == found["votes"])
                pending.append(
                    (label.strip(), said[1] if said else None, int(found["votes"].replace(",", "")), page, box)
                )
                if len(pending) == 2:
                    first, second = (line[1] for line in pending)
                    first = first or ("NO" if second == "YES" else "YES")
                    second = second or ("NO" if first == "YES" else "YES")
                    if {first, second} != {"YES", "NO"}:
                        raise ValueError(f"{day} {race[0]} {race[1]}: answers {first} and {second}")
                    labeled = dict(zip((first, second), pending, strict=True))
                    for answer in ("YES", "NO"):
                        _, _, votes, where, at = labeled[answer]
                        rows.append(
                            {
                                "day": day,
                                "state": "KS",
                                "court": race[0],
                                "number": race[1],
                                "post": None,
                                "name": labeled["YES"][0],
                                "answer": answer.lower(),
                                "votes": votes,
                                "page": where,
                                "x0": at[0],
                                "y0": at[1],
                                "x1": at[2],
                                "y1": at[3],
                                "origin": KANSAS + path,
                                "county": None,
                                "flags": 0,
                            }
                        )
                    race = None
    return rows


def oklahoma() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for day in OKLAHOMA_DAYS:
        url = OKLAHOMA + day.replace("-", "")
        for race in json.loads(raw.cached(SOURCE, url).path.read_text())["races"]:
            found = re.fullmatch(
                r"(SUPREME COURT|COURT OF CIVIL APPEALS|COURT OF CRIMINAL APPEALS) DISTRICT (\d+)"
                r"(?: - OFFICE (\d))?(?: - (.+))?",
                race["race"],
            )
            assert found is not None, race["race"]
            name = found[4]
            if race["question"] is not None:
                asked = re.fullmatch(r"Shall (.+) of the OKLAHOMA .+ be retained in office\?", race["question"])
                assert asked is not None, race["question"]
                name = asked[1]
            yes, no, total = (int(race[key].replace(",", "")) for key in ("yes", "no", "total"))
            if yes + no != total:
                raise ValueError(f"{day} {race['race']}: {yes} + {no} is not {total}")
            for answer, votes in (("yes", yes), ("no", no)):
                rows.append(
                    {
                        "day": day,
                        "state": "OK",
                        "court": slug(found[1].lower()),
                        "number": int(found[2]),
                        "post": int(found[3]) if found[3] else None,
                        "name": name,
                        "answer": answer,
                        "votes": votes,
                        "page": None,
                        "x0": None,
                        "y0": None,
                        "x1": None,
                        "y1": None,
                        "origin": url,
                        "county": None,
                        "flags": 0,
                    }
                )
    return rows


def parse() -> None:
    frame = (
        pl.concat(
            [
                medsl.precincts({("retention", year): raw.cached(source, url).path})
                for year, files in FILES.items()
                for source, url in files
            ],
            how="diagonal_relaxed",
        )
        .filter(pl.col("measure") == 1, ~pl.col("writein"), pl.col("stage") == "GEN")
        .with_columns(pl.col("precinct", "jurisdiction").str.strip_chars())
    )
    rows = []
    for office, seat, candidate in frame.select("office", "seat", "candidate").unique().iter_rows():
        text = re.sub(r"\s+", " ", plain(office).strip())
        found = re.fullmatch(r"(.+?) - (YES|NO|UNDERVOTES|OVERVOTES)", candidate)
        answer = ANSWERS.get(found[2] if found else candidate)
        if answer is None:
            continue
        court = next((key for pattern, key in COURTS if re.search(pattern, f"{text} {seat or ''}")), None)
        if court is None:
            continue
        name = found[1] if found else None
        if name is None:
            for pattern in NAMED:
                if match := re.search(pattern, text):
                    name = match["name"]
                    break
        place = (seat or "").strip().upper()
        number = None
        for source in (place, text):
            if match := re.search(r"\b(FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|EIGHTH)\b", source):
                number = [word.upper() for word in ORDINALS].index(match[1]) + 1
            elif match := re.search(r"\bDIV(?:ISION|\.)? ?(I{1,2})\b", source):
                number = len(match[1])
            elif match := re.search(r"(?:^|DISTRICT |DIV(?:ISION|\.)? ?|\b)0*(\d+)(?:ST|ND|RD|TH)?\b", source):
                number = int(match[1]) or None
            if number is not None:
                break
        post = re.search(r"OFFICE (\d)", f"{place} {text}")
        rows.append(
            (
                office,
                seat,
                candidate,
                court,
                name,
                answer,
                number,
                "AT-LARGE" in place or "AT LARGE" in text,
                int(post[1]) if post else None,
                "CHIEF JUSTICE" in text,
            )
        )
    labels = pl.DataFrame(
        rows,
        schema={
            "office": pl.String,
            "seat": pl.String,
            "candidate": pl.String,
            "court": pl.String,
            "name": pl.String,
            "answer": pl.String,
            "number": pl.Int16,
            "at_large": pl.Boolean,
            "post": pl.Int16,
            "chief": pl.Boolean,
        },
        orient="row",
    )
    frame = (
        frame.join(labels, on=["office", "seat", "candidate"], nulls_equal=True)
        .with_columns(
            day=pl.col("day").cast(pl.String),
            unit=pl.when(pl.col("kind").is_in(["county", "place"]) & (pl.col("precinct") != ""))
            .then(pl.lit("precinct"))
            .otherwise("kind"),
        )
        .with_columns(
            county=pl.when(pl.col("unit") == "precinct").then("code").otherwise(pl.col("fips")),
            code=pl.when(pl.col("unit") == "precinct")
            .then(pl.concat_str("year", "state", "code", "jurisdiction", "precinct", separator=":"))
            .otherwise("code"),
        )
    )
    official = pl.DataFrame(
        kansas() + oklahoma() + arizona(),
        schema={
            "day": pl.String,
            "state": pl.String,
            "court": pl.String,
            "number": pl.Int16,
            "post": pl.Int16,
            "name": pl.String,
            "answer": pl.String,
            "votes": pl.Int64,
            "page": pl.Int32,
            "x0": pl.Int32,
            "y0": pl.Int32,
            "x1": pl.Int32,
            "y1": pl.Int32,
            "origin": pl.String,
            "county": pl.String,
            "flags": pl.Int16,
        },
    )
    variants: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for state, name, mixed in [
        *official.filter(pl.col("answer") == "yes").select("state", "name", pl.lit(True)).iter_rows(),
        *frame.filter(pl.col("name").is_not_null()).select("state", "name", pl.lit(False)).unique().iter_rows(),
    ]:
        first, last = identity(name)
        if first:
            variants[(state, first, last)].insert(0 if mixed else len(variants[(state, first, last)]), name)
    surnames: dict[tuple[str, str], set[str]] = defaultdict(set)
    for state, first, last in variants:
        surnames[(state, last)].add(first)

    def resolve(state: str, name: str | None) -> str | None:
        if name is None:
            return None
        first, last = identity(name)
        if not first:
            firsts = surnames.get((state, last), set())
            if len(firsts) != 1:
                return None
            first = next(iter(firsts))
        return f"{state}:{slug(first)}-{slug(last)}"

    names = {
        f"{state}:{slug(first)}-{slug(last)}": display(max(texts, key=lambda text: (text != text.upper(), len(text))))
        for (state, first, last), texts in variants.items()
    }
    official = official.with_columns(
        person=pl.struct("state", "name").map_elements(
            lambda row: resolve(row["state"], row["name"]), return_dtype=pl.String
        )
    )
    frame = frame.with_columns(
        person=pl.struct("state", "name").map_elements(
            lambda row: resolve(row["state"], row["name"]), return_dtype=pl.String
        )
    )
    filled = (
        official.select(
            pl.col("day").str.slice(0, 4).cast(pl.Int32).alias("year"), "state", "court", "number", "post", "person"
        )
        .unique()
        .filter(pl.len().over("year", "state", "court", "number", "post") == 1)
        .rename({"person": "official"})
    )
    frame = frame.join(filled, on=["year", "state", "court", "number", "post"], how="left", nulls_equal=True)
    frame = frame.with_columns(person=pl.coalesce("person", "official"))
    groups = (
        frame.filter(pl.col("person").is_not_null())
        .group_by("year", "state", "court")
        .agg(pl.col("person").unique().alias("people"))
    )
    numbered = (
        frame.filter(pl.col("person").is_not_null(), pl.col("number").is_not_null())
        .group_by("year", "state", "court", "number")
        .agg(pl.col("person").unique().alias("numbered"))
    )
    frame = (
        frame.join(groups, on=["year", "state", "court"], how="left")
        .join(numbered, on=["year", "state", "court", "number"], how="left", nulls_equal=True)
        .with_columns(
            person=pl.coalesce(
                "person",
                pl.when(pl.col("name").is_null() & (pl.col("numbered").list.len() == 1)).then(
                    pl.col("numbered").list.first()
                ),
                pl.when(pl.col("name").is_null() & (pl.col("people").list.len() == 1)).then(
                    pl.col("people").list.first()
                ),
            )
        )
        .drop("people", "numbered", "official")
    )
    dropped = (
        frame.filter(pl.col("person").is_null())
        .group_by("year", "state", "court", "office")
        .agg(pl.col("votes").sum())
        .sort("state", "year")
    )
    answers = frame.group_by("year", "state", "court", "person").agg(pl.col("answer").n_unique().alias("answers"))
    frame = (
        frame.filter(pl.col("person").is_not_null())
        .join(answers, on=["year", "state", "court", "person"])
        .filter(pl.col("answers") == 2)
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.group_by(
        "year",
        "day",
        "state",
        "court",
        "person",
        "number",
        "at_large",
        "post",
        "chief",
        "unit",
        "code",
        "county",
        "answer",
    ).agg(pl.col("votes").sum()).write_parquet(PARSED / "medsl.parquet")
    official.write_parquet(PARSED / "official.parquet")
    pl.DataFrame(
        [(key, text) for key, text in names.items()], schema={"person": pl.String, "name": pl.String}, orient="row"
    ).write_parquet(PARSED / "names.parquet")
    with pl.Config(tbl_rows=100, fmt_str_lengths=70, tbl_width_chars=200):
        print("unnamed and dropped:")
        print(dropped)
        print(answers.filter(pl.col("answers") != 2))
    print(
        frame.select("state", "year", "court", "person").unique().height,
        "MEDSL questions,",
        official.select("day", "state", "court", "number", "post").unique().height,
        "official questions",
    )


def emit() -> Path:
    medsl_rows = pl.read_parquet(PARSED / "medsl.parquet")
    official = pl.read_parquet(PARSED / "official.parquet")
    names = dict(pl.read_parquet(PARSED / "names.parquet").iter_rows())
    race = ["day", "state", "court", "person"]
    races = (
        pl.concat(
            [
                medsl_rows.group_by(race).agg(
                    pl.col("number").drop_nulls().mode().first(),
                    pl.col("at_large").any(),
                    pl.col("post").drop_nulls().first(),
                    pl.col("chief").any(),
                    pl.col("county").drop_nulls().str.slice(0, 5).unique().alias("counties"),
                ),
                official.group_by(race).agg(
                    pl.col("number").first(),
                    pl.lit(False).alias("at_large"),
                    pl.col("post").first(),
                    pl.lit(False).alias("chief"),
                    pl.col("county").drop_nulls().unique().alias("counties"),
                ),
            ]
        )
        .group_by(race)
        .agg(
            pl.col("number").drop_nulls().first(),
            pl.col("at_large").any(),
            pl.col("post").drop_nulls().first(),
            pl.col("chief").any(),
            pl.col("counties").list.explode().unique().drop_nulls(),
        )
        .sort(race)
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Judicial retention elections for state supreme and appellate courts: MEDSL's precinct returns "
            "2016–2024, Kansas's official totals 2004–2024, Oklahoma's official totals 2016–2024, and Arizona's "
            "official canvasses of 2016, 2022, and 2024",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def note(entity: UUID, kind: str, body: str) -> None:
            cs.add("note", entity_id=entity, kind=kind, body_md=body)

        states = dict(
            conn.execute(
                """select substr(p.iso_code, 4), n.text from polity p
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   where p.iso_code like 'US-%'"""
            ).fetchall()
        )
        state_units = {
            abbreviation: (unit, area, code)
            for abbreviation, unit, area, code in conn.execute(
                """select substr(p.iso_code, 4), u.id, u.area_id, u.code from reporting_unit u
                   join polity_territory t on t.area_id = u.area_id
                   join polity p on p.id = t.polity_id and p.iso_code like 'US-%'
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        }
        counties = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code in ('us.county', 'us.place')"
            ).fetchall()
        }
        sizes = dict(
            conn.execute(
                """select left(code, 2), count(*) from reporting_unit where scheme_code = 'us.county'
                   and (valid is null or valid @> date '2020-11-03') group by 1"""
            ).fetchall()
        )
        keyed = {
            scheme: {value for (value,) in conn.execute("select value from external_id where scheme = %s", [scheme])}
            for scheme in ("us.office", "us.contest", "us.election")
        }
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/%'").fetchall()}
        shaped = dict(
            conn.execute(
                """select c.code, c.area_id from area_code c join area a on a.id = c.area_id
                   where a.scheme_code = 'us.precinct' and c.code ~ '^20(16|18|20|22|24):'"""
            ).fetchall()
        )
        existing = {
            code
            for (code,) in conn.execute(
                "select code from reporting_unit where scheme_code = 'us.precinct' and code ~ '^20(16|18|20|22|24):'"
            ).fetchall()
        }
        held = {
            key: [(row[0], *row[1:]) for row in rows]
            for key, rows in (
                (
                    key,
                    conn.execute(
                        """select r.id, r.election_id, r.stage, r.kind, r.source_id, r.completeness
                           from result_set r join external_id x on x.entity_id = r.contest_id
                           where x.scheme = 'us.contest' and x.value = %s and not exists (
                             select from external_id k where k.scheme = 'us.retention.result_set' and k.entity_id = r.id)""",
                        [key],
                    ).fetchall(),
                )
                for key in keyed["us.contest"]
                if re.fullmatch(r"20\d\d-\d\d-\d\d:KS:judge-of-the-court-of-appeals:seat-\d+", key)
            )
        }
        junk = defaultdict(list)
        for key, option, label in conn.execute(
            """select x.value, o.id, o.ballot_label from external_id x
               join contest_option o on o.contest_id = x.entity_id
               where x.scheme = 'us.contest' and x.value = any(%s)""",
            [list(held)],
        ).fetchall():
            junk[key].append((option, label))

        kansas_source = cs.source("Kansas Secretary of State", "Official vote totals, general elections")
        cs.add(
            "source",
            id=kansas_source,
            kind="official_body",
            publisher="Kansas Secretary of State",
            title="Official vote totals, general elections",
            url="https://sos.ks.gov/elections/election-results.html",
            license="Public record",
        )
        oklahoma_source = cs.source("Oklahoma State Election Board", "Official election results")
        cs.add(
            "source",
            id=oklahoma_source,
            kind="official_body",
            publisher="Oklahoma State Election Board",
            title="Official election results",
            url="https://results.okelections.gov/OKER/",
            license="Public record",
        )
        arizona_source = cs.source("Arizona Secretary of State", "State of Arizona Official Canvass")
        cs.add(
            "source",
            id=arizona_source,
            kind="official_body",
            publisher="Arizona Secretary of State",
            title="State of Arizona Official Canvass",
            url="https://azsos.gov/elections/results-data",
            license="Public record",
        )
        official_sources = {"KS": kansas_source, "OK": oklahoma_source, "AZ": arizona_source}
        methods = {"KS": KANSAS_METHOD, "OK": OKLAHOMA_METHOD, "AZ": ARIZONA_METHOD}
        artifacts = {
            url: cs.artifact(raw.cached(SOURCE, url), official_sources[state])
            for url, state in official.select("origin", "state").unique().sort("origin").iter_rows()
        }
        medsl_sources: dict[int, UUID] = {}
        for year, (text, _) in TITLES.items():
            medsl_sources[year] = cs.source(medsl_state.PUBLISHER, text)
            if medsl_sources[year] in cs.minted:
                raise ValueError(f"no source {text}")
        pages: dict[tuple[str, int], tuple[UUID, int, int]] = {}
        for url, number in official.filter(pl.col("page").is_not_null()).select("origin", "page").unique().iter_rows():
            pdf = raw.cached(SOURCE, url)
            image = PAGES / f"{pdf.sha256}-{number}.png"
            if not image.exists():
                PAGES.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                    + [str(pdf.path), str(image.with_suffix(""))],
                    check=True,
                )
            width, height = struct.unpack(">II", image.read_bytes()[16:24])
            page = cs.page(artifacts[url], number)
            cs.add(
                "source_page",
                id=page,
                artifact_id=artifacts[url],
                page_number=number,
                image_key=f"pages/{SOURCE}/{image.name}",
                width=width,
                height=height,
            )
            pages[(url, number)] = (page, width * 3, height * 3)

        systems = {}
        for state, share in (("", None), *THRESHOLDS.items()):
            key = "retention" if share is None else f"retention-{round(share * 100)}"
            systems[state] = cs.key("us.electoral_system", key)
            cs.add(
                "electoral_system",
                id=systems[state],
                polity_id=cs.polity("US" if share is None else f"US-{state}"),
                rules={"formula": "referendum"}
                if share is None
                else {"formula": "referendum", "majority": {"share": share}},
                rules_version=1,
            )
            name(
                systems[state],
                "Judicial retention, simple majority"
                if share is None
                else f"Judicial retention, {round(share * 100)} percent of Yes and No votes ({states[state]})",
            )
        districts: dict[str, UUID] = {}

        def electorate(state: str, court: str, number: int | None, at_large: bool, seen: set[str]) -> UUID:
            if (state, court) not in DISTRICTED or at_large or number is None:
                return state_units[state][1]
            if state == "AZ":
                if seen <= {"04013"} or seen <= {"04019"}:
                    return counties[next(iter(seen))][1]
                code = next((code for code, (_, members) in ARIZONA.items() if seen <= members), None)
                if code is None:
                    raise ValueError(f"no Arizona electorate holds {sorted(seen)}")
                text = ARIZONA[code][0]
            else:
                code, text = {
                    "FL": (f"FL:dca-{number}", f"{ORDINALS[number - 1]} District Court of Appeal"),
                    "IL": (f"IL:judicial-{number}", f"{ORDINALS[number - 1]} Judicial District"),
                    "NE": (f"NE:supreme-{number}", f"Supreme Court Judicial District {number}"),
                    "MD": (f"MD:appellate-{number}", f"{ORDINALS[number - 1]} Appellate Judicial Circuit"),
                }[state]
            if code not in districts:
                area = cs.area("us.judicial_district", code)
                districts[code] = area
                cs.add("area", id=area, scheme_code="us.judicial_district")
                cs.add("area_code", area_id=area, code=code, valid_from=None)
                cs.add("area_parent", area_id=area, parent_id=state_units[state][1])
                name(area, f"{text}, {state}")
            return districts[code]

        persons: set[str] = set()
        memberships: set[tuple[UUID, UUID, str]] = set()
        values: list[tuple[str, str, str, int, int]] = []
        units: dict[str, tuple[str, str, str]] = {}
        compared: list[tuple[str, int, int]] = []
        for row in races.iter_rows(named=True):
            day, state, court, person_key = row["day"], row["state"], row["court"], row["person"]
            year = int(day[:4])
            election_key = f"{day}:{state}"
            if election_key not in keyed["us.election"]:
                print(f"{election_key}: no election, skipped")
                continue
            number, post = row["number"], row["post"]
            title = {
                "supreme-court": "Chief Justice of the Supreme Court"
                if row["chief"]
                else "Justice of the Supreme Court",
                "court-of-appeals": "Judge of the Court of Appeals",
                "court-of-special-appeals": "Judge of the Appellate Court"
                if year > 2022
                else "Judge of the Court of Special Appeals",
                "district-court-of-appeal": "Judge of the District Court of Appeal",
                "appellate-court": "Judge of the Appellate Court",
                "court-of-civil-appeals": "Judge of the Court of Civil Appeals",
                "court-of-criminal-appeals": "Judge of the Court of Criminal Appeals",
            }[court]
            office_key = f"{slug(title)}:{state}"
            office = cs.key("us.office", office_key)
            if office_key not in keyed["us.office"]:
                keyed["us.office"].add(office_key)
                cs.add("office", id=office, polity_id=cs.polity(f"US-{state}"), kind="other")
                name(office, f"{title} of {states[state]}")
            judge = names[person_key]
            suffix = f"seat-{number}" if state == "KS" else person_key.split(":")[1]
            contest_key = f"{day}:{state}:{slug(title)}:{suffix}"
            contest = cs.key("us.contest", contest_key)
            minted = contest_key not in keyed["us.contest"]
            area = electorate(state, court, number, row["at_large"], set(row["counties"]))
            cs.add(
                "contest",
                id=contest,
                election_id=cs.key("us.election", election_key),
                kind="office",
                office_id=office,
                district_area_id=area,
                term="full",
                magnitude=1,
                round_number=1,
                electoral_system_id=systems.get(state, systems[""]),
                default_result_kind="counted",
            )
            seat = (
                f"Seat {number}"
                if state == "KS"
                else f"District {number}" + (f", Office {post}" if post else "")
                if state == "OK"
                else None
            )
            if minted:
                name(contest, f"{year} {states[state]} {title} retention: {judge}" + (f" ({seat})" if seat else ""))
                base = f"us/{state.lower()}/{day}-general/{slug(title)}-{suffix}"
                path, count = base, 1
                while path in taken:
                    count += 1
                    path = f"{base}-{count}"
                taken.add(path)
                cs.add("slug", path=path, entity_id=contest)
            if person_key not in persons:
                persons.add(person_key)
                person = cs.key(f"{SOURCE}.person", person_key)
                if person in cs.minted:
                    cs.add("person", id=person)
                    name(person, judge)
            person = cs.key(f"{SOURCE}.person", person_key)
            options = {}
            for position, answer in enumerate(("yes", "no")):
                options[answer] = cs.key("us.option", f"{contest_key}:{answer}")
                cs.add(
                    "contest_option",
                    id=options[answer],
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=answer.capitalize(),
                )
            candidacy = cs.key("us.candidacy", f"{contest_key}:yes:head")
            cs.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=options["yes"],
                role="head",
                ballot_name=judge,
                is_independent=False,
            )
            for option, label in junk.get(contest_key, []):
                spoiled = re.fullmatch(r"(?:.+ - )?(Undervotes|Overvotes)", label or "")
                if spoiled is not None:
                    cs.add("contest_option", id=option, contest_id=contest, kind="others", ballot_label=spoiled[1])
                elif label not in ("Yes", "No"):
                    raise ValueError(f"{contest_key}: unexpected option {label}")
            for result_set, election, stage, kind, source, completeness in held.get(contest_key, []):
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage=stage,
                    kind=kind,
                    status="superseded",
                    source_id=source,
                    is_default=False,
                    completeness=completeness,
                )
                note(result_set, "caveat", SUPERSEDED)
            totals = official.filter(*(pl.col(column) == row[column] for column in race))
            lines = medsl_rows.filter(*(pl.col(column) == row[column] for column in race))
            tallies: dict[str, int] = {}
            complete = False
            default = None
            if totals.height:
                url = totals["origin"][0]
                default = cs.key(f"{SOURCE}.result_set", f"{contest_key}:official")
                note(default, "methodology", f"{METHOD} {methods[state]}")
                for item in totals.iter_rows(named=True):
                    tallies[item["answer"]] = tallies.get(item["answer"], 0) + item["votes"]
                    unit = state_units[state][0] if item["county"] is None else counties[item["county"]][0]
                    if default not in cs.minted:
                        continue
                    cs.add(
                        "result_value",
                        result_set_id=default,
                        unit_id=unit,
                        option_id=options[item["answer"]],
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=item["flags"],
                        value=item["votes"],
                    )
                    if item["page"] is not None:
                        page, width, height = pages[(url, item["page"])]
                        cs.add(
                            "value_locator",
                            result_set_id=default,
                            unit_id=unit,
                            option_id=options[item["answer"]],
                            channel=0,
                            measure=1,
                            count_round=0,
                            page_id=page,
                            source_id=None,
                            x0=item["x0"] / width,
                            y0=item["y0"] / height,
                            x1=item["x1"] / width,
                            y1=item["y1"] / height,
                        )
                complete = True
            for result_set, election, stage, kind, source, completeness in held.get(contest_key, []):
                copy = cs.key(f"{SOURCE}.result_set", f"{contest_key}:{result_set}")
                cs.add(
                    "result_set",
                    id=copy,
                    election_id=election,
                    contest_id=contest,
                    stage=stage,
                    kind=kind,
                    status="active",
                    source_id=source,
                    is_default=False,
                    supersedes_id=result_set,
                    completeness=completeness,
                )
                note(copy, "methodology", TAKEN_OVER)
                if area in districts.values():
                    for (unit,) in conn.execute(
                        """select distinct u.id from result_value v join result_set r on r.k = v.result_set_k
                           join reporting_unit u on u.k = v.unit_k
                           where r.id = %s and u.scheme_code = 'us.precinct'""",
                        [result_set],
                    ).fetchall():
                        memberships.add((unit, area, day))
                if copy not in cs.minted:
                    continue
                facts = conn.execute(
                    """select u.id, v.option_k, o.id, o.ballot_label, v.channel, v.measure, v.count_round, v.flags,
                              v.value
                       from result_value v join result_set r on r.k = v.result_set_k
                       join reporting_unit u on u.k = v.unit_k left join contest_option o on o.k = v.option_k
                       where r.id = %s""",
                    [result_set],
                ).fetchall()
                lost: dict[int, int] = defaultdict(int)
                for _, k, option, _, _, _, _, _, value in facts:
                    if option is None:
                        lost[k] += value
                found = {k: answer for k, total in lost.items() for answer in ("yes", "no") if tallies[answer] == total}
                if sorted(found.values()) != ["no", "yes"] or len(found) != len(lost):
                    raise ValueError(f"{contest_key}: {dict(lost)} do not match {tallies}")
                for unit, k, option, label, channel, measure, count_round, flags, value in facts:
                    if (channel, measure, count_round) != (0, 1, 0):
                        raise ValueError(f"{contest_key}: unexpected fact {channel} {measure} {count_round}")
                    target = (
                        options[found[k]]
                        if option is None
                        else options[label.lower()]
                        if label in ("Yes", "No")
                        else option
                    )
                    values.append((str(copy), str(unit), str(target), value, flags))
            if lines.height and contest_key not in held:
                seen = set(row["counties"])
                expected = (
                    None
                    if state == "AK"
                    else sizes[state_units[state][2]]
                    if area == state_units[state][1]
                    else len(next(members for text, members in ARIZONA.values() if seen <= members))
                    if state == "AZ" and not seen <= {"04013"} and not seen <= {"04019"}
                    else None
                )
                missing = (expected or 0) - len(seen)
                fine = cs.key(f"{SOURCE}.result_set", f"{contest_key}:medsl")
                cs.add(
                    "result_set",
                    id=fine,
                    election_id=cs.key("us.election", election_key),
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=medsl_sources[year],
                    is_default=default is None,
                    completeness=None,
                )
                note(fine, "methodology", f"{METHOD} {MEDSL_METHOD}" if default is None else MEDSL_METHOD)
                if missing > 0:
                    note(fine, "caveat", MISSING.format(counties=f"{missing} of the {expected} counties it covers"))
                sums = dict(lines.group_by("answer").agg(pl.col("votes").sum()).iter_rows())
                if default is None:
                    default = fine
                    tallies = sums
                    complete = missing <= 0
                else:
                    compared.append(
                        (contest_key, sums.get("yes", 0) - tallies["yes"], sums.get("no", 0) - tallies["no"])
                    )
                for unit_kind, code, county, answer, votes in (
                    lines.group_by("unit", "code", "county", "answer").agg(pl.col("votes").sum()).iter_rows()
                ):
                    if unit_kind == "precinct":
                        unit = cs.unit("us.precinct", code, date.fromisoformat(day))
                        if area in districts.values():
                            memberships.add((unit, area, day))
                        if fine not in cs.minted:
                            continue
                        if code not in existing:
                            units[code] = (state, county, day)
                    elif fine not in cs.minted:
                        continue
                    elif unit_kind in ("county", "place"):
                        unit = counties[code][0]
                    else:
                        unit = cs.unit("us.ak_house_district", code)
                        if unit in cs.minted:
                            raise ValueError(f"{contest_key}: no Alaska district {code}")
                    values.append((str(fine), str(unit), str(options[answer]), int(votes), 0))
            if totals.height:
                cs.add(
                    "result_set",
                    id=default,
                    election_id=cs.key("us.election", election_key),
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=official_sources[state],
                    is_default=True,
                    completeness=1,
                )
            if default is None or not complete:
                continue
            yes, no = tallies.get("yes", 0), tallies.get("no", 0)
            share = THRESHOLDS.get(state)
            retained = yes >= share * (yes + no) if share is not None else yes > no
            cs.add(
                "outcome",
                result_set_id=default,
                option_id=options["yes"],
                candidacy_id=candidacy,
                kind="elected" if retained else "lost",
                seats=1 if retained else None,
            )
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "value": pl.Int64,
                    "flags": pl.Int16,
                },
                orient="row",
            )
            .group_by("result_set_id", "unit_id", "option_id", "flags")
            .agg(pl.col("value").sum())
            .with_columns(channel=pl.lit(0, pl.Int16), measure=pl.lit(1, pl.Int16), count_round=pl.lit(0, pl.Int16)),
        )
        cs.frame(
            "reporting_unit",
            pl.DataFrame(
                [
                    {
                        "id": str(cs.unit("us.precinct", code, date.fromisoformat(day))),
                        "polity_id": str(cs.polity(f"US-{state}")),
                        "scheme_code": "us.precinct",
                        "code": code,
                        "parent_id": str(counties[county][0]),
                        "area_relation": "equals" if code in shaped else "within",
                        "area_id": str(shaped.get(code, counties[county][1])),
                        "kind": "standard",
                        "valid_from": date.fromisoformat(day),
                        "valid_to": date.fromisoformat(day) + timedelta(days=1),
                    }
                    for code, (state, county, day) in sorted(units.items())
                ],
                schema={
                    "id": pl.String,
                    "polity_id": pl.String,
                    "scheme_code": pl.String,
                    "code": pl.String,
                    "parent_id": pl.String,
                    "area_relation": pl.String,
                    "area_id": pl.String,
                    "kind": pl.String,
                    "valid_from": pl.Date,
                    "valid_to": pl.Date,
                },
            ),
        )
        for unit, area, day in sorted(memberships):
            cs.add(
                "reporting_unit_area",
                unit_id=unit,
                area_id=area,
                valid_from=date.fromisoformat(day),
                valid_to=date.fromisoformat(day) + timedelta(days=1),
            )
        drift = [item for item in compared if item[1] or item[2]]
        print(f"{len(compared)} questions with both MEDSL and official totals, {len(drift)} differ: {drift[:20]}")
        print(f"{len(values)} values, {len(units)} new precincts, {len(persons)} judges")
        return cs.write()
