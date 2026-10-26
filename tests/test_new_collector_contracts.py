import importlib.util
import io
import json
import ssl
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


COLLECTORS = Path(__file__).resolve().parents[1] / "collectors"
sys.path.insert(0, str(COLLECTORS))
import fixed_channel_common as fixed  # noqa: E402


@pytest.fixture(autouse=True)
def no_network(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("contract tests must not access the network")

    monkeypatch.setattr(fixed, "fetch", forbidden)
    monkeypatch.setattr(fixed.urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(fixed.subprocess, "run", forbidden)
    monkeypatch.setenv("COLLECTOR_OUTPUT_ROOT", str(tmp_path / "output"))
    for name in ("GIE_API_KEY", "AGSI_API_KEY", "USDA_API_KEY", "DATA_GOV_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("argument", ["--selftest", "--self-test"])
def test_both_selftest_spellings_are_offline(monkeypatch, tmp_path, argument):
    monkeypatch.setattr(sys, "argv", ["collector.py", argument])
    assert fixed.run("gas", __file__) == 0
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize(
    "kind,key,body",
    [
        ("gas", "GIE_API_KEY", {"data": []}),
        ("gas", "GIE_API_KEY", {"data": [{"gasDayStart": "2026-10-01", "full": None}]}),
        ("nbu", None, []),
        ("nbu", None, [{"id_api": "unrelated", "value": 1}]),
        ("grain", "USDA_API_KEY", []),
        ("grain", "USDA_API_KEY", [{"attributeDescription": "unrelated", "value": 1}]),
    ],
)
def test_empty_or_unmatched_source_data_is_failed(monkeypatch, kind, key, body):
    if key:
        monkeypatch.setenv(key, "contract-test-placeholder")
    monkeypatch.setattr(fixed, "fetch", lambda *args, **kwargs: json.dumps(body).encode())
    result = fixed.COLLECTORS[kind](__file__)
    assert not result["observations"]
    assert all(item["state"] == "failed" for item in result["source_status"])
    assert fixed.collection_status(result) == "failed"


@pytest.mark.parametrize(
    "kind,key,body,expected_id",
    [
        ("gas", "GIE_API_KEY", {"data": [{"gasDayStart": "2026-10-01", "full": 80}]}, "eu_gas_storage_full"),
        ("nbu", None, [{"id_api": "Cur_acc", "tzep": "D021USD_7", "t023": "total", "dt": "20261001", "value": -20}], "ua_current_account"),
    ],
)
def test_matching_source_data_remains_usable(monkeypatch, kind, key, body, expected_id):
    if key:
        monkeypatch.setenv(key, "contract-test-placeholder")
    monkeypatch.setattr(fixed, "fetch", lambda *args, **kwargs: json.dumps(body).encode())
    result = fixed.COLLECTORS[kind](__file__)
    assert result["observations"][0]["id"] == expected_id
    assert result["observations"][0]["history"][0]["date"] == "2026-10-01"
    assert fixed.collection_status(result) == "ok"


@pytest.mark.parametrize("kind", ["gas", "grain"])
def test_missing_configuration_does_not_call_sources(monkeypatch, tmp_path, capsys, kind):
    monkeypatch.setattr(sys, "argv", ["collector.py"])
    assert fixed.run(kind, __file__) == 2
    output = tmp_path / "output"
    payload = json.loads((output / "geo-v6.json").read_text(encoding="utf-8"))
    assert payload["collection_status"] == "configuration_required"
    assert payload["observations"] == []
    assert json.loads(capsys.readouterr().out)["collection_status"] == "configuration_required"


def metric_payload(states):
    payload = fixed.base_payload("contract test", fixed.utc_now())
    payload["records"].append(fixed.source_record(
        "source", "测试", "测试", "https://example.test", payload["as_of"],
        dimension="aid", publisher="测试",
    ))
    payload["observations"].append(fixed.observation(
        "metric", "测试", "%", "日频", "测试", "测试",
        [{"date": "2026-01-01", "value": 1}], ["source"], "aid",
    ))
    for state in states:
        fixed.add_status(payload, state, state, "contract fixture")
    return payload


@pytest.mark.parametrize(
    "states,expected",
    [(["ok"], "ok"), (["ok", "failed"], "partial"), (["ok_fallback", "available"], "ok")],
)
def test_usable_partial_metrics_keep_publishable_exit(monkeypatch, tmp_path, capsys, states, expected):
    payload = metric_payload(states)
    monkeypatch.setitem(fixed.COLLECTORS, "gas", lambda _: payload)
    monkeypatch.setattr(sys, "argv", ["collector.py"])
    assert fixed.run("gas", __file__) == 0
    output = tmp_path / "output"
    for filename in ("geo-v6.json", "source-status.json", "_source_status.json"):
        assert json.loads((output / filename).read_text(encoding="utf-8"))["collection_status"] == expected
    assert json.loads(capsys.readouterr().out)["collection_status"] == expected


def test_all_failed_sources_do_not_publish_placeholders(monkeypatch, tmp_path):
    payload = metric_payload(["failed"])
    payload["observations"] = []
    monkeypatch.setitem(fixed.COLLECTORS, "gas", lambda _: payload)
    monkeypatch.setattr(sys, "argv", ["collector.py"])
    assert fixed.run("gas", __file__) == 1
    assert json.loads((tmp_path / "output" / "geo-v6.json").read_text(encoding="utf-8"))["collection_status"] == "failed"


def test_usda_keeps_good_years_and_reports_partial(monkeypatch):
    monkeypatch.setenv("USDA_API_KEY", "contract-test-placeholder")
    valid_year = fixed.date.today().year

    def fake_fetch(url, **kwargs):
        rows = [{"attributeDescription": "Production", "marketYear": valid_year, "value": 10}] if f"/year/{valid_year}?" in url else []
        return json.dumps(rows).encode()

    monkeypatch.setattr(fixed, "fetch", fake_fetch)
    result = fixed.collect_grain(__file__)
    assert len(result["observations"]) == 2
    assert len([item for item in result["source_status"] if item["state"] == "failed"]) == 10
    assert len([item for item in result["source_status"] if item["state"] == "partial"]) == 2
    assert fixed.collection_status(result) == "partial"


def load_stcn():
    spec = importlib.util.spec_from_file_location(
        "stcn_contract", COLLECTORS / "stcn-usiran-monitor" / "stcn_usiran_monitor.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("isolated", [True, False])
def test_stcn_output_root_and_legacy_default(monkeypatch, tmp_path, isolated):
    stcn = load_stcn()
    script_root = tmp_path / "source"
    script_root.mkdir()
    monkeypatch.setattr(stcn, "ROOT", script_root)
    monkeypatch.setattr(stcn, "discover", lambda *args: ({}, [], {"yw": 1, "xw": 1, "kx": 1}))
    if isolated:
        out = tmp_path / "isolated" / "output"
        monkeypatch.setenv("COLLECTOR_OUTPUT_ROOT", str(out))
    else:
        monkeypatch.delenv("COLLECTOR_OUTPUT_ROOT")
        out = script_root / "output"
    assert stcn.main(days=1, max_pages=1, workers=1) == 0
    assert json.loads((out / "geo-v6.json").read_text(encoding="utf-8"))["records"] == []
    assert (out / "_collection_report.json").is_file()
    if isolated:
        assert not (script_root / "output").exists()


def test_urllib_fallback_preserves_source_scoped_ca(monkeypatch):
    # Capture the real function before replacing other network entry points.
    module_spec = importlib.util.spec_from_file_location("fixed_tls_contract", COLLECTORS / "fixed_channel_common.py")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr=b"test failure"))
    captured = {}

    def fake_open(request, **kwargs):
        captured.update(kwargs)
        return io.BytesIO(b"response")

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_open)
    assert module.fetch("https://example.test", ca_cert=module.RUSSIAN_TRUSTED_SUB_CA) == b"response"
    context = captured["context"]
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN
    assert context.cert_store_stats()["x509_ca"] >= 1
