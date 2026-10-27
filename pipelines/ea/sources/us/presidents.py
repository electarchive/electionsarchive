import html
import re
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "us.presidents"
VERSION = "1"
PRESIDENT = UUID("01a13aad-ae18-70cb-af59-d7a7d21b91df")
VICE_PRESIDENT = UUID("01a13aad-ae18-70cb-af59-d7a8f4de2dd0")
NARA = "https://www.archives.gov/research/census/presidents"
SENATE = "https://www.senate.gov/about/officers-staff/vice-president/vice-presidents.htm"
ACTING = [
    (
        date(1985, 7, 13),
        "bush",
        "Ronald Reagan Presidential Library",
        (
            "Letter to the President Pro Tempore of the Senate and the Speaker of the House on the Discharge of the "
            "President's Powers and Duties During His Surgery, July 13, 1985"
        ),
        "https://www.reaganlibrary.gov/archives/speech/letter-president-pro-tempore-senate-and-speaker-house-discharge-presidents-powers",
        ".html",
    ),
    (
        date(2002, 6, 29),
        "cheney",
        "U.S. Government Publishing Office",
        (
            "Public Papers of the Presidents: George W. Bush (2002, Book I), Letter to Congressional Leaders on Temporary "
            "Transfer of the Powers and Duties of President of the United States, June 29, 2002"
        ),
        "https://www.govinfo.gov/content/pkg/PPP-2002-book1/html/PPP-2002-book1-doc-pg1083.htm",
        ".html",
    ),
    (
        date(2007, 7, 21),
        "cheney",
        "U.S. Government Publishing Office",
        (
            "Public Papers of the Presidents: George W. Bush (2007, Book II), Letter to Congressional Leaders on the "
            "Temporary Transfer of the Powers and Duties of the President of the United States, July 21, 2007"
        ),
        "https://www.govinfo.gov/content/pkg/PPP-2007-book2/html/PPP-2007-book2-doc-pg1003.htm",
        ".html",
    ),
    (
        date(2021, 11, 19),
        "harris",
        "U.S. Government Publishing Office",
        (
            "Daily Compilation of Presidential Documents DCPD-202100985, Letter to President Pro Tempore of the Senate on "
            "the Temporary Transfer of the Powers and Duties of President of the United States, November 19, 2021"
        ),
        "https://www.govinfo.gov/content/pkg/DCPD-202100985/pdf/DCPD-202100985.pdf",
        ".pdf",
    ),
]
UNELECTED = {"ford": "Q9582", "rockefeller": "Q11237"}
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE = re.compile(rf"\b({MONTHS}|Sept)\.? (\d{{1,2}}), (\d{{4}})")
SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
WINDOW = timedelta(days=200)
FIRST = date(1789, 4, 30)
NARA_NOTES = (
    "Every presidency with its dates in office, from the National Archives' list of US presidents. Each term is "
    "stored as its own tenure, split at the constitutional term dates (March 4 through 1933, January 20 from 1937)."
)
SENATE_NOTES = (
    "Vice presidents with their years in office, and in the footnotes the exact dates of deaths, resignations, "
    "successions to the presidency, and the oaths of the two appointed vice presidents. Terms that begin or end "
    "at a regular term date take that day from the presidential terms; John Adams's first term is stored by year."
)


def _days(text: str) -> list[date]:
    return [
        date(int(year), MONTHS.split("|").index("September" if month == "Sept" else month) + 1, int(day))
        for month, day, year in DATE.findall(text)
    ]


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _surname(label: str) -> str:
    words = [word for word in slug(label).split("-") if word not in SUFFIXES]
    return words[-1] if words else ""


def _boundaries() -> list[date]:
    starts = [date(year, 3, 4) for year in range(1793, 1934, 4)]
    starts += [date(year, 1, 20) for year in range(1937, date.today().year + 1, 4)]
    return starts


def fetch() -> None:
    raw.fetch(SOURCE, NARA, suffix=".html")
    raw.fetch(SOURCE, SENATE, suffix=".html")
    for *_, url, suffix in ACTING:
        raw.fetch(SOURCE, url, suffix=suffix)


