import html
import json
import re
import struct
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID, uuid5

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.package import NAMESPACE, Package
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
E3_LIVE = (
    "https://www.kpu.go.id/dmdocuments/DPD.pdf",
    (
        "Model E 3 DPD: Daftar Terpilih Anggota Dewan Perwakilan Daerah Pemilihan Umum Tahun 2009, per daerah "
        "pemilihan provinsi (KPU, kpu.go.id/dmdocuments/DPD.pdf)"
    ),
    KPU,
)
PAPUA_INTERIM = (
    "https://s.mkri.id/public/content/persidangan/putusan/ptusan_sidang_81phpu_A.pdf",
    (
        "Putusan Sela Mahkamah Konstitusi Nomor 47-81/PHPU.A-VII/2009 tentang Perselisihan Hasil Pemilihan Umum "
        "Anggota DPD Provinsi Papua (9 June 2009)"
    ),
    COURT,
)
PAPUA_FINAL = (
    (
        "https://s.mkri.id/public/content/persidangan/putusan/"
        "putusan_sidang_Putusan%20Yahukimo%20Nomor%2047.81PHPU.A.VII.2009%20telah%20baca_30%20Sept%202009.pdf"
    ),
    (
        "Putusan Mahkamah Konstitusi Nomor 47-81/PHPU.A-VII/2009 tentang Perselisihan Hasil Pemilihan Umum Anggota "
        "DPD Provinsi Papua (30 September 2009)"
    ),
    COURT,
)
SULTRA_E3 = (
    "https://web.archive.org/web/20260930235817id_/https://www.kpu.go.id/dmdocuments/DPD.pdf",
    E3_LIVE[1] + ", archived copy",
    KPU,
)
SULTRA_RULING = (
    "https://s.mkri.id/public/content/persidangan/putusan/putusan_sidang_29_PHPU_A_VII_2009.pdf",
    (
        "Putusan Mahkamah Konstitusi Nomor 29/PHPU.A-VII/2009 tentang Perselisihan Hasil Pemilihan Umum Anggota DPD "
        "Provinsi Sulawesi Tenggara (read 8 June 2009)"
    ),
    COURT,
)
COUNTS = {2004: 32, 2009: 33}
VOTES_2004 = (
    "https://web.archive.org/web/20040611230329id_/http://www.kpu.go.id:80/suara/hasilsuara_dpd_sah.php",
    (
        "Hasil Perhitungan Suara Sah DPD: all provinces (KPU, kpu.go.id/suara/hasilsuara_dpd_sah.php, archived "
        "11 June 2004)"
    ),
    KPU,
)


def fetch() -> None:
    raw.fetch(SOURCE, VOTES_2004[0], suffix=".php")
    for url, _, _ in (E3_LIVE, PAPUA_INTERIM, PAPUA_FINAL):
        raw.fetch(SOURCE, url, suffix=".pdf")
    for url, _, _ in (SULTRA_E3, SULTRA_RULING):
        try:
            raw.fetch(SOURCE, url, suffix=".pdf")
        except Exception as error:  # the archived copy is stored from the archive's own fetch when the host refuses
            print(f"skipped {url}: {error}")
    for url, suffix in ((E3[0], ".pdf"), (RULING[0], ".pdf"), (LIST_2004[0], ".htm")):
        try:
            raw.fetch(SOURCE, url, suffix=suffix)
        except Exception as error:  # the changeset pipeline's older files are optional for the package
            print(f"skipped {url}: {error}")


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
    try:
        raw.cached(SOURCE, E3[0])
        raw.cached(SOURCE, RULING[0])
        raw.cached(SOURCE, LIST_2004[0])
    except LookupError:
        pass
    else:
        _members()
    papua_2009().write()
    sultra_2009().write()
    try:
        raw.cached(SOURCE, VOTES_2004[0])
    except LookupError:
        pass
    else:
        votes_2004().write()


def _members() -> None:
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


