import csv
import re
from collections import defaultdict
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "tr.mayors_2024"
VERSION = "1"
PAGES = Path(__file__).with_name("mayors_2024_pages.tsv")
SWITCHES = Path(__file__).with_name("party_members.tsv")
CHANGES = Path(__file__).with_name("mayors_2024_changes.tsv")
CHANGE_PAGES = {
    "changes": "2024 Türkiye yerel seçimlerinden sonra belediye başkanlarının görev değişiklikleri",
    "kayyum": "2024-25 Türkiye kayyum atamaları",
}
UNPUBLISHED = Path(__file__).with_name("mayors_2024_unpublished.tsv")
LICENSE = "CC BY-SA 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
TABLE = re.compile(
    r"\{\{Siyasi parti haritarenk\|([^|}]*)\|((?:\[\[[^\]]*\]\]|[^|}])*)\|\s*([\d.]+)\s*\|[^|}]*\|\s*(\d)"
)
REF = re.compile(r"<ref[^>]*?/>|<ref[^>]*>.*?</ref>", re.DOTALL)
WINNERS = """
with rs as (
  select c.id contest_id, c.office_id, c.district_area_id, rs.k rsk
  from contest c join election e on e.id = c.election_id and e.date_from = '2024-03-31'
  join result_set rs on rs.contest_id = c.id and rs.is_default
  where c.kind = 'office'
), v as (
  select rs.contest_id, rs.office_id, rs.district_area_id, co.id option_id, co.party_id, sum(rv.value) votes
  from rs join result_value rv on rv.result_set_k = rs.rsk and rv.measure = 1
  join contest_option co on co.k = rv.option_k
  group by 1, 2, 3, 4, 5
)
select v.contest_id, v.office_id, v.option_id, v.votes,
  (select text from name where entity_id = v.contest_id limit 1),
  (select ca.person_id from candidacy ca where ca.option_id = v.option_id limit 1),
  coalesce((select ca.member_of_party_id from candidacy ca where ca.option_id = v.option_id limit 1), v.party_id),
  exists (select from contest c2 join election e2 on e2.id = c2.election_id and e2.date_from = '2024-06-02'
          where c2.office_id = v.office_id),
  (select n.text from name n join candidacy ca on ca.person_id = n.entity_id
   where ca.option_id = v.option_id and n.is_preferred limit 1)
from v order by v.contest_id, v.votes desc
"""


def url(title: str) -> str:
    return f"https://tr.wikipedia.org/w/index.php?title={quote(title.replace(' ', '_'))}&action=raw"


def titles() -> list[str]:
    with PAGES.open(encoding="utf-8") as handle:
        return [row["title"] for row in csv.DictReader(handle, delimiter="\t")]


def fold(text: str) -> str:
    text = text.replace("İ", "i").replace("I", "ı").lower()
    return re.sub(r"[^a-z0-9]", "", text.translate(str.maketrans("ığüşöçâîûêô", "igusocaiueo")))


def fetch() -> None:
    for title in CHANGE_PAGES.values():
        raw.fetch(SOURCE, url(title), suffix=".txt")
    for title in titles():
        raw.fetch(SOURCE, url(title), suffix=".txt")


