import struct
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import numpy as np
import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset
from ea.sources.licenses import UNLICENSED
from ea.store import DATA, connect

SOURCE = "de.reichstag"
VERSION = "1"
TSV = Path(__file__).with_name("reichstag.tsv")
PAGES = DATA / "pages" / SOURCE
PUBLISHER = "Statistisches Reichsamt"
ARCHIVE = "https://archive.org/download/per_statistisches-jahrbuch-fur-das-deutsche-reich_statistisches-jahrbuch-{item}_{year}_{volume}/page/n{leaf}.jpg"
VOTES, REGISTERED, CAST, VALID = 1, 2, 3, 4
DIGITIZED = 8
WIDTH = 1400
TOTALS = {"registered": REGISTERED, "cast": CAST, "valid": VALID}

PARTIES = {
    "spd": ("Q49768", "Sozialdemokratische Partei Deutschlands", "SPD"),
    "nsdap": ("Q7320", "Nationalsozialistische Deutsche Arbeiterpartei", "NSDAP"),
    "kpd": ("Q153401", "Kommunistische Partei Deutschlands", "KPD"),
    "zentrum": ("Q157537", "Deutsche Zentrumspartei", "Zentrum"),
    "bvp": ("Q302884", "Bayerische Volkspartei", "BVP"),
    "dnvp": ("Q158227", "Deutschnationale Volkspartei", "DNVP"),
    "dvp": ("Q316533", "Deutsche Volkspartei", "DVP"),
    "landvolk": ("Q315407", "Christlich-Nationale Bauern- und Landvolkpartei", "Landvolk"),
    "staatspartei": ("Q2283759", "Deutsche Staatspartei", "DStP"),
    "wirtschaftspartei": ("Q541230", "Reichspartei des deutschen Mittelstandes", "WP"),
    "csvd": ("Q314832", "Christlich-Sozialer Volksdienst", "CSVD"),
    "bauernpartei": ("Q871964", "Deutsche Bauernpartei", "DBP"),
    "wuerttemberg_landbund": ("Q2596896", "Württembergischer Bauern- und Weingärtnerbund", "Landbund"),
    "kampffront": ("Q179260", "Kampffront Schwarz-Weiß-Rot", "KSWR"),
    "dhp": ("Q567550", "Deutsch-Hannoversche Partei", "DHP"),
    "kvp": ("Q548022", "Konservative Volkspartei", "KVP"),
    "saechsisches_landvolk": ("Q27926490", "Sächsisches Landvolk", "SLV"),
    "nsfb": ("Q164866", "Nationalsozialistische Freiheitsbewegung", "NSFB"),
    "ddp": ("de.party:DDP", "Deutsche Demokratische Partei", "DDP"),
    "landbund": ("Q1801975", "Landbund", "Landbund"),
    "bbmb": ("Q812330", "Bayerischer Bauern- und Mittelstandsbund", "BBMB"),
    "hessischer_bauernbund": ("Q1615913", "Hessischer Bauernbund", "HBB"),
    "deutschsoziale": ("Q1206490", "Deutschsoziale Partei", "DSP"),
    "nlb": (None, "Nationalliberale Landespartei Bayern", "NLB"),
    "aufbaupartei": (None, "Deutsche Aufwertungs- und Aufbaupartei", "DAAP"),
    "grossdeutsch_hessen": (None, "Großdeutsch-Hessischer Bund", "GHB"),
    "bauernliste_ctb": (None, "Bauernliste Coblenz-Trier-Birkenfeld", "Bauernliste"),
    "badischer_landbund": (None, "Badischer Landbund", "BLB"),
    "rheinhessische_landliste": (None, "Rheinhessische Landliste", "RHL"),
    "bauern_schaffende": (None, "Bauern und schaffende Berufe", "BsB"),
}
BLOCS = {"kampffront"}
JOINT = {
    "zentrum_bvp": ("Zentrum und Bayerische Volkspartei", ("zentrum", "bvp")),
    "kvp_dhp": ("Konservative Volkspartei und Deutsch-Hannoversche Partei", ("kvp", "dhp")),
    "dvp_staatspartei": ("Einheitsliste Deutsche Volkspartei – Deutsche Staatspartei", ("dvp", "staatspartei")),
    "dnvp_nlb": (
        "Deutschnationale Volkspartei und Nationalliberale Landespartei Bayern",
        ("dnvp", "nlb"),
    ),
    "zentrum_aufbau": ("Zentrum und Deutsche Aufwertungs- und Aufbaupartei", ("zentrum", "aufbaupartei")),
    "hessischer_bauernbund_landliste": (
        "Hessischer Bauernbund und Rheinhessische Landliste",
        ("hessischer_bauernbund", "rheinhessische_landliste"),
    ),
    "wp_bauern_schaffende": (
        "Wirtschaftspartei des deutschen Mittelstandes und Bauern und schaffende Berufe",
        ("wirtschaftspartei", "bauern_schaffende"),
    ),
    "bbmb_wp": (
        "Bayerischer Bauern- und Mittelstandsbund und Wirtschaftspartei des deutschen Mittelstandes",
        ("bbmb", "wirtschaftspartei"),
    ),
    "aufwertung": ("Deutschsoziale Partei und Reichsbund für Aufwertung", ("deutschsoziale",)),
}
OTHERS = "Andere Parteien"

