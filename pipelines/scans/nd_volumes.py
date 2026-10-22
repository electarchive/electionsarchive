import csv
import json
import re
from pathlib import Path
from urllib.parse import unquote

from ea.layout import day_of

HERE = Path(__file__).resolve().parent
OFFICES = {
    "president": "presidential electors",
    "governor": "Governor",
    "lieutenant-governor": "Lieutenant Governor",
    "secretary-of-state": "Secretary of State",
    "auditor": "State Auditor",
    "treasurer": "State Treasurer",
    "attorney-general": "Attorney General",
    "superintendent-of-public-instruction": "Superintendent of Public Instruction",
    "commissioner-of-agriculture": "Commissioner of Agriculture and Labor",
    "railroad-commissioner": "Railroad Commissioner",
    "public-service-commissioner": "Public Service Commissioner",
    "tax-commissioner": "Tax Commissioner",
    "insurance-commissioner": "Insurance Commissioner or Commissioner of Insurance",
    "house": "U.S. Representative (one table, district 0 if at large)",
    "senate": "U.S. Senator",
}

with (HERE / "nd-sos-abstracts.tsv").open() as handle:
    rows = list(csv.DictReader(handle, delimiter="\t"))
seen: dict[str, int] = {}
for row in rows:
    seen[row["year"]] = seen.get(row["year"], 0) + 1
counter: dict[str, int] = {}
for row in rows:
    year = int(row["year"])
    counter[row["year"]] = counter.get(row["year"], 0) + 1
    name = f"nd-{year}" if seen[row["year"]] == 1 else f"nd-{year}-{counter[row['year']]}"
    day = row["date"] if re.fullmatch(r"\d{4}-\d\d-\d\d", row["date"]) else day_of(year)
    file = unquote(row["url"]).rsplit("/", 1)[-1].removesuffix(".pdf")
    volume = {
        "year": year,
        "day": day,
        "polity": "US-ND",
        "publisher": "North Dakota Secretary of State",
        "title": f"Official abstract of votes cast at the general election held {day}, {file}",
        "license": "Public record",
        "file": {"url": row["url"]},
        "raw_source": "us.scans",
        "create": True,
        "offices": OFFICES,
        "leaves": list(range(1, int(row["pages"]) + 1)),
    }
    path = HERE / "volumes" / f"{name}.json"
    if name != "nd-1952":
        path.write_text(json.dumps(volume, indent=2))
        print(name, day, volume["leaves"])
