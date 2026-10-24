import gzip
import json
import re
from datetime import date
from pathlib import Path
from uuid import UUID, uuid7

import polars as pl

from ea import changeset, raw
from ea.changeset import Changeset
from ea.sources.tr import oveo, ysk, ysk_local
from ea.sources.tr.ysk import (
    ALLIANCES,
    ALLIANCES_2018,
    CANDIDATES,
    CANDIDATES_2014,
    CANDIDATES_2018,
    DAY_2011,
    DAY_2014,
    DAY_2015,
    DAY_2017,
    DAY_2018,
    DAY_JUNE_2015,
    DOMESTIC_2017,
    INDEPENDENTS,
    INDEPENDENTS_DOMESTIC,
    INDEPENDENTS_DOMESTIC_2011,
    INDEPENDENTS_DOMESTIC_2015,
    INDEPENDENTS_DOMESTIC_2018,
    INDEPENDENTS_DOMESTIC_JUNE_2015,
    ISTANBUL_CELLS,
    MARCH,
    PARTIES,
    PARTIES_2011,
    PARTIES_2015,
    PARTIES_JUNE_2015,
    PRESIDENT_2014_CELLS,
    PRESIDENT_2018_CELLS,
    PRESIDENT_CELLS,
    PROVINCES,
    RERUN,
    ROUND_DAYS,
    VALID,
    VOTES,
    title,
    upper,
)
from ea.store import DATA, connect