ELECTIONS: dict[date, dict[str, Any]] = {
    date(1924, 12, 7): {
        "english": "December 1924 German federal election",
        "german": "Reichstagswahl Dezember 1924",
        "item": "fr-das-d",
        "year": "1924-1925",
        "edition": "1924/25",
        "volume": 44,
        "printed": "392 and 393",
        "seats": 493,
        "pages": {
            445: {
                "top": 0.245,
                "registered": (0.276, 0.341),
                "cast": (0.346, 0.410),
                "valid": (0.447, 0.513),
                "dnvp": (0.522, 0.597),
                "nsfb": (0.636, 0.695),
                "dvp": (0.733, 0.793),
                "zentrum": (0.828, 0.887),
            },
            446: {
                "top": 0.247,
                "ddp": (0.082, 0.146),
                "spd": (0.188, 0.256),
                "kpd": (0.298, 0.366),
                "bvp": (0.418, 0.490),
                "wirtschaftspartei": (0.535, 0.603),
                "aufwertung": (0.668, 0.715),
                "others": (0.758, 0.827),
            },
        },
        "lists": {
            1: {"wirtschaftspartei": "wp_bauern_schaffende"},
            2: {"bvp": "dhp"},
            3: {"bvp": "dhp"},
            12: {"bvp": "landbund"},
            14: {"bvp": "dhp"},
            15: {"bvp": "dhp"},
            16: {"bvp": "dhp"},
            17: {"zentrum": "zentrum_aufbau"},
            19: {"bvp": "grossdeutsch_hessen"},
            21: {"bvp": "bauernliste_ctb"},
            24: {"wirtschaftspartei": "bbmb_wp"},
            25: {"wirtschaftspartei": "bbmb_wp"},
            26: {"dnvp": "dnvp_nlb", "wirtschaftspartei": "bbmb_wp"},
            31: {"bvp": "wuerttemberg_landbund"},
            32: {"bvp": "badischer_landbund"},
            33: {"bvp": "hessischer_bauernbund_landliste"},
            34: {"bvp": "dhp"},
        },
        "won": {
            "dnvp": 103,
            "nsfb": 14,
            "dvp": 51,
            "zentrum": 69,
            "ddp": 32,
            "spd": 131,
            "kpd": 45,
            "bvp": 19,
            "landbund": 8,
            "dhp": 4,
            "wirtschaftspartei": 17,
        },
        "caveat": (
            "The table counts each list where it stood, and the national totals beneath it equal the sums of the "
            "Wahlkreise. One column holds the Bayerische Volkspartei (Wahlkreise 24 to 27), the Deutsch-Hannoversche "
            "Partei, the Landbund, and the regional farmers' lists joined to the Landbund's national list: the "
            "Großdeutsch-Hessischer Bund, the Bauernliste Coblenz-Trier-Birkenfeld, the Württembergischer Bauern- und "
            "Weingärtnerbund, the Badischer Landbund, and the Hessischer Bauernbund with the Rheinhessische Landliste. "
            "The Landbund's 8 seats include those won on these regional lists. Several cells sum joined lists, which "
            "the Reichsamt splits in its notes: Wahlkreis 17, Zentrum 385,654 and Deutsche Aufwertungs- und "
            "Aufbaupartei 26,554; Wahlkreis 1, Wirtschaftspartei 8,953 and Bauern und schaffende Berufe 14,634; "
            "Wahlkreis 24, Bayerischer Bauern- und Mittelstandsbund 159,666 and Wirtschaftspartei 8,665; Wahlkreis 25, "
            "Wirtschaftspartei 18,533 and Bayerischer Bauern- und Mittelstandsbund 100,317; Wahlkreis 26, Bayerischer "
            "Bauern- und Mittelstandsbund 36,338 and Bayerischer Mittelstandsbund (Wirtschaftspartei) 31,669. The "
            "Wirtschaftspartei's 17 seats include members of the Bayerischer Bauern- und Mittelstandsbund elected on "
            "its national list. In Wahlkreis 26 (Franken) the Deutschnationale Volkspartei ran a joint list with the "
            "Nationalliberale Landespartei Bayern."
        ),
    },
    date(1930, 9, 14): {
        "english": "1930 German federal election",
        "german": "Reichstagswahl 1930",
        "year": 1931,
        "volume": 50,
        "printed": "546 and 547",
        "seats": 577,
        "pages": {
            593: {
                "top": 0.215,
                "registered": (0.335, 0.405),
                "cast": (0.408, 0.476),
                "valid": (0.505, 0.575),
                "spd": (0.578, 0.636),
                "dnvp": (0.668, 0.728),
                "zentrum": (0.757, 0.8165),
                "kpd": (0.849, 0.905),
            },
            594: {
                "top": 0.222,
                "wirtschaftspartei": (0.297, 0.353),
                "dvp": (0.092, 0.150),
                "staatspartei": (0.188, 0.257),
                "nsdap": (0.393, 0.450),
                "landvolk": (0.492, 0.555),
                "kvp": (0.597, 0.645),
                "csvd": (0.685, 0.736),
                "others": (0.776, 0.844),
            },
        },
        "lists": {
            14: {"kvp": "kvp_dhp"},
            15: {"kvp": "dhp"},
            16: {"kvp": "dhp"},
            24: {"zentrum": "bvp"},
            25: {"zentrum": "bvp"},
            26: {"zentrum": "bvp"},
            27: {"zentrum": "zentrum_bvp"},
            28: {"landvolk": "saechsisches_landvolk"},
            29: {"landvolk": "saechsisches_landvolk"},
            30: {"landvolk": "saechsisches_landvolk"},
            31: {"staatspartei": "dvp_staatspartei"},
            32: {"staatspartei": "dvp_staatspartei"},
        },
        "won": {
            "spd": 143,
            "dnvp": 41,
            "zentrum": 68,
            "bvp": 19,
            "kpd": 77,
            "dvp": 30,
            "staatspartei": 20,
            "wirtschaftspartei": 23,
            "nsdap": 107,
            "landvolk": 19,
            "dhp": 3,
            "kvp": 4,
            "csvd": 14,
            "bauernpartei": 6,
            "wuerttemberg_landbund": 3,
        },
        "caveat": (
            "The table counts each list where it stood, and the national totals beneath it equal the sums of the "
            "Wahlkreise. The Konservative Volkspartei and the Deutsch-Hannoversche Partei share a column: the Deutsch-"
            "Hannoversche Partei stood alone in Wahlkreise 15 and 16, the two ran a joint list in Wahlkreis 14 "
            "(Weser-Ems), and the Konservative Volkspartei stood alone elsewhere. The Bayerische Volkspartei stood in "
            "Wahlkreise 24 to 26, and the joint list of the Zentrum and the Bayerische Volkspartei in Wahlkreis 27 "
            "(Pfalz) is counted by the Reichsamt with the Zentrum. The Sächsisches Landvolk stood in Wahlkreise 28 to "
            "30 on the national list of the Landvolk, whose 19 seats include any won there. In Wahlkreise 31 and 32 "
            "(Württemberg and Baden) the Deutsche Volkspartei and the Deutsche Staatspartei ran a joint list, which "
            "the Reichsamt counts with the Staatspartei. The Deutsche Bauernpartei (6 seats) and the Württembergischer "
            "Bauern- und Weingärtnerbund (3 seats) are counted under other parties."
        ),
    },
    date(1932, 7, 31): {
        "english": "July 1932 German federal election",
        "german": "Reichstagswahl Juli 1932",
        "year": 1932,
        "volume": 51,
        "printed": "542 and 543",
        "seats": 608,
        "pages": {
            585: {
                "top": 0.235,
                "registered": (0.315, 0.392),
                "cast": (0.397, 0.461),
                "valid": (0.494, 0.563),
                "spd": (0.564, 0.621),
                "nsdap": (0.657, 0.714),
                "kpd": (0.746, 0.806),
                "zentrum": (0.836, 0.897),
            },
            586: {
                "top": 0.235,
                "dnvp": (0.059, 0.118),
                "dvp": (0.170, 0.222),
                "landvolk": (0.288, 0.338),
                "staatspartei": (0.395, 0.452),
                "wirtschaftspartei": (0.505, 0.560),
                "csvd": (0.625, 0.684),
                "others": (0.745, 0.805),
            },
        },
        "lists": {
            24: {"zentrum": "bvp"},
            25: {"zentrum": "bvp"},
            26: {"zentrum": "bvp"},
            27: {"zentrum": "zentrum_bvp"},
        },
        "won": {
            "spd": 133,
            "nsdap": 230,
            "kpd": 89,
            "zentrum": 75,
            "bvp": 22,
            "dnvp": 37,
            "landvolk": 1,
            "dvp": 7,
            "staatspartei": 4,
            "wirtschaftspartei": 2,
            "csvd": 4,
            "bauernpartei": 2,
            "wuerttemberg_landbund": 2,
        },
        "caveat": (
            "The table counts each list where it stood. The national totals the Reichsamt printed beneath it also move "
            "182,500 votes cast for minor lists, which are counted here under other parties, to the national lists they "
            "were joined to. The joint list of the Zentrum and the Bayerische Volkspartei in Wahlkreis 27 (Pfalz) is "
            "counted by the Reichsamt with the Zentrum. Its member elected there belonged to the Bayerische Volkspartei, "
            "which also shared its national list with the Wirtschaftspartei; of the 24 members elected on that list, 22 "
            "were from the Bayerische Volkspartei and 2 from the Wirtschaftspartei. The Deutschnationale Volkspartei, the "
            "Deutsche Volkspartei, and the Landvolk shared a national list too, and their 45 members split 37, 7, and 1. "
            "The Deutsche Bauernpartei (2 seats) and the Württembergischer Bauern- und Weingärtnerbund (2 seats) stood in "
            "only a few constituencies, and their votes there are counted under other parties."
        ),
    },
    date(1933, 3, 5): {
        "english": "March 1933 German federal election",
        "german": "Reichstagswahl März 1933",
        "year": 1933,
        "volume": 52,
        "printed": "540 and 541",
        "seats": 647,
        "pages": {
            583: {
                "top": 0.186,
                "registered": (0.322, 0.392),
                "cast": (0.402, 0.475),
                "valid": (0.512, 0.589),
                "nsdap": (0.597, 0.670),
                "spd": (0.722, 0.792),
                "kpd": (0.837, 0.907),
            },
            584: {
                "top": 0.172,
                "zentrum": (0.092, 0.154),
                "kampffront": (0.202, 0.270),
                "dvp": (0.318, 0.380),
                "csvd": (0.436, 0.503),
                "bauernpartei": (0.552, 0.618),
                "staatspartei": (0.657, 0.726),
                "others": (0.775, 0.842),
            },
        },
        "lists": {
            24: {"zentrum": "bvp"},
            25: {"zentrum": "bvp"},
            26: {"zentrum": "bvp"},
            27: {"zentrum": "zentrum_bvp"},
        },
        "won": {
            "nsdap": 288,
            "spd": 120,
            "staatspartei": 5,
            "kpd": 81,
            "zentrum": 73,
            "bvp": 19,
            "kampffront": 52,
            "dvp": 2,
            "csvd": 4,
            "bauernpartei": 2,
            "wuerttemberg_landbund": 1,
        },
        "caveat": (
            "The Reichsamt notes that the 81 seats won by the Kommunistische Partei were void under the provisional law "
            "of 31 March 1933 on the coordination of the Länder with the Reich, and the Social Democratic seats under the "
            "decree of 7 July 1933 on securing the leadership of the state. The Deutsche Staatspartei stood on the Social Democrats' national list, and 5 of the "
            "125 members elected on it belonged to the Staatspartei. The joint list of the Zentrum and the Bayerische "
            "Volkspartei in Wahlkreis 27 (Pfalz) is counted by the Reichsamt with the Zentrum; its member elected there "
            "belonged to the Bayerische Volkspartei. The Deutsche Volkspartei, the Christlich-Sozialer Volksdienst, and "
            "the Deutsche Bauernpartei shared a national list, and their 8 members split 2, 4, and 2. The "
            "Württembergischer Bauern- und Weingärtnerbund won 1 seat; its votes are counted under other parties."
        ),
    },
}
SYSTEM = (
    "Proportional representation by the automatic method of the Reich Election Law of 27 April 1920. Each list won one "
    "seat for every 60,000 votes in its Wahlkreis. Remainders were pooled in the Wahlkreisverband, which gave one more "
    "seat for each 60,000, and the last remainders went to the party's national list (Reichswahlvorschlag), which could "
    "win no more seats than the party had won in the Wahlkreise. The size of the Reichstag followed the turnout."
)
NOTE = (
    "Votes by Wahlkreis are transcribed from the table Gesamtergebnis der Wahlen zum Reichstag in the Statistisches "
    "Jahrbuch für das Deutsche Reich. Every Wahlkreis's list votes add up to its valid votes, and each column adds up to "
    "the total printed beneath it. Ballots cast are the printed turnout (Wahlbeteiligung), which includes invalid ballots. "
    "Seats are the members elected for each party as the Jahrbuch reports them."
)


