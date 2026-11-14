from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.licenses import UNLICENSED
from ea.store import connect

SOURCE = "de.ddr_abstimmungen"
VERSION = "1"
URL_1951 = (
    "https://www.gvoon.de/art/dokumente/1955/statistisches-jahrbuch-ddr-1955-1/pdf/"
    "statistisches-jahrbuch-ddr-1955-1-seite_088.pdf"
)
URL_1968 = (
    "https://www.gvoon.de/art/dokumente/1969/verfassung-ddr-dokumente-kommentar-1969-band-1/pdf/"
    "verfassung-ddr-dokumente-kommentar-1969-band-1-seite_191.pdf"
)
D1951, D1951_END, D1968 = date(1951, 6, 3), date(1951, 6, 6), date(1968, 4, 6)
VOTES, REGISTERED, CAST, VALID, INVALID, VOTE_SHARE = 1, 2, 3, 4, 5, 13
SHARE_ONLY = 4
SYSTEM = (
    "A vote on one question put to every citizen entitled to vote in the German Democratic Republic. The ballot "
    "offered a Yes and a No, and the Central Voting Commission (Zentrale Abstimmungskommission) set the result. The "
    "state did not run a free campaign: the National Front's single list of candidates and its call to vote Yes "
    "framed both polls."
)
NOTE_1951 = (
    "Volksbefragung of 3 to 6 June 1951 against the remilitarization of Germany and for a peace treaty, as the "
    "Statistisches Jahrbuch der Deutschen Demokratischen Republik 1955 prints it (page 88, table 4). The table "
    "gives the number of citizens entitled to vote and, for each part, the turnout, the share of valid ballots, "
    "and the shares of Yes and No in the valid ballots, as percentages. The Yes and No counts are not printed, "
    "so the archive stores the shares as printed and does not derive counts from them. The table splits citizens "
    "over 18 from those aged 16 to 18."
)
NOTE_1951_ALL = (
    "All citizens entitled to vote: the Yearbook prints 13,703,077 eligible voters for the whole republic including "
    "the democratic sector of Greater Berlin, a turnout of 99.45 percent, and 96.02 percent Yes; for East Berlin "
    "952,278 eligible voters, 98.31 percent turnout, and 97.22 percent Yes. No share is printed for this group."
)
NOTE_1951_ADULTS = (
    "Citizens over 18, by Land and for the democratic sector of Greater Berlin. Special polling stations "
    "(Sonderabstimmungslokale, 6,934 eligible, 97.53 percent Yes) and the republic's total excluding Berlin "
    "(12,206,022 eligible, 95.84 percent Yes) are printed but not stored as units. The republic's turnout was 99.40 "
    "percent."
)
NOTE_1951_YOUTH = (
    "Citizens aged 16 to 18, by Land and for the democratic sector of Greater Berlin. The republic's turnout was "
    "99.24 percent."
)
NOTE_1968 = (
    "Result of the Volksentscheid of 6 April 1968 on the draft of the socialist constitution, as the Central Voting "
    "Commission reported it to the Volkskammer on 7 April 1968 and as Die Verfassung der Deutschen Demokratischen "
    "Republik: Dokumente, Kommentar (Staatsverlag der DDR, 1969), volume 1, page 191, prints it, by Bezirk. In "
    "every row the Yes and No votes add up to the valid votes and the valid and invalid votes to the votes cast, "
    "and the Bezirke add up to the republic. The commission counted 94.49 percent of those entitled to vote as "
    "Yes. Under the enabling law the constitution passed if a majority of those entitled to vote approved it."
)
LANDS = {
    "BB": ("Brandenburg", "Brandenburg"),
    "MV": ("Mecklenburg", "Mecklenburg"),
    "ST": ("Sachsen-Anhalt", "Saxony-Anhalt"),
    "TH": ("Thüringen", "Thuringia"),
    "SN": ("Sachsen", "Saxony"),
    "BE": ("Groß-Berlin, demokratischer Sektor", "Greater Berlin, democratic sector"),
}
NATION = ("DDR", "Deutsche Demokratische Republik", "German Democratic Republic")
ALL_1951 = {"DDR": (13703077, 96.02, None), "BE": (952278, 97.22, None)}
ADULTS_1951 = {
    "BB": (1832149, 96.82, 3.18),
    "MV": (1368706, 95.99, 4.01),
    "ST": (2843489, 96.71, 3.29),
    "TH": (2008571, 95.27, 4.73),
    "SN": (4146173, 95.04, 4.96),
    "BE": (922008, 97.21, 2.79),
    "DDR": (13128030, 95.94, 4.06),
}
YOUTH_1951 = {
    "BB": (87529, 98.07, 1.93),
    "MV": (76467, 98.27, 1.73),
    "ST": (129504, 98.59, 1.41),
    "TH": (88258, 97.45, 2.55),
    "SN": (162516, 97.70, 2.30),
    "BE": (30270, 97.62, 2.38),
    "DDR": (575047, 97.99, 2.01),
}
BEZIRKE = {
    "BE": "Berlin",
    "RO": "Rostock",
    "SN": "Schwerin",
    "NB": "Neubrandenburg",
    "PO": "Potsdam",
    "FF": "Frankfurt",
    "CB": "Cottbus",
    "MD": "Magdeburg",
    "HA": "Halle",
    "ER": "Erfurt",
    "GE": "Gera",
    "SH": "Suhl",
    "DD": "Dresden",
    "LE": "Leipzig",
    "KM": "Karl-Marx-Stadt",
}
ROWS_1968 = {
    "DDR": (12208986, 11536803, 409733, 11970889, 11946536, 24353),
    "BE": (821075, 746811, 46998, 795435, 793809, 1626),
    "RO": (578783, 557031, 11727, 569177, 568758, 419),
    "SN": (402945, 386829, 8697, 396606, 395526, 1080),
    "NB": (423627, 408262, 7134, 415787, 415396, 391),
    "PO": (800294, 758885, 26925, 786868, 785810, 1058),
    "FF": (461253, 441527, 12337, 454240, 453864, 376),
    "CB": (586608, 548173, 21669, 570312, 569842, 470),
    "MD": (932152, 885739, 27685, 914196, 913424, 772),
    "HA": (1370313, 1307830, 32670, 1343426, 1340500, 2926),
    "ER": (887409, 840551, 31214, 874163, 871765, 2398),
    "GE": (527760, 501959, 16260, 519217, 518219, 998),
    "SH": (395467, 374527, 14263, 389184, 388790, 394),
    "DD": (1383608, 1300627, 55381, 1359419, 1356008, 3411),
    "LE": (1099286, 1033080, 38826, 1075222, 1071906, 3316),
    "KM": (1538406, 1444972, 57947, 1507637, 1502919, 4718),
}


