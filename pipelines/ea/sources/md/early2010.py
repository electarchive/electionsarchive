"""The early parliamentary election of 28 November 2010, from the printed polling-station tables of the Central
Electoral Commission's book on it: the count of the night, and the recount of 15 December 2010."""

from datetime import date
from pathlib import Path

from ea import raw
from ea.package import Package
from ea.sources.md import cec, printed
from ea.sources.md.cec import Station, Table

DAY = "2010-11-28"
BOOK = "http://a.cec.md/storage/ckfinder/files"
PARTS = (f"{BOOK}/5122_electorala_2010_28_nov_partea_i(1).pdf", f"{BOOK}/7859_electorala_2010_28_nov_partea_ii(1).pdf")
# Pages of the second part: the polling-station tables of the count and of the recount, then the circumscription
# tables of each, which print the national total.
STATIONS = (156, 321)
RECOUNT = (322, 489)
CIRCUMSCRIPTIONS = (490, 493)
CIRCUMSCRIPTIONS_RECOUNT = (494, 497)
SEATS = 101

# The competitors in ballot order, as the book's list of abbreviations names them.
LISTS = (
    "Partidul Național Liberal",
    "Partidul Democrat din Moldova",
    "Partidul Liberal",
    "Partidul „Moldova Unită – Единая Молдова”",
    "Partidul Popular Creștin Democrat",
    "Partidul Mișcarea ACȚIUNEA EUROPEANĂ",
    "Partidul Politic „Patrioții Moldovei”",
    "Partidul Politic „Pentru Neam și Țară”",
    "Partidul Republican din Moldova",
    "Partidul Politic „Alianța MOLDOVA NOASTRĂ”",
    "Partidul Umanist din Moldova",
    "Partidul Comuniștilor din Republica Moldova",
    "Partidul Liberal Democrat din Moldova",
    "candidatul independent Pleșca Valeriu",
    "Partidul Conservator",
    "candidatul independent Stati Gabriel",
    "Partidul Popular Republican",
    "Mișcarea Social-Politică „RAVNOPRAVIE”",
    "Partidul Social Democrat",
    "candidatul independent Stepaniuc Victor",
    "Mișcarea Social-Politică a Romilor din Republica Moldova",
    "candidatul independent Nazarenco Evgheni",
    "candidatul independent Russu Gheorghe",
    "Partidul Muncii",
    "candidatul independent Laguta Maia",
    "Partidul Politic Partidul Ecologist „Alianța Verde” din Moldova",
    "candidatul independent Țîmbalist Tatiana",
    "candidatul independent Cereteu Romeo",
    "candidatul independent Bîrladeanu Afanasie",
    "candidatul independent Bolotnicov Oleg",
    "candidatul independent Cazac Oleg",
    "candidatul independent Țaulean Vitalie",
    "candidatul independent Burghilă-Leonte Elena",
    "candidatul independent Cușnir Valentina",
    "candidatul independent Demian Alexandru",
    "candidatul independent Iachim Sergiu",
    "candidatul independent Axenova Natalia",
    "candidatul independent Lupașcu Vasile",
    "candidatul independent Banari Sergiu",
)
# Seats the Commission's decisions of 6 and 18 December 2010 give the lists that passed the threshold.
SEATED = {
    "Partidul Comuniștilor din Republica Moldova": 42,
    "Partidul Liberal Democrat din Moldova": 32,
    "Partidul Democrat din Moldova": 15,
    "Partidul Liberal": 12,
}
SYSTEM = cec.key("electoral_system", "parliament", "list", "2010")
RULES = {"formula": "hare", "threshold": {"level": "party", "scope": "national", "percent": 4}}
SYSTEM_NOTE = (
    "A single national constituency elects 101 deputies by party-list proportional representation. The Commission's "
    "decisions of 6 and 18 December 2010 exclude the parties and social-political movements with under 4% of the "
    "valid votes nationwide, and independent candidates with under 2%. The electoral quotient is the valid votes "
    "divided by 101; each list that passed the threshold receives its votes divided by the quotient, rounded down, "
    "and the seats left over go one at a time to each of those lists, starting with the one that holds the most. The "
    "stored formula is the nearest the engine offers and does not reproduce that procedure. The 2% threshold for "
    "independent candidates is not stored in the rules."
)


def fetch() -> None:
    for url in PARTS:
        raw.fetch(cec.SOURCE, url, suffix=".pdf")


