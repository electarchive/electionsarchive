"""Spain's elections from the Ministry of the Interior's data files, down to the polling station.

One package per election date. A date's package holds every election the ministry publishes for that day: Congress
and Senate (a general election), municipal councils and island councils (local elections), the European Parliament,
or a referendum. Each contest's default result set sits on the finest unit the files give: the polling station (mesa)
where they list mesas, the municipality where they stop there, otherwise the constituency. The ministry's own
constituency totals are stored as declared values, so the package check compares them with the units' sums.
"""

import io
import re
import unicodedata
import zipfile
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from uuid import uuid5

import httpx
import polars as pl

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.es.infoelectoral import APLIEXTR, COUNTS, DOCXL, LAYOUTS, PAGE

SOURCE = "es.interior"
RAW = "es.infoelectoral"
SPAIN = str(uuid5(package.NAMESPACE, "polity\x1fES"))
TERRITORY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fESP"))
PUBLISHER = "Ministerio del Interior"
TITLE = "Resultados electorales (ficheros de datos de infoelectoral)"
KINDS = {"01": "referendum", "02": "congress", "03": "senate", "04": "municipal", "06": "cabildo", "07": "european"}
EXTENTS = ("MESA", "MUNI", "TOTA")
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
ABROAD = 4
ISLANDS = {"07", "35", "38"}
SINGLE = {"51", "52"}
REFERENDUMS = {
    date(1976, 12, 15): (
        "Law for Political Reform",
        "Referéndum sobre la Ley para la Reforma Política",
        "mandatory_referendum",
    ),
    date(1978, 12, 6): ("Spanish Constitution", "Referéndum sobre la Constitución Española", "mandatory_referendum"),
    date(1986, 3, 12): (
        "Spain's membership of the North Atlantic Treaty Organization",
        "Referéndum sobre la permanencia de España en la OTAN",
        "optional_referendum",
    ),
    date(2005, 2, 20): (
        "Treaty establishing a Constitution for Europe",
        "Referéndum sobre el Tratado por el que se establece una Constitución para Europa",
        "optional_referendum",
    ),
}
METHOD = (
    "Counted by polling station (mesa) in the Ministry of the Interior's data files. Registered voters are the census "
    "used in the count, valid ballots are the votes to candidacies, and blank and null ballots are stored apart. "
    "Spanish electoral law counts blank ballots as valid; the files give them separately. A line with no votes is not "
    "stored. The ministry's totals for the constituency are stored as declared values."
)
SYSTEMS: dict[str, tuple[dict[str, object], str, str]] = {
    "congress": (
        {
            "formula": "dhondt",
            "lists": {"order": "closed", "vacancy": "next_on_list"},
            "threshold": {"level": "list", "scope": "contest", "percent": 3.0},
        },
        "Congreso de los Diputados closed-list D'Hondt system",
        (
            "Closed party lists in 50 provincial constituencies; seats go by the D'Hondt method to the lists with at "
            "least 3% of the valid votes in the province, blank ballots included (Ley Orgánica 5/1985, articles 161 to "
            "163; in 1977 and 1979, Real Decreto-ley 20/1977)."
        ),
    ),
    "congress-single": (
        {"formula": "fptp"},
        "Congreso de los Diputados single-seat plurality system",
        "Ceuta and Melilla each elect one deputy, and the candidacy with the most votes wins the seat.",
    ),
    "senate": (
        {"formula": "limited"},
        "Senado limited vote",
        (
            "Each voter marks up to three candidates in a province (two in the larger islands, one in the smaller ones) and "
            "the candidates with the most votes win the seats."
        ),
    ),
    "senate-block": (
        {"formula": "block"},
        "Senado block vote in Ceuta and Melilla",
        "Ceuta and Melilla each elect two senators; each voter marks up to two candidates.",
    ),
    "senate-single": (
        {"formula": "fptp"},
        "Senado single-seat plurality in the smaller islands",
        "The smaller islands each elect one senator, and the candidate with the most votes wins the seat.",
    ),
    "municipal": (
        {
            "formula": "dhondt",
            "lists": {"order": "closed", "vacancy": "next_on_list"},
            "threshold": {"level": "list", "scope": "contest", "percent": 5.0},
        },
        "Spanish municipal closed-list D'Hondt system",
        (
            "Municipal councils are elected from closed lists in one constituency; seats go by the D'Hondt method to the "
            "lists with at least 5% of the valid votes (Ley Orgánica 5/1985, articles 179 and 180)."
        ),
    ),
    "municipal-open": (
        {"formula": "limited"},
        "Spanish municipal open-list limited vote",
        (
            "Municipalities of up to 250 residents elect their councillors from open lists; each voter marks a limited "
            "number of candidates and those with the most votes win (Ley Orgánica 5/1985, article 184)."
        ),
    ),
    "concejo-abierto": (
        {"formula": "fptp"},
        "Spanish open-council mayoral plurality",
        "Municipalities under the open council regime elect their mayor directly.",
    ),
    "cabildo": (
        {
            "formula": "dhondt",
            "lists": {"order": "closed", "vacancy": "next_on_list"},
            "threshold": {"level": "list", "scope": "contest", "percent": 5.0},
        },
        "Canarian cabildo closed-list D'Hondt system",
        (
            "Each Canary island elects its cabildo from closed lists, with seats by the D'Hondt method to the lists with at "
            "least 5% of the valid votes in the island."
        ),
    ),
    "european": (
        {"formula": "dhondt", "lists": {"order": "closed", "vacancy": "next_on_list"}},
        "European Parliament closed-list D'Hondt system in Spain",
        (
            "Spain elects its members of the European Parliament from closed lists in one national constituency, with seats "
            "by the D'Hondt method."
        ),
    ),
    "referendum": ({"formula": "referendum"}, "Spanish national referendum", "A national yes-or-no vote."),
}
Frame = pl.DataFrame


