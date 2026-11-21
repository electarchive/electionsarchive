from ea.sources.mx.diputados import distribute

COLUMNS = {
    "PRI": ("PRI",),
    "PVEM": ("PVEM",),
    "PRD": ("PRD",),
    "PT": ("PT",),
    "MC": ("MC",),
    "PRI_PVEM": ("PRI", "PVEM"),
    "PRD_PT_MC": ("PRD", "PT", "MC"),
}


def test_district_sum_follows_the_district_rule() -> None:
    # 101 votes marked PRI and PVEM in one district: 50 each, and the odd vote to the PRI, the coalition party with
    # the most votes in the district, however the casillas split the marks.
    rows = [(f"c{i}", "PRI_PVEM", 1) for i in range(101)] + [("c0", "PRI", 300), ("c1", "PVEM", 20)]
    out = distribute(rows, COLUMNS, {"PRI": 300, "PVEM": 20})
    assert sum(v for (_, party), v in out.items() if party == "PRI") == 300 + 51
    assert sum(v for (_, party), v in out.items() if party == "PVEM") == 20 + 50
    # Every casilla keeps its own marks.
    for i in range(101):
        assert out.get((f"c{i}", "PRI"), 0) + out.get((f"c{i}", "PVEM"), 0) == 1 + (300 if i == 0 else 0) + (
            20 if i == 1 else 0
        )


def test_three_party_remainder_goes_to_the_two_strongest() -> None:
    rows = [("a", "PRD_PT_MC", 4), ("b", "PRD_PT_MC", 4), ("a", "PT", 9), ("a", "MC", 5), ("b", "PRD", 7)]
    out = distribute(rows, COLUMNS, {"PRD": 7, "PT": 9, "MC": 5})
    totals = {party: sum(v for (_, p), v in out.items() if p == party) for party in ("PRD", "PT", "MC")}
    # 8 marks: 2 each, then the remainder of 2 to the PT (9) and the PRD (7).
    assert totals == {"PRD": 7 + 3, "PT": 9 + 3, "MC": 5 + 2}
    assert out[("a", "PRD")] + out[("a", "PT")] + out[("a", "MC")] == 4 + 9 + 5
    assert out[("b", "PRD")] + out[("b", "PT")] + out[("b", "MC")] == 4 + 7
