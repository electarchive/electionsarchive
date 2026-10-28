import json
import re
import subprocess
from datetime import date
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.tr.ysk import (
    INDEPENDENTS,
    INVALID,
    LICENSE,
    PROVINCES,
    PUBLISHER,
    REGISTERED,
    VALID,
    VOTERS,
    VOTES,
    upper,
)
from ea.sources.tr.ysk_local import party as local_party
from ea.store import connect

SOURCE = "tr.ysk_mayors"
API = "https://tr.wikipedia.org/w/api.php"
YSK = "https://www.ysk.gov.tr/doc/dosyalar/docs/"
RESULTS_2004 = f"{YSK}Mahalli/2004/BelediyeBaskanligi/Pdf/2004Mahalli-BelediyeBsk-Tumu.pdf"
CANDIDATES_2009 = (
    f"{YSK}2009MahalliIdareler/2009ilceBelediyeBsk-AdayListesi.pdf",
    f"{YSK}2009MahalliIdareler/2009BeldeBelediyeBsk-AdayListesi.pdf",
)


def titles(year: int) -> list[str]:
    url = (
        f"{API}?action=query&list=search&srsearch={quote(f'intitle:"{year} Türkiye yerel seçimleri"')}"
        "&srlimit=200&srnamespace=0&format=json"
    )
    found = json.loads(raw.cached(SOURCE, url).path.read_bytes())["query"]["search"]
    return sorted(row["title"] for row in found)


def page(title: str) -> str:
    return f"{API}?action=parse&format=json&formatversion=2&prop=wikitext&page={quote(title)}"


def fetch() -> None:
    for year in (2004, 2009):
        search = (
            f"{API}?action=query&list=search&srsearch={quote(f'intitle:"{year} Türkiye yerel seçimleri"')}"
            "&srlimit=200&srnamespace=0&format=json"
        )
        raw.fetch(SOURCE, search, suffix=".json")
        for title in titles(year):
            raw.fetch(SOURCE, page(title), suffix=".json")
    raw.fetch(SOURCE, RESULTS_2004, suffix=".pdf")
    for url in CANDIDATES_2009:
        raw.fetch(SOURCE, url, suffix=".pdf")


import polars as pl

from ea.store import DATA

PARSED = DATA / "parsed" / SOURCE
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>')
PARTIES_2004 = [
    "EMEP", "DSP", "ANAP", "BTP", "AK PARTİ", "BBP", "İP", "ÖDP", "LDP", "TKP", "DYP", "ATP",
    "MİLLET PARTİSİ", "CHP", "GENÇ PARTİ", "YTP", "SHP", "SAADET PARTİSİ", "DP", "MHP", "BAĞIMSIZLAR",
]  # fmt: skip
INTEGER = re.compile(r"^\d{1,3}(?:\.\d{3})*$")


