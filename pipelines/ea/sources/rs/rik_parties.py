"""The parties and coalitions behind the lists in the Republic Electoral Commission's results.

The Commission's tables print each list by its ballot name only, with no party code. Each list is given a party row
here. A list that a single party submitted under its own name in several elections, such as the Serbian Radical Party,
shares one row across those elections; a coalition that ran under the same name or with the same members in several
elections shares one row too. Every other list, such as a coalition led by one party under a new slogan, is its own
row. Which lists are the same party is curated by hand from the printed names. Linking a row to the same party in
other sources is left to the archive's matching pipelines.

The tables of 2003 and 2007 print only abbreviations, so those lists are named from the Commission's reports of the
results, which print the full list names. The text layer of the 2007 report misreads a few letters (СГРАНКА, КОАПИЦИЈА,
СОЦИЈАПДЕМОКРАТА, АПБАНАЦА, РЕФОРМИСГИЧКА), and the 2003 report misprints ОБНОВЕ as ОБНОБЕ; those are corrected below.

Colors are the archive's own editorial choice, taken from each party's logo; a coalition takes the color of the
party that leads it. Lists with no clear brand color have none.
"""

import re

from ea.package import Package

PARTY, BLOC, GROUP = "party", "bloc", "independent_group"
SNS, SPS, DS, DSS, SRS, SVM, SDA = "#0c4da2", "#e1241f", "#f2c200", "#2a52a0", "#1d3a7a", "#3e8e41", "#00843d"

# key: (kind, name, short name, color)
PARTIES: dict[str, tuple[str, str, str | None, str | None]] = {
    "srs": (PARTY, "Српска радикална странка", "СРС", SRS),
    "dss": (PARTY, "Демократска странка Србије", "ДСС", DSS),
    "ds": (PARTY, "Демократска странка", "ДС", DS),
    "g17": (PARTY, "Г17 плус", "Г17+", None),
    "sps": (PARTY, "Социјалистичка партија Србије", "СПС", SPS),
    "spo": (PARTY, "Српски покрет обнове", "СПО", None),
    "ldp": (PARTY, "Либерално демократска партија", "ЛДП", None),
    "svm": (PARTY, "Савез војвођанских Мађара", "СВМ", SVM),
    "sda": (PARTY, "Странка демократске акције Санџака", "СДА Санџака", SDA),
    "pdd": (PARTY, "Партија за демократско деловање", "ПДД", None),
    "ruska": (PARTY, "Руска странка", None, None),
    "dveri": (PARTY, "Српски покрет Двери", "Двери", None),
    "zavetnici": (PARTY, "Српска странка Заветници", "Заветници", None),
    "djb": (PARTY, "Доста је било", "ДЈБ", None),
    "urs": (PARTY, "Уједињени региони Србије", "УРС", None),
    "cp": (PARTY, "Црногорска партија", None, None),
    "reformisti": (PARTY, "Реформистичка странка", None, None),
    "roma-union": (PARTY, "Унија Рома Србије", None, None),
    "romska": (PARTY, "Ромска партија", None, None),
    "pss": (PARTY, "Покрет снага Србије", "ПСС", None),
    "spp": (PARTY, "Странка правде и помирења", "СПП", None),
    "narodna": (PARTY, "Народна странка", None, None),
    "mi-gin": (GROUP, "МИ – Глас из народа", "МИ–ГИН", None),
    "dss-ns": (BLOC, "Демократска странка Србије – Нова Србија", "ДСС–НС", DSS),
    "sps-pups-js": (
        BLOC,
        "Социјалистичка партија Србије – Партија уједињених пензионера Србије – Јединствена Србија",
        "СПС–ПУПС–ЈС",
        SPS,
    ),
    "sps-js": (BLOC, "Социјалистичка партија Србије – Јединствена Србија", "СПС–ЈС", SPS),
    "kapd": (BLOC, "Коалиција Албанаца Прешевске долине", "КАПД", None),
    "ada-ud": (BLOC, "Албанска демократска алтернатива – Уједињена долина", "АДА–УД", None),
    "vojvodjanske": (BLOC, "Коалиција „Војвођанске партије“", "Војвођанске партије", None),
    "nada": (BLOC, "НАДА за Србију", "НАДА", None),
    "spn": (BLOC, "Србија против насиља", "СПН", None),
    "ujedinjeni": (BLOC, "Уједињени за правду", None, None),
    "mora": (BLOC, "Мора другачије", None, None),
    "okupljanje": (BLOC, "Национално окупљање", None, None),
    "dobro-jutro": (BLOC, "Добро јутро Србијо", None, None),
}

