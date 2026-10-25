import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from ea import raw
from ea.sources.us import enr_clarity

SOURCE = "us.tx_elpaso"
VERSION = "1"
PLATFORM = "tx_elpaso"
FIPS = "48"
DISTRICT = re.compile(
    r"(?i)(?:\brep(?:resentative)?\b.*?\b(?:dist(?:rict)?|rep)\s*|city of (?:ep|el paso), dist\s*)(\d+)"
)
MAYOR = re.compile(r"(?i)\bmayor\b.*\bcity of (?:ep|el paso)\b|\bcity of (?:ep|el paso)\b.*\bmayor\b")


def fetch() -> None:
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips=FIPS, layer=layer), suffix=".zip")


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    for election in enr_clarity._read(registry):
        nodes = []
        for node in election["contests"]:
            district = DISTRICT.search(node["text"])
            if not re.search(r"(?i)city of (?:ep|el paso)", node["text"]) or (
                district is None and MAYOR.search(node["text"]) is None
            ):
                continue
            label = f"District {district[1]}" if district else "Mayor"
            nodes.append({**node, "text": f"City of El Paso City Council, {label}", "key": str(len(nodes))})
        if nodes:
            yield {**election, "contests": nodes}


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(
        SOURCE, PLATFORM, lambda link, tenant: link.startswith(tenant) and link.endswith("detailxml.zip")
    )