def parse() -> None:
    pdf = raw.cached(SOURCE, RESULTS_2004).path
    html = subprocess.run(["pdftotext", "-bbox", str(pdf), "-"], capture_output=True, text=True, check=True).stdout
    rows: list[dict[str, object]] = []
    for page in html.split("<page ")[1:]:
        words = [(float(a), float(b), float(c), float(d), t) for a, b, c, d, t in WORD.findall(page)]
        heading = [w for w in words if 38 < w[1] < 46 and w[0] < 400]
        province_words = [w[4] for w in sorted(heading, key=lambda w: w[0])]
        if "Çevresi:" not in province_words:
            raise ValueError("page without a seçim çevresi")
        province_heading = " ".join(province_words[province_words.index("Çevresi:") + 1 :])
        province = province_heading
        page_rows: list[dict[str, object]] = []
        vocabulary = {piece for party in PARTIES_2004 for piece in party.split()}
        top = next(w[1] for w in words if w[4] == "Sandık")
        header = sorted((w for w in words if top - 5 < w[1] < top + 22 and w[4] in vocabulary), key=lambda w: w[2])
        edges: dict[str, float] = {}
        cursor = 0
        while cursor < len(header):
            for size in (2, 1):
                pieces = header[cursor : cursor + size]
                label = " ".join(w[4] for w in pieces)
                if label in PARTIES_2004:
                    edges[label] = pieces[-1][2]
                    cursor += len(pieces)
                    break
            else:
                raise ValueError(f"{province}: header word {header[cursor][4]} is no party")
        header_bottom = max(
            w[3] for w in words if w[4] in ("sayısı", "oranı", "(%)", "ve") and top - 5 < w[1] < top + 22
        )
        lines: dict[int, list[tuple[float, float, str]]] = {}
        for x0, y0, x1, _, text in words:
            if y0 > header_bottom:
                lines.setdefault(round(y0 / 3), []).append((x0, x1, text))
        labels = [
            (key, line)
            for key, line in sorted(lines.items())
            if all(not (INTEGER.match(t) or re.match(r"^[\d.]+,\d$", t)) for _, _, t in line)
        ]
        for key, line in sorted(lines.items()):
            ordered = sorted(line)
            split = max((i + 1 for i, (_, _, t) in enumerate(ordered) if not INTEGER.match(t)), default=0)
            numbers = [(c, t) for _, c, t in ordered[split:]]
            near = sorted(numbers, key=lambda n: n[0])
            columns = [round(c) for c, _ in near]
            cutoff = min(edges.values()) - 35
            if len([c for c in columns if c < cutoff]) != 4:
                continue
            own = [(x0, t) for x0, _, t in ordered[:split]]
            name = (
                (" ".join(t for _, t in own), own[0][0])
                if own
                else next(
                    (
                        (" ".join(t for _, _, t in label), label[0][0])
                        for k, label in labels
                        if abs(k - key) <= 2 and label
                    ),
                    None,
                )
            )
            if name is None:
                raise ValueError(f"{province}: row without a name at {key}")
            record: dict[str, object] = {"province": province, "name": name[0], "level": None, "indent": name[1]}
            for (c, t), field in zip(
                [n for n in near if n[0] < cutoff], ("boxes", "registered", "voters", "valid"), strict=True
            ):
                record[field] = int(t.replace(".", ""))
            for c, t in near:
                if c >= cutoff:
                    if not edges:
                        raise ValueError(
                            f"{province}: no party header; band {[(round(w[1]), w[4]) for w in words if 50 < w[1] < 85][:12]}"
                        )
                    party = min(edges, key=lambda p: abs(edges[p] - c))
                    if abs(edges[party] - c) > 14:
                        raise ValueError(f"{province} {name[0]}: {t} at {c} is under no party")
                    record[party] = int(t.replace(".", ""))
            if name[0] != "Türkiye":
                page_rows.append(record)
        starts = sorted({round(float(r["indent"])) for r in page_rows})
        clusters: list[list[int]] = []
        for start in starts:
            if clusters and start - clusters[-1][-1] <= 12:
                clusters[-1].append(start)
            else:
                clusters.append([start])
        has_province = bool(page_rows) and str(page_rows[0]["name"]) == province_heading
        names = (
            ["province", "district", "town"]
            if has_province
            else ["district", "town"]
            if len(clusters) > 1
            else ["town"]
        )
        if len(clusters) > len(names):
            raise ValueError(
                f"{province_heading}: {len(clusters)} indent levels {[(r['name'], round(float(r['indent']))) for r in page_rows][:6]} {clusters}"
            )
        for record in page_rows:
            rank = next(i for i, cluster in enumerate(clusters) if round(float(record["indent"])) in cluster)
            record["level"] = names[rank]
            if record["level"] == "province":
                province = str(record["name"])
            record["province"] = province
            del record["indent"]
            rows.append(record)
    frame = pl.DataFrame(rows, infer_schema_length=None).fill_null(0)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "mayors_2004.parquet")


