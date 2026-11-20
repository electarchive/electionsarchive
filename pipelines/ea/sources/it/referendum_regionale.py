"""Regional referendums held under a region's own law, from the regions' results sites.

Lombardy's consultative referendum on greater autonomy (22 October 2017) comes from the Region's referendum site, which
publishes one JSON tally per municipality, per province, and for the whole region. Sardinia's consultative referendum
of 15 and 16 May 2011 on nuclear power plants and radioactive waste comes from the Region's results pages, by
municipality.

Production already holds each region's polity and area and every municipal reporting unit (written by `it.interno`),
so these packages mint no places; they reference them. Neither site publishes below the municipality.
"""

import csv
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid5

from ea import package, raw
from ea.package import NAMESPACE, Package

SOURCE = "it.referendum_regionale"
HERE = Path(__file__).parent
ITALY = "01a13bf7-2f71-7303-964e-fdd9421ca0b6"
LICENSE = (
    "No license stated. Data that Italian public bodies publish without a license are open by default (art. 52 of "
    "the Codice dell'amministrazione digitale)."
)
LOMBARDY = str(uuid5(NAMESPACE, "polity\x1fIT-25"))
SARDINIA = str(uuid5(NAMESPACE, "polity\x1fIT-88"))
LOM = "https://referendum.regione.lombardia.it"
SAR = "https://old.regione.sardegna.it"
SAR_PAGE = f"{SAR}/j/v/1849?&s=1&v=9&c=9068&va=2&ref=1&nbci=1"
LOMBARDY_AREA = "01a13bf7-2f89-7329-a8e6-bbf74e070566"
SARDINIA_AREA = "01a13bf7-2f89-7329-a8e6-bc0848c79c1a"
# The ISTAT province prefix of each Lombardy province, by the prefix of the Region's own municipality codes.
PROVINCES = {
    "101": "016",
    "102": "017",
    "103": "015",
    "104": "013",
    "105": "019",
    "106": "097",
    "107": "098",
    "108": "020",
    "109": "108",
    "110": "018",
    "111": "014",
    "112": "012",
}
# Names the Region's pages print that differ from the ones production holds.
LOM_ALIASES = {"SAN GIORGIO DI MANTOVA": "SAN GIORGIO BIGARELLO"}
SAR_DETAIL = f"{SAR}/referendum2011/quesito1/dettaglio/"
ELECTORS, VOTERS, VALID, INVALID, BLANK, VOTES = 2, 3, 4, 5, 6, 1


