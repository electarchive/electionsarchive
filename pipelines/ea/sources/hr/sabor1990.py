"""Croatia's 1990 election to the three chambers of the Sabor, digitized from the State Electoral Commission's scans.

The authority's archive publishes the first multiparty election only as scanned reports, one per chamber, each with
a supplementary report: the Socio-Political Council (Društveno-političko vijeće), the Council of Municipalities
(Vijeće općina), and the Council of Associated Labor (Vijeće udruženog rada). Every chamber elected one member in
each of its constituencies; a candidate with the number of votes the law required was elected, and where no candidate
had it, the vote was repeated among the leading candidates. The reports print, for each constituency and round, the registered voters, the voters, the
invalid ballots, and each candidate's votes. The scans carry no text layer, so each figure was read by character
recognition, compared with a second reading, and checked against the page image wherever the readings disagreed or the
report's own sums did not hold. The transcription lives in ``sabor1990.json`` beside this module and keeps each
figure's page and bounding box; ``izbori`` builds the package along with its own.
"""

import json
import re
from datetime import date
from difflib import SequenceMatcher
from functools import cache
from pathlib import Path
from typing import Any

from ea import raw
from ea.package import Package
from ea.sources.hr import izbori as z
from ea.sources.hr import skenovi as s

DATA_FILE = Path(__file__).with_name("sabor1990.json")
FIRST, SECOND = date(1990, 4, 22), date(1990, 5, 6)
ARCHIVE = f"{z.DATA}/parlament/1990/rezultati"
REPORTS = {
    "dpv": "1990_1_1_Sabor_Rezultati_Drustveno_politicko_vijece.pdf",
    "dpv_supplement": "1990_1_2_Sabor_Rezultati_Drustveno_politicko_vijece_dopunski_izvjestaj.pdf",
    "vo": "1990_1_3_Sabor_Rezultati_Vijece_opcina.pdf",
    "vo_supplement": "1990_1_4_Sabor_Rezultati_Vijece_opcina_dopunski_izvjestaj.pdf",
    "vur": "1990_1_5_Sabor_Rezultati_Vijece_udruzenog_rada.pdf",
    "vur_supplement": "1990_1_6_Sabor_Rezultati_Vijece_udruzenog_rada_dopunski_izvjestaj.pdf",
}
URLS = {key: f"{ARCHIVE}/{name}" for key, name in REPORTS.items()}
CHAMBERS = {
    "dpv": ("Socio-Political Council", "Društveno-političko vijeće", "socio-political"),
    "vo": ("Council of Municipalities", "Vijeće općina", "municipalities"),
    "vur": ("Council of Associated Labor", "Vijeće udruženog rada", "associated-labor"),
}
BODIES = {key: z.key("body", f"sabor_1990_{key}") for key in CHAMBERS}
SYSTEM = z.key("electoral_system", "majority_1990")
SCHEME = "hr.izborna_jedinica_1990"
SMALL = {"NA", "U", "POD"}
NOTE = (
    "Digitized from the State Electoral Commission's scanned report: each figure was read from the page image by "
    "character recognition, compared with a second reading, checked against the page image wherever the readings "
    "disagreed or the report's own sums did not hold, and keeps its page and bounding box. Names are spelled as the "
    "report prints them in capitals; its scans show few diacritics on capital letters, so some may be missing."
)


@cache
def data() -> dict[str, Any]:
    return json.loads(DATA_FILE.read_text())


def package_key() -> str:
    return f"{z.SOURCE}/sabor-{FIRST}"


def packages() -> list[str]:
    return [package_key()]


def fetch() -> None:
    for url in URLS.values():
        raw.fetch(z.SOURCE, url, suffix=".pdf")


def parse() -> None:
    build().write()


def constituencies(chamber: str) -> list[dict[str, Any]]:
    return data()["chambers"][chamber]["constituencies"]


# Parties and the electoral system, added to the izbori registry.


def tokens(candidate: dict[str, Any]) -> list[str] | None:
    """The parties a candidate's line names: none for an independent, and None where the line names no one."""
    return candidate["parties"] if candidate["parties"] or candidate["independent"] else None


