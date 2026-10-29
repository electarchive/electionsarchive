import csv
import json
import re
from collections import defaultdict
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.tr.ysk import PUBLISHER, VOTES
from ea.sources.tr.ysk_mayors import SOURCE as MAYORS
from ea.sources.tr.ysk_mayors import page, titles
from ea.store import connect

SOURCE = "tr.mayors_2004_2009"
VERSION = "1"
DAYS = {2004: date(2004, 3, 28), 2009: date(2009, 3, 29)}
UNPUBLISHED = Path(__file__).with_name("mayors_2004_2009_unpublished.tsv")
LICENSE = "CC BY-SA 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
REF = re.compile(r"<ref[^>]*?/>|<ref[^>]*>.*?</ref>", re.DOTALL)
NUMBER = re.compile(r"^\d{1,3}(?:\.\d{3})*$|^\d+$")
OPTIONS = """
select c.id, c.office_id, co.id, co.party_id,
  (select sum(rv.value) from result_set rs
   join result_value rv on rv.result_set_k = rs.k and rv.option_k = co.k and rv.measure = %s
   where rs.contest_id = c.id and rs.is_default),
  ca.person_id,
  (select n.text from name n where n.entity_id = ca.person_id order by n.is_preferred desc limit 1)
from contest c
join election e on e.id = c.election_id and e.date_from = %s
join external_id x on x.entity_id = c.id and x.scheme = 'tr.contest'
join contest_option co on co.contest_id = c.id
left join candidacy ca on ca.option_id = co.id
where c.kind = 'office'
"""


def fold(text: str) -> str:
    text = text.replace("İ", "i").replace("I", "ı").lower()
    return re.sub(r"[^a-z0-9]", "", text.translate(str.maketrans("ığüşöçâîûêô", "igusocaiueo")))


def cell(line: str) -> str:
    text = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", line[1:])
    text = re.sub(r"^\s*(?:style|bgcolor|align|width|rowspan|colspan)[^|]*\|", "", text)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>|''+", "", text)).strip()


