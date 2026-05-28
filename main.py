import os
import time
import json
import re
import hashlib
import requests
import feedparser
from pathlib import Path
from difflib import SequenceMatcher

# -------- CONFIG --------

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")

SEEN_FILE = Path("seen.json")

# 规则推送线（可调）
MIN_RULE_SCORE = 50    # 初筛评分线
MIN_PUSH_SCORE = 60    # 最低推送线
MIN_SINGLE_REPORT_SCORE = 70
MIN_GENERAL_AI_SCORE = 72

# 语义重复相似度阈值（标题）
SIMILARITY_THRESHOLD = 0.88

# DeepSeek 重试次数
DEEPEEK_MAX_RETRIES = 2

# -------- RSS SOURCES WITH WEIGHTS --------

RSS_SOURCES = [
    ("https://status.openai.com/history.rss", 1.3),
    ("https://github.com/openai/codex/issues.atom", 1.25),
    ("https://github.com/openai/codex/releases.atom", 1.25),
    ("https://github.com/anthropics/claude-code/releases.atom", 1.2),
    ("https://github.com/google-gemini/gemini-cli/releases.atom", 1.2),
    ("https://openai.com/news/rss.xml", 1.2),
    ("https://github.blog/changelog/label/copilot/feed/", 1.2),
    ("https://linux.do/latest.rss", 1.1),
    ("https://linux.do/top.rss", 1.1),
    ("https://linux.do/posts.rss", 1.1),
    ("https://aihot.virxact.com/feed.xml", 1.0),
    ("https://aihot.virxact.com/feed/daily.xml", 1.0),
    ("https://www.reddit.com/r/ChatGPT/search.rss?q=banned+OR+rate+limit&restrict_sr=1&sort=new", 0.9),
    ("https://hnrss.org/newest?q=OpenAI", 0.9),
    ("https://hnrss.org/newest?q=Claude", 0.9),
    ("https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", 0.8),
    ("https://huggingface.co/blog/feed.xml", 0.8),
]

BLOCK_KEYWORDS = [
    "AI art", "AI image", "stable diffusion", "NFT",
    "crypto", "wallpaper", "优惠码", "邀请码"
]

CORE_TOPIC_KEYWORDS = [
    "Codex", "Plus", "Pro", "API", "限流", "rate limit",
    "OAuth", "access token", "refresh token", "封号",
    "403", "401", "banned", "suspended"
]

MULTI_REPORT_KEYWORDS = [
    "多人反馈", "大面积", "普遍用户", "widespread", "multiple users"
]

# -------- UTIL --------

def load_seen():
    if not SEEN_FILE.exists():
        return []
    try:
        return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    except:
        return []

def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(seen[-2000:], ensure_ascii=False, indent=2), encoding="utf-8")

def short_text(text, limit=2000):
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit] + "..."

def generate_uid(title, link):
    raw = f"{title}|{link}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def similar(a, b):
    return SequenceMatcher(None, a, b).ratio()

# -------- SCORING --------

def score_item(title, summary, source, weight):
    text = f"{title} {summary}".lower()
    base_score = 0

    for kw in CORE_TOPIC_KEYWORDS:
        if kw.lower() in text:
            base_score += 24

    if "outage" in text or "incident" in text:
        base_score += 26

    base_score += int(weight * 10)

    score = min(base_score, 98)
    return score

def should_skip_rule(score):
    return score < MIN_RULE_SCORE

# -------- DEEPSEEK AI JUDGE --------

def call_deepseek(title, summary, source, link, score):
    system_prompt = """你是中文 AI 情报雷达的第一层审核器，
必须输出合法 JSON，不输出解释语句。
字段：
should_push (bool)
category (str)
scope (str)
risk (str)
confidence (str)
action (str)
reason (str)
no_hype_title (str)
hype_warning (str)
"""

    user_prompt = f"""
标题：{short_text(title,220)}
来源：{source}
摘要：{short_text(summary,1200)}
链接：{link}
规则评分：{score}
"""

    for attempt in range(DEEPEEK_MAX_RETRIES):
        try:
            resp = requests.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type":"application/json"},
                json={
                    "model": "deepseek-chat",
                    "messages":[
                        {"role":"system","content": system_prompt},
                        {"role":"user","content": user_prompt}
                    ],
                    "temperature": 0.0,
                    "max_tokens": 500,
                    "response_format": {"type":"json_object"}
                },
                timeout=35
            )
            if resp.status_code == 200:
                content = resp.json()["choices"][0]["message"]["content"]
                return json.loads(content)
        except Exception as e:
            print("DeepSeek error:", e)
            time.sleep(2)
    return None

# -------- MESSAGE BUILD --------

def build_card(title, summary, source, link, score, ai_info):
    level_icon = "🔴" if score >= 80 else "🟡"
    card = f"""{level_icon}{ai_info.get('category','其他')}【{short_text(title,60)}】

🔥 评分：{score}/100
📌 建议：{ai_info.get('action','观察')}
📝 摘要：
- {short_text(summary,400)}

🔎 详情：
来源：{source}
链接：{link}

⚠️ 风险：{ai_info.get('risk','中')}
🧠 可信度：{ai_info.get('confidence','中')}
📍 推送理由：{ai_info.get('reason','公开推断')}
"""
    if ai_info.get('hype_warning'):
        card += f"\n⚠️ 注意：{ai_info['hype_warning']}"
    return card

# -------- SEND FEISHU --------

def send_to_feishu(msg):
    if not FEISHU_WEBHOOK:
        print("Feishu webhook 未设置，跳过推送")
        return False

    payload = {"msg_type":"text", "content":{"text": msg[:3800]}}
    try:
        r = requests.post(FEISHU_WEBHOOK, json=payload, timeout=20)
        return r.status_code == 200
    except Exception as e:
        print("飞书推送异常：", e)
        return False

# -------- MAIN --------

def main():
    print("开始抓取 RSS 并评分推送...")
    seen = load_seen()
    new_seen = list(seen)
    titles_buffer = [x["title"] for x in seen]

    candidates = []

    for rss_url, weight in RSS_SOURCES:
        feed = feedparser.parse(rss_url)
        source_title = feed.feed.get("title", rss_url)

        for entry in feed.entries[:8]:
            title = short_text(entry.get("title",""))
            link = entry.get("link","")
            summary = short_text(entry.get("summary",""))

            uid = generate_uid(title, link)
            if uid in [x["uid"] for x in new_seen]:
                continue

            score = score_item(title, summary, source_title, weight)
            if should_skip_rule(score):
                new_seen.append({"uid":uid, "title":title})
                continue

            duplicates = any(similar(title, old) >= SIMILARITY_THRESHOLD for old in titles_buffer)
            if duplicates:
                new_seen.append({"uid":uid, "title":title})
                continue

            candidates.append((title, summary, source_title, link, score, uid))

    candidates.sort(key=lambda x:(-x[4], x[0]))

    for title, summary, source_title, link, score, uid in candidates:
        ai_judgement = call_deepseek(title, summary, source_title, link, score)
        if not ai_judgement:
            continue

        should_push = ai_judgement.get("should_push", False)
        if not should_push:
            new_seen.append({"uid":uid, "title":title})
            continue

        message = build_card(title, summary, source_title, link, score, ai_judgement)
        send_to_feishu(message)

        new_seen.append({"uid":uid, "title":title})
        time.sleep(2)

    save_seen(new_seen)
    print("Run 完成。")

if __name__ == "__main__":
    main()
