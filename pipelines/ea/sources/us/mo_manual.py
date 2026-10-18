import csv
import difflib
import io
import itertools
import json
import re
import statistics
import subprocess
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID

import numpy as np
import polars as pl
from PIL import Image, ImageChops, ImageFilter, ImageOps

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.mo_manual"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Missouri Secretary of State"
BOOK = "Official Manual of the State of Missouri"
IMAGE = "https://mdh.contentdm.oclc.org/iiif/2/bluebook:{}/full/full/0/default.jpg"
TABLES: list[tuple[str, str, list[tuple[int, float, float, float, float]], list[tuple[str, ...]]]] = [
    (
        "1914-11-03",
        "senate",
        [(9882, 0, 1, 0, 1), (9883, 0, 1, 0, 1), (9884, 0, 0.82, 0, 1)],
        [("DEM", "Stone"), ("REP", "Akins"), ("PRG", "Sager"), ("PRO", "Hill"), ("SOC", "Greene"), ("SLP", "Molineux")],
    ),
    (
        "1920-11-02",
        "senate",
        [(11842, 0.38, 1, 0, 1), (11843, 0.1, 0.79, 0, 1)],
        [("REP", "Spencer"), ("DEM", "Long"), ("SOC", "Hodges"), ("SLP", "Trudell"), ("FL", "Mallett")],
    ),
    (
        "1922-11-07",
        "senate",
        [(14505, 0.25, 1, 0, 1), (14506, 0.2, 0.69, 0, 1)],
        [("REP", "Brewster"), ("DEM", "Reed"), ("SOC", "Brandt"), ("SLP", "Cox")],
    ),
    (
        "1926-11-02",
        "senate",
        [(13612, 0.2, 1, 0, 1), (13613, 0.17, 0.6, 0, 1)],
        [
            ("REP", "Williams", "senate:special"),
            ("DEM", "Hawes", "senate:special"),
            ("REP", "Williams"),
            ("DEM", "Hawes"),
            ("SOC", "Morrison"),
            ("SLP", "Cox"),
            ("PRO", "Faris"),
        ],
    ),
    (
        "1928-11-06",
        "senate",
        [(15382, 0.53, 1, 0, 1), (15383, 0.2, 0.95, 0, 1)],
        [
            ("REP", "Patterson"),
            ("DEM", "Hay"),
            ("SOC", "Harrison"),
            ("SLP", "Cox"),
        ],
    ),
    (
        "1932-11-08",
        "senate",
        [(17054, 0.58, 1, 0, 1), (17055, 0.1, 0.96, 0, 1)],
        [
            ("DEM", "Clark"),
            ("REP", "Kiel"),
            ("SOC", "Hodges"),
            ("SLP", "Oberheu"),
            ("PRO", "Faris"),
            ("COM", "Pollack"),
        ],
    ),
    (
        "1934-11-06",
        "senate",
        [(18050, 0, 1, 0, 1), (18051, 0, 0.52, 0, 1)],
        [("DEM", "Truman"), ("REP", "Patterson"), ("SOC", "Meyer"), ("SLP", "Cox"), ("COM", "Brown")],
    ),
    (
        "1938-11-08",
        "senate",
        [(19824, 0, 1, 0, 1), (19825, 0, 0.57, 0, 1)],
        [("DEM", "Clark"), ("REP", "Caulfield"), ("SOC", "Hodges"), ("SLP", "Oberheu")],
    ),
    (
        "1940-11-05",
        "senate",
        [(22001, 0.75, 1, 0, 1), (22002, 0, 1, 0, 1), (22003, 0.17, 0.38, 0, 1)],
        [
            ("DEM", "Truman"),
            ("REP", "Davis"),
            ("SOC", "Rinck"),
            ("SLP", "Baeff"),
        ],
    ),
    (
        "1944-11-07",
        "senate",
        [(23210, 0.6, 1, 0, 1), (23211, 0, 1, 0, 1), (23212, 0, 0.36, 0, 1)],
        [
            ("DEM", "McKittrick"),
            ("REP", "Donnell"),
            ("SOC", "Priesler"),
            ("SLP", "Cox"),
        ],
    ),
    (
        "1946-11-05",
        "senate",
        [(24882, 0, 1, 0, 1), (24883, 0, 0.6, 0, 1)],
        [("DEM", "Briggs"), ("REP", "Kem"), ("SOC", "Rinck"), ("PRO", "Jackson"), ("SLP", "Baef")],
    ),
    (
        "1950-11-07",
        "senate",
        [(27535, 0, 1, 0, 1), (27536, 0, 0.58, 0, 1)],
        [("DEM", "Hennings"), ("REP", "Donnell"), ("SLP", "Genck"), ("CN", "Hamilton")],
    ),
    (
        "1952-11-04",
        "senate",
        [(28866, 0, 1, 0, 1), (28867, 0, 0.6, 0, 1)],
        [
            ("DEM", "Symington"),
            ("REP", "Kem"),
            ("WPRG", "Perkins"),
            ("SOC", "Hodges"),
            ("SLP", "Genck"),
            ("CN", "Frederick"),
        ],
    ),
    (
        "1956-11-06",
        "senate",
        [(31683, 0, 0.85, 0, 0.5), (31683, 0, 0.85, 0.5, 1)],
        [("DEM", "Hennings"), ("REP", "Douglas")],
    ),
    (
        "1958-11-04",
        "senate",
        [(33254, 0, 0.85, 0, 0.53), (33254, 0, 0.85, 0.53, 1)],
        [("DEM", "Symington"), ("REP", "Palmer")],
    ),
    (
        "1892-11-08",
        "president",
        [(2209, 0.16, 0.82, 0, 0.49), (2209, 0.16, 0.82, 0.49, 1)],
        [("DEM", "Cleveland"), ("REP", "Harrison"), ("PPL", "Weaver"), ("PRO", "Bidwell")],
    ),
    (
        "1896-11-03",
        "president",
        [(3120, 0.18, 0.95, 0, 1), (3121, 0.13, 0.78, 0, 1)],
        [
            ("DEM", "Bryan"),
            ("REP", "McKinley"),
            ("PRO", "Levering"),
            ("SLP", "Matchett"),
            ("NDEM", "Palmer"),
            ("NPRO", "Bentley"),
        ],
    ),
    (
        "1900-11-06",
        "president",
        [(4237, 0.18, 0.96, 0, 1), (4238, 0.14, 0.8, 0, 1)],
        [
            ("DEM", "Bryan"),
            ("REP", "McKinley"),
            ("PPL", "Barker"),
            ("PRO", "Woolley"),
            ("SOC", "Debs"),
            ("SLP", "Malloney"),
        ],
    ),
    (
        "1904-11-08",
        "president",
        [(5770, 0.15, 0.92, 0, 1), (5771, 0.12, 0.69, 0, 1)],
        [
            ("DEM", "Parker"),
            ("REP", "Roosevelt"),
            ("PRO", "Swallow"),
            ("SOC", "Debs"),
            ("SLP", "Corrigan"),
            ("PPL", "Watson"),
        ],
    ),
    (
        "1912-11-05",
        "president",
        [(8996, 0.12, 0.96, 0, 1), (8997, 0.11, 0.62, 0, 1)],
        [
            ("DEM", "Wilson"),
            ("REP", "Taft"),
            ("PRO", "Chafin"),
            ("SOC", "Debs"),
            ("SLP", "Reimer"),
            ("PRG", "Roosevelt"),
        ],
    ),
    (
        "1916-11-07",
        "president",
        [(10609, 0.11, 0.95, 0, 1), (10610, 0.11, 0.66, 0, 1)],
        [("DEM", "Wilson"), ("REP", "Hughes"), ("PRO", "Hanly"), ("SOC", "Benson"), ("SLP", "Reimer")],
    ),
    (
        "1920-11-02",
        "president",
        [(11815, 0.14, 0.965, 0, 1), (11816, 0.11, 0.52, 0, 1)],
        [
            ("REP", "Harding"),
            ("DEM", "Cox"),
            ("SOC", "Debs"),
            ("SLP", "Cox"),
            ("FL", "Christensen"),
            ("PRO", "Watkins"),
        ],
    ),
    (
        "1928-11-06",
        "president",
        [(15353, 0.12, 0.94, 0, 1), (15354, 0.1, 0.5, 0, 1)],
        [("REP", "Hoover"), ("DEM", "Smith"), ("SOC", "Thomas"), ("SLP", "Reynolds")],
    ),
    (
        "1932-11-08",
        "president",
        [(17053, 0.12, 0.95, 0, 1), (17054, 0.11, 0.52, 0, 1)],
        [
            ("DEM", "Roosevelt"),
            ("REP", "Hoover"),
            ("SOC", "Thomas"),
            ("SLP", "Reynolds"),
            ("PRO", "Upshaw"),
            ("COM", "Foster"),
        ],
    ),
    (
        "1936-11-03",
        "president",
        [(18904, 0.14, 0.96, 0, 1), (18905, 0.11, 0.55, 0, 1)],
        [
            ("DEM", "Roosevelt"),
            ("REP", "Landon"),
            ("SOC", "Thomas"),
            ("SLP", "Aiken"),
            ("PRO", "Colvin"),
            ("COM", "Browder"),
            ("UNI", "Lemke"),
        ],
    ),
    (
        "1940-11-05",
        "president",
        [(22000, 0.12, 0.96, 0, 1), (22001, 0.11, 0.62, 0, 1)],
        [("DEM", "Roosevelt"), ("REP", "Willkie"), ("SOC", "Thomas"), ("SLP", "Aiken"), ("PRO", "Babson")],
    ),
    (
        "1944-11-07",
        "president",
        [(23209, 0.12, 0.96, 0, 1), (23210, 0.11, 0.58, 0, 1)],
        [("DEM", "Roosevelt"), ("REP", "Dewey"), ("SOC", "Thomas"), ("SLP", "Teichert"), ("PRO", "Watson")],
    ),
    (
        "1948-11-02",
        "president",
        [(26104, 0.5, 0.94, 0, 1), (26105, 0.11, 0.92, 0, 1)],
        [("DEM", "Truman"), ("REP", "Dewey"), ("SOC", "Thomas"), ("WPRG", "Wallace")],
    ),
    (
        "1952-11-04",
        "president",
        [(28864, 0.46, 0.94, 0, 1), (28865, 0.11, 0.93, 0, 1)],
        [
            ("DEM", "Stevenson"),
            ("REP", "Eisenhower"),
            ("WPRG", "Hallinan"),
            ("SOC", "Hoopes"),
            ("SLP", "Hass"),
            ("PRO", "Hamblen"),
            ("CN", "MacArthur"),
            ("AF", "MacArthur"),
        ],
    ),
    (
        "1956-11-06",
        "president",
        [(31682, 0.13, 0.87, 0, 0.49), (31682, 0.13, 0.87, 0.49, 1)],
        [("DEM", "Stevenson"), ("REP", "Eisenhower")],
    ),
    (
        "1892-11-08",
        "governor",
        [(2232, 0.14, 0.81, 0, 0.52), (2232, 0.14, 0.8, 0.52, 1)],
        [("DEM", "Stone"), ("REP", "Warner"), ("PPL", "Leonard"), ("PRO", "Sobieski")],
    ),
    (
        "1900-11-06",
        "governor",
        [(4237, 0.185, 0.99, 0, 1), (4238, 0.13, 0.8, 0, 1)],
        [
            ("DEM", "Dockery"),
            ("REP", "Flory"),
            ("PPL", "Hillis"),
            ("PRO", "Stokes"),
            ("SDEM", "Lipscomb"),
            ("SLP", "Fry"),
        ],
    ),
    (
        "1904-11-08",
        "governor",
        [(5770, 0.15, 0.925, 0, 1), (5771, 0.125, 0.69, 0, 1)],
        [
            ("DEM", "Folk"),
            ("REP", "Walbridge"),
            ("PRO", "Hill"),
            ("SOC", "Behrens"),
            ("SLP", "White"),
            ("PPL", "Alldredge"),
        ],
    ),
    (
        "1912-11-05",
        "governor",
        [(8998, 0.15, 0.95, 0, 0.565), (8999, 0.12, 0.68, 0, 0.63)],
        [
            ("DEM", "Major"),
            ("REP", "McKinley"),
            ("PRO", "Stokes"),
            ("SOC", "Ward"),
            ("SLP", "Rogers"),
            ("PRG", "Nortoni"),
        ],
    ),
    (
        "1920-11-02",
        "governor",
        [(11843, 0.8, 0.95, 0, 1), (11844, 0.105, 0.96, 0, 1), (11845, 0.105, 0.43, 0, 1)],
        [
            ("REP", "Hyde"),
            ("DEM", "Atkinson"),
            ("SOC", "Aldrich"),
            ("SLP", "Middlecoff"),
            ("PRO", "Faris"),
            ("FW", "Hickman"),
        ],
    ),
    (
        "1924-11-04",
        "governor",
        [(12677, 0.155, 0.91, 0, 1), (12678, 0.12, 0.62, 0, 1)],
        [("REP", "Baker"), ("DEM", "Nelson"), ("SOC", "Brandt"), ("SLP", "Cox")],
    ),
    (
        "1932-11-08",
        "governor",
        [(17056, 0.125, 0.96, 0, 1), (17057, 0.115, 0.505, 0, 1)],
        [("DEM", "Park"), ("REP", "Winter"), ("SOC", "Wolf"), ("SLP", "Cox"), ("COM", "Penney")],
    ),
    (
        "1936-11-03",
        "governor",
        [(18905, 0.59, 0.965, 0, 1), (18906, 0.105, 0.955, 0, 1)],
        [("DEM", "Stark"), ("REP", "Barrett"), ("SOC", "Duemler"), ("SLP", "Cox"), ("COM", "Williams"), ("NP", "Botz")],
    ),
    (
        "1940-11-05",
        "governor",
        [(22001, 0.625, 0.955, 0, 1), (22002, 0.115, 0.945, 0, 1), (22003, 0.115, 0.39, 0, 1)],
        [("DEM", "McDaniel"), ("REP", "Donnell"), ("SOC", "High"), ("SLP", "Cox")],
    ),
    (
        "1944-11-07",
        "governor",
        [(23210, 0.6, 0.96, 0, 1), (23211, 0.12, 0.955, 0, 1), (23212, 0.12, 0.36, 0, 1)],
        [("DEM", "Donnelly"), ("REP", "Bradshaw"), ("SOC", "Rinck"), ("SLP", "Tendler")],
    ),
    (
        "1948-11-02",
        "governor",
        [(26106, 0.09, 0.935, 0, 1), (26107, 0.085, 0.535, 0, 1)],
        [("DEM", "Smith"), ("REP", "Thompson"), ("SOC", "Gipe"), ("SLP", "Genck"), ("WPRG", "Logsdon")],
    ),
    (
        "1952-11-04",
        "governor",
        [(28867, 0.615, 0.945, 0, 1), (28868, 0.09, 0.95, 0, 1), (28869, 0.09, 0.365, 0, 1)],
        [
            ("DEM", "Donnelly"),
            ("REP", "Elliott"),
            ("WPRG", "Edsell"),
            ("SOC", "Wheeler"),
            ("SLP", "Hayes"),
            ("CN", "Lohbeck"),
        ],
    ),
    (
        "1956-11-06",
        "governor",
        [(31684, 0.135, 0.86, 0, 0.5), (31684, 0.135, 0.86, 0.5, 1)],
        [("DEM", "Blair"), ("REP", "Hocker")],
    ),
]
MISORDERED: dict[tuple[str, str], list[tuple[str, str]]] = {
    ("1924-11-04", "president"): [("perry", "pettis")],
}
CHECKED: set[tuple[str, str, int]] = {
    ("1912-11-05", "governor", 3),
    ("1944-11-07", "governor", 2),
    ("1932-11-08", "governor", 2),
    ("1920-11-02", "governor", 5),
    ("1920-11-02", "governor", 4),
    ("1920-11-02", "governor", 3),
    ("1952-11-04", "governor", 5),
    ("1922-11-07", "senate", 1),
    ("1926-11-02", "senate", 0),
    ("1926-11-02", "senate", 1),
    ("1938-11-08", "senate", 3),
    ("1916-11-07", "president", 4),
    ("1920-11-02", "president", 1),
    ("1920-11-02", "president", 3),
    ("1920-11-02", "president", 4),
    ("1928-11-06", "president", 1),
    ("1932-11-08", "president", 1),
    ("1944-11-07", "president", 4),
}
NOTES: list[tuple[str, str, str]] = [
    (
        "1932-11-08",
        "president",
        "Monroe's figure for Hoover prints as 71 with its last place empty; it is read as 714, the figure ICPSR has.",
    ),
    (
        "1928-11-06",
        "president",
        "Monroe's figure for Smith prints with a broken third digit; it is read as 4,957, the figure ICPSR has.",
    ),
    (
        "1944-11-07",
        "senate",
        (
            "Sullivan's figure for McKittrick prints with a broken first digit that looks like an 8; it is read as "
            "3,314, which the printed total needs."
        ),
    ),
]
FIXES: dict[tuple[str, str, str, int], int] = {
    ("1900-11-06", "governor", "29033", 3): 62,
    ("1900-11-06", "governor", "29037", 3): 77,
    ("1900-11-06", "governor", "29039", 3): 23,
    ("1900-11-06", "governor", "29061", 3): 94,
    ("1900-11-06", "governor", "29077", 3): 51,
    ("1900-11-06", "governor", "29119", 3): 32,
    ("1900-11-06", "governor", "29123", 3): 16,
    ("1900-11-06", "governor", "29125", 3): 7,
    ("1900-11-06", "governor", "29167", 3): 27,
    ("1900-11-06", "governor", "29033", 4): 20,
    ("1900-11-06", "governor", "29039", 4): 20,
    ("1900-11-06", "governor", "29077", 4): 80,
    ("1900-11-06", "governor", "29117", 4): 22,
    ("1900-11-06", "governor", "29029", 2): 22,
    ("1900-11-06", "governor", "29049", 2): 26,
    ("1900-11-06", "governor", "29067", 2): 372,
    ("1900-11-06", "governor", "29009", 0): 2652,
    ("1900-11-06", "governor", "29011", 0): 2330,
    ("1900-11-06", "governor", "29127", 0): 3911,
    ("1900-11-06", "governor", "29053", 0): 2762,
    ("1912-11-05", "governor", "29135", 4): 0,
    ("1912-11-05", "governor", "29173", 4): 0,
    ("1912-11-05", "governor", "TOTAL", 4): 1861,
    ("1912-11-05", "governor", "29173", 3): 8,
    ("1904-11-08", "governor", "29001", 5): 65,
    ("1904-11-08", "governor", "29003", 5): 10,
    ("1904-11-08", "governor", "29005", 5): 12,
    ("1904-11-08", "governor", "29007", 5): 15,
    ("1904-11-08", "governor", "29009", 5): 22,
    ("1904-11-08", "governor", "29011", 5): 15,
    ("1904-11-08", "governor", "29013", 5): 48,
    ("1904-11-08", "governor", "29015", 5): 17,
    ("1904-11-08", "governor", "29017", 5): 5,
    ("1904-11-08", "governor", "29019", 5): 22,
    ("1904-11-08", "governor", "29021", 5): 26,
    ("1904-11-08", "governor", "29023", 5): 4,
    ("1904-11-08", "governor", "29025", 5): 33,
    ("1904-11-08", "governor", "29027", 5): 12,
    ("1904-11-08", "governor", "29029", 5): 25,
    ("1904-11-08", "governor", "29031", 5): 31,
    ("1904-11-08", "governor", "29033", 5): 26,
    ("1904-11-08", "governor", "29035", 5): 5,
    ("1904-11-08", "governor", "29037", 5): 15,
    ("1904-11-08", "governor", "29039", 5): 50,
    ("1904-11-08", "governor", "29041", 5): 22,
    ("1904-11-08", "governor", "29043", 5): 9,
    ("1904-11-08", "governor", "29045", 5): 7,
    ("1904-11-08", "governor", "29047", 5): 19,
    ("1904-11-08", "governor", "29049", 5): 15,
    ("1904-11-08", "governor", "29051", 5): 7,
    ("1904-11-08", "governor", "29053", 5): 31,
    ("1904-11-08", "governor", "29055", 5): 3,
    ("1904-11-08", "governor", "29057", 5): 36,
    ("1904-11-08", "governor", "29059", 5): 38,
    ("1904-11-08", "governor", "29061", 5): 37,
    ("1904-11-08", "governor", "29063", 5): 9,
    ("1904-11-08", "governor", "29065", 5): 18,
    ("1904-11-08", "governor", "29067", 5): 73,
    ("1904-11-08", "governor", "29069", 5): 11,
    ("1904-11-08", "governor", "29071", 5): 6,
    ("1904-11-08", "governor", "29073", 5): 3,
    ("1904-11-08", "governor", "29075", 5): 34,
    ("1904-11-08", "governor", "29077", 5): 87,
    ("1904-11-08", "governor", "29079", 5): 20,
    ("1904-11-08", "governor", "29081", 5): 28,
    ("1904-11-08", "governor", "29083", 5): 18,
    ("1904-11-08", "governor", "29085", 5): 51,
    ("1904-11-08", "governor", "29087", 5): 16,
    ("1904-11-08", "governor", "29089", 5): 10,
    ("1904-11-08", "governor", "29091", 5): 37,
    ("1904-11-08", "governor", "29093", 5): 11,
    ("1904-11-08", "governor", "29095", 5): 112,
    ("1904-11-08", "governor", "29097", 5): 39,
    ("1904-11-08", "governor", "29099", 5): 30,
    ("1904-11-08", "governor", "29101", 5): 16,
    ("1904-11-08", "governor", "29103", 5): 7,
    ("1904-11-08", "governor", "29105", 5): 32,
    ("1904-11-08", "governor", "29107", 5): 25,
    ("1904-11-08", "governor", "29109", 5): 9,
    ("1904-11-08", "governor", "29111", 5): 13,
    ("1904-11-08", "governor", "29113", 5): 7,
    ("1904-11-08", "governor", "29115", 5): 19,
    ("1904-11-08", "governor", "29117", 5): 110,
    ("1904-11-08", "governor", "29119", 5): 9,
    ("1904-11-08", "governor", "29121", 5): 59,
    ("1904-11-08", "governor", "29123", 5): 6,
    ("1904-11-08", "governor", "29125", 5): 2,
    ("1904-11-08", "governor", "29127", 5): 11,
    ("1904-11-08", "governor", "29129", 5): 8,
    ("1904-11-08", "governor", "29131", 5): 40,
    ("1904-11-08", "governor", "29133", 5): 12,
    ("1904-11-08", "governor", "29135", 5): 88,
    ("1904-11-08", "governor", "29137", 5): 18,
    ("1904-11-08", "governor", "29139", 5): 17,
    ("1904-11-08", "governor", "29141", 5): 10,
    ("1904-11-08", "governor", "29143", 5): 3,
    ("1904-11-08", "governor", "29145", 5): 27,
    ("1904-11-08", "governor", "29147", 5): 31,
    ("1904-11-08", "governor", "29149", 5): 6,
    ("1904-11-08", "governor", "29151", 5): 2,
    ("1904-11-08", "governor", "29153", 5): 15,
    ("1904-11-08", "governor", "29155", 5): 1,
    ("1904-11-08", "governor", "29157", 5): 7,
    ("1904-11-08", "governor", "29159", 5): 8,
    ("1904-11-08", "governor", "29161", 5): 61,
    ("1904-11-08", "governor", "29163", 5): 9,
    ("1904-11-08", "governor", "29165", 5): 2,
    ("1904-11-08", "governor", "29167", 5): 65,
    ("1904-11-08", "governor", "29169", 5): 3,
    ("1904-11-08", "governor", "29171", 5): 9,
    ("1904-11-08", "governor", "29173", 5): 1,
    ("1904-11-08", "governor", "29175", 5): 47,
    ("1904-11-08", "governor", "29177", 5): 7,
    ("1904-11-08", "governor", "29179", 5): 4,
    ("1904-11-08", "governor", "29181", 5): 63,
    ("1904-11-08", "governor", "29183", 5): 14,
    ("1904-11-08", "governor", "29185", 5): 29,
    ("1904-11-08", "governor", "29187", 5): 14,
    ("1904-11-08", "governor", "29186", 5): 6,
    ("1904-11-08", "governor", "29197", 5): 14,
    ("1904-11-08", "governor", "29199", 5): 9,
    ("1904-11-08", "governor", "29201", 5): 7,
    ("1904-11-08", "governor", "29203", 5): 10,
    ("1904-11-08", "governor", "29205", 5): 6,
    ("1904-11-08", "governor", "29207", 5): 8,
    ("1904-11-08", "governor", "29209", 5): 11,
    ("1904-11-08", "governor", "29211", 5): 9,
    ("1904-11-08", "governor", "29213", 5): 11,
    ("1904-11-08", "governor", "29215", 5): 18,
    ("1904-11-08", "governor", "29217", 5): 9,
    ("1904-11-08", "governor", "29219", 5): 9,
    ("1904-11-08", "governor", "29221", 5): 2,
    ("1904-11-08", "governor", "29223", 5): 7,
    ("1904-11-08", "governor", "29225", 5): 91,
    ("1904-11-08", "governor", "29227", 5): 16,
    ("1904-11-08", "governor", "29229", 5): 30,
    ("1904-11-08", "governor", "29510", 5): 149,
    ("1904-11-08", "governor", "29189", 5): 11,
    ("1904-11-08", "governor", "29195", 5): 27,
    ("1904-11-08", "governor", "TOTAL", 5): 2701,
    ("1904-11-08", "governor", "29039", 4): 3,
    ("1904-11-08", "governor", "29045", 4): 3,
    ("1904-11-08", "governor", "29065", 4): 0,
    ("1904-11-08", "governor", "29097", 4): 134,
    ("1904-11-08", "governor", "29125", 4): 0,
    ("1904-11-08", "governor", "29127", 4): 16,
    ("1904-11-08", "governor", "29153", 4): 2,
    ("1904-11-08", "governor", "29197", 4): 2,
    ("1904-11-08", "governor", "29203", 4): 8,
    ("1904-11-08", "governor", "29510", 4): 503,
    ("1904-11-08", "governor", "29189", 4): 28,
    ("1904-11-08", "governor", "TOTAL", 4): 1442,
    ("1904-11-08", "governor", "TOTAL", 3): 11031,
    ("1904-11-08", "governor", "29069", 3): 13,
    ("1904-11-08", "governor", "29163", 3): 9,
    ("1904-11-08", "governor", "29171", 3): 33,
    ("1904-11-08", "governor", "29177", 3): 11,
    ("1904-11-08", "governor", "29181", 3): 11,
    ("1904-11-08", "governor", "29207", 3): 132,
    ("1904-11-08", "governor", "29219", 3): 31,
    ("1904-11-08", "governor", "29127", 3): 60,
    ("1904-11-08", "governor", "29045", 2): 53,
    ("1904-11-08", "governor", "29129", 2): 31,
    ("1904-11-08", "governor", "29135", 2): 28,
    ("1904-11-08", "governor", "29179", 2): 3,
    ("1904-11-08", "governor", "29185", 2): 32,
    ("1904-11-08", "governor", "29179", 1): 491,
    ("1904-11-08", "governor", "29181", 1): 756,
    ("1904-11-08", "governor", "TOTAL", 1): 296552,
    ("1904-11-08", "governor", "29113", 0): 2360,
    ("1904-11-08", "governor", "29125", 0): 1217,
    ("1904-11-08", "governor", "29143", 0): 1292,
    ("1904-11-08", "governor", "29165", 0): 2613,
    ("1904-11-08", "governor", "29227", 0): 1035,
    ("1904-11-08", "governor", "TOTAL", 0): 326652,
    ("1936-11-03", "governor", "29005", 3): 0,
    ("1936-11-03", "governor", "29043", 3): 0,
    ("1892-11-08", "governor", "TOTAL", 3): 3393,
    ("1892-11-08", "governor", "TOTAL", 2): 37262,
    ("1892-11-08", "governor", "29005", 3): 53,
    ("1892-11-08", "governor", "29009", 3): 33,
    ("1892-11-08", "governor", "29037", 3): 59,
    ("1892-11-08", "governor", "29047", 3): 61,
    ("1892-11-08", "governor", "29049", 3): 37,
    ("1892-11-08", "governor", "29063", 3): 23,
    ("1892-11-08", "governor", "29071", 3): 13,
    ("1892-11-08", "governor", "29089", 3): 33,
    ("1892-11-08", "governor", "29101", 3): 39,
    ("1892-11-08", "governor", "29109", 3): 53,
    ("1892-11-08", "governor", "29117", 3): 33,
    ("1892-11-08", "governor", "29119", 3): 15,
    ("1892-11-08", "governor", "29121", 3): 36,
    ("1892-11-08", "governor", "29123", 3): 26,
    ("1892-11-08", "governor", "29125", 3): 3,
    ("1892-11-08", "governor", "29127", 3): 83,
    ("1892-11-08", "governor", "29131", 3): 5,
    ("1892-11-08", "governor", "29133", 3): 13,
    ("1892-11-08", "governor", "29135", 3): 12,
    ("1892-11-08", "governor", "29137", 3): 14,
    ("1892-11-08", "governor", "29141", 3): 6,
    ("1892-11-08", "governor", "29147", 3): 83,
    ("1892-11-08", "governor", "29167", 3): 21,
    ("1892-11-08", "governor", "29171", 3): 19,
    ("1892-11-08", "governor", "29181", 3): 1,
    ("1892-11-08", "governor", "29185", 3): 17,
    ("1892-11-08", "governor", "29197", 3): 13,
    ("1892-11-08", "governor", "29227", 3): 53,
    ("1892-11-08", "governor", "29510", 3): 203,
    ("1892-11-08", "governor", "29003", 2): 371,
    ("1892-11-08", "governor", "29005", 2): 1083,
    ("1892-11-08", "governor", "29017", 2): 43,
    ("1892-11-08", "governor", "29019", 2): 203,
    ("1892-11-08", "governor", "29029", 2): 283,
    ("1892-11-08", "governor", "29045", 2): 13,
    ("1892-11-08", "governor", "29055", 2): 35,
    ("1892-11-08", "governor", "29065", 2): 23,
    ("1892-11-08", "governor", "29083", 2): 349,
    ("1892-11-08", "governor", "29103", 2): 544,
    ("1892-11-08", "governor", "29109", 2): 753,
    ("1892-11-08", "governor", "29119", 2): 302,
    ("1892-11-08", "governor", "29121", 2): 655,
    ("1892-11-08", "governor", "29123", 2): 64,
    ("1892-11-08", "governor", "29131", 2): 286,
    ("1892-11-08", "governor", "29133", 2): 58,
    ("1892-11-08", "governor", "29135", 2): 603,
    ("1892-11-08", "governor", "29139", 2): 150,
    ("1892-11-08", "governor", "29141", 2): 275,
    ("1892-11-08", "governor", "29153", 2): 253,
    ("1892-11-08", "governor", "29155", 2): 9,
    ("1892-11-08", "governor", "29161", 2): 198,
    ("1892-11-08", "governor", "29165", 2): 135,
    ("1892-11-08", "governor", "29183", 2): 25,
    ("1892-11-08", "governor", "29185", 2): 520,
    ("1892-11-08", "governor", "29195", 2): 582,
    ("1892-11-08", "governor", "29199", 2): 458,
    ("1892-11-08", "governor", "29201", 2): 50,
    ("1892-11-08", "governor", "29205", 2): 93,
    ("1892-11-08", "governor", "29211", 2): 63,
    ("1892-11-08", "governor", "29213", 2): 76,
    ("1892-11-08", "governor", "29215", 2): 329,
    ("1892-11-08", "governor", "29221", 2): 9,
    ("1892-11-08", "governor", "29223", 2): 43,
    ("1892-11-08", "governor", "29225", 2): 362,
    ("1892-11-08", "governor", "29229", 2): 606,
    ("1892-11-08", "governor", "29035", 1): 377,
    ("1892-11-08", "governor", "29047", 1): 783,
    ("1892-11-08", "governor", "29073", 1): 1637,
    ("1892-11-08", "governor", "29079", 1): 2535,
    ("1892-11-08", "governor", "29113", 1): 1392,
    ("1892-11-08", "governor", "29121", 1): 2844,
    ("1892-11-08", "governor", "29179", 1): 282,
    ("1892-11-08", "governor", "29195", 1): 2689,
    ("1892-11-08", "governor", "29199", 1): 973,
    ("1892-11-08", "governor", "29215", 1): 1319,
    ("1892-11-08", "governor", "29510", 1): 37253,
    ("1892-11-08", "governor", "29037", 0): 3028,
    ("1892-11-08", "governor", "29073", 0): 593,
    ("1892-11-08", "governor", "29101", 0): 3097,
    ("1892-11-08", "governor", "29119", 0): 1033,
    ("1892-11-08", "governor", "29121", 0): 3277,
    ("1892-11-08", "governor", "29137", 0): 3854,
    ("1892-11-08", "governor", "29141", 0): 1135,
    ("1892-11-08", "governor", "29153", 0): 385,
    ("1892-11-08", "governor", "29163", 0): 3654,
    ("1892-11-08", "governor", "29175", 0): 3628,
    ("1892-11-08", "governor", "29181", 0): 809,
    ("1892-11-08", "governor", "29189", 0): 3079,
    ("1892-11-08", "governor", "29510", 0): 32839,
    ("1944-11-07", "governor", "29045", 2): 2,
    ("1944-11-07", "governor", "29133", 2): 9,
    ("1944-11-07", "governor", "29135", 2): 3,
    ("1944-11-07", "governor", "29143", 2): 3,
    ("1944-11-07", "governor", "29185", 2): 2,
    ("1932-11-08", "governor", "29185", 3): 6,
    ("1932-11-08", "governor", "29207", 3): 3,
    ("1932-11-08", "governor", "29225", 3): 3,
    ("1920-11-02", "governor", "29001", 5): 8,
    ("1920-11-02", "governor", "29003", 5): 5,
    ("1920-11-02", "governor", "29005", 5): 3,
    ("1920-11-02", "governor", "29007", 5): 6,
    ("1920-11-02", "governor", "29013", 5): 16,
    ("1920-11-02", "governor", "29183", 5): 13,
    ("1920-11-02", "governor", "29187", 5): 3,
    ("1920-11-02", "governor", "29189", 5): 237,
    ("1920-11-02", "governor", "29203", 5): 3,
    ("1920-11-02", "governor", "29213", 5): 3,
    ("1920-11-02", "governor", "29001", 4): 54,
    ("1920-11-02", "governor", "29005", 4): 18,
    ("1920-11-02", "governor", "29007", 4): 21,
    ("1920-11-02", "governor", "29183", 4): 19,
    ("1920-11-02", "governor", "29185", 4): 37,
    ("1920-11-02", "governor", "29197", 4): 16,
    ("1920-11-02", "governor", "29201", 4): 30,
    ("1920-11-02", "governor", "29217", 4): 32,
    ("1920-11-02", "governor", "29219", 4): 16,
    ("1920-11-02", "governor", "29510", 4): 670,
    ("1920-11-02", "governor", "29001", 3): 32,
    ("1920-11-02", "governor", "29007", 3): 2,
    ("1920-11-02", "governor", "29013", 3): 14,
    ("1920-11-02", "governor", "29211", 3): 3,
    ("1928-11-06", "senate", "29510", 0): 170645,
    ("1944-11-07", "senate", "29211", 0): 3314,
    ("1944-11-07", "senate", "29091", 2): 3,
    ("1944-11-07", "senate", "29105", 2): 3,
    ("1944-11-07", "senate", "29117", 2): 3,
    ("1944-11-07", "senate", "29135", 2): 3,
    ("1944-11-07", "senate", "TOTAL", 2): 1320,
    ("1892-11-08", "president", "29041", 0): 3463,
    ("1892-11-08", "president", "29047", 0): 3085,
    ("1892-11-08", "president", "29063", 0): 1372,
    ("1892-11-08", "president", "29127", 0): 3634,
    ("1892-11-08", "president", "29035", 1): 377,
    ("1892-11-08", "president", "29065", 1): 896,
    ("1892-11-08", "president", "29083", 1): 2563,
    ("1892-11-08", "president", "29109", 1): 2623,
    ("1892-11-08", "president", "29159", 1): 3610,
    ("1892-11-08", "president", "29510", 1): 35528,
    ("1892-11-08", "president", "29189", 2): 30,
    ("1892-11-08", "president", "29207", 2): 131,
    ("1892-11-08", "president", "29009", 3): 37,
    ("1892-11-08", "president", "29017", 3): 3,
    ("1892-11-08", "president", "29061", 3): 34,
    ("1892-11-08", "president", "29083", 3): 103,
    ("1892-11-08", "president", "29111", 3): 37,
    ("1892-11-08", "president", "TOTAL", 3): 4333,
    ("1896-11-03", "president", "29510", 0): 50091,
    ("1896-11-03", "president", "29037", 0): 3975,
    ("1896-11-03", "president", "TOTAL", 1): 304940,
    ("1896-11-03", "president", "TOTAL", 4): 2365,
    ("1896-11-03", "president", "29149", 2): 3,
    ("1896-11-03", "president", "29157", 2): 3,
    ("1904-11-08", "president", "29165", 1): 953,
    ("1904-11-08", "president", "29183", 1): 3203,
    ("1904-11-08", "president", "29510", 1): 57547,
    ("1904-11-08", "president", "29175", 2): 66,
    ("1904-11-08", "president", "29510", 2): 296,
    ("1904-11-08", "president", "29139", 5): 24,
    ("1904-11-08", "president", "29225", 5): 123,
    ("1904-11-08", "president", "29227", 5): 29,
    ("1916-11-07", "president", "29147", 4): 9,
    ("1920-11-02", "president", "TOTAL", 4): 3291,
    ("1920-11-02", "president", "TOTAL", 5): 5142,
    ("1924-11-04", "president", "29129", 4): 0,
    ("1924-11-04", "president", "29141", 4): 0,
    ("1924-11-04", "president", "29187", 4): 3,
    ("1924-11-04", "president", "TOTAL", 4): 883,
    ("1928-11-06", "president", "29137", 1): 4957,
    ("1940-11-05", "president", "29023", 4): 23,
    ("1940-11-05", "president", "29039", 4): 16,
    ("1940-11-05", "president", "29053", 4): 13,
    ("1940-11-05", "president", "29065", 4): 3,
    ("1940-11-05", "president", "29109", 4): 39,
    ("1940-11-05", "president", "29127", 4): 33,
    ("1932-11-08", "president", "29059", 1): 1958,
    ("1932-11-08", "president", "29137", 1): 714,
    ("1944-11-07", "president", "29007", 3): 0,
    ("1952-11-04", "president", "29173", 3): 1,
    ("1934-11-06", "senate", "29123", 2): 5,
    ("1934-11-06", "senate", "29153", 2): 14,
    ("1934-11-06", "senate", "29063", 3): 1,
    ("1934-11-06", "senate", "29085", 3): 1,
    ("1934-11-06", "senate", "29121", 3): 1,
    ("1934-11-06", "senate", "29151", 3): 1,
}
PARTIES = {
    "DEM": "democratic-party",
    "REP": "republican-party",
    "SOC": "socialist-party-of-america",
    "SLP": "socialist-labor-party-of-america",
    "PRO": "prohibition-party",
    "PPL": "peoples-party",
    "PRG": "progressive-party-1912",
    "WPRG": "MO:progressive-party",
    "LIB": "progressive-party-1924",
    "COM": "MO:communist",
    "UNI": "union-party-of-1936",
    "CN": "MO:christian-nationalist",
    "SDEM": "MO:social-democrat",
    "FW": "MO:farmer-work",
    "NDEM": None,
    "NPRO": None,
    "FL": None,
    "CWL": None,
    "AF": None,
    "NP": None,
    "MO:SOC": "MO:socialist",
    "MO:SLP": "MO:socialist-labor",
}
NAMES: dict[tuple[str, str, str], str] = {
    ("1892-11-08", "Weaver", "PPL"): "James B. Weaver",
    ("1892-11-08", "Bidwell", "PRO"): "John Bidwell",
    ("1896-11-03", "Bryan", "DEM"): "William Jennings Bryan",
    ("1896-11-03", "McKinley", "REP"): "William McKinley",
    ("1896-11-03", "Levering", "PRO"): "Joshua Levering",
    ("1896-11-03", "Matchett", "SLP"): "Charles H. Matchett",
    ("1896-11-03", "Palmer", "NDEM"): "John M. Palmer",
    ("1896-11-03", "Bentley", "NPRO"): "Charles E. Bentley",
    ("1900-11-06", "Bryan", "DEM"): "William Jennings Bryan",
    ("1900-11-06", "McKinley", "REP"): "William McKinley",
    ("1900-11-06", "Barker", "PPL"): "Wharton Barker",
    ("1900-11-06", "Woolley", "PRO"): "John G. Woolley",
    ("1900-11-06", "Debs", "SOC"): "Eugene V. Debs",
    ("1900-11-06", "Malloney", "SLP"): "Joseph F. Malloney",
    ("1904-11-08", "Parker", "DEM"): "Alton B. Parker",
    ("1904-11-08", "Roosevelt", "REP"): "Theodore Roosevelt",
    ("1904-11-08", "Swallow", "PRO"): "Silas C. Swallow",
    ("1904-11-08", "Debs", "SOC"): "Eugene V. Debs",
    ("1904-11-08", "Corrigan", "SLP"): "Charles H. Corregan",
    ("1904-11-08", "Watson", "PPL"): "Thomas E. Watson",
    ("1924-11-04", "Wallace", "CWL"): "William J. Wallace",
    ("1932-11-08", "Reynolds", "SLP"): "Verne L. Reynolds",
    ("1932-11-08", "Upshaw", "PRO"): "William David Upshaw",
    ("1932-11-08", "Foster", "COM"): "William Z. Foster",
    ("1936-11-03", "Aiken", "SLP"): "John W. Aiken",
    ("1936-11-03", "Colvin", "PRO"): "D. Leigh Colvin",
    ("1936-11-03", "Browder", "COM"): "Earl Browder",
    ("1940-11-05", "Aiken", "SLP"): "John W. Aiken",
    ("1940-11-05", "Babson", "PRO"): "Roger W. Babson",
    ("1944-11-07", "Teichert", "SLP"): "Edward A. Teichert",
    ("1944-11-07", "Watson", "PRO"): "Claude A. Watson",
    ("1952-11-04", "Hoopes", "SOC"): "Darlington Hoopes",
    ("1952-11-04", "MacArthur", "CN"): "Douglas MacArthur",
    ("1952-11-04", "MacArthur", "AF"): "Douglas MacArthur",
    ("1892-11-08", "Leonard", "PPL"): "Leverett Leonard",
    ("1892-11-08", "Sobieski", "PRO"): "John Sobieski",
    ("1896-11-03", "Faris", "PRO"): "Herman P. Faris",
    ("1896-11-03", "Fry", "SLP"): "Lewis C. Fry",
    ("1900-11-06", "Hillis", "PPL"): "J. H. Hillis",
    ("1900-11-06", "Stokes", "PRO"): "Charles E. Stokes",
    ("1900-11-06", "Lipscomb", "SDEM"): "Caleb Lipscomb",
    ("1900-11-06", "Fry", "SLP"): "Lewis C. Fry",
    ("1904-11-08", "Hill", "PRO"): "Orange J. Hill",
    ("1904-11-08", "Behrens", "SOC"): "E. T. Behrens",
    ("1904-11-08", "White", "SLP"): "J. Edward White",
    ("1904-11-08", "Alldredge", "PPL"): "William C. Alldredge",
    ("1912-11-05", "Stokes", "PRO"): "Charles E. Stokes",
    ("1912-11-05", "Ward", "SOC"): "Ward",
    ("1912-11-05", "Rogers", "SLP"): "Charles Rogers",
    ("1912-11-05", "Nortoni", "PRG"): "Albert D. Nortoni",
    ("1920-11-02", "Aldrich", "SOC"): "Marvin M. Aldrich",
    ("1920-11-02", "Middlecoff", "SLP"): "E. T. Middlecoff",
    ("1920-11-02", "Faris", "PRO"): "Herman P. Faris",
    ("1920-11-02", "Hickman", "FW"): "Vaughan Hickman",
    ("1924-11-04", "Baker", "REP"): "Sam Aaron Baker",
    ("1924-11-04", "Brandt", "SOC"): "William E. Brandt",
    ("1924-11-04", "Cox", "SLP"): "William W. Cox",
    ("1928-11-06", "Hodges", "SOC"): "Joseph G. Hodges",
    ("1928-11-06", "Middlecoff", "SLP"): "Edward G. Middlecoff",
    ("1932-11-08", "Wolf", "SOC"): "Louis Martin Wolf",
    ("1932-11-08", "Cox", "SLP"): "William W. Cox",
    ("1932-11-08", "Penney", "COM"): "Owen W. Penney",
    ("1936-11-03", "Duemler", "SOC"): "George K. Duemler",
    ("1936-11-03", "Cox", "SLP"): "William W. Cox",
    ("1936-11-03", "Williams", "COM"): "Frank A. Williams",
    ("1936-11-03", "Botz", "NP"): "Botz",
    ("1940-11-05", "High", "SOC"): "Jed A. High",
    ("1940-11-05", "Cox", "SLP"): "William W. Cox",
    ("1944-11-07", "Rinck", "SOC"): "William F. Rinck",
    ("1944-11-07", "Tendler", "SLP"): "Stephen Tendler",
    ("1948-11-02", "Gipe", "SOC"): "Gipe",
    ("1952-11-04", "Edsell", "WPRG"): "Edsell",
    ("1952-11-04", "Wheeler", "SOC"): "Wheeler",
    ("1952-11-04", "Hayes", "SLP"): "Hayes",
    ("1952-11-04", "Lohbeck", "CN"): "Lohbeck",
    ("1948-11-02", "Genck", "SLP"): "Henry W. Genck",
    ("1948-11-02", "Logsdon", "WPRG"): "Robert B. Logsdon",
}
LANGUAGES = ("eng", "deu", "ita", "spa", "nld", "por", "swe", "pol", "frk")
DEBUG: set[str] = set()


