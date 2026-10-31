from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.ny_redbook_gov"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "manual" / "MF-462"
PUBLISHER = "J. B. Lyon Company"
TITLE = "The New York Red Book, {edition} (official vote for Governor, {year})"
ITEM = "https://babel.hathitrust.org/cgi/pt?id={item}"
NOTE = (
    "Statewide totals for governor read from the Red Book's county table, which prints the whole state's returns on "
    "the last line of the table (HathiTrust page {seq}, printed page {page}), from HathiTrust's page OCR. Every "
    "candidate column and the blank, void, and scattering columns add up to the whole number of votes the volume "
    "prints. Blank, void, and scattering ballots are not candidates and are left out."
)
VOLUMES = {
    1908: ("1909", "nyp.33433084510936", "NY-RedBook-1909-nyp.33433084510936 (1).txt", 781, 635),
    1910: ("1911", "nyp.33433008778387", "NY-RedBook-1911-nyp.33433008778387.txt", 767, 631),
    1914: ("1915", "nyp.33433084510993", "NY-RedBook-1915-nyp.33433084510993.txt", 820, 686),
    1926: ("1927", "uc1.b4046285", "NY-RedBook-1927-uc1.b4046285.txt", 469, 371),
}
READINGS: dict[int, dict[str, object]] = {
    1908: {
        "lines": (
            ("Charles Evans Hughes", "Hughes", 804651),
            ("Lewis Stuyvesant Chanler", "Chanler", 735189),
            ("Clarence J. Shearn", "Shearn", 43212),
            ("Joshua Wanhope", "Wanhope", 33994),
            ("George E. Stockwell", "Stockwell", 18802),
            ("Leander A. Armstrong", "Armstrong", 3655),
        ),
        "left_out": 14353,
        "printed_total": 1653856,
    },
    1910: {
        "lines": (
            ("Henry L. Stimson", "Stimson", 622299),
            ("John Alden Dix", "Dix", 689700),
            ("John J. Hopper", "Hopper", 48470),
            ("Charles Edward Russell", "Russell", 48529),
            ("T. Alexander MacNicholl", "Nicholl", 22295),
            ("Frank E. Passanno", "Passanno", 5717),
        ),
        "left_out": 5819 + 2389 + 31,
        "printed_total": 1445249,
    },
    1914: {
        "lines": (
            ("Martin H. Glynn (Democratic)", "Glynn", 412253),
            ("Charles S. Whitman", "Whitman", 686701),
            ("Frederick M. Davenport", "Davenport", 45586),
            ("Gustave A. Strebel", "Strebel", 37793),
            ("William Sulzer (Prohibition)", "Sulzer", 54189),
            ("Martin H. Glynn (Independence League)", "Glynn", 125252),
            ("James T. Hunter", "Hunter", 2350),
            ("William Sulzer (American)", "Sulzer", 70655),
            ("Martin H. Glynn (No party)", "Glynn", 3764),
            ("William Sulzer (No party)", "Sulzer", 1426),
        ),
        "left_out": 32134 + 14729 + 43,
        "printed_total": 1486875,
    },
    1926: {
        "lines": (
            ("Alfred Emanuel Smith", "Smith", 1523813),
            ("Ogden L. Mills", "Mills", 1276137),
            ("Jacob Panken", "Panken", 83481),
            ("Jeremiah D. Crowley", "Crowley", 3553),
            ("Benjamin Gitlow", "Gitlow", 5507),
            ("Charles E. Manierre", "Manierre", 21285),
        ),
        "left_out": 64124,
        "printed_total": 2977900,
    },
}


def fetch() -> None:
    for _, item, name, _, _ in VOLUMES.values():
        raw.store(
            SOURCE,
            ITEM.format(item=item),
            (INBOX / name).read_bytes(),
            suffix=".txt",
            retrieved_at=datetime.now(UTC),
            media_type="text/plain",
        )


def parse() -> None:
    rows = []
    for year, reading in READINGS.items():
        lines: tuple[tuple[str, str, int], ...] = reading["lines"]  # type: ignore[assignment]
        total = sum(votes for _, _, votes in lines) + int(reading["left_out"])  # type: ignore[call-overload]
        if total != reading["printed_total"]:
            raise ValueError(f"{year}: columns add to {total}, the volume prints {reading['printed_total']}")
        for position, (label, _, votes) in enumerate(lines):
            rows.append((year, position, label, votes, reading["printed_total"]))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["year", "position", "label", "votes", "printed_total"], orient="row").write_csv(
        PARSED / "lines.tsv", separator="\t"
    )
    print(len(rows), "lines")


def emit() -> Path:
    frame = pl.read_csv(PARSED / "lines.tsv", separator="\t")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York's governor statewide totals for 1908, 1910, 1914, and 1926 from the Red Book",
            polity="US",
        )
        state = cs.unit("us.state", "36")
        for year, (edition, item, _, seq, page) in VOLUMES.items():
            title = TITLE.format(edition=edition, year=year)
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=ITEM.format(item=item),
                license="Public domain",
            )
            cs.artifact(raw.cached(SOURCE, ITEM.format(item=item)), source)
            contest, election = conn.execute(
                """select c.id, c.election_id from contest c join external_id k on k.entity_id = c.id
                   and k.scheme = 'us.contest' and k.value ~ %s""",
                [f"^{year}(-[0-9-]+)?:NY:governor$"],
            ).fetchone()
            options: dict[str, list[tuple[UUID, str, bool]]] = {}
            for option, label, carries in conn.execute(
                """select o.id, o.ballot_label, exists (
                       select from result_value v join reporting_unit u on u.k = v.unit_k
                       where v.option_k = o.k and u.scheme_code = 'us.state')
                   from contest_option o where o.contest_id = %s and o.kind in ('candidate', 'ticket')
                   order by 3 desc, 1""",
                [contest],
            ).fetchall():
                options.setdefault(label, []).append((option, label, carries))
            result_set = cs.key(f"{SOURCE}.result_set", f"{year}:NY:governor")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
                supersedes_id=None,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE.format(seq=seq, page=page))
            lines = frame.filter(pl.col("year") == year).sort("position")
            for label, votes in lines.select("label", "votes").iter_rows():
                candidates = options.get(label)
                if candidates is None:
                    raise LookupError(f"{year}: no option for {label}")
                option = candidates[0][0]
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=state,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
            leaders: dict[str, int] = {}
            for label, votes in lines.select("label", "votes").iter_rows():
                leaders[label.split(" (")[0]] = leaders.get(label.split(" (")[0], 0) + votes
            top = max(leaders, key=leaders.__getitem__)
            elected = conn.execute(
                """select o.id from contest_option o where o.contest_id = %s and o.kind = 'candidate'
                   and o.ballot_label like %s order by o.id limit 1""",
                [contest, f"%{top.split()[-1]}"],
            ).fetchone()
            cs.add("outcome", result_set_id=result_set, option_id=elected[0], kind="elected", seats=1)
        return cs.write()
