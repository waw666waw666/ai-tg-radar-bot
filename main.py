import os
import time
import json
import hashlib
import re
from pathlib import Path
from difflib import SequenceMatcher
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

import requests
import feedparser


DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

MODELSCOPE_API_KEY = os.environ.get("MODELSCOPE_API_KEY", "")
MODELSCOPE_BASE_URL = os.environ.get("MODELSCOPE_BASE_URL", "https://api-inference.modelscope.cn/v1")
MODELSCOPE_MODEL = os.environ.get("MODELSCOPE_MODEL", "")

FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")

SEEN_FILE = Path("seen.json")


# =========================
# 运行说明
# =========================
# main.py 不负责控制运行频率。
# 运行频率由 cron-job.org 控制。
#
# 你当前推荐 cron-job.org 表达式：
# 0 0,2,4-23 * * *
#
# 含义：
# 00:00、02:00、04:00 执行
# 05:00～23:00 每小时执行
#
# cron-job.org 时区必须是：
# Asia/Shanghai


# =========================
# 推送策略：过去 1 小时热点 + 中高质量 + 不刷屏
# =========================
HIGH_QUALITY_ONLY = True

HOT_WINDOW_HOURS = 1
OLD_ITEM_GRACE_HOURS = 2

MIN_RULE_SCORE = 56
MIN_PUSH_SCORE = 70
MIN_SINGLE_REPORT_SCORE = 70
MIN_GENERAL_AI_SCORE = 78
PREFERRED_ALLOW_JUDGE_SCORE = 65

SOFT_SEND_LIMIT = 3
HIGH_SCORE_SEND_BYPASS = 88
HARD_SEND_LIMIT = 8

MAX_JUDGE_COUNT = 28

# 重要：不要只抓 10 / 15 条，否则你看到的永远都是几分钟前。
# RSS 本身不是全站数据库，但这里会尽量读取 RSS 当前返回的更多条目。
MAX_ENTRIES_PER_FEED = 80

SEND_EMPTY_HEARTBEAT = False

# =========================
# 模型调用策略：ModelScope 优先，DeepSeek 兜底
# =========================
MODEL_REQUEST_INTERVAL_SECONDS = 2
MODEL_MAX_RETRIES = 3
MODEL_TIMEOUT_SECONDS = 75
MODEL_BACKOFF_BASE_SECONDS = 4

MERGE_SIMILAR_EVENTS = True
MERGE_SIMILARITY_THRESHOLD = 0.76
MAX_RELATED_UPDATES_IN_CARD = 5


# =========================
# 分数上限：避免单点反馈全是 100
# =========================
CAP_OFFICIAL_INCIDENT = 100
CAP_MULTI_CRITICAL = 94
CAP_LINUX_MULTI_PREFERRED = 92
CAP_LINUX_SINGLE_PREFERRED = 86
CAP_SINGLE_REPORT = 84
CAP_WEAK_SINGLE_REPORT = 78
CAP_DELETED_OR_INCOMPLETE = 76
CAP_QUESTION_ONLY = 72
CAP_UNCONFIRMED_NO_MULTI = 88
CAP_GENERAL_AI = 70
CAP_REDDIT_SINGLE = 82


# =========================
# 用户强偏好：PP / 接码 / 二验 / Free / 401 / 账号风控
# 只做风险观察，不输出教程
# =========================
PREFERRED_RISK_KEYWORDS = [
    "pp",
    "PP",
    "PayPal",
    "paypal",
    "无卡",
    "pp无卡",
    "PP无卡",
    "PP渠道",
    "pp渠道",
    "pp又复活",
    "PP又复活",
    "复活",
    "拉闸",
    "疑似拉闸",
    "变回free",
    "变回 Free",
    "注册成功瞬间变回free",
    "Plus变Free",
    "plus变free",
    "free",
    "Free",
    "手搓接码",
    "接码",
    "接码平台",
    "接码渠道",
    "手机接码",
    "手机号随机",
    "随机手机号",
    "二次验证",
    "二验",
    "三次验证",
    "强制二验",
    "gpt登录二次验证",
    "GPT登录二次验证",
    "登录二次验证",
    "所有邮箱都要接码",
    "邮箱都要接码",
    "邮箱注册",
    "注册入口",
    "手机号验证",
    "手机验证",
    "短信验证",
    "text message",
    "hero sms",
    "Hero SMS",
    "WhatsApp",
    "whatsapp",
    "巴西",
    "智利",
    "印尼",
    "印度尼西亚",
    "美国号码",
    "同一号码",
    "绑 3 次",
    "绑定 3 次",
    "成功率",
    "很快",
    "耗尽",
    "库存",
    "号码被消耗",
    "401",
    "403",
    "AT",
    "RT",
    "session",
    "auth.json",
    "OAuth",
    "access token",
    "refresh token",
]

PREFERRED_RISK_COMBO_HINTS = [
    ["pp", "free"],
    ["pp", "拉闸"],
    ["pp", "401"],
    ["pp", "无卡"],
    ["pp", "复活"],
    ["paypal", "plus"],
    ["plus", "free"],
    ["注册", "free"],
    ["注册成功", "free"],
    ["gpt", "二次验证"],
    ["gpt", "接码"],
    ["chatgpt", "接码"],
    ["chatgpt", "手机号"],
    ["codex", "text message"],
    ["codex", "接码"],
    ["codex", "短信"],
    ["codex", "二次验证"],
    ["team", "二次验证"],
    ["team", "接码"],
    ["邮箱", "接码"],
    ["注册", "接码"],
    ["手机号", "随机"],
    ["手搓接码", "巴西"],
    ["hero sms", "号码"],
    ["hero sms", "二次验证"],
    ["hero sms", "印尼"],
    ["whatsapp", "验证码"],
    ["401", "team"],
    ["401", "ak"],
    ["401", "cpa"],
    ["oauth", "失效"],
    ["access token", "失效"],
    ["refresh token", "失效"],
]


RSS_SOURCES = [
    "https://status.openai.com/history.rss",
    "https://github.com/openai/codex/issues.atom",
    "https://github.com/openai/codex/releases.atom",
    "https://github.com/anthropics/claude-code/releases.atom",
    "https://github.com/google-gemini/gemini-cli/releases.atom",

    "https://github.blog/changelog/label/copilot/feed/",
    "https://openai.com/news/rss.xml",

    "https://linux.do/latest.rss",
    "https://linux.do/top.rss",
    "https://linux.do/posts.rss",

    "https://www.reddit.com/r/ClaudeAI/search.rss?q=Claude%20Code%20OR%20HERMES.md%20OR%20billing%20OR%20refund&restrict_sr=1&sort=new",
    "https://www.reddit.com/r/OpenAI/search.rss?q=Codex%20OR%20rate%20limit%20OR%20banned%20OR%20suspended%20OR%20verification&restrict_sr=1&sort=new",
    "https://www.reddit.com/r/ChatGPT/search.rss?q=Plus%20OR%20banned%20OR%20suspended%20OR%20verification%20OR%20text%20message&restrict_sr=1&sort=new",
    "https://www.reddit.com/r/GitHubCopilot/search.rss?q=student%20OR%20model%20OR%20Codex%20OR%20Claude&restrict_sr=1&sort=new",

    "https://hnrss.org/newest?q=OpenAI",
    "https://hnrss.org/newest?q=Claude",
    "https://hnrss.org/newest?q=Codex",
    "https://hnrss.org/newest?q=Gemini",
    "https://hnrss.org/newest?q=Claude%20Code",
    "https://hnrss.org/newest?q=Gemini%20CLI",

    "https://aihot.virxact.com/feed.xml",
    "https://aihot.virxact.com/feed/daily.xml",

    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://huggingface.co/blog/feed.xml",
]


BLOCK_KEYWORDS = [
    "AI art",
    "AI image",
    "image generator",
    "stable diffusion",
    "midjourney",
    "prompt pack",
    "wallpaper",
    "NFT",
    "crypto",
    "web3",
    "metaverse",
    "机器人硬件",
    "具身智能",
    "自动驾驶",
    "投资人",
    "融资",
    "完成融资",
    "种子轮",
    "A轮",
    "B轮",
    "IPO",
    "股价",
    "股票",
    "财报电话会",
    "CEO访谈",
    "专访",
    "论文解读",
    "benchmark paper",
    "arxiv",
    "新论文",
    "研究人员提出",
    "招聘",
    "岗位",
    "普通招聘",
    "营销",
    "推广",
    "邀请码",
    "优惠码",
    "课程",
    "训练营",
    "卖课",
]


