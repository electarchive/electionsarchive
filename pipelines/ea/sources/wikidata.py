import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "wikidata"
VERSION = "4"
TITLE = "Political parties and their colors"
POSITIONED = "Political positions of parties"
COUNTRIES = {
    "US": "Q30",
    "CA": "Q16",
    "TR": "Q43",
    "DE": "Q183",
    "BR": "Q155",
    "FR": "Q142",
    "IT": "Q38",
    "IE": "Q27",
    "CH": "Q39",
    "GB": "Q145",
    "EU": None,
}
LANGUAGES = '"en", "mul", "fr", "de", "it", "pt", "tr", "ga", "rm"'
NAMES = """SELECT DISTINCT ?item ?kind ?name WHERE {{
  {scope}
  {{ ?item rdfs:label ?name . BIND("label" AS ?kind) }}
  UNION {{ ?item skos:altLabel ?name . BIND("alias" AS ?kind) }}
  UNION {{ ?item wdt:P1813 ?name . BIND("short" AS ?kind) }}
  UNION {{ ?item wdt:P1448 ?name . BIND("label" AS ?kind) }}
  FILTER(LANG(?name) IN ({languages}))
}}"""
SPANS = """SELECT DISTINCT ?item ?inception ?dissolved ?links WHERE {{
  {scope}
  ?item wikibase:sitelinks ?links .
  OPTIONAL {{ ?item wdt:P571 ?inception }}
  OPTIONAL {{ ?item wdt:P576 ?dissolved }}
}}"""
COLORS = """SELECT DISTINCT ?item ?statement ?color ?rank ?start ?startPrecision ?end ?endPrecision WHERE {{
  {scope}
  ?item p:P465 ?statement . ?statement ps:P465 ?color ; wikibase:rank ?rank .
  OPTIONAL {{ ?statement pqv:P580 [ wikibase:timeValue ?start ; wikibase:timePrecision ?startPrecision ] }}
  OPTIONAL {{ ?statement pqv:P582 [ wikibase:timeValue ?end ; wikibase:timePrecision ?endPrecision ] }}
}}"""
POSITIONS = """SELECT DISTINCT ?item ?statement ?position ?rank WHERE {{
  {scope}
  ?item p:P1387 ?statement . ?statement ps:P1387 ?position ; wikibase:rank ?rank .
}}"""
STEPS = {
    "Q1129409": -3,
    "Q15141601": -3,
    "Q164597": -2,
    "Q737014": -1,
    "Q6587194": 0,
    "Q844072": 1,
    "Q76074": 2,
    "Q127869500": 3,
    "Q204481": 3,
}
WEIGHTS = {"label": 3, "short": 2, "alias": 1}
THRESHOLD = 4
SPLIT = r"\s[-–—]\s"
LINKED = ("party", "federation", "parliamentary_group", "independent_group")
MATCHES: dict[tuple[str, str], str | None] = {
    ("US", "Green Party"): "Q849158",
    ("US", "Reform Party"): "Q1893430",
    ("US", "Socialist Party USA"): "Q581259",
    ("US-NY", "Liberal"): "Q6540770",
    ("US-NY", "Right To Life"): "Q2513114",
    ("CA", "Conservative"): "Q909827",
    ("CA", "Progressive Conservative"): "Q1292617",
    ("CA", "Labour Progressive Party"): "Q3366569",
    ("CH-ZH", "AL – Alternative Liste"): "Q438524",
    ("IE", "People Before Profit–Solidarity"): "Q21218940",
    ("EU", "Europe of Sovereign Nations Group"): "Q127264685",
    ("EU", "Group of the European People's Party (Christian Democrats)"): "Q635616",
    ("EU", "Group of the Greens/European Free Alliance"): "Q751935",
    (
        "EU",
        "Group of the Progressive Alliance of Socialists and Democrats in the European Parliament",
    ): "Q507343",
    ("EU", "Identity and Democracy Group"): "Q64590677",
    ("EU", "Non-attached Members"): "Q1440626",
    ("EU", "Renew Europe Group"): "Q64590185",
    ("EU", "The Left group in the European Parliament - GUE/NGL"): "Q753711",
    ("FR", "DIV"): "Q97161383",
    ("FR", "DVC"): "Q3032227",
    ("FR", "DVD"): "Q1230558",
    ("FR", "DVG"): "Q1230566",
    ("FR", "DSV"): "Q113835267",
    ("FR", "ECO"): "Q127282650",
    ("FR", "EXD"): "Q127282704",
    ("FR", "EXG"): "Q127294071",
    ("FR", "REG"): "Q127282254",
    ("FR", "UG"): "Q3550075",
    ("FR", "UXD"): "Q127038776",
    ("IT", "Alleanza Verdi e Sinistra"): "Q113168799",
    ("IT", "Azione – Italia Viva"): "Q114246565",
    ("IT", "Italia Sovrana e Popolare"): "Q113490502",
    ("IT", "Unione Popolare"): None,
    ("TR", "Yeşiller ve Sol Gelecek Partisi"): "Q725173",
}
INHERITED: dict[tuple[str, str], str] = {
    ("FR", "ENS"): "Q109929254",
    ("FR", "UG"): "Q126487286",
    ("FR", "UXD"): "Q127038760",
    ("IT", "Impegno Civico"): "Q113375810",
    ("IT", "Partito Democratico – Italia Democratica e Progressista"): "Q47729",
    ("IT", "Südtiroler Volkspartei – PATT"): "Q606620",
    ("CH-ZH", "Partei der Arbeit"): "Q661771",
}
CHOSEN = {"Q47729": "EF1C27", "Q113168799": "56B45D"}
PAINTED: dict[tuple[str, str, int | None], str] = {
    ("BR", "Republicanos", None): "41A9D0",
    ("DE", "Die PARTEI", None): "A73A1A",
    ("DE", "FAMILIE", None): "FF987D",
    ("DE", "PdF", None): "C49209",
    ("DE", "Tierschutzpartei", None): "006D42",
    ("FR", "DVC", None): "FAAF5F",
    ("FR", "LR", None): "3D8FE0",
    ("FR", "SOC", None): "C62C57",
    ("FR", "UDI", None): "32B6FE",
    ("FR", "UXD", None): "5A3B5C",
    ("IT", "Azione – Italia Viva", None): "392CAA",
    ("IT", "Lega per Salvini Premier", None): "008000",
    ("TR", "Adalet Partisi", 1961): "D360BC",
    ("TR", "Cumhuriyetçi Güven Partisi", None): "FF4500",
    ("TR", "Cumhuriyetçi Millet Partisi", None): "7A1F2B",
    ("TR", "Demokrat Parti", None): "CD42B2",
    ("TR", "Demokratik Parti", None): "FFA500",
    ("TR", "Fazilet Partisi", None): "FF2E84",
    ("TR", "Güven Partisi", None): "FF4500",
    ("TR", "Halkçı Parti", None): "D40000",
    ("TR", "Milli Selamet Partisi", None): "FF2E84",
    ("TR", "Millet Partisi", 1950): "7A1F2B",
    ("TR", "Millet Partisi", 1965): "C65E1F",
    ("TR", "Milliyetçi Hareket Partisi", None): "7A1F2B",
    ("TR", "Refah Partisi", None): "FF2E84",
    ("TR", "Türkiye Birlik Partisi", None): "B7006D",
    ("TR", "Türkiye İşçi Partisi", None): "CD0668",
    ("TR", "Yeni Türkiye Partisi", 1961): "00B0F0",
    ("TR", "Yeniden Refah Partisi", None): "474647",
    ("US", "Natural Law Party", None): "D4A017",
    ("US", "Reform Party", None): "6A287E",
    ("US", "Socialist Workers Party", None): "AA0000",
    ("US-NY", "Conservative Party", None): "FF8C00",
    ("US-NY", "Independence Party", None): "8E7CC3",
    ("US-NY", "Right To Life", None): "8B0A50",
}
PLACED: dict[tuple[str, str], tuple[int, int]] = {
    ("BR", "Partido dos Trabalhadores"): (-2, -1),
    ("BR", "Progressistas"): (1, 1),
    ("BR", "Republicanos"): (2, 2),
    ("CA", "Canadian Alliance"): (1, 2),
    ("CA", "Conservative"): (2, 2),
    ("CA", "Green Party of Canada"): (-1, -1),
    ("CA", "Ralliement Créditiste"): (2, 2),
    ("CA", "Reform Party of Canada"): (2, 2),
    ("CA", "Social Credit Party of Canada"): (2, 2),
    ("CH", "Die Mitte"): (0, 1),
    ("CH", "Eidgenössisch-Demokratische Union"): (2, 3),
    ("CH", "Evangelische Volkspartei der Schweiz"): (0, 0),
    ("DE", "BSW"): (-2, -2),
    ("DE", "Die PARTEI"): (-2, -1),
    ("DE", "FAMILIE"): (2, 2),
    ("DE", "PdF"): (-1, -1),
    ("DE", "SSW"): (-1, -1),
    ("DE", "Tierschutzpartei"): (-2, -2),
    ("DE", "Volt"): (-1, 0),
    ("IT", "Azione – Italia Viva"): (0, 0),
    ("IT", "Movimento 5 Stelle"): (-1, -1),
    ("TR", "Adalet Partisi"): (1, 1),
    ("TR", "Cumhuriyetçi Millet Partisi"): (2, 2),
    ("TR", "Demokratik Parti"): (1, 1),
    ("TR", "Demokratik Sol Parti"): (-1, -1),
    ("TR", "Fazilet Partisi"): (3, 3),
    ("TR", "Güven Partisi"): (0, 1),
    ("TR", "Halkçı Parti"): (-1, -1),
    ("TR", "Hürriyet Partisi"): (0, 1),
    ("TR", "Milli Selamet Partisi"): (3, 3),
    ("TR", "Millet Partisi"): (2, 2),
    ("TR", "Milliyetçi Demokrasi Partisi"): (2, 2),
    ("TR", "Milliyetçi Hareket Partisi"): (2, 3),
    ("TR", "Sosyaldemokrat Halkçı Parti"): (-1, -1),
    ("TR", "Türkiye İşçi Partisi"): (-2, -2),
    ("TR", "Yeni Türkiye Partisi"): (1, 1),
    ("US", "Democratic-Republican Party"): (-2, -2),
    ("US", "Socialist Workers Party"): (-3, -3),
    ("US-MN", "Minnesota Democratic–Farmer–Labor Party"): (-1, -1),
}
ORDER = {
    "CH": ["GLP", "EVP"],
    "DE": [
        "Die Linke",
        "BSW",
        "Tierschutzpartei",
        "SPD",
        "GRÜNE",
        "SSW",
        "PdF",
        "FDP",
        "FREIE WÄHLER",
        "CDU",
        "ÖDP",
        "CSU",
    ],
    "IT": [
        "Partito Democratico – Italia Democratica e Progressista",
        "Movimento 5 Stelle",
        "Azione – Italia Viva",
        "Südtiroler Volkspartei – PATT",
    ],
    "TR": [
        "Cumhuriyet Halk Partisi",
        "Sosyaldemokrat Halkçı Parti",
        "Demokratik Sol Parti",
        "Yeni Türkiye Partisi",
        "Adalet Partisi",
        "Demokratik Parti",
        "Doğru Yol Partisi",
        "Anavatan Partisi",
    ],
}


