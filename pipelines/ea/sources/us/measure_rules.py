import re
from uuid import UUID

from ea.changeset import Changeset

TWO_THIRDS = 2 / 3

SYSTEMS: dict[str, tuple[str, dict[str, object], str, str]] = {
    "referendum-fl-60": (
        "US-FL",
        {"formula": "referendum", "majority": {"share": 0.6}},
        "Florida constitutional amendment, 60 percent of Yes and No votes",
        "An amendment or revision takes effect when at least 60 percent of the voters voting on the measure approve it (Fla. Const. art. XI, § 5(e)).",
    ),
    "referendum-co-55": (
        "US-CO",
        {"formula": "referendum", "majority": {"share": 0.55}},
        "Colorado constitutional amendment, 55 percent of Yes and No votes",
        "A constitutional amendment needs at least 55 percent of the votes cast on it, unless it only repeals part of the constitution (Colo. Const. art. V, § 1(4)(b)).",
    ),
    "referendum-nh-two-thirds": (
        "US-NH",
        {"formula": "referendum", "majority": {"share": TWO_THIRDS}},
        "New Hampshire constitutional amendment, two-thirds of Yes and No votes",
        "An amendment is adopted when two-thirds of the qualified voters voting on the subject approve it (N.H. Const. pt. 2, art. 100).",
    ),
    "referendum-mn-ballots": (
        "US-MN",
        {"formula": "referendum", "minimum": {"share": 0.5, "strict": True, "of": "ballots"}},
        "Minnesota constitutional amendment, majority of all ballots cast",
        "An amendment is ratified by a majority of all the electors voting at the election, so a ballot left blank on the question counts as No (Minn. Const. art. IX, § 1; Minn. Stat. § 204D.15).",
    ),
    "referendum-hi-ballots": (
        "US-HI",
        {"formula": "referendum", "minimum": {"share": 0.5, "strict": True, "of": "ballots"}},
        "Hawaii constitutional amendment, majority of all ballots cast",
        "At a general election an amendment needs a majority of all the votes tallied on the question, and that majority must be at least 50 percent of the total votes cast at the election, so blank and spoiled ballots count as No (Haw. Const. art. XVII, § 2).",
    ),
    "referendum-wy-ballots": (
        "US-WY",
        {"formula": "referendum", "minimum": {"share": 0.5, "strict": True, "of": "ballots"}},
        "Wyoming initiative, majority of all ballots cast",
        "An initiated measure is approved only by a majority of those voting in the general election, not just of those voting on the measure (Wyo. Const. art. 3, § 52(f)).",
    ),
    "referendum-ne-35": (
        "US-NE",
        {"formula": "referendum", "minimum": {"share": 0.35, "of": "ballots"}},
        "Nebraska measure, majority with Yes at least 35 percent of all ballots cast",
        "A measure needs a majority of the votes cast on it, and the Yes votes must equal at least 35 percent of the total votes cast at the election (Neb. Const. art. III, § 4; art. XVI, § 1).",
    ),
    "referendum-ma-30": (
        "US-MA",
        {"formula": "referendum", "minimum": {"share": 0.3, "of": "ballots"}},
        "Massachusetts initiative or referendum, majority with Yes at least 30 percent of all ballots cast",
        "A law proposed by initiative petition, or a law put to referendum, is approved when a majority of the voters voting on it say Yes and the Yes votes equal at least 30 percent of the total ballots cast at the election (Mass. Const. amend. art. 48, The Initiative, pt. V, § 1; The Referendum, pt. III, § 4).",
    ),
    "referendum-tn-governor": (
        "US-TN",
        {"formula": "referendum", "minimum": {"share": 0.5, "strict": True, "of": "governor"}},
        "Tennessee constitutional amendment, Yes votes over half of the votes cast for governor",
        "An amendment is ratified when a majority of the voters voting on it approve it and the Yes votes are a majority of all the votes cast for governor at the same election (Tenn. Const. art. XI, § 3).",
    ),
    "referendum-ca-two-thirds": (
        "US-CA",
        {"formula": "referendum", "majority": {"share": TWO_THIRDS}},
        "California local measure, two-thirds of Yes and No votes",
        "A special tax proposed by a local government, and general obligation bonds of a city, county, or special district, need two-thirds of the voters voting on the measure (Cal. Const. art. XIII A, § 4; art. XIII C, § 2(d); art. XVI, § 18(a)).",
    ),
    "referendum-ca-55": (
        "US-CA",
        {"formula": "referendum", "majority": {"share": 0.55}},
        "California school bond, 55 percent of Yes and No votes",
        "General obligation bonds of a school district, community college district, or county office of education under Proposition 39 need 55 percent of the voters voting on the measure (Cal. Const. art. XIII A, § 1(b)(3); art. XVI, § 18(b)).",
    ),
    "referendum-wa-60-validated": (
        "US-WA",
        {
            "formula": "referendum",
            "majority": {"share": 0.6},
            "minimum": {"share": 0.24, "of": "previous_general"},
        },
        "Washington local bond or excess levy, 60 percent with turnout validation",
        "General obligation bonds and most excess property tax levies need three-fifths of the votes cast on the proposition, and the vote must be validated: either the total votes on it reach 40 percent of the total votes cast in the district at the last preceding general election, or the Yes votes reach three-fifths of that 40 percent. With 60 percent Yes, both tests come down to Yes votes of at least 24 percent of the last general election's vote (Wash. Const. art. VII, § 2(a); art. VIII, § 6; RCW 84.52.052; RCW 39.36.050).",
    ),
    "referendum-id-two-thirds": (
        "US-ID",
        {"formula": "referendum", "majority": {"share": TWO_THIRDS}},
        "Idaho local bond, two-thirds of Yes and No votes",
        "A county, city, school district, or other subdivision may incur debt beyond the year's revenue only with the assent of two-thirds of the voters voting at an election held for that purpose (Idaho Const. art. VIII, § 3).",
    ),
    "referendum-mo-four-sevenths": (
        "US-MO",
        {"formula": "referendum", "majority": {"share": 4 / 7}},
        "Missouri local bond at a general election, four-sevenths of Yes and No votes",
        "General obligation bonds of a county, city, school district, or other subdivision need four-sevenths of the votes cast at a general municipal election or a state general or primary election, and two-thirds at any other election (Mo. Const. art. VI, § 26(b)).",
    ),
    "referendum-id-55": (
        "US-ID",
        {"formula": "referendum", "majority": {"share": 0.55}},
        "Idaho school plant facilities levy, 55 percent of Yes and No votes",
        "A school plant facilities reserve fund levy that brings the district's plant facilities and bond levies to less than 0.2 percent of market value needs 55 percent of the electors voting (Idaho Code § 33-804(1)).",
    ),
    "referendum-id-60": (
        "US-ID",
        {"formula": "referendum", "majority": {"share": 0.6}},
        "Idaho school plant facilities levy, 60 percent of Yes and No votes",
        "A school plant facilities reserve fund levy that brings the district's plant facilities and bond levies to at least 0.2 and less than 0.3 percent of market value needs 60 percent of the electors voting (Idaho Code § 33-804(2)).",
    ),
    "referendum-mp-two-thirds": (
        "US-MP",
        {"formula": "referendum", "majority": {"share": TWO_THIRDS}},
        "Northern Mariana Islands constitutional convention question, two-thirds of votes cast",
        "The legislature convenes a constitutional convention only if two-thirds of the votes cast on the question are affirmative (N.M.I. Const. art. XVIII, § 2(c)).",
    ),
    "referendum-ok-60": (
        "US-OK",
        {"formula": "referendum", "majority": {"share": 0.6}},
        "Oklahoma local bond, 60 percent of Yes and No votes",
        "General obligation bonds of a county, city, town, or school district need three-fifths of the voters voting on the question (Okla. Const. art. X, §§ 26, 27).",
    ),
    "referendum-ia-60": (
        "US-IA",
        {"formula": "referendum", "majority": {"share": 0.6}},
        "Iowa local general obligation bond, 60 percent of Yes and No votes",
        "A county, city, or school district bond issue put to the voters needs 60 percent of the votes cast on the question (Iowa Code §§ 75.1, 296.6).",
    ),
    "referendum-nd-60": (
        "US-ND",
        {"formula": "referendum", "majority": {"share": 0.6}},
        "North Dakota local bond, 60 percent of Yes and No votes",
        "General obligation bonds of a political subdivision need 60 percent of the qualified electors voting on the question (N.D. Cent. Code § 21-03-07).",
    ),
}


