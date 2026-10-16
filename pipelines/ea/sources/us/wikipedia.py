import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.wikipedia"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
API = "https://en.wikipedia.org/w/api.php?"
YEARS = range(1824, 1981, 4)
TERSE = (1932, 1972)
ALIASES = {
    "Socialist": "Socialist Party of America",
    "Socialist Labor": "Socialist Labor Party of America",
    "Populist": "People's Party",
    "Dixiecrat": "States' Rights Democratic Party",
}
TERSE_NAMES = {
    (1972, "Schmitz"): "John G. Schmitz",
    (1972, "Anderson"): "Thomas J. Anderson",
    (1932, "Maurer"): "James H. Maurer",
}
PROGRESSIVE = {1912: "Q1363538", 1916: "Q1363538", 1924: "Q1638786"}
SPANS = {
    1824: (date(1824, 10, 26), date(1824, 12, 2)),
    1828: (date(1828, 10, 31), date(1828, 12, 2)),
    1832: (date(1832, 11, 2), date(1832, 12, 5)),
    1836: (date(1836, 11, 3), date(1836, 12, 7)),
    1840: (date(1840, 10, 30), date(1840, 12, 2)),
    1844: (date(1844, 11, 1), date(1844, 12, 4)),
}
TITLE = "United States presidential election articles, results by state"
NOTE = (
    "Votes as tabulated in the results-by-state table of the English Wikipedia's article on the election, which "
    "cites Dubin's and Burnham's compilations for the nineteenth century. "
    "Candidates outside the table's columns are counted under Others, and so is any gap "
    "between the named candidates and the table's state total; where that total is below the candidates' sum, the "
    "sum is the total. The winners shown are the slates that carried the "
    "state; where the table splits a state's electors among candidates on its ballot, the split is kept."
)


def page(year: int) -> str:
    return API + urlencode(
        {
            "action": "parse",
            "page": f"{year} United States presidential election",
            "prop": "text",
            "format": "json",
            "formatversion": 2,
            "redirects": 1,
        }
    )


@dataclass
class Cell:
    text: str
    links: list[str]
    colspan: int
    rowspan: int


class Reader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[Cell]]] = []
        self.seen = False
        self.depth = 0
        self.hidden = 0
        self.cell: Cell | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = {key: value or "" for key, value in attrs}
        if found.get("id", "").lower().startswith("results_by_state"):
            self.seen = True
        if tag == "table" and self.seen:
            self.depth += 1
            if self.depth == 1:
                self.tables.append([])
        if self.depth != 1:
            return
        if tag == "tr":
            self.tables[-1].append([])
        elif tag in ("td", "th") and self.tables[-1]:
            self.cell = Cell(
                "",
                [],
                int(re.sub(r"\D", "", found.get("colspan", "")) or 1),
                int(re.sub(r"\D", "", found.get("rowspan", "")) or 1),
            )
            self.tables[-1][-1].append(self.cell)
        elif self.cell is not None:
            if (self.hidden and tag in ("span", "sup", "style")) or (
                tag in ("sup", "style")
                or "display:none" in found.get("style", "").replace(" ", "")
                or "sortkey" in found.get("class", "")
            ):
                self.hidden += 1
            elif tag == "a" and found.get("title") and not self.hidden:
                self.cell.links.append(found["title"])
            elif tag == "br":
                self.cell.text += "\n"

    def handle_endtag(self, tag: str) -> None:
        if self.hidden and tag in ("span", "sup", "style"):
            self.hidden -= 1
        if tag == "table" and self.depth:
            self.depth -= 1
        if tag in ("td", "th"):
            self.cell = None

    def handle_data(self, data: str) -> None:
        if self.cell is not None and not self.hidden:
            self.cell.text += data


def grid(year: int) -> list[list[Cell]]:
    reader = Reader()
    reader.feed(json.loads(raw.cached(SOURCE, page(year)).path.read_text())["parse"]["text"])
    rows = next(table for table in reader.tables if len(table) > 10)
    lines: list[list[Cell]] = []
    pending: dict[int, tuple[Cell, int]] = {}
    for row in rows:
        line: list[Cell] = []
        cells = list(row)
        while cells or len(line) in pending:
            column = len(line)
            if column in pending:
                cell, left = pending.pop(column)
                if left > 1:
                    pending[column] = (cell, left - 1)
                line.append(cell)
                continue
            cell = cells.pop(0)
            for _ in range(cell.colspan):
                if cell.rowspan > 1:
                    pending[len(line)] = (cell, cell.rowspan - 1)
                line.append(cell)
        lines.append(line)
    return lines


