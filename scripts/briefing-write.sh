#!/usr/bin/env bash
# briefing-write.sh — 苒苒日简报主流程（cron argv 直跑）
#
# 设计：每个步骤独立、显式错误处理、失败返回非 0 exit code。
# 脚本内 chain (&&/;/) OK —— 5 铁律只约束 LLM 写 exec，不约束 shell 脚本。
#
# 用法：
#   bash briefing-write.sh                    # 正常跑（cron 默认）
#   bash briefing-write.sh --dry-run          # 干跑：跳过 LLM/image/Notion 写
#   DRY_RUN=1 bash briefing-write.sh          # 同上，环境变量方式
#   bash briefing-write.sh --skip-voice       # 跳过语音生成
#   bash briefing-write.sh --skip-cover       # 跳过封面图生成
#   bash briefing-write.sh --date 2026-08-15  # 跑指定日期（默认今天）
#
# 退出码：
#   0  - 全部成功
#   1  - 通用错误（参数解析等）
#   2  - env 加载失败（NOTION_API_TOKEN 缺失）
#   3  - Notion 路径解析失败
#   4  - 4 路 web_search 全部失败
#   5  - LLM 调用失败
#   6  - 封面图生成失败
#   7  - 封面图上传到 Notion 失败
#   8  - Notion 页面创建失败
#   9  - 今日摘要 block 插入失败
#   10 - cover/icon 设置失败
#   11 - 语音生成失败（不致命，记 warn）
#   12 - 本日简报已存在（NOTION_HAS_TODAY=1）

set -u  # 不用 set -e / pipefail —— 自己控制每个步骤退出码

# ----------------------------------------------------------------------------
# CLI 参数解析
# ----------------------------------------------------------------------------
SKIP_VOICE=0
SKIP_COVER=0
CUSTOM_DATE=""
DRY_RUN="${DRY_RUN:-0}"

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)       DRY_RUN=1 ;;
    --skip-voice)    SKIP_VOICE=1 ;;
    --skip-cover)    SKIP_COVER=1 ;;
    --date)          CUSTOM_DATE="${2:-}"; shift ;;
    --date=*)        CUSTOM_DATE="${1#*=}" ;;
    -h|--help)
      sed -n '2,28p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      exit 1
      ;;
  esac
  shift
done

export DRY_RUN

# ----------------------------------------------------------------------------
# 加载 lib
# ----------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/env.sh"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/log.sh"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/notion.sh"

log_init "/tmp/briefing-write-$$.log"

# ----------------------------------------------------------------------------
# Step 0: 加载环境
# ----------------------------------------------------------------------------
log_section "Step 0: load env"
if ! briefing_load_env; then
  log_error "FATAL: NOTION_API_TOKEN 未加载"
  exit 2
fi
log_info "NOTION_API_TOKEN loaded (prefix: ${NOTION_API_TOKEN:0:8}...)"

