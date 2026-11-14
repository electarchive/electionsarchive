import html
import re
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.gb.cc_elections import display
from ea.sources.gb.parish import fold, slug
from ea.sources.licenses import COUNCIL
from ea.store import DATA, connect

SOURCE = "gb.parish_elections"
VERSION = "1"
WARDS = "gb.parish_ward"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / SOURCE
VOTES = 1
MEASURES = {"electorate": 2, "ballots": 3, "rejected": 5}
COUNCILS: dict[str, dict] = {
    "E07000127": {
        "day": date(2026, 5, 7),
        "publisher": "West Lancashire Borough Council",
        "title": "Parish - West Lancashire Borough Council Elections 2026",
        "url": "https://democracy.westlancs.gov.uk/mgElectionResults.aspx?ID=17&RPID=15632299",
        "inbox": "west-lancashire",
        "reader": "moderngov",
    },
    "E06000006": {
        "day": date(2026, 5, 7),
        "publisher": "Halton Borough Council",
        "title": "Parish - Local Elections",
        "url": "https://moderngov.halton.gov.uk/mgElectionResults.aspx?ID=45&RPID=815205123",
        "inbox": "halton",
        "reader": "moderngov",
    },
    "E07000011": {
        "day": date(2026, 5, 7),
        "publisher": "Huntingdonshire District Council",
        "title": "Town and parish election results 2026",
        "url": "https://huntingdonshire.gov.uk/news/huntingdonshire-town-and-parish-election-results-2026/",
        "files": [
            "https://huntingdonshire.gov.uk/media/ybxjhnid/declaration-of-result-of-poll-parish-7th-may-2026.pdf",
            "https://huntingdonshire.gov.uk/media/umpollv3/notice-of-uncontested-election-town-parish-councils-7-may-2026.pdf",
        ],
        "reader": "notices",
    },
    "E07000177": {
        "day": date(2026, 5, 7),
        "publisher": "Cherwell District Council",
        "title": "Notices of uncontested parish elections, 7 May 2026",
        "url": "https://www.cherwell.gov.uk/download/downloads/id/18690/islip.pdf",
        "files": [
            f"https://www.cherwell.gov.uk/download/downloads/id/{id}/{name}.pdf"
            for id, name in (
                (18711, "barford"),
                (18710, "bletchingdon"),
                (18700, "bloxham"),
                (18715, "broughton"),
                (18717, "charlton-on-otmoor"),
                (18699, "deddington"),
                (18704, "duns-tew"),
                (18707, "epwell"),
                (18698, "fencott-and-murcott"),
                (18716, "finmere"),
                (18709, "fringford"),
                (18706, "hethe"),
                (18702, "horley"),
                (18701, "hornton"),
                (18692, "horton-cum-studley"),
                (18690, "islip"),
                (18691, "kirtlington"),
                (18693, "merton"),
                (18695, "milcombe"),
                (18703, "north-newington"),
                (18708, "shenington-with-alkerton"),
                (18719, "shutford"),
                (18705, "sibford-ferris"),
                (18718, "sibford-gower"),
                (18712, "steeple-aston"),
                (18694, "stratton-audley"),
                (18697, "swalcliffe"),
                (18696, "tadmarton"),
                (18713, "weston-on-the-green"),
                (18714, "wroxton"),
            )
        ],
        "reader": "notices",
    },
}
COUNCILS["E06000030"] = {
    "day": date(2026, 5, 7),
    "publisher": "Swindon Borough Council",
    "title": "Parish election results 2026",
    "url": "https://www.swindon.gov.uk/info/20073/elections_and_voting/769/election_results/3",
    "pages": ["https://www.swindon.gov.uk/info/20073/elections_and_voting/769/election_results/3"],
    "reader": "swindon",
}
COUNCILS["E07000220"] = {
    "day": date(2026, 5, 7),
    "publisher": "Rugby Borough Council",
    "title": "2026 elections - parish councils",
    "url": "https://rugby.gov.uk/w/2026-elections-parish-councils",
    "pages": ["https://rugby.gov.uk/w/2026-elections-parish-councils"],
    "files": [
        "https://www.rugby.gov.uk/documents/20124/63178158/Notice+of+uncontested+election+-+Easenhall.pdf/18782199-459c-7524-31df-171a6bd35d3e?t=1775749323133",
        "https://www.rugby.gov.uk/documents/20124/63178158/Notice+of+uncontested+election+-+Shilton+%26+Barnacle.pdf/71863cda-8bb5-15b9-109e-c011889bbad4?t=1775749336972",
        "https://www.rugby.gov.uk/documents/20124/63178158/Notice+of+uncontested+election+-+Wolston.pdf/990b1c59-a4ea-f6f2-7699-c53642e77a47?t=1775749346984",
        "https://www.rugby.gov.uk/documents/20124/63178158/Notice+of+uncontested+election+-+Wolvey.pdf/ec0ffaa0-9947-758b-6e42-e35b34f77995?t=1775749363889",
    ],
    "columns": (290, 290),
    "reader": "notices+rugby",
}
COUNCILS["E07000181"] = {
    "day": date(2026, 5, 7),
    "publisher": "West Oxfordshire District Council",
    "title": "Notice of uncontested election, parish and town councils, 7 May 2026",
    "url": "https://www.westoxon.gov.uk/media/kykluq1i/2026-notice-of-uncontested-election-parish.pdf",
    "files": ["https://www.westoxon.gov.uk/media/kykluq1i/2026-notice-of-uncontested-election-parish.pdf"],
    "reader": "notices",
}
HART = "https://www.hart.gov.uk"
COUNCILS["E07000084"] = {
    "day": date(2026, 5, 7),
    "publisher": "Basingstoke and Deane Borough Council",
    "title": "Notices of uncontested election, parish councils, 7 May 2026",
    "url": "https://www.basingstoke.gov.uk/county-borough-parish-elections",
    "files": [
        f"https://www.basingstoke.gov.uk/content/page/{id}/Notice%20of%20Uncontested%20Election%20-%20{name}.pdf"
        for id, name in (
            (92922, "Ashmansworth"),
            (92923, "Bishops%20Green%20Parish%20Ward"),
            (92924, "Burghclere"),
            (92925, "East%20Woodhay"),
            (92926, "Ecchinswell%20Parish%20Ward"),
            (92927, "Highclere%20Parish%20Ward"),
            (92928, "Newtown"),
            (92929, "Penwood%20Parish%20Ward"),
            (92930, "St%20Mary%20Bourne"),
        )
    ],
    "areas": {
        "Bishops Green": ("Ecchinswell, Sydmonton and Bishops Green", "Bishops Green"),
        "Ecchinswell": ("Ecchinswell, Sydmonton and Bishops Green", "Ecchinswell"),
        "Highclere Parish Ward": ("Highclere", "Highclere"),
        "Penwood Parish Ward": ("Highclere", "Penwood"),
    },
    "reader": "notices",
}
COUNCILS["E06000001"] = {
    "day": date(2026, 5, 7),
    "publisher": "Hartlepool Borough Council",
    "title": "Declaration of result of poll, Elwick Parish, 7 May 2026",
    "url": "https://www.hartlepool.gov.uk/downloads/file/1535/elwick-parish-election-results-7-may-2026",
    "polls": ["https://www.hartlepool.gov.uk/downloads/file/1535/elwick-parish-election-results-7-may-2026"],
    "order": "upper-last",
    "areas": {"ELWICK PARISH": ("Elwick", None)},
    "reader": "declarations",
}
COUNCILS["E07000089"] = {
    "day": date(2026, 5, 7),
    "publisher": "Hart District Council",
    "title": "Notice of uncontested town and parish elections, and the declaration of result for Fleet Town Council, 7 May 2026",
    "url": f"{HART}/voting-and-elections/7-may-2026-election-results",
    "files": [
        f"https://web.archive.org/web/2026id_/{HART}/sites/default/files/2026-04/Notice-of-Uncontested-Town-and-Parish-Elections-2026.pdf"
    ],
    "polls": [f"{HART}/sites/default/files/2026-05/7-may-2026-declaration-results-fleet-town-council-calthorpe.pdf"],
    "order": "upper-first",
    "areas": {
        "Calthorpe": ("Fleet", "Calthorpe"),
        "Fleet Town Council - Ancells": ("Fleet", "Ancells"),
        "Fleet Town Council - Courtmoor": ("Fleet", "Courtmoor"),
        "Fleet Town Council - Fleet Central": ("Fleet", "Fleet Central"),
        "Fleet Town Council - Pondtail": ("Fleet", "Pondtail"),
        "Church Crookham - Church Crookham East": ("Church Crookham", "Church Crookham East"),
        "Church Crookham - Gally Hill": ("Church Crookham", "Gally Hill"),
        "Church Crookham - Queen Elizabeth": ("Church Crookham", "Queen Elizabeth"),
        "Elvetham Heath - Elvetham Heath East": ("Elvetham Heath", "Elvetham Heath East"),
        "Elvetham Heath - Elvetham Heath West": ("Elvetham Heath", "Elvetham Heath West"),
        "Long Sutton & Well": ("Long Sutton", None),
    },
    "reader": "notices+declarations",
}
COUNCILS["E07000071"] = {
    "day": date(2026, 5, 7),
    "publisher": "Colchester City Council",
    "title": "Parish elections 7 May 2026: notices of uncontested election and the East Mersea declaration of result",
    "url": "https://new.colchester.gov.uk/elections/upcoming-elections/parish-elections-7-may-2026",
    "files": [
        "https://cbccrmdata.blob.core.windows.net/noteattachment/CBC-null-Uncontested-Notices-Uncontested%20Notices.pdf"
    ],
    "pages": ["https://new.colchester.gov.uk/elections/2026-election-results/east-mersea-parish-elections-results"],
    "poll": "East Mersea",
    "reader": "colchester+colchester_poll",
}
COUNCILS["E07000118"] = {
    "day": date(2026, 5, 7),
    "publisher": "Chorley Borough Council",
    "title": "Statement of uncontested elections, parish, 7 May 2026",
    "url": "https://chorley.gov.uk/downloads/file/1760/statement-of-uncontested-elections-parish",
    "files": ["https://chorley.gov.uk/downloads/file/1760/statement-of-uncontested-elections-parish"],
    "areas": {
        "EUXTON BUCKSHAW VILLAGE Parish Ward": ("Euxton", "Buckshaw Village"),
        "EUXTON NORTH & EAST Parish Ward": ("Euxton", "North & East"),
        "EUXTON SOUTH Parish Ward": ("Euxton", "South"),
        "HEAPEY Parish": ("Heapey", None),
        "MAWDESLEY Parish": ("Mawdesley", None),
        "WHEELTON Parish": ("Wheelton", None),
        "WHITTLE-LE-WOODS EAST Parish Ward": ("Whittle-le-Woods", "East"),
        "WHITTLE-LE-WOODS WEST Parish Ward": ("Whittle-le-Woods", "West"),
    },
    "reader": "chorley",
}
COUNCILS["E07000086"] = {
    "day": date(2026, 5, 7),
    "publisher": "Eastleigh Borough Council",
    "title": "Parish and town councils: declaration of results, 7 May 2026",
    "url": "https://www.eastleigh.gov.uk/media/16662/parish-and-town-councils-declaration-of-results.pdf",
    "files": ["https://www.eastleigh.gov.uk/media/16662/parish-and-town-councils-declaration-of-results.pdf"],
    "order": "comma",
    "reader": "declarations",
}
POST_NOMINALS = re.compile(r"^(?:[A-Z]{2,5}\.\s+)+")
BROWSER = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
PARTIES: dict[str, str | None] = {
    "": None,
    "Independent": "independent",
    "Independent no description": "independent",
    "Reform UK": "Q104721252",
    "Liberal Democrat": "Q9624",
    "Liberal Democrats": "Q9624",
    "Labour Party": "Q9630",
    "Labour": "Q9630",
    "Conservative Party": "Q9626",
    "Conservative Party Candidate": "Q9626",
    "The Conservative Party Candidate": "Q9626",
    "The Green Party candidate": "Q3499324",
    "Conservative": "Q9626",
    "Green Party": "Q3499324",
}
ALIASES = {
    ("E07000011", "Kimbolton & Stonely"): "Kimbolton",
    ("E07000011", "Leighton Bromswold"): "Leighton",
    ("E07000181", "Asthal"): "Asthall",
}
GROUPED = {
    ("E07000011", "Great and Little Gidding"),
    ("E07000071", "Abberton and Langenhoe"),
    ("E07000071", "Winstred Hundred"),
}
CONTEXT = (
    "England's parish and town councils are elected every four years, ward by ward where a parish is warded, by "
    "block vote, on the day of the principal council's elections. Where no more candidates are nominated than there "
    "are seats, those nominated are declared elected without a poll; seats left unfilled are filled by co-option."
)
UNSTATED = (
    "{publisher}'s results list the candidates declared elected unopposed but not how many seats the ward has, "
    "so any seat left unfilled is not recorded."
)


