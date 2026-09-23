"""Versioned research definitions. Conditions are hypotheses, not market forecasts."""
TOPICS = [
    {"id": "usiran", "name": "美伊局势", "dimensions": [
        ("us_readiness", "美军战备"), ("iran_readiness", "伊朗战备"),
        ("financial", "金融战线"), ("us_opinion", "美国民意"),
        ("israel", "以色列动向"), ("regional", "中东各国"),
        ("iran_domestic", "伊朗内政"), ("diplomacy", "外交斡旋")]},
    {"id": "ukraine", "name": "俄乌冲突", "dimensions": [
        ("frontline", "军事战线"), ("aid", "乌克兰后援"),
        ("russia_domestic", "俄罗斯内政"), ("eu_domestic", "欧盟内政"),
        ("diplomacy", "外交斡旋")]},
]
RULE_VERSION = "v6.3"
ASSETS = [
    {"id": "equity", "name": "权益", "channel": "增长与贸易 / 财政与产业需求", "mechanism": "成本、需求与风险溢价共同作用；地区和行业暴露需要分别检验。", "condition": "确认盈利或订单传导，并核对估值与市场反应", "counter": "需求下行、成本转嫁能力或估值变化抵消行业催化", "watch": "盈利修正、订单交付、行业相对表现", "horizon": "短期情绪 / 中期盈利", "terms": ["contract", "aid", "production", "采购", "援助", "合同"]},
    {"id": "rates", "name": "利率债", "channel": "通胀与政策 / 风险偏好", "mechanism": "避险需求与通胀、财政供给压力可能方向相反；需要分币种、期限研究。", "condition": "区分增长冲击与通胀冲击，观察政策预期", "counter": "通胀和发行供给压力抵消避险买盘", "watch": "收益率曲线、通胀预期、央行表态", "horizon": "短期 / 中期", "terms": ["fiscal", "budget", "inflation", "spending", "财政", "预算", "通胀"]},
    {"id": "credit", "name": "信用债", "channel": "增长与贸易 / 流动性", "mechanism": "融资条件、盈利和再融资能力影响信用风险；不能用国债方向替代信用利差。", "condition": "确认发行人暴露及融资条件变化", "counter": "政策支持、充足现金流或低直接暴露", "watch": "信用利差、融资成本、现金流", "horizon": "中期", "terms": ["debt", "loan", "sanction", "贷款", "制裁", "融资"]},
    {"id": "commodity", "name": "商品", "channel": "供需与运输 / 风险偏好", "mechanism": "原油与天然气关注供给和替代能力；黄金另需核对实际利率、美元和避险需求。", "condition": "出现可核实的供应、运输或库存变化", "counter": "替代供给、库存释放、需求走弱或风险溢价回落", "watch": "原油 / 天然气 / 黄金、库存、通行量", "horizon": "短期冲击 / 中期供需", "terms": ["oil", "energy", "shipping", "hormuz", "red sea", "能源", "原油", "海峡"]},
    {"id": "fx", "name": "外汇", "channel": "贸易条件 / 利差 / 资本流动", "mechanism": "贸易条件、利差和资金流共同影响货币；需要指定货币对与政策背景。", "condition": "确认贸易或资金流变化及相对政策差异", "counter": "政策干预、相对利差变化或影响已被定价", "watch": "美元、欧元、卢布及相关货币对与利差", "horizon": "短期 / 中期", "terms": ["export", "currency", "sanction", "出口", "汇率", "制裁"]},
]
SCENARIOS = [
    {"id": "sustain", "name": "持续消耗", "condition": "行动延续，补给与支持仍可维持，谈判尚未形成可验证安排", "counter": "交付中断、持续撤出或停火执行", "watch": "行动持续性、补给实际交付、谈判执行", "terms": ["sustain", "aid", "supply", "production", "补给", "援助"]},
    {"id": "escalate", "name": "升级扩散", "condition": "新增独立证据表明行动范围、强度或参与方扩大", "counter": "行动局限、单方夸大战果、未见持续后续行动", "watch": "行动地点与参与方、设施影响、独立核验", "terms": ["strike", "attack", "deploy", "打击", "袭击", "部署"]},
    {"id": "ease", "name": "缓和收束", "condition": "谈判安排伴随可验证的行动降温或协议执行", "counter": "只存在意向声明，实际行动未下降或协议破裂", "watch": "撤出、停火执行、第三方核实", "terms": ["ceasefire", "talks", "withdraw", "停火", "谈判", "撤出"]},
]

def topic_dimensions(topic):
    return dict(next(t["dimensions"] for t in TOPICS if t["id"] == topic))
