import difflib
import html
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.tr.ysk import PROVINCES, REGISTERED, VALID, VOTERS, VOTES, title, upper
from ea.store import DATA, connect

SOURCE = "tr.tuik_secim"
VERSION = "1"
APP = "https://biruni.tuik.gov.tr/secimdagitimapp/secim.zul"
TABLE = "Seçim çevresi ve ilçelere göre milletvekili genel seçimi sonuçları"
YEARS = ("1961", "1965", "1969", "1973", "1977", "1983", "1987", "1991", "1995", "1999", "2002", "2007")
PARSED = DATA / "parsed" / SOURCE
MEASURES = (("Sandık", "boxes"), ("Kayıtlı", "registered"), ("Oy kullanan", "voters"), ("Geçerli", "valid"))
PUBLISHER = "Türkiye İstatistik Kurumu"
TITLE = "Seçim çevresi ve ilçelere göre milletvekili genel seçimi sonuçları"
LICENSE = "No license stated. TÜİK allows its published statistics to be used with the source cited."
DAYS = {
    "1961": date(1961, 10, 15),
    "1965": date(1965, 10, 10),
    "1969": date(1969, 10, 12),
    "1973": date(1973, 10, 14),
    "1977": date(1977, 6, 5),
    "1983": date(1983, 11, 6),
    "1987": date(1987, 11, 29),
    "1991": date(1991, 10, 20),
    "1995": date(1995, 12, 24),
    "1999": date(1999, 4, 18),
    "2002": date(2002, 11, 3),
    "2007": date(2007, 7, 22),
}
PROVINCE_ALIASES = {
    "AFYON": "AFYONKARAHİSAR",
    "KAHRAMANMARAS": "KAHRAMANMARAŞ",
    "K,MARAŞ": "KAHRAMANMARAŞ",
    "K.MARAŞ": "KAHRAMANMARAŞ",
    "İÇEL": "MERSİN",
}
LABEL_ALIASES: dict[str, str] = {}
ILCE_ALIASES = {
    ("03", "SİNCANLI"): "SİNANPAŞA",
    ("07", "KALE"): "DEMRE",
    ("06", "KAZAN"): "KAHRAMANKAZAN",
    ("09", "YENİHİSAR"): "DİDİM",
    ("14", "CUMAOVA"): "CUMAYERİ",
}
METHODOLOGY = (
    "District results by ilçe from TÜİK's election database (table: {title}). TÜİK notes that its district totals come "
    "from the Official Gazette and its ilçe totals from the counting boards' consolidation reports, so the ilçes can "
    "add up to slightly different totals than the district's declared result, which stays the default."
)
NATIONAL = (
    "TÜİK reports this election's ilçes by province, while the contests were held in electoral districts that split "
    "some provinces, and it does not say which district each ilçe belonged to. Those provinces' ilçes are given "
    "here on the national contest only."
)
CUSTOMS = (
    "TÜİK's district totals for 2007 include the district's share of the votes cast at customs gates; its ilçe "
    "rows do not."
)
PARTS = {
    "İl/İlçe merkezi": "urban",
    "il/İlçe merkezi": "urban",
    "Şehir toplamı": "urban",
    "Belde/Köy": "rural",
    "Bucak ve köyler toplamı": "rural",
}
ITEM = r'<tr id="([^"]+)" z\.type="Lit"[^>]*>\s*<td[^>]*>([^<]+)'


def key(year: str, district: str) -> str:
    return f"{APP}?tablo=ilce&yil={year}&cevre={district}"


