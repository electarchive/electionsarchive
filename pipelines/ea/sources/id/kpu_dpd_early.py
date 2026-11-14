import html
import re
import struct
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.id.kpu_pilpres import PROVINCES
from ea.store import DATA, connect

SOURCE = "id.kpu_dpd_early"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
KPU = "Komisi Pemilihan Umum Republik Indonesia"
COURT = "Mahkamah Konstitusi Republik Indonesia"
DAYS = {2004: date(2004, 4, 5), 2009: date(2009, 4, 9)}
E3 = (
    "https://web.archive.org/web/20090525id_/http://www.kpu.go.id/dmdocuments/DPD.pdf",
    (
        "Model E 3 DPD: Daftar Terpilih Anggota Dewan Perwakilan Daerah Pemilihan Umum Tahun 2009, per daerah "
        "pemilihan provinsi (KPU, kpu.go.id/dmdocuments/DPD.pdf, archived 25 May 2009)"
    ),
    KPU,
)
RULING = (
    (
        "https://s.mkri.id/public/content/persidangan/putusan/"
        "Perkara%20no%2033%20PHPU-A-VII-2009%2015%20JUNI%202009%20--%20DPD%20NAD%20-%20Mursyid.pdf"
    ),
    (
        "Putusan Mahkamah Konstitusi Nomor 33/PHPU.A-VII/2009 tentang Perselisihan Hasil Pemilihan Umum Anggota DPD "
        "Provinsi Nanggroe Aceh Darussalam (15 June 2009)"
    ),
    COURT,
)
LIST_2004 = (
    "https://web.archive.org/web/20040806072658id_/http://www.kpu.go.id:80/berita/link/calegDPD.htm",
    (
        "Penetapan Calon Terpilih Anggota Dewan Perwakilan Daerah dalam Pemilihan Umum Tahun 2004: Nama-nama Calon "
        "Terpilih (KPU, kpu.go.id/berita/link/calegDPD.htm, dated 3 August 2004)"
    ),
    KPU,
)
ALIASES = {
    "NANGGROE ACEH DARUSSALAM": "ACEH",
    "BANGKA BELITUNG": "KEPULAUAN BANGKA BELITUNG",
    "D. I. YOGYAKARTA": "DAERAH ISTIMEWA YOGYAKARTA",
    "D.I. YOGYAKARTA": "DAERAH ISTIMEWA YOGYAKARTA",
    "IRIAN JAYA BARAT": "PAPUA BARAT",
}
SPANNING = {
    "KALIMANTAN TIMUR": ("64", "65"),
    "PAPUA": ("91", "93", "94", "95"),
    "PAPUA BARAT": ("92", "96"),
}
SPANNING_2004 = {"SULAWESI SELATAN": ("73", "76")}
HELD_2009 = {"SULAWESI TENGGARA", "PAPUA"}
ACEH_RULING = ("MURSYID", 20, 118149, "118.149")
COUNTS = {2004: 32, 2009: 33}


def fetch() -> None:
    for url, _, _ in (E3, RULING):
        raw.fetch(SOURCE, url, suffix=".pdf")
    raw.fetch(SOURCE, LIST_2004[0], suffix=".htm")


def _text(path: Path, *args: str) -> str:
    return subprocess.run(["pdftotext", *args, str(path), "-"], check=True, capture_output=True, text=True).stdout


def _box(path: Path, page: int, figure: str) -> list[float] | None:
    out = _text(path, "-bbox", "-f", str(page), "-l", str(page))
    size = re.search(r'<page width="([\d.]+)" height="([\d.]+)"', out)
    hits = re.findall(
        rf'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">{re.escape(figure)}<', out
    )
    if size is None or len(hits) != 1:
        return None
    width, height = float(size[1]), float(size[2])
    x0, y0, x1, y1 = (float(side) for side in hits[0])
    return [x0 / width, y0 / height, x1 / width, y1 / height]


def _codes(province: str, year: int) -> tuple[str, ...]:
    if province in SPANNING:
        return SPANNING[province]
    return SPANNING_2004.get(province, (PROVINCES[province],)) if year == 2004 else (PROVINCES[province],)


