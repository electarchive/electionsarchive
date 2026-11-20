"""The 12 February 2026 general election to the 13th Jatiya Sangsad, from the returning officers' result sheets that
the Election Commission's results portal serves (see `portal2026`)."""

import struct
import subprocess
import unicodedata
from datetime import date

from ea import raw
from ea.package import Package
from ea.sources.bd import portal2026
from ea.store import DATA

DAY = date(2026, 2, 12)

# The parties as the sheets print them, by the transcript's code, with the English name the archive gives each; a
# party that ran in an earlier general election keeps the name its earlier package gives it.
PARTIES = {
    "BNP": "Bangladesh Nationalist Party (BNP)",
    "JI": "Jamaat-e-Islami Bangladesh",
    "IAB": "Islami Andolan Bangladesh",
    "JP": "Jatiya Party (Ershad)",
    "JPJ": "Jatiya Party (Manju)",
    "JSD": "Jatiya Samajtantrik Dal-JSD",
    "BJSD": "Bangladesh Jatiya Samajtantrik Dal",
    "CPB": "Bangladesher Communist Party",
    "BKM": "Bangladesh Khelafat Majlish",
    "KM": "Khelafat Majlish",
    "BKA": "Bangladesh Khelafat Andolon",
    "ZP": "Jaker Party",
    "GF": "Gono Forum",
    "GFR": "Gono Front",
    "BIF": "Bangladesh Islami Front",
    "IFB": "Islamic Front Bangladesh",
    "LDP": "Liberal Democratic Party-LDP",
    "BML": "Bangladesh Muslim League",
    "BMLB": "Bangladesh Muslim League-BML",
    "BNF": "Bangladesh Nationalist Front (BNF)",
    "NPP": "National Peoples Party",
    "BJPB": "Bangladesh Jatiya Party-BJP",
    "BJP": "Bangladesh Jatiya Party",
    "BKP": "Bangladesh Kalyan Party",
    "GTP": "Gonotantri Party",
    "JUI": "Jamiate Ulamae Islam Bangladesh",
    "BSD": "Bangladesh Samajtantrik Dal",
    "BSDM": "Bangladesher Samajtantrik Dal (Marxist)",
    "RWP": "Revolutionary Workers Party of Bangladesh",
    "BNAP": "Bangladesh National Awami Party",
    "IOJ": "Islami Oikya Jote",
    "NCP": "National Citizen Party (NCP)",
    "GOP": "Gono Odhikar Parishad",
    "ABP": "Amar Bangladesh Party (AB Party)",
    "NO": "Nagorik Oikya",
    "AJD": "Amjanatar Dal",
    "BLP": "Bangladesh Labour Party",
    "NDM": "Nationalist Democratic Movement (NDM)",
    "JD": "Janatar Dal",
    "IBB": "Insaniyat Biplob Bangladesh",
    "BSM": "Bangladesh Sangskritik Muktijote",
    "BMJP": "Bangladesh Minority Janata Party (BMJP)",
    "BSP": "Bangladesh Supreme Party (BSP)",
    "BC": "Bangladesh Congress",
    "BRP": "Bangladesh Republican Party (BRP)",
    "GSA": "Gonosamhati Andolon",
    "BSAP": "Bangladesh Equal Rights Party (BEP)",
    "BDP": "Bangladesh Development Party",
    "BNIP": "Bangladesh Nezame Islam Party",
    "JAGPA": "Jatiya Ganatantrik Party (JAGPA)",
}

