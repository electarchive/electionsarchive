import re
from collections import Counter
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.tr import tbmm, tbmm_album
from ea.sources.tr.tbmm import fold, title
from ea.sources.tr.thea import PARTIES
from ea.sources.tr.ysk import INDEPENDENTS, PROVINCES
from ea.store import DATA, connect

SOURCE = "tr.tbmm_rolls"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Türkiye Büyük Millet Meclisi"
TERMS = {9: date(1950, 5, 14), 10: date(1954, 5, 2), 11: date(1957, 10, 27)}
PARTY_KEYS = {
    "DP": "demokrat-parti-1946",
    "CHP": "cumhuriyet-halk-partisi",
    "MP": "millet-partisi-1948",
    "CMP": "cumhuriyetci-millet-partisi",
    "TKP": "turkiye-koylu-partisi",
    "HP": "hurriyet-partisi",
    "VP": "vatan-partisi-1954",
}
CLEA = {5: "DP", 40: "CHP", 21: "MP", 38: "CMP", 31: "TKP", 12: "HP", 15: "VP"}
DOUBLES = {
    9: [
        ("Ali İhsan Bâbis", "denizli", "afyonkarahisar", 15),
        ("Osman Şevki Çiçekdağ", "kirsehir", "ankara", 16),
        ("Fuad Köprülü", "aydin", "istanbul", 10),
        ("Fevzi Lûtfi Karaosmanoğlu", "aydin", "manisa", 686),
        ("Refik Koraltan", "balikesir", "mersin", 16),
        ("Celâl Bayar", "bursa", "istanbul", 16),
        ("Fahri Belen", "canakkale", "bolu", 17),
        ("Ali Fuad Cebesoy", "istanbul", "eskisehir", 151),
        ("Halil Özyörük", "istanbul", "izmir", 16),
        ("Refik Şevket İnce", "izmir", "manisa", 16),
        ("Samet Ağaoğlu", "mugla", "manisa", 686),
    ],
    10: [
        ("Adnan Menderes", "aydin", "istanbul", 21),
        ("Celâl Bayar", "bursa", "istanbul", 20),
        ("Refik Koraltan", "kayseri", "mersin", 20),
        ("Fuad Köprülü", "zonguldak", "istanbul", 21),
    ],
    11: [
        ("Kasım Gülek", "ankara", "adana", 135),
        ("İsmet İnönü", "ankara", "malatya", 16),
        ("Kâmil Kırıkoğlu", "ankara", "sivas", 16),
        ("Turhan Feyzioğlu", "ankara", "sivas", 15),
        ("Ferid Melen", "ankara", "van", 16),
        ("Adnan Menderes", "aydin", "istanbul", 15),
        ("Celâl Bayar", "bursa", "istanbul", 15),
        ("Refik Koraltan", "kocaeli", "mersin", 15),
    ],
}
SWORN_LATER = {9: {"Reşat Şemsettin Sirer", "Ahmet Kemal Varınca"}}
ALIASES = tbmm_album.ALIASES | {"afyon": "afyonkarahisar", "kmaras": "kahramanmaras"}
DIGITS = re.compile(r"\d+")


