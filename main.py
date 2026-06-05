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


# 注意：这里只使用 Agnes AI 的文本模型，不使用魔搭 ModelScope，也不使用官网 DeepSeek API。
# 需要在 GitHub Secrets 中配置：AGNES_API_KEY / AGNES_BASE_URL / AGNES_MODEL。
# AGNES_MODEL 推荐使用 Agnes 2.0 Flash；如果官方文档给的是模型 ID，请以文档模型 ID 为准。

AGNES_API_KEY = os.environ.get("AGNES_API_KEY", "")
AGNES_BASE_URL = os.environ.get("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1")
AGNES_MODEL = os.environ.get("AGNES_MODEL", "agnes-2.0-flash")

FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")

SEEN_FILE = Path("seen.json")


# =========================
# 运行说明
# =========================
# main.py 不负责控制运行频率。
# 运行频率由 cron-job.org 控制。
#
# 你当前推荐 cron-job.org 表达式：
# 0 0,3,6,8,10,12,14,16,18,20,22 * * *
#
# 含义：
# 00:00、03:00、06:00 执行
# 08:00～22:00 每 2 小时执行
#
# cron-job.org 时区必须是：
# Asia/Shanghai


# =========================
# 推送策略：过去 1 小时热点 + 中高质量 + 不刷屏
# =========================
HIGH_QUALITY_ONLY = True

# cron-job.org 现在是白天约 2 小时一次、凌晨约 3 小时一次。
# 这里看过去 4 小时，避免 RSS 源延迟、cron 延迟、GitHub Actions 排队导致漏抓。
HOT_WINDOW_HOURS = 4
OLD_ITEM_GRACE_HOURS = 6

# 规则层不要太死：先放更多候选给魔搭综合判断；最终是否推送仍由 AI judge + 动态数量控制。
MIN_RULE_SCORE = 50
MIN_PUSH_SCORE = 65
MIN_SINGLE_REPORT_SCORE = 65
MIN_GENERAL_AI_SCORE = 76
PREFERRED_ALLOW_JUDGE_SCORE = 56

# 不固定 3 条或 5 条，由 should_stop_sending 动态控制。
SOFT_SEND_LIMIT = 999
HIGH_SCORE_SEND_BYPASS = 86
HARD_SEND_LIMIT = 6

# Agnes 负责深度判断，候选先规则筛选和合并，避免一次运行调用过多。
MAX_JUDGE_COUNT = 10

# 重要：不要只抓 10 / 15 条，否则你看到的永远都是几分钟前。
# RSS 本身不是全站数据库，但这里会尽量读取 RSS 当前返回的更多条目。
MAX_ENTRIES_PER_FEED = 100

SEND_EMPTY_HEARTBEAT = False

# =========================
# 模型调用策略：只使用 Agnes AI，不调用魔搭 ModelScope / 官网 DeepSeek
# =========================
# Agnes API 可能有分钟级限流；宁可慢一点，也不要一轮里连续 429 后乱推。
MODEL_REQUEST_INTERVAL_SECONDS = 25
# Agnes 限流时不要一轮里反复硬撞；失败后用安全规则兜底，下一轮 cron 再重试。
MODEL_MAX_RETRIES = 1
MODEL_TIMEOUT_SECONDS = 120
MODEL_BACKOFF_BASE_SECONDS = 30

# 当 Agnes 429 时，只允许“强相关/高分/多条同类”的规则兜底推送，避免完全静默，也避免垃圾乱推。
RATE_LIMIT_LOCAL_FALLBACK_ENABLED = True
RATE_LIMIT_LOCAL_FALLBACK_MIN_SCORE = 80
RATE_LIMIT_LOCAL_FALLBACK_MAX_SEND = 3

MERGE_SIMILAR_EVENTS = True
MERGE_SIMILARITY_THRESHOLD = 0.76
MAX_RELATED_UPDATES_IN_CARD = 4


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
    # PP / PayPal / 无卡
    "pp", "PP", "PayPal", "paypal", "无卡", "pp无卡", "PP无卡",
    "PP渠道", "pp渠道", "pp又复活", "PP又复活", "复活", "拉闸", "疑似拉闸",
    "变回free", "变回 Free", "注册成功瞬间变回free", "Plus变Free", "plus变free",

    # Free / Plus / Pro / Team / 账号风险
    "free", "Free", "Plus", "plus", "Pro", "pro", "Team", "team",
    "账号", "账号池", "共享号", "号池", "封号", "被封", "锁号", "禁用",
    "风控", "风控收紧", "异常支付", "支付异常", "订阅异常", "会员异常",

    # 接码 / 手机号 / 二验
    "手搓接码", "接码", "接码平台", "接码渠道", "手机接码",
    "手机号随机", "随机手机号", "二次验证", "二验", "三次验证", "强制二验",
    "重新验证", "gpt登录二次验证", "GPT登录二次验证", "登录二次验证",
    "所有邮箱都要接码", "邮箱都要接码", "邮箱注册", "注册入口",
    "手机号验证", "手机验证", "短信验证", "text message", "SMS", "sms", "phone verification",

    # 接码平台 / 国家
    "hero sms", "Hero SMS", "HeroSMS", "WhatsApp", "whatsapp",
    "巴西", "智利", "印尼", "印度尼西亚", "美国号码", "同一号码",
    "绑 3 次", "绑定 3 次", "成功率", "很快", "耗尽", "库存", "号码被消耗",

    # Token / OAuth / 401
    "401", "403", "429", "AT", "RT", "session", "auth.json", "OAuth", "oauth",
    "access token", "refresh token", "accessToken", "refresh_token", "unauthorized", "forbidden",

    # Codex / CPA / Sub2API
    "Codex", "codex", "CPA", "cpa", "Sub2API", "sub2api", "Cockpit", "cockpit",
    "Codex Manager", "9router", "AxonHub",

    # 额度 / 限流 / 订阅
    "额度", "限额", "5h", "周限额", "weekly limit", "quota", "rate limit",
    "billing", "refund", "out of credits", "workspace out of credits",
]

