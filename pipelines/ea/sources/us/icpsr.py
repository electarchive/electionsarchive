import io
import re
import subprocess
import tempfile
import unicodedata
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.icpsr"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DOWNLOADS = Path.home() / "Downloads"
STUDY = "https://www.icpsr.umich.edu/web/ICPSR/studies/{study}"
PUBLISHER = "Inter-university Consortium for Political and Social Research"
LICENSE = "ICPSR terms of use"
STUDIES = {
    "13": (
        "ICPSR_00013-V2.zip",
        "General Election Data for the United States, 1950-1990 (ICPSR 13)",
        ["0003", "0001", "0002", "0005", "0006", "0008", "0010", "0012", "0014", "0015"],
    ),
    "1": (
        "ICPSR_00001-V4.zip",
        "United States Historical Election Returns, 1824-1968 (ICPSR 1)",
        [f"{number:04d}" for number in range(1, 204)],
    ),
}
STATES = {
    1: "CT", 2: "ME", 3: "MA", 4: "NH", 5: "RI", 6: "VT", 11: "DE", 12: "NJ", 13: "NY", 14: "PA",
    21: "IL", 22: "IN", 23: "MI", 24: "OH", 25: "WI", 31: "IA", 32: "KS", 33: "MN", 34: "MO", 35: "NE",
    36: "ND", 37: "SD", 40: "VA", 41: "AL", 42: "AR", 43: "FL", 44: "GA", 45: "LA", 46: "MS", 47: "NC",
    48: "SC", 49: "TX", 51: "KY", 52: "MD", 53: "OK", 54: "TN", 56: "WV", 61: "AZ", 62: "CO", 63: "ID",
    64: "MT", 65: "NV", 66: "NM", 67: "UT", 68: "WY", 71: "CA", 72: "OR", 73: "WA", 82: "HI",
}  # fmt: skip
MISSPELLED = {
    ("FL", "dade"): "miamidade",
    ("HI", "oahu"): "honolulu",
    ("HI", "mauikalawao"): "maui",
    ("LA", "vermillion"): "vermilion",
    ("SD", "shannon"): "oglalalakota",
    ("NV", "ormsby"): "carsoncity",
    ("NV", "carson"): "carsoncity",
    ("KY", "mcclean"): "mclean",
    ("MS", "hines"): "hinds",
    ("CO", "delores"): "dolores",
}
FORMER = {
    ("VA", "nansemond"): ("Nansemond", "51800", date(1974, 1, 1)),
    ("VA", "princessanne"): ("Princess Anne", "51810", date(1963, 1, 1)),
    ("VA", "warwick"): ("Warwick", "51700", date(1958, 7, 16)),
    ("VA", "southnorfolk"): ("South Norfolk", "51550", date(1963, 1, 1)),
    ("VA", "elizabethcity"): ("Elizabeth City", "51650", date(1952, 7, 1)),
    ("VA", "cliftonforge"): ("Clifton Forge", "51005", date(2001, 7, 1)),
    ("VA", "southboston"): ("South Boston", "51083", date(1995, 6, 30)),
    ("SD", "washabaugh"): ("Washabaugh", "46071", date(1983, 1, 1)),
    ("SD", "armstrong"): ("Armstrong", "46041", date(1952, 11, 5)),
}
OFFICES = {"PRES": "president", "CONG": "house", "SEN": "senate", "H AL": "house"}
LABEL = re.compile(r"^\s*(?:VAR\s+0*(\d+)|V(\d+):?)\s+(.+?)\s*$")
VOTE = re.compile(r"^(\d)(\d\d) (\d) ([GMS]) (PRES|CONG|SEN|GOV|STATE|H AL) (\d{4}|OTHER|TOTAL|WALLACE) VOTE$")
PARTY = re.compile(r"Votes Cast for Party (\d{4}), (.+)$", re.MULTILINE)
SUMMARY = re.compile(r"^(\d{4}) (DEM|REP|OTHR|WALLACE) VTS (PRES|SENATE|HOUSE)$")
SUMMARIES = {
    "DEM": "0100",
    "REP": "0200",
    "OTHR": "OTHER",
    "WALLACE": "WALLACE",
    "PRES": "PRES",
    "SENATE": "SEN",
    "HOUSE": "CONG",
}
DISTRICT = re.compile(r"^(?:DISTRICT|CONG DIST NUMBER,? (\d{4})|(\d{4}) DIST NUMBR|CONGRESSIONAL DISTRICT)$")


