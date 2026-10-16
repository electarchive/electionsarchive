import re
import unicodedata
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "fr.cdsp"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILES = {
    "legi": "https://static.data.gouv.fr/resources/elections-legislatives-1958-2012/community/20150206-172137/LEGISLATIVES_1958-2012-csv.zip",
    "presi": "https://static.data.gouv.fr/resources/elections-presidentielles-1965-2012-1/community/20150209-113725/PRESIDENTIELLES_1965-2012-csv.zip",
}
LANDING = {
    "legi": "https://www.data.gouv.fr/datasets/elections-legislatives-1958-2012",
    "presi": "https://www.data.gouv.fr/datasets/elections-presidentielles-1965-2012-1",
}
TITLES = {"legi": "Élections législatives 1958-2012", "presi": "Élections présidentielles 1965-2012"}
PUBLISHER = "Centre de données socio-politiques, Sciences Po"
REGISTERED, VALID, INVALID, VOTERS, VOTES = 2, 4, 5, 14, 1
ACCOUNTING = {"Inscrits": REGISTERED, "Votants": VOTERS, "Exprimés": VALID, "Blancs et nuls": INVALID}
SKIPPED = {"élu premier tour", "Taux de participation (%)"}
PRESIDENTIAL = {
    1965: (date(1965, 12, 5), date(1965, 12, 19)),
    1969: (date(1969, 6, 1), date(1969, 6, 15)),
    1974: (date(1974, 5, 5), date(1974, 5, 19)),
    1981: (date(1981, 4, 26), date(1981, 5, 10)),
    1988: (date(1988, 4, 24), date(1988, 5, 8)),
    1995: (date(1995, 4, 23), date(1995, 5, 7)),
    2002: (date(2002, 4, 21), date(2002, 5, 5)),
    2007: (date(2007, 4, 22), date(2007, 5, 6)),
    2012: (date(2012, 4, 22), date(2012, 5, 6)),
}
LEGISLATIVE = {
    1958: (date(1958, 11, 23), date(1958, 11, 30)),
    1962: (date(1962, 11, 18), date(1962, 11, 25)),
    1967: (date(1967, 3, 5), date(1967, 3, 12)),
    1968: (date(1968, 6, 23), date(1968, 6, 30)),
    1973: (date(1973, 3, 4), date(1973, 3, 11)),
    1978: (date(1978, 3, 12), date(1978, 3, 19)),
    1981: (date(1981, 6, 14), date(1981, 6, 21)),
    1988: (date(1988, 6, 5), date(1988, 6, 12)),
    1993: (date(1993, 3, 21), date(1993, 3, 28)),
    1997: (date(1997, 5, 25), date(1997, 6, 1)),
    2002: (date(2002, 6, 9), date(2002, 6, 16)),
    2007: (date(2007, 6, 10), date(2007, 6, 17)),
    2012: (date(2012, 6, 10), date(2012, 6, 17)),
}
MAPS = [
    (date(1958, 11, 23), date(1967, 3, 5)),
    (date(1967, 3, 5), date(1986, 3, 16)),
    (date(1988, 6, 5), date(2012, 6, 10)),
    (date(2012, 6, 10), None),
]
DEPARTEMENTS_SPLIT = date(1968, 1, 1)
CORSICA_SPLIT = date(1976, 1, 1)
OLD = {"75": "Seine", "78": "Seine-et-Oise", "20": "Corse"}
OVERSEAS = {
    "GUADELOUPE": "971",
    "MARTINIQUE": "972",
    "GUYANE": "973",
    "LA REUNION": "974",
    "SAINT PIERRE ET MIQUELON": "975",
    "MAYOTTE": "976",
    "WALLIS ET FUTUNA": "986",
    "POLYNESIE FRANCAISE": "987",
    "NOUVELLE CALEDONIE": "988",
    "SAINT BARTHELEMY ET SAINT MARTIN": "ZX",
    "SAINT MARTIN SAINT BARTHELEMY": "ZX",
    "FRANCAIS DE L ETRANGER": "ZZ",
}
CANDIDATES = {
    "MITTERRAND": "François Mitterrand",
    "LECANUET": "Jean Lecanuet",
    "DE GAULLE": "Charles de Gaulle",
    "MARCILHACY": "Pierre Marcilhacy",
    "TIXIER-VIGNANCOUR": "Jean-Louis Tixier-Vignancour",
    "BARBU": "Marcel Barbu",
    "KRIVINE": "Alain Krivine",
    "ROCARD": "Michel Rocard",
    "DUCLOS": "Jacques Duclos",
    "DEFFERRE": "Gaston Defferre",
    "POMPIDOU": "Georges Pompidou",
    "POHER": "Alain Poher",
    "DUCATEL": "Louis Ducatel",
    "LAGUILLER": "Arlette Laguiller",
    "MULLER": "Émile Muller",
    "DUMONT": "René Dumont",
    "GISCARD D'ESTAING": "Valéry Giscard d'Estaing",
    "CHABAN-DELMAS": "Jacques Chaban-Delmas",
    "RENOUVIN": "Bertrand Renouvin",
    "ROYER": "Jean Royer",
    "LE PEN": "Jean-Marie Le Pen",
    "HERAUD": "Guy Héraud",
    "SEBAG": "Jean-Claude Sebag",
    "BOUCHARDEAU": "Huguette Bouchardeau",
    "MARCHAIS": "Georges Marchais",
    "CREPEAU": "Michel Crépeau",
    "LALONDE": "Brice Lalonde",
    "CHIRAC": "Jacques Chirac",
    "DEBRE": "Michel Debré",
    "GARAUD": "Marie-France Garaud",
    "BOUSSEL": "Pierre Boussel",
    "LAJOINIE": "André Lajoinie",
    "JUQUIN": "Pierre Juquin",
    "WAECHTER": "Antoine Waechter",
    "BARRE": "Raymond Barre",
    "HUE": "Robert Hue",
    "JOSPIN": "Lionel Jospin",
    "VOYNET": "Dominique Voynet",
    "BALLADUR": "Édouard Balladur",
    "DE VILLIERS": "Philippe de Villiers",
    "CHEMINADE": "Jacques Cheminade",
    "GLUCKSTEIN": "Daniel Gluckstein",
    "BESANCENOT": "Olivier Besancenot",
    "TAUBIRA": "Christiane Taubira",
    "CHEVENEMENT": "Jean-Pierre Chevènement",
    "MAMERE": "Noël Mamère",
    "LEPAGE": "Corinne Lepage",
    "BAYROU": "François Bayrou",
    "MADELIN": "Alain Madelin",
    "BOUTIN": "Christine Boutin",
    "MEGRET": "Bruno Mégret",
    "SAINT-JOSSE": "Jean Saint-Josse",
    "SCHIVARDI": "Gérard Schivardi",
    "BOVE": "José Bové",
    "BUFFET": "Marie-George Buffet",
    "ROYAL": "Ségolène Royal",
    "SARKOZY": "Nicolas Sarkozy",
    "NIHOUS": "Frédéric Nihous",
    "Nathalie ARTHAUD": "Nathalie Arthaud",
    "Philippe POUTOU": "Philippe Poutou",
    "Jean-Luc MELENCHON": "Jean-Luc Mélenchon",
    "François HOLLANDE": "François Hollande",
    "Eva JOLY": "Eva Joly",
    "François BAYROU": "François Bayrou",
    "Nicolas SARKOZY": "Nicolas Sarkozy",
    "Nicolas DUPONT-AIGNAN": "Nicolas Dupont-Aignan",
    "Marine LE PEN": "Marine Le Pen",
    "Jacques CHEMINADE": "Jacques Cheminade",
}
PARTIES = {
    "MRP": ("wikidata", "Q1236315"),
    "UNR": ("parlgov", "1094"),
    "UDR": ("parlgov", "1094"),
    "PCF": ("wikidata", "Q192821"),
    "PC": ("wikidata", "Q192821"),
    "CD": ("wikidata", "Q2037506"),
    "PS": ("wikidata", "Q170972"),
    "RI": ("wikidata", "Q2300622"),
    "UDF": ("wikidata", "Q827415"),
    "RPR": ("wikidata", "Q1052584"),
    "MRG": ("wikidata", "Q427965"),
    "PRG": ("wikidata", "Q427965"),
    "V": ("wikidata", "Q743390"),
    "VERTS": ("wikidata", "Q743390"),
    "UMP": ("wikidata", "Q173152"),
    "MODEM": ("wikidata", "Q587370"),
}
NEW_PARTIES = {
    "CIR": "Convention des institutions républicaines",
    "LCR": "Ligue communiste révolutionnaire",
    "PSU": "Parti socialiste unifié",
    "SFIO": "Section française de l'Internationale ouvrière",
    "LO": "Lutte ouvrière",
    "MDSR": "Mouvement démocrate socialiste de France",
    "NAR": "Nouvelle Action royaliste",
    "FN": "Front national",
    "MPT": "Mouvement pour un parti des travailleurs",
    "MPF": "Mouvement pour la France",
    "POE": "Parti ouvrier européen",
    "PT": "Parti des travailleurs",
    "P. REP": "Pôle républicain",
    "CAP 21": "Citoyenneté, action, participation pour le 21e siècle",
    "DL": "Démocratie libérale",
    "MNR": "Mouvement national républicain",
    "CNPT": "Chasse, pêche, nature et traditions",
    "FG": "Front de gauche",
    "EELV": "Europe Écologie Les Verts",
    "NPA": "Nouveau Parti anticapitaliste",
    "DLR": "Debout la République",
    "SP": "Solidarité et progrès",
}
TENDENCIES_1981 = {
    "COM": "Parti communiste",
    "EXG": "Extrême gauche",
    "SOC": "Parti socialiste",
    "RADICAUX GAUCHE": "Mouvement des radicaux de gauche",
    "DVG": "Divers gauche",
    "UDF": "Union pour la démocratie française",
    "RPR": "Rassemblement pour la République",
    "DVD": "Divers droite",
    "UDF-RPR": "Union pour la nouvelle majorité",
    "FRN": "Front national",
    "EXD": "Extrême droite",
    "ECO": "Écologistes",
    "REG": "Régionalistes",
}
NUANCES = {
    "COM": "Parti communiste français",
    "SOC": "Parti socialiste",
    "RPR": "Rassemblement pour la République",
    "UDF": "Union pour la démocratie française",
    "FRN": "Front national",
    "DVD": "Divers droite",
    "DVG": "Divers gauche",
    "EXG": "Extrême gauche",
    "EXD": "Extrême droite",
    "ECO": "Écologistes",
    "REG": "Régionalistes",
    "RDG": "Mouvement des radicaux de gauche",
    "MAJ": "Majorité présidentielle",
    "EXO": "Autres",
    "DIV": "Divers",
    "VEC": "Les Verts",
    "GEC": "Génération écologie",
    "PRG": "Parti radical de gauche",
    "UMP": "Union pour un mouvement populaire",
    "MODEM": "Mouvement démocrate",
    "PSLE": "Nouveau Centre",
    "FDG": "Front de gauche",
    "NouvC": "Nouveau Centre",
    "PRV": "Parti radical",
    "MODEM-": "Centristes",
    "MPF": "Mouvement pour la France",
}
NUANCE_FIXES = {
    1988: {
        "SCO": "MAJ",
        "SDC": "MAJ",
        "EXE": "EXO",
        "RFN": "FRN",
        "RRN": "FRN",
        "UOF": "UDF",
        "UDP": "UDF",
        "UDR": "UDF",
        "RDC": "RDG",
        "PRP": "RPR",
        "ECI": "ECO",
        "ROG": "DIV",
    }
}
NOTES = {
    "legi": (
        "Results by constituency, compiled by the Banque de données socio-politiques in Grenoble and Sciences Po's "
        "CDSP from the Interior Ministry's printed returns. Before 1988 votes are grouped by political tendency "
        "rather than by candidate, and before 1988 the files cover metropolitan France only. The 1986 election, held "
        "by list proportional representation in each département, is not included."
    ),
    "presi": (
        "Results by constituency, compiled by the Banque de données socio-politiques in Grenoble and Sciences Po's "
        "CDSP. Until 1995 the files cover metropolitan France only; 2002 adds the overseas départements and "
        "territories, and 2007 and 2012 add voters abroad. Here they are summed by département."
    ),
}
TENDENCY_CAVEAT = (
    "The source groups votes by political tendency and does not name the candidates. Where two candidates of one "
    "tendency stood in a constituency, their votes are combined, and the winner is the tendency with the most votes."
)
METROPOLITAN_CAVEAT = "Metropolitan France only: the overseas départements and territories are not in the source."
UNSETTLED_CAVEAT = "No candidate won a majority, and the source has no second round for this constituency."
NO_ABROAD_CAVEAT = "French voters abroad are not in the source."
PRESIDENT = (
    "The president is elected by direct universal suffrage. A candidate who wins more than half of the valid votes in "
    "the first round is elected; otherwise the two leading candidates go to a second round two weeks later, and the "
    "one with more votes wins."
)
THRESHOLDS = {
    1958: "Any first-round candidate with at least 5 percent of the valid votes could stand in the second round.",
    1967: "Any first-round candidate with at least 10 percent of registered voters could stand in the second round.",
    1978: "Any first-round candidate with at least 12.5 percent of registered voters could stand in the second round.",
}


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z]+", " ", plain.upper()).strip()


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".zip")


