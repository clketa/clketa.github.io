#!/usr/bin/env python3
"""briefing-build-summary.py — 构建 Notion 📋 今日摘要 block JSON

输入：
- stdin 或 --input-file：JSON 字段（punchline, domestic_main, international_main, market_main, tomorrow_watch, page_id, divider_id）

输出：
- stdout：Notion blocks/.../children PATCH body（JSON）
- 内含 position: { type: "after_block", after_block: { id: divider_id } }

用法（在 bash 里）：
  echo '{...}' | briefing-build-summary.py > summary_body.json
  curl -X PATCH "$NOTION_API_BASE/blocks/$PAGE_ID/children" \
    -H "..." --data-binary @summary_body.json

设计：每个 block 是单一独立动作，block 数组按顺序 = 渲染顺序
"""
import argparse
import json
import sys
from pathlib import Path


def rich_text(content: str, bold: bool = False, code: bool = False) -> list:
    """构造 rich_text 数组（短文本直接数组，无 annotation 包裹）"""
    text_obj = {"type": "text", "text": {"content": content}}
    if bold or code:
        text_obj["annotations"] = {"bold": bold, "code": code}
    return [text_obj]


def heading_2(text: str) -> dict:
    return {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": rich_text(text)},
    }


def heading_3(text: str) -> dict:
    return {
        "object": "block",
        "type": "heading_3",
        "heading_3": {"rich_text": rich_text(text)},
    }


def paragraph(rich_texts: list) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": rich_texts},
    }


def callout(emoji: str, rich_texts: list, color: str = "default") -> dict:
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": rich_texts,
            "icon": {"type": "emoji", "emoji": emoji},
            "color": color,
        },
    }


def divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def bulleted_list_item(text: str) -> dict:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": rich_text(text)},
    }


def build_blocks(fields: dict) -> list:
    """按顺序构造 12 个 block"""
    blocks = []

    # 1. heading_2 — 📋 今日摘要
    blocks.append(heading_2("📋 今日摘要"))

    # 2. callout — 💡 苒苒一句话 + punchline
    punchline = fields.get("punchline") or "（今日 punchline 待补充）"
    callout_rt = (
        rich_text("💡 ", bold=False) +
        rich_text("苒苒一句话：", bold=True) +
        rich_text(punchline)
    )
    blocks.append(callout("💡", callout_rt, color="yellow_background"))

    # 3. paragraph — 国内主线
    domestic = fields.get("domestic_main") or "（今日国内主线待补充）"
    blocks.append(paragraph(
        rich_text("国内主线：", bold=True) + rich_text(domestic)
    ))

    # 4. paragraph — 国际主线
    intl = fields.get("international_main") or "（今日国际主线待补充）"
    blocks.append(paragraph(
        rich_text("国际主线：", bold=True) + rich_text(intl)
    ))

    # 5. paragraph — 市场与隐忧（可选）
    market = fields.get("market_main") or ""
    if market:
        blocks.append(paragraph(
            rich_text("市场与隐忧：", bold=True) + rich_text(market)
        ))

    # 6. heading_3 — 明日值得关注
    blocks.append(heading_3("明日值得关注"))

    # 7-11. 5× bulleted_list_item
    tomorrow = fields.get("tomorrow_watch") or []
    if not isinstance(tomorrow, list):
        tomorrow = [str(tomorrow)]
    # 补齐到 5 条
    while len(tomorrow) < 5:
        tomorrow.append("（待补充）")
    for item in tomorrow[:5]:
        blocks.append(bulleted_list_item(str(item)))

    # 12. divider — 视觉分隔
    blocks.append(divider())

    return blocks


def build_patch_body(fields: dict) -> dict:
    blocks = build_blocks(fields)
    divider_id = fields.get("divider_id", "")
    body = {"children": blocks}
    if divider_id:
        body["position"] = {
            "type": "after_block",
            "after_block": {"id": divider_id},
        }
    return body


def main():
    parser = argparse.ArgumentParser(description="Build Notion summary block JSON")
    parser.add_argument("--input-file", default="-", help="输入 JSON，- 表示 stdin")
    parser.add_argument("--output", default="-", help="输出 JSON，- 表示 stdout")
    args = parser.parse_args()

    if args.input_file == "-":
        data = json.load(sys.stdin)
    else:
        data = json.loads(Path(args.input_file).read_text(encoding="utf-8"))

    body = build_patch_body(data)
    text = json.dumps(body, ensure_ascii=False, indent=2)
    if args.output == "-":
        print(text)
    else:
        Path(args.output).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())