# Each election's lists in ballot order: a key of PARTIES for a party or coalition that ran more than once, or the
# kind, short name, and color of a list that is its own row.
One = tuple[str, str | None, str | None]
LISTS: dict[str, list[str | One]] = {
    "2003-12-28": [
        "g17",
        "srs",
        "dss",
        (PARTY, "ДА", None),
        "ds",
        (BLOC, "СПО–НС", None),
        (PARTY, "Отпор", None),
        (BLOC, "За народно јединство", None),
        "sps",
        (BLOC, "Самостална Србија", None),
        (BLOC, "Одбрана и правда", None),
        (BLOC, "Заједно за толеранцију", None),
        (PARTY, "Либерали Србије", None),
        (PARTY, "Реформисти", None),
        (BLOC, "СНС – Народни блок", None),
        (PARTY, "Привредна снага Србије и дијаспора", None),
        (PARTY, "Лабуристичка партија Србије", None),
        (PARTY, "ССВ", None),
        (PARTY, "ЈУЛ", None),
    ],
    "2007-01-21": [
        "ds",
        "g17",
        (BLOC, "ЛДП–ГСС–СДУ–ЛСВ", None),
        "srs",
        "dss-ns",
        "pss",
        "spo",
        "svm",
        (BLOC, "ПУПС–СДП", None),
        (BLOC, "Листа за Санџак", SDA),
        "sps",
        (GROUP, "Зато што мора боље", None),
        "vojvodjanske",
        "roma-union",
        "reformisti",
        (PARTY, "ДЗС", None),
        "kapd",
        (PARTY, "Социјалдемократија", None),
        (BLOC, "Мађарска слога", None),
        "romska",
    ],
    "2008-05-11": [
        (BLOC, "За европску Србију", DS),
        "ldp",
        "dss-ns",
        "srs",
        "sps-pups-js",
        (BLOC, "Бошњачка листа за европски Санџак", SDA),
        (BLOC, "Мађарска коалиција", None),
        "reformisti",
        (PARTY, "Народна сељачка странка", None),
        "pss",
        (PARTY, "ГИГ", None),
        (PARTY, "Уједињени Власи Србије", None),
        "vojvodjanske",
        (PARTY, "Роми за Рома", None),
        "cp",
        "roma-union",
        "kapd",
        (PARTY, "Савез бачких Буњеваца", None),
        (PARTY, "Покрет Моја Србија", None),
        (PARTY, "Народни покрет за Србију", None),
        (PARTY, "Патриотска снага дијаспоре", None),
        "romska",
    ],
    "2012-05-06": [
        (BLOC, "Избор за бољи живот", DS),
        "srs",
        "urs",
        (BLOC, "Преокрет", None),
        (BLOC, "Покренимо Србију", SNS),
        "dss",
        "sps-pups-js",
        "dveri",
        "svm",
        "reformisti",
        "sda",
        (PARTY, "Покрет радника и сељака", None),
        (PARTY, "Социјалдемократски савез", None),
        (BLOC, "Све заједно", None),
        "kapd",
        "cp",
        (PARTY, "Комунистичка партија", None),
        (GROUP, "Ниједан од понуђених одговора", None),
    ],
    "2014-03-16": [
        (BLOC, None, SNS),
        "sps-pups-js",
        "dss",
        (BLOC, "ЛДП–БДЗС–СДУ", None),
        "svm",
        "srs",
        "urs",
        (BLOC, "Са Демократском странком за демократску Србију", DS),
        "dveri",
        "sda",
        (BLOC, "Борис Тадић – НДС, ЛСВ, ЗЗС, ВМДК, ЗЗВ, ДЛР", None),
        (PARTY, "Трећа Србија", None),
        "cp",
        (BLOC, "Листа националних заједница", None),
        "djb",
        (BLOC, "Коалиција грађана свих народа и народности", None),
        (GROUP, "Патриотски фронт", None),
        "ruska",
        "pdd",
    ],
    "2016-04-24": [
        (BLOC, "Србија побеђује", SNS),
        (BLOC, "За праведну Србију", DS),
        "sps-js",
        "srs",
        (BLOC, "Двери–ДСС", None),
        "svm",
        (BLOC, "Савез за бољу Србију", None),
        (PARTY, "Бошњачка демократска заједница Санџака", None),
        "sda",
        "zavetnici",
        (GROUP, "За препород Србије", None),
        "ruska",
        (PARTY, "Републиканска странка", None),
        (PARTY, "Српско руски покрет", None),
        (BLOC, "Србија за све нас", None),
        (PARTY, "Дијалог – Млади са ставом", None),
        "djb",
        "pdd",
        (PARTY, "Зелена странка", None),
        (BLOC, "Народни савез", None),
    ],
    "2020-06-21": [
        (BLOC, "За нашу децу", SNS),
        "sps-js",
        "srs",
        "svm",
        (PARTY, "Победа за Србију", None),
        (BLOC, "За Краљевину Србију", None),
        (BLOC, "Уједињена демократска Србија", None),
        (BLOC, "Само право", None),
        (BLOC, "Метла 2020", None),
        (BLOC, "Здраво да победи", None),
        "sda",
        "zavetnici",
        (BLOC, "Народни блок", None),
        (PARTY, "Покрет слободних грађана", None),
        (GROUP, "Суверенисти", None),
        "ada-ud",
        (GROUP, "1 од 5 милиона", None),
        (BLOC, "Нек маске падну", None),
        "ruska",
        (BLOC, "Коалиција за мир", None),
        (PARTY, "Покрет Левијатан", None),
    ],
    "2023-12-17": [
        (BLOC, "Србија не сме да стане", SNS),
        "sps-js",
        "srs",
        "okupljanje",
        "nada",
        "svm",
        "spn",
        "ujedinjeni",
        "sda",
        (BLOC, "Коалиција за мир и толеранцију", None),
        "narodna",
        "dobro-jutro",
        (BLOC, "Политичка борба Албанаца се наставља", None),
        "mi-gin",
        (BLOC, "Србија на западу", None),
        "ruska",
        "mora",
        "ada-ud",
    ],
    "2023-12-17-vojvodina": [
        (BLOC, "Војводина не сме да стане", SNS),
        "sps-js",
        "srs",
        "ruska",
        "nada",
        "svm",
        "spn",
        "ujedinjeni",
        "mora",
        "narodna",
        "okupljanje",
        "dobro-jutro",
        (BLOC, "Војвођани", None),
    ],
    "2024-06-02-belgrade": [
        (BLOC, "Београд сутра", SNS),
        "ruska",
        (GROUP, "Ћале ово је тебе", None),
        (GROUP, "За зелени Београд", None),
        (BLOC, "Београдски фронт", None),
        (BLOC, "Бирамо Београд", None),
        (BLOC, "МИ – Снага народа", None),
        (BLOC, "Народна листа", None),
        (BLOC, "Београд наш град", None),
        "roma-union",
        "spp",
        (BLOC, "Крени-Промени", None),
        "djb",
        "mi-gin",
    ],
    "2026-10-25": [
        (BLOC, "Уједињена Србија", SNS),
        (BLOC, "Фактор стабилности", SPS),
        (GROUP, "Студенти побеђују", None),
        (BLOC, "Људски", None),
        "svm",
        "spp",
        "ruska",
        (BLOC, "Аутентична десница", None),
        (BLOC, "Европска Србија", None),
        "sda",
        "pss",
        (PARTY, "Либерално зелена странка", None),
        (BLOC, "Бирам правду – бирам будућност", None),
        (PARTY, "Странка руско-српског јединства", None),
        (GROUP, "Ћале ово је за тебе", None),
        (BLOC, "Алтернатива за Србију", None),
        (BLOC, "Албанска коалиција", None),
        (PARTY, "Мајка Србија – Груда", None),
    ],
}

