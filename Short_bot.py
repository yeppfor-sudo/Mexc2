import ccxt, pandas as pd, requests, os, io, time
import matplotlib.pyplot as plt
from datetime import datetime

BOT_TOKEN=os.getenv("BOT_TOKEN"); CHAT_ID=os.getenv("CHAT_ID")

# BLOCKS - No stocks, commodities, gold, mega
BLOCK_STOCKS = {'TLT','TLH','IEF','SHY','INDA','EWJ','EWA','EWZ','KWEB','QQQ','SPY','DIA','IWM','EWW','EWC','MCHI','INDY','STRC','OURA','SPX','SPX500','NAS100','DJI','US30','UK100','GER40','RKLB','NVDA','AAPL','TSLA','MSFT','STOCK','ETF','OIL','SILVER','GOLD','XAU','XAG','XAUT','PAXG','KAU','TRY','EUR','GBP','JPY','AUD','CAD','CHF','NZD','USDC','BUSD','FDUSD','TUSD','DAI','USDE','USDP','GUSD','PYUSD','EURI','EURC','XLU','XLP','XLE','XLF','XLB','XLI','XLY','XLK','XLV','GLD','SLV','GDX','GDXJ','COPX','LIT','URA'}
BLOCK_MEGA = {'BTC','ETH','SOL','BNB','TRX','XRP','ADA','DOGE','AVAX','DOT','LINK','LTC','BCH','XLM','ETC','HBAR','APT','NEAR','UNI','FIL','ARB','OP','ATOM','MATIC','POL','TON','SHIB','PEPE','CRO','LEO','OKB','BGB','SUI','LDO','RNDR','TAO','FET','WIF','ENA','JUP','SEI','TIA','ONDO','W','PYTH','STRK','JTO','ENS'}
BLOCK_COMMODITY = {'LEAD','COPPER','NICKEL','ZINC','TIN','ALUM','ALUMINIUM','ALUMINUM','CORN','SOY','WHEAT','COTTON','SUGAR','COFFEE','COCOA','GAS'}
BLOCK_ALL = BLOCK_STOCKS | BLOCK_MEGA | BLOCK_COMMODITY

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

def is_valid_crypto(sym):
    base = sym.split('/')[0].split(':')[0].upper()
    if 'STOCK' in base: return False
    if base in BLOCK_ALL: return False
    if any(x in base for x in ['XAU','PAXG','GOLD','XAG','LEAD','COPPER','NICKEL','ZINC','TIN','GLD','SLV','ALUM']): return False
    if len(base) < 2 or len(base) > 6: return False
    if any(c.isdigit() for c in base): return False
    if not base.isalpha(): return False
    if not ('USDT:USDT' in sym or ':USDT' in sym): return False
    return True

# TOP GAINER + 45% PUMP CHECK
def check_top_gainer_and_pump(df_1d):
    if len(df_1d) < 35: return False, 0, 0, ""
    # Daily % today and yesterday
    df_1d['pct'] = df_1d['c'].pct_change()*100
    today_pct = df_1d['pct'].iloc[-1]
    yest_pct = df_1d['pct'].iloc[-2]
    # Top gainer = today >=6% or yesterday >=8% or both
    is_gainer = (today_pct >= 6) or (yest_pct >= 8)
    if not is_gainer:
        return False, 0, 0, ""
    # 45% pump from recent low (low of last 20 days vs high of last 3 days)
    low_20 = df_1d['l'].iloc[-20:].min()
    high_3 = df_1d['h'].iloc[-3:].max()
    pump_pct = (high_3 / low_20 - 1)*100 if low_20>0 else 0
    if pump_pct < 45:
        return False, 0, 0, ""
    return True, pump_pct, today_pct, f"Gainer T:{today_pct:.1f}% Y:{yest_pct:.1f}% Pump {pump_pct:.1f}% from {low_20}"

# TIGHT ON TOP - 1H
def check_tight_top(df_1h):
    if len(df_1h) < 20: return False, 0, ""
    last_6 = df_1h.iloc[-6:]
    # Must be near high
    high_20 = df_1h['h'].iloc[-20:].max()
    near_top = last_6['c'].mean() >= high_20 * 0.92
    if not near_top:
        return False, 0, ""
    # Tight range = (h-l)/c < 1.5%
    last_6['rng'] = (last_6['h'] - last_6['l']) / last_6['c']
    tight_candles = (last_6['rng'] < 0.015).sum()
    avg_rng = last_6['rng'].mean()
    # Need 2+ 1H candles closed tight
    if tight_candles >= 2 and avg_rng < 0.018 and avg_rng > 0.0008:
        return True, tight_candles, f"Tight Top {tight_candles}/6H avg {avg_rng*100:.2f}%"
    return False, 0, ""

# BREAKDOWN 15M EMA20 SOLID
def detect_15m_breakdown(df_15m):
    if len(df_15m) < 40: return False, ""
    df_15m['ema20']=ema(df_15m['c'],20)
    df_15m['ema6']=ema(df_15m['c'],6)
    df_15m['ema12']=ema(df_15m['c'],12)
    last = df_15m.iloc[-1]; prev = df_15m.iloc[-2]
    body = abs(last['c']-last['o']); range_c = last['h']-last['l']
    solid = body / range_c > 0.60 if range_c>0 else False
    bearish = last['c'] < last['o']
    breakdown = last['c'] < last['ema20'] and prev['c'] >= last['ema20']*0.995
    below_all = last['c'] < last['ema6'] and last['c'] < last['ema12']
    vol_ok = last['v'] > df_15m['v'].iloc[-20:].mean() * 0.7
    if breakdown and solid and bearish and below_all and vol_ok:
        return True, f"15M SOLID Breakdown EMA20 {last['ema20']:.6f} Close {last['c']:.6f} Body {body/range_c*100:.0f}% Bearish"
    return False, ""