def fetch() -> None:
    for council in COUNCILS.values():
        for url in council.get("files", []) + council.get("polls", []) + council.get("pages", []):
            try:
                raw.cached(SOURCE, url)
                continue
            except LookupError:
                pass
            response = httpx.get(url, headers={"User-Agent": BROWSER}, timeout=120, follow_redirects=True)
            response.raise_for_status()
            pdf = url not in council.get("pages", [])
            if pdf != response.content.startswith(b"%PDF"):
                raise ValueError(f"{url} is not the expected {'PDF' if pdf else 'page'}")
            raw.store(
                SOURCE,
                url,
                response.content,
                suffix=".pdf" if pdf else ".html",
                retrieved_at=datetime.now(UTC),
                media_type="application/pdf" if pdf else "text/html",
            )
        if "inbox" not in council:
            continue
        for file in sorted((INBOX / council["inbox"]).glob("*.html")):
            content = file.read_bytes()
            url = re.match(rb"<!-- URL: (\S+) -->", content)
            if url is None:
                raise ValueError(f"{file} has no URL header")
            raw.store(
                SOURCE,
                url.group(1).decode(),
                content,
                suffix=".html",
                retrieved_at=datetime.fromtimestamp(file.stat().st_mtime, UTC),
                media_type="text/html",
            )


