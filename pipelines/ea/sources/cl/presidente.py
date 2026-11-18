"""Chile's direct presidential elections under the 1925 Constitution, by province, from the Registro Electoral's returns.

The Servicio Electoral (Servel) publishes the Dirección del Registro Electoral's printed returns of each election as
scanned PDFs, one per year. The scans have no text layer, so each province's totals were keyed by hand from the page
images and checked against the printed sums; `presidente.tsv` holds every keyed number with its page and its box,
found on the page image around the keyed row and column.
"""

import json
from datetime import date, datetime
from pathlib import Path
from typing import NamedTuple
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.cl import servel
from ea.store import DATA

SOURCE = "cl.presidente"
CHILE = servel.CHILE
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fCHL"))
LISTING = "https://www.servel.cl/resultados-electorales-1925-1973/"
BROWSER = "browser://20261117-073136-9adcc0/"
MEMORIA = "https://www.memoriachilena.gob.cl/archivos2/pdfs/MC0043135.pdf"
INBOX = DATA / "inbox" / SOURCE
VALUES = Path(__file__).with_name("presidente.tsv")
SCHEME = "cl.provincia_historica"
PRESIDENCY = servel.PRESIDENCY
DPI = 150

# Measures
VOTES, REGISTERED, CAST, INVALID, BLANK = 1, 2, 3, 5, 6


class Column(NamedTuple):
    key: str  # the column's key in presidente.tsv
    kind: str  # candidate, others, invalid, blank, cast, registered
    label: str  # the printed heading, or the candidate's printed name


class Election(NamedTuple):
    day: date
    kind: str
    file: str
    period: str  # the provincial division the returns use
    columns: tuple[Column, ...]
    winner: str
    registered: int | None  # the national roll the returns print, if any
    printed: str  # the date in the returns' heading
    caveat: str
    advanced: tuple[str, ...] = ()  # with no majority, the two candidates the Congreso Pleno chose between
    misprints: tuple[str, ...] = ()  # provinces whose printed figures do not add up, and columns that differ nationally
    areas: tuple[
        tuple[str, str], ...
    ] = ()  # provinces whose area belongs to another division in this election, by that division
    absent: tuple[str, ...] = ()  # provinces of the division that did not yet exist, being part of another one
    misprinted: str = ""  # why the printed figures disagree, when the returns' misprint is not a candidate's figure


def _c(key: str, name: str) -> Column:
    return Column(key, "candidate", name)


SCATTERED = Column("scattered", "others", "Votos dispersos y en blanco")
TOTAL = Column("total", "cast", "Total de votantes")
SHAREPOINT = (
    "https://servelcl-my.sharepoint.com/personal/archivo-historicoii_servel_cl/Documents/RESULTADOS%20HISTORICOS/"
    "PRESIDENCIALES/1925/1925.pdf"
)

