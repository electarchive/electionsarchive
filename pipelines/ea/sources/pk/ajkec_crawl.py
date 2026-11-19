"""The Azad Jammu and Kashmir Election Commission's local bodies 2022 result site, fetched through the same
requests its own search form sends: the divisions' districts, each district's tehsils, each tehsil's councils, each
council's wards, and the result table of every ward (or of a union council's district council seat)."""

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import httpx

from ea import raw

SOURCE = "pk.ajkec"
SITE = "https://ermis.admin.gok.pk/local-bodies-2022"
DIVISIONS = ("Muzaffarabad", "Poonch", "Mirpur")
MUNICIPAL = {"Municipal Corporation": 3, "Municipal Committee": 4, "Town Committee": 5}


def division_url(division: str) -> str:
    return f"{SITE}/division/{division}"


def tehsils_url(district: int) -> str:
    return f"{SITE}/getTehsils?district_id={district}"


def councils_url(tehsil: int) -> str:
    return f"{SITE}/tehsil/{tehsil}/unioncouncils"


def wards_url(council: int) -> str:
    return f"{SITE}/counsil/{council}/wards"


def result_url(kind: int, district: int, tehsil: int, council: int, ward: int | None) -> str:
    return f"{SITE}/getwardResults?council_type={kind}&district={district}&tehsil={tehsil}&council={council}&ward={ward or ''}"


def options(text: str) -> list[tuple[int, str]]:
    return [(int(v), n.strip()) for v, n in re.findall(r'<option[^>]*value="(\d+)"[^>]*>([^<]*)', text)]


def kind_of(name: str) -> int | None:
    for prefix, kind in MUNICIPAL.items():
        if name.startswith(prefix):
            return kind
    return None


def fetch() -> None:
    client = httpx.Client(headers={"User-Agent": raw.USER_AGENT}, follow_redirects=True, timeout=120)
    have = raw.keys(SOURCE)
    token = ""

    def store(url: str, content: bytes, suffix: str = ".html") -> None:
        raw.store(
            SOURCE, url, content, suffix=suffix, retrieved_at=datetime.now(UTC), media_type="text/html; charset=UTF-8"
        )

    def text(url: str) -> str:
        return raw.cached(SOURCE, url).path.read_text()

    def get(url: str) -> None:
        if url not in have:
            for attempt in range(5):
                r = client.get(url, headers={"X-CSRF-TOKEN": token, "X-Requested-With": "XMLHttpRequest"})
                if r.status_code < 500:
                    break
            r.raise_for_status()
            store(url, r.content)

    def post(url: str, path: str, data: dict[str, object]) -> None:
        if url not in have:
            for attempt in range(5):
                r = client.post(
                    f"{SITE}/{path}",
                    data={"_token": token, **data},
                    headers={"X-CSRF-TOKEN": token, "X-Requested-With": "XMLHttpRequest"},
                )
                if r.status_code < 500:
                    break
            r.raise_for_status()
            store(url, r.content)

    home = f"{SITE}/home"
    if home not in have:
        store(home, client.get(home).content)
    for division in DIVISIONS:
        url = division_url(division)
        page = client.get(url)
        page.raise_for_status()
        match = re.search(r'csrf-token" content="([^"]+)', page.text)
        if match is None:
            raise ValueError("no CSRF token")
        token = match.group(1)
        if url not in have:
            store(url, page.content)
        html = text(url)
        districts = options(html.split('id="district-list"')[1].split("</select>")[0])
        for district, _ in districts:
            post(tehsils_url(district), "getTehsils", {"district_id": district})
            for tehsil, _ in options(text(tehsils_url(district))):
                get(councils_url(tehsil))
                jobs = []
                for council, name in options(text(councils_url(tehsil))):
                    get(wards_url(council))
                    municipal = kind_of(name)
                    wards = [w for w, _ in options(text(wards_url(council)))]
                    if municipal is None:
                        jobs.append((1, district, tehsil, council, None))
                    kind = municipal or 2
                    jobs += [(kind, district, tehsil, council, w) for w in wards]

                def run(job: tuple[int, int, int, int, int | None]) -> None:
                    kind, d, t, c, w = job
                    post(
                        result_url(*job),
                        "getwardResults",
                        {"council_type": kind, "district": d, "tehsil": t, "council": c, "ward": w or ""},
                    )

                with ThreadPoolExecutor(8) as pool:
                    list(pool.map(run, jobs))
        print(division, "done", flush=True)


