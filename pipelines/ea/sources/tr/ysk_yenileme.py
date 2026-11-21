"""The reruns of annulled local races that YSK held on June 1, 2014, by ballot box from its Sandık Sonuçları service.

The service lists a general election's reruns under the general election's id (getYenilemeSecimList) and gives each
rerun its own election id, which every other call takes in the same encoded form the site sends (three times the id,
plus 102). For each rerun the service gives the same box rows as for the general election: each ballot box's
registered voters, voters, valid and invalid votes, and the votes for each party's candidate or list and for the
independents."""

import gzip
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import NamedTuple
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.tr.ysk import INDEPENDENTS, INVALID, LICENSE, PUBLISHER, REGISTERED, VALID, VOTERS, VOTES, title
from ea.sources.tr.ysk_il_meclisi import TURKEY
from ea.sources.tr.ysk_mayor_boxes_2014 import ANNULLED
from ea.sources.tr.ysk_meclis import EMPTY, PARTIES, SITE
from ea.sources.tr.ysk_muhtarlik import API, HEADERS, get
from ea.store import connect

SOURCE = "tr.ysk_yenileme"
DAY = date(2014, 6, 1)
MARCH = date(2014, 3, 30)
KEY = f"{SOURCE}/{DAY}"
GENERAL = 11979  # the service's id of the local elections of March 30, 2014
MAYOR, COUNCIL = 2, 3


class Rerun(NamedTuple):
    secim: int  # the rerun's election id in the rerun list
    il: int  # the service's province, district, and belde ids of the place
    ilce: int
    belde: int = 0
    races: tuple[int, ...] = (MAYOR,)


# The reruns of the races that YSK annulled, as the contest key of the place. Ağrı and Yeşilyurt reran their mayoral
# and council races; their provincial council reruns are left to the provincial council pipeline. The other reruns
# that day are mayoral races alone, four of them in a belde.
RERUNS = {
    "agri": Rerun(12921, 4, 11, races=(MAYOR, COUNCIL)),
    "tokat-yesilyurt": Rerun(12897, 60, 751, races=(MAYOR, COUNCIL)),
    "aydin-buharkent": Rerun(13059, 9, 674),
    "bitlis-guroymak": Rerun(12846, 13, 691),
    "cankiri-sabanozu": Rerun(12871, 18, 542),
    "corum-ortakoy-astavul": Rerun(12773, 19, 449, 4747),
    "erzincan-tercan-cadirkaya": Rerun(12908, 24, 568, 4895),
    "eskisehir-mahmudiye": Rerun(12939, 26, 401),
    "kastamonu-catalzeytin": Rerun(12923, 37, 136),
    "nigde-merkez-baglama": Rerun(12922, 51, 437, 3626),
    "yozgat-sorgun-eymir": Rerun(13018, 66, 528, 4010),
    "bayburt-aydintepe": Rerun(12866, 69, 660),
    "yalova": Rerun(13060, 77, 609),
}
TITLE = "Sandık Sonuçları: 1 Haziran 2014 yenileme seçimleri"
NOTE = (
    "Results for each ballot box of the reruns YSK held on June 1, 2014, read from the results service of YSK's "
    "Sandık Sonuçları site. The service lists each rerun of the local elections of March 30, 2014 with its own "
    "election id. For each rerun race, it gives each ballot box's registered voters, voters, valid and invalid votes, "
    "the votes for each party's candidate or list, and the votes for the independents, by name where the service "
    "names them. It gives no outcome and no seats won."
)
METHOD = (
    "Counted for each ballot box by YSK and read from the results service of its Sandık Sonuçları site, which lists "
    "this race as a rerun of the annulled vote of March 30, 2014. Each box of the rerun is its own unit, dated June "
    "1, 2014, inside the neighborhood the service places it in. A party's votes go to its {what}; an independent "
    "the service names is their own option, and any other independents' votes are one option. The boxes add up to "
    "the totals the service gives for the {place}."
)
# The service prints the rerun's BDP candidates and lists under the name the party's local organization took that
# year, Demokratik Bölgeler Partisi; the archive holds both names on one party.
LABELS = PARTIES | {"DBP": "baris-ve-demokrasi-partisi"}
Row = dict[str, object]


def encoded(secim: int) -> int:
    """The id the site sends for an election: three times the service's id, plus 102."""
    return secim * 3 + 102


def rerun_list_url() -> str:
    return f"{API}getYenilemeSecimList?{urlencode({'secimId': encoded(GENERAL)})}"


