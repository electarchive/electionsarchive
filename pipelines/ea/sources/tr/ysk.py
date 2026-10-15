import csv
import difflib
import io
import json
import re
import struct
import subprocess
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from itertools import pairwise, repeat
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import numpy as np
import polars as pl
from PIL import Image

from ea import raw, scan
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "tr.ysk"
VERSION = "16"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
SCANS = PARSED / "scans"
OCR = PARSED / "ocr"
GAZETTE = "https://www.resmigazete.gov.tr/eskiler"
PRESIDENT = {1: f"{GAZETTE}/2023/05/20230519M1-1.pdf", 2: f"{GAZETTE}/2023/06/20230601M1-1.pdf"}
PARLIAMENT = f"{GAZETTE}/2023/05/20230530M1-1.pdf"
LISTS = f"{GAZETTE}/2023/04/20230419M1-1.pdf"
LISTS_2018 = "https://www.ysk.gov.tr/doc/dosyalar/docs/24Haziran2018/2018CBMV-MVKesinAdayListesi.pdf"
WITHDREW_2018 = "https://www.hurriyet.com.tr/gundem/iyi-parti-edirne-1inci-sira-adayi-istifa-etti-40855957"
LISTS_2015 = "https://www.ysk.gov.tr/doc/dosyalar/docs/Milletvekili/1Kasim2015/2015MVES-KesinAdayListesi.pdf"
LISTS_JUNE_2015 = "https://www.ysk.gov.tr/doc/dosyalar/docs/Milletvekili/7Haziran2015/2015MV-KesinAdayListesi.pdf"
LISTS_2011 = "https://www.ysk.gov.tr/doc/dosyalar/docs/2011MilletvekiliSecimi/KesinAdayListesi2011.pdf"
WITHDREW_2011 = "https://www.memurlar.net/haber/196377/"
WITHDREW_EARLY_2011 = "https://www.yenisafak.com/gundem/kaset-skandali-mhpde-9-yoneticiyi-goturdu-320288"
WITHDRAWN = {
    (LISTS_2018, "22", "İYİ Parti", 1): (WITHDREW_2018, "June 2, 2018"),
    (LISTS_2011, "01", "Milliyetçi Hareket Partisi", 1): (WITHDREW_EARLY_2011, None),
    (LISTS_2011, "06-2", "Milliyetçi Hareket Partisi", 1): (WITHDREW_2011, "May 21, 2011"),
    (LISTS_2011, "06-2", "Milliyetçi Hareket Partisi", 2): (WITHDREW_2011, "May 21, 2011"),
    (LISTS_2011, "34-1", "Milliyetçi Hareket Partisi", 2): (WITHDREW_EARLY_2011, None),
    (LISTS_2011, "34-2", "Milliyetçi Hareket Partisi", 2): (WITHDREW_2011, "May 21, 2011"),
    (LISTS_2011, "34-3", "Milliyetçi Hareket Partisi", 3): (WITHDREW_2011, "May 21, 2011"),
    (LISTS_2011, "55", "Milliyetçi Hareket Partisi", 1): (WITHDREW_2011, "May 21, 2011"),
    (LISTS_2011, "66", "Milliyetçi Hareket Partisi", 1): (WITHDREW_2011, "May 21, 2011"),
}
PRESIDENT_2018 = f"{GAZETTE}/2018/07/20180704M1-1.pdf"
PARLIAMENT_2018 = f"{GAZETTE}/2018/07/20180704M1-2.pdf"
PARLIAMENT_2015 = f"{GAZETTE}/2015/11/20151112M1-1.pdf"
PARLIAMENT_JUNE_2015 = f"{GAZETTE}/2015/06/20150618M1-1.pdf"
PARLIAMENT_2011 = f"{GAZETTE}/2011/06/20110623-4.pdf"
PARLIAMENT_2007 = f"{GAZETTE}/2007/07/20070730M1-2.doc"
ISTANBUL = f"{GAZETTE}/2019/07/20190711-5.pdf"
REFERENDUM_2017 = f"{GAZETTE}/2017/04/20170427M1-1.pdf"
REFERENDUM_2010 = f"{GAZETTE}/2010/09/ysk%20ekler.pdf"
COURT_2010 = f"{GAZETTE}/2010/08/20100801M1-1.htm"
REFERENDUM_2007 = f"{GAZETTE}/2007/10/20071031-16-2.rtf"
REFERENDUM_2007_CUSTOMS = f"{GAZETTE}/2007/10/20071031-16-3.rtf"
PRESIDENT_2014 = f"{GAZETTE}/2014/08/20140828M1.pdf"
REGISTERED, VOTERS, VALID, INVALID, VOTES, APPORTIONED = 2, 14, 4, 5, 1, 19
DERIVED, DIGITIZED = 1, 8
PUBLISHER = "Yüksek Seçim Kurulu"
ROUND_DAYS = {1: date(2023, 5, 14), 2: date(2023, 5, 28)}
INAUGURATED = date(2023, 6, 3)
DAY_2018 = date(2018, 6, 24)
DAY_2017 = date(2017, 4, 16)
DAY_2010 = date(2010, 9, 12)
DAY_2014 = date(2014, 8, 10)
DAY_2015 = date(2015, 11, 1)
DAY_JUNE_2015 = date(2015, 6, 7)
DAY_2011 = date(2011, 6, 12)
LIST_URLS = {DAY_2018: LISTS_2018, DAY_2015: LISTS_2015, DAY_JUNE_2015: LISTS_JUNE_2015, DAY_2011: LISTS_2011}
DAY_2007 = date(2007, 7, 22)
DAY_REFERENDUM_2007 = date(2007, 10, 21)
INAUGURATED_2014 = date(2014, 8, 28)
INAUGURATED_2018 = date(2018, 7, 9)
MP_START = date(2023, 6, 2)
MP_START_2018 = date(2018, 7, 7)
ATALAY = ("ŞERAFETTİN CAN ATALAY", date(2024, 1, 30))
RERUN = date(2019, 6, 23)
MARCH = date(2019, 3, 31)
MAYORALTY, LOCAL_ELECTIONS, RERUN_ELECTION, ARREST, IMAMOGLU = (
    "Q62925885",
    "Q28219941",
    "Q63639179",
    "Q133405670",
    "Q59313241",
)
MAYORS_QUERY = f"""SELECT ?statement ?person ?label ?start ?end ?cause ?elected WHERE {{
  ?person p:P39 ?statement . ?statement ps:P39 wd:{MAYORALTY} ; pq:P580 ?start .
  FILTER(?start >= "2019-01-01T00:00:00Z"^^xsd:dateTime)
  OPTIONAL {{ ?statement pq:P582 ?end }} OPTIONAL {{ ?statement pq:P1534 ?cause }} OPTIONAL {{ ?statement pq:P2715 ?elected }}
  ?person rdfs:label ?label FILTER(lang(?label) = "tr")
}} ORDER BY ?start"""
MAYORS = "https://query.wikidata.org/sparql?" + urlencode({"query": MAYORS_QUERY, "format": "json"})
MEMBERS, TERM_2018, TERM_2015, TERM_JUNE_2015, TERM_2011 = (
    "Q21030356",
    "Q55357134",
    "Q20878969",
    "Q20054638",
    "Q6577342",
)
MPS_2018_QUERY = f"""SELECT ?statement ?person ?label ?districtLabel ?group ?start ?end ?cause ?death WHERE {{
  ?person p:P39 ?statement . ?statement ps:P39 wd:{MEMBERS} ; pq:P2937 wd:{TERM_2018} .
  OPTIONAL {{ ?statement pq:P768 ?district . ?district rdfs:label ?districtLabel FILTER(lang(?districtLabel) = "tr") }}
  OPTIONAL {{ ?statement pq:P4100 ?group }} OPTIONAL {{ ?statement pq:P580 ?start }} OPTIONAL {{ ?statement pq:P582 ?end }} OPTIONAL {{ ?statement pq:P1534 ?cause }}
  OPTIONAL {{ ?person wdt:P570 ?death }}
  ?person rdfs:label ?label FILTER(lang(?label) = "tr")
}} ORDER BY ?person ?start"""
MPS_2018 = "https://query.wikidata.org/sparql?" + urlencode({"query": MPS_2018_QUERY, "format": "json"})
MPS_2015 = "https://query.wikidata.org/sparql?" + urlencode(
    {"query": MPS_2018_QUERY.replace(TERM_2018, TERM_2015), "format": "json"}
)
MPS_JUNE_2015 = "https://query.wikidata.org/sparql?" + urlencode(
    {"query": MPS_2018_QUERY.replace(TERM_2018, TERM_JUNE_2015), "format": "json"}
)
MPS_2011 = "https://query.wikidata.org/sparql?" + urlencode(
    {"query": MPS_2018_QUERY.replace(TERM_2018, TERM_2011), "format": "json"}
)
OZONDER = "Q6085420"
OZONDER_ITEM = f"https://www.wikidata.org/wiki/Special:EntityData/{OZONDER}.json"
DEATH_MARKER = "---------ÖLÜM------------"
OZONDER_PRESS = "https://www.sondakika.com/guncel/haber-mazbatasini-alamadi/"
OZONDER_SOURCE = (
    "Wikidata item for Mehmet Cihat Özönder, with his name and date of death. Used to name the MHP member whom the "
    "2007 gazette lists only as a death marker."
)
OZONDER_PRESS_SOURCE = (
    "Son Dakika report of July 27, 2007, on the death of Mehmet Cihat Özönder, the MHP member elected in İstanbul's "
    "3rd district, in a traffic accident the day before."
)
OZONDER_CAVEAT = (
    "The official gazette prints ÖLÜM (death) in place of one MHP member's name. The member was Mehmet Cihat "
    "Özönder. He died in a traffic accident on July 26, 2007, four days after the election, while traveling to "
    "collect his certificate of election. The gazette was published on July 30. His name comes from Wikidata, and "
    "the account of his death comes from a Son Dakika report of July 27, 2007."
)
WITHDREW_SOURCES = {
    WITHDREW_2018: (
        "Hürriyet",
        "İYİ Parti Edirne 1'inci sıra adayı istifa etti",
        (
            "DHA report published by Hürriyet on June 2, 2018, regarding the withdrawal of İmren Çakırlar, first on the İYİ "
            "Parti list in Edirne, and Orhan Çakırlar moving up to first place."
        ),
    ),
    WITHDREW_2011: (
        "Memurlar.net",
        "MHP'de kaset depremi: 6 yönetici birden istifa etti!",
        (
            "Anadolu Ajansı report of May 21, 2011, republished by Memurlar.net, regarding the withdrawal of six MHP "
            "officials: Deniz Bölükbaşı, Cihan Paçacı, Osman Çakır, Mehmet Ekici, Ümit Şafak, and Mehmet Taytak."
        ),
    ),
    WITHDREW_EARLY_2011: (
        "Yeni Şafak",
        "Kaset skandalı MHP'de 9 yöneticiyi götürdü",
        (
            "Yeni Şafak report of May 21, 2011, regarding MHP officials who withdrew their candidacies, including Recai "
            "Yıldırım and Bülent Didinmez, who had withdrawn earlier."
        ),
    ),
}
WITHDREW_NOTE = "Withdrew from the race on {date} after final lists were set."
WITHDREW_UNDATED_NOTE = "Withdrew from the race after final lists were set."
WITHDREW_CAVEAT = (
    "{name}, number {position} on the {party} list, withdrew from the race after final lists were set. The name "
    "remained on the printed list, and the party's seats went to the next candidates on the list."
)
EDIRNE_2018_CAVEAT = (
    "İmren Çakırlar was first on the İYİ Parti list, replacing Mehmet Akalın, whose candidacy YSK struck off because "
    "Mehmet Akalın had not resigned from public office in time. İmren Çakırlar withdrew on June 2, 2018, after the "
    "final lists were published, so the name remained on the printed list. İYİ Parti won one seat in this district, "
    "which went to the second candidate on the list, Orhan Çakırlar."
)
MPS_2018_SOURCE = (
    "Members of the 27th term of the Grand National Assembly, with the electoral district each represented and the "
    "dates each served, as recorded on Wikidata. Used for Kahramanmaraş's eight members, whom the gazette leaves out, "
    "and to check the names read from the gazette."
)
MPS_2015_SOURCE = (
    "Members of the 26th term of the Grand National Assembly, with the electoral district each represented, as "
    "recorded on Wikidata. Used to link the members elected in November 2015 to their Wikidata items and to check the "
    "names read from the gazette."
)
MPS_JUNE_2015_SOURCE = (
    "Members of the 25th term of the Grand National Assembly, with the electoral district each represented, as "
    "recorded on Wikidata. Used to link the members elected in June 2015 to their Wikidata items and to check the "
    "names read from the gazette."
)
MPS_2011_SOURCE = (
    "Members of the 24th term of the Grand National Assembly, with the electoral district each represented, as "
    "recorded on Wikidata. We use this list to link the members elected in 2011 to their Wikidata items and to check "
    "the names read from the gazette."
)
READ_PARLIAMENT = {
    (DAY_2011, "06-2", VOTERS): 1460751,
    (DAY_2011, "35-2", VOTERS): 1319211,
    (DAY_2018, "44", REGISTERED): 555931,
    (DAY_JUNE_2015, "31", REGISTERED): 987567,
    (DAY_JUNE_2015, "74", REGISTERED): 143001,
    (DAY_2015, "01", VALID): 1255729,
    (DAY_2015, "06-1", REGISTERED): 2099001,
    (DAY_2015, "14", VOTERS): 190041,
    (DAY_2015, "18", REGISTERED): 129979,
    (DAY_2015, "25", REGISTERED): 483561,
    (DAY_2015, "26", REGISTERED): 616751,
    (DAY_2015, "26", VOTERS): 546611,
    (DAY_2015, "30", REGISTERED): 157379,
    (DAY_2015, "30", VOTERS): 139055,
    (DAY_2015, "34-3", VOTERS): 3106713,
    (DAY_2015, "38", REGISTERED): 908971,
    (DAY_2015, "48", REGISTERED): 673642,
    (DAY_2015, "48", VOTERS): 584888,
    (DAY_2015, "55", REGISTERED): 924381,
    (DAY_2015, "67", REGISTERED): 446210,
    (DAY_2015, "78", VOTERS): 146784,
}
NAMES_PARLIAMENT = {
    (DAY_2011, "33", 10): "ALİ ÖZ",
    (DAY_2011, "34-1", 29): "DURMUŞALİ TORLAK",
    (DAY_2011, "34-2", 8): "İSMAİL SAFİ",
    (DAY_2011, "34-2", 17): "BİHLUN TAMAYLIGİL",
    (DAY_2011, "34-3", 24): "FERİT MEVLÜT ASLANOĞLU",
    (DAY_2011, "34-3", 27): "İHSAN BARUTÇU",
    (DAY_2015, "07", 1): "NİYAZİ NEFİ KARA",
    (DAY_2015, "07", 10): "HÜSEYİN SAMANİ",
    (DAY_2015, "13", 2): "MAHMUT CELADET GAYDALI",
    (DAY_2015, "34-1", 21): "BERAT ALBAYRAK",
    (DAY_2015, "34-2", 25): "AHMET HAMDİ ÇAMLI",
    (DAY_2015, "60", 1): "KADİM DURMAZ",
    (DAY_JUNE_2015, "01", 8): "İBRAHİM ÖZDİŞ",
    (DAY_JUNE_2015, "06-1", 6): "NECATİ YILMAZ",
    (DAY_JUNE_2015, "06-1", 14): "ALİ İHSAN ARSLAN",
    (DAY_JUNE_2015, "13", 1): "MİZGİN IRGAT",
    (DAY_JUNE_2015, "16", 13): "HÜSEYİN ŞAHİN",
    (DAY_JUNE_2015, "17", 1): "İBRAHİM KÜRŞAT TUNA",
    (DAY_JUNE_2015, "21", 7): "ZİYA PİR",
    (DAY_JUNE_2015, "25", 6): "İBRAHİM AYDEMİR",
    (DAY_JUNE_2015, "27", 8): "ABDULHAMİT GÜL",
    (DAY_JUNE_2015, "34-3", 11): "İLHAN CİHANER",
    (DAY_JUNE_2015, "38", 2): "ALİ KİLCİ",
    (DAY_JUNE_2015, "65", 7): "REMZİ ÖZGÖKÇE",
}
NAMES_LISTS = {
    ("01", "MP", 12, "forename"): "HAKKI",
    ("01", "MP", 14, "surname"): "ÇİNAR",
    ("01", "TKH", 11, "surname"): "KIRHAN",
    ("01", "Zafer", 8, "surname"): "ÇİTCİ",
    ("02", "Vatan", 4, "surname"): "KARĞİLİ",
    ("03", "CHP", 6, "forename"): "ANIL HALİS",
    ("04", "AP", 2, "forename"): "ABDULMECİT",
    ("04", "SOL Parti", 4, "forename"): "REMZİ",
    ("05", "Yenilik", 3, "surname"): "YIL",
    ("06-1", "Bağımsız", 4, "surname"): "İLİ",
    ("06-1", "CHP", 1, "forename"): "GAMZE",
    ("06-1", "Milli Yol", 2, "forename"): "FIRAT",
    ("06-1", "TKH", 1, "surname"): "KILÇER",
    ("06-2", "HAK-PAR", 3, "surname"): "ÇİFCİ",
    ("06-2", "Zafer", 11, "surname"): "ÖNALAN",
    ("06-3", "YSP", 12, "surname"): "OTİ",
    ("07", "ABP", 8, "surname"): "İRCİ",
    ("07", "SOL Parti", 9, "forename"): "REMZİYE GÖKÇE",
    ("07", "SOL Parti", 16, "forename"): "DURALİ",
    ("07", "TKH", 3, "forename"): "HAYATİ",
    ("07", "TKP", 1, "forename"): "NAZLI ECE",
    ("07", "TKP", 13, "forename"): "DURALİ",
    ("07", "Vatan", 9, "surname"): "YİĞİT",
    ("07", "YSP", 9, "forename"): "FIRAT",
    ("09", "ANAP", 7, "surname"): "ÖNGAY",
    ("09", "YSP", 3, "surname"): "ÇAKMAKÇİ",
    ("10", "Genç Parti", 1, "surname"): "ÖZULUS",
    ("10", "MHP", 3, "surname"): "ÖZIŞIKLIOĞLU",
    ("10", "MHP", 5, "surname"): "SIKICIKOĞLU",
    ("10", "TKH", 5, "surname"): "AÇİM",
    ("12", "İYİ Parti", 2, "surname"): "TANİN",
    ("12", "İYİ Parti", 3, "surname"): "ARPA",
    ("13", "AP", 2, "surname"): "NAZLİ",
    ("14", "Yenilik", 3, "forename"): "SEMAHAT",
    ("16-1", "AP", 2, "surname"): "SAYGILIER",
    ("16-1", "Milli Yol", 1, "surname"): "ÇİFCİ",
    ("16-2", "BBP", 7, "forename"): "SONER",
    ("16-2", "HAK-PAR", 10, "surname"): "DEMİR",
    ("16-2", "İYİ Parti", 2, "surname"): "ALAGÖZ",
    ("17", "ABP", 1, "surname"): "PİLİÇÇİ",
    ("17", "AP", 1, "surname"): "YILDEMİR",
    ("17", "AP", 2, "forename"): "GÖKHAN",
    ("17", "MHP", 1, "forename"): "SITKI",
    ("17", "TKH", 2, "surname"): "DEMİR",
    ("19", "CHP", 4, "surname"): "GÜHER",
    ("19", "Yenilik", 3, "surname"): "GÜRLAN",
    ("20", "GBP", 7, "forename"): "GEYLANİ",
    ("20", "YSP", 7, "forename"): "HAVVA",
    ("21", "MHP", 9, "forename"): "ABDURRAHMAN MUSTAFA UĞURLU",
    ("21", "YRP", 10, "surname"): "DEMİR",
    ("22", "Milli Yol", 2, "surname"): "İNĞ",
    ("22", "TİP", 1, "surname"): "MUHÇU",
    ("25", "SOL Parti", 5, "forename"): "ÜNAL",
    ("27", "AK Parti", 1, "forename"): "ABDULHAMİT",
    ("27", "Milli Yol", 7, "surname"): "ORHAN",
    ("30", "Yenilik", 1, "surname"): "İLCİ",
    ("31", "Memleket", 6, "surname"): "ÖKSÜZAŞIKI",
    ("32", "HKP", 1, "surname"): "GÜLAY",
    ("32", "Vatan", 1, "forename"): "MELAHAT",
    ("32", "Vatan", 3, "surname"): "CANITEZ",
    ("33", "AK Parti", 8, "forename"): "HAKKI",
    ("33", "Vatan", 4, "surname"): "ÇAMLI",
    ("33", "Zafer", 7, "forename"): "KAZIM CİHAT",
    ("34-1", "ABP", 14, "surname"): "DALĞIÇ",
    ("34-1", "AK Parti", 23, "surname"): "AHIAKIN",
    ("34-1", "GBP", 31, "surname"): "ÇİFÇİ",
    ("34-1", "TKH", 11, "surname"): "İROÇ",
    ("34-1", "TKH", 34, "forename"): "HAYATİ",
    ("34-1", "TİP", 21, "surname"): "PINAR",
    ("34-1", "YRP", 23, "surname"): "YİĞİT",
    ("34-2", "ABP", 24, "surname"): "DEMİR",
    ("34-2", "Bağımsız", 6, "surname"): "KONANCİ",
    ("34-2", "Genç Parti", 5, "forename"): "ÖMER IŞIK",
    ("34-2", "HAK-PAR", 19, "surname"): "ERKIRAN",
    ("34-2", "Memleket", 3, "surname"): "HACIGÜZELLER",
    ("34-2", "TKH", 1, "surname"): "KILÇER",
    ("34-2", "TİP", 3, "surname"): "ÖZADIKTİ",
    ("34-2", "Vatan", 26, "surname"): "YİĞİT",
    ("34-2", "Vatan", 27, "surname"): "ÇİRCİ",
    ("34-2", "YSP", 13, "surname"): "SAYMADİ",
    ("34-2", "YSP", 14, "surname"): "KIZILDERE",
    ("34-2", "Zafer", 24, "forename"): "YİĞİT CAN",
    ("34-2", "İYİ Parti", 25, "forename"): "AFFAN MAHMUT",
    ("34-3", "BBP", 12, "surname"): "ÖZCAN",
    ("34-3", "BBP", 19, "forename"): "CELAL RIDVAN",
    ("34-3", "CHP", 17, "forename"): "HÜSEYİN REMZİ",
    ("34-3", "Genç Parti", 1, "forename"): "REHA FERHUN",
    ("34-3", "HAK-PAR", 35, "surname"): "ÇİFTÇİ",
    ("34-3", "MHP", 27, "forename"): "BEKİR SITKI",
    ("34-3", "Milli Yol", 32, "surname"): "ÖZ",
    ("34-3", "SOL Parti", 19, "forename"): "İLAYDA",
    ("34-3", "YRP", 14, "surname"): "BAHÇİVAN",
    ("35-1", "ABP", 2, "forename"): "TİJEN",
    ("35-1", "GBP", 1, "surname"): "ÖNALAN",
    ("35-1", "HAK-PAR", 4, "surname"): "TOPCİ",
    ("35-1", "MHP", 8, "surname"): "KIZILÖRENLİ",
    ("35-1", "MP", 3, "surname"): "PINAR",
    ("35-1", "YSP", 1, "forename"): "BURCUGÜL",
    ("35-2", "ABP", 10, "surname"): "YİĞİTOĞLU",
    ("35-2", "Vatan", 14, "surname"): "İZGİ",
    ("36", "ABP", 3, "surname"): "YİĞİT",
    ("36", "SOL Parti", 2, "surname"): "KARABAG",
    ("36", "SOL Parti", 3, "surname"): "KARABAG",
    ("38", "Bağımsız", 4, "surname"): "ÇAKMAKÇİ",
    ("38", "MP", 1, "surname"): "İDİZ",
    ("41", "CHP", 12, "forename"): "BELKIZA",
    ("41", "Genç Parti", 4, "forename"): "SEBAHATTİN",
    ("41", "Yenilik", 1, "surname"): "YILDIZTEKİN",
    ("41", "Yenilik", 5, "surname"): "DEMİRKIYIK",
    ("41", "Yenilik", 9, "forename"): "BATUHAN",
    ("42", "ANAP", 1, "surname"): "İZGİ",
    ("42", "YRP", 13, "surname"): "CAMGÖZ",
    ("43", "Genç Parti", 1, "surname"): "GÜN",
    ("43", "Genç Parti", 2, "surname"): "KÜÇÜKSATICI",
    ("43", "YSP", 1, "forename"): "AYŞEGÜL",
    ("44", "Zafer", 4, "surname"): "AKÇİL",
    ("45", "MP", 2, "surname"): "BINARCI",
    ("45", "Yenilik", 4, "surname"): "KALAYCI",
    ("46", "GBP", 6, "forename"): "HALİL",
    ("46", "GBP", 8, "surname"): "KAYĞUSUZ",
    ("46", "YSP", 8, "surname"): "YILDIZ",
    ("47", "BBP", 3, "surname"): "YALÇİN",
    ("47", "GBP", 2, "surname"): "ÇİFÇİ",
    ("47", "GBP", 5, "surname"): "ÇİFÇİ",
    ("47", "MP", 1, "forename"): "ABDULAZİZ",
    ("48", "SOL Parti", 2, "surname"): "AKGÜL AKALAN",
    ("49", "AK Parti", 3, "surname"): "İŞIK",
    ("49", "YRP", 1, "forename"): "HAKI",
    ("49", "YSP", 3, "forename"): "SERHAT",
    ("50", "MHP", 2, "surname"): "ÇİFTCİ",
    ("52", "Vatan", 4, "forename"): "İSMAİL",
    ("52", "Vatan", 4, "surname"): "KÜÇÜKKARAHAN",
    ("52", "Vatan", 5, "surname"): "SÜMBÜL",
    ("54", "Vatan", 5, "surname"): "DİV",
    ("54", "Yenilik", 7, "surname"): "İRĞA",
    ("55", "ANAP", 3, "surname"): "BAKIR",
    ("55", "HKP", 5, "surname"): "GÜRTAN",
    ("56", "YRP", 2, "forename"): "ATAULLAH",
    ("58", "MP", 3, "surname"): "ÇAKIRELİ",
    ("59", "ABP", 1, "surname"): "ÇİL",
    ("59", "Vatan", 2, "surname"): "İÇÖZ",
    ("59", "Vatan", 5, "surname"): "İNCİ",
    ("59", "YSP", 3, "forename"): "GAMZE",
    ("61", "Memleket", 1, "surname"): "ARMUTCI",
    ("61", "Zafer", 2, "forename"): "HAVVA",
    ("63", "HKP", 4, "forename"): "FIRAT",
    ("63", "MHP", 5, "surname"): "ARPACİ",
    ("63", "Memleket", 7, "surname"): "ÇİFTÇİ",
    ("63", "SOL Parti", 10, "surname"): "ATICI",
    ("63", "TKH", 7, "surname"): "ÇAĞLİ",
    ("63", "YRP", 13, "surname"): "GENĞEÇ YAŞAR",
    ("63", "YSP", 13, "forename"): "MIZGİN",
    ("64", "BBP", 1, "forename"): "REMZİ",
    ("65", "YSP", 5, "surname"): "VARLİ",
    ("67", "Genç Parti", 2, "forename"): "BATUHAN",
    ("67", "İYİ Parti", 4, "forename"): "YİĞİTCAN",
    ("70", "Memleket", 2, "surname"): "YİĞİTOĞLU",
    ("73", "İYİ Parti", 2, "surname"): "İLÇİ",
    ("76", "ABP", 1, "surname"): "YILDIZ",
    ("76", "AK Parti", 1, "surname"): "ALAGÖZ",
    ("76", "Vatan", 2, "surname"): "TAŞCİ",
    ("76", "Yenilik", 2, "surname"): "ÇİFCİ",
    ("77", "HKP", 3, "surname"): "ÇINĞI OLKUN",
    ("77", "Milli Yol", 1, "forename"): "FIRAT",
    ("77", "Milli Yol", 2, "surname"): "YİLDIRIM",
    ("78", "AK Parti", 2, "surname"): "KESKİNKILIÇ",
    ("78", "MHP", 2, "surname"): "ORHAN",
    ("79", "SOL Parti", 1, "surname"): "KIZILER",
    ("80", "ABP", 4, "surname"): "İRCİ",
    ("80", "TKP", 3, "forename"): "SONER",
}
ITEMS_PARLIAMENT = {(DAY_2015, "43", 4): "Q106644100"}
VOTES_PARLIAMENT = {(DAY_2011, "30", "Millet Partisi"): 41}
TURNOUT_PARLIAMENT = {
    (DAY_2011, "13"): 84.86,
    (DAY_2011, "15"): 91.52,
    (DAY_2011, "64"): 91.55,
    (DAY_JUNE_2015, "65"): 84.39,
    (DAY_JUNE_2015, "75"): 83.88,
    (DAY_2015, "04"): 75.25,
    (DAY_2015, "30"): 88.36,
    (DAY_2015, "34-1"): 88.93,
    (DAY_2015, "48"): 86.82,
    (DAY_2015, "55"): 86.51,
    (DAY_2015, "61"): 85.21,
    (DAY_2015, "67"): 86.01,
    (DAY_2015, "68"): 84.47,
    (DAY_2015, "76"): 82.04,
    (DAY_2015, "78"): 87.11,
    (DAY_2015, "81"): 89.73,
}
READ_2017 = {
    ("03", REGISTERED): 500722,
    ("06", REGISTERED): 3853894,
    ("51", REGISTERED): 235896,
    ("65", INVALID): 15038,
}
ANSWERS_2017 = ("Evet", "Hayır")
DOMESTIC_2017 = [55319222, 48374576, 47528949, 845627, 24325633, 23203316]
POOL_2017 = {
    "abroad": (4, 8, [2972676, 1325682, 1309821, 15861, 778833, 530988]),
    "customs": (8, 9, [None, 98597, 97834, 763, 52997, 44837]),
}
NATIONAL_2017 = [58291898, 49798855, 48936604, 862251, 25157463, 23779141]
READ_2010 = {("26", REGISTERED): 570044}
DOMESTIC_2010 = [49495493, 38172800, 37449300, 723500, 21667427, 15781873]
POOL_2010 = {"customs": (8, 5, [2556335, 196299, 194737, 1562, 119817, 74920])}
DOMESTIC_REFERENDUM_2007 = [42665149, 28794216, 28142781, 651435, 19403987, 8738794]
NATIONAL_REFERENDUM_2007 = [42690252, 28819319, 28167661, 651658, 19422714, 8744947]
NATIONAL_2010 = [52051828, 38369099, 37644037, 725062, 21787244, 15856793]
MEMBERS_2018 = {
    "MİHRİMAH BELMA SATIR": "Q20472424",
    "ŞİRİN ÜNAL": "Q65499589",
    "ZEHRA TAŞKESENLİOĞLU": "Q65499843",
}
GROUPS_2018 = {
    "Q19077": "Adalet ve Kalkınma Partisi",
    "Q251077": "Milliyetçi Hareket Partisi",
    "Q15123187": "Halkların Demokratik Partisi",
    "Q19079": "Cumhuriyet Halk Partisi",
    "Q42313794": "İYİ Parti",
}
ENDINGS_2018 = {
    "Q51212178": "disqualified",
    "Q106224801": "disqualified",
    "Q106307091": "removed",
    "Q2916183": "removed",
    "Q5247364": "died",
}
MAYORS_SOURCE = (
    "Mayors of İstanbul since 2019, with start and end dates, the election each mayor won, and the cause of each early "
    "end, as recorded on Wikidata."
)
MARCH_NOTE = (
    "The Supreme Election Council (YSK) annulled this election on May 6, 2019, and ordered a rerun, held on June 23, "
    "2019. Ekrem İmamoğlu took office on April 17, 2019, and left on the day of the annulment."
)
COURT_2010_TITLE = "Anayasa Mahkemesinin 7/7/2010 tarihli ve E.: 2010/49, K.: 2010/87 sayılı Kararı"
COURT_2010_SOURCE = (
    "Constitutional Court decision E.2010/49, K.2010/87, July 7, 2010, on the challenge to Law 5982. Published in "
    "Resmî Gazete 27659 (Mükerrer), August 1, 2010. Section VI (Sonuç) lists the annulled provisions and the rejected "
    "challenges."
)
COURT_2010_NOTE = (
    "Before the vote, the Constitutional Court partly annulled Law 5982 (decision E.2010/49, K.2010/87 of July 7, 2010, "
    "published in Resmî Gazete 27659 (Mükerrer) on August 1, 2010). The court struck out the phrases that limited each "
    "voter to one candidate when judges, prosecutors, and university bodies nominate members of the Constitutional "
    "Court and elect members of the Supreme Board of Judges and Prosecutors. It also struck out the phrases that let "
    "the president appoint economics and political science academics and senior civil servants to that board. It "
    "rejected every other challenge to the law."
)
SUSPENDED = "Arrested and suspended from office by the Ministry of the Interior."
LICENSE = (
    "No license stated. Laws, regulations, and court decisions that have been officially published may be freely "
    "reproduced and used (Article 31 of Law 5846 on Intellectual and Artistic Works)."
)
DECISIONS = {
    PRESIDENT_2014: (
        "Karar no. 3719: Cumhurbaşkanı Seçimi kesin sonuçları",
        (
            "YSK decision 3719, published in Resmî Gazete 29102 (Mükerrer) on August 28, 2014: final results of the "
            "August 10, 2014 presidential election, the first in which voters elected the president directly. Pages 3–5 "
            "give the domestic, abroad, and customs-gate results and their combined total. The customs-gate table gives "
            "no registered voters. The tables are scanned images, so every count was digitized and carries a page and "
            "bounding-box locator."
        ),
    ),
    REFERENDUM_2010: (
        "Karar no. 846: 12 Eylül 2010 Anayasa Değişikliği Halkoylaması kesin sonuçları",
        (
            "YSK decision 846, published in Resmî Gazete 27708 on September 23, 2010, gives the final results of the "
            "September 12, 2010 constitutional referendum by province, with a separate table for votes cast at customs "
            "gates. Votes cast in prisons are counted in their provinces. The customs table gives the number of voters "
            "registered abroad, who in 2010 could vote only at customs gates, so we store it as the registered voters "
            "for customs gates. The tables are scanned images, so we digitized every count and recorded a page and "
            "bounding-box locator for each one."
        ),
    ),
    REFERENDUM_2007: (
        "Karar no. 873: 21 Ekim 2007 Anayasa Değişikliği Halkoylaması sonuçları",
        (
            "YSK decision 873 (October 30, 2007), published in Resmî Gazete 26686 on October 31, 2007, gives the results "
            "of the October 21, 2007 constitutional referendum on Law 5678, as amended by Law 5697. It has one table by "
            "province and one by customs gate. The tables are RTF files, so the counts were read from their text, not "
            "digitized from scans. The customs table lists the same number of registered voters and voters."
        ),
    ),
    REFERENDUM_2017: (
        "Karar no. 663: 16 Nisan 2017 Anayasa Değişikliği Halkoylaması kesin sonuçları",
        (
            "YSK decision 663, published in the Resmî Gazete (Mükerrer) on April 27, 2017, gives the final results of "
            "the constitutional referendum by province, with separate totals for votes cast abroad and at customs gates. "
            "The tables are scanned images, so we digitized every count and recorded a page and bounding-box locator for "
            "it. The customs table repeats the number of voters registered abroad, so we store no registered voter "
            "figures for customs gates."
        ),
    ),
    PRESIDENT[1]: (
        "Karar no. 2023/1091: Cumhurbaşkanı Seçimi kesin sonuçları",
        (
            "YSK decision 2023/1091, published in Resmî Gazete 32195 (Mükerrer) on May 19, 2023: final results of the "
            "first round of the presidential election. The tables are scanned images, so every count was digitized and "
            "carries a page and bounding-box locator."
        ),
    ),
    PRESIDENT[2]: (
        "Karar no. 2023/1269: Cumhurbaşkanı Seçimi ikinci oylama kesin sonuçları",
        (
            "YSK decision 2023/1269, published in Resmî Gazete 32208 (Mükerrer) on June 1, 2023: final results of the "
            "presidential runoff. The tables are scanned images, so every count was digitized and carries a page and "
            "bounding-box locator."
        ),
    ),
    PRESIDENT_2018: (
        "Karar no. 2018/952: Cumhurbaşkanı Seçimi kesin sonuçları",
        (
            "YSK decision 2018/952, published in Resmî Gazete 30468 (Mükerrer) on July 4, 2018: final results of the "
            "June 24, 2018 presidential election. The tables are scanned images, so every count was digitized and "
            "carries a page and bounding-box locator."
        ),
    ),
    PARLIAMENT: (
        "Karar no. 2023/1255: 28. Dönem Milletvekili Genel Seçimi kesin sonuçları",
        (
            "YSK decision 2023/1255, published in Resmî Gazete 32206 (Mükerrer) on May 30, 2023: final results of the "
            "parliamentary election for the 28th term. For each of the 87 electoral districts, it gives the turnout, each "
            "party's votes and its share of the votes cast abroad, the seats won, and the members elected. The tables are "
            "scanned images. Each cell was read three times by OCR and checked against the printed sums, and every count "
            "carries a page and bounding-box locator."
        ),
    ),
    LISTS: (
        "Karar no. 2023/643: 28. Dönem Milletvekili Genel Seçimi kesin aday listeleri",
        (
            "YSK decision 2023/643, published in Resmî Gazete 32168 (Mükerrer) on April 19, 2023: final candidate lists "
            "for the May 14, 2023 parliamentary election, including each party's ranked list and independent candidates "
            "in each of the 87 electoral districts. The lists are scanned images. We read only each candidate's rank and "
            "name; the education and occupation columns were blanked before OCR, and we only kept the blanked pages. "
            "Each name was read four times by OCR. We checked by eye any names where the readings disagreed or where "
            "accents did not match the marks on the scan. Every candidate carries a page and bounding-box locator."
        ),
    ),
    PARLIAMENT_2018: (
        "Karar no. 2018/953: 27. Dönem Milletvekili Genel Seçimi kesin sonuçları",
        (
            "YSK decision 2018/953, published in Resmî Gazete 30468 (Mükerrer) on July 4, 2018: final results of the "
            "parliamentary election for the 27th term. For each electoral district, it gives the turnout, each party's "
            "votes and its share of the votes cast abroad, the seats won, and the members elected. The tables are "
            "scanned images. Each cell was read three times by OCR and checked against the printed sums, and every count "
            "carries a page and bounding-box locator. The gazette prints Karaman's page twice (pages 63 and 65) and "
            "leaves out Kahramanmaraş."
        ),
    ),
    LISTS_2018: (
        "24 Haziran 2018 Milletvekili Seçimleri kesin aday listesi",
        (
            "YSK's final candidate lists for the June 24, 2018 parliamentary election, published on its website in PDF "
            "form alongside decision 2018/668 (Resmî Gazete 30436, Mükerrer, May 30, 2018). The lists show every "
            "party's ranked candidates and independent candidates for each of the 87 electoral districts. We read the "
            "rank and name of each candidate directly from the PDF text layer; we do not use OCR. Education and "
            "occupation columns are not read and are blanked in the page images. Every candidate has a page and "
            "bounding-box locator."
        ),
    ),
    LISTS_2015: (
        "1 Kasım 2015 Milletvekili Seçimleri kesin aday listesi",
        (
            "YSK's final candidate lists for the November 1, 2015 parliamentary election, published on its website in "
            "PDF form. The lists show every party's ranked candidates and independent candidates for each of the 85 "
            "electoral districts; independent candidates are printed with the number 0 instead of a rank. We read the "
            "rank and name of each candidate directly from the PDF text layer; we do not use OCR. Education and "
            "occupation columns are not read and are blanked in the page images. Every candidate has a page and "
            "bounding-box locator."
        ),
    ),
    LISTS_JUNE_2015: (
        "7 Haziran 2015 Milletvekili Seçimleri kesin aday listesi",
        (
            "YSK's final candidate lists for the June 7, 2015 parliamentary election, published on its website in PDF "
            "form. The lists show every party's ranked candidates and independent candidates for each of the 85 "
            "electoral districts. We read the rank and name of each candidate directly from the PDF text layer; we do "
            "not use OCR. Education and occupation columns are not read and are blanked in the page images. Every "
            "candidate has a page and bounding-box locator."
        ),
    ),
    LISTS_2011: (
        "12 Haziran 2011 Milletvekili Seçimleri kesin aday listesi",
        (
            "YSK's final candidate lists for the June 12, 2011 parliamentary election, published on its website in PDF "
            "form. The lists show every party's ranked candidates and independent candidates for each of the 85 "
            "electoral districts. We read the rank and name of each candidate directly from the PDF text layer; we do "
            "not use OCR. Long names that wrap onto multiple lines are joined into a single name. Education and "
            "occupation columns are not read and are blanked in the page images. Every candidate has a page and "
            "bounding-box locator."
        ),
    ),
    PARLIAMENT_2015: (
        "Karar no. 2310: 26. Dönem Milletvekili Genel Seçimi kesin sonuçları",
        (
            "YSK decision 2310, published in Resmî Gazete 29530 (Mükerrer) on November 12, 2015: final results of the "
            "November 1, 2015 parliamentary election for the 26th term. For each of the 85 electoral districts, it "
            "gives the turnout, each party's votes and its share of the votes cast abroad, the seats won, and the "
            "members elected. The tables are scanned images. Each cell was read three times by OCR and checked against "
            "the printed sums. Every count carries a page and bounding-box locator."
        ),
    ),
    PARLIAMENT_JUNE_2015: (
        "Karar no. 1415: 25. Dönem Milletvekili Genel Seçimi kesin sonuçları",
        (
            "YSK decision 1415, published in Resmî Gazete 29390 (Mükerrer) on June 18, 2015: final results of the "
            "June 7, 2015 parliamentary election for the 25th term. For each of the 85 electoral districts, it gives "
            "the turnout, each party's votes and its share of the votes cast abroad, the seats won, and the members "
            "elected. The tables are scanned images. Each cell was read three times by OCR and checked against the "
            "printed sums. Every count carries a page and bounding-box locator."
        ),
    ),
    PARLIAMENT_2011: (
        "Karar no. 1070: 24. Dönem Milletvekili Genel Seçimi kesin sonuçları",
        (
            "YSK decision 1070, published in Resmî Gazete 27973 on June 23, 2011: final results of the June 12, 2011 "
            "parliamentary election for the 24th term. For each of the 85 electoral districts, it gives the turnout, "
            "each party's votes and its share of the votes cast at customs gates, the seats won, and the members "
            "elected. The tables are scanned images. Each cell was read three times by OCR and checked against the "
            "printed sums. Every count carries a page and bounding-box locator."
        ),
    ),
    PARLIAMENT_2007: (
        "Karar no. 739: 23. Dönem Milletvekili Genel Seçimi kesin sonuçları",
        (
            "YSK decision 739, published in Resmî Gazete 26598 (Mükerrer) on July 30, 2007: final results of the July "
            "22, 2007 parliamentary election for the 23rd term. For each of the 85 electoral districts, it gives "
            "registered voters, turnout, valid votes, each party's votes and its share of the votes cast at customs "
            "gates, the seats won, and the members elected. The gazette published the result tables as a Word document, "
            "so the counts were read from its text, not digitized from scans. The district tables do not give invalid "
            "votes."
        ),
    ),
    ISTANBUL: (
        "Karar no. 5002: İstanbul Büyükşehir Belediye Başkanlığı Seçimi kesin sonuçları",
        (
            "YSK decision 5002, published in Resmî Gazete 30828 on July 11, 2019: final results of the June 23, 2019 "
            "İstanbul metropolitan mayoral election (annex 1). The table is a scanned image, so every count was digitized "
            "and carries a page and bounding-box locator."
        ),
    ),
}
PRESIDENT_METHODOLOGY = (
    "A candidate who wins more than half of the valid votes is elected. If no one does, the two candidates with the "
    "most votes face each other in a second round two weeks later. The current constitutional rule is Article 101; "
    "before the 2017 amendments, it was Article 102."
)
RENAMED = {
    "İşçi Partisi": ("Vatan Partisi", date(2015, 2, 15)),
    "Özgürlük ve Dayanışma Partisi": ("Sol Parti", date(2019, 12, 22)),
}
FORMER = {
    "Millet Partisi": ("Islahatçı Demokrasi Partisi", "IDP", date(1992, 11, 23)),
    "Milliyetçi Hareket Partisi": ("Milliyetçi Çalışma Partisi", "MÇP", date(1993, 1, 24)),
    "Türkiye Komünist Partisi": ("Sosyalist İktidar Partisi", "SİP", date(2001, 11, 11)),
    "Demokrat Parti": ("Doğru Yol Partisi", "DYP", date(2007, 5, 27)),
    "Genç Parti": ("Yeniden Doğuş Partisi", "YDP", date(2002, 8, 23)),
    "Emek Partisi": ("Emeğin Partisi", "EMEP", date(2005, 11, 27)),
}
MISPRINTED = {"Sabahat Akkiray": "Sabahat Akkiraz"}
PRESIDENT_CAVEAT = (
    "Customs-gate ballot boxes serve voters registered abroad, so the gazette prints the abroad register again for "
    "customs gates. It is stored once, under abroad. Prison and mobile ballot boxes are included in the domestic "
    "totals."
)
INCE_WITHDREW = (
    "Withdrew on May 11, 2023, after the ballots were printed. His name stayed on the ballot, and votes for him "
    "counted as valid."
)
PARLIAMENT_METHODOLOGY = (
    "Seats are allocated by D'Hondt in each of the 87 electoral districts, with each party's list counted on its own. "
    "Only parties that clear a national threshold take part: 7 percent of valid votes nationwide, counted for the "
    "whole alliance when the party belongs to one. Votes cast abroad and at customs gates form a national pool, which "
    "is shared out among the districts in proportion to each district's valid votes. Votes for an alliance with no "
    "party marked go to its member parties in proportion to their votes."
)
PARLIAMENT_2018_METHODOLOGY = (
    "Seats are allocated by D'Hondt in each of the 87 electoral districts, in two steps. First, each alliance receives "
    "seats based on the combined votes of its member parties, and each party outside an alliance receives seats based "
    "on its own votes. Then each alliance's seats are divided among its member parties, again by D'Hondt. Only parties "
    "that clear a national threshold of 10 percent of valid votes nationwide take part. For a party that belongs to an "
    "alliance, the threshold is counted for the whole alliance. Votes cast abroad and at customs gates form a national "
    "pool. That pool is shared out among the districts in proportion to each district's valid votes. Votes for an "
    "alliance with no party marked go to its member parties in proportion to their votes."
)
KAHRAMANMARAS_CAVEAT = (
    "The gazette prints Karaman's page twice and leaves out Kahramanmaraş. We therefore derive Kahramanmaraş's "
    "voters, valid votes, party votes, and seats by subtracting the other 86 districts from the national totals. Its "
    "share of the votes cast abroad is computed the same way as for every other district. We do not show its "
    "registered voters: subtracting the other districts from the national total gives fewer registered voters than "
    "voters, so the figure is unreliable. Its members come from Wikidata."
)
PARLIAMENT_2015_METHODOLOGY = (
    "Seats are allocated by D'Hondt in each of the 85 electoral districts, with each party's list counted on its own. "
    "Only parties that clear the national threshold of 10 percent of valid votes nationwide take part. Votes cast "
    "abroad and at customs gates form a national pool, which is shared out among the districts in proportion to each "
    "district's valid votes."
)
PARLIAMENT_2011_METHODOLOGY = (
    "Seats are allocated by D'Hondt in each of the 85 electoral districts, with each party's list counted on its own. "
    "Only parties that clear the national threshold of 10 percent of valid votes nationwide take part. Independent "
    "candidates face no threshold, and each counts as a list of one. Votes cast at customs gates form a national pool. "
    "Each party's share of that pool is divided among the districts in proportion to the party's own votes in each "
    "district."
)
INDEPENDENTS_2011_CAVEAT = (
    "The official gazette reports independent candidates as one combined total per district, so the results show them "
    "as a single option. Independents won 35 seats. The members list names each elected independent."
)
INDEPENDENTS_2007_CAVEAT = (
    "The official gazette reports independent candidates as one combined total per district, so the results show them "
    "as a single option. Independents won 26 seats. The members list names each elected independent. The decision's "
    "national seat table gives MHP 70 seats and 549 in all, but its district tables give MHP 71 seats and fill all "
    "550. The seats here follow the district tables."
)
DIYARBAKIR_2011_CAVEAT = (
    "Hatip Dicle, an independent, won a seat in Diyarbakır. Before the final results were published, the YSK ruled "
    "that a final prison sentence barred him from the Assembly, and the seat went to Oya Eronat of AK Parti. The "
    "official gazette lists Eronat, so this district shows six AK Parti seats and five independent seats."
)
REGISTERED_2015_CAVEAT = (
    "The registered voters printed on the 85 district pages add up to 54,084,543. That is 34,603 more than the "
    "domestic total of 54,049,940 printed in the same decision. Every district figure was checked against the scan "
    "and stored as printed, so the discrepancy comes from the gazette itself."
)
REGISTERED_JUNE_2015_CAVEAT = (
    "The registered voters printed on the 85 district pages add up to 53,772,719. That is 30,881 more than the "
    "domestic total of 53,741,838 printed in the same decision. Every district figure was checked against the scan "
    "and stored as printed, so the discrepancy comes from the gazette itself."
)
LISTS_2018_CAVEAT = (
    "The gazette records each member under the list they ran on. Some candidates ran on an ally's list, including two "
    "party leaders: Mustafa Destici of the Great Unity Party (BBP) ran on the AK Parti list, and Gültekin Uysal of the "
    "Democrat Party ran on the İYİ Parti list."
)
LEFT_2018 = {
    "Q51212178": (
        "Left the Assembly on appointment as a minister. Since the 2017 constitutional amendments, ministers cannot also "
        "be members of the Assembly."
    ),
    "Q106224801": "Left the Assembly after being elected mayor in the March 2019 local elections.",
}
CONVICTED = "Membership ended when a final court conviction was read out in the General Assembly."
POOL_METHODOLOGY_2015 = (
    "This contest holds the national pool of votes cast abroad and at customs gates, as cast. Each district contest "
    "holds its share of the pool as apportioned votes."
)
POOL_METHODOLOGY_2011 = (
    "This contest holds the national pool of votes cast at customs gates, as cast. Each district contest holds its "
    "share of the pool as apportioned votes."
)
POOL_METHODOLOGY = (
    f"{POOL_METHODOLOGY_2015} Votes for an alliance with no party marked are published for the pool only; district "
    "party votes already include them."
)
LISTS_CAVEAT = (
    "Some parties ran their candidates on other parties' lists, and the gazette records every member under the list "
    "they ran on. HDP's candidates ran on the Green Left Party (YSP) list. Candidates from Saadet, DEVA, Gelecek, and "
    "the Democrat Party ran on CHP lists as CHP members."
)
ATALAY_REMOVED = (
    "Elected while in prison and never sworn in. His membership ended when the Court of Cassation's ruling upholding "
    "his conviction was read out in the General Assembly."
)
ISTANBUL_CAVEAT = (
    "YSK tables label votes by party, not by candidate, and give independent candidates only as a combined total."
)
PROVINCES = [
    "Adana",
    "Adıyaman",
    "Afyonkarahisar",
    "Ağrı",
    "Amasya",
    "Ankara",
    "Antalya",
    "Artvin",
    "Aydın",
    "Balıkesir",
    "Bilecik",
    "Bingöl",
    "Bitlis",
    "Bolu",
    "Burdur",
    "Bursa",
    "Çanakkale",
    "Çankırı",
    "Çorum",
    "Denizli",
    "Diyarbakır",
    "Edirne",
    "Elazığ",
    "Erzincan",
    "Erzurum",
    "Eskişehir",
    "Gaziantep",
    "Giresun",
    "Gümüşhane",
    "Hakkari",
    "Hatay",
    "Isparta",
    "Mersin",
    "İstanbul",
    "İzmir",
    "Kars",
    "Kastamonu",
    "Kayseri",
    "Kırklareli",
    "Kırşehir",
    "Kocaeli",
    "Konya",
    "Kütahya",
    "Malatya",
    "Manisa",
    "Kahramanmaraş",
    "Mardin",
    "Muğla",
    "Muş",
    "Nevşehir",
    "Niğde",
    "Ordu",
    "Rize",
    "Sakarya",
    "Samsun",
    "Siirt",
    "Sinop",
    "Sivas",
    "Tekirdağ",
    "Tokat",
    "Trabzon",
    "Tunceli",
    "Şanlıurfa",
    "Uşak",
    "Van",
    "Yozgat",
    "Zonguldak",
    "Aksaray",
    "Bayburt",
    "Karaman",
    "Kırıkkale",
    "Batman",
    "Şırnak",
    "Bartın",
    "Ardahan",
    "Iğdır",
    "Yalova",
    "Karabük",
    "Kilis",
    "Osmaniye",
    "Düzce",
]
CANDIDATES = [
    ("Recep Tayyip Erdoğan", "Adalet ve Kalkınma Partisi"),
    ("Muharrem İnce", "Memleket Partisi"),
    ("Kemal Kılıçdaroğlu", "Cumhuriyet Halk Partisi"),
    ("Sinan Oğan", None),
]
CHANNELS = {"domestic": 0, "abroad": 4, "customs": 8}
PRESIDENT_CELLS = {
    1: {
        "domestic": (
            4,
            (258, 388),
            (160, 174, 188, 202),
            (60721745, 53993683, 52972934, 1020749),
            (395, 488),
            (256, 269, 281, 294),
            (26086102, 216470, 23873749, 2796613),
        ),
        "abroad": (
            5,
            (250, 382),
            (175, 188, 201, 214),
            (3423759, 1691287, 1676268, 15019),
            (397, 488),
            (291, 304, 316, 329),
            (967180, 15759, 665111, 28218),
        ),
        "customs": (
            6,
            (275, 407),
            (175, 188, 201, 214),
            (None, 148183, 146847, 1336),
            (397, 488),
            (290, 303, 316, 328),
            (80567, 3554, 56318, 6408),
        ),
    },
    2: {
        "domestic": (
            4,
            (258, 390),
            (155, 167, 180, 193),
            (60771236, 52093375, 51418556, 674819),
            (395, 488),
            (261, 273),
            (26690529, 24728027),
        ),
        "abroad": (
            5,
            (250, 382),
            (174, 187, 201, 214),
            (3426218, 1782886, 1774459, 8427),
            (397, 488),
            (289, 301),
            (1059494, 714965),
        ),
        "customs": (
            6,
            (275, 407),
            (174, 187, 201, 214),
            (None, 147340, 146298, 1042),
            (397, 488),
            (289, 302),
            (84566, 61732),
        ),
    },
}
PRESIDENT_TOTALS = {1: (27133849, 235783, 24595178, 2831239), 2: (27834589, 25504724)}
RUNOFF = (0, 2)
CANDIDATES_2018 = [
    ("Muharrem İnce", "Cumhuriyet Halk Partisi"),
    ("Meral Akşener", "İYİ Parti"),
    ("Recep Tayyip Erdoğan", "Adalet ve Kalkınma Partisi"),
    ("Selahattin Demirtaş", "Halkların Demokratik Partisi"),
    ("Temel Karamollaoğlu", "Saadet Partisi"),
    ("Doğu Perinçek", "Vatan Partisi"),
]
PRESIDENT_2018_CELLS = {
    "domestic": (
        3,
        (193, 325),
        (123, 139, 155, 171),
        (56322632, 49671344, 48562084, 1109260),
        (350, 422),
        (252, 268, 284, 300, 316, 332),
        (14951788, 3603858, 25436238, 4039390, 434882, 95928),
    ),
    "abroad": (
        4,
        (184, 316),
        (146, 162, 178, 194),
        (3044837, 1358584, 1341218, 17366),
        (335, 424),
        (285, 301, 317, 333, 349, 365),
        (328934, 36896, 807974, 157111, 7730, 2573),
    ),
    "customs": (
        5,
        (207, 337),
        (149, 165, 181, 197),
        (None, 168031, 165325, 2706),
        (329, 418),
        (288, 304, 320, 336, 352, 368),
        (59599, 8276, 86611, 9293, 1092, 454),
    ),
}
PRESIDENT_2018_TOTALS = (15340321, 3649030, 26330823, 4205794, 443704, 98955)
CANDIDATES_2014 = [
    ("Recep Tayyip Erdoğan", "Adalet ve Kalkınma Partisi"),
    ("Selahattin Demirtaş", "Halkların Demokratik Partisi"),
    ("Ekmeleddin Mehmet İhsanoğlu", None),
]
JOINT_2014 = ("Cumhuriyet Halk Partisi", "Milliyetçi Hareket Partisi")
PRESIDENT_2014_CELLS = {
    "domestic": (
        3,
        (257, 389),
        (221, 237, 252, 267),
        (52894115, 40753492, 40019352, 734140),
        (396, 489),
        (355, 370, 386),
        (20670826, 3914359, 15434167),
    ),
    "abroad": (
        3,
        (248, 380),
        (640, 656, 671, 686),
        (2798726, 232795, 230938, 1857),
        (398, 491),
        (771, 787, 802),
        (143873, 22582, 64483),
    ),
    "customs": (
        4,
        (271, 403),
        (256, 271, 286, 302),
        (None, 297340, 295621, 1719),
        (395, 487),
        (372, 387, 402),
        (185444, 21107, 89070),
    ),
}
PRESIDENT_2014_TOTALS = (21000143, 3958048, 15587720)
ISTANBUL_CELLS = [
    (None, REGISTERED, 10560963, 330, 535, 243),
    (None, VOTERS, 8925166, 330, 535, 283),
    (None, VALID, 8746566, 330, 535, 322),
    ("Saadet Partisi", VOTES, 47832, 368, 497, 633),
    ("Vatan Partisi", VOTES, 13962, 368, 497, 653),
    ("Cumhuriyet Halk Partisi", VOTES, 4742082, 368, 497, 675),
    ("Adalet ve Kalkınma Partisi", VOTES, 3936068, 368, 497, 696),
    ("others", VOTES, 6622, 368, 497, 717),
]
PARTIES = [
    ("Millet Partisi", "MP", None, 1041, 153, None, 51121),
    ("Hak ve Özgürlükler Partisi", "HAK-PAR", None, 1212, 90, None, 41207),
    ("Türkiye Komünist Partisi", "TKP", "Sosyalist Güç Birliği İttifakı", 1537, 150, 18, 62104),
    ("Türkiye Komünist Hareketi", "TKH", "Sosyalist Güç Birliği İttifakı", 487, 50, 6, 16933),
    ("Sol Parti", "SOL Parti", "Sosyalist Güç Birliği İttifakı", 2573, 194, 29, 75196),
    ("Genç Parti", "Genç Parti", None, 1680, 294, None, 110998),
    ("Memleket Partisi", "Memleket", None, 11838, 2921, None, 487910),
    ("Büyük Birlik Partisi", "BBP", "Cumhur İttifakı", 7349, 904, 47, 522470),
    ("Adalet ve Kalkınma Partisi", "AK Parti", "Cumhur İttifakı", 742022, 59727, 4576, 18586137),
    ("Yeniden Refah Partisi", "YRP", "Cumhur İttifakı", 18689, 2502, 121, 1505736),
    ("Milliyetçi Hareket Partisi", "MHP", "Cumhur İttifakı", 183598, 16733, 1144, 5283345),
    ("Yeşiller ve Sol Gelecek Partisi", "YSP", "Emek ve Özgürlük İttifakı", 172314, 7323, 191, 4624094),
    ("Türkiye İşçi Partisi", "TİP", "Emek ve Özgürlük İttifakı", 48064, 4195, 56, 903742),
    ("Adalet Birlik Partisi", "ABP", None, 2158, 260, None, 38778),
    ("Anavatan Partisi", "ANAP", None, 1332, 177, None, 64593),
    ("Yenilik Partisi", "Yenilik", None, 153, 10, None, 11008),
    ("Halkın Kurtuluş Partisi", "HKP", None, 721, 86, None, 31024),
    ("Milli Yol Partisi", "Milli Yol", None, 243, 38, None, 17477),
    ("Vatan Partisi", "Vatan", None, 1094, 124, None, 51502),
    ("Güç Birliği Partisi", "GBP", None, 789, 73, None, 25497),
    ("Cumhuriyet Halk Partisi", "CHP", "Millet İttifakı", 387985, 37638, 2097, 13374463),
    ("İYİ Parti", "İYİ Parti", "Millet İttifakı", 56332, 7702, 315, 5211632),
    ("Adalet Partisi", "AP", "ATA İttifakı", 3079, 409, 11, 105737),
    ("Zafer Partisi", "Zafer", "ATA İttifakı", 14638, 3058, 56, 1198647),
]
INDEPENDENTS = "Bağımsızlar"
INDEPENDENTS_DOMESTIC = 226831
ALLIANCES = [
    ("Sosyalist Güç Birliği İttifakı", 53),
    ("Cumhur İttifakı", 5888),
    ("Emek ve Özgürlük İttifakı", 247),
    ("Millet İttifakı", 2412),
    ("ATA İttifakı", 67),
]
POOL = {REGISTERED: 3423759, VOTERS: 1841846, VALID: 1814406, INVALID: 27440}
NATIONAL_VALID, DOMESTIC_VALID = 54442588, 52628182
SEATS = {
    "Adalet ve Kalkınma Partisi": 268,
    "Yeniden Refah Partisi": 5,
    "Milliyetçi Hareket Partisi": 50,
    "Yeşiller ve Sol Gelecek Partisi": 61,
    "Türkiye İşçi Partisi": 4,
    "Cumhuriyet Halk Partisi": 169,
    "İYİ Parti": 43,
}
SPLIT = {"İstanbul": 3, "Ankara": 3, "İzmir": 2, "Bursa": 2}
PARTIES_2018 = [
    ("Adalet ve Kalkınma Partisi", "AK Parti", "Cumhur İttifakı", 695711, 72216, 11034, 20559732),
    ("Milliyetçi Hareket Partisi", "MHP", "Cumhur İttifakı", 105431, 13464, 1708, 5444728),
    ("Hür Dava Partisi", "HÜDA PAR", None, 1633, 257, None, 153649),
    ("Vatan Partisi", "Vatan", None, 3372, 651, None, 110849),
    ("Halkların Demokratik Partisi", "HDP", None, 240510, 20170, None, 5606622),
    ("Cumhuriyet Halk Partisi", "CHP", "Millet İttifakı", 221797, 43957, 1539, 11086897),
    ("Saadet Partisi", "Saadet", "Millet İttifakı", 9762, 1562, 66, 660749),
    ("İYİ Parti", "İYİ Parti", "Millet İttifakı", 50080, 10538, 351, 4932510),
]
INDEPENDENTS_DOMESTIC_2018 = 75630
PARTIES_2015 = [
    ("Millet Partisi", "MP", None, 185, 50, None, 19479),
    ("Vatan Partisi", "Vatan", None, 3321, 639, None, 114843),
    ("Cumhuriyet Halk Partisi", "CHP", None, 177151, 33786, None, 11900875),
    ("Hak ve Özgürlükler Partisi", "HAK-PAR", None, 1620, 398, None, 106565),
    ("Saadet Partisi", "Saadet", None, 5621, 814, None, 319543),
    ("Demokratik Sol Parti", "DSP", None, 222, 60, None, 31523),
    ("Demokrat Parti", "DP", None, 377, 80, None, 68862),
    ("Bağımsız Türkiye Partisi", "BTP", None, 589, 123, None, 48585),
    ("Milliyetçi Hareket Partisi", "MHP", None, 81076, 10591, None, 5602469),
    ("Halkın Kurtuluş Partisi", "HKP", None, 1573, 277, None, 81207),
    ("Liberal Demokrat Parti", "LDP", None, 1573, 80, None, 25163),
    ("Halkların Demokratik Partisi", "HDP", None, 220059, 13823, None, 4914203),
    ("Büyük Birlik Partisi", "BBP", None, 5013, 837, None, 247354),
    ("Adalet ve Kalkınma Partisi", "AK Parti", None, 647028, 75504, None, 22959394),
    ("Komünist Parti", "KP", None, 1919, 120, None, 50488),
    ("Doğru Yol Partisi", "DYP", None, 365, 90, None, 13676),
]
INDEPENDENTS_DOMESTIC_2015 = 51038
DOMESTIC_2015 = {REGISTERED: 54049940, VOTERS: 47239370, VALID: 46555267, INVALID: 684103}
REGISTERED_SURPLUS = {DAY_2015: 34603, DAY_JUNE_2015: 30881}
PARTIES_JUNE_2015 = [
    ("Doğru Yol Partisi", "DYP", None, 1119, 183, None, 27550),
    ("Anadolu Partisi", "Anadolu", None, 531, 112, None, 27045),
    ("Hak ve Özgürlükler Partisi", "HAK-PAR", None, 1350, 224, None, 57142),
    ("Komünist Parti", "KP", None, 227, 56, None, 13497),
    ("Millet Partisi", "MP", None, 140, 26, None, 17307),
    ("Hak ve Adalet Partisi", "HAP", None, 522, 73, None, 5116),
    ("Merkez Parti", "MEP", None, 247, 49, None, 20649),
    ("Toplumsal Uzlaşma Reform ve Kalkınma Partisi", "TURK Parti", None, 672, 208, None, 71821),
    ("Halkın Kurtuluş Partisi", "HKP", None, 1579, 227, None, 58590),
    ("Liberal Demokrat Parti", "LDP", None, 372, 61, None, 26067),
    ("Milliyetçi Hareket Partisi", "MHP", None, 83457, 12994, None, 7423555),
    ("Halkların Demokratik Partisi", "HDP", None, 196827, 14528, None, 5847134),
    ("Saadet Partisi", "Saadet", None, 13999, 2312, None, 932867),
    ("Cumhuriyet Halk Partisi", "CHP", None, 146263, 33195, None, 11338681),
    ("Adalet ve Kalkınma Partisi", "AK Parti", None, 462506, 57158, None, 18347747),
    ("Demokratik Sol Parti", "DSP", None, 1380, 236, None, 84194),
    ("Yurt Partisi", "YURT-P", None, 149, 29, None, 9111),
    ("Demokrat Parti", "DP", None, 421, 146, None, 75217),
    ("Vatan Partisi", "Vatan", None, 5350, 1119, None, 155205),
    ("Bağımsız Türkiye Partisi", "BTP", None, 1191, 232, None, 95052),
]
INDEPENDENTS_DOMESTIC_JUNE_2015 = 488226
DOMESTIC_JUNE_2015 = {REGISTERED: 53741838, VOTERS: 46451389, VALID: 45121773, INVALID: 1329616}
PARTIES_2011 = [
    ("Adalet ve Kalkınma Partisi", "AK Parti", None, 0, 78875, None, 21320207),
    ("Demokrat Parti", "DP", None, 0, 705, None, 278775),
    ("Cumhuriyet Halk Partisi", "CHP", None, 0, 33552, None, 11122420),
    ("Emek Partisi", "EMEP", None, 0, 362, None, 31766),
    ("Millet Partisi", "MP", None, 0, 43, None, 60673),
    ("Liberal Demokrat Parti", "LDP", None, 0, 56, None, 15166),
    ("Saadet Partisi", "Saadet", None, 0, 1984, None, 541470),
    ("Hak ve Eşitlik Partisi", "HEPAR", None, 0, 333, None, 124082),
    ("Halkın Sesi Partisi", "HAS Parti", None, 0, 365, None, 329358),
    ("Milliyetçi Hareket Partisi", "MHP", None, 0, 10503, None, 5575010),
    ("Doğru Yol Partisi", "DYP", None, 0, 154, None, 64453),
    ("Türkiye Komünist Partisi", "TKP", None, 0, 220, None, 63786),
    ("Milliyetçi ve Muhafazakar Parti", "MMP", None, 0, 83, None, 36105),
    ("Büyük Birlik Partisi", "BBP", None, 0, 432, None, 322819),
    ("Demokratik Sol Parti", "DSP", None, 0, 200, None, 107889),
]
INDEPENDENTS_DOMESTIC_2011 = 2819917
DOMESTIC_2011 = {REGISTERED: 50237343, VOTERS: 43785665, VALID: 42813896, INVALID: 971769}
SEATS_JUNE_2015 = {
    "Milliyetçi Hareket Partisi": 80,
    "Halkların Demokratik Partisi": 80,
    "Cumhuriyet Halk Partisi": 132,
    "Adalet ve Kalkınma Partisi": 258,
}
POOL_PAGES: dict[date, list[tuple[int, str, int, dict[int, int | None], int]]] = {
    DAY_2015: [
        (3, "abroad", 4, {REGISTERED: 2899069, VOTERS: 1159871, VALID: 1147692, INVALID: 12179}, 3),
        (4, "customs", 8, {REGISTERED: None, VOTERS: 138454, VALID: 137272, INVALID: 1182}, 4),
    ],
    DAY_JUNE_2015: [
        (3, "abroad", 4, {REGISTERED: 2866979, VOTERS: 931646, VALID: 918302, INVALID: 13344}, 3),
        (4, "customs", 8, {REGISTERED: None, VOTERS: 124432, VALID: 123168, INVALID: 1264}, 4),
    ],
    DAY_2011: [(2, "customs", 8, {REGISTERED: 2568979, VOTERS: 129283, VALID: 127867, INVALID: 1416}, 4)],
}
SEATS_2011 = {
    "Adalet ve Kalkınma Partisi": 327,
    "Cumhuriyet Halk Partisi": 135,
    "Milliyetçi Hareket Partisi": 53,
    INDEPENDENTS: 35,
}
PARTIES_2007 = [
    ("Aydınlık Türkiye Partisi", "ATP", None, 0, 1357, None, 99625),
    ("Bağımsız Türkiye Partisi", "BTP", None, 0, 824, None, 181271),
    ("Saadet Partisi", "Saadet", None, 0, 6784, None, 813505),
    ("İşçi Partisi", "İP", None, 0, 1106, None, 127042),
    ("Cumhuriyet Halk Partisi", "CHP", None, 0, 40255, None, 7277553),
    ("Halkın Yükselişi Partisi", "HYP", None, 0, 791, None, 178219),
    ("Özgürlük ve Dayanışma Partisi", "ÖDP", None, 0, 1054, None, 51001),
    ("Genç Parti", "Genç Parti", None, 0, 5192, None, 1059679),
    ("Demokrat Parti", "DP", None, 0, 6187, None, 1892686),
    ("Liberal Demokrat Parti", "LDP", None, 0, 190, None, 35174),
    ("Milliyetçi Hareket Partisi", "MHP", None, 0, 33417, None, 4968452),
    ("Adalet ve Kalkınma Partisi", "AK Parti", None, 0, 128694, None, 16198597),
    ("Emek Partisi", "EMEP", None, 0, 522, None, 25770),
    ("Türkiye Komünist Partisi", "TKP", None, 0, 411, None, 78847),
]
INDEPENDENTS_DOMESTIC_2007 = 1835486
DOMESTIC_2007 = {REGISTERED: 42571284, VOTERS: 35828274, VALID: 34822907, INVALID: 1005367}
CUSTOMS_2007 = {REGISTERED: 228019, VOTERS: 228019, VALID: 226784, INVALID: 1235}
NATIONAL_2007 = {REGISTERED: 42799303, VOTERS: 36056293, VALID: 35049691, INVALID: 1006602}
SEATS_2007 = {
    "Adalet ve Kalkınma Partisi": 341,
    "Cumhuriyet Halk Partisi": 112,
    "Milliyetçi Hareket Partisi": 71,
    INDEPENDENTS: 26,
}
SEATS_2015 = {
    "Cumhuriyet Halk Partisi": 134,
    "Milliyetçi Hareket Partisi": 40,
    "Halkların Demokratik Partisi": 59,
    "Adalet ve Kalkınma Partisi": 317,
}
SPLIT_2015 = {"İstanbul": 3, "Ankara": 2, "İzmir": 2}
ALLIANCES_2018 = [("Cumhur İttifakı", 12742), ("Millet İttifakı", 1956)]
POOL_2018 = {REGISTERED: 3044837, VOTERS: 1525279, VALID: 1505809, INVALID: 19470}
DOMESTIC_2018 = {REGISTERED: 56322632, VOTERS: 49664165, VALID: 48631366, INVALID: 1032799}
SEATS_2018 = {
    "Adalet ve Kalkınma Partisi": 295,
    "Milliyetçi Hareket Partisi": 49,
    "Halkların Demokratik Partisi": 67,
    "Cumhuriyet Halk Partisi": 146,
    "İYİ Parti": 43,
}
KAHRAMANMARAS = (46, 0)
POOL_COLUMNS = [204, 682, 885, 1089, 1292, 1496, 1615]


