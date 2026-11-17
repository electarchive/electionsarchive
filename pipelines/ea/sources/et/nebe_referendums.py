"""Ethiopia's eight referendums, 1995 to 2023, from the National Election Board's reports and result forms.

The Board publishes an implementation report for each referendum on its referendum page (Begi 1995, Oromia-Somali 2004,
Wondo Genet 2008, Amhara-Qimant 2017, Sidama 2019) and a statement for South West Ethiopia (2021). For the southern
referendum of 6 February 2023 it publishes a summary report with a table by zone and special woreda, and a scanned
result form for each zone and special woreda on Google Drive; its June 2023 final report gives the Wolayta re-run of
19 June 2023. The 1995, 2004, 2008, 2017, 2019, and 2021 documents print national totals only (and, for 2017, the
registered voters of each woreda); the 2023 documents give each zone and special woreda.

The result forms for the Gamo, Gedeo, and Gofa zones answer 404 on Google Drive, so those three zones are built from
the same figures in the summary report's table.
"""

from datetime import date
from typing import NamedTuple
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "et.nebe_referendums"
ETHIOPIA = str(uuid5(package.NAMESPACE, "polity\x1fET"))
SITE = "https://www.nebe.org.et"
FILES = SITE + "/sites/default/files/"
PAGE = SITE + "/am/referendum"
RESULT_PAGE = SITE + "/am/Referendum_Result"
SWE_PAGE = "https://nebe.org.et/en/node/657"
DRIVE = "https://drive.google.com/uc?export=download&id="
VOTES, REGISTERED, VALID, INVALID, VOTERS = 1, 2, 4, 5, 14
_IDS = Package(SOURCE, SOURCE, "ids")

BEGI_PDF = FILES + "Begi-referendum.pdf"
OROMIA_PDF = FILES + "Oromia-and-Somali-Referendum.pdf"
WONDO_PDF = FILES + "Wondo%20Genet%20Referendum-Final.pdf"
QIMANT_PDF = FILES + "Amhara-and-Qimant-Referendum.pdf"
SIDAMA_PDF = FILES + "Sidama-referendum_0.pdf"
FINAL_PDF = FILES + "Final_Report_05-07-23_0.pdf"
WOLAYTA_PDF = FILES + "wolayta%20rr.pdf"
SUMMARY_ID = "1vJPGXELwyjLUEBdpkAGYfjtq8iETyOSy"
# Drive file ids of the result forms. Gamo, Gedeo, and Gofa answer 404 (ids below), so they have no form here.
FORMS = {
    "konso": "1wTAThrL-zMEIfzM70iF2j1ne4w67S_KI",
    "south-omo": "1Lsz141twbsJ24qEiiQ5UhoYFkFJl3FTU",
    "burji": "1zOZ8yrxKw9zof2TziZixn-A0Ahf13Tvb",
    "basketo": "1-TVmGUfqwaoZ68azEk52s_FOWb2nMLdt",
    "ale": "1qGomPEOlGbd_iazuHDcCwOyhmUrqw4d3",
    "dirashe": "1a7xlHZpqBTdYG0c2RoAEEpOrggA0xqpw",
    "amaro": "1PoPp95mubaF6zbh6UDE_zbvgNOPOcbqV",
}
MISSING_FORMS = {
    "gamo": "1aF_lWdioUtHrugUSGDX1jIliRl3aJNCc",
    "gedeo": "13a2MjLsa486jzoCGyvqqwdu025B8L2Bl",
    "gofa": "1EtPk4-hlhsCSsypVFLfKOVgcWxW-FQPW",
}
URLS = [
    PAGE,
    RESULT_PAGE,
    SWE_PAGE,
    BEGI_PDF,
    OROMIA_PDF,
    WONDO_PDF,
    QIMANT_PDF,
    SIDAMA_PDF,
    FINAL_PDF,
    WOLAYTA_PDF,
    DRIVE + SUMMARY_ID,
    *(DRIVE + i for i in FORMS.values()),
]


def key(kind: str, *parts: object) -> str:
    return _IDS.id(kind, *parts)


