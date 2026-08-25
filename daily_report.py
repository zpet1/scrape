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


def get_fedwatch():
    """Next-meeting probabilities + short lookback history via cme_fedwatch.
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

        lookback = hist.get("lookback") or {}
        if lookback:
            lines.append("\nLookback comparison (probability of no-change column, where available):")
            for period, snapshot in lookback.items():
                lines.append(f"  {period}: {snapshot}")
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
