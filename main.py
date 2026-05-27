import os
import time
import json
import hashlib
import re
from pathlib import Path
from difflib import SequenceMatcher

import requests
import feedparser


DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")

SEEN_FILE = Path("seen.json")


RSS_SOURCES = [
    # 中文社区 / 高价值情报源
    "https://linux.do/latest.rss",
    "https://linux.do/top.rss",
    "https://linux.do/posts.rss",

    # AIHOT：只加精选和日报，不加 all，避免刷屏
    "https://aihot.virxact.com/feed.xml",
    "https://aihot.virxact.com/feed/daily.xml",

    # 官方状态 / Codex
    "https://status.openai.com/history.rss",
    "https://github.com/openai/codex/issues.atom",

    # Reddit 社区反馈
    "https://www.reddit.com/r/ClaudeAI/search.rss?q=Claude%20Code%20OR%20HERMES.md%20OR%20billing%20OR%20refund&restrict_sr=1&sort=new",
    "https://www.reddit.com/r/OpenAI/search.rss?q=Codex%20OR%20rate%20limit%20OR%20banned%20OR%20suspended%20OR%20verification&restrict_sr=1&sort=new",
    "https://www.reddit.com/r/ChatGPT/search.rss?q=Plus%20OR%20banned%20OR%20suspended%20OR%20verification%20OR%20text%20message&restrict_sr=1&sort=new",
    "https://www.reddit.com/r/GitHubCopilot/search.rss?q=student%20OR%20model%20OR%20Codex%20OR%20Claude&restrict_sr=1&sort=new",

    # Hacker News 关键词
    "https://hnrss.org/newest?q=OpenAI",
    "https://hnrss.org/newest?q=Claude",
    "https://hnrss.org/newest?q=Codex",
    "https://hnrss.org/newest?q=Gemini",

    # 泛 AI 新闻，权重较低
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://huggingface.co/blog/feed.xml",
]


# 直接屏蔽：命中就不推
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


# 爆炸事件词：严重异常、封号、全线失效、官方 outage
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
]


# 你最关心的核心主题
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
    "Claude Code",
    "HERMES.md",
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


# 中等兴趣：普通 AI 产品、模型、工具
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


# 低价值泛新闻：不直接屏蔽，但降权
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
]


# 单点反馈：降权，避免把小样本当大事件
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
    "one user",
    "single report",
    "unconfirmed",
    "seems",
    "maybe",
    "appears",
]


# 多人反馈 / 官方确认：升权
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


def load_seen():
    if not SEEN_FILE.exists():
        return set()

    try:
        return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
    except Exception:
        return set()


