"""
直播预告解析器

输入: 一段作品标题 + 描述
输出: {
  has_preview: bool,
  scheduled_at: ISO datetime str | None,
  scheduled_text: str (原文匹配片段),
  books: list[str] (识别到的书名),
  raw_books: list[str] (含书名号的原文),
}

支持的时间表达:
  - "今晚 8 点" / "今晚 8:00" / "今晚八点"
  - "明晚 7:30" / "明天晚上 7 点"
  - "今天下午 3 点"
  - "周三 20:00" / "周日晚 7 点"
  - "6/19 直播" / "6 月 19 日"
  - "3 月 10 日 19:30"
  - "1219" (常见简写)
  - "今晚直播" / "今天直播" (无具体时间，只标"今天")

支持的图书表达:
  - 《书名》《书名 2》（中文书名号）
  - 《书名》（含字母数字标点）
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))

# 中文数字 → 阿拉伯
CN_NUM = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "十一": 11, "十二": 12, "十三": 13, "十四": 14, "十五": 15,
    "十六": 16, "十七": 17, "十八": 18, "十九": 19, "二十": 20,
    "二十一": 21, "二十二": 22, "二十三": 23, "二十四": 24,
}

# 周几映射
WEEKDAYS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}

# 时段判断（用于"晚 8 点"）
def _hour_with_period(hour: int, period: str) -> int:
    """
    period: 上午/下午/晚上/晚/凌晨/中午/早上 等
    返回 24 小时制
    """
    if not period:
        return hour
    if period in ("下午", "晚上", "晚", "傍晚"):
        return hour if hour >= 12 else hour + 12
    if period in ("上午", "早上", "凌晨", "清晨"):
        return hour if hour < 12 else hour - 12
    if period == "中午":
        return 12 if hour == 12 else 12 + (hour % 12)
    return hour


def _cn_to_int(text: str) -> int | None:
    """把'八'/'十二'/'二十'转成数字"""
    if text.isdigit():
        return int(text)
    return CN_NUM.get(text.strip())


def _resolve_relative_day(token: str, now: datetime) -> datetime | None:
    """今/明/后/大后 → 具体日期（保留时间为 0:00）"""
    if "今" in token:
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if "明" in token:
        return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    if "后天" in token:
        return (now + timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
    if "大后天" in token:
        return (now + timedelta(days=3)).replace(hour=0, minute=0, second=0, microsecond=0)
    return None


def _resolve_weekday(weekday_char: str, now: datetime) -> datetime:
    """周X → 最近的下一个该工作日（含今天则取今天）"""
    target = WEEKDAYS.get(weekday_char)
    if target is None:
        return now
    diff = (target - now.weekday()) % 7
    return (now + timedelta(days=diff)).replace(hour=0, minute=0, second=0, microsecond=0)


# ========== 时间提取 ==========

# 模式 A: 相对天数 + 时段 + 时间
#   "今晚 8 点 / 今天晚上 8:30 / 明晚七点 / 今天下午 3 点 / 今晚 20:00"
PAT_RELATIVE_HOUR = re.compile(
    r"(今天|今晚|今夜|明天|明晚|明夜|后天|后天晚上|大后天)"
    r"\s*(上午|下午|晚上|晚|早上|凌晨|傍晚|中午)?"
    r"\s*(\d{1,2}|[一二三四五六七八九十]{1,3})"
    r"\s*[:：点时](?:\s*(\d{1,2}|半))?"
)

# 模式 B: 周X + 时段 + 时间
#   "周三 20:00 / 周日晚 7 点 / 周五 19:30"
PAT_WEEKDAY = re.compile(
    r"(?:周|星期)([一二三四五六日天])"
    r"\s*(上午|下午|晚上|晚|早上|傍晚|中午)?"
    r"\s*(\d{1,2})"
    r"\s*[:：点时](?:\s*(\d{1,2}|半))?"
)

# 模式 C: 月/日 + 时间
#   "6/19 19:30 / 6 月 19 日晚 8 点 / 12-19 20:00"
PAT_MONTH_DAY = re.compile(
    r"(\d{1,2})\s*[/月\-]\s*(\d{1,2})\s*[日号]?"
    r"(?:\s*(上午|下午|晚上|晚|早上|傍晚|中午)?)"
    r"\s*(?:(\d{1,2})\s*[:：点时](?:\s*(\d{1,2}|半))?)?"
)

# 模式 D: 仅"直播"+ 相对天 (无时间)
#   "今晚直播 / 今天直播预告 / 明天开播"
PAT_DAY_ONLY = re.compile(
    r"(今天|今晚|今夜|明天|明晚|明夜|后天|大后天)"
    r"[^，,。！!\n]{0,8}(直播|开播|上播|播)"
)


def _extract_time(text: str, now: datetime) -> tuple[datetime | None, str] | tuple[None, None]:
    """
    从文本里挑一个最具体的时间。
    返回 (resolved_datetime, matched_text) 或 (None, None)
    """
    # 优先级：A > C > B > D（具体时间 > 月日 > 周X > 仅日期）
    for pat, kind in [(PAT_RELATIVE_HOUR, "rel"),
                      (PAT_MONTH_DAY, "md"),
                      (PAT_WEEKDAY, "wd"),
                      (PAT_DAY_ONLY, "day")]:
        m = pat.search(text)
        if not m:
            continue
        try:
            if kind == "rel":
                day_word, period, hour_str, min_str = m.groups()
                base = _resolve_relative_day(day_word, now)
                if not base:
                    continue
                h = _cn_to_int(hour_str)
                if h is None:
                    continue
                # 没明确时段时，"X 点"小于 12 默认晚上
                if not period and h <= 11:
                    period = "晚上"
                h = _hour_with_period(h, period or "")
                minute = 30 if min_str == "半" else (int(min_str) if min_str else 0)
                return base.replace(hour=h, minute=minute), m.group(0).strip()
            elif kind == "md":
                month, day, period, hour_str, min_str = m.groups()
                year = now.year
                # 月份太小 -> 可能是明年
                month_i, day_i = int(month), int(day)
                candidate = datetime(year, month_i, day_i, tzinfo=TZ)
                # 如果已经过去超过 7 天 → 视作明年
                if (now - candidate).days > 30:
                    candidate = candidate.replace(year=year + 1)
                if hour_str:
                    h = int(hour_str)
                    if not period and h <= 11:
                        period = "晚上"
                    h = _hour_with_period(h, period or "")
                    minute = 30 if min_str == "半" else (int(min_str) if min_str else 0)
                    candidate = candidate.replace(hour=h, minute=minute)
                return candidate, m.group(0).strip()
            elif kind == "wd":
                weekday_char, period, hour_str, min_str = m.groups()
                base = _resolve_weekday(weekday_char, now)
                h = int(hour_str)
                if not period and h <= 11:
                    period = "晚上"
                h = _hour_with_period(h, period or "")
                minute = 30 if min_str == "半" else (int(min_str) if min_str else 0)
                return base.replace(hour=h, minute=minute), m.group(0).strip()
            elif kind == "day":
                day_word, _ = m.groups()
                base = _resolve_relative_day(day_word, now)
                if base:
                    # 默认晚 8 点（直播默认）
                    return base.replace(hour=20), m.group(0).strip()
        except (ValueError, KeyError, TypeError):
            continue
    return None, None


# ========== 图书提取 ==========

# 中文书名号《》匹配
PAT_BOOK = re.compile(r"《([^《》\n]{1,40})》")


def _extract_books(text: str) -> tuple[list[str], list[str]]:
    """
    从文本里识别书名《XXX》
    返回 (清洗后的书名列表, 含书名号的原文列表)
    """
    raw = PAT_BOOK.findall(text)
    raw = [b.strip() for b in raw if b.strip()]
    # 简单去重保序
    seen = set()
    cleaned = []
    for b in raw:
        if b not in seen:
            seen.add(b)
            cleaned.append(b)
    return cleaned, [f"《{b}》" for b in cleaned]


# ========== 直播关键词检测 ==========

LIVESTREAM_KEYWORDS = (
    "直播", "开播", "上播", "播一场", "晚播", "今晚播", "今晚直播",
    "直播预告", "开播预告", "预告 直播", "直播上链",
    "直播间见", "直播间见面", "直播下单",
    "锁定直播间", "蹲直播", "等直播", "上直播",
)

# 否定语境：含这些词时通常不是预告
NEGATION_KEYWORDS = (
    "无直播", "不直播", "非直播", "停播", "暂停直播",
)

# 过去式语境：含这些词时直播已发生（不是预告）
PAST_KEYWORDS = (
    "上周直播", "上次直播", "昨天直播", "昨晚直播",
    "直播复盘", "复盘", "回顾", "回放",
    "已播", "已开播",
)


def _has_livestream_keyword(text: str) -> bool:
    return any(k in text for k in LIVESTREAM_KEYWORDS)


def _has_negation(text: str) -> bool:
    return any(k in text for k in NEGATION_KEYWORDS)


def _has_past_context(text: str) -> bool:
    return any(k in text for k in PAST_KEYWORDS)


# ========== 主入口 ==========

def parse_livestream(text: str, published_at: str | None = None) -> dict:
    """
    主入口：从文本（标题 + 描述）里解析直播预告

    返回:
    {
      has_preview:   bool,
      scheduled_at:  ISO 字符串 | None,
      scheduled_text: 原文片段,
      books:         ["书名 1", "书名 2"],
      raw_books:     ["《书名 1》", "《书名 2》"],
      keywords_hit:  bool (是否含直播关键词),
    }
    """
    now = datetime.now(TZ)
    text = text or ""

    keywords_hit = _has_livestream_keyword(text)
    negation = _has_negation(text)
    past_context = _has_past_context(text)
    scheduled_at, scheduled_text = _extract_time(text, now)
    books, raw_books = _extract_books(text)

    # 判定: 满足任一即视为有预告（除非含否定/过去式）
    #  1) 含直播关键词 + 含书名 → 大概率是
    #  2) 解析出未来时间 + 含书名 → 大概率是
    #  3) 含直播关键词 + 解析出未来时间 → 大概率是
    has_preview = bool(
        (keywords_hit and books) or
        (scheduled_at and books) or
        (keywords_hit and scheduled_at)
    )
    # 否定/过去式 — 直接否决
    if negation or past_context:
        has_preview = False
    # 已过去的时间不算预告（但有可能是周X匹配到本周已过去的某天）
    if scheduled_at and scheduled_at < now - timedelta(hours=2):
        if not (keywords_hit and books):
            has_preview = False

    return {
        "has_preview": has_preview,
        "scheduled_at": scheduled_at.isoformat(timespec="minutes") if scheduled_at else None,
        "scheduled_text": scheduled_text,
        "books": books,
        "raw_books": raw_books,
        "keywords_hit": keywords_hit,
    }


# ========== 自测 ==========

if __name__ == "__main__":
    samples = [
        "今晚 8 点直播，《白鹿原》《活着》必抢，链接见简介",
        "明晚 19:30 大场预告 《人间小满 3》《我是你的遗物》",
        "周三晚 8 点直播间见 — 中信新书《XXX》",
        "6/19 19:30 童书专场 《泉州寻宝记》《大中华寻宝记》",
        "今天直播预告：《泥潭》签名版限量",
        "新书推荐《真实之书》（无直播）",
        "上周直播复盘：《白鹿原》卖了 3000 本",
    ]
    for s in samples:
        r = parse_livestream(s)
        print(f"{s!r}")
        print(f"  → {r}")
        print()
