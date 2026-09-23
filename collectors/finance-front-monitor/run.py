#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一键运行 finance-front-monitor（Windows / 任何有 Python 的地方）。

三步流水线，任何一步失败立即停下：
  1. 离线自检（--selftest）
  2. 真实抓取 + 生成报告
  3. 回源对账（--save 留档 output/_verify_last.json）

代理：默认走系统代理自动探测。若系统代理不可用，二选一：
    run.py --proxy http://127.0.0.1:7897     显式指定
    set RM_PROXY=http://127.0.0.1:7897       环境变量（等价）
    run.py --proxy direct                    显式直连（排障用）
（历史备注：系统代理 51681 曾整体 502，7897 可用——但端口会变，
 每轮运行的「出口」都会印在报告头部，别靠记忆，靠打印。）

免交互（定时任务用）：set RM_NO_PAUSE=1

本文件从 run.bat 迁移而来（2026-09-23）。
原来的 run.bat 内容其实是 Python 源码，双击时由 cmd 逐行解释 →
满屏「不是内部或外部命令」。现在 run.bat 只负责找解释器，
流程逻辑住在这里，两者可以各自维护。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
NO_PAUSE = any(os.environ.get(k) == "1"
               for k in ("RM_NO_PAUSE", "RIM_NO_PAUSE", "NOPAUSE"))
README = os.path.join(ROOT, "README.md")


def parse_args(argv):
    """只认 --proxy（值可为 URL 或 direct）；其余原样透传给抓取步骤。"""
    proxy, rest, i = "", [], 0
    while i < len(argv):
        a = argv[i]
        if a == "--proxy" and i + 1 < len(argv):
            proxy = argv[i + 1]
            i += 2
            continue
        if a.startswith("--proxy="):
            proxy = a.split("=", 1)[1]
            i += 1
            continue
        rest.append(a)
        i += 1
    return proxy, rest


def run(label, args):
    print()
    print("=" * 70)
    print(f"[finance-front] {label}")
    print("=" * 70)
    rc = subprocess.call([PY] + args, cwd=ROOT)
    if rc != 0:
        print(f"\n!! {label} 失败（exit={rc}）——流水线在带病产出前停下。")
        if not NO_PAUSE and sys.stdin and sys.stdin.isatty():
            input("按回车关闭…")
        sys.exit(rc)


if __name__ == "__main__":
    print("finance-front-monitor —— 美伊冲突「金融战线」高频指标监测")
    print(f"工程说明（11 节）：{README}")

    cli_proxy, passthrough = parse_args(sys.argv[1:])
    proxy = cli_proxy.strip() or os.environ.get("RM_PROXY", "").strip()
    fetch_args = ["finance_monitor.py"] + (
        ["--proxy", proxy] if proxy else []) + passthrough

    run("1/3 离线自检", ["finance_monitor.py", "--selftest"])
    run("2/3 真实抓取 + 报告", fetch_args)
    run("3/3 回源对账", ["tools/verify_report.py", "--save"])

    print()
    print("=" * 70)
    print("全部完成。报告：output/LATEST.md（对账留档 output/_verify_last.json）")
    print("=" * 70)
    if not NO_PAUSE and sys.stdin and sys.stdin.isatty():
        input("按回车关闭…")
