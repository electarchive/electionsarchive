from datetime import date

from ea.sources.it import abolished


def row(
    year: str, kind: str, code: str, name: str, other: str, other_name: str, law: str = "", day: str = ""
) -> list[str]:
    return [year, kind, "05", "028", code, name, "05", "028", other, other_name, law, "", day, ""]


def test_effective_reads_the_day_the_law_came_into_force() -> None:
    dated = row("2019", "ES", "024001", "A", "024200", "C", day="20/02/2019")
    in_force = row(
        "1995", "ES", "029016", "Contarina", "029052", "Porto Viro", law="L.R. 49, in vigore dal 1° gennaio 1995"
    )
    undated = row(
        "1995", "ES", "028024", "Carrara San Giorgio", "028106", "Due Carrare", law="L.R. 21 marzo 1995, n. 14"
    )
    assert abolished.effective(dated) == (date(2019, 2, 20), "day")
    assert abolished.effective(in_force) == (date(1995, 1, 1), "day")
    assert abolished.effective(undated) == (date(1995, 1, 1), "year")
    ends = abolished.endings([undated], {"028024"})
    assert ends.row(0) == ("028024", "Carrara San Giorgio", date(1995, 1, 1), "year", date(1996, 1, 1))


def test_lineage_tells_mergers_from_absorptions_and_skips_splits() -> None:
    day = "01/01/2016"
    rows = [
        row("2016", "ES", "001001", "Alto", "001300", "Altobasso", day=day),
        row("2016", "ES", "001002", "Basso", "001300", "Altobasso", day=day),
        row("2016", "CS", "001300", "Altobasso", "001001", "Alto", day=day),
        row("2016", "CS", "001300", "Altobasso", "001002", "Basso", day=day),
        row("2017", "ES", "001003", "Monte", "001301", "Valle", day="01/01/2017"),
        row("2017", "ES", "001004", "Valle", "001301", "Valle", day="01/01/2017"),
        row("2017", "CS", "001301", "Valle", "001003", "Monte", day="01/01/2017"),
        row("2017", "CS", "001301", "Valle", "001004", "Valle", day="01/01/2017"),
        row("2018", "ES", "001005", "Piano", "001010", "Colle", day="01/01/2018"),
        row("2018", "AQES", "001010", "Colle", "001005", "Piano", day="01/01/2018"),
        # A comune formed from part of a comune that lives on is not a merger.
        row("2018", "CS", "001302", "Nuovo", "001010", "Colle", day="01/01/2018"),
        row("2023", "ES", "001006", "Tardi", "001303", "Dopo", day="01/01/2023"),
        row("2023", "CS", "001303", "Dopo", "001006", "Tardi", day="01/01/2023"),
    ]
    events = abolished.lineage(rows, {"001001", "001002", "001003", "001004", "001005", "001006"})
    found = {
        (kind, successor): notes
        for kind, successor, notes in events.select("kind", "successor", "notes").unique().iter_rows()
    }
    assert found == {
        ("merged", "001300"): "Alto and Basso merged to form Altobasso.",
        ("absorbed", "001301"): "Monte merged into Valle, which took the new code 001301.",
        ("absorbed", "001010"): "Colle absorbed Piano.",
    }
    merged = events.filter(events["successor"] == "001300")
    assert sorted(merged.select("code", "role").rows()) == [("001001", "from"), ("001002", "from"), ("001300", "to")]
