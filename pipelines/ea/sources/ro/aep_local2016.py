"""Romanian local elections of 5 June 2016, with the repeat of two mayoral contests on 19 June.

The Permanent Electoral Authority publishes the elections as two data.gov.ro datasets of the same three tables: the
minutes at the level of the electoral circumscription and the polling station (`pvpag1`), the same minutes with the
votes of every list and candidate (`pvpag2`), and the seats allocated (`mandate`). The first dataset holds the count
of 5 June. The second repeats it, except that the mayoral contests of the two communes whose top two candidates
tied, Vâlcelele (Buzău) and Șag (Timiș), carry the repeated vote instead. So the first dataset builds the election of
5 June, and the rows that differ in the second build the repeat as a separate election that reruns the first.

Council and mayoral contests are counted per circumscription (one per commune, town, or municipality), and the
county councils per polling station. Where the Authority also prints polling stations for a contest, as it does for
the six sectors of Bucharest and the General Council and General Mayor of Bucharest, the contest holds the stations
and the circumscription totals are stored as declared totals.
"""

from datetime import date

import polars as pl

from ea import raw
from ea.package import Package
from ea.sources.ro import aep
from ea.sources.ro.aep import (
    CAST,
    INVALID,
    REGISTERED,
    ROMANIA,
    SOURCE,
    VALID,
    VOTES,
    canon,
    independent,
    key,
)

FIRST = "07bf7437-070a-4faa-892f-f4029879c133"
SECOND = "ef4e5fdd-a5e7-432d-8a2e-369f86edc780"
URLS = {
    "mandate": f"{aep.HOST}/{FIRST}/resource/4adfba52-aa4b-41e2-8a1e-54d987614b68/download/exportmandate.csv",
    "pag1": f"{aep.HOST}/{FIRST}/resource/1bf4bbfc-49f0-4348-b0f0-a40eb26395fd/download/exportpvpag1.csv",
    "pag2": f"{aep.HOST}/{FIRST}/resource/1fea2b47-d88e-40e0-889d-01e7852adabc/download/exportpvpag2.csv",
    "legend": f"{aep.HOST}/{FIRST}/resource/4b7bb8be-07fd-4122-926d-8c16495777ae/download/alegerilocale2016.txt",
    "mandate-2": f"{aep.HOST}/{SECOND}/resource/b13bd0f6-8689-499e-bb1c-209d15f3856e/download/exportmandate-2.csv",
    "pag1-2": f"{aep.HOST}/{SECOND}/resource/e453e3a6-c14c-4d69-ac8b-ace9890b93e4/download/exportpvpag1-2.csv",
    "pag2-2": f"{aep.HOST}/{SECOND}/resource/41de9fd8-0861-4496-b73d-12db70096209/download/exportpvpag2-2-1.csv",
}
DAY = "2016-06-05"
RERUN = "2016-06-19"
DATES = {DAY: date(2016, 6, 5), RERUN: date(2016, 6, 19)}
PACKAGES = [f"ro.aep/{DAY}", f"ro.aep/{RERUN}"]
NUMBER = {"a": REGISTERED, "b": CAST, "c": VALID, "d": INVALID}
TIPS = {
    "CJ": ("list", "county", aep.COUNTY_COUNCIL, aep.SYSTEM_LIST, "County council", "Consiliul județean"),
    "CGMB": (
        "list",
        "all",
        aep.BUCHAREST_COUNCIL,
        aep.SYSTEM_LIST,
        "General Council of Bucharest",
        "Consiliul General al Municipiului București",
    ),
    "CL": ("list", "uat", aep.LOCAL_COUNCIL, aep.SYSTEM_LIST, "Local council", "Consiliul local"),
    "P": ("office", "uat", aep.MAYOR, aep.SYSTEM_PLURALITY, "Mayor", "Primar"),
    "PG": (
        "office",
        "all",
        aep.GENERAL_MAYOR,
        aep.SYSTEM_PLURALITY,
        "General Mayor of Bucharest",
        "Primarul General al Municipiului București",
    ),
}
SLUGS = {
    "CJ": "county-council",
    "CGMB": "bucharest-council",
    "CL": "local-council",
    "P": "mayor",
    "PG": "general-mayor",
}
METHOD = (
    "Counted per circumscription (a commune, town, or municipality) or per polling station and published by the "
    "Permanent Electoral Authority as open data. Registered voters are the voters on the electoral lists, ballots "
    "cast are the voters who took part, valid votes are the valid votes, and invalid ballots are the null votes. "
    "The file prints the party or alliance a mayoral candidate ran for, and prints a candidate's name only for "
    "independents and for the candidates elected."
)
TIE = (
    "The two leading candidates polled the same number of votes, so the Authority declared no mayor on 5 June. The "
    "vote was repeated in this commune on 19 June 2016."
)
RERUN_NOTE = (
    "The vote repeated on 19 June 2016 after the two leading candidates tied on 5 June. The Authority publishes the "
    "result per circumscription."
)


