import os
import re
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.fr.names import person_name
from ea.store import DATA, connect

SOURCE = "fr.municipales"
VERSION = "h1"
PARSED = DATA / "parsed" / "fr.municipales_history"
DAYS = {
    2001: (date(2001, 3, 11), date(2001, 3, 18)),
    2008: (date(2008, 3, 9), date(2008, 3, 16)),
    2014: (date(2014, 3, 23), date(2014, 3, 30)),
    2020: (date(2020, 3, 15), date(2020, 6, 28)),
}
NEXT = {2001: DAYS[2008][0], 2008: DAYS[2014][0], 2014: DAYS[2020][0], 2020: date(2026, 3, 15)}
STORAGE = "https://www.data.gouv.fr/storage/f"
STATIC = "https://static.data.gouv.fr"
R2020 = f"{STATIC}/resources/municipales-2020-resultats-2nd-tour"
FILES = {
    (2001, 0): f"{STATIC}/a8/7ed1963602a96808a3795400346086fb22e940ee1f1b87d0e8cd189fe6d12b.xls",
    (2008, 0): f"{STATIC}/18/22c3d2b3c8e3abb0fb447515ce23ad4858ad8b3522749d9924acc218f7b7a9.xls",
    (2014, 1): f"{STORAGE}/2014-03-25T16-06-23/muni-2014-resultats-com-1000-et-plus-t1.txt",
    (2014, 2): f"{STORAGE}/2014-03-31T09-49-28/muni-2014-resultats-com-1000-et-plus-t2.txt",
    (
        2020,
        1,
    ): f"{STATIC}/resources/elections-municipales-2020-resultats/20200525-133704/2020-05-18-resultats-communes-de-1000-et-plus.txt",
    (2020, 2): f"{R2020}/20200629-192435/2020-06-29-resultats-t2-communes-de-1000-hab-et-plus.txt",
}
DATASETS = {
    2001: "https://www.data.gouv.fr/datasets/53699392a3a729239d20421e/",
    2008: "https://www.data.gouv.fr/datasets/53699393a3a729239d204224/",
    2014: "https://www.data.gouv.fr/datasets/5369939aa3a729239d20423c/",
    2020: "https://www.data.gouv.fr/datasets/5e7102d5ebe5d838b7581662/",
}
ACCOUNTING = (
    ("registered", 2),
    ("voters", 14),
    ("valid", 4),
    ("invalid", 5),
    ("blank", 6),
)
LAYOUTS = {
    2001: (
        ["dept", "dept_label", "code", "label", "population", "registered", "abstentions", "pa", "voters", "pv"]
        + ["invalid", "pi1", "pi2", "valid", "pe1", "pe2"],
        ["nuance", "short", "votes", "p1", "p2"],
    ),
    2008: (
        ["dept", "dept_label", "code", "label", "registered", "abstentions", "pa", "voters", "pv"]
        + ["invalid", "pi1", "pi2", "valid", "pe1", "pe2"],
        ["nuance", "sex", "surname", "forename", "list_label", "seats", "votes", "p1", "p2"],
    ),
    2014: (
        ["export", "dept", "kind", "dept_label", "code", "label", "registered", "abstentions", "pa", "voters", "pv"]
        + ["invalid", "pi1", "pi2", "valid", "pe1", "pe2"],
        [
            "nuance",
            "sex",
            "surname",
            "forename",
            "list_label",
            "seats",
            "sector_seats",
            "cc_seats",
            "votes",
            "p1",
            "p2",
        ],
    ),
    2020: (
        ["dept", "dept_label", "code", "label", "registered", "abstentions", "pa", "voters", "pv"]
        + ["blank", "pb1", "pb2", "invalid", "pi1", "pi2", "valid", "pe1", "pe2"],
        ["panel", "nuance", "sex", "surname", "forename", "list_label", "seats", "sector_seats", "cc_seats", "votes"]
        + ["p1", "p2"],
    ),
}
RENUMBERED = {"97123": "97701", "97127": "97801"}
NUANCES = {
    "LGA": "Liste de gauche",
    "LDR": "Liste de droite",
    "LDD": "Liste divers droite",
    "LDG": "Liste divers gauche",
    "LMN": "Liste MNR",
}
NOTE = (
    "Each commune elects its municipal council from closed lists in up to two rounds. A list that wins an absolute "
    "majority of the valid votes in the first round, or the most votes in the second, receives half of the seats, "
    "rounded up. The remaining seats are shared by the highest-average (D'Hondt) method among all lists with at "
    "least 5 percent of the valid votes, the leading list included, and go to candidates in list order. Only lists "
    "with at least 10 percent of the first-round vote may stand in the second round, and they may merge with lists "
    "that took at least 5 percent."
)
NOTE_SCOPE = {
    2001: "Only communes of 3,500 or more inhabitants are loaded, from the ministry's file. It gives no seats, so "
    "none are recorded.",
    2008: "Only communes of 3,500 or more inhabitants are loaded.",
    2014: "Only communes of 1,000 or more inhabitants are loaded, which elect by closed lists.",
    2020: "Only communes of 1,000 or more inhabitants are loaded, which elect by closed lists. The second round "
    "was held on June 28 after the first was interrupted by the pandemic.",
}
NOTE_INVALID = (
    "The ministry's file for this year gives one count of blank and spoiled ballots together, stored as invalid."
)
RULES = {
    "formula": "dhondt",
    "tieBreak": "votes",
    "threshold": {"percent": 5, "level": "list", "scope": "contest"},
    "lists": {"order": "closed", "exhausted": "vacant", "vacancy": "next_on_list"},
    "bonus": {
        "level": "list",
        "share": {"percent": 50, "strict": True, "rounds": [1]},
        "size": {"kind": "fraction", "fraction": 0.5, "rounding": "up"},
    },
}


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=f".{url.rsplit('.', 1)[1]}")


