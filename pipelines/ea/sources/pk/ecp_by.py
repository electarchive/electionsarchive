"""Pakistan's National and Provincial Assembly by-elections held after those of 14 October 2018, by constituency, from
the Election Commission's scanned Forms 49.

The Commission published the scanned result forms of the by-elections it held in the term of the assemblies elected
on 25 July 2018 (Forms 45, 46, 47, 48, and 49) in shared folders. The folder of its page "Bye Elections 2018 Scanned
Forms" also holds the forms of four later polls: PP-218 Multan-VIII (31 March 2019), NA-75 Sialkot-IV (the re-poll of
10 April 2021), PP-84 Khushab-III (5 May 2021), and PP-38 Sialkot-IV (28 July 2021). Each Form 49 is the final
consolidated result of one constituency: the registered voters, each candidate's valid votes, and the valid and
rejected votes in all. The scans carry no text layer, so each figure was read from the page image, and each is stored
with its page and the box of its cell. The candidates' votes were checked against the valid votes the form prints. The
poll day of each is the date of its provisional result (Form 47), or the day before it when the returning officer
signed it after midnight.

The positions of the candidates and their cell boxes are in `ecp_by.json`."""

import json
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl
from PIL import Image

from ea import package, raw
from ea.package import Package
from ea.sources.pk import ps2018

SOURCE = "pk.ecp_by"
PAKISTAN = ps2018.PAKISTAN
DRIVE = "https://drive.usercontent.google.com/download?id={}&export=download"
LISTING = "https://drive.google.com/embeddedfolderview?id={}"
# The folder of "Bye Elections 2018 Scanned Forms", and its folders of National and Provincial Assembly seats.
ROOT = "15ucYQNz1kY4iCPF8WtpMu_K3Qa_AcM78"
BRANCHES = {"NA": "1Abh4gQbnuSr0B_dJQNbDERrzLLohJ_Jm", "PP": "1da38sy2lxeDnnRBGyvOHGzlfWYa50gxl"}
ECP_SITE = "https://ecp.gov.pk/bye-elections-2018-scanned-forms"
VOTES, REGISTERED, VALID, INVALID = 1, 2, 4, 5
DIGITIZED = 8
FORMS: list[dict] = json.loads((Path(__file__).parent / "ecp_by.json").read_text())
DAYS = sorted({form["date"] for form in FORMS})
PACKAGES = [f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in DAYS)]
PAGES = raw.DATA / "pages" / SOURCE


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


DATASET = key("source", "ecp-scans")
SYSTEM = key("electoral_system", "fptp")
# The assemblies elected on 25 July 2018, which the general election's pipeline already holds.
BODIES = {prefix: ps2018.ASSEMBLIES[prefix] for prefix in ("NA", "PP")}

CONTEXT = {
    "2019-03-31": "The Commission held a by-election on 31 March 2019 for PP-218 Multan-VIII in the Provincial "
    "Assembly of the Punjab. The returning officer signed its Form 49 on 1 April 2019.",
    "2021-04-10": "The Commission voided the by-election of 19 February 2021 for NA-75 Sialkot-IV in the National "
    "Assembly and ordered a fresh poll in the whole constituency, held on 10 April 2021. This is the result of that "
    "re-poll; the returning officer signed its Form 49 on 11 April 2021.",
    "2021-05-05": "The Commission held a by-election on 5 May 2021 for PP-84 Khushab-III in the Provincial Assembly "
    "of the Punjab. The returning officer signed its Form 49 on 6 May 2021.",
    "2021-07-28": "The Commission held a by-election on 28 July 2021 for PP-38 Sialkot-IV in the Provincial Assembly "
    "of the Punjab. The returning officer signed its Form 49 on 29 July 2021.",
}


def _url(form: dict, index: int) -> str:
    return DRIVE.format(form["files"][index]["drive"])


def _listings() -> list[str]:
    folders = [ROOT, *BRANCHES.values(), *sorted({form["folder"] for form in FORMS})]
    return [LISTING.format(folder) for folder in folders]