def _parties() -> tuple[dict[str, str], dict[str, list[str]]]:
    single: dict[str, str] = {}
    blocs: dict[str, list[str]] = {}
    for chamber in CHAMBERS:
        for district in constituencies(chamber):
            for r in district["rounds"]:
                for candidate in r["candidates"]:
                    named = candidate["parties"]
                    for token in named:
                        single.setdefault(s.party_id(token), s.party_name(token))
                    if len(named) > 1:
                        blocs.setdefault(s.option_party(named) or "", named)
    return single, blocs


def registry(p: Package, known: set[str]) -> None:
    """Add the three chambers, the 1990 system, and the parties of this election the other files do not name."""
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=z.CROATIA,
        valid_from=date(1990, 1, 1),
        valid_to=date(1992, 1, 1),
        rules={"formula": "two_round"},
        rules_version=1,
    )
    p.add(
        "note",
        id=z.key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=(
            "In 1990, each of the three chambers of the Sabor elected one member in each of its constituencies. A "
            "candidate with the number of votes the law required (article 58, paragraph 1, of the law on the election "
            "and recall of delegates and deputies) was elected; where none had it, the vote was repeated among the "
            "leading candidates (paragraph 2), and the one with the most votes was elected (paragraph 6)."
        ),
        lang="en",
    )
    for key, (english, native, _) in CHAMBERS.items():
        p.add("body", id=BODIES[key], polity_id=z.CROATIA, kind="council")
        p.name(BODIES[key], f"{english} of the Sabor of the Republic of Croatia", "en", "official")
        p.name(BODIES[key], f"{native} Sabora Republike Hrvatske", "hr", "native", False)
    single, blocs = _parties()
    for id, name in sorted(single.items()):
        if id in known:
            continue
        known.add(id)
        p.add("party", id=id, kind="party", polity_id=z.CROATIA)
        p.add("external_id", entity_id=id, scheme="hr.izbori.stranka", value=z.label(name))
        p.name(id, name, "hr", "official")
        short = z.abbreviation(name)
        if short:
            p.name(id, short, "hr", "abbreviation", False)
    for id, tokens in sorted(blocs.items()):
        if id in known:
            continue
        known.add(id)
        p.add("party", id=id, kind="bloc", polity_id=z.CROATIA)
        p.name(id, ", ".join(tokens), "hr", "official")
        p.name(id, ", ".join(sorted({s.party_name(t) for t in tokens})), "hr", "ballot", False)


# The package.


def title(text: str) -> str:
    """A constituency's name as the report prints it in capitals, in sentence case."""
    words = [w if re.fullmatch(r"[IVX]+", w) else w.lower() if w in SMALL else w.capitalize() for w in text.split()]
    return " ".join(words)


def slug_part(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", z.fold(text)).strip("-")


def _page_ids(p: Package) -> dict[str, dict[int, str]]:
    out: dict[str, dict[int, str]] = {}
    for key, url in URLS.items():
        stored = raw.cached(z.SOURCE, url)
        artifact = p.artifact(stored, z.SOURCE_ID)
        pages: dict[int, str] = {}
        for number, (width, height) in sorted(s._sizes(stored.path).items()):
            pages[number] = p.id("page", stored.sha256, number)
            p.add(
                "source_page",
                id=pages[number],
                artifact_id=artifact,
                page_number=number,
                image_key=f"pages/{z.SOURCE}/{stored.sha256}-{number}.png",
                width=round(width * 100 / 72),
                height=round(height * 100 / 72),
            )
        out[key] = pages
    return out


def build() -> Package:
    p = s._new(f"sabor-{FIRST}", "The 1990 election to the three chambers of the Sabor, digitized from the reports")
    pages = _page_ids(p)
    election = z.key("election", FIRST, "sabor")
    p.add(
        "election",
        id=election,
        polity_id=z.CROATIA,
        kind="general",
        date_from=FIRST,
        date_to=SECOND,
        status="held",
    )
    p.name(election, "1990 Croatian parliamentary election", "en", "official")
    p.name(election, "Izbori za Sabor Republike Hrvatske 1990.", "hr", "native", False)
    p.add("slug", path=f"hr/{FIRST}-parliamentary", entity_id=election, is_current=True)
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=z.CROATIA,
        kind="electoral_district",
        rank=1,
        noun="constituency",
        kinds=None,
        code_system=None,
    )
    values = s.Values()
    for chamber, (english, native, slug) in CHAMBERS.items():
        for index, district in enumerate(constituencies(chamber), 1):
            _constituency(p, election, chamber, index, district, english, native, slug, pages, values)
    values.write(p)
    return p


