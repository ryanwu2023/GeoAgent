# -*- coding: utf-8 -*-
"""一次性负向测试：人为复现「第 6 因子原始水位列写空」这一真实故障，
确认新的「落盘 CSV 每列均有值」对账项**真的会 FAIL**。

做法：把 taco.output_row 打补丁，让 TRHBCCCD_n_total 恒为 None，
然后跑一次离线重放。CSV 与 Excel 都走 output_row，所以两边都会空
（正是 2026-09-22 那次故障的形态）。

跑法： python tools/_negative_test.py
预期： 对账 FAIL，且失败项就是「落盘 CSV 每列均有值」。
用完即删，不进交付。
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from tacolib import taco          # noqa: E402
import taco_monitor as tm         # noqa: E402

_orig = taco.output_row


def _broken(result, i, columns=None):
    row = _orig(result, i, columns)
    row["TRHBCCCD_n_total"] = None       # <-- 复现故障：原始水位列写空
    return row


taco.output_row = _broken


def _latest_raw():
    """自动挑出最新一轮 output/<date>/raw 留档。

    原先是硬编码 output/2026-09-22/raw。2026-09-23 CO1 从新浪换成 Yahoo BZ=F
    之后，旧轮次的 raw 里 CO1 还是 sina_jsonp 载荷，拿它重放会撞上
    「payload 类型与当前 config 不符」，多报两条与本次负向目标无关的 FAIL，
    把真正要验证的那一条淹没在噪声里。改成取最新一轮，跟随配置演进。
    """
    base = os.path.join(HERE, "output")
    cands = []
    for name in os.listdir(base):
        if len(name) == 10 and name[4] == "-" and name[7] == "-":
            raw = os.path.join(base, name, "raw")
            if os.path.isdir(raw):
                cands.append((name, raw))
    if not cands:
        raise SystemExit("找不到任何 output/<date>/raw 留档，无法做负向测试")
    return max(cands)


_as_of, _raw = _latest_raw()
sys.argv = ["taco_monitor.py",
            "--from-file", _raw,
            "--as-of", _as_of,
            "--out-dir", os.path.join(HERE, "output", "_negtest")]
rc = tm.main()

print()
print("=" * 70)
print("负向测试退出码 = %s（期望非 0，即对账判 FAIL）" % rc)
print("=" * 70)
sys.exit(0 if rc != 0 else 1)
