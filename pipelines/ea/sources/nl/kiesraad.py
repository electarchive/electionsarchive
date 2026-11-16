import functools
import re
import zipfile
from collections import Counter
from collections.abc import Iterator
from datetime import date
from typing import NamedTuple
from uuid import uuid5
from xml.etree import ElementTree

import polars as pl

from ea import changeset, package, raw
from ea.package import Package
from ea.store import DATA

SOURCE = "nl.kiesraad"
OPEN = "https://data.overheid.nl/sites/default/files/dataset/"

# Election code (the Kiesraad's own) -> the EML archives on data.overheid.nl that hold it.
EML: dict[str, tuple[str, ...]] = {
    "EP2009": ("199da59f-ffb6-4ef0-9f21-0373cbc0df2e/resources/EML_bestanden_EP2009.zip",),
    "TK2010": ("fbf2c39e-b3c8-40c1-b52a-ba5c6b26cf1a/resources/EML_bestanden_TK2010.zip",),
    "EK2011": ("2769fdfe-ef46-4e4f-83de-ce7796a4de2d/resources/EML_bestanden_EK2011.zip",),
    "TK2012": ("31362154-3866-407d-97fd-96c9dc2639bc/resources/EML_bestanden_TK2012.zip",),
    "GR20121121": ("bc873a84-72fe-4852-ae63-ca2cdb21b4e9/resources/EML_bestanden_GR20121121.zip",),
    "GR20131113": ("2ec9f108-1243-490c-9713-47111b4d3451/resources/EML_bestanden_GR20131113.zip",),
    "GR2014": ("b52387b9-0520-4305-9592-ed5a1fcbb191/resources/EML_bestanden_GR2014.zip",),
    "EP2014": ("58f9ad84-ee16-467e-ac89-4ca0747f5747/resources/EML_bestanden_EP2014.zip",),
    "GR20141119": ("7b264e49-ce16-4927-b795-1433d3963c82/resources/EML_bestanden_GR20141119.zip",),
    "PS2015": ("5a1087ee-c3b8-4a6c-b40e-b24973d94d4e/resources/EML_bestanden_PS2015.zip",),
    "WS2015": ("094cae38-37bc-4a8c-9cac-ab14ce970efa/resources/EML_bestanden_WS2015.zip",),
    "EK2015": ("2769fdfe-ef46-4e4f-83de-ce7796a4de2d/resources/EML_bestanden_EK2015.zip",),
    "GR20151118": ("ef4f1c17-6221-464a-8b7d-7fb6a262cbb6/resources/EML_bestanden_GR20151118.zip",),
    "NR2016": ("3d5e39c0-54c2-42d3-99f8-aa8d4e124056/resources/EML_bestanden_NR2016.zip",),
    "GR20161123": ("14d78580-de8d-46df-ba0d-850e60ac0bf7/resources/EML_bestanden_GR20161123.zip",),
    "TK2017": ("5f636036-1634-4c7b-8ac9-8c0b4995ff4d/resources/Dataset_TK2017.zip",),
    "GR20171122": ("5581092b-fe81-4231-ac1f-1200ddf04064/resources/EML_bestanden_GR20171122.zip",),
    "GR2018": ("278d5537-4911-4f33-8d86-775de403e02f/resources/GR2018.zip",),
    "NR2018": ("b74f7374-d038-46cf-8932-ffe329a542fb/resources/emlbestandennr20180321.zip",),
    "GR20181121": ("8469c93e-9354-40ee-a325-01ddb264e825/resources/EMLbestandenGR20181121.zip",),
    "PS2019": ("8e85a43e-1d53-4e3a-a315-2c8b4b8cc333/resources/EMLbestandenPS2019.zip",),
    "WS2019": ("889582a0-a130-4cab-b281-9a4d983fe326/resources/EMLbestandenWS2019.zip",),
    "EK2019": ("625c1d0a-32bf-40b3-9079-bbff00f28667/resources/EML_bestanden_EK2019.zip",),
    "EP2019": ("2648b2ac-c254-4e63-9fe3-a3868acd919c/resources/EMLbestandenEP2019.zip",),
    "GR2020": ("0452b499-1eb8-45d1-9690-59d752724fda/resources/EML%20bestanden%20GRh2020.zip",),
    "TK2021": (
        "39e9bad4-4667-453f-ba6a-4733a956f6f8/resources/EML_bestanden_TK2021_deel_1.zip",
        "39e9bad4-4667-453f-ba6a-4733a956f6f8/resources/EML_bestanden_TK2021_deel_2.zip",
        "39e9bad4-4667-453f-ba6a-4733a956f6f8/resources/EML_bestanden_TK2021_deel_3.zip",
    ),
    "GR2021": ("662b3752-51e1-4557-ae89-ef43921b2ea5/resources/EML%20bestanden%20GRh2021.zip",),
    "GR2022": ("08b04bec-3332-4c76-bb0c-68bfaeb5df43/resources/GR2022_2022-03-29T15.14.zip",),
    "GR2022h": ("bceff656-83a0-4076-afb2-6bf5c54257e2/resources/EML%20bestanden%20GRh2022.zip",),
    "PS2023": (
        "be8b7869-4a12-4446-abab-5cd0a436dc4f/resources/EML_bestanden_PS2023_deel_1.zip",
        "be8b7869-4a12-4446-abab-5cd0a436dc4f/resources/EML_bestanden_PS2023_deel_2.zip",
        "be8b7869-4a12-4446-abab-5cd0a436dc4f/resources/EML_bestanden_PS2023_deel_3.zip",
    ),
    "WS2023": (
        "ee19bcda-0282-44a6-a464-44738afd755c/resources/EML_bestanden_AB2023_deel_1.zip",
        "ee19bcda-0282-44a6-a464-44738afd755c/resources/EML_bestanden_AB2023_deel_2.zip",
        "ee19bcda-0282-44a6-a464-44738afd755c/resources/EML_bestanden_AB2023_deel_3.zip",
    ),
    "EK2023": ("77d9a82f-16a6-49c4-a72f-731dd0f50dd8/resources/EML_bestanden_EK2023.zip",),
    "TK2023": (
        "e3fe6e42-06ab-4559-a466-a32b04247f68/resources/Verkiezingsuitslag%20Tweede%20Kamer%202023%20%28Deel%201%29.zip",
        "e3fe6e42-06ab-4559-a466-a32b04247f68/resources/Verkiezingsuitslag%20Tweede%20Kamer%202023%20%28Deel%202%29.zip",
        "e3fe6e42-06ab-4559-a466-a32b04247f68/resources/Verkiezingsuitslag%20Tweede%20Kamer%202023%20%28Deel%203%29.zip",
    ),
    "EP2024": (
        "050597d1-bee3-46e8-bca0-a4a8d0589a2e/resources/Verkiezingsuitslag%20Europees%20Parlement%202024%20%28Deel%201%29.zip",
        "050597d1-bee3-46e8-bca0-a4a8d0589a2e/resources/Verkiezingsuitslag%20Europees%20Parlement%202024%20%28Deel%202%29.zip",
        "050597d1-bee3-46e8-bca0-a4a8d0589a2e/resources/Verkiezingsuitslag%20Europees%20Parlement%202024%20%28Deel%203%29.zip",
    ),
    "TK2025": (
        "a16f3352-c9ce-4831-a314-f989d442a258/resources/Verkiezingsuitslag%20Tweede%20Kamer%202025%20%28Deel%201%29.zip",
        "a16f3352-c9ce-4831-a314-f989d442a258/resources/Verkiezingsuitslag%20Tweede%20Kamer%202025%20%28Deel%202%29.zip",
        "a16f3352-c9ce-4831-a314-f989d442a258/resources/Verkiezingsuitslag%20Tweede%20Kamer%202025%20%28Deel%203%29.zip",
    ),
    "GR2026": ("09cf04e7-11ac-4b53-a2cc-baccf95e82fd/resources/GR2026_2026_07_03T10.45.zip",),
}


