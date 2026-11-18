"""Montenegrin parliamentary and presidential results of 1997-2003, from the Republic Election Commission's decisions
in the official gazette (Službeni list Republike Crne Gore), which the gazette's site serves as page images.

The commission printed national totals only, so each result set holds one row for the whole republic. Every figure
was read from the page image, checked against the commission's own sums (valid ballots against the option votes,
ballots used against valid plus invalid, voters at the polling stations plus voters by letter), and carries the page
and a box around the line it stands on."""

import base64
import io
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import uuid5

from PIL import Image

from ea import package, raw
from ea.package import Package
from ea.sources.me import dik
from ea.store import DATA

SOURCE = "me.rik"
SITE = "https://www.sluzbenilist.me"
MONTENEGRO = dik.MONTENEGRO
VOTES, REGISTERED, CAST, VALID, INVALID, VOTERS = (
    dik.VOTES,
    dik.REGISTERED,
    dik.CAST,
    dik.VALID,
    dik.INVALID,
    dik.VOTERS,
)
DIGITIZED = 8
DEPENDS = ["me.dik/registry", "me.rik/registry"]

DOCUMENTS = {
    "par2001": 299229,
    "par2002": 298267,
    "pres1997": 295469,
    "pres1997-2": 294718,
    "pres1997-call": 295038,
    "pres2002": 300037,
    "pres2003-02": 300530,
    "pres2003-call": 301813,
    "pres2003": 301839,
    "pres1997-lists": 295039,
    "pres1997-lists-2": 300542,
    "pres2002-lists": 326810,
    "pres2002-repeat": 327752,
    "pres2003-lists": 308214,
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


GAZETTE = key("source", "gazette")


def page_url(document: int, page: int) -> str:
    return f"{SITE}/propisi/{document}?page={page}"


def _pages(path: Path) -> int:
    found = re.search(r"totalPages:\s*(\d+)", path.read_text(errors="replace"))
    if found is None:
        raise ValueError(f"{path} is not a gazette page")
    return int(found.group(1))


def fetch() -> None:
    for document in DOCUMENTS.values():
        first = raw.fetch(SOURCE, page_url(document, 1), suffix=".html", check=_pages)
        for page in range(2, _pages(first.path) + 1):
            raw.fetch(SOURCE, page_url(document, page), suffix=".html", check=_pages)


def image(document: int, page: int) -> bytes:
    """The scan of one gazette page, which the page's HTML embeds as a JPEG."""
    text = raw.cached(SOURCE, page_url(document, page)).path.read_text()
    found = re.search(r"data:image/jpeg;base64,([A-Za-z0-9+/=]+)", text)
    if found is None:
        raise ValueError(f"no page image in {document} page {page}")
    return base64.b64decode(found.group(1))


def issue(document: str) -> str:
    """The gazette issue a document appeared in, as the page's own header prints it."""
    text = raw.cached(SOURCE, page_url(DOCUMENTS[document], 1)).path.read_text()
    found = re.search(r"Službeni list Republike Crne Gore,\s*broj\s*(\d+/\d{4})", text)
    if found is None:
        raise ValueError(f"no issue number for {document}")
    return found.group(1)


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.replace("đ", "dj").replace("Đ", "Dj"))
    return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in text if not unicodedata.combining(c)).lower()).strip()


# A figure as printed: its value, the page of the document it is on, the vertical middle of its line, and the
# horizontal extent of the number, in pixels of the page image.
@dataclass(frozen=True)
class Fig:
    value: int
    page: int
    y: float
    x0: float
    x1: float


@dataclass(frozen=True)
class Option:
    label: str
    votes: Fig | None
    seats: int = 0
    name: str = ""  # a candidate's name in Latin script, when the label is in Cyrillic or carries a title


@dataclass(frozen=True)
class Vote:
    day: str
    kind: str  # parliament or president
    document: str
    english: str
    native: str
    acc: dict[int, Fig]
    options: list[Option]
    cites: tuple[str, ...] = ()  # the other documents of the vote: its calling, its candidate list, its repeat
    round_number: int = 1
    previous: str | None = None
    rerun_of: str | None = None
    annulled: str | None = None
    elected: str | None = None
    advanced: tuple[str, ...] = ()
    seats: int = 0
    note: str = ""


def _f(value: int, page: int, y: float, x0: float, x1: float) -> Fig:
    return Fig(value, page, y, x0, x1)


