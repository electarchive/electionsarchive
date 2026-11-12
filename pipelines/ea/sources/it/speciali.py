import html
import re
from dataclasses import dataclass
from datetime import date
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.it.interno import BLANK, INVALID, REGISTERED, VOTERS, VOTES
from ea.sources.it.storico import EXISTING, NEW, cased
from ea.store import DATA, connect

SOURCE = "it.speciali"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
VDA = "https://www.regione.vda.it/amministrazione/Elezioni/Dati_e_risultati/elezioni/VotiLista_i.aspx?idele={}&ord=1&setcar=n"


@dataclass(frozen=True)
class Region:
    iso: str
    slug: str
    native: str
    english: str
    council_it: str
    council_en: str
    publisher: str
    title: str
    home: str
    counted: str
    allocated: str


REGIONS = {
    "02": Region(
        iso="IT-23",
        slug="valle-d-aosta",
        native="Valle d'Aosta",
        english="Aosta Valley",
        council_it="Consiglio regionale della Valle d'Aosta",
        council_en="Regional Council of the Aosta Valley",
        publisher="Regione autonoma Valle d'Aosta",
        title="Elezioni: dati e risultati, elezioni regionali",
        home="https://www.regione.vda.it/amministrazione/Elezioni/Dati_e_risultati/Risultati/Elezioni_regionali/default_i.aspx",
        counted=(
            "Results are the Aosta Valley Region's regional summary of list votes and seats, from its election "
            "results pages."
        ),
        allocated=(
            "The council's 35 seats are allocated by list across the whole region, with a majority bonus for a list "
            "or group of lists with a common program, and the council elects the President of the Region."
        ),
    ),
}
VDA_HELD = {
    (date(2003, 6, 8), date(2003, 6, 8)): 62,
    (date(2008, 5, 25), date(2008, 5, 25)): 92,
    (date(2013, 5, 26), date(2013, 5, 26)): 113,
    (date(2018, 5, 20), date(2018, 5, 20)): 137,
    (date(2020, 9, 20), date(2020, 9, 21)): 142,
    (date(2025, 9, 28), date(2025, 9, 28)): 190,
}
PARTIES = {
    "PARTITO DEMOCRATICO": "PARTITO DEMOCRATICO",
    "MOVIMENTO 5 STELLE": "MOVIMENTO 5 STELLE",
    "MOVIMENTO CINQUE STELLE": "MOVIMENTO 5 STELLE",
    "IL POPOLO DELLA LIBERTÀ": "IL POPOLO DELLA LIBERTA'",
    "POPOLO DELLA LIBERTA'": "IL POPOLO DELLA LIBERTA'",
    "LEGA SALVINI VALLÉE D'AOSTE": "LEGA",
    "LEGA VALLÉE D'AOSTE": "LEGA",
    "FRATELLI D'ITALIA - GIORGIA MELONI": "FRATELLI D'ITALIA",
}
REGIONAL_PARTIES = {
    "UNION VALDÔTAINE": ("Union Valdôtaine", "UV"),
    "STELLA ALPINA": ("Stella Alpina", "SA"),
    "UNION VALDÔTAINE PROGRESSISTE": ("Union Valdôtaine Progressiste", "UVP"),
    "FÉDÉRATION AUTONOMISTE": ("Fédération Autonomiste", "FA"),
    "FEDERAZIONE AUTONOMISTA - FEDERATION AUTONOMISTE": ("Fédération Autonomiste", "FA"),
    "ALPE": ("Autonomie Liberté Participation Écologie", "ALPE"),
}
NOTES = {
    ("02", date(2025, 9, 28)): (
        "Seats are as the region shows them after the Council of State's ruling of May 7, 2026, which upheld "
        "Cristina Machet's removal from the council; her seat went to Andrea Campotaro of Alleanza Verdi Sinistra, "
        "the list with the lower vote total among those tied on remainders."
    ),
}
COUNT_LABELS = {"Elettori": "registered", "Votanti": "voters", "Schede bianche": "blank", "Schede nulle": "null"}


def vda_url(number: int) -> str:
    return VDA.format(number)