def url(path: str) -> str:
    return OPEN + path


def fetch() -> None:
    for paths in EML.values():
        for path in paths:
            raw.fetch(SOURCE, url(path), suffix=".zip")


E = "{urn:oasis:names:tc:evs:schema:eml}"
KR = "{http://www.kiesraad.nl/extensions}"
XNL = "{urn:oasis:names:tc:ciq:xsdschema:xNL:2.0}"
PARSED = DATA / "parsed" / SOURCE
REGISTERED, VALID, INVALID, BLANK, VOTERS, VOTES = 2, 4, 5, 6, 14, 1
TOTALS = {"TotalCounted": VALID, "Cast": REGISTERED}
REJECTED = {"ongeldig": INVALID, "blanco": BLANK}
UNCOUNTED = {"toegelaten kiezers": VOTERS}
COUNT_SCHEMA = {
    "eml": pl.String,
    "kind": pl.String,
    "authority": pl.String,
    "contest": pl.String,
    "unit": pl.String,
    "measure": pl.Int16,
    "aff": pl.String,
    "value": pl.Int64,
}


class Doc(NamedTuple):
    member: str
    kind: str
    eml: str
    authority: str
    created: str
    root: ElementTree.Element


def _text(element: ElementTree.Element | None, path: str) -> str | None:
    if element is None:
        return None
    found = element.find(path)
    return None if found is None or found.text is None else found.text.strip()


HEADER = {
    "kind": re.compile(rb'<(?:\w+:)?EML\b[^>]*?\sId="([^"]+)"'),
    "eml": re.compile(rb'<(?:\w+:)?ElectionIdentifier Id="([^"]+)"'),
    "authority": re.compile(rb'<(?:\w+:)?AuthorityIdentifier Id="([^"]*)"'),
    "created": re.compile(rb"<(?:\w+:)?CreationDateTime>([^<]+)<"),
    "contest": re.compile(rb'<(?:\w+:)?ContestIdentifier Id="([^"]*)"'),
}


def documents(code: str) -> Iterator[Doc]:
    """Every EML document of one election, one per kind, election, authority, and contest: where the archive ships a
    file twice, the copy created last wins. Files are picked from their headers and parsed one at a time."""
    chosen: dict[tuple[str, ...], tuple[str, str, str]] = {}
    for path in EML[code]:
        archive = zipfile.ZipFile(raw.cached(SOURCE, url(path)).path)
        for member in sorted(archive.namelist()):
            if not member.lower().endswith(".xml"):
                continue
            with archive.open(member) as handle:
                head = handle.read(1 << 16)
            found = {name: pattern.search(head) for name, pattern in HEADER.items()}
            if found["kind"] is None or found["eml"] is None:
                continue
            fields = {name: "" if match is None else match.group(1).decode() for name, match in found.items()}
            slot = (fields["kind"], fields["eml"], fields["authority"], fields["contest"])
            if slot not in chosen or chosen[slot][2] < fields["created"]:
                chosen[slot] = (path, member, fields["created"])
    for (kind, eml, authority, _), (path, member, created) in chosen.items():
        archive = zipfile.ZipFile(raw.cached(SOURCE, url(path)).path)
        yield Doc(member, kind, eml, authority, created, ElementTree.fromstring(archive.read(member)))


