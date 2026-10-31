import base64
import difflib
import json
import os
import re
import subprocess
from datetime import date

import polars as pl

from ea.changeset import slug
from ea.sources.us import nh_manual
from ea.sources.us.nh_sos_county import COLUMNS
from ea.store import DATA

SOURCE = "us.nh_manual_state"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
OFFICES = (
    (r"governor", "governor"),
    (r"united\s+states\s+senators?", "us senate"),
    (r"representatives?\s+in\s+congress", "us house"),
    (r"(?:[a-z]+\s+)?council(?:l?ors?)?", "executive council"),
    (r"state\s+senat\w*", "state senate"),
)
UNITS = [
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "sixth",
    "seventh",
    "eighth",
    "ninth",
    "tenth",
    "eleventh",
    "twelfth",
    "thirteenth",
    "fourteenth",
    "fifteenth",
    "sixteenth",
    "seventeenth",
    "eighteenth",
    "nineteenth",
]
ORDINALS = {
    **{word: number for number, word in enumerate(UNITS, 1)},
    "twentieth": 20,
    **{f"twenty{word}": 20 + number for number, word in enumerate(UNITS[:9], 1)},
}


def heading(text: str) -> tuple[str, str] | None:
    plain = re.sub(r"\s+", " ", text).strip()
    found = re.match(r"For\s+(?:the\s+)?(.*?)[:;.\-]?$", plain, re.IGNORECASE)
    if found:
        for pattern, name in OFFICES:
            if re.fullmatch(pattern, found[1].strip(), re.IGNORECASE):
                return "office", name
        return "other", plain
    if re.search(r"COUNTY\s+OFFIC|^[A-Z]{4,12}\s+COUNTY:?$", plain):
        return "other", plain
    digit = re.fullmatch(r"district\s+(?:no\.?\s*)?(\d{1,2})\s*:?", plain, re.IGNORECASE)
    if digit:
        return "district", digit[1]
    word = re.fullmatch(r"([A-Za-z1.\\]+(?:[\s-]+[A-Za-z.]+)?)\s+D[a-z][A-Za-z.]{3,8}\s*[:;]?", plain)
    if word:
        match = difflib.get_close_matches(re.sub(r"[^a-z]", "", word[1].lower()), ORDINALS, n=1, cutoff=0.8)
        if match:
            return "district", str(ORDINALS[match[0]])
    return None


def summaries(path) -> list[tuple[str, list[int]]]:
    layout = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    pages = layout.split("\f")
    starts = [
        number
        for number, text in enumerate(pages, 1)
        if re.search(r"(?im)^\s*For\s+Governor\s*[:;.]?\s*(?:Number\s+of\s+Votes)?\s*$", text)
    ]
    out = []
    for start in starts:
        end = start
        while end < min(start + 12, len(pages)) and not re.search(
            r"(?m)^\s*COUNTY OFFIC|^\s*(?i:For\s+(the\s+)?Sheriff)", pages[end - 1]
        ):
            end += 1
        primary = bool(re.search(r"(?i)total\s+vote\s*,\s*[rd]\b", "\n".join(pages[start - 1 : end])))
        out.append(("primary" if primary else "general", list(range(start, end + 1))))
    return list({stage: (stage, pages) for stage, pages in out}.values())


def mended(rows: list[nh_manual.Row]) -> list[nh_manual.Row]:
    out = []
    for row in rows:
        words = list(row.words)
        if len(words) > 1 and words[0].text in ("I", "1", "l") and words[1].text == "otal":
            words = [words[0]._replace(text="Total", x1=words[1].x1), *words[2:]]
        if words and re.fullmatch(r"TotalVote,?", words[0].text):
            words = [words[0]._replace(text="Total"), words[0]._replace(text="Vote,"), *words[1:]]
        if words and re.fullmatch(r"[fFl1I]otal", words[0].text):
            words[0] = words[0]._replace(text="Total")
        if len(words) > 1 and words[0].text == "Total" and re.fullmatch(r"[Vv][a-z)]{1,3}e(,?)", words[1].text):
            words[1] = words[1]._replace(text="Vote,")
        if len(words) > 2 and words[0].text == "Total" and re.fullmatch(r"[rd][.,]?", words[1].text):
            words.insert(1, words[0]._replace(text="Vote,"))
        out.append(row._replace(words=words))
    return out


def parse() -> None:
    frames = []
    for year in nh_manual.years():
        try:
            path = nh_manual.pdf(year)
        except LookupError:
            print(f"{year}: not fetched")
            continue
        found = summaries(path)
        print(year, [(stage, pages[0], pages[-1]) for stage, pages in found])
        for stage, pages in found:
            page_words = nh_manual.words(path, pages[0], pages[-1])
            frame = pl.DataFrame(
                nh_manual.lines(mended(nh_manual.rows(page_words)), heading=heading), infer_schema_length=None
            )
            if frame.height:
                frames.append(frame.with_columns(year=pl.lit(year), stage=pl.lit(stage)))
    PARSED.mkdir(parents=True, exist_ok=True)
    stable(pl.concat(frames, how="diagonal_relaxed")).write_parquet(PARSED / "lines.parquet")