def parse() -> None:
    from ea.sources import thea
    from ea.sources.tr import thea as held

    entries = pl.read_parquet(tbmm_album.PARSED / "entries.parquet")
    keys = {}
    for row in entries.filter(pl.col("term") >= 12).iter_rows(named=True):
        key = f"{fold(row['registered'] or row['name'])}:{fold(row['birthplace'] or '')}:{row['born']}"
        keys.setdefault(DIGITS.sub("", key), key)
    seats: Counter[tuple[int, str, str]] = Counter()
    for row in (
        pl.read_parquet(thea.PARSED / "lower.parquet")
        .filter((pl.col("ctr") == held.TURKEY) & pl.col("yr").is_in([day.year for day in TERMS.values()]))
        .iter_rows(named=True)
    ):
        if row["seat"]:
            province = ALIASES.get(fold(row["cst_n"]), fold(row["cst_n"]))
            seats[row["yr"], province, CLEA.get(row["pty"], "IND")] += int(row["seat"])
    members = []
    review = []
    for term, day in TERMS.items():
        listed: Counter[tuple[str, str]] = Counter()
        for row in entries.filter((pl.col("term") == term) & ~pl.col("by_election")).iter_rows(named=True):
            province = ALIASES.get(fold(row["province"]), fold(row["province"]))
            printed = row["parties"][0] if row["parties"] else None
            if printed is None:
                found = re.match(r"^(?:\[[^\]]*\]\s*)?(Bağımsız|[A-ZÇĞİÖŞÜ]{2,4})\b", row["bio"])
                printed = None if found is None else "IND" if found.group(1) == "Bağımsız" else found.group(1)
            won = [code for (year, where, code), count in seats.items() if year == day.year and where == province]
            party = printed if printed in won else won[0] if len(won) == 1 else None
            if party != printed:
                review.append({"term": term, "province": province, "name": row["name"], "album": printed, "set": party})
            key = f"{fold(row['registered'] or row['name'])}:{fold(row['birthplace'] or '')}:{row['born']}"
            name = title(DIGITS.sub("", row["name"]).strip())
            if name in SWORN_LATER.get(term, set()):
                continue
            base = {
                "term": term,
                "person": keys.get(DIGITS.sub("", key), DIGITS.sub("", key)),
                "name": name,
                "registered": None if row["registered"] is None else title(DIGITS.sub("", row["registered"]).strip()),
                "born": row["born"],
                "died": row["died"],
                "party": party,
                "listed": printed == "IND" and party != "IND",
                "page": row["page"],
            }
            members.append(base | {"province": province, "gave_up": None, "index_page": None})
            listed[province, str(party)] += 1
        for printed_name, other, chosen, page in DOUBLES[term]:
            base = max(
                (row for row in members if row["term"] == term and row["province"] == chosen),
                key=lambda row: SequenceMatcher(None, fold(printed_name), fold(row["name"])).ratio(),
            )
            members.append(base | {"province": other, "gave_up": chosen, "index_page": page})
            listed[other, str(base["party"])] += 1
        for (year, where, code), count in sorted(seats.items()):
            if year == day.year and listed[where, code] != count:
                review.append(
                    {
                        "term": term,
                        "province": where,
                        "name": f"seats {code}",
                        "album": str(listed[where, code]),
                        "set": str(count),
                    }
                )
        for (where, code), count in sorted(listed.items()):
            if seats[day.year, where, code] == 0:
                review.append(
                    {"term": term, "province": where, "name": f"seats {code}", "album": str(count), "set": "0"}
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(members, infer_schema_length=None)
    frame.write_parquet(PARSED / "members.parquet")
    pl.DataFrame(review, schema=["term", "province", "name", "album", "set"], orient="row").write_parquet(
        PARSED / "review.parquet"
    )
    print(frame.group_by("term", "party").len().sort("term", "party"))
    for row in review:
        print(row)


def emit() -> Path:
    members = pl.read_parquet(PARSED / "members.parquet")
    plates = {fold(name): plate for plate, name in enumerate(PROVINCES, 1)}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Elected members of the 1950, 1954, and 1957 Turkish general elections by province, from the TBMM Albümü, "
                "with members elected in two provinces from each term's minutes index; members who entered at a by-election "
                "are left out, including Reşat Şemsettin Sirer and Ahmet Kemal Varınca, whom the album leaves unmarked "
                "but who were not sworn in on 22 May 1950"
            ),
            polity="TR",
        )
        album = cs.source(PUBLISHER, tbmm_album.TITLE)
        cs.artifact(raw.cached(tbmm_album.SOURCE, tbmm_album.URL), album)
        indexes = {}
        for term in TERMS:
            url = tbmm.MINUTES.format(term=term)
            indexes[term] = cs.source(PUBLISHER, tbmm.MINUTES_TITLE.format(term=term))
            cs.add(
                "source",
                id=indexes[term],
                kind="official_body",
                publisher=PUBLISHER,
                title=tbmm.MINUTES_TITLE.format(term=term),
                url=url,
                license="Official public record, no license stated",
            )
            cs.artifact(raw.cached(tbmm.SOURCE, url), indexes[term])
        declared = dict(
            conn.execute(
                """select x.value, r.id from result_set r join source s on s.id = r.source_id
                   join external_id x on x.entity_id = r.contest_id and x.scheme = 'tr.contest'
                   where r.is_default and r.stage = 'official' and s.title like 'Constituency-Level%%'
                   and x.value like any(%s)""",
                [[f"{day}:parliament:%" for day in TERMS.values()]],
            ).fetchall()
        )
        written = set()
        for row in members.iter_rows(named=True):
            day = TERMS[row["term"]]
            contest_key = f"{day}:parliament:{plates[row['province']]:02}"
            contest = cs.key("tr.contest", contest_key)
            if contest in cs.minted:
                raise ValueError(f"{contest_key} is not loaded")
            key = PARTY_KEYS.get(row["party"])
            label = INDEPENDENTS if key is None else PARTIES[key][0][0]
            option = cs.key("tr.option", f"{contest_key}:{slug(label)}")
            if option in cs.minted:
                raise ValueError(f"{contest_key} has no {label} option")
            party = None if key is None else cs.key("tr.party", key)
            person = cs.key(f"{tbmm_album.SOURCE}.person", row["person"])
            if person in cs.minted and person not in written:
                written.add(person)
                cs.add(
                    "person",
                    id=person,
                    birth_date=None if row["born"] is None else date(row["born"], 1, 1),
                    birth_precision="year",
                    death_date=None if row["died"] is None else date.fromisoformat(row["died"]),
                    death_precision="day",
                )
                for text, preferred in ((row["name"], True), (row["registered"], False)):
                    if text is not None:
                        cs.add("name", entity_id=person, text=text, lang="tr", kind="official", is_preferred=preferred)
            written.add(person)
            candidacy = cs.key("tr.candidacy", f"{contest_key}:{slug(row['name'])}")
            cs.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role="list_member" if key is not None else "head",
                list_position=None,
                ballot_name=row["name"],
                member_of_party_id=None if row["listed"] else party,
                is_independent=key is None,
            )
            if party is not None:
                cs.add("candidacy_nomination", candidacy_id=candidacy, party_id=party, kind="nominated")
            cs.add("citation", entity_id=candidacy, source_id=album, locator={"term": row["term"], "page": row["page"]})
            if row["gave_up"] is not None:
                cs.add(
                    "citation",
                    entity_id=candidacy,
                    source_id=indexes[row["term"]],
                    locator={"item": "intihap dairesi tercihi", "page": row["index_page"]},
                )
            cs.add(
                "outcome",
                result_set_id=declared[contest_key],
                option_id=option,
                candidacy_id=candidacy,
                kind="elected",
                seats=None,
            )
        print(Counter(members["term"]))
        return cs.write()
