import re
from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.fl_runoff_people_merge"
VERSION = "1"

PAIRS = """
select r.id, y.person_id, y.ballot_name, 2, (select count(*) from candidacy z where z.person_id = y.person_id)
  from contest r join contest p on p.id = r.previous_round_id
  join changeset pc on pc.id = p.changeset_id and pc.pipeline = 'us.fl_primary_2026'
  join contest_option o on o.contest_id = r.id join candidacy y on y.option_id = o.id
  where r.round_number > 1
union all
select r.id, y.person_id, y.ballot_name, 1, (select count(*) from candidacy z where z.person_id = y.person_id)
  from contest r join contest p on p.id = r.previous_round_id
  join changeset pc on pc.id = p.changeset_id and pc.pipeline = 'us.fl_primary_2026'
  join contest_option o on o.contest_id = p.id join candidacy y on y.option_id = o.id
  where r.round_number > 1
"""


def _words(name: str) -> list[str]:
    name = re.sub(r"\b(dr|mr|mrs|ms|jr|sr|ii|iii)\b\.?", " ", name.lower())
    name = re.sub(r"[“”\"'].*?[“”\"']", " ", name)
    return re.findall(r"[a-zà-ÿ]+", name)


def _same(a: str, b: str) -> bool:
    x, y = _words(a), _words(b)
    if not x or not y or x[-1] != y[-1]:
        return False
    return x[0].startswith(y[0]) or y[0].startswith(x[0])


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Merge the people a Florida school board runoff and its August 18 first round list under different "
                "ids for the same candidate, where the last name is the same and one first name starts the other"
            ),
            polity="US-FL",
        )
        rounds: dict = {}
        for contest, person, name, rnd, history in conn.execute(PAIRS).fetchall():
            rounds.setdefault(contest, {1: {}, 2: {}})[rnd][person] = (name, history)
        merged = 0
        for contest, sides in sorted(rounds.items()):
            first = {p: v for p, v in sides[1].items() if p not in sides[2]}
            for person, (name, history) in sides[2].items():
                if person in sides[1]:
                    continue
                hits = [p for p, (other, _) in first.items() if _same(name, other)]
                if len(hits) != 1:
                    print(f"{contest}: {name!r} has {len(hits)} candidate matches in round 1")
                    continue
                other = hits[0]
                if cs.survivors.get(person, person) == cs.survivors.get(other, other):
                    continue
                if first[other][1] > history:
                    cs.merge(person, other)
                else:
                    cs.merge(other, person)
                merged += 1
                print(f"{contest}: {first[other][0]!r} / {name!r}")
        print(f"{merged} people merged")
        return cs.write()
