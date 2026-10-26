"""Build Chinese explanations for the actual crawler search queries.

The original rule is never changed.  Translations are stored separately and
only used by the standalone data-lineage explainer.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import build_data_lineage_explainer as explainer
from backend.translation_quality import normalize_terms


OUTPUT = ROOT / "config" / "keyword-translations.json"
FOREIGN = re.compile(r"[A-Za-z\u0400-\u04ff\u0600-\u06ff]")
CYRILLIC = re.compile(r"[\u0400-\u04ff]")
PERSIAN = re.compile(r"[\u0600-\u06ff]")

CYRILLIC_GLOSSARY = {
    "посредник": "调解方", "визит": "访问", "встреч": "会晤", "росси": "俄罗斯",
    "украин": "乌克兰", "москв": "莫斯科", "киев": "基辅", "переговор": "谈判",
    "перемири": "停火", "урегулир": "解决方案", "енергетич": "能源", "энергетич": "能源",
    "припинення вогню": "停火", "прекращение огня": "停火", "уступ": "让步",
    "готов": "准备或意愿", "отказ": "拒绝", "меморандум": "备忘录", "срыв": "破裂或受阻",
    "путин": "普京", "истанбул": "伊斯坦布尔", "выбор": "选举", "избир": "选举",
    "явк": "投票率", "кандидат": "候选人", "бюллетен": "选票", "голосов": "投票",
    "госдум": "俄罗斯国家杜马", "дум": "杜马", "парламент": "议会", "депутат": "议员",
    "единая россия": "统一俄罗斯党", "кпрф": "俄罗斯联邦共产党", "лдпр": "俄罗斯自由民主党",
    "избирком": "选举委员会", "нефт": "石油", "дизел": "柴油", "бензин": "汽油",
    "топлив": "燃料", "танкер": "油轮", "нефтепровод": "输油管道", "экспорт": "出口",
    "пошлин": "关税", "санкцион": "制裁", "обход санкц": "规避制裁", "минэнерго": "能源部",
    "новак": "诺瓦克", "роснефт": "俄罗斯石油公司", "газпром": "俄罗斯天然气工业股份公司",
    "транснефт": "俄罗斯石油管道运输公司", "лукойл": "卢克石油", "новатэк": "诺瓦泰克",
    "дефицит": "短缺或赤字", "заимствован": "借款", "резервн": "储备", "фонд": "基金",
    "фнб": "俄罗斯国家财富基金", "бюджет": "预算", "минфин": "财政部", "казначейств": "国库",
    "огфз": "俄罗斯联邦债券", "млрд": "十亿", "трлн": "万亿", "рубл": "卢布",
    "оборон": "国防", "военн": "军事", "расход": "支出", "экономик": "经济",
    "гособоронзаказ": "国家国防订单", "ввп": "国内生产总值", "кремл": "克里姆林宫",
    "федеральн": "联邦", "минобороны": "国防部", "госбюджет": "国家预算", "курс": "汇率",
    "девальвац": "贬值", "мосбирж": "莫斯科交易所", "доллар": "美元", "ключев": "关键",
    "ставк": "利率", "валют": "外汇", "интервенц": "干预", "центробанк": "中央银行",
    "инфляц": "通胀", "набиуллин": "纳比乌琳娜", "денежн": "货币", "кредитн": "信贷",
    "банк росси": "俄罗斯央行", "кадр": "劳动力", "нехватк": "短缺", "договор": "协议",
    "постав": "交付或供应", "оруж": "武器", "боеприпас": "弹药", "ракет": "导弹",
    "дрон": "无人机", "беспилот": "无人机", "фронт": "前线", "атак": "袭击",
    "удар": "打击", "оборона": "防御", "наступ": "进攻", "помощ": "援助",
    "зерн": "粮食", "пшениц": "小麦", "урожа": "收成", "порт": "港口",
    "черн": "黑海", "газ": "天然气", "электр": "电力", "санкц": "制裁",
}


def clean_for_model(text: str) -> str:
    text = re.sub(r"^rx:", "", text, flags=re.I)
    text = text.replace(r"\b", " ").replace(r"\s+", " ").replace(r"\w*", " ")
    text = text.replace("(?:", "(").replace("|", " or ")
    text = re.sub(r"[\\^$*+?{}\[\]()]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .,:;-'\"")
    return text or text.strip()


def cyrillic_explanation(text: str) -> str:
    lowered = text.lower()
    meanings = []
    for stem, meaning in sorted(CYRILLIC_GLOSSARY.items(), key=lambda item: len(item[0]), reverse=True):
        if stem in lowered and meaning not in meanings:
            meanings.append(meaning)
    if not meanings:
        meanings.append("俄语或乌克兰语主题表达")
    return "、".join(meanings) + "相关检索表达"


def load_models():
    import ctranslate2
    from transformers import MarianTokenizer

    model_root = Path(os.path.relpath(ROOT / "data" / "models" / "opus-mt-en-zh"))
    tokenizer = MarianTokenizer.from_pretrained(str(model_root), local_files_only=True)
    model = ctranslate2.Translator(
        str(model_root.parent / "opus-mt-en-zh-int8"), device="cpu", compute_type="int8", intra_threads=4
    )
    persian_root = model_root.parent / "persian-english" / "translate-fa_en-1_5"
    persian_model = persian_tokenizer = None
    if (persian_root / "model" / "model.bin").exists():
        import sentencepiece

        persian_tokenizer = sentencepiece.SentencePieceProcessor(model_file=str(persian_root / "sentencepiece.model"))
        persian_model = ctranslate2.Translator(str(persian_root / "model"), device="cpu", compute_type="int8", intra_threads=4)
    return tokenizer, model, persian_tokenizer, persian_model


def translate_english(batch: list[str], tokenizer, model) -> list[str]:
    encoded = [
        tokenizer.convert_ids_to_tokens(tokenizer.encode(">>cmn_Hans<< " + clean_for_model(text)))
        for text in batch
    ]
    output = model.translate_batch(encoded, beam_size=3, max_decoding_length=180)
    return [
        normalize_terms(tokenizer.decode(tokenizer.convert_tokens_to_ids(item.hypotheses[0]), skip_special_tokens=True))
        for item in output
    ]


def main() -> None:
    dataset = explainer.build_dataset(explainer.latest_snapshot())
    terms = list(dict.fromkeys(
        rule["raw"] for crawler in dataset["crawlers"] for rule in crawler["search_queries"] if FOREIGN.search(rule["raw"])
    ))
    cache = json.loads(OUTPUT.read_text(encoding="utf-8-sig")) if OUTPUT.is_file() else {}
    pending = [term for term in terms if term not in cache]
    if not pending:
        print(f"Keyword translations are complete: {len(cache)}")
        return
    tokenizer, model, persian_tokenizer, persian_model = load_models()
    for offset in range(0, len(pending), 32):
        batch = pending[offset:offset + 32]
        english_inputs: list[str] = []
        english_owners: list[str] = []
        for original in batch:
            if CYRILLIC.search(original):
                cache[original] = cyrillic_explanation(original)
                continue
            source = original
            if PERSIAN.search(original) and persian_model and persian_tokenizer:
                result = persian_model.translate_batch(
                    [persian_tokenizer.encode(clean_for_model(original), out_type=str)],
                    beam_size=3,
                    max_decoding_length=180,
                )[0]
                source = persian_tokenizer.decode(result.hypotheses[0])
            english_inputs.append(source)
            english_owners.append(original)
        if english_inputs:
            for original, translated in zip(english_owners, translate_english(english_inputs, tokenizer, model)):
                cache[original] = translated or "外文主题检索表达"
        OUTPUT.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print(f"Translated {min(offset + len(batch), len(pending))}/{len(pending)}", flush=True)
    print(json.dumps({"output": str(OUTPUT), "terms": len(terms), "translations": len(cache)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