def emit() -> Path:
    tables: dict[int, list[tuple[str, str, str, list[tuple[str, str, int]]]]] = defaultdict(list)
    beldes: dict[int, dict[tuple[str, str], list[tuple[str, str, str, str, str, int]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    pages: list[str] = []
    for year in DAYS:
        for title in titles(year):
            if not title.endswith(f"{year} Türkiye yerel seçimleri") or "'" not in title:
                continue
            pages.append(title)
            province = slug(title.split("'")[0])
            text = REF.sub("", json.loads(raw.cached(MAYORS, page(title)).path.read_bytes())["parse"]["wikitext"])
            for part in re.split(r"\n(?==+ ?[^=\n]+ ?=+\s*\n)", text):
                match = re.match(r"(=+) ?(.+?) ?=+\s*\n", part)
                heading = cell("|" + match.group(2)) if match else ""
                rows: list[tuple[str, str, int]] = []
                for chunk in re.split(r"\n\|-", part):
                    cells = [cell(line) for line in chunk.split("\n") if line.startswith("|") and line[:2] != "|}"]
                    if len(cells) >= 5 and cells[0] == "" and NUMBER.match(cells[4]):
                        rows.append((cells[2], cells[3], int(cells[4].replace(".", ""))))
                    elif len(cells) >= 4 and cells[0] and NUMBER.match(cells[3]):
                        district = re.sub(r"\s*beldeleri$", "", heading, flags=re.IGNORECASE)
                        beldes[year][(province, slug(cells[0]))].append(
                            (title, heading, slug(district), cells[1], cells[2], int(cells[3].replace(".", "")))
                        )
                if len(rows) >= 2:
                    tables[year].append((title, heading, province, rows))

    with connect() as conn:
        party_ids: dict[str, set[UUID | None]] = defaultdict(set)
        for entity, text in conn.execute(
            "select n.entity_id, n.text from name n join party p on p.id = n.entity_id"
        ).fetchall():
            party_ids[fold(text)].add(entity)
        party_ids[fold("Bağımsız")].add(None)
        party_ids[fold("Bağımsızlar")].add(None)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Mayors elected in 2004 and 2009, from Turkish Wikipedia's per-province tables checked against YSK's counts (2004) and candidate lists (2009)",
            polity="TR",
        )
        wiki = cs.source("Wikipedia", "Turkish Wikipedia: per-province 2004 and 2009 Türkiye yerel seçimleri articles")
        cs.add(
            "source",
            id=wiki,
            kind="community",
            publisher="Wikipedia",
            title="Turkish Wikipedia: per-province 2004 and 2009 Türkiye yerel seçimleri articles",
            url="https://tr.wikipedia.org/wiki/2009_Türkiye_yerel_seçimleri",
            license=LICENSE,
            license_url=LICENSE_URL,
        )
        for title in pages:
            cs.artifact(raw.cached(MAYORS, page(title)), wiki)
        namesakes: dict[tuple[UUID, str], set[UUID]] = defaultdict(set)
        for office, person, text in conn.execute(
            """select c.office_id, ca.person_id, n.text from candidacy ca
               join contest_option co on co.id = ca.option_id join contest c on c.id = co.contest_id
               join name n on n.entity_id = ca.person_id
               join external_id x on x.entity_id = c.id and x.scheme = 'tr.contest'
               where c.kind = 'office'"""
        ).fetchall():
            namesakes[(office, fold(text))].add(person)

        skipped: list[tuple[str, str, str]] = []
        published = 0
        for year, day in DAYS.items():
            ysk = cs.source(PUBLISHER, f"{year} local election results")
            unique: dict[UUID, dict[UUID, tuple]] = defaultdict(dict)
            for row in conn.execute(OPTIONS, [VOTES, day]).fetchall():
                unique[row[0]][row[2]] = row
            contests = {contest: list(options.values()) for contest, options in unique.items()}
            paths: dict[UUID, list[str]] = defaultdict(list)
            for entity, text in conn.execute(
                "select entity_id, path from slug where entity_id = any(%s) order by path", [list(contests)]
            ).fetchall():
                paths[entity].append(text)
            by_votes: dict[tuple[int, ...], list[tuple[str, str, str, list[tuple[str, str, int]]]]] = defaultdict(list)
            by_name: dict[tuple[str, str], list[int]] = defaultdict(list)
            for index, table in enumerate(tables[year]):
                by_votes[tuple(sorted((votes for _, _, votes in table[3]), reverse=True)[:3])].append(table)
                for _, person_name, _ in table[3]:
                    by_name[(table[2], fold(person_name))].append(index)
            for contest, options in contests.items():
                office = options[0][1]
                every = [text.split("/")[1:-2] for text in paths[contest]]
                parts = every[0]
                label = f"{year} {paths[contest][0]}"
                ranked = sorted((o for o in options if o[4] is not None), key=lambda o: -o[4])
                found: tuple[str, str, str, str, int] | None = None
                if ranked:
                    if len(ranked) > 1 and ranked[0][4] == ranked[1][4]:
                        skipped.append((str(contest), label, "tie"))
                        continue
                    hits = by_votes.get(tuple(int(o[4]) for o in ranked[:3]), []) if len(ranked) > 1 else []
                    if len(hits) == 1:
                        title, heading, _, rows = hits[0]
                        best = max(rows, key=lambda row: row[2])
                        found = (title, heading, best[0], best[1], best[2])
                else:
                    counts: dict[int, int] = defaultdict(int)
                    for option in options:
                        if option[6] is not None:
                            for index in set(by_name.get((parts[0], fold(option[6])), [])):
                                counts[index] += 1
                    best_count = max(counts.values(), default=0)
                    chosen = [index for index, count in counts.items() if count == best_count]
                    if best_count >= min(2, len(options)) and len(chosen) == 1:
                        title, heading, _, rows = tables[year][chosen[0]]
                        ordered = sorted(rows, key=lambda row: -row[2])
                        if ordered[0][2] == ordered[1][2]:
                            skipped.append((str(contest), label, f"tie in {title}"))
                            continue
                        found = (title, heading, ordered[0][0], ordered[0][1], ordered[0][2])
                for place in every if found is None else []:
                    entries = beldes[year].get((place[0], place[-1]), []) if len(place) == 3 else []
                    if len(entries) > 1:
                        entries = [entry for entry in entries if entry[2] == place[1]]
                    if len(entries) == 1:
                        found = entries[0][0], entries[0][1], entries[0][3], entries[0][4], entries[0][5]
                        break
                if found is None:
                    skipped.append((str(contest), label, "no second source"))
                    continue
                title, heading, party_text, person_name, votes = found
                parties = party_ids.get(fold(party_text))
                if parties is None:
                    skipped.append((str(contest), label, f"unknown party: {title} gives {party_text}"))
                    continue
                if ranked:
                    winner = ranked[0]
                    if winner[4] != votes or winner[3] not in parties:
                        skipped.append(
                            (str(contest), label, f"disagreement: {title} gives {person_name} ({party_text}, {votes})")
                        )
                        continue
                    matches = [winner] if winner[5] is not None else []
                else:
                    matches = [
                        option
                        for option in options
                        if option[3] in parties
                        and option[6] is not None
                        and (
                            SequenceMatcher(None, fold(option[6]), fold(person_name)).ratio() >= 0.85
                            or 2 <= len(person_name.split())
                            and {fold(word) for word in person_name.split()}
                            <= {fold(word) for word in option[6].split()}
                        )
                    ]
                    if len(matches) != 1:
                        skipped.append(
                            (str(contest), label, f"disagreement: {title} gives {person_name} ({party_text})")
                        )
                        continue
                if matches:
                    if ranked and SequenceMatcher(None, fold(matches[0][6] or ""), fold(person_name)).ratio() < 0.85:
                        skipped.append(
                            (str(contest), label, f"disagreement: {title} gives {person_name}, YSK {matches[0][6]}")
                        )
                        continue
                    person = matches[0][5]
                    party = matches[0][3]
                else:
                    party = ranked[0][3]
                    reuse = namesakes.get((office, fold(person_name)), set())
                    if len(reuse) == 1:
                        person = next(iter(reuse))
                    else:
                        person = cs.key(f"{SOURCE}.person", f"{day}:{'-'.join(parts)}:{slug(person_name)}")
                        if person in cs.minted:
                            cs.add("person", id=person)
                            cs.add(
                                "name",
                                entity_id=person,
                                text=person_name,
                                lang="tr",
                                kind="official",
                                is_preferred=True,
                                valid_from=None,
                                valid_from_precision="day",
                                valid_to=None,
                                valid_to_precision="day",
                            )
                start, end = date(year, 4, 1), date(year + 5, 4, 1)
                tenure = cs.key(f"{SOURCE}.tenure", str(contest))
                cs.add(
                    "tenure",
                    id=tenure,
                    person_id=person,
                    office_id=office,
                    from_date=start,
                    from_precision="month",
                    to_date=end,
                    to_precision="month",
                    acquired_via="elected",
                    contest_id=contest,
                    ended_via="term_end",
                )
                if party is not None:
                    cs.add(
                        "tenure_affiliation",
                        tenure_id=tenure,
                        kind="party",
                        party_id=party,
                        valid_from=start,
                        valid_from_precision="month",
                        valid_to=end,
                        valid_to_precision="month",
                    )
                cs.add("citation", entity_id=tenure, source_id=ysk, locator={"contest": str(contest)})
                cs.add("citation", entity_id=tenure, source_id=wiki, locator={"page": title, "section": heading})
                published += 1

        with UNPUBLISHED.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
            writer.writerow(("contest_id", "contest", "reason"))
            skipped.sort(key=lambda row: (row[2], row[1]))
            writer.writerows(skipped)
        print(f"{published} tenures; {len(skipped)} unpublished")
        return cs.write()
