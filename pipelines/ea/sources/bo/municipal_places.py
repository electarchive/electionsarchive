"""Bolivia's departments and municipalities, matched across the Atlas tables, the 1999 polling-table file, and the
boundary shapes."""

import csv
import difflib
import io
import json
import re
from dataclasses import dataclass, field

import shapely
import shapely.geometry

from ea import raw
from ea.sources.bo.municipal_atlas import DEPARTMENTS, fold

DEPARTMENT_CODES = dict(zip(("01", "02", "03", "04", "05", "06", "07", "08", "09"), DEPARTMENTS, strict=True))
CODE_OF = {name: code for code, name in DEPARTMENT_CODES.items()}
CSV_DEPARTMENTS = {
    "CHUQUISACA": "Chuquisaca",
    "LA PAZ": "La Paz",
    "COCHABAMBA": "Cochabamba",
    "ORURO": "Oruro",
    "POTOSI": "Potosí",
    "TARIJA": "Tarija",
    "SANTA CRUZ": "Santa Cruz",
    "BENI": "Beni",
    "PANDO": "Pando",
}

# A name as the Atlas tables print it, and the name the 1999 file gives the same municipality.
ATLAS_TO_FILE = {
    ("Beni", "Guayaramerín"): "PUERTO GUAYARAMERÍN",
    ("Beni", "Rurrenabaque"): "PUERTO RURRENABAQUE",
    ("Chuquisaca", "Huacaya"): "VILLA DE HUACAYA",
    ("Chuquisaca", "Icla (R. Mujía)"): "ICLA (RICARDO MUJIA)",
    ("Chuquisaca", "Las Carretas"): "LAS CARRERAS (LAS CARRETAS)",
    ("Chuquisaca", "Tarvita (Villa Arias)"): "TARVITA (VILLA ORÍAS)",
    ("Chuquisaca", "Villa Abecia"): "CAMATAQUI (VILLA ABECIA)",
    ("Chuquisaca", "Villa Mojocoya"): "MOJOCOYA",
    ("Chuquisaca", "Yamparáez"): "YAMPAREZ",
    ("Chuquisaca", "Zudáñez"): "VILLA ZUDAÑEZ (TACOPAYA)",
    ("Cochabamba", "Cuchumuela (V. G.Villarroel)"): "CUCHUMUELA (VILLA GUALBERTO VILLARROEL)",
    ("Cochabamba", "Independencia"): "AYOPAYA (VILLA DE INDEPENDENCIA)",
    ("Cochabamba", "San Benito (Villa J. Q. Mendoza)"): "SAN BENITO (VILLA JOSÉ QUINTÍN MENDOZA)",
    ("Cochabamba", "Toco"): "TOKO",
    ("La Paz", "Charazani (Gral. J. J. Pérez)"): "GENERAL JUAN JOSÉ PÉREZ (CHARAZANI)",
    ("La Paz", "Chulumani"): "CHULUMANI (VILLA DE LA LIBERTAD)",
    ("La Paz", "Irupana"): "IRUPANA (VILLA DE LANZA)",
    ("La Paz", "Licoma (Villa Libertad)"): "LICOMA PAMPA",
    ("La Paz", "Sica Sica"): "SICA SICA (VILLA AROMA)",
    ("La Paz", "Tito Yupanki"): "TITO YUPANQUI",
    ("Oruro", "Poopó"): "POOPÓ (VILLA POOPÓ)",
    ("Oruro", "Santiago de Andamarca"): "ANDAMARCA (SANTIAGO DE ANDAMARCA)",
    ("Oruro", "Santiago de Huayllamarca"): "HUAYLLAMARCA (SANTIAGO DE HUAYLLAMARCA)",
    ("Oruro", "Villa Huanuni"): "HUANUNI",
    ("Oruro", "Yunguyo del Litoral"): "YUNGUYO DE LITORAL",
    ("Pando", "Gonzalo Moreno"): "PUERTO GONZALO MORENO",
    ("Pando", "Ingavi"): "INGAVI (HUMAITA)",
    ("Pando", "San Pedro (Conquista)"): "SAN PEDRO",
    ("Pando", "Santa Rosa del Abuná"): "SANTA ROSA DEL ABUN",
    ("Potosí", "Acasio"): "ACACIO",
    ("Potosí", "Belén de Urmiri"): "URMIRI (BELÉN DE ANDAMARCA)",
    ("Potosí", 'Caiza "D"'): "CAIZA",
    ("Potosí", 'Colcha"K" (Villa San Martín)'): "COLCHA K (VILLA MARTÍN)",
    ("Potosí", "Puna"): "PUNA (VILLA TALAVERA)",
    ("Potosí", "Sacaca"): "SACACA (VILLA DE SACACA)",
    ("Potosí", "San Antonio de Esmoraca"): "SAN ANTONIO DE ESMORUCO",
    ("Santa Cruz", "General Saavedra"): "GENERAL AGUSTÍN SAAVEDRA",
    ("Santa Cruz", "San Ignacio de Velasco"): "SAN IGNACIO (SAN IGNACIO DE VELASCO)",
    ("Santa Cruz", "San Miguel de Velasco"): "SAN MIGUEL (SAN MIGUEL DE VELASCO)",
    ("Tarija", "Uriondo"): "URIONDO (CONCEPCIÓN)",
}