def fetch() -> None:
    for page in sorted({segment[0] for _, _, segments, _ in TABLES for segment in segments}):
        raw.fetch(SOURCE, IMAGE.format(page), suffix=".jpg")


def words(page: int) -> dict[str, list[tuple[float, float, float, str]]]:
    clean = PARSED / "ocr" / f"{page}-clean.png"
    if not clean.exists():
        pixels = np.array(
            ImageOps.autocontrast(Image.open(raw.cached(SOURCE, IMAGE.format(page)).path).convert("L"), cutoff=2)
        )
        dark = pixels < 215
        above = np.zeros(dark.shape, dtype=np.int32)
        below = np.zeros(dark.shape, dtype=np.int32)
        for y in range(1, dark.shape[0]):
            above[y] = (above[y - 1] + 1) * dark[y]
        for y in range(dark.shape[0] - 2, -1, -1):
            below[y] = (below[y + 1] + 1) * dark[y]
        rule = above + below > 80
        rule = rule | np.roll(rule, 1, 1) | np.roll(rule, -1, 1) | np.roll(rule, 2, 1) | np.roll(rule, -2, 1)
        pixels[rule & (pixels > 100)] = 255
        Image.fromarray(pixels).save(clean)
    source = Image.open(clean)
    double = source.resize((source.width * 2, source.height * 2), Image.Resampling.LANCZOS)
    triple = source.resize((source.width * 3, source.height * 3), Image.Resampling.LANCZOS)
    variants = {
        "x2unsharp": (2.0, double.filter(ImageFilter.UnsharpMask(4, 200, 2))),
        "x2": (2.0, double),
        "x3sharp": (3.0, triple.filter(ImageFilter.SHARPEN)),
        "x2local": (
            2.0,
            ImageChops.subtract(double.filter(ImageFilter.GaussianBlur(40)), double).point(
                lambda value: 255 - min(255, value * 4)
            ),
        ),
        "plain": (1.0, source.filter(ImageFilter.SHARPEN)),
    }
    found: dict[str, list[tuple[float, float, float, str]]] = {}
    for mode, (scale, picture) in variants.items():
        cache = PARSED / "ocr" / f"{page}-{mode}.tsv"
        if not cache.exists():
            buffer = io.BytesIO()
            picture.save(buffer, "PNG")
            cache.write_text(
                subprocess.run(
                    ["tesseract", "stdin", "stdout", "--psm", "6", "tsv"],
                    input=buffer.getvalue(),
                    capture_output=True,
                    check=True,
                ).stdout.decode()
            )
        found[mode] = [
            (
                (int(row["left"]) + int(row["width"])) / scale,
                (int(row["top"]) + int(row["height"]) / 2) / scale,
                int(row["left"]) / scale,
                row["text"].strip(),
            )
            for row in csv.DictReader(io.StringIO(cache.read_text()), delimiter="\t", quoting=csv.QUOTE_NONE)
            if (row["text"] or "").strip()
        ]
    return found


