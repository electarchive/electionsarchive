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
    "dvfp": ("Q541427", "Deutschvölkische Freiheitspartei", "DVFP"),
    "nationalliberale_vereinigung": (None, "Nationalliberale Vereinigung", "NLV"),
    "deutscher_bauernbund": (None, "Deutscher Bauernbund", "DBB"),
    "sh_landespartei": ("de.party:SHLP", "Schleswig-Holsteinische Landespartei", "SHLP"),
    "rheinhessische_bauernschaft": (None, "Rheinhessische Bauernschaft", "RHB"),
    "gasthofsgewerbe": (None, "Bayerisches Gasthofsgewerbe", "BGG"),
    "volksrecht": ("Q896602", "Reichspartei für Volksrecht und Aufwertung", "VRP"),
    "cnmp": (None, "Christlich-Nationale Mittelstandspartei", "CNMP"),
    "pfarrer_greber": (None, "Pfarrer-Greber-Partei", "PGP"),
    "csr": (None, "Christlich-Soziale Reichspartei", "CSRP"),
    "kratofiel": (None, "Beamtengruppe Kratofiel", "Kratofiel"),
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
    "vvv": ("Vaterländisch-völkische Vereinigung", ("dnvp",)),
    "vvr": ("Vaterländisch-völkischer Rechtsblock", ("dnvp",)),
    "dnvp_vvb": ("Deutschnationale Volkspartei und Völkisch-vaterländischer Block", ("dnvp",)),
    "dvp_nlp": ("Deutsche Volkspartei – Nationalliberale Partei", ("dvp",)),
    "ddp_bauernbund": ("Deutsche demokratische Partei und Deutscher Bauernbund", ("ddp", "deutscher_bauernbund")),
    "deutscher_block": ("Deutscher Block in Bayern", ("ddp", "deutscher_bauernbund")),
    "landliste": ("Landliste", ("landbund", "nationalliberale_vereinigung")),
    "hessischer_bauernbund_bauernschaft": (
        "Hessischer Bauernbund und Rheinhessische Bauernschaft",
        ("hessischer_bauernbund", "rheinhessische_bauernschaft"),
    ),
    "bbmb_gasthof": (
        "Bayerischer Bauern- und Mittelstandsbund und Bayerisches Gasthofsgewerbe",
        ("bbmb", "gasthofsgewerbe"),
    ),
    "ddp_volksrecht": (
        "Deutsche Demokratische Partei und Reichspartei für Volksrecht und Aufwertung",
        ("ddp", "volksrecht"),
    ),
    "wp_greber": (
        "Reichspartei des deutschen Mittelstandes und Pfarrer-Greber-Partei",
        ("wirtschaftspartei", "pfarrer_greber"),
    ),
    "landvolk_cnmp_dhp": (
        (
            "Christlich-Nationale Bauern- und Landvolkpartei, Christlich-Nationale Mittelstandspartei und "
            "Deutsch-Hannoversche Partei"
        ),
        ("landvolk", "cnmp", "dhp"),
    ),
    "landvolk_cnmp": (
        "Christlich-Nationale Bauern- und Landvolkpartei und Christlich-Nationale Mittelstandspartei",
        ("landvolk", "cnmp"),
    ),
    "landvolk_dhp": (
        "Christlich-Nationale Bauern- und Landvolkpartei und Deutsch-Hannoversche Partei",
        ("landvolk", "dhp"),
    ),
    "landvolk_slv": (
        "Christlich-Nationale Bauern- und Landvolkpartei und Sächsisches Landvolk",
        ("landvolk", "saechsisches_landvolk"),
    ),
    "landvolk_cnmp_slv": (
        (
            "Christlich-Nationale Bauern- und Landvolkpartei, Christlich-Nationale Mittelstandspartei und "
            "Sächsisches Landvolk"
        ),
        ("landvolk", "cnmp", "saechsisches_landvolk"),
    ),
    "volksrecht_csr_kratofiel": (
        "Reichspartei für Volksrecht und Aufwertung, Christlich-Soziale Reichspartei und Beamtengruppe Kratofiel",
        ("volksrecht", "csr", "kratofiel"),
    ),
    "volksrecht_kratofiel": (
        "Reichspartei für Volksrecht und Aufwertung und Beamtengruppe Kratofiel",
        ("volksrecht", "kratofiel"),
    ),
}
OTHERS = "Andere Parteien"

