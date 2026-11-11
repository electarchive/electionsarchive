import html
import re
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.it.storico import cased
from ea.store import DATA, connect

SOURCE = "it.june1946"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DELIVERED = DATA / "inbox" / "manual" / "MF-594"
FILES = {
    "referendum": ("it-referendum-1946-italia.html", "it-referendum-1946-circoscrizioni-province.html"),
    "costituente": ("it-costituente-1946-italia.html", "it-costituente-1946-circoscrizioni-province.html"),
}
DAY = date(1946, 6, 2)
END = date(1946, 6, 3)
FIRST_SITTING = date(1946, 6, 25)
DISSOLVED = date(1948, 1, 31)
CAMERA = date(1948, 4, 18)
SCHEME = "it.circoscrizione_costituente_1946"
GAZZETTA = "https://www.gazzettaufficiale.it/eli/gu/1946/03/12/60/so/60/sg/pdf"
REGISTERED, VALID, INVALID, BLANK, VOTES, VOTERS = 2, 4, 5, 6, 1, 14
SEATS = {
    1: 28,
    2: 18,
    3: 20,
    4: 36,
    5: 14,
    6: 18,
    7: 10,
    8: 5,
    9: 29,
    10: 16,
    11: 12,
    13: 24,
    14: 20,
    15: 14,
    16: 15,
    17: 10,
    18: 17,
    19: 12,
    20: 33,
    21: 16,
    22: 9,
    23: 30,
    24: 15,
    25: 21,
    26: 15,
    27: 7,
    28: 24,
    29: 27,
    30: 26,
    31: 14,
    32: 1,
}
MAGNITUDE = 556
EXISTING = {
    "DC": ("wikidata", "Q815348"),
    "PSIUP": ("wikidata", "Q2755002"),
    "PCI": ("wikidata", "Q461886"),
    "PRI": ("wikidata", "Q767560"),
    "PLI": ("wikidata", "Q508733"),
    "PS D'AZ.": ("wikidata", "Q1299105"),
}
NEW = {
    "UN.DEMOC.NAZIONALE": ("Unione Democratica Nazionale", "UDN", "list_vehicle"),
    "BLOCCO NAZ.LIBERTA'": ("Blocco Nazionale della Libertà", "BNL", "list_vehicle"),
    "PARTITO D'AZIONE": ("Partito d'Azione", "PdA", "party"),
    "MOV.INDIPEND.SIC.": ("Movimento per l'Indipendenza della Sicilia", "MIS", "party"),
    "CDR": ("Concentrazione Democratica Repubblicana", "CDR", "list_vehicle"),
    "MOV.UNIONISTA IT.": ("Movimento Unionista Italiano", "MUI", "party"),
    "PART.DEMOCR.LAVORO": ("Partito Democratico del Lavoro", "PDL", "party"),
    "FR.DEM.PROG.REPUB.": ("Fronte Democratico Progressista Repubblicano", None, "list_vehicle"),
}
RULES = {
    "formula": "imperiali_quota",
    "remainders": "national",
    "districtFormulas": [
        {"seats": 1, "formula": "fptp", "pooled": False},
        *({"seats": seats, "formula": "hagenbach_bischoff_quota"} for seats in range(2, 21)),
    ],
    "lists": {"order": "open", "vacancy": "next_by_votes"},
}
SYSTEM_NOTE = (
    "Deputies were elected by open-list proportional representation in 31 multi-member constituencies under "
    "Lieutenant's Legislative Decree 74 of March 10, 1946. In each constituency the quota was the valid list votes "
    "divided by its seats plus one, or plus two where it had more than 20 seats, and each list won a seat for every "
    "whole quota (art. 57). Seats left over and every list's remaining votes passed to a national college (Collegio "
    "Unico Nazionale), where the pooled remainders of lists linked to a national list were shared by whole quotas "
    "and then largest remainders (art. 61). Valle d'Aosta elected one deputy by plurality (art. 66)."
)
SEATS_NOTE = (
    "Each constituency's seats are those assigned by Table A of Decree 74 of March 10, 1946, which shared 573 seats. "
    "Voting was not held in the Trieste constituency or in the province of Bolzano, so 556 deputies were elected, and "
    "Trento's nine seats fell to the five that reproduce the seats Eligendo prints for it."
)
CONSTITUENCY_SEATS = (
    "Seats are those each list won by whole quotas in this constituency, as Eligendo prints them. Seats won in the "
    "national college are on the national seat allocation."
)
TOTALS_NOTE = (
    "Italy's national page prints {blank:,} blank and {invalid:,} invalid ballots (blank included), {blank_gap:,} and "
    "{invalid_gap:,} more than its constituency pages add up to. The constituency figures are loaded as printed."
)
INVALID_NOTE = (
    "Invalid ballots are left out for {places}, where the printed invalid ballots and valid list votes do not add up "
    "to the voters."
)
REFERENDUM_NOTE = (
    "Voters chose between a republic and a monarchy as the form of the State, under Lieutenant's Legislative Decree "
    "98 of March 16, 1946. The Court of Cassation proclaimed the result on June 18, 1946."
)
METHODOLOGY = (
    "Each constituency's electors, voters, valid, blank, and invalid ballots, and votes for the Republic and the "
    "Monarchy are the Interior Ministry's Eligendo figures; they add up to Eligendo's national totals. Invalid ballots "
    "here leave out the blank ones."
)