def fetch() -> None:
    for url in URLS.values():
        raw.fetch(SOURCE, url, suffix=".txt" if url.endswith(".txt") else ".csv")


def parse() -> None:
    first, rerun = build_first(), build_rerun()
    first.write()
    rerun.write()


def tidy(column: str) -> pl.Expr:
    expr = pl.col(column)
    for old, new in (("Ş", "Ș"), ("ş", "ș"), ("Ţ", "Ț"), ("ţ", "ț")):
        expr = expr.str.replace_all(old, new, literal=True)
    return expr.str.replace_all(r"\s+", " ").str.strip_chars().alias(column)


def table(name: str) -> pl.DataFrame:
    text = raw.cached(SOURCE, URLS[name]).path.read_bytes().decode("utf-8").replace("﻿", "")
    frame = pl.read_csv(text.encode(), infer_schema_length=0, truncate_ragged_lines=True)
    texts = [
        c for c in frame.columns if c in ("Nivel", "Alegeri", "NumeJudet", "Localitate", "Candidatura", "Competitor")
    ]
    return frame.with_columns(*(tidy(c) for c in texts))


def tip_of() -> pl.Expr:
    return (
        pl.when(pl.col("Alegeri") == "CONSILIERI JUDETENI")
        .then(pl.when(pl.col("NrJudet") == "42").then(pl.lit("CGMB")).otherwise(pl.lit("CJ")))
        .when(pl.col("Alegeri") == "CONSILIERI LOCALI")
        .then(pl.lit("CL"))
        .when(pl.col("Alegeri") == "PRIMARI")
        .then(pl.lit("P"))
        .when(pl.col("Alegeri") == "PRIMAR GENERAL")
        .then(pl.lit("PG"))
        .otherwise(None)
        .alias("tip")
    )


def scope_of() -> pl.Expr:
    return (
        pl.when(pl.col("tip") == "CJ")
        .then(pl.col("NrJudet"))
        .when(pl.col("tip").is_in(["CL", "P"]))
        .then(pl.col("Siruta"))
        .otherwise(pl.lit("all"))
        .alias("scope")
    )


def votes_table(name: str, day: str) -> pl.DataFrame:
    """One row per unit and competitor, with the minutes of the unit repeated on each."""
    frame = table(name)
    if frame["Alegeri"].is_null().any() or frame["Nivel"].is_null().any():
        raise ValueError(f"{name}: rows without a level or an election")
    frame = frame.with_columns(
        tip_of(),
        pl.col("NrSectie").fill_null(""),
        *(pl.col(c).cast(pl.Int64) for c in ("a", "b", "c", "d")),
        pl.col("NrVoturiValabile").cast(pl.Int64).alias("votes"),
        pl.col("PozitieBuletin").cast(pl.Int64).alias("position"),
        pl.col("NumarCandidati").cast(pl.Int64).alias("candidates"),
    )
    frame = frame.with_columns(scope_of())
    frame = frame.with_columns(
        pl.format("local2016-{}-{}", pl.col("tip"), pl.col("scope")).alias("contest"),
        pl.when(pl.col("Nivel") == "SV")
        .then(pl.format("{}:SV/{}/{}", pl.lit(day), pl.col("NrJudet"), pl.col("NrSectie")))
        .otherwise(pl.format("{}:CE/{}", pl.lit(day), pl.col("Siruta")))
        .alias("code"),
        pl.col("Candidatura").map_elements(canon, return_dtype=pl.String).alias("competitor"),
    )
    keys = ["code", "contest", "competitor"]
    if frame.select(keys).is_duplicated().any():
        raise ValueError(f"{name}: a unit lists a competitor twice")
    return frame


