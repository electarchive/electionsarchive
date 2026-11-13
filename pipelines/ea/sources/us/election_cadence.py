import csv
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid5

from ea.changeset import NAMESPACE, Changeset
from ea.store import ROOT, connect

SOURCE = "us.election_cadence"
VERSION = "3"
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
COLUMNS = ("selection", "period_years", "phase", "month", "day_rule", "valid_from", "valid_to")


def rows() -> list[dict[str, str]]:
    with TSV.open(newline="") as f:
        return list(csv.DictReader((line for line in f if not line.startswith("#")), delimiter="\t"))


def key(row: dict[str, str]) -> str:
    state, function, pattern = row["state"], row["function"], row["name_pattern"]
    if row["period_years"] and pattern in ("", VILLAGE):
        kind = "village" if pattern else function.capitalize() if function else row["government_kind"]
        return f"{state}:{kind}" + (f":{row['valid_from'][:4]}" if row["valid_from"] else "")
    return f"{state}:special:{function}:{pattern}"


def values(row: dict[str, str]) -> dict[str, object]:
    parse = {
        "period_years": int,
        "phase": int,
        "month": int,
        "valid_from": date.fromisoformat,
        "valid_to": date.fromisoformat,
    }
    return {column: parse.get(column, str)(row[column]) if row[column] else None for column in COLUMNS}


def emit() -> Path:
    with connect() as conn:
        governments = {
            (state, name): polity
            for state, name, polity in conn.execute(
                "select state, name, min(polity_id::text) from coverage_government"
                " where polity_id is not null group by 1, 2 having count(distinct polity_id) = 1"
            ).fetchall()
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Election cadence of US local governments as law states it: the cited state-by-kind calendar rules, the cited special district rules on who chooses a board and when, the cited overrides for single governments the archive links to a polity, and Michigan's 2024 switch to four-year county commissioner terms",
            polity="US",
        )
        cs.manifest["scope"] = {"polity": "US", "sweeps": ["election_cadence"]}
        sources: set[UUID] = set()
        counts: Counter[str] = Counter()

        def source(title: str, url: str) -> UUID:
            host = urlsplit(url).netloc.removeprefix("www.")
            id = cs.source(host, title)
            if id not in sources:
                sources.add(id)
                if id in cs.minted:
                    cs.add(
                        "source",
                        id=id,
                        kind="official_body"
                        if host.endswith((".gov", ".us")) or host in OFFICIAL_HOSTS
                        else "compendium",
                        publisher=host,
                        title=title,
                        url=url,
                        license=None,
                    )
            return id

        for row in rows():
            if not row["locator"] or not row["url"]:
                counts["uncited"] += 1
                continue
            if row["government"]:
                polity = governments.get((row["state"], row["government"]))
                if polity is None:
                    counts["unlinked"] += 1
                    continue
                id, shape = f"polity:{polity}", {"polity_id": UUID(polity)}
            else:
                id, shape = (
                    key(row),
                    {
                        "polity_id": cs.polity(f"US-{row['state']}"),
                        "government_kind": row["government_kind"],
                        "function": row["function"] or None,
                        "name_pattern": row["name_pattern"] or None,
                    },
                )
            cs.add(
                "election_cadence",
                id=uuid5(NAMESPACE, f"election_cadence\x1f{id}"),
                source_id=source(row["locator"], row["url"]),
                locator=row["locator"],
                **shape,
                **values(row),
            )
            counts["rules"] += 1
        print(dict(counts))
        return cs.write()