def uid(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return uid("area", scheme, code)


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().upper()
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", plain).split())


def tidy(text: str) -> str:
    return " ".join(text.split())


def title(text: str) -> str:
    """The files print some names in capitals; show those in title case."""
    text = tidy(text)
    if any(c.islower() for c in text):
        return text
    small = {"de", "del", "la", "las", "el", "los", "y", "e", "i", "d", "l", "en", "a", "o"}
    return " ".join(w.lower() if n and w.lower() in small else w.capitalize() for n, w in enumerate(text.split()))


def section_of(field_: str) -> str:
    found = re.fullmatch(r"(\d+)([A-Z ]?)", field_)
    if found is None:
        return field_.strip()
    return f"{int(found[1]):03d}{found[2].strip()}"


# raw files ---------------------------------------------------------------------------------------------------------


PUBLISHED = {
    "01": ["197812", "198603", "200502"],
    "02": [
        "197706", "197903", "198210", "198606", "198910", "199306", "199603", "200003",
        "200403", "200803", "201111", "201512", "201606", "201904", "201911", "202307",
    ],
    "04": [
        "197904", "198305", "198706", "199105", "199505", "199906", "200305", "200705",
        "201105", "201505", "201905", "202305",
    ],
    "06": [
        "197904", "198706", "199105", "199505", "199906", "200305", "200705", "201105",
        "201505", "201905", "202305",
    ],
    "07": ["198706", "198906", "199406", "199906", "200406", "200906", "201405", "201905", "202406"],
}  # fmt: skip
PUBLISHED["03"] = PUBLISHED["02"]


def fetch() -> None:
    """Every file the ministry's download area lists; one it does not publish answers 404 and is skipped."""
    for kind, processes in PUBLISHED.items():
        for process in processes:
            for extent in EXTENTS:
                url = f"{APLIEXTR}{kind}{process}_{extent}.zip"
                if url in raw.keys(RAW):
                    continue
                try:
                    raw.fetch(RAW, url, suffix=".zip")
                except httpx.HTTPStatusError as failure:
                    if failure.response.status_code not in (403, 404):
                        raise
                    print(f"{url}: {failure.response.status_code}", flush=True)
    url = DOCXL + "PROV_01_197612_1.zip"
    if prov1976() is None:
        raw.fetch(RAW, url, suffix=".zip")


@cache
def held() -> dict[tuple[str, str], dict[str, str]]:
    found: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    for url in raw.keys(RAW):
        match = re.search(r"/(\d\d)(\d{6})_(MESA|MUNI|TOTA)\.zip$", url)
        if match is not None and match[1] in KINDS:
            found[match[1], match[2]][match[3]] = url
    return dict(sorted(found.items(), key=lambda item: (item[0][1], item[0][0])))


def artifacts(kind: str, process: str) -> list[raw.Artifact]:
    return [raw.cached(RAW, url) for url in held()[kind, process].values()]


def member(kind: str, process: str, number: str) -> str:
    """A data file's text from the largest archive that holds it; the MESA archives hold everything the others do."""
    for extent in EXTENTS:
        url = held()[kind, process].get(extent)
        if url is None:
            continue
        with zipfile.ZipFile(raw.cached(RAW, url).path) as bundle:
            name = f"{number}{kind}{process[2:]}.DAT"
            if name in bundle.namelist():
                text = bundle.read(name).decode("latin-1")
                if text.strip():
                    return text
    return ""


def read(kind: str, process: str, number: str, layout: tuple[tuple[str, int, int], ...] | None = None) -> pl.DataFrame:
    text = member(kind, process, number)
    if not text:
        return pl.DataFrame()
    frame = pl.DataFrame({"line": text.split("\n")}).filter(pl.col("line").str.strip_chars().str.len_chars() > 0)
    return frame.select(
        (
            pl.col("line")
            .str.slice(start - 1, end - start + 1)
            .str.strip_chars()
            .cast(pl.Int64, strict=False)
            .fill_null(0)
            if name in COUNTS or name in {"yes", "no"}
            else pl.col("line").str.slice(start - 1, end - start + 1).str.strip_chars()
        ).alias(name)
        for name, start, end in layout or LAYOUTS[number]
    )


@cache
def day(kind: str, process: str) -> date:
    text = member(kind, process, "02")
    return date(int(text[16:20]), int(text[14:16]), int(text[12:14]))


def calendar() -> dict[date, dict[str, list[str]]]:
    found: dict[date, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for kind, process in held():
        found[election_day(kind, process)][kind].append(process)
    return dict(sorted(found.items()))


DAYS = {("07", "198706"): date(1987, 6, 10)}  # the control file prints 13 June; the election was held on the 10th


def election_day(kind: str, process: str) -> date:
    return DAYS.get((kind, process)) or day(kind, process)


# places and parties ------------------------------------------------------------------------------------------------


def real() -> pl.Expr:
    return (pl.col("community") != "99") & (pl.col("province") != "99")


@cache
def places() -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, str]]:
    """Communities, provinces, islands, and municipalities with the latest name the files print for each."""
    communities: dict[str, str] = {}
    provinces: dict[str, str] = {}
    islands: dict[str, str] = {}
    municipalities: dict[str, str] = {}
    belongs: dict[str, str] = {}
    for kind, process in held():
        if kind == "01" and process in {"197812"}:
            pass
        totals = read(kind, process, "07")
        if totals.height:
            for row in totals.iter_rows(named=True):
                text = title(str(row["name"]))
                if row["community"] != "99" and row["province"] == "99":
                    communities[row["community"]] = text
                elif row["province"] != "99" and kind in {"03", "06"} and row["district"] not in {"9", "0", ""}:
                    islands[f"{row['province']}{row['district']}"] = text
                elif row["province"] != "99" and row["community"] != "99" and kind in {"01", "02"}:
                    provinces[row["province"]] = text
                    belongs[row["province"]] = row["community"]
        if kind == "01" and process == "197812":
            continue
        for row in read(kind, process, "05").iter_rows(named=True) if member(kind, process, "05") else []:
            if row["municipal_district"] == "99" and row["province"] != "99":
                municipalities[f"{row['province']}{row['municipality']}"] = title(str(row["name"]))
        for row in read(kind, process, "11").iter_rows(named=True) if member(kind, process, "11") else []:
            municipalities[f"{row['province']}{row['municipality']}"] = title(str(row["name"]))
    return communities, provinces, islands, municipalities


@cache
def belonging() -> dict[str, str]:
    found: dict[str, str] = {}
    for kind, process in held():
        if kind == "02":
            for row in read(kind, process, "07").iter_rows(named=True):
                if row["province"] != "99" and row["community"] != "99":
                    found[row["province"]] = row["community"]
    return found


@cache
def municipality_codes() -> set[str]:
    found: set[str] = set()
    for kind, process in held():
        for number in ("09", "05", "11"):
            if not member(kind, process, number):
                continue
            frame = read(kind, process, number)
            found |= set(
                frame.filter(pl.col("province") != "99")
                .filter(pl.col("municipality") != "999")
                .select((pl.col("province") + pl.col("municipality")).alias("code"))["code"]
            )
    return found


def lists(kind: str, process: str) -> pl.DataFrame:
    frame = read(kind, process, "03")
    if not frame.height:
        return frame
    frame = frame.with_columns(
        label=pl.col("name").map_elements(tidy, return_dtype=pl.String),
        short=pl.col("abbrev").map_elements(tidy, return_dtype=pl.String),
    )
    head = frame.select(
        pl.col("list").alias("national_head"), pl.col("label").alias("head_label"), pl.col("short").alias("head_short")
    )
    return (
        frame.join(head, on="national_head", how="left")
        .with_columns(
            head_label=pl.coalesce("head_label", "label"),
            head_short=pl.coalesce("head_short", "short"),
        )
        .with_columns(
            party=pl.concat_str(
                pl.col("head_short").map_elements(fold, return_dtype=pl.String),
                pl.col("head_label").map_elements(fold, return_dtype=pl.String),
                separator="|",
            )
        )
    )


@cache
def parties() -> dict[str, tuple[str, str, bool]]:
    """Source-local parties: one per distinct pair of abbreviation and name, taken from the head of a coalition."""
    found: dict[str, tuple[str, str, bool]] = {}
    for kind, process in held():
        frame = lists(kind, process)
        for row in frame.iter_rows(named=True):
            found.setdefault(row["party"], (row["head_short"], row["head_label"], kind in {"04", "06"}))
    return found


def party_id(key: str) -> str:
    return uid("party", key)


# contests ------------------------------------------------------------------------------------------------------------


@dataclass
class Part:
    """One kind of election held on a day, normalized: its contests, the declared totals, lists, and units."""

    tag: str
    group: str  # election group: general, local, european, referendum
    contests: Frame  # ckey, name, seats, scheme, acode, system, kind, official
    declared: Frame  # ckey, census, blank, invalid, list_votes
    dvotes: Frame  # ckey, opt, votes, seats
    options: Frame  # ckey, opt, list (the list whose label and party the option carries)
    cands: Frame  # ckey, opt, position, kind, name, sex, elected
    units: Frame  # ucode, ckey, ucat, muni, census, blank, invalid, list_votes, official, channel
    uvotes: Frame  # ucode, opt, votes
    lists: Frame
    artifacts: list[raw.Artifact] = field(default_factory=list)
    note: str = METHOD
    ballots: bool = False


def empty(**schema: pl.DataType | type[pl.DataType]) -> Frame:
    return pl.DataFrame(schema=schema)


def mesa_code() -> pl.Expr:
    return (
        pl.when(pl.col("municipality") == "999")
        .then(pl.concat_str("province", pl.lit("999"), "municipal_district", pl.lit("0000"), "mesa"))
        .otherwise(pl.concat_str("province", "municipality", "municipal_district", "section", "mesa"))
        .alias("ucode")
    )


def sections(frame: Frame) -> Frame:
    if not frame.height:
        return frame
    known = {value: section_of(value) for value in frame["section"].unique().to_list()}
    return frame.with_columns(pl.col("section").replace_strict(known, return_dtype=pl.String))


def mesas(
    kind: str,
    process: str,
    place: pl.Expr,
    on: dict[str, str] | None = None,
    channel_abroad: bool = True,
) -> tuple[Frame, Frame]:
    """Polling-station units and their list votes; `place` names the contest each mesa belongs to."""
    totals = read(kind, process, "09")
    votes = read(kind, process, "10")
    if not totals.height or not votes.height:
        return empty(), empty()
    totals = sections(totals.filter(real()))
    votes = sections(votes.filter(real()))
    cera = pl.col("municipality") == "999"
    units = totals.select(
        mesa_code(),
        place.alias("ckey"),
        pl.when(cera).then(pl.lit("cera")).otherwise(pl.lit("mesa")).alias("ucat"),
        pl.when(cera).then(None).otherwise(pl.col("province") + pl.col("municipality")).alias("muni"),
        "province",
        "census",
        "blank",
        "invalid",
        "list_votes",
        (pl.col("official") == "S").alias("official"),
        pl.when(cera & pl.lit(channel_abroad)).then(ABROAD).otherwise(0).cast(pl.Int16).alias("channel"),
    )
    cast = votes.select(mesa_code(), pl.col("list").alias("opt"), "votes").filter(pl.col("votes") > 0)
    return units, cast