def save_seen(seen):
    SEEN_FILE.write_text(
        json.dumps(list(seen)[-1800:], ensure_ascii=False, indent=2),
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
    return normalize_text(text)


def short_text(text, limit=2500):
    text = clean_html(text)

    if len(text) <= limit:
        return text

    return text[:limit] + "..."


def item_id(title, link):
    raw = f"{title}|{link}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def text_signature(title):
    title = clean_html(title).lower()
    title = re.sub(r"https?://\S+", "", title)
    title = re.sub(r"[\W_]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def is_similar_title(title, existing_titles, threshold=0.84):
    sig = text_signature(title)

    for old_title in existing_titles:
        old_sig = text_signature(old_title)

        if not sig or not old_sig:
            continue

        ratio = SequenceMatcher(None, sig, old_sig).ratio()

        if ratio >= threshold:
            return True

    return False


def contains_keyword(text, keywords):
    lower = text.lower()
    return any(k.lower() in lower for k in keywords)


def matched_keywords(text, keywords):
    lower = text.lower()
    return [k for k in keywords if k.lower() in lower]


def get_source_profile(source, rss_url):
    text = f"{source} {rss_url}".lower()

    if "status.openai.com" in text:
        return {
            "name": "OpenAI Status",
            "authority": 25,
            "type": "官方状态",
        }

    if "github.com/openai/codex" in text:
        return {
            "name": "OpenAI Codex GitHub Issues",
            "authority": 22,
            "type": "官方仓库 / 用户反馈",
        }

    if "linux.do" in text:
        return {
            "name": "Linux.do",
            "authority": 18,
            "type": "中文社区",
        }

    if "aihot.virxact.com/feed.xml" in text:
        return {
            "name": "AIHOT 精选",
            "authority": 18,
            "type": "中文精选聚合",
        }

    if "aihot.virxact.com/feed/daily.xml" in text:
        return {
            "name": "AIHOT 日报",
            "authority": 16,
            "type": "中文日报聚合",
        }

    if "reddit.com" in text:
        return {
            "name": "Reddit",
            "authority": 14,
            "type": "海外社区",
        }

    if "hnrss.org" in text:
        return {
            "name": "Hacker News",
            "authority": 12,
            "type": "技术社区",
        }

    if "huggingface.co" in text:
        return {
            "name": "HuggingFace Blog",
            "authority": 12,
            "type": "官方/技术博客",
        }

    if "theverge.com" in text:
        return {
            "name": "The Verge AI",
            "authority": 8,
            "type": "泛 AI 媒体",
        }

    return {
        "name": source or rss_url,
        "authority": 8,
        "type": "公开 RSS",
    }


def clamp_score(score):
    return max(0, min(100, int(round(score))))


def level_from_score(score, has_critical, has_strong_evidence):
    if score >= 90 and has_critical and has_strong_evidence:
        return "爆炸"

    if score >= 75:
        return "高"

    if score >= 55:
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
    if score >= 90:
        return "S"
    if score >= 75:
        return "A"
    if score >= 55:
        return "B"
    if score >= 40:
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


def score_news(title, summary, source, rss_url):
    text = f"{title} {summary}"

    source_profile = get_source_profile(source, rss_url)

    critical_hits = matched_keywords(text, CRITICAL_EVENT_KEYWORDS)
    core_hits = matched_keywords(text, CORE_TOPIC_KEYWORDS)
    medium_hits = matched_keywords(text, MEDIUM_TOPIC_KEYWORDS)
    single_hits = matched_keywords(text, SINGLE_REPORT_KEYWORDS)
    multi_hits = matched_keywords(text, MULTI_REPORT_KEYWORDS)
    low_value_hits = matched_keywords(text, LOW_VALUE_KEYWORDS)
    block_hits = matched_keywords(text, BLOCK_KEYWORDS)

    score = 0
    reasons = []

    # 1. 来源权威度，最高 25
    source_score = source_profile["authority"]
    score += source_score
    reasons.append(f"来源权威 {source_score}")

    # 2. 主题相关度，最高 30
    topic_score = 0

    if core_hits:
        topic_score += 22
        reasons.append(f"核心主题 +22：{', '.join(core_hits[:5])}")

    if medium_hits:
        topic_score += 10
        reasons.append(f"普通 AI 主题 +10：{', '.join(medium_hits[:4])}")

    if len(core_hits) >= 2:
        topic_score += 5
        reasons.append("多个核心主题 +5")

    topic_score = min(topic_score, 30)
    score += topic_score

    # 3. 事件严重度，最高 25
    severity_score = 0

    if critical_hits:
        severity_score += 25
        reasons.append(f"严重事件 +25：{', '.join(critical_hits[:4])}")
    elif any(k.lower() in text.lower() for k in ["rate limit", "quota", "billing", "refund", "verification"]):
        severity_score += 12
        reasons.append("额度/计费/验证异常 +12")
    elif core_hits:
        severity_score += 8
        reasons.append("核心主题一般事件 +8")

    severity_score = min(severity_score, 25)
    score += severity_score

    # 4. 证据强度，范围 -12 到 +15
    evidence_score = 0
    has_strong_evidence = False

    if source_profile["authority"] >= 22:
        evidence_score += 12
        has_strong_evidence = True
        reasons.append("官方/官方仓库来源 +12")

    if multi_hits:
        evidence_score += 10
        has_strong_evidence = True
        reasons.append(f"多人/确认反馈 +10：{', '.join(multi_hits[:3])}")

    if single_hits:
        evidence_score -= 10
        reasons.append(f"单点/未确认反馈 -10：{', '.join(single_hits[:3])}")

    evidence_score = max(-12, min(15, evidence_score))
    score += evidence_score

    # 5. 鲜度/聚合质量，最高 5
    freshness_score = 0

    if "aihot.virxact.com/feed.xml" in rss_url:
        freshness_score += 5
        reasons.append("AIHOT 精选 +5")
    elif "aihot.virxact.com/feed/daily.xml" in rss_url:
        freshness_score += 4
        reasons.append("AIHOT 日报 +4")
    elif "latest" in rss_url or "newest" in rss_url or "issues.atom" in rss_url:
        freshness_score += 4
        reasons.append("最新流 +4")
    elif "top" in rss_url:
        freshness_score += 3
        reasons.append("热门流 +3")

    freshness_score = min(freshness_score, 5)
    score += freshness_score

    # 6. 噪音惩罚
    penalty = 0

    if block_hits:
        penalty += 100
        reasons.append(f"黑名单命中 -100：{', '.join(block_hits[:4])}")

    if low_value_hits:
        penalty += 12
        reasons.append(f"泛新闻降权 -12：{', '.join(low_value_hits[:3])}")

    # 泛 AI 媒体 + 没有核心主题，额外降权
    if source_profile["authority"] <= 12 and not core_hits and medium_hits:
        penalty += 15
        reasons.append("低权重泛 AI 新闻且无核心主题 -15")

    # 只有普通 AI，完全没有你关心的账号/Codex/风控类主题，降权
    if medium_hits and not core_hits and not critical_hits:
        penalty += 10
        reasons.append("仅普通 AI 主题 -10")

    score -= penalty

    score = clamp_score(score)

    has_critical = bool(critical_hits)

    # 高权威官方源也算强证据
    if source_profile["authority"] >= 22:
        has_strong_evidence = True

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
        "has_critical": has_critical,
        "has_strong_evidence": has_strong_evidence,
    }


def should_skip(title, summary, score_info):
    text = f"{title} {summary}"

    if score_info["block_hits"]:
        return True, "命中黑名单"

    if score_info["score"] < 55:
        return True, f"评分低于推送线：{score_info['score']}"

    if score_info["level"] == "低":
        return True, "低兴趣内容"

    # 单点反馈可以推，但必须分数足够高
    if score_info["single_hits"] and score_info["score"] < 70:
        return True, f"单点反馈且分数不足：{score_info['score']}"

    # 普通 AI 新闻必须达到较高分才推
    if score_info["medium_hits"] and not score_info["core_hits"] and score_info["score"] < 75:
        return True, "普通 AI 新闻分数不足"

    return False, ""


def fetch_feed(url):
    headers = {
        "User-Agent": "Mozilla/5.0 AI-Radar-Bot/2.0"
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return feedparser.parse(response.text)
    except Exception as e:
        print(f"Failed to fetch {url}: {e}")
        return None


def fallback_message(title, summary, source, link, score_info):
    level = score_info["level"]
    score = score_info["score"]
    rating = score_info["rating"]
    action = score_info["action"]
    reasons = score_info["reasons"]
    source_profile = score_info["source_profile"]

    if level == "爆炸":
        prefix = "🚨【爆炸新闻】"
    elif level == "高":
        prefix = "🔴【高关注】"
    else:
        prefix = "🟡【观察】"

    reason_text = "、".join(reasons[:5]) if reasons else "公开来源自动抓取"

    return f"""{prefix}{short_text(title, 100)}

{level_icon(level)} 兴趣等级：{level}
🔥 评分：{score}/100
🏷 评级：{rating}
📌 建议：{action}

📝 变化：
- {short_text(summary, 500)}

🔎 关键信息：
- 来源类型：{source_profile["type"]}
- 来源权重：{source_profile["authority"]}/25
- 触发原因：{reason_text}

⚠️ 风险判断：中
- 当前为公开来源自动抓取，未完成多来源交叉验证。
- 涉及账号、额度、封号、接码的信息需要继续核实。

可信度：中
理由：来自公开 RSS，但未做多来源验证。

来源：{source}
链接：{link}"""


def deepseek_summarize(title, summary, source, link, score_info):
    level = score_info["level"]
    score = score_info["score"]
    rating = score_info["rating"]
    action = score_info["action"]

    if not DEEPSEEK_API_KEY:
        print("DEEPSEEK_API_KEY not set, use fallback message.")
        return fallback_message(title, summary, source, link, score_info)

    raw_content = short_text(
        f"""
标题：{title}
来源：{source}
摘要：{summary}
链接：{link}

系统评分：
兴趣等级：{level}
评分：{score}/100
评级：{rating}
建议：{action}
来源信息：{score_info.get("source_profile")}
触发原因：{score_info.get("reasons")}
严重事件命中：{score_info.get("critical_hits")}
核心主题命中：{score_info.get("core_hits")}
普通主题命中：{score_info.get("medium_hits")}
单点反馈命中：{score_info.get("single_hits")}
多人反馈命中：{score_info.get("multi_hits")}
""",
        4500
    )

    system_prompt = """你是一个飞书 AI 情报频道的中文编辑。
你的任务是把公开 RSS、社区帖子、新闻源内容，总结成短、准、清晰的中文情报卡片。

严格要求：
1. 只能根据用户给出的内容总结，不要编造未出现的信息。
2. 必须区分已知信息和不确定信息。
3. 涉及封号、接码、账号、风控、额度、OAuth、access token、refresh token、401、共享号、Plus/Pro 时，只做风险分析，不提供薅号、绕风控、盗号、规避检测教程。
4. 如果只是单点反馈，必须写“单点反馈 / 未确认”，不要说成大规模。
5. 如果没有评论内容，不要输出“评论补充”模块。
6. 不要输出 Markdown 表格。
7. 不要输出代码块。
8. 不要写“根据原文”“根据你提供的信息”这种废话。
9. 标题要像情报快讯，短、准，不标题党。
10. 输出适合飞书手机端阅读，不要太长。
"""

    user_prompt = f"""请把下面信息整理成固定格式。

系统判断：
- 兴趣等级：{level}
- 评分：{score}/100
- 评级：{rating}
- 建议：{action}

标题规则：
- 爆炸等级：标题前加 🚨【爆炸新闻】
- 高等级：标题前加 🔴【高关注】
- 观察等级：标题前加 🟡【观察】
- 标题尽量 30 字以内
- 不要夸大，不要编造

必须输出：

{level_icon(level)} 兴趣等级：{level}
🔥 评分：{score}/100
🏷 评级：{rating}
📌 建议：{action}

📝 变化：
- 用 1-3 条写清楚发生了什么

🔎 关键信息：
- 类型：账号风控 / Codex / Claude Code / Plus / 普通 AI 新闻 / 其他
- 范围：单点反馈 / 多人反馈 / 官方信息 / 未确认
- 影响：一句话说明影响

⚠️ 风险判断：低/中/高
- 用 1-3 条说明为什么
- 如果是单点反馈，不要直接判断为大规模事件

可信度：高/中/低
理由：一句话

来源：{source}
链接：{link}

原始内容：
{raw_content}
"""

    try:
        response = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.12,
                "max_tokens": 850,
                "stream": False,
            },
            timeout=60,
        )

        if response.status_code != 200:
            print("DeepSeek failed:", response.status_code, response.text[:500])
            return fallback_message(title, summary, source, link, score_info)

        data = response.json()
        content = data["choices"][0]["message"]["content"].strip()

        if f"评分：{score}/100" not in content:
            content = f"{level_icon(level)} 兴趣等级：{level}\n🔥 评分：{score}/100\n🏷 评级：{rating}\n📌 建议：{action}\n\n{content}"

        return content

    except Exception as e:
        print("DeepSeek exception:", e)
        return fallback_message(title, summary, source, link, score_info)


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


