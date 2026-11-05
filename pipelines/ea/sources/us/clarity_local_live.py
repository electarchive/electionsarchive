import re
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree

import httpx
import polars as pl

from ea.sources.us.enr_2026 import table

STAMP = re.compile(r"(.+) ([ECMP])([SD])T")
LOCAL = re.compile(
    r"(?i)^(mayor|council member|board of (directors|trustees)|regent|school trustee|county |justice of the peace)"
)


def collect(client: httpx.Client, state: str, tenant: str, eid: str) -> dict[str, tuple[bytes, str]]:
    base = f"https://results.enr.clarityelections.com/{state}/{tenant}/{eid}"
    version = client.get(f"{base}/current_ver.txt")
    version.raise_for_status()
    url = f"{base}/{version.text.strip()}/reports/detailxml.zip"
    response = client.get(url)
    response.raise_for_status()
    return {url: (response.content, ".zip")}


def frame(state: str, county: str, paths: dict[str, Path]) -> pl.DataFrame:
    found = []
    for url, path in paths.items():
        with zipfile.ZipFile(path) as archive:
            root = ElementTree.fromstring(archive.read("detail.xml"))
        stamped = STAMP.fullmatch((root.findtext("Timestamp") or "").strip())
        stamp = (
            datetime.strptime(stamped[1], "%m/%d/%Y %I:%M:%S %p")
            .replace(
                tzinfo=timezone(timedelta(hours={"E": -5, "C": -6, "M": -7, "P": -8}[stamped[2]] + (stamped[3] == "D")))
            )
            .isoformat()
            if stamped
            else None
        )
        for contest in root.iter("Contest"):
            title = (contest.get("text") or "").strip()
            if contest.get("isQuestion") == "true" or not LOCAL.search(title):
                continue
            for choice in contest.iter("Choice"):
                name = re.sub(r"\s*(\(I\)|\*)$", "", choice.get("text") or "").strip()
                if name.lower() in ("over votes", "under votes"):
                    continue
                found.append(
                    {
                        "state": state,
                        "page": url,
                        "scope": "",
                        "contest": title,
                        "office": "other",
                        "district": "",
                        "party": None,
                        "county": county,
                        "candidate": name,
                        "write_in": "write-in" in name.lower(),
                        "votes": int(choice.get("totalVotes") or 0),
                        "as_of": stamp,
                        "reporting": int(contest.get("precinctsReported") or 0),
                        "total": int(contest.get("precinctsReporting") or 0),
                    }
                )
    return pl.DataFrame(found, schema=table({}).schema)