def real_rows(frame: Frame) -> Frame:
    return frame.filter(real()) if frame.height else frame


def part_congress(process: str) -> Part:
    kind = "02"
    inside = lists(kind, process)
    totals = real_rows(read(kind, process, "07"))
    contests = totals.select(
        pl.col("province").alias("ckey"),
        pl.col("name").map_elements(title, return_dtype=pl.String),
        "seats",
        pl.lit("es.interior_provincia").alias("scheme"),
        pl.col("province").alias("acode"),
        pl.when(pl.col("province").is_in(SINGLE))
        .then(pl.lit("congress-single"))
        .otherwise(pl.lit("congress"))
        .alias("system"),
        pl.lit("list_allocation").alias("kind"),
        (pl.col("official") == "S").alias("official"),
    )
    declared = totals.select(pl.col("province").alias("ckey"), "census", "blank", "invalid", "list_votes")
    dvotes = real_rows(read(kind, process, "08")).select(
        pl.col("province").alias("ckey"), pl.col("list").alias("opt"), "votes", "seats"
    )
    cands = read(kind, process, "04").select(
        pl.col("province").alias("ckey"),
        pl.col("list").alias("opt"),
        "position",
        "kind",
        pl.concat_str("given", "surname", "second_surname", separator=" ").alias("name"),
        "sex",
        (pl.col("elected") == "S").alias("elected"),
    )
    units, uvotes = mesas(kind, process, pl.col("province"))
    if uvotes.height:
        uvotes = uvotes.with_columns(opt=pl.col("opt") % 900000)
    else:
        units, uvotes = municipalities(kind, process, pl.col("province"))
    if not units.height:
        units, uvotes = constituencies(
            totals.select(
                pl.col("province").alias("ckey"), "census", "blank", "invalid", "list_votes", pl.col("official")
            ),
            dvotes,
        )
    options = with_options(dvotes, uvotes, units, cands)
    return Part(
        "congress",
        "general",
        contests,
        declared,
        dvotes,
        options,
        cands,
        units,
        uvotes,
        inside,
        artifacts(kind, process),
    )


def municipalities(kind: str, process: str, place: pl.Expr) -> tuple[Frame, Frame]:
    """Municipality units, for files that stop there."""
    totals = read(kind, process, "05")
    votes = read(kind, process, "06")
    if not totals.height or not votes.height:
        return empty(), empty()
    totals = totals.filter(real() & (pl.col("municipal_district") == "99"))
    votes = votes.filter((pl.col("province") != "99") & (pl.col("municipal_district") == "99"))
    units = totals.select(
        (pl.col("province") + pl.col("municipality")).alias("ucode"),
        place.alias("ckey"),
        pl.lit("municipality").alias("ucat"),
        (pl.col("province") + pl.col("municipality")).alias("muni"),
        "province",
        "census",
        "blank",
        "invalid",
        "list_votes",
        (pl.col("official") == "S").alias("official"),
        pl.lit(0, pl.Int16).alias("channel"),
    )
    cast = votes.select(
        (pl.col("province") + pl.col("municipality")).alias("ucode"), pl.col("list").alias("opt"), "votes"
    ).filter(pl.col("votes") > 0)
    return units, cast


def constituencies(totals: Frame, dvotes: Frame) -> tuple[Frame, Frame]:
    """Constituency units, for files that publish nothing finer."""
    if not totals.height:
        keys = dvotes.select("ckey").unique()
        totals = keys.with_columns(
            census=pl.lit(None, pl.Int64),
            blank=pl.lit(None, pl.Int64),
            invalid=pl.lit(None, pl.Int64),
            list_votes=pl.lit(None, pl.Int64),
            official=pl.lit(True),
        )
    units = totals.select(
        pl.col("ckey").alias("ucode"),
        "ckey",
        pl.lit("constituency").alias("ucat"),
        pl.lit(None, pl.String).alias("muni"),
        pl.col("ckey").alias("province"),
        "census",
        "blank",
        "invalid",
        "list_votes",
        (pl.col("official") == "S").alias("official") if totals["official"].dtype == pl.String else pl.col("official"),
        pl.lit(0, pl.Int16).alias("channel"),
    )
    cast = dvotes.select(pl.col("ckey").alias("ucode"), "opt", "votes").filter(pl.col("votes") > 0)
    return units, cast


def candidate_rows(frame: Frame, ckey: pl.Expr, opt: pl.Expr) -> Frame:
    if not frame.height:
        return empty(
            ckey=pl.String,
            opt=pl.Int64,
            position=pl.Int64,
            kind=pl.String,
            name=pl.String,
            sex=pl.String,
            elected=pl.Boolean,
        )
    return frame.select(
        ckey.alias("ckey"),
        opt.alias("opt"),
        "position",
        "kind",
        pl.concat_str("given", "surname", "second_surname", separator=" ")
        .str.replace_all(r"\s+", " ")
        .str.strip_chars()
        .alias("name"),
        "sex",
        (pl.col("elected") == "S").alias("elected"),
    )


def with_options(
    dvotes: Frame, uvotes: Frame, units: Frame, cands: Frame, link: Frame | None = None, okind: str = "list"
) -> Frame:
    found = pl.concat(
        [
            dvotes.select("ckey", "opt"),
            uvotes.join(units.select("ucode", "ckey"), on="ucode").select("ckey", "opt")
            if uvotes.height
            else empty(ckey=pl.String, opt=pl.Int64),
            cands.select("ckey", "opt"),
        ]
    ).unique()
    found = found.with_columns(list=pl.col("opt")) if link is None else found.join(link, on=["ckey", "opt"], how="left")
    return found.with_columns(okind=pl.lit(okind))


def part_european(process: str) -> Part:
    kind = "07"
    inside = lists(kind, process)
    everything = read(kind, process, "07")
    nation = everything.filter((pl.col("community") == "99") & (pl.col("province") == "99"))
    contests = nation.select(
        pl.lit("ES").alias("ckey"),
        pl.lit("España").alias("name"),
        "seats",
        pl.lit(None, pl.String).alias("scheme"),
        pl.lit(None, pl.String).alias("acode"),
        pl.lit("european").alias("system"),
        pl.lit("list_allocation").alias("kind"),
        (pl.col("official") == "S").alias("official"),
    )
    declared = nation.select(pl.lit("ES").alias("ckey"), "census", "blank", "invalid", "list_votes")
    votes = read(kind, process, "08")
    dvotes = votes.filter((pl.col("community") == "99") & (pl.col("province") == "99")).select(
        pl.lit("ES").alias("ckey"), pl.col("list").alias("opt"), "votes", "seats"
    )
    cands = candidate_rows(read(kind, process, "04"), pl.lit("ES"), pl.col("list"))
    units, uvotes = mesas(kind, process, pl.lit("ES"))
    if not units.height:
        units, uvotes = municipalities(kind, process, pl.lit("ES"))
    if not units.height:
        provinces = everything.filter(real()).select(
            pl.col("province").alias("ucode"),
            pl.lit("ES").alias("ckey"),
            pl.lit("province").alias("ucat"),
            pl.lit(None, pl.String).alias("muni"),
            "province",
            "census",
            "blank",
            "invalid",
            "list_votes",
            (pl.col("official") == "S").alias("official"),
            pl.lit(0, pl.Int16).alias("channel"),
        )
        units = provinces
        uvotes = (
            votes.filter(real())
            .select(pl.col("province").alias("ucode"), pl.col("list").alias("opt"), "votes")
            .filter(pl.col("votes") > 0)
        )
    return Part(
        "european",
        "european",
        contests,
        declared,
        dvotes,
        with_options(dvotes, uvotes, units, cands),
        cands,
        units,
        uvotes,
        inside,
        artifacts(kind, process),
    )


