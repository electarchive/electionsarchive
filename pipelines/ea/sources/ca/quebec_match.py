import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import NamedTuple
from uuid import UUID

from ea.changeset import Changeset
from ea.store import Connection, connect

SOURCE = "ca.quebec_match"
VERSION = "3"
SHORT = {"st": "saint", "ste": "sainte"}
# Members whose candidacies, won or lost, carry another printed name or a riding that is not a namesake, or lie two
# general elections apart, joined by hand. Each tuple lists ca.quebec candidacy keys of one person, and the member's
# notice on assnat.qc.ca names each of those candidacies, defeats included. The same name does not make people the
# same: Éric Girard (Lac-Saint-Jean) and Eric Girard (Groulx) are two members, and two Gilles Baril ran in
# Rouyn-Noranda-Témiscamingue in 1985.
SAME = (
    (  # Cloutier: François Cloutier, Ahuntsic 1970; François Cloutier, L'Acadie 1973
        "1970-04-29:3:1970-04-29:3:0",
        "1973-10-29:150:1973-10-29:150:0",
    ),
    (  # Brown: Glendon Pettes Brown, Brome 1970; Glendon dit Glen Brown, Brome-Missisquoi 1973
        "1970-04-29:14:1970-04-29:14:0",
        "1973-10-29:38:1973-10-29:38:0",
    ),
    (  # Harvey: André Harvey, Chauveau 1970; Charlesbourg 1973; Charlesbourg 1976; Jonquière 1981
        "1970-04-29:19:1970-04-29:19:0",
        "1973-10-29:18:1973-10-29:18:0",
        "1976-11-15:18:1976-11-15:18:1",
        "1981-04-13:880:1981-04-13:880:1",
    ),
    (  # Bossé: Alfre Bossé, Dorion 1970; Alfred Bossé, Dorion 1973
        "1970-04-29:25:1970-04-29:25:0",
        "1973-10-29:144:1973-10-29:144:0",
    ),
    (  # Burns: Robert Gordon Burns, Maisonneuve 1970; Robert Burns, Maisonneuve 1973
        "1970-04-29:57:1970-04-29:57:0",
        "1973-10-29:136:1973-10-29:136:0",
    ),
    (  # Berthiaume: Paul Berthiaume, Napierville-Laprairie 1970; Paul Berthiaume, La Prairie 1973
        "1970-04-29:68:1970-04-29:68:0",
        "1973-10-29:66:1973-10-29:66:0",
    ),
    (  # Picard: Fernand Picard, Olier 1970; Fernand Picard, Viau 1973
        "1970-04-29:71:1970-04-29:71:0",
        "1973-10-29:138:1973-10-29:138:0",
    ),
    (  # Séguin: Arthur-E. Séguin, Robert-Baldwin 1970; Arthur-E. Séguin, Pointe-Claire 1973
        "1970-04-29:80:1970-04-29:80:0",
        "1973-10-29:161:1973-10-29:161:0",
    ),
    (  # Ostiguy: Marcel Ostiguy, Rouville 1970; Marcel Ostiguy, Verchères 1973
        "1970-04-29:82:1970-04-29:82:0",
        "1973-10-29:57:1973-10-29:57:0",
    ),
    (  # Vaillancourt: Georges Vaillancourt, Stanstead 1970; Georges Vaillancourt, Orford 1973
        "1970-04-29:97:1970-04-29:97:0",
        "1973-10-29:37:1973-10-29:37:0",
    ),
    (  # Saint-Pierre: Guy St-Pierre, Verchères 1970; Guy Saint-Pierre, Chambly 1973
        "1970-04-29:104:1970-04-29:104:0",
        "1973-10-29:56:1973-10-29:56:0",
    ),
    (  # Drummond: Thomas Kevin Drummond, Westmount 1970; Kevin Drummond, Westmount 1973
        "1970-04-29:106:1970-04-29:106:0",
        "1973-10-29:152:1973-10-29:152:0",
    ),
    (  # Cournoyer: Jean Cournoyer, Saint-Jacques 1970; Chambly 1971 by-election; Robert-Baldwin 1973; Richelieu 1976
        "1970-04-29:87:1970-04-29:87:2",
        "1971-02-08:15:1971-02-08:15:0",
        "1973-10-29:160:1973-10-29:160:0",
        "1976-11-15:50:1976-11-15:50:1",
    ),
    (  # Fortier: J.-Arthur-Guy Fortier, Gaspé-Sud 1970; Guy Fortier, Gaspé 1973
        "1970-04-29:32:1970-04-29:32:0",
        "1973-10-29:2:1973-10-29:2:0",
    ),
    (  # Bienvenue: Jean Bienvenue, Matane 1970; Jean Bienvenue, Crémazie 1973
        "1970-04-29:60:1970-04-29:60:0",
        "1973-10-29:146:1973-10-29:146:0",
    ),
    (  # Latulippe: Paul-A. Latulippe, Frontenac 1970; Paul-André Latulippe, Mégantic-Compton 1973
        "1970-04-29:30:1970-04-29:30:0",
        "1973-10-29:25:1973-10-29:25:1",
    ),
    (  # Dumont: Bernard Dumont, Mégantic 1970; Bernard Dumont, Frontenac 1973
        "1970-04-29:62:1970-04-29:62:0",
        "1973-10-29:24:1973-10-29:24:3",
    ),
    (  # Bois: Armand Bois, Saint-Sauveur 1970; Armand Bois, Vanier 1973
        "1970-04-29:92:1970-04-29:92:0",
        "1973-10-29:104:1973-10-29:104:2",
    ),
    (  # Guay: Florian Guay, Dorchester 1970; Florian Guay, Beauce-Nord 1973
        "1970-04-29:24:1970-04-29:24:0",
        "1973-10-29:22:1973-10-29:22:2",
    ),
    (  # Samson: Camille Samson, Rouyn-Noranda 1976; Camil Samson, Rouyn-Noranda-Témiscamingue 1981
        "1976-11-15:85:1976-11-15:85:0",
        "1981-04-13:840:1981-04-13:840:1",
    ),
    (  # Baril PQ: Gilles Baril, Rouyn-Noranda-Témiscamingue 1981; Gilles Baril, Rouyn-Noranda-Témiscamingue 1985; Gilles Baril, Bourget 1989; Gilles Baril, Berthier 1994
        "1981-04-13:840:1981-04-13:840:0",
        "1985-12-02:840:1985-12-02:840:1",
        "1989-09-25:446:1989-09-25:446:1",
        "1994-09-12:425:1994-09-12:425:0",
    ),
    (  # Baril PLQ: Gilles Baril, Rouyn-Noranda-Témiscamingue 1985; Gilles Baril, Rouyn-Noranda-Témiscamingue 1989
        "1985-12-02:840:1985-12-02:840:0",
        "1989-09-25:650:1989-09-25:650:1",
    ),
    (  # Frulla: Liza Frulla-Hébert, Marguerite-Bourgeoys 1989; Liza Frulla, Marguerite-Bourgeoys 1994
        "1989-09-25:462:1989-09-25:462:0",
        "1994-09-12:477:1994-09-12:477:0",
    ),
    (  # Holden: Richard B. Holden, Westmount 1989; Richard B. Holden, Verdun 1994
        "1989-09-25:492:1989-09-25:492:0",
        "1994-09-12:475:1994-09-12:475:1",
    ),
    (  # Blais: Marguerite Blais, Saint-Henri-Sainte-Anne 2014; Marguerite Blais, Prévost 2018
        "2014-04-07:393:144",
        "2018-10-01:582:1240",
    ),
    (  # Legault: François Legault, Rousseau 2008; François Legault, L'Assomption 2012
        "2008-12-08:504:2008-12-08:504:0",
        "2012-09-04:363:2012-09-04:363:0",
    ),
    (  # Bonnardel: François Bonnardel, Shefford 2008; François Bonnardel, Granby 2012
        "2008-12-08:134:2008-12-08:134:0",
        "2012-09-04:133:2012-09-04:133:0",
    ),
    (  # Rebello: François Rebello, La Prairie 2008; François Rebello, Sanguinet 2012
        "2008-12-08:182:2008-12-08:182:0",
        "2012-09-04:177:2012-09-04:177:1",
    ),
    (  # Roy: Sylvie Roy, Lotbinière 2008; Sylvie Roy, Arthabaska 2012
        "2008-12-08:314:2008-12-08:314:0",
        "2012-09-04:323:2012-09-04:323:0",
    ),
    (  # Fréchette: Christine Fréchette, Sanguinet 2022; Christine Fréchette, Trois-Rivières 2026
        "2022-10-03:230:2339",
        "2026-10-05:691:3609",
    ),
    (  # Campeau: Richard Campeau, Bourget 2018; Richard Campeau, Camille-Laurin 2022
        "2018-10-01:370:1209",
        "2022-10-03:370:2452",
    ),
    (  # Beauchemin: Fred Beauchemin, Marguerite-Bourgeoys 2022; Frédéric Beauchemin, Brome-Missisquoi 2026
        "2022-10-03:304:2189",
        "2026-10-05:157:3549",
    ),
    (  # Blanchette Vézina: Maïté Blanchette Vézina, Rimouski 2022; Maïté Blanchette Vézina, La Peltrie 2026
        "2022-10-03:838:2499",
        "2026-10-05:729:3566",
    ),
    (  # Grenier: Fernand Grenier, Frontenac 1970; Fernand Grenier, Lotbinière 1973
        "1970-04-29:30:1970-04-29:30:1",
        "1973-10-29:28:1973-10-29:28:3",
    ),
    (  # Gauthier: Gilles Gauthier, Trois-Rivières 1970; Gilles Gauthier, Champlain 1976
        "1970-04-29:102:1970-04-29:102:1",
        "1976-11-15:32:1976-11-15:32:2",
    ),
    (  # Parizeau: Jacques Parizeau, Ahuntsic 1970; Jacques Parizeau, Crémazie 1973
        "1970-04-29:3:1970-04-29:3:1",
        "1973-10-29:146:1973-10-29:146:1",
    ),
    (  # Malépart: Jean-Claude Malepart, Sainte-Marie 1970; Jean-Claude Malépart, Sainte-Marie 1973
        "1970-04-29:94:1970-04-29:94:1",
        "1973-10-29:140:1973-10-29:140:0",
    ),
    (  # Proulx: Jérôme Proulx, Saint-Jean 1970; Jérôme Proulx, Saint-Jean 1973
        "1970-04-29:88:1970-04-29:88:1",
        "1973-10-29:93:1973-10-29:93:1",
    ),
    (  # Bergeron: Marc Bergeron, Mégantic 1970; Marc Bergeron, Frontenac 1973
        "1970-04-29:62:1970-04-29:62:1",
        "1973-10-29:24:1973-10-29:24:2",
    ),
    (  # Hamel: Paul-Yvon Hamel, Rouville 1970; Paul-Yvon Hamel, Iberville 1973
        "1970-04-29:82:1970-04-29:82:1",
        "1973-10-29:53:1973-10-29:53:1",
    ),
    (  # Marois: Pierre Marois, Chambly 1970; Pierre Marois, Laporte 1973
        "1970-04-29:15:1970-04-29:15:1",
        "1973-10-29:67:1973-10-29:67:1",
    ),
    (  # Lévesque: René Lévesque, Laurier 1970; René Lévesque, Dorion 1973
        "1970-04-29:49:1970-04-29:49:1",
        "1973-10-29:144:1973-10-29:144:1",
    ),
    (  # Michaud: Yves Michaud, Gouin 1970; Yves Michaud, Bourassa 1973
        "1970-04-29:34:1970-04-29:34:1",
        "1973-10-29:139:1973-10-29:139:1",
    ),
    (  # Tremblay: Charles Tremblay, Sainte-Marie 1973; Charles Tremblay, Saint-Hyacinthe 1976
        "1973-10-29:140:1973-10-29:140:1",
        "1976-11-15:51:1976-11-15:51:1",
    ),
    (  # Gautrin: Henri-François Gautrin, Mercier 1976; Henri-François Gautrin, Dorion 1981
        "1976-11-15:142:1976-11-15:142:4",
        "1981-04-13:655:1981-04-13:655:1",
    ),
    (  # Veilleux: Jacques Veilleux, Saint-Jean 1976; Jacques Veilleux, Saint-Jean 1981
        "1976-11-15:93:1976-11-15:93:1",
        "1981-04-13:525:1981-04-13:525:1",
    ),
    (  # Beaudoin: Louise Beaudoin, Jean-Talon 1976; Louise Beaudoin, Louis-Hébert 1985
        "1976-11-15:103:1976-11-15:103:1",
        "1985-12-02:190:1985-12-02:190:1",
    ),
    (  # Leduc: Michel Leduc, Laval 1976; Michel Leduc, Fabre 1985
        "1976-11-15:123:1976-11-15:123:1",
        "1985-12-02:580:1985-12-02:580:1",
    ),
    (  # Bourassa: Robert Bourassa, Mercier 1976; Robert Bourassa, Bertrand 1985
        "1976-11-15:142:1976-11-15:142:1",
        "1985-12-02:555:1985-12-02:555:1",
    ),
    (  # Dean: Robert Dean, Prévost 1985; Robert Dean, Groulx 1989
        "1985-12-02:570:1985-12-02:570:1",
        "1989-09-25:560:1989-09-25:560:1",
    ),
    (  # Beaumier: Yves Beaumier, Nicolet 1985; Yves Beaumier, Champlain 1989
        "1985-12-02:370:1985-12-02:370:1",
        "1989-09-25:680:1989-09-25:680:1",
    ),
    (  # Beaudoin: Denise Beaudoin, Argenteuil 1998; Denise Beaudoin, Mirabel 2007
        "1998-11-30:595:1998-11-30:595:1",
        "2007-03-26:492:2007-03-26:492:1",
    ),
    (  # Grégoire: Marie Grégoire, L'Assomption 1998; Marie Grégoire, Berthier 2003
        "1998-11-30:445:1998-11-30:445:2",
        "2003-04-14:354:2003-04-14:354:1",
    ),
    (  # Shaw: William F. Shaw, Jacques-Cartier 1998; William F. Shaw, D'Arcy-McGee 2003
        "1998-11-30:485:1998-11-30:485:2",
        "2003-04-14:406:2003-04-14:406:3",
    ),
    (  # Méthé: Lucille Méthé, Iberville 2003; Lucille Méthé, Saint-Jean 2008
        "2003-04-14:142:2003-04-14:142:2",
        "2008-12-08:188:2008-12-08:188:2",
    ),
    (  # Proulx: Sébastien Proulx, Laviolette 2003; Sébastien Proulx, Trois-Rivières 2008
        "2003-04-14:584:2003-04-14:584:2",
        "2008-12-08:334:2008-12-08:334:2",
    ),
    (  # Gaudreau: François Gaudreau, Vimont 2007; François Gaudreau, Sainte-Rose 2012
        "2007-03-26:448:2007-03-26:448:1",
        "2012-09-04:447:2012-09-04:447:1",
    ),
    (  # De Martin: Albert De Martin, Huntingdon 2008; Albert De Martin, Huntingdon 2014
        "2008-12-08:148:2008-12-08:148:2",
        "2014-04-07:149:319",
    ),
    (  # Lévesque: Christian Levesque, Lévis 2008; Christian Lévesque, Bellechasse 2012
        "2008-12-08:664:2008-12-08:664:1",
        "2012-09-04:303:2012-09-04:303:1",
    ),
    (  # Gosselin: Jean-François Gosselin, Jean-Lesage 2008; Jean-François Gosselin, La Peltrie 2012
        "2008-12-08:624:2008-12-08:624:2",
        "2012-09-04:603:2012-09-04:603:1",
    ),
    (  # Roy Verville: Monique Roy Verville, La Prairie 2008; Monique Roy Verville, Jean-Talon 2014
        "2008-12-08:182:2008-12-08:182:2",
        "2014-04-07:643:546",
    ),
    (  # Deschamps: Robert Deschamps, Saint-Maurice 2008; Robert Deschamps, Trois-Rivières 2012
        "2008-12-08:342:2008-12-08:342:2",
        "2012-09-04:333:2012-09-04:333:5",
    ),
    (  # Skeete: Christopher Skeete, Vimont 2012; Christopher Skeete, Fabre 2014
        "2012-09-04:449:2012-09-04:449:2",
        "2014-04-07:443:687",
    ),
    (  # Aussant: Jean-Martin Aussant, Nicolet-Bécancour 2012; Jean-Martin Aussant, Pointe-aux-Trembles 2018
        "2012-09-04:329:2012-09-04:329:1",
        "2018-10-01:380:1492",
    ),
    (  # Asselin: Mario Asselin, Taschereau 2012; Mario Asselin, Louis-Hébert 2014
        "2012-09-04:633:2012-09-04:633:2",
        "2014-04-07:653:353",
    ),
    (  # Zanetti: Sol Zanetti, Louis-Hébert 2012; Sol Zanetti, Jean-Lesage 2014
        "2012-09-04:653:2012-09-04:653:4",
        "2014-04-07:623:424",
    ),
    (  # Breton: Daniel Breton, Sainte-Marie-Saint-Jacques 2014; Daniel Breton, Saint-Hyacinthe 2018
        "2014-04-07:389:83",
        "2018-10-01:260:1542",
    ),
    (  # Trudel: Denise Trudel, Charlesbourg 2014; Denise Trudel, Roberval 2018
        "2014-04-07:619:600",
        "2018-10-01:930:1491",
    ),
    (  # Simard: Jean-François Simard, Montmorency 1998; Jean-François Simard, Montmorency 2003; Jean-François Simard, Montmorency 2018; Jean-François Simard, Montmorency 2022; Jean-François Simard, Montmorency 2026
        "1998-11-30:747:1998-11-30:747:0",
        "2003-04-14:672:2003-04-14:672:2",
        "2018-10-01:742:1643",
        "2022-10-03:742:2534",
        "2026-10-05:761:3426",
    ),
)


