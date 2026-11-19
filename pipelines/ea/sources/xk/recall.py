"""Kosovo's votes of 21 April 2024 on removing the mayors of four northern municipalities, from the KQZ.

A petition of a fifth of a municipality's registered voters puts the removal of its mayor to a vote, and the mayor is
removed only if a majority of all registered voters vote for it (Law 03/L-040 on Local Self-Government, art. 72). The
Central Election Commission ran the only such votes on 21 April 2024 in Leposaviq, Zubin Potok, Zveçan, and North
Mitrovica. It reported their progress in four press conferences on its site: turnout at 11:00 and 15:00, and at the
close of the polls the registered voters and voters of each municipality, with its declaration that turnout fell short
of the threshold and every initiative failed. It published no counts of votes for or against removal, so each
municipality's contest is stored as turnout only, with no options.

The conference pages are read from the raw cache, where `store_fetched` puts the copies a browser or an archive saved.
"""

import functools
import hashlib
import html
import json
import os
import re
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ea import raw
from ea.changeset import slug
from ea.package import Package
from ea.sources.xk import kqz
from ea.sources.xk.local import NAMES

SOURCE = kqz.SOURCE
FETCHED = "EA_KQZ_RECALL_FETCHED"
DAY = date(2024, 4, 21)
KEY = f"{SOURCE}/{DAY}"
RECALL_SOURCE = kqz.key("source", "xk.kqz.recall")
REGISTERED, VOTERS = 2, 14
LOCAL = ZoneInfo("Europe/Belgrade")

SECOND = (
    "https://kqz-ks.org/konferenca-e-dyte-kqz-vazhdon-prcesi-i-votimit-ne-komunat-leposaviq-zubin-potok-zvecan-dhe-"
    "mitrovice-e-veriut/"
)
THIRD = (
    "https://kqz-ks.org/konferenca-e-trete-kqz-njoftim-lidhur-e-ecurine-e-procesit-te-votimit-ne-komunat-leposaviq-"
    "zubin-potok-zvecan-dhe-mitrovice-e-veriut/"
)
FOURTH = (
    "https://kqz-ks.org/konferenca-e-katert-kqz-iniciativa-qytetare-per-largimin-e-kryetareve-te-komunave-leposaviq-"
    "zubin-potok-zvecan-dhe-mitrovice-e-veriut-ka-deshtuar/"
)
PAGES = (SECOND, THIRD, FOURTH)

# The municipalities in the order the conferences name them, by the Commission's code, with the stems that name them.
MUNICIPALITIES = {"12": "Leposaviq", "28": "Zubin Potok", "29": "Zveçan", "38": "Mitrovic"}
# The interim conferences, each with the clock time its counts were taken at.
INTERIM = {SECOND: "11:00", THIRD: "15:00"}
THRESHOLD_NOTE = (
    "The Central Election Commission published only turnout for this vote: the registered voters and the voters of "
    "each municipality, and the number who had voted by 11:00 and by 15:00. It published no counts of votes for or "
    "against the mayor's removal. At the close of the polls it declared that a majority of the registered voters had "
    "not voted, as Article 72 of the Law on Local Self-Government requires for a removal, so the 50% threshold was "
    "not reached and the citizens' initiative failed; the mayor stayed in office."
)
INTERIM_NOTE = (
    "The number of voters who had voted by {time} on election day, as the Central Election Commission announced it "
    "from the reports of the Municipal Election Commissions at its press conference that afternoon. It is a "
    "provisional count, not the final turnout."
)
FINAL_NOTE = (
    "The registered voters and the voters of the municipality as the Central Election Commission announced them at "
    "its press conference after the polls closed at 19:00, the final figures it published for this vote."
)


def store_fetched(folder: Path) -> int:
    """Put the saved conference pages into the raw cache; `folder` holds them and their `index.json`."""
    index = json.loads((folder / "index.json").read_text())
    stored = 0
    for link, entry in index.items():
        if link not in PAGES:
            continue
        content = (folder / entry["file"]).read_bytes()
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise ValueError(f"{link} does not match its checksum")
        raw.store(
            SOURCE,
            link,
            content,
            suffix=".html",
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            media_type="text/html",
        )
        stored += 1
    return stored


def fetch() -> None:
    folder = os.environ.get(FETCHED)
    if folder:
        print(f"{store_fetched(Path(folder))} recall conference pages stored", flush=True)


@functools.cache
def text(url: str) -> str:
    """The words of a conference page's article."""
    page = raw.cached(SOURCE, url).path.read_text(encoding="utf-8")
    page = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.DOTALL)
    words = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", page)).replace("\xa0", " ").split())
    start = words.find("Search for:")
    end = words.find("Rreth KQZ", start)
    if start < 0 or end < 0:
        raise ValueError(f"{url}: the article is not where it was")
    return words[start:end]


def number(printed: str) -> int:
    return int(printed.replace(",", "").replace(".", ""))


def interim(url: str) -> dict[str, int]:
    """The voters by municipality that an interim conference announced."""
    words = text(url)
    time = INTERIM[url]
    found = re.search(rf"deri në orën {time} është si në vijim: (.+?)\.", words)
    if not found:
        raise ValueError(f"{url}: no turnout at {time}")
    counts: dict[str, int] = {}
    for m, stem in MUNICIPALITIES.items():
        part = re.search(rf"{stem}\w*(?: e Veriut)?(?: kanë votuar)? (\d[\d,.]*)", found.group(1))
        if not part:
            raise ValueError(f"{url}: no turnout for {stem}")
        counts[m] = number(part.group(1))
    return counts


