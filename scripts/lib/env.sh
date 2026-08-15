#!/usr/bin/env bash
# env.sh — 加载 ~/.bashrc 注入 NOTION_API_TOKEN 等环境变量，并定义全局常量
# 用法：
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/env.sh"
#
# 注意：本脚本不会 set -e / set -u / set -o pipefail — 让上游调用者自己决定。
# 5 铁律只约束 LLM exec，脚本内 chain OK。

# ----------------------------------------------------------------------------
# 路径常量
# ----------------------------------------------------------------------------
BRIEFING_SITE_ROOT="${BRIEFING_SITE_ROOT:-/home/ubuntu/projects/briefing-site}"
BRIEFING_TMP_DIR="${BRIEFING_TMP_DIR:-/home/ubuntu/.openclaw/workspace/.briefing-tmp}"
BRIEFING_SCRIPTS_DIR="${BRIEFING_SCRIPTS_DIR:-${BRIEFING_SITE_ROOT}/scripts}"
BRIEFING_LIB_DIR="${BRIEFING_LIB_DIR:-${BRIEFING_SCRIPTS_DIR}/lib}"
BRIEFING_LOG_DIR="${BRIEFING_LOG_DIR:-/tmp}"

# 媒体保存目录（mmx image-generate 默认输出）
BRIEFING_MEDIA_DIR="${BRIEFING_MEDIA_DIR:-/home/ubuntu/.openclaw/media/tool-image-generation}"

# Notion API
NOTION_API_VERSION="${NOTION_API_VERSION:-2026-03-11}"
NOTION_API_BASE="https://api.notion.com/v1"

# mmx 模型
BRIEFING_LLM_MODEL="${BRIEFING_LLM_MODEL:-MiniMax-M3}"

# 干跑模式
DRY_RUN="${DRY_RUN:-0}"

# ----------------------------------------------------------------------------
# 加载 ~/.bashrc 注入 NOTION_API_TOKEN 等
# bashrc 顶部 export 在 case guard 之上，非交互 shell 不自动 source。
# ----------------------------------------------------------------------------
briefing_load_env() {
  if [ -z "${NOTION_API_TOKEN:-}" ] && [ -f "$HOME/.bashrc" ]; then
    # bash -c ". ~/.bashrc && env" 输出 + 过滤 NOTION
    NOTION_API_TOKEN="$(bash -c '. "$HOME/.bashrc" 2>/dev/null; echo "${NOTION_API_TOKEN:-}"' 2>/dev/null)"
    export NOTION_API_TOKEN
  fi
  if [ -z "${NOTION_API_TOKEN:-}" ]; then
    echo "[env] FATAL: NOTION_API_TOKEN 未设置，~/.bashrc 第 2 行 export 没生效" >&2
    return 1
  fi
  # 确保 PATH 含 ~/.npm-global/bin（mmx / ntn）
  if [ -d "$HOME/.npm-global/bin" ]; then
    export PATH="$HOME/.npm-global/bin:$PATH"
  fi
  mkdir -p "$BRIEFING_TMP_DIR" "$BRIEFING_MEDIA_DIR" "$BRIEFING_LOG_DIR" 2>/dev/null || true
  return 0
}

# ----------------------------------------------------------------------------
# 计算日期 / 周数 / 年份 / 是否周一
# 输出（写到 stdout，4 行）：
#   YYYY-MM-DD
#   YYYY
#   Wnn           (ISO 周，e.g. W33)
#   <is_monday>   (0 / 1)
# 用法：briefing_compute_date
# ----------------------------------------------------------------------------
briefing_compute_date() {
  local today
  today="$(date '+%Y-%m-%d')"
  local year
  year="$(date '+%Y')"
  local iso_week
  iso_week="$(date '+%V')"
  # 周数补零到 2 位
  printf '%s\n%s\nW%02d\n' "$today" "$year" "$((10#$iso_week))"
  if [ "$(date '+%u')" = "1" ]; then
    echo "1"
  else
    echo "0"
  fi
}

# ----------------------------------------------------------------------------
# 文件大小验证（自检，2026-07-22 老板要求）
# 用法：briefing_assert_file_size <path> <min_bytes>
# ----------------------------------------------------------------------------
briefing_assert_file_size() {
  local f="$1"
  local min_bytes="${2:-100}"
  if [ ! -f "$f" ]; then
    echo "[assert] FAIL: 文件不存在 $f" >&2
    return 1
  fi
  local sz
  sz="$(stat -c %s "$f" 2>/dev/null || echo 0)"
  if [ "$sz" -lt "$min_bytes" ]; then
    echo "[assert] FAIL: $f 大小 ${sz}B < ${min_bytes}B" >&2
    return 1
  fi
  echo "[assert] OK: $f ${sz}B"
  return 0
}