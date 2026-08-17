# The Intrinsic Value Podcast 配置设计

## 目标

在不修改生产代码和 cron 的前提下，把 The Intrinsic Value Podcast 加入 VPS 上既有的 YouTube → 微信公众号草稿流水线，并一次性回填最近 10 期。

## 生产现状

- VPS 每日 UTC 13:25（北京时间 21:25）执行 `process_sources.py --generate-article --push --max-items 10 --channel-limit 40`。
- `.env` 已配置 Supabase、Gemini 和微信公众号凭据。
- `SUPABASE_DB_URL` 存在时，`sources` 与 `writer_profiles` 均从 Supabase 的 `overlord_config` 表读取。
- 处理状态存放在 Supabase 的 `overlord_state` 表，键为 `state`。
- YouTube 账号已经订阅该频道，无需更改 YouTube 状态。

## 配置变更

在 `overlord_config.sources` 中加入：

- 名称：`The Intrinsic Value Podcast`
- URL：`https://www.youtube.com/@TheIntrinsicValuePodcast/videos`
- 类型：`youtube_channel`
- 系列：`炼金投研`
- 优先级：`5`
- 最低时长：`600`
- 模板：`intrinsic-value-auto`
- 目标：`wechat_draft`

在 `overlord_config.writer_profiles` 中加入 `intrinsic-value-auto`。该模板要求模型先判断视频的主要投资对象：

- 主要分析一家公司时采用 `deep-stock-analysis` 的章节结构；提到竞争对手不改变判断。
- 并列分析两家及以上公司，或属于组合扫描、市场圆桌、投资框架讨论时，采用 `interview` 的章节结构。
- 不补充转录文本以外的事实，所有判断归因于主持人或嘉宾。

## 初次回填

现有代码对全新来源默认只选择最新一条。为回填最近 10 条，不修改代码，而是在运行前向 `overlord_state.state.sources` 写入该来源的空扫描记录，使其被视为已存在来源。

随后以现有批处理命令运行一次，但将 `--channel-limit` 设为 `10`。来源优先级为 5，排在其他来源之前，因此 `--max-items 10` 会选中该频道最新 10 条，并按发布时间从旧到新生成期号、文章、封面和微信草稿。

运行成功后，既有每日 cron 无需修改；以后只处理该频道的新视频。

## 安全与恢复

- 修改前备份 `overlord_config` 的 `sources`、`writer_profiles` 和完整 `overlord_state.state` 为带时间戳的 JSON 文件。
- 配置写入使用单个数据库事务。
- 更新操作按来源名称和模板名称幂等执行，重复运行不会创建重复来源。
- 初次导入前先用程序的有效配置读取路径验证来源和模板可见。
- 单篇失败保留运行日志；未成功生成文章的条目不会提交草稿。

## 验收

- 有效来源列表出现 `The Intrinsic Value Podcast`，优先级为 5。
- `intrinsic-value-auto` 能通过现有 `load_writer_profile` 加载。
- 一次性回填选中该频道最新 10 条。
- 成功文章均返回微信公众号草稿 `media_id`。
- cron 内容与时间保持不变。
