"""
VESGLIDX Daily Index Tracker  —  V3.0 (conformed to published factsheet)
Verde ESG Leaders Index — Non-Investable Research Index

This version tracks the EXACT 97-constituent basket published in the V3.0
factsheet (VESGLIDX-FS-V3) at its published weights, so the live index matches
the website factsheet on inception day.

USAGE (Windows / PowerShell):
    cd Documents
    python vesglidx_daily_tracker.py

REQUIREMENTS:
    pip install yfinance pandas numpy

SCHEDULING (Windows Task Scheduler):
    Program:    python
    Arguments:  vesglidx_daily_tracker.py
    Start in:   C:\\Users\\david\\Documents      <-- run from ONE fixed folder
    Trigger:    Daily, weekdays, ~5:00 PM local

MECHANICS:
    * Inception (first run, July 1): the published weights are converted into
      fixed share counts at that day's closing prices, and the index is set to
      1,000.00 exactly. shares_i = weight_i * 1000 / price_i.
    * Each day after: index_value = sum(shares_i * price_i). Weights drift with
      prices (correct buy-and-hold behaviour between rebalances).
    * Quarterly rebalance (first trading day of Jan/Apr/Jul/Oct from Oct 2026;
      Mar/Jun/Sep/Dec before): shares are
      reset to the CURRENT target weights at that day's prices, holding the
      index value continuous (no jump). Update CONSTITUENTS below before a
      rebalance if the published basket has changed.
"""

import yfinance as yf
import pandas as pd
import numpy as np
import json
import os
import time
import base64
import requests
from datetime import datetime, date
import warnings
warnings.filterwarnings('ignore')

# ── CONFIG ────────────────────────────────────────────────────────────────
HISTORY_FILE = 'vesglidx_history.json'
LATEST_FILE  = 'vesglidx_latest.json'
WEIGHTS_FILE = 'vesglidx_weights.json'
MONTHLY_FILE = 'vesglidx_monthly_reports.json'

BASE_VALUE     = 1000.00
INCEPTION_DATE = '2026-07-01'
REBAL_MONTHS   = {1, 4, 7, 10}   # quarterly: first trading day of Jan/Apr/Jul/Oct (from Oct 2026)

# Last-resort manual close overrides, applied ONLY if both Yahoo and Stooq fail
# for a name. Key = ticker, value = that day's official close. Empty for normal
# runs. Example to strike inception if a name won't price from any feed:
#     MANUAL_PRICES = {'MRSH': 162.00}
MANUAL_PRICES = {}

# ── GITHUB PUBLISH (auto-push JSON so the website updates itself) ───────────
# After each run, the files below are pushed to this public repo via the GitHub
# API, so the Lovable site (which fetches the raw file) refreshes on its own.
# The token is read from the GITHUB_TOKEN environment variable — NEVER hardcode
# it in this file. Set PUSH_FILES = [] to turn pushing off.
GITHUB_REPO   = 'davideslattery-jpg/vesglidx-data'
GITHUB_BRANCH = 'main'
PUSH_FILES    = [LATEST_FILE, HISTORY_FILE, WEIGHTS_FILE]

