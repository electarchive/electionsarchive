import csv
import io
import json
import re
import unicodedata
import zipfile
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "it.interno"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
OPENDATA = "https://elezionistorico.interno.gov.it/daithome/documenti/opendata"
CAMERA = f"{OPENDATA}/camera/camera-20220925.zip"
REFERENDUM = f"{OPENDATA}/referendum/referendum-20250608.zip"
ISTAT = "https://www.istat.it/storage/codici-unita-amministrative/Elenco-comuni-italiani.csv"
QUERY = """
PREFIX ocd: <http://dati.camera.it/ocd/>
PREFIX dc: <http://purl.org/dc/elements/1.1/>
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
SELECT DISTINCT ?d ?cognome ?nome ?collegio ?inizio ?fine ?tipo ?nota ?lista WHERE {
  ?d a ocd:deputato ; ocd:rif_leg <http://dati.camera.it/ocd/legislatura.rdf/repubblica_19> ;
     foaf:surname ?cognome ; foaf:firstName ?nome ; ocd:rif_mandatoCamera ?m .
  ?m ocd:rif_leg <http://dati.camera.it/ocd/legislatura.rdf/repubblica_19> ; ocd:rif_elezione ?e .
  OPTIONAL { ?e dc:coverage ?collegio } OPTIONAL { ?e ocd:tipoElezione ?tipo } OPTIONAL { ?e ocd:lista ?lista }
  OPTIONAL { ?m ocd:startDate ?inizio } OPTIONAL { ?m ocd:endDate ?fine } OPTIONAL { ?m ocd:motivoTermine ?nota }
}
"""
MANDATES = "https://dati.camera.it/sparql?" + urlencode({"query": QUERY, "format": "application/sparql-results+json"})
GENERAL = date(2022, 9, 25)
VOTING = (date(2025, 6, 8), date(2025, 6, 9))
FOUNDING_MEMBER = date(1958, 1, 1)
FIRST_SITTING = date(2022, 10, 13)
AOSTA = "VALLE D'AOSTA - U01"
REGISTERED, VALID, INVALID, BLANK, VOTES, VOTERS = 2, 4, 5, 6, 1, 14
DERIVED = 1
ACCENTS = {"A": "À", "E": "È", "I": "Ì", "O": "Ò", "U": "Ù"}
ENDED_VIA = {
    "Dimissioni per incompatibilita'": "resigned",
    "Dimissioni": "resigned",
    "Annullamento Elezione": "annulled",
    "Decesso": "died",
}
PARTIES = {
    "FRATELLI D'ITALIA CON GIORGIA MELONI": ("Fratelli d'Italia", "FdI", "party"),
    "PARTITO DEMOCRATICO - ITALIA DEMOCRATICA E PROGRESSISTA": (
        "Partito Democratico – Italia Democratica e Progressista",
        "PD–IDP",
        "list_vehicle",
    ),
    "MOVIMENTO 5 STELLE": ("Movimento 5 Stelle", "M5S", "party"),
    "LEGA PER SALVINI PREMIER": ("Lega per Salvini Premier", "Lega", "party"),
    "FORZA ITALIA": ("Forza Italia", "FI", "party"),
    "AZIONE - ITALIA VIVA - CALENDA": ("Azione – Italia Viva", "A–IV", "list_vehicle"),
    "ALLEANZA VERDI E SINISTRA": ("Alleanza Verdi e Sinistra", "AVS", "list_vehicle"),
    "+EUROPA": ("+Europa", None, "party"),
    "ITALEXIT PER L'ITALIA": ("Italexit per l'Italia", None, "party"),
    "UNIONE POPOLARE CON DE MAGISTRIS": ("Unione Popolare", "UP", "list_vehicle"),
    "ITALIA SOVRANA E POPOLARE": ("Italia Sovrana e Popolare", "ISP", "list_vehicle"),
    "NOI MODERATI/LUPI - TOTI - BRUGNARO - UDC": ("Noi Moderati", "NM", "list_vehicle"),
    "SUD CHIAMA NORD": ("Sud chiama Nord", None, "party"),
    "VITA": ("Vita", None, "list_vehicle"),
    "IMPEGNO CIVICO LUIGI DI MAIO - CENTRO DEMOCRATICO": ("Impegno Civico", "IC", "list_vehicle"),
    "SUDTIROLER VOLKSPARTEI (SVP) - PATT": ("Südtiroler Volkspartei – PATT", "SVP–PATT", "list_vehicle"),
    "MASTELLA NOI DI CENTRO EUROPEISTI": ("Noi di Centro – Europeisti", None, "list_vehicle"),
    "PARTITO COMUNISTA ITALIANO": ("Partito Comunista Italiano", "PCI", "party"),
    "PARTITO ANIMALISTA - UCDL - 10 VOLTE MEGLIO": (
        "Partito Animalista – UCDL – 10 Volte Meglio",
        None,
        "list_vehicle",
    ),
    "ALTERNATIVA PER L'ITALIA - NO GREEN PASS": ("Alternativa per l'Italia", None, "party"),
    "PARTITO DELLA FOLLIA CREATIVA": ("Partito della Follia Creativa", None, "party"),
    "FORZA DEL POPOLO": ("Forza del Popolo", None, "party"),
    "FREE": ("Free", None, "party"),
}
COALITIONS = {
    "FRATELLI D'ITALIA CON GIORGIA MELONI": ("Center-right coalition", "Coalizione di centrodestra"),
    "PARTITO DEMOCRATICO - ITALIA DEMOCRATICA E PROGRESSISTA": (
        "Center-left coalition",
        "Coalizione di centrosinistra",
    ),
}
QUESTIONS = {
    1: (
        "Dismissals under the Jobs Act: repeal",
        "Contratto di lavoro a tutele crescenti – Disciplina dei licenziamenti illegittimi: abrogazione",
    ),
    2: (
        "Small businesses, dismissals, and severance: partial repeal",
        "Piccole imprese – Licenziamenti e relativa indennità: abrogazione parziale",
    ),
    3: (
        "Fixed-term contracts: partial repeal",
        (
            "Abrogazione parziale di norme in materia di apposizione di termine al contratto di lavoro subordinato, "
            "durata massima e condizioni per proroghe e rinnovi"
        ),
    ),
    4: (
        "Contractor liability for workplace accidents: repeal",
        (
            "Esclusione della responsabilità solidale di committente, appaltatore e subappaltatore per infortuni del "
            "dipendente d'impresa appaltatrice o subappaltatrice, come conseguenza dei rischi specifici propri "
            "dell'attività delle imprese predette: abrogazione"
        ),
    ),
    5: (
        "Citizenship: residence requirement halved from 10 to 5 years",
        (
            "Cittadinanza italiana: dimezzamento da 10 a 5 anni dei tempi di residenza legale in Italia dello straniero "
            "maggiorenne extracomunitario per la richiesta di concessione della cittadinanza italiana"
        ),
    ),
}
INTERNO_LICENSE = (
    "No license stated. Data that Italian public bodies publish without a license are open by default (art. 52 of "
    "the Codice dell'amministrazione digitale)."
)
INTERNO_NOTE = (
    "This is the Interior Ministry's provisional count, published in its Eligendo historical archive. The Chamber "
    "file has one row per municipality, single-member college, and list. It has no ISTAT codes, no invalid ballots, "
    "and no winners, and electors and voters by sex are not stored. The referendum files give electors, voters, "
    "blank ballots, valid votes, and Yes votes. No votes and invalid ballots follow by arithmetic."
)
ISTAT_NOTE = (
    "Current municipalities only. The 19 municipalities merged or renamed since the 2022 election keep the "
    "ministry's names and have no ISTAT code."
)
CAMERA_NOTE = (
    "Mandates of the 19th legislature, rewritten to each member's current state. A mandate's type and list may "
    "reflect a later change, but start and end dates and annulments are kept."
)
METHODOLOGY = (
    "Italy elects its 400 deputies under the Rosatellum (Law 165/2017). Each voter casts one ballot: a mark on a list "
    "also counts for the coalition's single-member candidate, and a mark on the candidate alone is shared among the "
    "coalition's lists in proportion to their votes in the college. 147 seats are filled by plurality in "
    "single-member colleges, 245 by a national largest-remainder (Hare) allocation among coalitions and lists in "
    "Italy outside Valle d'Aosta, and 8 abroad. A list needs 3 percent of the national valid vote and a coalition 10 "
    "percent. A coalition list's votes count toward the coalition only if the list has at least 1 percent. "
    "Linguistic-minority lists qualify with 20 percent in their region or two single-member wins."
)
ELECTION_NOTE = (
    "Vote counts are the Interior Ministry's provisional count. Outcomes come from the proclamation as recorded in "
    "the Chamber's mandates. The eight seats elected abroad and the Senate election held the same day are not yet in "
    "the archive."
)
LEGEND_NOTE = (
    "Votes marked only on a single-member candidate are stored on a Candidate only option for each coalition in "
    "every multi-member college. Their split among the coalition's lists is engine output, not data."
)
EXHAUSTED_NOTE = (
    "The list ran out of candidates in this college, so the seat went to its best-placed losing single-member "
    "candidate in the region, from {college} (art. 84 of Presidential Decree 361/1957)."
)
REVISED_SINGLE = "On {day}, after a recount, the Chamber seated {name} in place of the proclaimed winner."
REVISED_LIST = (
    "On {day}, the displaced single-member winner, {displaced}, took this list seat, and the election of "
    "{annulled}, who had been seated from the list's losing single-member candidates, was annulled."
)
REFERENDUM_OUTCOME = (
    "Turnout was {turnout} of registered voters, short of the quorum of more than half. Yes won {yes} of valid votes, "
    "so the law stays in force."
)
TURNOUT_NOTE = (
    "Some voters cast their ballots away from where they are registered, including hospital patients, polling staff, "
    "police on duty, and voters temporarily abroad. As a result, a polling section or a country abroad can show more "
    "voters than electors. Hospital sections have no electors of their own."
)
REFERENDUM_METHODOLOGY = (
    "An abrogative referendum repeals a law, in whole or in part, only if more than half of all registered voters, "
    "including those abroad, take part and a majority of valid votes is Yes (art. 75 of the Constitution)."
)


