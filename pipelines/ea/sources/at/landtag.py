"""Austria's nine Länder as polities under the national polity.

Each is a `federal_unit` polity keyed by its ISO 3166-2 code, so the id is the one a later pipeline derives from
`polity\\x1fAT-7`. The Landtag elections and bodies of `at.tirol` and `at.wien` belong to their Land through these ids.
"""

from uuid import uuid5

from ea import changeset, package
from ea.package import Package

SOURCE = "at.landtag"
AUSTRIA = str(uuid5(package.NAMESPACE, "polity\x1fAT"))

# ISO 3166-2:AT subdivision code, German name, English name (None where it is the same).
LAENDER = [
    ("AT-1", "Burgenland", None),
    ("AT-2", "Kärnten", "Carinthia"),
    ("AT-3", "Niederösterreich", "Lower Austria"),
    ("AT-4", "Oberösterreich", "Upper Austria"),
    ("AT-5", "Salzburg", None),
    ("AT-6", "Steiermark", "Styria"),
    ("AT-7", "Tirol", "Tyrol"),
    ("AT-8", "Vorarlberg", None),
    ("AT-9", "Wien", "Vienna"),
]


def polity(code: str) -> str:
    """The id of a Land from its ISO 3166-2 code, such as `AT-7`."""
    return str(uuid5(package.NAMESPACE, f"polity\x1f{code}"))


def fetch() -> None:
    """Nothing to download: the Länder and their ISO codes are fixed."""


def packages() -> list[str]:
    return [f"{SOURCE}/geo"]


def parse() -> None:
    geo().write()


def emit() -> None:
    package.run("load", packages())


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Austria's nine Länder as polities under the national polity, keyed by their ISO 3166-2 codes",
    )
    p.external.add(AUSTRIA)
    for code, german, english in LAENDER:
        id = polity(code)
        p.add("polity", id=id, kind="federal_unit", iso_code=code)
        p.add("polity_parent", polity_id=id, parent_id=AUSTRIA)
        p.name(id, german, "de", "official")
        if english is not None:
            p.name(id, english, "en", "official", False)
        p.add("slug", path=f"at/{changeset.slug(german)}", entity_id=id, is_current=True)
    return p
