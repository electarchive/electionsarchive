import csv
import io
import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw
from ea.sources.id.kpu_dpr import _slug
from ea.sources.id.kpu_pilpres import PROVINCES
from ea.store import DATA

SOURCE = "id.kpu_dpr_2014"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
KPU = "Komisi Pemilihan Umum Republik Indonesia"
JDIH = "https://jdih.kpu.go.id/data/data_kepkpu/"
SEATED = (
    "Keputusan KPU Nomor 416/Kpts/KPU/Tahun 2014 tentang Penetapan Perolehan Kursi Partai Politik dan Penetapan Calon "
    "Terpilih Anggota Dewan Perwakilan Rakyat dalam Pemilihan Umum Tahun 2014"
)
DECREES = {
    "416": (f"{JDIH}SK_KPU_416_Penetapan_Kursi_Calon_Terpilih_1452014.pdf", SEATED),
    "564": (f"{JDIH}564.pdf", f"Keputusan KPU Nomor 564/Kpts/KPU/Tahun 2014 tentang Perubahan atas {SEATED}"),
    "565": (f"{JDIH}565_001.pdf", f"Keputusan KPU Nomor 565/Kpts/KPU/Tahun 2014 tentang Perubahan Kedua atas {SEATED}"),
}
PARTIES = {
    1: "nasdem",
    2: "pkb",
    3: "pks",
    4: "pdip",
    5: "golkar",
    6: "gerindra",
    7: "demokrat",
    8: "pan",
    9: "ppp",
    10: "hanura",
    14: "pbb",
    15: "pkpi",
}
REPLACEMENTS = (
    ("NUSA TENGGARA TIMUR I", 8, 1, "H. SYAHRULAN PUA SAWA", "564", 5),
    ("JAWA TENGAH VI", 9, 1, "KH. MUSLICH ZA", "564", 5),
    ("JAWA BARAT VII", 4, 7, "DANIEL LUMBAN TOBING", "565", 6),
    ("KALIMANTAN TIMUR", 4, 1, "AWANG FERDIAN HIDAYAT, MM", "565", 7),
)
SPANNING = {"KALIMANTAN TIMUR": ("64", "65"), "PAPUA": ("91", "93", "94", "95"), "PAPUA BARAT": ("92", "96")}
ALIASES = {"DI YOGYAKARTA": "DAERAH ISTIMEWA YOGYAKARTA", "BANGKA BELITUNG": "KEPULAUAN BANGKA BELITUNG"}
VOTES = re.compile(r"\d{1,3}(?:\.\d{3})*")
THRESHOLD = 3.5
SEATS = 560
DAPILS = 77
DAY = date(2014, 4, 9)
COLUMNS = (("number", 140), ("province", 230), ("dapil", 335), ("party", 365), ("label", 505), ("dct", 535))
COLUMNS += (("name", 740), ("votes", 790), ("rank", 10_000))


def fetch() -> None:
    for url, _ in DECREES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def _path(key: str) -> Path:
    return raw.cached(SOURCE, DECREES[key][0]).path


def _int(text: str) -> int:
    return int(text.replace(".", ""))


def _province(text: str) -> str:
    return ALIASES.get(text, text)