def url(day: date, leaf: int) -> str:
    spec = ELECTIONS[day]
    return ARCHIVE.format(item=spec.get("item", "fr-das-deutsc"), year=spec["year"], volume=spec["volume"], leaf=leaf)


def title(day: date) -> str:
    spec = ELECTIONS[day]
    return f"Statistisches Jahrbuch für das Deutsche Reich {spec.get('edition', spec['year'])}"


def fetch() -> None:
    for day, spec in ELECTIONS.items():
        for leaf in spec["pages"]:
            raw.fetch(SOURCE, url(day, leaf), suffix=".jpg")


def bands(gray: np.ndarray, top: float, columns: dict[str, tuple[float, float]]) -> list[tuple[float, float]]:
    height, width = gray.shape
    left, right = next(iter(columns.values()))
    margin = (right - left) / 5
    strip = gray[:, int((left + margin) * width) : int((right - margin) * width)] < 120
    inked = strip.sum(axis=1) > 3
    lines: list[tuple[int, int]] = []
    for y in range(int(top * height), int(0.8 * height)):
        if not inked[y]:
            continue
        if lines and y - lines[-1][1] <= 2:
            lines[-1] = (lines[-1][0], y)
        else:
            lines.append((y, y))
    lines = [(top, bottom) for top, bottom in lines if bottom - top >= 8]
    if len(lines) < 36:
        raise ValueError(f"{len(lines)} text lines in the first column, expected at least 36")
    return [((top - 3) / height, (bottom + 3) / height) for top, bottom in lines[:35]]