def accounting_check(name: str, votes: pl.DataFrame) -> None:
    """The minutes in the votes table must agree with the minutes table, and the votes with the valid votes."""
    minutes = table(name).with_columns(tip_of(), pl.col("NrSectie").fill_null(""))
    minutes = minutes.with_columns(scope_of()).with_columns(
        pl.format("local2016-{}-{}", pl.col("tip"), pl.col("scope")).alias("contest"),
        *(pl.col(c).cast(pl.Int64).alias(f"{c}_1") for c in "abcd"),
    )
    on = ["Nivel", "contest", "NrJudet", "Siruta", "NrSectie"]
    units = votes.group_by(on).agg(pl.col("votes").sum().alias("sum"), *(pl.col(c).first() for c in "abcd"))
    joined = minutes.select(*on, *(f"{c}_1" for c in "abcd")).join(units, on=on, how="full", coalesce=True)
    bad = joined.filter(
        pl.any_horizontal(
            pl.col("sum").is_null(),
            pl.col("a_1").is_null(),
            pl.col("sum") != pl.col("c"),
            *(pl.col(c) != pl.col(f"{c}_1") for c in "abcd"),
        )
    )
    if bad.height:
        raise ValueError(f"{name}: {bad.height} units whose minutes and votes disagree: {bad.head(3).to_dicts()}")


def repeated() -> pl.DataFrame:
    """The rows of the second dataset that the first does not hold: the vote repeated on 19 June."""
    keys = ["Nivel", "Alegeri", "NrJudet", "Siruta", "NrSectie", "Candidatura", "NrVoturiValabile", "a", "b", "c", "d"]
    first = table("pag2").with_columns(pl.col("NrSectie").fill_null(""))
    second = table("pag2-2").with_columns(pl.col("NrSectie").fill_null(""))
    changed = second.join(first, on=keys, how="anti")
    gone = first.join(second, on=keys, how="anti")
    sirutas = sorted(set(changed["Siruta"]) | set(gone["Siruta"]))
    if not sirutas or set(changed["Alegeri"]) | set(gone["Alegeri"]) != {"PRIMARI"}:
        raise ValueError("the second dataset must differ from the first only in mayoral contests")
    if set(changed["Nivel"]) != {"CE"}:
        raise ValueError("the repeated vote must be counted per circumscription")
    return pl.DataFrame({"Siruta": sirutas})


def label_party(label: str) -> str:
    return key("party", "local2016", canon(label))


def places(votes: pl.DataFrame) -> pl.DataFrame:
    return votes.unique("contest", keep="first", maintain_order=True).select(
        "contest", "tip", "scope", "NrJudet", "NumeJudet", "Localitate", "Siruta"
    )


