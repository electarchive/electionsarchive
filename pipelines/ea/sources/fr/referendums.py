import html
import re
from datetime import date
from pathlib import Path
from uuid import UUID, uuid5

import polars as pl

from ea import raw
from ea.changeset import NAMESPACE, Changeset
from ea.store import DATA, connect

SOURCE = "fr.referendums"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
FIGURE = r"\d{1,3}(?:[ .]\d{3})*"
VOTES, REGISTERED, VALID, INVALID, VOTERS = 1, 2, 4, 5, 14
COUNCIL_URL = "https://www.conseil-constitutionnel.fr/decision/{}.htm"
COUNCIL_PUBLISHER = "Conseil constitutionnel"
COUNCIL_TITLE = "Décisions REF : proclamation des résultats des référendums"
COUNCIL_LICENSE = (
    "Freely reproducible with the Conseil constitutionnel's site cited as the source, without altering the text "
    "(legal notice)"
)
SENATE_URL = "https://www.senat.fr/reglement/pouvoirs_publics4.html"
SENATE_PUBLISHER = "Sénat"
SENATE_TITLE = "Recueil des textes relatifs aux pouvoirs publics : Constitution du 4 octobre 1958"
MINISTRY_PUBLISHER = "Ministère de l'Intérieur"
MINISTRY_TITLE = "Résultats des référendums de 1992, 2000 et 2005"
MINISTRY_FILES = {
    date(1992, 9, 20): (
        "https://static.data.gouv.fr/c6/cab108ed297b9c87bd77b76db521b30e226d46d36579ee5e709751aba37846.xls",
        "Départements",
        (2, 4, 6, 7, 9),
    ),
    date(2000, 9, 24): (
        "https://static.data.gouv.fr/b9/9517fe9620351baa5c57e47df23590207a2f62dca8e6ba7d26bbda8451e865.xls",
        "Departements",
        (2, 5, 10, 14, 18),
    ),
    date(2005, 5, 29): (
        "https://static.data.gouv.fr/50/cd65a14d56f53f704af4922d75223773ca39b5f92ceecd7d69a07ed1e19c53.xls",
        "Départements",
        (2, 4, 6, 8, 11),
    ),
}
OVERSEAS = {
    "ZA": "971",
    "ZB": "972",
    "ZC": "973",
    "ZD": "974",
    "ZM": "976",
    "ZN": "988",
    "ZP": "987",
    "ZS": "975",
    "ZW": "986",
    "ZZ": "ZZ",
}
REFERENDUMS = {
    date(1958, 9, 28): (
        None,
        "Constitution of the Fifth Republic",
        "Constitution de la Ve République",
    ),
    date(1961, 1, 8): (
        "1961/614ref",
        "Self-determination of the Algerian people",
        "Autodétermination de l'Algérie",
    ),
    date(1962, 4, 8): (
        "1962/627ref",
        "Évian Accords on Algerian independence",
        "Accords d'Évian sur l'indépendance de l'Algérie",
    ),
    date(1962, 10, 28): (
        "1962/629REF",
        "Direct popular election of the President",
        "Élection du président de la République au suffrage universel direct",
    ),
    date(1969, 4, 27): (
        "1969/6910ref",
        "Creation of regions and reform of the Senate",
        "Réforme du Sénat et régionalisation",
    ),
    date(1972, 4, 23): (
        "1972/7211ref",
        "Enlargement of the European Communities",
        "Élargissement de la Communauté économique européenne",
    ),
    date(1988, 11, 6): (
        "1988/8814ref",
        "Statute of New Caledonia",
        "Statut de la Nouvelle-Calédonie",
    ),
    date(1992, 9, 20): (
        "1992/9219ref",
        "Ratification of the Maastricht Treaty on European Union",
        "Ratification du traité de Maastricht sur l'Union européenne",
    ),
    date(2000, 9, 24): (
        "2000/200029REF",
        "Five-year presidential term",
        "Quinquennat",
    ),
    date(2005, 5, 29): (
        "2005/200538REF",
        "Ratification of the Treaty establishing a Constitution for Europe",
        "Ratification du traité établissant une Constitution pour l'Europe",
    ),
}
LEGIFRANCE_PUBLISHER = "Direction de l'information légale et administrative"
LEGIFRANCE_TITLE = "Légifrance : décrets décidant de soumettre un projet de loi au référendum"
QUESTIONS = {
    date(1992, 9, 20): (
        "https://www.legifrance.gouv.fr/jorf/id/JORFTEXT000000504610",
        (
            "Approuvez-vous le projet de loi soumis au peuple français par le Président de la République autorisant la "
            "ratification du Traité sur l'Union européenne ?"
        ),
    ),
    date(2000, 9, 24): (
        "https://www.legifrance.gouv.fr/jorf/id/JORFTEXT000000766148",
        (
            "Approuvez-vous le projet de loi constitutionnelle fixant la durée du mandat du Président de la République à "
            "cinq ans ?"
        ),
    ),
    date(2005, 5, 29): (
        "https://www.legifrance.gouv.fr/jorf/id/JORFTEXT000000627571",
        (
            "Approuvez-vous le projet de loi qui autorise la ratification du traité établissant une Constitution pour "
            "l'Europe ?"
        ),
    ),
    date(1972, 4, 23): (
        "https://www.legifrance.gouv.fr/jorf/id/JORFTEXT000000513770",
        (
            "Approuvez-vous, dans les perspectives nouvelles qui s'ouvrent à l'Europe, le projet de loi soumis au peuple "
            "français par le Président de la République, et autorisant la ratification du traité relatif à l'adhésion de "
            "la Grande-Bretagne, du Danemark, de l'Irlande et de la Norvège aux Communautés européennes ?"
        ),
    ),
    date(1988, 11, 6): (
        "https://www.legifrance.gouv.fr/jorf/id/JORFTEXT000000872797",
        (
            "Approuvez-vous le projet de loi soumis au peuple français par le Président de la République portant "
            "dispositions statutaires et préparatoires à l'autodétermination de la Nouvelle-Calédonie en 1998 ?"
        ),
    ),
}
MONTHS = [
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
]
UNBALANCED = {(date(2000, 9, 24), code) for code in ("35", "61", "67", "72", "74", "90")}
UNBALANCED_CAVEAT = (
    "In six départements (Ille-et-Vilaine, Orne, Bas-Rhin, Sarthe, Haute-Savoie, and Territoire de Belfort) the "
    "file's Yes and No votes differ from its valid votes by 1 to 20; both are kept as printed."
)
SYSTEM = (
    "The President of the Republic puts a bill to the people on the Government's or Parliament's proposal "
    "(article 11 of the Constitution), or a constitutional revision passed by both houses (article 89). The bill "
    "passes if Yes wins a majority of valid votes; there is no turnout quorum. The Conseil constitutionnel, from "
    "1961, oversees the vote and proclaims the result."
)
COUNCIL_METHOD = (
    "National totals as the Conseil constitutionnel proclaimed them, after its corrections and annulments, for every "
    "département and territory that voted and, from 1988, French voters abroad. Invalid "
    "ballots are voters less valid votes (blank and spoiled ballots together)."
)
SENATE_METHOD = (
    "Yes and No votes as the national commission set up by ordinance 58-734 of 20 August 1958 proclaimed them on "
    "4 October 1958 (Journal officiel of 5 October 1958), quoted in the Senate's collection of texts on the "
    "Constitution. The proclamation covered metropolitan France, Algeria, the overseas départements, and the "
    "overseas territories; registered voters and ballots cast are not in the source."
)
MINISTRY_METHOD = (
    "Results by département, overseas collectivity, and French voters abroad from the Interior Ministry's file. It "
    "predates the Conseil constitutionnel's corrections, so its sums differ slightly from the proclaimed totals. "
    "Invalid ballots are voters less valid votes."
)
MINISTRY_EXACT = (
    "Results by département, overseas collectivity, and French voters abroad from the Interior Ministry's file. Its "
    "sums equal the totals the Conseil constitutionnel proclaimed. Invalid ballots are voters less valid votes."
)


