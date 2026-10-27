"""Long-running login, token refresh and three-times-daily message collector."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.internal_messages import (
    CHINA_TZ,
    message_db_path,
    message_stats,
    next_scheduled_fetch,
    parse_fetch_times,
    raw_archive_root,
    record_run_finish,
    record_run_start,
    status_path,
    upsert_messages,
    utc_now,
)


def load_env(path: Path | None = None) -> None:
    target = path or ROOT / ".env"
    if not target.is_file():
        return
    for line in target.read_text(encoding="utf-8-sig").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"'))


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    base_url: str
    region: str
    username: str
    password: str
    client_category: str
    refresh_interval_sec: int = 1620
    page_size: int = 20
    max_pages: int = 100
    timeout_sec: float = 30
    enabled: bool = False
    fetch_times: str = "08:00,14:00,20:00"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            base_url=os.getenv("HT_MACRO_BASE_URL", "").strip().rstrip("/"),
            region=os.getenv("HT_MACRO_REGION", "+86").strip(),
            username=os.getenv("HT_MACRO_USERNAME", "").strip(),
            password=os.getenv("HT_MACRO_PASSWORD", "").strip(),
            client_category=os.getenv("HT_MACRO_CLIENT_CATEGORY", "1").strip(),
            refresh_interval_sec=int(os.getenv("HT_MACRO_REFRESH_INTERVAL_SEC", "1620")),
            page_size=int(os.getenv("HT_MACRO_PAGE_SIZE", "20")),
            max_pages=int(os.getenv("HT_MACRO_MAX_PAGES", "100")),
            timeout_sec=float(os.getenv("HT_MACRO_TIMEOUT_SEC", "30")),
            enabled=_enabled(os.getenv("HT_MACRO_ENABLED", "false")),
            fetch_times=os.getenv("HT_MACRO_FETCH_TIMES", "08:00,14:00,20:00").strip(),
        )

    def validate(self) -> None:
        if not self.enabled:
            return
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("HT_MACRO_BASE_URL 必须使用 http:// 或 https://")
        if not self.username or not self.password:
            raise ValueError("HT_MACRO_USERNAME / HT_MACRO_PASSWORD 未配置")
        if self.refresh_interval_sec < 60:
            raise ValueError("HT_MACRO_REFRESH_INTERVAL_SEC 必须至少为 60")
        if not 1 <= self.page_size <= 200:
            raise ValueError("HT_MACRO_PAGE_SIZE 必须在 1 到 200 之间")
        if not 1 <= self.max_pages <= 1000:
            raise ValueError("HT_MACRO_MAX_PAGES 必须在 1 到 1000 之间")
        parse_fetch_times(self.fetch_times)


def _extract_token(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key in ("access_token", "accessToken"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for value in payload.values():
            found = _extract_token(value)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _extract_token(value)
            if found:
                return found
    return None


def _safe_error(exc: BaseException, secrets: tuple[str, ...] = ()) -> str:
    text = f"{type(exc).__name__}: {str(exc)}"
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<redacted>")
    text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/-]+", "Bearer <redacted>", text)
    return text[:500]


def _ensure_success(response: httpx.Response) -> dict[str, Any]:
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("接口未返回 JSON 对象")
    code = str(payload.get("code", ""))
    if payload.get("status") is False or (code and code != "000000"):
        raise ValueError(f"上游接口失败: code={code or 'unknown'} msg={str(payload.get('msg', ''))[:200]}")
    return payload


class MessageApi:
    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.client = httpx.Client(
            base_url=settings.base_url,
            timeout=settings.timeout_sec,
            transport=transport,
            follow_redirects=False,
        )
        self.access_token = ""

    def close(self) -> None:
        self.client.close()

    def login(self) -> None:
        response = self.client.post(
            "/application/auth/oauth/two-stage/login",
            json={
                "region": self.settings.region,
                "username": self.settings.username,
                "password": self.settings.password,
                "clientCategory": self.settings.client_category,
            },
        )
        payload = _ensure_success(response)
        token = _extract_token(payload)
        if not token:
            raise ValueError("登录响应缺少 access_token")
        self.access_token = token

    def _headers(self) -> dict[str, str]:
        if not self.access_token:
            raise RuntimeError("尚未登录")
        return {"Authorization": f"Bearer {self.access_token}"}

    def refresh(self) -> None:
        current = self.access_token
        response = self.client.post(
            "/application/auth/oauth/refresh/token",
            headers=self._headers(),
            json={"accessToken": current},
        )
        payload = _ensure_success(response)
        self.access_token = _extract_token(payload) or current

    def fetch_page(self, page_num: int) -> dict[str, Any]:
        body = {
            "pageNum": page_num,
            "pageSize": self.settings.page_size,
            "msgTypes": ["WX"],
            "infoFlowSource": "external",
            "isImportant": None,
            "isShowMyCncpt": 0,
            "queryMsgType": 3,
        }
        response = self.client.post(
            "/application/lgrp-server/lgrp/api/msg/list",
            headers=self._headers(),
            json=body,
        )
        if response.status_code in (401, 403):
            self.login()
            response = self.client.post(
                "/application/lgrp-server/lgrp/api/msg/list",
                headers=self._headers(),
                json=body,
            )
        return _ensure_success(response)


def _page_data(payload: dict[str, Any]) -> tuple[int | None, list[dict[str, Any]]]:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("消息接口缺少 data 对象")
    items = data.get("list")
    if not isinstance(items, list):
        raise ValueError("消息接口缺少 data.list 数组")
    valid = [item for item in items if isinstance(item, dict)]
    try:
        total = int(data.get("total"))
    except (TypeError, ValueError):
        total = None
    return total, valid


def _archive_page(run_id: str, page_num: int, payload: dict[str, Any]) -> Path:
    now = datetime.now(timezone.utc)
    directory = raw_archive_root() / now.strftime("%Y-%m-%d") / run_id
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"page-{page_num:04d}.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    return target


def collect_once(api: MessageApi) -> dict[str, Any]:
    run_id = "htm-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    record_run_start(run_id)
    result: dict[str, Any] = {
        "run_id": run_id, "status": "running", "pages": 0, "received": 0,
        "inserted": 0, "updated": 0, "duplicates": 0, "upstream_total": None,
    }
    try:
        for page_num in range(1, api.settings.max_pages + 1):
            payload = api.fetch_page(page_num)
            _archive_page(run_id, page_num, payload)
            total, records = _page_data(payload)
            if result["upstream_total"] is None and total is not None:
                result["upstream_total"] = total
            if not records:
                break
            counts = upsert_messages(records)
            result["pages"] += 1
            for key in ("received", "inserted", "updated", "duplicates"):
                result[key] += counts[key]
            if total is not None and page_num * api.settings.page_size >= total:
                break
            if len(records) < api.settings.page_size:
                break
        result["status"] = "succeeded"
    except Exception as exc:
        result.update(
            status="failed",
            error_type=type(exc).__name__,
            error_message=_safe_error(exc, (api.settings.password, api.access_token)),
        )
    finally:
        record_run_finish(run_id, result)
    return result


def write_status(payload: dict[str, Any]) -> None:
    target = status_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    public = {key: value for key, value in payload.items() if key not in {"access_token", "password", "username"}}
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def run_service(settings: Settings, *, once: bool = False) -> int:
    settings.validate()
    if not settings.enabled:
        write_status({"state": "disabled", "updated_at": utc_now(), "message": "HT_MACRO_ENABLED 未开启"})
        if once:
            return 0
        while True:
            time.sleep(60)
    api = MessageApi(settings)
    fetch_times = parse_fetch_times(settings.fetch_times)
    status: dict[str, Any] = {"state": "starting", "started_at": utc_now(), "database": str(message_db_path())}
    try:
        try:
            api.login()
            status.update(state="authenticated", last_login_at=utc_now(), last_error=None)
            write_status(status)
        except Exception as exc:
            status.update(state="authentication_failed", last_error=_safe_error(exc, (settings.password, api.access_token)))
            write_status(status)
            if once:
                return 1
            while not api.access_token:
                time.sleep(60)
                try:
                    api.login()
                    status.update(state="authenticated", last_login_at=utc_now(), last_error=None)
                    write_status(status)
                except Exception as retry_exc:
                    status.update(
                        state="authentication_failed",
                        last_error=_safe_error(retry_exc, (settings.password, api.access_token)),
                    )
                    write_status(status)
        result = collect_once(api)
        status.update(
            state="running" if result["status"] == "succeeded" else "collection_failed",
            last_collection_at=utc_now(), last_collection=result,
            stats=message_stats(), last_error=result.get("error_message"),
        )
        write_status(status)
        if once:
            return 0 if result["status"] == "succeeded" else 1

        next_refresh = datetime.now(timezone.utc) + timedelta(seconds=settings.refresh_interval_sec)
        next_fetch = next_scheduled_fetch(datetime.now(timezone.utc), fetch_times).astimezone(timezone.utc)
        while True:
            now = datetime.now(timezone.utc)
            if now >= next_refresh:
                try:
                    api.refresh()
                    status.update(state="running", last_refresh_at=utc_now(), last_error=None)
                except Exception as exc:
                    try:
                        api.login()
                        status.update(state="running", last_login_at=utc_now(), last_error=None)
                    except Exception as login_exc:
                        status.update(
                            state="authentication_failed",
                            last_error=_safe_error(login_exc, (settings.password, api.access_token)),
                        )
                next_refresh = now + timedelta(seconds=settings.refresh_interval_sec)
                write_status(status)
            if now >= next_fetch:
                result = collect_once(api)
                status.update(
                    state="running" if result["status"] == "succeeded" else "collection_failed",
                    last_collection_at=utc_now(), last_collection=result,
                    stats=message_stats(), last_error=result.get("error_message"),
                )
                next_fetch = next_scheduled_fetch(datetime.now(timezone.utc), fetch_times).astimezone(timezone.utc)
                write_status(status)
            status["next_refresh_at"] = next_refresh.isoformat()
            status["next_collection_at"] = next_fetch.isoformat()
            write_status(status)
            delay = min((next_refresh - now).total_seconds(), (next_fetch - now).total_seconds(), 60)
            time.sleep(max(1, delay))
    except KeyboardInterrupt:
        status.update(state="stopped", stopped_at=utc_now())
        write_status(status)
        return 0
    finally:
        api.close()


def main(argv: list[str] | None = None) -> int:
    load_env()
    parser = argparse.ArgumentParser(description="华泰宏观内部消息采集服务")
    parser.add_argument("--once", action="store_true", help="登录并抓取一轮后退出")
    args = parser.parse_args(argv)
    try:
        return run_service(Settings.from_env(), once=args.once)
    except Exception as exc:
        settings = Settings.from_env()
        write_status({
            "state": "failed", "updated_at": utc_now(),
            "last_error": _safe_error(exc, (settings.password,)),
        })
        print(_safe_error(exc, (settings.password,)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
