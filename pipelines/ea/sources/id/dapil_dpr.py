import re
import subprocess
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.id import big
from ea.store import DATA, connect

SOURCE = "id.dapil_dpr"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SCHEME = "id.dapil_dpr"
PKPU = "https://jdih.kpu.go.id/peraturan-kpu/download/417"
PKPU_TITLE = (
    "Peraturan Komisi Pemilihan Umum Nomor 6 Tahun 2023 tentang Daerah Pemilihan dan Alokasi Kursi Anggota "
    "Dewan Perwakilan Rakyat, Dewan Perwakilan Rakyat Daerah Provinsi, dan Dewan Perwakilan Rakyat Daerah "
    "Kabupaten/Kota dalam Pemilihan Umum Tahun 2024"
)
LAW = "https://jdih.dkpp.go.id/common/dokumen/UU-No-7-Tahun-2017.pdf"
LAW_TITLE = "Undang-Undang Nomor 7 Tahun 2017 tentang Pemilihan Umum"
KPU = "Komisi Pemilihan Umum Republik Indonesia"
LAW_DAY = date(2017, 8, 16)
PERPPU_DAY = date(2022, 12, 12)
ELECTIONS = {"2019": date(2019, 4, 17), "2024": date(2024, 2, 14)}
PAGE_15 = {
    "Papua Selatan": ("Mappi", "Asmat"),
    "Papua Tengah": (
        "Nabire",
        "Puncak Jaya",
        "Paniai",
        "Mimika",
        "Puncak",
        "Dogiyai",
        "Intan Jaya",
        "Deiyai",
    ),
    "Papua Pegunungan": (
        "Jayawijaya",
        "Pegunungan Bintang",
        "Yahukimo",
        "Tolikara",
        "Mamberamo Tengah",
        "Yalimo",
        "Lanny Jaya",
        "Nduga",
    ),
    "Papua Barat": (
        "Manokwari",
        "Fak Fak",
        "Teluk Bintuni",
        "Teluk Wondama",
        "Kaimana",
        "Manokwari Selatan",
        "Pegunungan Arfak",
    ),
    "Papua Barat Daya": ("Sorong", "Sorong Selatan", "Raja Ampat", "Tambrauw", "Maybrat", "Kota Sorong"),
}
PROVINCE_ALIASES = {"daerahkhususibukotajakarta": "31"}
ALIASES = {"kotapadangsidempuan": "kotapadangsidimpuan"}
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10, "XI": 11}
VALID: dict[str, dict[str, date | None]] = {
    "both": {"valid_from": LAW_DAY, "valid_to": None},
    "2019": {"valid_from": LAW_DAY, "valid_to": PERPPU_DAY},
    "2024": {"valid_from": PERPPU_DAY, "valid_to": None},
}
SEATS = 580
FIELD = re.compile(r"\S+(?: \S+)*")


def fetch() -> None:
    raw.fetch(SOURCE, PKPU, suffix=".pdf")
    raw.fetch(SOURCE, LAW, suffix=".pdf")