def bundle_url(secim: int) -> str:
    return f"{API}getSecimSandikSonucList?{urlencode({'secimId': encoded(secim), 'yurtIciDisi': 1})}"


def results(il: int, ilce: int | str) -> dict[str, int | str]:
    return {
        "ilId": il,
        "ilceId": ilce,
        "beldeId": "",
        "sandikTuru": -1,
        "yurtIciDisi": 1,
        "secimCevresiId": "",
        **dict.fromkeys(EMPTY, ""),
    }


def crawl(client: httpx.Client, secim: int, il: int, races: tuple[int, ...]) -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []

    def call(path: str, query: dict[str, int | str]) -> list[dict[str, int | str]]:
        rows = get(client, path, query)
        out.append((f"{API}{path}?{urlencode(query)}", rows))
        return rows

    for turu in races:
        base: dict[str, int | str] = {"secimId": encoded(secim), "secimTuru": turu}
        call("getIlList", base | {"sandikTuru": -1, "yurtIciDisi": 1})
        places = call("getIlceList", base | {"ilId": il, "secimCevresiId": "", "sandikTuru": -1, "yurtIciDisi": 1})
        call(
            "getSandikSecimSonucBaslikList", base | {"secimCevresiId": "", "ilId": il, "bagimsiz": 1, "yurtIciDisi": 1}
        )
        for cevre in sorted({int(str(place["secim_CEVRESI_ID"])) for place in places}):
            call(
                "getSandikSecimSonucBaslikList",
                base | {"secimCevresiId": cevre, "ilId": il, "bagimsiz": 1, "yurtIciDisi": 1},
            )
        call("getSecimSandikSonucList", base | results(il, "") | {"sorguTuru": 1})
        for ilce in sorted({int(str(place["ilce_ID"])) for place in places}):
            call("getSecimSandikSonucList", base | results(il, ilce) | {"sorguTuru": 1})
    return out


def fetch() -> None:
    """The service answers a script only with a browser's User-Agent and the site as Referer, as the other YSK
    pipelines send; each rerun's responses are stored as one file, and the rerun list as another."""
    have = raw.keys(SOURCE)
    with httpx.Client(headers=HEADERS) as client:
        if rerun_list_url() not in have:
            body = get(client, "getYenilemeSecimList", {"secimId": encoded(GENERAL)})
            raw.store(
                SOURCE,
                rerun_list_url(),
                json.dumps(body, ensure_ascii=False).encode(),
                suffix=".json",
                retrieved_at=datetime.now(UTC),
                media_type="application/json",
            )
        for rerun in RERUNS.values():
            url = bundle_url(rerun.secim)
            if url in have:
                continue
            lines = "".join(
                json.dumps({"url": request, "response": response}, ensure_ascii=False) + "\n"
                for request, response in crawl(client, rerun.secim, rerun.il, rerun.races)
            )
            raw.store(
                SOURCE,
                url,
                gzip.compress(lines.encode(), mtime=0),
                suffix=".jsonl.gz",
                retrieved_at=datetime.now(UTC),
                media_type="application/gzip",
            )


@dataclass
class Race:
    seats: int = 0
    parties: dict[str, str] = field(default_factory=dict)  # column, then the party label the service prints
    named: dict[str, str] = field(default_factory=dict)  # column, then the independent candidate the service names
    total: Row = field(default_factory=dict)  # the service's total for the municipality
    boxes: list[Row] = field(default_factory=list)


def num(value: object) -> int:
    return int(str(value or 0))