# The 2009 Papua and Sulawesi Tenggara DPD contests are built as a package, since the changeset above holds the
# other 31 provinces and leaves these two out. Papua's winners changed through the Constitutional Court's ruling
# 47-81/PHPU.A-VII/2009 after a re-vote and recount in Yahukimo.
PAPUA = "PAPUA"
PAPUA_KEY = "2009-04-09:dpd:91"
INDONESIA = str(uuid5(NAMESPACE, "polity\x1fID"))
# Rows production already holds, from the id.kpu_dpd_early changeset and the national reporting units. The contest
# leaves its electoral system empty: reference partitions do not carry electoral systems, so a package cannot name one.
ELECTION_2009 = "198b6991-7161-533e-80d4-feeab9ab89c4"
DPD_BODY = "2142082d-1247-5971-9ccc-cc94ad9897ee"
PAPUA_UNIT = "3c75eaab-b7df-5cf1-8320-a458d8f38472"
DERIVED = 1  # result_value.flags: a figure computed from the sources' figures, not one a source prints
# E 3 name, the name in the Court's ruling, and the name in KPU's decree table as quoted in the interim ruling.
PAPUA_CANDIDATES = (
    ("TONNY TESAR", "Toni Tesar", "Tonny Tesar", "Tonny Tesar"),
    ("HELINA MURIB", "Herlina Murib", "Helina Murib", "Helina Murib"),
    (
        "PAULUS YOHANES SUMINO, Drs, MM",
        "Paulus Yohanes Sumino",
        "Paulus Yohanes Sumino, SE.MM",
        "Paulus Yohannes Sumino",
    ),
    ("FERDINANDA W. IBO YATIPAY", "Ferdinanda W. Ibo Yatipay", "Ferdinanda W.Ibo Yatipay", "Ferdinanda W. Ibo Yatipay"),
    (None, "Pdt. Elion Numberi, S.Th.", "Pdt. Elion Numberi, STh", None),
)
PAPUA_E3_NOTE = (
    "The four members declared elected in Papua and their valid votes, from Model E 3 DPD as published by KPU in May "
    "2009 (the list of elected candidates under KPU decree 255/Kpts/KPU/Tahun 2009). Losing candidates and the "
    "provincial total are not in this form, so the set is incomplete. It counts the Yahukimo figures that the "
    "Constitutional Court later set aside."
)
PAPUA_COURT_NOTE = (
    "The Constitutional Court (ruling 47-81/PHPU.A-VII/2009 of 30 September 2009, after its interim ruling of 9 June "
    "2009) found the DPD votes recorded in Yahukimo regency invalid, ordered a re-vote in 37 districts and a recount "
    "in 14, and set each candidate's correct Yahukimo votes. This set replaces KPU's Yahukimo figures with the "
    "Court's and keeps the rest of each province-wide total as KPU declared it. It lists only the five candidates "
    "whose rank the change decided: Elion Numberi rose from sixth to third and Paulus Yohanes Sumino fell from third "
    "to fourth, which put Ferdinanda W. Ibo Yatipay out of the four seats. The other ten candidates of the province "
    "are not listed, and the province's total of valid votes is not stated, so the set is incomplete."
)
PAPUA_METHOD = (
    "Each total is computed: the province-wide votes of Model E 3 DPD (for Numberi, KPU's total as quoted in the "
    "interim ruling, since E 3 lists only the elected), less the votes KPU recorded in Yahukimo (185,296 valid votes, "
    "as quoted in the interim ruling), plus the votes the Court set for Yahukimo in the final ruling. Numberi's "
    "figure rests on the 195,778 votes that the interim ruling quotes from the decree's appendix; KPU's provincial "
    "record gave 195,428, and either figure leaves him third."
)


def _figure(text: str) -> int:
    return int(text.replace(".", "").replace(",", ""))


def _page_of(path: Path, needle: str) -> int:
    return next(index for index, body in enumerate(_text(path).split("\f"), 1) if needle in body)