# Areas: (code, English name, Amharic name, scheme).
ZONE, WOREDA, AREA = "et.zone", "et.woreda", "et.referendum_area"
AREAS: list[tuple[str, str, str, str]] = [
    ("konso", "Konso", "ኮንሶ", ZONE),
    ("south-omo", "South Omo", "ደቡብ ኦሞ", ZONE),
    ("wolayta", "Wolayta", "ወላይታ", ZONE),
    ("gamo", "Gamo", "ጋሞ", ZONE),
    ("gedeo", "Gedeo", "ጌዴኦ", ZONE),
    ("gofa", "Gofa", "ጎፋ", ZONE),
    ("sidama", "Sidama", "ሲዳማ", ZONE),
    ("burji", "Burji", "ቡርጂ", ZONE),
    ("basketo", "Basketo", "ባስኬቶ", ZONE),
    ("ale", "Ale", "አሌ", ZONE),
    ("amaro", "Amaro", "አማሮ", ZONE),
    ("dirashe", "Dirashe", "ዲራሼ", ZONE),
    ("begi", "Begi", "ቤጊ", WOREDA),
    ("wondo-genet", "Wondo Genet", "ወንዶ ገነት", WOREDA),
    ("gondar-city", "Gondar city", "ጎንደር ከተማ", WOREDA),
    ("chilga", "Chilga", "ጭልጋ", WOREDA),
    ("metema", "Metema", "መተማ", WOREDA),
    ("quara", "Quara", "ቋራ", WOREDA),
    ("oromia-somali", "Oromia-Somali border kebeles", "የኦሮሚያና የሶማሌ ወሰን ቀበሌዎች", AREA),
    ("north-gondar", "North Gondar mixed Amhara-Qimant kebeles", "የሰሜን ጎንደር የአማራና ቅማንት ቀበሌዎች", AREA),
    ("south-west", "South West Ethiopia zones and Konta special woreda", "የደቡብ ምዕራብ ኢትዮጵያ ዞኖች", AREA),
]
SOUTH_ZONES = ["konso", "south-omo", "wolayta", "gamo", "gedeo", "gofa"]
SOUTH_SPECIAL = ["burji", "basketo", "ale", "amaro", "dirashe"]
NAMES = {code: en for code, en, _am, _scheme in AREAS}
SCHEMES = {code: scheme for code, _en, _am, scheme in AREAS}

# 6 February 2023, by zone and special woreda: stations, registered, voters who signed the roll, valid, invalid, yes, no.
# From the summary report's table (and, for all but Gamo, Gedeo, and Gofa, the Board-approved result forms, which agree).
Row = tuple[int, int, int, int, int, int, int]
SOUTH: dict[str, Row] = {
    "konso": (135, 111561, 105292, 103004, 2180, 101114, 1890),
    "south-omo": (428, 297948, 266991, 261523, 5273, 256159, 5364),
    "gamo": (805, 630340, 602133, 593439, 8141, 583757, 9682),
    "gedeo": (626, 372754, 300994, 288444, 11941, 241695, 46749),
    "gofa": (329, 289307, 264665, 255633, 8471, 243327, 12306),
    "burji": (39, 36123, 29943, 29225, 676, 28460, 765),
    "basketo": (50, 32639, 29912, 28807, 1098, 27674, 1133),
    "ale": (64, 29950, 23520, 22807, 710, 22395, 412),
    "amaro": (98, 94233, 91382, 90593, 598, 90377, 216),
    "dirashe": (85, 56801, 53155, 51920, 1163, 51467, 453),
}
# Polling stations whose results the Board left out of the sum, by zone (summary report's table).
EXCLUDED = {"konso": 4, "south-omo": 4, "gamo": 21, "gedeo": 23, "gofa": 10, "burji": 2, "basketo": 0, "ale": 3}
EXCLUDED |= {"amaro": 7, "dirashe": 7}
# The Wolayta re-run of 19 June 2023, from 1,787 of 1,812 stations: voters (who registered and voted that day), valid,
# invalid, yes, no. The Board's final report prints these; its Wolayta result card prints other figures (voters 840,226,
# valid 793,575, invalid 44,938, yes 752,044, no 41,531) that include more than the 1,787 stations' results.
WOLAYTA_RERUN = (838018, 791551, 44756, 750254, 41297)


