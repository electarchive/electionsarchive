"""New Zealand's binding polls on Māori wards and constituencies, held with the 11 October 2025 local elections, from
each council's declaration of result under section 86 of the Local Electoral Act 2001. The five regional councils that
nz.local already holds are not repeated here."""

import json
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from datetime import date
from html import unescape
from typing import Any
from uuid import uuid5

import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package

SOURCE = "nz.polls"
NEW_ZEALAND = str(uuid5(package.NAMESPACE, "polity\x1fNZ"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fNZL"))
DAY = "2025-10-11"
DATE = date(2025, 10, 11)
SCHEME = "nz.council"
SHAPES_URL = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/NZL/ADM2/"
    "geoBoundaries-NZL-ADM2_simplified.geojson"
)
EZ = "https://www.electionz.com/LGE2025Results/"


@dataclass(frozen=True)
class Council:
    key: str
    name: str
    shape: str | None  # the council's name among the Stats NZ territorial authority outlines; None for a region
    url: str
    suffix: str
    publisher: str
    noun: str = "wards"  # wards, or constituencies for a regional council


def c(key: str, name: str, shape: str | None, url: str, suffix: str = ".pdf", publisher: str = "", noun: str = "wards"):
    return Council(key, name, shape, url, suffix, publisher or f"{name}, Electoral Officer", noun)


OFFICER = "Warwick Lampp, Electoral Officer (Electionz.com Ltd)"
COUNCILS = [
    c(
        "trc",
        "Taranaki Regional Council",
        None,
        "https://www.trc.govt.nz/assets/Documents/Elections/2025-Elections/"
        "Local-Elections-2025-Declaration-Notice-Final-2025-10-17.pdf",
        noun="constituencies",
    ),
    c(
        "whangarei",
        "Whangārei District Council",
        "Whangarei District",
        "https://www.wdc.govt.nz/files/assets/public/v/2/documents/council/governance/elections-2025/results/"
        "2025-declaration-election-and-poll-notice-final.pdf",
    ),
    c(
        "thames-coromandel",
        "Thames-Coromandel District Council",
        "Thames-Coromandel District",
        "https://www.tcdc.govt.nz/files/assets/public/v/1/our-council/public-notices/governance-pn/"
        "thames-coromandel-district-council-declaration-notice-tcdc.pdf",
    ),
    c(
        "matamata-piako",
        "Matamata-Piako District Council",
        "Matamata-Piako District",
        EZ + "ELT15MP25_Result.pdf",
        ".pdf",
        OFFICER,
    ),
    c("waipa", "Waipā District Council", "Waipa District", EZ + "ELT17WP25_Result.pdf", ".pdf", OFFICER),
    c(
        "otorohanga",
        "Ōtorohanga District Council",
        "Ōtorohanga District",
        "https://www.otodc.govt.nz/assets/Documents/Elections/"
        "Otorohanga-District-Council-2025-Triennial-Elections-Final-FPP-Result.pdf",
    ),
    c("taupo", "Taupō District Council", "Taupo District", EZ + "ELT21TP25_Result.pdf", ".pdf", OFFICER),
    c(
        "wbop",
        "Western Bay of Plenty District Council",
        "Western Bay of Plenty District",
        EZ + "ELT22WB25_Result.pdf",
        ".pdf",
        OFFICER,
    ),
    c("rotorua", "Rotorua Lakes Council", "Rotorua District", EZ + "ELT24RL25_Result.pdf", ".pdf", OFFICER),
    c(
        "whakatane",
        "Whakatāne District Council",
        "Whakatane District",
        "https://www.whakatane.govt.nz/about-council/news/whakatane-district-council-local-election-2025-final-results",
        ".html",
    ),
    c(
        "kawerau",
        "Kawerau District Council",
        "Kawerau District",
        "https://www.kaweraudc.govt.nz/sites/www.kaweraudc.govt.nz/files/documents/"
        "kawerau_declaration_notice_-_final_election_results_17_oct_2025.pdf",
    ),
    c(
        "hastings",
        "Hastings District Council",
        "Hastings District",
        "https://www.hastingsdc.govt.nz/assets/Documents/Elections/2025/"
        "Hastings-District-Council-2025-Triennial-Elections-Final-Results.pdf",
    ),
    c(
        "napier",
        "Napier City Council",
        "Napier City",
        "https://www.napier.govt.nz/assets/Uploads/Napier-City-Council-2025-Triennial-Elections-Final-FPP-Result.pdf",
    ),
    c(
        "chb",
        "Central Hawke's Bay District Council",
        "Central Hawke's Bay District",
        "https://www.chbdc.govt.nz/assets/Uploads/"
        "Central-Hawkes-Bay-District-Council-2025-Triennial-Elections-Final-FPP-Result.pdf",
    ),
    c("whanganui", "Whanganui District Council", "Whanganui District", EZ + "ELT37WH25_Result.pdf", ".pdf", OFFICER),
    c(
        "rangitikei",
        "Rangitīkei District Council",
        "Rangitikei District",
        "https://www.rangitikei.govt.nz/council/elections/local-government-election-2025/election-results-2025",
        ".html",
    ),
    c(
        "newplymouth",
        "New Plymouth District Council",
        "New Plymouth District",
        "https://www.npdc.govt.nz/media/mjcog5cc/new-plymouth-declaration-notice-final.pdf",
    ),
    c(
        "palmerston-north",
        "Palmerston North City Council",
        "Palmerston North City",
        "https://www.pncc.govt.nz/News-Events/Public-notices/Declaration-of-results-of-election-and-poll-2025",
        ".html",
    ),
    c(
        "horowhenua",
        "Horowhenua District Council",
        "Horowhenua District",
        "https://www.horowhenua.govt.nz/files/assets/public/v/1/elections/2025/"
        "horowhenua-district-council-2025-triennial-elections-final-fpp-result.pdf",
    ),
    c(
        "kapiti",
        "Kāpiti Coast District Council",
        "Kapiti Coast District",
        "https://www.kapiticoast.govt.nz/media/2iohceux/kapiti-coast-declaration-notice-final.pdf",
    ),
    c(
        "south-wairarapa",
        "South Wairarapa District Council",
        "South Wairarapa District",
        "https://swdc.govt.nz/wp-content/uploads/South-Wairarapa-District-Council-2025-Triennial-Elections-Final-FPP-Result.pdf",
    ),
    c("hutt", "Hutt City Council", "Lower Hutt City", EZ + "ELT46HU25_Result.pdf", ".pdf", OFFICER),
    c(
        "porirua",
        "Porirua City Council",
        "Porirua City",
        "https://storage.googleapis.com/pcc-wagtail-media/documents/"
        "Porirua_City_Council_2025_Triennial_Elections_-_Final_STV_Result.pdf",
    ),
    c("nelson", "Nelson City Council", "Nelson City", EZ + "ELT52NL25_Result.pdf", ".pdf", OFFICER),
]


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/polls-{DAY}"]


def fetch() -> None:
    raw.fetch(SOURCE, SHAPES_URL, suffix=".geojson")
    for council in COUNCILS:
        raw.fetch(SOURCE, council.url, suffix=council.suffix)


def emit() -> None:
    package.run("load", packages())


def parse() -> None:
    geo().write()
    election().write()


# ---- reading the declarations -------------------------------------------------------------------------------------


@dataclass
class Poll:
    keep: int
    remove: int
    informal: int | None
    blank: int | None
    winner: str  # the option the declaration says received the most votes


def number(text: str) -> int:
    return int(text.replace(",", ""))


def lines_of(council: Council) -> list[str]:
    path = raw.cached(SOURCE, council.url).path
    if council.suffix == ".pdf":
        out = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True)
        text = out.stdout
    else:
        html = path.read_text(encoding="utf-8", errors="replace")
        html = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.DOTALL)
        html = re.sub(r"</(p|div|tr|li|h\d|table|td|th)>|<br\s*/?>", "\n", html)
        text = unescape(re.sub(r"<[^>]+>", " ", html))
    lines = (unicodedata.normalize("NFC", re.sub(r"[ \t\xa0]+", " ", line)).strip() for line in text.splitlines())
    merged: list[str] = []
    for line in lines:
        if not line:
            continue
        if council.suffix == ".html" and re.fullmatch(r"[\d,]+", line) and merged:
            merged[-1] = f"{merged[-1]} {line}"  # a table cell that the page puts on its own line
        else:
            merged.append(line)
    return merged