def _seats() -> pl.DataFrame:
    text = subprocess.run(
        ["pdftotext", "-f", "6", "-l", "7", "-layout", str(_path("416")), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    rows: list[dict[str, object]] = []
    totals: list[int] = []
    for line in text.splitlines():
        if match := re.match(r"\s*(\d{1,2})\s+([A-Z][A-Z .]+?)\s{2,}(\d{1,2})\s+((?:\d{1,3}\s+){11}\d{1,3})\s*$", line):
            counts = [int(count) for count in match.group(4).split()]
            for party, seats in zip(PARTIES, counts, strict=True):
                rows.append({"dapil": match.group(2), "magnitude": int(match.group(3)), "party": party, "seats": seats})
        elif match := re.match(r"\s*X\s+JUMLAH PEROLEHAN KURSI\s+([\d\s]+)$", line):
            totals = [int(count) for count in match.group(1).split()]
    seats = pl.DataFrame(rows)
    national = seats.group_by("party").agg(pl.col("seats").sum()).sort("party")
    if national["seats"].to_list() != totals:
        raise ValueError(f"seat columns {national['seats'].to_list()} do not match the printed totals {totals}")
    magnitudes = seats.group_by("dapil").agg(pl.col("magnitude").first(), pl.col("seats").sum())
    if magnitudes.height != DAPILS or magnitudes["magnitude"].sum() != SEATS:
        raise ValueError(f"{magnitudes.height} dapils with {magnitudes['magnitude'].sum()} seats")
    if magnitudes.filter(pl.col("magnitude") != pl.col("seats")).height:
        raise ValueError(f"seats do not fill {magnitudes.filter(pl.col('magnitude') != pl.col('seats')).rows()}")
    return seats


def _elected() -> pl.DataFrame:
    out = subprocess.run(
        ["pdftotext", "-f", "8", "-l", "21", "-tsv", str(_path("416")), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    words = [
        word
        for word in csv.DictReader(io.StringIO(out), delimiter="\t", quoting=csv.QUOTE_NONE)
        if word["level"] == "5" and word["text"].strip()
    ]
    lines: dict[tuple[int, int], list[dict[str, str]]] = {}
    for word in words:
        lines.setdefault((int(word["page_num"]), round(float(word["top"]))), []).append(word)
    rows: list[dict[str, object]] = []
    for (page, _), line in sorted(lines.items()):
        cells: dict[str, list[dict[str, str]]] = {}
        for word in sorted(line, key=lambda word: float(word["left"])):
            column = next(name for name, edge in COLUMNS if float(word["left"]) < edge)
            cells.setdefault(column, []).append(word)
        number = cells.get("number", [{"text": ""}])[0]["text"]
        if not number.isdigit() or len(cells) != len(COLUMNS):
            continue
        texts = {column: " ".join(word["text"] for word in found) for column, found in cells.items()}
        if not VOTES.fullmatch(texts["votes"]):
            raise ValueError(f"416 page {page}: unread line {texts}")
        box = cells["votes"][0]
        left, top, width, height = (float(box[key]) for key in ("left", "top", "width", "height"))
        rows.append(
            {
                "row": int(number),
                "province": _province(texts["province"]),
                "dapil": texts["dapil"],
                "party": int(texts["party"]),
                "candidate": int(texts["dct"]),
                "name": texts["name"],
                "votes": _int(texts["votes"]),
                "rank": int(texts["rank"]),
                "page": page,
                "box": [left, top, left + width, top + height],
            }
        )
    elected = pl.DataFrame(rows)
    if elected["row"].to_list() != list(range(1, SEATS + 1)):
        raise ValueError(f"416 lists {elected.height} elected members, not rows 1 to {SEATS}")
    return elected.drop("row")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    seats = _seats()
    elected = _elected()
    unknown = set(elected["province"]) - set(PROVINCES)
    if unknown:
        raise ValueError(f"unknown provinces {sorted(unknown)}")
    tally = elected.group_by("dapil", "party").len("count")
    check = (
        seats.filter(pl.col("seats") > 0)
        .join(tally, on=["dapil", "party"], how="full", coalesce=True)
        .filter(pl.col("seats").ne_missing(pl.col("count")))
    )
    if check.height:
        raise ValueError(f"elected members do not match the seat table: {check.rows()[:8]}")
    for dapil, party, number, _, key, _ in REPLACEMENTS:
        if elected.filter((pl.col("dapil") == dapil) & (pl.col("party") == party)).height < 1:
            raise ValueError(f"{key}: no {party} member in {dapil}")
        if (
            number
            and not elected.filter(
                (pl.col("dapil") == dapil) & (pl.col("party") == party) & (pl.col("candidate") == number)
            ).height
        ):
            raise ValueError(f"{key}: {party}/{number} is not elected in {dapil}")
    seats.write_parquet(PARSED / "seats.parquet")
    elected.write_parquet(PARSED / "elected.parquet")
    national = seats.group_by("party").agg(pl.col("seats").sum()).sort("party")
    print(f"{seats['dapil'].n_unique()} dapils, {elected.height} elected; seats {dict(national.rows())}")


DECREE_LICENSE = "Public record: official decree, no license stated"
SYSTEM = (
    "The 560 seats of the House of Representatives (Dewan Perwakilan Rakyat) were filled in 77 electoral districts "
    "(daerah pemilihan) of 3 to 10 seats under UU 8/2012. Only parties with at least 3.5 percent of the valid votes "
    "nationwide took part in the allocation (art. 208). In each district the valid votes of those parties were divided "
    "by the seats to give the quota (bilangan pembagi pemilihan, BPP); each party won one seat per whole quota, and the "
    "seats left went to the largest remainders (art. 212). Each party's seats went to its candidates with the most "
    "votes (art. 215). A voter marked a party or one of its candidates; both counted toward the party."
)
METHODOLOGY = (
    "Declared seats by party and the elected members with their own votes, from KPU decree 416/2014 (Model E-5 DPR and "
    "the recapitulation of Model E-3 DPR). The votes of every party and candidate by district were set in lampiran I "
    "of Model E-1 DPR to KPU decree 411/2014, which the KPU legal database does not carry (its copy holds only the DPD "
    "lampiran), so this set holds the elected members' votes alone and records completeness 0. The ten parties over "
    "the threshold won 560 seats; PBB and PKPI won none."
)
REPLACED = (
    "KPU replaced members it first declared elected in decree 416/2014 who died, resigned, or no longer met the "
    "requirements before taking office on 1 October 2014; the seat went to the party's candidate with the next most "
    "votes. The elected candidates here follow decrees 564/2014 (Nusa Tenggara Timur I and Jawa Tengah VI) and "
    "565/2014 (Jawa Barat VII and Kalimantan Timur), whose lampiran give neither the replacement's ballot number nor "
    "votes."
)


def _title(dapil: str) -> str:
    title = dapil.title().replace("Di ", "DI ").replace("Dki ", "DKI ")
    return re.sub(r"\b[IVX]+\b", lambda match: match.group(0).upper(), title, flags=re.IGNORECASE)


def emit() -> Path:
    from ea.changeset import Changeset
    from ea.store import connect

    seats = pl.read_parquet(PARSED / "seats.parquet")
    elected = pl.read_parquet(PARSED / "elected.parquet")
    day = DAY.isoformat()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Indonesia's 2014 House of Representatives (DPR) election: declared seats in all 77 districts and the "
                "560 elected members with their votes, as amended"
            ),
            polity="ID",
        )

        def name(entity: UUID, text: str, lang: str = "id", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        indonesia = cs.polity("ID")
        nation = cs.area("id.nation", "ID")
        body = cs.key("id.body", "dpr")
        if body in cs.minted:
            raise ValueError("publish id.kpu before id.kpu_dpr_2014")
        cs.add("body_size", body_id=body, seats=SEATS, valid_from=date(2014, 10, 1), valid_to=date(2019, 10, 1))

        sources: dict[str, UUID] = {}
        artifacts: dict[str, UUID] = {}
        for key, (url, title) in DECREES.items():
            found = sources[key] = cs.source(KPU, title)
            cs.add(
                "source",
                id=found,
                kind="official_body",
                publisher=KPU,
                title=title,
                url=url,
                license=DECREE_LICENSE,
                license_url="https://jdih.kpu.go.id",
            )
            artifacts[key] = cs.artifact(raw.cached(SOURCE, url), found)

        pages: dict[tuple[str, int], tuple[UUID, int, int]] = {}

        def page(key: str, number: int) -> tuple[UUID, int, int]:
            if (key, number) not in pages:
                cached = raw.cached(SOURCE, DECREES[key][0])
                image = DATA / "pages" / SOURCE / f"{cached.sha256}-{number}.png"
                if not image.exists():
                    image.parent.mkdir(parents=True, exist_ok=True)
                    subprocess.run(
                        ["pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                        + [str(cached.path), str(image.with_suffix(""))],
                        check=True,
                    )
                width, height = Image.open(image).size
                found = cs.page(artifacts[key], number)
                cs.add(
                    "source_page",
                    id=found,
                    artifact_id=artifacts[key],
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
                pages[key, number] = (found, width, height)
            return pages[key, number]

        election = cs.key("id.election", day)
        cs.add(
            "election",
            id=election,
            polity_id=indonesia,
            kind="general",
            date_from=DAY,
            date_to=DAY,
            date_precision="day",
            status="held",
        )
        name(election, "2014 Indonesian legislative election", "en", "english")
        name(election, "Pemilihan Umum Anggota DPR, DPD, dan DPRD Tahun 2014")
        path = f"id/{day}-legislative"
        cs.add("slug", path=path, entity_id=election)

        system = cs.key("id.electoral_system", "dpr:2014")
        cs.add(
            "electoral_system",
            id=system,
            polity_id=indonesia,
            rules={
                "formula": "hare",
                "threshold": {"percent": THRESHOLD, "level": "party", "scope": "national"},
                "lists": {"order": "open", "vacancy": "next_by_votes"},
            },
            rules_version=1,
        )
        name(system, "House of Representatives open-list proportional representation, 2014", "en", "english")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)

        parties = {number: cs.key("id.party", key) for number, key in PARTIES.items()}
        if any(party in cs.minted for party in parties.values()):
            raise ValueError("publish id.kpu before id.kpu_dpr_2014")
        for number in sorted(set(seats.filter(pl.col("seats") > 0)["party"])):
            cs.add(
                "citation",
                entity_id=parties[number],
                source_id=sources["416"],
                locator={"election": "2014", "nomor_urut": number},
            )

        cs.add(
            "scheme",
            code="id.dapil_dpr",
            polity_id=indonesia,
            kind="electoral_district",
            rank=2,
            noun="electoral district",
            kinds=None,
            code_system=None,
        )
        districts = elected.select("dapil", "province").unique()
        whole = {
            province
            for province, count in districts.group_by("province").len().rows()
            if count == 1 and province not in SPANNING
        }
        replaced = {(dapil, party, number) for dapil, party, number, *_ in REPLACEMENTS}
        for dapil, province in sorted(districts.rows()):
            key = f"{day}:dpr:{_slug(dapil)}"
            title = _title(dapil)
            area = cs.area("id.provinsi", PROVINCES[province])
            code = f"2014:{_slug(dapil)}"
            unit = cs.unit("id.dapil_dpr", code)
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=indonesia,
                scheme_code="id.dapil_dpr",
                code=code,
                area_relation="equals" if province in whole else "within",
                area_id=nation if province in SPANNING else area,
                kind="standard",
                valid_from=None,
            )
            name(unit, f"Daerah Pemilihan {title}")
            name(unit, f"{title} electoral district", "en", "english")
            contest = cs.key("id.contest", key)
            won = dict(seats.filter((pl.col("dapil") == dapil) & (pl.col("seats") > 0)).select("party", "seats").rows())
            magnitude = int(seats.filter(pl.col("dapil") == dapil)["magnitude"][0])
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="list_allocation",
                body_id=body,
                district_area_id=area if province in whole else None,
                term="full",
                magnitude=magnitude,
                round_number=1,
                previous_round_id=None,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            name(contest, f"Dewan Perwakilan Rakyat, {title}")
            name(contest, f"House of Representatives, {title}", "en", "english")
            cs.add("slug", path=f"{path}/dpr/{_slug(dapil)}", entity_id=contest)
            swaps = [row for row in REPLACEMENTS if row[0] == dapil]
            if swaps:
                cs.add("note", entity_id=contest, kind="caveat", body_md=REPLACED)
            result_set = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources["416"],
                is_default=True,
                completeness=0,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHODOLOGY)
            members = elected.filter(pl.col("dapil") == dapil)

            def candidate(
                party: int, number: int | None, ballot: str, option_key: str, listed: UUID, outcome: str
            ) -> tuple[UUID, UUID]:
                person = cs.key(f"{SOURCE}.person", f"2014:{key}:{party}:{option_key}")
                if person in cs.minted:
                    cs.add("person", id=person, gender=None)
                    name(person, ballot)
                option = cs.key("id.option", f"{key}:{party}:{option_key}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    parent_option_id=listed,
                    party_id=parties[party],
                    ballot_position=number,
                    ballot_label=ballot,
                )
                candidacy = cs.key("id.candidacy", f"{key}:{party}:{option_key}")
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="list_member",
                    ballot_name=ballot,
                    is_independent=False,
                    member_of_party_id=parties[party],
                )
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=outcome, seats=None)
                return option, candidacy

            for party in sorted(won):
                listed = cs.key("id.option", f"{key}:{party}")
                cs.add(
                    "contest_option",
                    id=listed,
                    contest_id=contest,
                    kind="list",
                    party_id=parties[party],
                    ballot_position=party,
                    ballot_label=None,
                )
                cs.add("outcome", result_set_id=result_set, option_id=listed, kind="won", seats=won[party])
                for number, ballot, votes, number_, box in (
                    members.filter(pl.col("party") == party)
                    .sort("candidate")
                    .select("candidate", "name", "votes", "page", "box")
                    .rows()
                ):
                    option, _ = candidate(
                        party,
                        number,
                        ballot,
                        str(number),
                        listed,
                        "lost" if (dapil, party, number) in replaced else "elected",
                    )
                    fact = {
                        "result_set_id": result_set,
                        "unit_id": unit,
                        "option_id": option,
                        "channel": 0,
                        "measure": 1,
                        "count_round": 0,
                    }
                    cs.add("result_value", **fact, flags=0, value=votes)
                    found, width, height = page("416", number_)
                    x0, y0, x1, y1 = (side * 100 / 72 for side in box)
                    cs.add(
                        "value_locator",
                        **fact,
                        page_id=found,
                        source_id=None,
                        x0=x0 / width,
                        y0=y0 / height,
                        x1=x1 / width,
                        y1=y1 / height,
                    )
                for ballot, decree, number_ in (row[3:] for row in swaps if row[1] == party):
                    _, candidacy = candidate(party, None, ballot, f"r{_slug(ballot)}", listed, "elected")
                    cs.add(
                        "citation",
                        entity_id=candidacy,
                        source_id=sources[decree],
                        locator={"page": number_},
                    )
                    page(decree, number_)
        return cs.write()
