import io
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID, uuid5

import polars as pl

from ea import raw
from ea.changeset import NAMESPACE, Changeset
from ea.store import connect

SOURCE = "pl.kbw_referendum"
VERSION = "1"
PUBLISHER = "Krajowe Biuro Wyborcze"
TITLE = "Dane wyborcze: referenda ogólnokrajowe"
PAGE = "https://danewyborcze.kbw.gov.pl/"
NOTICE_PUBLISHER = "Państwowa Komisja Wyborcza"
BASE = "https://danewyborcze.kbw.gov.pl/dane"
ELI = "https://eli.gov.pl/api/acts/DU"
PARSED = raw.parsed(SOURCE)
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5

FILES = {
    "1996": {"gmina": "1996/ref96gm.xlsx", "obwod": "1996/ref96obw.xlsx"},
    "1997": {"zip": "1997/referendum/Ref1997.zip"},
    "2003": {
        "gmina": "2003/referendum/prot-gm.xls",
        "obwod": "2003/referendum/prot-obw.xls",
        "powiat": "2003/referendum/prot-pow.xls",
    },
    "2015": {
        "gmina": "2015/referendum/ref2015gm.xls",
        "obwod": "2015/referendum/ref2015obw.zip",
        "wojewodztwo": "2015/referendum/ref2015woj.xls",
    },
    "2023": {
        "gmina": "2023/referendum/wyniki_referendum_po_gminach_csv.zip",
        "obwod": "2023/referendum/wyniki_referendum_po_obwodach_csv.zip",
        "powiat": "2023/referendum/wyniki_referendum_po_powiatach_csv.zip",
    },
}
NOTICES = {
    "1996": "DU/1996/101",
    "1997": "DU/1997/353",
    "1997c": "DU/1997/476",
    "2003": "DU/2003/953",
    "2003c": "DU/2003/1223",
    "2015": "DU/2015/1375",
    "2023": "DU/2023/2236",
}
ANNULLED_2003 = (("14", 2366, 1359, 3, 1356, 1109, 247), ("15", 1139, 745, 4, 741, 631, 110))
CORRECTED_2003 = {
    "electors": 29864969,
    "cast": 17576714,
    "invalid": 126187,
    "valid": 17450527,
    "yes": 13514872,
    "no": 3935655,
}

