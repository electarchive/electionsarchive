"""Viet Nam's provincial People's Council elections of 15 March 2026, from the provincial election committees.

Each of the 34 provincial election committees announced its council's winners by resolution. Most portals print
the winners without their votes; Ha Tinh's committee printed every candidate's votes and share of the valid ballots
for each of its twelve constituencies in Resolution 68/NQ-UBBC of 19 March 2026. That resolution reaches the web
only as a 19-page scan, attached to the provincial newspaper's report, so its figures are transcribed here from the
page images, with each vote count located on its page. The text layer the scanner added is too garbled to read the
names from, but its word boxes give each row's position, and its digits are compared with the transcription.

The committee's report 138/BC-UBBC to the new council, a born-digital PDF, gives the province's electorate and the
number who voted, the communes in each constituency, and the winners' titles (Ông or Bà).
"""

import re
import subprocess
import unicodedata
from datetime import date
from pathlib import Path
from typing import NamedTuple
from uuid import uuid5

from PIL import Image

from ea import package, raw
from ea.package import Package
from ea.store import DATA

SOURCE = "vn.hdnd"
VIETNAM = str(uuid5(package.NAMESPACE, "polity\x1fVN"))
DAY = date(2026, 3, 15)
PAGES = DATA / "pages" / SOURCE

HT_NEWS = "https://baohatinh.vn/bau-cu/ha-tinh-cong-bo-danh-sach-58-nguoi-trung-cu-dai-bieu-hdnd-tinh-post307762.html"
HT_SCAN = (
    "https://cdn.baohatinh.vn/files/KvAJejLA8GVFgs_o3pVAghy1-1G1zKb2tlzGV8Pb5CDyeO30ZxAKd-m1P3i82_n2/nq-68-ubbct.pdf"
)
HT_REPORT = (
    "https://dbndhatinh.vn/Media/FileManager/03.26.-BC-ket-qua-bau-cu-va-xac-nhan-tu-cach-dai-bieu-HDND-tinh-ok"
    "(27.03.2026_13h53p33)_signed%20(Q).pdf"
)
HT_ELECTORATE, HT_VOTERS = 968_380, 968_275

VOTES, REGISTERED, CAST = 1, 2, 3
DIGITIZED = 8
SCHEME = "vn.hdnd_tinh"
PROVINCE_SCHEME = "vn.province"


class Row(NamedTuple):
    district: int
    position: int
    name: str
    votes: int
    share: str  # percent of valid ballots as printed, with a decimal comma