def read(rerun: Rerun) -> dict[int, Race]:
    """Each race of one rerun, by the service's race type."""
    secim, place = rerun.secim, (rerun.il, rerun.ilce, rerun.belde)
    found: dict[int, Race] = {}
    with gzip.open(raw.cached(SOURCE, bundle_url(secim)).path, "rt") as file:
        for line in file:
            data = json.loads(line)
            parsed = urlparse(data["url"])
            query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
            if query["secimId"] != str(encoded(secim)):
                raise ValueError(f"{data['url']} is not election {secim}")
            race = found.setdefault(int(query["secimTuru"]), Race())
            response = data["response"]
            match parsed.path.rsplit("/", 1)[1]:
                case "getIlceList":
                    if [(num(r["il_ID"]), num(r["ilce_ID"]), num(r["belde_ID"])) for r in response] != [place]:
                        raise ValueError(f"election {secim}: unexpected places {response}")
                    race.seats = num(response[0]["secilecek_ADAY_SAYISI"])
                case "getSandikSecimSonucBaslikList":
                    labels = {str(c["column_NAME"]): str(c["ad"]) for c in response}
                    if query.get("secimCevresiId"):
                        race.named = {c: v for c, v in labels.items() if c.startswith("bagimsiz") and c[8].isdigit()}
                    else:
                        race.parties = {c: v for c, v in labels.items() if c.startswith("parti")}
                case "getSecimSandikSonucList":
                    for row in response:
                        if num(row["secim_ID"]) != secim:
                            raise ValueError(f"{data['url']}: a row of election {row['secim_ID']}")
                    if query.get("ilceId"):
                        race.boxes += response
                    else:
                        (race.total,) = response
    if set(found) != set(rerun.races) or not all(race.seats and race.total and race.boxes for race in found.values()):
        raise ValueError(f"election {secim}: races {sorted(found)} incomplete")
    return found


class Reference(NamedTuple):
    turkey: str
    march: dict[str, Row]  # each place's March 30 election and contest
    parties: dict[str, tuple[str, str]]  # the party's key, then its id and name on the rerun's day
    areas: dict[int, str]  # the service's neighborhood id, then the archive's area
    mayoral_system: str


def reference() -> Reference:
    """The rows the package attaches to, read from the archive."""
    with connect() as conn:
        march = {}
        for key in RERUNS:
            row = conn.execute(
                """select e.id::text election, e.polity_id::text polity, e.status, s.path,
                          c.office_id::text office, c.district_area_id::text area, n.text english
                   from external_id x join election e on e.id = x.entity_id
                   join contest c on c.election_id = e.id
                   join slug s on s.entity_id = e.id and s.is_current
                   join name n on n.entity_id = c.id and n.lang = 'en' and n.kind = 'official'
                   where x.scheme = 'tr.election' and x.value = %s and c.kind = 'office'""",
                [f"{MARCH}:{key}-mayor"],
            ).fetchall()
            # The March 30 race is annulled in the archive, or becomes so when its package loads with this one.
            if len(row) != 1 or key not in ANNULLED or row[0][2] not in ("annulled", "held"):
                raise ValueError(f"{key}: the archive holds {row} for March 30")
            march[key] = dict(zip(("election", "polity", "status", "path", "office", "area", "english"), row[0]))
        parties = {
            value: (entity, name)
            for value, entity, name in conn.execute(
                """select x.value, x.entity_id::text,
                          (select text from name n where n.entity_id = x.entity_id and n.kind = 'official'
                           and (n.valid_from is null or n.valid_from <= %s)
                           order by n.valid_from desc nulls last limit 1)
                   from external_id x where x.scheme = 'tr.party'""",
                [DAY],
            ).fetchall()
        }
        areas = {
            int(value): entity
            for value, entity in conn.execute(
                "select value, entity_id::text from external_id where scheme = 'tr.ysk.muhtarlik.2014'"
            ).fetchall()
        }
        systems = Counter(
            system
            for (system,) in conn.execute(
                """select c.electoral_system_id::text from external_id x join contest c on c.id = x.entity_id
                   where x.scheme = 'tr.contest' and x.value like %s and c.electoral_system_id is not null""",
                [f"{MARCH}:%-mayor"],
            ).fetchall()
        )
    return Reference(TURKEY, march, parties, areas, systems.most_common(1)[0][0])


