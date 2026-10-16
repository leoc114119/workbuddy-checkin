#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WorkBuddy 每日积分签到 —— 本地插件版（可共享）。

用法：
    wb_checkin.py status     只查签到状态（只读）
    wb_checkin.py claim      查状态，未签到则领取（默认动作）
    wb_checkin.py check      同 status，显式只读，用于校验
    wb_checkin.py accounts   列出本机发现的账号与各自 host（不联网）
    wb_checkin.py selftest   只验解密链路（keyblob → 令牌 → host），不联网、不改状态

通用参数：
    --json        以 JSON 输出（便于脚本/自动化消费）
    --account X   只处理某个账号，可重复
    -v/--verbose  打印更多过程信息（令牌始终脱敏）

环境变量：
    WB_AUTH_DIR   覆盖登录态目录（自动探测失败时手动指定）

设计要点（均为实测结论，勿随意改动）：
  * 状态端点必须用 POST /v2/billing/meter/checkin-activity-status。
    同目录的 checkin-status 是桩接口，对**所有账号**恒定返回 active:false、
    streak:0、total_credits:0、checkin_dates:null（哪怕对方已连续签到 17 天），
    会把「今天已签到」误报成「未签到」。
  * 判断账号状态请看 active / activity_name / theme_name / season / start_time /
    today_checked_in / streak_days / total_credits / checkin_dates。
    **不要用 daily_credit**：真端点对所有账号恒 100、桩端点恒 0，没有区分力。
    （曾据此误判某账号「漏签可补领」，实际它压根没有活动 —— active:false 且
    activity_name/theme_name/season/start_time 全空。）
  * 「没有活动」不等于「今天没签」：前者调领取端点只会得到 HTTP 400
    「签到活动未开启或已过期」，应当跳过而不是报错。
  * host **不写死账号名**：从令牌自己的 iss（签发方）推导，其次 auth.domain，
    最后才退回已知映射。账号文件名各版本/各渠道可能不同，写死必然漏。
  * 鉴权只需 Authorization: Bearer。X-User-Id / X-Domain 照官方客户端一并带上
    （实测传正确 uid、不传、传伪造 uid 三者返回一致，即并非必需）。