def text(cell: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", cell)).split())


def moderngov(lad: str, council: dict) -> list[dict]:
    reads = []
    index = raw._read(SOURCE)
    for url in sorted(
        key for key in index if "mgElectionAreaResults" in key and key.startswith(council["url"].split("/mg")[0])
    ):
        page = Path(raw.cached(SOURCE, url).path).read_text(encoding="utf-8")
        caption = re.search(r'<caption class="mgSectionTitle">(.*?) - results</caption>', page, re.DOTALL)
        if caption is None:
            raise ValueError(f"{url}: no caption")
        area = text(caption.group(1))
        parish, _, ward = area.partition(" - ")
        table = re.search(r'summary="Table of results[^"]*">(.*?)</table>', page, re.DOTALL)
        assert table is not None
        candidates = []
        for row in re.findall(r'<tr class="mgTable(?:Odd|Even)Row">(.*?)</tr>', table.group(1), re.DOTALL):
            cells = [text(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)]
            printed, description, votes, _, outcome = cells
            candidates.append(
                {
                    "printed": printed,
                    "name": printed.replace("`", "'"),
                    "description": description,
                    "votes": None if votes == "No contest" else int(votes.replace(",", "")),
                    "elected": outcome == "Elected",
                }
            )
        voting = re.search(r'summary="Voting summary table">(.*?)</table>', page, re.DOTALL)
        summary = {
            text(label): text(value)
            for label, value in re.findall(
                r"<td[^>]*>([^<]+)</td>\s*<td[^>]*mgAlignRightCell[^>]*>(.*?)</td>",
                voting.group(1) if voting else "",
                re.DOTALL,
            )
        }
        contested = any(candidate["votes"] is not None for candidate in candidates)
        if contested and any(candidate["votes"] is None for candidate in candidates):
            raise ValueError(f"{url}: mixed contest")

        def number(label: str) -> int | None:
            value = summary.get(label)
            return int(value.replace(",", "")) if value else None

        reads.append(
            {
                "lad": lad,
                "url": url,
                "page": None,
                "style": None,
                "parish": parish,
                "ward": ward or None,
                "places": number("Seats") if contested else None,
                "electorate": number("Electorate"),
                "ballots": number("Number of ballot papers issued"),
                "rejected": number("Number of ballot papers rejected"),
                "total": number("Total votes"),
                "remark": None,
                "candidates": candidates,
            }
        )
    return reads


def pdf_pages(url: str) -> list[tuple[str, list[tuple[float, float, str]]]]:
    path = str(raw.cached(SOURCE, url).path)
    layout = subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, text=True, check=True).stdout
    boxes = subprocess.run(["pdftotext", "-bbox", path, "-"], capture_output=True, text=True, check=True).stdout
    pages = []
    for text_page, box_page in zip(layout.split("\f"), boxes.split("<page ")[1:], strict=False):
        words = [
            (float(x), float(y), html.unescape(word))
            for x, y, word in re.findall(r'xMin="([\d.]+)" yMin="([\d.]+)"[^>]*>([^<]*)</word>', box_page)
        ]
        pages.append((text_page, words))
    return pages


def rows_of(words: list[tuple[float, float, str]], top: float, bottom: float) -> list[list[tuple[float, float, str]]]:
    lines: list[list[tuple[float, float, str]]] = []
    for word in sorted((w for w in words if top < w[1] < bottom), key=lambda w: (w[1], w[0])):
        if lines and word[1] - lines[-1][0][1] < 4:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line) for line in lines]


