import re
import subprocess
from pathlib import Path
from uuid import UUID

from ea.changeset import Changeset
from ea.sources.us.gov_official_totals import find, surname
from ea.store import connect

SOURCE = "us.wy_bluebook_gov"
VERSION = "1"
ORIGIN = "us.gov_official_totals"
NEEDLE = "BlueBookFinal"
YEARS = {
    1994: (27, UUID("10061caa-243d-5f0c-9471-b2949d8d72a8"), ("Geringer", "Karpan"), "2,227"),
}
COUNTIES = [
    "Albany",
    "Big Horn",
    "Campbell",
    "Carbon",
    "Converse",
    "Crook",
    "Fremont",
    "Goshen",
    "Hot Springs",
    "Johnson",
    "Laramie",
    "Lincoln",
    "Natrona",
    "Niobrara",
    "Park",
    "Platte",
    "Sheridan",
    "Sublette",
    "Sweetwater",
    "Teton",
    "Uinta",
    "Washakie",
    "Weston",
]
NOTE = (
    "County and statewide candidate totals as printed in the Wyoming Blue Book, Volume V, read from the page's text "
    "layer. The county lines add up to the printed TOTAL line; votes for other candidates, where the book prints them, are given only for the state."
)
WORD = re.compile(
    r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>|<page width="([\d.]+)" height="([\d.]+)"'
)


def words(path: Path, page: int) -> tuple[list[tuple[float, float, float, float, str]], float, float]:
    out = subprocess.run(
        ["pdftotext", "-bbox", "-f", str(page), "-l", str(page), str(path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    found: list[tuple[float, float, float, float, str]] = []
    width = height = 0.0
    for match in WORD.finditer(out):
        if match[6]:
            width, height = float(match[6]), float(match[7])
        else:
            found.append((float(match[1]), float(match[2]), float(match[3]), float(match[4]), match[5].strip("\x07")))
    return found, width, height


def lines(
    path: Path, page: int, columns: tuple[str, str], other: str | None
) -> tuple[
    dict[str, dict[str, tuple[int, tuple[float, float, float, float]]]],
    tuple[float, float, float, float] | None,
    float,
    float,
]:
    found, width, height = words(path, page)
    rows: dict[str, dict[str, tuple[int, tuple[float, float, float, float]]]] = {}
    for name in [*COUNTIES, "TOTAL"]:
        parts = name.split()
        for index in range(len(found) - len(parts) + 2):
            if [w[4] for w in found[index : index + len(parts)]] != parts:
                continue
            top = found[index][1]
            numbers = [
                w
                for w in found[index + len(parts) :]
                if abs(w[1] - top) < 2 and re.fullmatch(r"\d{1,3}(,\d{3})*", w[4])
            ]
            if len(numbers) >= 2:
                rows[name] = {
                    column: (int(w[4].replace(",", "")), (w[0], w[1], w[2], w[3]))
                    for column, w in zip(columns, numbers[:2], strict=True)
                }
                break
    box = None
    if other is not None:
        word = next(w for w in found if w[4] == other)
        box = (word[0], word[1], word[2], word[3])
    return rows, box, width, height


def emit() -> Path:
    stored = find(ORIGIN, NEEDLE)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Wyoming governor by county from the Blue Book",
            polity="US",
        )
        columns = (
            "id",
            "election_id",
            "contest_id",
            "stage",
            "kind",
            "status",
            "source_id",
            "as_of",
            "completeness",
            "supersedes_id",
        )
        counties = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.county' and code like '56%'"
            ).fetchall()
        )
        for year, (page_number, contest, cols, other) in YEARS.items():
            prior = conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]
            ).fetchall()
            if len(prior) != 1:
                raise ValueError(f"{year}: expected one default set")
            if conn.execute(
                """select exists (select from result_value v join reporting_unit u on u.k = v.unit_k
                   join result_set r on r.k = v.result_set_k where r.id = %s and u.scheme_code = 'us.county')""",
                [prior[0][0]],
            ).fetchone()[0]:
                continue
            rows, other_box, width, height = lines(stored.path, page_number, cols, other)
            if set(rows) != {*COUNTIES, "TOTAL"}:
                raise ValueError(f"{year}: missing lines: {set(COUNTIES) - set(rows)}")
            for column in cols:
                if sum(rows[c][column][0] for c in COUNTIES) != rows["TOTAL"][column][0]:
                    raise ValueError(f"{year}: {column} counties do not add to the printed total")
            election, source_id = prior[0][1], prior[0][6]
            options = {
                label: option
                for option, kind, label in conn.execute(
                    """select o.id, o.kind::text, o.ballot_label from contest_option o
                       where o.contest_id = %s and exists (select from result_value v join result_set r on r.k = v.result_set_k
                                                           where v.option_k = o.k and r.id = %s)""",
                    [contest, prior[0][0]],
                ).fetchall()
            }
            by_column = {
                column: next(o for label, o in options.items() if label and surname(label) == column.lower())
                for column in cols
            }
            state = conn.execute(
                """select u.id from reporting_unit u join result_value v on v.unit_k = u.k
                   join result_set r on r.k = v.result_set_k where r.id = %s and u.scheme_code = 'us.state' limit 1""",
                [prior[0][0]],
            ).fetchone()[0]
            artifact = cs.artifact(stored, source_id)
            page = cs.page(artifact, page_number)
            result_set = cs.key(f"{SOURCE}.result_set", f"{year}:WY:governor")
            for old in prior:
                cs.add("result_set", **dict(zip(columns, old, strict=True)), is_default=False)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source_id,
                is_default=True,
                completeness=1,
                supersedes_id=prior[0][0],
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE)

            def value(
                unit: UUID,
                option: UUID,
                votes: int,
                box: tuple[float, float, float, float],
                result_set: UUID = result_set,
                page: UUID = page,
                width: float = width,
                height: float = height,
            ) -> None:
                key = {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": option,
                    "channel": 0,
                    "measure": 1,
                    "count_round": 0,
                }
                cs.add("result_value", **key, flags=8, value=votes)
                cs.add(
                    "value_locator",
                    **key,
                    page_id=page,
                    source_id=None,
                    x0=box[0] / width,
                    y0=box[1] / height,
                    x1=box[2] / width,
                    y1=box[3] / height,
                )

            for index, county in enumerate(COUNTIES):
                for column in cols:
                    votes, box = rows[county][column]
                    value(counties[f"56{index * 2 + 1:03d}"], by_column[column], votes, box)
            for column in cols:
                votes, box = rows["TOTAL"][column]
                value(state, by_column[column], votes, box)
            if other is not None and other_box is not None:
                value(
                    state,
                    next(o for label, o in options.items() if label == "Other votes"),
                    int(other.replace(",", "")),
                    other_box,
                )
            cs.add(
                "outcome",
                result_set_id=result_set,
                option_id=by_column[cols[0]],
                kind="elected",
                seats=1,
            )
        return cs.write()
