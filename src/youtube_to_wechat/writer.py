"""Writer profile interface and built-in implementations.

Transforms a cleaned transcript artifact + source metadata into a structured
WeChat article draft.  A writer profile names the intended audience, style,
article structure, and compliance constraints.  The current built-in Gemini
writer is the MVP ``alchemy-research`` profile.

Usage::

    from youtube_to_wechat.writer import GeminiWriter

    writer = GeminiWriter()   # reads GEMINI_API_KEY from env
    article = writer.write(transcript="...", meta={"title": "..."})
    print(article.markdown)
"""
import abc
import os
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ArticleResult:
    """Structured output from an article writing pass.

    Attributes:
        markdown:     Full Markdown text of the generated article.
        title:        Suggested article title (may differ from video title).
        digest:       One-sentence WeChat digest (≤ 120 chars).
        key_points:   Short bullet summary (for quick scan).
        review_notes: Facts or claims requiring human verification.
    """

    markdown: str = ""
    title: str = ""
    digest: str = ""
    tags: List[str] = field(default_factory=list)
    key_points: List[str] = field(default_factory=list)
    review_notes: List[str] = field(default_factory=list)
    chart_data: str = ""


class WriterError(RuntimeError):
    """Raised when article generation fails."""


class BaseWriter(abc.ABC):
    """Plugin interface for generating a WeChat article from a transcript."""

    @abc.abstractmethod
    def write(self, transcript: str, meta: dict) -> ArticleResult:
        """Generate a WeChat article draft.

        Args:
            transcript: Cleaned plain-text transcript.
            meta:       Video metadata dict (at minimum ``{"title": "..."}``)

        Returns:
            :class:`ArticleResult` with the Markdown article and metadata.

        Raises:
            WriterError: On API or generation failures.
        """


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_WRITER_SYSTEM_PROMPT = """\
你是一位极具洞察力的“炼金术士”（科技与硬核投资内容创作者），正在为微信公众号「marv 的炼金术」下的独立栏目「炼金投研」撰写投资研究简报。

你的任务：根据提供的英文 YouTube 视频标题和文字转录，撰写一篇结构清晰、数据详实、带有独特观察感和锋利视角的中文推文草稿。

## 极其重要的合规与行文纪律（必读）
1. **中立陈述与归因**：绝对不能带有主观的买入/卖出建议。所有观点、结论必须前缀“博主认为……”、“视频提到……”、“AI提取显示……”。不要使用“我们应该”等具有诱导性的词语。
2. **标题去标签化**：标题应当是中性的逻辑推演，比如“博主观点深度拆解”或“逻辑推演”，不要出现“买入时机已到？”这种强烈倾向的表述。
3. **事实校对层（展现炼金术士的专业）**：如果 AI 自动提取的数据与当前客观现实（如当前年份为2024年）严重冲突（例如在2024年看到“2026年Q1财报营收”），必须在列出该数据时**直接给出纠偏**，格式为：`[注：推测此处为博主口误，实际应为 2024 Q1]`。
4. **标的命名规范**：在提到标的公司时，请务必使用“英文Ticker + 中文常用名”的格式，例如：**MELI 美客多**、**NVDA 英伟达**。标题建议中也应遵循此规范。
5. **区分来源（炼金旁注）**：凡是 transcript 中没有直接提到的分析、竞争对手、宏观背景等，**必须**使用专有标记补充：
   > 💡 **炼金旁注**：[补充的内容...]

## 文章结构要求（必须按此顺序输出 Markdown）

### 引言与核心金句
- 用 1-2 句话点明视频的核心冲突或主题。
- **提炼金句**：提取博主最震撼的一句原话（例如关于“渎职”的评论），加粗并以引用格式放在最前面，抓住读者眼球。

### 一、公司/主题简介
- 简要介绍视频涉及的公司或主题。
- 若需为小白读者补充背景，请使用 `> 💡 **炼金旁注**：` 补充。

### 二、核心数据与财务表现
- 提取 transcript 中的所有具体数字。
- 每个数据点用一个 bullet 列出，格式：**指标名称**：具体数值（同比变化）。
- **重点高亮**：将其中的关键百分比用加粗突出显示（例如：营收增长 **49%**，销售增长 **47%**），方便手机端快速滑动阅读。
- 遇到时间错乱的数据，直接按上述“事实校对”规则加上 `[注：...]`。
- 本节严禁使用旁注补充任何未提及的财务数据。

### 三、竞争格局
- 提炼 transcript 中对竞争对手、市场地位的相关描述。
- 若 transcript 未涉及或内容单薄，请使用 `> 💡 **炼金旁注**：` 简明扼要地说明。

### 四、管理层团队与风格
- 提炼 transcript 中对管理层决策风格、战略取向的相关描述。
- 举出 1-2 个具体的决策案例佐证。
- 使用 `> 💡 **炼金旁注**：` 详细介绍核心管理层（如创始人履历）。

### 五、核心观点与逻辑拆解
- 提炼博主/分析师的核心判断（3-6 条），每条必须是完整的论证链。
- 强调归因：“博主认为...”。带出“炼金术士”的犀利观察感。

### 六、长短期催化因素
- 分为短期催化（0-12个月）和长期催化（1-5年）。优先从 transcript 提取，不足时使用 `> 💡 **炼金旁注**：` 补充。

### 七、近期股价走势
- 提取具体股价数据（如跌幅、YTD）。
- 客观描述博主的买卖行为，不带任何推荐意味。本节严禁使用 AI 补充最新股价。

### 八、风险与核查点
- 列出博主或 transcript 中提到的风险因素。
- 标注需要人工进一步核查的异常数据点。

### 九、来源说明
- 注明视频来源、博主名称、原始链接。
- 在本节末尾再次原样输出一遍加粗的免责声明：
**【免责声明】本内容仅为 AI 对海外公开资讯的辅助整理，不代表本公众号立场，不构成任何投资建议。**

## 格式约定
- 语气要带有“极客气质”和“硬核投研感”。
- 使用 Markdown 标题（##、###）、加粗（**）、列表（-、*）。
- 摘要单独在文章最后一行以 `> 摘要：` 开头输出（不超过 120 字）。
- 另起一行，以 `> 标题建议：` 开头输出建议的公众号标题（注意去标签化、中立）。
- 另起一行，以 `> 标签：` 开头，输出 3-5 个适合归档的标签（带#号）。
- 另起一行，以 `> 图表数据：` 开头，输出单行 JSON 格式，包含核心财务数据对比，用于生成信息图。
- 另起一行，以 `> 核查点：` 开头，用 | 分隔。
- 不要输出 markdown 代码块包裹，直接输出正文。
- 不要在正文开头输出免责声明；免责声明只允许出现在「来源说明」末尾。
"""