def shifted(process: str) -> tuple[tuple[str, int, int], ...] | None:
    if process != "201505":
        return None
    return tuple(
        (name, start - 1, end - 1) if start > 100 else (name, start, end)
        for name, start, end in LAYOUTS["12"]
        if name != "sex"
    )


def part_municipal(process: str) -> Part:
    kind = "04"
    inside = lists(kind, process)
    code = pl.col("province") + pl.col("municipality")
    big = read(kind, process, "05").filter(real() & (pl.col("municipal_district") == "99"))
    contests = big.select(
        code.alias("ckey"),
        pl.col("name").map_elements(title, return_dtype=pl.String),
        "seats",
        pl.lit("es.interior_municipio").alias("scheme"),
        code.alias("acode"),
        pl.lit("municipal").alias("system"),
        pl.lit("list_allocation").alias("kind"),
        (pl.col("official") == "S").alias("official"),
    )
    declared = big.select(code.alias("ckey"), "census", "blank", "invalid", "list_votes")
    votes = read(kind, process, "06").filter((pl.col("province") != "99") & (pl.col("municipal_district") == "99"))
    dvotes = votes.select(code.alias("ckey"), pl.col("list").alias("opt"), "votes", "seats")
    cands = candidate_rows(read(kind, process, "04"), code, pl.col("list"))
    if process == "201105":
        cands = cands.with_columns(kind=pl.lit("T"))
    units, uvotes = mesas(kind, process, code)
    if not units.height:
        units, uvotes = municipalities(kind, process, code)
    options = with_options(dvotes, uvotes, units, cands)
    small = read(kind, process, "11")
    if small.height:
        tiers = small.select(
            code.alias("ckey"),
            pl.col("name").map_elements(title, return_dtype=pl.String),
            "seats",
            pl.lit("es.interior_municipio").alias("scheme"),
            code.alias("acode"),
            pl.when(pl.col("seats") == 1)
            .then(pl.lit("concejo-abierto"))
            .otherwise(pl.lit("municipal-open"))
            .alias("system"),
            pl.lit("body_seats").alias("kind"),
            (pl.col("official") == "S").alias("official"),
        )
        contests = pl.concat([contests, tiers])
        declared = pl.concat([declared, small.select(code.alias("ckey"), "census", "blank", "invalid", "list_votes")])
        rows = read(kind, process, "12", shifted(process))
        rows = rows.with_columns(
            ckey=code, order=pl.int_range(pl.len()).over("province", "municipality", "list") + 1
        ).with_columns(opt=pl.col("list") * 1000 + pl.col("order"))
        if "sex" not in rows.columns:
            rows = rows.with_columns(sex=pl.lit(""))
        smallc = rows.select(
            "ckey",
            "opt",
            pl.col("order").alias("position"),
            pl.lit("T").alias("kind"),
            pl.concat_str("given", "surname", "second_surname", separator=" ")
            .str.replace_all(r"\s+", " ")
            .str.strip_chars()
            .alias("name"),
            "sex",
            (pl.col("elected") == "S").alias("elected"),
        )
        smallv = rows.select(pl.col("ckey").alias("ucode"), "opt", "votes").filter(pl.col("votes") > 0)
        smallu = small.select(
            code.alias("ucode"),
            code.alias("ckey"),
            pl.lit("municipality").alias("ucat"),
            code.alias("muni"),
            "province",
            "census",
            "blank",
            "invalid",
            "list_votes",
            (pl.col("official") == "S").alias("official"),
            pl.lit(0, pl.Int16).alias("channel"),
        )
        link = rows.select("ckey", "opt", "list").unique()
        options = pl.concat([options, link.select("ckey", "opt", "list").with_columns(okind=pl.lit("candidate"))])
        cands = pl.concat([cands, smallc])
        units = pl.concat([units, smallu]) if units.height else smallu
        uvotes = pl.concat([uvotes, smallv]) if uvotes.height else smallv
    return Part(
        "municipal",
        "local",
        contests,
        declared,
        dvotes,
        options,
        cands,
        units,
        uvotes,
        inside,
        artifacts(kind, process),
    )


def island_map(kind: str, process: str, shortlist: set[str] | None = None) -> Frame:
    found = read(kind, process, "05")
    if not found.height:
        return empty(muni=pl.String, island=pl.String)
    return (
        found.filter(real() & (pl.col("municipal_district") == "99"))
        .select(
            (pl.col("province") + pl.col("municipality")).alias("muni"),
            (pl.col("province") + pl.col("district")).alias("island"),
        )
        .unique()
    )


def part_cabildo(process: str) -> Part:
    kind = "06"
    inside = lists(kind, process)
    totals = read(kind, process, "07").filter(real() & (pl.col("district") != "9"))
    ckey = pl.col("province") + pl.col("district")
    votes = read(kind, process, "08")
    votes = votes.filter(real() & (pl.col("district") != "9")) if votes.height else votes
    _, _, islands, _ = places()
    if totals.height:
        contests = totals.select(
            ckey.alias("ckey"),
            pl.col("name").map_elements(title, return_dtype=pl.String),
            "seats",
            pl.lit("es.interior_isla").alias("scheme"),
            ckey.alias("acode"),
            pl.lit("cabildo").alias("system"),
            pl.lit("list_allocation").alias("kind"),
            (pl.col("official") == "S").alias("official"),
        )
        declared = totals.select(ckey.alias("ckey"), "census", "blank", "invalid", "list_votes")
    else:
        keys = votes.select(ckey.alias("ckey"), "seats").group_by("ckey").agg(pl.col("seats").sum())
        contests = keys.select(
            "ckey",
            pl.col("ckey").map_elements(lambda key: islands.get(key, key), return_dtype=pl.String).alias("name"),
            "seats",
            pl.lit("es.interior_isla").alias("scheme"),
            pl.col("ckey").alias("acode"),
            pl.lit("cabildo").alias("system"),
            pl.lit("list_allocation").alias("kind"),
            pl.lit(True).alias("official"),
        )
        declared = empty(ckey=pl.String, census=pl.Int64, blank=pl.Int64, invalid=pl.Int64, list_votes=pl.Int64)
    dvotes = votes.select(ckey.alias("ckey"), pl.col("list").alias("opt"), "votes", "seats")
    candidates = read(kind, process, "04")
    cands = candidate_rows(
        candidates.filter(pl.col("province") != "99") if candidates.height else candidates, ckey, pl.col("list")
    )
    link = island_map(kind, process)
    units, uvotes = mesas(kind, process, pl.lit(None, pl.String))
    if units.height:
        units = units.drop("ckey").join(link, on="muni", how="left").rename({"island": "ckey"})
    else:
        units, uvotes = municipalities(kind, process, pl.lit(None, pl.String))
        if units.height:
            units = units.drop("ckey").join(link, on="muni", how="left").rename({"island": "ckey"})
    if not units.height:
        units, uvotes = constituencies(declared.join(contests.select("ckey", "official"), on="ckey"), dvotes)
    if units.height and "ckey" in units.columns:
        units = units.filter(pl.col("ckey").is_not_null()) if units["ckey"].null_count() else units
    return Part(
        "cabildo",
        "local",
        contests,
        declared,
        dvotes,
        with_options(dvotes, uvotes, units, cands),
        cands,
        units,
        uvotes,
        inside,
        artifacts(kind, process),
    )


def senate_key() -> pl.Expr:
    return (
        pl.when(pl.col("province").is_in(ISLANDS) & (pl.col("district") != "9"))
        .then(pl.col("province") + pl.col("district"))
        .otherwise(pl.col("province"))
    )


def senate_option() -> pl.Expr:
    return (pl.col("province") + pl.col("district") + pl.col("order").cast(pl.String).str.zfill(3)).cast(pl.Int64)


