# -*- coding: utf-8 -*-
"""
parse.py —— 载荷解析（零第三方依赖）

每个解析器都返回 (data:dict, meta:dict)：
  data = {YYYY-MM-DD: float}  或  {YYYY-MM-DD: {field: value}}
  meta = {n, first_date, last_date, last_value, notes...}

解析纪律（对应 README §5 的陷阱）：
  * 列名陷阱：写错列名只会「列名不存在」，不会报错成别的样子。
  * 降序响应：按日期排序后取末条，不要拿第一行当 latest。
  * 纪元零值哨兵：缺日期的 feed 给 1970-01-01；epoch 转换对 0 返回 None。
  * 空白值：FRED 用 "." 表示缺值，必须跳过而不是当 0。
"""
import csv
import io
import json
import re
from datetime import datetime, timezone


def _f(v):
    """把字符串转 float，缺值返回 None。"""
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", ".", "NA", "N/A", "null", "None", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _epoch_ms_to_date(ms):
    """ArcGIS date 字段 -> YYYY-MM-DD。epoch 0 视为哨兵，返回 None。"""
    try:
        ms = int(ms)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# FRED fredgraph.csv
# ---------------------------------------------------------------------------

def parse_fred_csv(body, date_col=None, value_col=None):
    """FRED fredgraph.csv -> {date: value}。

    头两列固定为 observation_date,<SERIES_ID>。value_col 仅作断言用。
    """
    text = body.decode("utf-8-sig", "replace")
    rdr = csv.reader(io.StringIO(text))
    try:
        header = next(rdr)
    except StopIteration:
        return {}, {"n": 0, "error": "empty-csv"}
    header = [h.strip() for h in header]
    if date_col and header and header[0] != date_col:
        # 不致命：FRED 偶尔改列名，按位置读
        pass
    if value_col and len(header) > 1 and header[1] != value_col:
        return {}, {"n": 0, "error": "value-col-mismatch: got %r want %r"
                    % (header[1], value_col)}

    out = {}
    skipped = 0
    for row in rdr:
        if len(row) < 2:
            continue
        d = row[0].strip()
        if not d or d.startswith("19") and len(d) == 4:
            continue
        v = _f(row[1])
        if v is None:
            skipped += 1
            continue
        out[d] = v
    return out, _summarize(out, header=header, skipped=skipped)


# ---------------------------------------------------------------------------
# Datawrapper CSV（特朗普支持率）
# ---------------------------------------------------------------------------

def parse_dw_csv(body, date_col="modeldate", value_col="approve",
                 date_format="%m/%d/%Y"):
    """Datawrapper 导出 CSV -> {date: approve}。"""
    text = body.decode("utf-8-sig", "replace")
    rdr = csv.DictReader(io.StringIO(text))
    if rdr.fieldnames is None:
        return {}, {"n": 0, "error": "no-header"}
    fields = [f.strip() for f in rdr.fieldnames]
    if date_col not in fields:
        return {}, {"n": 0, "error": "no-date-col:%s (have %s)"
                    % (date_col, fields)}
    if value_col not in fields:
        return {}, {"n": 0, "error": "no-value-col:%s (have %s)"
                    % (value_col, fields)}

    out = {}
    extra = {}
    bad_dates = 0
    for row in rdr:
        raw = (row.get(date_col) or "").strip()
        if not raw:
            continue
        try:
            d = datetime.strptime(raw, date_format).strftime("%Y-%m-%d")
        except ValueError:
            bad_dates += 1
            continue
        v = _f(row.get(value_col))
        if v is None:
            continue
        out[d] = v
        dis = _f(row.get("disapprove"))
        if dis is not None:
            extra.setdefault("disapprove", {})[d] = dis
    meta = _summarize(out, fields=fields, bad_dates=bad_dates)
    if "disapprove" in extra:
        meta["disapprove"] = extra["disapprove"]
    return out, meta


# ---------------------------------------------------------------------------
# ArcGIS FeatureServer JSON（IMF PortWatch）
# ---------------------------------------------------------------------------

