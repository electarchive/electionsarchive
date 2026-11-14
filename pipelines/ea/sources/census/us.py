import csv
import json
from collections import Counter
from urllib.parse import urlsplit
from uuid import UUID, uuid5

import polars as pl

from ea.changeset import NAMESPACE
from ea.sources import census
from ea.store import DATA, ROOT, connect

TSV = ROOT / "pipelines/ea/sources/us/election_cadence.tsv"
VILLAGE = "^VILLAGE OF "
OFFICIAL_HOSTS = frozenset(
    {
        "oscn.net",
        "nmonesource.com",
        "lldc.mainelegislature.org",
        "media.rivcocob.org",
        "harrisvotes.com",
        "elections.mctx.org",
        "galvestonvotes.org",
        "sariverauthority.org",
        "huntregional.org",
        "rpmh.net",
        "parkcitieswater.com",
        "evergreenuwcd.org",
        "hcuwcd1.org",
        "pbuwcd.com",
        "wgcd.net",
        "postoaksavannahgcd.specialdistrict.org",
        "desertwater.specialdistrict.org",
        "avek.specialdistrict.org",
        "pcwa.net",
        "bdvwa.org",
        "zone7water.com",
        "yourscvwater.com",
        "garlandcounty.org",
        "wellscounty.org",
        "accgov.com",
        "gwinnettcounty.com",
        "northbergen.org",
        "ucnj.com",
        "academyisd.net",
        "cfisd.net",
        "galenaparkisd.com",
        "hawkinsisd.org",
        "katyisd.org",
        "killeenisd.org",
        "cityoftyler.org",
        "sjgov.org",
        "ndwa.specialdistrict.org",
        "ccapweb.pacounties.org",
    }
)
LOCAL = {
    "county": "county",
    "municipality": "municipal",
    "township": "township",
    "school": "school",
    "college": "school",
    "special": "special_district",
}
STATES_SQL = """
select substr(p.iso_code, 4), n.text from polity p join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
where p.iso_code like 'US-%' and p.kind in ('federal_unit', 'region')
"""
OFFICES_SQL = """
select substr(p.iso_code, 4), o.id::text from office o join polity p on p.id = o.polity_id
where p.iso_code like 'US-%' and p.kind in ('federal_unit', 'region') and o.kind = 'governor'
"""
BODIES_SQL = """
select substr(p.iso_code, 4), b.kind::text, b.id::text from body b join polity p on p.id = b.polity_id
where p.iso_code like 'US-%' and p.kind in ('federal_unit', 'region')
    and b.kind in ('upper_house', 'lower_house', 'unicameral')
"""
FIPS_SQL = """
select distinct ac.code, substr(p.iso_code, 4)
from polity p
join polity_territory pt on pt.polity_id = p.id
join area a on a.id = pt.area_id and a.scheme_code = 'us.state'
join area_code ac on ac.area_id = a.id
where p.iso_code like 'US-%'
"""
SENATE_SQL = """
select s.label from seat s join external_id e on e.entity_id = s.body_id and e.scheme = 'us.body' and e.value = 'senate'
"""


def rows() -> list[dict[str, str]]:
    with TSV.open(newline="") as f:
        return list(csv.DictReader((line for line in f if not line.startswith("#")), delimiter="\t"))


def key(row: dict[str, str]) -> str:
    state, function, pattern = row["state"], row["function"], row["name_pattern"]
    if row["period_years"] and pattern in ("", VILLAGE):
        kind = "village" if pattern else function.capitalize() if function else row["government_kind"]
        return f"{state}:{kind}" + (f":{row['valid_from'][:4]}" if row["valid_from"] else "")
    return f"{state}:special:{function}:{pattern}"


def one(conn, query: str) -> dict[str, str]:
    found: dict[str, list[str]] = {}
    for st, value in conn.execute(query):
        found.setdefault(st, []).append(value)
    return {st: values[0] for st, values in found.items() if len(values) == 1}