# Resolution 68/NQ-UBBC, part I: every candidate in ballot order, by constituency, as printed on scan pages 2 to 5.
# A second name in parentheses is printed under the first on the ballot list (a former or religious name).
HT_ROWS = """
1|Trần Việt Hà|61470|94,17
1|Nguyễn Thị Thanh Hải|4759|7,29
1|Phạm Thị Vân Hạnh|4822|7,39
1|Đinh Thị Hiền|59632|91,35
1|Nguyễn Quốc Hùng|62156|95,22
1|Trương Huy Nam|4136|6,34
1|Nguyễn Hoài Sơn|62737|96,11
2|Hoàng Thị Duyên|3110|4,77
2|Phan Thiên Định|63424|97,27
2|Nguyễn Thị Mỹ Hạnh|3956|6,07
2|Nguyễn Ngọc Hoạch|63332|97,13
2|Lê Ngọc Huấn|62697|96,16
2|Nguyễn Thị Nhuần|62629|96,05
2|Trần Thị Quyên|3256|4,99
2|Trần Văn Viết|62431|95,75
3|Nguyễn Bá Đức|82338|95,35
3|Trương Thanh Huyền|83806|97,05
3|Nguyễn Hồng Lĩnh|84912|98,33
3|Lê Ngọc Mỹ|4219|4,89
3|Nguyễn Thị Thúy Nga|81749|94,67
3|Nguyễn Thị Nguyệt|81981|94,94
3|Trần Hữu Quý|5793|6,71
3|Nguyễn Thị Yến|4355|5,04
4|Nguyễn Thị Việt Hà|70782|98,29
4|Nguyễn Thị Hạnh|68288|94,83
4|Nguyễn Trọng Hiếu|71334|99,06
4|Trương Thị Liên|3056|4,24
4|Nguyễn Tùng Lĩnh|69522|96,54
4|Thiều Đình Long|2677|3,72
4|Trần Thị Quỳnh Nga (Trần Quỳnh Nga)|3216|4,47
4|Nguyễn Tú Tài|69152|96,03
5|Phan Trọng Bảy (Đại Đức Thích Chúc Giác)|99387|95,81
5|Nguyễn Đình Diệu|6714|6,47
5|Nguyễn Duy Lâm|102195|98,52
5|Trương Quang Long|97320|93,82
5|Trương Xuân Long|9248|8,92
5|Nguyễn Hiền Thương|6089|5,87
5|Trần Quang Tuấn|99982|96,38
5|Đinh Thị Hồng Vân|95536|92,10
6|Lê Văn Dũng|96961|96,59
6|Nguyễn Việt Hùng|97111|96,74
6|Lê Ngọc Hương|95774|95,41
6|Nguyễn Thị Bích Phương|6143|6,12
6|Trương Thị Xuân Phương|6024|6,00
6|Dương Tất Thắng|97130|96,76
6|Nguyễn Thị Mai Thủy|96457|96,09
6|Lê Văn Vinh|4916|4,90
7|Nguyễn Thanh Cảnh|6777|7,07
7|Võ Thị Hồng Minh|89364|93,23
7|Đào Thị Anh Nga|90429|94,34
7|Bùi Nhân Sâm|93187|97,22
7|Trần Mạnh Sơn|92948|96,97
7|Trần Nhật Tân|94344|98,42
7|Nguyễn Thị Nguyệt Thơ|4810|5,02
7|Lê Thị Huyền Trang|4942|5,16
8|Nguyễn Thành Đồng|61877|97,45
8|Bùi Việt Hùng|61697|97,16
8|Phan Trung Kiên|61021|96,10
8|Trần Văn Lượng|4890|7,70
8|Đoàn Thị Hằng Nga|4236|6,67
8|Nguyễn Thị Hà Tân|59190|93,21
8|Nguyễn Anh Thắng|59690|94,00
8|Ngô Thị Bích Thủy|3704|5,83
9|Lê Ngọc Hà|64540|96,97
9|Nguyễn Thị Cẩm Hà|3292|4,95
9|Nguyễn Thị Lệ Hà|64022|96,19
9|Hà Văn Hùng|64777|97,32
9|Hoàng Dương Liễu|2240|3,37
9|Nguyễn Thăng Long|64233|96,51
9|Ngô Thị Ánh Tuyết|2149|3,23
10|Trần Tú Anh|72950|98,45
10|Phan Thị Thúy Hằng|3263|4,40
10|Lê Thái Hòa|3285|4,43
10|Nguyễn Thế Hoàn|71706|96,77
10|Phạm Nghĩa|71618|96,65
10|Nguyễn Tiến Thắng|71828|96,93
10|Đặng Quỳnh Thơ|2741|3,70
10|Phan Hồng Yến|71308|96,23
11|Nguyễn Sỹ Chinh|91160|96,33
11|Nguyễn Ny Hương|90206|95,32
11|Nguyễn Thị Mỹ Lệ|5499|5,81
11|Hồ Huy Thành|93251|98,54
11|Trần Bình Thân|92224|97,45
11|Trần Quỳnh Trang|4050|4,28
11|Phạm Thị Tú|4112|4,35
11|Nguyễn Văn Tuấn (Nguyễn Ngọc Tuấn)|90999|96,16
12|Hà Văn Đàn|75750|98,26
12|Nguyễn Văn Đề|2614|3,39
12|Trần Báu Hà|76448|99,16
12|Lê Đình Hòa|2625|3,41
12|Nguyễn Thị Huế|3544|4,60
12|Nguyễn Huy Hùng|74056|96,06
12|Ngô Văn Huỳnh|74448|96,57
12|Nguyễn Trung Thành|74836|97,07
"""