CRITICAL_EVENT_KEYWORDS = [
    "大规模封号",
    "批量封号",
    "大规模封禁",
    "全线失效",
    "全部失效",
    "大面积异常",
    "无法登录",
    "登录失败",
    "OAuth 失效",
    "refresh token 失效",
    "access token 失效",
    "refresh_token 失效",
    "access_token 失效",
    "401 unauthorized",
    "403 forbidden",
    "outage",
    "degraded",
    "incident",
    "stream disconnected",
    "weekly limit",
    "quota drained",
    "学生包取消",
    "Plus 下线",
    "Pro 限制",
    "短信验证",
    "接码失败",
    "手搓接码",
    "接码渠道",
    "接码平台",
    "pp又复活",
    "PP又复活",
    "PP渠道",
    "pp渠道",
    "pp无卡",
    "PP无卡",
    "无卡",
    "拉闸",
    "疑似拉闸",
    "变回free",
    "注册成功瞬间变回free",
    "所有邮箱都要接码",
    "邮箱都要接码",
    "二次验证",
    "二验",
    "三次验证",
    "重新验证",
    "需要验证",
    "强制验证",
    "强验",
    "手机号验证",
    "手机验证",
    "接码二验",
    "接码验证",
    "Codex 接码",
    "codex接码",
    "text message verification",
    "rate limit incident",
    "Codex outage",
    "OpenAI outage",
    "Claude Code billing",
    "账户终止",
    "account terminated",
    "account suspended",
    "account disabled",
    "account banned",
    "banned",
    "suspended",
    "封禁",
    "禁用",
    "锁号",
]


CORE_TOPIC_KEYWORDS = [
    "Codex",
    "Plus",
    "Pro",
    "Team",
    "Enterprise",
    "Free",
    "封号",
    "被封",
    "冻结",
    "禁用",
    "风控",
    "下线",
    "失效",
    "风控收紧",
    "接码",
    "短信",
    "手机号",
    "验证码",
    "短信验证",
    "接码失败",
    "手搓接码",
    "接码渠道",
    "接码平台",
    "pp",
    "PP",
    "PayPal",
    "paypal",
    "无卡",
    "pp无卡",
    "PP无卡",
    "PP渠道",
    "pp渠道",
    "pp又复活",
    "PP又复活",
    "复活",
    "拉闸",
    "疑似拉闸",
    "变回free",
    "Free",
    "free",
    "邮箱接码",
    "所有邮箱都要接码",
    "手机号随机",
    "随机手机号",
    "巴西",
    "智利",
    "印尼",
    "印度尼西亚",
    "hero sms",
    "Hero SMS",
    "WhatsApp",
    "text message",
    "verification",
    "OAuth",
    "accessToken",
    "access token",
    "refresh_token",
    "refresh token",
    "401",
    "403",
    "rate limit",
    "quota",
    "限流",
    "额度",
    "5h额度",
    "weekly limit",
    "学生包",
    "GitHub Student",
    "GitHub Copilot",
    "Copilot",
    "Copilot Chat",
    "Copilot coding agent",
    "Claude Code",
    "Claude Pro",
    "Claude API",
    "Tier3",
    "Tier 3",
    "HERMES.md",
    "Gemini CLI",
    "billing",
    "refund",
    "账号",
    "账号池",
    "共享号",
    "Sub2API",
    "CPA",
    "Cockpit",
    "Codex Manager",
    "9router",
    "AxonHub",
    "passkey",
    "MFA",
]


MEDIUM_TOPIC_KEYWORDS = [
    "OpenAI",
    "ChatGPT",
    "Claude",
    "Anthropic",
    "Gemini",
    "Antigravity",
    "xAI",
    "GPU",
    "API",
    "model",
    "模型",
    "算力",
    "订阅",
    "额度提升",
    "价格",
    "AI",
    "Cursor",
    "Windsurf",
    "DeepSeek",
    "Qwen",
    "Llama",
    "agent",
    "MCP",
]


LOW_VALUE_KEYWORDS = [
    "CEO",
    "采访",
    "访谈",
    "观点",
    "预测",
    "估值",
    "营收",
    "收入",
    "利润",
    "市场规模",
    "行业报告",
    "普通发布",
    "产品发布",
    "新功能",
    "博客",
    "榜单",
    "The Verge",
    "Hugging Face",
    "prompt engineering",
    "epistemic",
    "数学",
    "科研",
    "家庭使用",
    "family",
    "story",
]


SINGLE_REPORT_KEYWORDS = [
    "单点反馈",
    "单人反馈",
    "有人反馈",
    "用户反馈",
    "疑似",
    "可能",
    "未确认",
    "暂无官方确认",
    "没有官方确认",
    "评论区",
    "个例",
    "个案",
    "求助",
    "请教",
    "询问",
    "问一下",
    "想问",
    "one user",
    "single report",
    "unconfirmed",
    "seems",
    "maybe",
    "appears",
]


MULTI_REPORT_KEYWORDS = [
    "多人反馈",
    "多位用户",
    "大量用户",
    "大批",
    "批量",
    "普遍",
    "集中反馈",
    "官方确认",
    "已确认",
    "confirmed",
    "confirmed by multiple",
    "many users",
    "multiple users",
    "widespread",
]


WEAK_SIGNAL_KEYWORDS = [
    "帖子已删",
    "已删除",
    "删除了",
    "原帖已删",
    "原帖已删除",
    "无具体",
    "没有具体",
    "不清楚",
    "不确定",
    "求助",
    "请教",
    "询问",
    "问一下",
    "有没有",
    "信息差",
]


QUESTION_ONLY_KEYWORDS = [
    "求助",
    "请教",
    "询问",
    "问一下",
    "想问",
    "有没有",
    "吗",
    "？",
    "?",
]


def load_seen():
    if not SEEN_FILE.exists():
        return set()

    try:
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))

        if isinstance(data, list):
            return set(data)

        if isinstance(data, dict):
            return set(data.keys())

        return set()
    except Exception:
        return set()