# ── PUBLISHED CONSTITUENTS (Q4 2026 reconstitution, 95 names) ─────────────
# (ticker, GICS sector, Verde ESG Score, target index weight %)
# Effective at the October 1, 2026 close. Source: December-review pro forma
# (verde_pipeline/review_2026-12-01/proforma_constituents_C_fullRubric_netZero.csv),
# scored Sep 30, 2026 under V3.0 with the published climate point schedule.
# The July 1 inception basket (97 names) is in git history (commit fe5f1a9).
CONSTITUENTS = [
    ('META'  , 'Communication Services'  ,  88.29,  10.000000),
    ('NVDA'  , 'Information Technology'  ,  87.20,   7.406093),
    ('V'     , 'Financials'              ,  99.44,   7.284848),
    ('MA'    , 'Financials'              ,  89.63,   4.720658),
    ('ABBV'  , 'Health Care'             ,  89.15,   4.499740),
    ('PM'    , 'Consumer Staples'        ,  96.76,   3.154582),
    ('NFLX'  , 'Communication Services'  ,  93.57,   2.961482),
    ('HD'    , 'Consumer Discretionary'  ,  93.57,   2.890337),
    ('DELL'  , 'Information Technology'  ,  97.38,   2.430278),
    ('TMO'   , 'Health Care'             ,  87.01,   2.375454),
    ('PANW'  , 'Information Technology'  ,  97.79,   2.310726),
    ('AXP'   , 'Financials'              ,  91.83,   2.047426),
    ('VZ'    , 'Communication Services'  ,  91.40,   1.903223),
    ('T'     , 'Communication Services'  ,  91.40,   1.672181),
    ('MCD'   , 'Consumer Discretionary'  ,  92.70,   1.655786),
    ('UNP'   , 'Industrials'             ,  90.90,   1.609487),
    ('ETN'   , 'Industrials'             ,  88.52,   1.606369),
    ('DHR'   , 'Health Care'             ,  94.04,   1.601402),
    ('QCOM'  , 'Information Technology'  ,  94.65,   1.341022),
    ('BKNG'  , 'Consumer Discretionary'  ,  96.96,   1.325357),
    ('PLD'   , 'Real Estate'             ,  95.71,   1.317730),
    ('SPGI'  , 'Financials'              ,  96.06,   1.215263),
    ('BMY'   , 'Health Care'             ,  86.36,   1.203354),
    ('PH'    , 'Industrials'             ,  82.66,   1.091454),
    ('NEM'   , 'Materials'               ,  82.06,   1.088701),
    ('MO'    , 'Consumer Staples'        ,  87.03,   1.070521),
    ('WDC'   , 'Information Technology'  ,  89.26,   1.069017),
    ('MDT'   , 'Health Care'             ,  87.08,   1.049843),
    ('TT'    , 'Industrials'             ,  90.43,   0.984352),
    ('NOW'   , 'Information Technology'  ,  91.36,   0.911071),
    ('FTNT'  , 'Information Technology'  ,  91.36,   0.869297),
    ('MCO'   , 'Financials'              ,  99.44,   0.852281),
    ('MRSH'  , 'Financials'              ,  96.06,   0.849578),
    ('CSX'   , 'Industrials'             ,  86.61,   0.816723),
    ('EMR'   , 'Industrials'             ,  85.17,   0.807269),
    ('CMCSA' , 'Communication Services'  ,  91.40,   0.769087),
    ('ECL'   , 'Materials'               ,  90.69,   0.761848),
    ('ACN'   , 'Information Technology'  ,  93.63,   0.748213),
    ('MRNA'  , 'Health Care'             ,  87.98,   0.726740),
    ('FDX'   , 'Industrials'             ,  90.90,   0.664561),
    ('NSC'   , 'Industrials'             ,  86.61,   0.660141),
    ('ADBE'  , 'Information Technology'  ,  93.63,   0.640932),
    ('CDNS'  , 'Information Technology'  ,  90.26,   0.592520),
    ('LITE'  , 'Information Technology'  ,  93.63,   0.582334),
    ('NDAQ'  , 'Financials'              ,  96.06,   0.539271),
    ('ROK'   , 'Industrials'             ,  97.18,   0.511236),
    ('AON'   , 'Financials'              ,  78.33,   0.496896),
    ('INTU'  , 'Information Technology'  ,  93.63,   0.492713),
    ('IQV'   , 'Health Care'             ,  98.21,   0.473103),
    ('PYPL'  , 'Financials'              ,  93.01,   0.457628),
    ('EBAY'  , 'Consumer Discretionary'  ,  87.35,   0.450631),
    ('VEEV'  , 'Health Care'             ,  87.61,   0.438747),
    ('WAT'   , 'Health Care'             ,  91.81,   0.433030),
    ('MET'   , 'Financials'              ,  66.06,   0.431007),
    ('CARR'  , 'Industrials'             ,  86.40,   0.427001),
    ('ILMN'  , 'Health Care'             ,  91.56,   0.416400),
    ('XYZ'   , 'Financials'              ,  82.86,   0.397029),
    ('CMG'   , 'Consumer Discretionary'  ,  90.53,   0.395226),
    ('MSCI'  , 'Financials'              ,  89.29,   0.380392),
    ('CBRE'  , 'Real Estate'             ,  89.68,   0.363315),
    ('EL'    , 'Consumer Staples'        ,  89.74,   0.329702),
    ('CIEN'  , 'Information Technology'  ,  91.36,   0.320027),
    ('UAL'   , 'Industrials'             ,  81.80,   0.318430),
    ('GEHC'  , 'Health Care'             ,  94.26,   0.306133),
    ('HSY'   , 'Consumer Staples'        ,  83.07,   0.287773),
    ('IR'    , 'Industrials'             ,  89.29,   0.285399),
    ('KHC'   , 'Consumer Staples'        ,  85.05,   0.251885),
    ('OTIS'  , 'Industrials'             ,  90.90,   0.243299),
    ('LVS'   , 'Consumer Discretionary'  ,  90.53,   0.242316),
    ('LH'    , 'Health Care'             ,  88.97,   0.242096),
    ('XYL'   , 'Industrials'             ,  89.42,   0.229572),
    ('TPR'   , 'Consumer Discretionary'  ,  89.46,   0.223192),
    ('NTRS'  , 'Financials'              ,  66.06,   0.222637),
    ('VRSK'  , 'Industrials'             ,  92.51,   0.218566),
    ('CHD'   , 'Consumer Staples'        ,  84.38,   0.205704),
    ('PPL'   , 'Utilities'               ,  71.58,   0.192295),
    ('PPG'   , 'Materials'               ,  75.27,   0.190371),
    ('HPQ'   , 'Information Technology'  ,  90.15,   0.183556),
    ('ESS'   , 'Real Estate'             ,  89.29,   0.180915),
    ('AMCR'  , 'Materials'               ,  84.75,   0.179212),
    ('BBY'   , 'Consumer Discretionary'  ,  86.27,   0.172277),
    ('SBAC'  , 'Real Estate'             ,  91.32,   0.170878),
    ('NRG'   , 'Utilities'               ,  74.54,   0.161852),
    ('BR'    , 'Industrials'             ,  81.45,   0.160677),
    ('EFX'   , 'Industrials'             ,  82.81,   0.144818),
    ('BALL'  , 'Materials'               ,  86.56,   0.140979),
    ('HAS'   , 'Consumer Discretionary'  ,  93.53,   0.126510),
    ('AVY'   , 'Materials'               ,  85.02,   0.120211),
    ('TECH'  , 'Health Care'             ,  87.61,   0.108033),
    ('BXP'   , 'Real Estate'             ,  89.29,   0.107429),
    ('DECK'  , 'Consumer Discretionary'  ,  90.53,   0.104462),
    ('CSGP'  , 'Real Estate'             ,  87.61,   0.104325),
    ('PTC'   , 'Information Technology'  ,  93.63,   0.102202),
    ('FRT'   , 'Real Estate'             ,  87.79,   0.090083),
    ('CLX'   , 'Consumer Staples'        ,  83.32,   0.089276),
]

