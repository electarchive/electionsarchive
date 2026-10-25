import html
import re
from collections.abc import Iterator, Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from ea import portals, raw
from ea.sources.us import enr_clarity

SOURCE = "us.enr_static"
VERSION = "1"
PLATFORM = "hosted_static"
HOST = "www.livevoterturnout.com"


def fetch() -> None:
    _, index = raw._index(SOURCE)
    listed = enr_clarity.tenants(PLATFORM)
    for fips in sorted({row["county_fips"][:2] for row in listed}):
        for layer in enr_clarity.LAYERS:
            raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips=fips, layer=layer), suffix=".zip")
    for row in listed:
        fetched = 0
        for number in range(1, int(row["last"] or 0) + 6):
            link = f"{row['tenant']}{number}/en/Index_{number}.html"
            if link in index:
                continue
            status, body, _ = portals.get(link)
            if status != 200:
                if number > int(row["last"] or 0):
                    break
                continue
            if b"election-title" not in body:
                continue
            raw.store(SOURCE, link, body, suffix=".html", retrieved_at=datetime.now(UTC), media_type="text/html")
            fetched += 1
        if HOST in portals.stopped:
            raise RuntimeError(f"{HOST} asked us to stop")
        print(f"{row['state']} {row['county']}: {fetched} fetched", flush=True)


def _text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    index = raw._index(SOURCE)[1]
    for tenant in registry:
        for link in sorted(key for key in index if key.startswith(tenant) and key.endswith(".html")):
            page = raw.cached(SOURCE, link).path.read_text(errors="replace")
            heading = re.search(r'class="election-title"[^>]*>(.*?)</h1>', page, re.DOTALL)
            if heading is None:
                print(f"{link}: no election title")
                continue
            title = _text(re.sub(r"<[^>]+>", " ", heading[1]))
            stated = re.search(
                r"([A-Z][a-z]+) (\d{1,2})(?:st|nd|rd|th)?, (\d{4})", title.replace("Janurary", "January")
            )
            numeric = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", title)
            day = (
                DAYS.get(link.removeprefix("https://www.livevoterturnout.com/ENR/").split("/en/")[0])
                or (datetime.strptime(" ".join(stated.groups()), "%B %d %Y").date() if stated else None)
                or (date(int(numeric[3]), int(numeric[1]), int(numeric[2])) if numeric else None)
            )
            if day is None:
                print(f"{link}: no date for {title!r}")
                continue
            contests: dict[str, dict[str, Any]] = {}
            for race, text in re.findall(r'id="raceHeading_(\d+)" class="sr-only">Contest: ([^<]*)</span>', page):
                found = re.fullmatch(r"(.*), VOTE FOR (\d+)", re.sub(r", Precincts Reported:.*$", "", _text(text)))
                contests[race] = {
                    "key": race,
                    "text": found[1] if found else _text(text),
                    "vote_for": int(found[2]) if found else 1,
                    "choices": {},
                }
            for race, choice, name in re.findall(
                r'data-race="(\d+)" data-contestant="(\d+)" data-candidate="true" class="content-data">\s*'
                r'<span[^>]*aria-hidden="true">([^<]*)</span>',
                page,
            ):
                label = re.sub(
                    rf"\s*\(({PARTY_WORDS})\)$|,\s*({PARTY_WORDS})(?: PARTY)?$", "", _text(name), flags=re.IGNORECASE
                )
                party = _name(name)[1] or (
                    found[1] or found[2]
                    if (
                        found := re.search(
                            rf"\(({PARTY_WORDS})\)$|,\s*({PARTY_WORDS})(?: PARTY)?$", _text(name), re.IGNORECASE
                        )
                    )
                    else None
                )
                contests[race]["choices"][choice] = {"text": label, "party": party, "total": "0", "places": []}
            for race, choice, votes in re.findall(
                r'data-race="(\d+)" data-contestant="(\d+)" data-votes="true" class="content-data">([^<]*)<', page
            ):
                contests[race]["choices"][choice]["total"] = votes.strip().replace(",", "")
            precincts = dict(re.findall(r'id="pH_(\d+)" class="sr-only">Precinct: ([^<]*)</span>', page))
            for place, race, body in re.findall(r'id="pRL_(\d+)_(\d+)"(.*?)(?=id="pRL_|id="pH_|$)', page, re.DOTALL):
                names = re.findall(r'id="pRCL_\d+_\d+_\d+" class="sr-only">([^<]*)</span>', body)
                votes = re.findall(r'dv="true" class="pcd">([^<]*)<', body)
                choices = {_name(choice["text"])[0]: choice for choice in contests[race]["choices"].values()}
                for name, value in zip(names, votes, strict=True):
                    key, party = _name(name)
                    choice = choices.get(key) or next(
                        iter(
                            [other for other_key, other in choices.items() if other_key in key or key in other_key]
                            if sum(other_key in key or key in other_key for other_key in choices) == 1
                            else []
                        ),
                        None,
                    )
                    if choice is None:
                        overlap = sorted(
                            ((len(set(key.split()) & set(other_key.split())), other_key) for other_key in choices),
                            reverse=True,
                        )
                        if not overlap or not overlap[0][0] or len(overlap) > 1 and overlap[1][0] == overlap[0][0]:
                            raise ValueError(f"{link}: no choice for {name!r} in contest {race}")
                        choice = choices[overlap[0][1]]
                    choice["party"] = choice["party"] or party
                    choice["places"].append((_text(precincts[place]), value.strip().replace(",", "") or "0"))
            disagree = [
                (
                    contest["text"],
                    choice["text"],
                    int(choice["total"] or 0),
                    sum(int(votes) for _, votes in choice["places"]),
                )
                for contest in contests.values()
                for choice in contest["choices"].values()
                if choice["places"] and sum(int(votes) for _, votes in choice["places"]) != int(choice["total"] or 0)
            ]
            if disagree:
                print(
                    f"{link}: {len(disagree)} precinct sums disagree with the printed totals, skipped: {disagree[:2]}"
                )
                continue
            if all(
                choice["total"] in ("", "0") for contest in contests.values() for choice in contest["choices"].values()
            ):
                print(f"{link}: no votes, skipped")
                continue
            yield {
                "tenant": tenant,
                "election": link.rsplit("/", 3)[1],
                "day": day,
                "title": title,
                "official": bool(re.search(r'id="lblOfficialResults"[^>]*>\s*OFFI?CI?AL', page, re.IGNORECASE)),
                "primary": False,
                "contests": [
                    {**contest, "choices": list(contest["choices"].values())} for contest in contests.values()
                ],
            }