def joined(line: list[tuple[float, float, str]], low: float, high: float) -> str:
    return " ".join(word for x, _, word in line if low <= x < high)


def surname_first(printed: str) -> str:
    surname, forenames = (part.strip() for part in printed.split(",", 1))
    return f"{forenames} {display(surname)}"


def area_of(page: str) -> tuple[str, str, str | None] | None:
    heading = re.search(
        r"Election of (?:a )?(?:(Parish|Town) )?Councillors? for(?: the)?\s*\n\s*(.+?)(?:\s*\n\s*|\s+)on(?=\s*\n|\s+(?:\d|Thursday))",
        page,
    )
    if heading is None:
        inline = re.search(
            r"Election of (Parish) Councillors for\s+(.+?)\s+Parish\s+Council\s+on Thursday", page, re.DOTALL
        )
        if inline:
            return inline.group(1), " ".join(inline.group(2).split()), None
        return None
    area = " ".join(heading.group(2).split())
    area = re.sub(r"^(?:Parish|Town) of ", "", area)
    area = re.sub(r"(?<=[A-Z]) (?:Parish|Town)$", "", area)
    if area.isupper():
        area = area.title()
    ward = re.fullmatch(r"(.+?) \((.+)\)", area)
    return heading.group(1) or "Parish", *((ward.group(1), ward.group(2)) if ward else (area, None))


def notices(lad: str, council: dict) -> list[dict]:
    reads: list[dict] = []
    for url in council["files"]:
        for number, (page, words) in enumerate(pdf_pages(url), start=1):
            if not page.strip():
                continue
            heading = area_of(page)
            if heading is not None and "(surname first)" in page:
                style, parish, ward = heading
                parish, ward = council.get("areas", {}).get(parish, (parish, ward))
                body = page.split("(surname first)")[1].split("Returning Officer")[0]
                lines = [re.split(r"\s{2,}", line)[0] for line in body.split("\n")[1:] if line[:1].strip()][:-1]
                if not lines or len(lines) % 2:
                    raise ValueError(f"{url} page {number}: odd name lines")
                candidates = [
                    {
                        "printed": f"{surname}, {forenames}".replace("`", "'"),
                        "known_as": None,
                        "description": "",
                        "votes": None,
                        "elected": True,
                    }
                    for surname, forenames in zip(lines[::2], lines[1::2], strict=True)
                ]
                reads.append(
                    {
                        "lad": lad,
                        "url": url,
                        "page": number,
                        "style": style,
                        "parish": parish,
                        "ward": ward,
                        "uncontested": False,
                        "places": None,
                        "electorate": None,
                        "ballots": None,
                        "rejected": None,
                        "total": None,
                        "remark": None,
                        "candidates": candidates,
                    }
                )
                continue
            if heading is None:
                if not reads and "UNCONTESTED ELECTIONS" in page:
                    continue
                if not reads or reads[-1]["url"] != url or not reads[-1]["uncontested"]:
                    raise ValueError(f"{url} page {number}: no heading")
                read = reads[-1]
                header = 0.0
            else:
                style, parish, ward = heading
                parish, ward = council.get("areas", {}).get(parish, (parish, ward))
                header = max(y for x, y, word in words if word == "Candidate" and x < 260 and y < 300)
                name_limit, description_x = council.get("columns", (215, 375))
                read: dict = {
                    "lad": lad,
                    "url": url,
                    "page": number,
                    "style": style,
                    "parish": parish,
                    "ward": ward,
                    "uncontested": "UNCONTESTED" in page,
                    "places": None,
                    "electorate": None,
                    "ballots": None,
                    "rejected": None,
                    "total": None,
                    "remark": None,
                    "candidates": [],
                }
                reads.append(read)
            candidates: list[dict] = read["candidates"]
            if read["uncontested"]:
                bottom = min((y for x, y, word in words if word.startswith(("Dated", "Printed"))), default=9999.0)
                for line in rows_of(words, header + 6, bottom):
                    name = joined(line, 0, name_limit)
                    description = joined(line, description_x, 999)
                    if re.match(r"[A-Z][A-Z'’ -]*, \S", name):
                        surname, forenames = name.split(",", 1)
                        candidates.append({"surname": surname, "forenames": forenames.strip(), "description": ""})
                    elif name and name == name.upper() and re.search(r"[A-Z]", name):
                        candidates.append({"surname": name, "forenames": "", "description": ""})
                    elif name:
                        if not candidates:
                            raise ValueError(f"{url} page {number}: no surname before {name!r}")
                        candidates[-1]["forenames"] = f"{candidates[-1]['forenames']} {name}".strip()
                    if description:
                        candidates[-1]["description"] = f"{candidates[-1]['description']} {description}".strip()
                continue
            bottom = min(y for x, y, word in words if word.startswith("*"))
            for line in rows_of(words, header + 6, bottom):
                name = joined(line, 0, 250)
                description = joined(line, 250, 470)
                votes = [word for x, _, word in line if x >= 470 and word.isdigit()]
                known = candidates[-1]["known_as"] if candidates else None
                if name.startswith("commonly known as "):
                    candidates[-1]["known_as"] = name.removeprefix("commonly known as ")
                elif name and known is not None and "," not in known:
                    candidates[-1]["known_as"] = f"{known}{'' if known.endswith('-') else ' '}{name}"
                elif re.match(r"[A-Z][A-Z'’ -]*,", name):
                    candidates.append(
                        {"printed": name, "known_as": None, "description": "", "votes": None, "elected": False}
                    )
                elif name:
                    raise ValueError(f"{url} page {number}: stray name {name}")
                if description:
                    candidates[-1]["description"] = f"{candidates[-1]['description']} {description}".strip()
                if votes:
                    candidates[-1]["votes"] = int(votes[0])
                if any(word == "(E)" for x, _, word in line):
                    candidates[-1]["elected"] = True
            figures = re.search(r"Vacant Seats:\s*(\d+)\s+Electorate:\s*(\d+)\s+Ballot Papers Issued:\s*(\d+)", page)
            rejected = re.search(r"\n\s+Total\s+(\d+)\s*\n", page)
            if figures is None or rejected is None:
                raise ValueError(f"{url} page {number}: no figures")
            read |= {
                "places": int(figures.group(1)),
                "electorate": int(figures.group(2)),
                "ballots": int(figures.group(3)),
                "rejected": int(rejected.group(1)),
            }
            lots = re.search(r"(Following an equality of votes.*?\.)\s*\n\s*\n", page, re.DOTALL)
            if lots:
                read["remark"] = " ".join(lots.group(1).split())
            if any(candidate["votes"] is None for candidate in candidates):
                raise ValueError(f"{url} page {number}: a candidate has no votes")
    for read in reads:
        for candidate in read["candidates"]:
            if read.pop("uncontested", None) or "surname" in candidate:
                candidate |= {
                    "printed": f"{candidate.pop('surname')}, {candidate.pop('forenames')}",
                    "known_as": None,
                    "votes": None,
                    "elected": True,
                }
            printed = candidate["printed"]
            candidate["name"] = surname_first(candidate["known_as"] or printed)
            if candidate["known_as"]:
                candidate["printed"] = f"{printed} commonly known as {candidate['known_as']}"
            del candidate["known_as"]
        read.pop("uncontested", None)
    return reads


