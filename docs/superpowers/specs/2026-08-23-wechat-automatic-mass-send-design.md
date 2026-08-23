# 微信公众号自动群发与自动发表设计

## 目标

在既有“生成文章并创建公众号草稿”的定时流水线中，提供可控的自动发布能力：每次 cron 执行产生的一批文章里，第一篇向全部关注者群发；其余文章自动公开发表、但不推送给关注者。

本功能使用微信公众号的官方群发与发表接口，不使用浏览器自动点击后台。

## 已确认的业务规则

- cron 每天只运行一次；不需要在不同 cron 运行之间保存当日群发额度状态。
- 每次 cron 运行视为一个独立批次，沿用现有全局候选队列的排序顺序。
- 启用自动发表后，批次中第一个成功创建的公众号草稿是唯一的“群发候选”。
- 该候选向全部关注者提交群发，并只在可重试的临时失败时重试一次。
- 该候选群发提交成功后，本批剩余文章只自动公开发表。
- 若群发因额度已用、接口未授权、内容审核等不可重试原因失败，或重试仍失败，候选文章本身也改为自动公开发表；剩余文章同样只自动公开发表。
- 后台操作和接口操作共用平台的群发额度。程序不写入或修改 VPS 上的 `.env`，也不会跨天保留“已群发”状态。
- 自动群发/发表不能通过公开 API 自动设置“炼金投研”合集及后台的“AI 生成”来源标识；该限制不在本功能范围内。现有正文 AI 声明继续保留。

## 配置与默认值

新增两个环境变量：

```dotenv
# 默认关闭；false 时维持现有行为：仅创建草稿。
WECHAT_AUTO_PUBLISH=false

# 自动发表启用时，默认允许本批第一篇尝试群发。
WECHAT_MASS_SEND=true
```

含义如下：

| `WECHAT_AUTO_PUBLISH` | `WECHAT_MASS_SEND` | 行为 |
|---|---|---|
| `false` / 未配置 | 任意值 | 创建草稿，不发表、不群发。 |
| `true` | `false` | 每篇创建草稿后自动公开发表，不群发。 |
| `true` | `true` / 未配置 | 第一篇尝试群发一次；其余文章自动公开发表。 |

`WECHAT_AUTO_PUBLISH` 仍以默认关闭保证现有生产部署升级后不会意外消耗群发额度。用户在 VPS `.env` 中显式设为 `true` 后，`WECHAT_MASS_SEND` 未设置即按 `true` 解释，符合“自动发表开启时默认群发首篇”的要求。

## 设计方案

### 1. 微信 API 边界

在 `src/youtube_to_wechat/wechat.py` 中封装三个明确职责的函数：

- `submit_mass_send(access_token, media_id)`：调用 `POST /cgi-bin/message/mass/sendall`，请求 `filter.is_to_all=true`、`msgtype="mpnews"`，返回微信 `msg_id`。
- `submit_publish(access_token, media_id)`：调用 `POST /cgi-bin/freepublish/submit`，返回 `publish_id`。
- `get_mass_send_status(access_token, msg_id)`：调用 `POST /cgi-bin/message/mass/get`，供日志或后续排障查询；本期不等待异步最终送达完成才处理下一篇。

所有函数必须把微信返回的非零 `errcode` 转换成携带错误码和消息的 `WechatError`，让调用方区分“已提交”与“未提交”。群发提交成功只代表微信已接受任务；最终状态仍是异步的。

### 2. 批次状态

在 `scripts/process_sources.py` 的单次 `main()` 调用内创建一个 `WeChatBatchState`，并将其传入每次公众号发布调用。状态只存在内存中：

```text
mass_send_enabled  ← WECHAT_MASS_SEND
mass_send_attempted ← false

每篇草稿创建成功后：
  若 auto_publish=false: 保留草稿
  若 mass_send_enabled 且 mass_send_attempted=false:
      mass_send_attempted=true
      尝试群发（至多一次临时错误重试）
      成功：后续文章仅发表
      失败：当前文章及后续文章仅发表
  否则：发表
```

因此无论一批有多少文章，最多只有一篇会发起群发提交；不会让第二篇再触发“当天已用完群发额度”的失败。

