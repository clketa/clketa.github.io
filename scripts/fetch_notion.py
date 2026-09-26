#!/usr/bin/env python3
"""fetch_notion.py - 从 Notion 抓取简报，导出为 Hugo markdown"""
import os, sys, json, time, urllib.request, urllib.parse
from pathlib import Path
from datetime import datetime

NOTION_API = "https://api.notion.com/v1"
NOTION_TOKEN = os.environ.get("NOTION_API_TOKEN")
NOTION_VERSION = "2026-03-11"
YEAR_2026_PAGE_ID = "3c2b7087-4975-81c7-9beb-eb79fbbb5069"
OUTPUT_DIR = Path("content/post")


def strip_trailing_json_block(content: str) -> str:
    """Strip a trailing ```json``` code block from markdown body before publishing.

    2026-09-24 修复：LLM 习惯在 markdown 末尾追加 JSON 备份 block（title +
    punchline + 主线 结构化数据），但 minimax safety filter 会把这段 JSON 判
    sensitive（错误码 1027）拒输出 → cron exit 5。

    兑底逻辑：即使 LLM 仍生成末尾 JSON，也从 publish 出去的文件里剥掉。
    前端 CSS（9-21 加的 json-viewer{display:none}）已经是视觉兑底，本函数
    从源头修。

    容忍几种变体：
    - 末尾 ```json 或 ```JSON（大小写）
    - 前面可能有一个 `---` 分隔线
    - 中间有任意换行/空白
    """
    import re
    pattern = re.compile(
        r"\n+\s*(?:---\s*\n)?\s*```(?:json|JSON)\b.*?```\s*\Z",
        re.DOTALL,
    )
    return pattern.sub("", content).rstrip() + "\n"


def api(path, **params):
    """调 Notion API，自动 retry 429 / 5xx / timeout（backoff 1s, 5s, 16s）

    2026-09-26 修复：原版只 print 后 raise，外层 main() 遇到 429 就 sys.exit(3)，
    search 兑底逻辑根本来不及跑。加 retry 让 429 / 5xx 能自动恢复。

    使用限量谪复：429/5xx/timeout 退避 1s/5s/16s，上限 3 次。其他 HTTP error 
    (404/401/403) 不重试，直接 raise。
    """
    url = f"{NOTION_API}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    print(f"[diag] GET {url}", file=sys.stderr)
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
    })
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            last_err = e
            body = e.read().decode("utf-8", errors="replace")[:300]
            # 429 / 5xx 才重试；4xx (除 429) 直接 raise
            if e.code == 429 or 500 <= e.code < 600:
                backoff = [1, 5, 16][attempt]
                print(f"[diag] retryable HTTPError {e.code} on {path} (attempt {attempt+1}/3, backoff {backoff}s): {body}", file=sys.stderr)
                time.sleep(backoff)
                continue
            else:
                print(f"[diag] non-retryable HTTPError {e.code} on {path}: {body}", file=sys.stderr)
                raise
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            backoff = [1, 5, 16][attempt]
            print(f"[diag] URLError/timeout on {path} (attempt {attempt+1}/3, backoff {backoff}s): {e}", file=sys.stderr)
            time.sleep(backoff)
            continue
    # 3 次都失败，raise 最后一次错误
    print(f"[diag] API retry exhausted after 3 attempts on {path}", file=sys.stderr)
    raise last_err


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