def count(value: str) -> int:
    return int(value.replace(",", ""))


def swindon(lad: str, council: dict) -> list[dict]:
    url = council["pages"][0]
    page = Path(raw.cached(SOURCE, url).path).read_text(encoding="utf-8")
    reads = []
    for heading, body in re.findall(r"<h2 id=\"[^\"]+\">(.*?)</h2>(.*?)<hr />", page + "<hr />", re.DOTALL):
        if "<table" not in body:
            continue
        area = text(heading)
        parish, _, ward = area.partition(" Parish \u2013 ")
        candidates = []
        for row in re.findall(r"<tr>\s*<td>(.*?)</tr>", body, re.DOTALL):
            printed, description, votes = (
                text(cell) for cell in re.findall(r"(.*?)</td>\s*(?:<td>|$)", row, re.DOTALL)
            )
            surname = re.match(r"((?:[A-Z][A-Z'\u2019-]*\s+)*[A-Z][A-Z'\u2019-]*)\s+(.+)", printed)
            if surname is None:
                raise ValueError(f"{url}: no surname in {printed}")
            figure = re.fullmatch(r"([\d,]+)( Elected)?", votes)
            if figure is None:
                raise ValueError(f"{url}: no votes in {votes}")
            candidates.append(
                {
                    "printed": printed,
                    "name": f"{POST_NOMINALS.sub('', surname.group(2))} {display(surname.group(1))}",
                    "description": description,
                    "votes": count(figure.group(1)),
                    "elected": figure.group(2) is not None,
                }
            )
        figures = {
            label: count(value)
            for label, value in re.findall(
                r"<li>(Number of ballot papers rejected|Ballot papers issued|Electorate): ([\d,]+)</li>", body
            )
        }
        reads.append(
            {
                "lad": lad,
                "url": url,
                "page": None,
                "style": None,
                "parish": parish,
                "ward": ward.removesuffix(" ward") or None,
                "places": sum(1 for candidate in candidates if candidate["elected"]),
                "electorate": figures["Electorate"],
                "ballots": figures["Ballot papers issued"],
                "rejected": figures["Number of ballot papers rejected"],
                "total": None,
                "remark": None,
                "candidates": candidates,
            }
        )
    return reads


def rugby(lad: str, council: dict) -> list[dict]:
    url = council["pages"][0]
    page = Path(raw.cached(SOURCE, url).path).read_text(encoding="utf-8")
    reads = []
    for heading, body in re.findall(
        r'<h2 class="section_title">(.*?) Parish Council</h2>\s*<div class="section_content">(.*?)</article>',
        page,
        re.DOTALL,
    ):
        if "<table" not in body:
            continue
        candidates = []
        for row in re.findall(r"<tr>\s*<td>(.*?)</tr>", body, re.DOTALL):
            printed, description, votes = (
                text(cell) for cell in re.findall(r"(.*?)</td>\s*(?:<td>|$)", row, re.DOTALL)
            )
            figure = re.fullmatch(r"([\d,]+)( elected)?", votes)
            if figure is None:
                raise ValueError(f"{url}: no votes in {votes}")
            candidates.append(
                {
                    "printed": printed,
                    "name": surname_first(printed),
                    "description": description,
                    "votes": count(figure.group(1)),
                    "elected": figure.group(2) is not None,
                }
            )
        figures = {
            label: count(value)
            for label, value in re.findall(r"(Vacant seats|Electorate|Ballot papers issued): ([\d,]+)", text(body))
        }
        reads.append(
            {
                "lad": lad,
                "url": url,
                "page": None,
                "style": "Parish",
                "parish": text(heading),
                "ward": None,
                "places": figures["Vacant seats"],
                "electorate": figures["Electorate"],
                "ballots": figures["Ballot papers issued"],
                "rejected": None,
                "total": None,
                "remark": None,
                "candidates": candidates,
            }
        )
    return reads


def person(printed: str, order: str) -> str:
    if order == "comma":
        return surname_first(printed)
    if order == "upper-first":
        found = re.fullmatch(r"((?:[A-Z][A-Z'\u2019-]+\s+)*[A-Z][A-Z'\u2019-]+)\s+(\S.*)", printed)
        if found is None:
            raise ValueError(f"no surname in {printed}")
        return f"{found.group(2)} {display(found.group(1))}"
    found = re.fullmatch(r"(\S.*?)\s+((?:[A-Z][A-Z'\u2019-]+\s?)+)", printed)
    if found is None:
        raise ValueError(f"no surname in {printed}")
    return f"{found.group(1)} {display(found.group(2).strip())}"


