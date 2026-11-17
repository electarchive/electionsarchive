"""The Saeima's votes for the President of Latvia, 1993-2023, from the sitting stenograms.

The Saeima elects the President (Satversme art. 35-36). Deputies vote on a ballot that lists every candidate, in rounds,
until one candidate has at least 51 votes. After each round the vote counters read out a protocol, which the stenogram
prints. Each round is stored as its own contest, chained to the round before it, with one national unit: the Saeima in
session.
"""

import html
import re
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

from ea import package, raw
from ea.package import Package
from ea.sources.licenses import UNLICENSED
from ea.sources.lv import cvk

SOURCE = "lv.saeima"
LATVIA = cvk.LATVIA
COUNTRY = cvk.COUNTRY
SAEIMA = cvk.SAEIMA
VOTES, CAST, VALID, INVALID, VOTERS = 1, 3, 4, 5, 14
STENO = "https://www.saeima.lv/lv/transcripts/view/{}"

URLS = {
    "1993": "https://www.saeima.lv/steno/st_93/060793.html",
    "1996": "https://www.saeima.lv/steno/st_96/st1806.html",
    "1999": "https://www.saeima.lv/steno/1999/st1706.html",
    "1999x": "https://www.saeima.lv/steno/1999/sp1706.html",
    "2003": "https://www.saeima.lv/steno/2002_8/st_030620/st2006.htm",
    "2007": (
        "https://www.saeima.lv/lv/sasaukumi/9-saeima/stenogrammas/"
        "latvijas-republikas-9-saeimas-pavasara-sesijas-astota-sede-2007-gada-31-maija/"
    ),
    "2011": STENO.format(62),
    "2015": STENO.format(303),
    "2019": STENO.format(539),
    "2023": "https://www.vestnesis.lv/op/2023/109.2",
}
ENCODINGS = {"1993": "cp1257", "1996": "cp1257", "1999": "cp1257", "1999x": "cp1257", "2003": "cp1257"}
STATEMENTS = {
    "2015": "https://likumi.lv/ta/id/274504",
    "2023": "https://www.vestnesis.lv/op/2023/104A.1",
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SRC = key("source", "saeima")
PRESIDENCY = key("office", "president")
SYSTEM = key("electoral_system", "president")


def fetch() -> None:
    for url in (*URLS.values(), *STATEMENTS.values()):
        raw.fetch(SOURCE, url, suffix=".html")


def text(name: str) -> str:
    """The stenogram as plain text: tags dropped, entities decoded, whitespace collapsed."""
    artifact = raw.cached(SOURCE, URLS[name])
    body = artifact.path.read_bytes().decode(ENCODINGS.get(name, "utf-8"), errors="replace")
    body = re.sub(r"(?is)<(script|style).*?</\1>", " ", body)
    body = re.sub(r"<[^>]+>", " ", body)
    return re.sub(r"[\s \u0085]+", " ", html.unescape(body))


@dataclass(frozen=True)
class Round:
    """One protocol of the vote counters.

    Candidates are listed with the votes "for" them. Where a protocol also prints the votes "against" each candidate,
    those go in `against`. A ballot has the names of the candidates the deputy votes against struck out, so a ballot
    with every name struck is a vote against all candidates, counted in `none`. `proof` holds phrases that must appear
    in the stenogram, which ties each number to its page.
    """

    day: str
    candidates: tuple[tuple[str, int], ...]
    proof: tuple[str, ...]
    against: tuple[int, ...] = ()
    issued: int | None = None
    cast: int | None = None
    valid: int | None = None
    invalid: int | None = None
    none: int | None = None
    protocol: str = ""


@dataclass(frozen=True)
class Vote:
    slug: str
    first: str
    last: str
    english: str
    native: str
    files: tuple[str, ...]
    rounds: tuple[Round, ...]
    winner: str | None
    note: str = ""


VOTES_1993 = (
    Round(
        "1993-07-06",
        (("Aivars Jerumanis", 14), ("Gunārs Meierovics", 35), ("Guntis Ulmanis", 12)),
        (
            "Izsniegtas 79 vēlēšanu zīmes. 21 iznīcināta",
            "Par nederīgām atzītas 2 zīmes",
            "Visi kandidāti bija izsvītroti 16 zīmēs",
            "par Gunti Ulmani - 12 balsis, par Aivaru Jerumani - 14 balsis, par Gunāru Meierovicu - 35 balsis",
        ),
        issued=79,
        cast=79,
        invalid=2,
        none=16,
    ),
    Round(
        "1993-07-07",
        (("Aivars Jerumanis", 10), ("Guntis Ulmanis", 46)),
        (
            "Izdalītas tika 79 vēlēšanu zīmes",
            "Trīs zīmes bija nederīgas",
            "Abi kandidāti tika svītroti 20 vēlēšanu zīmēs",
            "par Aivaru Jerumani - 10, par Gunti Ulmani - 46",
        ),
        issued=79,
        cast=79,
        invalid=3,
        none=20,
    ),
    Round(
        "1993-07-07",
        (("Guntis Ulmanis", 53),),
        (
            "Vēlēšanu zīmes tika izdotas 79 deputātiem",
            "tika izņemtas 79 vēlēšanu zīmes",
            "pret - 26, par - 53",
            "par Valsts Prezidentu ir ievēlēts Guntis Ulmanis",
        ),
        issued=79,
        cast=79,
        none=26,
    ),
)
VOTE_1996 = (
    Round(
        "1996-06-18",
        (("Ilga Kreituse", 25), ("Imants Liepa", 14), ("Alfrēds Rubiks", 5), ("Guntis Ulmanis", 53)),
        (
            "Deputātiem izsniegtas 100 vēlēšanu zīmes",
            "tika atrastas 99 vēlēšanu zīmes",
            "Par derīgām tika atzītas 99",
            "Par nederīgām vēlēšanu zīmes atzītas netika",
            "par Ilgu Kreitusi nodotas 25 balsis",
            "par Imantu Lietu nodotas 14 balsis",
            "par Alfrēdu Rubiku nodotas 5 balsis",
            "par Gunti Ulmani nodotas 53 balsis",
        ),
        issued=100,
        cast=99,
        valid=99,
        invalid=0,
    ),
)
VOTES_1999 = (
    Round(
        "1999-06-17",
        (
            ("Anatolijs Gorbunovs", 21),
            ("Arnis Kalniņš", 14),
            ("Vaira Paegle", 24),
            ("Jānis Priedkalns", 17),
            ("Raimonds Pauls", 24),
        ),
        (
            "protokolu nr.23",
            "Deputātiem izsniegtas 100",
            "par derīgām atzītas 100 vēlēšanu zīmes",
            (
                "par Anatoliju Gorbunovu - 21 balss, par Arni Kalniņu - 14 balsis, par Vairu Paegli - 24 balsis, "
                "par Jāni Priedkalnu - 17 balsis, par Raimondu Paulu - 24 balsis"
            ),
        ),
        issued=100,
        cast=100,
        valid=100,
        protocol="Nr. 23",
    ),
    Round(
        "1999-06-17",
        (
            ("Anatolijs Gorbunovs", 21),
            ("Arnis Kalniņš", 14),
            ("Vaira Paegle", 24),
            ("Jānis Priedkalns", 17),
            ("Raimonds Pauls", 24),
        ),
        (
            "protokolu nr. 24",
            "No vēlēšanu kastes izņemtas 100 vēlēšanu zīmes. Visas derīgas",
            "izslēdzot kandidātu ar vismazāko balsu skaitu, tātad Arni Kalniņu",
        ),
        issued=100,
        cast=100,
        valid=100,
        protocol="Nr. 24",
    ),
    Round(
        "1999-06-17",
        (("Anatolijs Gorbunovs", 23), ("Vaira Paegle", 25), ("Jānis Priedkalns", 17), ("Raimonds Pauls", 32)),
        (
            "protokolu nr.25",
            "Balsošanā piedalījās un deputātiem izsniegtas 99 vēlēšanu zīmes",
            "No vēlēšanu kastes izņemtas 99 vēlēšanu zīmes, visas ir derīgas",
            "Pret visiem kandidātiem balsots divās vēlēšanu zīmēs",
            (
                "par Anatoliju Gorbunovu nodotas 23 balsis, par Vairu Paegli - 25 balsis, "
                "par Jāni Priedkalnu - 17 balsis, par Raimodu Paulu - 32 balsis"
            ),
        ),
        issued=99,
        cast=99,
        valid=99,
        none=2,
        protocol="Nr. 25",
    ),
    Round(
        "1999-06-17",
        (("Anatolijs Gorbunovs", 22), ("Vaira Paegle", 24), ("Raimonds Pauls", 23)),
        (
            "protokolu nr.26",
            "Balsošanā piedalījās un deputātiem izsniegtas 69 vēlēšanu zīmes",
            "kuras visas atzītas par derīgām",
            "par Anatoliju Gorbunovu nodotas 22 balsis, par Vairu Paegli - 24 balsis, par Raimondu Paulu - 23 balsis",
        ),
        issued=69,
        cast=69,
        valid=69,
        protocol="Nr. 26",
    ),
    Round(
        "1999-06-17",
        (("Vaira Paegle", 24), ("Raimonds Pauls", 33)),
        (
            "protokolu nr.27",
            "Balsošanā piedalījās un deputātiem izsniegtas 77 vēlēšanu zīmes",
            "Pret visiem kandidātiem balsots 20 vēlēšanu zīmēs",
            "par Vairu Paegli - 24 balsis, par Raimondu Paulu - 33 balsis",
        ),
        issued=77,
        cast=77,
        valid=77,
        none=20,
        protocol="Nr. 27",
    ),
)
VOTE_1999X = (
    Round(
        "1999-06-17",
        (("Vaira Vīķe-Freiberga", 53), ("Valdis Birkavs", 20), ("Ingrīda Ūdre", 9)),
        (
            "protokolu nr. 29",
            "Deputātiem tika izsniegtas 84 vēlēšanu zīmes",
            "No vēlēšanu kastes izņemtas 84 zīmes, un visas atzītas par derīgām",
            "Pret visiem kandidātiem balsots 2 vēlēšanu zīmēs",
            "par Vairu Vīķi-Freibergu - 53 balsis",
            "par Valdi Birkavu - 20 balsis",
            "par Ingrīdu Ūdri - 9 balsis",
        ),
        issued=84,
        cast=84,
        valid=84,
        none=2,
        protocol="Nr. 29",
    ),
)
VOTE_2003 = (
    Round(
        "2003-06-20",
        (("Vaira Vīķe-Freiberga", 88),),
        (
            "protokols nr.11",
            "Deputātiem izsniegtas 96 vēlēšanu zīmes",
            "atzītas 94 vēlēšanu zīmes",
            "Pret kandidātu balsots 6 vēlēšanu zīmēs",
            "Par Vairu Vīķi-Freibergu nobalsojuši 88 deputāti",
        ),
        issued=96,
        cast=94,
        valid=94,
        none=6,
        protocol="Nr. 11",
    ),
)
VOTE_2007 = (
    Round(
        "2007-05-31",
        (("Valdis Zatlers", 58), ("Aivars Endziņš", 39)),
        (
            "protokols Nr.13",
            "Deputātiem izsniegtas 98 vēlēšanu zīmes",
            "No vēlēšanu kastes izņemtas 98 vēlēšanu zīmes",
            "Par derīgām atzītas visas 98 vēlēšanu zīmes",
            "Par nederīgu nav atzīta neviena",
            "Pret visiem kandidātiem balsots vienā vēlēšanu zīmē",
            "Valdis Zatlers – 58",
            "Pret – 40 balsis",
            "Aivars Endziņš: par – 39, pret – 59",
        ),
        against=(40, 59),
        issued=98,
        cast=98,
        valid=98,
        invalid=0,
        none=1,
        protocol="Nr. 13",
    ),
)
VOTE_2011 = (
    Round(
        "2011-06-02",
        (("Valdis Zatlers", 43), ("Andris Bērziņš", 50)),
        (
            "protokols Nr.12",
            "Deputātiem izsniegtas 99 vēlēšanu zīmes",
            "No vēlēšanu kastes izņemtas 99 vēlēšanu zīmes",
            "Par derīgām atzītas 98 vēlēšanu zīmes",
            "Par nederīgu atzīta 1 vēlēšanu zīme",
            "Pret visiem kandidātiem balsots 5 vēlēšanu zīmēs",
            "Valdis Zatlers – „par” – 43, „pret” – 55",
            "Andris Bērziņš – „par” – 50, „pret” – 48",
        ),
        against=(55, 48),
        issued=99,
        cast=99,
        valid=98,
        invalid=1,
        none=5,
        protocol="Nr. 12",
    ),
    Round(
        "2011-06-02",
        (("Valdis Zatlers", 41), ("Andris Bērziņš", 53)),
        (
            "protokols Nr.13",
            "Par derīgām atzītas 97 vēlēšanu zīmes",
            "Par nederīgām atzītas 2 vēlēšanu zīmes",
            "Pret visiem kandidātiem balsots 3 vēlēšanu zīmēs",
            "Valdis Zatlers – „par” – 41, „pret” – 56",
            "Andris Bērziņš – „par” – 53, „pret” – 44",
        ),
        against=(56, 44),
        issued=99,
        cast=99,
        valid=97,
        invalid=2,
        none=3,
        protocol="Nr. 13",
    ),
)
VOTE_2015 = (
    Round(
        "2015-06-03",
        (("Mārtiņš Bondars", 7), ("Sergejs Dolgopolovs", 23), ("Raimonds Vējonis", 34), ("Egils Levits", 24)),
        (
            "protokols Nr.8",
            "Deputātiem izsniegtas 99 vēlēšanu zīmes",
            "Par derīgām atzītas 93 vēlēšanu zīmes",
            "Par nederīgām atzītas 6 vēlēšanu zīmes",
            "Pret visiem kandidātiem balsots 5 vēlēšanu zīmēs",
            "Mārtiņš Bondars – par – 7, pret – 86",
            "Sergejs Dolgopolovs – par – 23, pret – 70",
            "Raimonds Vējonis – par – 34, pret – 59",
            "Egils Levits – par – 24, pret – 69",
        ),
        against=(86, 70, 59, 69),
        issued=99,
        cast=99,
        valid=93,
        invalid=6,
        none=5,
        protocol="Nr. 8",
    ),
    Round(
        "2015-06-03",
        (("Mārtiņš Bondars", 7), ("Sergejs Dolgopolovs", 24), ("Raimonds Vējonis", 34), ("Egils Levits", 25)),
        (
            "protokols Nr.9",
            "Par derīgām atzītas 96 vēlēšanu zīmes",
            "Par nederīgām atzītas 3 vēlēšanu zīmes",
            "Pret visiem kandidātiem balsots 6 vēlēšanu zīmēs",
            "Mārtiņš Bondars – par – 7, pret – 89",
            "Sergejs Dolgopolovs – par – 24, pret – 72",
            "Raimonds Vējonis – par – 34, pret – 62",
            "Egils Levits – par – 25, pret – 71",
        ),
        against=(89, 72, 62, 71),
        issued=99,
        cast=99,
        valid=96,
        invalid=3,
        none=6,
        protocol="Nr. 9",
    ),
    Round(
        "2015-06-03",
        (("Sergejs Dolgopolovs", 23), ("Raimonds Vējonis", 35), ("Egils Levits", 26)),
        (
            "protokols Nr.10",
            "No vēlēšanu kastes izņemtas 98 vēlēšanu zīmes",
            "Par derīgām atzītas 96 vēlēšanu zīmes",
            "Par nederīgām atzītas 2 vēlēšanu zīmes",
            "Pret visiem kandidātiem balsots 12 vēlēšanu zīmēs",
            "Sergejs Dolgopolovs – par – 23, pret – 73",
            "Raimonds Vējonis – par – 35, pret – 61",
            "Egils Levits – par – 26, pret – 70",
        ),
        against=(73, 61, 70),
        issued=99,
        cast=98,
        valid=96,
        invalid=2,
        none=12,
        protocol="Nr. 10",
    ),
    Round(
        "2015-06-03",
        (("Raimonds Vējonis", 46), ("Egils Levits", 26)),
        (
            "protokols Nr.11",
            "No vēlēšanu kastes izņemtas 99 vēlēšanu zīmes",
            "Par derīgām atzītas 98 vēlēšanu zīmes",
            "Par nederīgu atzīta 1 vēlēšanu zīme",
            "Pret visiem kandidātiem balsots 26 vēlēšanu zīmēs",
            "Raimonds Vējonis – par – 46, pret – 52",
            "Egils Levits – par – 26, pret – 72",
        ),
        against=(52, 72),
        issued=99,
        cast=99,
        valid=98,
        invalid=1,
        none=26,
        protocol="Nr. 11",
    ),
    Round(
        "2015-06-03",
        (("Raimonds Vējonis", 55),),
        (
            "protokols Nr.12",
            "No vēlēšanu kastes izņemtas 98 vēlēšanu zīmes",
            "Par derīgām atzītas 97 vēlēšanu zīmes",
            "Par nederīgu atzīta 1 vēlēšanu zīme",
            "Pret visiem kandidātiem balsots 42 vēlēšanu zīmēs",
            "par – 55, pret – 42",
        ),
        against=(42,),
        issued=99,
        cast=98,
        valid=97,
        invalid=1,
        none=42,
        protocol="Nr. 12",
    ),
)
VOTE_2019 = (
    Round(
        "2019-05-29",
        (("Egils Levits", 61), ("Juris Jansons", 8), ("Didzis Šmits", 24)),
        (
            "protokols Nr. 9",
            "Deputātiem izsniegtas 95 vēlēšanu zīmes",
            "No vēlēšanu kastes izņemtas 95 vēlēšanu zīmes",
            "Par derīgām atzītas 93 vēlēšanu zīmes",
            "Par nederīgām atzītas 2 vēlēšanu zīmes",
            "Pret visiem kandidātiem nav balsots nevienu reizi",
            "Egils Levits – par – 61",
            "pret – 32",
            "Juris Jansons – par – 8, pret – 85",
            "Didzis Šmits – par – 24, pret – 69",
        ),
        against=(32, 85, 69),
        issued=95,
        cast=95,
        valid=93,
        invalid=2,
        none=0,
        protocol="Nr. 9",
    ),
)
VOTE_2023 = (
    Round(
        "2023-05-31",
        (("Uldis Pīlēns", 25), ("Edgars Rinkēvičs", 42), ("Elīna Pinto", 10)),
        (
            "protokols Nr. 8",
            "Deputātiem izsniegtas 87 vēlēšanu zīmes",
            "No vēlēšanu kastes izņemtas 87 vēlēšanu zīmes",
            "Par derīgām atzītas 87 vēlēšanu zīmes",
            "Par nederīgām atzītu nav",
            "Pret visiem kandidātiem balsots 10 vēlēšanu zīmēs",
            "Uldis Pīlēns: par – 25, pret – 62",
            "Edgars Rinkēvičs: par – 42, pret – 45",
            "Elīna Pinto: par – 10, pret – 77",
        ),
        against=(62, 45, 77),
        issued=87,
        cast=87,
        valid=87,
        invalid=0,
        none=10,
        protocol="Nr. 8",
    ),
    Round(
        "2023-05-31",
        (("Uldis Pīlēns", 25), ("Edgars Rinkēvičs", 42), ("Elīna Pinto", 10)),
        (
            "protokols Nr. 9, aizvadot vēlēšanu otro kārtu",
            "otrajā vēlēšanu kārtā neviens no Valsts prezidenta amata kandidātiem nav ievēlēts",
        ),
        against=(62, 45, 77),
        issued=87,
        cast=87,
        valid=87,
        invalid=0,
        none=10,
        protocol="Nr. 9",
    ),
    Round(
        "2023-05-31",
        (("Uldis Pīlēns", 25), ("Edgars Rinkēvičs", 52)),
        (
            "protokols Nr. 10",
            "Edgars Rinkēvičs: par – 52, pret – 35",
            "par vienpadsmito Latvijas Republikas Valsts prezidentu ievēlēts Edgars Rinkēvičs",
        ),
        against=(62, 35),
        issued=87,
        cast=87,
        valid=87,
        invalid=0,
        none=10,
        protocol="Nr. 10",
    ),
)


def _vote(
    slug: str, year: int, files: tuple[str, ...], rounds: tuple[Round, ...], winner: str | None, note: str = ""
) -> Vote:
    return Vote(
        slug,
        rounds[0].day,
        rounds[-1].day,
        f"{year} Latvian presidential election",
        f"{year}. gada Valsts prezidenta vēlēšanas Saeimā",
        files,
        rounds,
        winner,
        note,
    )


ELECTIONS = (
    _vote(
        "1993-07-06",
        1993,
        ("1993",),
        VOTES_1993,
        "Guntis Ulmanis",
        "The first presidential election of the restored republic, held in the first sitting of the 5th Saeima. The "
        "first round was on 6 July and the next two on 7 July. Gunārs Meierovics withdrew his candidacy before the "
        "second round, so it and the third round list fewer names. In the third round Guntis Ulmanis was the only "
        "candidate, and a ballot with his name struck was a vote against him.",
    ),
    _vote("1996-06-18", 1996, ("1996",), VOTE_1996, "Guntis Ulmanis"),
    _vote(
        "1999-06-17",
        1999,
        ("1999",),
        VOTES_1999,
        None,
        "No candidate reached 51 votes in five rounds. The 17 deputies of the faction Tēvzemei un Brīvībai/LNNK stayed "
        "out of the votes after the third round, and Raimonds Pauls, the last candidate left, withdrew, so the "
        "Saeima declared the item closed without a result and held new elections that evening.",
    ),
    _vote(
        "1999-06-17-2",
        1999,
        ("1999x",),
        VOTE_1999X,
        "Vaira Vīķe-Freiberga",
        "New elections held at an extraordinary sitting the evening of the failed election, with new candidates. The "
        "counters' first protocol (Nr. 28) gave Ingrīda Ūdre 12, Valdis Birkavs 21, and Vaira Vīķe-Freiberga 50 "
        "votes of 84 valid ballots; one ballot with Vīķe-Freiberga's name partly struck could not be read as a vote "
        "for or against her, the counters split three to three on it twice, and the Presidium and the Council of "
        "Factions ordered the first vote repeated. The repeated vote is stored here as round 1.",
    ),
    _vote(
        "2003-06-20",
        2003,
        ("2003",),
        VOTE_2003,
        "Vaira Vīķe-Freiberga",
        "Vaira Vīķe-Freiberga was the only candidate, so a ballot with her name struck was a vote against her. Two "
        "sealed envelopes held no ballot, which is why 94 ballots were counted of the 96 issued.",
    ),
    _vote("2007-05-31", 2007, ("2007",), VOTE_2007, "Valdis Zatlers"),
    _vote("2011-06-02", 2011, ("2011",), VOTE_2011, "Andris Bērziņš"),
    _vote("2015-06-03", 2015, ("2015", "2015s"), VOTE_2015, "Raimonds Vējonis"),
    _vote(
        "2019-05-29",
        2019,
        ("2019",),
        VOTE_2019,
        "Egils Levits",
        "The first presidential vote held openly: deputies marked each candidate for or against rather than voting in "
        "secret.",
    ),
    _vote("2023-05-31", 2023, ("2023", "2023s"), VOTE_2023, "Edgars Rinkēvičs"),
)
FILES = {**URLS, "2015s": STATEMENTS["2015"], "2023s": STATEMENTS["2023"]}
OPEN_FROM = date(2019, 5, 29)

METHOD = (
    "Each round is one vote of the Saeima's 100 deputies in a sitting, read out by the vote counters from their "
    "protocol and printed in the sitting's stenogram. Before 2019 the ballot was secret: every deputy received a "
    "ballot listing all candidates and struck out the names of those voted against, and a ballot with one name left "
    "was a vote for that candidate. A ballot with every name struck was a vote against all candidates. From 2019 the "
    "vote is open and the protocol also prints the votes against each candidate. A candidate needs at least 51 votes "
    "(Satversme art. 36). If none has them, the candidates vote again, and from the third round the candidate with the "
    "fewest votes is dropped (Saeima Rules of Procedure art. 26). Votes are the votes for each candidate; ballots "
    "issued are the ballots handed to deputies, and ballots cast are the ballots taken from the box. A round's "
    "figures are stored only where its protocol prints them."
)


def lines(vote: Vote, round: Round) -> str:
    return ", ".join(f"{name}: {votes}" for name, votes in round.candidates)


def verify(vote: Vote) -> None:
    for name in vote.files:
        if name not in URLS:
            continue
        found = text(name)
        for round in vote.rounds:
            for phrase in round.proof:
                if phrase not in found:
                    raise ValueError(f"{vote.slug} {round.protocol or round.day}: {phrase!r} is not in the stenogram")
            printed = sum(votes for _, votes in round.candidates) + (round.none or 0)
            if round.valid is not None and round.valid != printed and vote.slug != "1996-06-18":
                raise ValueError(f"{vote.slug} {round.protocol}: {printed} votes against {round.valid} valid ballots")
            if round.against:
                if len(round.against) != len(round.candidates):
                    raise ValueError(f"{vote.slug} {round.protocol}: against counts do not match the candidates")
                for (_, votes), against in zip(round.candidates, round.against, strict=True):
                    if round.valid is not None and votes + against != round.valid:
                        raise ValueError(f"{vote.slug} {round.protocol}: {votes} for and {against} against")


def person(name: str) -> str:
    return key("person", name)


def geo() -> list[str]:
    return ["lv.cvk/geo", "lv.cvk/registry"]


def registry() -> Package:
    p = Package(
        "lv.saeima/registry",
        SOURCE,
        "The President of Latvia, the rule for electing the President, and the Saeima's stenograms as a source",
        depends=geo(),
    )
    p.external |= {LATVIA}
    p.add(
        "source",
        id=SRC,
        kind="official_body",
        publisher="Latvijas Republikas Saeima",
        title="Saeimas sēžu stenogrammas (Saeima sitting stenograms)",
        url="https://www.saeima.lv/lv/likumdosana/saeimas-sedes/saeimas-sezu-stenogrammas/",
        license=UNLICENSED,
        attribution="Latvijas Republikas Saeima",
    )
    for url in (*URLS.values(), *STATEMENTS.values()):
        p.artifact(raw.cached(SOURCE, url), SRC)
    for name in sorted({name for vote in ELECTIONS for r in vote.rounds for name, _ in r.candidates}):
        p.add("person", id=person(name))
        p.name(person(name), name, "lv", "official")
    p.add("office", id=PRESIDENCY, polity_id=LATVIA, kind="head_of_state")
    p.name(PRESIDENCY, "President of Latvia", "en", "official")
    p.name(PRESIDENCY, "Latvijas Valsts prezidents", "lv", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=LATVIA,
        valid_from=date(1993, 7, 6),
        valid_to=None,
        rules={
            "formula": "repeated_ballot",
            "majorities": [{"fromRound": 1, "share": 0.5, "strict": True}],
            "required": 51,
        },
        rules_version=1,
    )
    p.name(SYSTEM, "Election of the President by the Saeima", "en", "official")
    p.name(SYSTEM, "Valsts prezidenta vēlēšanas Saeimā", "lv", "native", False)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=(
            "The Saeima elects the President by an absolute majority of its 100 members, at least 51 votes "
            "(Satversme art. 36), in rounds until a candidate has them. If the last round gives no result, new "
            "elections are held with new or old candidates (Law on the Election of the President, art. 9)."
        ),
        lang="en",
    )
    return p


