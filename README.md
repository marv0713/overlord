# overlord

`overlord` 是一套基于 **Serverless 云端架构的自动化内容处理流水线**。它可以无人值守地从公开内容源抓取素材，调用 AI 生成深度文章，并自动排版推送到微信公众号草稿箱；按配置也可以继续自动发布。默认仍是安全的草稿模式，不会自动发布。系统采用“配置与代码解耦”的云端优先设计，同时向下兼容纯本地的开发和测试运行。当前支持 YouTube 频道和播客 RSS（包括小宇宙/RSSHub）两类来源。

## 能做什么

- 从单个 YouTube 视频 URL 获取元数据和字幕。
- 从配置的 YouTube 频道里扫描候选视频。
- 从播客 RSS 中读取剧集，下载音频并转写。
- 记录每个来源的处理进度，避免重复处理。
- 按全局系列编号生成公众号文章。
- 按不同博主/视频类型选择不同 writer profile。
- 生成本地 `article.md`、`article.html`、`transcript.txt`、`meta.json` 和 `run.json`。
- 可选推送到微信公众号草稿箱；默认仅创建草稿，也可按配置自动发布。

## 核心概念

- **source**：内容来源适配器。当前支持 `youtube_channel` 和 `podcast_rss`。
- **transcript**：供模型处理的正文文本，可能来自字幕、音频转写或正文抽取。
- **writer profile**：写作模板。用于区分单公司深度拆解、市场评论、访谈等文章结构。
- **processed store**：进度记录与状态管理。支持本地 `data/processed.json` 或云端 Supabase PostgreSQL 数据库。
- **compare evaluation**：字幕和音频转写的对比评估流程，不是默认发布路径。

## 目录结构

```text
config/
  sources.example.json          # 可提交的示例来源配置
  writer_profiles/              # 可提交的写作模板
docs/
  cloud_architecture.md         # 云端架构与数据库配置说明
scripts/
  process_youtube.py            # 单视频入口
  process_xiaoyuzhou.py         # 单个播客 RSS 剧集入口
  process_sources.py            # 配置来源批处理入口
  push_wechat_draft.py          # 推送微信公众号草稿
  generate_cover.py             # 生成封面图
src/youtube_to_wechat/
  *.py                          # 核心模块
tests/
  test_*.py                     # unittest 测试
```

本地运行产生的 `data/`、`outputs/`、`.env`、`config/sources.json` 都会被 `.gitignore` 忽略。

## 安装

建议使用 Python 3.12：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

系统还需要能运行 `yt-dlp`。依赖安装后，代码会优先使用环境里的 `yt-dlp`，找不到时会尝试 `python -m yt_dlp`。

播客转写依赖 `faster-whisper`。第一次运行会下载 Whisper 模型，音频较长时处理会比较慢。

## 配置

复制环境变量示例：

```bash
cp .env.example .env
```

需要生成文章时配置：

```text
GEMINI_API_KEY=
```

需要启用云端多端同步与动态配置（推荐）时配置：

```text
SUPABASE_DB_URL=postgresql://<user>:<password>@<host>:<port>/postgres
```

需要推送公众号草稿时再配置：

```text
WECHAT_APPID=
WECHAT_APPSECRET=
WECHAT_AUTHOR=
WECHAT_AUTO_PUBLISH=false
WECHAT_MASS_SEND=true
WECOM_WEBHOOK=
```

微信公众号发布开关：

- `WECHAT_AUTO_PUBLISH=false` 或未设置：所有成功结果都保留为草稿，沿用当前的人工提醒流程。
- `WECHAT_AUTO_PUBLISH=true` 且 `WECHAT_MASS_SEND=false`：每篇成功创建的草稿都会自动发布，但不会推送给粉丝。
- `WECHAT_AUTO_PUBLISH=true` 且 `WECHAT_MASS_SEND=true` 或未设置：每个 cron 批次中第一篇成功创建的草稿会提交群发给全部粉丝，后续草稿自动发布但不推送给粉丝。`WECHAT_MASS_SEND` 只有在自动发布开启时才生效；未设置时代码默认为 `true`。

