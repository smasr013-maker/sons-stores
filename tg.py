# -*- coding: utf-8 -*-
"""
✨ SoNs Store — Premium Accounts + Game Charging Bot
Telegram Stars + TON Connect
Developer: @xdell
"""

import telebot
from telebot import types
from telebot.types import LabeledPrice
import time, json, logging, uuid, os, threading, requests
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==================== CONFIG ====================
BOT_TOKEN     = os.environ.get('BOT_TOKEN',   "8232296660:AAFwfRPseSS5uGeNYerw3l51zzDqcPFzw0Q").strip()
OWNER_ID      = int(os.environ.get('OWNER_ID', '6285783725'))
WEBAPP_HOST   = "0.0.0.0"
WEBAPP_PORT   = int(os.environ.get('PORT', 5050))
WEBAPP_URL    = os.environ.get('WEBAPP_URL', 'https://mystore5050.loca.lt')

# TON Connect config
TON_MANIFEST_URL = os.environ.get('TON_MANIFEST_URL', f'{WEBAPP_URL}/tonconnect-manifest.json')
TON_RATE_USDT    = float(os.environ.get('TON_RATE_USDT', '7.0'))  # 1 TON = X USDT

DEFAULT_REDEEM_CODES = {"SONS50":1000,"SONS100":2000,"WELCOME25":500,"FREE10":200}

# ==================== AUTH HELPERS ====================
def load_admins():
    try:
        with open('bot_data/admins.json','r',encoding='utf-8') as f:
            return json.load(f)
    except:
        return {'owners':[OWNER_ID],'admins':[]}