class Option(NamedTuple):
    en: str
    label: str


class Referendum(NamedTuple):
    day: date
    ethiopian: str
    name: str
    amharic: str
    slug: str
    question_en: str
    question_am: str
    options: tuple[Option, ...]
    type: str
    title: str
    pdfs: tuple[str, ...]
    page: str
    method: str


COMMON_EN = (
    "organizing the six zones (Konso, Wolayta, South Omo, Gamo, Gedeo, Gofa) and the five special woredas (Burji, "
    "Basketo, Ale, Amaro, Dirashe) into one common region"
)
COMMON_AM = "የ6ቱ ዞኖች (ኮንሶ፣ ወላይታ፣ ደቡብ ኦሞ፣ ጋሞ፣ ጌዴኦ፣ ጎፋ) እና የ5ቱ ልዩ ወረዳዎች (ቡርጂ፣ ባስኬቶ፣ አሌ፣ አማሮ፣ ዲራሼ) በአንድ የጋራ ክልል መደራጀት"
YES_NO = (
    Option("I support " + COMMON_EN, "White dove: I support the common region"),
    Option("I do not support " + COMMON_EN, "Hut: I do not support the common region"),
)

BEGI = date(1995, 7, 2)
OROMIA = date(2004, 10, 24)
WONDO = date(2008, 11, 9)
QIMANT = date(2017, 9, 17)
SIDAMA = date(2019, 11, 20)
SOUTHWEST = date(2021, 9, 30)
FEB = date(2023, 2, 6)
JUNE = date(2023, 6, 19)

