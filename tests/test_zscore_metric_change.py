from backend.research import metric_view


def test_zscore_metric_reports_delta_without_percent():
    obs = {"series": "TACO_Index_T", "name": "TACO资产压力指数", "unit": "z",
           "history": [{"date": "2026-11-05", "value": -0.5048},
                       {"date": "2026-11-06", "value": -0.6381}]}
    view = metric_view(obs)
    assert view["delta"] == -0.1333
    assert view["pct"] is None
