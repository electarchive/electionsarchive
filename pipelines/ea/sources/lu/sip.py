"""Luxembourg's European Parliament elections of 1979, 1984, and 1989, from the government's bulletins of results.

The Service information et presse du gouvernement published each election's official results in its bulletin: the
electors, ballots, and the lists' votes and seats for the single national constituency, then every candidate's votes in
the order of the vote. The Ministry of State's open data portal holds the three pages of results as one PDF. The
figures were transcribed from the page images; the build checks that each list's candidates and list votes add up to
its printed total, that the lists add up to the printed total of valid votes, that the ballots add up, and that the
elected candidates match the seats each list won. Each value is located on the page through the PDF's text layer when
that layer reads it unambiguously.

The elections were held on the day of the legislative elections. The Chamber elections of those days are `lu.chd`'s.
The country is the area the archive already holds; the parties and people are this source's own.
"""

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from html import unescape
from pathlib import Path
from uuid import uuid5

from ea import package, raw
from ea.package import Package

SOURCE = "lu.sip"
URL = (
    "https://download.data.public.lu/resources/resultats-des-elections-europeennes-au-grand-duche-de-luxembourg-"
    "de-1979-1989/20170629-150001/Elections_EU_1979-1989.pdf"
)
PAGE = (
    "https://data.public.lu/fr/datasets/resultats-des-elections-europeennes-au-grand-duche-de-luxembourg-de-1979-1989/"
)
LUXEMBOURG = str(uuid5(package.NAMESPACE, "polity\x1fLU"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fLUX"))
REGISTERED, CAST, VALID, INVALID, BLANK = 2, 3, 4, 5, 6
VOTES, PREFERENCE, LIST_VOTES, CANDIDATE_VOTES = 1, 7, 8, 10
STATS = {"registered": REGISTERED, "cast": CAST, "valid": VALID, "invalid": INVALID, "blank": BLANK}

METHOD = (
    "Transcribed from the results the government's information service published for the election ({title}), which "
    "give the electors and ballots for the single national constituency, each list's votes and seats, and every "
    "candidate's votes. Each voter has six votes and may spread them across lists, giving a candidate at most two, so "
    "the lists' votes add up to far more than the valid ballots: the printed total of valid votes ({valid_votes:,}) "
    "counts every vote, and a list's votes are its candidates' personal votes plus the votes cast for the list as "
    "printed. The electoral quotient is {quotient:,}. The results number the candidates in the order of their votes, "
    "and the figures are stored as printed. The constituency is the only unit published."
)
SYSTEM_NOTE = (
    "From the first direct election in 1979, Luxembourg's six members of the European Parliament are elected in one "
    "national constituency by list proportional representation with the Hagenbach-Bischoff method. Each voter has "
    "six votes and may vote for a whole list or spread them across lists, giving a candidate at most two."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
SYSTEM = key("electoral_system", "european")

# Each list's party, source-local: key, name, abbreviation, English name.
PARTIES = {
    "lsap": ("Parti ouvrier socialiste luxembourgeois", "POSL", "Luxembourg Socialist Workers' Party"),
    "psd": ("Parti social-démocrate", "PSD", "Social Democratic Party"),
    "kpl": ("Parti communiste luxembourgeois", "KPL", "Communist Party of Luxembourg"),
    "dp": ("Parti démocratique", "DP", "Democratic Party"),
    "alw": ("Alternativ Lëscht Wiert Iech", "ALW", None),
    "csv": ("Parti chrétien-social", "CSV", "Christian Social People's Party"),
    "lcr": ("Ligue communiste révolutionnaire", "LCR", "Revolutionary Communist League"),
    "liberal": ("Liberal Partei", None, "Liberal Party"),
    "psi": ("Parti socialiste indépendant", "PSI", "Independent Socialist Party"),
    "greng-alternativ": ("Di Gréng - Alternativ", None, "The Greens - Alternative"),
    "gral": ("Gréng Alternativ Allianz", "GRAL", "Green Alternative Alliance"),
    "firwat": ("Firwat nët?", None, None),
    "glei": ("Gréng Lëscht Ekologesch Initiativ", "GLEI", "Green List Ecological Initiative"),
    "rsp": ("RSP Géint Rassismus a Faschismus", "RSP", None),
    "nb": ("Lëtzebuerg de Lëtzebuerger National Bewegong", "NB", None),
    "gap": ("Déi Gréng Alternativ", "GAP", "Green Alternative Party"),
}


@dataclass
class Entry:
    number: int
    label: str
    party: str
    seats: int
    listed: int
    total: int
    candidates: list[tuple[str, int, bool]]

    @property
    def personal(self) -> int:
        return sum(votes for _, votes, _ in self.candidates)


@dataclass
class Election:
    day: date
    title: str
    pages: tuple[int, int]
    stats: dict[str, int]
    valid_votes: int
    quotient: int
    entries: list[Entry]


def candidates(text: str) -> list[tuple[str, int, bool]]:
    found = []
    for line in text.strip().splitlines():
        name, votes, *elected = line.split(";")
        found.append((name.strip(), int(votes), bool(elected)))
    return found


def entry(number: int, label: str, party: str, seats: int, listed: int, total: int, text: str) -> Entry:
    return Entry(number, label, party, seats, listed, total, candidates(text))


ELECTIONS = [
    Election(
        date(1979, 6, 10),
        "Les résultats des élections du 10 juin 1979 pour l'Assemblée des Communautés européennes",
        (1, 3),
        {"registered": 212740, "cast": 189141, "blank": 10875, "invalid": 7507, "valid": 170759},
        974991,
        139285,
        [
            entry(
                1,
                "Parti ouvrier socialiste luxembourgeois",
                "lsap",
                1,
                144804,
                211106,
                """Abens Victor;12767;e
Poos Jacques F.;7596
Krieps Robert;7440
Berg Benny;6704
Wohlfart Joseph;6324
Schmit Lydie;6038
Hansen Frankie;3979
Van den Bulcke René;3726
Dondelinger Willy;3548
Thoss Maurice;3336
Meis Robert;2581
Schlechter Marcel;2263""",
            ),
            entry(
                2,
                "Parti social-démocrate",
                "psd",
                0,
                34974,
                68289,
                """Lulling Astrid;12273
Cravatte Henry;7535
Bousser Albert;2007
Hurt Georges;1594
Georges Fernand;1593
Schleimer Roger;1472
Weyler Pierre;1445
van Kauvenbergh Adrien;1434
Goedert Albert;1411
Gansen-Bertrang Lily;1134
Conter Claude;900
Schroeder François;517""",
            ),
            entry(
                3,
                "Parti communiste luxembourgeois",
                "kpl",
                0,
                38322,
                48813,
                """Urbany René;3680
Bernard Zénon;1752
Bisdorff Aloyse;1366
Wehenkel Henri;675
Hübsch Fernand;622
Grissius Francis;564
Passeri-Bechberger Marianne;373
Sowa Nicole;364
Pütz Marcel;329
Leick Georges;322
Krier Clement;283
Wesquet Jean;161""",
            ),
            entry(
                4,
                "Parti démocratique",
                "dp",
                2,
                168948,
                274307,
                """Thorn Gaston;32314;e
Flesch Colette;21660;e
Barthel Josy;12240
Krieps Emile;8469
Hamilius Jean;7204
Mart René;5977
Meintz Carlo;5207
Goerens Charles;3758
Eyschen Josy;2631
Prüssen Robert;2186
Gitzinger Robert;2085
Schon Jean-François;1628""",
            ),
            entry(
                5,
                "Alternativ Lëscht Wiert Iech",
                "alw",
                0,
                7776,
                9485,
                """Schons Guy;387
Dörner Charles;374
Krier-Binsfeld Mariette;340
Grosbusch Gilbert J. P.;230
Reding Jean-Claude;207
Houss Jules;171""",
            ),
            entry(
                6,
                "Parti chrétien-social",
                "csv",
                3,
                232548,
                352296,
                """Werner Pierre;27305;e
Boden Fernand;14532;e
Santer Jacques;14119;e
Wolter Jean;10678
Spautz Jean;9598
Ney Camille;9221
Estgen Nicolas;7283
Fischbach Marc;6282
Mosar Nic;6094
Lentz-Cornette Marcelle;5311
Bürger René;5133
Schiltges Marie-Madeleine;4192""",
            ),
            entry(
                7,
                "Ligue communiste révolutionnaire",
                "lcr",
                0,
                4014,
                5085,
                """Lulling Jean-Pierre;270
Gorza Thérèse;235
Portante Jean;179
Kremer Gaston;165
Kremer André;125
Mertzig Robert;97""",
            ),
            entry(
                8,
                "Liberal Partei",
                "liberal",
                0,
                3624,
                5610,
                """Bertrand Jean-Pierre;961
Faltz Madeleine;169
Dupont Albert;143
Besch André;133
Meiers Jules;125
Scherer Pierre;103
Dörner Charles Pierre;85
Neuman Norbert;82
Goergen François;73
Goetzinger Henri;52
Weides Roger;31
Weides Henri;29""",
            ),
        ],
    ),
    Election(
        date(1984, 6, 17),
        "Les résultats des élections du 17 juin 1984 pour l'Assemblée des Communautés européennes",
        (4, 5),
        {"registered": 215792, "cast": 191602, "blank": 12309, "invalid": 5405, "valid": 173888},
        990142,
        141449,
        [
            entry(
                1,
                "Kommunistesch Partei vu Lëtzebuerg (KPL)",
                "kpl",
                0,
                30546,
                40395,
                """Urbany René;3915
Bisdorff Aloyse;1346
Bernard Zénon;1297
Hoffmann André;525
Wehenkel Henri;524
Grissius Francis;461
Hübsch Fernand;393
Hoffmann François;346
Jones Ginette;290
Pütz Marcel;286
Muller Marie-Paule dite Milly;276
Moes André;190""",
            ),
            entry(
                2,
                "LSAP - d'Sozialisten",
                "lsap",
                2,
                209100,
                296382,
                """Abens Victor;18044;e
Poos Jacques F.;13411;e
Krieps Robert;11215
Berg Benny;6531
Schmit Lydie;6432
Goebbels Robert;6251
Wohlfahrt Joseph;6167
Hengel René;5257
Bintz-Erpelding Marie dite Micky;3827
Dondelinger Willy;3664
Schlechter Marcel;3355
Thoss Maurice;3128""",
            ),
            entry(
                3,
                "Chrëschtlech-Sozial Vollekspartei (CSV)",
                "csv",
                3,
                209718,
                345586,
                """Santer Jacques;22432;e
Boden Fernand;18445;e
Spautz Jean;15761;e
Juncker Jean-Claude;12800
Estgen Nicolas;12786
Fischbach Marc;11117
Lentz-Cornette Marcelle;8341
Juncker Edouard;7898
Muhlen Ernest;7560
Hennicot-Schoepges Erna;7481
Glesener Marcel;5899
Mosar Nic.;5348""",
            ),
            entry(
                4,
                "Demokratesch Partei - DP",
                "dp",
                1,
                117432,
                218481,
                """Flesch Colette;22239;e
Polfer Lydie;13388
Helminger Paul;9903
Barthel Josy;8935
Goerens Charles;8847
Krieps Emile;7762
Konen René dit Boy;7129
Hamilius Jean;5235
Mart René;5159
Meintz Carlo;4846
Hübsch René;4144
Grethen Henri;3462""",
            ),
            entry(
                5,
                "P.S.I. Parti Socialiste Indépendant",
                "psi",
                0,
                16722,
                25355,
                """Gremling Jean;4835
Gremling Jean-Georges;1128
Hermes Bernard dit Ben;415
Knepper Aimé;337
Felten Léon;331
Fally Vincent;281
Jacob Bernard dit Ben;264
Kieffer Bernard dit Ben;249
Clees Robert dit Roby;249
Flammang Erny;195
Sturm Louise;184
Moes Roland;165""",
            ),
            entry(
                6,
                "Ligue Communiste Révolutionnaire LCR (IVe Internationale)",
                "lcr",
                0,
                2550,
                3791,
                """Gorza Marie-Thérèse;181
Lulling Jean-Pierre;160
Kremer André;141
Portante Jean;116
Weimerskirch Florence dite Flo';114
Scholl Marianne;107
Kremer Gaston;85
Jost Frank dit Tun;84
Mertzig Robert;74
Theis Hubert dit Bert;71
Majeres Robert;60
Wirth Jos.;48""",
            ),
            entry(
                7,
                "Di Grëng - Alternativ",
                "greng-alternativ",
                0,
                50226,
                60152,
                """Huss Jean dit Muck;1289
Bodé Marie-Thérèse dite Thers;1195
Weber Joseph dit Jup;1158
Bebing Lucienne;1074
Bock Guy;914
Geisbusch Jean;806
Dabé Monique;780
Huss-Kremer Viviane;709
Breier Henri;657
Hilgert Marielle;560
Turcitu Rita;494
Schaeler Gérard;290""",
            ),
        ],
    ),
    Election(
        date(1989, 6, 18),
        "Les résultats des élections du 18 juin 1989 pour l'Assemblée des Communautés européennes",
        (6, 8),
        {"registered": 218940, "cast": 191342, "blank": 12000, "invalid": 4870, "valid": 174472},
        993951,
        141994,
        [
            entry(
                1,
                "Kommunistesch Partei vu Lëtzebuerg (KPL)",
                "kpl",
                0,
                32580,
                46791,
                """Urbany René;7049
Bernard Zénon;1693
Bisdorff Aloyse;1600
Hoffmann André;795
Wehenkel Henri;610
Hübsch Fernand;474
Jones Ginette;392
Pütz Marcel;389
Braun Litzelmann Arlette;361
Foetz Guy;354
Kersch Daniel;275
Ruckert-Müller Barbe;219""",
            ),
            entry(
                2,
                "Demokratesch Partei (DP)",
                "dp",
                1,
                100950,
                198254,
                """Flesch Colette;22009;e
Polfer Lydie épouse Wurth;20342
Goerens Charles;8846
Barthel Josy;8039
Helminger Paul;7561
Brasseur Anne;6630
Meintz Carlo;4858
Grethen Henri;4547
Mart René;4127
Hübsch René;3655
Rippinger Jean-Paul;3551
Peffer Théo;3139""",
            ),
            entry(
                3,
                "Chrëschtlech-Sozial Vollekspartei (CSV)",
                "csv",
                3,
                182886,
                346621,
                """Santer Jacques;28182;e
Juncker Jean-Claude;20615;e
Spautz Jean;15535;e
Lulling Astrid;15194
Boden Fernand;15027
Estgen Nicolas;11808
Reding Viviane;11103
Fischbach Marc;10840
Colling François;9743
Hennicot-Schoepges Erna;9294
Steichen René;8955
Bourg Willy;7439""",
            ),
            entry(
                4,
                "LSAP-d'Sozialisten",
                "lsap",
                2,
                166878,
                252920,
                """Poos Jacques F.;19657;e
Delvaux-Stehres Mady;9154;e
Goebbels Robert;8480
Krieps Robert;8467
Fayot Ben;7526
Lahure Johny;6960
Schlechter Marcel;6748
Wohlfart Georges;6071
Berg Benny;4354
Thoss Maurice;3229
Mangers-Anen Edmée;2801
Becker Raymond;2595""",
            ),
            entry(
                5,
                "Gréng Alternativ Allianz (GRAL)",
                "gral",
                0,
                6234,
                8577,
                """Altman Irène dite Ili;532
Closter Fernand;261
Grosbusch Gilbert J.F.;226
Schwartz Simone;223
Loos Guy;219
Loos-Waringo Renée;197
Turnau-Offermann Marie-Jeanne;171
Medernach Robert;151
Schaeler Gérard;109
Stein Jean-Paul;107
Seeberger Guy;76
Schwirtz Robert;71""",
            ),
            entry(
                6,
                "Firwat nët?",
                "firwat",
                0,
                604,
                1876,
                """Schmit Francis;710
Paulus Georges;562""",
            ),
            entry(
                7,
                "Gréng Lëscht Ekologesch Initiativ",
                "glei",
                0,
                41028,
                61054,
                """Weber Jup;10969
Schuster Jemp;1489
Köller Richard;1338
Meisch Jim;1160
de la Fontaine Isabelle;1151
Heyman Paul;895
Michaelis-Blaise Coryse;771
Kremer-Wagener Jacquie;558
Clesen Nick;506
Müller Georgette;490
Ruppert Fons;369
Grond Marc;330""",
            ),
            entry(
                8,
                "RSP Géint Rassismus a Faschismus",
                "rsp",
                0,
                4122,
                6053,
                """Engel Robert;443
Gorza Thérèse;277
Weimerskirch Florence dite Flou;201
Schiltz Mariette;187
Kremer André;135
Jost Frank dit Tun;133
Stoos Gilbert;110
Theis Hubert dit Bert;106
Turpel Justin;98
Mertzig Robert;91
Wirth Jos;76
Thümmel Claude;74""",
            ),
            entry(
                9,
                "Lëtzebuerg de Lëtzebuerger National Bewegong",
                "nb",
                0,
                24120,
                28867,
                """Federspiel Nicolas;799
Probst-Kieffer Marie-Jeanne;743
Haupert Paul;435
Flick Joseph;406
Hoffmann Nicole;402
Peters Pierre;400
Helbach Luc;326
Peffer Marc;307
Zondacq Henri;263
Hoff Johny;240
Hoss Armand;234
Wester Gust;192""",
            ),
            entry(
                10,
                "Déi Gréng Alternativ (GAP)",
                "gap",
                0,
                31488,
                42926,
                """Huss Jean;3867
Bock Guy;1371
Bodé Thers;1340
Turcitu Rita;824
Wagener Renée;752
Bausch François;719
Breier Henri;591
Wagener Paul dit Fifi;472
Graf Richard;458
Tramarin Jacqueline;439
Steinbach Gilbert;305
Plein Erny;300""",
            ),
        ],
    ),
]
# What the results print for each election's nominative votes and list votes, for the checks.
PRINTED = {
    date(1984, 6, 17): {"personal": 353848, "listed": 636294},
    date(1989, 6, 18): {"personal": 403049, "listed": 590902},
}


# Where the results' own totals disagree with the sum of the rows they print, by election.
DISCREPANCIES = {
    date(1989, 6, 18): (
        "The printed totals of the lists' votes (590,902) and of all valid votes (993,951) are 12 more than the sums "
        "of the ten lists' rows (590,890 and 993,939); every list's own candidates, list votes, and total agree, "
        "and the rows are stored as printed."
    )
}


def check(e: Election) -> None:
    """Raise on any figure that disagrees with another the results print, except the known discrepancies."""
    s = e.stats
    if s["blank"] + s["invalid"] + s["valid"] != s["cast"]:
        raise ValueError(f"{e.day}: ballots do not add up")
    for item in e.entries:
        if item.personal + item.listed != item.total:
            raise ValueError(f"{e.day} list {item.number}: {item.personal} + {item.listed} is not {item.total}")
        if sum(1 for _, _, elected in item.candidates if elected) != item.seats:
            raise ValueError(f"{e.day} list {item.number}: elected candidates differ from its {item.seats} seats")
        ranked = [votes for _, votes, _ in item.candidates]
        if ranked != sorted(ranked, reverse=True):
            raise ValueError(f"{e.day} list {item.number}: candidates are not in the order of their votes")
    if e.day in DISCREPANCIES:
        if sum(item.total for item in e.entries) != e.valid_votes - 12:
            raise ValueError(f"{e.day}: the known discrepancy of 12 votes no longer holds")
    elif sum(item.total for item in e.entries) != e.valid_votes:
        raise ValueError(f"{e.day}: lists add up to {sum(i.total for i in e.entries)}, not {e.valid_votes}")
    if sum(item.seats for item in e.entries) != 6:
        raise ValueError(f"{e.day}: seats do not add up to 6")
    printed = PRINTED.get(e.day)
    if (
        printed
        and e.day not in DISCREPANCIES
        and (
            sum(i.personal for i in e.entries) != printed["personal"]
            or sum(i.listed for i in e.entries) != printed["listed"]
        )
    ):
        raise ValueError(f"{e.day}: nominative or list votes differ from the printed totals")
    if abs(e.valid_votes // 7 + 1 - e.quotient) > 1:
        raise ValueError(f"{e.day}: quotient {e.quotient} does not fit {e.valid_votes} votes")


# Locating values on the page.

Box = tuple[float, float, float, float]


@dataclass
class Word:
    page: int
    text: str
    box: Box


def words() -> list[tuple[Word, tuple[float, float]]]:
    """Every word of the PDF with its box in points, and its page's size."""
    path = raw.cached(SOURCE, URL).path
    html = subprocess.run(["pdftotext", "-bbox", str(path), "-"], capture_output=True, check=True, text=True).stdout
    found = []
    for number, (head, body) in enumerate(re.findall(r"<page ([^>]*)>(.*?)</page>", html, flags=re.DOTALL), start=1):
        size = re.search(r'width="([\d.]+)" height="([\d.]+)"', head)
        if size is None:
            raise ValueError(f"page {number} has no size")
        for x0, y0, x1, y1, text in re.findall(
            r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', body
        ):
            found.append(
                (
                    Word(number, unescape(text), (float(x0), float(y0), float(x1), float(y1))),
                    (float(size[1]), float(size[2])),
                )
            )
    return found


def locate(value: int, pool: list[Word]) -> Word | None:
    """The word that reads as the number, when exactly one does."""
    text = f"{value:,}".replace(",", ".")
    hits = [w for w in pool if re.sub(r"[^\d.]", "", w.text) == text]
    return hits[0] if len(hits) == 1 and value >= 100 else None


# Packages.


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{e.day}" for e in ELECTIONS)]


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf", check=not_pdf)


def not_pdf(path: Path) -> None:
    if not path.read_bytes()[:5].startswith(b"%PDF"):
        raise ValueError(f"{path}: the portal did not answer with a PDF")


def parse() -> None:
    registry().write()
    for e in ELECTIONS:
        check(e)
        election(e).write()


def emit() -> None:
    package.run("load", packages())


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The government information service's bulletins as a source, Luxembourg's European Parliament electoral "
        "system, and the parties whose lists stood in 1979, 1984, and 1989",
    )
    p.external.add(LUXEMBOURG)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Service information et presse du gouvernement du Grand-Duché de Luxembourg",
        title="Résultats des élections européennes au Grand-Duché de Luxembourg de 1979-1989",
        url=PAGE,
        license="Published on the Luxembourg open data portal with the licence not specified",
        license_url=PAGE,
        attribution="Service information et presse, Gouvernement du Grand-Duché de Luxembourg",
    )
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=LUXEMBOURG,
        valid_from=date(1979, 6, 10),
        rules={"formula": "hagenbach_bischoff", "lists": {"order": "open", "vacancy": "next_by_votes"}},
        rules_version=1,
    )
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    for party, (native, abbreviation, english) in PARTIES.items():
        id = key("party", party)
        p.add("party", id=id, kind="party", polity_id=LUXEMBOURG)
        p.name(id, native, "fr", "official")
        if abbreviation and abbreviation != native:
            p.name(id, abbreviation, "fr", "abbreviation", False)
        if english:
            p.name(id, english, "en", "english", False)
    return p


def election(e: Election) -> Package:
    p = Package(
        f"{SOURCE}/{e.day}",
        SOURCE,
        f"Luxembourg's European Parliament election of {e.day}, from the government's results",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {LUXEMBOURG, COUNTRY, "world.country"}
    artifact = raw.cached(SOURCE, URL)
    p.uses(artifact)
    artifact_id = p.artifact(artifact, SOURCE_ID)
    first, last = e.pages
    sized = [(w, size) for w, size in words() if first <= w.page <= last]
    pool = [w for w, _ in sized]
    sizes = {w.page: size for w, size in sized}
    page_ids: dict[int, str] = {}

    def page_id(number: int) -> str:
        if number not in page_ids:
            id = page_ids[number] = key("page", number)
            width, height = sizes[number]
            p.add(
                "source_page",
                id=id,
                artifact_id=artifact_id,
                page_number=number,
                image_key=f"pages/{SOURCE}/{number}.png",
                width=round(width),
                height=round(height),
            )
        return page_ids[number]

    year = e.day.year
    election_id = key("election", e.day)
    p.add(
        "election",
        id=election_id,
        polity_id=LUXEMBOURG,
        kind="general",
        date_from=e.day,
        date_to=e.day,
        status="held",
    )
    english = f"{year} Luxembourg European Parliament election"
    p.name(election_id, english, "en", "official")
    p.name(election_id, f"Élections européennes {year}", "fr", "native", False)
    p.name(election_id, f"Europawahlen {year}", "lb", "native", False)
    path = f"lu/{e.day}-european"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.add(
        "citation",
        id=key("citation", election_id),
        entity_id=election_id,
        source_id=SOURCE_ID,
        locator=json.dumps({"url": URL, "title": e.title}),
    )
    unit = key("unit", e.day)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=LUXEMBOURG,
        scheme_code="world.country",
        code=f"{e.day}:national:LU",
        area_relation="equals",
        area_id=COUNTRY,
        kind="standard",
    )
    p.name(unit, "Luxembourg", "fr", "official")
    contest = key("contest", e.day)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="list_allocation",
        body_id=None,
        district_area_id=COUNTRY,
        magnitude=6,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, english, "en", "official")
    p.add("slug", path=f"{path}/national", entity_id=contest, is_current=True)
    result_set = key("result_set", e.day)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    body = " ".join(
        [METHOD.format(title=e.title, valid_votes=e.valid_votes, quotient=e.quotient), DISCREPANCIES.get(e.day, "")]
    ).strip()
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=body, lang="en")
    p.add(
        "citation",
        id=key("citation", result_set),
        entity_id=result_set,
        source_id=SOURCE_ID,
        locator=json.dumps({"url": URL, "title": e.title}),
    )

    def value(option: str | None, measure: int, number: int, declared: bool = False) -> None:
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=0,
            value=number,
        )
        word = locate(number, pool)
        if word is not None:
            p.add(
                "value_locator",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=0,
                page_id=page_id(word.page),
                source_id=SOURCE_ID,
                x0=word.box[0],
                y0=word.box[1],
                x1=word.box[2],
                y1=word.box[3],
            )
        if declared:
            p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=number)

    for name, measure in STATS.items():
        value(None, measure, e.stats[name], declared=True)
    for item in e.entries:
        list_option = key("option", e.day, "list", item.number)
        party = key("party", item.party)
        p.add(
            "contest_option",
            id=list_option,
            contest_id=contest,
            kind="list",
            party_id=party,
            ballot_position=item.number,
            ballot_label=item.label,
        )
        value(list_option, VOTES, item.total, declared=True)
        value(list_option, LIST_VOTES, item.listed)
        value(list_option, PREFERENCE, item.personal)
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=list_option,
            kind="won" if item.seats else "lost",
            seats=item.seats,
        )
        for rank, (who, votes, elected) in enumerate(item.candidates, start=1):
            person = key("person", e.day, item.number, who)
            p.add("person", id=person)
            p.name(person, who, "fr", "official")
            candidate = key("option", e.day, "candidate", item.number, who)
            p.add(
                "contest_option",
                id=candidate,
                contest_id=contest,
                kind="candidate",
                parent_option_id=list_option,
                party_id=party,
                ballot_position=rank,
                ballot_label=who,
            )
            candidacy = key("candidacy", e.day, item.number, who)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=list_option,
                role="list_member",
                ballot_name=who,
                member_of_party_id=party,
            )
            value(candidate, CANDIDATE_VOTES, votes)
            if elected:
                p.add(
                    "outcome", result_set_id=result_set, option_id=list_option, candidacy_id=candidacy, kind="elected"
                )
    p.accept(
        "accounting.votes_valid",
        "each voter has six votes, so the lists' votes add up to far more than the valid ballots; both are stored as "
        "the results print them",
    )
    return p