def declarations(lad: str, council: dict) -> list[dict]:
    order = council["order"]
    reads: list[dict] = []
    for url in council["polls"] if "polls" in council else council["files"]:
        for number, (page, _) in enumerate(pdf_pages(url), start=1):
            lines = page.split("\n")
            if not any("DECLARATION OF RESULT OF POLL" in line for line in lines):
                continue
            start = next(i for i, line in enumerate(lines) if "Election of" in line)
            owner = next(line.strip() for line in reversed(lines[:start]) if line.strip())
            heading = []
            for line in lines[start + 1 :]:
                if not line.strip() or line.strip().startswith("on "):
                    if heading:
                        break
                    continue
                if line.strip() != "for":
                    heading.append(line.strip())
            area = " ".join(heading)
            if area in council.get("areas", {}):
                parish, ward = council["areas"][area]
            else:
                body = re.fullmatch(r"(.+) (?:Town|Parish) Council", owner)
                if body is None:
                    raise ValueError(f"{url} page {number}: no area for {owner} / {area}")
                parish, ward = body.group(1), area.removesuffix(" Ward")
            parish, ward = parish.replace("\u2019", "'"), ward and ward.replace("\u2019", "'")
            top = next(i for i, line in enumerate(lines) if "Votes*" in line)
            end = next(i for i, line in enumerate(lines) if line.startswith("* If elected"))
            candidates: list[dict] = []
            for line in lines[top + 1 : end]:
                if not line.strip():
                    continue
                cells = re.split(r"\s{2,}", line.strip())
                column_zero = not line[0].isspace()
                rest = "  ".join(cells[1:] if column_zero else cells)
                if column_zero:
                    if candidates and (cells[0][0].islower() or cells[0].lower().startswith("commonly known as")):
                        candidates[-1]["printed"] += f" {cells[0]}"
                    else:
                        printed = cells[0].replace("`", "'")
                        candidates.append({"printed": printed, "description": "", "votes": None, "elected": False})
                if rest:
                    figure = re.fullmatch(r"(?:(.*?)\s{2,})?([\d,]+)(?:\s+(E|Elected))?", rest)
                    if figure is None or not candidates:
                        raise ValueError(f"{url} page {number}: cannot read {line.strip()!r}")
                    candidates[-1] |= {
                        "description": figure.group(1) or "",
                        "votes": count(figure.group(2)),
                        "elected": figure.group(3) is not None,
                    }
            declared = re.search(r"declare that(.*?)(?:is|are) duly elected", page, re.DOTALL)
            marked = any(c["elected"] for c in candidates)
            for candidate in candidates:
                found = re.fullmatch(r"(.*?)\s*\(?commonly known as:?\s*(.*?)\)?", candidate["printed"], re.IGNORECASE)
                main, known = found.groups() if found else (candidate["printed"], None)
                if not marked and declared and main in " ".join(declared.group(1).split()):
                    candidate["elected"] = True
                candidate["name"] = person((known or main).replace("\u2019", "'"), order)
                if known:
                    candidate["printed"] = f"{main} commonly known as {known}"
            figures = re.search(
                r"Vacant Seats:\s*(\d+)\s+Electorate:\s*([\d,]+)\s+Ballot Papers Issued:\s*([\d,]+)", page
            )
            rejected = re.search(r"\n\s+Total\s+(\d+)\s*\n", page)
            places = int(figures.group(1)) if figures else sum(c["elected"] for c in candidates)
            if places != sum(c["elected"] for c in candidates):
                raise ValueError(f"{url} page {number}: {places} seats, other elected count")
            reads.append(
                {
                    "lad": lad,
                    "url": url,
                    "page": number,
                    "style": None,
                    "parish": parish,
                    "ward": ward,
                    "places": places,
                    "electorate": count(figures.group(2)) if figures else None,
                    "ballots": count(figures.group(3)) if figures else None,
                    "rejected": int(rejected.group(1)) if rejected else None,
                    "total": None,
                    "remark": None,
                    "candidates": candidates,
                }
            )
    return reads


def chorley(lad: str, council: dict) -> list[dict]:
    reads: list[dict] = []
    for url in council["files"]:
        for number, (page, _) in enumerate(pdf_pages(url), start=1):
            heading = re.search(
                r"Election of Parish Councillors to(?: Parish Council)?\s*\n\s*(.+?)\s*\n\s*ON Thursday", page
            )
            if heading is None:
                continue
            parish, ward = council["areas"][heading.group(1)]
            body = page.split("Names of persons elected")[1].split("If a candidate")[0].split("\n")[1:]
            candidates: list[dict] = []
            for line in body:
                if not line[:3].strip():
                    continue
                cell = re.split(r"\s{2,}", line.strip())[0]
                if cell.startswith("(Known as"):
                    candidates[-1]["known"] = cell.removeprefix("(Known as ").removesuffix(")")
                elif line[0] == " " and line[1] != " ":
                    candidates.append({"printed": cell, "known": None})
            if not candidates:
                raise ValueError(f"{url} page {number}: no names")
            reads.append(
                {
                    "lad": lad,
                    "url": url,
                    "page": number,
                    "style": "Parish",
                    "parish": parish,
                    "ward": ward,
                    "places": None,
                    "electorate": None,
                    "ballots": None,
                    "rejected": None,
                    "total": None,
                    "remark": None,
                    "candidates": [
                        {
                            "printed": (
                                f"{c['printed']} commonly known as {c['known']}" if c["known"] else c["printed"]
                            ),
                            "name": c["known"] or surname_first(c["printed"]),
                            "description": "",
                            "votes": None,
                            "elected": True,
                        }
                        for c in candidates
                    ],
                }
            )
    return reads


