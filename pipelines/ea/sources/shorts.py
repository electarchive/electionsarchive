import re
from datetime import date
from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "shorts"
VERSION = "2"
PARTIES: dict[str, dict[str, str | tuple[tuple[str, str | None, str | None], ...]]] = {
    "us": {
        "Democratic Party": "Democratic",
        "Republican Party": "Republican",
        "Libertarian Party": "Libertarian",
        "Green Party": "Green",
        "Conservative Party": "Conservative",
        "Working Families Party": "Working Families",
        "Independence Party": "Independence",
        "Minnesota Democratic–Farmer–Labor Party": "DFL",
        "Reform Party": "Reform",
        "Natural Law Party": "Natural Law",
        "Constitution Party": "Constitution",
        "Peace and Freedom Party": "Peace and Freedom",
        "American Independent Party": "American Independent",
        "North Dakota Democratic–Nonpartisan League Party": "Democratic-NPL",
        "Women's Equality Party": "Women's Equality",
        "New Progressive Party": "New Progressive",
        "Popular Democratic Party": "Popular Democratic",
        "Puerto Rican Independence Party": "PIP",
    },
    "ca": {
        "Liberal Party of Canada": "Liberal",
        "Conservative Party of Canada": "Conservative",
        "Bloc Québécois": "Bloc",
        "Green Party of Canada": "Green",
        "Canadian Alliance": "Alliance",
        "Progressive Conservative": "PC",
        "Reform Party of Canada": "Reform",
        "Social Credit Party of Canada": "Social Credit",
        "Co-operative Commonwealth Federation": "CCF",
    },
    "gb": {
        "Labour Party": "Labour",
        "Liberal Democrats": "Lib Dem",
        "Scottish National Party": "SNP",
        "Democratic Unionist Party": "DUP",
        "Social Democratic and Labour Party": "SDLP",
        "Ulster Unionist Party": "UUP",
        "Green Party": "Green",
        "Reform UK": "Reform",
        "Alliance Party of Northern Ireland": "Alliance",
        "Traditional Unionist Voice": "TUV",
        "UK Independence Party": "UKIP",
        "Unionist Party (Scotland)": "Unionist",
    },
    "de": {
        "BÜNDNIS 90/DIE GRÜNEN": "Greens",
        "Die Linke": "Linke",
        "German Party": "DP",
        "Centre Party": "Zentrum",
        "FREIE WÄHLER": "Free Voters",
    },
    "fr": {
        "Ensemble": "Ensemble",
        "Union de la gauche": "NFP",
        "Nouvelle Union populaire écologique et sociale": "NUPES",
        "La République en marche": "LREM",
        "Parti socialiste": "PS",
        "Socialist Party": "PS",
        "Rassemblement national": (("FN", None, "2018-06-01"), ("RN", "2018-06-01", None)),
        "Parti communiste français": "PCF",
        "Parti communiste": "PCF",
        "Divers gauche": "Misc. left",
        "Divers droite": "Misc. right",
        "Divers centre": "Misc. center",
        "Écologistes": "Greens",
        "Régionalistes": "Regionalists",
        "Mouvement démocrate": "MoDem",
        "La France insoumise": "LFI",
        "Rally for the Republic": "RPR",
        "Extrême droite": "Far right",
    },
    "it": {
        "Democratic Party": "PD",
        "Partito Democratico – Italia Democratica e Progressista": "PD",
        "The People of Freedom": "PdL",
        "The Olive Tree": "Olive Tree",
        "Forza Italia": "FI",
        "Lega Nord": "Lega",
        "National Alliance": "AN",
        "Union of the Centre": "UDC",
        "Communist Refoundation Party": "PRC",
        "Italy of Values": "IdV",
        "Civic Choice": "SC",
        "Left, Ecology and Freedom": "SEL",
        "Democrats of the Left": "DS",
        "Party of Italian Communists": "PdCI",
        "Federation of the Greens": "Greens",
        "Christian Democracy": "DC",
        "Italian Communist Party": "PCI",
        "Italian Socialist Party": "PSI",
        "Italian Democratic Socialist Party": "PSDI",
        "Italian Liberal Party": "PLI",
        "Italian Republican Party": "PRI",
        "Azione – Italia Viva": "Azione–IV",
    },
    "tr": {
        "Adalet ve Kalkınma Partisi": "AKP",
        "İYİ Parti": "İYİ",
    },
    "ch": {
        "GRÜNE Schweiz": "Greens",
    },
}
OFFICES = {
    "us": {
        "President of the United States": "President",
        "Vice President of the United States": "Vice President",
    },
    "br": {"President of Brazil": "President"},
    "ca": {"Prime Minister of Canada": "Prime Minister"},
    "de": {"Chancellor of Germany": "Chancellor"},
    "fr": {"President of France": "President", "Prime Minister of France": "Prime Minister"},
    "gb": {"Prime Minister of the United Kingdom": "Prime Minister"},
    "it": {"President of the Italian Republic": "President", "Prime Minister of Italy": "Prime Minister"},
    "tr": {"President of Türkiye": "President", "Prime Minister of Turkey": "Prime Minister"},
}
BODIES = {
    "us": {"United States House of Representatives": "U.S. House", "United States Senate": "U.S. Senate"},
    "br": {"Federal Senate": "Senate"},
    "it": {"Parliament in joint session with regional delegates": "Parliament"},
    "tr": {"Grand National Assembly of Türkiye": "Grand National Assembly", "Senate of the Republic": "Senate"},
}
CHAMBERS = {
    "House of Representatives": "State House",
    "Senate": "State Senate",
    "State Senate": "State Senate",
    "State Assembly": "State Assembly",
    "Assembly": "State Assembly",
    "House of Delegates": "House of Delegates",
    "General Assembly": "General Assembly",
    "Legislature": "Legislature",
}


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Short names for the main parties, national offices, and legislatures of each polity, for US state "
                "legislatures, and for US state and local offices named after their polity, for headings and labels "
                "where the polity is already given"
            ),
            polity=None,
            kind="edit",
        )
        found = conn.execute(
            """select e.id, e.type::text, coalesce(pt.path, ot.path, bt.path), n.text
               from entity e
               left join party p on p.id = e.id
               left join office o on o.id = e.id
               left join body b on b.id = e.id
               left join slug pt on pt.entity_id = p.polity_id and pt.is_current
               left join slug ot on ot.entity_id = o.polity_id and ot.is_current
               left join slug bt on bt.entity_id = b.polity_id and bt.is_current
               join name n on n.entity_id = e.id and n.kind in ('official', 'english')
               where e.type in ('party', 'office', 'body')
                 and not exists (select from entity_merge m where m.entity_id = e.id)
                 and coalesce(pt.path, ot.path, bt.path) is not null"""
        ).fetchall()
        states: dict[str, list[str]] = {}
        for path, text in conn.execute(
            """select s.path, n.text from slug s
               join name n on n.entity_id = s.entity_id and n.kind in ('official', 'english') and n.lang = 'en'
               where s.is_current and s.path ~ '^us/[a-z]{2}(/[^/]+)*$'
               order by length(n.text) desc"""
        ).fetchall():
            states.setdefault(path, []).append(text)
        kinds = dict(conn.execute("select id, kind::text from body").fetchall())
        shorts: dict[str, set[tuple[str, str | None, str | None]]] = {}
        local: dict[str, set[str]] = {}
        hit: set[tuple[str, str, str]] = set()
        for entity, kind, path, text in found:
            root = path.split("/", 1)[0]
            table = {"party": PARTIES, "office": OFFICES, "body": BODIES}[kind]
            wanted = table.get(root, {}).get(text)
            if wanted is not None and (kind == "party" or path == root):
                hit.add((kind, root, text))
                spans = ((wanted, None, None),) if isinstance(wanted, str) else wanted
                shorts.setdefault(str(entity), set()).update(spans)
                continue
            known = states.get(path)
            if known is None:
                continue
            state = known[0]
            if kind == "office":
                rest = next(
                    (
                        cut
                        for name in known
                        if (cut := re.sub(rf"^{re.escape(name)} | of {re.escape(name)}$", "", text).strip()) != text
                    ),
                    text,
                )
                if (
                    rest != text
                    and rest[:1].isupper()
                    and not rest.startswith("House")
                    and not rest.endswith("University")
                ):
                    local.setdefault(str(entity), set()).add(rest)
            elif kind == "body" and kinds[entity] in ("lower_house", "upper_house", "unicameral"):
                chamber = CHAMBERS.get(text.removeprefix(f"{state} "))
                if text.startswith(f"{state} ") and chamber is not None:
                    shorts.setdefault(str(entity), set()).add((chamber, None, None))
        for entity, rests in local.items():
            if len(rests) == 1 and entity not in shorts:
                shorts[entity] = {(next(iter(rests)), None, None)}
        missing = [
            f"{kind} {root} {text}"
            for kind, table in (("party", PARTIES), ("office", OFFICES), ("body", BODIES))
            for root, names in table.items()
            for text in names
            if (kind, root, text) not in hit
        ]
        if missing:
            raise ValueError(f"no entity for: {missing}")
        for entity, spans in sorted(shorts.items()):
            if len({text for text, _, _ in spans}) > 1 and all(
                start is None and end is None for _, start, end in spans
            ):
                raise ValueError(f"{entity}: several short names {sorted(spans)}")
            for text, start, end in sorted(spans, key=lambda span: span[1] or ""):
                cs.add(
                    "name",
                    entity_id=entity,
                    text=text,
                    lang="en",
                    kind="short",
                    valid_from=None if start is None else date.fromisoformat(start),
                    valid_to=None if end is None else date.fromisoformat(end),
                    is_preferred=False,
                )
        return cs.write()