def number(value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    return int(float(value.replace(",", ".")))


def code_of(dept: str, commune: str) -> str | None:
    dept = dept.strip().upper()
    commune = commune.strip()
    if not commune.isdigit():
        return None
    if dept == "ZM":
        return f"976{commune.zfill(3)[1:]}"
    if dept == "ZS":
        return f"975{commune.zfill(2)}"
    if dept in ("ZN", "ZP", "ZW"):
        return None
    if dept.startswith("Z"):
        return RENUMBERED.get(f"97{commune.zfill(3)}", f"97{commune.zfill(3)}")
    return f"{dept.zfill(2)}{commune.zfill(3)}"


def rows_of(year: int, round_: int) -> list[list[str | None]]:
    path = raw.cached(SOURCE, FILES[year, 0 if year in (2001, 2008) else round_]).path
    if year in (2001, 2008):
        sheets = pl.read_excel(path, sheet_id=0, has_header=False, infer_schema_length=0, engine="calamine")
        return [list(row) for row in sheets[f"Tour {round_}"].slice(1).rows()]
    text = path.read_bytes().decode("cp1252", errors="replace")
    sep = "\t" if year == 2020 and round_ == 1 else ";"
    lines = text.split("\n")[1:]
    return [[field.strip() or None for field in line.rstrip("\r").split(sep)] for line in lines if line.strip()]


def unpack(rest: list[str | None]) -> list[str | None]:
    packed = [index for index, field in enumerate(rest) if field is not None and ";" in field]
    if not packed:
        return rest
    index = packed[0]
    return [*rest[:index], *[field or None for field in str(rest[index]).split(";")], *rest[index + 1 :]]


def regroup(rest: list[str | None], year: int) -> list[str | None]:
    lead = 1 if year == 2020 else 0
    width = 4 if year == 2008 else 6
    starts = [
        index
        for index in range(len(rest) - lead - 3)
        if (not lead or str(rest[index]).isdigit())
        and re.fullmatch(r"[A-Z0-9-]{1,8}", str(rest[index + lead]))
        and rest[index + lead + 1] in ("M", "F")
        and rest[index + lead + 2] is not None
    ]
    blocks: list[str | None] = []
    if not starts:
        return blocks
    for begin, end in zip(starts, [*starts[1:], len(rest)], strict=True):
        head, label, tail = (
            rest[begin : begin + lead + 4],
            rest[begin + lead + 4 : end - width],
            rest[end - width : end],
        )
        blocks += [*head, ";".join(str(part) for part in label if part is not None) or None, *tail]
    return blocks


def parse() -> None:
    accounting: list[dict[str, object]] = []
    lists: list[dict[str, object]] = []
    for year in DAYS:
        prefix, block = LAYOUTS[year]
        for round_ in (1, 2):
            if year in (2014, 2020) and (year, round_) not in FILES:
                continue
            for fields in rows_of(year, round_):
                while fields and fields[-1] is None:
                    fields.pop()
                code = code_of(str(fields[prefix.index("dept")]), str(fields[prefix.index("code")]))
                if code is None:
                    continue
                head = dict(zip(prefix, fields[: len(prefix)], strict=False))
                rest = fields[len(prefix) :]
                size = len(block)
                if year == 2008:
                    rest = unpack(rest)
                if year != 2001:
                    rest = regroup(rest, year)
                rest += [None] * (-len(rest) % size)
                accounting.append(
                    {
                        "year": year,
                        "round": round_,
                        "unit": code,
                        "label": head["label"],
                        "dept": str(head["dept"]).strip().upper().zfill(2),
                        **{name: number(head.get(name)) for name, _ in ACCOUNTING},
                    }
                )
                entries: list[dict[str, object]] = []
                try:
                    for index in range(len(rest) // size):
                        entry = dict(zip(block, rest[index * size : (index + 1) * size], strict=True))
                        if entry["votes"] is None:
                            continue
                        entries.append(
                            {
                                "year": year,
                                "round": round_,
                                "unit": code,
                                "panel": number(entry.get("panel")) or index + 1,
                                "printed": number(entry.get("panel")),
                                "nuance": entry["nuance"],
                                "short": entry.get("short"),
                                "list_label": entry.get("list_label"),
                                "sex": entry.get("sex"),
                                "surname": entry.get("surname"),
                                "forename": entry.get("forename"),
                                "seats": number(entry.get("seats")),
                                "votes": number(entry["votes"]),
                            }
                        )
                except ValueError:
                    entries = []
                lists += entries
    accounting_frame = pl.DataFrame(accounting, infer_schema_length=None)
    list_frame = pl.DataFrame(lists, infer_schema_length=None)
    sums = list_frame.group_by("year", "round", "unit").agg(total=pl.col("votes").sum())
    accounting_frame = accounting_frame.join(sums, on=["year", "round", "unit"], how="left").with_columns(
        complete=pl.col("total") == pl.col("valid")
    )
    print(
        accounting_frame.group_by("year", "round")
        .agg(communes=pl.len(), complete=pl.col("complete").sum())
        .sort("year", "round")
    )
    print(
        "duplicate communes:",
        accounting_frame.filter(accounting_frame.select("year", "round", "unit").is_duplicated()).height,
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    accounting_frame.write_parquet(PARSED / "accounting.parquet")
    list_frame.write_parquet(PARSED / "lists.parquet")


def emit() -> Path:
    year = int(os.environ["MUN_YEAR"])
    days = DAYS[year]
    accounting = pl.read_parquet(PARSED / "accounting.parquet").filter(pl.col("year") == year, pl.col("complete"))
    lists = pl.read_parquet(PARSED / "lists.parquet").filter(pl.col("year") == year)
    lists = lists.join(accounting.select("round", "unit"), on=["round", "unit"])
    by_unit = {(row["round"], row["unit"]): row for row in accounting.iter_rows(named=True)}
    list_rows: dict[tuple[int, str], list[dict[str, object]]] = {}
    for row in lists.sort("panel").iter_rows(named=True):
        list_rows.setdefault((row["round"], row["unit"]), []).append(row)
    sizes: dict[str, int] = {}
    for (round_, code), entries in sorted(list_rows.items()):
        total = sum(int(str(entry["seats"] or 0)) for entry in entries)
        if total:
            sizes[code] = total
    communes = sorted({code for _, code in by_unit})
    with connect() as conn:
        paths = {
            str(code): str(path).removeprefix("fr/")
            for code, path in conn.execute(
                "select e.value, s.path from external_id e join slug s on s.entity_id = e.entity_id"
                " where e.scheme = 'fr.municipales.polity'"
            ).fetchall()
        }
        taken = {
            str(path).removeprefix("fr/")
            for (path,) in conn.execute("select path from slug where path like 'fr/%' and path not like 'fr/%/%'")
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"{year} French municipal elections, both rounds, by commune (communes with list elections)",
            polity="FR",
        )

        def name(entity: UUID, text: str, lang: str = "fr", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        france = cs.polity("FR")
        source = cs.source("Ministère de l'Intérieur", f"Élections municipales {year}")
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher="Ministère de l'Intérieur",
            title=f"Élections municipales {year}",
            url=DATASETS[year],
            license="Licence Ouverte v2.0",
            license_url="https://www.etalab.gouv.fr/licence-ouverte-open-licence",
        )
        for key, url in FILES.items():
            if key[0] == year:
                cs.artifact(raw.cached(SOURCE, url), source)

        nuances: dict[str, UUID] = {}
        shorts = dict(lists.drop_nulls("short").unique("nuance").select("nuance", "short").iter_rows())
        for code in lists.drop_nulls("nuance").unique("nuance").sort("nuance")["nuance"]:
            nuances[code] = cs.key(f"{SOURCE}.nuance", f"{year}:{code}")
            cs.add("party", id=nuances[code], kind="nuance", polity_id=france)
            name(nuances[code], shorts.get(code, code))
            name(nuances[code], code, "fr", "abbreviation")
        system = cs.key(f"{SOURCE}.electoral_system", "list-1983")
        cs.add("electoral_system", id=system, polity_id=france, rules=RULES, rules_version=1)
        name(system, "French municipal list system", "en")
        cs.add("note", entity_id=system, kind="methodology", body_md=NOTE)
        umbrella = cs.key(f"{SOURCE}.election", str(year))
        cs.add(
            "election",
            id=umbrella,
            polity_id=france,
            kind="local",
            date_from=days[0],
            date_to=days[1],
            status="held",
        )
        name(umbrella, f"{year} French municipal elections", "en")
        name(umbrella, f"Élections municipales de {year}", "fr", "native")
        cs.add("slug", path=f"fr/{days[0]}-local", entity_id=umbrella)
        cs.add("note", entity_id=umbrella, kind="caveat", body_md=NOTE_SCOPE[year])
        if year != 2020:
            cs.add("note", entity_id=umbrella, kind="caveat", body_md=NOTE_INVALID)

        persons: dict[str, UUID] = {}
        values: list[dict[str, object]] = []
        pending: set[str] = set()

        def path_of(code: str, label: str) -> str:
            if code in paths:
                return paths[code]
            path = slug(label)
            if path in taken or path in pending:
                path = f"{path}-{code}"
            pending.add(path)
            paths[code] = path
            return path

        for code in communes:
            first = by_unit.get((1, code)) or by_unit[2, code]
            label = str(first["label"])
            path = f"fr/{path_of(code, label)}"
            department = code[:3] if code.startswith(("97", "98")) else code[:2]
            area = cs.area("fr.commune", code)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code="fr.commune")
                cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=None)
                cs.add(
                    "area_parent",
                    area_id=area,
                    parent_id=cs.area("fr.departement", department),
                    valid_from=None,
                    valid_to=None,
                )
                name(area, label)
            unit = cs.unit("fr.commune", code)
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=france,
                    scheme_code="fr.commune",
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                    valid_to=None,
                )
            polity = cs.key(f"{SOURCE}.polity", code)
            if polity in cs.minted:
                cs.add("polity", id=polity, kind="municipality", iso_code=None)
                name(polity, label)
                cs.add("polity_parent", polity_id=polity, parent_id=france)
                cs.add("polity_territory", polity_id=polity, area_id=area)
                cs.add("slug", path=path, entity_id=polity)
            body = cs.key(f"{SOURCE}.body", code)
            if body in cs.minted:
                cs.add("body", id=body, polity_id=polity, kind="unicameral")
                name(body, f"Municipal council of {label}", "en")
                name(body, f"Conseil municipal de {label}", "fr", "native")
            size = sizes.get(code)
            if size is not None:
                cs.add(
                    "body_size",
                    body_id=body,
                    seats=size,
                    valid_from=days[0],
                    valid_to=NEXT[year] - timedelta(days=1),
                )
            elections: dict[int, UUID] = {}
            previous = None
            for round_ in (1, 2):
                if (round_, code) not in by_unit:
                    continue
                day = days[round_ - 1]
                kind = "local" if round_ == 1 else "runoff"
                key = f"{year}:{code}:{round_}"
                elections[round_] = cs.key(f"{SOURCE}.election", key)
                cs.add(
                    "election",
                    id=elections[round_],
                    polity_id=polity,
                    parent_id=umbrella if round_ == 1 else elections.get(1, umbrella),
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(
                    elections[round_],
                    f"{year} {label} municipal election" + (", second round" if round_ == 2 else ""),
                    "en",
                )
                cs.add("slug", path=f"{path}/{day}-{kind}", entity_id=elections[round_])
                contest = cs.key(f"{SOURCE}.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=elections[round_],
                    kind="list_allocation",
                    body_id=body,
                    district_area_id=area,
                    magnitude=size,
                    round_number=round_,
                    previous_round_id=previous,
                    electoral_system_id=system,
                    default_result_kind="counted",
                )
                name(contest, f"{year} {label} municipal election, {'first' if round_ == 1 else 'second'} round", "en")
                cs.add("slug", path=f"{path}/{day}-{kind}/conseil-municipal", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=elections[round_],
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
                row = by_unit[round_, code]
                for field, measure in ACCOUNTING:
                    if row[field] is not None:
                        values.append(
                            {
                                "result_set_id": str(result_set),
                                "unit_id": str(unit),
                                "option_id": None,
                                "measure": measure,
                                "value": row[field],
                            }
                        )
                for entry in list_rows.get((round_, code), []):
                    panel = int(str(entry["panel"]))
                    option = cs.key(f"{SOURCE}.option", f"{key}:{panel}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        party_id=nuances.get(str(entry["nuance"])),
                        ballot_position=entry["printed"],
                        ballot_label=entry["list_label"] or entry["short"],
                    )
                    values.append(
                        {
                            "result_set_id": str(result_set),
                            "unit_id": str(unit),
                            "option_id": str(option),
                            "measure": 1,
                            "value": entry["votes"],
                        }
                    )
                    if entry["seats"]:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=entry["seats"])
                    if entry["surname"] is not None:
                        person_key = f"{year}:{code}:{entry['surname']}:{entry['forename']}"
                        if person_key not in persons:
                            persons[person_key] = cs.key(f"{SOURCE}.person", person_key)
                            if persons[person_key] in cs.minted:
                                cs.add(
                                    "person",
                                    id=persons[person_key],
                                    gender={"F": "female", "M": "male"}.get(str(entry["sex"])),
                                )
                                name(
                                    persons[person_key],
                                    person_name(str(entry["forename"] or ""), str(entry["surname"])),
                                )
                        cs.add(
                            "candidacy",
                            id=cs.key(f"{SOURCE}.candidacy", f"{key}:{panel}:1"),
                            person_id=persons[person_key],
                            option_id=option,
                            role="head",
                            list_position=1,
                            ballot_name=f"{entry['surname']} {entry['forename'] or ''}".strip(),
                        )
                previous = contest
        cs.frame(
            "result_value",
            pl.DataFrame(values, schema_overrides={"value": pl.Int64}, infer_schema_length=None)
            .drop_nulls("value")
            .with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.col("measure").cast(pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        return cs.write()