HEAD = re.compile(r"^M[āa]ori (?:wards?|constituenc\w+) poll\b(?!.*\bheld\b)", re.IGNORECASE)
ANSWER = re.compile(r"^I vote to (?P<what>KEEP|REMOVE) (?:the )?M[āa]ori \w+\s+(?P<n>[\d,]+)$", re.IGNORECASE)
INFORMAL = re.compile(r"^informal(?: votes)?:?\s+(?P<n>[\d,]+)$", re.IGNORECASE)
BLANK = re.compile(r"^blank(?: voting papers| votes)?:?\s+(?P<n>[\d,]+)$", re.IGNORECASE)
DECLARED = re.compile(r"option to (?P<what>KEEP|REMOVE)\b", re.IGNORECASE)


def poll_of(council: Council) -> Poll:
    lines = lines_of(council)
    text = " ".join(lines)
    if council.key == "whakatane":
        # A news page of the council's final results: no informal or blank counts, no separate declaration.
        m = re.search(r"binding poll on M[āa]ori wards.{0,40}?Keep: ([\d,]+) Remove: ([\d,]+)", text)
        if not m:
            raise ValueError("whakatane: poll counts not found")
        keep, remove = number(m[1]), number(m[2])
        return Poll(keep, remove, None, None, "KEEP" if keep > remove else "REMOVE")
    if council.key == "rangitikei":
        votes = {
            m[2].upper(): number(m[1]) for m in re.finditer(r"([\d,]+) voted to (REMOVE|KEEP) the", text, re.IGNORECASE)
        }
        d = DECLARED.search(text)
        if set(votes) != {"KEEP", "REMOVE"} or not d:
            raise ValueError("rangitikei: poll counts not found")
        return Poll(votes["KEEP"], votes["REMOVE"], None, None, d["what"].upper())
    start = next((i for i, line in enumerate(lines) if HEAD.match(line)), None)
    if start is None:
        raise ValueError(f"{council.key}: no poll section")
    votes: dict[str, int] = {}
    informal = blank = None
    winner = None
    for line in lines[start + 1 : start + 14]:
        if m := ANSWER.match(line):
            votes[m["what"].upper()] = number(m["n"])
        elif m := INFORMAL.match(line):
            informal = number(m["n"])
        elif m := BLANK.match(line):
            blank = number(m["n"])
        elif m := DECLARED.search(line):
            winner = m["what"].upper()
            break
    if set(votes) != {"KEEP", "REMOVE"} or winner is None:
        raise ValueError(f"{council.key}: incomplete poll section {votes} {winner}")
    return Poll(votes["KEEP"], votes["REMOVE"], informal, blank, winner)