def pages(name: str) -> list[tuple[str, str]]:
    found = []
    for file in FILES[name]:
        text = (DELIVERED / file).read_text(encoding="utf-8")
        parts = re.split(r"<!-- (https://elezionistorico[^ ]+) -->", text)
        found.extend(zip(parts[1::2], parts[2::2], strict=True))
    return found


def fetch() -> None:
    for name in FILES:
        for address, body in pages(name):
            raw.store(
                SOURCE,
                address,
                body.encode(),
                suffix=".html",
                retrieved_at=datetime(2026, 11, 10, tzinfo=UTC),
                media_type="text/html",
            )
    raw.fetch(SOURCE, GAZZETTA, suffix=".pdf")


def number(text: str) -> int:
    return int(text.replace(".", ""))


def flattened(address: str) -> str:
    text = raw.cached(SOURCE, address).path.read_text(encoding="utf-8")
    text = re.sub(
        r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>", "", text, flags=re.DOTALL)))
    )
    return text[text.rfind("02/06/1946 Area ITALIA") :]


def place(address: str, text: str) -> tuple[int, str] | None:
    if "&ne1=" not in address or "&ne2=" in address:
        return None if "&ne1=" in address else (0, "ITALIA")
    found = re.search(r"Circoscrizione (.+?) (?:Elettori|Affluenza)", text)
    if found is None:
        raise ValueError(f"no circoscrizione on {address}")
    return int(re.findall(r"&ne1=(\d+)", address)[0]), found[1].strip()


def parse() -> None:
    referendum = []
    for address, _ in pages("referendum"):
        text = flattened(address)
        where = place(address, text)
        if where is None:
            continue
        found = re.search(
            r"Elettori ([\d.]+) 1\. .*? Votanti ([\d.]+) % [\d,]+ Schede Valide ([\d.]+) Schede bianche ([\d.]+) "
            r"Schede non valide \(bianche incl\.\) ([\d.]+) Repubblica Monarchia ([\d.]+) ([\d.]+)",
            text,
        )
        if found is None:
            raise ValueError(f"no referendum figures on {address}")
        referendum.append((*where, address, *map(number, found.groups())))
    referendum = pl.DataFrame(
        referendum,
        schema=["n", "circ", "address", "registered", "voters", "valid", "blank", "spoiled", "republic", "monarchy"],
        orient="row",
    )
    circs = referendum.filter(pl.col("n") > 0)
    italy = referendum.filter(pl.col("n") == 0)
    measures = ["registered", "voters", "valid", "blank", "spoiled", "republic", "monarchy"]
    if circs.height != 31 or circs.select(measures).sum().row(0) != italy.select(measures).row(0):
        raise ValueError("the referendum's circoscrizioni do not add up to Italy")
    if referendum.filter(
        (pl.col("valid") != pl.col("republic") + pl.col("monarchy"))
        | (pl.col("voters") != pl.col("valid") + pl.col("spoiled"))
    ).height:
        raise ValueError("a referendum page does not add up")

    accounting = []
    lists = []
    for address, _ in pages("costituente"):
        text = flattened(address)
        where = place(address, text)
        if where is None:
            continue
        found = re.search(
            r"Elettori ([\d.]+) Votanti ([\d.]+) [\d,]+ % Schede Bianche ([\d.]+) Non valide \(bianche incl\.\) ([\d.]+) "
            r"Liste/Gruppi Voti % Seggi (.*?) TOTALI ([\d.]+) (\d+)",
            text,
        )
        if found is None:
            raise ValueError(f"no Constituent Assembly figures on {address}")
        rows = [
            (label.strip(), number(votes), int(seats or 0))
            for label, votes, seats in re.findall(r" ?(.+?) ([\d.]+) \d+,\d\d(?: (\d+))?(?= [^\d ]|$)", found[5])
        ]
        if sum(row[1] for row in rows) != number(found[6]) or sum(row[2] for row in rows) != int(found[7]):
            raise ValueError(f"lists do not add up to the totals on {address}")
        accounting.append((*where, address, *map(number, found.groups()[:4]), number(found[6])))
        lists.extend((*where, label, votes, seats) for label, votes, seats in rows)
    accounting = pl.DataFrame(
        accounting,
        schema=["n", "circ", "address", "registered", "voters", "blank", "spoiled", "valid"],
        orient="row",
    )
    lists = pl.DataFrame(lists, schema=["n", "circ", "label", "votes", "seats"], orient="row")
    national = lists.filter(pl.col("n") == 0)
    summed = (
        lists.filter(pl.col("n") > 0)
        .group_by("label")
        .agg(pl.col("votes").sum())
        .join(national, on="label", how="full", coalesce=True)
    )
    others = summed.filter(pl.col("n").is_null())["votes"].sum()
    if (
        summed.filter(
            pl.col("n").is_not_null() & (pl.col("label") != "ALTRE LISTE") & (pl.col("votes") != pl.col("votes_right"))
        ).height
        or national.filter(pl.col("label") == "ALTRE LISTE")["votes"][0] != others
    ):
        raise ValueError("the Constituent Assembly's circoscrizioni do not add up to Italy's list votes")
    circ_seats = lists.filter(pl.col("n") > 0).group_by("n").agg(pl.col("seats").sum())
    for n, won in circ_seats.iter_rows():
        if won > SEATS[n]:
            raise ValueError(f"circoscrizione {n} prints {won} seats, more than its {SEATS[n]}")
    if national["seats"].sum() != MAGNITUDE or sum(SEATS.values()) != MAGNITUDE:
        raise ValueError("the Constituent Assembly's seats do not add up to 556")
    PARSED.mkdir(parents=True, exist_ok=True)
    referendum.write_parquet(PARSED / "referendum.parquet")
    accounting.write_parquet(PARSED / "accounting.parquet")
    lists.write_parquet(PARSED / "lists.parquet")
    print(accounting.filter(pl.col("n") > 0).select(pl.exclude("n", "circ", "address")).sum())