def units(p: Package, votes: pl.DataFrame) -> pl.DataFrame:
    """Add each unit once and return the distinct units with their ids."""
    found = votes.unique("code", keep="first", maintain_order=True)
    if votes.select("code", "Siruta").unique().height != found.height:
        raise ValueError("a unit appears under two places")
    ids = [key("unit", code) for code in found["code"]]
    sv = found["Nivel"] == "SV"
    p.frame(
        "reporting_unit",
        pl.DataFrame(
            {
                "id": ids,
                "polity_id": ROMANIA,
                "scheme_code": ["ro.sv" if s else "ro.circumscription" for s in sv],
                "code": found["code"],
                "area_relation": "unlocated",
                "kind": ["standard" if s else "aggregate" for s in sv],
            }
        ),
    )
    names = [
        f"{place.title()} (station {nr})" if s else f"{place.title()} (circumscription {circ})"
        for place, nr, circ, s in zip(
            found["Localitate"], found["NrSectie"], found["NrCircumscriptie"], sv, strict=True
        )
    ]
    p.frame(
        "name",
        pl.DataFrame(
            {
                "id": [p.id("name", unit, "ro", "official", text) for unit, text in zip(ids, names, strict=True)],
                "entity_id": ids,
                "text": names,
                "lang": "ro",
                "kind": "official",
                "is_preferred": True,
            }
        ),
    )
    return found.select("code").with_columns(pl.Series("unit", ids))


def parties(p: Package, labels: list[str]) -> None:
    for label in sorted(labels):
        id = label_party(label)
        p.add("party", id=id, kind="bloc" if canon(label).startswith("ALIAN") else "party", polity_id=ROMANIA)
        p.name(id, label, "ro", "official")


def contest_rows(
    p: Package,
    election: str,
    day: str,
    votes: pl.DataFrame,
    seats: pl.DataFrame,
    declared: pl.DataFrame | None,
    suffix: str,
    tied: set[str],
) -> None:
    """Add the contests, result sets, options, and values of `votes`, then the seats and declared totals."""
    ids = units(p, votes)
    votes = votes.join(ids, on="code", how="left")
    for tip, (kind, scope, holder, system, english, romanian) in TIPS.items():
        part = votes.filter(pl.col("tip") == tip)
        if not part.height:
            continue
        where = places(part)
        contest_ids, set_ids, names_en, names_ro, slugs, notes = [], [], [], [], [], []
        for contest, _, scope_key, county_nce, county, place, siruta in where.iter_rows():
            if scope == "county":
                label = f"{county.title()} County"
            elif scope == "uat":
                label = place.title()
            else:
                label = ""
            label_ro = label.replace(" County", "") if scope == "county" else label
            contest_ids.append(key("contest", contest + suffix))
            set_ids.append(key("result_set", contest + suffix))
            names_en.append(
                f"2016 Romanian local elections, {english}"
                + (f", {label}" if label else "")
                + (", repeat" * bool(suffix))
            )
            names_ro.append(
                romanian + (f", {label_ro}" if label_ro else "") + (", vot repetat" * bool(suffix)) + ", 2016"
            )
            slugs.append(f"ro/{day}-local/{SLUGS[tip]}/{scope_key if scope != 'all' else 'bucharest'}")
            notes.append(METHOD + (" " + RERUN_NOTE if suffix else "") + (" " + TIE if contest in tied else ""))
        fields: dict[str, object] = {
            "election_id": election,
            "kind": "list_allocation" if kind == "list" else "office",
            "round_number": 1,
            "default_result_kind": "counted",
            "electoral_system_id": system,
        }
        if kind == "list":
            fields["body_id"] = holder
        else:
            fields["office_id"] = holder
            fields["magnitude"] = 1
        p.frame("contest", pl.DataFrame({"id": contest_ids, **fields}))
        p.frame(
            "name",
            pl.DataFrame(
                {
                    "id": [p.id("name", c, lang, "official") for c in contest_ids for lang in ("en", "ro")],
                    "entity_id": [c for c in contest_ids for _ in range(2)],
                    "text": [t for pair in zip(names_en, names_ro, strict=True) for t in pair],
                    "lang": ["en", "ro"] * len(contest_ids),
                    "kind": ["official", "native"] * len(contest_ids),
                    "is_preferred": [True, False] * len(contest_ids),
                }
            ),
        )
        p.frame("slug", pl.DataFrame({"path": slugs, "entity_id": contest_ids, "is_current": True}))
        p.frame(
            "result_set",
            pl.DataFrame(
                {
                    "id": set_ids,
                    "election_id": election,
                    "contest_id": contest_ids,
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
                    "id": [key("note", r) for r in set_ids],
                    "entity_id": set_ids,
                    "kind": "methodology",
                    "body_md": notes,
                    "lang": "en",
                }
            ),
        )
        options = options_of(p, part, kind, seats.filter(pl.col("tip") == tip), suffix)
        sets = pl.DataFrame({"contest": where["contest"], "result_set_id": set_ids})
        stage(p, sets, part, options)
        outcomes(p, sets, options, seats.filter(pl.col("tip") == tip))
        if declared is not None:
            totals(p, sets, options, declared.filter(pl.col("tip") == tip))