# ----------------------------------------------------------------------------
# Step 1: 计算日期 / 周数 / 年份 / is_monday
# ----------------------------------------------------------------------------
log_section "Step 1: compute date"
if [ -n "$CUSTOM_DATE" ]; then
  TODAY="$CUSTOM_DATE"
  # 用指定日期作为今日，重算周数 / 年份 / is_monday
  # 但日期相关函数读系统时间，这里 override
  YEAR="$(date -d "$TODAY" '+%Y')"
  ISO_WEEK="$(date -d "$TODAY" '+%V')"
  WEEK_TAG="W$(printf '%02d' "$((10#$ISO_WEEK))")"
  # is_monday: date -d "YYYY-MM-DD" '+%u' 给星期几
  DOW="$(date -d "$TODAY" '+%u')"
  if [ "$DOW" = "1" ]; then IS_MONDAY=1; else IS_MONDAY=0; fi
else
  mapfile -t DATE_INFO < <(briefing_compute_date)
  TODAY="${DATE_INFO[0]}"
  YEAR="${DATE_INFO[1]}"
  WEEK_TAG="${DATE_INFO[2]}"
  IS_MONDAY="${DATE_INFO[3]}"
fi
TODAY_COMPACT="$(echo "$TODAY" | tr -d '-')"  # yyyymmdd
log_info "TODAY=$TODAY  YEAR=$YEAR  WEEK_TAG=$WEEK_TAG  IS_MONDAY=$IS_MONDAY  COMPACT=$TODAY_COMPACT"

# ----------------------------------------------------------------------------
# 常量（来自 daily-news-briefing skill 2026-07-13）
# ----------------------------------------------------------------------------
QI_JU_ZHU_PAGE_ID="34ab7087-4975-80ce-b016-ffa9cbd97adb"  # 起居注
ZI_XUN_PAGE_ID="395b7087-4975-81f4-8b6c-c695d1e65e38"      # 📑 资讯
# 📅 YYYY + 🗓 Wnn 需要动态查/创建
YEAR_PAGE_TITLE="📅 ${YEAR}"
WEEK_PAGE_TITLE="🗓 ${WEEK_TAG}"

# ----------------------------------------------------------------------------
# Step 2: 解析 Notion 路径 起居注 → 资讯 → YYYY → Wnn
# ----------------------------------------------------------------------------
log_section "Step 2: verify Notion path"

if [ "$DRY_RUN" = "1" ]; then
  log_info "[dry-run] 跳过 Notion 路径查询"
  YEAR_PAGE_ID="DRY-RUN-YEAR-ID"
  WEEK_PAGE_ID="DRY-RUN-WEEK-ID"
else
  # 2a. 起居注 → 资讯（已 cache，可直接验）
  log_info "起居注 (cached) = $QI_JU_ZHU_PAGE_ID"
  log_info "📑 资讯 (cached) = $ZI_XUN_PAGE_ID"
  # 2b. 找 📅 YYYY 子页
  YEAR_PAGE_ID="$(notion_find_page_by_title "$ZI_XUN_PAGE_ID" "$YEAR_PAGE_TITLE" 2>/dev/null || true)"
  if [ -z "$YEAR_PAGE_ID" ]; then
    log_warn "📅 $YEAR 不存在，创建中..."
    YEAR_PAGE_ID="$(notion_create_page_with_title "$ZI_XUN_PAGE_ID" "$YEAR_PAGE_TITLE")" || {
      log_error "创建 📅 $YEAR 失败"; exit 3;
    }
    log_info "📅 $YEAR 创建成功: $YEAR_PAGE_ID"
  else
    log_info "📅 $YEAR 已存在: $YEAR_PAGE_ID"
  fi
  # 2c. 找 🗓 Wnn 子页
  WEEK_PAGE_ID="$(notion_find_page_by_title "$YEAR_PAGE_ID" "$WEEK_PAGE_TITLE" 2>/dev/null || true)"
  if [ -z "$WEEK_PAGE_ID" ]; then
    log_warn "$WEEK_PAGE_TITLE 不存在，创建中..."
    WEEK_PAGE_ID="$(notion_create_page_with_title "$YEAR_PAGE_ID" "$WEEK_PAGE_TITLE")" || {
      log_error "创建 $WEEK_PAGE_TITLE 失败"; exit 3;
    }
    log_info "$WEEK_PAGE_TITLE 创建成功: $WEEK_PAGE_ID"
  else
    log_info "$WEEK_PAGE_TITLE 已存在: $WEEK_PAGE_ID"
  fi
fi

# ----------------------------------------------------------------------------
# Step 3: 列本周已发简报 → dedup
# ----------------------------------------------------------------------------
log_section "Step 3: list existing briefings in $WEEK_TAG"

EXISTING_BRIEFINGS_FILE="${BRIEFING_TMP_DIR}/existing-briefings-${TODAY_COMPACT}.txt"
: > "$EXISTING_BRIEFINGS_FILE"

if [ "$DRY_RUN" = "1" ]; then
  log_info "[dry-run] 跳过已有简报查询"
  echo "(dry-run: 无已有简报数据)" > "$EXISTING_BRIEFINGS_FILE"
else
  CHILDREN_JSON="$(notion_list_children "$WEEK_PAGE_ID")"
  # 提取所有 child_page 的 id + title
  python3 -c '
import json, sys
data = json.loads(sys.argv[1])
target = sys.argv[2]
for b in data.get("results", []):
    if b.get("type") == "child_page":
        title = b.get("child_page", {}).get("title", "")
        bid = b.get("id", "")
        if title.startswith(target):
            print(f"- {title}  (id={bid})")
' "$CHILDREN_JSON" "$TODAY_COMPACT" >> "$EXISTING_BRIEFINGS_FILE"
  EXISTING_COUNT="$(grep -c '^- ' "$EXISTING_BRIEFINGS_FILE" || true)"
  log_info "本周已发今日前缀 ($TODAY_COMPACT-*) 简报数: $EXISTING_COUNT"
  # 如果今天已经发过，跳过写稿
  if [ "${EXISTING_COUNT:-0}" -gt 0 ]; then
    log_warn "今日 ($TODAY_COMPACT) 已有简报 $EXISTING_COUNT 篇，skip 写稿 + 上传"
    log_info "仅发语音确认（QQBot 通道）"
    cat "$EXISTING_BRIEFINGS_FILE" >&2
    NOTION_HAS_TODAY=1
  else
    NOTION_HAS_TODAY=0
  fi
fi

# ----------------------------------------------------------------------------
# Step 4: 4 路并行 web_search → JSONL
# ----------------------------------------------------------------------------
log_section "Step 4: 4-way web_search"
SEARCH_DIR="${BRIEFING_TMP_DIR}/search-${TODAY_COMPACT}"
mkdir -p "$SEARCH_DIR"
SEARCH_FILES=()

run_search() {
  local label="$1" query="$2"
  local outfile="${SEARCH_DIR}/${label}.jsonl"
  log_info "  search[$label] $query"
  if [ "$DRY_RUN" = "1" ]; then
    echo "{\"label\":\"$label\",\"query\":\"$query\",\"dry_run\":true,\"results\":[]}" > "$outfile"
  else
    mmx search query --q "$query" --output json > "$outfile" 2>/dev/null || \
      echo "{\"label\":\"$label\",\"query\":\"$query\",\"error\":\"search_failed\",\"results\":[]}" > "$outfile"
  fi
  if [ ! -s "$outfile" ]; then
    echo "{\"label\":\"$label\",\"query\":\"$query\",\"error\":\"empty\",\"results\":[]}" > "$outfile"
  fi
  SEARCH_FILES+=("$outfile")
  log_info "  search[$label] done → $outfile"
}

# 4 路并行（SEARCH_FILES 必须在父 shell 预定义 —— 子 shell 中的数组变更不会传回）
# 2026-08-15 修复：原版在 run_search 函数内做 SEARCH_FILES+=，但 4 路用 & 后台启动，
# 实际在子 shell 跑，修改不会传回父 shell，导致 SEARCH_OK 永远 = 0，触发 exit 4
SEARCH_FILES=(
  "${SEARCH_DIR}/domestic.jsonl"
  "${SEARCH_DIR}/international.jsonl"
  "${SEARCH_DIR}/tech_finance.jsonl"
  "${SEARCH_DIR}/markets.jsonl"
)
run_search "domestic"     "$TODAY 中国 国内 重要新闻 头条" &
PID1=$!
run_search "international" "$TODAY world top stories breaking news" &
PID2=$!
run_search "tech_finance"  "$TODAY 科技 财经 重要新闻 半导体 AI" &
PID3=$!
run_search "markets"       "global financial markets $TODAY Fed ECB central bank" &
PID4=$!
wait $PID1 $PID2 $PID3 $PID4

# 校验 4 路是否都成功
SEARCH_OK=0
for f in "${SEARCH_FILES[@]}"; do
  if grep -q '"error"' "$f" 2>/dev/null; then
    log_warn "search file 含 error: $f"
  else
    SEARCH_OK=$((SEARCH_OK+1))
  fi
done
log_info "search OK count: $SEARCH_OK / 4"
if [ "$SEARCH_OK" -eq 0 ] && [ "$DRY_RUN" != "1" ]; then
  log_error "4 路 search 全部失败"
  exit 4
fi

# ----------------------------------------------------------------------------
# Step 5: LLM 生成正文（Python wrapper）
# ----------------------------------------------------------------------------
log_section "Step 5: LLM generate body"
LLM_OUT="${BRIEFING_TMP_DIR}/llm-${TODAY_COMPACT}.json"
LLM_MD="${BRIEFING_TMP_DIR}/briefing-${TODAY_COMPACT}.md"
: > "$LLM_OUT"

if [ "$DRY_RUN" = "1" ]; then
  log_info "[dry-run] 跳过 LLM 调用，生成 fixture"
  cat > "$LLM_OUT" <<'FIX'
{
  "ok": true,
  "model": "MiniMax-M3 (dry-run)",
  "raw_text": "# 20260815-DRYRUN简报测试占位符\n\n(dry-run fixture)",
  "title": "20260815-DRYRUN简报测试占位符",
  "body_markdown": "# 20260815-DRYRUN简报测试占位符\n\n## 🇨🇳 国内要闻\n\n(dry-run)\n\n## 🌍 国际要闻\n\n(dry-run)",
  "summary_punchline": "[dry-run] 今日一句话 punchline 占位",
  "summary_domestic": "[dry-run] 国内主线占位",
  "summary_international": "[dry-run] 国际主线占位",
  "summary_market": "[dry-run] 市场与隐忧占位",
  "tomorrow_watch": ["占位 1", "占位 2", "占位 3", "占位 4", "占位 5"],
  "duration_seconds": 0.0,
  "error": null
}
FIX
elif [ "${NOTION_HAS_TODAY:-0}" = "1" ]; then
  log_info "今日已有简报，跳过 LLM"
else
  USER_PROMPT="请写今天的简报（$TODAY，${WEEK_TAG}，$( [ "$IS_MONDAY" = "1" ] && echo "周一（含本周日历）" || echo "非周一（不含本周日历）")）。"
  python3 "${SCRIPT_DIR}/briefing-llm-call.py" \
    --user "$USER_PROMPT" \
    --search-files "${SEARCH_FILES[@]}" \
    --existing-briefings "$EXISTING_BRIEFINGS_FILE" \
    --output "$LLM_OUT" 2>"${LLM_OUT}.err"
  RC=$?
  if [ $RC -ne 0 ]; then
    log_error "LLM 调用失败 exit=$RC"
    cat "${LLM_OUT}.err" >&2
    exit 5
  fi
  rm -f "${LLM_OUT}.err"
fi

# 校验 LLM 输出
if [ ! -s "$LLM_OUT" ]; then
  log_error "LLM 输出为空"
  exit 5
fi
briefing_assert_file_size "$LLM_OUT" 100
LLM_OK=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(1 if d.get("ok") else 0)' "$LLM_OUT" 2>/dev/null || echo 0)
if [ "${LLM_OK:-0}" != "1" ]; then
  log_error "LLM 输出 ok!=true"
  cat "$LLM_OUT" >&2
  exit 5
fi

LLM_TITLE=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("title",""))' "$LLM_OUT")
LLM_BODY=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("body_markdown",""))' "$LLM_OUT")
log_info "LLM title=$LLM_TITLE  body length=${#LLM_BODY}"
echo "$LLM_BODY" > "$LLM_MD"
briefing_assert_file_size "$LLM_MD" 50