# The full list names of 2003 and 2007, from the Commission's reports of the results, by ballot number
REPORTED = {
    "2003-12-28": {
        1: "Г17 ПЛУС - МИРОЉУБ ЛАБУС",
        2: "СРПСКА РАДИКАЛНА СТРАНКА - др ВОЈИСЛАВ ШЕШЕЉ",
        3: "ДЕМОКРАТСКА СТРАНКА СРБИЈЕ - ВОЈИСЛАВ КОШТУНИЦА",
        4: "ДЕМОКРАТСКА АЛТЕРНАТИВА-НЕБОЈША ЧОВИЋ",
        5: "ДЕМОКРАТСКА СТРАНКА - БОРИС ТАДИЋ",
        6: "СРПСКИ ПОКРЕТ ОБНОВЕ - НОВА СРБИЈА - ВУК ДРАШКОВИЋ - ВЕЛИМИР ИЛИЋ",
        7: "ОТПОР",
        8: (
            "„ЗА НАРОДНО ЈЕДИНСТВО“ проф. БОРИСЛАВ ПЕЛЕВИЋ И МАРИЈАН РИСТИЧЕВИЋ (Странка српског јединства, Народна "
            "сељачка странка, Народна странка, Наш дом Србија и Српска странка)"
        ),
        9: "СОЦИЈАЛИСТИЧКА ПАРТИЈА СРБИЈЕ - СЛОБОДАН МИЛОШЕВИЋ",
        10: (
            "САМОСТАЛНА СРБИЈА - др ВЛАДАН БАТИЋ (Демохришћанска странка Србије, Демократска странка „Отаџбина“, "
            "Демократски покрет Румуна Србије, Сељачка странка, Српска правда)"
        ),
        11: (
            "ОДБРАНА И ПРАВДА - ВУК ОБРАДОВИЋ И БОРИВОЈЕ БОРОВИЋ (Социјалдемократија, Народна странка Правда, Странка "
            "радника и пензионера - СРП и Социјалдемократска партија зелених)"
        ),
        12: "ЗАЈЕДНО ЗА ТОЛЕРАНЦИЈУ - ЧАНАК, КАСА, ЉАЈИЋ",
        13: "ЛИБЕРАЛИ СРБИЈЕ - ДУШАН МИХАЈЛОВИЋ",
        14: "РЕФОРМИСТИ - СОЦИЈАЛДЕМОКРАТСКЕ ПАРТИЈЕ ВОЈВОДИНЕ - СРБИЈЕ, МИОДРАГ - МИЛЕ ИСАКОВ",
        15: "СОЦИЈАЛИСТИЧКА НАРОДНА СТРАНКА - НАРОДНИ БЛОК - ГЕНЕРАЛ НЕБОЈША ПАВКОВИЋ",
        16: "ПРИВРЕДНА СНАГА СРБИЈЕ И ДИЈАСПОРА - БРАНКО ДРАГАШ",
        17: "ЛАБУРИСТИЧКА ПАРТИЈА СРБИЈЕ - ДРАГАН МИЛОВАНОВИЋ",
        18: "САВЕЗ СРБА ВОЈВОДИНЕ - ДУШАН САЛАТИЋ",
        19: "ЈУГОСЛОВЕНСКА ЛЕВИЦА - ЈУЛ",
    },
    "2007-01-21": {
        1: "ДЕМОКРАТСКА СТРАНКА - БОРИС ТАДИЋ",
        2: "Г17 ПЛУС - МЛАЂАН ДИНКИЋ",
        3: (
            "ЛИБЕРАЛНО ДЕМОКРАТСКА ПАРТИЈА - ГРАЂАНСКИ САВЕЗ СРБИЈЕ - СОЦИЈАЛДЕМОКРАТСКА УНИЈА - ЛИГА "
            "СОЦИЈАЛДЕМОКРАТА ВОЈВОДИНЕ - ЧЕДОМИР ЈОВАНОВИЋ"
        ),
        4: "СРПСКА РАДИКАЛНА СТРАНКА - ДР ВОЈИСЛАВ ШЕШЕЉ",
        5: "ДЕМОКРАТСКА СТРАНКА СРБИЈЕ - НОВА СРБИЈА - ДР ВОЈИСЛАВ КОШТУНИЦА",
        6: "ПОКРЕТ СНАГА СРБИЈЕ - БОГОЉУБ КАРИЋ",
        7: "СРПСКИ ПОКРЕТ ОБНОВЕ - ВУК ДРАШКОВИЋ",
        8: "САВЕЗ ВОЈВОЂАНСКИХ МАЂАРА - ЈОЖЕФ КАСА",
        9: (
            "ПАРТИЈА УЈЕДИЊЕНИХ ПЕНЗИОНЕРА СРБИЈЕ (ПУПС) - ДР ЈОВАН КРКОБАБИЋ И СОЦИЈАЛДЕМОКРАТСКА ПАРТИЈА (СДП) - "
            "ДР НЕБОЈША ЧОВИЋ"
        ),
        10: "КОАЛИЦИЈА ЛИСТА ЗА САНЏАК ДР СУЛЕЈМАН УГЉАНИН",
        11: "СОЦИЈАЛИСТИЧКА ПАРТИЈА СРБИЈЕ",
        12: "БРАНКО ПАВЛОВИЋ - „ЗАТО ШТО МОРА БОЉЕ“",
        13: "КОАЛИЦИЈА „ВОЈВОЂАНСКЕ ПАРТИЈЕ“ - МР ИГОР КУРЈАЧКИ",
        14: "УНИЈА РОМА СРБИЈЕ - ДР РАЈКО ЂУРИЋ",
        15: "РЕФОРМИСТИЧКА СТРАНКА - ДР АЛЕКСАНДАР ВИШЊИЋ",
        16: "ДЕМОКРАТСКА ЗАЈЕДНИЦА СРБИЈЕ - ДР ОБРЕН ЈОКСИМОВИЋ",
        17: "КОАЛИЦИЈА АЛБАНАЦА ПРЕШЕВСКЕ ДОЛИНЕ",
        18: "СОЦИЈАЛДЕМОКРАТИЈА - НЕНАД ВУКАСОВИЋ",
        19: "КОАЛИЦИЈА МАЂАРСКА СЛОГА - АНДРАШ АГОШТОН - ДР ПАП ШАНДОР",
        20: "РОМСКА ПАРТИЈА - ШАЈН СРЂАН",
    },
}

