import json
import re
import zipfile
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.fr import cdsp
from ea.store import DATA, connect

SOURCE = "fr.legislatives_1986"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
API = "https://fr.wikipedia.org/w/api.php?"
SEARCH = API + urlencode(
    {
        "action": "query",
        "list": "search",
        "srsearch": 'intitle:"législatives de 1986"',
        "srlimit": 200,
        "format": "json",
        "formatversion": 2,
    }
)
TITLE = re.compile(r"^Élections? législatives? de 1986 (?:dans|en|à) (?:l'|la |le |les )?(.+)$")
SINGLE = ("Mayotte", "Saint-Pierre-et-Miquelon", "Wallis-et-Futuna")
TEMPLATE = "Modèle:Résultat Législatives 1986, {}"
DAYS = (date(1986, 3, 16), date(1986, 3, 23))
ALIASES = {"Côtes-du-Nord": "22", "Wallis-et-Futuna": "986"}
PUBLISHER = "Wikipédia"
SOURCE_TITLE = "Élections législatives de 1986, par département"
RULES = {
    "formula": "dhondt",
    "threshold": {"percent": 5, "level": "list"},
    "lists": {"order": "closed", "vacancy": "next_on_list"},
    "tieBreak": "votes",
}
SYSTEM = (
    "In 1986 each département elected its deputies from closed party lists in a single round, by the highest "
    "averages (d'Hondt) among the lists with at least 5 percent of the valid votes; a tied seat went to the list with "
    "more votes. Mayotte, Saint-Pierre-et-Miquelon, and Wallis-et-Futuna kept one seat each, elected in two rounds."
)
METHOD = (
    "List votes, seats, and list leaders from the French Wikipedia's article on the 1986 legislative election in "
    "this département, which cites the Interior Ministry's results and *Le Monde*; registered voters, voters, and "
    "valid votes from the same table."
)
UNEVEN = (
    "The lists' votes in the source add up to {listed:,} while its valid votes are {valid:,}; the archive keeps both "
    "as published."
)
FAR_LEFT = {
    "94": (
        " Every other list matches Sciences Po CDSP's tendency totals for the département, but Lutte ouvrière, the "
        "MPPT, and the LCR add up to 8,626 against CDSP's far-left total of 7,235, exactly the 1,391-vote gap. Which "
        "of the three figures is wrong cannot be told without the official list results."
    )
}


def page(title: str) -> str:
    return API + urlencode(
        {"action": "parse", "page": title, "prop": "wikitext", "format": "json", "formatversion": 2, "redirects": 1}
    )


def fetch() -> None:
    raw.fetch(SOURCE, SEARCH, suffix=".json")
    for hit in json.loads(raw.cached(SOURCE, SEARCH).path.read_text())["query"]["search"]:
        if TITLE.match(hit["title"]) and not re.search(r"circonscription|secteur", hit["title"]):
            raw.fetch(SOURCE, page(hit["title"]), suffix=".json")
    for place in SINGLE:
        raw.fetch(SOURCE, page(TEMPLATE.format(place)), suffix=".json")


