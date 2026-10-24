import io
import json
import re
import struct
import subprocess
import time
import unicodedata
import zipfile
import zlib
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.gb import ons_local, scottish_parliament, umit
from ea.store import DATA, connect

SOURCE = "gb.scot_council"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2022, 5, 5)
EMB = (
    "https://www.emb.scot/downloads/file/980/local-government-elections-2022-results-updated-with-postals-station-split"
)
MIRROR = "https://stv-results.prorep.org.uk"
INDEX = f"{MIRROR}/elections_2022_05.html"
BUNDLE = f"{MIRROR}/20220505_ME/ZIPs/All_Councils_All_wards.zip"


def bundle_wards() -> list[tuple[str, str, str]]:
    councils = {
        found.group(1)[:3]: found.group(1)
        for found in re.finditer(r'20220505_ME/pages/(C\d\d_[^/"]+)/', raw.cached(SOURCE, INDEX).path.read_text())
    }
    wards = []
    for info in zipfile.ZipFile(raw.cached(SOURCE, BUNDLE).path).infolist():
        found = re.match(r"Count_20220505_ME_(C\d\d)_W(\d\d)_MCEX_\d{8}_\d{6}__(.+)\.zip$", info.filename)
        if found is None:
            raise ValueError(f"unexpected bundle member {info.filename}")
        council, ward, tail = found.groups()
        name = f"Count_20220505_ME_{council}_W{ward}_MCEX_CandidateVotesPerStage__{tail}.pdf"
        wards.append((info.filename, name, f"{MIRROR}/20220505_ME/media/{councils[council]}/Ward_{ward}/{name}"))
    return wards


def stage_url(member: str, name: str, url: str) -> str:
    try:
        return raw.cached(SOURCE, f"{BUNDLE}#{member}/{name}").url
    except LookupError:
        return url


def fetch() -> None:
    for url, suffix in ((EMB, ".xlsx"), (INDEX, ".html"), (BUNDLE, ".zip")):
        raw.fetch(SOURCE, url, suffix=suffix)
    path = raw.cached(SOURCE, BUNDLE).path
    content = path.read_bytes()
    retrieved = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    infos = {info.filename: info for info in zipfile.ZipFile(path).infolist()}
    for member, name, url in bundle_wards():
        info = infos[member]
        start = content.find(member.encode(), max(0, info.header_offset)) - 30
        method = struct.unpack("<H", content[start + 8 : start + 10])[0]
        name_length, extra_length = struct.unpack("<HH", content[start + 26 : start + 30])
        body = content[start + 30 + name_length + extra_length :][: info.compress_size]
        try:
            data = zlib.decompress(body, -15) if method == 8 else body
            good = content[start : start + 4] == b"PK\x03\x04" and zlib.crc32(data) == info.CRC
        except zlib.error:
            good = False
        if good and name in zipfile.ZipFile(io.BytesIO(data)).namelist():
            raw.store(
                SOURCE,
                f"{BUNDLE}#{member}/{name}",
                zipfile.ZipFile(io.BytesIO(data)).read(name),
                suffix=".pdf",
                retrieved_at=retrieved,
                media_type="application/pdf",
            )
            continue
        try:
            raw.cached(SOURCE, url)
            continue
        except LookupError:
            pass
        try:
            raw.fetch(SOURCE, url, suffix=".pdf")
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 429 or error.response.status_code >= 500:
                raise
            print("missing", url, error.response.status_code)
        time.sleep(2)


CHECKED: dict[str, str] = {
    "S12000005": "https://www.clacks.gov.uk/document/6700.pdf",
}
ALIASES = {"comhairle nan eilean siar": "na h eileanan siar", "comhairle eilean siar": "na h eileanan siar"}
NUMBER = r"-?[\d,]+(?:\.\d+)?"


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"['’`]", "", text.replace("&", " and "))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def council_key(text: str) -> str:
    text = fold(re.sub(r"^C\d\d_", "", text).replace("_", " "))
    text = re.sub(r"^the | council$", "", text).strip()
    return ALIASES.get(text, text)


def name_key(text: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower())


