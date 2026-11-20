import io
import zipfile
from datetime import date

import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "fr.presidentielle"
DAY = date(1848, 12, 10)
ZIP_URL = "https://conflit-politique-data.ams3.cdn.digitaloceanspaces.com/zip/pres1848_csv.zip"
MEMBER = "pres1848_csv/pres1848comm.csv"
# Production holds France under the id its first pipeline minted, not the derived one.
FRANCE = "01a13b8a-b1bc-7045-aa86-d470ebdf31b2"
PRESIDENCY = "123c5b56-1c14-597f-8ce7-bff6d1ca2a1f"
NATION_AREA = "01a13b8a-b1cf-7371-8194-5f2639c265b0"
# The source codes each commune by today's département. The 86 départements of 1848 are today's, except that the
# Seine and the Seine-et-Oise were later split up and Corsica was one département.
DEPARTEMENT_1848 = {
    "75": "75",
    "92": "75",
    "93": "75",
    "94": "75",
    "78": "78",
    "91": "78",
    "95": "78",
    "2A": "20",
    "2B": "20",
}
UNITS = {
    "01": "b622c0dd-828a-5da6-9a8a-8d043ba82221",
    "02": "ab605372-7a0a-50d5-b9e1-517ad4232e5e",
    "03": "2eacc2ac-13c6-5afc-ad24-9907f572a410",
    "04": "33e77ae2-e7dc-5dab-a123-e3810269b1fa",
    "05": "cd34e2d6-2383-5407-a030-6d7584d8be85",
    "07": "c88c6154-036e-5096-8d13-fdc7a2b9dfb0",
    "08": "e33ede70-6b9c-5139-b85d-c69458181cf4",
    "09": "40a6d852-8e5e-5248-aba0-f13c99727ee0",
    "10": "1e2cf3fa-6542-5e3e-a1b3-8a8df4b0174c",
    "11": "a777ba14-dd98-517a-8265-d953c55404da",
    "12": "0ec8d91c-e276-554b-9e63-2599fab45ce8",
    "13": "03761006-c2ec-5c84-8241-1af31b710498",
    "14": "ad5482ac-fbdf-5246-8a02-8b709e786b3e",
    "15": "5518ca07-5dd3-526c-b4b6-38d3466347f9",
    "16": "8b58bcac-5310-5c78-853c-30a005dfd3a7",
    "17": "b83729d2-d796-5c32-9581-b1cf3f132b61",
    "18": "45b2871b-78d1-5478-b9e3-a38e3230886f",
    "19": "76e761d2-00b0-5343-9862-71675e4af489",
    "20": "7c3d795c-db79-54e3-8e96-11c0ec9ae1db",
    "21": "4ddc74e1-2f66-571c-9db6-2db587e01d2e",
    "22": "384f4963-f8f6-5e40-b068-c42d79eb8c42",
    "23": "17a788f3-2d66-57db-a230-04c08bbb7386",
    "24": "bc1b96e2-e560-55df-abbe-0620cc1928f2",
    "25": "ad1f29a1-d9cd-5030-8c72-3d64ccbed810",
    "26": "785afe69-2769-5984-be0b-d0e7e87f019a",
    "27": "98698747-a6d9-5c2b-bb98-d7518517c969",
    "28": "4eb4dfe1-601a-532e-9ceb-bfcd75244307",
    "29": "caeb551d-bab4-5e00-a6a7-2c2a78a4caec",
    "30": "ba873d97-6d1c-5cdc-98a9-cb91c537a3ea",
    "31": "086cecab-892a-5c39-81f4-d623b331861b",
    "32": "9e9ec906-4db1-5b2b-b6e7-f033e0277919",
    "33": "027f6c56-31ef-5180-984f-9483591b83aa",
    "34": "52961ea0-739e-52ae-84c7-5839d0d498a8",
    "35": "3605d775-5e9d-5644-9410-d8364a49639e",
    "36": "849faaa1-05d2-5029-9755-2446bb169cc1",
    "37": "1ee499d0-ef54-588e-9ce2-45162cd8b88e",
    "38": "f3306e2e-28b5-539b-ae6b-841105f680fa",
    "39": "d67ea065-4386-52ec-8739-1f929ab1b646",
    "40": "54ddba69-2d46-5489-b6df-c7835ed1f18e",
    "41": "13ca0641-7259-5a62-b588-0f5a540fcebf",
    "42": "16846eea-1a26-5b35-9a89-c7d87ad821ee",
    "43": "30f074a6-90c3-5280-aae1-9c23e03db0e8",
    "44": "2772f9f6-aa8d-5475-80af-b19b94e50404",
    "45": "515c07fe-7061-5514-92d5-944c88f35521",
    "46": "df77e281-5611-58c8-8f2c-d2ba9439dbb4",
    "47": "7883ea75-e319-5208-a3d8-7d5133717215",
    "48": "ea126f5f-c5b4-5430-b3ea-163267ed8319",
    "49": "f797bad6-068d-5619-b6ca-4acdf9a29585",
    "50": "b40bf2fd-0e5d-5c15-acb7-4fc9d2bbda6a",
    "51": "ff396f86-9159-5727-9256-38eb0898a5e0",
    "52": "c2279dfa-d4cc-5530-8374-795827adb943",
    "53": "9806a076-fcd9-5932-b3cf-b11d71059a1f",
    "54": "cbc1faf6-cd8c-5cfe-add1-b7ef2159457a",
    "55": "8fa79bb3-3372-5f7b-bf32-5f0425de2d95",
    "56": "5ed2cd79-f990-53bb-8ab6-4850df3997cb",
    "57": "64c49d7d-5b8a-592d-9f62-10ceaf7c70e1",
    "58": "f2e4e10d-dc90-5931-a3df-f12951711e90",
    "59": "fd52a81b-84a2-5907-adf9-36b7b33a71ee",
    "60": "7dfab13e-7d08-5337-89d0-0e8f652d03af",
    "61": "14d2bb6a-5052-56db-bc8a-70ed38eb0b15",
    "62": "df2d9218-51a3-5fa0-ad02-888cb72305c3",
    "63": "3a60e012-111f-5195-9677-80daf96df0c6",
    "64": "e0ea7b7c-8d66-55e9-9089-627a59895b7f",
    "65": "8c645a6c-f08f-514f-98c1-91cfabe8f60a",
    "66": "21ba94a1-4715-5dda-8d54-6d8980afd894",
    "67": "5fa46faf-817d-50e4-80fb-221f8620e1a6",
    "68": "fee5c633-b023-50f1-8a58-1dbb523fe5ff",
    "69": "04d771b8-2b62-5d74-a737-bcc754a61d7e",
    "70": "712740b3-ec74-5e5f-898b-b6e2f01ef22b",
    "71": "57dc142d-fdce-5583-8aad-638f0535cb06",
    "72": "80d72382-1854-5810-a3e6-00f3cebe9774",
    "75": "c4bb7c16-875f-5e23-bf77-c052bd37bc37",
    "76": "891ee65e-8880-5998-8fe3-85c1e30cd051",
    "77": "b9c0484b-7578-5864-9f4c-3d9393391e9c",
    "78": "5def4913-914d-56fc-a17c-dd419a01ce43",
    "79": "b097afdb-138f-5f2f-93bf-114b13f8ecb4",
    "80": "5d5f6c9e-f09b-5d60-84f9-b305cd4ad3ce",
    "81": "d65d0796-0f7c-5535-ab90-80195788d6d5",
    "82": "dad0e7a7-2b24-514a-a82b-d08f73901b09",
    "83": "1dc18c92-8f86-5e94-a10f-f1343c4a7ffd",
    "84": "3282e482-07f2-50b4-bd21-334d345a5401",
    "85": "ba49bed0-2142-5092-9e69-fee7a5267b95",
    "86": "3524548c-9a47-5c3b-9379-787b8ce1bdf8",
    "87": "8fc15642-c839-5e34-bdb6-abd81fb5193a",
    "88": "5fb77a09-4ae4-5d26-bf15-2a46f71ba4e0",
    "89": "3624fc03-3575-5a99-abf7-346930e24a95",
}
CANDIDATES = (
    ("BONAPARTE", "Louis-Napoléon Bonaparte", "voixBONAPARTE"),
    ("CAVAIGNAC", "Louis-Eugène Cavaignac", "voixCAVAIGNAC"),
    ("LEDRU", "Alexandre Ledru-Rollin", "voixLEDRUROLLIN"),
    ("RASPAIL", "François-Vincent Raspail", "voixRASPAIL"),
    ("LAMARTINE", "Alphonse de Lamartine", "voixLAMARTINE"),
    ("CHANGARNIER", "Nicolas Changarnier", "voixCHANGARNIER"),
)
FIGURES = ((2, "inscrits"), (3, "votants"), (4, "exprimes"))
SYSTEM_NOTE = (
    "The Constitution of 4 November 1848 had the President elected by direct, universal male suffrage for four "
    "years, in one round, for the whole country. To win, a candidate needed an absolute majority of the votes cast "
    "and at least two million votes; otherwise the National Assembly chose among the five leading candidates."
)
METHOD = (
    "Votes by commune, digitized from the official reports (procès-verbaux) of the 1848 vote kept at the Archives "
    "nationales (B/II/958 to B/II/1046), summed here by département of 1848. The Seine (Paris and its suburbs) and "
    "the Seine-et-Oise are the sums of the communes that today make up several départements, and Corsica is one "
    "département. The digitization covers 33,040 communes. It lacks the communes of Alsace and Lorraine that left "
    "France in 1871 and some others, so its totals fall short of the national count, and in places the printed "
    "voters are fewer than the valid votes. Registered voters, voters, and valid votes are kept as printed. The "
    "official count in Le Moniteur universel by département is not yet loaded."
)