def _papua_votes() -> list[dict[str, object]]:
    """Each candidate's province-wide votes under E 3 and under the Court's ruling, with the figures behind them."""
    e3 = raw.cached(SOURCE, E3_LIVE[0]).path
    pages = _text(e3, "-layout").split("\f")
    page = next(
        index
        for index, body in enumerate(pages, 1)
        if (found := re.search(r"DAERAH PEMILIHAN PROVINSI\s*:\s*(.+)", body)) and found[1].strip() == PAPUA
    )
    elected = {
        m[2].strip(): (_figure(m[3]), int(m[1]))
        for m in re.finditer(r"^\s*([1-4])\s{2,}(\S.+?)\s{2,}([\d,]+)\s+([1-4])\s*$", pages[page - 1], re.MULTILINE)
    }
    interim = " ".join(_text(raw.cached(SOURCE, PAPUA_INTERIM[0]).path, "-layout").split())
    # KPU's decree table as quoted by the petitioner: name, valid votes, rank.
    decree = {
        re.sub(r"\s+", " ", m[1]).strip(): _figure(m[2])
        for m in re.finditer(
            r"\d{1,2} ([A-Z][A-Za-z.,\s]+?) (\d{1,3}\.\d{3}) \d{1,2}(?= \d{1,2} |\s*Jumlah)",
            interim.split("Jumlah Perolehan Suara")[0].split("Suara Sah Calon", 1)[-1],
        )
    }
    recorded = re.search(r"jumlah suara sah adalah ([\d.]+)", interim)
    old = {
        name: _figure(votes)
        for name, votes in re.findall(
            r"(Paulus Yohannes Sumino|Tonny Tesar|Terry J Antoh Asmuruf|Annie Numberi|Ferdinanda W\. Ibo Yatipay|"
            r"Sofia S\. Monim)\s+(?:dicatat dengan jumlah |sebesar )?([\d.]+) suara",
            interim.split("dicatat dengan jumlah", 1)[0][-200:] + interim.split("Bukti P-3", 1)[0],
        )
    }
    if recorded is None:
        raise ValueError("interim ruling no longer states Yahukimo's valid votes")
    final = _text(raw.cached(SOURCE, PAPUA_FINAL[0]).path, "-layout")
    amar = final.split("4. AMAR PUTUSAN", 1)[1]
    court = {
        re.sub(r"\s+", " ", m[1]).strip(): _figure(m[2])
        for m in re.finditer(r"^\s*\d{1,2}\s+(\S.+?)\s{2,}([\d.]+)\s*$", amar, re.MULTILINE)
    }
    if sum(old.values()) != _figure(recorded[1]) or len(court) != 15:
        raise ValueError(f"Yahukimo figures {old} and {court} do not match the rulings' totals")
    rows: list[dict[str, object]] = []
    for e3_name, ruling_name, decree_name, quoted in PAPUA_CANDIDATES:
        before = elected[e3_name][0] if e3_name else decree[decree_name]
        recorded_votes = next((v for k, v in old.items() if quoted and k.split()[-1] == quoted.split()[-1]), 0)
        if e3_name and decree[decree_name] != before:
            raise ValueError(f"{e3_name}: E 3 {before} disagrees with the decree table {decree[decree_name]}")
        rows.append(
            {
                "name": e3_name or ruling_name,
                "e3": elected[e3_name][0] if e3_name else None,
                "e3_page": page if e3_name else None,
                "before": before,
                "recorded": recorded_votes,
                "court": court[ruling_name],
                "votes": before - recorded_votes + court[ruling_name],
            }
        )
    rows.sort(key=lambda row: -int(str(row["votes"])))
    return rows


