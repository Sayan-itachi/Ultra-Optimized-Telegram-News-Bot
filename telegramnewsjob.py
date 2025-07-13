#!/usr/bin/env python
"""telegramnewsjob.py – Ultra-optimized AI & IT news engine for Telegram.

This script discovers 20 high-signal stories per day (10 global AI/Tech + 10 IT-industry)
then pushes them to a Telegram channel in the *exact* text-card format specified by
product requirements.

Stack:
1. DiscoveryEngine   → Google News, HackerNews, Google Trends
2. EmotionEngine     → headline rewriting
3. SignalEngine      → add stock/valuation & key metrics via yfinance / scraping
4. Formatter         → build text-only 'designed' post per template
5. DeliveryEngine    → async send via python-telegram-bot (v20)
6. Logging           → log.txt successes & failures

Usage (schedule daily via cron / Task Scheduler):
$ python telegramnewsjob.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any

import requests
import yfinance as yf
from bs4 import BeautifulSoup
from pytrends.request import TrendReq
from telegram.ext import ApplicationBuilder
from logging.handlers import RotatingFileHandler
import time
from urllib.parse import urljoin

try:
    from newspaper import Article
    NEWSPAPER_INSTALLED = True
except ImportError:
    NEWSPAPER_INSTALLED = False

import feedparser
import hashlib
from collections import defaultdict

# --------------------------------------------------------------------------------------
# CONFIGURATION ------------------------------------------------------------------------
# --------------------------------------------------------------------------------------
TOKEN = "YourToken created my telegram bot after creating the channel"
CHANNEL_ID = "@Your channel Name"  # must be admin

BASE_DIR = Path(__file__).parent
CACHE_FILE = BASE_DIR / "cache.json"
LOG_FILE = BASE_DIR / "log.txt"
POSTS_PER_DAY = 20  # 10 AI/Tech + 10 IT-specific
DELAY_BETWEEN_POSTS = 30  # seconds (adjust as needed)
HEADERS = {"User-Agent": "Mozilla/5.0"}
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/122.0",
]

# Tech news site configs for direct scraping
TECH_SITES = [
    {
        "url": "https://techcrunch.com/",
        "article_selector": "article.post-block",
        "title_selector": "h2.post-block__title a",
        "link_selector": "h2.post-block__title a"
    },
    {
        "url": "https://venturebeat.com/",
        "article_selector": "article.Article",
        "title_selector": "h2.ArticleListing__title a",
        "link_selector": "h2.ArticleListing__title a"
    },
    {
        "url": "https://www.theverge.com/tech",
        "article_selector": "div.c-compact-river__entry",
        "title_selector": "h2.c-entry-box--compact__title a",
        "link_selector": "h2.c-entry-box--compact__title a"
    },
    {
        "url": "https://www.wired.com/tag/artificial-intelligence/",
        "article_selector": "div.SummaryItemWrapper-gdEuvf",
        "title_selector": "h3.SummaryItemHedBase-dZmlME a",
        "link_selector": "h3.SummaryItemHedBase-dZmlME a"
    }
]

# Additional news sources
MORE_TECH_URLS = [
    "https://www.artificialintelligence-news.com/",
    "https://www.analyticsinsight.net/category/latest-news/",
    "https://www.unite.ai/news/",
    "https://www.infoq.com/ai-ml-data-eng/",
    "https://www.datanami.com/category/machine-learning/",
    "https://www.kdnuggets.com/news/index.html",
    "https://www.marktechpost.com/category/ai-news/",
    "https://www.analyticsvidhya.com/blog/",
    "https://www.geekwire.com/artificial-intelligence/",
    "https://www.nextbigfuture.com/category/artificial-intelligence"
]

# India-specific tech news
INDIA_TECH_URLS = [
    "https://inc42.com/topics/artificial-intelligence/",
    "https://yourstory.com/topic/artificial-intelligence",
    "https://entrackr.com/tag/artificial-intelligence/",
    "https://www.moneycontrol.com/news/technology/",
    "https://economictimes.indiatimes.com/tech",
]

# China tech news (English)
CHINA_TECH_URLS = [
    "https://pandaily.com/tag/artificial-intelligence/",
    "https://technode.com/category/artificial-intelligence/",
    "https://www.scmp.com/tech",
    "https://www.chinadaily.com.cn/business/scitech",
]

# Company whitelist for better extraction
KNOWN_COMPANIES = {
    "openai", "google", "microsoft", "meta", "facebook", "apple", "amazon", "nvidia", 
    "tesla", "spacex", "anthropic", "deepmind", "huawei", "baidu", "alibaba", "tencent",
    "infosys", "tcs", "wipro", "hcl", "tech mahindra", "cognizant", "accenture",
    "samsung", "sony", "intel", "amd", "qualcomm", "broadcom", "oracle", "salesforce",
    "uber", "lyft", "airbnb", "stripe", "paypal", "square", "robinhood", "coinbase",
    "zoom", "slack", "spotify", "netflix", "twitter", "x corp", "linkedin", "tiktok",
    "bytedance", "snap", "pinterest", "reddit", "discord", "twitch", "epic games"
}

# Fallback content for when scraping fails
BACKUP_HEADLINES = [
    {"title": "OpenAI Quietly Builds Next-Gen AI Infrastructure to Challenge Google", "url": "https://techcrunch.com/ai", "company": "OpenAI"},
    {"title": "India's IT Giants Eye $50B AI Market Amid Global Tech Shifts", "url": "https://economictimes.com/tech", "company": "Infosys"},
    {"title": "China's Baidu Turbo-Charges Dragon AI to Compete with ChatGPT", "url": "https://technode.com/ai", "company": "Baidu"},
    {"title": "Meta Silently Fires 2,000 Engineers While Doubling AI Investment", "url": "https://theverge.com/meta", "company": "Meta"},
    {"title": "Microsoft Replaces Human Customer Service with GPT-Powered Bots", "url": "https://venturebeat.com/ai", "company": "Microsoft"},
    {"title": "Nvidia Explodes Past $2 Trillion Valuation on AI Chip Demand", "url": "https://wired.com/nvidia", "company": "Nvidia"},
    {"title": "TCS Shuts Down Legacy Systems, Goes All-In on AI Automation", "url": "https://inc42.com/tcs", "company": "TCS"},
    {"title": "Tesla's FSD AI Quietly Replaces Human Drivers in Beta Cities", "url": "https://futurism.com/tesla", "company": "Tesla"},
]

# Error tracking
class ErrorTracker:
    def __init__(self):
        self.errors = defaultdict(list)
        self.successes = defaultdict(int)
    
    def log_error(self, source: str, error: str):
        self.errors[source].append(error)
    
    def log_success(self, source: str, count: int):
        self.successes[source] = count
    
    def get_report(self) -> str:
        report = "=== DISCOVERY REPORT ===\n"
        for source, count in self.successes.items():
            report += f"✅ {source}: {count} items\n"
        for source, errs in self.errors.items():
            report += f"❌ {source}: {len(errs)} errors\n"
        return report

error_tracker = ErrorTracker()

# --------------------------------------------------------------------------------------
# LOGGING ------------------------------------------------------------------------------
logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
# rotate at 2 MB keep 3 backups
rot_handler = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3)
rot_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
logging.getLogger().addHandler(rot_handler)
log = logging.getLogger("newsbot")

# --------------------------------------------------------------------------------------
# UTILITIES ----------------------------------------------------------------------------
_slug_re = re.compile(r"[^a-z0-9]+")

def slugify(text: str) -> str:
    return _slug_re.sub("-", text.lower())[:60].strip("-")

def today_str() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%a | %d %B %Y")

# -------------------------- HTTP UTILITIES ---------------------------------------------

def get_html(url: str, timeout: int = 20) -> str:
    """Fetch url with random UA and small random delay to avoid throttling."""
    ua = random.choice(USER_AGENTS)
    try:
        time_delay = random.uniform(0.5, 1.5)
        time.sleep(time_delay)
    except Exception:
        pass
    resp = requests.get(url, headers={"User-Agent": ua}, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def validate_url(url: str) -> bool:
    try:
        r = requests.head(url, timeout=10, allow_redirects=True)
        return r.status_code == 200
    except Exception:
        return False

# --------------------------------------------------------------------------------------
# DISCOVERY ENGINE ---------------------------------------------------------------------
class DiscoveryEngine:
    """Collect raw candidate headlines & urls from multiple sources."""

    def __init__(self):
        self.trends = TrendReq(hl="en-US", tz=330)
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': random.choice(USER_AGENTS),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })

        # Additional sources
        self.tech_rss = [
            "https://techcrunch.com/feed/",
            "https://venturebeat.com/feed/",
            "https://www.theverge.com/rss/index.xml",
            "https://feeds.arstechnica.com/arstechnica/index/",
            "https://www.zdnet.com/topic/artificial-intelligence/rss.xml",
            "https://www.wired.com/feed/category/gear/latest/rss",
            "https://www.technologyreview.com/topnews.rss",
            "https://futurism.com/feed",
            "https://www.sciencedaily.com/rss/computers_math/artificial_intelligence.xml",
            "https://www.cnet.com/ai/rss/"
        ]

        self.reddit_subs = [
            "MachineLearning",
            "ArtificialIntelligence",
            "OpenAI",
            "technology",
            "Futurology",
            "TechNewsToday",
            "computervision",
            "datascience",
            "deeplearning",
            "AskProgramming"
        ]

    def google_news(self) -> List[Dict[str, str]]:
        """Use Google News RSS instead of HTML scraping for reliability."""
        items = []
        queries = ["AI", "artificial+intelligence", "machine+learning", "tech+news", "startup"]
        
        for query in queries:
            try:
                rss_url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
                feed = feedparser.parse(rss_url)
                for entry in feed.entries[:10]:  # Top 10 per query
                    items.append({"title": entry.title, "url": entry.link})
                error_tracker.log_success(f"Google News ({query})", len(feed.entries[:10]))
            except Exception as e:
                error_tracker.log_error(f"Google News ({query})", str(e))
                log.error(f"Google News RSS fail for {query}: {e}")
        
        return items

    def hackernews(self) -> List[Dict[str, str]]:
        # Simple scrape Top stories page for titles containing AI-related keywords.
        html = get_html("https://news.ycombinator.com/")
        soup = BeautifulSoup(html, "html.parser")
        keywords = {"ai", "chatgpt", "llm", "gpt", "machine learning"}
        items = []
        for a in soup.select("a.storylink"):
            title = a.text.strip()
            if any(k.lower() in title.lower() for k in keywords):
                items.append({"title": title, "url": a.get("href", "")})
            if len(items) >= 10:
                break
        return items

    def google_trends(self) -> List[str]:
        kw_list = ["Artificial Intelligence", "ChatGPT", "OpenAI"]

        all_trending_queries = []
        for kw in kw_list:
            try:
                self.trends.build_payload([kw], timeframe="now 1-d", geo="US")
                related_queries_data = self.trends.related_queries()
                if kw in related_queries_data and isinstance(related_queries_data[kw], dict):
                    trending_df = related_queries_data[kw].get("top")
                    if trending_df is not None and not trending_df.empty:
                        all_trending_queries.extend(trending_df["query"].head(5).tolist())
                    else:
                        log.warning(f"No 'top' trending queries found for keyword: {kw}")
                else:
                    log.warning(f"Unexpected data structure or no data for keyword: {kw} in Google Trends")
            except Exception as e:
                log.error(f"Error fetching/processing Google Trends for keyword '{kw}': {e}")
        return list(set(all_trending_queries)) # Return unique queries from all keywords

    # For Twitter/X trends we will skip due to API restrictions; placeholder.

    def _extract_from_site(self, site_config: dict) -> List[Dict[str, str]]:
        """Extract articles from a site using its specific selectors."""
        items = []
        try:
            response = self.session.get(site_config["url"], timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            
            for article in soup.select(site_config["article_selector"])[:5]:  # Top 5 articles
                try:
                    title_elem = article.select_one(site_config["title_selector"])
                    link_elem = article.select_one(site_config["link_selector"])
                    
                    if title_elem and link_elem:
                        title = title_elem.get_text().strip()
                        link = link_elem.get("href", "")
                        if not link.startswith(("http://", "https://")):
                            link = urljoin(site_config["url"], link)
                        
                        if title and link:  # Remove restrictive filtering
                            items.append({"title": title, "url": link})
                except Exception as e:
                    error_tracker.log_error(site_config["url"], f"Article extraction: {e}")
            
            error_tracker.log_success(site_config["url"], len(items))
        except Exception as e:
            error_tracker.log_error(site_config["url"], f"Site scrape: {e}")
        return items

    def _scrape_generic_tech_news(self, urls: List[str]) -> List[Dict[str, str]]:
        """Scrape articles from generic tech news sites."""
        items = []
        for url in urls:
            try:
                response = self.session.get(url, timeout=15)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")
                
                site_items = []
                # Try common article selectors
                selectors = [
                    "article", "div.post", ".article", ".story",
                    "div.entry", ".news-item", ".blog-post"
                ]
                
                for selector in selectors:
                    articles = soup.select(selector)
                    if articles:
                        for article in articles[:3]:  # Top 3 from each selector
                            # Look for title in common heading tags
                            title_elem = (article.find(['h1', 'h2', 'h3']) or 
                                        article.find(class_=lambda x: x and ('title' in x.lower())))
                            
                            if title_elem:
                                title = title_elem.get_text().strip()
                                # Find closest link
                                link_elem = title_elem.find('a') or title_elem.find_parent('a')
                                if link_elem:
                                    link = link_elem.get('href', '')
                                    if not link.startswith(("http://", "https://")):
                                        link = urljoin(url, link)
                                    
                                    if title and link:  # Less restrictive filtering
                                        site_items.append({"title": title, "url": link})
                        break  # If we found articles with one selector, stop trying others
                
                items.extend(site_items)
                error_tracker.log_success(f"Generic {url}", len(site_items))
            except Exception as e:
                error_tracker.log_error(f"Generic {url}", f"Scrape: {e}")
                continue
        return items

    def discover(self) -> List[Dict[str, str]]:
        pool: List[Dict[str, str]] = []
        
        # 1. Scrape major tech sites with specific selectors
        for site_config in TECH_SITES:
            items = self._extract_from_site(site_config)
            pool.extend(items)
            log.info(f"Found {len(items)} items from {site_config['url']}")

        # 2. Scrape additional tech news sites
        more_items = self._scrape_generic_tech_news(MORE_TECH_URLS)
        pool.extend(more_items)
        log.info(f"Found {len(more_items)} items from additional tech sites")

        # 3. Scrape India-specific tech news
        india_items = self._scrape_generic_tech_news(INDIA_TECH_URLS)
        pool.extend(india_items)
        log.info(f"Found {len(india_items)} items from Indian tech sites")

        # 4. Scrape China tech news
        china_items = self._scrape_generic_tech_news(CHINA_TECH_URLS)
        pool.extend(china_items)
        log.info(f"Found {len(china_items)} items from Chinese tech sites")

        try:
            pool.extend(self.google_news())
        except Exception as e:
            error_tracker.log_error("Google News", str(e))
            log.error(f"Google News scrape failed: {e}")

        try:
            pool.extend(self.hackernews())
        except Exception as e:
            error_tracker.log_error("Hacker News", str(e))
            log.error(f"HN scrape failed: {e}")

        # RSS feeds quick ingest
        rss_urls = [
            "https://techcrunch.com/feed/",
            "https://www.theverge.com/rss/index.xml",
            "https://feeds.arstechnica.com/arstechnica/index/",
            "https://www.zdnet.com/topic/artificial-intelligence/rss.xml",
            "https://www.wired.com/feed/category/gear/latest/rss",
            "https://www.technologyreview.com/topnews.rss",
            "https://futurism.com/feed",
            "https://www.sciencedaily.com/rss/computers_math/artificial_intelligence.xml",
            "https://www.cnet.com/ai/rss/",
            "https://news.ycombinator.com/rss"
        ]
        for feed_url in rss_urls:
            try:
                feed = feedparser.parse(feed_url)
                rss_items = []
                for entry in feed.entries[:5]:
                    rss_items.append({"title": entry.title, "url": entry.link})
                pool.extend(rss_items)
                error_tracker.log_success(f"RSS {feed_url.split('/')[2]}", len(rss_items))
            except Exception as e:
                error_tracker.log_error(f"RSS {feed_url}", str(e))
                log.error(f"RSS scrape fail {feed_url}: {e}")

        # Reddit scrape
        try:
            pool.extend(self.reddit_scrape())
        except Exception as e:
            error_tracker.log_error("Reddit", str(e))
            log.error(f"Reddit scrape fail: {e}")

        # Deduplicate by title and URL
        seen_title, seen_url = set(), set()
        seen_hashes = set()  # Hash-based dedup for content similarity
        deduped = []
        for itm in pool:
            # Create content hash for similarity detection
            content_hash = hashlib.md5((itm["title"] + itm["url"]).encode()).hexdigest()
            
            if (itm["title"] in seen_title or 
                itm["url"] in seen_url or 
                content_hash in seen_hashes):
                continue
                
            deduped.append(itm)
            seen_title.add(itm["title"])
            seen_url.add(itm["url"])
            seen_hashes.add(content_hash)

        log.info(f"After deduplication: {len(deduped)} unique items")
        
        # Fallback system if not enough content
        if len(deduped) < 5:
            log.warning("Very few items found after dedup. Adding backup content...")
            for backup in BACKUP_HEADLINES:
                backup_hash = hashlib.md5((backup["title"] + backup["url"]).encode()).hexdigest()
                if backup_hash not in seen_hashes:
                    deduped.append(backup)
                    seen_hashes.add(backup_hash)
                if len(deduped) >= 10:  # Stop when we have enough
                    break
        
        # Print live report to terminal
        print("\n" + error_tracker.get_report())
        print(f"📊 FINAL RESULT: {len(deduped)} unique articles ready for posting")
        
        return deduped[:50]

    # ------------------- Generic website scraping ----------------------------------

    def _scrape_site_headlines(self, url: str) -> List[Dict[str, str]]:
        out = []
        try:
            if NEWSPAPER_INSTALLED:
                article = Article(url)
                article.download()
                article.parse()
                title = article.title
                if title:
                    out.append({"title": title, "url": url})
            else:
                html = get_html(url)
                soup = BeautifulSoup(html, "html.parser")
                h_tag = soup.find("h1") or soup.find("title")
                if h_tag and h_tag.text.strip():
                    out.append({"title": h_tag.text.strip(), "url": url})
        except Exception as e:
            log.error(f"Generic scrape failed for {url}: {e}")
        return out

    def reddit_scrape(self) -> List[Dict[str, str]]:
        """Use Reddit JSON API instead of RSS for better reliability."""
        items = []
        for sub in self.reddit_subs[:5]:  # Limit to avoid rate limits
            try:
                json_url = f"https://www.reddit.com/r/{sub}/hot.json?limit=5"
                headers = {
                    'User-Agent': 'NewsBot/1.0 (by /u/newsbot)',
                    'Accept': 'application/json'
                }
                response = requests.get(json_url, headers=headers, timeout=10)
                data = response.json()
                
                for post in data['data']['children']:
                    post_data = post['data']
                    title = post_data.get('title', '')
                    url = post_data.get('url', '')
                    
                    # Filter for AI/tech related posts
                    if any(keyword in title.lower() for keyword in ['ai', 'artificial intelligence', 'machine learning', 'tech', 'startup']):
                        items.append({"title": title, "url": url})
                
                error_tracker.log_success(f"Reddit r/{sub}", len(data['data']['children']))
            except Exception as e:
                error_tracker.log_error(f"Reddit r/{sub}", str(e))
                log.error(f"Reddit JSON fail {sub}: {e}")
        
        return items

# --------------------------------------------------------------------------------------
# EMOTION & SIGNAL ENGINE --------------------------------------------------------------
class SignalEngine:
    ticker_cache: Dict[str, str] = {
        "OpenAI": "MSFT",
        "Google": "GOOGL",
        "Alphabet": "GOOGL",
        "Microsoft": "MSFT",
        "Meta": "META",
        "Nvidia": "NVDA",
        "Infosys": "INFY",
        "TCS": "TCS.NS",
        "SAP": "SAP",
    }

    country_emojis = {
        "India": "🇮🇳",
        "USA": "🇺🇸",
        "China": "🇨🇳",
    }

    def enrich(self, item: Dict[str, str]) -> Dict[str, Any]:
        title = item["title"]
        
        # Better company extraction using whitelist
        company = "TechCorp"  # Default fallback
        title_lower = title.lower()
        
        # Check against known companies (longest match first)
        sorted_companies = sorted(KNOWN_COMPANIES, key=len, reverse=True)
        for known_company in sorted_companies:
            if known_company in title_lower:
                company = known_company.title()
                break
        
        # If no known company, try to extract from common patterns
        if company == "TechCorp":
            patterns = [
                r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(?:announces|launches|releases|unveils)",
                r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+",
                r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)'s\s+",
            ]
            for pattern in patterns:
                match = re.search(pattern, title)
                if match:
                    potential_company = match.group(1)
                    if len(potential_company) > 2 and potential_company.lower() not in ['the', 'new', 'big', 'top', 'best']:
                        company = potential_company
                        break

        ticker = self.ticker_cache.get(company)
        stock_price = "N/A"
        if ticker:
            try:
                price = yf.Ticker(ticker).info.get("regularMarketPrice")
                if price:
                    stock_price = f"${price:,.2f}"
            except Exception as e:
                log.warning(f"yfinance fail {ticker}: {e}")

        # Country inference
        country = "USA"
        company_lower = company.lower()
        if any(indian_co in company_lower for indian_co in ["infosys", "tcs", "wipro", "hcl"]):
            country = "India"
        elif any(chinese_co in company_lower for chinese_co in ["huawei", "baidu", "alibaba", "tencent", "bytedance"]):
            country = "China"

        # Better emotion headline generation
        verbs = ["shuts down", "explodes", "quietly builds", "replaces", "turbo-charges", "silently fires"]
        if any(v in title_lower for v in [v.lower() for v in verbs]):
            new_headline = title
        else:
            # Only enhance if title doesn't already have emotional words
            if not any(word in title_lower for word in ["breaks", "shocking", "massive", "huge", "major"]):
                main_part = title.split(':')[0] if ':' in title else title
                new_headline = f"{company} {random.choice(verbs).title()} — {main_part}"
            else:
                new_headline = title

        # Smarter sentiment tagging based on keywords
        if any(word in title_lower for word in ["layoff", "fire", "cut", "reduce", "close", "shut"]):
            chosen_tag = "🔴 Layoff"
        elif any(word in title_lower for word in ["funding", "raise", "growth", "launch", "hire", "expand"]):
            chosen_tag = "🟢 Growth"
        elif any(word in title_lower for word in ["ai", "tool", "app", "platform", "model"]):
            chosen_tag = "🤖 New Tool"
        else:
            chosen_tag = "🧠 Strategy"

        return {
            "company": company,
            "country": country,
            "emoji": self.country_emojis.get(country, "🌍"),
            "headline": new_headline,
            "stock": stock_price,
            "tag": chosen_tag,
            "url": item["url"],
        }

# --------------------------------------------------------------------------------------
# FORMATTER ----------------------------------------------------------------------------
class Formatter:
    # Dynamic emote pools based on content
    EMOTE_POOLS = {
        "layoffs": ["🚨", "📉", "⚠️"],
        "ai_tools": ["🤖", "⚙️", "🧠"],
        "predictions": ["🔮", "🌟", "📊"],
        "positive": ["📈", "🚀", "🔥"],
        "negative": ["📉", "⚠️", "🚨"],
        "meta_ai": ["🧠", "🔮", "⚡"],
        "power_moves": ["🔥", "⚡", "🎯"],
        "default": ["📰", "🔍", "💡"]
    }
    
    # Strategic insight templates
    INSIGHT_TEMPLATES = [
        "Signals a major power shift in {industry}.",
        "Hints at long-term intent by {company}.",
        "Could alter the AI stack economics completely.",
        "This is not just {action} — it's a test case for AI-first capitalism.",
        "Represents a fundamental change in how {industry} operates.",
        "Shows {company} is betting big on {trend} dominance.",
        "This move could reshape competitive dynamics across {sector}.",
        "Early signal of what's coming for the entire {industry}.",
        "Strategic positioning for the next wave of {technology}.",
        "Reveals {company}'s true priorities behind the headlines."
    ]
    
    # Context-aware hashtag pools
    HASHTAG_POOLS = {
        "ai": ["#AIInfrastructure", "#LLMShift", "#OpenSourceAI", "#AIStack"],
        "meta": ["#MetaMoves", "#SocialAI", "#MetaReality"],
        "google": ["#GoogleShift", "#SearchAI", "#CloudWars"],
        "openai": ["#OpenAIMoves", "#GPTShift", "#FoundationalAI"],
        "china": ["#ChinaTech", "#DragonAI", "#EastVsWest"],
        "india": ["#IndiaSignals", "#BharatTech", "#DigitalIndia"],
        "funding": ["#VCMoves", "#StartupSignals", "#TechFunding"],
        "layoffs": ["#TechLayoffs", "#AutomationEdge", "#WorkforceTrends"],
        "automation": ["#AutomationEdge", "#FutureWork", "#AIJobs"],
        "startup": ["#StartupSignals", "#FounderMoves", "#ScaleUp"],
        "general": ["#TechTrends", "#FounderSignals", "#TechShift", "#AINews"]
    }
    
    @staticmethod
    def _get_dynamic_emote(title: str, tag: str) -> str:
        """Select emote based on content keywords."""
        title_lower = title.lower()
        
        if any(word in title_lower for word in ["layoff", "fire", "cut", "shutdown", "close"]):
            return random.choice(Formatter.EMOTE_POOLS["layoffs"])
        elif any(word in title_lower for word in ["ai", "robot", "automat", "tool"]):
            return random.choice(Formatter.EMOTE_POOLS["ai_tools"])
        elif any(word in title_lower for word in ["predict", "future", "2026", "2025", "forecast"]):
            return random.choice(Formatter.EMOTE_POOLS["predictions"])
        elif tag == "🟢 Growth" or any(word in title_lower for word in ["raise", "funding", "growth", "launch"]):
            return random.choice(Formatter.EMOTE_POOLS["positive"])
        elif tag == "🔴 Layoff" or any(word in title_lower for word in ["drop", "fall", "decline"]):
            return random.choice(Formatter.EMOTE_POOLS["negative"])
        elif any(word in title_lower for word in ["meta", "openai", "deepmind"]):
            return random.choice(Formatter.EMOTE_POOLS["meta_ai"])
        elif any(word in title_lower for word in ["shift", "pivot", "move", "stealth", "quiet"]):
            return random.choice(Formatter.EMOTE_POOLS["power_moves"])
        else:
            return random.choice(Formatter.EMOTE_POOLS["default"])
    
    @staticmethod
    def _convert_to_inr(usd_price: str) -> tuple[str, str]:
        """Convert USD to INR and determine arrow direction."""
        if usd_price == "N/A":
            return "N/A", ""
        
        try:
            # Extract price from format like "$147.33"
            price_num = float(usd_price.replace("$", "").replace(",", ""))
            inr_price = price_num * 83.1
            
            # Simple modulus logic for arrow (mock percentage change)
            arrow = "↗" if int(price_num) % 2 == 0 else "↘"
            
            return f"₹{inr_price:,.0f}", arrow
        except:
            return "N/A", ""
    
    @staticmethod
    def _generate_description(title: str, original_summary: str = "") -> str:
        """Generate a clean description separate from headline."""
        # Use original summary if available, otherwise create from title
        if original_summary and len(original_summary) > 20:
            # Clean up and limit summary
            desc = original_summary.strip()
            if len(desc) > 200:
                desc = desc[:200] + "..."
            return desc
        else:
            # Generate from title if no summary
            words = title.split()
            if len(words) > 8:
                return " ".join(words[3:]) + " Details are emerging about the strategic implications."
            else:
                return "Breaking development in the tech industry with significant market implications."
    
    @staticmethod
    def _generate_insight(company: str, title: str) -> str:
        """Generate strategic insight using templates."""
        title_lower = title.lower()
        
        # Determine context variables
        if any(word in title_lower for word in ["ai", "artificial intelligence"]):
            industry = "AI"
            action = "automation"
            trend = "AI"
            technology = "artificial intelligence"
            sector = "tech"
        elif any(word in title_lower for word in ["funding", "raise", "investment"]):
            industry = "venture capital"
            action = "funding"
            trend = "investment"
            technology = "fintech"
            sector = "startup ecosystem"
        elif any(word in title_lower for word in ["layoff", "fire", "cut"]):
            industry = "workforce management"
            action = "restructuring"
            trend = "automation"
            technology = "workforce tech"
            sector = "employment"
        else:
            industry = "technology"
            action = "innovation"
            trend = "digital transformation"
            technology = "emerging tech"
            sector = "tech industry"
        
        template = random.choice(Formatter.INSIGHT_TEMPLATES)
        return template.format(
            company=company,
            industry=industry,
            action=action,
            trend=trend,
            technology=technology,
            sector=sector
        )
    
    @staticmethod
    def _generate_hashtags(title: str, company: str, country: str) -> str:
        """Generate context-aware hashtags with colored dots."""
        title_lower = title.lower()
        company_lower = company.lower()
        tags = []
        
        # Company-specific tags
        if "meta" in company_lower:
            tags.extend(random.sample(Formatter.HASHTAG_POOLS["meta"], 2))
        elif "google" in company_lower:
            tags.extend(random.sample(Formatter.HASHTAG_POOLS["google"], 2))
        elif "openai" in company_lower:
            tags.extend(random.sample(Formatter.HASHTAG_POOLS["openai"], 2))
        
        # Content-specific tags
        if any(word in title_lower for word in ["ai", "artificial intelligence"]):
            tags.extend(random.sample(Formatter.HASHTAG_POOLS["ai"], 1))
        
        if any(word in title_lower for word in ["funding", "raise", "investment"]):
            tags.extend(random.sample(Formatter.HASHTAG_POOLS["funding"], 1))
        
        if any(word in title_lower for word in ["layoff", "fire", "cut"]):
            tags.extend(random.sample(Formatter.HASHTAG_POOLS["layoffs"], 1))
        
        # Country-specific tags
        if country == "China":
            tags.extend(random.sample(Formatter.HASHTAG_POOLS["china"], 1))
        elif country == "India":
            tags.extend(random.sample(Formatter.HASHTAG_POOLS["india"], 1))
        
        # Fill remaining with general tags
        while len(tags) < 4:
            tags.extend(random.sample(Formatter.HASHTAG_POOLS["general"], 1))
        
        # Limit to 6 and add colored dots
        tags = list(set(tags))[:6]  # Remove duplicates and limit
        colors = ["🔵", "🟣", "🔴", "🟠", "🟡", "🟢"]
        tagged = [f"{tag} {colors[i % len(colors)]}" for i, tag in enumerate(tags)]
        
        return "   ".join(tagged)

    @staticmethod
    def build_post(data: Dict[str, Any]) -> str:
        # Get dynamic emote for headline
        emote = Formatter._get_dynamic_emote(data["headline"], data["tag"])
        
        # Convert stock price to INR
        inr_price, arrow = Formatter._convert_to_inr(data["stock"])
        
        # Format stock line
        if inr_price != "N/A":
            stock_line = f"📊 {inr_price} {arrow} | {data['stock']} USD"
        else:
            stock_line = f"📊 Stock: {data['stock']}"
        
        # Get date
        date = today_str()
        
        # Generate description (separate from headline)
        description = Formatter._generate_description(data["headline"])
        
        # Generate strategic insight
        insight = Formatter._generate_insight(data["company"], data["headline"])
        
        # Generate context-aware hashtags
        hashtags = Formatter._generate_hashtags(data["headline"], data["company"], data["country"])
        
        # Clean URL for display
        clean_url = data["url"]
        if len(clean_url) > 50:
            try:
                from urllib.parse import urlparse
                parsed = urlparse(clean_url)
                clean_url = f"{parsed.netloc}{parsed.path}..."
            except:
                clean_url = clean_url[:50] + "..."
        
        # Build the post with perfect spacing
        post = f"""{emote} *{data['headline']}*

