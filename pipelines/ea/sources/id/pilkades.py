import csv
import io
import itertools
from datetime import date
from pathlib import Path
from uuid import uuid5

import numpy as np
from PIL import Image

from ea import package, raw
from ea.changeset import Changeset, slug
from ea.package import Package
from ea.store import DATA, connect

SOURCE = "id.pilkades"
VERSION = "1"
PAGES = DATA / "pages" / SOURCE
TRANSCRIPT = Path(__file__).with_name("pilkades_bangka_tengah_2022.tsv")
SITE = "https://drupal.dinsospmd.babelprov.go.id"
ARTICLE = f"{SITE}/pleno-penetapan-29-kepala-desa-terpilih-berdasarkan-perhitungan-hasil-perolehan-suara"
IMAGES = ["1_9", "2_9", "3_8", "4_2", "5_3", "6_0", "7", "8_1", "9_0", "10_0", "11"]
PUBLISHER = "Dinas Sosial dan Pemberdayaan Masyarakat Desa Provinsi Kepulauan Bangka Belitung"
TITLE = "Pleno Penetapan 29 Kepala Desa Terpilih Berdasarkan Perhitungan Hasil Perolehan Suara"
REGENCY = "19.04"
DAY = date(2022, 6, 22)
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
MEASURES = {"registered": REGISTERED, "ballots": BALLOTS, "valid": VALID, "invalid": INVALID}
DECLARED = {
    "19.04.03.2003": 1180,
    "19.04.03.2005": 577,
    "19.04.03.2010": 435,
    "19.04.03.2008": 825,
    "19.04.03.2006": 945,
    "19.04.03.2002": 280,
    "19.04.03.2011": 439,
    "19.04.03.2007": 821,
    "19.04.05.2008": 369,
    "19.04.05.2006": 531,
    "19.04.05.2001": 766,
    "19.04.05.2005": 1260,
    "19.04.02.2018": 870,
    "19.04.02.2010": 574,
    "19.04.02.2013": 505,
    "19.04.02.2004": 662,
    "19.04.02.2012": 480,
    "19.04.01.2008": 364,
    "19.04.01.2003": 1446,
    "19.04.04.2008": 548,
    "19.04.04.2006": 551,
    "19.04.04.2001": 828,
    "19.04.04.2009": 394,
    "19.04.04.2010": 458,
    "19.04.06.2005": 598,
    "19.04.06.2001": 1691,
    "19.04.06.2007": 472,
    "19.04.06.2003": 867,
    "19.04.06.2008": 612,
}
METHOD = (
    "Transcribed from the provincial social and village empowerment office's recapitulation tables, which give each "
    "candidate's votes, valid and invalid ballots, ballots cast, and registered voters (DPT plus DPTb) by polling "
    "station (TPS) and for the village. Every candidate's polling station votes sum to the printed total, the "
    "candidates sum to the valid votes in each polling station, and valid plus invalid equals ballots cast. The "
    "winner and the winning vote are as the office's article lists them from the plenary of 22 June 2022."
)
NOTES = {
    "19.04.05.2008": "The table gives Bukit Kijang's 15 invalid ballots for the village only, not by polling station.",
    "19.04.05.2001": "The image of Namang's table is cut off below the invalid ballots, so ballots cast and registered "
    "voters are missing.",
}


def _image(name: str) -> str:
    return f"{SITE}/sites/default/files/images/berita/{name}.jpg"


def fetch() -> None:
    raw.fetch(SOURCE, ARTICLE, suffix=".html")
    for name in IMAGES:
        raw.fetch(SOURCE, _image(name), suffix=".jpg")


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def _rows() -> list[dict[str, str]]:
    return list(csv.DictReader(TRANSCRIPT.open(encoding="utf-8"), delimiter="\t"))


def _segments(content: bytes) -> tuple[int, int, list[tuple[float, float, float, float]]]:
    pixels = np.asarray(Image.open(io.BytesIO(content)).convert("RGB")).astype(int)
    height, width = pixels.shape[:2]
    red, green, blue = pixels[..., 0], pixels[..., 1], pixels[..., 2]
    yellow = ((red > 200) & (green > 200) & (blue < 120)).mean(axis=1) > 0.3
    dark = pixels.mean(axis=2) < 175
    edges = [0, *(y for y in range(1, height) if yellow[y] != yellow[y - 1]), height]
    boxes = []
    for top, bottom in itertools.pairwise(edges):
        if bottom - top < 40 or yellow[top]:
            continue
        columns = np.flatnonzero(dark[top:bottom].mean(axis=0) > 0.3)
        boxes.append((float(columns[0] / width), top / height, float((columns[-1] + 1) / width), bottom / height))
    return width, height, boxes