TICKERS       = [c[0] for c in CONSTITUENTS]
SECTOR        = {c[0]: c[1] for c in CONSTITUENTS}
ESG_SCORE     = {c[0]: c[2] for c in CONSTITUENTS}
_raw_w        = {c[0]: c[3] for c in CONSTITUENTS}
_w_total      = sum(_raw_w.values())
TARGET_WEIGHT = {k: v / _w_total for k, v in _raw_w.items()}   # normalised to 1.0


# ── HELPERS ───────────────────────────────────────────────────────────────
def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default

def save_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


def push_to_github():
    """Publish the JSON outputs to the public data repo via the GitHub API so
    the website updates itself. No git install needed. Reads the token from the
    GITHUB_TOKEN environment variable; skips quietly if it isn't set."""
    if not PUSH_FILES:
        return
    token = os.environ.get('GITHUB_TOKEN')
    if not token:
        print("  (GitHub push skipped: GITHUB_TOKEN environment variable not set)")
        return
    headers = {'Authorization': f'Bearer {token}',
               'Accept': 'application/vnd.github+json',
               'X-GitHub-Api-Version': '2022-11-28'}
    for path in PUSH_FILES:
        if not os.path.exists(path):
            continue
        with open(path, 'rb') as fh:
            content_b64 = base64.b64encode(fh.read()).decode()
        api = f'https://api.github.com/repos/{GITHUB_REPO}/contents/{path}'
        # An update needs the file's current SHA; a first-time create does not.
        sha = None
        try:
            r = requests.get(api, headers=headers,
                             params={'ref': GITHUB_BRANCH}, timeout=20)
            if r.status_code == 200:
                sha = r.json().get('sha')
        except Exception:
            pass
        payload = {'message': f'Update {path} {datetime.now().strftime("%Y-%m-%d %H:%M")}',
                   'content': content_b64, 'branch': GITHUB_BRANCH}
        if sha:
            payload['sha'] = sha
        try:
            r = requests.put(api, headers=headers, json=payload, timeout=30)
            if r.status_code in (200, 201):
                print(f"  pushed {path} -> GitHub")
            else:
                print(f"  GitHub push FAILED for {path}: "
                      f"{r.status_code} {r.text[:140]}")
        except Exception as e:
            print(f"  GitHub push error for {path}: {e}")