def options_of(p: Package, part: pl.DataFrame, kind: str, seats: pl.DataFrame, suffix: str) -> pl.DataFrame:
    pairs = part.unique(["contest", "competitor"], keep="first", maintain_order=True).select(
        "contest", "competitor", "Candidatura", "CodUnic", "position", "candidates"
    )
    pairs = pairs.with_columns(
        pl.col("Candidatura").map_elements(lambda label: independent(label) or "", return_dtype=pl.String).alias("_i"),
        pl.format("{}{}", pl.col("contest"), pl.lit(suffix)).alias("_c"),
    )
    pairs = pairs.with_columns(
        (pl.col("CodUnic") == "999").alias("indie"),
        pl.struct("_c", "competitor")
        .map_elements(lambda r: key("option", r["_c"], r["competitor"]), return_dtype=pl.String)
        .alias("option_id"),
    )
    winners = seats.select("contest", "competitor", "person").filter(pl.col("person").is_not_null())
    pairs = pairs.join(winners, on=["contest", "competitor"], how="left")
    pairs = pairs.with_columns(
        pl.when(pl.col("indie")).then(pl.col("Candidatura")).otherwise(pl.col("person")).alias("who")
    )
    p.frame(
        "contest_option",
        pl.DataFrame(
            {
                "id": pairs["option_id"],
                "contest_id": [key("contest", c) for c in pairs["_c"]],
                "kind": ["candidate" if kind == "office" or i else "list" for i in pairs["indie"]],
                "party_id": [
                    None if i else label_party(label) for i, label in zip(pairs["indie"], pairs["Candidatura"])
                ],
                "ballot_position": pairs["position"],
                "ballot_label": [
                    label.title() if indie else label
                    for label, indie in zip(pairs["Candidatura"], pairs["indie"], strict=True)
                ],
            }
        ),
    )
    people = pairs.filter(pl.col("who").is_not_null())
    if people.height:
        who = [key("person", o) for o in people["option_id"]]
        p.frame("person", pl.DataFrame({"id": who}))
        p.frame(
            "name",
            pl.DataFrame(
                {
                    "id": [p.id("name", w, "ro", "official") for w in who],
                    "entity_id": who,
                    "text": [s.title() for s in people["who"]],
                    "lang": "ro",
                    "kind": "official",
                    "is_preferred": True,
                }
            ),
        )
        p.frame(
            "candidacy",
            pl.DataFrame(
                {
                    "id": [key("candidacy", o) for o in people["option_id"]],
                    "person_id": who,
                    "option_id": people["option_id"],
                    "role": "head",
                    "ballot_name": [s.title() for s in people["who"]],
                    "is_independent": people["indie"],
                }
            ),
        )
    return pairs.select("contest", "competitor", "option_id", "who")