PARLIAMENT_NOTE = (
    "As the commission's decision states, the whole republic was one constituency under article 12 of the Law on the "
    "Election of Councilors and Members of Parliament, and {special} of the {seats} members were elected at the "
    "polling stations a decision of the Parliament designated (Sl. list RCG {designation}); the seats were allocated "
    "under article 95 of that law. The commission printed only the republic-wide votes of each list and the seats "
    "each won, so no electoral system is attached here: the seats are stored as declared."
)
PRESIDENT_NOTE = (
    "Under the Law on the Election of the President of the Republic (Sl. list RCG 49/92), a candidate needed a "
    "majority of the votes of those who voted, and the election was valid only if more than half the registered "
    "voters took part (art. 16); otherwise the two leading candidates met again, and a vote that stayed below half "
    "the registered voters was void and the whole procedure was repeated."
)
METHOD = (
    "The national figures the Republic Election Commission declared in its decision on the results, published in "
    "the Službeni list Republike Crne Gore {gazette} and read from the gazette's page images. {measures}"
)
PARLIAMENT_MEASURES = (
    "Registered voters are the voters on the roll, voters are those who voted at the polling stations or by letter, "
    "ballots cast are the ballots used, and valid and invalid are the valid and invalid ballots."
)
PRESIDENT_MEASURES = (
    "Registered voters are the voters on the roll and voters are those who voted; the decision prints no count of "
    "valid or invalid ballots, so the candidates' votes are stored without one."
)