def parse() -> None:
    with connect() as conn:
        codes = (
            dict(
                conn.execute(
                    """select n.text, c.code from area a join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id and n.lang = 'fr'
                   where a.scheme_code = 'fr.departement'"""
                ).fetchall()
            )
            | ALIASES
        )

    def number(cell: str) -> int | None:
        cell = re.sub(r"(?i)\{\{formatnum:([^}]*)\}\}|</?center>", lambda found: found.group(1) or "", cell)
        cell = re.sub(r"'|&nbsp;|\s", "", cell)
        return int(cell) if re.fullmatch(r"\d+", cell) else None

    def cells(row: str) -> list[str]:
        found = []
        for line in row.split("\n"):
            line = line.strip()
            if not line.startswith("|") or line.startswith(("|}", "|+")):
                continue
            for cell in line[1:].split("||"):
                if re.match(r"\s*(?:style|align|colspan|rowspan|class|scope|bgcolor|width)\s*=", cell) and "|" in cell:
                    cell = cell.split("|", 1)[1]
                found.append(cell.strip())
        return found

    def template(row: str, name: str) -> tuple[list[str], int] | None:
        start = row.find("{{" + name + "|")
        if start < 0:
            return None
        depth, index = 0, start
        while True:
            if row.startswith("{{", index):
                depth, index = depth + 1, index + 2
            elif row.startswith("}}", index):
                depth, index = depth - 1, index + 2
                if depth == 0:
                    break
            else:
                index += 1
        inner = re.sub(r"\{\{[^{}]*\}\}", "", row[start + len(name) + 3 : index - 2])
        inner = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", inner)
        return [part.strip() for part in inner.split("|")], index

    lists: list[dict[str, object]] = []
    totals: list[dict[str, object]] = []
    found = json.loads(raw.cached(SOURCE, SEARCH).path.read_text())["query"]["search"]
    for hit in sorted(found, key=lambda hit: hit["title"]):
        matched = TITLE.match(hit["title"])
        if matched is None or re.search(r"circonscription|secteur", hit["title"]):
            continue
        place = matched[1]
        code = codes[place]
        text = json.loads(raw.cached(SOURCE, page(hit["title"])).path.read_text())["parse"]["wikitext"]
        if place in SINGLE:
            text = json.loads(raw.cached(SOURCE, page(TEMPLATE.format(place))).path.read_text())["parse"]["wikitext"]
        text = re.sub(r"<ref[^>/]*/>|<ref[^>]*>.*?</ref>|<!--.*?-->", "", text, flags=re.DOTALL)
        table = next(
            table
            for table in re.findall(
                r"^\{\|.*?^\|\}|\{\{En-tête Résultats.*?^\|\}", text, flags=re.DOTALL | re.MULTILINE
            )
            if place in SINGLE or ("Voix" in table and re.search(r"Si[èe]ges|Élus", table))
        )
        stats = {
            key: int(value.replace(" ", ""))
            for key, value in re.findall(r"\|\s*((?:inscrits|votants|exprimés)[12]|sièges)\s*=\s*(\d[\d ]*)", table)
        }
        position = 0
        for row in re.split(r"^\|-.*$", table, flags=re.MULTILINE):
            parsed = template(row, "Candidat élection" if place in SINGLE else "Liste candidate élection")
            rest = cells(row[parsed[1] :] if parsed else row)
            numbers = [value for value in map(number, rest) if value is not None]
            if parsed is None:
                labels = [cell for cell in rest if re.search(r"[A-Za-zÀ-ÿ]{3}", cell) and number(cell) is None]
                if not labels:
                    continue
                key = re.sub(r"'|\[\[(?:[^|\]]*\|)?|\]\]", "", labels[-1] if "couleurs" in labels[0] else labels[0])
                heading = re.match(r"\s*(Inscrits|Votants|Exprimés|Suffrages exprimés)\b", key)
                if heading and numbers:
                    stats.setdefault({"I": "inscrits1", "V": "votants1"}.get(heading[1][0], "exprimés1"), numbers[0])
                if heading or len(numbers) < 2 or "Source" in key:
                    continue
                parts = re.split(r"<br\s*/?>", key)
                head, label, listed, elected = (
                    parts[0].strip(),
                    parts[1].strip() if len(parts) > 1 else None,
                    None,
                    False,
                )
            else:
                params = parsed[0]
                named = dict(param.split("=", 1) for param in params if "=" in param)
                positional = [param for param in params if "=" not in param]
                if place in SINGLE:
                    head, label, listed = positional[0], positional[1] if len(positional) > 1 else None, None
                    elected = "élu" in positional[2:]
                else:
                    head, label, listed = named.get("tête_liste"), positional[0] if positional else None, None
                    listed, elected = named.get("nom_liste") or None, False
            if len(numbers) < (1 if place in SINGLE else 2):
                raise ValueError(f"{place}: no votes for {head}: {rest}")
            position += 1
            if place in SINGLE:
                for round_number, votes in enumerate(numbers[:2], start=1):
                    lists.append(
                        {
                            "code": code,
                            "place": place,
                            "round": round_number,
                            "position": position,
                            "head": head,
                            "label": label,
                            "list": None,
                            "votes": votes,
                            "seats": int(elected and round_number == len(numbers[:2])),
                        }
                    )
                continue
            lists.append(
                {
                    "code": code,
                    "place": place,
                    "round": 1,
                    "position": position,
                    "head": head,
                    "label": label,
                    "list": listed,
                    "votes": numbers[0],
                    "seats": numbers[-1],
                }
            )
        for round_number in (1, 2):
            if f"exprimés{round_number}" in stats:
                totals.append(
                    {
                        "code": code,
                        "round": round_number,
                        "registered": stats.get(f"inscrits{round_number}"),
                        "voters": stats.get(f"votants{round_number}"),
                        "valid": stats[f"exprimés{round_number}"],
                    }
                )
    frame = pl.DataFrame(lists)
    accounting = pl.DataFrame(totals)
    with zipfile.ZipFile(raw.cached(cdsp.SOURCE, cdsp.FILES["legi"]).path) as archive:
        official = (
            pl.read_csv(archive.read("LEGISLATIVES_1958-2012-csv/cdsp_legi1986_circ.csv"), infer_schema_length=0)
            .group_by(code=pl.col("code département").str.zfill(2))
            .agg(pl.col("exprimés").cast(pl.Int64).sum().alias("cdsp"))
        )
    check = (
        frame.filter(pl.col("round") == 1)
        .group_by("code")
        .agg(pl.col("votes").sum(), pl.col("seats").sum())
        .join(accounting.filter(pl.col("round") == 1), on="code", how="left")
        .join(official, on="code", how="left")
        .filter(
            (pl.col("votes") != pl.col("valid")) | (pl.col("cdsp").is_not_null() & (pl.col("valid") != pl.col("cdsp")))
        )
    )
    print(check.sort("code"))
    print(f"{frame.filter(pl.col('round') == 1)['seats'].sum()} seats in {frame['code'].n_unique()} départements")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lists.parquet")
    accounting.write_parquet(PARSED / "totals.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lists.parquet")
    totals = pl.read_parquet(PARSED / "totals.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="The 1986 French legislative election by département, from the French Wikipedia",
            polity="FR",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        source = cs.source(PUBLISHER, SOURCE_TITLE)
        uneven = dict(
            conn.execute(
                """select n.entity_id, n.id from note n join changeset c on c.id = n.changeset_id
                   where c.pipeline = %s and n.kind = 'caveat' and n.body_md like 'The lists'' votes in the source%%'""",
                [SOURCE],
            ).fetchall()
        )
        cs.add(
            "source",
            id=source,
            kind="community",
            publisher=PUBLISHER,
            title=SOURCE_TITLE,
            url="https://fr.wikipedia.org/wiki/%C3%89lections_l%C3%A9gislatives_fran%C3%A7aises_de_1986",
            license="CC BY-SA 4.0",
        )
        cs.artifact(raw.cached(SOURCE, SEARCH), source)
        for hit in json.loads(raw.cached(SOURCE, SEARCH).path.read_text())["query"]["search"]:
            if TITLE.match(hit["title"]) and not re.search(r"circonscription|secteur", hit["title"]):
                cs.artifact(raw.cached(SOURCE, page(hit["title"])), source)
        for place in SINGLE:
            cs.artifact(raw.cached(SOURCE, page(TEMPLATE.format(place))), source)

        france = cs.polity("FR")
        assembly = cs.key("fr.body", "national-assembly")
        majority = cs.key("fr.electoral_system", "legislative-two-round")
        if assembly in cs.minted or majority in cs.minted:
            raise ValueError("publish fr.cdsp before fr.legislatives_1986")
        proportional = cs.key("fr.electoral_system", "legislative-list-pr-1986")
        if proportional in cs.minted:
            cs.add("electoral_system", id=proportional, polity_id=france, rules=RULES, rules_version=1)
            name(proportional, "French département list proportional representation (1986)")
            cs.add("note", entity_id=proportional, kind="methodology", body_md=SYSTEM)
        aliases = {"LV": "VERTS", "MPPT": "MPT", "Mouvement pour un parti des travailleurs": "MPT"}
        parties = {}
        for label in frame["label"].drop_nulls().unique():
            known = aliases.get(label, label)
            if known in cdsp.PARTIES:
                parties[label] = cs.key(*cdsp.PARTIES[known])
            elif known in cdsp.NEW_PARTIES:
                parties[label] = cs.key("fr.party", slug(cdsp.NEW_PARTIES[known]))
            if parties.get(label) in cs.minted:
                raise ValueError(f"party {label} is missing; publish fr.cdsp first")

        cycle = cs.key("fr.election", f"{DAYS[0]}:legislative")
        cs.add(
            "election", id=cycle, polity_id=france, kind="general", date_from=DAYS[0], date_to=DAYS[1], status="held"
        )
        name(cycle, "1986 French legislative election")
        name(cycle, "Élections législatives de 1986", "fr", "native")
        cs.add("slug", path=f"fr/{DAYS[0]}-legislative", entity_id=cycle)
        elections = {}
        for number, day in enumerate(DAYS, start=1):
            stage = "general" if number == 1 else "runoff"
            elections[number] = cs.key("fr.election", str(day))
            cs.add(
                "election",
                id=elections[number],
                polity_id=france,
                parent_id=cycle,
                kind=stage,
                date_from=day,
                date_to=day,
                status="held",
            )
            name(elections[number], f"1986 French legislative election, {'first' if number == 1 else 'second'} round")
            cs.add("slug", path=f"fr/{day}-{stage}", entity_id=elections[number])

        accounting = {(code, number): row for code, number, *row in totals.iter_rows()}
        previous: dict[str, UUID] = {}
        for (code, number), lines in frame.sort("code", "round", "position").group_by(
            "code", "round", maintain_order=True
        ):
            place = lines["place"][0]
            single = place in SINGLE
            day = DAYS[number - 1]
            stage = "general" if number == 1 else "runoff"
            unit = cs.unit("fr.departement", code, on=day)
            area = cs.area("fr.departement", code, on=day)
            if unit in cs.minted or area in cs.minted:
                raise ValueError(f"no département {code}")
            contest_key = f"{day}:{code}"
            contest = cs.key("fr.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=elections[number],
                kind="body_seats",
                body_id=assembly,
                district_area_id=area,
                magnitude=1 if single else int(lines["seats"].sum()),
                round_number=number,
                previous_round_id=previous.get(code),
                electoral_system_id=majority if single else proportional,
                default_result_kind="counted",
            )
            previous[code] = contest
            name(
                contest,
                f"{place}, 1986 legislative election"
                + (f", {'first' if number == 1 else 'second'} round" if single else ""),
            )
            cs.add("slug", path=f"fr/{day}-{stage}/{slug(place)}", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=elections[number],
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
            registered, voters, valid = accounting.get((code, number), (None, None, None))
            listed = int(lines["votes"].sum())
            if valid is not None and listed != valid:
                body = UNEVEN.format(listed=listed, valid=valid) + FAR_LEFT.get(code, "")
                cs.add(
                    "note",
                    **({"id": uneven[result_set]} if result_set in uneven else {}),
                    entity_id=result_set,
                    kind="caveat",
                    body_md=body,
                )
            for measure, value in (
                (cdsp.REGISTERED, registered),
                (cdsp.VOTERS, voters),
                (cdsp.VALID, valid),
                (cdsp.INVALID, None if voters is None or valid is None else voters - valid),
            ):
                if value is not None and result_set in cs.minted:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
            later = (
                set(frame.filter((pl.col("code") == code) & (pl.col("round") == 2))["head"]) if number == 1 else set()
            )
            for position, head, label, listed_name, votes, seats in lines.select(
                "position", "head", "label", "list", "votes", "seats"
            ).iter_rows():
                option_key = f"{contest_key}:{position}"
                option = cs.key("fr.option", option_key)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate" if single else "list",
                    party_id=parties.get(label),
                    ballot_position=position,
                    ballot_label=head if single else listed_name or label or head,
                )
                person = cs.key(f"{SOURCE}.person", f"{code}:{slug(head)}")
                if person in cs.minted:
                    cs.add("person", id=person)
                    name(person, head)
                cs.add(
                    "candidacy",
                    id=cs.key("fr.candidacy", option_key),
                    person_id=person,
                    option_id=option,
                    role="head" if single else "list_member",
                    list_position=None if single else 1,
                    ballot_name=head,
                    is_independent=False,
                )
                if result_set in cs.minted:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=cdsp.VOTES,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
                if seats:
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="elected" if single else "won",
                        seats=None if single else seats,
                    )
                elif head in later:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="advanced")
        return cs.write()
