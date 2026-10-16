import itertools
import re
import struct
import subprocess
from datetime import date
from pathlib import Path
from statistics import median
from uuid import UUID

import polars as pl

from ea import raw, scan
from ea.changeset import Changeset, slug
from ea.sources.tr.ysk import (
    ANSWERS_2017,
    DERIVED,
    DIGITIZED,
    INVALID,
    LICENSE,
    PUBLISHER,
    REGISTERED,
    VALID,
    VOTERS,
    VOTES,
)
from ea.store import DATA, connect

SOURCE = "tr.gazette"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
OCR = PARSED / "ocr"
ARCHIVE = "https://www.resmigazete.gov.tr/arsiv"
COLUMNS = (REGISTERED, VOTERS, INVALID, VALID, VOTES, VOTES)
TABLE_1961 = """
domestic 12735009 10322169 39608 10282561 6348191 3934370
"""
TABLE_1982 = """
01 642708 587722 1307 586415 531016 55399
02 131789 116311 202 116109 110834 5275
03 273770 256500 656 255844 238709 17135
04 115532 102779 125 102654 98955 3699
05 159261 149413 292 149121 139364 9757
06 1375024 1235885 2490 1233395 1086945 146450
07 365427 342437 960 341477 315606 25871
08 108737 98957 230 98727 93450 5277
09 352537 332480 1048 331432 307341 24091
10 484804 451459 1171 450288 414771 35517
11 81979 78087 180 77907 74678 3229
12 66293 59245 272 58973 45115 13858
13 72675 65925 156 65769 57448 8321
14 241268 225884 543 225341 210617 14724
15 121334 113501 311 113190 105097 8093
16 645128 606473 1474 604999 562010 42989
17 226392 212085 515 211570 198894 12676
18 112508 102702 209 102493 96328 6165
19 257410 237949 593 237356 216290 21066
20 302579 278006 771 277235 255799 21436
21 247473 214486 793 213693 171598 42095
22 192907 181325 438 180887 167723 13164
23 174358 153299 279 153020 130548 22472
24 111614 100824 176 100648 91323 9325
25 297424 265807 345 265462 250281 15181
26 292641 274696 527 274169 254435 19734
27 335518 309058 494 308564 291573 16991
28 208518 186967 480 186487 176457 10030
29 104816 88919 200 88719 81117 7602
30 44223 40078 114 39964 33083 6881
31 346113 323910 484 323426 305317 18109
32 161956 151964 471 151493 139077 12416
33 395640 368096 879 367217 333737 33480
34 2777249 2417815 5260 2412555 2146443 266112
35 1062776 1036974 2228 1034746 936708 98038
36 251194 226032 214 225818 219164 6654
37 234083 211940 444 211496 198569 12927
38 336448 303732 505 303227 279952 23275
39 149009 140806 319 140487 131168 9319
40 95301 84729 161 84568 79460 5108
41 315008 287764 772 286992 253955 33037
42 681316 622250 1681 620569 551642 68927
43 255221 240263 535 239728 222898 16830
44 235491 208263 393 207870 189234 18636
45 494417 472466 1297 471169 440100 31069
46 250078 228785 520 228265 212783 15482
47 164011 143510 448 143062 124280 18782
48 233134 221620 652 220968 204757 16211
49 85637 75180 221 74959 63826 11133
50 112529 101729 291 101438 91738 9700
51 204388 184219 418 183801 167599 16202
52 300212 271120 638 270482 255236 15246
53 157240 132940 390 132550 121212 11338
54 274465 254159 623 253536 231744 21792
55 452943 415379 862 414517 392271 22246
56 135498 121062 403 120659 106238 14421
57 127918 113763 345 113418 103555 9863
58 300604 271546 513 271033 248802 22231
59 190932 179881 384 179497 168970 10527
60 275469 255594 549 255045 242149 12896
61 301932 265386 610 264776 248021 16755
62 56855 51153 107 51046 42182 8864
63 217656 188344 305 188039 178198 9841
64 126564 117792 293 117499 109171 8328
65 132130 120259 206 120053 109326 10727
66 195985 178905 280 178625 167856 10769
67 456865 426899 1446 425453 390816 34637
"""
TABLE_1987 = """
01 837827 775512 35810 739702 408229 331473
02 166889 156721 6024 150697 71350 79347
03 328881 314318 16113 298205 141337 156868
04 142673 131835 5169 126666 59912 66754
05 186845 179582 8735 170847 80156 90691
06 1769180 1607914 77482 1530432 752674 777758
07 498131 469838 22826 447012 242971 204041
08 122351 115426 4548 110878 65751 45127
09 432370 416803 18664 398139 197141 200998
10 579393 559685 22313 537372 261980 275392
11 95800 93006 4256 88750 41945 46805
12 82819 78913 4534 74379 35805 38574
13 88847 84662 3644 81018 30294 50724
14 284766 274182 10937 263245 106834 156411
15 142773 138695 5820 132875 59186 73689
16 819828 784919 31570 753349 367631 385718
17 262097 253462 8869 244593 115780 128813
18 125654 121201 5980 115221 44947 70274
19 302067 290845 11326 279519 121225 158294
20 377256 364747 16759 347988 177861 170127
21 330519 300345 16369 283976 170160 113816
22 228564 220971 8894 212077 132879 79198
23 216995 204856 10315 194541 108912 85629
24 130288 121441 4066 117375 47886 69489
25 359989 336946 13585 323361 121340 202021
26 358532 342286 16482 325804 157057 168747
27 430250 401120 17892 383228 223239 159989
28 236340 223810 8385 215425 90960 124465
29 119974 110791 4287 106504 35145 71359
30 60584 57563 2077 55486 35152 20334
31 456720 432591 18237 414354 235680 178674
32 194347 188777 8699 180078 128188 51890
33 529511 497744 25219 472525 254219 218306
34 3486194 3070158 150759 2919399 1458789 1460610
35 1402841 1309352 56659 1252693 729255 523438
36 283720 264740 10406 254334 130254 124080
37 250168 239692 10544 229148 108040 121108
38 424614 402627 17219 385408 139986 245422
39 175056 168890 6379 162511 95889 66622
40 113329 106108 4976 101132 42840 58292
41 428571 397938 17119 380819 229164 151655
42 850493 816194 42314 773880 313557 460323
43 303705 295127 14121 281006 131271 149735
44 298094 275323 9456 265867 76229 189638
45 601818 586128 23439 562689 307705 254984
46 329828 310374 12955 297419 151254 146165
47 230390 213362 9293 204069 136886 67183
48 299245 287268 12945 274323 151229 123094
49 113093 105465 5345 100120 38144 61976
50 141402 134258 6595 127663 59868 67795
51 250378 238646 11028 227618 110294 117324
52 352845 328725 13847 314878 136261 178617
53 191029 178082 6192 171890 102505 69385
54 338621 323721 14660 309061 148856 160205
55 571219 538377 21320 517057 271040 246017
56 174748 163007 7487 155520 93842 61678
57 142034 135629 5895 129734 70318 59416
58 346219 327950 14054 313896 122045 191851
59 239963 229941 9865 220076 128471 91605
60 329180 313811 12809 301002 133660 167342
61 372522 345314 13041 332273 159642 172631
62 63374 58946 1619 57327 45130 12197
63 296554 275441 11894 263547 130125 133422
64 152932 146547 6169 140378 63772 76606
65 192618 181320 8683 172637 78407 94230
66 241388 231441 8314 223127 74238 148889
67 554660 531687 26861 504826 312625 192201
customs - 253725 8817 244908 106044 138864
"""
TABLE_1988 = """
01 875608 766762 24608 742154 224142 518012
02 178361 152090 5308 146782 51150 95632
03 327352 305342 11408 293934 98408 195526
04 149390 129451 4225 125226 38704 86522
05 191071 175304 5821 169483 56864 112619
06 1814765 1586315 50482 1535833 560236 975597
07 512258 464471 15097 449374 131860 317514
08 125877 109617 3428 106189 33156 73033
09 438578 408303 14499 393804 149369 244435
10 589677 546726 16015 530711 191304 339407
11 97372 92051 2941 89110 34675 54435
12 86651 78680 4851 73829 26493 47336
13 90634 82261 3387 78874 28867 50007
14 289549 267141 8627 258514 106921 151593
15 144423 134308 4883 129425 46324 83101
16 837673 776157 25198 750959 278587 472372
17 265623 247221 6254 240967 85173 155794
18 128620 117740 5127 112613 45842 66771
19 308604 281426 8390 273036 97290 175746
20 385284 355327 12173 343154 119216 223938
21 349177 295547 12792 282755 76885 205870
22 233210 216837 4941 211896 56475 155421
23 226529 202267 9409 192858 55330 137528
24 134492 113141 3331 109810 44850 64960
25 371478 327056 12189 314867 126653 188214
26 380888 352634 10853 341781 114025 227756
27 445833 391994 13936 378058 103469 274589
28 243804 214237 7027 207210 80704 126506
29 124511 102502 4152 98350 48081 50269
30 63794 59063 1364 57699 11495 46204
31 474999 424203 13564 410639 126101 284538
33 551484 491783 16424 475359 154281 321078
32 197313 184465 6498 177967 38134 139833
34 3661364 3030195 91486 2938709 1192319 1746390
35 1451086 1298829 37885 1260944 411917 849027
46 346555 305508 9762 295746 93527 202219
36 297812 253546 7480 246066 67093 178973
37 254371 230443 8736 221707 81010 140697
38 434945 387918 13014 374904 159847 215057
39 178693 165538 4218 161320 53802 107518
40 125746 110730 3869 106861 38160 68701
41 448251 395311 12487 382824 112946 269878
42 870497 790884 30300 760584 270879 489705
43 309382 289908 11334 278574 102756 175818
44 309832 269707 9471 260236 151557 108679
45 611141 579064 17712 561352 187921 373431
47 242336 209870 9493 200377 63044 137333
48 305311 281900 8376 273524 88957 184567
49 123078 109321 3976 105345 32046 73299
50 144692 129299 4779 124520 42066 82454
51 261074 232762 8469 224293 74413 149880
52 362267 319649 13141 306508 117550 188958
53 198925 169812 6905 162907 50728 112179
54 355649 322090 11351 310739 116034 194705
55 587624 525304 16310 508994 154666 354328
63 314711 265616 9090 256526 82081 174445
56 185648 165330 7794 157536 49612 107924
57 144853 128897 4914 123983 42609 81374
58 351847 311437 10455 300982 112351 188631
59 244167 225117 6268 218849 68844 150005
60 335644 304280 8941 295339 108879 186460
61 380560 336593 13824 322769 118885 203884
62 65680 56307 1323 54984 9925 45059
64 156156 145772 5206 140566 53270 87296
65 201478 182476 6790 175686 48547 127139
66 250322 224664 7236 217428 95941 121487
67 571939 523665 21805 501860 129885 371975
customs - 20709 593 20116 9802 10314
"""
REFERENDUMS = {
    date(1961, 7, 9): (
        10859,
        "Karar no. 106: Anayasanın halkoyuna sunulmasının kesin sonuçları",
        (
            "YSK decision 106, published in Resmî Gazete 10859 on July 20, 1961, gives the final results of the July 9, "
            "1961 referendum on the Constitution adopted by the Constituent Assembly on May 27, 1961. It gives national "
            "totals only, in its text, and no results by province. The scan's text layer misreads the valid votes in "
            "their first mention; the decision repeats them correctly, and the counts add up."
        ),
        TABLE_1961,
        {14: 1},
        None,
        "9 Temmuz 1961 Anayasa Halkoylaması",
        "Constitution of the Republic of Turkey (1961)",
        "Türkiye Cumhuriyeti Anayasası",
    ),
    date(1982, 11, 7): (
        17874,
        "Karar no. 458: 7 Kasım 1982 Anayasa halkoylamasının il sonuçları",
        (
            "YSK decision 458 (November 13, 1982), published in Resmî Gazete 17874 on November 20, 1982, gives the "
            "results of the November 7, 1982 constitutional referendum by province and in total, after the 730 ballot "
            "boxes missing from the first announcement (decision 444, Resmî Gazete 17863) had come in. The table is a "
            "typewritten scan, so every count was digitized and carries a page and bounding-box locator. Kars's No votes "
            "are illegible and were derived as its valid votes less its Yes votes; four printed percentages disagree with "
            "their counts and were left aside."
        ),
        TABLE_1982,
        {12: 39, 13: 28},
        (20690914, 18885488, 43498, 18841990, 17215559, 1626431),
        "7 Kasım 1982 Anayasa Halkoylaması",
        "Constitution of the Republic of Turkey (Law 2709)",
        "2709 sayılı Türkiye Cumhuriyeti Anayasası",
    ),
    date(1987, 9, 6): (
        19572,
        "Karar no. 398: 6 Eylül 1987 Anayasa Değişikliği Halkoylaması sonuçları",
        (
            "YSK decision 398 (September 9, 1987), published in Resmî Gazete 19572 on September 12, 1987, gives the "
            "results of the September 6, 1987 referendum on Article 4 of Law 3361, which repealed Provisional Article 4 "
            "of the Constitution, by province, with a separate row for customs gates. The customs row repeats its "
            "voters as registered voters, so we store no registered voters for customs gates. The table is a scan, so "
            "every count was digitized and carries a page and bounding-box locator."
        ),
        TABLE_1987,
        {4: 24, 5: 24, 6: 20},
        (25841905, 24183096, 1080148, 23102948, 11605417, 11497531),
        "6 Eylül 1987 Anayasa Değişikliği Halkoylaması",
        "Repeal of Provisional Article 4 of the Constitution (Law 3361)",
        "3361 sayılı Kanun ile Anayasanın geçici 4 üncü maddesinin kaldırılması",
    ),
    date(1988, 9, 25): (
        19946,
        "Karar no. 277: 25 Eylül 1988 Anayasa Değişikliği Halkoylaması sonuçları",
        (
            "YSK decision 277, published in Resmî Gazete 19946 on October 1, 1988, gives the results of the September "
            "25, 1988 referendum on Law 3467, which amended Article 127 of the Constitution to bring local elections "
            "forward, by province, with a separate row for customs gates. The customs row repeats its voters as "
            "registered voters, so we store no registered voters for customs gates. The table is a dot-matrix print "
            "scanned without a text layer, so every count was digitized and carries a page and bounding-box locator."
        ),
        TABLE_1988,
        {6: 24, 7: 25, 8: 19},
        (26718518, 23730164, 793402, 22936762, 8025131, 14911631),
        "25 Eylül 1988 Anayasa Değişikliği Halkoylaması",
        "Amendment of Article 127 of the Constitution (Law 3467)",
        "3467 sayılı Kanun ile Anayasanın 127 nci maddesinin değiştirilmesi",
    ),
}
DERIVED_CELLS = {(date(1982, 11, 7), "36", 5)}
NOTES = {
    date(1961, 7, 9): (
        "context",
        (
            "YSK published only national totals for this referendum. The Constitution passed with 61.7 percent of the valid "
            "votes."
        ),
    ),
    date(1982, 11, 7): (
        "context",
        (
            "Under Provisional Article 1 of the Constitution, its approval also elected Kenan Evren, the chairman of the "
            "National Security Council, President of the Republic for seven years."
        ),
    ),
    date(1987, 9, 6): (
        "caveat",
        (
            "Results are missing from eleven villages of Ovacık in Tunceli, 932 registered voters in all, whose ballot "
            "boxes were lost in an incident on the way to the district. YSK found that they could not change the outcome. "
            "The province rows list 25,841,905 voters on the polling-station lists, more than the 25,732,345 on the "
            "registers, because polling-station officials were added to the lists where they served."
        ),
    ),
}


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
    for issue, *_ in REFERENDUMS.values():
        raw.fetch(SOURCE, f"{ARCHIVE}/{issue}.pdf", suffix=".pdf")


