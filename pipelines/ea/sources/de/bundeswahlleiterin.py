import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "de.bundeswahlleiterin"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
BWL = "https://www.bundeswahlleiterin.de"
OPENDATA = f"{BWL}/bundestagswahlen/2025/ergebnisse/opendata/btw25"
EW24 = f"{BWL}/europawahlen/2024/ergebnisse/opendata/ew24"
FILES = {
    "results": f"{OPENDATA}/csv/kerg2.csv",
    "elected": f"{OPENDATA}/daten/gewaehlte_02.xml",
    "parties": f"{BWL}/dam/jcr/925d6e98-3616-465a-835a-cec1ea73abc2/btw25_parteien.csv",
    "districts": f"{BWL}/dam/jcr/e79a7bd3-0607-4e87-9752-8e601e299e00/btw25_wbz.zip",
    "shapes": f"{BWL}/dam/jcr/556bec9c-be80-4818-a368-fe6596f15f08/btw25_geometrie_wahlkreise_shp_geo.zip",
    "ep_results": f"{EW24}/csv/kerg2.csv",
    "ep_elected": f"{EW24}/daten/gewaehlte_02.xml",
    "ep_parties": f"{BWL}/dam/jcr/2a99cb27-da04-4a51-977a-4ef408e3479f/ew24_parteien_utf8.csv",
}
ELECTION = date(2025, 2, 23)
CLOSED = date(2025, 2, 24)
CONSTITUTED = date(2025, 3, 25)
EUROPEAN = date(2024, 6, 9)
EUROPEAN_SEATS = 96
FOUNDING_MEMBER = date(1958, 1, 1)
UNABBREVIATED = {"Partei für schulmedizinische Verjüngungsforschung": "Verjüngungsforschung"}
LAENDER = {
    "01": ("SH", "Schleswig-Holstein", None),
    "02": ("HH", "Hamburg", None),
    "03": ("NI", "Niedersachsen", "Lower Saxony"),
    "04": ("HB", "Bremen", None),
    "05": ("NW", "Nordrhein-Westfalen", "North Rhine-Westphalia"),
    "06": ("HE", "Hessen", "Hesse"),
    "07": ("RP", "Rheinland-Pfalz", "Rhineland-Palatinate"),
    "08": ("BW", "Baden-Württemberg", None),
    "09": ("BY", "Bayern", "Bavaria"),
    "10": ("SL", "Saarland", None),
    "11": ("BE", "Berlin", None),
    "12": ("BB", "Brandenburg", None),
    "13": ("MV", "Mecklenburg-Vorpommern", "Mecklenburg-Western Pomerania"),
    "14": ("SN", "Sachsen", "Saxony"),
    "15": ("ST", "Sachsen-Anhalt", "Saxony-Anhalt"),
    "16": ("TH", "Thüringen", "Thuringia"),
}
CITY_STATES = {"02": "Hamburg, Freie und Hansestadt", "11": "Berlin, Stadt"}
SEATS = {"CDU": 164, "CSU": 44, "AfD": 152, "SPD": 120, "GRÜNE": 85, "Die Linke": 64, "SSW": 1}
UNSEATED = 23
EXEMPT = "SSW"
COLORS = {
    "CDU": "#151518",
    "CSU": "#1f7cfe",
    "SPD": "#e3000f",
    "AfD": "#009ee0",
    "GRÜNE": "#46962b",
    "Die Linke": "#be3075",
    "FDP": "#ffed00",
    "BSW": "#9102b9",
    "SSW": "#003c8f",
    "FREIE WÄHLER": "#f7a800",
}
UNALLOCATED = 32
ACCOUNTING = {"Wahlberechtigte": 2, "Wählende": 14, "Gültige": 4, "Ungültige": 5}
KINDS = {
    "0": ("standard", 1, 0, "Wahlbezirk"),
    "5": ("postal_district", 3, 0, "Briefwahlbezirk"),
    "6": ("mobile", 6, 0, "Sonderwahlbezirk"),
    "8": ("aggregate", 0, UNALLOCATED, "Wahlbezirk"),
}
BALLOTS = {"Erststimmen": 1, "Zweitstimmen": 2}
ANNOTATION = re.compile(r"\s*\((Teil|Wkr\. [^)]*|in .+ enthalten|einschl\. .+)\)$")
THRESHOLD = {"percent": 5, "level": "party", "scope": "national", "constituencies": 3}
RESULTS_NOTE = (
    "Unsuccessful candidates may have their personal data withdrawn after the election (§ 86 (3) BWO), so only the "
    "630 elected members are named. Every other constituency candidate appears under their party or, for "
    "independents, their surname."
)
DISTRICTS_NOTE = (
    "A polling district with fewer than 30 voters hands its ballot box to another district in the same constituency, "
    "which counts both together (§ 68 (2) BWO). The receiving district reports the combined result. Municipalities "
    "did not flag every special polling district (hospitals, care homes, and similar), so that kind is incomplete. "
    "Party columns cover every list. First votes for candidates outside the listed parties are combined as others."
)
LISTS_METHODOLOGY = (
    "Since the 2023 reform, the Bundestag has 630 seats. They are divided by the Sainte-Laguë/Schepers method on "
    "second votes nationwide, among parties that won at least 5 percent of second votes or three constituencies. "
    "Parties of national minorities, such as the SSW, are exempt from both conditions. Each party's seats are then "
    "divided among its Land lists by the same method."
)
CONSTITUENCIES_METHODOLOGY = (
    "The candidate with the most first votes wins the constituency, but takes a seat only if the party's second-vote "
    "seats in that Land cover it. A party's winners are seated in order of their first-vote share until its seats in "
    "the Land run out. Any remaining seats go to its Land list in list order. Independent winners always take their "
    "seat. The Federal Constitutional Court (2 BvF 1/23, 30 July 2024) kept the three-constituency alternative to the "
    "5 percent threshold."
)
UNSEATED_NOTE = "Won the constituency but was not seated: the party's second-vote seats in this Land did not cover it."
DUPLICATE = (
    "The source lists two postal districts in Halver numbered 099. They are kept as separate units. This one carries "
    "the suffix ~2."
)
UNASSIGNED = "Holds registered voters that the source does not assign to a polling district."
EUROPEAN_METHODOLOGY = (
    "Germany's 96 seats are divided among parties nationwide using the Sainte-Laguë/Schepers method. There is no "
    "threshold: the Federal Constitutional Court struck down the 5 percent threshold in 2011 and the 3 percent "
    "threshold in 2014. Each party runs either one federal list or a separate list in each Land. A party with Land "
    "lists divides its seats among them using the same method, based on its votes in each Land. Voters cast one "
    "vote, for a party's list."
)


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix="." + url.rsplit(".", 1)[1])


