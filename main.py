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


# =========================
# 推送策略：自适应推送，不固定最多几条
# =========================
HIGH_QUALITY_ONLY = True

# 规则评分线：允许进入 AI 判断
MIN_RULE_SCORE = 50

# 最低最终推送线
MIN_PUSH_SCORE = 60

# 单点反馈推送线
MIN_SINGLE_REPORT_SCORE = 70

# 普通 AI 新闻最低推送线
MIN_GENERAL_AI_SCORE = 72

# 不限制推送最大数量，自适应由 AI 判断决定
MAX_SEND_COUNT = None

# 每次最多 AI 判断数，避免浪费 token
MAX_JUDGE_COUNT = 20

SEND_EMPTY_HEARTBEAT = False


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
    "https://www.reddit.com/r/ClaudeAI/search.rss?q=Claude%20Code%20OR%20billing%20OR%20refund&restrict_sr=1&sort=new",
    "https://www.reddit.com/r/OpenAI/search.rss?q=rate%20limit%20OR%20suspended%20OR%20verification&restrict_sr=1&sort=new",
    "https://www.reddit.com/r/ChatGPT/search.rss?q=banned%20OR%20verification&restrict_sr=1&sort=new",
    "https://www.reddit.com/r/GitHubCopilot/search.rss?q=student%20OR%20Copilot&restrict_sr=1&sort=new",
    "https://hnrss.org/newest?q=OpenAI",
    "https://hnrss.org/newest?q=Claude",
    "https://aihot.virxact.com/feed.xml",
    "https://aihot.virxact.com/feed/daily.xml",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://huggingface.co/blog/feed.xml",
]


BLOCK_KEYWORDS = [
    "NFT", "crypto", "AI wallpaper", "加密", "metaverse",
    "招聘", "岗位", "培训", "优惠码", "邀请码",
]

CORE_TOPIC_KEYWORDS = [
    "Codex", "Plus", "Pro", "API", "模型", "额度", "rate limit",
    "OAuth", "access token", "refresh token", "封号", "suspended",
    "banned", "verification", "401", "403",
]


def load_seen():
    if not SEEN_FILE.exists():
        return set()
    return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))


def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(list(seen)[-4000:], ensure_ascii=False, indent=2), encoding="utf-8")


def short_text(text, limit=2000):
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def item_id(title, link):
    return hashlib.sha256(f"{title}|{link}".encode("utf-8")).hexdigest()


def score_news(title, summary, source, rss_url):
    text = f"{title} {summary}".lower()
    score = 0

    if any(k.lower() in text for k in CORE_TOPIC_KEYWORDS):
        score += 24
    if "outage" in text or "incident" in text:
        score += 26

    if "releases.atom" in rss_url or "codex" in rss_url:
        score += 10

    score += len(source)

    return {
        "score": min(score, 98),
        "core_hits": bool(any(k.lower() in text for k in CORE_TOPIC_KEYWORDS)),
        "critical_hits": bool("outage" in text or "incident" in text),
    }


def should_skip_by_rules(score_info):
    score = score_info["score"]
    if score < MIN_RULE_SCORE:
        return True
    return False


def default_ai_judgement(score_info):
    return {"should_push": score_info["score"] >= MIN_PUSH_SCORE}


def ai_judge_news(title, summary, source, link, score_info):
    return default_ai_judgement(score_info), "rule-ai"


def deepseek_summarize(title, summary, source, link, score_info, ai_judgement):
    return f"📌 {title}\n📍 来源：{source}\n🔗 {link}\n⚡ 分数：{score_info['score']}"


def send_feishu(message):
    if not FEISHU_WEBHOOK:
        return False
    payload = {"msg_type": "text", "content": {"text": message[:3500]}}
    try:
        r = requests.post(FEISHU_WEBHOOK, json=payload, timeout=20)
        return r.status_code == 200
    except:
        return False


def main():
    seen = load_seen()
    new_seen = set(seen)
    candidates = []

    for url in RSS_SOURCES:
        feed = feedparser.parse(url)
        for entry in feed.entries[:8]:
            title = short_text(entry.get("title", ""))
            link = entry.get("link", "")
            summary = short_text(entry.get("summary", ""))

            uid = item_id(title, link)
            if uid in seen:
                continue

            score_info = score_news(title, summary, feed.feed.get("title", ""), url)
            if should_skip_by_rules(score_info):
                new_seen.add(uid)
                continue

            candidates.append({
                "title": title,
                "summary": summary,
                "source": feed.feed.get("title", ""),
                "link": link,
                "score_info": score_info,
            })

    for item in candidates:
        ai_judgement, _ = ai_judge_news(
            item["title"], item["summary"], item["source"], item["link"], item["score_info"]
        )

        if not ai_judgement.get("should_push"):
            new_seen.add(item_id(item["title"], item["link"]))
            continue

        message = deepseek_summarize(
            item["title"], item["summary"], item["source"], item["link"],
            item["score_info"], ai_judgement
        )
        send_feishu(message)
        new_seen.add(item_id(item["title"], item["link"]))

    save_seen(new_seen)


if __name__ == "__main__":
    main()