def parse() -> None:
    OCR.mkdir(parents=True, exist_ok=True)
    pages = []
    values = []
    for day, (issue, _, _, table, layout, totals, *_) in REFERENDUMS.items():
        pdf = raw.cached(SOURCE, f"{ARCHIVE}/{issue}.pdf")
        rows = [
            (unit, [int(value.replace("-", "0")) for value in counts])
            for unit, *counts in (line.split() for line in table.strip().splitlines())
        ]
        for unit, (registered, voters, invalid, valid, yes, no) in rows:
            if voters != valid + invalid or valid != yes + no or 0 < registered < voters:
                raise ValueError(f"{day}: {unit} does not add up")
        provinces = [counts for unit, counts in rows if unit.isdigit()]
        if totals is not None and (
            sorted(int(unit) for unit, _ in rows if unit.isdigit()) != list(range(1, len(provinces) + 1))
            or [sum(column) for column in zip(*provinces, strict=True)] != list(totals)
        ):
            raise ValueError(f"{day}: the provinces do not sum to the printed totals")
        start = 0
        for page, count in layout.items():
            rendered = render(pdf, page)
            pages.append(rendered)
            width, height = int(str(rendered["width"])) * 3, int(str(rendered["height"])) * 3
            words = (
                [(text, box) for text, box, *_ in scan.words(pdf.path, page, OCR / f"{issue}-{page}.tsv")]
                if issue == 19946
                else scan.layer(pdf.path, page)
            )
            spots: dict[int, list[scan.Box]] = {}
            for text, box in words:
                if digits := re.sub(r"\D", "", text):
                    spots.setdefault(int(digits), []).append(box)
            block = rows[start : start + count]
            start += count
            if len(block) == 1:
                for column, value in enumerate(block[0][1]):
                    if value not in spots:
                        raise ValueError(f"{day}: {value} is not on page {page}")
                    x0, y0, x1, y1 = spots[value][0]
                    values.append(
                        (day, block[0][0], column, value, page, [x0 / width, y0 / height, x1 / width, y1 / height])
                    )
                continue
            single = {
                (index, column): spots[value][0]
                for index, (_, counts) in enumerate(block)
                for column, value in enumerate(counts)
                if value and len(spots.get(value, [])) == 1
            }
            across = {
                column: median((box[0] + box[2]) / 2 for (_, at), box in single.items() if at == column)
                for column in range(6)
            }
            found = {
                cell: box for cell, box in single.items() if abs((box[0] + box[2]) / 2 - across[cell[1]]) < width * 0.04
            }
            for index, (_, counts) in enumerate(block):
                row = [box for (at, _), box in found.items() if at == index]
                if not row:
                    continue
                level = median((box[1] + box[3]) / 2 for box in row)
                for column, value in enumerate(counts):
                    near = [
                        box
                        for box in spots.get(value, [])
                        if abs((box[0] + box[2]) / 2 - across[column]) < width * 0.04
                        and abs((box[1] + box[3]) / 2 - level) < height * 0.006
                    ]
                    if (index, column) not in found and len(near) == 1:
                        found[(index, column)] = near[0]
            levels = {
                index: median((box[1] + box[3]) / 2 for (at, _), box in found.items() if at == index)
                for index in range(len(block))
                if any(at == index for at, _ in found)
            }
            known = sorted(levels)
            pitch = median((levels[b] - levels[a]) / (b - a) for a, b in itertools.pairwise(known))
            half = median((box[3] - box[1]) / 2 for box in found.values()) + 4
            digits = 1 if issue == 17874 else 4 / 3
            glyph = median(
                (box[2] - box[0]) / (len(str(block[at][1][column])) * digits) for (at, column), box in found.items()
            )
            right = {
                column: median(box[2] for (_, at), box in found.items() if at == column) + 4 for column in range(6)
            }
            for index, (unit, counts) in enumerate(block):
                if index not in levels:
                    below = max((at for at in known if at < index), default=None)
                    above = min((at for at in known if at > index), default=None)
                    if below is not None and above is not None:
                        levels[index] = levels[below] + (levels[above] - levels[below]) * (index - below) / (
                            above - below
                        )
                    else:
                        anchor = below if below is not None else known[0]
                        levels[index] = levels[anchor] + pitch * (index - anchor)
                for column, value in enumerate(counts):
                    if value == 0:
                        continue
                    x0, y0, x1, y1 = found.get(
                        (index, column),
                        (
                            right[column] - glyph * len(str(value)) * digits - 8,
                            levels[index] - half,
                            right[column],
                            levels[index] + half,
                        ),
                    )
                    values.append((day, unit, column, value, page, [x0 / width, y0 / height, x1 / width, y1 / height]))
        if start != len(rows):
            raise ValueError(f"{day}: the page layout covers {start} of {len(rows)} rows")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(pages).write_parquet(PARSED / "pages.parquet")
    pl.DataFrame(
        values,
        schema={
            "day": pl.Date,
            "unit": pl.String,
            "column": pl.Int16,
            "value": pl.Int64,
            "page": pl.Int16,
            "box": pl.List(pl.Float64),
        },
        orient="row",
    ).write_parquet(PARSED / "referendums.parquet")


