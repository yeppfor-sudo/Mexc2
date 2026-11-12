# ===============================================================
#  MEXC PERPETUAL SCANNER - 4H  (GitHub Actions + Telegram)
#  Pattern: long downtrend -> small-candle base at the lows
#  (EMA 6/12/20 falling & squeezing) -> one big green candle
#  that closes above ALL three EMAs
# ===============================================================
import os, io, json, time, requests, numpy as np, pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

# ---------------- SETTINGS ----------------
BASE_URL        = "https://contract.mexc.com"
INTERVAL        = "Hour4"
TF_SECONDS      = 4 * 3600
CANDLES         = 260
QUOTE           = "USDT"

MAX_AGE         = 1            # 1 = trigger must be the LAST closed 4h candle
BASE_CANDLES    = 10
MAX_BASE_RANGE  = 18.0
DROP_LOOKBACK   = 80
MIN_DROP_PCT    = 20.0
MAX_BASE_POS    = 0.30
MIN_BODY_PCT    = 3.0
MIN_BODY_MULT   = 2.5
MIN_BODY_RATIO  = 0.50
MIN_SCORE       = 50
MAX_ALERTS      = 15           # max Telegram alerts per run
WORKERS         = 8

STATE_FILE      = "alerts_state.json"
STATE_KEEP_DAYS = 7

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT  = os.environ.get("TELEGRAM_CHAT_ID", "")

# ---------------- HELPERS ----------------
def ema(arr, n):
    return pd.Series(arr).ewm(span=n, adjust=False).mean().values

def get_symbols():
    r = requests.get(f"{BASE_URL}/api/v1/contract/detail", timeout=20).json()
    out = []
    for s in r.get("data", []):
        if s.get("quoteCoin") == QUOTE and s.get("state") == 0:
            out.append(s["symbol"])
    return sorted(out)

def get_klines(sym, tries=3):
    end = int(time.time())
    start = end - CANDLES * TF_SECONDS
    for t in range(tries):
        try:
            r = requests.get(
                f"{BASE_URL}/api/v1/contract/kline/{sym}",
                params={"interval": INTERVAL, "start": start, "end": end},
                timeout=15)
            d = r.json().get("data")
            if not d or not d.get("time"):
                return None
            df = pd.DataFrame({
                "t": d["time"], "o": d["open"], "h": d["high"],
                "l": d["low"], "c": d["close"], "v": d["vol"]}).astype(float)
            return df
        except Exception:
            time.sleep(0.6 * (t + 1))
    return None

def split_closed(df):
    now = time.time()
    return df[df["t"] + TF_SECONDS <= now].reset_index(drop=True)

# ---------------- PATTERN ----------------
def check(df, i):
    o = df["o"].values; h = df["h"].values
    l = df["l"].values; c = df["c"].values
    e6, e12, e20 = ema(c, 6), ema(c, 12), ema(c, 20)

    bs = i - BASE_CANDLES
    ws = bs - DROP_LOOKBACK
    if ws < 0:
        return None

    body = c[i] - o[i]
    if body <= 0:
        return None
    body_pct = body / o[i] * 100
    rng = h[i] - l[i]
    if rng <= 0 or body / rng < MIN_BODY_RATIO:
        return None
    avg_body = np.mean(np.abs(c[bs:i] - o[bs:i]))
    if avg_body <= 0:
        return None
    body_mult = body / avg_body
    if body_pct < MIN_BODY_PCT or body_mult < MIN_BODY_MULT:
        return None
    if c[i] <= max(e6[i], e12[i], e20[i]):
        return None
    if o[i] > e20[i - 1] * 1.01:
        return None

    base_hi = h[bs:i].max()
    base_lo = l[bs:i].min()
    base_rng = (base_hi - base_lo) / base_lo * 100
    if base_rng > MAX_BASE_RANGE:
        return None

    peak = h[ws:bs].max()
    win_low = min(l[ws:i].min(), base_lo)
    drop = (peak - base_lo) / peak * 100
    if drop < MIN_DROP_PCT:
        return None
    pos = (base_lo - win_low) / max(peak - win_low, 1e-12)
    if pos > MAX_BASE_POS:
        return None
    if not (e20[i - 1] < e20[bs - 20]):
        return None
    if c[i - 1] > e20[i - 1] * 1.02:
        return None

    squeeze = (max(e6[i-1], e12[i-1], e20[i-1]) - min(e6[i-1], e12[i-1], e20[i-1])) / c[i-1] * 100
    score = 0
    score += min(25, drop / 40 * 25)
    score += max(0, 20 - base_rng / MAX_BASE_RANGE * 20)
    score += min(25, body_mult / 6 * 25)
    score += max(0, 15 - squeeze * 3)
    score += 15 if c[i] > max(e6[i], e12[i], e20[i]) * 1.01 else 8
    score = round(score)

    return dict(i=i, bs=bs, drop=round(drop, 1), base_rng=round(base_rng, 1),
                body_pct=round(body_pct, 1), body_mult=round(body_mult, 1),
                squeeze=round(squeeze, 2), score=score,
                base_hi=base_hi, base_lo=base_lo)

