#!/usr/bin/env bash
# log.sh — 统一日志样式
# 用法：
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/log.sh"
#   log_init "/tmp/briefing-write.log"   # 可选，默认 /tmp/briefing-write-<pid>.log
#   log_info "step 1 OK"
#   log_warn "deprecated field"
#   log_error "fetch failed"
#   log_section "Step 3: web_search"

LOG_FILE="${LOG_FILE:-/tmp/briefing-write-$$.log}"
LOG_STDOUT=1

log_init() {
  local f="${1:-/tmp/briefing-write-$$.log}"
  LOG_FILE="$f"
  : > "$LOG_FILE"
}

_log() {
  local level="$1"; shift
  local ts
  ts="$(date '+%Y-%m-%d %H:%M:%S %Z')"
  local msg="[$ts] [$level] $*"
  echo "$msg" >> "$LOG_FILE"
  if [ "${LOG_STDOUT:-1}" = "1" ]; then
    echo "$msg"
  fi
}

log_info()  { _log "INFO"  "$@"; }
log_warn()  { _log "WARN"  "$@" >&2; }
log_error() { _log "ERROR" "$@" >&2; }
log_section() {
  local title="$*"
  local sep
  sep="$(printf '=%.0s' $(seq 1 60))"
  _log "SECTION" "$sep $title $sep"
}

# 命令包装：跑一个命令并记录 exit code，但不让 set -e 触发
# 用法：run_or_die "step name" cmd args...
run_or_die() {
  local label="$1"; shift
  log_section "$label"
  "$@"
  local rc=$?
  if [ $rc -ne 0 ]; then
    log_error "$label 失败 exit=$rc"
    return $rc
  fi
  log_info "$label OK"
  return 0
}