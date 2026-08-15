# briefing-write.sh — 苒苒日简报脚本化（2026-08-15 重构）

把原本 LLM-driven 的 daily-news-briefing skill 重构成纯 shell 脚本 + Python helpers。
**LLM 不再写任何 exec 命令**——所有 chain 都在脚本里。

## 文件清单

```
scripts/
├── briefing-write.sh          # 主 orchestrator（cron argv 直跑这个）
├── briefing-llm-call.py       # LLM 包装（mmx text chat + JSON 解析）
├── briefing-build-summary.py  # Notion 📋 今日摘要 blocks JSON 构造
├── cron-deploy.sh             # 老的 12:00 部署脚本（不动）
├── fetch_notion.py            # 老的 Notion→Hugo 抓取（不动）
├── cron-snapshots/            # 当前 cron config 快照（每周对比用）
└── lib/
    ├── env.sh                 # env 加载 + 日期/周数计算
    ├── log.sh                 # log_info/warn/error/section
    └── notion.sh              # Notion API curl helpers
```

## 怎么跑

### cron 自动跑（默认）

- 11:30 Asia/Shanghai → `bash /home/ubuntu/projects/briefing-site/scripts/briefing-write.sh`
- 11:45 fallback → 同上
- 失败告警自动推到 QQBot

### 手动跑

```bash
# 干跑：跳过 LLM / image / Notion 写，只验证脚本逻辑
bash /home/ubuntu/projects/briefing-site/scripts/briefing-write.sh --dry-run

# 跳过语音
bash .../briefing-write.sh --skip-voice

# 跳过封面图（Notion page 不会 set cover）
bash .../briefing-write.sh --skip-cover

# 跑指定日期（默认今天）
bash .../briefing-write.sh --date 2026-08-15

# 完整跑（cron 模式）
bash /home/ubuntu/projects/briefing-site/scripts/briefing-write.sh
```

## 怎么 debug

### 看日志

```bash
# 每次跑一份 log（PID 后缀）
ls -lt /tmp/briefing-write-*.log | head -3
tail -100 /tmp/briefing-write-$$.log
```

### 看运行产物

```bash
# LLM 输出 JSON（title + body + summary）
cat ~/.openclaw/workspace/.briefing-tmp/llm-YYYYMMDD.json

# markdown body（最终送 Notion 的内容）
cat ~/.openclaw/workspace/.briefing-tmp/briefing-YYYYMMDD.md

# 4 路 search JSONL
ls ~/.openclaw/workspace/.briefing-tmp/search-YYYYMMDD/

# 语音文本 + 最终报告 JSON
cat ~/.openclaw/workspace/.briefing-tmp/briefing-YYYYMMDD-voice.txt
cat ~/.openclaw/workspace/.briefing-tmp/briefing-report-YYYYMMDD.json

# 关键路径 env（debug 时方便引用）
cat /tmp/briefing-last-run.env
```

### 看 cron config

```bash
openclaw cron get 00697af5-e096-40ea-a9d3-c9729d6d260f  # write-1130
openclaw cron get 96be92cc-d750-4baf-8d82-da83a8702004  # write-1145-fallback
```

或对比 `scripts/cron-snapshots/` 下的快照。

### 退出码含义

| code | 含义 |
|---|---|
| 0  | 全部成功 |
| 1  | 通用错误（参数解析等） |
| 2  | NOTION_API_TOKEN 缺失 |
| 3  | Notion 路径解析失败（起居注/资讯/📅 YYYY/🗓 Wnn 创建失败） |
| 4  | 4 路 web_search 全部失败 |
| 5  | LLM 调用失败 |
| 6  | 封面图生成失败 |
| 7  | 封面图上传 Notion 失败 |
| 8  | Notion 页面创建失败 |
| 9  | 📋 今日摘要 block 插入失败 |
| 10 | cover / icon 设置失败 |
| 11 | 语音生成失败（不致命，记 warn） |
| 12 | 本日简报已存在（脚本自动跳过写稿，只发语音确认） |

## 关键约束（2026-08-15 老板拍板）

1. **脚本内 `&&` / `;` / `|` chain 是 OK 的**——5 铁律只约束 LLM 写的 exec 命令
2. **每个步骤独立 try 错误处理 + 显式 exit code**——不依赖 LLM 重试机制
3. **fallbacks 永远 None**——绝不回 phantom model `minimax/MiniMax-1`
4. **image_generate 完成后才能 write 后续**——脚本里 mmx image generate 同步等结果再继续（不并发抢 token）
5. **失败告警 exit code 自动通过 cron runner 推到 QQBot**——failureAlert.after=1

## 设计变更点

- **之前**：cron agentTurn → LLM 读 skill → LLM 写 exec 链（5 铁律 LLM 误读）
- **之后**：cron command → bash script → 固定步骤 chain（每步独立 try/catch）

LLM 唯一还在的地方：`briefing-llm-call.py` 内部调 `mmx text chat` 子进程。
LLM 本身被锁在固定 prompt + 固定 max-tokens 里，不会产生"自作主张"的 exec。