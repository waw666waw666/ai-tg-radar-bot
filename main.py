import os
import time
import json
import requests
import feedparser
from datetime import datetime, timedelta
from typing import List, Dict, Any
from queue import Queue
import threading

# -------------------------------
# 配置区
# -------------------------------

# GitHub Secrets 中配置
MODELSCOPE_API_KEY = os.getenv("MODELSCOPE_API_KEY", "")
MODELSCOPE_BASE_URL = os.getenv("MODELSCOPE_BASE_URL", "https://api-inference.modelscope.cn/v1")
MODELSCOPE_MODEL = os.getenv("MODELSCOPE_MODEL", "deepseek-ai/DeepSeek-V4-Flash")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

# RSS 来源列表
RSS_URLS = [
    "https://linux.do/feed",
    "https://news.example.com/rss",
    # 可继续添加其他源
]

# 推送间隔配置
DAY_INTERVAL_SECONDS = 3600  # 白天每小时一次
NIGHT_INTERVAL_SECONDS = 7200  # 夜间每2小时一次
NIGHT_START = 0  # 00:00
NIGHT_END = 5    # 05:00

# 内容评分阈值
MIN_SCORE = 70

# 输出队列
output_queue = Queue()

# -------------------------------
# 工具函数
# -------------------------------

def is_night(now: datetime) -> bool:
    return NIGHT_START <= now.hour < NIGHT_END

def fetch_rss(url: str) -> List[Dict[str, Any]]:
    """
    获取 RSS 内容，解析成统一格式
    """
    try:
        feed = feedparser.parse(url)
        entries = []
        for entry in feed.entries:
            try:
                published = datetime(*entry.published_parsed[:6])
            except:
                published = datetime.utcnow()
            entries.append({
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "summary": entry.get("summary", ""),
                "published": published
            })
        return entries
    except Exception as e:
        print(f"[RSS] 获取失败 {url}: {e}")
        return []

def score_content(entry: Dict[str, Any]) -> int:
    """
    简化权重算法：
    - 最新内容优先
    - 标题中关键词加分
    - 来源可信度加分
    """
    score = 50  # 基础分
    delta_hours = (datetime.utcnow() - entry["published"]).total_seconds() / 3600
    if delta_hours < 1:
        score += 30
    elif delta_hours < 3:
        score += 20
    elif delta_hours < 6:
        score += 10

    keywords = ["PP", "Codex", "DeepSeek", "二次验证", "接码", "风控", "限制"]
    for kw in keywords:
        if kw.lower() in entry["title"].lower() or kw.lower() in entry["summary"].lower():
            score += 5

    score = min(score, 100)
    return score

def call_modelscope(prompt: str) -> Dict[str, Any]:
    """
    调用 ModelScope 生成内容
    """
    headers = {
        "Authorization": f"Bearer {MODELSCOPE_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "inputs": prompt,
        "parameters": {
            "max_new_tokens": 500
        }
    }
    try:
        response = requests.post(
            f"{MODELSCOPE_BASE_URL}/models/{MODELSCOPE_MODEL}/infer",
            headers=headers,
            json=payload,
            timeout=15
        )
        response.raise_for_status()
        data = response.json()
        return data
    except requests.exceptions.RequestException as e:
        print(f"[ModelScope] 请求失败: {e}")
        return {}

def fallback_deepseek(prompt: str) -> Dict[str, Any]:
    """
    DeepSeek 备用生成
    """
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {"prompt": prompt, "max_tokens": 500}
    try:
        response = requests.post(
            "https://api.deepseek.cn/v1/text",
            headers=headers,
            json=payload,
            timeout=15
        )
        response.raise_for_status()
        return response.json()
    except:
        return {}

def summarize_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """
    使用 ModelScope 或 DeepSeek 生成摘要
    """
    prompt = f"请根据以下内容生成结构化摘要，输出 JSON：\n标题: {entry['title']}\n内容: {entry['summary']}\n发布时间: {entry['published']}\n"
    result = call_modelscope(prompt)
    if not result or "outputs" not in result:
        result = fallback_deepseek(prompt)
    summary_text = result.get("outputs", result.get("text", ""))
    return {
        "title": entry["title"],
        "link": entry["link"],
        "summary": summary_text,
        "published": entry["published"],
        "score": score_content(entry)
    }

def push_to_feishu(article: Dict[str, Any]):
    """
    飞书推送
    """
    webhook_url = os.getenv("FEISHU_WEBHOOK", "")
    if not webhook_url:
        print("[Feishu] 未配置 webhook")
        return
    payload = {
        "msg_type": "text",
        "content": {
            "text": f"【标题】{article['title']}\n【摘要】{article['summary']}\n【链接】{article['link']}\n【发布时间】{article['published']}\n【评分】{article['score']}"
        }
    }
    try:
        requests.post(webhook_url, json=payload, timeout=10)
    except Exception as e:
        print(f"[Feishu] 推送失败: {e}")

# -------------------------------
# 主流程
# -------------------------------

def main_loop():
    while True:
        now = datetime.utcnow()
        interval = NIGHT_INTERVAL_SECONDS if is_night(now) else DAY_INTERVAL_SECONDS

        all_entries = []
        for url in RSS_URLS:
            all_entries.extend(fetch_rss(url))

        # 过滤最近1小时内内容
        one_hour_ago = datetime.utcnow() - timedelta(hours=1)
        recent_entries = [e for e in all_entries if e["published"] >= one_hour_ago]

        # 去重标题
        seen_titles = set()
        unique_entries = []
        for e in recent_entries:
            if e["title"] not in seen_titles:
                unique_entries.append(e)
                seen_titles.add(e["title"])

        # 排序并评分
        processed = [summarize_entry(e) for e in unique_entries]
        processed.sort(key=lambda x: x["score"], reverse=True)

        # 推送评分大于阈值
        for article in processed:
            if article["score"] >= MIN_SCORE:
                push_to_feishu(article)

        time.sleep(interval)

# -------------------------------
# 启动
# -------------------------------

if __name__ == "__main__":
    main_loop()