def fetch() -> None:
    raw.fetch(SOURCE, ZIP_URL, suffix=".zip")


def communes() -> pl.DataFrame:
    with zipfile.ZipFile(raw.cached(SOURCE, ZIP_URL).path) as archive:
        content = archive.read(MEMBER)
    frame = pl.read_csv(io.BytesIO(content), infer_schema=False)
    numbers = [column for _, column in FIGURES] + [column for _, _, column in CANDIDATES]
    frame = frame.with_columns(pl.col(numbers).cast(pl.Int64))
    return frame.with_columns(unit=pl.col("dep").replace_strict(DEPARTEMENT_1848, default=pl.col("dep")))


def parse() -> None:
    election().write()


def emit() -> None:
    package.run("load", ["fr.presidentielle/president-1848"])


def election() -> Package:
    p = Package(
        "fr.presidentielle/president-1848",
        SOURCE,
        "The presidential election of 10 December 1848 by département, from the commune-level digitization of the "
        "official reports",
    )
    external = {FRANCE, PRESIDENCY, NATION_AREA, *UNITS.values()}
    p.external |= external
    source = p.id("source", SOURCE)
    p.add(
        "source",
        id=source,
        kind="academic",
        publisher="Julia Cagé and Thomas Piketty",
        title="Une histoire du conflit politique: élections et inégalités sociales en France, 1789-2022 (data)",
        url="https://unehistoireduconflitpolitique.fr",
        license="Free use with the source cited",
        attribution="J. Cagé and T. Piketty (2023), Une histoire du conflit politique, Seuil",
        notes="Results digitized from the procès-verbaux at the Archives nationales, B/II/958 to B/II/1046.",
    )
    p.artifact(raw.cached(SOURCE, ZIP_URL), source)
    p.accept(
        "accounting.ballots_sum",
        "In Indre, Lot-et-Garonne, Aude, and Meurthe (Meurthe-et-Moselle) the digitized reports print more valid "
        "votes than voters, in a few communes each, and the sums keep the figures as printed.",
    )
    frame = communes()
    system = p.id("electoral_system", "president-1848")
    p.add(
        "electoral_system",
        id=system,
        polity_id=FRANCE,
        valid_from=date(1848, 11, 4),
        valid_to=date(1852, 12, 2),
        rules={"formula": "contingent", "majority": 0.5, "of": "votes", "chooser": "legislature", "among": 5},
        rules_version=1,
    )
    p.add("note", id=p.id("note", system), entity_id=system, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    election = p.id("election", DAY)
    p.add("election", id=election, polity_id=FRANCE, kind="general", date_from=DAY, date_to=DAY, status="held")
    p.add("slug", path="fr/1848-12-10-presidential", entity_id=election, is_current=True)
    p.name(election, "1848 French presidential election", "en", "official")
    p.name(election, "Élection du président de la République de 1848", "fr", "native", False)
    contest = p.id("contest", DAY)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        office_id=PRESIDENCY,
        district_area_id=NATION_AREA,
        magnitude=1,
        round_number=1,
        electoral_system_id=system,
        default_result_kind="counted",
    )
    p.name(contest, "1848 French presidential election", "en", "official")
    p.add("slug", path="fr/1848-12-10-presidential/president", entity_id=contest, is_current=True)
    result_set = p.id("result_set", DAY)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=source,
        status="active",
        is_default=True,
    )
    p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    units = frame.group_by("unit").agg(
        pl.col([column for _, column in FIGURES]).sum(), pl.col([c for _, _, c in CANDIDATES]).sum()
    )
    winner = max(CANDIDATES, key=lambda item: units[item[2]].sum())[0]
    options = {}
    for key, name, column in CANDIDATES:
        person = p.id("person", key)
        p.add("person", id=person, gender="male")
        p.name(person, name, "fr", "official")
        option = options[column] = p.id("option", DAY, key)
        p.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=name)
        p.add(
            "candidacy",
            id=p.id("candidacy", DAY, key),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=name,
        )
        p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if key == winner else "lost")
    rows = []
    for row in units.sort("unit").iter_rows(named=True):
        unit = UNITS[row["unit"]]
        for measure, column in FIGURES:
            rows.append((result_set, unit, None, measure, row[column]))
        for column, option in options.items():
            rows.append((result_set, unit, option, 1, row[column]))
    p.frame(
        "result_value",
        pl.DataFrame(
            rows,
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
    return p
