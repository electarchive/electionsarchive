"""Romanian local elections of 10 and 24 June 2012 and the parliamentary election of 9 December 2012.

The Permanent Electoral Authority publishes both elections on data.gov.ro. The local elections come as eight regional
files in the export format of the central database: one row per polling station or circumscription, contest, and
list or candidate, with the minutes of the unit on a row of its own. The parliamentary election comes as one polling
station file, plus tables at the level of the college and the circumscription that carry the seats and the totals.

The 2012 parliament was elected in single-member colleges. A candidate who polled more than half of the votes in the
college won it. The other seats were allocated to party lists by circumscription and filled from the candidates who
came closest in their colleges. So a contest here is one college of one chamber, its options are the candidates, and
a candidate's outcome is the mandate the Authority prints for them.
"""

import io
from datetime import date
from pathlib import Path

import fastexcel
import polars as pl

from ea import raw
from ea.package import Package
from ea.sources.ro import aep
from ea.sources.ro.aep import (
    BLANK,
    CAST,
    INVALID,
    REGISTERED,
    ROMANIA,
    SOURCE,
    VALID,
    VOTES,
    canon,
    key,
)

HOST = "http://data.gov.ro/storage/f"
URLS = {
    "competitors": f"{HOST}/2014-03-25T08%3A58%3A46.712Z/competitori-electorali.xls",
    "local-layout": f"{HOST}/2014-03-25T08%3A56%3A31.370Z/structura-baza-de-date-locale-2012.xls",
    "bucuresti-ilfov": f"{HOST}/2014-03-26T14%3A18%3A21.609Z/bucuresti-ilfov.csv",
    "centru": f"{HOST}/2014-03-26T14%3A20%3A07.378Z/centru.csv",
    "nord-est": f"{HOST}/2014-03-26T14%3A22%3A10.000Z/nord-est.csv",
    "nord-vest": f"{HOST}/2014-03-26T14%3A23%3A58.605Z/nord-vest.csv",
    "sud-est": f"{HOST}/2014-03-26T14%3A25%3A13.851Z/sud-est.csv",
    "sud-muntenia": f"{HOST}/2014-03-26T14%3A27%3A35.126Z/sud-muntenia.csv",
    "sud-vest": f"{HOST}/2014-03-26T14%3A28%3A40.048Z/sud-vest.csv",
    "vest": f"{HOST}/2014-03-26T14%3A29%3A35.998Z/vest.csv",
    "stations": f"{HOST}/2014-03-25T15%3A00%3A44.860Z/rezultatenivelsectieparlamentare2012.csv",
    "colleges": f"{HOST}/2014-03-25T15%3A01%3A45.933Z/statisticinivelcolegiu.xls",
    "circumscriptions": f"{HOST}/2014-03-25T15%3A02%3A44.090Z/rezultate2012nivelcircumscriptie.xls",
    "candidates": f"{HOST}/2014-03-25T15%3A03%3A59.881Z/rezultatecandidati2012.xls",
    "localities": f"{HOST}/2014-03-25T15%3A03%3A11.661Z/rezultate2012-statisticapelocalitati.xls",
    "abroad": f"{HOST}/2014-03-25T15%3A03%3A40.917Z/rezultate2012-statisticastrainatatepetari.xls",
}
REGIONS = [
    k
    for k in URLS
    if k in ("bucuresti-ilfov", "centru", "nord-est", "nord-vest", "sud-est", "sud-muntenia", "sud-vest", "vest")
]
FIRST, SECOND, PARLIAMENT = "2012-06-10", "2012-06-24", "2012-12-09"
DATES = {FIRST: date(2012, 6, 10), SECOND: date(2012, 6, 24), PARLIAMENT: date(2012, 12, 9)}
PACKAGES = ["ro.aep/2012-registry", f"ro.aep/{FIRST}", f"ro.aep/{SECOND}", f"ro.aep/{PARLIAMENT}"]
TURN = {FIRST: 1, SECOND: 2}
BUCHAREST = 42
ABROAD = 43
INDEPENDENT_CODE = 999
FOLD = str.maketrans({"Ş": "Ș", "ş": "ș", "Ţ": "Ț", "ţ": "ț"})

LOCAL_METHOD = (
    "Counted per polling station or per circumscription and published by the Permanent Electoral Authority as open "
    "data. Registered voters are the voters on the electoral lists, ballots cast are the voters who took part, valid "
    "votes are the valid votes, and invalid ballots are the null votes. The file prints the name of a candidate for "
    "mayor, county council president, and independent candidate, and the party, alliance, or organization that put "
    "the candidate up."
)
PARLIAMENT_METHOD = (
    "Counted per polling station and published by the Permanent Electoral Authority as open data. Registered voters "
    "are the voters on the electoral lists, ballots cast are the voters who took part, valid votes are the votes for "
    "the candidates of the college, invalid ballots are the null votes, and blank ballots are the white votes. "
    "A candidate won the college outright with more than half of the votes. Other seats went to party lists by "
    "circumscription and were filled from the best placed candidates of each list, so a college can send two "
    "candidates to the chamber and another none. The mandate column of the Authority's candidate table marks every "
    "candidate who took a seat, and is stored as declared."
)