def upper(text: str) -> str:
    return text.replace("i", "İ").replace("ı", "I").upper()


def title(text: str) -> str:
    lower = " ".join(text.replace("I", "ı").replace("İ", "i").lower().split())
    return re.sub(r"(?<![^\W\d_])(?<!['’])(?!(?<=-)y?[iıuü]\b)[^\W\d_]", lambda letter: upper(letter[0]), lower)


def render(pdf: raw.Artifact, page: int) -> dict[str, object]:
    image = PAGES / f"{pdf.sha256}-{page}.png"
    if not image.exists():
        PAGES.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "pdftoppm",
                "-r",
                "100",
                "-png",
                "-f",
                str(page),
                "-l",
                str(page),
                "-singlefile",
                str(pdf.path),
                str(image.with_suffix("")),
            ],
            check=True,
        )
    width, height = struct.unpack(">II", image.read_bytes()[16:24])
    return {"url": pdf.url, "page": page, "image": f"pages/{SOURCE}/{image.name}", "width": width, "height": height}


def fetch() -> None:
    for url in (
        *PRESIDENT.values(),
        PRESIDENT_2018,
        PARLIAMENT,
        LISTS,
        PARLIAMENT_2018,
        LISTS_2018,
        LISTS_2015,
        LISTS_JUNE_2015,
        LISTS_2011,
        ISTANBUL,
        REFERENDUM_2017,
        REFERENDUM_2010,
        PRESIDENT_2014,
        PARLIAMENT_2015,
        PARLIAMENT_JUNE_2015,
        PARLIAMENT_2011,
    ):
        raw.fetch(SOURCE, url, suffix=".pdf")
    raw.fetch(SOURCE, PARLIAMENT_2007, suffix=".doc")
    raw.fetch(SOURCE, COURT_2010, suffix=".htm")
    raw.fetch(SOURCE, OZONDER_PRESS, suffix=".htm")
    for url in WITHDREW_SOURCES:
        raw.fetch(SOURCE, url, suffix=".htm")
    for url in (REFERENDUM_2007, REFERENDUM_2007_CUSTOMS):
        raw.fetch(SOURCE, url, suffix=".rtf")
    for url in (MAYORS, MPS_2018, MPS_2015, MPS_JUNE_2015, MPS_2011, OZONDER_ITEM):
        raw.fetch(SOURCE, url, suffix=".json")


