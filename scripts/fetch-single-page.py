#!/usr/bin/env python3
"""fetch-single-page.py — 一次性单页 fetch，绕过 children 列表 indexing 时差

用法：
  python3 fetch-single-page.py <page_id> <output_md_path>

行为：
  - GET /pages/<id> 拿 title
  - GET /blocks/<id>/children?page_size=100 全量拉 blocks
  - 转 markdown（复用 fetch_notion.py 的 blocks_to_md 逻辑）
  - 写 front-matter + body 到 output
  - 与 fetch_notion.py 输出格式完全一致（commit 起来一致）
"""
import os, sys, json, urllib.request, urllib.parse
from pathlib import Path
from datetime import datetime

NOTION_API = "https://api.notion.com/v1"
NOTION_TOKEN = os.environ["NOTION_API_TOKEN"]
NOTION_VERSION = "2026-03-11"


def api(path):
    req = urllib.request.Request(
        f"{NOTION_API}/{path}",
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": NOTION_VERSION,
        },
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def fetch_all_children(block_id):
    blocks = []
    cursor = None
    while True:
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        data = api(f"blocks/{block_id}/children?{urllib.parse.urlencode(params)}")
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


def main():
    if len(sys.argv) != 3:
        print("usage: fetch-single-page.py <page_id> <output_md_path>", file=sys.stderr)
        sys.exit(2)

    page_id = sys.argv[1]
    output_path = Path(sys.argv[2])

    page = api(f"pages/{page_id}")
    rt = page.get("properties", {}).get("title", {}).get("title", [])
    title = "".join(t.get("plain_text", "") for t in rt) if rt else ""
    if not title:
        print(f"ERROR: page {page_id} has no title", file=sys.stderr)
        sys.exit(3)

    blocks = fetch_all_children(page_id)
    body = blocks_to_md(blocks)

    safe_title = json.dumps(title, ensure_ascii=False)
    front_matter = f"---\ntitle: {safe_title}\ndate: {datetime.now().strftime('%Y-%m-%dT%H:%M:%S+08:00')}\ndraft: false\n---\n\n"
    output_path.write_text(front_matter + body, encoding="utf-8")
    print(f"OK {output_path} ({len(front_matter + body)} bytes)  title={title!r}")


if __name__ == "__main__":
    main()