def _table(path: Path, tables: tuple[int, int], totals: tuple[int, int]) -> Table:
    stations: list[Station] = []
    for circumscription, block in printed.circumscriptions(path, *tables).items():
        seen: set[str] = set()
        for row in block.rows:
            if row.number in seen:
                raise ValueError(f"circumscription {circumscription} numbers station {row.number} twice")
            seen.add(row.number)
            stations.append(
                Station(circumscription, f"{circumscription}/{row.number}", row.place, "", list(row.values))
            )
    national = printed.read(path, *totals, "national")
    total = national.top or national.bottom
    if total is None:
        raise ValueError("the national table prints no total row")
    labels = list(LISTS)
    return Table(stations, list(total), labels, [""] * len(labels), (0, 1, 2, 3, 5, 45), 6, 6 + len(labels))


def tables() -> tuple[Table, Table]:
    path = raw.cached(cec.SOURCE, PARTS[1]).path
    return _table(path, STATIONS, CIRCUMSCRIPTIONS), _table(path, RECOUNT, CIRCUMSCRIPTIONS_RECOUNT)


def party_ids() -> dict[str, str]:
    """The party id of every list on the ballot that is not a candidate."""
    return {label: cec.party_id(label) for label in LISTS if not label.startswith("candidatul independent")}


def _set(p: Package, election: str, contest: str, name: str, stage: str, default: bool, replaces: str | None) -> str:
    result_set = cec.key("result_set", name)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage=stage,
        kind="counted",
        source_id=cec.CEC_SOURCE,
        status="active",
        is_default=default,
        completeness=1.0,
        supersedes_id=replaces,
    )
    p.add(
        "note", id=cec.key("note", result_set), entity_id=result_set, kind="methodology", body_md=cec.METHOD, lang="en"
    )
    return result_set


def build(p: Package, day: str, first: date) -> None:
    for url in PARTS:
        p.artifact(raw.cached(cec.SOURCE, url), cec.CEC_SOURCE)
    count, recount = tables()
    if [s.code for s in count.stations] != [s.code for s in recount.stations]:
        raise ValueError("the recount prints other polling stations than the count")
    known = {row["id"] for row in cec.registry().rows["party"]}
    for label, id in party_ids().items():
        if id not in known:
            p.add("party", id=id, kind="party", polity_id=cec.MOLDOVA)
            p.name(id, label, "ro", "official")
    p.add("electoral_system", id=SYSTEM, polity_id=cec.MOLDOVA, rules=RULES, rules_version=1)
    p.add("note", id=cec.key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    election = cec._election(
        p,
        f"ps{day}",
        first,
        "general",
        "2010 Moldovan early parliamentary election",
        "Alegeri parlamentare anticipate din 28.11.2010",
        f"md/{day}-general",
    )
    contest = cec._contest(
        p,
        f"ps{day}",
        election,
        "list_allocation",
        "2010 Moldovan early parliamentary election",
        "Alegeri parlamentare anticipate 2010",
        f"md/{day}-general/parliament",
        body_id=cec.PARLIAMENT,
        magnitude=SEATS,
        electoral_system_id=SYSTEM,
    )
    options: list[str] = []
    for position, label in enumerate(LISTS, 1):
        option = cec.key("option", f"ps{day}", position)
        options.append(option)
        independent = label.startswith("candidatul independent")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate" if independent else "list",
            party_id=None if independent else cec.party_id(label),
            ballot_position=position,
            ballot_label=label,
        )
    units = cec._units(p, day, [count, recount])
    first_set = ""
    for name, t, stage, default in (
        (f"ps{day}", count, "official", False),
        (f"ps{day}-recount", recount, "recount", True),
    ):
        result_set = _set(p, election, contest, name, stage, default, first_set or None)
        first_set = first_set or result_set
        cec._votes(p, result_set, units, options, t)
        seated = set()
        for label, option in zip(LISTS, options, strict=True):
            seats = SEATED.get(label)
            if seats:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=seats)
                seated.add(label)
            else:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="lost")
        if seated != set(SEATED) or sum(SEATED.values()) != SEATS:
            raise ValueError("a list of the decision is not on the ballot")
        p.add(
            "note",
            id=cec.key("note", result_set, "outcomes"),
            entity_id=result_set,
            kind="methodology",
            body_md="The seats are as the Central Electoral Commission declared them in its decision No. "
            + (
                "3948 of 6 December 2010"
                if stage == "official"
                else "of 18 December 2010 on the recount of 15 December 2010"
            )
            + ", printed in the first part of its book on the election. The national totals stored as declared are the "
            "total row of the book's table of circumscription results"
            + (" for the count." if stage == "official" else " for the recount."),
            lang="en",
        )
