import csv
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "wikidata"
VERSION = "11"
TITLE = "Political parties and their colors"
HAND_COLORS = Path(__file__).with_name("tr_party_colors.tsv")
HAND_POSITIONS = Path(__file__).with_name("tr_party_positions.tsv")
POSITIONED = "Political positions of parties"
COUNTRIES = {
    "US": "Q30",
    "CA": "Q16",
    "TR": "Q43",
    "DE": "Q183",
    "BR": "Q155",
    "FR": "Q142",
    "IT": "Q38",
    "IE": "Q27",
    "CH": "Q39",
    "GB": "Q145",
    "EU": None,
}
LANGUAGES = '"en", "mul", "fr", "de", "it", "pt", "tr", "ga", "rm"'
NAMES = """SELECT DISTINCT ?item ?kind ?name WHERE {{
  {scope}
  {{ ?item rdfs:label ?name . BIND("label" AS ?kind) }}
  UNION {{ ?item skos:altLabel ?name . BIND("alias" AS ?kind) }}
  UNION {{ ?item wdt:P1813 ?name . BIND("short" AS ?kind) }}
  UNION {{ ?item wdt:P1448 ?name . BIND("label" AS ?kind) }}
  FILTER(LANG(?name) IN ({languages}))
}}"""
LABELS = """SELECT DISTINCT ?item ?name WHERE {{
  {scope}
  ?item rdfs:label ?name .
  FILTER(LANG(?name) = "en")
}}"""
SPANS = """SELECT DISTINCT ?item ?inception ?dissolved ?links WHERE {{
  {scope}
  ?item wikibase:sitelinks ?links .
  OPTIONAL {{ ?item wdt:P571 ?inception }}
  OPTIONAL {{ ?item wdt:P576 ?dissolved }}
}}"""
COLORS = """SELECT DISTINCT ?item ?statement ?color ?rank ?start ?startPrecision ?end ?endPrecision WHERE {{
  {scope}
  ?item p:P465 ?statement . ?statement ps:P465 ?color ; wikibase:rank ?rank .
  OPTIONAL {{ ?statement pqv:P580 [ wikibase:timeValue ?start ; wikibase:timePrecision ?startPrecision ] }}
  OPTIONAL {{ ?statement pqv:P582 [ wikibase:timeValue ?end ; wikibase:timePrecision ?endPrecision ] }}
}}"""
POSITIONS = """SELECT DISTINCT ?item ?statement ?position ?rank WHERE {{
  {scope}
  ?item p:P1387 ?statement . ?statement ps:P1387 ?position ; wikibase:rank ?rank .
}}"""
STEPS = {
    "Q1129409": -3,
    "Q15141601": -3,
    "Q164597": -2,
    "Q737014": -1,
    "Q6587194": 0,
    "Q844072": 1,
    "Q76074": 2,
    "Q127869500": 3,
    "Q204481": 3,
}
WEIGHTS = {"label": 3, "short": 2, "alias": 1}
THRESHOLD = 4
SPLIT = r"\s[-–—]\s"
LINKED = ("party", "federation", "parliamentary_group", "independent_group")
MATCHES: dict[tuple[str, str], str | None] = {
    ("US", "Forward Party"): "Q108816586",
    ("US-CO", "Forward Party"): None,
    ("US-PA", "Forward Party"): None,
    ("US-VA", "Forward Party"): None,
    ("US", "Green Party"): "Q849158",
    ("US", "Reform Party"): "Q1893430",
    ("US", "Socialist Party USA"): "Q581259",
    ("US-NY", "Liberal"): "Q6540770",
    ("US-NY", "Right To Life"): "Q2513114",
    ("CA", "Conservative"): "Q909827",
    ("CA", "Progressive Conservative"): "Q1292617",
    ("CA", "Labour Progressive Party"): "Q3366569",
    ("CH-ZH", "AL – Alternative Liste"): "Q438524",
    ("IE", "People Before Profit–Solidarity"): "Q21218940",
    ("EU", "Europe of Sovereign Nations Group"): "Q127264685",
    ("EU", "Group of the European People's Party (Christian Democrats)"): "Q635616",
    ("EU", "Group of the Greens/European Free Alliance"): "Q751935",
    (
        "EU",
        "Group of the Progressive Alliance of Socialists and Democrats in the European Parliament",
    ): "Q507343",
    ("EU", "Identity and Democracy Group"): "Q64590677",
    ("EU", "Non-attached Members"): "Q1440626",
    ("EU", "Renew Europe Group"): "Q64590185",
    ("EU", "The Left group in the European Parliament - GUE/NGL"): "Q753711",
    ("FR", "DIV"): "Q97161383",
    ("FR", "DVC"): "Q3032227",
    ("FR", "DVD"): "Q1230558",
    ("FR", "DVG"): "Q1230566",
    ("FR", "DSV"): "Q113835267",
    ("FR", "ECO"): "Q127282650",
    ("FR", "EXD"): "Q127282704",
    ("FR", "EXG"): "Q127294071",
    ("FR", "REG"): "Q127282254",
    ("FR", "UG"): None,
    ("FR", "UXD"): "Q127038776",
    ("IT", "Alleanza Verdi e Sinistra"): "Q113168799",
    ("IT", "Azione – Italia Viva"): "Q114246565",
    ("IT", "Italia Sovrana e Popolare"): "Q113490502",
    ("IT", "Unione Popolare"): None,
    ("TR", "Yeşiller ve Sol Gelecek Partisi"): "Q725173",
    ("TR", "Cumhuriyetçi Köylü Millet Partisi"): "Q6089601",
    ("DE", "de.party:KPD"): "Q153401",
    ("DE", "de.party:KPD (OST)"): "Q321633",
    ("DE", "de.party:KPD/AO"): "Q1752945",
    ("DE", "de.party:KPD/ML"): "Q324107",
    ("DE", "DL"): None,
    ("CA", "Marxist-Leninist Party of Canada"): "Q2344321",
    ("CA", "Reconstruction Party"): "Q7302781",
    ("CA", "Confederation of Regions Western Party"): "Q2992408",
    ("CA-QC", "Parti québécois"): "Q950356",
    ("CA-QC", "Parti conservateur du Québec"): "Q3366254",
    ("CA-QC", "Québec solidaire"): "Q2660011",
    ("CA-QC", "Coalition avenir Québec"): "Q2348226",
    ("CA-QC", "Parti libéral du Québec"): "Q1631384",
    ("CA-BC", "BC NDP"): "Q919052",
    ("CA-BC", "BC Green Party"): "Q3366818",
    ("DE", "Deutsche Reichs-Partei"): "Q495497",
    ("DE", "NATURGESETZ PARTEI, AUFBRUCH ZU NEUEM BEWUSSTSEIN"): "Q137687443",
    ("GB", "National Front"): "Q158093",
    ("GB", "Independent Labour Party"): "Q1507913",
    ("GB", "Independent Labour"): None,
    ("US-CO", "Farmer-Labor Party"): None,
}
INHERITED: dict[tuple[str, str], str] = {
    ("FR", "ENS"): "Q109929254",
    ("FR", "UG"): "Q126487286",
    ("FR", "UXD"): "Q127038760",
    ("IT", "Impegno Civico"): "Q113375810",
    ("IT", "Partito Democratico – Italia Democratica e Progressista"): "Q47729",
    ("IT", "Südtiroler Volkspartei – PATT"): "Q606620",
    ("CH-ZH", "Partei der Arbeit"): "Q661771",
}
CHOSEN = {"Q47729": "EF1C27", "Q113168799": "56B45D"}
PAINTED: dict[tuple[str, str, int | None], str] = {
    ("US", "American Independent Party", None): "800080",
    ("US", "Anti-Administration", None): "5BA45B",
    ("US", "Anti-Jacksonian", None): "FFE6B0",
    ("US", "Constitutional Union Party", None): "F59100",
    ("US", "Democratic-Republican Party", None): "008000",
    ("US", "Federalist Party", None): "EA9978",
    ("US", "Free Soil Party", None): "CCCC66",
    ("US", "Greenback Party", None): "0BDA51",
    ("US", "Jacksonian", None): "698DC5",
    ("US", "Liberal Republican Party", None): "FF69B4",
    ("US", "Liberty Party", None): "00DDDD",
    ("US", "National Republican Party", None): "FFE6B0",
    ("US", "Progressive Party", 1912): "B63434",
    ("US", "Progressive Party", 1924): "5FD170",
    ("US", "Pro-Administration", None): "F4B183",
    ("US", "Prohibition Party", None): "FF00FF",
    ("US", "Socialist Party USA", None): "CD3700",
    ("US", "Socialist Party of America", None): "CD3700",
    ("US", "Southern Democrats", None): "4F990C",
    ("US", "States' Rights Democratic Party", None): "FF9955",
    ("US", "Union Party", None): "7F3300",
    ("DE", "Bavarian Peasants' League", None): "4C9A3C",
    ("DE", "Bavarian People's Party", None): "5EA4D8",
    ("DE", "Centre Party", None): "6E6E6E",
    ("DE", "Christian National Working Group", None): "9A7B4F",
    ("DE", "Christian Social People's Service", None): "F0A030",
    ("DE", "Christian-National Peasants' and Farmers' Party", None): "3E8E5E",
    ("DE", "German Democratic Party", None): "DCCA4A",
    ("DE", "German National People's Party", None): "63B8FF",
    ("DE", "German People's Party", None): "D5AC27",
    ("DE", "Nazi Party", None): "A0522D",
    ("DE", "Reich Party of the German Middle Class", None): "B8860B",
    ("FR", "Democratic and Socialist Union of the Resistance", None): "E8A0BF",
    ("FR", "Democratic Republican Alliance", None): "8DB6E0",
    ("FR", "Ecologist Party", None): "6AB023",
    ("FR", "French Socialist Party", None): "D9534F",
    ("FR", "Gaullists", None): "4F6BB5",
    ("FR", "Independent Republicans", None): "7BA7D9",
    ("FR", "Independent Socialists", None): "E07A9C",
    ("FR", "Les Centristes", None): "3BA0D8",
    ("FR", "Popular Democratic Party", None): "A0C4E8",
    ("FR", "Rally of Republican Lefts", None): "F2C14E",
    ("FR", "Reforming Movement", None): "F2A65A",
    ("FR", "Republican and Social Action", None): "6A8CC7",
    ("FR", "Socialist Party of France – Jean Jaurès Union", None): "E08A8A",
    ("FR", "The Greens", None): "3FA34D",
    ("GB", "Coalition Labour", None): "D97B7B",
    ("GB", "Plaid Cymru", None): "A3B800",
    ("GB", "National Labour Organisation", None): "C27BA0",
    ("GB", "Radicals", None): "C2185B",
    ("GB", "All-for-Ireland", None): "8DB600",
    ("GB", "Alliance for Green Socialism", None): "7CBA00",
    ("GB", "Anti-Federalist League", None): "9B4F96",
    ("GB", "Aontú", None): "44532A",
    ("GB", "Campaign for a More Prosperous Britain", None): "A9A9A9",
    ("GB", "Cannabis Is Safer Than Alcohol", None): "6B8E23",
    ("GB", "Chartist", None): "B22222",
    ("GB", "Christian Pacifist", None): "C9A0DC",
    ("GB", "Christian Party, Proclaiming Christ's Lordship", None): "9B59B6",
    ("GB", "Christian Vote", None): "B28DC7",
    ("GB", "Climate Party", None): "2E8B57",
    ("GB", "Communist League", None): "A00000",
    ("GB", "Communist Party of Britain", None): "D50000",
    ("GB", "Communist Party of England Marxist-Leninist", None): "8B0000",
    ("GB", "Communist Party of Great Britain", None): "C00000",
    ("GB", "Democratic Labour", None): "E9A0A0",
    ("GB", "Free Trade", None): "E6C35C",
    ("GB", "Green Party", None): "02A95B",
    ("GB", "Health Concern", None): "0071BB",
    ("GB", "Home Rule League (Parnellite)", None): "66BB66",
    ("GB", "Independent Conservative", None): "8FB8E8",
    ("GB", "Independent Liberal", None): "FFC77A",
    ("GB", "Independent Liberal and Crofters", None): "FFD39B",
    ("GB", "Independent Liberal-Labour", None): "F4B183",
    ("GB", "Independent Nationalist", None): "8FD18F",
    ("GB", "Independent Progressive", None): "C4A484",
    ("GB", "Independent Republican", None): "3F8F3F",
    ("GB", "Independent Tariff Reform", None): "6F8FAF",
    ("GB", "Independent Unionist", None): "A3C1E0",
    ("GB", "Independent Unionist with Coalition Coupon", None): "7FA3CC",
    ("GB", "Independent with Coalition Coupon", None): "BBBBBB",
    ("GB", "Irish Confederation", None): "2E8B57",
    ("GB", "Irish Conservative", None): "4F6FAF",
    ("GB", "Irish Labour", None): "CC0000",
    ("GB", "Irish National Federation", None): "4CAF50",
    ("GB", "Irish National League", None): "99CC33",
    ("GB", "Irish Nationalist", None): "2EA44F",
    ("GB", "Irish Unionist", None): "9999FF",
    ("GB", "Labour Unionist", None): "8F7FBF",
    ("GB", "Labour with Coalition Coupon", None): "E07070",
    ("GB", "Left Unity", None): "D7263D",
    ("GB", "Legalise Cannabis", None): "4E9A06",
    ("GB", "Liberal Unionist and Crofters", None): "E8A96B",
    ("GB", "Liberal and Crofters", None): "FFB347",
    ("GB", "Liberal-Conservative", None): "B0A8D9",
    ("GB", "Liberal-Labour", None): "FF9966",
    ("GB", "NFDDSS", None): "A0522D",
    ("GB", "NI Labour", None): "E06666",
    ("GB", "National Democratic and Labour with Coalition Coupon", None): "C08080",
    ("GB", "National Front", None): "191970",
    ("GB", "National Government", None): "4F6EA8",
    ("GB", "National Independent", None): "9DB4D6",
    ("GB", "National Party", None): "4B3B8F",
    ("GB", "Nationalist Party of Northern Ireland", None): "1E9E50",
    ("GB", "Natural Law", None): "E6B800",
    ("GB", "New Party", None): "5C4033",
    ("GB", "PEOPLE Party", None): "77AA33",
    ("GB", "Party of Women", None): "7B2D8E",
    ("GB", "Peelite", None): "99CCFF",
    ("GB", "Pirate Party UK", None): "660099",
    ("GB", "ProLife Alliance", None): "C43B8C",
    ("GB", "Protestant Unionist", None): "C8553D",
    ("GB", "Rainbow Dream Ticket", None): "FF69B4",
    ("GB", "Red Front", None): "B30000",
    ("GB", "Repeal Association and Whig", None): "9ACD32",
    ("GB", "Republican Clubs", None): "006400",
    ("GB", "Republican Labour", None): "8DC63F",
    ("GB", "Russellite Unionist", None): "88AAEE",
    ("GB", "Scottish Green Party", None): "00B140",
    ("GB", "Scottish Prohibition", None): "8B5A2B",
    ("GB", "Scottish Socialist Alliance", None): "F04E37",
    ("GB", "Social Democratic Federation", None): "C83737",
    ("GB", "Social Democratic and Labour Party", None): "2AA82C",
    ("GB", "Socialist (GB)", None): "E0115F",
    ("GB", "Socialist Alliance", None): "D2042D",
    ("GB", "Socialist Alternative", None): "B71C1C",
    ("GB", "Socialist Unity", None): "CE1126",
    ("GB", "Speaker with Coalition Coupon", None): "666666",
    ("GB", "St George's, Hanover Square, Independent Conservative Association", None): "6F95C9",
    ("GB", "The Justice & Anti-Corruption Party", None): "6D6D9B",
    ("GB", "The Peace Party - Non-violence, Justice, Environment", None): "8FBC8F",
    ("GB", "The Socialist Party of Great Britain", None): "D71920",
    ("GB", "The Yorkshire Party", None): "0099CC",
    ("GB", "UK Independence Party", None): "6D3177",
    ("GB", "Ulster Liberal", None): "FFAA00",
    ("GB", "Ulster Popular Unionist", None): "7799CC",
    ("GB", "Unionist Party (Scotland)", None): "5E4FA2",
    ("GB", "United Democratic", None): "B8B8B8",
    ("GB", "United Ulster Unionist", None): "5577BB",
    ("GB", "Unity", None): "4C9A2A",
    ("GB", "Vanguard", None): "E07020",
    ("GB", "Wessex Regionalist", None): "9B111E",
    ("GB", "Wick Radical Workingmen's Association", None): "E68A00",
    ("GB", "Workers Party of Britain", None): "990000",
    ("GB", "Yorkshire First", None): "33AADD",
    ("IT", "Christian Democratic Centre", None): "7FA9DC",
    ("IT", "Federation of the Greens", None): "5DB04B",
    ("IT", "New PSI", None): "E0614A",
    ("IT", "Party of Italian Communists", None): "D0383A",
    ("IT", "Populars for Prodi", None): "58B6D8",
    ("IT", "Radical Party", None): "D9B300",
    ("IT", "The Olive Tree", None): "7FB547",
    ("IT", "White Flower", None): "6FA0DC",
    ("BR", "Republicanos", None): "41A9D0",
    ("DE", "Die PARTEI", None): "A73A1A",
    ("DE", "FAMILIE", None): "FF987D",
    ("DE", "PdF", None): "C49209",
    ("DE", "Tierschutzpartei", None): "006D42",
    ("FR", "DVC", None): "FAAF5F",
    ("FR", "LR", None): "3D8FE0",
    ("FR", "SOC", None): "C62C57",
    ("FR", "UDI", None): "32B6FE",
    ("FR", "UXD", None): "5A3B5C",
    ("IT", "Azione – Italia Viva", None): "392CAA",
    ("IT", "Lega per Salvini Premier", None): "008000",
    ("TR", "Adalet Partisi", 1961): "D360BC",
    ("TR", "Anavatan Partisi", 2023): "FBBA00",
    ("TR", "Barış Partisi", None): "CD4A4C",
    ("TR", "Büyük Birlik Partisi", None): "CC5252",
    ("TR", "Cumhuriyetçi Güven Partisi", None): "FF4500",
    ("TR", "Cumhuriyetçi Millet Partisi", None): "7A1F2B",
    ("TR", "Demokrat Parti", None): "CD42B2",
    ("TR", "Demokrat Türkiye Partisi", None): "DA7766",
    ("TR", "Demokratik Parti", None): "FFA500",
    ("TR", "Doğru Yol Partisi", 2011): "D360BC",
    ("TR", "Fazilet Partisi", None): "FF2E84",
    ("TR", "Güven Partisi", None): "FF4500",
    ("TR", "Halkçı Parti", None): "D40000",
    ("TR", "Hürriyet Partisi", 1957): "C1F711",
    ("TR", "Komünist Parti", None): "CC0000",
    ("TR", "Memleket Partisi", None): "0D5DA6",
    ("TR", "Millet Partisi", 1950): "7A1F2B",
    ("TR", "Millet Partisi", 1965): "C65E1F",
    ("TR", "Millet Partisi", 1987): "C83737",
    ("TR", "Milli Selamet Partisi", None): "FF2E84",
    ("TR", "Milli Yol Partisi", None): "F40F0F",
    ("TR", "Milliyetçi Hareket Partisi", None): "7A1F2B",
    ("TR", "Refah Partisi", None): "FF2E84",
    ("TR", "Türkiye Birlik Partisi", None): "B7006D",
    ("TR", "Türkiye İşçi Partisi", None): "CD0668",
    ("TR", "Türkiye Komünist Hareketi", None): "990000",
    ("TR", "Yeni Parti", None): "4B4AAF",
    ("TR", "Yeni Türkiye Partisi", 1961): "00B0F0",
    ("TR", "Yeniden Refah Partisi", None): "474647",
    ("TR", "Yeşiller ve Sol Gelecek Partisi", None): "388537",
    ("TR", "Zafer Partisi", None): "373736",
    ("US", "Natural Law Party", None): "D4A017",
    ("US", "Reform Party", None): "6A287E",
    ("US", "Socialist Workers Party", None): "AA0000",
    ("US-NY", "Conservative Party", None): "FF8C00",
    ("US-NY", "Independence Party", None): "8E7CC3",
    ("US-NY", "Right To Life", None): "8B0A50",
}
PLACED: dict[tuple[str, str], tuple[int, int]] = {
    ("DE", "Bavarian Peasants' League"): (0, 1),
    ("DE", "Bavarian People's Party"): (1, 2),
    ("DE", "Centre Party"): (0, 1),
    ("DE", "Christian National Working Group"): (2, 2),
    ("DE", "Christian Social People's Service"): (1, 1),
    ("DE", "Christian-National Peasants' and Farmers' Party"): (2, 2),
    ("DE", "Free People's Party"): (1, 1),
    ("DE", "German Democratic Party"): (-1, 0),
    ("DE", "German National People's Party"): (2, 3),
    ("DE", "German People's Party"): (0, 1),
    ("DE", "Nazi Party"): (3, 3),
    ("DE", "Reich Party of the German Middle Class"): (1, 2),
    ("FR", "Centre, Democracy and Progress"): (0, 1),
    ("FR", "Citizen and Republican Movement"): (-2, -1),
    ("FR", "Democratic and Socialist Union of the Resistance"): (-1, 0),
    ("FR", "French Socialist Party"): (-3, -2),
    ("FR", "Gaullists"): (1, 2),
    ("FR", "Independent Radicals"): (0, 1),
    ("FR", "Independent Republicans"): (1, 1),
    ("FR", "Independent Socialists"): (-2, -1),
    ("FR", "Rally of Republican Lefts"): (0, 1),
    ("FR", "Reforming Movement"): (0, 0),
    ("FR", "Republican and Social Action"): (1, 2),
    ("FR", "Republican Federation"): (1, 2),
    ("FR", "Republican Party of Liberty"): (1, 2),
    ("FR", "Republican-Socialist Party"): (-1, 0),
    ("FR", "Socialist Party of France – Jean Jaurès Union"): (-1, 0),
    ("GB", "Coalition Labour"): (-1, 0),
    ("GB", "National Labour Organisation"): (-1, 0),
    ("GB", "National Liberal Party"): (0, 1),
    ("IT", "Christian Democratic Centre"): (0, 1),
    ("IT", "Democratic Union for the Republic"): (0, 0),
    ("IT", "Federation of the Greens"): (-2, -1),
    ("IT", "Italian Democratic Socialist Party"): (-1, -1),
    ("IT", "Italian Democratic Socialists"): (-1, -1),
    ("IT", "Italian Liberal Party"): (0, 1),
    ("IT", "Italian Socialist Party"): (-2, -1),
    ("IT", "Populars for Prodi"): (0, 0),
    ("IT", "The Olive Tree"): (-1, -1),
    ("IT", "Union of the Centre (1993-1998)"): (0, 0),
    ("IT", "White Flower"): (0, 1),
    ("TR", "Demokrat Türkiye Partisi"): (1, 1),
    ("BR", "Partido dos Trabalhadores"): (-2, -1),
    ("BR", "Progressistas"): (1, 1),
    ("BR", "Republicanos"): (2, 2),
    ("CA", "Canadian Alliance"): (1, 2),
    ("CA", "Conservative"): (2, 2),
    ("CA", "Green Party of Canada"): (-1, -1),
    ("CA", "Ralliement Créditiste"): (2, 2),
    ("CA", "Reform Party of Canada"): (2, 2),
    ("CA", "Social Credit Party of Canada"): (2, 2),
    ("CH", "Die Mitte"): (0, 1),
    ("CH", "Eidgenössisch-Demokratische Union"): (2, 3),
    ("CH", "Evangelische Volkspartei der Schweiz"): (0, 0),
    ("DE", "BSW"): (-2, -2),
    ("DE", "Die PARTEI"): (-2, -1),
    ("DE", "FAMILIE"): (2, 2),
    ("DE", "PdF"): (-1, -1),
    ("DE", "SSW"): (-1, -1),
    ("DE", "Tierschutzpartei"): (-2, -2),
    ("DE", "Volt"): (-1, 0),
    ("IT", "Azione – Italia Viva"): (0, 0),
    ("IT", "Movimento 5 Stelle"): (-1, -1),
    ("TR", "Adalet Partisi"): (1, 1),
    ("TR", "Cumhuriyetçi Millet Partisi"): (2, 2),
    ("TR", "Demokratik Parti"): (1, 1),
    ("TR", "Demokratik Sol Parti"): (-1, -1),
    ("TR", "Fazilet Partisi"): (3, 3),
    ("TR", "Güven Partisi"): (0, 1),
    ("TR", "Halkçı Parti"): (-1, -1),
    ("TR", "Hürriyet Partisi"): (0, 1),
    ("TR", "Milli Selamet Partisi"): (3, 3),
    ("TR", "Millet Partisi"): (2, 2),
    ("TR", "Milliyetçi Demokrasi Partisi"): (2, 2),
    ("TR", "Milliyetçi Hareket Partisi"): (2, 3),
    ("TR", "Sosyaldemokrat Halkçı Parti"): (-1, -1),
    ("TR", "Türkiye İşçi Partisi"): (-2, -2),
    ("TR", "Yeni Türkiye Partisi"): (1, 1),
    ("US", "Democratic-Republican Party"): (-2, -2),
    ("US", "Socialist Workers Party"): (-3, -3),
    ("US-MN", "Minnesota Democratic–Farmer–Labor Party"): (-1, -1),
}
ORDER = {
    "CH": ["GLP", "EVP"],
    "DE": [
        "Die Linke",
        "BSW",
        "Tierschutzpartei",
        "SPD",
        "GRÜNE",
        "SSW",
        "PdF",
        "FDP",
        "FREIE WÄHLER",
        "CDU",
        "ÖDP",
        "CSU",
    ],
    "IT": [
        "Partito Democratico – Italia Democratica e Progressista",
        "Movimento 5 Stelle",
        "Azione – Italia Viva",
        "Südtiroler Volkspartei – PATT",
    ],
    "TR": [
        "Cumhuriyet Halk Partisi",
        "Sosyaldemokrat Halkçı Parti",
        "Demokratik Sol Parti",
        "Yeni Türkiye Partisi",
        "Adalet Partisi",
        "Demokratik Parti",
        "Doğru Yol Partisi",
        "Anavatan Partisi",
    ],
}


