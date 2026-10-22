import json
from collections.abc import Iterator, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from ea import portals, raw
from ea.sources.us import enr_clarity

SOURCE = "us.enr_ev"
VERSION = "1"
PLATFORM = "enhanced_voting"
HOST = "app.enhancedvoting.com"
API = f"https://{HOST}/results/public/api"
CDN = f"https://{HOST}/cdn/results/"


def fetch() -> None:
    _, index = raw._index(SOURCE)
    cutoff = date.today() - timedelta(days=14)
    settled = date.today() - timedelta(days=120)
    listed = enr_clarity.tenants(PLATFORM)
    for fips in sorted({row["county_fips"][:2] for row in listed}):
        for layer in enr_clarity.LAYERS:
            raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips=fips, layer=layer), suffix=".zip")
    for row in listed:
        tenant = row["tenant"].rstrip("/").rsplit("/", 1)[1]
        status, body, _ = portals.get(f"{API}/jurisdictions/{tenant}")
        if status != 200:
            print(f"{tenant}: {status}")
            continue
        fetched = skipped = 0
        for election in json.loads(body)["elections"]:
            link = f"{API}/elections/{tenant}/{election['publicElectionId']}/data"
            day = date.fromisoformat(election["electionDate"][:10])
            if day > cutoff:
                skipped += 1
                continue
            if link in index:
                continue
            status, body, _ = portals.get(link)
            if status != 200:
                print(f"{link}: {status}")
                continue
            data = json.loads(body)
            export = data["jurisdiction"].get("mediaExportPath")
            if not data["election"]["isOfficialResults"] and day > settled or not export:
                print(f"{link}: not official" if export else f"{link}: no export")
                skipped += 1
                continue
            status, content, _ = portals.get(f"{CDN}{export}")
            if status != 200:
                print(f"{CDN}{export}: {status}")
                continue
            now = datetime.now(UTC)
            raw.store(
                SOURCE, f"{CDN}{export}", content, suffix=".json", retrieved_at=now, media_type="application/json"
            )
            raw.store(SOURCE, link, body, suffix=".json", retrieved_at=now, media_type="application/json")
            fetched += 1
        if HOST in portals.stopped:
            raise RuntimeError(f"{HOST} asked us to stop")
        print(f"{row['state']} {row['county']}: {fetched} fetched, {skipped} too recent or unofficial", flush=True)


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    index = raw._index(SOURCE)[1]
    for tenant in registry:
        slug = tenant.rstrip("/").rsplit("/", 1)[1]
        for link in sorted(key for key in index if key.startswith(f"{API}/elections/{slug}/")):
            data = json.loads(raw.cached(SOURCE, link).path.read_bytes())
            export = json.loads(raw.cached(SOURCE, f"{CDN}{data['jurisdiction']['mediaExportPath']}").path.read_bytes())
            yield {
                "tenant": tenant,
                "election": link.rsplit("/", 2)[1],
                "day": date.fromisoformat(export["electionDate"][:10]),
                "title": export["electionName"] or "",
                "official": data["election"]["isOfficialResults"],
                "primary": bool(data["election"].get("isPrimary")),
                "contests": [
                    {
                        "key": item["id"],
                        "text": item["name"] or "",
                        "vote_for": int(item["voteFor"] or 1),
                        "choices": [
                            {
                                "text": option["name"] or "",
                                "party": (option["politicalParty"] or "").removesuffix(" Party") or None,
                                "total": str(option["voteCount"] or 0),
                                "places": [
                                    (place["name"] or "", str(place["voteCount"] or 0))
                                    for place in option["precinctResults"] or []
                                ],
                            }
                            for option in item["ballotOptions"]
                        ],
                    }
                    for item in export["results"]["ballotItems"]
                ],
            }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: f"/{tenant.rstrip('/').rsplit('/', 1)[1]}/" in link)
