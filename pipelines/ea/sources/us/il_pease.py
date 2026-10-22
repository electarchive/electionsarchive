import re
import subprocess
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from PIL import Image

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.il_pease"
VERSION = "1"
BOOK = "https://archive.org/download/illinoiselection18peas/illinoiselection18peas.pdf"
LANDING = "https://archive.org/details/illinoiselection18peas"
PUBLISHER = "Illinois State Historical Library"
TITLE = "Illinois Election Returns, 1818-1848"
DIGITIZED = 8
DAYS = {1828: "1828-10-31", 1832: "1832-11-02"}
PAGES = {1828: 139, 1832: 162}
TOTALS = {1828: 14287, 1832: 21468}
VOTES = {
    (1828, "Jackson"): (9600, (402, 291, 452, 312)),
    (1828, "Adams"): (4687, (562, 290, 610, 313)),
    (1832, "Jackson"): (14617, (283, 293, 336, 314)),
    (1832, "Clay"): (6754, (427, 293, 474, 314)),
    (1832, "Wirt"): (97, (587, 293, 616, 314)),
}
TOTAL_BOXES = {1828: (309, 291, 366, 313), 1832: (208, 293, 258, 314)}
LEGENDS = {"Wirt": ("William Wirt (Anti-Masonic)", "antimasonicparty")}
NOTE = (
    "The state's popular vote for president as printed in Theodore Calvin Pease's Illinois Election Returns, "
    "1818-1848 (Illinois State Historical Library, 1923), compiled mainly from the county clerks' original returns "
    "held by the Secretary of State. The column sum equals the printed state total, and each number carries its "
    "page and box. Tickets that Wikipedia's results-by-state table names keep that table's candidates. "
    "Wikipedia's earlier figures are kept as a superseded, non-default set."
)
HELD = (
    "The printed figure for a top-two candidate differs from Wikipedia's by more than 1 percent and no second source "
    "settles it, so Wikipedia's set stays the default and this one is kept beside it."
)


def fetch() -> None:
    raw.fetch(SOURCE, BOOK, suffix=".pdf")