def _scope(country: str | None) -> str:
    if country is None:
        return "?item wdt:P31 wd:Q779079 ."
    return f"?item wdt:P31/wdt:P279* wd:Q7278 ; wdt:P17 wd:{country} ."


def _url(template: str, scope: str) -> str:
    return "https://query.wikidata.org/sparql?" + urlencode(
        {"query": template.format(scope=scope, languages=LANGUAGES), "format": "json"}
    )


def _extra(template: str) -> str:
    items = sorted({*(item for item in MATCHES.values() if item is not None), *INHERITED.values()})
    return _url(template, "VALUES ?item { " + " ".join(f"wd:{item}" for item in items) + " }")


def _urls() -> list[str]:
    return [
        _url(template, _scope(country)) for country in COUNTRIES.values() for template in (NAMES, LABELS, SPANS, COLORS)
    ] + [_extra(COLORS), _extra(LABELS)]


def _placed() -> list[str]:
    return [_url(POSITIONS, _scope(country)) for country in COUNTRIES.values()] + [_extra(POSITIONS)]


def _rows(url: str) -> list[dict[str, str]]:
    return [
        {key: value["value"] for key, value in binding.items()}
        for binding in json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))["results"]["bindings"]
    ]


def _norm(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.replace("ı", "i").replace("İ", "i").lower().replace("&", " and "))
    return " ".join(re.findall(r"[a-z0-9]+", "".join(c for c in folded if not unicodedata.combining(c))))