VOTES_DATA: list[Vote] = [
    Vote(
        day="1997-10-05",
        kind="president",
        document="pres1997",
        english="1997 Montenegrin presidential election",
        native="Izbori za predsjednika Republike Crne Gore 1997",
        acc={REGISTERED: _f(461738, 1, 381, 380, 435), VOTERS: _f(311118, 1, 397, 235, 280)},
        cites=("pres1997-call", "pres1997-lists"),
        options=[
            Option("Др Новица Станић", _f(5109, 1, 514.6, 546, 580), name="Novica Stanić"),
            Option("Мило Ђукановић", _f(145348, 1, 530.6, 530, 574), name="Milo Đukanović"),
            Option("Аћим Вишњић", _f(4635, 1, 546.6, 547, 580), name="Aćim Višnjić"),
            Option("Мр Момир Булатовић", _f(147615, 1, 563.6, 530, 574), name="Momir Bulatović"),
            Option("Милан Радуловић", _f(620, 1, 579.6, 563, 580), name="Milan Radulović"),
            Option("Др Драган Хајдуковић", _f(1998, 1, 596, 547, 580), name="Dragan Hajduković"),
            Option("Проф. др Слободан Вујачић", _f(383, 1, 612, 572, 590), name="Slobodan Vujačić"),
            Option("Проф. др Новица Војиновић", _f(785, 1, 629, 555, 580), name="Novica Vojinović"),
        ],
        advanced=("Momir Bulatović", "Milo Đukanović"),
        note=(
            "No candidate won the majority the law required, so the commission sent Momir Bulatović and Milo "
            "Đukanović, both nominated by the Democratic Party of Socialists, to a second vote on 19 October 1997. "
            "The decision gives 67.38 percent turnout."
        ),
    ),
    Vote(
        day="1997-10-19",
        kind="president",
        document="pres1997-2",
        english="1997 Montenegrin presidential election, second round",
        native="Izbori za predsjednika Republike Crne Gore 1997, ponovno glasanje",
        acc={REGISTERED: _f(470491, 1, 296, 383, 430), VOTERS: _f(347688, 1, 313.5, 230, 278)},
        options=[
            Option("Мр Момир Булатовић", _f(169257, 1, 380.6, 235, 275), name="Momir Bulatović"),
            Option("Мило Ђукановић", _f(174745, 1, 413.6, 87, 128), name="Milo Đukanović"),
        ],
        cites=("pres1997-lists-2",),
        round_number=2,
        previous="1997-10-05",
        elected="Milo Đukanović",
        note=("The decision gives 73.9 percent turnout, and 49.2 and 50.8 percent of the votes to the two candidates."),
    ),
    Vote(
        day="2002-12-22",
        kind="president",
        document="pres2002-lists",
        cites=("pres2002", "pres2002-repeat"),
        english="December 2002 Montenegrin presidential election",
        native="Izbori za predsjednika Republike Crne Gore 2002",
        acc={},
        options=[
            Option("Obrad Marković", None),
            Option("Milan-Milo Radulović", None),
            Option("Dragan Hajduković", None),
            Option("Filip Vujanović", None),
            Option("Milan Šparović", None),
            Option("Milivoje Bakić", None),
            Option("Ilija Darmanović", None),
            Option("Mihailo-Milo Marković", None),
            Option("Jovan Pejović", None),
            Option("Aleksandar Vasiljević", None),
            Option("Đorđije Milić", None),
        ],
        annulled=(
            "Fewer than half the registered voters took part, so the vote was void under article 16 of the Law on "
            "the Election of the President of the Republic, and the Republic Election Commission called a repeat "
            "vote among the same candidates for 9 February 2003 (decision 01-476 of 30 December 2002). The gazette "
            "prints the candidates but not the counts."
        ),
    ),
    Vote(
        day="2003-02-09",
        kind="president",
        document="pres2003-02",
        english="February 2003 Montenegrin presidential election (repeat vote)",
        native="Ponovno glasanje za izbor predsjednika Republike Crne Gore 2003",
        acc={REGISTERED: _f(456981, 1, 304.7, 120, 168), VOTERS: _f(213142, 1, 238.2, 355, 403)},
        options=[
            Option("Obrad Marković", None),
            Option("Milan-Milo Radulović", None),
            Option("Dragan Hajduković", None),
            Option("Filip Vujanović", None),
            Option("Milan Šparović", None),
            Option("Milivoje Bakić", None),
            Option("Ilija Darmanović", None),
            Option("Mihailo-Milo Marković", None),
            Option("Jovan Pejović", None),
            Option("Aleksandar Vasiljević", None),
            Option("Đorđije Milić", None),
        ],
        cites=("pres2002-repeat",),
        rerun_of="2002-12-22",
        annulled=(
            "213,142 voters, 47.5 percent of the roll at the 1,019 polling stations that voted, took part; 83 "
            "polling stations with 8,050 voters did not vote, and even with them turnout could not reach half of "
            "the 456,981 registered voters, so the Republic Election Commission declared the whole procedure to be "
            "repeated (decision 01-81 of 14 February 2003). The decision prints no candidate's votes."
        ),
    ),
    Vote(
        day="2003-05-11",
        kind="president",
        document="pres2003",
        english="May 2003 Montenegrin presidential election",
        native="Izbori za predsjednika Republike Crne Gore 2003",
        acc={
            REGISTERED: _f(458399, 1, 402.8, 572, 613),
            VOTERS: _f(221497, 1, 563.3, 237, 285),
            CAST: _f(221497, 1, 611.3, 177, 225),
            INVALID: _f(4502, 1, 627.3, 177, 210),
            VALID: _f(216995, 1, 645.8, 177, 225),
        },
        options=[
            Option("Dr Dragan Hajduković", _f(9242, 1, 725.8, 322, 358), name="Dragan Hajduković"),
            Option("Filip Vujanović", _f(139620, 1, 742.3, 260, 305)),
            Option("Miodrag Živković", _f(68133, 1, 758.8, 281, 325)),
        ],
        rerun_of="2003-02-09",
        cites=("pres2003-call", "pres2003-lists"),
        elected="Filip Vujanović",
        note=(
            "Held under the new Law on the Election of the President of the Republic (Sl. list RCG 11/2003), which "
            "dropped the rule that more than half the registered voters must take part."
        ),
    ),
    Vote(
        day="2001-04-22",
        kind="parliament",
        document="par2001",
        english="2001 Montenegrin parliamentary election",
        native="Izbori za poslanike u Skupštinu Republike Crne Gore 2001",
        acc={
            REGISTERED: _f(447673, 1, 359.5, 375, 425),
            VOTERS: _f(366152, 1, 393.5, 217, 256),
            CAST: _f(366152, 1, 424.5, 159, 196),
            INVALID: _f(2748, 1, 435.5, 159, 181),
            VALID: _f(363404, 1, 446, 159, 196),
        },
        options=[
            Option("Liberalni savez Crne Gore - Liberali, nego što!", _f(28746, 1, 478.8, 85, 123), 6),
            Option("Srpska radikalna stranka dr Vojislav Šešelj", _f(4275, 1, 489.5, 605, 634)),
            Option('"LDP glas za Crnu Goru"', _f(354, 1, 509.5, 332, 350)),
            Option("Osman Redža Partija demokratskog prosperiteta", _f(1572, 1, 531.8, 85, 110)),
            Option("Demokratski savez u Crnoj Gori - Mehmet Bardhi", _f(3570, 1, 553.3, 85, 111), 1),
            Option("Bošnjačko-muslimanska demokratska koalicija u Crnoj Gori", _f(4046, 1, 573.8, 85, 111)),
            Option(
                "Stranka zaštite štednih uloga i socijalne sigurnosti građana Crne Gore",
                _f(199, 1, 584.3, 617, 634),
            ),
            Option("Zajedno za Jugoslaviju", _f(148513, 1, 605.3, 310, 348), 33),
            Option("Dr Novak Kilibarda Narodna sloga Crne Gore", _f(268, 1, 627.3, 85, 103)),
            Option("Demokratska unija Albanaca", _f(4232, 1, 636.5, 322, 353), 1),
            Option("Jugoslovenska levica u Crnoj Gori", _f(190, 1, 657.6, 475, 495)),
            Option("Stranka deviznih štediša Crne Gore", _f(639, 1, 678.6, 407, 428)),
            Option('"Pobjeda je Crne Gore - Demokratska koalicija Milo Đukanović"', _f(153946, 1, 689.6, 595, 634), 36),
            Option(
                "Komunističke i radničke partije - za Jugoslaviju i vlast radničke klase", _f(1640, 1, 711.1, 534, 565)
            ),
            Option("Stranka prirodnoga zakona Crne Gore", _f(512, 1, 731.6, 402, 423)),
            Option('"Narodna socijalistička stranka Crne Gore - Momir Bulatović"', _f(10702, 1, 742.6, 597, 634)),
        ],
        seats=77,
        note=PARLIAMENT_NOTE.format(special=5, seats=77, designation="5/98"),
    ),
    Vote(
        day="2002-10-20",
        kind="parliament",
        document="par2002",
        english="2002 Montenegrin parliamentary election",
        native="Izbori za poslanike u Skupštinu Republike Crne Gore 2002",
        acc={
            REGISTERED: _f(455791, 1, 467.5, 388, 428),
            VOTERS: _f(353102, 1, 514, 230, 276),
            CAST: _f(353102, 1, 563.6, 165, 211),
            INVALID: _f(4704, 1, 580, 165, 198),
            VALID: _f(348398, 1, 596.6, 165, 210),
        },
        options=[
            Option('"Patriotska koalicija za Jugoslaviju" (NSS-SRS-JUL-SDŠ CG)', _f(9920, 1, 645, 502, 533)),
            Option("Zajedno za promjene - SNP - SNS - NS", _f(133900, 1, 661.5, 487, 533), 30),
            Option(
                "Stranka zaštite štednih uloga i socijalne sigurnosti građana Crne Gore", _f(851, 1, 693.5, 547, 568)
            ),
            Option(
                "Socijalistička partija Jugoslavije sa komunistima - Za očuvanje Jugoslavije",
                _f(2242, 1, 726, 507, 538),
            ),
            Option("Crna Gora može - Liberalni savez Crne Gore!", _f(20365, 1, 742.5, 495, 530), 4),
            Option("Bošnjačka demokratska koalicija - Harun Hadžić", _f(2446, 1, 759, 495, 525)),
            Option("Bošnjačka koalicija", _f(2173, 1, 775, 500, 530)),
            Option('Demokratska koalicija "Albanci zajedno"', _f(8498, 1, 791, 485, 515), 2),
            Option("Srpska radikalna stranka", _f(837, 1, 807, 502, 523)),
            Option('"Demokratska lista za evropsku Crnu Goru - Milo Đukanović"', _f(167166, 2, 108, 482, 528), 39),
        ],
        seats=75,
        note=PARLIAMENT_NOTE.format(special=4, seats=75, designation="48/2002"),
    ),
]
VOTES_BY_DAY = {vote.day: vote for vote in VOTES_DATA}