def build(vote: Vote) -> Package:
    verify(vote)
    p = Package(
        f"lv.saeima/{vote.slug}",
        SOURCE,
        f"The {vote.english}: each round's votes for each candidate, from the Saeima's stenogram",
        depends=["lv.saeima/registry", *geo()],
    )
    p.external |= {LATVIA, COUNTRY}
    for name in vote.files:
        p.uses(raw.cached(SOURCE, FILES[name]))
    if vote.slug == "1996-06-18":
        p.accept(
            "accounting.votes_valid",
            "the vote counters' protocol prints 99 valid ballots but only 97 votes for the four candidates (25, 14, 5, "
            "and 53); the two ballots between them are not accounted for in the protocol, and both figures are stored "
            "as printed",
        )
    first, last = date.fromisoformat(vote.first), date.fromisoformat(vote.last)
    election = key("election", vote.slug)
    where = f"lv/{vote.slug}-president"
    p.add("election", id=election, polity_id=LATVIA, kind="indirect", date_from=first, date_to=last, status="held")
    p.name(election, vote.english, "en", "official")
    p.name(election, vote.native, "lv", "native", False)
    p.add("slug", path=where, entity_id=election, is_current=True)
    if vote.note:
        p.add("note", id=key("note", election), entity_id=election, kind="caveat", body_md=vote.note, lang="en")
    unit = key("unit", vote.slug)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=LATVIA,
        scheme_code="lv.valsts",
        code=f"{vote.slug}:saeima",
        area_relation="equals",
        area_id=COUNTRY,
        kind="aggregate",
    )
    p.name(unit, "Saeima in session", "en", "official")
    p.name(unit, "Saeimas sēde", "lv", "native", False)
    rows: list[dict[str, object]] = []
    previous: str | None = None
    page = vote.files[0]
    for number, found in enumerate(vote.rounds, start=1):
        many = len(vote.rounds) > 1
        contest = key("contest", vote.slug, number)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="office",
            office_id=PRESIDENCY,
            body_id=SAEIMA,
            district_area_id=COUNTRY,
            magnitude=1,
            round_number=number,
            previous_round_id=previous,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{vote.english}, round {number}" if many else vote.english, "en", "official")
        p.name(contest, f"{vote.native}, {number}. kārta" if many else vote.native, "lv", "native", False)
        p.add("slug", path=f"{where}/round-{number}" if many else f"{where}/vote", entity_id=contest, is_current=True)
        previous = contest
        result_set = key("result_set", vote.slug, number)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SRC,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        p.add(
            "citation",
            id=key("citation", result_set),
            entity_id=result_set,
            source_id=SRC,
            locator={
                "url": FILES[page],
                "sitting": found.day,
                **({"protocol": found.protocol} if found.protocol else {}),
            },
        )
        values: list[tuple[int, str | None, int | None]] = [
            (VOTERS, None, found.issued),
            (CAST, None, found.cast),
            (VALID, None, found.valid),
            (INVALID, None, found.invalid),
        ]
        options: dict[str, str] = {}
        for index, (name, votes) in enumerate(found.candidates, start=1):
            who = person(name)
            option = options[name] = key("option", vote.slug, number, name)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=index,
                ballot_label=name,
            )
            p.add(
                "candidacy",
                id=key("candidacy", vote.slug, number, name),
                person_id=who,
                option_id=option,
                role="head",
                ballot_name=name,
            )
            values.append((VOTES, option, votes))
        if found.none is not None:
            none = key("option", vote.slug, number, "none")
            p.add(
                "contest_option",
                id=none,
                contest_id=contest,
                kind="none_of_the_above",
                ballot_position=len(found.candidates) + 1,
                ballot_label="Pret visiem kandidātiem",
            )
            values.append((VOTES, none, found.none))
        if found.against:
            text_against = ", ".join(
                f"{name} {against}" for (name, _), against in zip(found.candidates, found.against, strict=True)
            )
            p.add(
                "note",
                id=key("note", result_set, "against"),
                entity_id=result_set,
                kind="caveat",
                body_md=f"Votes against each candidate, as the protocol prints them: {text_against}.",
                lang="en",
            )
        if number == len(vote.rounds) and vote.winner is not None:
            p.add("outcome", result_set_id=result_set, option_id=options[vote.winner], kind="elected")
        for measure, option, value in values:
            if value is None:
                continue
            p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=value)
            rows.append(
                {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": option,
                    "channel": 0,
                    "measure": measure,
                    "count_round": 0,
                    "flags": 0,
                    "value": value,
                }
            )
    for row in rows:
        p.add("result_value", **row)
    return p


def packages() -> list[str]:
    return ["lv.saeima/registry", *(f"lv.saeima/{vote.slug}" for vote in ELECTIONS)]


def parse() -> None:
    registry().write()
    for vote in ELECTIONS:
        build(vote).write()


def emit() -> None:
    package.run("load", packages())