def ward_key(text: str) -> str:
    return re.sub(r" ward$", "", re.sub(r"^ward \d+ ?", "", fold(text)))


def parse() -> None:
    emb = pl.read_excel(raw.cached(SOURCE, EMB).path, sheet_name="RawDetails", engine="calamine")
    features = [
        feature
        for key in ("wd22", "lad22")
        for url in ons_local.pages(key)
        for feature in json.loads(raw.cached(ons_local.SOURCE, url).path.read_text())["features"]
    ]
    wards = {
        (council_key(p["LAD22NM"]), ward_key(p["WD22NM"])): p
        for feature in features
        if (p := feature["properties"]).get("WD22CD", "").startswith("S13")
    }
    if len(wards) != 355:
        raise ValueError(f"{len(wards)} Scottish wards in the December 2022 layer")
    councils = {council_key(p["LAD22NM"]): p["LAD22CD"] for p in wards.values()}
    emb_wards: dict[str, list[tuple[str, int, bool, int | None, str, str]]] = {}
    emb_facts: dict[str, tuple[int, int, int, int]] = {}
    for row in emb.iter_rows(named=True):
        key = f"{row['Authority Name']}\x1f{row['Ward Number and Description']}"
        emb_wards.setdefault(key, []).append(
            (
                str(row["CandidateName"]),
                int(row["NumberOfFirstPreferenceVotes"] or 0),
                row["CandidateElected"] == "Yes",
                None if row["CandidateStageElected"] is None else int(row["CandidateStageElected"]),
                str(row["Affiliation"]),
                str(row["AffiliationNational"]),
            )
        )
        emb_facts[key] = (
            int(row["Electorate"]),
            int(row["Total Ballot Papers"] or 0),
            int(row["Total Ballot Papers Included In Count"] or 0),
            int(row["Total Ballot Papers Rejected"] or 0),
        )
    units: list[dict[str, str | int | bool | None]] = []
    totals: list[dict[str, str | int | bool | None]] = []
    matched: set[str] = set()
    unread: set[str] = set()
    for member, name, url in bundle_wards():
        locator = stage_url(member, name, url)
        council = council_key(re.findall(r"media/(C\d\d_[^/]+)/", url)[0])
        try:
            path = raw.cached(SOURCE, locator).path
        except LookupError:
            unread.add(council)
            units.append({"council_code": councils.get(council), "member": member, "status": "no stage report"})
            continue
        text = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
        ).stdout
        facts = {
            key: Decimal(found.group(1).replace(",", ""))
            for key, pattern in (
                ("valid", r"Total Number of Valid Votes\s+(" + NUMBER + ")"),
                ("seats", r"Positions to be Filled\s+(" + NUMBER + ")"),
                ("quota", r"Quota\s+(" + NUMBER + ")"),
            )
            if (found := re.search(pattern, text)) is not None
        }
        stage = 0
        rows: list[tuple[int, str, Decimal, str | None]] = []
        exhausted: dict[int, Decimal] = {}
        dropped: set[str] = set()
        odd = []
        for line in text.splitlines():
            if (found := re.match(r"\s*Stage (\d+)\s*$", line)) is not None:
                stage = int(found.group(1))
                continue
            if stage == 0 or not line.strip() or line[0].isspace():
                continue
            if (found := re.match(r"Exclusion of (.+?)(?:\s{2,}|$)", line)) is not None:
                dropped.add(name_key(found.group(1)))
                continue
            if re.match(r"(Surplus of|Report Name|Transfer of)", line):
                continue
            status_found = re.search(r"\s(Elected|Excluded)$", line.rstrip())
            outcome = status_found.group(1) if status_found else None
            fields = re.split(r"\s{2,}", line.rstrip()[: status_found.start()] if status_found else line.strip())
            numbers = []
            while fields and re.fullmatch(NUMBER, fields[-1]):
                numbers.insert(0, fields.pop())
            if not numbers and fields and rows and rows[-1][0] == stage:
                rows[-1] = (stage, f"{rows[-1][1]} {fields[0]}", rows[-1][2], rows[-1][3])
                continue
            if not numbers or not fields:
                odd.append(line.strip())
                continue
            value = Decimal(numbers[-1].replace(",", ""))
            if fields[0].startswith("Non-transferable votes"):
                exhausted[stage] = exhausted.get(stage, Decimal(0)) + value
                continue
            rows.append((stage, fields[0], value, outcome))
        names = [label for number, label, _, _ in rows if number == 1]
        found_emb = [
            key
            for key, lines in emb_wards.items()
            if council_key(key.split("\x1f")[0]) == council
            and {name_key(line[0]) for line in lines} == {name_key(label) for label in names}
        ]
        key = found_emb[0] if len(found_emb) == 1 else None
        first = {
            label: line
            for line in (emb_wards[key] if key else [])
            for label in names
            if name_key(label) == name_key(line[0])
        }
        description = key.split("\x1f")[1] if key else None
        ons = wards.get((council, ward_key(description))) if description else None
        elected = {label for _, label, _, outcome in rows if outcome == "Elected"}
        stages = sorted({number for number, _, _, _ in rows})
        status = "loaded"
        if odd:
            status = f"unread lines: {odd[:3]}"
        elif key is None:
            status = "no single EMB ward with the same candidates"
        elif ons is None:
            status = "no ONS ward code"
        elif any(Decimal(first[label][1]) != votes for number, label, votes, _ in rows if number == 1):
            status = "stage 1 differs from EMB first preferences"
        elif facts.get("valid") != Decimal(emb_facts[key][2]):
            status = "valid votes differ from EMB"
        elif elected != {label for label, line in first.items() if line[2]}:
            status = "elected differ from EMB"
        elif len(elected) != facts.get("seats"):
            status = "elected do not fill the seats"
        else:
            for number in stages:
                total = exhausted.get(number, Decimal(0))
                for at, _, votes, _ in rows:
                    if at == number:
                        total += votes
                if total != facts["valid"]:
                    status = f"stage {number} sums to {total}, not {facts['valid']}"
                    break
            for candidate in sorted(elected):
                if status != "loaded":
                    break
                best = max(votes for _, label, votes, _ in rows if label == candidate)
                when = min(number for number, label, _, outcome in rows if label == candidate and outcome == "Elected")
                if best < facts["quota"] and when != stages[-1]:
                    status = f"{candidate} elected below the quota before the last stage"
                elif first[candidate][3] != when:
                    status = f"{candidate} elected at stage {when}, EMB says {first[candidate][3]}"
        if key is not None:
            matched.add(key)
        electorate, ballots, _, invalid = emb_facts[key] if key else (None, None, None, None)
        units.append(
            {
                "council_code": councils.get(council),
                "member": member,
                "locator": locator,
                "status": status,
                "emb_ward": description,
                "ward_code": ons["WD22CD"] if ons else None,
                "ward_name": ons["WD22NM"] if ons else None,
                "seats": int(facts["seats"]) if "seats" in facts else None,
                "electorate": electorate,
                "ballots": ballots,
                "valid": str(facts.get("valid")),
                "invalid": invalid,
                "quota": str(facts.get("quota")),
                "uncontested": False,
            }
        )
        if status != "loaded" or ons is None:
            continue
        order = {label: number for number, label in enumerate(names, start=1)}
        for number, label, votes, _ in rows:
            line = first[label]
            totals.append(
                {
                    "ward_code": ons["WD22CD"],
                    "number": order[label],
                    "name": re.sub(
                        r"[ÂÃ][\x80-\xbf]",
                        lambda pair: pair.group(0).encode("latin-1").decode("utf-8"),
                        line[0],
                    )
                    if "Ã" in label
                    else label,
                    "party": line[5],
                    "elected": line[2],
                    "outcome": "elected" if line[2] else ("eliminated" if name_key(label) in dropped else "lost"),
                    "stage": number,
                    "votes": str(votes),
                }
            )
        for number in stages:
            totals.append(
                {
                    "ward_code": ons["WD22CD"],
                    "number": 0,
                    "name": None,
                    "party": None,
                    "elected": False,
                    "outcome": None,
                    "stage": number,
                    "votes": str(exhausted.get(number, Decimal(0))),
                }
            )
    for key, lines in emb_wards.items():
        authority, description = key.split("\x1f")
        council = council_key(authority)
        if key in matched or emb_facts[key][2] == 0 or council not in unread:
            continue
        ons = wards.get((council, ward_key(description)))
        electorate, ballots, included, invalid = emb_facts[key]
        units.append(
            {
                "council_code": councils.get(council),
                "member": None,
                "locator": None,
                "status": "first preferences do not sum to the included ballots"
                if sum(line[1] for line in lines) != included
                else ("loaded" if ons else "no ONS ward code"),
                "emb_ward": description,
                "ward_code": ons["WD22CD"] if ons else None,
                "ward_name": ons["WD22NM"] if ons else None,
                "seats": sum(line[2] for line in lines),
                "electorate": electorate,
                "ballots": ballots,
                "valid": str(included),
                "invalid": invalid,
                "quota": None,
                "uncontested": False,
                "emb_only": True,
            }
        )
        if ons and sum(line[1] for line in lines) == included:
            for number, line in enumerate(lines, start=1):
                totals.append(
                    {
                        "ward_code": ons["WD22CD"],
                        "number": number,
                        "name": line[0],
                        "party": line[5],
                        "elected": line[2],
                        "outcome": "elected" if line[2] else "lost",
                        "stage": 1,
                        "votes": str(line[1]),
                        "stage_elected": line[3],
                    }
                )
    for key, lines in emb_wards.items():
        if key in matched or not all(line[2] for line in lines) or emb_facts[key][2] != 0:
            continue
        authority, description = key.split("\x1f")
        council = council_key(authority)
        ons = wards.get((council, ward_key(description)))
        units.append(
            {
                "council_code": councils.get(council),
                "member": None,
                "locator": None,
                "status": "loaded" if ons else "no ONS ward code",
                "emb_ward": description,
                "ward_code": ons["WD22CD"] if ons else None,
                "ward_name": ons["WD22NM"] if ons else None,
                "seats": len(lines),
                "electorate": emb_facts[key][0],
                "ballots": None,
                "valid": None,
                "invalid": None,
                "quota": None,
                "uncontested": True,
            }
        )
        if ons:
            for number, line in enumerate(lines, start=1):
                totals.append(
                    {
                        "ward_code": ons["WD22CD"],
                        "number": number,
                        "name": line[0],
                        "party": line[5],
                        "elected": True,
                        "outcome": "elected",
                        "stage": 0,
                        "votes": None,
                    }
                )
    frame = pl.DataFrame(units, infer_schema_length=None)
    print(frame.group_by("status").len().sort("len", descending=True).rows())
    print("council names", {row["council_code"] for row in units if row["council_code"] is None})
    codes = set(frame.filter(pl.col("status") == "loaded")["ward_code"]) | set(frame["council_code"].drop_nulls())
    with connect() as conn:
        conn.execute("create temp table feature (code text, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy feature from stdin") as copy:
            for feature in features:
                code = feature["properties"].get("WD22CD") or feature["properties"].get("LAD22CD")
                if code in codes:
                    copy.write_row((code, json.dumps(feature["geometry"])))
        shapes = conn.execute(
            """select code, encode(st_asewkb(st_multi(st_transform(geom, 4326))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
               from (select code, st_multi(st_collectionextract(st_makevalid(st_union(
                       st_makevalid(st_force2d(st_setsrid(st_geomfromgeojson(geojson), 27700))))), 3)) as geom
                     from feature group by code) whole"""
        ).fetchall()
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "units.parquet")
    pl.DataFrame(totals, infer_schema_length=None).write_parquet(PARSED / "totals.parquet")
    pl.DataFrame(
        shapes, schema={"code": pl.String, "geom": pl.String, "label_point": pl.String}, orient="row"
    ).write_parquet(PARSED / "shapes.parquet")