def part_senate(process: str) -> Part:
    kind = "03"
    inside = lists(kind, process)
    everything = real_rows(read(kind, process, "07"))
    totals = everything.filter(~pl.col("province").is_in(ISLANDS) | (pl.col("district") != "9"))
    contests = totals.select(
        senate_key().alias("ckey"),
        pl.col("name").map_elements(title, return_dtype=pl.String),
        "seats",
        pl.when(pl.col("province").is_in(ISLANDS))
        .then(pl.lit("es.interior_isla"))
        .otherwise(pl.lit("es.interior_provincia"))
        .alias("scheme"),
        senate_key().alias("acode"),
        pl.when(pl.col("province").is_in(SINGLE))
        .then(pl.lit("senate-block"))
        .when(pl.col("seats") == 1)
        .then(pl.lit("senate-single"))
        .otherwise(pl.lit("senate"))
        .alias("system"),
        pl.lit("body_seats").alias("kind"),
        (pl.col("official") == "S").alias("official"),
    )
    declared = totals.select(senate_key().alias("ckey"), "census", "blank", "invalid", "list_votes")
    votes = real_rows(read(kind, process, "08"))
    dvotes = votes.filter(~pl.col("province").is_in(ISLANDS) | (pl.col("district") != "9")).select(
        senate_key().alias("ckey"), pl.col("list").alias("opt"), "votes", "seats"
    )
    raw04 = read(kind, process, "04", (*LAYOUTS["04"], ("whole", 26, 100)))
    if raw04.height and (raw04["surname"] == "").any():
        raw04 = raw04.with_columns(given=pl.col("whole"), surname=pl.lit(""), second_surname=pl.lit(""))
    raw04 = raw04.filter(pl.col("province") != "99").with_columns(order=pl.col("municipality").cast(pl.Int64))
    cands = candidate_rows(raw04, senate_key(), senate_option())
    link = raw04.select(senate_key().alias("ckey"), senate_option().alias("opt"), pl.col("list")).unique()
    units, uvotes = senate_mesas(kind, process)
    if not units.height:
        units, uvotes = constituencies(
            totals.select(senate_key().alias("ckey"), "census", "blank", "invalid", "list_votes", "official"), dvotes
        )
    return Part(
        "senate",
        "general",
        contests,
        declared,
        dvotes,
        with_options(dvotes, uvotes, units, cands, link, "candidate"),
        cands,
        units,
        uvotes,
        inside,
        artifacts(kind, process),
    )


def senate_mesas(kind: str, process: str) -> tuple[Frame, Frame]:
    units, cast = mesas(kind, process, pl.lit(None, pl.String))
    if not units.height:
        return units, cast
    link = island_map(kind, process)
    totals = sections(read(kind, process, "09").filter(real()))
    districts = totals.select(mesa_code(), "municipal_district", "municipality", "province")
    units = units.join(districts.select("ucode", "municipal_district"), on="ucode", how="left").drop("ckey")
    ours = units.join(link.rename({"island": "found"}), on="muni", how="left")
    ours = ours.with_columns(
        ckey=pl.when(pl.col("province").is_in(ISLANDS))
        .then(
            pl.when(pl.col("ucat") == "cera")
            .then(pl.col("province") + pl.col("municipal_district").cast(pl.Int64).cast(pl.String))
            .otherwise(pl.col("found"))
        )
        .otherwise(pl.col("province"))
    )
    gone = ours.filter(
        pl.col("province").is_in(ISLANDS) & (pl.col("ucat") == "cera") & (pl.col("municipal_district") == "09")
    )
    ours = ours.join(gone.select("ucode"), on="ucode", how="anti").drop("found", "municipal_district")
    cast = cast.join(ours.select("ucode"), on="ucode", how="semi")
    return ours, cast


def answers() -> Frame:
    return pl.DataFrame(
        {"list": [1, 2], "label": ["Sí", "No"], "short": ["SÍ", "NO"], "party": [None, None]},
        schema={"list": pl.Int64, "label": pl.String, "short": pl.String, "party": pl.String},
    )


def part_referendum(process: str) -> Part:
    kind = "01"
    totals_layout = (*LAYOUTS["07"], ("yes", 156, 163), ("no", 164, 171))
    mesa_layout = (*LAYOUTS["09"], ("yes", 87, 93), ("no", 94, 100))
    totals = read(kind, process, "07", totals_layout)
    nation = totals.filter((pl.col("community") == "99") & (pl.col("province") == "99"))
    contests = pl.DataFrame(
        {
            "ckey": ["ES"],
            "name": [REFERENDUMS[election_day(kind, process)][1]],
            "seats": [None],
            "scheme": [None],
            "acode": [None],
            "system": ["referendum"],
            "kind": ["ballot_question"],
            "official": [bool((nation["official"] == "S").all())],
        },
        schema={
            "ckey": pl.String,
            "name": pl.String,
            "seats": pl.Int64,
            "scheme": pl.String,
            "acode": pl.String,
            "system": pl.String,
            "kind": pl.String,
            "official": pl.Boolean,
        },
    )
    declared = nation.select(pl.lit("ES").alias("ckey"), "census", "blank", "invalid", "list_votes")
    dvotes = pl.concat(
        [
            nation.select(
                pl.lit("ES").alias("ckey"),
                pl.lit(1, pl.Int64).alias("opt"),
                pl.col("yes").alias("votes"),
                pl.lit(None, pl.Int64).alias("seats"),
            ),
            nation.select(
                pl.lit("ES").alias("ckey"),
                pl.lit(2, pl.Int64).alias("opt"),
                pl.col("no").alias("votes"),
                pl.lit(None, pl.Int64).alias("seats"),
            ),
        ]
    )
    options = pl.DataFrame(
        {"ckey": ["ES", "ES"], "opt": [1, 2], "list": [1, 2], "okind": ["answer", "answer"]},
        schema={"ckey": pl.String, "opt": pl.Int64, "list": pl.Int64, "okind": pl.String},
    )
    detail = read(kind, process, "09", mesa_layout)
    if detail.height:
        detail = sections(detail.filter(real()))
        cera = pl.col("municipality") == "999"
        units = detail.select(
            mesa_code(),
            pl.lit("ES").alias("ckey"),
            pl.when(cera).then(pl.lit("cera")).otherwise(pl.lit("mesa")).alias("ucat"),
            pl.when(cera).then(None).otherwise(pl.col("province") + pl.col("municipality")).alias("muni"),
            "province",
            "census",
            "blank",
            "invalid",
            (pl.col("yes") + pl.col("no")).alias("list_votes"),
            (pl.col("official") == "S").alias("official"),
            pl.when(cera).then(ABROAD).otherwise(0).cast(pl.Int16).alias("channel"),
        )
        rows = detail.select(mesa_code(), "yes", "no")
    else:
        rows = totals.filter(real())
        units = rows.select(
            pl.col("province").alias("ucode"),
            pl.lit("ES").alias("ckey"),
            pl.lit("province").alias("ucat"),
            pl.lit(None, pl.String).alias("muni"),
            "province",
            "census",
            "blank",
            "invalid",
            (pl.col("yes") + pl.col("no")).alias("list_votes"),
            (pl.col("official") == "S").alias("official"),
            pl.lit(0, pl.Int16).alias("channel"),
        )
        rows = rows.select(pl.col("province").alias("ucode"), "yes", "no")
    uvotes = pl.concat(
        [
            rows.select("ucode", pl.lit(1, pl.Int64).alias("opt"), pl.col("yes").alias("votes")),
            rows.select("ucode", pl.lit(2, pl.Int64).alias("opt"), pl.col("no").alias("votes")),
        ]
    ).filter(pl.col("votes") > 0)
    empty_cands = candidate_rows(empty(), pl.lit(""), pl.lit(0))
    note = (
        "Votes for yes and no, registered voters, blank ballots, and null ballots from the Ministry of the Interior's "
        "data files. Valid ballots here are the votes for yes and no; blank ballots are stored apart."
    )
    return Part(
        "referendum",
        "referendum",
        contests,
        declared,
        dvotes,
        options,
        empty_cands,
        units,
        uvotes,
        answers(),
        artifacts(kind, process),
        note,
    )


def prov1976() -> str | None:
    return next((key for key in sorted(raw.keys(RAW)) if key.endswith("/PROV_01_197612_1.zip")), None)