def parse() -> None:
    pages: list[dict[str, object]] = []
    president: list[dict[str, object]] = []
    ballots = [
        (
            str(ROUND_DAYS[number]),
            PRESIDENT[number],
            channels,
            PRESIDENT_TOTALS[number],
            RUNOFF if number == 2 else range(len(CANDIDATES)),
        )
        for number, channels in PRESIDENT_CELLS.items()
    ] + [
        (str(DAY_2018), PRESIDENT_2018, PRESIDENT_2018_CELLS, PRESIDENT_2018_TOTALS, range(len(CANDIDATES_2018))),
        (str(DAY_2014), PRESIDENT_2014, PRESIDENT_2014_CELLS, PRESIDENT_2014_TOTALS, range(len(CANDIDATES_2014))),
    ]
    for day, url, channels, totals, indices in ballots:
        pdf = raw.cached(SOURCE, url)
        summed = [0] * len(totals)
        for unit, (page, (ax0, ax1), arows, accounting, (cx0, cx1), crows, votes) in channels.items():
            rendered = render(pdf, page)
            pages.append(rendered)
            width, height = int(str(rendered["width"])), int(str(rendered["height"]))
            if sum(votes) != accounting[2]:
                raise ValueError(f"{day}, {unit}: candidate votes do not sum to valid votes")
            cells = [
                (None, measure, value, ax0, ax1, y)
                for measure, value, y in zip((REGISTERED, VOTERS, VALID, INVALID), accounting, arows, strict=True)
                if value is not None
            ] + [
                (candidate, VOTES, value, cx0, cx1, y)
                for candidate, value, y in zip(indices, votes, crows, strict=True)
            ]
            for candidate, measure, value, x0, x1, y in cells:
                president.append(
                    {
                        "day": day,
                        "unit": unit,
                        "channel": 0 if measure == REGISTERED else CHANNELS[unit],
                        "measure": measure,
                        "candidate": candidate,
                        "value": value,
                        "page": page,
                        "box": [x0 / width, (y - 7) / height, x1 / width, (y + 7) / height],
                    }
                )
            for index, value in enumerate(votes):
                summed[index] += value
        if tuple(summed) != totals:
            raise ValueError(f"{day}: channels do not sum to the printed totals")

    pdf = raw.cached(SOURCE, ISTANBUL)
    rendered = render(pdf, 3)
    pages.append(rendered)
    width, height = int(str(rendered["width"])), int(str(rendered["height"]))
    if sum(value for option, _, value, *_ in ISTANBUL_CELLS if option is not None) != ISTANBUL_CELLS[2][2]:
        raise ValueError("İstanbul 2019: votes do not sum to valid votes")
    istanbul = [
        {
            "option": option,
            "measure": measure,
            "value": value,
            "page": 3,
            "box": [x0 / width, (y - 8) / height, x1 / width, (y + 8) / height],
        }
        for option, measure, value, x0, x1, y in ISTANBUL_CELLS
    ]

    pdf = raw.cached(SOURCE, PARLIAMENT)
    SCANS.mkdir(parents=True, exist_ok=True)
    OCR.mkdir(parents=True, exist_ok=True)
    if not list(SCANS.glob(f"{pdf.sha256}-*.png")):
        subprocess.run(["pdftoppm", "-r", "300", "-gray", "-png", str(pdf.path), str(SCANS / pdf.sha256)], check=True)
    images = sorted(SCANS.glob(f"{pdf.sha256}-*.png"))
    scan_width, scan_height = struct.unpack(">II", images[0].read_bytes()[16:24])
    readings = []
    for image in images[5:]:
        cache = OCR / f"{image.stem}.json"
        if not cache.exists():
            layout = []
            requests: list[tuple[scan.Box, bool]] = [((400, 325, 2100, 380), False)]
            for rows, columns in scan.tables(image):
                kind = {3: "accounting" if columns[0] > 400 else "seats", 4: "elected", 5: "parties"}[len(columns)]
                numeric = {"accounting": {1}, "parties": {1, 2, 3}, "seats": {1}, "elected": {0}}[kind]
                wanted = [
                    (r, c)
                    for r in range(0 if kind == "accounting" else 2, len(rows) - 1)
                    for c in range(len(columns) - 1)
                    if kind != "accounting" or c == 1
                ]
                layout.append((kind, rows, columns, wanted))
                requests += [((columns[c], rows[r], columns[c + 1], rows[r + 1]), c in numeric) for r, c in wanted]
            read = iter(scan.read(image, requests))
            cache.write_text(
                json.dumps(
                    {
                        "title": next(read)[0],
                        "tables": [
                            {
                                "kind": kind,
                                "rows": rows,
                                "columns": columns,
                                "cells": [[r, c, next(read)] for r, c in wanted],
                            }
                            for kind, rows, columns, wanted in layout
                        ],
                    },
                    ensure_ascii=False,
                )
            )
        readings.append(json.loads(cache.read_text()))
    print("pages read:", len(readings))

    expected = {
        re.sub(r"\W", "", f"{upper(p)}İLİ({n})NUMARALISEÇİMÇEVRESİ" if n else f"{upper(p)}İLİSEÇİMÇEVRESİ"): (plate, n)
        for plate, p in enumerate(PROVINCES, 1)
        for n in (range(1, SPLIT[p] + 1) if p in SPLIT else [0])
    }
    labels = [upper(full) for full, *_ in PARTIES] + [upper(INDEPENDENTS)]
    names = [full for full, *_ in PARTIES] + [INDEPENDENTS]
    sections: dict[tuple[int, int], dict[str, list[tuple[int, list[tuple[list[str], scan.Box]]]]]] = {}
    for page, data in enumerate(readings, 6):
        match = difflib.get_close_matches(re.sub(r"\W", "", data["title"]), expected, n=1, cutoff=0.8)
        if not match:
            raise ValueError(f"page {page}: unknown district title {data['title']!r}")
        section = sections.setdefault(expected[match[0]], {})
        for table in data["tables"]:
            rows, columns = table["rows"], table["columns"]
            by_row: dict[int, list[tuple[list[str], scan.Box]]] = {}
            for r, c, cell in table["cells"]:
                by_row.setdefault(r, []).append((cell, (columns[c], rows[r], columns[c + 1], rows[r + 1])))
            if table["kind"] == "accounting" and "accounting" in section:
                continue
            section.setdefault(table["kind"], []).extend((page, cells) for _, cells in sorted(by_row.items()))
    if len(sections) != 87:
        raise ValueError(f"found {len(sections)} districts, expected 87")

    def number(cell: list[str]) -> Counter[int]:
        return Counter(
            int(text.replace(",", ".").replace(".", ""))
            for text in cell
            if re.fullmatch(r"\d{1,3}(\.\d{3})*", text.replace(",", "."))
        )

    def box(page: int, cell: scan.Box) -> dict[str, object]:
        return {
            "page": page,
            "box": [cell[0] / scan_width, cell[1] / scan_height, cell[2] / scan_width, cell[3] / scan_height],
        }

    pool_totals = [abroad + customs + (common or 0) for _, _, _, abroad, customs, common, _ in PARTIES] + [0]
    problems: list[tuple[object, ...]] = []
    unanimous = seen = 0
    districts: list[dict[str, object]] = []
    values: list[dict[str, object]] = []
    seats: list[dict[str, object]] = []
    won_national: Counter[str] = Counter()
    elected: list[dict[str, object]] = []
    for (plate, n), section in sorted(sections.items()):
        code = f"{plate:02}-{n}" if n else f"{plate:02}"
        accounting = []
        for page, cells in section["accounting"]:
            top = number(cells[0][0]).most_common(1)
            accounting.append((top[0][0] if top else -1, page, cells[0][1]))
        _, _, _, valid, share, reflected, magnitude = [value for value, _, _ in accounting]
        if valid + share != reflected:
            problems.append((code, "accounting", accounting))
        districts.append({"code": code, "plate": plate, "number": n, "magnitude": magnitude})
        for measure, (value, page, cell) in zip(
            (REGISTERED, VOTERS, None, VALID, APPORTIONED), accounting[:5], strict=True
        ):
            if measure is not None:
                values.append({"code": code, "option": None, "measure": measure, "value": value, **box(page, cell)})
        if len(section["parties"]) != len(labels):
            problems.append((code, "party rows", len(section["parties"])))
            continue
        party_rows = []
        for index, (page, cells) in enumerate(section["parties"]):
            if difflib.get_close_matches(cells[0][0][0], labels, n=1, cutoff=0.6) != [labels[index]]:
                problems.append((code, "party order", index, cells[0][0]))
            counts = [number(cell) for cell, _ in cells[1:]]
            seen += 3
            unanimous += sum(len(set(cell)) == 1 for cell, _ in cells[1:])
            apportioned = round(pool_totals[index] * valid / DOMESTIC_VALID)
            combos = [
                (counts[0][c] + counts[1][y] + counts[2][t], c, y, t)
                for c in counts[0]
                for y in counts[1]
                for t in counts[2]
                if c + y == t and abs(y - apportioned) <= 2
            ]
            if not combos:
                problems.append((code, "row", index, [cell for cell, _ in cells[1:]], apportioned))
                continue
            _, c, y, t = max(combos)
            party_rows.append((c, y, t))
            values.append(
                {"code": code, "option": names[index], "measure": VOTES, "value": c, **box(page, cells[1][1])}
            )
            values.append(
                {"code": code, "option": names[index], "measure": APPORTIONED, "value": y, **box(page, cells[2][1])}
            )
        if [sum(row[i] for row in party_rows) for i in range(3)] != [valid, share, reflected]:
            problems.append((code, "sums", valid, share, reflected))
        won: Counter[str] = Counter()
        for sequence, (page, cells) in enumerate(section.get("elected", []), 1):
            label = difflib.get_close_matches(cells[2][0][0], labels, n=1, cutoff=0.6)
            if sequence not in number(cells[0][0]) or not label:
                problems.append((code, "elected", sequence, cells))
                continue
            won[label[0]] += 1
            elected.append(
                {
                    "code": code,
                    "sequence": sequence,
                    "name": cells[1][0][0],
                    "party": names[labels.index(label[0])],
                    **box(page, cells[1][1]),
                }
            )
        declared: dict[str, int] = {}
        for _, cells in section["seats"]:
            label = difflib.get_close_matches(cells[0][0][0], labels, n=1, cutoff=0.6)
            counted = number(cells[1][0])
            if not label or not counted:
                problems.append((code, "seats", cells))
                continue
            declared[label[0]] = won[label[0]] if won[label[0]] in counted else counted.most_common(1)[0][0]
        if sum(declared.values()) != magnitude or won != Counter(declared):
            problems.append((code, "seat totals", magnitude, declared, won))
        for label, count in declared.items():
            seats.append({"code": code, "option": names[labels.index(label)], "seats": count})
            won_national[names[labels.index(label)]] += count
    if problems:
        raise ValueError(f"the parliamentary gazette does not reconcile: {problems}")
    print(f"digitized {seen} vote cells; {unanimous} unanimous across three OCR passes, the rest reconciled")

    frame = pl.DataFrame(values, schema_overrides={"box": pl.List(pl.Float32)})
    domestic = dict(frame.filter(pl.col("measure") == VOTES).group_by("option").agg(pl.col("value").sum()).iter_rows())
    if [domestic[full] for full, *_ in PARTIES] != [value for *_, value in PARTIES] or domestic[
        INDEPENDENTS
    ] != INDEPENDENTS_DOMESTIC:
        raise ValueError("district votes do not sum to the printed domestic totals")
    if (
        frame.filter(pl.col("measure") == VALID, pl.col("option").is_null())["value"].sum() + POOL[VALID]
        != NATIONAL_VALID
    ):
        raise ValueError("district and pool valid votes do not sum to the national total")
    if won_national != Counter(SEATS) or won_national.total() != 600:
        raise ValueError("district seats do not sum to the printed national seats")

    layout = scan.tables(images[2])
    accounting_rows, party_lines, alliance_lines = layout[0][0], layout[1][0], layout[2][0]
    pool = [
        {
            "unit": "abroad-customs",
            "option": None,
            "channel": 0,
            "measure": measure,
            "value": POOL[measure],
            **box(3, (772, y0, 1154, y1)),
        }
        for measure, (y0, y1) in zip(POOL, pairwise(accounting_rows))
    ]
    for (full, _, _, abroad, customs, common, _), (y0, y1) in zip(PARTIES, pairwise(party_lines[1:])):
        cells = [("abroad", 4, VOTES, abroad, 1), ("customs", 8, VOTES, customs, 2)]
        if common:
            cells.append(("abroad-customs", 0, APPORTIONED, common, 3))
        pool.extend(
            {
                "unit": unit,
                "option": full,
                "channel": channel,
                "measure": measure,
                "value": value,
                **box(3, (POOL_COLUMNS[column], y0, POOL_COLUMNS[column + 1], y1)),
            }
            for unit, channel, measure, value, column in cells
        )
    for (label, common), (y0, y1) in zip(ALLIANCES, pairwise(alliance_lines[1:])):
        pool.append(
            {
                "unit": "abroad-customs",
                "option": label,
                "channel": 0,
                "measure": VOTES,
                "value": common,
                **box(3, (682, y0, 886, y1)),
            }
        )
    if sum(int(str(row["value"])) for row in pool if row["measure"] == VOTES) != POOL[VALID]:
        raise ValueError("pool votes do not sum to the pool's valid votes")
    if sum(common for *_, common, _ in PARTIES if common) != sum(common for _, common in ALLIANCES):
        raise ValueError("distributed alliance votes do not match the alliance totals")
    pages += [render(pdf, page) for page in sorted({3, *(int(str(row["page"])) for row in (*values, *elected))})]

    pdf = raw.cached(SOURCE, LISTS)

    def recognize(image: Image.Image, cell: scan.Box, factor: float, psm: str) -> str:
        crop = image.crop(cell)
        if factor != 1:
            crop = crop.resize((round(crop.width * factor), round(crop.height * factor)), Image.Resampling.LANCZOS)
        padded = Image.new("L", (crop.width + 40, crop.height + 40), 255)
        padded.paste(crop, (20, 20))
        buffer = io.BytesIO()
        padded.save(buffer, "PNG")
        return (
            subprocess.run(
                ["tesseract", "stdin", "stdout", "--psm", psm, "-l", "tur"],
                input=buffer.getvalue(),
                capture_output=True,
                check=True,
            )
            .stdout.decode()
            .strip()
        )

    def masked_page(
        page: int,
    ) -> tuple[list[tuple[str, int, list[int], list[str], list[str]]], list[tuple[float, float, str]]]:
        image = SCANS / f"{pdf.sha256}-{page}.png"
        if not image.exists():
            with tempfile.TemporaryDirectory() as folder:
                subprocess.run(
                    [
                        "pdftoppm",
                        "-r",
                        "300",
                        "-gray",
                        "-png",
                        "-f",
                        str(page),
                        "-l",
                        str(page),
                        "-singlefile",
                        str(pdf.path),
                        f"{folder}/page",
                    ],
                    check=True,
                )
                pixels = np.asarray(Image.open(f"{folder}/page.png").convert("L")).copy()
            dark = pixels < 160
            width = pixels.shape[1]
            bands: list[list[int]] = []
            for y in np.flatnonzero(dark.any(axis=1)):
                if bands and y - bands[-1][1] <= 3:
                    bands[-1][1] = int(y)
                else:
                    bands.append([int(y), int(y)])
            for top, bottom in bands:
                xs = np.flatnonzero(dark[top : bottom + 1].any(axis=0))
                if dark[top : bottom + 1, int(width * 0.07) : int(width * 0.115)].any() or not (
                    width * 0.15 < xs[0] < width * 0.36
                ):
                    pixels[top : bottom + 1, int(width * 0.385) :] = 255
            Image.fromarray(pixels).save(image)
        cache = OCR / f"{image.stem}.json"
        if not cache.exists():
            masked = Image.open(image).convert("L")
            dark = np.asarray(masked) < 160
            width = dark.shape[1]
            lines: list[list[int]] = []
            for y in np.flatnonzero(dark.any(axis=1)):
                if lines and y - lines[-1][1] <= 3:
                    lines[-1][1] = int(y)
                else:
                    lines.append([int(y), int(y)])
            lines = [line for line in lines if line[0] >= 230 and line[1] - line[0] >= 12]
            numbered = [
                bool(dark[top : bottom + 1, int(width * 0.07) : int(width * 0.115)].any()) for top, bottom in lines
            ]
            profile = np.zeros(width, dtype=int)
            for (top, bottom), flag in zip(lines, numbered, strict=True):
                if flag:
                    profile += dark[top : bottom + 1].any(axis=0)
            gaps: list[list[int]] = []
            for x in range(int(width * 0.08), int(width * 0.13)):
                if profile[x] == 0:
                    if gaps and gaps[-1][1] == x - 1:
                        gaps[-1][1] = x
                    else:
                        gaps.append([x, x])
            shift = max(gaps, key=lambda gap: gap[1] - gap[0])[1] + 1 - 293
            items: list[list[object]] = []
            for (top, bottom), flag in zip(lines, numbered, strict=True):
                xs = np.flatnonzero(dark[top : bottom + 1].any(axis=0))
                if flag:
                    cut = 604 + shift
                    items.append(
                        [
                            "row",
                            shift,
                            [173 + shift, top - 8, int(xs[-1]) + 10, bottom + 8],
                            [
                                recognize(masked, (280 + shift, top - 8, cut, bottom + 8), f, "7")
                                for f in (1, 1.25, 1.5)
                            ],
                            [
                                recognize(masked, (cut - 4, top - 8, 958 + shift, bottom + 8), f, "7")
                                for f in (1, 1.25, 1.5)
                            ],
                        ]
                    )
                else:
                    cell = (int(xs[0]) - 10, top - 8, int(xs[-1]) + 10, bottom + 8)
                    items.append(["heading", 0, list(cell), [recognize(masked, cell, 1, "6")], []])
            table = subprocess.run(
                ["tesseract", str(image), "stdout", "--psm", "6", "-l", "tur", "tsv"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            words = [
                [int(row["left"]) + int(row["width"]) / 2, int(row["top"]) + int(row["height"]) / 2, row["text"]]
                for row in csv.DictReader(io.StringIO(table), delimiter="\t", quoting=csv.QUOTE_NONE)
                if row["level"] == "5" and row["text"].strip()
            ]
            cache.write_text(json.dumps({"items": items, "words": words}, ensure_ascii=False))
        data = json.loads(cache.read_text())
        return [tuple(item) for item in data["items"]], [tuple(word) for word in data["words"]]

    with ThreadPoolExecutor(12) as executor:
        list_pages = list(executor.map(masked_page, range(3, 426)))
    first_scan = Image.open(SCANS / f"{pdf.sha256}-3.png")
    list_width, list_height = first_scan.width, first_scan.height

    def normal(text: str) -> str:
        return " ".join(re.sub(r"[^A-ZÇĞİÖŞÜÂÎÛ ]", " ", upper(text)).split())

    ballot_headings = {upper(full): full for full, *_ in PARTIES} | {"BAĞIMSIZ": INDEPENDENTS}
    abbreviations = {full: short for full, short, *_ in PARTIES} | {INDEPENDENTS: "Bağımsız"}
    squeezed_provinces = {upper(province).replace(" ", ""): province for province in PROVINCES}
    candidates: list[dict[str, object]] = []
    unread: list[tuple[object, ...]] = []
    used: set[tuple[str, str, int, str]] = set()
    constituency: tuple[str, int] | None = None
    ticket: str | None = None
    listed_here: set[str] = set()
    skip_header = False
    position = 0
    for page, (items, words) in enumerate(list_pages, 3):
        for kind, shift, cell, forenames, surnames in items:
            if kind == "heading":
                for line in forenames[0].split("\n"):
                    squeezed = re.sub(r"[^A-ZÇĞİÖŞÜ0-9]", "", upper(line))
                    if not squeezed or "LİSTES" in squeezed or "MİLLETVEK" in squeezed:
                        continue
                    if "SEÇİM" in squeezed or "NOLU" in squeezed or "ÇEVRES" in squeezed:
                        head = re.sub(r"(-?.?NOLU|İLİ|İL)$", "", re.split(r"SEÇ", squeezed)[0])
                        match = difflib.get_close_matches(head, squeezed_provinces, 1, 0.6)
                        if not match:
                            raise ValueError(f"lists, page {page}: unknown district heading {line!r}")
                        province = squeezed_provinces[match[0]]
                        if constituency is None or constituency[0] != province:
                            constituency = (province, 1 if province in SPLIT else 0)
                            listed_here = set()
                            ticket = None
                        continue
                    match = difflib.get_close_matches(normal(line), ballot_headings, 1, 0.75)
                    if not match:
                        raise ValueError(f"lists, page {page}: unknown party heading {line!r}")
                    ticket = ballot_headings[match[0]]
                    if constituency is not None and ticket in listed_here:
                        if not constituency[1]:
                            raise ValueError(f"lists, page {page}: {ticket} listed twice in {constituency[0]}")
                        constituency = (constituency[0], constituency[1] + 1)
                        listed_here = set()
                    listed_here.add(ticket)
                    position = 0
                    skip_header = True
                continue
            if skip_header or (normal(forenames[0]) == "ADI" and normal(surnames[0]) == "SOYADI"):
                skip_header = False
                continue
            if constituency is None or ticket is None:
                raise ValueError(f"lists, page {page}: a candidate row before any district or party heading")
            position += 1
            plate = PROVINCES.index(constituency[0]) + 1
            code = f"{plate:02}-{constituency[1]}" if constituency[1] else f"{plate:02}"
            x0, top, x1, bottom = cell
            parts = []
            for field, readings, (left, right) in (
                ("forename", forenames, (270, 607)),
                ("surname", surnames, (607, 960)),
            ):
                third = " ".join(
                    text
                    for x, y, text in sorted(words, key=lambda word: (word[0], word[1]))
                    if left + shift <= x < right + shift and top <= y < bottom
                )
                texts = [normal(text) for text in readings] + [normal(third)]
                best, count = Counter(text.replace(" ", "") for text in texts).most_common(1)[0]
                override = (code, abbreviations[ticket], position, field)
                if override in NAMES_LISTS:
                    used.add(override)
                    parts.append(NAMES_LISTS[override])
                elif count >= 3 and best:
                    parts.append(
                        max((text for text in texts if text.replace(" ", "") == best), key=lambda t: t.count(" "))
                    )
                else:
                    unread.append((*override, page, texts))
            candidates.append(
                {
                    "code": code,
                    "party": ticket,
                    "position": position,
                    "name": " ".join(parts),
                    "sequence": None,
                    "url": LISTS,
                    "withdrew": False,
                    "page": page,
                    "box": [x0 / list_width, top / list_height, x1 / list_width, bottom / list_height],
                }
            )
    if unread or used != set(NAMES_LISTS):
        raise ValueError(f"lists: unread names {unread}, unused readings {set(NAMES_LISTS) - used}")
    print(f"lists: {len(candidates)} candidates, {len(NAMES_LISTS)} names read by eye")
    for page in sorted({int(str(row["page"])) for row in candidates}):
        image = PAGES / f"{pdf.sha256}-{page}.png"
        if not image.exists():
            PAGES.mkdir(parents=True, exist_ok=True)
            masked = Image.open(SCANS / f"{pdf.sha256}-{page}.png")
            masked.resize((masked.width // 3, masked.height // 3), Image.Resampling.LANCZOS).save(image)
        width, height = Image.open(image).size
        pages.append(
            {"url": LISTS, "page": page, "image": f"pages/{SOURCE}/{image.name}", "width": width, "height": height}
        )

    parliaments: dict[date, dict[str, list[dict[str, object]]]] = {}
    for day, url, standing, split, totals, independents, won_seats, query in (
        (
            DAY_2018,
            PARLIAMENT_2018,
            PARTIES_2018,
            SPLIT,
            DOMESTIC_2018,
            INDEPENDENTS_DOMESTIC_2018,
            SEATS_2018,
            MPS_2018,
        ),
        (
            DAY_2015,
            PARLIAMENT_2015,
            PARTIES_2015,
            SPLIT_2015,
            DOMESTIC_2015,
            INDEPENDENTS_DOMESTIC_2015,
            SEATS_2015,
            MPS_2015,
        ),
        (
            DAY_JUNE_2015,
            PARLIAMENT_JUNE_2015,
            PARTIES_JUNE_2015,
            SPLIT_2015,
            DOMESTIC_JUNE_2015,
            INDEPENDENTS_DOMESTIC_JUNE_2015,
            SEATS_JUNE_2015,
            MPS_JUNE_2015,
        ),
        (
            DAY_2011,
            PARLIAMENT_2011,
            PARTIES_2011,
            SPLIT_2015,
            DOMESTIC_2011,
            INDEPENDENTS_DOMESTIC_2011,
            SEATS_2011,
            MPS_2011,
        ),
    ):
        pdf = raw.cached(SOURCE, url)
        if not list(SCANS.glob(f"{pdf.sha256}-*.png")):
            subprocess.run(
                ["pdftoppm", "-r", "300", "-gray", "-png", str(pdf.path), str(SCANS / pdf.sha256)], check=True
            )
        images = sorted(SCANS.glob(f"{pdf.sha256}-*.png"))
        first = 4 if day == DAY_2011 else 6
        scanned_pages = range(first, len(images) + 1)
        with ThreadPoolExecutor(8) as executor:
            scanned = list(
                executor.map(
                    scan.words,
                    repeat(pdf.path),
                    scanned_pages,
                    [OCR / f"{pdf.sha256}-{page}.tsv" for page in scanned_pages],
                    repeat("tur"),
                )
            )
        labels = [upper(full) for full, *_ in standing] + [upper(INDEPENDENTS)]
        names = [full for full, *_ in standing] + [INDEPENDENTS]
        pool_totals = [abroad + customs + (common or 0) for _, _, _, abroad, customs, common, _ in standing] + [0]
        domestic_valid = totals[VALID]
        domestic_votes = [domestic for *_, domestic in standing] + [independents]

        def apportion(
            index: int,
            valid: int,
            votes: int,
            by_party: bool = day == DAY_2011,
            pool: list[int] = pool_totals,
            basis: list[int] = domestic_votes,
            total: int = domestic_valid,
        ) -> int:
            if by_party:
                return round(pool[index] * votes / basis[index])
            return round(pool[index] * valid / total)

        titles = {
            re.sub(r"\W", "", f"{upper(p)}İLİ({n})NUMARALISEÇİMÇEVRESİ" if n else f"{upper(p)}İLİSEÇİMÇEVRESİ"): (
                plate,
                n,
            )
            for plate, p in enumerate(PROVINCES, 1)
            for n in (range(1, split[p] + 1) if p in split else [0])
        }

        def fold(text: str) -> str:
            return re.sub(r"[^A-ZÇĞİÖŞÜ]", "", upper(text).translate(str.maketrans("ÂÎÛ", "AİU")))

        def bare(text: str) -> str:
            return fold(text).translate(str.maketrans("ÇĞİÖŞÜ", "CGIOSU"))

        statements = [
            {name: value["value"].rsplit("/", 1)[-1] for name, value in binding.items()}
            for binding in json.loads(raw.cached(SOURCE, query).path.read_text())["results"]["bindings"]
        ]
        members: dict[str, list[dict[str, str]]] = {}
        for row in statements:
            members.setdefault(row["person"], []).append(row)

        district_pages: dict[tuple[int, int], int] = {}
        repeated: list[int] = []
        accounting_lines: dict[tuple[int, int], list[tuple[scan.Box, list[str]]]] = {}
        vote_lines: dict[tuple[int, int], list[tuple[str, list[scan.Box], list[str], list[list[str]]]]] = {}
        seat_lines: dict[tuple[int, int], list[tuple[str, list[str]]]] = {}
        member_lines: dict[tuple[int, int], list[tuple[int, str, str, str, scan.Box]]] = {}
        for page, found in enumerate(scanned, first):
            slope = scan.skew(found)
            lines = scan.lines(found, slope, 18)
            texts = [" ".join(word[0] for word in line) for line in lines]
            heading = next(index for index, text in enumerate(texts[:4]) if "SEÇİM" in text)
            match = difflib.get_close_matches(re.sub(r"\W", "", texts[heading]), titles, n=1, cutoff=0.8)
            if not match and "GENEL SEÇİMİ SONUCU" in texts[heading]:
                continue
            if not match:
                raise ValueError(f"{day} page {page}: unknown district title {texts[heading]!r}")
            district = titles[match[0]]
            votes_mark, seats_mark, members_mark = [
                next((index for index, text in enumerate(texts) if re.search(pattern, text)), None)
                for pattern in ("ADAYLARA|DAĞILI", "KAZANDIKLARI|ADAYLARIN", "SEÇİLENLERİN|OLDUKLARI|AD-SOYADLARI")
            ]
            if members_mark is None:
                raise ValueError(f"{day} page {page}: no list of members")
            if votes_mark is not None and district in district_pages:
                repeated.append(page)
                continue
            image = images[page - 1]
            cache = OCR / f"{image.stem}.json"
            requests: list[tuple[scan.Box, bool]] = []
            levels = [round(scan.level(line[0], slope)) for line in lines]
            header = lines[members_mark + 1]
            split_at = next(word[1][0] for word in header if re.match("S[İI]YAS|PART", word[0])) - 15
            rows = []
            for line, level in zip(lines[members_mark + 2 :], levels[members_mark + 2 :], strict=True):
                named = [word for word in line if word[1][2] < split_at]
                if not named:
                    continue
                party = " ".join(word[0] for word in line if word[1][2] >= split_at)
                spot = (min(word[1][0] for word in named) - 10, level - 19, split_at, level + 19)
                rows.append((" ".join(word[0] for word in named), party, spot))
                requests.append((spot, False))
            accounting_cells: list[scan.Box] = []
            party_rows: list[tuple[str, list[scan.Box], list[str]]] = []
            seat_rows: list[tuple[str, str, scan.Box]] = []
            if votes_mark is not None and seats_mark is not None:
                district_pages[district] = page
                dark = np.asarray(Image.open(image).convert("L")) < 160
                top, bottom = levels[heading] + 10, levels[votes_mark] - 20
                start, end = (1150, 1460) if day == DAY_2011 else (1300, 1750)
                edges = np.flatnonzero(dark[top + 20 : bottom - 10, start:end].mean(axis=0) > 0.5) + start
                left, right = int(edges[0]), int(edges[-1])
                runs: list[list[int]] = []
                for y in np.flatnonzero(dark[top:bottom, left + 10 : right - 10].mean(axis=1) > 0.6).tolist():
                    if runs and y - runs[-1][-1] <= 3:
                        runs[-1].append(y)
                    else:
                        runs.append([y])
                rules = [top + (run[0] + run[-1]) // 2 for run in runs]
                if len(rules) != 8:
                    raise ValueError(f"{day} page {page}: found {len(rules)} accounting rules")
                accounting_cells = [(left, y0, right, y1) for y0, y1 in pairwise(rules)]
                anchor = next(word[1][0] for word in lines[votes_mark + 1] if word[0].startswith("ÇEV"))
                party_rows = []
                for line, level in zip(
                    lines[votes_mark + 2 : seats_mark], levels[votes_mark + 2 : seats_mark], strict=True
                ):
                    cells = [
                        (anchor + x0, level - 19, anchor + x1, level + 19)
                        for x0, x1 in ((-40, 140), (230, 375), (460, 610))
                    ]
                    printed = [
                        " ".join(word[0] for word in line if cell[0] <= (word[1][0] + word[1][2]) / 2 < cell[2])
                        for cell in cells
                    ]
                    label = " ".join(word[0] for word in line if word[1][2] < anchor - 50)
                    if label:
                        party_rows.append((label, cells, printed))
                seat_rows = [
                    (
                        " ".join(word[0] for word in line[:-1]),
                        line[-1][0],
                        (line[-1][1][0] - 40, level - 19, line[-1][1][2] + 40, level + 19),
                    )
                    for line, level in zip(
                        lines[seats_mark + 2 : members_mark], levels[seats_mark + 2 : members_mark], strict=True
                    )
                ]
                requests += [(cell, True) for cell in accounting_cells]
                requests += [(cell, True) for _, cells, _ in party_rows for cell in cells]
                requests += [(cell, True) for *_, cell in seat_rows]
            asked = json.loads(json.dumps(requests))
            if not cache.exists() or json.loads(cache.read_text())["requests"] != asked:
                cache.write_text(
                    json.dumps({"requests": asked, "read": scan.read(image, requests)}, ensure_ascii=False)
                )
            read = iter(json.loads(cache.read_text())["read"])
            member_lines.setdefault(district, []).extend(
                (page, text, next(read)[0], party, spot) for text, party, spot in rows
            )
            if accounting_cells:
                accounting_lines[district] = [(cell, next(read)) for cell in accounting_cells]
                vote_lines[district] = [
                    (label, cells, printed, [next(read) for _ in cells]) for label, cells, printed in party_rows
                ]
                seat_lines[district] = [(label, [*next(read), count]) for label, count, _ in seat_rows]
        if day == DAY_2018 and (
            len(district_pages) != 86
            or KAHRAMANMARAS in district_pages
            or repeated != [57]
            or district_pages[(70, 0)] != 55
        ):
            raise ValueError(f"2018: unexpected districts {len(district_pages)} or repeated pages {repeated}")
        if day != DAY_2018 and (len(district_pages) != len(titles) or repeated):
            raise ValueError(f"{day}: found {len(district_pages)} of {len(titles)} districts, repeated {repeated}")

        width, height = struct.unpack(">II", images[0].read_bytes()[16:24])

        def located(page: int, cell: scan.Box, width: int = width, height: int = height) -> dict[str, object]:
            return {"page": page, "box": [cell[0] / width, cell[1] / height, cell[2] / width, cell[3] / height]}

        problems = []
        district_rows: list[dict[str, object]] = []
        values_rows: list[dict[str, object]] = []
        seat_counts: list[dict[str, object]] = []
        elected_rows: list[dict[str, object]] = []
        claimed: set[str] = set()

        def loose(texts: list[str]) -> Counter[int]:
            found: Counter[int] = Counter()
            for text in texts:
                for value, weight in scan.numbers(text).items():
                    found[value] += weight
            return found

        def tokens(text: str) -> list[str]:
            return [
                token
                for word in upper(text).translate(str.maketrans("ÂÎÛ", "AİU")).split()
                if (token := re.sub(r"[^A-ZÇĞİÖŞÜ]", "", word))
            ]

        def plain(token: str) -> str:
            return token.translate(str.maketrans("ÇĞİÖŞÜ", "CGIOSU"))

        def agreement(picked: list[tuple[int, int, int, int]], printed: list[Counter[int]]) -> int:
            sums = [sum(row[i] for row in picked) for i in (1, 2, 3)]
            return sum(total in read for total, read in zip(sums, printed, strict=True))

        known = {person: tokens(rows[0]["label"]) for person, rows in members.items()}
        places = {
            person: " ".join(bare(row.get("districtLabel", "")) for row in rows) for person, rows in members.items()
        }
        printed_names = Counter(bare(text) for lines in member_lines.values() for _, text, *_ in lines)
        for (plate, n), page in sorted(district_pages.items()):
            code = f"{plate:02}-{n}" if n else f"{plate:02}"
            accounting = accounting_lines[(plate, n)]
            reads = [number(texts) for _, texts in accounting]
            if (day, code, VALID) in READ_PARLIAMENT:
                reads[3] = Counter({READ_PARLIAMENT[(day, code, VALID)]: 9})
            rows = vote_lines[(plate, n)]
            counts = [
                [number([*texts, text.translate(scan.CONFUSABLE)]) for texts, text in zip(passes, printed, strict=True)]
                for _, _, printed, passes in rows
            ]
            for index, full in enumerate(names):
                if (day, code, full) in VOTES_PARLIAMENT:
                    counts[index][0] = Counter({VOTES_PARLIAMENT[(day, code, full)]: 9})
            rowsum = sum(row[0].most_common(1)[0][0] for row in counts if row[0])
            for index, (label, *_) in enumerate(rows):
                if difflib.get_close_matches(label, labels, n=1, cutoff=0.6) != [labels[index]]:
                    problems.append((code, "party order", index, label))
            guesses = sorted(
                {*reads[3], *(total - share for total in reads[5] for share in reads[4])},
                key=lambda value: (
                    abs(value - rowsum) <= rowsum / 50,
                    reads[3][value]
                    + max(
                        (reads[5][total] + reads[4][total - value] for total in reads[5] if total - value in reads[4]),
                        default=0,
                    ),
                    -abs(value - rowsum),
                ),
                reverse=True,
            ) or [rowsum]
            picked: list[tuple[int, int, int, int]] = []
            for guess in guesses:
                choices = []
                for index, (cs, ys, ts) in enumerate(counts):
                    options = {
                        (cs[c] + ys[y] + ts[c + y], c, y, c + y)
                        for c in cs
                        for y in range(max(apportion(index, guess, c) - 2, 0), apportion(index, guess, c) + 3)
                    } | {
                        (cs[t - y] + ys[y] + ts[t], t - y, y, t)
                        for t in ts
                        for y in range(max(apportion(index, guess, t) - 2, 0), apportion(index, guess, t) + 3)
                        if t >= y
                    }
                    choices.append(sorted(options, reverse=True))
                if any(not options for options in choices):
                    continue
                attempt = [options[0] for options in choices]
                if agreement(attempt, reads[3:6]) < 2:
                    swaps = [
                        (
                            agreement([*attempt[:index], option, *attempt[index + 1 :]], reads[3:6]),
                            option[0],
                            index,
                            option,
                        )
                        for index, options in enumerate(choices)
                        for option in options[1:]
                    ]
                    best = max(swaps, default=None)
                    if best is None or best[0] < 2:
                        continue
                    attempt[best[2]] = best[3]
                picked = attempt
                break
            if not picked:
                problems.append((code, "sums", reads[3:6], guesses[:3]))
                continue
            valid, share, reflected = [sum(row[i] for row in picked) for i in (1, 2, 3)]
            for index, ((_, cells, _, _), (_, c, y, _)) in enumerate(zip(rows, picked, strict=True)):
                if abs(y - apportion(index, valid, c)) > 2:
                    problems.append((code, "apportioned", index, y))
                values_rows.append(
                    {"code": code, "option": names[index], "measure": VOTES, "value": c, **located(page, cells[0])}
                )
                values_rows.append(
                    {
                        "code": code,
                        "option": names[index],
                        "measure": APPORTIONED,
                        "value": y,
                        **located(page, cells[1]),
                    }
                )
            turnout = TURNOUT_PARLIAMENT.get(
                (day, code), next((found for text in accounting[2][1] if (found := scan.percent(text))), None)
            )
            printed_registered, printed_voters = loose(accounting[0][1]), loose(accounting[1][1])
            edited = [
                {
                    int(text[:i] + digit + text[i + 1 :])
                    for value in found
                    for text in [str(value)]
                    for i in range(len(text))
                    for digit in "0123456789"
                    if i or digit != "0"
                }
                for found in (printed_registered, printed_voters)
            ]
            pairs = sorted(
                (printed_registered[registered] + printed_voters[voters], registered, voters)
                for registered, voters in {(r, v) for r in printed_registered for v in {*printed_voters, *edited[1]}}
                | {(r, v) for r in edited[0] for v in printed_voters}
                if valid < voters <= registered and turnout == round(voters / registered * 100, 2)
            )[::-1]
            pairs = [
                pair
                for pair in pairs
                if READ_PARLIAMENT.get((day, code, REGISTERED), pair[1]) == pair[1]
                and READ_PARLIAMENT.get((day, code, VOTERS), pair[2]) == pair[2]
            ]
            if not pairs or (len(pairs) > 1 and pairs[0][0] == pairs[1][0]):
                problems.append((code, "turnout", accounting[0][1], accounting[1][1], turnout, pairs[:3]))
                continue
            _, registered, voters = pairs[0]
            if registered not in printed_registered or voters not in printed_voters:
                print(
                    f"{day} turnout corrects a misread:", code, accounting[0][1], accounting[1][1], registered, voters
                )
            for measure, value, index in zip(
                (REGISTERED, VOTERS, VALID, APPORTIONED), (registered, voters, valid, share), (0, 1, 3, 4), strict=True
            ):
                values_rows.append(
                    {
                        "code": code,
                        "option": None,
                        "measure": measure,
                        "value": value,
                        **located(page, accounting[index][0]),
                    }
                )
            won: Counter[str] = Counter()
            for sequence, (where, text, read, party_text, cell) in enumerate(member_lines[(plate, n)], 1):
                if (day, code, sequence) in NAMES_PARLIAMENT:
                    text = read = NAMES_PARLIAMENT[(day, code, sequence)]
                party = difflib.get_close_matches(party_text, labels, n=1, cutoff=0.6)
                if not party:
                    problems.append((code, "elected party", sequence, party_text))
                    continue
                won[names[labels.index(party[0])]] += 1
                place = bare(PROVINCES[plate - 1])
                matches = (
                    [(True, True, 0, True, ITEMS_PARLIAMENT[(day, code, sequence)], tokens(text))]
                    if (day, code, sequence) in ITEMS_PARLIAMENT
                    else [
                        (True, True, 0, True, person, tokens(text))
                        for name, person in MEMBERS_2018.items()
                        if day == DAY_2018 and bare(name) in (bare(text), bare(read))
                    ]
                    or [
                        (True, place in places[person], len(words), candidate == text, person, written)
                        for candidate in dict.fromkeys((text, read))
                        for written in [tokens(candidate)]
                        for person, words in known.items()
                        if person not in claimed
                        and (
                            day == DAY_2018
                            or place in places[person]
                            or (
                                not places[person].strip()
                                and printed_names[bare(candidate)] == 1
                                and {plain(w) for w in words} == {plain(t) for t in written}
                            )
                        )
                        and all(plain(w) in {plain(t) for t in written} for w in words)
                    ]
                    or [
                        (False, True, len(words), candidate == text, person, written)
                        for candidate in dict.fromkeys((text, read))
                        for written in [tokens(candidate)]
                        for person, words in known.items()
                        if person not in claimed
                        and place in places[person]
                        and all(
                            any(difflib.SequenceMatcher(None, plain(w), plain(t)).ratio() >= 0.8 for t in written)
                            for w in words
                        )
                    ]
                )
                if not matches and day != DAY_2018:
                    if text != read:
                        print(f"{day} member readings differ:", code, sequence, text, "|", read)
                    elected_rows.append(
                        {
                            "code": code,
                            "sequence": sequence,
                            "name": title(" ".join(tokens(text))),
                            "party": names[labels.index(party[0])],
                            "person": None,
                            "label": title(" ".join(tokens(text))),
                            **located(where, cell),
                        }
                    )
                    continue
                if not matches:
                    problems.append((code, "unmatched member", text, read))
                    continue
                matches.sort(reverse=True)
                exact, _, _, _, person, written = matches[0]
                rivals = [match for match in matches[1:] if match[:3] == matches[0][:3] and match[4] != person]
                if rivals:
                    problems.append((code, "ambiguous member", text, read, [match[4] for match in matches[:3]]))
                    continue
                if not exact:
                    print(f"{day} member read loosely:", code, text, "|", read, "->", members[person][0]["label"])
                claimed.add(person)
                spelled = {plain(word): word for word in known.get(person, [])}
                elected_rows.append(
                    {
                        "code": code,
                        "sequence": sequence,
                        "name": title(" ".join(spelled.get(plain(word), word) for word in written)),
                        "party": names[labels.index(party[0])],
                        "person": person,
                        "label": members[person][0]["label"] if person in members else title(" ".join(written)),
                        **located(where, cell),
                    }
                )
            declared: Counter[str] = Counter()
            for label, texts in seat_lines[(plate, n)]:
                party = difflib.get_close_matches(label, labels, n=1, cutoff=0.6)
                read = number([text.translate(scan.CONFUSABLE) for text in texts])
                full = names[labels.index(party[0])] if party else None
                if full is not None and (won[full] in read or (won[full] and won.total() in loose(accounting[6][1]))):
                    declared[full] = won[full]
                elif full is not None and read:
                    declared[full] = read.most_common(1)[0][0]
                else:
                    problems.append((code, "seats", label, texts))
            magnitude = won.total()
            if won != declared:
                problems.append((code, "seat totals", accounting[6][1], declared, won))
            if magnitude not in loose(accounting[6][1]):
                print(f"{day} district magnitude from its seats:", code, magnitude, accounting[6][1])
            district_rows.append({"code": code, "plate": plate, "number": n, "magnitude": magnitude})
            seat_counts += [{"code": code, "option": option, "seats": count} for option, count in declared.items()]
        if problems:
            for problem in problems:
                print(problem)
            raise ValueError(f"the {day} parliamentary gazette does not reconcile: {len(problems)} problems")

        summed = {
            (option, measure): value
            for option, measure, value in pl.DataFrame(values_rows)
            .group_by("option", "measure")
            .agg(pl.col("value").sum())
            .iter_rows()
        }
        tenure_rows: list[dict[str, object]] = []
        if day == DAY_2018:
            code = f"{KAHRAMANMARAS[0]:02}"
            valid = DOMESTIC_2018[VALID] - summed[(None, VALID)]
            if DOMESTIC_2018[REGISTERED] - summed[(None, REGISTERED)] >= DOMESTIC_2018[VOTERS] - summed[(None, VOTERS)]:
                raise ValueError(
                    "2018: Kahramanmaraş's registered voters can now be derived; store them and fix the caveat"
                )
            derived: list[tuple[str | None, int, int]] = [
                (None, VOTERS, DOMESTIC_2018[VOTERS] - summed[(None, VOTERS)]),
                (None, VALID, valid),
                (None, APPORTIONED, round(POOL_2018[VALID] * valid / domestic_valid)),
            ]
            for index, full in enumerate(names):
                domestic = INDEPENDENTS_DOMESTIC_2018 if full == INDEPENDENTS else PARTIES_2018[index][6]
                derived.append((full, VOTES, domestic - summed[(full, VOTES)]))
                derived.append((full, APPORTIONED, round(pool_totals[index] * valid / domestic_valid)))
            if (
                sum(value for option, measure, value in derived if option is not None and measure == VOTES) != valid
                or sum(value for option, measure, value in derived if option is not None and measure == APPORTIONED)
                != derived[2][2]
            ):
                raise ValueError("2018: Kahramanmaraş's derived votes do not add up")
            values_rows += [
                {"code": code, "option": option, "measure": measure, "value": value, "page": None, "box": None}
                for option, measure, value in derived
            ]
            won_elsewhere = Counter[str]()
            for row in seat_counts:
                won_elsewhere[str(row["option"])] += int(str(row["seats"]))
            remaining = {
                party: count - won_elsewhere[party]
                for party, count in SEATS_2018.items()
                if count > won_elsewhere[party]
            }
            district_rows.append(
                {
                    "code": code,
                    "plate": KAHRAMANMARAS[0],
                    "number": KAHRAMANMARAS[1],
                    "magnitude": sum(remaining.values()),
                }
            )
            seat_counts += [{"code": code, "option": option, "seats": count} for option, count in remaining.items()]
            listed = sorted(
                (
                    (names.index(GROUPS_2018[rows[0]["group"]]), rows[0]["label"], person)
                    for person, rows in members.items()
                    if any(bare(PROVINCES[KAHRAMANMARAS[0] - 1]) in bare(row.get("districtLabel", "")) for row in rows)
                ),
            )
            if Counter(names[index] for index, _, _ in listed) != Counter(remaining):
                raise ValueError(
                    f"2018: Kahramanmaraş's members on Wikidata do not match its derived seats {remaining}"
                )
            elected_rows += [
                {
                    "code": code,
                    "sequence": sequence,
                    "name": label,
                    "party": names[index],
                    "person": person,
                    "label": label,
                    "page": None,
                    "box": None,
                }
                for sequence, (index, label, person) in enumerate(listed, 1)
            ]
            claimed.update(person for *_, person in listed)
            for person in sorted(claimed):
                start: date | None = MP_START_2018
                opened = None
                for row in sorted(
                    {row["statement"]: row for row in members.get(person, [])}.values(),
                    key=lambda row: row.get("start", ""),
                ):
                    if start is None:
                        start, opened = date.fromisoformat(row["start"][:10]), row["statement"]
                    died = "end" in row and row["end"] == row.get("death")
                    if row.get("cause") in ENDINGS_2018 or died:
                        tenure_rows.append(
                            {
                                "person": person,
                                "start": start,
                                "end": date.fromisoformat(row["end"][:10]),
                                "ended_via": "died" if died else ENDINGS_2018[row["cause"]],
                                "cause": row.get("cause"),
                                "statement": row["statement"],
                            }
                        )
                        start = None
                if start is not None and opened is not None:
                    tenure_rows.append(
                        {
                            "person": person,
                            "start": start,
                            "end": None,
                            "ended_via": None,
                            "cause": None,
                            "statement": opened,
                        }
                    )
        else:
            mismatched = {
                key: (summed.get(key), value)
                for key, value in [
                    *(((full, VOTES), domestic) for full, *_, domestic in standing),
                    ((INDEPENDENTS, VOTES), independents),
                    ((None, REGISTERED), totals[REGISTERED] + REGISTERED_SURPLUS.get(day, 0)),
                    *(((None, measure), totals[measure]) for measure in (VOTERS, VALID)),
                ]
                if summed.get(key) != value
            }
            if mismatched:
                raise ValueError(f"{day}: district sums differ from the printed domestic totals: {mismatched}")
        national_seats = Counter[str]()
        for row in seat_counts:
            national_seats[str(row["option"])] += int(str(row["seats"]))
        if national_seats != Counter(won_seats) or len(elected_rows) != national_seats.total():
            raise ValueError(f"{day}: district seats do not sum to the printed national seats")

        pool_rows: list[dict[str, object]] = []
        if day == DAY_2018:
            pool_rows += [
                {
                    "unit": "abroad-customs",
                    "option": None,
                    "channel": 0,
                    "measure": measure,
                    "value": value,
                    **located(3, (776, y0, 1156, y1)),
                }
                for (measure, value), (y0, y1) in zip(
                    POOL_2018.items(), pairwise([557, 604, 650, 697, 743]), strict=True
                )
            ]
            party_lines = [1018, 1079, 1141, 1203, 1264, 1327, 1389, 1451, 1513]
            for (full, _, _, abroad, customs, common, _), (y0, y1) in zip(
                PARTIES_2018, pairwise(party_lines), strict=True
            ):
                cells = [("abroad", 4, VOTES, abroad, (687, 890)), ("customs", 8, VOTES, customs, (890, 1092))]
                if common:
                    cells.append(("abroad-customs", 0, APPORTIONED, common, (1092, 1295)))
                pool_rows += [
                    {
                        "unit": unit,
                        "option": full,
                        "channel": channel,
                        "measure": measure,
                        "value": value,
                        **located(3, (x0, y0, x1, y1)),
                    }
                    for unit, channel, measure, value, (x0, x1) in cells
                ]
            for (label, common), (y0, y1) in zip(ALLIANCES_2018, pairwise([1730, 1779, 1829]), strict=True):
                pool_rows.append(
                    {
                        "unit": "abroad-customs",
                        "option": label,
                        "channel": 0,
                        "measure": VOTES,
                        "value": common,
                        **located(3, (687, y0, 891, y1)),
                    }
                )
            if sum(int(str(row["value"])) for row in pool_rows if row["measure"] == VOTES) != POOL_2018[VALID]:
                raise ValueError("2018: pool votes do not sum to the pool's valid votes")
        else:
            for page, unit, channel, accounting_values, column in POOL_PAGES[day]:
                found = scan.tables(images[page - 1])
                if day == DAY_2011:
                    found = [found[1], (found[2][0], [14, 989, 1267, 1411])]
                (account_rows, account_columns), *fragments = found
                option_columns = fragments[-1][1]
                lines = sorted(
                    {y for rows, columns in fragments if abs(columns[1] - option_columns[1]) < 6 for y in rows}
                )
                pitch = sorted(b - a for a, b in pairwise(lines))[len(lines) // 2]
                option_rows = [
                    round(a + step * (b - a) / steps)
                    for a, b in pairwise(lines)
                    for steps in [max(1, round((b - a) / pitch))]
                    for step in range(steps)
                ] + [lines[-1]]
                pool_rows += [
                    {
                        "unit": unit,
                        "option": None,
                        "channel": 0 if measure == REGISTERED else channel,
                        "measure": measure,
                        "value": value,
                        **located(page, (account_columns[1], y0, account_columns[2], y1)),
                    }
                    for (measure, value), (y0, y1) in zip(
                        accounting_values.items(), pairwise(account_rows), strict=False
                    )
                    if value is not None
                ]
                pool_rows += [
                    {
                        "unit": unit,
                        "option": party[0],
                        "channel": channel,
                        "measure": VOTES,
                        "value": party[column],
                        **located(page, (option_columns[1], y0, option_columns[2], y1)),
                    }
                    for party, (y0, y1) in zip(standing, pairwise(option_rows[1:]), strict=False)
                ]
                if (
                    sum(int(str(row["value"])) for row in pool_rows if row["unit"] == unit and row["measure"] == VOTES)
                    != accounting_values[VALID]
                ):
                    raise ValueError(f"{day}: {unit} votes do not sum to the valid votes")
        parliaments[day] = {
            "districts": district_rows,
            "district_values": values_rows,
            "seats": seat_counts,
            "elected": elected_rows,
            "pool": pool_rows,
            "tenures": tenure_rows,
        }
        pages += [
            render(pdf, page)
            for page in sorted(
                {
                    *(int(str(row["page"])) for row in pool_rows),
                    *(int(str(row["page"])) for row in (*values_rows, *elected_rows) if row["page"] is not None),
                }
            )
        ]

    referendums: dict[int, list[dict[str, object]]] = {}
    columns_2017 = [(REGISTERED, None, 2), (VOTERS, None, 3), (VALID, None, 4), (INVALID, None, 5)] + [
        (VOTES, answer, cell) for answer, cell in zip(ANSWERS_2017, (6, 8), strict=True)
    ]
    for day, url, pins, domestic_totals, pools, national_totals, layout in (
        (DAY_2017, REFERENDUM_2017, READ_2017, DOMESTIC_2017, POOL_2017, NATIONAL_2017, (3, 4, 5)),
        (DAY_2010, REFERENDUM_2010, READ_2010, DOMESTIC_2010, POOL_2010, NATIONAL_2010, (2, 3, 4)),
    ):
        pdf = raw.cached(SOURCE, url)
        if not list(SCANS.glob(f"{pdf.sha256}-*.png")):
            subprocess.run(
                ["pdftoppm", "-r", "300", "-gray", "-png", str(pdf.path), str(SCANS / pdf.sha256)], check=True
            )
        images = sorted(SCANS.glob(f"{pdf.sha256}-*.png"))
        referendum = referendums.setdefault(day.year, [])
        plate = 0
        for page in layout:
            image = images[page - 1]
            width, height = struct.unpack(">II", image.read_bytes()[16:24])
            dark = np.asarray(Image.open(image).convert("L")) < 160
            if day == DAY_2017:
                rules = scan.tables(image)[0][0][1:]
            else:
                lines: list[list[int]] = []
                for y in np.flatnonzero(dark[:, 300:1400].mean(axis=1) > 0.5).tolist():
                    if lines and y - lines[-1][-1] <= 3:
                        lines[-1].append(y)
                    else:
                        lines.append([y])
                found = [(line[0] + line[-1]) // 2 for line in lines if line[0] > 200]
                pitch = float(np.median([b - a for a, b in pairwise(found) if b - a < 70]))
                rules = [found[0]]
                for y in found[1:]:
                    steps = round((y - rules[-1]) / pitch)
                    rules += [round(rules[-1] + (y - rules[-1]) * step / steps) for step in range(1, steps + 1)]
                rules = rules[:39]
            spans = (
                [dark[rules[0] + 10 : rules[-1] - 10].mean(axis=0) > 0.5] * 38
                if day == DAY_2017
                else [dark[y0 + 6 : y1 - 6].mean(axis=0) > 0.7 for y0, y1 in pairwise(rules)]
            )
            grid: list[list[int]] = []
            for span in spans:
                edges: list[list[int]] = []
                for x in np.flatnonzero(span).tolist():
                    if edges and x - edges[-1][-1] <= 6:
                        edges[-1].append(x)
                    else:
                        edges.append([x])
                columns = [(edge[0] + edge[-1]) // 2 for edge in edges]
                grid.append(columns if len(columns) == 11 or not grid else grid[-1])
            cells = [
                [(x0, y0, x1, y1) for x0, x1 in pairwise(columns)]
                for (y0, y1), columns in zip(pairwise(rules), grid, strict=True)
            ][: 5 if page == layout[-1] else 38]
            if len(rules) != 39 or any(len(row) != 10 for row in cells):
                raise ValueError(f"{day}: unexpected table on page {page}")
            requests = [(cell, index > 0) for row in cells for index, cell in enumerate(row)]
            cache = OCR / f"{image.stem}.json"
            asked = json.loads(json.dumps(requests))
            if not cache.exists() or json.loads(cache.read_text())["requests"] != asked:
                cache.write_text(
                    json.dumps({"requests": asked, "read": scan.read(image, requests)}, ensure_ascii=False)
                )
            read = iter(json.loads(cache.read_text())["read"])
            for row in cells:
                texts = [next(read) for _ in row]
                plate += 1
                code = f"{plate:02}"
                if difflib.SequenceMatcher(None, upper(PROVINCES[plate - 1]), texts[0][0].strip(" .,")).ratio() < 0.7:
                    raise ValueError(f"{day}: expected {PROVINCES[plate - 1]} on page {page}, read {texts[0]}")
                reads = [
                    Counter({pins[(code, measure)]: 9})
                    if (code, measure) in pins
                    else Counter(
                        int(digits)
                        for text in texts[cell]
                        if (digits := re.sub(r"\D", "", text.translate(scan.CONFUSABLE)))
                    )
                    for measure, _, cell in columns_2017
                ]
                shares = [
                    {share for text in texts[cell] if (share := scan.percent(text)) is not None} for cell in (7, 9)
                ]
                scored = sorted(
                    (
                        reads[4][yes]
                        + reads[5][no]
                        + reads[2][yes + no]
                        + reads[3][spoiled]
                        + reads[1][yes + no + spoiled],
                        [yes + no + spoiled, yes + no, spoiled, yes, no],
                    )
                    for yes in set(reads[4]) | {valid - no for valid in reads[2] for no in reads[5]}
                    for no in set(reads[5]) | {valid - yes for valid in reads[2] for yes in reads[4]}
                    for spoiled in set(reads[3]) | {voters - valid for voters in reads[1] for valid in reads[2]}
                    if yes > 0
                    and no > 0
                    and spoiled >= 0
                    and (round(yes * 100 / (yes + no), 2) in shares[0] or round(no * 100 / (yes + no), 2) in shares[1])
                )[::-1]
                if not scored or scored[0][0] < 9 or (len(scored) > 1 and scored[1][0] == scored[0][0]):
                    raise ValueError(f"{day}: cannot reconcile {PROVINCES[plate - 1]}: {texts} {scored[:3]}")
                registered = reads[0].most_common()
                if len(registered) > 1 and registered[0][1] == registered[1][1] or registered[0][0] < scored[0][1][0]:
                    raise ValueError(f"{day}: unclear registered voters in {PROVINCES[plate - 1]}: {registered}")
                for (measure, option, cell), value in zip(columns_2017, [registered[0][0], *scored[0][1]], strict=True):
                    x0, y0, x1, y1 = row[cell]
                    referendum.append(
                        {
                            "unit": code,
                            "option": option,
                            "channel": 0,
                            "measure": measure,
                            "value": value,
                            "page": page,
                            "box": [x0 / width, y0 / height, x1 / width, y1 / height],
                        }
                    )
        summed = [sum(int(str(row["value"])) for row in referendum[index::6]) for index in range(6)]
        if plate != 81 or summed != domestic_totals:
            raise ValueError(f"{day}: provinces sum to {summed}, not the domestic totals {domestic_totals}")
        for unit, (channel, page, printed) in pools.items():
            image = images[page - 1]
            width, height = struct.unpack(">II", image.read_bytes()[16:24])
            found = sorted(
                scan.words(pdf.path, page, OCR / f"{pdf.sha256}-{page}.tsv", "tur"), key=lambda word: word[1][1]
            )
            spots: dict[int, scan.Box] = {}
            for word in found:
                if re.fullmatch(r"\d+|\d{1,3}(\.\d{3})+", digits := re.sub(r"[^\d.]", "", word[0]).strip(".")):
                    spots.setdefault(int(digits.replace(".", "")), word[1])
            if missing := {value for value in printed if value is not None and value not in spots}:
                spotted = {word[2] for word in found if word[1] in [spots.get(value or 0) for value in printed]}
                boxes = [word[1] for word in found if word[2] in spotted]
                for spot, texts in zip(
                    boxes,
                    scan.read(image, [((x0 - 8, y0 - 8, x1 + 8, y1 + 8), True) for x0, y0, x1, y1 in boxes]),
                    strict=True,
                ):
                    for text in texts:
                        if (digits := re.sub(r"\D", "", text)) and int(digits) in missing:
                            spots.setdefault(int(digits), spot)
            for index, value in enumerate(printed):
                if value is not None and value not in spots and printed[index - 1] is not None:
                    above, higher = spots[printed[index - 1] or 0], spots[printed[index - 2] or 0]
                    pitch = above[1] - higher[1]
                    guess = (above[0] - 30, above[1] + pitch - 8, above[2] + 8, above[3] + pitch + 8)
                    if str(value) not in [re.sub(r"\D", "", text) for text in scan.read(image, [(guess, True)])[0]]:
                        raise ValueError(f"{day}: {value} not found on page {page}")
                    spots[value] = guess
            for (measure, option, _), value in zip(columns_2017, printed, strict=True):
                if value is None:
                    continue
                x0, y0, x1, y1 = spots[value]
                referendum.append(
                    {
                        "unit": unit,
                        "option": option,
                        "channel": channel,
                        "measure": measure,
                        "value": value,
                        "page": page,
                        "box": [(x0 - 8) / width, (y0 - 8) / height, (x1 + 8) / width, (y1 + 8) / height],
                    }
                )
        if [
            domestic + sum(printed[index] or 0 for _, _, printed in pools.values())
            for index, domestic in enumerate(domestic_totals)
        ] != national_totals:
            raise ValueError(f"{day}: domestic, abroad, and customs totals do not add up to the national totals")
        pages += [render(pdf, page) for page in (*layout, *(page for _, page, _ in pools.values()))]

    doc = [
        line.strip()
        for line in subprocess.run(
            ["textutil", "-format", "doc", "-convert", "txt", "-stdout", str(raw.cached(SOURCE, PARLIAMENT_2007).path)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        if line.strip()
    ]
    headings = [index for index, line in enumerate(doc) if line.endswith(" SEÇİM ÇEVRESİ")]
    blocks: dict[str, list[str]] = {}
    for first, last in pairwise([*headings, len(doc)]):
        block = doc[first + 1 : last]
        if doc[first] in blocks:
            blocks[doc[first]] += block[block.index("PARTİ ADI") + 1 :]
        else:
            blocks[doc[first]] = block
    labels = {upper(full): full for full, *_ in PARTIES_2007} | {"BAĞIMSIZLAR": INDEPENDENTS, "BAĞIMSIZ": INDEPENDENTS}
    ozonder = json.loads(raw.cached(SOURCE, OZONDER_ITEM).path.read_text())["entities"][OZONDER]["labels"]["tr"][
        "value"
    ]
    parsed_2007: dict[str, list[dict[str, object]]] = {
        "districts": [],
        "district_values": [],
        "seats": [],
        "elected": [],
        "pool": [],
    }
    for heading, block in blocks.items():
        match = re.fullmatch(r"(.+) İLİ(?:\s+\(\s*(\d)\s*\) NUMARALI)? SEÇİM ÇEVRESİ", heading)
        if match is None:
            raise ValueError(f"{DAY_2007}: unexpected district heading {heading}")
        plate = [upper(province) for province in PROVINCES].index(match[1]) + 1
        numbered = int(match[2] or 0)
        code = f"{plate:02}-{numbered}" if numbered else f"{plate:02}"
        at = block.index("ÇEVRENİN MİLLETVEKİLİ SAYISI") + 1
        registered, voters, turnout, valid, customs, _, magnitude = block[at : at + 7]
        first = block.index("TOPLAM") + 1
        stop = block.index("SİYASİ PARTİLERİN KAZANDIKLARI MİLLETVEKİLİ SAYILARI")
        votes = [
            (labels[block[index]], *(int(value.replace(".", "")) for value in block[index + 1 : index + 4]))
            for index in range(first, stop, 4)
        ]
        listed = block.index("MİLLETVEKİLİ SEÇİLENLERİN AD-SOYADLARI İLE BAĞLI OLDUKLARI SİYASİ PARTİ")
        seated = {labels[block[index]]: int(block[index + 1]) for index in range(stop + 3, listed, 2)}
        listing = [(block[index], labels[block[index + 1]]) for index in range(listed + 3, len(block), 2)]
        accounting = {
            REGISTERED: int(registered.replace(".", "")),
            VOTERS: int(voters.replace(".", "")),
            VALID: int(valid.replace(".", "")),
            APPORTIONED: int(customs.replace(".", "")),
        }
        if (
            [label for label, *_ in votes] != [*(full for full, *_ in PARTIES_2007), INDEPENDENTS]
            or sum(district for _, district, _, _ in votes) != accounting[VALID]
            or sum(share for _, _, share, _ in votes) != accounting[APPORTIONED]
            or any(district + share != total for _, district, share, total in votes)
            or sum(seated.values()) != int(magnitude)
            or Counter(party for _, party in listing) != Counter(seated)
            or f"%{accounting[VOTERS] * 100 / accounting[REGISTERED]:.2f}".replace(".", ",") != turnout
        ):
            raise ValueError(f"{DAY_2007}: {heading} does not add up")
        parsed_2007["districts"].append({"code": code, "plate": plate, "number": numbered, "magnitude": int(magnitude)})
        parsed_2007["district_values"] += [
            {"code": code, "option": None, "measure": measure, "value": value, "page": None, "box": None}
            for measure, value in accounting.items()
        ] + [
            {"code": code, "option": label, "measure": measure, "value": value, "page": None, "box": None}
            for label, district, share, _ in votes
            for measure, value in ((VOTES, district), (APPORTIONED, share))
        ]
        parsed_2007["seats"] += [{"code": code, "option": label, "seats": seats} for label, seats in seated.items()]
        parsed_2007["elected"] += [
            {
                "code": code,
                "sequence": sequence,
                "name": title(printed),
                "party": party,
                "person": OZONDER if printed == DEATH_MARKER else None,
                "label": ozonder if printed == DEATH_MARKER else title(printed),
                "page": None,
                "box": None,
            }
            for sequence, (printed, party) in enumerate(listing, 1)
        ]
    summed: Counter[object] = Counter()
    for row in parsed_2007["district_values"]:
        summed[(row["option"], row["measure"])] += int(str(row["value"]))
    for row in parsed_2007["seats"]:
        summed[row["option"]] += int(str(row["seats"]))
    if (
        len(blocks) != 85
        or any(summed[(None, measure)] != DOMESTIC_2007[measure] for measure in (REGISTERED, VOTERS, VALID))
        or summed[(None, APPORTIONED)] != CUSTOMS_2007[VALID]
        or any(
            (summed[(full, VOTES)], summed[(full, APPORTIONED)]) != (domestic, share)
            for full, _, _, _, share, _, domestic in PARTIES_2007
        )
        or (summed[(INDEPENDENTS, VOTES)], summed[(INDEPENDENTS, APPORTIONED)]) != (INDEPENDENTS_DOMESTIC_2007, 0)
        or any(summed[label] != seats for label, seats in SEATS_2007.items())
        or any(DOMESTIC_2007[measure] + CUSTOMS_2007[measure] != NATIONAL_2007[measure] for measure in NATIONAL_2007)
        or CUSTOMS_2007[VOTERS] != CUSTOMS_2007[VALID] + CUSTOMS_2007[INVALID]
    ):
        raise ValueError(f"{DAY_2007}: districts do not add up to the domestic and customs totals")
    parsed_2007["pool"] = [
        {
            "unit": "customs",
            "option": None,
            "channel": 0 if measure == REGISTERED else CHANNELS["customs"],
            "measure": measure,
            "value": value,
            "page": None,
            "box": None,
        }
        for measure, value in CUSTOMS_2007.items()
    ] + [
        {
            "unit": "customs",
            "option": full,
            "channel": CHANNELS["customs"],
            "measure": VOTES,
            "value": share,
            "page": None,
            "box": None,
        }
        for full, _, _, _, share, _, _ in PARTIES_2007
    ]
    parliaments[DAY_2007] = parsed_2007

    edge = 322.0
    printed: list[dict[str, object]] = []
    for day, standing, split in (
        (DAY_2018, PARTIES_2018, SPLIT),
        (DAY_2015, PARTIES_2015, SPLIT_2015),
        (DAY_JUNE_2015, PARTIES_JUNE_2015, SPLIT_2015),
        (DAY_2011, PARTIES_2011, SPLIT_2015),
    ):
        list_url = LIST_URLS[day]
        pdf = raw.cached(SOURCE, list_url)
        district_headings = {f"{upper(province)} İLİ": f"{plate:02}" for plate, province in enumerate(PROVINCES, 1)} | {
            f"{upper(province)}-{number} NOLU": f"{plate:02}-{number}"
            for plate, province in enumerate(PROVINCES, 1)
            for number in range(1, split.get(province, 0) + 1)
        }
        list_headings = {upper(full): full for full, *_ in standing} | {"BAĞIMSIZ": INDEPENDENTS}
        printed_district: str | None = None
        printed_ticket: str | None = None
        position = 0
        layout = subprocess.run(
            ["pdftotext", "-bbox", str(pdf.path), "-"], capture_output=True, check=True
        ).stdout.decode()
        for page, text in enumerate(layout.split("<page ")[1:], 1):
            page_width, page_height = (float(value) for value in re.findall(r'(?:width|height)="([\d.]+)"', text)[:2])
            lines: dict[float, list[tuple[float, float, float, float, str]]] = {}
            for x0, y0, x1, y1, word in re.findall(
                r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>', text
            ):
                lines.setdefault(round(float(y0), 1), []).append((float(x0), float(y0), float(x1), float(y1), word))
            numbered = [y for y, words in lines.items() if min(words)[0] < 51 and min(words)[4].isdigit()]
            for y, words in list(lines.items()):
                if (50 <= min(words)[0] < 56 or 176 <= min(words)[0] < 181) and not min(words)[4].isdigit():
                    nearest = min(numbered, key=lambda row: abs(row - y))
                    if abs(nearest - y) > 8:
                        raise ValueError(f"lists {list_url}, page {page}: a wrapped name far from any row at {y}")
                    lines[nearest] += lines.pop(y)
            kept: list[tuple[float, float]] = []
            for _, words in sorted(lines.items()):
                words.sort()
                if words[0][0] >= edge:
                    continue
                if words[0][0] >= 53:
                    kept.append((words[0][1], max(word[3] for word in words)))
                    line = " ".join(word[4] for word in words)
                    if line.endswith(" SEÇİM ÇEVRESİ"):
                        printed_district = district_headings[line.removesuffix(" SEÇİM ÇEVRESİ")]
                    elif line in list_headings:
                        printed_ticket = list_headings[line]
                        position = 0
                    elif line != "MİLLETVEKİLİ SEÇİMLERİ KESİN ADAY LİSTESİ" and not re.fullmatch(
                        r"\d+ / \d+( \S+ \S+)?", line
                    ):
                        raise ValueError(f"lists {list_url}, page {page}: unknown heading {line!r}")
                    continue
                if words[0][4] == "S.":
                    continue
                if printed_district is None or printed_ticket is None:
                    raise ValueError(
                        f"lists {list_url}, page {page}: a candidate row before any district or party heading"
                    )
                position += 1
                if printed_ticket != INDEPENDENTS and int(words[0][4]) != position:
                    raise ValueError(
                        f"lists {list_url}, page {page}: rank {words[0][4]} printed at position {position}"
                    )
                printed.append(
                    {
                        "code": printed_district,
                        "party": printed_ticket,
                        "position": position,
                        "name": " ".join(
                            word[4]
                            for word in sorted(words[1:], key=lambda word: (word[0] >= 175, word[1], word[0]))
                            if word[2] <= edge
                        ),
                        "sequence": None,
                        "url": list_url,
                        "withdrew": (list_url, printed_district, printed_ticket, position) in WITHDRAWN,
                        "page": page,
                        "box": [
                            (words[0][0] - 2) / page_width,
                            (min(word[1] for word in words) - 2) / page_height,
                            edge / page_width,
                            (max(word[3] for word in words) + 2) / page_height,
                        ],
                    }
                )
            image = PAGES / f"{pdf.sha256}-{page}.png"
            if not image.exists():
                PAGES.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory() as folder:
                    subprocess.run(
                        ["pdftoppm", "-r", "100", "-png", "-gray", "-f", str(page), "-l", str(page), "-singlefile"]
                        + [str(pdf.path), f"{folder}/page"],
                        check=True,
                    )
                    pixels = np.asarray(Image.open(f"{folder}/page.png").convert("L")).copy()
                ratio = pixels.shape[1] / page_width
                keep = np.zeros(pixels.shape[0], dtype=bool)
                for top, bottom in kept:
                    keep[int((top - 2) * ratio) : int((bottom + 2) * ratio) + 1] = True
                pixels[~keep, int(edge * ratio) :] = 255
                Image.fromarray(pixels).save(image)
            width, height = Image.open(image).size
            pages.append(
                {
                    "url": list_url,
                    "page": page,
                    "image": f"pages/{SOURCE}/{image.name}",
                    "width": width,
                    "height": height,
                }
            )

    folding = str.maketrans("ÇĞİÖŞÜÂÎÛ", "CGIOSUAIU")
    for list_url, list_rows, list_districts, list_values, list_elected in (
        (LISTS, candidates, districts, values, elected),
        *(
            (
                list_url,
                [row for row in printed if row["url"] == list_url],
                parliaments[day]["districts"],
                parliaments[day]["district_values"],
                parliaments[day]["elected"],
            )
            for day, list_url in LIST_URLS.items()
        ),
    ):
        lists: dict[tuple[str, str], list[dict[str, object]]] = {}
        for row in list_rows:
            lists.setdefault((str(row["code"]), str(row["party"])), []).append(row)
        magnitudes = {str(row["code"]): int(str(row["magnitude"])) for row in list_districts}
        voted = {
            (str(row["code"]), str(row["option"]))
            for row in list_values
            if row["measure"] == VOTES and row["option"] is not None and int(str(row["value"])) > 0
        }
        if set(lists) != voted:
            raise ValueError(
                f"lists {list_url}: lists without votes {set(lists) - voted}, votes without lists {voted - set(lists)}"
            )
        if wrong := [
            (pair, len(rows))
            for pair, rows in lists.items()
            if pair[1] != INDEPENDENTS and len(rows) != magnitudes[pair[0]]
        ]:
            raise ValueError(f"lists {list_url}: lists whose length is not the district magnitude {wrong}")
        winners: dict[tuple[str, str], dict[str, object]] = {}
        for row in list_elected:
            folded = re.sub(r"[^A-Z]", "", upper(str(row["name"])).translate(folding))
            winners.setdefault((str(row["code"]), str(row["party"])), {})[folded] = row["sequence"]
        for pair, sequences in winners.items():
            standing_rows = [row for row in lists[pair] if not row["withdrew"]]
            for row in standing_rows if pair[1] == INDEPENDENTS else standing_rows[: len(sequences)]:
                folded = re.sub(r"[^A-Z]", "", upper(str(row["name"])).translate(folding))
                match = difflib.get_close_matches(folded, sequences, 1, 0.9)
                if match:
                    row["sequence"] = sequences.pop(match[0])
            if sequences:
                raise ValueError(f"lists {list_url}: {pair} elected members not first on the list {sorted(sequences)}")
        print(f"lists: {len(list_rows)} candidates on {len(lists)} lists from {list_url}")
    if sum(bool(row["withdrew"]) for row in printed) != len(WITHDRAWN):
        raise ValueError("lists: a withdrawn candidate is missing from the lists")

    tables = [
        [
            line.strip()
            for line in subprocess.run(
                ["textutil", "-format", "rtf", "-convert", "txt", "-stdout", str(raw.cached(SOURCE, url).path)],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.splitlines()
            if line.strip()
        ]
        for url in (REFERENDUM_2007, REFERENDUM_2007_CUSTOMS)
    ]
    referendum = referendums.setdefault(DAY_REFERENDUM_2007.year, [])
    for unit, channel, table, at in [
        *(
            (f"{plate:02}", 0, tables[0], tables[0].index(upper(province)) + 1)
            for plate, province in enumerate(PROVINCES, 1)
        ),
        ("customs", CHANNELS["customs"], tables[1], tables[1].index("TOPLAM") - 7),
    ]:
        _, voters, valid, invalid, registered, yes, no = (int(value.replace(".", "")) for value in table[at : at + 7])
        if voters != valid + invalid or valid != yes + no or registered < voters:
            raise ValueError(f"{DAY_REFERENDUM_2007}: {unit} does not add up")
        referendum += [
            {
                "unit": unit,
                "option": option,
                "channel": channel,
                "measure": measure,
                "value": value,
                "page": None,
                "box": None,
            }
            for (measure, option, _), value in zip(
                columns_2017, (registered, voters, valid, invalid, yes, no), strict=True
            )
        ]
    domestic_2007 = [sum(int(str(row["value"])) for row in referendum[index : 81 * 6 : 6]) for index in range(6)]
    customs_2007 = [int(str(row["value"])) for row in referendum[81 * 6 :]]
    if (
        domestic_2007 != DOMESTIC_REFERENDUM_2007
        or [domestic + customs for domestic, customs in zip(domestic_2007, customs_2007, strict=True)]
        != NATIONAL_REFERENDUM_2007
    ):
        raise ValueError(f"{DAY_REFERENDUM_2007}: provinces and customs gates do not add up to the national totals")

    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(pages).unique(["url", "page"]).write_parquet(PARSED / "pages.parquet")
    pl.DataFrame(president, schema_overrides={"box": pl.List(pl.Float32)}).write_parquet(PARSED / "president.parquet")
    pl.DataFrame(istanbul, schema_overrides={"box": pl.List(pl.Float32)}).write_parquet(PARSED / "istanbul.parquet")
    pl.DataFrame(districts).write_parquet(PARSED / "districts.parquet")
    frame.write_parquet(PARSED / "district_values.parquet")
    pl.DataFrame(seats).write_parquet(PARSED / "seats.parquet")
    pl.DataFrame(elected, schema_overrides={"box": pl.List(pl.Float32)}).write_parquet(PARSED / "elected.parquet")
    pl.DataFrame(
        [*candidates, *printed], schema_overrides={"box": pl.List(pl.Float32), "sequence": pl.Int64}
    ).write_parquet(PARSED / "lists.parquet")
    pl.DataFrame(pool, schema_overrides={"box": pl.List(pl.Float32)}).write_parquet(PARSED / "pool.parquet")
    for day, frames in parliaments.items():
        for kind, rows in frames.items():
            if rows:
                pl.DataFrame(
                    rows,
                    schema_overrides={
                        column: dtype
                        for column, dtype in (("box", pl.List(pl.Float32)), ("person", pl.String))
                        if column in rows[0]
                    },
                ).write_parquet(PARSED / f"{kind}_{day}.parquet")
    for year, referendum in referendums.items():
        pl.DataFrame(referendum, schema_overrides={"box": pl.List(pl.Float32)}).write_parquet(
            PARSED / f"referendum_{year}.parquet"
        )

    held: dict[tuple[str, str], dict[str, str]] = {}
    for binding in json.loads(raw.cached(SOURCE, MAYORS).path.read_text())["results"]["bindings"]:
        row = {name: value["value"].rsplit("/", 1)[-1] for name, value in binding.items()}
        statement = (row["person"], row["start"])
        if len(row) > len(held.get(statement, {})):
            held[statement] = row
    pl.DataFrame(
        [
            {
                "statement": row["statement"],
                "person": row["person"],
                "label": row["label"],
                "start": date.fromisoformat(row["start"][:10]),
                "end": date.fromisoformat(row["end"][:10]) if "end" in row else None,
                "cause": row.get("cause"),
                "elected": row.get("elected"),
            }
            for row in held.values()
        ]
    ).sort("start").write_parquet(PARSED / "mayors.parquet")


def emit() -> Path:
    pages = pl.read_parquet(PARSED / "pages.parquet")
    president = pl.read_parquet(PARSED / "president.parquet")
    istanbul = pl.read_parquet(PARSED / "istanbul.parquet")
    tenures_2018 = pl.read_parquet(PARSED / f"tenures_{DAY_2018}.parquet")
    ranked = pl.read_parquet(PARSED / "lists.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "2014, 2018, and 2023 Turkish presidential elections, the 2007, 2011, June 2015, November 2015, 2018, and "
                "2023 parliamentary elections, the 2007, 2010, and 2017 constitutional referendums, "
                "the 2018 and 2023 parliamentary candidate lists, and the June 2019 İstanbul mayoral election, from YSK decisions in the "
                "Resmî Gazete"
            ),
            polity="TR",
        )

        def name(
            entity: UUID,
            text: str,
            lang: str = "tr",
            kind: str = "official",
            start: date | None = None,
            end: date | None = None,
        ) -> None:
            cs.add(
                "name",
                entity_id=entity,
                text=text,
                lang=lang,
                kind=kind,
                is_preferred=kind == "official" and end is None,
                valid_from=start,
                valid_from_precision="day",
                valid_to=end,
                valid_to_precision="day",
            )

        sources: dict[str, UUID] = {}
        page_ids: dict[tuple[str, int], str] = {}
        for url, (heading, note) in DECISIONS.items():
            sources[url] = cs.source(PUBLISHER, heading)
            cs.add(
                "source",
                id=sources[url],
                kind="official_body",
                publisher=PUBLISHER,
                title=heading,
                url=url,
                license=LICENSE,
                notes=note,
            )
            artifact = cs.artifact(raw.cached(SOURCE, url), sources[url])
            for _, number, image, width, height in pages.filter(pl.col("url") == url).sort("page").iter_rows():
                page_ids[(url, number)] = str(cs.page(artifact, number))
                cs.add(
                    "source_page",
                    id=cs.page(artifact, number),
                    artifact_id=artifact,
                    page_number=number,
                    image_key=image,
                    width=width,
                    height=height,
                )

        cs.artifact(raw.cached(SOURCE, REFERENDUM_2007_CUSTOMS), sources[REFERENDUM_2007])
        turkey = cs.polity("TR")
        cs.add("polity", id=turkey, kind="sovereign_state", iso_code="TR")
        name(turkey, "Türkiye", "en")
        name(turkey, "Türkiye Cumhuriyeti", kind="native")
        cs.add("slug", path="tr", entity_id=turkey)
        cs.add("external_id", entity_id=turkey, scheme="wikidata", value="Q43")
        for code, rank, noun, kind in (
            ("tr.nation", 0, "country", "administrative"),
            ("tr.il", 1, "province", "administrative"),
            ("tr.secim_cevresi", 2, "electoral district", "electoral_district"),
        ):
            cs.add("scheme", code=code, polity_id=turkey, kind=kind, rank=rank, noun=noun)
        nation = cs.area("tr.nation", "TR")
        cs.add("area", id=nation, scheme_code="tr.nation")
        cs.add("area_code", area_id=nation, code="TR", valid_from=None)
        name(nation, "Türkiye", "en")
        cs.add("polity_territory", polity_id=turkey, area_id=nation)
        provinces: dict[str, UUID] = {}
        for plate, label in enumerate(PROVINCES, 1):
            code = f"{plate:02}"
            provinces[code] = cs.area("tr.il", code)
            cs.add("area", id=provinces[code], scheme_code="tr.il")
            cs.add("area_code", area_id=provinces[code], code=code, valid_from=None)
            cs.add("area_parent", area_id=provinces[code], parent_id=nation, valid_from=None)
            name(provinces[code], label)

        units: dict[str, UUID] = {}
        for code, kind, label in (
            ("abroad-customs", "aggregate", "Yurt dışı ve gümrük kapıları"),
            ("domestic", "aggregate", "Yurt içi"),
            ("abroad", "abroad_mission", "Yurt dışı"),
            ("customs", "customs_gate", "Gümrük kapıları"),
        ):
            units[code] = cs.unit("tr.nation", code)
            cs.add(
                "reporting_unit",
                id=units[code],
                polity_id=turkey,
                scheme_code="tr.nation",
                code=code,
                parent_id=units["abroad-customs"] if code in ("abroad", "customs") else None,
                area_relation="equals" if code == "domestic" else "unlocated",
                area_id=nation if code == "domestic" else None,
                kind=kind,
            )
            name(units[code], label)
        for code, area in provinces.items():
            units[code] = cs.unit("tr.il", code)
            cs.add(
                "reporting_unit",
                id=units[code],
                polity_id=turkey,
                scheme_code="tr.il",
                code=code,
                parent_id=None,
                area_relation="equals",
                area_id=area,
                kind="aggregate",
            )
            name(units[code], PROVINCES[int(code) - 1])

        parties: dict[str, UUID] = {}
        for full, short in [
            *((full, short) for full, short, *_ in PARTIES),
            ("Halkların Demokratik Partisi", "HDP"),
            ("Saadet Partisi", "Saadet"),
            ("Hür Dava Partisi", "HÜDA PAR"),
            *((full, short) for full, short, *_ in PARTIES_2015),
            *((full, short) for full, short, *_ in PARTIES_JUNE_2015),
            *((full, short) for full, short, *_ in PARTIES_2011),
            *((full, short) for full, short, *_ in PARTIES_2007),
        ]:
            if full in parties:
                continue
            if full in RENAMED:
                successor, day = RENAMED[full]
                parties[full] = parties[successor]
                name(parties[full], full, end=day)
                name(parties[full], short, kind="abbreviation", end=day)
                continue
            start = next((day for successor, day in RENAMED.values() if successor == full), None)
            parties[full] = cs.key("tr.party", slug(full))
            cs.add("party", id=parties[full], kind="party", polity_id=turkey)
            if full in FORMER:
                former, abbreviation, start = FORMER[full]
                name(parties[full], former, end=start)
                name(parties[full], abbreviation, kind="abbreviation", end=start)
            name(parties[full], full, start=start)
            name(parties[full], short, kind="abbreviation", start=start)
        persons: dict[str, UUID] = {}
        for label in dict.fromkeys(
            [label for label, _ in CANDIDATES + CANDIDATES_2018 + CANDIDATES_2014]
            + ["Ekrem İmamoğlu", "Binali Yıldırım"]
        ):
            persons[label] = cs.key("tr.person", slug(label))
            if persons[label] in cs.minted:
                cs.add("person", id=persons[label])
                name(persons[label], label)

        assembly = cs.key("tr.body", "tbmm")
        cs.add("body", id=assembly, polity_id=turkey, kind="unicameral")
        name(assembly, "Grand National Assembly of Türkiye", "en")
        name(assembly, "Türkiye Büyük Millet Meclisi", kind="native")
        name(assembly, "TBMM", kind="abbreviation")
        cs.add(
            "body_size",
            body_id=assembly,
            seats=550,
            valid_from=date(1995, 7, 1),
            valid_from_precision="month",
            valid_to=date(2018, 7, 1),
            valid_to_precision="month",
        )
        cs.add(
            "body_size",
            body_id=assembly,
            seats=600,
            valid_from=date(2018, 7, 1),
            valid_from_precision="month",
            valid_to=None,
            valid_to_precision="day",
        )
        office = cs.key("tr.office", "president")
        cs.add("office", id=office, polity_id=turkey, kind="head_of_state")
        name(office, "President of Türkiye", "en")
        name(office, "Türkiye Cumhurbaşkanı", kind="native")
        systems: dict[str, UUID] = {}
        lists = {"order": "closed", "vacancy": "by_election"}
        for code, rules, label, methodology in (
            (
                "president",
                {"formula": "two_round", "majority": 0.5, "advance": 2},
                "Turkish presidential election",
                PRESIDENT_METHODOLOGY,
            ),
            (
                "parliament",
                {
                    "formula": "dhondt",
                    "threshold": {"percent": 7, "level": "alliance", "scope": "national"},
                    "lists": lists,
                },
                "Grand National Assembly proportional representation since 2022",
                PARLIAMENT_METHODOLOGY,
            ),
            (
                "parliament_2018",
                {
                    "formula": "dhondt",
                    "threshold": {"percent": 10, "level": "alliance", "scope": "national"},
                    "pooling": "alliance",
                    "lists": lists,
                },
                "Grand National Assembly proportional representation, 2018 to 2022",
                PARLIAMENT_2018_METHODOLOGY,
            ),
            (
                "parliament_2015",
                {
                    "formula": "dhondt",
                    "threshold": {"percent": 10, "level": "party", "scope": "national"},
                    "lists": lists,
                },
                "Grand National Assembly proportional representation, 2015 to 2018",
                PARLIAMENT_2015_METHODOLOGY,
            ),
            (
                "parliament_2011",
                {
                    "formula": "dhondt",
                    "threshold": {"percent": 10, "level": "party", "scope": "national"},
                    "lists": lists,
                },
                "Grand National Assembly proportional representation in 2011",
                PARLIAMENT_2011_METHODOLOGY,
            ),
            (
                "parliament_2007",
                {
                    "formula": "dhondt",
                    "threshold": {"percent": 10, "level": "party", "scope": "national"},
                    "lists": lists,
                },
                "Grand National Assembly proportional representation in 2007",
                PARLIAMENT_2011_METHODOLOGY,
            ),
            ("mayor", {"formula": "fptp"}, "Turkish mayoral plurality", None),
            ("referendum", {"formula": "referendum"}, "Turkish constitutional referendum", None),
        ):
            systems[code] = cs.key("tr.electoral_system", code)
            cs.add("electoral_system", id=systems[code], polity_id=turkey, rules=rules, rules_version=1)
            name(systems[code], label, "en")
            if methodology is not None:
                cs.add("note", entity_id=systems[code], kind="methodology", body_md=methodology)

        cycle = cs.key("tr.election", f"{ROUND_DAYS[1]}:general-elections")
        cs.add(
            "election",
            id=cycle,
            polity_id=turkey,
            kind="general",
            date_from=ROUND_DAYS[1],
            date_to=ROUND_DAYS[2],
            status="held",
        )
        name(cycle, "2023 Turkish general elections", "en")
        cs.add("slug", path=f"tr/{ROUND_DAYS[1]}-general-elections", entity_id=cycle)
        cs.add("note", entity_id=cycle, kind="caveat", body_md=PRESIDENT_CAVEAT)
        elections: dict[int, UUID] = {}
        paths: dict[int, str] = {}
        for number, kind, label, native in (
            (
                1,
                "general",
                "2023 Turkish general election",
                "Cumhurbaşkanı Seçimi ve 28. Dönem Milletvekili Genel Seçimi",
            ),
            (2, "runoff", "2023 Turkish presidential runoff", "Cumhurbaşkanı Seçimi ikinci oylama"),
        ):
            elections[number] = cs.key("tr.election", str(ROUND_DAYS[number]))
            cs.add(
                "election",
                id=elections[number],
                polity_id=turkey,
                parent_id=cycle,
                kind=kind,
                date_from=ROUND_DAYS[number],
                date_to=ROUND_DAYS[number],
                status="held",
            )
            name(elections[number], label, "en")
            name(elections[number], native, kind="native")
            paths[number] = f"tr/{ROUND_DAYS[number]}-{kind}"
            cs.add("slug", path=paths[number], entity_id=elections[number])

        cs.add(
            "party_relation",
            from_party_id=parties["Yeşiller ve Sol Gelecek Partisi"],
            to_party_id=parties["Halkların Demokratik Partisi"],
            kind="list_vehicle_for",
            date_precision="day",
            election_id=elections[1],
        )
        cs.add(
            "party_relation",
            from_party_id=parties["Komünist Parti"],
            to_party_id=parties["Türkiye Komünist Partisi"],
            kind="absorbed_by",
            date=date(2017, 1, 1),
            date_precision="month",
        )

        result_sets: dict[str, str] = {}
        options: dict[str, str] = {}
        facts: list[dict[str, object]] = []

        def result_set(contest_key: str, election: UUID, contest: UUID, url: str) -> UUID:
            id = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=id,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[url],
                is_default=True,
                completeness=1,
            )
            if id in cs.minted:
                result_sets[contest_key] = str(id)
            return id

        def collect(frame: pl.DataFrame, contest_key: str, url: str, unit: str | None = None) -> None:
            for row in frame.iter_rows(named=True):
                facts.append(
                    {
                        "set": contest_key,
                        "unit": str(units[row["unit"]] if unit is None else unit),
                        "option": None if row["option"] is None else options[f"{contest_key}:{row['option']}"],
                        "channel": row.get("channel", 0),
                        "measure": row["measure"],
                        "value": row["value"],
                        "page": None if row["page"] is None else page_ids[(url, row["page"])],
                        "box": row["box"],
                    }
                )

        contests: dict[int, UUID] = {}
        for number, day in ROUND_DAYS.items():
            contest_key = f"{day}:president"
            contests[number] = cs.key("tr.contest", contest_key)
            cs.add(
                "contest",
                id=contests[number],
                election_id=elections[number],
                kind="office",
                office_id=office,
                magnitude=1,
                round_number=number,
                previous_round_id=contests.get(1) if number == 2 else None,
                electoral_system_id=systems["president"],
                default_result_kind="counted",
            )
            name(
                contests[number],
                f"2023 Turkish presidential election, {'first round' if number == 1 else 'runoff'}",
                "en",
            )
            cs.add("slug", path=f"{paths[number]}/president", entity_id=contests[number])
            declared = result_set(contest_key, elections[number], contests[number], PRESIDENT[number])
            for index in range(len(CANDIDATES)) if number == 1 else RUNOFF:
                label, party = CANDIDATES[index]
                option = cs.key("tr.option", f"{contest_key}:{slug(label)}")
                options[f"{contest_key}:{index}"] = str(option)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contests[number],
                    kind="candidate",
                    party_id=None if party is None else parties[party],
                    ballot_label=label,
                )
                candidacy = cs.key("tr.candidacy", f"{contest_key}:{slug(label)}")
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=persons[label],
                    option_id=option,
                    role="head",
                    member_of_party_id=None if party is None else parties[party],
                    is_independent=party is None,
                )
                if party is not None:
                    cs.add("candidacy_nomination", candidacy_id=candidacy, party_id=parties[party], kind="nominated")
                outcome = {0: "advanced" if number == 1 else "elected", 1: "withdrew", 2: "advanced", 3: "eliminated"}
                if number == 1 or index == 0:
                    cs.add(
                        "outcome",
                        result_set_id=declared,
                        option_id=option,
                        kind=outcome[index],
                        notes=INCE_WITHDREW if index == 1 else None,
                    )
            collect(
                president.filter(pl.col("day") == str(day)).rename({"candidate": "option"}),
                contest_key,
                PRESIDENT[number],
            )
        general = cs.key("tr.election", str(DAY_2018))
        cs.add(
            "election",
            id=general,
            polity_id=turkey,
            kind="general",
            date_from=DAY_2018,
            date_to=DAY_2018,
            status="held",
        )
        name(general, "2018 Turkish general election", "en")
        name(general, "Cumhurbaşkanı ve 27. Dönem Milletvekili Genel Seçimi", kind="native")
        cs.add("slug", path=f"tr/{DAY_2018}-general", entity_id=general)
        cs.add("note", entity_id=general, kind="caveat", body_md=PRESIDENT_CAVEAT)
        contest_key = f"{DAY_2018}:president"
        contest = cs.key("tr.contest", contest_key)
        cs.add(
            "contest",
            id=contest,
            election_id=general,
            kind="office",
            office_id=office,
            magnitude=1,
            round_number=1,
            previous_round_id=None,
            electoral_system_id=systems["president"],
            default_result_kind="counted",
        )
        name(contest, "2018 Turkish presidential election", "en")
        cs.add("slug", path=f"tr/{DAY_2018}-general/president", entity_id=contest)
        declared = result_set(contest_key, general, contest, PRESIDENT_2018)
        for index, (label, party) in enumerate(CANDIDATES_2018):
            option = cs.key("tr.option", f"{contest_key}:{slug(label)}")
            options[f"{contest_key}:{index}"] = str(option)
            cs.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=parties[party],
                ballot_label=label,
            )
            candidacy = cs.key("tr.candidacy", f"{contest_key}:{slug(label)}")
            cs.add(
                "candidacy",
                id=candidacy,
                person_id=persons[label],
                option_id=option,
                role="head",
                member_of_party_id=parties[party],
                is_independent=False,
            )
            cs.add("candidacy_nomination", candidacy_id=candidacy, party_id=parties[party], kind="nominated")
            cs.add("outcome", result_set_id=declared, option_id=option, kind="elected" if index == 2 else "lost")
        collect(
            president.filter(pl.col("day") == str(DAY_2018)).rename({"candidate": "option"}),
            contest_key,
            PRESIDENT_2018,
        )
        presidential = cs.key("tr.election", str(DAY_2014))
        cs.add(
            "election",
            id=presidential,
            polity_id=turkey,
            kind="general",
            date_from=DAY_2014,
            date_to=DAY_2014,
            status="held",
        )
        name(presidential, "2014 Turkish presidential election", "en")
        name(presidential, "Onikinci Cumhurbaşkanı Seçimi", kind="native")
        cs.add("slug", path=f"tr/{DAY_2014}-presidential", entity_id=presidential)
        contest_key = f"{DAY_2014}:president"
        direct = cs.key("tr.contest", contest_key)
        cs.add(
            "contest",
            id=direct,
            election_id=presidential,
            kind="office",
            office_id=office,
            magnitude=1,
            round_number=1,
            previous_round_id=None,
            electoral_system_id=systems["president"],
            default_result_kind="counted",
        )
        name(direct, "2014 Turkish presidential election", "en")
        cs.add("slug", path=f"tr/{DAY_2014}-presidential/president", entity_id=direct)
        declared = result_set(contest_key, presidential, direct, PRESIDENT_2014)
        for index, (label, party) in enumerate(CANDIDATES_2014):
            option = cs.key("tr.option", f"{contest_key}:{slug(label)}")
            options[f"{contest_key}:{index}"] = str(option)
            cs.add(
                "contest_option",
                id=option,
                contest_id=direct,
                kind="candidate",
                party_id=None if party is None else parties[party],
                ballot_label=label,
            )
            candidacy = cs.key("tr.candidacy", f"{contest_key}:{slug(label)}")
            cs.add(
                "candidacy",
                id=candidacy,
                person_id=persons[label],
                option_id=option,
                role="head",
                member_of_party_id=None if party is None else parties[party],
                is_independent=False,
            )
            for nominator in JOINT_2014 if party is None else (party,):
                cs.add("candidacy_nomination", candidacy_id=candidacy, party_id=parties[nominator], kind="nominated")
            cs.add("outcome", result_set_id=declared, option_id=option, kind="elected" if index == 0 else "lost")
        collect(
            president.filter(pl.col("day") == str(DAY_2014)).rename({"candidate": "option"}),
            contest_key,
            PRESIDENT_2014,
        )
        direct_term = cs.key(f"{SOURCE}.tenure", f"{DAY_2014}:president")
        cs.add(
            "tenure",
            id=direct_term,
            person_id=persons[CANDIDATES[0][0]],
            office_id=office,
            from_date=INAUGURATED_2014,
            to_date=INAUGURATED_2018,
            acquired_via="elected",
            contest_id=direct,
            ended_via="term_end",
            succeeds_tenure_id=None,
        )
        cs.add("tenure_event", tenure_id=direct_term, kind="sworn_in", date=INAUGURATED_2014)
        first_term = cs.key(f"{SOURCE}.tenure", f"{DAY_2018}:president")
        cs.add(
            "tenure",
            id=first_term,
            person_id=persons[CANDIDATES[0][0]],
            office_id=office,
            from_date=INAUGURATED_2018,
            to_date=INAUGURATED,
            acquired_via="elected",
            contest_id=contest,
            ended_via="term_end",
            succeeds_tenure_id=direct_term,
        )
        cs.add("tenure_event", tenure_id=first_term, kind="sworn_in", date=INAUGURATED_2018)
        tenure = cs.key(f"{SOURCE}.tenure", f"{ROUND_DAYS[2]}:president")
        cs.add(
            "tenure",
            id=tenure,
            person_id=persons[CANDIDATES[0][0]],
            office_id=office,
            from_date=INAUGURATED,
            to_date=None,
            acquired_via="elected",
            contest_id=contests[2],
            ended_via=None,
            succeeds_tenure_id=first_term,
        )
        cs.add("tenure_event", tenure_id=tenure, kind="sworn_in", date=INAUGURATED)

        members_source = cs.source("Wikidata", "Members of the 27th Grand National Assembly")
        cs.add(
            "source",
            id=members_source,
            kind="wikidata",
            publisher="Wikidata",
            title="Members of the 27th Grand National Assembly",
            url=f"https://www.wikidata.org/wiki/{TERM_2018}",
            license="CC0 1.0",
            notes=MPS_2018_SOURCE,
        )
        cs.artifact(raw.cached(SOURCE, MPS_2018), members_source)
        members_2015 = cs.source("Wikidata", "Members of the 26th Grand National Assembly")
        cs.add(
            "source",
            id=members_2015,
            kind="wikidata",
            publisher="Wikidata",
            title="Members of the 26th Grand National Assembly",
            url=f"https://www.wikidata.org/wiki/{TERM_2015}",
            license="CC0 1.0",
            notes=MPS_2015_SOURCE,
        )
        cs.artifact(raw.cached(SOURCE, MPS_2015), members_2015)
        november = cs.key("tr.election", str(DAY_2015))
        cs.add(
            "election",
            id=november,
            polity_id=turkey,
            kind="general",
            date_from=DAY_2015,
            date_to=DAY_2015,
            status="held",
        )
        name(november, "November 2015 Turkish general election", "en")
        name(november, "26. Dönem Milletvekili Genel Seçimi", kind="native")
        cs.add("slug", path=f"tr/{DAY_2015}-general", entity_id=november)
        members_june = cs.source("Wikidata", "Members of the 25th Grand National Assembly")
        cs.add(
            "source",
            id=members_june,
            kind="wikidata",
            publisher="Wikidata",
            title="Members of the 25th Grand National Assembly",
            url=f"https://www.wikidata.org/wiki/{TERM_JUNE_2015}",
            license="CC0 1.0",
            notes=MPS_JUNE_2015_SOURCE,
        )
        cs.artifact(raw.cached(SOURCE, MPS_JUNE_2015), members_june)
        june = cs.key("tr.election", str(DAY_JUNE_2015))
        cs.add(
            "election",
            id=june,
            polity_id=turkey,
            kind="general",
            date_from=DAY_JUNE_2015,
            date_to=DAY_JUNE_2015,
            status="held",
        )
        name(june, "June 2015 Turkish general election", "en")
        name(june, "25. Dönem Milletvekili Genel Seçimi", kind="native")
        cs.add("slug", path=f"tr/{DAY_JUNE_2015}-general", entity_id=june)
        ozonder_item = cs.source("Wikidata", "Mehmet Cihat Özönder")
        cs.add(
            "source",
            id=ozonder_item,
            kind="wikidata",
            publisher="Wikidata",
            title="Mehmet Cihat Özönder",
            url=f"https://www.wikidata.org/wiki/{OZONDER}",
            license="CC0 1.0",
            notes=OZONDER_SOURCE,
        )
        cs.artifact(raw.cached(SOURCE, OZONDER_ITEM), ozonder_item)
        ozonder_press = cs.source("Son Dakika", "Mazbatasını Alamadı")
        cs.add(
            "source",
            id=ozonder_press,
            kind="news_agency",
            publisher="Son Dakika",
            title="Mazbatasını Alamadı",
            url=OZONDER_PRESS,
            notes=OZONDER_PRESS_SOURCE,
        )
        cs.artifact(raw.cached(SOURCE, OZONDER_PRESS), ozonder_press)
        withdrew_sources: dict[str, UUID] = {}
        for url, (publisher, heading, note) in WITHDREW_SOURCES.items():
            withdrew_sources[url] = cs.source(publisher, heading)
            cs.add(
                "source",
                id=withdrew_sources[url],
                kind="news_agency",
                publisher=publisher,
                title=heading,
                url=url,
                notes=note,
            )
            cs.artifact(raw.cached(SOURCE, url), withdrew_sources[url])
        members_2011 = cs.source("Wikidata", "Members of the 24th Grand National Assembly")
        cs.add(
            "source",
            id=members_2011,
            kind="wikidata",
            publisher="Wikidata",
            title="Members of the 24th Grand National Assembly",
            url=f"https://www.wikidata.org/wiki/{TERM_2011}",
            license="CC0 1.0",
            notes=MPS_2011_SOURCE,
        )
        cs.artifact(raw.cached(SOURCE, MPS_2011), members_2011)
        held_2011 = cs.key("tr.election", str(DAY_2011))
        cs.add(
            "election",
            id=held_2011,
            polity_id=turkey,
            kind="general",
            date_from=DAY_2011,
            date_to=DAY_2011,
            status="held",
        )
        name(held_2011, "2011 Turkish general election", "en")
        name(held_2011, "24. Dönem Milletvekili Genel Seçimi", kind="native")
        cs.add("slug", path=f"tr/{DAY_2011}-general", entity_id=held_2011)
        held_2007 = cs.key("tr.election", str(DAY_2007))
        cs.add(
            "election",
            id=held_2007,
            polity_id=turkey,
            kind="general",
            date_from=DAY_2007,
            date_to=DAY_2007,
            status="held",
        )
        name(held_2007, "2007 Turkish general election", "en")
        name(held_2007, "23. Dönem Milletvekili Genel Seçimi", kind="native")
        cs.add("slug", path=f"tr/{DAY_2007}-general", entity_id=held_2007)
        district_units: dict[str, UUID] = {}
        november_members: dict[tuple[str, str], UUID] = {}
        for day, election, path, url, system, standing, coalitions, won_seats, suffix, caveat in (
            (
                ROUND_DAYS[1],
                elections[1],
                paths[1],
                PARLIAMENT,
                "parliament",
                PARTIES,
                ALLIANCES,
                SEATS,
                "",
                LISTS_CAVEAT,
            ),
            (
                DAY_2018,
                general,
                f"tr/{DAY_2018}-general",
                PARLIAMENT_2018,
                "parliament_2018",
                PARTIES_2018,
                ALLIANCES_2018,
                SEATS_2018,
                f"_{DAY_2018}",
                LISTS_2018_CAVEAT,
            ),
            (
                DAY_2015,
                november,
                f"tr/{DAY_2015}-general",
                PARLIAMENT_2015,
                "parliament_2015",
                PARTIES_2015,
                [],
                SEATS_2015,
                f"_{DAY_2015}",
                REGISTERED_2015_CAVEAT,
            ),
            (
                DAY_JUNE_2015,
                june,
                f"tr/{DAY_JUNE_2015}-general",
                PARLIAMENT_JUNE_2015,
                "parliament_2015",
                PARTIES_JUNE_2015,
                [],
                SEATS_JUNE_2015,
                f"_{DAY_JUNE_2015}",
                REGISTERED_JUNE_2015_CAVEAT,
            ),
            (
                DAY_2011,
                held_2011,
                f"tr/{DAY_2011}-general",
                PARLIAMENT_2011,
                "parliament_2011",
                PARTIES_2011,
                [],
                SEATS_2011,
                f"_{DAY_2011}",
                INDEPENDENTS_2011_CAVEAT,
            ),
            (
                DAY_2007,
                held_2007,
                f"tr/{DAY_2007}-general",
                PARLIAMENT_2007,
                "parliament_2007",
                PARTIES_2007,
                [],
                SEATS_2007,
                f"_{DAY_2007}",
                INDEPENDENTS_2007_CAVEAT,
            ),
        ):
            alliances = {label: cs.key("tr.alliance", f"{day}:{slug(label)}") for label, _ in coalitions}
            for label, alliance in alliances.items():
                cs.add("alliance", id=alliance, kind="electoral_alliance", election_id=election)
                name(alliance, label)
            for full, _, alliance, *_ in standing:
                if alliance is not None:
                    cs.add("alliance_member", alliance_id=alliances[alliance], party_id=parties[full])
            national_key = f"{day}:parliament"
            national = cs.key("tr.contest", national_key)
            cs.add(
                "contest",
                id=national,
                election_id=election,
                kind="list_allocation",
                body_id=assembly,
                district_area_id=nation,
                magnitude=sum(won_seats.values()),
                round_number=1,
                electoral_system_id=systems[system],
                default_result_kind="counted",
            )
            name(national, "Grand National Assembly, national pool and seats", "en")
            cs.add("slug", path=f"{path}/parliament", entity_id=national)
            cs.add(
                "note",
                entity_id=national,
                kind="methodology",
                body_md=POOL_METHODOLOGY
                if coalitions
                else POOL_METHODOLOGY_2011
                if day in (DAY_2011, DAY_2007)
                else POOL_METHODOLOGY_2015,
            )
            cs.add("note", entity_id=national, kind="caveat", body_md=caveat)
            declared = result_set(national_key, election, national, url)
            for full, _, alliance, *_ in [
                *standing,
                *([(INDEPENDENTS, None, None)] if INDEPENDENTS in won_seats else []),
            ]:
                option = cs.key("tr.option", f"{national_key}:{slug(full)}")
                options[f"{national_key}:{full}"] = str(option)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=national,
                    kind="others" if full == INDEPENDENTS else "list",
                    party_id=None if full == INDEPENDENTS else parties[full],
                    alliance_id=None if alliance is None else alliances[alliance],
                    ballot_label=full,
                )
                if full in won_seats:
                    cs.add("outcome", result_set_id=declared, option_id=option, kind="won", seats=won_seats[full])
            for label, alliance in alliances.items():
                option = cs.key("tr.option", f"{national_key}:{slug(label)}")
                options[f"{national_key}:{label}"] = str(option)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=national,
                    kind="legend",
                    party_id=None,
                    alliance_id=alliance,
                    ballot_label=label,
                )
            collect(pl.read_parquet(PARSED / f"pool{suffix}.parquet"), national_key, url)

            district_values = pl.read_parquet(PARSED / f"district_values{suffix}.parquet")
            seats = pl.read_parquet(PARSED / f"seats{suffix}.parquet")
            elected = pl.read_parquet(PARSED / f"elected{suffix}.parquet")
            for code, plate, number, magnitude in pl.read_parquet(PARSED / f"districts{suffix}.parquet").iter_rows():
                province = PROVINCES[plate - 1]
                area = provinces[f"{plate:02}"]
                native = f"{province} {number}. seçim çevresi" if number else f"{province} seçim çevresi"
                if number:
                    area = cs.area("tr.secim_cevresi", code)
                if code not in district_units:
                    if number:
                        cs.add("area", id=area, scheme_code="tr.secim_cevresi")
                        cs.add("area_code", area_id=area, code=code, valid_from=None)
                        cs.add("area_parent", area_id=area, parent_id=provinces[f"{plate:02}"], valid_from=None)
                        name(area, native)
                    district_units[code] = cs.unit("tr.secim_cevresi", code)
                    cs.add(
                        "reporting_unit",
                        id=district_units[code],
                        polity_id=turkey,
                        scheme_code="tr.secim_cevresi",
                        code=code,
                        parent_id=None,
                        area_relation="equals",
                        area_id=area,
                        kind="aggregate",
                    )
                    name(district_units[code], native)
                label = f"{province} {number}" if number else province
                contest_key = f"{national_key}:{code}"
                contest = cs.key("tr.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="list_allocation",
                    body_id=assembly,
                    district_area_id=area,
                    magnitude=magnitude,
                    round_number=1,
                    allocation_parent_id=national,
                    electoral_system_id=systems[system],
                    default_result_kind="counted",
                )
                name(contest, f"Grand National Assembly, {label}", "en")
                cs.add("slug", path=f"{path}/parliament/{slug(label)}", entity_id=contest)
                if day == DAY_2018 and (plate, number) == KAHRAMANMARAS:
                    cs.add("note", entity_id=contest, kind="caveat", body_md=KAHRAMANMARAS_CAVEAT)
                if day == DAY_2011 and code == "21":
                    cs.add("note", entity_id=contest, kind="caveat", body_md=DIYARBAKIR_2011_CAVEAT)
                declared = result_set(contest_key, election, contest, url)
                won = dict(seats.filter(pl.col("code") == code).select("option", "seats").iter_rows())
                for full, _, alliance, *_ in [*standing, (INDEPENDENTS, None, None)]:
                    option = cs.key("tr.option", f"{contest_key}:{slug(full)}")
                    options[f"{contest_key}:{full}"] = str(option)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="others" if full == INDEPENDENTS else "list",
                        party_id=None if full == INDEPENDENTS else parties[full],
                        alliance_id=None if alliance is None else alliances[alliance],
                        ballot_label=full,
                    )
                    if full in won:
                        cs.add("outcome", result_set_id=declared, option_id=option, kind="won", seats=won[full])
                collect(district_values.filter(pl.col("code") == code), contest_key, url, str(district_units[code]))
                list_url = ({ROUND_DAYS[1]: LISTS} | LIST_URLS).get(day, "")
                listed = ranked.filter(pl.col("url") == list_url, pl.col("code") == code).to_dicts()
                ranks = {row["sequence"]: row for row in listed if row["sequence"] is not None}
                for row in listed:
                    if row["sequence"] is not None:
                        continue
                    party = row["party"]
                    member = f"{day}:{code}:{slug(party)}:{row['position']}"
                    person = cs.key(f"{SOURCE}.person", member)
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, title(row["name"]))
                    candidacy = cs.key("tr.candidacy", member)
                    cs.add(
                        "candidacy",
                        id=candidacy,
                        person_id=person,
                        option_id=options[f"{contest_key}:{party}"],
                        role="head" if party == INDEPENDENTS else "list_member",
                        list_position=None if party == INDEPENDENTS else row["position"],
                        ballot_name=None,
                        member_of_party_id=None,
                        is_independent=party == INDEPENDENTS,
                    )
                    if party != INDEPENDENTS:
                        cs.add(
                            "candidacy_nomination", candidacy_id=candidacy, party_id=parties[party], kind="nominated"
                        )
                    cs.add(
                        "citation",
                        entity_id=candidacy,
                        source_id=sources[list_url],
                        locator={
                            "page_id": page_ids[(list_url, row["page"])],
                            "bbox": [round(value, 6) for value in row["box"]],
                        },
                    )
                    if row["withdrew"]:
                        report, withdrawn_on = WITHDRAWN[(list_url, code, party, row["position"])]
                        cs.add(
                            "outcome",
                            result_set_id=declared,
                            option_id=options[f"{contest_key}:{party}"],
                            candidacy_id=candidacy,
                            kind="withdrew",
                            notes=WITHDREW_UNDATED_NOTE
                            if withdrawn_on is None
                            else WITHDREW_NOTE.format(date=withdrawn_on),
                        )
                        cs.add("citation", entity_id=candidacy, source_id=withdrew_sources[report], locator=None)
                        cs.add(
                            "note",
                            entity_id=contest,
                            kind="caveat",
                            body_md=EDIRNE_2018_CAVEAT
                            if day == DAY_2018
                            else WITHDREW_CAVEAT.format(name=title(row["name"]), position=row["position"], party=party),
                        )
                        cs.add("citation", entity_id=contest, source_id=withdrew_sources[report], locator=None)
                for row in elected.filter(pl.col("code") == code).iter_rows(named=True):
                    member = f"{day}:{code}:{row['sequence']}"
                    party = row["party"]
                    qid = row.get("person")
                    if day == ROUND_DAYS[1]:
                        person = cs.key(f"{SOURCE}.person", member)
                        if person in cs.minted:
                            cs.add("person", id=person)
                            name(person, title(row["name"]))
                            if row["name"] == ATALAY[0]:
                                name(person, "Can Atalay", kind="short")
                    elif (
                        known := next(
                            (uuid for label, uuid in persons.items() if slug(label) == slug(row["label"])), None
                        )
                    ) is not None:
                        person = known
                        if qid is not None:
                            cs.add("external_id", entity_id=person, scheme="wikidata", value=qid)
                    elif day == DAY_JUNE_2015 and (code, row["name"]) in november_members:
                        person = november_members[(code, row["name"])]
                        if qid is not None:
                            cs.add("external_id", entity_id=person, scheme="wikidata", value=qid)
                    elif qid is not None:
                        person = cs.key("wikidata", qid)
                        if person in cs.minted:
                            cs.add("person", id=person)
                            name(person, row["label"])
                    else:
                        person = cs.key(f"{SOURCE}.person", member)
                        if person in cs.minted:
                            cs.add("person", id=person)
                            name(person, MISPRINTED.get(row["label"], row["label"]))
                    if day == DAY_2015:
                        november_members[(code, row["name"])] = person
                    candidacy = cs.key("tr.candidacy", member)
                    cs.add(
                        "candidacy",
                        id=candidacy,
                        person_id=person,
                        option_id=options[f"{contest_key}:{party}"],
                        role="head" if party == INDEPENDENTS else "list_member",
                        list_position=ranks[row["sequence"]]["position"]
                        if row["sequence"] in ranks and party != INDEPENDENTS
                        else None,
                        ballot_name=row["name"]
                        if day != ROUND_DAYS[1] and (row["page"] is not None or qid is None)
                        else None,
                        member_of_party_id=None if party == INDEPENDENTS else parties[party],
                        is_independent=party == INDEPENDENTS,
                    )
                    if party != INDEPENDENTS:
                        cs.add(
                            "candidacy_nomination", candidacy_id=candidacy, party_id=parties[party], kind="nominated"
                        )
                    cs.add(
                        "outcome",
                        result_set_id=declared,
                        option_id=options[f"{contest_key}:{party}"],
                        candidacy_id=candidacy,
                        kind="elected",
                    )
                    if day == DAY_2018 and row["page"] is None and qid is not None:
                        cs.add("citation", entity_id=candidacy, source_id=members_source, locator={"item": qid})
                    else:
                        cs.add(
                            "citation",
                            entity_id=candidacy,
                            source_id=sources[url],
                            locator=None
                            if row["page"] is None
                            else {
                                "page_id": page_ids[(url, row["page"])],
                                "bbox": [round(value, 6) for value in row["box"]],
                            },
                        )
                    if row["sequence"] in ranks:
                        cs.add(
                            "citation",
                            entity_id=candidacy,
                            source_id=sources[list_url],
                            locator={
                                "page_id": page_ids[(list_url, ranks[row["sequence"]]["page"])],
                                "bbox": [round(value, 6) for value in ranks[row["sequence"]]["box"]],
                            },
                        )
                    if qid == OZONDER:
                        cs.add("citation", entity_id=candidacy, source_id=ozonder_item, locator={"item": qid})
                        for cited in (ozonder_item, ozonder_press):
                            cs.add("citation", entity_id=contest, source_id=cited, locator=None)
                        cs.add("note", entity_id=contest, kind="caveat", body_md=OZONDER_CAVEAT)
                    if day == DAY_2018:
                        terms = [
                            (start, end, ended_via, cause, statement)
                            for _, start, end, ended_via, cause, statement in tenures_2018.filter(
                                pl.col("person") == qid
                            ).iter_rows()
                        ] or [(MP_START_2018, None, None, None, None)]
                    elif day in (DAY_2015, DAY_JUNE_2015, DAY_2011, DAY_2007):
                        terms = []
                    elif row["name"] == ATALAY[0]:
                        terms = [(MP_START, ATALAY[1], "removed", None, None)]
                    else:
                        terms = [(MP_START, None, None, None, None)]
                    previous: UUID | None = None
                    for start, end, ended_via, cause, statement in terms:
                        tenure = cs.key(f"{SOURCE}.tenure", member if previous is None else f"{member}:{start}")
                        cs.add(
                            "tenure",
                            id=tenure,
                            person_id=person,
                            body_id=assembly,
                            from_date=start,
                            to_date=MP_START if day == DAY_2018 and end is None else end,
                            acquired_via="elected",
                            contest_id=contest,
                            ended_via="term_end" if day == DAY_2018 and end is None else ended_via,
                            resumes_tenure_id=previous,
                        )
                        cs.add(
                            "tenure_affiliation",
                            tenure_id=tenure,
                            kind="party",
                            party_id=parties[party],
                            valid_from=start,
                        )
                        if statement is not None:
                            cs.add(
                                "citation", entity_id=tenure, source_id=members_source, locator={"statement": statement}
                            )
                        if previous is not None:
                            cs.add(
                                "tenure_event",
                                tenure_id=tenure,
                                kind="reinstated",
                                date=start,
                                source_id=members_source,
                            )
                        if cause in LEFT_2018:
                            cs.add("note", entity_id=tenure, kind="context", body_md=LEFT_2018[cause])
                        if ended_via in ("removed", "died"):
                            cs.add(
                                "tenure_event",
                                tenure_id=tenure,
                                kind=ended_via,
                                date=end,
                                actor=None if ended_via == "died" else "Türkiye Büyük Millet Meclisi Genel Kurulu",
                                reason=None
                                if ended_via == "died"
                                else ATALAY_REMOVED
                                if day != DAY_2018
                                else CONVICTED,
                                source_id=members_source if day == DAY_2018 else None,
                            )
                        previous = tenure

        for day, url, native, law, law_native in (
            (
                DAY_2017,
                REFERENDUM_2017,
                "16 Nisan 2017 Anayasa Değişikliği Halkoylaması",
                "Amendments to the Constitution (Law 6771)",
                "6771 sayılı Türkiye Cumhuriyeti Anayasasında Değişiklik Yapılmasına Dair Kanun",
            ),
            (
                DAY_2010,
                REFERENDUM_2010,
                "12 Eylül 2010 Anayasa Değişikliği Halkoylaması",
                "Amendments to the Constitution (Law 5982)",
                "5982 sayılı Türkiye Cumhuriyeti Anayasasının Bazı Maddelerinde Değişiklik Yapılması Hakkında Kanun",
            ),
            (
                DAY_REFERENDUM_2007,
                REFERENDUM_2007,
                "21 Ekim 2007 Anayasa Değişikliği Halkoylaması",
                "Amendments to the Constitution (Law 5678)",
                "5678 sayılı Türkiye Cumhuriyeti Anayasasının Bazı Maddelerinde Değişiklik Yapılması Hakkında Kanun",
            ),
        ):
            vote = cs.key("tr.election", f"{day}:referendum")
            cs.add(
                "election",
                id=vote,
                polity_id=turkey,
                kind="referendum",
                date_from=day,
                date_to=day,
                status="held",
            )
            name(vote, f"{day.year} Turkish constitutional referendum", "en")
            name(vote, native, kind="native")
            cs.add("slug", path=f"tr/{day}-referendum", entity_id=vote)
            if day == DAY_2010:
                court = cs.source("Anayasa Mahkemesi", COURT_2010_TITLE)
                cs.add(
                    "source",
                    id=court,
                    kind="official_body",
                    publisher="Anayasa Mahkemesi",
                    title=COURT_2010_TITLE,
                    url=COURT_2010,
                    license=LICENSE,
                    notes=COURT_2010_SOURCE,
                )
                cs.artifact(raw.cached(SOURCE, COURT_2010), court)
                cs.add("note", entity_id=vote, kind="context", body_md=COURT_2010_NOTE)
                cs.add("citation", entity_id=vote, source_id=court, locator={"section": "VI- SONUÇ"})
            contest_key = f"{day}:constitution"
            contest = cs.key("tr.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=vote,
                kind="ballot_question",
                electoral_system_id=systems["referendum"],
                round_number=1,
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
            name(contest, law, "en")
            name(contest, law_native, kind="native")
            cs.add("slug", path=f"tr/{day}-referendum/constitution", entity_id=contest)
            declared = result_set(contest_key, vote, contest, url)
            for position, answer in enumerate(ANSWERS_2017, 1):
                option = cs.key("tr.option", f"{contest_key}:{slug(answer)}")
                options[f"{contest_key}:{answer}"] = str(option)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position,
                    ballot_label=answer,
                )
                if position == 1:
                    cs.add("outcome", result_set_id=declared, option_id=option, kind="won")
            collect(pl.read_parquet(PARSED / f"referendum_{day.year}.parquet"), contest_key, url)

        metropolis = cs.key("tr.polity", "istanbul")
        cs.add("polity", id=metropolis, kind="municipality", iso_code=None)
        name(metropolis, "İstanbul Metropolitan Municipality", "en")
        name(metropolis, "İstanbul Büyükşehir Belediyesi", kind="native")
        cs.add("polity_parent", polity_id=metropolis, parent_id=turkey)
        cs.add("polity_territory", polity_id=metropolis, area_id=provinces["34"])
        cs.add("slug", path="tr/istanbul", entity_id=metropolis)
        mayor = cs.key("tr.office", "istanbul-mayor")
        cs.add("office", id=mayor, polity_id=metropolis, kind="mayor")
        name(mayor, "Mayor of İstanbul", "en")
        name(mayor, "İstanbul Büyükşehir Belediye Başkanı", kind="native")
        wikidata = cs.source("Wikidata", "Mayors of İstanbul")
        cs.add(
            "source",
            id=wikidata,
            kind="wikidata",
            publisher="Wikidata",
            title="Mayors of İstanbul",
            url=f"https://www.wikidata.org/wiki/{MAYORALTY}",
            license="CC0 1.0",
            notes=MAYORS_SOURCE,
        )
        cs.artifact(raw.cached(SOURCE, MAYORS), wikidata)
        local = cs.key("tr.election", f"{MARCH}:local")
        cs.add("election", id=local, polity_id=turkey, kind="local", date_from=MARCH, date_to=MARCH, status="held")
        name(local, "2019 Turkish local elections", "en")
        name(local, "2019 Mahalli İdareler Genel Seçimleri", kind="native")
        cs.add("slug", path=f"tr/{MARCH}-local", entity_id=local)
        cs.add("external_id", entity_id=local, scheme="wikidata", value=LOCAL_ELECTIONS)
        mayoral: dict[date, UUID] = {}
        annulled: UUID | None = None
        for day, status, label, native, standing in (
            (
                MARCH,
                "annulled",
                "March 2019 İstanbul mayoral election",
                "31 Mart 2019 İstanbul Büyükşehir Belediye Başkanlığı Seçimi",
                ("Cumhuriyet Halk Partisi", "Adalet ve Kalkınma Partisi"),
            ),
            (
                RERUN,
                "held",
                "June 2019 İstanbul mayoral election",
                "23 Haziran 2019 İstanbul Büyükşehir Belediye Başkanlığı Seçimi",
                ("Saadet Partisi", "Vatan Partisi", "Cumhuriyet Halk Partisi", "Adalet ve Kalkınma Partisi", "others"),
            ),
        ):
            election = cs.key("tr.election", f"{day}:istanbul-mayor")
            cs.add(
                "election",
                id=election,
                polity_id=metropolis,
                parent_id=local if day == MARCH else None,
                kind="local",
                date_from=day,
                date_to=day,
                status=status,
                rerun_of_id=annulled,
            )
            name(election, label, "en")
            name(election, native, kind="native")
            path = f"tr/istanbul/{day}-local"
            cs.add("slug", path=path, entity_id=election)
            contest_key = f"{day}:istanbul-mayor"
            mayoral[day] = contest = cs.key("tr.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=mayor,
                district_area_id=provinces["34"],
                magnitude=1,
                round_number=1,
                electoral_system_id=systems["mayor"],
                default_result_kind="counted",
            )
            name(contest, "Mayor of İstanbul", "en")
            cs.add("slug", path=f"{path}/mayor", entity_id=contest)
            if day == MARCH:
                annulled = election
                cs.add("note", entity_id=election, kind="context", body_md=MARCH_NOTE)
                declared = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=declared,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="annulled",
                    source_id=wikidata,
                    is_default=True,
                    completeness=0,
                )
            else:
                cs.add("external_id", entity_id=election, scheme="wikidata", value=RERUN_ELECTION)
                cs.add("note", entity_id=contest, kind="caveat", body_md=ISTANBUL_CAVEAT)
                declared = result_set(contest_key, election, contest, ISTANBUL)
            for party in standing:
                candidate, supporter = {
                    "Cumhuriyet Halk Partisi": ("Ekrem İmamoğlu", "İYİ Parti"),
                    "Adalet ve Kalkınma Partisi": ("Binali Yıldırım", "Milliyetçi Hareket Partisi"),
                }.get(party, (None, None))
                option = cs.key("tr.option", f"{contest_key}:{slug(party)}")
                options[f"{contest_key}:{party}"] = str(option)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="others" if party == "others" else "candidate",
                    party_id=None if party == "others" else parties[party],
                    ballot_label=INDEPENDENTS if party == "others" else party,
                )
                if candidate is not None and supporter is not None:
                    candidacy = cs.key("tr.candidacy", f"{contest_key}:{slug(candidate)}")
                    cs.add(
                        "candidacy",
                        id=candidacy,
                        person_id=persons[candidate],
                        option_id=option,
                        role="head",
                        member_of_party_id=parties[party],
                        is_independent=False,
                    )
                    cs.add("candidacy_nomination", candidacy_id=candidacy, party_id=parties[party], kind="nominated")
                    cs.add(
                        "candidacy_nomination", candidacy_id=candidacy, party_id=parties[supporter], kind="supported"
                    )
                if candidate == "Ekrem İmamoğlu":
                    cs.add("outcome", result_set_id=declared, option_id=option, kind="elected", notes=None)
        collect(istanbul, f"{RERUN}:istanbul-mayor", ISTANBUL, str(units["34"]))

        cs.add("external_id", entity_id=persons["Ekrem İmamoğlu"], scheme="wikidata", value=IMAMOGLU)
        previous: UUID | None = None
        for statement, qid, label, start, end, cause, elected_in in pl.read_parquet(
            PARSED / "mayors.parquet"
        ).iter_rows():
            person = persons["Ekrem İmamoğlu"] if qid == IMAMOGLU else cs.key("wikidata", qid)
            if person in cs.minted:
                cs.add("person", id=person)
                name(person, label)
            tenure = cs.key(f"{SOURCE}.tenure", f"istanbul-mayor:{qid}:{start}")
            cs.add(
                "tenure",
                id=tenure,
                person_id=person,
                office_id=mayor,
                from_date=start,
                to_date=end,
                acquired_via="acting" if elected_in is None else "elected",
                contest_id={LOCAL_ELECTIONS: mayoral[MARCH], RERUN_ELECTION: mayoral[RERUN]}.get(elected_in or ""),
                ended_via="annulled" if cause == RERUN_ELECTION else None,
                succeeds_tenure_id=previous,
            )
            cs.add("citation", entity_id=tenure, source_id=wikidata, locator={"statement": statement})
            if qid == IMAMOGLU:
                cs.add(
                    "tenure_affiliation",
                    tenure_id=tenure,
                    kind="party",
                    party_id=parties["Cumhuriyet Halk Partisi"],
                    valid_from=start,
                )
            if cause == ARREST:
                for kind in ("arrested", "suspended"):
                    cs.add(
                        "tenure_event",
                        tenure_id=tenure,
                        kind=kind,
                        date=end,
                        actor="İçişleri Bakanlığı" if kind == "suspended" else None,
                        reason=SUSPENDED if kind == "suspended" else None,
                        source_id=wikidata,
                    )
            previous = tenure

        located = pl.DataFrame(
            facts,
            schema={
                "set": pl.String,
                "unit": pl.String,
                "option": pl.String,
                "channel": pl.Int16,
                "measure": pl.Int16,
                "value": pl.Int64,
                "page": pl.String,
                "box": pl.List(pl.Float64),
            },
        ).filter(pl.col("set").is_in(list(result_sets)))
        keys = {
            "result_set_id": pl.col("set").replace_strict(result_sets),
            "unit_id": "unit",
            "option_id": "option",
            "channel": "channel",
            "measure": "measure",
            "count_round": pl.lit(0, pl.Int16),
        }
        cs.frame(
            "result_value",
            located.select(
                **keys,
                flags=pl.when(pl.col("page").is_null()).then(DERIVED).otherwise(DIGITIZED).cast(pl.Int16),
                value="value",
            ),
        )
        cs.frame(
            "value_locator",
            located.filter(pl.col("page").is_not_null()).select(
                **keys,
                page_id="page",
                x0=pl.col("box").list.get(0),
                y0=pl.col("box").list.get(1),
                x1=pl.col("box").list.get(2),
                y1=pl.col("box").list.get(3),
            ),
        )
        return cs.write()
