import re
from datetime import date
from uuid import uuid5

from ea import raw
from ea.changeset import slug
from ea.package import NAMESPACE, Package, run

SOURCE = "de.ddr_kommunal"
# Germany was created before packages: production holds it under this id, not under the derived one.
COUNTRY = "01a13bb5-c550-7079-8650-a96e2b145352"
WIKI = "https://de.wikipedia.org/w/index.php?title={}&action=raw"
URL_1990 = WIKI.format("Kommunalwahlen_in_der_DDR_1990")
URL_BERLIN = WIKI.format("Wahl_der_Stadtverordnetenversammlung_von_Berlin_1990")
URL_1989 = WIKI.format("Kommunalwahlen_in_der_DDR_1989")
URL_LAW = "https://www.verfassungen.de/ddr/kommunalwahlgesetz90.htm"
D1989, D1990 = date(1989, 5, 7), date(1990, 5, 6)
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5

# Units that production already holds, written by de.ddr_abstimmungen.
UNITS = {
    "DDR": "fb46de20-d2ac-5bf5-8002-b6dee971d85a",
    "BB": "e7f8ed84-e9ba-51b4-bb79-e5e1e611b273",
    "MV": "ccc0ffda-9634-5300-9897-5322747efb06",
    "ST": "001985e4-be5e-5fde-9184-392de8013e5d",
    "TH": "75046aae-4431-53d7-897d-a2bff34cbace",
    "SN": "78f59d86-1f9c-5801-91e9-306e9c61c5c3",
    "BE": "dbeb46ce-c87b-513c-b0dd-bd41ed34f88f",
}
LANDS = {
    "Brandenburg": ("BB", "Brandenburg"),
    "Mecklenburg-Vorpommern": ("MV", "Mecklenburg-Vorpommern"),
    "Sachsen": ("SN", "Saxony"),
    "Sachsen-Anhalt": ("ST", "Saxony-Anhalt"),
    "Thüringen": ("TH", "Thuringia"),
}
LAND_NAMES = {**{name: code for name, (code, _) in LANDS.items()}, "Ost-Berlin": "BE"}
TOTAL_LABEL = "Gesamte DDR"
# Where a printed valid-vote total differs from the sum of its row's lists, by the difference. The republic's valid
# total is printed as 24,438,753; the Länder's printed valid totals and its own lists add up to 25,438,753.
MISPRINTED = {"DDR": 1_000_000}
# Thüringen's B90/Gr. column prints 285,678, which is 4 more than Thüringen's printed valid total and the republic's
# printed B90/Gr. total allow: with 285,674 the six lists add up to the printed 4,325,257 and the Länder's B90/Gr.
# counts add up to the republic's 1,961,772.
CORRECTED = {("TH", 4): (285678, 285674)}
BEZIRKE = [
    "Mitte",
    "Prenzlauer Berg",
    "Friedrichshain",
    "Treptow",
    "Köpenick",
    "Lichtenberg",
    "Weißensee",
    "Pankow",
    "Marzahn",
    "Hohenschönhausen",
    "Hellersdorf",
]
# The party columns of the Land table, and the party each one is, where it is one party.
TABLE_PARTIES = [
    ("cdu", "CDU", "CDU"),
    ("spd", "SPD", "SPD"),
    ("fdp", "F.D.P.", None),
    ("pds", "PDS", "PDS"),
    ("b90", "B90/Gr.", None),
    ("other", "Sonstige", None),
]
# Berlin's lists, with the English and German names and the key of the party where the archive keeps one.
BERLIN_PARTIES = {
    "SPD": ("SPD", "Sozialdemokratische Partei in der DDR", "Social Democratic Party in the GDR"),
    "PDS": ("PDS", "Partei des Demokratischen Sozialismus", "Party of Democratic Socialism"),
    "CDU": ("CDU", "Christlich-Demokratische Union Deutschlands", "Christian Democratic Union of Germany"),
    "Bündnis 90": ("B90", "Bündnis 90", "Alliance 90"),
    "Grüne Liste": ("GL", "Grüne Liste", "Green List"),
    "BFD": ("BFD", "Bund Freier Demokraten", "Federation of Free Democrats"),
    "DSU": ("DSU", "Deutsche Soziale Union", "German Social Union"),
    "FDP": ("FDP", "Freie Demokratische Partei der DDR", "Free Democratic Party of the GDR"),
    "ALL": ("ALL", "Alternative Linke Liste", "Alternative Left List"),
    "DA": ("DA", "Demokratischer Aufbruch", "Democratic Awakening"),
    "DBD": ("DBD", "Demokratische Bauernpartei Deutschlands", "Democratic Farmers' Party of Germany"),
    "KPD": ("KPD", "Kommunistische Partei Deutschlands", "Communist Party of Germany"),
    "AA-Wyd": ("AAW", "Autonome Aktion Wydoks", "Autonomous Action Wydoks"),
    "VS": ("VS", "Volkssolidarität", "People's Solidarity"),
    "BdD": ("BDD", "Bund der Diabetiker", "Association of Diabetics"),
    "SSB GP": ("GP", "Die Grauen – Graue Panther", "The Grays – Gray Panthers"),
    "DFP": ("DFP", "Deutsche Forumpartei", "German Forum Party"),
    "EUdDDR": ("EUD", "Europa-Union der DDR", "European Union of the GDR"),
    "VAA": (
        "VAA",
        "Vereinigung der Arbeitskreise für Arbeitnehmerpolitik und Demokratie",
        "Association of Working Groups for Employee Policy and Democracy",
    ),
    "CDVP": ("CDV", "Christlich-Demokratische Volkspartei", "Christian Democratic People's Party"),
}
ALLIANCE_NOTE = (
    "The Alternative Linke Liste (ALL) was a joint list of the Demokratischer Frauenbund Deutschlands, the Alternative "
    "Jugendliste, the Vereinigte Linke, Die Nelken, and the Unabhängiger Frauenverband."
)
SYSTEM_1990 = (
    "Free elections to the Kreistage, Stadtverordnetenversammlungen, Stadtbezirksversammlungen, and "
    "Gemeindevertretungen under the Kommunalwahlgesetz of 6 March 1990 (GBl. I S. 105): a proportional election with "
    "a personal vote. Every voter had three votes, which could be given to candidates of different lists "
    "(panaschieren) or piled on one candidate (kumulieren). Section 35 (2) shares the seats among the lists by the "
    "largest remainder method: a list's valid votes times the number of seats, divided by all valid votes, with whole "
    "numbers first and the remaining seats by the largest fractions. There was no threshold. A list's seats go to its "
    "candidates with the most votes."
)
SYSTEM_1989 = (
    "A vote on the single list of candidates the National Front (Nationale Front) put up for each assembly. A voter "
    "approved the list by folding the ballot and put it in the box without marking it, and rejected it by striking "
    "out every candidate. The state ran no free campaign, and the seats were shared among parties and mass "
    "organizations in advance."
)
NOTE_1989 = (
    "The Kommunalwahlen of 7 May 1989 chose the Kreistage, Stadtverordnetenversammlungen, Stadtbezirksversammlungen, "
    "and Gemeindevertretungen on the National Front's single lists. The archive holds the republic's official total "
    "as announced on the evening of 7 May: 12,488,742 eligible voters, 12,335,487 ballots cast, 11,136 invalid, and "
    "12,324,351 valid, of which 12,182,050 approved the list and 142,301 rejected it. Opposition observers counted "
    "more rejecting votes than the commission announced in nearly every district, and the official result is "
    "recognized as falsified."
)
NOTE_1990 = (
    "The Kommunalwahlen of 6 May 1990, the only free local elections of the GDR, chose 7,787 local assemblies "
    "together. The archive holds each Land's figures summed over all its assemblies, as the Statistisches Bundesamt "
    "published them in Wahlen 1990 in den neuen Ländern und Berlin-Ost (Fachserie 1, Sonderheft, 1992). Every voter "
    "had three votes, so the figures for the lists are votes, not voters: they add up to the valid votes, which are "
    "about three times the voters. Voters is the number of ballots cast. Invalid ballots are counted in ballots, not "
    "in votes, and the table prints no valid ballots. The F.D.P. column holds the Bund Freier Demokraten, the FDP of "
    "the GDR, and the Liberals' joint lists, and the B90/Gr. column holds Bündnis 90 and the Greens; Sonstige holds "
    "every other list and the independent candidates. Two figures of the source are set right. Thüringen's B90/Gr. "
    "count is printed as 285,678 and the archive stores 285,674, the figure that makes Thüringen's lists add up to its "
    "printed 4,325,257 valid votes and the Länder's B90/Gr. counts add up to the republic's printed 1,961,772. The "
    "republic's valid votes are printed as 24,438,753 although the Länder's valid votes and the republic's own lists "
    "add up to 25,438,753; the archive stores no republic row."
)
NOTE_BERLIN = (
    "East Berlin's Stadtverordnetenversammlung of 138 seats, elected on 6 May 1990 together with the other local "
    "assemblies. The source prints the votes of each list in the city and in each of its eleven Stadtbezirke, with "
    "the eligible voters and the voters, and the seats of each list for the whole city. Every voter had three votes. "
    "The invalid ballots (18,394) and the republic's tables come from the Statistisches Bundesamt's table of the "
    "Kommunalwahl by Land; the Stadtbezirke's invalid ballots are not printed. The Statistisches Bundesamt table "
    "counts the Democratic Awakening among the CDU, the BFD and the FDP together as the F.D.P., and Bündnis 90 and "
    "the Greens together as B90/Gr. " + ALLIANCE_NOTE
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def fetch() -> None:
    for url in (URL_1990, URL_BERLIN, URL_1989):
        raw.fetch(SOURCE, url, suffix=".wikitext")
    raw.fetch(SOURCE, URL_LAW, suffix=".htm")


def text(url: str) -> str:
    return raw.cached(SOURCE, url).path.read_text()


def number(cell: str) -> int:
    digits = re.sub(r"[^\d]", "", cell)
    if not digits:
        raise ValueError(f"no number in {cell!r}")
    return int(digits)


def plain(cell: str) -> str:
    cell = re.sub(r"\{\{[^}]*\}\}", "", cell)
    cell = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", cell)
    cell = re.sub(r"<[^>]+>", "", cell)
    return cell.replace("'''", "").strip()


def cells(line: str) -> list[str]:
    out = []
    for cell in line[1:].split("||"):
        out.append(re.sub(r"^\s*(?:rowspan|colspan|align)=[^|]*\|", "", cell, flags=re.IGNORECASE).strip())
    return out


def kommunal() -> dict[str, dict[str, list[int]]]:
    """The Kommunalwahl rows of the two Land tables: the first six numbers are the electorate and the votes, the
    next twelve the votes and shares of the six party columns."""
    found: dict[str, dict[str, list[int]]] = {}
    land = ""
    for line in text(URL_1990).splitlines():
        if not line.startswith("|") or line[:2] in ("|-", "|}", "|+"):
            continue
        row = cells(line)
        if "'''" in row[0]:
            land = plain(row[0])
            row = row[1:]
        if not row or "Kommunalwahl" not in plain(row[0]):
            continue
        numbers = [plain(c) for c in row[1:]]
        if len(numbers) == 6:
            found.setdefault(land, {})["table"] = [number(c) for c in numbers]
        elif len(numbers) == 12:
            found.setdefault(land, {})["parties"] = [number(c) for c in numbers[0::2]]
    return found


def land_rows() -> dict[str, tuple[int, int, int, int, int, list[int]]]:
    """For each Land, East Berlin, and the republic: registered, voters, invalid ballots, valid votes, the shares'
    counts, and the six party counts. Raises when a row does not add up."""
    rows: dict[str, tuple[int, int, int, int, int, list[int]]] = {}
    for name, parts in kommunal().items():
        code = "DDR" if name.startswith(TOTAL_LABEL) else LAND_NAMES[name]
        registered, voters, _, invalid, _, valid = parts["table"]
        votes = list(parts["parties"])
        for (where, index), (printed, fixed) in CORRECTED.items():
            if where == code:
                if votes[index] != printed:
                    raise ValueError(f"{name}: the source no longer prints {printed}")
                votes[index] = fixed
        if sum(votes) - valid != MISPRINTED.get(code, 0):
            raise ValueError(f"{name}: the lists add up to {sum(votes)}, not the {valid} valid votes")
        if invalid + 0 > voters or voters > registered:
            raise ValueError(f"{name}: the voters do not fit the electorate")
        rows[code] = (registered, voters, invalid, valid, 0, votes)
    if set(rows) != {*LAND_NAMES.values(), "DDR"}:
        raise ValueError(f"unexpected rows: {sorted(rows)}")
    return rows


def berlin_tables() -> dict[str, tuple[int, int, dict[str, tuple[int, int | None]]]]:
    """The city's and each Stadtbezirk's eligible voters, voters, and votes (with seats, for the city) by list."""
    tables: dict[str, tuple[int, int, dict[str, tuple[int, int | None]]]] = {}
    where = ""
    registered = voters = 0
    lists: dict[str, tuple[int, int | None]] = {}
    block: list[str] = []

    def close() -> None:
        nonlocal registered, voters
        if not where or not block:
            return
        label = plain(block[0])
        if label == "Wahlberechtigte":
            registered = number(block[1])
        elif label == "Wahlbeteiligung":
            voters = number(block[1])
        elif len(block) >= 2 and re.fullmatch(r"[\d.]+", block[1].split()[0] if block[1].split() else ""):
            seats = None
            if len(block) >= 4 and re.fullmatch(r"\d+", block[3].strip()):
                seats = int(block[3])
            lists[re.sub(r"\(\*\)", "", label).strip()] = (number(block[1].split()[0]), seats)

    for line in [*text(URL_BERLIN).splitlines(), "== end =="]:
        heading = re.match(r"^(={2,3})\s*(.*?)\s*\1\s*$", line)
        if heading:
            close()
            block = []
            if where and lists:
                tables[where] = (registered, voters, lists)
            title = heading.group(2)
            where = (
                "city"
                if title == "Gesamtergebnis"
                else title.removeprefix("Bezirk ")
                if title.startswith("Bezirk ")
                else ""
            )
            registered = voters = 0
            lists = {}
            continue
        if line.startswith(("|-", "|}")):
            close()
            block = []
        elif line.startswith("|") and not line.startswith("|+"):
            if 'align="left"' in line:
                close()
                block = []
            block.extend(cells(line))
    return tables


def check_berlin(rows: dict[str, tuple[int, int, int, int, int, list[int]]]) -> None:
    tables = berlin_tables()
    if set(tables) != {"city", *BEZIRKE}:
        raise ValueError(f"unexpected Berlin tables: {sorted(tables)}")
    city_registered, city_voters, city_lists = tables["city"]
    districts = [t for k, t in tables.items() if k != "city"]
    if sum(t[0] for t in districts) != city_registered or sum(t[1] for t in districts) != city_voters:
        raise ValueError("Berlin: the Stadtbezirke's electorate or voters do not add up to the city")
    for label in city_lists:
        if sum(t[2].get(label, (0, None))[0] for k, t in tables.items() if k != "city") != city_lists[label][0]:
            raise ValueError(f"Berlin: the Stadtbezirke's votes for {label} do not add up to the city")
    if sum(seats or 0 for _, seats in city_lists.values()) != 138:
        raise ValueError("Berlin: the seats do not add up to 138")
    registered, voters, _, valid, _, _ = rows["BE"]
    if (registered, voters) != (city_registered, city_voters):
        raise ValueError("Berlin: the city table disagrees with the Land table on the electorate")
    if sum(votes for votes, _ in city_lists.values()) != valid:
        raise ValueError("Berlin: the lists do not add up to the Land table's valid votes")


def check() -> None:
    rows = land_rows()
    check_berlin(rows)
    total = rows["DDR"]
    parts = [row for code, row in rows.items() if code != "DDR"]
    for label, got, printed in (
        ("electorate", sum(row[0] for row in parts), total[0]),
        ("voters", sum(row[1] for row in parts), total[1]),
        ("invalid ballots", sum(row[2] for row in parts), total[2]),
        ("valid votes", sum(row[3] for row in parts), total[3]),
    ):
        if got != printed:
            print(f"note: the Länder's {label} add up to {got:,}, not the republic's printed {printed:,}")


def packages() -> list[str]:
    return [f"{SOURCE}/registry", f"{SOURCE}/1989-05-07", f"{SOURCE}/1990-05-06"]


def parse() -> None:
    check()
    registry().write()
    local_1989().write()
    local_1990().write()


def emit() -> None:
    run("load", packages())


def party(code: str) -> str:
    return key("party", code)


def source_ids() -> dict[str, str]:
    return {name: key("source", name) for name in ("wiki1990", "wiki1989", "wikiberlin", "law")}


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The parties, the East Berlin city assembly, and the electoral systems of the GDR's 1989 and 1990 local elections",
    )
    p.external.add(COUNTRY)
    ids = source_ids()
    for name, url, title, publisher in (
        (
            "wiki1990",
            URL_1990,
            "Kommunalwahlen in der DDR 1990 (German Wikipedia), with the table of the Statistisches Bundesamt, Wahlen 1990 in den neuen Ländern und Berlin-Ost, Fachserie 1, Sonderheft (1992)",
            "Wikipedia contributors",
        ),
        (
            "wikiberlin",
            URL_BERLIN,
            "Wahl der Stadtverordnetenversammlung von Berlin 1990 (German Wikipedia)",
            "Wikipedia contributors",
        ),
        ("wiki1989", URL_1989, "Kommunalwahlen in der DDR 1989 (German Wikipedia)", "Wikipedia contributors"),
    ):
        p.add(
            "source",
            id=ids[name],
            kind="community",
            publisher=publisher,
            title=title,
            url=url,
            license="CC BY-SA 4.0",
            license_url="https://creativecommons.org/licenses/by-sa/4.0/",
            attribution="Wikipedia contributors, German Wikipedia (revision pinned in the raw cache)",
            notes="The figures are public statistics; the page is cited at the revision current when it was retrieved.",
        )
        p.artifact(raw.cached(SOURCE, url), ids[name])
    p.add(
        "source",
        id=ids["law"],
        kind="official_body",
        publisher="Volkskammer der DDR",
        title="Gesetz über die Wahlen zu Kreistagen, Stadtverordnetenversammlungen, Stadtbezirksversammlungen und Gemeindevertretungen am 6. Mai 1990 vom 6. März 1990, GBl. I 1990 S. 105",
        url=URL_LAW,
        license="Official legal text",
        notes="Text as printed in the Gesetzblatt der DDR, transcribed on verfassungen.de.",
    )
    p.artifact(raw.cached(SOURCE, URL_LAW), ids["law"])
    for code, english, german in (
        ("cdu", "Christian Democratic Union", "Christlich-Demokratische Union Deutschlands"),
        ("spd", "Social Democratic Party in the GDR", "Sozialdemokratische Partei in der DDR"),
        ("pds", "Party of Democratic Socialism", "Partei des Demokratischen Sozialismus"),
    ):
        id = party(code)
        p.add("party", id=id, kind="party", polity_id=COUNTRY)
        p.name(id, english, "en", "official")
        p.name(id, german, "de", "native", False)
    for label, (code, german, english) in BERLIN_PARTIES.items():
        if code in ("SPD", "PDS", "CDU"):
            continue
        id = party(code)
        p.add("party", id=id, kind="bloc" if label == "ALL" else "party", polity_id=COUNTRY)
        p.name(id, english, "en", "official")
        p.name(id, german, "de", "native", False)
        p.name(id, label, "de", "abbreviation", False)
    p.add(
        "scheme",
        code="de.ddr_stadtbezirk",
        polity_id=COUNTRY,
        kind="administrative",
        rank=2,
        noun="town district",
    )
    body = key("body", "berlin-svv")
    p.add("body", id=body, polity_id=COUNTRY, kind="council")
    p.name(body, "East Berlin City Assembly", "en", "official")
    p.name(body, "Stadtverordnetenversammlung von Berlin (Ost)", "de", "native", False)
    p.add("body_size", body_id=body, seats=138, valid_from=D1990, valid_to=date(1990, 10, 3))
    for id, rules, method, since, until in (
        (key("system", "1989"), {"formula": "referendum"}, SYSTEM_1989, date(1949, 10, 7), date(1990, 3, 6)),
        (key("system", "1990"), {"formula": "hare"}, SYSTEM_1990, date(1990, 3, 6), date(1990, 10, 3)),
    ):
        p.add(
            "electoral_system", id=id, polity_id=COUNTRY, valid_from=since, valid_to=until, rules=rules, rules_version=1
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    return p


def election(p: Package, day: date, english: str, german: str) -> tuple[str, str]:
    election = key("election", day)
    p.add("election", id=election, polity_id=COUNTRY, kind="local", date_from=day, date_to=day, status="held")
    p.name(election, english, "en", "official")
    p.name(election, german, "de", "native", False)
    path = f"de/{day}-ddr-local"
    p.add("slug", path=path, entity_id=election, is_current=True)
    return election, path


def result_set(p: Package, election: str, contest: str, name: str, source: str, note: str) -> str:
    id = key("result_set", name)
    p.add(
        "result_set",
        id=id,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=source,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=note, lang="en")
    return id


def value(p: Package, rs: str, unit: str, option: str | None, measure: int, amount: int) -> None:
    p.add(
        "result_value",
        result_set_id=rs,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=amount,
    )


def local_1989() -> Package:
    p = Package(
        f"{SOURCE}/1989-05-07",
        SOURCE,
        "The 7 May 1989 GDR local elections: the republic's official total",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {COUNTRY, UNITS["DDR"]}
    ids = source_ids()
    p.uses(raw.cached(SOURCE, URL_1989))
    vote, path = election(p, D1989, "7 May 1989 East German local elections", "Kommunalwahlen in der DDR 1989")
    contest = key("contest", D1989, "ddr")
    p.add(
        "contest",
        id=contest,
        election_id=vote,
        kind="ballot_question",
        round_number=1,
        electoral_system_id=key("system", "1989"),
        default_result_kind="counted",
    )
    p.add("ballot_question", contest_id=contest, type="plebiscite")
    p.name(contest, "National Front lists for the local assemblies", "en", "official")
    p.name(contest, "Wahlvorschläge der Nationalen Front zu den Kommunalwahlen", "de", "native", False)
    p.add("slug", path=f"{path}/national-front-lists", entity_id=contest, is_current=True)
    yes, no = key("option", D1989, "yes"), key("option", D1989, "no")
    for position, (option, label, english) in enumerate(
        ((yes, "Für den Wahlvorschlag", "For the list"), (no, "Gegen den Wahlvorschlag", "Against the list")), 1
    ):
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="answer",
            ballot_position=position,
            ballot_label=label,
        )
        p.name(option, english, "en", "official")
    rs = result_set(p, vote, contest, "1989", ids["wiki1989"], NOTE_1989)
    p.add("outcome", result_set_id=rs, option_id=yes, kind="won")
    p.add("outcome", result_set_id=rs, option_id=no, kind="lost")
    unit = UNITS["DDR"]
    for option, measure, amount in (
        (yes, VOTES, 12182050),
        (no, VOTES, 142301),
        (None, REGISTERED, 12488742),
        (None, CAST, 12335487),
        (None, VALID, 12324351),
        (None, INVALID, 11136),
    ):
        value(p, rs, unit, option, measure, amount)
    return p


def local_1990() -> Package:
    rows = land_rows()
    tables = berlin_tables()
    p = Package(
        f"{SOURCE}/1990-05-06",
        SOURCE,
        "The 6 May 1990 GDR local elections by Land, and East Berlin's city assembly by Stadtbezirk",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {COUNTRY, *(UNITS[code] for code in (*LAND_NAMES.values(),))}
    ids = source_ids()
    for url in (URL_1990, URL_BERLIN, URL_LAW):
        p.uses(raw.cached(SOURCE, url))
    vote, path = election(p, D1990, "6 May 1990 East German local elections", "Kommunalwahlen in der DDR 1990")
    system = key("system", "1990")
    for name, (code, english) in LANDS.items():
        contest = key("contest", D1990, code)
        p.add(
            "contest",
            id=contest,
            election_id=vote,
            kind="list_allocation",
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(contest, f"Local assemblies of {english}, all together", "en", "official")
        p.name(contest, f"Kommunale Volksvertretungen in {name}, zusammen", "de", "native", False)
        p.add("slug", path=f"{path}/{slug(english)}", entity_id=contest, is_current=True)
        options = {}
        for position, (column, label, party_key) in enumerate(TABLE_PARTIES, 1):
            option = options[column] = key("option", D1990, code, column)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="others" if column == "other" else "list",
                party_id=party(party_key.lower()) if party_key else None,
                ballot_position=position,
                ballot_label=label,
            )
            p.name(option, label, "de", "official")
        rs = result_set(p, vote, contest, f"1990:{code}", ids["wiki1990"], NOTE_1990)
        registered, voters, invalid, _, _, votes = rows[code]
        unit = UNITS[code]
        value(p, rs, unit, None, REGISTERED, registered)
        value(p, rs, unit, None, CAST, voters)
        value(p, rs, unit, None, INVALID, invalid)
        for (column, _, _), amount in zip(TABLE_PARTIES, votes, strict=True):
            value(p, rs, unit, options[column], VOTES, amount)
    # East Berlin's city assembly.
    contest = key("contest", D1990, "berlin")
    p.add(
        "contest",
        id=contest,
        election_id=vote,
        kind="list_allocation",
        body_id=key("body", "berlin-svv"),
        magnitude=138,
        round_number=1,
        electoral_system_id=system,
        default_result_kind="counted",
    )
    p.name(contest, "East Berlin City Assembly", "en", "official")
    p.name(contest, "Stadtverordnetenversammlung von Berlin (Ost)", "de", "native", False)
    p.add("slug", path=f"{path}/east-berlin", entity_id=contest, is_current=True)
    registered, voters, lists = tables["city"]
    options = {}
    for position, (label, (votes, seats)) in enumerate(lists.items(), 1):
        code = BERLIN_PARTIES[label][0] if label in BERLIN_PARTIES else "IND"
        option = options[label] = key("option", D1990, "berlin", code)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list" if label in BERLIN_PARTIES else "independents",
            party_id=party(code.lower() if code in ("SPD", "PDS", "CDU") else code)
            if label in BERLIN_PARTIES
            else None,
            ballot_position=position,
            ballot_label=label,
        )
        p.name(option, label, "de", "official")
    rs = result_set(p, vote, contest, "1990:berlin", ids["wikiberlin"], NOTE_BERLIN)
    for label, (_, seats) in lists.items():
        if seats:
            p.add("outcome", result_set_id=rs, option_id=options[label], kind="won", seats=seats)
    units = {"city": UNITS["BE"]}
    for bezirk in BEZIRKE:
        unit = units[bezirk] = key("unit", "berlin", bezirk)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=COUNTRY,
            scheme_code="de.ddr_stadtbezirk",
            code=f"BE-{bezirk}",
            parent_id=UNITS["BE"],
            area_relation="unlocated",
            kind="standard",
        )
        p.name(unit, f"Stadtbezirk {bezirk}", "de", "native", False)
        p.name(unit, f"{bezirk} (East Berlin)", "en", "official")
    for where, (registered, voters, lists_here) in tables.items():
        unit = units[where]
        value(p, rs, unit, None, REGISTERED, registered)
        value(p, rs, unit, None, CAST, voters)
        for label, (votes, _) in lists_here.items():
            value(p, rs, unit, options[label], VOTES, votes)
    value(p, rs, UNITS["BE"], None, INVALID, rows["BE"][2])
    return p