def scan_symbol(sym):
    df = get_klines(sym)
    if df is None or len(df) < DROP_LOOKBACK + BASE_CANDLES + 25:
        return None
    closed = split_closed(df)
    n = len(closed)
    for k in range(MAX_AGE):
        i = n - 1 - k
        res = check(closed, i)
        if res:
            res["symbol"] = sym
            res["trigger_time"] = datetime.fromtimestamp(
                closed["t"].iloc[i], tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            res["age"] = k + 1
            res["trigger_close"] = float(closed["c"].iloc[i])
            res["_df"] = df          # full data incl. forming candle (for live chart)
            return res
    return None

# ---------------- CHART ----------------
def make_chart(res, bars=110):
    sym = res["symbol"]
    full = res["_df"]
    cf = full["c"].values
    e6a, e12a, e20a = ema(cf, 6), ema(cf, 12), ema(cf, 20)
    df = full.tail(bars).reset_index(drop=True)
    e6, e12, e20 = e6a[-bars:], e12a[-bars:], e20a[-bars:]

    fig, ax = plt.subplots(figsize=(11, 6))
    for x, row in df.iterrows():
        col = "#16a34a" if row["c"] >= row["o"] else "#ef4444"
        ax.vlines(x, row["l"], row["h"], color=col, lw=1)
        ax.add_patch(Rectangle((x - 0.32, min(row["o"], row["c"])), 0.64,
                               max(abs(row["c"] - row["o"]), row["c"] * 1e-4),
                               color=col))
    ax.plot(e6,  color="#e879f9", lw=1.3, label="EMA 6")
    ax.plot(e12, color="#f59e0b", lw=1.3, label="EMA 12")
    ax.plot(e20, color="#6366f1", lw=1.6, label="EMA 20")

    times = [datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d %H:%M") for t in df["t"]]
    tt = res["trigger_time"]
    if tt in times:
        ti = times.index(tt)
        ax.axvspan(ti - BASE_CANDLES - 0.5, ti - 0.5, color="#3b82f6", alpha=0.10)
        ax.annotate("TRIGGER", (ti, df["h"].iloc[ti]),
                    xytext=(ti - 6, df["h"].iloc[ti] * 1.03),
                    arrowprops=dict(arrowstyle="->"), fontsize=9, fontweight="bold")
    last = df["c"].iloc[-1]
    ax.axhline(last, color="gray", ls=":", lw=0.8)
    ax.text(len(df) - 1, last, f"  {last:g}", va="center", fontsize=9)
    ax.set_title(f"{sym} | 4H | Score {res['score']} | Drop {res['drop']}% | "
                 f"Body x{res['body_mult']} | Live {last:g}", fontsize=11)
    ax.grid(alpha=0.2); ax.legend(loc="upper left")
    ax.set_xticks(range(0, len(df), 15))
    ax.set_xticklabels([times[k][5:] for k in range(0, len(df), 15)], fontsize=8)
    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    buf.seek(0)
    return buf, float(last)

# ---------------- TELEGRAM ----------------
def send_photo(buf, caption):
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto",
        data={"chat_id": TG_CHAT, "caption": caption[:1000], "parse_mode": "HTML"},
        files={"photo": ("chart.png", buf, "image/png")}, timeout=40)
    if not r.ok:
        print("Telegram error:", r.status_code, r.text[:200])
    return r.ok

def send_text(text):
    requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                  data={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML"}, timeout=30)

# ---------------- STATE (dedupe) ----------------
def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(state):
    cutoff = time.time() - STATE_KEEP_DAYS * 86400
    state = {k: v for k, v in state.items() if v >= cutoff}
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=1)

# ---------------- MAIN ----------------
def main():
    if not TG_TOKEN or not TG_CHAT:
        print("Missing TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID secrets")
        return

    t0 = time.time()
    symbols = get_symbols()
    print(f"Scanning {len(symbols)} MEXC USDT perpetual pairs on 4H ...")

    results = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(scan_symbol, s): s for s in symbols}
        for f in as_completed(futs):
            try:
                r = f.result()
                if r and r["score"] >= MIN_SCORE:
                    results.append(r)
            except Exception as e:
                print("err", futs[f], e)

    results.sort(key=lambda x: x["score"], reverse=True)
    print(f"Done in {time.time() - t0:.0f}s | {len(results)} matches")

    state = load_state()
    sent = 0
    for r in results:
        key = f"{r['symbol']}|{r['trigger_time']}"
        if key in state:
            continue                       # already alerted for this candle
        if sent >= MAX_ALERTS:
            break
        buf, live = make_chart(r)
        move = (live / r["trigger_close"] - 1) * 100
        sym = r["symbol"]
        caption = (
            f"🟢 <b>{sym}</b> | 4H squeeze breakout\n"
            f"Score: <b>{r['score']}</b>\n"
            f"Trigger candle: {r['trigger_time']} UTC\n"
            f"Drop before base: {r['drop']}% | Base range: {r['base_rng']}%\n"
            f"Trigger body: {r['body_pct']}% (x{r['body_mult']} avg)\n"
            f"Trigger close: {r['trigger_close']:g} | Live: {live:g} ({move:+.1f}%)\n"
            f"https://futures.mexc.com/exchange/{sym}"
        )
        if send_photo(buf, caption):
            state[key] = time.time()
            sent += 1
            time.sleep(1.2)

    save_state(state)
    print(f"Alerts sent: {sent}")

if __name__ == "__main__":
    main()
