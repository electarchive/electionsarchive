import json
import re
import zipfile
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from ea import raw
from ea.sources.us import enr_clarity

SOURCE = "us.pa_allegheny"
VERSION = "1"
PLATFORM = "pa_allegheny"
SITE = "https://data.wprdc.org/"
LISTING = SITE + "dataset/election-results"
PACKAGE = SITE + "api/3/action/package_show?id=election-results"
FIPS = "42"


def zips() -> list[str]:
    package = json.loads(raw.cached(SOURCE, PACKAGE).path.read_bytes())
    return [
        resource["url"]
        for resource in package["result"]["resources"]
        if resource["format"] == "ZIP" and "detailxml" in resource["url"]
    ]


def fetch() -> None:
    raw.fetch(SOURCE, PACKAGE, suffix=".json")
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips=FIPS, layer=layer), suffix=".zip")
    for url in zips():
        raw.fetch(SOURCE, url, suffix=".zip")


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    tenant = next(iter(registry))
    for url in zips():
        with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive, archive.open("detail.xml") as stream:
            title = ""
            day = None
            contests: list[dict[str, Any]] = []
            contest: dict[str, Any] | None = None
            choice: dict[str, Any] | None = None
            skip = False
            for event, element in ElementTree.iterparse(stream, events=("start", "end")):
                tag = element.tag
                if event == "start":
                    if tag == "Contest":
                        text = element.get("text") or ""
                        skip = enr_clarity.SKIPPED.search(enr_clarity._plain(text)) is not None
                        contest = {
                            "key": element.get("key"),
                            "text": text,
                            "vote_for": int(element.get("voteFor") or 1),
                            "choices": [],
                        }
                    elif tag == "Choice" and contest is not None:
                        choice = {
                            "text": element.get("text") or "",
                            "party": element.get("party"),
                            "total": element.get("totalVotes") or "0",
                            "places": [],
                        }
                    continue
                if tag == "ElectionName":
                    title = (element.text or "").strip()
                elif tag == "ElectionDate":
                    day = datetime.strptime((element.text or "").strip(), "%m/%d/%Y").date()
                elif tag == "Precinct" and choice is not None and not skip:
                    choice["places"].append((element.get("name") or "", element.get("votes") or "0"))
                elif tag == "Choice" and contest is not None and choice is not None:
                    if not skip:
                        contest["choices"].append(choice)
                    choice = None
                    element.clear()
                elif tag == "Contest" and contest is not None:
                    if not skip:
                        contests.append(contest)
                    contest = None
                    element.clear()
        if re.search(r"(?i)special", title) or day is None:
            continue
        yield {
            "tenant": tenant,
            "election": title,
            "day": day,
            "title": title,
            "official": True,
            "primary": False,
            "contests": contests,
        }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: link.startswith(SITE))