VERSION = "2"
DAY_2004 = date(2004, 3, 28)
PAGE_2004 = "https://www.ysk.gov.tr/doc/sayfa/28-mart-2004-mahalli-idareler-genel-secimleri/2818.html"
METHOD_2004 = (
    "YSK publishes the 2004 mayoral results by municipality as votes by party, without the winners' names. The "
    "party named as winner here is the one with the most votes in YSK's table."
)
TIE_2004 = "YSK's table gives no single party the most votes here, or the most votes go to independents together."
SPELLINGS = {"AFYON": "AFYONKARAHİSAR", "KAZAN": "KAHRAMANKAZAN", "EYÜP": "EYÜPSULTAN"}
AREAS = {
    ("07", "Kale"): "Demre",
    ("22", "Süleoğlu"): "Süloğlu",
    ("42", "Hadım"): "Hadim",
    ("26", "Mihalıçcık"): "Mihalıççık",
    ("44", "Arapkir"): "Arapgir",
    ("44", "Pötürge"): "Pütürge",
    ("55", "19 Mayıs"): "19-May",
    ("56", "Aydınlar"): "Tillo",
}


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "mayors_2004.parquet")
    labels = [column for column in frame.columns if column in PARTIES_2004]
    owners = {label: "aydinlik-turkiye-partisi" if label == "ATP" else local_party(label, 2004) for label in labels}
    plates = {upper(name): f"{plate:02}" for plate, name in enumerate(PROVINCES, 1)}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Turkey's 2004 mayoral results by municipality, party votes without candidates' names",
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
                   join area_parent ap on ap.area_id = a.id and ap.valid @> %s::date
                   join area p on p.id = ap.parent_id and p.scheme_code = 'tr.il'
                   join area_code pc on pc.area_id = p.id
                   join name n on n.entity_id = a.id
                   where a.scheme_code = 'tr.ilce'""",
                [DAY_2004],
            ).fetchall()
        }
        existing = {
            value for (value,) in conn.execute("select value from external_id where scheme = 'tr.polity'").fetchall()
        }
        territorial = {polity for (polity,) in conn.execute("select polity_id from polity_territory").fetchall()}
        mayoral = cs.key("tr.electoral_system", "mayor")
        parent = cs.key("tr.election", f"{DAY_2004}:local")
        if cs.minted:
            raise ValueError("publish tr.ysk_local before tr.ysk_mayors")
        party_ids = {label: cs.key("tr.party", owner) for label, owner in owners.items() if owner is not None}
        party_names = dict(
            conn.execute(
                """select e.value, (select text from name n where n.entity_id = e.entity_id and n.kind = 'official'
                                    order by n.valid_from desc nulls last limit 1)
                   from external_id e where e.scheme = 'tr.party'"""
            ).fetchall()
        )
        source = cs.source(PUBLISHER, "2004 local election results")
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title="2004 local election results",
                url=PAGE_2004,
                license=LICENSE,
            )
        cs.artifact(raw.cached(SOURCE, RESULTS_2004), source)
        seen: set[str] = set()
        moved: dict[str, str] = {}
        used: set[UUID] = set()
        district = legacy = ""
        for row in frame.iter_rows(named=True):
            level = row["level"]
            text = SPELLINGS.get(upper(row["province"]), upper(row["province"]))
            plate = plates[text]
            province = PROVINCES[int(plate) - 1]
            province_slug = slug(province)
            if level == "province":
                district = ""
                continue
            label_text = re.sub(r"\s*\(\d\)$", "", str(row["name"])).strip()
            if level == "district":
                district = legacy = label_text
            central = district.startswith("Merkez")
            if level == "district":
                parts = [province_slug] if central else [province_slug, slug(district)]
                english = province if central else district
            elif district:
                parts = [province_slug, "merkez" if central else slug(district), slug(label_text)]
                english = label_text
            elif label_text == "Merkez":
                parts = [province_slug, "merkez"]
                english = f"{province} Merkez"
            else:
                direct = f"{province_slug}-{slug(label_text)}"
                parts = (
                    [province_slug, slug(label_text)]
                    if direct in existing
                    else [province_slug, "merkez", slug(label_text)]
                )
                english = label_text
            key = "-".join(parts)
            path = "tr/" + "/".join(parts)
            if key in seen:
                raise ValueError(f"two 2004 municipalities share {key}")
            seen.add(key)
            if level == "town" and not district:
                before = "-".join(
                    [province_slug, "merkez" if legacy.startswith("Merkez") else slug(legacy), slug(label_text)]
                )
                if before != key:
                    moved[before] = key
                    cs.renamed[f"{DAY_2004}:{key}-mayor"] = f"{DAY_2004}:{before}-mayor"
            lookup = f"{province} Merkez" if central or not district else re.sub(r"\s*\(.*\)$", "", district)
            lookup = AREAS.get((plate, lookup), lookup)
            area = districts.get((plate, upper(SPELLINGS.get(upper(lookup), lookup))))
            if area is None:
                raise ValueError(f"no 2004 ilçe area for {key} ({lookup})")
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
            if level == "district" and polity not in territorial:
                territorial.add(polity)
                cs.add(
                    "polity_territory",
                    polity_id=polity,
                    area_id=area,
                    valid_from=None,
                    valid_from_precision="day",
                    valid_to=None,
                    valid_to_precision="day",
                )
            election = cs.key("tr.election", f"{DAY_2004}:{key}-mayor")
            cs.add(
                "election",
                id=election,
                polity_id=polity,
                parent_id=parent,
                kind="local",
                date_from=DAY_2004,
                date_to=DAY_2004,
                status="held",
            )
            name(election, f"2004 {english} mayoral election", "en")
            name(election, f"2004 {english} Belediye Başkanlığı Seçimi", kind="native")
            cs.add("slug", path=f"{path}/{DAY_2004}-local", entity_id=election)
            contest_key = f"{DAY_2004}:{key}-mayor"
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
            cs.add("slug", path=f"{path}/{DAY_2004}-local/mayor", entity_id=contest)
            scheme = "tr.ilce" if level == "district" else "tr.belde"
            unit = cs.unit(scheme, key)
            used.add(unit)
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=turkey,
                scheme_code=scheme,
                code=key,
                area_relation="within",
                area_id=area,
                kind="aggregate",
            )
            name(unit, english)
            declared = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=declared,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=declared, kind="methodology", body_md=METHOD_2004)
            votes = {label: row[label] for label in labels if row[label] > 0}
            options: dict[str, UUID] = {}
            for label in votes:
                owner = owners[label]
                options[label] = cs.key("tr.option", f"{contest_key}:{owner or 'independents'}")
                cs.add(
                    "contest_option",
                    id=options[label],
                    contest_id=contest,
                    kind="independents" if owner is None else "candidate",
                    party_id=None if owner is None else party_ids[label],
                    ballot_label=INDEPENDENTS if owner is None else party_names[owner],
                )
            ranked = sorted(votes, key=lambda label: votes[label], reverse=True)
            if len(ranked) > 1 and votes[ranked[0]] == votes[ranked[1]] or owners[ranked[0]] is None:
                cs.add("note", entity_id=declared, kind="caveat", body_md=TIE_2004)
            else:
                cs.add("outcome", result_set_id=declared, option_id=options[ranked[0]], kind="elected")
            for measure, value in zip(
                (REGISTERED, VOTERS, VALID, INVALID),
                (row["registered"], row["voters"], row["valid"], row["voters"] - row["valid"]),
                strict=True,
            ):
                cs.add(
                    "result_value",
                    result_set_id=declared,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
            for label, count in votes.items():
                cs.add(
                    "result_value",
                    result_set_id=declared,
                    unit_id=unit,
                    option_id=options[label],
                    channel=0,
                    measure=VOTES,
                    count_round=0,
                    flags=0,
                    value=count,
                )
        for before, after in moved.items():
            if before in existing:
                cs.merge(cs.key("tr.polity", before), cs.key("tr.polity", after))
                cs.merge(cs.key("tr.office", f"{before}-mayor"), cs.key("tr.office", f"{after}-mayor"))
        for (unit,) in conn.execute(
            """select ru.id from reporting_unit ru join changeset ch on ch.id = ru.changeset_id
               where ch.pipeline = %s""",
            [SOURCE],
        ).fetchall():
            if unit not in used:
                cs.add(
                    "entity_retirement",
                    entity_id=unit,
                    reason="A 2004 reporting unit filed under the wrong ilçe, replaced by the town's unit in its own ilçe.",
                )
        return cs.write()
