# WorkBuddy 每日签到插件

每天领 100 积分。**纯 Python 标准库**（自带 AES-256 实现，零依赖），
macOS / Linux / Windows 通用，**可以直接分享给别人用** —— 别人装上后读的是
**他自己**的登录态，不需要任何配置。

---

## 一、给别人安装（4 步）

### 方式 A：从 GitHub 装（推荐，一步）

在 WorkBuddy 里打开 **设置 → 插件 → 添加市场**，粘贴：

```
leoc114119/workbuddy-checkin
```

然后在插件列表里启用「workbuddy-checkin」即可。**不需要下载任何文件。**

### 方式 B：本地目录装（离线 / 自己改）

```bash
# 1) 把整个 workbuddy-checkin/ 目录放到插件市场目录下
#    ~/.workbuddy/plugins/marketplaces/workbuddy-checkin/

# 2) 登记进插件市场（预演 → 确认无误再加 --install）
python3 ~/.workbuddy/plugins/marketplaces/workbuddy-checkin/plugins/workbuddy-checkin/scripts/install_plugin.py
python3 ~/.workbuddy/plugins/marketplaces/workbuddy-checkin/plugins/workbuddy-checkin/scripts/install_plugin.py --install

# 3) 重启 WorkBuddy，在插件列表里启用「workbuddy-checkin」
```

### 装完都建议跑一次

```bash
# 想每天 8 点自动签到，装系统定时器
python3 .../scripts/install_schedule.py            # 预演
python3 .../scripts/install_schedule.py --install  # 安装（默认 08:00）

# 先自检，确认能读到他自己的账号
python3 .../scripts/wb_checkin.py selftest
```

**不需要任何配置**：登录态路径、解密密钥、API 域名全部自动发现。

> 本仓库同时是一个可直接安装的插件市场（`owner/repo`）。
> 发布方式见 §9。

---

## 二、为什么能跨机器工作（最容易被搞错的一点）

WorkBuddy 的登录态文件里，`accessToken` / `nickname` 等字段是
`$wbEncrypted` 信封加密的。**密钥是两层**：

```
全局常量 protector secret（所有安装都一样）
        │  sha256
        ▼
   protector key  (keyId = 9127dea1b44020a7)
        │  解开 ~/.workbuddy/keyblob 里的 wrapped 段
        ▼
   本机数据密钥 data key  (keyId = 每个机器随机生成)
        │  真正的载体
        ▼
   auth 文件里的 accessToken 等字段
```

- `~/.workbuddy/keyblob`（权限 600）里存着**本机数据密钥**，被 protector key 包着。
- **数据密钥每台机器不同**（首次运行时随机生成）。
- 脚本按信封里的 `keyId` 选钥匙：先拿本机 keyblob 解出的数据密钥试，
  再回退到 protector key。

> ⚠️ **踩过的坑**：一开始直接把 protector secret 当成数据密钥去解密。
> 本机凑巧能用 —— 因为这台机器的 auth 信封 `keyId` 恰好等于 protector 的 keyId。
> 这种写法一到别人机器上就会解不开。现在的实现会先解 keyblob，与机器无关。

**域名也不写死**：从 JWT 的 `iss` 字段推导（`host_src` 会标明来源是
`token.iss` / `auth.domain` / `known-hosts` / `default(兜底)`）。

---

## 三、目录结构

```
workbuddy-checkin/
├── .codebuddy-plugin/marketplace.json          # 市场清单
├── README.md
└── plugins/workbuddy-checkin/
    ├── .codebuddy-plugin/plugin.json           # 插件清单
    ├── SKILL.md                                # 触发词与用法
    └── scripts/
        ├── wb_checkin.py                       # 主程序
        ├── wbcrypto.py                         # $wbEncrypted 解密（纯标准库）
        ├── install_plugin.py                   # 登记插件市场
        └── install_schedule.py                 # 装系统定时器
```

---

## 四、用法

