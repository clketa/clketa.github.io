#!/usr/bin/env python3
"""briefing-llm-call.py — LLM wrapper for daily-news-briefing

设计原则：
1. 输入：system prompt + user prompt + 一些选项
2. 输出：JSON 到 stdout，包含 LLM 原始响应 + 解析后的结构化字段
3. 内部调 `mmx text chat` 子进程（避免直接调 minimax API 需要管 API key）
4. 任何步骤失败返回非 0 + 错误 JSON 到 stderr

用法：
  briefing-llm-call.py \
    --system "你是 苒苒..." \
    --user "写今日简报..." \
    --output /tmp/llm-response.json

可选：
  --model MiniMax-M3       (默认 MiniMax-M3)
  --max-tokens 4096        (默认 8192，给简报留足空间)
  --temperature 0.7
  --search-files f1.jsonl f2.jsonl ...   (把搜索结果注入 prompt)
  --existing-briefings path/to/file.txt  (把本周已发简报注入 prompt 用于 dedup)

输出 JSON 格式：
{
  "ok": true,
  "model": "MiniMax-M3",
  "raw_text": "<LLM 原始输出 markdown>",
  "title": "yyyymmdd-<≤15字>",
  "body_markdown": "<完整 markdown 正文>",
  "summary_punchline": "<1 句 punchline>",
  "summary_domestic": "<国内主线 1-2 句>",
  "summary_international": "<国际主线 1-2 句>",
  "summary_market": "<市场与隐忧 1-2 句，可空>",
  "tomorrow_watch": ["<事件 1>", "<事件 2>", ...5条],
  "non_consensus_judgments": [
    {"judgment": "...", "logic": "..."},
    ...
  ],
  "duration_seconds": 12.3,
  "error": null
}
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


# ----------------------------------------------------------------------------
# 默认 system prompt
# ----------------------------------------------------------------------------
DEFAULT_SYSTEM_PROMPT = """你是「苒苒」，老板的私人 AI 助理。

【身份】
- 你的语气直接、有立场、敢下判断
- 用第一人称「我」，不用「AI 觉得」「作为一个 AI」
- 用「——」、列表、表格表达，不写废话

【今日简报输出格式 - 必须严格遵守】

输出 **纯 markdown**（不要 ```markdown``` 包裹，不要任何前言后语）。

第一行必须是 H1 标题，格式：`yyyymmdd-<≤15字简述>`。
- yyyymmdd 是今天日期
- 后面必须 ≤15 个汉字（中文字符），不允许英文逗号/破折号当字
- 不要写"今日简报""日报"等无意义前缀
- 标题示例：`20260815-GDP稳长鑫CPI同节点`

标题之后，第一段是元数据 quote 块：
```
> 编制：苒苒 · YYYY-MM-DD HH:MM GMT+8
> 立场：以上仅为编制者的解读，不构成投资或行动建议
> 信息源：文末统一附"原文链接"
```
（HH:MM 用真实生成时刻）

然后一条 `---` 分隔线。

分隔线之后、第一个 ## 之前，**插入**一个 callout 摘要块（这个 callout 在 Notion 里单独前置到页面顶部，所以 markdown 里也要放一份）：
```
> 💡 **苒苒一句话：**<1 句 punchline，1 句话尖锐判断，不是摘要的摘要>
```

然后 4 个 bullet：
```
- **国内主线：**<1-2 句话>
- **国际主线：**<1-2 句话>
- **市场与隐忧：**<1-2 句话，可空就空>
- **明日值得关注：**<5 条具体事件>
```

接下来是正文：
```
## 🇨🇳 国内要闻

### 1. <故事标题>
**发生了什么**
- 3-5 个 bullet 关键事实

**苒苒按**
- 2-3 句个人解读

(2-4 个国内故事)

## 🌍 国际要闻

### 1. <故事标题>
**发生了什么**
- 3-5 个 bullet

**背景** (可选)
- 1-3 个 bullet

**苒苒按**
- 2-3 句个人解读

(2-4 个国际故事)
```

如果当天有强判断空间，追加：
```
## 📌 苒苒今日 5 个非共识判断

| # | 判断 | 逻辑 |
|---|---|---|
| 1 | ... | ... |
| 2 | ... | ... |
| ... | ... | ... |
```

如果今天是周一（周一才加，否则整段删掉）：
```
## 📅 本周关注日历

| 日期 | 事件 |
|---|---|
| 周X (今天) | ... |
| 周X | ... |
| ... | ... |
```

最后：
```
## 📎 原文链接

**国内**
- <故事1>：[来源名](URL) · [来源名](URL)
- ...

**国际**
- <故事1>：[来源名](URL) · [来源名](URL)
- ...

---

*本简报由 苒苒（AI 私人助理）编制，仅供老板私人参考。*
```

【dedup 规则】
- 已注入 "本周已发简报" 列表，**禁止**重复已覆盖的核心话题
- 如果是同一事件的后续进展，标题里加「后续：」并简述

【输出末尾必须追加】
在 markdown 末尾的 `---` 之后，加一段 JSON 块（方便解析），格式：

