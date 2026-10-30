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