def _stooq_series(ticker):
    """Daily {date_str: close} for a US ticker from Stooq (free, no API key)."""
    sym = ticker.lower().replace('.', '-')          # class shares: BRK.B -> brk-b
    url = f'https://stooq.com/q/d/l/?s={sym}.us&i=d'
    try:
        df = pd.read_csv(url)
    except Exception:
        return {}
    if df is None or df.empty or 'Close' not in df.columns or 'Date' not in df.columns:
        return {}
    df = df.dropna(subset=['Close'])
    return {str(d): float(c) for d, c in zip(df['Date'], df['Close'])}


def _stooq_close(ticker, ref_date=None):
    """Close on ref_date if present, else the most recent close on/before it;
    if ref_date is None, the latest available close. None on total failure.
    NOTE: Stooq closes are raw (not split/div-adjusted). For a single straggler
    at/near inception this is immaterial — it just fills the name Yahoo drops."""
    s = _stooq_series(ticker)
    if not s:
        return None
    if ref_date is None:
        return s[max(s)]
    if ref_date in s:
        return s[ref_date]
    earlier = [d for d in s if d <= ref_date]
    return s[max(earlier)] if earlier else None


def _extract_close(df, single_ticker_cols, target_date=None):
    """From a yfinance frame return ({ticker: close}, chosen_date_str).
    Picks the target_date row (exact, else most recent on/before); when
    target_date is None, uses the last available row."""
    if df is None or df.empty:
        return {}, None
    if isinstance(df.columns, pd.MultiIndex):
        closes = df['Close']
    else:
        closes = df[['Close']]
        closes.columns = single_ticker_cols[:1]
    closes = closes.dropna(how='all')
    if closes.empty:
        return {}, None
    if target_date is not None:
        idx = [d for d in closes.index if d.strftime('%Y-%m-%d') <= target_date]
        sel = max(idx) if idx else closes.index[-1]
    else:
        sel = closes.index[-1]
    row = closes.loc[sel].to_dict()
    return ({k: float(v) for k, v in row.items() if pd.notna(v)},
            sel.strftime('%Y-%m-%d'))


