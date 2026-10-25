import re
import subprocess
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

from ea import raw
from ea.sources.us import enr_clarity, mi_detroit

SOURCE = "us.mi_wayne"
PLATFORM = "mi_wayne"
LISTING = "https://www.waynecountymi.gov/Government/Elected-Officials/Clerk/Elections/Election-Results-Candidates"
FILES = "https://www.waynecountymi.gov/files/assets/mainsite/v/1/clerk/documents/elections/election-results"
BROWSER = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/155.0.0.0 Safari/537.36"
)
ELECTIONS = {
    "2022-august-2/official81622": (date(2022, 8, 2), "August 2, 2022 - Primary Election", True),
    "2022-november-8/official_summary_11822": (date(2022, 11, 8), "November 8, 2022 - General Election", False),
    "2024-august-6/officialaug24_sum": (date(2024, 8, 6), "August 6, 2024 - Primary Election", True),
    "2024-november-5/official_gen_2024": (date(2024, 11, 5), "November 5, 2024 - General Election", False),
}


def fetch() -> None:
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips="26", layer=layer), suffix=".zip")
    raw.USER_AGENT = BROWSER
    for path in ELECTIONS:
        raw.fetch(SOURCE, f"{FILES}/{path}.pdf", suffix=".pdf")


def _read(registry: object) -> Iterator[dict[str, Any]]:
    for path, (day, title, primary) in ELECTIONS.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, f"{FILES}/{path}.pdf").path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        yield {
            "tenant": LISTING,
            "election": path,
            "day": day,
            "title": title,
            "official": True,
            "primary": primary,
            "contests": [
                {
                    **contest,
                    "text": re.sub(
                        r"\s*\((D|R)\)$", lambda m: " Democratic" if m[1] == "D" else " Republican", contest["text"]
                    ),
                }
                for contest in mi_detroit.contests(text, re.compile(r"^County Commissioner \d+\w\w District"))
            ],
        }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: link.endswith(".pdf"))