def part_referendum_1976() -> Part:
    url = prov1976()
    if url is None:
        raise LookupError("the 1976 referendum file is not in the raw cache")
    with zipfile.ZipFile(raw.cached(RAW, url).path) as bundle:
        body = bundle.read("PROV_01_197612_1.xlsx")
    frame = pl.read_excel(io.BytesIO(body), engine="calamine", has_header=False).slice(2)
    rows = []
    for row in frame.iter_rows():
        code = str(row[1]).strip().zfill(2)
        census, blank, null, yes, no = int(row[5]), int(row[9]), int(row[10]), int(row[11]), int(row[12])
        if yes + no != int(row[8]) or yes + no + blank != int(row[7]):
            raise ValueError(f"1976 province {code}: the file's figures do not add up")
        rows.append((code, census, blank, null, yes, no))
    table = pl.DataFrame(rows, schema=["province", "census", "blank", "invalid", "yes", "no"], orient="row")
    total = table.select(pl.all().exclude("province").sum())
    contests = pl.DataFrame(
        {
            "ckey": ["ES"],
            "name": [REFERENDUMS[date(1976, 12, 15)][1]],
            "seats": [None],
            "scheme": [None],
            "acode": [None],
            "system": ["referendum"],
            "kind": ["ballot_question"],
            "official": [True],
        },
        schema={
            "ckey": pl.String,
            "name": pl.String,
            "seats": pl.Int64,
            "scheme": pl.String,
            "acode": pl.String,
            "system": pl.String,
            "kind": pl.String,
            "official": pl.Boolean,
        },
    )
    units = table.select(
        pl.col("province").alias("ucode"),
        pl.lit("ES").alias("ckey"),
        pl.lit("province").alias("ucat"),
        pl.lit(None, pl.String).alias("muni"),
        "province",
        "census",
        "blank",
        "invalid",
        (pl.col("yes") + pl.col("no")).alias("list_votes"),
        pl.lit(True).alias("official"),
        pl.lit(0, pl.Int16).alias("channel"),
    )
    uvotes = pl.concat(
        [
            table.select(
                pl.col("province").alias("ucode"), pl.lit(1, pl.Int64).alias("opt"), pl.col("yes").alias("votes")
            ),
            table.select(
                pl.col("province").alias("ucode"), pl.lit(2, pl.Int64).alias("opt"), pl.col("no").alias("votes")
            ),
        ]
    ).filter(pl.col("votes") > 0)
    declared = total.select(
        pl.lit("ES").alias("ckey"), "census", "blank", "invalid", (pl.col("yes") + pl.col("no")).alias("list_votes")
    )
    dvotes = pl.DataFrame(
        {
            "ckey": ["ES", "ES"],
            "opt": [1, 2],
            "votes": [int(total["yes"][0]), int(total["no"][0])],
            "seats": [None, None],
        },
        schema={"ckey": pl.String, "opt": pl.Int64, "votes": pl.Int64, "seats": pl.Int64},
    )
    options = pl.DataFrame(
        {"ckey": ["ES", "ES"], "opt": [1, 2], "list": [1, 2], "okind": ["answer", "answer"]},
        schema={"ckey": pl.String, "opt": pl.Int64, "list": pl.Int64, "okind": pl.String},
    )
    note = "Votes for yes and no, registered voters, blank ballots, and null ballots by province from the Ministry of the Interior's spreadsheet."
    return Part(
        "referendum",
        "referendum",
        contests,
        declared,
        dvotes,
        options,
        candidate_rows(empty(), pl.lit(""), pl.lit(0)),
        units,
        uvotes,
        answers(),
        [raw.cached(RAW, url)],
        note,
    )


# packages ------------------------------------------------------------------------------------------------------------

SOURCE_ID = uid("source", "infoelectoral")
BODIES = {
    "congress": ("lower_house", "Congress of Deputies", "Congreso de los Diputados"),
    "senate": ("upper_house", "Senate", "Senado"),
    "municipal": ("council", "Municipal councils", "Ayuntamientos"),
    "cabildo": ("council", "Island councils of the Canary Islands", "Cabildos insulares"),
}
SCHEMES = (
    ("es.interior_comunidad", 1, "administrative", "autonomous community"),
    ("es.interior_provincia", 2, "administrative", "province"),
    ("es.interior_isla", 3, "electoral_district", "island"),
    ("es.interior_municipio", 3, "administrative", "municipality"),
    ("es.interior_mesa", 5, "precinct", "polling station"),
)


def geo() -> Package:
    p = Package(
        "es.interior/geo",
        SOURCE,
        "Spain's autonomous communities, provinces, islands, and municipalities as the Ministry of the Interior's "
        "results files name them, without boundaries",
    )
    p.external |= {SPAIN, TERRITORY}
    communities, provinces, islands, municipalities = places()
    for code, rank, kind, noun in SCHEMES:
        p.add("scheme", code=code, polity_id=SPAIN, kind=kind, rank=rank, noun=noun)
    belongs = belonging()

    def place(scheme: str, code: str, text: str, parent: str, lang: str = "es") -> None:
        id = area(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=parent)
        p.name(id, text, lang, "official")

    for code, text in sorted(communities.items()):
        place("es.interior_comunidad", code, text, TERRITORY)
    for code, text in sorted(provinces.items()):
        place(
            "es.interior_provincia",
            code,
            text,
            area("es.interior_comunidad", belongs[code]) if code in belongs else TERRITORY,
        )
    for code, text in sorted(islands.items()):
        place("es.interior_isla", code, text, area("es.interior_provincia", code[:2]))
    for code in sorted(municipality_codes() | set(municipalities)):
        place(
            "es.interior_municipio",
            code,
            municipalities.get(code, code),
            area("es.interior_provincia", code[:2]) if code[:2] in provinces else TERRITORY,
        )
    return p