# ----------------------------------------------------------------------------
# Step 6: 生成封面图
# ----------------------------------------------------------------------------
log_section "Step 6: generate cover image"
COVER_PATH="${BRIEFING_MEDIA_DIR}/briefing-${TODAY_COMPACT}-cover.png"

if [ "$SKIP_COVER" = "1" ]; then
  log_info "skip-cover 模式，跳过封面"
elif [ "$DRY_RUN" = "1" ]; then
  log_info "[dry-run] 跳过封面生成"
  echo "[dry-run placeholder]" > "$COVER_PATH"
elif [ "${NOTION_HAS_TODAY:-0}" = "1" ]; then
  log_info "今日已有简报，跳过封面"
else
  COVER_PROMPT="Editorial illustration for daily news briefing $TODAY: ${LLM_TITLE#${TODAY_COMPACT}-}. Modern flat-vector style, 16:9 aspect ratio, no text, no logos. Use motifs from today's top stories (geopolitics, technology, finance). Soft editorial color palette, magazine-quality composition, professional news illustration style."
  log_info "  mmx image generate ... --out $COVER_PATH"
  mmx image generate \
    --prompt "$COVER_PROMPT" \
    --aspect-ratio "16:9" \
    --out "$COVER_PATH" 2>"${COVER_PATH}.err"
  RC=$?
  if [ $RC -ne 0 ] || [ ! -s "$COVER_PATH" ]; then
    log_error "封面图生成失败 exit=$RC — stderr 保留到 ${COVER_PATH}.err，内容打到 stdout 给 OpenClaw 捕获"
    cat "${COVER_PATH}.err" 2>/dev/null || echo "(.err file empty/missing)"
    exit 6
  fi
  rm -f "${COVER_PATH}.err"
  briefing_assert_file_size "$COVER_PATH" 10240  # 至少 10KB