```json
{
  "title": "yyyymmdd-<≤15字>",
  "punchline": "1 句 punchline（callout 文，不要重复标题）",
  "domestic_main": "国内主线 1-2 句",
  "international_main": "国际主线 1-2 句",
  "market_main": "市场与隐忧 1-2 句，可空字符串",
  "tomorrow_watch": ["事件 1", "事件 2", "事件 3", "事件 4", "事件 5"]
}
```
"""


# ----------------------------------------------------------------------------
# 调 mmx text chat 子进程
# ----------------------------------------------------------------------------
def call_mmx_text(model: str, system: str, user_msg: str, max_tokens: int, temperature: float) -> str:
    cmd = [
        "mmx", "text", "chat",
        "--model", model,
        "--message", user_msg,
        "--system", system,
        "--max-tokens", str(max_tokens),
        "--temperature", str(temperature),
        "--output", "text",
        "--quiet",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 分钟上限
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"mmx text chat 超时（>{600}s）") from e
    except FileNotFoundError as e:
        raise RuntimeError("mmx CLI 未找到（PATH 没 ~/.npm-global/bin）") from e
    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip()
        raise RuntimeError(f"mmx text chat 失败 exit={proc.returncode}: {err[:500]}")
    return proc.stdout


# ----------------------------------------------------------------------------
# 解析末尾 JSON 块
# ----------------------------------------------------------------------------
JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)
TITLE_RE = re.compile(r"^#\s+(\d{8}-.{1,30})", re.MULTILINE)


def parse_structured_fields(raw_text: str) -> dict:
    """从 LLM 输出末尾的 ```json 块提取结构化字段"""
    result = {
        "title": "",
        "punchline": "",
        "domestic_main": "",
        "international_main": "",
        "market_main": "",
        "tomorrow_watch": [],
    }
    # 找末尾 json 块
    matches = JSON_BLOCK_RE.findall(raw_text)
    if not matches:
        # fallback: 从 H1 提取 title
        m = TITLE_RE.search(raw_text)
        if m:
            result["title"] = m.group(1).strip()
        return result
    # 取最后一个 json block（结构化数据块在末尾）
    try:
        data = json.loads(matches[-1])
        for k in result.keys():
            if k in data:
                result[k] = data[k]
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        # fallback: 解析失败仍用 H1
        m = TITLE_RE.search(raw_text)
        if m:
            result["title"] = m.group(1).strip()
    if not result["title"]:
        m = TITLE_RE.search(raw_text)
        if m:
            result["title"] = m.group(1).strip()
    return result


# ----------------------------------------------------------------------------
# 构造 user prompt
# ----------------------------------------------------------------------------
def build_user_prompt(args, parsed_search_results: str, existing_briefings: str) -> str:
    parts = []
    if args.user:
        parts.append(args.user.strip())
    if parsed_search_results:
        parts.append("\n\n【4 路 web_search 结果汇总（JSONL）】\n" + parsed_search_results)
    if existing_briefings:
        parts.append("\n\n【本周已发简报（用于 dedup，列出每篇覆盖的 top story）】\n" + existing_briefings)
    parts.append("\n\n今天是 " + time.strftime("%Y-%m-%d %A") + "（Asia/Shanghai）")
    parts.append("\n请严格按 system prompt 格式输出 markdown，末尾带 ```json 结构化字段块。")
    return "\n".join(parts)


# ----------------------------------------------------------------------------
# 读 JSONL 搜索结果
# ----------------------------------------------------------------------------
def read_search_files(paths):
    out = []
    for p in paths:
        path = Path(p)
        if not path.exists():
            continue
        out.append(f"\n--- {path.name} ---")
        try:
            with open(path, "r", encoding="utf-8") as f:
                # 限制每文件大小 100KB，避免 token 爆炸
                content = f.read(100 * 1024)
                out.append(content)
        except Exception as e:
            out.append(f"(read error: {e})")
    return "\n".join(out)


# ----------------------------------------------------------------------------
# 读本周已发简报
# ----------------------------------------------------------------------------
def read_existing_briefings(path):
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")[:50 * 1024]


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Briefing LLM call wrapper")
    parser.add_argument("--system", default=DEFAULT_SYSTEM_PROMPT)
    parser.add_argument("--user", default="请写今天的简报。")
    parser.add_argument("--model", default=os.environ.get("BRIEFING_LLM_MODEL", "MiniMax-M3"))
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--search-files", nargs="*", default=[])
    parser.add_argument("--existing-briefings", default="")
    parser.add_argument("--output", default="-", help="输出 JSON 文件路径，- 表示 stdout")
    args = parser.parse_args()

    t0 = time.time()
    parsed_search = read_search_files(args.search_files)
    existing_brief = read_existing_briefings(args.existing_briefings)
    user_prompt = build_user_prompt(args, parsed_search, existing_brief)

    out = {
        "ok": False,
        "model": args.model,
        "raw_text": "",
        "title": "",
        "body_markdown": "",
        "summary_punchline": "",
        "summary_domestic": "",
        "summary_international": "",
        "summary_market": "",
        "tomorrow_watch": [],
        "duration_seconds": 0.0,
        "error": None,
    }
    try:
        raw = call_mmx_text(args.model, args.system, user_prompt, args.max_tokens, args.temperature)
    except Exception as e:
        out["error"] = str(e)
        out["duration_seconds"] = time.time() - t0
        emit(out, args.output)
        return 1

    fields = parse_structured_fields(raw)
    out["ok"] = True
    out["raw_text"] = raw
    out["title"] = fields["title"]
    out["body_markdown"] = raw
    out["summary_punchline"] = fields["punchline"]
    out["summary_domestic"] = fields["domestic_main"]
    out["summary_international"] = fields["international_main"]
    out["summary_market"] = fields["market_main"]
    out["tomorrow_watch"] = fields["tomorrow_watch"]
    out["duration_seconds"] = time.time() - t0
    emit(out, args.output)
    return 0


def emit(out: dict, target: str):
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if target == "-":
        print(text)
    else:
        Path(target).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())