def segment(
    page: int,
    box: tuple[float, float, float, float],
    expected: dict[str, list[int]],
    order: list[str],
) -> dict[str, tuple[int, list[list[int]]]]:
    found = words(page)
    picture = Image.open(PARSED / "ocr" / f"{page}-clean.png")
    top, bottom = box[0] * picture.height, box[1] * picture.height
    left, right = box[2] * picture.width, box[3] * picture.width
    tokens: list[tuple[float, float, int]] = []
    for found_words in found.values():
        pieces = sorted(
            (
                (start, right_edge, middle, re.sub(r"(?<=\d)\.(?=\d{3})", ",", raw_text.strip("|)]}!'([{.")))
                for right_edge, middle, start, raw_text in found_words
                if top <= middle <= bottom and left <= start and right_edge <= right
            )
        )
        runs: list[list[tuple[float, float, float, str]]] = []
        for piece in pieces:
            run = next(
                (
                    run
                    for run in reversed(runs)
                    if re.fullmatch(r"[\d,]+", piece[3])
                    and re.fullmatch(r"[\d,]+", run[-1][3])
                    and -2 < piece[0] - run[-1][1] < 30
                    and abs(piece[2] - run[-1][2]) < (14 if "," in (piece[3], run[-1][3]) else 8)
                ),
                None,
            )
            if run is None:
                runs.append([piece])
            else:
                run.append(piece)
        for run in runs:
            joined = "".join(piece[3] for piece in run).strip(",")
            group = (
                [(run[-1][1], run[-1][2], joined)]
                if len(run) > 1 and re.fullmatch(r"\d{1,3}(,\d{3})+", joined)
                else [(piece[1], piece[2], piece[3].strip(",")) for piece in run]
            )
            for right_edge, middle, text in group:
                if re.fullmatch(r"\d{1,3}(,\d{3})*|\d{4,6}", text):
                    tokens.append((right_edge, middle, int(text.replace(",", ""))))
    places: dict[int, list[tuple[float, float]]] = {}
    for x, y, value in tokens:
        places.setdefault(value, []).append((x, y))
    size = len(next(iter(expected.values())))

    def anchor(slope: float, tolerance: float) -> dict[str, tuple[float, list[tuple[int, float, float]]]]:
        anchored: dict[str, tuple[float, list[tuple[int, float, float]]]] = {}
        for county, values in expected.items():
            hits = [
                (column, x, y - slope * x)
                for column, value in enumerate(values)
                if value >= 10
                for x, y in places.get(value, [])
            ]
            if not hits:
                continue
            middle = max(
                (hit[2] for hit in hits),
                key=lambda y: len({column for column, _, other in hits if abs(other - y) <= tolerance}),
            )
            support = [hit for hit in hits if abs(hit[2] - middle) <= tolerance]
            columns = {column for column, _, _ in support}
            if len(columns) >= min(2, len([value for value in values if value >= 10])):
                anchored[county] = (statistics.median(y for _, _, y in support), support)
        taken: dict[int, str] = {}
        for county, (middle, support) in sorted(anchored.items(), key=lambda item: -len(item[1][1])):
            if any(abs(middle - other) < 10 for other in (anchored[one][0] for one in taken.values())):
                continue
            taken[len(taken)] = county
        return {county: anchored[county] for county in taken.values()}

    first = anchor(0.0, 14)
    slopes = [
        (b[2] - a[2]) / (b[1] - a[1])
        for _, support in first.values()
        for a, b in itertools.combinations(support, 2)
        if abs(b[1] - a[1]) > 200
    ]
    slope = statistics.median(slopes) if slopes else 0.0
    anchored = anchor(slope, 8)
    ranked = sorted(
        (order.index(county), middle, county) for county, (middle, _) in anchored.items() if county in order
    )
    chains: list[list[str]] = []
    for position, (_, middle, county) in enumerate(ranked):
        chains.append(
            max(
                (chains[earlier] for earlier in range(position) if ranked[earlier][1] < middle),
                key=len,
                default=[],
            )
            + [county]
        )
    chain = max(chains, key=len, default=[])
    keep = set(chain)
    kept = [(order.index(county), anchored[county][0]) for county in chain]
    if len(kept) >= 2:
        step = statistics.median((b[1] - a[1]) / (b[0] - a[0]) for a, b in itertools.pairwise(kept))
        for index, middle, county in ranked:
            near = min(kept, key=lambda one: abs(one[0] - index))
            if county not in keep and abs(near[1] + (index - near[0]) * step - middle) <= step * 4.5:
                keep.add(county)
    anchored = {county: value for county, value in anchored.items() if county in keep or county not in order}
    if len(anchored) < 3:
        return {}
    matched: dict[int, list[float]] = {}
    for county, (middle, _) in anchored.items():
        for column, value in enumerate(expected[county]):
            if value:
                matched.setdefault(column, []).extend(
                    x for x, y in places.get(value, []) if abs(y - slope * x - middle) <= 8
                )
    rough = {column: statistics.median(xs) for column, xs in matched.items() if xs}
    spacing = statistics.median(
        [
            rough[column + 1] - rough[column]
            for column in rough
            if column + 1 in rough and rough[column + 1] > rough[column]
        ]
        or [140]
    )
    spots: dict[int, float] = {}
    for column in sorted(rough):
        near = [
            x for x in matched[column] if column - 1 not in spots or abs(x - spots[column - 1] - spacing) <= spacing / 2
        ]
        if near:
            spots[column] = statistics.median(near)
    lines = {county: middle for county, (middle, _) in anchored.items()}
    indexes = sorted((order.index(county), middle) for county, middle in lines.items() if county in order[:-1])
    pitch = statistics.median((b[1] - a[1]) / (b[0] - a[0]) for a, b in itertools.pairwise(indexes) if b[0] > a[0])
    for (low, low_y), (high, high_y) in itertools.pairwise(indexes):
        if (
            high - low > 1
            and abs(high_y - low_y - (high - low) * pitch) < pitch * 0.4 * (high - low - 1) ** 0.5
            or high - low == 2
            and pitch * 1.3 < high_y - low_y < pitch * 2.7
        ):
            for index in range(low + 1, high):
                lines[order[index]] = low_y + (index - low) * (high_y - low_y) / (high - low)
    for start, step in ((indexes[0], -1), (indexes[-1], 1)):
        for distance in (1, 2):
            index = start[0] + step * distance
            middle = start[1] + step * distance * pitch
            if (
                0 <= index < len(order)
                and order[index] not in lines
                and top <= middle + slope * left <= bottom
                and (
                    distance == 1
                    or any(
                        any(abs(x - spot) <= 14 for spot in spots.values())
                        and abs(y - slope * x - middle) <= pitch * 0.4
                        for x, y, _ in tokens
                    )
                )
            ):
                lines[order[index]] = middle
    labeled = [
        y - slope * x
        for found_words in found.values()
        for _, y, x, text in found_words
        if top <= y <= bottom and left <= x <= right and re.match(r"Totals?\b", text)
    ]
    if labeled and "TOTAL" not in anchored:
        lines["TOTAL"] = statistics.median(labeled)
    if DEBUG and "rows" in DEBUG:
        print(
            page,
            slope,
            pitch,
            spots,
            [(order[index], round(middle)) for index, middle in indexes],
            sorted((round(y), c) for c, y in lines.items()),
        )
    gaps: list[list[int]] = []
    for column in range(size):
        if column not in spots:
            if gaps and gaps[-1][-1] == column - 1:
                gaps[-1].append(column)
            else:
                gaps.append([column])
    for run in gaps:
        low = spots.get(run[0] - 1, left) + 40
        high = (
            spots[run[-1] + 1] - 40
            if run[-1] + 1 in spots
            else spots[run[0] - 1] + spacing * (len(run) + 0.5)
            if run[0] - 1 in spots
            else right
        )
        xs = sorted(
            x
            for x, y, _ in tokens
            if low <= x <= high and any(abs(y - slope * x - middle) <= 8 for middle in lines.values())
        )
        for reach in (40, 12):
            clusters: list[list[float]] = []
            for x in xs:
                if clusters and x - clusters[-1][-1] <= reach:
                    clusters[-1].append(x)
                else:
                    clusters.append([x])
            clusters = [cluster for cluster in clusters if len(cluster) >= 2]
            if len(clusters) >= len(run):
                break
        clusters = sorted(sorted(clusters, key=len)[-len(run) :], key=lambda cluster: cluster[0])
        if len(clusters) == len(run) and all(len(cluster) >= 2 for cluster in clusters):
            for column, cluster in zip(run, clusters, strict=True):
                spots[column] = statistics.median(cluster)
        elif run[0] - 1 in spots:
            picks: dict[int, float] = {}
            previous = spots[run[0] - 1]
            for column in run:
                near = [
                    cluster
                    for cluster in clusters
                    if len(cluster) >= 2 and abs(statistics.median(cluster) - previous - spacing) <= spacing / 4
                ]
                previous = statistics.median(max(near, key=len)) if near else previous + spacing
                if near:
                    picks[column] = previous
            if picks and run[-1] in picks or run[-1] + 1 in spots:
                for column in run:
                    if column not in picks:
                        before = max((one for one in picks if one < column), default=None)
                        after = min((one for one in picks if one > column), default=None)
                        if before is not None and after is not None:
                            picks[column] = picks[before] + (picks[after] - picks[before]) * (column - before) / (
                                after - before
                            )
                spots.update(picks)
    missing = [column for column in range(size) if column not in spots]
    if missing:
        raise ValueError(f"{page}: no column found for {missing}")
    cache = PARSED / "ocr" / f"{page}-{box[0]}-{box[1]}-{box[2]}-{box[3]}-cells.json"
    known: dict[str, list[str]] = json.loads(cache.read_text()) if cache.exists() else {}
    reaches = {
        column: min(150, int(spot - spots[column - 1]) - 12) if column - 1 in spots else 150
        for column, spot in spots.items()
    }
    jobs: dict[str, list[tuple[bytes, str, str]]] = {}
    for middle in lines.values():
        for column, spot in spots.items():
            reach = reaches[column]
            key = f"{round(spot)},{round(middle)}" + ("" if reach == 150 else f",{reach}")
            if key in known or key in jobs:
                continue
            actual = middle + slope * spot
            crop = picture.crop((int(spot - reach), int(actual - 15), int(spot + 8), int(actual + 10)))
            ink = crop.tobytes()
            inked = [x for x in range(crop.width) if any(ink[y * crop.width + x] < 110 for y in range(6, 13))]
            group = [inked[-1]] if inked and inked[-1] >= crop.width - 30 else []
            for x in reversed(inked[:-1] if group else []):
                if group[-1] - x > 30:
                    break
                group.append(x)
            jobs[key] = []
            if group:
                tight = crop.crop((max(0, group[-1] - 4), 0, min(crop.width, group[0] + 5), crop.height))
                wide = crop.crop((max(0, group[-1] - 8), 0, min(crop.width, group[0] + 6), crop.height))
                double = tight.resize((tight.width * 2, tight.height * 2), Image.Resampling.LANCZOS)
                triple = tight.resize((tight.width * 3, tight.height * 3), Image.Resampling.LANCZOS)
                wide = wide.resize((wide.width * 3, wide.height * 3), Image.Resampling.LANCZOS)
                for variant, psm, language in (
                    (double.point(lambda value: 0 if value < 160 else 255), "7", "eng"),
                    (triple, "10", "eng"),
                    *((wide, "7", language) for language in LANGUAGES),
                ):
                    buffer = io.BytesIO()
                    ImageOps.expand(variant, 30, 255).save(buffer, "PNG")
                    jobs[key].append((buffer.getvalue(), psm, language))
    flat = [(key, index, job) for key, images in jobs.items() for index, job in enumerate(images)]
    batches = [
        chunk
        for _, group in itertools.groupby(sorted(flat, key=lambda item: item[2][1:]), key=lambda item: item[2][1:])
        for chunk in itertools.batched(group, 200)
    ]
    with tempfile.TemporaryDirectory() as folder:

        def read(batch: tuple[tuple[str, int, tuple[bytes, str, str]], ...]) -> list[str]:
            names = []
            for key, index, (image, _, _) in batch:
                path = Path(folder) / f"{key.replace(',', '-')}-{index}.png"
                path.write_bytes(image)
                names.append(str(path))
            listing = Path(folder) / f"{batch[0][0].replace(',', '-')}-{batch[0][1]}.txt"
            listing.write_text("\n".join(names) + "\n")
            _, psm, language = batch[0][2]
            output = subprocess.run(
                [
                    "tesseract",
                    str(listing),
                    "stdout",
                    "-l",
                    language,
                    "--psm",
                    psm,
                    "-c",
                    "tessedit_char_whitelist=0123456789,.",
                ],
                capture_output=True,
                check=True,
            ).stdout.decode()
            return [text.strip() for text in output.split("\f")][: len(batch)]

        with ThreadPoolExecutor(8) as pool:
            for batch, texts in zip(batches, pool.map(read, batches), strict=True):
                if len(texts) != len(batch):
                    raise ValueError(f"{page}: tesseract returned {len(texts)} of {len(batch)} cells")
                for (key, index, _), text in zip(batch, texts, strict=True):
                    known.setdefault(key, [""] * len(jobs[key]))[index] = text
    for key in jobs:
        known.setdefault(key, [])
    cache.write_text(json.dumps(known))
    cells: dict[str, tuple[int, list[list[int]]]] = {}
    for county, middle in lines.items():
        readings: list[list[int]] = []
        for column in range(size):
            spot = spots[column]
            cell = [
                value for x, y, value in tokens if abs(x - spot) <= 14 and abs(y - slope * x - middle) <= pitch * 0.4
            ]
            key = f"{round(spot)},{round(middle)}" + ("" if reaches[column] == 150 else f",{reaches[column]}")
            if not known[key]:
                cell = []
            for found_text in known[key]:
                text = re.sub(r"(?<=\d)\.(?=\d{3})", ",", found_text.strip(" .,"))
                if re.fullmatch(r"\d{1,3}(,\d{3})*", text) or (
                    re.fullmatch(r"\d{4,6}", text) and not any(re.search(r"\d[.,]\d", one) for one in known[key])
                ):
                    cell.append(int(text.replace(",", "")))
            if DEBUG and county in DEBUG:
                print(page, county, column, cell, known[key])
            readings.append(cell)
        cells[county] = (len(anchored[county][1]) if county in anchored else 0, readings)
    return cells


