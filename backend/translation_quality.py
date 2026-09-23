"""Conservative display checks, not semantic verification."""
import re

GLOSSARY={'Zelenskyy':'泽连斯基','Zelensky':'泽连斯基','Kyiv':'基辅','Kiev':'基辅','Zaporizhzhia':'扎波罗热','Houthis':'胡塞武装','Houthi':'胡塞武装','IRGC':'伊朗伊斯兰革命卫队','Hormuz':'霍尔木兹'}

def language(text):
    if re.search(r'[\u4e00-\u9fff]',text):return 'zh'
    if re.search(r'[\u0400-\u04ff]',text):return 'cyrillic'
    if re.search(r'[\u0590-\u05ff]',text):return 'hebrew'
    if re.search(r'[\u0600-\u06ff]',text):return 'fa' if re.search(r'[پچژگکی]',text) else 'arabic_unknown'
    # Latin script is not sufficient evidence that text is English.
    if re.search(r'\b(the|and|of|to|in|for|with|on|from|is|are|says|said|US|Iran|Israel|Ukraine)\b',text,re.I):return 'en'
    return 'unknown'

def normalize_terms(text):
    for source,target in GLOSSARY.items():text=re.sub(r'\b'+source+r'\b',target,text,flags=re.I)
    return text.replace('泽伦斯基','泽连斯基').replace('基辅市市','基辅市')

def quality(original,translated):
    issues=[]
    if not translated or '译文准备中' in translated:issues.append('缺少可靠中文译文')
    if re.search(r'[\u0400-\u06ff]',translated):issues.append('译文含未翻译文字')
    # Short codes and proper nouns may remain; lengthy Latin phrases require review.
    if re.search(r'(?:[A-Za-z]{3,}\s+){2}[A-Za-z]{3,}',translated):issues.append('译文含较长外文片段')
    nums=lambda s:set(re.findall(r'(?<!\w)\d+(?:[.,]\d+)*(?:%|％)?',s))
    if language(original)!='zh' and nums(original)!=nums(translated):issues.append('数字表达发生变化，需核对原文')
    return {'status':'待复核' if issues else '机器译文待语义核验' if language(original)!='zh' else '中文原文', 'issues':issues,'language':language(original)}
