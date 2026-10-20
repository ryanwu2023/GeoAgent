import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _engine(collector: str, module_name: str):
    collector_dir = ROOT / "collectors" / collector
    module_path = collector_dir / module_name
    spec = importlib.util.spec_from_file_location(collector.replace("-", "_"), module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    config = json.loads((collector_dir / "config.json").read_text(encoding="utf-8"))
    return module.Engine(config)


def _assert_buckets(engine, title: str, expected: set[str]):
    result = engine.classify(title, "")
    assert result is not None
    assert expected <= set(result.get("buckets") or [])


def test_front_covers_current_drone_energy_and_transliteration_terms():
    engine = _engine("ua-front-monitor", "ua_front_monitor.py")
    _assert_buckets(
        engine,
        "Russian jet-powered Shaheds attack Kyiv energy facilities; interceptor drones shoot down targets",
        {"drone_war", "air_defense"},
    )
    _assert_buckets(
        engine,
        "Russian forces launch ground attack in the Покровськ sector",
        {"donbas_front"},
    )
    _assert_buckets(
        engine,
        "Naval drone strike near Crimea disrupts the Ukrainian sea corridor",
        {"black_sea"},
    )
    sanctions = engine.classify(
        "Trump takes 60 days to assess potential sanctions against Russia, Rubio claims",
        "",
    )
    assert sanctions is None or "claims_intel" not in (sanctions.get("buckets") or [])


def test_aid_covers_purl_interceptors_and_support_loan():
    engine = _engine("ukraine-aid-monitor", "ukraine_aid_monitor.py")
    _assert_buckets(
        engine,
        "NATO allies procure air defence interceptors for Ukraine under PURL",
        {"weapons_aid"},
    )
    _assert_buckets(
        engine,
        "EU disburses Ukraine Support Loan tranche to Kyiv",
        {"aid_packages", "loans_finance"},
    )


def test_diplomacy_covers_energy_ceasefire_and_monitoring():
    engine = _engine("ru-diplomacy-monitor", "ru_diplomacy_monitor.py")
    _assert_buckets(
        engine,
        "Ukraine backs energy ceasefire and monitoring mechanism with Russia",
        {"talks_process", "ua_stance"},
    )
    result = engine.classify(
        "Ukraine says Russia violated the energy ceasefire and the truce is broken",
        "",
    )
    assert result is not None
    assert result["flags"].get("ceasefire_breach")


def test_russia_and_eu_cover_sanctions_circumvention_and_funding():
    russian = _engine("russia-domestic-monitor", "russia_domestic_monitor.py")
    _assert_buckets(
        russian,
        "EU sanctions Russian shadow fleet over price cap evasion",
        {"oil_exports"},
    )
    result = russian.classify(
        "Graham Act secondary tariffs on buyers of Russian oil put on hold under waiver",
        "",
    )
    assert result is not None
    assert "oil_exports" in result["buckets"]
    assert result["flags"].get("easing_measure")
    european = _engine("eu-domestic-monitor", "eu_domestic_monitor.py")
    _assert_buckets(
        european,
        "EU adopts sanctions on Russia shadow fleet circumvention",
        {"russia_policy"},
    )
    _assert_buckets(
        european,
        "European Union summit debates Ukraine Support Loan budget",
        {"eu_politics"},
    )


def test_asset_transmission_covers_gas_flows_and_black_sea_grain_route():
    engine = _engine("asset-transmission-monitor", "asset_transmission_monitor.py")
    _assert_buckets(
        engine,
        "Europe gas storage daily withdrawal accelerates",
        {"eu_gas"},
    )
    _assert_buckets(
        engine,
        "Ukraine grain export volume rises through Pivdennyi on the Black Sea corridor",
        {"grain_food"},
    )
    _assert_buckets(
        engine,
        "Ukraine drone strike damaged the Ryazan refinery and halted oil output",
        {"supply_loss"},
    )

