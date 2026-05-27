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
    # 中文社区 / 情报源
    "https://linux.do/latest.rss",
    "https://linux.do/top.rss",
    "https://linux.do/posts.rss",

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

    # 泛 AI 新闻
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://huggingface.co/blog/feed.xml",
]


# 直接屏蔽：不感兴趣 / 低价值 / 容易刷屏
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
    "裁员以外的普通招聘",
    "营销",
    "推广",
    "邀请码",
    "优惠码",
    "课程",
    "训练营",
    "卖课",
]


# 爆炸关键词：命中后会大幅加分，但不会无脑爆炸，还会结合来源/多人反馈/核心主题
BREAKING_KEYWORDS = [
    "大规模封号",
    "批量封号",
    "大规模封禁",
    "全线失效",
    "全部失效",
    "大面积异常",
    "登录失败",
    "无法登录",
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
]


# 高兴趣：账号、风控、Codex、Plus、Claude Code、OAuth
HIGH_KEYWORDS = [
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


# 中兴趣：普通 AI 产品 / 模型 / 算力
MEDIUM_KEYWORDS = [
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
]


# 低价值泛新闻：不是完全屏蔽，但降权
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
]


# 单点反馈词：自动降级，避免把小样本当爆炸新闻
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
]


# 多人反馈词：提高权重
MULTI_REPORT_KEYWORDS = [
    "多人反馈",
    "多位用户",
    "大量用户",
    "大批",
    "批量",
    "普遍",
    "集中反馈",
    "confirmed by multiple",
    "many users",
    "multiple users",
    "widespread",
]


