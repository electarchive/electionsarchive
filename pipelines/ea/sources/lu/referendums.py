"""Luxembourg's national referendums, by commune.

The referendum of 10 July 2005 on the Treaty establishing a Constitution for Europe is read from the government's
referendum site, which lists every commune's yes and no votes and the national totals, and checked against the
national results in the STATEC and University of Luxembourg report on the referendum. The site prints no ballot
counts by commune (registered voters, blank or invalid ballots), so the commune rows hold the answers alone. The
national turnout and blank ballots appear only in the report and are recorded in the package's methodology note.
"""

import re
from datetime import date
from html import unescape
from typing import NamedTuple

from ea import package, raw
from ea.package import Package
from ea.sources.lu import elections
from ea.sources.lu.model import slug

SOURCE = "lu.referendums"
SITE = "https://www.verfassung-fir-europa.lu/fr/referendum/resultat/index.html"
REPORT = (
    "https://statistiques.public.lu/dam-assets/fr/publications/thematique/conditions-sociales/"
    "referendum-traite-2005/referendum-traite-2005.pdf"
)
DOSSIER = "https://wdocs-pub.chd.lu/docs/compilation/docpa/pdf/5443_Dossier_Complet.pdf"
DAY = date(2005, 7, 10)
QUESTION = (
    "Etes-vous en faveur du Traité établissant une Constitution pour l'Europe, signé à Rome, le 29 octobre 2004 ?"
)
# The report's national table (table 2.1): registered voters, ballots in the box, blank or invalid, valid, yes, no.
NATIONAL = {"registered": 220717, "cast": 199609, "blank_or_invalid": 5894, "valid": 193715, "yes": 109494, "no": 84221}
# The site spells one commune differently from the cadastre's file for the period; `elections.SPELLINGS` maps it.
SOURCE_ID = elections.key("source", SOURCE)
REPORT_ID = elections.key("source", f"{SOURCE}:report")
METHOD = (
    "Counted per commune and published by the Luxembourg government on its referendum site. The site prints each "
    "commune's yes and no votes and the national totals, and no other counts by commune: the authority reports no "
    "registered voters, blank, or invalid ballots below the national level. The national figures are 220,717 "
    "registered voters, 199,609 ballots in the box (90.44% of the registered), 5,894 blank or invalid ballots, and "
    "193,715 valid ballots, as the STATEC and University of Luxembourg report prints them. The vote was compulsory."
)


class Row(NamedTuple):
    commune: str
    yes: int
    no: int


def fetch() -> None:
    for url, suffix in ((SITE, ".html"), (REPORT, ".pdf"), (DOSSIER, ".pdf")):
        raw.fetch(SOURCE, url, suffix=suffix)


def packages() -> list[str]:
    return [f"{SOURCE}/{DAY.isoformat()}"]


def parse() -> None:
    build().write()


def emit() -> None:
    package.run("load", packages())


def read() -> tuple[list[Row], Row]:
    """Every commune's votes, and the national total row, from the site's results table."""
    text = raw.cached(SOURCE, SITE).path.read_text(encoding="utf-8", errors="replace")
    found: list[Row] = []
    total: Row | None = None
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, flags=re.DOTALL):
        cells = [
            unescape(re.sub(r"<[^>]+>", "", cell)).strip()
            for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.DOTALL)
        ]
        if len(cells) < 6 or not cells[0].endswith("%"):
            continue
        item = Row(cells[1], int(cells[3]), int(cells[5]))
        if cells[1].startswith("Total au niveau national"):
            total = item
        else:
            found.append(item)
    if total is None:
        raise ValueError("the results table has no national total")
    if len(found) != 118 or len({slug(r.commune) for r in found}) != 118:
        raise ValueError(f"expected 118 communes, found {len(found)}")
    if (sum(r.yes for r in found), sum(r.no for r in found)) != (total.yes, total.no):
        raise ValueError("the communes do not add up to the printed national total")
    if (total.yes, total.no) != (NATIONAL["yes"], NATIONAL["no"]):
        raise ValueError("the site's national total disagrees with the report")
    return found, total


def key(kind: str, *parts: object) -> str:
    return elections.key(kind, SOURCE, *parts)