def chart_short_breakdown(df_1h, df_15m, base, exn, pump_info, tight_info, break_info):
    fig, (ax1, ax2) = plt.subplots(2,1,figsize=(12,7), gridspec_kw={'height_ratios':[2,1]})
    d1 = df_1h.tail(60)
    for idx,row in d1.iterrows():
        col='#00b26a' if row['c']>=row['o'] else '#ff3b30'
        ax1.plot([idx,idx],[row['l'],row['h']],color=col,lw=1)
        ax1.plot([idx,idx],[row['o'],row['c']],color=col,lw=3)
    ax1.plot(d1.index, ema(d1['c'],20), color='#5b4cff', lw=1.2, label='EMA20 1H')
    ax1.plot(d1.index, ema(d1['c'],6), color='orange', lw=0.8, label='EMA6')
    ax1.plot(d1.index, ema(d1['c'],12), color='yellow', lw=0.8, label='EMA12')
    # Highlight tight top circle like your screenshot
    ax1.axvspan(d1.index[-6], d1.index[-1], color='lightcoral', alpha=0.25)
    ax1.set_title(f"{base} SHORT {exn} - {pump_info} | {tight_info}", fontweight='bold', color='red')
    ax1.legend(); ax1.grid(alpha=0.2)

    d15 = df_15m.tail(50)
    for idx,row in d15.iterrows():
        col='#00b26a' if row['c']>=row['o'] else '#ff3b30'
        ax2.plot([idx,idx],[row['l'],row['h']],color=col,lw=1)
        ax2.plot([idx,idx],[row['o'],row['c']],color=col,lw=3)
    ax2.plot(d15.index, ema(d15['c'],20), color='#5b4cff', lw=1.5, label='EMA20 15M')
    ax2.plot(d15.index, ema(d15['c'],6), color='orange', lw=0.8, label='EMA6')
    ax2.plot(d15.index, ema(d15['c'],12), color='yellow', lw=0.8, label='EMA12')
    ax2.set_title(f"15M Breakdown: {break_info}", fontsize=10, color='red')
    ax2.legend(); ax2.grid(alpha=0.2)
    plt.tight_layout()
    return fig

send_text(f"🔻 <b>SHORT TOP BOT {datetime.utcnow().strftime('%H:%M UTC')}</b>\nScanning for Pump 45%+ Top Gainers")

EXS = [
    ('BINANCE', ccxt.binanceusdm, {'enableRateLimit': True}),
    ('MEXC', ccxt.mexc, {'enableRateLimit': True, 'options': {'defaultType': 'swap'}}),
    ('OKX', ccxt.okx, {'enableRateLimit': True, 'options': {'defaultType': 'swap'}}),
    ('BYBIT', ccxt.bybit, {'enableRateLimit': True, 'options': {'defaultType': 'swap'}}),
    ('GATE', ccxt.gate, {'enableRateLimit': True, 'options': {'defaultType': 'swap'}}),
    ('BITGET', ccxt.bitget, {'enableRateLimit': True, 'options': {'defaultType': 'swap'}}),
]

total_shorts = 0; scanned = 0

for exn, cls, opts in EXS:
    try:
        ex = cls(opts); ex.load_markets()
        syms = [s for s in ex.markets if is_valid_crypto(s)]
        send_text(f"📊 {exn}: {len(syms)} USDT PERP scanning")
        for sym in syms:
            base = sym.split('/')[0].split(':')[0].upper()
            try:
                # 1D for top gainer + pump
                df_1d = pd.DataFrame(ex.fetch_ohlcv(sym,'1d',limit=40), columns=['t','o','h','l','c','v'])
                is_gainer, pump_pct, today_pct, pump_info = check_top_gainer_and_pump(df_1d)
                if not is_gainer: continue
                # 1H tight on top
                df_1h = pd.DataFrame(ex.fetch_ohlcv(sym,'1h',limit=60), columns=['t','o','h','l','c','v'])
                is_tight, tight_n, tight_info = check_tight_top(df_1h)
                if not is_tight: continue
                # 15M breakdown EMA20 solid
                df_15m = pd.DataFrame(ex.fetch_ohlcv(sym,'15m',limit=60), columns=['t','o','h','l','c','v'])
                is_break, break_info = detect_15m_breakdown(df_15m)
                scanned+=1
                if is_break:
                    fig = chart_short_breakdown(df_1h, df_15m, base, exn, pump_info, tight_info, break_info)
                    cap = f"🔻 <b>SHORT BREAKDOWN 15M {base} {exn}</b>\n{pump_info}\n{tight_info}\n{break_info}\nPrice {df_15m.iloc[-1]['c']:.6f}\nEntry breakdown - Target tight top low"
                    send_photo(fig, cap)
                    total_shorts+=1
                    time.sleep(0.6)
                    if total_shorts >= 20: break
            except: continue
        if total_shorts >= 20: break
    except Exception as e:
        send_text(f"⚠️ {exn} {str(e)[:100]}")
        continue

send_text(f"✅ <b>SHORT Done</b> Scanned gainer+pump {scanned} Found SHORT breakdowns {total_shorts}\nEvery 4h scan")
