"""欧洲主权债收益率：TradingView日频主序列，FRED月频交叉核验。"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import socket
import subprocess
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from io import StringIO
from pathlib import Path


COLLECTORS_ROOT = Path(__file__).resolve().parents[1]
if str(COLLECTORS_ROOT) not in sys.path:
    sys.path.insert(0, str(COLLECTORS_ROOT))

from fixed_channel_common import (  # noqa: E402
    add_status,
    archive_raw,
    atomic_json,
    base_payload,
    dedupe_history,
    observation,
    source_record,
    utc_now,
)


TRADINGVIEW_SERIES = {
    "TVC:DE10Y": ("ua_aid_de10y", "德国10年期国债收益率", "tradingview-de10y"),
    "TVC:FR10Y": ("ua_aid_fr10y", "法国10年期国债收益率", "tradingview-fr10y"),
    "TVC:EU10Y": ("ua_aid_eu10y", "欧元区10年期收益率", "tradingview-eu10y"),
}
FRED_SERIES = {
    "TVC:DE10Y": ("IRLTLT01DEM156N", "fred-de10y"),
    "TVC:FR10Y": ("IRLTLT01FRM156N", "fred-fr10y"),
    "TVC:EU10Y": ("IRLTLT01EZM156N", "fred-eu10y"),
}


def _proxy_candidates() -> list[str | None]:
    """返回快速探活后的代理出口；环境配置优先，本机常见端口次之。"""
    candidates = []
    for value in urllib.request.getproxies().values():
        if isinstance(value, str) and value.startswith("http") and value not in candidates:
            candidates.append(value)
    for port in (7897, 7890, 10809, 1080, 8080, 8888, 4780, 2080, 20171, 33210):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.15):
                value = f"http://127.0.0.1:{port}"
                if value not in candidates:
                    candidates.append(value)
        except OSError:
            continue
    return candidates + [None]


def fetch_tradingview_history(symbol: str, bars: int = 800) -> tuple[bytes, list[dict]]:
    """读取TradingView Scanner的最新完整日频观测。

    Scanner返回品种自己的最后更新时间和交易时段。历史由本地geo.v6逐日累积，
    避免依赖易超时的非公开WebSocket历史协议。
    """
    del bars  # 保留稳定接口；HTTP Scanner每次返回最新观测。
    errors = []
    body = json.dumps({
        "symbols": {"tickers": [symbol], "query": {"types": []}},
        "columns": ["name", "close", "time", "current_session", "timezone"],
    }, separators=(",", ":"))
    for proxy in _proxy_candidates():
        try:
            command = [
                "curl.exe" if os.name == "nt" else "curl",
                "-L", "--fail", "--silent", "--show-error", "--max-time", "12",
                "-H", "Content-Type: application/json",
                "-H", "Origin: https://www.tradingview.com",
                "-H", "Referer: https://www.tradingview.com/",
                "--data-raw", body,
                "https://scanner.tradingview.com/global/scan",
            ]
            if proxy:
                command[1:1] = ["--proxy", proxy]
            completed = subprocess.run(command, capture_output=True, timeout=17, check=False)
            if completed.returncode:
                raise RuntimeError(completed.stderr.decode("utf-8", "replace").strip())
            raw = completed.stdout
            result = json.loads(raw.decode("utf-8"))
            rows = result.get("data") or []
            if not rows:
                raise ValueError(f"TradingView未返回{symbol}")
            values = rows[0].get("d") or []
            if len(values) < 5:
                raise ValueError(f"TradingView {symbol}字段不完整")
            close, stamp, session = values[1], values[2], values[3]
            if session == "regular":
                return raw, []
            if isinstance(close, bool) or not isinstance(close, (int, float)) or not math.isfinite(close):
                raise ValueError(f"TradingView {symbol}收盘收益率无效")
            if not isinstance(stamp, (int, float)) or stamp <= 1_000_000_000:
                raise ValueError(f"TradingView {symbol}观测时间无效")
            day = datetime.fromtimestamp(stamp, timezone.utc).date().isoformat()
            return raw, [{"date": day, "value": float(close)}]
        except Exception as exc:
            errors.append(f"{proxy or '直连'}: {type(exc).__name__}: {exc}")
    raise RuntimeError("；".join(errors)[-1200:])


def fetch_fred_monthly(series_id: str) -> tuple[bytes, list[dict]]:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    errors = []
    raw = None
    for proxy in _proxy_candidates():
        command = [
            "curl.exe" if os.name == "nt" else "curl",
            "-L", "--fail", "--silent", "--show-error", "--max-time", "5",
            "-H", "User-Agent: Mozilla/5.0 GeoAgent/6", url,
        ]
        if proxy:
            command[1:1] = ["--proxy", proxy]
        try:
            completed = subprocess.run(command, capture_output=True, timeout=8, check=False)
            if completed.returncode:
                raise RuntimeError(completed.stderr.decode("utf-8", "replace").strip())
            raw = completed.stdout
            if raw:
                break
        except Exception as exc:
            errors.append(f"{proxy or '直连'}: {type(exc).__name__}: {exc}")
    if not raw:
        raise RuntimeError("；".join(errors)[-1200:] or "FRED返回空响应")
    rows = []
    for item in csv.DictReader(StringIO(raw.decode("utf-8-sig"))):
        raw_value = str(item.get(series_id, "")).strip()
        if not raw_value or raw_value == ".":
            continue
        try:
            value = float(raw_value)
            day = date.fromisoformat(str(item.get("observation_date", ""))).isoformat()
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            rows.append({"date": day, "value": value})
    return raw, dedupe_history(rows)


def completed_daily_history(rows: list[dict], today: date | None = None) -> list[dict]:
    cutoff = today or date.today()
    completed = []
    for row in rows:
        try:
            day = date.fromisoformat(str(row.get("date")))
            value = row.get("value")
        except (TypeError, ValueError):
            continue
        if day >= cutoff or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if math.isfinite(value):
            completed.append({"date": day.isoformat(), "value": float(value)})
    return dedupe_history(completed)


def same_day_spread(france: list[dict], germany: list[dict]) -> list[dict]:
    de = {
        row["date"]: row["value"] for row in germany
        if isinstance(row.get("value"), (int, float)) and not isinstance(row.get("value"), bool)
        and math.isfinite(row["value"])
    }
    output = []
    for row in france:
        value = row.get("value")
        if row.get("date") not in de or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if math.isfinite(value):
            output.append({"date": row["date"], "value": round((value - de[row["date"]]) * 100, 6)})
    return dedupe_history(output)


def _validation(history: list[dict], series_id: str, state: str) -> dict:
    return {
        "provider": "FRED/OECD",
        "series": series_id,
        "frequency": "月度",
        "state": state,
        "latest": history[-1] if history else None,
        "role": "交叉核验，不补写TradingView日频值",
    }


def build_payload(output_root: Path, today: date | None = None) -> dict:
    output_root = Path(output_root)
    current_day = today or date.today()
    payload = base_payload("乌克兰后援：欧洲主权债收益率", utc_now())
    existing_histories: dict[str, list[dict]] = {}
    existing_file = output_root / "geo-v6.json"
    if existing_file.is_file():
        try:
            existing_payload = json.loads(existing_file.read_text(encoding="utf-8"))
            existing_histories = {
                item.get("series") or item.get("id"): item.get("history", [])
                for item in existing_payload.get("observations", []) if isinstance(item, dict)
            }
        except (OSError, ValueError, TypeError):
            existing_histories = {}
    histories: dict[str, list[dict]] = {}
    fred_histories: dict[str, list[dict]] = {}
    failures = 0

    for symbol, (_, label, source_id) in TRADINGVIEW_SERIES.items():
        link = f"https://www.tradingview.com/symbols/{symbol.replace(':', '-')}/"
        payload["records"].append(source_record(
            source_id, label, f"TradingView {symbol} 已完成交易日日线。", link,
            payload["as_of"], dimension="aid", publisher="TradingView", tier="未分级",
            verification="结构化行情已导入，未作冲突因果归因",
        ))
        try:
            raw, rows = fetch_tradingview_history(symbol)
            raw_ref = archive_raw(output_root, source_id, raw, "txt")
            rows = completed_daily_history(rows, current_day)
            oid = TRADINGVIEW_SERIES[symbol][0]
            combined = completed_daily_history(existing_histories.get(oid, []) + rows, current_day)
            if not combined:
                raise ValueError("未获得已完成交易日的有效观测")
            histories[symbol] = combined
            state = "ok" if rows else "ok-empty"
            detail = f"累计{len(combined)}期；本轮新增或修订{len(rows)}期" if rows else f"累计{len(combined)}期；当前交易时段未结束，无新增"
            add_status(payload, source_id, state, detail, raw=raw_ref)
        except Exception as exc:
            failures += 1
            oid = TRADINGVIEW_SERIES[symbol][0]
            preserved = completed_daily_history(existing_histories.get(oid, []), current_day)
            if preserved:
                histories[symbol] = preserved
            add_status(payload, source_id, "failed", str(exc)[:400])

    fred_futures = {}
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="fred-yield") as executor:
        for symbol, (series_id, source_id) in FRED_SERIES.items():
            label = TRADINGVIEW_SERIES[symbol][1]
            payload["records"].append(source_record(
                source_id, f"{label}月频核验", f"FRED/OECD {series_id} 月频基准序列。",
                f"https://fred.stlouisfed.org/series/{series_id}", payload["as_of"],
                dimension="aid", publisher="FRED/OECD", tier="T1",
            ))
            fred_futures[symbol] = executor.submit(fetch_fred_monthly, series_id)

    for symbol, (series_id, source_id) in FRED_SERIES.items():
        label = TRADINGVIEW_SERIES[symbol][1]
        try:
            raw, rows = fred_futures[symbol].result()
            raw_ref = archive_raw(output_root, source_id, raw, "csv")
            fred_histories[symbol] = rows
            state = "ok" if rows else "ok-empty"
            add_status(payload, source_id, state, f"获得{len(rows)}期月频核验值", raw=raw_ref)
        except Exception as exc:
            failures += 1
            fred_histories[symbol] = []
            add_status(payload, source_id, "failed", str(exc)[:400])

    boundary = "收益率同时受通胀、ECB政策、财政与供给影响；同步变化不能单独归因于俄乌冲突。"
    for symbol, history in histories.items():
        oid, label, tv_source = TRADINGVIEW_SERIES[symbol]
        fred_id, fred_source = FRED_SERIES[symbol]
        item = observation(
            oid, label, "%", "日频", f"TradingView {symbol} 已完成交易日收盘收益率。",
            boundary, history, [tv_source, fred_source], "aid", bucket="rates",
        )
        item["shared_market"] = False
        item["validation"] = _validation(
            fred_histories.get(symbol, []), fred_id,
            "可用" if fred_histories.get(symbol) else "暂不可用",
        )
        payload["observations"].append(item)

    spread = same_day_spread(histories.get("TVC:FR10Y", []), histories.get("TVC:DE10Y", []))
    if spread:
        item = observation(
            "ua_aid_fr_de_10y_spread", "法德10年期国债利差", "bp", "日频",
            "同日法国10年期收益率减德国10年期收益率，再乘100换算为基点。",
            "利差扩大反映法国相对德国风险溢价上升，也可能由财政、政治和债券供给驱动。",
            spread, ["tradingview-fr10y", "tradingview-de10y"], "aid", bucket="rates",
        )
        item["shared_market"] = False
        payload["observations"].append(item)

    if payload["observations"]:
        payload["collection_status"] = "partial" if failures else "ok"
    else:
        payload["collection_status"] = "failed"
    return payload


def write_payload(payload: dict, output_root: Path) -> None:
    output_root = Path(output_root)
    atomic_json(output_root / "geo-v6.json", payload)
    atomic_json(output_root / "source-status.json", {
        "as_of": payload["as_of"],
        "collection_status": payload["collection_status"],
        "sources": payload["source_status"],
    })
    atomic_json(output_root / "_source_status.json", {
        "run_at": payload["as_of"],
        "collection_status": payload["collection_status"],
        "sources": [
            {
                "id": item["source"],
                "publisher": item["source"],
                "state": item["state"],
                "error": item["detail"] if item["state"] == "failed" else None,
                "latest": None,
            }
            for item in payload["source_status"]
        ],
    })


def collect_and_write(output_root: Path, today: date | None = None) -> dict:
    payload = build_payload(output_root, today=today)
    write_payload(payload, output_root)
    return payload


def selftest() -> int:
    checks = [
        same_day_spread(
            [{"date": "2026-01-01", "value": 4.0}],
            [{"date": "2026-01-01", "value": 3.0}],
        ) == [{"date": "2026-01-01", "value": 100.0}],
        completed_daily_history(
            [{"date": "2026-01-01", "value": 3.0}, {"date": "2026-01-02", "value": 4.0}],
            date(2026, 1, 2),
        ) == [{"date": "2026-01-01", "value": 3.0}],
    ]
    print(f"[selftest] 欧洲收益率 {sum(checks)}/{len(checks)} 通过")
    return 0 if all(checks) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", "--self-test", dest="selftest", action="store_true")
    parser.add_argument("--out-dir")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    output_root = Path(args.out_dir or os.getenv("COLLECTOR_OUTPUT_ROOT") or Path(__file__).parent / "output")
    payload = collect_and_write(output_root)
    print(json.dumps({
        "collector": "ukraine-aid-monitor:european-yields",
        "collection_status": payload["collection_status"],
        "observations": len(payload["observations"]),
    }, ensure_ascii=False))
    return 0 if payload["observations"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
