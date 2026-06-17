# 图书直播预告监控

每天定时浏览 9 个达人的抖音/小红书主页，自动识别"直播预告时间 + 图书清单"，输出本地 HTML 报告。

---

## 你需要做的（首次配置，一次 10 分钟）

### 一、装 Python（如果没装）

打开 Microsoft Store，搜索 **Python 3.11**，点"获取"安装。

不能装 Python 的话，让管理员装一下。装好后在 PowerShell 输入 `python --version` 验证，应该看到 `Python 3.11.x`。

### 二、装依赖

打开 PowerShell，**进入这个目录**，执行：

```powershell
cd D:\jiangqianyi\Desktop\AI\books-dashboard\lab\livestream
pip install playwright pyyaml
playwright install chromium
```

等完成（大约 3 分钟，下载 Chromium 浏览器）。

### 三、首次登录

```powershell
python setup_login.py
```

脚本会：
1. 打开一个 Chrome 浏览器，跳转到抖音首页
2. 你**用手机扫码登录抖音**（建议用一个**不常用的小号**，避免主账号被风控）
3. 登录完按回车
4. 跳转到小红书 → 同样扫码登录小红书
5. 登录完按回车，关闭即可

之后登录信息保存在 `.chrome_userdata/` 目录，**不用每天再登录**。

### 四、试运行一次

```powershell
python -m main
```

跑 3-5 分钟，结束后会生成 `output/index.html`，双击打开就能看到报告。

### 五、设定每天自动跑（Windows 任务计划）

1. 按 `Win + S`，搜"任务计划程序"，打开
2. 右侧 → 点"创建基本任务"
3. 名称：`图书直播预告`
4. 触发器 → 选"每天"，时间填 `08:00`（早上 8 点你来公司前跑完）
5. 操作 → 选"启动程序"
6. 程序/脚本：`D:\jiangqianyi\Desktop\AI\books-dashboard\lab\livestream\daily_run.bat`
7. 起始于（可选）：`D:\jiangqianyi\Desktop\AI\books-dashboard\lab\livestream`
8. 完成

之后**电脑早上开机后就自动跑**，到点出报告。

---

## 每天怎么用

打开 `D:\jiangqianyi\Desktop\AI\books-dashboard\lab\livestream\output\index.html`

你会看到：

```
📅 直播预告
├─ [抖音] 宇辉同行
│   📅 06/18 19:30 (明天)
│   📕《人间小满 3》《我是你的遗物》
│   原文: "明晚 19:30"
├─ [小红书] 中信书店
│   📅 06/19 20:00 (后天)
│   📕《泉州寻宝记》
└─ ...

📚 含书名（未识别为直播预告）
（这里是有书名但没有直播线索的作品 — 可能是新书推荐 / 测评）

⚠️ 抓取失败
（哪个账号失败了，下次手动看一下）
```

---

## 常见问题

**Q：每天什么时候自动跑？**
A：你设定的任务计划时间。建议早上 8 点（电脑开机即跑）。

**Q：电脑没开机会跑吗？**
A：不会。任务计划只在电脑开机时执行。如果你早上 9 点开机，它会在开机后跑。

**Q：脚本会不会让我抖音/小红书账号被封？**
A：每天浏览 9 个公开主页 = 普通用户行为，被封概率极低。但建议用小号（不是你常用账号）做登录。

**Q：抓不到数据怎么办？**
A：看报告底部"⚠️ 抓取失败"列表。常见原因：
- 抖音/小红书的页面结构变了（找我修选择器）
- 你的登录 cookie 过期了 → 重新跑 `setup_login.py`
- 那个账号当天没发新作品 → 正常，不算失败

**Q：怎么换/加监控账号？**
A：编辑 `accounts.yaml`，加/改账号。修改后下次跑就生效。

**Q：要不要把数据同步给同事？**
A：现在是本地版（只有你能看）。等你试用一周觉得稳定了，告诉我，我帮你接到正式 books-dashboard 网页里给同事看。

---

## 文件结构

```
lab/livestream/
├─ accounts.yaml          # 账号配置（要监控谁）
├─ setup_login.py         # 首次登录脚本（只跑一次）
├─ main.py                # 每日抓取脚本
├─ daily_run.bat          # Windows 双击启动 / 任务计划用
├─ scrapers/
│   ├─ douyin.py          # 抖音抓取
│   ├─ xiaohongshu.py     # 小红书抓取
│   ├─ extractor.py       # 时间 + 书名解析
│   └─ render.py          # HTML 报告渲染
├─ data/
│   └─ snapshot_*.json    # 每天的原始数据
├─ output/
│   └─ index.html         # 当日 HTML 报告
└─ .chrome_userdata/      # 你的登录信息（不要删）
```