{stock_line}
🏢 {data['company']} | {data['emoji']} | 📅 {date}

📝 *Description:*
{description}

🧠 *Why it matters:*
{insight}

{hashtags}

🔗 Source: {clean_url}"""
        
        return post

# --------------------------------------------------------------------------------------
# DELIVERY ENGINE ----------------------------------------------------------------------
class DeliveryEngine:
    def __init__(self, token: str):
        self.token = token
        self.app = None

    async def send_post(self, text: str):
        max_len = 4096
        if len(text) > max_len:
            text = text[:max_len - 10] + "…"
        attempts, delay = 0, 5
        while attempts < 3:
            try:
                await self.app.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="Markdown")
                log.info(f"Posted: {text[:60]}…")
                return True
            except Exception as e:
                attempts += 1
                log.error(f"Telegram send failure attempt {attempts}: {e}")
                await asyncio.sleep(delay + random.uniform(0, 3))
                delay *= 2  # exponential backoff
        return False

    async def run(self, messages: List[str]):
        self.app = await ApplicationBuilder().token(self.token).build().__aenter__()
        failed: List[str] = []
        for msg in messages:
            ok = await self.send_post(msg)
            if not ok:
                failed.append(msg)
            await asyncio.sleep(DELAY_BETWEEN_POSTS)
        if failed:
            failed_file = BASE_DIR / "failed_posts.json"
            try:
                existing = []
                if failed_file.exists():
                    existing = json.loads(failed_file.read_text())
            except Exception:
                existing = []
            existing.extend(failed)
            failed_file.write_text(json.dumps(existing, indent=2))
        await self.app.__aexit__(None, None, None)

# --------------------------------------------------------------------------------------
# MAIN PIPELINE ------------------------------------------------------------------------
async def main():
    log.info("===== RUN START =====")
    # load cache to avoid duplicates per day
    cache: Dict[str, Any] = {}
    if CACHE_FILE.exists():
        cache = json.loads(CACHE_FILE.read_text())
    today_key = datetime.now(timezone.utc).date().isoformat()
    if cache.get("date") == today_key:
        posted_titles = set(cache.get("titles", []))
    else:
        posted_titles = set()
        cache = {"date": today_key, "titles": []}

    discover = DiscoveryEngine()
    raw_items = discover.discover()
    log.info(f"DiscoveryEngine found {len(raw_items)} raw items.")

    # Filter out already posted
    new_items = [i for i in raw_items if i["title"] not in posted_titles]
    log.info(f"{len(new_items)} items remain after cache/title deduplication.")

    # Pick top stories
    selected = new_items[:POSTS_PER_DAY]
    log.info(f"Selected {len(selected)} items to process for posting.")

    signal = SignalEngine()
    formatter = Formatter()
    messages: List[str] = []
    for itm in selected:
        enriched = signal.enrich(itm)
        if not enriched:
            log.warning(f"SignalEngine failed to enrich item: {itm.get('title')}, skipping.")
            continue
        messages.append(formatter.build_post(enriched))
        cache["titles"].append(itm["title"])

    if not messages:
        log.warning("No messages were generated to send. Check discovery and enrichment logs.")

    # Save cache early
    CACHE_FILE.write_text(json.dumps(cache, indent=2))

    # Delivery
    deliver = DeliveryEngine(TOKEN)
    log.info(f"Handing {len(messages)} formatted messages to DeliveryEngine.")
    await deliver.run(messages)

    log.info("===== RUN COMPLETE =====")

# --------------------------------------------------------------------------------------
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Interrupted by user")