def build() -> Package:
    day = DAY.isoformat()
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        "Luxembourg's referendum of 10 July 2005 on the Treaty establishing a Constitution for Europe, by commune, "
        "from the government's referendum site",
        depends=[f"{elections.SOURCE}/geo", f"{elections.SOURCE}/registry"],
    )
    p.external |= {elections.LUXEMBOURG, elections.COUNTRY}
    site, report, dossier = (raw.cached(SOURCE, url) for url in (SITE, REPORT, DOSSIER))
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Gouvernement luxembourgeois, Service information et presse",
        title="Verfassung fir Europa: résultat du référendum du 10 juillet 2005",
        url=SITE,
        license="Public website of the Luxembourg government; no licence stated",
        license_url="https://www.verfassung-fir-europa.lu/fr/support/notice/index.html",
        attribution="Gouvernement luxembourgeois, verfassung-fir-europa.lu",
    )
    p.add(
        "source",
        id=REPORT_ID,
        kind="official_body",
        publisher="STATEC and Université du Luxembourg",
        title="Référendum sur le Traité établissant une Constitution pour l'Europe, 10 juillet 2005: rapport",
        url=REPORT,
        license="Public report of STATEC; no licence stated",
        license_url="https://statistiques.public.lu/fr/mentions-legales.html",
        attribution="STATEC, Université du Luxembourg",
    )
    p.artifact(site, SOURCE_ID)
    p.artifact(report, REPORT_ID)
    p.artifact(dossier, SOURCE_ID)
    communes, total = read()
    english, french, luxembourgish = elections.NAMES["referendum"]
    election = key("election", day)
    p.add(
        "election",
        id=election,
        polity_id=elections.LUXEMBOURG,
        kind="referendum",
        date_from=DAY,
        date_to=DAY,
        status="held",
    )
    p.name(election, f"2005 Luxembourg {english}", "en", "official")
    p.name(election, f"{french} 2005", "fr", "native", False)
    p.name(election, f"{luxembourgish} 2005", "lb", "native", False)
    path = f"lu/{day}-referendum"
    p.add("slug", path=path, entity_id=election, is_current=True)
    contest = key("contest", day, 1)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="ballot_question",
        district_area_id=elections.COUNTRY,
        round_number=1,
        electoral_system_id=elections.SYSTEM_REFERENDUM,
        default_result_kind="counted",
    )
    p.add("ballot_question", contest_id=contest, type="referendum")
    p.name(contest, "2005 Luxembourg referendum on the Treaty establishing a Constitution for Europe", "en", "official")
    p.name(contest, QUESTION, "fr", "native", False)
    p.add("slug", path=f"{path}/question-1", entity_id=contest, is_current=True)
    result_set = key("result_set", day, 1)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    options = {}
    for position, (answer, label) in enumerate((("yes", "Oui"), ("no", "Non")), 1):
        options[answer] = key("option", day, 1, answer)
        p.add(
            "contest_option",
            id=options[answer],
            contest_id=contest,
            kind="answer",
            ballot_position=position,
            ballot_label=label,
        )
        p.name(options[answer], answer.title(), "en", "english")
    values = elections.Facts()
    for row in communes:
        text = elections.commune_name(row.commune)
        unit = key("unit", f"{day}:{slug(text)}")
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=elections.LUXEMBOURG,
            scheme_code="lu.count",
            code=f"{day}:{slug(text)}",
            area_relation="equals",
            area_id=elections.area("lu.commune", text),
            kind="standard",
        )
        p.name(unit, row.commune, "fr", "official")
        values.add(result_set, unit, options["yes"], elections.VOTES, row.yes)
        values.add(result_set, unit, options["no"], elections.VOTES, row.no)
    p.frame("result_value", values.frame())
    for answer, votes in (("yes", total.yes), ("no", total.no)):
        p.add("declared", result_set_id=result_set, option_id=options[answer], measure=elections.VOTES, value=votes)
    for answer, option in options.items():
        p.add("outcome", result_set_id=result_set, option_id=option, kind="won" if answer == "yes" else "lost")
    print(f"{p.key}: {len(communes)} communes, {total.yes:,} yes, {total.no:,} no", flush=True)
    return p