fi

# ----------------------------------------------------------------------------
# Step 7: 上传封面图到 Notion (single_part 模式)
# ----------------------------------------------------------------------------
log_section "Step 7: upload cover to Notion"
FILE_UPLOAD_ID=""

if [ "$DRY_RUN" = "1" ]; then
  log_info "[dry-run] 跳过 file_upload"
  FILE_UPLOAD_ID="DRY-RUN-FILE-ID"
elif [ "${NOTION_HAS_TODAY:-0}" = "1" ] || [ "$SKIP_COVER" = "1" ]; then
  log_info "跳过 file_upload"
else
  # Step 7a: 创建 record（stderr 捕获到 .err 文件，失败时 cat 到 stderr）
  log_info "  POST /v1/file_uploads (single_part)"
  FU_ERR_LOG="${BRIEFING_TMP_DIR}/briefing-${TODAY_COMPACT}-fu-err.log"
  FU_RECORD="$(notion_create_file_upload_record "briefing-${TODAY_COMPACT}-cover.png" "image/png" 2>"$FU_ERR_LOG")"
  FILE_UPLOAD_ID="$(echo "$FU_RECORD" | jget id)"
  UPLOAD_URL="$(echo "$FU_RECORD" | jget upload_url)"
  if [ -z "$FILE_UPLOAD_ID" ] || [ -z "$UPLOAD_URL" ]; then
    log_error "file_upload record 创建失败 — FU_RECORD 输出到 stdout 供 OpenClaw 捕获"
    echo "FU_RECORD=$FU_RECORD"
    cat "$FU_ERR_LOG" 2>/dev/null || echo "(.err file empty)"
    exit 7
  fi
  log_info "  file_upload id=$FILE_UPLOAD_ID"
  # Step 7b: 发送文件（同样 stderr 捕获）
  log_info "  POST $UPLOAD_URL (multipart)"
  FU_RESULT="$(notion_send_file_upload "$UPLOAD_URL" "$COVER_PATH" 2>>"$FU_ERR_LOG")"
  FU_STATUS="$(echo "$FU_RESULT" | jget status)"
  if [ "$FU_STATUS" != "uploaded" ]; then
    log_error "file_upload send 失败 status=$FU_STATUS — FU_RESULT 输出到 stdout 供 OpenClaw 捕获"
    echo "FU_RESULT=$FU_RESULT"
    cat "$FU_ERR_LOG" 2>/dev/null || echo "(.err file empty)"
    exit 7
  fi
  log_info "  file_upload status=uploaded ✓"
  rm -f "$FU_ERR_LOG"