EC_SITE = "https://ec.ajk.gov.pk/local-bodies-election-2022"
EC_TYPES = {
    "ممبر یونین کونسل": 2,
    "ممبر ڈسٹرکٹ کونسل": 1,
    "ممبر میونسپل کمیٹی": 4,
    "ممبر ٹاون کمیٹی": 5,
    "میونسپل کارپوریشن": 3,
}
EC_MUNICIPAL = {"میونسپل کارپوریشن": 3, "میونسپل کمیٹی": 4, "ٹاون کمیٹی": 5, "ٹاؤن کمیٹی": 5}


def ec_url(endpoint: str, **params: str) -> str:
    from urllib.parse import urlencode

    return f"{EC_SITE}/ajax/{endpoint}.php?{urlencode(params)}"


def ec_options(text: str) -> list[str]:
    return [v for v in re.findall(r'<option value="([^"]+)"', text)]


def fetch_ec() -> None:
    """The Muzaffarabad division's results, which the Commission's own site serves by name instead of by number."""
    from urllib.parse import parse_qsl, urlsplit

    client = httpx.Client(headers={"User-Agent": raw.USER_AGENT}, follow_redirects=True, timeout=120)
    have = raw.keys(SOURCE)

    def post(url: str) -> str:
        if url not in have:
            parts = urlsplit(url)
            data = dict(parse_qsl(parts.query))
            for attempt in range(5):
                r = client.post(f"{EC_SITE}/ajax/{parts.path.rsplit('/', 1)[1]}", data=data)
                if r.status_code < 500:
                    break
            r.raise_for_status()
            raw.store(
                SOURCE,
                url,
                r.content,
                suffix=".html",
                retrieved_at=datetime.now(UTC),
                media_type="text/html; charset=UTF-8",
            )
            have.add(url)
        return raw.cached(SOURCE, url).path.read_text()

    page = f"{EC_SITE}/"
    if page not in have:
        raw.fetch(SOURCE, page, suffix=".html")
    html = raw.cached(SOURCE, page).path.read_text()
    districts = ec_options(html.split('id="district-list"')[1].split("</select>")[0])
    jobs: list[str] = []
    for district in districts:
        for tehsil in ec_options(post(ec_url("get-tehsil-ep", district_id=district))):
            for uc in ec_options(post(ec_url("get-uc-ep", tehsile_id=tehsil, district_id=district))):
                municipal = any(uc.startswith(prefix) for prefix in EC_MUNICIPAL)
                wards = ec_options(post(ec_url("get-ward-ep", uc_id=uc, tehsile_id=tehsil, district_id=district)))
                if not municipal:
                    jobs.append(
                        ec_url(
                            "show_results", type="ممبر ڈسٹرکٹ کونسل", district=district, tehsil=tehsil, uc=uc, ward=""
                        )
                    )
                kind = "ممبر یونین کونسل"
                for prefix, code in EC_MUNICIPAL.items():
                    if uc.startswith(prefix):
                        kind = next(name for name, k in EC_TYPES.items() if k == code)
                jobs += [
                    ec_url("show_results", type=kind, district=district, tehsil=tehsil, uc=uc, ward=w) for w in wards
                ]
    print(len(jobs), "ec results", flush=True)
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(post, jobs))
    print("Muzaffarabad done", flush=True)