Question = tuple[str, str, str, str]
Referendum = tuple[str, date, date, str, str, str, bool, tuple[Question, ...]]
REFERENDUMS: dict[str, Referendum] = {
    "1996a": (
        "1996",
        date(1996, 2, 18),
        date(1996, 2, 18),
        "1996 referendum on universal enfranchisement",
        "Referendum o powszechnym uwłaszczeniu obywateli 1996",
        "1996-02-18-enfranchisement",
        False,
        (
            (
                "Enfranchisement of citizens",
                "Uwłaszczenie obywateli",
                "Czy jesteś za przeprowadzeniem powszechnego uwłaszczenia obywateli?",
                "Do you favor carrying out the universal enfranchisement of citizens?",
            ),
        ),
    ),
    "1996b": (
        "1996",
        date(1996, 2, 18),
        date(1996, 2, 18),
        "1996 referendum on uses of state property",
        "Referendum o niektórych kierunkach wykorzystania majątku państwowego 1996",
        "1996-02-18-state-property",
        False,
        (
            (
                "Pension and public-sector obligations",
                "Zobowiązania wobec emerytów i pracowników sfery budżetowej",
                (
                    "Czy jesteś za tym, aby zobowiązania wobec emerytów i rencistów oraz pracowników sfery budżetowej, "
                    "wynikające z orzeczeń Trybunału Konstytucyjnego, były zaspokojone z prywatyzowanego majątku "
                    "państwowego?"
                ),
                (
                    "Do you favor meeting the obligations to retirees, pensioners, and public-sector employees that "
                    "follow from Constitutional Tribunal rulings out of privatized state property?"
                ),
            ),
            (
                "Pension funds",
                "Powszechne fundusze emerytalne",
                (
                    "Czy jesteś za tym, aby część prywatyzowanego majątku państwowego zasiliła powszechne fundusze "
                    "emerytalne?"
                ),
                "Do you favor devoting part of privatized state property to the universal pension funds?",
            ),
            (
                "National Investment Funds",
                "Narodowe Fundusze Inwestycyjne",
                (
                    "Czy jesteś za tym, aby zwiększyć wartość świadectw udziałowych Narodowych Funduszy Inwestycyjnych "
                    "przez objęcie tym programem dalszych przedsiębiorstw?"
                ),
                (
                    "Do you favor raising the value of the National Investment Funds' share certificates by bringing "
                    "further enterprises into the program?"
                ),
            ),
            (
                "Privatization vouchers",
                "Bony prywatyzacyjne",
                "Czy jesteś za uwzględnieniem w programie uwłaszczeniowym bonów prywatyzacyjnych?",
                "Do you favor including privatization vouchers in the enfranchisement program?",
            ),
        ),
    ),
    "1997": (
        "1997",
        date(1997, 5, 25),
        date(1997, 5, 25),
        "1997 constitutional referendum",
        "Referendum konstytucyjne 1997",
        "1997-05-25-constitution",
        True,
        (
            (
                "Adoption of the Constitution",
                "Przyjęcie Konstytucji",
                (
                    "Czy jesteś za przyjęciem Konstytucji Rzeczypospolitej Polskiej uchwalonej przez Zgromadzenie "
                    "Narodowe w dniu 2 kwietnia 1997 r.?"
                ),
                (
                    "Do you favor adopting the Constitution of the Republic of Poland passed by the National Assembly "
                    "on 2 April 1997?"
                ),
            ),
        ),
    ),
    "2003": (
        "2003",
        date(2003, 6, 7),
        date(2003, 6, 8),
        "2003 referendum on accession to the European Union",
        "Referendum w sprawie przystąpienia do Unii Europejskiej 2003",
        "2003-06-08-eu-accession",
        True,
        (
            (
                "Accession to the European Union",
                "Przystąpienie do Unii Europejskiej",
                "Czy wyraża Pani/Pan zgodę na przystąpienie Rzeczypospolitej Polskiej do Unii Europejskiej?",
                "Do you consent to the accession of the Republic of Poland to the European Union?",
            ),
        ),
    ),
    "2015": (
        "2015",
        date(2015, 9, 6),
        date(2015, 9, 6),
        "2015 national referendum",
        "Referendum ogólnokrajowe 2015",
        "2015-09-06-referendum",
        False,
        (
            (
                "Single-member constituencies in Sejm elections",
                "Jednomandatowe okręgi wyborcze w wyborach do Sejmu",
                (
                    "Czy jest Pani/Pan za wprowadzeniem jednomandatowych okręgów wyborczych w wyborach do Sejmu "
                    "Rzeczypospolitej Polskiej?"
                ),
                "Are you in favor of introducing single-member constituencies in elections to the Sejm?",
            ),
            (
                "Funding of political parties from the state budget",
                "Finansowanie partii politycznych z budżetu państwa",
                (
                    "Czy jest Pani/Pan za utrzymaniem dotychczasowego sposobu finansowania partii politycznych z "
                    "budżetu państwa?"
                ),
                "Are you in favor of keeping the present way of funding political parties from the state budget?",
            ),
            (
                "Doubts in tax law resolved for the taxpayer",
                "Wątpliwości co do prawa podatkowego na korzyść podatnika",
                (
                    "Czy jest Pani/Pan za wprowadzeniem zasady ogólnej rozstrzygania wątpliwości co do wykładni "
                    "przepisów prawa podatkowego na korzyść podatnika?"
                ),
                (
                    "Are you in favor of introducing a general rule that doubts about the interpretation of tax law are "
                    "settled in the taxpayer's favor?"
                ),
            ),
        ),
    ),
    "2023": (
        "2023",
        date(2023, 10, 15),
        date(2023, 10, 15),
        "2023 national referendum",
        "Referendum ogólnokrajowe 2023",
        "2023-10-15-referendum",
        False,
        (
            (
                "Sale of state assets to foreign entities",
                "Sprzedaż majątku państwowego podmiotom zagranicznym",
                (
                    "Czy popierasz wyprzedaż majątku państwowego podmiotom zagranicznym, prowadzącą do utraty "
                    "kontroli Polek i Polaków nad strategicznymi sektorami gospodarki?"
                ),
                (
                    "Do you support the sale of state assets to foreign entities, leading to a loss of Polish "
                    "control over strategic sectors of the economy?"
                ),
            ),
            (
                "Retirement age",
                "Wiek emerytalny",
                (
                    "Czy popierasz podniesienie wieku emerytalnego, w tym przywrócenie podwyższonego do 67 lat "
                    "wieku emerytalnego dla kobiet i mężczyzn?"
                ),
                (
                    "Do you support raising the retirement age, including restoring the higher retirement age of 67 "
                    "for women and men?"
                ),
            ),
            (
                "Barrier on the border with Belarus",
                "Bariera na granicy z Białorusią",
                "Czy popierasz likwidację bariery na granicy Rzeczypospolitej Polskiej z Republiką Białorusi?",
                "Do you support removing the barrier on the border of the Republic of Poland with the Republic of Belarus?",
            ),
            (
                "Relocation of migrants",
                "Relokacja imigrantów",
                (
                    "Czy popierasz przyjęcie tysięcy nielegalnych imigrantów z Bliskiego Wschodu i Afryki, zgodnie z "
                    "przymusowym mechanizmem relokacji narzucanym przez biurokrację europejską?"
                ),
                (
                    "Do you support admitting thousands of illegal immigrants from the Middle East and Africa under "
                    "the compulsory relocation mechanism imposed by the European bureaucracy?"
                ),
            ),
        ),
    ),
}
NATIONAL = {
    "1996a": (28009715, 9076004, {1: (152678, 8923326, 8580129, 343197)}),
    "1996b": (
        28009715,
        9085145,
        {
            1: (208221, 8876924, 8439458, 437466),
            2: (216851, 8868294, 8512931, 355363),
            3: (511019, 8574126, 1985567, 6588559),
            4: (299887, 8785258, 8022353, 762905),
        },
    ),
    "1997": (28319650, 12137136, {1: (170002, 11967134, 6396641, 5570493)}),
    "2003": (29864969, 17576714, {1: (126187, 17450527, 13514872, 3935655)}),
    "2015": (
        30565826,
        2384780,
        {
            1: (60850, 2323930, 1829995, 493935),
            2: (56271, 2328509, 404515, 1923994),
            3: (62696, 2322084, 2194689, 127395),
        },
    ),
    "2023": (
        29532595,
        12082588,
        {
            1: (830388, 11252200, 394704, 10857496),
            2: (799123, 11283465, 608254, 10675211),
            3: (828908, 11253680, 445270, 10808410),
            4: (842922, 11239666, 360803, 10878863),
        },
    ),
}
BINDING = {"1997", "2003"}
METHOD = (
    "Voters entitled to vote, ballots cast, and the valid and invalid votes on the question, summed from the Krajowe "
    "Biuro Wyborcze's files of results by polling district (obwód głosowania). Ballots cast are the ballots taken "
    "from the urn."
)
GMINA_METHOD = (
    "Votes and accounting summed from the polling districts of each gmina, with votes from abroad and ships each in "
    "one unit, as the Krajowe Biuro Wyborcze's file of results by gmina prints them."
)
OBWOD_METHOD = (
    "Votes and accounting by polling district (obwód głosowania) as the Krajowe Biuro Wyborcze's file prints them. "
    "Polling districts are described by addresses, not mapped, so they have no area."
)
SYSTEM_NOTE = (
    "A national referendum is decided by a majority of valid votes on each question. The result binds only if more "
    "than half of the voters entitled to vote took part (Konstytucja, arts. 125 and 235; ustawa o referendum "
    "ogólnokrajowym of 14 March 2003, art. 75; before it, the 1995 act)."
)
NOTE_NONBINDING = (
    " Fewer than half of the voters entitled to vote took part, so the result did not bind and the Państwowa Komisja "
    "Wyborcza declared no binding outcome; none is recorded."
)


