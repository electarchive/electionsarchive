import polars as pl
import shapely

from ea.sources.jp.hor_smd import blocks, carve, numeral


def test_numeral() -> None:
    assert numeral("千三百十三") == 1313
    assert numeral("七百四十七") == 747
    assert numeral("二十八") == 28
    assert numeral("４１") == 41


def test_blocks_annex() -> None:
    rule = blocks("一番から二十六番まで及び二十八番に限る。")
    assert rule is not None and rule.only
    assert rule.holds(1, None) and rule.holds(26, None) and rule.holds(28, None)
    assert not rule.holds(27, None) and not rule.holds(29, None)


def test_blocks_go_and_lots() -> None:
    rule = blocks("二十八番、二十九番四号、二十九番八号から二十九番二十二号まで及び四十四番から四十六番までに限る。")
    assert rule is not None
    assert rule.holds(29, None) and rule.holds(45, None) and not rule.holds(30, None)
    lots = blocks("六百十八番地から七百四十七番地二まで及び八百四十一番地を除く。")
    assert lots is not None and not lots.only
    assert lots.holds(747, 2) and not lots.holds(747, 3) and lots.holds(841, None) and not lots.holds(842, None)


def test_blocks_curated_and_other() -> None:
    rule = blocks("27・29～41番に限る。")
    assert rule is not None and rule.holds(27, None) and rule.holds(35, None) and not rule.holds(28, None)
    assert blocks("字カキヲジ原に限る。") is None
    assert blocks("1～32番") is None


def test_carve() -> None:
    square = shapely.box(0, 0, 2, 1)
    sites = pl.DataFrame({"x": [0.5, 1.5], "y": [0.5, 0.5], "block": ["1", "2"]})
    rule = blocks("一番に限る。")
    assert rule is not None
    pieces, why = carve(square, 7, [(8, rule)], 7, sites)
    assert why is None
    shapes = {district: shape for shape, district in pieces}
    assert shapes[8].equals(shapely.box(0, 0, 1, 1)) and shapes[7].equals(shapely.box(1, 0, 2, 1))
    pieces, why = carve(square, 7, [(8, rule)], 7, sites.filter(pl.col("block") == "2"))
    assert why is not None and pieces == [(square, 7)]
    pieces, why = carve(square, 7, [(8, rule)], 7, sites.filter(pl.col("block") == "1"))
    assert why is None and pieces == [(square, 8)]