def _constituency(
    p: Package,
    election: str,
    chamber: str,
    index: int,
    district: dict[str, Any],
    english: str,
    native: str,
    slug: str,
    pages: dict[str, dict[int, str]],
    values: s.Values,
) -> None:
    number = district["no"] or district.get("index") or index
    name = title(district["name"])
    code = f"{chamber}:{number}"
    unit = s._unit(p, f"{FIRST}:{code}", SCHEME, name=f"{name} ({english})")
    page_ids = pages[district["report"]]
    previous: str | None = None
    rounds = district["rounds"]
    elected = _winner(district)
    for n, r in enumerate(rounds, 1):
        tag = f"{FIRST}:{code}:{n}"
        contest = z.key("contest", tag)
        result_set = z.key("result_set", tag)
        path = f"hr/{FIRST}-parliamentary/{slug}/{number}-{slug_part(name)}" + ("" if n == 1 else "-repeat")
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=BODIES[chamber],
            magnitude=1,
            round_number=n,
            previous_round_id=previous,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{name} constituency, {english}" + ("" if n == 1 else " (repeat vote)"), "en", "official")
        p.name(
            contest,
            f"Izborna jedinica {name}, {native}" + ("" if n == 1 else " (ponovljeni izbori)"),
            "hr",
            "native",
            False,
        )
        p.add("slug", path=path, entity_id=contest, is_current=True)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=z.SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        extra = ""
        known = [c["votes"] for c in r["candidates"]]
        if None not in known:
            mismatch = r["cast"] - sum(known) - r["invalid"]
            if mismatch:
                extra = (
                    f" The report's own figures differ here: the candidates' votes and the invalid ballots add up to "
                    f"{r['cast'] - mismatch:,}, {abs(mismatch):,} {'fewer' if mismatch > 0 else 'more'} than the voters."
                )
        for caveat in district.get("caveats", []):
            if caveat["round"] == n:
                extra += " " + caveat["text"]
        s._note(
            p,
            result_set,
            "methodology",
            f"{NOTE} The report prints the constituency's registered voters, voters, "
            f"invalid ballots, and each candidate's votes.{extra}",
        )
        b = r["boxes"]
        values.add(result_set, unit, None, s.REGISTERED, r["registered"], b["registered"], page_ids)
        values.add(result_set, unit, None, s.VOTERS, r["cast"], b["cast"], page_ids)
        values.add(result_set, unit, None, s.INVALID, r["invalid"], b["invalid"], page_ids)
        later = {_key(c) for c in rounds[n]["candidates"]} if n < len(rounds) else set()
        for position, c in enumerate(r["candidates"], 1):
            option = s._candidate(p, contest, f"{tag}:{position}", position, c["name"], tokens(c), label=c["label"])
            if c["votes"] is not None:
                values.add(result_set, unit, option, s.VOTES, c["votes"], c["box"], page_ids)
            if n == len(rounds):
                kind = "elected" if _key(c) == elected else "lost"
            else:
                kind = "advanced" if _key(c) in later else "eliminated"
            p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
        previous = contest


def _key(candidate: dict[str, Any]) -> str:
    return z.label(candidate["name"])


def _winner(district: dict[str, Any]) -> str:
    """The candidate the report declares elected, or the one with the most votes where it names no one."""
    last = district["rounds"][-1]["candidates"]
    named = district.get("elected")
    if named:
        words = z.label(named)
        best = max(last, key=lambda c: SequenceMatcher(None, z.label(c["printed"]), words).ratio())
        if SequenceMatcher(None, z.label(best["printed"]), words).ratio() >= 0.75:
            return _key(best)
    return _key(max(last, key=lambda c: c["votes"] or 0))