PREFERRED_RISK_COMBO_HINTS = [
    ["pp", "free"], ["pp", "拉闸"], ["pp", "401"], ["pp", "无卡"], ["pp", "复活"],
    ["paypal", "plus"], ["plus", "free"], ["注册", "free"], ["注册成功", "free"],

    ["gpt", "二次验证"], ["gpt", "接码"], ["chatgpt", "接码"], ["chatgpt", "手机号"],
    ["邮箱", "接码"], ["注册", "接码"], ["手机号", "随机"], ["手搓接码", "巴西"],

    ["hero sms", "号码"], ["hero sms", "二次验证"], ["hero sms", "印尼"], ["whatsapp", "验证码"],

    ["codex", "text message"], ["codex", "接码"], ["codex", "短信"], ["codex", "二次验证"],
    ["codex", "额度"], ["codex", "free"], ["codex", "limit"],

    ["team", "二次验证"], ["team", "接码"], ["team", "401"],

    ["401", "team"], ["401", "ak"], ["401", "cpa"], ["401", "sub2api"],
    ["oauth", "失效"], ["access token", "失效"], ["refresh token", "失效"],

    ["cpa", "sub2api"], ["cpa", "冲突"], ["sub2api", "free"],
    ["账号池", "401"], ["共享号", "封号"],
]


