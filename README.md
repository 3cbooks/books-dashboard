# 📚 图书趋势看板 · Books Trend Dashboard

> 行业专家视角的中国图书市场每日趋势看板。
> 抓取多源数据 → 自动聚合 → 增量机会洞察。

🌐 **在线访问**: https://jiang12481.github.io/books-dashboard/

## ✨ 特性

- **多源数据聚合**：当当 / 京东 / 豆瓣 / 微信读书 / 百度新闻 / 出版商务网 / Publishers Weekly
- **每日自动更新**：GitHub Actions 在北京时间凌晨 3:00 自动跑抓取脚本
- **增量机会洞察**：基于规则引擎自动生成 3-5 条短洞察（品类占比、环比、跨渠道差异等）
- **零成本部署**：纯静态站，托管在 GitHub Pages，全程免费

## 🏗 架构

```
GitHub Actions (cron)
    ↓ 每天 03:00 北京时间
Python 抓取脚本（scrapers/）
    ↓ 输出 JSON
data/*.json
    ↓ git auto-commit
GitHub Pages
    ↓
浏览器（Tailwind + Chart.js）
```

## 📁 目录结构

```
books-dashboard/
├── index.html              # 主页
├── assets/
│   ├── css/style.css       # 自定义样式
│   └── js/app.js           # 前端逻辑
├── data/                   # 抓取后的 JSON（脚本会覆写）
│   ├── meta.json           # 元数据 + 各源状态
│   ├── books.json          # 新书列表
│   ├── news.json           # 行业新闻
│   └── insights.json       # 自动生成的洞察
├── scrapers/               # Python 抓取脚本
│   ├── dangdang.py
│   ├── jd.py
│   ├── douban.py
│   ├── weread.py
│   ├── baidu_news.py
│   ├── cptoday.py
│   ├── publishers_weekly.py
│   ├── isbn_registry.py
│   ├── insights.py         # 规则引擎生成洞察
│   └── main.py             # 调度入口
└── .github/workflows/
    └── daily-scrape.yml    # 每日定时任务
```

## 🚀 本地预览

```bash
# 进入项目目录后，用 Python 起一个静态服务器
python -m http.server 8000
# 然后打开 http://localhost:8000
```

## 📅 更新计划

- [x] 项目骨架 + 假数据
- [ ] 当当新书榜抓取
- [ ] 百度新闻抓取
- [ ] GitHub Actions 配置
- [ ] 京东 / 豆瓣 / 微信读书
- [ ] 出版商务网 / Publishers Weekly
- [ ] ISBN 备案条目流
- [ ] 洞察规则引擎
- [ ] Chart.js 图表强化

## 📜 数据来源声明

本项目仅做合理范围内的公开页面信息聚合，遵守各站点 robots.txt，每日 1 次低频访问。
所有数据版权归原始来源所有。