VOTES, REGISTERED, BALLOTS, VALID, INVALID, EXHAUSTED, QUOTA = 1, 2, 3, 4, 5, 21, 26
OGL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"


def emit() -> Path:
    units = pl.read_parquet(PARSED / "units.parquet").filter(pl.col("status") == "loaded")
    totals = pl.read_parquet(PARSED / "totals.parquet")
    shapes = {code: (geom, point) for code, geom, point in pl.read_parquet(PARSED / "shapes.parquet").rows()}
    names = {
        p["LAD22CD"]: p["LAD22NM"]
        for url in ons_local.pages("wd22")
        for feature in json.loads(raw.cached(ons_local.SOURCE, url).path.read_text())["features"]
        if (p := feature["properties"])["WD22CD"].startswith("S13")
    }
    emb = pl.read_excel(raw.cached(SOURCE, EMB).path, sheet_name="RawDetails", engine="calamine")
    authorities = {}
    for authority in emb["Authority Name"].unique().to_list():
        key = council_key(authority)
        authorities[next(code for code, label in names.items() if council_key(label) == key)] = authority
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Scottish council elections of 5 May 2022 by ward with every STV stage, from the councils' eCount "
            "reports, tied to the Electoral Management Board's national results, with ONS ward and council shapes",
            polity="GB-SCT",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        gb = cs.polity("GB")
        scotland = cs.polity("GB-SCT")
        country = cs.area("gb.country", "SCT", DAY)
        system = cs.key("gb.electoral_system", "stv")
        if cs.minted & {country, system}:
            raise ValueError("publish gb.umit and the stv electoral system first")
        parties: dict[str, UUID] = {}

        def party_of(label: str | None) -> UUID | None:
            if label is None or label == "Independent" or label in umit.NOT_PARTIES:
                return None
            key = scottish_parliament.PARTY_KEYS.get(label.lower()) or umit.ALIASES.get(label) or slug(label)
            if key not in parties:
                if key in umit.EXISTING:
                    parties[key] = cs.key(*umit.EXISTING[key])
                else:
                    parties[key] = cs.key("gb.party", key)
                    if parties[key] in cs.minted:
                        cs.add("party", id=parties[key], kind="party", polity_id=gb)
                        name(parties[key], umit.NAMES.get(key, label))
            return parties[key]

        emb_source = cs.source("Electoral Management Board for Scotland", "Local Government Elections 2022 - Results")
        cs.add(
            "source",
            id=emb_source,
            kind="official_body",
            publisher="Electoral Management Board for Scotland",
            title="Local Government Elections 2022 - Results",
            url=EMB,
            license="Open Government Licence v3.0",
            license_url=OGL,
            attribution="Electoral Management Board for Scotland",
            notes="The national results workbook (updated 13 May 2022 with the postal and polling station split); its "
            "RawDetails sheet has one row per candidate per ward with first preferences, the elected, and the stage "
            "each was elected at.",
        )
        cs.artifact(raw.cached(SOURCE, EMB), emb_source)
        wd22 = cs.source("Office for National Statistics", ons_local.LAYERS["wd22"][4])
        lad22 = cs.source("Office for National Statistics", ons_local.LAYERS["lad22"][4])
        if cs.minted & {wd22, lad22}:
            raise ValueError("publish gb.ons_local first")
        paths = {path for (path,) in conn.execute("select path from slug where path like 'gb/%%'").fetchall()}
        national = cs.key("gb.election", f"{DAY}:local:sct")
        cs.add("election", id=national, polity_id=scotland, kind="local", date_from=DAY, date_to=DAY, status="held")
        name(national, "2022 Scottish local elections")
        cs.add("slug", path=f"gb/scotland/{DAY}-local", entity_id=national)
        councils: dict[str, tuple[UUID, UUID, UUID, UUID, str, UUID]] = {}
        for code in sorted(set(units["council_code"])):
            label = names[code]
            authority = authorities[code]
            area = cs.area("gb.lad", code)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code="gb.lad")
                cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=None)
                cs.add("area_parent", area_id=area, parent_id=country, valid_from=None, valid_to=None)
                name(area, label)
                geom, point = shapes[code]
                cs.add(
                    "area_geometry",
                    area_id=area,
                    valid_from=DAY,
                    valid_to=None,
                    geom=geom,
                    label_point=point,
                    quality="official",
                    source_id=lad22,
                )
            polity = cs.key("gb.polity", code)
            path = f"gb/{slug(label)}"
            if polity in cs.minted:
                cs.add("polity", id=polity, kind="municipality", iso_code=None)
                cs.add("polity_parent", polity_id=polity, parent_id=scotland)
                cs.add("polity_territory", polity_id=polity, area_id=area)
                if path in paths:
                    path = f"gb/{slug(label)}-council"
                cs.add("slug", path=path, entity_id=polity)
            else:
                current = conn.execute(
                    "select path from slug where entity_id = %s order by is_current desc limit 1", [polity]
                ).fetchone()
                if current is None:
                    raise ValueError(f"{code} has no slug")
                path = current[0]
            name(polity, authority)
            body = cs.key("gb.body", code)
            if body in cs.minted:
                cs.add("body", id=body, polity_id=polity, kind="council")
            name(body, authority)
            seats = int(units.filter(pl.col("council_code") == code)["seats"].sum())
            cs.add(
                "body_size",
                body_id=body,
                seats=seats,
                valid_from=DAY,
                valid_from_precision="day",
                valid_to=None,
                valid_to_precision="day",
            )
            election = cs.key("gb.election", f"{DAY}:local:{code}")
            cs.add(
                "election",
                id=election,
                polity_id=polity,
                parent_id=national,
                kind="local",
                date_from=DAY,
                date_to=DAY,
                status="held",
            )
            name(election, f"2022 {authority} election")
            cs.add("slug", path=f"{path}/{DAY}-local", entity_id=election)
            council_page = next(
                f"{MIRROR}/20220505_ME/pages/{found}/{found}.html"
                for found in re.findall(
                    r'20220505_ME/pages/(C\d\d_[^/"]+)/', raw.cached(SOURCE, INDEX).path.read_text()
                )
                if council_key(found) == council_key(label)
            )
            source = cs.source(authority, "Local government election 5 May 2022: eCount reports")
            checked = CHECKED.get(code)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=authority,
                title="Local government election 5 May 2022: eCount reports",
                url=council_page,
                license="Open Government Licence v3.0",
                license_url=OGL,
                attribution=authority,
                notes=(
                    "The returning officer's Candidate Votes per Stage report for each ward, as produced by the "
                    "Scottish eCount system, retrieved from the stv-results.prorep.org.uk mirror of the councils' "
                    "published files"
                    + (f"; the mirror's copy matched the council's own ({checked}) byte for byte." if checked else ".")
                ),
            )
            councils[code] = (area, polity, body, election, path, source)

        for unit_row in units.sort("ward_code").iter_rows(named=True):
            code, ward_code, label = unit_row["council_code"], unit_row["ward_code"], unit_row["ward_name"]
            area, polity, body, election, path, source = councils[code]
            ward = cs.area("gb.ward", ward_code)
            if ward in cs.minted:
                cs.add("area", id=ward, scheme_code="gb.ward")
                cs.add("area_code", area_id=ward, code=ward_code, valid_from=None, valid_to=None)
                cs.add("area_parent", area_id=ward, parent_id=area, valid_from=None, valid_to=None)
                name(ward, label)
                geom, point = shapes[ward_code]
                cs.add(
                    "area_geometry",
                    area_id=ward,
                    valid_from=DAY,
                    valid_to=None,
                    geom=geom,
                    label_point=point,
                    quality="official",
                    source_id=wd22,
                )
            unit = cs.unit("gb.ward", ward_code)
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=polity,
                    scheme_code="gb.ward",
                    code=ward_code,
                    area_relation="equals",
                    area_id=ward,
                    kind="standard",
                    valid_from=None,
                )
                name(unit, label)
            contest_key = f"{DAY}:local:{ward_code}"
            contest = cs.key("gb.contest", contest_key)
            uncontested = unit_row["uncontested"]
            emb_only = unit_row["emb_only"] is True
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=body,
                district_area_id=ward,
                magnitude=unit_row["seats"],
                round_number=1,
                electoral_system_id=system,
                default_result_kind="tacit" if uncontested else "counted",
            )
            name(contest, f"{label} ward, {authorities[code]}, 2022")
            cs.add("slug", path=f"{path}/{DAY}-local/{slug(label)}", entity_id=contest)
            result_set = cs.key("gb.scot_council.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="tacit" if uncontested else "counted",
                status="active",
                source_id=emb_source if uncontested or emb_only else source,
                is_default=True,
                completeness=1,
            )
            if uncontested:
                cs.add("citation", entity_id=result_set, source_id=emb_source, locator={"ward": unit_row["emb_ward"]})
            elif emb_only:
                cs.add("citation", entity_id=result_set, source_id=emb_source, locator={"ward": unit_row["emb_ward"]})
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md="The council's stage report (Candidate Votes per Stage) was not found, so this ward "
                    "shows the Electoral Management Board's electorate, ballots, first preferences, and declared "
                    "members, with the stage at which each was elected; transfers are not included.",
                )
            else:
                cs.add(
                    "citation",
                    entity_id=result_set,
                    source_id=source,
                    locator={"ward": unit_row["emb_ward"], "file": unit_row["locator"]},
                )
                cs.add("citation", entity_id=result_set, source_id=emb_source, locator={"ward": unit_row["emb_ward"]})
                cs.artifact(raw.cached(SOURCE, unit_row["locator"]), source)
                checked = CHECKED.get(code)
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="methodology",
                    body_md="The stages are the council's own eCount report (Candidate Votes per Stage), retrieved "
                    "from the stv-results.prorep.org.uk mirror"
                    + (
                        ", which matched the council's copy byte for byte where checked."
                        if checked
                        else "; the council's own copy could not be reached to compare."
                    )
                    + " Stage 1 equals the Electoral Management Board's first preferences, the elected equal its "
                    "declared members, and every stage sums to the valid votes.",
                )
            if not uncontested:
                for measure, amount in (
                    (REGISTERED, unit_row["electorate"]),
                    (BALLOTS, unit_row["ballots"]),
                    (VALID, unit_row["valid"]),
                    (INVALID, unit_row["invalid"]),
                    (QUOTA, unit_row["quota"]),
                ):
                    if amount is None:
                        continue
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=Decimal(str(amount)),
                    )
            lines = totals.filter(pl.col("ward_code") == ward_code)
            for candidate in (
                lines.filter(pl.col("number") > 0)
                .select("number", "name", "party", "elected", "outcome", "stage_elected")
                .unique(maintain_order=True, subset=["number"])
                .iter_rows(named=True)
            ):
                owner = party_of(candidate["party"])
                full = " ".join(
                    word.capitalize() if word.isupper() and len(word) > 1 else word
                    for word in candidate["name"].split()
                )
                option_key = f"{contest_key}:{candidate['number']}"
                option = cs.key("gb.scot_council.option", option_key)
                person = cs.key("gb.scot_council.person", option_key)
                cs.add("person", id=person)
                name(person, full)
                cs.add(
                    "contest_option", id=option, contest_id=contest, kind="candidate", party_id=owner, ballot_label=full
                )
                cs.add(
                    "candidacy",
                    id=cs.key("gb.scot_council.candidacy", option_key),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=candidate["name"],
                    member_of_party_id=owner,
                    is_independent=candidate["party"] == "Independent",
                )
                if not uncontested:
                    for row in (
                        lines.filter(pl.col("number") == candidate["number"]).sort("stage").iter_rows(named=True)
                    ):
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=VOTES,
                            count_round=row["stage"],
                            flags=0,
                            value=Decimal(row["votes"]),
                        )
                        if Decimal(row["votes"]) == 0 and row["stage"] > 1 and not candidate["elected"]:
                            break
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind=candidate["outcome"],
                    notes=f"Elected at stage {candidate['stage_elected']}."
                    if emb_only and candidate["elected"]
                    else None,
                )
            for row in lines.filter(pl.col("number") == 0).iter_rows(named=True):
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=EXHAUSTED,
                    count_round=row["stage"],
                    flags=0,
                    value=Decimal(row["votes"]),
                )
        if (
            conn.execute("select 1 from boundary_set where scheme_code = 'gb.ward' and vintage = '2022'").fetchone()
            is None
        ):
            raise ValueError("the gb.ward 2022 boundary set is missing")
        return cs.write()