def parse() -> None:
    results = pl.read_csv(raw.cached(SOURCE, FILES["results"]).path, separator=";", skip_rows=9, infer_schema=False)
    if results.select("Wahlart", "Wahltag").unique().rows() != [("BT", f"{ELECTION:%d.%m.%Y}")]:
        raise ValueError("kerg2.csv is not the 2025 Bundestag election")
    results = results.filter(pl.col("Anzahl").is_not_null()).select(
        area="Gebietsart",
        code="Gebietsnummer",
        label="Gebietsname",
        parent="UegGebietsnummer",
        group="Gruppenname",
        kind="Gruppenart",
        ballot=pl.col("Stimme").cast(pl.Int16).fill_null(0),
        value=pl.col("Anzahl").cast(pl.Int64),
        seated="Gewählt",
    )
    unknown = set(results.filter(pl.col("kind") == "System-Gruppe")["group"]) - set(ACCOUNTING)
    if unknown:
        raise ValueError(f"unknown accounting groups: {unknown}")
    by_constituency = results.filter(pl.col("area") == "Wahlkreis").with_columns(
        wk=pl.col("code").str.zfill(3), land=pl.col("parent").str.zfill(2)
    )
    by_land = results.filter(pl.col("area") == "Land").with_columns(land=pl.col("code").str.zfill(2))
    keys = ["group", "ballot"]
    for label, coarse, fine, by in (
        ("Land", by_land, by_constituency, ["land"]),
        ("Bund", results.filter(pl.col("area") == "Bund"), by_land, []),
    ):
        summed = (
            fine.with_columns(
                group=pl.when(pl.col("kind") == "Einzelbewerber/Wählergruppe")
                .then(pl.col("kind"))
                .otherwise(pl.col("group"))
            )
            .group_by(*by, *keys)
            .agg(pl.col("value").sum().alias("sum"))
        )
        short = coarse.join(summed, on=[*by, *keys], how="full", coalesce=True).filter(
            pl.col("value").ne_missing(pl.col("sum"))
        )
        if short.height:
            raise ValueError(f"{label} totals differ from the sum of their parts: {short.head(5)}")
    constituencies = by_constituency.select("wk", "land", name="label").unique().sort("wk")
    if constituencies.height != 299 or constituencies["wk"].is_duplicated().any():
        raise ValueError(f"expected 299 constituencies, got {constituencies.height}")
    official = by_constituency.select(
        "wk",
        "land",
        "ballot",
        group=pl.when(pl.col("kind") == "System-Gruppe").then(None).otherwise(pl.col("group")),
        measure=pl.col("group").replace_strict(ACCOUNTING, default=1, return_dtype=pl.Int16),
        value="value",
    )

    candidates = (
        by_constituency.filter((pl.col("ballot") == 1) & (pl.col("kind") != "System-Gruppe"))
        .select("wk", "group", "seated", independent=pl.col("kind") != "Partei", votes="value")
        .with_columns(
            top=pl.col("votes") == pl.col("votes").max().over("wk"),
            ties=(pl.col("votes") == pl.col("votes").max().over("wk")).sum().over("wk"),
        )
    )
    winners = candidates.filter(pl.col("top"))
    problems = {
        "ties for first place": winners.filter(pl.col("ties") > 1),
        "seated winners who did not top the count": winners.filter(
            (pl.col("seated") != "–") & (pl.col("seated") != pl.col("group"))
        ),
    }
    failures = [f"{label}: {frame.head(3)}" for label, frame in problems.items() if frame.height]
    unseated = winners.filter(pl.col("seated") == "–").height
    if unseated != UNSEATED:
        failures.append(f"expected {UNSEATED} unseated constituency winners, got {unseated}")
    if failures:
        raise ValueError("\n".join(failures))
    candidates = candidates.with_columns(
        outcome=pl.when(~pl.col("top"))
        .then(None)
        .when(pl.col("seated") == "–")
        .then(pl.lit("won"))
        .otherwise(pl.lit("elected"))
    ).select("wk", "group", "independent", "outcome")
    lists = by_land.filter((pl.col("ballot") == 2) & (pl.col("kind") == "Partei")).select("land", "group")

    parties = pl.read_csv(
        raw.cached(SOURCE, FILES["parties"]).path, separator=";", comment_prefix="#", infer_schema=False
    ).filter(pl.col("Gruppenart_XML") == "PARTEI")
    short_names = dict(parties.select("Gruppenschluessel", "GruppennameKurz").iter_rows())
    missing = (set(lists["group"]) | set(candidates.filter(~pl.col("independent"))["group"])) - set(
        parties["GruppennameKurz"]
    )
    if missing:
        raise ValueError(f"parties missing from the party list: {missing}")

    with zipfile.ZipFile(raw.cached(SOURCE, FILES["districts"]).path) as archive:
        districts = pl.read_csv(
            archive.read("btw25_wbz_ergebnisse.csv"), separator=";", skip_rows=4, infer_schema=False
        )
        leitband = pl.read_csv(archive.read("btw25_wbz_leitband.csv"), separator=";", skip_rows=4, infer_schema=False)
    columns = {
        "Wahlberechtigte (A)": (0, None, 2),
        "Wählende (B)": (0, None, 14),
        **{
            column: (BALLOTS[ballot], None if group in ("Gültige", "Ungültige") else group, ACCOUNTING.get(group, 1))
            for column in districts.columns
            if " - " in column
            for group, ballot in [column.rsplit(" - ", 1)]
        },
    }
    districts = districts.rename(
        {
            "Wahlkreis": "wk",
            "Land": "land",
            "Regierungsbezirk": "rb",
            "Kreis": "kreis",
            "Verbandsgemeinde": "vg",
            "Gemeinde": "gemeinde",
            "Kennziffer Urnenwahlbezirke nach § 68 BWO": "handover",
            "Gemeindename": "municipality",
            "Wahlbezirk": "number",
            "Bezirksart": "kind",
            "Ungekürzte Wahlbezirksbezeichnung": "full_number",
            "Bezeichnung des Wahlbezirkes gemäß Anlage 30 zur BWO": "label",
        }
    ).with_columns(pl.col(list(columns)).cast(pl.Int64))
    unknown = set(districts["kind"]) - set(KINDS)
    if unknown:
        raise ValueError(f"unknown district kinds: {unknown}")
    districts = districts.with_columns(
        district=pl.col("land") + pl.col("rb") + pl.col("kreis"),
        city=pl.col("land").is_in(list(CITY_STATES)),
        joint=pl.col("gemeinde").str.starts_with("9"),
    ).with_columns(
        code=pl.format(
            "{}:{}{}{}:{}:{}", "wk", "district", "vg", "gemeinde", "kind", pl.col("number").str.strip_chars()
        ),
        area_scheme=pl.when(pl.col("city"))
        .then(pl.lit("de.bezirk"))
        .when(pl.col("joint"))
        .then(pl.lit("de.kreis"))
        .otherwise(pl.lit("de.gemeinde")),
        area_code=pl.when(pl.col("city"))
        .then(pl.col("land") + "0" + pl.col("kreis"))
        .when(pl.col("joint"))
        .then(pl.col("district"))
        .otherwise(pl.col("district") + pl.col("gemeinde")),
    )
    if districts.filter(pl.col("joint") & (pl.col("kind") != "5")).height:
        raise ValueError("joint postal districts that are not postal districts")
    districts = districts.with_columns(repeat=pl.int_range(pl.len()).over("code")).with_columns(
        code=pl.when(pl.col("repeat") > 0)
        .then(pl.col("code") + "~" + (pl.col("repeat") + 1).cast(pl.String))
        .otherwise(pl.col("code"))
    )
    if districts.filter(pl.col("repeat") > 0)["code"].to_list() != ["148:059620012012:5:099~2"]:
        raise ValueError(f"unexpected duplicate districts: {districts.filter(pl.col('repeat') > 0)['code']}")

    merged = districts.filter(pl.col("handover") != "0000")
    receivers = merged.filter(pl.col("Wählende (B)") > 0)
    if receivers["handover"].is_duplicated().any() or set(receivers["handover"]) != set(merged["handover"]):
        raise ValueError("§ 68 groups without exactly one receiving district")
    givers = merged.join(receivers.select("handover", counted_with="code"), on="handover").filter(
        pl.col("code") != pl.col("counted_with")
    )
    if givers.select(pl.sum_horizontal(list(columns))).to_series().sum():
        raise ValueError("districts counted with another still hold values")
    districts = districts.join(givers.select("code", "counted_with"), on="code", how="left")

    values = (
        districts.filter(pl.col("counted_with").is_null())
        .unpivot(on=list(columns), index=["code", "wk", "land", "kind"], variable_name="column")
        .with_columns(
            ballot=pl.col("column").replace_strict({c: b for c, (b, _, _) in columns.items()}, return_dtype=pl.Int16),
            group=pl.col("column").replace_strict({c: g for c, (_, g, _) in columns.items()}, return_dtype=pl.String),
            measure=pl.col("column").replace_strict({c: m for c, (_, _, m) in columns.items()}, return_dtype=pl.Int16),
            channel=pl.col("kind").replace_strict({k: c for k, (_, c, _, _) in KINDS.items()}, return_dtype=pl.Int16),
            flags=pl.col("kind").replace_strict({k: f for k, (_, _, f, _) in KINDS.items()}, return_dtype=pl.Int16),
        )
        .drop("column")
    )
    if values.filter((pl.col("kind") == "5") & (pl.col("measure") == 2) & (pl.col("value") != 0)).height:
        raise ValueError("postal districts with registered voters")
    values = values.filter(~((pl.col("kind") == "5") & (pl.col("measure") == 2)))
    unlisted = official.filter(pl.col("group").is_not_null()).join(
        pl.DataFrame(
            [(ballot, group) for ballot, group, _ in columns.values() if group is not None],
            schema={"ballot": pl.Int16, "group": pl.String},
            orient="row",
        ),
        on=["ballot", "group"],
        how="anti",
    )
    if unlisted.filter(pl.col("ballot") == 2).height:
        raise ValueError(f"second votes for a party without a column in the district file: {unlisted.head(3)}")
    expected = (
        official.join(
            unlisted.select("wk", "ballot", "group", others=pl.lit(True)), on=["wk", "ballot", "group"], how="left"
        )
        .with_columns(group=pl.when(pl.col("others")).then(pl.lit("Übrige")).otherwise(pl.col("group")))
        .drop("others")
    )
    keys = ["wk", "ballot", "group", "measure"]
    compared = (
        expected.group_by(keys)
        .agg(pl.col("value").sum())
        .join(
            values.group_by(keys).agg(pl.col("value").sum().alias("districts")),
            on=keys,
            how="full",
            coalesce=True,
            nulls_equal=True,
        )
        .filter(pl.col("value").fill_null(0) != pl.col("districts").fill_null(0))
    )
    if compared.height:
        raise ValueError(f"polling districts do not add up to the constituency results: {compared.head(5)}")
    others = expected.filter(pl.col("group") == "Übrige").select("wk").unique()
    options = pl.concat(
        [
            candidates.select("wk", "group", ballot=pl.lit(1, pl.Int16)),
            others.select("wk", group=pl.lit("Übrige"), ballot=pl.lit(1, pl.Int16)),
        ]
    )
    stray = values.filter(pl.col("group").is_not_null()).join(
        pl.concat(
            [
                options.select("ballot", "wk", "group"),
                constituencies.join(lists, on="land").select("wk", "group", ballot=pl.lit(2, pl.Int16)),
            ],
            how="diagonal",
        ),
        on=["ballot", "wk", "group"],
        how="anti",
    )
    if stray["value"].sum():
        raise ValueError(f"district votes for options that did not stand: {stray.filter(pl.col('value') > 0)}")
    values = values.join(stray.select("code", "ballot", "group"), on=["code", "ballot", "group"], how="anti")

    text = "Gemeinde-/Verbands-/Kreis-/Regierungsbezirks-/Land-/Wahlkreistext"
    leitband = leitband.with_columns(
        district=pl.col("Land") + pl.col("Regierungsbezirk") + pl.col("Kreis"),
        label=pl.col(text).str.replace(ANNOTATION.pattern, ""),
    )
    kreis_names = leitband.filter(pl.col("Satzart") == "40").select("district", "label").unique()
    gemeinde_names = (
        leitband.filter((pl.col("Satzart") == "60") & ~pl.col("Gemeinde").str.starts_with("9"))
        .select(code=pl.col("district") + pl.col("Gemeinde"), name="label")
        .unique()
    )
    located = districts.filter(~pl.col("city"))
    kreise = located.select("land", "district").unique().join(kreis_names, on="district", how="left")
    boroughs = (
        districts.filter(pl.col("city"))
        .select("land", "district", "area_code")
        .unique()
        .join(kreis_names, on="district", how="left")
        .with_columns(label=pl.col("label").str.replace(r"^.*, Bezirk ", "").str.replace(r", Berlin-(Ost|West)$", ""))
        .unique(["area_code", "label"])
    )
    gemeinden = (
        located.filter(~pl.col("joint"))
        .select("district", "area_code", fallback=pl.col("municipality").str.replace(ANNOTATION.pattern, ""))
        .unique("area_code")
        .join(gemeinde_names, left_on="area_code", right_on="code", how="left")
    )
    if (
        kreise["label"].null_count()
        or kreise["district"].is_duplicated().any()
        or boroughs["label"].null_count()
        or boroughs["area_code"].is_duplicated().any()
        or gemeinden["area_code"].is_duplicated().any()
    ):
        raise ValueError("Kreise or boroughs without one name, or Gemeinden with two")
    areas = pl.concat(
        [
            pl.DataFrame(
                {
                    "scheme": ["de.kreis"] * len(CITY_STATES),
                    "code": [f"{land}000" for land in CITY_STATES],
                    "parent_scheme": ["de.land"] * len(CITY_STATES),
                    "parent_code": list(CITY_STATES),
                    "name": list(CITY_STATES.values()),
                }
            ),
            kreise.select(
                scheme=pl.lit("de.kreis"),
                code="district",
                parent_scheme=pl.lit("de.land"),
                parent_code="land",
                name="label",
            ),
            boroughs.select(
                scheme=pl.lit("de.bezirk"),
                code="area_code",
                parent_scheme=pl.lit("de.kreis"),
                parent_code=pl.col("land") + "000",
                name="label",
            ),
            gemeinden.select(
                scheme=pl.lit("de.gemeinde"),
                code="area_code",
                parent_scheme=pl.lit("de.kreis"),
                parent_code="district",
                name=pl.coalesce("name", "fallback"),
            ),
        ]
    ).sort("scheme", "code")
    units = (
        districts.join(
            areas.filter(pl.col("scheme") == "de.gemeinde").select(area_code="code", place="name"),
            on="area_code",
            how="left",
        )
        .select(
            "code",
            "wk",
            "kind",
            "area_scheme",
            "area_code",
            "counted_with",
            name=pl.format(
                "{}, {} {}{}",
                pl.coalesce("place", pl.col("municipality").str.replace(ANNOTATION.pattern, "")),
                pl.col("kind").replace_strict({k: noun for k, (_, _, _, noun) in KINDS.items()}),
                pl.col("full_number").str.strip_chars(),
                pl.when(pl.col("label") == "–").then(pl.lit("")).otherwise(pl.format(" ({})", "label")),
            ),
        )
        .sort("code")
    )

    with zipfile.ZipFile(raw.cached(SOURCE, FILES["shapes"]).path) as archive:
        parts = {
            member.rsplit(".", 1)[1].lower(): io.BytesIO(archive.read(member))
            for member in archive.namelist()
            if member.rsplit(".", 1)[1].lower() in ("shp", "shx", "dbf")
        }
    reader = shapefile.Reader(shp=parts["shp"], shx=parts["shx"], dbf=parts["dbf"], encoding="utf-8")
    shapes = [
        (str(record["WKR_NR"]).zfill(3), json.dumps(shape.__geo_interface__))
        for record, shape in zip(reader.records(), reader.shapes(), strict=True)
        if record is not None and shape is not None
    ]
    if sorted(code for code, _ in shapes) != constituencies["wk"].to_list():
        raise ValueError("constituency shapes do not match the constituencies")
    land_of = dict(constituencies.select("wk", "land").iter_rows())
    with connect() as conn:
        geometry = pl.DataFrame(
            conn.execute(
                """with wk as (
                     select code, land, st_multi(st_collectionextract(st_makevalid(
                              st_setsrid(st_geomfromgeojson(geojson), 4326)), 3)) as geom
                     from unnest(%s::text[], %s::text[], %s::text[]) as s(code, land, geojson)
                   ),
                   united as (
                     select 'de.wahlkreis' as scheme, code, geom from wk
                     union all select 'de.land', land, st_multi(st_union(geom)) from wk group by land
                     union all select 'de.nation', 'DE', st_multi(st_union(geom)) from wk
                   )
                   select scheme, code, encode(st_asewkb(geom), 'hex'), encode(st_asewkb(st_pointonsurface(geom)), 'hex')
                   from united order by scheme, code""",
                [
                    [code for code, _ in shapes],
                    [land_of[code] for code, _ in shapes],
                    [geojson for _, geojson in shapes],
                ],
            ).fetchall(),
            schema=["scheme", "code", "geom", "label_point"],
            orient="row",
        )

    european_parties = (
        pl.read_csv(raw.cached(SOURCE, FILES["ep_parties"]).path, separator=";", comment_prefix="#", infer_schema=False)
        .filter(pl.col("Gruppenart_XML") == "PARTEI")
        .select(
            "Gruppenschluessel",
            short=pl.coalesce(
                "Gruppenname_kurz", pl.col("Gruppenname_lang").replace_strict(UNABBREVIATED, default=None)
            ),
            name="Gruppenname_lang",
        )
    )
    if european_parties["short"].null_count() or european_parties["short"].is_duplicated().any():
        raise ValueError("Europawahl parties without one short name")

    members: list[dict[str, object]] = []
    for file, groups in (
        ("elected", short_names),
        ("ep_elected", dict(european_parties.select("Gruppenschluessel", "short").iter_rows())),
    ):
        for candidate in ET.parse(raw.cached(SOURCE, FILES[file]).path).getroot().iter("Kandidat"):
            person = candidate.find("Personendaten")
            data = candidate.find("Wahldaten")
            if person is None or data is None:
                raise ValueError("elected member without personal or election data")
            listed_as = person.get("NameTitelVorname1", "")
            surname = listed_as.partition(", ")[0]
            family = person.get("Name", "")
            if not person.get("Rufname") or not surname.endswith(family):
                raise ValueError(f"cannot read the name {listed_as}")
            particles = [token for token in surname.removesuffix(family).split() if not token.endswith(".")]
            constituency = next((e for e in data if e.get("Gebietsart") == "WAHLKREIS"), None)
            party_list = next((e for e in data if e.get("Gebietsart") in ("LAND", "BUND")), None)
            members.append(
                {
                    "file": file,
                    "key": f"{person.get('Name')}:{person.get('Vorname')}:{person.get('Geburtsdatum')}",
                    "name": " ".join([person.get("Rufname", ""), *particles, family]),
                    "ballot_name": listed_as,
                    "birth_year": int(person.get("Geburtsdatum", "")),
                    "gender": {"m": "male", "w": "female", "d": "diverse"}[person.get("Geschlecht", "")],
                    "seat": {"E": "constituency", "Z": "list", "J": "list"}[data.get("Gewaehlt", "")],
                    "wk": None if constituency is None else constituency.get("Gebietsnummer", "").zfill(3),
                    "wk_group": None if constituency is None else groups[constituency.get("Gruppe", "")],
                    "land": None if party_list is None else party_list.get("Gebietsnummer", "").zfill(2),
                    "list_group": None if party_list is None else groups[party_list.get("Gruppe", "")],
                    "position": None if party_list is None else int(party_list.get("Listenplatz", "")),
                }
            )
    listed = pl.DataFrame(members)
    european_members = listed.filter(pl.col("file") == "ep_elected").drop("file", "seat", "wk", "wk_group")
    elected = (
        listed.filter(pl.col("file") == "elected")
        .drop("file")
        .with_columns(
            seat_land=pl.when(pl.col("seat") == "constituency")
            .then(pl.col("wk").replace_strict(land_of, default=None))
            .otherwise(pl.col("land")),
            seat_group=pl.when(pl.col("seat") == "constituency")
            .then(pl.col("wk_group"))
            .otherwise(pl.col("list_group")),
        )
    )
    seated = winners.filter(pl.col("seated") != "–").select("wk", wk_group="group")
    problems = {
        "duplicate members": elected.filter(pl.col("key").is_duplicated()),
        "constituency members who did not win a seat": elected.filter(pl.col("seat") == "constituency").join(
            seated, on=["wk", "wk_group"], how="anti"
        ),
        "seated winners missing from the members": seated.join(
            elected.filter(pl.col("seat") == "constituency"), on=["wk", "wk_group"], how="anti"
        ),
        "list members without a list": elected.filter((pl.col("seat") == "list") & pl.col("land").is_null()),
        "candidacies on lists that did not stand": elected.filter(pl.col("land").is_not_null()).join(
            lists, left_on=["land", "list_group"], right_on=["land", "group"], how="anti"
        ),
        "candidacies for candidates who did not stand": elected.filter(pl.col("wk").is_not_null()).join(
            candidates, left_on=["wk", "wk_group"], right_on=["wk", "group"], how="anti"
        ),
    }
    failures = [f"{label}: {frame.head(3)}" for label, frame in problems.items() if frame.height]
    seats = elected.group_by(land="seat_land", group="seat_group").len("seats")
    totals = dict(seats.group_by("group").agg(pl.col("seats").sum()).iter_rows())
    if totals != SEATS:
        failures.append(f"seats differ from the declared result: {totals}")
    if failures:
        raise ValueError("\n".join(failures))

    european = pl.read_csv(
        raw.cached(SOURCE, FILES["ep_results"]).path, separator=";", skip_rows=9, infer_schema=False
    ).filter(pl.col("Anzahl").is_not_null())
    if european.select("Wahlart", "Wahltag").unique().rows() != [("EP", f"{EUROPEAN:%d.%m.%Y}")]:
        raise ValueError("the Europawahl kerg2.csv is not the 2024 European Parliament election")
    unknown = set(european.filter(pl.col("Gruppenart") != "Partei")["Gruppenname"]) - set(ACCOUNTING)
    if unknown:
        raise ValueError(f"unknown Europawahl accounting groups: {unknown}")
    european = european.select(
        area="Gebietsart",
        code="Gebietsnummer",
        parent="UegGebietsnummer",
        group=pl.when(pl.col("Gruppenart") == "Partei").then(pl.col("Gruppenname")),
        position=pl.col("Gruppenreihenfolge").cast(pl.Int16),
        measure=pl.col("Gruppenname").replace_strict(ACCOUNTING, default=1, return_dtype=pl.Int16),
        value=pl.col("Anzahl").cast(pl.Int64),
    )
    keys = ["group", "measure"]
    for label, coarse, fine in (("Land", "Land", "Kreis"), ("Bund", "Bund", "Land")):
        short = (
            european.filter(pl.col("area") == coarse)
            .select("code", *keys, "value")
            .join(
                european.filter(pl.col("area") == fine)
                .group_by("parent", *keys)
                .agg(pl.col("value").sum().alias("sum")),
                left_on=["code", *keys],
                right_on=["parent", *keys],
                how="full",
                coalesce=True,
                nulls_equal=True,
            )
            .filter(pl.col("value").ne_missing(pl.col("sum")))
        )
        if short.height:
            raise ValueError(f"Europawahl {label} totals differ from the sum of their parts: {short.head(5)}")
    kreis_values = european.filter(pl.col("area") == "Kreis").select("group", "measure", "value", kreis="code")
    if set(kreis_values["kreis"]) != set(areas.filter(pl.col("scheme") == "de.kreis")["code"]):
        raise ValueError("the Europawahl Kreise differ from the 2025 Kreise")
    european_options = (
        european.filter((pl.col("area") == "Bund") & (pl.col("measure") == 1))
        .select("group", "position", votes="value")
        .sort("position")
    )
    missing = set(european_options["group"]) - set(european_parties["short"])
    if missing:
        raise ValueError(f"Europawahl parties missing from the party list: {missing}")
    land_lists = european_members.filter(pl.col("land") != "99").select(group="list_group").unique()
    declared = pl.concat(
        [
            european_members.group_by(option="list_group").len("seats").with_columns(pool=pl.lit("national")),
            european_members.filter(pl.col("land") != "99").group_by(pool="list_group", option="land").len("seats"),
        ],
        how="diagonal",
    )
    pools = pl.concat(
        [
            european_options.select(
                pool=pl.lit("national"), option="group", votes="votes", seats=pl.lit(EUROPEAN_SEATS, pl.UInt32)
            ),
            european.filter((pl.col("area") == "Land") & (pl.col("measure") == 1))
            .join(land_lists, on="group")
            .join(declared.filter(pl.col("pool") == "national").select(group="option", seats="seats"), on="group")
            .select(pool="group", option="code", votes="value", seats="seats"),
        ]
    )
    allocated = (
        pools.join(pl.DataFrame({"divisor": [k + 0.5 for k in range(EUROPEAN_SEATS)]}), how="cross")
        .filter((pl.col("votes") / pl.col("divisor")).rank("ordinal", descending=True).over("pool") <= pl.col("seats"))
        .group_by("pool", "option")
        .len("seats")
    )
    problems = {
        "duplicate Europawahl members": european_members.filter(pl.col("key").is_duplicated()),
        "Europawahl members on lists that did not stand": european_members.join(
            european_options, left_on="list_group", right_on="group", how="anti"
        ),
        "seats that Sainte-Laguë/Schepers does not reproduce": allocated.join(
            declared, on=["pool", "option"], how="full", coalesce=True
        ).filter(pl.col("seats").ne_missing(pl.col("seats_right"))),
    }
    failures = [f"{label}: {frame.head(3)}" for label, frame in problems.items() if frame.height]
    if failures:
        raise ValueError("\n".join(failures))
    european_lists = (
        european.filter((pl.col("area") == "Land") & (pl.col("measure") == 1) & (pl.col("value") > 0))
        .join(land_lists, on="group")
        .select("group", land="code")
        .join(declared.select(group="pool", land="option", seats="seats"), on=["group", "land"], how="left")
        .sort("group", "land")
    )

    PARSED.mkdir(parents=True, exist_ok=True)
    constituencies.write_parquet(PARSED / "constituencies.parquet")
    geometry.write_parquet(PARSED / "shapes.parquet")
    areas.write_parquet(PARSED / "areas.parquet")
    units.write_parquet(PARSED / "units.parquet")
    values.select("code", "wk", "land", "ballot", "group", "channel", "measure", "flags", "value").write_parquet(
        PARSED / "district_values.parquet"
    )
    official.write_parquet(PARSED / "official_values.parquet")
    pl.concat(
        [candidates, others.select("wk", group=pl.lit("Übrige"), independent=pl.lit(False), outcome=pl.lit(None))],
        how="vertical_relaxed",
    ).sort("wk", "group").write_parquet(PARSED / "candidates.parquet")
    lists.join(seats, on=["land", "group"], how="left").sort("land", "group").write_parquet(PARSED / "lists.parquet")
    parties.select(short="GruppennameKurz", name="Gruppenname").write_parquet(PARSED / "parties.parquet")
    elected.drop("seat_land", "seat_group").sort("key").write_parquet(PARSED / "members.parquet")
    kreis_values.write_parquet(PARSED / "ep_values.parquet")
    european_options.join(
        declared.filter(pl.col("pool") == "national").select(group="option", seats="seats"), on="group", how="left"
    ).write_parquet(PARSED / "ep_options.parquet")
    european_lists.write_parquet(PARSED / "ep_lists.parquet")
    european_parties.select("short", "name").write_parquet(PARSED / "ep_parties.parquet")
    european_members.sort("key").write_parquet(PARSED / "ep_members.parquet")


