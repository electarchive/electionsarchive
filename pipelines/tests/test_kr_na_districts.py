import shapely

from ea.sources.kr import na_districts as nd


def _emd(town: str, name: str) -> nd.Emd:
    return nd.Emd(code=f"x/{town}{name}", name=name, town=town, province="11", shape=shapely.Point(0, 0), point=(0, 0))


def test_counted_forms() -> None:
    assert nd.counted("숭인제1동") == ["숭인1동", "숭인제1동"]
    assert nd.counted("거제1동") == ["거1동", "거제1동"]
    assert nd.counted("종로1·2·3·4가동") == ["종로1234가동"]
    assert nd.drawn("종로1_2_3_4가동") == "종로1234가동"


def test_polling_district_names() -> None:
    assert nd.POLLING.sub("", "지행면제7투") == "지행면"
    assert nd.POLLING.sub("", "당산제2동제2투") == "당산제2동"
    assert not nd._place("잘못투입구분된투표지")
    assert nd._place("지행면")


def test_hinted_prefers_the_counting_office() -> None:
    here, there = _emd("수원시장안구", "율천동"), _emd("수원시권선구", "율천동")
    assert nd._hinted([here, there], "수원시장안구", "수원시갑") == [here]
    assert nd._hinted([here, there], "화성시갑", "화성시갑") == []


def test_merged_dongs() -> None:
    index = nd.Index([_emd("마포구", "공덕1동"), _emd("마포구", "공덕2동"), _emd("동작구", "흑석동")])
    assert {emd.name for emd in index.merged("11", "공덕동")} == {"공덕1동", "공덕2동"}
    assert [emd.name for emd in index.merged("11", "흑석1동")] == ["흑석동"]
