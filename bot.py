import os, ccxt, pandas as pd, requests, json, time, pytz
from datetime import datetime
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT = os.getenv("CHAT_ID")
PKT = pytz.timezone('Asia/Karachi')

HIST_FILE = "alerts_history.json"
TIMEFRAMES = ['4h','8h','12h','1d','3d','5d','7d']

def send_msg(text):
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                  json={"chat_id": CHAT, "text": text, "parse_mode": "Markdown"}, timeout=20)

def send_photo(path, caption):
    with open(path, 'rb') as f:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
                      data={"chat_id": CHAT, "caption": caption, "parse_mode": "Markdown"},
                      files={"photo": f}, timeout=30)

def load_hist():
    try:
        with open(HIST_FILE,'r') as f: return json.load(f)
    except: return {}
def save_hist(d):
    with open(HIST_FILE,'w') as f: json.dump(d,f)

exchange = ccxt.mexc({'enableRateLimit': True})
exchange.load_markets()

def fetch_tf(symbol, tf):
    # Fetch and resample for 3d/5d/7d
    if tf in ['4h','8h','12h']:
        ohlcv = exchange.fetch_ohlcv(symbol, '1h', limit=500)
        df = pd.DataFrame(ohlcv, columns=['t','o','h','l','c','v'])
        df['time'] = pd.to_datetime(df['t'], unit='ms', utc=True)
        df.set_index('time', inplace=True)
        df_r = df.resample(tf).agg({'o':'first','h':'max','l':'min','c':'last','v':'sum'}).dropna().reset_index()
        return df_r
    elif tf == '1d':
        ohlcv = exchange.fetch_ohlcv(symbol, '1d', limit=200)
        df = pd.DataFrame(ohlcv, columns=['t','o','h','l','c','v'])
        df['time'] = pd.to_datetime(df['t'], unit='ms', utc=True)
        return df
    else:
        ohlcv = exchange.fetch_ohlcv(symbol, '1d', limit=300)
        df = pd.DataFrame(ohlcv, columns=['t','o','h','l','c','v'])
        df['time'] = pd.to_datetime(df['t'], unit='ms', utc=True)
        df.set_index('time', inplace=True)
        rule = {'3d':'3D','5d':'5D','7d':'7D'}[tf]
        df_r = df.resample(rule).agg({'o':'first','h':'max','l':'min','c':'last','v':'sum'}).dropna().reset_index()
        return df_r

def check_setup(df):
    if len(df) < 60: return None
    df['ema20'] = df['c'].ewm(span=20).mean()
    df['ema50'] = df['c'].ewm(span=50).mean()
    df['ema100'] = df['c'].ewm(span=100).mean()
    df['vol_avg'] = df['v'].rolling(20).mean()
    
    last = df.iloc[-1] # latest closed candle
    prev = df.iloc[-2]
    
    # 1. Downtrend - price below ema50, ema20 slope down
    if not (last['ema20'] < last['ema50'] < last['ema100']): return None
    if df['ema20'].iloc[-1] > df['ema20'].iloc[-10]: return None
    
    # 2. Holds support - low near 30-bar lowest
    lowest_low = df.iloc[-30:-1]['l'].min()
    if last['l'] > lowest_low * 1.02: return None # must be near support
    if last['l'] < lowest_low * 0.97: return None # not breaking hard
    
    # 3. Bounce with volume
    if last['v'] < last['vol_avg'] * 1.2: return None
    
    # 4. Rejected in HTF EMA - wick touches ema20/50
    # Like your screenshot, high touches yellow/purple line
    ema_touch = last['ema20']
    if not (last['h'] >= ema_touch * 0.998 and last['h'] <= ema_touch * 1.03): 
        # also check ema50 as HTF
        if not (last['h'] >= last['ema50'] * 0.998 and last['h'] <= last['ema50'] * 1.03):
            return None
    
    # 5. After rejected close near open - small body, long upper wick
    body = abs(last['c'] - last['o'])
    range_c = last['h'] - last['l']
    if range_c == 0: return None
    if body / range_c > 0.4: return None # body must be small <40% of range
    upper_wick = last['h'] - max(last['o'], last['c'])
    if upper_wick < body * 1.2: return None # wick must be long
    
    # 6. Candle must closed green
    if not (last['c'] > last['o']): return None
    
    # All conditions passed
    entry = float(last['c'])
    sl = float(last['l'] * 0.985) # below support wick
    risk = entry - sl
    if risk <=0: return None
    tp1 = entry + risk * 2
    tp2 = entry + risk * 3
    rr = 2.0
    
    return {'entry': entry, 'sl': sl, 'tp1': tp1, 'tp2': tp2, 'rr': rr,
            'support': lowest_low, 'ema': float(ema_touch), 'vol': float(last['v'])}

