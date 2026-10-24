import difflib
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import quote

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.gb.umit import fold
from ea.store import DATA, connect

SOURCE = "gb.ireland_1885"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SERVICES = "https://services-eu1.arcgis.com/FH5XCsx8rYXqnjF5/arcgis/rest/services"
LAYERS = {
    "barony": ("National_Statutory_Boundaries_-_Baronies_Ungeneralised_-_2026", 0, 1027),
    "parish": ("National_Statutory_Boundaries_-_Civil_Parishes_Ungeneralised_2026", 6, 3825),
    "county": ("National_Statutory_Boundaries_-_Counties_Ungeneralised_-_2026", 1, 9261),
}
TOWNLANDS = f"{SERVICES}/Townlands___OSi_National_Statutory_Boundaries___Gen20m/FeatureServer/0/query"
TOWNLAND_COUNT = 50031
STATUTE = "https://archive.org/details/india.history.resource.106075"
ELECTION = date(1885, 11, 24)
REDISTRIBUTION = date(1918, 12, 14)
TOLERANCE = 50
DUBLIN_CENTER = (-6.2592, 53.3472)
DUBLIN = {
    "NW": "dublin-college-green",
    "NE": "dublin-harbour",
    "SE": "dublin-st-stephen-s-green",
    "SW": "dublin-st-patrick-s",
}
CITIES = {
    "CORK": "cork-city",
    "GALWAY": "galway-city",
    "KILKENNY": "kilkenny-city",
    "LIMERICK": "limerick-city",
    "WATERFORD": "waterford-city",
}
TRANSFERS = {("KILKENNY", "KILCULLIHEEN"): "waterford-city"}
RULES: dict[str, dict[str, list[tuple[str, ...]]]] = {
    "CARLOW": {"carlow": [("B", "*")]},
    "CAVAN": {
        "west-cavan": [
            ("B", "CLANMAHON"),
            ("B", "TULLYHAW"),
            ("B", "LOUGHTEE LOWER"),
            ("B", "TULLYHUNCO"),
            ("P", "LOUGHTEE UPPER", "ANNAGELLIFF"),
            ("P", "LOUGHTEE UPPER", "CASTLETERRA"),
            ("P", "LOUGHTEE UPPER", "KILMORE"),
            ("P", "LOUGHTEE UPPER", "URNEY"),
            ("P", "TULLYGARVEY", "ANNAGH"),
            ("T", "", "DENN", "CRUMLIN"),
        ],
        "east-cavan": [("B", "CASTLERAHAN"), ("B", "TULLYGARVEY"), ("B", "CLANKEE"), ("B", "LOUGHTEE UPPER")],
    },
    "CLARE": {
        "east-clare": [
            ("B", "BURREN"),
            ("B", "BUNRATTY LOWER"),
            ("B", "TULLA UPPER"),
            ("B", "BUNRATTY UPPER"),
            ("B", "TULLA LOWER"),
            ("P", "INCHIQUIN", "DYSERT"),
            ("P", "INCHIQUIN", "KILKEEDY"),
            ("P", "INCHIQUIN", "KILLINABOY"),
            ("P", "INCHIQUIN", "KILNAMONA"),
            (
                "P",
                "INCHIQUIN",
                "RATH",
                "CARROWVERE",
                "DRINAGH",
                "LOUGHNAGOWAN",
                "MARTRY",
                "MOANREEL NORTH",
                "MOANREEL SOUTH",
            ),
            ("P", "INCHIQUIN", "RUAN"),
            ("P", "ISLANDS", "CLAREABBEY"),
            ("P", "ISLANDS", "CLONDAGAD", "DEHOMAD", "FURROOR", "LISCASEY"),
            ("P", "ISLANDS", "DRUMCLIFF"),
            ("P", "ISLANDS", "KILLONE"),
        ],
        "west-clare": [
            ("B", "CLONDERALAW"),
            ("B", "IBRICKAN"),
            ("B", "CORCOMROE"),
            ("B", "MOYARTA"),
            ("B", "INCHIQUIN"),
            ("B", "ISLANDS"),
        ],
    },
    "CORK": {
        "north-cork": [
            ("B", "DUHALLOW"),
            ("B", "ORRERY AND KILMORE"),
            ("P", "FERMOY", "ARDSKEAGH"),
            ("P", "FERMOY", "BALLYHAY"),
            ("P", "FERMOY", "DONERAILE"),
            ("P", "FERMOY", "IMPHRICK"),
            ("T", "FERMOY", "KILQUANE", "BALLYLOPEN"),
        ],
        "north-east-cork": [
            ("B", "CONDONS AND CLANGIBBON"),
            ("B", "KINATALLOON"),
            ("B", "FERMOY"),
            *(
                ("P", "BARRYMORE", parish)
                for parish in (
                    "ARDNAGEEHY",
                    "BRITWAY",
                    "CASTLELYONS",
                    "COOLE",
                    "DUNBULLOGE",
                    "GORTROE",
                    "KILSHANAHAN",
                    "KNOCKMOURNE",
                    "RATHCORMACK",
                    "TEMPLEBODAN",
                    "WHITECHURCH",
                )
            ),
            ("P", "BARRETTS", "MOURNEABBEY"),
        ],
        "mid-cork": [("B", "BARRETTS"), ("B", "MUSKERRY EAST"), ("B", "MUSKERRY WEST")],
        "east-cork": [("B", "IMOKILLY"), ("B", "BARRYMORE")],
        "west-cork": [
            ("B", "BANTRY"),
            ("B", "BEAR"),
            ("B", "CARBERY WEST WEST DIVISION"),
            ("P", "CARBERY WEST EAST DIVISION", "AGHADOWN"),
            ("P", "CARBERY WEST EAST DIVISION", "CLEAR ISLAND"),
            ("P", "CARBERY WEST EAST DIVISION", "CREAGH", "GORTNACLOHY", "SMORANE"),
            ("P", "CARBERY WEST EAST DIVISION", "TULLAGH"),
        ],
        "south-cork": [
            ("B", "CARBERY EAST WEST DIVISION"),
            ("B", "IBANE AND BARRYROE"),
            *(
                ("P", "CARBERY EAST EAST DIVISION", parish)
                for parish in (
                    "BALLYMONEY",
                    "DESERT",
                    "DESERTSERGES",
                    "ISLAND",
                    "KILGARIFF",
                    "KILNAGROSS",
                    "TEMPLEBRYAN",
                )
            ),
            ("B", "CARBERY WEST EAST DIVISION"),
        ],
        "south-east-cork": [
            ("B", "COURCEYS"),
            ("B", "KINALEA"),
            ("B", "KINSALE"),
            ("B", "KERRYCURRIHY"),
            ("B", "KINALMEAKY"),
            ("B", "CARBERY EAST EAST DIVISION"),
            *(
                ("P", "IBANE AND BARRYROE", parish)
                for parish in (
                    "ABBEYMAHON",
                    "DESERT",
                    "DONAGHMORE",
                    "KILSILLAGH",
                    "LISLEE",
                    "TEMPLEOMALUS",
                    "TEMPLEQUINLAN",
                    "TIMOLEAGUE",
                )
            ),
            ("T", "IBANE AND BARRYROE", "ISLAND", "AHIDELAKE"),
        ],
    },
    "DONEGAL": {
        "north-donegal": [
            ("B", "INISHOWEN EAST"),
            ("B", "INISHOWEN WEST"),
            ("P", "KILMACRENAN", "CLONDAVADDOG"),
            ("P", "KILMACRENAN", "KILLYGARVAN"),
            ("P", "KILMACRENAN", "TULLYFERN"),
            ("T", "KILMACRENAN", "AUGHNISH", "DRUMHERRIVE", "GLENALLA", "RAY"),
        ],
        "west-donegal": [
            ("B", "BOYLAGH"),
            *(
                ("P", "KILMACRENAN", parish)
                for parish in (
                    "CLONDAHORKY",
                    "GARTAN",
                    "KILMACRENAN",
                    "MEVAGH",
                    "RAYMUNTERDONEY",
                    "TULLAGHOBEGLY",
                )
            ),
            (
                "T",
                "KILMACRENAN",
                "CONWAL",
                "BALLYBOLAUDER",
                "BOHEOLAN",
                "CABRA BROOK",
                "CABRA GLEBE",
                "CARNATREANTAGH",
                "CARRICK",
                "CARRICKYSCANLAN",
                "CLONCARNEY",
                "DROMORE",
                "DRUMCAVANY",
                "KEELOGES",
                "MAGHERNAGRAN",
                "POLLANS",
                "ROSHIN",
                "STACKARNAGH",
                "TEMPLEDOUGLAS",
                "TREANBEG",
                "TULLANASCREEN",
            ),
        ],
        "east-donegal": [
            ("B", "RAPHOE NORTH"),
            ("B", "RAPHOE SOUTH"),
            ("P", "INISHOWEN WEST", "BURT"),
            ("B", "KILMACRENAN"),
        ],
        "south-donegal": [("B", "BANAGH"), ("B", "TIRHUGH")],
    },
    "DUBLIN": {
        "north-county-dublin": [
            ("B", "BALROTHERY EAST"),
            ("B", "BALROTHERY WEST"),
            ("B", "CASTLEKNOCK"),
            ("B", "COOLOCK"),
            ("B", "NETHERCROSS"),
            ("B", "NEWCASTLE"),
            ("P", "RATHDOWN", "KILGOBBIN"),
            ("P", "RATHDOWN", "KILTIERNAN"),
            ("P", "RATHDOWN", "WHITECHURCH"),
            ("T", "RATHDOWN", "TANEY", "BALALLY", "BALLINTEER", "KINGSTOWN", "TIKNOCK"),
            (
                "T",
                "RATHDOWN",
                "RATHFARNHAM",
                "BALLYROAN",
                "BUTTERFIELD",
                "OLDORCHARD",
                "RATHFARNHAM",
                "WHITEHALL",
                "WILLBROOK",
            ),
            ("B", "UPPERCROSS"),
        ],
        "south-county-dublin": [
            ("B", "DUBLIN"),
            ("B", "RATHDOWN"),
            ("P", "UPPERCROSS", "DONNYBROOK"),
            ("P", "UPPERCROSS", "ST CATHERINES"),
            ("P", "UPPERCROSS", "ST PETERS"),
        ],
    },
    "GALWAY": {
        "galway-connemara": [("B", "BALLYNAHINCH"), ("B", "MOYCULLEN"), ("B", "ROSS")],
        "north-galway": [("B", "BALLYMOE"), ("B", "CLARE"), ("B", "DUNMORE")],
        "east-galway": [
            ("B", "CLONMACNOWEN"),
            ("B", "KILCONNELL"),
            ("B", "KILLIAN"),
            ("B", "LONGFORD"),
            ("B", "TIAQUIN"),
        ],
        "south-galway": [
            ("B", "ARAN"),
            ("B", "ATHENRY"),
            ("B", "DUNKELLIN"),
            ("B", "KILTARTAN"),
            ("B", "LEITRIM"),
            ("B", "LOUGHREA"),
        ],
    },
    "KERRY": {
        "north-kerry": [("B", "CLANMAURICE"), ("B", "IRAGHTICONNOR")],
        "west-kerry": [
            ("B", "CORKAGUINY"),
            *(
                ("P", "TRUGHANACMY", parish)
                for parish in (
                    "ANNAGH",
                    "ARDFERT",
                    "BALLYNAHAGLISH",
                    "BALLYSEEDY",
                    "CLOGHERBRIEN",
                    "FENIT",
                    "KILCOLMAN",
                    "KILGARRYLANDER",
                    "KILLORGLIN",
                    "KILTALLAGH",
                    "RATASS",
                    "TRALEE",
                )
            ),
        ],
        "south-kerry": [("B", "DUNKERRON NORTH"), ("B", "DUNKERRON SOUTH"), ("B", "GLANAROUGHT"), ("B", "IVERAGH")],
        "east-kerry": [("B", "MAGUNIHY"), ("B", "TRUGHANACMY")],
    },
    "KILDARE": {
        "north-kildare": [
            ("B", "CARBURY"),
            ("B", "CLANE"),
            ("B", "CONNELL"),
            ("B", "IKEATHY AND OUGHTERANY"),
            ("B", "NAAS NORTH"),
            ("B", "SALT NORTH"),
            ("B", "SALT SOUTH"),
            ("P", "NAAS SOUTH", "KILL"),
            ("P", "NAAS SOUTH", "KILLASHEE"),
            ("P", "NAAS SOUTH", "TIPPERKEVIN"),
        ],
        "south-kildare": [
            ("B", "OFFALY EAST"),
            ("B", "KILCULLEN"),
            ("B", "KILKEA AND MOONE"),
            ("B", "NARRAGH AND REBAN EAST"),
            ("B", "NARRAGH AND REBAN WEST"),
            ("B", "OFFALY WEST"),
            ("B", "NAAS SOUTH"),
        ],
    },
    "KILKENNY": {
        "north-kilkenny": [
            ("B", "CRANNAGH"),
            ("B", "FASSADININ"),
            ("B", "GALMOY"),
            ("B", "SHILLELOGHER"),
            ("B", "GOWRAN"),
        ],
        "south-kilkenny": [
            ("B", "CALLAN"),
            ("B", "IDA"),
            ("B", "IVERK"),
            ("B", "KELLS"),
            ("B", "KNOCKTOPHER"),
            *(
                ("P", "GOWRAN", parish)
                for parish in (
                    "INISTIOGE",
                    "PLEBERSTOWN",
                    "FAMMA",
                    "JERPOINTABBEY",
                    "JERPOINTWEST",
                    "THOMASTOWN",
                    "BALLYLINCH",
                    "KILFANE",
                    "COLUMBKILLE",
                    "GRAIGUENAMANAGH",
                    "ULLARD",
                )
            ),
        ],
    },
    "OFFALY": {
        "birr": [("B", "BALLYBOY"), ("B", "BALLYBRITT"), ("B", "CLONLISK"), ("B", "EGLISH"), ("B", "GARRYCASTLE")],
        "tullamore": [
            ("B", "BALLYCOWAN"),
            ("B", "COOLESTOWN"),
            ("B", "GEASHILL"),
            ("B", "KILCOURSEY"),
            ("B", "PHILIPSTOWN LOWER"),
            ("B", "PHILIPSTOWN UPPER"),
            ("B", "WARRENSTOWN"),
        ],
    },
    "LEITRIM": {
        "north-leitrim": [
            ("B", "DRUMAHAIRE"),
            ("B", "ROSCLOGHER"),
            ("P", "LEITRIM", "KILTUBBRID"),
            (
                "T",
                "LEITRIM",
                "KILTOGHERT",
                "ACRES",
                "AGHAGRANIA",
                "AGHNAGOLLOP",
                "ARDCOLUM",
                "BARNAMEENAGH",
                "BARNAMEENAGH WEST",
                "BLACKROCK",
                "CARRICKBAUN",
                "CARRICKNABRACK",
                "CORLOUGH",
                "CORLOUGHLIN",
                "CORMEELTAN",
                "CORMONGAN",
                "CORNAMUDDAGH",
                "CORNASHAMSOGE",
                "CORRACHUILL",
                "CORRYARD",
                "CREENAGH",
                "CREY",
                "DERRINTOBER",
                "DERRINTONNY",
                "DERRYHALLAGH",
                "DERRYNASEER",
                "DERRYTEIGEROE",
                "DORRUSAWILLIN",
                "DRISTERNAUN",
                "DRUMCOORA",
                "DRUMCROMAN",
                "DRUMDERG",
                "DRUMHALWY",
                "DRUMDUFF",
                "DRUMSHANBO",
                "GREAGHFARNAGH",
                "GREAGHNAGUILLAUN",
                "LARGAN",
                "LARGAN MOUNTAIN",
                "LAVAUR",
                "MAHANAGH",
                "MONEYNURE",
                "MURHAUN",
                "ROSCUNNISH",
                "SHANCURRY",
                "SHESKINACURRY",
            ),
        ],
        "south-leitrim": [("B", "CARRIGALLEN"), ("B", "MOHILL"), ("B", "LEITRIM")],
    },
    "LIMERICK": {
        "west-limerick": [
            ("B", "CONNELLO LOWER"),
            ("B", "CONNELLO UPPER"),
            ("B", "GLENQUIN"),
            ("B", "KENRY"),
            ("B", "SHANID"),
            ("P", "COSHMA", "ADARE"),
            ("P", "COSHMA", "DREHIDTARSNA"),
            ("P", "COSHMA", "KILLONAHAN"),
        ],
        "east-limerick": [
            ("B", "CLANWILLIAM"),
            ("B", "COONAGH"),
            ("B", "COSHLEA"),
            ("B", "KILMALLOCK"),
            ("B", "OWNEYBEG"),
            ("B", "PUBBLEBRIEN"),
            ("B", "SMALLCOUNTY"),
            ("B", "COSHMA"),
        ],
    },
    "LONGFORD": {
        "north-longford": [
            ("B", "GRANARD"),
            ("P", "LONGFORD", "KILLOE"),
            ("S", "LONGFORD", "TEMPLEMICHAEL", "KILLOE"),
            ("P", "ARDAGH", "MOSTRIM"),
            ("P", "ARDAGH", "STREET"),
            ("T", "ARDAGH", "CLONBRONEY", "RINVANNY", "CARTRONREAGH"),
            ("T", "ARDAGH", "GRANARD", "CASTLENUGENT"),
        ],
        "south-longford": [("B", "MOYDOW"), ("B", "RATHCLINE"), ("B", "SHRULE"), ("B", "ARDAGH"), ("B", "LONGFORD")],
    },
    "LOUTH": {
        "north-louth": [
            ("B", "LOUTH"),
            ("B", "DUNDALK LOWER"),
            ("B", "DUNDALK UPPER"),
            ("P", "ARDEE", "KILLANY"),
            ("P", "ARDEE", "LOUTH"),
        ],
        "south-louth": [("B", "ARDEE"), ("B", "DROGHEDA"), ("B", "FERRARD")],
    },
    "MAYO": {
        "north-mayo": [("B", "ERRIS"), ("B", "TIRAWLEY"), ("P", "GALLEN", "ATTYMASS"), ("P", "GALLEN", "KILGARVAN")],
        "west-mayo": [("B", "BURRISHOOLE"), ("B", "MURRISK"), ("B", "CARRA")],
        "east-mayo": [("B", "COSTELLO"), ("B", "GALLEN")],
        "south-mayo": [
            ("B", "CLANMORRIS"),
            ("B", "KILMAINE"),
            ("P", "COSTELLO", "AGHAMORE"),
            ("P", "COSTELLO", "ANNAGH"),
            ("P", "COSTELLO", "BEKAN"),
            ("P", "COSTELLO", "KNOCK"),
            ("P", "CARRA", "BALLYOVEY"),
        ],
    },
    "MEATH": {
        "north-meath": [
            ("B", "FORE"),
            ("B", "KELLS LOWER"),
            ("B", "KELLS UPPER"),
            ("B", "MORGALLION"),
            ("B", "SLANE LOWER"),
            ("B", "SLANE UPPER"),
            ("B", "NAVAN LOWER"),
            ("P", "SKREEN", "ATHLUMNEY"),
            ("P", "SKREEN", "ARDMULCHAN"),
        ],
        "south-meath": [
            *(
                ("B", barony)
                for barony in (
                    "DEECE LOWER",
                    "DEECE UPPER",
                    "DULEEK LOWER",
                    "DULEEK UPPER",
                    "DUNBOYNE",
                    "LUNE",
                    "MOYFENRATH LOWER",
                    "MOYFENRATH UPPER",
                    "NAVAN UPPER",
                    "RATOATH",
                    "SKREEN",
                )
            ),
            ("P", "NAVAN LOWER", "CHURCHTOWN"),
            ("P", "NAVAN LOWER", "RATAINE"),
        ],
    },
    "MONAGHAN": {
        "north-monaghan": [("B", "DARTREE"), ("B", "MONAGHAN"), ("B", "TROUGH")],
        "south-monaghan": [("B", "CREMORNE"), ("B", "FARNEY")],
    },
    "LAOIS": {
        "ossory": [
            ("B", "CLANDONAGH"),
            ("B", "CLARMALLAGH"),
            ("B", "MARYBOROUGH WEST"),
            ("B", "TINNAHINCH"),
            ("B", "UPPERWOODS"),
            ("P", "PORTNAHINCH", "ARDEA"),
        ],
        "leix": [
            ("B", "BALLYADAMS"),
            ("B", "CULLENAGH"),
            ("B", "MARYBOROUGH EAST"),
            ("B", "SLIEVEMARGY"),
            ("B", "STRADBALLY"),
            ("B", "PORTNAHINCH"),
        ],
    },
    "ROSCOMMON": {
        "north-roscommon": [("B", "BALLINTOBER NORTH"), ("B", "BOYLE"), ("B", "FRENCHPARK"), ("B", "ROSCOMMON")],
        "south-roscommon": [
            ("B", "ATHLONE NORTH"),
            ("B", "ATHLONE SOUTH"),
            ("B", "BALLINTOBER SOUTH"),
            ("B", "BALLYMOE"),
            ("B", "CASTLEREAGH"),
            ("B", "MOYCARN"),
            ("P", "ROSCOMMON", "CLOONFINLOUGH"),
            ("P", "ROSCOMMON", "KILBRIDE"),
            (
                "T",
                "ROSCOMMON",
                "LISONUFFY",
                "COGGALKEENAGH",
                "COGGALMORE",
                "COGGALSTACK",
                "COGGALTONROE",
                "COGGALFORTYACRES",
            ),
        ],
    },
    "SLIGO": {
        "north-sligo": [("B", "CARBURY"), ("B", "TIRERAGH"), ("P", "LEYNY", "BALLYSADARE"), ("P", "LEYNY", "KILLORAN")],
        "south-sligo": [("B", "COOLAVIN"), ("B", "CORRAN"), ("B", "TIRERRILL"), ("B", "LEYNY")],
    },
    "TIPPERARY": {
        "north-tipperary": [
            ("B", "ORMOND LOWER"),
            ("B", "ORMOND UPPER"),
            ("B", "OWNEY AND ARRA"),
            ("P", "KILNAMANAGH UPPER", "GLENKEEN"),
            ("P", "KILNAMANAGH UPPER", "UPPERCHURCH"),
        ],
        "mid-tipperary": [
            ("B", "ELIOGARTY"),
            ("B", "IKERRIN"),
            ("B", "KILNAMANAGH LOWER"),
            ("B", "KILNAMANAGH UPPER"),
            *(("P", "SLIEVARDAGH", parish) for parish in ("BALLINGARRY", "BUOLICK", "FENNOR", "KILCOOLY", "LICKFINN")),
        ],
        "south-tipperary": [("B", "CLANWILLIAM"), ("B", "IFFA AND OFFA WEST")],
        "east-tipperary": [("B", "IFFA AND OFFA EAST"), ("B", "MIDDLETHIRD"), ("B", "SLIEVARDAGH")],
    },
    "WATERFORD": {
        "west-waterford": [("B", "COSHMORE AND COSHBRIDE"), ("B", "DECIES WITHIN DRUM"), ("B", "DECIES WITHOUT DRUM")],
        "east-waterford": [
            ("B", "GAULTIERE"),
            ("B", "GLENAHIRY"),
            ("B", "MIDDLETHIRD"),
            ("B", "UPPERTHIRD"),
            ("P", "DECIES WITHOUT DRUM", "BALLYLANEEN"),
            ("P", "DECIES WITHOUT DRUM", "CLONEA", "BALLYRANDLE", "KILGROVAN"),
            ("P", "DECIES WITHOUT DRUM", "FEWS"),
            ("P", "DECIES WITHOUT DRUM", "KILBARRYMEADEN"),
            ("P", "DECIES WITHOUT DRUM", "KILROSSANTY"),
            ("P", "DECIES WITHOUT DRUM", "MONKSLAND"),
            ("P", "DECIES WITHOUT DRUM", "ROSSMIRE"),
            ("P", "DECIES WITHOUT DRUM", "STRADBALLY"),
            ("T", "DECIES WITHOUT DRUM", "NEWCASTLE", "LISSAHANE"),
        ],
    },
    "WESTMEATH": {
        "north-westmeath": [
            ("B", "CORKAREE"),
            ("B", "DELVIN"),
            ("B", "FARBILL"),
            ("B", "FORE"),
            ("B", "MOYGOISH"),
            ("B", "MOYASHEL AND MAGHERADERNON"),
            ("P", "FARTULLAGH", "LYNN"),
            ("P", "FARTULLAGH", "MOYLISKER"),
            ("P", "FARTULLAGH", "MULLINGAR"),
        ],
        "south-westmeath": [
            ("B", "BRAWNY"),
            ("B", "CLONLONAN"),
            ("B", "KILKENNY WEST"),
            ("B", "MOYCASHEL"),
            ("B", "RATHCONRATH"),
            ("B", "FARTULLAGH"),
            ("P", "MOYGOISH", "KILMACNEVAN"),
            ("P", "MOYASHEL AND MAGHERADERNON", "DYSART", "BALLYOTE", "SLANEBEG", "SLANEMORE"),
        ],
    },
    "WEXFORD": {
        "north-wexford": [
            ("B", "BALLAGHKEEN NORTH"),
            ("B", "BALLAGHKEEN SOUTH"),
            ("B", "GOREY"),
            ("B", "SCARAWALSH"),
            ("B", "SHELMALIERE EAST"),
            *(
                ("P", "BANTRY", parish)
                for parish in (
                    "CHAPEL",
                    "CLONLEIGH",
                    "CLONMORE",
                    "KILLANN",
                    "KILLEGNEY",
                    "ROSSDROIT",
                    "ST JOHNS",
                    "ST MULLINS",
                    "TEMPLELUDIGAN",
                    "TEMPLESCOBY",
                )
            ),
            ("P", "SHELMALIERE WEST", "CLONMORE"),
        ],
        "south-wexford": [
            ("B", "BARGY"),
            ("B", "FORTH"),
            ("B", "SHELBURNE"),
            ("B", "BANTRY"),
            ("B", "SHELMALIERE WEST"),
        ],
    },
    "WICKLOW": {
        "west-wicklow": [
            ("B", "BALLINACOR SOUTH"),
            ("B", "SHILLELAGH"),
            ("B", "TALBOTSTOWN LOWER"),
            ("B", "TALBOTSTOWN UPPER"),
            ("B", "BALLINACOR NORTH"),
        ],
        "east-wicklow": [
            ("B", "ARKLOW"),
            ("B", "NEWCASTLE"),
            ("B", "RATHDOWN"),
            ("P", "BALLINACOR NORTH", "CALARY"),
        ],
    },
}
NOTES = (
    "Synthetic shapes for Ireland's 1885–1918 county divisions outside the six counties, built from Tailte Éireann's "
    "statutory baronies, civil parishes, and townlands as the Redistribution of Seats Act 1885 (48 & 49 Vict. c. 23), "
    "Seventh Schedule, Part III, defines each division. The barony, parish, and townland lines are today's statutory "
    "ones. Each county's city seat (Cork, Galway, Kilkenny, Limerick, and Waterford, which also takes the barony of "
    "Kilculliheen) is the rest of its county, and Dublin's four divisions, which the Act defines by municipal wards, "
    "are the rest of County Dublin cut into quarters at the Liffey and Sackville Street."
)


