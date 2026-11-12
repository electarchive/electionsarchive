import html
import re
import struct
import subprocess
import tempfile
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "gb.neighborhood_plans"
VERSION = "1"
ROWS = Path(__file__).with_name("neighborhood_referendums.tsv")
SCANS = Path(__file__).with_name("neighborhood_scans.tsv")
PAGE_IMAGES = DATA / "pages" / SOURCE
SCHEME = "gb.neighborhood_area"
VOTES, REGISTERED, BALLOTS, INVALID, VOTERS = 1, 2, 3, 5, 14
SHARE_ONLY, DIGITIZED = 4, 8
PAGE_PREFIXES = (
    "https://www.stratford.gov.uk/news/",
    "https://www.stratford.gov.uk/planning-building/",
    "https://www.west-norfolk.gov.uk/info/",
    "https://somerset.gov.uk/news/",
    "https://netherstowey-pc.gov.uk/news/",
    "https://www.eastherts.gov.uk/planning-building/",
    "https://www.hart.gov.uk/news/",
    "https://www.eastherts.gov.uk/planning-and-building/",
    "https://web.archive.org/web/",
    "https://www.warwickdc.gov.uk/info/",
    "https://www.ashford.gov.uk/your-council/",
    "https://www.ambervalley.gov.uk/",
    "https://www.nwleics.gov.uk/pages/",
    "https://elections.shropshire.gov.uk/",
    "https://hardenvillagecouncil.gov.uk/",
    "https://www.middevon.gov.uk/your-council/",
    "https://www.cherwell.gov.uk/info/",
    "https://www.bassetlaw.gov.uk/council-and-democracy/",
    "https://www.north-norfolk.gov.uk/info/",
    "https://eastcambs.gov.uk/about-council/",
)
HANDWRITTEN = (
    "https://info.ambervalley.gov.uk/docarc/",
    "/ashby_de_la_zouch_neighbourhood_plan_referendum_result/",
    "/ellistown_and_battleflat_neighbourhood_plan_referendum_result/",
    "/lockington_cum_hemington_neighbourhood_plan_referendum_result/",
    "/download/downloads/id/4521/",
    "/download/downloads/id/8012/",
    "/download/downloads/id/8854/",
    "lanliveryparishcouncil.gov.uk/data/uploads/570_1456565066.pdf",
)
PAGES = {
    "E06000052": (
        "https://www.cornwall.gov.uk/the-council-and-democracy/elections/elections-and-referendums/"
        "neighbourhood-plan-referendum-results"
    ),
    "E07000084": "https://www.basingstoke.gov.uk/NP-referendum-results",
    "E06000061": (
        "https://www.northnorthants.gov.uk/elections-and-voting/election-results/"
        "neighbourhood-planning-referendum-results"
    ),
    "E07000085": "https://www.easthants.gov.uk/elections/election-results",
    "E07000171": (
        "https://www.bassetlaw.gov.uk/council-and-democracy/elections-in-bassetlaw/elections-information/"
        "past-election-results/election-results-2021/neighbourhood-plan-referendum-results-2021/"
    ),
    "E07000070": (
        "https://www.chelmsford.gov.uk/voting-and-elections/results-of-elections/"
        "neighbourhood-planning-referendum-results"
    ),
}
DOCUMENTS = "https://elections.wiltshire.gov.uk/Home/Document/"
OFFICE = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.oasis.opendocument.text": ".odt",
}
HTML = (
    "/declaration-result-poll-clapham-cum-newby-neighbourhood-plan-area",
    "-declaration-result-poll",
    "-neighbourhood-planning-referendum-results-15-december-2022",
    "/sherington-neighbourhood-plan",
    "/woughton-community-council-neighbourhood-plan",
)
CONTEXT = {
    "residential": (
        "A neighborhood planning referendum under Schedule 4B of the Town and Country Planning Act 1990 and the "
        "Neighbourhood Planning (Referendums) Regulations 2012. Voters registered in the neighborhood area were "
        "asked whether {council} should use the neighborhood plan to help it decide planning applications there; "
        "the council makes the plan part of its development plan if more than half of those voting say Yes."
    ),
    "business": (
        "A business referendum under Schedule 4B of the Town and Country Planning Act 1990, held with the "
        "residential referendum in a neighborhood area designated as a business area. Non-domestic ratepayers in "
        "the area were asked whether {council} should use the neighborhood plan to help it decide planning "
        "applications there."
    ),
}
WITHIN = (
    "The neighborhood area is not a single parish, so it is shown within {council}'s area rather than drawn on its own."
)