def url(year: str, kind: str) -> str:
    return f"{BASE}/{FILES[year][kind]}"


def notice_url(eli: str) -> str:
    return f"{ELI}/{eli.removeprefix('DU/')}/text.pdf"


def fetch() -> None:
    for files in FILES.values():
        for path in files.values():
            raw.fetch(SOURCE, f"{BASE}/{path}", suffix=Path(path).suffix)
    for eli in NOTICES.values():
        raw.fetch(SOURCE, notice_url(eli), suffix=".pdf")


def sheet(year: str, kind: str, member: str | None = None) -> pl.DataFrame:
    path = raw.cached(SOURCE, url(year, kind)).path
    if path.suffix in (".xls", ".xlsx"):
        return pl.read_excel(path, has_header=False, infer_schema_length=0)
    archive = zipfile.ZipFile(path)
    names = [name for name in archive.namelist() if not name.startswith("__MACOSX")]
    if member is not None:
        name = next(name for name in names if name.lower().endswith(member))
    else:
        name = next(name for name in names if name.lower().endswith((".csv", ".xls", ".xlsx")))
    data = archive.read(name)
    if name.lower().endswith(".csv"):
        return pl.read_csv(io.BytesIO(data), separator=";", has_header=False, infer_schema_length=0, skip_rows=1)
    return pl.read_excel(io.BytesIO(data), has_header=False, infer_schema_length=0)


