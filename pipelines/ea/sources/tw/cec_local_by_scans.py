"""The four county and city mayoral by-elections that the Central Election Commission publishes only as scanned
tables: Taoyuan County 1997, Hualien County 2003, Taitung County 2006, and Keelung City 2007. Each has a table of
votes by township or district (the Commission's "polling station table", which holds no polling stations), a table of
the candidates with their sex, birth date, party, and total, and a one-row summary. The figures here were read from
the scans and are checked against the printed totals by `check`; `cec_local_by` finds the box of each figure on the
page."""

import itertools
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pymupdf
from PIL import Image
from scipy import ndimage


class Cand(NamedTuple):
    no: int
    name: str
    sex: str  # 1 male, 2 female
    birth: tuple[int, int, int]  # the Republic of China year, month, and day
    party: str  # empty for no party
    total: int
    elected: bool


class Line(NamedTuple):
    name: str
    votes: tuple[int, ...]
    valid: int
    invalid: int
    cast: int
    unused_issued: int  # ballots issued and not cast (D)
    unused: int  # ballots left over (F)
    registered: int


class Scan(NamedTuple):
    cands: tuple[Cand, ...]
    total: Line
    lines: tuple[Line, ...]


SCANS: dict[str, Scan] = {
    "1997-03-15": Scan(
        (
            Cand(1, "賴來焜", "1", (45, 1, 25), "新黨", 45526, False),
            Cand(2, "方力偕", "1", (35, 1, 4), "中國國民黨", 216325, False),
            Cand(3, "呂秀蓮", "2", (33, 6, 7), "民主進步黨", 324074, True),
        ),
        Line("合計", (45526, 216325, 324074), 585925, 8604, 594529, 2, 417049, 1011580),
        (
            Line("桃園市", (8041, 29538, 61853), 99432, 1383, 100815, 1, 78323, 179139),
            Line("龜山鄉", (3148, 14842, 18070), 36060, 539, 36599, 0, 33168, 69767),
            Line("八德市", (5123, 24557, 25864), 55544, 935, 56479, 0, 41230, 97709),
            Line("蘆竹鄉", (1735, 7866, 17695), 27296, 406, 27702, 0, 19734, 47436),
            Line("大園鄉", (1223, 9064, 15870), 26157, 380, 26537, 0, 19207, 45744),
            Line("大溪鎮", (2115, 12745, 18142), 33002, 482, 33484, 0, 20790, 54274),
            Line("復興鄉", (111, 2158, 1397), 3666, 36, 3702, 0, 4012, 7714),
            Line("中壢市", (11465, 49021, 58108), 118594, 1659, 120253, 0, 79269, 199522),
            Line("平鎮市", (5447, 25621, 34387), 65455, 1082, 66537, 1, 43373, 109911),
            Line("楊梅鎮", (2593, 14874, 25948), 43415, 712, 44127, 0, 28983, 73110),
            Line("龍潭鄉", (3447, 14260, 17413), 35120, 513, 35633, 0, 24335, 59968),
            Line("新屋鄉", (538, 6122, 14529), 21189, 214, 21403, 0, 12094, 33497),
            Line("觀音鄉", (540, 5657, 14798), 20995, 263, 21258, 0, 12531, 33789),
        ),
    ),
    "2003-08-02": Scan(
        (
            Cand(1, "吳國棟", "1", (31, 1, 3), "", 26205, False),
            Cand(2, "齊淑英", "2", (39, 10, 8), "綠黨", 749, False),
            Cand(3, "游盈隆", "1", (45, 11, 23), "民主進步黨", 41508, False),
            Cand(4, "謝深山", "1", (28, 2, 3), "中國國民黨", 73710, True),
            Cand(5, "許家琛", "1", (24, 7, 1), "", 304, False),
        ),
        Line("總計", (26205, 749, 41508, 73710, 304), 142476, 1035, 143511, 0, 113174, 256685),
        (
            Line("花蓮市", (7414, 302, 14065, 23007, 84), 44872, 257, 45129, 0, 31302, 76431),
            Line("新城鄉", (1297, 53, 2428, 4889, 19), 8686, 84, 8770, 0, 6816, 15586),
            Line("秀林鄉", (1202, 11, 1220, 3565, 9), 6007, 66, 6073, 0, 4134, 10207),
            Line("吉安鄉", (6994, 178, 10162, 15437, 64), 32835, 233, 33068, 0, 24231, 57299),
            Line("壽豐鄉", (1493, 40, 2804, 4744, 18), 9099, 53, 9152, 0, 6382, 15534),
            Line("鳳林鎮", (1646, 27, 1652, 2664, 14), 6003, 47, 6050, 0, 4585, 10635),
            Line("光復鄉", (897, 27, 1678, 3554, 16), 6172, 32, 6204, 0, 5877, 12081),
            Line("豐濱鄉", (384, 6, 493, 1201, 6), 2090, 16, 2106, 0, 2591, 4697),
            Line("萬榮鄉", (504, 3, 436, 1556, 6), 2505, 21, 2526, 0, 2386, 4912),
            Line("瑞穗鄉", (841, 15, 1750, 2854, 15), 5475, 39, 5514, 0, 5411, 10925),
            Line("玉里鎮", (1773, 61, 3335, 6233, 36), 11438, 143, 11581, 0, 11739, 23320),
            Line("富里鄉", (1623, 20, 1214, 2092, 13), 4962, 31, 4993, 0, 5290, 10283),
            Line("卓溪鄉", (137, 6, 271, 1914, 4), 2332, 13, 2345, 0, 2430, 4775),
        ),
    ),
    "2006-04-01": Scan(
        (
            Cand(1, "羅法尼耀學海", "1", (54, 10, 6), "", 976, False),
            Cand(2, "賴坤成", "1", (53, 6, 3), "", 4793, False),
            Cand(3, "鄺麗貞", "2", (52, 3, 12), "中國國民黨", 42578, True),
            Cand(4, "劉櫂豪", "1", (56, 2, 2), "", 19228, False),
        ),
        Line("合計", (976, 4793, 42578, 19228), 67575, 535, 68110, 0, 110316, 178426),
        (
            Line("臺東市", (209, 3103, 18135, 10335), 31782, 238, 32020, 0, 47656, 79676),
            Line("成功鎮", (242, 200, 3349, 925), 4716, 42, 4758, 0, 8488, 13246),
            Line("關山鎮", (16, 208, 1856, 734), 2814, 24, 2838, 0, 4914, 7752),
            Line("卑南鄉", (40, 316, 3464, 1827), 5647, 49, 5696, 0, 9182, 14878),
            Line("鹿野鄉", (27, 150, 1498, 913), 2588, 18, 2606, 0, 4684, 7290),
            Line("池上鄉", (24, 162, 1653, 809), 2648, 18, 2666, 0, 5047, 7713),
            Line("東河鄉", (71, 129, 2046, 733), 2979, 35, 3014, 0, 5328, 8342),
            Line("長濱鄉", (61, 146, 1843, 645), 2695, 26, 2721, 0, 4758, 7479),
            Line("太麻里鄉", (58, 157, 2726, 890), 3831, 22, 3853, 0, 5864, 9717),
            Line("大武鄉", (28, 41, 1409, 415), 1893, 11, 1904, 0, 3905, 5809),
            Line("綠島鄉", (8, 18, 603, 214), 843, 3, 846, 0, 1516, 2362),
            Line("海端鄉", (24, 12, 931, 123), 1090, 13, 1103, 0, 2114, 3217),
            Line("延平鄉", (13, 26, 811, 172), 1022, 9, 1031, 0, 1745, 2776),
            Line("金峰鄉", (26, 46, 914, 196), 1182, 5, 1187, 0, 1269, 2456),
            Line("達仁鄉", (64, 43, 916, 133), 1156, 1, 1157, 0, 1887, 3044),
            Line("蘭嶼鄉", (65, 36, 424, 164), 689, 21, 710, 0, 1959, 2669),
        ),
    ),
    "2007-05-12": Scan(
        (
            Cand(1, "張通賢", "1", (48, 12, 1), "", 1105, False),
            Cand(2, "劉文雄", "1", (43, 9, 18), "", 33956, False),
            Cand(3, "張通榮", "1", (38, 2, 4), "中國國民黨", 56115, True),
            Cand(4, "張潮鐘", "1", (37, 4, 10), "", 206, False),
            Cand(5, "施世明", "1", (41, 11, 2), "民主進步黨", 26908, False),
        ),
        Line("總計", (1105, 33956, 56115, 206, 26908), 118290, 1012, 119302, 0, 174882, 294184),
        (
            Line("中正區", (159, 5195, 11371, 24, 2948), 19697, 175, 19872, 0, 24684, 44556),
            Line("信義區", (114, 5158, 8139, 27, 2870), 16308, 125, 16433, 0, 22767, 39200),
            Line("仁愛區", (147, 4620, 7387, 23, 4320), 16497, 136, 16633, 0, 21465, 38098),
            Line("中山區", (142, 4374, 6954, 35, 5508), 17013, 171, 17184, 0, 23157, 40341),
            Line("安樂區", (210, 6995, 10584, 31, 5662), 23482, 187, 23669, 0, 39021, 62690),
            Line("暖暖區", (158, 3913, 4736, 29, 1932), 10768, 96, 10864, 0, 17301, 28165),
            Line("七堵區", (175, 3701, 6944, 37, 3668), 14525, 122, 14647, 0, 26487, 41134),
        ),
    ),
}