def key(kind: str, *parts: object) -> str:
    return str(uuid5(NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.replace("’", "'").replace("`", "'"))
    text = "".join(c for c in text if not unicodedata.combining(c)).upper()
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", text.replace("'", ""))).strip()


# ---------------------------------------------------------------- Lombardy


def lom_json(path: str) -> dict[str, Any]:
    value = json.loads(raw.cached(SOURCE, f"{LOM}/{path}").path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(path)
    return value


def lom_places() -> list[tuple[str, str, str, str]]:
    """(province name, comune name, ISTAT-style site code, tally path) for every comune."""
    root = lom_json("data/navigation/root.json")
    out: list[tuple[str, str, str, str]] = []
    for province in dict(root["subRegions"]).values():
        page = lom_json(province["url"])
        for comune in dict(page["subRegions"]).values():
            tail = comune["url"].removeprefix("data/navigation/").removesuffix(".json")
            level, ident = tail.split("/")[0], tail.split("/")[1]
            out.append(
                (province["name"], comune["name"], comune["customCode"], f"data/results/tallies/{level}/{ident}/1.json")
            )
    return out


def fetch() -> None:
    root_url = f"{LOM}/data/navigation/root.json"
    raw.fetch(SOURCE, root_url, suffix=".json")
    root = lom_json("data/navigation/root.json")
    provinces = [f"{LOM}/{p['url']}" for p in dict(root["subRegions"]).values()]
    raw.fetch_many(SOURCE, provinces, suffix=".json")
    places = lom_places()
    urls = [f"{LOM}/data/results/tallies/0/1/1.json"]
    urls += [f"{LOM}/{path}" for *_, path in places]
    raw.fetch_many(SOURCE, urls, suffix=".json")
    raw.fetch(SOURCE, SAR_PAGE, suffix=".html")
    raw.fetch(SOURCE, SAR_DETAIL, suffix=".html")


# ---------------------------------------------------------------- shared pieces


@dataclass
class Count:
    name: str
    voters: int
    yes: int
    no: int
    blank: int
    invalid: int | None = None
    unit: str = ""
    area: str = ""


def source_id(name: str) -> str:
    return key("source", name)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The sources and the consultative-referendum rules of the regional referendums of Lombardy in 2017 and "
        "Sardinia in 2011",
    )
    p.external |= {ITALY, LOMBARDY, SARDINIA}
    p.add(
        "source",
        id=source_id("lombardia"),
        kind="official_body",
        publisher="Regione Lombardia",
        title="Referendum consultivo per l'autonomia della Lombardia, 22 ottobre 2017: risultati",
        url=LOM,
        license=LICENSE,
        attribution="Regione Lombardia",
    )
    p.add(
        "source",
        id=source_id("sardegna"),
        kind="official_body",
        publisher="Regione Autonoma della Sardegna",
        title="Referendum consultivo regionale del 15 e 16 maggio 2011: risultati",
        url=f"{SAR}/argomenti/attivita_istituzionali/referendum2011/",
        license=LICENSE,
        attribution="Regione Autonoma della Sardegna",
    )
    for polity in (LOMBARDY, SARDINIA):
        id = key("electoral_system", polity)
        p.add("electoral_system", id=id, polity_id=polity, rules={"formula": "referendum"}, rules_version=1)
        p.add(
            "note",
            id=key("note", id),
            entity_id=id,
            kind="methodology",
            body_md="A consultative referendum of the region's voters under the region's own law: the answer with "
            "more valid votes expresses the region's opinion, and no quorum applies.",
            lang="en",
        )
    return p


def units(file: str) -> list[dict[str, str]]:
    with (HERE / file).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def referendum(
    p: Package,
    *,
    polity: str,
    region_area: str,
    source: str,
    first: date,
    last: date,
    path: str,
    names: tuple[str, str],
    question: tuple[str, str],
    slug: str,
    labels: tuple[str, str],
    counts: list[Count],
    total: Count,
    electors: int | None,
    method: str,
    stage: str,
) -> None:
    """One election with one consultative question, with values by municipality and declared regional totals."""
    election = key("election", polity, first)
    p.add(
        "election",
        id=election,
        polity_id=polity,
        kind="referendum",
        date_from=first,
        date_to=last,
        status="held",
    )
    p.name(election, names[0], "en", "official")
    p.name(election, names[1], "it", "native", False)
    p.add("slug", path=path, entity_id=election, is_current=True)
    contest = key("contest", polity, first)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="ballot_question",
        district_area_id=region_area,
        round_number=1,
        electoral_system_id=key("electoral_system", polity),
        default_result_kind="counted",
    )
    p.add("ballot_question", contest_id=contest, type="plebiscite")
    p.name(contest, question[0], "en", "official")
    p.name(contest, question[1], "it", "native", False)
    p.add("slug", path=f"{path}/{slug}", entity_id=contest, is_current=True)
    options = {}
    for position, (answer, label) in enumerate((("yes", labels[0]), ("no", labels[1])), 1):
        options[answer] = key("option", polity, first, answer)
        p.add(
            "contest_option",
            id=options[answer],
            contest_id=contest,
            kind="answer",
            ballot_position=position,
            ballot_label=label,
        )
        p.name(options[answer], answer.title(), "en", "english")
    result_set = key("result_set", polity, first)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage=stage,
        kind="counted",
        source_id=source,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")

    def put(unit: str, option: str | None, measure: int, value: int) -> None:
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=0,
            value=value,
        )

    for count in counts:
        put(count.unit, None, VOTERS, count.voters)
        put(count.unit, None, VALID, count.yes + count.no)
        put(count.unit, None, BLANK, count.blank)
        if count.invalid is not None:
            put(count.unit, None, INVALID, count.invalid)
        put(count.unit, options["yes"], VOTES, count.yes)
        put(count.unit, options["no"], VOTES, count.no)

    def declare(option: str | None, measure: int, value: int) -> None:
        p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=value)

    declare(None, VOTERS, total.voters)
    declare(None, BLANK, total.blank)
    if total.invalid is not None:
        declare(None, INVALID, total.invalid)
    declare(options["yes"], VOTES, total.yes)
    declare(options["no"], VOTES, total.no)
    if electors is not None:
        declare(None, ELECTORS, electors)
    yes_won = total.yes > total.no
    p.add("outcome", result_set_id=result_set, option_id=options["yes"], kind="won" if yes_won else "lost")
    p.add("outcome", result_set_id=result_set, option_id=options["no"], kind="lost" if yes_won else "won")