def _not_html(path: Path) -> None:
    if path.read_bytes()[:64].lstrip().lower().startswith((b"<!doctype", b"<html")):
        raise ValueError("the host returned a web page, not the scan")


def fetch() -> None:
    for url in _listings():
        raw.fetch(SOURCE, url, suffix=".html")
    for form in FORMS:
        for index, file in enumerate(form["files"]):
            raw.fetch(SOURCE, _url(form, index), suffix=file["suffix"], check=_not_html)


def parse() -> None:
    registry().write()
    for day in DAYS:
        election(day).write()


def emit() -> None:
    package.run("load", PACKAGES)


def packages() -> list[str]:
    return PACKAGES


def party_id(name: str) -> str:
    return key("party", name)


def _render(form: dict, index: int) -> tuple[str, int, int]:
    """The page image, a PNG named for the file's hash and page number, and its size."""
    page = form["pages"][index]
    artifact = raw.cached(SOURCE, _url(form, page["file"]))
    image = PAGES / f"{artifact.sha256}-{page['page']}.png"
    if not image.exists():
        PAGES.mkdir(parents=True, exist_ok=True)
        if artifact.path.suffix == ".pdf":
            with tempfile.TemporaryDirectory() as folder:
                subprocess.run(
                    [
                        "pdftoppm",
                        "-r",
                        "150",
                        "-png",
                        "-f",
                        str(page["page"]),
                        "-l",
                        str(page["page"]),
                        "-singlefile",
                        str(artifact.path),
                        f"{folder}/p",
                    ],
                    check=True,
                )
                Image.open(f"{folder}/p.png").convert("RGB").save(image)
        else:
            picture = Image.open(artifact.path).convert("RGB")
            picture.thumbnail((2400, 3400))
            picture.save(image)
    width, height = Image.open(image).size
    return f"pages/{SOURCE}/{image.name}", width, height


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The parties of Pakistan's National and Provincial Assembly by-elections of 2019 to 2021 whose scanned "
        "result forms the Election Commission filed with those of 2018, and the forms they come from",
    )
    p.external |= {PAKISTAN}
    p.add(
        "source",
        id=DATASET,
        kind="official_body",
        publisher="Election Commission of Pakistan",
        title="Bye Elections 2018: scanned Forms 45, 46, 47, 48, and 49, with those of later by-elections",
        url=ECP_SITE,
        license="Official publication; reuse terms are not stated",
        attribution="Election Commission of Pakistan",
    )
    for url in _listings():
        p.artifact(raw.cached(SOURCE, url), DATASET)
    for form in FORMS:
        for index in range(len(form["files"])):
            p.artifact(raw.cached(SOURCE, _url(form, index)), DATASET)
    p.add("electoral_system", id=SYSTEM, polity_id=PAKISTAN, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Each general constituency of the National Assembly and of the provincial assemblies elects one "
        "member by plurality, and a by-election fills a vacated seat the same way.",
        lang="en",
    )
    names = sorted({c["party"] for form in FORMS for c in form["candidates"]} - {"Independent"})
    for name in names:
        id = party_id(name)
        p.add("party", id=id, kind="party", polity_id=PAKISTAN)
        p.name(id, name, "en", "official")
    return p