def parse_arcgis_json(body, date_field="date", fields=None):
    """ArcGIS query f=json -> {date: {field: value}}。

    date_field 可能是 esriFieldTypeDate（epoch ms）或 DateOnly（字符串）。
    两种都要吃下。
    """
    try:
        d = json.loads(body.decode("utf-8"))
    except Exception as e:                                   # noqa: BLE001
        return {}, {"n": 0, "error": "json-parse:%s" % str(e)[:120]}
    if "error" in d:
        return {}, {"n": 0, "error": "arcgis-error:%s" % str(d["error"])[:200]}
    feats = d.get("features")
    if feats is None:
        return {}, {"n": 0, "error": "no-features-key"}

    fields = fields or ["n_total", "n_tanker", "capacity"]
    out = {}
    dropped = 0
    for ft in feats:
        attrs = ft.get("attributes") or {}
        raw = attrs.get(date_field)
        if raw is None:
            dropped += 1
            continue
        if isinstance(raw, (int, float)):
            ds = _epoch_ms_to_date(raw)
        else:
            ds = str(raw).strip()[:10]
            if not ds or ds.startswith("1970-01-01"):
                ds = None
        if not ds:
            dropped += 1
            continue
        rec = {}
        for f in fields:
            v = attrs.get(f)
            rec[f] = None if v is None else (int(v) if isinstance(v, float)
                                             and v == int(v) else v)
        out[ds] = rec

    dates = sorted(out)
    meta = {
        "n": len(out),
        "dropped": dropped,
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
        "fields": fields,
        "exceeded_transfer_limit": d.get("exceededTransferLimit", False),
    }
    if dates:
        last_rec = out[dates[-1]]
        meta["last_record"] = last_rec
        # last_value 取主计数（n_total），便于统一展示
        if isinstance(last_rec, dict):
            meta["last_value"] = (last_rec.get("n_total")
                                  if last_rec.get("n_total") is not None
                                  else next(iter(last_rec.values()), None))
        else:
            meta["last_value"] = last_rec
        # 最近若干日明细（供报告 §5 上下文表）
        meta["recent"] = [dict(date=dd, **out[dd]) for dd in dates[::-1][:10]]
    return out, meta


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------

def _summarize(out, **kw):
    dates = sorted(out)
    meta = {
        "n": len(out),
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
    }
    if dates:
        meta["last_value"] = out[dates[-1]]
    meta.update(kw)
    return meta


# ---------------------------------------------------------------------------
# 新浪财经 外盘期货日K（JSONP）
# ---------------------------------------------------------------------------

def parse_sina_futures_jsonp(body, value_field="close", drop_from=None):
    """新浪外盘期货日K JSONP -> {date: value}。

    响应形如::

        /*<script>location.href='//sina.com';</script>*/
        var t=([{"date":"2016-09-22","open":"46.990","close":"47.500",...}, ...]);

    纪律：
      * 载荷不是 JSON，外面包了一层 JSONP 壳，必须先剥壳再 json.loads。
      * **当日会话未收盘**：新浪按北京时间标注会话日期，运行当天那一行是
        「进行中」的盘中价（实测 09-22 08:32 该行 high-low 仅 0.3 美元，
        而 09-21 完整会话振幅 5.1 美元）。drop_from 传运行日期即可丢弃它，
        否则每天会把一个未走完的盘中价当成收盘价写进指数。
    """
    try:
        text = body.decode("utf-8", "replace")
    except Exception:                                        # noqa: BLE001
        return {}, {"n": 0, "error": "decode-failed"}
    m = re.search(r"var\s+t=\((\[.*\])\)", text, re.S)
    if not m:
        return {}, {"n": 0, "error": "jsonp-envelope-not-found"}
    try:
        rows = json.loads(m.group(1))
    except Exception as e:                                   # noqa: BLE001
        return {}, {"n": 0, "error": "jsonp-json-parse:%s" % str(e)[:80]}

    out = {}
    dropped_inprogress = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        d = (r.get("date") or "").strip()
        if len(d) != 10 or d[4] != "-":
            continue
        if drop_from and d >= drop_from:
            dropped_inprogress += 1
            continue
        v = _f(r.get(value_field))
        if v is not None:
            out[d] = v

    dates = sorted(out)
    meta = {"n": len(out), "rows_raw": len(rows),
            "dropped_inprogress": dropped_inprogress,
            "value_field": value_field}
    if dates:
        meta["first_date"] = dates[0]
        meta["last_date"] = dates[-1]
        meta["last_value"] = out[dates[-1]]
    return out, meta


# ---------------------------------------------------------------------------
# Yahoo Finance chart API（BZ=F 等）
# ---------------------------------------------------------------------------