_GENERAL_SYSTEM_PROMPT = """\
你是一个专业、客观的内容分析助手。
你的任务：根据提供的视频标题和文字转录，提取核心干货，输出一篇结构清晰、语言精炼的总结笔记。
必须保持客观中立，所有观点必须归因于视频本身，不能出现“公众号”、“本期栏目”、“小编”等字眼。

## 格式约定 (必须遵守)
- 语气客观精炼，剔除所有客套话。
- 摘要单独在文章最后一行以 `> 摘要：` 开头输出（不超过 120 字）。
- 另起一行，以 `> 标题建议：` 开头输出建议的标题。
- 另起一行，以 `> 标签：` 开头，输出 3-5 个适合归档的标签（带#号）。
- 另起一行，以 `> 图表数据：` 开头，输出单行 JSON 格式。
- 另起一行，以 `> 核查点：` 开头，用 | 分隔。
- 不要输出 markdown 代码块包裹，直接输出正文。
"""

_MAX_TRANSCRIPT_CHARS = 12_000


class GeminiWriter(BaseWriter):
    """Article writer powered by Google Gemini and the alchemy-research profile.

    Args:
        model:   Gemini model name.  Defaults to ``"gemini-2.5-flash"``.
        api_key: Gemini API key.  Falls back to ``GEMINI_API_KEY`` env var.
    """

    def __init__(
        self,
        model: str = "gemini-2.5-flash",
        api_key: Optional[str] = None,
        profile_name: str = "alchemy-research",
        profile_prompt: str = "",
    ) -> None:
        self.model_name = model
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.profile_name = profile_name
        self.profile_prompt = profile_prompt

    def _get_client(self):
        try:
            from google import genai  # noqa: PLC0415
        except ImportError as exc:
            raise WriterError(
                "google-genai is not installed. "
                "Install it with: pip install google-genai"
            ) from exc
        if not self.api_key:
            raise WriterError(
                "GEMINI_API_KEY is not set. "
                "Export it as an environment variable or pass api_key= to GeminiWriter."
            )
        return genai.Client(api_key=self.api_key)

    def build_prompt(self, transcript: str, meta: dict) -> str:
        title = meta.get("title", "")
        channel = meta.get("uploader") or meta.get("channel") or ""
        url = meta.get("webpage_url") or meta.get("url") or ""
        issue = meta.get("issue")
        truncated = transcript[:_MAX_TRANSCRIPT_CHARS]
        
        # Decide base persona based on profile
        if self.profile_name in ["alchemy-research", "market-commentary", "deep-stock-analysis"]:
            base_prompt = _WRITER_SYSTEM_PROMPT
        else:
            base_prompt = _GENERAL_SYSTEM_PROMPT
            
        profile_section = ""
        if self.profile_prompt:
            profile_section = (
                "\n\n## 当前执行要求\n"
                f"writer_profile：{self.profile_name}\n"
                f"{self.profile_prompt}"
            )

        issue_str = f"【注意】本期文章编号为 {issue}。请在输出“标题建议”和文章正文首行 Markdown 标题时，务必将编号加在最前面（例如「{issue} | 原标题」）。\n" if issue else ""

        return (
            f"{base_prompt}{profile_section}\n\n"
            f"视频标题：{title}\n"
            f"频道/博主：{channel}\n"
            f"视频链接：{url}\n"
            f"{issue_str}\n"
            f"Transcript：\n\n{truncated}"
        )

    def write(self, transcript: str, meta: dict) -> ArticleResult:
        """Generate a WeChat article draft from the transcript.

        Args:
            transcript: Cleaned transcript text.
            meta:       Video metadata dict.

        Returns:
            :class:`ArticleResult` with populated fields.

        Raises:
            WriterError: On API or parsing failures.
        """
        if not transcript.strip():
            return ArticleResult(
                markdown="*transcript 为空，无法生成文章。*",
                review_notes=["transcript 为空"],
            )

        title = meta.get("title", "")
        url = meta.get("webpage_url") or meta.get("url") or ""
        prompt = self.build_prompt(transcript, meta)

        try:
            client = self._get_client()
            response = client.models.generate_content(
                model=self.model_name,
                contents=prompt,
            )
            raw = response.text.strip()
        except WriterError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise WriterError(f"Gemini API call failed: {exc}") from exc

        return _parse_article_response(raw, title, url)