def key(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.replace("ß", "ss")).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]", "", folded)


def proper(text: str) -> str:
    return re.sub(r"([AEIOU])'(?=\s|$)", lambda match: ACCENTS[match[1]], text.strip()).title()


def born(text: str) -> date:
    return datetime.strptime(text.split()[0], "%d/%m/%Y").date()


def fetch() -> None:
    raw.fetch(SOURCE, CAMERA, suffix=".zip")
    raw.fetch(SOURCE, REFERENDUM, suffix=".zip")
    raw.fetch(SOURCE, ISTAT, suffix=".csv")
    raw.fetch(SOURCE, MANDATES, suffix=".json")


def parse() -> None:
    with raw.cached(SOURCE, ISTAT).path.open(encoding="latin-1", newline="") as file:
        istat = list(csv.reader(file, delimiter=";"))[1:]
    regions: dict[str, str] = {}
    codes: dict[tuple[str, str], str] = {}
    official: dict[str, str] = {}
    for row in istat:
        regions.setdefault(row[0], row[10])
        official[row[4]] = row[5]
        for label in {row[5], row[6], row[7]} - {""}:
            codes[row[0], key(label)] = row[4]

    def region_of(label: str) -> str:
        found = [code for code, text in regions.items() if key(text).startswith(key(re.sub(r" \d$", "", label)))]
        if len(found) != 1:
            raise ValueError(f"region {label!r} matches {found}")
        return found[0]

    comuni: dict[str, tuple[str, str, bool]] = {}

    def comune(region: str, label: str) -> str:
        code = codes.get((region, key(label))) or codes.get(
            (region, key(label).replace("J", "I")), f"{region}:{key(label)}"
        )
        comuni.setdefault(code, (region, official.get(code, proper(label)), code in official))
        return code

    archive = zipfile.ZipFile(raw.cached(SOURCE, CAMERA).path)

    def table(member: str, columns: list[str]) -> pl.DataFrame:
        return pl.read_csv(io.BytesIO(archive.read(member)), separator=";", infer_schema=False, columns=columns)

    rows = table(
        "camera2022_Italia_LivComune.csv",
        [
            "CIRC-REG",
            "COLLPLURI",
            "COLLUNINOM",
            "COMUNE",
            "ELETTORITOT",
            "VOTANTITOT",
            "SKBIANCHE",
            "VOTILISTA",
            "DESCRLISTA",
            "COGNOME",
            "NOME",
            "DATANASCITA",
            "SESSO",
            "VOTICANDIDATO",
        ],
    )
    aosta = region_of("VALLE D'AOSTA")
    circ_regions = {label: region_of(label) for label in rows["CIRC-REG"].unique()}
    rows = rows.with_columns(
        pl.col("ELETTORITOT", "VOTANTITOT", "SKBIANCHE", "VOTILISTA", "VOTICANDIDATO").cast(pl.Int64),
        region=pl.col("CIRC-REG").replace_strict(circ_regions),
        college=pl.col("COLLUNINOM").str.split(" (").list.first(),
    )
    located = {
        f"{region}\x1f{label}": comune(region, label)
        for region, label in rows.select("region", "COMUNE").unique().sort("region", "COMUNE").iter_rows()
    }
    rows = rows.with_columns(
        comune=pl.concat_str("region", pl.lit("\x1f"), "COMUNE").replace_strict(located),
    ).with_columns(unit=pl.concat_str("college", pl.lit("/"), "comune"))
    aosta_votes = (
        table("camera2022_VAosta_LivComune.csv", ["COMUNE", "COGNOME", "NOME", "DATANASCITA", "SESSO", "TOTVOTI"])
        .with_columns(pl.col("TOTVOTI").cast(pl.Int64), college=pl.lit(AOSTA))
        .with_columns(comune=pl.col("COMUNE").map_elements(lambda label: comune(aosta, label), pl.String))
        .with_columns(unit=pl.concat_str("college", pl.lit("/"), "comune"))
    )
    aosta_accounting = (
        table(
            "camera2022_VAosta_LivComune_Scrutini.csv",
            ["DENOMINAZIONE", "ELETTORITOTALI", "VOTANTITOTALI", "SKBIANCHE"],
        )
        .with_columns(pl.col("ELETTORITOTALI", "VOTANTITOTALI", "SKBIANCHE").cast(pl.Int64), college=pl.lit(AOSTA))
        .with_columns(comune=pl.col("DENOMINAZIONE").map_elements(lambda label: comune(aosta, label), pl.String))
        .with_columns(unit=pl.concat_str("college", pl.lit("/"), "comune"))
    )
    if set(aosta_votes["comune"]) != set(aosta_accounting["comune"]):
        raise ValueError("Valle d'Aosta's vote and accounting files list different municipalities")

    circs = {
        label: regions[region].split("/")[0] + label.removeprefix(re.sub(r" \d$", "", label))
        for label, region in circ_regions.items()
    }
    circs["VALLE D'AOSTA"] = regions[aosta].split("/")[0]
    plurinominal_of = dict(rows.select("college", "COLLPLURI").unique().iter_rows())
    if len(plurinominal_of) != rows["college"].n_unique():
        raise ValueError("a single-member college spans two multi-member colleges")
    singles: list[tuple[str, str, str, str]] = []
    for value in sorted(rows["COLLUNINOM"].unique()):
        code, label = value.removesuffix(")").split(" (", 1)
        circ, number = code.rsplit(" - ", 1)
        town = official.get(codes.get((circ_regions[circ], key(label)), ""), proper(label))
        singles.append((code, "it.uninominale_camera", f"{circs[circ]} - {number} ({town})", plurinominal_of[code]))
    colleges = [
        *[(code, "it.circoscrizione_camera", label, None) for code, label in sorted(circs.items())],
        *[
            (
                code,
                "it.plurinominale_camera",
                f"{circs[code.rsplit(' - ', 1)[0]]} - {code.rsplit(' - ', 1)[1]}",
                code.rsplit(" - ", 1)[0],
            )
            for code in sorted(set(plurinominal_of.values()))
        ],
        *singles,
        (AOSTA, "it.uninominale_camera", f"{circs["VALLE D'AOSTA"]} - U01 (Aosta)", "VALLE D'AOSTA"),
    ]

    units = pl.concat(
        [rows.select("college", "comune", "unit").unique(), aosta_accounting.select("college", "comune", "unit")]
    )
    spread = units.group_by("comune").agg(pl.len().alias("colleges"))
    units = units.join(spread, on="comune").sort("unit")
    accounting = rows.select("unit", "ELETTORITOT", "VOTANTITOT", "SKBIANCHE").unique()
    if accounting["unit"].n_unique() != accounting.height:
        raise ValueError("a municipality's accounting differs between rows")
    accounting = (
        pl.concat(
            [
                accounting,
                aosta_accounting.select(
                    "unit", ELETTORITOT="ELETTORITOTALI", VOTANTITOT="VOTANTITOTALI", SKBIANCHE="SKBIANCHE"
                ),
            ]
        )
        .unpivot(index="unit", variable_name="field")
        .with_columns(
            measure=pl.col("field").replace_strict(
                {"ELETTORITOT": REGISTERED, "VOTANTITOT": VOTERS, "SKBIANCHE": BLANK}, return_dtype=pl.Int16
            )
        )
    )

    candidates = pl.concat(
        [
            rows.select("college", "COGNOME", "NOME", "DATANASCITA", "SESSO").unique(),
            aosta_votes.select("college", "COGNOME", "NOME", "DATANASCITA", "SESSO").unique(),
        ]
    ).with_columns(birth=pl.col("DATANASCITA").map_elements(born, pl.Date))
    if candidates.select("college", "COGNOME", "NOME").n_unique() != candidates.height:
        raise ValueError("two single-member candidates in one college share a name")
    candidate_votes = pl.concat(
        [
            rows.select("college", "unit", "COGNOME", "NOME", votes="VOTICANDIDATO").unique(),
            aosta_votes.select("college", "unit", "COGNOME", "NOME", votes="TOTVOTI"),
        ]
    )
    if candidate_votes.select("unit", "COGNOME", "NOME").n_unique() != candidate_votes.height:
        raise ValueError("a candidate's votes differ between rows of one municipality")

    lists_of = {
        (college, surname, forename): labels
        for college, surname, forename, labels in rows.group_by("college", "COGNOME", "NOME", maintain_order=True)
        .agg(pl.col("DESCRLISTA").unique(maintain_order=True))
        .iter_rows()
    }
    component: dict[str, str] = {}

    def root(label: str) -> str:
        while component.setdefault(label, label) != label:
            label = component[label]
        return label

    for labels in lists_of.values():
        first = root(labels[0])
        for other in labels[1:]:
            component[root(other)] = first
    groups = {label: min(member for member in component if root(member) == root(label)) for label in component}
    candidates = candidates.with_columns(
        group=pl.struct("college", "COGNOME", "NOME").map_elements(
            lambda row: (
                groups[lists_of[row["college"], row["COGNOME"], row["NOME"]][0]]
                if (row["college"], row["COGNOME"], row["NOME"]) in lists_of
                else None
            ),
            pl.String,
        )
    )

    list_votes = rows.select(college="COLLPLURI", unit="unit", option="DESCRLISTA", votes="VOTILISTA")
    shown = rows.group_by("unit", "COGNOME", "NOME").agg(pl.col("VOTILISTA").sum().alias("listed"))
    legend_votes = (
        candidate_votes.join(shown, on=["unit", "COGNOME", "NOME"])
        .join(candidates.select("college", "COGNOME", "NOME", "group"), on=["college", "COGNOME", "NOME"])
        .select(
            college=pl.col("college").replace_strict(plurinominal_of),
            unit="unit",
            option=pl.lit("legend:") + pl.col("group"),
            votes=pl.col("votes") - pl.col("listed"),
        )
    )
    negative = legend_votes.filter(pl.col("votes") < 0)
    print("negative candidate-only votes:", negative.height, negative.head(5).rows())

    members = (
        table(
            "camera2022_candidatilista.csv",
            ["CollPlurinom", "descrlista", "cognome", "nome", "datanascita", "sesso", "CODTIPOELETTO"],
        )
        .with_columns(
            birth=pl.col("datanascita").map_elements(born, pl.Date),
            position=pl.int_range(pl.len()).over("CollPlurinom", "descrlista") + 1,
            elected=pl.col("CODTIPOELETTO") == "E",
        )
        .select(
            college="CollPlurinom",
            label="descrlista",
            position="position",
            surname="cognome",
            forename="nome",
            birth="birth",
            sex="sesso",
            elected="elected",
        )
    )
    mandates = [
        {field: value["value"] for field, value in binding.items()}
        for binding in json.loads(raw.cached(SOURCE, MANDATES).path.read_text())["results"]["bindings"]
    ]

    def who(surname: str, forename: str) -> tuple[str, str]:
        return key(surname), key(forename.split()[0])

    sitting: dict[tuple[str, str], dict[str, str]] = {}
    for mandate in mandates:
        if mandate["inizio"] <= FIRST_SITTING.strftime("%Y%m%d"):
            if who(mandate["cognome"], mandate["nome"]) in sitting:
                raise ValueError(f"two deputies seated in 2022 are both {mandate['cognome']} {mandate['nome']}")
            sitting[who(mandate["cognome"], mandate["nome"])] = mandate

    totals = candidate_votes.group_by("college", "COGNOME", "NOME").agg(pl.col("votes").sum())
    ranked = totals.sort("votes", descending=True).with_columns(rank=pl.int_range(pl.len()).over("college"))
    tied = ranked.filter(pl.col("rank") < 2).group_by("college").agg(pl.col("votes").n_unique() == 1)
    if tied["votes"].any():
        raise ValueError(f"tied single-member colleges: {tied.filter('votes')['college'].to_list()}")
    winners = {
        college: (surname, forename)
        for college, surname, forename, *_ in ranked.filter(pl.col("rank") == 0).iter_rows()
    }
    unseated = [(college, *winner) for college, winner in winners.items() if who(*winner) not in sitting]
    if unseated:
        raise ValueError(f"preliminary plurality winners without a 2022 mandate: {unseated}")
    seated = {who(*winner) for winner in winners.values()} | {
        who(surname, forename)
        for surname, forename in members.filter("elected").select("surname", "forename").iter_rows()
    }
    births = {
        (college, surname, forename): (birth, sex)
        for college, surname, forename, _, sex, birth, _ in candidates.iter_rows()
    }
    exhausted = [
        {
            "college": sitting[who(surname, forename)]["collegio"],
            "label": labels[0],
            "position": None,
            "surname": surname,
            "forename": forename,
            "birth": births[college, surname, forename][0],
            "sex": births[college, surname, forename][1],
            "elected": True,
            "single": college,
        }
        for (college, surname, forename), labels in lists_of.items()
        if who(surname, forename) in sitting
        and who(surname, forename) not in seated
        and sitting[who(surname, forename)]["collegio"] in set(plurinominal_of.values())
    ]
    members = pl.concat(
        [
            members.with_columns(single=pl.lit(None, pl.String)),
            pl.DataFrame(exhausted, schema=members.schema | {"single": pl.String}),
        ]
    )

    annulments: list[dict[str, object]] = []
    for annulled in (mandate for mandate in mandates if mandate.get("nota") == "Annullamento Elezione"):
        replacement = next(
            mandate
            for mandate in mandates
            if mandate.get("tipo") == "maggioritario" and mandate["inizio"] == annulled["fine"]
        )
        college, surname, forename, birth = next(
            row
            for row in candidates.select("college", "COGNOME", "NOME", "birth").iter_rows()
            if who(row[1], row[2]) == who(replacement["cognome"], replacement["nome"])
        )
        displaced = winners[college]
        listed = {
            who(surname, forename): f"{key(surname)}:{key(forename)}:{birth}"
            for surname, forename, birth in members.filter(pl.col("college") == annulled["collegio"])
            .select("surname", "forename", "birth")
            .iter_rows()
        }
        if who(*displaced) not in listed:
            raise ValueError(f"{displaced} is not on a list in {annulled['collegio']}")
        annulments.append(
            {
                "day": datetime.strptime(annulled["fine"], "%Y%m%d").date(),
                "single": college,
                "list": annulled["collegio"],
                "replacement": f"{key(surname)}:{key(forename)}:{birth}",
                "displaced": listed[who(*displaced)],
                "annulled": listed[who(annulled["cognome"], annulled["nome"])],
            }
        )
    print(
        "list seats passed to a losing single-member candidate (art. 84):",
        [(row["surname"], row["college"]) for row in exhausted],
    )

    persons: dict[str, tuple[str, str, date, str]] = {}
    people: dict[tuple[str, str], set[str]] = {}
    for surname, forename, birth, sex in [
        *candidates.select("COGNOME", "NOME", "birth", "SESSO").iter_rows(),
        *members.select("surname", "forename", "birth", "sex").iter_rows(),
    ]:
        person = f"{key(surname)}:{key(forename)}:{birth}"
        persons.setdefault(person, (proper(surname), proper(forename), birth, sex))
        people.setdefault(who(surname, forename), set()).add(person)
    elected_in: dict[str, str] = {}
    for college, (surname, forename) in winners.items():
        elected_in[f"{key(surname)}:{key(forename)}:{births[college, surname, forename][0]}"] = college
    for college, surname, forename, birth in (
        members.filter("elected").select("college", "surname", "forename", "birth").iter_rows()
    ):
        elected_in[f"{key(surname)}:{key(forename)}:{birth}"] = college
    for annulment in annulments:
        elected_in[str(annulment["replacement"])] = str(annulment["single"])
    stood: dict[str, set[str]] = {}
    for college, surname, forename, birth in members.select("college", "surname", "forename", "birth").iter_rows():
        stood.setdefault(f"{key(surname)}:{key(forename)}:{birth}", set()).add(college)
    for college, surname, forename, birth in candidates.select("college", "COGNOME", "NOME", "birth").iter_rows():
        stood.setdefault(f"{key(surname)}:{key(forename)}:{birth}", set()).add(plurinominal_of.get(college, college))
    tenures: list[dict[str, object]] = []
    succeeded: set[str] = set()
    for mandate in sorted(mandates, key=lambda mandate: mandate["inizio"]):
        found = people.get(who(mandate["cognome"], mandate["nome"]), set())
        if len(found) > 1:
            found = {person for person in found if person.split(":")[1] == key(mandate["nome"])} or found
        if len(found) > 1:
            found = {person for person in found if mandate.get("collegio") in stood[person]}
        if len(found) != 1 or mandate.get("tipo") == "maggioritario suppletiva":
            continue
        person = next(iter(found))
        replacement = mandate["inizio"] > FIRST_SITTING.strftime("%Y%m%d") and mandate.get("tipo") == "proporzionale"
        predecessor = max(
            (
                other
                for other in mandates
                if replacement
                and (other.get("fine") or "9") <= mandate["inizio"]
                and other.get("collegio") == mandate.get("collegio")
                and other.get("lista") == mandate.get("lista")
                and other["d"] not in succeeded
            ),
            key=lambda other: other["fine"],
            default=None,
        )
        if predecessor is not None:
            succeeded.add(predecessor["d"])
        tenures.append(
            {
                "deputy": mandate["d"].rsplit("/", 1)[1],
                "person": person,
                "start": datetime.strptime(mandate["inizio"], "%Y%m%d").date(),
                "end": datetime.strptime(mandate["fine"], "%Y%m%d").date() if mandate.get("fine") else None,
                "replacement": replacement,
                "college": mandate.get("collegio") if replacement else elected_in.get(person),
                "ended": mandate.get("nota"),
                "predecessor": predecessor["d"].rsplit("/", 1)[1] if predecessor else None,
            }
        )
    print("mandates:", len(mandates), "matched to one candidate:", len(tenures), "persons:", len(persons))

    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(list(regions.items()), schema=["code", "name"], orient="row").write_parquet(PARSED / "regions.parquet")
    pl.DataFrame(colleges, schema=["code", "scheme", "name", "parent"], orient="row").write_parquet(
        PARSED / "colleges.parquet"
    )
    units.write_parquet(PARSED / "units.parquet")
    accounting.select("unit", "measure", "value").write_parquet(PARSED / "accounting.parquet")
    candidates.select(
        "college",
        "group",
        person=pl.concat_str(
            pl.col("COGNOME").map_elements(key, pl.String),
            pl.col("NOME").map_elements(key, pl.String),
            pl.col("birth").cast(pl.String),
            separator=":",
        ),
        surname="COGNOME",
        forename="NOME",
        lists=pl.struct("college", "COGNOME", "NOME").map_elements(
            lambda row: lists_of.get((row["college"], row["COGNOME"], row["NOME"]), []), pl.List(pl.String)
        ),
        won=pl.struct("college", "COGNOME", "NOME").map_elements(
            lambda row: winners[row["college"]] == (row["COGNOME"], row["NOME"]), pl.Boolean
        ),
    ).write_parquet(PARSED / "candidates.parquet")
    pl.concat([list_votes, legend_votes]).write_parquet(PARSED / "list_votes.parquet")
    candidate_votes.rename({"COGNOME": "surname", "NOME": "forename"}).write_parquet(PARSED / "candidate_votes.parquet")
    members.with_columns(
        person=pl.concat_str(
            pl.col("surname").map_elements(key, pl.String),
            pl.col("forename").map_elements(key, pl.String),
            pl.col("birth").cast(pl.String),
            separator=":",
        )
    ).write_parquet(PARSED / "members.parquet")
    pl.DataFrame(
        [(person, *values) for person, values in persons.items()],
        schema=["person", "surname", "forename", "birth", "sex"],
        orient="row",
    ).write_parquet(PARSED / "persons.parquet")
    pl.DataFrame(annulments).write_parquet(PARSED / "annulments.parquet")
    pl.DataFrame(tenures, infer_schema_length=None).write_parquet(PARSED / "tenures.parquet")
    pl.DataFrame(list(groups.items()), schema=["label", "group"], orient="row").write_parquet(PARSED / "lists.parquet")

    referendum = zipfile.ZipFile(raw.cached(SOURCE, REFERENDUM).path)
    fields = ["NUMQUESITO", "ELETTORITOT", "VOTANTITOT", "SKBIANCHE", "VOTIVALIDI", "VOTIVALIDI_SI"]

    def sheet(member: str, columns: list[str]) -> pl.DataFrame:
        return (
            pl.read_excel(io.BytesIO(referendum.read(member)), infer_schema_length=0, columns=fields + columns)
            .with_columns(pl.col(fields).cast(pl.Int64))
            .rename({"NUMQUESITO": "question"})
        )

    def place(frame: pl.DataFrame) -> pl.DataFrame:
        found = {
            f"{label}\x1f{name}": comune(region_of(label), name)
            for label, name in frame.select("REGIONE", "COMUNE").unique().sort("REGIONE", "COMUNE").iter_rows()
        }
        return frame.with_columns(comune=pl.concat_str("REGIONE", pl.lit("\x1f"), "COMUNE").replace_strict(found))

    municipal = place(sheet("OPENDATA_LivComune.xlsx", ["REGIONE", "COMUNE"])).with_columns(
        scheme=pl.lit("it.comune"), unit="comune", channel=pl.lit(0, pl.Int16)
    )
    abroad = sheet("OPENDATA_livEsteroNazione.xlsx", ["NAZIONE"]).with_columns(
        scheme=pl.lit("it.estero"),
        unit=pl.col("NAZIONE").map_elements(key, pl.String),
        comune=pl.lit(None, pl.String),
        channel=pl.lit(4, pl.Int16),
    )
    sections = pl.concat(
        [
            place(sheet(f"OPENDATA_Referendum{question}_livSez.xlsx", ["REGIONE", "COMUNE", "SEZIONE"]))
            .with_columns(number=pl.col("SEZIONE").str.strip_prefix("SEZIONE "))
            .with_columns(repeat=pl.int_range(pl.len()).over("comune", "number"))
            .with_columns(
                scheme=pl.lit("it.sezione"),
                unit=pl.concat_str("comune", pl.lit("/"), "number")
                + pl.when(pl.col("repeat") > 0)
                .then(pl.lit("#") + (pl.col("repeat") + 1).cast(pl.String))
                .otherwise(pl.lit("")),
                channel=pl.lit(0, pl.Int16),
            )
            for question in range(1, 6)
        ],
        how="diagonal_relaxed",
    )
    print(
        "section numbers repeated within a municipality:",
        sections.filter(pl.col("repeat") > 0, pl.col("question") == 1).height,
    )
    columns = ["question", "scheme", "unit", "channel", *fields[1:]]
    votes = (
        pl.concat(
            [
                municipal.select(columns).with_columns(grain=pl.lit("comuni")),
                abroad.select(columns).with_columns(grain=pl.lit("comuni")),
                sections.select(columns).with_columns(grain=pl.lit("sections")),
            ]
        )
        .with_columns(
            INVALID=pl.col("VOTANTITOT") - pl.col("SKBIANCHE") - pl.col("VOTIVALIDI"),
            NO=pl.col("VOTIVALIDI") - pl.col("VOTIVALIDI_SI"),
        )
        .unpivot(index=["question", "grain", "scheme", "unit", "channel"], variable_name="field")
        .with_columns(
            measure=pl.col("field").replace_strict(
                {
                    "ELETTORITOT": REGISTERED,
                    "VOTANTITOT": VOTERS,
                    "SKBIANCHE": BLANK,
                    "VOTIVALIDI": VALID,
                    "INVALID": INVALID,
                    "VOTIVALIDI_SI": VOTES,
                    "NO": VOTES,
                },
                return_dtype=pl.Int16,
            ),
            answer=pl.col("field").replace_strict({"VOTIVALIDI_SI": "yes", "NO": "no"}, default=None),
            flags=pl.when(pl.col("field").is_in(["INVALID", "NO"])).then(DERIVED).otherwise(0).cast(pl.Int16),
        )
    )
    if votes.filter(pl.col("value") < 0).height:
        raise ValueError("a derived referendum count is negative")
    pl.concat(
        [
            municipal.select("scheme", "unit", "comune", label="COMUNE").unique("unit"),
            abroad.select("scheme", "unit", "comune", label="NAZIONE").unique("unit"),
            sections.select("scheme", "unit", "comune", label="SEZIONE").unique("unit"),
        ]
    ).sort("scheme", "unit").write_parquet(PARSED / "referendum_units.parquet")
    votes.drop("field").write_parquet(PARSED / "referendum_votes.parquet")
    pl.DataFrame(
        [(code, region, name, matched) for code, (region, name, matched) in comuni.items()],
        schema=["code", "region", "name", "matched"],
        orient="row",
    ).write_parquet(PARSED / "comuni.parquet")


def emit() -> Path:
    regions = pl.read_parquet(PARSED / "regions.parquet")
    colleges = pl.read_parquet(PARSED / "colleges.parquet")
    units = pl.read_parquet(PARSED / "units.parquet")
    comuni = pl.read_parquet(PARSED / "comuni.parquet")
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    list_votes = pl.read_parquet(PARSED / "list_votes.parquet")
    members = pl.read_parquet(PARSED / "members.parquet")
    persons = pl.read_parquet(PARSED / "persons.parquet")
    tenures = pl.read_parquet(PARSED / "tenures.parquet")
    lists = dict(pl.read_parquet(PARSED / "lists.parquet").iter_rows())
    referendum_units = pl.read_parquet(PARSED / "referendum_units.parquet")
    referendum_votes = pl.read_parquet(PARSED / "referendum_votes.parquet")
    revisions = pl.read_parquet(PARSED / "annulments.parquet").rows(named=True)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "2022 Italian general election, Chamber of Deputies, by municipality, "
                "and the 2025 abrogative referendums by polling section"
            ),
            polity="IT",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        interno = cs.source("Ministero dell'Interno", "Eligendo: archivio storico delle elezioni, open data")
        istat = cs.source("ISTAT", "Elenco dei comuni italiani")
        camera = cs.source("Camera dei deputati", "Mandati dei deputati della XIX legislatura")
        for source, publisher, title, url, license, license_url, notes in (
            (
                interno,
                "Ministero dell'Interno",
                "Eligendo: archivio storico delle elezioni, open data",
                "https://elezionistorico.interno.gov.it/eligendo/opendata.php",
                INTERNO_LICENSE,
                None,
                INTERNO_NOTE,
            ),
            (
                istat,
                "ISTAT",
                "Elenco dei comuni italiani",
                "https://www.istat.it/classificazione/codici-dei-comuni-delle-province-e-delle-regioni/",
                "CC BY 4.0",
                "https://creativecommons.org/licenses/by/4.0/",
                ISTAT_NOTE,
            ),
            (
                camera,
                "Camera dei deputati",
                "Mandati dei deputati della XIX legislatura",
                "https://dati.camera.it/",
                "CC BY 3.0",
                "https://creativecommons.org/licenses/by/3.0/deed.it",
                CAMERA_NOTE,
            ),
        ):
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=publisher,
                title=title,
                url=url,
                license=license,
                license_url=license_url,
                notes=notes,
            )
        cs.artifact(raw.cached(SOURCE, CAMERA), interno)
        cs.artifact(raw.cached(SOURCE, REFERENDUM), interno)
        cs.artifact(raw.cached(SOURCE, ISTAT), istat)
        cs.artifact(raw.cached(SOURCE, MANDATES), camera)

        italy = cs.polity("IT")
        cs.add("polity", id=italy, kind="sovereign_state", iso_code="IT")
        name(italy, "Italy")
        name(italy, "Repubblica Italiana", "it", "native")
        cs.add("slug", path="it", entity_id=italy)
        cs.add("external_id", entity_id=italy, scheme="wikidata", value="Q38")
        union = cs.polity("EU")
        if union in cs.minted:
            raise ValueError("publish eu.eurlex before it.interno")
        cs.add("polity_parent", polity_id=italy, parent_id=union, valid_from=FOUNDING_MEMBER)
        for code, rank, noun, kind in (
            ("it.nation", 0, "country", "administrative"),
            ("it.regione", 1, "region", "administrative"),
            ("it.comune", 2, "municipality", "administrative"),
            ("it.circoscrizione_camera", 1, "constituency", "electoral_district"),
            ("it.plurinominale_camera", 2, "multi-member college", "electoral_district"),
            ("it.uninominale_camera", 3, "single-member college", "electoral_district"),
            ("it.comune_collegio", 4, "municipality part", "electoral_district"),
            ("it.sezione", 5, "polling section", "precinct"),
            ("it.estero", 1, "country abroad", "electoral_district"),
        ):
            cs.add("scheme", code=code, polity_id=italy, kind=kind, rank=rank, noun=noun)
        nation = cs.area("it.nation", "IT")
        cs.add("area", id=nation, scheme_code="it.nation")
        cs.add("area_code", area_id=nation, code="IT", valid_from=None)
        name(nation, "Italy")
        cs.add("polity_territory", polity_id=italy, area_id=nation)
        region_ids: dict[str, UUID] = {}
        for code, label in regions.iter_rows():
            region_ids[code] = cs.area("it.regione", code)
            cs.add("area", id=region_ids[code], scheme_code="it.regione")
            cs.add("area_code", area_id=region_ids[code], code=code, valid_from=None)
            cs.add("area_parent", area_id=region_ids[code], parent_id=nation, valid_from=None)
            name(region_ids[code], label, "it")

        comune_ids = {
            code: str(cs.area("it.comune", code) if matched else cs.key("it.eligendo.comune", code))
            for code, matched in comuni.select("code", "matched").iter_rows()
        }
        comune_names = dict(comuni.select("code", "name").iter_rows())
        comune_area = pl.col("code").replace_strict(comune_ids)
        cs.frame("area", comuni.select(id=comune_area, scheme_code=pl.lit("it.comune")))
        cs.frame(
            "area_code",
            comuni.filter("matched").select(area_id=comune_area, code="code", valid_from=pl.lit(None, pl.Date)),
        )
        cs.frame(
            "area_parent",
            comuni.select(
                area_id=comune_area,
                parent_id=pl.col("region").replace_strict({code: str(id) for code, id in region_ids.items()}),
                valid_from=pl.lit(None, pl.Date),
            ),
        )
        cs.frame(
            "name",
            comuni.select(
                entity_id=comune_area,
                text="name",
                lang=pl.lit("it"),
                kind=pl.lit("official"),
                is_preferred=pl.lit(True),
            ),
        )

        parent_of = dict(colleges.select("code", "parent").iter_rows())
        region_of = dict(comuni.select("code", "region").iter_rows())
        circ_regions: dict[str, set[str]] = {}
        for college, comune in units.select("college", "comune").iter_rows():
            while parent_of[college] is not None:
                college = parent_of[college]
            circ_regions.setdefault(college, set()).add(region_of[comune])
        if any(len(found) != 1 for found in circ_regions.values()):
            raise ValueError(f"a constituency spans regions: {circ_regions}")
        college_ids: dict[str, UUID] = {}
        college_names: dict[str, str] = {}
        for code, scheme, label, parent in colleges.iter_rows():
            college_ids[code] = cs.area(scheme, code)
            college_names[code] = label
            cs.add("area", id=college_ids[code], scheme_code=scheme)
            cs.add("area_code", area_id=college_ids[code], code=code, valid_from=None)
            cs.add(
                "area_parent",
                area_id=college_ids[code],
                parent_id=region_ids[next(iter(circ_regions[code]))] if parent is None else college_ids[parent],
                valid_from=None,
            )
            name(college_ids[code], label, "it")

        unit_ids = {code: str(cs.unit("it.comune_collegio", code)) for code in units["unit"]}
        unit = pl.col("unit").replace_strict(unit_ids)
        cs.frame(
            "reporting_unit",
            units.select(
                id=unit,
                polity_id=pl.lit(str(italy)),
                scheme_code=pl.lit("it.comune_collegio"),
                code="unit",
                area_relation=pl.when(pl.col("colleges") == 1).then(pl.lit("equals")).otherwise(pl.lit("within")),
                area_id=pl.col("comune").replace_strict(comune_ids),
                kind=pl.lit("standard"),
                valid_from=pl.lit(GENERAL),
            ),
        )
        cs.frame(
            "reporting_unit_area",
            units.select(
                unit_id=unit,
                area_id=pl.col("college").replace_strict({code: str(id) for code, id in college_ids.items()}),
                valid_from=pl.lit(GENERAL),
            ),
        )
        cs.frame(
            "name",
            units.select(
                entity_id=unit,
                text=pl.col("comune").replace_strict(comune_names)
                + pl.when(pl.col("colleges") > 1)
                .then(pl.lit(", ") + pl.col("college").replace_strict(college_names))
                .otherwise(pl.lit("")),
                lang=pl.lit("it"),
                kind=pl.lit("official"),
                is_preferred=pl.lit(True),
            ),
        )

        party_ids: dict[str, UUID] = {}
        for label, (full, abbreviation, kind) in PARTIES.items():
            party_ids[label] = cs.key("it.party", slug(full))
            cs.add("party", id=party_ids[label], kind=kind, polity_id=italy)
            name(party_ids[label], full, "it")
            if abbreviation is not None:
                name(party_ids[label], abbreviation, "it", "abbreviation")
            name(party_ids[label], label, "it", "ballot")
        chamber = cs.key("it.body", "chamber-of-deputies")
        cs.add("body", id=chamber, polity_id=italy, kind="lower_house")
        name(chamber, "Chamber of Deputies")
        name(chamber, "Camera dei deputati", "it", "native")
        cs.add("body_size", body_id=chamber, seats=400, valid_from=FIRST_SITTING)
        systems: dict[str, UUID] = {}
        for code, rules, label, methodology in (
            (
                "camera-single-member",
                {"formula": "fptp"},
                "Chamber of Deputies single-member colleges since 2017",
                None,
            ),
            (
                "camera-proportional",
                {"formula": "hare", "threshold": {"percent": 3, "level": "list", "scope": "national"}},
                "Chamber of Deputies proportional seats since 2017",
                METHODOLOGY,
            ),
            (
                "abrogative-referendum",
                {"formula": "referendum", "quorum": 0.5},
                "Italian abrogative referendum",
                REFERENDUM_METHODOLOGY,
            ),
        ):
            systems[code] = cs.key("it.electoral_system", code)
            cs.add("electoral_system", id=systems[code], polity_id=italy, rules=rules, rules_version=1)
            name(systems[code], label)
            if methodology is not None:
                cs.add("note", entity_id=systems[code], kind="methodology", body_md=methodology)

        general = cs.key("it.election", f"{GENERAL}:general")
        cs.add(
            "election", id=general, polity_id=italy, kind="general", date_from=GENERAL, date_to=GENERAL, status="held"
        )
        name(general, "2022 Italian general election")
        name(general, "Elezioni politiche del 2022", "it", "native")
        path = f"it/{GENERAL}-general"
        cs.add("slug", path=path, entity_id=general)
        cs.add("note", entity_id=general, kind="context", body_md=ELECTION_NOTE)
        groups: dict[str, list[str]] = {}
        for label, group in lists.items():
            groups.setdefault(group, []).append(label)
        alliance_ids: dict[str, UUID] = {}
        for group, labels in groups.items():
            if len(labels) == 1:
                continue
            english, native = next(COALITIONS[label] for label in labels if label in COALITIONS)
            alliance_ids[group] = cs.key("it.alliance", f"{GENERAL}:{slug(english)}")
            cs.add("alliance", id=alliance_ids[group], kind="electoral_alliance", election_id=general)
            name(alliance_ids[group], english)
            name(alliance_ids[group], native, "it", "native")
            for label in labels:
                cs.add("alliance_member", alliance_id=alliance_ids[group], party_id=party_ids[label])

        person_ids: dict[str, UUID] = {}
        for person, surname, forename, birth, sex in persons.iter_rows():
            person_ids[person] = cs.key(f"{SOURCE}.person", person)
            cs.add(
                "person",
                id=person_ids[person],
                birth_date=birth,
                birth_precision="day",
                gender={"M": "male", "F": "female"}[sex],
            )
            name(person_ids[person], f"{forename} {surname}", "it")
        person_names = {person: f"{forename} {surname}" for person, surname, forename, *_ in persons.iter_rows()}

        result_sets: dict[str, str] = {}

        def result_set(
            key: str,
            election: UUID,
            contest: UUID | None,
            stage: str,
            source: UUID,
            completeness: float,
            default: bool = True,
        ) -> UUID:
            id = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=id,
                election_id=election,
                contest_id=contest,
                stage=stage,
                kind="counted",
                status="active",
                source_id=source,
                is_default=default,
                completeness=completeness,
            )
            if id in cs.minted:
                result_sets[key] = str(id)
            return id

        def day(value: date) -> str:
            return f"{value:%B} {value.day}, {value.year}"

        national = cs.key("it.contest", f"{GENERAL}:camera")
        cs.add(
            "contest",
            id=national,
            election_id=general,
            kind="list_allocation",
            body_id=chamber,
            district_area_id=nation,
            magnitude=245,
            electoral_system_id=systems["camera-proportional"],
            default_result_kind="counted",
        )
        name(national, "Chamber of Deputies, national seat allocation")
        cs.add("slug", path=f"{path}/chamber", entity_id=national)
        cs.add("note", entity_id=national, kind="caveat", body_md=LEGEND_NOTE)
        result_set(f"{GENERAL}:accounting", general, None, "preliminary", interno, 1)
        proclaimed = result_set(f"{GENERAL}:camera:official", general, national, "official", interno, 0)
        seats = {
            (college, label): count
            for college, label, count in members.filter("elected").group_by("college", "label").len().iter_rows()
        }
        for label in sorted(lists):
            option = cs.key("it.option", f"{GENERAL}:camera:{slug(label)}")
            cs.add(
                "contest_option",
                id=option,
                contest_id=national,
                kind="list",
                party_id=party_ids[label],
                alliance_id=alliance_ids.get(lists[label]),
                ballot_label=label,
            )
            won = sum(count for (_, other), count in seats.items() if other == label)
            if won:
                cs.add("outcome", result_set_id=proclaimed, option_id=option, kind="won", seats=won)

        contests: dict[str, UUID] = {}
        options: dict[str, str] = {}
        exhausted = {
            (college, label): EXHAUSTED_NOTE.format(college=college_names[single])
            for college, label, single in members.filter(pl.col("position").is_null())
            .select("college", "label", "single")
            .iter_rows()
        }
        offered = list_votes.select("college", "option").unique()
        for code, label in (
            colleges.filter(pl.col("scheme") == "it.plurinominale_camera").select("code", "name").iter_rows()
        ):
            contest_key = f"{GENERAL}:camera:{code}"
            contests[code] = cs.key("it.contest", contest_key)
            cs.add(
                "contest",
                id=contests[code],
                election_id=general,
                kind="list_allocation",
                body_id=chamber,
                district_area_id=college_ids[code],
                allocation_parent_id=national,
                electoral_system_id=systems["camera-proportional"],
                default_result_kind="counted",
            )
            name(contests[code], f"Chamber of Deputies, {label}")
            cs.add("slug", path=f"{path}/chamber/{slug(code)}", entity_id=contests[code])
            result_set(contest_key, general, contests[code], "preliminary", interno, 1)
            official = result_set(f"{contest_key}:official", general, contests[code], "official", interno, 0)
            revision = next((revision for revision in revisions if revision["list"] == code), None)
            revised = (
                None
                if revision is None
                else result_set(f"{contest_key}:revised", general, contests[code], "court_revised", camera, 0)
            )
            for option in sorted(offered.filter(pl.col("college") == code)["option"]):
                group = option.removeprefix("legend:")
                legend = option != group
                id = cs.key("it.option", f"{contest_key}:{slug(option)}")
                options[f"{code}:{option}"] = str(id)
                cs.add(
                    "contest_option",
                    id=id,
                    contest_id=contests[code],
                    kind="legend" if legend else "list",
                    party_id=None if legend and group in alliance_ids else party_ids[group],
                    alliance_id=alliance_ids.get(group if legend else lists[option]),
                    ballot_label="Candidate only" if legend else option,
                )
                if (code, option) not in seats:
                    continue
                cs.add(
                    "outcome",
                    result_set_id=official,
                    option_id=id,
                    kind="won",
                    seats=seats[code, option],
                    notes=exhausted.get((code, option)),
                )
                if revised is not None and revision is not None:
                    annulled = members.filter(pl.col("person") == revision["annulled"], pl.col("college") == code)
                    cs.add(
                        "outcome",
                        result_set_id=revised,
                        option_id=id,
                        kind="won",
                        seats=seats[code, option],
                        notes=REVISED_LIST.format(
                            day=day(revision["day"]),
                            displaced=person_names[revision["displaced"]],
                            annulled=person_names[revision["annulled"]],
                        )
                        if option in annulled["label"]
                        else None,
                    )
        for college, label, position, surname, forename, person in (
            members.filter(pl.col("position").is_not_null())
            .select("college", "label", "position", "surname", "forename", "person")
            .iter_rows()
        ):
            cs.add(
                "candidacy",
                id=cs.key("it.candidacy", f"{GENERAL}:camera:{college}:{slug(label)}:{position}"),
                person_id=person_ids[person],
                option_id=UUID(options[f"{college}:{label}"]),
                role="list_member",
                list_position=position,
                ballot_name=f"{surname} {forename}",
            )

        for code, label in (
            colleges.filter(pl.col("scheme") == "it.uninominale_camera").select("code", "name").iter_rows()
        ):
            contest_key = f"{GENERAL}:camera:{code}"
            contests[code] = cs.key("it.contest", contest_key)
            cs.add(
                "contest",
                id=contests[code],
                election_id=general,
                kind="body_seats",
                body_id=chamber,
                district_area_id=college_ids[code],
                magnitude=1,
                electoral_system_id=systems["camera-single-member"],
                default_result_kind="counted",
            )
            name(contests[code], f"Chamber of Deputies, {label}")
            cs.add("slug", path=f"{path}/chamber/{slug(code)}", entity_id=contests[code])
            result_set(contest_key, general, contests[code], "preliminary", interno, 1)
            official = result_set(f"{contest_key}:official", general, contests[code], "official", camera, 0)
            revision = next((revision for revision in revisions if revision["single"] == code), None)
            revised = (
                None
                if revision is None
                else result_set(f"{contest_key}:revised", general, contests[code], "court_revised", camera, 0)
            )
            for group, person, surname, forename, won in (
                candidates.filter(pl.col("college") == code)
                .select("group", "person", "surname", "forename", "won")
                .sort("surname", "forename")
                .iter_rows()
            ):
                id = cs.key("it.option", f"{contest_key}:{person}")
                options[f"{code}:{person}"] = str(id)
                cs.add(
                    "contest_option",
                    id=id,
                    contest_id=contests[code],
                    kind="candidate",
                    party_id=None if group is None or group in alliance_ids else party_ids[group],
                    alliance_id=None if group is None else alliance_ids.get(group),
                    ballot_label=f"{surname} {forename}",
                )
                cs.add(
                    "candidacy",
                    id=cs.key("it.candidacy", f"{contest_key}:{person}"),
                    person_id=person_ids[person],
                    option_id=id,
                    role="head",
                    ballot_name=f"{surname} {forename}",
                )
                if won:
                    cs.add("outcome", result_set_id=official, option_id=id, kind="elected")
                if revised is not None and revision is not None and person == revision["replacement"]:
                    cs.add(
                        "outcome",
                        result_set_id=revised,
                        option_id=id,
                        kind="elected",
                        notes=REVISED_SINGLE.format(day=day(revision["day"]), name=person_names[person]),
                    )

        staged: list[dict[str, object]] = []
        deputies = set(tenures["deputy"])
        identified: set[str] = set()
        for deputy, person, start, end, replacement, college, ended, predecessor in tenures.iter_rows():
            staged.append(
                {
                    "id": cs.key(f"{SOURCE}.tenure", f"19:{deputy}"),
                    "person_id": person_ids[person],
                    "body_id": chamber,
                    "from_date": start,
                    "to_date": end,
                    "acquired_via": "list_replacement" if replacement else "elected",
                    "contest_id": contests[college],
                    "ended_via": None if ended is None else ENDED_VIA[ended],
                    "succeeds_tenure_id": cs.key(f"{SOURCE}.tenure", f"19:{predecessor}")
                    if predecessor in deputies
                    else None,
                }
            )
            if person_ids[person] in cs.minted and person not in identified:
                identified.add(person)
                cs.add(
                    "external_id",
                    entity_id=person_ids[person],
                    scheme="it.camera.persona",
                    value=deputy.removeprefix("d").removesuffix("_19"),
                )
        for row in staged:
            cs.add("tenure", **row)

        referendum = cs.key("it.election", f"{VOTING[0]}:referendum")
        cs.add(
            "election",
            id=referendum,
            polity_id=italy,
            kind="referendum",
            date_from=VOTING[0],
            date_to=VOTING[1],
            status="held",
        )
        name(referendum, "2025 Italian abrogative referendums")
        name(referendum, "Referendum abrogativi del 2025", "it", "native")
        referendum_path = f"it/{VOTING[0]}-referendum"
        cs.add("slug", path=referendum_path, entity_id=referendum)
        cs.add("note", entity_id=referendum, kind="caveat", body_md=TURNOUT_NOTE)
        referendum_ids = {
            f"{scheme}:{code}": str(cs.unit(scheme, code))
            for scheme, code in referendum_units.select("scheme", "unit").iter_rows()
        }
        referendum_unit = pl.concat_str("scheme", pl.lit(":"), "unit").replace_strict(referendum_ids)
        countries = {
            label: proper(label)
            .replace(" E ", " e ")
            .replace(" Di ", " di ")
            .replace(" Del ", " del ")
            .replace(" D'", " d'")
            .replace("Tomè", "Tomé")
            for label in referendum_units.filter(pl.col("scheme") == "it.estero")["label"]
        }
        cs.frame(
            "reporting_unit",
            referendum_units.select(
                id=referendum_unit,
                polity_id=pl.lit(str(italy)),
                scheme_code="scheme",
                code="unit",
                area_relation=pl.when(pl.col("scheme") == "it.comune")
                .then(pl.lit("equals"))
                .when(pl.col("scheme") == "it.sezione")
                .then(pl.lit("within"))
                .otherwise(pl.lit("unlocated")),
                area_id=pl.col("comune").replace_strict(comune_ids, default=None),
                kind=pl.when(pl.col("scheme") == "it.estero")
                .then(pl.lit("abroad_mission"))
                .otherwise(pl.lit("standard")),
                valid_from=pl.when(pl.col("scheme") == "it.sezione").then(pl.lit(VOTING[0])),
            ),
        )
        cs.frame(
            "name",
            referendum_units.select(
                entity_id=referendum_unit,
                text=pl.when(pl.col("scheme") == "it.estero")
                .then(pl.col("label").replace_strict(countries, default=None))
                .when(pl.col("scheme") == "it.sezione")
                .then(
                    pl.col("comune").replace_strict(comune_names, default=None)
                    + pl.lit(", sezione ")
                    + pl.col("unit").str.split("/").list.last().str.replace(r"#(\d+)$", " (${1})")
                )
                .otherwise(pl.col("comune").replace_strict(comune_names, default=None)),
                lang=pl.lit("it"),
                kind=pl.lit("official"),
                is_preferred=pl.lit(True),
            ),
        )
        comuni_totals = {
            (question, measure, answer): value
            for question, measure, answer, value in referendum_votes.filter(pl.col("grain") == "comuni")
            .group_by("question", "measure", "answer")
            .agg(pl.col("value").sum())
            .iter_rows()
        }
        municipal_valid = dict(
            referendum_votes.filter(pl.col("scheme") == "it.comune", pl.col("measure") == VALID)
            .group_by("question")
            .agg(pl.col("value").sum())
            .iter_rows()
        )
        section_valid = dict(
            referendum_votes.filter(pl.col("grain") == "sections", pl.col("measure") == VALID)
            .group_by("question")
            .agg(pl.col("value").sum())
            .iter_rows()
        )
        for number, (english, native) in QUESTIONS.items():
            contest_key = f"{VOTING[0]}:referendum:{number}"
            contest = cs.key("it.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=referendum,
                kind="ballot_question",
                electoral_system_id=systems["abrogative-referendum"],
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type="optional_referendum")
            name(contest, english)
            name(contest, native, "it", "native")
            cs.add("slug", path=f"{referendum_path}/question-{number}", entity_id=contest)
            for position, (answer, label) in enumerate((("yes", "Sì"), ("no", "No")), 1):
                id = cs.key("it.option", f"{contest_key}:{answer}")
                options[f"{number}:{answer}"] = str(id)
                cs.add(
                    "contest_option",
                    id=id,
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position,
                    ballot_label=label,
                )
            counted = result_set(contest_key, referendum, contest, "preliminary", interno, 1)
            result_set(
                f"{contest_key}:sections",
                referendum,
                contest,
                "preliminary",
                interno,
                round(section_valid[number] / municipal_valid[number], 4),
                default=False,
            )
            turnout = comuni_totals[number, VOTERS, None] / comuni_totals[number, REGISTERED, None]
            yes = comuni_totals[number, VOTES, "yes"] / comuni_totals[number, VALID, None]
            cs.add(
                "outcome",
                result_set_id=counted,
                option_id=UUID(options[f"{number}:yes"]),
                kind="lost",
                notes=REFERENDUM_OUTCOME.format(turnout=f"{100 * turnout:.2f} percent", yes=f"{100 * yes:.2f} percent"),
            )

        fixed = {"count_round": pl.lit(0, pl.Int16), "value": pl.col("value")}
        list_key = pl.lit(f"{GENERAL}:camera:") + pl.col("college")
        cs.frame(
            "result_value",
            list_votes.rename({"votes": "value"})
            .filter(list_key.is_in(list(result_sets)))
            .select(
                result_set_id=list_key.replace_strict(result_sets),
                unit_id=unit,
                option_id=pl.concat_str("college", pl.lit(":"), "option").replace_strict(options),
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(VOTES, pl.Int16),
                flags=pl.lit(0, pl.Int16),
                **fixed,
            ),
        )
        cs.frame(
            "result_value",
            pl.read_parquet(PARSED / "candidate_votes.parquet")
            .join(candidates.select("college", "surname", "forename", "person"), on=["college", "surname", "forename"])
            .rename({"votes": "value"})
            .filter(list_key.is_in(list(result_sets)))
            .select(
                result_set_id=list_key.replace_strict(result_sets),
                unit_id=unit,
                option_id=pl.concat_str("college", pl.lit(":"), "person").replace_strict(options),
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(VOTES, pl.Int16),
                flags=pl.lit(0, pl.Int16),
                **fixed,
            ),
        )
        if f"{GENERAL}:accounting" in result_sets:
            cs.frame(
                "result_value",
                pl.read_parquet(PARSED / "accounting.parquet").select(
                    result_set_id=pl.lit(result_sets[f"{GENERAL}:accounting"]),
                    unit_id=unit,
                    option_id=pl.lit(None, pl.String),
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.col("measure").cast(pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    **fixed,
                ),
            )
        referendum_key = (
            pl.lit(f"{VOTING[0]}:referendum:")
            + pl.col("question").cast(pl.String)
            + pl.when(pl.col("grain") == "sections").then(pl.lit(":sections")).otherwise(pl.lit(""))
        )
        cs.frame(
            "result_value",
            referendum_votes.filter(referendum_key.is_in(list(result_sets))).select(
                result_set_id=referendum_key.replace_strict(result_sets),
                unit_id=referendum_unit,
                option_id=pl.concat_str(pl.col("question").cast(pl.String), pl.lit(":"), "answer").replace_strict(
                    options, default=None
                ),
                channel=pl.col("channel").cast(pl.Int16),
                measure=pl.col("measure").cast(pl.Int16),
                flags=pl.col("flags").cast(pl.Int16),
                **fixed,
            ),
        )
        return cs.write()