def number(column: int) -> pl.Expr:
    return (
        pl.col(f"column_{column}")
        .str.strip_chars()
        .replace({"-": None, "": None})
        .str.replace_all(r"\s", "")
        .cast(pl.Int64, strict=False)
    )


def local_code(code: pl.Expr, place: pl.Expr, length: int) -> pl.Expr:
    teryt = code.str.strip_chars().str.zfill(length)
    lowered = place.str.to_lowercase()
    if length == 5:
        return teryt
    return (
        pl.when(teryt.str.ends_with("9901") & teryt.str.starts_with("14"))
        .then(pl.lit("zagranica"))
        .when(teryt.str.ends_with("9901") | lowered.str.starts_with("stat"))
        .then(pl.lit("statki"))
        .when(lowered == "zagranica")
        .then(pl.lit("zagranica"))
        .otherwise(pl.when(teryt.str.contains(r"^\d+$")).then(teryt.str.slice(0, 6)).otherwise(teryt))
    )


def layout(
    year: str,
    level: str,
    code: int,
    place: int,
    commission: int | None,
    electors: int,
    cast: int,
    questions: dict[int, tuple[int, int, int, int]],
    length: int = 6,
    unplaced: str | None = None,
    member: str | None = None,
    name: int | None = None,
) -> pl.DataFrame:
    frame = sheet(year, level, member)
    pattern = r"^\d{5}$" if length == 5 else r"^\d{5,7}$"
    selected = frame.filter(pl.col(f"column_{code}").str.strip_chars().str.contains(pattern).fill_null(False))
    if unplaced is not None:
        selected = pl.concat(
            [selected, frame.filter(pl.col(f"column_{code}").is_null() & (pl.col(f"column_{place}") != ""))]
        )
    frames = []
    for number_, (valid, invalid, yes, no) in questions.items():
        frames.append(
            selected.select(
                pl.lit(number_, pl.Int16).alias("q"),
                local_code(pl.col(f"column_{code}").fill_null("zagranica"), pl.col(f"column_{place}"), length).alias(
                    "gmina"
                ),
                pl.concat_str(
                    pl.col(f"column_{code}").str.strip_chars().fill_null(""),
                    pl.when(pl.col(f"column_{code}").is_null())
                    .then(
                        pl.col(f"column_{place}")
                        + pl.lit(":")
                        + pl.col(f"column_{name if name is not None else place}")
                    )
                    .otherwise(pl.lit("")),
                ).alias("origin"),
                (
                    pl.col(f"column_{commission}").str.strip_chars()
                    if commission is not None
                    else pl.lit(None, pl.String)
                ).alias("commission"),
                number(electors).alias("electors"),
                number(cast).alias("cast"),
                number(valid).alias("valid"),
                number(invalid).alias("invalid"),
                number(yes).alias("yes"),
                number(no).alias("no"),
            )
        )
    return pl.concat(frames)