def villages() -> dict[str, dict[str, str]]:
    codes = sorted({row["code"] for row in _rows()})
    with connect() as conn:
        found = conn.execute(
            """select ac.code, a.id::text, n.text, e.entity_id::text from area a
               join area_code ac on ac.area_id = a.id
               join name n on n.entity_id = a.id and n.is_preferred
               left join external_id e on e.scheme = 'id.big.polity' and e.value = ac.code
               where a.scheme_code = 'id.desa' and ac.code = any(%s)""",
            (codes,),
        ).fetchall()
    return {code: {"area": area, "name": name, "polity": polity} for code, area, name, polity in found}


def _regency() -> tuple[str, str, str]:
    with connect() as conn:
        row = conn.execute(
            """select p.id::text, e.entity_id::text, s.path from polity p, external_id e
               join slug s on s.entity_id = e.entity_id and s.is_current
               where p.iso_code = 'ID' and e.scheme = 'id.big.polity' and e.value = %s""",
            (REGENCY,),
        ).fetchone()
    if row is None:
        raise ValueError(f"no regency polity {REGENCY}")
    return row[0], row[1], row[2]


def emit() -> Path:
    found = villages()
    _, regency, path = _regency()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"{len(found)} villages of Bangka Tengah as submunicipal polities for their village head elections",
            polity="ID",
        )
        for code, row in sorted(found.items()):
            polity = cs.key("id.big.polity", code)
            if polity in cs.minted:
                cs.add("polity", id=polity, kind="submunicipal", iso_code=None)
            cs.add("polity_parent", polity_id=polity, parent_id=regency)
            cs.add(
                "polity_territory", polity_id=polity, area_id=cs.area("id.desa", code), valid_from=None, valid_to=None
            )
            cs.add("name", entity_id=polity, text=row["name"], lang="id", kind="official", is_preferred=True)
            cs.add("slug", path=f"{path}/{slug(row['name'])}", entity_id=polity)
        return cs.write()


def packages() -> list[str]:
    return [f"{SOURCE}/{DAY}"]