REFERENDUMS: dict[date, Referendum] = {
    BEGI: Referendum(
        BEGI,
        "25 Sene 1987 E.C.",
        "1995 Begi woreda referendum",
        "የቤጊ ወረዳ ህዝበ ውሳኔ (1987 ዓ.ም)",
        "begi",
        "Which regional state, Oromia or Benishangul-Gumuz, should administer the Begi special woreda",
        "የቤጊ ወረዳ በየትኛው ክልል ይተዳደር",
        (),
        "referendum",
        "የቤጊ ወረዳ ህዝበ ውሳኔ (1987 ዓ.ም)",
        (BEGI_PDF,),
        PAGE,
        "The Board's report prints the registered voters and the voters who voted in the 88 kebeles of Begi woreda "
        "and no count by option, so only these two totals are held. The Prime Minister's office had asked the Board "
        "to run the vote under the Transitional Charter, with the woreda held outside both regions until then.",
    ),
    OROMIA: Referendum(
        OROMIA,
        "14 Tikimt 1997 E.C.",
        "2004 Oromia-Somali border referendum",
        "የሶማሌና ኦሮሚያ ክልሎች ሕዝበ ውሳኔ",
        "oromia-somali",
        "Which regional state, Oromia (Oda) or Somali (camel), should administer each disputed border kebele",
        "በኦሮሚያና በሱማሌ ክልሎች ወሰን ላይ የሚገኙ ቀበሌዎች በየትኛው ክልል ይተዳደሩ",
        (
            Option("Be administered by Oromia", "Oda: Oromia region"),
            Option("Be administered by Somali region", "Camel: Somali region"),
        ),
        "referendum",
        "የሶማሌና ኦሮሚያ ክልሎች ሕዝበ ውሳኔ",
        (OROMIA_PDF,),
        PAGE,
        "Totals for the border kebeles of Oromia and Somali regions that voted, as the Board's report prints them. "
        "The Board planned the vote in 463 kebeles; 39 did not vote and the results of two were annulled, and the "
        "table leaves out four of the 14 kebeles of Berbere woreda, so it covers the rest of the 422 kebeles the "
        "Board found properly run. The report prints no count by woreda or kebele, and its figure for non-voters "
        "(41,260) differs from the registered voters less the voters (40,766); only the registered and the voters "
        "are stored.",
    ),
    WONDO: Referendum(
        WONDO,
        "30 Tikimt 2001 E.C.",
        "2008 Wondo Genet referendum",
        "የወንዶ ገነት ሕዝቦች ሕዝበ ውሳኔ",
        "wondo-genet",
        "Whether the kebeles of Wondo Genet woreda join Oromia (Oda) or remain in the Southern Nations, "
        "Nationalities, and Peoples' Region (hut)",
        "የወንዶ ገነት ወረዳ ቀበሌዎች በኦሮሚያ ወይስ በደቡብ ክልል ይሁኑ",
        (
            Option("Join Oromia", "Oda: Oromia region"),
            Option("Remain in the Southern Nations, Nationalities, and Peoples' Region", "Hut: Southern region"),
        ),
        "referendum",
        "የወንዶ ገነት ሕዝቦች ሕዝበ ውሳኔ",
        (WONDO_PDF,),
        PAGE,
        "Totals for the 13 kebeles of Wondo Genet woreda in 46 polling stations, as the Board's report prints them "
        "(its text carries some wording from the Amhara-Qimant report). Registration was 28,869 men and 25,938 "
        "women. The two options' votes add to 50,715 of the 53,155 voters, and the report prints no invalid count "
        "(its table shows a dash), so the other 2,440 ballots are not classified.",
    ),
    QIMANT: Referendum(
        QIMANT,
        "7 Meskerem 2010 E.C.",
        "2017 Amhara-Qimant referendum",
        "የአማራና ቅማንት ሕዝቦች ሕዝበ ውሳኔ",
        "amhara-qimant",
        "Whether the mixed Amhara and Qimant kebeles of North Gondar zone are administered by the existing "
        "administration or by the Qimant self-administration",
        "በነባሩ አስተዳደር ወይም በቅማንት ራስ አስተዳደር መተዳደር",
        (
            Option("The existing administration", "Existing administration"),
            Option("The Qimant self-administration", "Qimant self-administration"),
        ),
        "referendum",
        "የአማራና ቅማንት ሕዝቦች ሕዝበ ውሳኔ",
        (QIMANT_PDF,),
        PAGE,
        "Registered voters by woreda (Gondar city, Chilga, Metema, Quara) and the voters and invalid ballots in "
        "all, from the 8 kebeles and 24 polling stations where the vote took place (12 kebeles of four woredas were "
        "planned; registration did not take place in four kebeles of Chilga). The report prints no count by option; "
        "its figure for non-voters (2,454) differs from the registered voters less the voters (2,452).",
    ),
    SIDAMA: Referendum(
        SIDAMA,
        "10 Hidar 2012 E.C.",
        "2019 Sidama statehood referendum",
        "የሲዳማ በክልልነት የመደራጀት ሕዝበ ውሳኔ",
        "sidama",
        "Whether Sidama is organized as a regional state (Shafeta) or stays in the Southern Nations, Nationalities, "
        "and Peoples' Region (Gojo)",
        "ሲዳማ በክልልነት ይደራጅ ወይስ በደቡብ ክልል ይቆይ",
        (
            Option("Organize Sidama as a regional state", "Shafeta: Sidama region"),
            Option("Stay in the Southern Nations, Nationalities, and Peoples' Region", "Gojo: remain in the south"),
        ),
        "referendum",
        "የሲዳማ በክልልነት የመደራጀት ሕዝበ ውሳኔ",
        (SIDAMA_PDF,),
        PAGE,
        "The Board's final result of 24 Hidar 2012 E.C. (3 December 2019), after it cancelled the results of 164 of "
        "the 1,861 stations (127 with more votes than registered voters, 37 with a sum error of more than 10 "
        "votes). The registered voters and the voters are the totals the report prints, which include the 248,097 "
        "votes from the cancelled stations; the option votes and the 16,624 invalid ballots count the remaining "
        "stations, so the options and the invalid ballots add to the voters less 248,097. The provisional result "
        "of the polling day (2,225,249 Shafeta and 33,463 Gojo) is not stored.",
    ),
    SOUTHWEST: Referendum(
        SOUTHWEST,
        "20 Meskerem 2014 E.C.",
        "2021 South West Ethiopia referendum",
        "የደቡብ ምዕራብ ኢትዮጵያ ሕዝቦች ሕዝበ ውሳኔ",
        "south-west",
        "Whether Konta special woreda and the West Omo, Bench Sheko, Dawro, Kafa, and Shaka zones establish a "
        "joint regional government or remain in the Southern Nations, Nationalities, and Peoples' Region",
        "የኮንታ ልዩ ወረዳ እና የምዕራብ ኦሞ፣ ቤንች ሸኮ፣ ዳውሮ፣ ካፋ እና ሸካ ዞኖች በአንድ የጋራ ክልል መደራጀት",
        (
            Option("Establish a joint regional government", "Support the joint region"),
            Option("Remain in the Southern Nations, Nationalities, and Peoples' Region", "Remain in the south"),
        ),
        "referendum",
        "Statement on the South West Ethiopia People's Referendum (9 October 2021)",
        (),
        SWE_PAGE,
        "Totals for the one combined result, which the House of Federation asked the Board to present together, "
        "from 1,613 polling stations in 22 constituencies. The Board's statement prints the registered voters and "
        "the voters, and says the majority voted for the joint region without printing a count by option.",
    ),
    FEB: Referendum(
        FEB,
        "29 Tir 2015 E.C.",
        "2023 southern zones referendum",
        "የደቡብ ብ/ብ/ሕ/ክልል ስድስት ዞኖች እና አምስት ልዩ ወረዳዎች ህዝበ ውሳኔ",
        "southern-zones",
        "Whether to support " + COMMON_EN,
        "በአንድ የጋራ ክልል መደራጀትን አደግፋለሁ ወይስ አልደግፍም",
        YES_NO,
        "referendum",
        "የህዝበ ውሳኔ ውጤት (Referendum result)",
        (FINAL_PDF,),
        RESULT_PAGE,
        "",
    ),
    JUNE: Referendum(
        JUNE,
        "12 Sene 2015 E.C.",
        "2023 Wolayta zone referendum re-run",
        "የዎላይታ ዞን የድጋሚ ህዝበ ውሳኔ",
        "wolayta-rerun",
        "Whether to support " + COMMON_EN + ", held again in Wolayta zone",
        "በአንድ የጋራ ክልል መደራጀትን አደግፋለሁ ወይስ አልደግፍም (ዎላይታ)",
        YES_NO,
        "referendum",
        "Final report on the referendum in the southern zones and the Wolayta re-run (5 July 2023)",
        (FINAL_PDF, WOLAYTA_PDF),
        PAGE,
        "",
    ),
}


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in REFERENDUMS)]


