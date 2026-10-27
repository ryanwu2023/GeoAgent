"""Backend-only model configuration and transport contracts; no live secrets."""
import asyncio
import json
import os
import stat

import httpx
import pytest

from backend import llm, model_settings


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(model_settings, "ROOT", tmp_path)
    monkeypatch.setattr(llm, "ROOT", tmp_path)
    for key in model_settings.ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("GEOAGENT_PICHAT_CA_FILE", raising=False)


def deepseek(**changes):
    return {
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
        "timeout_seconds": 90,
        "api_key": "test-model",
        **changes,
    }


def mock_http(monkeypatch, handler):
    actual = httpx.AsyncClient
    options = []

    def client(**kwargs):
        options.append(dict(kwargs))
        return actual(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(llm.httpx, "AsyncClient", client)
    return options


def test_unconfigured_is_disabled_not_a_local_language_model():
    public = model_settings.public_settings()
    assert set(public) == set(model_settings.PUBLIC_KEYS)
    assert public["source"] == "none"
    assert public["provider"] == "disabled"
    assert not public["configured"]
    assert not public["api_key_configured"]
    assert llm.status()["mode"] == "本地证据检索"


def test_saved_settings_are_private_atomic_and_hot_read(monkeypatch):
    original_replace = model_settings.os.replace
    published = []

    def replace(source, target):
        if os.name != "nt":
            assert stat.S_IMODE(model_settings.os.stat(source).st_mode) == 0o600
        published.append(json.loads(model_settings.Path(source).read_text(encoding="utf-8")))
        original_replace(source, target)

    monkeypatch.setattr(model_settings.os, "replace", replace)
    result = model_settings.save_settings(deepseek())
    assert set(result) == set(model_settings.PUBLIC_KEYS)
    assert result["source"] == "saved"
    assert result["configured"]
    assert "test-model" not in json.dumps(result)
    path = model_settings.settings_path()
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert len(published) == 1
    assert not list(path.parent.glob(".model-settings-*"))
    assert llm.config()["model"] == "deepseek-flash"
    before = llm.cache_identity()
    model_settings.save_settings(deepseek(model="explicit-next-model", api_key=""))
    assert llm.config()["model"] == "explicit-next-model"
    assert llm.config()["api_key"] == "test-model"
    assert llm.cache_identity() != before


def test_saved_provider_wins_over_both_environment_providers(monkeypatch):
    monkeypatch.setenv("PICHAT_API_KEY", "test-pi-env")
    monkeypatch.setenv("PICHAT_BASE_URL", "https://pichat.example")
    monkeypatch.setenv("LLM_API_KEY", "test-ll-env")
    model_settings.save_settings(deepseek())

    current = llm.config()
    assert current["provider"] == "deepseek"
    assert current["api_key"] == "test-model"
    assert current["PICHAT_API_KEY"] == ""
    assert "test-pi-env" not in json.dumps(current)
    assert "test-ll-env" not in json.dumps(current)


def test_disabled_saved_selection_never_falls_back(monkeypatch):
    monkeypatch.setenv("PICHAT_API_KEY", "test-pi-env")
    monkeypatch.setenv("LLM_API_KEY", "test-ll-env")
    model_settings.save_settings({"provider": "disabled", "api_key": "test-drop"})
    assert model_settings.public_settings()["source"] == "saved"
    assert not llm.status()["configured"]
    assert json.loads(model_settings.settings_path().read_text())["api_key"] == ""
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **_: pytest.fail("disabled must not call HTTP"))
    with pytest.raises(ValueError, match="停用"):
        asyncio.run(llm.complete([{"role": "user", "content": "test"}]))


@pytest.mark.parametrize("changes", [
    {"provider": "openai-compatible"},
    {"base_url": "https://other.example"},
    {"base_url": "https://api.deepseek.com/v1"},
])
def test_switching_provider_or_endpoint_does_not_forward_old_key(changes):
    model_settings.save_settings(deepseek())
    draft = model_settings.prepare_settings(deepseek(api_key="", **changes))
    assert draft["api_key"] == ""
    assert not model_settings.public_view(draft)["configured"]