def key(text: str) -> str:
    return fold(re.sub(r"\([^)]*RA\)", "", text.removeprefix("Part of ").removeprefix("PART OF "))).replace(" ", "")


def layer_pages(name: str) -> list[str]:
    service, layer, count = LAYERS[name]
    return [
        f"{SERVICES}/{service}/FeatureServer/{layer}/query?where=1%3D1&outFields=ENG_NAME_VALUE&outSR=2157"
        f"&maxAllowableOffset=5&orderByFields=OBJECTID&resultOffset={offset}&resultRecordCount=1000&f=geojson"
        for offset in range(0, count, 1000)
    ]


def townland_pages() -> list[str]:
    return [
        f"{TOWNLANDS}?where=1%3D1&outFields=TD_ID,ENGLISH,COUNTY&returnGeometry=false&orderByFields=TD_ID"
        f"&resultOffset={offset}&resultRecordCount=2000&f=json"
        for offset in range(0, TOWNLAND_COUNT, 2000)
    ]


def wanted_townlands() -> dict[tuple[str, str], list[str]]:
    rows = [
        feature["attributes"]
        for url in townland_pages()
        for feature in json.loads(raw.cached(SOURCE, url).path.read_text())["features"]
    ]
    named: dict[str, dict[str, list[str]]] = {}
    for row in rows:
        named.setdefault(row["COUNTY"].upper(), {}).setdefault(key(row["ENGLISH"]), []).append(row["TD_ID"])
    found: dict[tuple[str, str], list[str]] = {}
    for county, divisions in RULES.items():
        for rules in divisions.values():
            for rule in rules:
                names = rule[3:] if rule[0] in ("P", "T") else ()
                for name in names:
                    candidates = named.get(county, {})
                    match = (
                        key(name)
                        if key(name) in candidates
                        else next(iter(difflib.get_close_matches(key(name), list(candidates), n=1, cutoff=0.85)), None)
                    )
                    if match is None:
                        raise ValueError(f"no townland {name} in {county}")
                    found[(county, name)] = candidates[match]
    return found