def text_of(path: Path) -> str:
    page = path.read_text(errors="replace")
    page = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.DOTALL)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page))).replace("\xa0", " ")


def number(text: str) -> int:
    return int(re.sub(r"[ .]", "", text))


def fetch() -> None:
    raw.fetch(SOURCE, SENATE_URL, suffix=".html")
    for decision, _, _ in REFERENDUMS.values():
        if decision is not None:
            raw.fetch(SOURCE, COUNCIL_URL.format(decision), suffix=".html")
    for url, _, _ in MINISTRY_FILES.values():
        raw.fetch(SOURCE, url, suffix=".xls")


def parse() -> None:
    rows = []
    for day, (decision, _, _) in REFERENDUMS.items():
        if decision is None:
            text = text_of(raw.cached(SOURCE, SENATE_URL).path)
            found = re.search(r"a été : ([\d ]+) « oui » contre ([\d ]+) « non »", text)
            if found is None:
                raise ValueError("1958: no result in the Senate's page")
            rows.append((day, "FR", None, None, None, number(found[1]), number(found[2])))
            continue
        text = text_of(raw.cached(SOURCE, COUNCIL_URL.format(decision)).path)
        found = re.search(
            rf"inscrits : ({FIGURE})(?: ;)? Votants : ({FIGURE})(?: ;)? Suffrages exprimés? : ({FIGURE})(?: ;)? "
            rf"(?:Majorité absolue : {FIGURE} )?Oui : ({FIGURE})(?: ;)? Non : ({FIGURE})",
            text,
            flags=re.IGNORECASE,
        )
        if found is None:
            raise ValueError(f"{day}: no result in the proclamation")
        registered, voters, valid, yes, no = (number(value) for value in found.groups())
        if yes + no != valid:
            raise ValueError(f"{day}: Yes and No do not add up to valid votes")
        rows.append((day, "FR", registered, voters, valid, yes, no))
    for day, (url, sheet, columns) in MINISTRY_FILES.items():
        frame = pl.read_excel(raw.cached(SOURCE, url).path, sheet_name=sheet, engine="calamine", has_header=False)
        frame = frame.filter(pl.col("column_2").str.contains(r"^\d+$"))
        for row in frame.iter_rows(named=True):
            code = row["column_0"].strip()
            code = OVERSEAS.get(code, code.zfill(2))
            registered, voters, valid, yes, no = (int(row[f"column_{column}"]) for column in columns)
            if yes + no != valid and (day, code) not in UNBALANCED:
                raise ValueError(f"{day} {code}: the file's figures do not add up")
            rows.append((day, code, registered, voters, valid, yes, no))
    frame = pl.DataFrame(
        rows, schema=["day", "unit", "registered", "voters", "valid", "yes", "no"], orient="row"
    ).with_columns(invalid=pl.col("voters") - pl.col("valid"))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "results.parquet")
    print(
        frame.with_columns(level=pl.when(pl.col("unit") == "FR").then(pl.lit("national")).otherwise(pl.lit("units")))
        .group_by("day", "level")
        .agg(pl.len(), pl.col("registered", "voters", "valid", "yes", "no").sum())
        .sort("day", "level")
    )