def check() -> list[str]:
    """The ways a scan's figures disagree with its own sums, row by row and column by column."""
    found: list[str] = []
    for day, scan in SCANS.items():
        rows = (*scan.lines, scan.total)
        for row in rows:
            if sum(row.votes) != row.valid:
                found.append(f"{day} {row.name}: candidate votes add up to {sum(row.votes)}, valid is {row.valid}")
            if row.valid + row.invalid != row.cast:
                found.append(f"{day} {row.name}: valid and invalid ballots do not add up to the ballots cast")
            if row.cast + row.unused_issued + row.unused != row.registered:
                found.append(f"{day} {row.name}: ballots cast, issued, and unused do not add up to the voters")
        for index in range(len(scan.cands)):
            added = sum(row.votes[index] for row in scan.lines)
            if added != scan.total.votes[index] or scan.total.votes[index] != scan.cands[index].total:
                found.append(f"{day} candidate {index + 1}: rows add up to {added}")
        for field in ("valid", "invalid", "cast", "unused_issued", "unused", "registered"):
            added = sum(getattr(row, field) for row in scan.lines)
            if added != getattr(scan.total, field):
                found.append(f"{day} {field}: rows add up to {added}, total is {getattr(scan.total, field)}")
        top = max(cand.total for cand in scan.cands)
        if [cand.no for cand in scan.cands if cand.elected] != [cand.no for cand in scan.cands if cand.total == top]:
            found.append(f"{day}: the elected candidate is not the one with the most votes")
    return found


