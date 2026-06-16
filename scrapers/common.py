"""
公共工具模块
- 统一的 HTTP 请求函数（带重试、UA、超时）
- 统一的 JSON 读写
- 统一的分类映射 / 时间格式
"""
from __future__ import annotations

import json
import logging
import random
import time
from pathlib import Path
from typing import Any

import requests

# ============================================================
# 路径
# ============================================================

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# ============================================================
# 日志
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# ============================================================
# HTTP 请求（带重试、UA 池、礼貌延迟）
# ============================================================

# 几个常见的真实浏览器 User-Agent — 随机选一个，降低被反爬命中的概率
_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]


def http_get(
    url: str,
    *,
    params: dict | None = None,
    timeout: int = 15,
    max_retries: int = 3,
    encoding: str | None = None,
    extra_headers: dict | None = None,
) -> requests.Response | None:
    """
    带重试、UA 轮换、礼貌延迟的 GET 请求。
    失败时返回 None（不抛异常 — 让上游决定怎么 fallback）。
    """
    headers = {
        "User-Agent": random.choice(_UA_POOL),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    }
    if extra_headers:
        headers.update(extra_headers)

    log = get_logger("http")
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            if encoding:
                resp.encoding = encoding
            elif resp.encoding == "ISO-8859-1":  # requests 误判常见
                resp.encoding = resp.apparent_encoding

            if resp.status_code == 200:
                return resp

            log.warning("[%d/%d] %s → HTTP %d", attempt, max_retries, url, resp.status_code)
        except requests.RequestException as e:
            log.warning("[%d/%d] %s → %s", attempt, max_retries, url, e)

        # 指数退避 + 抖动
        time.sleep(2 ** attempt + random.random())

    log.error("✗ 放弃: %s", url)
    return None


# ============================================================
# JSON 读写（用于读取上次的兜底数据 / 写入最新数据）
# ============================================================

def load_json(name: str, default: Any = None) -> Any:
    """从 data/ 目录读 JSON，文件不存在或解析失败时返回 default。"""
    path = DATA_DIR / name
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        get_logger("io").error("✗ JSON 解析失败: %s", path)
        return default


def save_json(name: str, data: Any) -> None:
    """写入 data/ 目录，UTF-8、缩进 2、不转义中文。"""
    path = DATA_DIR / name
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    get_logger("io").info("→ %s (%d 条)", path.name,
                          len(data) if isinstance(data, list) else 1)


# ============================================================
# 分类映射（把各源的原始分类标准化成网站的 7 类）
# ============================================================

CATEGORY_RULES = [
    ("小说",   ("小说", "fiction")),
    ("文学",   ("文学", "诗歌", "散文", "随笔")),
    ("童书",   ("童书", "少儿", "绘本", "儿童")),
    ("经管",   ("经管", "经济", "管理", "财经", "金融", "商业", "投资")),
    ("科技",   ("科技", "计算机", "互联网", "AI", "人工智能", "科普")),
    ("人文",   ("历史", "传记", "哲学", "宗教", "人文", "文化")),
    ("社科",   ("社科", "社会", "政治", "法律", "教育", "心理")),
]


def normalize_category(raw: str | None) -> str:
    """把任意原始分类字符串映射到网站的 7 类。"""
    if not raw:
        return "其他"
    raw = str(raw)
    for std, keywords in CATEGORY_RULES:
        if any(k in raw for k in keywords):
            return std
    return "其他"


# ============================================================
# 时间格式
# ============================================================

def now_iso(tz_offset_hours: int = 8) -> str:
    """北京时间的 ISO8601（带 +08:00 时区）。"""
    from datetime import datetime, timezone, timedelta
    tz = timezone(timedelta(hours=tz_offset_hours))
    return datetime.now(tz).isoformat(timespec="seconds")