def papua_2009() -> Package:
    p = Package(
        "id.kpu_dpd_early/dpd-2009-papua",
        SOURCE,
        "The 2009 Regional Representative Council election in Papua, with KPU's declared members and the winners "
        "after the Constitutional Court's Yahukimo re-vote",
    )
    p.external |= {INDONESIA, ELECTION_2009, DPD_BODY, PAPUA_UNIT}
    sources: dict[str, str] = {}
    artifacts: dict[str, str] = {}
    for url, title, publisher in (E3_LIVE, PAPUA_INTERIM, PAPUA_FINAL):
        sources[url] = p.id("source", url)
        court = publisher == COURT
        p.add(
            "source",
            id=sources[url],
            kind="official_body",
            publisher=publisher,
            title=title,
            url=url,
            license=f"Public record: official {'court ruling' if court else 'publication'}, no license stated",
            license_url="https://www.mkri.id" if court else "https://www.kpu.go.id",
        )
        artifacts[url] = p.artifact(raw.cached(SOURCE, url), sources[url])
    votes = _papua_votes()
    e3_pages: dict[int, str] = {}

    def page(number: int) -> str:
        if number not in e3_pages:
            cached = raw.cached(SOURCE, E3_LIVE[0])
            image = DATA / "pages" / SOURCE / f"{cached.sha256}-{number}.png"
            if not image.exists():
                image.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                    + [str(cached.path), str(image.with_suffix(""))],
                    check=True,
                )
            width, height = struct.unpack(">II", image.read_bytes()[16:24])
            e3_pages[number] = p.id("page", E3_LIVE[0], number)
            p.add(
                "source_page",
                id=e3_pages[number],
                artifact_id=artifacts[E3_LIVE[0]],
                page_number=number,
                image_key=f"pages/{SOURCE}/{image.name}",
                width=width,
                height=height,
            )
        return e3_pages[number]

    contest = p.id("contest", PAPUA_KEY)
    p.add(
        "contest",
        id=contest,
        election_id=ELECTION_2009,
        kind="body_seats",
        body_id=DPD_BODY,
        district_area_id=None,
        term="full",
        magnitude=4,
        round_number=1,
        default_result_kind="counted",
    )
    p.name(contest, "2009 Regional Representative Council election in Papua", "en", "english")
    p.name(contest, "Pemilihan Umum Anggota DPD Tahun 2009 Provinsi Papua", "id", "official")
    p.add("slug", path="id/2009-04-09-legislative/dpd-papua", entity_id=contest, is_current=True)
    declared = p.id("result_set", PAPUA_KEY, "certified")
    revised = p.id("result_set", PAPUA_KEY, "court_revised")
    for id, stage, url, default, completeness in (
        (declared, "certified", E3_LIVE[0], False, 0),
        (revised, "court_revised", PAPUA_FINAL[0], True, 0),
    ):
        p.add(
            "result_set",
            id=id,
            election_id=ELECTION_2009,
            contest_id=contest,
            stage=stage,
            kind="counted",
            source_id=sources[url],
            status="active",
            is_default=default,
            completeness=completeness,
        )
    for id, kind, body in (
        (declared, "methodology", PAPUA_E3_NOTE),
        (revised, "methodology", PAPUA_COURT_NOTE),
        (revised, "caveat", PAPUA_METHOD),
    ):
        p.add("note", id=p.id("note", id, kind), entity_id=id, kind=kind, body_md=body, lang="en")
    e3_path = raw.cached(SOURCE, E3_LIVE[0]).path
    for row in votes:
        ballot = str(row["name"])
        tag = f"{PAPUA_KEY}:{slug(ballot)}"
        option = p.id("option", tag)
        person = p.id("person", tag)
        candidacy = p.id("candidacy", tag)
        p.add("contest_option", id=option, contest_id=contest, kind="candidate", party_id=None, ballot_label=ballot)
        p.add("person", id=person)
        p.name(person, ballot, "id", "official")
        p.add(
            "candidacy",
            id=candidacy,
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=ballot,
            is_independent=True,
        )
        fact = {"unit_id": PAPUA_UNIT, "option_id": option, "channel": 0, "measure": 1, "count_round": 0}
        top = votes.index(row) < 4
        if row["e3"] is not None:
            number = int(str(row["e3_page"]))
            p.add("outcome", result_set_id=declared, option_id=option, kind="elected", seats=1)
            p.add("result_value", **fact, result_set_id=declared, flags=0, value=int(str(row["e3"])))
            box = _box(e3_path, number, f"{int(str(row['e3'])):,}")
            if box is not None:
                x0, y0, x1, y1 = box
                p.add(
                    "value_locator",
                    **fact,
                    result_set_id=declared,
                    page_id=page(number),
                    source_id=None,
                    x0=x0,
                    y0=y0,
                    x1=x1,
                    y1=y1,
                )
        p.add(
            "outcome",
            result_set_id=revised,
            option_id=option,
            kind="elected" if top else "lost",
            seats=1 if top else None,
        )
        p.add("result_value", **fact, result_set_id=revised, flags=DERIVED, value=int(str(row["votes"])))
    return p


SULTRA_KEY = "2009-04-09:dpd:74"
SULTRA_AREA = "9003683a-707c-5780-a14e-40c436ef98ac"
SULTRA_UNIT = "ed721e6e-32df-5f4a-907a-749410258421"
SULTRA_E3_NOTE = (
    "The four members declared elected in Sulawesi Tenggara and their valid votes, from Model E 3 DPD as published by "
    "KPU in May 2009 (the list of elected candidates under KPU decree 255/Kpts/KPU/Tahun 2009). Losing candidates "
    "and the provincial total are not in this form, so the set is incomplete. Hoesein Effendy is listed fourth with "
    "29,052 votes, before the Constitutional Court raised Kamaruddin's total."
)
SULTRA_COURT_NOTE = (
    "The Constitutional Court (ruling 29/PHPU.A-VII/2009, deliberated 7 June and read 8 June 2009) partly granted "
    "the petition of candidate Drs. H. Kamaruddin (ballot number 23), annulled KPU decree 255/Kpts/KPU/Tahun 2009 "
    "as to his votes, and set them at 29,385 instead of 28,985. That is more than Hoesein Effendy's 29,052, so "
    "Kamaruddin takes the fourth seat. This set lists every candidate of the province as KPU's decree table, "
    "quoted in the ruling, gave them, with only Kamaruddin's figure replaced. The province's total of valid votes "
    "is not stated, so the set has no total."
)


