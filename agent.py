"""Free daily agent: RSS headlines + snippets + tags, Yahoo index/stock quotes with 1-month history -> data.json"""
import os, threading, json, re, html, datetime, urllib.request, xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

UA = {"User-Agent": "Mozilla/5.0 (signal-daily-bot)"}
FEEDS = {
 "ai": [("TechCrunch", "https://techcrunch.com/category/artificial-intelligence/feed/"),
        ("The Verge", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
        ("VentureBeat", "https://venturebeat.com/category/ai/feed/")],
 "markets": [("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
             ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664"),
             ("MarketWatch", "https://feeds.marketwatch.com/marketwatch/topstories/")]}
INDICES = [("S&P 500", "^GSPC"), ("Nasdaq", "^IXIC"), ("Nikkei 225", "^N225"), ("FTSE 100", "^FTSE")]
WATCH = ["NVDA", "MSFT", "GOOGL", "AMZN", "META", "AAPL", "TSLA", "AMD", "AVGO", "TSM", "PLTR", "ORCL"]
TAGS = {"OpenAI": r"\bopenai\b|chatgpt|\bgpt", "Anthropic": r"anthropic|\bclaude\b",
        "Google": r"\bgoogle\b|gemini|deepmind", "Meta": r"\bmeta\b|\bllama\b",
        "Nvidia": r"nvidia|\bgpus?\b|\bchips?\b", "Microsoft": r"microsoft|copilot",
        "Regulation": r"regulat|legislation|senate|congress|lawsuit|\bEU\b",
        "Funding": r"funding|raises|valuation|\bipo\b|acquir",
        "Rates & Fed": r"\bfed\b|interest rate|inflation|central bank",
        "Earnings": r"earnings|profit|revenue|quarterly",
        "Oil & energy": r"\boil\b|opec|energy", "Crypto": r"bitcoin|crypto|ethereum"}

def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=8).read()

def tags(t):
    return [k for k, rx in TAGS.items() if re.search(rx, t, re.I)][:3]

def snippet(raw, title):
    s = html.unescape(re.sub(r"<[^>]+>", " ", raw or "")); s = re.sub(r"\s+", " ", s).strip()
    if not s or s.lower().startswith(title.lower()[:30]): return ""
    return s if len(s) <= 150 else s[:150].rsplit(" ", 1)[0] + "…"

def items(source, url):
    out = []
    try: root = ET.fromstring(get(url))
    except Exception as e: print("skip", source, e); return out
    for it in root.iter():
        if it.tag.split("}")[-1] not in ("item", "entry"): continue
        f = {c.tag.split("}")[-1]: c for c in it}
        title = (f["title"].text or "").strip() if "title" in f else ""
        link = ((f["link"].text or f["link"].get("href") or "").strip()) if "link" in f else ""
        d = f.get("pubDate") or f.get("published") or f.get("updated")
        try:
            when = parsedate_to_datetime(d.text) if "pubDate" in f else datetime.datetime.fromisoformat(d.text.replace("Z", "+00:00"))
            if when.tzinfo is None: when = when.replace(tzinfo=datetime.timezone.utc)
        except Exception: when = datetime.datetime.now(datetime.timezone.utc)
        desc = (f.get("description") or f.get("summary"))
        if title and link:
            out.append({"title": title, "summary": snippet(desc.text if desc is not None else "", title),
                        "source": source, "url": link, "tags": tags(title), "_t": when})
    return out

def chart(sym):
    try:
        j = json.loads(get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=1mo"))
        r = j["chart"]["result"][0]
        pairs = [(t, c) for t, c in zip(r.get("timestamp", []), r["indicators"]["quote"][0]["close"]) if c is not None]
        closes = [c for _, c in pairs]
        off = r["meta"].get("gmtoffset", 0)
        dates = [datetime.datetime.fromtimestamp(t + off, datetime.timezone.utc).strftime("%Y-%m-%d") for t, _ in pairs[-22:]]
        p = r["meta"]["regularMarketPrice"]
        prev = closes[-2] if len(closes) > 1 else r["meta"]["chartPreviousClose"]
        return p, round((p - prev) / prev * 100, 2), [round(c, 2) for c in closes[-22:]], dates
    except Exception as e:
        print("skip", sym, e); return None

from concurrent.futures import ThreadPoolExecutor
threading.Timer(120, lambda: (print("watchdog: took too long, stopping"), os._exit(1))).start()  # hard stop

with ThreadPoolExecutor(8) as ex:   # fetch everything in parallel
    idx_res = list(ex.map(lambda x: chart(x[1]), INDICES))
    stk_res = list(ex.map(chart, WATCH))
    feed_res = {k: list(ex.map(lambda f: items(*f), v)) for k, v in FEEDS.items()}

data = {"indices": [], "stocks": []}
for (n, s), q in zip(INDICES, idx_res):
    if q: data["indices"].append({"name": n, "symbol": s, "value": f"{q[0]:,.2f}", "change": q[1], "series": q[2], "dates": q[3]})
for s, q in zip(WATCH, stk_res):
    if q: data["stocks"].append({"symbol": s, "price": round(q[0], 2), "change": q[1], "series": q[2], "dates": q[3]})
data["stocks"].sort(key=lambda x: x["change"], reverse=True)
for key, lists in feed_res.items():
    allit = sorted([i for l in lists for i in l], key=lambda i: i["_t"], reverse=True)
    seen, keep = set(), []
    for i in allit:
        if i["title"] not in seen and len(keep) < 12:
            seen.add(i["title"]); keep.append(i)
    for i in keep: del i["_t"]
    data[key] = keep
if not (data["indices"] or data["stocks"] or data["ai"] or data["markets"]):
    print("Nothing could be fetched; keeping the previous data.json"); raise SystemExit(1)
data["updated"] = datetime.datetime.now(datetime.timezone.utc).strftime("%d %b %Y, %H:%M UTC")
data["updated_iso"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
json.dump(data, open("data.json", "w"), indent=2)
print("data.json updated:", len(data["indices"]), "indices,", len(data["stocks"]), "stocks,", len(data["ai"]), "AI items,", len(data["markets"]), "market items")
os._exit(0)