def _presidencies() -> list[tuple[str, date, date | None]]:
    rows: list[tuple[str, date, date | None]] = []
    for row in re.findall(r"<tr.*?</tr>", raw.cached(SOURCE, NARA).path.read_text(), flags=re.DOTALL):
        cells = [_text(cell) for cell in re.findall(r"<td.*?</td>", row, flags=re.DOTALL)]
        if len(cells) != 2:
            continue
        dates = _days(cells[1])
        rows.append((cells[0], dates[0], dates[1] if len(dates) > 1 else None))
    return rows


def _vice_presidencies() -> list[tuple[str, date, str, date | None, str | None]]:
    page = raw.cached(SOURCE, SENATE).path.read_text(encoding="utf-8", errors="replace")
    notes = {
        int(number): text
        for number, text in re.findall(r"(?:^|\s)(\d{1,2})\. (.*?)(?=\s\d{1,2}\. [A-Z\"]|\sReturn to )", _text(page))
    }
    boundaries = {start.year: start for start in [FIRST, *_boundaries()]}
    rows: list[tuple[str, date, str, date | None, str | None]] = []
    for row in re.findall(r"<tr>.*?</tr>", page, flags=re.DOTALL):
        cells = re.findall(r"<td.*?</td>", row, flags=re.DOTALL)
        if len(cells) != 4:
            continue
        name = _text(re.sub(r"<sup>.*?</sup>", "", cells[0]))
        years = re.findall(r"\d{4}|present", _text(re.sub(r"<sup>.*?</sup>", "", cells[3])))
        start, ended, cause = boundaries.get(int(years[0])), None, None
        precision = "year" if start == FIRST else "day"
        if start == FIRST:
            start = date(1789, 1, 1)
        if years[-1] != "present":
            ended, cause = boundaries.get(int(years[-1])), "term_end"
        for number in re.findall(r"<sup>\s*(\d+)\s*</sup>", row):
            note = notes[int(number)]
            if (found := re.search(rf"took the oath of office[^.]*? on ({DATE.pattern})", note)) is not None:
                start, precision = _days(found[1])[0], "day"
            if (
                found := re.search(
                    rf"(died in office|resigned|succeeded to the presidency),? (?:on )?({DATE.pattern})", note
                )
            ) is not None:
                ended = _days(found[2])[0]
                cause = {"died in office": "died", "resigned": "resigned"}.get(found[1])
        if start is None:
            raise ValueError(f"{name}: no start date")
        rows.append((name, start, precision, ended, cause))
    return rows


