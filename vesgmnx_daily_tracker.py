"""
VESGMNX Daily Index Tracker  —  V1.0
Verde ESG Market Neutral Index — Non-Investable Research Index

Long the best-scored S&P 500 companies, short the worst-scored, sector neutral
and beta neutral. Built on the same Verde ESG Scores as VESGLIDX.

This script is separate from vesglidx_daily_tracker.py and never touches the
VESGLIDX files. It borrows that tracker's price download and GitHub publish
helpers so both indices use the same price source.

MECHANICS (methodology VESGMNX-M-V1):
    * Scores: vesgmnx_scores.csv (ticker, name, sector, verde_esg_score,
      scores_as_of). Replace this file before each quarterly reconstitution.
    * Selection: within each GICS sector, rank by Verde ESG Score (ties share
      the average rank). Long = sector percentile >= 0.80, short = <= 0.20.
      Sectors with fewer than 5 scored names are left out.
    * Weights: each sector carries its share of scored names in BOTH legs;
      names are equal-weighted inside a sector leg. Each leg sums to 100%.
    * Beta neutrality: betas are measured against SPY over the trailing 252
      trading days. Hedge ratio h = beta(long leg) / beta(short leg), limited
      to 0.50-2.00. The short leg is held at h dollars per 1 dollar long.
    * Level: between reconstitutions both legs are buy-and-hold.
          index_t = index_reb * (1 + (L_t - 1) - h * (S_t - 1))
      where L_t and S_t are the leg values relative to the reconstitution close.
      Gross of trading costs, financing and stock borrow, as VESGLIDX is.
    * Inception: 1,000.00 at the October 8, 2026 close. The date is pinned, so
      a later run still strikes that close.
    * Reconstitution: first trading day of Jan/Apr/Jul/Oct, at the close.

USAGE:
    python vesgmnx_daily_tracker.py        (run from the repo folder)
"""

import os
import math
from datetime import datetime, date, timedelta, timezone

import numpy as np
import pandas as pd

import vesglidx_daily_tracker as base   # shared: price panel, JSON helpers, publish

# ── CONFIG ────────────────────────────────────────────────────────────────
SCORES_FILE    = 'vesgmnx_scores.csv'
PORTFOLIO_FILE = 'vesgmnx_portfolio.json'
HISTORY_FILE   = 'vesgmnx_history.json'
LATEST_FILE    = 'vesgmnx_latest.json'

INDEX_NAME     = 'Verde ESG Market Neutral Index'
TICKER         = 'VESGMNX'
METHODOLOGY    = 'VESGMNX-M-V1'
BASE_VALUE     = 1000.00
INCEPTION_DATE = '2026-10-08'
REBAL_MONTHS   = {1, 4, 7, 10}

MARKET         = 'SPY'          # beta reference
TOP_PCT        = 0.80           # long: sector percentile at or above
BOTTOM_PCT     = 0.20           # short: sector percentile at or below
MIN_SECTOR     = 5              # fewest scored names for a sector to take part
BETA_WINDOW    = 252            # trading days of returns used for beta
BETA_MIN_OBS   = 120            # fewer observations -> beta assumed 1.0
BETA_CLIP      = (0.30, 2.50)
HEDGE_CLIP     = (0.50, 2.00)
MIN_COVERAGE   = 0.95           # share of names that must have a close to record a day
INCEPTION_GRACE_DAYS = 14       # refuse to strike a "new" inception after this


# ── CLOCK ─────────────────────────────────────────────────────────────────
def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def bar_is_final(d):
    """A day's close is trusted once the US market has been shut for an hour
    (21:00 UTC), so a run during the session never records a part-day price."""
    now = utcnow()
    return d < now.strftime('%Y-%m-%d') or (d == now.strftime('%Y-%m-%d') and now.hour >= 21)


# ── PRICES ────────────────────────────────────────────────────────────────
def fetch_panel(tickers, period):
    """Daily closes, index 'YYYY-MM-DD', columns = tickers. Downloaded in
    batches through the VESGLIDX tracker's helper (Yahoo, adjusted closes)."""
    tickers = sorted(set(tickers))
    parts = []
    for i in range(0, len(tickers), 100):
        part = base.fetch_price_panel(tickers[i:i + 100], period=period)
        if part is not None and not part.empty:
            parts.append(part)
    if not parts:
        return pd.DataFrame()
    panel = pd.concat(parts, axis=1)
    panel = panel.loc[:, ~panel.columns.duplicated()]
    return panel.sort_index()