def fetch_prices(tickers, target_date=None):
    """Yahoo batch -> Yahoo per-ticker retry -> Stooq fallback -> manual override.
    When target_date is set (e.g. inception), prices are pinned to that trading
    date, so the run can happen the next day and still strike the right close.
    Returns (prices_dict, price_date_str, missing_list)."""
    prices, latest = {}, None

    # 1) Yahoo batch
    for attempt in range(2):
        try:
            df = yf.download(tickers, period='5d', interval='1d',
                             auto_adjust=True, progress=False, threads=False)
            prices, latest = _extract_close(df, tickers, target_date)
            break
        except Exception as e:
            print(f"  batch download attempt {attempt+1} failed: {e}")
            time.sleep(2)

    # 2) Yahoo per-ticker retry for stragglers
    missing = [t for t in tickers if t not in prices]
    if missing:
        print(f"  retrying {len(missing)} ticker(s) individually...")
        for t in missing:
            for attempt in range(2):
                try:
                    df = yf.download(t, period='5d', interval='1d',
                                     auto_adjust=True, progress=False, threads=False)
                    px_map, dt = _extract_close(df, [t], target_date)
                    if px_map:
                        prices[t] = list(px_map.values())[0]
                        if latest is None:
                            latest = dt
                        break
                except Exception:
                    pass
                time.sleep(1)

    # 3) Stooq fallback for names Yahoo refuses (transient "delisted" errors)
    missing = [t for t in tickers if t not in prices]
    if missing:
        ref = target_date or latest
        print(f"  Yahoo still missing {len(missing)}; trying Stooq fallback...")
        for t in list(missing):
            px = _stooq_close(t, ref)
            if px is not None:
                prices[t] = px
                if latest is None and ref is not None:
                    latest = ref
                print(f"    Stooq: {t} = {px:.2f}")

    # 4) Manual overrides — final safety net (see MANUAL_PRICES at top of file)
    for t, px in MANUAL_PRICES.items():
        if t in tickers and t not in prices:
            prices[t] = float(px)
            print(f"    Manual override: {t} = {float(px):.2f}")

    missing = [t for t in tickers if t not in prices]
    return prices, latest, missing


def is_rebalancing_day(market_date, history, last_rebal_date=None):
    """First trading day of a rebalancing month, judged by the MARKET-DATA date.

    FIX (Sep 2026): this used the run date. GitHub Actions cron runs can start
    after midnight UTC, so a run dated Sep 1 processing the Aug 31 close
    triggered a spurious 'rebalance' stamped Aug 31. We now test the market
    date, and never rebalance twice in the same month."""
    if not history.get('daily'):
        return False  # inception handled separately
    if market_date.month not in REBAL_MONTHS:
        return False
    if last_rebal_date and last_rebal_date[:7] == market_date.strftime('%Y-%m'):
        return False  # already rebalanced this month
    last_date = datetime.strptime(history['daily'][-1]['date'], '%Y-%m-%d').date()
    return last_date.month != market_date.month


def compute_shares(target_weights, prices, index_value):
    """shares_i = w_i * index_value / price_i (only for priced names)."""
    return {t: (w * index_value) / prices[t]
            for t, w in target_weights.items()
            if t in prices and prices[t] > 0}


# ── CATCH-UP (Oct 2026) ───────────────────────────────────────────────────
# Yahoo's newest daily bar is sometimes incomplete for a few hours after the
# close. The tracker used to take "the newest row", so a late run could record
# the PREVIOUS day again and today was skipped for good (Sep 2026: 9/14, 9/16,
# 9/21, 9/25, 9/29, 9/30). Each run now pulls the last month of closes and
# records EVERY trading day the history is missing since the last rebalance,
# but only days on which at least MIN_COVERAGE of constituents have a close.
# An incomplete newest bar is skipped and picked up by the next run.
CATCHUP_PERIOD = '1mo'
MIN_COVERAGE   = 0.95


def _close_frame(df, ticker=None):
    """yfinance frame -> DataFrame of closes (columns = tickers)."""
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        closes = df['Close']
        if ticker is not None and isinstance(closes, pd.DataFrame):
            closes = closes.iloc[:, :1]
            closes.columns = [ticker]
        return closes
    return df[['Close']].rename(columns={'Close': ticker})