# 必须关注主题：这些组合出现时更值得推送
CORE_TOPICS = [
    "401",
    "403",
    "OAuth",
    "refresh token",
    "access token",
    "Codex",
    "Claude Code",
    "Plus",
    "Pro",
    "Team",
    "Free",
    "封号",
    "被封",
    "接码",
    "短信验证",
    "rate limit",
    "quota",
    "weekly limit",
    "学生包",
    "GitHub Copilot",
    "Sub2API",
    "CPA",
    "Cockpit",
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
        json.dumps(list(seen)[-1500:], ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def normalize_text(text):
    return " ".join((text or "").replace("\n", " ").split())


def clean_html(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
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
    title = title.lower()
    title = re.sub(r"https?://\S+", "", title)
    title = re.sub(r"[\W_]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def is_similar_title(title, existing_titles, threshold=0.82):
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


def get_source_weight(source, rss_url):
    text = f"{source} {rss_url}".lower()

    if "status.openai.com" in text:
        return 5

    if "github.com/openai/codex" in text:
        return 4

    if "linux.do" in text:
        return 3

    if "reddit.com" in text:
        return 2

    if "hnrss.org" in text:
        return 1

    if "theverge.com" in text:
        return 0

    if "huggingface.co" in text:
        return 0

    return 0


def get_level_from_score(score, has_breaking, has_multi, source_weight, core_hits_count):
    # 更严格的爆炸机制：
    # 只有“爆炸关键词 + 多人/官方/高权重来源/多个核心主题”才判爆炸
    if has_breaking and (has_multi or source_weight >= 4 or core_hits_count >= 2) and score >= 11:
        return "爆炸"

    if score >= 8:
        return "高"

    if score >= 5:
        return "中"

    return "低"


def level_icon(level):
    if level == "爆炸":
        return "🚨"
    if level == "高":
        return "🔴"
    if level == "中":
        return "🟡"
    return "🟢"


def action_from_score(score, level):
    if level == "爆炸":
        return "需要立刻关注"
    if level == "高":
        return "需要关注"
    if level == "中":
        return "暂时观察"
    return "可忽略"


def score_news(title, summary, source, rss_url):
    text = f"{title} {summary}"
    score = 0
    reasons = []

    source_weight = get_source_weight(source, rss_url)
    score += source_weight

    if source_weight > 0:
        reasons.append(f"来源权重 +{source_weight}")

    breaking_hits = matched_keywords(text, BREAKING_KEYWORDS)
    high_hits = matched_keywords(text, HIGH_KEYWORDS)
    medium_hits = matched_keywords(text, MEDIUM_KEYWORDS)
    core_hits = matched_keywords(text, CORE_TOPICS)

    has_breaking = bool(breaking_hits)
    has_multi = contains_keyword(text, MULTI_REPORT_KEYWORDS)
    has_single = contains_keyword(text, SINGLE_REPORT_KEYWORDS)

    if breaking_hits:
        score += 7
        reasons.append(f"爆炸关键词 +7：{', '.join(breaking_hits[:3])}")

    if high_hits:
        score += 4
        reasons.append(f"高兴趣关键词 +4：{', '.join(high_hits[:4])}")

    if medium_hits:
        score += 2
        reasons.append(f"中兴趣关键词 +2：{', '.join(medium_hits[:3])}")

    if has_multi:
        score += 2
        reasons.append("多人反馈 +2")

    if has_single:
        score -= 2
        reasons.append("单点/未确认反馈 -2")

    if contains_keyword(text, LOW_VALUE_KEYWORDS):
        score -= 1
        reasons.append("泛新闻降权 -1")

    if contains_keyword(text, BLOCK_KEYWORDS):
        score -= 10
        reasons.append("命中黑名单 -10")

    if len(core_hits) >= 2:
        score += 2
        reasons.append(f"多个核心主题 +2：{', '.join(core_hits[:4])}")

    if len(core_hits) >= 3:
        score += 1
        reasons.append("核心主题密度高 +1")

    level = get_level_from_score(
        score=score,
        has_breaking=has_breaking,
        has_multi=has_multi,
        source_weight=source_weight,
        core_hits_count=len(core_hits),
    )

    return {
        "score": score,
        "level": level,
        "reasons": reasons,
        "breaking_hits": breaking_hits,
        "high_hits": high_hits,
        "medium_hits": medium_hits,
        "core_hits": core_hits,
        "source_weight": source_weight,
        "has_single": has_single,
        "has_multi": has_multi,
    }


def should_skip(title, summary, score_info):
    text = f"{title} {summary}"
    score = score_info["score"]
    level = score_info["level"]

    if contains_keyword(text, BLOCK_KEYWORDS):
        return True, "命中黑名单"

    if score < 5:
        return True, f"评分过低：{score}"

    if level == "低":
        return True, "低兴趣内容"

    # 只有泛 AI，没有高兴趣关键词，也没有核心主题，跳过
    if (
        score_info["medium_hits"]
        and not score_info["high_hits"]
        and not score_info["core_hits"]
        and score_info["source_weight"] <= 1
    ):
        return True, "泛 AI 新闻，缺少核心主题"

    return False, ""


def fetch_feed(url):
    headers = {
        "User-Agent": "Mozilla/5.0 AI-Radar-Bot/1.0"
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
    reasons = score_info["reasons"]
    action = action_from_score(score, level)

    if level == "爆炸":
        prefix = "🚨【爆炸新闻】"
    elif level == "高":
        prefix = "🔴【高关注】"
    else:
        prefix = "🟡【观察】"

    reason_text = "、".join(reasons[:4]) if reasons else "公开来源自动抓取"

    return f"""{prefix}{short_text(title, 120)}

{level_icon(level)} 兴趣等级：{level}
🔥 评分：{score}

📝 变化：
- {short_text(summary, 500)}

🔎 关键信息：
- 类型：AI / 账号风控 / Codex / Claude Code / Plus / 其他
- 范围：公开来源，未完成多源交叉验证
- 触发原因：{reason_text}

⚠️ 风险判断：中
- 只能当作公开线索，涉及账号、额度、封号、接码的信息需要继续核实。

📌 建议：
- {action}
- 不要基于单条反馈立刻调整全部账号策略。

可信度：中
理由：来自公开 RSS，但未做多来源验证。

来源：{source}
链接：{link}"""


def deepseek_summarize(title, summary, source, link, score_info):
    level = score_info["level"]
    score = score_info["score"]
    reasons = score_info["reasons"]
    action = action_from_score(score, level)

    if not DEEPSEEK_API_KEY:
        print("DEEPSEEK_API_KEY not set, use fallback message.")
        return fallback_message(title, summary, source, link, score_info)

    raw_content = short_text(
        f"""
标题：{title}
来源：{source}
摘要：{summary}
链接：{link}
兴趣等级：{level}
评分：{score}
系统建议：{action}
触发原因：{reasons}
核心命中：{score_info.get("core_hits", [])}
爆炸命中：{score_info.get("breaking_hits", [])}
高兴趣命中：{score_info.get("high_hits", [])}
是否单点反馈：{score_info.get("has_single")}
是否多人反馈：{score_info.get("has_multi")}
""",
        4200
    )

    system_prompt = """你是一个 Telegram / 飞书 AI 情报频道的中文编辑。
你的任务是把公开 RSS、社区帖子、新闻源内容，总结成简短、清晰、像情报卡片一样的中文推送。

核心要求：
1. 只根据用户给出的内容总结，不要编造未出现的信息。
2. 必须区分已知信息和不确定信息。
3. 涉及封号、接码、账号、风控、额度、OAuth、access token、refresh token、401、共享号、Plus/Pro 时，只做风险分析，不提供薅号、绕风控、盗号、规避检测教程。
4. 中文输出，短句优先，适合 Telegram / 飞书阅读。
5. 不要输出 Markdown 表格。
6. 不要输出代码块。
7. 如果原文只是单点反馈，必须写“单点反馈 / 未确认”，并降低风险判断强度。
8. 如果没有评论内容，不要输出“评论补充”模块。
9. 输出要像情报卡片，不要像长文章。
10. 标题要短、准、像快讯，但不要标题党。
11. 不要写“根据原文”这种废话。
12. 不要写无意义安全提示。
"""

    user_prompt = f"""请把下面信息整理成固定格式。

当前系统判断：
- 兴趣等级：{level}
- 评分：{score}
- 建议：{action}
- 触发原因：{reasons}

标题规则：
- 爆炸等级：标题前加 🚨【爆炸新闻】
- 高等级：标题前加 🔴【高关注】
- 中等级：标题前加 🟡【观察】
- 标题一句话，尽量 30 字以内
- 不要夸大，不要编造

必须输出：

{level_icon(level)} 兴趣等级：{level}
🔥 评分：{score}

📝 变化：
- 用 1-3 条写清楚发生了什么

🔎 关键信息：
- 类型：账号风控 / Codex / Claude Code / Plus / 普通 AI 新闻 / 其他
- 范围：单点反馈 / 多人反馈 / 官方信息 / 未确认
- 影响：一句话说明影响

⚠️ 风险判断：低/中/高
- 用 1-3 条说明为什么
- 如果是单点反馈，不要直接判断为大规模事件

📌 建议：
- {action}
- 给 1 条具体建议

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

        if f"兴趣等级：{level}" not in content:
            content = f"{level_icon(level)} 兴趣等级：{level}\n🔥 评分：{score}\n\n{content}"

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
    skipped_count = 0
    skipped_similar_count = 0
    accepted_titles = []

    for rss_url in RSS_SOURCES:
        feed = fetch_feed(rss_url)

        if not feed:
            continue

        source = feed.feed.get("title", rss_url)

        for entry in feed.entries[:12]:
            title = entry.get("title", "")
            link = entry.get("link", "")
            summary = entry.get("summary", "") or entry.get("description", "")

            title = clean_html(title)
            summary = clean_html(summary)

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
                "中": 2,
                "低": 3,
            }.get(score_info["level"], 3)

            candidates.append({
                "priority": priority,
                "score": score_info["score"],
                "uid": uid,
                "level": score_info["level"],
                "title": title,
                "summary": summary,
                "source": source,
                "link": link,
                "score_info": score_info,
            })

            accepted_titles.append(title)

    # 优先级：爆炸 > 高 > 中；同级按评分高低
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
