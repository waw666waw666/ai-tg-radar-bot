import os
import time
import json
import hashlib
from pathlib import Path

import requests
import feedparser


DEEPSEEK_API_KEY = os.environ.get("sk-bb9c971e44414d73808e0b64f2e2fc3a", "")
FEISHU_WEBHOOK = os.environ.get("https://open.feishu.cn/open-apis/bot/v2/hook/9a81d0a7-7895-419d-9455-9df86f42fd58", "")

SEEN_FILE = Path("seen.json")

RSS_SOURCES = [
    "https://linux.do/latest.rss",
    "https://linux.do/top.rss",
    "https://linux.do/posts.rss",
    "https://status.openai.com/history.rss",
    "https://github.com/openai/codex/issues.atom",
    "https://www.reddit.com/r/ClaudeAI/search.rss?q=Claude%20Code%20OR%20HERMES.md%20OR%20billing%20OR%20refund&restrict_sr=1&sort=new",
    "https://www.reddit.com/r/OpenAI/search.rss?q=Codex%20OR%20rate%20limit%20OR%20banned%20OR%20suspended%20OR%20verification&restrict_sr=1&sort=new",
    "https://www.reddit.com/r/ChatGPT/search.rss?q=Plus%20OR%20banned%20OR%20suspended%20OR%20verification%20OR%20text%20message&restrict_sr=1&sort=new",
    "https://www.reddit.com/r/GitHubCopilot/search.rss?q=student%20OR%20model%20OR%20Codex%20OR%20Claude&restrict_sr=1&sort=new",
    "https://hnrss.org/newest?q=OpenAI",
    "https://hnrss.org/newest?q=Claude",
    "https://hnrss.org/newest?q=Codex",
    "https://hnrss.org/newest?q=Gemini",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://huggingface.co/blog/feed.xml",
]

HIGH_KEYWORDS = [
    "Codex", "Plus", "Pro", "封号", "被封", "冻结", "禁用", "风控",
    "接码", "短信", "手机号", "text message", "verification",
    "OAuth", "accessToken", "access token", "refresh_token",
    "refresh token", "401", "403", "rate limit", "quota",
    "限流", "额度", "学生包", "Claude Code", "HERMES.md",
    "billing", "refund"
]

MEDIUM_KEYWORDS = [
    "OpenAI", "ChatGPT", "Claude", "Anthropic", "Gemini",
    "Antigravity", "GitHub Copilot", "xAI", "GPU", "API",
    "model", "模型", "算力", "订阅"
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
        json.dumps(list(seen)[-800:], ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def item_id(title, link):
    raw = f"{title}|{link}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def contains_keyword(text, keywords):
    lower = text.lower()
    return any(k.lower() in lower for k in keywords)


def get_level(text):
    if contains_keyword(text, HIGH_KEYWORDS):
        return "高"
    if contains_keyword(text, MEDIUM_KEYWORDS):
        return "中"
    return "低"


def level_icon(level):
    if level == "高":
        return "🔴"
    if level == "中":
        return "🟡"
    return "🟢"


def short_text(text, limit=2500):
    text = " ".join((text or "").replace("\n", " ").split())
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def fetch_feed(url):
    headers = {"User-Agent": "Mozilla/5.0 AI-Radar-Bot/1.0"}
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return feedparser.parse(response.text)
    except Exception as e:
        print(f"Failed to fetch {url}: {e}")
        return None


def fallback_message(title, summary, source, link, level):
    return f"""📌【{short_text(title, 120)}】

{level_icon(level)} 兴趣等级：{level}

📝 变化：
- {short_text(summary, 500)}

🔎 关键信息：
- 来源：{source}
- 当前为公开 RSS 自动抓取，未经过多来源交叉验证

⚠️ 风险判断：
- 中
- 只能当作公开线索，涉及账号、额度、封号、接码的信息需要继续核实

💬 评论补充：
暂无评论补充

可信度：中
理由：来自公开 RSS，但未做多来源验证

来源：{source}
链接：{link}"""


def deepseek_summarize(title, summary, source, link, level):
    if not DEEPSEEK_API_KEY:
        return fallback_message(title, summary, source, link, level)

    raw_content = short_text(
        f"标题：{title}\n来源：{source}\n摘要：{summary}\n链接：{link}",
        3500
    )

    system_prompt = """你是一个 Telegram / 飞书 AI情报频道的中文编辑。
你的任务是把公开 RSS、社区帖子、新闻源内容，总结成简短、清晰、像情报卡片一样的中文推送。

要求：
1. 只根据用户给出的内容总结，不要编造未出现的信息。
2. 必须区分已知信息和不确定信息。
3. 涉及封号、接码、账号、风控、额度、OAuth、access token、refresh token、401、共享号、Plus/Pro 时，只做风险分析，不提供薅号、绕风控、盗号、规避检测教程。
4. 中文输出，短句优先，适合 Telegram / 飞书阅读。
5. 不要输出 Markdown 表格。
6. 不要输出代码块。
"""

    user_prompt = f"""请把下面信息整理成固定格式：

📌【标题】一句话标题，保留重点

{level_icon(level)} 兴趣等级：{level}

📝 变化：
- 用 1-3 条写清楚发生了什么

🔎 关键信息：
- 用 1-4 条提取关键点
- 如果是社区传言，要写“社区反馈 / 单点反馈 / 多人反馈 / 未确认”

⚠️ 风险判断：
- 低/中/高
- 用 1-3 条说明为什么

💬 评论补充：
- 如果原文没有评论，就写“暂无评论补充”
- 不要编造评论

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
                "temperature": 0.2,
                "max_tokens": 900,
                "stream": False,
            },
            timeout=60,
        )

        if response.status_code != 200:
            print("DeepSeek failed:", response.status_code, response.text[:500])
            return fallback_message(title, summary, source, link, level)

        data = response.json()
        return data["choices"][0]["message"]["content"].strip()

    except Exception as e:
        print("DeepSeek exception:", e)
        return fallback_message(title, summary, source, link, level)


def send_feishu(message):
    if not FEISHU_WEBHOOK:
        print("FEISHU_WEBHOOK not set, skip Feishu.")
        return

    payload = {
        "msg_type": "text",
        "content": {
            "text": message[:3800]
        }
    }

    try:
        response = requests.post(FEISHU_WEBHOOK, json=payload, timeout=30)
        if response.status_code != 200:
            print("Feishu send failed:", response.status_code, response.text[:500])
    except Exception as e:
        print("Feishu exception:", e)


def main():
    seen = load_seen()
    new_seen = set(seen)
    sent_count = 0

    for rss_url in RSS_SOURCES:
        feed = fetch_feed(rss_url)
        if not feed:
            continue

        source = feed.feed.get("title", rss_url)

        for entry in feed.entries[:8]:
            title = entry.get("title", "")
            link = entry.get("link", "")
            summary = entry.get("summary", "") or entry.get("description", "")

            if not title or not link:
                continue

            text = f"{title} {summary}"

            if not contains_keyword(text, HIGH_KEYWORDS + MEDIUM_KEYWORDS):
                continue

            uid = item_id(title, link)
            if uid in seen:
                continue

            level = get_level(text)
            message = deepseek_summarize(title, summary, source, link, level)

            send_feishu(message)

            new_seen.add(uid)
            sent_count += 1
            time.sleep(3)

            if sent_count >= 5:
                save_seen(new_seen)
                print(f"Sent {sent_count} messages.")
                return

    save_seen(new_seen)
    print(f"Sent {sent_count} messages.")


if __name__ == "__main__":
    main()