def election(day: str) -> Package:
    forms = [form for form in FORMS if form["date"] == day]
    when = date.fromisoformat(day)
    seats = ", ".join(f"{form['code']} {form['name']}" for form in forms)
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"Pakistan's by-election of {when.day} {when:%B %Y} ({seats}), from the Election Commission's scanned Form 49",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {PAKISTAN, "pk.na", ps2018.PA_SCHEME}
    for url in _listings():
        p.uses(raw.cached(SOURCE, url))
    election = key("election", day)
    p.add("election", id=election, polity_id=PAKISTAN, kind="by_election", date_from=when, date_to=when, status="held")
    p.add("slug", path=f"pk/{day}-by-election", entity_id=election, is_current=True)
    p.name(election, f"Pakistani by-election of {when.day} {when:%B %Y}", "en", "official")
    p.add(
        "note",
        id=key("note", election, "coverage"),
        entity_id=election,
        kind="context",
        body_md=CONTEXT[day],
        lang="en",
    )
    values: list[dict] = []
    locators: list[dict] = []
    declared: list[tuple[str, str | None, int, int]] = []
    for form in forms:
        code = form["code"]
        prefix = code[:2]
        assembly = BODIES[prefix]
        p.external.add(assembly.body)
        label = f"{code} {form['name']}"
        contest = key("contest", day, code)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=assembly.body,
            magnitude=1,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{assembly.title} by-election, {label}", "en", "official")
        p.add("slug", path=f"pk/{day}-by-election/{code.lower()}", entity_id=contest, is_current=True)
        unit = key("unit", day, code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=PAKISTAN,
            scheme_code="pk.na" if prefix == "NA" else ps2018.PA_SCHEME,
            code=f"{day}:{code}",
            area_relation="unlocated",
            kind="standard",
        )
        p.name(unit, label, "en", "official")
        result_set = key("result_set", day, code)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=DATASET,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        pages: list[str] = []
        for index, page in enumerate(form["pages"]):
            image, width, height = _render(form, index)
            artifact = raw.cached(SOURCE, _url(form, page["file"]))
            id = key("page", day, code, index)
            pages.append(id)
            p.uses(artifact)
            p.add(
                "source_page",
                id=id,
                artifact_id=str(uuid5(package.NAMESPACE, f"source_artifact\x1f{DATASET}\x1f{artifact.storage_key}")),
                page_number=page["page"],
                image_key=image,
                width=width,
                height=height,
            )

        def put(option: str | None, measure: int, value: int, page: int, box: list[float]) -> None:
            values.append(
                {"result_set_id": result_set, "unit_id": unit, "option_id": option, "measure": measure, "value": value}
            )
            locators.append(
                {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": option,
                    "measure": measure,
                    "page_id": pages[page],
                    "x0": box[0],
                    "y0": box[1],
                    "x1": box[2],
                    "y1": box[3],
                }
            )
            declared.append((result_set, option, measure, value))

        for field, measure in (("registered", REGISTERED), ("valid", VALID), ("invalid", INVALID)):
            box = form[f"{field}_box"]
            put(None, measure, form[field], box[0], box[1:])
        for c in form["candidates"]:
            position = c["position"]
            option = key("option", day, code, position)
            person = key("person", day, code, position)
            independent = c["party"] == "Independent"
            p.add("person", id=person)
            p.name(person, c["name"], "en", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=position,
                ballot_label=c["name"],
            )
            p.add(
                "candidacy",
                id=key("candidacy", day, code, position),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=c["name"],
                member_of_party_id=None if independent else party_id(c["party"]),
                is_independent=independent,
            )
            put(option, VOTES, c["votes"], c["page"], c["box"])
    facts = pl.DataFrame(values, schema_overrides={"option_id": pl.String}).with_columns(
        channel=pl.lit(0, pl.Int16),
        count_round=pl.lit(0, pl.Int16),
        flags=pl.lit(DIGITIZED, pl.Int16),
        measure=pl.col("measure").cast(pl.Int16),
    )
    p.frame("result_value", facts)
    where = pl.DataFrame(locators, schema_overrides={"option_id": pl.String}).with_columns(
        channel=pl.lit(0, pl.Int16),
        count_round=pl.lit(0, pl.Int16),
        measure=pl.col("measure").cast(pl.Int16),
        source_id=pl.lit(None, pl.String),
    )
    p.frame("value_locator", where)
    for rs, option, measure, value in declared:
        p.add("declared", result_set_id=rs, option_id=option, measure=measure, value=value)
    return p


METHOD = (
    "The final consolidated result of the constituency (Form 49) issued by the Election Commission of Pakistan for "
    "the by-election, read from the Commission's scanned form. The scan has no text layer, so each figure was read "
    "from the page image and is stored with its page and the box of its cell. Registered voters are the total the "
    "form prints for the constituency; the candidates' votes add up to the valid votes the form prints. Candidates' "
    "names are spelled as the form prints them. The form does not name the winner, and none is stored."
)