def registry() -> Package:
    p = Package(
        "es.interior/registry",
        SOURCE,
        "The Ministry of the Interior as a source, Spain's chambers and councils, their electoral systems, and the "
        "party labels its results files print",
        depends=["es.interior/geo"],
    )
    p.external |= {SPAIN, TERRITORY}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=PAGE,
        license="Reuse under Ley 37/2007 with the source cited",
        license_url="https://www.boe.es/buscar/act.php?id=BOE-A-2007-19814",
        attribution="Origen de los datos: Ministerio del Interior.",
    )
    for key, (kind, english, native) in BODIES.items():
        id = uid("body", key)
        p.add("body", id=id, polity_id=SPAIN, kind=kind)
        p.name(id, english, "en", "official")
        p.name(id, native, "es", "native", False)
    for key, (rules, english, text) in SYSTEMS.items():
        id = uid("system", key)
        p.add("electoral_system", id=id, polity_id=SPAIN, rules=rules, rules_version=1)
        p.name(id, english, "en", "official")
        p.add("note", id=uid("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    for key, (short, label, _local) in sorted(parties().items()):
        id = party_id(key)
        p.add("party", id=id, kind="party", polity_id=SPAIN)
        p.name(id, title(label) if label.isupper() else label, "es", "official")
        if short and fold(short) != fold(label):
            p.name(id, short, "es", "abbreviation", False)
        p.add("external_id", entity_id=id, scheme="es.interior.party", value=key)
    return p


def groups_on(d: date) -> set[str]:
    kinds = set(calendar().get(d, {})) | ({"01"} if d == date(1976, 12, 15) else set())
    return {
        name
        for name, wanted in (
            ("general", {"02", "03"}),
            ("local", {"04", "06"}),
            ("european", {"07"}),
            ("referendum", {"01"}),
        )
        if kinds & wanted
    }


def titles(d: date, group: str) -> tuple[str, str]:
    twice = sum(1 for other in calendar() if other.year == d.year and group in groups_on(other)) > 1
    month = f"{d:%B} " if twice else ""
    months = [
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "octubre",
        "noviembre",
        "diciembre",
    ]
    native_month = f"{months[d.month - 1]} de " if twice else ""
    if group == "general":
        return f"{month}{d.year} Spanish general election", f"Elecciones generales de {native_month}{d.year}"
    if group == "local":
        return f"{month}{d.year} Spanish local elections", f"Elecciones locales de {native_month}{d.year}"
    if group == "european":
        return (
            f"{month}{d.year} European Parliament election in Spain",
            f"Elecciones al Parlamento Europeo de {native_month}{d.year}",
        )
    english, native, _kind = REFERENDUMS[d]
    return f"{d.year} Spanish referendum on the {english}", native


def parts_for(d: date) -> list[Part]:
    if d == date(1976, 12, 15):
        return [part_referendum_1976()]
    found = calendar()[d]
    built: list[Part] = []
    for kind, builder in (
        ("02", part_congress),
        ("03", part_senate),
        ("04", part_municipal),
        ("06", part_cabildo),
        ("07", part_european),
        ("01", part_referendum),
    ):
        for process in found.get(kind, []):
            built.append(builder(process))
    return built


def dates() -> list[date]:
    found = set(calendar())
    if prov1976() is not None:
        found.add(date(1976, 12, 15))
    return sorted(found)


def packages() -> list[str]:
    return ["es.interior/geo", "es.interior/registry", *(f"es.interior/{d}" for d in dates())]


def shown(full: str) -> str:
    if full.strip() and not any(c.islower() for c in full.replace("ª", "").replace("º", "")):
        from ea.names import display

        return display(full, "es")
    return full


def ids(kind: str, *columns: Sequence[object]) -> list[str]:
    return [uid(kind, *row) for row in zip(*columns, strict=True)]


def nonnegative(*names: str) -> pl.Expr:
    return pl.all_horizontal(*(pl.col(name).is_null() | (pl.col(name) >= 0) for name in names))


MEASURES = (
    (REGISTERED, pl.when(nonnegative("census")).then(pl.col("census"))),
    (
        BALLOTS,
        pl.when(nonnegative("list_votes", "blank", "invalid")).then(
            pl.col("list_votes") + pl.col("blank") + pl.col("invalid")
        ),
    ),
    (VALID, pl.when(nonnegative("list_votes")).then(pl.col("list_votes"))),
    (INVALID, pl.when(nonnegative("invalid")).then(pl.col("invalid"))),
    (BLANK, pl.when(nonnegative("blank")).then(pl.col("blank"))),
)
FACT = {
    "result_set_id": pl.String,
    "unit_id": pl.String,
    "option_id": pl.String,
    "channel": pl.Int16,
    "measure": pl.Int16,
    "count_round": pl.Int16,
    "flags": pl.Int16,
    "value": pl.Int64,
}
TOTALS: list[dict[str, object]] = []


class Build:
    def __init__(self, d: date) -> None:
        self.d = d
        self.p = Package(
            f"es.interior/{d}",
            SOURCE,
            f"Spain's elections of {d} from the Ministry of the Interior's data files, down to the finest unit published",
            depends=["es.interior/geo", "es.interior/registry"],
        )
        self.p.external |= {SPAIN, TERRITORY}
        self.elections: dict[str, str] = {}
        self.units: dict[str, Frame] = {}
        self.values: list[Frame] = []
        self.declared: list[Frame] = []
        self.seen: set[str] = set()

    def election(self, group: str, kind: str) -> str:
        if group in self.elections:
            return self.elections[group]
        p, d = self.p, self.d
        id = self.elections[group] = uid("election", d, group)
        p.add("election", id=id, polity_id=SPAIN, kind=kind, date_from=d, date_to=d, status="held")
        english, native = titles(d, group)
        p.name(id, english, "en", "official")
        p.name(id, native, "es", "native", False)
        p.add("slug", path=f"es/interior/{d}-{group}", entity_id=id, is_current=True)
        return id

    def add(self, part: Part) -> None:
        p, d = self.p, self.d
        tag = part.tag
        kind = {"general": "general", "local": "local", "european": "general", "referendum": "referendum"}[part.group]
        election = self.election(part.group, kind)
        for artifact in part.artifacts:
            if artifact.sha256 not in self.seen:
                self.seen.add(artifact.sha256)
                p.artifact(artifact, SOURCE_ID)
        p.add(
            "note",
            id=uid("note", election, tag),
            entity_id=election,
            kind="methodology",
            body_md=f"{tag.capitalize()}: {part.note}",
            lang="en",
        )
        contests = part.contests.unique("ckey", keep="first", maintain_order=True).sort("ckey")
        keys = contests["ckey"].to_list()
        rows = contests.rows(named=True)
        base = f"es/interior/{d}-{part.group}/{tag}"
        slugs = [f"{base}/{changeset.slug(row['name'])}" for row in rows]
        counts = {value: slugs.count(value) for value in set(slugs)}
        body = uid("body", tag) if tag in BODIES else None
        for row, path in zip(rows, slugs, strict=True):
            contest = uid("contest", d, tag, row["ckey"])
            if counts[path] > 1:
                path = f"{path}-{row['ckey'].lower()}"
            p.add(
                "contest",
                id=contest,
                election_id=election,
                kind=row["kind"],
                body_id=body,
                district_area_id=area(row["scheme"], row["acode"]) if row["scheme"] else TERRITORY,
                magnitude=row["seats"] if row["kind"] != "ballot_question" else None,
                round_number=1,
                electoral_system_id=uid("system", row["system"]),
                default_result_kind="counted",
            )
            p.name(contest, row["name"], "es", "official")
            p.add("slug", path=path, entity_id=contest, is_current=True)
            if row["kind"] == "ballot_question":
                p.add("ballot_question", contest_id=contest, type=REFERENDUMS[d][2])
            result_set = uid("result_set", d, tag, row["ckey"])
            official = bool(row["official"]) and bool(
                part.units.filter(pl.col("ckey") == row["ckey"])["official"].all()
            )
            p.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official" if official else "preliminary",
                kind="counted",
                source_id=SOURCE_ID,
                status="active",
                is_default=True,
                completeness=1.0,
            )
        set_of = {key: uid("result_set", d, tag, key) for key in keys}
        # options
        key_label = (
            part.lists.select("list", "label", "short", "party").unique("list", keep="first")
            if part.lists.height
            else None
        )
        options = part.options.filter(pl.col("ckey").is_in(keys))
        if key_label is not None:
            options = options.join(key_label, on="list", how="left")
        else:
            options = options.with_columns(label=pl.lit(None, pl.String), party=pl.lit(None, pl.String))
        options = options.sort("ckey", "opt")
        option_id = ids(
            "option", [d] * options.height, [tag] * options.height, options["ckey"].to_list(), options["opt"].to_list()
        )
        p.frame(
            "contest_option",
            pl.DataFrame(
                {
                    "id": option_id,
                    "contest_id": [uid("contest", d, tag, key) for key in options["ckey"]],
                    "kind": options["okind"],
                    "party_id": [party_id(key) if key is not None else None for key in options["party"]],
                    "ballot_label": [
                        tidy(label) if label else f"List {opt}"
                        for label, opt in zip(options["label"], options["opt"], strict=True)
                    ],
                }
            ),
        )
        index = options.select("ckey", "opt", "okind").with_columns(option_id=pl.Series(option_id))
        # people
        cands = part.cands.filter(pl.col("ckey").is_in(keys))
        if cands.height:
            n = cands.height
            tags = [f"{d}:{tag}:{i}" for i in range(n)]
            person_ids = [uid("person", value) for value in tags]
            candidacy_ids = [uid("candidacy", value) for value in tags]
            joined = cands.with_row_index("n").join(index, on=["ckey", "opt"], how="left").sort("n")
            full = [shown(value) for value in joined["name"].to_list()]
            p.frame(
                "person",
                pl.DataFrame(
                    {"id": person_ids, "gender": [{"M": "male", "F": "female"}.get(sex) for sex in joined["sex"]]}
                ),
            )
            p.frame(
                "name",
                pl.DataFrame(
                    {
                        "id": ids("name", person_ids, ["es"] * n, ["official"] * n, full),
                        "entity_id": person_ids,
                        "text": full,
                        "lang": ["es"] * n,
                        "kind": ["official"] * n,
                        "is_preferred": [True] * n,
                    }
                ),
            )
            p.frame(
                "candidacy",
                pl.DataFrame(
                    {
                        "id": candidacy_ids,
                        "person_id": person_ids,
                        "option_id": joined["option_id"],
                        "role": [
                            "substitute" if kind_ == "S" else ("head" if tag == "senate" else "list_member")
                            for kind_ in joined["kind"]
                        ],
                        "list_position": joined["position"],
                        "ballot_name": full,
                        "is_independent": [False] * n,
                    }
                ),
            )
            won = joined.with_columns(candidacy=pl.Series(candidacy_ids)).filter(
                pl.col("elected") & (pl.col("kind") == "T")
            )
            won = won.with_columns(
                candidacy=pl.when(pl.col("okind") == "list").then(pl.col("candidacy")).otherwise(None)
            )
            p.frame(
                "outcome",
                pl.DataFrame(
                    {
                        "result_set_id": [set_of[key] for key in won["ckey"]],
                        "option_id": won["option_id"],
                        "candidacy_id": won["candidacy"],
                        "kind": ["elected"] * won.height,
                    }
                ),
            )
        # seats by list
        if tag != "senate":
            seats = part.dvotes.filter(pl.col("ckey").is_in(keys) & (pl.col("seats") > 0)).join(
                index, on=["ckey", "opt"], how="left"
            )
            p.frame(
                "outcome",
                pl.DataFrame(
                    {
                        "result_set_id": [set_of[key] for key in seats["ckey"]],
                        "option_id": seats["option_id"],
                        "kind": ["won"] * seats.height,
                        "seats": seats["seats"],
                    }
                ),
            )
        # units
        units = part.units.filter(pl.col("ckey").is_in(keys)).unique(
            ["ucode", "ckey"], keep="first", maintain_order=True
        )
        declared_rows = part.declared.filter(pl.col("ckey").is_in(keys)).unique(
            "ckey", keep="first", maintain_order=True
        )
        uvotes = part.uvotes
        missing = [key for key in keys if key not in set(units["ckey"].to_list())]
        if missing:
            wanted = (
                pl.DataFrame({"ckey": missing})
                .join(declared_rows, on="ckey", how="left")
                .join(contests.select("ckey", "official"), on="ckey", how="left")
            )
            fallback, cast = constituencies(wanted, part.dvotes.filter(pl.col("ckey").is_in(missing)))
            if tag == "municipal":
                fallback = fallback.with_columns(ucat=pl.lit("municipality"), muni=pl.col("ckey"))
            units = pl.concat([units, fallback.select(units.columns)])
            uvotes = pl.concat([uvotes, cast]) if uvotes.height else cast
        scheme = (
            pl.when(pl.col("ucat").is_in(["mesa", "cera"]))
            .then(pl.lit("es.interior_mesa"))
            .when(pl.col("ucat") == "municipality")
            .then(pl.lit("es.interior_municipio"))
            .when(pl.col("ucode").str.len_chars() >= 3)
            .then(pl.lit("es.interior_isla"))
            .otherwise(pl.lit("es.interior_provincia"))
        )
        located = units.with_columns(scheme=scheme).with_columns(
            area_scheme=pl.when(pl.col("ucat") == "cera")
            .then(pl.lit("es.interior_provincia"))
            .when(pl.col("scheme") == "es.interior_mesa")
            .then(pl.lit("es.interior_municipio"))
            .otherwise(pl.col("scheme")),
            acode=pl.when(pl.col("ucat") == "cera")
            .then(pl.col("province"))
            .when(pl.col("scheme") == "es.interior_mesa")
            .then(pl.col("muni"))
            .when(pl.col("scheme") == "es.interior_municipio")
            .then(pl.col("muni"))
            .otherwise(pl.col("ucode")),
        )
        built = located.unique(["scheme", "ucode"]).select("scheme", "ucode", "ucat", "area_scheme", "acode")
        built = built.with_columns(
            unit_id=pl.Series(ids("unit", built["scheme"].to_list(), built["ucode"].to_list()), dtype=pl.String)
        )
        fresh = built.filter(~pl.col("unit_id").is_in(list(self.units))) if self.units else built
        for unit_id in fresh["unit_id"].to_list():
            self.units[unit_id] = fresh
        p.frame(
            "reporting_unit",
            pl.DataFrame(
                {
                    "id": fresh["unit_id"],
                    "polity_id": [SPAIN] * fresh.height,
                    "scheme_code": fresh["scheme"],
                    "code": [f"{d}:{code}" for code in fresh["ucode"]],
                    "area_relation": [
                        "within" if scheme_ == "es.interior_mesa" else "equals" for scheme_ in fresh["scheme"]
                    ],
                    "area_id": [area(a, c) for a, c in zip(fresh["area_scheme"], fresh["acode"], strict=True)],
                    "kind": ["aggregate" if cat == "cera" else "standard" for cat in fresh["ucat"]],
                }
            ),
        )
        located = located.join(built.select("scheme", "ucode", "unit_id"), on=["scheme", "ucode"], how="left")
        located = located.with_columns(result_set_id=pl.col("ckey").replace_strict(set_of, return_dtype=pl.String))
        # facts
        measure_rows = [
            located.select(
                "result_set_id",
                "unit_id",
                pl.lit(None, pl.String).alias("option_id"),
                "channel",
                pl.lit(measure, pl.Int16).alias("measure"),
                expr.alias("value"),
            ).filter((pl.col("value") > 0) if measure == REGISTERED else pl.col("value").is_not_null())
            for measure, expr in MEASURES
        ]
        votes = (
            uvotes.join(located.select("ucode", "ckey", "unit_id", "result_set_id", "channel", "scheme"), on="ucode")
            .join(index, on=["ckey", "opt"], how="inner")
            .select(
                "result_set_id",
                "unit_id",
                "option_id",
                "channel",
                pl.lit(VOTES, pl.Int16).alias("measure"),
                pl.col("votes").alias("value"),
            )
        )
        self.values.extend([*measure_rows, votes])
        # declared
        declared = declared_rows
        pieces = [
            declared.select(
                pl.col("ckey").replace_strict(set_of, return_dtype=pl.String).alias("result_set_id"),
                pl.lit(None, pl.String).alias("option_id"),
                pl.lit(measure, pl.Int16).alias("measure"),
                expr.alias("value"),
            )
            for measure, expr in MEASURES
        ]
        dv = part.dvotes.filter(pl.col("ckey").is_in(keys)).join(index, on=["ckey", "opt"], how="inner")
        pieces.append(
            dv.select(
                pl.col("ckey").replace_strict(set_of, return_dtype=pl.String).alias("result_set_id"),
                "option_id",
                pl.lit(VOTES, pl.Int16).alias("measure"),
                pl.col("votes").alias("value"),
            )
        )
        self.declared.extend(pieces)
        # national comparison
        total_declared = int(declared["list_votes"].sum()) if declared.height else 0
        total_units = int(votes["value"].sum()) if votes.height else 0
        TOTALS.append(
            {
                "date": str(d),
                "tag": tag,
                "contests": len(keys),
                "units": units.height,
                "declared_valid": total_declared,
                "unit_votes_sum": total_units,
                "declared_candidate_votes": int(part.dvotes.filter(pl.col("ckey").is_in(keys))["votes"].sum())
                if part.dvotes.height
                else 0,
            }
        )

    def finish(self) -> Package:
        p = self.p
        facts = pl.concat(self.values) if self.values else pl.DataFrame(schema=FACT)
        p.frame(
            "result_value",
            facts.with_columns(count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
        )
        if self.declared:
            p.frame("declared", pl.concat(self.declared).filter(pl.col("value").is_not_null()))
        return p


REASONS = {
    "accounting.votes_valid": (
        "In {n} units the votes to candidacies printed for the unit's lists do not add up to the unit's own total of "
        "valid votes in the ministry's files; both are stored as printed."
    ),
    "accounting.turnout": (
        "{n} units print more ballots than registered voters in the ministry's files; the census and the ballots are "
        "stored as printed."
    ),
    "accounting.declared": (
        "{n} of the ministry's constituency totals differ from the sums of its own unit-level figures in the same "
        "files; both are stored as printed."
    ),
    "outcomes.seats_magnitude": (
        "In {n} contests the seats the ministry declares for the lists do not add up to the seats of the body in the "
        "same files; the declared seats are stored as printed."
    ),
    "outcomes.list_members": (
        "In {n} lists the ministry flags a different number of candidates as elected than the seats it declares for "
        "the list; both are stored as printed."
    ),
    "outcomes.single_winner": (
        "In {n} single-seat contests the ministry flags more than one candidate as elected; the flags are stored as "
        "printed."
    ),
}


def build(d: date) -> Package:
    b = Build(d)
    for part in parts_for(d):
        b.add(part)
    return b.finish()


def sealed(p: Package) -> Package:
    """Write the package, check it, accept what the ministry's own figures cause, and write it again."""
    out = p.write()
    for code, count, _sample in package.check(out):
        if code not in REASONS:
            raise ValueError(f"{p.key}: {code} ({count}) is not one the source's figures can explain")
        p.accept(code, REASONS[code].format(n=f"{count:,}"))
    return p


def parse() -> None:
    geo().write()
    registry().write()
    for d in dates():
        sealed(build(d)).write()


def emit() -> None:
    package.run("load", packages())