def stage(p: Package, sets: pl.DataFrame, rows: pl.DataFrame, options: pl.DataFrame) -> None:
    accounting = rows.unique(["unit", "contest"], keep="first", maintain_order=True)
    parts = [
        accounting.select(
            "contest",
            "unit",
            pl.lit(None, pl.String).alias("option_id"),
            pl.lit(measure, pl.Int16).alias("measure"),
            pl.col(column).alias("value"),
        )
        for column, measure in NUMBER.items()
    ]
    joined = rows.join(options.select("contest", "competitor", "option_id"), on=["contest", "competitor"], how="left")
    parts.append(
        joined.select(
            "contest",
            "unit",
            "option_id",
            pl.lit(VOTES, pl.Int16).alias("measure"),
            pl.col("votes").alias("value"),
        )
    )
    stacked = pl.concat(parts).join(sets, on="contest", how="left")
    p.frame(
        "result_value",
        stacked.select(
            "result_set_id",
            pl.col("unit").alias("unit_id"),
            "option_id",
            pl.lit(0, pl.Int16).alias("channel"),
            "measure",
            pl.lit(0, pl.Int16).alias("count_round"),
            pl.lit(0, pl.Int16).alias("flags"),
            "value",
        ),
    )


def outcomes(p: Package, sets: pl.DataFrame, options: pl.DataFrame, seats: pl.DataFrame) -> None:
    """Seats and elected mayors as the Authority prints them."""
    if not seats.height:
        return
    found = seats.join(options, on=["contest", "competitor"], how="left").join(sets, on="contest", how="inner")
    missing = found.filter(pl.col("option_id").is_null() & (pl.col("seats") > 0))
    if missing.height:
        raise ValueError(f"seats for competitors without a ballot line: {missing.head(3).to_dicts()}")
    found = found.filter(pl.col("option_id").is_not_null() & (pl.col("seats") > 0))
    office = found["tip"].is_in(["P", "PG"]) if found.height else pl.Series([], dtype=pl.Boolean)
    p.frame(
        "outcome",
        pl.DataFrame(
            {
                "result_set_id": found["result_set_id"],
                "option_id": found["option_id"],
                "kind": ["elected" if o else "won" for o in office],
                "seats": found["seats"],
            }
        ),
    )


def totals(p: Package, sets: pl.DataFrame, options: pl.DataFrame, rows: pl.DataFrame) -> None:
    """The circumscription totals of a contest that is held by stations."""
    if not rows.height:
        return
    accounting = rows.unique("contest", keep="first", maintain_order=True)
    parts = [
        accounting.select(
            "contest",
            pl.lit(None, pl.String).alias("option_id"),
            pl.lit(measure, pl.Int16).alias("measure"),
            pl.col(column).alias("value"),
        )
        for column, measure in NUMBER.items()
    ]
    joined = rows.join(options.select("contest", "competitor", "option_id"), on=["contest", "competitor"], how="left")
    if joined["option_id"].is_null().any():
        raise ValueError("declared votes for a competitor without a ballot line")
    parts.append(
        joined.select("contest", "option_id", pl.lit(VOTES, pl.Int16).alias("measure"), pl.col("votes").alias("value"))
    )
    stacked = pl.concat(parts).join(sets, on="contest", how="inner")
    p.frame("declared", stacked.select("result_set_id", "option_id", "measure", "value"))


def seats_table(name: str, tip_filter: pl.Expr | None = None) -> pl.DataFrame:
    frame = table(name).with_columns(tip_of(), pl.col("Siruta").fill_null(""))
    frame = frame.with_columns(scope_of())
    frame = frame.with_columns(
        pl.format("local2016-{}-{}", pl.col("tip"), pl.col("scope")).alias("contest"),
        pl.col("Competitor").map_elements(canon, return_dtype=pl.String).alias("competitor"),
        pl.col("NumarMandate").cast(pl.Int64).alias("seats"),
        pl.col("CandidaturaPersoana").alias("person"),
    )
    if tip_filter is not None:
        frame = frame.filter(tip_filter)
    return frame.select("tip", "contest", "competitor", "seats", "person")


def finish(p: Package, names: list[str]) -> None:
    for name in names:
        p.artifact(raw.cached(SOURCE, URLS[name]), aep.AEP_SOURCE)
    p.accept(
        "accounting.ballots_sum",
        "in some units the valid votes and null votes the minutes print do not add up to the voters who took part; "
        "stored as printed",
    )


