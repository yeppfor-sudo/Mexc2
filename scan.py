import os, json, time, io
from datetime import datetime, timezone
import requests
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import mplfinance as mpf

BASE = "https://contract.mexc.com/api/v1/contract"
TFS = ["4h", "8h", "1d", "2d", "3d"]
NATIVE = {"4h": "Hour4", "8h": "Hour8", "1d": "Day1"}
RESAMPLE = {"2d": ("1d", "2D"), "3d": ("1d", "3D")}

PUMP_MIN = 0.80
PULLBACK_MIN = 0.15
HOLD_FRAC = 0.30
BASE_MIN, BASE_MAX = 6, 25
RANGE_MAX = 0.28
EMA_SPREAD_MAX = 0.07
BO_MIN_GAIN = 0.04
BO_MAX_EXT = 1.50
LOOKBACK = 150
SLEEP = 0.12
MAX_ALERTS = 15
COOLDOWN_H = {"BASE": 72, "BREAKOUT": 24}
STATE_FILE = "sent.json"

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")

EXCLUDE = {"XAU", "XAG", "GOLD", "SILVER", "OIL", "BRENT", "WTI", "TSLA",
           "NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "COIN", "MSTR",
           "CRCL", "HOOD", "CRWV", "SPY", "QQQ", "SPX", "NDX", "PAXG"}

S = requests.Session()
RAW = {}


def get(url, params=None, tries=4):
    for i in range(tries):
        try:
            r = S.get(url, params=params, timeout=20)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(2 + i * 2)
        except Exception:
            time.sleep(1)
    return None


def raw_klines(sym, src):
    key = (sym, src)
    if key in RAW:
        return RAW[key]
    j = get(f"{BASE}/kline/{sym}", {"interval": NATIVE[src]})
    time.sleep(SLEEP)
    if not j or not j.get("success") or not j.get("data"):
        return None
    d = j["data"]
    df = pd.DataFrame({
        "time": pd.to_datetime(d["time"], unit="s"),
        "open": d["open"], "high": d["high"],
        "low": d["low"], "close": d["close"], "vol": d["vol"],
    }).set_index("time").astype(float)
    RAW[key] = df
    return df


def klines(sym, tf):
    if tf in NATIVE:
        df = raw_klines(sym, tf)
    else:
        src, rule = RESAMPLE[tf]
        df = raw_klines(sym, src)
        if df is None:
            return None
        df = df.resample(rule, origin="epoch").agg(
            {"open": "first", "high": "max", "low": "min",
             "close": "last", "vol": "sum"}).dropna()
    return None if df is None else df.tail(400)


def add_ema(df):
    df = df.copy()
    for n in (6, 12, 20):
        df[f"e{n}"] = df.close.ewm(span=n, adjust=False).mean()
    return df


def detect(df, mode):
    c = add_ema(df.iloc[:-1])
    if len(c) < 60:
        return None
    t = c.iloc[-1]
    for n in range(BASE_MAX, BASE_MIN - 1, -1):
        base_end = len(c) - 1 if mode == "BREAKOUT" else len(c)
        base_start = base_end - n
        if base_start < 25:
            continue
        base = c.iloc[base_start:base_end]
        pre = c.iloc[max(0, base_start - LOOKBACK):base_start]
        if len(pre) < 20:
            continue
        bh, bl = base.high.max(), base.low.min()
        if (bh - bl) / bl > RANGE_MAX:
            continue
        pk = int(np.argmax(pre.high.values))
        P = pre.high.values[pk]
        if (len(pre) - 1 - pk) < 3:
            continue
        L = pre.low.values[:pk + 1].min()
        if P / L - 1 < PUMP_MIN:
            continue
        if bh > P * (1 - PULLBACK_MIN):
            continue
        if bl < L + HOLD_FRAC * (P - L):
            continue
        lb = base.iloc[-1]
        es = [lb.e6, lb.e12, lb.e20]
        if (max(es) - min(es)) / lb.close > EMA_SPREAD_MAX:
            continue
        if mode == "BASE":
            if abs(t.close - t.e20) / t.close > 0.08:
                continue
        else:
            if not (t.close > t.open and t.close > bh
                    and t.close > max(t.e6, t.e12, t.e20)):
                continue
            if t.close / t.open - 1 < BO_MIN_GAIN:
                continue
            if t.close > bh * BO_MAX_EXT:
                continue
        return {
            "mode": mode, "pump": round((P / L - 1) * 100),
            "pullback": round((1 - bh / P) * 100),
            "base_n": n, "range": round((bh - bl) / bl * 100, 1),
            "base_high": float(bh), "base_low": float(bl),
            "close": float(t.close), "ctime": str(c.index[-1]),
        }
    return None


