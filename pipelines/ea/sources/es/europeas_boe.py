from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "es.europeas_boe"
VERSION = "1"
PUBLISHER = "Junta Electoral Central"
BASE = "https://www.boe.es/boe/dias"
LICENSE = "Reuse under Ley 37/2007 with the source cited"
ATTRIBUTION = "Origen de los datos: Boletín Oficial del Estado."
PROCLAMATIONS = {
    1987: (
        "BOE-A-1987-15255",
        "1987/07/02/pdfs/A20083-20083.pdf",
        "Acuerdo de 30 de junio de 1987 por el que se procede a la proclamación de electos al Parlamento Europeo",
        20083,
        60,
    ),
    1989: (
        "BOE-A-1989-15963",
        "1989/07/06/pdfs/A21472-21472.pdf",
        "Acuerdo de 5 de julio de 1989 por el que se procede a la proclamación de Diputados electos al Parlamento Europeo",
        21472,
        60,
    ),
    1994: (
        "BOE-A-1994-15239",
        "1994/06/30/pdfs/A21095-21095.pdf",
        "Acuerdo de 29 de junio de 1994 por el que se procede a la proclamación de Diputados electos al Parlamento Europeo",
        21095,
        64,
    ),
    1999: (
        "BOE-A-1999-14598",
        "1999/07/01/pdfs/A25068-25068.pdf",
        "Acuerdo de 29 de junio de 1999 por el que se procede a la proclamación de Diputados electos al Parlamento Europeo",
        25068,
        64,
    ),
    2004: (
        "BOE-A-2004-12376",
        "2004/07/01/pdfs/A24477-24477.pdf",
        "Acuerdo de 29 de junio de 2004 por el que se procede a la proclamación de Diputados electos al Parlamento Europeo",
        24477,
        54,
    ),
    2009: (
        "BOE-A-2009-10477",
        "2009/06/24/pdfs/BOE-A-2009-10477.pdf",
        "Acuerdo de 23 de junio de 2009 por el que se procede a la proclamación de Diputados electos al Parlamento Europeo",
        52838,
        50,
    ),
    2014: (
        "BOE-A-2014-6234",
        "2014/06/12/pdfs/BOE-A-2014-6234.pdf",
        "Acuerdo de 11 de junio de 2014 por el que se procede a la proclamación de Diputados electos al Parlamento Europeo",
        44717,
        54,
    ),
    2019: (
        "BOE-A-2019-8953",
        "2019/06/14/pdfs/BOE-A-2019-8953.pdf",
        "Acuerdo de 13 de junio de 2019 por el que se procede a la proclamación de Diputados electos al Parlamento Europeo",
        62477,
        54,
    ),
    2024: (
        "BOE-A-2024-13093",
        "2024/06/28/pdfs/BOE-A-2024-13093.pdf",
        "Acuerdo de 27 de junio de 2024 por el que se procede a la proclamación de candidatos electos al Parlamento Europeo",
        74719,
        61,
    ),
}
ADDITIONS = {
    2009: (
        "BOE-A-2011-19757",
        "2011/12/19/pdfs/BOE-A-2011-19757.pdf",
        "Acuerdo de 1 de diciembre de 2011 de asignación y proclamación de cuatro nuevos miembros del Parlamento Europeo",
        138936,
        4,
    ),
    2019: (
        "BOE-A-2020-1104",
        "2020/01/24/pdfs/BOE-A-2020-1104.pdf",
        (
            "Acuerdo de 23 de enero de 2020 por el que se procede a la proclamación de los candidatos electos al "
            "Parlamento Europeo que han de ocupar los cinco escaños adicionales de España"
        ),
        7441,
        5,
    ),
}
AGREE = (
    "Cross-checked against the Junta Electoral Central's proclamation of elected members ({id}, page {page}): "
    "the proclaimed members, counted by list, give the {seats} seats per list that the ministry's file flags."
)
AGREE_2009 = (
    "Cross-checked against the Junta Electoral Central's proclamation of elected members ({id}, page {page}): "
    "the {seats} proclaimed members, counted by list, agree with the ministry's file except for four seats."
)
LISBON = (
    " The proclamation of 23 June 2009 names {seats} members, the contest's magnitude; the Junta Electoral Central "
    "assigned the four seats that Spain gained under the Treaty of Lisbon on 1 December 2011 ({id}, page {page}) to "
    "the next candidates of the Partido Socialista Obrero Español (two), the Partido Popular, and Coalición por "
    "Europa, so the ministry's file flags {flagged} candidates."
)
BREXIT = (
    " The five seats that Spain gained when the United Kingdom left the Union went, by the Junta Electoral "
    "Central's decision of 23 January 2020 ({id}, page {page}), to the next candidates of the Partido Socialista "
    "Obrero Español, Vox, the Partido Popular, Ciudadanos, and Lliures per Europa with effect from 1 February "
    "2020. The ministry's file flags only the {seats} proclaimed in 2019, and that is what this set holds."
)