def stable(frame: pl.DataFrame) -> pl.DataFrame:
    if not (PARSED / "lines.parquet").exists():
        return frame.with_row_index("index").with_columns(pl.col("index").cast(pl.Int64))
    keys = ["year", "stage", "page", "x0", "y0", "x1", "y1"]
    old = indexed().select(*keys, "index", pl.col("group").alias("before"))
    assert not old.select(keys).is_duplicated().any()
    frame = frame.join(old, on=keys, how="left", maintain_order="left")
    top = int(old["index"].max() or 0)
    frame = frame.with_columns(index=pl.coalesce("index", top + 1 + pl.col("index").is_null().cum_sum().cast(pl.Int64)))
    ceiling = int(max(old["before"].max() or 0, frame["group"].max() or 0)) + 1
    mapped: dict[tuple[int, str, int], int] = {}
    for (year, stage), rows in frame.filter(pl.col("group").is_not_null()).group_by("year", "stage"):
        taken: set[int] = set()
        fresh = ceiling
        for group, block in rows.sort("index").group_by("group", maintain_order=True):
            before = block["before"].drop_nulls()
            choice = int(before.mode().sort()[0]) if before.len() else None
            if choice is None or choice in taken:
                choice, fresh = fresh, fresh + 1
            taken.add(choice)
            mapped[(int(year), str(stage), int(group[0]))] = choice
    frame = frame.with_columns(
        group=pl.struct("year", "stage", "group").map_elements(
            lambda row: mapped.get((row["year"], row["stage"], row["group"])), return_dtype=pl.Int64
        )
    )
    return frame.drop("before").sort("index")


READ_DPI = 150
READ_PROMPT = (
    "This is a page of the summary of New Hampshire statewide election returns. Below are the blocks of candidate "
    "lines this page holds, each with a block number, its office, and numbered lines (candidate and town). For each "
    "numbered line, read the vote count printed on that candidate's line in the page image. For each block, also read "
    "the number printed on its 'Total vote' line, or null if the page shows none for that block. Reply with only a "
    'JSON object {"lines": {"<line number>": <integer or null>}, "totals": {"<block number>": <integer or null>}}. '
    "Use null where the number cannot be read. Do not add, correct, or compute numbers."
)
JOB = "summary_pages"


def pending(frame: pl.DataFrame) -> pl.DataFrame:
    frame = nh_manual.blocks(frame.drop("added", "agrees", "printed_right", strict=False))
    return frame.filter(pl.col("agrees").is_null() | ~pl.col("agrees"))


def reread() -> str:
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming

    from ea import batch

    frame = pending(arbitrated(indexed(), vision=False))
    folder = DATA / "tmp" / SOURCE
    folder.mkdir(parents=True, exist_ok=True)
    price_in, price_out = batch.PRICES["claude-haiku-5-5"]
    wanted: dict[str, batch.Want] = {}
    for (year, page), rows in frame.group_by("year", "page", maintain_order=True):
        image = folder / f"{year}-{page}.png"
        if not image.exists():
            stem = folder / f".{year}-{page}"
            subprocess.run(
                ["pdftoppm", "-r", str(READ_DPI), "-png", "-singlefile", "-f", str(page), "-l", str(page)]
                + [str(nh_manual.pdf(int(year))), str(stem)],
                check=True,
            )
            stem.with_suffix(".png").replace(image)
        listing = []
        for group, block in rows.group_by("stage", "group", maintain_order=True):
            first = block.row(0, named=True)
            label = " ".join(
                str(part)
                for part in (first["office"], first["district"], first["total_party"] or first["party"])
                if part
            )
            listing.append(f"Block {block['year'][0]}{first['stage'][0]}{first['group']}: {label}")
            listing.extend(
                f"  Line {row['index']}: {row['name']}{', ' + row['town'] if row['town'] else ''}"
                for row in block.iter_rows(named=True)
            )
        params = MessageCreateParamsNonStreaming(
            model="claude-haiku-5-5",
            max_tokens=3000,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": base64.b64encode(image.read_bytes()).decode(),
                            },
                        },
                        {"type": "text", "text": f"{READ_PROMPT}\n\n" + "\n".join(listing)},
                    ],
                }
            ],
        )
        wanted[f"read:{year}:{page}"] = (params, (3500 * price_in + (200 + 14 * rows.height) * price_out) / 1e6)
    return batch.ensure(SOURCE, JOB, wanted)[1]


def seen() -> tuple[dict[int, int], dict[str, int | None]]:
    from ea import raw

    lines: dict[int, int] = {}
    totals: dict[str, int | None] = {}
    frame = pending(arbitrated(indexed(), vision=False))
    for year, page in frame.select("year", "page").unique().rows():
        try:
            text = json.loads(raw.cached(SOURCE, f"read:{year}:{page}").path.read_text())["content"][0]["text"]
        except LookupError:
            continue
        found = re.search(r"\{.*\}", text, re.DOTALL)
        if not found:
            continue
        try:
            reply = json.loads(found[0])
        except ValueError:
            continue
        for key, value in (reply.get("lines") or {}).items():
            if isinstance(value, int) and key.isdigit():
                lines[int(key)] = value
        for key, value in (reply.get("totals") or {}).items():
            if isinstance(value, int):
                totals[key] = value
    return lines, totals


