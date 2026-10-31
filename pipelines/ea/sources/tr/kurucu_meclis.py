import json
import re
import subprocess
from collections import Counter
from datetime import date
from pathlib import Path
from uuid import uuid5

from ea import raw
from ea.changeset import Changeset
from ea.sources.tr.tbmm import fold, title
from ea.store import DATA, connect

SOURCE = "tr.kurucu_meclis"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://cdn.tbmm.gov.tr/TbmmWeb/Albumler/Tbmm_Albumu/Cilt4.pdf"
PUBLISHER = "Türkiye Büyük Millet Meclisi"
TITLE = "TBMM Albümü 1920-2010, 4. Cilt (1960-1983)"
OFFSET = 1586
BODIES = {
    "mbk": {
        "slug": "milli-birlik-komitesi",
        "kind": "appointed_assembly",
        "en": "National Unity Committee",
        "tr": "Millî Birlik Komitesi",
        "pages": (41, 47),
        "role": "Millî Birlik Komitesi",
        "opened": date(1960, 5, 27),
        "closed": date(1961, 10, 25),
        "note": (
            "The officers who seized power on 27 May 1960, named as the committee by the Provisional Constitution "
            "(Law 1 of 12 June 1960). It held legislative power until the Constituent Assembly was formed, and from "
            "6 January 1961 sat as one of its two chambers with the House of Representatives, until the Grand "
            "National Assembly met on 25 October 1961. Fourteen members were removed on 13 November 1960."
        ),
    },
    "tm": {
        "slug": "temsilciler-meclisi",
        "kind": "constituent_assembly",
        "en": "House of Representatives",
        "tr": "Temsilciler Meclisi",
        "pages": (54, 102),
        "role": "Kurucu Meclis",
        "opened": date(1961, 1, 6),
        "closed": date(1961, 10, 25),
        "note": (
            "The elected chamber of the Constituent Assembly (Kurucu Meclis) of 1961, set up by Law 157 of "
            "13 December 1960 alongside the National Unity Committee. It drafted the 1961 Constitution and the "
            "election laws. Its members were the ministers, ten chosen by the head of state, eighteen by the "
            "National Unity Committee, representatives of the Republican People's Party and the Republican "
            "Villagers Nation Party, one or more chosen in each province by an electoral college, and "
            "representatives of the judiciary, the universities, the press, the bar associations, and other "
            "organizations. It met from 6 January 1961 until the Grand National Assembly met on 25 October 1961."
        ),
    },
    "mgk": {
        "slug": "milli-guvenlik-konseyi",
        "kind": "appointed_assembly",
        "en": "National Security Council",
        "tr": "Millî Güvenlik Konseyi",
        "pages": (201, 201),
        "role": "Millî Güvenlik Konseyi",
        "opened": date(1980, 9, 12),
        "closed": date(1983, 12, 6),
        "note": (
            "The council of the chief of the general staff and the commanders of the land, naval, and air forces "
            "and the gendarmerie that took power on 12 September 1980 and held legislative power under Law 2324. "
            "From 1981 it sat as one of the two chambers of the Constituent Assembly with the Consultative "
            "Assembly, until the Grand National Assembly met on 6 December 1983."
        ),
    },
    "dm": {
        "slug": "danisma-meclisi",
        "kind": "constituent_assembly",
        "en": "Consultative Assembly",
        "tr": "Danışma Meclisi",
        "pages": (208, 236),
        "role": "Danışma Meclisi",
        "opened": date(1981, 10, 15),
        "closed": date(1983, 12, 6),
        "seats": 160,
        "note": (
            "The appointed chamber of the Constituent Assembly of 1981, set up by Law 2485 of 29 June 1981 "
            "alongside the National Security Council. It drafted the 1982 Constitution and the election and party "
            "laws. Its 160 members were all chosen by the National Security Council: 120 from candidates nominated "
            "in each province and 40 directly. It met from 23 October 1981 until the Grand National Assembly met "
            "on 6 December 1983."
        ),
    },
}
ELECTIONS = {date(1961, 10, 15), date(1983, 11, 6)}
ENDS = {
    "Hıfzı Oğuz BEKATA": (
        date(1961, 10, 15),
        (
            "The album dates the membership to 12 October 1975, the end of the member's Senate term; it is closed at "
            "the 1961 election, at which the member was elected to the Senate."
        ),
    )
}
GROUPS = {
    "Bakanlar Kurulu Üyesi": ("ex_officio", "Member as a minister in the Council of Ministers."),
    "Devlet Başkanı Temsilcisi": ("appointed", "Chosen by the head of state."),
    "Milli Birlik Komitesi Temsilcisi": ("appointed", "Chosen by the National Unity Committee."),
    "CHP Temsilcisi": ("indirect", "Representative of the Republican People's Party."),
    "CKMP Temsilcisi": ("indirect", "Representative of the Republican Villagers Nation Party."),
    "Basın Temsilcisi": ("indirect", "Representative of the press."),
    "Üniversite Temsilcisi": ("indirect", "Representative of the universities."),
    "Yargı Organları Temsilcisi": ("indirect", "Representative of the judiciary."),
    "Barolar Temsilcisi": ("indirect", "Representative of the bar associations."),
    "Odalar Temsilcisi": ("indirect", "Representative of the chambers of commerce and industry."),
    "Öğretmen Teşekkülleri Temsilcisi": ("indirect", "Representative of the teachers' organizations."),
    "Esnaf Teşekkülleri Temsilcisi": ("indirect", "Representative of the tradesmen's organizations."),
    "İşçi Sendikaları Temsilcisi": ("indirect", "Representative of the trade unions."),
    "Tarım Teşekkülleri Temsilcisi": ("indirect", "Representative of the farmers' organizations."),
    "Eski Muharipler Birliği Temsilcisi": ("indirect", "Representative of the veterans' association."),
    "Gençlik Temsilcisi": ("indirect", "Representative of the youth organizations."),
    "MGK’CA SEÇİLEN ÜYE": ("appointed", "Chosen directly by the National Security Council."),
}
PARTIES = {"CHP Temsilcisi": "Cumhuriyet Halk Partisi", "CKMP Temsilcisi": "Cumhuriyetçi Köylü Millet Partisi"}
HEAD = re.compile(
    r"[A-ZÇĞİÖŞÜÂÎÛ /’'()]{3,}|[^–]{0,50}(Üye|Temsilci)[^–]{0,40}|\(Mill[iî] Birlik Komitesi\) Üyesi|Milli Güvenlik Konseyi Üyesi"
)
SPAN = re.compile(
    r"((?:Kurucu Meclis|Danışma Meclisi|Millî Birlik Komitesi|Millî Güvenlik Konseyi)[^()–]{0,80}?) ?\(([^()]*\d{4}[^()]*)\)"
)
DAY = re.compile(r"(\d\d?)[.,] ?(\d\d?)[.,] ?(\d{4})")
SPEAKER = re.compile(r"(Temsilciler|Danışma) Meclisi Başkanı\b")


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    pdf = raw.cached(SOURCE, URL).path
    entries = []
    for key, body in BODIES.items():
        first, last = body["pages"]
        current: dict[str, object] | None = None
        rows = []
        for page in range(first, last + 1):
            for column in (0, 1):
                lines = (
                    subprocess.run(
                        [
                            "pdftotext",
                            "-f",
                            str(page),
                            "-l",
                            str(page),
                            "-layout",
                            "-x",
                            str(column * 312),
                            "-y",
                            "0",
                            "-W",
                            "312",
                            "-H",
                            "880",
                            str(pdf),
                            "-",
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    .stdout.replace("\xad", "")
                    .splitlines()
                )
                for index, line in enumerate(lines):
                    stripped = line.strip()
                    if stripped == "DİPNOTLAR":
                        break
                    if not stripped or re.fullmatch(r"\d{4}|Kurucu Meclis / \d{4} - \d{4}", stripped):
                        continue
                    following = next((text for text in lines[index + 1 : index + 3] if text.strip()), "")
                    if (
                        len(line) - len(line.lstrip()) <= 4
                        and HEAD.fullmatch(stripped)
                        and len(following) - len(following.lstrip()) >= 6
                    ):
                        current = {"body": key, "page": page + OFFSET, "head": stripped, "lines": []}
                        rows.append(current)
                    elif current is not None:
                        current["lines"].append(stripped)
        for row in rows:
            lines = row["lines"]
            name = lines[0]
            index = 1
            while name.count("(") > name.count(")") or (index < len(lines) and re.fullmatch(r"\(.*\)", lines[index])):
                name += " " + lines[index]
                index += 1
            text = ""
            for line in lines[index:]:
                text = text[:-1] + line if text.endswith("-") and not text.endswith(" -") else f"{text} {line}".strip()
            text = re.sub(r"\((\d\d\.\d\d\.\d{4} – \d\d\.\d\d\.\d{4}) –", r"(\1) –", re.sub(r"\s+", " ", text))
            listed = re.match(r"^(.*?)\s*(?:\((.*)\))?$", re.sub(r"\d+", "", name).strip())
            assert listed is not None
            born = re.match(r"^(.+?) [–-] (\d{4})", text)
            died = re.search(r"Ölüm Tarihi: " + DAY.pattern, text)
            spans = []
            for match in SPAN.finditer(text):
                if not match.group(1).startswith(body["role"]):
                    continue
                days = [date(int(y), int(m), int(d)) for d, m, y in DAY.findall(match.group(2))]
                if not days or not body["opened"] <= days[0] <= body["closed"]:
                    continue
                start, end = (days[0], days[-1]) if len(days) > 1 else (body["opened"], days[0])
                span = [match.group(1), start.isoformat(), end.isoformat(), match.group(2)]
                if spans and spans[-1][2] == span[1]:
                    spans[-1] = [spans[-1][0], spans[-1][1], span[2], f"{spans[-1][3]}; {span[3]}"]
                elif span not in spans:
                    spans.append(span)
            if not spans:
                for match in re.finditer(r"\(([^()]*\d{4}[^()]*)\)", text):
                    days = [date(int(y), int(m), int(d)) for d, m, y in DAY.findall(match.group(1))]
                    if len(days) > 1 and body["opened"] <= days[0] <= body["closed"]:
                        spans.append([body["role"], days[0].isoformat(), days[-1].isoformat(), match.group(1)])
                        break
            entries.append(
                {
                    "body": key,
                    "page": row["page"],
                    "head": row["head"],
                    "name": listed.group(1).strip(),
                    "registered": listed.group(2),
                    "born": None if born is None else int(born.group(2)),
                    "died": None if died is None else f"{died.group(3)}-{died.group(2)}-{died.group(1)}",
                    "spans": spans,
                    "speaker": SPEAKER.search(text) is not None,
                    "text": text,
                }
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    (PARSED / "entries.json").write_text(json.dumps(entries, ensure_ascii=False, indent=1))
    print(Counter((entry["body"], len(entry["spans"])) for entry in entries))
    for entry in entries:
        if len(entry["spans"]) != 1:
            print(entry["body"], entry["name"], entry["spans"])


def emit() -> Path:
    entries = json.loads((PARSED / "entries.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Members of the Constituent Assemblies of 1960-1961 and 1980-1983 (the National Unity Committee, the "
                "House of Representatives, the National Security Council, and the Consultative Assembly) from the "
                "TBMM Albümü"
            ),
            polity="TR",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=URL,
            license="Official public record, no license stated",
            notes=(
                "Biographies of every member of the National Unity Committee, the House of Representatives, the "
                "National Security Council, and the Consultative Assembly, each with the group or province the "
                "member sat for and the dates of membership, a resignation, or an annulment, and the date of death."
            ),
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        turkey = conn.execute("select id from polity where iso_code = 'TR'").fetchone()[0]
        parties = dict(
            conn.execute(
                """select n.text, p.id from party p join name n on n.entity_id = p.id
                   where p.polity_id = %s and n.text = any(%s)""",
                [turkey, list(PARTIES.values())],
            ).fetchall()
        )
        known: dict[str, list[tuple[object, int | None]]] = {}
        for person, text, born in conn.execute(
            """select distinct p.id, n.text, extract(year from p.birth_date)::int from person p
               join name n on n.entity_id = p.id
               where exists (select 1 from tenure t join body b on b.id = t.body_id
                             where t.person_id = p.id and b.polity_id = %s)""",
            [turkey],
        ):
            found = known.setdefault(fold(text), [])
            if (person, born) not in found:
                found.append((person, born))
        bodies = {}
        for key, body in BODIES.items():
            bodies[key] = cs.key("tr.body", body["slug"])
            if bodies[key] in cs.minted:
                cs.add("body", id=bodies[key], polity_id=turkey, kind=body["kind"])
                for text, lang, kind in ((body["en"], "en", "official"), (body["tr"], "tr", "native")):
                    cs.add(
                        "name",
                        entity_id=bodies[key],
                        text=text,
                        lang=lang,
                        kind=kind,
                        is_preferred=kind == "official",
                        valid_from=None,
                        valid_from_precision="day",
                        valid_to=None,
                        valid_to_precision="day",
                    )
            cs.add("note", entity_id=bodies[key], kind="context", body_md=body["note"])
            cs.add("citation", entity_id=bodies[key], source_id=source, locator={"page": body["pages"][0] + OFFSET})
            if "seats" in body:
                cs.add(
                    "body_size",
                    body_id=bodies[key],
                    seats=body["seats"],
                    valid_from=body["opened"],
                    valid_to=body["closed"],
                )
            cs.add(
                "body_event",
                id=uuid5(bodies[key], "dissolved"),
                body_id=bodies[key],
                kind="dissolved",
                date=body["closed"],
                date_precision="day",
                source_id=source,
            )
        counts: Counter[object] = Counter()
        written: set[object] = set()
        for entry in entries:
            if not entry["spans"]:
                print("no span", entry["body"], entry["name"])
                continue
            body = BODIES[entry["body"]]
            names = [text for text in (entry["name"], entry["registered"]) if text is not None]
            candidates = sorted(
                {
                    (born is None, str(person), person)
                    for text in names
                    for person, born in known.get(fold(text), [])
                    if born is None or entry["born"] is None or born == entry["born"]
                }
            )
            if candidates:
                person = candidates[0][2]
                counts["matched"] += 1
                for *_, other in candidates[1:]:
                    cs.merge(other, person)
                    counts["merged"] += 1
            else:
                person = cs.key(f"{SOURCE}.person", f"{fold(entry['registered'] or entry['name'])}:{entry['born']}")
                if person not in written:
                    written.add(person)
                    counts["minted"] += 1
                    cs.add(
                        "person",
                        id=person,
                        birth_date=None if entry["born"] is None else date(entry["born"], 1, 1),
                        birth_precision="year",
                        death_date=None if entry["died"] is None else date.fromisoformat(entry["died"]),
                        death_precision="day",
                    )
                    for text, preferred in ((entry["name"], True), (entry["registered"], False)):
                        if text is not None:
                            cs.add(
                                "name",
                                entity_id=person,
                                text=title(text),
                                lang="tr",
                                kind="official",
                                is_preferred=preferred,
                            )
            head = entry["head"]
            group = GROUPS.get(head)
            if entry["body"] in ("mbk", "mgk"):
                via, context = "ex_officio", None
            elif group is not None:
                via, context = group
            else:
                province = title(re.sub(r" \(.*\)| İli? Temsilcisi$", "", head))
                via = "indirect" if entry["body"] == "tm" else "appointed"
                context = f"Member for {province} Province" + (
                    ", chosen by an electoral college of the province."
                    if entry["body"] == "tm"
                    else ", chosen by the National Security Council from the province's nominees."
                )
            for role, start, end, said in entry["spans"]:
                start, end = date.fromisoformat(start), date.fromisoformat(end)
                end, correction = ENDS.get(entry["name"], (end, None))
                died = entry["died"] is not None and date.fromisoformat(entry["died"]) == end
                if end == body["closed"]:
                    ended = "dissolved"
                elif "İstifa" in said or "istifa" in said:
                    ended = "resigned"
                elif "hükümsüz" in said or "iptal" in said:
                    ended = "annulled"
                elif died:
                    ended = "died"
                elif end in ELECTIONS:
                    ended = "chose_another_seat"
                elif entry["body"] == "mbk" and end == date(1960, 11, 13):
                    ended = "removed"
                else:
                    ended = None
                    print("unexplained end", entry["body"], entry["name"], start, end, said)
                tenure = cs.key(f"{SOURCE}.tenure", f"{entry['body']}:{person}:{start}")
                cs.add(
                    "tenure",
                    id=tenure,
                    person_id=person,
                    body_id=bodies[entry["body"]],
                    from_date=start,
                    from_precision="day",
                    to_date=end,
                    to_precision="day",
                    acquired_via=via,
                    ended_via=ended,
                )
                cs.add("citation", entity_id=tenure, source_id=source, locator={"page": entry["page"]})
                sentences = [] if context is None else [context]
                if "Başkanı" in role:
                    sentences.append("Chairman.")
                if entry["speaker"]:
                    sentences.append(f"Speaker of the {body['en']}.")
                if ended == "chose_another_seat":
                    sentences.append("Membership ended on election to the Grand National Assembly.")
                if ended == "removed":
                    sentences.append("One of the fourteen members removed from the committee on 13 November 1960.")
                if correction is not None:
                    sentences.append(correction)
                if ended == "annulled":
                    sentences.append("The Supreme Election Council annulled the member's election.")
                if sentences:
                    cs.add("note", entity_id=tenure, kind="context", body_md=" ".join(sentences))
                if ended in ("resigned", "died", "removed"):
                    cs.add("tenure_event", tenure_id=tenure, kind=ended, date=end, source_id=source)
                if head in PARTIES and PARTIES[head] in parties:
                    cs.add(
                        "tenure_affiliation",
                        tenure_id=tenure,
                        kind="party",
                        party_id=parties[PARTIES[head]],
                        valid_from=start,
                        valid_from_precision="day",
                        valid_to=end,
                    )
                counts[entry["body"], ended] += 1
        for key, count in sorted(counts.items(), key=str):
            print(key, count)
        return cs.write()
