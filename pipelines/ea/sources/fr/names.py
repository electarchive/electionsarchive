LOWERCASE = ("de", "du", "des", "d'", "le", "la")


def proper(text: str) -> str:
    words = text.title().split(" ")
    return " ".join(
        word.lower() if index and word.lower() in LOWERCASE and index < len(words) - 1 else word
        for index, word in enumerate(words)
    ).replace("D'", "d'")


def person_name(forename: str | None, surname: str) -> str:
    return " ".join(
        part
        for part in (
            (proper(forename) if forename and forename.isupper() else forename),
            proper(surname) if surname.isupper() else surname,
        )
        if part
    )