def _artifact(study: str) -> raw.Artifact:
    url = f"{STUDY.format(study=study)}/{STUDIES[study][0]}"
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        path = DOWNLOADS / STUDIES[study][0]
        return raw.store(
            SOURCE,
            url,
            path.read_bytes(),
            suffix=".zip",
            retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
            media_type="application/zip",
        )


def fetch() -> None:
    for study in STUDIES:
        _artifact(study)


def _day(year: int) -> date:
    return date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7)


def parse() -> None:
    with connect() as conn:
        fips: dict[str, dict[str, str]] = {}
        for code, text, iso in conn.execute(
            """select u.code, n.text, p.iso_code from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               join reporting_unit s on s.scheme_code = 'us.state' and s.code = left(u.code, 2)
               join polity p on p.id = s.polity_id
               where u.scheme_code = 'us.county'"""
        ).fetchall():
            plain = re.sub(
                r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower().replace("saint ", "st ").replace("&", "and")
            )
            names_ = fips.setdefault(iso.removeprefix("US-"), {})
            if int(code[2:]) >= 500:
                names_[f"{plain}city"] = code
            names_.setdefault(plain, code)
    parties: dict[str, str] = {}
    frames: list[pl.DataFrame] = []
    for study, (_, _, datasets) in STUDIES.items():
        archive = zipfile.ZipFile(_artifact(study).path)
        names = archive.namelist()
        for dataset in datasets:
            with tempfile.TemporaryDirectory() as folder:
                pdf = Path(folder) / "codebook.pdf"
                pdf.write_bytes(
                    archive.read(
                        next(
                            n
                            for n in names
                            if f"/DS{dataset}/" in n and n.endswith(("-Codebook.pdf", "-Codebook-ICPSR.pdf"))
                        )
                    )
                )
                text = subprocess.run(
                    ["pdftotext", "-layout", str(pdf), "-"], check=True, capture_output=True, text=True
                ).stdout
            labels: dict[int, str] = {}
            for line in text.splitlines():
                found = LABEL.match(line)
                if found:
                    number = int(found[1] or found[2])
                    label = re.sub(r"\s{2,}.*$", "", found[3]).strip()
                    summary = SUMMARY.match(label)
                    if summary:
                        label = f"{summary[1][1:]} 0 G {SUMMARIES[summary[3]]} {SUMMARIES[summary[2]]} VOTE"
                    if number not in labels and (VOTE.match(label) or re.search(r"STATE|COUNTY|DIST", label)):
                        labels[number] = label
            for code, label in PARTY.findall(text):
                parties.setdefault(code, re.sub(r"\s+", " ", label).strip())
            frame = pl.read_csv(
                io.BytesIO(archive.read(next(n for n in names if f"/DS{dataset}/" in n and n.endswith("-Data.tsv")))),
                separator="\t",
                infer_schema_length=0,
            )
            state = next(n for n, label in labels.items() if re.fullmatch(r"(ICPR |ICPSR )?(STATE|COUNTY)( CODE)?", label) and label != "ICPSR COUNTY CODE")
            county = next(n for n, label in labels.items() if label == "COUNTY NAME")
            districts = {
                int(found[1] or found[2] or 0): n
                for n, label in labels.items()
                if (found := DISTRICT.match(label)) is not None
            }
            votes = []
            for number, label in labels.items():
                found = VOTE.match(label)
                if found is None or found[4] != "G" or found[5] not in OFFICES or found[6] == "TOTAL":
                    continue
                year = 1000 + int(found[1] + found[2])
                if study == "1" and year >= 1950:
                    continue
                district = districts.get(year, districts.get(0))
                votes.append(
                    frame.select(
                        state=pl.col(f"V{state}").cast(pl.Int16, strict=False),
                        county=pl.col(f"V{county}").str.strip_chars(),
                        district=(
                            pl.lit("98")
                            if found[5] == "H AL"
                            else pl.col(f"V{district}")
                            if district
                            else pl.lit(None, pl.String)
                        ).cast(pl.Int16, strict=False),
                        value=pl.col(f"V{number}").str.strip_chars().cast(pl.Int64, strict=False),
                    ).with_columns(
                        study=pl.lit(study),
                        dataset=pl.lit(dataset),
                        year=pl.lit(year, pl.Int16),
                        office=pl.lit(OFFICES[found[5]]),
                        seat=pl.lit(int(found[3]), pl.Int16),
                        party=pl.lit(found[6]),
                    )
                )
            if votes:
                frames.append(pl.concat(votes))
    frame = pl.concat(frames).filter(
        pl.col("value").is_not_null() & (pl.col("value") != 9999999) & (pl.col("value") > 0)
    )
    frame = frame.with_columns(postal=pl.col("state").replace_strict(STATES, default=None)).filter(
        pl.col("postal").is_not_null()
    )
    plain = (
        pl.col("county")
        .str.split("/")
        .list.first()
        .str.to_lowercase()
        .str.replace_all(r"^st\.? |\bsaint ", "st ")
        .str.replace_all("&", "and")
        .str.replace_all(r"\b(county|parish)\b", "")
        .str.replace_all(r"[^a-z]", "")
    )
    for (postal, typo), right in MISSPELLED.items():
        fips[postal][typo] = fips[postal][right]

    def code(postal: str, text: str) -> str | None:
        found = fips.get(postal, {})
        if text in found:
            return found[text]
        if (postal, text) in FORMER:
            return f"{postal}:{text}"
        near = {value for key, value in found.items() if len(text) >= 12 and key.startswith(text)}
        return near.pop() if len(near) == 1 else None

    frame = frame.with_columns(plain=plain)
    frame = frame.with_columns(
        code=pl.struct("postal", "plain").map_elements(
            lambda row: code(row["postal"], row["plain"]), return_dtype=pl.String
        )
    ).drop("plain")
    lost = frame.filter(pl.col("code").is_null()).group_by("postal", "county").agg(pl.col("value").sum())
    if lost.height:
        print(lost.sort("value", descending=True).head(60))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "values.parquet")
    pl.DataFrame({"party": list(parties), "name": list(parties.values())}).write_parquet(PARSED / "parties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "values.parquet")
    names = dict(pl.read_parquet(PARSED / "parties.parquet").iter_rows())
    names |= {"0100": "Democratic", "0200": "Republican", "0809": "Democratic Farmer Labor", "9999": "Scattering"}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="House, Senate, and presidential results by county, 1824–1990, from ICPSR 1 and 13",
            polity="US",
        )
        sources: dict[str, UUID] = {}
        for study, (_, title, _) in STUDIES.items():
            sources[study] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[study],
                kind="academic",
                publisher=PUBLISHER,
                title=title,
                url=STUDY.format(study=study),
                license=LICENSE,
            )
            cs.artifact(_artifact(study), sources[study])
        cs.add(
            "scheme", code="us.former_county", polity_id=cs.polity("US"), kind="synthetic", rank=2, noun="former county"
        )
        counties: dict[str, tuple[UUID, UUID]] = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county'"
            ).fetchall()
        }
        states: dict[str, UUID] = dict(
            conn.execute("select substr(iso_code, 4), id from polity where iso_code like 'US-%%'").fetchall()
        )
        units: dict[str, str] = {code: str(unit) for code, (unit, _) in counties.items()}
        for (postal, plain), (label, successor, ended) in FORMER.items():
            code = f"{postal}:{plain}"
            unit = cs.unit("us.former_county", code)
            units[code] = str(unit)
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=states[postal],
                scheme_code="us.former_county",
                code=code,
                parent_id=cs.unit("us.state", successor[:2]),
                area_relation="within",
                area_id=counties[successor][1],
                kind="standard",
                valid_from=None,
                valid_to=ended,
            )
            cs.add("name", entity_id=unit, text=label, lang="en", kind="official", is_preferred=True)
        contests: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest'").fetchall()
        )
        filled = {
            contest
            for (contest,) in conn.execute(
                """select distinct rs.contest_id from result_set rs join source s on s.id = rs.source_id
                   where not rs.is_default and rs.contest_id is not null and s.publisher <> %s""",
                [PUBLISHER],
            ).fetchall()
        }
        dated: dict[tuple[int, str, str], list[str]] = {}
        for key in contests:
            found = re.fullmatch(r"(\d{4})(?:-\d\d){0,2}:([A-Z]{2}):(house:\d+|senate|senate:special|president)", key)
            if found:
                dated.setdefault((int(found[1]), found[2], found[3]), []).append(key)

        def resolve(study: str, year: int, postal: str, rest: str) -> str | None:
            if study == "13":
                key = f"{_day(year)}:{postal}:{rest}"
                return key if key in contests else None
            found = dated.get((year, postal, rest), [])
            return found[0] if len(found) == 1 else None

        single = {
            (int(year), postal)
            for year, postal in frame.filter(pl.col("office") == "house")
            .group_by("year", "postal")
            .agg(pl.col("district").filter(pl.col("district") < 50).n_unique().alias("n"))
            .filter(pl.col("n") == 1)
            .select("year", "postal")
            .iter_rows()
        }
        frame = frame.filter(pl.col("code").is_not_null())
        house = frame.filter(pl.col("office") == "house")
        house = house.filter((pl.col("district") < 50) | (pl.col("district") == 98)).with_columns(
            seat=pl.when(pl.col("district") == 98).then(0).otherwise(pl.col("district")).cast(pl.Int16)
        )
        groups: list[tuple[str, pl.DataFrame]] = []
        for (study, year, postal, seat), lines in house.group_by(
            "study", "year", "postal", "seat", maintain_order=True
        ):
            key = resolve(str(study), int(year), str(postal), f"house:{seat}")
            if key is None and (int(year), postal) in single:
                key = resolve(str(study), int(year), str(postal), "house:0")
            groups.append((key or f"{year}:{postal}:house:{seat}", lines))
        statewide = frame.filter(pl.col("office") != "house")
        for (study, year, postal, office), lines in statewide.group_by(
            "study", "year", "postal", "office", maintain_order=True
        ):
            seats = sorted(lines["seat"].unique())
            sizes = sorted(seats, key=lambda seat: -int(lines.filter(pl.col("seat") == seat)["value"].sum()))
            for seat, rest in zip(sizes, [str(office), f"{office}:special"], strict=False):
                key = resolve(str(study), int(year), str(postal), rest)
                groups.append((key or f"{year}:{postal}:{rest}", lines.filter(pl.col("seat") == seat)))
        skipped: dict[str, int] = {}
        values: list[pl.DataFrame] = []
        for key, lines in groups:
            contest = contests.get(key)
            if contest is None:
                skipped["no contest"] = skipped.get("no contest", 0) + 1
                continue
            if contest in filled:
                skipped["already by county"] = skipped.get("already by county", 0) + 1
                continue
            row = conn.execute(
                """select c.election_id, min(v.count_round),
                          coalesce(sum(v.value) filter (where v.measure = 4 and v.option_k = 0),
                                   sum(v.value) filter (where v.measure = 1))::bigint
                   from contest c join result_set rs on rs.contest_id = c.id and rs.is_default
                   join result_value v on v.result_set_k = rs.k and v.measure in (1, 4)
                   where c.id = %s group by 1""",
                [contest],
            ).fetchone()
            if row is None or row[1] is None or row[1] > 1 or not row[2]:
                skipped["no official total"] = skipped.get("no official total", 0) + 1
                continue
            election, first, official = row
            known = conn.execute(
                """select o.id, o.kind, lower(coalesce(n.text, '')),
                          (select sum(v.value)::bigint from result_value v
                           where v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1 and v.count_round = %s)
                   from contest_option o
                   join result_set rs on rs.contest_id = o.contest_id and rs.is_default
                   left join name n on n.entity_id = o.party_id and n.is_preferred and n.lang = 'en'
                   where o.contest_id = %s""",
                [first, contest],
            ).fetchall()
            known = [(option, kind, party, total) for option, kind, party, total in known if total]
            sums = dict(lines.group_by("party").agg(pl.col("value").sum()).iter_rows())
            counted = sum(sums.values())
            complete = abs(counted - official) <= max(50, official * 0.01)
            picked: dict[str, UUID | None] = {}
            free = list(known)
            for party in sorted(sums, key=lambda party: -sums[party]):
                words = set(re.findall(r"[a-z]+", names.get(party, "").lower())) - {"party", "of", "the", "and", "for"}
                if party == "9999":
                    match = [option for option in free if option[1] == "write_in"]
                elif party in ("0100", "0809"):
                    match = [option for option in free if "democratic" in option[2] or "farmer" in option[2]]
                elif party == "0200":
                    match = [option for option in free if "republican" in option[2]]
                elif words:
                    match = [
                        option
                        for option in free
                        if option[1] != "write_in" and words & set(re.findall(r"[a-z]+", option[2]))
                    ]
                else:
                    match = []
                if len(match) > 1:
                    match = sorted(match, key=lambda option: abs(option[3] - sums[party]))[:1]
                if not match and complete:
                    match = [option for option in free if abs(option[3] - sums[party]) <= max(10, option[3] * 0.01)]
                if len(match) == 1:
                    picked[party] = match[0][0]
                    free.remove(match[0])
                else:
                    picked[party] = None
            drifted = [
                (party, sums[party], total)
                for party, option in picked.items()
                if option is not None
                for known_option, _, _, total in known
                if known_option == option
                and (
                    sums[party] > total + max(50, total * 0.02)
                    or (complete and total >= official * 0.05 and abs(sums[party] - total) > max(50, total * 0.02))
                )
            ]
            for party, _, total in drifted:
                if party not in ("0100", "0200") and total < official * 0.05:
                    picked[party] = None
            drifted = [line for line in drifted if picked[line[0]] is not None]
            if drifted:
                print(f"{key}: {drifted} against the official totals, skipped")
                skipped["drifted"] = skipped.get("drifted", 0) + 1
                continue
            if "0100" in sums and picked.get("0100") is None or "0200" in sums and picked.get("0200") is None:
                print(f"{key}: no Democratic or Republican option, skipped")
                skipped["no major option"] = skipped.get("no major option", 0) + 1
                continue
            if counted > official * 1.02:
                print(f"{key}: the county sum {counted} exceeds the official {official}, skipped")
                skipped["overcounted"] = skipped.get("overcounted", 0) + 1
                continue
            result_set = cs.key(f"{SOURCE}.result_set", f"{key}:county")
            if result_set not in cs.minted:
                continue
            if None in picked.values():
                pool = cs.key("us.option", f"{key}:others:icpsr")
                cs.add("contest_option", id=pool, contest_id=contest, kind="others", ballot_label="Others")
                picked = {party: pool if option is None else option for party, option in picked.items()}
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[str(lines["study"][0])],
                is_default=False,
                completeness=round(counted / official, 4),
            )
            values.append(
                lines.select(
                    result_set_id=pl.lit(str(result_set)),
                    unit_id=pl.col("code").replace_strict(units),
                    option_id=pl.col("party").replace_strict({party: str(option) for party, option in picked.items()}),
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.lit(1, pl.Int16),
                    count_round=pl.lit(first, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    value="value",
                )
                .group_by("result_set_id", "unit_id", "option_id", "channel", "measure", "count_round", "flags")
                .agg(pl.col("value").sum())
            )
        print(skipped, len(values))
        cs.frame("result_value", pl.concat(values))
        return cs.write()
