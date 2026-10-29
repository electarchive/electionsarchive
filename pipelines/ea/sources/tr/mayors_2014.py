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

SOURCE = "tr.mayors_2014"
VERSION = "1"
PAGES = Path(__file__).with_name("mayors_2014_pages.tsv")
UNPUBLISHED = Path(__file__).with_name("mayors_2014_unpublished.tsv")
LICENSE = "CC BY-SA 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
ROW = re.compile(r"\|bgcolor[^\n]*\n\|[^\n]*\n\|[^\n]*\n\|([^\n]*)\n\|\s*([\d.]+)\s*\n\|")
REF = re.compile(r"<ref[^>]*?/>|<ref[^>]*>.*?</ref>", re.DOTALL)
WINNERS = """
with rs as (
  select c.id contest_id, c.office_id, c.district_area_id, rs.k rsk
  from contest c join election e on e.id = c.election_id and e.date_from = '2014-03-30'
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
  exists (select from contest c2 join election e2 on e2.id = c2.election_id
          where c2.office_id = v.office_id and e2.date_from > '2014-03-30' and e2.date_from < '2015-01-01'),
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
    for title in titles():
        raw.fetch(SOURCE, url(title), suffix=".txt")


def emit() -> Path:
    tables: dict[tuple[int, ...], list[tuple[str, str, str]]] = defaultdict(list)
    for title in titles():
        text = REF.sub("", raw.cached(SOURCE, url(title)).path.read_text(encoding="utf-8"))
        for part in re.split(r"\n(?==+ ?[^=\n]+ ?=+\s*\n)", text):
            heading = re.match(r"(=+) ?(.+?) ?=+\s*\n", part)
            rows = [
                (
                    re.sub(
                        r"^[^|]*\|",
                        "",
                        re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", re.sub(r"<[^>]*>", "", match.group(1))),
                    ).strip(),
                    int(match.group(2).replace(".", "")),
                )
                for match in ROW.finditer(part)
            ]
            if len(rows) >= 2:
                key = tuple(sorted((votes for _, votes in rows), reverse=True)[:3])
                top = max(rows, key=lambda row: row[1])
                tables[key].append((title, heading.group(2) if heading else "", top[0]))

    with connect() as conn:
        contests: dict[object, list[tuple]] = defaultdict(list)
        for row in conn.execute(WINNERS).fetchall():
            contests[row[0]].append(row)
        parties_present = {row[0] for row in conn.execute("select id from party").fetchall()}

        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Mayors elected on 30 March 2014, from YSK counts confirmed by Turkish Wikipedia's per-province tables",
            polity="TR",
        )
        ysk = cs.source("Yüksek Seçim Kurulu", "2014 local election results")
        wiki = cs.source("Wikipedia", "Turkish Wikipedia: per-province 2014 Türkiye yerel seçimleri articles")
        cs.add(
            "source",
            id=wiki,
            kind="community",
            publisher="Wikipedia",
            title="Turkish Wikipedia: per-province 2014 Türkiye yerel seçimleri articles",
            url="https://tr.wikipedia.org/wiki/2014_Türkiye_yerel_seçimleri",
            license=LICENSE,
            license_url=LICENSE_URL,
        )
        for title in titles():
            cs.artifact(raw.cached(SOURCE, url(title)), wiki)

        skipped: list[tuple[str, str, str]] = []
        published = 0
        for contest, options in contests.items():
            name = options[0][4]
            top, second = options[0], options[1] if len(options) > 1 else None
            votes = [int(option[3]) for option in options]
            if top[7]:
                skipped.append((str(contest), name, "rerun or by-election later in 2014"))
                continue
            if second is None or votes[0] == votes[1]:
                skipped.append((str(contest), name, "tie or single candidate"))
                continue
            hits = tables.get(tuple(votes[:3]), [])
            if len(hits) != 1:
                skipped.append((str(contest), name, "no second source" if not hits else "ambiguous second source"))
                continue
            title, heading, winner = hits[0]
            if SequenceMatcher(None, fold(winner), fold(top[8] or "")).ratio() < 0.85:
                skipped.append((str(contest), name, f"disagreement: {title} lists {winner} first"))
                continue
            tenure = cs.key(f"{SOURCE}.tenure", str(contest))
            cs.add(
                "tenure",
                id=tenure,
                person_id=top[5],
                office_id=top[1],
                from_date=date(2014, 4, 1),
                from_precision="month",
                to_date=date(2019, 4, 1),
                to_precision="month",
                acquired_via="elected",
                contest_id=contest,
                ended_via="term_end",
            )
            if top[6] in parties_present:
                cs.add(
                    "tenure_affiliation",
                    tenure_id=tenure,
                    kind="party",
                    party_id=top[6],
                    valid_from=date(2014, 4, 1),
                    valid_from_precision="month",
                    valid_to=date(2019, 4, 1),
                    valid_to_precision="month",
                )
            cs.add("citation", entity_id=tenure, source_id=ysk, locator={"contest": str(contest)})
            cs.add("citation", entity_id=tenure, source_id=wiki, locator={"page": title, "section": heading})
            published += 1

        with UNPUBLISHED.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
            writer.writerow(("contest_id", "contest", "reason"))
            writer.writerows(sorted(skipped, key=lambda row: (row[2], row[1])))
        print(f"{published} tenures; {len(skipped)} unpublished")
        return cs.write()