def systems(cs: Changeset, keys: set[str]) -> dict[str, UUID]:
    found = {key: cs.key("us.electoral_system", key) for key in keys}
    missing = [key for key, system in found.items() if system in cs.minted]
    if missing:
        raise ValueError(f"the measure systems must already exist: {', '.join(sorted(missing))}")
    return found


def emit_systems(cs: Changeset) -> dict[str, UUID]:
    found = {}
    for key, (polity, rules, title, note) in SYSTEMS.items():
        system = cs.key("us.electoral_system", key)
        cs.add("electoral_system", id=system, polity_id=cs.polity(polity), rules=rules, rules_version=1)
        cs.add("name", entity_id=system, text=title, lang="en", kind="official", is_preferred=True)
        cs.add("note", entity_id=system, kind="context", body_md=note)
        found[key] = system
    return found


STATEWIDE = {
    "FL": "referendum-fl-60",
    "NH": "referendum-nh-two-thirds",
    "MN": "referendum-mn-ballots",
    "HI": "referendum-hi-ballots",
    "WY": "referendum-wy-ballots",
    "NE": "referendum-ne-35",
    "MA": "referendum-ma-30",
    "TN": "referendum-tn-governor",
}


def statewide(state: str, label: str) -> str:
    if state == "CO":
        return "referendum-co-55" if label.startswith("Amendment") else "referendum"
    if state == "MP":
        return "referendum-mp-two-thirds" if label.startswith("Constitutional Convention") else "referendum"
    return STATEWIDE.get(state, "referendum")


