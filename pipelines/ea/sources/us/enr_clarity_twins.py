import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from uuid import UUID

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.enr_clarity_twins"
VERSION = "2"
LIVE = """not exists (select from entity_retirement r where r.entity_id = {0}.id)
          and not exists (select from entity_merge m where m.entity_id = {0}.id)"""


def _person(name: str) -> str | None:
    if re.search(r"\bwrite|^(yes|no|for|against)\b", name, re.IGNORECASE):
        return None
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    words = [
        word
        for word in re.findall(r"[a-z]+", re.sub(r"\(.*?\)|\"[^\"]*\"", " ", plain))
        if word not in ("jr", "sr", "ii", "iii", "iv", "dr", "mr", "mrs", "ms")
    ]
    return f"{words[0]} {words[-1]}" if words else None


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Retire contests that us.enr_clarity wrote a second time under its own office or body key while another pipeline already holds them",
            polity="US",
            kind="correction",
        )
        rows = conn.execute(
            f"""with mine as (
                  select x.id, x.round_number, x.party_scope_id, e.polity_id, e.date_from from contest x
                  join election e on e.id = x.election_id join changeset g on g.id = x.changeset_id
                  where g.pipeline = 'us.enr_clarity' and x.kind <> 'ballot_question' and {LIVE.format("x")}
                    and exists (select from result_set r where r.contest_id = x.id)
                    and not exists (select from result_set r join changeset h on h.id = r.changeset_id
                                    where r.contest_id = x.id and h.pipeline <> 'us.enr_clarity')
                )
                select m.id, o.id,
                  (select text from name n where n.entity_id = m.id and n.is_preferred and n.lang = 'en' limit 1),
                  (select text from name n where n.entity_id = o.id and n.is_preferred and n.lang = 'en' limit 1),
                  (select string_agg(distinct h.pipeline, ', ') from result_set r join changeset h on h.id = r.changeset_id
                   where r.contest_id = o.id),
                  (select array_agg(y.ballot_name) from contest_option p join candidacy y on y.option_id = p.id
                   where p.contest_id = m.id and y.ballot_name is not null),
                  (select array_agg(y.ballot_name) from contest_option p join candidacy y on y.option_id = p.id
                   where p.contest_id = o.id and y.ballot_name is not null)
                from mine m
                join election e on e.polity_id = m.polity_id and e.date_from = m.date_from
                join contest o on o.election_id = e.id and o.id <> m.id and o.kind <> 'ballot_question'
                  and o.round_number is not distinct from m.round_number
                  and o.party_scope_id is not distinct from m.party_scope_id and {LIVE.format("o")}
                where exists (select from result_set r where r.contest_id = o.id)
                  and not exists (select from result_set r join changeset h on h.id = r.changeset_id
                                  where r.contest_id = o.id and h.pipeline = 'us.enr_clarity')"""
        ).fetchall()
        twins: dict[UUID, list[tuple[str, str, str]]] = defaultdict(list)
        for contest, _, title, other, pipelines, ours, theirs in rows:
            mine = {person for person in map(_person, ours or []) if person}
            held = {person for person in map(_person, theirs or []) if person}
            shared = len(mine & held)
            if mine and shared * 2 > len(mine) and shared * 2 > len(held) and (len(mine) > 1 or len(held) == 1):
                twins[contest].append((title, other, pipelines))
        for contest, found in sorted(twins.items()):
            title, other, pipelines = found[0]
            cs.add(
                "entity_retirement",
                entity_id=contest,
                reason=f"{title} was written twice: {pipelines} already holds it as {other}, "
                "on the same day, in the same polity, with the same candidates",
            )
        print(f"{len(twins)} contests")
        return cs.write()