# District spellings the sheets use for a district the earlier reports spell otherwise.
DISTRICT_ALIASES = {
    "চুয়াডাঙ্গা": "চুয়াডাংগা",
    "টাঙ্গাইল": "টাংগাইল",
    "মাগুড়া": "মাগুরা",
    "মাদারিপুর": "মাদারীপুর",
    "ব্রাক্ষণবাড়িয়া": "ব্রাহ্মণবাড়িয়া",
    "নেত্রকোণা": "নেত্রকোনা",
    "মুন্সিগঞ্জ": "মুন্সীগঞ্জ",
    "শরিয়তপুর": "শরীয়তপুর",
    "রাঙ্গামাটি": "পার্বত্য রাংগামাটি",
    "খাগড়াছড়ি": "পার্বত্য খাগড়াছড়ি",
    "বান্দরবান": "পার্বত্য বান্দরবান",
}

METHOD = (
    "Counted by the returning officer of each constituency and sent to the Bangladesh Election Commission on its "
    "result sheet (বার্তা প্রেরণ শিট, the preliminary unofficial result), which the Commission's results portal "
    "serves as a scan. The scans carry no usable text, so every candidate's votes and each total were read by eye "
    "and checked against the sheet's printed valid votes and against the valid, rejected, and registered figures "
    "the portal lists for the constituency. Ballots cast are the valid and rejected ballots together. The candidate "
    "with the most votes is stored as elected."
)


def bengali_name(name: str) -> str:
    """A constituency's name with the earlier reports' district spelling, for its English name."""
    name = unicodedata.normalize("NFC", name)
    district, _, number = name.rpartition("-")
    district = district.strip()
    for variant, canonical in DISTRICT_ALIASES.items():
        if unicodedata.normalize("NFC", variant) == district:
            district = unicodedata.normalize("NFC", canonical)
    return f"{district}-{number}"


def seats() -> dict[str, dict]:
    sheets = portal2026.transcript()["sheets"]
    return {cid: e for cid, e in sorted(sheets.items(), key=lambda kv: int(kv[0]))}


def parties_used() -> set[str]:
    return {PARTIES[c[1]] for e in seats().values() for c in e["candidates"] if c[1] != "IND"}


def bengali_parties() -> dict[str, str]:
    printed = portal2026.transcript()["parties"]
    return {PARTIES[k]: v for k, v in printed.items() if k in PARTIES}


def page_image(path, image_key: str) -> tuple[int, int]:
    target = DATA / image_key
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        subprocess.run(
            [
                "pdftoppm",
                "-r",
                "100",
                "-png",
                "-singlefile",
                "-f",
                "1",
                "-l",
                "1",
                str(path),
                str(target.with_suffix("")),
            ],
            check=True,
            stderr=subprocess.DEVNULL,
        )
    width, height = struct.unpack(">II", target.read_bytes()[16:24])
    return width, height