# Part II of the resolution: the winners of each constituency, by votes, as printed on scan pages 5 and 6.
HT_WINNERS = {
    1: ["Nguyễn Hoài Sơn", "Nguyễn Quốc Hùng", "Trần Việt Hà", "Đinh Thị Hiền"],
    2: ["Phan Thiên Định", "Nguyễn Ngọc Hoạch", "Lê Ngọc Huấn", "Nguyễn Thị Nhuần", "Trần Văn Viết"],
    3: ["Nguyễn Hồng Lĩnh", "Trương Thanh Huyền", "Nguyễn Bá Đức", "Nguyễn Thị Nguyệt", "Nguyễn Thị Thúy Nga"],
    4: ["Nguyễn Trọng Hiếu", "Nguyễn Thị Việt Hà", "Nguyễn Tùng Lĩnh", "Nguyễn Tú Tài", "Nguyễn Thị Hạnh"],
    5: [
        "Nguyễn Duy Lâm",
        "Trần Quang Tuấn",
        "Phan Trọng Bảy (Đại Đức Thích Chúc Giác)",
        "Trương Quang Long",
        "Đinh Thị Hồng Vân",
    ],
    6: ["Dương Tất Thắng", "Nguyễn Việt Hùng", "Lê Văn Dũng", "Nguyễn Thị Mai Thủy", "Lê Ngọc Hương"],
    7: ["Trần Nhật Tân", "Bùi Nhân Sâm", "Trần Mạnh Sơn", "Đào Thị Anh Nga", "Võ Thị Hồng Minh"],
    8: ["Nguyễn Thành Đồng", "Bùi Việt Hùng", "Phan Trung Kiên", "Nguyễn Anh Thắng", "Nguyễn Thị Hà Tân"],
    9: ["Hà Văn Hùng", "Lê Ngọc Hà", "Nguyễn Thăng Long", "Nguyễn Thị Lệ Hà"],
    10: ["Trần Tú Anh", "Nguyễn Tiến Thắng", "Nguyễn Thế Hoàn", "Phạm Nghĩa", "Phan Hồng Yến"],
    11: ["Hồ Huy Thành", "Trần Bình Thân", "Nguyễn Sỹ Chinh", "Nguyễn Văn Tuấn (Nguyễn Ngọc Tuấn)", "Nguyễn Ny Hương"],
    12: ["Trần Báu Hà", "Hà Văn Đàn", "Nguyễn Trung Thành", "Ngô Văn Huỳnh", "Nguyễn Huy Hùng"],
}
# How many part I rows each scan page holds, in order: page 2 holds constituencies 1 to 3, and so on.
HT_PAGE_ROWS = {2: 23, 3: 26, 4: 33, 5: 12}
# Page 5 also holds the start of part II, the winners again, below this fraction of the page height.
HT_PAGE_STOP = {5: 0.44}
SEATS = 58
CANDIDATES = 94


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


HA_TINH = key("polity", "ha-tinh")
COUNCIL = key("body", "ha-tinh")
SYSTEM = key("electoral_system", "block")
SCAN_SOURCE = key("source", "ha-tinh", "68")
REPORT_SOURCE = key("source", "ha-tinh", "138")
NEWS_SOURCE = key("source", "ha-tinh", "news")
PROVINCE_UNIT = str(uuid5(package.NAMESPACE, "vn.baucuquochoi\x1funit\x1fha-tinh"))  # the province, from that pipeline