def sums(counts: list[Count]) -> dict[str, int]:
    return {
        "voters": sum(c.voters for c in counts),
        "yes": sum(c.yes for c in counts),
        "no": sum(c.no for c in counts),
        "blank": sum(c.blank for c in counts),
        "invalid": sum(c.invalid or 0 for c in counts),
    }


def differences(counts: list[Count], total: Count) -> list[str]:
    mine = sums(counts)
    out = []
    for name, value in mine.items():
        theirs = getattr(total, name)
        if theirs is not None and value != theirs:
            out.append(f"{name} {value:,} by municipality against {theirs:,} for the region")
    return out


# ---------------------------------------------------------------- Lombardy


def lom_count(path: str, name: str) -> Count:
    tally = lom_json(path)
    stats = tally["stats"]
    votes = {r["bName"]: r["votes"] for r in tally["results"]}
    return Count(
        name,
        int(stats["totalVotes"]),
        int(votes["SI"]),
        int(votes["NO"]),
        int(votes["scheda bianca"]),
    )


def lom_units(places: list[tuple[str, str, str, str]]) -> tuple[list[Count], list[tuple[str, str]]]:
    """Each municipality's count with its production unit. A municipality merged out of existence since has no unit
    of its own; it is returned with the area of the municipality that absorbed it."""
    table = units("lombardia_comuni.tsv")
    by_name: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in table:
        by_name.setdefault((row["code"][:3], norm(row["name"])), []).append(row)
    counts: list[Count] = []
    merged: list[tuple[str, str]] = []
    for _, name, code, path in places:
        count = lom_count(path, name)
        prefix = PROVINCES[code[:3]]
        wanted = LOM_ALIASES.get(norm(name), norm(name))
        found = by_name.get((prefix, wanted), [])
        if not found:
            # Production files a few municipalities under another province than the Region does.
            found = [row for (_, other), rows in by_name.items() if other == wanted for row in rows]
        if len(found) > 1:
            found = [r for r in found if not r["valid_to"]] or found
        if not found:
            merged.append((name, prefix))
            count.unit = ""
        elif len(found) == 1:
            count.unit, count.area = found[0]["unit_id"], found[0]["area_id"]
        else:
            raise ValueError(f"{name} matches {len(found)} municipalities")
        counts.append(count)
    return counts, merged