def read_all() -> dict[str, dict[str, pl.DataFrame]]:
    q96 = {k: (19 + 6 * (k - 1) + 3, 19 + 6 * (k - 1), 19 + 6 * (k - 1) + 4, 19 + 6 * (k - 1) + 5) for k in range(1, 5)}
    g96 = {k: (16 + 6 * (k - 1) + 3, 16 + 6 * (k - 1), 16 + 6 * (k - 1) + 4, 16 + 6 * (k - 1) + 5) for k in range(1, 5)}
    out: dict[str, dict[str, pl.DataFrame]] = {}
    out["1996a"] = {
        "gmina": layout("1996", "gmina", 0, 3, None, 5, 7, {1: (11, 8, 12, 13)}, 5),
        "obwod": layout("1996", "obwod", 0, 3, 4, 8, 10, {1: (14, 11, 15, 16)}, 5),
    }
    out["1996b"] = {
        "gmina": layout("1996", "gmina", 0, 3, None, 5, 15, g96, 5),
        "obwod": layout("1996", "obwod", 0, 3, 4, 8, 18, q96, 5),
    }
    out["1997"] = {
        "gmina": layout("1997", "zip", 0, 3, None, 5, 7, {1: (11, 8, 12, 13)}, 5, member="ref97gm.xls"),
        "obwod": layout("1997", "zip", 0, 3, 4, 8, 10, {1: (14, 11, 15, 16)}, 5, member="ref97obw.xls"),
    }
    out["2003"] = {
        "gmina": layout("2003", "gmina", 0, 1, None, 5, 8, {1: (10, 9, 11, 12)}),
        "obwod": layout("2003", "obwod", 0, 1, 3, 6, 9, {1: (11, 10, 12, 13)}),
    }
    q15 = {k: (23 + 4 * (k - 1), 22 + 4 * (k - 1), 24 + 4 * (k - 1), 25 + 4 * (k - 1)) for k in range(1, 4)}
    q15o = {k: (v[0] + 7, v[1] + 7, v[2] + 7, v[3] + 7) for k, v in q15.items()}
    out["2015"] = {
        "gmina": layout("2015", "gmina", 0, 1, None, 5, 21, q15),
        "obwod": layout("2015", "obwod", 0, 1, 4, 12, 28, q15o),
    }
    q23 = {k: (24 + 4 * (k - 1), 25 + 4 * (k - 1), 26 + 4 * (k - 1), 27 + 4 * (k - 1)) for k in range(1, 5)}
    q23o = {k: (v[0] + 2, v[1] + 2, v[2] + 2, v[3] + 2) for k, v in q23.items()}
    out["2023"] = {
        "gmina": layout("2023", "gmina", 0, 1, None, 7, 23, q23, unplaced="zagranica"),
        "obwod": layout("2023", "obwod", 2, 4, 0, 9, 25, q23o, unplaced="zagranica", name=3),
    }
    return out


