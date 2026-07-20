#!/usr/bin/env bash
# cron-deploy.sh — 简报自动部署脚本（无 LLM，完全绕开 minimax overload）
# 替代 OpenClaw cron `daily-briefing-noon`，避免 12:00-13:30 LLM 容量高峰翻车
# 用法：
#   1. crontab -e 添加：30 13 * * * bash /home/ubuntu/projects/briefing-site/scripts/cron-deploy.sh >> /tmp/briefing-deploy.log 2>&1
#   2. chmod +x /home/ubuntu/projects/briefing-site/scripts/cron-deploy.sh

set -u  # 不要 set -e / set -o pipefail — 自己控制每个步骤的退出码
cd "$(dirname "$0")/.." || { echo "FATAL: cannot cd to briefing-site root"; exit 1; }

LOG=/tmp/briefing-deploy.log
exec > "$LOG" 2>&1

echo ""
echo "=== Briefing Deploy Start: $(date '+%Y-%m-%d %H:%M:%S %Z') ==="

# 0. 加载环境（NOTION_API_TOKEN 等）
if [ -f ~/.bashrc ]; then
  source ~/.bashrc
fi

# 1. fetch_notion (带重试 3 次)
success=0
for i in 1 2 3; do
  python3 scripts/fetch_notion.py > /tmp/fetch_notion.log 2>&1
  rc=$?
  tail -10 /tmp/fetch_notion.log
  if [ $rc -eq 0 ]; then
    success=1; break
  fi
  echo "[retry $i/3] fetch_notion failed (rc=$rc), sleeping 30s..."
  sleep 30
done
rm -f /tmp/fetch_notion.log
if [ $success -ne 1 ]; then
  echo "FATAL: fetch_notion 失败 3 次"
  exit 10
fi
echo "fetch_notion OK"

# 2. hugo build (带重试 3 次)
success=0
for i in 1 2 3; do
  hugo --gc --minify > /tmp/hugo.log 2>&1
  rc=$?
  tail -8 /tmp/hugo.log
  if [ $rc -eq 0 ]; then
    success=1; break
  fi
  echo "[retry $i/3] hugo build failed (rc=$rc), sleeping 30s..."
  sleep 30
done
rm -f /tmp/hugo.log
if [ $success -ne 1 ]; then
  echo "FATAL: hugo build 失败 3 次"
  exit 11
fi
echo "hugo build OK"

# 3. git add + check changes
git add -A
changes=$(git status --short | wc -l)
echo "Changes: $changes"

if [ "$changes" -gt 0 ]; then
  # 4. commit
  git commit -m "chore: auto-update briefings $(date +%Y-%m-%d)" > /tmp/commit.log 2>&1
  rc=$?
  cat /tmp/commit.log
  rm -f /tmp/commit.log
  if [ $rc -ne 0 ]; then
    echo "FATAL: git commit failed (rc=$rc)"
    exit 13
  fi
  echo "commit OK"

  # 5. push (带重试 3 次)
  success=0
  for i in 1 2 3; do
    git push origin main > /tmp/push.log 2>&1
    rc=$?
    cat /tmp/push.log
    if [ $rc -eq 0 ]; then
      success=1; break
    fi
    echo "[retry $i/3] git push failed (rc=$rc), sleeping 30s..."
    sleep 30
  done
  rm -f /tmp/push.log
  if [ $success -ne 1 ]; then
    echo "FATAL: git push 失败 3 次"
    exit 12
  fi
  echo "push OK"
else
  echo "No changes today (skip commit/push)"
fi

# 6. deploy-verify (注意: Hugo 默认 lowercase URL slug)
TODAY_PREFIX=$(date +%Y%m%d)
TODAY_FILE=$(ls content/post/ 2>/dev/null | grep "^${TODAY_PREFIX}-" | head -1 | sed "s/\.md$//")
if [ -n "$TODAY_FILE" ]; then
  # Hugo lowercase: LPR -> lpr
  SLUG_LOWER=$(echo "$TODAY_FILE" | tr 'A-Z' 'a-z')
  SUFFIX=${SLUG_LOWER#${TODAY_PREFIX}-}
  ENCODED=$(python3 -c "import urllib.parse; print(urllib.parse.quote('post/${TODAY_PREFIX}-${SUFFIX}'))")
  echo "[deploy-verify] Trying https://articles.stario.ltd/${ENCODED}/"
  for i in 1 2 3; do
    STATUS=$(curl -sI "https://articles.stario.ltd/${ENCODED}/" | head -1)
    echo "[deploy-verify $i] $STATUS"
    if echo "$STATUS" | grep -q "200"; then
      echo "deploy OK (HTTP 200)"
      break
    fi
    if [ $i -lt 3 ]; then sleep 30; fi
  done
else
  echo "[deploy-verify] No today file (upstream Notion 今日尚未发布, 正常跳过)"
fi

# 7. git log
git log --oneline -5

echo "=== Briefing Deploy Done: $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
exit 0