def parse() -> None:
    PAGES.mkdir(parents=True, exist_ok=True)
    pages = []
    for day, spec in ELECTIONS.items():
        for leaf, layout in spec["pages"].items():
            top, columns = layout["top"], {k: v for k, v in layout.items() if k != "top"}
            artifact = raw.cached(SOURCE, url(day, leaf))
            image = PAGES / f"{artifact.sha256}-1.png"
            if not image.exists():
                with Image.open(artifact.path) as scan:
                    scan.convert("L").resize((WIDTH, round(scan.height * WIDTH / scan.width))).save(image)
            width, height = struct.unpack(">II", image.read_bytes()[16:24])
            with Image.open(image) as page:
                gray = np.asarray(page.convert("L"))
            pages.append(
                {
                    "date": day,
                    "leaf": leaf,
                    "image": f"pages/{SOURCE}/{image.name}",
                    "width": width,
                    "height": height,
                    "rows": [list(row) for row in bands(gray, top, columns)],
                }
            )
    folder = raw.parsed(SOURCE)
    folder.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(pages).write_parquet(folder / "pages.parquet")


def returns() -> pl.DataFrame:
    frame = pl.read_csv(TSV, separator="\t", try_parse_dates=True, schema_overrides={"votes": pl.Int64})
    for (day,), group in frame.group_by("date"):
        spec = ELECTIONS[day]
        if sorted(group["wahlkreis"].unique()) != list(range(1, 36)):
            raise ValueError(f"{day}: expected Wahlkreise 1 to 35")
        printed = {column for layout in spec["pages"].values() for column in layout if column != "top"}
        if set(group["column"]) - printed:
            raise ValueError(f"{day}: columns not on the pages: {set(group['column']) - printed}")
        for (wahlkreis,), rows in group.group_by("wahlkreis"):
            counts = dict(rows.select("column", "votes").iter_rows())
            lists = sum(votes for column, votes in counts.items() if column not in TOTALS)
            if lists != counts["valid"] or not counts["valid"] <= counts["cast"] <= counts["registered"]:
                raise ValueError(f"{day} Wahlkreis {wahlkreis}: {lists} list votes against {counts['valid']} valid")
        if sum(spec["won"].values()) != spec["seats"]:
            raise ValueError(f"{day}: seats add up to {sum(spec['won'].values())}, not {spec['seats']}")
    return frame


