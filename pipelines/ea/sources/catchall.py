from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "catchall"
VERSION = "1"
LABELS = {
    "Diğer partiler ve bağımsızlar": ("tr", "Other parties and independents"),
    "Bağımsızlar": ("tr", "Independents"),
    "Übrige": ("de", "Others"),
}


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="English names for the catch-all options of Turkey and Germany",
            polity=None,
        )
        for option, label in conn.execute(
            "select id, ballot_label from contest_option where ballot_label = any(%s) order by id", [list(LABELS)]
        ).fetchall():
            lang, english = LABELS[label]
            cs.add("name", entity_id=option, text=label, lang=lang, kind="official", is_preferred=True)
            cs.add("name", entity_id=option, text=english, lang="en", kind="english", is_preferred=False)
        return cs.write()