def fetch() -> None:
    for url in URLS:
        raw.fetch(SOURCE, url, suffix=".html" if url in (PAGE, RESULT_PAGE, SWE_PAGE) else ".pdf")


def parse() -> None:
    check()
    for p in (geo(), registry(), *(build(day) for day in REFERENDUMS)):
        p.write()


def emit() -> None:
    package.run("load", packages())


def check() -> None:
    """Refuse figures that do not add up the way the Board's own totals do."""
    for zone, (_, registered, voters, valid, invalid, yes, no) in SOUTH.items():
        if yes + no != valid:
            raise ValueError(f"{zone}: yes and no do not add up to the valid votes")
        if voters > registered or valid + invalid > voters + 10:
            raise ValueError(f"{zone}: accounting does not fit")
    voters, valid, invalid, yes, no = WOLAYTA_RERUN
    if yes + no != valid:
        raise ValueError("Wolayta re-run: yes and no do not add up to the valid votes")
    if 5939 + 3069 + 10866 + 3409 != 23283:
        raise ValueError("Qimant: woredas do not add up to the registered voters")
    if 129424 + 70336 != 199760:
        raise ValueError("Oromia-Somali: options do not add up to the voters")
    if 28869 + 25938 != 54807:
        raise ValueError("Wondo Genet: registration by sex does not add up")
    if 1984283 + 30018 + 16624 + 248097 != 2279022:
        raise ValueError("Sidama: the report's figures do not add up to the voters")


def cached(url: str) -> raw.Artifact:
    return raw.cached(SOURCE, url)