SOURCE = "tr.ysk_sandik"
VERSION = "14"
PARSED = DATA / "parsed" / SOURCE
SITE = "https://sonuc.ysk.gov.tr"
PUBLISHER = "Yüksek Seçim Kurulu (YSK)"
TITLE = "Election results by province, district, and neighborhood"
NOTE = (
    "Ballot-box results as published by YSK, obtained through a third-party mirror and regrouped by neighborhood, "
    "for each province, district, and neighborhood, and for prisons that report their own ballot boxes. The copy was "
    "retrieved on March 8, 2026. It covers votes cast in Turkey only, not abroad or at customs gates. It includes no "
    "counts of registered voters, ballots cast, or invalid votes. Votes for an alliance with no party marked are "
    "listed under the alliance, so party totals run below the official ones, which include each party's share of "
    "those votes. Independent candidates are combined into one total per contest. In Diyarbakır in 2011, the "
    "neighborhood results omit the votes of one candidate, whose election was annulled, while each place's valid "
    "votes still include them. The archive counts the difference in valid votes and gives it to no option. For 2011, "
    "the copy has no neighborhood results for 19 districts, most of them central districts that were later split or "
    "renamed, so those votes are missing here; the 2014 and 2019 local copies also leave some neighborhoods out, "
    "though their province and district totals are complete. Neighborhoods are matched to Oy ve Ötesi's "
    "neighborhood list by province and district. In the 2014 and 2019 local elections independent candidates are "
    "listed by name; in 2024 every party the copy does not list is reported together with the independents as one "
    "total. Towns whose ballot boxes are reported only as a whole are kept as one unit. For 2014, the places whose "
    "races YSK's March 30 totals leave out are left out here too: 12 mayoral races, and the provincial and "
    "municipal council races in central Ağrı and Yeşilyurt in Tokat. The copy's valid votes for the 2014 İstanbul "
    "mayoral race include 91 votes in Kadıköy that it assigns to no candidate; they are counted in valid votes but "
    "given to no option."
)
ABOLISHED = date(2014, 3, 30)
ABSORBED_NOTE = (
    "Law 6360 abolished this former town at the March 30, 2014 local elections. Its neighborhoods are linked to the "
    "neighborhood or village that carries the town's name, matched by name, not from the law's annexes."
)
CONTESTS = {
    "2014-cumhurbaskanlıgı-secimleri": (str(DAY_2014), f"{DAY_2014}:president"),
    "2011-milletvekilligi-genel-secimleri": (str(DAY_2011), f"{DAY_2011}:parliament"),
    "2015-haziran-milletvekilligi-genel-secimleri": (str(DAY_JUNE_2015), f"{DAY_JUNE_2015}:parliament"),
    "2015-kasım-milletvekilligi-genel-seçimleri": (str(DAY_2015), f"{DAY_2015}:parliament"),
    "2017-referandum-secimleri": (f"{DAY_2017}:referendum", f"{DAY_2017}:constitution"),
    "2018-cumhurbaskanlıgı-secimleri": (str(DAY_2018), f"{DAY_2018}:president"),
    "2018-milletvekilligi-genel-secimleri": (str(DAY_2018), f"{DAY_2018}:parliament"),
    "2019-yerel-buyuksehir-baskanlık-secimleri": (f"{MARCH}:istanbul-mayor", f"{MARCH}:istanbul-mayor"),
    "2019-buyuksehir-birinci-tur-belediye-baskanligi-secimleri": (f"{RERUN}:istanbul-mayor", f"{RERUN}:istanbul-mayor"),
    "2023-cumhurbaskanlıgı-secimleri": (str(ROUND_DAYS[1]), f"{ROUND_DAYS[1]}:president"),
    "2023-cumhurbaskanlıgı-ikinci-tur-secimleri": (str(ROUND_DAYS[2]), f"{ROUND_DAYS[2]}:president"),
    "2023-milletvekilligi-genel-secimleri": (str(ROUND_DAYS[1]), f"{ROUND_DAYS[1]}:parliament"),
}
LOCAL = [
    f"{year}-yerel-{office}-secimleri"
    for year in (2014, 2019, 2024)
    for office in ("buyuksehir-baskanlık", "belediye-baskanlık", "belediye-meclis", "il-genel-meclis-uyeligi")
]
URLS = {slug: f"{SOURCE}/{slug}" for slug in [*CONTESTS, *LOCAL]}
NEW_PARTIES = {"Demokratik Sol Parti": "DSP", "Bağımsız Türkiye Partisi": "BTP", "Demokrat Parti": "DP"}
LABELS = (
    {upper(short): full for full, short, *_ in PARTIES}
    | {upper(label): label for label, _ in ALLIANCES}
    | {short: full for full, short in NEW_PARTIES.items()}
    | {upper(short): full for full, short, *_ in PARTIES_2015}
    | {upper(short): full for full, short, *_ in PARTIES_JUNE_2015}
    | {upper(short): full for full, short, *_ in PARTIES_2011}
    | {
        "ANADOLU PARTİSİ": "Anadolu Partisi",
        "SAADET": "Saadet Partisi",
        "VATAN PARTİSİ": "Vatan Partisi",
        "YEŞİL SOL PARTİ": "Yeşiller ve Sol Gelecek Partisi",
        "YENİDEN REFAH": "Yeniden Refah Partisi",
        "ZAFER PARTİSİ": "Zafer Partisi",
        "GENÇPARTİ": "Genç Parti",
        "AB": "Adalet Birlik Partisi",
        "MİLLET": "Millet Partisi",
        "YP": "Yenilik Partisi",
        "HDP": "Halkların Demokratik Partisi",
        "HÜDA PAR": "Hür Dava Partisi",
        "EVET": "Evet",
        "HAYIR": "Hayır",
    }
)
DISTRICTS = {("20", "MERKEZ"): "MERKEZEFENDİ"}
UNATTRIBUTED = {
    ("2014-yerel-belediye-baskanlık-secimleri", "İSTANBUL"),
    ("2011-milletvekilligi-genel-secimleri", "DİYARBAKIR"),
}
ANKARA_2015 = {
    1: [
        "Akyurt",
        "Bala",
        "Çankaya",
        "Elmadağ",
        "Etimesgut",
        "Evren",
        "Gölbaşı",
        "Haymana",
        "Mamak",
        "Polatlı",
        "Sincan",
        "Şereflikoçhisar",
    ],
    2: [
        "Altındağ",
        "Ayaş",
        "Beypazarı",
        "Çamlıdere",
        "Çubuk",
        "Güdül",
        "Kahramankazan",
        "Kalecik",
        "Keçiören",
        "Kızılcahamam",
        "Nallıhan",
        "Pursaklar",
        "Yenimahalle",
    ],
}
OVO_DISTRICTS = {"19 MAYIS": "19-MAY"}
PRISON = r"CEZA|TUTUKEV|TİPİ\b|GÜVENLİKLİ"


def fetch() -> None:
    for url in URLS.values():
        raw.cached(SOURCE, url)