class App:
    def __init__(self) -> None:
        self.client = httpx.Client(timeout=120, follow_redirects=True, headers={"User-Agent": raw.USER_AGENT})
        page = self.client.get(APP).text
        self.action = "https://biruni.tuik.gov.tr" + re.search(r'zk_action="([^"]+)"', page).group(1)
        self.dtid = re.search(r'z\.dtid="([^"]+)"', page).group(1)
        self.button = re.search(r'<button type="button" id="([^"]+)"[^>]*>\s*Raporu Oluştur', page).group(1)
        at = page.index(TABLE)
        item = re.findall(r'<tr id="([^"]+)" z\.type="Lit"', page[:at])[-1]
        box = re.findall(r'<div id="([^"]+)" z\.type="zul\.sel\.Libox"', page[:at])[-1]
        self.years = self.select(box, item)

    def post(self, data: list[tuple[str, str]]) -> str:
        response = self.client.post(
            self.action,
            content=urlencode([("dtid", self.dtid), *data]),
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        )
        response.raise_for_status()
        return response.text

    def select(self, box: str, item: str) -> tuple[str, dict[str, str]]:
        text = self.post([("cmd.0", "onSelect"), ("uuid.0", box), ("data.0", item)])
        if re.search(r"<d>[^<]+_e2</d>\s*<d>visibility</d>\s*<d>true</d>", text):
            self.post([("cmd.0", "onCheck"), ("uuid.0", self.button.removesuffix("e3") + "g2"), ("data.0", "true")])
        found = re.search(r'<div id="([^"]+)" z\.type="zul\.sel\.Libox"', text)
        if found is None:
            return "", {}
        return found.group(1), {html.unescape(label).strip(): uuid for uuid, label in re.findall(ITEM, text)}

    def report(self) -> str:
        text = self.post(
            [("cmd.0", "onClick"), ("uuid.0", self.button), ("data.0", "43"), ("data.0", "9"), ("data.0", "")]
        )
        return re.search(r"<c>redirect</c>\s*<d>([^<]+)</d>", text).group(1)


def year(label: str) -> None:
    for attempt in range(6):
        try:
            app = App()
            box, years = app.years
            districts_box, districts = app.select(box, years[label])
            for district, item in districts.items():
                url = key(label, district)
                try:
                    raw.cached(SOURCE, url)
                    continue
                except LookupError:
                    pass
                app.select(districts_box, item)
                link = app.report().replace("http://", "https://")
                content = app.client.get(link).content
                if b"<tr" not in content:
                    raise ValueError(f"{label} {district}: empty report")
                raw.store(SOURCE, url, content, suffix=".html", retrieved_at=datetime.now(UTC), media_type="text/html")
                print(label, district, len(content), flush=True)
            return
        except (httpx.HTTPError, AttributeError, ValueError, KeyError) as error:
            print(label, "retry", attempt, repr(error), flush=True)
            time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"{label}: gave up")


def fetch() -> None:
    with ThreadPoolExecutor(3) as pool:
        list(pool.map(year, YEARS))


def cells(row: str) -> list[str]:
    return [
        html.unescape(re.sub(r"<[^>]+>", "", cell)).replace("\xa0", " ").strip()
        for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.DOTALL | re.IGNORECASE)
    ]


