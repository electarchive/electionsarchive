import hashlib
from pathlib import Path
from uuid import UUID

import httpx
from psycopg import Connection

from ea.sources.us.as_precincts import AS_OF_2026, DAY_2026, DELEGATE_2026, KEYWORDS_2026, read

READ = "db9527f65fba49db84684bf6e224a87fbd93b44b14ca943000ec1e3e3c3d152e"
CONTEST = "2026-11-03:AS:house:0"


def collect(client: httpx.Client) -> dict[str, tuple[bytes, str]]:
    response = client.get(DELEGATE_2026)
    response.raise_for_status()
    return {DELEGATE_2026: (response.content, ".pdf")}


def plan(
    conn: Connection, paths: dict[str, Path]
) -> tuple[list[tuple[str, str, UUID, dict[tuple[UUID, UUID], int], tuple[int, int], str | None]], list[str]]:
    path = paths[DELEGATE_2026]
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if sha != READ:
        raise ValueError(f"American Samoa's delegate results file changed ({sha}); check it and update us.as_precincts")
    unit = conn.execute(
        """select u.id from reporting_unit u join polity p on p.id = u.polity_id
           where p.iso_code = 'US-AS' and u.scheme_code = 'us.state'"""
    ).fetchall()[0][0]
    options = conn.execute(
        """select o.contest_id, o.id, lower(o.ballot_label) from external_id e
           join contest_option o on o.contest_id = e.entity_id
           where e.scheme = 'us.contest' and e.value = %s""",
        [CONTEST],
    ).fetchall()
    districts = read(path, DAY_2026, KEYWORDS_2026)
    sums: dict[tuple[UUID, UUID], int] = {}
    unmatched = []
    for keyword in KEYWORDS_2026:
        hits = [option for _, option, label in options if keyword in label]
        if len(hits) != 1:
            unmatched.append(f"{CONTEST}: {len(hits)} options for {keyword}")
            continue
        sums[(unit, hits[0])] = sum(votes[keyword] for votes in districts.values())
    if unmatched:
        return [], unmatched
    reporting = (len(districts), len(districts))
    return [("AS", CONTEST, options[0][0], sums, reporting, AS_OF_2026.isoformat())], unmatched
