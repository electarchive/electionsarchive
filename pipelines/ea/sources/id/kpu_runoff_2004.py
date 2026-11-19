"""Indonesia's 2004 presidential runoff (20 September 2004) nationally, from KPU decree 98/SK/KPU/2004.

The decree declares the two tickets' national votes and the 114,257,054 valid votes. KPU's site prints the decree
as a page and announces it in an article; neither reaches the province tables of the decree's annex, so only the
nation is loaded. The runoff joins the 2004 election and first-round contest that `id.kpu_pilpres` holds.
"""

import os
import re
from datetime import datetime
from pathlib import Path
from uuid import uuid5

from ea import package, raw
from ea.package import Package

SOURCE = "id.kpu_runoff_2004"
KEY = "id.kpu_runoff_2004/pilpres-2004-runoff"
DAY = "2004-09-20"
DECREE = (
    "https://web.archive.org/web/20050212003404/http://www.kpu.go.id/peraturan_uu/KepKPU_98_Thn2004.htm",
    (
        "Keputusan Komisi Pemilihan Umum Nomor 98/SK/KPU/Tahun 2004 tentang Penetapan dan Pengumuman Hasil Rekapitulasi "
        "Penghitungan Suara Pemilihan Umum Presiden dan Wakil Presiden Putaran Kedua Tahun 2004"
    ),
)
ARTICLE = (
    "https://web.archive.org/web/20050411235809/http://www.kpu.go.id:80/penetapanhasil/lihat-dalam.php?ID=8&cat=penetapanhasil",
    "Hasil Pemilu Presiden Putaran II Ditetapkan dengan Keputusan KPU 98/SK/KPU/2004",
)
FETCHED = (
    ("c848629609ea0e450592b703aed843ed831359b8085558db19fc81c7f85f3fbe", DECREE[0]),
    ("4fe0d66f020eedaf94425b2e3acb4b728a8c2d266dff5e60b802e07b94fac657", ARTICLE[0]),
)
COUNTRY = str(uuid5(package.NAMESPACE, "polity\x1fID"))
NATION = "518ec4bb-3c49-597a-9cb0-071beb1a090b"
NATION_UNIT = "a4d0067d-0784-5dc1-938b-7f66fd629a3d"
PRESIDENCY = "97106898-58e9-58b2-afc6-1a8f2c1fef92"
ELECTION = "5d5e68f0-ed8f-5962-8b95-e6afa4dab576"
FIRST_ROUND = "e1879aa4-1422-566f-bf13-fffd4dcadcbb"
MEASURES = {"votes": 1, "valid": 4}
# Ballot position, head and running mate with their persons from the first round, ballot names, votes, outcome.
TICKETS = (
    (
        2, "Hj. Megawati Soekarnoputri", "26052a89-7895-5e6e-a9c8-f5e3597a0ea3",
        "K.H. Hasyim Muzadi", "ba403ec9-3b58-515e-9a40-abf4f3337a79",
        "Megawati Soekarnoputri and Hasyim Muzadi", 44_990_704, "lost",
    ),
    (
        4, "H. Susilo Bambang Yudhoyono", "cb67db6e-2c7e-5e3e-8e2d-357310dd7335",
        "Drs. H. Muhammad Yusuf Kalla", "270c530f-8be4-50aa-967c-22daa5234085",
        "Susilo Bambang Yudhoyono and Jusuf Kalla", 69_266_350, "elected",
    ),
)  # fmt: skip
VALID = 114_257_054
NOTE = (
    "National ticket votes as declared in KPU decree 98/SK/KPU/Tahun 2004 of 4 October 2004 (diktum KETIGA), based on "
    "the recapitulation record 160/15-BA/X/2004 of the same day. The decree's two ticket figures sum to its 114,257,054 "
    "valid votes, and KPU's announcement of the decree gives the shares 60.62 and 39.38 percent. The decree's annex "
    "with the figures by province is not part of the copies KPU's site kept, so the runoff is loaded nationally "
    "only."
)


def fetch() -> None:
    for _, url in FETCHED:
        raw.fetch(SOURCE, url, suffix=".html")


def seed(folder: str) -> None:
    """Store the two pages from a folder of files the archive fetched, keyed by their digest."""
    for digest, url in FETCHED:
        with open(os.path.join(folder, f"{digest}.html"), "rb") as handle:
            raw.store(
                SOURCE, url, handle.read(), suffix=".html",
                retrieved_at=datetime.fromisoformat("2026-11-19T21:57:24+00:00"), media_type="text/html",
            )  # fmt: skip