METHOD = (
    "Resolution 68/NQ-UBBC of Ha Tinh's provincial election committee, dated 19 March 2026, prints for each of the "
    "twelve constituencies every candidate in ballot order with their votes and their share of the constituency's "
    "valid ballots, to two decimals. The resolution survives online only as a scan, so the votes were transcribed "
    "from the page images and each is located on its page. Every share was checked against the votes: within each "
    "constituency, one number of valid ballots reproduces every printed share. The resolution does not print the "
    "constituency's electorate, voters, or valid ballots, so none is stored; each voter could mark as many "
    "candidates as there were seats. The winners and their order agree with part II of the resolution and with the "
    "committee's report 138/BC-UBBC to the new council."
)
TOTALS = (
    "The committee's report 138/BC-UBBC of 27 March 2026 gives the province's turnout as 968,275 of 968,380 voters "
    "on the rolls, 99.99 percent. The valid ballots implied by the resolution's printed shares sum to between "
    "{low:,} and {high:,} across the twelve constituencies, below the number who voted."
)
SYSTEM_NOTE = (
    "Each constituency elects up to five councillors. A voter may mark as many candidates as the constituency has "
    "seats, and the candidates with the most votes win, provided each has more than half of the valid ballots. A "
    "further ballot is held only when fewer than two thirds of a constituency's seats are filled."
)


def fetch() -> None:
    raw.fetch(SOURCE, HT_NEWS, suffix=".html")
    raw.fetch(SOURCE, HT_SCAN, suffix=".pdf")
    raw.fetch(SOURCE, HT_REPORT, suffix=".pdf")


def packages() -> list[str]:
    return [f"{SOURCE}/registry", f"{SOURCE}/ha-tinh-{DAY}"]


def slug(text: str) -> str:
    plain = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")


