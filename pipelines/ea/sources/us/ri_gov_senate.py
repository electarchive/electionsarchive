import html
import re
from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import canvass
from ea.store import connect

SOURCE = "us.ri_gov_senate"
VERSION = "1"
BASE = "https://web.archive.org/web/2012id_/http://www.elections.state.ri.us/elections/results/"
PUBLISHER = "Rhode Island Board of Elections"
TITLE = "Election results by city and town"
PAGES = {
    "2000-11-07:RI:senate": "2000/generalelection/comsen.php",
    "2002-11-05:RI:governor": "2002/generalelection/governor.php",
    "2002-11-05:RI:senate": "2002/generalelection/ussenate.php",
}
TOWN_PREFIX = {"E": "East ", "W": "West ", "No": "North ", "So": "South "}


def fetch() -> None:
    for page in PAGES.values():
        raw.fetch(SOURCE, BASE + page, suffix=".php")


def emit() -> Path:
    county = {re.sub(r"[^a-z]", "", town.lower()): code for code, towns in canvass.TOWNS.items() for town in towns}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Rhode Island's 2000 and 2002 U.S. Senate and 2002 governor elections by county from the Board of Elections' results by city and town",
            polity="US-RI",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://elections.ri.gov/elections/results/",
                license="Public record",
            )
        for key, page in PAGES.items():
            artifact = raw.cached(SOURCE, BASE + page)
            cs.artifact(artifact, source)
            text = artifact.path.read_text(errors="replace")
            table = []
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.DOTALL | re.IGNORECASE):
                cells = [
                    re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", cell))).strip()
                    for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL | re.IGNORECASE)
                ]
                if cells := [cell for cell in cells if cell]:
                    table.append(cells)
            start = next(n for n, row in enumerate(table) if row[0] == "COMMUNITY")
            heads = [head for head in table[start][1:] if not head.startswith("%")]
            names = [re.sub(r"\s*(\((REP|DEM|REF|IND)\)|(Dem|Rep)\.)$", "", head).title() for head in heads]
            sums = [0] * len(names)
            counties: dict[str, list[int]] = {}
            totals: list[int] = []
            towns = 0
            for row in table[start + 1 :]:
                votes = [int(cell.replace(",", "")) for cell in row[1:] if "." not in cell]
                if len(votes) != len(names):
                    raise ValueError(f"{key} {row[0]}: {len(votes)} values for {len(names)} candidates")
                if row[0] == "STATE TOTAL":
                    totals = votes
                    continue
                towns += 1
                code = county[
                    re.sub(r"[^a-z]", "", re.sub(r"^(E|W|No|So)\.\s*", lambda m: TOWN_PREFIX[m[1]], row[0]).lower())
                ]
                counties.setdefault(code, [0] * len(names))
                for index, value in enumerate(votes):
                    counties[code][index] += value
                    sums[index] += value
            if towns != 39 or sums != totals:
                raise ValueError(f"{key}: {towns} towns, sums {sums} against state totals {totals}")
            contest, election = conn.execute(
                "select c.id, c.election_id from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = %s",
                [key],
            ).fetchone()
            options = {
                re.sub(r"[^a-z]", "", label.lower()): id
                for label, id in conn.execute(
                    "select ballot_label, id from contest_option where contest_id = %s", [contest]
                ).fetchall()
            }
            official = conn.execute(
                "select sum(v.value) from result_set r join result_value v on v.result_set_k = r.k where r.contest_id = %s and r.is_default and v.measure = 1",
                [contest],
            ).fetchone()[0]
            result_set = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=False,
                completeness=round(sum(totals) / float(official), 4),
            )
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md="Each county is the sum of its cities and towns in the Board of Elections' results by city and town, whose 39 communities sum to the printed state total for every candidate.",
            )
            for code, votes in counties.items():
                unit = cs.unit("us.county", code)
                if unit in cs.minted:
                    raise ValueError(f"county {code} does not exist")
                for name, value in zip(names, votes):
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=options[re.sub(r"[^a-z]", "", name.lower())],
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        return cs.write()