def tidy(value: object) -> str:
    return " ".join(str(value or "").translate(FOLD).split())


def fetch() -> None:
    for url in URLS.values():
        raw.fetch(SOURCE, url, suffix=Path(url).suffix)


def party_id(label: str) -> str:
    return key("party", "2012", canon(label))


def sheet(name: str) -> pl.DataFrame:
    book = fastexcel.read_excel(str(raw.cached(SOURCE, URLS[name]).path))
    return book.load_sheet_by_name(book.sheet_names[0], header_row=0, dtypes="string").to_polars()


_LOCAL: pl.DataFrame | None = None
_STATIONS: pl.DataFrame | None = None


def local_all() -> pl.DataFrame:
    """Every row of the eight regional files, with the printed columns cast and the contest type named."""
    global _LOCAL
    if _LOCAL is not None:
        return _LOCAL
    frames = []
    for region in REGIONS:
        text = raw.cached(SOURCE, URLS[region]).path.read_bytes().decode("utf-16")
        frame = pl.read_csv(io.BytesIO(text.encode()), separator=";", infer_schema_length=0, truncate_ragged_lines=True)
        frames.append(frame.select(c for c in frame.columns if c and not c.startswith("_duplicated")))
    data = pl.concat(frames)
    texts = ("DEN_JUD", "DEN_CIRC", "NUMEC", "DENP")
    numbers = [c for c in data.columns if c not in texts]
    data = data.with_columns(
        *(pl.col(c).cast(pl.Int64) for c in numbers),
        *(pl.col(c).map_elements(tidy, return_dtype=pl.String).alias(c) for c in texts),
    ).with_columns(pl.col(c).replace("", None) for c in texts)
    data = data.with_columns(
        pl.when(pl.col("TIPPV") == 1)
        .then(pl.lit("P"))
        .when(pl.col("TIPPV") == 2)
        .then(pl.lit("CL"))
        .when(pl.col("TIPPV") == 3)
        .then(pl.when(pl.col("JUD") == BUCHAREST).then(pl.lit("CGMB")).otherwise(pl.lit("CJ")))
        .otherwise(pl.when(pl.col("JUD") == BUCHAREST).then(pl.lit("PG")).otherwise(pl.lit("PCJ")))
        .alias("tip"),
    )
    data = data.with_columns(
        pl.when(pl.col("tip").is_in(["P", "CL"]))
        .then(pl.format("{}-{}", pl.col("JUD"), pl.col("CIRC")))
        .when(pl.col("tip").is_in(["CJ", "PCJ"]))
        .then(pl.col("JUD").cast(pl.String))
        .otherwise(pl.lit("bucharest"))
        .alias("scope"),
        (pl.col("NPOZ") == 0).alias("minutes"),
    )
    _LOCAL = data
    return data


def competitors() -> pl.DataFrame:
    frame = sheet("competitors").select(
        pl.col("CODU").cast(pl.Int64).alias("code"),
        pl.col("DENP").map_elements(tidy, return_dtype=pl.String).alias("label"),
        pl.col("TIPP").str.strip_chars().alias("type"),
    )
    if frame["code"].n_unique() != frame.height:
        raise ValueError("a competitor code repeats")
    return frame


def stations_all() -> pl.DataFrame:
    global _STATIONS
    if _STATIONS is None:
        frame = pl.read_csv(
            raw.cached(SOURCE, URLS["stations"]).path, separator=";", infer_schema_length=0, truncate_ragged_lines=True
        )
        numbers = [
            "numarCircumscriptie",
            "numarSectie",
            "numarColegiu",
            "NrAlegatoriListe",
            "NrAlegatoriPrezentatiLaUrne",
            "TotalVoturiValabilExprimateLaNivelSectie",
            "NrVoturiNule",
            "NrVoturiAlbe",
            "pozitieBuletin",
            "VoturiCandidat",
        ]
        _STATIONS = frame.with_columns(
            *(pl.col(c).cast(pl.Int64) for c in numbers),
            *(
                pl.col(c).map_elements(tidy, return_dtype=pl.String).alias(c)
                for c in ("numeCircumscriptie", "Localitate", "NumeCandidat", "numeCompetitor")
            ),
        )
    return _STATIONS


def label_pool() -> dict[str, str]:
    """Every party, alliance, and organization label the three sources print, by folded name."""
    found: dict[str, str] = {}
    for label in competitors()["label"]:
        if canon(label) != "CANDIDAT INDEPENDENT":
            found.setdefault(canon(label), label)
    local = local_all()
    for label in local.filter(pl.col("CODU") != INDEPENDENT_CODE)["DENP"].drop_nulls().unique().sort():
        found.setdefault(canon(label), label)
    for label in stations_all()["numeCompetitor"].unique().sort():
        if label != "NULL":
            found.setdefault(canon(label), label)
    return found


