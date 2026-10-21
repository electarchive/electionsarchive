from ea import raw

SOURCE = "us.elstats_specials"
CANVASS = "https://klvg4oyd4j.execute-api.us-west-2.amazonaws.com/prod/PublicFiles/ee3072ab0d43456cb15a51f7d82c77a2/6dc5f101-ba93-42f1-a40a-77761ce5625b/1997%20Special%20Election_CongDist3.pdf"
CANDIDATES = [
    ("Carol A. Miller", "Green"),
    ("Eric P. Serna", "Democratic"),
    ("Bill Redmond", "Republican"),
    ("Daniel J. Pearlman", "Reform"),
    ("Edward D. Nagel", "Libertarian"),
    ("John Bishop", None),
    ("Orlin G. Cole", None),
    ("Michael A. Guss", None),
]
LEFT = [265, 485, 702, 918, 1134, 1349, 1566, 1781, 2004]
RIGHT = [433, 652, 866, 1080, 1295, 1509, 1725, 1939, 2162]
COUNTIES = [
    ("Bernalillo", 7, RIGHT, 1406, (173, 296, 1206, 4, 6, 0, 0, 0)),
    ("Cibola", 8, LEFT, 1370, (39, 195, 150, 6, 4, 0, 0, 0)),
    ("Colfax", 9, RIGHT, 1413, (325, 1203, 1360, 8, 6, 1, 0, 0)),
    ("Curry", 10, LEFT, 1358, (167, 1249, 3840, 13, 8, 0, 0, 0)),
    ("Harding", 11, RIGHT, 1414, (6, 114, 218, 0, 0, 0, 0, 0)),
    ("Los Alamos", 12, LEFT, 1356, (1210, 1696, 3433, 26, 62, 4, 1, 0)),
    ("McKinley", 14, LEFT, 1342, (769, 4953, 2779, 33, 38, 0, 0, 0)),
    ("Mora", 15, RIGHT, 1422, (80, 944, 321, 2, 2, 0, 0, 0)),
    ("Quay", 16, LEFT, 1363, (75, 816, 1298, 5, 2, 0, 0, 1)),
    ("Rio Arriba", 17, RIGHT, 1410, (841, 4897, 1650, 8, 12, 1, 0, 0)),
    ("Roosevelt", 18, LEFT, 1362, (119, 584, 1756, 4, 4, 0, 0, 0)),
    ("Sandoval", 21, RIGHT, 1399, (1588, 3095, 5401, 29, 61, 6, 0, 0)),
    ("San Juan", 19, RIGHT, 1404, (919, 4511, 10858, 54, 64, 4, 0, 0)),
    ("San Miguel", 20, LEFT, 1354, (678, 3193, 1034, 9, 13, 0, 0, 0)),
    ("Santa Fe", 22, LEFT, 1363, (8697, 10073, 6623, 52, 91, 20, 0, 2)),
    ("Taos", 23, RIGHT, 1403, (1394, 2463, 1114, 48, 17, 0, 0, 2)),
    ("Union", 24, LEFT, 1371, (21, 260, 518, 3, 3, 0, 0, 0)),
]


def fetch() -> None:
    raw.fetch(SOURCE, CANVASS, suffix=".pdf")


def rows() -> list[dict[str, object]]:
    found = []
    for county, page, columns, top, votes in COUNTIES:
        for index, ((name, party), count) in enumerate(zip(CANDIDATES, votes, strict=True)):
            found.append(
                {
                    "state": "NM",
                    "election_date": "1997-05-13",
                    "district_name": "3",
                    "candidate_name": name,
                    "candidate_party_name": party,
                    "division_name": county,
                    "votes": str(count),
                    "page": page,
                    "box": [columns[index] / 2550, (top - 6) / 1650, columns[index + 1] / 2550, (top + 40) / 1650],
                }
            )
    return found