安全：令牌只在内存中解密后立即用于请求头，不落盘、不完整回显。
"""

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wbcrypto import (  # noqa: E402
    KEYBLOB_PATH, candidate_keys, mask, open_wb_field, unwrap_keyblob,
)

# 已知账号名 → host。仅作**最后兜底**；正常情况下 host 由令牌的 iss 推导。
KNOWN_HOSTS = {
    "workbuddy-desktop": "www.codebuddy.cn",
    "workbuddy-desktop-ai": "www.workbuddy.ai",
}
DEFAULT_HOST = "www.codebuddy.cn"

PATH_STATUS = "/v2/billing/meter/checkin-activity-status"
PATH_CLAIM = "/v2/billing/meter/daily-checkin"
TIMEOUT = 25


def auth_dirs():
    """跨平台候选登录态目录，按优先级排列。"""
    home = os.path.expanduser("~")
    leaf = ("CodeBuddyExtension", "Data", "Public", "auth")
    cands = []
    if sys.platform == "darwin":
        cands.append(os.path.join(home, "Library", "Application Support", *leaf))
        cands.append(os.path.join(home, ".workbuddy", "auth"))
    elif os.name == "nt":
        appdata = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
        cands.append(os.path.join(appdata, *leaf))
        cands.append(os.path.join(home, ".workbuddy", "auth"))
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
        cands.append(os.path.join(xdg, *leaf))
        cands.append(os.path.join(home, ".workbuddy", "auth"))
    override = os.environ.get("WB_AUTH_DIR")
    if override:
        cands.insert(0, os.path.expanduser(override))
    return cands


def find_auth_dir():
    """返回第一个存在且含 .info 的目录；找不到返回 None。"""
    for d in auth_dirs():
        if os.path.isdir(d) and any(f.endswith(".info") for f in os.listdir(d)):
            return d
    return None


def host_from_token(token):
    """从 JWT 解出签发方域名 —— 这是「该打哪个 host」最可靠的来源。"""
    try:
        parts = str(token).split(".")
        if len(parts) < 2:
            return None
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8", "replace"))
    except Exception:
        return None
    for field in ("iss", "domain", "aud"):
        v = data.get(field)
        for item in (v if isinstance(v, list) else [v]):
            if isinstance(item, str) and "://" in item:
                host = urllib.parse.urlsplit(item).hostname
                if host:
                    return host
    return None


class Account:
    def __init__(self, name, path):
        self.name = name
        self.path = path
        self.host = None
        self.host_src = None
        self.token = None
        self.uid = None
        self.domain = None
        self.nickname = None
        self.error = None

    def load(self):
        """读取并解密登录态；失败时记录 error 而不是抛出，便于多账号并存。"""
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            self.error = "读取失败: %s: %s" % (type(exc).__name__, exc)
            return self

        auth = data.get("auth") or {}
        account = data.get("account") or {}
        try:
            self.token = open_wb_field(auth.get("accessToken"))
        except Exception as exc:
            self.error = "令牌解密失败: %s: %s" % (type(exc).__name__, exc)
            return self

        self.uid = account.get("uid") or ""
        self.domain = auth.get("domain") or ""

        # host 优先级：令牌 iss > auth.domain > 已知映射 > 默认
        iss_host = host_from_token(self.token)
        if iss_host:
            self.host, self.host_src = iss_host, "token.iss"
        elif self.domain:
            self.host, self.host_src = self.domain, "auth.domain"
        elif self.name in KNOWN_HOSTS:
            self.host, self.host_src = KNOWN_HOSTS[self.name], "known-hosts"
        else:
            self.host, self.host_src = DEFAULT_HOST, "default(兜底)"

        try:
            self.nickname = open_wb_field(account.get("nickname"))
        except Exception:
            self.nickname = None
        if not self.token or not self.uid:
            self.error = "登录态缺少 accessToken 或 uid"
        return self

    def request(self, path, method="POST", verbose=False):
        url = "https://%s%s" % (self.host, path)
        # 服务端只认 Bearer；X-User-Id / X-Domain 与官方客户端保持一致地带上，
        # 但实测并非必需（不传 / 传伪造 uid 结果完全相同）。
        headers = {
            "Authorization": "Bearer " + self.token,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-User-Id": self.uid,
        }
        if self.domain:
            headers["X-Domain"] = self.domain
        body = b"{}" if method == "POST" else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        if verbose:
            print("    → %s %s (X-User-Id=%s, token=%s)"
                  % (method, url, self.uid, mask(self.token)), file=sys.stderr)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return resp.status, json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                return exc.code, json.loads(raw)
            except Exception:
                return exc.code, {"raw": raw[:300]}
        except Exception as exc:
            return "ERR", {"error": "%s: %s" % (type(exc).__name__, exc)}

    def status(self, verbose=False):
        code, payload = self.request(PATH_STATUS, "POST", verbose)
        if code != 200:
            return {"ok": False, "http": code, "error": _err_of(payload, code)}
        data = (payload or {}).get("data") or {}
        if (payload or {}).get("code") != 0:
            return {"ok": False, "http": code,
                    "error": (payload or {}).get("msg") or "接口返回非 0"}
        return {
            "ok": True,
            "active": bool(data.get("active")),
            "activity_name": data.get("activity_name") or "",
            "theme_name": data.get("theme_name") or "",
            "season": data.get("season") or 0,
            "start_time": data.get("start_time") or "",
            "end_time": data.get("end_time") or "",
            "today_checked_in": bool(data.get("today_checked_in")),
            "streak_days": data.get("streak_days") or 0,
            "daily_credit": data.get("daily_credit") or 0,
            "today_credit": data.get("today_credit") or 0,
            "total_credits": data.get("total_credits"),
            "checkin_dates": data.get("checkin_dates") or [],
        }

    def claim(self, verbose=False):
        code, payload = self.request(PATH_CLAIM, "POST", verbose)
        payload = payload or {}
        if code != 200:
            return {"ok": False, "http": code, "error": _err_of(payload, code)}
        if payload.get("code") not in (0, None):
            return {"ok": False, "http": code,
                    "error": payload.get("msg") or "领取失败",
                    "code": payload.get("code")}
        data = payload.get("data") or {}
        return {
            "ok": True,
            "credit": data.get("credit") or payload.get("credit") or 0,
            "streak_days": data.get("streak_days") or payload.get("streak_days") or 0,
        }


def _err_of(payload, http_code):
    payload = payload or {}
    if payload.get("msg"):
        return payload["msg"]
    if payload.get("error"):
        return payload["error"]
    if payload.get("raw"):
        return payload["raw"]
    if http_code == 401:
        return "401 未授权（host 用错了？该账号需打自己的域名）"
    return "HTTP %s" % http_code


def discover(only=None, auth_dir=None):
    """扫描登录态目录，返回账号列表。"""
    d = auth_dir or find_auth_dir()
    if not d:
        return []
    accounts = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".info"):
            continue
        name = fn[:-len(".info")]
        if only and name not in only:
            continue
        accounts.append(Account(name, os.path.join(d, fn)).load())
    return accounts


def cmd_accounts(args):
    d = find_auth_dir()
    accounts = discover(args.account)
    rows = []
    for acc in accounts:
        rows.append({
            "account": acc.name,
            "host": acc.host,
            "host_source": acc.host_src,
            "uid": acc.uid or None,
            "nickname": acc.nickname,
            "token": mask(acc.token) if acc.token else None,
            "usable": acc.error is None,
            "error": acc.error,
        })
    if args.json:
        print(json.dumps({"auth_dir": d, "accounts": rows}, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("未发现任何登录态。已尝试：")
        for c in auth_dirs():
            print("  - %s %s" % (c, "(存在)" if os.path.isdir(c) else "(不存在)"))
        print("可用 WB_AUTH_DIR 手动指定目录。")
        return 1
    print("登录态目录：%s" % d)
    for r in rows:
        print("%-26s host=%-18s [%s] %s" % (
            r["account"], r["host"], r["host_source"],
            "✅ " + r["nickname"] if r["usable"] else "❌ " + str(r["error"])))
    print("\n令牌一律脱敏，不落盘。")
    return 0


def cmd_selftest(args):
    """只验解密链路，不联网、不改任何状态。"""
    ok = True
    print("1) keyblob")
    print("   路径：%s %s" % (KEYBLOB_PATH, "(存在)" if os.path.isfile(KEYBLOB_PATH) else "(不存在)"))
    kb = unwrap_keyblob()
    if kb:
        for kid in kb:
            print("   ✅ 解出本机数据密钥 keyId=%s" % kid)
    else:
        print("   ℹ️  未能解出数据密钥（若 auth 字段用保护密钥加密，仍可正常工作）")
    keys = candidate_keys()
    print("   可用密钥：%s" % ", ".join(sorted(k for k in keys if k)))

    print("2) 登录态目录")
    d = find_auth_dir()
    if not d:
        print("   ❌ 未找到。尝试过的路径：")
        for c in auth_dirs():
            print("      - %s" % c)
        return 1
    print("   ✅ %s" % d)

    print("3) 令牌解密与 host 推导")
    accounts = discover(args.account)
    if not accounts:
        print("   ❌ 目录内没有 .info")
        return 1
    for acc in accounts:
        if acc.error:
            print("   ❌ %s：%s" % (acc.name, acc.error))
            ok = False
        else:
            print("   ✅ %s：token=%s host=%s（来源 %s）"
                  % (acc.name, mask(acc.token), acc.host, acc.host_src))
    print()
    print("自检通过。" if ok else "自检未通过。")
    return 0 if ok else 1


def run(args, do_claim):
    accounts = discover(args.account)
    if not accounts:
        print("未发现任何 WorkBuddy 登录态。已尝试：", file=sys.stderr)
        for c in auth_dirs():
            print("  - %s" % c, file=sys.stderr)
        print("（工作区外的机器可用 WB_AUTH_DIR 指定目录）", file=sys.stderr)
        return 2

    results = []
    exit_code = 0
    for acc in accounts:
        entry = {"account": acc.name, "host": acc.host, "nickname": acc.nickname}
        if not args.json:
            print("─" * 62)
            print("账号 %s  (%s)" % (acc.name, acc.nickname or "?"))
            print("  host: %s" % acc.host)

        if acc.error:
            entry.update({"ok": False, "stage": "auth", "error": acc.error})
            results.append(entry)
            exit_code = max(exit_code, 1)
            if not args.json:
                print("  ❌ %s" % acc.error)
            continue

        st = acc.status(args.verbose)
        entry["status"] = st
        if not st.get("ok"):
            entry.update({"ok": False, "stage": "status", "error": st.get("error")})
            results.append(entry)
            exit_code = max(exit_code, 1)
            if not args.json:
                print("  ❌ 状态查询失败：%s" % st.get("error"))
            continue

        if not args.json:
            if st["active"]:
                print("  活动：进行中（%s）" % (st["activity_name"] or "未命名"))
            else:
                print("  活动：该账号没有进行中的签到活动")
            print("  今日已签到：%s" % ("是" if st["today_checked_in"] else "否"))
            print("  连续天数：%d 天" % st["streak_days"])
            print("  每日积分：%s" % st["daily_credit"])

        # 没有活动 ≠ 今天没签。前者领不了（服务端返回 400「签到活动未开启或已过期」），
        # 必须单独判定，否则会把「该账号压根没有活动」误报成「漏签了、快去补领」。
        if not st["active"]:
            entry.update({"ok": True, "action": "no_activity"})
            if not args.json:
                print("  ℹ️  该账号当前没有可签到的活动，跳过（不是漏签，也无需补领）。")
            results.append(entry)
            continue

        if st["today_checked_in"]:
            entry.update({"ok": True, "action": "already_checked_in"})
            if not args.json:
                print("  ✅ 今天已签过，无需操作。")
            results.append(entry)
            continue

        if not do_claim:
            entry.update({"ok": True, "action": "would_claim"})
            if not args.json:
                print("  ⏳ 今天还没签（只读模式，未领取）。跑 `claim` 即可领取。")
            results.append(entry)
            continue

        cl = acc.claim(args.verbose)
        entry["claim"] = cl
        if cl.get("ok"):
            entry.update({"ok": True, "action": "claimed"})
            if not args.json:
                print("  🎉 签到成功：+%s 积分（连续 %s 天）"
                      % (cl.get("credit", 0), cl.get("streak_days", 0)))
        else:
            entry.update({"ok": False, "stage": "claim", "error": cl.get("error")})
            exit_code = max(exit_code, 1)
            if not args.json:
                print("  ❌ 领取失败：%s" % cl.get("error"))
        results.append(entry)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("─" * 62)
        counted = [r for r in results if r.get("action") != "no_activity"]
        skipped = len(results) - len(counted)
        ok = sum(1 for r in counted if r.get("ok"))
        msg = "合计 %d 个账号，成功 %d 个" % (len(results), ok)
        if skipped:
            msg += "，%d 个无签到活动（跳过，非失败）" % skipped
        print(msg + "。")
    return exit_code


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="WorkBuddy 每日积分签到（本地插件版，可共享）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n  wb_checkin.py selftest\n  wb_checkin.py status\n  wb_checkin.py claim --json",
    )
    ap.add_argument("action", nargs="?", default="claim",
                    choices=["status", "claim", "check", "accounts", "selftest"],
                    help="status=只查 / claim=查并领取(默认) / check=纯只读 / "
                         "accounts=列账号 / selftest=只验解密链路")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--account", action="append", help="只处理指定账号（可重复）")
    ap.add_argument("-v", "--verbose", action="store_true", help="打印请求细节")
    args = ap.parse_args(argv)

    if args.action == "selftest":
        return cmd_selftest(args)
    if args.action == "accounts":
        return cmd_accounts(args)
    if args.action in ("status", "check"):
        return run(args, do_claim=False)
    return run(args, do_claim=True)


if __name__ == "__main__":
    sys.exit(main())
