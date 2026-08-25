"""
Daily 9:25am ET pre-market report.

Combines three things into one text file:
  1. Headlines -- reuses headline_scraper.collect_headlines() UNCHANGED
     (same feeds, same time-window/timezone logic, same filtering).
  2. VIX level -- via yfinance.
  3. CME FedWatch rate-probability table -- via the `cme-fedwatch` package
     (unofficial, but pulls from CME settlement + FRED, no Selenium/scraping
     of the JS-heavy QuikStrike widget, which is what the CME page actually
     embeds and is not realistically scrapable with requests/BeautifulSoup).

Designed to run unattended on a schedule (see .github/workflows/daily_report.yml).
Because GitHub Actions cron is UTC-only and ET shifts with daylight saving,
this script is invoked at TWO candidate UTC times every weekday and simply
exits immediately if the current time in America/New_York isn't actually
close to 9:25am -- so only the correct one of the two ever does real work.
Set FORCE_RUN=1 to bypass that check (used for manual testing).

Requires: pip install -r requirements.txt
"""

import os
import sys
from datetime import datetime

import pytz

# Import the scraper's collection logic as-is -- do not reimplement it.
from headline_scraper import collect_headlines

ET = pytz.timezone("America/New_York")

# How close to 9:25am ET "now" has to be for this run to do real work.
TARGET_HOUR, TARGET_MINUTE = 9, 25
WINDOW_MINUTES = 10  # accept 9:15-9:35 ET, generous enough for runner start-up lag


def should_run_now():
    if os.environ.get("FORCE_RUN") == "1":
        return True
    now_et = datetime.now(ET)
    target = now_et.replace(hour=TARGET_HOUR, minute=TARGET_MINUTE, second=0, microsecond=0)
    return abs((now_et - target).total_seconds()) <= WINDOW_MINUTES * 60


def get_vix():
    """Current VIX level via yfinance. Returns a string; never raises."""
    try:
        import yfinance as yf
        vix = yf.Ticker("^VIX")
        hist = vix.history(period="1d", interval="1m")
        if hist.empty:
            hist = vix.history(period="5d")
        last = hist["Close"].dropna().iloc[-1]
        as_of = hist.index[-1]
        return f"{last:.2f} (as of {as_of.strftime('%Y-%m-%d %H:%M %Z')})"
    except Exception as e:
        return f"UNAVAILABLE -- error: {e}"


def get_fedwatch_raw_table():
    """Scrape the EXACT raw probability table text off the live CME FedWatch
    page -- same numbers/columns (Now, 1 Day, 1 Week, 1 Month) and same rate
    tiers a human would see and copy-paste, including near-zero tiers like
    400-425 that the cme_fedwatch package's free feed can't reliably show.

    The tool itself is a JS widget (quikstrike.net) inside an iframe, so a
    plain HTTP request just gets an empty shell -- this uses a real headless
    browser to render it first. Returns the raw text block, or None if
    anything about the page/widget didn't load as expected (site changed,
    slow network, etc.) so the caller can fall back to the API-based summary.
    """
    from playwright.sync_api import sync_playwright

    url = "https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=60000)

            # Dismiss a cookie-consent overlay if one shows up, so it doesn't
            # block the widget from rendering underneath it.
            for selector in ["#onetrust-accept-btn-handler", "text=Accept All Cookies", "text=Accept"]:
                try:
                    page.click(selector, timeout=3000)
                    break
                except Exception:
                    continue

            # The tool renders inside an iframe pointing at quikstrike.net.
            frame = None
            for _ in range(20):  # give the iframe a few seconds to appear
                for f in page.frames:
                    if "quikstrike" in (f.url or ""):
                        frame = f
                        break
                if frame:
                    break
                page.wait_for_timeout(500)

            if frame is None:
                browser.close()
                return None

            frame.wait_for_selector("text=Target Rate", timeout=30000)
            text = frame.inner_text("body")
            browser.close()

        start = text.find("Target Rate")
        if start == -1:
            return None
        end = text.find("Data as of")
        if end == -1:
            return text[start:start + 2000].strip()
        end = text.find("\n", end)
        return text[start: end if end != -1 else len(text)].strip()
    except Exception:
        return None


def get_fedwatch():
    """Next-meeting probabilities + short lookback history via cme_fedwatch.
    Used only as a fallback if the raw-table scrape fails.
    Returns a formatted multi-line string; never raises."""
    try:
        from cme_fedwatch import get_probabilities, get_history

        probs = get_probabilities("next")
        hist = get_history("next", days=10)

        lines = []
        lines.append(f"EFFR: {probs.get('effr')}%   Current target: {probs.get('current_target')}")

        status = probs.get("schedule_status") or {}
        if status.get("state") not in (None, "ok"):
            lines.append(
                f"[WARNING] FOMC schedule status: {status.get('state')} "
                f"(remaining={status.get('remaining')}, last_known={status.get('last_known')}) "
                f"-- the cme-fedwatch package's hardcoded meeting schedule may need updating."
            )

        meetings = probs.get("meetings") or []
        if meetings:
            m = meetings[0]
            lines.append(f"\nNext meeting: {m.get('date')}  (contract {m.get('contract')})")
            lines.append("Current probabilities:")
            for rate_range, pct in m.get("probabilities", {}).items():
                lines.append(f"  {rate_range}: {pct}%")
        else:
            lines.append("\n[No upcoming meeting data returned]")

        lookback = hist.get("lookback") or []
        if lookback:
            lines.append("\nLookback comparison:")
            for snap in lookback:
                label = snap.get("label")
                trade_date = snap.get("trade_date")
                probs_str = ", ".join(
                    f"{rate_range}: {pct}%" for rate_range, pct in snap.get("probabilities", {}).items()
                )
                lines.append(f"  {label} (as of {trade_date}): {probs_str}")
        else:
            lines.append("\n[No lookback/history data available -- CME's free feed only retains ~5 business days]")

        return "\n".join(lines)
    except Exception as e:
        return f"UNAVAILABLE -- error: {e}"


def build_report():
    now_et = datetime.now(ET)
    date_str = now_et.strftime("%Y-%m-%d")

    headlines = collect_headlines(filter_to_window=True, debug=False)

    lines = []
    lines.append(f"PRE-MARKET REPORT -- {now_et.strftime('%A, %B %d, %Y %H:%M %Z')}")
    lines.append("=" * 60)

    lines.append("\n--- VIX ---")
    lines.append(get_vix())

    lines.append("\n--- CME FedWatch ---")
    raw_table = get_fedwatch_raw_table()
    if raw_table:
        lines.append(raw_table)
    else:
        lines.append("[Raw table scrape failed -- falling back to cme_fedwatch API summary]")
        lines.append(get_fedwatch())

    lines.append(f"\n--- Headlines ({len(headlines)}) ---")
    for source, time_str, title in headlines:
        lines.append(f"[{time_str}] ({source}) {title}")

    return date_str, "\n".join(lines)


def main():
    if not should_run_now():
        print("Not within the 9:25am ET window (and FORCE_RUN not set) -- skipping, no output written.")
        return

    date_str, report_text = build_report()

    os.makedirs("reports", exist_ok=True)
    out_path = os.path.join("reports", f"report_{date_str}.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    print(f"Wrote {out_path}")
    print(report_text)


if __name__ == "__main__":
    sys.exit(main())
