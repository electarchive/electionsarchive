from datetime import date
from pathlib import Path
from uuid import UUID

from ea.changeset import Changeset, slug
from ea.sources.gb import umit
from ea.store import connect

SOURCE = "gb.leh_unopposed"
VERSION = "1"
DAYS = {2021: date(2021, 5, 6), 2022: date(2022, 5, 5)}
NOTICE = "The council's notice declares the candidates elected without a poll; the Elections Centre workbook behind gb.leh omits the ward."
SOPN = (
    "The council's statement of persons nominated lists one candidate for one seat; no declaration of the result was "
    "found, so the unopposed return is read from the statement."
)
SHROPSHIRE = "https://shropshire.gov.uk/media/18344/albrighton-uncontested.pdf"
HUNTINGDONSHIRE = "https://www.huntingdonshire.gov.uk/election-2022/may-2022-election-results/"
CARMARTHENSHIRE = "https://www.sirgar.llyw.cymru/media/1228165/lacharn.pdf"
NPT = "https://www.npt.gov.uk/32678"
FLINTSHIRE = "https://www.flintshire.gov.uk/en/PDFFiles/Council-Democracy/Elections-22/SOPN-FCC.pdf"
WARDS = (
    (2021, "Shropshire Council", "E06000051", "E05008137", "Albrighton", 1, SHROPSHIRE, NOTICE, (("Nigel Peter Lumby", "conservative"),)),
    (
        2022,
        "Huntingdonshire District Council",
        "E07000011",
        "E05011264",
        "Holywell-cum-Needingworth",
        2,
        f"{HUNTINGDONSHIRE}holywell-cum-needingworth-ward",
        NOTICE,
        (("Paul James Hodgson-Jones", "conservative"), ("Jon Neish", "conservative")),
    ),
    (
        2022,
        "Huntingdonshire District Council",
        "E07000011",
        "E05011276",
        "Sawtry",
        2,
        f"{HUNTINGDONSHIRE}sawtry-ward",
        NOTICE,
        (("Simon Bywater", "conservative"), ("Ross Ben Martin", "conservative")),
    ),
    (
        2022,
        "Carmarthenshire County Council",
        "W06000010",
        "W05001199",
        "Laugharne Township",
        1,
        CARMARTHENSHIRE,
        NOTICE + " The notice prints no party.",
        (("Pamela Jane Tremlett", None),),
    ),
    (
        2022,
        "Neath Port Talbot County Borough Council",
        "W06000012",
        "W05001701",
        "Gwynfi and Croeserw",
        1,
        "https://www.npt.gov.uk/32544",
        NOTICE + " The notice prints no party.",
        (("Scott Jones", None),),
    ),
    (
        2022,
        "Neath Port Talbot County Borough Council",
        "W06000012",
        "W05001700",
        "Gwaun-Cae-Gurwen and Lower Brynamman",
        2,
        NPT,
        NOTICE + " The notice prints no party.",
        (("Sonia Helen Reynolds", None), ("Sarah Amber Thomas", None)),
    ),
    (2022, "Flintshire County Council", "W06000005", "W05001581", "Caerwys", 1, FLINTSHIRE, SOPN, (("Steve Copple", "independent"),)),
    (
        2022,
        "Flintshire County Council",
        "W06000005",
        "W05001595",
        "Higher Kinnerton",
        1,
        FLINTSHIRE,
        SOPN,
        (("Mike Allport", "independent"),),
    ),
)


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Unopposed council ward returns in 2021 and 2022 missing from the Elections Centre workbooks, from the councils' own notices",
            polity="GB",
        )

        def name(entity: UUID, text: str, kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind=kind, is_preferred=kind == "official")

        gb = cs.polity("GB")
        fptp = cs.key("gb.electoral_system", "fptp")
        block = cs.key("gb.electoral_system", "block")
        parties: dict[str, UUID | None] = {"conservative": cs.key(*umit.EXISTING["conservative"]), "independent": None}
        sources: dict[str, UUID] = {}
        for year, council, la, ward, label, seats, url, note, people in WARDS:
            day = DAYS[year]
            polity = cs.key("gb.polity", la)
            body = cs.key("gb.body", la)
            election = cs.key("gb.election", f"{day}:local:{la}")
            if {polity, body, election} & cs.minted:
                raise ValueError(f"{la} {year}: publish gb.leh first")
            if url not in sources:
                sources[url] = cs.source(council, f"Uncontested election notice, {label}, {year}")
                cs.add(
                    "source",
                    id=sources[url],
                    kind="official_body",
                    publisher=council,
                    title=f"Uncontested election notice, {label}, {year}",
                    url=url,
                    notes=note,
                )
            path = conn.execute("select path from slug where entity_id = %s", [polity]).fetchone()[0]
            council_name = conn.execute(
                "select text from name where entity_id = %s and is_preferred limit 1", [polity]
            ).fetchone()[0]
            area = cs.area("gb.ward", ward)
            unit = cs.unit("gb.ward", ward)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code="gb.ward")
                cs.add("area_code", area_id=area, code=ward, valid_from=None, valid_to=None)
                cs.add("area_parent", area_id=area, parent_id=cs.area("gb.lad", la), valid_from=None, valid_to=None)
                name(area, label)
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=polity,
                    scheme_code="gb.ward",
                    code=ward,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
                name(unit, label)
            contest_key = f"{day}:local:{ward}"
            contest = cs.key("gb.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=body,
                district_area_id=area,
                magnitude=seats,
                round_number=1,
                electoral_system_id=fptp if seats == 1 else block,
                default_result_kind="tacit",
            )
            name(contest, f"{label} ward, {council_name}, {year}")
            cs.add("slug", path=f"{path}/{day}-local/{slug(label)}-{ward.lower()}", entity_id=contest)
            result_set = cs.key("gb.leh_unopposed.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="tacit",
                status="active",
                source_id=sources[url],
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="caveat", body_md=note)
            for number, (person_name, party_key) in enumerate(people, start=1):
                option_key = f"{contest_key}:{number}"
                option = cs.key("gb.leh_unopposed.option", option_key)
                person = cs.key("gb.leh_unopposed.person", option_key)
                cs.add("person", id=person)
                name(person, person_name)
                party = parties[party_key] if party_key is not None else None
                cs.add("contest_option", id=option, contest_id=contest, kind="candidate", party_id=party, ballot_label=person_name)
                cs.add(
                    "candidacy",
                    id=cs.key("gb.leh_unopposed.candidacy", option_key),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=person_name,
                    member_of_party_id=party,
                    is_independent=party_key == "independent",
                )
                cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        return cs.write()