def final() -> dict[str, tuple[int, int]]:
    """The voters and registered voters by municipality that the last conference announced."""
    words = text(FOURTH)
    counts: dict[str, tuple[int, int]] = {}
    for m, stem in MUNICIPALITIES.items():
        part = re.search(
            rf"Në komunën e {stem}\w*(?: së Veriut)? (?:kanë votuar (\d[\d,.]*) votues|nuk ka votuar asnjë votues) "
            rf"nga (\d[\d,.]*) (?:apo|pra kemi) ([\d.]+)%",
            words,
        )
        if not part:
            raise ValueError(f"{FOURTH}: no final turnout for {stem}")
        voters = number(part.group(1)) if part.group(1) else 0
        registered = number(part.group(2))
        if abs(round(100 * voters / registered, 2) - float(part.group(3))) > 0.011:
            raise ValueError(f"{stem}: {voters} of {registered} is not the {part.group(3)}% the page prints")
        counts[m] = (voters, registered)
    if "ka dështuar" not in words or "50%" not in words:
        raise ValueError("the last conference no longer declares the initiatives failed")
    return counts


def election_package() -> Package:
    p = Package(
        KEY,
        SOURCE,
        "The votes of 21 April 2024 on removing the mayors of Leposaviq, Zubin Potok, Zveçan, and North Mitrovica: "
        "the registered voters and voters of each municipality from the Central Election Commission",
    )
    # The municipalities and their mayors' offices are rows of the geo and registry packages, already loaded.
    p.external |= {kqz.KOSOVO, "xk.komuna"}
    p.external |= {kqz.municipality(m) for m in MUNICIPALITIES}
    p.external |= {kqz.office(m) for m in MUNICIPALITIES}
    p.add(
        "source",
        id=RECALL_SOURCE,
        kind="official_body",
        publisher="Komisioni Qendror i Zgjedhjeve",
        title="Press conferences on the votes to remove the mayors of four northern municipalities, 21 April 2024",
        url=FOURTH,
        license="Official election results of the Republic of Kosovo, published for public use",
        license_url="https://kqz-ks.org/rezultatet/",
        attribution="Central Election Commission of the Republic of Kosovo (KQZ), kqz-ks.org",
    )
    for url in PAGES:
        p.artifact(raw.cached(SOURCE, url), RECALL_SOURCE)
    election = kqz.key("election", DAY, "recall")
    p.add("election", id=election, polity_id=kqz.KOSOVO, kind="recall", date_from=DAY, date_to=DAY, status="held")
    p.name(election, "2024 Kosovan votes on removing the mayors of four northern municipalities", "en", "official")
    p.name(
        election,
        "Votimi për largimin e kryetarëve të komunave Leposaviq, Zubin Potok, Zveçan dhe Mitrovicë e Veriut",
        "sq",
        "native",
        False,
    )
    path = f"xk/{DAY}-mayoral-recall"
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.add("note", id=kqz.key("note", election), entity_id=election, kind="context", body_md=THRESHOLD_NOTE, lang="en")
    finals = final()
    interims = {url: interim(url) for url in INTERIM}
    rows: list[tuple[str, str, int, int]] = []
    for m, (voters, registered) in finals.items():
        sq, sr = NAMES[m]
        contest = kqz.key("contest", DAY, "recall", m)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="ballot_question",
            office_id=kqz.office(m),
            district_area_id=kqz.municipality(m),
            round_number=1,
            default_result_kind="counted",
        )
        p.add("ballot_question", contest_id=contest, type="recall_question")
        p.name(contest, f"Removal of the Mayor of {sq}", "en", "official")
        p.name(contest, f"Largimi nga funksioni i kryetarit të Komunës së {sq}", "sq", "native", False)
        p.name(contest, f"Razrešenje predsednika opštine {sr}", "sr", "native", False)
        p.add("slug", path=f"{path}/{slug(sq)}", entity_id=contest, is_current=True)
        unit = kqz.key("unit", f"{DAY}:municipality:{m}")
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=kqz.KOSOVO,
            scheme_code="xk.komuna",
            code=f"{DAY}:{m}",
            area_relation="equals",
            area_id=kqz.municipality(m),
            kind="aggregate",
        )
        p.name(unit, sq, "sq", "official")
        rs = kqz.key("result_set", DAY, "recall", m)
        p.add(
            "result_set",
            id=rs,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=RECALL_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=kqz.key("note", rs), entity_id=rs, kind="methodology", body_md=FINAL_NOTE, lang="en")
        p.add(
            "note", id=kqz.key("note", rs, "threshold"), entity_id=rs, kind="caveat", body_md=THRESHOLD_NOTE, lang="en"
        )
        p.add("declared", result_set_id=rs, option_id=None, measure=REGISTERED, value=registered)
        p.add("declared", result_set_id=rs, option_id=None, measure=VOTERS, value=voters)
        rows += [(rs, unit, REGISTERED, registered), (rs, unit, VOTERS, voters)]
        for url, time in INTERIM.items():
            hour, minute = (int(part) for part in time.split(":"))
            early = kqz.key("result_set", DAY, "recall", m, time)
            p.add(
                "result_set",
                id=early,
                election_id=election,
                contest_id=contest,
                stage="live",
                kind="counted",
                source_id=RECALL_SOURCE,
                status="active",
                is_default=False,
                as_of=datetime(DAY.year, DAY.month, DAY.day, hour, minute, tzinfo=LOCAL).astimezone(UTC),
            )
            p.add(
                "note",
                id=kqz.key("note", early),
                entity_id=early,
                kind="methodology",
                body_md=INTERIM_NOTE.format(time=time),
                lang="en",
            )
            rows.append((early, unit, VOTERS, interims[url][m]))
    facts = kqz.Facts()
    for rs, unit, measure, value in rows:
        facts.add(rs, unit, None, measure, value)
    p.frame("result_value", facts.frame())
    return p
