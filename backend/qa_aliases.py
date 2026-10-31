"""地缘问答的版本化实体与跨语言检索扩展。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re

from .qa_scope import ResearchScope


ALIAS_VERSION = "geo-aliases/2026-10-31"

ALIASES: dict[str, tuple[str, ...]] = {
    "美伊": ("美国 伊朗", "US Iran", "United States Iran", "Washington Tehran"),
    "俄乌": ("俄罗斯 乌克兰", "Russia Ukraine", "Moscow Kyiv"),
    "胡塞": ("胡塞武装", "Houthi", "Ansar Allah"),
    "霍尔木兹": ("霍尔木兹海峡", "Strait of Hormuz", "Hormuz shipping"),
    "曼德海峡": ("曼德海峡", "Bab el-Mandeb", "Bab al-Mandab"),
    "航运": ("船舶 通行 油轮", "shipping vessel tanker transit"),
    "袭击": ("打击 攻击", "attack strike"),
    "谈判": ("会谈 斡旋 协议", "talks negotiation mediation agreement"),
    "停火": ("停战 休战", "ceasefire truce"),
    "制裁": ("经济限制 金融限制", "sanctions economic restrictions OFAC"),
    "援助": ("军援 拨款 交付", "aid military assistance disbursement delivery"),
    "原油": ("布伦特 WTI 石油", "crude oil Brent WTI"),
    "美债": ("美国国债 收益率", "US Treasury yield"),
    "佩泽希齐扬": ("马苏德·佩泽希齐扬", "Masoud Pezeshkian"),
    "特朗普": ("唐纳德·特朗普", "Donald Trump"),
    "泽连斯基": ("弗拉基米尔·泽连斯基", "Volodymyr Zelenskyy", "Volodymyr Zelensky"),
    "普京": ("弗拉基米尔·普京", "Vladimir Putin"),
    "哈梅内伊": ("阿亚图拉哈梅内伊", "Ayatollah Khamenei", "Ali Khamenei"),
}


@dataclass(frozen=True)
class ExpandedQuery:
    original: str
    entities: tuple[str, ...]
    aliases: tuple[str, ...]
    chinese_queries: tuple[str, ...]
    english_queries: tuple[str, ...]
    exclude_terms: tuple[str, ...]
    version: str = ALIAS_VERSION

    def all_text(self) -> str:
        return " ".join(dict.fromkeys((self.original, *self.aliases, *self.chinese_queries, *self.english_queries)))

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        for key in ("entities", "aliases", "chinese_queries", "english_queries", "exclude_terms"):
            value[key] = list(value[key])
        return value


def expand_query(query: str, scope: ResearchScope) -> ExpandedQuery:
    text = str(query or "").strip()
    entities: list[str] = []
    aliases: list[str] = []
    chinese: list[str] = [text] if re.search(r"[\u4e00-\u9fff]", text) else []
    english: list[str] = [text] if re.search(r"[A-Za-z]", text) else []
    lowered = text.lower()
    for entity, values in ALIASES.items():
        if entity in text or any(value.lower() in lowered for value in values):
            entities.append(entity)
            aliases.extend(values)
            chinese.extend(value for value in values if re.search(r"[\u4e00-\u9fff]", value))
            english.extend(value for value in values if re.search(r"[A-Za-z]", value))
    excludes = []
    if scope.primary_topic == "ukraine":
        excludes.extend(("伊朗", "美伊"))
    elif scope.primary_topic == "usiran":
        excludes.extend(("乌克兰", "俄乌"))
    return ExpandedQuery(
        original=text,
        entities=tuple(dict.fromkeys(entities)), aliases=tuple(dict.fromkeys(aliases)),
        chinese_queries=tuple(dict.fromkeys(chinese)), english_queries=tuple(dict.fromkeys(english)),
        exclude_terms=tuple(excludes),
    )
