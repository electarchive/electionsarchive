import hashlib
from pathlib import Path
from uuid import UUID

import httpx
from psycopg import Connection

from ea.changeset import slug
from ea.sources.us.webb_2026 import AS_OF, CONTESTS, DAY, PLACE, PROPOSITION, URL

READ = "84f193b25fbf1bba4562c438a5569341046939275b92fc9c54fa7c19c0465cba"


def collect(client: httpx.Client) -> dict[str, tuple[bytes, str]]:
    response = client.get(URL)
    response.raise_for_status()
    return {URL: (response.content, ".pdf")}


def plan(
    conn: Connection, paths: dict[str, Path]
) -> tuple[list[tuple[str, str, UUID, dict[tuple[UUID, UUID], int], tuple[int, int], str | None]], list[str]]:
    sha = hashlib.sha256(paths[URL].read_bytes()).hexdigest()
    if sha != READ:
        raise ValueError(f"Webb County posted a new results file ({sha}); transcribe it into us.webb_2026")
    unit = conn.execute(
        "select id from reporting_unit where scheme_code = 'us.place' and code = %s", [PLACE]
    ).fetchall()[0][0]
    tallies = []
    unmatched = []
    for name, _, _, rows in CONTESTS:
        key = (
            PROPOSITION
            if name == "proposition-1"
            else f"{DAY}:TX:{PLACE}:{'mayor' if name == 'mayor' else f'council:{name}'}"
        )
        found = conn.execute(
            """select e.value, o.id, o.contest_id from external_id e join contest_option o on o.id = e.entity_id
               where e.scheme = 'us.option' and e.value like %s""",
            [f"{key}:%"],
        ).fetchall()
        options = {value: option for value, option, _ in found}
        sums: dict[tuple[UUID, UUID], int] = {}
        for row in rows:
            option = options.get(f"{key}:{slug(row[0])}")
            if option is None:
                unmatched.append(f"{key}: no option for {row[0]}")
                continue
            sums[(unit, option)] = row[1]
        if sums:
            tallies.append(("TX", key, found[0][2], sums, (0, 0), AS_OF.isoformat()))
    return tallies, unmatched