# ── CONSTRUCTION ──────────────────────────────────────────────────────────
def load_scores():
    df = pd.read_csv(SCORES_FILE)
    df['ticker'] = df['ticker'].astype(str).str.strip().str.upper()
    df = df.dropna(subset=['ticker', 'sector', 'verde_esg_score']).drop_duplicates('ticker')
    return df.set_index('ticker')


def select_legs(scores):
    """Return (long_tickers, short_tickers, sector_counts) from the full scored set."""
    n = scores.groupby('sector')['verde_esg_score'].transform('size')
    df = scores[n >= MIN_SECTOR].copy()
    df['pct'] = df.groupby('sector')['verde_esg_score'].rank(pct=True, method='average')
    longs = df.index[df['pct'] >= TOP_PCT].tolist()
    shorts = df.index[df['pct'] <= BOTTOM_PCT].tolist()
    return longs, shorts, df.groupby('sector').size().to_dict()


def leg_weights(scores, longs, shorts, sector_counts):
    """Sector-neutral weights. A sector takes part only if it has at least one
    name in each leg; its weight in both legs is its share of scored names."""
    sec = scores['sector']
    used = [s for s in sector_counts
            if any(sec[t] == s for t in longs) and any(sec[t] == s for t in shorts)]
    total = sum(sector_counts[s] for s in used)
    wl, ws = {}, {}
    for s in used:
        L = [t for t in longs if sec[t] == s]
        S = [t for t in shorts if sec[t] == s]
        for t in L:
            wl[t] = sector_counts[s] / total / len(L)
        for t in S:
            ws[t] = sector_counts[s] / total / len(S)
    return wl, ws


def trailing_betas(panel, asof, tickers):
    """Beta to SPY over the trailing BETA_WINDOW returns ending at `asof`."""
    hist = panel.loc[:asof]
    rets = hist.pct_change().iloc[1:].iloc[-BETA_WINDOW:]
    out = {}
    if MARKET not in rets.columns:
        return {t: 1.0 for t in tickers}
    m = rets[MARKET]
    for t in tickers:
        b = 1.0
        if t in rets.columns:
            pair = pd.concat([rets[t], m], axis=1).dropna()
            if len(pair) >= BETA_MIN_OBS and pair.iloc[:, 1].var() > 0:
                b = float(pair.iloc[:, 0].cov(pair.iloc[:, 1]) / pair.iloc[:, 1].var())
                b = float(np.clip(b, *BETA_CLIP))
        out[t] = b
    return out


def build_portfolio(scores, panel, d, index_value):
    """Strike a new portfolio at the close of date d (a row of `panel`)."""
    longs, shorts, sector_counts = select_legs(scores)
    row = panel.ffill().loc[d]
    priced = {t for t in longs + shorts if t in row.index and pd.notna(row[t]) and row[t] > 0}
    excluded = sorted(set(longs + shorts) - priced)
    longs = [t for t in longs if t in priced]
    shorts = [t for t in shorts if t in priced]
    wl, ws = leg_weights(scores, longs, shorts, sector_counts)
    betas = trailing_betas(panel, d, list(wl) + list(ws))
    beta_long = sum(wl[t] * betas[t] for t in wl)
    beta_short = sum(ws[t] * betas[t] for t in ws)
    hedge = float(np.clip(beta_long / beta_short, *HEDGE_CLIP)) if beta_short > 0 else 1.0

    def leg(w):
        return {t: {'weight': round(w[t], 8), 'price': float(row[t]),
                    'beta': round(betas[t], 4), 'sector': scores.at[t, 'sector'],
                    'score': float(scores.at[t, 'verde_esg_score'])} for t in w}

    as_of = str(scores['scores_as_of'].iloc[0]) if 'scores_as_of' in scores.columns else None
    return {
        'rebal_date': d,
        'index_value_at_rebal': round(index_value, 6),
        'hedge_ratio': round(hedge, 6),
        'beta_long': round(beta_long, 4),
        'beta_short': round(beta_short, 4),
        'scores_as_of': as_of,
        'excluded_no_price': excluded,
        'long': leg(wl),
        'short': leg(ws),
        'last_prices': {t: float(row[t]) for t in list(wl) + list(ws)},
    }


def leg_value(leg, prices):
    """Value of a buy-and-hold leg relative to its reconstitution close (1.0)."""
    return sum(v['weight'] * prices[t] / v['price'] for t, v in leg.items())


