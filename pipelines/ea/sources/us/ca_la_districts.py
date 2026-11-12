from pathlib import Path

from ea.sources.us import ceda_measures


def emit() -> Path:
    return ceda_measures.emit(only=frozenset(ceda_measures.DISTRICTS))
