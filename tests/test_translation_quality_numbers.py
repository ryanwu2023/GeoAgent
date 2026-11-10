from backend.translation_quality import quality


def test_scaled_numbers_match_chinese_units():
    original = "Iraq Secures U.S. Agreement to Resume Oil Exports Through Strait of Hormuz as War Losses Reach $60 Billion"
    assert quality(original, "伊拉克获得美国同意，恢复经霍尔木兹海峡的石油出口，战争损失达600亿美元")["issues"] == []
    assert quality("Iran exports 1.2 million barrels a day", "伊朗日出口120万桶")["issues"] == []
    assert quality("The US sends 1,200 troops to the region", "美国向该地区增派1200名士兵")["issues"] == []


def test_wrong_magnitude_is_still_flagged():
    issues = quality("War losses reach $60 billion", "战争损失达60亿美元")["issues"]
    assert "数字表达发生变化，需核对原文" in issues


def test_abbreviated_scales_match_chinese_units():
    original = "Iran denies talks on reopening Strait of Hormuz in exchange for $8B in frozen assets"
    assert quality(original, "伊朗否认就以80亿美元冻结资产换取重新开放霍尔木兹海峡进行谈判")["issues"] == []
    assert quality("Kyiv gets $1.5M in aid", "基辅获得150万美元援助")["issues"] == []
    assert quality("Iran gets $8B", "伊朗获得8亿美元")["issues"]
