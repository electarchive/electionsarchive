import json

from ea.changeset import Changeset
from ea.sources import census
from ea.store import connect

VERSION = "2"
STATUSES = ("own", "within", "none", "de_facto", "former")


def rows(spec: dict) -> list[dict]:
    found = spec["countries"]
    codes = {row["iso"] for row in found}
    if len(codes) != len(found):
        raise ValueError("census.world: an ISO code is listed twice")
    for row in found:
        iso, status = row["iso"], row["status"]
        if status not in STATUSES:
            raise ValueError(f"census.world {iso}: status {status!r} is not one of {', '.join(STATUSES)}")
        if (status == "within") != bool(row.get("within")):
            raise ValueError(f"census.world {iso}: a within row, and only a within row, names its parent")
        if status == "within" and row["within"] not in codes:
            raise ValueError(f"census.world {iso}: parent {row['within']} is not listed")
        if (status == "de_facto") != bool(row.get("recognized_within")):
            raise ValueError(
                f"census.world {iso}: a de_facto row, and only a de_facto row, names its recognized parent"
            )
        if status == "de_facto" and row["recognized_within"] not in codes:
            raise ValueError(f"census.world {iso}: recognized parent {row['recognized_within']} is not listed")
        if status != "own" and not (row.get("reason") or "").strip():
            raise ValueError(f"census.world {iso}: a {status} row gives its reason")
        if row["source"] not in spec["sources"]:
            raise ValueError(f"census.world {iso}: no source {row['source']}")
        electorate = row.get("electorate")
        if electorate is not None and not {"value", "date", "source"} <= electorate.keys():
            raise ValueError(f"census.world {iso}: an electorate gives its value, date, and source")
    return found


def emit(spec: dict | None = None) -> str:
    spec = spec or json.loads((census.FOLDER / "world.json").read_text())
    found = rows(spec)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline="census.world",
            version=VERSION,
            summary="World tier of the election census: one row for each ISO 3166-1 code, Kosovo, and each de facto or "
            "former state, saying whether it holds elections of its own, holds them within another polity, holds none, "
            "or is a de facto state recognized within another polity, each cited to an official or parliamentary source",
            polity=None,
        )
        sources = {}
        for tag, source in spec["sources"].items():
            id = cs.source(source["publisher"], source["title"])
            if id in cs.minted and id not in sources.values():
                cs.add(
                    "source",
                    id=id,
                    kind=source["kind"],
                    publisher=source["publisher"],
                    title=source["title"],
                    url=source["url"],
                    notes=source.get("note"),
                    license=source.get("license"),
                )
            sources[tag] = id
        for row in found:
            electorate = row.get("electorate") or {}
            cs.add(
                "census_world",
                iso_code=row["iso"],
                name_en=row["name_en"],
                status=row["status"],
                within_iso=row.get("within"),
                recognized_within=row.get("recognized_within"),
                reason=row.get("reason"),
                electorate=electorate.get("value"),
                electorate_date=electorate.get("date"),
                electorate_precision=electorate.get("precision", "day") if electorate else None,
                electorate_source_id=sources[electorate["source"]] if electorate else None,
                electorate_locator=(electorate.get("locator") or spec["sources"][electorate["source"]]["url"])
                if electorate
                else None,
                source_id=sources[row["source"]],
                locator=row.get("locator") or spec["sources"][row["source"]]["url"],
            )
        cs.manifest["scope"] = {"polity": None, "sweeps": ["census_world"]}
        return str(cs.write())
