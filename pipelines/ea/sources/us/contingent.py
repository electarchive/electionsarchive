from collections.abc import Callable
from uuid import UUID

from ea.changeset import Changeset

SYSTEMS = {
    "contingent_legislature_2": (
        {"formula": "contingent", "majority": 0.5, "chooser": "legislature", "among": 2},
        "Majority, else the legislature chooses from the top two",
    ),
    "contingent_legislature_3": (
        {"formula": "contingent", "majority": 0.5, "chooser": "legislature", "among": 3},
        "Majority, else the legislature chooses from the top three",
    ),
    "contingent_legislature_4": (
        {"formula": "contingent", "majority": 0.5, "chooser": "legislature", "among": 4},
        "Majority, else the legislature chooses from the top four",
    ),
    "contingent_house_districts": (
        {"formula": "contingent", "majority": 0.5, "districts": True, "chooser": "lower_house", "among": 2},
        "Majority of the vote and of House districts, else the House chooses from the top two",
    ),
    "contingent_house_delegations": (
        {"formula": "contingent", "majority": 0.5, "of": "appointed", "chooser": "house_delegations", "among": 3},
        "Majority of electors appointed, else the House chooses from the top three, one vote per state",
    ),
    "contingent_senate": (
        {"formula": "contingent", "majority": 0.5, "of": "appointed", "chooser": "senate", "among": 2},
        "Majority of electors appointed, else the Senate chooses from the top two",
    ),
}

GOVERNORS: dict[str, tuple[int | None, int | None, str]] = {
    "VT": (None, None, "contingent_legislature_3"),
    "NH": (None, 1912, "contingent_legislature_2"),
    "CT": (1819, 1900, "contingent_legislature_2"),
    "MA": (None, 1854, "contingent_legislature_4"),
    "RI": (None, 1893, "contingent_legislature_2"),
    "ME": (None, 1879, "contingent_legislature_4"),
    "GA": (None, 1966, "contingent_legislature_2"),
    "MS": (1895, 2019, "contingent_house_districts"),
}


def governor(state: str, year: int) -> str | None:
    if state not in GOVERNORS:
        return None
    start, end, key = GOVERNORS[state]
    return key if (start is None or year >= start) and (end is None or year <= end) else None


def systems(cs: Changeset, name: Callable[[UUID, str], None]) -> Callable[[str], UUID]:
    ids: dict[str, UUID] = {}

    def system(key: str) -> UUID:
        if key not in ids:
            ids[key] = cs.key("us.electoral_system", key)
            if ids[key] in cs.minted:
                rules, text = SYSTEMS[key]
                cs.add("electoral_system", id=ids[key], polity_id=cs.polity("US"), rules=rules, rules_version=1)
                name(ids[key], text)
        return ids[key]

    return system
