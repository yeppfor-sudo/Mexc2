import ccxt, pandas as pd, requests, os, io, json, time
import matplotlib.pyplot as plt
from datetime import datetime, timedelta

BOT_TOKEN=os.getenv("BOT_TOKEN"); CHAT_ID=os.getenv("CHAT_ID")
MEMORY_FILE="short_memory.json"

# ========= ULTRA FILTER - ONLY CRYPTO =========
BLOCK_STOCKS = {'TLT','TLH','IEF','SHY','INDA','EWJ','EWA','EWZ','KWEB','QQQ','SPY','DIA','IWM','EWW','EWC','MCHI','INDY','STRC','OURA','SPX','SPX500','NAS100','DJI','US30','UK100','GER40','RKLB','NVDA','AAPL','TSLA','MSFT','STOCK','ETF','OIL','SILVER','GOLD','XAU','XAG','XAUT','PAXG','KAU','TRY','EUR','GBP','JPY','AUD','CAD','CHF','NZD','USDC','BUSD','FDUSD','TUSD','DAI','USDE','USDP','GUSD','PYUSD','EURI','EURC','XLU','XLP','XLE','XLF','XLB','XLI','XLY','XLK','XLV','GLD','SLV','GDX','GDXJ','COPX','LIT','URA','SQQQ','TQQQ','SOXS','SOXL','SPXS','SPXL','SKDD','XSOXS','XSOX','LABU','LABD','TECL','TECS','UPRO','SPXU','TZA','TNA','FAS','FAZ','SDOW','UDOW','SOXL','SOXS','SPXS','SPXL'}

BLOCK_MEGA = {'BTC','ETH','SOL','BNB','TRX','XRP','ADA','DOGE','AVAX','DOT','LINK','LTC','BCH','XLM','ETC','HBAR','APT','NEAR','UNI','FIL','ARB','OP','ATOM','MATIC','POL','TON','SHIB','PEPE','CRO','LEO','OKB','BGB','SUI','LDO','RNDR','TAO','FET','WIF','ENA','JUP','SEI','TIA','ONDO','W','PYTH','STRK','JTO','ENS'}

BLOCK_COMMODITY = {'LEAD','COPPER','NICKEL','ZINC','TIN','ALUM','ALUMINIUM','ALUMINUM','CORN','SOY','WHEAT','COTTON','SUGAR','COFFEE','COCOA','GAS'}

BLOCK_ALL = BLOCK_STOCKS | BLOCK_MEGA | BLOCK_COMMODITY

def is_valid_crypto(sym):
    if not sym.endswith('USDT:USDT'): 
        return False
    base = sym.split('/')[0].upper()
    if any(x in base for x in ['STOCK','ETF','SOX','QQQ','SPX','DJI','SKDD','XSOX','TLT','SPY','DIA','TLT','GDX']):
        return False
    if any(x in base for x in ['XAU','PAXG','GOLD','XAG','LEAD','COPPER','NICKEL','ZINC','TIN','GLD','SLV','ALUM']):
        return False
    if base in BLOCK_ALL: 
        return False
    if len(base) < 2 or len(base) > 6: 
        return False
    if any(c.isdigit() for c in base): 
        return False
    if not base.isalpha(): 
        return False
    return True
# ========= END FILTER =========

def send_text(msg):
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={"chat_id":CHAT_ID,"text":msg,"parse_mode":"HTML"}, timeout=20)
        print(msg)
    except: pass

def send_photo(fig, cap):
    try:
        buf=io.BytesIO(); fig.savefig(buf,format='png',dpi=150,bbox_inches='tight')
        buf.seek(0); plt.close(fig)
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", data={"chat_id":CHAT_ID,"caption":cap,"parse_mode":"HTML"}, files={"photo":buf}, timeout=30)
    except Exception as e: print(f"Photo {e}")

def ema(s,n): return s.ewm(span=n, adjust=False).mean()

def check_gainer_pump(df_1d):
    if len(df_1d) < 30: return False, 0, ""
    df_1d['pct'] = df_1d['c'].pct_change()*100
    today = df_1d['pct'].iloc[-1]
    yest = df_1d['pct'].iloc[-2]
    is_gainer = (today >= 4) or (yest >= 6) or (today+yest >= 10)
    if not is_gainer: return False, 0, ""
    low_20 = df_1d['l'].iloc[-20:].min()
    high_3 = df_1d['h'].iloc[-3:].max()
    pump = (high_3/low_20-1)*100 if low_20>0 else 0
    if pump < 35: return False, 0, ""
    return True, pump, f"Gainer T:{today:.1f}% Y:{yest:.1f}% Pump {pump:.0f}%"

