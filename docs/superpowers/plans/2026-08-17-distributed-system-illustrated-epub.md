# 《图解分布式系统原理》EPUB Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a validated EPUB 3 containing the complete Chinese text and embedded diagrams from chapters 1–7 of codedump.info's distributed-systems series.

**Architecture:** A standalone Python standard-library builder downloads the seven fixed chapter URLs, extracts each `single-content` subtree into clean XHTML, downloads and rewrites referenced media, and packages metadata, cover, navigation, chapters, CSS, and images into EPUB 3. Unit tests cover extraction, void-element serialization, media rewriting, nested navigation, and package invariants; an integration verifier compares every source chapter's H2 headings and final paragraph with the generated EPUB.

**Tech Stack:** Python 3 standard library (`html.parser`, `urllib`, `xml.etree`, `zipfile`, `unittest`), `xmllint`, EPUB 3/XHTML.

---

### Task 1: Extract clean, complete chapter XHTML

**Files:**
- Create: `build_codedump_epub.py`
- Create: `test_build_codedump_epub.py`

- [ ] **Step 1: Write the failing extraction test**

```python
def test_extracts_only_single_content_and_keeps_tail():
    page = '<nav>站点</nav><div class="single-content"><p>开头</p><img src="/a.png"><h2 id="end">本章小结</h2><p>结尾</p><script>bad()</script></div><aside>广告</aside>'
    chapter = extract_chapter(page, "https://www.codedump.info/dist-system-cn/introduction/")
    root = ET.fromstring(chapter.xhtml)
    text = ''.join(root.itertext())
    assert "开头" in text and "本章小结" in text and "结尾" in text
    assert "站点" not in text and "广告" not in text and "bad()" not in text
```

- [ ] **Step 2: Run the test and verify RED**

Run: `python3 -m unittest -v test_build_codedump_epub.py`
Expected: FAIL because `extract_chapter` does not exist.

- [ ] **Step 3: Implement the chapter model and subtree extractor**

```python
@dataclass
class Chapter:
    slug: str
    title: str
    xhtml: str
    headings: list[tuple[str, str]]
    image_urls: list[str]

CHAPTERS = [
    ("introduction", "第一章：分布式系统概述"),
    ("systemmodel", "第二章：分布式系统模型"),
    ("time", "第三章：分布式系统中的时间和顺序"),
    ("replication", "第四章：复制"),
    ("consensus", "第五章：共识算法"),
    ("partitioning", "第六章：分区"),
    ("transaction", "第七章：事务"),
]
```

Implement an `HTMLParser` that starts at `<div class="single-content">`, tracks nesting, omits `script/style`, serializes `img/br/hr` as XML self-closing elements, strips site-only classes, preserves `id`, and collects H2 labels and image URLs. Resolve URLs with `urllib.parse.urljoin`.

- [ ] **Step 4: Run the extraction test and verify GREEN**

Run: `python3 -m unittest -v test_build_codedump_epub.py`
Expected: PASS.

- [ ] **Step 5: Commit the extractor**

```bash
git add build_codedump_epub.py test_build_codedump_epub.py
git commit -m "feat: extract codedump distributed systems chapters"
```

### Task 2: Download and embed every diagram

**Files:**
- Modify: `build_codedump_epub.py`
- Modify: `test_build_codedump_epub.py`

- [ ] **Step 1: Write the failing media rewrite test**

```python
def test_rewrites_images_to_unique_local_paths():
    body = '<p><img src="https://www.codedump.info/media/a/diagram.png" alt="图"></p>'
    rewritten, assets = localize_media(body, fetch_bytes=lambda url: (b"PNG", "image/png"))
    assert 'src="images/' in rewritten
    assert "https://" not in rewritten
    assert len(assets) == 1
    assert next(iter(assets.values())).data == b"PNG"
```

- [ ] **Step 2: Run the media test and verify RED**

Run: `python3 -m unittest -v test_build_codedump_epub.py`
Expected: FAIL because `localize_media` does not exist.

- [ ] **Step 3: Implement media localization**

Create filenames from `sha256(url)[:12] + normalized_suffix`, fetch with a user agent and timeout, determine MIME type from the response plus `mimetypes`, reject empty responses, deduplicate repeated URLs, and rewrite all `src` values to `images/<name>`. Any failed download raises `BuildError` containing the original URL.