def fetch() -> None:
    for number in VDA_HELD.values():
        raw.fetch(SOURCE, vda_url(number), suffix=".html")


def number(text: str) -> int:
    return int(text.replace(".", "").strip())


def vda(page: str) -> tuple[dict[str, int], list[dict[str, object]]]:
    text = re.sub(r"(?s)<script.*?</script>|<style.*?</style>", "", page)
    text = re.sub(r"</t[dh]>", "|", text)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"\s+", " ", text)
    start = text.index("Riepilogo regionale")
    end = text.find("Riepilogo per programma", start)
    end = text.index("Elenco ", start) if end < 0 else end
    summary = text[start:end]
    counts = {
        key: number(match[1])
        for label, key in COUNT_LABELS.items()
        if (match := re.search(rf"{label}\s*\|\s*([\d.]+)", summary))
    }
    body = summary[summary.index("Seggi|") + len("Seggi|") :]
    lists = []
    for match in re.finditer(
        r"\|\s*\|\s*([^|]+?)\s*\|\s*(?:(\d*)\s*\|)?\s*([\d.]+)\s*\|\s*[\d,]+%\s*\|\s*\d+\s*\|\s*(\d+|-)\s*\|", body
    ):
        lists.append(
            {
                "list": match[1].strip(),
                "group": int(match[2]) if match[2] else None,
                "votes": number(match[3]),
                "seats": 0 if match[4] == "-" else int(match[4]),
            }
        )
    return counts, lists


def parse() -> None:
    elections = []
    lists = []
    for (day, last), number_ in VDA_HELD.items():
        counts, rows = vda(raw.cached(SOURCE, vda_url(number_)).path.read_text(encoding="utf-8", errors="replace"))
        frame = pl.DataFrame(rows, schema_overrides={"group": pl.Int64})
        if frame["votes"].sum() != counts["voters"] - counts["blank"] - counts["null"]:
            raise ValueError(f"02 {day}: list votes do not add up to valid votes")
        if frame["seats"].sum() != 35:
            raise ValueError(f"02 {day}: seats do not add up to 35")
        elections.append({"region": "02", "day": day, "last": last, "url": vda_url(number_), "size": 35, **counts})
        lists.extend({"region": "02", "day": day, **row} for row in rows)
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(elections).write_parquet(PARSED / "elections.parquet")
    pl.DataFrame(lists, schema_overrides={"group": pl.Int64}).write_parquet(PARSED / "lists.parquet")


