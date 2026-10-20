"""Shared runtime for the five fixed, source-backed Ukraine transmission collectors."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable


UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 GeoAgent/6"
RUSSIAN_TRUSTED_SUB_CA = Path(__file__).resolve().parent / "certificates" / "russian-trusted-sub-ca-2024.crt"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def output_root(script_file: str) -> Path:
    return Path(os.getenv("COLLECTOR_OUTPUT_ROOT") or (Path(script_file).resolve().parent / "output"))


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def fetch(
    url: str, *, headers: dict[str, str] | None = None, timeout: int = 45,
    ca_cert: Path | None = None,
) -> bytes:
    request_headers = {"User-Agent": UA, "Accept": "application/json,text/html,application/xml,text/xml,*/*"}
    request_headers.update(headers or {})
    curl = "curl.exe" if os.name == "nt" else "curl"
    command = [curl, "-L", "--fail", "--silent", "--show-error", "--max-time", str(timeout)]
    if ca_cert is not None:
        if not ca_cert.is_file():
            raise FileNotFoundError(f"TLS CA certificate not found: {ca_cert}")
        command.extend(["--cacert", str(ca_cert)])
    for key, value in request_headers.items():
        command.extend(["-H", f"{key}: {value}"])
    command.append(url)
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout + 5, check=False)
        if result.returncode == 0:
            return result.stdout
        curl_error = result.stderr.decode("utf-8", "replace").strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        curl_error = str(exc)
    request = urllib.request.Request(url, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except Exception as exc:
        raise RuntimeError(f"fetch failed: {type(exc).__name__}: {exc}; curl={curl_error[:240]}") from exc


def archive_raw(root: Path, name: str, content: bytes, suffix: str) -> str:
    digest = hashlib.sha256(content).hexdigest()[:12]
    relative = Path("raw") / f"{name}-{digest}.{suffix}"
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return str(relative).replace("\\", "/")


def number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
    if not text or text in {"-", "—", "null", "None"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def point(day: str, value: Any) -> dict | None:
    parsed = number(value)
    return {"date": day, "value": parsed} if parsed is not None else None


def dedupe_history(rows: list[dict]) -> list[dict]:
    by_date = {row["date"]: row for row in rows if row and row.get("date")}
    return [by_date[key] for key in sorted(by_date)]


def source_record(
    rid: str, title: str, summary: str, link: str, fetched_at: str, *,
    dimension: str, publisher: str, tier: str = "T1", verification: str = "官方结构化数据已导入，研究含义待核验"
) -> dict:
    return {
        "id": rid, "title": title, "title_zh": title, "summary": summary, "summary_zh": summary,
        "link": link, "source_id": rid, "source_name": publisher, "publisher": publisher,
        "tier": tier, "verification": verification, "topics": ["ukraine"],
        "dimensions": {"ukraine": dimension}, "fetched_at": fetched_at,
    }


def observation(
    oid: str, name: str, unit: str, frequency: str, method: str, boundary: str,
    history: list[dict], source_ids: list[str], dimension: str, bucket: str | None = None
) -> dict:
    if bucket is None:
        if oid == "ru_cbr_key_rate":
            bucket = "rates"
        elif oid.startswith(("ru_usdrub", "ua_current", "ua_goods", "ua_reserve")):
            bucket = "fx"
        elif oid.startswith(("brent_", "ru_brent", "eu_gas", "ua_wheat", "ua_corn")):
            bucket = "commodity"
        else:
            bucket = "market"
    return {
        "id": oid, "series": oid, "name": name, "unit": unit, "frequency": frequency,
        "scope": "俄乌冲突资产传导", "topic": "ukraine", "dimension": dimension,
        "bucket": bucket,
        "method": method, "boundary": boundary, "history": dedupe_history(history),
        "source_ids": source_ids,
    }


def base_payload(name: str, fetched_at: str) -> dict:
    return {
        "schema_version": "geo.v6/1", "name": name, "as_of": fetched_at,
        "records": [], "observations": [], "source_status": [],
    }


def add_status(payload: dict, source: str, state: str, detail: str, *, raw: str | None = None) -> None:
    item = {"source": source, "state": state, "detail": detail, "checked_at": payload["as_of"]}
    if raw:
        item["raw"] = raw
    payload["source_status"].append(item)


def yahoo_chart(symbol: str, years: int = 2) -> tuple[bytes, list[dict]]:
    encoded = urllib.parse.quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range={years}y&interval=1d"
    raw = fetch(url)
    body = json.loads(raw.decode("utf-8"))
    result = body["chart"]["result"][0]
    timestamps = result.get("timestamp") or []
    closes = ((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    history = []
    for stamp, value in zip(timestamps, closes):
        if value is not None:
            day = datetime.fromtimestamp(stamp, timezone.utc).date().isoformat()
            # Yahoo may expose the still-trading daily candle.  The research
            # layer labels this series as a close, so publish completed days only.
            if day < date.today().isoformat():
                history.append({"date": day, "value": float(value)})
    return raw, dedupe_history(history)


def collect_russia(script_file: str) -> dict:
    root, now = output_root(script_file), utc_now()
    payload = base_payload("俄罗斯金融压力固定渠道", now)
    cbr_id, brent_id, budget_id = "ru-cbr-market", "ru-brent-market", "ru-budget-execution"
    payload["records"].extend([
        source_record(cbr_id, "俄罗斯央行金融条件", "官方美元兑卢布和关键利率历史数据。", "https://www.cbr.ru/development/SXML/", now, dimension="russia_domestic", publisher="俄罗斯央行"),
        source_record(brent_id, "Brent原油市场背景", "Brent近月期货收盘价，用于能源收入环境判断。", "https://finance.yahoo.com/quote/BZ%3DF/", now, dimension="russia_domestic", publisher="雅虎财经", tier="未分级"),
        source_record(budget_id, "俄罗斯联邦预算执行", "俄罗斯国库局月度联邦预算执行文件入口；不把预算计划当作实际执行。", "https://roskazna.gov.ru/ispolnenie-byudzhetov/federalnyj-byudzhet/", now, dimension="russia_domestic", publisher="俄罗斯联邦国库局"),
    ])
    rub_history: list[dict] = []
    try:
        start = (date.today() - timedelta(days=730)).strftime("%d/%m/%Y")
        end = date.today().strftime("%d/%m/%Y")
        url = f"https://www.cbr.ru/scripts/XML_dynamic.asp?date_req1={start}&date_req2={end}&VAL_NM_RQ=R01235"
        raw = fetch(url)
        raw_ref = archive_raw(root, "cbr-usd-rub", raw, "xml")
        tree = ET.fromstring(raw.decode("windows-1251", "replace"))
        for row in tree.findall(".//Record"):
            value = number(row.findtext("Value"))
            nominal = number(row.findtext("Nominal")) or 1
            if value is not None:
                rub_history.append({"date": datetime.strptime(row.attrib["Date"], "%d.%m.%Y").date().isoformat(), "value": value / nominal})
        payload["observations"].append(observation(
            "ru_usdrub_cbr", "俄罗斯央行美元兑卢布官方汇率", "卢布/美元", "日频",
            "俄罗斯央行官方每日汇率，每美元对应卢布数量。", "官方汇率不等同于可自由交易的离岸价格；资本管制会影响价格发现。",
            rub_history, [cbr_id], "russia_domestic"))
        add_status(payload, "俄罗斯央行美元兑卢布", "ok", f"获得{len(rub_history)}期", raw=raw_ref)
    except Exception as exc:
        add_status(payload, "俄罗斯央行美元兑卢布", "failed", str(exc)[:400])
    try:
        start_text = (date.today() - timedelta(days=730)).strftime("%d.%m.%Y")
        end_text = date.today().strftime("%d.%m.%Y")
        url = "https://www.cbr.ru/hd_base/KeyRate/?" + urllib.parse.urlencode({"UniDbQuery.Posted": "True", "UniDbQuery.From": start_text, "UniDbQuery.To": end_text})
        raw = fetch(url)
        raw_ref = archive_raw(root, "cbr-key-rate", raw, "html")
        text = raw.decode("utf-8", "replace")
        rate_history = []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.I | re.S):
            cells = [html.unescape(re.sub(r"<[^>]+>", "", cell)).strip() for cell in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.I | re.S)]
            if len(cells) >= 2 and re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", cells[0]):
                val = number(cells[1])
                if val is not None:
                    rate_history.append({"date": datetime.strptime(cells[0], "%d.%m.%Y").date().isoformat(), "value": val})
        if not rate_history:
            raise ValueError("官方页面未解析出关键利率表")
        payload["observations"].append(observation(
            "ru_cbr_key_rate", "俄罗斯央行关键利率", "%", "事件驱动",
            "俄罗斯央行官方关键利率历史表。", "政策利率反映通胀和金融稳定应对，也受央行制度与行政措施影响。",
            rate_history, [cbr_id], "russia_domestic"))
        add_status(payload, "俄罗斯央行关键利率", "ok", f"获得{len(rate_history)}期", raw=raw_ref)
    except Exception as exc:
        add_status(payload, "俄罗斯央行关键利率", "failed", str(exc)[:400])
    brent_history: list[dict] = []
    try:
        raw, brent_history = yahoo_chart("BZ=F")
        raw_ref = archive_raw(root, "yahoo-brent", raw, "json")
        payload["observations"].append(observation(
            "brent_russia_context", "Brent原油（俄罗斯收入环境）", "美元/桶", "日频",
            "Yahoo Finance BZ=F已完成交易日收盘价。", "Brent不是俄罗斯实际出口实现价格，不能替代Urals折价、出口量与税收数据。",
            brent_history, [brent_id], "russia_domestic"))
        add_status(payload, "Brent", "ok", f"获得{len(brent_history)}期", raw=raw_ref)
    except Exception as exc:
        add_status(payload, "Brent", "failed", str(exc)[:400])
    if rub_history and brent_history:
        rub = {x["date"]: x["value"] for x in rub_history}
        proxy = [{"date": x["date"], "value": x["value"] * rub[x["date"]]} for x in brent_history if x["date"] in rub]
        payload["observations"].append(observation(
            "ru_brent_rub_proxy", "Brent卢布计价代理", "卢布/桶", "日频",
            "同日Brent美元价格乘俄罗斯央行美元兑卢布官方汇率。", "仅表示折算环境；未扣除Urals折价、制裁成本、运费、税制和出口量。",
            proxy, [cbr_id, brent_id], "russia_domestic"))
    try:
        raw = fetch(
            "https://roskazna.gov.ru/ispolnenie-byudzhetov/federalnyj-byudzhet/",
            ca_cert=RUSSIAN_TRUSTED_SUB_CA,
        )
        raw_ref = archive_raw(root, "roskazna-budget-index", raw, "html")
        text = raw.decode("utf-8", "replace")
        zip_links = re.findall(r'href=["\']([^"\']+\.zip)(?:#[^"\']*)?["\']', text, re.I)
        add_status(payload, "俄罗斯联邦预算执行", "available", f"发现{len(zip_links)}个月度压缩包；原始入口已归档，预算表字段映射待来源格式稳定后启用", raw=raw_ref)
    except Exception as exc:
        add_status(payload, "俄罗斯联邦预算执行", "failed", f"官方站点TLS链或请求失败：{str(exc)[:300]}")
    return payload


def collect_gas(script_file: str) -> dict:
    root, now = output_root(script_file), utc_now()
    payload = base_payload("欧洲天然气库存固定渠道", now)
    rid = "gie-agsi-eu"
    payload["records"].append(source_record(rid, "欧洲天然气库存、注气和取气", "GIE AGSI按日汇总的欧盟天然气库存数据。", "https://agsi.gie.eu/", now, dimension="eu_domestic", publisher="Gas Infrastructure Europe"))
    key = os.getenv("GIE_API_KEY") or os.getenv("AGSI_API_KEY")
    if not key:
        add_status(payload, "GIE AGSI", "configuration_required", "缺少GIE_API_KEY；未用旧数据或零值替代")
        return payload
    try:
        start = (date.today() - timedelta(days=730)).isoformat()
        url = "https://agsi.gie.eu/api?" + urllib.parse.urlencode({"country": "eu", "from": start, "to": date.today().isoformat(), "size": 3000})
        raw = fetch(url, headers={"x-key": key})
        raw_ref = archive_raw(root, "agsi-eu", raw, "json")
        body = json.loads(raw.decode("utf-8"))
        if body.get("error"):
            raise RuntimeError(body.get("message") or body["error"])
        rows = body.get("data") or []
        if isinstance(rows, dict):
            rows = [rows]
        specs = {
            "eu_gas_storage_twh": ("欧洲天然气库存量", "TWh", "gasInStorage"),
            "eu_gas_storage_full": ("欧洲天然气库容使用率", "%", "full"),
            "eu_gas_injection": ("欧洲天然气当日注气量", "GWh/日", "injection"),
            "eu_gas_withdrawal": ("欧洲天然气当日取气量", "GWh/日", "withdrawal"),
        }
        for oid, (name, unit, field) in specs.items():
            history = []
            for row in rows:
                day = row.get("gasDayStart") or row.get("gasDayStartedOn") or row.get("gas_day")
                if day and number(row.get(field)) is not None:
                    history.append({"date": str(day)[:10], "value": number(row[field])})
            if history:
                payload["observations"].append(observation(oid, name, unit, "日频", f"GIE AGSI欧盟汇总字段{field}。", "运营商报告可能包含估算、修订和覆盖不完整；必须同时查看状态和完整度。", history, [rid], "eu_domestic"))
        add_status(payload, "GIE AGSI", "ok", f"获得{len(rows)}日，生成{len(payload['observations'])}项指标", raw=raw_ref)
    except Exception as exc:
        add_status(payload, "GIE AGSI", "failed", str(exc)[:400])
    return payload


def collect_grain(script_file: str) -> dict:
    root, now = output_root(script_file), utc_now()
    payload = base_payload("乌克兰粮食供需固定渠道", now)
    key = os.getenv("USDA_API_KEY") or os.getenv("DATA_GOV_API_KEY")
    commodities = {"wheat": ("0410000", "小麦"), "corn": ("0440000", "玉米")}
    for slug, (_, label) in commodities.items():
        payload["records"].append(source_record(f"usda-psd-{slug}", f"乌克兰{label}供需估计", "USDA PS&D生产、出口和期末库存估计。", "https://apps.fas.usda.gov/psdonline/", now, dimension="aid", publisher="美国农业部海外农业局"))
    if not key:
        add_status(payload, "USDA PS&D", "configuration_required", "缺少USDA_API_KEY；可从api.data.gov免费申请，未用旧数据或零值替代")
        return payload
    names = {"production": "产量", "exports": "出口估计", "ending stocks": "期末库存"}
    for slug, (code, label) in commodities.items():
        grouped: dict[str, list[dict]] = {key_: [] for key_ in names}
        units: dict[str, str] = {}
        successes = 0
        for year in range(date.today().year - 4, date.today().year + 2):
            try:
                url = f"https://api.fas.usda.gov/api/psd/commodity/{code}/country/UP/year/{year}?" + urllib.parse.urlencode({"api_key": key})
                raw = fetch(url)
                archive_raw(root, f"usda-{slug}-{year}", raw, "json")
                rows = json.loads(raw.decode("utf-8"))
                if isinstance(rows, dict):
                    rows = rows.get("data") or rows.get("results") or []
                for row in rows:
                    lower = {str(k).lower(): v for k, v in row.items()}
                    attribute = str(lower.get("attributedescription") or lower.get("attribute_description") or "").strip().lower()
                    if attribute not in grouped:
                        continue
                    market_year = str(lower.get("marketyear") or lower.get("market_year") or year)
                    value = number(lower.get("value"))
                    if value is not None:
                        grouped[attribute].append({"date": f"{market_year}-07-01", "value": value})
                        units[attribute] = str(lower.get("unitdescription") or lower.get("unit_description") or "千吨")
                successes += 1
            except Exception as exc:
                add_status(payload, f"USDA {label} {year}", "failed", str(exc)[:260])
        for attribute, history in grouped.items():
            if history:
                payload["observations"].append(observation(
                    f"ua_{slug}_{attribute.replace(' ', '_')}", f"乌克兰{label}{names[attribute]}", units.get(attribute, "千吨"), "月度修订/市场年度",
                    f"USDA PS&D乌克兰{label}{names[attribute]}，按市场年度记录并随月报修订。", "这是供需估计，不是港口当日实际发运；同一市场年度新预测会修订历史值。",
                    history, [f"usda-psd-{slug}"], "aid"))
        add_status(payload, f"USDA {label}", "ok" if successes else "failed", f"成功请求{successes}个市场年度")
    return payload


def collect_nbu(script_file: str) -> dict:
    root, now = output_root(script_file), utc_now()
    payload = base_payload("乌克兰外部收支固定渠道", now)
    rid = "nbu-balance-of-payments"
    link = "https://bank.gov.ua/NBUStatService/v1/statdirectory/balanceofpayments?json"
    payload["records"].append(source_record(rid, "乌克兰国际收支", "乌克兰国家银行月度国际收支结构化数据。", link, now, dimension="aid", publisher="乌克兰国家银行"))
    try:
        start = (date.today().replace(day=1) - timedelta(days=730)).strftime("%Y%m%d")
        url = f"https://bank.gov.ua/NBUStatService/v1/statdirectory/balanceofpayments?start={start}&json"
        raw = fetch(url, timeout=90)
        raw_ref = archive_raw(root, "nbu-balance-of-payments", raw, "json")
        rows = json.loads(raw.decode("utf-8-sig"))
        specs = {
            "Cur_acc": ("ua_current_account", "乌克兰经常账户余额", "total"),
            "G": ("ua_goods_balance", "乌克兰货物贸易余额", "total"),
            "G_S": ("ua_goods_services_balance", "乌克兰货物与服务余额", "total"),
            "RA": ("ua_reserve_asset_flow", "乌克兰储备资产交易变化", "#"),
        }
        for code, (oid, name, flow) in specs.items():
            history = []
            for row in rows:
                if row.get("id_api") != code or row.get("tzep") != "D021USD_7" or str(row.get("t023")) != flow:
                    continue
                raw_day = str(row.get("dt") or "")
                if len(raw_day) == 8 and number(row.get("value")) is not None:
                    history.append({"date": datetime.strptime(raw_day, "%Y%m%d").date().isoformat(), "value": number(row["value"])})
            if history:
                payload["observations"].append(observation(oid, name, "百万美元", "月度", f"NBU Balance of Payments，id_api={code}，美元口径。", "国际收支数据会修订；储备资产交易变化不等于期末国际储备余额。", history, [rid], "aid"))
        add_status(payload, "乌克兰国家银行国际收支", "ok", f"解析{len(rows)}行，生成{len(payload['observations'])}项指标", raw=raw_ref)
    except Exception as exc:
        add_status(payload, "乌克兰国家银行国际收支", "failed", str(exc)[:400])
    return payload


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            self.parts.append(value)


def eurostat_series(product: str, flow: str, start: str) -> tuple[bytes, list[dict]]:
    base = "https://ec.europa.eu/eurostat/api/comext/dissemination/statistics/1.0/data/DS-045409"
    params = {
        "lang": "en", "freq": "M", "reporter": "EU27_2020", "partner": "UA",
        "product": product, "flow": flow, "indicators": "VALUE_IN_EUROS", "sinceTimePeriod": start,
    }
    raw = fetch(base + "?" + urllib.parse.urlencode(params), timeout=90)
    body = json.loads(raw.decode("utf-8"))
    values = body.get("value") or {}
    time_cat = body["dimension"]["time"]["category"]
    index = time_cat.get("index") or {}
    history = []
    for period, position in index.items():
        value = values.get(str(position))
        if value is not None:
            history.append({"date": f"{period}-01", "value": float(value) / 1_000_000})
    return raw, history


def collect_eu_flow(script_file: str) -> dict:
    root, now = output_root(script_file), utc_now()
    payload = base_payload("欧乌贸易与欧盟实际拨款固定渠道", now)
    trade_id, aid_id = "eurostat-eu-ukraine-trade", "ec-ukraine-facility"
    payload["records"].extend([
        source_record(trade_id, "欧盟与乌克兰商品贸易", "Eurostat Comext按月商品贸易价值。", "https://ec.europa.eu/eurostat/databrowser/view/DS-045409/", now, dimension="aid", publisher="欧盟统计局"),
        source_record(aid_id, "欧盟对乌克兰实际拨款", "欧盟委员会Ukraine Facility付款时间线，仅提取明确写为已拨付的款项。", "https://commission.europa.eu/topics/eu-solidarity-ukraine/eu-assistance-ukraine/ukraine-facility_en", now, dimension="aid", publisher="欧盟委员会"),
    ])
    start = f"{date.today().year - 3}-01"
    trade_specs = [
        ("eu_imports_from_ukraine", "欧盟自乌克兰进口额", "TOTAL", "1"),
        ("eu_exports_to_ukraine", "欧盟向乌克兰出口额", "TOTAL", "2"),
        ("eu_cereal_imports_from_ukraine", "欧盟自乌克兰进口谷物额", "10", "1"),
        ("eu_energy_exports_to_ukraine", "欧盟向乌克兰出口能源产品额", "27", "2"),
    ]
    for oid, name, product, flow in trade_specs:
        try:
            raw, history = eurostat_series(product, flow, start)
            raw_ref = archive_raw(root, f"eurostat-{product}-{flow}", raw, "json")
            if not history:
                raise ValueError("接口返回空序列")
            payload["observations"].append(observation(oid, name, "百万欧元", "月度", f"Eurostat DS-045409，EU27_2020，伙伴UA，HS产品{product}，流向{flow}。", "贸易数据可能修订；金额变化同时受价格、数量和汇率影响。", history, [trade_id], "aid"))
            add_status(payload, f"Eurostat {oid}", "ok", f"获得{len(history)}期", raw=raw_ref)
        except Exception as exc:
            add_status(payload, f"Eurostat {oid}", "failed", str(exc)[:300])
    try:
        url = "https://commission.europa.eu/topics/eu-solidarity-ukraine/eu-assistance-ukraine/ukraine-facility_en"
        raw = fetch(url)
        raw_ref = archive_raw(root, "ec-ukraine-facility", raw, "html")
        parser = TextExtractor()
        parser.feed(raw.decode("utf-8", "replace"))
        text = " ".join(parser.parts)
        if "Sorry -" in text or "Access Denied" in text:
            raise RuntimeError("欧盟委员会主时间线返回访问拦截页")
        # The prose between a timeline date and the payment sentence changes
        # often.  Bound each match by the next date instead of a brittle fixed
        # character distance and accept the documented bridge-financing form.
        date_re = re.compile(r"\b(\d{1,2}\s+[A-Za-z]+\s+20\d{2})\b")
        dated = list(date_re.finditer(text))
        by_day: dict[str, float] = {}
        amount_re = re.compile(
            r"(?:Disbursement of|disburses?|released?)"
            r".{0,160}?(?:nearly |over |around |an additional |the first )*"
            r"€\s?([0-9]+(?:\.[0-9]+)?)\s+billion",
            re.I,
        )
        for index, match in enumerate(dated):
            segment = text[match.end(): dated[index + 1].start() if index + 1 < len(dated) else len(text)]
            amount = amount_re.search(segment)
            if not amount:
                continue
            try:
                day = datetime.strptime(match.group(1), "%d %B %Y").date().isoformat()
            except ValueError:
                continue
            by_day[day] = by_day.get(day, 0.0) + float(amount.group(1))
        payments = [{"date": day, "value": value} for day, value in sorted(by_day.items())]
        if not payments:
            raise ValueError("官方时间线未解析出明确已拨付款项")
        payload["observations"].append(observation(
            "eu_ukraine_facility_disbursement", "欧盟Ukraine Facility单笔实际拨款", "十亿欧元", "事件驱动",
            "只提取欧盟委员会时间线中明确使用拨付语义的付款；同日多笔合并。", "不包含承诺、批准、担保和预计投资；仍需与乌克兰财政部到账记录交叉核对。",
            payments, [aid_id], "aid"))
        add_status(payload, "欧盟Ukraine Facility", "ok", f"提取{len(payments)}个付款日", raw=raw_ref)
    except Exception as primary_exc:
        # The Commission front door sometimes serves a bot-challenge page.
        # Its Enlargement directorate publishes an official, simpler page with
        # the cumulative Pillar-I amount, which preserves the actual-disbursed
        # distinction while keeping the collector useful.
        try:
            fallback_url = "https://enlargement.ec.europa.eu/funding-technical-assistance/ukraine-facility_en"
            raw = fetch(fallback_url)
            raw_ref = archive_raw(root, "ec-ukraine-facility-fallback", raw, "html")
            parser = TextExtractor()
            parser.feed(raw.decode("utf-8", "replace"))
            text = " ".join(parser.parts)
            match = re.search(
                r"Since\s+2024.{0,220}?disbursed\s+(?:in total\s+)?EUR\s*([0-9]+(?:\.[0-9]+)?)\s*billion",
                text,
                re.I,
            )
            if not match:
                raise ValueError("官方备用页未解析出累计实际拨款")
            payload["observations"].append(observation(
                "eu_ukraine_facility_disbursed_cumulative", "欧盟Ukraine Facility累计实际拨款", "十亿欧元", "页面更新时",
                "欧盟委员会DG ENEST官方页面披露的Pillar I累计已拨付金额，按采集日形成快照。",
                "备用页未披露每笔付款日期；数值可能晚于实际支付日，不能与单笔事件序列混用。",
                [{"date": date.today().isoformat(), "value": float(match.group(1))}], [aid_id], "aid"))
            add_status(payload, "欧盟Ukraine Facility", "ok_fallback", f"主时间线不可访问（{primary_exc}）；已从DG ENEST备用页取得累计实际拨款", raw=raw_ref)
        except Exception as fallback_exc:
            add_status(payload, "欧盟Ukraine Facility", "failed", f"主时间线：{primary_exc}；官方备用页：{fallback_exc}"[:400])
    return payload


COLLECTORS: dict[str, Callable[[str], dict]] = {
    "russia": collect_russia,
    "gas": collect_gas,
    "grain": collect_grain,
    "nbu": collect_nbu,
    "eu-flow": collect_eu_flow,
}


def validate_payload(payload: dict) -> None:
    assert payload["schema_version"] == "geo.v6/1"
    record_ids = {row["id"] for row in payload["records"]}
    assert len(record_ids) == len(payload["records"])
    for obs in payload["observations"]:
        assert obs["source_ids"] and set(obs["source_ids"]).issubset(record_ids)
        dates = [row["date"] for row in obs["history"]]
        assert dates == sorted(set(dates))


def run(kind: str, script_file: str) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        fixture = base_payload("selftest", utc_now())
        fixture["records"].append(source_record("source", "测试", "测试", "https://example.org", fixture["as_of"], dimension="aid", publisher="测试"))
        fixture["observations"].append(observation("metric", "测试", "%", "日频", "测试", "测试", [{"date": "2026-01-01", "value": 1}], ["source"], "aid"))
        validate_payload(fixture)
        print(f"[selftest] {kind}: ok")
        return 0
    root = output_root(script_file)
    payload = COLLECTORS[kind](script_file)
    validate_payload(payload)
    atomic_json(root / "geo-v6.json", payload)
    atomic_json(root / "source-status.json", {"as_of": payload["as_of"], "sources": payload["source_status"]})
    # Native monitor projection used by 数据源 -> 来源状态.
    atomic_json(root / "_source_status.json", {
        "run_at": payload["as_of"],
        "proxy": "无（直连）",
        "sources": [
            {
                "id": item["source"],
                "publisher": item["source"],
                "state": "ok" if item["state"] == "ok_fallback" else item["state"],
                "error": item["detail"] if item["state"] not in {"ok", "ok_fallback", "available"} else None,
            }
            for item in payload["source_status"]
        ],
    })
    print(json.dumps({"collector": kind, "records": len(payload["records"]), "observations": len(payload["observations"]), "status": payload["source_status"]}, ensure_ascii=False))
    return 0