def _counts(doc: Doc, names: dict[str, str]) -> list[tuple[str, str, str, str, str | None, int, str | None, int]]:
    rows: list[tuple[str, str, str, str, str | None, int, str | None, int]] = []
    authority = doc.root.find(f"{E}ManagingAuthority/{E}AuthorityIdentifier")
    if doc.kind == "510b" and authority is not None and authority.text:
        names.setdefault(doc.authority, authority.text.strip())
    for contest in doc.root.iter(E + "Contest"):
        identifier = contest.find(E + "ContestIdentifier")
        number = "" if identifier is None else str(identifier.get("Id") or "")
        blocks = [(None, contest.find(E + "TotalVotes"))]
        for block in contest.findall(E + "ReportingUnitVotes"):
            unit = block.find(E + "ReportingUnitIdentifier")
            if unit is not None and unit.text:
                names.setdefault(str(unit.get("Id")), unit.text.strip())
            blocks.append(("" if unit is None else str(unit.get("Id")), block))
        for unit, block in blocks:
            if block is None:
                continue
            for child in block:
                tag = child.tag.removeprefix(E)
                measure: int | None = None
                aff = None
                value = None
                if tag == "Selection":
                    affiliation = child.find(E + "AffiliationIdentifier")
                    if affiliation is None:
                        continue
                    measure, aff, value = VOTES, str(affiliation.get("Id")), _text(child, E + "ValidVotes")
                elif tag in TOTALS:
                    measure, value = TOTALS[tag], child.text
                elif tag == "RejectedVotes":
                    measure, value = REJECTED.get(str(child.get("ReasonCode"))), child.text
                elif tag == "UncountedVotes":
                    measure, value = UNCOUNTED.get(str(child.get("ReasonCode"))), child.text
                if measure is None or value is None or not value.strip():
                    continue
                rows.append((doc.eml, doc.kind, doc.authority, number, unit, measure, aff, int(value)))
    return rows


def _name(candidate: ElementTree.Element) -> tuple[str, str | None, str | None]:
    person = candidate.find(f"{E}CandidateFullName/{XNL}PersonName")
    first = _text(person, XNL + "FirstName")
    initials = _text(person, XNL + "NameLine")
    prefix = _text(person, XNL + "NamePrefix")
    last = _text(person, XNL + "LastName") or ""
    full = " ".join(part for part in (first or initials, prefix, last) if part)
    return full, initials, _text(candidate, E + "Gender")


def parse_code(code: str) -> None:
    counts: list[tuple[str, str, str, str, str | None, int, str | None, int]] = []
    elections: dict[str, dict[str, object]] = {}
    affiliations: dict[tuple[str, str], str] = {}
    candidates: list[dict[str, object]] = []
    regions: list[dict[str, object]] = []
    names: dict[str, str] = {}
    for doc in documents(code):
        identifier = next(doc.root.iter(E + "ElectionIdentifier"))
        domain = identifier.find(KR + "ElectionDomain")
        entry = elections.setdefault(
            doc.eml,
            {
                "eml": doc.eml,
                "category": _text(identifier, E + "ElectionCategory"),
                "subcategory": _text(identifier, KR + "ElectionSubcategory"),
                "name": _text(identifier, E + "ElectionName"),
                "day": _text(identifier, KR + "ElectionDate"),
                "domain_id": None if domain is None else domain.get("Id"),
                "domain": None if domain is None or domain.text is None else domain.text.strip(),
                "seats": None,
                "question": None,
                "folder": doc.member.rsplit("/", 2)[-2] if doc.member.count("/") else None,
            },
        )
        for selection in doc.root.iter(E + "Selection"):
            affiliation = selection.find(E + "AffiliationIdentifier")
            if affiliation is not None:
                text = _text(affiliation, E + "RegisteredName")
                if text:
                    affiliations.setdefault((doc.eml, str(affiliation.get("Id"))), text)
        for affiliation in doc.root.iter(E + "Affiliation"):
            identifier_ = affiliation.find(E + "AffiliationIdentifier")
            if identifier_ is not None:
                text = _text(identifier_, E + "RegisteredName")
                if text:
                    affiliations.setdefault((doc.eml, str(identifier_.get("Id"))), text)
        if doc.kind.startswith("510"):
            counts.extend(_counts(doc, names))
        elif doc.kind == "110a":
            seats = _text(doc.root, f".//{KR}NumberOfSeats")
            entry["seats"] = None if seats is None else int(seats)
            for region in doc.root.iter(KR + "Region"):
                regions.append(
                    {
                        "eml": doc.eml,
                        "number": region.get("RegionNumber"),
                        "category": region.get("RegionCategory"),
                        "name": _text(region, KR + "RegionName"),
                        "superior": region.get("SuperiorRegionNumber"),
                        "superior_category": region.get("SuperiorRegionCategory"),
                    }
                )
        elif doc.kind == "630":
            entry["question"] = _text(doc.root, f".//{E}ProposalName")
            for option in doc.root.iter(E + "ReferendumOptionIdentifier"):
                affiliations[(doc.eml, f"answer:{option.get('Id')}")] = (option.text or "").strip()
        elif doc.kind == "520":
            for contest in doc.root.iter(E + "Contest"):
                aff = None
                for selection in contest.findall(E + "Selection"):
                    affiliation = selection.find(E + "AffiliationIdentifier")
                    if affiliation is not None:
                        aff = str(affiliation.get("Id"))
                        continue
                    candidate = selection.find(E + "Candidate")
                    if candidate is None or aff is None:
                        continue
                    full, initials, gender = _name(candidate)
                    number = candidate.find(E + "CandidateIdentifier")
                    candidates.append(
                        {
                            "eml": doc.eml,
                            "aff": aff,
                            "candidate": None if number is None else number.get("Id"),
                            "name": full,
                            "initials": initials,
                            "gender": gender,
                            "elected": _text(selection, E + "Elected") == "yes",
                        }
                    )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(counts, schema=COUNT_SCHEMA, orient="row").write_parquet(PARSED / f"{code}.counts.parquet")
    pl.DataFrame(list(elections.values()), infer_schema_length=None).write_parquet(PARSED / f"{code}.elections.parquet")
    pl.DataFrame(
        [{"eml": eml, "aff": aff, "name": text} for (eml, aff), text in affiliations.items()],
        schema={"eml": pl.String, "aff": pl.String, "name": pl.String},
    ).write_parquet(PARSED / f"{code}.affiliations.parquet")
    pl.DataFrame(
        candidates,
        schema={
            "eml": pl.String,
            "aff": pl.String,
            "candidate": pl.String,
            "name": pl.String,
            "initials": pl.String,
            "gender": pl.String,
            "elected": pl.Boolean,
        },
    ).write_parquet(PARSED / f"{code}.candidates.parquet")
    pl.DataFrame(
        regions,
        schema={
            "eml": pl.String,
            "number": pl.String,
            "category": pl.String,
            "name": pl.String,
            "superior": pl.String,
            "superior_category": pl.String,
        },
    ).write_parquet(PARSED / f"{code}.regions.parquet")
    pl.DataFrame(
        {"unit": list(names), "name": list(names.values())}, schema={"unit": pl.String, "name": pl.String}
    ).write_parquet(PARSED / f"{code}.names.parquet")
    print(f"{code}: {len(elections)} elections, {len(counts):,} counts, {len(candidates):,} candidates", flush=True)


