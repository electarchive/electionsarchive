import json
import re
import subprocess
import zipfile
from datetime import UTC, datetime
from uuid import UUID

import polars as pl
from psycopg import Connection

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import ceda

SOURCE = "us.civmint"
PLACES = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2020_Gazetteer/2020_Gaz_place_national.zip"
COUSUBS = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2020_Gazetteer/2020_Gaz_cousubs_national.zip"
DIRECTORY = "https://educationdata.urban.org/api/v1/school-districts/ccd/directory/2022/?fips={fips}"
Found = tuple[UUID, UUID, str, str, str]


def cached(url: str, suffix: str) -> bytes:
    try:
        return raw.cached(SOURCE, url).path.read_bytes()
    except LookupError:
        content = subprocess.run(["curl", "-sSL", "-m", "180", "-A", "Mozilla/5.0", url], capture_output=True, check=True).stdout
        raw.store(SOURCE, url, content, suffix=suffix, retrieved_at=datetime.now(UTC), media_type="application/octet-stream")
        return content


def gazetteer(url: str, state: str) -> pl.DataFrame:
    cached(url, ".zip")
    with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
        frame = pl.read_csv(archive.read(archive.namelist()[0]), separator="\t", infer_schema=False)
    return frame.rename(lambda column: column.strip()).filter(pl.col("USPS") == state)


def town_core(text: str) -> str:
    return ceda._core(re.sub(r"(?i)\bcharter\b|^township of (.+)$", r"\1", text))


def school_core(text: str) -> str:
    return " ".join(word for word in ceda._core(text).split() if word not in {"public", "community"})