def polls() -> dict[str, Poll]:
    out = {}
    for council in COUNCILS:
        poll = poll_of(council)
        most = "KEEP" if poll.keep > poll.remove else "REMOVE"
        if poll.winner != most:
            raise ValueError(f"{council.key}: declared {poll.winner} but the counts favor {most}")
        out[council.key] = poll
    return out


# ---- the packages -------------------------------------------------------------------------------------------------


def source_id(council: Council) -> str:
    return key("source", council.key)


def area(council: Council) -> str:
    return key("area", SCHEME, council.key)


SHAPES_SOURCE = key("source", "geoboundaries")
VINTAGE = date(2021, 1, 1)


def shape(geometry: dict[str, Any]) -> shapely.MultiPolygon:
    polygon = shapely.make_valid(shapely.geometry.shape(geometry))
    parts = [part for part in shapely.get_parts(polygon) if part.geom_type in {"Polygon", "MultiPolygon"}]
    flat = [g for part in parts for g in shapely.get_parts(part)]
    return shapely.set_srid(shapely.MultiPolygon(flat), 4326)


def mend(name: str) -> str:
    """The outline file stores some UTF-8 names as if they were Latin-1; undo that."""
    try:
        name = name.encode("latin-1").decode("utf-8")
    except UnicodeEncodeError, UnicodeDecodeError:
        pass
    return unicodedata.normalize("NFC", name)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The councils that polled on Māori wards and constituencies in 2025, with the territorial authority outlines",
    )
    p.external |= {NEW_ZEALAND, COUNTRY}
    p.add(
        "source",
        id=SHAPES_SOURCE,
        kind="academic",
        publisher="geoBoundaries (Stats NZ territorial authority 2021, generalised)",
        title="geoBoundaries gbOpen New Zealand ADM2: Territorial Authority 2021 (generalised)",
        url="https://www.geoboundaries.org/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Stats NZ (CC BY 4.0), via geoBoundaries (William & Mary geoLab)",
    )
    p.artifact(raw.cached(SOURCE, SHAPES_URL), SHAPES_SOURCE)
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=NEW_ZEALAND,
        kind="administrative",
        rank=2,
        noun="council area",
        code_system=None,
    )
    p.add(
        "boundary_set",
        id=key("boundary_set", SCHEME, 2021),
        scheme_code=SCHEME,
        vintage="2021",
        valid_from=VINTAGE,
        as_of=VINTAGE,
    )
    path = raw.cached(SOURCE, SHAPES_URL).path
    features = json.loads(path.read_text(encoding="utf-8"))["features"]
    outlines = {mend(f["properties"]["shapeName"]): shape(f["geometry"]) for f in features}
    for council in COUNCILS:
        a = area(council)
        p.add("area", id=a, scheme_code=SCHEME)
        p.add("area_code", area_id=a, code=council.key)
        p.add("area_parent", area_id=a, parent_id=COUNTRY)
        p.name(a, council.name, "en", "official")
        if council.shape is None:
            continue
        geometry = outlines[mend(council.shape)]
        p.add(
            "area_geometry",
            area_id=a,
            valid_from=VINTAGE,
            geom=shapely.to_wkb(geometry, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(geometry), 4326), hex=True, include_srid=True
            ),
            quality="official",
            source_id=SHAPES_SOURCE,
        )
    return p