def fetch_price_panel(tickers, period=CATCHUP_PERIOD):
    """Daily closes for the last ~month: index = 'YYYY-MM-DD', columns = tickers."""
    panel = pd.DataFrame()
    for attempt in range(2):
        try:
            panel = _close_frame(yf.download(tickers, period=period, interval='1d',
                                             auto_adjust=True, progress=False,
                                             threads=False))
            if not panel.empty:
                break
        except Exception as e:
            print(f"  panel download attempt {attempt+1} failed: {e}")
            time.sleep(2)
    missing = [t for t in tickers if t not in panel.columns or panel[t].isna().all()]
    if missing:
        print(f"  retrying {len(missing)} ticker(s) individually...")
    for t in missing:
        try:
            one = _close_frame(yf.download(t, period=period, interval='1d',
                                           auto_adjust=True, progress=False,
                                           threads=False), ticker=t)
            if not one.empty:
                panel = one if panel.empty else panel.drop(columns=[t], errors='ignore').join(one, how='outer')
        except Exception:
            pass
        time.sleep(1)
    if panel.empty:
        return panel
    panel.index = [pd.Timestamp(d).strftime('%Y-%m-%d') for d in panel.index]
    panel = panel[~panel.index.duplicated(keep='last')]
    return panel.sort_index()


def catch_up(history, weights_data, today):
    """Record every complete trading day missing since the last rebalance.
    Returns True if anything was written."""
    universe = sorted(set(TICKERS) | set(weights_data.get('shares', {})))
    panel = fetch_price_panel(universe)
    if panel.empty:
        print("ERROR: no price data returned. Nothing written.")
        return False
    today_str = today.strftime('%Y-%m-%d')
    have = {r['date'] for r in history['daily']}
    rebal = weights_data.get('rebal_date', INCEPTION_DATE)
    last = history['daily'][-1]['date']
    print(f"Price data covers {panel.index[0]} .. {panel.index[-1]}; "
          f"history ends {last}")

    todo = []
    for d in panel.index:
        if d <= rebal or d in have or d > today_str:
            continue
        row = panel.loc[d]
        n = sum(1 for t in universe if t in row.index and pd.notna(row[t]))
        if n >= MIN_COVERAGE * len(universe):
            todo.append(d)
        else:
            print(f"  {d}: only {n}/{len(universe)} closes available yet — "
                  "skipped, the next run will pick it up")
    if not todo:
        print("No new complete trading days to record.")
        return False

    for d in sorted(todo):
        prev = max((r for r in history['daily'] if r['date'] < d),
                   key=lambda r: r['date'])
        row = panel.loc[d]
        prices = {t: float(row[t]) for t in universe
                  if t in row.index and pd.notna(row[t])}
        for t in universe:                         # forward-fill stragglers
            if t not in prices and t in prev.get('prices', {}):
                prices[t] = prev['prices'][t]
        shares = weights_data['shares']
        value = sum(shares[t] * prices[t] for t in shares if t in prices)
        rebalanced = False
        if d > last and is_rebalancing_day(
                datetime.strptime(d, '%Y-%m-%d').date(), history,
                weights_data.get('rebal_date')):
            print(f"\n>>> QUARTERLY REBALANCE at the {d} close <<<")
            weights_data.clear()
            weights_data.update({
                'shares': compute_shares(TARGET_WEIGHT, prices, value),
                'rebal_date': d,
                'rebal_prices': prices,
                'target_weights': TARGET_WEIGHT,
            })
            save_json(WEIGHTS_FILE, weights_data)
            rebalanced = True
        kind = 'gap filled' if d < last else 'new day'
        print(f"  {d}: {value:,.4f}  ({kind}{', REBALANCED' if rebalanced else ''})")
        history['daily'].append({
            'date': d,
            'index_value': round(value, 4),
            'daily_return_pct': 0.0,
            'prices': {t: prices[t] for t in universe if t in prices},
            'rebalanced': rebalanced,
        })
        history['daily'].sort(key=lambda r: r['date'])
        last = max(last, d)

    # daily returns follow from consecutive levels (fixes the day after a gap)
    daily = history['daily']
    for i, r in enumerate(daily):
        r['daily_return_pct'] = 0.0 if i == 0 else round(
            (r['index_value'] / daily[i - 1]['index_value'] - 1) * 100, 4)
    save_json(HISTORY_FILE, history)
    return True


