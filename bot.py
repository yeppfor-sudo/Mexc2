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
    
    last = df.iloc[-1]
    
    # 1. Downtrend
    if not (last['ema20'] < last['ema50'] < last['ema100']): return None
    if df['ema20'].iloc[-1] > df['ema20'].iloc[-10]: return None
    
    # 2. Holds support - FIXED 5% not 2%
    lowest_low = df.iloc[-30:-1]['l'].min()
    if last['l'] > lowest_low * 1.05: return None
    if last['l'] < lowest_low * 0.97: return None
    
    # 3. Volume - FIXED 0.9 not 1.2 for low price coins
    if last['v'] < last['vol_avg'] * 0.9: return None
    
    # 4. EMA Rejection
    ema_touch = last['ema20']
    if not (last['h'] >= ema_touch * 0.998 and last['h'] <= ema_touch * 1.03): 
        if not (last['h'] >= last['ema50'] * 0.998 and last['h'] <= last['ema50'] * 1.03):
            return None
    
    # 5. Small body + long upper wick
    body = abs(last['c'] - last['o'])
    range_c = last['h'] - last['l']
    if range_c == 0: return None
    if body / range_c > 0.4: return None
    upper_wick = last['h'] - max(last['o'], last['c'])
    if upper_wick < body * 1.2: return None
    
    # 6. Green close
    if not (last['c'] > last['o']): return None
    
    entry = float(last['c'])
    sl = float(last['l'] * 0.985)
    risk = entry - sl
    if risk <=0: return None
    tp1 = entry + risk * 2
    tp2 = entry + risk * 3
    
    return {'entry': entry, 'sl': sl, 'tp1': tp1, 'tp2': tp2, 'rr': 2.0,
            'support': lowest_low, 'ema': float(ema_touch), 'vol': float(last['v'])}

def draw_chart(df, symbol, tf, setup):
    fig, ax = plt.subplots(figsize=(12,6), facecolor='black')
    ax.set_facecolor('black')
    view = df.tail(60)
    x = range(len(view))
    ax.plot(x, view['ema20'], color='violet', lw=1.2, label='EMA20')
    ax.plot(x, view['ema50'], color='orange', lw=1.2, label='EMA50')
    ax.plot(x, view['ema100'], color='blue', lw=1.2, label='EMA100')
    for i, (_, row) in enumerate(view.iterrows()):
        col = '#00ff88' if row['c']>=row['o'] else '#ff4444'
        ax.plot([i,i], [row['l'], row['h']], color=col, lw=1)
        ax.plot([i,i], [row['o'], row['c']], color=col, lw=4)
    ax.axhline(setup['support'], color='cyan', ls='--', lw=1)
    ax.annotate('', xy=(len(view)-1, view.iloc[-1]['h']), xytext=(len(view)-1+3, view.iloc[-1]['h']+ (view['h'].max()-view['l'].min())*0.1),
                arrowprops=dict(facecolor='lime', width=8, headwidth=20, color='lime'))
    ax.axhline(setup['entry'], color='white', lw=1)
    ax.axhline(setup['sl'], color='red', lw=1, label=f"SL {setup['sl']:.5f}")
    ax.axhline(setup['tp1'], color='lime', ls='--', lw=1, label=f"TP1 {setup['tp1']:.5f}")
    ax.set_title(f"{symbol} {tf} | PKT {datetime.now(PKT).strftime('%Y-%m-%d %H:%M')}", color='white', fontsize=11)
    ax.legend(facecolor='black', labelcolor='white')
    ax.grid(alpha=0.15)
    plt.tight_layout()
    path = f"chart_{symbol.replace('/','')}_{tf}.png"
    plt.savefig(path, dpi=150, facecolor='black')
    plt.close()
    return path

hist = load_hist()
symbols = [s for s in exchange.markets if s.endswith('/USDT:USDT')]
try:
    tickers = exchange.fetch_tickers()
    symbols = [s for s in symbols if tickers.get(s,{}).get('quoteVolume',0) > 250_000]
except: pass
symbols = symbols[:150]
found = 0
now_pkt = datetime.now(PKT)

for sym in symbols:
    for tf in TIMEFRAMES:
        try:
            key = f"{sym}_{tf}"
            if time.time() - hist.get(key,0) < 24*3600: continue
            df = fetch_tf(sym, tf)
            setup = check_setup(df)
            if not setup: continue
            chart_path = draw_chart(df, sym, tf, setup)
            caption = f"🟢 *NEW SETUP*\n*Coin:* {sym}\n*TF:* {tf}\n*Entry:* `{setup['entry']:.6f}`\n*SL:* `{setup['sl']:.6f}`\n*TP1:* `{setup['tp1']:.6f}`\n*TP2:* `{setup['tp2']:.6f}`\n*Time:* {now_pkt.strftime('%I:%M %p')}"
            send_photo(chart_path, caption)
            hist[key]=time.time()
            save_hist(hist)
            found+=1
            time.sleep(1)
        except Exception as e:
            print(f"{sym} {tf} {e}")
            continue

if found==0:
    send_msg("*please be patient*")