def registry() -> Package:
    p = Package(
        "ro.aep/2012-registry",
        SOURCE,
        "Parties, alliances, and minority organizations on the Romanian ballots of 2012, from the Permanent "
        "Electoral Authority's open data",
        depends=["ro.aep/registry"],
    )
    p.external.add(ROMANIA)
    p.artifact(raw.cached(SOURCE, URLS["competitors"]), aep.AEP_SOURCE)
    kinds = dict(
        zip(competitors()["label"].map_elements(canon, return_dtype=pl.String), competitors()["type"], strict=True)
    )
    for name, label in sorted(label_pool().items()):
        id = party_id(label)
        p.add("party", id=id, kind="bloc" if kinds.get(name) == "A" else "party", polity_id=ROMANIA)
        p.name(id, label, "ro", "official")
    return p


def parties_of(labels: pl.Series) -> list[str | None]:
    return [None if label is None else party_id(label) for label in labels]


def label_names(p: Package, ids: list[str], texts: list[str], lang: str = "ro") -> None:
    p.frame(
        "name",
        pl.DataFrame(
            {
                "id": [p.id("name", i, lang, "official") for i in ids],
                "entity_id": ids,
                "text": texts,
                "lang": lang,
                "kind": "official",
                "is_preferred": True,
            }
        ),
    )


def contest_frames(
    p: Package,
    election: str,
    contests: pl.DataFrame,
    fields: dict[str, object],
    slug_head: str,
) -> None:
    """`contests` holds `key`, `en`, `ro`, and `slug`; add the contests, names, slugs, result sets, and notes."""
    ids = [key("contest", k) for k in contests["key"]]
    sets = [key("result_set", k) for k in contests["key"]]
    p.frame("contest", pl.DataFrame({"id": ids, "election_id": election, **fields}))
    p.frame(
        "name",
        pl.DataFrame(
            {
                "id": [p.id("name", c, lang, "official") for c in ids for lang in ("en", "ro")],
                "entity_id": [c for c in ids for _ in range(2)],
                "text": [t for pair in zip(contests["en"], contests["ro"], strict=True) for t in pair],
                "lang": ["en", "ro"] * len(ids),
                "kind": ["official", "native"] * len(ids),
                "is_preferred": [True, False] * len(ids),
            }
        ),
    )
    p.frame(
        "slug",
        pl.DataFrame({"path": [f"{slug_head}/{s}" for s in contests["slug"]], "entity_id": ids, "is_current": True}),
    )
    p.frame(
        "result_set",
        pl.DataFrame(
            {
                "id": sets,
                "election_id": election,
                "contest_id": ids,
                "stage": "official",
                "kind": "counted",
                "source_id": aep.AEP_SOURCE,
                "status": "active",
                "is_default": True,
                "completeness": 1.0,
            }
        ),
    )
    p.frame(
        "note",
        pl.DataFrame(
            {
                "id": [key("note", r) for r in sets],
                "entity_id": sets,
                "kind": "methodology",
                "body_md": contests["note"],
                "lang": "en",
            }
        ),
    )


def person_frames(p: Package, options: pl.DataFrame) -> None:
    """`options` holds `option_id`, `name`, and `independent` for the options that name a person."""
    named = options.filter(pl.col("name").is_not_null())
    if not named.height:
        return
    who = [key("person", o) for o in named["option_id"]]
    p.frame("person", pl.DataFrame({"id": who}))
    label_names(p, who, [n.title() for n in named["name"]])
    p.frame(
        "candidacy",
        pl.DataFrame(
            {
                "id": [key("candidacy", o) for o in named["option_id"]],
                "person_id": who,
                "option_id": named["option_id"],
                "role": "head",
                "ballot_name": [n.title() for n in named["name"]],
                "is_independent": named["independent"],
            }
        ),
    )


def result_values(frame: pl.DataFrame) -> pl.DataFrame:
    """`frame` holds `result_set_id`, `unit_id`, `option_id`, `measure`, and `value`; fill the fact columns."""
    return frame.select(
        "result_set_id",
        "unit_id",
        "option_id",
        pl.lit(0, pl.Int16).alias("channel"),
        pl.col("measure").cast(pl.Int16),
        pl.lit(0, pl.Int16).alias("count_round"),
        pl.lit(0, pl.Int16).alias("flags"),
        pl.col("value").cast(pl.Int64),
    ).filter(pl.col("value").is_not_null())


def unit_frames(p: Package, units: pl.DataFrame) -> None:
    """`units` holds `code`, `scheme`, `kind`, `name`."""
    ids = [key("unit", c) for c in units["code"]]
    p.frame(
        "reporting_unit",
        pl.DataFrame(
            {
                "id": ids,
                "polity_id": ROMANIA,
                "scheme_code": units["scheme"],
                "code": units["code"],
                "area_relation": "unlocated",
                "kind": units["kind"],
            }
        ),
    )
    label_names(p, ids, units["name"].to_list())


# ---------------------------------------------------------------- local elections

