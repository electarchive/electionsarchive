import re
from uuid import UUID

from psycopg import Connection

from ea.changeset import Changeset, slug

FUNCTIONS = (
    (r"fire", "fire-protection"),
    (r"hospital", "hospital"),
    (r"ambulance", "ambulance"),
    (r"drainage", "drainage"),
    (r"road lighting", "lighting"),
    (r"road", "road"),
    (r"cemetery", "cemetery"),
    (r"recreation", "park"),
    (r"water", "water"),
    (r"sales tax", "sales-tax"),
    (r"crime", "improvement"),
    (r"school", "school"),
    (r"college", "college"),
    (r"economic|industrial", "economic-development"),
)


def display_name(label: str) -> str:
    text = re.sub(r"\s*\(.*$|\s+Prop\.? No\.? \d+ of \d+$", "", label)
    text = re.sub(r"\bDist\.", "District", text)
    text = re.sub(r"\bNumber\b", "No.", text)
    text = re.sub(r"\bPrev\.", "Prevention", text)
    text = re.sub(r"\bNeigh\.", "Neighborhood", text)
    text = re.sub(r"\bImp\.", "Improvement", text)
    text = re.sub(r"\bSub\.", "Subdivision", text)
    text = re.sub(r"\bDev\.", "Development", text)
    return re.sub(r"\s+", " ", text).strip()


def special(
    conn: Connection,
    cs: Changeset,
    state: str,
    county: tuple[UUID, UUID, str, str],
    display: str,
    slugs: dict[str, UUID],
    function: str | None = None,
) -> tuple[UUID, UUID, str, str, str] | None:
    county_polity, county_area, county_path, county_code = county
    function = function or next(
        (value for pattern, value in FUNCTIONS if re.search(pattern, display, re.IGNORECASE)), "district"
    )
    code = f"{county_code}:{function}:{slug(display)}"
    path = f"{county_path}/{slug(display)}"
    if path in slugs:
        existing = conn.execute(
            """select t.polity_id, t.area_id, c.code from polity_territory t
               join area_code c on c.area_id = t.area_id where t.polity_id = %s limit 1""",
            [slugs[path]],
        ).fetchone()
        if existing is None:
            return None
        return (existing[0], existing[1], display, path, existing[2])
    polity = cs.key("us.polity", f"special:{code}")
    slugs[path] = polity
    area = cs.area("us.special_district", code)
    if polity in cs.minted:
        cs.add("polity", id=polity, kind="special_district", iso_code=None)
        cs.add("name", entity_id=polity, text=display, lang="en", kind="official", is_preferred=True)
        cs.add("polity_parent", polity_id=polity, parent_id=county_polity)
        cs.add("slug", path=path, entity_id=polity)
        cs.add("area", id=area, scheme_code="us.special_district")
        cs.add("area_code", area_id=area, code=code, valid_from=None)
        cs.add("area_parent", area_id=area, parent_id=county_area)
        cs.add("name", entity_id=area, text=f"{display}, {state}", lang="en", kind="official", is_preferred=True)
        cs.add("polity_territory", polity_id=polity, area_id=area)
        cs.add(
            "reporting_unit",
            id=cs.unit("us.special_district", code),
            polity_id=polity,
            scheme_code="us.special_district",
            code=code,
            area_relation="equals",
            area_id=area,
            kind="standard",
            valid_from=None,
        )
    return (polity, area, display, path, code)