def main():
    print("DEEPSEEK_API_KEY set:", bool(DEEPSEEK_API_KEY))
    print("FEISHU_WEBHOOK set:", bool(FEISHU_WEBHOOK))
    print("FEISHU_WEBHOOK length:", len(FEISHU_WEBHOOK))

    seen = load_seen()
    new_seen = set(seen)

    candidates = []
    accepted_titles = []
    skipped_count = 0
    skipped_similar_count = 0

    for rss_url in RSS_SOURCES:
        feed = fetch_feed(rss_url)

        if not feed:
            continue

        source = feed.feed.get("title", rss_url)

        for entry in feed.entries[:12]:
            title = clean_html(entry.get("title", ""))
            link = entry.get("link", "")
            summary = clean_html(entry.get("summary", "") or entry.get("description", ""))

            if not title or not link:
                continue

            uid = item_id(title, link)

            if uid in seen:
                continue

            if is_similar_title(title, accepted_titles):
                skipped_similar_count += 1
                print(f"Skip similar: {short_text(title, 80)}")
                new_seen.add(uid)
                continue

            score_info = score_news(title, summary, source, rss_url)

            skip, skip_reason = should_skip(title, summary, score_info)

            if skip:
                skipped_count += 1
                print(f"Skip: {short_text(title, 80)} | {skip_reason}")
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
                "score_info": score_info,
            })

            accepted_titles.append(title)

    # 优先级：爆炸 > 高 > 观察；同级按评分高低
    candidates.sort(key=lambda item: (item["priority"], -item["score"]))

    sent_count = 0
    max_send_count = 5

    for item in candidates:
        message = deepseek_summarize(
            item["title"],
            item["summary"],
            item["source"],
            item["link"],
            item["score_info"],
        )

        success = send_feishu(message)

        new_seen.add(item["uid"])

        if success:
            sent_count += 1

        time.sleep(3)

        if sent_count >= max_send_count:
            break

    save_seen(new_seen)

    print(f"Candidates: {len(candidates)}")
    print(f"Skipped: {skipped_count}")
    print(f"Skipped similar: {skipped_similar_count}")
    print(f"Sent {sent_count} messages.")


if __name__ == "__main__":
    main()
