#!/usr/bin/env python3
"""noon-deploy.py — 9-29 重构：noon cron 简化 argv，绕过 fetch_notion.py 复杂链路。

fetch_notion.py 有 8+ 个失败点（weeks iteration / W39 indexing / search fallback /
api retry / main loop crash / today summary / Notion 429 / UUID 截断 / input+output filter），
每天暴露一个 bug。9-24 / 9-25 / 9-26 / 9-27 / 9-28 连续 6 天每天一个 bug。

修法：noon cron 只做 3 件事：
1. 读 .briefing-tmp/briefing-report-YYYYMMDD.json 的 notion_url → 提取 page_id
2. 调 fetch-single-page.py <page_id> <output_md_path> 单拉（已含 Notion created_time +
   blocks_to_md H1 强制覆盖 + retry-on-429 防御）
3. 完成退出

完全绕过 fetch_notion.py 的 weeks iteration / search fallback / main loop crash 路径。
fetch_notion.py 保留给 reindex / 历史回填用，不在 noon cron 链路。
"""
import os, sys, json, re, subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta

BRIEFING_TMP = Path("/home/ubuntu/.openclaw/workspace/.briefing-tmp")
CONTENT_POST = Path("/home/ubuntu/projects/briefing-site/content/post")
SH = "+08:00"


def extract_page_id(notion_url: str) -> str:
    """https://www.notion.so/3eab7087497581d1851ddcd1e28d8692
       → 3eab7087-4975-81d1-851d-dcd1e28d8692 (36 字符完整 UUID)
    """
    raw = notion_url.rstrip("/").split("/")[-1]
    # hex 32 字符 → 加 - 分隔符
    if "-" not in raw and len(raw) == 32:
        return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
    if len(raw) == 36:
        return raw
    raise ValueError(f"无法从 notion_url 提取 page_id: {notion_url!r}")


def today_compact() -> str:
    """Asia/Shanghai 今天的 8 位日期"""
    return datetime.now(tz=timezone(timedelta(hours=8))).strftime("%Y%m%d")


def main():
    today = today_compact()
    report_path = BRIEFING_TMP / f"briefing-report-{today}.json"
    if not report_path.exists():
        print(f"ERR: briefing-report 不存在: {report_path}", file=sys.stderr)
        print(f"  说明 write-1130 cron 没跑通，先查上游", file=sys.stderr)
        return 2

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"ERR parse report: {e}", file=sys.stderr)
        return 3

    if not report.get("ok"):
        print(f"ERR report ok=false: {report}", file=sys.stderr)
        return 4

    notion_url = report.get("notion_url", "")
    title = report.get("title", "")
    if not notion_url or not title:
        print(f"ERR report missing notion_url or title", file=sys.stderr)
        return 5

    # 防 UUID 截断 bug：自动从 hex 加 - 分隔符
    try:
        page_id = extract_page_id(notion_url)
    except ValueError as e:
        print(f"ERR extract page_id: {e}", file=sys.stderr)
        return 6

    out_path = CONTENT_POST / f"{title}.md"
    if out_path.exists():
        print(f"[diag] today markdown already exists: {out_path.name}", file=sys.stderr)
        return 0

    print(f"[diag] page_id: {page_id}", file=sys.stderr)
    print(f"[diag] output: {out_path}", file=sys.stderr)

    # 调 fetch-single-page.py（已含 Notion created_time + blocks_to_md H1 强制覆盖）
    proc = subprocess.run(
        ["python3", "/home/ubuntu/projects/briefing-site/scripts/fetch-single-page.py",
         page_id, str(out_path)],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        print(f"ERR fetch-single-page exit={proc.returncode}", file=sys.stderr)
        print(f"  stderr: {proc.stderr[:500]}", file=sys.stderr)
        return proc.returncode

    print(f"OK {out_path} ({out_path.stat().st_size} bytes)  title={title!r}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())