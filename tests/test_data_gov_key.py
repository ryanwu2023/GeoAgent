import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULES = [
    'collectors/defense-spend-monitor/spend_monitor.py',
    'collectors/readiness-monitor/readiness_monitor.py',
]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize('path', MODULES)
def test_demo_key_replaced_only_for_data_gov_hosts(path, monkeypatch):
    m = load(path, 'data_gov_' + Path(path).stem)
    crs = 'https://api.congress.gov/v3/crsreport?limit=40&format=json&api_key=DEMO_KEY'
    monkeypatch.delenv('DATA_GOV_API_KEY', raising=False)
    assert m.with_data_gov_key(crs) == crs
    monkeypatch.setenv('DATA_GOV_API_KEY', 'personal-key')
    assert m.with_data_gov_key(crs) == 'https://api.congress.gov/v3/crsreport?limit=40&format=json&api_key=personal-key'
    other = 'https://example.com/feed?api_key=DEMO_KEY'
    assert m.with_data_gov_key(other) == other
    custom = 'https://api.congress.gov/v3/crsreport?api_key=own-key'
    assert m.with_data_gov_key(custom) == custom
