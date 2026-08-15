#!/usr/bin/env bash
# notion.sh — Notion API helpers (curl + ntn)
#
# 设计原则：
# 1. 每个函数 = 一个独立动作，调用方自己串联。脚本内 chain OK（5 铁律只约束 LLM exec）
# 2. 所有 API 调用走 curl + python3 解析 JSON（避免 ntn 对部分 endpoint 支持不全）
# 3. 失败返回非 0 exit code
# 4. 顶层变量依赖 env.sh 已 source：NOTION_API_TOKEN, NOTION_API_VERSION, NOTION_API_BASE

# ----------------------------------------------------------------------------
# 通用 API 调用
# 用法：notion_api METHOD /v1/... [json_body_file]
# 输出：原始 JSON 写到 stdout
# ----------------------------------------------------------------------------
notion_api() {
  local method="$1"
  local path="$2"
  local body="${3:-}"
  local url="${NOTION_API_BASE}${path}"
  if [ -n "$body" ]; then
    curl -sS -X "$method" "$url" \
      -H "Authorization: Bearer ${NOTION_API_TOKEN}" \
      -H "Notion-Version: ${NOTION_API_VERSION}" \
      -H "Content-Type: application/json" \
      --data-binary "@${body}"
  else
    curl -sS -X "$method" "$url" \
      -H "Authorization: Bearer ${NOTION_API_TOKEN}" \
      -H "Notion-Version: ${NOTION_API_VERSION}"
  fi
}

# ----------------------------------------------------------------------------
# 递归列所有 child block / 子页（带分页）
# 用法：notion_list_children <block_id>
# 输出：JSON 数组到 stdout
# ----------------------------------------------------------------------------
notion_list_children() {
  local block_id="$1"
  local cursor=""
  local out="[]"
  while :; do
    local q=""
    if [ -n "$cursor" ]; then
      q="?start_cursor=${cursor}&page_size=100"
    else
      q="?page_size=100"
    fi
    local resp
    resp="$(curl -sS \
      -H "Authorization: Bearer ${NOTION_API_TOKEN}" \
      -H "Notion-Version: ${NOTION_API_VERSION}" \
      "${NOTION_API_BASE}/blocks/${block_id}/children${q}")"
    # 把 results 追加到 out
    out="$(python3 -c '
import json, sys
cur = json.loads(sys.argv[1])
new = json.loads(sys.argv[2])
cur["results"].extend(new.get("results", []))
cur["has_more"] = new.get("has_more", False)
cur["next_cursor"] = new.get("next_cursor")
print(json.dumps(cur))
' "$out" "$resp")"
    cursor="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(d.get("next_cursor") or "")' "$out")"
    [ -z "$cursor" ] && break
    # 安全上限：防止无限循环
    local cnt
    cnt="$(python3 -c 'import json,sys; print(len(json.loads(sys.argv[1])["results"]))' "$out")"
    if [ "${cnt:-0}" -gt 5000 ]; then
      echo "[notion] WARN: children >5000, abort pagination" >&2
      break
    fi
  done
  echo "$out"
}