def annul(frame: pl.DataFrame) -> pl.DataFrame:
    for commission, electors, cast, invalid, valid, yes, no in ANNULLED_2003:
        row = (
            (pl.col("commission") == commission)
            & (pl.col("electors") == electors)
            & (pl.col("cast") == cast)
            & (pl.col("invalid") == invalid)
            & (pl.col("valid") == valid)
            & (pl.col("yes") == yes)
            & (pl.col("no") == no)
        )
        if frame.filter(row).height != 1:
            raise ValueError(f"polling district {commission} annulled in 2003 not found once")
        frame = frame.filter(~row)
    return frame


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    frames: dict[str, list[pl.DataFrame]] = {"gmina": [], "obwod": []}
    for key, levels in read_all().items():
        for level, frame in levels.items():
            frame = frame.with_columns(ref=pl.lit(key))
            if key == "1997":
                frame = frame.with_columns(
                    no=pl.when(pl.col("no") > pl.col("valid"))
                    .then(pl.col("valid") - pl.col("yes"))
                    .otherwise(pl.col("no"))
                )
                if level == "gmina":
                    frame = frame.with_columns(
                        no=pl.when(pl.col("yes") + pl.col("no") != pl.col("valid"))
                        .then(pl.col("valid") - pl.col("yes"))
                        .otherwise(pl.col("no"))
                    )
            if level == "gmina":
                frame = frame.group_by("ref", "q", "gmina", maintain_order=True).agg(
                    pl.col("electors", "cast", "valid", "invalid", "yes", "no").sum()
                )
            if level == "obwod" and frame.select("q", "origin", "commission").is_duplicated().any():
                dupes = frame.filter(pl.struct("q", "origin", "commission").is_duplicated())
                print(f"{key}: {dupes.height} duplicate polling districts: {dupes.head(3).rows()}")
            bad = frame.filter(
                pl.col("valid").is_not_null()
                & ((pl.col("yes") + pl.col("no") != pl.col("valid")) | (pl.col("valid") > pl.col("cast")))
            )
            blank = frame.filter(pl.col("valid").is_null()).height
            print(f"{key} {level}: {frame.height} rows, {blank} blank, {bad.height} not adding up")
            if bad.height:
                print(bad.head(3).rows())
            if key == "2003" and level == "obwod":
                frame = annul(frame)
            frames[level].append(frame)
    for level, collected in frames.items():
        pl.concat(collected).write_parquet(PARSED / f"{level}.parquet")
    by_gmina = pl.read_parquet(PARSED / "gmina.parquet")
    by_obwod = pl.read_parquet(PARSED / "obwod.parquet")
    columns = ["electors", "cast", "valid", "yes", "no"]
    summed = by_obwod.group_by("ref", "q", "gmina").agg(pl.col(columns).sum())
    differ = by_gmina.join(summed, on=["ref", "q", "gmina"], how="full", suffix="_obwod", coalesce=True).filter(
        pl.any_horizontal(pl.col(c).fill_null(-1) != pl.col(f"{c}_obwod").fill_null(-1) for c in columns)
    )
    for key, (electors, ballots, questions) in NATIONAL.items():
        for number_, (invalid, valid, yes, no) in questions.items():
            got = (
                by_obwod.filter((pl.col("ref") == key) & (pl.col("q") == number_))
                .select(pl.col("electors", "cast", "invalid", "valid", "yes", "no").sum())
                .row(0)
            )
            want = (electors, ballots, invalid, valid, yes, no)
            if got != want:
                print(f"{key} question {number_}: polling districts {got}, notice {want}")
    print(f"{differ.height} gmina rows differ from their polling districts")
    print(differ.head(12).select("ref", "q", "gmina", *columns, *[f"{c}_obwod" for c in columns]).rows())