def build(m) -> Package:
    """The election's package; `m` is the `ecs_parliament` module, whose ids and helpers it shares."""
    p = Package(
        f"{m.SOURCE}/{DAY}",
        m.SOURCE,
        "The 12 February 2026 general election to Bangladesh's 13th Jatiya Sangsad by constituency, from the "
        "returning officers' result sheets on the Election Commission's results portal",
        depends=[f"{m.SOURCE}/geo", f"{m.SOURCE}/registry"],
    )
    p.external |= {m.BANGLADESH}
    source = m.key("source", "portal2026")
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Bangladesh Election Commission",
        title="Results portal of the 2026 general election and referendum",
        url=portal2026.PAGE,
        license="No license stated; published by the electoral authority for public use",
        attribution="Bangladesh Election Commission",
    )
    portal = {s["constituency_id"]: s for s in portal2026.sheets()}
    for zilla in portal2026.zillas():
        p.artifact(raw.cached(m.SOURCE, portal2026.sheets_url(str(zilla["zillaID"]))), source)
    id = m.key("election", DAY)
    path = f"bd/{DAY}"
    p.add("election", id=id, polity_id=m.BANGLADESH, kind="general", date_from=DAY, date_to=DAY, status="held")
    p.add("slug", path=path, entity_id=id, is_current=True)
    p.name(id, "2026 Bangladeshi general election", "en", "official")
    noted: list[str] = []
    for cid, e in seats().items():
        stored = raw.cached(m.SOURCE, portal2026.sheet_document(portal[cid]) or "")
        artifact = p.artifact(stored, source)
        page = m.key("page", DAY, cid)
        image_key = f"pages/{m.SOURCE}/{stored.sha256}-1.png"
        width, height = page_image(stored.path, image_key)
        p.add(
            "source_page", id=page, artifact_id=artifact, page_number=1, image_key=image_key, width=width, height=height
        )
        name = m.english(bengali_name(e["name"]))
        tag = f"{DAY}:{e['number']}"
        district = m.area(name)
        unit = m.key("unit", tag)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=m.BANGLADESH,
            scheme_code=m.SCHEME,
            code=tag,
            area_relation="equals",
            area_id=district,
            kind="standard",
        )
        p.name(unit, f"{e['number']} {name}", "en", "official")
        p.name(unit, unicodedata.normalize("NFC", e["name"]), "bn", "official")
        contest = m.key("contest", tag)
        p.add(
            "contest",
            id=contest,
            election_id=id,
            kind="body_seats",
            body_id=m.SANGSAD,
            district_area_id=district,
            magnitude=1,
            round_number=1,
            electoral_system_id=m.FPTP,
            default_result_kind="counted",
        )
        p.name(contest, f"2026 Jatiya Sangsad election, {name}", "en", "official")
        p.add("slug", path=f"{path}/{e['number']}-{m.changeset.slug(name)}", entity_id=contest, is_current=True)
        result_set = m.key("result_set", tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=id,
            contest_id=contest,
            stage="preliminary",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        method = METHOD
        if e.get("suspended"):
            method += (
                f" Polling was stopped at {e['suspended']} polling centres here; the votes are those of the others."
            )
        if e.get("note"):
            method += " " + e["note"]
            if sum(c[2] for c in e["candidates"]) != e["valid"]:
                noted.append(str(e["number"]))
        p.add("note", id=m.key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        top = sorted((c[2] for c in e["candidates"]), reverse=True)

        def put(
            option: str | None, measure: int, value: int, at: tuple[str, str, str] = (result_set, unit, page)
        ) -> None:
            """A value, located on its sheet's page; the scans give no cell boxes, so the box is the page."""
            key = {"result_set_id": at[0], "unit_id": at[1], "option_id": option, "channel": 0, "measure": measure}
            p.add("result_value", **key, count_round=0, flags=m.DIGITIZED, value=value)
            p.add("value_locator", **key, count_round=0, page_id=at[2], source_id=None, x0=0.0, y0=0.0, x1=1.0, y1=1.0)

        for index, (person_name, code, votes) in enumerate(e["candidates"], start=1):
            person_name = unicodedata.normalize("NFC", person_name)
            solo = code == "IND"
            party = None if solo else m.party(PARTIES[code])
            person = m.key("person", tag, index)
            option = m.key("option", tag, index)
            p.add("person", id=person)
            p.name(person, person_name, "bn", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party,
                ballot_position=index,
                ballot_label=person_name,
            )
            p.add(
                "candidacy",
                id=m.key("candidacy", tag, index),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=person_name,
                member_of_party_id=party,
                is_independent=solo,
            )
            if votes == top[0] and (len(top) < 2 or top[0] > top[1]):
                p.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
            put(option, m.VOTES, votes)
        for measure, value in (
            (m.REGISTERED, e["registered"]),
            (m.CAST, e["cast"]),
            (m.INVALID, e["rejected"]),
            (m.VALID, e["valid"]),
        ):
            put(None, measure, value)
    if noted:
        p.accept(
            "accounting.votes_valid",
            f"In {len(noted)} constituencies ({', '.join(noted)}) the candidates' votes as read from the scanned "
            "sheet add to a different number than the valid votes the sheet and the portal print; both are stored as "
            "read, and each result set's note gives the two figures.",
        )
    return p