fi

# ----------------------------------------------------------------------------
# Step 8: 创建 Notion 页面（用 markdown body）
# ----------------------------------------------------------------------------
log_section "Step 8: create Notion page"
NEW_PAGE_ID=""
NEW_PAGE_URL=""

if [ "$DRY_RUN" = "1" ]; then
  log_info "[dry-run] 跳过 Notion 页面创建"
  NEW_PAGE_ID="DRY-RUN-PAGE-ID"
  NEW_PAGE_URL="https://www.notion.so/clketa/DRY-RUN-PAGE-${TODAY_COMPACT}"
elif [ "${NOTION_HAS_TODAY:-0}" = "1" ]; then
  log_info "今日已有简报，跳过创建"
else
  log_info "  ntn pages create --parent page:$WEEK_PAGE_ID"
  NEW_PAGE_ID="$(notion_create_page_md "$WEEK_PAGE_ID" "$LLM_MD")" || {
    log_error "Notion 页面创建失败"; exit 8;
  }
  if [ -z "$NEW_PAGE_ID" ]; then
    log_error "Notion 页面 ID 解析失败"
    exit 8
  fi
  log_info "  page_id=$NEW_PAGE_ID"
  NEW_PAGE_URL="https://www.notion.so/$(notion_page_url_to_id "$NEW_PAGE_ID")"
  log_info "  url=$NEW_PAGE_URL"