# ── MAIN ──────────────────────────────────────────────────────────────────
def main():
    today = date.today()
    today_str = today.strftime('%Y-%m-%d')
    print(f"VESGLIDX Daily Tracker (V3.0) — {today_str}")
    print("=" * 52)

    history = load_json(HISTORY_FILE, {'daily': [], 'metadata': {
        'index_name': 'Verde ESG Leaders Index',
        'ticker': 'VESGLIDX',
        'base_value': BASE_VALUE,
        'inception_date': INCEPTION_DATE,
        'methodology': 'VESGLIDX-FS-V3',
    }})
    weights_data = load_json(WEIGHTS_FILE, {})
    inception = not history['daily']

    # SAFETY GUARD (Sep 2026): the index is live. If the history file is
    # missing (wrong folder, fresh Colab, failed checkout), NEVER strike a new
    # inception — that would overwrite the published index with 1,000.00.
    if inception and today > datetime.strptime(INCEPTION_DATE, '%Y-%m-%d').date():
        print(f"ERROR: no {HISTORY_FILE} found in {os.getcwd()}, but the index "
              f"has been live since {INCEPTION_DATE}. Refusing to start a new "
              "index. Run from the folder/repo that holds the live JSON files. "
              "Nothing written, nothing pushed.")
        return

    if not inception:
        if not catch_up(history, weights_data, today):
            print("Nothing written, nothing pushed.")
            return
        last_rec = history['daily'][-1]
        publish_snapshot(history, weights_data, today, last_rec['date'],
                         last_rec['index_value'], last_rec['daily_return_pct'] / 100)
        return

    print(f"Fetching prices for {len(TICKERS)} constituents...")
    # At inception, pin to the published inception date so a next-day run still
    # strikes the correct 07-01 basket (not that day's close).
    target = INCEPTION_DATE if inception else None
    prices, latest_date, missing = fetch_prices(TICKERS, target_date=target)
    if not prices:
        print("ERROR: no price data returned. Market closed or network issue. "
              "Nothing written.")
        return
    print(f"Latest market data: {latest_date}")
    print(f"Tickers with valid prices: {len(prices)}/{len(TICKERS)}")
    if missing:
        print(f"Missing: {', '.join(missing)}")

    # Stamp every stored record with the MARKET-DATA date, not the run date, so a
    # next-day run of a prior close is labelled correctly (a 07-02 run of the
    # 07-01 close is dated 07-01) and re-running the same day cannot clobber it.
    stamp = latest_date or today_str

    # ── INCEPTION: must have a complete basket ──
    if inception:
        if missing:
            print("\n*** INCEPTION ABORTED ***")
            print("Inception requires all 97 constituents to price so the "
                  "starting basket matches the factsheet exactly.")
            print("Re-run after market close (the missing names are almost "
                  "always transient yfinance failures). Nothing was written.")
            return
        shares = compute_shares(TARGET_WEIGHT, prices, BASE_VALUE)
        index_value = sum(shares[t] * prices[t] for t in shares)   # == 1000.00
        daily_return = 0.0
        weights_data = {
            'shares': shares,
            'rebal_date': stamp,
            'rebal_prices': prices,
            'target_weights': TARGET_WEIGHT,
        }
        save_json(WEIGHTS_FILE, weights_data)
        rebalanced = True
        print(f"\nINCEPTION DAY — index set to {BASE_VALUE:,.2f} "
              f"on all {len(shares)} constituents")
    # ── Append to history ──
    entry = {
        'date': stamp,
        'index_value': round(index_value, 4),
        'daily_return_pct': round(daily_return * 100, 4),
        'prices': {t: prices[t] for t in TICKERS if t in prices},
        'rebalanced': rebalanced,
    }
    history['daily'] = [d for d in history['daily'] if d['date'] != stamp]
    history['daily'].append(entry)
    history['daily'].sort(key=lambda x: x['date'])
    save_json(HISTORY_FILE, history)
    publish_snapshot(history, weights_data, today, stamp, index_value, daily_return)