ELECTIONS: dict[date, dict[str, Any]] = {
    date(1924, 5, 4): {
        "english": "May 1924 German federal election",
        "german": "Reichstagswahl Mai 1924",
        "item": "fr-das-d",
        "year": "1924-1925",
        "edition": "1924/25",
        "volume": 44,
        "printed": "390 and 391",
        "seats": 472,
        "pages": {
            443: {
                "top": 0.232,
                "registered": (0.303, 0.370),
                "cast": (0.372, 0.432),
                "valid": (0.478, 0.545),
                "dnvp": (0.560, 0.626),
                "dvfp": (0.668, 0.727),
                "dvp": (0.782, 0.826),
                "zentrum": (0.860, 0.917),
            },
            444: {
                "top": 0.228,
                "ddp": (0.122, 0.177),
                "spd": (0.222, 0.290),
                "kpd": (0.338, 0.405),
                "bvp": (0.465, 0.530),
                "landliste": (0.585, 0.642),
                "wirtschaftspartei": (0.693, 0.767),
                "others": (0.795, 0.866),
            },
        },
        "lists": {
            **{wk: {"bvp": "deutschsoziale"} for wk in (*range(1, 12), *range(14, 19), 20, 21, 23, 28, 29, 30)},
            2: {"bvp": "deutschsoziale", "landliste": "nationalliberale_vereinigung"},
            3: {"bvp": "deutschsoziale", "landliste": "nationalliberale_vereinigung"},
            4: {"bvp": "deutschsoziale", "landliste": "nationalliberale_vereinigung"},
            5: {"bvp": "deutschsoziale", "landliste": "nationalliberale_vereinigung"},
            10: {"bvp": "deutschsoziale", "ddp": "ddp_bauernbund"},
            12: {"dnvp": "vvv", "landliste": "landbund"},
            13: {"wirtschaftspartei": "sh_landespartei"},
            14: {"bvp": "deutschsoziale", "wirtschaftspartei": "dhp"},
            15: {"bvp": "deutschsoziale", "wirtschaftspartei": "dhp"},
            16: {"bvp": "deutschsoziale", "wirtschaftspartei": "dhp"},
            24: {"dvp": "dvp_nlp", "ddp": "deutscher_block", "wirtschaftspartei": "bbmb_gasthof"},
            25: {"dvp": "dvp_nlp", "ddp": "deutscher_block", "wirtschaftspartei": "bbmb"},
            26: {"dnvp": "dnvp_nlb", "ddp": "deutscher_block"},
            27: {"ddp": "deutscher_block"},
            28: {"bvp": "deutschsoziale", "landliste": "nationalliberale_vereinigung"},
            29: {"bvp": "deutschsoziale", "landliste": "nationalliberale_vereinigung"},
            30: {"bvp": "deutschsoziale", "landliste": "nationalliberale_vereinigung"},
            31: {"dnvp": "vvr", "landliste": "wuerttemberg_landbund"},
            32: {"landliste": "badischer_landbund"},
            33: {"dnvp": "dnvp_vvb", "landliste": "hessischer_bauernbund_bauernschaft"},
            34: {"wirtschaftspartei": "dhp"},
        },
        "won": {
            "dnvp": 95,
            "dvfp": 32,
            "dvp": 45,
            "zentrum": 65,
            "ddp": 28,
            "spd": 100,
            "kpd": 62,
            "bvp": 16,
            "deutschsoziale": 4,
            "landliste": 10,
            "bbmb_wp": 10,
            "dhp": 5,
        },
        "caveat": (
            "The table counts each list where it stood, and the national totals beneath it equal the sums of the "
            "Wahlkreise. Oppeln (Wahlkreis 9) includes the supplementary elections of 21 September 1924, and Dresden-"
            "Bautzen (Wahlkreis 28) the repeat vote in Dittmannsdorf held the same day. One column holds the "
            "Bayerische Volkspartei (Wahlkreise 24 to 27) and the Deutschsoziale Partei elsewhere. The Landliste "
            "(national list 17) column holds its member lists: the Nationalliberale Vereinigung, the Landbund, the "
            "Württembergischer Bauern- und Weingärtnerbund, the Badischer Landbund, and the Hessischer Bauernbund "
            "with the Rheinhessische Bauernschaft; the marker on Wahlkreis 7 (Breslau) is illegible in the scan, so "
            "its 3,012 votes are credited to the Landliste. Its 10 seats are the Landliste's. A further column "
            "holds the lists joined to the Bayerische Bauern- und Mittelstandsbund's national list 8, which are the "
            "Wirtschaftspartei des deutschen Mittelstandes and its regional affiliates (the Hausbesitz und "
            "Gewerbeverband in Wahlkreis 10, the Rheinischer Wirtschaftsbund in Wahlkreise 20 to 23, the Bayerischer "
            "Mittelstandsbund in Wahlkreis 26, and the Wirtschaftliche Vereinigungen of the Württemberg and Baden "
            "middle classes in Wahlkreise 31 and 32, all counted here under the Wirtschaftspartei), and the lists "
            "joined to the Deutsch-Hannoversche Partei's national list 9. National list 8 won 10 seats, held here by "
            "the joint list of the Bayerischer Bauern- und Mittelstandsbund and the Wirtschaftspartei, and list 9 "
            "won 5. In Wahlkreis 24 the cell joins the Bayerischer Bauern- und Mittelstandsbund (98,096) and the "
            "Bayerisches Gasthofsgewerbe (2,594). In Wahlkreis 27 (Pfalz) the Vereinigte nationale Rechte stood as "
            "one list; the Reichsamt credits 60,000 of its votes to the Deutsche Volkspartei and the remaining "
            "23,542 to the Deutschnationale Volkspartei, as shown. The Deutschvölkische Freiheitspartei column is "
            "the national list 23 of the völkisch movement."
        ),
    },
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
    date(1928, 5, 20): {
        "english": "1928 German federal election",
        "german": "Reichstagswahl 1928",
        "year": 1929,
        "volume": 48,
        "printed": "484 and 485",
        "seats": 491,
        "pages": {
            535: {
                "top": 0.255,
                "registered": (0.300, 0.368),
                "cast": (0.372, 0.446),
                "valid": (0.480, 0.551),
                "spd": (0.552, 0.618),
                "dnvp": (0.645, 0.712),
                "zentrum": (0.752, 0.818),
                "dvp": (0.843, 0.903),
            },
            536: {
                "top": 0.245,
                "kpd": (0.100, 0.169),
                "ddp": (0.200, 0.262),
                "wirtschaftspartei": (0.312, 0.375),
                "nsdap": (0.417, 0.476),
                "bauernpartei": (0.513, 0.569),
                "landvolk": (0.619, 0.670),
                "volksrecht": (0.716, 0.771),
                "others": (0.806, 0.861),
            },
        },
        "lists": {
            **{wk: {"ddp": "ddp_volksrecht"} for wk in (10, 23, 26)},
            2: {"ddp": "ddp_volksrecht", "landvolk": "landvolk_cnmp_dhp"},
            3: {"ddp": "ddp_volksrecht", "landvolk": "landvolk_cnmp_dhp"},
            4: {"ddp": "ddp_volksrecht", "landvolk": "landvolk_cnmp"},
            14: {"ddp": "ddp_volksrecht", "landvolk": "landvolk_dhp"},
            15: {"landvolk": "landvolk_dhp"},
            16: {"landvolk": "landvolk_dhp"},
            20: {"wirtschaftspartei": "wp_greber"},
            21: {"wirtschaftspartei": "wp_greber"},
            24: {"zentrum": "bvp", "bauernpartei": "bbmb", "volksrecht": "volksrecht_csr_kratofiel"},
            25: {"zentrum": "bvp", "bauernpartei": "bbmb", "volksrecht": "volksrecht_csr_kratofiel"},
            26: {"zentrum": "bvp", "bauernpartei": "bbmb", "volksrecht": "volksrecht_kratofiel"},
            27: {"zentrum": "zentrum_bvp", "volksrecht": "volksrecht_kratofiel"},
            28: {"landvolk": "landvolk_slv"},
            29: {"landvolk": "landvolk_cnmp_slv"},
            30: {"ddp": "ddp_volksrecht", "landvolk": "landvolk_slv"},
            31: {"landvolk": "wuerttemberg_landbund"},
            34: {"landvolk": "landvolk_dhp"},
            35: {"landvolk": "landvolk_cnmp"},
        },
        "won": {
            "spd": 153,
            "dnvp": 73,
            "zentrum": 62,
            "bvp": 16,
            "dvp": 45,
            "kpd": 54,
            "ddp": 25,
            "wirtschaftspartei": 23,
            "nsdap": 12,
            "bauernpartei": 8,
            "wuerttemberg_landbund": 3,
            "landvolk": 10,
            "dhp": 3,
            "saechsisches_landvolk": 2,
            "volksrecht": 2,
        },
        "caveat": (
            "The table counts each list where it stood, and the national totals beneath it equal the sums of the "
            "Wahlkreise. Many cells sum a party's list and the regional lists joined (angeschlossen) to its national "
            "list, which the Reichsamt splits in its notes; each such cell is stored as one joint list. The Deutsche "
            "Demokratische Partei column holds the Reichspartei für Volksrecht und Aufwertung where it was joined to "
            "national list 6 (Wahlkreise 2, 3, 4, 10, 14, 23, 26, and 30; 26,252 votes in all). The "
            "Wirtschaftspartei column holds the Pfarrer-Greber-Partei in Wahlkreise 20 and 21 (1,516 and 7,996), "
            "and in Wahlkreis 23 (Düsseldorf West) the sum of two Wirtschaftspartei lists, Geck (16,899) and Bredt "
            "(31,319). One column holds the Christlich-Nationale Bauern- und Landvolkpartei (national list 15) with "
            "the Christlich-Nationale Mittelstandspartei and the Deutsch-Hannoversche Partei joined to it, the "
            "Sächsisches Landvolk (Wahlkreise 28 to 30), and the Landbund (national list 14), which stood only in "
            "Württemberg as the Bauern- und Weingärtnerbund; the national split is Landbund 199,513, Landvolkpartei "
            "571,586, Mittelstandspartei 9,933, Deutsch-Hannoversche Partei 195,375, and Sächsisches Landvolk "
            "127,648. Of the 13 members elected on the Landvolkpartei's lists, the Reichsamt names 3 as Deutsch-"
            "Hannoversche Partei (2 in the Wahlkreise and 1 in the Wahlkreisverbände), so 10 are counted for the "
            "Landvolkpartei. The Bayerische Volkspartei stood in Wahlkreise 24 to 26 in the Zentrum column, and the "
            "joint list of the Zentrum and the Bayerische Volkspartei in Wahlkreis 27 (Pfalz) is counted by the "
            "Reichsamt with the Zentrum (Zentrum 3,710,747 and Bayerische Volkspartei 945,306 nationally). The "
            "Deutsche Bauernpartei stood in Bavaria (Wahlkreise 24 to 26) as the Bayerischer Bauern- und "
            "Mittelstandsbund, and its 8 seats include those won there. In Wahlkreise 24 to 27 the Volksrechtpartei "
            "column holds joint lists with the Beamtengruppe Kratofiel, and in Wahlkreise 24 and 25 also with the "
            "Christlich-Soziale Reichspartei."
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