PREFIX = re.compile(r"^ИЗБОРН[AА] ЛИСТ[AА]( КАНДИДАТА ЗА ОДБОРНИКЕ СКУПШТИНЕ ГРАДА БЕОГРАДА)?\s*[-–]?\s*")


def list_name(slug: str, number: int, label: str) -> str:
    """The name of a list as the Commission prints it, without the words 'electoral list' it opens with."""
    reported = REPORTED.get(slug, {}).get(number)
    if reported is not None:
        return reported
    return " ".join(PREFIX.sub("", " ".join(label.split())).split())


def party_key(slug: str, number: int) -> str:
    entry = LISTS[slug][number - 1]
    return entry if isinstance(entry, str) else f"{slug}/{number}"


def add_parties(p: Package, labels: dict[str, dict[int, str]], polity: str, colors_source: str) -> None:
    """Add a party row for every list of the elections in `labels` (slug, then ballot number, then printed label)."""
    printed: dict[str, list[str]] = {}
    one_off: dict[str, One] = {}
    for slug, by_number in labels.items():
        if sorted(by_number) != list(range(1, len(LISTS[slug]) + 1)):
            raise ValueError(f"{slug}: the lists printed do not match the {len(LISTS[slug])} curated here")
        for number, label in sorted(by_number.items()):
            k = party_key(slug, number)
            names = printed.setdefault(k, [])
            name = list_name(slug, number, label)
            if name not in names:
                names.append(name)
            entry = LISTS[slug][number - 1]
            if not isinstance(entry, str):
                one_off[k] = entry
    unused = set(PARTIES) - set(printed)
    if unused:
        raise ValueError(f"curated parties no list uses: {sorted(unused)}")
    for k, names in printed.items():
        id = p.id("party", k)
        if k in PARTIES:
            kind, official, short, color = PARTIES[k]
        else:
            kind, short, color = one_off[k]
            official = names[-1]
        p.add("party", id=id, kind=kind, polity_id=polity)
        p.name(id, official, "sr", "official")
        for name in names:
            if name != official:
                p.name(id, name, "sr", "ballot", False)
        if short and short != official:
            p.name(id, short, "sr", "short", False)
        if color:
            p.add("party_color", party_id=id, color=color, valid_from=None, valid_to=None, source_id=colors_source)