def parse() -> None:
    plates = {upper(label): f"{plate:02}" for plate, label in enumerate(PROVINCES, 1)}
    frames = []
    unattributed = []
    gaps = {}
    for slug, url in URLS.items():
        rows = []
        with gzip.open(raw.cached(SOURCE, url).path, "rt") as file:
            for line in file:
                data = json.loads(line)
                location = data["location"]
                results = data["results"]
                if gap := data["stats"]["validVoteSum"] - sum(result["votes"] for result in results):
                    if (slug, location["province"]) in UNATTRIBUTED:
                        if location["scope"] == "province":
                            gaps[slug] = gap
                        elif location["scope"] == "neighborhood":
                            unattributed.append(
                                (
                                    slug,
                                    plates[location["province"]],
                                    location["district"],
                                    location["neighborhood"],
                                    gap,
                                )
                            )
                    else:
                        raise ValueError(f"{slug} {location['sourceId']}: votes do not sum to valid votes")
                rows += [
                    (
                        slug,
                        location["scope"],
                        plates[location["province"]],
                        location["district"],
                        location["neighborhood"],
                        result["name"],
                        result["votes"],
                    )
                    for result in results
                ]
        frames.append(
            pl.DataFrame(
                rows, schema=["slug", "scope", "plate", "district", "neighborhood", "label", "votes"], orient="row"
            )
        )
    frame = pl.concat(frames).with_columns(
        pl.col("district", "neighborhood").str.replace_all(r"\s+", " ").str.strip_chars()
    )
    if frame.select("slug", "scope", "plate", "district", "neighborhood", "label").is_duplicated().any():
        raise ValueError("a label repeats within a place")
    places = ["slug", "scope", "plate", "district"]
    missing = (
        frame.filter(pl.col("scope") == "district")
        .select(places)
        .unique()
        .join(
            frame.filter(pl.col("scope") == "neighborhood")
            .select(places)
            .unique()
            .with_columns(scope=pl.lit("district")),
            on=places,
            how="anti",
        )
    )
    print(
        "2011 districts without neighborhoods:",
        sorted(missing.filter(pl.col("slug").str.starts_with("2011")).select("plate", "district").iter_rows()),
    )
    if (
        frame.filter(
            (pl.col("scope") == "province")
            & pl.col("slug").str.starts_with("2011")
            & ~pl.col("label").is_in(list(LABELS))
        )["votes"].sum()
        + gaps["2011-milletvekilligi-genel-secimleri"]
        != INDEPENDENTS_DOMESTIC_2011
    ):
        raise ValueError("2011 independent candidates do not add up to the gazette's total")
    for coarser, finer, keys, kept in (
        ("province", "district", ["slug", "plate", "label"], frame),
        (
            "district",
            "neighborhood",
            ["slug", "plate", "district", "label"],
            frame.join(missing, on=places, how="anti"),
        ),
    ):
        coarse, fine = (
            kept.filter(pl.col("scope") == scope).group_by(keys).agg(pl.col("votes").sum()).sort(keys)
            for scope in (coarser, finer)
        )
        if not coarse.equals(fine):
            raise ValueError(f"{finer} votes do not add up to {coarser} votes")
    PARSED.mkdir(parents=True, exist_ok=True)
    neighborhoods = frame.filter(pl.col("scope") == "neighborhood", pl.col("slug").is_in(list(CONTESTS))).drop("scope")
    neighborhoods.write_parquet(PARSED / "neighborhoods.parquet")
    pl.concat(
        [
            frame.filter(pl.col("scope") == "neighborhood"),
            frame.join(missing.filter(pl.col("slug").str.contains("yerel")), on=places, how="semi"),
        ]
    ).filter(pl.col("slug").is_in(LOCAL)).drop("scope").write_parquet(PARSED / "local.parquet")
    pl.DataFrame(
        unattributed, schema=["slug", "plate", "district", "neighborhood", "votes"], orient="row"
    ).with_columns(pl.col("district", "neighborhood").str.replace_all(r"\s+", " ").str.strip_chars()).write_parquet(
        PARSED / "unattributed.parquet"
    )
    print(
        neighborhoods.group_by("slug").agg(
            pl.col("votes").sum(), places=pl.struct("plate", "district", "neighborhood").n_unique()
        )
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "neighborhoods.parquet").filter(
        ~pl.col("slug").str.starts_with("2019") | (pl.col("plate") == "34")
    )
    ilce = pl.read_parquet(oveo.PARSED / "ilce.parquet")
    districts = {(plate, upper(name)): id for id, plate, name in ilce.iter_rows()}
    district_names = {id: name for id, _, name in ilce.iter_rows()}
    neighborhoods = {
        (district, upper(name)): id
        for id, district, name in pl.read_parquet(oveo.PARSED / "mahalle.parquet").iter_rows()
    }
    local = pl.read_parquet(ysk_local.PARSED / "municipal.parquet")
    slugs: dict[tuple[str, str, str | None], set[str]] = {}
    for plate, district, neighborhood, slug in (
        pl.concat(
            [
                frame.select("plate", "district", "neighborhood", "slug"),
                local.select("plate", "district", "neighborhood", "slug"),
            ]
        )
        .unique()
        .iter_rows()
    ):
        slugs.setdefault((plate, district, neighborhood), set()).add(slug)
    sorted_into = {
        f"{plate}:{name}": f"{plate}-{number}"
        for plate, groups in oveo.DISTRICTS.items()
        for number, names in groups.items()
        for name in names
    }
    sorted_2015 = {place: code for place, code in sorted_into.items() if not place.startswith("16:")} | {
        f"06:{name}": f"06-{number}" for number, names in ANKARA_2015.items() for name in names
    }
    sorted_2011 = sorted_2015 | {"06:Akyurt": "06-2", "06:Elmadağ": "06-2"}
    official: dict[str, int | None] = {
        f"{MARCH}:istanbul-mayor": None,
        f"{RERUN}:istanbul-mayor": next(
            value for option, measure, value, *_ in ISTANBUL_CELLS if option is None and measure == VALID
        ),
        **{f"{day}:president": PRESIDENT_CELLS[number]["domestic"][3][2] for number, day in ROUND_DAYS.items()},
        f"{DAY_2018}:president": PRESIDENT_2018_CELLS["domestic"][3][2],
        f"{DAY_2014}:president": PRESIDENT_2014_CELLS["domestic"][3][2],
        f"{DAY_2017}:constitution": DOMESTIC_2017[2],
        **{
            f"{day}:parliament:{code}": value
            for day, suffix in (
                (ROUND_DAYS[1], ""),
                (DAY_2018, f"_{DAY_2018}"),
                (DAY_2015, f"_{DAY_2015}"),
                (DAY_JUNE_2015, f"_{DAY_JUNE_2015}"),
                (DAY_2011, f"_{DAY_2011}"),
            )
            for code, value in pl.read_parquet(ysk.PARSED / f"district_values{suffix}.parquet")
            .filter(pl.col("option").is_null() & (pl.col("measure") == VALID))
            .select("code", "value")
            .iter_rows()
        },
        **{
            f"{ysk_local.DAYS[year]}:{ysk_local.OFFICES[office][0]}": value
            for year, office, value in pl.read_parquet(ysk_local.PARSED / "values.parquet")
            .filter(
                pl.col("year").is_in([2014, 2019, 2024]),
                pl.col("office").is_in(["il_genel", "belediye_meclisi"]),
                pl.col("unit") == "domestic",
                pl.col("column") == "valid",
            )
            .select("year", "office", "value")
            .iter_rows()
        },
    }
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Neighborhood results of Turkey's 2014 presidential election, its 2011, June 2015, and "
                "November 2015 parliamentary elections, its 2017 constitutional referendum, its 2018 and 2023 general elections, "
                "İstanbul's 2019 mayoral elections, and the 2014, 2019, and 2024 local elections, from YSK's ballot-box results"
            ),
            polity="TR",
        )

        def name(entity: UUID, text: str, kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang="tr", kind=kind, is_preferred=kind == "official")

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="aggregator",
            publisher=PUBLISHER,
            title=TITLE,
            url=SITE,
            license="No license stated.",
            notes=NOTE,
        )
        for url in URLS.values():
            cs.artifact(raw.cached(SOURCE, url), source)
        turkey = cs.polity("TR")
        cs.add("scheme", code="tr.cezaevi", polity_id=turkey, kind="precinct", rank=3, noun="prison")
        cs.add("scheme", code="tr.belde", polity_id=turkey, kind="administrative", rank=3, noun="town")

        events: dict[UUID, UUID] = dict(
            conn.execute(
                """select m.area_id, e.id from area_event e join area_event_member m on m.event_id = e.id
                   where e.kind = 'absorbed' and e.date = %s and m.role = 'to'""",
                [ABOLISHED],
            ).fetchall()
        )
        absorbed: dict[UUID, list[UUID]] = {}
        units: dict[str, str] = {}
        seats: dict[str, str] = {}
        seats_2015: dict[str, str] = {}
        seats_2011: dict[str, str] = {}
        added: list[str] = []
        rows: list[dict[str, object]] = []
        for plate, district, neighborhood in (
            pl.concat(
                [frame.select("plate", "district", "neighborhood"), local.select("plate", "district", "neighborhood")]
            )
            .unique()
            .sort("plate", "district", "neighborhood", nulls_last=False)
            .iter_rows()
        ):
            label = DISTRICTS.get((plate, district)) or (
                f"{upper(PROVINCES[int(plate) - 1])} MERKEZ" if district == "MERKEZ" else district.split(" - ")[0]
            )
            ilce_id = districts[(plate, OVO_DISTRICTS.get(label, label))]
            parent = cs.key("tr.oveo.ilce", str(ilce_id))
            if neighborhood is None:
                town = title(district.split(" - ")[1])
                unit = cs.unit("tr.belde", f"{plate}:{title(label)}:{town}")
                units[f"{plate}\x1f{district}\x1f"] = str(unit)
                rows.append(
                    {
                        "id": str(unit),
                        "polity_id": str(turkey),
                        "scheme_code": "tr.belde",
                        "code": f"{plate}:{title(label)}:{town}",
                        "parent_id": None,
                        "area_relation": "within",
                        "area_id": str(parent),
                        "kind": "aggregate",
                    }
                )
                name(unit, town)
                continue
            bare = [
                f"{neighborhood} {suffix}"
                for suffix in ("MAH.", "KÖYÜ")
                if not neighborhood.endswith((" MAH.", " KÖYÜ"))
                and (ilce_id, f"{neighborhood} {suffix}") in neighborhoods
            ]
            found = next(
                (
                    neighborhoods[(ilce_id, form)]
                    for form in (
                        neighborhood,
                        re.sub(r" KÖYÜ$", " MAH.", neighborhood),
                        re.sub(r"^[^-]+-", "", neighborhood),
                        *(bare if len(bare) == 1 else ()),
                    )
                    if (ilce_id, form) in neighborhoods
                    and (
                        form == neighborhood
                        or slugs.get((plate, district, form), set()).isdisjoint(slugs[(plate, district, neighborhood)])
                    )
                ),
                None,
            )
            prison = found is None and re.search(PRISON, neighborhood) is not None
            if found is not None:
                area = cs.key("tr.oveo.mahalle", str(found))
            elif prison:
                area = parent
            else:
                area = cs.key(f"{SOURCE}.mahalle", f"{ilce_id}:{neighborhood}")
                cs.add("area", id=area, scheme_code="tr.mahalle")
                cs.add("area_parent", area_id=area, parent_id=parent, valid_from=None)
                name(area, title(neighborhood))
                added.append(f"{plate} {label} {neighborhood}")
                town = neighborhood.split("-")[0]
                successors = [
                    neighborhoods[(ilce_id, f"{town} {suffix}")]
                    for suffix in ("MAH.", "KÖYÜ")
                    if (ilce_id, f"{town} {suffix}") in neighborhoods
                ]
                if (
                    "-" in neighborhood
                    and len(successors) == 1
                    and all(election.startswith("2011") for election in slugs[(plate, district, neighborhood)])
                ):
                    absorbed.setdefault(cs.key("tr.oveo.mahalle", str(successors[0])), []).append(area)
            if parent in cs.minted or (found is not None and area in cs.minted):
                raise ValueError("publish tr.oveo before tr.ysk_sandik")
            scheme = "tr.cezaevi" if prison else "tr.mahalle"
            code = f"{plate}:{title(label)}:{title(neighborhood)}"
            unit = cs.unit(scheme, code)
            place = f"{plate}\x1f{district}\x1f{neighborhood}"
            units[place] = str(unit)
            seats[place] = sorted_into.get(f"{plate}:{district_names[ilce_id]}", plate)
            seats_2015[place] = sorted_2015.get(f"{plate}:{district_names[ilce_id]}", plate)
            seats_2011[place] = sorted_2011.get(f"{plate}:{district_names[ilce_id]}", plate)
            rows.append(
                {
                    "id": str(unit),
                    "polity_id": str(turkey),
                    "scheme_code": scheme,
                    "code": code,
                    "parent_id": None,
                    "area_relation": "within" if prison else "equals",
                    "area_id": str(area),
                    "kind": "prison" if prison else "aggregate",
                }
            )
            name(unit, title(neighborhood))
        cs.frame("reporting_unit", pl.DataFrame(rows, schema_overrides={"parent_id": pl.String}).unique())
        for successor, former in absorbed.items():
            event = events.get(successor) or uuid7()
            cs.add("area_event", id=event, kind="absorbed", date=ABOLISHED, source_id=source, notes=ABSORBED_NOTE)
            cs.add("area_event_member", event_id=event, area_id=successor, role="to")
            for area in former:
                cs.add("area_event_member", event_id=event, area_id=area, role="from")
        print(f"{len(units)} units, {len(added)} neighborhoods not in Oy ve Ötesi's list: {added}")

        place = pl.concat_str("plate", "district", pl.col("neighborhood").fill_null(""), separator="\x1f")
        parliament = pl.col("slug").str.contains("milletvekilligi")
        frame = frame.with_columns(
            unit=place.replace_strict(units),
            contest=pl.col("slug").replace_strict({slug: contest for slug, (_, contest) in CONTESTS.items()})
            + pl.when(parliament)
            .then(
                ":"
                + pl.when(pl.col("slug").str.starts_with("2015"))
                .then(place.replace_strict(seats_2015))
                .when(pl.col("slug").str.starts_with("2011"))
                .then(place.replace_strict(seats_2011))
                .otherwise(place.replace_strict(seats))
            )
            .otherwise(pl.lit("")),
            option=pl.when(pl.col("slug").str.contains("cumhurbaskan"))
            .then(
                pl.col("label").replace_strict(
                    {upper(label): label for label, _ in CANDIDATES + CANDIDATES_2018 + CANDIDATES_2014}, default=None
                )
            )
            .otherwise(
                pl.col("label").replace_strict(
                    LABELS, default=pl.when(parliament).then(pl.lit(INDEPENDENTS)).otherwise(pl.lit("others"))
                )
            ),
        )
        if frame["option"].null_count():
            raise ValueError(f"unknown candidates: {frame.filter(pl.col('option').is_null())['label'].unique()}")
        votes = frame.group_by("contest", "unit", "option").agg(pl.col("votes").sum())
        independents = dict(
            votes.filter(pl.col("option") == INDEPENDENTS)
            .group_by(pl.col("contest").str.slice(0, 10))
            .agg(pl.col("votes").sum())
            .iter_rows()
        )
        rerun = votes.filter((pl.col("contest") == f"{RERUN}:istanbul-mayor") & (pl.col("option") == "others"))
        if independents != {
            str(ROUND_DAYS[1]): INDEPENDENTS_DOMESTIC,
            str(DAY_2018): INDEPENDENTS_DOMESTIC_2018,
            str(DAY_2015): INDEPENDENTS_DOMESTIC_2015,
            str(DAY_JUNE_2015): INDEPENDENTS_DOMESTIC_JUNE_2015,
            str(DAY_2011): independents[str(DAY_2011)],
        } or (rerun["votes"].sum() != ISTANBUL_CELLS[-1][2]):
            raise ValueError("independent candidates do not add up to the gazette's totals")
        print(
            "March independents:",
            frame.filter((pl.col("contest") == f"{MARCH}:istanbul-mayor") & (pl.col("option") == "others"))["label"]
            .unique()
            .sort()
            .to_list(),
        )

        parties: dict[str, UUID] = {}
        alliances = {
            f"{day}:{label}": cs.key("tr.alliance", f"{day}:{changeset.slug(label)}")
            for day, standing in ((ROUND_DAYS[1], ALLIANCES), (DAY_2018, ALLIANCES_2018))
            for label, _ in standing
        }
        options: dict[str, str] = {}
        for contest_key, option in votes.select("contest", "option").unique().sort("contest", "option").iter_rows():
            id = cs.key("tr.option", f"{contest_key}:{changeset.slug(option)}")
            options[f"{contest_key}\x1f{option}"] = str(id)
            if id not in cs.minted:
                continue
            contest = cs.key("tr.contest", contest_key)
            if contest in cs.minted:
                raise ValueError("publish tr.ysk before tr.ysk_sandik")
            if contest_key == f"{MARCH}:istanbul-mayor" and option != "others":
                parties[option] = cs.key("tr.party", changeset.slug(option))
                if parties[option] in cs.minted:
                    if option not in NEW_PARTIES:
                        raise ValueError(f"publish tr.ysk before tr.ysk_sandik: no party {option}")
                    cs.add("party", id=parties[option], kind="party", polity_id=turkey)
                    name(parties[option], option)
                    name(parties[option], NEW_PARTIES[option], kind="abbreviation")
                cs.add(
                    "contest_option",
                    id=id,
                    contest_id=contest,
                    kind="candidate",
                    party_id=parties[option],
                    ballot_label=option,
                )
            elif contest_key == f"{MARCH}:istanbul-mayor":
                cs.add(
                    "contest_option", id=id, contest_id=contest, kind="others", party_id=None, ballot_label=INDEPENDENTS
                )
            elif (alliance := alliances.get(f"{contest_key[:10]}:{option}")) is not None and alliance not in cs.minted:
                cs.add(
                    "contest_option",
                    id=id,
                    contest_id=contest,
                    kind="legend",
                    party_id=None,
                    alliance_id=alliance,
                    ballot_label=option,
                )
            else:
                raise ValueError(f"{contest_key}: no option for {option}")

        local = local.with_columns(unit=place.replace_strict(units))
        for contest_key, option in local.select("contest", "option").unique().iter_rows():
            options[f"{contest_key}\x1f{option}"] = str(cs.key("tr.option", option))
        votes = pl.concat([votes, local.group_by("contest", "unit", "option").agg(pl.col("votes").sum())])
        unattributed = pl.read_parquet(PARSED / "unattributed.parquet").join(
            pl.concat(
                [
                    frame.select("slug", "plate", "district", "neighborhood", "contest", "unit"),
                    local.select("slug", "plate", "district", "neighborhood", "contest", "unit"),
                ]
            ).unique(),
            on=["slug", "plate", "district", "neighborhood"],
        )
        valid = (
            pl.concat(
                [frame, local.select(frame.columns), unattributed.select("contest", "unit", "votes")],
                how="diagonal_relaxed",
            )
            .group_by("contest", "unit")
            .agg(pl.col("votes").sum())
        )
        elections = {
            contest_key: contest_key if contest_key.endswith("-mayor") else f"{contest_key[:10]}:local"
            for contest_key in local["contest"].unique()
        } | {contest_key: election for election, contest_key in CONTESTS.values()}
        result_sets: dict[str, str] = {}
        for contest_key, counted in valid.group_by("contest").agg(pl.col("votes").sum()).sort("contest").iter_rows():
            election_key = elections.get(contest_key) or next(
                election for election, prefix in CONTESTS.values() if contest_key.startswith(prefix)
            )
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            denominator = official.get(contest_key)
            annulled = contest_key.split(":")[1].removesuffix("-mayor") in {
                str(MARCH): ysk_local.ANNULLED[2019] | {"istanbul"},
                str(ysk_local.DAYS[2024]): ysk_local.ANNULLED[2024],
            }.get(contest_key[:10], set())
            cs.add(
                "result_set",
                id=result_set,
                election_id=cs.key("tr.election", election_key),
                contest_id=cs.key("tr.contest", contest_key),
                stage="alternative",
                kind="counted",
                status="annulled" if annulled else "active",
                source_id=source,
                is_default=False,
                completeness=None if denominator is None else round(counted / denominator, 4),
            )
            if result_set in cs.minted:
                result_sets[contest_key] = str(result_set)
                if contest_key in unattributed["contest"]:
                    gap = unattributed.filter(pl.col("contest") == contest_key)["votes"].sum()
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md=(
                            f"The neighborhood results give this contest {gap:,} valid votes that no candidate holds. "
                            "They are counted in valid votes here but not given to any option."
                        ),
                    )
        values = pl.concat(
            [
                votes.select(
                    "contest",
                    "unit",
                    option_id=pl.concat_str("contest", "option", separator="\x1f").replace_strict(options),
                    measure=pl.lit(VOTES, pl.Int16),
                    value="votes",
                ),
                valid.select(
                    "contest", "unit", option_id=pl.lit(None, pl.String), measure=pl.lit(VALID, pl.Int16), value="votes"
                ),
            ]
        ).filter(pl.col("contest").is_in(list(result_sets)))
        cs.frame(
            "result_value",
            values.select(
                result_set_id=pl.col("contest").replace_strict(result_sets),
                unit_id="unit",
                option_id="option_id",
                channel=pl.lit(0, pl.Int16),
                measure="measure",
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
                value="value",
            ),
        )
        return cs.write()
