from backend.meeting_adapter import _point


def test_common_venues_have_map_points():
    for city in ("基辅", "迈阿密", "圣彼得堡", "德黑兰"):
        assert _point(city) and _point(city)[0]["label"] == city


def test_unknown_venue_gets_no_point():
    assert _point("地点未定") == []
    assert _point("") == []