def emit() -> Path:
    results = pl.read_parquet(PARSED / "results.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "France's ten national referendums of the Fifth Republic, 1958–2005, with the proclaimed national "
                "totals and, for 1992, 2000, and 2005, results by département from the Interior Ministry"
            ),
            polity="FR",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        france = cs.polity("FR")
        council = cs.source(COUNCIL_PUBLISHER, COUNCIL_TITLE)
        cs.add(
            "source",
            id=council,
            kind="official_body",
            publisher=COUNCIL_PUBLISHER,
            title=COUNCIL_TITLE,
            url="https://www.conseil-constitutionnel.fr/les-decisions/type/REF",
            license=COUNCIL_LICENSE,
        )
        senate = cs.source(SENATE_PUBLISHER, SENATE_TITLE)
        cs.add(
            "source",
            id=senate,
            kind="official_body",
            publisher=SENATE_PUBLISHER,
            title=SENATE_TITLE,
            url=SENATE_URL,
            license="Public record",
        )
        ministry = cs.source(MINISTRY_PUBLISHER, MINISTRY_TITLE)
        cs.add(
            "source",
            id=ministry,
            kind="official_body",
            publisher=MINISTRY_PUBLISHER,
            title=MINISTRY_TITLE,
            url="https://www.data.gouv.fr/datasets/referendum-de-2005-571473",
            license="Licence Ouverte v1.0",
            license_url="https://www.etalab.gouv.fr/licence-ouverte-open-licence",
        )
        legifrance = cs.source(LEGIFRANCE_PUBLISHER, LEGIFRANCE_TITLE)
        cs.add(
            "source",
            id=legifrance,
            kind="official_body",
            publisher=LEGIFRANCE_PUBLISHER,
            title=LEGIFRANCE_TITLE,
            url="https://www.legifrance.gouv.fr/",
            license="Licence Ouverte v2.0",
            license_url="https://www.etalab.gouv.fr/licence-ouverte-open-licence",
        )
        cs.artifact(raw.cached(SOURCE, SENATE_URL), senate)
        for decision, _, _ in REFERENDUMS.values():
            if decision is not None:
                cs.artifact(raw.cached(SOURCE, COUNCIL_URL.format(decision)), council)
        for url, _, _ in MINISTRY_FILES.values():
            cs.artifact(raw.cached(SOURCE, url), ministry)

        system = cs.key("fr.electoral_system", "referendum")
        cs.add("electoral_system", id=system, polity_id=france, rules={"formula": "referendum"}, rules_version=1)
        name(system, "French national referendum")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)
        nation = cs.unit("fr.nation", "FR")
        values = []
        for day, (decision, english, french) in REFERENDUMS.items():
            key = f"{day}:referendum"
            election = cs.key("fr.election", key)
            cs.add(
                "election", id=election, polity_id=france, kind="referendum", date_from=day, date_to=day, status="held"
            )
            month = f"{day:%B} " if day.year == 1962 else ""
            name(election, f"{month}{day.year} French referendum")
            name(
                election,
                f"Référendum du {day.day}{'er' if day.day == 1 else ''} {MONTHS[day.month - 1]} {day.year}",
                "fr",
                "native",
            )
            path = f"fr/{day}-referendum"
            cs.add("slug", path=path, entity_id=election)
            contest = cs.key("fr.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                electoral_system_id=system,
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type="plebiscite")
            if day in QUESTIONS:
                url, body = QUESTIONS[day]
                cs.add(
                    "ballot_question_text",
                    id=uuid5(NAMESPACE, f"ballot_question_text\x1f{contest}\x1ffr"),
                    contest_id=contest,
                    kind="text",
                    lang="fr",
                    body=body,
                    url=url,
                    source_id=legifrance,
                )
            name(contest, english)
            name(contest, french, "fr", "native")
            cs.add("slug", path=f"{path}/question", entity_id=contest)
            options = {}
            for position, (answer, label) in enumerate((("yes", "Oui"), ("no", "Non")), 1):
                options[answer] = cs.key("fr.option", f"{key}:{answer}")
                cs.add(
                    "contest_option",
                    id=options[answer],
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position,
                    ballot_label=label,
                )
            rows = results.filter(pl.col("day") == day)
            national = rows.filter(pl.col("unit") == "FR").row(0, named=True)
            winner = "yes" if national["yes"] > national["no"] else "no"
            sets = {key: (rows.filter(pl.col("unit") == "FR"), True, council if decision else senate)}
            units = rows.filter(pl.col("unit") != "FR")
            if units.height:
                sets[f"{key}:departements"] = (units, False, ministry)
            for set_key, (rows_in, default, source) in sets.items():
                result_set = cs.key(f"{SOURCE}.result_set", set_key)
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
                    completeness=1 if default else min(1, round(units["valid"].sum() / national["valid"], 4)),
                )
                if default:
                    note = COUNCIL_METHOD if decision else SENATE_METHOD
                else:
                    exact = all(
                        units[column].sum() == national[column] for column in ("registered", "voters", "yes", "no")
                    )
                    note = MINISTRY_EXACT if exact else MINISTRY_METHOD
                cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
                if not default and any(unbalanced[0] == day for unbalanced in UNBALANCED):
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=UNBALANCED_CAVEAT)
                for answer, option in options.items():
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="won" if answer == winner else "lost",
                    )
                for row in rows_in.iter_rows(named=True):
                    unit = nation if row["unit"] == "FR" else cs.unit("fr.departement", row["unit"], on=day)
                    for option, measure, value in (
                        (options["yes"], VOTES, row["yes"]),
                        (options["no"], VOTES, row["no"]),
                        (None, REGISTERED, row["registered"]),
                        (None, VOTERS, row["voters"]),
                        (None, VALID, row["valid"]),
                        (None, INVALID, row["invalid"]),
                    ):
                        if value is not None:
                            values.append(
                                (str(result_set), str(unit), None if option is None else str(option), measure, value)
                            )
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "measure": pl.Int16,
                    "value": pl.Int64,
                },
                orient="row",
            ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
        )
        return cs.write()
