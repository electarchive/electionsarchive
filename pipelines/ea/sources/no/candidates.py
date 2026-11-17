"""Candidate lists and personal votes that Valgdirektoratet publishes as spreadsheets, read into one row per candidate."""

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime

import polars as pl

from ea import raw

HOST = "https://www.valgdirektoratet.no/globalassets"
SHARD = "no.valg.kandidater"
# (year, election type) -> the file of every list and candidate, or of every candidate with personal votes.
FILES: dict[tuple[str, str], tuple[str, str]] = {
    ("2013", "ST"): (
        f"{HOST}/dokumenter-2017/historikk/valglister-og-kandidater-2013/eksport_kandidater_2013_130805.xls",
        ".xls",
    ),
    ("2017", "ST"): (f"{HOST}/dokumenter-2017/valglister-og-kandidater-2017/listekandidater2017.csv", ".csv"),
    ("2021", "ST"): (f"{HOST}/dokumenter-2021/lister-og-partier/valglisterogkandidaterstortingsvalget2021.csv", ".csv"),
    ("2025", "ST"): (
        f"{HOST}/valgdata/lister-og-kandidater/valgdistrikt/lister_og_kandidater_stortingsvalget_2025.csv",
        ".csv",
    ),
    ("2023", "FY"): (
        f"{HOST}/dokumenter-2023/om-valg/valgdata/valgte-kandidater/valgte_kandidater_fylkesting_2023---med-forbehold-om-endringer.xlsx",
        ".xlsx",
    ),
    ("2023", "KO"): (
        f"{HOST}/dokumenter-2023/om-valg/valgdata/valgte-kandidater/valgte_kandidater_kommunestyre_2023---med-forbehold-om-endringer-3.xlsx",
        ".xlsx",
    ),
}


@dataclass
class Candidate:
    contest: str  # the district's name for a Storting list, the county or municipality number for a council list
    party: str
    party_code: str | None
    position: int
    name: str
    born: date | None
    born_year: int | None
    gender: str | None
    residence: str | None
    votes: int | None = None
    elected: bool | None = None
    stemmetillegg: bool | None = None
    slengere: int | None = None


def fetch() -> None:
    for url, suffix in FILES.values():
        raw.fetch(SHARD, url, suffix=suffix)


def artifact(year: str, kind: str) -> raw.Artifact:
    return raw.cached(SHARD, FILES[(year, kind)][0])


def _day(text: str | None) -> date | None:
    if not text or not text.strip():
        return None
    return datetime.strptime(text.strip(), "%d.%m.%Y").date()


def _gender(text: str | None) -> str | None:
    return {"m": "male", "mann": "male", "k": "female", "kvinne": "female"}.get((text or "").strip().casefold())


def _text(value: object) -> str | None:
    text = " ".join(str(value).split()) if value is not None else ""
    return text or None


def _rows(art: raw.Artifact, encoding: str, delimiter: str) -> list[dict[str, str]]:
    text = art.path.read_bytes().decode(encoding)
    return list(csv.DictReader(io.StringIO(text), delimiter=delimiter))


def storting(year: str) -> list[Candidate]:
    """Every candidate on every list of a Storting election."""
    art = artifact(year, "ST")
    found: list[Candidate] = []
    if year == "2013":
        for row in pl.read_excel(art.path).iter_rows(named=True):
            found.append(
                Candidate(
                    contest=str(_text(row["county"])),
                    party=str(_text(row["party_name"])),
                    party_code=_text(row["party_id"]),
                    position=int(row["candidate_number"]),
                    name=str(_text(row["candidate_name"])),
                    born=None,
                    born_year=int(row["candidate_birthyear"]) if row["candidate_birthyear"] else None,
                    gender=_gender(row["candidate_gender"]),
                    residence=_text(row["candidate_residence"]),
                )
            )
    elif year == "2017":
        for row in _rows(art, "cp1252", ";"):
            found.append(
                Candidate(
                    contest=str(_text(row["Fylke"])),
                    party=str(_text(row["Parti"])),
                    party_code=_text(row["Partikode"]),
                    position=int(row["Kandidatnr."]),
                    name=str(_text(row["Navn"])),
                    born=_day(row["Fødselsdato"]),
                    born_year=None,
                    gender=_gender(row["Kjønn"]),
                    residence=None,
                )
            )
    elif year == "2021":
        for row in _rows(art, "utf-8", ","):
            found.append(
                Candidate(
                    contest=str(_text(row["valgdistrikt"])),
                    party=str(_text(row["partinavn"])),
                    party_code=_text(row["partikode"]),
                    position=int(row["kandidatnr"]),
                    name=str(_text(row["navn"])),
                    born=_day(row["fødselsdato"]),
                    born_year=None,
                    gender=_gender(row["kjønn"]),
                    residence=_text(row["bosted"]),
                )
            )
    elif year == "2025":
        for row in _rows(art, "utf-8-sig", ";"):
            found.append(
                Candidate(
                    contest=str(_text(row["Valgdistrikt"])),
                    party=str(_text(row["Partinavn"])),
                    party_code=None,
                    position=int(row["Kandidatnr"]),
                    name=str(_text(row["Navn"])),
                    born=_day(row["Fødselsdato"]),
                    born_year=None,
                    gender=_gender(row["Kjønn"]),
                    residence=_text(row["Bosted"]),
                )
            )
    else:
        raise ValueError(f"no candidate file for the {year} Storting election")
    return found


def council(year: str, kind: str) -> list[Candidate]:
    """Every candidate of a county or municipal council election, with personal votes and the elected flag."""
    art = artifact(year, kind)
    column = "fylkesnummer" if kind == "FY" else "kommunenummer"
    found: list[Candidate] = []
    for row in pl.read_excel(art.path).iter_rows(named=True):
        found.append(
            Candidate(
                contest=str(row[column]),
                party=str(_text(row["partinavn"])),
                party_code=None,
                position=int(row["listeplass"]),
                name=str(_text(row["navn"])),
                born=row["fødselsdato"],
                born_year=None,
                gender=None,
                residence=None,
                votes=int(row["personstemmer"]),
                elected=row["innvalgt"] == "Ja",
                stemmetillegg=row["stemmetillegg"] == "Ja" if "stemmetillegg" in row else None,
                slengere=int(row["slengere"]) if "slengere" in row else None,
            )
        )
    return found