def _2009() -> list[dict[str, object]]:
    path = raw.cached(SOURCE, E3[0]).path
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for page, text in enumerate(_text(path, "-layout").split("\f"), 1):
        heading = re.search(r"DAERAH PEMILIHAN PROVINSI\s*:\s*(.+)", text)
        if heading is None:
            continue
        label = heading[1].strip()
        province = ALIASES.get(label, label)
        if province in seen:
            continue
        seen.add(province)
        found = [
            (int(m[1]), m[2].strip(), m[3], int(m[4]))
            for m in re.finditer(r"^\s*([1-4])\s{2,}(\S.+?)\s{2,}([\d,]+)\s+([1-4])\s*$", text, re.MULTILINE)
            if m[2].strip() != "2"
        ]
        if [entry[0] for entry in found] != [1, 2, 3, 4] or [entry[3] for entry in found] != [1, 2, 3, 4]:
            raise ValueError(f"{label}: rows {found}")
        votes = [int(entry[2].replace(",", "")) for entry in found]
        if votes != sorted(votes, reverse=True) or len(set(votes)) != 4:
            raise ValueError(f"{label}: votes {votes} out of rank order")
        for (_, name, figure, rank), value in zip(found, votes, strict=True):
            rows.append(
                {
                    "year": 2009,
                    "province": province,
                    "name": name,
                    "number": None,
                    "votes": value,
                    "rank": rank,
                    "elected": True,
                    "url": E3[0],
                    "page": page,
                    "box": _box(path, page, figure),
                }
            )
    if len(seen) != COUNTS[2009]:
        raise ValueError(f"E-3 lists {len(seen)} provinces")
    ruling = raw.cached(SOURCE, RULING[0]).path
    name, number, value, figure = ACEH_RULING
    text = _text(ruling, "-layout")
    if f"menjadi {figure} suara" not in re.sub(r"\s+", " ", text):
        raise ValueError("ruling 33/PHPU.A-VII/2009 no longer states Mursyid's total")
    pages = text.split("\f")
    page = next(index for index, body in enumerate(pages, 1) if figure in body and "Mursyid" in body)
    aceh = sorted((row for row in rows if row["province"] == "ACEH"), key=lambda row: -int(str(row["votes"])))
    if value <= int(str(aceh[3]["votes"])) or value >= int(str(aceh[2]["votes"])):
        raise ValueError("Mursyid's corrected total does not fall between third and fourth place")
    aceh[3]["elected"] = False
    rows.append(
        {
            "year": 2009,
            "province": "ACEH",
            "name": name,
            "number": number,
            "votes": value,
            "rank": None,
            "elected": True,
            "url": RULING[0],
            "page": page,
            "box": _box(ruling, page, figure),
        }
    )
    return [row for row in rows if row["province"] not in HELD_2009]


def _2004() -> list[dict[str, object]]:
    text = raw.cached(SOURCE, LIST_2004[0]).path.read_bytes().decode("cp1252")
    cells = [
        re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()
        for cell in re.findall(
            r"<td.*?</td>", re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL), re.DOTALL | re.IGNORECASE
        )
    ]
    provinces = [name.strip().upper() for name in re.split(r"\s*\d+\.\s+", cells[2]) if name.strip()]
    names = [name.strip() for name in re.split(r"(?:^|\s)[1-4]\.\s+", cells[3]) if name.strip()]
    if len(provinces) != COUNTS[2004] or len(names) != 4 * COUNTS[2004]:
        raise ValueError(f"2004 list: {len(provinces)} provinces, {len(names)} names")
    rows: list[dict[str, object]] = []
    for index, label in enumerate(provinces):
        province = ALIASES.get(label, label)
        for rank, name in enumerate(names[4 * index : 4 * index + 4], 1):
            rows.append(
                {
                    "year": 2004,
                    "province": province,
                    "name": name,
                    "number": None,
                    "votes": None,
                    "rank": rank,
                    "elected": True,
                    "url": LIST_2004[0],
                    "page": None,
                    "box": None,
                }
            )
    return rows


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    rows = _2004() + _2009()
    for row in rows:
        _codes(str(row["province"]), int(str(row["year"])))
    frame = pl.DataFrame(rows, infer_schema_length=None)
    frame.write_parquet(PARSED / "members.parquet")
    for year in DAYS:
        part = frame.filter(pl.col("year") == year)
        print(
            f"{year}: {part['province'].n_unique()} provinces, {part.filter(pl.col('elected')).height} elected, "
            f"{part.filter(pl.col('votes').is_not_null()).height} values"
        )