def emit() -> Path:
    boundaries = _boundaries()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Presidents and vice presidents of the United States, every term from 1789, with acting presidents",
            polity="US",
        )
        nara = cs.source("National Archives and Records Administration", "US Presidents in the Census Records")
        cs.add(
            "source",
            id=nara,
            kind="official_body",
            publisher="National Archives and Records Administration",
            title="US Presidents in the Census Records",
            url=NARA,
            license="Public domain (US federal government work)",
            notes=NARA_NOTES,
        )
        cs.artifact(raw.cached(SOURCE, NARA), nara)
        senate = cs.source("United States Senate", "Vice Presidents of the United States")
        cs.add(
            "source",
            id=senate,
            kind="official_body",
            publisher="United States Senate",
            title="Vice Presidents of the United States",
            url=SENATE,
            license="Public domain (US federal government work)",
            notes=SENATE_NOTES,
        )
        cs.artifact(raw.cached(SOURCE, SENATE), senate)
        winners: list[tuple[date, UUID, UUID, UUID, str]] = [
            (day, office, contest, person, _surname(label))
            for day, office, contest, person, label in conn.execute(
                """select e.date_from, c.office_id, c.id, coalesce(m.into_id, cd.person_id),
                          coalesce(cd.ballot_name, op.ballot_label)
                   from contest c
                   join election e on e.id = c.election_id and e.kind = 'indirect'
                   join result_set rs on rs.contest_id = c.id
                   join outcome o on o.result_set_id = rs.id and o.kind in ('won', 'elected')
                   join contest_option op on op.id = o.option_id
                   join candidacy cd on cd.option_id = o.option_id and cd.role = 'head'
                   left join entity_merge m on m.entity_id = cd.person_id
                   where c.office_id = any(%s)""",
                [[PRESIDENT, VICE_PRESIDENT]],
            ).fetchall()
        ]
        deaths: dict[UUID, date] = dict(
            conn.execute("select id, death_date from person where death_date is not null").fetchall()
        )

        def person(surname: str, on: date) -> UUID:
            found = max(
                ((day, someone) for day, _, _, someone, name in winners if name == surname and day <= on),
                default=None,
                key=lambda pair: pair[0],
            )
            return found[1] if found is not None else cs.key("wikidata", UNELECTED[surname])

        def contest(office: UUID, surname: str, term: date, someone: UUID) -> UUID | None:
            offices = {office} if office == PRESIDENT or term.year > 1804 else {PRESIDENT}
            found = max(
                (
                    (day, contest)
                    for day, held, contest, chosen, name in winners
                    if held in offices and chosen == someone and name == surname and term - WINDOW <= day < term
                ),
                default=None,
                key=lambda pair: pair[0],
            )
            return found[1] if found is not None else None

        for office, source, spans in (
            (
                PRESIDENT,
                nara,
                [
                    (name, start, "day", end, None if end is None or end in boundaries else "died_or_resigned")
                    for name, start, end in _presidencies()
                ],
            ),
            (VICE_PRESIDENT, senate, _vice_presidencies()),
        ):
            previous: UUID | None = None
            last: dict[UUID, UUID] = {}
            for name, start, precision, end, cause in spans:
                surname = _surname(name)
                someone = person(surname, max(start, FIRST))
                stops = [b for b in boundaries if start < b and (end is None or b < end)]
                segments: list[tuple[date, date | None]] = list(zip([start, *stops], [*stops, end], strict=True))
                resumed = last.get(someone) if previous is not None and last.get(someone) != previous else None
                for index, (begin, finish) in enumerate(segments):
                    term = FIRST if begin.year == 1789 else begin
                    won = contest(office, surname, term, someone)
                    if index == 0 and won is None:
                        via = "appointed" if surname in UNELECTED and office == VICE_PRESIDENT else "succession"
                    else:
                        via = "elected"
                    ended = None
                    if finish is not None:
                        if index < len(segments) - 1 or finish in boundaries:
                            ended = "term_end"
                        elif cause == "died_or_resigned":
                            ended = (
                                "died"
                                if abs(deaths.get(someone, date.min) - finish) <= timedelta(days=1)
                                else "resigned"
                            )
                        else:
                            ended = cause
                    tenure = cs.key(f"{SOURCE}.tenure", f"{'P' if office == PRESIDENT else 'V'}:{surname}:{begin}")
                    cs.add(
                        "tenure",
                        id=tenure,
                        person_id=someone,
                        office_id=office,
                        from_date=begin,
                        from_precision=precision if index == 0 else "day",
                        to_date=finish,
                        to_precision="day",
                        acquired_via=via,
                        contest_id=won,
                        ended_via=ended,
                        resumes_tenure_id=resumed if index == 0 else None,
                        succeeds_tenure_id=previous,
                    )
                    cs.add("citation", entity_id=tenure, source_id=source, locator={"row": name})
                    if office == VICE_PRESIDENT:
                        cs.add("citation", entity_id=tenure, source_id=nara, locator={"terms": "regular term dates"})
                    previous = last[someone] = tenure
        for day, surname, publisher, title, url, _ in ACTING:
            letter = cs.source(publisher, title)
            cs.add(
                "source",
                id=letter,
                kind="official_body",
                publisher=publisher,
                title=title,
                url=url,
                license="Public domain (US federal government work)",
            )
            cs.artifact(raw.cached(SOURCE, url), letter)
            tenure = cs.key(f"{SOURCE}.tenure", f"acting:{surname}:{day}")
            cs.add(
                "tenure",
                id=tenure,
                person_id=person(surname, day),
                office_id=PRESIDENT,
                from_date=day,
                from_precision="day",
                to_date=day,
                to_precision="day",
                acquired_via="acting",
            )
            cs.add("citation", entity_id=tenure, source_id=letter, locator={"date": day.isoformat()})
        return cs.write()