def emit() -> Path:
    by_obwod = pl.read_parquet(PARSED / "obwod.parquet").filter(pl.col("valid").is_not_null())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Polish national referendums of 1996, 1997, 2003, 2015, and 2023: national results from the "
                "Państwowa Komisja Wyborcza's notices, and gmina and polling-district results from the Krajowe Biuro "
                "Wyborcze's files"
            ),
            polity="PL",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        poland = cs.polity("PL")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PAGE,
            license="Official public record, no license stated",
            attribution="Źródło danych: Państwowa Komisja Wyborcza, Krajowe Biuro Wyborcze.",
        )
        for year, files in FILES.items():
            for kind in files:
                cs.artifact(raw.cached(SOURCE, url(year, kind)), source)
        notices: dict[str, UUID] = {}
        for key, eli in NOTICES.items():
            title = (
                f"Obwieszczenie Państwowej Komisji Wyborczej o wynikach referendum ({eli.replace('DU/', 'Dz. U. ')})"
            )
            notices[key] = cs.source(NOTICE_PUBLISHER, title)
            cs.add(
                "source",
                id=notices[key],
                kind="official_body",
                publisher=NOTICE_PUBLISHER,
                title=title,
                url=notice_url(eli),
                license="Public record: official legislation, free to reuse",
                license_url="https://isap.sejm.gov.pl/isap.nsf/home.xsp",
            )
            cs.artifact(raw.cached(SOURCE, notice_url(eli)), notices[key])
        notice_for = {
            "1996a": "1996",
            "1996b": "1996",
            "1997": "1997c",
            "2003": "2003c",
            "2015": "2015",
            "2023": "2023",
        }

        system = cs.key("pl.electoral_system", "referendum")
        cs.add("electoral_system", id=system, polity_id=poland, rules={"formula": "referendum"}, rules_version=1)
        name(system, "Polish national referendum")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM_NOTE)

        unit_rows: dict[UUID, dict[str, str | None]] = {}
        values: list[tuple[str, str, str | None, int, int]] = []
        [(nation_area,)] = conn.execute("select id from area where scheme_code = 'pl.nation'").fetchall()
        nation = cs.unit("pl.nation", "PL")
        unit_rows[nation] = {
            "scheme_code": "pl.nation",
            "code": "PL",
            "parent_id": None,
            "area_relation": "equals",
            "area_id": str(nation_area),
            "kind": "standard",
        }
        areas = {
            code[:6]: area
            for code, area in conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'pl.gmina'"
            ).fetchall()
        }
        gmina_units: dict[str, UUID] = {}

        def gmina_unit(code: str) -> UUID:
            if code not in gmina_units:
                unit = gmina_units[code] = cs.unit("pl.gmina", code)
                area = areas.get(code)
                unit_rows[unit] = {
                    "scheme_code": "pl.gmina",
                    "code": code,
                    "parent_id": None,
                    "area_relation": "equals" if area is not None else "unlocated",
                    "area_id": None if area is None else str(area),
                    "kind": "standard" if code.isdigit() else "aggregate",
                }
            return gmina_units[code]

        def put(result_set: UUID, unit: UUID, electors: int, ballots: int, valid: int) -> None:
            for measure, value in (
                (REGISTERED, electors),
                (BALLOTS, ballots),
                (VALID, valid),
                (INVALID, ballots - valid),
            ):
                values.append((str(result_set), str(unit), None, measure, value))

        for ref, (year, first, day, english, native, slug_, binding, questions) in REFERENDUMS.items():
            electors, ballots, national = NATIONAL[ref]
            election = cs.key("pl.election", f"{day}:referendum:{ref}")
            cs.add(
                "election",
                id=election,
                polity_id=poland,
                kind="referendum",
                date_from=first,
                date_to=day,
                status="held",
            )
            name(election, english)
            name(election, native, "pl", "native")
            path = f"pl/{slug_}"
            cs.add("slug", path=path, entity_id=election)
            local = by_obwod.filter(pl.col("ref") == ref)
            for number_, (english_name, native_name, text, text_en) in enumerate(questions, 1):
                _, valid, yes, no = national[number_]
                key = f"{day}:{ref}:{number_}"
                contest = cs.key(f"{SOURCE}.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="ballot_question",
                    electoral_system_id=system,
                    round_number=1,
                    default_result_kind="counted",
                )
                cs.add("ballot_question", contest_id=contest, type="referendum")
                label = f"{english}, question {number_}" if len(questions) > 1 else english
                name(contest, f"{label}: {english_name}")
                name(contest, f"{native_name} ({number_})" if len(questions) > 1 else native_name, "pl", "native")
                cs.add(
                    "slug",
                    path=f"{path}/question-{number_}" if len(questions) > 1 else f"{path}/question",
                    entity_id=contest,
                )
                for lang, body in (("pl", text), ("en", text_en)):
                    if body:
                        cs.add(
                            "ballot_question_text",
                            id=uuid5(NAMESPACE, f"ballot_question_text\x1f{contest}\x1f{lang}"),
                            contest_id=contest,
                            kind="text",
                            lang=lang,
                            body=body,
                            url=notice_url(NOTICES[notice_for[ref]]),
                            source_id=notices[notice_for[ref]],
                            locator=None,
                        )
                options = {}
                for position, (answer, tag, label_en) in enumerate((("yes", "TAK", "Yes"), ("no", "NIE", "No")), 1):
                    options[answer] = cs.key(f"{SOURCE}.option", f"{key}:{answer}")
                    cs.add(
                        "contest_option",
                        id=options[answer],
                        contest_id=contest,
                        kind="answer",
                        ballot_position=position,
                        ballot_label=tag,
                    )
                    name(options[answer], label_en)
                    name(options[answer], tag, "pl", "native")

                def make_set(label: str, default: bool, completeness: float, note: str, origin: UUID) -> UUID:
                    result_set = cs.key(f"{SOURCE}.result_set", f"{key}:{label}")
                    cs.add(
                        "result_set",
                        id=result_set,
                        election_id=election,
                        contest_id=contest,
                        stage="certified" if default else "official",
                        kind="counted",
                        status="active",
                        source_id=origin,
                        is_default=default,
                        completeness=completeness,
                    )
                    cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
                    return result_set

                extra = (
                    ""
                    if binding
                    else " Fewer than half of the voters entitled to vote took part, so the result did not bind."
                )
                national_set = make_set(
                    "national",
                    True,
                    1,
                    f"National totals as the Państwowa Komisja Wyborcza's notice in the Dziennik Ustaw prints them: "
                    f"voters entitled to vote, ballots cast (valid ballots, the voters who took part), and valid "
                    f"votes on the question. Invalid is the ballots cast less the valid votes.{extra}",
                    notices[notice_for[ref]],
                )
                put(national_set, nation, electors, ballots, valid)
                values.append((str(national_set), str(nation), str(options["yes"]), VOTES, yes))
                values.append((str(national_set), str(nation), str(options["no"]), VOTES, no))
                winner = "yes" if yes > no else "no"
                for answer, option in options.items():
                    cs.add(
                        "outcome",
                        result_set_id=national_set,
                        option_id=option,
                        kind="won" if answer == winner else "lost",
                    )

                rows = local.filter(pl.col("q") == number_)
                completeness = min(int(rows["valid"].sum()) / valid, 1)
                gmina_set = make_set("gmina", False, completeness, GMINA_METHOD + extra, source)
                obwod_set = make_set("obwod", False, completeness, OBWOD_METHOD + extra, source)
                for row in (
                    rows.group_by("gmina")
                    .agg(pl.col("electors", "cast", "valid", "yes", "no").sum())
                    .iter_rows(named=True)
                ):
                    unit = gmina_unit(row["gmina"])
                    put(gmina_set, unit, row["electors"], row["cast"], row["valid"])
                    values.append((str(gmina_set), str(unit), str(options["yes"]), VOTES, row["yes"]))
                    values.append((str(gmina_set), str(unit), str(options["no"]), VOTES, row["no"]))
                for row in rows.iter_rows(named=True):
                    code = f"{day}:{row['gmina'] if row['gmina'].isdigit() else row['origin']}:{row['commission']}"
                    unit = cs.unit("pl.obwod", code)
                    unit_rows[unit] = {
                        "scheme_code": "pl.obwod",
                        "code": code,
                        "parent_id": str(gmina_unit(row["gmina"])),
                        "area_relation": "unlocated",
                        "area_id": None,
                        "kind": "standard" if row["gmina"].isdigit() else "aggregate",
                    }
                    put(obwod_set, unit, row["electors"], row["cast"], row["valid"])
                    values.append((str(obwod_set), str(unit), str(options["yes"]), VOTES, row["yes"]))
                    values.append((str(obwod_set), str(unit), str(options["no"]), VOTES, row["no"]))

        minted = [
            {"id": str(unit), "polity_id": str(poland), **row} for unit, row in unit_rows.items() if unit in cs.minted
        ]
        cs.frame(
            "reporting_unit", pl.DataFrame(minted, schema_overrides={"parent_id": pl.String, "area_id": pl.String})
        )
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "measure": pl.Int16,
                    "value": pl.Int64,
                },
                orient="row",
            ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
        )
        return cs.write()
