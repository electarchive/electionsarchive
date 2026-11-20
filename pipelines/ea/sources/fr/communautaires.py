from datetime import date
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.fr import municipales, municipales_history
from ea.store import DATA

SOURCE = "fr.communautaires"
RAW = "fr.municipales"
REFERENCE = DATA / "reference" / "fr"
INTERIEUR = "Ministère de l'Intérieur"
MINISTRY_SOURCE = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fministry"))
YEARS = (2014, 2020, 2026)
PARSED = DATA / "parsed" / "fr.communautaires"
ROUNDS = {
    2014: {1: date(2014, 3, 23), 2: date(2014, 3, 30)},
    2020: {1: date(2020, 3, 15), 2: date(2020, 6, 28)},
    2026: {1: date(2026, 3, 15), 2: date(2026, 3, 22)},
}


def fetch() -> None:
    for year in (2014, 2020):
        for round_ in (1, 2):
            url = municipales_history.FILES[year, round_]
            raw.fetch(RAW, url, suffix=f".{url.rsplit('.', 1)[1]}")
    for round_ in (1, 2):
        url = municipales.FILES[round_, "communes"]
        raw.fetch(RAW, url, suffix=".csv")
    raw.fetch(RAW, municipales.SEATS, suffix=".xlsx")


def history() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Per-list votes and intercommunal seats, and per-commune accounting, for 2014 and 2020."""
    accounting: list[dict[str, object]] = []
    lists: list[dict[str, object]] = []
    for year in (2014, 2020):
        prefix, block = municipales_history.LAYOUTS[year]
        for round_ in (1, 2):
            for fields in municipales_history.rows_of(year, round_):
                while fields and fields[-1] is None:
                    fields.pop()
                code = municipales_history.code_of(str(fields[prefix.index("dept")]), str(fields[prefix.index("code")]))
                if code is None:
                    continue
                head = dict(zip(prefix, fields[: len(prefix)], strict=False))
                rest = municipales_history.regroup(fields[len(prefix) :], year)
                rest += [None] * (-len(rest) % len(block))
                accounting.append(
                    {
                        "year": year,
                        "round": round_,
                        "unit": code,
                        "label": head["label"],
                        **{
                            name: municipales_history.number(head.get(name))
                            for name, _ in municipales_history.ACCOUNTING
                        },
                    }
                )
                for index in range(len(rest) // len(block)):
                    entry = dict(zip(block, rest[index * len(block) : (index + 1) * len(block)], strict=True))
                    if entry["votes"] is None:
                        continue
                    lists.append(
                        {
                            "year": year,
                            "round": round_,
                            "unit": code,
                            "panel": municipales_history.number(entry.get("panel")) or index + 1,
                            "nuance": entry["nuance"],
                            "list_label": entry.get("list_label"),
                            "votes": municipales_history.number(entry["votes"]),
                            "seats": municipales_history.number(entry.get("seats")),
                            "cc_seats": municipales_history.number(entry.get("cc_seats")),
                        }
                    )
    return accounting, lists


def current() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    accounting: list[dict[str, object]] = []
    lists: list[dict[str, object]] = []
    for round_ in (1, 2):
        frame = pl.read_csv(
            raw.cached(RAW, municipales.FILES[round_, "communes"]).path,
            separator=";",
            infer_schema_length=0,
            truncate_ragged_lines=True,
            null_values=[""],
        )
        count = max(int(c.rsplit(" ", 1)[1]) for c in frame.columns if c.startswith("Voix "))
        for row in frame.iter_rows(named=True):
            unit = str(row["Code commune"])
            accounting.append(
                {
                    "year": 2026,
                    "round": round_,
                    "unit": unit,
                    "label": row["Libellé commune"],
                    "registered": int(str(row["Inscrits"])),
                    "voters": int(str(row["Votants"])),
                    "valid": int(str(row["Exprimés"])),
                    "invalid": int(str(row["Nuls"])),
                    "blank": int(str(row["Blancs"])),
                }
            )
            for i in range(1, count + 1):
                if row.get(f"Voix {i}") is None:
                    continue
                cc = row.get(f"Sièges au CC {i}")
                cm = row.get(f"Sièges au CM {i}")
                lists.append(
                    {
                        "year": 2026,
                        "round": round_,
                        "unit": unit,
                        "panel": int(str(row.get(f"Numéro de panneau {i}") or i)),
                        "nuance": row.get(f"Nuance liste {i}"),
                        "list_label": row.get(f"Libellé de liste {i}"),
                        "votes": int(str(row[f"Voix {i}"])),
                        "seats": int(str(cm)) if cm is not None else None,
                        "cc_seats": int(str(cc)) if cc is not None else None,
                    }
                )
    return accounting, lists


def tables() -> None:
    accounting: list[dict[str, object]] = []
    lists: list[dict[str, object]] = []
    for part in (history(), current()):
        accounting += part[0]
        lists += part[1]
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(accounting, infer_schema_length=None).write_parquet(PARSED / "accounting.parquet")
    frame = pl.DataFrame(lists, infer_schema_length=None)
    frame.write_parquet(PARSED / "lists.parquet")
    print(
        frame.group_by("year", "round")
        .agg(
            communes=pl.col("unit").n_unique(),
            lists=pl.len(),
            with_cc=(pl.col("cc_seats").fill_null(0) > 0).sum(),
            cc_seats=pl.col("cc_seats").sum(),
        )
        .sort("year", "round")
    )


DATASETS = {
    2014: "https://www.data.gouv.fr/datasets/5369939aa3a729239d20423c/",
    2020: "https://www.data.gouv.fr/datasets/5e7102d5ebe5d838b7581662/",
    2026: "https://www.data.gouv.fr/datasets/elections-municipales-2026-resultats-du-premier-tour",
}
NOTE = (
    "Since 2014, the municipal ballot in communes of 1,000 inhabitants or more carries a second list of candidates for "
    "the commune's seats on its intercommunal council (fléchage, Code électoral, art. L273-1 and following). No separate "
    "vote is cast: the votes recorded here are the municipal list votes of the commune's own contest, and each list "
    "wins intercommunal seats in proportion to them, as the Interior Ministry's file declares. The seats are stored as "
    "declared by the ministry. {scope}"
)
SCOPE = {
    2014: "The 2014 file does not say which intercommunal body the commune belongs to, so this contest has no body.",
    2020: "The 2020 file does not say which intercommunal body the commune belongs to, so this contest has no body.",
    2026: "The commune's intercommunal body is the one in the ministry's 2026 seats referential, when the commune has one.",
}
BLANK_NOTE = " The 2014 file reports blank and invalid ballots as one figure, which is recorded as invalid ballots."


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def councils() -> pl.DataFrame:
    """One row per EPCI in the 2026 referential, with its council size."""
    frame = pl.read_excel(raw.cached(RAW, municipales.SEATS).path, infer_schema_length=0).filter(
        pl.col("CODE_COM_ASSOCIEE").is_null() & pl.col("CODE_EPCI").is_not_null()
    )
    return (
        frame.group_by("CODE_EPCI")
        .agg(name=pl.col("LIB_EPCI").first(), seats=pl.col("NBRE_SAP_EPCI").cast(pl.Int64).sum())
        .sort("CODE_EPCI")
    )


def membership() -> dict[str, str]:
    frame = pl.read_excel(raw.cached(RAW, municipales.SEATS).path, infer_schema_length=0).filter(
        pl.col("CODE_COM_ASSOCIEE").is_null() & pl.col("CODE_EPCI").is_not_null()
    )
    return dict(frame.select(pl.col("CODE_COMMUNE").str.zfill(5), "CODE_EPCI").iter_rows())


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{year}" for year in YEARS)]


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The intercommunal councils (EPCI) of France and the Interior Ministry's municipal files as a source",
    )
    p.add(
        "source",
        id=MINISTRY_SOURCE,
        kind="official_body",
        publisher=INTERIEUR,
        title="Élections municipales: résultats par commune, 2014, 2020, and 2026",
        url=DATASETS[2026],
        license="Licence Ouverte v2.0",
        license_url="https://www.etalab.gouv.fr/licence-ouverte-open-licence",
        attribution=INTERIEUR,
    )
    for year in (2014, 2020):
        for round_ in (1, 2):
            p.artifact(raw.cached(RAW, municipales_history.FILES[year, round_]), MINISTRY_SOURCE)
    for round_ in (1, 2):
        p.artifact(raw.cached(RAW, municipales.FILES[round_, "communes"]), MINISTRY_SOURCE)
    p.artifact(raw.cached(RAW, municipales.SEATS), MINISTRY_SOURCE)
    for row in councils().iter_rows(named=True):
        polity = key("polity", row["CODE_EPCI"])
        body = key("body", row["CODE_EPCI"])
        p.add("polity", id=polity, kind="special_district")
        p.name(polity, str(row["name"]), "fr", "official")
        p.add("external_id", entity_id=polity, scheme="fr.communautaires.siren", value=row["CODE_EPCI"])
        p.add("body", id=body, polity_id=polity, kind="council")
        p.name(body, f"Council of {row['name']}", "en", "official")
        p.name(body, f"Conseil communautaire, {row['name']}", "fr", "native", False)
        p.add("body_size", body_id=body, seats=row["seats"], valid_from=ROUNDS[2026][1])
    return p


def reference(year: int) -> dict[str, pl.DataFrame]:
    ids = pl.scan_parquet(REFERENCE / "external_id.parquet").filter(pl.col("pipeline") == RAW)
    prefix = f"{year}:"
    contests = (
        ids.filter((pl.col("scheme") == "fr.municipales.contest") & pl.col("value").str.starts_with(prefix))
        .select(contest_id="entity_id", key="value")
        .collect()
        .join(
            pl.read_parquet(REFERENCE / "contest.parquet").select(
                contest_id="id", election_id="election_id", area_id="district_area_id", title="name"
            ),
            on="contest_id",
        )
    )
    nuances = (
        ids.filter((pl.col("scheme") == "fr.municipales.nuance") & pl.col("value").str.starts_with(prefix))
        .select(party_id="entity_id", nuance=pl.col("value").str.slice(len(prefix)))
        .collect()
    )
    units = pl.read_parquet(REFERENCE / "reporting_unit.parquet").filter(
        (pl.col("pipeline") == RAW) & pl.col("scheme_code").is_in(["fr.commune", "fr.secteur"])
    )
    return {"contests": contests, "nuances": nuances, "units": units}


def covers(start: object, end: object, day: date) -> bool:
    return (not isinstance(start, date) or start <= day) and (not isinstance(end, date) or day <= end)


def election(year: int) -> Package:
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {year} intercommunal council seats elected on the French municipal ballot, by commune",
        depends=[f"{SOURCE}/registry"],
    )
    ref = reference(year)
    contests = {row["key"]: row for row in ref["contests"].iter_rows(named=True)}
    parties = {nuance: party for party, nuance in ref["nuances"].iter_rows()}
    units: dict[str, list[dict[str, object]]] = {}
    for row in ref["units"].iter_rows(named=True):
        units.setdefault(str(row["code"]), []).append(row)
    accounting = pl.read_parquet(PARSED / "accounting.parquet").filter(pl.col("year") == year)
    lists = pl.read_parquet(PARSED / "lists.parquet").filter(pl.col("year") == year)
    totals = {(row["round"], row["unit"]): row for row in accounting.iter_rows(named=True)}
    epci = membership() if year == 2026 else {}
    for round_ in (1, 2):
        p.uses(
            raw.cached(
                RAW, municipales.FILES[round_, "communes"] if year == 2026 else municipales_history.FILES[year, round_]
            )
        )
    if year == 2026:
        p.uses(raw.cached(RAW, municipales.SEATS))
    grouped: dict[tuple[int, str], list[dict[str, object]]] = {}
    for row in lists.sort("panel").iter_rows(named=True):
        grouped.setdefault((int(row["round"]), str(row["unit"])), []).append(row)
    built: dict[tuple[int, str], str] = {}
    missing: list[str] = []
    note = NOTE.format(scope=SCOPE[year]) + (BLANK_NOTE if year == 2014 else "")
    values: list[dict[str, object]] = []
    for (round_, unit), entries in sorted(grouped.items()):
        seats = sum(int(str(e["cc_seats"] or 0)) for e in entries)
        if seats == 0:
            continue
        found = contests.get(f"{year}:{unit}:{round_}")
        day = ROUNDS[year][round_]
        candidates = [u for u in units.get(unit, []) if covers(u["valid_from"], u["valid_to"], day)]
        if found is None or len(candidates) != 1:
            missing.append(f"{year}:{unit}:{round_}")
            continue
        unit_id = str(candidates[0]["id"])
        p.external |= {str(found["election_id"]), str(found["area_id"]), unit_id}
        contest = p.id("contest", year, unit, round_)
        built[round_, unit] = contest
        siren = epci.get(unit)
        title = str(found["title"])
        title = (
            title.replace("municipal election", "intercommunal council seats")
            if "municipal election" in title
            else f"{title}, intercommunal council seats"
        )
        p.add(
            "contest",
            id=contest,
            election_id=found["election_id"],
            kind="list_allocation",
            body_id=key("body", siren) if siren else None,
            district_area_id=found["area_id"],
            magnitude=seats,
            round_number=round_,
            previous_round_id=built.get((1, unit)) if round_ == 2 else None,
            default_result_kind="counted",
        )
        p.name(contest, title, "en", "official")
        result_set = p.id("result_set", year, unit, round_)
        p.add(
            "result_set",
            id=result_set,
            election_id=found["election_id"],
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=MINISTRY_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=note, lang="en")
        total = totals[round_, unit]
        for field, measure in municipales_history.ACCOUNTING:
            if total.get(field) is not None:
                values.append(
                    {
                        "result_set_id": result_set,
                        "unit_id": unit_id,
                        "option_id": None,
                        "measure": measure,
                        "value": total[field],
                    }
                )
        for entry in entries:
            option = p.id("option", year, unit, round_, entry["panel"])
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=parties.get(str(entry["nuance"])),
                ballot_position=entry["panel"],
                ballot_label=entry["list_label"],
            )
            values.append(
                {
                    "result_set_id": result_set,
                    "unit_id": unit_id,
                    "option_id": option,
                    "measure": 1,
                    "value": entry["votes"],
                }
            )
            if entry["cc_seats"]:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=entry["cc_seats"])
    if year == 2026:
        p.accept(
            "accounting.votes_valid",
            "In 16 communes the ministry's results file gives list votes that do not add up to its own valid-ballot "
            "figure; both are recorded as published.",
        )
    p.external |= {party for party in parties.values()}
    frame = pl.DataFrame(values, schema_overrides={"option_id": pl.String}).with_columns(
        channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)
    )
    p.frame("result_value", frame)
    print(f"{year}: {len(built)} contests, {len(missing)} without an existing municipal contest: {missing[:5]}")
    return p


def parse() -> None:
    tables()
    registry().write()
    for year in YEARS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())