def check() -> None:
    total = ROWS_1968["DDR"]
    for code, (registered, yes, no, cast, valid, invalid) in ROWS_1968.items():
        if yes + no != valid or valid + invalid != cast or cast > registered:
            raise ValueError(f"1968 {code}: the row does not add up")
    for index in range(6):
        if sum(row[index] for code, row in ROWS_1968.items() if code != "DDR") != total[index]:
            raise ValueError(f"1968: column {index} does not add up to the republic")
    for table in (ADULTS_1951, YOUTH_1951):
        for code, (_, yes, no) in table.items():
            if abs(yes + no - 100) > 0.005:
                raise ValueError(f"1951 {code}: the shares do not add up to 100")
    if ALL_1951["DDR"][0] != ADULTS_1951["DDR"][0] + YOUTH_1951["DDR"][0]:
        raise ValueError("1951: the age groups do not add up to all citizens")


def fetch() -> None:
    raw.fetch(SOURCE, URL_1951, suffix=".pdf")
    raw.fetch(SOURCE, URL_1968, suffix=".pdf")


def parse() -> None:
    check()


def emit() -> Path:
    check()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="GDR plebiscites: the 1951 Volksbefragung by Land and the 1968 constitutional Volksentscheid by Bezirk",
            polity="DE",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        germany = cs.polity("DE")
        for scheme, noun in (("de.ddr_nation", "republic"), ("de.ddr_land", "state"), ("de.ddr_bezirk", "district")):
            cs.add("scheme", code=scheme, polity_id=germany, kind="administrative", rank=1, noun=noun)

        def unit_for(scheme: str, code: str, german: str, english: str) -> UUID:
            unit = cs.unit(scheme, code)
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=germany,
                    scheme_code=scheme,
                    code=code,
                    area_relation="unlocated",
                    area_id=None,
                    kind="standard",
                    valid_from=None,
                )
                name(unit, english)
                name(unit, german, "de", "native")
            return unit

        nation = unit_for("de.ddr_nation", *NATION[:1], NATION[1], NATION[2])
        lands = {code: unit_for("de.ddr_land", code, german, english) for code, (german, english) in LANDS.items()}
        bezirke = {code: unit_for("de.ddr_bezirk", code, label, label) for code, label in BEZIRKE.items()}

        system = cs.key("de.electoral_system", "ddr-abstimmung")
        cs.add("electoral_system", id=system, polity_id=germany, rules={"formula": "referendum"}, rules_version=1)
        name(system, "GDR plebiscites")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)

        def add_source(publisher: str, title: str, url: str, notes: str) -> UUID:
            source = cs.source(publisher, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=publisher,
                title=title,
                url=url,
                license=UNLICENSED,
                notes=notes,
            )
            cs.artifact(raw.cached(SOURCE, url), source)
            return source

        yearbook = add_source(
            "Staatliche Zentralverwaltung für Statistik",
            "Statistisches Jahrbuch der Deutschen Demokratischen Republik 1955",
            URL_1951,
            "VII. Wahlen, table 4: Volksbefragung vom 3. bis 6. Juni 1951, printed page 88; a scan of the yearbook "
            "hosted by gvoon.de.",
        )
        commission = add_source(
            "Zentrale Abstimmungskommission der DDR",
            "Gesamtergebnis des Volksentscheides vom 6. April 1968 über die Verfassung der Deutschen Demokratischen "
            "Republik",
            URL_1968,
            "Printed on page 191 of Die Verfassung der Deutschen Demokratischen Republik: Dokumente, Kommentar, "
            "volume 1 (Staatsverlag der DDR, 1969); a scan hosted by gvoon.de.",
        )

        values: list[tuple[str, str, str | None, int, float, int]] = []

        def election_for(start: date, end: date, english: str, german: str, key: str) -> tuple[UUID, str]:
            election = cs.key("de.election", f"{start}:{key}")
            cs.add(
                "election",
                id=election,
                polity_id=germany,
                kind="referendum",
                date_from=start,
                date_to=end,
                status="held",
            )
            name(election, english)
            name(election, german, "de", "native")
            path = f"de/{start}-{key}"
            cs.add("slug", path=path, entity_id=election)
            return election, path

        def contest_for(
            election: UUID,
            path: str,
            key: str,
            english: str,
            german: str,
            kind: str,
            text: str | None,
            text_de: str | None,
            url: str,
            source: UUID,
        ) -> tuple[UUID, UUID, UUID]:
            contest = cs.key("de.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                electoral_system_id=system,
                round_number=1,
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type=kind)
            name(contest, english)
            name(contest, german, "de", "native")
            cs.add("slug", path=f"{path}/question", entity_id=contest)
            yes, no = (cs.key("de.option", f"{key}:{answer}") for answer in ("yes", "no"))
            for position, (option, label, label_en) in enumerate(((yes, "Ja", "Yes"), (no, "Nein", "No")), 1):
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position,
                    ballot_label=label,
                )
                name(option, label_en)
            for lang, body in (("en", text), ("de", text_de)):
                if body is not None:
                    cs.add(
                        "ballot_question_text",
                        contest_id=contest,
                        kind="text",
                        lang=lang,
                        body=body,
                        url=url,
                        source_id=source,
                        locator=None,
                    )
            return contest, yes, no

        def result_set_for(
            election: UUID, contest: UUID, key: str, source: UUID, default: bool, note: str, outcome: tuple[UUID, UUID]
        ) -> UUID:
            result_set = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=default,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
            cs.add("outcome", result_set_id=result_set, option_id=outcome[0], kind="won")
            cs.add("outcome", result_set_id=result_set, option_id=outcome[1], kind="lost")
            return result_set

        election, path = election_for(
            D1951,
            D1951_END,
            "June 1951 East German referendum on remilitarization",
            "Volksbefragung der DDR 1951",
            "volksbefragung",
        )
        contest, yes, no = contest_for(
            election,
            path,
            f"{D1951}:volksbefragung",
            "Against the remilitarization of Germany and for a peace treaty",
            "Gegen die Remilitarisierung Deutschlands und für den Abschluß eines Friedensvertrages",
            "plebiscite",
            "Against the remilitarization of Germany and for the conclusion of a peace treaty with Germany",
            "Gegen die Remilitarisierung Deutschlands und für den Abschluß eines Friedensvertrages mit Deutschland",
            URL_1951,
            yearbook,
        )
        for key, default, note, table, units in (
            ("all", True, f"{NOTE_1951} {NOTE_1951_ALL}", ALL_1951, {"DDR": nation, "BE": lands["BE"]}),
            ("adults", False, f"{NOTE_1951} {NOTE_1951_ADULTS}", ADULTS_1951, {**lands, "DDR": nation}),
            ("youth", False, f"{NOTE_1951} {NOTE_1951_YOUTH}", YOUTH_1951, {**lands, "DDR": nation}),
        ):
            result_set = result_set_for(election, contest, f"{D1951}:{key}", yearbook, default, note, (yes, no))
            for code, (registered, yes_share, no_share) in table.items():
                row = (str(result_set), str(units[code]))
                values.append((*row, None, REGISTERED, registered, 0))
                values.append((*row, str(yes), VOTE_SHARE, yes_share, SHARE_ONLY))
                if no_share is not None:
                    values.append((*row, str(no), VOTE_SHARE, no_share, SHARE_ONLY))

        election, path = election_for(
            D1968,
            D1968,
            "6 April 1968 East German constitutional referendum",
            "Volksentscheid über die Verfassung der DDR 1968",
            "volksentscheid",
        )
        contest, yes, no = contest_for(
            election,
            path,
            f"{D1968}:volksentscheid",
            "Constitution of the German Democratic Republic",
            "Verfassung der Deutschen Demokratischen Republik",
            "mandatory_referendum",
            None,
            None,
            URL_1968,
            commission,
        )
        result_set = result_set_for(election, contest, f"{D1968}:bezirke", commission, True, NOTE_1968, (yes, no))
        for code, (registered, yes_votes, no_votes, cast, valid, invalid) in ROWS_1968.items():
            row = (str(result_set), str(nation if code == "DDR" else bezirke[code]))
            for option, measure, value in (
                (str(yes), VOTES, yes_votes),
                (str(no), VOTES, no_votes),
                (None, REGISTERED, registered),
                (None, CAST, cast),
                (None, VALID, valid),
                (None, INVALID, invalid),
            ):
                values.append((*row[:2], option, measure, value, 0))

        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "measure": pl.Int16,
                    "value": pl.Float64,
                    "flags": pl.Int16,
                },
                orient="row",
            ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16)),
        )
        return cs.write()
