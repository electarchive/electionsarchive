import re

from ea import raw
from ea.store import connect

FEEDS = "us.local_2026"


def copy_feed(source: str, url: str) -> None:
    try:
        raw.cached(source, url)
    except LookupError:
        held = raw.cached(FEEDS, url)
        raw.store(
            source,
            url,
            held.path.read_bytes(),
            suffix=".csv",
            retrieved_at=held.retrieved_at,
            media_type="text/csv",
        )


def conservation_districts(state: str) -> dict[str, list[str]]:
    with connect() as conn:
        counties = dict(
            conn.execute(
                """select upper(n.text), c.code from slug s
                   join polity_territory t on t.polity_id = s.entity_id
                   join area_code c on c.area_id = t.area_id
                   join name n on n.entity_id = s.entity_id and n.is_preferred and n.lang = 'en'
                   where s.path ~ %s""",
                [f"^us/{state.lower()}/[a-z-]+-county$"],
            ).fetchall()
        )
        by_fips: dict[str, list[str]] = {}
        for county, name in conn.execute(
            "select county, name from coverage_government where state = %s and kind = 'special' and name ~* 'soil and|soil conservation|soil and water'",
            [state],
        ).fetchall():
            by_fips.setdefault(county, []).append(name)
    return {re.sub(r" COUNTY$", "", label): by_fips.get(fips, []) for label, fips in counties.items()}