ELECTIONS: dict[int, Election] = {
    1925: Election(
        date(1925, 10, 24),
        "general",
        SHAREPOINT,
        "1925",
        (
            _c("figueroa", "Emiliano Figueroa"),
            _c("salas", "José Santos Salas"),
            Column("invalid", "invalid", "Votos en blanco y nulos"),
            TOTAL,
        ),
        "figueroa",
        302142,
        "22 de octubre de 1925",
        (
            "The returns print blank and null votes as one figure, stored here as invalid ballots. Their headings date "
            "the election 22 October 1925; it was held on 24 October. For O'Higgins the summary table prints 2.316 "
            "votes for Salas, which does not add up to its total; the province's own table prints 2.116, which does, "
            "and that figure is used."
        ),
    ),
    1927: Election(
        date(1927, 5, 22),
        "special",
        BROWSER + "1927.pdf",
        "1925",
        (_c("ibanez", "Carlos Ibáñez del Campo"), SCATTERED, TOTAL),
        "ibanez",
        None,
        "22 de mayo de 1927",
        (
            "The returns print scattered and blank votes as one figure, stored here as a single option. They print no "
            "total for Tarapacá, so its figures are the sum of its two departments, Pisagua and Tarapacá."
        ),
    ),
    1931: Election(
        date(1931, 10, 4),
        "special",
        BROWSER + "1931.pdf",
        "1925",
        (
            _c("montero", "Juan Esteban Montero"),
            _c("alessandri", "Arturo Alessandri"),
            _c("hidalgo", "Manuel Hidalgo"),
            _c("lafferte", "Elías Lafferte"),
            SCATTERED,
            TOTAL,
        ),
        "montero",
        388959,
        "4 de octubre de 1931",
        "The returns print scattered and blank votes as one figure, stored here as a single option.",
    ),
    1932: Election(
        date(1932, 10, 30),
        "special",
        BROWSER + "1932.pdf",
        "1928",
        (
            _c("alessandri", "Arturo Alessandri Palma"),
            _c("rodriguez", "Héctor Rodríguez de la Sotta"),
            _c("zanartu", "Enrique Zañartu Prieto"),
            _c("grove", "Marmaduque Grove Vallejo"),
            _c("lafferte", "Elías Lafferte Gaviño"),
            SCATTERED,
            TOTAL,
        ),
        "alessandri",
        464879,
        "30 de octubre de 1932",
        (
            "The returns print scattered and blank votes as one figure, stored here as a single option. Aysén and "
            "Magallanes were not called to vote. The national roll is the number of registrations the Registry "
            "counted on election day, before the cancellations it lists."
        ),
    ),
    1938: Election(
        date(1938, 10, 25),
        "general",
        BROWSER + "1938.pdf",
        "1937",
        (
            Column("registered", "registered", "Inscritos hábiles"),
            _c("aguirre", "Pedro Aguirre Cerda"),
            _c("ross", "Gustavo Ross Santa María"),
            _c("ibanez", "Carlos Ibáñez del Campo"),
            Column("scattered", "others", "Dispersos, a diversos nombres"),
            Column("blank", "blank", "En blanco"),
            Column("invalid", "invalid", "Votos anulados no escrutados por el Tribunal Calificador"),
            TOTAL,
        ),
        "aguirre",
        None,
        "25 de octubre de 1938",
        (
            "Province totals from the Registro Electoral's general result by communes, departments and provinces, as "
            "the Tribunal Calificador counted the election in its session of 12 December 1938. The returns also print "
            "each department and commune, which this result set does not hold. Registered voters (inscritos hábiles) "
            "are printed by province and stored as such. Blank votes and the null votes the Tribunal did not count "
            "are separate columns. Osorno was still a department of Valdivia, so Valdivia's figures cover the "
            "province as it stood before Osorno was made a province in 1940. Carlos Ibáñez withdrew before the vote; "
            "the returns still credit him with 112 votes. In Arauco the 18 votes of the province row sit under the "
            "null votes, where its departments print them; read so, every column adds up to the national figures. "
            "Chiloé's printed total of voters, 5,770, is ten fewer than its own figures and its departments add up "
            "to (5,780); the national total of 443,888 includes the 5,780. Chiloé's figure is stored as printed. "
            "Pedro Aguirre Cerda won an absolute majority of the valid votes and was proclaimed President."
        ),
        misprints=("Chiloé", "total"),
        misprinted=(
            "The returns print Chiloé's total of voters as 5,770, ten fewer than its own figures and its departments "
            "add up to, while the printed national total counts 5,780; both are stored as printed, as the result "
            "set's caveat says."
        ),
        areas=(("Valdivia", "1938"),),
        absent=("Osorno",),
    ),
    1942: Election(
        date(1942, 2, 1),
        "special",
        BROWSER + "1942.pdf",
        "1937",
        (
            _c("rios", "Juan Antonio Ríos Morales"),
            _c("ibanez", "Carlos Ibáñez del Campo"),
            Column("scattered", "others", "Dispersos"),
            Column("blank", "blank", "En blanco"),
            Column("invalid", "invalid", "Nulos o viciados"),
            TOTAL,
        ),
        "rios",
        581486,
        "1 de febrero de 1942",
        (
            "Province totals from the Registro Electoral's summary by communes, departments and provinces, as the "
            "Tribunal Calificador counted the election. The returns also print each department and commune, the "
            "registered voters and the share who voted by province, which this result set does not hold. Blank "
            "votes and null (viciados) votes are separate columns. The returns give 80.2 percent of the roll as "
            "having voted. In Antofagasta the province row prints 56 under the null votes and 87 under the blank "
            "votes; its departments add up to 87 null and 56 blank, and the national figures only add up with "
            "those, so they are stored that way, each box on the figure as printed. Concepción's Ibáñez figure is "
            "marred by a speck on the scan; it is read as 9,042, which its departments add up to and which makes "
            "the province's total add up. Juan Antonio Ríos won an absolute majority of the valid votes and was "
            "proclaimed President."
        ),
    ),
    1946: Election(
        date(1946, 9, 4),
        "special",
        MEMORIA,
        "1937",
        (
            _c("gonzalez", "Gabriel González Videla"),
            _c("cruzcoke", "Eduardo Cruz Coke"),
            _c("alessandri", "Fernando Alessandri Rodríguez"),
            _c("ibanez", "Bernardo Ibáñez"),
            Column("scattered", "others", "Dispersos"),
            Column("blank", "blank", "En blanco"),
            Column("invalid", "invalid", "Viciados (nulos)"),
            TOTAL,
        ),
        "gonzalez",
        631257,
        "4 de septiembre de 1946",
        (
            "Province totals from the Tribunal Calificador's general final count, as the Dirección del Registro "
            "Electoral printed it, in the copy the Biblioteca Nacional de Chile holds. The count prints each "
            "department's figures too, which this result set does not hold. Blank and null votes are separate "
            "columns. The returns give about 75.9 percent of the roll as having voted. In Aconcagua the printed "
            "figures add up to 12,620, ten more than its printed total of 12,610: the department figures sum to "
            "4,673 votes for González Videla, not the 4,683 the province column prints, and the national figure for "
            "him is the sum of the printed province figures. The printed national total for Ibáñez, 12,114, is ten "
            "fewer than his province figures add up to. Each is stored as printed. No candidate won an absolute "
            "majority, so the Congreso Pleno chose between Gabriel González Videla and Eduardo Cruz Coke, and "
            "elected González Videla on 24 October 1946; its vote is not part of these returns."
        ),
        ("gonzalez", "cruzcoke"),
        ("Aconcagua", "ibanez"),
    ),
    1952: Election(
        date(1952, 9, 4),
        "general",
        BROWSER + "1952.pdf",
        "1937",
        (
            _c("ibanez", "Carlos Ibáñez del Campo"),
            _c("matte", "Arturo Matte Larraín"),
            _c("alfonso", "Pedro E. Alfonso Barrios"),
            _c("allende", "Salvador Allende Gossens"),
            Column("invalid", "invalid", "Votos nulos y en blanco"),
            TOTAL,
        ),
        "ibanez",
        None,
        "4 de septiembre de 1952",
        (
            "Province totals from the Tribunal Calificador's summary table by province. The table also prints each "
            "candidate's figure and the null and blank votes for the men's and the women's registers, and the "
            "registered voters by province; this result set holds the totals only, and the men's and women's figures "
            "are keyed to check them. Null and blank votes are printed as one figure for each register, stored here "
            "as invalid ballots. In Valparaíso, Arauco and Valdivia the men's null-and-blank figure is smudged on the "
            "scan; it is set so that valid votes plus null and blank votes equal the printed total of voters, which "
            "also makes the nation's men's figure add up. No candidate won an absolute majority, so the Congreso "
            "Pleno chose between Carlos Ibáñez del Campo and Arturo Matte Larraín, and elected Ibáñez; its vote is "
            "not part of these returns."
        ),
        ("ibanez", "matte"),
    ),
    1958: Election(
        date(1958, 9, 4),
        "general",
        BROWSER + "1958.pdf",
        "1937",
        (
            _c("alessandri", "Jorge Alessandri Rodríguez"),
            _c("bossay", "Luis Bossay Leiva"),
            _c("zamorano", "Antonio Zamorano Herrera"),
            _c("allende", "Salvador Allende Gossens"),
            _c("frei", "Eduardo Frei Montalva"),
            Column("invalid", "invalid", "Nulos y blancos"),
            TOTAL,
        ),
        "alessandri",
        None,
        "4 de septiembre de 1958",
        (
            "The returns print null and blank votes as one figure, stored here as invalid ballots. They also print "
            "each figure for the men's and the women's registers, which this result set does not hold. No candidate "
            "won an absolute majority, so the Congreso Pleno chose between Jorge Alessandri and Salvador Allende, and "
            "elected Alessandri on 24 October 1958; its vote is not part of these returns. The national figures are "
            "the returns' own totals."
        ),
        ("alessandri", "allende"),
    ),
    1970: Election(
        date(1970, 9, 4),
        "general",
        BROWSER + "1970.pdf",
        "1937",
        (
            _c("tomic", "Radomiro Tomic Romero"),
            _c("alessandri", "Jorge Alessandri Rodríguez"),
            _c("allende", "Salvador Allende Gossens"),
            Column("invalid", "invalid", "Nulos y blancos"),
            TOTAL,
        ),
        "allende",
        None,
        "4 de septiembre de 1970",
        (
            "Province totals from the Registry's summary table of the election, which also prints each figure for "
            "the men's and the women's registers; this result set holds the totals only. Null and blank votes are "
            "printed as one figure, stored here as invalid ballots. No candidate won an absolute majority, so the "
            "Congreso Pleno chose between Salvador Allende and Jorge Alessandri, and elected Allende on 24 October "
            "1970; its vote is not part of these returns."
        ),
        ("allende", "alessandri"),
    ),
}

