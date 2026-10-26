import difflib
import html
import re
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import httpx

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "tr.tbmm"
BASE = "https://www.tbmm.gov.tr"
FORM = f"{BASE}/milletvekili-arama/form"
TOKEN = re.compile(r'X-CSRF-TOKEN-TBMM\.WEB\.Mvc\.Prod(?:" type="hidden" value=|": )"([^"]+)"')
OPTION = re.compile(r'<option value="([^"]*)"[^>]*>\s*([^<]*)')
IMAGE = re.compile(rb'src="data:[^"]*"')
PAGE = 50


def listing(term: int, page: int, province: str = "") -> str:
    return f"{BASE}/milletvekili-arama/liste#donem={term}&page={page}" + (f"&il={province}" if province else "")


def detail(term: int, member: str) -> str:
    return f"{BASE}/milletvekili/milletvekilidetay?DonemId={term}&Id={member}"


ENTRY = re.compile(r'<strong class="isim_soyisim"[^>]*>\s*([^<]+?)\s*<.*?milletvekilidetay\?DonemId=([0-9a-f-]+)&(?:amp;)?Id=([0-9a-f-]+)', re.S)


def members(term: int) -> list[tuple[str, str, str]]:
    found: dict[str, tuple[str, str, str]] = {}
    _, index = raw._index(SOURCE)
    provinces = sorted({key.split("&il=")[1] for key in index if f"donem={term}&page=" in key and "&il=" in key})
    for province in ["", *provinces]:
        page = 0
        while True:
            try:
                artifact = raw.cached(SOURCE, listing(term, page, province))
            except LookupError:
                break
            text = artifact.path.read_text()
            for name, term_id, member in ENTRY.findall(text):
                found[member] = (html.unescape(name), term_id, member)
            if text.count('class="isim_soyisim"') < PAGE:
                break
            page += 1
    return list(found.values())


def store(url: str, response: httpx.Response) -> str:
    response.raise_for_status()
    content = IMAGE.sub(b'src=""', response.content)
    raw.store(SOURCE, url, content, suffix=".html", retrieved_at=datetime.now(UTC), media_type="text/html")
    time.sleep(1)
    return content.decode()


def fetch() -> None:
    with httpx.Client(headers={"User-Agent": raw.USER_AGENT}, follow_redirects=True, timeout=180) as client:
        form = client.get(FORM).text
        token = TOKEN.search(form)
        assert token is not None
        start = form.index('id="DonemId"')
        ids = {
            int(text.strip()): value
            for value, text in OPTION.findall(form[start : form.index("</select>", start)])
            if text.strip().isdigit()
        }
        for term, term_id in sorted(ids.items()):
            if term not in (9, 10, 11) and term < 17:
                continue
            first = listing(term, 0)
            try:
                raw.cached(SOURCE, first)
                continue
            except LookupError:
                pass
            response = client.post(
                f"{BASE}/milletvekili-arama/liste",
                data={
                    "X-CSRF-TOKEN-TBMM.WEB.Mvc.Prod": token.group(1),
                    "Adi": "",
                    "Soyadi": "",
                    "IlId": "",
                    "DonemId": term_id,
                    "PartiId": "",
                    "Ozgecmis": "",
                },
            )
            fresh = TOKEN.search(response.text)
            assert fresh is not None
            text = store(first, response)
            page = 0
            while text.count('class="isim_soyisim"') == PAGE:
                page += 1
                text = store(
                    listing(term, page),
                    client.post(
                        f"{BASE}/milletvekili-arama/yeniden-yukle",
                        data={
                            "milletvekiliAramaVM[DonemId]": term_id.lower(),
                            "milletvekiliAramaVM[PageIndex]": "0",
                            "pageIndex": str(page),
                            "totalSize": str(PAGE),
                        },
                        headers={"X-CSRF-TOKEN-TBMM.WEB.Mvc.Prod": fresh.group(1), "X-Requested-With": "XMLHttpRequest"},
                    ),
                )
            print(f"term {term}: {page + 1} pages")
        select = form.index('id="IlId"')
        provinces = [value for value, _ in OPTION.findall(form[select : form.index("</select>", select)]) if value]
        for term in (24, 25, 26):
            for province in provinces:
                first = listing(term, 0, province)
                try:
                    raw.cached(SOURCE, first)
                    continue
                except LookupError:
                    pass
                response = client.post(
                    f"{BASE}/milletvekili-arama/liste",
                    data={
                        "X-CSRF-TOKEN-TBMM.WEB.Mvc.Prod": token.group(1),
                        "Adi": "",
                        "Soyadi": "",
                        "IlId": province,
                        "DonemId": ids[term],
                        "PartiId": "",
                        "Ozgecmis": "",
                    },
                )
                fresh = TOKEN.search(response.text)
                assert fresh is not None
                text = store(first, response)
                page = 0
                while text.count('class="isim_soyisim"') == PAGE:
                    page += 1
                    text = store(
                        listing(term, page, province),
                        client.post(
                            f"{BASE}/milletvekili-arama/yeniden-yukle",
                            data={
                                "milletvekiliAramaVM[DonemId]": ids[term].lower(),
                                "milletvekiliAramaVM[IlId]": province.lower(),
                                "milletvekiliAramaVM[PageIndex]": "0",
                                "pageIndex": str(page),
                                "totalSize": str(PAGE),
                            },
                            headers={
                                "X-CSRF-TOKEN-TBMM.WEB.Mvc.Prod": fresh.group(1),
                                "X-Requested-With": "XMLHttpRequest",
                            },
                        ),
                    )

        for term in sorted(ids):
            if term not in (9, 10, 11) and term < 17:
                continue
            missing = []
            for _, term_id, member in members(term):
                try:
                    raw.cached(SOURCE, detail(term, member))
                except LookupError:
                    missing.append(member)
            print(f"term {term}: {len(missing)} details to fetch")

            def one(member: str, term: int = term) -> None:
                with httpx.Client(headers={"User-Agent": raw.USER_AGENT}, follow_redirects=True, timeout=180) as pool:
                    for attempt in range(4):
                        try:
                            store(
                                detail(term, member),
                                pool.get(f"{BASE}/milletvekili/milletvekilidetay?DonemId={ids[term]}&Id={member}"),
                            )
                            return
                        except httpx.HTTPError:
                            time.sleep(5 * (attempt + 1))
                    raise RuntimeError(f"{term} {member}")

            with ThreadPoolExecutor(12) as workers:
                list(workers.map(one, missing))