def person_name(option: Option) -> str:
    return option.name or option.label


def person_id(name: str) -> str:
    return key("person", fold(name))


def party_id(label: str) -> str:
    return key("party", fold(label))


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{vote.day}" for vote in VOTES_DATA)]


def parse() -> None:
    registry().write()
    for vote in VOTES_DATA:
        build(vote).write()


def emit() -> None:
    package.run("load", packages())


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Montenegrin parties and presidential candidates of 1997-2003, from the Republic Election Commission's "
        "decisions in the official gazette",
    )
    p.external.add(MONTENEGRO)
    p.add(
        "source",
        id=GAZETTE,
        kind="official_body",
        publisher="Republička izborna komisija Republike Crne Gore",
        title="Decisions and reports of the Republic Election Commission in the Službeni list Republike Crne Gore",
        url=f"{SITE}/propisi",
        license="Official publication; reuse terms are not stated",
        attribution="Republička izborna komisija; Službeni list Crne Gore",
    )
    parties: dict[str, str] = {}
    people: dict[str, Option] = {}
    for vote in VOTES_DATA:
        for option in vote.options:
            if vote.kind == "parliament":
                parties.setdefault(party_id(option.label), option.label.strip('"'))
            else:
                people.setdefault(person_id(person_name(option)), option)
    for id, text in sorted(parties.items(), key=lambda item: item[1]):
        bloc = re.search(r"koalicij|zajedno|lista|savez u", text, re.IGNORECASE)
        p.add("party", id=id, kind="bloc" if bloc else "party", polity_id=MONTENEGRO)
        p.name(id, text, "sr", "official")
    for id, option in sorted(people.items(), key=lambda item: person_name(item[1])):
        p.add("person", id=id)
        p.name(id, person_name(option), "sr", "official")
        if re.search(r"[Ѐ-ӿ]", option.label):
            p.name(id, re.sub(r"^(?:(?:Проф|Др|Мр|др)\.?\s+)+", "", option.label), "sr", "ballot", False)
    return p