fi

# ----------------------------------------------------------------------------
# Step 9: 插入 📋 今日摘要（callout + 主线 + 明日 5 条 + divider）
# ----------------------------------------------------------------------------
log_section "Step 9: insert summary module"

if [ "$DRY_RUN" = "1" ]; then
  log_info "[dry-run] 跳过 summary 插入"
elif [ "${NOTION_HAS_TODAY:-0}" = "1" ]; then
  log_info "跳过 summary 插入"
else
  # 找页面里 divider block id（在 markdown body 自动生成的位置）
  log_info "  GET /v1/blocks/$NEW_PAGE_ID/children → 找 divider"
  CHILDREN_RESP="$(notion_list_children "$NEW_PAGE_ID" 2>/dev/null | head -c 50000)"
  DIVIDER_ID="$(python3 -c '
import json, sys
try:
    d = json.loads(sys.argv[1])
    for b in d.get("results", []):
        if b.get("type") == "divider":
            print(b["id"])
            sys.exit(0)
except Exception:
    pass
sys.exit(1)
' "$CHILDREN_RESP")"
  if [ -z "$DIVIDER_ID" ]; then
    log_warn "找不到 divider block（markdown body 可能没生成 divider）"
    log_info "尝试在页面末尾插入（不指定 position）"
    DIVIDER_ID=""
  else
    log_info "  divider_id=$DIVIDER_ID"
  fi
  # 构造 body
  SUMMARY_INPUT="$(python3 -c '