def parse() -> None:
    accounting: list[pl.DataFrame] = []
    options: list[pl.DataFrame] = []
    for kind, url in FILES.items():
        with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
            members = [
                (member, match)
                for member in sorted(archive.namelist())
                if (match := re.search(r"cdsp_(?:legi|presi)(\d{4})t([12])_circ\.csv$", member))
            ]
            for member, match in members:
                year, number = int(match[1]), int(match[2])
                frame = pl.read_csv(archive.read(member), infer_schema=False, truncate_ragged_lines=True)
                columns = frame.columns
                abbreviations = frame.row(0) if frame[columns[0]][0] is None else None
                frame = frame.rename({columns[0]: "dept", columns[1]: "dept_label", columns[2]: "circ"}).filter(
                    pl.col("dept").str.strip_chars().fill_null("") != ""
                )
                if "élu premier tour" in frame.columns:
                    frame = frame.filter(pl.col("élu premier tour") == "N")
                places = {
                    f"{code}|{label}": OVERSEAS[fold(label)]
                    if code.strip()[:2] in ("97", "98", "99")
                    else code.strip().zfill(2)
                    for code, label in frame.select("dept", "dept_label").unique().iter_rows()
                }
                frame = frame.with_columns(
                    dept=pl.concat_str("dept", pl.lit("|"), "dept_label").replace_strict(places),
                    circ=pl.col("circ").str.strip_chars(),
                ).with_columns(
                    circ=pl.when(pl.lit(kind == "presi"))
                    .then(pl.lit(None, pl.String))
                    .otherwise(pl.col("dept") + pl.col("circ").str.zfill(2))
                )
                keys = [
                    pl.lit(kind).alias("kind"),
                    pl.lit(year).alias("year"),
                    pl.lit(number).alias("round"),
                    "dept",
                    "circ",
                ]
                present = [column for column in ACCOUNTING if column in frame.columns]
                accounting.append(
                    frame.unpivot(on=present, index=["dept", "circ"], variable_name="field").select(
                        *keys,
                        measure=pl.col("field").replace_strict(ACCOUNTING, return_dtype=pl.Int16),
                        value=pl.col("value").str.replace_all(r"\s", "").cast(pl.Int64),
                    )
                )
                slots = sorted({int(c.split(" ")[0]) for c in frame.columns if re.fullmatch(r"\d+ Nom candidat", c)})
                if slots:
                    blocks = []
                    for slot in slots:
                        blocks.append(
                            frame.select(
                                *keys,
                                slot=pl.lit(slot),
                                sex=pl.col(f"{slot} Sexe candidat")
                                if f"{slot} Sexe candidat" in frame.columns
                                else pl.lit(None, pl.String),
                                surname=pl.col(f"{slot} Nom candidat").str.strip_chars(),
                                forename=pl.col(f"{slot} Prénom candidat").str.strip_chars(),
                                etiquette=pl.col(f"{slot} Etiquette liste").str.strip_chars(),
                                nuance=pl.col(f"{slot} nuance").str.strip_chars(),
                                votes=pl.col(f"{slot} voix").str.replace_all(r"\s", "").cast(pl.Int64),
                                elected=pl.col(f"{slot} Elu") == "O"
                                if f"{slot} Elu" in frame.columns
                                else pl.lit(None, pl.Boolean),
                            ).filter(pl.col("surname").fill_null("") != "")
                        )
                    options.append(
                        pl.concat(blocks).with_columns(
                            nuance=pl.col("nuance").replace(NUANCE_FIXES.get(year, {})),
                            label=pl.col("surname") + " " + pl.col("forename").fill_null(""),
                        )
                    )
                    continue
                first = (
                    columns.index("Blancs et nuls") + 1
                    if "Blancs et nuls" in columns
                    else columns.index("Exprimés") + 1
                )
                rows = []
                for position, column in enumerate(columns[first:], start=first):
                    if column in SKIPPED:
                        continue
                    if kind == "presi":
                        if "(" in column:
                            person, party = column.rsplit("(", 1)
                        elif column.endswith(")"):
                            person, party = column.rsplit(" ", 1)
                        else:
                            person, party = column, ""
                        label, abbreviation = CANDIDATES[person.strip()], party.strip(" )")
                    elif abbreviations is not None:
                        label, abbreviation = column.strip(), str(abbreviations[position]).strip()
                    else:
                        label, abbreviation = TENDENCIES_1981[column], column
                    rows.append(
                        frame.select(
                            *keys,
                            slot=pl.lit(position),
                            label=pl.lit(label),
                            abbreviation=pl.lit(abbreviation),
                            votes=pl.col(column).str.replace_all(r"\s", "").cast(pl.Int64),
                        )
                    )
                options.append(pl.concat(rows))
    values = pl.concat(accounting)
    standing = pl.concat(options, how="diagonal")
    values = values.group_by("kind", "year", "round", "dept", "circ", "measure").agg(pl.col("value").sum())
    voters = values.filter(pl.col("measure") == VOTERS).select("kind", "year", "round", "dept", "circ", voters="value")
    values = (
        values.join(voters, on=["kind", "year", "round", "dept", "circ"], how="left", nulls_equal=True)
        .filter((pl.col("measure") != REGISTERED) | pl.col("voters").is_null() | (pl.col("value") >= pl.col("voters")))
        .drop("voters")
    )
    standing = (
        standing.group_by("kind", "year", "round", "dept", "circ", "label", maintain_order=True)
        .agg(
            pl.col("slot").first(),
            pl.col("votes").sum(),
            *(
                pl.col(c).first()
                for c in ("abbreviation", "sex", "surname", "forename", "etiquette", "nuance", "elected")
            ),
        )
        .filter(pl.col("votes") > 0)
    )

    keys = ["kind", "year", "round", "dept", "circ"]
    totals = values.filter(pl.col("measure") == VALID).select(*keys, valid="value")
    sums = standing.group_by(keys).agg(pl.col("votes").sum().alias("sum"))
    mismatched = totals.join(sums, on=keys, how="full", coalesce=True, nulls_equal=True).filter(
        pl.col("valid").ne_missing(pl.col("sum"))
    )
    print(mismatched.group_by("kind", "year", "round").len().sort("kind", "year", "round").rows())

    contest = ["kind", "year", "round", "circ"]
    legi = standing.filter(pl.col("kind") == "legi")
    registered = values.filter(pl.col("measure") == REGISTERED).select(*keys, registered="value")
    ranked = (
        legi.join(totals, on=keys, how="left", nulls_equal=True)
        .join(registered, on=keys, how="left", nulls_equal=True)
        .with_columns(
            top=pl.col("votes") == pl.col("votes").max().over(contest),
            ties=(pl.col("votes") == pl.col("votes").max().over(contest)).sum().over(contest),
        )
    )
    runoffs = legi.filter(pl.col("round") == 2).select("year", "circ", "label").unique()
    first = ranked.filter(pl.col("round") == 1).join(
        runoffs.select("year", "circ").unique().with_columns(runoff=pl.lit(True)), on=["year", "circ"], how="left"
    )
    unsettled = first.filter(pl.col("runoff").is_null() & pl.col("top") & (pl.col("votes") * 2 <= pl.col("valid")))
    won = first.filter(pl.col("runoff").is_null() & pl.col("top") & (pl.col("votes") * 2 > pl.col("valid")))
    second = ranked.filter((pl.col("round") == 2) & pl.col("top"))
    ties = pl.concat([won, second], how="diagonal").filter(pl.col("ties") > 1)
    if ties.height:
        raise ValueError(f"tied winners: {ties.head(5)}")
    advanced = first.filter(pl.col("runoff").fill_null(False)).join(runoffs, on=["year", "circ", "label"], how="semi")
    presi = standing.filter(pl.col("kind") == "presi")
    national = presi.group_by("year", "round", "label").agg(pl.col("votes").sum())
    leaders = national.with_columns(rank=pl.col("votes").rank("ordinal", descending=True).over("year", "round"))
    outcomes = pl.concat(
        [
            won.select(*contest, "label", outcome=pl.lit("elected")),
            second.select(*contest, "label", outcome=pl.lit("elected")),
            advanced.select(*contest, "label", outcome=pl.lit("advanced")),
            leaders.filter((pl.col("round") == 1) & (pl.col("rank") <= 2)).select(
                pl.lit("presi").alias("kind"),
                "year",
                "round",
                pl.lit(None, pl.String).alias("circ"),
                "label",
                outcome=pl.lit("advanced"),
            ),
            leaders.filter((pl.col("round") == 2) & (pl.col("rank") == 1)).select(
                pl.lit("presi").alias("kind"),
                "year",
                "round",
                pl.lit(None, pl.String).alias("circ"),
                "label",
                outcome=pl.lit("elected"),
            ),
        ]
    )
    seats = (
        outcomes.filter(pl.col("outcome") == "elected", pl.col("kind") == "legi").group_by("year").len().sort("year")
    )
    contests = legi.filter(pl.col("round") == 1).group_by("year").agg(pl.col("circ").n_unique()).sort("year")
    print(seats.join(contests, on="year").rows())

    PARSED.mkdir(parents=True, exist_ok=True)
    values.write_parquet(PARSED / "values.parquet")
    unsettled.select("year", "circ").write_parquet(PARSED / "unsettled.parquet")
    standing.join(outcomes, on=[*contest, "label"], how="left", nulls_equal=True).write_parquet(
        PARSED / "options.parquet"
    )