SUPERSEDES = ("us.wikipedia_gov_history", "us.klarner")
COUNCIL_PLANS = {
    1970: (None, date(1982, 1, 1), "used in the 1970-1980 elections"),
    1982: (date(1982, 1, 1), date(1992, 1, 1), "1981 plan"),
    1992: (date(1992, 1, 1), date(2002, 1, 1), "1992 plan"),
    2002: (date(2002, 1, 1), date(2012, 1, 1), "2002 plan"),
    2012: (date(2012, 1, 1), date(2022, 1, 1), "2012 plan"),
}
COUNCIL_FIXES = {
    "Earl A. Rinker III. Auburn": "Earl A. Rinker III",
    "Earl A. Rinker HI": "Earl A. Rinker III",
    "Earl A. Rmker IH": "Earl A. Rinker III",
    "Raymond S, Burton": "Raymond S. Burton",
    "Lou D'AUesandro": "Lou D'Allesandro",
    "Lou DAllesandro": "Lou D'Allesandro",
    "M. Edna Lamoniagne": "M. Edna Lamontagne",
    "Albert E. PaqueLte": "Albert E. Paquette",
    "Jean R.Wallin": "Jean R. Wallin",
    "Earl A. Rinker IB": "Earl A. Rinker III",
    "Ra>mond S. Burton": "Raymond S. Burton",
    "John 1) Shea": "John D. Shea",
    "John D. Shea. Nel.son": "John D. Shea",
    "Bemie G. Sparks": "Bernie G. Sparks",
    "Beth Funicclla": "Beth Funicella",
    "Bev FFollingworth": "Bev Hollingworth",
    "Bev Hollingvvorth": "Bev Hollingworth",
    "Bev Hollmgworth": "Bev Hollingworth",
    "Beverly HoUingworth": "Beverly Hollingworth",
    "Chnstopher Sununu": "Christopher Sununu",
    "Chuck Rolecdc Bedfofxl": "Chuck Rolecek",
    "Colin Van Ostem": "Colin Van Ostern",
    "Dan St Hilaire": "Dan St. Hilaire",
    "David K. WTieeler": "David K. Wheeler",
    "Debwa B. Pignatelli": "Debora B. Pignatelli",
    "Kenneth E. Elevens": "Kenneth E. Blevens",
    "MichaelJ. Baldassarre": "Michael J. Baldassarre",
    "Nick Mart": "Nick Hart",
    "Peter J. Spauldmg": "Peter J. Spaulding",
    "Peter M. Estabrooks": "Peter H. Estabrooks",
    "Ramsay McLauchian": "Ramsay McLauchlan",
    "Robert Bums": "Robert Burns",
    "Timothy R Dillon": "Timothy P. Dillon",
    "Bernard A. Streeter. Jr.": "Bernard A. Streeter, Jr.",
}
PARTY_KEYS = {"r": "republican-party", "d": "democratic-party"}
TOWN = re.compile(r"(.+[a-z])[.,]\s+([A-Z][a-z]+(?: [A-Z][a-z]+)?)")


def indexed() -> pl.DataFrame:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    if "index" in frame.columns:
        return frame.select("index", pl.exclude("index"))
    return frame.with_row_index("index").with_columns(pl.col("index").cast(pl.Int64))


def second(frame: pl.DataFrame) -> pl.DataFrame:
    lines, totals = seen()
    frame = frame.with_columns(
        key=pl.col("year").cast(pl.String) + pl.col("stage").str.slice(0, 1) + pl.col("group").cast(pl.String),
        vision=pl.col("index").replace_strict(lines, default=None, return_dtype=pl.Int64),
    )
    frame = frame.with_columns(
        vision_total=pl.col("key").replace_strict(totals, default=None, return_dtype=pl.Int64),
        read=pl.col("vision").is_not_null().all().over("year", "stage", "group"),
        vision_sum=pl.col("vision").sum().over("year", "stage", "group"),
        text_agrees=(pl.col("vision") == pl.col("votes")).all().over("year", "stage", "group"),
    )
    target = pl.coalesce("printed", "vision_total")
    fixed = pl.col("read") & ~pl.coalesce("agrees", pl.lit(False)) & (pl.col("vision_sum") == target)
    return frame.with_columns(
        votes=pl.when(fixed).then(pl.col("vision")).otherwise(pl.col("votes")),
        printed=pl.when(fixed).then(target).otherwise(pl.col("printed")),
        reads_agree=pl.col("read")
        & pl.col("text_agrees")
        & pl.col("printed").is_null()
        & pl.col("vision_total").is_null(),
        resolved=fixed,
    ).drop("key", "vision", "vision_total", "read", "vision_sum", "text_agrees")


def arbitrated(frame: pl.DataFrame, vision: bool = True) -> pl.DataFrame:
    frame = nh_manual.verified(frame.drop("added", "agrees", strict=False))
    swaps: dict[int, int] = {}
    for _, rows in frame.filter(pl.col("printed").is_not_null() & ~pl.col("agrees")).group_by("year", "stage", "group"):
        gap = int(rows["added"][0]) - int(rows["printed"][0])
        fits = []
        for index, votes, digits in rows.select("index", "votes", "digits").iter_rows():
            tokens = (digits or "").split()
            if len(tokens) > 1 and re.fullmatch(r"\d{1,3}(?:,\d{3})*", tokens[-1]):
                alone = int(tokens[-1].replace(",", ""))
                if votes - alone == gap:
                    fits.append((index, alone))
        if len(fits) == 1:
            swaps[fits[0][0]] = fits[0][1]
    frame = frame.with_columns(
        votes=pl.col("index").replace_strict(swaps, default=pl.col("votes"), return_dtype=pl.Int64),
        clean=pl.col("clean") & ~pl.col("index").is_in(list(swaps)),
    )
    if vision:
        frame = second(nh_manual.verified(frame.drop("added", "agrees", "printed_right", strict=False)))
    frame = nh_manual.blocks(frame.drop("added", "agrees", "printed_right", strict=False))
    cut = pl.col("digits").str.starts_with(",").any().over("year", "stage", "group") & ~(
        pl.col("resolved").any().over("year", "stage", "group") if vision else pl.lit(False)
    )
    return frame.with_columns(agrees=pl.when(cut).then(False).otherwise(pl.col("agrees")))