- [ ] **Step 4: Run all unit tests and verify GREEN**

Run: `python3 -m unittest -v test_build_codedump_epub.py`
Expected: all tests PASS.

- [ ] **Step 5: Commit media embedding**

```bash
git add build_codedump_epub.py test_build_codedump_epub.py
git commit -m "feat: embed codedump diagrams in EPUB"
```

### Task 3: Package EPUB 3 with cover, source page, and two-level TOC

**Files:**
- Modify: `build_codedump_epub.py`
- Modify: `test_build_codedump_epub.py`

- [ ] **Step 1: Write failing package tests**

```python
def test_nav_has_chapters_and_h2_children():
    nav = build_nav([Chapter("one", "第一章", "", [("a", "第一节")], [])])
    root = ET.fromstring(nav)
    links = ["".join(a.itertext()).strip() for a in root.iter("{http://www.w3.org/1999/xhtml}a")]
    assert links == ["第一章", "第一节"]

def test_mimetype_is_first_and_uncompressed(tmp_path):
    build_epub(sample_book(), tmp_path / "book.epub")
    with zipfile.ZipFile(tmp_path / "book.epub") as zf:
        first = zf.infolist()[0]
        assert first.filename == "mimetype"
        assert first.compress_type == zipfile.ZIP_STORED
```

- [ ] **Step 2: Run package tests and verify RED**

Run: `python3 -m unittest -v test_build_codedump_epub.py`
Expected: FAIL because `build_nav` and `build_epub` do not exist.

- [ ] **Step 3: Implement EPUB packaging**

Generate `mimetype`, `META-INF/container.xml`, `EPUB/package.opf`, `EPUB/nav.xhtml`, `EPUB/cover.xhtml`, `EPUB/source.xhtml`, `EPUB/style.css`, seven chapter XHTML files, and all localized images. Use title `图解分布式系统原理`, creator `Lichuang`, language `zh-CN`, EPUB 3 modified timestamp, CC BY-SA 4.0 attribution, and manifest entries with exact media types. The nav contains each chapter link with a nested ordered list of that chapter's H2 links.

- [ ] **Step 4: Run all tests and verify GREEN**

Run: `python3 -m unittest -v test_build_codedump_epub.py`
Expected: all tests PASS.

- [ ] **Step 5: Commit the packager**

```bash
git add build_codedump_epub.py test_build_codedump_epub.py
git commit -m "feat: package illustrated distributed systems EPUB"
```

### Task 4: Build and verify the final ebook

**Files:**
- Create: `图解分布式系统原理_第一章至第七章.epub`

- [ ] **Step 1: Run the builder against the live source**

Run: `python3 build_codedump_epub.py`
Expected: exit 0 and summary reporting 7 chapters plus a nonzero embedded-image count.

- [ ] **Step 2: Verify ZIP and XML integrity**

```bash
unzip -t 图解分布式系统原理_第一章至第七章.epub
verify_dir=$(mktemp -d /tmp/codedump-epub-verify.XXXXXX)
unzip -q 图解分布式系统原理_第一章至第七章.epub -d "$verify_dir"
find "$verify_dir/EPUB" -name '*.xhtml' -print0 | xargs -0 -n1 xmllint --noout
xmllint --noout "$verify_dir/EPUB/package.opf" "$verify_dir/META-INF/container.xml"
rm -R "$verify_dir"
```

Expected: no archive or XML errors.

- [ ] **Step 3: Run the source-to-book completeness verifier**

Run: `python3 build_codedump_epub.py --verify 图解分布式系统原理_第一章至第七章.epub`
Expected: `7/7 chapters complete`; every source H2 exists in the matching chapter, every final source paragraph matches, every image reference resolves to an archive member, and no chapter image uses `http://` or `https://`.

- [ ] **Step 4: Run the full test suite once more**

Run: `python3 -m unittest -v test_build_codedump_epub.py`
Expected: all tests PASS.

- [ ] **Step 5: Commit the final artifact**

```bash
git add 图解分布式系统原理_第一章至第七章.epub
git commit -m "build: add illustrated distributed systems EPUB"
```