def emit() -> Path:
    constituencies = pl.read_parquet(PARSED / "constituencies.parquet")
    shapes = {
        (scheme, code): (geom, point)
        for scheme, code, geom, point in pl.read_parquet(PARSED / "shapes.parquet").iter_rows()
    }
    areas = pl.read_parquet(PARSED / "areas.parquet")
    units = pl.read_parquet(PARSED / "units.parquet")
    district_values = pl.read_parquet(PARSED / "district_values.parquet")
    official = pl.read_parquet(PARSED / "official_values.parquet")
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    lists = pl.read_parquet(PARSED / "lists.parquet")
    parties = pl.read_parquet(PARSED / "parties.parquet")
    members = pl.read_parquet(PARSED / "members.parquet")
    european_values = pl.read_parquet(PARSED / "ep_values.parquet")
    european_options = pl.read_parquet(PARSED / "ep_options.parquet")
    european_lists = pl.read_parquet(PARSED / "ep_lists.parquet")
    european_parties = pl.read_parquet(PARSED / "ep_parties.parquet")
    european_members = pl.read_parquet(PARSED / "ep_members.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="2025 German federal election by polling district and 2024 European Parliament election in Germany",
            polity="DE",
        )

        def name(entity: UUID, text: str, lang: str = "de", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        results = cs.source("Die Bundeswahlleiterin", "Bundestagswahl 2025: Ergebnisse")
        statistics = cs.source("Die Bundeswahlleiterin", "Bundestagswahl 2025: Wahlbezirksstatistik")
        european = cs.source("Die Bundeswahlleiterin", "Europawahl 2024: Ergebnisse")
        for source, title, folder, held, notes in (
            (results, "Bundestagswahl 2025: Ergebnisse", "bundestagswahlen", ELECTION, RESULTS_NOTE),
            (statistics, "Bundestagswahl 2025: Wahlbezirksstatistik", "bundestagswahlen", ELECTION, DISTRICTS_NOTE),
            (european, "Europawahl 2024: Ergebnisse", "europawahlen", EUROPEAN, None),
        ):
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher="Die Bundeswahlleiterin",
                title=title,
                url=f"{BWL}/{folder}/{held.year}/ergebnisse/opendata.html",
                license="Datenlizenz Deutschland – Namensnennung – Version 2.0",
                license_url="https://www.govdata.de/dl-de/by-2-0",
                attribution=f"© Die Bundeswahlleiterin, Wiesbaden {held.year}",
                notes=notes,
            )
        for label, url in FILES.items():
            cs.artifact(
                raw.cached(SOURCE, url),
                european if label.startswith("ep_") else statistics if label == "districts" else results,
            )

        germany = cs.polity("DE")
        cs.add("polity", id=germany, kind="sovereign_state", iso_code="DE")
        name(germany, "Germany", "en")
        name(germany, "Bundesrepublik Deutschland", "de", "native")
        cs.add("slug", path="de", entity_id=germany)
        cs.add("external_id", entity_id=germany, scheme="wikidata", value="Q183")
        union = cs.polity("EU")
        parliament = cs.key("eu.body", "european-parliament")
        umbrella = cs.key("eu.election", "2024-06-06:european-parliament")
        if {union, parliament, umbrella} & cs.minted:
            raise ValueError("publish eu.eurlex before the Europawahl")
        cs.add("polity_parent", polity_id=germany, parent_id=union, valid_from=FOUNDING_MEMBER)
        for code, rank, noun, kind in (
            ("de.nation", 0, "country", "administrative"),
            ("de.land", 1, "state", "administrative"),
            ("de.kreis", 2, "district", "administrative"),
            ("de.wahlkreis", 2, "constituency", "electoral_district"),
            ("de.bezirk", 3, "borough", "administrative"),
            ("de.gemeinde", 3, "municipality", "administrative"),
            ("de.wahlbezirk", 4, "polling district", "precinct"),
        ):
            cs.add("scheme", code=code, polity_id=germany, kind=kind, rank=rank, noun=noun)
        nation = cs.area("de.nation", "DE")
        cs.add("area", id=nation, scheme_code="de.nation")
        cs.add("area_code", area_id=nation, code="DE", valid_from=None)
        name(nation, "Germany", "en")
        cs.add("polity_territory", polity_id=germany, area_id=nation)
        laender: dict[str, UUID] = {}
        for code, (abbreviation, label, english) in LAENDER.items():
            polity = cs.polity(f"DE-{abbreviation}")
            cs.add("polity", id=polity, kind="federal_unit", iso_code=f"DE-{abbreviation}")
            cs.add("polity_parent", polity_id=polity, parent_id=germany)
            laender[code] = cs.area("de.land", code)
            cs.add("area", id=laender[code], scheme_code="de.land")
            cs.add("area_code", area_id=laender[code], code=code, valid_from=None)
            cs.add("area_parent", area_id=laender[code], parent_id=nation, valid_from=None)
            cs.add("polity_territory", polity_id=polity, area_id=laender[code])
            for entity in (polity, laender[code]):
                name(entity, label)
                if english is not None:
                    name(entity, english, "en", "english")
        for (scheme, code), (geom, point) in shapes.items():
            if scheme != "de.wahlkreis":
                cs.add(
                    "area_geometry",
                    area_id=nation if scheme == "de.nation" else laender[code],
                    valid_from=None,
                    geom=geom,
                    label_point=point,
                    quality="official",
                    source_id=results,
                )

        constituency_areas: dict[str, UUID] = {}
        constituency_units: dict[str, UUID] = {}
        for wk, land, label in constituencies.iter_rows():
            constituency_areas[wk] = cs.area("de.wahlkreis", wk)
            cs.add("area", id=constituency_areas[wk], scheme_code="de.wahlkreis")
            cs.add("area_code", area_id=constituency_areas[wk], code=wk, valid_from=ELECTION)
            cs.add("area_parent", area_id=constituency_areas[wk], parent_id=laender[land], valid_from=ELECTION)
            name(constituency_areas[wk], label)
            cs.add(
                "area_geometry",
                area_id=constituency_areas[wk],
                valid_from=ELECTION,
                geom=shapes[("de.wahlkreis", wk)][0],
                label_point=shapes[("de.wahlkreis", wk)][1],
                quality="official",
                source_id=results,
            )
            constituency_units[wk] = cs.unit("de.wahlkreis", wk)
            cs.add(
                "reporting_unit",
                id=constituency_units[wk],
                polity_id=germany,
                scheme_code="de.wahlkreis",
                code=wk,
                area_relation="equals",
                area_id=constituency_areas[wk],
                kind="standard",
                valid_from=ELECTION,
            )
        cs.add("boundary_set", scheme_code="de.wahlkreis", vintage="2025", valid_from=ELECTION, as_of=ELECTION)

        area_ids = {
            f"{scheme}:{code}": str(cs.area(scheme, code))
            for scheme, code in areas.select("scheme", "code").iter_rows()
        }
        area_ids |= {f"de.land:{code}": str(id) for code, id in laender.items()}
        area = pl.concat_str("scheme", pl.lit(":"), "code").replace_strict(area_ids)
        cs.frame("area", areas.select(id=area, scheme_code="scheme"))
        cs.frame("area_code", areas.select(area_id=area, code="code", valid_from=pl.lit(None, pl.Date)))
        cs.frame(
            "area_parent",
            areas.select(
                area_id=area,
                parent_id=pl.concat_str("parent_scheme", pl.lit(":"), "parent_code").replace_strict(area_ids),
                valid_from=pl.lit(None, pl.Date),
            ),
        )
        cs.frame(
            "name",
            areas.select(
                entity_id=area, text="name", lang=pl.lit("de"), kind=pl.lit("official"), is_preferred=pl.lit(True)
            ),
        )
        unit_ids = {code: str(cs.unit("de.wahlbezirk", code)) for code in units["code"]}
        unit = pl.col("code").replace_strict(unit_ids)
        cs.frame(
            "reporting_unit",
            units.select(
                id=unit,
                polity_id=pl.lit(str(germany)),
                scheme_code=pl.lit("de.wahlbezirk"),
                code="code",
                counted_with_id=pl.col("counted_with").replace_strict(unit_ids, default=None),
                area_relation=pl.lit("within"),
                area_id=pl.concat_str("area_scheme", pl.lit(":"), "area_code").replace_strict(area_ids),
                kind=pl.col("kind").replace_strict({k: kind for k, (kind, _, _, _) in KINDS.items()}),
                valid_from=pl.lit(ELECTION),
                valid_to=pl.lit(CLOSED),
            ),
        )
        cs.frame(
            "reporting_unit_area",
            units.select(
                unit_id=unit,
                area_id=pl.col("wk").replace_strict({wk: str(id) for wk, id in constituency_areas.items()}),
                valid_from=pl.lit(ELECTION),
                valid_to=pl.lit(CLOSED),
            ),
        )
        cs.frame(
            "name",
            units.select(
                entity_id=unit, text="name", lang=pl.lit("de"), kind=pl.lit("official"), is_preferred=pl.lit(True)
            ),
        )
        for code in units.filter(pl.col("code").str.contains("~"))["code"]:
            cs.add("note", entity_id=UUID(unit_ids[code]), kind="caveat", body_md=DUPLICATE)
        for code in units.filter(pl.col("kind") == "8")["code"]:
            cs.add("note", entity_id=UUID(unit_ids[code]), kind="caveat", body_md=UNASSIGNED)

        party_ids: dict[str, UUID] = {}
        for short, label in parties.iter_rows():
            party_ids[short] = cs.key("de.party", short.upper())
            cs.add("party", id=party_ids[short], kind="party", polity_id=germany)
            name(party_ids[short], label)
            if short != label:
                name(party_ids[short], short, "de", "abbreviation")
            if short in COLORS:
                cs.add("party_color", party_id=party_ids[short], color=COLORS[short], valid_from=None)
        known = {short.upper(): id for short, id in party_ids.items()}
        european_party_ids: dict[str, UUID] = {}
        for short, label in european_parties.iter_rows():
            if short.upper() in known:
                european_party_ids[short] = known[short.upper()]
                continue
            european_party_ids[short] = cs.key("de.party", short.upper())
            cs.add("party", id=european_party_ids[short], kind="party", polity_id=germany)
            name(european_party_ids[short], label)
            if short != label:
                name(european_party_ids[short], short, "de", "abbreviation")
        bundestag = cs.key("de.body", "bundestag")
        cs.add("body", id=bundestag, polity_id=germany, kind="lower_house")
        name(bundestag, "Bundestag", "en")
        name(bundestag, "Deutscher Bundestag", "de", "native")
        cs.add("body_size", body_id=bundestag, seats=736, valid_from=date(2021, 10, 26), valid_to=CONSTITUTED)
        cs.add("body_size", body_id=bundestag, seats=630, valid_from=CONSTITUTED)
        systems: dict[str, UUID] = {}
        for key, rules, label, methodology in (
            (
                "bundestag-lists",
                {"formula": "sainte_lague_schepers", "threshold": THRESHOLD},
                "Bundestag second-vote allocation since the 2023 reform",
                LISTS_METHODOLOGY,
            ),
            (
                "bundestag-constituencies",
                {"formula": "fptp"},
                "Bundestag constituencies since the 2023 reform",
                CONSTITUENCIES_METHODOLOGY,
            ),
            (
                "european-parliament",
                {"formula": "sainte_lague_schepers"},
                "European Parliament elections in Germany since 2014",
                EUROPEAN_METHODOLOGY,
            ),
        ):
            systems[key] = cs.key("de.electoral_system", key)
            cs.add("electoral_system", id=systems[key], polity_id=germany, rules=rules, rules_version=1)
            name(systems[key], label, "en")
            cs.add("note", entity_id=systems[key], kind="methodology", body_md=methodology)
        election = cs.key("de.election", str(ELECTION))
        cs.add(
            "election",
            id=election,
            polity_id=germany,
            kind="general",
            date_from=ELECTION,
            date_to=ELECTION,
            status="held",
        )
        name(election, "2025 German federal election", "en")
        name(election, "Bundestagswahl 2025", "de", "native")
        path = f"de/{ELECTION}-general"
        cs.add("slug", path=path, entity_id=election)

        result_sets: dict[str, str] = {}
        default_sets: dict[str, UUID] = {}

        def result_set(key: str, contest: UUID | None) -> None:
            for suffix, source, default in (("", results, True), (":districts", statistics, False)):
                if key == "national" and suffix:
                    continue
                set_id = cs.key(f"{SOURCE}.result_set", f"{ELECTION}:{key}{suffix}")
                cs.add(
                    "result_set",
                    id=set_id,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=default,
                    completeness=0 if key == "national" else 1,
                )
                if default:
                    default_sets[key] = set_id
                if set_id in cs.minted:
                    result_sets[f"{key}{suffix}"] = str(set_id)

        national = cs.key("de.contest", f"{ELECTION}:bundestag")
        cs.add(
            "contest",
            id=national,
            election_id=election,
            kind="list_allocation",
            body_id=bundestag,
            district_area_id=nation,
            magnitude=630,
            electoral_system_id=systems["bundestag-lists"],
            rules_override={"threshold": {**THRESHOLD, "exempt": [str(party_ids[EXEMPT])]}},
            default_result_kind="counted",
        )
        name(national, "2025 German federal election, national seat allocation", "en")
        cs.add("slug", path=f"{path}/bundestag", entity_id=national)
        result_set("national", national)
        result_set("election", None)
        national_options: dict[str, UUID] = {}
        options: dict[str, str] = {}
        for land, frame in lists.group_by("land", maintain_order=True):
            land = str(land[0])
            contest_key = f"{ELECTION}:list:{land}"
            contest = cs.key("de.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="list_allocation",
                body_id=bundestag,
                district_area_id=laender[land],
                allocation_parent_id=national,
                electoral_system_id=systems["bundestag-lists"],
                default_result_kind="counted",
            )
            name(contest, f"2025 German federal election, {LAENDER[land][1]} party lists", "en")
            cs.add("slug", path=f"{path}/{slug(LAENDER[land][1])}-lists", entity_id=contest)
            result_set(f"list:{land}", contest)
            for group, seats in frame.select("group", "seats").iter_rows():
                if group not in national_options:
                    national_options[group] = cs.key("de.option", f"{ELECTION}:bundestag:{group}")
                    cs.add(
                        "contest_option",
                        id=national_options[group],
                        contest_id=national,
                        kind="list",
                        party_id=party_ids[group],
                        ballot_label=group,
                    )
                option = cs.key("de.option", f"{contest_key}:{group}")
                options[f"2:{land}:{group}"] = str(option)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="list",
                    party_id=party_ids[group],
                    ballot_label=group,
                )
                if seats is not None:
                    cs.add(
                        "outcome", result_set_id=default_sets[f"list:{land}"], option_id=option, kind="won", seats=seats
                    )
        for group, seats in lists.group_by("group").agg(pl.col("seats").sum()).filter(pl.col("seats") > 0).iter_rows():
            cs.add(
                "outcome",
                result_set_id=default_sets["national"],
                option_id=national_options[group],
                kind="won",
                seats=seats,
            )

        labels = dict(constituencies.select("wk", "name").iter_rows())
        for wk, frame in candidates.group_by("wk", maintain_order=True):
            wk = str(wk[0])
            contest_key = f"{ELECTION}:{wk}"
            contest = cs.key("de.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=bundestag,
                district_area_id=constituency_areas[wk],
                magnitude=1,
                electoral_system_id=systems["bundestag-constituencies"],
                default_result_kind="counted",
            )
            name(contest, f"Constituency {int(wk)}, {labels[wk]}", "en")
            cs.add("slug", path=f"{path}/{slug(labels[wk])}", entity_id=contest)
            result_set(wk, contest)
            for group, independent, outcome in frame.select("group", "independent", "outcome").iter_rows():
                option = cs.key("de.option", f"{contest_key}:{'others' if group == 'Übrige' else group}")
                options[f"1:{wk}:{group}"] = str(option)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="others" if group == "Übrige" else "candidate",
                    party_id=None if independent or group == "Übrige" else party_ids[group],
                    ballot_label=group.removeprefix("EB:").strip(),
                )
                if outcome is not None:
                    cs.add(
                        "outcome",
                        result_set_id=default_sets[wk],
                        option_id=option,
                        kind=outcome,
                        notes=UNSEATED_NOTE if outcome == "won" else None,
                    )

        persons: dict[str, UUID] = {}
        for row in members.iter_rows(named=True):
            person = cs.key(f"{SOURCE}.person", f"{ELECTION.year}:{row['key']}")
            persons[row["key"]] = person
            cs.add(
                "person",
                id=person,
                gender=row["gender"],
                birth_date=date(row["birth_year"], 1, 1),
                birth_precision="year",
            )
            name(person, row["name"])
            if row["wk"] is not None:
                cs.add(
                    "candidacy",
                    id=cs.key("de.candidacy", f"{ELECTION}:{row['wk']}:{row['wk_group']}"),
                    person_id=person,
                    option_id=UUID(options[f"1:{row['wk']}:{row['wk_group']}"]),
                    role="head",
                    ballot_name=row["ballot_name"],
                    member_of_party_id=party_ids[row["wk_group"]],
                )
            if row["land"] is not None:
                candidacy = cs.key(
                    "de.candidacy", f"{ELECTION}:list:{row['land']}:{row['list_group']}:{row['position']}"
                )
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=UUID(options[f"2:{row['land']}:{row['list_group']}"]),
                    role="list_member",
                    list_position=row["position"],
                    ballot_name=row["ballot_name"],
                    member_of_party_id=party_ids[row["list_group"]],
                )
                if row["seat"] == "list":
                    cs.add(
                        "outcome",
                        result_set_id=default_sets[f"list:{row['land']}"],
                        option_id=UUID(options[f"2:{row['land']}:{row['list_group']}"]),
                        candidacy_id=candidacy,
                        kind="elected",
                    )

        set_key = (
            pl.when(pl.col("ballot") == 0)
            .then(pl.lit("election"))
            .when(pl.col("ballot") == 1)
            .then(pl.col("wk"))
            .otherwise(pl.lit("list:") + pl.col("land"))
        )
        option = (
            pl.when(pl.col("group").is_not_null())
            .then(pl.format("{}:{}:{}", "ballot", pl.when(pl.col("ballot") == 1).then("wk").otherwise("land"), "group"))
            .replace_strict(options, default=None)
        )
        columns = ["result_set_id", "unit_id", "option_id", "channel", "measure", "count_round", "flags", "value"]
        cs.frame(
            "result_value",
            official.filter(set_key.is_in(list(result_sets))).select(
                result_set_id=set_key.replace_strict(result_sets),
                unit_id=pl.col("wk").replace_strict({wk: str(id) for wk, id in constituency_units.items()}),
                option_id=option,
                channel=pl.lit(0, pl.Int16),
                measure="measure",
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
                value="value",
            ),
        )
        by_district = set_key + ":districts"
        cs.frame(
            "result_value",
            district_values.filter(by_district.is_in(list(result_sets)))
            .select(
                result_set_id=by_district.replace_strict(result_sets),
                unit_id=unit,
                option_id=option,
                channel="channel",
                measure="measure",
                count_round=pl.lit(0, pl.Int16),
                flags="flags",
                value="value",
            )
            .select(columns),
        )

        european_election = cs.key("de.election", f"{EUROPEAN}:european-parliament")
        cs.add(
            "election",
            id=european_election,
            polity_id=germany,
            parent_id=umbrella,
            kind="general",
            date_from=EUROPEAN,
            date_to=EUROPEAN,
            status="held",
        )
        name(european_election, "2024 European Parliament election in Germany", "en")
        name(european_election, "Europawahl 2024", "de", "native")
        european_path = f"de/{EUROPEAN}-european-parliament"
        cs.add("slug", path=european_path, entity_id=european_election)
        european_contest = cs.key("de.contest", f"{EUROPEAN}:european-parliament")
        cs.add(
            "contest",
            id=european_contest,
            election_id=european_election,
            kind="list_allocation",
            body_id=parliament,
            district_area_id=nation,
            magnitude=EUROPEAN_SEATS,
            electoral_system_id=systems["european-parliament"],
            default_result_kind="counted",
        )
        name(european_contest, "2024 European Parliament election in Germany, national seat allocation", "en")
        cs.add("slug", path=f"{european_path}/european-parliament", entity_id=european_contest)
        european_set = cs.key(f"{SOURCE}.result_set", f"{EUROPEAN}:european-parliament")
        cs.add(
            "result_set",
            id=european_set,
            election_id=european_election,
            contest_id=european_contest,
            stage="official",
            kind="counted",
            status="active",
            source_id=european,
            is_default=True,
            completeness=1,
        )
        european_option_ids: dict[str, str] = {}
        for group, position, _, seats in european_options.iter_rows():
            option = cs.key("de.option", f"{EUROPEAN}:european-parliament:{group}")
            european_option_ids[group] = str(option)
            cs.add(
                "contest_option",
                id=option,
                contest_id=european_contest,
                kind="list",
                party_id=european_party_ids[group],
                ballot_position=position,
                ballot_label=group,
            )
            if seats is not None:
                cs.add("outcome", result_set_id=european_set, option_id=option, kind="won", seats=seats)
        land_options: dict[str, UUID] = {}
        for group, land, seats in european_lists.iter_rows():
            contest_key = f"{EUROPEAN}:european-parliament:{group}:{land}"
            contest = cs.key("de.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=european_election,
                kind="list_allocation",
                body_id=parliament,
                district_area_id=laender[land],
                party_scope_id=european_party_ids[group],
                allocation_parent_id=european_contest,
                electoral_system_id=systems["european-parliament"],
                default_result_kind="counted",
            )
            name(contest, f"2024 European Parliament election in Germany, {group} list for {LAENDER[land][1]}", "en")
            cs.add("slug", path=f"{european_path}/{slug(group)}-{slug(LAENDER[land][1])}", entity_id=contest)
            land_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=land_set,
                election_id=european_election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=european,
                is_default=True,
                completeness=0,
            )
            land_options[f"{land}:{group}"] = cs.key("de.option", contest_key)
            cs.add(
                "contest_option",
                id=land_options[f"{land}:{group}"],
                contest_id=contest,
                kind="list",
                party_id=european_party_ids[group],
                ballot_label=group,
            )
            if seats is not None:
                cs.add(
                    "outcome",
                    result_set_id=land_set,
                    option_id=land_options[f"{land}:{group}"],
                    kind="won",
                    seats=seats,
                )
        for row in european_members.iter_rows(named=True):
            person = persons.get(row["key"])
            if person is None:
                person = cs.key(f"{SOURCE}.person", f"{EUROPEAN.year}:{row['key']}")
                cs.add(
                    "person",
                    id=person,
                    gender=row["gender"],
                    birth_date=date(row["birth_year"], 1, 1),
                    birth_precision="year",
                )
                name(person, row["name"])
            candidacy = cs.key(
                "de.candidacy", f"{EUROPEAN}:european-parliament:{row['land']}:{row['list_group']}:{row['position']}"
            )
            is_national = row["land"] == "99"
            option = (
                UUID(european_option_ids[row["list_group"]])
                if is_national
                else land_options[f"{row['land']}:{row['list_group']}"]
            )
            cs.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role="list_member",
                list_position=row["position"],
                ballot_name=row["ballot_name"],
                member_of_party_id=european_party_ids[row["list_group"]],
            )
            cs.add(
                "outcome",
                result_set_id=european_set
                if is_national
                else cs.key(
                    f"{SOURCE}.result_set", f"{EUROPEAN}:european-parliament:{row['list_group']}:{row['land']}"
                ),
                option_id=option,
                candidacy_id=candidacy,
                kind="elected",
            )

        kreis_units: dict[str, str] = {}
        for code in sorted(set(european_values["kreis"])):
            kreis_units[code] = str(cs.unit("de.kreis", code))
            cs.add(
                "reporting_unit",
                id=UUID(kreis_units[code]),
                polity_id=germany,
                scheme_code="de.kreis",
                code=code,
                area_relation="equals",
                area_id=UUID(area_ids[f"de.kreis:{code}"]),
                kind="standard",
            )
        if european_set in cs.minted:
            cs.frame(
                "result_value",
                european_values.select(
                    result_set_id=pl.lit(str(european_set)),
                    unit_id=pl.col("kreis").replace_strict(kreis_units),
                    option_id=pl.col("group").replace_strict(european_option_ids, default=None),
                    channel=pl.lit(0, pl.Int16),
                    measure="measure",
                    count_round=pl.lit(0, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    value="value",
                ),
            )
        return cs.write()