def parsed(code: str, table: str) -> pl.DataFrame:
    return pl.read_parquet(PARSED / f"{code}.{table}.parquet")


NETHERLANDS = str(uuid5(package.NAMESPACE, "polity\x1fNL"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fNLD"))
PROVINCES = {
    "Groningen": "NL-GR",
    "Fryslan": "NL-FR",
    "Drenthe": "NL-DR",
    "Overijssel": "NL-OV",
    "Flevoland": "NL-FL",
    "Gelderland": "NL-GE",
    "Utrecht": "NL-UT",
    "NoordHolland": "NL-NH",
    "ZuidHolland": "NL-ZH",
    "Zeeland": "NL-ZE",
    "NoordBrabant": "NL-NB",
    "Limburg": "NL-LI",
}
PROVINCE_NAMES = {
    "NL-GR": "Groningen",
    "NL-FR": "Fryslân",
    "NL-DR": "Drenthe",
    "NL-OV": "Overijssel",
    "NL-FL": "Flevoland",
    "NL-GE": "Gelderland",
    "NL-UT": "Utrecht",
    "NL-NH": "Noord-Holland",
    "NL-ZH": "Zuid-Holland",
    "NL-ZE": "Zeeland",
    "NL-NB": "Noord-Brabant",
    "NL-LI": "Limburg",
}
# The national electoral districts (kieskringen) by number, and the province each lies in.
KIESKRINGEN = {
    "1": "NL-GR",
    "2": "NL-FR",
    "3": "NL-DR",
    "4": "NL-OV",
    "5": "NL-FL",
    "6": "NL-GE",
    "7": "NL-GE",
    "8": "NL-UT",
    "9": "NL-NH",
    "10": "NL-NH",
    "11": "NL-NH",
    "12": "NL-ZH",
    "13": "NL-ZH",
    "14": "NL-ZH",
    "15": "NL-ZH",
    "16": "NL-ZE",
    "17": "NL-NB",
    "18": "NL-NB",
    "19": "NL-LI",
}
# The Caribbean public bodies (openbare lichamen) carry municipal codes in the 9000s and lie in no province.
PUBLIC_BODIES = {"9001", "9002", "9003"}
# Code 9010 is the postal polling station for voters abroad, counted in The Hague's electoral district.
ABROAD = "9010"
CATEGORIES = {
    "TK": ("general", "tweede-kamer", "Dutch general election", "Tweede Kamerverkiezing"),
    "EP": ("general", "europees-parlement", "European Parliament election in the Netherlands", "Europese verkiezing"),
    "PS": ("local", "provinciale-staten", "Dutch provincial elections", "Provinciale Statenverkiezingen"),
    "AB": ("local", "waterschappen", "Dutch water board elections", "Waterschapsverkiezingen"),
    "GR": ("local", "gemeenteraad", "Dutch municipal elections", "Gemeenteraadsverkiezingen"),
    "NR": ("referendum", "referendum", "Dutch advisory referendum", "Raadgevend referendum"),
}
# The 2022 municipal archive is the Kiesraad's pre-publication of the municipalities' own files.
PRELIMINARY = {"GR2022"}
OFFICIAL_METHOD = (
    "Counted by the municipalities and totaled by the principal and central polling committees, as published by the "
    "Kiesraad in Election Markup Language (EML) on data.overheid.nl. Each municipality's figures are the ones its "
    "principal polling committee (hoofdstembureau) recorded in the district count file, or, where no such file "
    "exists, the central polling committee's total count file or the municipality's own count file. Registered "
    "voters are the electors entitled to vote (kiesgerechtigden), voters are the electors admitted to vote "
    "(toegelaten kiezers), and valid votes are the votes cast on the lists. Each vote in a Dutch list election is "
    "cast for one candidate; the list total is the sum of its candidates' votes, and candidate votes are not stored "
    "here."
)
STATION_METHOD = (
    "Counted per polling station (stembureau), as each municipality recorded them in its EML count file, published "
    "by the Kiesraad on data.overheid.nl. Voters may vote at any polling station in their municipality, so the "
    "electorate a station file lists is not a station's own electorate and is not stored here. Station figures can "
    "differ slightly from the official municipal count, which the principal polling committee may have corrected; "
    "completeness is the station valid votes over the official valid votes."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def unit(scheme: str, code: str) -> str:
    return key("unit", scheme, code)


def fold(text: str) -> str:
    return " ".join(re.sub(r"[^0-9a-z]+", " ", changeset.slug(text).replace("-", " ")).split())


def party(name: str) -> str:
    return key("party", fold(name))


def _suffix(eml: str) -> str:
    return eml.split("_", 1)[1] if "_" in eml else eml


@functools.cache
def elections() -> pl.DataFrame:
    """Every EML election with its archive code, date, category, and domain."""
    frames = [parsed(code, "elections").with_columns(code=pl.lit(code)) for code in EML]
    frame = pl.concat(frames, how="diagonal_relaxed").with_columns(
        day=pl.col("day").str.slice(0, 10).str.to_date(),
        domain_id=pl.when(pl.col("domain_id").str.contains(r"^\d{1,4}$"))
        .then(pl.col("domain_id").str.zfill(4))
        .otherwise(None),
    )
    # Bonaire's 2023 count file carries the day before; every election in an archive shares one day.
    first = frame.group_by("code").agg(pl.col("day").mode().first().alias("mode"))
    frame = frame.join(first, on="code").with_columns(
        day=pl.when(pl.col("category").is_in(["GR"])).then("day").otherwise("mode")
    )
    return frame.drop("mode").filter(pl.col("category").is_in(list(CATEGORIES))).sort("day", "eml")


def days() -> list[date]:
    return sorted(set(elections()["day"].to_list()))


@functools.cache
def counts(code: str) -> pl.DataFrame:
    return parsed(code, "counts")


@functools.cache
def names() -> dict[str, str]:
    found: dict[str, str] = {}
    for code in EML:
        for unit_id, text in parsed(code, "names").iter_rows():
            found[str(unit_id)] = str(text)
    return found


def _municipal_unit(raw_id: str) -> str | None:
    """The municipality code of a municipal reporting unit id, such as HSB9::0363 or 0363."""
    tail = raw_id.rsplit("::", 1)[-1]
    return tail if re.fullmatch(r"\d{4}", tail) else None


@functools.cache
def municipalities() -> dict[str, tuple[str, str | None]]:
    """Each municipal code with its latest name and its province (None for the Caribbean public bodies)."""
    found: dict[str, tuple[str, str | None]] = {}
    province: dict[str, str] = {}
    text: dict[str, str] = {}
    for code in EML:
        frame = counts(code)
        regions = parsed(code, "regions")
        provinces = {
            row["number"]: PROVINCES.get(_suffix(row["eml"]))
            for row in regions.filter(pl.col("category") == "PROVINCIE").iter_rows(named=True)
        }
        kieskring = {
            (row["eml"], row["number"]): row["superior"]
            for row in regions.filter(pl.col("category") == "KIESKRING").iter_rows(named=True)
        }
        for row in regions.filter(pl.col("category") == "GEMEENTE").iter_rows(named=True):
            text[row["number"]] = row["name"]
            if row["superior_category"] == "KIESKRING":
                if (row["eml"], row["superior"]) in kieskring and kieskring[(row["eml"], row["superior"])] in provinces:
                    found_province = provinces[kieskring[(row["eml"], row["superior"])]]
                    if found_province is not None:
                        province[row["number"]] = found_province
                elif row["eml"][:2] in ("TK", "EP", "NR") and row["superior"] in KIESKRINGEN:
                    province.setdefault(row["number"], KIESKRINGEN[row["superior"]])
        for kind, authority, contest, unit_id in (
            frame.select("kind", "authority", "contest", "unit").unique().iter_rows()
        ):
            code_ = authority if kind == "510b" else _municipal_unit(unit_id) if unit_id else None
            if code_ is None or not re.fullmatch(r"\d{4}", code_):
                continue
            text.setdefault(code_, names().get(code_ if kind == "510b" else unit_id, code_))
            if kind == "510c" and str(authority).startswith("HSB") and code[:2] in ("TK", "EP", "NR"):
                province.setdefault(code_, KIESKRINGEN.get(str(authority).removeprefix("HSB"), ""))
    folders = elections().filter(pl.col("category") == "GR", pl.col("domain_id").is_not_null())
    for domain_id, folder in folders.select("domain_id", "folder").iter_rows():
        match = re.match(r"\d+ (.+)", str(folder or ""))
        if match is not None:
            province.setdefault(domain_id, PROVINCES.get(match.group(1).replace("-", "").replace("â", "a"), ""))
    for code_, name in text.items():
        clean = re.sub(r"^(Gemeente|Openbaar lichaam) ", "", name).strip()
        found[code_] = (clean, None if code_ in PUBLIC_BODIES or code_ == ABROAD else province.get(code_) or None)
    return found


def _boards() -> dict[str, str]:
    frame = elections().filter(pl.col("category") == "AB")
    return {_suffix(eml): str(domain or _suffix(eml)) for eml, domain in frame.select("eml", "domain").iter_rows()}


def _board_name(eml: str) -> str:
    row = elections().filter(pl.col("eml") == eml).row(0, named=True)
    return str(row["domain"] or row["name"] or _suffix(eml)).removeprefix("Waterschap ").strip()


KIESRAAD = key("source", "kiesraad")


def _source(p: Package) -> None:
    p.add(
        "source",
        id=KIESRAAD,
        kind="official_body",
        publisher="Kiesraad",
        title="Verkiezingsuitslagen in EML (open data)",
        url="https://data.overheid.nl/community/organization/kiesraad",
        license="CC0 1.0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        attribution="Kiesraad",
    )


def body(category: str, code: str) -> str:
    return key("body", category, code)


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in days())]


def parse() -> None:
    for code in EML:
        parse_code(code)
    registry().write()
    for day in days():
        build(day).write()


def emit() -> None:
    package.run("load", packages())


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Dutch provinces, municipalities, water boards, representative bodies, and parties from the Kiesraad's EML "
        "election results",
    )
    p.external |= {NETHERLANDS, COUNTRY}
    _source(p)
    for paths in EML.values():
        for path in paths:
            p.artifact(raw.cached(SOURCE, url(path)), KIESRAAD)
    for code, rank, kind, noun, system in (
        ("nl.provincie", 1, "administrative", "province", "iso_3166_2"),
        ("nl.waterschap", 1, "administrative", "water board", None),
        ("nl.gemeente", 2, "administrative", "municipality", "cbs"),
        ("nl.stembureau", 3, "precinct", "polling station", None),
    ):
        p.add("scheme", code=code, polity_id=NETHERLANDS, kind=kind, rank=rank, noun=noun, code_system=system)

    def place(scheme: str, code: str, text: str, parent: str) -> str:
        id = area(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=parent)
        p.name(id, text, "nl", "official")
        return id

    for code, text in PROVINCE_NAMES.items():
        place("nl.provincie", code, text, COUNTRY)
        p.add("body", id=body("PS", code), polity_id=NETHERLANDS, kind="council")
        p.name(body("PS", code), f"Provinciale Staten van {text}", "nl", "official")
        p.name(body("PS", code), f"Provincial Council of {text}", "en", "official", False)
    councils = set(
        elections().filter(pl.col("category") == "GR", pl.col("domain_id").is_not_null())["domain_id"].to_list()
    )
    for code, (text, province) in sorted(municipalities().items()):
        if code == ABROAD:
            continue
        place("nl.gemeente", code, text, COUNTRY if province is None else area("nl.provincie", province))
        p.add(
            "reporting_unit",
            id=unit("nl.gemeente", code),
            polity_id=NETHERLANDS,
            scheme_code="nl.gemeente",
            code=code,
            area_relation="equals",
            area_id=area("nl.gemeente", code),
            kind="standard",
        )
        if code in councils:
            p.add("body", id=body("GR", code), polity_id=NETHERLANDS, kind="council")
            p.name(body("GR", code), f"Gemeenteraad van {text}", "nl", "official")
            p.name(body("GR", code), f"Municipal Council of {text}", "en", "official", False)
    p.add(
        "reporting_unit",
        id=unit("nl.gemeente", ABROAD),
        polity_id=NETHERLANDS,
        scheme_code="nl.gemeente",
        code=ABROAD,
        area_relation="unlocated",
        kind="postal_district",
    )
    for code, text in sorted(_boards().items()):
        place("nl.waterschap", code, text.removeprefix("Waterschap ").strip(), COUNTRY)
        p.add("body", id=body("AB", code), polity_id=NETHERLANDS, kind="council")
        p.name(body("AB", code), f"Algemeen bestuur {text}", "nl", "official")
        p.name(body("AB", code), f"General board of {text}", "en", "official", False)
    for id, kind, nl, en in (
        (body("TK", "NL"), "lower_house", "Tweede Kamer der Staten-Generaal", "House of Representatives"),
    ):
        p.add("body", id=id, polity_id=NETHERLANDS, kind=kind)
        p.name(id, nl, "nl", "official")
        p.name(id, en, "en", "official", False)
    parties: dict[str, str] = {}
    for code in EML:
        for eml, aff, text in parsed(code, "affiliations").iter_rows():
            if aff.startswith("answer:") or str(eml).startswith("NR") or not fold(text):
                continue
            parties.setdefault(fold(text), " ".join(text.split()))
    for folded, text in sorted(parties.items()):
        id = party(text)
        p.add("party", id=id, kind="party", polity_id=NETHERLANDS)
        p.add("external_id", entity_id=id, scheme="nl.kiesraad.party", value=folded)
        p.name(id, text, "nl", "official")
    return p