def parse() -> None:
    (PARSED / "ocr").mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '29%' and u.code <> '29510'"""
            ).fetchall()
        }
    if len(codes) != 114:
        raise ValueError("Missouri needs 114 county names")
    jackson = codes["jackson"]
    base = [
        codes[key]
        for key in sorted(
            codes,
            key=lambda key: re.sub(
                r"^mc", "mac", re.sub(r"^ste?(?=charles|clair|genevieve|francois|louis)", "saint", key)
            ),
        )
    ] + ["29510", "TOTAL"]
    rows: list[dict[str, object]] = []
    caveats: list[dict[str, object]] = [{"day": day, "office": office, "note": note} for day, office, note in NOTES]
    for day, office, segments, candidates in TABLES:
        with connect() as conn:
            offices = [candidate[2] if len(candidate) > 2 else office for candidate in candidates]
            diagnostic: dict[tuple[str, int], int] = {}
            shared = Counter((offices[column], candidate[1]) for column, candidate in enumerate(candidates))
            for each in set(offices):
                for county, text, votes in conn.execute(
                    """select u.code, o.ballot_label, v.value::int from result_value v
                       join result_set rs on rs.k = v.result_set_k join source s on s.id = rs.source_id
                       join contest_option o on o.k = v.option_k join reporting_unit u on u.k = v.unit_k
                       join external_id x on x.entity_id = rs.contest_id and x.scheme = 'us.contest'
                       where x.value = %s and (s.title like '%%(ICPSR 1)' or s.title like '%%(ICPSR 13)'
                       or s.title = 'Gubernatorial county returns, 1865–2020')
                       and v.measure = 1""",
                    [f"{day}:MO:{each}"],
                ).fetchall():
                    words = [
                        word
                        for word in re.sub(r"\([^)]*\)|[^a-z ()]", "", (text or "").lower()).split()
                        if word not in {"jr", "sr", "ii", "iii"}
                    ] or [""]
                    surname = re.sub(r"[^a-z]", "", words[-1])
                    column = next(
                        (
                            column
                            for column, candidate in enumerate(candidates)
                            if offices[column] == each
                            and (
                                shared[(each, candidate[1])] == 1
                                or {"DEM": "democrat", "SLP": "socialist labor", "SOC": "socialist party"}.get(
                                    candidate[0], "-"
                                )
                                in re.sub(r"ic\b", "", (text or "").lower().replace("(socialist)", "(socialist party)"))
                            )
                            and difflib.SequenceMatcher(
                                None, surname, re.sub(r"[^a-z]", "", candidate[1].lower())
                            ).ratio()
                            >= 0.8
                        ),
                        None,
                    )
                    if column is not None:
                        diagnostic[(county, column)] = diagnostic.get((county, column), 0) + votes
            if office == "governor":
                for column in range(len(candidates)):
                    if diagnostic.get(("29189", column), 0) > diagnostic.get(("29510", column), 0) > 0:
                        diagnostic[("29189", column)] -= diagnostic[("29510", column)]
            totals: dict[int, int] = {}
            for each in set(offices):
                for text, votes in conn.execute(
                    """select o.ballot_label, v.value::int from result_value v
                       join result_set rs on rs.k = v.result_set_k join source s on s.id = rs.source_id
                       join contest_option o on o.k = v.option_k
                       join external_id x on x.entity_id = rs.contest_id and x.scheme = 'us.contest'
                       where x.value = %s and s.title like 'Statistics of the %%' and v.measure = 1""",
                    [f"{day}:MO:{each}"],
                ).fetchall():
                    words = [
                        word
                        for word in re.sub(r"\([^)]*\)|[^a-z ()]", "", (text or "").lower()).split()
                        if word not in {"jr", "sr", "ii", "iii"}
                    ] or [""]
                    surname = re.sub(r"[^a-z]", "", words[-1])
                    for column, candidate in enumerate(candidates):
                        if (
                            offices[column] == each
                            and difflib.SequenceMatcher(None, surname, candidate[1].lower()).ratio() >= 0.8
                        ):
                            totals[column] = votes
        informed = {column for _, column in diagnostic}
        expected = {
            county: [diagnostic.get((county, column), 0) for column in range(len(candidates))]
            for county in {county for county, _ in diagnostic}
        }
        expected["TOTAL"] = [
            totals.get(column, sum(values[column] for values in expected.values())) for column in range(len(candidates))
        ]
        if "29510" not in expected:
            expected.pop("29189", None)
        listed = list(base)
        for first, second in MISORDERED.get((day, office), []):
            one, other = listed.index(codes[first]), listed.index(codes[second])
            listed[one], listed[other] = listed[other], listed[one]
        swapped = list(listed)
        francois, genevieve = swapped.index(codes["stfrancois"]), swapped.index(codes["stegenevieve"])
        swapped[francois], swapped[genevieve] = swapped[genevieve], swapped[francois]
        readings: dict[str, tuple[int, list[list[int]]]] = {}
        missing: list[str] = []
        fallback: dict[str, tuple[int, list[list[int]]]] = {}
        for order in (
            arrangement
            for each in (listed, swapped)
            for arrangement in (each, each[: each.index(jackson) + 1] + ["KC"] + each[each.index(jackson) + 1 :])
        ):
            readings = {}
            for page, top, bottom, left, right in segments:
                for county, (support, cells) in segment(page, (top, bottom, left, right), expected, order).items():
                    if county not in readings or (support, sum(map(bool, cells))) > (
                        readings[county][0],
                        sum(map(bool, readings[county][1])),
                    ):
                        readings[county] = (support, cells)
            missing = sorted((set(base) - set(readings)) - ({jackson} if "KC" in readings else set()))
            if not missing and (jackson in readings and readings[jackson][0] or "KC" in readings):
                break
            if not missing and not fallback:
                fallback = readings
        else:
            if fallback:
                readings, missing = fallback, []
        if missing:
            raise ValueError(f"{day} {office}: missing {missing}")
        order = sorted(set(readings) - {"TOTAL"})
        chosen: dict[str, list[int]] = {}
        for county, (_, cells) in readings.items():
            chosen[county] = []
            for column, cell in enumerate(cells):
                hint = (
                    diagnostic.get((county, column), 0)
                    if county in expected
                    and county != "TOTAL"
                    and column in informed
                    and not (county == jackson and "KC" in readings)
                    else None
                )
                majority, support = next(
                    ((value, count) for value, count in Counter(cell).most_common() if count >= 2), (0, 0)
                )
                chosen[county].append(hint if hint in cell or (hint == 0 and support <= 3) else majority)
        for (when, what, county, column), votes in FIXES.items():
            if (when, what) == (day, office):
                chosen[county][column] = votes
        for column, (_, label, *_) in enumerate(candidates):
            total = sum(chosen[county][column] for county in order)
            if total == chosen["TOTAL"][column]:
                continue
            gap = chosen["TOTAL"][column] - total
            others = sorted(
                {
                    (county, value)
                    for county in order
                    for value in [
                        *readings[county][1][column],
                        0,
                        *([expected[county][column]] if county in expected else []),
                    ]
                    if value != chosen[county][column] and (day, office, county, column) not in FIXES
                }
            )
            swaps = [
                picked
                for size in (1, 2)
                for picked in itertools.combinations(others, size)
                if len({county for county, _ in picked}) == size
                and sum(value - chosen[county][column] for county, value in picked) == gap
            ]
            if (day, office, column) in CHECKED or (
                column in informed
                and abs(gap) <= max(10, total // 100)
                and any(county in expected for county in order)
                and all(
                    chosen[county][column] == expected[county][column]
                    for county in order
                    if county in expected and not (county == jackson and "KC" in readings)
                )
            ):
                caveats.append(
                    {
                        "day": day,
                        "office": offices[column],
                        "note": f"The Manual's printed total for {label} is {chosen['TOTAL'][column]:,}; "
                        f"its county figures sum to {total:,}.",
                    }
                )
                continue
            swaps = [one for one in swaps if len(one) == len(swaps[0])]
            if len(swaps) > 1:
                swaps = [
                    one
                    for one in swaps
                    if all(county in expected and expected[county][column] == value for county, value in one)
                ] or swaps
            if len(swaps) != 1:
                raise ValueError(
                    f"{day} {office} {label}: off by {gap}, "
                    f"other readings {[(county, value) for county, value in others if value]}, "
                    f"unlike ICPSR {[(county, chosen[county][column], expected[county][column]) for county in order if county in expected and chosen[county][column] != expected[county][column]]}"
                )
            for county, value in swaps[0]:
                chosen[county][column] = value
        for county in order:
            for (party, label, *_), each, votes in zip(candidates, offices, chosen[county], strict=True):
                rows.append(
                    {
                        "day": day,
                        "office": each,
                        "county": jackson if county == "KC" else county,
                        "label": label,
                        "party": party,
                        "votes": votes,
                    }
                )
    (
        pl.DataFrame(rows)
        .group_by("day", "office", "county", "label", "party", maintain_order=True)
        .agg(pl.col("votes").sum())
        .write_parquet(PARSED / "lines.parquet")
    )
    pl.DataFrame(caveats, schema={"day": pl.String, "office": pl.String, "note": pl.String}).write_parquet(
        PARSED / "caveats.parquet"
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    notes: dict[tuple[str, str], list[str]] = {}
    for day, office, note in pl.read_parquet(PARSED / "caveats.parquet").iter_rows():
        notes.setdefault((day, office), []).append(note)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Missouri's president, governor, and U.S. Senate elections by county from the Official Manual",
            polity="US",
        )
        book = cs.source(PUBLISHER, BOOK)
        cs.add(
            "source",
            id=book,
            kind="official_body",
            publisher=PUBLISHER,
            title=BOOK,
            url="https://mdh.contentdm.oclc.org/digital/collection/bluebook",
            license="Public record",
        )
        for _, _, segments, _ in TABLES:
            for page in {segment[0] for segment in segments}:
                cs.artifact(raw.cached(SOURCE, IMAGE.format(page)), book)
        counties: dict[str, UUID] = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.county' and code like '29%%'"
            ).fetchall()
        )
        if len(counties) != 115:
            raise ValueError("Missouri needs 115 counties")
        parties = {code: cs.key("us.party", party) if party else None for code, party in PARTIES.items()}
        if cs.minted & set(parties.values()) - {None}:
            raise ValueError("the parties must exist")

        def surname(text: str) -> str:
            words = [
                word
                for word in re.sub(r"[^a-z ]", "", re.sub(r"\([^)]*\)", " ", text.lower())).split()
                if word not in {"jr", "sr", "ii", "iii"}
            ]
            return words[-1] if words else ""

        born: set[UUID] = set()
        contests: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':MO:'")
        )
        columns = (
            "id",
            "election_id",
            "contest_id",
            "stage",
            "kind",
            "status",
            "source_id",
            "as_of",
            "completeness",
            "supersedes_id",
        )
        for (day, office), lines in frame.group_by("day", "office", maintain_order=True):
            key = f"{day}:MO:{office}"
            contest = contests[key]
            result_set = cs.key(f"{SOURCE}.result_set", key)
            ((election_id,),) = conn.execute("select election_id from contest where id = %s", [contest]).fetchall()
            options = conn.execute(
                """select id, ballot_label, party_id from contest_option
                   where contest_id = %s and parent_option_id is null and kind in ('candidate', 'ticket')""",
                [contest],
            ).fetchall()
            for row in conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]
            ).fetchall():
                if row[0] != result_set:
                    cs.add("result_set", **dict(zip(columns, row, strict=True)), is_default=False)
            outcomes = conn.execute(
                """select o.option_id, o.kind, o.seats from outcome o
                   join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s""",
                [contest],
            ).fetchall()
            on_file = dict(
                conn.execute(
                    """select o.id, sum(v.value)::bigint from result_value v
                       join result_set rs on rs.k = v.result_set_k and rs.is_default
                       join contest_option o on o.k = v.option_k
                       where rs.contest_id = %s and v.measure = 1 group by 1""",
                    [contest],
                ).fetchall()
            )
            picked: dict[tuple[str, str], UUID] = {}
            totals: dict[UUID, int] = {}
            for label, party, votes in (
                lines.group_by("label", "party", maintain_order=True).agg(pl.col("votes").sum()).iter_rows()
            ):
                wanted = surname(label)
                code = party if office == "president" else f"MO:{party}" if f"MO:{party}" in parties else party
                matches = [option for option, text, _ in options if surname(text or "") == wanted] or [
                    option
                    for option, text, _ in options
                    if surname(text or "")
                    in difflib.get_close_matches(wanted, [surname(other or "") for _, other, _ in options], 1, 0.8)
                ]
                if len(matches) > 1:
                    matches = [option for option, _, owner in options if option in matches and owner == parties[code]]
                if len(matches) > 1:
                    raise ValueError(f"{key}: {len(matches)} options for {label}")
                if matches:
                    option = matches[0]
                else:
                    full = NAMES.get((day, label, party))
                    if full is None:
                        raise ValueError(f"{key}: no option or name for {label} ({party})")
                    known = [
                        (person, text)
                        for person, text in conn.execute(
                            """select distinct cd.person_id, n.text from candidacy cd
                               join contest_option o on o.id = cd.option_id
                               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
                               join name n on n.entity_id = cd.person_id and n.is_preferred
                               where x.value ~ %s and case when x.value ~ '^[0-9]{4}' then left(x.value, 4)::int end between %s and %s""",
                            [":president$" if office == "president" else ":MO:", int(day[:4]) - 12, int(day[:4]) + 12],
                        ).fetchall()
                        if " " in full and surname(text) == wanted and text[:1].lower() == full[:1].lower()
                    ]
                    known = [
                        person
                        for person, text in known
                        if len({person for person, _ in known}) == 1
                        or text == full
                        and len({person for person, other in known if other == full}) == 1
                    ]
                    if len(set(known)) > 1:
                        raise ValueError(f"{key}: {len(set(known))} people for {full}")
                    if not known and " " in full and office == "president":
                        known = [
                            person
                            for (person,) in conn.execute(
                                "select distinct entity_id from name where text = %s and is_preferred", [full]
                            ).fetchall()
                        ]
                    if len(set(known)) > 1:
                        raise ValueError(f"{key}: {len(set(known))} people named {full}")
                    person = (
                        known[0] if known else cs.key(f"{SOURCE}.person", f"MO:{re.sub(r'[^a-z]+', '-', full.lower())}")
                    )
                    if person in cs.minted and person not in born:
                        born.add(person)
                        cs.add("person", id=person)
                        cs.add("name", entity_id=person, text=full, lang="en", kind="official", is_preferred=True)
                    option = cs.key("us.option", f"{key}:{person}:{party}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="ticket" if office == "president" else "candidate",
                        party_id=parties[code],
                        ballot_label=full,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{person}:{party}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=full,
                    )
                if option in on_file and abs(on_file[option] - votes) > max(10, on_file[option] // 200):
                    print(f"{key}: {label} has {votes}, {on_file[option]} on file")
                picked[(label, party)] = option
                totals[option] = totals.get(option, 0) + votes
            cs.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=book,
                is_default=True,
                completeness=1,
            )
            if (day, office) in notes:
                prior = conn.execute(
                    "select id from note where entity_id = %s and kind = 'caveat'", [result_set]
                ).fetchone()
                cs.add(
                    "note",
                    **({"id": prior[0]} if prior else {}),
                    entity_id=result_set,
                    kind="caveat",
                    body_md=" ".join(notes[(day, office)]),
                )
            if conn.execute("select from result_set where id = %s", [result_set]).fetchone() is None:
                for county, label, party, votes in lines.select("county", "label", "party", "votes").iter_rows():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=counties[county],
                        option_id=picked[(label, party)],
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
            top = max(totals, key=totals.__getitem__)
            outcomes = [
                (option, kind, seats)
                for option, kind, seats in outcomes
                if kind not in {"elected", "won"} or option == top
            ]
            for option, kind, seats in outcomes:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            if not any(kind in {"elected", "won"} for _, kind, _ in outcomes):
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=top,
                    kind="elected",
                    seats=1,
                )
        return cs.write()