def emit() -> str:
    spec = json.loads((census.FOLDER / "us.json").read_text())
    classes = {entry["key"]: entry for entry in spec["classes"]}
    counts: Counter[str] = Counter()
    with connect() as conn:
        names = dict(conn.execute(STATES_SQL).fetchall())
        by_name = {name: st for st, name in names.items()}
        governors = one(conn, OFFICES_SQL)
        bodies = {(st, kind): id for st, kind, id in conn.execute(BODIES_SQL)}
        seats = [label for (label,) in conn.execute(SENATE_SQL)]
        abbreviations = dict(conn.execute(FIPS_SQL).fetchall())
        governments: dict[tuple[str, str], tuple[str, str]] = {
            (state, name): (polity, kind)
            for state, name, polity, kind in conn.execute(
                "select state, name, min(polity_id::text), min(kind) from coverage_government"
                " where polity_id is not null group by 1, 2 having count(distinct polity_id) = 1"
            ).fetchall()
        }

    def state_rows(entry: dict, field: str, source: str, locate, target) -> None:
        laws = entry.pop("laws", {})
        for group in entry.pop(field):
            for st in group["states"].split():
                rule = {k: v for k, v in group.items() if k != "states"}
                if "locator" not in rule:
                    rule.update(laws.get(st, {}))
                entry["cadence"].append(
                    {
                        "within": f"US-{st}",
                        "government_kind": None,
                        "function": None,
                        "month": 11,
                        "day_rule": "nov_tuesday_after_first_monday",
                        "source": source,
                        **target(st),
                        **rule,
                        "locator": rule.get("locator") or locate(names[st]),
                    }
                )

    for entry in spec["classes"]:
        entry.setdefault("measure", {"unit": "polity"})
        entry.setdefault("cadence", [])
        entry.setdefault("dates", [])

    president = classes["president"]
    for group in president.pop("appointed"):
        for st in group["states"].split():
            president["cadence"].append(
                {
                    "within": f"US-{st}",
                    "government_kind": None,
                    "function": None,
                    "office": "us.office:president",
                    "selection": "appointed",
                    **{k: v for k, v in group.items() if k != "states"},
                }
            )

    senate = classes["us_senate"]
    rule = senate.pop("senate")
    for label in sorted(seats):
        name, _, numeral = label.rpartition(" Class ")
        st = by_name[name]
        senate["cadence"].append(
            {
                "within": f"US-{st}",
                "government_kind": "national",
                "function": "us_senate",
                "name_pattern": f" Class {numeral}$",
                "body": "us.body:senate",
                "period_years": 6,
                "phase": rule["phases"][numeral],
                "month": 11,
                "day_rule": "nov_tuesday_after_first_monday",
                "term_years": 6,
                "valid_from": rule["admitted"].get(st, rule["valid_from"]),
                "source": rule["source"],
                "locator": rule["locator"],
            }
        )

    state_rows(
        classes["governor"],
        "governors",
        "bots-governors",
        lambda name: (
            f"Table 4.1, {name} row (length of regular term), with Table 6.1 (the years the governor is elected)"
        ),
        lambda st: {"office": governors[st]},
    )
    state_rows(
        classes["judicial"],
        "judges",
        "bots-courts",
        lambda name: f"Table 5.6, {name} Supreme Court row: justices chosen or retained by popular election",
        lambda st: {"government_kind": "state", "function": "judicial", "month": None, "day_rule": None},
    )
    state_rows(
        classes["statewide_exec"],
        "executives",
        "bots-executives",
        lambda name: f"Table 6.1, {name} row",
        lambda st: {"government_kind": "state", "function": "statewide_exec"},
    )
    for level, kinds in (("state_upper", ("upper_house", "unicameral")), ("state_lower", ("lower_house",))):
        state_rows(
            classes[level],
            "chambers",
            "bots-legislators",
            lambda name: f"Table 6.2, {name} row: members elected in odd-numbered years",
            lambda st, kinds=kinds: {"body": next(bodies[(st, kind)] for kind in kinds if (st, kind) in bodies)},
        )

    for row in rows():
        url = row["url"]
        host = urlsplit(url).netloc.removeprefix("www.")
        tag = f"tsv:{host}:{row['locator']}"
        spec["sources"].setdefault(
            tag,
            {
                "publisher": host,
                "title": row["locator"],
                "url": url,
                "kind": "official_body" if host.endswith((".gov", ".us")) or host in OFFICIAL_HOSTS else "compendium",
                "license": None,
            },
        )
        fields = {
            column: (int(row[column]) if column in ("period_years", "phase", "month") else row[column])
            for column in ("selection", "period_years", "phase", "month", "day_rule", "valid_from", "valid_to")
            if row[column]
        }
        if row["government"]:
            linked = governments.get((row["state"], row["government"]))
            if linked is None:
                counts["unlinked"] += 1
                continue
            polity, kind = linked
            shape = {"polity_id": UUID(polity), "government_kind": None, "function": None}
            id = uuid5(NAMESPACE, f"election_cadence\x1fpolity:{polity}")
        else:
            kind = row["government_kind"]
            shape = {
                "within": f"US-{row['state']}",
                "government_kind": kind,
                "function": row["function"] or None,
                "name_pattern": row["name_pattern"] or None,
            }
            id = uuid5(NAMESPACE, f"election_cadence\x1f{key(row)}")
        if kind not in LOCAL:
            raise ValueError(f"no local class for {row['state']} {row['government'] or kind}")
        classes[LOCAL[kind]]["cadence"].append({"id": id, **shape, **fields, "source": tag, "locator": row["locator"]})
        counts["rules"] += 1

    all_levels = ("us_house", "us_senate", "governor", "statewide_exec", "state_upper", "state_lower")
    for group in spec.pop("runoffs"):
        for st in group["states"].split():
            for level in group.get("levels", all_levels):
                classes[level]["cadence"].append(
                    {
                        "id": uuid5(
                            NAMESPACE, f"election_cadence\x1frunoff:{st}:{group['stage']}:{level}:{group['first']}"
                        ),
                        "within": f"US-{st}",
                        "name_pattern": f"^{group['stage']}$",
                        "runoff": "majority" if group["share"] == 0.5 else "threshold",
                        "runoff_threshold": group["share"],
                        "runoff_rule": f"{group['stage']}: {group['authority'][st]}",
                        "valid_from": f"{group['first']}-01-01",
                        "valid_to": f"{group['end'] + 1}-01-01" if group["end"] else None,
                        "source": "state-codes",
                        "locator": group["authority"][st],
                    }
                )
                counts["runoffs"] += 1
    for cycle in spec.pop("cycles"):
        classes[cycle["class"]]["cadence"].append(
            {
                "id": uuid5(NAMESPACE, f"election_cadence\x1fcycle:{cycle['class']}"),
                "period_years": cycle["period_years"],
                "source": cycle["source"],
                "locator": cycle["locator"],
            }
        )

    cog = pl.read_parquet(DATA / "parsed" / "us.cog" / "governments.parquet")
    for tier in spec["tiers"]:
        if kind := tier.pop("cog", None):
            frame = cog.filter(pl.col("kind") == kind)
            tier["counts"] = [{"date": "2022-01-01", "precision": "year", "value": frame.height, "source": "cog"}] + [
                {
                    "within": f"US-{abbreviations[fips]}",
                    "date": "2022-01-01",
                    "precision": "year",
                    "value": n,
                    "source": "cog",
                }
                for fips, n in sorted(frame.group_by("state_fips").len().iter_rows())
            ]
    print(dict(counts))
    return census.emit("us", spec)