def check_tight_top(df_1h):
    if len(df_1h) < 20: return False, 0, ""
    last_6 = df_1h.iloc[-6:]
    high_20 = df_1h['h'].iloc[-20:].max()
    near_top = last_6['c'].mean() >= high_20*0.88
    if not near_top: return False, 0, ""
    last_6['rng'] = (last_6['h']-last_6['l'])/last_6['c']
    tight = (last_6['rng'] < 0.022).sum()
    avg = last_6['rng'].mean()
    if tight >= 2:
        return True, tight, f"Tight Top {tight}/6H {avg*100:.2f}%"
    return False, 0, ""

def detect_15m_breakdown(df_15m):
    if len(df_15m) < 30: return False, ""
    df_15m['ema20']=ema(df_15m['c'],20)
    df_15m['ema6']=ema(df_15m['c'],6)
    last = df_15m.iloc[-1]; prev = df_15m.iloc[-2]
    body = abs(last['c']-last['o']); rng = last['h']-last['l']
    solid = body/rng > 0.55 if rng>0 else False
    bear = last['c'] < last['o']
    breakdown = last['c'] < last['ema20'] and prev['c'] >= last['ema20']*0.998
    if breakdown and solid and bear:
        return True, f"15M Breakdown EMA20 {last['ema20']:.6f} Close {last['c']:.6f} {body/rng*100:.0f}%"
    return False, ""

def chart_saved_top(df_1h, base, exn, info):
    fig, ax = plt.subplots(figsize=(11,4))
    d = df_1h.tail(50)
    for idx,row in d.iterrows():
        col='#00b26a' if row['c']>=row['o'] else '#ff3b30'
        ax.plot([idx,idx],[row['l'],row['h']],color=col,lw=1)
        ax.plot([idx,idx],[row['o'],row['c']],color=col,lw=3)
    ax.plot(d.index, ema(d['c'],20), color='#5b4cff', lw=1.2, label='EMA20 1H')
    ax.axvspan(d.index[-6], d.index[-1], color='lightcoral', alpha=0.25)
    ax.set_title(f"{base} TOP GAINER {exn} SAVED - {info}", fontweight='bold', color='red')
    ax.legend(); ax.grid(alpha=0.2)
    return fig

def chart_breakdown(df_1h, df_15m, base, exn, saved_info, break_info):
    fig, (ax1, ax2) = plt.subplots(2,1,figsize=(12,7), gridspec_kw={'height_ratios':[2,1]})
    d1 = df_1h.tail(50)
    for idx,row in d1.iterrows():
        col='#00b26a' if row['c']>=row['o'] else '#ff3b30'
        ax1.plot([idx,idx],[row['l'],row['h']],color=col,lw=1)
        ax1.plot([idx,idx],[row['o'],row['c']],color=col,lw=3)
    ax1.plot(d1.index, ema(d1['c'],20), color='#5b4cff', lw=1.2, label='EMA20 1H')
    ax1.axvspan(d1.index[-6], d1.index[-1], color='lightcoral', alpha=0.25)
    ax1.set_title(f"{base} SHORT TOP {exn} SAVED {saved_info['first_seen'][:10]}", fontweight='bold', color='red')
    ax1.legend(); ax1.grid(alpha=0.2)
    d15 = df_15m.tail(40)
    for idx,row in d15.iterrows():
        col='#00b26a' if row['c']>=row['o'] else '#ff3b30'
        ax2.plot([idx,idx],[row['l'],row['h']],color=col,lw=1)
        ax2.plot([idx,idx],[row['o'],row['c']],color=col,lw=3)
    ax2.plot(d15.index, ema(d15['c'],20), color='#5b4cff', lw=1.5, label='EMA20 15M')
    ax2.set_title(f"15M Breakdown: {break_info}", fontsize=10, color='red')
    ax2.legend(); ax2.grid(alpha=0.2)
    plt.tight_layout()
    return fig