def geo() -> Package:
    p = Package(f"{SOURCE}/geo", SOURCE, "The zones, special woredas, and woredas of the Ethiopian referendums")
    p.external.add(ETHIOPIA)
    schemes = (
        (ZONE, 2, "administrative", "zone or special woreda"),
        (WOREDA, 3, "administrative", "woreda"),
        (AREA, 4, "synthetic", "referendum area"),
    )
    for code, rank, kind, noun in schemes:
        p.add("scheme", code=code, polity_id=ETHIOPIA, kind=kind, rank=rank, noun=noun)
    for code, en, am, scheme in AREAS:
        area = key("area", code)
        p.add("area", id=area, scheme_code=scheme)
        p.add("area_code", area_id=area, code=code)
        p.name(area, en, "en", "official")
        p.name(area, am, "am", "native", False)
    return p


def registry() -> Package:
    p = Package(f"{SOURCE}/registry", SOURCE, "The electoral system of the Ethiopian referendums")
    p.external.add(ETHIOPIA)
    system = key("electoral_system", "referendum")
    p.add(
        "electoral_system",
        id=system,
        polity_id=ETHIOPIA,
        rules={"formula": "referendum"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", system),
        entity_id=system,
        kind="methodology",
        body_md=(
            "A referendum is decided by the option with the most votes in the voting area. Under the Electoral "
            "Proclamation 1162/2019, art. 11, the Board runs a referendum when the body the constitution empowers "
            "decides one, and reports the result to it."
        ),
        lang="en",
    )
    return p


def unit(p: Package, day: date, code: str, scheme: str, area: str, relation: str, parent: str | None = None) -> str:
    id = key("unit", day, code)
    p.add(
        "reporting_unit",
        id=id,
        polity_id=ETHIOPIA,
        scheme_code=scheme,
        code=f"{day}:{code}",
        parent_id=parent,
        area_relation=relation,
        area_id=key("area", area),
        kind="aggregate" if scheme == AREA else "standard",
    )
    p.name(id, NAMES[area], "en", "official")
    return id


def build(day: date) -> Package:
    spec = REFERENDUMS[day]
    depends = [f"{SOURCE}/geo", f"{SOURCE}/registry"]
    if day == JUNE:
        depends.append(f"{SOURCE}/{FEB}")
    p = Package(f"{SOURCE}/{day}", SOURCE, f"{spec.name} from the National Election Board's report", depends)
    p.external.add(ETHIOPIA)
    source = key("source", day)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="National Election Board of Ethiopia",
        title=spec.title,
        url=spec.pdfs[0] if spec.pdfs else spec.page,
        license="No license stated: official report of the Board; facts published with attribution",
        attribution="National Election Board of Ethiopia",
    )
    for url in (*spec.pdfs, spec.page):
        p.artifact(cached(url), source)
    if day == FEB:
        for url in (DRIVE + SUMMARY_ID, *(DRIVE + i for i in FORMS.values())):
            p.artifact(cached(url), source)
    election = key("election", day)
    p.add(
        "election",
        id=election,
        polity_id=ETHIOPIA,
        kind="referendum",
        date_from=day,
        date_to=day,
        date_precision="day",
        original_text=spec.ethiopian,
        status="held",
        rerun_of_id=key("election", FEB) if day == JUNE else None,
    )
    p.name(election, spec.name, "en", "official")
    p.name(election, spec.amharic, "am", "native", False)
    base = f"et/{day}-{spec.slug}"
    p.add("slug", path=base, entity_id=election, is_current=True)
    result = {
        BEGI: begi,
        OROMIA: oromia,
        WONDO: wondo,
        QIMANT: qimant,
        SIDAMA: sidama,
        SOUTHWEST: southwest,
        FEB: south,
        JUNE: rerun,
    }[day]
    result(p, spec, election, base, source)
    return p


def contest(p: Package, spec: Referendum, election: str, base: str, name: str, *, area: str, code: str) -> str:
    id = key("contest", spec.day, code)
    p.add(
        "contest",
        id=id,
        election_id=election,
        kind="ballot_question",
        district_area_id=key("area", area),
        round_number=1,
        electoral_system_id=key("electoral_system", "referendum"),
        default_result_kind="counted",
    )
    p.add("ballot_question", contest_id=id, type=spec.type)
    p.name(id, name, "en", "official")
    p.name(id, spec.question_am, "am", "official", False)
    p.add("slug", path=f"{base}/{'question' if code == 'all' else code}", entity_id=id, is_current=True)
    return id