def election_rows(p: Package, day: str, english: str, romanian: str, slug: str, **fields: object) -> str:
    election = key("election", f"local{day}")
    p.add(
        "election",
        id=election,
        polity_id=ROMANIA,
        date_from=DATES[day],
        date_to=DATES[day],
        status="held",
        **fields,
    )
    p.add("slug", path=slug, entity_id=election, is_current=True)
    p.name(election, english, "en", "official")
    p.name(election, romanian, "ro", "native", False)
    return election


def build_first() -> Package:
    p = Package(
        f"ro.aep/{DAY}",
        SOURCE,
        "The Romanian local elections of 5 June 2016 by circumscription and polling station, from the Permanent "
        "Electoral Authority's open data",
        depends=["ro.aep/registry"],
    )
    p.external.add(ROMANIA)
    votes = votes_table("pag2", DAY)
    accounting_check("pag1", votes)
    rerun = repeated()["Siruta"].to_list()
    election = election_rows(
        p,
        DAY,
        "2016 Romanian local elections",
        "Alegeri locale din 5 iunie 2016",
        f"ro/{DAY}-local",
        kind="local",
    )
    labels = votes.filter(pl.col("CodUnic") != "999")["Candidatura"].unique().to_list()
    parties(p, labels)
    sv = set(votes.filter(pl.col("Nivel") == "SV")["contest"])
    stations = votes.filter(pl.col("contest").is_in(sv) & (pl.col("Nivel") == "SV"))
    circumscriptions = votes.filter(pl.col("Nivel") == "CE")
    declared = circumscriptions.filter(pl.col("contest").is_in(sv))
    main = pl.concat([stations, circumscriptions.filter(~pl.col("contest").is_in(sv))])
    seats = seats_table("mandate")
    tied = {f"local2016-P-{s}" for s in rerun}
    elected = set(seats.filter(pl.col("tip") == "P")["contest"])
    mayoral = set(votes.filter(pl.col("tip") == "P")["contest"])
    if mayoral - elected != tied:
        raise ValueError(f"mayoral contests without a declared mayor are not the repeated ones: {mayoral - elected}")
    for contest in tied:
        top = votes.filter(pl.col("contest") == contest)["votes"].sort(descending=True).to_list()
        if top[0] != top[1]:
            raise ValueError(f"{contest}: the leading candidates did not tie")
    contest_rows(p, election, DAY, main, seats, declared, "", tied)
    finish(p, ["pag1", "pag2", "mandate", "legend"])
    return p


def build_rerun() -> Package:
    p = Package(
        f"ro.aep/{RERUN}",
        SOURCE,
        "The mayoral contests of two Romanian communes repeated on 19 June 2016 after a tie, from the Permanent "
        "Electoral Authority's open data",
        depends=["ro.aep/registry", f"ro.aep/{DAY}"],
    )
    p.external.add(ROMANIA)
    sirutas = repeated()["Siruta"].to_list()
    votes = votes_table("pag2-2", RERUN)
    accounting_check("pag1-2", votes)
    votes = votes.filter((pl.col("tip") == "P") & pl.col("Siruta").is_in(sirutas))
    if votes["Nivel"].n_unique() != 1 or votes["Nivel"][0] != "CE":
        raise ValueError("the repeated vote must be counted per circumscription")
    first = key("election", f"local{DAY}")
    election = election_rows(
        p,
        RERUN,
        "2016 Romanian local elections, repeat of two mayoral contests",
        "Alegeri locale, vot repetat pentru primar, 19 iunie 2016",
        f"ro/{RERUN}-local",
        kind="special",
        rerun_of_id=first,
    )
    seats = seats_table("mandate-2", (pl.col("tip") == "P") & pl.col("Siruta").is_in(sirutas))
    if seats.height != len(sirutas):
        raise ValueError("every repeated mayoral contest needs one elected mayor")
    contest_rows(p, election, RERUN, votes, seats, None, "-rerun", set())
    finish(p, ["pag1-2", "pag2-2", "mandate-2"])
    return p