def load_mem():
    if os.path.exists(MEMORY_FILE):
        try:
            data=json.loads(open(MEMORY_FILE).read())
            cutoff=datetime.utcnow()-timedelta(days=4)
            cleaned={}
            for k,v in data.items():
                b=v.get('base',k).upper()
                if datetime.fromisoformat(v['first_seen'])>cutoff and b not in BLOCK_ALL:
                    if 'STOCK' not in b and 'SOX' not in b and 'SKDD' not in b and 'XSOX' not in b:
                        cleaned[b]=v
            return cleaned
        except: pass
    return {}

def save_mem(m):
    try: open(MEMORY_FILE,'w').write(json.dumps(m,indent=2))
    except: pass

send_text(f"🔻 <b>SHORT V3 ULTRA FILTER {datetime.utcnow().strftime('%H:%M UTC')}</b>\nOnly Crypto USDT PERP")

EXS = [
    ('BINANCE', ccxt.binanceusdm, {'enableRateLimit': True}),
    ('MEXC', ccxt.mexc, {'enableRateLimit': True, 'options': {'defaultType': 'swap'}}),
    ('OKX', ccxt.okx, {'enableRateLimit': True, 'options': {'defaultType': 'swap'}}),
    ('GATE', ccxt.gate, {'enableRateLimit': True, 'options': {'defaultType': 'swap'}}),
    ('BITGET', ccxt.bitget, {'enableRateLimit': True, 'options': {'defaultType': 'swap'}}),
]

memory=load_mem()
send_text(f"🧠 Short Memory: {len(memory)} coins (cleaned)")
seen=set(memory.keys())
new=0; breaks=0; scanned=0; gainer_found=0

for exn, cls, opts in EXS:
    try:
        ex=cls(opts); ex.load_markets()
        syms=[s for s in ex.markets if is_valid_crypto(s)]
        send_text(f"📊 {exn}: {len(syms)} USDT PERP - ONLY CRYPTO")
        for sym in syms:
            base=sym.split('/')[0].split(':')[0].upper()
            if base in memory:
                try:
                    df_15m=pd.DataFrame(ex.fetch_ohlcv(sym,'15m',limit=50), columns=['t','o','h','l','c','v'])
                    is_b, br_info=detect_15m_breakdown(df_15m)
                    if is_b:
                        df_1h=pd.DataFrame(ex.fetch_ohlcv(sym,'1h',limit=60), columns=['t','o','h','l','c','v'])
                        fig=chart_breakdown(df_1h, df_15m, base, exn, memory[base], br_info)
                        cap=f"🔻 <b>SHORT BREAKDOWN 15M {base} {exn}</b>\n{memory[base]['info']}\n{br_info}\nPrice {df_15m.iloc[-1]['c']:.6f}\n🔥 Deleted"
                        send_photo(fig, cap)
                        del memory[base]; breaks+=1; save_mem(memory)
                        time.sleep(0.5)
                except: continue
                continue
            if base in seen: continue
            try:
                df_1d=pd.DataFrame(ex.fetch_ohlcv(sym,'1d',limit=35), columns=['t','o','h','l','c','v'])
                ok_gainer, pump, info_gainer=check_gainer_pump(df_1d)
                if not ok_gainer: continue
                gainer_found+=1
                df_1h=pd.DataFrame(ex.fetch_ohlcv(sym,'1h',limit=50), columns=['t','o','h','l','c','v'])
                ok_tight, tight_n, info_tight=check_tight_top(df_1h)
                if not ok_tight: continue
                scanned+=1
                memory[base]={"symbol":sym,"base":base,"exchange":exn,"info":f"{info_gainer} | {info_tight}","pump":pump,"first_seen":datetime.utcnow().isoformat()}
                seen.add(base); new+=1
                if new<=12:
                    fig=chart_saved_top(df_1h, base, exn, f"{info_gainer} | {info_tight}")
                    cap=f"📦 <b>SAVED TOP {base} {exn}</b>\n{info_gainer}\n{info_tight}\nWatching 15M breakdown"
                    send_photo(fig, cap)
                    time.sleep(0.4)
                save_mem(memory)
            except: continue
    except Exception as e:
        send_text(f"⚠️ {exn} {str(e)[:120]}")
        continue

save_mem(memory)
send_text(f"✅ <b>SHORT V3 DONE</b> Gainers {gainer_found} New Saved {new} Breakouts 15M {breaks}\n🧠 Total {len(memory)} only crypto\nNo SKDD XSOXS LEAD GLD BTC ETH")