def _official(frame: pl.DataFrame) -> tuple[pl.DataFrame, str]:
    """The municipal figures of one EML election: the district count files when there are any, else the total
    count file's municipal rows, else each municipality's own total."""
    district = frame.filter(pl.col("kind") == "510c", pl.col("unit").is_not_null())
    if district.height:
        grain, chosen = "district", district.with_columns(municipality=pl.col("unit").str.split("::").list.last())
        local = frame.filter(pl.col("kind") == "510b", pl.col("unit").is_null()).with_columns(
            municipality=pl.col("authority")
        )
        missing = local.filter(~pl.col("municipality").is_in(chosen["municipality"].unique().implode()))
        chosen = pl.concat([chosen, missing])
    else:
        total = frame.filter(pl.col("kind") == "510d", pl.col("unit").str.contains(r"^\d{4}$"))
        if total.height:
            grain, chosen = "total", total.with_columns(municipality=pl.col("unit"))
        else:
            grain = "municipal"
            chosen = frame.filter(pl.col("kind") == "510b", pl.col("unit").is_null()).with_columns(
                municipality=pl.col("authority")
            )
    chosen = chosen.filter(pl.col("municipality").str.contains(r"^\d{4}$"))
    # A zero electorate is a blank in the source (the Kiesraad lists the true figures in its notes), never a count.
    chosen = chosen.filter(~((pl.col("measure") == REGISTERED) & (pl.col("value") == 0)))
    return chosen.group_by("municipality", "measure", "aff").agg(pl.col("value").sum()), grain