def packages() -> list[str]:
    return [KEY]


def parse() -> None:
    pilpres().write()


def emit() -> None:
    package.run("load", packages())


def _figures(path: object) -> set[int]:
    text = Path(str(path)).read_text(encoding="utf-8", errors="replace")
    return {int(m.replace(".", "")) for m in re.findall(r"\d{1,3}(?:\.\d{3})+", text)}


def pilpres() -> Package:
    p = Package(KEY, SOURCE, "The 2004 Indonesian presidential runoff nationally, from KPU decree 98/SK/KPU/2004")
    p.external |= {
        COUNTRY, NATION, NATION_UNIT, PRESIDENCY, ELECTION, FIRST_ROUND,
        *(value for t in TICKETS for value in (t[2], t[4])),
    }  # fmt: skip
    decree = raw.cached(SOURCE, DECREE[0])
    article = raw.cached(SOURCE, ARTICLE[0])
    seen = _figures(decree.path)
    for need in (VALID, *(t[6] for t in TICKETS)):
        if need not in seen:
            raise ValueError(f"the decree page does not print {need}")
    if sum(t[6] for t in TICKETS) != VALID:
        raise ValueError("2004 runoff: ticket votes do not sum to the valid votes")
    if not {t[6] for t in TICKETS} <= _figures(article.path):
        raise ValueError("2004 runoff: the announcement does not state both ticket totals")
    for share, votes in (("60,62", TICKETS[1][6]), ("39,38", TICKETS[0][6])):
        if f"{100 * votes / VALID:.2f}".replace(".", ",") != share:
            raise ValueError(f"2004 runoff: {votes} is not {share} percent")
    sources = {}
    for artifact, (url, title) in ((decree, DECREE), (article, ARTICLE)):
        source = p.id("source", url)
        p.add(
            "source",
            id=source,
            kind="official_body",
            publisher="Komisi Pemilihan Umum",
            title=title,
            url=url,
            license="Public record: official decree and announcement, no license stated",
            license_url="https://www.kpu.go.id",
        )
        p.artifact(artifact, source)
        sources[url] = source
    contest = p.id("contest", DAY, "presiden")
    p.add(
        "contest",
        id=contest,
        election_id=ELECTION,
        kind="office",
        office_id=PRESIDENCY,
        district_area_id=NATION,
        term="full",
        magnitude=1,
        round_number=2,
        previous_round_id=FIRST_ROUND,
        default_result_kind="counted",
    )
    p.name(contest, "2004 Indonesian presidential election, second round", "en", "english")
    p.name(contest, "Pemilihan Umum Presiden dan Wakil Presiden Tahun 2004 Putaran Kedua", "id", "official")
    p.add("slug", path="id/2004-07-05-presidential/president-second-round", entity_id=contest, is_current=True)
    result_set = p.id("result_set", DAY, "presiden")
    p.add(
        "result_set",
        id=result_set,
        election_id=ELECTION,
        contest_id=contest,
        stage="certified",
        kind="counted",
        status="active",
        source_id=sources[DECREE[0]],
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=NOTE, lang="en")

    def value(option: str | None, measure: str, number: int) -> None:
        fact = {
            "result_set_id": result_set,
            "unit_id": NATION_UNIT,
            "option_id": option,
            "channel": 0,
            "measure": MEASURES[measure],
            "count_round": 0,
        }
        p.add("result_value", **fact, flags=0, value=number)
        p.add("value_locator", **fact, page_id=None, source_id=sources[DECREE[0]])
        p.add("declared", result_set_id=result_set, option_id=option, measure=MEASURES[measure], value=number)

    for number, head, head_id, vice, vice_id, label, votes, outcome in TICKETS:
        option = p.id("option", DAY, number)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=number,
            ballot_label=label,
        )
        for role, ballot, person in (("head", head, head_id), ("running_mate", vice, vice_id)):
            p.add(
                "candidacy",
                id=p.id("candidacy", DAY, number, role),
                person_id=person,
                option_id=option,
                role=role,
                ballot_name=ballot,
                is_independent=False,
            )
        p.add("outcome", result_set_id=result_set, option_id=option, kind=outcome)
        value(option, "votes", votes)
    value(None, "valid", VALID)
    return p