def fetch() -> None:
    for year in YEARS:
        raw.fetch(SOURCE, page(year), suffix=".json")


def parse() -> None:
    with connect() as conn:
        states = {
            re.sub(r"[^a-z]", "", text.lower()): iso[3:]
            for iso, text in conn.execute(
                """select p.iso_code, n.text from polity p join name n on n.entity_id = p.id and n.is_preferred
                   where p.iso_code ~ '^US-[A-Z]{2}$' and n.lang = 'en'"""
            ).fetchall()
        }
    states["dc"] = "DC"
    states.update({postal.lower(): postal for postal in set(states.values())})

    def number(text: str) -> int | None:
        cleaned = text.strip().replace(",", "").replace("−", "-").replace("–", "-")
        return int(cleaned) if re.fullmatch(r"-?\d+", cleaned) else None

    rows: list[dict[str, object]] = []
    for year in YEARS:
        lines = grid(year)
        header, sub = lines[0], lines[1]
        groups = [
            (column, cell.text.strip())
            for column, cell in enumerate(header)
            if not column or cell is not header[column - 1]
        ]
        columns: dict[int, tuple[str, int]] = {}
        electors: int | None = None
        for index, (start, label) in enumerate(groups):
            end = groups[index + 1][0] if index + 1 < len(groups) else len(header)
            kind = (
                "state"
                if start == 0 or not label or label == "EV"
                else "others"
                if re.match(r"(?i)others?$", label)
                else "total"
                if re.search(r"(?i)total", label)
                else "skip"
                if re.match(r"(?i)margin|citation", label)
                else "candidate"
            )
            for column in range(start, end):
                text = sub[column].text.strip().lower().replace("\n", " ") if column < len(sub) else ""
                measure = (
                    "ev"
                    if text in ("electoral votes", "ev")
                    else "votes"
                    if text in ("#", "votes", "votes cast")
                    else None
                )
                if kind == "state" and measure == "ev":
                    electors = column
                elif kind == "total" and column == start:
                    columns[column] = ("total", -1)
                elif kind in ("candidate", "others") and measure is not None:
                    columns[column] = (measure, index)
        for line in lines[2:]:
            first = re.sub(r"\[.*?\]|†|\*|Tooltip.*", "", line[0].text.split("\n")[0]).strip()
            base, _, district = first.partition("-")
            postal = states.get(re.sub(r"[^a-z]", "", base.lower()))
            if postal is None or line[0] is line[-1]:
                continue
            values: dict[int, dict[str, int | None]] = {}
            total = None
            for column, (measure, index) in columns.items():
                if column < len(line):
                    if measure == "total":
                        total = number(line[column].text)
                    else:
                        values.setdefault(index, {})[measure] = number(line[column].text)
            for index, cells in values.items():
                if cells.get("votes") is None and not cells.get("ev"):
                    continue
                label = groups[index][1]
                rows.append(
                    {
                        "year": year,
                        "state": postal,
                        "district": district.strip() or None,
                        "electors": None if electors is None else number(line[electors].text),
                        "column": index,
                        "kind": "others" if re.match(r"(?i)others?$", label) else "candidate",
                        "label": label,
                        "votes": cells.get("votes"),
                        "ev": cells.get("ev") or 0,
                        "total": total,
                    }
                )
    frame = pl.DataFrame(
        rows,
        schema={
            "year": pl.Int16,
            "state": pl.String,
            "district": pl.String,
            "electors": pl.Int16,
            "column": pl.Int16,
            "kind": pl.String,
            "label": pl.String,
            "votes": pl.Int64,
            "ev": pl.Int16,
            "total": pl.Int64,
        },
    )
    covered = frame.filter(pl.col("district").is_null()).select("year", "state").unique()
    parts = frame.filter(pl.col("district").is_not_null()).join(covered, on=["year", "state"])
    statewide = (
        frame.filter(pl.col("district").is_null())
        .join(
            parts.group_by("year", "state", "column").agg(pl.col("ev").sum().alias("extra")),
            on=["year", "state", "column"],
            how="left",
        )
        .join(
            parts.unique(["year", "state", "district"])
            .group_by("year", "state")
            .agg(pl.col("electors").sum().alias("more")),
            on=["year", "state"],
            how="left",
        )
        .with_columns(
            ev=pl.col("ev") + pl.col("extra").fill_null(0),
            electors=pl.col("electors") + pl.col("more").fill_null(0),
        )
        .drop("extra", "more")
    )
    districts = (
        frame.filter(pl.col("district").is_not_null())
        .join(covered, on=["year", "state"], how="anti")
        .group_by("year", "state", "column", "kind", "label")
        .agg(
            pl.col("electors").sum(),
            pl.col("votes").sum(),
            pl.col("ev").sum(),
        )
    )
    district_totals = (
        frame.filter(pl.col("district").is_not_null())
        .join(covered, on=["year", "state"], how="anti")
        .unique(["year", "state", "district"])
        .group_by("year", "state")
        .agg(pl.col("total").sum(), pl.col("electors").sum().alias("seats"))
    )
    districts = districts.join(district_totals, on=["year", "state"]).select(
        "year",
        "state",
        pl.col("seats").alias("electors"),
        "column",
        "kind",
        "label",
        "votes",
        "ev",
        "total",
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.concat([statewide.drop("district"), districts.cast(statewide.drop("district").schema)])
    frame.write_parquet(PARSED / "rows.parquet")
    print(
        frame.group_by("year").agg(pl.col("state").n_unique(), pl.col("votes").sum()).sort("year"),
        f"states summed from districts: {sorted(set(districts.select('year', 'state').iter_rows()))}",
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "rows.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Presidential results by state, 1824–1980, from the English Wikipedia's results-by-state tables",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source("Wikipedia", TITLE)
        cs.add(
            "source",
            id=source,
            kind="community",
            publisher="Wikipedia",
            title=TITLE,
            url="https://en.wikipedia.org/wiki/List_of_United_States_presidential_elections_by_popular_vote_margin",
            license="CC BY-SA 4.0",
            notes=NOTE,
        )
        for year in YEARS:
            cs.artifact(raw.cached(SOURCE, page(year)), source)

        def plain(text: str) -> list[str]:
            folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
            return [part for part in re.findall(r"[a-z]+", folded) if part not in ("jr", "sr")]

        college: dict[tuple[int, str], list[tuple[list[str], UUID, UUID | None]]] = {}
        for year, office, text, person, party in conn.execute(
            """select substr(n.text, 1, 4)::int,
                      case when n.text like '%%Vice President' then 'vice_president' else 'president' end,
                      p.text, cy.person_id, o.party_id
               from candidacy cy join contest_option o on o.id = cy.option_id join contest k on k.id = o.contest_id
               join changeset x on x.id = k.changeset_id
               join name n on n.entity_id = k.id and n.is_preferred
               join name p on p.entity_id = cy.person_id and p.is_preferred
               where x.pipeline = 'us.nara' and k.round_number = 1 and n.text ~ '^\\d{4} '"""
        ).fetchall():
            college.setdefault((year, office), []).append((plain(text), person, party))
        parties = {
            re.sub(r"[^a-z]", "", text.lower()): party
            for text, party in conn.execute(
                """select n.text, p.id from party p join name n on n.entity_id = p.id and n.lang = 'en'
                   join polity y on y.id = p.polity_id where y.iso_code = 'US' and n.text <> 'Progressive Party'"""
            ).fetchall()
        }
        progressive = dict(
            conn.execute(
                "select x.value, x.entity_id from external_id x where x.scheme = 'wikidata' and x.value = any(%s)",
                [list(PROGRESSIVE.values())],
            ).fetchall()
        )
        states = {
            iso[3:]: (code, text)
            for iso, code, text in conn.execute(
                """select p.iso_code, u.code, n.text from polity p
                   join polity_territory t on t.polity_id = p.id
                   join reporting_unit u on u.area_id = t.area_id and u.scheme_code = 'us.state'
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   where p.iso_code ~ '^US-[A-Z]{2}$'"""
            ).fetchall()
        }
        known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'")}
        paths = dict(conn.execute("select path, entity_id from slug where is_current").fetchall())
        office = cs.key("us.office", "president")
        college_body = cs.key("us.body", "electoral_college")
        system = cs.key("us.electoral_system", "winner_take_all")
        if cs.minted & {office, college_body, system}:
            raise ValueError("the presidency, the Electoral College, and winner-take-all must already exist")
        surnames: dict[str, set[str]] = {}
        for label in frame.filter(~pl.col("year").is_in(TERSE))["label"].unique():
            head = label.split("\n")[0].split("/")[0].strip()
            if len(plain(head)) > 1:
                surnames.setdefault(plain(head)[-1], set()).add(head)

        people: set[UUID] = set()
        everyone = [entry for entries in college.values() for entry in entries]

        def person(year: int, role: str, text: str) -> tuple[UUID, UUID | None]:
            full = TERSE_NAMES.get((year, text), text)
            words = plain(full)
            if len(words) == 1 and len(surnames.get(words[0], set())) == 1:
                (full,) = surnames[words[0]]
                words = plain(full)
            for pool in (college.get((year, role), []), everyone):
                found = [
                    (entity, party if pool is not everyone else None)
                    for parts, entity, party in pool
                    if parts[-1] == words[-1] and (len(words) == 1 or parts[0][0] == words[0][0])
                ]
                if len({entity for entity, _ in found}) == 1:
                    return found[0]
            entity = cs.key(f"{SOURCE}.person", slug(f"{words[0]} {words[-1]}"))
            if entity in cs.minted and entity not in people:
                cs.add("person", id=entity)
                name(entity, full)
            people.add(entity)
            return entity, None

        def election(key: str, polity: UUID, span: tuple[date, date], title: str, path: str, parent: UUID | None):
            if path in paths and key not in known:
                return paths[path]
            entity = cs.key("us.election", key)
            if key not in known:
                known.add(key)
                cs.add(
                    "election",
                    id=entity,
                    polity_id=polity,
                    parent_id=parent,
                    kind="general",
                    date_from=span[0],
                    date_to=span[1],
                    date_precision="day",
                    status="held",
                )
                name(entity, title)
                cs.add("slug", path=path, entity_id=entity)
            return entity

        unmatched: set[str] = set()
        skipped: list[str] = []
        for (year,), lines in frame.group_by("year", maintain_order=True):
            if year in SPANS:
                span, stem, tag = SPANS[year], str(year), f"{year}-presidential"
            else:
                first = date(year, 11, 1) + timedelta(days=(-date(year, 11, 1).weekday()) % 7)
                day = first + timedelta(days=1)
                span, stem, tag = (day, day), str(day), f"{day}-general"
            umbrella = election(
                tag if year in SPANS else stem,
                cs.polity("US"),
                span,
                f"{year} United States general election",
                f"us/{tag}",
                None,
            )
            national_key = f"{stem}:president"
            national = cs.key("us.contest", national_key)
            seats_by_state = {
                state: max([*group["electors"].drop_nulls().to_list(), sum(group["ev"].to_list())])
                for (state,), group in lines.group_by("state")
            }
            cs.add(
                "contest",
                id=national,
                election_id=umbrella,
                kind="office",
                round_number=1,
                default_result_kind="counted",
                office_id=office,
                body_id=college_body,
                magnitude=sum(seats_by_state.values()),
            )
            name(national, f"{year} United States presidential election")
            cs.add("slug", path=f"us/{tag}/president", entity_id=national)
            national_set = cs.key(f"{SOURCE}.result_set", national_key)
            cs.add(
                "result_set",
                id=national_set,
                election_id=umbrella,
                contest_id=national,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=0,
            )
            won: dict[UUID, int] = {}
            heads: dict[UUID, tuple[UUID | None, str]] = {}
            for (state,), rows in lines.group_by("state", maintain_order=True):
                if not rows["votes"].fill_null(0).sum():
                    skipped.append(f"{year} {state}")
                    continue
                code, state_name = states[state]
                parent = election(
                    f"{tag}:{state}" if year in SPANS else f"{stem}:{state}",
                    cs.polity(f"US-{state}"),
                    span,
                    f"{year} {state_name} general election",
                    f"us/{state.lower()}/{tag}",
                    umbrella,
                )
                contest_key = f"{stem}:{state}:president"
                contest = cs.key("us.contest", contest_key)
                electors = seats_by_state[state]
                cs.add(
                    "contest",
                    id=contest,
                    election_id=parent,
                    kind="elector_slate",
                    round_number=1,
                    default_result_kind="counted",
                    body_id=college_body,
                    district_area_id=cs.area("us.state", code),
                    magnitude=electors,
                    allocation_parent_id=national,
                    electoral_system_id=system,
                )
                name(contest, f"{year} presidential election in {state_name}")
                cs.add("slug", path=f"us/{state.lower()}/{tag}/president", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=parent,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
                cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE)
                unit = cs.unit("us.state", code)
                cells: dict[UUID, int] = {}
                pledged: dict[UUID, int] = {}
                owners: dict[UUID, UUID] = {}
                others: UUID | None = None
                for row in rows.sort("column").iter_rows(named=True):
                    parts = [part.strip() for part in row["label"].split("\n") if part.strip()]
                    if row["kind"] == "others":
                        others = cs.key("us.option", f"{contest_key}:others")
                        cs.add("contest_option", id=others, contest_id=contest, kind="others", ballot_label="Others")
                        cells[others] = row["votes"] or 0
                        continue
                    text, _, mate = parts[0].partition("/")
                    labels = [label.strip() for part in parts[1:] for label in part.split("/") if label.strip()]
                    if year not in TERSE:
                        labels, mate = [mate, *labels] if mate else labels, ""
                    nobody = re.match(r"(?i)no candidate|unpledged", text) is not None
                    head, party = (None, None) if nobody else person(year, "president", text)
                    for label in labels:
                        if label.startswith("Progressive") and year in PROGRESSIVE:
                            party = party or progressive.get(PROGRESSIVE[year])
                        label = ALIASES.get(label, label)
                        party = party or next(
                            (
                                parties[key]
                                for key in (
                                    re.sub(r"[^a-z]", "", f"{label} party".lower()),
                                    re.sub(r"[^a-z]", "", label.lower()),
                                    re.sub(r"[^a-z]", "", f"{label}ic party".lower()),
                                )
                                if key in parties
                            ),
                            None,
                        )
                    if party is None and labels:
                        unmatched.add(f"{year} {' / '.join(labels)}")
                    option = cs.key("us.option", f"{contest_key}:{head or slug(row['label'])}")
                    shown = " / ".join(labels)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="ticket",
                        party_id=party,
                        ballot_label=f"{text} ({shown})" if shown else text,
                    )
                    if head is not None:
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{contest_key}:{head}:head"),
                            person_id=head,
                            option_id=option,
                            role="head",
                            ballot_name=text,
                            is_independent=party is None,
                        )
                        heads[head] = (party, text)
                        owners[option] = head
                        if mate:
                            running, _ = person(year, "vice_president", mate)
                            cs.add(
                                "candidacy",
                                id=cs.key("us.candidacy", f"{contest_key}:{head}:running_mate"),
                                person_id=running,
                                option_id=option,
                                role="running_mate",
                                ballot_name=mate,
                                is_independent=party is None,
                            )
                    cells[option] = row["votes"] or 0
                    pledged[option] = row["ev"]
                listed = sum(cells.values())
                total = max([*rows["total"].drop_nulls().to_list(), listed])
                if total > listed:
                    if others is None:
                        others = cs.key("us.option", f"{contest_key}:others")
                        cs.add("contest_option", id=others, contest_id=contest, kind="others", ballot_label="Others")
                        cells[others] = 0
                    cells[others] += total - listed
                if result_set in cs.minted:
                    for option, votes in [*cells.items(), (None, total)]:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=1 if option else 4,
                            count_round=0,
                            flags=0,
                            value=votes,
                        )
                voted = {option: seats for option, seats in pledged.items() if seats and cells.get(option)}
                if len(voted) < 2 or sum(voted.values()) != electors:
                    leader = max(pledged, key=lambda option: cells[option], default=None)
                    voted = {} if leader is None or not cells[leader] else {leader: electors}
                for option, seats in voted.items():
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=seats)
                    if option in owners:
                        won[owners[option]] = won.get(owners[option], 0) + seats
            for head, seats in won.items():
                option = cs.key("us.option", f"{national_key}:{head}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=national,
                    kind="ticket",
                    party_id=heads[head][0],
                    ballot_label=heads[head][1],
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{national_key}:{head}:head"),
                    person_id=head,
                    option_id=option,
                    role="head",
                    is_independent=heads[head][0] is None,
                )
                cs.add("outcome", result_set_id=national_set, option_id=option, kind="won", seats=seats)
        print(f"no popular vote: {skipped}")
        print(f"parties with no match: {sorted(unmatched)}")
        return cs.write()
