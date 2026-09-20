#!/usr/bin/env python3
"""check-existing-page-date.py — 校验 Notion child_page 标题日期与 body 编制日期一致

用于 Step 3 dedup 增强：之前 9-18 cron 写过一条 title=20260920 body=2026-09-18 的孤儿 page，
导致 9-20 cron 误判 dedup 跳过。

修法：
  - stdin: Notion API /blocks/<Wnn>/children 的 JSON 响应
  - argv[1]: expected_yyyy_mm_dd（如 2026-09-20）
  - 对每条 child_page，如果 title 提取出的日期 == expected：
    - fetch page 前 5 个 blocks
    - 找第一个 quote block，从 rich_text 提取 \\d{4}-\\d{2}-\\d{2}
    - 等于 expected → 打印到 stdout（计入 EXISTING_COUNT）
    - 不等 / 缺 quote / fetch 失败 → 打印 WARN 到 stderr（orphan，不计入）

Stdout 格式（与原 Step 3 输出兼容）：`- TITLE  (id=PAGE_ID)`
Stderr 格式：WARN: TITLE (id=PAGE_ID) ...
Exit: 0
"""
import json, sys, os, re, urllib.request, urllib.error

EXPECTED_DATE = sys.argv[1]
TOKEN = os.environ["NOTION_API_TOKEN"]
NOTION_VERSION = "2026-03-11"


def fetch_first_quote_date(page_id):
    """Fetch first 5 blocks of page; return (date_str_or_None, error_str_or_None)."""
    try:
        req = urllib.request.Request(
            f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=5",
            headers={"Authorization": f"Bearer {TOKEN}", "Notion-Version": NOTION_VERSION},
        )
        d = json.loads(urllib.request.urlopen(req, timeout=10).read())
    except urllib.error.HTTPError as e:
        return None, f"http_error: {e.code}"
    except Exception as e:
        return None, f"fetch_failed: {type(e).__name__}: {e}"

    for b in d.get("results", []):
        if b.get("type") == "quote":
            rt = b["quote"]["rich_text"]
            txt = "".join(t.get("plain_text", "") for t in rt) if rt else ""
            m = re.search(r"\d{4}-\d{2}-\d{2}", txt)
            if m:
                return m.group(0), None
    return None, "no_quote_with_date"


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        print(f"WARN: empty stdin (no children JSON)", file=sys.stderr)
        return
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"WARN: stdin is not valid JSON: {e}", file=sys.stderr)
        return

    for b in data.get("results", []):
        if b.get("type") != "child_page":
            continue
        title = b.get("child_page", {}).get("title", "")
        bid = b.get("id", "")
        if not title or not bid:
            continue
        # title 形如 YYYYMMDD-...，提取日期前缀
        m = re.match(r"(\d{4})(\d{2})(\d{2})-", title)
        if not m:
            continue
        title_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        if title_date != EXPECTED_DATE:
            continue  # 不是今日候选

        # fetch body 校验
        body_date, err = fetch_first_quote_date(bid)
        if err:
            print(f"WARN: {title} (id={bid}) body fetch {err} — exclude from dedup", file=sys.stderr)
            continue
        if body_date == EXPECTED_DATE:
            print(f"- {title}  (id={bid})")
        else:
            print(
                f"WARN: {title} (id={bid}) ORPHAN — title_date={title_date} body_date={body_date} != expected={EXPECTED_DATE}; excluding from dedup",
                file=sys.stderr,
            )


if __name__ == "__main__":
    main()