def _stations(frame: pl.DataFrame) -> pl.DataFrame:
    return (
        frame.filter(pl.col("kind") == "510b", pl.col("unit").str.contains("::"), pl.col("measure") != REGISTERED)
        .group_by("unit", "measure", "aff")
        .agg(pl.col("value").first(), pl.len().alias("n"))
    )


def _declared(frame: pl.DataFrame) -> pl.DataFrame:
    total = frame.filter(pl.col("kind") == "510d", pl.col("unit").is_null())
    return total.group_by("measure", "aff").agg(pl.col("value").sum())


TOTALS_REPORT: dict[str, str] = {}


def build(day: date) -> Package:
    here = elections().filter(pl.col("day") == day)
    codes = sorted(set(here["code"].to_list()))
    categories = sorted(set(here["category"].to_list()))
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"Dutch elections of {day} ({', '.join(categories)}) by municipality and polling station from the Kiesraad",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {NETHERLANDS, COUNTRY}
    for code in codes:
        for path in EML[code]:
            p.uses(raw.cached(SOURCE, url(path)))
    known = municipalities()
    station_units: dict[str, str] = {}
    values: list[pl.DataFrame] = []
    slugs: set[str] = set()
    for category in categories:
        kind, word, en, nl = CATEGORIES[category]
        rows = here.filter(pl.col("category") == category)
        election = key("election", day, category)
        reorganization = category == "GR" and rows.height < 100
        title = f"{day.year} {en}"
        native = f"{nl} {day.year}"
        if reorganization:
            title = f"{day:%B} {day.year} Dutch municipal reorganization elections"
            native = f"Herindelingsverkiezingen {day:%-d-%-m-%Y}"
        p.add(
            "election",
            id=election,
            polity_id=NETHERLANDS,
            kind=kind,
            date_from=day,
            date_to=day,
            date_precision="day",
            status="held",
        )
        p.name(election, title, "en", "official")
        p.name(election, native, "nl", "native", False)
        base = f"nl/{day}-{word}"
        p.add("slug", path=base, entity_id=election, is_current=True)
        for row in rows.iter_rows(named=True):
            values.extend(_contest(p, row, election, base, slugs, known, station_units))
    p.frame("result_value", pl.concat(values))
    return p