def emit() -> Path:
    tables: dict[tuple[int, ...], list[tuple[str, str, list[tuple[str, int, int]]]]] = defaultdict(list)
    for title in titles():
        text = raw.cached(SOURCE, url(title)).path.read_text(encoding="utf-8")
        for part in re.split(r"\n(?==+ ?[^=\n]+ ?=+\s*\n)", text):
            heading = re.match(r"(=+) ?(.+?) ?=+\s*\n", part)
            part = REF.sub("", part)
            rows = [
                (match.group(2).strip(), int(match.group(3).replace(".", "")), int(match.group(4)))
                for match in TABLE.finditer(part)
            ]
            if len(rows) >= 2:
                key = tuple(sorted((votes for _, votes, _ in rows), reverse=True)[:3])
                tables[key].append((title, heading.group(2) if heading else "", rows))

    with connect() as conn:
        contests: dict[object, list[tuple]] = defaultdict(list)
        for row in conn.execute(WINNERS).fetchall():
            contests[row[0]].append(row)

        parties = dict(conn.execute("select value, entity_id from external_id where scheme = 'tr.party'").fetchall())
        people = {
            f"{scheme}:{value}": person
            for scheme, value, person in conn.execute(
                "select scheme, value, entity_id from external_id where scheme in ('wikidata', 'tr.ysk_local.person')"
            ).fetchall()
        }
        switches: dict[object, list[dict[str, str]]] = defaultdict(list)
        with SWITCHES.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                if row["who"] in people:
                    switches[people[row["who"]]].append(row)

        changes: dict[str, dict[str, str]] = {}
        with CHANGES.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                changes[fold(row["municipality"])] = row

        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Mayors elected on 31 March 2024, from YSK counts confirmed by Turkish Wikipedia's per-province tables",
            polity="TR",
        )
        ysk = cs.source("Yüksek Seçim Kurulu", "2024 local election results")
        wiki = cs.source("Wikipedia", "Turkish Wikipedia: per-province 2024 Türkiye yerel seçimleri articles")
        cs.add(
            "source",
            id=wiki,
            kind="community",
            publisher="Wikipedia",
            title="Turkish Wikipedia: per-province 2024 Türkiye yerel seçimleri articles",
            url="https://tr.wikipedia.org/wiki/2024_Türkiye_yerel_seçimleri",
            license=LICENSE,
            license_url=LICENSE_URL,
        )
        for title in titles():
            cs.artifact(raw.cached(SOURCE, url(title)), wiki)
        index = cs.source("Wikipedia", "Turkish Wikipedia: mayoral changes after the 2024 local elections")
        cs.add(
            "source",
            id=index,
            kind="community",
            publisher="Wikipedia",
            title="Turkish Wikipedia: mayoral changes after the 2024 local elections",
            url=f"https://tr.wikipedia.org/wiki/{quote(CHANGE_PAGES['changes'].replace(' ', '_'))}",
            license=LICENSE,
            license_url=LICENSE_URL,
        )
        for title in CHANGE_PAGES.values():
            cs.artifact(raw.cached(SOURCE, url(title)), index)
        matched: set[str] = set()
        event_sources: dict[str, object] = {}

        skipped: list[tuple[str, str, str]] = []
        published = 0
        for contest, options in contests.items():
            name = options[0][4]
            top, second = options[0], options[1] if len(options) > 1 else None
            votes = [int(option[3]) for option in options]
            if top[7]:
                skipped.append((str(contest), name, "rerun held on 2024-06-02"))
                continue
            if second is None or votes[0] == votes[1]:
                skipped.append((str(contest), name, "tie or single candidate"))
                continue
            change = changes.get(fold(name.removeprefix("Mayor of ")))
            if change is not None and SequenceMatcher(None, fold(top[8] or ""), fold(change["mayor"])).ratio() < 0.85:
                change = None
            hits = tables.get(tuple(votes[:3]), [])
            if len(hits) != 1 and change is None:
                skipped.append((str(contest), name, "no second source" if not hits else "ambiguous second source"))
                continue
            title, heading, candidates = hits[0] if hits else ("", "", [])
            flagged = [candidate for candidate in candidates if candidate[2] == 1]
            if hits and (len(flagged) != 1 or flagged[0][1] != votes[0]):
                skipped.append(
                    (
                        str(contest),
                        name,
                        f"disagreement: {title} flags {flagged[0][0]} with {flagged[0][1]}"
                        if flagged
                        else f"disagreement: {title} flags no winner",
                    )
                )
                continue
            tenure = cs.key(f"{SOURCE}.tenure", str(contest))
            if change is not None:
                matched.add(fold(change["municipality"]))
            cs.add(
                "tenure",
                id=tenure,
                person_id=top[5],
                office_id=top[1],
                from_date=date(2024, 4, 1),
                from_precision="month",
                to_date=date.fromisoformat(change["date"]) if change is not None else None,
                acquired_via="elected",
                contest_id=contest,
                ended_via=None if change is None else "died" if change["kind"] == "died" else "removed",
            )
            start = date(2024, 4, 1)
            spans = [
                (
                    parties[row["party"]],
                    date.fromisoformat(row["valid_from"]) if row["valid_from"] else None,
                    row["from_precision"] or "day",
                    date.fromisoformat(row["valid_to"]) if row["valid_to"] else None,
                    row["to_precision"] or "day",
                )
                for row in sorted(switches.get(top[5], []), key=lambda row: row["valid_from"] or "0")
                if row["party"]
            ]
            if top[6] is not None and (not spans or (spans[0][1] is not None and spans[0][0] != top[6])):
                spans.insert(
                    0, (top[6], start, "month", spans[0][1] if spans else None, spans[0][2] if spans else "day")
                )
            for party, valid_from, from_precision, valid_to, to_precision in spans:
                cs.add(
                    "tenure_affiliation",
                    tenure_id=tenure,
                    kind="party",
                    party_id=party,
                    valid_from=valid_from or start,
                    valid_from_precision=from_precision if valid_from else "month",
                    valid_to=valid_to,
                    valid_to_precision=to_precision,
                )
            cs.add("citation", entity_id=tenure, source_id=ysk, locator={"contest": str(contest)})
            if hits:
                cs.add("citation", entity_id=tenure, source_id=wiki, locator={"page": title, "section": heading})
            published += 1
            if change is None:
                continue
            when = date.fromisoformat(change["date"])
            locator = {"page": CHANGE_PAGES[change["page"]]}
            if change["source_url"] not in event_sources:
                event_sources[change["source_url"]] = cs.source(change["publisher"], change["source_title"])
                cs.add(
                    "source",
                    id=event_sources[change["source_url"]],
                    kind="news_agency",
                    publisher=change["publisher"],
                    title=change["source_title"],
                    url=change["source_url"],
                )
            report = event_sources[change["source_url"]]
            cs.add("citation", entity_id=tenure, source_id=index, locator=locator)
            cs.add("citation", entity_id=tenure, source_id=report, locator={"url": change["source_url"]})
            for kind in {"suspended": ("suspended",), "trustee": ("removed", "trustee_appointed"), "died": ("died",)}[
                change["kind"]
            ]:
                cs.add(
                    "tenure_event",
                    tenure_id=tenure,
                    kind=kind,
                    date=when,
                    actor=None if change["kind"] == "died" else "İçişleri Bakanlığı",
                    source_id=report,
                )
            if change["replacement"]:
                successor = cs.key(f"{SOURCE}.person", f"{contest}:{change['replacement']}")
                cs.add("person", id=successor)
                cs.add(
                    "name",
                    entity_id=successor,
                    text=change["replacement"],
                    lang="tr",
                    kind="official",
                    is_preferred=True,
                )
                replacement = cs.key(f"{SOURCE}.tenure", f"{contest}:{change['replacement']}")
                cs.add(
                    "tenure",
                    id=replacement,
                    person_id=successor,
                    office_id=top[1],
                    from_date=when,
                    acquired_via="trustee" if change["kind"] == "trustee" else "acting",
                    succeeds_tenure_id=tenure,
                )
                cs.add("citation", entity_id=replacement, source_id=index, locator=locator)
                cs.add("citation", entity_id=replacement, source_id=report, locator={"url": change["source_url"]})
                if change["party"]:
                    cs.add(
                        "tenure_affiliation",
                        tenure_id=replacement,
                        kind="party",
                        party_id=parties[change["party"]],
                        valid_from=when,
                    )

        for key in sorted(set(changes) - matched):
            print(f"unmatched change: {changes[key]['municipality']} / {changes[key]['mayor']}")
        with UNPUBLISHED.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
            writer.writerow(("contest_id", "contest", "reason"))
            writer.writerows(sorted(skipped, key=lambda row: (row[2], row[1])))
        print(f"{published} tenures; {len(skipped)} unpublished")
        return cs.write()