def build() -> Package:
    ref = reference()
    p = Package(
        KEY,
        SOURCE,
        f"Turkey's June 1, 2014 reruns of the annulled mayoral races of {len(RERUNS)} municipalities, and the council "
        "races of Ağrı and Tokat Yeşilyurt, by ballot box, from YSK's Sandık Sonuçları",
    )
    source = p.id("source", TITLE)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=SITE,
        license=LICENSE,
        notes=NOTE,
    )
    listed = json.loads(raw.cached(SOURCE, rerun_list_url()).path.read_text())
    p.artifact(raw.cached(SOURCE, rerun_list_url()), source)
    external: set[str] = {ref.turkey, "tr.sandik", ref.mayoral_system}

    umbrella = p.id("election", f"{DAY}:local-rerun")
    p.add(
        "election",
        id=umbrella,
        polity_id=ref.turkey,
        kind="local",
        date_from=DAY,
        date_to=DAY,
        status="held",
    )
    p.name(umbrella, "2014 Turkish local election reruns", "en", "official")
    p.name(umbrella, "1 Haziran 2014 Yenileme Seçimleri", "tr", "native", False)
    p.add("slug", path=f"tr/{DAY}-local-rerun", entity_id=umbrella, is_current=True)
    p.add("external_id", entity_id=umbrella, scheme="tr.election", value=f"{DAY}:local-rerun")

    units: dict[str, str] = {}
    unit_rows: list[Row] = []
    values: list[tuple[str, str, str | None, int, int]] = []
    summary = []
    for key, rerun in RERUNS.items():
        il, ilce, belde = rerun.il, rerun.ilce, rerun.belde
        entries = [r for r in listed if num(r["secim_ID"]) == rerun.secim]
        if {num(r["secim_TURU_KODU"]) for r in entries} < set(rerun.races) or {
            r["secim_TARIHI"][:10] for r in entries
        } != {str(DAY)}:
            raise ValueError(f"{key}: the rerun list gives {entries}")
        races = read(rerun)
        p.artifact(raw.cached(SOURCE, bundle_url(rerun.secim)), source)
        march = ref.march[key]
        english = str(march["english"]).removeprefix("Mayor of ")
        path = str(march["path"]).replace(f"/{MARCH}-local", f"/{DAY}-local")
        external |= {str(march["election"]), str(march["polity"]), str(march["office"]), str(march["area"])}
        election = p.id("election", f"{DAY}:{key}-mayor")
        p.add(
            "election",
            id=election,
            polity_id=march["polity"],
            parent_id=umbrella,
            kind="local",
            date_from=DAY,
            date_to=DAY,
            status="held",
            rerun_of_id=march["election"],
        )
        if COUNCIL in rerun.races:
            p.name(election, f"{DAY.year} {english} local election rerun", "en", "official")
            p.name(
                election, f"{english} Belediye Başkanlığı ve Belediye Meclisi Yenileme Seçimi", "tr", "native", False
            )
        else:
            p.name(election, f"{DAY.year} {english} mayoral election rerun", "en", "official")
            p.name(election, f"{english} Belediye Başkanlığı Yenileme Seçimi", "tr", "native", False)
        p.add("slug", path=path, entity_id=election, is_current=True)
        p.add("external_id", entity_id=election, scheme="tr.election", value=f"{DAY}:{key}-mayor")

        for turu, race in sorted(races.items()):
            mayoral = turu == MAYOR
            contest_key = f"{DAY}:{key}-{'mayor' if mayoral else 'council'}"
            contest = p.id("contest", contest_key)
            p.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office" if mayoral else "body_seats",
                office_id=march["office"] if mayoral else None,
                district_area_id=march["area"],
                magnitude=1 if mayoral else race.seats,
                round_number=1,
                electoral_system_id=ref.mayoral_system if mayoral else None,
                default_result_kind="counted",
            )
            if mayoral:
                if race.seats != 1:
                    raise ValueError(f"{key}: {race.seats} mayors")
                p.name(contest, f"Mayor of {english}", "en", "official")
                p.add("slug", path=f"{path}/mayor", entity_id=contest, is_current=True)
            else:
                p.name(contest, f"{english} municipal council", "en", "official")
                p.name(contest, f"{english} Belediye Meclisi Üyeliği", "tr", "native", False)
                p.add("slug", path=f"{path}/municipal-council", entity_id=contest, is_current=True)
            p.add("external_id", entity_id=contest, scheme="tr.contest", value=contest_key)
            result_set = p.id("result_set", contest_key)
            p.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            p.add(
                "note",
                id=p.id("note", result_set, "method"),
                entity_id=result_set,
                kind="methodology",
                body_md=METHOD.format(
                    what="candidate" if mayoral else "list", place="municipality" if mayoral else "council"
                ),
                lang="en",
            )
            options: dict[str, str] = {}

            def option(column: str | None, race: Race = race, contest: str = contest, mayoral: bool = mayoral) -> str:
                if column is None:
                    slot, kind, party, label = "independents", "independents", None, INDEPENDENTS
                elif column.startswith("parti"):
                    owner = LABELS[race.parties[column]]
                    party, label = ref.parties[owner]
                    slot, kind = owner, "candidate" if mayoral else "list"
                    external.add(party)
                else:
                    slot, kind, party, label = column.removesuffix("_ALDIGI_OY"), "candidate", None, None
                    label = title(race.named[column])
                if slot not in options:
                    options[slot] = p.id("option", contest, slot)
                    p.add(
                        "contest_option",
                        id=options[slot],
                        contest_id=contest,
                        kind=kind,
                        party_id=party,
                        ballot_label=label,
                    )
                return options[slot]

            sums: Counter[str] = Counter()
            for row in sorted(race.boxes, key=lambda r: num(r["sandik_ID"])):
                if (num(row["il_ID"]), num(row["ilce_ID"]), num(row["belde_ID"])) != (il, ilce, belde):
                    raise ValueError(f"{key}: a box outside the municipality: {row['sandik_ID']}")
                code = f"{DAY}:{il:02d}:{ilce}:{row['sandik_NO']}"
                if code not in units:
                    units[code] = p.id("unit", code)
                    area = None if num(row.get("cezaevi_ID")) else ref.areas.get(num(row.get("muhtarlik_ID")))
                    area = area or str(march["area"])
                    external.add(area)
                    unit_rows.append(
                        {
                            "id": units[code],
                            "polity_id": ref.turkey,
                            "scheme_code": "tr.sandik",
                            "code": code,
                            "area_relation": "within",
                            "area_id": area,
                            "kind": "standard",
                            "valid_from": DAY,
                            "valid_to": DAY + timedelta(days=1),
                        }
                    )
                unit = units[code]
                for measure, column in (
                    (REGISTERED, "secmen_SAYISI"),
                    (VOTERS, "oy_KULLANAN_SECMEN_SAYISI"),
                    (VALID, "gecerli_OY_TOPLAMI"),
                    (INVALID, "gecersiz_OY_TOPLAMI"),
                ):
                    values.append((result_set, unit, None, measure, num(row.get(column))))
                counted = named = 0
                for column, value in row.items():
                    if column.endswith("_ALDIGI_OY") and num(value):
                        sums[column] += num(value)
                        if column.startswith("parti"):
                            values.append((result_set, unit, option(column), VOTES, num(value)))
                            counted += num(value)
                        elif column.startswith("bagimsiz") and column in race.named:
                            values.append((result_set, unit, option(column), VOTES, num(value)))
                            named += num(value)
                        elif not column.startswith("bagimsiz"):
                            raise ValueError(f"{code}: unexpected column {column}")
                pooled = num(row.get("bagimsiz_TOPLAM_OY")) - named
                if pooled < 0:
                    raise ValueError(f"{code}: named independents exceed the total")
                if pooled:
                    values.append((result_set, unit, option(None), VOTES, pooled))
                counted += named + pooled
                if counted != num(row.get("gecerli_OY_TOPLAMI")):
                    raise ValueError(f"{code}: votes {counted} against valid {row.get('gecerli_OY_TOPLAMI')}")
                for column in (
                    "secmen_SAYISI",
                    "oy_KULLANAN_SECMEN_SAYISI",
                    "gecerli_OY_TOPLAMI",
                    "gecersiz_OY_TOPLAMI",
                ):
                    sums[column] += num(row.get(column))
                sums["bagimsiz_TOPLAM_OY"] += num(row.get("bagimsiz_TOPLAM_OY"))
            for column, value in race.total.items():
                if column.endswith(("_ALDIGI_OY", "_SAYISI", "_TOPLAMI")) and (
                    column.startswith(("parti", "bagimsiz", "secmen", "oy_", "gecerli", "gecersiz"))
                    and num(value) != sums[column]
                ):
                    raise ValueError(f"{contest_key}: {column} boxes {sums[column]}, service total {value}")
            summary.append(
                f"{contest_key}: {len(race.boxes)} boxes, registered {sums['secmen_SAYISI']}, voters "
                f"{sums['oy_KULLANAN_SECMEN_SAYISI']}, valid {sums['gecerli_OY_TOPLAMI']}"
            )
    p.frame("reporting_unit", pl.DataFrame(unit_rows))
    p.frame(
        "result_value",
        pl.DataFrame(
            values,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
            },
            orient="row",
        ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
    )
    p.external |= external
    p.accept(
        "accounting.turnout",
        "In five boxes, one each in Ağrı, Yeşilyurt, Buharkent, Mahmudiye, and Yalova, the service gives one to six "
        "more voters than registered voters, in both races where there are two; the boxes are stored as the service "
        "publishes them.",
    )
    print("\n".join(summary))
    print(f"{len(units)} boxes, {len(values)} values")
    return p


def packages() -> list[str]:
    return [KEY]


def parse() -> None:
    build().write()


def emit() -> None:
    package.run("load", packages())
