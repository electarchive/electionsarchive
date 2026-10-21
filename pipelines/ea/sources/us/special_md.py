import re
from datetime import date

from ea import raw

SOURCE = "us.elstats_specials"
PAGES = [
    ("https://elections.maryland.gov/elections/archive/2008/2008_special_4th_congressional_general_results.html", date(2008, 6, 17), 4),
    ("https://elections.maryland.gov/elections/archive/2020/results/special_general/gen_detail_results_2020_3_BOT00907.html", date(2020, 4, 28), 7),
]
PARTY = re.compile(r"^(.*?)\s+(Democrat|Democratic|Republican|Libertarian|Unaffiliated(?: \(Write-In\))?)$")


def fetch() -> None:
    for page, _, _ in PAGES:
        raw.fetch(SOURCE, page, suffix=".html")


def rows() -> list[dict[str, object]]:
    found = []
    for page, day, district in PAGES:
        header: list[tuple[str, str | None]] = []
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", raw.cached(SOURCE, page).path.read_text(errors="replace"), re.S | re.I):
            cells = [
                re.sub(r"\s+", " ", re.sub(r"<[^>]*>", "", cell).replace("&nbsp;", " ")).strip()
                for cell in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S | re.I)
            ]
            if cells[:1] in (["County"], ["Jurisdiction"]):
                header = []
                for cell in cells[1:]:
                    hit = PARTY.match(cell)
                    if "Write" in cell or hit is None:
                        header.append(("Write-ins", None))
                    else:
                        header.append((hit[1], "Democratic" if hit[2] == "Democrat" else hit[2]))
            elif header and len(cells) == len(header) + 1 and cells[0] and not cells[0].startswith("Total"):
                found.extend(
                    {
                        "state": "MD",
                        "election_date": str(day),
                        "district_name": str(district),
                        "candidate_name": name,
                        "candidate_party_name": None if party is None or party.startswith("Unaffiliated") else party,
                        "division_name": cells[0],
                        "votes": cell.split()[0].replace(",", ""),
                    }
                    for (name, party), cell in zip(header, cells[1:], strict=True)
                )
    return found