RSS_SOURCES = [
    # =========================
    # 官方状态 / 官方发布：紧急消息优先
    # =========================
    "https://status.openai.com/history.rss",
    "https://status.anthropic.com/history.rss",
    "https://www.githubstatus.com/history.rss",

    "https://github.com/openai/codex/issues.atom",
    "https://github.com/openai/codex/releases.atom",
    "https://github.com/anthropics/claude-code/releases.atom",
    "https://github.com/google-gemini/gemini-cli/releases.atom",

    "https://github.blog/changelog/label/copilot/feed/",
    "https://openai.com/news/rss.xml",

    # =========================
    # 中文社区：你最关心的 PP / 接码 / 二验 / 401 / Codex 多来自这里
    # =========================
    "https://linux.do/latest.rss",
    "https://linux.do/top.rss",
    "https://linux.do/posts.rss",

    # =========================
    # 国内 / 中文 AI 信息源：用于补充大事件，不作为高权重风控源
    # =========================
    "https://www.qbitai.com/feed",
    "https://rsshub.app/36kr/newsflashes",

    # =========================
    # Reddit：海外社区反馈
    # =========================
    "https://www.reddit.com/r/ClaudeAI/search.rss?q=Claude%20Code%20OR%20HERMES.md%20OR%20billing%20OR%20refund%20OR%20ban%20OR%20suspended%20OR%20verification&restrict_sr=1&sort=new",
    "https://www.reddit.com/r/OpenAI/search.rss?q=Codex%20OR%20rate%20limit%20OR%20banned%20OR%20suspended%20OR%20verification%20OR%20OAuth%20OR%20401%20OR%20text%20message&restrict_sr=1&sort=new",
    "https://www.reddit.com/r/ChatGPT/search.rss?q=Plus%20OR%20Pro%20OR%20Free%20OR%20banned%20OR%20suspended%20OR%20verification%20OR%20text%20message%20OR%20phone%20OR%20SMS%20OR%20OAuth%20OR%20401&restrict_sr=1&sort=new",
    "https://www.reddit.com/r/GitHubCopilot/search.rss?q=student%20OR%20model%20OR%20Codex%20OR%20Claude%20OR%20quota%20OR%20limit%20OR%20billing%20OR%20suspended&restrict_sr=1&sort=new",

    # =========================
    # Hacker News：海外技术社区
    # =========================
    "https://hnrss.org/newest?q=OpenAI",
    "https://hnrss.org/newest?q=Claude",
    "https://hnrss.org/newest?q=Codex",
    "https://hnrss.org/newest?q=Gemini",
    "https://hnrss.org/newest?q=Claude%20Code",
    "https://hnrss.org/newest?q=Gemini%20CLI",
    "https://hnrss.org/newest?q=OAuth",
    "https://hnrss.org/newest?q=rate%20limit",
    "https://hnrss.org/newest?q=API%20billing",

    # =========================
    # 聚合源
    # =========================
    "https://aihot.virxact.com/feed.xml",
    "https://aihot.virxact.com/feed/daily.xml",

    # =========================
    # 泛 AI 源：保留，但权重低
    # =========================
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

    if "status.anthropic.com" in text:
        return {"name": "Anthropic Status", "authority": 24, "type": "A类 / 官方状态"}

    if "githubstatus.com" in text:
        return {"name": "GitHub Status", "authority": 23, "type": "A类 / 官方状态"}

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

    if "qbitai.com" in text:
        return {"name": "量子位", "authority": 12, "type": "C类 / 中文 AI 媒体"}

    if "rsshub.app/36kr/newsflashes" in text or "36kr" in text:
        return {"name": "36氪快讯", "authority": 10, "type": "C类 / 中文快讯聚合"}

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
    # 你要的是过去 4 小时内的热点综合判断：
    # 30～180 分钟通常比刚发几分钟更适合判断，因为评论和同类反馈更成熟。
    if age_minutes is None:
        return 2, "发布时间未知 +2"

    if age_minutes <= 10:
        return 4, "0-10分钟新帖 +4"

    if age_minutes <= 30:
        return 6, "10-30分钟热点 +6"

    if age_minutes <= 60:
        return 8, "30-60分钟成熟热点 +8"

    if age_minutes <= 180:
        return 9, "1-3小时多源综合窗口 +9"

    if age_minutes <= HOT_WINDOW_HOURS * 60:
        return 7, "3-4小时热点补抓 +7"

    if age_minutes <= OLD_ITEM_GRACE_HOURS * 60:
        return 2, "4-6小时旧热点 +2"

    return 0, "超过6小时 +0"


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
    elif "qbitai.com" in rss_url:
        source_freshness_score += 1
        reasons.append("量子位中文 AI 源 +1")
    elif "36kr" in rss_url:
        source_freshness_score += 1
        reasons.append("36氪快讯源 +1")

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


def is_official_emergency(score_info):
    source_profile = score_info.get("source_profile", {})
    source_name = source_profile.get("name", "")
    authority = source_profile.get("authority", 0)

    critical_hits = score_info.get("critical_hits", [])
    has_critical = score_info.get("has_critical", False)

    official_names = [
        "OpenAI Status",
        "Anthropic Status",
        "GitHub Status",
        "OpenAI News",
        "OpenAI Codex Issues",
        "OpenAI Codex Releases",
        "Claude Code Releases",
        "Gemini CLI Releases",
        "GitHub Copilot Changelog",
    ]

    if source_name in official_names and (has_critical or critical_hits):
        return True

    if authority >= 21 and (has_critical or critical_hits):
        return True

    return False


def should_skip_by_rules(score_info):
    score = score_info["score"]

    if is_official_emergency(score_info):
        return False, ""

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


def call_llm_json(messages, max_tokens=1200):
    # 只使用 Agnes AI，不调用魔搭 ModelScope / 官网 DeepSeek。
    providers = [
        {
            "name": "Agnes",
            "api_key": AGNES_API_KEY,
            "base_url": AGNES_BASE_URL,
            "model": AGNES_MODEL,
            "response_format": {"type": "json_object"},
        },
    ]

    last_status = "all_failed"

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
            last_status = status or "all_failed"
            continue

        try:
            data = json.loads(content)
            print(f"LLM JSON provider used: {provider['name']}")
            return data, provider["name"], "ok"
        except Exception as e:
            print(f"LLM JSON parse failed: {provider['name']} | {e} | content={content[:500]}")
            last_status = "json_parse_failed"
            continue

    return None, "none", last_status or "all_failed"
def call_llm_text(messages, max_tokens=1800):
    providers = [
        {
            "name": "Agnes",
            "api_key": AGNES_API_KEY,
            "base_url": AGNES_BASE_URL,
            "model": AGNES_MODEL,
        },
    ]

    last_status = "all_failed"

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
            last_status = status or "all_failed"
            continue

        print(f"LLM text provider used: {provider['name']}")
        return content, provider["name"], "ok"

    return "", "none", last_status or "all_failed"
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


def failed_ai_judgement(reason="AI 判断失败，跳过推送"):
    return {
        "should_push": False,
        "category": "AI判断失败",
        "scope": "未判断",
        "risk": "低",
        "confidence": "低",
        "action": "可忽略",
        "reason": reason,
        "no_hype_title": "",
        "hype_warning": "",
    }


def should_rule_fallback_push(item, judge_status):
    """Agnes 不可用时的安全兜底：只推官方事故或中文 L 站强相关内容。"""
    if not RATE_LIMIT_LOCAL_FALLBACK_ENABLED:
        return False

    if judge_status not in ["429_rate_limited", "all_failed", "server_500", "server_502", "server_503", "server_504"]:
        return False

    score_info = item.get("score_info", {})
    score = item.get("score", 0)
    title = item.get("title", "")
    summary = item.get("summary", "")
    related_updates = filter_related_updates_for_card(title, summary, item.get("source", ""), item.get("related_updates", []))
    related_count = len(related_updates)
    theme = item.get("theme", "general")
    source_profile = score_info.get("source_profile", {})
    source_name = source_profile.get("name", "")

    if should_skip_when_ai_unavailable(title, summary, item.get("source", ""), score_info):
        return False

    if is_official_emergency(score_info):
        return True

    # 绝不兜底推 general 主题，避免“有趣网站/音乐/杂谈”混进来。
    if theme == "general" and not is_high_value_question(title, summary):
        return False

    # 用户最关心：PP / 接码 / 二验 / 401 / OAuth / Codex / CPA / Sub2API / 额度。
    if score >= RATE_LIMIT_LOCAL_FALLBACK_MIN_SCORE and score_info.get("has_preferred_signal"):
        return True

    # L站高质量问题反馈：分高 + 强主题，可以用规则兜底。
    if (
        score >= 78
        and (source_name.startswith("LINUX") or source_name == "Linux.do")
        and theme != "general"
        and (score_info.get("has_preferred_signal") or related_count >= 1)
    ):
        return True

    # 多条真正同类反馈，即使 AI 限流也可以推一条观察卡。
    if score >= 78 and related_count >= 2 and theme != "general":
        return True

    return False



def classify_local_category(title, summary):
    text = f"{title} {summary}".lower()
    if any(k in text for k in ["pp", "paypal", "gopay", "支付", "无卡"]):
        return "支付 / PP / 渠道风险"
    if any(k in text for k in ["接码", "手机号", "短信", "二验", "二次验证", "验证", "phone", "sms"]):
        return "账号验证 / 接码风险"
    if any(k in text for k in ["codex", "sub2api", "cpa", "401", "oauth", "token", "auth.json", "额度"]):
        return "Codex / 中转 / 额度异常"
    if any(k in text for k in ["claude code", "copilot", "cli", "插件", "agent", "mcp"]):
        return "AI 工具 / 工作流问题"
    return "社区风险观察"
def build_rate_limit_fallback_judgement(item, judge_status):
    score_info = item.get("score_info", {})
    related_count = len(filter_related_updates_for_card(item.get("title", ""), item.get("summary", ""), item.get("source", ""), item.get("related_updates", [])))
    scope = "社区多点反馈" if related_count else "单点反馈"

    if is_official_emergency(score_info):
        category = "官方服务异常"
        scope = "官方确认"
        risk = "高"
        confidence = "高"
        action = "需要立刻关注"
        reason = "该信息来自官方状态或公告，属于需要关注的服务异常。"
    else:
        category = classify_local_category(item.get("title", ""), item.get("summary", ""))
        risk = "中"
        confidence = "中"
        action = "暂时观察"
        reason = "该信息来自公开社区反馈，属于高相关风险信号，建议结合后续同类反馈观察。"

    return {
        "should_push": True,
        "category": category,
        "scope": scope,
        "risk": risk,
        "confidence": confidence,
        "action": action,
        "reason": reason,
        "no_hype_title": item.get("title", ""),
        "hype_warning": "",
    }


def ai_judge_news(title, summary, source, link, score_info, related_updates=None):
    if not AGNES_API_KEY:
        return failed_ai_judgement("AGNES_API_KEY 未配置，跳过推送"), "no_api_key"

    source_profile = score_info["source_profile"]
    related_updates = related_updates or []

    system_prompt = """你是一个 AI 情报雷达的第一层审核器。
你只输出 JSON，不输出任何解释文字。

任务：
判断一条公开 RSS / 社区信息是否值得推送到飞书 AI 情报群。

必须遵守：
1. 只能根据输入判断，不要编造。
2. 当前是中高质量模式：65 分以上有资格进入判断，但普通低价值内容不要推。你需要结合主信息和 related_updates 多条公开信息做综合判断。
3. 如果原文没有“大规模 / 多人 / 官方确认”，禁止判断为大规模事件。
4. 单个帖子、单个用户、疑似传言，scope 必须是“单点反馈”或“未确认”。
5. 涉及账号、封号、接码、OAuth、token、401、Plus、Pro、Codex、PP、无卡，只能做风险判断，不能提供绕风控、薅号、盗号、规避检测方法。
6. 普通产品发布、融资、观点、采访、论文、泛 AI 新闻，除非与 Codex / Claude Code / Copilot / Plus / PP / 接码 / 二验 / 风控 / 401 / OAuth 强相关，否则 should_push=false。
7. 不确定就降低 confidence。
8. 不要标题党。
9. Reddit 搜索结果如果只是普通聊天、提示词、娱乐、观点，不要推送。
10. 如果 score >= 88 且确实与核心主题相关，可以更积极推送。
11. 用户特别偏好这类情报：pp又复活、pp渠道疑似拉闸、注册成功瞬间变回free、gpt登录二次验证、手机号随机、手搓接码、巴西/智利/印尼/hero sms/WhatsApp 接码反馈。命中这些时更积极推送，但仍然要写成风险观察，不要写教程。
12. 如果 related_updates 里有同类反馈，需要综合判断是否形成同一事件；如果 2 条以上都指向账号验证、额度、401、接码、PP、Codex、CPA、Sub2API，可把 scope 评为“社区多点反馈”，但不能写官方确认。
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
- 65 分以上才有资格进入判断，是否推送由你结合多条 RSS 质量决定。
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

证据来源 / 同主题 RSS：
{build_evidence_text_for_prompt({"evidence_updates": related_updates}) if isinstance(related_updates, list) else json.dumps(related_updates, ensure_ascii=False)[:1800]}
"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    judgement, provider_name, provider_status = call_llm_json(messages, max_tokens=1200)

    if not judgement:
        print("AI judge failed: all providers failed")
        return failed_ai_judgement("Agnes 判断失败或限流，跳过推送，等待下一轮重试"), provider_status

    print(f"AI judge provider used: {provider_name}")

    return normalize_ai_judgement(judgement, score_info), "ok"



def is_mostly_english(text):
    text = text or ""
    letters = re.findall(r"[A-Za-z]", text)
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    return len(letters) >= 30 and len(letters) > len(cjk) * 2



INTERNAL_STATUS_WORDS = [
    "ModelScope", "Agnes", "限流", "429", "fallback", "规则兜底", "AI judge failed",
    "下一轮恢复", "英文待解读", "原文为英文", "待中文解读", "完整中文解读会在"
]

QUESTION_WORDS = [
    "怎么", "如何", "有没有", "求助", "请教", "能不能", "为什么", "怎么办", "咋", "吗", "？", "?",
    "help", "how to", "why", "anyone", "does anyone", "what should", "how do i"
]

QUESTION_HIGH_VALUE_HINTS = [
    "codex", "claude code", "copilot", "sub2api", "cpa", "401", "403", "oauth",
    "token", "auth.json", "接码", "二验", "二次验证", "手机号", "额度", "限流", "pp", "paypal", "gopay"
]


def is_question_post(title, summary=""):
    text = f"{title} {summary}".lower()
    return any(word.lower() in text for word in QUESTION_WORDS)


def is_high_value_question(title, summary=""):
    text = f"{title} {summary}".lower()
    return is_question_post(title, summary) and any(h.lower() in text for h in QUESTION_HIGH_VALUE_HINTS)


def is_official_source_name(source_name):
    source_name = source_name or ""
    return any(marker in source_name for marker in ["Status", "OpenAI", "GitHub", "Copilot", "Claude Code Releases", "Gemini CLI Releases"])


def source_is_linux(source_name):
    source_name = (source_name or "").lower()
    return source_name.startswith("linux") or "linux do" in source_name or "linux.do" in source_name


def should_skip_when_ai_unavailable(title, summary, source, score_info):
    """AI 不可用时，不推英文社区内容；官方事故和中文 L 站高价值内容允许干净兜底。"""
    source_profile = score_info.get("source_profile", {}) if isinstance(score_info, dict) else {}
    source_name = source_profile.get("name", source or "")

    if is_official_emergency(score_info):
        return False

    if source_is_linux(source_name) and not is_mostly_english(f"{title} {summary}"):
        return False

    # Reddit / HN / 英文社区内容如果 AI 无法翻译总结，直接跳过，避免飞书出现英文和内部状态。
    if is_mostly_english(f"{title} {summary}"):
        return True

    # 非官方、非 L 站中文高价值内容，AI 不可用时也尽量跳过。
    return True


def strip_internal_status_text(text):
    text = text or ""
    lines = []
    for line in text.splitlines():
        if any(word in line for word in INTERNAL_STATUS_WORDS):
            continue
        lines.append(line)
    return "\n".join(lines).strip()

def humanize_title_to_chinese(title, score_info=None):
    title = clean_html(title or "")
    if not title:
        return "未命名情报"

    lower = title.lower()

    replacements = [
        ("claude status site", "Claude 状态页面使用反馈"),
        ("incident with actions and pages", "GitHub Actions 与 Pages 服务异常"),
        ("actions is experiencing degraded availability", "GitHub Actions 可用性下降"),
        ("elevated errors", "错误率升高"),
        ("rate limit", "限流异常"),
        ("billing", "计费问题"),
        ("verification", "验证问题"),
        ("text message", "短信验证"),
        ("suspended", "账号暂停"),
        ("banned", "账号封禁"),
    ]
    for key, value in replacements:
        if key in lower:
            if is_mostly_english(title):
                return f"{value}\n原题：{short_text(title, 120)}"
            return value

    if is_mostly_english(title):
        return f"英文社区反馈\n原题：{short_text(title, 120)}"

    return title


def chinese_safe_summary(summary):
    summary = clean_html(summary or "")
    if not summary:
        return "来源摘要较短，建议点开原帖查看完整上下文。"
    if is_mostly_english(summary):
        return "该信息来自英文社区，当前只保留标题级风险信号；完整中文解读需要等待 AI 正常返回后再生成。"
    return short_text(summary, 460)


def filter_related_updates_for_card(title, summary, source, related_updates):
    related_updates = related_updates or []
    if not related_updates:
        return []

    main_theme = get_event_theme(title, summary)
    main_parts = set(main_theme.split("+")) if main_theme != "general" else set()
    result = []
    seen_titles = set()

    for update in related_updates:
        u_title = update.get("title", "")
        u_source = update.get("source", "")
        if not u_title or u_title in seen_titles:
            continue

        # 飞书同类补充不要出现内部状态或英文待解读类标题。
        if any(word in u_title for word in INTERNAL_STATUS_WORDS):
            continue

        # fallback 卡片里不展示英文 Reddit/HN 补充，避免用户看不懂。
        if is_mostly_english(u_title) and not (("Status" in source or "status" in source) and source == u_source):
            continue

        u_theme = get_event_theme(u_title, "")
        u_parts = set(u_theme.split("+")) if u_theme != "general" else set()
        overlap = main_parts & u_parts
        title_sim = similarity(title, u_title)

        official_same_source = ("Status" in source or "status" in source) and source == u_source
        strong_overlap = len(overlap) >= 2 or any(part in overlap for part in ["mfa", "phone_verification", "codex", "quota", "team", "token_401"])

        if official_same_source or title_sim >= 0.64 or strong_overlap:
            result.append(update)
            seen_titles.add(u_title)

        if len(result) >= MAX_RELATED_UPDATES_IN_CARD:
            break

    return result


def fallback_message(title, summary, source, link, score_info, ai_judgement, published_time="未知", related_updates=None):
    related_updates = filter_related_updates_for_card(title, summary, source, related_updates or [])

    level = score_info["level"]
    score = score_info["score"]
    rating = score_info["rating"]
    source_profile = score_info["source_profile"]

    action = ai_judgement.get("action") or score_info["action"]
    risk = ai_judgement.get("risk", "中")
    confidence = ai_judgement.get("confidence", "中")
    scope = ai_judgement.get("scope", "未确认")
    category = ai_judgement.get("category", "其他")
    reason = ai_judgement.get("reason", "公开来源自动抓取")
    title_text = ai_judgement.get("no_hype_title") or title
    title_text = humanize_title_to_chinese(title_text, score_info)

    if level == "爆炸":
        prefix = "🚨"
    elif level == "高":
        prefix = "📌"
    else:
        prefix = "🟡"

    safe_summary = chinese_safe_summary(summary)

    related_text = ""
    if related_updates:
        lines = []
        for update in related_updates[:MAX_RELATED_UPDATES_IN_CARD]:
            u_title = humanize_title_to_chinese(update.get("title", ""))
            lines.append(f"- {update.get('published_time', '未知')}：{short_text(u_title, 90)}")
        related_text = "\n\n💬 同类补充：\n" + "\n".join(lines)

    impact_lines = []
    theme = get_event_theme(title, summary)
    if "codex" in theme:
        impact_lines.append("- 可能影响 Codex 使用、额度判断或相关自动化工作流。")
    if "mfa" in theme or "phone_verification" in theme:
        impact_lines.append("- 可能影响登录验证、手机号验证、接码成功率或账号稳定性。")
    if "token_401" in theme:
        impact_lines.append("- 可能影响 OAuth / token / 401 相关调用稳定性。")
    if "quota" in theme:
        impact_lines.append("- 可能影响额度、限流、计费或中转可用性。")
    if not impact_lines:
        impact_lines.append("- 当前只作为公开来源风险观察，是否影响你需要结合后续反馈确认。")

    today_advice = ""
    if level in ["爆炸", "高"] or category in ["账号风控 / 额度 / 接码 / 工具异常", "官方服务异常"]:
        today_advice = f"\n\n🧭 今日建议：\n- 先观察下一轮 RSS / 社区反馈；如果涉及账号、接码、401、额度或中转，暂时不要高频折腾。\n- 来源：{source}\n- 发布时间：{published_time}"

    message = f"""{prefix} {short_text(title_text, 100)}

{level_icon(level)} 兴趣等级：{level}
🔥 评分：{score}/100
🏷 评级：{rating}
📌 建议：{action}

📝 变化：
- {safe_summary}

🔎 关键信息：
- 类型：{category}
- 范围：{scope}
- 来源类型：{source_profile["type"]}

🎯 可能影响：
{chr(10).join(impact_lines)}

🧯 风险判断：{risk}
- {reason}{today_advice}{related_text}

可信度：{confidence}
理由：来自公开来源，当前按来源可信度、主题相关性和同类反馈数量综合判断。

来源：{source}
发布时间：{published_time}
链接：{link}"""

    return strip_internal_status_text(message)

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
    related_updates = filter_related_updates_for_card(title, summary, source, related_updates or [])

    level = score_info["level"]
    score = score_info["score"]
    rating = score_info["rating"]

    action = ai_judgement.get("action") or score_info["action"]
    risk = ai_judgement.get("risk", "中")
    confidence = ai_judgement.get("confidence", "中")
    scope = ai_judgement.get("scope", "未确认")
    category = ai_judgement.get("category", "其他")
    no_hype_title = ai_judgement.get("no_hype_title", "")

    display_title = humanize_title_to_chinese(no_hype_title or title, score_info)

    if not AGNES_API_KEY:
        print("No Agnes API key set, use fallback message.")
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

是否提问帖：{is_question_post(title, summary)}
是否高价值问题：{is_high_value_question(title, summary)}

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
        7600
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
17. 如果标题或正文是英文，必须翻译成中文并解释，不要直接粘贴英文长段。
18. 如果这是提问帖，必须增加“🧠 AI 综合回答”板块：结合原帖、同类合并反馈和公开来源，给出当前最可能的答案、可能原因和建议。证据不足时要明确写“当前只能按单点反馈判断”。
19. “🧠 AI 综合回答”不能输出违规教程；涉及接码、绕风控、薅号、盗号、规避检测时，只做风险解释和安全建议。
18. 标题必须让中文用户一眼看懂发生了什么；如果原题是英文，可以在标题下保留“原题：...”。
19. 飞书正文不要显示“触发原因、来源权威、核心主题+18、规则命中”等程序调试日志。
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
- 用中文写清楚发生了什么；英文原文必须翻译，不要直接粘贴英文长段

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

🧠 AI 综合回答：
- 只有提问帖才写。结合当前原帖和同类反馈，直接回答“怎么回事 / 可能原因 / 建议怎么处理”。非提问帖不要写这个板块。

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

    body, provider_name, provider_status = call_llm_text(messages, max_tokens=1800)

    if not body:
        print("Summary failed: all providers failed")
        return ""

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
    # 不固定 3 条或 5 条。
    # 分数越高，允许推送越多；低分内容自动少推。
    if sent_count >= HARD_SEND_LIMIT:
        return True

    # 90+ 高价值内容，不受软限制影响
    if next_score >= 90:
        return False

    # 80+ 中高价值内容，最多允许到 10 条
    if next_score >= 80 and sent_count < 10:
        return False

    # 70+ 普通高质量内容，最多允许到 6 条
    if next_score >= 70 and sent_count < 6:
        return False

    # 65+ 只允许少量进入
    if next_score >= 65 and sent_count < 3:
        return False

    return True


def should_merge_candidates(a, b):
    if not MERGE_SIMILAR_EVENTS:
        return False

    a_theme = a.get("theme", "general")
    b_theme = b.get("theme", "general")
    a_title = a.get("title", "")
    b_title = b.get("title", "")
    a_source = a.get("source", "")
    b_source = b.get("source", "")

    # 官方状态源只和同一官方源 / 高相似标题合并，避免混进社区杂帖。
    official_markers = ["Status", "status", "Incident", "incident"]
    if any(m in a_source for m in official_markers) or any(m in b_source for m in official_markers):
        if a_source == b_source and similarity(a_title, b_title) >= 0.38:
            return True
        if similarity(a_title, b_title) >= 0.72:
            return True
        return False

    # 泛 pp / free / token 词太容易误合并，必须满足更强条件。
    weak_broad_parts = {"pp", "free", "token_401", "general"}

    if a_theme != "general" and b_theme != "general":
        a_parts = set(a_theme.split("+"))
        b_parts = set(b_theme.split("+"))
        overlap = a_parts & b_parts

        # 只有单个宽泛主题重叠，不直接合并。
        if len(overlap) == 1 and list(overlap)[0] in weak_broad_parts:
            return similarity(a_title, b_title) >= 0.58

        # 两个以上主题重叠，或同为强主题，才合并。
        strong_parts = {"phone_verification", "mfa", "codex", "team", "quota", "claude"}
        if len(overlap) >= 2:
            return True
        if overlap and any(part in strong_parts for part in overlap) and similarity(a_title, b_title) >= 0.25:
            return True

    if a_theme != "general" and a_theme == b_theme:
        if similarity(a_title, b_title) >= 0.45:
            return True

        a_text = f"{a_title} {a.get('summary', '')}"
        b_text = f"{b_title} {b.get('summary', '')}"

        if similarity(a_text, b_text) >= 0.82:
            return True

    if similarity(a_title, b_title) >= 0.86:
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



# =========================
# 多 RSS 主题聚合：一条情报融合多条 RSS / 多个帖子
# =========================
MAX_CLUSTERS_TO_JUDGE = 10
MAX_EVIDENCE_PER_CLUSTER = 6
MIN_CLUSTER_PUSH_SCORE = 62


def cluster_topic_from_text(title, summary, source=""):
    text = f"{title} {summary} {source}".lower()

    official_words = ["status", "incident", "outage", "degraded", "history.rss", "官方状态", "github status", "openai status", "anthropic status"]
    if any(w in text for w in official_words):
        return "official_incident"

    if any(w in text for w in ["封号", "被封", "封禁", "禁用", "suspended", "banned", "disabled", "terminated", "recovering account", "账号恢复"]):
        return "account_ban"

    if any(w in text for w in ["二次验证", "二验", "三次验证", "手机号", "手机验证", "短信", "接码", "sms", "text message", "phone verification", "whatsapp", "hero sms"]):
        return "phone_verification"

    if any(w in text for w in ["pp", "paypal", "gopay", "无卡", "支付", "扣款", "订阅", "plus", "pro", "充值", "续费", "变回free", "手搓"]):
        return "pp_payment"

    if any(w in text for w in ["401", "403", "oauth", "access token", "refresh token", "auth.json", "session", "json登陆", "json 登录", "凭证"]):
        return "token_oauth"

    if any(w in text for w in ["sub2api", "cpa", "中转", "号池", "free号池", "分组", "new api", "one api", "api额度", "额度池"]):
        return "cpa_sub2api"

    if "codex" in text:
        return "codex_issue"

    if any(w in text for w in ["claude code", "claude cli", "cc ", "ccs", "opus", "sonnet", "max", "anthropic"]):
        return "claude_code"

    if any(w in text for w in ["copilot", "github copilot", "coding agent"]):
        return "copilot_issue"

    if any(w in text for w in ["agent", "agents.md", "mcp", "skill", "workflow", "工作流", "插件", "多agent", "多 agent", "自动化"]):
        return "ai_tool_tip"

    if any(w in text for w in ["公益", "共享", "免费", "低价", "福利", "渠道", "拼团", "车", "随便蹬", "薅"]):
        return "channel_signal"

    return "general"


def cluster_topic_label(topic):
    labels = {
        "official_incident": "官方事故 / 服务状态",
        "account_ban": "账号封禁 / 恢复账号",
        "phone_verification": "手机号验证 / 接码 / 二验",
        "pp_payment": "Plus / Pro / PP / 支付订阅",
        "token_oauth": "OAuth / Token / JSON / 401",
        "cpa_sub2api": "CPA / Sub2API / 中转 / 号池",
        "codex_issue": "Codex 使用 / 额度 / 桌面端",
        "claude_code": "Claude Code / Claude 订阅",
        "copilot_issue": "GitHub Copilot / Coding Agent",
        "ai_tool_tip": "AI 工具技巧 / 工作流",
        "channel_signal": "渠道 / 福利 / 共享 / 低成本",
        "general": "普通 AI 信息",
    }
    return labels.get(topic, topic)


def is_strong_cluster_item(item):
    score = int(item.get("score", 0))
    topic = item.get("cluster_topic") or cluster_topic_from_text(item.get("title", ""), item.get("summary", ""), item.get("source", ""))
    if topic in ["official_incident", "account_ban", "phone_verification", "pp_payment", "token_oauth", "cpa_sub2api", "codex_issue"]:
        return score >= 62
    if topic in ["claude_code", "copilot_issue", "channel_signal"]:
        return score >= 68
    if topic == "ai_tool_tip":
        return score >= 76
    return score >= 86


def cluster_candidates_for_ai(candidates):
    """把多个 RSS 候选合并为少量主题 cluster，避免一条 RSS 一条推。"""
    clusters = {}

    for item in candidates:
        topic = cluster_topic_from_text(item.get("title", ""), item.get("summary", ""), item.get("source", ""))
        item["cluster_topic"] = topic

        # 普通 general 只保留极高分，避免杂乱
        if topic == "general" and item.get("score", 0) < 86:
            continue

        clusters.setdefault(topic, []).append(item)

    cluster_items = []

    for topic, items in clusters.items():
        items.sort(key=lambda x: x.get("score", 0), reverse=True)
        main = dict(items[0])
        main["cluster_topic"] = topic
        main["cluster_label"] = cluster_topic_label(topic)

        evidence = []
        seen_links = set()

        for item in items:
            link = item.get("link", "")
            if link in seen_links:
                continue
            seen_links.add(link)
            evidence.append({
                "title": item.get("title", ""),
                "source": item.get("source", ""),
                "link": item.get("link", ""),
                "published_time": item.get("published_time", "未知"),
                "score": item.get("score", 0),
                "topic": topic,
            })

        # 合并原先 related_updates，但严格限制数量和相关性
        for item in items:
            for rel in item.get("related_updates", []) or []:
                link = rel.get("link", "")
                if link and link in seen_links:
                    continue
                rel_topic = cluster_topic_from_text(rel.get("title", ""), "", rel.get("source", ""))
                if rel_topic != topic:
                    continue
                seen_links.add(link)
                evidence.append({
                    "title": rel.get("title", ""),
                    "source": rel.get("source", ""),
                    "link": rel.get("link", ""),
                    "published_time": rel.get("published_time", "未知"),
                    "score": rel.get("score", 0),
                    "topic": topic,
                })

        evidence = evidence[:MAX_EVIDENCE_PER_CLUSTER]
        main["related_updates"] = evidence[1:]
        main["evidence_updates"] = evidence
        main["cluster_size"] = len(evidence)

        # 多条证据给聚合加分，但不超过 94，避免夸张
        if len(evidence) >= 2:
            bonus = min(8, (len(evidence) - 1) * 2)
            main["score"] = min(94, int(main.get("score", 0)) + bonus)
            try:
                main["score_info"]["score"] = main["score"]
                main["score_info"].setdefault("reasons", []).append(f"同主题多源证据 +{bonus}：{len(evidence)} 条")
            except Exception:
                pass

        if main.get("score", 0) >= MIN_CLUSTER_PUSH_SCORE and is_strong_cluster_item(main):
            cluster_items.append(main)

    cluster_items.sort(key=lambda x: (x.get("priority", 3), -x.get("score", 0), -x.get("cluster_size", 1)))
    return cluster_items[:MAX_CLUSTERS_TO_JUDGE]


def build_evidence_text_for_prompt(item):
    evidence = item.get("evidence_updates") or []
    if not evidence:
        return "无"

    lines = []
    for idx, ev in enumerate(evidence[:MAX_EVIDENCE_PER_CLUSTER], start=1):
        lines.append(
            f"{idx}. 时间：{ev.get('published_time', '未知')}｜来源：{ev.get('source', '')}｜标题：{short_text(ev.get('title', ''), 120)}｜链接：{ev.get('link', '')}"
        )
    return "\n".join(lines)

def should_mark_seen_when_skipped(score_info, published_dt, title, summary):
    age_minutes = get_age_minutes(published_dt)

    # 黑名单 / 明显低分垃圾，直接 seen。
    if score_info.get("block_hits"):
        return True

    if score_info["score"] < 38:
        return True

    # 超过 2 小时旧内容，直接 seen。
    if age_minutes is not None and age_minutes > OLD_ITEM_GRACE_HOURS * 60:
        return True

    # 强偏好但刚发不久，先不要 seen，允许后面 30～60 分钟评论发酵后复扫。
    if has_preferred_signal(title, summary) and age_minutes is not None and age_minutes <= 30:
        return False

    return True


def main():
    print("AGNES_API_KEY set:", bool(AGNES_API_KEY))
    print("AGNES_BASE_URL:", AGNES_BASE_URL)
    print("AGNES_MODEL:", AGNES_MODEL)
    print("MODELSCOPE_DISABLED:", True)
    print("OFFICIAL_DEEPSEEK_DISABLED:", True)
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
    print("RATE_LIMIT_LOCAL_FALLBACK_ENABLED:", RATE_LIMIT_LOCAL_FALLBACK_ENABLED)
    print("RATE_LIMIT_LOCAL_FALLBACK_MIN_SCORE:", RATE_LIMIT_LOCAL_FALLBACK_MIN_SCORE)
    print("RATE_LIMIT_LOCAL_FALLBACK_MAX_SEND:", RATE_LIMIT_LOCAL_FALLBACK_MAX_SEND)
    print("CHINESE_OUTPUT_ENFORCED:", True)
    print("FEISHU_INTERNAL_STATUS_HIDDEN:", True)
    print("QUESTION_POST_AI_ANSWER_ENABLED:", True)
    print("ENGLISH_COMMUNITY_REQUIRES_AI:", True)
    print("STRICT_RELATED_FILTER_ENABLED:", True)
    print("MERGE_SIMILAR_EVENTS:", MERGE_SIMILAR_EVENTS)
    print("CAP_LINUX_SINGLE_PREFERRED:", CAP_LINUX_SINGLE_PREFERRED)
    print("CAP_DELETED_OR_INCOMPLETE:", CAP_DELETED_OR_INCOMPLETE)
    print("Cron recommended: 0 0,3,6,8,10,12,14,16,18,20,22 * * *")

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
    merged_candidate_count = len(candidates)
    candidates = cluster_candidates_for_ai(candidates)

    print(f"Candidates before merge: {original_candidate_count}")
    print(f"Candidates after merge: {merged_candidate_count}")
    print(f"Candidates after cluster: {len(candidates)}")

    sent_count = 0
    judged_count = 0
    rate_limit_fallback_sent = 0

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

        if judge_status in ["429_rate_limited", "all_failed", "no_api_key"]:
            skipped_ai_count += 1
            print(f"AI judge failed: {short_text(item['title'], 80)} | status={judge_status} | reason={ai_judgement.get('reason')}")

            # 429 / all_failed 时不要继续硬撞魔搭。
            # 但对官方紧急事故、L站高分强相关、多条同类反馈，允许规则兜底推送。
            if should_rule_fallback_push(item, judge_status) and rate_limit_fallback_sent < RATE_LIMIT_LOCAL_FALLBACK_MAX_SEND:
                fallback_judgement = build_rate_limit_fallback_judgement(item, judge_status)
                message = fallback_message(
                    item["title"],
                    item["summary"],
                    item["source"],
                    item["link"],
                    item["score_info"],
                    fallback_judgement,
                    item.get("published_time", "未知"),
                    item.get("related_updates", []),
                )
                success = send_feishu(message)

                # 兜底推送成功才写 seen；失败则下一轮重试。
                if success:
                    new_seen.add(item["uid"])
                    for related in filter_related_updates_for_card(item["title"], item["summary"], item["source"], item.get("related_updates", [])):
                        if related.get("link"):
                            new_seen.add(item_id(related.get("title", ""), related.get("link", "")))
                    sent_count += 1
                    rate_limit_fallback_sent += 1
                    print(f"Rule fallback pushed due to Agnes failure: {short_text(item['title'], 80)}")

                # 不再继续调用模型，但继续看后面是否还有可规则兜底的高价值候选。
                continue

            if rate_limit_fallback_sent >= RATE_LIMIT_LOCAL_FALLBACK_MAX_SEND:
                print("Stop this run: rate-limit fallback max reached. Will retry next cron run.")
                break

            print("Skip this item because Agnes failed/rate-limited. Continue checking next candidate for safe rule fallback.")
            continue

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

        if not message:
            skipped_ai_count += 1
            print(f"Summary failed: {short_text(item['title'], 80)}")

            # AI judge 已经通过，但总结阶段限流时，用规则兜底卡片补发，避免高价值信息完全静默。
            if should_rule_fallback_push(item, "429_rate_limited"):
                message = fallback_message(
                    item["title"],
                    item["summary"],
                    item["source"],
                    item["link"],
                    item["score_info"],
                    ai_judgement,
                    item.get("published_time", "未知"),
                    related_updates,
                )
            else:
                print("Stop this run because Agnes summary failed. Will retry next cron run.")
                break

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
主题聚合后候选：{len(candidates)}
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