TEXT = re.compile(r"<script.*?</script>|<style.*?</style>", re.S)
TAGS = re.compile(r"<[^>]+>")
HEAD = re.compile(r"(\d+)\s?\. DÖNEM (.*?) MİLLETVEKİLİ(.*?)(?: Adres :|$)")
DATE = re.compile(r"(\d\d)\.(\d\d)\.(\d{4})")
DIED = re.compile(r"Vefat Tarihi : (\d\d)\.(\d\d)\.(\d{4})")
ASCII = str.maketrans("çğıöşüâîû", "cgiosuaiu")


def lower(text: str) -> str:
    return text.replace("İ", "i").replace("I", "ı").lower()


def fold(text: str) -> str:
    return re.sub(r"[^a-z]", "", lower(text).translate(ASCII))


def capitalize(word: str) -> str:
    word = lower(word)
    return word[:1].replace("i", "İ").replace("ı", "I").upper() + word[1:]


def title(text: str) -> str:
    return " ".join(
        word if not (len(word) > 1 and word == word.upper() and word.isalpha()) else capitalize(word)
        for word in text.split()
    )


def page(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(TAGS.sub(" ", TEXT.sub("", text))))


def rows(term: int) -> list[dict[str, object]]:
    found = []
    for name, term_id, member in members(term):
        text = page(raw.cached(SOURCE, detail(term, member)).path.read_text())
        head = HEAD.search(text[text.find("Komisyon Konuşmaları") :])
        assert head is not None, (term, member)
        label, province = head.group(2).rsplit(" ", 1) if " " in head.group(2) else ("", head.group(2))
        tail = head.group(3).strip()
        end = DATE.search(tail)
        reason = "" if end is None else tail[: end.start()].strip(" -")
        found.append(
            {
                "member": member,
                "name": name,
                "party": label if label != "MECLİS" else None,
                "province": province,
                "end": None if end is None else f"{end.group(3)}-{end.group(2)}-{end.group(1)}",
                "reason": reason,
            }
        )
    return found