def parse() -> None:
    _, index = raw._index(SOURCE)
    rows: list[dict[str, object]] = []
    notes: list[dict[str, str]] = []
    for url in sorted(index):
        year, district = re.search(r"yil=(\d{4})&cevre=(.+)$", url).groups()
        text = re.sub(
            r"<script.*?</script>", "", raw.cached(SOURCE, url).path.read_bytes().decode("cp1254"), flags=re.DOTALL
        )
        table = [cells(row) for row in re.findall(r"<tr.*?</tr>", text, flags=re.DOTALL | re.IGNORECASE)]
        title = next(row[0] for row in table if row and "Seçim" in row[0] and "Sonuçları" in row[0])
        if year not in title:
            raise ValueError(f"{url}: title {title}")
        start = next(i for i, row in enumerate(table) if row and row[0] == title)
        end = next(i for i, row in enumerate(table) if i > start and any(re.fullmatch(r"\d+", cell) for cell in row))
        labels = [
            cell
            for row in table[start + 1 : end]
            for cell in row
            if cell and cell != "Seçim çevresi ve ilçe" and not cell.startswith("Seçime")
        ]
        columns = [code for prefix, code in MEASURES if any(label.startswith(prefix) for label in labels)] + [
            re.sub(r"\s+", " ", label) for label in labels if not label.startswith(tuple(p for p, _ in MEASURES))
        ]
        level = None
        for row in table[end:]:
            if not row or not any(row):
                continue
            if row[0] and not any(re.fullmatch(r"\d+", cell) for cell in row[1:]):
                notes.append({"year": year, "district": district, "text": " ".join(cell for cell in row if cell)})
                continue
            if row[0]:
                level, name = ("nation" if row[0] == "Türkiye" else "district"), row[0]
            elif len(row) > 1 and row[1] in PARTS:
                level = PARTS[row[1]]
            elif len(row) > 1 and row[1]:
                level, name = "ilce", row[1]
            else:
                continue
            numbers = [
                cell
                for cell in (row[1:] if level in ("nation", "district") else row[2:])
                if cell and not re.fullmatch(r"\(\d+\)", cell)
            ]
            if level in PARTS.values() and len(numbers) != len(columns):
                continue
            if len(numbers) != len(columns) or any(not re.fullmatch(r"-|\d+", cell) for cell in numbers):
                raise ValueError(f"{url}: {row}")
            values = [0 if cell == "-" else int(cell) for cell in numbers]
            for column, value in zip(columns, values, strict=True):
                rows.append(
                    {"year": year, "district": district, "level": level, "name": name, "column": column, "value": value}
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows)
    frame.write_parquet(PARSED / "ilce.parquet")
    pl.DataFrame(notes).unique().write_parquet(PARSED / "notes.parquet")
    print(frame.group_by("year", "level").agg(pl.col("name").n_unique()).sort("year", "level"))