def emit() -> None:
    elections = pl.read_parquet(PARSED / "elections.parquet")
    lists = pl.read_parquet(PARSED / "lists.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Regional council elections in Italy's special-statute regions from the regions' own results, by region",
            polity="IT",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        italy = cs.polity("IT")
        if italy in cs.minted:
            raise ValueError("publish it.interno before it.speciali")
        parties: dict[tuple[str, str], UUID] = {}

        def party_of(shown: str) -> UUID:
            label = PARTIES.get(shown.upper(), shown.upper())
            spec = EXISTING.get(label)
            if spec is not None:
                if spec not in parties:
                    parties[spec] = cs.key(*spec)
                    if parties[spec] in cs.minted:
                        raise ValueError(f"{spec} is not in the database")
                return parties[spec]
            if label in REGIONAL_PARTIES:
                full, abbreviation = REGIONAL_PARTIES[label]
                key, kind = ("it.party", slug(full)), "party"
            elif label in NEW:
                full, abbreviation, kind = NEW[label]
                key = ("it.party", slug(full))
            else:
                full, abbreviation, kind = cased(shown) if shown.isupper() else shown, None, "list_vehicle"
                key = ("it.storico.list", slug(label))
            if key not in parties:
                parties[key] = cs.key(*key)
                if parties[key] in cs.minted:
                    cs.add("party", id=parties[key], kind=kind, polity_id=italy)
                    if abbreviation is not None:
                        name(parties[key], abbreviation, "it", "abbreviation")
                    name(parties[key], full, "it")
            return parties[key]

        rows: list[dict[str, object]] = []

        def value(result_set: UUID, unit: UUID, option: UUID | None, measure: int, count: int | None) -> None:
            if count is not None:
                rows.append(
                    {
                        "result_set_id": str(result_set),
                        "unit_id": str(unit),
                        "option_id": None if option is None else str(option),
                        "measure": measure,
                        "value": count,
                    }
                )

        def result_set(key: str, election: UUID, contest: UUID | None, source: UUID) -> UUID:
            id = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=id,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            return id

        for code in sorted(elections["region"].unique()):
            info = REGIONS[code]
            source = cs.source(info.publisher, info.title)
            if source in cs.minted:
                cs.add(
                    "source", id=source, kind="official_body", publisher=info.publisher, title=info.title, url=info.home
                )
            region = cs.polity(info.iso)
            area = cs.area("it.regione", code)
            unit = cs.unit("it.regione", code)
            if area in cs.minted or unit in cs.minted:
                raise ValueError("publish it.istat before it.speciali")
            if region in cs.minted:
                cs.add("polity", id=region, kind="region", iso_code=info.iso)
                cs.add("slug", path=f"it/{info.slug}", entity_id=region)
                cs.add("polity_parent", polity_id=region, parent_id=italy)
                cs.add("polity_territory", polity_id=region, area_id=area)
                name(region, info.native, "it")
                if info.english != info.native:
                    name(region, info.english, "en", "english")
            body = cs.key(f"{SOURCE}.body", code)
            if body in cs.minted:
                cs.add("body", id=body, polity_id=region, kind="unicameral")
                name(body, info.council_en)
                name(body, info.council_it, "it", "native")
            held = elections.filter(pl.col("region") == code).sort("day")
            days = held["day"].to_list()
            for total in held.iter_rows(named=True):
                day = total["day"]
                cs.artifact(raw.cached(SOURCE, total["url"]), source)
                label = str(day.year) if sum(other.year == day.year for other in days) == 1 else f"{day:%B} {day.year}"
                election = cs.key(f"{SOURCE}.election", f"{code}:{day}")
                cs.add(
                    "election",
                    id=election,
                    polity_id=region,
                    kind="general",
                    date_from=day,
                    date_to=total["last"],
                    status="held",
                )
                name(election, f"{label} {info.english} regional election")
                name(election, f"Elezioni regionali in {info.native} del {day.year}", "it", "native")
                path = f"it/{info.slug}/{day}-general"
                cs.add("slug", path=path, entity_id=election)
                accounting = result_set(f"{code}:{day}:election", election, None, source)
                value(accounting, unit, None, REGISTERED, total["registered"])
                value(accounting, unit, None, VOTERS, total["voters"])
                value(accounting, unit, None, BLANK, total["blank"])
                value(accounting, unit, None, INVALID, total["null"])
                contest = cs.key(f"{SOURCE}.contest", f"{code}:{day}:list")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="list_allocation",
                    body_id=body,
                    district_area_id=area,
                    magnitude=total["size"],
                    default_result_kind="counted",
                )
                name(contest, f"{label} {info.english} regional election, council lists")
                cs.add("slug", path=f"{path}/consiglio", entity_id=contest)
                cs.add(
                    "note",
                    entity_id=contest,
                    kind="context",
                    body_md=" ".join([info.counted, info.allocated, *filter(None, [NOTES.get((code, day))])]),
                )
                list_set = result_set(f"{code}:{day}:list", election, contest, source)
                for row in (
                    lists.filter(pl.col("region") == code, pl.col("day") == day)
                    .sort("votes", "list", descending=[True, False])
                    .iter_rows(named=True)
                ):
                    option = cs.key(f"{SOURCE}.option", f"{code}:{day}:list:{row['list']}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        party_id=party_of(row["list"]),
                        ballot_label=row["list"],
                    )
                    value(list_set, unit, option, VOTES, row["votes"])
                    if row["seats"]:
                        cs.add("outcome", result_set_id=list_set, option_id=option, kind="won", seats=row["seats"])
        cs.frame(
            "result_value",
            pl.DataFrame(rows, schema_overrides={"value": pl.Int64}).with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.col("measure").cast(pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        cs.write()