def emit() -> Path:
    frame = returns()
    pages = pl.read_parquet(raw.parsed(SOURCE) / "pages.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Weimar Reichstag elections by Wahlkreis from the Statistisches Jahrbuch",
            polity="DE",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        germany = cs.polity("DE")
        reichstag = cs.key("de.body", "reichstag")
        if reichstag in cs.minted:
            cs.add("body", id=reichstag, polity_id=germany, kind="lower_house")
            name(reichstag, "Reichstag")
            name(reichstag, "Reichstag", "de", "native")
        parties: dict[str, UUID] = {}
        for key, (item, label, short) in PARTIES.items():
            if item is None:
                parties[key] = cs.key(f"{SOURCE}.party", key)
            elif ":" in item:
                parties[key] = cs.key(*item.split(":", 1))
            else:
                parties[key] = cs.key("wikidata", item)
            if parties[key] in cs.minted:
                cs.add("party", id=parties[key], kind="bloc" if key in BLOCS else "party", polity_id=germany)
                name(parties[key], label, "de")
                name(parties[key], short, "de", "abbreviation")
        units = {number: cs.unit("de.reich_wahlkreis", str(number)) for number in range(1, 36)}

        values: list[tuple[str, str, str | None, int, int, int]] = []
        locators: list[tuple[str, str, str | None, int, str, float, float, float, float]] = []
        for day, spec in ELECTIONS.items():
            source = cs.source(PUBLISHER, title(day))
            first = next(iter(spec["pages"]))
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title(day),
                    url=url(day, first),
                    license=UNLICENSED,
                    notes=(
                        f"Chapter Wahlen und Abstimmungen, table Gesamtergebnis der Wahlen zum Reichstag, printed "
                        f"pages {spec['printed']}, from the Internet Archive scan of volume {spec['volume']}."
                    ),
                )
            page_ids: dict[int, UUID] = {}
            bands_of: dict[int, list[list[float]]] = {}
            for page in pages.filter(pl.col("date") == day).iter_rows(named=True):
                artifact = cs.artifact(raw.cached(SOURCE, url(day, page["leaf"])), source)
                page_ids[page["leaf"]] = cs.page(artifact, 1)
                bands_of[page["leaf"]] = page["rows"]
                cs.add(
                    "source_page",
                    id=page_ids[page["leaf"]],
                    artifact_id=artifact,
                    page_number=1,
                    image_key=page["image"],
                    width=page["width"],
                    height=page["height"],
                )

            election = cs.key("de.election", f"{day}:general")
            cs.add(
                "election", id=election, polity_id=germany, kind="general", date_from=day, date_to=day, status="held"
            )
            name(election, spec["english"])
            name(election, spec["german"], "de", "native")
            path = f"de/{day}-general"
            cs.add("slug", path=path, entity_id=election)
            contest = cs.key("de.contest", f"{day}:reichstag")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="list_allocation",
                body_id=reichstag,
                magnitude=spec["seats"],
                default_result_kind="counted",
            )
            name(contest, "Reichstag")
            cs.add("slug", path=f"{path}/reichstag", entity_id=contest)
            cs.add("note", entity_id=contest, kind="methodology", body_md=f"{SYSTEM}\n\n{NOTE}")
            cs.add("note", entity_id=contest, kind="caveat", body_md=spec["caveat"])
            result_set = cs.key(f"{SOURCE}.result_set", str(day))
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )

            group = frame.filter(pl.col("date") == day)
            stood = {
                spec["lists"].get(wk, {}).get(column, column)
                for wk, column in group.select("wahlkreis", "column").iter_rows()
            }
            options: dict[str, UUID] = {}
            for key in sorted((stood - set(TOTALS)) | set(spec["won"])):
                option = cs.key("de.option", f"{day}:reichstag:{key}")
                if key == "others":
                    cs.add("contest_option", id=option, contest_id=contest, kind="others", ballot_label=OTHERS)
                elif key in JOINT:
                    label, members = JOINT[key]
                    alliance = cs.key("de.alliance", f"{day}:{key}")
                    cs.add("alliance", id=alliance, kind="joint_list", election_id=election)
                    name(alliance, label, "de")
                    for member in members:
                        cs.add("alliance_member", alliance_id=alliance, party_id=parties[member], role=None)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        alliance_id=alliance,
                        ballot_label=label,
                    )
                else:
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        party_id=parties[key],
                        ballot_label=PARTIES[key][1],
                    )
                options[key] = option
            for key, seats in spec["won"].items():
                cs.add("outcome", result_set_id=result_set, option_id=options[key], kind="won", seats=seats)

            for leaf, layout in spec["pages"].items():
                for column, box in layout.items():
                    if column == "top":
                        continue
                    left, right = box
                    for wahlkreis, votes in (
                        group.filter(pl.col("column") == column)
                        .select("wahlkreis", "votes")
                        .sort("wahlkreis")
                        .iter_rows()
                    ):
                        top, bottom = bands_of[leaf][wahlkreis - 1]
                        key = spec["lists"].get(wahlkreis, {}).get(column, column)
                        option = None if column in TOTALS else str(options[key])
                        measure = TOTALS.get(column, VOTES)
                        row = (str(result_set), str(units[wahlkreis]), option, measure)
                        values.append((*row, votes, DIGITIZED))
                        locators.append((*row, str(page_ids[leaf]), left, top, right, bottom))
            if len(values) != len({value[:4] for value in values}):
                raise ValueError(f"{day}: a value is placed twice")

        def frame_of(data: list[Any], schema: dict[str, Any]) -> pl.DataFrame:
            return pl.DataFrame(data, schema=schema, orient="row").with_columns(
                channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16)
            )

        key_schema = {"result_set_id": pl.String, "unit_id": pl.String, "option_id": pl.String, "measure": pl.Int16}
        cs.frame("result_value", frame_of(values, {**key_schema, "value": pl.Int64, "flags": pl.Int16}))
        cs.frame(
            "value_locator",
            frame_of(
                locators,
                {
                    **key_schema,
                    "page_id": pl.String,
                    "x0": pl.Float64,
                    "y0": pl.Float64,
                    "x1": pl.Float64,
                    "y1": pl.Float64,
                },
            ),
        )
        return cs.write()
