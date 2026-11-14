import json
from uuid import UUID, uuid5

from ea.changeset import NAMESPACE, Changeset
from ea.store import ROOT, connect

VERSION = "1"
FOLDER = ROOT / "pipelines/ea/census"
SELECTORS = ("election_kinds", "contest_kinds", "body_kinds", "office_kinds", "polity_kinds", "schemes", "except_codes")
TABLES = ["census_tier", "census_tier_count", "census_class", "census_match", "census_date"]
CADENCE = (
    "name_pattern",
    "selection",
    "period_years",
    "phase",
    "month",
    "day_rule",
    "classes",
    "term_years",
    "partisan",
    "runoff",
    "runoff_threshold",
    "runoff_rule",
    "administered_by",
    "valid_from",
    "valid_to",
    "called",
    "vacancy_rule",
    "certify_days",
)
CLASS_FIELDS = (
    "name_en",
    "valid_from",
    "valid_to",
    "first_held",
    "earliest_record",
    "contests_per_unit",
    "disposition",
    "disposition_reason",
    "est_votes",
    "effort",
)


def array(values: list[str]) -> str:
    return "{" + ",".join(json.dumps(value) for value in values) + "}"


def emit(cc: str, spec: dict | None = None) -> str:
    spec = spec or json.loads((FOLDER / f"{cc}.json").read_text())
    pipeline = f"census.{cc}"

    def mint(table: str, *parts: object) -> UUID:
        return uuid5(NAMESPACE, "\x1f".join([table, cc, *map(str, parts)]))

    with connect() as conn:
        schemes = {code for (code,) in conn.execute("select code from scheme")}
        rank = dict(conn.execute("select code, rank from scheme").fetchall())
        refs = {
            row[field]
            for entry in spec["classes"]
            for row in [*entry.get("match", []), *entry.get("cadence", [])]
            for field in ("body", "office")
            if isinstance(row.get(field), str)
        }
        pairs = [ref.split(":", 1) for ref in sorted(refs) if ":" in ref]
        entities: dict[str, object] = {
            f"{scheme}:{value}": entity
            for scheme, value, entity in conn.execute(
                "select e.scheme, e.value, e.entity_id from external_id e"
                " join unnest(%s::text[], %s::text[]) r(scheme, value) using (scheme, value)",
                [[p[0] for p in pairs], [p[1] for p in pairs]],
            )
        }
        entities |= {ref: UUID(ref) for ref in refs if ref not in entities and len(ref) == 36}
        if missing := sorted(refs - entities.keys()):
            raise ValueError(f"{pipeline}: no entity for {', '.join(missing)}")
        cs = Changeset(
            conn,
            pipeline=pipeline,
            version=VERSION,
            summary=f"Election census of {spec['name']}: its tiers of government and classes of election, the selectors "
            "that map held contests to each class, and each class's elections listed from official sources with their "
            "contest and seat counts",
            polity=spec["polity"],
        )
        polity = cs.polity(spec["polity"])
        create = spec.get("create")
        if polity in cs.minted and not create:
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
                    license=source.get("license"),
                )
            sources[tag] = id

        if create and polity in cs.minted:
            cs.add("polity", id=polity, kind=create["kind"], iso_code=spec["polity"])
            for name in create["names"]:
                kind = name.get("kind", "official")
                cs.add(
                    "name",
                    entity_id=polity,
                    text=name["text"],
                    lang=name["lang"],
                    kind=kind,
                    is_preferred=kind == "official",
                    source_id=sources[create["source"]],
                )
            cs.add("slug", path=create["slug"], entity_id=polity)
            cs.add("external_id", entity_id=polity, scheme="wikidata", value=create["wikidata"])

        tiers = {tier["key"]: mint("census_tier", tier["key"]) for tier in spec["tiers"]}
        for tier in spec["tiers"]:
            cs.add(
                "census_tier",
                id=tiers[tier["key"]],
                polity_id=polity,
                key=tier["key"],
                name=tier["name"],
                name_en=tier.get("name_en"),
                parent_tier_id=tiers[tier["parent"]] if tier.get("parent") else None,
                within_polity_id=region(tier["within"]) if tier.get("within") else None,
                polity_kind=tier.get("polity_kind"),
                scheme_code=tier["scheme"] if tier.get("scheme") in schemes else None,
                elects=tier.get("elects", True),
                valid_from=tier.get("valid_from"),
                valid_to=tier.get("valid_to"),
                source_id=sources[tier["source"]],
                locator=spec["sources"][tier["source"]]["url"],
            )
            for count in tier.get("counts", []):
                cs.add(
                    "census_tier_count",
                    id=mint("census_tier_count", tier["key"], count.get("within"), count["date"], count["source"]),
                    tier_id=tiers[tier["key"]],
                    within_polity_id=region(count["within"]) if count.get("within") else None,
                    date=count["date"],
                    date_precision=count.get("precision", "day"),
                    value=count["value"],
                    source_id=sources[count["source"]],
                    locator=count.get("locator") or spec["sources"][count["source"]]["url"],
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
                boundary_source_id=sources[entry["boundary_source"]] if entry.get("boundary_source") else None,
                note=entry.get("note"),
                source_id=sources[entry["source"]],
                locator=entry.get("locator") or spec["sources"][entry["source"]]["url"],
                **{field: entry[field] for field in CLASS_FIELDS if field in entry},
            )
            for number, rule in enumerate(entry.get("cadence", [])):
                cs.add(
                    "election_cadence",
                    id=rule.get("id") or mint("election_cadence", key, number),
                    polity_id=rule.get("polity_id") or (region(rule["within"]) if rule.get("within") else polity),
                    class_id=id,
                    government_kind=rule.get("government_kind", entry["tier"]),
                    function=rule.get("function", key),
                    body_id=entities[rule["body"]] if rule.get("body") else None,
                    office_id=entities[rule["office"]] if rule.get("office") else None,
                    source_id=sources[rule["source"]],
                    locator=rule.get("locator") or spec["sources"][rule["source"]]["url"],
                    **{field: rule[field] for field in CADENCE if field in rule},
                )
            for number, row in enumerate(entry.get("access", [])):
                cs.add(
                    "results_access",
                    id=mint("results_access", key, number),
                    polity_id=region(row["within"]) if row.get("within") else polity,
                    class_id=id,
                    role=row.get("role", "publishes"),
                    platform=row["platform"],
                    url_pattern=row["url"],
                    format=row["format"],
                    access=row["access"],
                    first_date=row.get("first_date"),
                    last_date=row.get("last_date"),
                    checked_at=row["checked_at"],
                    evidence=row.get("evidence"),
                    license=row.get("license"),
                    finest_scheme_code=row["finest"] if row.get("finest") in schemes else None,
                    source_id=sources[row["source"]] if row.get("source") else None,
                )
            for number, match in enumerate(entry.get("match", [])):
                cs.add(
                    "census_match",
                    id=mint("census_match", key, number),
                    class_id=id,
                    within_polity_id=(region(match["within"]) if match["within"] else None)
                    if "within" in match
                    else polity,
                    iso_pattern=match.get("iso_pattern"),
                    name_pattern=match.get("name_pattern"),
                    body_id=entities[match["body"]] if match.get("body") else None,
                    office_id=entities[match["office"]] if match.get("office") else None,
                    precedence=match.get("precedence", 0),
                    valid_from=match.get("valid_from"),
                    valid_to=match.get("valid_to"),
                    **{column: array(match[column]) for column in SELECTORS if column in match},
                )
            for row in entry.get("dates", []):
                fields = {
                    "class_id": id,
                    "date": row["date"],
                    "date_precision": row.get("precision", "day"),
                    "election_kind": row.get("election_kind"),
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
        cs.manifest["scope"] = {
            "polity": spec["polity"],
            "sweeps": [t for t in [*TABLES, "election_cadence", "results_access"] if t in cs.rows],
        }
        return str(cs.write())