def index_level(pf, prices):
    L = leg_value(pf['long'], prices)
    S = leg_value(pf['short'], prices)
    value = pf['index_value_at_rebal'] * (1 + (L - 1) - pf['hedge_ratio'] * (S - 1))
    return value, L, S


def held(pf):
    return sorted(set(pf['long']) | set(pf['short']))


def is_rebalancing_day(d, history, pf):
    md = datetime.strptime(d, '%Y-%m-%d').date()
    if md.month not in REBAL_MONTHS:
        return False
    if pf['rebal_date'][:7] == d[:7]:
        return False                                   # already struck this month
    last = datetime.strptime(history['daily'][-1]['date'], '%Y-%m-%d').date()
    return last.month != md.month                      # first trading day of the month


# ── RUN ───────────────────────────────────────────────────────────────────
def strike_inception(history, today):
    if today > datetime.strptime(INCEPTION_DATE, '%Y-%m-%d').date() + timedelta(days=INCEPTION_GRACE_DAYS):
        print(f"ERROR: no {HISTORY_FILE} in {os.getcwd()}, but {TICKER} should have been "
              f"live since {INCEPTION_DATE}. Refusing to start a new index. Nothing written.")
        return None
    scores = load_scores()
    longs, shorts, _ = select_legs(scores)
    print(f"Inception: {len(longs)} long and {len(shorts)} short candidates; fetching price history...")
    panel = fetch_panel(longs + shorts + [MARKET], period='2y')
    if panel.empty or INCEPTION_DATE not in panel.index or not bar_is_final(INCEPTION_DATE):
        print(f"The {INCEPTION_DATE} close is not available yet. Nothing written; the next run will strike it.")
        return None
    row = panel.loc[INCEPTION_DATE]
    n = int(row[[t for t in longs + shorts if t in row.index]].notna().sum())
    if n < MIN_COVERAGE * len(longs + shorts):
        print(f"Only {n}/{len(longs + shorts)} closes for {INCEPTION_DATE} so far. Nothing written; "
              "the next run will strike it.")
        return None
    pf = build_portfolio(scores, panel, INCEPTION_DATE, BASE_VALUE)
    history['daily'] = [{'date': INCEPTION_DATE, 'index_value': BASE_VALUE, 'daily_return_pct': 0.0,
                         'long_leg': 1.0, 'short_leg': 1.0, 'rebalanced': True}]
    print(f"INCEPTION — {TICKER} set to {BASE_VALUE:,.2f} at the {INCEPTION_DATE} close: "
          f"{len(pf['long'])} long, {len(pf['short'])} short, hedge ratio {pf['hedge_ratio']:.3f}")
    if pf['excluded_no_price']:
        print(f"  left out (no price): {', '.join(pf['excluded_no_price'])}")
    return pf


def catch_up(history, pf, today):
    """Record every complete trading day missing since the last reconstitution."""
    names = held(pf)
    panel = fetch_panel(names, period=base.CATCHUP_PERIOD)
    if panel.empty:
        print("ERROR: no price data returned. Nothing written.")
        return False
    today_str = today.strftime('%Y-%m-%d')
    have = {r['date'] for r in history['daily']}
    last = history['daily'][-1]['date']
    todo = []
    for d in panel.index:
        if d <= pf['rebal_date'] or d in have or d > today_str or not bar_is_final(d):
            continue
        n = int(panel.loc[d].reindex(names).notna().sum())
        if n >= MIN_COVERAGE * len(names):
            todo.append(d)
        else:
            print(f"  {d}: only {n}/{len(names)} closes available yet — skipped, the next run will pick it up")
    if not todo:
        print("No new complete trading days to record.")
        return False

    wrote = False
    for d in sorted(todo):
        if d <= pf['rebal_date']:
            continue                                   # a reconstitution just moved past it
        row = panel.loc[d]
        prices = dict(pf['last_prices'])               # carry the last known close for stragglers
        prices.update({t: float(row[t]) for t in names if t in row.index and pd.notna(row[t])})
        value, L, S = index_level(pf, prices)
        rebalanced = False
        if d > last and is_rebalancing_day(d, history, pf):
            print(f"\n>>> QUARTERLY RECONSTITUTION at the {d} close <<<")
            scores = load_scores()
            if 'scores_as_of' in scores.columns and str(scores['scores_as_of'].iloc[0]) == pf.get('scores_as_of'):
                print("  WARNING: vesgmnx_scores.csv has not been updated since the last "
                      "reconstitution; carrying the prior scores forward.")
            longs, shorts, _ = select_legs(scores)
            big = fetch_panel(longs + shorts + [MARKET], period='2y')
            cand = [t for t in longs + shorts if t in big.columns]
            if big.empty or d not in big.index or \
                    int(big.loc[d].reindex(cand).notna().sum()) < MIN_COVERAGE * len(longs + shorts):
                print(f"  prices for the new constituents are incomplete for {d}; "
                      "nothing recorded for this day, the next run will retry.")
                break
            new_pf = build_portfolio(scores, big, d, value)
            pf.clear()
            pf.update(new_pf)
            names = held(pf)
            rebalanced = True
            print(f"  {len(pf['long'])} long, {len(pf['short'])} short, hedge ratio {pf['hedge_ratio']:.3f}")
        else:
            pf['last_prices'] = prices if d >= last else pf['last_prices']
        print(f"  {d}: {value:,.4f}{'  (RECONSTITUTED)' if rebalanced else ''}")
        history['daily'].append({'date': d, 'index_value': round(value, 4), 'daily_return_pct': 0.0,
                                 'long_leg': round(L, 6), 'short_leg': round(S, 6), 'rebalanced': rebalanced})
        history['daily'].sort(key=lambda r: r['date'])
        last = max(last, d)
        wrote = True
        if rebalanced:
            break                                      # later days are valued on the next run

    daily = history['daily']
    for i, r in enumerate(daily):
        r['daily_return_pct'] = 0.0 if i == 0 else round(
            (r['index_value'] / daily[i - 1]['index_value'] - 1) * 100, 4)
    return wrote