# A municipality as the tables name it, and the boundary set's name for it, where the two differ by more than spelling.
SHAPE_NAMES = {
    ("Beni", "Rurrenabaque"): "Puerto Menor de Rurrenabaque",
    ("Chuquisaca", "Icla (R. Mujía)"): "Villa Ricardo Mugia - Icla",
    ("Chuquisaca", "San Pablo de Huacareta"): "Huacareta",
    ("Chuquisaca", "Villa Alcalá"): "Alcalá",
    ("Chuquisaca", "Villa Azurduy"): "Azurduy",
    ("La Paz", "Ancoraimes"): "Villa Ancoraimes",
    ("La Paz", "Guaqui"): "Puerto Mayor de Guaqui",
    ("La Paz", "Licoma (Villa Libertad)"): "Villa Libertad Licoma",
    ("La Paz", "Puerto Carabuco"): "Puerto Mayor de Carabuco",
    ("La Paz", "San Andres de Machaca"): "La (Marka) San Andrés de Mach",
    ("Oruro", "San Pedro de Totora"): "Totora",
    ("Oruro", "Soracachi"): "Paria",
    ("Pando", "El Sena"): "Sena",
    ("Pando", "Santa Rosa del Abuná"): "Santa Rosa",
    ("Potosí", "San Pablo de Lípez"): "San Pablo",
    ("Potosí", "San Pedro de Buena Vista"): "San Pedro",
    ("Potosí", "Vitichi"): "Vitiche",
    ("Santa Cruz", "La Bélgica"): "Colpa Bélgica",
    ("Santa Cruz", "Puerto Fernández"): "Fernández Alonso",
    ("Santa Cruz", "San José de Chiquitos"): "San José",
    ("Santa Cruz", "San Juan de Yapacaní"): "San Juan",
    ("Santa Cruz", "Santa Rosa del Sara"): "Santa Rosa",
    ("Tarija", "Villa San Lorenzo"): "San Lorenzo",
}


@dataclass
class Place:
    department: str
    name: str
    code: str | None  # the six-digit code of the 1999 polling-table file, where that file holds the municipality
    names: set[str] = field(default_factory=set)
    shape: shapely.Geometry | None = None

    @property
    def key(self) -> str:
        return self.code or f"{CODE_OF[self.department]}:{fold(self.name)}"