def lombardy() -> Package:
    p = Package(
        f"{SOURCE}/2017-lombardia",
        SOURCE,
        "Lombardy's consultative referendum on greater autonomy of 22 October 2017, by municipality, from the "
        "Region's referendum site",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {ITALY, LOMBARDY, LOMBARDY_AREA, "it.comune"}
    p.artifact(raw.cached(SOURCE, f"{LOM}/data/results/tallies/0/1/1.json"), source_id("lombardia"))
    places = lom_places()
    cited: set[str] = set()
    for _, _, _, path in places:
        artifact = raw.cached(SOURCE, f"{LOM}/{path}")
        if artifact.storage_key not in cited:
            cited.add(artifact.storage_key)
            p.artifact(artifact, source_id("lombardia"))
        else:
            p.uses(artifact)
    counts, merged = lom_units(places)
    for name, prefix in merged:
        # Campospinoso, in the province of Pavia, merged into Campospinoso Albaredo in 2019.
        if (name, prefix) != ("CAMPOSPINOSO", "018"):
            raise ValueError(f"{name} has no unit in production")
    host = next(r for r in units("lombardia_comuni.tsv") if norm(r["name"]) == "CAMPOSPINOSO ALBAREDO")
    for count in counts:
        if count.unit:
            p.external.add(count.unit)
            continue
        count.unit = key("unit", "campospinoso")
        p.add(
            "reporting_unit",
            id=count.unit,
            polity_id=ITALY,
            scheme_code="it.comune",
            code="018025",
            area_relation="within",
            area_id=host["area_id"],
            kind="standard",
        )
        p.name(count.unit, "Campospinoso", "it", "official")
        p.external.add(host["area_id"])
    total = lom_count("data/results/tallies/0/1/1.json", "Lombardia")
    method = (
        "Counted by the municipal electoral offices and published by the Region as one JSON tally per municipality. "
        "The Region's tallies give each municipality's voters, blank ballots, and Yes and No votes; they print no "
        "registered voters and no invalid ballots, and the site's registered-voters field repeats the voters."
    )
    over = [c for c in counts if c.yes + c.no + c.blank != c.voters]
    if over:
        p.accept(
            "accounting.ballots_sum",
            "; ".join(
                f"{c.name.title()}'s tally prints {c.voters:,} voters but {c.yes:,} Yes, {c.no:,} No, and "
                f"{c.blank:,} blank ballots, {c.yes + c.no + c.blank - c.voters:,} more"
                for c in over
            )
            + ". The figures are stored as printed.",
        )
    wrong = differences(counts, total)
    if wrong:
        p.accept(
            "accounting.declared",
            "The Region's municipal tallies disagree with its regional tally: " + "; ".join(wrong) + ".",
        )
    referendum(
        p,
        polity=LOMBARDY,
        region_area=LOMBARDY_AREA,
        source=source_id("lombardia"),
        first=date(2017, 10, 22),
        last=date(2017, 10, 22),
        path="it/lombardia/2017-10-22-referendum",
        names=(
            "2017 Lombardy autonomy referendum",
            "Referendum consultivo sull'autonomia della Lombardia del 22 ottobre 2017",
        ),
        question=(
            (
                "Do you want the Region of Lombardy, in the framework of national unity, to take steps with the "
                "Government to obtain further forms and special conditions of autonomy?"
            ),
            (
                "Volete voi che la Regione Lombardia, nel quadro dell'unità nazionale, intraprenda le iniziative "
                "istituzionali necessarie per richiedere allo Stato l'attribuzione di ulteriori forme e condizioni "
                "particolari di autonomia?"
            ),
        ),
        slug="autonomy",
        labels=("Sì", "No"),
        counts=counts,
        total=total,
        electors=None,
        method=method,
        stage="official",
    )
    return p


# ---------------------------------------------------------------- Sardinia


def sar_number(text: str) -> int:
    return int(re.sub(r"[.\s]", "", text))


def sar_cells(page: str) -> list[list[str]]:
    """Each table row's cells, with line breaks inside a cell shown as a bar."""
    rows = []
    for block in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.DOTALL):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", block, re.DOTALL)
        rows.append(
            [
                re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<br\s*/?>", "|", cell))).replace(" |", "|").strip()
                for cell in cells
            ]
        )
    return rows


def sar_count(name: str, cells: list[str]) -> Count:
    """A row's voters, the Yes and No votes, and the blank and null ballots."""
    voters = re.search(r"\|\s*([\d.]+)", cells[0])
    yes, no = re.search(r"\(([\d.]+)\)", cells[2]), re.search(r"\(([\d.]+)\)", cells[3])
    if voters is None or yes is None or no is None:
        raise ValueError(f"{name}: the Sardinian row has changed")
    blank, invalid = (sar_number(x) for x in cells[4].split("|"))
    return Count(name, sar_number(voters[1]), sar_number(yes[1]), sar_number(no[1]), blank, invalid)


