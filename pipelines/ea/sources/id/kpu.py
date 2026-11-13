import re
from datetime import date
from pathlib import Path
from uuid import UUID

import httpx

from ea import raw
from ea.changeset import Changeset
from ea.sources.id.big import ADMINISTRATIVE, PROVINCES
from ea.store import connect

SOURCE = "id.kpu"
VERSION = "1"
KPU = "Komisi Pemilihan Umum Republik Indonesia"
JDIH = "https://jdih.kpu.go.id/data/data_kepkpu/"
DECREES = {
    "2019": (
        (
            "Keputusan KPU Nomor 309/PL.01.1-Kpt/03/KPU/IV/2018 tentang Perubahan Kedua atas Keputusan KPU Nomor "
            "58/PL.01.1-Kpt/03/KPU/II/2018 tentang Penetapan Partai Politik Peserta Pemilihan Umum Tahun 2019"
        ),
        f"{JDIH}2018kpt309.pdf",
    ),
    "2024": (
        (
            "Keputusan KPU Nomor 519 Tahun 2022 tentang Penetapan Nomor Urut Partai Politik Peserta Pemilihan Umum "
            "Tahun 2024"
        ),
        f"{JDIH}2022kpt519.pdf",
    ),
    "2024u": (
        (
            "Keputusan KPU Nomor 552 Tahun 2022 tentang Perubahan atas Keputusan KPU Nomor 519 Tahun 2022 (nomor urut "
            "Partai Ummat)"
        ),
        f"{JDIH}2022kpt552.pdf",
    ),
}
LAWS = {
    "2017": (
        "Undang-Undang Nomor 7 Tahun 2017 tentang Pemilihan Umum",
        "https://peraturan.go.id/files/uu7-2017bt.pdf",
        "Kementerian Hukum Republik Indonesia",
    ),
    "2023": (
        (
            "Undang-Undang Nomor 7 Tahun 2023 tentang Penetapan Peraturan Pemerintah Pengganti Undang-Undang Nomor 1 "
            "Tahun 2022 tentang Perubahan atas Undang-Undang Nomor 7 Tahun 2017 tentang Pemilihan Umum menjadi "
            "Undang-Undang"
        ),
        "https://peraturan.go.id/files/uu-no-7-tahun-2023.pdf",
        "Kementerian Hukum Republik Indonesia",
    ),
}
SIZES = {
    "dpr": ((575, date(2019, 10, 1), date(2024, 10, 1)), (580, date(2024, 10, 1), None)),
    "dpd": ((136, date(2019, 10, 1), date(2024, 10, 1)), (152, date(2024, 10, 1), None)),
    "mpr": ((711, date(2019, 10, 1), date(2024, 10, 1)), (732, date(2024, 10, 1), None)),
}
BODIES = {
    "dpr": ("lower_house", "House of Representatives", "Dewan Perwakilan Rakyat", "DPR"),
    "dpd": ("upper_house", "Regional Representative Council", "Dewan Perwakilan Daerah", "DPD"),
    "mpr": ("council", "People's Consultative Assembly", "Majelis Permusyawaratan Rakyat", "MPR"),
}
OFFICES = {
    "presiden": ("head_of_state", "President of Indonesia", "Presiden Republik Indonesia"),
    "wakil-presiden": ("other", "Vice President of Indonesia", "Wakil Presiden Republik Indonesia"),
}
PAPUA = {"91", "92", "93", "94", "95", "96"}
PARTIES: dict[str, tuple[str, str, str | None, str, str | None, dict[str, int], tuple[str, ...]]] = {
    "pkb": (
        "Partai Kebangkitan Bangsa",
        "PKB",
        "National Awakening Party",
        "Q1257317",
        "2559",
        {"2019": 1, "2024": 1},
        (),
    ),
    "gerindra": (
        "Partai Gerakan Indonesia Raya",
        "Gerindra",
        "Great Indonesia Movement Party",
        "Q4261459",
        "4495",
        {"2019": 2, "2024": 2},
        (),
    ),
    "pdip": (
        "Partai Demokrasi Indonesia Perjuangan",
        "PDI-P",
        "Indonesian Democratic Party of Struggle",
        "Q1186306",
        "2560",
        {"2019": 3, "2024": 3},
        (),
    ),
    "golkar": (
        "Partai Golongan Karya",
        "Golkar",
        "Party of Functional Groups",
        "Q1536552",
        "2304",
        {"2019": 4, "2024": 4},
        (),
    ),
    "nasdem": ("Partai NasDem", "NasDem", "NasDem Party", "Q4207219", "4496", {"2019": 5, "2024": 5}, ()),
    "buruh": ("Partai Buruh", "Buruh", "Labour Party", "Q108940356", None, {"2024": 6}, ()),
    "gelora": (
        "Partai Gelombang Rakyat Indonesia",
        "Gelora",
        "Indonesian People's Wave Party",
        "Q97016042",
        None,
        {"2024": 7},
        (),
    ),
    "pks": (
        "Partai Keadilan Sejahtera",
        "PKS",
        "Prosperous Justice Party",
        "Q1511101",
        "2556",
        {"2019": 8, "2024": 8},
        (),
    ),
    "pkn": ("Partai Kebangkitan Nusantara", "PKN", "Nusantara Awakening Party", "Q115754699", None, {"2024": 9}, ()),
    "hanura": (
        "Partai Hati Nurani Rakyat",
        "Hanura",
        "People's Conscience Party",
        "Q4262041",
        "4497",
        {"2019": 13, "2024": 10},
        (),
    ),
    "garuda": (
        "Partai Garda Perubahan Indonesia",
        "Garuda",
        None,
        "Q49707489",
        None,
        {"2019": 6, "2024": 11},
        ("Partai Gerakan Perubahan Indonesia",),
    ),
    "pan": (
        "Partai Amanat Nasional",
        "PAN",
        "National Mandate Party",
        "Q2434365",
        "2557",
        {"2019": 12, "2024": 12},
        (),
    ),
    "pbb": ("Partai Bulan Bintang", "PBB", "Crescent Star Party", "Q2915024", "4210", {"2019": 19, "2024": 13}, ()),
    "demokrat": ("Partai Demokrat", "Demokrat", "Democratic Party", "Q1186227", "2561", {"2019": 14, "2024": 14}, ()),
    "psi": (
        "Partai Solidaritas Indonesia",
        "PSI",
        "Indonesian Solidarity Party",
        "Q23744654",
        None,
        {"2019": 11, "2024": 15},
        (),
    ),
    "perindo": (
        "Partai Persatuan Indonesia",
        "Perindo",
        "Perindo Party",
        "Q19752495",
        None,
        {"2019": 9, "2024": 16},
        (),
    ),
    "ppp": (
        "Partai Persatuan Pembangunan",
        "PPP",
        "United Development Party",
        "Q2669302",
        "2558",
        {"2019": 10, "2024": 17},
        (),
    ),
    "ummat": ("Partai Ummat", "Ummat", "Ummah Party", "Q106255756", None, {"2024": 24}, ()),
    "berkarya": ("Partai Berkarya", "Berkarya", "Berkarya Party", "Q31173603", None, {"2019": 7}, ()),
    "pkpi": (
        "Partai Keadilan dan Persatuan Indonesia",
        "PKPI",
        "Indonesian Justice and Unity Party",
        "Q13405290",
        "4476",
        {"2019": 20},
        (),
    ),
    "pna": ("Partai Nanggroe Aceh", "PNA", None, "Q19730391", None, {"2019": 18, "2024": 18}, ()),
    "gabthat": (
        "Partai Generasi Atjeh Beusaboh Tha'at dan Taqwa",
        "Gabthat",
        None,
        "Q56393536",
        None,
        {"2024": 19},
        (),
    ),
    "pda": ("Partai Darul Aceh", "PDA", None, "Q4920529", None, {"2019": 17, "2024": 20}, ("Partai Daerah Aceh",)),
    "pa": ("Partai Aceh", "PA", "Aceh Party", "Q4262048", None, {"2019": 15, "2024": 21}, ()),
    "pas-aceh": ("Partai Adil Sejahtera Aceh", "PAS Aceh", None, "Q123553439", None, {"2024": 22}, ()),
    "sira": ("Partai Soliditas Independen Rakyat Aceh", "SIRA", None, "Q116311936", None, {"2019": 16, "2024": 23}, ()),
}
ACEH = {"pna", "gabthat", "pda", "pa", "pas-aceh", "sira"}