def title(text: str) -> str:
    small = {"de", "del", "la", "las", "los", "el", "y"}

    def word(w: str, first: bool) -> str:
        if not first and w in small:
            return w
        return "".join(part[:1].upper() + part[1:] for part in re.split(r"(?<=[(\-])", w))

    return " ".join(word(w, i == 0) for i, w in enumerate(text.lower().split(" ")))


def polling_table_rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text), delimiter="|"))


def file_municipalities(text: str) -> dict[tuple[str, str], str]:
    """The 1999 file's municipalities: {(department, name): six-digit code}."""
    out: dict[tuple[str, str], str] = {}
    for row in polling_table_rows(text):
        out[(CSV_DEPARTMENTS[row["Nombre DEPARTAMENTO"]], row["Nombre MUNICIPIO"])] = row["Codigo MUNICIPIO"]
    return out


def resolve(atlas_names: dict[str, list[str]], by_file: dict[tuple[str, str], str]) -> dict[tuple[str, str], Place]:
    """Every municipality any source names, keyed by (department, name as the Atlas prints it or the file gives it)."""
    places: dict[tuple[str, str], Place] = {}
    taken: set[tuple[str, str]] = set()
    folded = {(d, fold(n)): (d, n) for (d, n) in by_file}
    for department, names in atlas_names.items():
        for name in names:
            file_name = ATLAS_TO_FILE.get((department, name))
            hit = (department, file_name) if file_name else folded.get((department, fold(name)))
            if hit is not None:
                if hit not in by_file:
                    raise KeyError(f"{hit} is not in the 1999 file")
                taken.add(hit)
                place = places.setdefault(hit, Place(department, name, by_file[hit]))
                place.names |= {name, title(hit[1])}
                places[(department, name)] = place
            else:
                places[(department, name)] = Place(department, name, None, {name})
    for (department, name), code in by_file.items():
        if (department, name) not in taken:
            places[(department, name)] = Place(department, title(name), code, {title(name)})
    return places


def match_shapes(places: dict[tuple[str, str], Place], shapes: dict[str, list[tuple[str, shapely.Geometry]]]) -> None:
    """Give each municipality the boundary shape whose name is closest to one of its names, within its department."""
    unique = {id(place): place for place in places.values()}
    for department in DEPARTMENTS:
        here = [p for p in unique.values() if p.department == department]
        pool = dict(shapes.get(department, []))
        left = {fold(n): n for n in pool}
        for place in sorted(here, key=lambda p: p.name):
            named = next((SHAPE_NAMES[(department, n)] for n in place.names if (department, n) in SHAPE_NAMES), None)
            if named is not None:
                place.shape = pool[named]
                left.pop(fold(named))
                continue
            spellings = [fold(re.sub(r"\(.*?\)", "", n)) for n in place.names]
            spellings += [fold(m) for n in place.names for m in re.findall(r"\((.*?)\)", n)]
            chosen = next((s for s in spellings if s in left), None)
            if chosen is None:
                close = [c for s in spellings for c in difflib.get_close_matches(s, list(left), n=1, cutoff=0.86) if s]
                chosen = close[0] if close else None
            if chosen is not None:
                place.shape = pool[left.pop(chosen)]


def read_shapes(
    adm1: str, adm3: str
) -> tuple[dict[str, shapely.Geometry], dict[str, list[tuple[str, shapely.Geometry]]]]:
    departments: dict[str, shapely.Geometry] = {}
    for feature in json.loads(adm1)["features"]:
        departments[feature["properties"]["shapeName"]] = shapely.geometry.shape(feature["geometry"])
    municipalities: dict[str, list[tuple[str, shapely.Geometry]]] = {}
    for feature in json.loads(adm3)["features"]:
        shape = shapely.geometry.shape(feature["geometry"])
        home = max(departments, key=lambda d: departments[d].intersection(shape).area)
        municipalities.setdefault(home, []).append((feature["properties"]["shapeName"], shape))
    return departments, municipalities


__all__ = ["Place", "raw"]
