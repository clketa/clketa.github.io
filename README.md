# 苒苒简报站

每日资讯简报，自动从 Notion 同步，Hugo 渲染，GitHub Pages 部署。

线上地址：<https://clketa.github.io/briefing-site/>

## 本地预览

```bash
hugo server -D
```

打开 <http://localhost:1313/briefing-site/>

## 同步 Notion 内容

```bash
python3 scripts/fetch_notion.py
```

会从 Notion "起居注 / 📑 资讯 / 📅 2026 / 🗓 Wnn" 下抓所有 child_page，导出为 Hugo markdown。

## 部署

push main 分支 → GitHub Actions 自动 build + deploy。

## GitHub Secrets

需要 repo 设置 → Secrets and variables → Actions 添加：

- `NOTION_API_TOKEN`: Notion integration token

## 工程结构

```
briefing-site/
├── content/post/           # Hugo 文章（自动从 Notion 同步）
├── scripts/fetch_notion.py # Notion → Markdown 抓取
├── themes/PaperMod/        # PaperMod 主题（git submodule）
├── .github/workflows/      # GitHub Actions
└── hugo.toml               # Hugo 配置
```