def options(p: Package, spec: Referendum, contest_id: str, code: str) -> list[str]:
    found = []
    for position, option in enumerate(spec.options, 1):
        id = key("option", spec.day, code, position)
        found.append(id)
        p.add(
            "contest_option",
            id=id,
            contest_id=contest_id,
            kind="answer",
            ballot_position=position,
            ballot_label=option.label,
        )
        p.name(id, option.en, "en", "official")
    return found


def result_set(p: Package, spec: Referendum, election: str, contest_id: str, source: str, code: str, note: str) -> str:
    id = key("result_set", spec.day, code)
    p.add(
        "result_set",
        id=id,
        election_id=election,
        contest_id=contest_id,
        stage="official",
        kind="counted",
        source_id=source,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=note, lang="en")
    return id


def put(
    facts: list[tuple[str, str | None, int, int]], unit_id: str, option: str | None, measure: int, value: int
) -> None:
    facts.append((unit_id, option, measure, value))


def flush(p: Package, result_set_id: str, facts: list[tuple[str, str | None, int, int]]) -> None:
    p.frame(
        "result_value",
        pl.DataFrame(facts, schema=["unit_id", "option_id", "measure", "value"], orient="row").with_columns(
            result_set_id=pl.lit(result_set_id),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )


def single(
    p: Package,
    spec: Referendum,
    election: str,
    base: str,
    source: str,
    area: str,
    totals: dict[int, int],
    votes: tuple[int, ...] = (),
    won: int | None = None,
    note: str = "",
) -> None:
    """One contest over one area, with the totals the report prints."""
    c = contest(p, spec, election, base, spec.name, area=area, code="all")
    found = options(p, spec, c, "all")
    rs = result_set(p, spec, election, c, source, "all", spec.method + note)
    u = unit(p, spec.day, area, SCHEMES[area], area, "equals")
    facts: list[tuple[str, str | None, int, int]] = []
    for measure, value in totals.items():
        put(facts, u, None, measure, value)
    for option, count in zip(found, votes, strict=False):
        put(facts, u, option, VOTES, count)
    flush(p, rs, facts)
    if won is not None:
        p.add("outcome", result_set_id=rs, option_id=found[won], kind="won")


def begi(p: Package, spec: Referendum, election: str, base: str, source: str) -> None:
    single(p, spec, election, base, source, "begi", {REGISTERED: 56586, VOTERS: 45965})


def oromia(p: Package, spec: Referendum, election: str, base: str, source: str) -> None:
    single(
        p,
        spec,
        election,
        base,
        source,
        "oromia-somali",
        {REGISTERED: 240526, VOTERS: 199760},
        (129424, 70336),
    )


def wondo(p: Package, spec: Referendum, election: str, base: str, source: str) -> None:
    single(p, spec, election, base, source, "wondo-genet", {REGISTERED: 54807, VOTERS: 53155}, (19029, 31686))


def qimant(p: Package, spec: Referendum, election: str, base: str, source: str) -> None:
    c = contest(p, spec, election, base, spec.name, area="north-gondar", code="all")
    options(p, spec, c, "all")
    rs = result_set(p, spec, election, c, source, "all", spec.method)
    total = unit(p, spec.day, "north-gondar", AREA, "north-gondar", "equals")
    out: list[tuple[str, str | None, int, int]] = []
    put(out, total, None, REGISTERED, 23283)
    put(out, total, None, VOTERS, 20831)
    put(out, total, None, INVALID, 1744)
    for woreda, registered in (("gondar-city", 3069), ("chilga", 5939), ("metema", 10866), ("quara", 3409)):
        part = unit(p, spec.day, woreda, WOREDA, woreda, "within", total)
        put(out, part, None, REGISTERED, registered)
    flush(p, rs, out)


def sidama(p: Package, spec: Referendum, election: str, base: str, source: str) -> None:
    single(
        p,
        spec,
        election,
        base,
        source,
        "sidama",
        {REGISTERED: 2304577, VOTERS: 2279022, INVALID: 16624},
        (1984283, 30018),
        won=0,
    )


def southwest(p: Package, spec: Referendum, election: str, base: str, source: str) -> None:
    single(p, spec, election, base, source, "south-west", {REGISTERED: 1344622, VOTERS: 1262679}, won=0)


SOUTH_METHOD = (
    "Figures for {name} from the Board-approved result form (voters who registered, voters who signed the roll, "
    "valid and invalid ballots, and the votes for each option), approved by the Board on 10 and 12 Sene 2015 E.C. "
    "(17 and 19 June 2023). The Board left the results of {excluded} polling station(s) in the zone out of the sum "
    "because of irregularities it found. The valid votes are the votes for the two options; the voters are those "
    "who signed the roll."
)
SUMMARY_METHOD = (
    "Figures for {name} from the table of approved results in the Board's summary report (voters who registered, "
    "voters who signed the roll, valid and invalid ballots, and the votes for each option), because the zone's "
    "result form is not available. The Board left the results of {excluded} polling station(s) in the zone out of "
    "the sum because of irregularities it found. The valid votes are the votes for the two options; the voters "
    "are those who signed the roll."
)


def south(p: Package, spec: Referendum, election: str, base: str, source: str) -> None:
    for zone in [*SOUTH_ZONES, *SOUTH_SPECIAL]:
        name = NAMES[zone]
        noun = "special woreda" if zone in SOUTH_SPECIAL else "zone"
        c = contest(p, spec, election, base, f"{spec.name}: {name} {noun}", area=zone, code=zone)
        found = options(p, spec, c, zone)
        if zone == "wolayta":
            # The Board voided the results of 6 February's Wolayta vote and ordered the vote held again on 19 June.
            continue
        _stations, registered, voters, valid, invalid, yes, no = SOUTH[zone]
        template = SOUTH_METHOD if zone in FORMS else SUMMARY_METHOD
        rs = result_set(p, spec, election, c, source, zone, template.format(name=name, excluded=EXCLUDED[zone]))
        u = unit(p, spec.day, zone, ZONE, zone, "equals")
        out: list[tuple[str, str | None, int, int]] = []
        for measure, value in ((REGISTERED, registered), (VOTERS, voters), (VALID, valid), (INVALID, invalid)):
            put(out, u, None, measure, value)
        put(out, u, found[0], VOTES, yes)
        put(out, u, found[1], VOTES, no)
        flush(p, rs, out)
        p.add("outcome", result_set_id=rs, option_id=found[0], kind="won")


def rerun(p: Package, spec: Referendum, election: str, base: str, source: str) -> None:
    voters, valid, invalid, yes, no = WOLAYTA_RERUN
    c = contest(p, spec, election, base, spec.name, area="wolayta", code="all")
    found = options(p, spec, c, "all")
    note = (
        "Totals for Wolayta zone from the Board's final report of 5 July 2023, covering the 1,787 of 1,812 polling "
        "stations whose results the Board approved (it left out 25). Voters registered and voted on the same day, "
        "so the report prints one figure for both (412,953 women and 425,065 men); it is stored as the voters. The "
        "valid votes are the votes for the two options. The Board's Wolayta result card prints other figures "
        "(voters 840,226, valid 793,575, invalid 44,938, yes 752,044, no 41,531); the final report's figures are "
        "stored because they are the ones it gives in its list of approved results."
    )
    rs = result_set(p, spec, election, c, source, "all", note)
    u = unit(p, spec.day, "wolayta", ZONE, "wolayta", "equals")
    out: list[tuple[str, str | None, int, int]] = []
    for measure, value in ((VOTERS, voters), (VALID, valid), (INVALID, invalid)):
        put(out, u, None, measure, value)
    put(out, u, found[0], VOTES, yes)
    put(out, u, found[1], VOTES, no)
    flush(p, rs, out)
    p.add("outcome", result_set_id=rs, option_id=found[0], kind="won")
