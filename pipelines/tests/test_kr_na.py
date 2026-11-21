from ea.sources.kr import na


def test_split_ongjin() -> None:
    names = [f"무소속 후보{index}" for index in range(14)]
    votes = [0, 7997, *[0] * 6, 10832, *[0] * 5]
    row = {"sgg": "제29선거구(옹진군 갑구)", "head": (names, 14), "votes": votes, "valid": 18829, "invalid": 0}
    first, second = na._split("19500530", "41", [row])
    assert (first["sgg"], first["head"][0], first["valid"]) == ("제29선거구(옹진군 갑구)", names[:8], 7997)
    assert (second["sgg"], second["head"][0], second["valid"]) == ("제30선거구(옹진군 을구)", names[8:], 10832)


def test_member_rows() -> None:
    text = (
        "! [[옹진군 갑]]\n| [[서범석]]\n| x\n"
        '! rowspan="2"| [[양주군 을]]\n| [[강성태 (자유당 정치인)|강성태]]\n'
        "! [[장단군 (선거구)|장단군]]\n| [[백상규]]\n"
    )
    assert na.MEMBER_ROW.findall(text) == [("옹진군 갑", "서범석"), ("양주군 을", "강성태"), ("장단군", "백상규")]
