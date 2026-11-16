# ruff: noqa: N999
from ea.sources import census


def emit() -> str:
    return census.emit("in")