def emit() -> Path:
    for year, total in TOTALS.items():
        assert sum(value for (y, _), (value, _) in VOTES.items() if y == year) == total
    with connect() as conn:
        old = {
            year: row
            for year, *row in conn.execute(
                """select extract(year from e.date_from)::int, r.id, r.election_id, r.contest_id, r.stage, r.kind,
                          r.source_id, r.completeness
                   from result_set r join contest k on k.id = r.contest_id join election e on e.id = r.election_id
                   join slug sl on sl.entity_id = k.id and sl.path like 'us/il/%%/president' and sl.is_current
                   join source s on s.id = r.source_id
                   where k.kind = 'elector_slate' and e.date_from = any(%s::date[]) and s.publisher = 'Wikipedia'
                     and r.is_default""",
                [list(DAYS.values())],
            ).fetchall()
        }
        options = defaultdict(list)
        for rs, option, label, kind, unit, measure, value in conn.execute(
            """select r.id, o.id, o.ballot_label, o.kind, u.id, v.measure, v.value
               from result_set r join result_value v on v.result_set_k = r.k
               left join contest_option o on o.k = v.option_k join reporting_unit u on u.k = v.unit_k
               where r.id = any(%s) and v.channel = 0""",
            [[row[0] for row in old.values()]],
        ).fetchall():
            options[rs].append((option, label, kind, unit, measure, Decimal(value)))
        outcomes = defaultdict(list)
        for rs, option, kind, seats, candidacy in conn.execute(
            "select result_set_id, option_id, kind, seats, candidacy_id from outcome where result_set_id = any(%s)",
            [[row[0] for row in old.values()]],
        ).fetchall():
            outcomes[rs].append((option, kind, seats, candidacy))
        parties = {
            re.sub(r"[^a-z]", "", text.lower()): party
            for text, party in conn.execute(
                """select n.text, p.id from party p join name n on n.entity_id = p.id and n.lang = 'en'
                   join polity y on y.id = p.polity_id where y.iso_code = 'US'"""
            ).fetchall()
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Illinois presidential results for 1828 and 1832 from Pease's Illinois Election Returns, "
            "superseding Wikipedia's",
            polity="US",
        )
        book = raw.cached(SOURCE, BOOK)
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=LANDING,
            license="Public domain",
        )
        artifact = cs.artifact(book, source)
        pages = {}
        for year, number in PAGES.items():
            image = DATA / "pages" / SOURCE / f"{book.sha256}-{number}.png"
            image.parent.mkdir(parents=True, exist_ok=True)
            if not image.exists():
                subprocess.run(
                    ["pdftoppm", "-r", "200", "-gray", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                    + [str(book.path), str(image.with_suffix(""))],
                    check=True,
                )
            with Image.open(image) as rendered:
                width, height = rendered.size
            page = cs.page(artifact, number)
            cs.add(
                "source_page",
                id=page,
                artifact_id=artifact,
                page_number=number,
                image_key=f"pages/{SOURCE}/{book.sha256}-{number}.png",
                width=width,
                height=height,
            )
            pages[year] = (page, width, height)
        for year, (rs, election, contest, stage, kind, source_id, completeness) in sorted(old.items()):
            surnames = [surname for (y, surname) in VOTES if y == year]
            tickets = {option: (label, value) for option, label, okind, _, _, value in options[rs] if okind == "ticket"}
            unit = next(row[3] for row in options[rs])
            total_before = next(row[5] for row in options[rs] if row[4] == 4)
            matched = {}
            for surname in surnames:
                hits = [option for option, (label, _) in tickets.items() if surname in label]
                if len(hits) == 1:
                    matched[surname] = hits[0]
            ids = dict(matched)
            for surname in surnames:
                if surname in matched:
                    continue
                label, party_name = LEGENDS[surname]
                ids[surname] = cs.key(f"{SOURCE}.option", f"{year}:IL:{slug(label)}")
                cs.add(
                    "contest_option",
                    id=ids[surname],
                    contest_id=contest,
                    kind="legend",
                    party_id=parties.get(party_name),
                    ballot_label=label,
                )
            winning = {option for option, *_ in outcomes[rs]}
            assert winning <= set(matched.values())
            ranked = sorted(tickets.items(), key=lambda item: -item[1][1])[:2]
            top_two_off = any(
                abs(next(VOTES[(year, s)][0] for s, o in matched.items() if o == option) - value) > value / 100
                for option, (_, value) in ranked
            )
            default = not top_two_off
            result_set = cs.key(f"{SOURCE}.result_set", f"{year}:IL")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=source,
                status="active",
                is_default=default,
                supersedes_id=rs if default else None,
                completeness=min(Decimal(1), round(Decimal(TOTALS[year]) / total_before, 4)),
            )
            cs.add(
                "result_set",
                id=rs,
                election_id=election,
                contest_id=contest,
                stage=stage,
                kind=kind,
                source_id=source_id,
                status="superseded" if default else "active",
                is_default=not default,
                completeness=completeness,
            )
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=NOTE + ("" if default else " " + HELD),
            )
            page, width, height = pages[year]
            for surname in surnames:
                value, (x0, y0, x1, y1) = VOTES[(year, surname)]
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=ids[surname],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=DIGITIZED,
                    value=value,
                )
                cs.add(
                    "value_locator",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=ids[surname],
                    channel=0,
                    measure=1,
                    count_round=0,
                    page_id=page,
                    source_id=None,
                    x0=x0 / width,
                    y0=y0 / height,
                    x1=x1 / width,
                    y1=y1 / height,
                )
            x0, y0, x1, y1 = TOTAL_BOXES[year]
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=None,
                channel=0,
                measure=4,
                count_round=0,
                flags=DIGITIZED,
                value=TOTALS[year],
            )
            cs.add(
                "value_locator",
                result_set_id=result_set,
                unit_id=unit,
                option_id=None,
                channel=0,
                measure=4,
                count_round=0,
                page_id=page,
                source_id=None,
                x0=x0 / width,
                y0=y0 / height,
                x1=x1 / width,
                y1=y1 / height,
            )
            for option, okind, seats, candidacy in outcomes[rs] if default else []:
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind=okind,
                    seats=seats,
                    candidacy_id=candidacy,
                )
            print(
                year,
                "default" if default else "held",
                {s: VOTES[(year, s)][0] for s in surnames},
                "against",
                {l: int(v) for l, v in tickets.values()},
            )
        return cs.write()