def publish_snapshot(history, weights_data, today, stamp, index_value, daily_return):
    # ── Latest snapshot (for website) ──
    total_return = (index_value / BASE_VALUE - 1) * 100
    # YTD: measure from the prior year-end close. If the index launched this
    # year (no prior-year data), measure from the inception base value, so YTD
    # equals since-inception in the launch year rather than defaulting to 0.
    year = int(stamp[:4])
    prior_year = [d for d in history['daily'] if int(d['date'][:4]) < year]
    if prior_year:
        ytd_base = max(prior_year, key=lambda d: d['date'])['index_value']
    else:
        ytd_base = BASE_VALUE
    ytd_return = (index_value / ytd_base - 1) * 100

    latest = {
        'index_name': 'Verde ESG Leaders Index',
        'ticker': 'VESGLIDX',
        'as_of_date': stamp,
        'index_value': round(index_value, 2),
        'daily_change_pct': round(daily_return * 100, 3),
        'ytd_return_pct': round(ytd_return, 2) if ytd_return is not None else None,
        'since_inception_return_pct': round(total_return, 2),
        'base_value': BASE_VALUE,
        'inception_date': INCEPTION_DATE,
        'constituent_count': len(weights_data.get('shares', {})) or len(TICKERS),
        'last_rebalance_date': weights_data['rebal_date'],
        'updated_at': datetime.now().isoformat(),
    }
    save_json(LATEST_FILE, latest)

    print("\n" + "=" * 52)
    print(f"VESGLIDX: {index_value:,.2f}  ({daily_return*100:+.3f}% today)")
    if ytd_return is not None:
        print(f"YTD: {ytd_return:+.2f}%")
    print(f"Since inception: {total_return:+.2f}%")
    print("=" * 52)

    generate_monthly_report_if_needed(history, today)

    print("\nFiles updated:")
    print(f"  {HISTORY_FILE}  (full daily history)")
    print(f"  {LATEST_FILE}   (current snapshot — feed this to website)")
    print(f"  {WEIGHTS_FILE}  (current shares / target weights)")

    print("\nPublishing to GitHub (site auto-updates)...")
    push_to_github()


def generate_monthly_report_if_needed(history, today):
    daily = history['daily']
    if len(daily) < 2:
        return
    current_month = today.strftime('%Y-%m')
    month_entries = [d for d in daily if d['date'].startswith(current_month)]
    if not month_entries:
        return
    prior = [d for d in daily if d['date'] < month_entries[0]['date']]
    start_value = prior[-1]['index_value'] if prior else month_entries[0]['index_value']
    end_value   = month_entries[-1]['index_value']
    month_return = (end_value / start_value - 1) * 100

    monthly = load_json(MONTHLY_FILE, {'reports': []})
    monthly['reports'] = [r for r in monthly['reports'] if r['month'] != current_month]
    monthly['reports'].append({
        'month': current_month,
        'start_value': round(start_value, 2),
        'end_value': round(end_value, 2),
        'month_return_pct': round(month_return, 2),
        'trading_days': len(month_entries),
        'generated_at': datetime.now().isoformat(),
    })
    monthly['reports'].sort(key=lambda x: x['month'])
    save_json(MONTHLY_FILE, monthly)
    print(f"\nMonthly report updated for {current_month}: {month_return:+.2f}%")


if __name__ == '__main__':
    main()