LOCAL = {
    # tip: (kind, body or office, system, English, Romanian, slug)
    "CJ": ("list", aep.COUNTY_COUNCIL, aep.SYSTEM_LIST, "County council", "Consiliul județean", "county-council"),
    "PCJ": (
        "office",
        aep.COUNTY_PRESIDENT,
        aep.SYSTEM_PLURALITY,
        "County council president",
        "Președinte de consiliu județean",
        "county-president",
    ),
    "CL": ("list", aep.LOCAL_COUNCIL, aep.SYSTEM_LIST, "Local council", "Consiliul local", "local-council"),
    "P": ("office", aep.MAYOR, aep.SYSTEM_PLURALITY, "Mayor", "Primar", "mayor"),
    "CGMB": (
        "list",
        aep.BUCHAREST_COUNCIL,
        aep.SYSTEM_LIST,
        "General Council of Bucharest",
        "Consiliul General al Municipiului București",
        "bucharest-council",
    ),
    "PG": (
        "office",
        aep.GENERAL_MAYOR,
        aep.SYSTEM_PLURALITY,
        "General Mayor of Bucharest",
        "Primarul General al Municipiului București",
        "general-mayor",
    ),
}


def local_rows(day: str) -> pl.DataFrame:
    data = local_all().filter(pl.col("TUR") == TURN[day])
    seen = data.filter(~pl.col("minutes")).select("TUR", "JUD", "CIRC", "SV", "TIPPV", "NPOZ")
    if seen.is_duplicated().any():
        raise ValueError(f"{day}: a unit lists a ballot line twice")
    if data.filter(pl.col("minutes")).select("JUD", "CIRC", "SV", "TIPPV").is_duplicated().any():
        raise ValueError(f"{day}: a unit has two minutes rows")
    return data.with_columns(
        pl.format("local{}-{}-{}", pl.lit(day), pl.col("tip"), pl.col("scope")).alias("contest"),
        pl.when(pl.col("SV") > 0)
        .then(pl.format("{}:{}/{}/{}", pl.lit(day), pl.col("JUD"), pl.col("CIRC"), pl.col("SV")))
        .otherwise(pl.format("{}:CE/{}/{}", pl.lit(day), pl.col("JUD"), pl.col("CIRC")))
        .alias("code"),
    )