启用自动发布前，公众号账号必须具备发布（`freepublish`）权限；只有 `WECHAT_MASS_SEND` 的有效值为 `true`（显式设置为 `true` 或未设置而采用默认值）时才需要群发权限。相关 API 调用还要求将 VPS IP 加入公众号 API 白名单。自动 API 无法设置合集，也无法设置“来源：官方 AI 生成”标记；文章正文中的 AI 生成披露仍会保留。

明确可判断的群发拒绝或失败（例如配额、权限、校验等错误）会为该文章回退到 `freepublish` 公开发布；群发结果歧义时不会自动重试或回退，必须人工核验，不要直接盲目重跑整个批次。API 与后端共享配额：群发只有在确认请求于发送前失败时才安全重试一次；一旦进入歧义状态，后续条目不会再提交群发。提交群发是异步操作，不等同于已经送达粉丝。

设置 `WECOM_WEBHOOK` 后，微信公众号推送结果（包括成功和需要人工核验的结果）会按结果发送企业微信机器人提醒；未设置时不发送该提醒。微信公众号结果的 `action` 含义为：`draft` 表示草稿已保留（未自动发布，或自动发布失败）；`mass_send` 表示已向群发 API 提交，`task_id` 是 API 返回的任务 ID；`publish` 表示已向 `freepublish` API 提交（未群发），`task_id` 是 API 返回的发布 ID；`mass_send_unknown` 表示群发提交结果不确定；`publish_unknown` 表示公开发布提交结果不确定。后两种结果都必须先到微信公众号后台核对，绝不要盲目重跑或重新提交受影响的文章。`error_code` 和 `error_message` 记录 API 错误或降级/核验原因，`publish` 结果中的错误字段可能记录群发失败后回退发布的原因；unknown 结果的 `task_id` 可能为空，不代表没有提交。

终端日志是首要核验依据；每次运行的 `run.json` 只有在 `PublishResult` 已返回、原有 `run.json` 可读且结果成功持久化后才会出现 `wechat_publish`。因此缺少该字段不能证明没有提交，尤其不能排除结果持久化失败；如果一次运行配置了多个目的地，该字段还会被后续目的地结果覆盖。同时检查控制台日志，并按需配置 `WECOM_WEBHOOK` 获取提醒。

复制来源配置示例：

```bash
cp config/sources.example.json config/sources.json
```

如果你配置了 `SUPABASE_DB_URL`，系统将直接连接云端数据库（参考 `docs/cloud_architecture.md`），读取云端配置与 Prompt，本地配置将仅作为未联网时的降级备用（Fallback）。

`config/sources.json`（或云端配置）字段说明：

- `type`：当前支持 `youtube_channel`、`podcast_rss`。
- `name`：来源名称。
- `url`：来源主页地址，例如 YouTube 频道页或小宇宙节目页。
- `rss_url`：播客 RSS 地址，仅 `podcast_rss` 需要。小宇宙可使用 RSSHub 路由。
- `enabled`：是否启用该来源。
- `series`：公众号系列名。
- `priority`：全局队列优先级，数字越小越先处理。
- `min_duration_seconds`：过滤短视频。
- `writer_profile`：使用 `config/writer_profiles/<name>.md`。
- `compare_evaluation`：默认 `none`。
- `destinations`：发布目的地，可用 `wechat_draft`、`pushplus`、`email`；省略时默认是 `["wechat_draft"]`，因此省略并不是关闭微信推送。显式使用微信公众号时写 `"destinations": ["wechat_draft"]`；只发邮件、不走微信时写 `"destinations": ["email"]`，只发 PushPlus、不走微信时写 `"destinations": ["pushplus"]`。也可以组合已支持的目的地，例如 `["wechat_draft", "pushplus"]`。

## 运行

单视频提取字幕和本地输出：

```bash
PYTHONPATH=src .venv/bin/python scripts/process_youtube.py "https://www.youtube.com/watch?v=VIDEO_ID" --skip-audio
```

查看配置来源下一步会处理哪些视频：

```bash
PYTHONPATH=src .venv/bin/python scripts/process_sources.py --dry-run --max-items 5
```

处理配置来源中的下一条视频，只生成本地 transcript 和占位文章：

```bash
PYTHONPATH=src .venv/bin/python scripts/process_sources.py --max-items 1
```

处理配置来源中的下一条视频，并调用 Gemini 生成文章：

```bash
PYTHONPATH=src .venv/bin/python scripts/process_sources.py --generate-article --max-items 1
```