def is_page(url: str) -> bool:
    return url in PAGES.values() or "/w/" in url or url.startswith(PAGE_PREFIXES) or url.endswith(HTML)


def document(url: str) -> None:
    try:
        raw.cached(SOURCE, url)
    except LookupError:
        response = httpx.get(url, headers={"User-Agent": raw.USER_AGENT}, follow_redirects=True, timeout=600)
        response.raise_for_status()
        media_type = response.headers["content-type"].split(";")[0]
        raw.store(
            SOURCE,
            url,
            response.content,
            suffix=OFFICE[media_type],
            retrieved_at=datetime.now(UTC),
            media_type=media_type,
        )


def fetch() -> None:
    for url in PAGES.values():
        raw.fetch(SOURCE, url, suffix=".html")
    for url in pl.read_csv(ROWS, separator="\t", infer_schema=False)["url"]:
        if url.startswith(DOCUMENTS) or url.endswith(".docx"):
            document(url)
        else:
            raw.fetch(SOURCE, url, suffix=".jpg" if url.endswith(".jpg") else ".html" if is_page(url) else ".pdf")


def printed(url: str) -> tuple[str, bool, set[str]]:
    path = raw.cached(SOURCE, url).path
    pdf = path.suffix in OFFICE.values() or path.suffix == ".jpg"
    if path.suffix == ".jpg":
        text = subprocess.run(["tesseract", str(path), "-"], capture_output=True, text=True, check=True).stdout
    elif path.suffix == ".pdf":
        text = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
        ).stdout
        if not text.strip():
            with tempfile.TemporaryDirectory() as folder:
                subprocess.run(["pdftoppm", "-r", "200", "-png", str(path), f"{folder}/page"], check=True)
                text = "".join(
                    subprocess.run(["tesseract", str(png), "-"], capture_output=True, text=True, check=True).stdout
                    for png in sorted(Path(folder).glob("page*.png"))
                )
                subprocess.run(["pdftoppm", "-r", "400", "-png", str(path), f"{folder}/fine"], check=True)
                text += "".join(
                    subprocess.run(
                        ["tesseract", str(png), "-", "--psm", "6"], capture_output=True, text=True, check=True
                    ).stdout
                    for png in sorted(Path(folder).glob("fine*.png"))
                )
    elif pdf:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml" if path.suffix == ".docx" else "content.xml").decode()
        xml = re.sub(r"</w:p>|</text:p>|<w:tab/>|<text:tab/>|<text:s/>|</w:tc>|</table:table-cell>", " ", xml)
        text = html.unescape(re.sub(r"<[^>]+>", "", xml))
    else:
        text = html.unescape(re.sub(r"(?s)<(script|style).*?</\1>|<[^>]+>", " ", path.read_text()))
    text = re.sub(r"\s+", " ", text)
    return text, pdf, set(re.findall(r"\d+(?:\.\d+)?", re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)))


