from backend.qa_metrics import build_metric_block, select_metric_series


CATALOG = [
    {"series": "ttf_gas", "name": "欧洲天然气TTF", "current": {"date": "2026-10-24", "value": 90.0}},
    {"series": "ru_usdrub_cbr", "name": "俄罗斯央行美元兑卢布官方汇率", "current": {"date": "2026-10-24", "value": 81.0}},
    {"series": "finance:brent", "name": "布伦特原油", "current": {"date": "2026-10-24", "value": 116.0}},
    {"series": "ru_brent_rub_proxy", "name": "俄罗斯石油卢布收入代理", "current": {"date": "2026-10-24", "value": 9300.0}},
    {"series": "ru_fiscal_deficit", "name": "俄罗斯财政赤字", "current": {"date": "2026-09-30", "value": 3.1}},
    {"series": "ru_cbr_key_rate", "name": "俄罗斯央行关键利率", "current": {"date": "2026-10-24", "value": 17.0}},
    {"series": "eu_ukraine_facility_disbursement", "name": "欧盟援乌实际拨款", "current": {"date": "2026-10-02", "value": 2.9}},
    {"series": "eu_ukraine_facility_commitment", "name": "欧盟援乌承诺", "current": {"date": "2026-10-02", "value": 5.0}},
    {"series": "gold", "name": "黄金", "current": {"date": "2026-10-24", "value": 4000.0}},
    {"series": "dgs10", "name": "10年期美债收益率", "current": {"date": "2026-10-24", "value": 5.0}},
    {"series": "TACO_INDEX", "name": "TACO资产压力指数", "current": {"date": "2026-10-24", "value": 0.47}},
    {"series": "hy_oas", "name": "高收益债调整利差", "current": {"date": "2026-10-24", "value": 340}},
]


def test_selects_russian_fiscal_stress_basket_from_question():
    selected = select_metric_series("俄罗斯财政压力如何变化？", "ukraine", CATALOG, limit=5)
    series = [item["series"] for item in selected]
    assert series[:4] == [
        "ru_usdrub_cbr", "finance:brent", "ru_brent_rub_proxy", "ru_fiscal_deficit",
    ]
    assert "ru_cbr_key_rate" in series
    assert "ttf_gas" not in series


def test_selects_actual_disbursement_before_commitment_for_ukraine_aid():
    selected = select_metric_series("欧盟援乌实际拨款和交付有什么变化？", "ukraine", CATALOG, limit=3)
    series = [item["series"] for item in selected]
    assert series[0] == "eu_ukraine_facility_disbursement"
    assert series.index("eu_ukraine_facility_disbursement") < series.index("eu_ukraine_facility_commitment")


def test_selects_usiran_cross_asset_basket_not_first_catalog_rows():
    selected = select_metric_series("美伊冲突对原油、黄金、美债和信用有什么影响？", "usiran", CATALOG, limit=6)
    series = {item["series"] for item in selected}
    assert {"finance:brent", "gold", "dgs10", "hy_oas"}.issubset(series)
    assert "ttf_gas" not in series


def test_metric_block_preserves_units_dates_and_computes_path_statistics():
    block = build_metric_block([{
        "metric": {
            "series": "finance:brent", "name": "布伦特原油", "unit": "美元/桶",
            "frequency": "日度", "freshness": "可用",
        },
        "history": [
            {"date": "2026-10-21", "value": 110.0},
            {"date": "2026-10-22", "value": 116.0},
            {"date": "2026-10-23", "value": 112.0},
        ],
        "as_of": "2026-10-25",
    }])
    row = block["metrics"][0]
    assert row["series"] == "finance:brent"
    assert row["unit"] == "美元/桶"
    assert row["first"] == {"date": "2026-10-21", "value": 110.0}
    assert row["latest"] == {"date": "2026-10-23", "value": 112.0}
    assert row["minimum"] == {"date": "2026-10-21", "value": 110.0}
    assert row["maximum"] == {"date": "2026-10-22", "value": 116.0}
    assert row["observations"] == 3
    assert row["frequency"] == "日度"
    assert row["lag_days"] == 2


def test_metric_block_ignores_invalid_points_and_falls_back_to_current():
    block = build_metric_block([{
        "metric": {
            "series": "aid", "name": "实际拨款", "unit": "十亿欧元",
            "current": {"date": "2026-10-02", "value": "2.9"},
            "frequency": "不定期", "freshness": "数据滞后",
        },
        "history": [
            {"date": "", "value": 5},
            {"date": "2026-09-01", "value": "not-a-number"},
        ],
        "as_of": "bad-date",
    }])
    row = block["metrics"][0]
    assert row["latest"] == {"date": "2026-10-02", "value": 2.9}
    assert row["observations"] == 1
    assert row["lag_days"] is None
    assert row["freshness"] == "数据滞后"


def test_metric_block_deduplicates_dates_and_rejects_non_finite_values():
    block = build_metric_block([{
        "metric": {"series": "x", "name": "X", "unit": "点"},
        "history": [
            {"date": "2026-10-01", "value": 1},
            {"date": "2026-10-01", "value": 2},
            {"date": "2026-10-02", "value": float("nan")},
            {"date": "2026-10-03", "value": float("inf")},
        ],
    }])
    row = block["metrics"][0]
    assert row["observations"] == 1
    assert row["latest"]["value"] == 2.0