def make_chart(sym, tf, r):
    df = add_ema(klines(sym, tf)).tail(90)
    mc = mpf.make_marketcolors(up="#0ba66b", down="#f8445b",
                               edge="inherit", wick="inherit")
    sty = mpf.make_mpf_style(marketcolors=mc, base_mpl_style="default",
                             gridstyle="-", gridcolor="#eeeeee",
                             facecolor="white")
    ap = [mpf.make_addplot(df.e6, color="#d98cf0", width=1),
          mpf.make_addplot(df.e12, color="#ff9f1a", width=1),
          mpf.make_addplot(df.e20, color="#6a4cff", width=1)]
    buf = io.BytesIO()
    mpf.plot(df, type="candle", style=sty, addplot=ap,
             hlines=dict(hlines=[r["base_high"], r["base_low"]],
                         colors=["#1a73e8", "#1a73e8"], linestyle="--"),
             title=f"{sym} {tf} {r['mode']}", figsize=(9, 5),
             savefig=dict(fname=buf, dpi=110, format="png"))
    buf.seek(0)
    return buf


def send(sym, tf, r):
    icon = "🚀" if r["mode"] == "BREAKOUT" else "🟡"
    cap = (f"{icon} {sym} | {tf} | {r['mode']}\n"
           f"Pump: +{r['pump']}% | Pullback: -{r['pullback']}%\n"
           f"Base: {r['base_n']} candles, range {r['range']}%\n"
           f"Base high: {r['base_high']:.6g}\n"
           f"Base low: {r['base_low']:.6g}\n"
           f"Close: {r['close']:.6g}")
    if not TG_TOKEN or not TG_CHAT:
        print("No Telegram secrets set:\n", cap)
        return False
    try:
        img = make_chart(sym, tf, r)
        resp = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto",
            data={"chat_id": TG_CHAT, "caption": cap},
            files={"photo": ("chart.png", img, "image/png")}, timeout=60)
        if resp.status_code != 200:
            print("Telegram error", resp.text)
            return False
        return True
    except Exception as e:
        print("send error", sym, tf, e)
        return False


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    with open(STATE_FILE, "w") as f:
        json.dump(st, f, indent=1)


def main():
    state = load_state()
    now = datetime.now(timezone.utc)
    d = get(f"{BASE}/detail")
    syms = []
    for x in d["data"]:
        if x.get("quoteCoin") != "USDT" or x.get("state", 0) != 0:
            continue
        if x["symbol"].split("_")[0].upper() in EXCLUDE:
            continue
        syms.append(x["symbol"])
    print("Pairs:", len(syms))

    hits = []
    for sym in syms:
        for tf in TFS:
            try:
                df = klines(sym, tf)
                if df is None or len(df) < 70:
                    continue
                for mode in ("BREAKOUT", "BASE"):
                    r = detect(df, mode)
                    if r:
                        hits.append((sym, tf, r))
                        break
            except Exception as e:
                print("err", sym, tf, e)

    hits.sort(key=lambda h: (h[2]["mode"] != "BREAKOUT", -h[2]["pump"]))
    sent = 0
    for sym, tf, r in hits:
        if sent >= MAX_ALERTS:
            break
        key = f"{sym}|{tf}|{r['mode']}"
        old = state.get(key)
        if old:
            if old["ctime"] == r["ctime"]:
                continue
            age = (now - datetime.fromisoformat(old["sent"])).total_seconds() / 3600
            if age < COOLDOWN_H[r["mode"]]:
                continue
        if send(sym, tf, r):
            state[key] = {"ctime": r["ctime"], "sent": now.isoformat()}
            sent += 1
            time.sleep(1)

    cutoff = 14 * 24
    state = {k: v for k, v in state.items()
             if (now - datetime.fromisoformat(v["sent"])).total_seconds() / 3600 < cutoff}
    save_state(state)
    print(f"Hits: {len(hits)} | Alerts sent: {sent}")


if __name__ == "__main__":
    main()