def plain(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


def split(name: str, town: str | None) -> tuple[str, str | None]:
    name = re.sub(r"\b([A-Z]),\s+(?=[A-Z][a-z])", r"\1. ", name)
    stranded = nh_manual.STRANDED.fullmatch(name)
    if stranded:
        return stranded[1], stranded[2]
    name = re.sub(r"(?<=[a-z])[.,]?\s+(?:d|r)$", "", name)
    if town is not None and not re.match(r"[A-Z][a-z]", town) and TOWN.fullmatch(name):
        town = None
    suffixed = re.fullmatch(r"(.+?),? (Jr|Sr|II|III|IV)\.?,\s+([A-Z][a-z]+(?: [A-Z][a-z]+)?)", name)
    if suffixed:
        return f"{suffixed[1]}, {suffixed[2]}{'.' if suffixed[2] in ('Jr', 'Sr') else ''}", suffixed[3]
    if town and re.fullmatch(r"(?i)(?:r|d|l|lib|ind)(?:\s*(?:and|&)\s*(?:r|d|l|lib|ind))*", town) and "," in name:
        name, town = (part.strip() for part in name.rsplit(",", 1))
    if town and not re.search(r"[A-Za-z]{3}", town):
        town = None
    title = re.fullmatch(r"(Jr|Sr|II|III|IV)\.?\s+(.+)", town or "")
    if title:
        name, town = f"{name}, {title[1]}{'.' if title[1] in ('Jr', 'Sr') else ''}", title[2]
    found = TOWN.fullmatch(name) if town is None else None
    if found and not re.fullmatch(r"(?:Jr|Sr|II|III|IV)", found[2]):
        return found[1], found[2]
    return name, town


def suffix(office: str, district: str | None, stage: str, side: str | None) -> str | None:
    party = {"r": "republican", "d": "democratic"}.get(side or "")
    if office == "governor":
        return "governor" if stage == "general" else f"governor:{party}"
    if office == "us senate":
        return "senate" if stage == "general" else f"senate:{party}"
    if office == "us house" and district:
        return f"house:{district}" if stage == "general" else f"house:{district}:{party}"
    if office == "state senate" and district:
        return f"senate:{district}:g" if stage == "general" else f"senate:{district}:{side}p"
    if office == "executive council" and district:
        return f"executive-council:{district}" if stage == "general" else f"executive-council:{district}:{party}"
    return None


def named(office: str, district: str | None, year: int, side: str) -> tuple[str, str]:
    party = {"r": "Republican", "d": "Democratic"}[side]
    lower = party.lower()
    if office == "state senate":
        return (
            f"{year} New Hampshire Senate {party} primary, District {district}",
            f"state-senate-{district}-{lower}-primary",
        )
    if office == "executive council":
        return (
            f"{year} New Hampshire Executive Council {party} primary, District {district}",
            f"executive-council-{district}-{lower}-primary",
        )
    if office == "us house":
        ordinal = {"1": "1st", "2": "2nd"}[str(district)]
        return (
            f"{year} U.S. House {party} primary in New Hampshire's {ordinal} congressional district",
            f"house-{district}-{lower}",
        )
    return f"{year} U.S. Senate {party} primary in New Hampshire", f"senate-{lower}"


class Council:
    def __init__(self, cs, conn, contests, primaries, manual, located, report) -> None:
        self.cs, self.conn, self.contests, self.primaries = cs, conn, contests, primaries
        self.manual, self.located, self.report = manual, located, report
        self.body = cs.key("us.body", "NH:executive-council")
        cs.add("body", id=self.body, polity_id=cs.polity("US-NH"), kind="council")
        self.name(self.body, "New Hampshire Executive Council")
        self.system = cs.key("us.electoral_system", "fptp")
        self.state = cs.area("us.state", "33")
        self.areas: dict[tuple[int, str], tuple[object, object]] = {}
        self.people: dict[tuple[str, str], object] = {}
        self.known: dict[str, set[object]] = {}
        for label, person in conn.execute(
            """select distinct c.ballot_name, c.person_id from candidacy c
               join contest_option o on o.id = c.option_id
               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
               where x.value ~ '^(19[6-9]\\d|20[01]\\d)-\\d\\d-\\d\\d:NH:' and c.person_id is not null"""
        ).fetchall():
            self.known.setdefault(label, set()).add(person)

    def name(self, entity: object, text: str) -> None:
        if entity in self.cs.minted:
            self.cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

    def district(self, year: int, number: str) -> tuple[object, object]:
        plan = max(start for start in COUNCIL_PLANS if start <= year)
        if (plan, number) not in self.areas:
            cs = self.cs
            valid_from, valid_to, label = COUNCIL_PLANS[plan]
            code = f"33:executive-council:{number}:{plan}"
            area = cs.area("us.local_district", code)
            unit = cs.unit("us.local_district", code)
            cs.add("area", id=area, scheme_code="us.local_district")
            cs.add("area_code", area_id=area, code=code, valid_from=valid_from, valid_to=valid_to)
            cs.add("area_parent", area_id=area, parent_id=self.state, valid_from=valid_from, valid_to=valid_to)
            self.name(area, f"New Hampshire Executive Council District {number} ({label})")
            if valid_from is None:
                cs.add(
                    "note",
                    entity_id=area,
                    kind="methodology",
                    body_md="The Manual for the General Court dates the council districts used from 1982 to a 1981 plan; this area stands for the district as used in the 1970 through 1980 elections, without checking whether it was redrawn within that decade.",
                )
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=cs.polity("US-NH"),
                scheme_code="us.local_district",
                code=code,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=valid_from,
                valid_to=valid_to,
            )
            self.areas[(plan, number)] = (area, unit)
        return self.areas[(plan, number)]

    def person(self, label: str) -> object:
        key = nh_manual.person(label)
        if key not in self.people:
            hits = self.known.get(label, set())
            if len(hits) == 1:
                self.people[key] = next(iter(hits))
            else:
                person = self.cs.key(f"{SOURCE}.person", f"executive-council:{key[0]}:{key[1]}")
                if person in self.cs.minted:
                    self.cs.add("person", id=person)
                self.name(person, label)
                self.people[key] = person
        return self.people[key]

    def election(self, value: str) -> object | None:
        found = self.conn.execute(
            "select entity_id from external_id where scheme = 'us.election' and value = %s", [value]
        ).fetchone()
        return found[0] if found else None

    def load(self, year: int, stage: str, number: str, side: str | None, rows: pl.DataFrame, tag: str) -> None:
        cs, report = self.cs, self.report
        printed = set(rows["printed"].drop_nulls().to_list())
        checked = (
            (rows["agrees"].is_not_null().all() or rows["reads_agree"].all())
            and rows["agrees"].drop_nulls().all()
            or int(rows["votes"].sum()) in printed
        )
        if not checked:
            report.setdefault("executive council: lines do not add to a printed total", []).append(tag)
            return
        if stage == "general":
            governor = self.contests.get((year, "general", "governor"))
            day = governor[0][:10] if governor else None
            election = self.election(f"{day}:NH") if day else None
        else:
            day = self.primaries.get(year)
            election = self.election(f"{day}:NH:primary") if day else None
        if election is None:
            report.setdefault("executive council: no election", []).append(tag)
            return
        labels = []
        for name, town in rows.select("name", "town").iter_rows():
            name = COUNCIL_FIXES.get(name, name)
            name = COUNCIL_FIXES.get(split(name, town)[0], split(name, town)[0])
            labels.append(None if re.fullmatch(r"(?i)scatter\w*|write-?ins?", name) else name)
        bad = [
            label
            for label in labels
            if label and (not nh_manual.CLEAN.fullmatch(label) or len(nh_manual.person(label)[0]) < 3)
        ]
        if bad:
            report.setdefault("executive council: not a clean name", []).append(f"{tag}: {bad}")
            return
        rows = rows.with_columns(label=pl.Series(labels, dtype=pl.String))
        external = f"{day}:NH:{suffix('executive council', number, stage, side)}"
        contest = cs.key("us.contest", external)
        if contest not in cs.minted:
            default = self.conn.execute(
                """select c.pipeline from result_set rs join changeset c on c.id = rs.changeset_id
                   where rs.contest_id = %s and rs.is_default and rs.status = 'active'""",
                [contest],
            ).fetchone()
            if default and default[0] != SOURCE:
                report.setdefault(f"executive council: kept {default[0]}", []).append(tag)
                return
        area, unit = self.district(year, number)
        cs.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=self.body,
            district_area_id=area,
            magnitude=1,
            term="full",
            round_number=1,
            electoral_system_id=self.system,
            party_scope_id=cs.key("us.party", PARTY_KEYS[str(side)]) if stage == "primary" else None,
            default_result_kind="counted",
        )
        if stage == "general":
            text, path = (
                f"{year} New Hampshire Executive Council election, District {number}",
                f"executive-council-{number}",
            )
        else:
            text, path = named("executive council", number, year, str(side))
        self.name(contest, text)
        cs.add("slug", path=f"us/nh/{day}-{stage}/{path}", entity_id=contest)
        options: dict[str | None, object] = {}
        tally = []
        for position, (label, block) in enumerate(rows.group_by("label", maintain_order=True), 1):
            label = label[0]
            if label is None:
                option = cs.key("us.option", f"{external}:others")
                cs.add("contest_option", id=option, contest_id=contest, kind="others", ballot_label="Others")
            else:
                parties = set(block["party"].drop_nulls().to_list())
                party = PARTY_KEYS.get(next(iter(parties))) if stage == "general" and len(parties) == 1 else None
                option = cs.key("us.option", f"{external}:{slug(label)}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=cs.key("us.party", party) if party else None,
                    ballot_position=position,
                    ballot_label=label,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{external}:{slug(label)}:head"),
                    person_id=self.person(label),
                    option_id=option,
                    role="head",
                    ballot_name=label,
                    is_independent=stage == "general" and parties <= {"i", "ind"} and bool(parties),
                )
                tally.append((int(block["votes"].sum()), option))
            options[label] = option
        source = self.manual(year)[0]
        result_set = cs.key(f"{SOURCE}.result_set", external)
        cs.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="certified",
            kind="counted",
            status="active",
            source_id=source,
            is_default=True,
            completeness=1,
        )
        cs.add("external_id", entity_id=result_set, scheme="ea.authoritative", value=str(result_set))
        ranked = sorted(tally, key=lambda entry: -entry[0])
        if ranked and ranked[0][0] > 0 and (len(ranked) == 1 or ranked[0][0] > ranked[1][0]):
            cs.add("outcome", result_set_id=result_set, option_id=ranked[0][1], kind="won", seats=None)
        for (label,), block in rows.group_by("label", maintain_order=True):
            self.located(year, result_set, unit, options[label], int(block["votes"].sum()), block)
        report.setdefault("executive council: loaded", []).append(tag)