def render(artifact: raw.Artifact, number: int) -> tuple[Path, int, int]:
    PAGE_IMAGES.mkdir(parents=True, exist_ok=True)
    image = PAGE_IMAGES / f"{artifact.sha256}-{number}.png"
    if not image.exists():
        subprocess.run(
            ["pdftoppm", "-r", "150", "-gray", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
            + [str(artifact.path), str(image.with_suffix(""))],
            check=True,
        )
    width, height = struct.unpack(">II", image.read_bytes()[16:24])
    return image, width, height


def emit() -> Path:
    table = pl.read_csv(ROWS, separator="\t", infer_schema=False)
    boxes: dict[tuple[str, str, str], tuple[int, float, float, float, float]] = {}
    for row in pl.read_csv(SCANS, separator="\t", infer_schema=False).iter_rows(named=True):
        x0, y0, x1, y1 = (float(row[corner]) for corner in ("x0", "y0", "x1", "y1"))
        boxes[row["url"], row["kind"], row["field"]] = (int(row["page"]), x0, y0, x1, y1)
    scanned = {url for url, _, _ in boxes}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Neighborhood planning referendums in England, from the counting officers' declarations of result",
            polity="GB",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        system = cs.key("gb.electoral_system", "referendum")
        if system in cs.minted:
            raise ValueError("publish gb.referendums first")
        gb = cs.polity("GB")
        if conn.execute("select 1 from scheme where code = %s", [SCHEME]).fetchone() is None:
            cs.add("scheme", code=SCHEME, polity_id=gb, kind="electoral_district", rank=4, noun="neighborhood area")
        councils: dict[str, tuple[str, str]] = {}
        for code in sorted(set(table["council"])):
            polity = cs.key("gb.polity", code)
            if polity in cs.minted:
                raise ValueError(f"{code}: the council must exist")
            found = conn.execute(
                "select n.text, s.path from name n join slug s on s.entity_id = n.entity_id"
                " where n.entity_id = %s and n.is_preferred order by s.is_current desc limit 1",
                [polity],
            ).fetchone()
            if found is None:
                raise ValueError(f"{code}: the council has no name or slug")
            councils[code] = (found[0], found[1])
        elections: dict[UUID, str] = {}
        added: set[UUID] = set()
        pages: dict[tuple[str, int], UUID] = {}
        for (day, code), polls in table.group_by(["day", "council"], maintain_order=True):
            council_name, path = councils[str(code)]
            place = council_name.removesuffix(" City Council").removesuffix(" Council")
            election = cs.key("gb.election", f"{day}:local:{code}")
            if election in cs.minted:
                national = conn.execute(
                    "select entity_id from external_id where scheme = 'gb.election' and value = %s",
                    [f"{day}:local:gb"],
                ).fetchone()
                cs.add(
                    "election",
                    id=election,
                    polity_id=cs.key("gb.polity", str(code)),
                    parent_id=None if national is None else national[0],
                    kind="referendum",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                plural = "s" if polls.height > 1 else ""
                name(
                    election,
                    f"{place} neighborhood planning referendum{plural}, {date.fromisoformat(str(day)):%-d %B %Y}",
                )
                elections[election] = f"{path}/{day}-referendum"
                cs.add("slug", path=elections[election], entity_id=election)
            elif election not in elections:
                found = conn.execute(
                    "select path from slug where entity_id = %s order by is_current desc limit 1", [election]
                ).fetchone()
                if found is None:
                    raise ValueError(f"{day} {code}: the election has no slug")
                elections[election] = found[0]
            for row in polls.iter_rows(named=True):
                scan = row["url"] in scanned
                text, pdf, numbers = ("", True, set()) if scan else printed(row["url"])
                handwritten = scan or any(part in row["url"] for part in HANDWRITTEN)
                for field in ("yes", "no", "rejected", "electorate", "ballots", "turnout"):
                    if row[field] is not None and row[field] not in numbers and not handwritten:
                        raise ValueError(f"{row['plan']}: {field} {row[field]} is not printed in {row['url']}")
                area_name = str(row["area_name"])
                plan = str(row["plan"])
                key = f"{day}:neighborhood:{code}:{slug(plan)}:{row['kind']}"
                title = (
                    f"Decision statement: {plan} {row['kind']} referendum, {day}"
                    if pdf and "decision" in row["url"].lower()
                    else f"Declaration of result of poll: {plan} {row['kind']} referendum, {day}"
                    if pdf
                    else "Neighbourhood plan referendum results"
                    if row["url"] in PAGES.values()
                    else f"{plan} referendum results"
                )
                source = cs.source(council_name, title)
                if source in cs.minted and source not in added:
                    added.add(source)
                    cs.add(
                        "source", id=source, kind="official_body", publisher=council_name, title=title, url=row["url"]
                    )
                    artifact = raw.cached(SOURCE, row["url"])
                    artifact_id = cs.artifact(artifact, source)
                    for url, kind, field in boxes:
                        number = boxes[url, kind, field][0]
                        if url == row["url"] and (url, number) not in pages:
                            image, width, height = render(artifact, number)
                            pages[url, number] = cs.page(artifact_id, number)
                            cs.add(
                                "source_page",
                                id=pages[url, number],
                                artifact_id=artifact_id,
                                page_number=number,
                                image_key=f"pages/{SOURCE}/{image.name}",
                                width=width,
                                height=height,
                            )
                unit_code = f"{code}:{slug(re.sub(r'(?i) neighbourhood (plan )?area$', '', area_name))}"
                unit = cs.unit(SCHEME, unit_code)
                if unit in cs.minted and unit not in added:
                    added.add(unit)
                    parish = row["area"]
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=cs.key("gb.polity", str(code)),
                        scheme_code=SCHEME,
                        code=unit_code,
                        area_relation="within" if parish is None else "equals",
                        area_id=cs.area("gb.lad", str(code)) if parish is None else cs.area("gb.parish", parish),
                        kind="standard",
                        valid_from=None,
                    )
                    name(unit, area_name[0].upper() + area_name[1:])
                contest = cs.key("gb.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="ballot_question",
                    district_area_id=None if row["area"] is None else cs.area("gb.parish", row["area"]),
                    electoral_system_id=system,
                    round_number=1,
                    default_result_kind="counted",
                )
                cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
                label = plan if row["kind"] == "residential" else f"{plan} (business referendum)"
                name(contest, label)
                cs.add("slug", path=f"{elections[election]}/{slug(label)}", entity_id=contest)
                context = CONTEXT[str(row["kind"])].format(council=council_name)
                if row["area"] is None:
                    context = f"{context} {WITHIN.format(council=council_name)}"
                cs.add("note", entity_id=contest, kind="context", body_md=context)
                options = [cs.key("gb.neighborhood_plans.option", f"{key}:{position}") for position in range(2)]
                for position, (option, answer) in enumerate(zip(options, ("Yes", "No"), strict=True)):
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="answer",
                        ballot_position=position + 1,
                        ballot_label=answer,
                    )
                yes, no = int(row["yes"]), int(row["no"])
                if yes == no:
                    raise ValueError(f"{plan}: tied")
                verdict = re.search(r"voted\W*(in favour of|against)", text, re.IGNORECASE)
                if (
                    pdf
                    and not handwritten
                    and verdict is not None
                    and (verdict.group(1).lower() == "against") != (no > yes)
                ):
                    raise ValueError(f"{plan}: the declared outcome does not match the counts")
                result_set = cs.key("gb.neighborhood_plans.result_set", key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
                cs.add("citation", entity_id=result_set, source_id=source)
                cs.add("outcome", result_set_id=result_set, option_id=options[no > yes], kind="won")
                for field, option, measure in (
                    ("yes", options[0], VOTES),
                    ("no", options[1], VOTES),
                    ("rejected", None, INVALID),
                    ("electorate", None, REGISTERED),
                    ("ballots", None, BALLOTS),
                    ("turnout", None, VOTERS),
                ):
                    value = row[field]
                    if value is None:
                        continue
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=(SHARE_ONLY if measure == VOTERS else 0) | (DIGITIZED if scan else 0),
                        value=round(float(value) / 100, 4) if measure == VOTERS else int(value),
                    )
                    if scan:
                        number, x0, y0, x1, y1 = boxes[row["url"], str(row["kind"]), field]
                        if (row["url"], number) not in pages:
                            found = conn.execute(
                                "select p.id from source_page p join source_artifact a on a.id = p.artifact_id"
                                " where a.url = %s and p.page_number = %s",
                                [row["url"], number],
                            ).fetchone()
                            if found is None:
                                raise ValueError(f"{row['url']}: no page {number}")
                            pages[row["url"], number] = found[0]
                        cs.add(
                            "value_locator",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            page_id=pages[row["url"], number],
                            x0=x0,
                            y0=y0,
                            x1=x1,
                            y1=y1,
                        )
        return cs.write()