def _pages_of(p: Package, document: str) -> dict[int, tuple[str, int, int]]:
    """Cite each page of a document, and return each page's id and image size."""
    number = DOCUMENTS[document]
    first = raw.cached(SOURCE, page_url(number, 1))
    out: dict[int, tuple[str, int, int]] = {}
    for page in range(1, _pages(first.path) + 1):
        stored = raw.cached(SOURCE, page_url(number, page))
        artifact = p.artifact(stored, GAZETTE)
        scan = image(number, page)
        width, height = Image.open(io.BytesIO(scan)).size
        name = f"{stored.sha256}-1.jpg"
        folder = DATA / "pages" / SOURCE
        folder.mkdir(parents=True, exist_ok=True)
        if not (folder / name).exists():
            (folder / name).write_bytes(scan)
        id = p.id("page", stored.sha256, 1)
        p.add(
            "source_page",
            id=id,
            artifact_id=artifact,
            page_number=1,
            image_key=f"pages/{SOURCE}/{name}",
            width=width,
            height=height,
        )
        out[page] = (id, width, height)
    return out


def _value(
    p: Package,
    pages: dict[int, tuple[str, int, int]],
    result_set: str,
    unit: str,
    option: str | None,
    measure: int,
    fig: Fig,
) -> None:
    row = {"result_set_id": result_set, "unit_id": unit, "option_id": option, "channel": 0, "measure": measure}
    p.add("result_value", **row, count_round=0, flags=DIGITIZED, value=fig.value)
    p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=fig.value)
    page, width, height = pages[fig.page]
    p.add(
        "value_locator",
        **row,
        count_round=0,
        page_id=page,
        x0=round(max(fig.x0 - 4, 0) / width, 4),
        y0=round(max(fig.y - 8, 0) / height, 4),
        x1=round(min(fig.x1 + 4, width) / width, 4),
        y1=round(min(fig.y + 8, height) / height, 4),
    )


def _slug(vote: Vote) -> str:
    if vote.kind == "parliament":
        return f"me/{vote.day}-general"
    return f"me/{vote.day}-presidential" + ("-runoff" if vote.round_number == 2 else "")