def parse_yahoo_chart(body, value_field="close", drop_from=None):
    """Yahoo Finance chart API -> {date: value}。

    响应形如::

        {"chart":{"result":[{"meta":{...,"gmtoffset":-14400,"symbol":"BZ=F"},
          "timestamp":[1727064000,...],
          "indicators":{"quote":[{"close":[73.9,...]}]}}],"error":null}}

    纪律：
      * timestamp 是 UTC **秒**，不是毫秒 —— 别套用 ArcGIS 的毫秒分支。
      * 归属日必须按**交易所本地时区**取（用 meta.gmtoffset 偏移）。
        BZ=F 在夏令时下 bar 起点为 04:00Z，直接按 UTC 切日期恰好同一天；
        但换到冬令时（EST, -5h）就变成 05:00Z，若照抄 UTC 会在跨日边界
        上错一天。按 gmtoffset 偏移后再取日期，两种时令都对。
      * close 可以是 null（假期 / 合约切换日无成交），必须跳过而不是当 0。
      * **当日未收盘**：运行当天那一根是盘中价。drop_from 传运行日期即可
        丢弃它，否则每天会把一个没走完的盘中价当收盘价写进指数。
      * 载荷可能同时带 open/high/low/close/volume，value_field 指定取哪一列。
    """
    try:
        doc = json.loads(body.decode("utf-8"))
    except Exception as e:                                   # noqa: BLE001
        return {}, {"n": 0, "error": "json-parse:%s" % str(e)[:120]}
    chart = doc.get("chart")
    if not isinstance(chart, dict):
        return {}, {"n": 0, "error": "no-chart-key"}
    if chart.get("error"):
        return {}, {"n": 0, "error": "yahoo-error:%s" % str(chart["error"])[:200]}
    results = chart.get("result") or []
    if not results:
        return {}, {"n": 0, "error": "empty-result"}
    r0 = results[0]
    meta_in = r0.get("meta") or {}
    try:
        gmtoffset = int(meta_in.get("gmtoffset") or 0)
    except (TypeError, ValueError):
        gmtoffset = 0
    ts = r0.get("timestamp") or []
    quote = ((r0.get("indicators") or {}).get("quote") or [{}])[0]
    vals = quote.get(value_field) or []

    out = {}
    dropped_null = 0
    dropped_inprogress = 0
    bad_rows = 0
    for i, t in enumerate(ts):
        if i >= len(vals):
            break
        v = _f(vals[i])
        if v is None:
            dropped_null += 1
            continue
        try:
            ds = datetime.fromtimestamp(
                int(t) + gmtoffset, tz=timezone.utc).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OverflowError, OSError):
            bad_rows += 1
            continue
        if drop_from and ds >= drop_from:
            dropped_inprogress += 1
            continue
        out[ds] = v

    dates = sorted(out)
    meta = {
        "n": len(out),
        "rows_raw": len(ts),
        "dropped_null": dropped_null,
        "dropped_inprogress": dropped_inprogress,
        "bad_rows": bad_rows,
        "value_field": value_field,
        "symbol": meta_in.get("symbol"),
        "exchange": meta_in.get("exchangeName"),
        "full_exchange": meta_in.get("fullExchangeName"),
        "instrument_type": meta_in.get("instrumentType"),
        "currency": meta_in.get("currency"),
        "timezone": meta_in.get("exchangeTimezoneName"),
        "regular_market_price": meta_in.get("regularMarketPrice"),
        "regular_market_time": meta_in.get("regularMarketTime"),
    }
    if dates:
        meta["first_date"] = dates[0]
        meta["last_date"] = dates[-1]
        meta["last_value"] = out[dates[-1]]
    return out, meta


# ---------------------------------------------------------------------------
# 分发
# ---------------------------------------------------------------------------

def parse_payload(body, series_cfg):
    """按 series_cfg['payload'] 分发解析。"""
    kind = series_cfg.get("payload", "csv")
    if kind == "csv":
        prov = (series_cfg.get("provider") or "").lower()
        if "datawrapper" in prov or series_cfg.get("date_format"):
            return parse_dw_csv(body,
                                date_col=series_cfg.get("date_col", "modeldate"),
                                value_col=series_cfg.get("value_col", "approve"),
                                date_format=series_cfg.get("date_format",
                                                           "%m/%d/%Y"))
        return parse_fred_csv(body,
                              date_col=series_cfg.get("date_col"),
                              value_col=series_cfg.get("value_col"))
    if kind == "sina_jsonp":
        return parse_sina_futures_jsonp(
            body,
            value_field=series_cfg.get("value_col", "close"),
            drop_from=series_cfg.get("_drop_from"))
    if kind == "yahoo_chart":
        return parse_yahoo_chart(
            body,
            value_field=series_cfg.get("value_col", "close"),
            drop_from=series_cfg.get("_drop_from"))
    if kind == "arcgis_json":
        return parse_arcgis_json(body,
                                 date_field=series_cfg.get("date_col", "date"),
                                 fields=list(
                                     (series_cfg.get("_fields") or {}).values()
                                 ) or None)
    if kind == "json":
        try:
            return json.loads(body.decode("utf-8")), {"n": 1}
        except Exception as e:                               # noqa: BLE001
            return {}, {"n": 0, "error": str(e)[:120]}
    return {}, {"n": 0, "error": "unknown-payload-kind:%s" % kind}