import json, sys
llm = json.load(open(sys.argv[1]))
divider = sys.argv[2]
out = {
  "punchline": llm.get("summary_punchline",""),
  "domestic_main": llm.get("summary_domestic",""),
  "international_main": llm.get("summary_international",""),
  "market_main": llm.get("summary_market",""),
  "tomorrow_watch": llm.get("tomorrow_watch", []),
  "divider_id": divider,
}
print(json.dumps(out, ensure_ascii=False))
' "$LLM_OUT" "$DIVIDER_ID")"
  SUMMARY_BODY_FILE="${BRIEFING_TMP_DIR}/summary-body-${TODAY_COMPACT}.json"
  echo "$SUMMARY_INPUT" | python3 "${SCRIPT_DIR}/briefing-build-summary.py" > "$SUMMARY_BODY_FILE"
  # PATCH 插入
  log_info "  PATCH /v1/blocks/$NEW_PAGE_ID/children"
  PATCH_RESP="$(curl -sS -X PATCH "${NOTION_API_BASE}/blocks/${NEW_PAGE_ID}/children" \
    -H "Authorization: Bearer ${NOTION_API_TOKEN}" \
    -H "Notion-Version: ${NOTION_API_VERSION}" \
    -H "Content-Type: application/json" \
    --data-binary "@${SUMMARY_BODY_FILE}")"
  PATCH_OK="$(echo "$PATCH_RESP" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(1 if "results" in d else 0)' 2>/dev/null || echo 0)"
  if [ "${PATCH_OK:-0}" != "1" ]; then
    log_error "summary 插入失败"
    echo "$PATCH_RESP" >&2
    exit 9
  fi
  log_info "  summary 插入成功"
fi

# ----------------------------------------------------------------------------
# Step 10: 设置 cover / icon
# ----------------------------------------------------------------------------
log_section "Step 10: set cover + icon"

if [ "$DRY_RUN" = "1" ]; then
  log_info "[dry-run] 跳过 cover/icon 设置"
elif [ "${NOTION_HAS_TODAY:-0}" = "1" ]; then
  log_info "跳过 cover/icon 设置"
else
  if [ -n "${FILE_UPLOAD_ID:-}" ] && [ "$FILE_UPLOAD_ID" != "DRY-RUN-FILE-ID" ]; then
    log_info "  PATCH cover = file_upload $FILE_UPLOAD_ID"
    CV="$(notion_set_cover_file "$NEW_PAGE_ID" "$FILE_UPLOAD_ID")"
    if ! echo "$CV" | grep -q '"id"'; then
      log_error "cover 设置失败"
      echo "$CV" >&2
      exit 10
    fi
    log_info "  cover OK"
  fi
  log_info "  PATCH icon = 📰"
  IC="$(notion_set_icon_emoji "$NEW_PAGE_ID" "📰")"
  if ! echo "$IC" | grep -q '"id"'; then
    log_error "icon 设置失败"
    echo "$IC" >&2
    exit 10
  fi
  log_info "  icon OK"
fi

# ----------------------------------------------------------------------------
# Step 11: 生成语音（QQBot 通道 BRIEFING MODE）
# ----------------------------------------------------------------------------
log_section "Step 11: generate voice"
VOICE_FILE="${BRIEFING_TMP_DIR}/briefing-${TODAY_COMPACT}-voice.txt"

# 构造语音文本（BRIEFING MODE 顺序：punchline + 标题 + 国内主线 + 国际主线 + 覆盖范围）
VOICE_COVERAGE=$([ "$IS_MONDAY" = "1" ] && echo "周一含周历" || echo "非周一不含周历")
VOICE_TEXT="$(python3 -c '
import json, sys
llm = json.load(open(sys.argv[1]))
date = sys.argv[2]
coverage = sys.argv[3]
title = llm.get("title","")
punch = llm.get("summary_punchline","")
dom = llm.get("summary_domestic","")
intl = llm.get("summary_international","")
out = []
out.append(f"老板，今日简报跑完了。{date}")
out.append(f"标题：{title}")
if punch: out.append(f"punchline：{punch}")
if dom: out.append(f"国内主线：{dom}")
if intl: out.append(f"国际主线：{intl}")
out.append(f"覆盖范围：已避重 / 已后续标注 / {coverage}")
print("\n".join(out))
' "$LLM_OUT" "$TODAY" "$VOICE_COVERAGE" 2>/dev/null || echo "（语音文本构造失败）")"
echo "$VOICE_TEXT" > "$VOICE_FILE"
log_info "voice text: $VOICE_FILE ($(wc -c < "$VOICE_FILE") bytes)"

