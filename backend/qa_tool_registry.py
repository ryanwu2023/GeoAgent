"""图式问答工具能力注册表：固定Schema、权限过滤与调用前校验。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
from typing import Any, Iterable

from .qa_plan_schema import ResearchTask, TOOL_CATEGORIES
from .qa_scope import ResearchScope


@dataclass(frozen=True)
class ToolCapability:
    name: str
    category: str
    description: str
    input_schema: dict[str, Any]
    source_type: str = "local"
    mcp_server: str = "geoagent"
    data_classification: str = "public"
    read_only: bool = True
    supported_topics: tuple[str, ...] = ("usiran", "ukraine")
    version: str = "v1"

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{1,100}", self.name):
            raise ValueError("工具名称格式无效")
        if self.category not in TOOL_CATEGORIES:
            raise ValueError("工具类别无效")
        if self.source_type not in {"local", "external_mcp"}:
            raise ValueError("工具来源类型无效")
        if self.data_classification not in {"public", "internal", "confidential"}:
            raise ValueError("工具数据权限无效")
        if not self.read_only:
            raise ValueError("研究工具必须是只读工具")
        if not isinstance(self.input_schema, dict) or self.input_schema.get("type") != "object":
            raise ValueError("工具参数Schema必须是对象")

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description[:500],
                "parameters": self.input_schema,
            },
        }


class ToolCapabilityRegistry:
    def __init__(self, tools: Iterable[ToolCapability] = ()) -> None:
        self._tools: dict[str, ToolCapability] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: ToolCapability) -> None:
        if not isinstance(tool, ToolCapability):
            raise TypeError("工具必须是ToolCapability")
        if tool.name in self._tools:
            raise ValueError(f"重复工具：{tool.name}")
        self._tools[tool.name] = tool

    def all(self) -> list[ToolCapability]:
        return [self._tools[name] for name in sorted(self._tools)]

    @property
    def schema_version(self) -> str:
        payload = json.dumps(
            [asdict(item) for item in self.all()], ensure_ascii=False,
            sort_keys=True, separators=(",", ":"),
        )
        return "tools-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def select(self, task: ResearchTask, scope: ResearchScope) -> list[ToolCapability]:
        categories = set(task.allowed_tool_categories)
        permissions = set(scope.source_permissions)
        topics = set(task.scope_topics or scope.topics)
        selected = []
        for tool in self.all():
            if tool.category not in categories:
                continue
            if tool.data_classification not in permissions:
                continue
            if tool.source_type == "external_mcp" and "external_mcp" not in permissions:
                continue
            if topics and tool.supported_topics and not topics.intersection(tool.supported_topics):
                continue
            selected.append(tool)
        return selected

    def validate_call(
        self, name: str, arguments: dict[str, Any], task: ResearchTask, scope: ResearchScope,
    ) -> dict[str, Any]:
        capability = self._tools.get(str(name))
        if capability is None:
            raise ValueError("不允许的工具")
        allowed = {item.name for item in self.select(task, scope)}
        if capability.name not in allowed:
            raise ValueError("工具超出任务权限")
        value = dict(arguments or {})
        self._validate_schema(value, capability.input_schema)
        return scope.validate_arguments(capability.name, value)

    @staticmethod
    def _validate_schema(value: dict[str, Any], schema: dict[str, Any]) -> None:
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        required = schema.get("required") if isinstance(schema.get("required"), list) else []
        missing = [name for name in required if name not in value or value[name] in (None, "")]
        if missing:
            raise ValueError("工具参数缺少：" + ",".join(missing))
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(properties))
            if unknown:
                raise ValueError("工具参数包含未知字段：" + ",".join(unknown))
        for name, item in value.items():
            rule = properties.get(name)
            if not isinstance(rule, dict):
                continue
            expected = rule.get("type")
            valid = (
                expected == "string" and isinstance(item, str)
                or expected == "integer" and isinstance(item, int) and not isinstance(item, bool)
                or expected == "number" and isinstance(item, (int, float)) and not isinstance(item, bool)
                or expected == "array" and isinstance(item, list)
                or expected == "boolean" and isinstance(item, bool)
                or expected == "object" and isinstance(item, dict)
                or expected is None
            )
            if not valid:
                raise ValueError(f"工具参数{name}类型无效")
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                if "minimum" in rule and item < rule["minimum"]:
                    raise ValueError(f"工具参数{name}低于允许范围")
                if "maximum" in rule and item > rule["maximum"]:
                    raise ValueError(f"工具参数{name}高于允许范围")
            if isinstance(item, str):
                if "minLength" in rule and len(item) < rule["minLength"]:
                    raise ValueError(f"工具参数{name}不能为空")
                if "maxLength" in rule and len(item) > rule["maxLength"]:
                    raise ValueError(f"工具参数{name}过长")
            if "enum" in rule and item not in rule["enum"]:
                raise ValueError(f"工具参数{name}不在允许范围")


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object", "properties": properties,
        "required": required or [], "additionalProperties": False,
    }


_COMMON_SCOPE = {
    "topic": {"type": "string", "enum": ["usiran", "ukraine", "all"]},
    "start": {"type": "string", "maxLength": 10},
    "end": {"type": "string", "maxLength": 10},
}


def default_registry() -> ToolCapabilityRegistry:
    """注册现有九个GeoAgent工具；其中三个华泰工具沿用原名称。"""
    values = [
        ToolCapability(
            "geo_workspace_summary", "workspace", "读取冻结范围、覆盖和重要指标摘要",
            _object(dict(_COMMON_SCOPE)),
        ),
        ToolCapability(
            "geo_evidence_search", "public_evidence", "在冻结范围内检索公开证据摘要",
            _object({
                "q": {"type": "string", "minLength": 1, "maxLength": 300},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                "source_tiers": {"type": "array"}, **_COMMON_SCOPE,
            }, ["q"]),
        ),
        ToolCapability(
            "geo_evidence_get", "public_evidence", "读取本轮已检索公开证据的完整正文",
            _object({"record_id": {"type": "string", "minLength": 1, "maxLength": 200}}, ["record_id"]),
        ),
        ToolCapability(
            "geo_metric_catalog", "market_data", "列出冻结范围内可用量化指标",
            _object({"limit": {"type": "integer", "minimum": 1, "maximum": 20}, **_COMMON_SCOPE}),
        ),
        ToolCapability(
            "geo_metric_series", "market_data", "读取指定指标的有界历史序列",
            _object({
                "series": {"type": "string", "minLength": 1, "maxLength": 200},
                "max_points": {"type": "integer", "minimum": 2, "maximum": 50}, **_COMMON_SCOPE,
            }, ["series"]),
        ),
        ToolCapability(
            "geo_analysis_view", "analysis", "读取资产和情景的条件研判及数据缺口",
            _object({
                "asset_id": {"type": "string", "maxLength": 200},
                "scenario_id": {"type": "string", "maxLength": 200}, **_COMMON_SCOPE,
            }),
        ),
        ToolCapability(
            "geo_internal_message_search", "internal_research", "检索华泰内部消息线索，需外部核验",
            _object({
                "q": {"type": "string", "minLength": 1, "maxLength": 300},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20}, **_COMMON_SCOPE,
            }, ["q"]), data_classification="internal"),
        ToolCapability(
            "geo_internal_message_get", "internal_research", "读取本轮已检索华泰消息正文",
            _object({"message_id": {"type": "string", "minLength": 1, "maxLength": 200}}, ["message_id"]),
            data_classification="internal"),
        ToolCapability(
            "geo_internal_message_stats", "internal_research", "读取华泰消息库覆盖与采集状态",
            _object({}), data_classification="internal"),
    ]
    return ToolCapabilityRegistry(values)