def clean(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def rows() -> list[Row]:
    found: list[Row] = []
    for line in HT_ROWS.strip().splitlines():
        district, name, votes, share = line.split("|")
        position = sum(1 for r in found if r.district == int(district)) + 1
        found.append(Row(int(district), position, clean(name), int(votes), share))
    if len(found) != CANDIDATES or sum(HT_PAGE_ROWS.values()) != CANDIDATES:
        raise ValueError(f"{len(found)} candidates transcribed, expected {CANDIDATES}")
    return found


def valid_range(district: list[Row]) -> tuple[int, int]:
    """The valid ballots that reproduce every printed share in a constituency, as an inclusive range."""
    low, high = 0, 10**9
    for row in district:
        hundredths = int(row.share.replace(",", ""))
        # a printed share h/100 percent means h - 0.5 <= votes * 10000 / valid <= h + 0.5
        low = max(low, -(-row.votes * 20000 // (2 * hundredths + 1)))
        high = min(high, row.votes * 20000 // (2 * hundredths - 1))
    if low > high:
        raise ValueError(f"no valid-ballot count reproduces the shares in constituency {district[0].district}")
    return low, high


def winners(found: list[Row]) -> dict[int, list[Row]]:
    by: dict[int, list[Row]] = {}
    for row in found:
        by.setdefault(row.district, []).append(row)
    out: dict[int, list[Row]] = {}
    for district, members in sorted(by.items()):
        seats = len(HT_WINNERS[district])
        ranked = sorted(members, key=lambda r: -r.votes)
        if ranked[seats - 1].votes == ranked[seats].votes:
            raise ValueError(f"tie for the last seat in constituency {district}")
        low, _ = valid_range(members)
        top = ranked[:seats]
        if [r.name for r in top] != HT_WINNERS[district]:
            raise ValueError(f"constituency {district}: winners by votes differ from part II of the resolution")
        if any(r.votes * 2 <= low for r in top):
            raise ValueError(f"constituency {district}: a winner without a majority of the valid ballots")
        out[district] = top
    if sum(len(v) for v in out.values()) != SEATS:
        raise ValueError("the winners do not fill the council's 58 seats")
    return out


def report() -> tuple[dict[int, str], dict[str, str]]:
    """Report 138/BC-UBBC: the communes of each constituency and each winner's title, checked against the scan."""
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, HT_REPORT).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    text = clean(text.replace("\f", " ")).split("Trên đây là")[0]
    for phrase in ("968.275/968.380", "99,99%", "58 đại biểu", "94 người", "68/NQ-UBBC"):
        if phrase not in text:
            raise ValueError(f"the report no longer prints {phrase!r}")
    if round(HT_VOTERS / HT_ELECTORATE * 100, 2) != 99.99:
        raise ValueError("printed turnout does not match the printed counts")
    communes: dict[int, str] = {}
    titles: dict[str, str] = {}
    parts = re.split(r"\b(\d{1,2})\. Đơn vị bầu cử số (\d{1,2}) \(Gồm các ([^:]*): ", text)
    for i in range(1, len(parts), 4):
        district = int(parts[i + 1])
        body = re.sub(r"\s\d\s(?=\()", " ", parts[i + 3])  # page numbers between entries
        head, _, rest = body.partition(")")
        communes[district] = clean(head)
        names = re.findall(r"\(\d\)\. (Ông|Bà) (.+?)(?=\s*\(\d\)\.|\s*\d{1,2}\. Đơn vị|\s+\d\s|\s*III?\.|$)", rest)
        expected = sorted(HT_WINNERS[district])
        got = sorted(clean(n) for _, n in names)
        if got != expected:
            raise ValueError(f"report lists {got} in constituency {district}, the resolution {expected}")
        for title, name in names:
            titles[clean(name)] = "male" if title == "Ông" else "female"
    if sorted(communes) != list(range(1, 13)):
        raise ValueError(f"report describes constituencies {sorted(communes)}")
    return communes, titles


def _digits(text: str) -> str:
    table = str.maketrans({"t": "1", "I": "1", "l": "1", "s": "5", "S": "5", "O": "0", "o": "0"})
    return re.sub(r"\D", "", text.translate(table))


class Box(NamedTuple):
    page: int
    x0: float
    y0: float
    x1: float
    y1: float


def locate(found: list[Row]) -> tuple[list[Box], int]:
    """Each row's vote cell, from the word boxes of the scanner's text layer, aligned to the transcription.

    Returns the boxes in transcription order and how many of the scanner's readings agree with the transcribed
    digits."""
    scan = raw.cached(SOURCE, HT_SCAN).path
    boxes: list[Box] = []
    agree = 0
    start = 0
    for page, count in HT_PAGE_ROWS.items():
        expected = found[start : start + count]
        start += count
        xml = subprocess.run(
            ["pdftotext", "-bbox", "-f", str(page), "-l", str(page), str(scan), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        size = re.search(r'<page width="([\d.]+)" height="([\d.]+)"', xml)
        if size is None:
            raise ValueError(f"page {page} has no size in the text layer")
        width, height = float(size[1]), float(size[2])
        words = [
            (float(a), float(b), float(c), float(d), w)
            for a, b, c, d, w in re.findall(
                r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>', xml
            )
        ]
        stop = HT_PAGE_STOP.get(page, 1.0) * height
        column = sorted(
            (w for w in words if 0.66 < (w[0] + w[2]) / 2 / width < 0.79 and w[1] < stop and _digits(w[4])),
            key=lambda w: (w[1], w[0]),
        )
        # The scanner sometimes splits one number into several words on the same line: join them.
        lines: list[list[tuple[float, float, float, float, str]]] = []
        for w in column:
            if lines and abs(w[1] - lines[-1][0][1]) < 5:
                lines[-1].append(w)
            else:
                lines.append([w])
        cells = [
            (
                min(w[0] for w in line),
                min(w[1] for w in line),
                max(w[2] for w in line),
                max(w[3] for w in line),
                "".join(w[4] for w in sorted(line)),
            )
            for line in lines
            if len(_digits("".join(w[4] for w in line))) >= 3
        ]
        # Align in order (a small edit-distance alignment): match a cell to a row when its digits nearly agree.
        n, m = len(expected), len(cells)
        score = [[0.0] * (m + 1) for _ in range(n + 1)]
        for i in range(n - 1, -1, -1):
            for j in range(m - 1, -1, -1):
                want, got = str(expected[i].votes), _digits(cells[j][4])
                same = sum(a == b for a, b in zip(want, got)) / max(len(want), len(got))
                score[i][j] = max(score[i + 1][j], score[i][j + 1], same + score[i + 1][j + 1] if same >= 0.5 else 0)
        match: dict[int, tuple[float, float, float, float, str]] = {}
        i = j = 0
        while i < n and j < m:
            want, got = str(expected[i].votes), _digits(cells[j][4])
            same = sum(a == b for a, b in zip(want, got)) / max(len(want), len(got))
            if same >= 0.5 and score[i][j] == same + score[i + 1][j + 1]:
                match[i] = cells[j]
                agree += got == want
                i, j = i + 1, j + 1
            elif score[i][j] == score[i + 1][j]:
                i += 1
            else:
                j += 1
        if len(match) < count * 0.8:
            raise ValueError(f"page {page}: only {len(match)} of {count} vote cells found in the text layer")
        left = min(c[0] for c in match.values()) - 6
        right = max(c[2] for c in match.values()) + 6
        known = sorted(match)
        for i in range(count):
            if i in match:
                top, bottom = match[i][1], match[i][3]
            else:  # interpolate the row from its matched neighbors
                before = max((k for k in known if k < i), default=None)
                after = min((k for k in known if k > i), default=None)
                a = before if before is not None else after
                b = after if after is not None else before
                assert a is not None and b is not None
                step = (match[b][1] - match[a][1]) / (b - a) if a != b else 21.0
                top = match[a][1] + step * (i - a)
                bottom = top + (match[a][3] - match[a][1])
            boxes.append(
                Box(
                    page,
                    round(max(left, 0) / width, 4),
                    round(max(top - 4, 0) / height, 4),
                    round(min(right, width) / width, 4),
                    round(min(bottom + 4, height) / height, 4),
                )
            )
    return boxes, agree


def page_image(page: int) -> Path:
    """The page's own scan, extracted from the PDF unchanged."""
    scan = raw.cached(SOURCE, HT_SCAN)
    target = PAGES / f"{scan.sha256}-{page}.jpg"
    if not target.exists():
        PAGES.mkdir(parents=True, exist_ok=True)
        stem = PAGES / f"tmp-{page}"
        subprocess.run(
            ["pdfimages", "-j", "-f", str(page), "-l", str(page), str(scan.path), str(stem)],
            check=True,
        )
        made = sorted(PAGES.glob(f"tmp-{page}-*.jpg"))
        if len(made) != 1:
            raise ValueError(f"page {page} of the scan holds {len(made)} images")
        made[0].rename(target)
    return target


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Ha Tinh province, its People's Council, the council's electoral system, and the provincial election "
        "committee's publications",
    )
    p.external.add(VIETNAM)
    p.add("polity", id=HA_TINH, kind="region", iso_code="VN-23")
    p.name(HA_TINH, "Ha Tinh", "en", "official")
    p.name(HA_TINH, "Hà Tĩnh", "vi", "native", False)
    p.add("slug", path="vn/ha-tinh", entity_id=HA_TINH, is_current=True)
    p.add("polity_parent", polity_id=HA_TINH, parent_id=VIETNAM)
    p.add("body", id=COUNCIL, polity_id=HA_TINH, kind="council")
    p.name(COUNCIL, "Ha Tinh Provincial People's Council", "en", "official")
    p.name(COUNCIL, "Hội đồng nhân dân tỉnh Hà Tĩnh", "vi", "native", False)
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=VIETNAM,
        kind="electoral_district",
        rank=2,
        noun="provincial council constituency",
    )
    p.add("electoral_system", id=SYSTEM, polity_id=VIETNAM, rules={"formula": "block"}, rules_version=1)
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    p.add(
        "source",
        id=SCAN_SOURCE,
        kind="official_body",
        publisher="Ủy ban bầu cử tỉnh Hà Tĩnh",
        title=(
            "Nghị quyết 68/NQ-UBBC về việc công bố kết quả bầu cử và danh sách những người trúng cử đại biểu "
            "Hội đồng nhân dân tỉnh Hà Tĩnh khóa XIX, nhiệm kỳ 2026-2031 (scan)"
        ),
        url=HT_SCAN,
        license="Official document; reuse terms are not stated",
        attribution="Ủy ban bầu cử tỉnh Hà Tĩnh",
    )
    p.add(
        "source",
        id=REPORT_SOURCE,
        kind="official_body",
        publisher="Ủy ban bầu cử tỉnh Hà Tĩnh",
        title=(
            "Báo cáo 138/BC-UBBC: kết quả bầu cử và kết quả xác nhận tư cách đại biểu Hội đồng nhân dân tỉnh "
            "khóa XIX, nhiệm kỳ 2026-2031"
        ),
        url=HT_REPORT,
        license="Official document; reuse terms are not stated",
        attribution="Ủy ban bầu cử tỉnh Hà Tĩnh",
    )
    p.add(
        "source",
        id=NEWS_SOURCE,
        kind="news_agency",
        publisher="Báo và Phát thanh, Truyền hình Hà Tĩnh",
        title="Hà Tĩnh công bố danh sách 58 người trúng cử đại biểu HĐND tỉnh",
        url=HT_NEWS,
        license="Not stated",
        attribution="baohatinh.vn",
    )
    p.artifact(raw.cached(SOURCE, HT_SCAN), SCAN_SOURCE)
    p.artifact(raw.cached(SOURCE, HT_REPORT), REPORT_SOURCE)
    p.artifact(raw.cached(SOURCE, HT_NEWS), NEWS_SOURCE)
    return p


def ha_tinh() -> Package:
    found = rows()
    elected = winners(found)
    communes, titles = report()
    boxes, agree = locate(found)
    if agree < CANDIDATES // 2:
        raise ValueError(f"only {agree} of the scanner's vote readings agree with the transcription")
    by: dict[int, list[Row]] = {}
    for row in found:
        by.setdefault(row.district, []).append(row)
    ranges = {d: valid_range(members) for d, members in by.items()}
    low = sum(r[0] for r in ranges.values())
    high = sum(r[1] for r in ranges.values())
    if high > HT_VOTERS:
        raise ValueError("the shares imply more valid ballots than voters")

    p = Package(
        f"{SOURCE}/ha-tinh-{DAY}",
        SOURCE,
        "Ha Tinh's People's Council election of 15 March 2026: every candidate's votes in each of the twelve "
        "constituencies, the 58 winners, and the province's electorate and turnout",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.update({VIETNAM, PROVINCE_SCHEME, PROVINCE_UNIT})
    for url in (HT_SCAN, HT_REPORT):
        p.uses(raw.cached(SOURCE, url))
    scan = raw.cached(SOURCE, HT_SCAN)
    stored = str(uuid5(package.NAMESPACE, f"source_artifact\x1f{SCAN_SOURCE}\x1f{scan.storage_key}"))
    pages: dict[int, str] = {}
    for page in HT_PAGE_ROWS:
        image = page_image(page)
        width, height = Image.open(image).size
        pages[page] = key("page", scan.sha256, page)
        p.add(
            "source_page",
            id=pages[page],
            artifact_id=stored,
            page_number=page,
            image_key=f"pages/{SOURCE}/{image.name}",
            width=width,
            height=height,
        )

    election = key("election", "ha-tinh", DAY)
    p.add("election", id=election, polity_id=HA_TINH, kind="general", date_from=DAY, date_to=DAY, status="held")
    p.add("slug", path=f"vn/ha-tinh/{DAY}-council", entity_id=election, is_current=True)
    p.name(election, "2026 Ha Tinh People's Council election", "en", "official")
    p.name(election, "Bầu cử đại biểu Hội đồng nhân dân tỉnh Hà Tĩnh khóa XIX", "vi", "native", False)

    def put(rs: str, at: str, option: str | None, measure: int, value: int, flags: int = 0) -> None:
        p.add(
            "result_value",
            result_set_id=rs,
            unit_id=at,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=flags,
            value=value,
        )

    province = key("result_set", "ha-tinh", "province")
    p.add(
        "result_set",
        id=province,
        election_id=election,
        contest_id=None,
        stage="official",
        kind="counted",
        source_id=REPORT_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=key("note", province),
        entity_id=province,
        kind="methodology",
        body_md=TOTALS.format(low=low, high=high),
        lang="en",
    )
    put(province, PROVINCE_UNIT, None, REGISTERED, HT_ELECTORATE)
    put(province, PROVINCE_UNIT, None, CAST, HT_VOTERS)

    index = 0
    for district, members in sorted(by.items()):
        contest = key("contest", "ha-tinh", district)
        seats = len(HT_WINNERS[district])
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=COUNCIL,
            magnitude=seats,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"2026 Ha Tinh People's Council election, constituency {district}", "en", "official")
        p.name(contest, f"Bầu cử đại biểu HĐND tỉnh Hà Tĩnh, đơn vị bầu cử số {district}", "vi", "native", False)
        p.add("slug", path=f"vn/ha-tinh/{DAY}-council/constituency-{district}", entity_id=contest, is_current=True)
        unit = key("unit", "ha-tinh", district)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=VIETNAM,
            scheme_code=SCHEME,
            code=f"{DAY}:ha-tinh:{district}",
            area_relation="unlocated",
            area_id=None,
            kind="standard",
        )
        p.name(unit, f"Hà Tĩnh, đơn vị bầu cử số {district}", "vi", "official")
        p.add(
            "note",
            id=key("note", unit),
            entity_id=unit,
            kind="methodology",
            body_md=f"The constituency takes in these communes and wards (per report 138/BC-UBBC): {communes[district]}.",
            lang="en",
        )
        rs = key("result_set", "ha-tinh", district)
        p.add(
            "result_set",
            id=rs,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SCAN_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=METHOD, lang="en")
        won = {r.name for r in elected[district]}
        for row in members:
            box = boxes[index]
            index += 1
            name, _, other = row.name.partition(" (")
            person = key("person", "ha-tinh", district, row.position)
            gender = titles.get(row.name)
            p.add("person", id=person, gender=gender)
            p.name(person, name, "vi", "official")
            if other:
                p.name(person, other.rstrip(")"), "vi", "ballot", False)
            option = key("option", "ha-tinh", district, row.position)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=row.position,
                ballot_label=row.name,
            )
            candidacy = key("candidacy", "ha-tinh", district, row.position)
            p.add("candidacy", id=candidacy, person_id=person, option_id=option, role="head", ballot_name=row.name)
            put(rs, unit, option, VOTES, row.votes, DIGITIZED)
            p.add(
                "value_locator",
                result_set_id=rs,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=VOTES,
                count_round=0,
                page_id=pages[box.page],
                x0=box.x0,
                y0=box.y0,
                x1=box.x1,
                y1=box.y1,
            )
            p.add(
                "outcome",
                result_set_id=rs,
                option_id=option,
                kind="elected" if row.name in won else "lost",
            )
    return p


def parse() -> None:
    registry().write()
    ha_tinh().write()


def emit() -> None:
    package.run("load", packages())