def fetch() -> None:
    for _, path, *_ in [*PROCLAMATIONS.values(), *ADDITIONS.values()]:
        raw.fetch(SOURCE, f"{BASE}/{path}", suffix=".pdf")


def parse() -> None:
    pass


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Spain's European Parliament elections of 1987 to 2024: the Junta Electoral Central's proclamations "
                "of elected members in the Boletín Oficial del Estado, cited on each default result set"
            ),
            polity="ES",
        )
        parliament = cs.key("eu.body", "european-parliament")
        sources: dict[str, UUID] = {}

        def cite(notice: tuple[str, str, str, int, int]) -> UUID:
            id, path, title, *_ = notice
            if id not in sources:
                source = sources[id] = cs.source(PUBLISHER, title)
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title,
                    url=f"https://www.boe.es/diario_boe/txt.php?id={id}",
                    license=LICENSE,
                    attribution=ATTRIBUTION,
                )
                cs.artifact(raw.cached(SOURCE, f"{BASE}/{path}"), source)
            return sources[id]

        for year, notice in PROCLAMATIONS.items():
            rows = conn.execute(
                """select rs.id, e.date_from, c.magnitude,
                          (select coalesce(sum(o.seats), 0) from outcome o where o.result_set_id = rs.id)
                   from contest c join election e on e.id = c.election_id
                   join polity p on p.id = e.polity_id and p.iso_code = 'ES'
                   join result_set rs on rs.contest_id = c.id and rs.is_default and rs.status = 'active'
                   where (e.parent_id is not null or e.date_from = '1987-06-10') and extract(year from e.date_from) = %s
                     and c.body_id = %s""",
                [year, parliament],
            ).fetchall()
            if len(rows) != 1:
                raise ValueError(f"{year}: found {len(rows)} default sets")
            result_set, _, magnitude, flagged = rows[0]
            proclaimed = notice[4]
            added = ADDITIONS[year][4] if year in ADDITIONS else 0
            if proclaimed != magnitude or (flagged != proclaimed + added and year != 2019):
                raise ValueError(f"{year}: {flagged} flagged, {proclaimed} proclaimed, magnitude {magnitude}")
            if year == 2019 and flagged != proclaimed:
                raise ValueError(f"{year}: {flagged} flagged, {proclaimed} proclaimed")
            cs.add(
                "citation", entity_id=result_set, source_id=cite(notice), locator={"page": notice[3], "id": notice[0]}
            )
            text = (AGREE_2009 if year == 2009 else AGREE).format(id=notice[0], page=notice[3], seats=proclaimed)
            if year in ADDITIONS:
                extra = ADDITIONS[year]
                cs.add(
                    "citation",
                    entity_id=result_set,
                    source_id=cite(extra),
                    locator={"page": extra[3], "id": extra[0]},
                )
                if year == 2009:
                    text += LISBON.format(seats=proclaimed, id=extra[0], page=extra[3], flagged=flagged)
                else:
                    text += BREXIT.format(id=extra[0], page=extra[3], seats=flagged)
            cs.add("note", entity_id=result_set, kind="methodology", body_md=text)
        return cs.write()