def build() -> Package:
    held = villages()
    rows = _rows()
    if missing := sorted({row["code"] for row in rows if held.get(row["code"], {}).get("polity") is None}):
        raise ValueError(f"no village polity for {', '.join(missing)}; run ea emit {SOURCE} first")
    indonesia, regency, path = _regency()
    with connect() as conn:
        polity_slugs = dict(
            conn.execute(
                "select entity_id::text, path from slug where is_current and entity_id = any(%s::uuid[])",
                ([row["polity"] for row in held.values()],),
            ).fetchall()
        )
    p = Package(
        f"{SOURCE}/{DAY}",
        SOURCE,
        "Bangka Tengah's 2022 village head elections in 29 villages by polling station, from the Bangka Belitung "
        "social and village empowerment office",
    )
    split = {(row["code"], row["row"]) for row in rows if row["tps"] != "0"}
    p.external |= {
        "id.desa",
        indonesia,
        regency,
        *(row["area"] for row in held.values()),
        *(r["polity"] for r in held.values()),
    }
    source = key("source", "dinsospmd-babel")
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=ARTICLE,
        license="Official public record, no license stated",
        attribution=PUBLISHER,
    )
    p.artifact(raw.cached(SOURCE, ARTICLE), source)
    PAGES.mkdir(parents=True, exist_ok=True)
    pages: dict[str, tuple[str, list[tuple[float, float, float, float]]]] = {}
    for name in IMAGES:
        artifact = raw.cached(SOURCE, _image(name))
        content = artifact.path.read_bytes()
        artifact_id = p.artifact(artifact, source)
        width, height, boxes = _segments(content)
        image = PAGES / f"{artifact.sha256}.png"
        if not image.exists():
            Image.open(io.BytesIO(content)).save(image)
        page = key("page", artifact_id, 1)
        p.add(
            "source_page",
            id=page,
            artifact_id=artifact_id,
            page_number=1,
            image_key=f"pages/{SOURCE}/{image.name}",
            width=width,
            height=height,
        )
        pages[f"{name}.jpg"] = (page, boxes)
    p.add("scheme", code="id.tps_desa", polity_id=indonesia, kind="precinct", rank=5, noun="polling station")
    system = key("system", "plurality")
    p.add(
        "electoral_system",
        id=system,
        polity_id=indonesia,
        valid_from=None,
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.name(system, "Village head plurality", "en", "official")
    election = key("election", DAY)
    p.add("election", id=election, polity_id=regency, kind="local", date_from=DAY, date_to=DAY, status="held")
    p.name(election, "2022 Bangka Tengah village head elections", "en", "official")
    p.name(election, "Pemilihan Kepala Desa Serentak Kabupaten Bangka Tengah Tahun 2022", "id", "native", False)
    election_path = f"{path}/{DAY}-village-heads"
    p.add("slug", path=election_path, entity_id=election, is_current=True)
    p.add("citation", id=key("citation", election), entity_id=election, source_id=source, locator=None)
    for code in sorted({row["code"] for row in rows}):
        place = held[code]
        mine = [row for row in rows if row["code"] == code]
        label = place["name"]
        office = key("office", code)
        p.add("office", id=office, polity_id=place["polity"], body_id=None, kind="mayor")
        p.name(office, f"Village Head of {label}", "en", "official")
        p.name(office, f"Kepala Desa {label}", "id", "native", False)
        contest = key("contest", DAY, code)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="office",
            office_id=office,
            district_area_id=place["area"],
            magnitude=1,
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(contest, f"{label} village head", "en", "official")
        p.name(contest, f"Pemilihan Kepala Desa {label}", "id", "native", False)
        p.add(
            "slug",
            path=f"{election_path}/{polity_slugs[place['polity']].rsplit('/', 1)[1]}",
            entity_id=contest,
            is_current=True,
        )
        result_set = key("result_set", DAY, code)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        body = METHOD + (f" {NOTES[code]}" if code in NOTES else "")
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=body, lang="en")
        village = key("unit", code)
        p.add(
            "reporting_unit",
            id=village,
            polity_id=indonesia,
            scheme_code="id.desa",
            code=code,
            parent_id=None,
            area_relation="equals",
            area_id=place["area"],
            kind="standard",
        )
        units = {"0": village}
        for tps in sorted({int(row["tps"]) for row in mine if row["tps"] != "0"}):
            unit = units[str(tps)] = key("unit", DAY, code, tps)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=indonesia,
                scheme_code="id.tps_desa",
                code=f"{DAY}:{code}:{tps}",
                parent_id=village,
                area_relation="within",
                area_id=place["area"],
                kind="standard",
            )
            p.name(unit, f"TPS {tps}", "id", "official")
        options: dict[str, str] = {}
        totals: dict[str, int] = {}
        for row in mine:
            number = row["row"]
            if number.isdigit() and number not in options:
                option = options[number] = key("option", DAY, code, number)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=None,
                    ballot_position=int(number),
                    ballot_label=row["name"],
                )
                person = key("person", DAY, code, number)
                p.add("person", id=person)
                p.name(person, row["name"], "id", "official")
                p.add(
                    "candidacy",
                    id=key("candidacy", DAY, code, number),
                    person_id=person,
                    option_id=option,
                    role="head",
                    list_position=None,
                    ballot_name=row["name"],
                    member_of_party_id=None,
                    is_independent=False,
                )
            if number.isdigit() and row["tps"] == "0":
                totals[number] = int(row["value"])
            if row["tps"] == "0" and (code, number) in split:
                continue
            option = options.get(number)
            measure = VOTES if number.isdigit() else MEASURES[number]
            unit = units[row["tps"]]
            page, boxes = pages[row["image"]]
            x0, y0, x1, y1 = boxes[int(row["segment"])]
            fact = {
                "result_set_id": result_set,
                "unit_id": unit,
                "option_id": option,
                "channel": 0,
                "measure": measure,
                "count_round": 0,
            }
            p.add("result_value", **fact, flags=0, value=int(row["value"]))
            p.add("value_locator", **fact, page_id=page, source_id=source, x0=x0, y0=y0, x1=x1, y1=y1)
        winner = max(totals, key=lambda number: totals[number])
        if totals[winner] != DECLARED[code] or sorted(totals.values())[-2:-1] == [totals[winner]]:
            raise ValueError(f"{code}: winner's total {totals[winner]} disagrees with the declared {DECLARED[code]}")
        p.add("outcome", result_set_id=result_set, option_id=options[winner], kind="elected")
        p.add("declared", result_set_id=result_set, option_id=options[winner], measure=VOTES, value=DECLARED[code])
    return p


def parse() -> None:
    build().write()