# The x positions, in pixels of the page rendered at 200 dpi, of the ruling lines between a table's columns: the name
# column, one column per candidate, the valid, invalid, and cast ballots, the ballots issued and not cast, the
# ballots issued, the ballots left over, the voters, and the turnout. The scans' lines are broken by noise, so the
# positions were read from the page.
COLUMNS: dict[str, list[int]] = {
    "1997-03-15": [432, 576, 725, 871, 1020, 1168, 1314, 1461, 1607, 1754, 1902, 2049, 2196],
    "2003-08-02": [262, 602, 722, 842, 962, 1081, 1200, 1319, 1438, 1557, 1678, 1798, 1918, 2038, 2155],
    "2006-04-01": [71, 236, 422, 606, 788, 970, 1156, 1286, 1464, 1588, 1761, 1949, 2142, 2298],
    "2007-05-12": [312, 438, 577, 717, 857, 996, 1133, 1262, 1380, 1510, 1630, 1762, 1945, 2125, 2239],
}
DPI = 200
PAGE_WIDTH = 2339  # the scans are A4 landscape, in pixels at DPI
PAGE_HEIGHT = 1654


def render(path: Path) -> Image.Image:
    pixmap = pymupdf.open(path)[0].get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY)
    return Image.frombytes("L", (pixmap.width, pixmap.height), pixmap.samples)