def save_admins(data):
    os.makedirs('bot_data', exist_ok=True)
    with open('bot_data/admins.json','w',encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def is_owner(uid:int)->bool:
    return int(uid)==int(OWNER_ID)

def is_admin(uid:int)->bool:
    a=load_admins()
    try:
        return int(uid) in [int(x) for x in a.get('admins',[])] or is_owner(uid)
    except:
        return is_owner(uid)

# ==================== DATABASE ====================
class Database:
    def __init__(self):
        os.makedirs('bot_data', exist_ok=True)
        self.accounts        = self._load('accounts.json', [])
        self.users           = self._load('users.json', [])
        self.purchases       = self._load('purchases.json', [])
        self.pending         = self._load('pending.json', {})
        self.balances        = self._load('balances.json', [])
        self.redeem_codes    = self._load('redeem_codes.json', DEFAULT_REDEEM_CODES)
        self.used_codes      = self._load('used_codes.json', [])
        self.stats           = self._load('stats.json', {'revenue':0,'purchases':0})
        self.categories      = self._load('categories.json', {})
        self.admins          = self._load('admins.json', {'owners':[OWNER_ID],'admins':[]})
        self.wallet_tx       = self._load('wallet_transactions.json', [])
        # Game charging orders
        self.game_orders     = self._load('game_orders.json', [])
        self.game_categories = self._load('game_categories.json', {})
        # TON pending payments
        self.ton_payments    = self._load('ton_payments.json', {})
        # Total collected revenue in USD
        self.revenue_usd     = self._load('revenue_usd.json', {'total_usd': 0.0})

    def _load(self, n, d):
        try:
            with open(f'bot_data/{n}','r',encoding='utf-8') as f: return json.load(f)
        except: return d

    def _save(self, n, data):
        with open(f'bot_data/{n}','w',encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    # ── Balances ──────────────────────────────────────────────
    def get_balance(self, uid):
        b = next((b for b in self.balances if b.get('user_id')==str(uid)), None)
        return b['stars'] if b else 0

    def add_balance(self, uid, amount):
        b = next((b for b in self.balances if b.get('user_id')==str(uid)), None)
        if b: b['stars'] += amount
        else: self.balances.append({'user_id':str(uid),'stars':amount})
        self._save('balances.json', self.balances)

    def deduct_balance(self, uid, amount):
        b = next((b for b in self.balances if b.get('user_id')==str(uid)), None)
        if b and b['stars'] >= amount:
            b['stars'] -= amount; self._save('balances.json', self.balances); return True
        return False

    # ── Accounts ──────────────────────────────────────────────
    def get_accounts(self): return self.accounts
    def get_account(self, aid): return next((a for a in self.accounts if a.get('id')==aid), None)

    def add_account(self, data):
        data['id'] = uuid.uuid4().hex[:8]; data['sold'] = 0
        data['created'] = datetime.now().isoformat()
        self.accounts.append(data); self._save('accounts.json', self.accounts)
        return data['id']

    def update_account(self, aid, upd):
        a = self.get_account(aid)
        if a: a.update(upd); self._save('accounts.json', self.accounts); return True
        return False

    def delete_account(self, aid):
        a = self.get_account(aid)
        if a: self.accounts.remove(a); self._save('accounts.json', self.accounts); return True
        return False

    def update_stock(self, aid, s):
        a = self.get_account(aid)
        if a: a['stock'] = s; self._save('accounts.json', self.accounts)

    def increment_sold(self, aid):
        a = self.get_account(aid)
        if a: a['sold'] = a.get('sold',0)+1; self._save('accounts.json', self.accounts)

    # ── Stats ─────────────────────────────────────────────────
    def get_stats(self):
        return {'total_accounts':len(self.accounts),
                'total_sold':sum(a.get('sold',0) for a in self.accounts),
                'revenue':sum(a.get('sold',0)*a.get('price',0) for a in self.accounts),
                'total_purchases':self.stats.get('purchases',0),
                'total_game_orders':len(self.game_orders),
                'total_usd': self.get_total_usd()}

    # ── Users ─────────────────────────────────────────────────
    def get_or_create_user(self, uid, username=None, first_name=None):
        u = next((u for u in self.users if u.get('id')==str(uid)), None)
        if u: u['last_active']=datetime.now().isoformat(); self._save('users.json',self.users); return u
        nu = {'id':str(uid),'username':username,'first_name':first_name,
              'spent':0,'purchases':0,'joined':datetime.now().isoformat(),'last_active':datetime.now().isoformat()}
        self.users.append(nu); self._save('users.json',self.users); return nu

    def update_user_stats(self, uid, amount):
        u = next((u for u in self.users if u.get('id')==str(uid)), None)
        if u: u['spent']=u.get('spent',0)+amount; u['purchases']=u.get('purchases',0)+1; self._save('users.json',self.users)

    def get_user_stats(self, uid):
        u = next((u for u in self.users if u.get('id')==str(uid)), None)
        return {'spent':u.get('spent',0),'purchases':u.get('purchases',0),'joined':u.get('joined','')} if u else {'spent':0,'purchases':0}

    def get_all_users(self): return self.users
    def get_user_count(self): return len(self.users)

    # ── Redeem Codes ──────────────────────────────────────────
    def add_redeem_code(self, code, amount):
        code = code.strip().upper()
        if not code or amount<=0: return False
        self.redeem_codes[code] = amount; self._save('redeem_codes.json',self.redeem_codes); return True

    def delete_redeem_code(self, code):
        code = code.strip().upper()
        if code in self.redeem_codes:
            del self.redeem_codes[code]; self._save('redeem_codes.json',self.redeem_codes); return True
        return False

    def redeem_code(self, uid, code):
        code = code.strip().upper()
        if code in self.used_codes: return 0
        amount = self.redeem_codes.get(code, 0)
        if amount > 0:
            self.used_codes.append(code); self._save('used_codes.json',self.used_codes)
            self.add_balance(uid, amount); return amount
        return 0

    def get_redeem_codes(self): return self.redeem_codes

    # ── Pending ───────────────────────────────────────────────
    def add_pending(self, pid, data): self.pending[pid]=data; self._save('pending.json',self.pending)
    def get_pending(self, pid):       return self.pending.get(pid)
    def remove_pending(self, pid):
        if pid in self.pending: del self.pending[pid]; self._save('pending.json',self.pending)

    # ── Purchases ─────────────────────────────────────────────
    def add_purchase(self, data):
        data['id']=uuid.uuid4().hex[:8]; data['timestamp']=datetime.now().isoformat()
        self.purchases.append(data); self._save('purchases.json',self.purchases)
        price = data.get('price',0)
        self.stats['revenue']=self.stats.get('revenue',0)+price
        self.stats['purchases']=self.stats.get('purchases',0)+1
        self._save('stats.json',self.stats)
        usd_amount = round(price * STAR_TO_USD, 4)
        self.revenue_usd['total_usd'] = round(self.revenue_usd.get('total_usd',0) + usd_amount, 4)
        self._save('revenue_usd.json', self.revenue_usd)

    def get_total_usd(self):
        return self.revenue_usd.get('total_usd', 0.0)

    def add_usd_revenue(self, usd_amount):
        self.revenue_usd['total_usd'] = round(self.revenue_usd.get('total_usd',0) + usd_amount, 4)
        self._save('revenue_usd.json', self.revenue_usd)

    def get_user_purchases(self, uid, limit=30):
        ups = [p for p in self.purchases if p.get('user_id')==str(uid)]
        return sorted(ups, key=lambda x:x.get('timestamp',''), reverse=True)[:limit]

    # ── Game Categories & Orders ──────────────────────────────
    def get_game_categories(self): return self.game_categories

    def add_game_category(self, key, data):
        self.game_categories[key] = data
        self._save('game_categories.json', self.game_categories)

    def delete_game_category(self, key):
        if key in self.game_categories:
            del self.game_categories[key]
            self._save('game_categories.json', self.game_categories)
            return True
        return False

    def get_game_orders(self, uid=None):
        if uid:
            return [o for o in self.game_orders if o.get('user_id')==str(uid)]
        return self.game_orders

    def add_game_order(self, data):
        data['id']     = uuid.uuid4().hex[:8]
        data['status'] = 'pending_payment'
        data['created'] = datetime.now().isoformat()
        self.game_orders.append(data)
        self._save('game_orders.json', self.game_orders)
        return data['id']

    def get_game_order(self, oid):
        return next((o for o in self.game_orders if o.get('id')==oid), None)

    def update_game_order(self, oid, upd):
        o = self.get_game_order(oid)
        if o: o.update(upd); self._save('game_orders.json', self.game_orders); return True
        return False

    # ── TON Payments ──────────────────────────────────────────
    def save_ton_payment(self, pid, data):
        self.ton_payments[pid] = data
        self._save('ton_payments.json', self.ton_payments)

    def get_ton_payment(self, pid):
        return self.ton_payments.get(pid)

    def confirm_ton_payment(self, pid):
        p = self.ton_payments.get(pid)
        if p:
            p['status'] = 'confirmed'
            p['confirmed_at'] = datetime.now().isoformat()
            self._save('ton_payments.json', self.ton_payments)
            return p
        return None

    def remove_ton_payment(self, pid):
        if pid in self.ton_payments:
            del self.ton_payments[pid]
            self._save('ton_payments.json', self.ton_payments)

    # ── Admins ────────────────────────────────────────────────
    def add_admin(self, uid):
        a = load_admins()
        if int(uid) not in [int(x) for x in a['admins']]:
            a['admins'].append(int(uid))
            save_admins(a)
            self.admins = a
            return True
        return False

    def remove_admin(self, uid):
        a = load_admins()
        try:
            a['admins'] = [x for x in a['admins'] if int(x) != int(uid)]
            save_admins(a)
            self.admins = a
            return True
        except: return False

    def get_admins_list(self):
        return load_admins()


db = Database()

# ==================== SAMPLE DATA ====================
if not db.get_accounts():
    for a in [
        {"name":"Google Premium","category":"google","price":50,"stock":10,"account":"google@ex.com:pass123","description":"حساب جوجل بريميوم + 100GB"},
        {"name":"Netflix Ultra HD","category":"netflix","price":100,"stock":5,"account":"netflix@ex.com:pass789","description":"نتفليكس 4K - 4 شاشات"},
        {"name":"Spotify Premium","category":"spotify","price":60,"stock":15,"account":"spotify@ex.com:pass321","description":"سبوتيفاي بريميوم فردي"},
        {"name":"Netflix Standard","category":"netflix","price":65,"stock":12,"account":"nf2@ex.com:pass654","description":"نتفليكس HD - شاشتان"},
    ]: db.add_account(a)

if not db.get_game_categories():
    for k, v in {
        "pubg":    {"name":"PUBG Mobile","color":"#f5a623","fields":["player_id","server"],"packages":[{"label":"60 UC","price":50},{"label":"325 UC","price":250},{"label":"660 UC","price":500},{"label":"1800 UC","price":1300}]},
        "freefire":{"name":"Free Fire","color":"#ff4444","fields":["player_id"],"packages":[{"label":"100 Diamonds","price":80},{"label":"310 Diamonds","price":230},{"label":"520 Diamonds","price":380},{"label":"1060 Diamonds","price":750}]},
        "clashofclans":{"name":"Clash of Clans","color":"#4a90e2","fields":["player_tag"],"packages":[{"label":"80 Gems","price":60},{"label":"500 Gems","price":350},{"label":"1200 Gems","price":800},{"label":"2500 Gems","price":1600}]},
        "mlbb":    {"name":"Mobile Legends","color":"#9b59b6","fields":["player_id","zone_id"],"packages":[{"label":"86 Diamonds","price":75},{"label":"172 Diamonds","price":145},{"label":"429 Diamonds","price":350},{"label":"1000 Diamonds","price":800}]},
    }.items(): db.add_game_category(k, v)

if not db.categories:
    db.categories = {
        "google":  {"name":"Google","color":"#4285F4"},
        "netflix": {"name":"Netflix","color":"#E50914"},
        "spotify": {"name":"Spotify","color":"#1ED760"},
    }
    db._save('categories.json', db.categories)

bot = telebot.TeleBot(BOT_TOKEN)

# ==================== COMMISSION CONFIG ====================
COMMISSION_RATE = 0.005  # 0.5% عمولة على جميع العمليات

# ==================== FAWRY/VODAFONE CASH CONFIG ====================
VODAFONE_CASH_NUMBER = "01093075185"

# ==================== TOTAL REVENUE IN USD ====================
# سعر النجمة بالدولار (1 نجمة ≈ 0.013 دولار)
STAR_TO_USD = float(os.environ.get('STAR_TO_USD', '0.013'))

def calc_price_with_commission(price: int) -> int:
    """احسب السعر بعد إضافة عمولة 0.5%"""
    return int(price + price * COMMISSION_RATE)


# ==================== HTML WEB APP ====================
HTML_WEBAPP = r'''<!DOCTYPE html>
<html lang="ar" dir="rtl" id="root">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>SoNs Store</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<script src="https://unpkg.com/@tonconnect/ui@2.0.5/dist/tonconnect-ui.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
:root{
  /* Dark Mode palette */
  --K:#0a0b10;--W:#ffffff;--B:#3b82f6;--B2:#1d4ed8;
  --Y:#FFD600;--Y2:#d4aa00;
  --bg:#0f1117;--card:#1a1d27;--card2:#22263a;--card3:#2a2f45;
  --txt:#e8eaf0;--gray:#7c84a0;
  --bdr:rgba(255,255,255,.07);--sh:0 4px 20px rgba(0,0,0,.4);
  --r:20px;--rsm:14px;
  --ton:#0098ea;--ton2:#006ab8;
  --success:#22c55e;--danger:#ef4444;--warn:#f59e0b;
}
body{font-family:-apple-system,BlinkMacSystemFont,'SF Pro Text',sans-serif;background:var(--bg);color:var(--txt);min-height:100vh;overflow-x:hidden}

/* ═══ SPLASH ════════════════════════════════════════════ */
#splash{position:fixed;inset:0;z-index:9999;background:var(--K);display:flex;flex-direction:column;align-items:center;justify-content:center;transition:opacity .5s,transform .5s}
#splash.out{opacity:0;transform:scale(1.07);pointer-events:none}
.sp-ring{width:112px;height:112px;border-radius:34px;background:linear-gradient(145deg,var(--B),var(--B2));display:flex;align-items:center;justify-content:center;margin-bottom:24px;animation:sPulse 2s ease-in-out infinite;box-shadow:0 0 0 0 rgba(59,130,246,.4)}
@keyframes sPulse{0%,100%{box-shadow:0 0 0 0 rgba(59,130,246,.4)}50%{box-shadow:0 0 0 20px rgba(59,130,246,0)}}
.sp-t{font-size:26px;font-weight:800;color:#fff;margin-bottom:4px}.sp-t b{color:var(--Y)}
.sp-s{font-size:12px;color:rgba(255,255,255,.3);margin-bottom:42px}
.sp-track{width:150px;height:3px;background:rgba(255,255,255,.1);border-radius:99px;overflow:hidden}
.sp-bar{height:100%;width:0;background:linear-gradient(90deg,var(--B),var(--Y));border-radius:99px;animation:sBar 1.5s cubic-bezier(.4,0,.2,1) forwards}
@keyframes sBar{0%{width:0}40%{width:55%}80%{width:88%}100%{width:100%}}

/* ═══ TOPBAR ════════════════════════════════════════════ */
.topbar{position:sticky;top:0;z-index:60;display:flex;align-items:center;justify-content:space-between;padding:13px 18px 10px;background:rgba(15,17,23,.92);backdrop-filter:blur(22px) saturate(1.8);-webkit-backdrop-filter:blur(22px) saturate(1.8);border-bottom:.5px solid var(--bdr)}
.tb-brand{display:flex;align-items:center;gap:9px}
.tb-logo{width:32px;height:32px;border-radius:10px;background:linear-gradient(135deg,var(--B),var(--B2));display:flex;align-items:center;justify-content:center}
.tb-name{font-size:17px;font-weight:800;color:var(--txt)}.tb-name b{color:var(--Y)}
.lang-btn{background:var(--B);border:none;border-radius:20px;padding:5px 13px;font-size:12px;font-weight:700;color:#fff;cursor:pointer;font-family:inherit;transition:opacity .2s}
.lang-btn:active{opacity:.8}

/* ═══ LAYOUT ════════════════════════════════════════════ */
#app{max-width:500px;margin:0 auto;padding:14px 16px 100px}

/* ═══ USER CARD ═════════════════════════════════════════ */
.uid-card{background:linear-gradient(135deg,#1a1d27,#1e2235);border-radius:24px;padding:18px;margin-bottom:14px;display:flex;align-items:center;gap:14px;cursor:pointer;position:relative;overflow:hidden;transition:transform .2s;box-shadow:0 12px 44px rgba(0,0,0,.5);border:1px solid var(--bdr)}
.uid-card::before{content:'';position:absolute;top:0;left:0;right:0;height:2.5px;background:linear-gradient(90deg,var(--B),var(--Y))}
.uid-card:active{transform:scale(.97)}
.uid-av-wrap{position:relative;flex-shrink:0}
.uid-av{width:60px;height:60px;border-radius:50%;background:linear-gradient(135deg,var(--B),var(--B2));display:flex;align-items:center;justify-content:center;font-size:26px;font-weight:800;color:#fff;border:2.5px solid rgba(255,255,255,.1);overflow:hidden}
.uid-av img{width:100%;height:100%;object-fit:cover}
.uid-dot{width:13px;height:13px;border-radius:50%;background:var(--Y);border:2.5px solid var(--card);position:absolute;bottom:1px;right:1px}
.uid-info{flex:1;min-width:0}
.uid-name{font-size:16px;font-weight:800;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:3px}
.uid-handle{font-size:11px;color:rgba(255,255,255,.35);font-family:monospace;margin-bottom:8px}
.uid-badges{display:flex;gap:5px;flex-wrap:wrap}
.badge{border-radius:20px;padding:3px 9px;font-size:10px;font-weight:700}
.bp{background:var(--Y);color:var(--K)}.ba{background:var(--B);color:#fff}.bl{background:rgba(255,255,255,.1);color:rgba(255,255,255,.5)}
.uid-arr{color:rgba(255,255,255,.15);font-size:22px;flex-shrink:0}

/* ═══ BALANCE ═══════════════════════════════════════════ */
.bal-row{display:flex;gap:10px;margin-bottom:20px}
.bal-box{flex:1;background:var(--card);border-radius:var(--r);padding:15px;box-shadow:var(--sh);display:flex;align-items:center;gap:12px;border:1px solid var(--bdr)}
.bal-ic{width:48px;height:48px;border-radius:14px;background:linear-gradient(135deg,var(--Y),var(--Y2));display:flex;align-items:center;justify-content:center;flex-shrink:0;box-shadow:0 4px 16px rgba(255,214,0,.2)}
.bal-n{font-size:23px;font-weight:800;color:var(--txt)}.bal-l{font-size:11px;color:var(--gray)}
.add-btn{width:50px;height:50px;border-radius:25px;border:none;background:var(--B);color:#fff;font-size:28px;display:flex;align-items:center;justify-content:center;cursor:pointer;box-shadow:0 6px 22px rgba(59,130,246,.3);transition:transform .15s;flex-shrink:0;align-self:center}
.add-btn:active{transform:scale(.88)}

/* ═══ BALANCE CIRCLES ════════════════════════════════════ */
.bal-circles-row{display:flex;gap:16px;margin-bottom:20px;justify-content:center}
.bal-circle-wrap{display:flex;flex-direction:column;align-items:center;gap:8px;flex:1}
.bal-circle{position:relative;width:120px;height:120px;flex-shrink:0}
.bal-ring-svg{position:absolute;inset:0;width:100%;height:100%}
.bal-circle-inner{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:1px}
.bal-circle-val{font-size:15px;font-weight:800;color:var(--txt);line-height:1.1;max-width:80px;text-align:center;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bal-circle-lbl{font-size:9px;color:var(--gray);font-weight:600;text-transform:uppercase;letter-spacing:.5px}
.bal-circle-add{width:40px;height:40px;border-radius:50%;border:none;color:#fff;font-size:22px;display:flex;align-items:center;justify-content:center;cursor:pointer;background:var(--B);box-shadow:0 4px 16px rgba(59,130,246,.35);transition:transform .15s;font-weight:300}
.bal-circle-add:active{transform:scale(.88)}
.bal-circle-stars{background:radial-gradient(circle at 40% 40%,rgba(255,214,0,.08),transparent 70%)}
.bal-circle-ton{background:radial-gradient(circle at 40% 40%,rgba(0,152,234,.08),transparent 70%)}

/* ═══ TON WALLET BUTTON ══════════════════════════════════ */
.ton-wallet-strip{background:var(--card);border-radius:16px;padding:12px 16px;margin-bottom:14px;display:flex;align-items:center;gap:12px;border:1px solid rgba(0,152,234,.25);cursor:pointer;transition:border-color .2s}
.ton-wallet-strip:active{border-color:var(--ton)}
.ton-ic{width:40px;height:40px;border-radius:12px;background:linear-gradient(135deg,var(--ton),var(--ton2));display:flex;align-items:center;justify-content:center;flex-shrink:0}
.ton-wallet-info{flex:1;min-width:0}
.ton-wallet-label{font-size:13px;font-weight:700;color:var(--txt);margin-bottom:2px}
.ton-wallet-addr{font-size:11px;color:var(--gray);font-family:monospace;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ton-connect-btn{background:linear-gradient(135deg,var(--ton),var(--ton2));border:none;border-radius:12px;padding:8px 14px;font-size:12px;font-weight:700;color:#fff;cursor:pointer;font-family:inherit;flex-shrink:0}

/* ═══ SECTION HEADER ════════════════════════════════════ */
.sec-h{display:flex;align-items:center;justify-content:space-between;margin:16px 0 12px}
.sec-t{font-size:19px;font-weight:800;color:var(--txt)}

/* ═══ CATEGORY GRID ═════════════════════════════════════ */
.cat-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:13px;margin-bottom:8px}
.cat-card{background:var(--card);border-radius:22px;padding:20px 14px;text-align:center;cursor:pointer;box-shadow:var(--sh);transition:transform .2s,box-shadow .2s;position:relative;overflow:hidden;border:1.5px solid transparent}
.cat-card:active{transform:scale(.95);border-color:var(--B)}
.cat-bg{position:absolute;inset:0;opacity:.08;border-radius:22px}
.cat-iw{width:74px;height:74px;border-radius:22px;display:flex;align-items:center;justify-content:center;margin:0 auto 13px;position:relative;z-index:1}
.cat-iw svg{width:50px;height:50px}
.cat-nm{font-size:15px;font-weight:700;position:relative;z-index:1;color:var(--txt)}
.cat-cnt{font-size:11px;color:var(--gray);margin-top:3px;position:relative;z-index:1}

/* ═══ GAME SECTION ══════════════════════════════════════ */
.game-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:13px;margin-bottom:8px}
.game-card{background:var(--card);border-radius:22px;padding:18px 14px;text-align:center;cursor:pointer;box-shadow:var(--sh);transition:transform .2s;position:relative;overflow:hidden;border:1.5px solid transparent}
.game-card:active{transform:scale(.95);border-color:var(--B)}
.game-card-color{position:absolute;inset:0;opacity:.09}
.game-card-icon{font-size:42px;margin-bottom:10px;position:relative;z-index:1}
.game-card-name{font-size:14px;font-weight:700;color:var(--txt);position:relative;z-index:1}
.game-card-cnt{font-size:11px;color:var(--gray);margin-top:3px;position:relative;z-index:1}

.pkg-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:16px}
.pkg-btn{background:var(--card2);border:1.5px solid var(--bdr);border-radius:16px;padding:14px 10px;text-align:center;cursor:pointer;transition:border-color .2s,transform .15s;font-family:inherit}
.pkg-btn:active{transform:scale(.95)}
.pkg-btn.selected{border-color:var(--B);background:rgba(59,130,246,.1)}
.pkg-btn .pkg-label{font-size:14px;font-weight:700;color:var(--txt);margin-bottom:4px}
.pkg-btn .pkg-price{font-size:12px;color:var(--Y);font-weight:700}

.game-order-form{background:var(--card);border-radius:20px;padding:18px;border:1px solid var(--bdr);margin-bottom:16px}
.game-order-title{font-size:16px;font-weight:800;color:var(--txt);margin-bottom:14px}
.field-label{font-size:12px;color:var(--gray);font-weight:700;margin-bottom:6px}

/* ═══ PRODUCTS ══════════════════════════════════════════ */
.prod-list{display:flex;flex-direction:column;gap:11px}
.prod-card{background:var(--card);border-radius:var(--r);padding:15px;display:flex;gap:13px;align-items:center;box-shadow:var(--sh);transition:transform .15s;border:1.5px solid var(--bdr)}
.prod-card:active{transform:scale(.98);border-color:var(--B)}
.prod-ic{width:54px;height:54px;border-radius:15px;display:flex;align-items:center;justify-content:center;flex-shrink:0}
.prod-ic svg{width:34px;height:34px}
.prod-info{flex:1;min-width:0}
.prod-nm{font-size:15px;font-weight:700;margin-bottom:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--txt)}
.prod-ds{font-size:11px;color:var(--gray);margin-bottom:7px}
.prod-meta{display:flex;align-items:center;gap:7px;flex-wrap:wrap}
.price-tag{background:var(--Y);color:var(--K);padding:3px 10px;border-radius:20px;font-size:12px;font-weight:800}
.stk-ok{font-size:11px;color:var(--B);font-weight:700}
.stk-no{font-size:11px;color:var(--gray)}
.buy-btn{background:var(--B);border:none;border-radius:22px;padding:9px 18px;color:#fff;font-weight:700;font-size:13px;cursor:pointer;transition:background .15s;flex-shrink:0;font-family:inherit}
.buy-btn:hover{background:var(--B2)}
.buy-btn:active{opacity:.8}
.buy-btn:disabled{background:var(--gray);cursor:default}

/* ═══ TABS ═══════════════════════════════════════════════ */
.tab-bar{
  position:fixed;bottom:0;left:0;right:0;z-index:100;
  background:rgba(15,17,23,.95);
  backdrop-filter:saturate(5) blur(32px);
  -webkit-backdrop-filter:saturate(5) blur(32px);
  border-top:.5px solid rgba(255,255,255,.07);
  padding:8px 6px calc(8px + env(safe-area-inset-bottom));
  display:flex;justify-content:space-around;align-items:flex-start;
}
.tab-btn{
  flex:1;display:flex;flex-direction:column;align-items:center;gap:4px;
  background:none;border:none;padding:4px 2px;cursor:pointer;
  color:var(--gray);font-size:10px;font-weight:500;font-family:inherit;
  transition:color .22s;-webkit-user-select:none;user-select:none;
}
.tab-pill{
  width:44px;height:30px;border-radius:16px;
  display:flex;align-items:center;justify-content:center;
  transition:background .22s,transform .18s;
}
.tab-pill svg{width:20px;height:20px;transition:transform .22s}
.tab-btn .tab-lbl{font-size:9px;font-weight:500;transition:font-weight .22s,color .22s;line-height:1}
.tab-btn.active{color:var(--B)}
.tab-btn.active .tab-pill{background:rgba(59,130,246,.15)}
.tab-btn.active .tab-pill svg{transform:scale(1.12)}
.tab-btn.active .tab-lbl{font-weight:700}
.tab-btn:active .tab-pill{transform:scale(.9)}

/* ═══ MODAL ═════════════════════════════════════════════ */
.modal{display:none;position:fixed;inset:0;z-index:1000;background:rgba(0,0,0,.75);backdrop-filter:blur(6px);align-items:flex-end;justify-content:center}
.modal-box{background:var(--card);border-radius:28px 28px 0 0;padding:0 0 20px;width:100%;max-width:500px;max-height:92vh;overflow-y:auto;border-top:1px solid rgba(255,255,255,.08)}
.m-handle{width:36px;height:4px;background:rgba(255,255,255,.15);border-radius:2px;margin:12px auto 16px}
.m-head{display:flex;align-items:center;justify-content:space-between;padding:0 20px 14px;border-bottom:.5px solid var(--bdr)}
.m-head h3{font-size:17px;font-weight:800;color:var(--txt)}
.m-close{background:var(--card2);border:none;border-radius:50%;width:30px;height:30px;font-size:16px;cursor:pointer;color:var(--gray);display:flex;align-items:center;justify-content:center}
.m-body{padding:16px 20px 0}

.inp{width:100%;padding:13px;border:1.5px solid var(--bdr);border-radius:13px;background:var(--card2);color:var(--txt);font-size:15px;text-align:center;margin-bottom:12px;font-family:inherit;outline:none;transition:border-color .2s}
.inp:focus{border-color:var(--B)}
textarea.inp{resize:none;text-align:right}

.sbtn{background:var(--B);border:none;border-radius:15px;padding:14px;width:100%;color:#fff;font-size:15px;font-weight:800;cursor:pointer;font-family:inherit;transition:background .2s}
.sbtn:hover{background:var(--B2)}
.sbtn:active{opacity:.85}
.sbtn.yn{background:linear-gradient(135deg,var(--Y),var(--Y2));color:var(--K)}
.sbtn.ton{background:linear-gradient(135deg,var(--ton),var(--ton2))}
.sbtn.danger{background:var(--danger)}

/* ═══ PAY TABS ══════════════════════════════════════════ */
.pay-tabs{display:flex;gap:8px;margin-bottom:16px;background:var(--card2);border-radius:14px;padding:4px}
.pay-tab{flex:1;border:none;border-radius:11px;padding:9px 6px;font-size:13px;font-weight:700;cursor:pointer;font-family:inherit;background:transparent;color:var(--gray);transition:all .2s}
.pay-tab.active{background:var(--card3);color:var(--txt);box-shadow:0 2px 8px rgba(0,0,0,.3)}
.pay-tab.ton-tab.active{color:var(--ton)}

.amt-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:14px}
.amt-btn{background:var(--card2);border:1.5px solid var(--bdr);border-radius:12px;padding:10px 6px;text-align:center;cursor:pointer;font-size:13px;font-weight:700;color:var(--txt);transition:border-color .2s,transform .15s}
.amt-btn:active{transform:scale(.95);border-color:var(--B)}

/* ═══ TON PAYMENT SECTION ════════════════════════════════ */
.ton-qr-box{text-align:center;padding:10px 0}
.ton-qr-box img{width:180px;height:180px;border-radius:16px;margin-bottom:10px;border:2px solid var(--bdr)}
.ton-pay-info{background:rgba(0,152,234,.08);border:1.5px solid rgba(0,152,234,.25);border-radius:13px;padding:12px 14px;font-size:13px;margin-top:10px;line-height:1.7;color:var(--txt)}
.ton-addr-box{background:var(--card2);border:1px solid var(--bdr);border-radius:12px;padding:10px 14px;margin:10px 0;font-family:monospace;font-size:11px;color:var(--B);word-break:break-all;cursor:pointer}

/* ═══ ORDER STATUS BADGE ═════════════════════════════════ */
.order-badge{display:inline-block;padding:3px 10px;border-radius:20px;font-size:11px;font-weight:700}
.order-badge.pending{background:rgba(245,158,11,.15);color:var(--warn)}
.order-badge.paid{background:rgba(34,197,94,.15);color:var(--success)}
.order-badge.processing{background:rgba(59,130,246,.15);color:var(--B)}
.order-badge.done{background:rgba(34,197,94,.15);color:var(--success)}
.order-badge.cancelled{background:rgba(239,68,68,.15);color:var(--danger)}

/* ═══ PROFILE / STATS ════════════════════════════════════ */
.stat-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-top:14px}
.stat-box{background:var(--card);border-radius:var(--r);padding:16px;text-align:center;box-shadow:var(--sh);border:1px solid var(--bdr)}
.stat-n{font-size:22px;font-weight:800;color:var(--B)}.stat-l{font-size:11px;color:var(--gray);margin-top:4px}

/* ═══ PURCHASES ═════════════════════════════════════════ */
.purch-item{background:var(--card);border-radius:var(--r);padding:14px 16px;margin-bottom:10px;box-shadow:var(--sh);display:flex;align-items:center;gap:12px;border-right:4px solid var(--Y);border:1px solid var(--bdr);border-right:4px solid var(--Y)}

/* ═══ ADMIN ═════════════════════════════════════════════ */
.adm-card{background:var(--card);border-radius:var(--r);padding:14px 16px;margin-bottom:10px;display:flex;align-items:center;gap:14px;cursor:pointer;box-shadow:var(--sh);transition:transform .15s;border:1px solid var(--bdr)}
.adm-card:active{transform:scale(.98)}
.adm-ic{width:48px;height:48px;border-radius:14px;background:var(--card2);display:flex;align-items:center;justify-content:center;flex-shrink:0}
.adm-ic svg{width:26px;height:26px}
.adm-info{flex:1}.adm-t{font-size:15px;font-weight:700;margin-bottom:3px;color:var(--txt)}.adm-d{font-size:11px;color:var(--gray)}
.adm-arr{color:var(--gray);font-size:20px}
.adm-stats{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-bottom:16px}
.del-btn{background:rgba(239,68,68,.15);color:var(--danger);border:1px solid rgba(239,68,68,.3);border-radius:20px;padding:6px 13px;cursor:pointer;font-size:12px;font-family:inherit;font-weight:700}
.edit-btn{background:rgba(59,130,246,.15);color:var(--B);border:1px solid rgba(59,130,246,.3);border-radius:20px;padding:6px 13px;cursor:pointer;font-size:12px;margin-inline-start:6px;font-family:inherit;font-weight:700}
.done-btn{background:rgba(34,197,94,.15);color:var(--success);border:1px solid rgba(34,197,94,.3);border-radius:20px;padding:6px 13px;cursor:pointer;font-size:12px;font-family:inherit;font-weight:700}

/* ═══ BACK + MISC ════════════════════════════════════════ */
.back-btn{display:inline-flex;align-items:center;gap:6px;background:var(--card);border:1px solid var(--bdr);border-radius:22px;padding:8px 16px;font-size:14px;color:var(--B);cursor:pointer;margin-bottom:14px;font-family:inherit;font-weight:700}
.toast{position:fixed;bottom:92px;left:16px;right:16px;background:var(--card2);color:var(--txt);padding:13px 16px;border-radius:15px;text-align:center;z-index:2000;font-size:14px;font-weight:600;animation:toastIn .25s ease;pointer-events:none;border:1px solid var(--bdr);border-left:4px solid var(--Y)}
@keyframes toastIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
.loading{text-align:center;padding:60px 0}
.spinner{width:36px;height:36px;border:3px solid var(--bdr);border-top-color:var(--B);border-radius:50%;animation:spin 1s linear infinite;margin:0 auto 14px}
@keyframes spin{to{transform:rotate(360deg)}}
.empty{text-align:center;padding:60px 20px;color:var(--gray);font-size:15px}

/* ═══ USER INFO MODAL ════════════════════════════════════ */
.uif-head{background:linear-gradient(135deg,#1a1d27,#1e2235);padding:24px 20px 20px;text-align:center;position:relative;border-bottom:1px solid var(--bdr)}
.uif-head::after{content:'';position:absolute;bottom:0;left:0;right:0;height:2px;background:linear-gradient(90deg,var(--B),var(--Y))}
.uif-av{width:80px;height:80px;border-radius:50%;background:linear-gradient(135deg,var(--B),var(--B2));display:flex;align-items:center;justify-content:center;font-size:32px;color:#fff;border:3px solid rgba(255,255,255,.15);margin:0 auto 14px;overflow:hidden}
.uif-av img{width:100%;height:100%;object-fit:cover}
.uif-nm{font-size:19px;font-weight:800;color:#fff;margin-bottom:4px}
.uif-h{font-size:13px;color:rgba(255,255,255,.45);font-family:monospace;margin-bottom:10px}
.uif-bgs{display:flex;gap:6px;justify-content:center;flex-wrap:wrap}
.uif-body{padding:16px 20px;background:var(--card)}
.uif-row{padding:13px 0;border-bottom:.5px solid var(--bdr);display:flex;justify-content:space-between;align-items:center}
.uif-row:last-child{border-bottom:none}
.uif-lbl{font-size:13px;color:var(--gray);font-weight:600}
.uif-val{font-size:13px;color:var(--txt);font-weight:700}
.uif-val.mono{font-family:monospace;color:var(--B)}

/* ═══ WELCOME ════════════════════════════════════════════ */
.wlc-wrap{min-height:calc(100vh - 80px);display:flex;align-items:center;justify-content:center}
.wlc-card{background:linear-gradient(135deg,#1a1d27,#1e2235);border-radius:32px;padding:36px 22px;text-align:center;width:100%;position:relative;overflow:hidden;box-shadow:0 20px 60px rgba(0,0,0,.6);border:1px solid var(--bdr)}
.wlc-card::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,var(--B),var(--ton),var(--Y))}
.wlc-t{font-size:26px;font-weight:800;color:#fff;margin:18px 0 10px}.wlc-t b{color:var(--Y)}
.wlc-s{font-size:13px;color:rgba(255,255,255,.35);line-height:1.7;margin-bottom:26px}
.wlc-btns{display:flex;gap:10px;justify-content:center;flex-wrap:wrap}
.wlc-btn{background:var(--B);border:none;border-radius:999px;color:#fff;padding:14px 26px;font-size:15px;font-weight:800;cursor:pointer;font-family:inherit;transition:all .2s}
.wlc-btn:active{opacity:.85;transform:scale(.97)}
.wlc-btn.sec{background:rgba(255,255,255,.08);color:#fff}
.wlc-btn.ton{background:linear-gradient(135deg,var(--ton),var(--ton2))}
</style>
</head>
<body>

<!-- SPLASH -->
<div id="splash">
  <div class="sp-ring">
    <svg width="60" height="60" viewBox="0 0 60 60" fill="none">
      <circle cx="30" cy="30" r="26" fill="rgba(255,255,255,.1)"/>
      <polygon points="30,10 34,22 47,22 37,30 41,43 30,35 19,43 23,30 13,22 26,22" fill="#FFD600"/>
    </svg>
  </div>
  <div class="sp-t">SoNs <b>Store</b></div>
  <div class="sp-s">متجر الحسابات المميزة + شحن الألعاب</div>
  <div class="sp-track"><div class="sp-bar"></div></div>
</div>

<!-- TOPBAR -->
<div class="topbar">
  <div class="tb-brand">
    <div class="tb-logo">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="white">
        <polygon points="12,2 15.09,8.26 22,9.27 17,14.14 18.18,21.02 12,17.77 5.82,21.02 7,14.14 2,9.27 8.91,8.26"/>
      </svg>
    </div>
    <div class="tb-name">SoNs <b>◆</b></div>
  </div>
  <div style="display:flex;gap:8px;align-items:center">
    <div id="ton-connect-btn-wrap"></div>
    <button class="lang-btn" id="langBtn" onclick="toggleLang()">EN</button>
  </div>
</div>

<!-- APP -->
<div id="app"></div>

<!-- ═══ TAB BAR ═════════════════════════════════════════ -->
<nav class="tab-bar">
  <button class="tab-btn" id="tab-store" onclick="switchTab('store')">
    <div class="tab-pill">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M6 2L3 6v14a2 2 0 002 2h14a2 2 0 002-2V6l-3-4z"/><line x1="3" y1="6" x2="21" y2="6"/>
        <path d="M16 10a4 4 0 01-8 0"/>
      </svg>
    </div>
    <span class="tab-lbl" data-ar="المتجر" data-en="Store">المتجر</span>
  </button>
  <button class="tab-btn" id="tab-games" onclick="switchTab('games')">
    <div class="tab-pill">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect x="2" y="6" width="20" height="12" rx="3"/>
        <path d="M6 12h4M8 10v4M15 11h2M17 13h-2"/>
      </svg>
    </div>
    <span class="tab-lbl" data-ar="الألعاب" data-en="Games">الألعاب</span>
  </button>
  <button class="tab-btn" id="tab-purchases" onclick="switchTab('purchases')">
    <div class="tab-pill">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2"/>
        <rect x="9" y="3" width="6" height="4" rx="1"/>
        <path d="M9 12h6M9 16h4"/>
      </svg>
    </div>
    <span class="tab-lbl" data-ar="طلباتي" data-en="Orders">طلباتي</span>
  </button>
  <button class="tab-btn" id="tab-profile" onclick="switchTab('profile')">
    <div class="tab-pill">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/><circle cx="12" cy="7" r="4"/>
      </svg>
    </div>
    <span class="tab-lbl" data-ar="ملفي" data-en="Profile">ملفي</span>
  </button>
  <button class="tab-btn" id="tab-admin" onclick="switchTab('admin')" style="display:none">
    <div class="tab-pill">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="3"/>
        <path d="M19.07 4.93l-1.41 1.41M4.93 4.93l1.41 1.41M12 2v2m0 18v-2m7.07-2.93l-1.41-1.41M4.93 19.07l1.41-1.41M22 12h-2M4 12H2"/>
      </svg>
    </div>
    <span class="tab-lbl" data-ar="أدمن" data-en="Admin">أدمن</span>
  </button>
</nav>

<!-- DEPOSIT MODAL -->
<div id="depositModal" class="modal" onclick="if(event.target===this)cModal('depositModal')">
  <div class="modal-box">
    <div class="m-handle"></div>
    <div class="m-head"><h3>⭐ شحن الرصيد</h3><button class="m-close" onclick="cModal('depositModal')">✕</button></div>
    <div class="m-body">
      <div class="pay-tabs">
        <button class="pay-tab active" id="ptab-stars" onclick="setPayTab('stars')">⭐ Stars</button>
        <button class="pay-tab ton-tab" id="ptab-ton" onclick="setPayTab('ton')">💎 TON</button>
        <button class="pay-tab" id="ptab-vf" onclick="setPayTab('vf')" style="color:#e30613">📱 فودافون</button>
      </div>
      <!-- Stars -->
      <div id="sec-stars">
        <div class="amt-grid">
          <div class="amt-btn" onclick="setA(50)">50 ⭐</div><div class="amt-btn" onclick="setA(100)">100 ⭐</div>
          <div class="amt-btn" onclick="setA(250)">250 ⭐</div><div class="amt-btn" onclick="setA(500)">500 ⭐</div>
          <div class="amt-btn" onclick="setA(1000)">1000 ⭐</div><div class="amt-btn" onclick="setA(2500)">2500 ⭐</div>
        </div>
        <input type="number" id="starsAmt" class="inp" placeholder="أو أدخل مبلغ مخصص..." min="1">
        <button class="sbtn" onclick="depositStars()">⭐ شحن بنجوم تيليجرام</button>
      </div>
      <!-- TON -->
      <div id="sec-ton" style="display:none">
        <div style="background:rgba(0,152,234,.08);border:1px solid rgba(0,152,234,.2);border-radius:14px;padding:12px;margin-bottom:14px;font-size:13px;color:var(--gray);text-align:center">
          💎 ادفع بـ TON مباشرة من محفظتك<br>
          <span style="font-size:11px">سيُضاف رصيدك تلقائياً بعد التأكيد</span>
        </div>
        <div class="amt-grid">
          <div class="amt-btn" onclick="setTA(100)">100 ⭐</div><div class="amt-btn" onclick="setTA(250)">250 ⭐</div>
          <div class="amt-btn" onclick="setTA(500)">500 ⭐</div><div class="amt-btn" onclick="setTA(1000)">1000 ⭐</div>
          <div class="amt-btn" onclick="setTA(2500)">2500 ⭐</div><div class="amt-btn" onclick="setTA(5000)">5000 ⭐</div>
        </div>
        <input type="number" id="tonAmt" class="inp" placeholder="عدد النجوم المراد شحنها..." min="1">
        <div id="ton-rate-info" style="font-size:12px;color:var(--ton);text-align:center;margin-bottom:12px;font-weight:700"></div>
        <div id="ton-balance-display" style="background:rgba(0,152,234,.08);border:1px solid rgba(0,152,234,.2);border-radius:12px;padding:10px;margin-bottom:10px;text-align:center;display:none">
        <div style="font-size:11px;color:var(--gray);margin-bottom:3px">رصيد محفظة TON</div>
        <div id="ton-wallet-balance-val" style="font-size:20px;font-weight:800;color:var(--ton)">— TON</div>
      </div>
      <div id="ton-wallet-status" style="margin-bottom:12px;text-align:center;font-size:13px;color:var(--gray)">جاري التحقق من المحفظة...</div>
        <button class="sbtn ton" onclick="initTonPayment()">💎 الدفع بـ TON</button>
        <!-- VODAFONE CASH section (injected by JS) -->
        <div id="ton-pay-result" style="display:none;margin-top:14px">
          <div class="ton-pay-info">
            <div style="font-weight:700;margin-bottom:8px;color:var(--txt)">📋 تفاصيل الدفع</div>
            <div>💎 المبلغ: <b id="ton-pay-amount" style="color:var(--ton)"></b></div>
            <div style="margin-top:6px">⭐ النجوم: <b id="ton-pay-stars" style="color:var(--Y)"></b></div>
            <div class="ton-addr-box" id="ton-pay-addr" onclick="copyTonAddr()">انقر للنسخ</div>
            <div style="font-size:11px;color:var(--gray)">أرسل المبلغ الدقيق مع المذكرة أدناه</div>
            <div class="ton-addr-box" id="ton-pay-memo" style="color:var(--Y);margin-top:6px;font-size:12px" onclick="copyTonMemo()">معرف الدفع</div>
          </div>
          <button class="sbtn" style="margin-top:10px;background:var(--card2);color:var(--txt)" onclick="checkTonStatus()">🔍 تحقق من الدفع</button>
        </div>
      </div>
      <!-- Vodafone Cash -->
      <div id="sec-vf" style="display:none">
        <div style="background:rgba(227,6,19,.07);border:1px solid rgba(227,6,19,.2);border-radius:14px;padding:14px;margin-bottom:14px;text-align:center">
          <div style="font-size:36px;margin-bottom:8px">📱</div>
          <div style="font-weight:800;color:#e30613;font-size:15px;margin-bottom:4px">فودافون كاش</div>
          <div style="font-size:12px;color:var(--gray)">أدخل عدد النجوم التي تريد شحنها</div>
        </div>
        <div class="amt-grid">
          <div class="amt-btn" onclick="setVF(100)">100 ⭐</div>
          <div class="amt-btn" onclick="setVF(250)">250 ⭐</div>
          <div class="amt-btn" onclick="setVF(500)">500 ⭐</div>
          <div class="amt-btn" onclick="setVF(1000)">1000 ⭐</div>
          <div class="amt-btn" onclick="setVF(2500)">2500 ⭐</div>
          <div class="amt-btn" onclick="setVF(5000)">5000 ⭐</div>
        </div>
        <input type="number" id="vfAmt" class="inp" placeholder="أو أدخل عدد مخصص..." min="1">
        <button class="sbtn" style="background:linear-gradient(135deg,#e30613,#b00010);margin-bottom:10px" onclick="initVodafoneCash()">📱 شراء بفودافون كاش</button>
        <div id="vf-result" style="display:none;margin-top:12px">
          <div style="background:rgba(227,6,19,.07);border:1.5px solid rgba(227,6,19,.3);border-radius:16px;padding:16px;text-align:center">
            <div style="font-size:13px;color:var(--gray);margin-bottom:10px">حوّل المبلغ لرقم فودافون كاش التالي:</div>
            <div style="font-size:28px;font-weight:900;color:#e30613;margin-bottom:8px;font-family:monospace;cursor:pointer" onclick="navigator.clipboard.writeText('01093075185');toast('✓ تم نسخ الرقم')" id="vf-number">01093075185</div>
            <div style="font-size:11px;color:var(--gray);margin-bottom:12px">انقر الرقم لنسخه</div>
            <div id="vf-details" style="background:var(--card2);border-radius:12px;padding:10px;font-size:13px;color:var(--txt);margin-bottom:10px;line-height:1.8"></div>
            <div style="font-size:11px;color:var(--warn);font-weight:700">⚠️ بعد التحويل، أرسل صورة الإيصال للدعم</div>
            <div style="font-size:10px;color:var(--gray);margin-top:4px">سيتم إضافة رصيدك خلال ساعة من التأكيد</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- PURCHASE SUCCESS MODAL -->
<div id="purchaseModal" class="modal" onclick="if(event.target===this)cModal('purchaseModal')">
  <div class="modal-box">
    <div class="m-handle"></div>
    <div class="m-head"><h3 id="pTitle">✅ تم الشراء</h3><button class="m-close" onclick="cModal('purchaseModal')">✕</button></div>
    <div class="m-body" id="pBody"></div>
  </div>
</div>

<!-- USER INFO MODAL -->
<div id="accountModal" class="modal" onclick="if(event.target===this)cModal('accountModal')">
  <div class="modal-box" style="background:transparent;border-radius:28px 28px 0 0;overflow:hidden">
    <div class="m-handle" style="background:rgba(255,255,255,.15);margin-bottom:0"></div>
    <div id="accBody"></div>
  </div>
</div>

<!-- REDEEM MODAL -->
<div id="redeemModal" class="modal" onclick="if(event.target===this)cModal('redeemModal')">
  <div class="modal-box">
    <div class="m-handle"></div>
    <div class="m-head"><h3>🎟️ كود الشحن</h3><button class="m-close" onclick="cModal('redeemModal')">✕</button></div>
    <div class="m-body">
      <input type="text" id="redeemInp" class="inp" placeholder="أدخل الكود..." style="text-transform:uppercase">
      <button class="sbtn" onclick="submitRedeem()">تفعيل الكود</button>
    </div>
  </div>
</div>

<!-- GAME ORDER MODAL -->
<div id="gameOrderModal" class="modal" onclick="if(event.target===this)cModal('gameOrderModal')">
  <div class="modal-box">
    <div class="m-handle"></div>
    <div class="m-head"><h3 id="gameOrderTitle">🎮 طلب شحن</h3><button class="m-close" onclick="cModal('gameOrderModal')">✕</button></div>
    <div class="m-body" id="gameOrderBody"></div>
  </div>
</div>

<script>
// ═══ TELEGRAM USER ════════════════════════════════════════
const tg = window.Telegram?.WebApp;
if(tg){ tg.expand(); tg.ready(); }

function getTGUser(){
  const r=tg?.initDataUnsafe?.user||{};
  return {id:r.id||null,first_name:r.first_name||'',last_name:r.last_name||'',
          username:r.username||'',language_code:r.language_code||'ar',
          is_premium:r.is_premium||false,photo_url:r.photo_url||'',
          full_name:[r.first_name,r.last_name].filter(Boolean).join(' ')};
}
const user    = getTGUser();
const OWNER_ID= 6285783725;
let isAdmin   = false;

// Check admin status from server
async function checkAdminStatus(){
  if(!user.id) return;
  try{
    const d = await f(`/api/check_admin?user_id=${user.id}`);
    isAdmin = d.is_admin || false;
    if(isAdmin){
      document.getElementById('tab-admin').style.display='';
    }
  }catch(e){}
}
checkAdminStatus();

// ═══ TON CONNECT ══════════════════════════════════════════
let tonConnectUI = null;
let connectedWallet = null;
let currentTonPaymentId = null;

async function initTonConnect(){
  try{
    // ملاحظة: تحتاج إلى رفع tonconnect-manifest.json على خادمك
    tonConnectUI = new TON_CONNECT_UI.TonConnectUI({
      manifestUrl: window.location.origin + '/tonconnect-manifest.json',
      buttonRootId: 'ton-connect-btn-wrap'
    });
    tonConnectUI.onStatusChange(wallet => {
      connectedWallet = wallet;
      updateTonWalletUI();
      refreshTonBalMain();
    });
  }catch(e){
    console.warn('TON Connect init error:', e);
  }
}

function updateTonWalletUI(){
  const strip = document.getElementById('ton-wallet-strip-main');
  const status = document.getElementById('ton-wallet-status');
  const balDisplay = document.getElementById('ton-balance-display');
  const balVal = document.getElementById('ton-wallet-balance-val');
  if(connectedWallet){
    const addr = connectedWallet.account.address;
    const short = addr.slice(0,6)+'...'+addr.slice(-4);
    if(strip){
      strip.querySelector('.ton-wallet-addr').textContent = short;
      strip.querySelector('.ton-wallet-label').textContent = '💎 محفظة TON متصلة';
    }
    if(status) status.innerHTML = `<span style="color:var(--success)">✓ محفظة متصلة: ${short}</span>`;
    // Show TON balance
    if(balDisplay) balDisplay.style.display='';
    const rawBal = connectedWallet?.account?.balance;
    if(rawBal && balVal){
      const tonBal = parseFloat((parseInt(rawBal)/1e9).toFixed(3));
      cachedTonBalance = tonBal;
      balVal.textContent = tonBal + ' TON';
    } else if(balVal){
      balVal.textContent = '— TON';
    }
  } else {
    if(strip){
      strip.querySelector('.ton-wallet-addr').textContent = 'اضغط للربط';
      strip.querySelector('.ton-wallet-label').textContent = '💎 ربط محفظة TON';
    }
    if(status) status.innerHTML = `<span style="color:var(--warn)">⚠️ يرجى ربط محفظة TON أولاً</span>`;
    if(balDisplay) balDisplay.style.display='none';
  }
}

async function connectTonWallet(){
  if(!tonConnectUI) return toast('❌ خدمة TON غير متاحة');
  try{
    await tonConnectUI.openModal();
  }catch(e){ toast('❌ خطأ في فتح TON: '+e.message); }
}

// ═══ LANGUAGE ═════════════════════════════════════════════
const TXT={
  ar:{store:'المتجر',orders:'طلباتي',profile:'ملفي',admin:'أدمن',games:'الألعاب',
      open:'✨ افتح المتجر',myp:'👤 ملفي',stars:'نجمة',cats:'الفئات',
      buy:'شراء',in:'متوفر',out:'نفد',back:'رجوع',
      myord:'مشترياتي',noord:'لا توجد طلبات بعد',recharge:'⭐ شحن',
      spent:'إجمالي الإنفاق',orders_c:'عدد الطلبات',apanel:'لوحة التحكم',
      revenue:'الإيرادات',sales:'المبيعات',users:'المستخدمين',
      madm:'إدارة الحسابات',rcodes:'أكواد الشحن',allord:'كل الطلبات',
      bcast:'إشعارات',addacc:'➕ إضافة حساب',addcode:'➕ كود جديد',
      sendall:'📤 إرسال للجميع',insuf:'رصيدك غير كافٍ! شحن الآن؟',
      pok:'✅ تم الشراء بنجاح!',rem:'رصيدك المتبقي',
      copied:'تم النسخ ✓',addstars:'عدد النجوم:',
      writemsg:'اكتب رسالتك...',redeem:'🎟️ كود شحن',yes:'نعم',no:'لا'},
  en:{store:'Store',orders:'Orders',profile:'Profile',admin:'Admin',games:'Games',
      open:'✨ Open Store',myp:'👤 Profile',stars:'Stars',cats:'Categories',
      buy:'Buy',in:'In Stock',out:'Out',back:'Back',
      myord:'My Orders',noord:'No orders yet',recharge:'⭐ Recharge',
      spent:'Total Spent',orders_c:'Total Orders',apanel:'Admin Panel',
      revenue:'Revenue',sales:'Sales',users:'Users',
      madm:'Manage Accounts',rcodes:'Redeem Codes',allord:'All Orders',
      bcast:'Broadcast',addacc:'➕ Add Account',addcode:'➕ Add Code',
      sendall:'📤 Send All',insuf:'Insufficient balance! Recharge?',
      pok:'✅ Purchase Successful!',rem:'Remaining Balance',
      copied:'Copied ✓',addstars:'Stars to add:',
      writemsg:'Write your message...',redeem:'🎟️ Redeem',yes:'Yes',no:'No'}
};
let lang = user.language_code==='en'?'en':'ar';
const t = k => TXT[lang][k]||k;

function toggleLang(){
  lang=lang==='ar'?'en':'ar';
  document.getElementById('langBtn').textContent=lang==='ar'?'EN':'عر';
  const root=document.getElementById('root');
  root.lang=lang; root.dir=lang==='ar'?'rtl':'ltr';
  document.querySelectorAll('.tab-lbl').forEach(el=>{ el.textContent=el.dataset[lang]; });
  switchTab(curTab);
}

// ═══ STATE ═════════════════════════════════════════════════
let curTab=null, curCat=null, curAdmin=null, cachedBal=0;
let curGame=null, selectedPkg=null;
const TON_GAME_RATE = 0.02;  // 1 star = 0.02 TON
const TON_STORE_WALLET = 'UQA2NRUfGsW4IFP8yprXDqkthwa4-NstFwFn8k54-WiMBy2M';
let cachedTonBalance = null;

// Get TON balance from connected wallet
function getTonBalance(){
  if(!connectedWallet) return null;
  const bal = connectedWallet?.account?.balance;
  if(bal) cachedTonBalance = parseFloat((parseInt(bal)/1e9).toFixed(3));
  return cachedTonBalance;
}

// Show TON balance in game order modal
async function updateGameTonBal(){
  const el = document.getElementById('ton-game-bal');
  if(!el) return;
  if(!connectedWallet){
    el.textContent = 'غير متصل';
    el.style.color = 'var(--warn)';
    return;
  }
  const bal = getTonBalance();
  if(bal !== null){
    el.textContent = bal + ' TON';
    el.style.color = 'var(--ton)';
  } else {
    el.textContent = 'محفظة متصلة';
    el.style.color = 'var(--ton)';
  }
}
const params=new URLSearchParams(location.search);
const initTab=params.get('tab')||'store';

window.addEventListener('load',()=>{
  setTimeout(()=>document.getElementById('splash').classList.add('out'),1400);
  initTonConnect();
});

async function refreshBal(){ if(!user.id)return; try{const d=await f(`/api/balance/${user.id}`);cachedBal=d.balance||0;}catch(e){} }
refreshBal(); setInterval(refreshBal,12000);

// ═══ ICONS ════════════════════════════════════════════════
const ICONS={
  google:`<svg viewBox="0 0 48 48"><path fill="#4285F4" d="M46.1 24.5c0-1.5-.1-3-.4-4.4H24v8.3h12.4c-.5 3-2.2 5.5-4.6 7.1v5.9h7.5c4.4-4 6.8-9.9 6.8-16.9z"/><path fill="#34A853" d="M24 47c6.3 0 11.5-2.1 15.4-5.6l-7.5-5.9c-2.1 1.4-4.8 2.1-7.9 2.1-6 0-11.2-4.1-13-9.6H3.3v6.1C7.1 42.2 15 47 24 47z"/><path fill="#FBBC05" d="M11 28.9c-.5-1.5-.7-3.1-.7-4.7s.2-3.2.7-4.7v-6.1H3.3C1.7 16.5.8 20.1.8 24s.9 7.5 2.5 10.5L11 28.9z"/><path fill="#EA4335" d="M24 9.5c3.4 0 6.5 1.2 9 3.5l6.7-6.7C35.5 2.6 30.3.5 24 .5 15 .5 7.1 5.3 3.3 12.9L11 19c1.8-5.5 7-9.5 13-9.5z"/></svg>`,
  netflix:`<svg viewBox="0 0 48 48"><rect width="48" height="48" rx="8" fill="#E50914"/><path fill="white" d="M13 8h6l7 20V8h6v32h-6L19 20v20h-6z"/></svg>`,
  spotify:`<svg viewBox="0 0 48 48"><circle cx="24" cy="24" r="22" fill="#1ED760"/><path fill="white" d="M33 31.2c-5-3-12.5-3.8-17.8-2.1-.7.2-1.4-.2-1.5-.9-.2-.7.2-1.4.9-1.5 5.7-1.8 13.7-1.1 19.5 2.4.6.4.8 1.2.4 1.8-.4.7-1.1.6-1.5.3zM35.5 25.8c-6-3.6-15.4-4.8-22.6-2.6-.8.3-1.7-.2-2-1-.3-.8.2-1.7 1-2 8.1-2.4 18.2.4 25.3 4.6.7.4 1 1.3.5 2-.4.7-1.5.7-2.2 0zM11.6 15.5c-1 .3-2-.3-2.3-1.3-.3-1 .3-2 1.3-2.3 9.5-2.8 19.8.3 27.3 4.8.9.5 1.1 1.6.6 2.5s-1.6 1.1-2.6.6C29 15 20 13 11.6 15.5z"/></svg>`,
};
const GAME_IMGS={
  pubg:`<svg viewBox="0 0 80 80" fill="none" xmlns="http://www.w3.org/2000/svg">
    <rect width="80" height="80" rx="20" fill="#1a1200"/>
    <rect width="80" height="80" rx="20" fill="url(#pg)"/>
    <defs>
      <linearGradient id="pg" x1="0" y1="0" x2="80" y2="80" gradientUnits="userSpaceOnUse">
        <stop stop-color="#f5a623"/>
        <stop offset="1" stop-color="#c47d0a"/>
      </linearGradient>
    </defs>
    <!-- helmet shape -->
    <ellipse cx="40" cy="34" rx="18" ry="16" fill="#1a1200" opacity=".9"/>
    <rect x="22" y="38" width="36" height="7" rx="3" fill="#1a1200" opacity=".9"/>
    <ellipse cx="40" cy="34" rx="15" ry="13" fill="#f5a623" opacity=".25"/>
    <!-- visor -->
    <path d="M26 40 Q40 50 54 40" stroke="#f5a623" stroke-width="2.5" fill="none" stroke-linecap="round"/>
    <!-- scope circles -->
    <circle cx="40" cy="58" r="7" stroke="#f5a623" stroke-width="2" fill="none"/>
    <circle cx="40" cy="58" r="3" fill="#f5a623" opacity=".6"/>
    <line x1="40" y1="49" x2="40" y2="51" stroke="#f5a623" stroke-width="2"/>
    <line x1="40" y1="65" x2="40" y2="67" stroke="#f5a623" stroke-width="2"/>
    <line x1="31" y1="58" x2="33" y2="58" stroke="#f5a623" stroke-width="2"/>
    <line x1="47" y1="58" x2="49" y2="58" stroke="#f5a623" stroke-width="2"/>
    <!-- text PUBG -->
    <text x="40" y="30" text-anchor="middle" fill="#f5a623" font-size="10" font-weight="900" font-family="Arial" letter-spacing="1">PUBG</text>
  </svg>`,

  freefire:`<svg viewBox="0 0 80 80" fill="none" xmlns="http://www.w3.org/2000/svg">
    <defs>
      <linearGradient id="ffg" x1="0" y1="0" x2="80" y2="80" gradientUnits="userSpaceOnUse">
        <stop stop-color="#ff2200"/>
        <stop offset="1" stop-color="#ff6600"/>
      </linearGradient>
      <radialGradient id="ffr" cx="40" cy="55" r="25" gradientUnits="userSpaceOnUse">
        <stop stop-color="#ffcc00" stop-opacity=".7"/>
        <stop offset="1" stop-color="#ff4400" stop-opacity="0"/>
      </radialGradient>
    </defs>
    <rect width="80" height="80" rx="20" fill="#1a0500"/>
    <circle cx="40" cy="55" r="24" fill="url(#ffr)"/>
    <!-- flame 1 -->
    <path d="M40 18 C36 28 28 32 30 44 C32 52 40 56 40 56 C40 56 48 52 50 44 C52 32 44 28 40 18Z" fill="url(#ffg)" opacity=".9"/>
    <!-- flame 2 -->
    <path d="M40 28 C38 34 33 37 34 44 C35 49 40 52 40 52 C40 52 45 49 46 44 C47 37 42 34 40 28Z" fill="#ffcc00" opacity=".85"/>
    <!-- flame 3 core -->
    <path d="M40 36 C39 39 37 41 38 44 C39 47 40 48 40 48 C40 48 41 47 42 44 C43 41 41 39 40 36Z" fill="#fff" opacity=".9"/>
    <!-- FF text -->
    <text x="40" y="70" text-anchor="middle" fill="#ff4400" font-size="9" font-weight="900" font-family="Arial" letter-spacing="2">FREE FIRE</text>
  </svg>`,

  clashofclans:`<svg viewBox="0 0 80 80" fill="none" xmlns="http://www.w3.org/2000/svg">
    <defs>
      <linearGradient id="cg" x1="0" y1="0" x2="80" y2="80" gradientUnits="userSpaceOnUse">
        <stop stop-color="#4a90e2"/>
        <stop offset="1" stop-color="#2255aa"/>
      </linearGradient>
    </defs>
    <rect width="80" height="80" rx="20" fill="#0a1a33"/>
    <!-- castle base -->
    <rect x="20" y="44" width="40" height="22" rx="4" fill="url(#cg)" opacity=".9"/>
    <!-- castle gate -->
    <path d="M34 66 L34 54 Q40 48 46 54 L46 66Z" fill="#0a1a33"/>
    <!-- towers -->
    <rect x="16" y="32" width="14" height="34" rx="3" fill="#4a90e2"/>
    <rect x="50" y="32" width="14" height="34" rx="3" fill="#4a90e2"/>
    <!-- tower tops -->
    <rect x="14" y="26" width="18" height="10" rx="2" fill="#5aa0f2"/>
    <rect x="48" y="26" width="18" height="10" rx="2" fill="#5aa0f2"/>
    <!-- battlements left -->
    <rect x="14" y="22" width="4" height="7" rx="1" fill="#5aa0f2"/>
    <rect x="20" y="22" width="4" height="7" rx="1" fill="#5aa0f2"/>
    <rect x="26" y="22" width="4" height="7" rx="1" fill="#5aa0f2"/>
    <!-- battlements right -->
    <rect x="48" y="22" width="4" height="7" rx="1" fill="#5aa0f2"/>
    <rect x="54" y="22" width="4" height="7" rx="1" fill="#5aa0f2"/>
    <rect x="60" y="22" width="4" height="7" rx="1" fill="#5aa0f2"/>
    <!-- main tower top -->
    <rect x="27" y="18" width="26" height="10" rx="2" fill="#2255aa"/>
    <rect x="25" y="13" width="6" height="8" rx="1" fill="#4a90e2"/>
    <rect x="33" y="13" width="6" height="8" rx="1" fill="#4a90e2"/>
    <rect x="41" y="13" width="6" height="8" rx="1" fill="#4a90e2"/>
    <rect x="49" y="13" width="6" height="8" rx="1" fill="#4a90e2"/>
    <!-- flag -->
    <line x1="40" y1="6" x2="40" y2="18" stroke="#ffcc00" stroke-width="2"/>
    <polygon points="40,7 48,11 40,15" fill="#ffcc00"/>
  </svg>`,

  mlbb:`<svg viewBox="0 0 80 80" fill="none" xmlns="http://www.w3.org/2000/svg">
    <defs>
      <linearGradient id="mg" x1="0" y1="0" x2="80" y2="80" gradientUnits="userSpaceOnUse">
        <stop stop-color="#9b59b6"/>
        <stop offset="1" stop-color="#6c3483"/>
      </linearGradient>
      <linearGradient id="mg2" x1="40" y1="10" x2="40" y2="70" gradientUnits="userSpaceOnUse">
        <stop stop-color="#e8aaff"/>
        <stop offset="1" stop-color="#9b59b6"/>
      </linearGradient>
    </defs>
    <rect width="80" height="80" rx="20" fill="#0d0520"/>
    <!-- shield outer -->
    <path d="M40 12 L60 22 L60 48 Q60 62 40 70 Q20 62 20 48 L20 22 Z" fill="url(#mg)" opacity=".85"/>
    <!-- shield inner -->
    <path d="M40 19 L54 27 L54 46 Q54 57 40 64 Q26 57 26 46 L26 27 Z" fill="#0d0520" opacity=".6"/>
    <!-- sword -->
    <line x1="40" y1="22" x2="40" y2="58" stroke="url(#mg2)" stroke-width="3" stroke-linecap="round"/>
    <!-- crossguard -->
    <line x1="28" y1="36" x2="52" y2="36" stroke="#e8aaff" stroke-width="3" stroke-linecap="round"/>
    <!-- diamond pommel -->
    <polygon points="40,55 44,58 40,62 36,58" fill="#e8aaff"/>
    <!-- stars -->
    <circle cx="28" cy="24" r="2" fill="#e8aaff" opacity=".6"/>
    <circle cx="52" cy="24" r="2" fill="#e8aaff" opacity=".6"/>
    <circle cx="62" cy="44" r="1.5" fill="#e8aaff" opacity=".5"/>
    <circle cx="18" cy="44" r="1.5" fill="#e8aaff" opacity=".5"/>
  </svg>`,
};

// Generic game image for unknown games
const getGameImg = k => {
  if(GAME_IMGS[k]) return GAME_IMGS[k];
  return `<svg viewBox="0 0 80 80" fill="none"><rect width="80" height="80" rx="20" fill="#1a1d27"/>
    <rect x="16" y="24" width="48" height="32" rx="8" fill="#2d3250"/>
    <path d="M26 40h12M32 34v12M50 39h4M46 43h4" stroke="#7c84a0" stroke-width="2.5" stroke-linecap="round"/>
    <text x="40" y="68" text-anchor="middle" fill="#7c84a0" font-size="9" font-family="Arial">${k.slice(0,6).toUpperCase()}</text>
  </svg>`;
};

const getCatSVG = c => ICONS[c]||`<svg viewBox="0 0 48 48"><rect width="48" height="48" rx="14" fill="#22263a"/></svg>`;
const getGameIcon = k => `<span style="font-size:36px">${{pubg:'🎯',freefire:'🔥',clashofclans:'🏰',mlbb:'⚔️'}[k]||'🎮'}</span>`;

// ═══ USER CARD ════════════════════════════════════════════
function buildUID(bal){
  const av=user.photo_url?`<img src="${user.photo_url}" alt="">`:( user.first_name?.charAt(0).toUpperCase()||'👤');
  const bgs=[];
  if(user.is_premium) bgs.push('<span class="badge bp">⭐ Premium</span>');
  if(isAdmin)         bgs.push('<span class="badge ba">🔧 Admin</span>');
  if(user.language_code) bgs.push(`<span class="badge bl">${user.language_code.toUpperCase()}</span>`);
  const handle=user.username?`@${user.username}`:user.id?`ID: ${user.id}`:'';
  return `
  <div class="uid-card" onclick="showAccInfo()">
    <div class="uid-av-wrap">
      <div class="uid-av">${av}</div><div class="uid-dot"></div>
    </div>
    <div class="uid-info">
      <div class="uid-name">${e(user.full_name||user.first_name||'مستخدم')}</div>
      <div class="uid-handle">${e(handle)}</div>
      <div class="uid-badges">${bgs.join('')}</div>
    </div>
    <div class="uid-arr">›</div>
  </div>
  <div id="ton-wallet-strip-main" class="ton-wallet-strip" onclick="connectTonWallet()">
    <div class="ton-ic">
      <svg width="22" height="22" viewBox="0 0 56 56" fill="none">
        <path d="M28 56C43.464 56 56 43.464 56 28C56 12.536 43.464 0 28 0C12.536 0 0 12.536 0 28C0 43.464 12.536 56 28 56Z" fill="#0098EA"/>
        <path d="M37.5603 15.6277H18.4386C14.9228 15.6277 12.6944 19.3818 14.4632 22.4301L26.2644 42.8285C27.0345 44.1537 28.9644 44.1537 29.7345 42.8285L41.5357 22.4301C43.3056 19.3818 41.0772 15.6277 37.5603 15.6277ZM26.2335 39.2285L23.7089 34.8451L17.9203 24.3277H26.2335V39.2285ZM38.0786 24.3277L32.2901 34.8451L29.7655 39.2285V24.3277H38.0786Z" fill="white"/>
      </svg>
    </div>
    <div class="ton-wallet-info">
      <div class="ton-wallet-label">💎 ربط محفظة TON</div>
      <div class="ton-wallet-addr">اضغط للربط</div>
    </div>
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--gray)" stroke-width="2"><path d="M9 18l6-6-6-6"/></svg>
  </div>
  <!-- ══ BALANCE CIRCLES ROW ══ -->
  <div class="bal-circles-row">
    <!-- Stars balance -->
    <div class="bal-circle-wrap">
      <div class="bal-circle bal-circle-stars">
        <svg viewBox="0 0 36 36" class="bal-ring-svg">
          <circle cx="18" cy="18" r="15.5" fill="none" stroke="rgba(255,214,0,.15)" stroke-width="2.5"/>
          <circle cx="18" cy="18" r="15.5" fill="none" stroke="var(--Y)" stroke-width="2.5"
                  stroke-linecap="round" stroke-dasharray="97.4" stroke-dashoffset="20"
                  transform="rotate(-90 18 18)"/>
        </svg>
        <div class="bal-circle-inner">
          <svg width="22" height="22" viewBox="0 0 24 24">
            <polygon points="12,2 15.09,8.26 22,9.27 17,14.14 18.18,21.02 12,17.77 5.82,21.02 7,14.14 2,9.27 8.91,8.26" fill="#FFD600" stroke="#b8950a" stroke-width="1" stroke-linejoin="round"/>
          </svg>
          <div class="bal-circle-val">${bal??cachedBal}</div>
          <div class="bal-circle-lbl">${t('stars')}</div>
        </div>
      </div>
      <button class="bal-circle-add" onclick="showDeposit()">+</button>
    </div>
    <!-- TON balance -->
    <div class="bal-circle-wrap">
      <div class="bal-circle bal-circle-ton">
        <svg viewBox="0 0 36 36" class="bal-ring-svg">
          <circle cx="18" cy="18" r="15.5" fill="none" stroke="rgba(0,152,234,.15)" stroke-width="2.5"/>
          <circle cx="18" cy="18" r="15.5" fill="none" stroke="var(--ton)" stroke-width="2.5"
                  stroke-linecap="round" stroke-dasharray="97.4" stroke-dashoffset="40"
                  transform="rotate(-90 18 18)"/>
        </svg>
        <div class="bal-circle-inner">
          <svg width="20" height="20" viewBox="0 0 56 56" fill="none">
            <path d="M28 56C43.464 56 56 43.464 56 28C56 12.536 43.464 0 28 0C12.536 0 0 12.536 0 28C0 43.464 12.536 56 28 56Z" fill="#0098EA"/>
            <path d="M37.5603 15.6277H18.4386C14.9228 15.6277 12.6944 19.3818 14.4632 22.4301L26.2644 42.8285C27.0345 44.1537 28.9644 44.1537 29.7345 42.8285L41.5357 22.4301C43.3056 19.3818 41.0772 15.6277 37.5603 15.6277ZM26.2335 39.2285L23.7089 34.8451L17.9203 24.3277H26.2335V39.2285ZM38.0786 24.3277L32.2901 34.8451L29.7655 39.2285V24.3277H38.0786Z" fill="white"/>
          </svg>
          <div class="bal-circle-val" id="ton-bal-main" style="color:var(--ton)">—</div>
          <div class="bal-circle-lbl">TON</div>
        </div>
      </div>
      <button class="bal-circle-add" style="background:linear-gradient(135deg,var(--ton),var(--ton2));box-shadow:0 4px 14px rgba(0,152,234,.35)" onclick="showTonDeposit()">+</button>
    </div>
  </div>
  <!-- placeholder to keep compat with old code that reads ton-bal-main via bal-row -->
  <div class="bal-row" style="display:none">
    <div class="bal-box" style="border-color:rgba(0,152,234,.25)">
      <div class="bal-ic" style="background:linear-gradient(135deg,var(--ton),var(--ton2));box-shadow:0 4px 16px rgba(0,152,234,.25)">
        <svg width="26" height="26" viewBox="0 0 56 56" fill="none"><path d="M28 56C43.464 56 56 43.464 56 28C56 12.536 43.464 0 28 0C12.536 0 0 12.536 0 28C0 43.464 12.536 56 28 56Z" fill="#0098EA"/><path d="M37.5603 15.6277H18.4386C14.9228 15.6277 12.6944 19.3818 14.4632 22.4301L26.2644 42.8285C27.0345 44.1537 28.9644 44.1537 29.7345 42.8285L41.5357 22.4301C43.3056 19.3818 41.0772 15.6277 37.5603 15.6277ZM26.2335 39.2285L23.7089 34.8451L17.9203 24.3277H26.2335V39.2285ZM38.0786 24.3277L32.2901 34.8451L29.7655 39.2285V24.3277H38.0786Z" fill="white"/></svg>
      </div>
      <div>
        <div class="bal-n" style="color:var(--ton)">—</div>
        <div class="bal-l">TON</div>
      </div>
    </div>
    <button class="add-btn" style="display:none"></button>
  </div>`;
}

// ═══ WELCOME ══════════════════════════════════════════════
function loadWelcome(){
  document.getElementById('app').innerHTML=`
  <div class="wlc-wrap"><div class="wlc-card">
    <svg width="80" height="80" viewBox="0 0 80 80" fill="none">
      <rect width="80" height="80" rx="24" fill="rgba(59,130,246,.12)"/>
      <polygon points="40,16 46,31 62,32 50,44 53,60 40,53 27,60 30,44 18,32 34,31" fill="#FFD600"/>
    </svg>
    <div class="wlc-t">SoNs <b>Store</b></div>
    <div class="wlc-s">أفضل متجر للحسابات المميزة + شحن الألعاب<br>دفع بنجوم تيليجرام أو TON</div>
    <div class="wlc-btns">
      <button class="wlc-btn" onclick="switchTab('store')">${t('open')}</button>
      <button class="wlc-btn ton" onclick="switchTab('games')">🎮 شحن الألعاب</button>
      <button class="wlc-btn sec" onclick="switchTab('profile')">${t('myp')}</button>
    </div>
  </div></div>`;
}

// ═══ STORE ════════════════════════════════════════════════
async function loadStore(){
  const app=document.getElementById('app');
  if(curCat) return loadProducts(curCat);
  app.innerHTML=loading();
  try{
    const[store,bal]=await Promise.all([f('/api/store'),f(`/api/balance/${user.id}`)]);
    const cats=store.categories||{}, accs=store.accounts||[];
    let h=buildUID(bal.balance||0);
    h+=`<div class="sec-h"><div class="sec-t">${t('cats')}</div></div><div class="cat-grid">`;
    for(const[k,c] of Object.entries(cats)){
      const cnt=accs.filter(a=>a.category===k).length;
      h+=`<div class="cat-card" onclick="openCat('${k}')">
        <div class="cat-bg" style="background:${c.color||'#3b82f6'}"></div>
        <div class="cat-iw" style="background:${(c.color||'#3b82f6')}1a">${getCatSVG(k)}</div>
        <div class="cat-nm">${e(c.name)}</div>
        <div class="cat-cnt">${cnt} منتج</div>
      </div>`;
    }
    h+='</div>'; app.innerHTML=h;
    updateTonWalletUI();
    refreshTonBalMain();
  }catch(err){ app.innerHTML=empty('❌'); }
}
function openCat(c){ curCat=c; loadProducts(c); }

async function loadProducts(cat){
  const app=document.getElementById('app'); app.innerHTML=loading();
  try{
    const store=await f('/api/store');
    const accs=(store.accounts||[]).filter(a=>a.category===cat);
    const ci=(store.categories||{})[cat]||{};
    let h=`<button class="back-btn" onclick="goBack()">← ${t('back')}</button>
      <div class="sec-h"><div class="sec-t" style="display:flex;align-items:center;gap:8px">
        <span style="width:28px;height:28px;display:inline-flex">${getCatSVG(cat)}</span>${ci.name||cat}
      </div></div><div class="prod-list">`;
    if(accs.length){
      for(const a of accs){
        const ok=a.stock>0;
        h+=`<div class="prod-card">
          <div class="prod-ic" style="background:${(ci.color||'#3b82f6')}18">${getCatSVG(cat)}</div>
          <div class="prod-info">
            <div class="prod-nm">${e(a.name)}</div>
            <div class="prod-ds">${e(a.description||'')}</div>
            <div class="prod-meta">
              <span class="price-tag">⭐ ${Math.ceil(a.price*1.005)}</span>
              <span class="${ok?'stk-ok':'stk-no'}">${ok?t('in')+' ('+a.stock+')':t('out')}</span>
            </div>
          </div>
          <button class="buy-btn" ${!ok?'disabled':''} onclick="buyAcc('${a.id}',${a.price})">${t('buy')}</button>
        </div>`;
      }
    } else h+=empty('📭');
    h+='</div>'; app.innerHTML=h;
  }catch(err){ app.innerHTML=empty('❌'); }
}

async function buyAcc(aid,price){
  if(!user.id) return toast('❌ يجب تسجيل الدخول');
  try{
    const d=await fetch('/api/purchase',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({account_id:aid,user_id:user.id})}).then(r=>r.json());
    if(d.success){
      cachedBal=d.new_balance||0;
      document.getElementById('pTitle').textContent=t('pok');
      document.getElementById('pBody').innerHTML=`
        <p style="color:var(--gray);font-size:13px;margin-bottom:10px">${t('rem')}: ⭐ ${d.new_balance}</p>
        <div style="background:var(--card2);padding:14px;border-radius:13px;font-family:monospace;font-size:12px;word-break:break-all;margin:12px 0;border:1px solid var(--bdr)">${e(d.details)}</div>
        <button class="sbtn" onclick="navigator.clipboard.writeText(${JSON.stringify(d.details)});toast(t('copied'))">📋 نسخ البيانات</button>`;
      document.getElementById('purchaseModal').style.display='flex';
      if(curCat) loadProducts(curCat);
    } else if(d.error==='insufficient'){ if(confirm(t('insuf'))) showDeposit(); }
    else toast('❌ '+(d.error||''));
  }catch(err){ toast('❌'); }
}

// ═══ GAMES SECTION ════════════════════════════════════════
async function loadGames(){
  const app=document.getElementById('app');
  if(curGame) return loadGamePackages(curGame);
  app.innerHTML=loading();
  try{
    const data = await f('/api/games');
    const cats = data.categories||{};
    let h=`<div class="sec-h"><div class="sec-t">🎮 شحن الألعاب</div></div>
    <div style="background:linear-gradient(135deg,rgba(0,152,234,.12),rgba(0,106,184,.07));border:1px solid rgba(0,152,234,.3);border-radius:14px;padding:12px 14px;margin-bottom:16px;font-size:13px;color:var(--txt);display:flex;align-items:center;gap:10px">
      <svg width="28" height="28" viewBox="0 0 56 56" fill="none"><path d="M28 56C43.464 56 56 43.464 56 28C56 12.536 43.464 0 28 0C12.536 0 0 12.536 0 28C0 43.464 12.536 56 28 56Z" fill="#0098EA"/><path d="M37.5603 15.6277H18.4386C14.9228 15.6277 12.6944 19.3818 14.4632 22.4301L26.2644 42.8285C27.0345 44.1537 28.9644 44.1537 29.7345 42.8285L41.5357 22.4301C43.3056 19.3818 41.0772 15.6277 37.5603 15.6277ZM26.2335 39.2285L23.7089 34.8451L17.9203 24.3277H26.2335V39.2285ZM38.0786 24.3277L32.2901 34.8451L29.7655 39.2285V24.3277H38.0786Z" fill="white"/></svg>
      <div><b style="color:var(--ton)">الدفع بـ TON فقط</b><br><span style="font-size:11px;color:var(--gray)">⚡ شحن فوري — ادفع من محفظة TON مباشرة</span></div>
    </div>
    <div class="game-grid">`;
    for(const[k,c] of Object.entries(cats)){
      h+=`<div class="game-card" onclick="openGame('${k}')" style="padding:0;overflow:hidden">
        <div style="position:relative;width:100%;padding-bottom:88%;background:#0d0f18">
          <div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center">
            ${getGameImg(k)}
          </div>
          <div style="position:absolute;inset:0;background:linear-gradient(to top,rgba(0,0,0,.8) 0%,rgba(0,0,0,.1) 55%,transparent 100%)"></div>
          <div style="position:absolute;bottom:0;left:0;right:0;padding:10px 12px">
            <div class="game-card-name" style="font-size:13px;margin-bottom:2px">${e(c.name)}</div>
            <div class="game-card-cnt">💎 ${(c.packages||[]).length} باقة TON</div>
          </div>
        </div>
      </div>`;
    }
    h+='</div>'; app.innerHTML=h;
  }catch(err){ app.innerHTML=empty('❌'); }
}

function openGame(k){ curGame=k; loadGamePackages(k); }

async function loadGamePackages(gameKey){
  const app=document.getElementById('app'); app.innerHTML=loading();
  try{
    const data = await f('/api/games');
    const game = (data.categories||{})[gameKey];
    if(!game){ app.innerHTML=empty('❌'); return; }
    let h=`<button class="back-btn" onclick="goBackGame()">← ${t('back')}</button>
    <div class="sec-h"><div class="sec-t">${getGameIcon(gameKey)} ${e(game.name)}</div></div>
    <div class="sec-h" style="margin-top:0"><div class="sec-t" style="font-size:15px;color:var(--gray)">اختر الباقة</div></div>
    <div class="pkg-grid" id="pkgGrid">`;
    (game.packages||[]).forEach((p,i)=>{
      const tonPkg = (p.ton_price || (p.price * TON_GAME_RATE)).toFixed(3);
      h+=`<div class="pkg-btn" id="pkg-${i}" onclick="selectPkg(${i},'${e(p.label)}',${p.price},${tonPkg})">
        <div class="pkg-label">${e(p.label)}</div>
        <div class="pkg-price" style="color:var(--ton)">💎 ${tonPkg} TON</div>
      </div>`;
    });
    h+=`</div>
    <div style="background:rgba(0,152,234,.07);border:1px solid rgba(0,152,234,.2);border-radius:14px;padding:10px 14px;margin-bottom:14px;font-size:13px;color:var(--gray);text-align:center">
      💎 شحن الألعاب يتم الدفع فيه بـ <b style="color:var(--ton)">TON</b> فقط
    </div>
    <div class="game-order-form">
      <div class="game-order-title">📝 معلومات حسابك</div>`;
    (game.fields||['player_id']).forEach(field=>{
      const labels = {player_id:'معرف اللاعب (Player ID)',server:'الخادم (Server)',zone_id:'Zone ID',player_tag:'Player Tag'};
      h+=`<div class="field-label">${labels[field]||field}</div>
      <input type="text" id="field_${field}" class="inp" placeholder="${labels[field]||field}..." style="text-align:right">`;
    });
    h+=`<div id="sel-pkg-display" style="margin-bottom:12px;text-align:center;color:var(--gray);font-size:13px">لم تختر باقة بعد</div>
    <button class="sbtn" onclick="submitGameOrder('${gameKey}')">✅ تأكيد الطلب والدفع</button>
    </div>`;
    app.innerHTML=h;
    selectedPkg=null;
  }catch(err){ app.innerHTML=empty('❌'); }
}

function selectPkg(idx, label, price, tonPrice){
  document.querySelectorAll('.pkg-btn').forEach(b=>b.classList.remove('selected'));
  document.getElementById('pkg-'+idx).classList.add('selected');
  selectedPkg={idx,label,price,tonPrice};
  document.getElementById('sel-pkg-display').innerHTML=
    `<b style="color:var(--txt)">✓ ${e(label)}</b> — <span style="color:var(--ton)">💎 ${tonPrice||price} TON</span>`;
}

async function submitGameOrder(gameKey){
  if(!user.id) return toast('❌ يجب تسجيل الدخول');
  if(!selectedPkg) return toast('❌ اختر باقة أولاً');

  const data = await f('/api/games');
  const game = (data.categories||{})[gameKey];
  const fields_data = {};
  for(const field of (game.fields||['player_id'])){
    const val = document.getElementById('field_'+field)?.value?.trim();
    if(!val) return toast(`❌ أدخل ${field}`);
    fields_data[field] = val;
  }

  // Load TON balance and show in modal
  const tonBal = getTonBalance();

  // Open game order modal with payment confirmation
  document.getElementById('gameOrderTitle').textContent = `🎮 تأكيد شحن ${game.name}`;
  document.getElementById('gameOrderBody').innerHTML = `
    <div style="background:var(--card2);border-radius:14px;padding:14px;margin-bottom:14px;border:1px solid var(--bdr)">
      <div style="font-size:13px;color:var(--gray);margin-bottom:8px">ملخص الطلب</div>
      <div style="display:flex;justify-content:space-between;margin-bottom:6px">
        <span style="color:var(--gray)">اللعبة</span><b>${e(game.name)}</b>
      </div>
      <div style="display:flex;justify-content:space-between;margin-bottom:6px">
        <span style="color:var(--gray)">الباقة</span><b>${e(selectedPkg.label)}</b>
      </div>
      ${Object.entries(fields_data).map(([k,v])=>`
      <div style="display:flex;justify-content:space-between;margin-bottom:6px">
        <span style="color:var(--gray)">${k}</span><b style="font-family:monospace">${e(v)}</b>
      </div>`).join('')}
      <div style="border-top:1px solid var(--bdr);margin-top:10px;padding-top:10px;display:flex;justify-content:space-between;align-items:center">
        <span style="color:var(--gray)">التكلفة</span>
        <div style="text-align:left">
          <b style="color:var(--ton);font-size:16px">💎 ${selectedPkg.tonPrice||selectedPkg.price} TON</b>
        </div>
      </div>
    </div>
    <div style="background:rgba(0,152,234,.07);border:1px solid rgba(0,152,234,.2);border-radius:13px;padding:10px 14px;font-size:12px;color:var(--gray);text-align:center;margin-bottom:12px;line-height:1.7">
      💎 سيتم خصم المبلغ من محفظة TON المتصلة<br>
      <span style="color:var(--ton);font-weight:700">رصيد TON: <span id="ton-game-bal">جاري التحميل...</span></span>
    </div>
    <button class="sbtn ton" onclick="confirmGameOrderTON('${gameKey}','${encodeURIComponent(JSON.stringify(fields_data))}',${selectedPkg.price},'${encodeURIComponent(selectedPkg.label)}',${selectedPkg.tonPrice||0})">
      💎 الدفع بـ TON وإرسال الطلب
    </button>
    <button class="sbtn" style="background:var(--card2);color:var(--gray);margin-top:8px" onclick="cModal('gameOrderModal')">إلغاء</button>`;
  document.getElementById('gameOrderModal').style.display='flex';
  setTimeout(updateGameTonBal, 100);
}

async function confirmGameOrder(gameKey, fieldsEncoded, price, labelEncoded){
  const fields_data = JSON.parse(decodeURIComponent(fieldsEncoded));
  const label = decodeURIComponent(labelEncoded);
  try{
    const d = await fetch('/api/game_order',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({user_id:user.id,game:gameKey,package_label:label,price,fields:fields_data})
    }).then(r=>r.json());
    if(d.success){
      cachedBal = d.new_balance||0;
      cModal('gameOrderModal');
      toast(`✅ تم إرسال طلبك! رقم الطلب: #${d.order_id}`);
      curGame=null; loadGames();
    } else if(d.error==='insufficient'){ cModal('gameOrderModal'); if(confirm(t('insuf'))) showDeposit(); }
    else toast('❌ '+(d.error||''));
  }catch(err){ toast('❌'); }
}

// ═══ GAME ORDER via TON ═════════════════════════════════════
async function confirmGameOrderTON(gameKey, fieldsEncoded, price, labelEncoded, tonPrice){
  const fields_data = JSON.parse(decodeURIComponent(fieldsEncoded));
  const label = decodeURIComponent(labelEncoded);
  const finalTon = tonPrice > 0 ? tonPrice : parseFloat((price * TON_GAME_RATE).toFixed(3));

  // ── حالة 1: لا توجد محفظة مربوطة ──
  if(!tonConnectUI){
    return toast('❌ خدمة TON غير متاحة، أعد تحميل الصفحة');
  }
  if(!connectedWallet){
    toast('⚠️ ارتباط المحفظة مطلوب...');
    try{
      await tonConnectUI.openModal();
      // انتظر قليلاً ثم أعد المحاولة
      await new Promise(r=>setTimeout(r,3000));
      if(!connectedWallet){
        return toast('❌ لم يتم ربط المحفظة، حاول مجدداً');
      }
    }catch(e){
      return toast('❌ لم يتم ربط المحفظة');
    }
  }

  // ── حالة 2: المحفظة مربوطة — أرسل المعاملة ──
  const gameOrderBtn = document.querySelector('#gameOrderModal .sbtn.ton');
  if(gameOrderBtn){ gameOrderBtn.textContent='⏳ جاري الإرسال...'; gameOrderBtn.disabled=true; }

  try{
    const pid = 'game_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2,6);
    const nanotons = String(Math.round(finalTon * 1e9));

    const tx = {
      validUntil: Math.floor(Date.now()/1000) + 600,
      messages:[{
        address: TON_STORE_WALLET,
        amount: nanotons
      }]
    };

    toast('💎 افتح تطبيق المحفظة لتأكيد الدفع...');
    const result = await tonConnectUI.sendTransaction(tx);

    if(result){
      // ── المعاملة أُرسلت بنجاح ──
      const d = await fetch('/api/game_order_ton',{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({
          user_id: user.id,
          game: gameKey,
          package_label: label,
          price,
          ton_price: finalTon,
          fields: fields_data,
          payment_id: pid,
          tx_boc: result.boc || ''
        })
      }).then(r=>r.json());

      cModal('gameOrderModal');
      if(d.success){
        toast('✅ تم إرسال الطلب! رقم: #'+d.order_id);
        curGame=null; loadGames();
      } else {
        toast('⚠️ تم الدفع لكن حدث خطأ: '+(d.error||''));
      }
    }
  }catch(txErr){
    const msg = txErr?.message || String(txErr);
    if(msg.includes('USER_DECLINED') || msg.includes('Reject')){
      toast('❌ تم رفض العملية من المحفظة');
    } else if(msg.includes('INSUFFICIENT')){
      toast('❌ رصيد TON غير كافٍ في المحفظة');
    } else {
      // إذا فشل TonConnect SDK — إظهار طريقة يدوية
      showManualTonPayment(finalTon, gameKey, fieldsEncoded, price, labelEncoded);
    }
  }finally{
    if(gameOrderBtn){ gameOrderBtn.textContent='💎 الدفع بـ TON وإرسال الطلب'; gameOrderBtn.disabled=false; }
  }
}

// ── طريقة الدفع اليدوي كبديل عند فشل TonConnect ──
function showManualTonPayment(finalTon, gameKey, fieldsEncoded, price, labelEncoded){
  const memo = 'GAME'+Date.now().toString(36).toUpperCase();
  const body = document.getElementById('gameOrderBody');
  if(!body) return;
  body.innerHTML = `
    <div style="background:rgba(0,152,234,.08);border:1.5px solid rgba(0,152,234,.3);border-radius:16px;padding:16px;text-align:center">
      <div style="font-size:15px;font-weight:800;color:var(--ton);margin-bottom:12px">💎 أرسل TON يدوياً</div>
      <div style="font-size:13px;color:var(--gray);margin-bottom:10px">افتح محفظتك وأرسل المبلغ التالي:</div>
      <div style="font-size:26px;font-weight:900;color:var(--ton);margin-bottom:10px">${finalTon} TON</div>
      <div style="background:var(--card2);border-radius:12px;padding:10px;font-family:monospace;font-size:11px;color:var(--B);word-break:break-all;cursor:pointer;margin-bottom:6px"
           onclick="navigator.clipboard.writeText('${TON_STORE_WALLET}');toast('✓ تم نسخ العنوان')">${TON_STORE_WALLET}</div>
      <div style="font-size:11px;color:var(--gray);margin-bottom:6px">MEMO (مطلوب):</div>
      <div style="background:var(--card2);border-radius:10px;padding:8px;font-family:monospace;font-size:13px;color:var(--Y);cursor:pointer"
           onclick="navigator.clipboard.writeText('${memo}');toast('✓ تم نسخ الـ Memo')">${memo}</div>
    </div>
    <button class="sbtn" style="margin-top:12px" onclick="submitManualGameOrder('${gameKey}','${fieldsEncoded}',${price},'${labelEncoded}','${finalTon}','${memo}')">
      ✅ أرسلت المبلغ — تأكيد الطلب
    </button>
    <button class="sbtn" style="margin-top:8px;background:var(--card2);color:var(--gray)" onclick="cModal('gameOrderModal')">إلغاء</button>`;
}

async function submitManualGameOrder(gameKey, fieldsEncoded, price, labelEncoded, finalTon, memo){
  const fields_data = JSON.parse(decodeURIComponent(fieldsEncoded));
  const label = decodeURIComponent(labelEncoded);
  try{
    const d = await fetch('/api/game_order_ton',{
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({user_id:user.id,game:gameKey,package_label:label,price,ton_price:parseFloat(finalTon),fields:fields_data,payment_id:memo,manual:true})
    }).then(r=>r.json());
    cModal('gameOrderModal');
    if(d.success){ toast('✅ تم إرسال طلبك! رقم: #'+d.order_id); curGame=null; loadGames(); }
    else toast('❌ '+(d.error||''));
  }catch(e){ toast('❌ خطأ'); }
}

// ═══ DEPOSIT — Stars & TON ════════════════════════════════
let activePay='stars';
function setPayTab(p){
  activePay=p;
  document.getElementById('ptab-stars').classList.toggle('active',p==='stars');
  document.getElementById('ptab-ton').classList.toggle('active',p==='ton');
  document.getElementById('ptab-vf').classList.toggle('active',p==='vf');
  document.getElementById('sec-stars').style.display=p==='stars'?'':'none';
  document.getElementById('sec-ton').style.display=p==='ton'?'':'none';
  document.getElementById('sec-vf').style.display=p==='vf'?'':'none';
  if(p==='ton') updateTonWalletUI();
}
function setVF(n){ document.getElementById('vfAmt').value=n; }

async function initVodafoneCash(){
  const stars=parseInt(document.getElementById('vfAmt').value);
  if(!stars||stars<1) return toast('❌ أدخل عدد النجوم');
  if(!user.id) return toast('❌ يجب تسجيل الدخول');
  try{
    const d = await fetch('/api/vodafone_cash/init',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({user_id:user.id,stars})
    }).then(r=>r.json());
    if(!d.success) return toast('❌ '+(d.error||''));
    document.getElementById('vf-details').innerHTML=
      `⭐ <b>${d.stars} نجمة</b><br>
       💵 المبلغ المطلوب: <b style="color:var(--Y)">$${d.usd_equivalent}</b><br>
       🆔 رقم الطلب: <code style="font-size:11px">${d.payment_id}</code>`;
    document.getElementById('vf-result').style.display='';
    toast('✅ أرسل المبلغ للرقم الظاهر');
  }catch(e){ toast('❌ '+e.message); }
}
function setA(n){ document.getElementById('starsAmt').value=n; }
function setTA(n){ document.getElementById('tonAmt').value=n; updateTonRate(n); }

function updateTonRate(s){
  const el=document.getElementById('ton-rate-info');
  if(!s||s<1){el.textContent='';return;}
  // simple rate: 1 star = 0.013 USDT, 1 TON = TON_RATE_USDT
  fetch(`/api/ton/rate?stars=${s}`).then(r=>r.json()).then(d=>{
    el.textContent=`≈ ${d.ton} TON (${d.usdt} USDT)`;
  }).catch(()=>{});
}
document.addEventListener('DOMContentLoaded',()=>{
  const ta = document.getElementById('tonAmt');
  if(ta) ta.addEventListener('input',ev=>updateTonRate(parseInt(ev.target.value)));
});

async function depositStars(){
  const amt=parseInt(document.getElementById('starsAmt').value);
  if(!amt||amt<1) return toast('❌ أدخل مبلغاً صحيحاً');
  if(!user.id) return toast('❌ يجب تسجيل الدخول');
  try{
    const d=await f(`/api/deposit?user_id=${user.id}&amount=${amt}`);
    if(d.success){
      tg.openInvoice(d.url,s=>{
        if(s==='paid'){toast('🎉 تم الشحن!');setTimeout(()=>{refreshBal();if(curTab==='profile')loadProfile();},1500);}
        else if(s==='cancelled')toast('❌ إلغاء');
      });
    } else toast('❌ '+(d.error||''));
  }catch(err){ toast('❌'); }
}

async function initTonPayment(){
  const stars=parseInt(document.getElementById('tonAmt').value);
  if(!stars||stars<1) return toast('❌ أدخل عدد النجوم');
  if(!user.id) return toast('❌ يجب تسجيل الدخول');

  // Create a TON payment session on server
  try{
    const d = await fetch('/api/ton/create',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({user_id:user.id,stars})
    }).then(r=>r.json());
    if(!d.success) return toast('❌ '+(d.error||''));
    currentTonPaymentId = d.payment_id;
    document.getElementById('ton-pay-amount').textContent = d.ton_amount+' TON';
    document.getElementById('ton-pay-stars').textContent = stars+' ⭐';
    document.getElementById('ton-pay-addr').textContent = d.wallet_address;
    document.getElementById('ton-pay-addr').dataset.copy = d.wallet_address;
    document.getElementById('ton-pay-memo').textContent = 'Memo: '+d.memo;
    document.getElementById('ton-pay-memo').dataset.copy = d.memo;
    document.getElementById('ton-pay-result').style.display='';

    // If wallet connected via TonConnect, send transaction automatically
    if(connectedWallet && tonConnectUI){
      try{
        const tx = {
          validUntil: Math.floor(Date.now()/1000)+300,
          messages:[{
            address: d.wallet_address,
            amount: String(Math.round(parseFloat(d.ton_amount)*1e9)),
            payload: btoa(d.memo)
          }]
        };
        toast('⏳ جاري إرسال المعاملة...');
        await tonConnectUI.sendTransaction(tx);
        toast('✅ تم إرسال المعاملة! في انتظار التأكيد...');
      }catch(txErr){
        toast('ℹ️ أرسل المبلغ يدوياً للعنوان أعلاه');
      }
    } else {
      toast('ℹ️ ارسل TON للعنوان الظاهر أو اربط المحفظة');
    }
  }catch(err){ toast('❌ '+err.message); }
}

function copyTonAddr(){
  const el=document.getElementById('ton-pay-addr');
  navigator.clipboard.writeText(el.dataset.copy||el.textContent).then(()=>toast('✓ تم نسخ العنوان'));
}
function copyTonMemo(){
  const el=document.getElementById('ton-pay-memo');
  const txt=(el.dataset.copy||el.textContent).replace('Memo: ','');
  navigator.clipboard.writeText(txt).then(()=>toast('✓ تم نسخ الـ Memo'));
}

async function checkTonStatus(){
  if(!currentTonPaymentId) return;
  try{
    const d=await f(`/api/ton/status?payment_id=${currentTonPaymentId}`);
    if(d.status==='confirmed'){
      toast('✅ تم الشحن بـ TON!');
      setTimeout(()=>{refreshBal();if(curTab==='profile')loadProfile();cModal('depositModal');},2000);
    } else if(d.status==='pending') toast('⏳ في انتظار تأكيد الشبكة...');
    else toast('ℹ️ الحالة: '+(d.status||'غير معروف'));
  }catch(err){ toast('❌'); }
}

// ═══ PURCHASES ════════════════════════════════════════════
async function loadPurchases(){
  const app=document.getElementById('app'); app.innerHTML=loading();
  try{
    const[purch,gOrds]=await Promise.all([
      f(`/api/purchases?user_id=${user.id}`),
      f(`/api/game_orders?user_id=${user.id}`)
    ]);
    let h='';
    if(gOrds.orders?.length){
      h+=`<div class="sec-h"><div class="sec-t">🎮 طلبات الألعاب</div></div>`;
      for(const o of gOrds.orders){
        const statusMap={pending_payment:'في انتظار الدفع',paid:'مدفوع',processing:'قيد التنفيذ',done:'مكتمل',cancelled:'ملغي'};
        const statusClass={pending_payment:'pending',paid:'paid',processing:'processing',done:'done',cancelled:'cancelled'};
        h+=`<div class="purch-item">
          <div style="font-size:28px;flex-shrink:0">${getGameIcon(o.game)}</div>
          <div style="flex:1">
            <div style="font-weight:700;font-size:14px;margin-bottom:3px">${e(o.game_name||o.game)} — ${e(o.package_label)}</div>
            <div style="font-size:11px;color:var(--gray);margin-bottom:4px">⭐ ${o.price} | ${new Date(o.created).toLocaleDateString()}</div>
            <span class="order-badge ${statusClass[o.status]||'pending'}">${statusMap[o.status]||o.status}</span>
          </div>
        </div>`;
      }
    }
    if(purch.purchases?.length){
      h+=`<div class="sec-h"><div class="sec-t">🛍️ الحسابات المشتراة</div></div>`;
      for(const p of purch.purchases){
        h+=`<div class="purch-item">
          <div style="width:36px;height:36px;flex-shrink:0">${getCatSVG(p.category||'google')}</div>
          <div style="flex:1">
            <div style="font-weight:700;font-size:14px;margin-bottom:3px">${e(p.account_name)}</div>
            <div style="font-size:11px;color:var(--gray)">⭐ ${p.price} | ${new Date(p.purchase_date||p.timestamp).toLocaleDateString(lang==='en'?'en':'ar')}</div>
          </div>
        </div>`;
      }
    }
    if(!h) app.innerHTML=`<div class="empty">📭<br>${t('noord')}</div>`;
    else app.innerHTML=h;
  }catch(err){ app.innerHTML=empty('❌'); }
}

// ═══ PROFILE ══════════════════════════════════════════════
async function loadProfile(){
  const app=document.getElementById('app'); app.innerHTML=loading();
  try{
    const[bal,stats]=await Promise.all([f(`/api/balance/${user.id}`),f(`/api/user_stats/${user.id}`)]);
    let h=buildUID(bal.balance||0);
    h+=`<div style="display:flex;gap:10px;margin-bottom:4px">
      <button class="sbtn" style="flex:1;padding:12px;font-size:14px" onclick="showDeposit()">${t('recharge')}</button>
      <button class="sbtn" style="flex:1;padding:12px;font-size:14px;background:var(--card2);color:var(--txt)" onclick="showRedeem()">${t('redeem')}</button>
    </div>
    <div class="stat-grid">
      <div class="stat-box"><div class="stat-n">⭐ ${stats.spent||0}</div><div class="stat-l">${t('spent')}</div></div>
      <div class="stat-box"><div class="stat-n">📦 ${stats.purchases||0}</div><div class="stat-l">${t('orders_c')}</div></div>
    </div>`;
    app.innerHTML=h;
    updateTonWalletUI();
    refreshTonBalMain();
  }catch(err){ app.innerHTML=empty('❌'); }
}

// ═══ USER INFO MODAL ══════════════════════════════════════
async function showAccInfo(){
  try{
    const[sr,br]=await Promise.all([f(`/api/user_stats/${user.id}`),f(`/api/balance/${user.id}`)]);
    const av=user.photo_url?`<img src="${user.photo_url}" alt="">`:( user.first_name?.charAt(0).toUpperCase()||'👤');
    const bgs=[];
    if(user.is_premium) bgs.push('<span class="badge bp">⭐ Premium</span>');
    if(isAdmin) bgs.push('<span class="badge ba">🔧 Admin</span>');
    const walletAddr = connectedWallet?.account?.address;
    document.getElementById('accBody').innerHTML=`
    <div class="uif-head">
      <div class="uif-av">${av}</div>
      <div class="uif-nm">${e(user.full_name||user.first_name||'مستخدم')}</div>
      <div class="uif-h">${user.username?'@'+user.username:'ID: '+(user.id||'—')}</div>
      <div class="uif-bgs">${bgs.join('')}</div>
    </div>
    <div class="uif-body">
      <div class="uif-row"><span class="uif-lbl">الاسم</span><span class="uif-val">${e(user.full_name||user.first_name||'—')}</span></div>
      <div class="uif-row"><span class="uif-lbl">Telegram ID</span><span class="uif-val mono">${user.id||'—'}</span></div>
      <div class="uif-row"><span class="uif-lbl">اسم المستخدم</span><span class="uif-val">${user.username?'@'+user.username:'—'}</span></div>
      <div class="uif-row"><span class="uif-lbl">💎 محفظة TON</span><span class="uif-val mono" style="font-size:11px">${walletAddr?walletAddr.slice(0,8)+'...':'غير مربوطة'}</span></div>
      <div class="uif-row"><span class="uif-lbl">⭐ الرصيد</span><span class="uif-val mono">${br.balance||0} نجمة</span></div>
      <div class="uif-row"><span class="uif-lbl">💰 الإنفاق</span><span class="uif-val">${sr.spent||0} ⭐</span></div>
      <div class="uif-row"><span class="uif-lbl">📦 الطلبات</span><span class="uif-val">${sr.purchases||0}</span></div>
      <div style="height:14px"></div>
      <button class="sbtn" onclick="navigator.clipboard.writeText('${user.id}');toast('✓ تم نسخ الـ ID')">📋 نسخ معرفي</button>
      ${walletAddr?`<button class="sbtn" style="margin-top:8px;background:var(--card2);color:var(--gray)" onclick="tonConnectUI?.disconnect()">🔌 فصل المحفظة</button>`:''}
    </div>`;
    document.getElementById('accountModal').style.display='flex';
  }catch(err){ toast('❌'); }
}

// ═══ REDEEM ═══════════════════════════════════════════════
function showRedeem(){ document.getElementById('redeemModal').style.display='flex'; }
async function submitRedeem(){
  const code=document.getElementById('redeemInp').value.trim().toUpperCase();
  if(!code) return toast('❌ أدخل الكود');
  try{
    const d=await f(`/api/redeem?user_id=${user.id}&code=${encodeURIComponent(code)}`);
    if(d.success){ toast(`🎉 تمت إضافة ${d.amount} ⭐`); cModal('redeemModal'); refreshBal(); if(curTab==='profile')loadProfile(); }
    else toast('❌ '+(d.error||'كود غير صحيح'));
  }catch(err){ toast('❌'); }
}

// ═══ ADMIN ════════════════════════════════════════════════
async function loadAdmin(){
  if(curAdmin) return loadAdminSec(curAdmin);
  const app=document.getElementById('app'); app.innerHTML=loading();
  try{
    const stats=await f('/api/admin_stats');
    const menus=[
      {id:'accounts',t:t('madm'),d:'إضافة/تعديل/حذف الحسابات',icon:'🛍️'},
      {id:'games',t:'إدارة الألعاب',d:'أقسام وباقات شحن الألعاب',icon:'🎮'},
      {id:'game_orders',t:'طلبات الألعاب',d:'مراجعة وتنفيذ الطلبات',icon:'📋'},
      {id:'users',t:'المستخدمون',d:'عرض وإدارة المستخدمين',icon:'👥'},
      {id:'admins',t:'إدارة الأدمن',d:'إضافة/إزالة الأدمن',icon:'🔧'},
      {id:'redeem_codes',t:t('rcodes'),d:'إضافة وعرض أكواد الشحن',icon:'🎟️'},
      {id:'ton_payments',t:'مدفوعات TON',d:'تأكيد مدفوعات TON',icon:'💎'},
      {id:'purchases',t:t('allord'),d:'جميع المعاملات',icon:'📊'},
      {id:'broadcast',t:t('bcast'),d:'إرسال إشعار للجميع',icon:'📢'},
      {id:'categories',t:'إدارة الأقسام',d:'إضافة وحذف أقسام المتجر',icon:'🗂️'},
      {id:'vodafone_payments',t:'مدفوعات فودافون',d:'تأكيد طلبات فودافون كاش',icon:'📱'},
    ];
    let h=`<div class="sec-h"><div class="sec-t">${t('apanel')}</div></div>
      <div class="adm-stats">
        <div class="stat-box"><div class="stat-n">⭐ ${stats.revenue||0}</div><div class="stat-l">${t('revenue')}</div></div>
        <div class="stat-box"><div class="stat-n">📦 ${stats.purchases||0}</div><div class="stat-l">${t('sales')}</div></div>
        <div class="stat-box"><div class="stat-n">👥 ${stats.users||0}</div><div class="stat-l">${t('users')}</div></div>
        <div class="stat-box"><div class="stat-n">🎮 ${stats.game_orders||0}</div><div class="stat-l">طلبات الألعاب</div></div>
      </div>
      <div style="background:linear-gradient(135deg,rgba(34,197,94,.12),rgba(21,128,61,.08));border:1px solid rgba(34,197,94,.25);border-radius:16px;padding:14px 18px;margin-bottom:16px;display:flex;align-items:center;justify-content:space-between">
        <div>
          <div style="font-size:11px;color:var(--gray);margin-bottom:4px">💰 إجمالي الإيرادات بالدولار</div>
          <div style="font-size:26px;font-weight:900;color:var(--success)">$${(stats.total_usd||0).toFixed(2)}</div>
        </div>
        <div style="font-size:42px;opacity:.3">💵</div>
      </div>`;
    for(const m of menus){
      h+=`<div class="adm-card" onclick="openAdminSec('${m.id}')">
        <div class="adm-ic" style="font-size:24px">${m.icon}</div>
        <div class="adm-info"><div class="adm-t">${m.t}</div><div class="adm-d">${m.d}</div></div>
        <div class="adm-arr">›</div>
      </div>`;
    }
    app.innerHTML=h;
  }catch(err){ app.innerHTML=empty('❌'); }
}
function openAdminSec(s){ curAdmin=s; loadAdminSec(s); }
function goBackAdmin(){ curAdmin=null; loadAdmin(); }

async function loadAdminSec(sec){
  const app=document.getElementById('app'); app.innerHTML=loading();
  try{
    let h=`<button class="back-btn" onclick="goBackAdmin()">← رجوع</button>`;
    if(sec==='accounts'){
      const d=await f('/api/store');
      h+=`<div class="sec-h"><div class="sec-t">${t('madm')}</div></div>
          <div style="margin-bottom:12px"><button class="sbtn" onclick="adminAddAcc()" style="padding:12px">${t('addacc')}</button></div>`;
      for(const a of (d.accounts||[])){
        h+=`<div class="adm-card"><div class="adm-ic">${getCatSVG(a.category)}</div>
          <div class="adm-info"><div class="adm-t">${e(a.name)}</div><div class="adm-d">⭐${a.price} | Stock:${a.stock} | Sold:${a.sold||0}</div></div>
          <button class="edit-btn" onclick="adminEditAcc('${a.id}')">تعديل</button>
          <button class="del-btn" onclick="adminDelAcc('${a.id}')">حذف</button>
        </div>`;
      }
    } else if(sec==='games'){
      const d=await f('/api/games');
      h+=`<div class="sec-h"><div class="sec-t">إدارة الألعاب</div></div>
          <div style="margin-bottom:12px"><button class="sbtn" onclick="adminAddGame()" style="padding:12px">➕ إضافة لعبة</button></div>`;
      for(const[k,g] of Object.entries(d.categories||{})){
        h+=`<div class="adm-card">
          <div class="adm-ic" style="font-size:24px">${getGameIcon(k)}</div>
          <div class="adm-info"><div class="adm-t">${e(g.name)}</div><div class="adm-d">${(g.packages||[]).length} باقة</div></div>
          <button class="del-btn" onclick="adminDelGame('${k}')">حذف</button>
        </div>`;
      }
    } else if(sec==='game_orders'){
      const d=await f(`/api/admin/game_orders?admin_id=${user.id}`);
      h+=`<div class="sec-h"><div class="sec-t">طلبات الألعاب</div></div>`;
      for(const o of (d.orders||[])){
        const statusMap={pending_payment:'⏳ انتظار دفع',paid:'💳 مدفوع',processing:'⚙️ قيد التنفيذ',done:'✅ مكتمل',cancelled:'❌ ملغي'};
        const fieldStr=Object.entries(o.fields||{}).map(([k,v])=>`${k}:${v}`).join(' | ');
        h+=`<div class="adm-card">
          <div class="adm-ic" style="font-size:24px">${getGameIcon(o.game)}</div>
          <div class="adm-info">
            <div class="adm-t">${e(o.game_name||o.game)} — ${e(o.package_label)}</div>
            <div class="adm-d">⭐${o.price} | ${fieldStr}</div>
            <div class="adm-d">ID:${o.user_id} | ${statusMap[o.status]||o.status}</div>
          </div>
          ${o.status==='paid'?`<button class="done-btn" onclick="adminMarkGameDone('${o.id}')">تنفيذ</button>`:''}
          ${o.status==='pending_payment'?`<button class="del-btn" onclick="adminCancelGameOrder('${o.id}')">إلغاء</button>`:''}
        </div>`;
      }
    } else if(sec==='admins'){
      if(!is_owner_check()) return app.innerHTML=empty('⛔ للمالك فقط');
      const admData = await f(`/api/admin/list_admins?admin_id=${user.id}`);
      h+=`<div class="sec-h"><div class="sec-t">إدارة الأدمن</div></div>
          <div style="background:rgba(59,130,246,.07);border:1px solid rgba(59,130,246,.15);border-radius:14px;padding:12px;margin-bottom:14px;font-size:13px;color:var(--gray)">
            يمكن للمالك فقط إضافة أو إزالة الأدمن
          </div>
          <input type="text" id="newAdminId" class="inp" placeholder="أدخل Telegram ID للأدمن الجديد...">
          <button class="sbtn" style="margin-bottom:16px" onclick="adminAddAdmin()">➕ إضافة أدمن</button>
          <div class="sec-h" style="margin-top:4px"><div class="sec-t" style="font-size:15px">الأدمن الحاليون</div></div>`;
      for(const aid of (admData.admins||[])){
        h+=`<div class="adm-card">
          <div class="adm-ic"><svg viewBox="0 0 24 24" fill="none" stroke="var(--B)" stroke-width="2"><path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/><circle cx="12" cy="7" r="4"/></svg></div>
          <div class="adm-info"><div class="adm-t" style="font-family:monospace">${aid}</div><div class="adm-d">أدمن</div></div>
          <button class="del-btn" onclick="adminRemoveAdmin('${aid}')">إزالة</button>
        </div>`;
      }
      if(!(admData.admins||[]).length) h+=`<div class="empty">لا يوجد أدمن حتى الآن</div>`;
    } else if(sec==='ton_payments'){
      const d=await f(`/api/admin/ton_payments?admin_id=${user.id}`);
      h+=`<div class="sec-h"><div class="sec-t">مدفوعات TON</div></div>`;
      for(const[pid,p] of Object.entries(d.payments||{})){
        if(p.status==='confirmed') continue;
        h+=`<div class="adm-card">
          <div class="adm-ic" style="font-size:24px">💎</div>
          <div class="adm-info">
            <div class="adm-t">⭐ ${p.stars} نجمة = ${p.ton_amount} TON</div>
            <div class="adm-d">UID:${p.user_id} | Memo:${p.memo}</div>
          </div>
          <button class="done-btn" onclick="adminConfirmTon('${pid}')">تأكيد</button>
        </div>`;
      }
    } else if(sec==='users'){
      const us=await f('/api/users');
      h+=`<div class="sec-h"><div class="sec-t">المستخدمون</div></div>`;
      for(const u of (us||[])){
        h+=`<div class="adm-card">
          <div class="adm-ic"><svg viewBox="0 0 24 24" fill="none" stroke="var(--B)" stroke-width="2" stroke-linecap="round"><path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/><circle cx="12" cy="7" r="4"/></svg></div>
          <div class="adm-info"><div class="adm-t">${e(u.first_name||'—')} ${u.username?'@'+u.username:''}</div><div class="adm-d">ID:${u.id} | ⭐${u.spent||0}</div></div>
          <button class="edit-btn" onclick="adminAddStars('${u.id}')">+⭐</button>
        </div>`;
      }
    } else if(sec==='redeem_codes'){
      const codes=await f(`/api/admin/redeem_codes?admin_id=${user.id}`);
      h+=`<div class="sec-h"><div class="sec-t">${t('rcodes')}</div></div>
          <div style="margin-bottom:12px">
            <input type="text" id="nCode" class="inp" placeholder="كود جديد..." style="margin-bottom:8px">
            <input type="number" id="nCodeAmt" class="inp" placeholder="عدد النجوم...">
            <button class="sbtn" onclick="adminAddCode()" style="margin-top:8px;padding:12px">${t('addcode')}</button>
          </div>`;
      for(const[c,a] of Object.entries(codes||{})){
        h+=`<div class="adm-card">
          <div class="adm-ic" style="font-size:24px">🎟️</div>
          <div class="adm-info"><div class="adm-t" style="font-family:monospace">${e(c)}</div><div class="adm-d">⭐ ${a} نجمة</div></div>
          <button class="del-btn" onclick="adminDelCode('${c}')">حذف</button>
        </div>`;
      }
    } else if(sec==='purchases'){
      const d=await f(`/api/admin/purchases?admin_id=${user.id}`);
      h+=`<div class="sec-h"><div class="sec-t">${t('allord')}</div></div>`;
      for(const p of (d.purchases||[])){
        h+=`<div class="purch-item">
          <div style="width:36px;height:36px;flex-shrink:0">${getCatSVG(p.category||'google')}</div>
          <div style="flex:1"><div style="font-weight:700;margin-bottom:3px">${e(p.account_name)}</div>
          <div style="font-size:11px;color:var(--gray)">ID:${p.user_id} | ⭐${p.price} | ${p.payment_method||'balance'}</div></div>
        </div>`;
      }
    } else if(sec==='broadcast'){
      h+=`<div class="sec-h"><div class="sec-t">${t('bcast')}</div></div>
          <textarea id="bMsg" class="inp" rows="5" placeholder="${t('writemsg')}"></textarea>
          <button class="sbtn" onclick="adminBroadcast()" style="margin-top:8px;padding:12px">${t('sendall')}</button>`;
    } else if(sec==='categories'){
      const d=await f('/api/store');
      const cats=d.categories||{};
      h+=`<div class="sec-h"><div class="sec-t">🗂️ إدارة الأقسام</div></div>
          <div style="margin-bottom:14px">
            <input type="text" id="newCatKey" class="inp" placeholder="مفتاح القسم (مثل: amazon)..." style="margin-bottom:8px">
            <input type="text" id="newCatName" class="inp" placeholder="اسم القسم (مثل: Amazon)..." style="margin-bottom:8px">
            <input type="text" id="newCatColor" class="inp" placeholder="اللون (hex مثل #FF9900)..." value="#3b82f6">
            <button class="sbtn" onclick="adminAddCategory()" style="margin-top:8px;padding:12px">➕ إضافة قسم جديد</button>
          </div>
          <div class="sec-h" style="margin-top:4px"><div class="sec-t" style="font-size:15px">الأقسام الحالية</div></div>`;
      for(const[k,c] of Object.entries(cats)){
        h+=`<div class="adm-card">
          <div class="adm-ic" style="width:48px;height:48px;border-radius:14px;background:${c.color||'#3b82f6'}22;display:flex;align-items:center;justify-content:center">
            <span style="font-size:20px;font-weight:800;color:${c.color||'#3b82f6'}">${(c.name||k).charAt(0).toUpperCase()}</span>
          </div>
          <div class="adm-info"><div class="adm-t">${e(c.name||k)}</div><div class="adm-d" style="font-family:monospace">${k}</div></div>
          <button class="del-btn" onclick="adminDelCategory('${k}')">حذف</button>
        </div>`;
      }
    } else if(sec==='vodafone_payments'){
      const d=await f('/api/admin/vodafone_payments?admin_id='+user.id);
      h+=`<div class="sec-h"><div class="sec-t">📱 مدفوعات فودافون كاش</div></div>`;
      const payments = d.payments||[];
      if(!payments.length) h+=`<div class="empty">📭<br>لا توجد مدفوعات معلقة</div>`;
      for(const p of payments){
        h+=`<div class="adm-card">
          <div class="adm-ic" style="font-size:24px">📱</div>
          <div class="adm-info">
            <div class="adm-t">⭐ ${p.stars} نجمة</div>
            <div class="adm-d">UID:${p.user_id} | ${new Date(p.created).toLocaleDateString('ar')}</div>
            <div class="adm-d" style="font-family:monospace;font-size:10px">${p.id}</div>
          </div>
          <button class="done-btn" onclick="adminConfirmVF('${p.id}')">تأكيد</button>
        </div>`;
      }
    }
    app.innerHTML=h;
  }catch(err){ app.innerHTML=empty('❌'); }
}

// ═══ ADMIN HELPERS ════════════════════════════════════════
function is_owner_check(){ return user.id===OWNER_ID; }

async function adminAddAdmin(){
  const newId=document.getElementById('newAdminId').value.trim(); if(!newId)return;
  const d=await fetch('/api/admin/add_admin',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({admin_id:user.id,new_admin_id:parseInt(newId)})}).then(r=>r.json());
  toast(d.success?`✅ تمت إضافة أدمن جديد`:'❌ '+d.error); if(d.success)loadAdminSec('admins');
}
async function adminRemoveAdmin(aid){
  if(!confirm('إزالة هذا الأدمن؟'))return;
  const d=await fetch('/api/admin/remove_admin',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({admin_id:user.id,target_id:parseInt(aid)})}).then(r=>r.json());
  toast(d.success?'✅ تمت الإزالة':'❌ '+d.error); if(d.success)loadAdminSec('admins');
}
async function adminAddGame(){
  const key=prompt('مفتاح اللعبة (مثل: pubg2):'); if(!key)return;
  const name=prompt('اسم اللعبة:'); if(!name)return;
  const color=prompt('اللون (hex مثل #ff4444):','#3b82f6');
  const fields=prompt('الحقول المطلوبة (مفصولة بفاصلة، مثل: player_id,server):','player_id');
  const fieldsArr=fields.split(',').map(s=>s.trim()).filter(Boolean);
  let packages=[];
  const numPkg=parseInt(prompt('عدد الباقات:')||'0');
  for(let i=0;i<numPkg;i++){
    const label=prompt(`الباقة ${i+1} - الاسم:`); if(!label)break;
    const price=parseInt(prompt(`الباقة ${i+1} - السعر (نجوم):`)||'0');
    if(label&&price>0) packages.push({label,price});
  }
  const d=await fetch('/api/admin/add_game',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({admin_id:user.id,key,name,color,fields:fieldsArr,packages})}).then(r=>r.json());
  toast(d.success?'✅ تمت إضافة اللعبة':'❌ '+d.error); if(d.success)loadAdminSec('games');
}
async function adminDelGame(key){
  if(!confirm('حذف هذه اللعبة؟'))return;
  const d=await fetch('/api/admin/delete_game',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({admin_id:user.id,key})}).then(r=>r.json());
  toast(d.success?'✅ تم الحذف':'❌'); if(d.success)loadAdminSec('games');
}
async function adminMarkGameDone(oid){
  if(!confirm('تأكيد تنفيذ الطلب؟'))return;
  const d=await fetch('/api/admin/game_order_update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({admin_id:user.id,order_id:oid,status:'done'})}).then(r=>r.json());
  toast(d.success?'✅ تم تنفيذ الطلب':'❌'); if(d.success)loadAdminSec('game_orders');
}
async function adminCancelGameOrder(oid){
  if(!confirm('إلغاء هذا الطلب؟'))return;
  const d=await fetch('/api/admin/game_order_update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({admin_id:user.id,order_id:oid,status:'cancelled'})}).then(r=>r.json());
  toast(d.success?'✅ تم الإلغاء وإعادة الرصيد':'❌'); if(d.success)loadAdminSec('game_orders');
}
async function adminConfirmTon(pid){
  if(!confirm('تأكيد استلام مدفوعة TON؟'))return;
  const d=await fetch('/api/admin/confirm_ton',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({admin_id:user.id,payment_id:pid})}).then(r=>r.json());
  toast(d.success?'✅ تم تأكيد الدفع وإضافة الرصيد':'❌'); if(d.success)loadAdminSec('ton_payments');
}
async function adminAddAcc(){
  const cats=['google','netflix','spotify'];
  const nm=prompt('اسم الحساب:'); if(!nm)return;
  const cat=prompt('الفئة ('+cats.join('/')+'):'); if(!cats.includes(cat))return alert('فئة غير صحيحة');
  const price=parseInt(prompt('السعر (نجوم):')); if(!price)return;
  const stock=parseInt(prompt('الكمية:')); if(isNaN(stock))return;
  const acc=prompt('بيانات الحساب:'); if(!acc)return;
  const desc=prompt('الوصف:')||'';
  const d=await fetch('/api/admin/add_account',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({admin_id:user.id,name:nm,category:cat,price,stock,account:acc,description:desc})}).then(r=>r.json());
  toast(d.success?'✅ تمت الإضافة':'❌ '+d.error); if(d.success)loadAdminSec('accounts');
}
async function adminDelAcc(id){
  if(!confirm('حذف هذا الحساب؟'))return;
  const d=await fetch('/api/admin/delete_account',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({admin_id:user.id,account_id:id})}).then(r=>r.json());
  toast(d.success?'✅ تم الحذف':'❌'); if(d.success)loadAdminSec('accounts');
}
async function adminEditAcc(id){
  const field=prompt('تعديل (name/price/stock/description):'); if(!field)return;
  const val=prompt('القيمة الجديدة:'); if(val===null)return;
  const upd={}; upd[field]=(field==='price'||field==='stock')?parseInt(val):val;
  const d=await fetch('/api/admin/edit_account',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({admin_id:user.id,account_id:id,updates:upd})}).then(r=>r.json());
  toast(d.success?'✅ تم التعديل':'❌'); if(d.success)loadAdminSec('accounts');
}
async function adminAddStars(uid){
  const amt=parseInt(prompt(t('addstars'))); if(!amt)return;
  const d=await fetch('/api/admin/add_stars',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({admin_id:user.id,user_id:uid,amount:amt})}).then(r=>r.json());
  toast(d.success?`✅ تمت إضافة ${amt}⭐`:'❌');
}
async function adminAddCode(){
  const c=document.getElementById('nCode').value.trim().toUpperCase(); if(!c)return;
  const a=parseInt(document.getElementById('nCodeAmt').value); if(!a)return;
  const d=await fetch('/api/admin/add_redeem_code',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({admin_id:user.id,code:c,amount:a})}).then(r=>r.json());
  toast(d.success?'✅ تمت الإضافة':'❌'); if(d.success)loadAdminSec('redeem_codes');
}
async function adminDelCode(c){
  const d=await fetch('/api/admin/remove_redeem_code',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({admin_id:user.id,code:c})}).then(r=>r.json());
  toast(d.success?'✅ تم الحذف':'❌'); if(d.success)loadAdminSec('redeem_codes');
}
async function adminBroadcast(){
  const msg=document.getElementById('bMsg').value.trim(); if(!msg)return;
  if(!confirm('إرسال للجميع؟'))return;
  const d=await fetch('/api/admin/broadcast',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({admin_id:user.id,message:msg})}).then(r=>r.json());
  toast(d.success?`✅ تم لـ ${d.sent} مستخدم`:'❌');
}

async function adminAddCategory(){
  const key=document.getElementById('newCatKey').value.trim().toLowerCase().replace(/\s+/g,'_'); if(!key)return toast('❌ أدخل مفتاح القسم');
  const name=document.getElementById('newCatName').value.trim(); if(!name)return toast('❌ أدخل اسم القسم');
  const color=document.getElementById('newCatColor').value.trim()||'#3b82f6';
  const d=await fetch('/api/admin/add_category',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({admin_id:user.id,key,name,color})}).then(r=>r.json());
  toast(d.success?'✅ تمت إضافة القسم':'❌ '+d.error); if(d.success)loadAdminSec('categories');
}
async function adminDelCategory(key){
  if(!confirm('حذف قسم '+key+'؟'))return;
  const d=await fetch('/api/admin/delete_category',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({admin_id:user.id,key})}).then(r=>r.json());
  toast(d.success?'✅ تم الحذف':'❌ '+d.error); if(d.success)loadAdminSec('categories');
}
async function adminConfirmVF(pid){
  if(!confirm('تأكيد استلام مدفوعة فودافون كاش؟'))return;
  const d=await fetch('/api/admin/confirm_vodafone',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({admin_id:user.id,payment_id:pid})}).then(r=>r.json());
  toast(d.success?'✅ تم تأكيد الدفع وإضافة الرصيد':'❌ '+d.error); if(d.success)loadAdminSec('vodafone_payments');
}

// ═══ HELPERS ══════════════════════════════════════════════
function switchTab(tab){
  curTab=tab; curCat=null; curAdmin=null; curGame=null;
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  const el=document.getElementById('tab-'+tab); if(el)el.classList.add('active');
  if(tab==='store') loadStore();
  else if(tab==='games') loadGames();
  else if(tab==='purchases') loadPurchases();
  else if(tab==='profile') loadProfile();
  else if(tab==='admin'&&isAdmin) loadAdmin();
  else loadWelcome();
}
function goBack(){ curCat=null; loadStore(); }
function goBackGame(){ curGame=null; loadGames(); }
function showDeposit(){
  document.getElementById('depositModal').style.display='flex';
  setPayTab('stars');
  document.getElementById('ton-pay-result').style.display='none';
}
function showTonDeposit(){
  document.getElementById('depositModal').style.display='flex';
  setPayTab('ton');
  document.getElementById('ton-pay-result').style.display='none';
  updateTonWalletUI();
}

// تحديث رصيد TON في الصفحة الرئيسية
function refreshTonBalMain(){
  const el = document.getElementById('ton-bal-main');
  if(!el) return;
  if(!connectedWallet){
    el.textContent = '—';
    return;
  }
  const rawBal = connectedWallet?.account?.balance;
  if(rawBal){
    const tonBal = parseFloat((parseInt(rawBal)/1e9).toFixed(3));
    cachedTonBalance = tonBal;
    el.textContent = tonBal;
  } else {
    el.textContent = '✓';
  }
}
function cModal(id){ document.getElementById(id).style.display='none'; }
function toast(msg){
  const el=document.createElement('div'); el.className='toast'; el.textContent=msg;
  document.body.appendChild(el); setTimeout(()=>el.remove(),2800);
}
const f = url => fetch(url).then(r=>r.json());
function e(s){ if(!s)return''; const d=document.createElement('div'); d.textContent=s; return d.innerHTML; }
function loading(){ return '<div class="loading"><div class="spinner"></div></div>'; }
function empty(ic){ return `<div class="empty">${ic}</div>`; }

// ═══ BOOT ═════════════════════════════════════════════════
const initTabParam=params.get('tab')||'store';
if(['store','games','profile','purchases'].includes(initTabParam)) switchTab(initTabParam);
else if(initTabParam==='admin') checkAdminStatus().then(()=>{if(isAdmin)switchTab('admin');else loadWelcome();});
else loadWelcome();
</script>
</body>
</html>
'''

TON_MANIFEST = '''{
  "url": "''' + WEBAPP_URL + '''",
  "name": "SoNs Store",
  "iconUrl": "''' + WEBAPP_URL + '''/icon.png",
  "termsOfUseUrl": "''' + WEBAPP_URL + '''",
  "privacyPolicyUrl": "''' + WEBAPP_URL + '''"
}'''

# ==================== HTTP HANDLER ====================
# TON wallet address where customers send payments (set yours here)
TON_WALLET_ADDRESS = os.environ.get('TON_WALLET_ADDRESS', 'UQA2NRUfGsW4IFP8yprXDqkthwa4-NstFwFn8k54-WiMBy2M')
# 1 star = X TON (example: 50 stars = 1 TON at current rates)
STAR_TO_TON = float(os.environ.get('STAR_TO_TON', '0.02'))  # 1 star ≈ 0.02 TON

class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin','*')
        self.send_header('Access-Control-Allow-Methods','GET,POST,OPTIONS')
        self.send_header('Access-Control-Allow-Headers','Content-Type')
        self.end_headers()

    def do_GET(self):
        path=urlparse(self.path).path; q=parse_qs(urlparse(self.path).query)
        try:
            if path=='/':
                self._html(HTML_WEBAPP)
            elif path=='/tonconnect-manifest.json':
                self.send_response(200)
                self.send_header('Content-type','application/json')
                self.send_header('Access-Control-Allow-Origin','*')
                self.end_headers()
                self.wfile.write(TON_MANIFEST.encode())

            elif path=='/api/store':
                self._json({'accounts':db.get_accounts(),'categories':db.categories})
            elif path=='/api/games':
                self._json({'categories':db.get_game_categories()})
            elif path=='/api/check_admin':
                uid = int(q.get('user_id',[0])[0])
                self._json({'is_admin': is_admin(uid)})
            elif path.startswith('/api/balance/'):
                self._json({'balance':db.get_balance(int(path.split('/')[-1]))})
            elif path=='/api/purchases':
                self._json({'purchases':db.get_user_purchases(str(q.get('user_id',[0])[0]),50)})
            elif path=='/api/game_orders':
                uid = q.get('user_id',[None])[0]
                orders = db.get_game_orders(uid) if uid else []
                # attach game name
                for o in orders:
                    gc = db.get_game_categories().get(o.get('game',''))
                    if gc: o['game_name'] = gc.get('name','')
                self._json({'orders':orders})
            elif path.startswith('/api/user_stats/'):
                self._json(db.get_user_stats(int(path.split('/')[-1])))
            elif path=='/api/users':
                self._json(db.get_all_users())
            elif path=='/api/admin_stats':
                s=db.get_stats()
                self._json({'revenue':s['revenue'],'purchases':s['total_purchases'],
                            'users':db.get_user_count(),'accounts':s['total_accounts'],
                            'game_orders':s['total_game_orders'],
                            'total_usd': round(s.get('total_usd',0),2)})
            elif path=='/api/admin/redeem_codes':
                if is_admin(int(q.get('admin_id',[0])[0])): self._json(db.get_redeem_codes())
                else: self._err(403)
            elif path=='/api/admin/purchases':
                if is_admin(int(q.get('admin_id',[0])[0])): self._json({'purchases':db.purchases})
                else: self._err(403)
            elif path=='/api/admin/game_orders':
                if is_admin(int(q.get('admin_id',[0])[0])):
                    orders = db.get_game_orders()
                    for o in orders:
                        gc = db.get_game_categories().get(o.get('game',''))
                        if gc: o['game_name'] = gc.get('name','')
                    self._json({'orders':orders})
                else: self._err(403)
            elif path=='/api/admin/list_admins':
                if is_owner(int(q.get('admin_id',[0])[0])):
                    self._json(db.get_admins_list())
                else: self._err(403)
            elif path=='/api/admin/ton_payments':
                if is_admin(int(q.get('admin_id',[0])[0])):
                    self._json({'payments':db.ton_payments})
                else: self._err(403)
            elif path=='/api/admin/vodafone_payments':
                if is_admin(int(q.get('admin_id',[0])[0])):
                    vf_list = [{'id':pid,'user_id':p.get('user_id'),'stars':p.get('stars'),
                                'created':p.get('created','')}
                               for pid,p in db.pending.items()
                               if isinstance(p,dict) and p.get('type')=='vodafone_cash']
                    self._json({'payments':vf_list})
                else: self._err(403)
            elif path=='/api/deposit':
                uid=int(q.get('user_id',[0])[0]); amt=int(q.get('amount',[0])[0])
                if uid<=0 or amt<=0: return self._json({'success':False,'error':'Invalid'})
                amt=max(1,min(100000,amt))
                pid=f"dep_{uid}_{amt}_{uuid.uuid4().hex[:8]}"
                db.add_pending(pid,{'type':'deposit','user_id':uid,'amount':amt})
                try:
                    inv=bot.create_invoice_link(title=f"⭐ شحن {amt} نجمة",
                        description=f"إضافة {amt} نجمة",payload=pid,
                        provider_token="",currency="XTR",
                        prices=[LabeledPrice(label=f"{amt} نجمة",amount=amt)])
                    self._json({'success':True,'url':inv})
                except Exception as ex: self._json({'success':False,'error':str(ex)})
            elif path=='/api/ton/rate':
                stars=int(q.get('stars',[1])[0])
                ton_amount = round(stars * STAR_TO_TON, 4)
                usdt = round(ton_amount * TON_RATE_USDT, 2)
                self._json({'stars':stars,'ton':ton_amount,'usdt':usdt,'rate':STAR_TO_TON})
            elif path=='/api/ton/status':
                pid=q.get('payment_id',[''])[0]
                p=db.get_ton_payment(pid)
                if p: self._json({'status':p.get('status','pending')})
                else: self._json({'status':'not_found'})
            elif path=='/api/redeem':
                uid=int(q.get('user_id',[0])[0]); code=q.get('code',[''])[0]
                amount=db.redeem_code(uid,code)
                if amount>0: self._json({'success':True,'amount':amount})
                else: self._json({'success':False,'error':'كود غير صحيح أو مستخدم بالفعل'})
            else: self._err(404)
        except Exception as ex: logger.error(f"GET {path}: {ex}"); self._err(500)

    def do_POST(self):
        path=urlparse(self.path).path
        try:
            length=int(self.headers.get('Content-Length',0))
            body=json.loads(self.rfile.read(length).decode()) if length else {}

            if path=='/api/purchase':
                acc=db.get_account(body['account_id'])
                if not acc: return self._json({'success':False,'error':'Not found'})
                if acc['stock']<=0: return self._json({'success':False,'error':'Out of stock'})
                final_price = calc_price_with_commission(acc['price'])
                if db.get_balance(body['user_id'])<final_price:
                    return self._json({'success':False,'error':'insufficient'})
                if db.deduct_balance(body['user_id'],final_price):
                    db.update_stock(body['account_id'],acc['stock']-1)
                    db.increment_sold(body['account_id'])
                    commission_stars = final_price - acc['price']
                    db.add_purchase({'user_id':str(body['user_id']),'account_id':body['account_id'],
                        'account_name':acc['name'],'price':final_price,
                        'base_price':acc['price'],'commission':commission_stars,
                        'payment_method':'balance','purchase_date':datetime.now().isoformat(),
                        'account_details':acc['account'],'category':acc.get('category','')})
                    db.update_user_stats(body['user_id'],final_price)
                    self._json({'success':True,'details':acc['account'],'new_balance':db.get_balance(body['user_id'])})
                else: self._json({'success':False,'error':'Failed'})

            # ── Game Order (deducts balance immediately) ──
            elif path=='/api/game_order':
                uid = str(body.get('user_id'))
                game_key = body.get('game')
                label = body.get('package_label','')
                base_price = int(body.get('price',0))
                price = calc_price_with_commission(base_price)
                fields = body.get('fields',{})

                if not uid or not game_key or not base_price:
                    return self._json({'success':False,'error':'Invalid'})
                if db.get_balance(uid) < price:
                    return self._json({'success':False,'error':'insufficient'})

                gc = db.get_game_categories().get(game_key,{})
                if not gc: return self._json({'success':False,'error':'لعبة غير موجودة'})

                if db.deduct_balance(uid, price):
                    oid = db.add_game_order({
                        'user_id': uid,
                        'game': game_key,
                        'game_name': gc.get('name',''),
                        'package_label': label,
                        'price': price,
                        'fields': fields,
                        'status': 'paid'
                    })
                    # Notify admins
                    field_txt = '\n'.join(f'• {k}: {v}' for k,v in fields.items())
                    try:
                        for adm_id in db.get_admins_list().get('admins',[]) + [OWNER_ID]:
                            bot.send_message(int(adm_id),
                                f"🎮 <b>طلب شحن جديد!</b>\n\n"
                                f"🆔 رقم الطلب: <code>{oid}</code>\n"
                                f"👤 المستخدم: {uid}\n"
                                f"🎯 اللعبة: {gc.get('name',game_key)}\n"
                                f"📦 الباقة: {label}\n"
                                f"⭐ السعر: {price}\n"
                                f"{field_txt}\n\n"
                                f"✅ يرجى تنفيذ الطلب من لوحة التحكم",
                                parse_mode='HTML')
                    except: pass
                    self._json({'success':True,'order_id':oid,'new_balance':db.get_balance(uid)})
                else:
                    self._json({'success':False,'error':'insufficient'})

            # ── TON Payment Create ──
            elif path=='/api/ton/create':
                uid = body.get('user_id')
                stars = int(body.get('stars',0))
                if not uid or stars < 1:
                    return self._json({'success':False,'error':'Invalid'})
                ton_amount = round(stars * STAR_TO_TON, 4)
                memo = f"SONS{uid}{uuid.uuid4().hex[:6].upper()}"
                pid = f"ton_{uid}_{uuid.uuid4().hex[:8]}"
                db.save_ton_payment(pid, {
                    'user_id': str(uid),
                    'stars': stars,
                    'ton_amount': ton_amount,
                    'memo': memo,
                    'status': 'pending',
                    'created': datetime.now().isoformat()
                })
                self._json({
                    'success': True,
                    'payment_id': pid,
                    'wallet_address': TON_WALLET_ADDRESS,
                    'ton_amount': ton_amount,
                    'memo': memo
                })

            # ── Game Order via TON (no Stars deduction) ──
            elif path=='/api/game_order_ton':
                uid = str(body.get('user_id'))
                game_key = body.get('game')
                label = body.get('package_label','')
                price = int(body.get('price',0))
                ton_price = float(body.get('ton_price',0))
                fields = body.get('fields',{})
                payment_id = body.get('payment_id','')

                if not uid or not game_key:
                    return self._json({'success':False,'error':'Invalid'})

                gc = db.get_game_categories().get(game_key,{})
                if not gc: return self._json({'success':False,'error':'لعبة غير موجودة'})

                oid = db.add_game_order({
                    'user_id': uid,
                    'game': game_key,
                    'game_name': gc.get('name',''),
                    'package_label': label,
                    'price': price,
                    'ton_price': ton_price,
                    'payment_method': 'ton',
                    'payment_id': payment_id,
                    'fields': fields,
                    'status': 'paid'
                })
                # Notify admins
                field_txt = '\n'.join(f'• {k}: {v}' for k,v in fields.items())
                try:
                    for adm_id in db.get_admins_list().get('admins',[]) + [OWNER_ID]:
                        bot.send_message(int(adm_id),
                            f"🎮 <b>طلب شحن جديد (TON)!</b>\n\n"
                            f"🆔 رقم الطلب: <code>{oid}</code>\n"
                            f"👤 المستخدم: {uid}\n"
                            f"🎯 اللعبة: {gc.get('name',game_key)}\n"
                            f"📦 الباقة: {label}\n"
                            f"💎 المدفوع: {ton_price} TON\n"
                            f"{field_txt}\n\n"
                            f"✅ يرجى تنفيذ الطلب من لوحة التحكم",
                            parse_mode='HTML')
                except: pass
                self._json({'success':True,'order_id':oid})

            # ── Vodafone Cash payment init ──
            elif path=='/api/vodafone_cash/init':
                uid = body.get('user_id')
                stars = int(body.get('stars', 0))
                if not uid or stars < 1:
                    return self._json({'success':False,'error':'Invalid'})
                final_stars = stars  # no commission on deposit (only on purchase)
                pid = f"vf_{uid}_{uuid.uuid4().hex[:8]}"
                db.add_pending(pid, {
                    'type': 'vodafone_cash',
                    'user_id': str(uid),
                    'stars': final_stars,
                    'status': 'pending',
                    'created': datetime.now().isoformat()
                })
                self._json({
                    'success': True,
                    'payment_id': pid,
                    'vodafone_number': VODAFONE_CASH_NUMBER,
                    'stars': final_stars,
                    'usd_equivalent': round(final_stars * STAR_TO_USD, 2)
                })

            # ── Admin: Confirm Vodafone Cash ──
            elif path=='/api/admin/confirm_vodafone':
                if not is_admin(body.get('admin_id',0)):
                    return self._err(403)
                pid = body.get('payment_id')
                p = db.get_pending(pid)
                if not p:
                    return self._json({'success':False,'error':'Not found'})
                stars = p.get('stars',0)
                db.add_balance(p['user_id'], stars)
                db.add_usd_revenue(round(stars * STAR_TO_USD, 4))
                db.remove_pending(pid)
                try:
                    bot.send_message(int(p['user_id']),
                        f"✅ <b>تم شحن رصيدك عبر فودافون كاش!</b>

"
                        f"📱 تمت الموافقة على الدفع
"
                        f"⭐ تمت إضافة <b>{stars} نجمة</b> لرصيدك",
                        parse_mode='HTML')
                except: pass
                self._json({'success':True})

            # ── Admin: Confirm TON Payment ──
            elif path=='/api/admin/confirm_ton':
                if not is_admin(body.get('admin_id',0)):
                    return self._err(403)
                pid = body.get('payment_id')
                p = db.confirm_ton_payment(pid)
                if p:
                    db.add_balance(p['user_id'], p['stars'])
                    try:
                        bot.send_message(int(p['user_id']),
                            f"✅ <b>تم شحن رصيدك بـ TON!</b>\n\n"
                            f"💎 الدفع: <b>{p['ton_amount']} TON</b>\n"
                            f"⭐ تمت إضافة <b>{p['stars']} نجمة</b>",
                            parse_mode='HTML')
                    except: pass
                    self._json({'success':True})
                else: self._json({'success':False,'error':'Payment not found'})

            # ── Admin: Add Admin (owner only) ──
            elif path=='/api/admin/add_admin':
                if not is_owner(body.get('admin_id',0)):
                    return self._json({'success':False,'error':'للمالك فقط'})
                new_id = body.get('new_admin_id')
                if not new_id: return self._json({'success':False,'error':'Invalid ID'})
                result = db.add_admin(new_id)
                self._json({'success':result,'error':'موجود بالفعل' if not result else ''})

            # ── Admin: Remove Admin (owner only) ──
            elif path=='/api/admin/remove_admin':
                if not is_owner(body.get('admin_id',0)):
                    return self._json({'success':False,'error':'للمالك فقط'})
                db.remove_admin(body.get('target_id'))
                self._json({'success':True})

            # ── Admin: Manage Store Categories ──
            elif path=='/api/admin/add_category':
                if not is_admin(body.get('admin_id',0)):
                    return self._err(403)
                key = body.get('key','').lower().strip().replace(' ','_')
                if not key: return self._json({'success':False,'error':'Invalid key'})
                db.categories[key] = {
                    'name': body.get('name', key),
                    'color': body.get('color','#3b82f6')
                }
                db._save('categories.json', db.categories)
                self._json({'success':True})

            elif path=='/api/admin/delete_category':
                if not is_admin(body.get('admin_id',0)):
                    return self._err(403)
                key = body.get('key','')
                if key in db.categories:
                    del db.categories[key]
                    db._save('categories.json', db.categories)
                    self._json({'success':True})
                else:
                    self._json({'success':False,'error':'Not found'})

            # ── Admin: Add Game Category ──
            elif path=='/api/admin/add_game':
                if not is_admin(body.get('admin_id',0)):
                    return self._err(403)
                key = body.get('key','').lower().strip()
                if not key: return self._json({'success':False,'error':'Invalid key'})
                db.add_game_category(key, {
                    'name': body.get('name',key),
                    'color': body.get('color','#3b82f6'),
                    'fields': body.get('fields',['player_id']),
                    'packages': body.get('packages',[])
                })
                self._json({'success':True})

            # ── Admin: Delete Game Category ──
            elif path=='/api/admin/delete_game':
                if not is_admin(body.get('admin_id',0)):
                    return self._err(403)
                self._json({'success':db.delete_game_category(body.get('key',''))})

            # ── Admin: Update Game Order Status ──
            elif path=='/api/admin/game_order_update':
                if not is_admin(body.get('admin_id',0)):
                    return self._err(403)
                oid = body.get('order_id')
                new_status = body.get('status')
                order = db.get_game_order(oid)
                if not order: return self._json({'success':False,'error':'Not found'})
                db.update_game_order(oid, {'status': new_status})
                msg_map = {
                    'done': f"✅ <b>تم شحن طلبك!</b>\n\n🎮 {order.get('game_name',order.get('game',''))}\n📦 {order.get('package_label','')}\n\nتمتع باللعب! 🎉",
                    'cancelled': f"❌ <b>تم إلغاء طلبك</b>\n\n🎮 {order.get('game_name','')}\nتم إعادة ⭐ {order.get('price',0)} نجمة لرصيدك"
                }
                # Refund if cancelled
                if new_status == 'cancelled':
                    db.add_balance(order['user_id'], order.get('price',0))
                if new_status in msg_map:
                    try: bot.send_message(int(order['user_id']), msg_map[new_status], parse_mode='HTML')
                    except: pass
                self._json({'success':True})

            elif path=='/api/admin/add_account' and is_admin(body.get('admin_id',0)):
                db.add_account({'name':body['name'],'category':body.get('category',''),
                                'price':body['price'],'stock':body['stock'],
                                'account':body['account'],'description':body.get('description','')})
                self._json({'success':True})
            elif path=='/api/admin/edit_account' and is_admin(body.get('admin_id',0)):
                self._json({'success':db.update_account(body['account_id'],body['updates'])})
            elif path=='/api/admin/delete_account' and is_admin(body.get('admin_id',0)):
                self._json({'success':db.delete_account(body['account_id'])})
            elif path=='/api/admin/add_redeem_code' and is_admin(body.get('admin_id',0)):
                self._json({'success':db.add_redeem_code(body.get('code',''),int(body.get('amount',0)))})
            elif path=='/api/admin/remove_redeem_code' and is_admin(body.get('admin_id',0)):
                self._json({'success':db.delete_redeem_code(body.get('code',''))})
            elif path=='/api/admin/add_stars' and is_admin(body.get('admin_id',0)):
                db.add_balance(body['user_id'],body['amount']); self._json({'success':True})
            elif path=='/api/admin/broadcast' and is_admin(body.get('admin_id',0)):
                sent=0
                for u in db.get_all_users():
                    try: bot.send_message(int(u['id']),body['message']); sent+=1; time.sleep(0.05)
                    except: pass
                self._json({'success':True,'sent':sent})
            else: self._err(404)
        except Exception as ex: logger.error(f"POST {path}: {ex}"); self._err(500)

    def _html(self,c):
        self.send_response(200); self.send_header('Content-type','text/html; charset=utf-8')
        self.end_headers(); self.wfile.write(c.encode())
    def _json(self,data):
        self.send_response(200); self.send_header('Content-type','application/json')
        self.send_header('Access-Control-Allow-Origin','*')
        self.end_headers(); self.wfile.write(json.dumps(data,ensure_ascii=False).encode())
    def _err(self,code): self.send_response(code); self.end_headers()
    def log_message(self,*a): pass

# ==================== BOT HANDLERS ====================
@bot.message_handler(commands=['start'])
def start(msg):
    db.get_or_create_user(msg.from_user.id,msg.from_user.username,msg.from_user.first_name)
    kb=types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🛍️ افتح المتجر",web_app=types.WebAppInfo(url=WEBAPP_URL)))
    kb.add(types.InlineKeyboardButton("🎮 شحن الألعاب",web_app=types.WebAppInfo(url=f"{WEBAPP_URL}?tab=games")))
    if is_admin(msg.from_user.id):
        kb.add(types.InlineKeyboardButton("🔧 لوحة الأدمن",web_app=types.WebAppInfo(url=f"{WEBAPP_URL}?tab=admin")))
    bot.send_message(msg.chat.id,
        f"<b>✨ SoNs Store</b>\n\n"
        f"👋 مرحباً {msg.from_user.first_name}!\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛍️ متجر حسابات مميزة\n"
        f"🎮 شحن ألعاب: PUBG • FF • MLBB • COC\n"
        f"⭐ دفع بنجوم تيليجرام\n"
        f"💎 دفع بـ TON مباشرة\n"
        f"📱 دفع بفودافون كاش\n"
        f"⚡ توصيل فوري\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👇 اضغط لفتح التطبيق",
        reply_markup=kb,parse_mode='HTML')

@bot.message_handler(commands=['addadmin'])
def add_admin_cmd(msg):
    if not is_owner(msg.from_user.id):
        return bot.reply_to(msg, "⛔ للمالك فقط")
    parts = msg.text.split()
    if len(parts) < 2:
        return bot.reply_to(msg, "الاستخدام: /addadmin [user_id]")
    try:
        new_id = int(parts[1])
        if db.add_admin(new_id):
            bot.reply_to(msg, f"✅ تمت إضافة {new_id} كأدمن")
        else:
            bot.reply_to(msg, "⚠️ هذا المستخدم أدمن بالفعل")
    except: bot.reply_to(msg, "❌ معرف غير صحيح")

@bot.message_handler(commands=['removeadmin'])
def remove_admin_cmd(msg):
    if not is_owner(msg.from_user.id):
        return bot.reply_to(msg, "⛔ للمالك فقط")
    parts = msg.text.split()
    if len(parts) < 2:
        return bot.reply_to(msg, "الاستخدام: /removeadmin [user_id]")
    try:
        rem_id = int(parts[1])
        db.remove_admin(rem_id)
        bot.reply_to(msg, f"✅ تمت إزالة {rem_id} من الأدمن")
    except: bot.reply_to(msg, "❌ خطأ")

@bot.message_handler(commands=['admins'])
def list_admins_cmd(msg):
    if not is_owner(msg.from_user.id):
        return bot.reply_to(msg, "⛔ للمالك فقط")
    a = db.get_admins_list()
    txt = f"🔧 <b>الأدمن الحاليون:</b>\n"
    for aid in a.get('admins',[]):
        txt += f"• <code>{aid}</code>\n"
    if not a.get('admins'): txt += "لا يوجد أدمن حتى الآن"
    bot.reply_to(msg, txt, parse_mode='HTML')

@bot.pre_checkout_query_handler(func=lambda q:True)
def pre_checkout(q):
    p=db.get_pending(q.invoice_payload)
    bot.answer_pre_checkout_query(q.id,ok=bool(p),error_message="انتهت الجلسة" if not p else None)

@bot.message_handler(content_types=['successful_payment'])
def payment_ok(msg):
    pay=msg.successful_payment; p=db.get_pending(pay.invoice_payload)
    if not p: return
    if p['type']=='deposit':
        db.add_balance(p['user_id'],p['amount'])
        bot.send_message(p['user_id'],
            f"✅ <b>تم الشحن!</b>\n⭐ تمت إضافة <b>{p['amount']} نجمة</b>",
            parse_mode='HTML')
    db.remove_pending(pay.invoice_payload)

# ==================== RUN ====================
def run_web():
    HTTPServer((WEBAPP_HOST,WEBAPP_PORT),Handler).serve_forever()

def run_bot():
    while True:
        try: bot.remove_webhook(); bot.infinity_polling(timeout=60)
        except Exception as ex: logger.error(f"Bot: {ex}"); time.sleep(10)

if __name__=="__main__":
    print("\n"+"═"*60)
    print("✨  SoNs Store — Premium + Game Charging  ✨")
    print(f"🌐  http://localhost:{WEBAPP_PORT}")
    print(f"💎  TON Wallet: {TON_WALLET_ADDRESS[:20]}...")
    print(f"⭐  Star-to-TON rate: {STAR_TO_TON} TON per star")
    print(f"🎮  Game categories: {len(db.get_game_categories())}")
    print(f"🔧  Owner ID: {OWNER_ID}")
    print("═"*60+"\n")
    print("📋 TON Setup:")
    print("   1. Set TON_WALLET_ADDRESS env var to your TON wallet")
    print("   2. Set STAR_TO_TON rate (default 0.02)")
    print("   3. Upload tonconnect-manifest.json to your WEBAPP_URL")
    print("   4. Admin can confirm TON payments from admin panel\n")
    threading.Thread(target=run_web, daemon=True).start()
    run_bot()
