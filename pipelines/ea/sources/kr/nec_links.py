"""The cities, counties, and districts of the National Election Commission's presidential reporting units (`kr.nec`).

`kr.nec` places every unit, from a province down to a polling district, within its province's area, and those
provinces have no outlines. `kr.nec_stats` holds the provinces and the cities, counties, and districts of today, with
geoBoundaries outlines, named as the commission names its counting offices. This package adds area memberships to
`kr.nec`'s units and leaves the units' own rows as `kr.nec` wrote them:

- each province unit is a member of `kr.nec_stats`'s province of its code;
- from the 1995 local elections on, each city, county, or district unit, and each polling district under it, is a
  member of `kr.nec_stats`'s city, county, or district, matched by the counting office's name inside its province as
  `kr.nec_stats` matches its own units. Before 1995 a counting office's name can stand for another territory (the
  cities and the counties of one name were merged on January 1, 1995), so those units get none.

The units that carry votes get no province membership: it would take the place of their own province in rollups,
because the two are areas of one scheme, and with it the commission's national area that the contests are counted
for. Through its city, county, or district a unit also reaches the country.

A counting office the commission splits by National Assembly constituency (동래구갑, 동래구을) is a member of its
district. The cities and counties merged since 1995 are members of the city that took them in, until the day of the
merger, and together they cover it; 연기군, which became Sejong on July 1, 2012, is a member of Sejong. A county that
became a city with its territory kept (당진군 → 당진시) is a member of the city, and 군위군, which moved from North
Gyeongsang to Daegu on July 1, 2023, with its territory kept, is a member of 군위군 throughout. 공주시 and 청원군
before July 1, 2012, and 논산시 before September 19, 2003, held a little more land than the area of their name holds
today; their memberships stand for that territory too.
"""

from datetime import date
from functools import cache
from pathlib import Path

import polars as pl

from ea import package
from ea.package import Package
from ea.sources.kr import areas
from ea.store import DATA

SOURCE = "kr.nec_links"
KEY = f"{SOURCE}/units"
UNITS = "kr.nec"
LINK_FROM = date(int(areas.LINK_FROM[:4]), int(areas.LINK_FROM[4:6]), int(areas.LINK_FROM[6:]))
MERGED = {
    ("46", "여천시"): ("46", "여수시", date(1998, 4, 1)),
    ("46", "여천군"): ("46", "여수시", date(1998, 4, 1)),
    ("49", "북제주군"): ("49", "제주시", date(2006, 7, 1)),
    ("49", "남제주군"): ("49", "서귀포시", date(2006, 7, 1)),
    ("48", "마산시"): ("48", "창원시", date(2010, 7, 1)),
    ("48", "진해시"): ("48", "창원시", date(2010, 7, 1)),
    ("44", "연기군"): ("50", "세종특별자치시", date(2012, 7, 1)),
    ("43", "청원군"): ("43", "청주시", date(2014, 7, 1)),
}
"""Cities and counties merged into another since 1995: the province and the city that took them in, and the day."""
CONSTITUENCY = {"갑", "을", "병", "정"}
"""The suffixes of the counting offices a city, county, or district splits by National Assembly constituency."""


def reference() -> Path:
    """The reference partition of South Korea: downloaded to `data/reference/kr/`, or the server's own copy."""
    for folder in (DATA / "reference" / "kr", DATA / "ops" / "reference" / "kr"):
        if (folder / "reporting_unit.parquet").exists():
            return folder
    raise FileNotFoundError("the reference partition of kr is in neither data/reference/kr nor data/ops/reference/kr")


@cache
def places() -> dict[tuple[str, str], str]:
    """`kr.nec_stats`'s province and city, county, and district areas by scheme and code."""
    folder = reference()
    found = (
        pl.read_parquet(folder / "area.parquet", columns=["id", "scheme_code", "pipeline"])
        .filter(
            pl.col("pipeline") == areas.SOURCE,
            pl.col("scheme_code").is_in([areas.SIDO_SCHEME, areas.SIGUNGU_SCHEME]),
        )
        .join(
            pl.read_parquet(folder / "area_code.parquet", columns=["area_id", "code"]), left_on="id", right_on="area_id"
        )
    )
    out = {}
    for id, scheme, code in found.select("id", "scheme_code", "code").iter_rows():
        if id != areas.area(scheme, code):
            raise ValueError(f"{scheme} {code}: area {id} is not the id kr.nec_stats mints")
        out[scheme, code] = id
    return out