def washington(place: str, title: str, text: str) -> str:
    wording = f"{title} {text}"
    if re.search(r"\bbonds\b", wording, re.IGNORECASE) and re.search(
        r"excess|general obligation", wording, re.IGNORECASE
    ):
        return "referendum-wa-60-validated"
    if "School" not in place and re.search(
        r"excess (?:property )?tax|excess levy|special property tax|without regard to the dollar rate",
        wording,
        re.IGNORECASE,
    ):
        return "referendum-wa-60-validated"
    return "referendum"


def california(entity: str, wording: str) -> str:
    if re.search(r"initiative|petition", wording, re.IGNORECASE):
        return "referendum"
    if re.search(r"school|college|education", entity, re.IGNORECASE):
        if re.search(r"\bbonds?\b", wording, re.IGNORECASE):
            return "referendum-ca-55" if re.search(r"audit|oversight", wording, re.IGNORECASE) else "referendum"
        if re.search(r"parcel|per square foot", wording, re.IGNORECASE):
            return "referendum-ca-two-thirds"
        return "referendum"
    if re.search(r"special tax|parcel tax|general obligation bonds?", wording, re.IGNORECASE):
        return "referendum-ca-two-thirds"
    return "referendum"


LOCAL = {
    ("CA", "Liberty Union High School District", "Measure X: School Bonds"): "referendum-ca-55",
    ("CA", "City of Alameda", "Measure L: General Obligation Bond"): "referendum-ca-two-thirds",
    ("CA", "United Water Conservation District", "Measure D: Water Supply Bond"): "referendum-ca-two-thirds",
    (
        "CA",
        "Riverside County Transportation Commission",
        "Measure A: Traffic Relief and Road Repair Renewal",
    ): "referendum-ca-two-thirds",
    (
        "CA",
        "Desert Recreation District",
        "Measure P: Local Park Repair and Senior Services",
    ): "referendum-ca-two-thirds",
    ("CA", "City of Richmond", "Measure Q: Fire Facilities Bonds"): "referendum-ca-two-thirds",
    ("CA", "City of Colfax", "Measure I: Street Improvement Sales Tax"): "referendum-ca-two-thirds",
    ("CA", "Sonoma County", "Measure H: Regional Parks Sales Tax"): "referendum-ca-two-thirds",
    ("CA", "City of Mill Valley", "Measure T: Municipal Service Tax"): "referendum-ca-two-thirds",
    ("CA", "City of Monterey", "Measure S: Sales Tax"): "referendum-ca-two-thirds",
    ("CA", "North County Recreation and Park District", "Measure V: Parcel Tax"): "referendum-ca-two-thirds",
    ("CA", "Soledad-Mission Recreation District", "Measure U: Parcel Tax"): "referendum-ca-two-thirds",
    ("CA", "Isla Vista Community Services District", "Measure E2026: Utility Users' Tax"): "referendum-ca-two-thirds",
    ("ID", "Highland Joint School District No. 305", "Plant facility levy"): "referendum-id-55",
    ("ID", "Moscow School District No. 281", "School plant facilities reserve fund levy"): "referendum-id-60",
}


def local(state: str, entity: str, label: str, wording: str) -> str:
    found = LOCAL.get((state, entity, label))
    if found is not None:
        return found
    if state == "CA":
        return california(entity, wording)
    if state == "WA":
        return washington(entity, label, wording)
    return "referendum"
