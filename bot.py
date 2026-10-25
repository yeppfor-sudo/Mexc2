import os, ccxt, pandas as pd, time, requests, json
from datetime import datetime, timedelta

TOKEN=os.getenv("TELEGRAM_TOKEN")
CHAT=os.getenv("CHAT_ID")
FILE="alerted.json"

def send(msg):
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id":CHAT,"text":msg}, timeout=10)
    except: pass

def load():
    try:
        if os.path.exists(FILE):
            with open(FILE,'r') as f: return json.load(f)
    except: pass
    return {}
def save(d):
    with open(FILE,'w') as f: json.dump(d,f)

exchange=ccxt.mexc({'enableRateLimit':True})
exchange.load_markets()

EXCLUDE = ['STOCK','JPY','EUR','GBP','CHF','AUD','CAD','NZD','HKD','SGD']
syms=[s for s in exchange.markets if s.endswith('/USDT:USDT') and not any(x in s for x in EXCLUDE) and s.split('/')[0] not in EXCLUDE]

alerted=load()
now=datetime.utcnow()
alerted={k:v for k,v in alerted.items() if datetime.fromisoformat(v) > now - timedelta(days=2)}

total=0

# === 1. CHOCH + RETEST FOR ALL TIMEFRAMES (your WET logic) ===
TF_CONFIG = {
    '5m': 10, '15m': 10, '1h': 15, '4h': 20, '1d': 20, '1W': 10
}

for TF, max_alert in TF_CONFIG.items():
    print(f"\n--- CHOCH TF: {TF} ---")
    cnt=0
    for sym in syms:
        try:
            key=f"CHOCH_{sym}_{TF}"
            if key in alerted: continue

            ohlcv=exchange.fetch_ohlcv(sym, TF, limit=100)
            if len(ohlcv)<80: continue
            df=pd.DataFrame(ohlcv, columns=['t','o','h','l','c','v']).iloc[:-1]
            close, open_ = df['c'], df['o']
            c=close.iloc[-1]

            ema6=close.ewm(span=6).mean()
            ema12=close.ewm(span=12).mean()
            ema20=close.ewm(span=20).mean()
            e6,e12,e20=ema6.iloc[-1],ema12.iloc[-1],ema20.iloc[-1]
            pe6,pe12,pe20=ema6.iloc[-2],ema12.iloc[-2],ema20.iloc[-2]

            if (ema6.iloc[-35:-5] < ema12.iloc[-35:-5]).mean() <= 0.7: continue
            last_LH=df.iloc[-60:-10]['h'].max()
            broke=any(close.iloc[i] > last_LH for i in range(-10, -1))
            if not broke: continue

            retest_dist=(c - last_LH)/last_LH*100
            max_ret = 8 if TF=='1W' else 5
            if retest_dist < -3 or retest_dist > max_ret: continue
            if not (e6>e12>e20 and not (pe6>pe12>pe20)): continue
            if not (close.iloc[-1] > open_.iloc[-1]): continue

            coin=sym.replace('/USDT:USDT','')
            emoji="⚡" if TF=='5m' else "↗️" if TF=='4h' else "💎"
            msg=(
                f"{emoji} CHOCH + RETEST [{TF}]\n"
                f"Coin:{coin}\n"
                f"Price:{c:.6f}\n"
                f"CHOCH Level:{last_LH:.6f}\n"
                f"Retest:{retest_dist:+.1f}%\n"
                f"Buy Zone!"
            )
            send(msg); time.sleep(0.4)
            alerted[key]=now.isoformat()
            cnt+=1; total+=1
            if cnt>=max_alert: break
        except: continue
    print(f"Done {TF}: {cnt}")

# === 2. NEW: 5m PUMP + DOWNTREND PATTERN (your screenshot) ===
print(f"\n--- 5m PUMP + DOWNTREND (like screenshot) ---")
cnt2=0
for sym in syms[:250]:  # check top 250 for 5m to avoid ban
    try:
        key=f"PUMPBLEED_{sym}_5m"
        if key in alerted: continue
        
        ohlcv=exchange.fetch_ohlcv(sym, '5m', limit=400)
        if len(ohlcv)<200: continue
        df=pd.DataFrame(ohlcv, columns=['t','o','h','l','c','v'])
        
        df['gain_1'] = (df['c'] - df['o'])/df['o']*100
        df['gain_3'] = (df['c'] - df['o'].shift(2))/df['o'].shift(2)*100
        
        big_pump_idx = df[(df['gain_1'] > 35) | (df['gain_3'] > 60)].index
        if len(big_pump_idx)==0: continue
        last_pump_idx = big_pump_idx[-1]
        if len(df) - last_pump_idx < 30: continue  # pump too recent
        
        pump_high = df.loc[last_pump_idx, 'h']
        current = df.iloc[-1]['c']
        drop = (current - pump_high)/pump_high*100
        
        if drop > -20: continue  # not dropped enough
        
        # downtrend check - like your screenshot lower highs
        recent_high = df.iloc[-60:]['h'].max()
        prev_high = df.iloc[-120:-60]['h'].max()
        if recent_high >= prev_high: continue
        
        # bottom squeeze - ready for second pump
        ema20 = df['c'].ewm(span=20).mean().iloc[-1]
        near_bottom = abs(current - ema20)/ema20*100 < 4
        
        coin=sym.replace('/USDT:USDT','')
        msg=(
            f"📉 PUMP then BLEED [5m]\n"
            f"Coin:{coin}\n"
            f"Current:{current:.6f}\n"
            f"Top was:{pump_high:.6f}\n"
            f"Drop:{drop:.0f}% from top\n"
            f"{'✅ READY for next CHOCH!' if near_bottom else '⏳ Still bleeding'}\n"
            f"Same as your screenshot!"
        )
        send(msg); time.sleep(0.4)
        alerted[key]=now.isoformat()
        cnt2+=1; total+=1
        if cnt2>=15: break
        
    except: continue

print(f"Done PUMP BLEED: {cnt2}")

save(alerted)
print(f"\nTOTAL ALERTS: {total}")