def emit() -> Path:
    values = pl.read_parquet(PARSED / "values.parquet")
    options = pl.read_parquet(PARSED / "options.parquet")
    unsettled = set(pl.read_parquet(PARSED / "unsettled.parquet").iter_rows())
    with connect() as conn:
        labels: dict[str, str] = dict(
            conn.execute(
                """select c.code, n.text from area a join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id
                   where a.scheme_code = 'fr.departement' and n.is_preferred"""
            ).fetchall()
        )
        labels["ZZ"] = "Français établis hors de France"
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="French presidential elections 1965–2012 by département and legislative elections 1958–2012 by constituency",
            polity="FR",
        )

        def name(entity: UUID, text: str, lang: str = "fr", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        def existing(scheme: str, value: str) -> UUID:
            entity = cs.key(scheme, value)
            if entity in cs.minted:
                raise ValueError(f"{scheme}:{value} is not in the database")
            return entity

        sources: dict[str, UUID] = {}
        for kind, url in FILES.items():
            sources[kind] = cs.source(PUBLISHER, TITLES[kind])
            cs.add(
                "source",
                id=sources[kind],
                kind="academic",
                publisher=PUBLISHER,
                title=TITLES[kind],
                url=LANDING[kind],
                license="ODbL 1.0",
                license_url="https://opendatacommons.org/licenses/odbl/1-0/",
                attribution="Centre de données socio-politiques (CDSP), Sciences Po, via data.gouv.fr",
                notes=NOTES[kind],
            )
            cs.artifact(raw.cached(SOURCE, url), sources[kind])

        france = cs.polity("FR")
        nation = cs.area("fr.nation", "FR")
        departements = {code: cs.area("fr.departement", code) for code in labels if code != "ZZ"}
        if nation in cs.minted or any(area in cs.minted for area in departements.values()):
            raise ValueError("publish fr.interieur before fr.cdsp")
        assembly = existing("fr.body", "national-assembly")
        legislative = existing("fr.electoral_system", "legislative-two-round")
        office = cs.key("fr.office", "president")
        if office in cs.minted:
            cs.add("office", id=office, polity_id=france, kind="head_of_state")
            name(office, "President of France", "en")
            name(office, "Président de la République", "fr", "native")
        presidential = cs.key("fr.electoral_system", "presidential-two-round")
        if presidential in cs.minted:
            cs.add(
                "electoral_system", id=presidential, polity_id=france, rules={"formula": "two_round"}, rules_version=1
            )
            name(presidential, "French presidential two-round system", "en")
            cs.add("note", entity_id=presidential, kind="methodology", body_md=PRESIDENT)

        def label_of(code: str, day: date) -> str:
            if code == "20" or (code in ("75", "78") and day < MAPS[1][0]):
                return OLD[code]
            return labels[code]

        def ordinal(number: int) -> str:
            return f"{number}{'re' if number == 1 else 'e'} circonscription"

        units: dict[tuple[str, date | None], UUID] = {}

        def department_unit(code: str, day: date) -> UUID:
            old = code == "20" or (code in ("75", "78") and day < DEPARTEMENTS_SPLIT)
            start = (
                DEPARTEMENTS_SPLIT
                if not old and code in ("75", "78", "91", "92", "93", "94", "95")
                else CORSICA_SPLIT
                if code in ("2A", "2B")
                else None
            )
            end = (CORSICA_SPLIT if code == "20" else DEPARTEMENTS_SPLIT) if old else None
            key = (code, start or date(1958, 1, 1))
            if key not in units:
                units[key] = cs.unit("fr.departement", code, on=key[1])
                if units[key] in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=units[key],
                        polity_id=france,
                        scheme_code="fr.departement",
                        code=code,
                        area_relation="unlocated" if code == "ZZ" else "within" if old else "equals",
                        area_id=nation if old else departements.get(code),
                        kind="abroad_mission" if code == "ZZ" else "standard",
                        valid_from=start,
                        valid_to=end,
                    )
                    name(units[key], label_of(code, day))
            return units[key]

        legi = options.filter(pl.col("kind") == "legi")
        first_seen = (
            legi.with_columns(
                day=pl.struct("year", "round").map_elements(
                    lambda row: LEGISLATIVE[row["year"]][row["round"] - 1], return_dtype=pl.Date
                )
            )
            .group_by(
                "circ",
                pl.col("year")
                .map_elements(
                    lambda year: next(
                        i
                        for i, (start, end) in enumerate(MAPS)
                        if start <= LEGISLATIVE[year][0] and (end is None or LEGISLATIVE[year][0] < end)
                    ),
                    return_dtype=pl.Int64,
                )
                .alias("era"),
            )
            .agg(pl.col("day").min(), pl.col("dept").first())
        )
        circ_areas: dict[tuple[str, int], UUID] = {}
        circ_units: dict[tuple[str, int], UUID] = {}
        for circ, era, seen, dept in first_seen.sort("era", "circ").iter_rows():
            if era == 3:
                circ_units[(circ, era)] = cs.unit("fr.circonscription", circ, on=MAPS[3][0])
                circ_areas[(circ, era)] = cs.area("fr.circonscription", circ, on=MAPS[3][0])
                if circ_units[(circ, era)] in cs.minted or circ_areas[(circ, era)] in cs.minted:
                    raise ValueError(f"constituency {circ} is not in the database")
                continue
            start = CORSICA_SPLIT if era == 1 and dept in ("2A", "2B") else seen
            end = CORSICA_SPLIT if era == 1 and dept == "20" else MAPS[era][1]
            area = circ_areas[(circ, era)] = cs.area("fr.circonscription", circ, on=start)
            unit = circ_units[(circ, era)] = cs.unit("fr.circonscription", circ, on=start)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code="fr.circonscription")
                cs.add("area_code", area_id=area, code=circ, valid_from=start, valid_to=end)
                cs.add(
                    "area_parent",
                    area_id=area,
                    parent_id=nation if dept == "20" or (era == 0 and dept in ("75", "78")) else departements[dept],
                    valid_from=start,
                    valid_to=end,
                )
                name(area, f"{label_of(dept, start)}, {ordinal(int(circ[-2:]))}")
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=france,
                    scheme_code="fr.circonscription",
                    code=circ,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=start,
                    valid_to=end,
                )
                name(unit, f"{label_of(dept, start)}, {ordinal(int(circ[-2:]))}")

        parties: dict[str, UUID] = {}

        def party_of(scheme: str, value: str, label: str, abbreviation: str | None, kind: str) -> UUID:
            key = f"{scheme}:{value}"
            if key not in parties:
                parties[key] = cs.key(scheme, value)
                if parties[key] in cs.minted:
                    cs.add("party", id=parties[key], kind=kind, polity_id=france)
                    name(parties[key], label)
                    if abbreviation:
                        name(parties[key], abbreviation, "fr", "abbreviation")
            return parties[key]

        def presidential_party(abbreviation: str) -> UUID | None:
            if abbreviation in PARTIES:
                return existing(*PARTIES[abbreviation])
            if abbreviation in NEW_PARTIES:
                return party_of(
                    "fr.party", slug(NEW_PARTIES[abbreviation]), NEW_PARTIES[abbreviation], abbreviation, "party"
                )
            return None

        persons: dict[str, UUID] = {}

        def person_of(key: str, display: str, sex: str | None) -> UUID:
            if key not in persons:
                persons[key] = cs.key(f"{SOURCE}.person", key)
                if persons[key] in cs.minted:
                    cs.add("person", id=persons[key], gender={"F": "female", "M": "male"}.get(sex or ""))
                    name(persons[key], display)
            return persons[key]

        def proper(text: str) -> str:
            words = text.title().split(" ")
            return (
                " ".join(
                    word.lower()
                    if index and word.lower() in ("de", "du", "des", "d'", "le", "la") and index < len(words) - 1
                    else word
                    for index, word in enumerate(words)
                ).replace("D'", "d'")
                if text.isupper()
                else text
            )

        def value_row(result_set: UUID, unit: UUID, option: UUID | None, measure: int, value: int) -> None:
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=value,
            )

        for kind, schedule in (("presi", PRESIDENTIAL), ("legi", LEGISLATIVE)):
            for year, days in schedule.items():
                noun = "presidential" if kind == "presi" else "legislative"
                cycle = cs.key("fr.election", f"{days[0]}:{noun}")
                cs.add(
                    "election",
                    id=cycle,
                    polity_id=france,
                    kind="general",
                    date_from=days[0],
                    date_to=days[1],
                    status="held",
                )
                name(cycle, f"{year} French {noun} election", "en")
                name(
                    cycle,
                    f"Élection présidentielle de {year}" if kind == "presi" else f"Élections législatives de {year}",
                    "fr",
                    "native",
                )
                cs.add("slug", path=f"fr/{days[0]}-{noun}", entity_id=cycle)
                if kind == "legi" and year < 1978:
                    cs.add("note", entity_id=cycle, kind="context", body_md=THRESHOLDS[1958 if year < 1967 else 1967])
                previous: dict[str, UUID] = {}
                for number, day in enumerate(days, start=1):
                    stage = "general" if number == 1 else "runoff"
                    ordinal_word = "first" if number == 1 else "second"
                    election = cs.key("fr.election", str(day))
                    cs.add(
                        "election",
                        id=election,
                        polity_id=france,
                        parent_id=cycle,
                        kind=stage,
                        date_from=day,
                        date_to=day,
                        status="held",
                    )
                    name(election, f"{year} French {noun} election, {ordinal_word} round", "en")
                    cs.add("slug", path=f"fr/{day}-{stage}", entity_id=election)
                    held = options.filter(
                        (pl.col("kind") == kind) & (pl.col("year") == year) & (pl.col("round") == number)
                    )
                    counted = values.filter(
                        (pl.col("kind") == kind) & (pl.col("year") == year) & (pl.col("round") == number)
                    )
                    if kind == "presi":
                        contest_key = f"{day}:president"
                        contest = cs.key("fr.contest", contest_key)
                        cs.add(
                            "contest",
                            id=contest,
                            election_id=election,
                            kind="office",
                            office_id=office,
                            district_area_id=nation,
                            magnitude=1,
                            round_number=number,
                            previous_round_id=previous.get("president"),
                            electoral_system_id=presidential,
                            default_result_kind="counted",
                        )
                        previous["president"] = contest
                        name(contest, f"{year} French presidential election, {ordinal_word} round", "en")
                        cs.add("slug", path=f"fr/{day}-{stage}/president", entity_id=contest)
                        result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                        partial = year <= 2002
                        cs.add(
                            "result_set",
                            id=result_set,
                            election_id=election,
                            contest_id=contest,
                            stage="official",
                            kind="counted",
                            status="active",
                            source_id=sources[kind],
                            is_default=True,
                            completeness=None if partial else 1,
                        )
                        fresh = result_set in cs.minted
                        if fresh and partial:
                            cs.add(
                                "note",
                                entity_id=result_set,
                                kind="caveat",
                                body_md=METROPOLITAN_CAVEAT if year < 2002 else NO_ABROAD_CAVEAT,
                            )
                        if fresh:
                            for dept, measure, value in counted.select("dept", "measure", "value").iter_rows():
                                value_row(result_set, department_unit(dept, day), None, measure, value)
                        for label, abbreviation, outcome in (
                            held.group_by("label", maintain_order=True)
                            .agg(pl.col("abbreviation").first(), pl.col("outcome").first())
                            .iter_rows()
                        ):
                            party = presidential_party(abbreviation)
                            option_key = f"{contest_key}:{slug(label)}"
                            option = cs.key("fr.option", option_key)
                            cs.add(
                                "contest_option",
                                id=option,
                                contest_id=contest,
                                kind="candidate",
                                party_id=party,
                                ballot_label=label,
                            )
                            cs.add(
                                "candidacy",
                                id=cs.key("fr.candidacy", option_key),
                                person_id=person_of(f"president:{slug(label)}", label, None),
                                option_id=option,
                                role="head",
                                ballot_name=label,
                                member_of_party_id=party,
                                is_independent=party is None,
                            )
                            if outcome is not None:
                                cs.add("outcome", result_set_id=result_set, option_id=option, kind=outcome)
                            if fresh:
                                for dept, votes in (
                                    held.filter(pl.col("label") == label).select("dept", "votes").iter_rows()
                                ):
                                    value_row(result_set, department_unit(dept, day), option, VOTES, votes)
                        continue
                    era = next(
                        i for i, (start, end) in enumerate(MAPS) if start <= days[0] and (end is None or days[0] < end)
                    )
                    grouped = {circ: frame for (circ,), frame in counted.group_by("circ")}
                    for (circ,), lines in sorted(held.group_by("circ"), key=lambda group: group[0]):
                        dept = lines["dept"][0]
                        district = label_of(dept, day)
                        ordinal_number = int(circ[-2:])
                        contest_key = f"{day}:{circ}"
                        contest = cs.key("fr.contest", contest_key)
                        cs.add(
                            "contest",
                            id=contest,
                            election_id=election,
                            kind="body_seats",
                            body_id=assembly,
                            district_area_id=circ_areas[(circ, era)],
                            magnitude=1,
                            round_number=number,
                            previous_round_id=previous.get(circ),
                            electoral_system_id=legislative,
                            default_result_kind="counted",
                        )
                        previous[circ] = contest
                        name(contest, f"{district}, constituency {ordinal_number}, {ordinal_word} round", "en")
                        cs.add("slug", path=f"fr/{day}-{stage}/{slug(district)}-{ordinal_number}", entity_id=contest)
                        result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                        cs.add(
                            "result_set",
                            id=result_set,
                            election_id=election,
                            contest_id=contest,
                            stage="official",
                            kind="counted",
                            status="active",
                            source_id=sources[kind],
                            is_default=True,
                            completeness=1,
                        )
                        fresh = result_set in cs.minted
                        unit = circ_units[(circ, era)]
                        tendencies = year < 1988
                        if fresh and tendencies:
                            cs.add("note", entity_id=result_set, kind="caveat", body_md=TENDENCY_CAVEAT)
                        if fresh and number == 1 and (year, circ) in unsettled:
                            cs.add("note", entity_id=result_set, kind="caveat", body_md=UNSETTLED_CAVEAT)
                        if fresh and circ in grouped:
                            for measure, value in grouped[circ].select("measure", "value").iter_rows():
                                value_row(result_set, unit, None, measure, value)
                        for line in lines.sort("slot").iter_rows(named=True):
                            option_key = f"{contest_key}:{line['slot']}"
                            option = cs.key("fr.option", option_key)
                            if tendencies:
                                party = party_of(
                                    f"{SOURCE}.tendency",
                                    f"{year}:{slug(line['label'])}",
                                    line["label"],
                                    line["abbreviation"],
                                    "nuance",
                                )
                                cs.add(
                                    "contest_option",
                                    id=option,
                                    contest_id=contest,
                                    kind="candidate",
                                    party_id=party,
                                    ballot_label=line["label"],
                                )
                            else:
                                if line["nuance"] is not None and line["nuance"] not in NUANCES:
                                    raise ValueError(f"unknown nuance {line['nuance']} in {year}")
                                party = (
                                    None
                                    if line["nuance"] is None
                                    else party_of(
                                        "fr.interieur.nuance",
                                        f"{year}:{line['nuance']}",
                                        NUANCES[line["nuance"]],
                                        line["nuance"],
                                        "nuance",
                                    )
                                )
                                ballot = f"{line['surname']} {line['forename'] or ''}".strip()
                                cs.add(
                                    "contest_option",
                                    id=option,
                                    contest_id=contest,
                                    kind="candidate",
                                    party_id=party,
                                    ballot_label=ballot,
                                )
                                person = person_of(
                                    f"{dept}:{fold(line['surname'])}:{fold(line['forename'] or '')}",
                                    proper(f"{line['forename'] or ''} {line['surname']}".strip()),
                                    line["sex"],
                                )
                                cs.add(
                                    "candidacy",
                                    id=cs.key("fr.candidacy", option_key),
                                    person_id=person,
                                    option_id=option,
                                    role="head",
                                    ballot_name=ballot,
                                    is_independent=False,
                                )
                            if line["outcome"] is not None:
                                cs.add("outcome", result_set_id=result_set, option_id=option, kind=line["outcome"])
                            if fresh:
                                value_row(result_set, unit, option, VOTES, line["votes"])
        return cs.write()