def colchester(lad: str, council: dict) -> list[dict]:
    reads: list[dict] = []
    for url in council["files"]:
        parish = ward = None
        for number, (page, words) in enumerate(pdf_pages(url), start=1):
            heading = re.search(r"Election of Parish Councillors to\s*\n\s*(.+?) Parish Council\s*\n", page)
            if heading:
                parish, ward = " ".join(heading.group(1).split()), None
            elif parish is None:
                continue
            bottom = min((y for x, y, word in words if word == "Dated"), default=9999.0)
            read: dict | None = None
            pending: str | None = None
            started = False
            for line in rows_of(words, 0, bottom):
                label = " ".join(word for _, _, word in line)
                name = joined(line, 0, 215)
                if label.startswith("Name of Candidate"):
                    started = True
                    ward = pending or ward
                    pending = None
                    read = next(
                        (r for r in reads if r["parish"] == parish and r["ward"] == ward and r["url"] == url), None
                    )
                    if read is None:
                        read = {
                            "lad": lad,
                            "url": url,
                            "page": number,
                            "style": "Parish",
                            "parish": parish,
                            "ward": ward,
                            "places": None,
                            "electorate": None,
                            "ballots": None,
                            "rejected": None,
                            "total": None,
                            "remark": None,
                            "candidates": [],
                        }
                        reads.append(read)
                    continue
                if not name and label.endswith(" Ward"):
                    pending = label.removesuffix(" Ward")
                    started = False
                    continue
                if not started or read is None:
                    continue
                candidates = read["candidates"]
                description = joined(line, 380, 999)
                if re.fullmatch(r"[A-Z][A-Z'\u2019 -]*", name):
                    candidates.append({"surname": name, "forenames": "", "description": ""})
                elif name:
                    candidates[-1]["forenames"] = f"{candidates[-1]['forenames']} {name}".strip()
                if description:
                    candidates[-1]["description"] = f"{candidates[-1]['description']} {description}".strip()
    for read in reads:
        for candidate in read["candidates"]:
            candidate["printed"] = f"{candidate.pop('surname')}, {candidate.pop('forenames')}"
            candidate["name"] = surname_first(candidate["printed"])
            candidate |= {"votes": None, "elected": True}
    return reads


def colchester_poll(lad: str, council: dict) -> list[dict]:
    url = council["pages"][0]
    page = Path(raw.cached(SOURCE, url).path).read_text(encoding="utf-8")
    body = page.split("<tbody>")[1].split("</tbody>")[0]
    candidates = []
    for row in re.findall(r"<tr>(.*?)</tr>", body, re.DOTALL):
        name, description, votes = (text(cell) for cell in re.findall(r"<td>(.*?)</td>", row, re.DOTALL))
        found = re.fullmatch(r"(.*?) commonly known as (.*)", name)
        printed, known = found.groups() if found else (name, None)
        figure = re.fullmatch(r"([\d,]+)( Elected)?", votes)
        if figure is None:
            raise ValueError(f"{url}: no votes in {votes}")
        candidates.append(
            {
                "printed": f"{printed} commonly known as {known}" if known else printed,
                "name": surname_first(known or printed),
                "description": description,
                "votes": count(figure.group(1)),
                "elected": figure.group(2) is not None,
            }
        )
    figures = dict(re.findall(r"(Vacant seats|Electorate|Ballot papers issued): ([\d,]+)", text(page)))
    rejected = re.search(r"<strong>Total</strong></td>\s*<td>(\d+)", page)
    return [
        {
            "lad": lad,
            "url": url,
            "page": None,
            "style": "Parish",
            "parish": council["poll"],
            "ward": None,
            "places": count(figures["Vacant seats"]),
            "electorate": count(figures["Electorate"]),
            "ballots": count(figures["Ballot papers issued"]),
            "rejected": int(rejected.group(1)) if rejected else None,
            "total": None,
            "remark": None,
            "candidates": candidates,
        }
    ]


READERS = {
    "moderngov": moderngov,
    "notices": notices,
    "swindon": swindon,
    "rugby": rugby,
    "declarations": declarations,
    "chorley": chorley,
    "colchester": colchester,
    "colchester_poll": colchester_poll,
}


