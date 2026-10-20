"""Guard the authenticated model-check proxy budget and auth boundaries."""
from pathlib import Path
import re


CONFIG = Path(__file__).resolve().parents[1] / "deploy/gateway.conf"


def location(config, path):
    match = re.search(rf"location\s+{re.escape(path)}\s*\{{([^{{}}]*)\}}", config)
    assert match is not None, f"Missing gateway location: {path}"
    return match.group(1)


def test_model_draft_check_keeps_full_api_read_budget():
    config = CONFIG.read_text()
    integration = location(config, "/api/integrations")
    public_api = location(config, "/")
    # The browser uses /api/integrations/model/test, not /api/model/check.
    # A bare location silently inherits nginx's 60s default while the model
    # default is 90s, causing HTML 504s before the application's JSON response.
    assert "proxy_read_timeout 300s;" in integration
    assert "proxy_read_timeout 300s;" in public_api


def test_long_running_integration_checks_preserve_gateway_auth_contract():
    config = CONFIG.read_text()
    integration = location(config, "/api/integrations")
    public_api = location(config, "/")
    mcp = location(config, "= /mcp")
    server_directives = config[:config.index("location")]
    assert 'auth_basic "GeoAgent";' in server_directives
    assert "auth_basic_user_file /etc/geoagent-auth/auth;" in server_directives
    assert "auth_basic off;" not in integration
    assert "auth_basic off;" not in public_api
    assert "proxy_set_header X-GeoAgent-Gateway-User $remote_user;" in integration
    assert 'proxy_set_header X-GeoAgent-Gateway-User "";' in public_api
    assert "auth_basic off;" in mcp
    assert 'proxy_set_header X-GeoAgent-Gateway-User "";' in mcp
    assert "proxy_pass http://127.0.0.1:8000;" in integration
