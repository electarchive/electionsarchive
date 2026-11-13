from ea.sources import census


def emit() -> str:
    return census.emit("es")