def skew(ink: np.ndarray) -> float:
    """The angle in degrees, within two, that turns the page's text rows level."""
    small = Image.fromarray(ink[::2, ::2].astype("uint8") * 255)
    best, angle = -1.0, 0.0
    for tenths in range(-20, 21):
        level = np.asarray(small.rotate(tenths / 10, resample=Image.Resampling.BILINEAR)) > 100
        spread = float(level.sum(axis=1).astype(float).var())
        if spread > best:
            best, angle = spread, tenths / 10
    return angle


def bands(on: np.ndarray, gap: int, least: int) -> list[tuple[int, int]]:
    found: list[tuple[int, int]] = []
    start = last = -1
    for index in np.nonzero(on)[0]:
        if start < 0:
            start = last = int(index)
        elif index - last > gap:
            found.append((start, last))
            start = last = int(index)
        else:
            last = int(index)
    if start >= 0:
        found.append((start, last))
    return [band for band in found if band[1] - band[0] >= least]


def unrotate(angle: float, size: tuple[int, int], box: tuple[float, float, float, float]) -> tuple[float, ...]:
    """The box in the page as scanned, given a box in the page turned level by `angle`."""
    width, height = size
    theta = np.deg2rad(angle)
    xs: list[float] = []
    ys: list[float] = []
    for x, y in ((box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])):
        dx, dy = x - width / 2, y - height / 2
        xs.append(dx * np.cos(theta) - dy * np.sin(theta) + width / 2)
        ys.append(dx * np.sin(theta) + dy * np.cos(theta) + height / 2)
    return min(xs), min(ys), max(xs), max(ys)


def locate(path: Path, day: str) -> dict[tuple[int, int], tuple[float, float, float, float]]:
    """The box, as fractions of the page, of every cell of a scan's table: (row, column) -> (x0, y0, x1, y1). Row 0 is
    the total row, and the townships follow in order. Column 0 is the first candidate, then the other candidates, the
    valid, invalid, and cast ballots, the ballots issued and not cast, the ballots issued, the ballots left over, and
    the voters."""
    scan = SCANS[day]
    page = render(path)
    ink = np.asarray(page) < 140
    angle = skew(ink)
    level = np.asarray(page.rotate(angle, resample=Image.Resampling.BILINEAR, fillcolor=255)) < 140
    rules = ndimage.binary_opening(level, structure=np.ones((1, 60))) | ndimage.binary_opening(
        level, structure=np.ones((60, 1))
    )
    text = level & ~ndimage.binary_dilation(rules, structure=np.ones((3, 3)))
    xs = COLUMNS[day]
    count = len(scan.lines) + 1
    # the voters column holds a number in every row and nothing else below the header
    voters = 1 + len(scan.cands) + 6
    found = bands(text[:, xs[voters] + 6 : xs[voters + 1] - 6].sum(axis=1) > 0, 4, 14)
    # keep the longest run of rows at an even pitch, which drops a page number or a speck below the table
    pitch = float(np.median([b[0] - a[0] for a, b in itertools.pairwise(found)]))
    runs: list[list[tuple[int, int]]] = [[found[0]]]
    for previous, band in itertools.pairwise(found):
        if abs((band[0] - previous[0]) - pitch) <= pitch * 0.25:
            runs[-1].append(band)
        else:
            runs.append([band])
    found = max(runs, key=len)[-count:]
    if len(found) == count - 1:
        # the total row sits on a ruling line and its digits are cut away with it
        shift = round(pitch)
        found = [(found[0][0] - shift, found[0][1] - shift), *found]
    if len(found) != count:
        raise ValueError(f"{day}: found {len(found)} rows in the scan, expected {count}")
    size = (page.width, page.height)
    out: dict[tuple[int, int], tuple[float, float, float, float]] = {}
    for row, (top, bottom) in enumerate(found):
        for column in range(len(scan.cands) + 7):
            left = xs[1 + column] + 3
            right = xs[2 + column] - 3
            x0, y0, x1, y1 = unrotate(angle, size, (left, top - 4, right, bottom + 4))
            out[row, column] = (x0 / size[0], y0 / size[1], x1 / size[0], y1 / size[1])
    return out