def emit():
    from ea import raw
    from ea.changeset import Changeset
    from ea.store import connect

    lines = arbitrated(indexed())
    lines = (
        lines.with_columns(
            pl.struct("name", "town")
            .map_elements(
                lambda row: dict(zip(("name", "town"), split(row["name"], row["town"]), strict=True)),
                return_dtype=pl.Struct({"name": pl.String, "town": pl.String}),
            )
            .alias("fixed")
        )
        .drop("name", "town")
        .unnest("fixed")
    )
    only = os.environ.get("NH_YEARS")
    if only:
        lines = lines.filter(pl.col("year").is_in([int(year) for year in only.split(",")]))
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Hampshire's governor, U.S. Senate, U.S. House, State Senate, and Executive Council returns, primaries and generals, 1970-2014, read from the statewide summaries in the Secretary of State's Manual for the General Court",
            polity="US-NH",
        )
        contests: dict[tuple[int, str, str], tuple[str, object]] = {}
        for value, contest in conn.execute(
            """select value, entity_id from external_id
               where scheme = 'us.contest' and value ~ '^(19[7-9]\\d|200\\d|201[0-4])-(09|11)-\\d\\d:NH:'
               order by value"""
        ).fetchall():
            month = value[5:7]
            contests[(int(value[:4]), "general" if month == "11" else "primary", value[14:])] = (value, contest)
        primaries = {year: value[:10] for (year, stage, _), (value, _) in contests.items() if stage == "primary"}
        sources: dict[int, tuple[object, object, object, str]] = {}
        pages: dict[tuple[int, int], object] = {}
        report: dict[str, list[str]] = {}
        values = []

        def manual(year: int) -> tuple[object, object, object, str]:
            if year not in sources:
                path = nh_manual.pdf(year)
                artifact = None
                for kind in ("_bw", "") if year != 2008 else ("",):
                    try:
                        artifact = raw.cached(nh_manual.SOURCE, nh_manual.link(year, kind))
                        break
                    except LookupError:
                        continue
                assert artifact is not None
                title = f"Manual for the General Court, {year + 1}"
                source = cs.source(nh_manual.PUBLISHER, title)
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=nh_manual.PUBLISHER,
                    title=title,
                    url="https://scholars.unh.edu/court/61/"
                    if year == 2008
                    else f"https://archive.org/details/{nh_manual.ITEMS[year]}",
                    license="Public domain",
                )
                sources[year] = (source, cs.artifact(artifact, source), path, artifact.sha256)
            return sources[year]

        def located(year: int, result_set: object, unit: object, option: object, votes: int, mine: pl.DataFrame):
            _, artifact_id, path, sha = manual(year)
            first_page = int(mine["page"][0])
            mine = mine.filter(pl.col("page") == first_page)
            if (year, first_page) not in pages:
                image, width, height = nh_manual.render(path, sha, first_page)
                page = cs.page(artifact_id, first_page)
                pages[(year, first_page)] = page
                cs.add(
                    "source_page",
                    id=page,
                    artifact_id=artifact_id,
                    page_number=first_page,
                    image_key=f"pages/{nh_manual.SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
            values.append(
                {
                    "result_set_id": str(result_set),
                    "unit_id": str(unit),
                    "option_id": str(option),
                    "channel": 0,
                    "measure": 1,
                    "count_round": 0,
                    "flags": nh_manual.DIGITIZED | (nh_manual.DERIVED if mine.height > 1 else 0),
                    "value": votes,
                    "page_id": str(pages[(year, first_page)]),
                    "x0": float(mine["x0"].min()),
                    "y0": float(mine["y0"].min()),
                    "x1": float(mine["x1"].max()),
                    "y1": float(mine["y1"].max()),
                }
            )

        council = Council(cs, conn, contests, primaries, manual, located, report)
        keyed = lines.group_by("year", "stage", "office", "district", "side", maintain_order=True)
        for (year, stage, office, district, side), rows in keyed:
            tag = f"{year} {stage} {office} {district or ''} {side or ''}".strip()
            if office == "executive council":
                council.load(int(year), str(stage), str(district), side, rows, tag)
                continue
            wanted = suffix(str(office), district, str(stage), side)
            found = contests.get((int(year), str(stage), wanted or ""))
            template = None
            if found is None and stage == "primary" and wanted and side and int(year) in primaries:
                general = contests.get((int(year), "general", suffix(str(office), district, "general", None) or ""))
                if general is not None:
                    template = general[1]
                    found = (f"{primaries[int(year)]}:NH:{wanted}", None)
            if found is None:
                report.setdefault("no contest", []).append(tag)
                continue
            external, contest = found
            if rows.filter(pl.col("agrees").is_null()).height and not rows["reads_agree"].all():
                report.setdefault("no printed total", []).append(tag)
                continue
            if rows.filter(pl.col("agrees").is_not_null()).height and not rows["agrees"].all():
                report.setdefault("lines do not add to the printed total", []).append(tag)
                continue
            default = conn.execute(
                """select rs.id, c.pipeline, rs.election_id, rs.supersedes_id, array_agg(distinct v.unit_k)
                   from result_set rs join changeset c on c.id = rs.changeset_id
                   left join result_value v on v.result_set_k = rs.k
                   where rs.contest_id = %s and rs.is_default and rs.status = 'active' group by 1, 2, 3, 4""",
                [template or contest],
            ).fetchone()
            if template is not None:
                election = conn.execute(
                    "select entity_id from external_id where scheme = 'us.election' and value = %s",
                    [f"{primaries[int(year)]}:NH:primary"],
                ).fetchone()
                if default is None or election is None:
                    report.setdefault("no unit", []).append(tag)
                    continue
                election = election[0]
                units = [unit for unit in default[4] if unit is not None]
                if len(units) != 1:
                    report.setdefault("general has more than one unit", []).append(tag)
                    continue
                unit = conn.execute("select id from reporting_unit where k = %s", [units[0]]).fetchone()[0]
                default = None
            elif default is not None and default[1] not in (*SUPERSEDES, SOURCE):
                report.setdefault(f"kept {default[1]}", []).append(tag)
                continue
            own = default is not None and default[1] == SOURCE
            if template is None and default is not None:
                election = default[2]
                units = [unit for unit in default[4] if unit is not None]
                if len(units) != 1:
                    report.setdefault("default has more than one unit", []).append(tag)
                    continue
                unit = conn.execute("select id from reporting_unit where k = %s", [units[0]]).fetchone()[0]
            elif template is None and office in ("governor", "us senate"):
                election = conn.execute("select election_id from contest where id = %s", [contest]).fetchone()[0]
                unit = cs.unit("us.state", "33")
            elif template is None:
                report.setdefault("no unit", []).append(tag)
                continue
            people: list[tuple[str, object]] = (
                conn.execute(
                    """select o.ballot_label, c.person_id from contest_option o
                       join candidacy c on c.option_id = o.id and c.role = 'head'
                       where o.contest_id = %s and o.kind = 'candidate'""",
                    [template],
                ).fetchall()
                if template is not None
                else []
            )
            known: dict[str, tuple[str, object]] = {}
            options = conn.execute(
                """select o.id::text, o.ballot_label, o.kind,
                          coalesce((select sum(v.value) from result_value v join result_set rs on rs.k = v.result_set_k
                                    where rs.id = %s and v.option_k = o.k and v.measure = 1), 0)::bigint
                   from contest_option o where o.contest_id = %s and o.parent_option_id is null""",
                [default[0] if default else None, contest],
            ).fetchall()
            others = [option[0] for option in options if option[2] in ("others", "write_in")]
            totals = rows.group_by("name", maintain_order=True).agg(
                pl.col("votes").sum(), pl.col("town").drop_nulls().first(), pl.col("writein").any()
            )
            matched: dict[str, str | None] = {}
            fresh: list[str] = []
            failed = None
            for name, _, town, writein in totals.iter_rows():
                mine = nh_manual.person(name)
                if name == "Scatter" or re.fullmatch(r"(?i)scatter\w*|write-?ins?", name):
                    matched[name] = None
                    continue
                hits = [
                    option
                    for option in options
                    if option[2] == "candidate"
                    and (
                        nh_manual.person(option[1])[0] == mine[0]
                        or len(mine[0]) > 4
                        and difflib.SequenceMatcher(None, nh_manual.person(option[1])[0], mine[0]).ratio() >= 0.8
                        and nh_manual.person(option[1])[1] == mine[1]
                    )
                ]
                if len(hits) > 1:
                    hits = [hit for hit in hits if plain(hit[1]) == plain(name)] or hits
                if len(hits) > 1:
                    hits = [hit for hit in hits if nh_manual.person(hit[1])[1] == mine[1]] or hits
                if len(hits) == 1:
                    matched[name] = hits[0][0]
                elif hits:
                    failed = f"{name} matches {[hit[1] for hit in hits]}"
                    break
                elif template is not None:
                    friends = [p for p in people if plain(p[0]) == plain(name)] or [
                        p for p in people if nh_manual.person(p[0]) == mine
                    ]
                    if len(friends) == 1:
                        known[name] = (name if nh_manual.CLEAN.fullmatch(name) else friends[0][0], friends[0][1])
                    elif not nh_manual.CLEAN.fullmatch(name) or len(mine[0]) < 3:
                        failed = f"{name} is not a clean name"
                        break
                    fresh.append(name)
                elif stage == "primary" or writein:
                    matched[name] = None
                elif town and nh_manual.CLEAN.fullmatch(name) and len(mine[0]) > 2:
                    fresh.append(name)
                else:
                    failed = f"{name} matches nothing"
                    break
            idle = [
                option[1]
                for option in options
                if option[2] == "candidate" and option[0] not in matched.values() and (default is None or option[3])
            ]
            loose = [name for name, hit in matched.items() if hit is None and name != "Scatter"] + fresh
            if not failed and len(idle) == 1 and len(loose) == 1:
                option = next(o for o in options if o[1] == idle[0] and o[2] == "candidate")
                if (
                    difflib.SequenceMatcher(None, plain(option[1]), plain(loose[0])).ratio() >= 0.75
                    or difflib.SequenceMatcher(
                        None, nh_manual.person(option[1])[0], nh_manual.person(loose[0])[0]
                    ).ratio()
                    >= 0.8
                ):
                    matched[loose[0]] = option[0]
                    fresh = [name for name in fresh if name != loose[0]]
                    idle, loose = [], []
            if not failed and idle and loose:
                failed = f"unmatched lines {loose} and unmatched options {idle}"
            if not failed and options and not [m for m in matched.values() if m and m not in others]:
                failed = f"no line matches the contest's options: {list(totals['name'])}"
            if not failed and None in matched.values() and len(others) > 1:
                failed = "more than one others option"
            if failed:
                report.setdefault("candidate mismatch", []).append(f"{tag} {external}: {failed}")
                continue
            if template is not None:
                contest = cs.key("us.contest", external)
                shape = dict(
                    zip(
                        ("kind", "office_id", "body_id", "district_area_id", "seat_id", "boundary_set_id")
                        + ("magnitude", "electoral_system_id", "term"),
                        conn.execute(
                            """select kind, office_id, body_id, district_area_id, seat_id, boundary_set_id, magnitude,
                                      electoral_system_id, term from contest where id = %s""",
                            [template],
                        ).fetchone(),
                        strict=True,
                    )
                )
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    **shape,
                    party_scope_id=cs.key("us.party", {"r": "republican-party", "d": "democratic-party"}[str(side)]),
                    round_number=1,
                    default_result_kind="counted",
                )
                text, path = named(str(office), district, int(year), str(side))
                if contest in cs.minted:
                    cs.add("name", entity_id=contest, text=text, lang="en", kind="official", is_preferred=True)
                cs.add("slug", path=f"us/nh/{primaries[int(year)]}-primary/{path}", entity_id=contest)
                report.setdefault("contest created from the manual", []).append(tag)
            added = []
            if None in matched.values():
                if not others:
                    option = cs.key("us.option", f"{external}:others")
                    added.append(("others", "Others", option))
                    others.append(str(option))
                matched = {name: hit or others[0] for name, hit in matched.items()}
            for name in fresh:
                option = cs.key("us.option", f"{external}:{slug(known.get(name, (name,))[0])}")
                added.append(("candidate", name, option))
                matched[name] = str(option)
            ours: dict[str, int] = {}
            for name, votes in totals.select("name", "votes").iter_rows():
                ours[str(matched[name])] = ours.get(str(matched[name]), 0) + votes
            theirs = {option[0]: option[3] for option in options if option[3]}
            if default is not None and any(ours.get(o, 0) != theirs.get(o, 0) for o in {*ours, *theirs}):
                gap = {
                    label: (theirs.get(oid, 0), ours.get(oid, 0))
                    for oid, label, _, _ in options
                    if theirs.get(oid, 0) != ours.get(oid, 0)
                }
                report.setdefault(f"differs from {default[1]}", []).append(f"{tag} {external} {gap}")
            declared = (
                conn.execute(
                    "select option_id::text, kind, seats from outcome where result_set_id = %s", [default[0]]
                ).fetchall()
                if default is not None
                else []
            )
            winners = {option for option, kind, _ in declared if kind in ("elected", "won")}
            ranked = sorted(((votes, option) for option, votes in ours.items() if option not in others), reverse=True)
            if winners and (
                len(winners) > len(ranked)
                or {option for votes, option in ranked if votes >= ranked[len(winners) - 1][0]} != winners
            ):
                report.setdefault("declared winner is not the top vote-getter", []).append(tag)
                continue
            for offset, (kind, name, option) in enumerate(added):
                if kind == "others":
                    cs.add("contest_option", id=option, contest_id=contest, kind="others", ballot_label="Others")
                    continue
                label, human = known.get(name, (name, None))
                if human is None:
                    human = cs.key(
                        f"{SOURCE}.person", f"{external.rsplit(':', 1)[0] if template else external}:{slug(name)}"
                    )
                    if human in cs.minted:
                        cs.add("person", id=human)
                        cs.add("name", entity_id=human, text=name, lang="en", kind="official", is_preferred=True)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    ballot_position=len(options) + offset + 1,
                    ballot_label=label,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{external}:{slug(label)}:head"),
                    person_id=human,
                    option_id=option,
                    role="head",
                    ballot_name=label,
                    is_independent=False,
                )
                if template is None:
                    report.setdefault("option added from the manual", []).append(f"{tag} {name}")
            source = manual(int(year))[0]
            result_set = cs.key(f"{SOURCE}.result_set", external)
            if default is not None and not own:
                old = dict(
                    zip(
                        COLUMNS,
                        conn.execute(
                            f"select {', '.join(COLUMNS)} from result_set where id = %s", [default[0]]
                        ).fetchone(),
                        strict=True,
                    )
                )
                cs.add("result_set", **old, status="superseded", is_default=False)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                supersedes_id=default[3] if own else default[0] if default else None,
                is_default=True,
                completeness=1,
            )
            cs.add("external_id", entity_id=result_set, scheme="ea.authoritative", value=str(result_set))
            for option, kind, seats in declared:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            if (
                template is not None
                and ranked
                and ranked[0][0] > 0
                and (len(ranked) == 1 or ranked[0][0] > ranked[1][0])
            ):
                cs.add("outcome", result_set_id=result_set, option_id=ranked[0][1], kind="won", seats=None)
            for option, votes in ours.items():
                mine = rows.filter(pl.col("name").is_in([name for name, hit in matched.items() if hit == option]))
                located(int(year), result_set, unit, option, votes, mine)
        for reason, items in report.items():
            print(f"{len(items)}: {reason}")
            for item in items[: int(os.environ.get("NH_SHOW", "25"))]:
                print("   ", item)
        print(
            f"{len(values)} values in {len(cs.rows.get('result_set', [])) - len([r for r in cs.rows.get('result_set', []) if r.get('status') == 'superseded'])} sets"
        )
        if values:
            frame = pl.DataFrame(
                values,
                schema_overrides={"channel": pl.Int16, "measure": pl.Int16, "count_round": pl.Int16, "flags": pl.Int16},
            )
            cs.frame("result_value", frame.drop("page_id", "x0", "y0", "x1", "y1"))
            cs.frame(
                "value_locator",
                frame.select(
                    "result_set_id",
                    "unit_id",
                    "option_id",
                    "channel",
                    "measure",
                    "count_round",
                    "page_id",
                    "x0",
                    "y0",
                    "x1",
                    "y1",
                ).with_columns(source_id=pl.lit(None, pl.String)),
            )
        return cs.write()
