---
name: workbuddy-checkin
description: WorkBuddy 每日积分签到。查询今天是否已签到、连续天数与积分，未签到则领取每日 100 积分。当用户说「签到」「WorkBuddy 签到」「领积分」「查签到」「今天签到了吗」时使用。支持多账号，跨 macOS/Linux/Windows。
---

# WorkBuddy 每日签到

每天领 100 积分。这个插件把「读本机登录态 → 解密令牌 → 查状态 → 未签到就领」一条龙做完。
纯标准库、零依赖，读的是**本机**登录态，所以换台机器也能直接用。

## 怎么跑

```bash
PY=python3                                     # 系统自带即可（实测 3.9.6 能跑）
DIR=~/.workbuddy/plugins/marketplaces/workbuddy-checkin/plugins/workbuddy-checkin/scripts

$PY $DIR/wb_checkin.py selftest   # 自检（换机器先跑这个）
$PY $DIR/wb_checkin.py status     # 只查，不领
$PY $DIR/wb_checkin.py claim      # 查，没签就领（默认动作）
$PY $DIR/wb_checkin.py check      # 纯只读，用于验证
$PY $DIR/wb_checkin.py accounts   # 列出本机账号与各自 host
```

加 `--json` 拿结构化输出，加 `--account workbuddy-desktop` 只处理某个账号，
`-v` 看请求细节。

想每天 8 点自动签到：`$PY $DIR/install_schedule.py --install`（先不带 `--install` 预演）。

## 六个要点（都是实测踩出来的）

1. **必须用 `checkin-activity-status`，不是 `checkin-status`。**
   后者是桩接口，对**所有账号**恒定返回全 0（`active:false`、`streak_days:0`、
   `total_credits:0`、`checkin_dates:null`），哪怕账号已连续签到 17 天 ——
   旧脚本就是被它骗的，会把「已签到」误报成「没签」。
   ⚠️ 别拿 `daily_credit` 判断账号状态：它在真端点恒 100（**无论有没有活动**）、
   在桩端点恒 0，**毫无区分力**。判断「有没有活动」看 `active` /
   `activity_name` / `theme_name` / `season` / `start_time`。
2. **「没有活动」≠「今天没签」。**
   `active:false` 且 `activity_name` 为空 = 该账号当前没有可签到的活动档期，
   强行领取只会得到 **HTTP 400「签到活动未开启或已过期」**。别把它当成漏签去补领。
3. **必须用 POST。** 同一路径 GET 会 404。
4. **`X-User-Id` 不是必需的**，服务端只认 `Authorization: Bearer`。
   实测传正确 uid、不传、传全 0 伪造 uid，三者返回完全一致。
   脚本仍然带上它（无害，且与官方客户端行为一致），但别误以为缺了就会被拒。
5. **host 从令牌推导，不是写死的。**
   auth 文件里的 JWT `iss` 自带签发域（如 `https://www.codebuddy.cn/...`），
   脚本据此推出该账号该打哪个域名，`host_src` 会标明来源
   （`token.iss` / `auth.domain` / `known-hosts` / `default` 兜底）。
   用错 host 会一直 401 —— 这是老脚本「永远 401」的根因。
6. **密钥是两层的，别搞混。**
   `~/.workbuddy/keyblob`（权限 600）里存着**本机数据密钥**，它被一个
   全局常量 protector key（keyId `9127dea1b44020a7`）包着；真正解 auth 字段的
   是**数据密钥**，而**数据密钥每台机器都不一样**。
   脚本按信封里的 `keyId` 选钥匙：先用本机 keyblob 解出的数据密钥，再回退 protector。
   ⚠️ 直接把 protector 当数据密钥用会**只在本机碰巧能跑**（本机信封 keyId 恰好相等），
   到别人机器上就解不开。换机器排查问题时先用 `selftest` 确认密钥这一环。

## 安全

- 令牌只在内存里解密后立刻用于请求头，**不落盘、不完整回显**（输出一律脱敏成前 6 位）。
- 脚本只**读**登录态目录与 keyblob，不写不回改。
- 解密是纯标准库实现（自带 AES-256 + GHASH），不需要 `pip install` 任何东西。
- 除 WorkBuddy 官方 API 外不连任何第三方服务。

## 注意

- 两个账号是**独立**的：一个签了不代表另一个签了。
- 已签到再跑 `claim` 是安全的：脚本看到 `today_checked_in` 为真就直接跳过，不会重复请求。
- 两种信封都支持：对称 `sym-v1`（用与 keyId 匹配的密钥直解）与非对称
  `asym-v1`（先用使用者密钥拆出文件密钥再解载荷，**不需要任何私钥**）。