def _parse_article_response(raw: str, fallback_title: str, url: str) -> ArticleResult:
    """Extract structured fields from the raw Gemini article response."""
    lines = raw.splitlines()

    digest = ""
    suggested_title = fallback_title
    tags: List[str] = []
    review_notes: List[str] = []
    chart_data = ""
    body_lines: List[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("> 摘要："):
            digest = stripped[len("> 摘要："):].strip()
        elif stripped.startswith("> 标题建议："):
            suggested_title = stripped[len("> 标题建议："):].strip()
        elif stripped.startswith("> 标签："):
            tags_raw = stripped[len("> 标签："):].strip()
            # Split by space, comma or standard markdown formatting, keep the #
            tags = [t.strip() for t in tags_raw.replace(",", " ").replace("，", " ").split() if t.strip()]
        elif stripped.startswith("> 图表数据："):
            chart_data = stripped[len("> 图表数据："):].strip()
        elif stripped.startswith("> 核查点："):
            notes_raw = stripped[len("> 核查点："):].strip()
            review_notes = [n.strip() for n in notes_raw.split("|") if n.strip()]
        else:
            body_lines.append(line)

    markdown = _remove_opening_disclaimer("\n".join(body_lines).strip())

    # Prepend the suggested title as H1 if not already present
    if suggested_title and not markdown.startswith(f"# {suggested_title}"):
        # Insert after the disclaimer block (first 1-2 lines now)
        m_lines = markdown.splitlines()
        insert_idx = 0
        m_lines.insert(insert_idx, f"# {suggested_title}")
        markdown = "\n".join(m_lines)

    # Append tags to the bottom of the markdown for visual display
    if tags:
        markdown += f"\n\n---\n**标签**：{' '.join(tags)}\n"

    # Extract key_points from the markdown (lines starting with "- " under 核查 sections)
    key_points: List[str] = []
    in_key_section = False
    for line in body_lines:
        stripped = line.strip()
        if "核心观点" in stripped or "核心数据" in stripped:
            in_key_section = True
        elif stripped.startswith("## ") or stripped.startswith("### "):
            in_key_section = False
        if in_key_section and stripped.startswith("- "):
            key_points.append(stripped[2:].strip())

    return ArticleResult(
        markdown=markdown,
        title=suggested_title,
        digest=digest,
        tags=tags,
        key_points=key_points[:8],
        review_notes=review_notes,
        chart_data=chart_data,
    )


def _remove_opening_disclaimer(markdown: str) -> str:
    lines = markdown.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and "免责声明" in lines[0]:
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
    return "\n".join(lines).strip()