```bash
PY=python3                                  # 系统自带即可，实测 3.9.6 也能跑
DIR=~/.workbuddy/plugins/marketplaces/workbuddy-checkin/plugins/workbuddy-checkin/scripts

$PY $DIR/wb_checkin.py selftest   # 自检：能不能解出密钥、读到账号、推导域名
$PY $DIR/wb_checkin.py status     # 只查状态
$PY $DIR/wb_checkin.py claim      # 查，没签就领（默认）
$PY $DIR/wb_checkin.py check      # 纯只读，绝不触发领取
$PY $DIR/wb_checkin.py accounts   # 列账号与推导出的 host
```

- `--json` 结构化输出 · `--account <名>` 限定账号（可重复） · `-v` 打印请求细节
- 退出码：`0` 正常（含「今天已签」「没有活动」）· 非 0 表示真的出错

### 环境变量（一般不用设）

| 变量 | 作用 |
|---|---|
| `WB_AUTH_DIR` | 手动指定登录态目录 |
| `WB_KEYBLOB` | 手动指定 keyblob 路径 |

### 登录态目录（自动探测）

| 平台 | 路径 |
|---|---|
| macOS | `~/Library/Application Support/CodeBuddyExtension/Data/Public/auth` |
| Windows | `%APPDATA%\CodeBuddyExtension\Data\Public\auth` |
| Linux | `$XDG_CONFIG_HOME/CodeBuddyExtension/Data/Public/auth` |
| 兜底 | `~/.workbuddy/auth` |

---

## 五、修掉的两个原版 bug

| # | 原版问题 | 真相 | 证据 |
|---|---|---|---|
| 1 | 查 `checkin-status` | 该端点是桩数据，对**所有账号**恒定全 0（`active:false`/`streak_days:0`/`total_credits:0`），哪怕对方已连续签到 17 天，会把「已签到」误报成「没签」 | 同一账号真端点 `active:true`/`streak:17`/`total:1700`，桩接口全 0 |
| 2 | host 写死 `copilot.tencent.com` | 不同账号要用不同域名，否则永远 401 | 换成正确 host 后 200；交叉打错 host 得 `401 Authorization Required (APISIX)` |

原版还把 host 按账号名硬编码成一张表；现在改从 JWT `iss` 推导，换账号/换环境都不用改代码。

## 六、三条硬契约

1. **端点**：`POST /v2/billing/meter/checkin-activity-status`（GET 会 404）
2. **鉴权**：`Authorization: Bearer <token>` 即可 —— 服务端**只认 Bearer**。
   `X-User-Id` 与 `X-Domain` 脚本照官方客户端一并带上（无害），但实测
   **不是必需的**：传正确 uid、不传、传全 0 伪造 uid，三者返回完全一致。
3. **host 按账号分**：每个 JWT 的 `iss` 自带签发域，与 auth 文件里的
   `auth.domain` 一致，脚本据此推导。

领取端点：`POST /v2/billing/meter/daily-checkin`。

## 七、⚠️ 判断账号状态看哪几个字段

`daily_credit` / `today_credit` **不能**用来判断「这个账号签没签」：
真端点对**所有**账号恒返回 100，桩端点对**所有**账号恒返回 0，毫无区分力。
（踩过的坑：曾据此误判某账号「漏签、可补领」，实际它**压根没有活动**。）

判断「有没有活动」看这组（两个账号取值截然不同）：

| 字段 | desk（有活动，已签） | ai（**无活动**） | 桩接口 |
|---|---|---|---|
| `active` | `true` | `false` | `false` |
| `activity_name` | `Buddy应用` | **空** | 空 |
| `theme_name` | `Buddy加油站` | **空** | 空 |
| `season` | 10 | **0** | 0 |
| `start_time` / `end_time` | `2026-09-30`→`2026-10-31` | **空** | 空 |
| `action_button.show` | `true` | **false** | false |
| `today_checked_in` | `true` | `false` | `false` |
| `streak_days` | 17 | 0 | 0 |
| `total_credits` | 1700 | 0 | 0 |
| `checkin_dates` | 17 条日期 | `null` | `null` |
| `daily_credit` | 100 | 100 | 0 | ← **无区分力，别用** |

### 没有活动 ≠ 今天没签

`active:false` 表示**该账号当前没有可签到的活动**。此时强行调领取端点只会得到
**HTTP 400「签到活动未开启或已过期」** —— 不是漏签，也无需补领。
脚本已单独识别这种情形（输出 `ℹ️ 该账号当前没有可签到的活动`，退出码仍为 0）。