def build_local(day: str) -> Package:
    first = day == FIRST
    p = Package(
        f"ro.aep/{day}",
        SOURCE,
        "The Romanian local elections of 10 June 2012 by circumscription and polling station, from the "
        "Permanent Electoral Authority's open data"
        if first
        else "The Romanian local votes of 24 June 2012 in the communes and towns whose councils or mayors were "
        "chosen that day, from the Permanent Electoral Authority's open data",
        depends=["ro.aep/registry", "ro.aep/2012-registry"],
    )
    p.external.add(ROMANIA)
    for name in REGIONS:
        p.artifact(raw.cached(SOURCE, URLS[name]), aep.AEP_SOURCE) if first else p.uses(raw.cached(SOURCE, URLS[name]))
    if first:
        p.artifact(raw.cached(SOURCE, URLS["local-layout"]), aep.AEP_SOURCE)
    else:
        p.uses(raw.cached(SOURCE, URLS["local-layout"]))
    data = local_rows(day)
    codes = competitors()
    printed = dict(zip(codes["code"], codes["label"], strict=True))
    wrong = data.filter(
        (pl.col("NPOZ") > 0)
        & (pl.col("CODU") != INDEPENDENT_CODE)
        & (
            pl.col("DENP").map_elements(canon, return_dtype=pl.String)
            != pl.col("CODU").replace_strict({c: canon(v) for c, v in printed.items()}, default=None)
        )
    )
    if wrong.height:
        raise ValueError(f"{day}: competitor names differ from the competitor table: {wrong.head(3).to_dicts()}")
    election = key("election", f"local{day}")
    p.add(
        "election",
        id=election,
        polity_id=ROMANIA,
        kind="local",
        date_from=DATES[day],
        date_to=DATES[day],
        status="held",
    )
    p.add("slug", path=f"ro/{day}-local", entity_id=election, is_current=True)
    english = "2012 Romanian local elections" if first else "2012 Romanian local elections, votes of 24 June"
    romanian = "Alegeri locale din 10 iunie 2012" if first else "Alegeri locale, votul din 24 iunie 2012"
    p.name(election, english, "en", "official")
    p.name(election, romanian, "ro", "native", False)

    stationed = set(data.filter(pl.col("SV") > 0)["contest"])
    main = data.filter(pl.when(pl.col("contest").is_in(stationed)).then(pl.col("SV") > 0).otherwise(pl.col("SV") == 0))
    declared = data.filter(pl.col("contest").is_in(stationed) & (pl.col("SV") == 0))
    places = main.group_by("code", maintain_order=True).agg(
        pl.col("SV").first(), pl.col("DEN_CIRC").drop_nulls().first(), pl.col("DEN_JUD").first()
    )
    station = places["SV"] > 0
    unit_frames(
        p,
        pl.DataFrame(
            {
                "code": places["code"],
                "scheme": ["ro.sv" if s else "ro.circumscription" for s in station],
                "kind": ["standard" if s else "aggregate" for s in station],
                "name": [
                    f"{(circ or county).title()} (station {sv})" if s else f"{(circ or county).title()}"
                    for circ, county, sv, s in zip(
                        places["DEN_CIRC"], places["DEN_JUD"], places["SV"], station, strict=True
                    )
                ],
            }
        ),
    )
    unit_ids = {c: key("unit", c) for c in places["code"]}
    labels: dict[str, str] = {}
    for tip, (kind, holder, system, en, ro, slug) in LOCAL.items():
        rows = main.filter(pl.col("tip") == tip)
        if not rows.height:
            continue
        found = rows.group_by("contest", maintain_order=True).agg(
            pl.col("scope").first(),
            pl.col("DEN_JUD").first(),
            pl.col("DEN_CIRC").drop_nulls().first(),
            pl.col("JUD").first(),
            pl.col("CIRC").first(),
        )
        names_en, names_ro, slugs, notes = [], [], [], []
        for scope, county, circ in zip(found["scope"], found["DEN_JUD"], found["DEN_CIRC"], strict=True):
            if tip in ("P", "CL"):
                where = where_ro = (circ or county).title()
            elif tip in ("CJ", "PCJ"):
                where = f"{county.title()} County"
                where_ro = county.title()
            else:
                where = where_ro = ""
            names_en.append(f"{english}, {en}" + (f", {where}" if where else ""))
            names_ro.append(f"{ro}" + (f", {where_ro}" if where_ro else "") + (", 2012"))
            slugs.append(f"{slug}/{scope}")
            notes.append(LOCAL_METHOD)
        contests = pl.DataFrame({"key": found["contest"], "en": names_en, "ro": names_ro, "slug": slugs, "note": notes})
        fields: dict[str, object] = {
            "kind": "list_allocation" if kind == "list" else "office",
            "round_number": 1,
            "default_result_kind": "counted",
            "electoral_system_id": system,
        }
        fields["body_id" if kind == "list" else "office_id"] = holder
        if kind == "office":
            fields["magnitude"] = 1
        contest_frames(p, election, contests, fields, f"ro/{day}-local")
        lines = rows.filter(~pl.col("minutes"))
        options = lines.group_by(["contest", "NPOZ"], maintain_order=True).agg(
            pl.col("CODU").first(),
            pl.col("CODU").n_unique().alias("codes"),
            pl.col("DENP").first(),
            pl.col("NUMEC").drop_nulls().first(),
            pl.col("NUMEC").drop_nulls().n_unique().alias("persons"),
        )
        if (options["codes"] > 1).any() or (options["persons"] > 1).any():
            raise ValueError(f"{day} {tip}: a ballot line changes between units")
        indep = options["CODU"] == INDEPENDENT_CODE
        options = options.with_columns(
            pl.struct("contest", "NPOZ")
            .map_elements(lambda r: key("option", r["contest"], r["NPOZ"]), return_dtype=pl.String)
            .alias("option_id"),
            indep.alias("independent"),
        )
        p.frame(
            "contest_option",
            pl.DataFrame(
                {
                    "id": options["option_id"],
                    "contest_id": [key("contest", c) for c in options["contest"]],
                    "kind": ["candidate" if kind == "office" or i else "list" for i in options["independent"]],
                    "party_id": [
                        None if i else party_id(label)
                        for i, label in zip(options["independent"], options["DENP"], strict=True)
                    ],
                    "ballot_position": options["NPOZ"].cast(pl.Int32),
                    "ballot_label": [
                        (name.title() if i and name else label)
                        for i, name, label in zip(
                            options["independent"], options["NUMEC"], options["DENP"], strict=True
                        )
                    ],
                }
            ),
        )
        person_frames(
            p,
            options.filter(pl.col("NUMEC").is_not_null() & (pl.col("independent") | (kind == "office"))).select(
                "option_id", pl.col("NUMEC").alias("name"), "independent"
            ),
        )
        for tip_option in options["option_id"]:
            labels[tip_option] = tip
        lookup = options.select("contest", "NPOZ", "option_id")
        sets = pl.DataFrame(
            {"contest": found["contest"], "result_set_id": [key("result_set", c) for c in found["contest"]]}
        )
        stage(p, sets, rows.filter(pl.col("minutes")), lines, lookup, unit_ids)
        outcomes(p, sets, data.filter((pl.col("tip") == tip) & (pl.col("SV") == 0) & ~pl.col("minutes")), lookup, kind)
        totals(p, sets, declared.filter(pl.col("tip") == tip), lookup)
    accept(p)
    return p


MEASURES = {REGISTERED: "TA", CAST: "TAPU", VALID: "TVVE", INVALID: "TVN"}


def stage(
    p: Package,
    sets: pl.DataFrame,
    minutes: pl.DataFrame,
    lines: pl.DataFrame,
    lookup: pl.DataFrame,
    unit_ids: dict[str, str],
) -> None:
    """The minutes of each unit and the votes of each ballot line."""
    if set(lines["code"]) - set(minutes["code"]) or minutes.select("contest", "code").is_duplicated().any():
        raise ValueError("a unit has votes without one set of minutes")
    parts = [
        minutes.select(
            "contest",
            "code",
            pl.lit(None, pl.String).alias("option_id"),
            pl.lit(measure, pl.Int16).alias("measure"),
            pl.col(column).alias("value"),
        )
        for measure, column in MEASURES.items()
    ]
    joined = lines.join(lookup, on=["contest", "NPOZ"], how="left")
    parts.append(
        joined.select(
            "contest", "code", "option_id", pl.lit(VOTES, pl.Int16).alias("measure"), pl.col("VVE").alias("value")
        )
    )
    stacked = pl.concat(parts).join(sets, on="contest", how="left")
    p.frame(
        "result_value",
        result_values(
            stacked.select(
                "result_set_id",
                pl.col("code").replace_strict(unit_ids).alias("unit_id"),
                "option_id",
                "measure",
                "value",
            )
        ),
    )