ELECTIONS = {
    2004: ("2004 Indonesian legislative election", "Pemilihan Umum Anggota DPR, DPD, dan DPRD Tahun 2004"),
    2009: ("2009 Indonesian legislative election", "Pemilihan Umum Anggota DPR, DPD, dan DPRD Tahun 2009"),
}
NOTES = {
    2004: (
        "The four members declared elected in each of 32 provinces, from KPU's list of elected DPD members of the 2004 "
        "election as published on kpu.go.id (dated 3 August 2004, after the Constitutional Court's rulings on the DPD "
        "results). The list gives names only, in KPU's order; no candidate votes are online from KPU for 2004. West "
        "Sulawesi, created in October 2004, voted within South Sulawesi; West Irian Jaya (now West Papua and Southwest "
        "Papua) voted as its own province; North Kalimantan voted within East Kalimantan."
    ),
    2009: (
        "The four members declared elected in each province and their valid votes, from Model E 3 DPD as published by "
        "KPU in May 2009 (the list of elected candidates under KPU decree 255/Kpts/KPU/Tahun 2009). Losing candidates "
        "and provincial totals are not in this form, so the set is incomplete. North Kalimantan voted within East "
        "Kalimantan, and Papua and West Papua included the provinces later split from them."
    ),
}
ACEH_NOTE = (
    "The Constitutional Court (ruling 33/PHPU.A-VII/2009 of 15 June 2009) set Mursyid's (ballot number 20) votes in "
    "Bener Meriah at 48,022, for a provincial total of 118,149, above the 101,808 of A Khalid, whom Model E 3 DPD had "
    "declared fourth. Mursyid took the fourth seat; the other figures are those of Model E 3 DPD."
)
SYSTEM_NOTE = (
    "Each province elects four members. Voters mark one candidate, and the four candidates with the most valid votes "
    "in the province are elected (UU 12/2003 for 2004, UU 10/2008 for 2009, UU 8/2012 for 2014)."
)