def build(vote: Vote) -> Package:
    day = date.fromisoformat(vote.day)
    what = "parliamentary" if vote.kind == "parliament" else "presidential"
    p = Package(
        f"{SOURCE}/{vote.day}",
        SOURCE,
        f"The Montenegrin {what} election of {vote.day}"
        + (", second round" if vote.round_number == 2 else "")
        + ", national totals from the Republic Election Commission's decision in the official gazette",
        depends=[*DEPENDS, *([f"{SOURCE}/{vote.previous}"] if vote.previous else [])],
    )
    p.external.add(MONTENEGRO)
    pages = _pages_of(p, vote.document)
    for document in vote.cites:
        _pages_of(p, document)
    election = key("election", vote.day)
    p.add(
        "election",
        id=election,
        polity_id=MONTENEGRO,
        kind="runoff" if vote.round_number == 2 else "general",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="annulled" if vote.annulled else "held",
        rerun_of_id=key("election", vote.rerun_of) if vote.rerun_of else None,
    )
    p.add("slug", path=_slug(vote), entity_id=election, is_current=True)
    p.name(election, vote.english, "en", "official")
    p.name(election, vote.native, "sr", "native", False)
    if vote.annulled:
        p.add(
            "note",
            id=key("note", election, "annulled"),
            entity_id=election,
            kind="caveat",
            body_md=vote.annulled,
            lang="en",
        )
    contest = key("contest", vote.day)
    path = _slug(vote) + ("/parliament" if vote.kind == "parliament" else "/president")
    fields: dict[str, object] = (
        {"body_id": dik.PARLIAMENT, "magnitude": vote.seats, "electoral_system_id": None}
        if vote.kind == "parliament"
        else {"office_id": dik.PRESIDENCY, "magnitude": 1, "electoral_system_id": dik.SYSTEM_PRESIDENT}
    )
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation" if vote.kind == "parliament" else "office",
        round_number=vote.round_number,
        previous_round_id=key("contest", vote.previous) if vote.previous else None,
        default_result_kind="counted",
        **fields,
    )
    p.name(contest, vote.english, "en", "official")
    p.name(contest, vote.native, "sr", "native", False)
    p.add("slug", path=path, entity_id=contest, is_current=True)
    p.add(
        "note",
        id=key("note", contest, "rules"),
        entity_id=contest,
        kind="methodology",
        body_md=" ".join(part for part in (vote.note, PRESIDENT_NOTE if vote.kind == "president" else "") if part),
        lang="en",
    )
    options: list[str] = []
    for position, option in enumerate(vote.options, 1):
        id = key("option", vote.day, position)
        options.append(id)
        if vote.kind == "parliament":
            p.add(
                "contest_option",
                id=id,
                contest_id=contest,
                kind="list",
                party_id=party_id(option.label),
                ballot_position=position,
                ballot_label=option.label,
            )
        else:
            p.add(
                "contest_option",
                id=id,
                contest_id=contest,
                kind="candidate",
                ballot_position=position,
                ballot_label=option.label,
            )
            p.add(
                "candidacy",
                id=key("candidacy", vote.day, position),
                person_id=person_id(person_name(option)),
                option_id=id,
                role="head",
                ballot_name=option.label,
            )
    if not vote.acc:
        return p
    result_set = key("result_set", vote.day)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=GAZETTE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    measures = PARLIAMENT_MEASURES if vote.kind == "parliament" or CAST in vote.acc else PRESIDENT_MEASURES
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=METHOD.format(gazette=issue(vote.document), measures=measures),
        lang="en",
    )
    unit = key("unit", vote.day, "ME")
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=MONTENEGRO,
        scheme_code="me.country",
        code=f"rik:{vote.day}:ME",
        parent_id=None,
        area_relation="unlocated",
        area_id=None,
        kind="standard",
    )
    p.name(unit, "Montenegro", "sr", "official")
    for measure, fig in vote.acc.items():
        _value(p, pages, result_set, unit, None, measure, fig)
    for id, option in zip(options, vote.options, strict=True):
        if option.votes is not None:
            _value(p, pages, result_set, unit, id, VOTES, option.votes)
    if vote.annulled:
        p.add(
            "note",
            id=key("note", result_set, "annulled"),
            entity_id=result_set,
            kind="caveat",
            body_md=vote.annulled,
            lang="en",
        )
    for id, option in zip(options, vote.options, strict=True):
        name = person_name(option)
        if vote.kind == "parliament":
            if option.seats:
                p.add("outcome", result_set_id=result_set, option_id=id, kind="won", seats=option.seats)
        elif vote.elected:
            p.add("outcome", result_set_id=result_set, option_id=id, kind="elected" if name == vote.elected else "lost")
        elif vote.advanced:
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=id,
                kind="advanced" if name in vote.advanced else "eliminated",
            )
    return p


def check_sums() -> list[str]:
    """Compare each vote's figures with the commission's own totals; returns the disagreements."""
    problems = []
    for vote in VOTES_DATA:
        a = {measure: fig.value for measure, fig in vote.acc.items()}
        votes = [option.votes.value for option in vote.options if option.votes is not None]
        if VALID in a and sum(votes) != a[VALID]:
            problems.append(f"{vote.day}: option votes {sum(votes)} against {a[VALID]} valid")
        if CAST in a and a[CAST] != a[VALID] + a[INVALID]:
            problems.append(f"{vote.day}: {a[CAST]} cast against {a[VALID]} valid and {a[INVALID]} invalid")
        if vote.kind == "parliament" and sum(option.seats for option in vote.options) != vote.seats:
            problems.append(f"{vote.day}: seats do not add up to {vote.seats}")
    return problems