def outcomes(p: Package, sets: pl.DataFrame, rows: pl.DataFrame, lookup: pl.DataFrame, kind: str) -> None:
    """Seats of a council and the elected mayor or president, as the Authority prints them."""
    found = rows.with_columns(
        (pl.col("MAN_1") + pl.col("MAN_2") + pl.col("MAN_3") + pl.col("MAN_4")).alias("seats")
    ).filter(pl.col("seats") > 0)
    found = found.join(lookup, on=["contest", "NPOZ"], how="left").join(sets, on="contest", how="left")
    if found["option_id"].is_null().any():
        raise ValueError("seats for a ballot line that has no votes")
    if kind == "office" and (found["seats"] != 1).any():
        raise ValueError("an office with more than one winner")
    p.frame(
        "outcome",
        pl.DataFrame(
            {
                "result_set_id": found["result_set_id"],
                "option_id": found["option_id"],
                "kind": "elected" if kind == "office" else "won",
                "seats": found["seats"].cast(pl.Int32),
            }
        ),
    )


def totals(p: Package, sets: pl.DataFrame, rows: pl.DataFrame, lookup: pl.DataFrame) -> None:
    """The county or circumscription totals of a contest that is held by stations."""
    if not rows.height:
        return
    parts = [
        rows.filter(pl.col("minutes")).select(
            "contest",
            pl.lit(None, pl.String).alias("option_id"),
            pl.lit(measure, pl.Int16).alias("measure"),
            pl.col(column).alias("value"),
        )
        for measure, column in MEASURES.items()
    ]
    joined = rows.filter(~pl.col("minutes")).join(lookup, on=["contest", "NPOZ"], how="left")
    parts.append(
        joined.select("contest", "option_id", pl.lit(VOTES, pl.Int16).alias("measure"), pl.col("VVE").alias("value"))
    )
    stacked = pl.concat(parts).join(sets, on="contest", how="inner")
    p.frame("declared", stacked.select("result_set_id", "option_id", "measure", "value"))


def accept(p: Package) -> None:
    p.accept(
        "accounting.ballots_sum",
        "in some units the valid and null votes the minutes print do not add up to the voters who took part; "
        "stored as printed",
    )
    p.accept(
        "accounting.turnout",
        "some units print more voters than registered voters on the lists, mostly on special and supplementary "
        "lists; stored as printed",
    )


# ---------------------------------------------------------------- parliamentary election

CHAMBERS = {
    "CAMERA DEPUTATILOR": (aep.CHAMBER, "Chamber of Deputies", "Camera Deputaților", "chamber", "cd"),
    "SENAT": (aep.SENATE, "Senate", "Senat", "senate", "s"),
}
STATION_MEASURES = {
    REGISTERED: "NrAlegatoriListe",
    CAST: "NrAlegatoriPrezentatiLaUrne",
    VALID: "TotalVoturiValabilExprimateLaNivelSectie",
    INVALID: "NrVoturiNule",
    BLANK: "NrVoturiAlbe",
}


def mandates() -> pl.DataFrame:
    """The candidates who took a seat, from the Authority's candidate table."""
    frame = sheet("candidates")
    senate = frame["Colegiu senat"].is_not_null()
    return (
        frame.filter(pl.col("Mandat Atribuit") == "1")
        .select(
            pl.col("Nr circumscriptie").cast(pl.Int64).alias("circ"),
            pl.when(pl.col("Colegiu senat").is_not_null())
            .then(pl.lit("SENAT"))
            .otherwise(pl.lit("CAMERA DEPUTATILOR"))
            .alias("chamber"),
            pl.coalesce("Colegiu senat", "Colegiu Deputati").cast(pl.Int64).alias("college"),
            pl.col("NUME").map_elements(tidy, return_dtype=pl.String).alias("name"),
            pl.col("NumeCompetitor").map_elements(tidy, return_dtype=pl.String).alias("party"),
        )
        .with_columns(pl.lit(senate.sum() >= 0).alias("_"))
    )