（不同账号的活动是各自独立的：有的账号有活动，有的没有，属正常情况。）

---

## 八、安全模型

- 令牌只在**内存**里解密后立刻用作请求头，**不落盘、不完整回显**（输出脱敏为前 6 位）。
- 脚本只**读** auth 目录与 keyblob，不写不改。
- 解密是纯标准库实现（自带 AES-256 与 GHASH），无需 `pip install`。
- 无第三方依赖：只用 `urllib` / `json` / `hashlib` / `struct` / `base64`。
- **没有任何数据外发**：除了 WorkBuddy 官方 API，脚本不连任何第三方服务。

> 📢 **分享时请务必说明**：这个插件会读取对方本机的 WorkBuddy 登录令牌
> （只读、只在本机内存中使用）。请只分享给你信任的人，并让 TA 知情。

---

## 九、发布给别人（插件市场）

**自己的市场**（推荐，官方支持、不需要任何人审核）：本插件**已发布**在

> **https://github.com/leoc114119/workbuddy-checkin**

这正是 `.codebuddy-plugin/marketplace.json` 在仓库根、插件在 `plugins/<名字>/`
的官方目录约定。别人在 **设置 → 插件 → 添加市场** 里粘贴来源就能装：

| 来源写法 | 例子 |
|---|---|
| GitHub 简写 | `owner/repo` |
| SSH | `git@github.com:owner/repo.git` |
| ZIP 直链 | `https://example.com/marketplace.zip` |
| 本地路径 | `./path/to/marketplace` |

> 这四种来源是 App 内置的「添加市场」界面原生支持的（源码里的
> `plugins.addMarketplace.sourceTip`），不是变通办法。

**腾讯官方市场**（`codebuddy-plugins-official`，206 个插件）：那个是腾讯
编辑维护的，zip 托管在 `download.codebuddy.cn`、`isBuiltIn: true`，App 里
**没有任何上传入口**。要进去只能按它 README 写的「提交 PR」或走开放平台
入驻+审核，不是自助上架。

给别人之前，**务必**把 §8 那条告知念给对方听。

---

## 十、已知限制

- `scheme: "asym-v1"` 的非对称信封**已支持**（v1.2.0 起）：先用使用者密钥
  拆出文件密钥，再解载荷，**不需要任何私钥**。解密逻辑照抄 App 自身的
  `sealAsymmetricEnvelope` / `openAsymmetricEnvelope`；AAD 按
  `buildFileKeyWrapContext` 与 `buildAuthenticatedContextAad(...,'asym-v1')`
  逐字节重建，并用 Node 原生 crypto 造的真实信封做过交叉验证
  （含 purpose / resourceId / fieldPath / 开发者公钥 id 的篡改用例）。
  ⚠️ 但本机登录态目前全是 `sym-v1`，**没有真实的 asym-v1 样本**可测，
  所以这一条属于「按源码实现 + 往返验证」，不是「实测过生产数据」。
- 定时器依赖系统调度：**机器关机/睡眠时到点不会补跑**
  （macOS launchd 会在唤醒后补跑一次错过的时点）。
- 每天 100 积分是**规则值**；实际到账以服务端为准。

---

## 十一、卸载

```bash
python3 .../scripts/install_schedule.py --uninstall   # 先摘定时器
```

然后删掉 `~/.workbuddy/plugins/marketplaces/workbuddy-checkin/`，
并从 `~/.workbuddy/plugins/known_marketplaces.json` 中删掉 `workbuddy-checkin` 条目
（`install_plugin.py --install` 会留 `*.bak-<日期>-install` 备份可还原）。

> 注：`known_marketplaces.json` / `configured-plugins.json` 由 WorkBuddy **自己维护**
> —— App 运行期间会重写这两个文件。目录型市场只要登记进 `known_marketplaces.json`
> 即可被发现（可参考 `experts` 市场：它在 `configured-plugins.json` 里是 0 条，
> 照样正常工作）。所以**不要**指望手改 `configured-plugins.json` 来启用插件，
> 启用请走 UI。