if [ "$SKIP_VOICE" = "1" ] || [ "$DRY_RUN" = "1" ]; then
  log_info "skip-voice 或 dry-run，跳过 mmx speech synthesize"
  VOICE_MP3=""
else
  VOICE_MP3="${BRIEFING_MEDIA_DIR}/briefing-${TODAY_COMPACT}-voice.mp3"
  log_info "  mmx speech synthesize ... --out $VOICE_MP3"
  if mmx speech synthesize \
      --text "$VOICE_TEXT" \
      --language zh \
      --voice "Chinese (Mandarin)_Warm_Bestie" \
      --volume 3 \
      --out "$VOICE_MP3" 2>"${VOICE_MP3}.err"; then
    briefing_assert_file_size "$VOICE_MP3" 1024 || true  # mp3 至少 1KB
    log_info "  voice mp3 OK: $VOICE_MP3"
  else
    log_warn "语音生成失败（不致命）"
    cat "${VOICE_MP3}.err" >&2 2>/dev/null || true
    rm -f "${VOICE_MP3}.err"
    VOICE_MP3=""
  fi
fi

# ----------------------------------------------------------------------------
# Step 12: 输出 BRIEFING MODE 报告（cron runner 投递用）
# ----------------------------------------------------------------------------
log_section "Step 12: emit report"

REPORT_FILE="${BRIEFING_TMP_DIR}/briefing-report-${TODAY_COMPACT}.json"
python3 -c '
import json, sys
out = {
    "ok": True,
    "date": sys.argv[1],
    "title": sys.argv[2],
    "notion_url": sys.argv[3],
    "voice_text": open(sys.argv[4]).read(),
    "voice_mp3": sys.argv[5] or None,
    "dry_run": sys.argv[6] == "1",
    "skipped_today_exists": sys.argv[7] == "1",
}
print(json.dumps(out, ensure_ascii=False, indent=2))
' "$TODAY" "$LLM_TITLE" "${NEW_PAGE_URL:-}" "$VOICE_FILE" "${VOICE_MP3:-}" "$DRY_RUN" "${NOTION_HAS_TODAY:-0}" > "$REPORT_FILE"
log_info "report: $REPORT_FILE"

# 直接 stdout 打一份（cron runner capture）
cat "$REPORT_FILE"

# 本次 run 用的关键路径写到 /tmp 方便 debug
cat > "/tmp/briefing-last-run.env" <<ENV
TODAY=$TODAY
YEAR=$YEAR
WEEK_TAG=$WEEK_TAG
IS_MONDAY=$IS_MONDAY
TODAY_COMPACT=$TODAY_COMPACT
LLM_TITLE=$LLM_TITLE
NEW_PAGE_ID=${NEW_PAGE_ID:-}
NEW_PAGE_URL=${NEW_PAGE_URL:-}
FILE_UPLOAD_ID=${FILE_UPLOAD_ID:-}
COVER_PATH=$COVER_PATH
VOICE_MP3=${VOICE_MP3:-}
VOICE_FILE=$VOICE_FILE
LLM_OUT=$LLM_OUT
LLM_MD=$LLM_MD
SEARCH_DIR=$SEARCH_DIR
LOG_FILE=$LOG_FILE
DRY_RUN=$DRY_RUN
ENV

log_section "DONE"
log_info "TODAY=$TODAY  LLM_TITLE=$LLM_TITLE  NEW_PAGE_URL=${NEW_PAGE_URL:-}"

# 退出码
if [ "${NOTION_HAS_TODAY:-0}" = "1" ]; then
  exit 12
fi
exit 0