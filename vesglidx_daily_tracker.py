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
    * Quarterly rebalance (first trading day of Mar/Jun/Sep/Dec): shares are
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
REBAL_MONTHS   = {3, 6, 9, 12}   # quarterly

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

# ── PUBLISHED CONSTITUENTS (VESGLIDX-FS-V3, 97 names) ──────────────────────
# (ticker, GICS sector, Verde ESG Score, published index weight %)
# Source of truth = VESGLIDX_Factsheet V3.0. Weights are normalised below.
#
# NOTE: Marsh & McLennan rebranded to "Marsh" and CHANGED its NYSE ticker from
# MMC to MRSH (effective ~July 1, 2026). MMC no longer resolves on any feed;
# MRSH is the live symbol. The factsheet's original "MRSH" was correct and
# should stand. (This reverses the earlier, now-outdated MRSH->MMC switch.)
CONSTITUENTS = [
    ('META',  'Communication Services', 88.3, 10.00),
    ('V',     'Financials',             93.0,  6.27),
    ('NVDA',  'Information Technology',  87.5,  6.16),
    ('MSFT',  'Information Technology',  87.5,  6.16),
    ('MA',    'Financials',             89.6,  4.26),
    ('ABBV',  'Health Care',            89.2,  3.92),
    ('NFLX',  'Communication Services', 93.6,  3.55),
    ('UNH',   'Health Care',            84.8,  3.37),
    ('HD',    'Consumer Discretionary', 93.6,  3.18),
    ('PM',    'Consumer Staples',       90.2,  2.75),
    ('AXP',   'Financials',             85.4,  1.98),
    ('VZ',    'Communication Services', 91.4,  1.90),
    ('MCD',   'Consumer Discretionary', 86.4,  1.88),
    ('AMGN',  'Health Care',            85.5,  1.77),
    ('UNP',   'Industrials',            91.1,  1.61),
    ('T',     'Communication Services', 91.4,  1.58),
    ('GILD',  'Health Care',            86.2,  1.51),
    ('ETN',   'Industrials',            83.2,  1.40),
    ('DE',    'Industrials',            80.2,  1.38),
    ('PLD',   'Real Estate',            89.4,  1.33),
    ('BKNG',  'Consumer Discretionary', 90.6,  1.28),
    ('DELL',  'Information Technology',  92.1,  1.27),
    ('SPGI',  'Financials',             89.6,  1.23),
    ('QCOM',  'Information Technology',  89.4,  1.10),
    ('PANW',  'Information Technology',  91.4,  1.09),
    ('MO',    'Consumer Staples',       82.3,  1.09),
    ('PH',    'Industrials',            82.7,  1.01),
    ('MDT',   'Health Care',            87.0,  1.00),
    ('NEM',   'Materials',              82.0,  0.96),
    ('TT',    'Industrials',            85.5,  0.95),
    ('CMCSA', 'Communication Services', 91.4,  0.85),
    ('ELV',   'Health Care',            86.0,  0.85),
    ('WDC',   'Information Technology',  89.2,  0.85),
    ('CSX',   'Industrials',            86.8,  0.83),
    ('MCO',   'Financials',             93.0,  0.80),
    ('FDX',   'Industrials',            91.1,  0.79),
    ('MRSH',  'Financials',             89.6,  0.78),   # was MMC; NYSE ticker changed to MRSH (Marsh rebrand, ~Jul 2026)
    ('ECL',   'Materials',              84.8,  0.67),
    ('NSC',   'Industrials',            82.0,  0.63),
    ('AON',   'Financials',             78.3,  0.60),
    ('NOW',   'Information Technology',  91.4,  0.57),
    ('FTNT',  'Information Technology',  91.4,  0.52),
    ('ACN',   'Information Technology',  87.5,  0.52),
    ('ROK',   'Industrials',            92.0,  0.50),
    ('NDAQ',  'Financials',             89.6,  0.48),
    ('ADBE',  'Information Technology',  87.5,  0.48),
    ('EW',    'Health Care',            83.2,  0.45),
    ('MSCI',  'Financials',             82.9,  0.41),
    ('MET',   'Financials',             66.1,  0.39),
    ('INTU',  'Information Technology',  87.5,  0.38),
    ('CMG',   'Consumer Discretionary', 90.6,  0.37),
    ('PYPL',  'Financials',             93.0,  0.37),
    ('KDP',   'Consumer Staples',       81.1,  0.37),
    ('XYZ',   'Financials',             82.9,  0.37),   # Block, Inc. (was SQ)
    ('CIEN',  'Information Technology',  91.4,  0.34),
    ('WAT',   'Health Care',            86.6,  0.34),
    ('HSY',   'Consumer Staples',       82.3,  0.34),
    ('LVS',   'Consumer Discretionary', 90.6,  0.33),
    ('LITE',  'Information Technology',  87.5,  0.32),
    ('UAL',   'Industrials',            82.0,  0.31),
    ('IQV',   'Health Care',            91.9,  0.31),
    ('KVUE',  'Consumer Staples',       81.7,  0.30),
    ('EL',    'Consumer Staples',       89.9,  0.30),
    ('PCG',   'Utilities',              70.0,  0.29),
    ('GEHC',  'Health Care',            89.1,  0.29),
    ('VEEV',  'Health Care',            87.8,  0.27),
    ('OTIS',  'Industrials',            91.1,  0.27),
    ('TPR',   'Consumer Discretionary', 84.6,  0.26),
    ('IR',    'Industrials',            84.2,  0.26),
    ('XYL',   'Industrials',            89.8,  0.26),
    ('NTRS',  'Financials',             66.1,  0.23),
    ('VRSK',  'Industrials',            86.3,  0.23),
    ('NRG',   'Utilities',              74.5,  0.22),
    ('SBAC',  'Real Estate',            91.5,  0.22),
    ('LH',    'Health Care',            89.1,  0.21),
    ('CHD',   'Consumer Staples',       84.0,  0.21),
    ('PPL',   'Utilities',              71.6,  0.21),
    ('PPG',   'Materials',              75.2,  0.21),
    ('RL',    'Consumer Discretionary', 86.1,  0.21),
    ('ESS',   'Real Estate',            89.4,  0.19),
    ('ES',    'Utilities',              65.2,  0.19),
    ('EFX',   'Industrials',            82.8,  0.19),
    ('NTAP',  'Information Technology',  87.5,  0.16),
    ('LII',   'Industrials',            80.4,  0.16),
    ('AMCR',  'Materials',              78.9,  0.15),
    ('DECK',  'Consumer Discretionary', 90.6,  0.15),
    ('BBY',   'Consumer Discretionary', 86.4,  0.14),
    ('REG',   'Real Estate',            85.1,  0.14),
    ('BALL',  'Materials',              86.2,  0.13),
    ('DOC',   'Real Estate',            85.1,  0.13),   # Healthpeak Properties
    ('HAS',   'Consumer Discretionary', 87.2,  0.11),
    ('AVY',   'Materials',              84.7,  0.11),
    ('BXP',   'Real Estate',            89.4,  0.11),
    ('SWK',   'Industrials',            80.8,  0.11),
    ('CLX',   'Consumer Staples',       84.1,  0.10),
    ('FRT',   'Real Estate',            88.1,  0.10),
    ('PTC',   'Information Technology',  87.5,  0.07),
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


def is_rebalancing_day(today, history):
    """First trading day of a rebalancing month (history already exists)."""
    if not history.get('daily'):
        return False  # inception handled separately
    if today.month not in REBAL_MONTHS:
        return False
    last_date = datetime.strptime(history['daily'][-1]['date'], '%Y-%m-%d').date()
    return last_date.month != today.month


def compute_shares(target_weights, prices, index_value):
    """shares_i = w_i * index_value / price_i (only for priced names)."""
    return {t: (w * index_value) / prices[t]
            for t, w in target_weights.items()
            if t in prices and prices[t] > 0}


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
    else:
        prev = history['daily'][-1]
        prev_value  = prev['index_value']
        prev_prices = prev.get('prices', {})
        # forward-fill any name missing today with its last known price
        for t in TICKERS:
            if t not in prices and t in prev_prices:
                prices[t] = prev_prices[t]

        rebalanced = is_rebalancing_day(today, weights_data and history or history)
        if rebalanced:
            print("\n>>> QUARTERLY REBALANCE <<<")
            shares = compute_shares(TARGET_WEIGHT, prices, prev_value)  # continuity
            weights_data = {
                'shares': shares,
                'rebal_date': stamp,
                'rebal_prices': prices,
                'target_weights': TARGET_WEIGHT,
            }
            save_json(WEIGHTS_FILE, weights_data)
            print(f"Shares reset to target weights for {len(shares)} constituents")
        else:
            shares = weights_data.get('shares', {})

        index_value = sum(shares[t] * prices[t] for t in shares if t in prices)
        daily_return = index_value / prev_value - 1
        print(f"\nDaily return: {daily_return*100:+.3f}%")
        print(f"Index value: {prev_value:,.2f} -> {index_value:,.2f}")

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
        'constituent_count': len(TICKERS),
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
