import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.tr.ysk import LICENSE, PROVINCES, PUBLISHER, upper
from ea.sources.tr.ysk_local import DATED, LABELS, title
from ea.sources.tr.ysk_mayors import CANDIDATES_2009
from ea.sources.tr.ysk_mayors import SOURCE as MAYORS
from ea.store import DATA, connect

SOURCE = "tr.ysk_candidates_2009"
VERSION = "1"
DAY = date(2009, 3, 29)
PAGE = "https://www.ysk.gov.tr/doc/sayfa/29-mart-2009-mahalli-idareler-genel-secimleri/2819.html"
SPELLINGS = {"KAZAN": "KAHRAMANKAZAN", "EYÜP": "EYÜPSULTAN"}
PARSED = DATA / "parsed" / SOURCE
CAVEAT = (
    "YSK has published the candidates for this race but, for 2009, no vote counts by municipality; its results "
    "for 2009 are by province and nationwide only. The mayor elected is not recorded here."
)


def parse() -> None:
    rows: list[dict[str, str]] = []
    for kind, link in zip(("ilce", "belde"), CANDIDATES_2009, strict=True):
        pdf = raw.cached(MAYORS, link).path
        text = subprocess.run(
            ["pdftotext", "-layout", str(pdf), "-"], check=True, capture_output=True, text=True
        ).stdout
        columns: list[tuple[str, int]] = []
        for line in text.splitlines():
            if line.startswith("İL ADI"):
                fields = [
                    "province",
                    "district",
                    *(["town"] if kind == "belde" else []),
                    "party",
                    "given",
                    "family",
                    "job",
                ]
                heads = [
                    "İL ADI",
                    "İLÇE ADI",
                    *(["BELDE ADI"] if kind == "belde" else []),
                    "PARTİ/BAĞIMSIZ",
                    "ADI",
                    "SOYADI",
                    "MESLEĞİ",
                ]
                columns = []
                start = 0
                for field, head in zip(fields, heads, strict=True):
                    at = line.index(head, line.index("PARTİ/BAĞIMSIZ") + 10 if field == "given" else start)
                    columns.append((field, at))
                    start = at + 1
                continue
            if not columns or not line.strip():
                continue
            record = {
                field: line[at : columns[index + 1][1] if index + 1 < len(columns) else len(line)].strip()
                for index, (field, at) in enumerate(columns)
            }
            if re.search(r"\s{2,}", record["given"]):
                record["given"], tail = re.split(r"\s{2,}", record["given"], maxsplit=1)
                record["family"] = tail + record["family"]
            if record["province"] and record["party"]:
                rows.append({"kind": kind, "town": "", **record})
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(PARSED / "candidates.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    plates = {upper(name): f"{plate:02}" for plate, name in enumerate(PROVINCES, 1)}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Turkey's 2009 mayoral candidates by municipality, without votes",
            polity="TR",
        )

        def name(entity: UUID, text: str, lang: str = "tr", kind: str = "official") -> None:
            cs.add(
                "name",
                entity_id=entity,
                text=text,
                lang=lang,
                kind=kind,
                is_preferred=kind == "official",
                valid_from=None,
                valid_from_precision="day",
                valid_to=None,
                valid_to_precision="day",
            )

        turkey = cs.polity("TR")
        districts = {
            (plate, upper(text)): id
            for plate, text, id in conn.execute(
                """select pc.code, n.text, a.id from area a
                   join area_parent ap on ap.area_id = a.id
                   join area p on p.id = ap.parent_id and p.scheme_code = 'tr.il'
                   join area_code pc on pc.area_id = p.id
                   join name n on n.entity_id = a.id
                   where a.scheme_code = 'tr.ilce'"""
            ).fetchall()
        }
        existing = {
            value for (value,) in conn.execute("select value from external_id where scheme = 'tr.polity'").fetchall()
        }
        mayoral = cs.key("tr.electoral_system", "mayor")
        parent = cs.key("tr.election", f"{DAY}:local")
        if cs.minted:
            raise ValueError("publish tr.ysk_local before tr.ysk_candidates_2009")
        party_names = dict(
            conn.execute(
                """select e.value, (select text from name n where n.entity_id = e.entity_id and n.kind = 'official'
                                    order by n.valid_from desc nulls last limit 1)
                   from external_id e where e.scheme = 'tr.party'"""
            ).fetchall()
        )
        source = cs.source(PUBLISHER, "2009 local election results")
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title="2009 local election results",
                url=PAGE,
                license=LICENSE,
            )
        artifacts = [cs.artifact(raw.cached(MAYORS, link), source) for link in CANDIDATES_2009]
        del artifacts
        seen: set[str] = set()
        people: set[UUID] = set()
        grouped = frame.group_by("kind", "province", "district", "town", maintain_order=True)
        for (kind, province, district, town), candidates in grouped:
            plate = plates[str(province)]
            province_slug = slug(PROVINCES[int(plate) - 1])
            central = str(district) in (str(province), "MERKEZ") or str(district).endswith(" MERKEZ")
            if kind == "ilce":
                parts = [province_slug] if central else [province_slug, slug(str(district))]
                english = title(str(province) if central else str(district))
            else:
                parts = [province_slug, "merkez" if central else slug(str(district)), slug(str(town))]
                english = title(str(town))
            key = "-".join(parts)
            path = "tr/" + "/".join(parts)
            if key in seen:
                raise ValueError(f"two 2009 municipalities share {key}")
            seen.add(key)
            lookup = SPELLINGS.get(str(district), str(district))
            area = districts.get((plate, upper(lookup)))
            polity = cs.key("tr.polity", key)
            office = cs.key("tr.office", f"{key}-mayor")
            if key not in existing:
                cs.add("polity", id=polity, kind="municipality", iso_code=None)
                name(polity, f"{english} Municipality", "en")
                name(polity, f"{english} Belediyesi", kind="native")
                cs.add("polity_parent", polity_id=polity, parent_id=turkey)
                cs.add("slug", path=path, entity_id=polity)
                cs.add("office", id=office, polity_id=polity, kind="mayor")
                name(office, f"Mayor of {english}", "en")
                name(office, f"{english} Belediye Başkanı", kind="native")
            election = cs.key("tr.election", f"{DAY}:{key}-mayor")
            cs.add(
                "election",
                id=election,
                polity_id=polity,
                parent_id=parent,
                kind="local",
                date_from=DAY,
                date_to=DAY,
                status="held",
            )
            name(election, f"2009 {english} mayoral election", "en")
            name(election, f"2009 {english} Belediye Başkanlığı Seçimi", kind="native")
            cs.add("slug", path=f"{path}/{DAY}-local", entity_id=election)
            contest_key = f"{DAY}:{key}-mayor"
            contest = cs.key("tr.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=area,
                magnitude=1,
                round_number=1,
                electoral_system_id=mayoral,
                default_result_kind="counted",
            )
            name(contest, f"Mayor of {english}", "en")
            cs.add("slug", path=f"{path}/{DAY}-local/mayor", entity_id=contest)
            cs.add("note", entity_id=contest, kind="caveat", body_md=CAVEAT)
            for row in candidates.iter_rows(named=True):
                label = re.sub(r"\s*\(.*\)$", "", row["party"])
                owner = None if label == "BAĞIMSIZ" else DATED.get((label, 2009), LABELS.get(label))
                if owner is None and label != "BAĞIMSIZ":
                    raise ValueError(f"no party for {label}")
                person_name = title(f"{row['given']} {row['family']}")
                option_key = f"{contest_key}:{owner or slug(person_name)}"
                option = cs.key("tr.option", option_key)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=None if owner is None else cs.key("tr.party", owner),
                    ballot_label=person_name if owner is None else party_names[owner],
                )
                person = cs.key(f"{SOURCE}.person", f"{contest_key}:{slug(person_name)}")
                if person not in people:
                    people.add(person)
                    cs.add("person", id=person)
                    name(person, person_name)
                candidacy = cs.key("tr.candidacy", f"{option_key}:{slug(person_name)}")
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="head",
                    member_of_party_id=None if owner is None else cs.key("tr.party", owner),
                    is_independent=owner is None,
                )
                if owner is not None:
                    cs.add(
                        "candidacy_nomination",
                        candidacy_id=candidacy,
                        party_id=cs.key("tr.party", owner),
                        kind="nominated",
                    )
        return cs.write()