def fetch() -> None:
    for _, url in DECREES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")
    for _, url, _ in LAWS.values():
        for attempt in range(3):
            try:
                raw.fetch(SOURCE, url, suffix=".pdf")
                break
            except httpx.HTTPError:
                if attempt == 2:
                    raise


def _bare(text: str) -> str:
    return re.sub(r"^(Kabupaten|Kota)( Administrasi)? ", "", text)


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Indonesia's elected bodies and offices (DPR, DPD, MPR, the DPRD of every province, regency, and city, "
                "president, vice president, governors, regents, and mayors) and the parties of the 2019 and 2024 elections"
            ),
            polity="ID",
        )

        def name(entity: UUID, text: str, lang: str = "id", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        sources: dict[str, UUID] = {}
        for key, (title, url) in DECREES.items():
            sources[key] = cs.source(KPU, title)
            cs.add(
                "source",
                id=sources[key],
                kind="official_body",
                publisher=KPU,
                title=title,
                url=url,
                license="Public record: official decree, no license stated",
                license_url="https://jdih.kpu.go.id",
            )
            cs.artifact(raw.cached(SOURCE, url), sources[key])
        for key, (title, url, publisher) in LAWS.items():
            sources[key] = cs.source(publisher, title)
            cs.add(
                "source",
                id=sources[key],
                kind="official_body",
                publisher=publisher,
                title=title,
                url=url,
                license="Public record: statute, not subject to copyright (UU 28/2014 art. 42)",
            )
            cs.artifact(raw.cached(SOURCE, url), sources[key])

        indonesia = cs.polity("ID")
        if indonesia in cs.minted:
            raise ValueError("publish id.big before id.kpu")
        bodies: dict[str, UUID] = {}
        for key, (kind, english, native, short) in BODIES.items():
            bodies[key] = cs.key("id.body", key)
            cs.add("body", id=bodies[key], polity_id=indonesia, kind=kind)
            name(bodies[key], english, "en", "english")
            name(bodies[key], native)
            name(bodies[key], short, "id", "abbreviation")
            for seats, since, until in SIZES[key]:
                cs.add("body_size", body_id=bodies[key], seats=seats, valid_from=since, valid_to=until)
        for key, (kind, english, native) in OFFICES.items():
            office = cs.key("id.office", key)
            cs.add("office", id=office, polity_id=indonesia, kind=kind, body_id=None)
            name(office, english, "en", "english")
            name(office, native)

        for code, (iso, _, native, english) in PROVINCES.items():
            polity = cs.polity(f"ID-{iso}")
            if polity in cs.minted:
                raise ValueError(f"ID-{iso} is not published")
            council = cs.key("id.body", f"dprd:{code}")
            cs.add("body", id=council, polity_id=polity, kind="unicameral")
            if code == "11":
                name(council, "Dewan Perwakilan Rakyat Aceh")
                name(council, "DPRA", "id", "abbreviation")
                name(council, "Aceh House of Representatives", "en", "english")
            elif code in PAPUA:
                name(council, f"Dewan Perwakilan Rakyat {native}")
                name(council, f"{english} House of Representatives", "en", "english")
            else:
                name(council, f"Dewan Perwakilan Rakyat Daerah Provinsi {native}")
                name(council, f"{english} Regional House of Representatives", "en", "english")
            governor = cs.key("id.office", f"gubernur:{code}")
            cs.add("office", id=governor, polity_id=polity, kind="governor", body_id=None)
            name(governor, f"Gubernur {native}")
            name(governor, f"Governor of {english}", "en", "english")

        for code, text, kind in conn.execute(
            """select c.code, n.text, k.kind from area a
               join area_code c on c.area_id = a.id and upper_inf(c.valid)
               join area_kind k on k.area_id = a.id
               join name n on n.entity_id = a.id and n.lang = 'id' and n.kind = 'official'
               where a.scheme_code = 'id.kabkota' order by c.code"""
        ).fetchall():
            if code in ADMINISTRATIVE:
                continue
            polity = cs.key("id.big.polity", code)
            if polity in cs.minted:
                raise ValueError(f"the polity of {code} is not published")
            bare = _bare(text)
            noun = "Kota" if kind == "kota" else "Kabupaten"
            council = cs.key("id.body", f"dprd:{code}")
            cs.add("body", id=council, polity_id=polity, kind="unicameral")
            if code.startswith("11."):
                name(council, f"Dewan Perwakilan Rakyat {noun} {bare}")
                name(council, "DPRK", "id", "abbreviation")
            else:
                name(council, f"Dewan Perwakilan Rakyat Daerah {noun} {bare}")
            head = cs.key("id.office", f"kepala:{code}")
            cs.add("office", id=head, polity_id=polity, kind="mayor", body_id=None)
            if kind == "kota":
                name(head, f"Wali Kota {bare}")
                name(head, f"Mayor of {bare}", "en", "english")
            else:
                name(head, f"Bupati {bare}")
                name(head, f"Regent of {bare}", "en", "english")

        aceh = cs.polity("ID-AC")
        for key, (native, short, english, item, partyfacts, numbers, former) in PARTIES.items():
            party = cs.key("id.party", key)
            cs.add("party", id=party, kind="party", polity_id=aceh if key in ACEH else indonesia)
            name(party, native)
            name(party, short, "id", "abbreviation")
            if english is not None:
                name(party, english, "en", "english")
            for text in former:
                name(party, text, "id", "historical")
            cs.add("external_id", entity_id=party, scheme="wikidata", value=item)
            if partyfacts is not None:
                cs.add("external_id", entity_id=party, scheme="partyfacts", value=partyfacts)
            for year, number in numbers.items():
                source = sources["2024u" if key == "ummat" else year]
                cs.add("citation", entity_id=party, source_id=source, locator={"election": year, "nomor_urut": number})
        return cs.write()
