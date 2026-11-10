import csv
import re
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid5

from ea.changeset import NAMESPACE, Changeset
from ea.coverage_tables import CALENDAR, DISTRICT_CALENDAR, HELD_IN, SWITCHED
from ea.store import ROOT, connect

SOURCE = "us.election_cadence"
VERSION = "1"
KINDS = ("county", "municipality", "township", "school", "special")
DAY_RULES = {
    "november": (11, "nov_tuesday_after_first_monday"),
    "other_date": (None, "off_november"),
    "mixed": (None, "mixed"),
    "appointed": (None, None),
    "landowner": (None, None),
}
SWITCH_CITES = {
    ("MI", "county"): (
        "MCL 46.410 as amended by 2021 PA 121: commissioners elected at or after the 2024 general election serve four-year terms",
        "https://www.legislature.mi.gov/Laws/MCL?objectName=mcl-46-410",
    )
}
CITE = re.compile(r"^(.*?)[,;]?\s*(https?://\S+?)[.)]?$", re.DOTALL)


def split(cite: str) -> tuple[str, str | None]:
    match = CITE.match(cite.strip())
    return (match[1].strip(), match[2]) if match else (cite.strip(), None)


def board(row: dict[str, str] | None) -> dict[str, object]:
    if row is None:
        return {}
    month, day_rule = DAY_RULES[row["group"]]
    return {
        "selection": row["group"] if row["group"] in ("appointed", "landowner") else "elected",
        **({"month": month, "day_rule": day_rule} if day_rule is not None else {}),
    }


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
                        kind="official_body" if host.endswith((".gov", ".us")) else "compendium",
                        publisher=host,
                        title=title,
                        url=url,
                        license=None,
                    )
            return id

        def rule(key: str, cite: str | None, url: str | None = None, /, **row: object) -> None:
            locator, link = split(cite or "")
            link = url or link
            if not locator or link is None:
                counts["uncited"] += 1
                return
            cs.add(
                "election_cadence",
                id=uuid5(NAMESPACE, f"election_cadence\x1f{key}"),
                source_id=source(locator, link),
                locator=locator,
                **row,
            )
            counts["rules"] += 1

        with (ROOT / "pipelines/ea/coverage_inputs/special-district-elections.tsv").open(newline="") as f:
            districts = {
                (row["state"], row["function"], row["name_pattern"]): row
                for row in csv.DictReader((line for line in f if not line.startswith("#")), delimiter="\t")
            }

        for (state, kind), spec in sorted(CALENDAR.items()):
            period, phase, cite = spec[:3]
            month = spec[3] if len(spec) == 4 else HELD_IN.get((state, kind))
            switch = SWITCHED.get((state, kind))
            shape = (
                {"government_kind": kind}
                if kind in KINDS
                else {"government_kind": "municipality", "name_pattern": "^VILLAGE OF "}
                if kind == "village"
                else {"government_kind": "special", "function": kind.lower()}
            )
            rule(
                f"{state}:{kind}",
                cite,
                **{
                    "polity_id": cs.polity(f"US-{state}"),
                    "period_years": period,
                    "phase": phase,
                    "month": month,
                    "day_rule": "nov_tuesday_after_first_monday"
                    if month == 11
                    else "with_state_primary"
                    if month == 8
                    else None,
                    "valid_to": None if switch is None else date(switch[0], 1, 1),
                    **shape,
                    **board(districts.pop((state, kind.lower(), ""), None)),
                },
            )
            if switch is not None:
                text, url = SWITCH_CITES[(state, kind)]
                rule(
                    f"{state}:{kind}:{switch[0]}",
                    text,
                    url,
                    polity_id=cs.polity(f"US-{state}"),
                    period_years=switch[1][0],
                    phase=switch[1][1],
                    month=11,
                    day_rule="nov_tuesday_after_first_monday",
                    valid_from=date(switch[0], 1, 1),
                    **shape,
                )

        for (state, function, pattern), row in districts.items():
            rule(
                f"{state}:special:{function}:{pattern}",
                row["statute"],
                row["url"] or None,
                polity_id=cs.polity(f"US-{state}"),
                government_kind="special",
                function=function or None,
                name_pattern=pattern or None,
                **board(row),
            )

        for (state, name), (period, phase, month, cite) in sorted(DISTRICT_CALENDAR.items()):
            polity = governments.get((state, name))
            if polity is None:
                counts["unlinked"] += 1
                continue
            rule(
                f"polity:{polity}",
                cite,
                polity_id=UUID(polity),
                period_years=period,
                phase=phase,
                month=month,
                day_rule="nov_tuesday_after_first_monday" if month == 11 else None,
            )
        print(dict(counts))
        return cs.write()