def sultra_2009() -> Package:
    p = Package(
        "id.kpu_dpd_early/dpd-2009-sultra",
        SOURCE,
        "The 2009 Regional Representative Council election in Sulawesi Tenggara, with KPU's declared members and the "
        "winners after the Constitutional Court's ruling on Kamaruddin's votes",
    )
    p.external |= {INDONESIA, ELECTION_2009, DPD_BODY, SULTRA_AREA, SULTRA_UNIT}
    sources: dict[str, str] = {}
    artifacts: dict[str, str] = {}
    for url, title, publisher in (SULTRA_E3, SULTRA_RULING):
        sources[url] = p.id("source", url)
        court = publisher == COURT
        p.add(
            "source",
            id=sources[url],
            kind="official_body",
            publisher=publisher,
            title=title,
            url=url,
            license=f"Public record: official {'court ruling' if court else 'publication'}, no license stated",
            license_url="https://www.mkri.id" if court else "https://www.kpu.go.id",
        )
        artifacts[url] = p.artifact(raw.cached(SOURCE, url), sources[url])
    e3_path = raw.cached(SOURCE, SULTRA_E3[0]).path
    pages = _text(e3_path, "-layout").split("\f")
    number = next(
        i
        for i, body in enumerate(pages, 1)
        if (found := re.search(r"DAERAH PEMILIHAN PROVINSI\s*:\s*(.+)", body))
        and found[1].strip() == "SULAWESI TENGGARA"
    )
    elected = {
        m[2].strip(): (_figure(m[3]), int(m[1]))
        for m in re.finditer(
            r"^\s*([1-4])\s{2,}([A-Z].+?)\s{2,}([\d,]+)\s+([1-4])\s*$", pages[number - 1], re.MULTILINE
        )
    }
    if len(elected) != 4:
        raise ValueError(f"expected four members on E 3 page {number}, found {elected}")
    ruling = _text(raw.cached(SOURCE, SULTRA_RULING[0]).path, "-layout")
    table = ruling.split("Peringkat", 1)[1].split("Jumlah", 1)[0]
    # KPU's table comes first; the petitioner's own claimed figures follow it
    rows = re.findall(r"^\s*\d{1,2}\s+(\S.+?)\s{2,}(\d{1,3}\.\d{3})\s+(\d{1,2})\s*$", table, re.MULTILINE)[:62]
    candidates = {re.sub(r"\s+", " ", name).strip(): (_figure(votes), int(rank)) for name, votes, rank in rows}
    if len(candidates) != 62:
        raise ValueError(f"{len(candidates)} candidates in the ruling's table, not 62")
    amar = ruling.split("5. AMAR PUTUSAN", 1)[1]
    if not re.search(r"sejumlah\s+29\.385", amar):
        raise ValueError("the ruling no longer sets Kamaruddin's votes at 29,385")
    kamaruddin = "KAMARUDDIN, H., Drs."
    if candidates[kamaruddin][0] != 28985:
        raise ValueError("the ruling's table no longer gives Kamaruddin 28,985")
    court = {name: votes for name, (votes, _) in candidates.items()}
    court[kamaruddin] = 29385
    ranked = sorted(court, key=lambda name: -court[name])
    for name, (votes, _) in elected.items():
        if court[next(k for k in court if k.split(",")[0] == name.split(",")[0])] != votes:
            raise ValueError(f"{name}: E 3 {votes} disagrees with the ruling's table")
    winners = set(ranked[:4])
    contest = p.id("contest", SULTRA_KEY)
    p.add(
        "contest",
        id=contest,
        election_id=ELECTION_2009,
        kind="body_seats",
        body_id=DPD_BODY,
        district_area_id=SULTRA_AREA,
        term="full",
        magnitude=4,
        round_number=1,
        default_result_kind="counted",
    )
    p.name(contest, "2009 Regional Representative Council election in Sulawesi Tenggara", "en", "english")
    p.name(contest, "Pemilihan Umum Anggota DPD Tahun 2009 Provinsi Sulawesi Tenggara", "id", "official")
    p.add("slug", path="id/2009-04-09-legislative/dpd-sulawesi-tenggara", entity_id=contest, is_current=True)
    declared = p.id("result_set", SULTRA_KEY, "certified")
    revised = p.id("result_set", SULTRA_KEY, "court_revised")
    for id, stage, url, default in (
        (declared, "certified", SULTRA_E3[0], False),
        (revised, "court_revised", SULTRA_RULING[0], True),
    ):
        p.add(
            "result_set",
            id=id,
            election_id=ELECTION_2009,
            contest_id=contest,
            stage=stage,
            kind="counted",
            source_id=sources[url],
            status="active",
            is_default=default,
            completeness=0,
        )
    for id, body in ((declared, SULTRA_E3_NOTE), (revised, SULTRA_COURT_NOTE)):
        p.add("note", id=p.id("note", id, "methodology"), entity_id=id, kind="methodology", body_md=body, lang="en")
    image = DATA / "pages" / SOURCE / f"{raw.cached(SOURCE, SULTRA_E3[0]).sha256}-{number}.png"
    if not image.exists():
        image.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
            + [str(e3_path), str(image.with_suffix(""))],
            check=True,
        )
    width, height = struct.unpack(">II", image.read_bytes()[16:24])
    page = p.id("page", SULTRA_E3[0], number)
    p.add(
        "source_page",
        id=page,
        artifact_id=artifacts[SULTRA_E3[0]],
        page_number=number,
        image_key=f"pages/{SOURCE}/{image.name}",
        width=width,
        height=height,
    )
    for ballot in ranked:
        tag = f"{SULTRA_KEY}:{slug(ballot)}"
        option = p.id("option", tag)
        person = p.id("person", tag)
        p.add("contest_option", id=option, contest_id=contest, kind="candidate", party_id=None, ballot_label=ballot)
        p.add("person", id=person)
        p.name(person, ballot, "id", "official")
        p.add(
            "candidacy",
            id=p.id("candidacy", tag),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=ballot,
            is_independent=True,
        )
        fact = {"unit_id": SULTRA_UNIT, "option_id": option, "channel": 0, "measure": 1, "count_round": 0}
        e3_name = next((k for k in elected if k.split(",")[0] == ballot.split(",")[0]), None)
        if e3_name is not None:
            p.add("outcome", result_set_id=declared, option_id=option, kind="elected", seats=1)
            p.add("result_value", **fact, result_set_id=declared, flags=0, value=elected[e3_name][0])
            box = _box(e3_path, number, f"{elected[e3_name][0]:,}")
            if box is not None:
                x0, y0, x1, y1 = box
                p.add(
                    "value_locator",
                    **fact,
                    result_set_id=declared,
                    page_id=page,
                    source_id=None,
                    x0=x0,
                    y0=y0,
                    x1=x1,
                    y1=y1,
                )
        top = ballot in winners
        p.add(
            "outcome",
            result_set_id=revised,
            option_id=option,
            kind="elected" if top else "lost",
            seats=1 if top else None,
        )
        p.add("result_value", **fact, result_set_id=revised, flags=0, value=court[ballot])
        p.add("value_locator", **fact, result_set_id=revised, page_id=None, source_id=sources[SULTRA_RULING[0]])
    return p


