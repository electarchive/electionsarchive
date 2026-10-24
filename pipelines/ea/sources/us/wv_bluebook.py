import subprocess
from datetime import UTC, datetime

from ea import raw

SOURCE = "us.wv_bluebook"
URL = "https://www.wvlegislature.gov/legisdocs/publications/bluebook/WVS_Bluebook_{}.pdf"
EDITIONS = range(1917, 1993, 2)


def fetch() -> None:
    for edition in EDITIONS:
        url = URL.format(edition)
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            try:
                body = subprocess.run(
                    ["curl", "-sL", "-m", "1500", "-A", "Mozilla/5.0", url], capture_output=True, check=True
                ).stdout
            except subprocess.CalledProcessError as error:
                print(edition, "failed", error.returncode)
                continue
            if body[:4] != b"%PDF":
                print(edition, "is not a PDF")
                continue
            raw.store(SOURCE, url, body, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf")
            print(edition, len(body))