class Civil:
    def __init__(self, conn: Connection, cs: Changeset, fips: str, state: str) -> None:
        self.cs = cs
        self.fips = fips
        self.state = state
        self.found: dict[str, Found] = {}
        self.places: dict[str, list[str]] = {}
        self.towns: dict[tuple[str, str], str] = {}
        self.schools: dict[str, list[str]] = {}
        self.slugs: dict[str, UUID] = dict(conn.execute("select path, entity_id from slug where path like %s", [f"us/{state.lower()}/%"]).fetchall())
        for scheme, code, polity, area, text, path in conn.execute(
            """select a.scheme_code, c.code, t.polity_id, a.id, n.text, s.path from area a
               join area_code c on c.area_id = a.id
               join polity_territory t on t.area_id = a.id
               join polity p on p.id = t.polity_id and p.kind in ('county', 'municipality', 'school_district')
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = p.id and s.is_current
               where a.scheme_code in ('us.county', 'us.place', 'us.town', 'us.school_district') and left(c.code, 2) = %s""",
            [fips],
        ).fetchall():
            key = f"{scheme}:{code}"
            self.found[key] = (polity, area, text, path, code)
            if scheme == "us.town":
                self.towns[(code[:5], town_core(text))] = key
            elif scheme == "us.place":
                self.places.setdefault(ceda._city(text), []).append(key)
            elif scheme == "us.school_district":
                self.schools.setdefault(ceda._core(text), []).append(key)
        self.place_specs: dict[str, list[tuple[str, str]]] = {}
        for geoid, full in sorted(gazetteer(PLACES, state).select("GEOID", "NAME").iter_rows(), key=lambda row: row[1].endswith("CDP")):
            label = re.sub(r"^Village of | Village$", "", re.sub(r" (city|village|town|borough|CDP)$", "", full))
            if full.endswith("CDP") and ceda._city(label) in self.place_specs:
                continue
            self.place_specs.setdefault(ceda._city(label), []).append((geoid, label))
        self.town_specs: dict[str, list[tuple[str, str]]] = {}
        for geoid, full in gazetteer(COUSUBS, state).select("GEOID", "NAME").iter_rows():
            if full.lower().endswith("township"):
                self.town_specs.setdefault(geoid[:5] + town_core(full), []).append((geoid, full.title()))
        directory = json.loads(cached(DIRECTORY.format(fips=int(fips)), ".json"))["results"]
        self.school_specs = [(entry["leaid"], entry["lea_name"]) for entry in directory]

    def place(self, name: str) -> str | None:
        options = self.places.get(ceda._city(name), [])
        if len(options) == 1:
            return options[0]
        specs = self.place_specs.get(ceda._city(name), [])
        return self.mint("us.place", specs[0][0], specs[0][1]) if not options and len(specs) == 1 else None

    def town(self, name: str, county: str) -> str | None:
        bare = town_core(name if "ownship" in name else f"{name} Township")
        key = self.towns.get((county, bare))
        if key is not None:
            return key
        specs = self.town_specs.get(county + bare, [])
        return self.mint("us.town", specs[0][0], specs[0][1]) if len(specs) == 1 else None

    def school(self, name: str) -> str | None:
        options = self.schools.get(ceda._core(name), [])
        if len(options) == 1:
            return options[0]
        match = re.search(r"(\d+\w*)$", name)
        if match is None:
            named = [(leaid, lea) for leaid, lea in self.school_specs if school_core(lea) == school_core(name)]
            if len(named) != 1:
                return None
            key = f"us.school_district:{named[0][0]}"
            return key if key in self.found else self.mint("us.school_district", named[0][0], named[0][1])
        first = name.split()[0].lower()
        specs = [
            (leaid, lea)
            for leaid, lea in self.school_specs
            if match is not None and re.search(rf"\b{match[1]}$", lea) and lea.split()[0].lower() == first
        ]
        if not specs and match is not None:
            specs = [(leaid, lea) for leaid, lea in self.school_specs if re.fullmatch(rf"[A-Z]+ {match[1]}", lea)]
        if len(specs) != 1:
            return None
        key = f"us.school_district:{specs[0][0]}"
        return key if key in self.found else self.mint("us.school_district", specs[0][0], name)

    def mint(self, scheme: str, geoid: str, text: str) -> str | None:
        cs = self.cs
        key = f"{scheme}:{geoid}"
        if key in self.found:
            return key
        polity_id = cs.key("us.polity", f"{'school' if scheme == 'us.school_district' else scheme.removeprefix('us.')}:{geoid}")
        path = f"us/{self.state.lower()}/{slug(text)}"
        if path in self.slugs:
            path = f"{path}-{geoid[:5]}" if scheme == "us.town" else f"{path}-{scheme.removeprefix('us.')}"
        if path in self.slugs:
            print("slug taken:", path)
            return None
        self.slugs[path] = polity_id
        area_id = cs.area(scheme, geoid)
        state_polity = cs.polity(f"US-{self.state}")
        cs.add("polity", id=polity_id, kind="school_district" if scheme == "us.school_district" else "municipality", iso_code=None)
        cs.add("name", entity_id=polity_id, text=text, lang="en", kind="official", is_preferred=True)
        cs.add("polity_parent", polity_id=polity_id, parent_id=state_polity)
        cs.add("slug", path=path, entity_id=polity_id)
        cs.add("area", id=area_id, scheme_code=scheme)
        cs.add("area_code", area_id=area_id, code=geoid, valid_from=None)
        cs.add("area_parent", area_id=area_id, parent_id=cs.area("us.county", geoid[:5]) if scheme == "us.town" else cs.area("us.state", self.fips))
        cs.add("name", entity_id=area_id, text=f"{text}, {self.state}", lang="en", kind="official", is_preferred=True)
        cs.add("polity_territory", polity_id=polity_id, area_id=area_id)
        cs.add(
            "reporting_unit",
            id=cs.unit(scheme, geoid),
            polity_id=polity_id,
            scheme_code=scheme,
            code=geoid,
            area_relation="equals",
            area_id=area_id,
            kind="standard",
            valid_from=None,
        )
        self.found[key] = (polity_id, area_id, text, path, geoid)
        return key