# KPU's count of each candidate's votes in the 2004 DPD election, onto the contests and options that the changeset
# above holds for the four members declared elected in each province. The ids file maps each province's code on KPU's
# page to the contest, the reporting unit, and the options of those members (by ballot number) that production holds.
ELECTION_2004 = "b05d3054-0285-5735-906e-c9478432b9ec"
NATION = "518ec4bb-3c49-597a-9cb0-071beb1a090b"
IDS_2004 = Path(__file__).with_name("kpu_dpd_2004_ids.json")
VALID = 4  # result_value.measure: valid votes
VOTES_2004_NOTE = (
    "Each candidate's votes and the province's total of valid votes, as KPU's results page for the DPD election "
    "showed them in June 2004, from the first archived copy of the page. The page lists every candidate of the "
    "province in order of votes, with the ballot number, and marks the first four, who took the province's four "
    "seats, in red. KPU's later list of the members declared elected (August 2004) is the certified set; it names "
    "KH. Achmad Chalwani in Central Java where this page's first four include Drs. H. Dahlan Rais, M. Hum."
)


def _votes_page() -> dict[str, list[tuple[str, int, int, str]]]:
    """The page's tables by province name: each candidate's name, ballot number, votes, and the marker color."""
    text = raw.cached(SOURCE, VOTES_2004[0]).path.read_bytes().decode("cp1252")
    tables: dict[str, list[tuple[str, int, int, str]]] = {}
    for table in text.split("<tr bgcolor=FC9601>")[1:]:
        name = re.search(r"color=white>([^<]+)</font>", table)
        if name is None:
            raise ValueError("province table without a name")
        rows: list[tuple[str, int, int, str]] = []
        total = None
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.DOTALL):
            cells = [
                html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
                for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
            ]
            color = re.search(r"color=(red|blue)>", row)
            if len(cells) == 4 and cells[1].isdigit():
                rows.append(
                    (re.sub(r"\s+", " ", cells[0]), int(cells[1]), _figure(cells[2]), color[1] if color else "")
                )
            elif cells and cells[0] == "Total":
                total = _figure(cells[1])
        if total is None or sum(votes for _, _, votes, _ in rows) != total:
            raise ValueError(f"{name[1]}: candidates do not sum to the printed total")
        rows.append(("Total", 0, total, ""))
        tables[name[1]] = rows
    return tables


