#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把本插件登记进 WorkBuddy 的插件市场列表（供**别人**安装用）。

背景：WorkBuddy 只认 `~/.workbuddy/plugins/known_marketplaces.json` 里登记的
「目录型市场」。把插件目录拷进 marketplaces/ 还不会生效，得在注册表里挂一笔。

    python3 install_plugin.py              # 预演：只打印将要做的改动
    python3 install_plugin.py --install    # 真正写入（自动备份原文件）

写之前会把原文件备份成 `*.bak-<日期>-install`，可随时回滚。
重复运行安全（幂等）。
"""

import argparse
import datetime
import json
import os
import shutil
import sys

MARKETPLACE = "workbuddy-checkin"
HERE = os.path.dirname(os.path.abspath(__file__))
# <mkt>/plugins/<name>/scripts/install_plugin.py -> 上跳三层才是市场根 <mkt>
MKT_SRC = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
PLUGINS_DIR = os.path.expanduser("~/.workbuddy/plugins")
MKT_DST = os.path.join(PLUGINS_DIR, "marketplaces", MARKETPLACE)
KNOWN = os.path.join(PLUGINS_DIR, "known_marketplaces.json")


def entry_for(path):
    return {
        "manifestName": MARKETPLACE,
        "type": "directory",
        "source": {"source": "directory", "path": path},
        "installLocation": path,
        "description": "WorkBuddy 签到本地插件（每日积分）",
        "lastUpdated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "autoUpdate": False,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="登记 workbuddy-checkin 插件市场")
    ap.add_argument("--install", action="store_true", help="真正写入（默认只预演）")
    args = ap.parse_args(argv)

    print("源市场目录：%s" % MKT_SRC)
    print("目标位置　：%s" % MKT_DST)
    print("注册表　　：%s" % KNOWN)
    print("-" * 62)

    if not os.path.isfile(os.path.join(MKT_SRC, ".codebuddy-plugin", "marketplace.json")):
        print("❌ 源目录不像一个插件市场（缺 .codebuddy-plugin/marketplace.json）")
        return 1

    try:
        known = json.load(open(KNOWN, encoding="utf-8"))
    except Exception as exc:
        print("❌ 读不到注册表 %s：%s" % (KNOWN, exc))
        return 1

    need_copy = os.path.abspath(MKT_SRC) != os.path.abspath(MKT_DST)
    already = MARKETPLACE in known

    print("需要拷贝目录：%s" % ("是" if need_copy else "否（就地安装）"))
    print("注册表已有条目：%s" % ("是（将更新）" if already else "否（将新增）"))

    if not args.install:
        print()
        print("将写入的条目：")
        print(json.dumps(entry_for(MKT_DST), ensure_ascii=False, indent=2))
        print()
        print("预演结束。确认无误后加 --install 执行。")
        return 0

    if need_copy:
        if os.path.exists(MKT_DST):
            shutil.rmtree(MKT_DST)
        os.makedirs(os.path.dirname(MKT_DST), exist_ok=True)
        shutil.copytree(MKT_SRC, MKT_DST)
        print("✅ 已拷贝到 %s" % MKT_DST)

    stamp = datetime.datetime.now().strftime("%Y%m%d")
    bak = "%s.bak-%s-install" % (KNOWN, stamp)
    if not os.path.exists(bak):
        shutil.copy2(KNOWN, bak)
        print("✅ 已备份注册表 -> %s" % bak)

    known[MARKETPLACE] = entry_for(MKT_DST)
    tmp = KNOWN + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(known, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, KNOWN)
    print("✅ 已登记到 %s" % KNOWN)
    print()
    print("接下来：重启 WorkBuddy，在插件列表里启用「workbuddy-checkin」（签到）。")
    print("想每天 8 点自动签到，再跑：python3 install_schedule.py --install")
    return 0


if __name__ == "__main__":
    sys.exit(main())
