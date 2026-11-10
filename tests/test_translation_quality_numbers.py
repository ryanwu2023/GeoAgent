from backend.translation_quality import quality


def test_scaled_numbers_match_chinese_units():
    original = "Iraq Secures U.S. Agreement to Resume Oil Exports Through Strait of Hormuz as War Losses Reach $60 Billion"
    assert quality(original, "伊拉克获得美国同意，恢复经霍尔木兹海峡的石油出口，战争损失达600亿美元")["issues"] == []
    assert quality("Iran exports 1.2 million barrels a day", "伊朗日出口120万桶")["issues"] == []
    assert quality("The US sends 1,200 troops to the region", "美国向该地区增派1200名士兵")["issues"] == []


def test_wrong_magnitude_is_still_flagged():
    issues = quality("War losses reach $60 billion", "战争损失达60亿美元")["issues"]
    assert "数字表达发生变化，需核对原文" in issues