def units() -> pl.DataFrame:
    return pl.read_parquet(
        reference() / "reporting_unit.parquet", columns=["id", "scheme_code", "code", "parent_id", "pipeline"]
    ).filter(pl.col("pipeline") == UNITS)


def office(town: str) -> str:
    """The city, county, or district of a counting office the commission splits by constituency (동래구갑 → 동래구)."""
    return town[:-1] if town[-1:] in CONSTITUENCY and town[-2:-1] in {"시", "군", "구"} else town


def sigungu(day: date, code: str, town: str) -> str | None:
    """The `kr.nec_stats` city, county, or district code of a counting office on a day, or None."""
    if day < LINK_FROM:
        return None
    town = office(town)
    town = areas.RENAMED.get((code, town), town)
    code = areas.MOVED.get((code, town), code)
    whole = areas.city(town)
    if (code, whole) in MERGED and day < MERGED[code, whole][2]:
        code, whole, _ = MERGED[code, whole]
    names = places()
    if (areas.SIGUNGU_SCHEME, f"{code}:{whole}") in names:
        return f"{code}:{whole}"
    promoted = f"{whole[:-1]}시" if whole.endswith("군") else None
    if promoted and (areas.SIGUNGU_SCHEME, f"{code}:{promoted}") in names:
        return f"{code}:{promoted}"
    return None


def merged_on(code: str, town: str) -> date | None:
    """The day a counting office's city or county was merged into another, if it was."""
    found = MERGED.get((code, areas.city(office(town))))
    return found[2] if found else None


def packages() -> list[str]:
    return [KEY]


def parse() -> None:
    build().write()


def emit() -> None:
    package.run("load", packages())


def build() -> Package:
    p = Package(
        KEY,
        SOURCE,
        "The provinces, and from 1995 the cities, counties, and districts, of the National Election Commission's "
        "presidential reporting units",
    )
    names = places()
    found = units()
    schemes = set(found["scheme_code"])
    if schemes != {"kr.sido", "kr.sigungu", "kr.tupyogu"}:
        raise ValueError(f"kr.nec writes units of {sorted(schemes)}")
    rows: list[dict[str, object]] = []
    missing: dict[tuple[str, str], int] = {}
    days: dict[str, set[date]] = {}
    for unit, code in found.filter(pl.col("scheme_code") == "kr.sido").select("id", "code").iter_rows():
        rows.append({"unit_id": unit, "area_id": names[areas.SIDO_SCHEME, code]})
    for unit, code, parent in (
        found.filter(pl.col("scheme_code") == "kr.tupyogu").select("id", "code", "parent_id").iter_rows()
    ):
        day, province, town = code.split("/")[:3]
        held = date(int(day[:4]), int(day[4:6]), int(day[6:]))
        days.setdefault(parent, set()).add(held)
        place = sigungu(held, province, town)
        if place is not None:
            rows.append({"unit_id": unit, "area_id": names[areas.SIGUNGU_SCHEME, place]})
        elif held >= LINK_FROM:
            missing[province, town] = missing.get((province, town), 0) + 1
    if missing:
        raise ValueError(f"counting offices without an area: {sorted(missing.items())}")
    # A city, county, or district unit is undated and stands for its counting office on every day it reports; its
    # membership runs from the first day names are matched until the day its city or county was merged away, if it was.
    for unit, code in found.filter(pl.col("scheme_code") == "kr.sigungu").select("id", "code").iter_rows():
        province, town = code.split("/", 1)
        targets = {sigungu(day, province, town) for day in days.get(unit, set()) if day >= LINK_FROM}
        if not targets:
            continue
        if len(targets) != 1 or None in targets:
            raise ValueError(f"{code}: counting office matches {targets} over its days")
        place = targets.pop()
        assert place is not None
        rows.append(
            {
                "unit_id": unit,
                "area_id": names[areas.SIGUNGU_SCHEME, place],
                "valid_from": LINK_FROM,
                "valid_to": merged_on(province, town),
            }
        )
    frame = pl.DataFrame(
        rows, schema={"unit_id": pl.String, "area_id": pl.String, "valid_from": pl.Date, "valid_to": pl.Date}
    )
    p.frame("reporting_unit_area", frame)
    p.external |= set(frame["unit_id"]) | set(frame["area_id"])
    counts = found.join(frame.select(pl.col("unit_id").alias("id")).unique(), on="id").group_by("scheme_code").len()
    print(f"{frame.height} memberships: {dict(counts.sort('scheme_code').iter_rows())} units of {found.height}")
    return p