def sar_rows() -> tuple[list[Count], Count, int]:
    """Municipal counts, the regional count, and the regional electorate, from the results pages."""
    from ea.sources.it.sardegna import canon

    summary = sar_cells(raw.cached(SOURCE, SAR_PAGE).path.read_text(encoding="utf-8", errors="replace"))
    detail = sar_cells(raw.cached(SOURCE, SAR_DETAIL).path.read_text(encoding="utf-8", errors="replace"))
    top = next(cells for cells in summary if cells[0] == "REGIONALE")
    electors = sar_number(top[7])
    total = sar_count("Sardegna", next(c for c in detail if c[0] == "REGIONALE")[1:])
    ids = {canon(r["name"]): r["unit_id"] for r in units("sardegna_comuni.tsv")}
    out = []
    for cells in detail:
        if len(cells) == 7 and cells[0] == "-":
            count = sar_count(cells[1], cells[2:])
            count.unit = ids[canon(count.name)]
            out.append(count)
    if len(out) != 377 or len({c.unit for c in out}) != 377:
        raise ValueError(f"expected 377 municipalities, found {len(out)}")
    return out, total, electors


def sardinia() -> Package:
    p = Package(
        f"{SOURCE}/2011-sardegna",
        SOURCE,
        "Sardinia's consultative referendum on nuclear power of 15 and 16 May 2011, by municipality, from the "
        "Region's results pages",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {ITALY, SARDINIA, SARDINIA_AREA}
    for url in (SAR_PAGE, SAR_DETAIL):
        p.artifact(raw.cached(SOURCE, url), source_id("sardegna"))
    counts, total, electors = sar_rows()
    p.external |= {c.unit for c in counts}
    wrong = differences(counts, total)
    off = [c for c in counts if c.yes + c.no + c.blank + (c.invalid or 0) != c.voters]
    if off:
        gap = sum(c.voters - c.yes - c.no - c.blank - (c.invalid or 0) for c in off)
        p.accept(
            "accounting.ballots_sum",
            f"In {len(off)} municipalities the Region prints voters that differ from the Yes, No, blank, and null "
            f"ballots it prints for them, by {gap:,} ballots in all; the figures are stored as printed.",
        )
    ballots = total.yes + total.no + total.blank + (total.invalid or 0)
    if ballots != total.voters:
        wrong.append(
            f"the regional row's Yes, No, blank, and null ballots add up to {ballots:,}, "
            f"{total.voters - ballots:,} short of its {total.voters:,} voters"
        )
    if wrong:
        p.accept(
            "accounting.declared",
            "The Region's own figures disagree: " + "; ".join(wrong) + ". The figures are stored as printed.",
        )
    method = (
        "Counted by the municipal electoral offices and published by the Region, which prints each municipality's "
        "voters, Yes and No votes, and blank and null ballots, and the region's registered voters. The Region prints "
        "no registered voters by municipality, so none are stored below the region."
    )
    referendum(
        p,
        polity=SARDINIA,
        region_area=SARDINIA_AREA,
        source=source_id("sardegna"),
        first=date(2011, 5, 15),
        last=date(2011, 5, 16),
        path="it/sardegna/2011-05-15-referendum",
        names=(
            "2011 Sardinian nuclear power referendum",
            "Referendum consultivo regionale del 15 e 16 maggio 2011 sul nucleare in Sardegna",
        ),
        question=(
            (
                "Are you opposed to the installation in Sardinia of nuclear power plants and of sites for storing "
                "radioactive waste, whether left over from them or already existing?"
            ),
            (
                "Sei contrario all'installazione in Sardegna di centrali nucleari e di siti per lo stoccaggio di scorie "
                "radioattive da esse residuate o preesistenti?"
            ),
        ),
        slug="nuclear",
        labels=("Sì", "No"),
        counts=counts,
        total=total,
        electors=electors,
        method=method,
        stage="official",
    )
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/registry", f"{SOURCE}/2017-lombardia", f"{SOURCE}/2011-sardegna"]


def parse() -> None:
    registry().write()
    lombardy().write()
    sardinia().write()


def emit() -> None:
    package.run("load", packages())