def college_totals() -> pl.DataFrame:
    frame = sheet("colleges")
    return frame.select(
        pl.col("Nr Circumscriptie").cast(pl.Int64).alias("circ"),
        pl.col("Nr. Colegiu").cast(pl.Int64).alias("college"),
        pl.col("Tip Colegiu").str.strip_chars().alias("kind"),
        pl.col("Nr Alegatori Inscrisi In Liste").cast(pl.Int64).alias(str(REGISTERED)),
        pl.col("Nr Alegatori prezenti la urne").cast(pl.Int64).alias(str(CAST)),
        pl.col("Nr voturi valabil exprimate").cast(pl.Int64).alias(str(VALID)),
        pl.col("Nr Voturi Nule").cast(pl.Int64).alias(str(INVALID)),
        pl.col("Nr voturi albe").cast(pl.Int64).alias(str(BLANK)),
        pl.col("Pozitie Buletin Vot").cast(pl.Int64).alias("position"),
        pl.col("Voturi Obtinute Candidat").cast(pl.Int64).alias("votes"),
    )


def build_parliament() -> Package:
    day = PARLIAMENT
    p = Package(
        f"ro.aep/{day}",
        SOURCE,
        "The Romanian parliamentary election of 9 December 2012 by polling station and college, from the Permanent "
        "Electoral Authority's open data",
        depends=["ro.aep/registry", "ro.aep/2012-registry"],
    )
    p.external.add(ROMANIA)
    for name in ("stations", "colleges", "circumscriptions", "candidates"):
        p.artifact(raw.cached(SOURCE, URLS[name]), aep.AEP_SOURCE)
    data = stations_all().with_columns(
        pl.col("numeTipColegiu").replace_strict({k: v[4] for k, v in CHAMBERS.items()}).alias("ch"),
        pl.format("{}:{}/{}", pl.lit(day), pl.col("numarCircumscriptie"), pl.col("numarSectie")).alias("code"),
        pl.format(
            "ps2012-{}-{}-{}",
            pl.col("numeTipColegiu").replace_strict({k: v[4] for k, v in CHAMBERS.items()}),
            pl.col("numarCircumscriptie"),
            pl.col("numarColegiu"),
        ).alias("contest"),
    )
    stations = data.group_by("code", maintain_order=True).agg(
        pl.col("numarCircumscriptie").first(),
        pl.col("numeCircumscriptie").first(),
        pl.col("numarSectie").first(),
        pl.col("Localitate").n_unique().alias("places"),
        pl.col("Localitate").first(),
    )
    if (stations["places"] > 1).any():
        raise ValueError("a polling station has two localities")
    abroad = stations["numarCircumscriptie"] == ABROAD
    unit_frames(
        p,
        pl.DataFrame(
            {
                "code": stations["code"],
                "scheme": "ro.sv",
                "kind": ["abroad_mission" if a else "standard" for a in abroad],
                "name": [
                    f"{place.title()} (abroad, station {nr})"
                    if a
                    else f"{place.title()} ({county.title()}, station {nr})"
                    for place, county, nr, a in zip(
                        stations["Localitate"],
                        stations["numeCircumscriptie"],
                        stations["numarSectie"],
                        abroad,
                        strict=True,
                    )
                ],
            }
        ),
    )
    unit_ids = {c: key("unit", c) for c in stations["code"]}
    labels = label_pool()
    del labels
    election = key("election", f"ps{day}")
    p.add(
        "election",
        id=election,
        polity_id=ROMANIA,
        kind="general",
        date_from=DATES[day],
        date_to=DATES[day],
        status="held",
    )
    p.add("slug", path=f"ro/{day}-general", entity_id=election, is_current=True)
    p.name(election, "2012 Romanian parliamentary election", "en", "official")
    p.name(election, "Alegeri parlamentare din 9 decembrie 2012", "ro", "native", False)
    seats = mandates()
    placed = 0
    for chamber, (body, en, ro, slug, short) in CHAMBERS.items():
        part = data.filter(pl.col("numeTipColegiu") == chamber)
        found = part.group_by("contest", maintain_order=True).agg(
            pl.col("numarCircumscriptie").first().alias("circ"),
            pl.col("numeCircumscriptie").first().alias("county"),
            pl.col("numarColegiu").first().alias("college"),
        )
        names_en, names_ro, slugs = [], [], []
        for circ, county, college in zip(found["circ"], found["county"], found["college"], strict=True):
            where = "Abroad" if circ == ABROAD else county.title().replace("Mun. ", "")
            where_ro = "Străinătate" if circ == ABROAD else where
            names_en.append(f"2012 Romanian {en} election, {where}, college {college}")
            names_ro.append(f"Alegeri pentru {ro}, {where_ro}, colegiul {college}, 2012")
            slugs.append(f"{slug}/{circ}/{college}")
        contests = pl.DataFrame(
            {
                "key": found["contest"],
                "en": names_en,
                "ro": names_ro,
                "slug": slugs,
                "note": PARLIAMENT_METHOD,
            }
        )
        contest_frames(
            p,
            election,
            contests,
            {
                "kind": "body_seats",
                "body_id": body,
                "round_number": 1,
                "default_result_kind": "counted",
            },
            f"ro/{day}-general",
        )
        lines = part.group_by(["contest", "pozitieBuletin"], maintain_order=True).agg(
            pl.col("NumeCandidat").first(),
            pl.col("numeCompetitor").first(),
            pl.col("numarCircumscriptie").first().alias("circ"),
            pl.col("numarColegiu").first().alias("college"),
        )
        lines = lines.with_columns(
            pl.struct("contest", "pozitieBuletin")
            .map_elements(lambda r: key("option", r["contest"], r["pozitieBuletin"]), return_dtype=pl.String)
            .alias("option_id"),
            (pl.col("numeCompetitor") == "NULL").alias("independent"),
        )
        p.frame(
            "contest_option",
            pl.DataFrame(
                {
                    "id": lines["option_id"],
                    "contest_id": [key("contest", c) for c in lines["contest"]],
                    "kind": "candidate",
                    "party_id": [
                        None if i else party_id(label)
                        for i, label in zip(lines["independent"], lines["numeCompetitor"], strict=True)
                    ],
                    "ballot_position": lines["pozitieBuletin"].cast(pl.Int32),
                    "ballot_label": lines["NumeCandidat"].map_elements(str.title, return_dtype=pl.String),
                }
            ),
        )
        person_frames(p, lines.select("option_id", pl.col("NumeCandidat").alias("name"), "independent"))
        lookup = lines.select("contest", pl.col("pozitieBuletin").alias("NPOZ"), "option_id")
        sets = pl.DataFrame(
            {"contest": found["contest"], "result_set_id": [key("result_set", c) for c in found["contest"]]}
        )
        parts = [
            part.group_by(["contest", "code"], maintain_order=True)
            .agg(pl.col(column).first().alias("value"))
            .select(
                "contest",
                "code",
                pl.lit(None, pl.String).alias("option_id"),
                pl.lit(measure, pl.Int16).alias("measure"),
                "value",
            )
            for measure, column in STATION_MEASURES.items()
        ]
        votes = part.join(lookup.rename({"NPOZ": "pozitieBuletin"}), on=["contest", "pozitieBuletin"], how="left")
        parts.append(
            votes.select(
                "contest",
                "code",
                "option_id",
                pl.lit(VOTES, pl.Int16).alias("measure"),
                pl.col("VoturiCandidat").alias("value"),
            )
        )
        stacked = pl.concat(parts).join(sets, on="contest", how="left")
        p.frame(
            "result_value",
            result_values(
                stacked.select(
                    "result_set_id",
                    pl.col("code").replace_strict(unit_ids).alias("unit_id"),
                    "option_id",
                    "measure",
                    "value",
                )
            ),
        )
        won = seats.filter(pl.col("chamber") == chamber).join(
            lines.with_columns(
                pl.col("NumeCandidat").alias("name"),
                pl.when(pl.col("independent")).then(pl.lit("NULL")).otherwise(pl.col("numeCompetitor")).alias("party"),
            ).select("circ", "college", "name", "party", "option_id", "contest"),
            on=["circ", "college", "name", "party"],
            how="left",
        )
        if won["option_id"].is_null().any():
            raise ValueError(
                f"{chamber}: mandates for candidates without a ballot line: {won.filter(pl.col('option_id').is_null()).head(3).to_dicts()}"
            )
        placed += won.height
        p.frame(
            "outcome",
            pl.DataFrame(
                {
                    "result_set_id": [key("result_set", c) for c in won["contest"]],
                    "option_id": won["option_id"],
                    "kind": "elected",
                    "seats": 1,
                }
            ).with_columns(pl.col("seats").cast(pl.Int32)),
        )
        declared_rows(p, chamber, lines, lookup)
    if placed != seats.height:
        raise ValueError(f"{placed} of {seats.height} mandates placed")
    accept(p)
    return p