def townland_geometry_urls() -> list[str]:
    ids = sorted({id for ids in wanted_townlands().values() for id in ids})
    return [
        f"{TOWNLANDS}?where={quote('TD_ID in (' + ','.join(repr(id) for id in ids[start : start + 100]) + ')')}"
        "&outFields=TD_ID&outSR=2157&f=geojson"
        for start in range(0, len(ids), 100)
    ]


def fetch() -> None:
    for name in LAYERS:
        for url in layer_pages(name):
            raw.fetch(SOURCE, url, suffix=".geojson")
    for url in townland_pages():
        raw.fetch(SOURCE, url, suffix=".json")
    for url in townland_geometry_urls():
        raw.fetch(SOURCE, url, suffix=".geojson")


def parse() -> None:
    features = {
        name: [
            (feature["properties"]["ENG_NAME_VALUE"], json.dumps(feature["geometry"]))
            for url in layer_pages(name)
            for feature in json.loads(raw.cached(SOURCE, url).path.read_text())["features"]
            if feature["geometry"] is not None
        ]
        for name in LAYERS
    }
    townlands = [
        (feature["properties"]["TD_ID"], json.dumps(feature["geometry"]))
        for url in townland_geometry_urls()
        for feature in json.loads(raw.cached(SOURCE, url).path.read_text())["features"]
    ]
    wanted = wanted_townlands()
    rows = [
        *((layer, *row) for layer, rows in features.items() for row in rows),
        *(("townland", *row) for row in townlands),
    ]
    with connect() as conn:
        conn.execute("set work_mem = '512MB'")
        conn.execute("create temp table source_shape (id int, layer text, name text, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy source_shape from stdin") as copy:
            for id, row in enumerate(rows):
                copy.write_row((id, *row))
        conn.execute(
            """create temp table shape on commit drop as
               select id, layer, name, st_multi(st_collectionextract(st_makevalid(
                        st_setsrid(st_force2d(st_geomfromgeojson(geojson)), 2157)), 3)) as geom
               from source_shape"""
        )
        conn.execute("create index on shape using gist (geom)")
        conn.execute("create index on shape (id)")
        conn.execute(
            """create temp table placed on commit drop as
               select s.id, s.layer, s.name,
                      (select c.name from shape c where c.layer = 'county' and st_intersects(c.geom, s.geom)
                       order by st_area(st_intersection(c.geom, s.geom)) desc limit 1) as county,
                      (select b.id from shape b where b.layer = 'barony' and s.layer = 'parish'
                         and st_intersects(b.geom, s.geom)
                       order by st_area(st_intersection(b.geom, s.geom)) desc limit 1) as barony
               from shape s where s.layer <> 'county'"""
        )
        placed = conn.execute("select id, layer, name, county, barony from placed where county is not null").fetchall()
        print(conn.execute("select layer, count(*) from placed where county is null group by layer").fetchall())
        baronies: dict[tuple[str, str], list[int]] = {}
        for id, layer, name, county, _ in placed:
            if layer == "barony":
                baronies.setdefault((county.upper(), key(name)), []).append(id)
        parishes: dict[tuple[str, int], dict[str, list[int]]] = {}
        for id, layer, name, county, barony in placed:
            if layer == "parish":
                parishes.setdefault((county.upper(), barony), {}).setdefault(key(name), []).append(id)
        by_td = {name: id for id, layer, name, *_ in placed if layer == "townland"}
        misses: list[str] = []

        def barony(county: str, name: str) -> list[int]:
            if (county, key(name)) not in baronies:
                misses.append(f"barony {name} in {county}")
                return []
            return baronies[(county, key(name))]

        def parish(county: str, barony_id: list[int] | None, name: str) -> list[int]:
            pools = [
                names
                for (place, owner), names in parishes.items()
                if place == county and (barony_id is None or owner in barony_id)
            ]
            pool = {name: ids for names in pools for name, ids in names.items()}
            match = (
                key(name)
                if key(name) in pool
                else next(iter(difflib.get_close_matches(key(name), list(pool), n=1, cutoff=0.8)), None)
            )
            if match is None:
                misses.append(f"parish {name} in {county} barony {barony_id}")
                return []
            return pool[match]

        units: list[tuple[str, str, str, list[int], list[int], list[int], list[int]]] = []
        for county, divisions in RULES.items():
            for division, rules in divisions.items():
                for kind, *rest in rules:
                    if kind == "B" and rest[0] == "*":
                        units.append((division, county, "C", [], [], [], []))
                    elif kind == "B":
                        units.append((division, county, "B", barony(county, rest[0]), [], [], []))
                    elif kind == "P":
                        units.append(
                            (
                                division,
                                county,
                                "P",
                                parish(county, None, rest[1]),
                                [by_td[id] for name in rest[2:] for id in wanted[(county, name)]],
                                [],
                                barony(county, rest[0]),
                            )
                        )
                    elif kind == "S":
                        units.append(
                            (
                                division,
                                county,
                                "S",
                                parish(county, None, rest[1]),
                                [],
                                parish(county, None, rest[2]),
                                barony(county, rest[0]),
                            )
                        )
                    else:
                        units.append(
                            (
                                division,
                                county,
                                "T",
                                [by_td[id] for name in rest[2:] for id in wanted[(county, name)]],
                                [],
                                parish(county, None, rest[1]),
                                [],
                            )
                        )
        if misses:
            raise ValueError("unmatched:\n" + "\n".join(misses))
        transfers = {division: barony(county, name) for (county, name), division in TRANSFERS.items()}
        conn.execute(
            """create temp table unit (division text, county text, kind text, ids int[], minus int[], within int[], baronies int[])
               on commit drop"""
        )
        with conn.cursor() as cursor, cursor.copy("copy unit from stdin") as copy:
            for row in units:
                copy.write_row(row)
        conn.execute(
            """create temp table piece on commit drop as
               select u.division, u.county, u.kind,
                      (select st_union(t.geom) from shape t where t.id = any(u.ids)
                         and exists (select 1 from shape p where p.id = any(u.within)
                                     and st_contains(p.geom, st_pointonsurface(t.geom)))) as geom
               from unit u where u.kind = 'T'"""
        )
        conn.execute(
            """insert into piece
               select u.division, u.county, 'S', (
                 select st_union(part.geom)
                 from st_dump(st_intersection(
                        (select st_union(p.geom) from shape p where p.id = any(u.ids)),
                        (select st_union(b.geom) from shape b where b.id = any(u.baronies)))) part
                 where true
                   and st_length(st_intersection(st_boundary(part.geom),
                         (select st_buffer(st_union(k.geom), 30) from shape k where k.id = any(u.within))))
                       > 0.5 * st_perimeter(part.geom))
               from unit u where u.kind = 'S'"""
        )
        conn.execute(
            """insert into piece
               select u.division, u.county, 'P', st_difference(
                 st_intersection((select st_union(p.geom) from shape p where p.id = any(u.ids)),
                                 (select st_union(b.geom) from shape b where b.id = any(u.baronies))),
                 coalesce((select st_union(geom) from (
                     select t.geom from shape t where t.id = any(u.minus)
                     union all select x.geom from piece x where x.county = u.county) taken),
                   'SRID=2157;POLYGON EMPTY'::geometry))
               from unit u where u.kind = 'P'"""
        )
        conn.execute(
            """insert into piece
               select u.division, u.county, 'B', st_difference(
                 (select st_union(b.geom) from shape b where b.id = any(u.ids)),
                 coalesce((select st_union(x.geom) from piece x where x.county = u.county),
                   'SRID=2157;POLYGON EMPTY'::geometry))
               from unit u where u.kind = 'B'"""
        )
        conn.execute(
            """insert into piece
               select u.division, u.county, 'C', c.geom from unit u join shape c on c.layer = 'county' and c.name ilike u.county
               where u.kind = 'C'"""
        )
        conn.execute(
            """create temp table division on commit drop as
               select division, county, st_multi(st_collectionextract(st_makevalid(st_union(geom)), 3)) as geom
               from piece where geom is not null group by division, county"""
        )
        for division, ids in transfers.items():
            conn.execute(
                """insert into division select %s, 'TRANSFER', st_union(geom) from shape where id = any(%s)""",
                [division, ids],
            )
        conn.execute(
            """create temp table rest on commit drop as
               select upper(c.name) as county, st_multi(st_collectionextract(st_difference(c.geom,
                        coalesce((select st_union(d.geom) from division d where d.county ilike c.name
                                   or (d.county = 'TRANSFER')), 'SRID=2157;POLYGON EMPTY'::geometry)), 3)) as geom
               from (select name, st_union(geom) as geom from shape where layer = 'county' group by name) c"""
        )
        for county, division in CITIES.items():
            conn.execute(
                "insert into division select %s, %s, geom from rest where county = %s", [division, county, county]
            )
        conn.execute(
            """insert into division
               select %(quarters)s::jsonb ->> (case when st_y(st_pointonsurface(q.geom)) >= y then 'N' else 'S' end
                                            || case when st_x(st_pointonsurface(q.geom)) >= x then 'E' else 'W' end),
                      'DUBLIN', q.geom
               from rest r,
                    lateral (select st_x(c) as x, st_y(c) as y
                             from st_transform(st_setsrid(st_point(%(lon)s, %(lat)s), 4326), 2157) c) center,
                    lateral (select st_intersection(r.geom, st_makeenvelope(x0, y0, x0 + 40000, y0 + 40000, 2157)) as geom
                             from (values (center.x - 40000, center.y - 40000), (center.x, center.y - 40000),
                                          (center.x - 40000, center.y), (center.x, center.y)) corner(x0, y0)) q
               where r.county = 'DUBLIN' and not st_isempty(q.geom)""",
            {"quarters": json.dumps(DUBLIN), "lon": DUBLIN_CENTER[0], "lat": DUBLIN_CENTER[1]},
        )
        conn.execute(
            """insert into division
               select (select d.division from division d where d.county ilike r.county
                       order by st_area(st_intersection(d.geom, st_buffer(part.geom, 30))) desc limit 1),
                      r.county, part.geom
               from rest r, lateral st_dump(r.geom) part
               where r.county not in (select unnest(%s::text[])) and r.county <> 'DUBLIN'""",
            [[*CITIES, "DUBLIN"]],
        )
        shapes = conn.execute(
            """select division, encode(st_asewkb(st_transform(geom, 4326)), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
               from (select division, st_setsrid(st_coveragesimplify(geom, %s) over (), 2157) as geom
                     from (select division, st_setsrid(st_coverageclean(geom) over (), 2157) as geom
                           from (select division, st_multi(st_collectionextract(st_makevalid(st_union(geom)), 3)) as geom
                                 from division where division is not null group by division) whole) clean) simple""",
            [TOLERANCE],
        ).fetchall()
    frame = pl.DataFrame(
        shapes, schema={"division": pl.String, "geom": pl.String, "label_point": pl.String}, orient="row"
    )
    print(frame.height, "divisions")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "areas.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "areas.parquet")
    with connect() as conn:
        areas = {
            code.removeprefix("1885:irl-"): (area, start, end)
            for code, area, start, end in conn.execute(
                """select c.code, a.id, lower(c.valid), upper(c.valid) from area a join area_code c on c.area_id = a.id
                   where a.scheme_code = 'gb.pcon' and c.code like '1885:irl-%'"""
            ).fetchall()
        }
        divisions = set(frame["division"])
        if missing := sorted(divisions - set(areas)):
            raise ValueError(f"no gb.pcon area for {missing}")
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Synthetic shapes for Ireland's 1885–1918 constituencies outside the six counties, built from "
            "Tailte Éireann's baronies, civil parishes, and townlands as the Redistribution of Seats Act 1885 "
            "defines them",
            polity="GB",
        )
        publisher = "Tailte Éireann"
        title = "National Statutory Boundaries: Baronies, Civil Parishes, Counties, and Townlands"
        source = cs.source(publisher, title)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=publisher,
            title=title,
            url="https://data-osi.opendata.arcgis.com/",
            license="CC BY 4.0",
            license_url="https://creativecommons.org/licenses/by/4.0/",
            attribution="© Tailte Éireann",
            notes=f"{NOTES} Statute text: The Public General Statutes 1885, pp. 201–213 ({STATUTE}).",
        )
        for name in LAYERS:
            for url in layer_pages(name):
                cs.artifact(raw.cached(SOURCE, url), source)
        for url in [*townland_pages(), *townland_geometry_urls()]:
            cs.artifact(raw.cached(SOURCE, url), source)
        for division, geom, point in frame.rows():
            area, start, end = areas[division]
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=start,
                valid_to=end,
                geom=geom,
                label_point=point,
                quality="synthetic",
                source_id=source,
            )
        return cs.write()