def save_seen(seen):
    SEEN_FILE.write_text(
        json.dumps(list(seen)[-4000:], ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def normalize_text(text):
    return " ".join((text or "").replace("\n", " ").split())


def clean_html(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&#39;", "'", text)
    return normalize_text(text)


def short_text(text, limit=2500):
    text = clean_html(text)

    if len(text) <= limit:
        return text

    return text[:limit] + "..."


def parse_entry_datetime(entry):
    for key in ["published_parsed", "updated_parsed", "created_parsed"]:
        value = entry.get(key)
        if value:
            try:
                return datetime.fromtimestamp(time.mktime(value), tz=timezone.utc)
            except Exception:
                pass

    for key in ["published", "updated", "created", "pubDate"]:
        value = entry.get(key)
        if value:
            try:
                dt = parsedate_to_datetime(value)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                pass

    return None


def format_datetime_for_feishu(dt):
    if not dt:
        return "未知"

    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        beijing_dt = dt.astimezone(timezone(timedelta(hours=8)))
        return beijing_dt.strftime("%Y-%m-%d %H:%M:%S 北京时间")
    except Exception:
        return "未知"


def get_age_minutes(dt):
    if not dt:
        return None

    try:
        now = datetime.now(timezone.utc)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        age = now - dt.astimezone(timezone.utc)
        return max(0, age.total_seconds() / 60)
    except Exception:
        return None


def item_id(title, link):
    raw = f"{title}|{link}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def text_signature(text):
    text = clean_html(text).lower()
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[\W_]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def is_similar_title(title, existing_titles, threshold=0.86):
    sig = text_signature(title)

    for old_title in existing_titles:
        old_sig = text_signature(old_title)

        if not sig or not old_sig:
            continue

        ratio = SequenceMatcher(None, sig, old_sig).ratio()

        if ratio >= threshold:
            return True

    return False


def similarity(a, b):
    a_sig = text_signature(a)
    b_sig = text_signature(b)

    if not a_sig or not b_sig:
        return 0

    return SequenceMatcher(None, a_sig, b_sig).ratio()


def matched_keywords(text, keywords):
    lower = text.lower()
    return [k for k in keywords if k.lower() in lower]


def get_source_profile(source, rss_url):
    text = f"{source} {rss_url}".lower()

    if "status.openai.com" in text:
        return {"name": "OpenAI Status", "authority": 25, "type": "A类 / 官方状态"}

    if "github.com/openai/codex/issues" in text:
        return {"name": "OpenAI Codex Issues", "authority": 23, "type": "A类 / 官方仓库用户反馈"}

    if "github.com/openai/codex/releases" in text:
        return {"name": "OpenAI Codex Releases", "authority": 23, "type": "A类 / 官方仓库发布"}

    if "github.com/anthropics/claude-code/releases" in text:
        return {"name": "Claude Code Releases", "authority": 22, "type": "A类 / 官方仓库发布"}

    if "github.com/google-gemini/gemini-cli/releases" in text:
        return {"name": "Gemini CLI Releases", "authority": 21, "type": "A类 / 官方仓库发布"}

    if "github.blog/changelog/label/copilot/feed" in text:
        return {"name": "GitHub Copilot Changelog", "authority": 21, "type": "A类 / GitHub Copilot 官方变更"}

    if "openai.com/news/rss.xml" in text:
        return {"name": "OpenAI News", "authority": 18, "type": "A类 / OpenAI 官方新闻"}

    if "linux.do/latest" in text:
        return {"name": "LINUX DO - 最新话题", "authority": 24, "type": "B类 / 中文社区一线反馈"}

    if "linux.do/top" in text:
        return {"name": "LINUX DO - 热门话题", "authority": 23, "type": "B类 / 中文社区热门反馈"}

    if "linux.do/posts" in text:
        return {"name": "LINUX DO - 最新帖子", "authority": 22, "type": "B类 / 中文社区帖子反馈"}

    if "linux.do" in text:
        return {"name": "Linux.do", "authority": 23, "type": "B类 / 中文社区一线反馈"}

    if "reddit.com" in text:
        return {"name": "Reddit", "authority": 12, "type": "B类 / 海外社区反馈"}

    if "hnrss.org" in text:
        return {"name": "Hacker News", "authority": 10, "type": "B类 / 技术社区讨论"}

    if "aihot.virxact.com/feed.xml" in text:
        return {"name": "AIHOT 精选", "authority": 16, "type": "C类 / 中文精选聚合"}

    if "aihot.virxact.com/feed/daily.xml" in text:
        return {"name": "AIHOT 日报", "authority": 14, "type": "C类 / 中文日报聚合"}

    if "huggingface.co" in text:
        return {"name": "HuggingFace Blog", "authority": 8, "type": "D类 / 技术博客"}

    if "theverge.com" in text:
        return {"name": "The Verge AI", "authority": 6, "type": "D类 / 泛 AI 媒体"}

    return {"name": source or rss_url, "authority": 8, "type": "未知公开 RSS"}


def clamp_score(score):
    return max(0, min(100, int(round(score))))


def level_from_score(score, has_critical, has_strong_evidence):
    if score >= 92 and has_critical and has_strong_evidence:
        return "爆炸"

    if score >= 70:
        return "高"

    if score >= 58:
        return "观察"

    return "低"


def level_icon(level):
    if level == "爆炸":
        return "🚨"
    if level == "高":
        return "🔴"
    if level == "观察":
        return "🟡"
    return "⚪"


def rating_from_score(score):
    if score >= 92:
        return "S"
    if score >= 85:
        return "A+"
    if score >= 78:
        return "A"
    if score >= 70:
        return "B+"
    if score >= 58:
        return "B"
    if score >= 45:
        return "C"
    return "D"


def action_from_score(score, level):
    if level == "爆炸":
        return "需要立刻关注"
    if level == "高":
        return "需要关注"
    if level == "观察":
        return "暂时观察"
    return "可忽略"


def get_event_theme(title, summary):
    text = f"{title} {summary}".lower()

    theme_parts = []

    if any(k in text for k in ["pp", "paypal", "无卡"]):
        theme_parts.append("pp")
    if any(k in text for k in ["free", "变回free", "plus变free"]):
        theme_parts.append("free")
    if any(k in text for k in ["接码", "手机号", "短信", "text message", "hero sms", "whatsapp", "巴西", "智利", "印尼"]):
        theme_parts.append("phone_verification")
    if any(k in text for k in ["二次验证", "二验", "三次验证", "强验", "重新验证"]):
        theme_parts.append("mfa")
    if "codex" in text:
        theme_parts.append("codex")
    if "team" in text:
        theme_parts.append("team")
    if "claude" in text:
        theme_parts.append("claude")
    if any(k in text for k in ["401", "403", "oauth", "access token", "refresh token", "at", "rt", "session", "auth.json"]):
        theme_parts.append("token_401")
    if any(k in text for k in ["额度", "quota", "rate limit", "weekly limit", "5h"]):
        theme_parts.append("quota")

    if not theme_parts:
        return "general"

    return "+".join(sorted(set(theme_parts)))


def has_preferred_signal(title, summary):
    text = f"{title} {summary}"
    preferred_hits = matched_keywords(text, PREFERRED_RISK_KEYWORDS)

    if preferred_hits:
        return True

    lower_text = text.lower()

    for combo in PREFERRED_RISK_COMBO_HINTS:
        if all(part.lower() in lower_text for part in combo):
            return True

    return False


def time_score_from_age(age_minutes):
    # 不是“越新越好”。
    # 你要的是过去 1 小时热点，所以 30～60 分钟反而更适合判断，评论和反馈更成熟。
    if age_minutes is None:
        return 2, "发布时间未知 +2"

    if age_minutes <= 10:
        return 4, "0-10分钟新帖 +4"

    if age_minutes <= 30:
        return 6, "10-30分钟热点 +6"

    if age_minutes <= 60:
        return 8, "30-60分钟成熟热点 +8"

    if age_minutes <= OLD_ITEM_GRACE_HOURS * 60:
        return 2, "1-2小时旧热点 +2"

    return 0, "超过2小时 +0"


def should_ignore_by_time(published_dt, score_info, title, summary):
    age_minutes = get_age_minutes(published_dt)

    if age_minutes is None:
        return False, ""

    if age_minutes <= HOT_WINDOW_HOURS * 60:
        return False, ""

    if age_minutes <= OLD_ITEM_GRACE_HOURS * 60:
        if score_info["score"] >= HIGH_SCORE_SEND_BYPASS:
            return False, ""

        if has_preferred_signal(title, summary):
            return False, ""

        if score_info["source_profile"]["authority"] >= 21 and score_info["has_critical"]:
            return False, ""

        return True, f"超过1小时且不是强偏好/高分/官方事故：{int(age_minutes)}分钟"

    if score_info["score"] >= 92 and score_info["has_critical"]:
        return False, ""

    return True, f"超过热点窗口太久：{int(age_minutes)}分钟"


def is_deleted_or_incomplete(title, summary):
    text = f"{title} {summary}"
    hits = matched_keywords(text, WEAK_SIGNAL_KEYWORDS)

    if hits:
        return True, hits

    return False, []


def is_question_only(title, summary):
    text = f"{title} {summary}"
    hits = matched_keywords(text, QUESTION_ONLY_KEYWORDS)

    if not hits:
        return False, []

    has_real_signal = matched_keywords(text, CRITICAL_EVENT_KEYWORDS) or matched_keywords(text, PREFERRED_RISK_KEYWORDS)
    if has_real_signal:
        return False, []

    return True, hits


def apply_score_caps(score, score_info, title, summary):
    source_profile = score_info["source_profile"]

    single_hits = score_info["single_hits"]
    multi_hits = score_info["multi_hits"]
    has_preferred = score_info.get("has_preferred_signal", False)
    has_critical = score_info.get("has_critical", False)
    has_strong_evidence = score_info.get("has_strong_evidence", False)
    core_hits = score_info.get("core_hits", [])
    medium_hits = score_info.get("medium_hits", [])
    preferred_hits = score_info.get("preferred_hits", [])

    deleted_or_incomplete, weak_hits = is_deleted_or_incomplete(title, summary)
    question_only, question_hits = is_question_only(title, summary)

    cap_reasons = []

    is_officialish = source_profile["authority"] >= 21 and source_profile["name"] not in [
        "LINUX DO - 最新话题",
        "LINUX DO - 热门话题",
        "LINUX DO - 最新帖子",
        "Linux.do",
    ]

    is_linux = source_profile["name"].lower().startswith("linux") or source_profile["name"].startswith("LINUX")
    is_multi_confirmed = bool(multi_hits)

    if is_officialish and has_critical:
        final_cap = CAP_OFFICIAL_INCIDENT
    elif is_multi_confirmed and has_critical:
        final_cap = CAP_MULTI_CRITICAL
    elif is_linux and is_multi_confirmed and has_preferred:
        final_cap = CAP_LINUX_MULTI_PREFERRED
    elif is_linux and single_hits and has_preferred:
        final_cap = CAP_LINUX_SINGLE_PREFERRED
    elif single_hits and has_preferred:
        final_cap = CAP_SINGLE_REPORT
    elif single_hits:
        final_cap = CAP_WEAK_SINGLE_REPORT
    elif medium_hits and not core_hits and not preferred_hits and not has_critical:
        final_cap = CAP_GENERAL_AI
    else:
        final_cap = CAP_UNCONFIRMED_NO_MULTI

    if deleted_or_incomplete:
        final_cap = min(final_cap, CAP_DELETED_OR_INCOMPLETE)
        cap_reasons.append(f"已删/信息不完整上限 {CAP_DELETED_OR_INCOMPLETE}：{', '.join(weak_hits[:3])}")

    if question_only:
        final_cap = min(final_cap, CAP_QUESTION_ONLY)
        cap_reasons.append(f"普通询问帖上限 {CAP_QUESTION_ONLY}：{', '.join(question_hits[:3])}")

    if source_profile["name"] == "Reddit" and not multi_hits:
        final_cap = min(final_cap, CAP_REDDIT_SINGLE)
        cap_reasons.append(f"Reddit 单点上限 {CAP_REDDIT_SINGLE}")

    if not (has_strong_evidence and has_critical) and not multi_hits:
        final_cap = min(final_cap, CAP_UNCONFIRMED_NO_MULTI)

    if score > final_cap:
        score = final_cap
        cap_reasons.append(f"应用最终评分上限 {final_cap}")

    score = clamp_score(score)

    return score, cap_reasons


def score_news(title, summary, source, rss_url, published_dt=None):
    # 重要：这里只能用标题和摘要评分，不能把 RSS 源标题 / 查询词算进去。
    text = f"{title} {summary}"

    source_profile = get_source_profile(source, rss_url)

    critical_hits = matched_keywords(text, CRITICAL_EVENT_KEYWORDS)
    core_hits = matched_keywords(text, CORE_TOPIC_KEYWORDS)
    medium_hits = matched_keywords(text, MEDIUM_TOPIC_KEYWORDS)
    single_hits = matched_keywords(text, SINGLE_REPORT_KEYWORDS)
    multi_hits = matched_keywords(text, MULTI_REPORT_KEYWORDS)
    low_value_hits = matched_keywords(text, LOW_VALUE_KEYWORDS)
    block_hits = matched_keywords(text, BLOCK_KEYWORDS)
    preferred_hits = matched_keywords(text, PREFERRED_RISK_KEYWORDS)

    score = 0
    reasons = []

    source_score = source_profile["authority"]
    score += source_score
    reasons.append(f"来源权威 {source_score}")

    topic_score = 0

    if core_hits:
        topic_score += 18
        reasons.append(f"核心主题 +18：{', '.join(core_hits[:5])}")

    if medium_hits:
        topic_score += 5
        reasons.append(f"普通 AI 主题 +5：{', '.join(medium_hits[:4])}")

    if len(core_hits) >= 2:
        topic_score += 4
        reasons.append("多个核心主题 +4")

    topic_score = min(topic_score, 24)
    score += topic_score

    special_combo_score = 0
    lower_text = text.lower()

    codex_related = "codex" in lower_text
    verification_related = any(k in lower_text for k in [
        "二次验证",
        "二验",
        "三次验证",
        "重新验证",
        "需要验证",
        "强制验证",
        "强验",
        "手机号验证",
        "手机验证",
        "短信验证",
        "接码",
        "verification",
        "text message",
    ])

    if codex_related and verification_related:
        special_combo_score += 8
        reasons.append("Codex + 接码/验证特殊关注 +8")

    if preferred_hits:
        preferred_score = min(14, 6 + len(preferred_hits[:4]) * 2)
        special_combo_score += preferred_score
        reasons.append(f"你关注的 PP/接码/二验风险词 +{preferred_score}：{', '.join(preferred_hits[:6])}")

    combo_hit_count = 0

    for combo in PREFERRED_RISK_COMBO_HINTS:
        if all(part.lower() in lower_text for part in combo):
            combo_hit_count += 1

    if combo_hit_count:
        combo_score = min(14, combo_hit_count * 5)
        special_combo_score += combo_score
        reasons.append(f"你关注的风险组合命中 +{combo_score}")

    if source_profile["name"].startswith("LINUX") or source_profile["name"] == "Linux.do":
        if preferred_hits:
            special_combo_score += 5
            reasons.append("Linux.do + 你关注主题 +5")

    score += special_combo_score

    severity_score = 0

    if critical_hits:
        severity_score += 16
        reasons.append(f"严重事件 +16：{', '.join(critical_hits[:4])}")
    elif any(k.lower() in text.lower() for k in ["rate limit", "quota", "billing", "refund", "verification", "401", "403"]):
        severity_score += 9
        reasons.append("额度/计费/验证/错误码异常 +9")
    elif core_hits:
        severity_score += 5
        reasons.append("核心主题一般事件 +5")

    severity_score = min(severity_score, 16)
    score += severity_score

    evidence_score = 0
    has_strong_evidence = False

    officialish = source_profile["authority"] >= 21 and not source_profile["name"].startswith("LINUX") and source_profile["name"] != "Linux.do"

    if officialish:
        evidence_score += 8
        has_strong_evidence = True
        reasons.append("官方/高权重来源 +8")

    if source_profile["name"].startswith("LINUX") or source_profile["name"] == "Linux.do":
        if core_hits:
            evidence_score += 4
            reasons.append("Linux.do 核心主题 +4")

    if multi_hits:
        evidence_score += 14
        has_strong_evidence = True
        reasons.append(f"多人/确认反馈 +14：{', '.join(multi_hits[:3])}")

    if single_hits:
        evidence_score -= 10
        reasons.append(f"单点/未确认反馈 -10：{', '.join(single_hits[:3])}")

    evidence_score = max(-14, min(18, evidence_score))
    score += evidence_score

    age_minutes = get_age_minutes(published_dt)
    freshness_score, freshness_reason = time_score_from_age(age_minutes)
    score += freshness_score
    reasons.append(freshness_reason)

    source_freshness_score = 0

    if "aihot.virxact.com/feed.xml" in rss_url:
        source_freshness_score += 2
        reasons.append("AIHOT 精选 +2")
    elif "aihot.virxact.com/feed/daily.xml" in rss_url:
        source_freshness_score += 1
        reasons.append("AIHOT 日报 +1")
    elif "github.blog/changelog/label/copilot/feed" in rss_url:
        source_freshness_score += 3
        reasons.append("Copilot 官方变更流 +3")
    elif "openai.com/news/rss.xml" in rss_url:
        source_freshness_score += 2
        reasons.append("OpenAI 官方新闻流 +2")
    elif "releases.atom" in rss_url:
        source_freshness_score += 3
        reasons.append("官方发布流 +3")
    elif "latest" in rss_url or "newest" in rss_url or "issues.atom" in rss_url:
        source_freshness_score += 2
        reasons.append("最新流 +2")
    elif "top" in rss_url:
        source_freshness_score += 2
        reasons.append("热门流 +2")

    source_freshness_score = min(source_freshness_score, 3)
    score += source_freshness_score

    penalty = 0

    if block_hits:
        penalty += 100
        reasons.append(f"黑名单命中 -100：{', '.join(block_hits[:4])}")

    if low_value_hits:
        penalty += 15
        reasons.append(f"泛新闻降权 -15：{', '.join(low_value_hits[:3])}")

    if source_profile["authority"] <= 12 and not core_hits and medium_hits:
        penalty += 22
        reasons.append("低权重泛 AI 新闻且无核心主题 -22")

    if medium_hits and not core_hits and not critical_hits:
        penalty += 16
        reasons.append("仅普通 AI 主题 -16")

    if source_profile["name"] in ["The Verge AI", "HuggingFace Blog"] and not core_hits:
        penalty += 20
        reasons.append("泛 AI 源无核心主题 -20")

    if source_profile["name"] == "Reddit" and not core_hits and not critical_hits:
        penalty += 25
        reasons.append("Reddit 搜索结果无核心主题 -25")

    score -= penalty

    has_critical = bool(critical_hits)

    if officialish:
        has_strong_evidence = True

    if multi_hits:
        has_strong_evidence = True

    score = clamp_score(score)

    preliminary_info = {
        "score": score,
        "source_profile": source_profile,
        "critical_hits": critical_hits,
        "core_hits": core_hits,
        "medium_hits": medium_hits,
        "single_hits": single_hits,
        "multi_hits": multi_hits,
        "low_value_hits": low_value_hits,
        "block_hits": block_hits,
        "preferred_hits": preferred_hits,
        "has_preferred_signal": bool(preferred_hits or combo_hit_count),
        "has_critical": has_critical,
        "has_strong_evidence": has_strong_evidence,
    }

    score, cap_reasons = apply_score_caps(score, preliminary_info, title, summary)

    if cap_reasons:
        reasons.extend([f"评分上限：{r}" for r in cap_reasons])

    level = level_from_score(score, has_critical, has_strong_evidence)
    rating = rating_from_score(score)
    action = action_from_score(score, level)

    return {
        "score": score,
        "level": level,
        "rating": rating,
        "action": action,
        "reasons": reasons,
        "source_profile": source_profile,
        "critical_hits": critical_hits,
        "core_hits": core_hits,
        "medium_hits": medium_hits,
        "single_hits": single_hits,
        "multi_hits": multi_hits,
        "low_value_hits": low_value_hits,
        "block_hits": block_hits,
        "preferred_hits": preferred_hits,
        "has_preferred_signal": bool(preferred_hits or combo_hit_count),
        "has_critical": has_critical,
        "has_strong_evidence": has_strong_evidence,
    }


def should_skip_by_rules(score_info):
    score = score_info["score"]

    if score_info["block_hits"]:
        return True, "命中黑名单"

    if score < MIN_RULE_SCORE:
        return True, f"评分低于规则筛选线：{score}"

    if score < MIN_PUSH_SCORE:
        if score_info.get("has_preferred_signal") and score >= PREFERRED_ALLOW_JUDGE_SCORE:
            return False, ""

        return True, f"评分低于最终推送线：{score}"

    if score_info["single_hits"] and score < MIN_SINGLE_REPORT_SCORE:
        if score_info.get("has_preferred_signal") and score >= PREFERRED_ALLOW_JUDGE_SCORE:
            return False, ""

        return True, f"单点反馈分数不足：{score}"

    if score_info["medium_hits"] and not score_info["core_hits"] and score < MIN_GENERAL_AI_SCORE:
        if score_info.get("has_preferred_signal"):
            return False, ""

        return True, f"普通 AI 新闻分数不足：{score}"

    if not score_info["core_hits"] and not score_info["critical_hits"] and score < MIN_GENERAL_AI_SCORE:
        if score_info.get("has_preferred_signal"):
            return False, ""

        return True, f"非核心主题分数不足：{score}"

    return False, ""


def fetch_feed(url):
    headers = {
        "User-Agent": "Mozilla/5.0 AI-Radar-Bot/10.0",
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        feed = feedparser.parse(response.text)

        if getattr(feed, "bozo", False):
            print(f"Feed parsed with warning: {url} | {getattr(feed, 'bozo_exception', '')}")

        return feed, ""
    except Exception as e:
        return None, str(e)


def normalize_base_url(base_url):
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url:
        return ""

    if base_url.endswith("/chat/completions"):
        return base_url

    return base_url + "/chat/completions"


def extract_chat_content(data):
    try:
        return data["choices"][0]["message"]["content"].strip()
    except Exception:
        return ""


def call_openai_compatible_chat(provider_name, api_key, base_url, model, messages, temperature, max_tokens, response_format=None):
    if not api_key or not base_url or not model:
        return None, f"{provider_name}: missing_config"

    url = normalize_base_url(base_url)

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }

    if response_format:
        payload["response_format"] = response_format

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    last_error = ""

    for attempt in range(1, MODEL_MAX_RETRIES + 1):
        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=MODEL_TIMEOUT_SECONDS,
            )

            if response.status_code == 429:
                wait_seconds = MODEL_BACKOFF_BASE_SECONDS * attempt
                print(f"{provider_name} rate limited: 429, retry_after={wait_seconds}s, attempt={attempt}/{MODEL_MAX_RETRIES}")
                time.sleep(wait_seconds)
                last_error = "429_rate_limited"
                continue

            if response.status_code >= 500:
                wait_seconds = MODEL_BACKOFF_BASE_SECONDS * attempt
                print(f"{provider_name} server error: {response.status_code}, retry_after={wait_seconds}s, body={response.text[:300]}")
                time.sleep(wait_seconds)
                last_error = f"server_{response.status_code}"
                continue

            if response.status_code != 200:
                print(f"{provider_name} failed: {response.status_code}, body={response.text[:500]}")
                return None, f"http_{response.status_code}"

            data = response.json()
            content = extract_chat_content(data)

            if not content:
                print(f"{provider_name} empty content: {str(data)[:500]}")
                return None, "empty_content"

            time.sleep(MODEL_REQUEST_INTERVAL_SECONDS)
            return content, "ok"

        except Exception as e:
            wait_seconds = MODEL_BACKOFF_BASE_SECONDS * attempt
            print(f"{provider_name} exception: {e}, retry_after={wait_seconds}s, attempt={attempt}/{MODEL_MAX_RETRIES}")
            time.sleep(wait_seconds)
            last_error = str(e)[:120]

    return None, last_error or "unknown_error"


def call_llm_json(messages, max_tokens=700):
    # 第一优先级：魔搭 ModelScope
    providers = [
        {
            "name": "ModelScope",
            "api_key": MODELSCOPE_API_KEY,
            "base_url": MODELSCOPE_BASE_URL,
            "model": MODELSCOPE_MODEL,
            "response_format": {"type": "json_object"},
        },
        {
            "name": "DeepSeek",
            "api_key": DEEPSEEK_API_KEY,
            "base_url": DEEPSEEK_BASE_URL,
            "model": DEEPSEEK_MODEL,
            "response_format": {"type": "json_object"},
        },
    ]

    for provider in providers:
        content, status = call_openai_compatible_chat(
            provider_name=provider["name"],
            api_key=provider["api_key"],
            base_url=provider["base_url"],
            model=provider["model"],
            messages=messages,
            temperature=0.0,
            max_tokens=max_tokens,
            response_format=provider["response_format"],
        )

        if not content:
            print(f"LLM JSON provider failed: {provider['name']} | {status}")
            continue

        try:
            data = json.loads(content)
            print(f"LLM JSON provider used: {provider['name']}")
            return data, provider["name"], "ok"
        except Exception as e:
            print(f"LLM JSON parse failed: {provider['name']} | {e} | content={content[:500]}")
            continue

    return None, "none", "all_failed"


def call_llm_text(messages, max_tokens=950):
    providers = [
        {
            "name": "ModelScope",
            "api_key": MODELSCOPE_API_KEY,
            "base_url": MODELSCOPE_BASE_URL,
            "model": MODELSCOPE_MODEL,
        },
        {
            "name": "DeepSeek",
            "api_key": DEEPSEEK_API_KEY,
            "base_url": DEEPSEEK_BASE_URL,
            "model": DEEPSEEK_MODEL,
        },
    ]

    for provider in providers:
        content, status = call_openai_compatible_chat(
            provider_name=provider["name"],
            api_key=provider["api_key"],
            base_url=provider["base_url"],
            model=provider["model"],
            messages=messages,
            temperature=0.05,
            max_tokens=max_tokens,
            response_format=None,
        )

        if not content:
            print(f"LLM text provider failed: {provider['name']} | {status}")
            continue

        print(f"LLM text provider used: {provider['name']}")
        return content, provider["name"], "ok"

    return "", "none", "all_failed"


def default_ai_judgement(score_info):
    score = score_info["score"]
    level = score_info["level"]

    if level == "爆炸":
        should_push = True
        risk = "高"
        confidence = "高" if score_info["has_strong_evidence"] else "中"
    elif level == "高":
        should_push = True
        risk = "中"
        confidence = "中"
    else:
        should_push = False
        risk = "低"
        confidence = "低"

    return {
        "should_push": should_push,
        "category": "其他",
        "scope": "未确认",
        "risk": risk,
        "confidence": confidence,
        "action": action_from_score(score, level),
        "reason": "规则评分兜底判断",
        "no_hype_title": "",
        "hype_warning": "",
    }


def normalize_ai_judgement(data, score_info):
    fallback = default_ai_judgement(score_info)

    if not isinstance(data, dict):
        return fallback

    def pick(key, default):
        value = data.get(key, default)
        if value is None:
            return default
        return value

    should_push = pick("should_push", fallback["should_push"])
    if isinstance(should_push, str):
        should_push = should_push.strip().lower() in ["true", "yes", "1", "是", "需要", "推送"]

    category = str(pick("category", fallback["category"]))[:40]
    scope = str(pick("scope", fallback["scope"]))[:40]
    risk = str(pick("risk", fallback["risk"]))[:20]
    confidence = str(pick("confidence", fallback["confidence"]))[:20]
    action = str(pick("action", fallback["action"]))[:30]
    reason = str(pick("reason", fallback["reason"]))[:140]
    no_hype_title = str(pick("no_hype_title", fallback["no_hype_title"]))[:70]
    hype_warning = str(pick("hype_warning", ""))[:140]

    allowed_risk = ["低", "中", "高"]
    allowed_confidence = ["低", "中", "高"]
    allowed_action = ["需要立刻关注", "需要关注", "暂时观察", "可忽略"]

    if risk not in allowed_risk:
        risk = fallback["risk"]

    if confidence not in allowed_confidence:
        confidence = fallback["confidence"]

    if action not in allowed_action:
        action = fallback["action"]

    score = score_info["score"]

    if score >= 92 and score_info["has_critical"] and score_info["has_strong_evidence"]:
        should_push = True
        risk = "高"

    if score_info.get("has_preferred_signal") and score >= MIN_PUSH_SCORE:
        if confidence == "低":
            confidence = "中"
        should_push = bool(should_push)

    if HIGH_QUALITY_ONLY:
        if score < MIN_PUSH_SCORE and not (score_info.get("has_preferred_signal") and score >= PREFERRED_ALLOW_JUDGE_SCORE):
            should_push = False
            action = "可忽略"

        if confidence == "低" and score < HIGH_SCORE_SEND_BYPASS and not score_info.get("has_preferred_signal"):
            should_push = False
            action = "可忽略"

        if risk == "低" and score < MIN_GENERAL_AI_SCORE and not score_info.get("has_preferred_signal"):
            should_push = False
            action = "可忽略"

        if scope in ["单点反馈", "未确认"] and score < MIN_SINGLE_REPORT_SCORE:
            if not (score_info.get("has_preferred_signal") and score >= PREFERRED_ALLOW_JUDGE_SCORE):
                should_push = False
                action = "可忽略"

    return {
        "should_push": bool(should_push),
        "category": category,
        "scope": scope,
        "risk": risk,
        "confidence": confidence,
        "action": action,
        "reason": reason,
        "no_hype_title": no_hype_title,
        "hype_warning": hype_warning,
    }


def ai_judge_news(title, summary, source, link, score_info, related_updates=None):
    if not MODELSCOPE_API_KEY and not DEEPSEEK_API_KEY:
        return default_ai_judgement(score_info), "no_api_key"

    source_profile = score_info["source_profile"]
    related_updates = related_updates or []

    system_prompt = """你是一个 AI 情报雷达的第一层审核器。
你只输出 JSON，不输出任何解释文字。

任务：
判断一条公开 RSS / 社区信息是否值得推送到飞书 AI 情报群。

必须遵守：
1. 只能根据输入判断，不要编造。
2. 当前是中高质量模式：70 分以上有资格推送，但普通低价值内容不要推。
3. 如果原文没有“大规模 / 多人 / 官方确认”，禁止判断为大规模事件。
4. 单个帖子、单个用户、疑似传言，scope 必须是“单点反馈”或“未确认”。
5. 涉及账号、封号、接码、OAuth、token、401、Plus、Pro、Codex、PP、无卡，只能做风险判断，不能提供绕风控、薅号、盗号、规避检测方法。
6. 普通产品发布、融资、观点、采访、论文、泛 AI 新闻，除非与 Codex / Claude Code / Copilot / Plus / PP / 接码 / 二验 / 风控 / 401 / OAuth 强相关，否则 should_push=false。
7. 不确定就降低 confidence。
8. 不要标题党。
9. Reddit 搜索结果如果只是普通聊天、提示词、娱乐、观点，不要推送。
10. 如果 score >= 88 且确实与核心主题相关，可以更积极推送。
11. 用户特别偏好这类情报：pp又复活、pp渠道疑似拉闸、注册成功瞬间变回free、gpt登录二次验证、手机号随机、手搓接码、巴西/智利/印尼/hero sms/WhatsApp 接码反馈。命中这些时更积极推送，但仍然要写成风险观察，不要写教程。
12. 如果 related_updates 里有同类反馈，可以把 scope 评为“多点反馈”或“社区多点反馈”，但不能写官方确认。
13. 如果只是求助/询问/帖子已删/信息不完整，应该降低 confidence，并说明信息不足。

必须输出合法 JSON，格式示例：
{
  "should_push": true,
  "category": "账号风控",
  "scope": "单点反馈",
  "risk": "中",
  "confidence": "中",
  "action": "暂时观察",
  "reason": "涉及 PP/接码/二次验证相关风险反馈，但来源仍是社区单点或少量反馈",
  "no_hype_title": "PP/接码/二次验证风险反馈待观察",
  "hype_warning": "不能写成官方确认或大规模事件"
}
"""

    user_prompt = f"""请输出 JSON。

系统规则评分：
- score: {score_info["score"]}/100
- level: {score_info["level"]}
- rating: {score_info["rating"]}
- action: {score_info["action"]}
- source_profile: {source_profile}
- reasons: {score_info["reasons"]}
- critical_hits: {score_info["critical_hits"]}
- core_hits: {score_info["core_hits"]}
- medium_hits: {score_info["medium_hits"]}
- single_hits: {score_info["single_hits"]}
- multi_hits: {score_info["multi_hits"]}
- preferred_hits: {score_info.get("preferred_hits", [])}

中高质量推送标准：
- 70 分以上才有资格推送。
- 普通低价值内容不要推。
- 单点反馈可以推，但必须是账号 / Codex / OAuth / 401 / Plus / PP / 无卡 / 接码 / 二次验证 / 手机号验证 / Claude Code / Copilot / 额度 / 风控相关。
- 没有核心主题、没有严重事件、没有官方确认的普通 AI 新闻不要推。
- 只要对 Codex / Claude Code / Copilot / Plus / PP渠道 / 无卡PayPal / 接码 / 二次验证 / OAuth / 401 / 账号风控 / 额度 / 订阅 / API 可用性有实际影响，可以推。
- Reddit 普通帖子、提示词讨论、数学/科研泛新闻、家庭使用 ChatGPT 这类内容不要推。
- 用户喜欢的格式是：标题、兴趣等级、变化、成本/渠道/注意、关键信息、风险判断、评论补充、来源、发布时间、链接。
- 只做风险观察，不输出购买接码、绕过验证、规避风控的操作教程。

原始信息：
标题：{short_text(title, 240)}
来源：{source}
链接：{link}
摘要：{short_text(summary, 1800)}

同类合并反馈：
{json.dumps(related_updates, ensure_ascii=False)[:1800]}
"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    judgement, provider_name, provider_status = call_llm_json(messages, max_tokens=700)

    if not judgement:
        print("AI judge failed: all providers failed")
        return default_ai_judgement(score_info), provider_status

    print(f"AI judge provider used: {provider_name}")

    return normalize_ai_judgement(judgement, score_info), "ok"


def fallback_message(title, summary, source, link, score_info, ai_judgement, published_time="未知", related_updates=None):
    related_updates = related_updates or []

    level = score_info["level"]
    score = score_info["score"]
    rating = score_info["rating"]
    reasons = score_info["reasons"]
    source_profile = score_info["source_profile"]

    action = ai_judgement.get("action") or score_info["action"]
    risk = ai_judgement.get("risk", "中")
    confidence = ai_judgement.get("confidence", "中")
    scope = ai_judgement.get("scope", "未确认")
    category = ai_judgement.get("category", "其他")
    reason = ai_judgement.get("reason", "公开来源自动抓取")
    title_text = ai_judgement.get("no_hype_title") or short_text(title, 90)

    if level == "爆炸":
        prefix = "🚨"
    elif level == "高":
        prefix = "📌"
    else:
        prefix = "🟡"

    reason_text = "、".join(reasons[:5]) if reasons else "公开来源自动抓取"

    related_text = ""
    if related_updates:
        lines = []
        for update in related_updates[:MAX_RELATED_UPDATES_IN_CARD]:
            lines.append(f"- {update.get('published_time', '未知')}：{short_text(update.get('title', ''), 80)}")
        related_text = "\n\n💬 同类补充：\n" + "\n".join(lines)

    return f"""{prefix} {short_text(title_text, 80)}

{level_icon(level)} 兴趣等级：{level}
🔥 评分：{score}/100
🏷 评级：{rating}
📌 建议：{action}

📝 变化：
- {short_text(summary, 420)}

🔎 关键信息：
- 类型：{category}
- 范围：{scope}
- 来源类型：{source_profile["type"]}
- 触发原因：{reason_text}

⚠️ 风险判断：{risk}
- {reason}{related_text}

可信度：{confidence}
理由：来自公开来源，需结合更多反馈继续观察。

来源：{source}
发布时间：{published_time}
链接：{link}"""


def build_related_updates_text(related_updates):
    if not related_updates:
        return "无"

    lines = []

    for update in related_updates[:MAX_RELATED_UPDATES_IN_CARD]:
        lines.append(
            f"- {update.get('published_time', '未知')}｜{short_text(update.get('title', ''), 90)}｜{update.get('source', '')}"
        )

    return "\n".join(lines)


def remove_duplicate_interest_lines(text):
    lines = text.splitlines()
    result = []
    interest_seen = False

    for line in lines:
        if "兴趣等级：" in line:
            if interest_seen:
                continue
            interest_seen = True

        result.append(line)

    return "\n".join(result).strip()


def deepseek_summarize(title, summary, source, link, score_info, ai_judgement, published_time="未知", related_updates=None):
    related_updates = related_updates or []

    level = score_info["level"]
    score = score_info["score"]
    rating = score_info["rating"]

    action = ai_judgement.get("action") or score_info["action"]
    risk = ai_judgement.get("risk", "中")
    confidence = ai_judgement.get("confidence", "中")
    scope = ai_judgement.get("scope", "未确认")
    category = ai_judgement.get("category", "其他")
    no_hype_title = ai_judgement.get("no_hype_title", "")

    display_title = no_hype_title or title

    if not MODELSCOPE_API_KEY and not DEEPSEEK_API_KEY:
        print("No LLM API key set, use fallback message.")
        return fallback_message(title, summary, source, link, score_info, ai_judgement, published_time, related_updates)

    related_text = build_related_updates_text(related_updates)

    raw_content = short_text(
        f"""
标题：{title}
来源：{source}
发布时间：{published_time}
摘要：{summary}
链接：{link}

同类合并反馈：
{related_text}

规则评分：
兴趣等级：{level}
评分：{score}/100
评级：{rating}
来源信息：{score_info.get("source_profile")}
触发原因：{score_info.get("reasons")}
严重事件命中：{score_info.get("critical_hits")}
核心主题命中：{score_info.get("core_hits")}
普通主题命中：{score_info.get("medium_hits")}
单点反馈命中：{score_info.get("single_hits")}
多人反馈命中：{score_info.get("multi_hits")}
偏好命中：{score_info.get("preferred_hits")}

AI 预判：
{json.dumps(ai_judgement, ensure_ascii=False)}
""",
        5600
    )

    system_prompt = """你是一个飞书 AI 情报频道的中文编辑。
你的任务是把公开 RSS、社区帖子、新闻源内容，总结成短、准、清晰的中文情报卡片。

硬性规则：
1. 只能根据输入内容总结，不要编造。
2. 必须遵守 AI 预判里的 category / scope / risk / confidence / action。
3. 如果 AI 预判 scope 是“单点反馈”或“未确认”，禁止写“大规模、批量、全线、已确认、普遍”。
4. 如果原文没有官方确认，不要写“官方确认”。
5. 涉及封号、接码、账号、风控、额度、OAuth、access token、refresh token、401、共享号、Plus/Pro、PP、无卡，只做风险分析，不提供薅号、绕风控、盗号、规避检测教程。
6. 不要输出 Markdown 表格。
7. 不要输出代码块。
8. 不要写“根据原文”“根据你提供的信息”这种废话。
9. 标题要短、准，不标题党。
10. 输出适合飞书手机端阅读，不要太长。
11. 宁可保守，不要夸大。
12. 用户喜欢“图文情报卡”的风格：变化、成本/渠道、注意、关键信息、风险判断、评论补充、来源、发布时间、链接。
13. 如果原文里没有成本/渠道/评论补充，就不要编造；可以省略对应小节。
14. 不要输出“兴趣等级、评分、评级、建议”，这些由程序统一添加，避免重复。
15. 如果有同类合并反馈，要在“💬 同类补充”中整理，不要当成多条重复消息。
16. 如果帖子已删、只是求助、只是单人询问，要明确“信息不完整/仅单点反馈”，不要夸大。
"""

    user_prompt = f"""请生成飞书情报卡片正文。

程序会自动添加这个统一头部，你不要重复输出：
📌 {short_text(display_title, 60)}
{level_icon(level)} 兴趣等级：{level}
🔥 评分：{score}/100
🏷 评级：{rating}
📌 建议：{action}

你只输出以下正文部分，可按需要省略没有依据的小节：

📝 变化：
- 写清楚发生了什么

💰 成本/渠道：
- 只有原文出现价格、渠道、国家、hero sms、WhatsApp、巴西/智利/印尼等信息才写

⚠️ 注意：
- 写不确定性、并非所有人都有、单点反馈、帖子已删、信息不完整等限制

🔎 关键信息：
- 类型：{category}
- 范围：{scope}
- 影响：一句话说明影响

🧯 风险判断：
- 保守判断，不夸大

💬 同类补充：
- 有同类合并反馈时才写

可信度：{confidence}
理由：一句话

不要输出来源、发布时间、链接，程序会统一添加到底部。

要求：
- 不要输出教程
- 不要教人怎么买接码、怎么绕验证、怎么保号
- 不要把单点反馈写成全网事件
- 如果是 PP / 接码 / 二验 / free / 邮箱注册收紧相关内容，要优先整理成风险观察卡片

原始内容：
{raw_content}
"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    body, provider_name, provider_status = call_llm_text(messages, max_tokens=950)

    if not body:
        print("Summary failed: all providers failed")
        return fallback_message(title, summary, source, link, score_info, ai_judgement, published_time, related_updates)

    body = remove_duplicate_interest_lines(body)

    header = f"""📌 {short_text(display_title, 60)}

{level_icon(level)} 兴趣等级：{level}
🔥 评分：{score}/100
🏷 评级：{rating}
📌 建议：{action}"""

    footer = f"""来源：{source}
发布时间：{published_time}
链接：{link}"""

    content = f"{header}\n\n{body}\n\n{footer}"
    return content[:3800]


def send_feishu(message):
    if not FEISHU_WEBHOOK:
        print("FEISHU_WEBHOOK not set, skip Feishu.")
        return False

    payload = {
        "msg_type": "text",
        "content": {
            "text": message[:3800]
        }
    }

    try:
        response = requests.post(FEISHU_WEBHOOK, json=payload, timeout=30)
        print("Feishu status:", response.status_code)
        print("Feishu body:", response.text[:500])

        if response.status_code != 200:
            print("Feishu send failed:", response.status_code, response.text[:500])
            return False

        return True

    except Exception as e:
        print("Feishu exception:", e)
        return False


def send_empty_heartbeat(summary_text):
    if not SEND_EMPTY_HEARTBEAT:
        return False

    message = f"""✅ AI Radar 已自动运行

本次没有推送高质量情报。

{summary_text}
"""

    return send_feishu(message)


def should_stop_sending(sent_count, next_score):
    if sent_count < SOFT_SEND_LIMIT:
        return False

    if sent_count >= HARD_SEND_LIMIT:
        return True

    if next_score >= HIGH_SCORE_SEND_BYPASS:
        return False

    return True


def should_merge_candidates(a, b):
    if not MERGE_SIMILAR_EVENTS:
        return False

    if a.get("theme") != "general" and a.get("theme") == b.get("theme"):
        if similarity(a.get("title", ""), b.get("title", "")) >= 0.32:
            return True

        a_text = f"{a.get('title', '')} {a.get('summary', '')}"
        b_text = f"{b.get('title', '')} {b.get('summary', '')}"

        if similarity(a_text, b_text) >= MERGE_SIMILARITY_THRESHOLD:
            return True

    if similarity(a.get("title", ""), b.get("title", "")) >= 0.84:
        return True

    return False


def merge_related_candidates(candidates):
    if not MERGE_SIMILAR_EVENTS:
        return candidates

    merged = []

    for item in candidates:
        placed = False

        for group in merged:
            if should_merge_candidates(group, item):
                group.setdefault("related_updates", [])

                group["related_updates"].append({
                    "title": item["title"],
                    "source": item["source"],
                    "link": item["link"],
                    "published_time": item.get("published_time", "未知"),
                    "score": item["score"],
                })

                if item["score"] > group["score"]:
                    previous_main = {
                        "title": group["title"],
                        "source": group["source"],
                        "link": group["link"],
                        "published_time": group.get("published_time", "未知"),
                        "score": group["score"],
                    }

                    group["related_updates"].append(previous_main)

                    for key in [
                        "priority",
                        "score",
                        "uid",
                        "title",
                        "summary",
                        "source",
                        "link",
                        "published_time",
                        "published_dt",
                        "score_info",
                        "theme",
                    ]:
                        group[key] = item[key]

                placed = True
                break

        if not placed:
            copied = dict(item)
            copied["related_updates"] = []
            merged.append(copied)

    merged.sort(key=lambda item: (item["priority"], -item["score"]))
    return merged


def should_mark_seen_when_skipped(score_info, published_dt, title, summary):
    age_minutes = get_age_minutes(published_dt)

    # 黑名单 / 明显低分垃圾，直接 seen。
    if score_info.get("block_hits"):
        return True

    if score_info["score"] < 45:
        return True

    # 超过 2 小时旧内容，直接 seen。
    if age_minutes is not None and age_minutes > OLD_ITEM_GRACE_HOURS * 60:
        return True

    # 强偏好但刚发不久，先不要 seen，允许后面 30～60 分钟评论发酵后复扫。
    if has_preferred_signal(title, summary) and age_minutes is not None and age_minutes <= 30:
        return False

    return True


def main():
    print("MODELSCOPE_API_KEY set:", bool(MODELSCOPE_API_KEY))
    print("MODELSCOPE_BASE_URL:", MODELSCOPE_BASE_URL)
    print("MODELSCOPE_MODEL:", MODELSCOPE_MODEL)
    print("DEEPSEEK_API_KEY set:", bool(DEEPSEEK_API_KEY))
    print("DEEPSEEK_BASE_URL:", DEEPSEEK_BASE_URL)
    print("DEEPSEEK_MODEL:", DEEPSEEK_MODEL)
    print("FEISHU_WEBHOOK set:", bool(FEISHU_WEBHOOK))
    print("FEISHU_WEBHOOK length:", len(FEISHU_WEBHOOK))
    print("HIGH_QUALITY_ONLY:", HIGH_QUALITY_ONLY)
    print("HOT_WINDOW_HOURS:", HOT_WINDOW_HOURS)
    print("OLD_ITEM_GRACE_HOURS:", OLD_ITEM_GRACE_HOURS)
    print("MIN_RULE_SCORE:", MIN_RULE_SCORE)
    print("MIN_PUSH_SCORE:", MIN_PUSH_SCORE)
    print("MIN_SINGLE_REPORT_SCORE:", MIN_SINGLE_REPORT_SCORE)
    print("MIN_GENERAL_AI_SCORE:", MIN_GENERAL_AI_SCORE)
    print("PREFERRED_ALLOW_JUDGE_SCORE:", PREFERRED_ALLOW_JUDGE_SCORE)
    print("SOFT_SEND_LIMIT:", SOFT_SEND_LIMIT)
    print("HIGH_SCORE_SEND_BYPASS:", HIGH_SCORE_SEND_BYPASS)
    print("HARD_SEND_LIMIT:", HARD_SEND_LIMIT)
    print("MAX_JUDGE_COUNT:", MAX_JUDGE_COUNT)
    print("MAX_ENTRIES_PER_FEED:", MAX_ENTRIES_PER_FEED)
    print("SEND_EMPTY_HEARTBEAT:", SEND_EMPTY_HEARTBEAT)
    print("MERGE_SIMILAR_EVENTS:", MERGE_SIMILAR_EVENTS)
    print("CAP_LINUX_SINGLE_PREFERRED:", CAP_LINUX_SINGLE_PREFERRED)
    print("CAP_DELETED_OR_INCOMPLETE:", CAP_DELETED_OR_INCOMPLETE)
    print("Cron recommended: 0 0,2,4-23 * * *")

    seen = load_seen()
    new_seen = set(seen)

    candidates = []
    accepted_titles = []

    skipped_rule_count = 0
    skipped_time_count = 0
    skipped_similar_count = 0
    skipped_ai_count = 0
    failed_feed_count = 0

    feed_stats = []

    for rss_url in RSS_SOURCES:
        feed, error = fetch_feed(rss_url)

        if not feed:
            failed_feed_count += 1
            print(f"Feed failed: {rss_url} | {error}")
            feed_stats.append({
                "url": rss_url,
                "ok": False,
                "entries": 0,
                "accepted": 0,
                "skipped": 0,
                "error": error,
            })
            continue

        source = feed.feed.get("title", rss_url)
        all_entries_count = len(feed.entries)
        entries = feed.entries[:MAX_ENTRIES_PER_FEED]

        feed_accepted = 0
        feed_skipped = 0

        print(f"Feed OK: {source} | {rss_url} | entries_total={all_entries_count} | entries_read={len(entries)}")

        for entry in entries:
            title = clean_html(entry.get("title", ""))
            link = entry.get("link", "")
            summary = clean_html(entry.get("summary", "") or entry.get("description", ""))
            published_dt = parse_entry_datetime(entry)
            published_time = format_datetime_for_feishu(published_dt)

            if not title or not link:
                feed_skipped += 1
                continue

            uid = item_id(title, link)

            if uid in seen:
                feed_skipped += 1
                continue

            if is_similar_title(title, accepted_titles):
                skipped_similar_count += 1
                feed_skipped += 1
                print(f"Skip similar: {short_text(title, 80)}")
                new_seen.add(uid)
                continue

            score_info = score_news(title, summary, source, rss_url, published_dt)

            ignore_by_time, time_reason = should_ignore_by_time(published_dt, score_info, title, summary)
            if ignore_by_time:
                skipped_time_count += 1
                feed_skipped += 1
                print(f"Skip by time: {short_text(title, 80)} | {time_reason}")

                if should_mark_seen_when_skipped(score_info, published_dt, title, summary):
                    new_seen.add(uid)

                continue

            skip, skip_reason = should_skip_by_rules(score_info)

            if skip:
                skipped_rule_count += 1
                feed_skipped += 1
                print(f"Skip by rules: {short_text(title, 80)} | {skip_reason}")

                if should_mark_seen_when_skipped(score_info, published_dt, title, summary):
                    new_seen.add(uid)

                continue

            priority = {
                "爆炸": 0,
                "高": 1,
                "观察": 2,
                "低": 3,
            }.get(score_info["level"], 3)

            candidates.append({
                "priority": priority,
                "score": score_info["score"],
                "uid": uid,
                "title": title,
                "summary": summary,
                "source": source,
                "link": link,
                "published_time": published_time,
                "published_dt": published_dt,
                "theme": get_event_theme(title, summary),
                "score_info": score_info,
            })

            accepted_titles.append(title)
            feed_accepted += 1

        feed_stats.append({
            "url": rss_url,
            "ok": True,
            "entries": all_entries_count,
            "read": len(entries),
            "accepted": feed_accepted,
            "skipped": feed_skipped,
            "error": "",
        })

    candidates.sort(key=lambda item: (item["priority"], -item["score"]))
    original_candidate_count = len(candidates)

    candidates = merge_related_candidates(candidates)

    print(f"Candidates before merge: {original_candidate_count}")
    print(f"Candidates after merge: {len(candidates)}")

    sent_count = 0
    judged_count = 0

    for item in candidates:
        if judged_count >= MAX_JUDGE_COUNT:
            print("Stop judging: MAX_JUDGE_COUNT reached.")
            break

        if should_stop_sending(sent_count, item["score"]):
            print(
                "Stop sending:",
                f"sent_count={sent_count}",
                f"next_score={item['score']}",
                f"soft_limit={SOFT_SEND_LIMIT}",
                f"high_bypass={HIGH_SCORE_SEND_BYPASS}",
            )
            break

        related_updates = item.get("related_updates", [])

        ai_judgement, judge_status = ai_judge_news(
            item["title"],
            item["summary"],
            item["source"],
            item["link"],
            item["score_info"],
            related_updates,
        )

        judged_count += 1

        print(
            "AI judge:",
            judge_status,
            "| score=",
            item["score"],
            "| theme=",
            item.get("theme"),
            "| related=",
            len(related_updates),
            "| push=",
            ai_judgement.get("should_push"),
            "| risk=",
            ai_judgement.get("risk"),
            "| confidence=",
            ai_judgement.get("confidence"),
            "| title=",
            short_text(item["title"], 80),
        )

        if not ai_judgement.get("should_push", False):
            skipped_ai_count += 1
            new_seen.add(item["uid"])

            for related in related_updates:
                if related.get("link"):
                    new_seen.add(item_id(related.get("title", ""), related.get("link", "")))

            print(f"Skip by AI judge: {short_text(item['title'], 80)} | {ai_judgement.get('reason')}")
            continue

        message = deepseek_summarize(
            item["title"],
            item["summary"],
            item["source"],
            item["link"],
            item["score_info"],
            ai_judgement,
            item.get("published_time", "未知"),
            related_updates,
        )

        success = send_feishu(message)

        new_seen.add(item["uid"])

        for related in related_updates:
            if related.get("link"):
                new_seen.add(item_id(related.get("title", ""), related.get("link", "")))

        if success:
            sent_count += 1

        time.sleep(3)

    save_seen(new_seen)

    print("===== Feed health summary =====")
    for stat in feed_stats:
        status = "OK" if stat["ok"] else "FAILED"
        print(
            f"{status} | entries={stat.get('entries', 0)} | read={stat.get('read', 0)} | accepted={stat['accepted']} | skipped={stat['skipped']} | {stat['url']}"
        )

    summary_text = f"""候选：{original_candidate_count}
合并后候选：{len(candidates)}
AI 判断：{judged_count}
规则跳过：{skipped_rule_count}
时间跳过：{skipped_time_count}
AI 跳过：{skipped_ai_count}
相似跳过：{skipped_similar_count}
失败源：{failed_feed_count}
实际推送：{sent_count}"""

    print("===== Run summary =====")
    print(summary_text)

    if sent_count == 0:
        send_empty_heartbeat(summary_text)


if __name__ == "__main__":
    main()