DAYS = {
    "buckspaenr/2": date(2020, 11, 3),
    "buckspaenr/4": date(2021, 5, 18),
    "buckspaenr/6": date(2021, 11, 2),
    "chesterpaenr/2": date(2025, 11, 4),
    "chesterpaenr/3": date(2026, 5, 19),
    "franklinpaenr/2": date(2020, 11, 3),
    "lehighpaenr/1": date(2021, 11, 2),
    "lehighpaenr/3": date(2022, 5, 17),
    "lehighpaenr/4": date(2022, 11, 8),
    "lehighpaenr/5": date(2023, 5, 16),
    "lehighpaenr/6": date(2023, 11, 7),
    "lehighpaenr/7": date(2024, 4, 23),
    "lehighpaenr/8": date(2024, 11, 5),
    "lehighpaenr/9": date(2025, 5, 20),
    "lehighpaenr/10": date(2025, 11, 4),
    "lehighpaenr/12": date(2026, 5, 19),
    "ellistxenr/2": date(2025, 11, 4),
    "franklinpaenr/6": date(2022, 5, 17),
    "franklinpaenr/8": date(2023, 5, 16),
    "franklinpaenr/14": date(2026, 5, 19),
    "haystxenr/6": date(2020, 12, 8),
    "haystxenr/8": date(2021, 11, 2),
    "haystxenr/13": date(2023, 5, 6),
    "northamptonpaenr/3": date(2023, 11, 7),
    "northamptonpaenr/4": date(2023, 11, 7),
    "northamptonpaenr/6": date(2024, 4, 23),
    "northamptonpaenr/7": date(2024, 11, 5),
    "northamptonpaenr/8": date(2025, 5, 20),
    "northamptonpaenr/10": date(2025, 11, 4),
    "northamptonpaenr/11": date(2026, 5, 19),
    "williamsontxenr/3": date(2024, 11, 5),
}
PARTY_WORDS = r"DEM|DEMOCRAT|DEMOCRATIC|REP|REPUBLICAN|LIB|LIBERTARIAN|GRN|GREEN|IND|INDEPENDENT|CONSTITUTION|FORWARD"


def _name(text: str) -> tuple[str, str | None]:
    name = _text(text).upper()
    suffix = re.search(rf",\s*({PARTY_WORDS})(?: PARTY)?$|,\s*([A-Z ]+) PARTY$", name)
    prefix = re.match(rf"({PARTY_WORDS}) (?=\S)", name)
    party = (suffix and (suffix[1] or suffix[2])) or (prefix and prefix[1]) or None
    name = name[: suffix.start()] if suffix else name
    name = name[prefix.end() :] if prefix else name
    return re.sub(r"[^A-Z0-9]+", " ", name).strip(), party


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: link.startswith(tenant))