def _scope(country: str | None) -> str:
    if country is None:
        return "?item wdt:P31 wd:Q779079 ."
    return f"?item wdt:P31/wdt:P279* wd:Q7278 ; wdt:P17 wd:{country} ."


def _url(template: str, scope: str) -> str:
    return "https://query.wikidata.org/sparql?" + urlencode(
        {"query": template.format(scope=scope, languages=LANGUAGES), "format": "json"}
    )


def _extra(template: str) -> str:
    items = sorted({*(item for item in MATCHES.values() if item is not None), *INHERITED.values()})
    return _url(template, "VALUES ?item { " + " ".join(f"wd:{item}" for item in items) + " }")


def _urls() -> list[str]:
    return [
        _url(template, _scope(country)) for country in COUNTRIES.values() for template in (NAMES, SPANS, COLORS)
    ] + [_extra(COLORS)]


def _placed() -> list[str]:
    return [_url(POSITIONS, _scope(country)) for country in COUNTRIES.values()] + [_extra(POSITIONS)]


def _rows(url: str) -> list[dict[str, str]]:
    return [
        {key: value["value"] for key, value in binding.items()}
        for binding in json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))["results"]["bindings"]
    ]


def _norm(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.replace("ı", "i").replace("İ", "i").lower().replace("&", " and "))
    return " ".join(re.findall(r"[a-z0-9]+", "".join(c for c in folded if not unicodedata.combining(c))))


