from datetime import date
from pathlib import Path
from urllib.parse import quote, urlencode
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "br.lineage"
VERSION = "2"
API = "https://pt.wikipedia.org/w/api.php?"
REGISTRY = "https://www.tse.jus.br/partidos/partidos-registrados-no-tse"
REGISTRY_TITLE = "Partidos políticos registrados no TSE"
PL = UUID("01a13ba1-d3a6-7084-b7c4-4e61cf17fdcb")
PRONA = UUID("0ceace95-0cf1-5f77-a522-d02b0eabf623")
DEM = UUID("047942f1-3645-53eb-b2af-e248d22ff95c")
PSL = UUID("a32698f3-392d-5bad-882c-b48702ce4df4")
UNIAO = UUID("01a13ba1-d3a6-7084-b7c4-4e6cb40d9817")
RENAMES = {
    UUID("01a13ba1-d3a6-7084-b7c4-4e621dcc5077"): (
        "Cidadania (partido político)",
        (UUID("d5886f87-ddc5-5a65-887c-5111cc14c0d2"),),
        (
            ("Partido Popular Socialista", "PPS", None, date(2019, 9, 19)),
            ("Cidadania", "CIDADANIA", date(2019, 9, 19), None),
        ),
    ),
    UUID("bcca7b9d-1390-5d5e-b5e6-617a0a6b340a"): (
        "Mobilização Nacional",
        (UUID("01a13ba1-d3a6-7084-b7c4-4e6703e88a24"),),
        (
            ("Partido da Mobilização Nacional", "PMN", None, date(2023, 12, 5)),
            ("Mobilização Nacional", "MOBILIZA", date(2023, 12, 5), None),
        ),
    ),
    UUID("01a13ba1-d3a6-7084-b7c4-4e728aeca60b"): (
        "Avante",
        (UUID("f9efc41f-8d66-581a-9bd9-f590cd540da8"), UUID("9c245393-ffa2-518e-9d23-676e7fed0f36")),
        (
            ("Partido Trabalhista do Brasil", "PT do B", None, date(2017, 9, 12)),
            ("Avante", "AVANTE", date(2017, 9, 12), None),
        ),
    ),
    UUID("01a13ba1-d3a6-7084-b7c4-4e6380f80843"): (
        "Democracia Cristã (Brasil)",
        (UUID("a053351c-54db-5a3b-a8bc-603e3387a6cc"),),
        (
            ("Partido Social Democrata Cristão", "PSDC", None, date(2018, 5, 17)),
            ("Democracia Cristã", "DC", date(2018, 5, 17), None),
        ),
    ),
    UUID("e66aab18-1bbf-5a9b-9ea5-e1d94215e783"): (
        "Democrata (Brasil)",
        (UUID("01a13ba1-d3a6-7084-b7c4-4e685047ff31"),),
        (
            ("Partido da Mulher Brasileira", "PMB", None, date(2025, 12, 2)),
            ("Democrata", "DEMOCRATA", date(2025, 12, 2), None),
        ),
    ),
    UUID("01a13ba1-d3a6-7084-b7c4-4e5b69af0a9d"): (
        "Movimento Democrático Brasileiro (1980)",
        (UUID("bc0a81e7-6f81-5d6d-ae14-17cac2b4156c"),),
        (
            ("Partido do Movimento Democrático Brasileiro", "PMDB", None, date(2018, 5, 15)),
            ("Movimento Democrático Brasileiro", "MDB", date(2018, 5, 15), None),
        ),
    ),
    UUID("01a13ba1-d3a6-7084-b7c4-4e5670d90be6"): (
        "Republicanos (partido político)",
        (UUID("ab49dfc5-c1bd-5894-985b-272a9ccb0ca6"),),
        (
            ("Partido Republicano Brasileiro", "PRB", None, date(2019, 8, 15)),
            ("Republicanos", "REPUBLICANOS", date(2019, 8, 15), None),
        ),
    ),
    PL: (
        "Partido Liberal (2006)",
        (UUID("898c5d63-a24d-511c-8a0a-3a9628301986"),),
        (
            ("Partido da República", "PR", date(2006, 12, 19), date(2019, 5, 7)),
            ("Partido Liberal", "PL", date(2019, 5, 7), None),
        ),
    ),
    UUID("01a13ba1-d3a6-7084-b7c4-4e69313a554d"): (
        "Agir (Brasil)",
        (UUID("6f6e5286-9b0c-5543-95d8-883cb792ca22"), UUID("0e10e3d0-447c-5b5e-b447-008a6cdcf9bc")),
        (
            ("Partido da Reconstrução Nacional", "PRN", None, date(2001, 4, 24)),
            ("Partido Trabalhista Cristão", "PTC", date(2001, 4, 24), date(2022, 3, 31)),
            ("Agir", "AGIR", date(2022, 3, 31), None),
        ),
    ),
    UUID("01a13ba1-d3a6-7084-b7c4-4e730866ac7a"): (
        "Solidariedade (partido político)",
        (UUID("281cf1b8-06ce-5c14-9b47-dfa9e0454ea6"),),
        (("Solidariedade", "SD", None, date(2018, 6, 28)), ("Solidariedade", "SOLIDARIEDADE", date(2018, 6, 28), None)),
    ),
    UUID("6ec07fd5-7ae4-5f0c-85ef-15a4f169e6db"): (
        "Partido Humanista da Solidariedade",
        (UUID("f7cd137d-dfb7-5564-ac74-4d66b4c753a2"),),
        (
            ("Partido da Solidariedade Nacional", "PSN", None, date(2000, 5, 30)),
            ("Partido Humanista da Solidariedade", "PHS", date(2000, 5, 30), None),
        ),
    ),
    DEM: (
        "Democratas (Brasil)",
        (UUID("ef31efea-21fb-54d5-83dd-ef0eab65db4c"),),
        (("Partido da Frente Liberal", "PFL", None, date(2007, 3, 28)), ("Democratas", "DEM", date(2007, 3, 28), None)),
    ),
}
RELATIONS = (
    ("Partido Liberal (2006)", PRONA, PL, "merged_into", date(2006, 12, 19), "day"),
    ("União Brasil", DEM, UNIAO, "merged_into", date(2022, 2, 8), "day"),
    ("União Brasil", PSL, UNIAO, "merged_into", date(2022, 2, 8), "day"),
)
TITLES = tuple(dict.fromkeys([*(title for title, _, _ in RENAMES.values()), *(row[0] for row in RELATIONS)]))


