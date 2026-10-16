#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给「每天 8 点自动签到」装系统级定时器（跨平台）。

DSH 的排程只属于某个会话、且要求宿主开着；要让**别人的机器**上每天自动签到，
得用系统自带的调度器：

    macOS   launchd   ~/Library/LaunchAgents/com.workbuddy.checkin.plist
    Linux   crontab   一行带标记注释的条目
    Windows 任务计划  schtasks /Create /SC DAILY

用法（**默认只打印，不动系统**）：

    python3 install_schedule.py                 # 打印将要写入的内容
    python3 install_schedule.py --install       # 真正安装
    python3 install_schedule.py --uninstall     # 卸载（只删自己那条）
    python3 install_schedule.py --time 08:30    # 改时间（默认 08:00）

可选：
    --python /path/to/python3   指定解释器（默认用当前解释器）
    --platform darwin|linux|win32   仅用于预览/测试别的平台
"""

import argparse
import os
import subprocess
import sys

LABEL = "com.workbuddy.checkin"
CRON_MARK = "# workbuddy-daily-checkin"
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "wb_checkin.py")
LOG = os.path.expanduser("~/.workbuddy/checkin.log")


def plist_path():
    return os.path.expanduser("~/Library/LaunchAgents/%s.plist" % LABEL)


def xml_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def render_plist(python, hour, minute):
    args = "".join(
        "    <string>%s</string>\n" % xml_escape(a)
        for a in (python, SCRIPT, "claim")
    )
    return """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>%s</string>
  <key>ProgramArguments</key>
  <array>
%s  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>%d</integer>
    <key>Minute</key><integer>%d</integer>
  </dict>
  <key>StandardOutPath</key><string>%s</string>
  <key>StandardErrorPath</key><string>%s</string>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
""" % (LABEL, args, hour, minute, xml_escape(LOG), xml_escape(LOG))


def render_cron(python, hour, minute):
    return "%d %d * * * %s %s claim >> %s 2>&1 %s\n" % (
        minute, hour, python, SCRIPT, LOG, CRON_MARK)


def render_schtasks(python, hour, minute):
    return [
        "schtasks", "/Create", "/F", "/SC", "DAILY",
        "/ST", "%02d:%02d" % (hour, minute),
        "/TN", LABEL,
        "/TR", '"%s" "%s" claim' % (python, SCRIPT),
    ]


def _read_crontab():
    try:
        out = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        return out.stdout if out.returncode == 0 else ""
    except FileNotFoundError:
        return ""


def _write_crontab(text):
    subprocess.run(["crontab", "-"], input=text, text=True, check=True)


def install(plat, python, hour, minute):
    if plat == "darwin":
        p = plist_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(render_plist(python, hour, minute))
        subprocess.run(["launchctl", "unload", p], capture_output=True)
        r = subprocess.run(["launchctl", "load", p], capture_output=True, text=True)
        print("已写入 %s" % p)
        if r.returncode != 0:
            print("launchctl load 返回 %d：%s" % (r.returncode, r.stderr.strip()))
        print("每天 %02d:%02d 自动签到已就绪。" % (hour, minute))
    elif plat == "linux":
        lines = [l for l in _read_crontab().splitlines() if CRON_MARK not in l]
        lines.append(render_cron(python, hour, minute).rstrip("\n"))
        _write_crontab("\n".join(lines) + "\n")
        print("已写入 crontab（标记 %s），每天 %02d:%02d。" % (CRON_MARK, hour, minute))
    elif plat == "win32":
        cmd = render_schtasks(python, hour, minute)
        r = subprocess.run(cmd, capture_output=True, text=True)
        print("执行：%s" % " ".join(cmd))
        if r.returncode != 0:
            print("失败：%s" % (r.stderr or r.stdout).strip())
            return 1
        print("每天 %02d:%02d 自动签到已就绪。" % (hour, minute))
    else:
        print("不支持的平台：%s" % plat, file=sys.stderr)
        return 1
    return 0


def uninstall(plat):
    if plat == "darwin":
        p = plist_path()
        subprocess.run(["launchctl", "unload", p], capture_output=True)
        if os.path.exists(p):
            os.remove(p)
            print("已删除 %s" % p)
        else:
            print("没有找到 %s，无需卸载。" % p)
    elif plat == "linux":
        lines = [l for l in _read_crontab().splitlines() if CRON_MARK not in l]
        _write_crontab("\n".join(lines) + "\n" if lines else "")
        print("已从 crontab 移除标记为 %s 的条目。" % CRON_MARK)
    elif plat == "win32":
        subprocess.run(["schtasks", "/Delete", "/F", "/TN", LABEL],
                       capture_output=True, text=True)
        print("已删除计划任务 %s。" % LABEL)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="为每日自动签到安装系统定时器")
    ap.add_argument("--install", action="store_true", help="真正安装（默认只打印）")
    ap.add_argument("--uninstall", action="store_true", help="卸载")
    ap.add_argument("--time", default="08:00", help="触发时间 HH:MM（默认 08:00）")
    ap.add_argument("--python", default=sys.executable, help="python3 解释器路径")
    ap.add_argument("--platform", default=sys.platform,
                    choices=["darwin", "linux", "win32"], help="仅用于预览别的平台")
    args = ap.parse_args(argv)

    try:
        hour, minute = (int(x) for x in args.time.split(":"))
        assert 0 <= hour < 24 and 0 <= minute < 60
    except Exception:
        print("时间格式应为 HH:MM，例如 08:00", file=sys.stderr)
        return 2

    if args.uninstall:
        return uninstall(args.platform)
    if args.install:
        return install(args.platform, args.python, hour, minute)

    print("平台：%s    时间：每天 %02d:%02d" % (args.platform, hour, minute))
    print("解释器：%s" % args.python)
    print("脚本：%s" % SCRIPT)
    print("日志：%s" % LOG)
    print("-" * 62)
    if args.platform == "darwin":
        print("将写入 %s：" % plist_path())
        print(render_plist(args.python, hour, minute))
        print("执行安装：python3 %s --install" % os.path.basename(__file__))
    elif args.platform == "linux":
        print("将加入 crontab：")
        print(render_cron(args.python, hour, minute))
        print("执行安装：python3 %s --install" % os.path.basename(__file__))
    else:
        print("将执行：")
        print(" ".join(render_schtasks(args.python, hour, minute)))
        print("执行安装：python3 %s --install" % os.path.basename(__file__))
    return 0


if __name__ == "__main__":
    sys.exit(main())