列出小宇宙/RSS 播客的最新剧集：

```bash
PYTHONPATH=src .venv/bin/python scripts/process_xiaoyuzhou.py \
  --rss "https://rsshub.app/xiaoyuzhou/podcast/PODCAST_ID" \
  --list
```

处理单个播客 RSS 的最新合格剧集，下载音频并转写：

```bash
PYTHONPATH=src .venv/bin/python scripts/process_xiaoyuzhou.py \
  --rss "https://rsshub.app/xiaoyuzhou/podcast/PODCAST_ID"
```

处理单个播客剧集并调用 Gemini 生成文章：

```bash
PYTHONPATH=src .venv/bin/python scripts/process_xiaoyuzhou.py \
  --rss "https://rsshub.app/xiaoyuzhou/podcast/PODCAST_ID" \
  --generate-article
```

推送本地文章到微信公众号草稿箱：

```bash
PYTHONPATH=src .venv/bin/python scripts/push_wechat_draft.py \
  outputs/youtube/<source_slug>/<video_id>/article.md \
  --cover outputs/youtube/<source_slug>/<video_id>/cover.png
```

批量生成并按配置推送（包括自动发布）时，来源配置可以显式写 `"destinations": ["wechat_draft"]`；若省略 `destinations`，代码同样默认走微信公众号草稿目的地：

```bash
PYTHONPATH=src .venv/bin/python scripts/process_sources.py \
  --generate-article --push --max-items 1
```

上面的 `--push` 入口才会读取 `WECHAT_AUTO_PUBLISH` 和 `WECHAT_MASS_SEND`。`scripts/push_wechat_draft.py` 是仅创建草稿的手动入口，会忽略这两个自动发布开关。

生成封面图：

```bash
PYTHONPATH=src .venv/bin/python scripts/generate_cover.py \
  --ticker "MSFT 微软" \
  --subtitle "Ackman AI 换仓" \
  --issue "No.002" \
  --hook "价值洼地，还是暗藏杀机？" \
  --output outputs/youtube/<source_slug>/<video_id>/cover.png
```

## 队列规则

`process_sources.py` 会构建一个全局候选队列：

- 所有来源共享同一个 `series` 编号，例如 `No.001`、`No.002`。
- 先按 `priority` 排序。
- 对已有处理记录的 YouTube 来源，会继续补处理历史记录之间漏掉的未处理视频，并按发布时间从旧到新处理。
- 对已有处理记录的播客来源，会处理未处理且符合时长要求的剧集，并按发布时间从旧到新处理。
- 对新加入、没有历史记录的来源，只选最新一条符合时长要求的内容，避免一次性回填旧内容。
- 每次默认只处理 1 条，可通过 `--max-items` 调整。

`process_sources.py` 的 `--output-dir` 是批处理输出根目录。默认值沿用早期 YouTube 路径 `outputs/youtube`；如果混合处理 YouTube 和播客，可以显式指定成 `outputs/sources`。

单播客调试入口 `process_xiaoyuzhou.py` 默认输出到：

```text
outputs/
  xiaoyuzhou/
    <podcast_slug>/
      <episode_id>/
        audio/
        transcript.txt
        meta.json
        article.md
        article.html
        run.json
```

## Writer Profiles

writer profile 放在 `config/writer_profiles/`：

- `deep-stock-analysis.md`：单公司/少数公司深度投研。
- `market-commentary.md`：市场评论、行业趋势、多公司横向讨论。

通用合规要求在 `src/youtube_to_wechat/writer.py`，profile 只负责补充结构和风格。生成文章时会去掉开头免责声明，仅保留文末免责声明。

## 微信排版

微信公众号草稿推送使用 `src/youtube_to_wechat/wechat.py` 里的 Markdown 转 HTML 逻辑。当前样式针对手机阅读做了处理：

- 不使用 `text-align: justify`，避免 iPhone 窄屏中英混排时字距被拉大。
- 段落和列表左对齐。
- 保留适度行高和较小段间距。

## 测试

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

## 安全边界

- 不提交 `.env`。
- 不提交真实 `config/sources.json`。
- 不提交 `data/processed.json`。
- 不提交 `outputs/` 下的生成文章、封面或草稿素材。
- 默认只创建公众号草稿；启用自动发布后才会按上述开关发布。
