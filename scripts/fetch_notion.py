#!/usr/bin/env python3
"""fetch_notion.py - 从 Notion 抓取简报，导出为 Hugo markdown"""
import os, sys, json, urllib.request, urllib.parse
from pathlib import Path
from datetime import datetime

NOTION_API = "https://api.notion.com/v1"
NOTION_TOKEN = os.environ.get("NOTION_API_TOKEN")
NOTION_VERSION = "2026-03-11"
YEAR_2026_PAGE_ID = "395b7087-4975-814a-87c4-ed304502a93e"
OUTPUT_DIR = Path("content/post")


def api(path, **params):
    url = f"{NOTION_API}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    print(f"[diag] GET {url}")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
    })
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"[diag] HTTPError on {path}: {e.code} {e.reason}", file=sys.stderr)
        raise


def fetch_all_children(block_id):
    blocks = []
    cursor = None
    while True:
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        data = api(f"blocks/{block_id}/children", **params)
        blocks.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    for b in blocks:
        if b.get("has_children"):
            b["_children"] = fetch_all_children(b["id"])
    return blocks


def rich_text_to_md(rt):
    parts = []
    for t in rt:
        text = t.get("plain_text", "")
        ann = t.get("annotations", {})
        if ann.get("code"):
            text = f"`{text}`"
        if ann.get("bold"):
            text = f"**{text}**"
        if ann.get("italic"):
            text = f"*{text}*"
        if ann.get("strikethrough"):
            text = f"~~{text}~~"
        href = t.get("href")
        if href:
            text = f"[{text}]({href})"
        parts.append(text)
    return "".join(parts)


def table_to_md(rows):
    if not rows:
        return ""
    md_rows = []
    for i, row in enumerate(rows):
        cells = row.get("table_row", {}).get("cells", [])
        cell_texts = [rich_text_to_md(c) for c in cells]
        md_rows.append("| " + " | ".join(cell_texts) + " |")
        if i == 0:
            md_rows.append("| " + " | ".join(["---"] * len(cells)) + " |")
    return "\n".join(md_rows) + "\n\n"


def blocks_to_md(blocks, indent=0):
    out = []
    for b in blocks:
        t = b["type"]
        if t == "paragraph":
            txt = rich_text_to_md(b["paragraph"]["rich_text"])
            if txt:
                out.append("  " * indent + txt + "\n\n")
        elif t == "heading_1":
            txt = rich_text_to_md(b["heading_1"]["rich_text"])
            out.append("  " * indent + f"# {txt}\n\n")
        elif t == "heading_2":
            txt = rich_text_to_md(b["heading_2"]["rich_text"])
            out.append("  " * indent + f"## {txt}\n\n")
        elif t == "heading_3":
            txt = rich_text_to_md(b["heading_3"]["rich_text"])
            out.append("  " * indent + f"### {txt}\n\n")
        elif t == "bulleted_list_item":
            txt = rich_text_to_md(b["bulleted_list_item"]["rich_text"])
            children = b.get("_children", [])
            out.append("  " * indent + f"- {txt}\n")
            if children:
                out.append(blocks_to_md(children, indent + 1))
        elif t == "numbered_list_item":
            txt = rich_text_to_md(b["numbered_list_item"]["rich_text"])
            out.append("  " * indent + f"1. {txt}\n")
            if b.get("_children"):
                out.append(blocks_to_md(b["_children"], indent + 1))
        elif t == "callout":
            txt = rich_text_to_md(b["callout"]["rich_text"])
            emoji = b["callout"].get("icon", {}).get("emoji", "💡")
            out.append(f"> {emoji} {txt}\n\n")
        elif t == "quote":
            txt = rich_text_to_md(b["quote"]["rich_text"])
            out.append(f"> {txt}\n\n")
        elif t == "divider":
            out.append("\n---\n\n")
        elif t == "code":
            lang = b["code"].get("language", "")
            txt = rich_text_to_md(b["code"]["rich_text"])
            out.append(f"```{lang}\n{txt}\n```\n\n")
        elif t == "image":
            img = b["image"]
            url = img.get("external", {}).get("url") or img.get("file", {}).get("url", "")
            caption = ""
            if img.get("caption"):
                caption = rich_text_to_md(img["caption"])
            out.append(f"![{caption}]({url})\n\n")
        elif t == "table":
            rows = b.get("_children", [])
            if rows:
                out.append(table_to_md(rows))
        elif t == "toggle":
            txt = rich_text_to_md(b["toggle"]["rich_text"])
            out.append(f"<details><summary>{txt}</summary>\n\n")
            if b.get("_children"):
                out.append(blocks_to_md(b["_children"], indent + 1))
            out.append("</details>\n\n")
    return "".join(out)


def page_to_md(page_id, title):
    blocks = fetch_all_children(page_id)
    body = blocks_to_md(blocks)
    safe_title = json.dumps(title, ensure_ascii=False)
    front_matter = f"---\ntitle: {safe_title}\ndate: {datetime.now().strftime('%Y-%m-%dT%H:%M:%S+08:00')}\ndraft: false\n---\n\n"
    return front_matter + body


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # 诊断输出
    token = os.environ.get("NOTION_API_TOKEN")
    print(f"[diag] NOTION_API_TOKEN present: {bool(token)}")
    if token:
        print(f"[diag] token prefix: {token[:8]}... suffix: ...{token[-4:]}")
    else:
        print("[diag] FATAL: NOTION_API_TOKEN is empty or not set", file=sys.stderr)
        sys.exit(2)
    print(f"[diag] YEAR_2026_PAGE_ID: {YEAR_2026_PAGE_ID}")
    try:
        # 动态发现：找 📅 2026 下面所有 🗓 Wnn 子页
        weeks_data = api(f"blocks/{YEAR_2026_PAGE_ID}/children")
        weeks = [b for b in weeks_data.get("results", []) if b.get("type") == "child_page"]
        print(f"[diag] Found {len(weeks)} week pages under 📅 2026")
        total = 0
        for week in weeks:
            wpid = week["id"]
            wtitle = week["child_page"]["title"]
            print(f"[diag] -- Processing {wtitle} ({wpid})")
            briefings = api(f"blocks/{wpid}/children").get("results", [])
            print(f"[diag]    {wtitle} has {len(briefings)} children blocks")
            for b in briefings:
                if b.get("type") != "child_page":
                    continue
                pid = b["id"]
                title = b["child_page"]["title"]
                slug = title.replace("/", "-").replace(" ", "_")
                content = page_to_md(pid, title)
                out_file = OUTPUT_DIR / f"{slug}.md"
                out_file.write_text(content)
                print(f"OK {out_file} ({len(content)} bytes)")
                total += 1
        print(f"[diag] Done. {total} briefings fetched.")
    except urllib.error.HTTPError as e:
        print(f"[diag] HTTPError: {e.code} {e.reason}", file=sys.stderr)
        body = e.read().decode("utf-8", errors="replace")
        print(f"[diag] response body: {body[:1000]}", file=sys.stderr)
        sys.exit(3)
    except Exception as e:
        import traceback
        print(f"[diag] Exception: {type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(4)


if __name__ == "__main__":
    main()