VERSION = "1"
NOTES = (
    "The Assembly's member search lists every member of a term with the province, the party label, and the date and "
    "reason the membership ended; it does not give start dates, and a member who replaced a departing one is listed "
    "as any other. Persons are matched to the term's elected candidacies by name."
)
TITLE = "Türkiye Büyük Millet Meclisi: Milletvekilleri (dönem listeleri ve özgeçmiş sayfaları)"
ELECTIONS = {
    9: (date(1950, 5, 14),),
    10: (date(1954, 5, 2),),
    11: (date(1957, 10, 27),),
    17: (date(1983, 11, 6),),
    18: (date(1987, 11, 29),),
    19: (date(1991, 10, 20),),
    20: (date(1995, 12, 24),),
    21: (date(1999, 4, 18),),
    22: (date(2002, 11, 3), date(2003, 3, 9)),
    23: (date(2007, 7, 22),),
    24: (date(2011, 6, 12),),
    25: (date(2015, 6, 7),),
    26: (date(2015, 11, 1),),
}
ENDS = {
    "": "term_end",
    "GENEL SEÇİMLERİN YENİLENMESİ (DÖNEM BİTİMİ)": "term_end",
    "VEFAT": "died",
    "MİLLETVEKİLLİĞİNDEN İSTİFA": "resigned",
    "27/05/1960 TSK MÜDAHALESİ": "dissolved",
    "ANAYASAYA GÖRE MİLLETVEKİLLİĞİNİN DÜŞMESİ": "lost_seat",
    "BELEDİYE BAŞKANLIĞINI TERCİH ETME": "chose_another_seat",
    "CUMHURBAŞKANI SEÇİLME": "elevated",
    "AY'NIN 106'NCI MADDESİ UYARINCA CUMHURBAŞKANI YRD. VEYA BAKAN OLARAK ATANMA": "appointed",
    "YSK İPTALİ": "annulled",
    "ÜYELİĞİN YÜKSEK SEÇİM KURULUNCA İPTALİ": "annulled",
}


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Members of the Grand National Assembly, terms 9–11 and 17–26 (1950–1960 and 1983–2018), from the TBMM",
            polity="TR",
        )
        source = cs.source("Türkiye Büyük Millet Meclisi", TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher="Türkiye Büyük Millet Meclisi",
            title=TITLE,
            url=f"{BASE}/milletvekili-arama/form",
            license="Official public record, no license stated",
            notes=NOTES,
        )
        assembly = cs.key("tr.body", "tbmm")
        pools: dict[int, list[tuple[UUID, UUID, list[str]]]] = {}
        for term, days in ELECTIONS.items():
            pools[term] = (
                [
                    (person, contest, names)
                    for person, contest, names in conn.execute(
                        """select ca.person_id, k.id, (select array_agg(text) from name where entity_id = ca.person_id)
                           from outcome x join candidacy ca on ca.id = x.candidacy_id
                           join contest_option o on o.id = ca.option_id join contest k on k.id = o.contest_id
                           join election e on e.id = k.election_id join polity p on p.id = e.polity_id
                           where p.iso_code = 'TR' and e.date_from = any(%s) and x.kind::text in ('elected', 'won')""",
                        [list(days)],
                    ).fetchall()
                ]
                if term >= 17
                else []
            )
        counts: Counter[str] = Counter()
        for term in sorted(ELECTIONS):
            listed = rows(term)
            matched: dict[str, tuple[UUID, UUID]] = {}
            free = {index: names for index, (_, _, names) in enumerate(pools[term])}
            for strictness in range(4):
                claims: defaultdict[int, list[str]] = defaultdict(list)
                options: dict[str, list[int]] = {}
                for row in listed:
                    member = str(row["member"])
                    if member in matched:
                        continue
                    words = [fold(word) for word in str(row["name"]).split()]
                    options[member] = []
                    for index, names in free.items():
                        for name in names:
                            theirs = [fold(word) for word in name.split()]
                            if strictness == 0:
                                hit = fold(name) == fold(str(row["name"]))
                            elif strictness == 1:
                                hit = theirs[-1] == words[-1] and all(word in words for word in theirs[:-1])
                            elif strictness == 2:
                                hit = (
                                    theirs[-1] == words[-1]
                                    and difflib.SequenceMatcher(None, "".join(theirs[:-1]), "".join(words[:-1])).ratio()
                                    >= 0.75
                                )
                            else:
                                hit = theirs[-1] == words[-1]
                            if hit:
                                options[member].append(index)
                                claims[index].append(member)
                                break
                for member, found in options.items():
                    if len(found) == 1 and claims[found[0]] == [member]:
                        person, contest, _ = pools[term][found[0]]
                        matched[member] = (person, contest)
                        del free[found[0]]
            for row in listed:
                member = str(row["member"])
                hit = matched.get(member)
                tenure = cs.key(f"{SOURCE}.tenure", f"{term}:{member}")
                if hit is None:
                    person = cs.key(f"{SOURCE}.person", f"{term}:{member}")
                    if person in cs.minted:
                        cs.add("person", id=person)
                        cs.add(
                            "name",
                            entity_id=person,
                            text=title(str(row["name"])),
                            lang="tr",
                            kind="official",
                            is_preferred=True,
                        )
                    counts[term, "new"] += 1
                else:
                    person = hit[0]
                    counts[term, "matched"] += 1
                cs.artifact(raw.cached(SOURCE, detail(term, member)), source)
                cs.add(
                    "tenure",
                    id=tenure,
                    person_id=person,
                    body_id=assembly,
                    from_date=None,
                    to_date=row["end"],
                    acquired_via="elected",
                    contest_id=None if hit is None else hit[1],
                    ended_via=None if row["end"] is None else ENDS[str(row["reason"])],
                )
                cs.add("citation", entity_id=tenure, source_id=source, locator={"term": term, "member": member})
                if term < 17 and row["province"] != "BİLİNMİYOR":
                    cs.add(
                        "note",
                        entity_id=tenure,
                        kind="context",
                        body_md=f"Member for {capitalize(str(row['province']))}.",
                    )
                if row["end"] is not None and ENDS[str(row["reason"])] in ("died", "resigned"):
                    cs.add(
                        "tenure_event",
                        tenure_id=tenure,
                        kind=ENDS[str(row["reason"])],
                        date=row["end"],
                        source_id=source,
                    )
        for key, count in sorted(counts.items()):
            print(key, count)
        return cs.write()