def _year(value: str | None) -> int | None:
    return int(value[:4]) if value is not None and re.match(r"\d{4}-", value) else None


def _day(value: str | None, precision: str | None) -> tuple[date | None, str]:
    if value is None or not re.match(r"\d{4}-\d\d-\d\d", value):
        return None, "day"
    kind = {"11": "day", "10": "month"}.get(precision or "", "year")
    day = date.fromisoformat(value[:10])
    return day.replace(day=1) if kind == "month" else day.replace(month=1, day=1) if kind == "year" else day, kind


def fetch() -> None:
    for url in _urls() + _placed():
        raw.fetch(SOURCE, url, suffix=".json")


def emit() -> Path:
    index: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(dict))
    spans: dict[str, tuple[int | None, int | None, int]] = {}
    statements: dict[str, list[dict[str, str]]] = defaultdict(list)
    stances: dict[str, list[dict[str, str]]] = defaultdict(list)
    for scope, country in COUNTRIES.items():
        for row in _rows(_url(NAMES, _scope(country))):
            item = row["item"].rsplit("/", 1)[1]
            parts = re.split(SPLIT, row["name"])
            weighted = [(row["name"], WEIGHTS[row["kind"]])]
            if len(parts) > 1:
                weighted += [(part, 2 if row["kind"] == "label" else 1) for part in parts]
            for text, weight in weighted:
                key = _norm(text)
                if key:
                    index[scope][key][item] = max(index[scope][key].get(item, 0), weight)
        for row in _rows(_url(SPANS, _scope(country))):
            item = row["item"].rsplit("/", 1)[1]
            began, ended, _ = spans.get(item, (None, None, 0))
            spans[item] = (
                min(filter(None, (began, _year(row.get("inception")))), default=None),
                max(filter(None, (ended, _year(row.get("dissolved")))), default=None),
                int(row["links"]),
            )
    seen: set[str] = set()
    for url in [_url(COLORS, _scope(country)) for country in COUNTRIES.values()] + [_extra(COLORS)] + _placed():
        for row in _rows(url):
            if row["statement"] not in seen:
                seen.add(row["statement"])
                (stances if "position" in row else statements)[row["item"].rsplit("/", 1)[1]].append(row)

    def spectrum(item: str | None) -> tuple[int, int] | None:
        rows = [
            row
            for row in stances.get(item or "", [])
            if not row["rank"].endswith("DeprecatedRank") and row["position"].rsplit("/", 1)[1] in STEPS
        ]
        steps = [
            STEPS[row["position"].rsplit("/", 1)[1]]
            for row in [row for row in rows if row["rank"].endswith("PreferredRank")] or rows
        ]
        return (min(steps), max(steps)) if steps else None

    def colors(item: str | None) -> list[tuple[str, date | None, str, date | None, str]]:
        if item is None:
            return []
        rows = [
            row
            for row in statements.get(item, [])
            if not row["rank"].endswith("DeprecatedRank") and re.fullmatch(r"[0-9A-Fa-f]{6}", row["color"])
        ]
        rows = (
            [row for row in rows if row["color"].upper() == CHOSEN[item]]
            if item in CHOSEN
            else [row for row in rows if row["rank"].endswith("PreferredRank")] or rows
        )
        dated = [row for row in rows if "start" in row or "end" in row]
        if not dated:
            return (
                []
                if len({row["color"].lower() for row in rows}) != 1
                else [(rows[0]["color"], None, "day", None, "day")]
            )
        ranges: list[tuple[str, date | None, str, date | None, str]] = []
        for row in sorted(dated, key=lambda row: row.get("start", "")):
            start, start_precision = _day(row.get("start"), row.get("startPrecision"))
            end, end_precision = _day(row.get("end"), row.get("endPrecision"))
            last = ranges[-1][3] if ranges else None
            if (ranges and (last is None or start is None or start < last)) or (start and end and end <= start):
                continue
            ranges.append((row["color"], start, start_precision, end, end_precision))
        return ranges

    with connect() as conn:
        parties: list[tuple[UUID, str, str, list[str], list[str], int | None, int | None, int]] = conn.execute(
            """select p.id, po.iso_code, p.kind,
                      coalesce((select array_agg(n.text order by n.is_preferred desc, n.text) from name n
                                where n.entity_id = p.id and n.kind <> 'abbreviation'), '{}'),
                      coalesce((select array_agg(n.text order by n.text) from name n
                                where n.entity_id = p.id and n.kind = 'abbreviation'), '{}'),
                      s.first, s.last, s.options
               from party p
               join polity po on po.id = p.polity_id
               cross join lateral (
                 select min(extract(year from e.date_from))::int, max(extract(year from e.date_from))::int, count(*)::int
                 from contest_option o join contest k on k.id = o.contest_id join election e on e.id = k.election_id
                 where o.party_id = p.id
               ) s(first, last, options)
               where not exists (select from entity_merge m where m.entity_id = p.id)
               order by p.id"""
        ).fetchall()
        states: dict[str, str] = dict(
            conn.execute(
                """select po.iso_code, n.text from polity po
                   join name n on n.entity_id = po.id and n.is_preferred and n.lang = 'en'
                   where po.iso_code like 'US-%'"""
            ).fetchall()
        )
        painted: set[UUID] = {
            party
            for (party,) in conn.execute(
                """select distinct pc.party_id from party_color pc join changeset c on c.id = pc.changeset_id
                   where c.pipeline <> %s""",
                [SOURCE],
            ).fetchall()
        }
        taken: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'wikidata'").fetchall()
        )

        owned = {entity: item for item, entity in taken.items()}
        found: dict[UUID, tuple[str | None, str | None, int]] = {}
        claims: dict[str, list[tuple[int, int, UUID]]] = defaultdict(list)
        for party, iso, kind, full, short, first, last, options in parties:
            scope = iso.split("-")[0]
            if scope not in COUNTRIES:
                continue
            forced = next((name for name in full + short if (iso, name) in MATCHES), None)
            inherited = next((INHERITED[iso, name] for name in full + short if (iso, name) in INHERITED), None)
            if forced is not None:
                item = MATCHES[iso, forced]
                found[party] = (item, inherited, options)
                if item is not None:
                    claims[item].append((1_000, options, party))
                continue
            if party in owned:
                found[party] = (owned[party], inherited, options)
                claims[owned[party]].append((1_000, options, party))
                continue
            scores: Counter[str] = Counter()
            for text in full + short:
                variants = {text, f"{text} party"}
                if text in full and kind in LINKED:
                    variants.add(re.split(SPLIT, text)[0])
                if iso in states:
                    variants |= {f"{states[iso]} {text}", f"{text} of {states[iso]}", f"{text} of {states[iso]} State"}
                hits: dict[str, int] = {}
                for variant in variants:
                    for item, weight in index[scope].get(_norm(variant), {}).items():
                        hits[item] = max(hits.get(item, 0), weight)
                for item, weight in hits.items():
                    scores[item] += weight * (1 if text in short else 2)
            ranked: list[tuple[int, int, str]] = []
            for item, score in scores.items():
                began, ended, links = spans.get(item, (None, None, 0))
                if (
                    first is None
                    or last is None
                    or ((began is None or began <= last + 1) and (ended is None or ended >= first - 1))
                ):
                    ranked.append((score, links, item))
            ranked.sort(reverse=True)
            if not ranked or ranked[0][0] < THRESHOLD:
                found[party] = (None, inherited, options)
                continue
            score, links, item = ranked[0]
            if len(ranked) > 1 and score == ranked[1][0] and links < 2 * ranked[1][1] + 2:
                found[party] = (None, inherited, options)
                continue
            if kind in LINKED:
                found[party] = (item, inherited, options)
                claims[item].append((score, options, party))
            else:
                found[party] = (None, inherited or item, options)

        linked: dict[UUID, str] = {}
        for item, claimants in claims.items():
            owner = taken.get(item)
            winner = max(claimants)[2]
            if owner is None or owner == winner:
                linked[winner] = item
            for _, _, party in claimants:
                if party not in linked and found[party][1] is None:
                    found[party] = (found[party][0], item, found[party][2])

        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Parties matched to Wikidata items by name within each country and period, with each item's colors "
                "for parties that have no color from another source and each item's political position, placed by "
                "hand for seat winners Wikidata leaves blank"
            ),
            polity=None,
        )
        wikidata = cs.source("Wikidata", TITLE)
        cs.add(
            "source",
            id=wikidata,
            kind="wikidata",
            publisher="Wikidata",
            title=TITLE,
            url="https://www.wikidata.org/wiki/Property:P465",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        positioned = cs.source("Wikidata", POSITIONED)
        cs.add(
            "source",
            id=positioned,
            kind="wikidata",
            publisher="Wikidata",
            title=POSITIONED,
            url="https://www.wikidata.org/wiki/Property:P1387",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        for url in _urls():
            cs.artifact(raw.cached(SOURCE, url), wikidata)
        for url in _placed():
            cs.artifact(raw.cached(SOURCE, url), positioned)
        report: Counter[tuple[str, str]] = Counter()
        shades: defaultdict[UUID, list[tuple[str, date | None, date | None]]] = defaultdict(list)
        for party, iso, kind, full, short, first, last, options in parties:
            if party not in found:
                continue
            scope = "US-*" if iso.startswith("US-") else iso
            report[scope, "parties"] += 1
            report[scope, "options"] += options
            item, inherited, _ = found[party]
            if party in linked and taken.get(linked[party]) != party:
                cs.add("external_id", entity_id=party, scheme="wikidata", value=linked[party])
            if party in linked:
                report[scope, "linked"] += 1
            sourced = spectrum(linked.get(party)) or spectrum(inherited)
            span = sourced or next((PLACED[iso, name] for name in full + short if (iso, name) in PLACED), None)
            if span is not None:
                cs.add(
                    "party_position",
                    party_id=party,
                    low=span[0],
                    high=span[1],
                    rank=min(
                        (ORDER[iso].index(name) for name in full + short if name in ORDER.get(iso, [])), default=None
                    ),
                    source_id=None if sourced is None else positioned,
                )
                report[scope, "positioned"] += options
            if party in painted:
                report[scope, "painted"] += options
                continue
            hand = next(
                (
                    PAINTED[key]
                    for name in full + short
                    for key in ((iso, name, first), (iso, name, None))
                    if key in PAINTED
                ),
                None,
            )
            ranges = [(hand, None, "day", None, "day")] if hand else colors(linked.get(party)) or colors(inherited)
            for color, start, start_precision, end, end_precision in ranges:
                cs.add(
                    "party_color",
                    party_id=party,
                    color=f"#{color.lower()}",
                    valid_from=start,
                    valid_from_precision=start_precision,
                    valid_to=end,
                    valid_to_precision=end_precision,
                    source_id=None if hand else wikidata,
                )
                shades[party].append((f"#{color.lower()}", start, end))
            if ranges:
                report[scope, "painted"] += options
            elif options >= 50:
                print(f"no color: {iso} {(full + short)[0]} ({options} options, item {linked.get(party) or inherited})")
        for scope in sorted({scope for scope, _ in report}):
            print(
                f"{scope}: {report[scope, 'linked']} of {report[scope, 'parties']} parties linked, "
                f"{report[scope, 'painted']} of {report[scope, 'options']} options colored, "
                f"{report[scope, 'positioned']} positioned"
            )
        for party, color, start, end in conn.execute(
            """select pc.party_id, pc.color, pc.valid_from, pc.valid_to from party_color pc
               join changeset c on c.id = pc.changeset_id where c.pipeline <> %s""",
            [SOURCE],
        ).fetchall():
            shades[party].append((color, start, end))
        lab: dict[str, tuple[float, float, float]] = {}
        for color in {color for ranges in shades.values() for color, _, _ in ranges}:
            rgb = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
            r, g, b = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
            l_, m_, s_ = (
                (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3),
                (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3),
                (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3),
            )
            lab[color] = (
                min(0.86, max(0.52, 0.2104542553 * l_ + 0.793617785 * m_ - 0.0040720468 * s_)),
                1.9779984951 * l_ - 2.428592205 * m_ + 0.4505937099 * s_,
                0.0259040371 * l_ + 0.7827717662 * m_ - 0.808675766 * s_,
            )
        labels: dict[UUID, str] = {party: (full + short)[0] for party, _, _, full, short, *_ in parties}
        seated: defaultdict[tuple[str, date], dict[UUID, str | None]] = defaultdict(dict)
        for iso, day, party, principal in conn.execute(
            """select distinct coalesce(po.iso_code, (select n.text from name n where n.entity_id = po.id
                                                 order by n.is_preferred desc limit 1)),
                      e.date_from, coalesce(m.into_id, o.party_id),
                      (select coalesce(tm.into_id, r.to_party_id) from party_relation r
                       left join entity_merge fm on fm.entity_id = r.from_party_id
                       left join entity_merge tm on tm.entity_id = r.to_party_id
                       where r.kind = 'list_vehicle_for' and r.election_id = e.id
                         and coalesce(fm.into_id, r.from_party_id) = coalesce(m.into_id, o.party_id) limit 1)
               from outcome x join contest_option o on o.id = x.option_id
               join contest k on k.id = o.contest_id join election e on e.id = k.election_id
               join polity po on po.id = e.polity_id
               left join entity_merge m on m.entity_id = o.party_id
               where x.kind::text in ('elected', 'won') and o.party_id is not null and e.date_from is not null"""
        ).fetchall():
            shade = next(
                (
                    color
                    for owner in (party, principal)
                    for color, start, end in shades.get(owner, [])
                    if (start is None or start <= day) and (end is None or day < end)
                ),
                None,
            )
            seated[iso, day][party] = shade
        for iso, day in sorted(seated):
            winners = sorted(seated[iso, day].items(), key=lambda item: labels.get(item[0], ""))
            for place, (one, color) in enumerate(winners):
                for other, shade in winners[place + 1 :]:
                    if color and shade and (gap := math.dist(lab[color], lab[shade])) < 0.1:
                        print(f"close: {iso} {day} {labels.get(one)} {color} ~ {labels.get(other)} {shade} ({gap:.3f})")
            if bare := [labels.get(party, str(party)) for party, color in winners if color is None]:
                print(f"no color: {iso} {day} seat winners {', '.join(bare)}")
        return cs.write()