def draw_chart(df, symbol, tf, setup):
    fig, ax = plt.subplots(figsize=(12,6), facecolor='black')
    ax.set_facecolor('black')
    view = df.tail(60)
    x = range(len(view))
    ax.plot(x, view['ema20'], color='violet', lw=1.2, label='EMA20')
    ax.plot(x, view['ema50'], color='orange', lw=1.2, label='EMA50')
    ax.plot(x, view['ema100'], color='blue', lw=1.2, label='EMA100 HTF')
    # candles
    for i, (_, row) in enumerate(view.iterrows()):
        col = '#00ff88' if row['c']>=row['o'] else '#ff4444'
        ax.plot([i,i], [row['l'], row['h']], color=col, lw=1)
        ax.plot([i,i], [row['o'], row['c']], color=col, lw=4)
    # support
    ax.axhline(setup['support'], color='cyan', ls='--', lw=1, label=f"Support {setup['support']:.5f}")
    # last candle highlight with green arrow like your screenshot
    ax.annotate('', xy=(len(view)-1, view.iloc[-1]['h']), xytext=(len(view)-1+3, view.iloc[-1]['h']+ (view['h'].max()-view['l'].min())*0.1),
                arrowprops=dict(facecolor='lime', shrink=0.05, width=8, headwidth=20, color='lime'))
    # Entry/SL/TP
    ax.axhline(setup['entry'], color='white', lw=1)
    ax.axhline(setup['sl'], color='red', lw=1, label=f"SL {setup['sl']:.5f}")
    ax.axhline(setup['tp1'], color='lime', ls='--', lw=1, label=f"TP1 {setup['tp1']:.5f} RR 1:2")
    ax.text(len(view)-1, setup['entry'], f" ENTRY {setup['entry']:.5f}", color='white', fontsize=8)
    ax.set_title(f"{symbol} {tf} | Downtrend + Support Hold + Vol Bounce + EMA Rejection + Green Close | PKT {datetime.now(PKT).strftime('%Y-%m-%d %H:%M')}", color='white', fontsize=11)
    ax.legend(facecolor='black', labelcolor='white')
    ax.grid(alpha=0.15)
    plt.tight_layout()
    path = f"chart_{symbol.replace('/','')}_{tf}.png"
    plt.savefig(path, dpi=150, facecolor='black')
    plt.close()
    return path

# SCAN
hist = load_hist()
symbols = [s for s in exchange.markets if s.endswith('/USDT:USDT')]
try:
    tickers = exchange.fetch_tickers()
    symbols = [s for s in symbols if tickers.get(s,{}).get('quoteVolume',0) > 250_000]
except: pass
symbols = symbols[:150]

found = 0
now_pkt = datetime.now(PKT)
print(f"Scanning {len(symbols)} perps PKT {now_pkt}")

for sym in symbols:
    for tf in TIMEFRAMES:
        try:
            key = f"{sym}_{tf}"
            if time.time() - hist.get(key,0) < 24*3600: continue
            
            df = fetch_tf(sym, tf)
            setup = check_setup(df)
            if not setup: continue
            
            chart_path = draw_chart(df, sym, tf, setup)
            caption = f"🟢 *NEW SETUP - EMA REJECTION BOUNCE*\n\n*Coin:* {sym}\n*TF:* {tf}\n*Conditions:*\n1. Downtrend ✓\n2. Holds Support {setup['support']:.5f} ✓\n3. Volume Bounce {setup['vol']:.0f} ✓\n4. Rejected at EMA {setup['ema']:.5f} ✓\n5. Close Near Open (Small Body) ✓\n6. Green Close ✓\n\n*Entry:* `{setup['entry']:.6f}`\n*SL:* `{setup['sl']:.6f}`\n*TP1:* `{setup['tp1']:.6f}` RR 1:2\n*TP2:* `{setup['tp2']:.6f}` RR 1:3\n*R:R:* {setup['rr']}\n*Time PKT:* {now_pkt.strftime('%I:%M %p')}"
            send_photo(chart_path, caption)
            hist[key]=time.time()
            save_hist(hist)
            found+=1
            time.sleep(1)
        except Exception as e:
            print(f"{sym} {tf} error {e}")
            continue

if found==0:
    send_msg("*please be patient*")
print(f"Done found {found}")