草稿创建失败的文章不消耗群发资格；下一篇成功创建草稿的文章成为群发候选。封面缺失、凭据缺失或草稿创建失败均不执行发表/群发，并写清错误日志。

### 3. 发布器职责

`WechatDraftPublisher` 保留“组装 HTML、上传封面、创建草稿”的职责，并在创建草稿后根据配置与批次状态选择：保留草稿、群发或自动发表。

发布器返回结构化结果，而不是只打印日志。结果至少包含：草稿 `media_id`、动作（`draft` / `mass_send` / `publish`）、群发 `msg_id` 或发表 `publish_id`、是否发生重试及可读失败原因。`process_sources.py` 将该结果补写到对应文章的 `run.json`；已有控制台输出和企业微信提醒也相应说明最终动作。

临时失败只包括连接超时、DNS/网络异常与 HTTP 5xx。群发额度、权限、参数或微信内容校验错误不重试，直接降级到自动发表；发表失败则保留草稿并记录失败，不删除草稿，也不重复创建草稿。

### 4. 通知和可观测性

每篇文章输出一条明确日志，例如：

```text
[Source] WeChat: mass send submitted msg_id=...
[Source] WeChat: published without mass send publish_id=...
[Source] WeChat: mass send unavailable (errcode=...), published instead publish_id=...
[Source] WeChat: publish failed; draft retained media_id=...
```

如果配置了 `WECOM_WEBHOOK`，通知文案与实际结果一致：首篇显示“已提交群发”，其余显示“已自动发表、未群发”，降级时显示群发失败原因与公开发表结果。不会再误提示“请前往草稿箱预览并群发”。

## 非目标

- 不自动向“炼金投研”合集归类。
- 不自动设置公众号后台的 AI 生成来源标识。
- 不使用 Playwright、浏览器会话、二维码登录或后台页面模拟。
- 不跨 cron 执行持久化或推算群发额度。
- 不把未群发文章排队到未来日期再群发。
- 不实施按标签、按 OpenID 列表群发。

## 验收标准

### 配置兼容性

1. 未设置 `WECHAT_AUTO_PUBLISH` 时，运行现有 cron：每篇仅创建草稿；不得调用群发或发表接口。
2. `WECHAT_AUTO_PUBLISH=true`、`WECHAT_MASS_SEND=false` 时，批次内每篇都创建草稿并调用发表接口一次；不得调用群发接口。
3. `WECHAT_AUTO_PUBLISH=true`、`WECHAT_MASS_SEND=true` 或未设置时，单篇批次创建草稿后提交一次群发；不得额外调用发表接口。

### 多篇批次

4. 两篇及以上文章的批次中，第一篇成功创建的草稿仅调用一次群发接口；其余每篇仅调用一次发表接口。
5. 一批十篇文章时，群发接口总调用次数始终不超过一次，发表接口调用次数为九次。
6. 第一篇草稿创建失败时，第二篇成功草稿成为唯一群发候选；之后文章全部仅发表。

### 失败与重试

7. 首篇群发遇到一次网络超时后重试一次；重试成功时不调用该篇发表接口，后续文章只发表。
8. 首篇群发遇到额度/权限/内容校验类微信错误时不重试；该篇调用发表接口一次，后续文章只发表。
9. 首篇群发网络失败两次时，该篇调用发表接口一次，后续文章只发表。
10. 某篇发表接口失败时，草稿保留，`run.json` 与日志记录失败；不得重复创建草稿或把同一草稿再次群发。

### 记录与通知

11. 每篇成功草稿的 `run.json` 记录草稿 ID、最终动作及对应任务 ID（群发 `msg_id` 或发表 `publish_id`）。
12. 配置企业微信 webhook 时，首篇群发、后续发表及群发失败降级三种通知文本均与实际动作匹配。
13. 自动化测试覆盖以上分支，且现有全量测试套件保持通过。

## 实施边界

实现时仅修改 Python 发布链路、测试和说明文档；不修改 cron 表达式、Supabase schema 或现有来源配置。发布前需先在测试公众号或人工可监控的一次正式 cron 中验证接口权限和 IP 白名单，再将 `WECHAT_AUTO_PUBLISH=true` 写入 VPS 环境变量。