def _year(value: str | None) -> int | None:
    return int(value[:4]) if value is not None and re.match(r"\d{4}-", value) else None


def _day(value: str | None, precision: str | None) -> tuple[date | None, str]:
    if value is None or not re.match(r"\d{4}-\d\d-\d\d", value):
        return None, "day"
    kind = {"11": "day", "10": "month"}.get(precision or "", "year")
    day = date.fromisoformat(value[:10])
    return day.replace(day=1) if kind == "month" else day.replace(month=1, day=1) if kind == "year" else day, kind


def fetch() -> None:
    for url in _urls() + _placed():
        raw.fetch(SOURCE, url, suffix=".json")


def emit() -> Path:
    index: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(dict))
    spans: dict[str, tuple[int | None, int | None, int]] = {}
    statements: dict[str, list[dict[str, str]]] = defaultdict(list)
    stances: dict[str, list[dict[str, str]]] = defaultdict(list)
    for scope, country in COUNTRIES.items():
        for row in _rows(_url(NAMES, _scope(country))):
            item = row["item"].rsplit("/", 1)[1]
            parts = re.split(SPLIT, row["name"])
            weighted = [(row["name"], WEIGHTS[row["kind"]])]
            if len(parts) > 1:
                weighted += [(part, 2 if row["kind"] == "label" else 1) for part in parts]
            for text, weight in weighted:
                key = _norm(text)
                if key:
                    index[scope][key][item] = max(index[scope][key].get(item, 0), weight)
        for row in _rows(_url(SPANS, _scope(country))):
            item = row["item"].rsplit("/", 1)[1]
            began, ended, _ = spans.get(item, (None, None, 0))
            spans[item] = (
                min(filter(None, (began, _year(row.get("inception")))), default=None),
                max(filter(None, (ended, _year(row.get("dissolved")))), default=None),
                int(row["links"]),
            )
    seen: set[str] = set()
    for url in [_url(COLORS, _scope(country)) for country in COUNTRIES.values()] + [_extra(COLORS)] + _placed():
        for row in _rows(url):
            if row["statement"] not in seen:
                seen.add(row["statement"])
                (stances if "position" in row else statements)[row["item"].rsplit("/", 1)[1]].append(row)

    def spectrum(item: str | None) -> tuple[int, int] | None:
        rows = [
            row
            for row in stances.get(item or "", [])
            if not row["rank"].endswith("DeprecatedRank") and row["position"].rsplit("/", 1)[1] in STEPS
        ]
        steps = [
            STEPS[row["position"].rsplit("/", 1)[1]]
            for row in [row for row in rows if row["rank"].endswith("PreferredRank")] or rows
        ]
        return (min(steps), max(steps)) if steps else None

    def colors(item: str | None) -> list[tuple[str, date | None, str, date | None, str]]:
        if item is None:
            return []
        rows = [
            row
            for row in statements.get(item, [])
            if not row["rank"].endswith("DeprecatedRank") and re.fullmatch(r"[0-9A-Fa-f]{6}", row["color"])
        ]
        rows = (
            [row for row in rows if row["color"].upper() == CHOSEN[item]]
            if item in CHOSEN
            else [row for row in rows if row["rank"].endswith("PreferredRank")] or rows
        )
        dated = [row for row in rows if "start" in row or "end" in row]
        if not dated:
            return (
                []
                if len({row["color"].lower() for row in rows}) != 1
                else [(rows[0]["color"], None, "day", None, "day")]
            )
        ranges: list[tuple[str, date | None, str, date | None, str]] = []
        for row in sorted(dated, key=lambda row: row.get("start", "")):
            start, start_precision = _day(row.get("start"), row.get("startPrecision"))
            end, end_precision = _day(row.get("end"), row.get("endPrecision"))
            last = ranges[-1][3] if ranges else None
            if (ranges and (last is None or start is None or start < last)) or (start and end and end <= start):
                continue
            ranges.append((row["color"], start, start_precision, end, end_precision))
        return ranges

    with connect() as conn:
        parties: list[tuple[UUID, str, str, list[str], list[str], list[str], int | None, int | None, int]] = (
            conn.execute(
                """select p.id, po.iso_code, p.kind,
                      coalesce((select array_agg(n.text order by n.is_preferred desc, n.text) from name n
                                where n.entity_id = p.id and n.kind <> 'abbreviation'), '{}'),
                      coalesce((select array_agg(n.text order by n.text) from name n
                                where n.entity_id = p.id and n.kind = 'abbreviation'), '{}'),
                      coalesce((select array_agg(x.scheme || ':' || x.value) from external_id x
                                where x.entity_id = p.id and x.scheme <> 'wikidata'), '{}'),
                      s.first, s.last, s.options
               from party p
               join polity po on po.id = p.polity_id
               cross join lateral (
                 select min(extract(year from e.date_from))::int, max(extract(year from e.date_from))::int, count(*)::int
                 from contest_option o join contest k on k.id = o.contest_id join election e on e.id = k.election_id
                 where o.party_id = p.id
               ) s(first, last, options)
               where not exists (select from entity_merge m where m.entity_id = p.id)
               order by p.id"""
            ).fetchall()
        )
        others: defaultdict[UUID, set[str]] = defaultdict(set)
        for entity, text in conn.execute(
            """select n.entity_id, n.text from name n join party p on p.id = n.entity_id
               join changeset c on c.id = n.changeset_id where c.pipeline <> %s""",
            [SOURCE],
        ).fetchall():
            others[entity].add(text.casefold())
        english: dict[str, str] = {}
        for url in [_url(LABELS, _scope(country)) for country in COUNTRIES.values()] + [_extra(LABELS)]:
            for row in _rows(url):
                english.setdefault(row["item"].rsplit("/", 1)[1], row["name"])
        states: dict[str, str] = dict(
            conn.execute(
                """select po.iso_code, n.text from polity po
                   join name n on n.entity_id = po.id and n.is_preferred and n.lang = 'en'
                   where po.iso_code like 'US-%'"""
            ).fetchall()
        )
        painted: set[UUID] = {
            party
            for (party,) in conn.execute(
                """select distinct pc.party_id from party_color pc join changeset c on c.id = pc.changeset_id
                   where c.pipeline <> %s""",
                [SOURCE],
            ).fetchall()
        }
        taken: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'wikidata'").fetchall()
        )

        owned = {entity: item for item, entity in taken.items()}
        found: dict[UUID, tuple[str | None, str | None, int]] = {}
        unlinked: dict[UUID, str] = {}
        claims: dict[str, list[tuple[int, int, UUID]]] = defaultdict(list)
        for party, iso, kind, full, short, codes, first, last, options in parties:
            scope = iso.split("-")[0]
            if scope not in COUNTRIES:
                continue
            forced = next((name for name in full + short + codes if (iso, name) in MATCHES), None)
            inherited = next((INHERITED[iso, name] for name in full + short if (iso, name) in INHERITED), None)
            if forced is not None:
                item = MATCHES[iso, forced]
                found[party] = (item, inherited, options)
                if item is not None:
                    claims[item].append((1_000, options, party))
                elif party in owned:
                    unlinked[party] = owned[party]
                    del taken[owned[party]]
                continue
            if party in owned:
                found[party] = (owned[party], inherited, options)
                claims[owned[party]].append((1_000, options, party))
                continue
            scores: Counter[str] = Counter()
            for text in full + short:
                variants = {text, f"{text} party"}
                if text in full and kind in LINKED:
                    variants.add(re.split(SPLIT, text)[0])
                if iso in states:
                    variants |= {f"{states[iso]} {text}", f"{text} of {states[iso]}", f"{text} of {states[iso]} State"}
                hits: dict[str, int] = {}
                for variant in variants:
                    for item, weight in index[scope].get(_norm(variant), {}).items():
                        hits[item] = max(hits.get(item, 0), weight)
                for item, weight in hits.items():
                    scores[item] += weight * (1 if text in short else 2)
            ranked: list[tuple[int, int, str]] = []
            for item, score in scores.items():
                began, ended, links = spans.get(item, (None, None, 0))
                if (
                    first is None
                    or last is None
                    or ((began is None or began <= last + 1) and (ended is None or ended >= first - 1))
                ):
                    ranked.append((score, links, item))
            ranked.sort(reverse=True)
            if not ranked or ranked[0][0] < THRESHOLD:
                found[party] = (None, inherited, options)
                continue
            score, links, item = ranked[0]
            if len(ranked) > 1 and score == ranked[1][0] and links < 2 * ranked[1][1] + 2:
                found[party] = (None, inherited, options)
                continue
            if kind in LINKED:
                found[party] = (item, inherited, options)
                claims[item].append((score, options, party))
            else:
                found[party] = (None, inherited or item, options)

        linked: dict[UUID, str] = {}
        for item, claimants in claims.items():
            owner = taken.get(item)
            winner = max(claimants)[2]
            if owner is None or owner == winner:
                linked[winner] = item
            for _, _, party in claimants:
                if party not in linked and found[party][1] is None:
                    found[party] = (found[party][0], item, found[party][2])

        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Parties matched to Wikidata items by name within each country and period, with each item's colors "
                "for parties that have no color from another source and each item's political position, placed by "
                "hand for seat winners Wikidata leaves blank"
            ),
            polity=None,
        )
        wikidata = cs.source("Wikidata", TITLE)
        with HAND_COLORS.open(encoding="utf-8") as handle:
            sourced_colors = {row["party"]: row for row in csv.DictReader(handle, delimiter="\t")}
        with HAND_POSITIONS.open(encoding="utf-8") as handle:
            sourced_positions = {row["party"]: row for row in csv.DictReader(handle, delimiter="\t")}
        sourced_positions_source: dict[str, UUID] = {}
        for key, row in sourced_positions.items():
            sourced_positions_source[key] = cs.source(row["publisher"], row["title"])
            cs.add(
                "source",
                id=sourced_positions_source[key],
                kind=row["kind"],
                publisher=row["publisher"],
                title=row["title"],
                url=row["url"],
                license="Official public record, no license stated",
            )
        sourced_colors_source: dict[str, UUID] = {}
        for key, row in sourced_colors.items():
            sourced_colors_source[key] = cs.source(row["publisher"], row["title"])
            cs.add(
                "source",
                id=sourced_colors_source[key],
                kind=row["kind"],
                publisher=row["publisher"],
                title=row["title"],
                url=row["url"],
                license="Official public record, no license stated",
            )
        cs.add(
            "source",
            id=wikidata,
            kind="wikidata",
            publisher="Wikidata",
            title=TITLE,
            url="https://www.wikidata.org/wiki/Property:P465",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        positioned = cs.source("Wikidata", POSITIONED)
        cs.add(
            "source",
            id=positioned,
            kind="wikidata",
            publisher="Wikidata",
            title=POSITIONED,
            url="https://www.wikidata.org/wiki/Property:P1387",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        for url in _urls():
            cs.artifact(raw.cached(SOURCE, url), wikidata)
        for url in _placed():
            cs.artifact(raw.cached(SOURCE, url), positioned)
        report: Counter[tuple[str, str]] = Counter()
        shades: defaultdict[UUID, list[tuple[str, date | None, date | None]]] = defaultdict(list)
        for party, iso, kind, full, short, codes, first, last, options in parties:
            if party not in found:
                continue
            scope = "US-*" if iso.startswith("US-") else iso
            report[scope, "parties"] += 1
            report[scope, "options"] += options
            item, inherited, _ = found[party]
            if party in linked and taken.get(linked[party]) != party:
                cs.add("external_id", entity_id=party, scheme="wikidata", value=linked[party])
            label = english.get(linked[party]) if party in linked else None
            if label is not None and label.casefold() not in others[party]:
                cs.add(
                    "name",
                    entity_id=party,
                    text=label,
                    lang="en",
                    kind="english",
                    is_preferred=False,
                    valid_from_precision="day",
                    valid_to_precision="day",
                    source_id=wikidata,
                )
                report[scope, "named"] += 1
            if party in linked:
                report[scope, "linked"] += 1
            sourced = spectrum(linked.get(party)) or spectrum(inherited)
            hand_position = sourced_positions.get(str(party))
            span = (
                (int(hand_position["low"]), int(hand_position["high"]))
                if hand_position
                else sourced or next((PLACED[iso, name] for name in full + short if (iso, name) in PLACED), None)
            )
            if span is not None:
                cs.add(
                    "party_position",
                    party_id=party,
                    low=span[0],
                    high=span[1],
                    rank=None
                    if hand_position
                    else min((ORDER[iso].index(name) for name in full + short if name in ORDER.get(iso, [])), default=None),
                    source_id=sourced_positions_source[str(party)]
                    if hand_position
                    else None
                    if sourced is None
                    else positioned,
                )
                report[scope, "positioned"] += options
            if party in painted:
                report[scope, "painted"] += options
                continue
            if str(party) in sourced_colors:
                row = sourced_colors[str(party)]
                cs.add("party_color", party_id=party, color=row["color"], source_id=sourced_colors_source[str(party)])
                shades[party].append((row["color"], None, None))
                report[scope, "painted"] += options
                continue
            hand, country = (
                next(
                    (
                        PAINTED[key]
                        for name in full + short
                        for key in ((place, name, first), (place, name, None))
                        if key in PAINTED
                    ),
                    None,
                )
                for place in (iso, iso.split("-")[0])
            )
            ranges = (
                [(hand, None, "day", None, "day")]
                if hand
                else colors(linked.get(party))
                or colors(inherited)
                or ([(country, None, "day", None, "day")] if country else [])
            )
            for color, start, start_precision, end, end_precision in ranges:
                cs.add(
                    "party_color",
                    party_id=party,
                    color=f"#{color.lower()}",
                    valid_from=start,
                    valid_from_precision=start_precision,
                    valid_to=end,
                    valid_to_precision=end_precision,
                    source_id=None if hand else wikidata,
                )
                shades[party].append((f"#{color.lower()}", start, end))
            if ranges:
                report[scope, "painted"] += options
            elif options >= 50:
                print(f"no color: {iso} {(full + short)[0]} ({options} options, item {linked.get(party) or inherited})")
        for party, item in unlinked.items():
            cs.add(
                "external_id_drop",
                entity_id=party,
                scheme="wikidata",
                value=item,
                reason=f"Matched by hand: the party is not Wikidata's {item}",
            )
        for name_id, party, text in conn.execute(
            """select n.id, n.entity_id, n.text from name n join changeset c on c.id = n.changeset_id
               where n.entity_id = any(%s) and n.kind = 'english' and c.pipeline = %s""",
            [list(unlinked), SOURCE],
        ).fetchall():
            cs.add(
                "name_drop", name_id=name_id, entity_id=party, text=text, reason="Named after an unlinked Wikidata item"
            )
        for scope in sorted({scope for scope, _ in report}):
            print(
                f"{scope}: {report[scope, 'linked']} of {report[scope, 'parties']} parties linked, "
                f"{report[scope, 'painted']} of {report[scope, 'options']} options colored, "
                f"{report[scope, 'positioned']} positioned, {report[scope, 'named']} given an English name"
            )
        for party, color, start, end in conn.execute(
            """select pc.party_id, pc.color, pc.valid_from, pc.valid_to from party_color pc
               join changeset c on c.id = pc.changeset_id where c.pipeline <> %s""",
            [SOURCE],
        ).fetchall():
            shades[party].append((color, start, end))
        lab: dict[str, tuple[float, float, float]] = {}
        for color in {color for ranges in shades.values() for color, _, _ in ranges}:
            rgb = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
            r, g, b = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
            l_, m_, s_ = (
                (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3),
                (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3),
                (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3),
            )
            lab[color] = (
                min(0.86, max(0.52, 0.2104542553 * l_ + 0.793617785 * m_ - 0.0040720468 * s_)),
                1.9779984951 * l_ - 2.428592205 * m_ + 0.4505937099 * s_,
                0.0259040371 * l_ + 0.7827717662 * m_ - 0.808675766 * s_,
            )
        labels: dict[UUID, str] = {party: (full + short)[0] for party, _, _, full, short, *_ in parties}
        seated: defaultdict[tuple[str, date], dict[UUID, str | None]] = defaultdict(dict)
        for iso, day, party, principal in conn.execute(
            """select distinct coalesce(po.iso_code, (select n.text from name n where n.entity_id = po.id
                                                 order by n.is_preferred desc limit 1)),
                      e.date_from, coalesce(m.into_id, o.party_id),
                      (select coalesce(tm.into_id, r.to_party_id) from party_relation r
                       left join entity_merge fm on fm.entity_id = r.from_party_id
                       left join entity_merge tm on tm.entity_id = r.to_party_id
                       where r.kind = 'list_vehicle_for' and r.election_id = e.id
                         and coalesce(fm.into_id, r.from_party_id) = coalesce(m.into_id, o.party_id) limit 1)
               from outcome x join contest_option o on o.id = x.option_id
               join contest k on k.id = o.contest_id join election e on e.id = k.election_id
               join polity po on po.id = e.polity_id
               left join entity_merge m on m.entity_id = o.party_id
               where x.kind::text in ('elected', 'won') and o.party_id is not null and e.date_from is not null"""
        ).fetchall():
            shade = next(
                (
                    color
                    for owner in (party, principal)
                    for color, start, end in shades.get(owner, [])
                    if (start is None or start <= day) and (end is None or day < end)
                ),
                None,
            )
            seated[iso, day][party] = shade
        for iso, day in sorted(seated):
            winners = sorted(seated[iso, day].items(), key=lambda item: labels.get(item[0], ""))
            for place, (one, color) in enumerate(winners):
                for other, shade in winners[place + 1 :]:
                    if color and shade and (gap := math.dist(lab[color], lab[shade])) < 0.1:
                        print(f"close: {iso} {day} {labels.get(one)} {color} ~ {labels.get(other)} {shade} ({gap:.3f})")
            if bare := [labels.get(party, str(party)) for party, color in winners if color is None]:
                print(f"no color: {iso} {day} seat winners {', '.join(bare)}")
        return cs.write()