def emit() -> Path:
    members = pl.read_parquet(PARSED / "members.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Indonesia's Regional Representative Council (DPD) elections of 2004 and 2009: the four members "
                "declared elected in each province, with their votes in 2009 from KPU's Model E 3 DPD"
            ),
            polity="ID",
        )

        def name(entity: UUID, text: str, lang: str = "id", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        indonesia = cs.polity("ID")
        nation = cs.area("id.nation", "ID")
        body = cs.key("id.body", "dpd")
        system = cs.key("id.electoral_system", "dpd:sntv-4")
        if system in cs.minted:
            cs.add("electoral_system", id=system, polity_id=indonesia, rules={"formula": "sntv"}, rules_version=1)
            cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM_NOTE)

        sources: dict[str, UUID] = {}
        artifacts: dict[str, UUID] = {}
        for url, title, publisher in (E3, RULING, LIST_2004):
            sources[url] = cs.source(publisher, title)
            court = publisher == COURT
            cs.add(
                "source",
                id=sources[url],
                kind="official_body",
                publisher=publisher,
                title=title,
                url=url,
                license=f"Public record: official {'court ruling' if court else 'publication'}, no license stated",
                license_url="https://www.mkri.id" if court else "https://www.kpu.go.id",
            )
            artifacts[url] = cs.artifact(raw.cached(SOURCE, url), sources[url])

        pages: dict[tuple[str, int], UUID] = {}

        def page(url: str, number: int) -> UUID:
            if (url, number) not in pages:
                cached = raw.cached(SOURCE, url)
                image = DATA / "pages" / SOURCE / f"{cached.sha256}-{number}.png"
                if not image.exists():
                    image.parent.mkdir(parents=True, exist_ok=True)
                    subprocess.run(
                        ["pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                        + [str(cached.path), str(image.with_suffix(""))],
                        check=True,
                    )
                width, height = struct.unpack(">II", image.read_bytes()[16:24])
                found = pages[url, number] = cs.page(artifacts[url], number)
                cs.add(
                    "source_page",
                    id=found,
                    artifact_id=artifacts[url],
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
            return pages[url, number]

        units: dict[str, UUID] = {}

        def unit(codes: tuple[str, ...], label: str) -> UUID:
            code = "+".join(codes)
            if code not in units:
                found = units[code] = cs.unit("id.provinsi", code)
                if found in cs.minted:
                    whole = len(codes) == 1
                    cs.add(
                        "reporting_unit",
                        id=found,
                        polity_id=indonesia,
                        scheme_code="id.provinsi",
                        code=code,
                        area_relation="equals" if whole else "within",
                        area_id=cs.area("id.provinsi", code) if whole else nation,
                        kind="standard",
                        valid_from=None,
                    )
                    if not whole:
                        name(found, label)
                        name(found, f"{label}, including West Sulawesi", "en", "english")
            return units[code]

        for year, day in DAYS.items():
            stamp = day.isoformat()
            election = cs.key("id.election", stamp)
            cs.add(
                "election",
                id=election,
                polity_id=indonesia,
                kind="general",
                date_from=day,
                date_to=day,
                date_precision="day",
                status="held",
            )
            english, official = ELECTIONS[year]
            name(election, english, "en", "english")
            name(election, official)
            cs.add("slug", path=f"id/{stamp}-legislative", entity_id=election)
            rows = members.filter(pl.col("year") == year)
            for province in sorted(set(rows["province"])):
                codes = _codes(province, year)
                key = f"{stamp}:dpd:{codes[0]}"
                contest = cs.key("id.contest", key)
                label = province.title().replace("Dki", "DKI")
                if year == 2004 and province == "PAPUA BARAT":
                    label = "Irian Jaya Barat"
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=body,
                    district_area_id=cs.area("id.provinsi", codes[0]) if len(codes) == 1 else None,
                    term="full",
                    magnitude=4,
                    round_number=1,
                    previous_round_id=None,
                    electoral_system_id=system,
                    default_result_kind="counted",
                )
                name(contest, f"{year} Regional Representative Council election in {label}", "en", "english")
                name(contest, f"Pemilihan Umum Anggota DPD Tahun {year} Provinsi {label}")
                cs.add("slug", path=f"id/{stamp}-legislative/dpd-{slug(label)}", entity_id=contest)
                revised = year == 2009 and province == "ACEH"
                result_set = cs.key(f"{SOURCE}.result_set", key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="court_revised" if revised else "certified",
                    kind="counted",
                    status="active",
                    source_id=sources[RULING[0] if revised else LIST_2004[0] if year == 2004 else E3[0]],
                    is_default=True,
                    completeness=0,
                )
                cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTES[year])
                if revised:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=ACEH_NOTE)
                for ballot, number, votes, elected, url, number_, box in (
                    rows.filter(pl.col("province") == province)
                    .select("name", "number", "votes", "elected", "url", "page", "box")
                    .rows()
                ):
                    tag = f"{key}:{slug(ballot)}"
                    option = cs.key("id.option", tag)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=None,
                        ballot_position=number,
                        ballot_label=ballot,
                    )
                    person = cs.key(f"{SOURCE}.person", tag)
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, ballot)
                    cs.add(
                        "candidacy",
                        id=cs.key("id.candidacy", tag),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=ballot,
                        is_independent=True,
                    )
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="elected" if elected else "lost",
                        seats=1 if elected else None,
                    )
                    if votes is None:
                        continue
                    fact = {
                        "result_set_id": result_set,
                        "unit_id": unit(codes, label),
                        "option_id": option,
                        "channel": 0,
                        "measure": 1,
                        "count_round": 0,
                    }
                    cs.add("result_value", **fact, flags=0, value=votes)
                    if box is None:
                        continue
                    x0, y0, x1, y1 = box
                    cs.add(
                        "value_locator",
                        **fact,
                        page_id=page(url, number_),
                        source_id=None,
                        x0=x0,
                        y0=y0,
                        x1=x1,
                        y1=y1,
                    )
        return cs.write()
