import json
from uuid import UUID, uuid5

from ea.changeset import NAMESPACE, Changeset
from ea.store import ROOT, connect

VERSION = "1"
FOLDER = ROOT / "pipelines/ea/census"
SELECTORS = ("election_kinds", "contest_kinds", "body_kinds", "office_kinds", "schemes", "except_codes")
TABLES = ["census_tier", "census_tier_count", "census_class", "census_match", "census_date"]


def array(values: list[str]) -> str:
    return "{" + ",".join(json.dumps(value) for value in values) + "}"


def emit(cc: str) -> str:
    spec = json.loads((FOLDER / f"{cc}.json").read_text())
    pipeline = f"census.{cc}"

    def mint(table: str, *parts: object) -> UUID:
        return uuid5(NAMESPACE, "\x1f".join([table, cc, *map(str, parts)]))

    with connect() as conn:
        schemes = {code for (code,) in conn.execute("select code from scheme")}
        rank = dict(conn.execute("select code, rank from scheme").fetchall())
        cs = Changeset(
            conn,
            pipeline=pipeline,
            version=VERSION,
            summary=f"Election census of {spec['name']}: its tiers of government and classes of election, the selectors "
            "that map held contests to each class, and each class's elections listed from official sources with their "
            "contest and seat counts",
            polity=spec["polity"],
        )
        cs.manifest["scope"] = {"polity": spec["polity"], "sweeps": [t for t in TABLES if t != "census_tier_count"]}
        polity = cs.polity(spec["polity"])
        if polity in cs.minted:
            raise ValueError(f"polity {spec['polity']} is not in the archive")

        def region(iso: str) -> UUID:
            id = cs.polity(iso)
            if id in cs.minted:
                raise ValueError(f"polity {iso} is not in the archive")
            return id

        sources: dict[str, UUID] = {}
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
                )
            sources[tag] = id

        tiers = {tier["key"]: mint("census_tier", tier["key"]) for tier in spec["tiers"]}
        for tier in spec["tiers"]:
            cs.add(
                "census_tier",
                id=tiers[tier["key"]],
                polity_id=polity,
                key=tier["key"],
                name=tier["name"],
                parent_tier_id=tiers[tier["parent"]] if tier.get("parent") else None,
                polity_kind=tier.get("polity_kind"),
                source_id=sources[tier["source"]],
                locator=spec["sources"][tier["source"]]["url"],
            )

        for entry in spec["classes"]:
            key = entry["key"]
            measure = entry["measure"]
            listed = measure.get("fine") or measure.get("grain") or measure.get("schemes") or []
            known = [code for code in listed if code in schemes]
            if unknown := sorted(set(listed) - schemes):
                print(f"{pipeline} {key}: no scheme {', '.join(unknown)}")
            id = mint("census_class", key)
            cs.add(
                "census_class",
                id=id,
                polity_id=polity,
                tier_id=tiers[entry["tier"]],
                key=key,
                name=entry["name"],
                category=entry["category"],
                goal=entry["goal"],
                measure=measure,
                finest_scheme_code=max(known, key=lambda code: rank[code]) if known else None,
                finest_note=entry.get("finest_note"),
                note=entry.get("note"),
                source_id=sources[entry["source"]],
                locator=spec["sources"][entry["source"]]["url"],
            )
            for number, match in enumerate(entry["match"]):
                cs.add(
                    "census_match",
                    id=mint("census_match", key, number),
                    class_id=id,
                    within_polity_id=polity,
                    iso_pattern=match.get("iso_pattern"),
                    valid_from=match.get("valid_from"),
                    valid_to=match.get("valid_to"),
                    **{column: array(match[column]) for column in SELECTORS if column in match},
                )
            for row in entry["dates"]:
                fields = {
                    "class_id": id,
                    "date": row["date"],
                    "date_precision": row.get("precision", "day"),
                    "through": row.get("through"),
                    "contests": row.get("contests"),
                    "seats": row.get("seats"),
                    "regions": row.get("regions"),
                    "deferred": array(row["deferred"]) if row.get("deferred") else None,
                    "note": row.get("note"),
                }
                cited = {}
                if row.get("source"):
                    cited = {
                        "source_id": sources[row["source"]],
                        "locator": spec["sources"][row["source"]]["url"],
                        "quote": row.get("quote"),
                    }
                cs.add(
                    "census_date", id=mint("census_date", key, None, row["date"], row.get("through")), **fields, **cited
                )
                for iso, state in (row.get("states") or {}).items():
                    cs.add(
                        "census_date",
                        id=mint("census_date", key, iso, row["date"]),
                        class_id=id,
                        within_polity_id=region(iso),
                        date=row["date"],
                        date_precision=fields["date_precision"],
                        contests=state["constituencies"],
                        source_id=sources[state["source"]],
                        locator=spec["sources"][state["source"]]["url"],
                        quote=state["quote"],
                    )
        return str(cs.write())