def _contest(
    p: Package,
    row: dict[str, object],
    election: str,
    base: str,
    slugs: set[str],
    known: dict[str, tuple[str, str | None]],
    station_units: dict[str, str],
) -> list[pl.DataFrame]:
    eml, code, category, day = str(row["eml"]), str(row["code"]), str(row["category"]), row["day"]
    assert isinstance(day, date)
    frame = counts(code).filter(pl.col("eml") == eml)
    if frame.height == 0:
        return []
    affiliations = parsed(code, "affiliations").filter(pl.col("eml") == eml)
    labels = dict(zip(affiliations["aff"], affiliations["name"], strict=True))
    contest = key("contest", eml)
    answers = category == "NR"
    if category in ("TK", "EP", "NR"):
        district, body_id = COUNTRY, body("TK", "NL") if category == "TK" else None
        title = {"TK": "Tweede Kamer", "EP": "Europees Parlement"}.get(category) or str(row["question"] or row["name"])
        path = (
            f"{base}/{changeset.slug({'TK': 'tweede-kamer', 'EP': 'europees-parlement'}.get(category, 'referendum'))}"
        )
    elif category == "PS":
        province = PROVINCES[_suffix(eml)]
        district, body_id = area("nl.provincie", province), body("PS", province)
        title = f"Provinciale Staten {PROVINCE_NAMES[province]}"
        path = f"{base}/{changeset.slug(PROVINCE_NAMES[province])}"
    elif category == "AB":
        board = _suffix(eml)
        district, body_id = area("nl.waterschap", board), body("AB", board)
        title = f"Algemeen bestuur {_board_name(eml)}"
        path = f"{base}/{changeset.slug(_board_name(eml))}"
    else:
        municipality = str(row["domain_id"] or "")
        if municipality not in known:
            municipality = next(iter(frame.filter(pl.col("kind") == "510b")["authority"].unique().to_list()), "")
        district, body_id = area("nl.gemeente", municipality), body("GR", municipality)
        title = f"Gemeenteraad {known[municipality][0]}"
        path = f"{base}/{changeset.slug(known[municipality][0])}"
    if path in slugs:
        path = f"{path}-{changeset.slug(_suffix(eml))}"
    slugs.add(path)
    seats = row["seats"]
    results = parsed(code, "candidates").filter(pl.col("eml") == eml)
    elected = results.filter(pl.col("elected"))
    won = Counter(elected["aff"].to_list())
    magnitude = None if answers else int(str(seats)) if seats is not None else sum(won.values()) or None
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="ballot_question" if answers else "list_allocation",
        body_id=body_id,
        district_area_id=district,
        magnitude=magnitude,
        round_number=1,
        default_result_kind="counted",
    )
    p.name(contest, title, "nl", "official")
    p.add("slug", path=path, entity_id=contest, is_current=True)
    if answers:
        p.add("ballot_question", contest_id=contest, type="optional_referendum")
    official, grain = _official(frame)
    stations = _stations(frame)
    options: dict[str, str] = {}
    present = sorted(
        {a for a in frame["aff"].drop_nulls().unique().to_list()} | {a for a in labels if not a.startswith("answer")},
        key=lambda a: (int(a) if a.isdigit() else 10**6, a),
    )
    for aff in present:
        option = options[aff] = key("option", eml, aff)
        text = " ".join(str(labels.get(aff) or "").split())
        if answers:
            text = labels.get(f"answer:{aff}") or text
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="answer" if answers else "list",
            party_id=party(text) if not answers and fold(text) else None,
            ballot_position=int(aff) if aff.isdigit() else None,
            ballot_label=text or f"Lijst {aff}",
        )
    stage = "preliminary" if code in PRELIMINARY else "official"
    official_set = key("result_set", eml, "municipal")
    station_set = key("result_set", eml, "station")
    p.add(
        "result_set",
        id=official_set,
        election_id=election,
        contest_id=contest,
        stage=stage,
        kind="counted",
        source_id=KIESRAAD,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=key("note", official_set),
        entity_id=official_set,
        kind="methodology",
        body_md=OFFICIAL_METHOD,
        lang="en",
    )
    valid = int(official.filter(pl.col("measure") == VALID)["value"].sum())
    station_valid = int(stations.filter(pl.col("measure") == VALID)["value"].sum())
    out: list[pl.DataFrame] = []
    out.append(
        official.select(
            result_set_id=pl.lit(official_set),
            unit_id=pl.col("municipality").map_elements(lambda c: unit("nl.gemeente", c), return_dtype=pl.String),
            option_id=pl.col("aff").replace_strict(options, default=None, return_dtype=pl.String),
            measure=pl.col("measure"),
            value=pl.col("value"),
        )
    )
    if stations.height:
        p.add(
            "result_set",
            id=station_set,
            election_id=election,
            contest_id=contest,
            stage=stage,
            kind="counted",
            source_id=KIESRAAD,
            status="active",
            is_default=False,
            completeness=round(min(station_valid / valid, 1.0), 6) if valid else 0.0,
        )
        p.add(
            "note",
            id=key("note", station_set),
            entity_id=station_set,
            kind="methodology",
            body_md=STATION_METHOD,
            lang="en",
        )
        with_station = set(stations["unit"].str.split("::").list.first().unique().to_list())
        lacking = sorted(set(official["municipality"].unique().to_list()) - with_station)
        if lacking:
            listed = ", ".join(known.get(c, (c, None))[0] for c in lacking)
            p.add(
                "note",
                id=key("note", station_set, "missing"),
                entity_id=station_set,
                kind="caveat",
                body_md=f"The Kiesraad's archive has no polling-station figures for {len(lacking)} "
                f"municipalit{'y' if len(lacking) == 1 else 'ies'}: {listed}. Their votes are in the municipal "
                "result set only.",
                lang="en",
            )
        for raw_id in stations["unit"].unique().to_list():
            if raw_id in station_units:
                continue
            municipality = raw_id.split("::")[0]
            id = station_units[raw_id] = unit("nl.stembureau", f"{day}:{raw_id}")
            abroad = municipality == ABROAD
            p.add(
                "reporting_unit",
                id=id,
                polity_id=NETHERLANDS,
                scheme_code="nl.stembureau",
                code=f"{day}:{raw_id}",
                parent_id=unit("nl.gemeente", municipality) if municipality in known else None,
                area_relation="unlocated" if abroad or municipality not in known else "within",
                area_id=None if abroad or municipality not in known else area("nl.gemeente", municipality),
                kind="abroad_mission" if abroad else "standard",
                valid_from=day,
                valid_from_precision="day",
            )
        out.append(
            stations.select(
                result_set_id=pl.lit(station_set),
                unit_id=pl.col("unit").replace_strict(station_units, return_dtype=pl.String),
                option_id=pl.col("aff").replace_strict(options, default=None, return_dtype=pl.String),
                measure=pl.col("measure"),
                value=pl.col("value"),
            )
        )
    declared = _declared(frame)
    summed = official.group_by("measure", "aff").agg(pl.col("value").sum().alias("summed"))
    joined = declared.join(summed, on=["measure", "aff"], how="left", nulls_equal=True)
    agree = joined.filter(pl.col("value") == pl.col("summed"), pl.col("measure") != REGISTERED)
    differ = joined.filter(pl.col("value") != pl.col("summed") | pl.col("summed").is_null())
    for measure, aff, value in agree.select("measure", "aff", "value").iter_rows():
        p.add(
            "declared",
            result_set_id=official_set,
            option_id=None if aff is None else options[aff],
            measure=measure,
            value=value,
        )
    TOTALS_REPORT[eml] = (
        f"{grain}; valid {valid:,} vs declared "
        f"{declared.filter(pl.col('measure') == VALID)['value'].sum():,}; "
        f"{agree.height} figures agree, {differ.height} differ"
        + (f" ({differ.select('measure', 'aff', 'value', 'summed').rows()[:6]})" if differ.height else "")
        + f"; stations hold {station_valid:,} valid"
    )
    if not answers:
        persons = results.filter(pl.col("elected")).unique(["aff", "candidate"], keep="first")
        for aff, n in sorted(won.items()):
            if aff in options:
                p.add("outcome", result_set_id=official_set, option_id=options[aff], kind="won", seats=n)
        for entry in persons.iter_rows(named=True):
            if entry["aff"] not in options:
                continue
            tag = f"{eml}:{entry['aff']}:{entry['candidate']}"
            person = key("person", tag)
            p.add("person", id=person, gender={"male": "male", "female": "female"}.get(str(entry["gender"])))
            p.name(person, str(entry["name"]), "nl", "official")
            candidacy = key("candidacy", tag)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=options[entry["aff"]],
                role="list_member",
                list_position=int(entry["candidate"]) if str(entry["candidate"]).isdigit() else None,
                ballot_name=entry["name"],
                is_independent=False,
            )
            p.add(
                "outcome",
                result_set_id=official_set,
                option_id=options[entry["aff"]],
                candidacy_id=candidacy,
                kind="elected",
            )
    else:
        tally = official.filter(pl.col("measure") == VOTES).group_by("aff").agg(pl.col("value").sum())
        if tally.height:
            top = tally.sort("value", descending=True).row(0)
            p.add("outcome", result_set_id=official_set, option_id=options[top[0]], kind="won")
    return [
        frame_.with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16))
        for frame_ in out
    ]