METHOD = (
    "The council's electoral officer counted the poll and printed it in the declaration of result under section 86 "
    "of the Local Electoral Act 2001. The poll is binding: the option that received the most votes applies to the "
    "council's 2028 and 2031 elections. Informal and blank counts are stored as the declaration prints them; the "
    "declaration gives no registered, cast, or valid totals."
)
NO_COUNTS = " This source prints no informal or blank counts."


def election() -> Package:
    p = Package(
        f"{SOURCE}/polls-{DAY}",
        SOURCE,
        "The 11 October 2025 polls on Māori wards and constituencies, from the councils' declarations of result",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {NEW_ZEALAND, COUNTRY}
    path = f"nz/{DAY}-maori-ward-polls"
    el = key("election", DAY)
    p.add(
        "election",
        id=el,
        polity_id=NEW_ZEALAND,
        kind="referendum",
        date_from=DATE,
        date_to=DATE,
        original_text="11 October 2025",
        status="held",
    )
    p.name(el, "2025 New Zealand local polls on Māori wards and constituencies", "en", "official")
    p.add("slug", path=path, entity_id=el, is_current=True)
    for council in COUNCILS:
        artifact = raw.cached(SOURCE, council.url)
        sid = source_id(council)
        p.add(
            "source",
            id=sid,
            kind="official_body",
            publisher=council.publisher,
            title=f"{council.name}: declaration of result, 2025 Māori {council.noun} poll",
            url=council.url,
            license="No license stated; published by the electoral officer for public use",
            attribution=council.publisher,
        )
        p.artifact(artifact, sid)
        p.uses(artifact)
    result = polls()
    for council in COUNCILS:
        poll = result[council.key]
        sid = source_id(council)
        tag = f"{council.key}:maori-poll"
        contest = key("contest", DAY, tag)
        p.add(
            "contest",
            id=contest,
            election_id=el,
            kind="ballot_question",
            district_area_id=area(council),
            round_number=1,
            default_result_kind="counted",
        )
        p.add("ballot_question", contest_id=contest, type="plebiscite")
        p.name(contest, f"{council.name}: poll on the Māori {council.noun}, 2025", "en", "official")
        p.add("slug", path=f"{path}/{council.key}", entity_id=contest, is_current=True)
        rs = key("result_set", DAY, tag)
        p.add(
            "result_set",
            id=rs,
            election_id=el,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=sid,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        note = METHOD + (NO_COUNTS if poll.informal is None else "")
        p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=note, lang="en")
        unit = key("unit", DAY, tag)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=NEW_ZEALAND,
            scheme_code=SCHEME,
            code=f"{DAY}:{tag}",
            area_relation="equals",
            area_id=area(council),
            kind="standard",
        )
        for position, (answer, votes) in enumerate((("remove", poll.remove), ("keep", poll.keep)), 1):
            option = key("option", DAY, tag, answer)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=f"I vote to {answer} the Māori {council.noun}",
            )
            p.add(
                "result_value",
                result_set_id=rs,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=1,
                count_round=0,
                flags=0,
                value=votes,
            )
            p.add(
                "outcome",
                result_set_id=rs,
                option_id=option,
                kind="won" if answer.upper() == poll.winner else "lost",
            )
        for measure, value in ((5, poll.informal), (6, poll.blank)):
            if value is not None:
                p.add(
                    "result_value",
                    result_set_id=rs,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
    return p