def _squash(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def _lampiran() -> list[tuple[str, str, int, list[str]]]:
    text = subprocess.run(
        ["pdftotext", "-layout", "-f", "5", "-l", "14", str(raw.cached(SOURCE, PKPU).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    provinces: list[list[str]] = []
    heads: list[tuple[int, int]] = []
    names: list[str] = []
    items: list[list[str]] = []
    lines = text.splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith("No."))
    for line in lines[start:]:
        if re.search(r"Pemilihan|Kursi|jdih|Jumlah|Kecamatan\)|^\s*(No\.|-)", line):
            continue
        fields = [(m.group(0), m.start()) for m in FIELD.finditer(re.sub(r"(?<=\s)(\d{1,2})\.\s+", r"\1. ", line))]
        seats = [int(field) for field, column in fields if 40 <= column < 62 and field.isdigit()]
        for field, column in fields:
            if column < 4 and field.isdigit():
                provinces.append([])
            elif column < 22 and not field.isdigit():
                provinces[-1].append(field)
            elif 22 <= column < 48 and not field.isdigit():
                if seats:
                    heads.append((len(provinces) - 1, seats[0]))
                    names.append(field)
                    items.append([])
                else:
                    names[-1] = f"{names[-1]} {field}"
            elif column >= 55 and not (field.isdigit() and column < 62):
                if (item := re.match(r"\d+\. (.*)", field)) is not None or seats:
                    items[-1].append(field if item is None else item.group(1))
                else:
                    items[-1][-1] = f"{items[-1][-1]} {field}"
    titles = [re.sub(r"\s*\(.*$", "", " ".join(words)) for words in provinces]
    rows = []
    for (index, seats), name, listed in zip(heads, names, items, strict=True):
        province = titles[index]
        if province in PAGE_15 and province != "Papua Selatan":
            raise ValueError(f"{province} is on page 15")
        rows.append((province, name, seats, listed + list(PAGE_15[province] if province == "Papua Selatan" else ())))
    for province, listed in PAGE_15.items():
        if province != "Papua Selatan":
            rows.append((province, province, 3, list(listed)))
    return rows


def _province(name: str) -> str:
    squashed = _squash(name)
    if squashed in PROVINCE_ALIASES:
        return PROVINCE_ALIASES[squashed]
    found = [code for code, (_, _, native, _) in big.PROVINCES.items() if _squash(native) == squashed]
    if len(found) != 1:
        raise ValueError(f"province {name} not found")
    return found[0]


def parse() -> None:
    rows = _lampiran()
    with connect() as conn:
        kabkota: dict[tuple[str, str], tuple[str, UUID]] = {}
        for code, area, text in conn.execute(
            """select c.code, a.id, n.text from area a
               join area_code c on c.area_id = a.id and c.valid @> %s::date
               join name n on n.entity_id = a.id and n.lang = 'id' and n.kind = 'official'
               where a.scheme_code = 'id.kabkota'""",
            [ELECTIONS["2024"]],
        ).fetchall():
            bare = re.sub(r"^(Kabupaten Administrasi|Kota Administrasi|Kabupaten|Kota)\s+", "", text)
            city = text.startswith("Kota ") and not text.startswith("Kota Administrasi")
            kabkota[code[:2], _squash(("Kota " if city else "") + bare)] = (code, area)
        dapils: list[dict[str, Any]] = []
        errors = []
        seen: dict[UUID, str] = {}
        for title, name, seats, listed in rows:
            province = _province(title)
            numeral = name.rsplit(" ", 1)[-1]
            number = ROMAN.get(numeral, 1)
            members = []
            for item in listed:
                label = re.sub(r"\s*\+\s*Luar\s+Negeri$", "", item)
                key = ALIASES.get(_squash(label), _squash(label))
                if (province, key) not in kabkota:
                    errors.append(f"{name}: {item} not found in {province}")
                    continue
                code, area = kabkota[province, key]
                if area in seen:
                    errors.append(f"{name}: {item} already in {seen[area]}")
                seen[area] = name
                members.append(area)
            dapils.append(
                {
                    "vintage": "2024",
                    "code": f"{province}{number:02d}",
                    "province": province,
                    "name": big.PROVINCES[province][2] + (f" {numeral}" if numeral in ROMAN else ""),
                    "english": big.PROVINCES[province][3] + (f" {numeral}" if numeral in ROMAN else ""),
                    "seats": seats,
                    "abroad": any("Luar Negeri" in item for item in listed),
                    "members": members,
                }
            )
        if len(seen) != len(kabkota):
            errors.append(f"{len(seen)} of {len(kabkota)} regencies and cities assigned")
        if len(dapils) != 84 or sum(d["seats"] for d in dapils) != SEATS:
            errors.append(f"{len(dapils)} dapil and {sum(d['seats'] for d in dapils)} seats")
        if len({d["code"] for d in dapils}) != len(dapils):
            errors.append("duplicate dapil codes")
        if errors:
            raise ValueError("; ".join(errors[:20]))

        held = conn.execute(
            """select n.text, array_agg(distinct ru.area_id)
               from contest c
               join slug s on s.entity_id = c.id and s.path like 'id/2019-04-17-general/dpr/%%'
               join name n on n.entity_id = c.id and n.lang = 'id' and n.kind = 'official'
               join result_set rs on rs.contest_id = c.id
               join result_value rv on rv.result_set_k = rs.k
               join reporting_unit ru on ru.k = rv.unit_k and ru.scheme_code = 'id.kabkota'
               group by n.text"""
        ).fetchall()
        if len(held) != 80:
            raise ValueError(f"{len(held)} DPR 2019 contests published, not 80")
        later = {frozenset(d["members"]): d for d in dapils}
        parents = dict(
            conn.execute(
                """select p.area_id, c.code from area_parent p
                   join area_code c on c.area_id = p.parent_id and c.valid @> %s::date
                   join area a on a.id = p.area_id and a.scheme_code = 'id.kabkota'
                   where p.valid @> %s::date""",
                [ELECTIONS["2019"], ELECTIONS["2019"]],
            ).fetchall()
        )
        for text, members in held:
            members = frozenset(members)
            if members in later:
                later[members]["vintage"] = "both"
                continue
            provinces = {parents[member] for member in members}
            if len(provinces) != 1 or provinces & {"91", "92"} != provinces:
                raise ValueError(f"2019 {text}: no 2024 dapil has the same regencies and cities")
            (province,) = provinces
            if {member for member, parent in parents.items() if parent == province} != members:
                raise ValueError(f"2019 {text}: not the whole province {province}")
            dapils.append(
                {
                    "vintage": "2019",
                    "code": f"{province}01",
                    "province": province,
                    "name": big.PROVINCES[province][2],
                    "english": big.PROVINCES[province][3],
                    "seats": 10 if province == "91" else 3,
                    "abroad": False,
                    "members": sorted(members),
                }
            )
        if sum(d["vintage"] == "2024" for d in dapils) != 6 or sum(d["vintage"] == "2019" for d in dapils) != 2:
            raise ValueError("only the Papua dapil should differ between 2019 and 2024")
        shapes = conn.execute(
            """select k, encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(geom), 3))), 'hex'),
                      encode(st_asewkb(st_pointonsurface(geom)), 'hex')
               from (select k, st_union(g.geom) as geom
                     from unnest(%s::int[], %s::uuid[]) as m(k, area_id)
                     join area_geometry g on g.area_id = m.area_id
                     group by k) u""",
            [
                [index for index, d in enumerate(dapils) for _ in d["members"]],
                [member for d in dapils for member in d["members"]],
            ],
        ).fetchall()
    geometry = {index: (geom, point) for index, geom, point in shapes}
    frame = pl.DataFrame(
        [
            {
                **d,
                "members": [str(member) for member in d["members"]],
                "geom": geometry[index][0],
                "label_point": geometry[index][1],
            }
            for index, d in enumerate(dapils)
        ]
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "dapil.parquet")
    print(frame.group_by("vintage").len().sort("vintage"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "dapil.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Indonesia's House of Representatives electoral districts: 80 for 2019 and 84 for 2024",
            polity="ID",
        )

        def name(entity: UUID, text: str, lang: str = "id", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        regulation = cs.source(KPU, PKPU_TITLE)
        cs.add(
            "source",
            id=regulation,
            kind="official_body",
            publisher=KPU,
            title=PKPU_TITLE,
            url=PKPU,
            license="Public record: official regulation, no license stated",
            notes=(
                "Lampiran I, pages 5 to 15: each DPR dapil for 2024 with its seats and its regencies and cities. "
                "Page 15 has no text layer and was read by eye. DKI Jakarta II also counts the votes cast abroad."
            ),
        )
        cs.artifact(raw.cached(SOURCE, PKPU), regulation)
        law = cs.source("Dewan Kehormatan Penyelenggara Pemilu", LAW_TITLE)
        cs.add(
            "source",
            id=law,
            kind="official_body",
            publisher="Dewan Kehormatan Penyelenggara Pemilu",
            title=LAW_TITLE,
            url=LAW,
            license="Public record: statute, no license stated",
            notes=(
                "Lampiran III lists the 80 DPR dapil used in 2019. The certified 2019 results by regency and city "
                "(published from the KPU DD1-DPR certificates) give the same composition, and match the 2024 "
                "dapil everywhere except Papua and Papua Barat, which Perppu 1/2022 split by the new provinces."
            ),
        )
        cs.artifact(raw.cached(SOURCE, LAW), law)
        shapes = cs.source(big.PUBLISHER, big.TITLE)

        indonesia = cs.polity("ID")
        cs.add(
            "scheme",
            code=SCHEME,
            polity_id=indonesia,
            kind="electoral_district",
            rank=2,
            noun="electoral district",
            kinds=None,
            code_system="kpu",
        )
        for row in frame.sort("code", "vintage").iter_rows(named=True):
            valid = VALID[row["vintage"]]
            on = None if row["vintage"] == "both" else ELECTIONS[row["vintage"]]
            area = cs.area(SCHEME, row["code"], on)
            cs.add("area", id=area, scheme_code=SCHEME)
            cs.add("area_code", area_id=area, code=row["code"], **valid)
            cs.add("area_parent", area_id=area, parent_id=cs.area("id.provinsi", row["province"]), **valid)
            for member in row["members"]:
                cs.add("area_membership", area_id=UUID(member), parent_id=area, partial=False, **valid)
            cs.add(
                "area_geometry",
                area_id=area,
                geom=row["geom"],
                label_point=row["label_point"],
                quality="official",
                source_id=shapes,
                **valid,
            )
            name(area, row["name"])
            if row["english"] != row["name"]:
                name(area, row["english"], "en", "english")
        for vintage, as_of, valid_from, valid_to in (
            ("2019", ELECTIONS["2019"], LAW_DAY, PERPPU_DAY),
            ("2024", ELECTIONS["2024"], PERPPU_DAY, None),
        ):
            cs.add(
                "boundary_set",
                scheme_code=SCHEME,
                vintage=vintage,
                valid_from=valid_from,
                valid_to=valid_to,
                as_of=as_of,
            )
        return cs.write()
