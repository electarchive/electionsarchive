from ea.names import display


def person_name(forename: str | None, surname: str) -> str:
    return display(" ".join(part for part in (forename, surname) if part), "fr")