def declared_rows(p: Package, chamber: str, lines: pl.DataFrame, lookup: pl.DataFrame) -> None:
    """The college totals the Authority prints."""
    frame = college_totals().filter(pl.col("kind") == CHAMBERS[chamber][4])
    frame = frame.with_columns(
        pl.format("ps2012-{}-{}-{}", pl.lit(CHAMBERS[chamber][4]), pl.col("circ"), pl.col("college")).alias("contest")
    )
    joined = frame.join(lookup.rename({"NPOZ": "position"}), on=["contest", "position"], how="left")
    if joined["option_id"].is_null().any():
        raise ValueError(f"{chamber}: declared votes for a candidate without a ballot line")
    parts = [
        frame.unique("contest").select(
            "contest",
            pl.lit(None, pl.String).alias("option_id"),
            pl.lit(measure, pl.Int16).alias("measure"),
            pl.col(str(measure)).alias("value"),
        )
        for measure in STATION_MEASURES
    ]
    parts.append(
        joined.select("contest", "option_id", pl.lit(VOTES, pl.Int16).alias("measure"), pl.col("votes").alias("value"))
    )
    stacked = pl.concat(parts)
    stacked = stacked.with_columns(
        pl.col("contest").map_elements(lambda c: key("result_set", c), return_dtype=pl.String).alias("result_set_id")
    )
    p.frame("declared", stacked.select("result_set_id", "option_id", "measure", "value"))


def build(key_: str) -> Package:
    if key_ == "registry":
        return registry()
    if key_ == PARLIAMENT:
        return build_parliament()
    return build_local(key_)


def parse() -> None:
    for name in ("registry", FIRST, SECOND, PARLIAMENT):
        build(name).write()