def publish(history, pf):
    rec = history['daily'][-1]
    value = rec['index_value']
    year = int(rec['date'][:4])
    prior = [d for d in history['daily'] if int(d['date'][:4]) < year]
    ytd_base = max(prior, key=lambda d: d['date'])['index_value'] if prior else BASE_VALUE
    latest = {
        'index_name': INDEX_NAME,
        'ticker': TICKER,
        'as_of_date': rec['date'],
        'index_value': round(value, 2),
        'daily_change_pct': round(rec['daily_return_pct'], 3),
        'ytd_return_pct': round((value / ytd_base - 1) * 100, 2),
        'since_inception_return_pct': round((value / BASE_VALUE - 1) * 100, 2),
        'base_value': BASE_VALUE,
        'inception_date': INCEPTION_DATE,
        'long_count': len(pf['long']),
        'short_count': len(pf['short']),
        'hedge_ratio': pf['hedge_ratio'],
        'beta_long': pf['beta_long'],
        'beta_short': pf['beta_short'],
        'long_leg_return_since_rebalance_pct': round((rec['long_leg'] - 1) * 100, 2),
        'short_leg_return_since_rebalance_pct': round((rec['short_leg'] - 1) * 100, 2),
        'last_rebalance_date': pf['rebal_date'],
        'scores_as_of': pf.get('scores_as_of'),
        'methodology': METHODOLOGY,
        'updated_at': datetime.now().isoformat(),
    }
    base.save_json(HISTORY_FILE, history)
    base.save_json(PORTFOLIO_FILE, pf)
    base.save_json(LATEST_FILE, latest)
    print("\n" + "=" * 52)
    print(f"{TICKER}: {value:,.2f}  ({rec['daily_return_pct']:+.3f}% on {rec['date']})")
    print(f"Since inception: {latest['since_inception_return_pct']:+.2f}%")
    print("=" * 52)
    print("\nPublishing to GitHub...")
    base.PUSH_FILES = [LATEST_FILE, HISTORY_FILE, PORTFOLIO_FILE]
    base.push_to_github()


def main():
    today = utcnow().date()
    print(f"{TICKER} Daily Tracker (V1.0) — {today}")
    print("=" * 52)
    history = base.load_json(HISTORY_FILE, {'daily': [], 'metadata': {
        'index_name': INDEX_NAME, 'ticker': TICKER, 'base_value': BASE_VALUE,
        'inception_date': INCEPTION_DATE, 'methodology': METHODOLOGY}})
    pf = base.load_json(PORTFOLIO_FILE, {})

    if today < datetime.strptime(INCEPTION_DATE, '%Y-%m-%d').date():
        print(f"{TICKER} starts at the {INCEPTION_DATE} close. Nothing to do yet.")
        return
    if not history['daily'] or not pf:
        pf = strike_inception(history, today)
        if pf is None:
            return
        publish(history, pf)
        return
    if not catch_up(history, pf, today):
        print("Nothing written, nothing pushed.")
        return
    publish(history, pf)


if __name__ == '__main__':
    main()
