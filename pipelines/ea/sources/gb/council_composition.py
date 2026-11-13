import sys
from datetime import date
from pathlib import Path
from uuid import UUID, uuid5

import polars as pl

from ea import raw
from ea.changeset import NAMESPACE, Changeset
from ea.sources.gb import council_size, leh
from ea.store import connect

SOURCE = "gb.council_composition"
VERSION = "1"
URL = council_size.OCD_URL
SHADOW = {"E06000067": "ESY", "E06000068": "WSY"}
PARTIES = {
    "con": ("wikidata", "Q9626"),
    "lab": ("wikidata", "Q9630"),
    "ld": ("gb.party", "liberal-democrats"),
    "ukip": ("gb.party", "uk-independence-party"),
    "ref": ("gb.party", "reform-uk"),
    "pc": ("gb.party", "plaid-cymru"),
    "snp": ("gb.party", "scottish-national-party"),
}
GREENS = {
    "E": ("gb.party", "green-party"),
    "W": ("gb.party", "green-party"),
    "S": ("gb.party", "scottish-greens"),
    "N": ("gb.party", "green-party-northern-ireland"),
}
LABELS = {
    "CON": "con",
    "LAB": "lab",
    "LD": "ld",
    "GRN": "green",
    "GREEN": "green",
    "PC": "pc",
    "PLAID": "pc",
    "SNP": "snp",
    "REF": "ref",
    "UKIP": "ukip",
}
CAVEAT = (
    "Party groups and control are from Open Council Data UK's history table (CC BY-SA 4.0), a compiled source, one "
    "snapshot per council per calendar year, not dated to an election or a change of control. Independents, local "
    "parties, and vacancies are one 'other' group, so an administration that includes independents or local parties "
    "marks that whole group."
)


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".csv")


def control(label: str) -> tuple[str | None, list[str]]:
    if label in ("NULL", "Other", "IND"):
        return None, []
    if label == "NOC" or label.endswith(" plurality"):
        return "noc", []
    if label.endswith(" Mayor"):
        return "mayor", [label.removesuffix(" Mayor")]
    if label.endswith(" min"):
        groups = label.removesuffix(" min").split("/")
        return ("minority" if len(groups) == 1 else "coalition"), groups
    groups = label.split("/")
    return ("majority" if len(groups) == 1 else "coalition"), groups


def emit() -> Path:
    ocd = pl.read_csv(raw.cached(SOURCE, URL).path, infer_schema_length=0).rename({"": "gss"})
    current = {
        council: SHADOW.get(gss, gss)
        for council, gss in ocd.filter(pl.col("gss").is_not_null()).select("council id", "gss").iter_rows()
    }

    with connect() as conn:
        bodies = {
            code: (body, name)
            for code, body, name in conn.execute(
                """select e.value, e.entity_id,
                          (select n.text from name n where n.entity_id = e.entity_id order by n.is_preferred desc limit 1)
                   from external_id e join body b on b.id = e.entity_id
                   where e.scheme = 'gb.body' and (e.value ~ '^(E0[6-9]|E10|W06|S12|N09)[0-9]{6}$' or e.value in ('ESY', 'WSY'))"""
            )
        }

        def fold(name: str) -> str:
            name = name.removesuffix(" Council").removesuffix(" County").removesuffix(" CC")
            return leh.fold(name)

        free = {fold(name): code for code, (_, name) in bodies.items() if code not in current.values()}
        codes: dict[str, str] = {}
        unmatched: dict[str, tuple[str, str, str]] = {}
        for council, authority, year in ocd.select("council id", "authority", "year").iter_rows():
            code = current.get(council) or free.get(fold(authority))
            if code is not None and code in bodies:
                codes[council] = code
            else:
                first, last, _ = unmatched.get(council, (year, year, authority))
                unmatched[council] = (min(first, year), max(last, year), authority)

        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Party groups and control of British councils, 2016 to 2026, from Open Council Data UK",
            polity="GB",
        )
        cs.manifest["scope"] = {"polity": "GB", "sweeps": ["body_composition", "body_composition_party"]}
        compiler = cs.source("Open Council Data UK", "Council history 2016-2026")
        cs.add(
            "source",
            id=compiler,
            kind="compendium",
            publisher="Open Council Data UK",
            title="Council history 2016-2026",
            url=council_size.OCD_PAGE,
            license="CC BY-SA 4.0",
            license_url="https://creativecommons.org/licenses/by-sa/4.0/",
            attribution="Council history from Open Council Data UK, CC BY-SA 4.0.",
            notes=council_size.CAVEAT,
        )
        cs.artifact(raw.cached(SOURCE, URL), compiler)

        parties: dict[tuple[str, str], UUID] = {}

        def party(key: tuple[str, str]) -> UUID:
            if key not in parties:
                parties[key] = cs.key(*key)
                if parties[key] in cs.minted:
                    raise SystemExit(f"no party under {key}")
            return parties[key]

        snapshots = 0
        seen: set[str] = set()
        for row in ocd.iter_rows(named=True):
            code = codes.get(row["council id"])
            if code is None:
                continue
            body, _ = bodies[code]
            seen.add(code)
            label = row["majority"]
            kind, groups = control(label)
            composition = uuid5(NAMESPACE, f"{SOURCE}\x1f{code}\x1f{row['year']}")
            cs.add(
                "body_composition",
                id=composition,
                body_id=body,
                as_of=date(int(row["year"]), 1, 1),
                as_of_precision="year",
                seats=int(row["total"]),
                control=kind,
                control_label=None if label == "NULL" else label,
                source_id=compiler,
                locator=f"history2016-26.csv, id {row['id']}",
            )
            roles = {
                LABELS.get(group.upper(), "other"): "head" if index == 0 else "member"
                for index, group in reversed(list(enumerate(groups)))
            }
            for column in ("con", "lab", "ld", "green", "ukip", "ref", "pc", "snp", "other"):
                seats = int(row[column] or 0)
                role = roles.get(column)
                if seats == 0 and role is None:
                    continue
                if column == "other":
                    owner: dict[str, object] = {"party_id": None, "bucket": "other"}
                else:
                    owner = {
                        "party_id": party(GREENS[code[0]] if column == "green" else PARTIES[column]),
                        "bucket": None,
                    }
                cs.add(
                    "body_composition_party",
                    id=uuid5(NAMESPACE, f"{SOURCE}\x1f{code}\x1f{row['year']}\x1f{column}"),
                    composition_id=composition,
                    seats=seats,
                    role=role,
                    **owner,
                )
            snapshots += 1

        print(f"{snapshots} snapshots, {len(seen)} councils", file=sys.stderr)
        for council, (first, last, authority) in sorted(unmatched.items()):
            print(f"unmatched: {council} {authority} {first}-{last}", file=sys.stderr)
        return cs.write()