def emit() -> Path:
    pages = pl.read_parquet(PARSED / "pages.parquet")
    values = pl.read_parquet(PARSED / "referendums.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "The 1961, 1982, 1987, and 1988 Turkish constitutional referendums, by province from 1982, from YSK "
                "decisions in the Resmî Gazete"
            ),
            polity="TR",
        )

        def name(entity: UUID, text: str, lang: str = "tr", kind: str = "official") -> None:
            cs.add(
                "name",
                entity_id=entity,
                text=text,
                lang=lang,
                kind=kind,
                is_preferred=kind == "official",
                valid_from=None,
                valid_from_precision="day",
                valid_to=None,
                valid_to_precision="day",
            )

        turkey = cs.polity("TR")
        system = cs.key("tr.electoral_system", "referendum")
        units = {
            unit: cs.unit("tr.nation" if unit in ("domestic", "customs") else "tr.il", unit)
            for unit in set(values["unit"])
        }
        if turkey in cs.minted or system in cs.minted or cs.minted & set(units.values()):
            raise ValueError("publish tr.ysk before tr.gazette")
        result_sets: dict[date, UUID] = {}
        options: dict[tuple[date, int], UUID] = {}
        page_ids: dict[tuple[date, int], UUID] = {}
        for day, (issue, heading, notes, _, _, _, native, law, law_native) in REFERENDUMS.items():
            url = f"{ARCHIVE}/{issue}.pdf"
            source = cs.source(PUBLISHER, heading)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=heading,
                url=url,
                license=LICENSE,
                notes=notes,
            )
            artifact = cs.artifact(raw.cached(SOURCE, url), source)
            for _, number, image, width, height in pages.filter(pl.col("url") == url).sort("page").iter_rows():
                page_ids[(day, number)] = cs.page(artifact, number)
                cs.add(
                    "source_page",
                    id=page_ids[(day, number)],
                    artifact_id=artifact,
                    page_number=number,
                    image_key=image,
                    width=width,
                    height=height,
                )
            vote = cs.key("tr.election", f"{day}:referendum")
            cs.add("election", id=vote, polity_id=turkey, kind="referendum", date_from=day, date_to=day, status="held")
            name(vote, f"{day.year} Turkish constitutional referendum", "en")
            name(vote, native, kind="native")
            cs.add("slug", path=f"tr/{day}-referendum", entity_id=vote)
            contest_key = f"{day}:constitution"
            contest = cs.key("tr.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=vote,
                kind="ballot_question",
                electoral_system_id=system,
                round_number=1,
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
            name(contest, law, "en")
            name(contest, law_native, kind="native")
            cs.add("slug", path=f"tr/{day}-referendum/constitution", entity_id=contest)
            declared = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=declared,
                election_id=vote,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            if declared in cs.minted:
                result_sets[day] = declared
            if day in NOTES:
                kind, body = NOTES[day]
                cs.add("note", entity_id=vote if kind == "context" else declared, kind=kind, body_md=body)
            day_values = values.filter(pl.col("day") == day)
            yes, no = (day_values.filter(pl.col("column") == column)["value"].sum() for column in (4, 5))
            for position, answer in enumerate(ANSWERS_2017, 1):
                option = cs.key("tr.option", f"{contest_key}:{slug(answer)}")
                options[(day, position + 3)] = option
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position,
                    ballot_label=answer,
                )
                if (yes > no) == (position == 1):
                    cs.add("outcome", result_set_id=declared, option_id=option, kind="won")

        located = values.filter(pl.col("day").is_in(list(result_sets))).with_columns(
            result_set_id=pl.col("day").replace_strict({day: str(id) for day, id in result_sets.items()}),
            unit_id=pl.col("unit").replace_strict({unit: str(id) for unit, id in units.items()}),
            option_id=pl.struct("day", "column").map_elements(
                lambda cell: None if cell["column"] < 4 else str(options[(cell["day"], cell["column"])]),
                pl.String,
            ),
            page_id=pl.struct("day", "page").map_elements(
                lambda cell: str(page_ids[(cell["day"], cell["page"])]), pl.String
            ),
            measure=pl.col("column").replace_strict(dict(enumerate(COLUMNS))),
            flags=pl.struct("day", "unit", "column").map_elements(
                lambda cell: (
                    DIGITIZED | DERIVED if (cell["day"], cell["unit"], cell["column"]) in DERIVED_CELLS else DIGITIZED
                ),
                pl.Int16,
            ),
        )
        keys = {
            "result_set_id": "result_set_id",
            "unit_id": "unit_id",
            "option_id": "option_id",
            "channel": pl.lit(0, pl.Int16),
            "measure": pl.col("measure").cast(pl.Int16),
            "count_round": pl.lit(0, pl.Int16),
        }
        cs.frame("result_value", located.select(**keys, flags="flags", value="value"))
        cs.frame(
            "value_locator",
            located.select(
                **keys,
                page_id="page_id",
                source_id=pl.lit(None, pl.String),
                x0=pl.col("box").list.get(0),
                y0=pl.col("box").list.get(1),
                x1=pl.col("box").list.get(2),
                y1=pl.col("box").list.get(3),
            ),
        )
        return cs.write()
