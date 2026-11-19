"""Pakistan's by-elections of 14 October 2018, by constituency, from the Election Commission's scanned Forms 49.

The Commission published the scanned result forms of each by-election constituency (Forms 45, 46, 48, and 49) in
shared folders linked from its page "Bye Elections 2018 Scanned Forms". Each Form 49 is the final consolidated result
of one constituency: the registered voters, each candidate's valid votes, and the valid and rejected votes in all. The
scans carry no text layer, so each figure was read from the page image, and each is stored with its page and the box of
its cell. The candidates' votes were checked against the valid votes the form prints, and the registered voters
against the sum of the male and female voters it prints.

Only the constituencies whose folders hold a legible Form 49 of the 14 October 2018 polls are stored. The folders of
the later polls of 2019 and 2021 (PP-218, NA-75, PP-38, PP-84) hold forms of other days, which are not stored here.
The positions of the candidates and their cell boxes are in `ecp2018bye.json`."""

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

SOURCE = "pk.ecp2018bye"
PAKISTAN = ps2018.PAKISTAN
DAY = date(2018, 10, 14)
STAMP = DAY.isoformat()
DRIVE = "https://drive.usercontent.google.com/download?id={}&export=download"
FOLDER = "https://drive.google.com/embeddedfolderview?id=15ucYQNz1kY4iCPF8WtpMu_K3Qa_AcM78"
ECP_PAGE = "https://web.archive.org/web/20251121100017id_/https://ecp.gov.pk/general-elections-2018-scanned-forms-and-returned-candidates-notification"
ECP_SITE = "https://ecp.gov.pk/bye-elections-2018-scanned-forms"
VOTES, REGISTERED, VALID, INVALID = 1, 2, 4, 5
DIGITIZED = 8
PACKAGES = [f"{SOURCE}/registry", f"{SOURCE}/{STAMP}"]
FORMS: list[dict] = json.loads((Path(__file__).parent / "ecp2018bye.json").read_text())
PAGES = raw.DATA / "pages" / SOURCE


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


DATASET = key("source", "ecp-scans")
SYSTEM = key("electoral_system", "fptp")
# The bodies of the general election's package, mapped to this package's own.
BODIES = {a.body: key("body", prefix) for prefix, a in ps2018.ASSEMBLIES.items() if prefix in ("NA", "PP")}


def _url(form: dict, index: int) -> str:
    return DRIVE.format(form["files"][index]["drive"])


def _not_html(path: Path) -> None:
    if path.read_bytes()[:64].lstrip().lower().startswith((b"<!doctype", b"<html")):
        raise ValueError("the host returned a web page, not the scan")


def fetch() -> None:
    raw.fetch(SOURCE, FOLDER, suffix=".html")
    for form in FORMS:
        for index, file in enumerate(form["files"]):
            raw.fetch(SOURCE, _url(form, index), suffix=file["suffix"], check=_not_html)


def parse() -> None:
    registry().write()
    election().write()


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
        "The parties of the by-elections of 14 October 2018 to Pakistan's National Assembly and Punjab's Provincial "
        "Assembly, and the Election Commission's scanned result forms they come from",
    )
    p.external |= {PAKISTAN}
    p.add(
        "source",
        id=DATASET,
        kind="official_body",
        publisher="Election Commission of Pakistan",
        title="Bye Elections 2018: scanned Forms 45, 46, 48, and 49",
        url=ECP_SITE,
        license="Official publication; reuse terms are not stated",
        attribution="Election Commission of Pakistan",
    )
    p.artifact(raw.cached(SOURCE, FOLDER), DATASET)
    p.artifact(raw.cached(SOURCE, ECP_PAGE), DATASET)
    for form in FORMS:
        for index in range(len(form["files"])):
            p.artifact(raw.cached(SOURCE, _url(form, index)), DATASET)
    for prefix, assembly in ps2018.ASSEMBLIES.items():
        if prefix in ("NA", "PP"):
            body = BODIES[assembly.body]
            p.add("body", id=body, polity_id=PAKISTAN, kind=assembly.kind)
            p.name(body, assembly.name, "en", "official")
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


def election() -> Package:
    p = Package(
        f"{SOURCE}/{STAMP}",
        SOURCE,
        "Pakistan's by-elections of 14 October 2018 to the National Assembly and the Provincial Assembly of the "
        "Punjab, by constituency, from the Election Commission's scanned Forms 49",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {PAKISTAN, "pk.na", ps2018.PA_SCHEME}
    p.uses(raw.cached(SOURCE, FOLDER))
    p.uses(raw.cached(SOURCE, ECP_PAGE))
    election = key("election", STAMP)
    p.add("election", id=election, polity_id=PAKISTAN, kind="by_election", date_from=DAY, date_to=DAY, status="held")
    p.add("slug", path=f"pk/{STAMP}-by-election", entity_id=election, is_current=True)
    p.name(election, "2018 Pakistani by-elections", "en", "official")
    p.add(
        "note",
        id=key("note", election, "coverage"),
        entity_id=election,
        kind="context",
        body_md="The Commission held by-elections on 14 October 2018 for seats of the National Assembly and the "
        "provincial assemblies left vacant by the general election of 25 July 2018. The Form 49 of each "
        "constituency below is signed by its returning officer between 16 and 19 October 2018. The Commission's "
        "folders hold a legible Form 49 for only some of the constituencies polled, and those are stored here.",
        lang="en",
    )
    values: list[dict] = []
    locators: list[dict] = []
    declared: list[tuple[str, str | None, int, int]] = []
    for form in FORMS:
        code = form["code"]
        prefix = code[:2]
        assembly = ps2018.ASSEMBLIES["NA" if prefix == "NA" else "PP"]
        name = form["name"]
        label = f"{code} {name}"
        contest = key("contest", STAMP, code)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=BODIES[assembly.body],
            magnitude=1,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{assembly.title} by-election, {label}", "en", "official")
        p.add("slug", path=f"pk/{STAMP}-by-election/{code.lower()}", entity_id=contest, is_current=True)
        unit = key("unit", STAMP, code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=PAKISTAN,
            scheme_code="pk.na" if prefix == "NA" else ps2018.PA_SCHEME,
            code=f"{STAMP}:{code}",
            area_relation="unlocated",
            kind="standard",
        )
        p.name(unit, label, "en", "official")
        result_set = key("result_set", STAMP, code)
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
            id = key("page", code, index)
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
            option = key("option", STAMP, code, position)
            person = key("person", STAMP, code, position)
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
                id=key("candidacy", STAMP, code, position),
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
    "The final consolidated result of the constituency (Form 49) issued by the Election Commission of Pakistan for the "
    "by-election of 14 October 2018, read from the Commission's scanned form. The scan has no text layer, so each "
    "figure was read from the page image and is stored with its page and the box of its cell. Registered voters are "
    "the total the form prints for the constituency; the candidates' votes were checked against the valid votes the "
    "form prints. The form does not name the winner, and none is stored."
)