def emit() -> Path:
    referendum = pl.read_parquet(PARSED / "referendum.parquet")
    accounting = pl.read_parquet(PARSED / "accounting.parquet")
    lists = pl.read_parquet(PARSED / "lists.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Italy's institutional referendum and Constituent Assembly election of June 2, 1946, by "
                "constituency, from the Interior Ministry's Eligendo archive"
            ),
            polity="IT",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        def value(result_set: UUID, unit: UUID, option: UUID | None, measure: int, amount: int) -> None:
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=amount,
            )

        def result_set(key: str, election: UUID, contest: UUID) -> UUID:
            id = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=id,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=interno,
                is_default=True,
                completeness=1,
            )
            return id

        italy = cs.polity("IT")
        nation = cs.area("it.nation", "IT")
        interno = cs.source("Ministero dell'Interno", "Eligendo: archivio storico delle elezioni, open data")
        gazzetta = cs.source(
            "Ministero della Giustizia", "Gazzetta Ufficiale della Repubblica Italiana, Serie generale"
        )
        if interno in cs.minted or gazzetta in cs.minted:
            raise ValueError("publish it.interno and it.storico before it.june1946")
        cs.artifact(raw.cached(SOURCE, GAZZETTA), gazzetta)
        for frame in (referendum, accounting):
            for (address,) in frame.select("address").iter_rows():
                cs.artifact(raw.cached(SOURCE, address), interno)

        if conn.execute("select 1 from scheme where code = %s", [SCHEME]).fetchone() is None:
            cs.add("scheme", code=SCHEME, polity_id=italy, kind="electoral_district", rank=2, noun="constituency")
        units: dict[int, UUID] = {}
        areas: dict[int, UUID] = {}
        circ_names = dict(accounting.filter(pl.col("n") > 0).select("n", "circ").iter_rows())
        for n, circ in sorted(circ_names.items()):
            label = cased(circ)
            areas[n] = cs.area(SCHEME, str(n), on=DAY)
            units[n] = cs.unit(SCHEME, str(n), on=DAY)
            if areas[n] in cs.minted:
                cs.add("area", id=areas[n], scheme_code=SCHEME)
                cs.add("area_code", area_id=areas[n], code=str(n), valid_from=DAY, valid_to=CAMERA)
                cs.add("area_parent", area_id=areas[n], parent_id=nation, valid_from=DAY, valid_to=CAMERA)
                name(areas[n], label, "it")
            if units[n] in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=units[n],
                    polity_id=italy,
                    scheme_code=SCHEME,
                    code=str(n),
                    area_relation="equals",
                    area_id=areas[n],
                    kind="standard",
                    valid_from=DAY,
                    valid_to=CAMERA,
                )
                name(units[n], label, "it")

        system = cs.key("it.electoral_system", "institutional-referendum-1946")
        cs.add("electoral_system", id=system, polity_id=italy, rules={"formula": "referendum"}, rules_version=1)
        name(system, "Italian institutional referendum of 1946")
        cs.add("note", entity_id=system, kind="methodology", body_md=REFERENDUM_NOTE)
        election = cs.key("it.election", f"{DAY}:referendum")
        cs.add("election", id=election, polity_id=italy, kind="referendum", date_from=DAY, date_to=END, status="held")
        name(election, "1946 Italian institutional referendum")
        name(election, "Referendum istituzionale del 1946", "it", "native")
        path = f"it/{DAY}-referendum"
        cs.add("slug", path=path, entity_id=election)
        contest_key = f"{DAY}:referendum:1"
        contest = cs.key("it.contest", contest_key)
        cs.add(
            "contest",
            id=contest,
            election_id=election,
            kind="ballot_question",
            electoral_system_id=system,
            default_result_kind="counted",
        )
        cs.add("ballot_question", contest_id=contest, type="plebiscite")
        name(contest, "Form of the State: republic or monarchy")
        name(contest, "Referendum sulla forma istituzionale dello Stato", "it", "native")
        cs.add("slug", path=f"{path}/question-1", entity_id=contest)
        options = {}
        for position, (column, label) in enumerate((("republic", "Repubblica"), ("monarchy", "Monarchia")), 1):
            options[column] = cs.key("it.option", f"{contest_key}:{column}")
            cs.add(
                "contest_option",
                id=options[column],
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
        counted = result_set(contest_key, election, contest)
        cs.add("note", entity_id=counted, kind="methodology", body_md=METHODOLOGY)
        for n, registered, voters, valid, blank, spoiled, republic, monarchy in (
            referendum.filter(pl.col("n") > 0)
            .sort("n")
            .select("n", "registered", "voters", "valid", "blank", "spoiled", "republic", "monarchy")
            .iter_rows()
        ):
            for option, measure, amount in (
                (options["republic"], VOTES, republic),
                (options["monarchy"], VOTES, monarchy),
                (None, REGISTERED, registered),
                (None, VOTERS, voters),
                (None, VALID, valid),
                (None, BLANK, blank),
                (None, INVALID, spoiled - blank),
            ):
                value(counted, units[n], option, measure, amount)
        totals = referendum.filter(pl.col("n") == 0).row(0, named=True)
        winner = "republic" if totals["republic"] > totals["monarchy"] else "monarchy"
        for column, option in options.items():
            cs.add("outcome", result_set_id=counted, option_id=option, kind="won" if column == winner else "lost")

        assembly = cs.key("it.body", "constituent-assembly")
        if assembly in cs.minted:
            cs.add("body", id=assembly, polity_id=italy, kind="constituent_assembly")
            name(assembly, "Constituent Assembly")
            name(assembly, "Assemblea Costituente", "it", "native")
            cs.add("body_size", body_id=assembly, seats=MAGNITUDE, valid_from=FIRST_SITTING, valid_to=DISSOLVED)
        system = cs.key("it.electoral_system", "costituente-proportional-1946")
        cs.add("electoral_system", id=system, polity_id=italy, rules=RULES, rules_version=1)
        name(system, "Constituent Assembly proportional representation, 1946")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM_NOTE)
        election = cs.key("it.election", f"{DAY}:costituente")
        cs.add("election", id=election, polity_id=italy, kind="general", date_from=DAY, date_to=END, status="held")
        name(election, "1946 Italian Constituent Assembly election")
        name(election, "Elezione dell'Assemblea Costituente del 1946", "it", "native")
        path = f"it/{DAY}-constituent-assembly"
        cs.add("slug", path=path, entity_id=election)

        parties: dict[tuple[str, str], UUID] = {}

        def party_of(label: str) -> UUID:
            if label in EXISTING:
                key = EXISTING[label]
                if key not in parties:
                    parties[key] = cs.key(*key)
                    if parties[key] in cs.minted:
                        raise ValueError(f"{key} is not in the database")
                return parties[key]
            full, abbreviation, kind = NEW.get(label, (label, None, "list_vehicle"))
            key = ("it.party", slug(full)) if label in NEW else ("it.storico.list", slug(label))
            if key not in parties:
                parties[key] = cs.key(*key)
                if parties[key] in cs.minted:
                    cs.add("party", id=parties[key], kind=kind, polity_id=italy)
                    if abbreviation is not None:
                        name(parties[key], abbreviation, "it", "abbreviation")
                    name(parties[key], full if label in NEW else cased(label), "it")
            return parties[key]

        contest_key = f"{DAY}:costituente"
        contest = cs.key("it.contest", contest_key)
        cs.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=assembly,
            district_area_id=nation,
            magnitude=MAGNITUDE,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        name(contest, "Constituent Assembly, national seat allocation")
        cs.add("slug", path=f"{path}/assembly", entity_id=contest)
        cs.add("note", entity_id=contest, kind="methodology", body_md=SEATS_NOTE)
        counted = result_set(contest_key, election, contest)
        italy_row = accounting.filter(pl.col("n") == 0).row(0, named=True)
        circ_rows = accounting.filter(pl.col("n") > 0).sort("n")
        cs.add(
            "note",
            entity_id=counted,
            kind="caveat",
            body_md=TOTALS_NOTE.format(
                blank=italy_row["blank"],
                invalid=italy_row["spoiled"],
                blank_gap=italy_row["blank"] - circ_rows["blank"].sum(),
                invalid_gap=italy_row["spoiled"] - circ_rows["spoiled"].sum(),
            ),
        )
        unbalanced = circ_rows.filter(pl.col("voters") != pl.col("valid") + pl.col("spoiled"))
        if unbalanced.height:
            places = [cased(circ) for circ in unbalanced["circ"]]
            cs.add(
                "note",
                entity_id=counted,
                kind="caveat",
                body_md=INVALID_NOTE.format(places=", ".join(places[:-1]) + f", and {places[-1]}"),
            )
        for n, registered, voters, blank, spoiled, valid in circ_rows.select(
            "n", "registered", "voters", "blank", "spoiled", "valid"
        ).iter_rows():
            value(counted, units[n], None, REGISTERED, registered)
            value(counted, units[n], None, VOTERS, voters)
            value(counted, units[n], None, VALID, valid)
            value(counted, units[n], None, BLANK, blank)
            if voters == valid + spoiled:
                value(counted, units[n], None, INVALID, spoiled - blank)
        circ_lists = lists.filter((pl.col("n") > 0) & (pl.col("votes") > 0))
        won = dict(lists.filter(pl.col("n") == 0).select("label", "seats").iter_rows())
        options = {}
        for label in sorted(set(circ_lists["label"])):
            options[label] = cs.key("it.option", f"{contest_key}:{slug(label)}")
            cs.add(
                "contest_option",
                id=options[label],
                contest_id=contest,
                kind="list",
                party_id=party_of(label),
                ballot_label=label,
            )
            if won.get(label):
                cs.add("outcome", result_set_id=counted, option_id=options[label], kind="won", seats=won[label])
        for n, label, votes in circ_lists.sort("n", "label").select("n", "label", "votes").iter_rows():
            value(counted, units[n], options[label], VOTES, votes)

        for n, circ, valid in circ_rows.select("n", "circ", "valid").iter_rows():
            label = cased(circ)
            child_key = f"{contest_key}:{n}"
            child = cs.key("it.contest", child_key)
            cs.add(
                "contest",
                id=child,
                election_id=election,
                kind="list_allocation",
                body_id=assembly,
                district_area_id=areas[n],
                allocation_parent_id=contest,
                magnitude=SEATS[n],
                electoral_system_id=system,
                default_result_kind="counted",
            )
            name(child, f"Constituent Assembly, {label}")
            cs.add("slug", path=f"{path}/assembly/{slug(circ)}", entity_id=child)
            child_set = result_set(child_key, election, child)
            if n != 32:
                cs.add("note", entity_id=child_set, kind="methodology", body_md=CONSTITUENCY_SEATS)
            value(child_set, units[n], None, VALID, valid)
            for label, votes, seats in (
                circ_lists.filter(pl.col("n") == n).sort("label").select("label", "votes", "seats").iter_rows()
            ):
                child_option = cs.key("it.option", f"{child_key}:{slug(label)}")
                cs.add(
                    "contest_option",
                    id=child_option,
                    contest_id=child,
                    kind="list",
                    party_id=party_of(label),
                    ballot_label=label,
                )
                value(child_set, units[n], child_option, VOTES, votes)
                if seats:
                    cs.add("outcome", result_set_id=child_set, option_id=child_option, kind="won", seats=seats)
        return cs.write()