def run_search_fallback(weeks: list, today_compact: str) -> tuple:
    """2026-09-25 fix：W39 children API indexing 延迟兑底。

    主循环拉完后，如果 today 8位前缀不在已拉 titles 里，走 POST /v1/search 倒查。
    9-26 升级：抽成独立函数，主循环崩了也能调。
    Returns:
        (fetched_count, bytes_written) 供 main() 更新 total 计数。
    """
    if not weeks:
        return (0, 0)
    # 检查今天 markdown 是否已在 content/post/ 里（避免重复拉）
    existing = list(OUTPUT_DIR.glob(f"{today_compact}-*.md"))
    if existing:
        print(f"[diag] today markdown already exists, skip search: {[p.name for p in existing]}", file=sys.stderr)
        return (0, 0)
    print(f"[diag] WARN: 今天 {today_compact} 不在 W39 children 拉取结果中，启用 POST /v1/search 兑底", file=sys.stderr)
    try:
        search_req = urllib.request.Request(
            f"https://api.notion.com/v1/search",
            method="POST",
            headers={
                "Authorization": f"Bearer {NOTION_TOKEN}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
            data=json.dumps({"query": today_compact, "page_size": 20}).encode("utf-8"),
        )
        search_res = json.loads(urllib.request.urlopen(search_req, timeout=30).read())
    except Exception as e:
        print(f"[diag] search fallback ERR: {type(e).__name__}: {e}", file=sys.stderr)
        return (0, 0)

    week_ids = {w["id"] for w in weeks}
    fetched = 0
    bytes_written = 0
    for r in search_res.get("results", []):
        if r.get("object") != "page":
            continue
        rt = r.get("properties", {}).get("title", {}).get("title", [])
        ptitle = "".join(t.get("plain_text", "") for t in rt)
        parent = r.get("parent", {})
        if not ptitle.startswith(today_compact):
            continue
        parent_id = parent.get("page_id") or parent.get("id")
        if parent_id not in week_ids:
            continue
        slug = ptitle.replace("/", "-").replace(" ", "_")
        try:
            content = page_to_md(r["id"], ptitle)
            content = strip_trailing_json_block(content)
            out_file = OUTPUT_DIR / f"{slug}.md"
            if out_file.exists():
                continue
            out_file.write_text(content)
            print(f"OK [search fallback] {out_file} ({len(content)} bytes)", file=sys.stderr)
            fetched += 1
            bytes_written += len(content)
        except Exception as e:
            print(f"[diag] search fallback write ERR for {r['id']}: {type(e).__name__}: {e}", file=sys.stderr)
    return (fetched, bytes_written)


def run_search_fallback_after_main_loop_crash():
    """2026-09-26 fix：主循环崩了（429 / 5xx / network）也触发 search 兑底。

    之前 search fallback 写在主循环跑完后，主循环 crash 就不会跑。9-26 实测：
    noon cron fetch_notion 第一个 API call 就 429 crash，整个主循环都没跑完，
    search fallback 没机会触发。

    主循环 crash 后无法读 `weeks` 变量，跳过 Wxx filter，全局 search Notion 找今天 page。
    跳过已存在的 markdown 文件避免重复拉。
    """
    print("[diag] main loop crashed, attempting search fallback without Wxx filter...", file=sys.stderr)
    today_compact = time.strftime("%Y%m%d", time.localtime())
    try:
        search_req = urllib.request.Request(
            f"https://api.notion.com/v1/search",
            method="POST",
            headers={
                "Authorization": f"Bearer {NOTION_TOKEN}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
            data=json.dumps({"query": today_compact, "page_size": 20}).encode("utf-8"),
        )
        search_res = json.loads(urllib.request.urlopen(search_req, timeout=30).read())
    except Exception as e:
        print(f"[diag] crash fallback search ERR: {type(e).__name__}: {e}", file=sys.stderr)
        return
    for r in search_res.get("results", []):
        if r.get("object") != "page":
            continue
        rt = r.get("properties", {}).get("title", {}).get("title", [])
        ptitle = "".join(t.get("plain_text", "") for t in rt)
        if not ptitle.startswith(today_compact):
            continue
        slug = ptitle.replace("/", "-").replace(" ", "_")
        out_file = OUTPUT_DIR / f"{slug}.md"
        if out_file.exists():
            continue
        try:
            content = page_to_md(r["id"], ptitle)
            content = strip_trailing_json_block(content)
            out_file.write_text(content)
            print(f"OK [crash fallback] {out_file} ({len(content)} bytes)", file=sys.stderr)
        except Exception as e:
            print(f"[diag] crash fallback write ERR: {type(e).__name__}: {e}", file=sys.stderr)


def write_today_summary():
    """2026-09-26 P1：告诉 healthcheck 今天的 markdown 到底在不在 content/post/。

    输出 TODAY_IN_POST=true|false 到 stderr，让 noon cron / healthcheck 能
    精准判断。今天的简报没在 content/post/ → healthcheck 应 ALARM。
    """
    today_compact = time.strftime("%Y%m%d", time.localtime())
    matches = list(OUTPUT_DIR.glob(f"{today_compact}-*.md"))
    if matches:
        names = [p.name for p in matches]
        print(f"[summary] TODAY_IN_POST=true count={len(matches)} files={names}", file=sys.stderr)
    else:
        print(f"[summary] TODAY_IN_POST=false today={today_compact}", file=sys.stderr)


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # 诊断输出
    token = os.environ.get("NOTION_API_TOKEN")
    print(f"[diag] NOTION_API_TOKEN present: {bool(token)}", file=sys.stderr)
    if token:
        print(f"[diag] token prefix: {token[:8]}... suffix: ...{token[-4:]}", file=sys.stderr)
    else:
        print("[diag] FATAL: NOTION_API_TOKEN is empty or not set", file=sys.stderr)
        sys.exit(2)
    print(f"[diag] YEAR_2026_PAGE_ID: {YEAR_2026_PAGE_ID}", file=sys.stderr)
    try:
        # 动态发现：找 📅 2026 下面所有 🗓 Wnn 子页
        weeks_data = api(f"blocks/{YEAR_2026_PAGE_ID}/children")
        weeks = [b for b in weeks_data.get("results", []) if b.get("type") == "child_page"]
        print(f"[diag] Found {len(weeks)} week pages under 📅 2026", file=sys.stderr)
        total = 0
        for week in weeks:
            wpid = week["id"]
            wtitle = week["child_page"]["title"]
            print(f"[diag] -- Processing {wtitle} ({wpid})", file=sys.stderr)
            briefings = api(f"blocks/{wpid}/children").get("results", [])
            print(f"[diag]    {wtitle} has {len(briefings)} children blocks", file=sys.stderr)
            for b in briefings:
                if b.get("type") != "child_page":
                    continue
                pid = b["id"]
                title = b["child_page"]["title"]
                # Fallback: child_page.title 为空时（body 不以 H1 开头 Notion 不会生成），
                # 从 page properties.title 取，或 page children 的第一个 heading_1 取
                if not title:
                    try:
                        meta = api(f"pages/{pid}")
                        rt = meta.get("properties", {}).get("title", {}).get("title", [])
                        if rt:
                            title = rt[0].get("plain_text", "")
                    except Exception:
                        pass
                if not title:
                    try:
                        pb_res = api(f"blocks/{pid}/children").get("results", [])
                        for pb in pb_res:
                            if pb.get("type") == "heading_1":
                                title = rich_text_to_md(pb["heading_1"]["rich_text"]).lstrip("# ").strip()
                                break
                    except Exception:
                        pass
                if not title:
                    print(f"[diag] WARN: 跳过空标题 page {pid}（child_page/properties/heading_1 都为空）", file=sys.stderr)
                    continue
                slug = title.replace("/", "-").replace(" ", "_")
                content = page_to_md(pid, title)
                content = strip_trailing_json_block(content)  # 2026-09-24 修复：兑底剥离末尾 ```json``` code block
                out_file = OUTPUT_DIR / f"{slug}.md"
                out_file.write_text(content)
                print(f"OK {out_file} ({len(content)} bytes)", file=sys.stderr)
                total += 1

        # 2026-09-25 fix：W39 children API indexing 延迟兑底。9-25 实测 children 只返 1 篇
        # （9-21），但 W39 下其实有 4 篇 page（含 9-25/9-22/9-23）。Notion API 是增量 indexing
        # 的，children API 不一定及时更新。如果主循环拉完后今天日期前缀不在已拉 titles 里，
        # 走 POST /v1/search 按 today 8位前缀倒查。今天 page 能找到 parent 是某个 Wxx 就补拉。
        today_compact = time.strftime("%Y%m%d", time.localtime())
        # 2026-09-26 fix：抽离成函数，让主循环崩了 (429/5xx) 也能调用兑底
        fetched, _ = run_search_fallback(weeks, today_compact)
        total += fetched

        # stdout: clean summary only — cron delivery pushes this to channel
        print(f"✅ daily-briefing fetched: {total} briefings from {len(weeks)} weeks")
    except urllib.error.HTTPError as e:
        print(f"[diag] main loop HTTPError: {e.code} {e.reason}（即使 retry 仍失败）", file=sys.stderr)
        body = e.read().decode("utf-8", errors="replace")
        print(f"[diag] response body: {body[:1000]}", file=sys.stderr)
        # 2026-09-26 修复：主循环崩了也要触发 search fallback（仅这一次也要兑底）
        run_search_fallback_after_main_loop_crash()
        sys.exit(3)
    except Exception as e:
        import traceback
        print(f"[diag] Exception: {type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc()
        run_search_fallback_after_main_loop_crash()
        sys.exit(4)
    finally:
        # 2026-09-26 修复：告诉 healthcheck 今天的 markdown 到底在不在
        write_today_summary()


if __name__ == "__main__":
    main()