# The provinces each division's returns list, in their printed order, with the province's name as the TSV keys it.
PROVINCES: dict[str, tuple[str, ...]] = {
    "1925": (
        "Tarapacá",
        "Antofagasta",
        "Atacama",
        "Coquimbo",
        "Aconcagua",
        "Valparaíso",
        "Santiago",
        "O'Higgins",
        "Colchagua",
        "Curicó",
        "Talca",
        "Linares",
        "Maule",
        "Ñuble",
        "Concepción",
        "Arauco",
        "Bío-Bío",
        "Malleco",
        "Cautín",
        "Valdivia",
        "Llanquihue",
        "Chiloé",
    ),
    "1928": (
        "Tarapacá",
        "Antofagasta",
        "Atacama",
        "Coquimbo",
        "Aconcagua",
        "Santiago",
        "Colchagua",
        "Talca",
        "Maule",
        "Ñuble",
        "Concepción",
        "Bío-Bío",
        "Cautín",
        "Valdivia",
        "Llanquihue",
        "Chiloé",
    ),
    "1937": (
        "Tarapacá",
        "Antofagasta",
        "Atacama",
        "Coquimbo",
        "Aconcagua",
        "Valparaíso",
        "Santiago",
        "O'Higgins",
        "Colchagua",
        "Curicó",
        "Talca",
        "Maule",
        "Linares",
        "Ñuble",
        "Concepción",
        "Arauco",
        "Bío-Bío",
        "Malleco",
        "Cautín",
        "Valdivia",
        "Osorno",
        "Llanquihue",
        "Chiloé",
        "Aysén",
        "Magallanes",
    ),
    "1938": ("Valdivia",),
}
DIVISIONS = {
    "1925": "provincial division of 1925 to 1927",
    "1928": "provincial division of 1928 to 1936",
    "1937": "provincial division of 1937 to 1974",
    "1938": "provincial division of 1937 to 1940, with Osorno",
}
SYSTEM_RULES = {"formula": "contingent", "majority": 0.5, "chooser": "legislature", "among": 2}
SYSTEM_NOTE = (
    "The candidate with an absolute majority of the votes cast was elected; otherwise the Congreso Pleno (both "
    "chambers in joint session) chose between the two candidates with the most votes (1925 Constitution, article 64)."
)
METHOD = (
    "Province totals from the Dirección del Registro Electoral's printed returns of the election, as the Servicio "
    "Electoral (Servel) publishes them in its historical archive. The PDF is a scan without a text layer, so the "
    "numbers were keyed by hand from the page images; every province's figures add up to its printed total, the "
    "provinces add up to the printed national totals (a caveat says where they do not), and each number keeps its "
    "page and box. The returns also print each department (and, from 1938, each commune), which this result set does not hold."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def slug(text: str) -> str:
    import unicodedata

    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return "".join(ch if ch.isalnum() else "-" for ch in folded).strip("-")


def area(period: str, province: str) -> str:
    return key("area", SCHEME, f"{period}:{slug(province)}")


def units(election: Election) -> list[tuple[str, str]]:
    """The provinces an election's returns list, in printed order, each with the division its area belongs to."""
    return [
        (dict(election.areas).get(name, election.period), name)
        for name in PROVINCES[election.period]
        if name not in election.absent
    ]


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{e.day}" for e in ELECTIONS.values())]


def fetch() -> None:
    """Load the returns saved from Servel's archive (data/inbox/cl.presidente/) into the raw cache."""
    index = json.loads((INBOX / "index.json").read_text())
    wanted = {e.file for e in ELECTIONS.values()}
    for url, entry in index.items():
        if url not in wanted:
            continue
        raw.store(
            SOURCE,
            entry.get("source", url),
            (INBOX / entry["file"]).read_bytes(),
            suffix=Path(entry["file"]).suffix,
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            media_type=entry["media_type"],
        )


def _cached(election: Election) -> raw.Artifact:
    index = json.loads((INBOX / "index.json").read_text()) if (INBOX / "index.json").exists() else {}
    return raw.cached(SOURCE, index.get(election.file, {}).get("source", election.file))


def sexed(column: str) -> bool:
    """Whether a keyed column is a men's or women's figure, kept to check the totals and never stored."""
    return column.endswith(("_m", "_f"))


def values() -> pl.DataFrame:
    """Every keyed number, with province rows printed in parts (one per department) added together."""
    frame = pl.read_csv(VALUES, separator="\t", schema_overrides={"value": pl.Int64})
    return frame.group_by("year", "province", "column", maintain_order=True).agg(
        pl.col("value").sum(),
        pl.col("page").first(),
        pl.col("width").first(),
        pl.col("height").first(),
        pl.col("x0").min(),
        pl.col("y0").min(),
        pl.col("x1").max(),
        pl.col("y1").max(),
        pl.len().alias("parts"),
    )


def parse() -> None:
    frame = values()
    for year, election in ELECTIONS.items():
        rows = frame.filter(pl.col("year") == year)
        provinces = rows.filter(pl.col("province") != "TOTAL")
        names = provinces["province"].unique(maintain_order=True).to_list()
        if sorted(names) != sorted(name for _, name in units(election)):
            raise ValueError(f"{year}: provinces {names} differ from the {election.period} division")
        keys = {c.key for c in election.columns}
        if {c for c in rows["column"] if not sexed(c)} != keys:
            raise ValueError(f"{year}: columns {sorted(set(rows['column']))}, expected {sorted(keys)}")
        parts = [c.key for c in election.columns if c.kind not in ("cast", "registered")]
        for name in names:
            row = dict(provinces.filter(pl.col("province") == name).select("column", "value").iter_rows())
            if name not in election.misprints and sum(row.get(k, 0) for k in parts) != row["total"]:
                raise ValueError(f"{year} {name}: the parts do not add up to the printed total")
            for k in parts:
                if f"{k}_m" in row and row[f"{k}_m"] + row[f"{k}_f"] != row[k]:
                    raise ValueError(f"{year} {name} {k}: the men's and women's figures do not add up to the total")
        national = dict(rows.filter(pl.col("province") == "TOTAL").select("column", "value").iter_rows())
        summed = provinces.group_by("column").agg(pl.col("value").sum())
        for column, value in summed.iter_rows():
            if sexed(column):
                continue
            if national.get(column) != value and column not in election.misprints:
                raise ValueError(
                    f"{year} {column}: provinces add up to {value}, not the printed {national.get(column)}"
                )
    registry().write()
    for year in ELECTIONS:
        build(year).write()


def emit() -> None:
    package.run("load", packages())


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Chile's historical provinces as the Registro Electoral's presidential returns of 1925 to 1970 list them, "
        "and the presidential electoral system of the 1925 Constitution",
    )
    p.external.update({CHILE, COUNTRY})
    p.add(
        "source",
        id=key("source"),
        kind="official_body",
        publisher="Servicio Electoral de Chile (Servel)",
        title="Resultados electorales 1925-1973: elecciones presidenciales (Dirección del Registro Electoral)",
        url=LISTING,
        license="No license stated: official results published by Servel; facts published with attribution",
        attribution="Servicio Electoral de Chile (Servel)",
    )
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=CHILE,
        kind="administrative",
        rank=1,
        noun="province (historical division)",
        code_system=None,
    )
    for period, names in PROVINCES.items():
        for name in names:
            id = area(period, name)
            p.add("area", id=id, scheme_code=SCHEME)
            p.add("area_code", area_id=id, code=f"{period}:{slug(name)}")
            p.add("area_parent", area_id=id, parent_id=COUNTRY)
            p.name(id, f"Provincia de {name} ({DIVISIONS[period]})", "en", "official")
            p.name(id, f"Provincia de {name}", "es", "official", False)
    system = key("electoral_system", "president-1925")
    p.add(
        "electoral_system",
        id=system,
        polity_id=CHILE,
        valid_from=date(1925, 9, 18),
        valid_to=date(1973, 9, 11),
        rules=SYSTEM_RULES,
        rules_version=1,
    )
    p.add("note", id=key("note", system), entity_id=system, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    return p


def build(year: int) -> Package:
    election = ELECTIONS[year]
    day = election.day
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"{year} Chilean presidential election by province, from the Registro Electoral's returns (Servel)",
        depends=[f"{SOURCE}/registry", "cl.servel/registry"],
    )
    p.external.add(CHILE)
    source = key("source")
    artifact = _cached(election)
    artifact_id = p.artifact(artifact, source)
    frame = values().filter(pl.col("year") == year, ~pl.col("column").str.contains("_[mf]$"))

    election_id = key("election", day)
    p.add(
        "election",
        id=election_id,
        polity_id=CHILE,
        kind=election.kind,
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    extra = election.kind == "special"
    p.name(election_id, f"{year} Chilean presidential election", "en", "official")
    p.name(
        election_id,
        f"Elección {'extraordinaria ' if extra else ''}de Presidente de la República de {year}",
        "es",
        "native",
        False,
    )
    base = f"cl/{day}-presidential"
    p.add("slug", path=base, entity_id=election_id, is_current=True)

    contest = key("contest", day)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="office",
        office_id=PRESIDENCY,
        magnitude=1,
        round_number=1,
        electoral_system_id=key("electoral_system", "president-1925"),
        default_result_kind="counted",
    )
    p.name(contest, "Presidente de la República", "es", "official")
    p.add("slug", path=f"{base}/presidente-de-la-republica", entity_id=contest, is_current=True)

    result_set = key("result_set", day)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="certified",
        kind="counted",
        source_id=source,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    caveat = election.caveat
    if election.registered is not None:
        caveat += (
            f" The returns print the national electoral roll, {election.registered:,} registered voters, but no roll "
            "by province."
        )
    p.add("note", id=key("note", result_set, "caveat"), entity_id=result_set, kind="caveat", body_md=caveat, lang="en")

    if election.misprints:
        p.accept(
            "accounting.declared",
            election.misprinted
            or "The returns' printed national figure for a candidate differs from the sum of their printed province "
            "figures; both are stored as printed, as the result set's caveat says.",
        )

    options: dict[str, str] = {}
    measures: dict[str, int] = {}
    position = 0
    for column in election.columns:
        if column.kind in ("candidate", "others"):
            position += 1
            option = options[column.key] = key("option", day, column.key)
            measures[column.key] = VOTES
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind=column.kind,
                ballot_position=position,
                ballot_label=column.label,
            )
            if column.kind == "candidate":
                person = key("person", day, column.key)
                p.add("person", id=person)
                p.name(person, column.label, "es", "official")
                p.add(
                    "candidacy",
                    id=key("candidacy", day, column.key),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=column.label,
                    is_independent=None,
                )
        else:
            measures[column.key] = {"invalid": INVALID, "blank": BLANK, "cast": CAST, "registered": REGISTERED}[
                column.kind
            ]
    if election.advanced:
        for column in election.advanced:
            p.add("outcome", result_set_id=result_set, option_id=options[column], kind="advanced")
    else:
        p.add("outcome", result_set_id=result_set, option_id=options[election.winner], kind="elected")

    pages: dict[int, str] = {}
    for page, width, height in frame.select("page", "width", "height").unique().sort("page").iter_rows():
        pages[page] = key("page", year, page)
        p.add(
            "source_page",
            id=pages[page],
            artifact_id=artifact_id,
            page_number=page,
            image_key=f"pages/{SOURCE}/{artifact.sha256}-{page}.png",
            width=width,
            height=height,
        )

    facts: list[tuple[str, str | None, int, int]] = []
    boxes: list[tuple[str, str | None, int, str, float, float, float, float]] = []
    for period, name in units(election):
        unit = key("unit", period, slug(name))
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=CHILE,
            scheme_code=SCHEME,
            code=f"{period}:{slug(name)}",
            area_relation="equals",
            area_id=area(period, name),
            kind="standard",
        )
        for column, value, page, x0, y0, x1, y1 in (
            frame.filter(pl.col("province") == name)
            .select("column", "value", "page", "x0", "y0", "x1", "y1")
            .iter_rows()
        ):
            option = options.get(column)
            facts.append((unit, option, measures[column], value))
            if x0 is not None:
                boxes.append((unit, option, measures[column], pages[page], x0, y0, x1, y1))
    national = frame.filter(pl.col("province") == "TOTAL")
    for column, value in national.select("column", "value").iter_rows():
        p.add(
            "declared", result_set_id=result_set, option_id=options.get(column), measure=measures[column], value=value
        )

    p.frame(
        "result_value",
        pl.DataFrame(facts, schema=["unit_id", "option_id", "measure", "value"], orient="row").with_columns(
            result_set_id=pl.lit(result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    p.frame(
        "value_locator",
        pl.DataFrame(
            boxes, schema=["unit_id", "option_id", "measure", "page_id", "x0", "y0", "x1", "y1"], orient="row"
        ).with_columns(
            result_set_id=pl.lit(result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
            source_id=pl.lit(source),
        ),
    )
    return p