def norm(text: str) -> str:
    return " ".join(upper(text.replace("\xa0", " ").translate(str.maketrans("âîûÂÎÛ", "aiuAİU"))).split())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "ilce.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Ilçe results of Turkey's general elections from 1961 to 2007, from TÜİK's election database",
            polity="TR",
        )
        turkey = cs.polity("TR")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=APP,
            license=LICENSE,
            notes=METHODOLOGY.format(title=TITLE),
        )
        _, index = raw._index(SOURCE)
        for url in sorted(index):
            cs.artifact(raw.cached(SOURCE, url), source)

        areas: dict[UUID, list[tuple[str, date | None, date | None, str]]] = {}
        for area, text, start, end, plate in conn.execute(
            """select a.id, n.text, n.valid_from, n.valid_to, pc.code from area a
               join name n on n.entity_id = a.id and n.lang = 'tr'
               join area_parent ap on ap.area_id = a.id
               join area p on p.id = ap.parent_id and p.scheme_code = 'tr.il'
               join area_code pc on pc.area_id = p.id
               where a.scheme_code = 'tr.ilce'"""
        ).fetchall():
            areas.setdefault(area, []).append((norm(text), start, end, plate))
        created = dict(
            conn.execute(
                """select m.area_id, min(e.date) from area_event e join area_event_member m on m.event_id = e.id
                   where e.kind = 'created' and m.role = 'to' and m.area_id = any(%s) group by 1""",
                [list(areas)],
            ).fetchall()
        )
        ended = dict(
            conn.execute(
                """select m.area_id, max(e.date) from area_event e join area_event_member m on m.event_id = e.id
                   where e.kind in ('absorbed', 'split', 'dissolved', 'merged') and m.role = 'from'
                     and not exists (select from area_event_member t where t.event_id = e.id and t.role = 'to'
                                     and t.area_id = m.area_id)
                     and m.area_id = any(%s) group by 1""",
                [list(areas)],
            ).fetchall()
        )
        province_created = dict(
            conn.execute(
                """select ac.code, min(e.date) from area a join area_code ac on ac.area_id = a.id
                   join area_event_member m on m.area_id = a.id and m.role = 'to'
                   join area_event e on e.id = m.event_id and e.kind = 'created'
                   where a.scheme_code = 'tr.il' group by 1"""
            ).fetchall()
        )
        plates = {norm(name): f"{number:02}" for number, name in enumerate(PROVINCES, 1)}

        def match(day: date, plate: str, label: str, late: bool = False) -> UUID | None:
            allowed = {plate} | {code for code, start in province_created.items() if start > day}
            forms = {norm(re.sub(r"\s*\(.*?\)", "", label))} | {norm(alt) for alt in re.findall(r"\((.*?)\)", label)}
            forms = {ILCE_ALIASES.get((plate, form), form) for form in forms}
            if "MERKEZ" in forms:
                forms = {f"{norm(PROVINCES[int(plate) - 1])} MERKEZ"}
            since = day - timedelta(days=400) if late else day
            live = {
                area: names
                for area, names in areas.items()
                if (created.get(area) is None or created[area] <= day)
                and (ended.get(area) is None or ended[area] > since)
            }

            def named(test) -> set[UUID]:
                return {
                    area
                    for area, names in live.items()
                    for text, start, end, parent in names
                    if parent in allowed
                    and (start is None or start <= day)
                    and (end is None or end > since)
                    and test(text)
                }

            found = named(lambda text: text in forms)
            if not found:
                found = named(
                    lambda text: any(
                        re.search(r"[,.]", form) is not None
                        and re.fullmatch(re.sub(r"[,.]", ".*", form), text.replace(" ", ""))
                        for form in forms
                    )
                )
            if not found:
                scored = sorted(
                    (difflib.SequenceMatcher(None, form, text).ratio(), area, text)
                    for area, names in live.items()
                    for text, start, end, parent in names
                    if parent in allowed and (end is None or end > since)
                    for form in forms
                )
                if (
                    scored
                    and scored[-1][0] >= 0.85
                    and (len(scored) < 2 or scored[-2][0] < scored[-1][0] or scored[-2][1] == scored[-1][1])
                ):
                    fuzzy.append(f"{day.year} {plate} {label} -> {scored[-1][2]}")
                    found = {scored[-1][1]}
            if len(found) > 1:
                ambiguous.append(f"{day.year} {plate} {label}: {len(found)}")
                return None
            return next(iter(found), None)

        fuzzy: list[str] = []
        ambiguous: list[str] = []
        missing: list[str] = []
        lagged: list[str] = []
        units: dict[tuple[str, str, str], UUID] = {}
        rows = (
            frame.filter(pl.col("level") == "ilce")
            .select("year", "district", "name")
            .unique()
            .sort("year", "district", "name")
        )
        for year, district, label in rows.iter_rows():
            province = norm(re.sub(r"\s*\(\d+\)$", "", district))
            plate = plates[PROVINCE_ALIASES.get(province, province)]
            area = match(DAYS[year], plate, label)
            if area is None and (area := match(DAYS[year], plate, label, late=True)) is not None:
                lagged.append(f"{year} {district} {label}")
            if area is None:
                missing.append(f"{year} {district} {label}")
                continue
            units[(year, district, label)] = area
        print("fuzzy:", *fuzzy, sep="\n  ")
        print("lagged:", *lagged, sep="\n  ")
        print("ambiguous:", *ambiguous, sep="\n  ")
        if missing:
            raise ValueError("unmatched ilçes:\n  " + "\n  ".join(missing))

        existing = dict(
            conn.execute(
                "select area_id, id from reporting_unit where scheme_code = 'tr.ilce' and area_relation = 'equals'"
            ).fetchall()
        )
        unit_ids: dict[UUID, UUID] = {}
        for area in sorted(set(units.values())):
            if area in existing:
                unit_ids[area] = existing[area]
                continue
            text, _, end, plate = max(areas[area], key=lambda row: (row[2] is None, row[2] or date.min))
            label = title(text)
            code = f"{plate}:{label}"
            unit = cs.unit("tr.ilce", code)
            unit_ids[area] = unit
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=turkey,
                    scheme_code="tr.ilce",
                    code=code,
                    parent_id=None,
                    area_relation="equals",
                    area_id=area,
                    kind="aggregate",
                )
        contests = {
            key: (contest, election)
            for key, contest, election in conn.execute(
                """select x.value, c.id, c.election_id from external_id x join contest c on c.id = x.entity_id
                   where x.scheme = 'tr.contest' and x.value like any(%s)""",
                [[f"{day}:parliament%" for day in DAYS.values()]],
            ).fetchall()
        }
        options: dict[UUID, dict[str, UUID]] = {}
        for contest, option, kind, ballot, party in conn.execute(
            """select o.contest_id, o.id, o.kind, o.ballot_label, n.text from contest_option o
               left join name n on n.entity_id = o.party_id
               where o.contest_id = any(%s)""",
            [[contest for contest, _ in contests.values()]],
        ).fetchall():
            labels = options.setdefault(contest, {})
            if kind == "others":
                labels["BĞMZ"] = option
            for text in (ballot, party):
                if text is not None:
                    labels[norm(text)] = option
        frame = frame.filter(pl.col("level").is_in(["ilce", "district"])).with_columns(
            column=pl.col("column").replace(LABEL_ALIASES)
        )
        unmatched: set[str] = set()
        result_sets = 0
        for year, day in DAYS.items():
            held = frame.filter(pl.col("year") == year)
            national_key = f"{day}:parliament"
            groups: dict[str, list[str]] = {}
            for district in sorted(held["district"].unique()):
                number = re.search(r"\((\d+)\)$", district)
                province = norm(re.sub(r"\s*\(\d+\)$", "", district))
                plate = plates[PROVINCE_ALIASES.get(province, province)]
                key = f"{national_key}:{plate}" + (f"-{number.group(1)}" if number else "")
                if key in contests:
                    groups.setdefault(key, []).append(district)
                elif national_key not in contests:
                    raise ValueError(f"{year} {district}: no contest {key}")
            if national_key in contests:
                groups[national_key] = sorted(held["district"].unique())
            for contest_key, districts in groups.items():
                contest, election = contests[contest_key]
                rows = held.filter(pl.col("district").is_in(districts))
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                ilce = rows.filter(pl.col("level") == "ilce")
                declared = rows.filter(pl.col("level") == "district").group_by("column").agg(pl.col("value").sum())
                summed = ilce.group_by("column").agg(pl.col("value").sum())
                totals = dict(declared.iter_rows())
                sums = dict(summed.iter_rows())
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="alternative",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=False,
                    completeness=round(min(1, sums["valid"] / totals["valid"]), 4) if totals.get("valid") else None,
                )
                result_sets += 1
                off = [
                    f"{column} ({sums.get(column, 0):,} against {value:,})"
                    for column, value in sorted(totals.items())
                    if column != "boxes" and sums.get(column, 0) != value
                ]
                if off:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md="TÜİK's ilçe rows do not add up to its district totals for "
                        + ", ".join(off)
                        + ". The ilçe figures are stored as TÜİK prints them.",
                    )
                if contest_key == national_key and year in ("1983", "1987"):
                    cs.add("note", entity_id=result_set, kind="methodology", body_md=NATIONAL)
                if year == "2007":
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=CUSTOMS)
                labels = options.get(contest, {})
                seen: set[tuple[UUID, str]] = set()
                for district, label, column, value in ilce.select("district", "name", "column", "value").iter_rows():
                    if column == "boxes":
                        continue
                    unit = unit_ids[units[(year, district, label)]]
                    if (unit, column) in seen:
                        raise ValueError(f"{contest_key}: {district} {label} twice")
                    seen.add((unit, column))
                    measure = {"registered": REGISTERED, "voters": VOTERS, "valid": VALID}.get(column, VOTES)
                    option = None if measure != VOTES else labels.get(norm(column))
                    if measure == VOTES and option is None:
                        if value:
                            unmatched.add(f"{contest_key} {column}")
                        continue
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        if unmatched:
            raise ValueError("no option for:\n  " + "\n  ".join(sorted(unmatched)))
        print(
            f"{result_sets} result sets, {len(unit_ids)} ilçe units ({len(unit_ids) - len(existing.keys() & unit_ids.keys())} new)"
        )
        return cs.write()
