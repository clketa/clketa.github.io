#!/usr/bin/env python3
"""check-content-overlap.py — compare today's LLM body vs last N days' briefings

Usage:
  echo "$LLM_BODY" | check-content-overlap.py <today_yyyymmdd> <content_dir> [max_days=3]

Output (stdout): "<similarity>|<matched_file>"
Output (stderr): log lines

Exit codes:
  0  = overlap < 0.5 (pass)
  13 = overlap >= 0.5 (reject — LLM likely recycled prior briefing)
  2  = invalid input
"""
import sys, os, re
from pathlib import Path


def tokenize(text: str) -> set:
    """strip front-matter + metadata + H1 + template skeleton; tokenize Chinese chars + English words + numbers.

    2026-09-23 fix：token-level jaccard 把模板化 token 算进 overlap（'国内主线' / '国际主线' /
    '市场与隐忧' / '明日值得关注' / '## 🇨🇳 国内要闻' / '### 1.' / '**发生了什么**' / '**苒苒按**' / '📎 原文链接'
    这些骨架词所有 briefing 都有），造成 jaccard 必然 > 0.5 误报。
    现额外 strip markdown 标题 + bold + bullet/number markers，让 token set 只含**实际新闻内容**
    """
    # strip YAML front-matter
    text = re.sub(r"^---[\s\S]*?---\s*", "", text)
    # strip first 3 leading quote blocks (编制/立场/信息源)
    text = re.sub(r"(?:^> [^\n]*\n){1,5}", "", text, count=1)
    # strip H1 title
    text = re.sub(r"^# [^\n]*\n", "", text)
    # strip ALL heading lines (## / ###) — section/subsection 标题骨架，所有 briefing 一样
    text = re.sub(r"^#{1,6}\s+[^\n]*\n", "", text, flags=re.MULTILINE)
    # strip all bold inline markers **...** — **发生了什么** / **苒苒按** / **国内主线：**
    text = re.sub(r"\*\*[^*]+\*\*", "", text)
    # strip bullet/number markers at line start (- / • / 1. / 2. ...)
    text = re.sub(r"^[ \t]*[-\u2022]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[ \t]*\d+\.\s+", "", text, flags=re.MULTILINE)
    # strip markdown image syntax ![alt](url)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    # strip markdown link markup, keep URL text only
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    # strip template-only section 标题骨架 (这些是固定短语）
    # 	苒苒一句话 / 发生了什么 / 苒苒按 / 原文链接 / 国内主线 / 国际主线 / 市场与隐忧
    # 	明日值得关注 / 覆盖范围
    template_skeleton = (
        "苒苒一句话",
        "发生了什么",
        "苒苒按",
        "原文链接",
        "国内主线",
        "国际主线",
        "市场与隐忧",
        "明日值得关注",
        "覆盖范围",
        "已避重",
        "已后续标注",
        "非周一不含周历",
    )
    for token in template_skeleton:
        text = text.replace(token, " ")
    # tokenize: Chinese chars each as token, English words + numbers grouped
    tokens = set(re.findall(r"[\u4e00-\u9fff]|[A-Za-z]+|[0-9]+", text))
    return tokens


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: check-content-overlap.py <today_yyyymmdd> <content_dir> [max_days=3]", file=sys.stderr)
        return 2

    today_ymmdd = sys.argv[1]
    content_dir = sys.argv[2]
    max_days = int(sys.argv[3]) if len(sys.argv) > 3 else 3

    today_body = sys.stdin.read()
    today_tokens = tokenize(today_body)

    if not today_tokens:
        print(f"WARN: today body has 0 tokens after tokenize — pass", file=sys.stderr)
        print(f"0.000|none")
        return 0

    content_dir_p = Path(content_dir)
    if not content_dir_p.is_dir():
        print(f"ERROR: content_dir not found: {content_dir}", file=sys.stderr)
        return 2

    # find last N days' briefing markdown files (sorted by date desc, exclude today)
    files = sorted(content_dir_p.glob("*.md"), reverse=True)
    files = [f for f in files if not f.name.startswith(today_ymmdd)]
    files = files[:max_days]

    if not files:
        print(f"INFO: no prior briefings found to compare against", file=sys.stderr)
        print(f"0.000|none")
        return 0

    max_sim = 0.0
    max_file = ""
    for f in files:
        try:
            other_body = f.read_text(encoding="utf-8")
        except Exception as e:
            print(f"WARN: cannot read {f}: {e}", file=sys.stderr)
            continue
        other_tokens = tokenize(other_body)
        if not other_tokens:
            continue
        union = today_tokens | other_tokens
        if not union:
            continue
        sim = len(today_tokens & other_tokens) / len(union)
        if sim > max_sim:
            max_sim = sim
            max_file = f.name

    print(f"INFO: max overlap = {max_sim:.3f} vs {max_file}", file=sys.stderr)
    print(f"{max_sim:.3f}|{max_file}")

    if max_sim >= 0.5:
        return 13
    return 0


if __name__ == "__main__":
    sys.exit(main())