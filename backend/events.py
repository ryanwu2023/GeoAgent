"""Map news across domains; keyword signals are not verified conflict assessments."""
import re

CATEGORIES = {
    'military': '军事行动', 'diplomacy': '外交谈判', 'policy': '制裁与政策',
    'energy': '能源与运输', 'support': '军费与援助', 'domestic': '政治与社会',
}
SIGNALS = {'escalation': '升级相关', 'sustain': '持续相关', 'easing': '缓和相关', 'unknown': '待研判'}
PLACE_NAMES = dict(zip(
    ['5th Fleet','6th Fleet','7th Fleet','Apra Harbor','Arabian Sea','Atlantic Ocean','Bab el-Mandeb','Bahrain','Bremerton','Busan','Caribbean','Centcom','Central Caribbean','Darwin','East China Sea','Eastern Pacific','Ecuador','Everett','Guam','Guantanamo','Gulf of Oman','Hormuz','Indian Ocean','Indo-Pacific','Indopacom','Iwakuni','Kitsap','Luzon Strait','Manila','Mayport','Mediterranean','Middle East','Newport News','Norfolk','Norwegian Sea','Okinawa','Pacific Ocean','Pearl Harbor','Persian Gulf','Perth','Red Sea','Rota','San Diego','Sasebo','Sea of Japan','Singapore','Souda Bay','South China Sea','Southcom','Southern Pacific','Strait of Hormuz','Subic Bay','Suez Canal','Western Atlantic','Western Pacific','Yokosuka'],
    ['第五舰队辖区','第六舰队辖区','第七舰队辖区','阿普拉港','阿拉伯海','大西洋','曼德海峡','巴林','布雷默顿','釜山','加勒比海','中央司令部辖区','加勒比海中部','达尔文','东海','东太平洋','厄瓜多尔','埃弗里特','关岛','关塔那摩','阿曼湾','霍尔木兹','印度洋','印度洋与太平洋地区','印太司令部辖区','岩国','基察普','吕宋海峡','马尼拉','梅波特','地中海','中东','纽波特纽斯','诺福克','挪威海','冲绳','太平洋','珍珠港','波斯湾','珀斯','红海','罗塔','圣迭戈','佐世保','日本海','新加坡','苏达湾','南海','南方司令部辖区','南太平洋','霍尔木兹海峡','苏比克湾','苏伊士运河','西大西洋','西太平洋','横须贺']))
# Regional news anchors only. Never use an inferred location as an observed deployment.
GAZETTEER = [
    ('tehran','德黑兰',51.39,35.69),('kyiv','基辅',30.52,50.45),('kiev','基辅',30.52,50.45),
    ('zaporizhzhia','扎波罗热',35.14,47.84),('donetsk','顿涅茨克',37.80,48.02),
    ('kharkiv','哈尔科夫',36.23,49.99),('odesa','敖德萨',30.72,46.48),
    ('crimea','克里米亚',34.10,45.30),('black sea','黑海',34,43),('moscow','莫斯科',37.62,55.75),
    ('strait of hormuz','霍尔木兹海峡',56.25,26.57),('hormuz','霍尔木兹海峡',56.25,26.57),
    ('red sea','红海',38,20),('bab al-mandab','曼德海峡',43.33,12.58),('bab el-mandeb','曼德海峡',43.33,12.58),
    ('gaza','加沙',34.47,31.50),('lebanon','黎巴嫩',35.86,33.85),('yemen','也门',47,15.5),
    ('oman','阿曼',57,21),('qatar','卡塔尔',51.2,25.3),('saudi arabia','沙特阿拉伯',45,24),
    ('iran','伊朗',54,32),('ukraine','乌克兰',31,49),
]

def event_metadata(record):
    if record.get('supplemental'):
        return {'event_category':'research','event_category_label':'补充研究','situation_signal':'unknown','signal_label':'研究观点','signal_basis':'补充报告，不作为独立事件确认','points':[]}

    title = record.get('original_title', record['title']).lower()
    text = title + ' ' + record.get('original_text', record['text']).lower()
    category = 'domestic'
    rules = [
        ('diplomacy', r'negotiat|ceasefire|peace talks|summit|envoy|diploma|谈判|停火|斡旋'),
        ('policy', r'sanction|embargo|resolution|tariff|制裁|禁运|关税'),
        ('energy', r'\boil\b|gas supply|tanker|pipeline|shipping|strait|energy|能源|原油|运输|海峡'),
        ('support', r'procurement|contract|budget|\baid\b|missile.*produc|military financing|军费|采购|援助|补给'),
        ('military', r'navy|fleet|warship|destroyer|aircraft|strike|drone|missile|military|offensive|部署|打击|军事|舰队'),
    ]
    for content in (title, text):
        match = next((k for k,p in rules if re.search(p,content)), None)
        if match:
            category=match
            break
    signal='unknown'
    if re.search(r'ceasefire.*(?:collapse|violation|break)|launches? (?:an? )?offensive|(?:russian|israeli|iranian) (?:strike|attack)|strikes? (?:on|hit)|tanker hit|(?<!de-)(?<!de )\bescalat|遭袭|发动攻势|冲突升级',title):
        signal='escalation'
    elif re.search(r'ceasefire|peace talks|truce|de.escalat|withdrawal|停火|和谈|撤军|缓和',title):
        signal='easing'
    elif re.search(r'continues?|maintain|extend service|sustain|resupply|持续|维持|补给',title):
        signal='sustain'
    if record.get('event_category') in CATEGORIES:
        category=record['event_category']
    if record.get('situation_signal') in SIGNALS and record.get('signal_basis'):
        signal=record['situation_signal']
    points=[{**p,'label':PLACE_NAMES.get(p['label'],p['label'])} for p in record['points']]
    if not points and record.get('topics'):
        for content in (title, text):
            for term,label,lon,lat in GAZETTEER:
                if (re.search(r'\b'+re.escape(term)+r'\b',content) or label in content) and label not in {p['label'] for p in points}:
                    points.append({'label':label,'lon':lon,'lat':lat,'precision':'area','role':'报道关联地区','evidence_text':term,'location_method':'地名匹配，使用地区参考点'})
                    if len(points)>=3:break
            if points:break
    return {'event_category':category,'event_category_label':CATEGORIES[category],
            'situation_signal':signal,'signal_label':SIGNALS[signal],
            'signal_basis':record.get('signal_basis') or '按报道标题归类的观察线索，整体局势需结合多方证据判断。', 'points':points}