# ----------------------------------------------------------------------------
# 找页面：按 title 精确匹配（在指定 parent 下）
# 用法：notion_find_page_by_title <parent_id> <title>
# 输出：page_id 到 stdout（找不到则空字符串）
# ----------------------------------------------------------------------------
notion_find_page_by_title() {
  local parent_id="$1"
  local target_title="$2"
  # 用 search 接口过滤（title 包含 target）
  local resp
  resp="$(curl -sS -X POST "${NOTION_API_BASE}/search" \
    -H "Authorization: Bearer ${NOTION_API_TOKEN}" \
    -H "Notion-Version: ${NOTION_API_VERSION}" \
    -H "Content-Type: application/json" \
    --data-binary "$(python3 -c '
import json,sys
parent = sys.argv[1]
print(json.dumps({"query": sys.argv[2], "filter": {"value": "page", "property": "object"}, "page_size": 50}))
' "$parent_id" "$target_title")")"
  # 解析：找到 parent_id 匹配 + title 一致的 page
  python3 -c '
import json, sys
data = json.loads(sys.argv[1])
parent = sys.argv[2]
target = sys.argv[3]
for r in data.get("results", []):
    if r.get("object") != "page":
        continue
    pid_parent = r.get("parent", {})
    ptype = pid_parent.get("type")
    pid = pid_parent.get(ptype) if ptype else None
    if pid != parent:
        continue
    title = ""
    props = r.get("properties", {})
    if "title" in props:
        title_arr = props["title"].get("title", [])
        title = "".join(t.get("plain_text", "") for t in title_arr)
    elif "Name" in props:
        title_arr = props["Name"].get("title", [])
        title = "".join(t.get("plain_text", "") for t in title_arr)
    if title.strip() == target.strip():
        print(r["id"])
        sys.exit(0)
sys.exit(1)
' "$resp" "$parent_id" "$target_title"
}

# ----------------------------------------------------------------------------
# 创建子页（设置 title 显式，避免 # heading 留空问题）
# 用法：notion_create_page_with_title <parent_id> <title> [content_markdown_file]
# 输出：new page_id 到 stdout
# ----------------------------------------------------------------------------
notion_create_page_with_title() {
  local parent_id="$1"
  local title="$2"
  local content_file="${3:-}"
  # 构建 body
  local body_file
  body_file="$(mktemp)"
  python3 -c '
import json, sys
parent = sys.argv[1]
title = sys.argv[2]
content_file = sys.argv[3]
body = {
    "parent": {"type": "page_id", "page_id": parent},
    "properties": {
        "title": {"title": [{"type": "text", "text": {"content": title}}]}
    }
}
if content_file:
    with open(content_file, "r", encoding="utf-8") as f:
        body["children"] = [{"object":"block","type":"paragraph","paragraph":{"rich_text":[{"type":"text","text":{"content": f.read()[:1900]}}]}}]
print(json.dumps(body))
' "$parent_id" "$title" "$content_file" > "$body_file"
  local resp
  resp="$(curl -sS -X POST "${NOTION_API_BASE}/pages" \
    -H "Authorization: Bearer ${NOTION_API_TOKEN}" \
    -H "Notion-Version: ${NOTION_API_VERSION}" \
    -H "Content-Type: application/json" \
    --data-binary "@${body_file}")"
  rm -f "$body_file"
  # 提取 page id
  python3 -c '
import json, sys
d = json.loads(sys.stdin.read())
if "id" in d:
    print(d["id"])
elif "message" in d:
    print("ERROR:" + d.get("message",""), file=sys.stderr)
    sys.exit(1)
else:
    print("ERROR:unexpected response", file=sys.stderr)
    sys.exit(2)
' <<< "$resp"
}

# ----------------------------------------------------------------------------
# 通过 markdown 创建子页（用 ntn pages create）
# 用法：notion_create_page_md <parent_id> <markdown_file>
# 输出：new page_id 到 stdout
# ----------------------------------------------------------------------------
notion_create_page_md() {
  local parent_id="$1"
  local md_file="$2"
  if [ ! -f "$md_file" ]; then
    echo "ERROR: markdown file not found: $md_file" >&2
    return 1
  fi
  # ntn pages create --parent page:<id> < file
  ntn pages create --parent "page:${parent_id}" < "$md_file" 2>/dev/null
}

# ----------------------------------------------------------------------------
# 设置 page cover (file_upload)
# 用法：notion_set_cover_file <page_id> <file_upload_id>
# ----------------------------------------------------------------------------
notion_set_cover_file() {
  local page_id="$1"
  local fu_id="$2"
  curl -sS -X PATCH "${NOTION_API_BASE}/pages/${page_id}" \
    -H "Authorization: Bearer ${NOTION_API_TOKEN}" \
    -H "Notion-Version: ${NOTION_API_VERSION}" \
    -H "Content-Type: application/json" \
    -d "{\"cover\":{\"type\":\"file_upload\",\"file_upload\":{\"id\":\"${fu_id}\"}}}"
}

# ----------------------------------------------------------------------------
# 设置 page icon (emoji)
# 用法：notion_set_icon_emoji <page_id> <emoji>
# ----------------------------------------------------------------------------
notion_set_icon_emoji() {
  local page_id="$1"
  local emoji="$2"
  curl -sS -X PATCH "${NOTION_API_BASE}/pages/${page_id}" \
    -H "Authorization: Bearer ${NOTION_API_TOKEN}" \
    -H "Notion-Version: ${NOTION_API_VERSION}" \
    -H "Content-Type: application/json" \
    -d "{\"icon\":{\"type\":\"emoji\",\"emoji\":\"${emoji}\"}}"
}

# ----------------------------------------------------------------------------
# file_upload: 单步模式创建 record（无文件内容）
# 用法：notion_create_file_upload_record <filename> <content_type>
# 输出：JSON 到 stdout（含 id + upload_url）
# ----------------------------------------------------------------------------
notion_create_file_upload_record() {
  local filename="$1"
  local content_type="${2:-image/png}"
  curl -sS -X POST "${NOTION_API_BASE}/file_uploads" \
    -H "Authorization: Bearer ${NOTION_API_TOKEN}" \
    -H "Notion-Version: ${NOTION_API_VERSION}" \
    -H "Content-Type: application/json" \
    -d "{\"mode\":\"single_part\",\"filename\":\"${filename}\",\"content_type\":\"${content_type}\"}"
}

# ----------------------------------------------------------------------------
# file_upload: 发送文件内容到 upload_url（multipart）
# 用法：notion_send_file_upload <upload_url> <file_path>
# ----------------------------------------------------------------------------
notion_send_file_upload() {
  local upload_url="$1"
  local file_path="$2"
  curl -sS -X POST "${upload_url}" \
    -H "Authorization: Bearer ${NOTION_API_TOKEN}" \
    -H "Notion-Version: ${NOTION_API_VERSION}" \
    -F "file=@${file_path};type=$(file -b --mime-type "${file_path}" 2>/dev/null || echo image/png);filename=$(basename "${file_path}")"
}

# ----------------------------------------------------------------------------
# 解析 Notion page URL（返回 32 字符 page_id）
# 用法：notion_page_url_to_id <url>
# 输出：page_id（32 hex，no dashes）到 stdout
# ----------------------------------------------------------------------------
notion_page_url_to_id() {
  local url="$1"
  python3 -c '
import re, sys
url = sys.argv[1]
m = re.search(r"([0-9a-f]{32})", url.replace("-", ""))
print(m.group(1) if m else "")
' "$url"
}

# ----------------------------------------------------------------------------
# 解析 JSON 字段（轻量 wrapper，避免每个调用都写 python3 -c）
# 用法：echo "$json" | jget <key1> [key2] ...
# ----------------------------------------------------------------------------
jget() {
  python3 -c '
import json, sys
keys = sys.argv[1:]
d = json.load(sys.stdin)
for k in keys:
    if d is None:
        break
    d = d.get(k)
print("" if d is None else d)
' "$@"
}