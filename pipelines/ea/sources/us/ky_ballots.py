import re
from collections import Counter

CITY = re.compile(r"(?i)\bCity of ([A-Za-z'. -]+?)\s*(?:,|\(|$|\b(?:mayor|council|commission))")


def letters(text: str) -> str:
    return re.sub(r"[^A-Z]", "", text.upper())


def city_by_candidate(printed: list[str], mayor: bool, last_name: str) -> str | None:
    key = letters(last_name)
    if not key:
        return None
    lines = [line.strip() for line in printed]
    squashed = [letters(line) for line in lines]
    votes: Counter[str] = Counter()
    for i, squashed_line in enumerate(squashed):
        if key not in squashed_line or len(lines[i]) >= 60:
            continue
        for j in range(i - 1, max(-1, i - 30), -1):
            if re.search(r"(?i)vote ?for", lines[j]):
                context = " ".join(lines[max(0, j - 4) : j + 1])
                found = CITY.findall(context)
                if found and bool(re.search(r"(?i)mayor", context)) == mayor:
                    votes[re.sub(r"\s+City$", "", found[-1].strip().title())] += 1
                break
    return votes.most_common(1)[0][0] if len(votes) == 1 else None


SCHOOL = re.compile(r"(?i)\b(\d+|S)(?:st|nd|rd|th)\s+Educational|education\w*[ ,]+(?:[A-Za-z ]{0,20}?)district\s+(\d+)")


def school_district_by_candidate(printed: list[str], last_name: str) -> str | None:
    key = letters(last_name)
    if not key:
        return None
    lines = [line.strip() for line in printed]
    squashed = [letters(line) for line in lines]
    votes: Counter[str] = Counter()
    for i, squashed_line in enumerate(squashed):
        if key not in squashed_line or len(lines[i]) >= 60:
            continue
        for j in range(i - 1, max(-1, i - 12), -1):
            if re.search(r"(?i)vote ?for", lines[j]):
                context = " ".join(lines[max(0, j - 4) : j + 1])
                found = SCHOOL.search(context)
                if found and not re.search(r"(?i)independent", context):
                    votes["5" if (found[1] or "").upper() == "S" else found[1] or found[2]] += 1
                break
    return votes.most_common(1)[0][0] if len(votes) == 1 else None


MISREAD = {"AUBUM": "AUBURN"}


def city_letters(name: str) -> str:
    squashed = letters(re.sub(r"(?i)\s+city$", "", name.strip())) or letters(name)
    return MISREAD.get(squashed, squashed)


def near[T](table: dict[tuple[str, str], T], key: tuple[str, str]) -> T | None:
    if key in table:
        return table[key]
    county, want = key
    found = [
        value
        for (owner, other), value in table.items()
        if owner == county
        and min(len(other), len(want)) >= 4
        and (other.startswith(want) or want.startswith(other) or other.endswith(want) or want.endswith(other))
    ]
    return found[0] if len(found) == 1 else None


def join_headings(lines: list[str]) -> list[str]:
    return [
        f"{line} {lines[k + 1]}"
        if k + 1 < len(lines)
        and re.fullmatch(r"[A-Z][A-Z'. -]+", line.strip())
        and re.match(r"(?i)CITY (?:COUNCIL|COMMISSIONERS?)\b", lines[k + 1].strip())
        else line
        for k, line in enumerate(lines)
    ]


def unwrap(lines: list[str]) -> list[str]:
    return re.sub(r"(?i)(Vote ?for\s+up)[ \t]*\n[ \t]*(to\b)", r"\1 \2", "\n".join(lines)).splitlines()