def page(title: str) -> str:
    return API + urlencode(
        {"action": "parse", "page": title, "prop": "wikitext", "format": "json", "formatversion": 2, "redirects": 1}
    )


def fetch() -> None:
    for title in TITLES:
        raw.fetch(SOURCE, page(title), suffix=".json")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Brazilian party lineage: renamed parties held as one party each with dated names (PPS to Cidadania, "
                "PMN to Mobilização Nacional, PT do B to Avante, PSDC to DC, PMB to Democrata, PMDB to MDB, PRB to "
                "Republicanos, PR to PL, PRN to PTC to Agir, SD to Solidariedade, PSN to PHS, and PFL to Democratas), "
                "the 1985 PL and PRONA merged into PR, and DEM and PSL merged into União Brasil"
            ),
            polity="BR",
        )
        sources = {}
        for title in TITLES:
            sources[title] = cs.source("Wikipedia (pt)", title)
            cs.add(
                "source",
                id=sources[title],
                kind="community",
                publisher="Wikipedia (pt)",
                title=title,
                url=f"https://pt.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
                license="CC BY-SA 4.0",
            )
            cs.artifact(raw.cached(SOURCE, page(title)), sources[title])
        registry = cs.source("Tribunal Superior Eleitoral", REGISTRY_TITLE)
        cs.add(
            "source",
            id=registry,
            kind="official_body",
            publisher="Tribunal Superior Eleitoral",
            title=REGISTRY_TITLE,
            url=REGISTRY,
            notes="The TSE's register of parties, with each party's name changes, mergers, and incorporations and the date the TSE approved them.",
        )
        for party, (title, duplicates, versions) in RENAMES.items():
            for duplicate in duplicates:
                cs.add("entity_merge", entity_id=duplicate, into_id=party)
            for full, abbreviation, start, end in versions:
                for text, kind in ((full, "official"), (abbreviation, "abbreviation")):
                    cs.add(
                        "name",
                        entity_id=party,
                        text=text,
                        lang="pt",
                        kind=kind,
                        is_preferred=kind == "official" and end is None,
                        valid_from=start,
                        valid_from_precision="day",
                        valid_to=end,
                        valid_to_precision="day",
                    )
                    for source in (sources[title], registry):
                        cs.add("citation", entity_id=party, source_id=source, locator={"name": text})
            for name_id, owner, text in conn.execute(
                """select id, entity_id, text from name where entity_id = any(%s) and lang = 'pt'
                   and kind in ('official', 'abbreviation') and valid_from is null and valid_to is null""",
                [[party, *duplicates]],
            ).fetchall():
                cs.add(
                    "name_drop",
                    name_id=name_id,
                    entity_id=owner,
                    text=text,
                    reason="Replaced by the party's dated names.",
                )
        (old_pl,) = conn.execute(
            "select entity_id from external_id where scheme = 'br.tse.party' and value = '22:PL:1985'"
        ).fetchone()
        for title, origin, target, kind, day, precision in (
            ("Partido Liberal (2006)", old_pl, PL, "merged_into", date(2006, 12, 19), "day"),
            *RELATIONS,
        ):
            cs.add(
                "party_relation",
                from_party_id=origin,
                to_party_id=target,
                kind=kind,
                date=day,
                date_precision=precision,
                source_id=sources[title],
            )
        return cs.write()
