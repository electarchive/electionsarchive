import json

from ea import raw

SOURCE = "es.cat"
API = "https://resultats.dadeselectorals.gencat.cat/webelec/api"


def elections() -> list[dict[str, object]]:
    listing = raw.fetch(SOURCE, f"{API}/tipusProces/A/proces", suffix=".json")
    return sorted(json.loads(listing.path.read_bytes()), key=lambda election: str(election["codi"]), reverse=True)


def fetch() -> None:
    for election in elections():
        code = election["codi"]
        raw.fetch(SOURCE, f"{API}/proces/{code}/nivellTerritorial", suffix=".json")
        for level in ("CA", "PR", "MU", "ME"):
            raw.fetch(SOURCE, f"{API}/proces/{code}/nivellTerritorial/{level}/exportacio", suffix=".zip")
        if isinstance(url := election.get("urlElectes"), str):
            raw.fetch(SOURCE, url, suffix=".pdf")