@pytest.mark.parametrize("url", [
    "https://api.deepseek.com/",
    "https://API.DEEPSEEK.COM:443/chat/completions",
])
def test_same_normalized_target_can_keep_blank_key(url):
    model_settings.save_settings(deepseek())
    draft = model_settings.prepare_settings(deepseek(base_url=url, api_key=""))
    assert draft["api_key"] == "test-model"
    assert draft["base_url"] == "https://api.deepseek.com"


def test_disabled_then_reenabled_does_not_resurrect_old_key():
    model_settings.save_settings(deepseek())
    model_settings.save_settings({"provider": "disabled"})
    public = model_settings.save_settings(deepseek(api_key=""))
    assert not public["configured"]
    assert model_settings.effective_settings()["api_key"] == ""


def test_environment_compatibility_and_hot_dotenv(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text("\n".join([
        "LLM_BASE_URL=https://models.example/v1", "LLM_API_KEY=test-env-a", "",
    ]))
    first = model_settings.public_settings()
    assert first["source"] == "environment"
    assert first["provider"] == "openai-compatible"
    assert first["model"] == ""
    assert llm.status()["auto_model"]
    dotenv.write_text("\n".join([
        "LLM_BASE_URL=https://models.example/v1", "LLM_API_KEY=test-env-b",
        "LLM_MODEL=file-model", "",
    ]))
    assert llm.config()["api_key"] == "test-env-b"
    monkeypatch.setenv("LLM_MODEL", "process-model")
    assert llm.config()["model"] == "process-model"
    monkeypatch.setenv("PICHAT_API_KEY", "test-pi-old")
    assert llm.config()["provider"] == "pichat"


def test_legacy_http_environment_is_readable_but_new_remote_http_rejected(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://internal.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-legacy")
    assert model_settings.public_settings()["base_url"] == "http://internal.example/v1"
    with pytest.raises(ValueError, match="HTTPS"):
        model_settings.prepare_settings(deepseek(base_url="http://internal.example/v1"))


@pytest.mark.parametrize("url", [
    "http://localhost:8000/v1",
    "http://127.0.0.1:8000/v1",
    "http://[::1]:8000/v1",
])
def test_loopback_http_is_available_for_local_models(url):
    result = model_settings.prepare_settings(deepseek(provider="openai-compatible", base_url=url))
    assert result["base_url"] == url


@pytest.mark.parametrize("url", [
    "https://user:password@api.example/v1",
    "https://user@api.example/v1",
    "https://api.example/v1?key=test",
    "https://api.example/v1#test",
    "https://api.example/chat",
    "https://api.example/chat/",
    "https://api.example/admin/api",
    "https://api.example/%63hat",
    "https://api.example/v1/../chat",
    "https://api.example\\@other.example/v1",
    "https://api.example/v1\n",
    "https://api.example:0/v1",
    "http://private.example/v1",
    "file:///tmp/model",
])
def test_unsafe_or_page_urls_are_rejected_without_echoing_inputs(url):
    with pytest.raises(ValueError) as error:
        model_settings.prepare_settings(deepseek(base_url=url))
    assert url not in str(error.value)
    assert "password" not in str(error.value)


@pytest.mark.parametrize("timeout", [0, 301, True, float("inf"), "wrong"])
def test_timeout_validation(timeout):
    with pytest.raises(ValueError, match="超时"):
        model_settings.prepare_settings(deepseek(timeout_seconds=timeout))


def test_page_direct_model_must_be_explicit():
    with pytest.raises(ValueError, match="模型名称"):
        model_settings.prepare_settings(deepseek(model=""))


def test_pichat_model_is_not_claimed_to_be_selectable():
    public = model_settings.save_settings(deepseek(
        provider="pichat", base_url="https://pichat.example/api/v1/chat", model="ignored-model",
    ))
    assert public["model"] == "Pi Chat Agent"
    assert model_settings.effective_settings()["model"] == ""
    model_settings.save_settings({**public, "api_key": ""})
    assert model_settings.effective_settings()["api_key"] == "test-model"


def test_corrupt_saved_file_fails_closed_even_with_environment_key(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-no-env")
    path = model_settings.settings_path()
    path.parent.mkdir(parents=True)
    path.write_text("not-json-with-sensitive-content")
    public = model_settings.public_settings()
    assert set(public) == {*model_settings.PUBLIC_KEYS, "config_error"}
    assert public["source"] == "error"
    assert not public["configured"]
    assert not public["api_key_configured"]
    assert "sensitive-content" not in json.dumps(public)
    assert "test-no-env" not in json.dumps(public)
    assert llm.status()["config_error"] == public["config_error"]
    assert llm.cache_identity()["provider"] == "disabled"
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **_: pytest.fail("invalid settings must not call HTTP"))
    with pytest.raises(ValueError, match="阻止回退") as error:
        asyncio.run(llm.complete([{"role": "user", "content": "test"}]))
    assert "sensitive-content" not in str(error.value)
    model_settings.save_settings({"provider": "disabled"})
    assert not model_settings.public_settings()["configured"]


def test_complete_new_settings_can_replace_corrupt_saved_configuration():
    path = model_settings.settings_path()
    path.parent.mkdir(parents=True)
    path.write_text("broken-model-configuration")
    result = model_settings.save_settings(deepseek())
    assert result["configured"]
    assert model_settings.public_settings() == result


def test_atomic_publish_failure_preserves_previous_settings(monkeypatch):
    model_settings.save_settings(deepseek())
    path = model_settings.settings_path()
    before = path.read_bytes()

    def failed_replace(*_):
        raise OSError("simulated atomic publish failure")

    monkeypatch.setattr(model_settings.os, "replace", failed_replace)
    with pytest.raises(OSError, match="simulated"):
        model_settings.save_settings(deepseek(model="new-model", api_key="test-new"))
    assert path.read_bytes() == before
    assert not list(path.parent.glob(".model-settings-*"))


def test_symlink_settings_are_rejected(tmp_path):
    path = model_settings.settings_path()
    path.parent.mkdir(parents=True)
    private = tmp_path / "not-model-settings.json"
    private.write_text("do-not-read")
    try:
        path.symlink_to(private)
    except OSError as exc:
        pytest.skip(f"当前 Windows 账户不能创建符号链接：{exc.winerror if hasattr(exc, 'winerror') else exc}")
    assert model_settings.public_settings()["source"] == "error"
    with pytest.raises(ValueError, match="符号链接"):
        model_settings.effective_settings()
    with pytest.raises(ValueError, match="符号链接"):
        model_settings.save_settings(deepseek())
    assert private.read_text() == "do-not-read"


@pytest.mark.parametrize("error", [
    PermissionError("private-path-or-secret"), ValueError("invalid-secret-input"),
])
def test_status_is_safe_when_optional_configuration_cannot_be_read(monkeypatch, error):
    def unreadable(**_):
        raise error
    monkeypatch.setattr(model_settings, "effective_settings", unreadable)
    public = model_settings.public_settings()
    assert public["source"] == "error" and public["configured"] is False
    assert public["base_url"] == "" and public["model"] == ""
    assert str(error) not in json.dumps(public)
    status = llm.status()
    assert status["mode"] == "本地证据检索"
    assert not status["configured"] and not status["auto_model"]
    assert llm.cache_identity()["provider"] == "disabled"
    with pytest.raises(type(error)):
        asyncio.run(llm.complete([{"role": "user", "content": "test"}]))


def test_invalid_environment_configuration_has_safe_repair_projection(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-environment-secret")
    monkeypatch.setenv("LLM_BASE_URL", "https://secret:credential@example.test")
    public = model_settings.public_settings()
    assert public["source"] == "error"
    assert not public["configured"]
    assert "test-environment-secret" not in json.dumps(public)
    assert "credential" not in json.dumps(public)
    with pytest.raises(ValueError):
        model_settings.effective_settings()


def test_deepseek_uses_explicit_fast_nonstreaming_request(monkeypatch):
    model_settings.save_settings(deepseek())
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-model"
        body = json.loads(request.content)
        assert body == {
            "model": "deepseek-flash", "messages": [{"role": "user", "content": "测试"}],
            "stream": False, "thinking": {"type": "disabled"},
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": "连接成功"}}]})

    monkeypatch.setattr(llm, "pichat_tls_context", lambda: pytest.fail("DeepSeek must not use PiChat CA"))
    options = mock_http(monkeypatch, handler)
    assert asyncio.run(llm.complete([{"role": "user", "content": "测试"}])) == ("连接成功", "deepseek-flash")
    assert len(calls) == 1
    assert options[0]["verify"] is True
    assert options[0]["follow_redirects"] is False


def test_pichat_transport_uses_pichat_tls_only(monkeypatch):
    model_settings.save_settings(deepseek(provider="pichat", base_url="https://pichat.example"))
    tls_calls = []
    monkeypatch.setattr(llm, "pichat_tls_context", lambda: tls_calls.append(True) or True)

    def handler(request):
        assert request.url.path == "/api/v1/chat"
        assert json.loads(request.content) == {"message": "[USER]\n测试", "stream": False}
        return httpx.Response(200, json={"text": "连接成功"})

    options = mock_http(monkeypatch, handler)
    assert asyncio.run(llm.complete([{"role": "user", "content": "测试"}])) == ("连接成功", "Pi Chat Agent")
    assert tls_calls == [True]
    assert options[0]["follow_redirects"] is False


def test_draft_test_does_not_save_or_reread_other_provider(monkeypatch):
    model_settings.save_settings(deepseek(provider="pichat", base_url="https://pichat.example", api_key="test-pi"))
    before = model_settings.settings_path().read_bytes()
    draft = model_settings.prepare_settings(deepseek(api_key="test-draft"))
    monkeypatch.setattr(llm, "config", lambda: pytest.fail("draft must not reread live config"))

    def handler(request):
        assert request.headers["Authorization"] == "Bearer test-draft"
        assert request.url.host == "api.deepseek.com"
        return httpx.Response(200, json={"choices": [{"message": {"content": "draft success"}}]})

    mock_http(monkeypatch, handler)
    assert asyncio.run(llm.complete([{"role": "user", "content": "test"}], settings_override=draft))[0] == "draft success"
    assert model_settings.settings_path().read_bytes() == before


def test_blank_key_draft_cannot_borrow_live_provider_key(monkeypatch):
    model_settings.save_settings(deepseek())
    draft = model_settings.prepare_settings(deepseek(base_url="https://other.example", api_key=""))
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **_: pytest.fail("missing key must not call HTTP"))
    with pytest.raises(ValueError, match="API Key"):
        asyncio.run(llm.complete([], settings_override=draft))


def test_redirect_is_not_followed_and_secret_never_reaches_second_host(monkeypatch):
    model_settings.save_settings(deepseek())
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(307, headers={"Location": "https://untrusted.example/collect"})

    mock_http(monkeypatch, handler)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(llm.complete([{"role": "user", "content": "test"}]))
    assert calls == ["https://api.deepseek.com/chat/completions"]


@pytest.mark.parametrize("finish_reason", ["length", "content_filter", "tool_calls", None])
def test_incomplete_completion_is_rejected_even_when_content_is_valid_json(monkeypatch, finish_reason):
    model_settings.save_settings(deepseek())

    def handler(_):
        return httpx.Response(200, json={"choices": [{
            "finish_reason": finish_reason,
            "message": {"content": '{"translation":"一段看似完整的译文"}'},
        }]})

    mock_http(monkeypatch, handler)
    with pytest.raises(ValueError, match="未完整"):
        asyncio.run(llm.complete([{"role": "user", "content": "test"}]))


@pytest.mark.parametrize("content", ["", " \n\t", None])
def test_empty_completion_text_is_rejected(monkeypatch, content):
    model_settings.save_settings(deepseek())
    mock_http(monkeypatch, lambda _: httpx.Response(200, json={"choices": [{
        "finish_reason": "stop", "message": {"content": content},
    }]}))

    with pytest.raises(ValueError, match="未返回文本"):
        asyncio.run(llm.complete([{"role": "user", "content": "test"}]))


@pytest.mark.parametrize("explicit_stop", [True, False])
def test_complete_text_allows_stop_or_legacy_absent_finish_reason(monkeypatch, explicit_stop):
    model_settings.save_settings(deepseek())
    choice = {"message": {"content": "完整回答"}}
    if explicit_stop:
        choice["finish_reason"] = "stop"
    mock_http(monkeypatch, lambda _: httpx.Response(200, json={"choices": [choice]}))

    assert asyncio.run(llm.complete([{"role": "user", "content": "test"}])) == ("完整回答", "deepseek-flash")