def votes_2004() -> Package:
    p = Package(
        "id.kpu_dpd_early/dpd-2004-votes",
        SOURCE,
        "Each candidate's votes in the 2004 Regional Representative Council election in 32 provinces, from KPU's "
        "results page",
    )
    ids = json.loads(IDS_2004.read_text())
    tables = _votes_page()
    if len(tables) != len(ids):
        raise ValueError(f"{len(tables)} provinces on the page, {len(ids)} in the ids file")
    url, title, publisher = VOTES_2004
    source = p.id("source", url)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher=publisher,
        title=title,
        url=url,
        license="Public record: official publication, no license stated",
        license_url="https://www.kpu.go.id",
    )
    p.artifact(raw.cached(SOURCE, url), source)
    p.external |= {INDONESIA, ELECTION_2004, NATION, "id.provinsi"}
    for code, entry in sorted(ids.items()):
        key = f"2004-04-05:dpd:{code}"
        rows = tables[entry["name"]]
        contest = entry["contest"]
        unit = entry["unit"]
        if unit is None:  # South Sulawesi together with West Sulawesi, created in October 2004
            unit = p.id("unit", entry["unit_code"])
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=INDONESIA,
                scheme_code="id.provinsi",
                code=entry["unit_code"],
                area_relation="within",
                area_id=NATION,
                kind="standard",
            )
            p.name(unit, "Sulawesi Selatan", "id", "official")
            p.name(unit, "South Sulawesi, including West Sulawesi", "en", "english")
        p.external |= {contest, unit} if entry["unit"] else {contest}
        result_set = p.id("result_set", key, "official")
        p.add(
            "result_set",
            id=result_set,
            election_id=ELECTION_2004,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=0,
        )
        p.add(
            "note",
            id=p.id("note", result_set, "methodology"),
            entity_id=result_set,
            kind="methodology",
            body_md=VOTES_2004_NOTE,
            lang="en",
        )
        for ballot, number, votes, _ in rows:
            fact: dict[str, object]
            if ballot == "Total":
                fact = {"unit_id": unit, "option_id": None, "channel": 0, "measure": VALID, "count_round": 0}
            else:
                option = entry["options"].get(str(number))
                if option is None:
                    tag = f"{key}:{number}"
                    option = p.id("option", tag)
                    person = p.id("person", tag)
                    p.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=None,
                        ballot_position=number,
                        ballot_label=ballot,
                    )
                    p.add("person", id=person)
                    p.name(person, ballot, "id", "official")
                    p.add(
                        "candidacy",
                        id=p.id("candidacy", tag),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=ballot,
                        is_independent=True,
                    )
                else:
                    p.external.add(option)
                fact = {"unit_id": unit, "option_id": option, "channel": 0, "measure": 1, "count_round": 0}
            p.add("result_value", **fact, result_set_id=result_set, flags=0, value=votes)
            p.add("value_locator", **fact, result_set_id=result_set, page_id=None, source_id=source)
    return p