def parse() -> None:
    with connect() as conn:
        parishes = conn.execute(
            """select pc.code, n.text, pe.value, c.code
               from area a join area_code c on c.area_id = a.id
               join area_parent p on p.area_id = a.id join area_code pc on pc.area_id = p.parent_id
               join name n on n.entity_id = a.id and n.lang = 'en'
               join polity_territory pt on pt.area_id = a.id and pt.valid @> %s::date
               join external_id pe on pe.entity_id = pt.polity_id and pe.scheme = 'gb.polity'
               where a.scheme_code = 'gb.parish' and pc.code = any(%s)""",
            [date(2026, 5, 7), sorted(COUNCILS)],
        ).fetchall()
    known: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for lad, name, key, code in parishes:
        known.setdefault((lad, fold(name)), set()).add((key, code))
    rows = []
    misses: list[str] = []
    for lad, council in COUNCILS.items():
        for read in (read for reader in council["reader"].split("+") for read in READERS[reader](lad, council)):
            if (lad, read["parish"]) in GROUPED:
                continue
            found = known.get((lad, fold(ALIASES.get((lad, read["parish"]), read["parish"]))), set())
            if len(found) != 1:
                misses.append(f"{lad} {read['parish']}: {len(found)} parishes")
                continue
            key, code = next(iter(found))
            for position, candidate in enumerate(read.pop("candidates"), start=1):
                rows.append({**read, "key": key, "code": code, "day": council["day"], "number": position, **candidate})
    if misses:
        raise ValueError("; ".join(misses))
    frame = pl.DataFrame(rows, infer_schema_length=None)
    contests = frame.select("key", "ward").unique().height
    uncontested = frame.filter(pl.col("votes").is_null()).select("key", "ward").unique().height
    print(
        f"{frame['key'].n_unique()} councils, {contests} contests ({uncontested} uncontested), "
        f"{frame.height} candidates",
        file=sys.stderr,
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "candidates.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="English parish and town council elections, ward by ward, from the principal councils' published "
            "results: polls with votes, and wards filled unopposed as tacit sets",
            polity="GB",
        )

        def name(entity: UUID, value: str) -> None:
            cs.add("name", entity_id=entity, text=value, lang="en", kind="official", is_preferred=True)

        systems = {code: cs.key("gb.electoral_system", code) for code in ("fptp", "block")}
        if any(system in cs.minted for system in systems.values()):
            raise ValueError("publish gb.parliament first")
        gb = cs.polity("GB")
        if conn.execute("select 1 from scheme where code = %s", [WARDS]).fetchone() is None:
            cs.add(
                "scheme", code=WARDS, polity_id=gb, kind="electoral_district", rank=5, noun="parish or community ward"
            )
        sources: dict[str, UUID] = {}
        for lad, council in COUNCILS.items():
            if lad not in set(frame["lad"]):
                continue
            sources[lad] = cs.source(council["publisher"], council["title"])
            cs.add(
                "source",
                id=sources[lad],
                kind="official_body",
                publisher=council["publisher"],
                title=council["title"],
                url=council["url"],
                license=COUNCIL,
            )
            for url in sorted(set(frame.filter(pl.col("lad") == lad)["url"])):
                cs.artifact(raw.cached(SOURCE, url), sources[lad])
        keys = sorted(set(frame["key"]))
        paths = dict(
            conn.execute(
                """select e.value, s.path from external_id e join slug s on s.entity_id = e.entity_id and s.is_current
                   where e.scheme = 'gb.polity' and e.value = any(%s)""",
                [keys],
            ).fetchall()
        )
        bodies = dict(
            conn.execute(
                """select e.value, n.text from external_id e join body b on b.id = e.entity_id
                   join name n on n.entity_id = e.entity_id and n.is_preferred
                   where e.scheme = 'gb.body' and e.value = any(%s)""",
                [keys],
            ).fetchall()
        )
        if missing := sorted(set(keys) - bodies.keys()):
            raise ValueError(f"no council body for {', '.join(missing)}; emit gb.parish first")
        parties: dict[str, UUID] = {}

        def party_of(description: str) -> UUID | None:
            target = PARTIES.get(description)
            if target is None or target == "independent":
                return None
            if target not in parties:
                parties[target] = cs.key("wikidata", target)
                if parties[target] in cs.minted:
                    raise ValueError(f"{target} is not a party in the archive")
            return parties[target]

        people: dict[str, UUID] = {}
        elections: set[str] = set()
        for (key, ward), lines in frame.group_by("key", "ward", maintain_order=True):
            assert isinstance(key, str)
            first = lines.row(0, named=True)
            day = first["day"]
            places = first["places"]
            tacit = first["votes"] is None
            polity = cs.key("gb.polity", key)
            parish_area = cs.area("gb.parish", first["code"])
            election_key = f"{day}:pc:{key}"
            election = cs.key("gb.election", election_key)
            election_path = f"{paths[key]}/{day}-local"
            if election_key not in elections:
                elections.add(election_key)
                cs.add(
                    "election", id=election, polity_id=polity, kind="local", date_from=day, date_to=day, status="held"
                )
                name(election, f"{day.year} {bodies[key]} election")
                cs.add("slug", path=election_path, entity_id=election)
                cs.add("note", entity_id=election, kind="context", body_md=CONTEXT)
            if ward is None:
                scheme, code, area = "gb.parish", first["code"], parish_area
            else:
                scheme, code = WARDS, f"{key}:{slug(ward)}"
                area = cs.area(WARDS, code)
                if area in cs.minted:
                    cs.add("area", id=area, scheme_code=WARDS)
                    cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=None)
                    cs.add("area_parent", area_id=area, parent_id=parish_area, valid_from=None, valid_to=None)
                name(area, ward)
            unit = cs.unit(scheme, code)
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=polity,
                    scheme_code=scheme,
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
                name(unit, ward or first["parish"])
            contest_key = f"{election_key}:{slug(ward) if ward else 'council'}"
            contest = cs.key("gb.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=cs.key("gb.body", key),
                district_area_id=area,
                magnitude=places,
                round_number=1,
                electoral_system_id=systems["fptp" if places == 1 else "block"],
                default_result_kind="tacit" if tacit else "counted",
            )
            name(contest, f"{bodies[key]}, {ward} ward, {day.year}" if ward else f"{bodies[key]}, {day.year}")
            cs.add("slug", path=f"{election_path}/{slug(ward) if ward else 'council'}", entity_id=contest)
            source = sources[first["lad"]]
            cs.add("citation", entity_id=contest, source_id=source)
            locator: dict[str, object] = {"url": first["url"]}
            if first["page"] is not None:
                locator["page"] = first["page"]
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="tacit" if tacit else "counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("citation", entity_id=result_set, source_id=source, locator=locator)
            publisher = COUNCILS[first["lad"]]["publisher"]
            if first["remark"]:
                cs.add(
                    "note", entity_id=result_set, kind="context", body_md=f"The declaration reads: “{first['remark']}”"
                )
            if tacit:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=UNSTATED.format(publisher=publisher))
            for field, measure in MEASURES.items():
                if first[field] is not None:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=first[field],
                    )
            for line in lines.iter_rows(named=True):
                option_key = f"{contest_key}:{line['number']}"
                person_key = f"{key}:{slug(line['name'])}"
                if person_key not in people:
                    people[person_key] = cs.key(f"{SOURCE}.person", person_key)
                    cs.add("person", id=people[person_key])
                    name(people[person_key], line["name"])
                party = party_of(line["description"])
                if line["description"] and line["description"] not in PARTIES:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md=f"{line['name']} stood as “{line['description']}”; no party is linked.",
                    )
                option = cs.key(f"{SOURCE}.option", option_key)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party,
                    ballot_label=line["name"],
                )
                cs.add(
                    "candidacy",
                    id=cs.key(f"{SOURCE}.candidacy", option_key),
                    person_id=people[person_key],
                    option_id=option,
                    role="head",
                    ballot_name=line["printed"],
                    member_of_party_id=party,
                    is_independent=PARTIES.get(line["description"]) == "independent",
                )
                if line["votes"] is not None:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=VOTES,
                        count_round=0,
                        flags=0,
                        value=line["votes"],
                    )
                if line["elected"]:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        return cs.write()