class Run(NamedTuple):
    person: UUID
    day: date
    area: UUID
    party: UUID | None
    printed: str
    won: bool


def words(text: str) -> list[str]:
    folded = "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))
    return [SHORT.get(word, word) for word in re.split(r"[^a-z]+", folded) if len(word) > 1]


def variant(a: str, b: str) -> bool:
    *left, _ = a.split()
    *right, _ = b.split()
    return set(left) <= set(right) or set(right) <= set(left) or (left[0][:4] == right[0][:4] and len(left[0]) > 3)


def groups(conn: Connection) -> tuple[dict[UUID, list[UUID]], dict[UUID, Run]]:
    rows = conn.execute(
        """select cy.person_id, e.date_from, e.kind = 'general', k.district_area_id, o.party_id, cy.ballot_name,
                  exists (select from outcome w join result_set r on r.id = w.result_set_id
                          where w.option_id = o.id and r.is_default and w.kind = 'elected'),
                  array(select n.text from name n where n.entity_id = k.district_area_id)
           from candidacy cy
           join contest_option o on o.id = cy.option_id
           join contest k on k.id = o.contest_id
           join election e on e.id = k.election_id
           join changeset c on c.id = k.changeset_id
           where c.pipeline = 'ca.quebec' and c.status = 'published'"""
    ).fetchall()
    runs = [
        Run(person, day, area, party, " ".join(words(printed)), won)
        for person, day, _, area, party, printed, won, _ in rows
    ]
    generals = sorted({day for _, day, general, *_ in rows if general})
    tokens = {
        area: {word for label in labels for word in words(label) if len(word) > 2} for *_, area, _, _, _, labels in rows
    }
    generic = {
        word
        for general in generals
        for word, count in Counter(
            word for area in {run.area for run in runs if run.day == general} for word in tokens[area]
        ).items()
        if count > 2
    }

    def near(a: Run, b: Run) -> bool:
        return a.area == b.area or bool((tokens[a.area] & tokens[b.area]) - generic)

    def horizon(day: date, step: int) -> date:
        later = [general for general in generals if general > day]
        return later[min(step, len(later)) - 1] if later else day

    by_surname: dict[str, list[Run]] = defaultdict(list)
    for run in runs:
        if run.printed:
            by_surname[run.printed.split()[-1]].append(run)
    rules: dict[str, list[tuple[Run, Run]]] = {"riding": [], "member": [], "party": [], "variant": []}
    for same in by_surname.values():
        for a in same:
            for b in same:
                if not a.day < b.day <= horizon(a.day, 2):
                    continue
                party = a.party is not None and a.party == b.party
                next_general = b.day <= horizon(a.day, 1)
                if a.printed != b.printed:
                    alone = a.party is None and b.party is None and a.area == b.area
                    if next_general and (party or alone) and near(a, b) and variant(a.printed, b.printed):
                        rules["variant"].append((a, b))
                elif next_general and near(a, b):
                    rules["riding"].append((a, b))
                elif next_general and a.won and party:
                    rules["member"].append((a, b))
                elif party and near(a, b):
                    rules["party"].append((a, b))

    parent: dict[UUID, UUID] = {}
    days: dict[UUID, set[date]] = {run.person: {run.day} for run in runs}

    def root(node: UUID) -> UUID:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for rule, pairs in rules.items():
        sides = Counter(side for a, b in pairs for side in ((a.person, b.day), (b.person, a.day)))
        matched = clashes = 0
        for a, b in sorted(pairs, key=lambda pair: (pair[0].day, pair[1].day)):
            if sides[a.person, b.day] != 1 or sides[b.person, a.day] != 1:
                continue
            left, right = root(a.person), root(b.person)
            if left == right:
                continue
            if days[left] & days[right]:
                clashes += 1
                continue
            parent[left] = right
            days[right] |= days.pop(left)
            matched += 1
        print(f"{rule}: {len(pairs)} pairs, {matched} joined, {clashes} refused for running twice on one day")

    for keys in SAME:
        found = dict(
            conn.execute(
                """select x.value, cy.person_id from candidacy cy
                   join external_id x on x.entity_id = cy.id and x.scheme = %s
                   where x.value = any(%s)""",
                ["ca.quebec.candidacy", list(keys)],
            ).fetchall()
        )
        if len(found) != len(keys):
            raise ValueError(f"{SOURCE}: {sorted(set(keys) - set(found))} are not candidacies")
        first, *rest = (root(found[key]) for key in keys)
        for node in rest:
            if node != first:
                days[first] |= days.pop(node, set())
                parent[node] = first
    print(f"{len(SAME)} hand joins")

    groups: dict[UUID, list[UUID]] = defaultdict(list)
    for node in list(parent):
        groups[root(node)].append(node)
    return {key: people for key, people in groups.items() if len(people) > 1}, {run.person: run for run in runs}


def emit() -> Path:
    with connect() as conn:
        found, runs = groups(conn)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Quebec provincial candidates matched across elections by printed name, ignoring initials: in the same or a namesake riding at the next general election, as a sitting member under the same party at the next general election, in the same or a namesake riding under the same party two general elections later, or with a shortened or extended given name in the same or a namesake riding under the same party, or as an independent in the same riding, at the next general election, and members joined by hand on the Assemblée nationale's member pages",
            polity="CA-QC",
            kind="match",
        )
        sizes = Counter(len(people) for people in found.values())
        print(
            f"{sum(sizes.values())} people from {sum(n * c for n, c in sizes.items())} candidacies; sizes {sorted(sizes.items())}"
        )
        for people in found.values():
            survivor = min(people, key=lambda person: (runs[person].day, person.int))
            for person in people:
                if person != survivor:
                    cs.add("entity_merge", entity_id=person, into_id=survivor)
        return cs.write()
