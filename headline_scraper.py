"""
Pre-market headline scraper.

Pulls headlines from a handful of free financial RSS feeds, filters to a
time window (default 8:00-9:30am ET), and prints/saves them as a plain list
-- no scoring, just the raw headlines -- ready to paste into an LLM prompt.

Run this locally (it needs internet access to the RSS hosts below; it will
NOT work inside a sandboxed environment with restricted network egress).

Requires: pip install feedparser pytz
"""

import feedparser
import pytz
import calendar
from datetime import datetime, time as dtime

# --- Config ---------------------------------------------------------------

FEEDS = {
    "CNBC Top News": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
    "CNBC Markets": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
    "MarketWatch Top": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
    "Investing.com": "https://www.investing.com/rss/news.rss",
}

TIMEZONE = pytz.timezone("America/New_York")
WINDOW_START = dtime(8, 0)   # 8:00 AM ET
WINDOW_END = dtime(9, 30)    # 9:30 AM ET

# ---------------------------------------------------------------------------


def in_window(published_struct, window_start=WINDOW_START, window_end=WINDOW_END):
    """Check whether an entry's published time falls inside the ET window AND is from today.
    (Time-of-day alone isn't enough -- a stale entry from yesterday at 8:15am would otherwise
    slip through just because the clock time matches.)
    """
    if published_struct is None:
        return False
    # feedparser normalizes published_parsed to UTC -- timegm() correctly treats
    # the struct as UTC. (mktime() would wrongly assume it's local system time,
    # silently shifting every timestamp.)
    utc_dt = datetime.fromtimestamp(calendar.timegm(published_struct), tz=pytz.utc)
    et_dt = utc_dt.astimezone(TIMEZONE)
    today_et = datetime.now(TIMEZONE).date()
    is_today = et_dt.date() == today_et
    is_in_time_window = window_start <= et_dt.time() <= window_end
    return (is_today and is_in_time_window), et_dt


def collect_headlines(feeds=FEEDS, filter_to_window=True, debug=False):
    """Fetch all feeds and return a list of (source, time_str, headline) tuples.

    Set debug=True to print raw UTC timestamp alongside the converted ET timestamp
    for every entry seen, so you can manually verify the conversion against a
    headline whose real publish time you can look up independently.
    """
    results = []
    for source, url in feeds.items():
        parsed = feedparser.parse(url)
        for entry in parsed.entries:
            published_struct = entry.get("published_parsed") or entry.get("updated_parsed")
            if published_struct is None:
                # No timestamp available -- can't verify it's in-window, so exclude
                # it when filtering is on, rather than letting it slip through unchecked.
                if debug:
                    print(f"DEBUG no timestamp -> EXCLUDED (filter_to_window={filter_to_window}) | "
                          f"{entry.get('title', '').strip()[:60]}")
                if not filter_to_window:
                    results.append((source, "unknown time", entry.get("title", "").strip()))
                continue

            ok, et_dt = in_window(published_struct)

            if debug:
                utc_dt = datetime.fromtimestamp(calendar.timegm(published_struct), tz=pytz.utc)
                print(f"DEBUG raw_utc={utc_dt.strftime('%H:%M UTC')} -> "
                      f"converted_et={et_dt.strftime('%H:%M ET')} | in_window={ok} | "
                      f"{entry.get('title', '').strip()[:60]}")

            if filter_to_window and not ok:
                continue

            time_str = et_dt.strftime("%H:%M ET")
            results.append((source, time_str, entry.get("title", "").strip()))

    # Sort by time where known
    results.sort(key=lambda r: r[1])
    return results


def print_headlines(headlines):
    print(f"\n{len(headlines)} headlines collected\n" + "-" * 40)
    for source, time_str, title in headlines:
        print(f"[{time_str}] ({source}) {title}")


def save_as_plain_list(headlines, path="headlines.txt"):
    """Save just the headline text, one per line -- ready to paste into an LLM prompt."""
    with open(path, "w", encoding="utf-8") as f:
        for _, _, title in headlines:
            f.write(title + "\n")
    print(f"\nSaved {len(headlines)} plain headlines to {path}")


if __name__ == "__main__":
    # Set debug=True the first few times you run this to sanity-check the
    # UTC -> ET conversion against a headline you can verify independently.
    headlines = collect_headlines(filter_to_window=True, debug=False)
    print_headlines(headlines)
    save_as_plain_list(headlines)