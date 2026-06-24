# -*- coding: utf-8 -*-
"""
SoNs Store — Telegram Mini App Store
Payment: Telegram Stars + Binance Pay (manual confirm with tracking memo)
All products are fully managed by the store owner/admin via the admin panel.
"""

import telebot
from telebot import types
from telebot.types import LabeledPrice
import time, json, logging, uuid, os, threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==================== CONFIG ====================
BOT_TOKEN   = os.environ.get('BOT_TOKEN', '8353606401:AAGU_I2A3OQvbYcPy7OCxWbwi2pSe_dN4ns').strip()
OWNER_ID    = int(os.environ.get('OWNER_ID', '6285783725'))
WEBAPP_HOST = "0.0.0.0"
WEBAPP_PORT = int(os.environ.get('PORT', 5050))
WEBAPP_URL  = os.environ.get('WEBAPP_URL', 'https://ninjadev.tech')

# Binance Pay merchant info (shown to the buyer to send payment + tracking memo)
BINANCE_PAY_ID = os.environ.get('BINANCE_PAY_ID', '1139696096')   # Your Binance Pay ID / Pay-ID
BINANCE_PAY_QR = os.environ.get('BINANCE_PAY_QR', '')            # Optional: link to a QR image
STAR_TO_USD    = float(os.environ.get('STAR_TO_USD', '0.013'))   # used only for admin USD stats

DEFAULT_REDEEM_CODES = {"WELCOME25": 500, "FREE10": 200}

# ==================== AUTH HELPERS ====================
def load_admins():
    try:
        with open('bot_data/admins.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'owners': [OWNER_ID], 'admins': []}

def save_admins(data):
    os.makedirs('bot_data', exist_ok=True)
    with open('bot_data/admins.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def is_owner(uid: int) -> bool:
    return int(uid) == int(OWNER_ID)

def is_admin(uid: int) -> bool:
    a = load_admins()
    try:
        return int(uid) in [int(x) for x in a.get('admins', [])] or is_owner(uid)
    except Exception:
        return is_owner(uid)

# ==================== DATABASE ====================
class Database:
    def __init__(self):
        os.makedirs('bot_data', exist_ok=True)
        self.products      = self._load('products.json', [])
        self.categories    = self._load('categories.json', {})
        self.users         = self._load('users.json', [])
        self.purchases     = self._load('purchases.json', [])
        self.pending       = self._load('pending.json', {})
        self.balances      = self._load('balances.json', [])
        self.redeem_codes  = self._load('redeem_codes.json', DEFAULT_REDEEM_CODES)
        self.used_codes    = self._load('used_codes.json', [])
        self.stats         = self._load('stats.json', {'revenue': 0, 'purchases': 0})
        self.admins        = self._load('admins.json', {'owners': [OWNER_ID], 'admins': []})
        self.binance_pays   = self._load('binance_payments.json', {})
        self.revenue_usd    = self._load('revenue_usd.json', {'total_usd': 0.0})
        self.banners        = self._load('banners.json', [])
        self.binance_orders = self._load('binance_orders.json', {})

    def _load(self, n, d):
        try:
            with open(f'bot_data/{n}', 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return d

    def _save(self, n, data):
        with open(f'bot_data/{n}', 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    # ── Balances ──────────────────────────────────────────────
    def get_balance(self, uid):
        b = next((b for b in self.balances if b.get('user_id') == str(uid)), None)
        return b['stars'] if b else 0

    def add_balance(self, uid, amount):
        b = next((b for b in self.balances if b.get('user_id') == str(uid)), None)
        if b:
            b['stars'] += amount
        else:
            self.balances.append({'user_id': str(uid), 'stars': amount})
        self._save('balances.json', self.balances)

    def deduct_balance(self, uid, amount):
        b = next((b for b in self.balances if b.get('user_id') == str(uid)), None)
        if b and b['stars'] >= amount:
            b['stars'] -= amount
            self._save('balances.json', self.balances)
            return True
        return False

    # ── Categories ────────────────────────────────────────────
    def add_category(self, key, data):
        self.categories[key] = data
        self._save('categories.json', self.categories)

    def delete_category(self, key):
        if key in self.categories:
            del self.categories[key]
            self._save('categories.json', self.categories)
            return True
        return False

    # ── Products (fully owner-managed: name, image, price, stock, content) ──
    def get_products(self):
        return self.products

    def get_product(self, pid):
        return next((p for p in self.products if p.get('id') == pid), None)

    def add_product(self, data):
        data['id'] = uuid.uuid4().hex[:8]
        data['sold'] = 0
        data['created'] = datetime.now().isoformat()
        self.products.append(data)
        self._save('products.json', self.products)
        return data['id']

    def update_product(self, pid, upd):
        p = self.get_product(pid)
        if p:
            p.update(upd)
            self._save('products.json', self.products)
            return True
        return False

    def delete_product(self, pid):
        p = self.get_product(pid)
        if p:
            self.products.remove(p)
            self._save('products.json', self.products)
            return True
        return False

    def update_stock(self, pid, s):
        p = self.get_product(pid)
        if p:
            p['stock'] = s
            self._save('products.json', self.products)

    def increment_sold(self, pid):
        p = self.get_product(pid)
        if p:
            p['sold'] = p.get('sold', 0) + 1
            self._save('products.json', self.products)

    # ── Stats ─────────────────────────────────────────────────
    def get_stats(self):
        return {
            'total_products': len(self.products),
            'total_sold': sum(p.get('sold', 0) for p in self.products),
            'revenue': sum(p.get('sold', 0) * p.get('price', 0) for p in self.products),
            'total_purchases': self.stats.get('purchases', 0),
            'total_usd': self.get_total_usd(),
        }

    # ── Users ─────────────────────────────────────────────────
    def get_or_create_user(self, uid, username=None, first_name=None):
        u = next((u for u in self.users if u.get('id') == str(uid)), None)
        if u:
            u['last_active'] = datetime.now().isoformat()
            self._save('users.json', self.users)
            return u
        nu = {'id': str(uid), 'username': username, 'first_name': first_name,
              'spent': 0, 'purchases': 0, 'joined': datetime.now().isoformat(),
              'last_active': datetime.now().isoformat()}
        self.users.append(nu)
        self._save('users.json', self.users)
        return nu

    def update_user_stats(self, uid, amount):
        u = next((u for u in self.users if u.get('id') == str(uid)), None)
        if u:
            u['spent'] = u.get('spent', 0) + amount
            u['purchases'] = u.get('purchases', 0) + 1
            self._save('users.json', self.users)

    def get_user_stats(self, uid):
        u = next((u for u in self.users if u.get('id') == str(uid)), None)
        return {'spent': u.get('spent', 0), 'purchases': u.get('purchases', 0),
                 'joined': u.get('joined', '')} if u else {'spent': 0, 'purchases': 0}

    def get_all_users(self):
        return self.users

    def get_user_count(self):
        return len(self.users)

    # ── Redeem Codes ──────────────────────────────────────────
    def add_redeem_code(self, code, amount):
        code = code.strip().upper()
        if not code or amount <= 0:
            return False
        self.redeem_codes[code] = amount
        self._save('redeem_codes.json', self.redeem_codes)
        return True

    def delete_redeem_code(self, code):
        code = code.strip().upper()
        if code in self.redeem_codes:
            del self.redeem_codes[code]
            self._save('redeem_codes.json', self.redeem_codes)
            return True
        return False

    def redeem_code(self, uid, code):
        code = code.strip().upper()
        if code in self.used_codes:
            return 0
        amount = self.redeem_codes.get(code, 0)
        if amount > 0:
            self.used_codes.append(code)
            self._save('used_codes.json', self.used_codes)
            self.add_balance(uid, amount)
            return amount
        return 0

    def get_redeem_codes(self):
        return self.redeem_codes

    # ── Pending (Stars invoices) ──────────────────────────────
    def add_pending(self, pid, data):
        self.pending[pid] = data
        self._save('pending.json', self.pending)

    def get_pending(self, pid):
        return self.pending.get(pid)

    def remove_pending(self, pid):
        if pid in self.pending:
            del self.pending[pid]
            self._save('pending.json', self.pending)

    # ── Purchases ─────────────────────────────────────────────
    def add_purchase(self, data):
        data['id'] = uuid.uuid4().hex[:8]
        data['timestamp'] = datetime.now().isoformat()
        self.purchases.append(data)
        self._save('purchases.json', self.purchases)
        price = data.get('price', 0)
        self.stats['revenue'] = self.stats.get('revenue', 0) + price
        self.stats['purchases'] = self.stats.get('purchases', 0) + 1
        self._save('stats.json', self.stats)
        self.add_usd_revenue(round(price * STAR_TO_USD, 4))

    def get_total_usd(self):
        return self.revenue_usd.get('total_usd', 0.0)

    def add_usd_revenue(self, usd_amount):
        self.revenue_usd['total_usd'] = round(self.revenue_usd.get('total_usd', 0) + usd_amount, 4)
        self._save('revenue_usd.json', self.revenue_usd)

    def get_user_purchases(self, uid, limit=30):
        ups = [p for p in self.purchases if p.get('user_id') == str(uid)]
        return sorted(ups, key=lambda x: x.get('timestamp', ''), reverse=True)[:limit]

    # ── Binance Pay (manual, tracked by memo/comment = order id) ─
    def create_binance_payment(self, uid, stars):
        pid = f"BNB-{uuid.uuid4().hex[:8].upper()}"
        usd = round(stars * STAR_TO_USD, 2)
        self.binance_pays[pid] = {
            'user_id': str(uid), 'stars': stars, 'usd': usd,
            'status': 'pending', 'created': datetime.now().isoformat()
        }
        self._save('binance_payments.json', self.binance_pays)
        return pid, usd

    def confirm_binance_payment(self, pid):
        p = self.binance_pays.get(pid)
        if p and p['status'] != 'confirmed':
            p['status'] = 'confirmed'
            p['confirmed_at'] = datetime.now().isoformat()
            self._save('binance_payments.json', self.binance_pays)
            return p
        return None

    def get_binance_payment(self, pid):
        return self.binance_pays.get(pid)

    # ── Banners (animated ads carousel) ──────────────────────
    def get_banners(self):
        return self.banners

    def add_banner(self, data):
        data['id'] = uuid.uuid4().hex[:8]
        data['created'] = datetime.now().isoformat()
        self.banners.append(data)
        self._save('banners.json', self.banners)
        return data['id']

    def delete_banner(self, bid):
        b = next((x for x in self.banners if x.get('id') == bid), None)
        if b:
            self.banners.remove(b)
            self._save('banners.json', self.banners)
            return True
        return False

    # ── Binance Pay — direct product purchase orders ─────────
    def create_binance_order(self, uid, product_id, price_usd, product_name):
        oid = f"BNB-{uuid.uuid4().hex[:8].upper()}"
        self.binance_orders[oid] = {
            'user_id': str(uid), 'product_id': product_id, 'product_name': product_name,
            'usd': price_usd, 'status': 'pending', 'created': datetime.now().isoformat()
        }
        self._save('binance_orders.json', self.binance_orders)
        return oid

    def get_binance_order(self, oid):
        return self.binance_orders.get(oid)

    def confirm_binance_order(self, oid):
        o = self.binance_orders.get(oid)
        if o and o['status'] != 'confirmed':
            o['status'] = 'confirmed'
            o['confirmed_at'] = datetime.now().isoformat()
            self._save('binance_orders.json', self.binance_orders)
            return o
        return None

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
        except Exception:
            return False

    def get_admins_list(self):
        return load_admins()


db = Database()

if not db.categories:
    db.categories = {
        "general": {"name": "عام", "color": "#3b82f6"},
    }
    db._save('categories.json', db.categories)

bot = telebot.TeleBot(BOT_TOKEN)

COMMISSION_RATE = 0.0  # غيّرها لو حابب تضيف عمولة على عمليات الشراء

def calc_price_with_commission(price: int) -> int:
    return int(round(price + price * COMMISSION_RATE))


# ==================== HTML WEB APP ====================
HTML_WEBAPP = r'''<!DOCTYPE html>
<html lang="ar" dir="rtl" id="root">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>SoNs</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
:root{
  --bg:#0b0c10;--card:#15171f;--card2:#1b1e29;--card3:#222637;
  --txt:#f2f3f7;--gray:#888fa8;
  --bdr:rgba(255,255,255,.08);--sh:0 8px 28px rgba(0,0,0,.45);
  --blue:#2f6bff;--blue2:#1947c9;
  --gold:#ffcf3f;--gold2:#c99a10;
  --bnb:#f3ba2f;--bnb2:#c8951a;
  --success:#22c55e;--danger:#ef4444;--warn:#f59e0b;
  --r:22px;
}
body{font-family:-apple-system,BlinkMacSystemFont,'SF Pro Text',sans-serif;background:radial-gradient(circle at 30% 0%,#101522 0%,var(--bg) 55%);color:var(--txt);min-height:100vh}

/* SPLASH — Portals-style */
#splash{position:fixed;inset:0;z-index:9999;background:radial-gradient(circle at 50% 30%,#16203f 0%,#070810 70%);display:flex;flex-direction:column;align-items:center;justify-content:center;transition:opacity .5s,transform .5s;overflow:hidden}
#splash.out{opacity:0;transform:scale(1.06);pointer-events:none}
.sp-star{position:absolute;color:rgba(255,255,255,.6);font-size:14px;animation:tw 2.6s ease-in-out infinite}
.sp-glow{position:absolute;width:220px;height:220px;border-radius:50%;background:radial-gradient(circle,rgba(47,107,255,.5),transparent 70%);filter:blur(10px);top:8%;left:10%}
@keyframes tw{0%,100%{opacity:.2}50%{opacity:1}}
.sp-logo{width:96px;height:96px;border-radius:30px;background:linear-gradient(150deg,var(--blue),var(--blue2));display:flex;align-items:center;justify-content:center;margin-bottom:22px;box-shadow:0 0 50px rgba(47,107,255,.45);position:relative;z-index:2}
.sp-title{font-size:34px;font-weight:800;color:#fff;display:flex;align-items:center;gap:8px;position:relative;z-index:2}
.sp-sub{font-size:13px;color:rgba(255,255,255,.4);margin-top:8px;margin-bottom:40px;position:relative;z-index:2}
.sp-track{width:160px;height:3px;background:rgba(255,255,255,.1);border-radius:99px;overflow:hidden;position:relative;z-index:2}
.sp-bar{height:100%;width:0;background:linear-gradient(90deg,var(--blue),var(--gold));border-radius:99px;animation:sBar 1.4s cubic-bezier(.4,0,.2,1) forwards}
@keyframes sBar{0%{width:0}45%{width:60%}80%{width:90%}100%{width:100%}}

/* TOPBAR */
.topbar{position:sticky;top:0;z-index:60;display:flex;align-items:center;justify-content:space-between;padding:13px 16px 10px;background:rgba(11,12,16,.9);backdrop-filter:blur(20px) saturate(1.7);border-bottom:.5px solid var(--bdr)}
.tb-brand{display:flex;align-items:center;gap:9px}
.tb-logo{width:30px;height:30px;border-radius:10px;background:linear-gradient(150deg,var(--blue),var(--blue2));display:flex;align-items:center;justify-content:center}
.tb-name{font-size:17px;font-weight:800;color:var(--txt)}
.tb-right{display:flex;gap:8px;align-items:center}
.bal-chip{display:flex;align-items:center;gap:5px;background:var(--card2);border:1px solid var(--bdr);border-radius:20px;padding:6px 12px;font-size:12px;font-weight:700;color:var(--gold)}
.lang-btn{background:var(--blue);border:none;border-radius:20px;padding:6px 12px;font-size:12px;font-weight:700;color:#fff;cursor:pointer;font-family:inherit}

#app{max-width:520px;margin:0 auto;padding:14px 14px 100px}

/* USER CARD */
.uid-card{background:linear-gradient(135deg,#171a26,#1c2236);border-radius:24px;padding:18px;margin-bottom:14px;display:flex;align-items:center;gap:14px;cursor:pointer;position:relative;overflow:hidden;box-shadow:var(--sh);border:1px solid var(--bdr)}
.uid-card::before{content:'';position:absolute;top:0;left:0;right:0;height:2.5px;background:linear-gradient(90deg,var(--blue),var(--gold))}
.uid-av{width:58px;height:58px;border-radius:50%;background:linear-gradient(150deg,var(--blue),var(--blue2));display:flex;align-items:center;justify-content:center;font-size:24px;font-weight:800;color:#fff;border:2px solid rgba(255,255,255,.12);overflow:hidden;flex-shrink:0}
.uid-av img{width:100%;height:100%;object-fit:cover}
.uid-info{flex:1;min-width:0}
.uid-name{font-size:16px;font-weight:800;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.uid-handle{font-size:11px;color:rgba(255,255,255,.35);font-family:monospace;margin-top:2px}
.uid-arr{color:rgba(255,255,255,.18);font-size:22px}

/* BALANCE CIRCLE */
.bal-circles-row{display:flex;gap:14px;margin-bottom:18px;justify-content:center}
.bal-circle-wrap{display:flex;flex-direction:column;align-items:center;gap:8px;flex:1}
.bal-circle{position:relative;width:104px;height:104px}
.bal-ring-svg{position:absolute;inset:0;width:100%;height:100%}
.bal-circle-inner{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:1px}
.bal-circle-val{font-size:16px;font-weight:800;color:var(--txt)}
.bal-circle-lbl{font-size:9px;color:var(--gray);font-weight:700;text-transform:uppercase;letter-spacing:.5px}
.bal-circle-add{width:38px;height:38px;border-radius:50%;border:none;color:#fff;font-size:21px;display:flex;align-items:center;justify-content:center;cursor:pointer;background:var(--blue);box-shadow:0 4px 16px rgba(47,107,255,.35)}

/* SECTION */
.sec-h{display:flex;align-items:center;justify-content:space-between;margin:16px 0 12px}
.sec-t{font-size:18px;font-weight:800;color:var(--txt)}

/* ROUNDED TRANSPARENT ICON CARDS (Portals-style) */
.cat-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:13px}
.icon-card{position:relative;border-radius:26px;aspect-ratio:1/1;overflow:hidden;cursor:pointer;box-shadow:var(--sh);border:1px solid var(--bdr);background:var(--card2)}
.icon-card:active{transform:scale(.96)}
.icon-card .ic-bg{position:absolute;inset:0;opacity:.9}
.icon-card .ic-glass{position:absolute;inset:0;background:linear-gradient(160deg,rgba(255,255,255,.14),rgba(255,255,255,0) 40%)}
.icon-card .ic-emoji{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:46px;filter:drop-shadow(0 6px 14px rgba(0,0,0,.35))}
.icon-card img.ic-img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.icon-card .ic-label{position:absolute;bottom:0;left:0;right:0;padding:10px 12px;background:linear-gradient(to top,rgba(0,0,0,.78),transparent);font-size:13px;font-weight:700;color:#fff}
.icon-card .ic-badge{position:absolute;top:8px;right:8px;background:rgba(0,0,0,.45);border-radius:14px;padding:3px 9px;font-size:11px;font-weight:700;color:#fff;backdrop-filter:blur(4px)}

/* ANIMATED BANNER ADS CAROUSEL */
.banner-carousel{position:relative;border-radius:24px;overflow:hidden;margin-bottom:16px;box-shadow:var(--sh);border:1px solid var(--bdr);background:var(--card2)}
.banner-track{display:flex;transition:transform .5s cubic-bezier(.4,0,.2,1)}
.banner-slide{min-width:100%;aspect-ratio:16/8.2;position:relative;display:flex;align-items:center;padding:22px;overflow:hidden;cursor:pointer}
.banner-slide img.bn-bg{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.banner-slide .bn-overlay{position:absolute;inset:0;background:linear-gradient(100deg,rgba(0,0,0,.55) 0%,rgba(0,0,0,.15) 55%,transparent 100%)}
.banner-slide .bn-content{position:relative;z-index:2;max-width:70%}
.banner-slide .bn-title{font-size:22px;font-weight:900;color:#fff;line-height:1.2;margin-bottom:8px;text-shadow:0 2px 10px rgba(0,0,0,.4)}
.banner-slide .bn-pill{display:inline-flex;align-items:center;background:rgba(255,255,255,.92);color:#1a1a2e;border-radius:20px;padding:7px 16px;font-size:13px;font-weight:800}
.banner-dots{position:absolute;bottom:10px;left:0;right:0;display:flex;justify-content:center;gap:5px;z-index:3}
.banner-dot{width:6px;height:6px;border-radius:50%;background:rgba(255,255,255,.35);transition:all .3s}
.banner-dot.active{width:18px;background:#fff}
@keyframes bnFloat{0%,100%{transform:translateY(0)}50%{transform:translateY(-6px)}}
.bn-float{animation:bnFloat 3s ease-in-out infinite}
.prod-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}
.prod-tile{background:var(--card2);border-radius:24px;padding:14px;text-align:center;box-shadow:var(--sh);border:1px solid var(--bdr);position:relative;overflow:hidden}
.prod-tile .pt-img{width:100%;aspect-ratio:1/1;border-radius:18px;display:flex;align-items:center;justify-content:center;font-size:42px;margin-bottom:10px;overflow:hidden;background:radial-gradient(circle at 40% 30%,rgba(255,255,255,.08),transparent 70%)}
.prod-tile .pt-img img{width:100%;height:100%;object-fit:cover;border-radius:18px}
.prod-tile .pt-name{font-size:13px;font-weight:700;color:var(--txt);margin-bottom:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.prod-tile .pt-price{display:inline-flex;align-items:center;gap:4px;background:rgba(255,207,63,.12);color:var(--gold);border-radius:20px;padding:4px 10px;font-size:12px;font-weight:800;margin-bottom:8px}
.prod-tile .pt-stock{font-size:10px;color:var(--gray);margin-bottom:8px}
.pt-buy{width:100%;background:var(--blue);border:none;border-radius:16px;padding:9px;color:#fff;font-weight:700;font-size:12.5px;cursor:pointer;font-family:inherit}
.pt-buy:disabled{background:var(--card3);color:var(--gray)}

/* TABS — separate floating glassy capsules, glow blur on active (exact reference look) */
.tab-bar-wrap{position:fixed;bottom:0;left:0;right:0;z-index:100;display:flex;justify-content:center;padding:0 14px calc(12px + env(safe-area-inset-bottom))}
.tab-bar{
  display:flex;align-items:center;gap:8px;
  max-width:520px;width:100%;
}
.tab-btn{
  flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;
  background:rgba(22,24,32,.62);
  backdrop-filter:saturate(2.4) blur(18px);-webkit-backdrop-filter:saturate(2.4) blur(18px);
  border:1px solid rgba(255,255,255,.06);
  cursor:pointer;
  color:rgba(255,255,255,.4);font-size:10.5px;font-weight:600;font-family:inherit;
  border-radius:24px;position:relative;transition:all .28s cubic-bezier(.4,0,.2,1);
  padding:10px 6px;
  overflow:visible;
}
.tab-pill{width:26px;height:26px;border-radius:50%;display:flex;align-items:center;justify-content:center;transition:transform .25s}
.tab-pill svg{width:18px;height:18px;transition:transform .25s}
.tab-lbl{transition:opacity .2s,font-weight .2s}
.tab-btn.active{
  flex:1.6;
  color:#fff;
  background:linear-gradient(180deg,#3a8bff,#1456d6);
  border-color:rgba(255,255,255,.12);
  box-shadow:0 0 0 1px rgba(255,255,255,.08) inset,0 10px 30px rgba(40,120,255,.6),0 0 40px rgba(40,120,255,.45);
}
.tab-btn.active .tab-lbl{font-weight:800}
.tab-btn:active{transform:scale(.95)}

/* MODAL */
.modal{display:none;position:fixed;inset:0;z-index:1000;background:rgba(0,0,0,.75);backdrop-filter:blur(6px);align-items:flex-end;justify-content:center}
.modal-box{background:var(--card);border-radius:28px 28px 0 0;padding:0 0 20px;width:100%;max-width:520px;max-height:92vh;overflow-y:auto;border-top:1px solid var(--bdr)}
.m-handle{width:36px;height:4px;background:rgba(255,255,255,.15);border-radius:2px;margin:12px auto 16px}
.m-head{display:flex;align-items:center;justify-content:space-between;padding:0 20px 14px;border-bottom:.5px solid var(--bdr)}
.m-head h3{font-size:17px;font-weight:800}
.m-close{background:var(--card2);border:none;border-radius:50%;width:30px;height:30px;font-size:16px;cursor:pointer;color:var(--gray)}
.m-body{padding:16px 20px 0}
.inp{width:100%;padding:13px;border:1.5px solid var(--bdr);border-radius:13px;background:var(--card2);color:var(--txt);font-size:15px;text-align:center;margin-bottom:12px;font-family:inherit;outline:none}
textarea.inp{text-align:right;resize:none}
.sbtn{background:var(--blue);border:none;border-radius:15px;padding:14px;width:100%;color:#fff;font-size:15px;font-weight:800;cursor:pointer;font-family:inherit}
.sbtn.bnb{background:linear-gradient(135deg,var(--bnb),var(--bnb2));color:#1a1300}
.sbtn.ghost{background:var(--card2);color:var(--txt)}
.pay-tabs{display:flex;gap:8px;margin-bottom:16px;background:var(--card2);border-radius:14px;padding:4px}
.pay-tab{flex:1;border:none;border-radius:11px;padding:9px 6px;font-size:13px;font-weight:700;cursor:pointer;background:transparent;color:var(--gray);font-family:inherit}
.pay-tab.active{background:var(--card3);color:var(--txt)}
.amt-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:14px}
.amt-btn{background:var(--card2);border:1.5px solid var(--bdr);border-radius:12px;padding:10px 6px;text-align:center;cursor:pointer;font-size:13px;font-weight:700;color:var(--txt)}
.bnb-box{background:rgba(243,186,47,.08);border:1.5px solid rgba(243,186,47,.3);border-radius:16px;padding:16px;text-align:center}
.bnb-addr{background:var(--card2);border-radius:12px;padding:10px;font-family:monospace;font-size:13px;color:var(--gold);cursor:pointer;margin:10px 0;word-break:break-all}
.pm-opt.selected{border-color:var(--blue)!important;background:rgba(47,107,255,.18)!important;color:#fff!important}
.order-badge{display:inline-block;padding:3px 10px;border-radius:20px;font-size:11px;font-weight:700}
.order-badge.pending{background:rgba(245,158,11,.15);color:var(--warn)}
.order-badge.confirmed{background:rgba(34,197,94,.15);color:var(--success)}
.stat-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-top:14px}
.stat-box{background:var(--card2);border-radius:18px;padding:16px;text-align:center;box-shadow:var(--sh);border:1px solid var(--bdr)}
.stat-n{font-size:21px;font-weight:800;color:var(--blue)}.stat-l{font-size:11px;color:var(--gray);margin-top:4px}
.purch-item{background:var(--card2);border-radius:18px;padding:14px 16px;margin-bottom:10px;box-shadow:var(--sh);display:flex;align-items:center;gap:12px;border:1px solid var(--bdr);border-right:4px solid var(--gold)}
.adm-card{background:var(--card2);border-radius:18px;padding:14px 16px;margin-bottom:10px;display:flex;align-items:center;gap:14px;cursor:pointer;box-shadow:var(--sh);border:1px solid var(--bdr)}
.adm-ic{width:46px;height:46px;border-radius:14px;background:var(--card3);display:flex;align-items:center;justify-content:center;flex-shrink:0;font-size:22px}
.adm-info{flex:1}.adm-t{font-size:15px;font-weight:700}.adm-d{font-size:11px;color:var(--gray)}
.adm-arr{color:var(--gray);font-size:20px}
.adm-stats{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-bottom:16px}
.del-btn{background:rgba(239,68,68,.15);color:var(--danger);border:1px solid rgba(239,68,68,.3);border-radius:20px;padding:6px 13px;cursor:pointer;font-size:12px;font-family:inherit;font-weight:700}
.edit-btn{background:rgba(47,107,255,.15);color:var(--blue);border:1px solid rgba(47,107,255,.3);border-radius:20px;padding:6px 13px;cursor:pointer;font-size:12px;margin-inline-start:6px;font-family:inherit;font-weight:700}
.done-btn{background:rgba(34,197,94,.15);color:var(--success);border:1px solid rgba(34,197,94,.3);border-radius:20px;padding:6px 13px;cursor:pointer;font-size:12px;font-family:inherit;font-weight:700}
.back-btn{display:inline-flex;align-items:center;gap:6px;background:var(--card2);border:1px solid var(--bdr);border-radius:22px;padding:8px 16px;font-size:14px;color:var(--blue);cursor:pointer;margin-bottom:14px;font-family:inherit;font-weight:700}
.toast{position:fixed;bottom:92px;left:16px;right:16px;background:var(--card2);color:var(--txt);padding:13px 16px;border-radius:15px;text-align:center;z-index:2000;font-size:14px;font-weight:600;border:1px solid var(--bdr);border-left:4px solid var(--gold)}
.loading{text-align:center;padding:60px 0}
.spinner{width:36px;height:36px;border:3px solid var(--bdr);border-top-color:var(--blue);border-radius:50%;animation:spin 1s linear infinite;margin:0 auto 14px}
@keyframes spin{to{transform:rotate(360deg)}}
.empty{text-align:center;padding:60px 20px;color:var(--gray);font-size:15px}
.wlc-wrap{min-height:calc(100vh - 80px);display:flex;align-items:center;justify-content:center}
.wlc-card{background:linear-gradient(135deg,#171a26,#1c2236);border-radius:32px;padding:36px 22px;text-align:center;width:100%;box-shadow:0 20px 60px rgba(0,0,0,.6);border:1px solid var(--bdr)}
.wlc-t{font-size:26px;font-weight:800;color:#fff;margin:18px 0 10px}
.wlc-s{font-size:13px;color:rgba(255,255,255,.4);line-height:1.7;margin-bottom:26px}
.wlc-btns{display:flex;gap:10px;justify-content:center;flex-wrap:wrap}
.wlc-btn{background:var(--blue);border:none;border-radius:999px;color:#fff;padding:14px 26px;font-size:15px;font-weight:800;cursor:pointer;font-family:inherit}
.wlc-btn.sec{background:rgba(255,255,255,.08)}
</style>
</head>
<body>

<div id="splash">
  <div class="sp-glow"></div>
  <div class="sp-logo">
    <svg width="48" height="48" viewBox="0 0 48 48" fill="none">
      <polygon points="24,6 30,18 42,19 33,28 36,40 24,33 12,40 15,28 6,19 18,18" fill="#FFCF3F"/>
    </svg>
  </div>
  <div class="sp-title">SoNs</div>
  <div class="sp-sub">متجرك الرقمي الموثوق</div>
  <div class="sp-track"><div class="sp-bar"></div></div>
</div>

<div class="topbar">
  <div class="tb-brand">
    <div class="tb-logo">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="white">
        <polygon points="12,2 15.09,8.26 22,9.27 17,14.14 18.18,21.02 12,17.77 5.82,21.02 7,14.14 2,9.27 8.91,8.26"/>
      </svg>
    </div>
    <div class="tb-name">SoNs</div>
  </div>
  <div class="tb-right">
    <div class="bal-chip">⭐ <span id="topBal">0</span></div>
    <button class="lang-btn" id="langBtn" onclick="toggleLang()">EN</button>
  </div>
</div>

<div id="app"></div>

<div class="tab-bar-wrap">
<nav class="tab-bar">
  <button class="tab-btn" id="tab-store" onclick="switchTab('store')">
    <div class="tab-pill"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2L3 6v14a2 2 0 002 2h14a2 2 0 002-2V6l-3-4z"/><line x1="3" y1="6" x2="21" y2="6"/><path d="M16 10a4 4 0 01-8 0"/></svg></div>
    <span class="tab-lbl" data-ar="المتجر" data-en="Store">المتجر</span>
  </button>
  <button class="tab-btn" id="tab-purchases" onclick="switchTab('purchases')">
    <div class="tab-pill"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2"/><rect x="9" y="3" width="6" height="4" rx="1"/><path d="M9 12h6M9 16h4"/></svg></div>
    <span class="tab-lbl" data-ar="طلباتي" data-en="Orders">طلباتي</span>
  </button>
  <button class="tab-btn" id="tab-profile" onclick="switchTab('profile')">
    <div class="tab-pill"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/><circle cx="12" cy="7" r="4"/></svg></div>
    <span class="tab-lbl" data-ar="ملفي" data-en="Profile">ملفي</span>
  </button>
  <button class="tab-btn" id="tab-admin" onclick="switchTab('admin')" style="display:none">
    <div class="tab-pill"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.07 4.93l-1.41 1.41M4.93 4.93l1.41 1.41M12 2v2m0 18v-2m7.07-2.93l-1.41-1.41M4.93 19.07l1.41-1.41M22 12h-2M4 12H2"/></svg></div>
    <span class="tab-lbl" data-ar="أدمن" data-en="Admin">أدمن</span>
  </button>
</nav>
</div>

<!-- DEPOSIT MODAL -->
<div id="depositModal" class="modal" onclick="if(event.target===this)cModal('depositModal')">
  <div class="modal-box">
    <div class="m-handle"></div>
    <div class="m-head"><h3>⭐ شحن الرصيد</h3><button class="m-close" onclick="cModal('depositModal')">✕</button></div>
    <div class="m-body">
      <div class="pay-tabs">
        <button class="pay-tab active" id="ptab-stars" onclick="setPayTab('stars')">⭐ Stars</button>
        <button class="pay-tab" id="ptab-bnb" onclick="setPayTab('bnb')">🟡 Binance Pay</button>
      </div>
      <div id="sec-stars">
        <div class="amt-grid">
          <div class="amt-btn" onclick="setA(50)">50 ⭐</div><div class="amt-btn" onclick="setA(100)">100 ⭐</div>
          <div class="amt-btn" onclick="setA(250)">250 ⭐</div><div class="amt-btn" onclick="setA(500)">500 ⭐</div>
          <div class="amt-btn" onclick="setA(1000)">1000 ⭐</div><div class="amt-btn" onclick="setA(2500)">2500 ⭐</div>
        </div>
        <input type="number" id="starsAmt" class="inp" placeholder="أو أدخل مبلغ مخصص..." min="1">
        <button class="sbtn" onclick="depositStars()">⭐ شحن بنجوم تيليجرام</button>
      </div>
      <div id="sec-bnb" style="display:none">
        <div class="amt-grid">
          <div class="amt-btn" onclick="setBA(100)">100 ⭐</div><div class="amt-btn" onclick="setBA(250)">250 ⭐</div>
          <div class="amt-btn" onclick="setBA(500)">500 ⭐</div><div class="amt-btn" onclick="setBA(1000)">1000 ⭐</div>
          <div class="amt-btn" onclick="setBA(2500)">2500 ⭐</div><div class="amt-btn" onclick="setBA(5000)">5000 ⭐</div>
        </div>
        <input type="number" id="bnbAmt" class="inp" placeholder="عدد النجوم المطلوبة..." min="1">
        <button class="sbtn bnb" onclick="initBinancePay()">🟡 إنشاء طلب دفع Binance Pay</button>
        <div id="bnb-result" style="display:none;margin-top:14px">
          <div class="bnb-box">
            <div style="font-weight:800;margin-bottom:8px">📋 تفاصيل الدفع</div>
            <div>💵 المبلغ: <b id="bnb-usd" style="color:var(--gold)"></b></div>
            <div style="margin-top:4px">⭐ النجوم: <b id="bnb-stars"></b></div>
            <div style="font-size:11px;color:var(--gray);margin-top:10px">أرسل المبلغ إلى Binance Pay ID التالي:</div>
            <div class="bnb-addr" onclick="copyBnb('id')" id="bnb-payid"></div>
            <div style="font-size:11px;color:var(--gray)">⚠️ اكتب الكود التالي في خانة الملاحظة/التعليق (Remark) عند الدفع — يستخدمه النظام لتتبع طلبك تلقائياً:</div>
            <div class="bnb-addr" style="color:#fff" onclick="copyBnb('memo')" id="bnb-memo"></div>
            <div style="font-size:11px;color:var(--warn);margin-top:6px">بعد الدفع اضغط "تحقق من الحالة"، أو انتظر تأكيد الأدمن</div>
          </div>
          <button class="sbtn ghost" style="margin-top:10px" onclick="checkBnbStatus()">🔍 تحقق من الدفع</button>
        </div>
      </div>
    </div>
  </div>
</div>

<div id="purchaseModal" class="modal" onclick="if(event.target===this)cModal('purchaseModal')">
  <div class="modal-box">
    <div class="m-handle"></div>
    <div class="m-head"><h3 id="pTitle">✅ تم الشراء</h3><button class="m-close" onclick="cModal('purchaseModal')">✕</button></div>
    <div class="m-body" id="pBody"></div>
  </div>
</div>

<div id="redeemModal" class="modal" onclick="if(event.target===this)cModal('redeemModal')">
  <div class="modal-box">
    <div class="m-handle"></div>
    <div class="m-head"><h3>🎟️ كود شحن</h3><button class="m-close" onclick="cModal('redeemModal')">✕</button></div>
    <div class="m-body">
      <input type="text" id="redeemInp" class="inp" placeholder="أدخل الكود..." style="text-transform:uppercase">
      <button class="sbtn" onclick="submitRedeem()">تفعيل الكود</button>
    </div>
  </div>
</div>

<!-- PRODUCT MODAL (add/edit) -->
<div id="prodModal" class="modal" onclick="if(event.target===this)cModal('prodModal')">
  <div class="modal-box">
    <div class="m-handle"></div>
    <div class="m-head"><h3 id="prodModalTitle">➕ إضافة منتج</h3><button class="m-close" onclick="cModal('prodModal')">✕</button></div>
    <div class="m-body">
      <input type="hidden" id="pf_id">
      <div style="text-align:center;margin-bottom:14px">
        <div id="pf_preview" style="width:100px;height:100px;border-radius:20px;background:var(--card2);margin:0 auto 10px;display:flex;align-items:center;justify-content:center;font-size:38px;overflow:hidden;border:1px solid var(--bdr)">🛍️</div>
        <label class="sbtn ghost" style="display:inline-block;width:auto;padding:8px 18px;font-size:13px;cursor:pointer">
          📷 رفع صورة من الجهاز
          <input type="file" id="pf_file" accept="image/*" style="display:none" onchange="handleImgUpload(this,'pf_preview','pf_image')">
        </label>
        <input type="hidden" id="pf_image">
      </div>
      <input type="text" id="pf_name" class="inp" placeholder="اسم المنتج">
      <input type="text" id="pf_emoji" class="inp" placeholder="إيموجي احتياطي (مثل 🛍️) إن لم ترفع صورة">
      <input type="text" id="pf_category" class="inp" placeholder="مفتاح القسم (اتركه فارغاً ليظهر كمنتج مستقل بدون قسم)">
      <div class="field-label" style="margin-bottom:6px;color:var(--gray);font-size:12px">طريقة الدفع المسموحة</div>
      <div style="display:flex;gap:8px;margin-bottom:12px">
        <button type="button" class="pm-opt" id="pm-s" onclick="setPM('s')" style="flex:1;background:var(--card2);border:1.5px solid var(--bdr);border-radius:12px;padding:10px;color:var(--txt);font-weight:700;cursor:pointer;font-family:inherit">⭐ نجوم فقط</button>
        <button type="button" class="pm-opt" id="pm-b" onclick="setPM('b')" style="flex:1;background:var(--card2);border:1.5px solid var(--bdr);border-radius:12px;padding:10px;color:var(--txt);font-weight:700;cursor:pointer;font-family:inherit">🟡 بينانس فقط</button>
        <button type="button" class="pm-opt" id="pm-sb" onclick="setPM('sb')" style="flex:1;background:var(--card2);border:1.5px solid var(--bdr);border-radius:12px;padding:10px;color:var(--txt);font-weight:700;cursor:pointer;font-family:inherit">⭐🟡 كلاهما</button>
      </div>
      <input type="number" id="pf_price" class="inp" placeholder="السعر بالنجوم (لو مفعّل دفع النجوم)">
      <input type="number" step="0.01" id="pf_price_usd" class="inp" placeholder="السعر بالدولار (لو مفعّل بينانس) — اتركه فارغاً ليُحسب تلقائياً من النجوم">
      <input type="number" id="pf_stock" class="inp" placeholder="الكمية المتوفرة">
      <textarea id="pf_description" class="inp" rows="2" placeholder="وصف قصير يظهر في المتجر"></textarea>
      <textarea id="pf_content" class="inp" rows="3" placeholder="محتوى المنتج (يظهر للمشتري بعد الدفع: رابط/كود/تعليمات)"></textarea>
      <button class="sbtn" onclick="saveProduct()" style="margin-top:6px">💾 حفظ المنتج</button>
    </div>
  </div>
</div>

<!-- CATEGORY MODAL (add) -->
<div id="catModal" class="modal" onclick="if(event.target===this)cModal('catModal')">
  <div class="modal-box">
    <div class="m-handle"></div>
    <div class="m-head"><h3>🗂️ إضافة قسم</h3><button class="m-close" onclick="cModal('catModal')">✕</button></div>
    <div class="m-body">
      <div style="text-align:center;margin-bottom:14px">
        <div id="cf_preview" style="width:100px;height:100px;border-radius:20px;background:var(--card2);margin:0 auto 10px;display:flex;align-items:center;justify-content:center;font-size:38px;overflow:hidden;border:1px solid var(--bdr)">🗂️</div>
        <label class="sbtn ghost" style="display:inline-block;width:auto;padding:8px 18px;font-size:13px;cursor:pointer">
          📷 رفع صورة من الجهاز
          <input type="file" id="cf_file" accept="image/*" style="display:none" onchange="handleImgUpload(this,'cf_preview','cf_image')">
        </label>
        <input type="hidden" id="cf_image">
      </div>
      <input type="text" id="cf_key" class="inp" placeholder="مفتاح القسم (مثل: gifts)">
      <input type="text" id="cf_name" class="inp" placeholder="اسم القسم">
      <input type="text" id="cf_emoji" class="inp" placeholder="إيموجي احتياطي (مثل 🎁) إن لم ترفع صورة">
      <input type="text" id="cf_color" class="inp" placeholder="اللون hex مثل #2f6bff" value="#2f6bff">
      <button class="sbtn" onclick="saveCategory()" style="margin-top:6px">💾 حفظ القسم</button>
    </div>
  </div>
</div>

<!-- BANNER MODAL (animated ads) -->
<div id="bannerModal" class="modal" onclick="if(event.target===this)cModal('bannerModal')">
  <div class="modal-box">
    <div class="m-handle"></div>
    <div class="m-head"><h3>📢 إضافة إعلان</h3><button class="m-close" onclick="cModal('bannerModal')">✕</button></div>
    <div class="m-body">
      <div style="text-align:center;margin-bottom:14px">
        <div id="bf_preview" style="width:100%;aspect-ratio:16/8;border-radius:18px;background:var(--card2);margin:0 auto 10px;display:flex;align-items:center;justify-content:center;font-size:34px;overflow:hidden;border:1px solid var(--bdr)">📢</div>
        <label class="sbtn ghost" style="display:inline-block;width:auto;padding:8px 18px;font-size:13px;cursor:pointer">
          📷 رفع صورة الإعلان من الجهاز
          <input type="file" id="bf_file" accept="image/*" style="display:none" onchange="handleImgUpload(this,'bf_preview','bf_image')">
        </label>
        <input type="hidden" id="bf_image">
      </div>
      <input type="text" id="bf_title" class="inp" placeholder="عنوان الإعلان">
      <input type="text" id="bf_subtitle" class="inp" placeholder="نص الزر/الشعار الصغير (اختياري)">
      <input type="text" id="bf_link" class="inp" placeholder="رابط عند الضغط (اختياري)">
      <button class="sbtn" onclick="saveBanner()" style="margin-top:6px">💾 حفظ الإعلان</button>
    </div>
  </div>
</div>

<script>
const tg = window.Telegram?.WebApp;
if(tg){ tg.expand(); tg.ready(); }
function getTGUser(){
  const r=tg?.initDataUnsafe?.user||{};
  return {id:r.id||null,first_name:r.first_name||'',last_name:r.last_name||'',
          username:r.username||'',language_code:r.language_code||'ar',
          is_premium:r.is_premium||false,photo_url:r.photo_url||'',
          full_name:[r.first_name,r.last_name].filter(Boolean).join(' ')};
}
const user = getTGUser();
let isAdmin = false, cachedBal = 0;

async function checkAdminStatus(){
  if(!user.id) return;
  try{
    const d = await f(`/api/check_admin?user_id=${user.id}`);
    isAdmin = d.is_admin || false;
    if(isAdmin) document.getElementById('tab-admin').style.display='';
  }catch(e){}
}
checkAdminStatus();

const TXT={
  ar:{store:'المتجر',orders:'طلباتي',profile:'ملفي',admin:'أدمن',
      open:'✨ افتح المتجر',myp:'👤 ملفي',stars:'نجمة',cats:'الفئات',
      buy:'شراء',in:'متوفر',out:'نفد',back:'رجوع',
      myord:'مشترياتي',noord:'لا توجد طلبات بعد',recharge:'⭐ شحن',
      spent:'إجمالي الإنفاق',orders_c:'عدد الطلبات',apanel:'لوحة التحكم',
      revenue:'الإيرادات',sales:'المبيعات',users:'المستخدمين',
      madm:'إدارة المنتجات',rcodes:'أكواد الشحن',allord:'كل الطلبات',
      bcast:'إشعارات',addacc:'➕ إضافة منتج',addcode:'➕ كود جديد',
      sendall:'📤 إرسال للجميع',insuf:'رصيدك غير كافٍ! شحن الآن؟',
      pok:'✅ تم الشراء بنجاح!',rem:'رصيدك المتبقي',
      copied:'تم النسخ ✓',redeem:'🎟️ كود شحن'},
  en:{store:'Store',orders:'Orders',profile:'Profile',admin:'Admin',
      open:'✨ Open Store',myp:'👤 Profile',stars:'Stars',cats:'Categories',
      buy:'Buy',in:'In Stock',out:'Out',back:'Back',
      myord:'My Orders',noord:'No orders yet',recharge:'⭐ Recharge',
      spent:'Total Spent',orders_c:'Total Orders',apanel:'Admin Panel',
      revenue:'Revenue',sales:'Sales',users:'Users',
      madm:'Manage Products',rcodes:'Redeem Codes',allord:'All Orders',
      bcast:'Broadcast',addacc:'➕ Add Product',addcode:'➕ Add Code',
      sendall:'📤 Send All',insuf:'Insufficient balance! Recharge?',
      pok:'✅ Purchase Successful!',rem:'Remaining Balance',
      copied:'Copied ✓',redeem:'🎟️ Redeem'}
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

let curTab=null, curCat=null, curAdmin=null;
const params=new URLSearchParams(location.search);

window.addEventListener('load',()=>{ setTimeout(()=>document.getElementById('splash').classList.add('out'),1300); });

async function refreshBal(){
  if(!user.id) return;
  try{ const d=await f(`/api/balance/${user.id}`); cachedBal=d.balance||0; document.getElementById('topBal').textContent=cachedBal; }catch(e){}
}
refreshBal(); setInterval(refreshBal,12000);

const FALLBACK_EMOJI = ['🎁','💎','🛒','📦','✨','🔷','🟡','🎯'];
function emojiFor(seed){ let h=0; for(const c of String(seed)) h=(h+c.charCodeAt(0))%FALLBACK_EMOJI.length; return FALLBACK_EMOJI[h]; }

let bannerIdx=0, bannerTimer=null;
function renderBanners(banners){
  if(!banners||!banners.length) return '';
  let h=`<div class="banner-carousel"><div class="banner-track" id="bnTrack">`;
  for(const b of banners){
    h+=`<div class="banner-slide" onclick="${b.link?`openBannerLink('${encodeURIComponent(b.link)}')`:''}" style="background:linear-gradient(120deg,${b.color1||'#1947c9'},${b.color2||'#7b2ff7'})">
      ${b.image?`<img class="bn-bg" src="${b.image}">`:''}
      <div class="bn-overlay"></div>
      <div class="bn-content">
        <div class="bn-title">${e(b.title||'')}</div>
        ${b.subtitle?`<div class="bn-pill bn-float">${e(b.subtitle)}</div>`:''}
      </div>
    </div>`;
  }
  h+=`</div><div class="banner-dots">${banners.map((_,i)=>`<div class="banner-dot${i===0?' active':''}"></div>`).join('')}</div></div>`;
  return h;
}
function startBannerAuto(count){
  if(bannerTimer) clearInterval(bannerTimer);
  if(count<2) return;
  bannerTimer=setInterval(()=>{
    bannerIdx=(bannerIdx+1)%count;
    const track=document.getElementById('bnTrack');
    if(track) track.style.transform=`translateX(${bannerIdx*100*(lang==='ar'?1:-1)}%)`;
    document.querySelectorAll('.banner-dot').forEach((d,i)=>d.classList.toggle('active',i===bannerIdx));
  },4000);
}
function openBannerLink(url){ try{ window.open(decodeURIComponent(url),'_blank'); }catch(e){} }

function buildUID(bal){
  const av=user.photo_url?`<img src="${user.photo_url}" alt="">`:( user.first_name?.charAt(0).toUpperCase()||'👤');
  const handle=user.username?`@${user.username}`:user.id?`ID: ${user.id}`:'';
  return `
  <div class="uid-card">
    <div class="uid-av">${av}</div>
    <div class="uid-info">
      <div class="uid-name">${e(user.full_name||user.first_name||'مستخدم')}</div>
      <div class="uid-handle">${e(handle)}</div>
    </div>
  </div>
  <div class="bal-circles-row">
    <div class="bal-circle-wrap">
      <div class="bal-circle">
        <svg viewBox="0 0 36 36" class="bal-ring-svg">
          <circle cx="18" cy="18" r="15.5" fill="none" stroke="rgba(255,207,63,.15)" stroke-width="2.5"/>
          <circle cx="18" cy="18" r="15.5" fill="none" stroke="var(--gold)" stroke-width="2.5" stroke-linecap="round" stroke-dasharray="97.4" stroke-dashoffset="20" transform="rotate(-90 18 18)"/>
        </svg>
        <div class="bal-circle-inner">
          <svg width="22" height="22" viewBox="0 0 24 24"><polygon points="12,2 15.09,8.26 22,9.27 17,14.14 18.18,21.02 12,17.77 5.82,21.02 7,14.14 2,9.27 8.91,8.26" fill="#FFCF3F" stroke="#a87a08" stroke-width="1" stroke-linejoin="round"/></svg>
          <div class="bal-circle-val">${bal??cachedBal}</div>
          <div class="bal-circle-lbl">${t('stars')}</div>
        </div>
      </div>
      <button class="bal-circle-add" onclick="showDeposit()">+</button>
    </div>
  </div>`;
}

function loadWelcome(){
  document.getElementById('app').innerHTML=`
  <div class="wlc-wrap"><div class="wlc-card">
    <svg width="72" height="72" viewBox="0 0 72 72" fill="none">
      <rect width="72" height="72" rx="22" fill="rgba(47,107,255,.14)"/>
      <polygon points="36,14 41,27 55,28 44,38 47,52 36,46 25,52 28,38 17,28 31,27" fill="#FFCF3F"/>
    </svg>
    <div class="wlc-t">SoNs</div>
    <div class="wlc-s">متجرك الرقمي الموثوق<br>دفع بنجوم تيليجرام أو Binance Pay</div>
    <div class="wlc-btns">
      <button class="wlc-btn" onclick="switchTab('store')">${t('open')}</button>
      <button class="wlc-btn sec" onclick="switchTab('profile')">${t('myp')}</button>
    </div>
  </div></div>`;
}

async function loadStore(){
  const app=document.getElementById('app');
  if(curCat) return loadProducts(curCat);
  app.innerHTML=loading();
  try{
    const[store,bal]=await Promise.all([f('/api/store'),f(`/api/balance/${user.id}`)]);
    const cats=store.categories||{}, prods=store.products||[];
    const standalone=prods.filter(p=>!p.category);
    let h=renderBanners(store.banners||[]);
    h+=`<div class="sec-h"><div class="sec-t">${t('cats')}</div></div><div class="cat-grid">`;
    const palette=['#2f6bff','#ff5da2','#ffcf3f','#22c55e','#9b59b6','#f39c12'];
    let ci=0;
    for(const[k,c] of Object.entries(cats)){
      const cnt=prods.filter(p=>p.category===k).length;
      const color=c.color||palette[ci%palette.length]; ci++;
      h+=`<div class="icon-card" onclick="openCat('${k}')" style="background:${color}2a">
        <div class="ic-bg" style="background:radial-gradient(circle at 30% 20%,${color}55,transparent 70%)"></div>
        <div class="ic-glass"></div>
        ${c.image?`<img class="ic-img" src="${c.image}">`:`<div class="ic-emoji">${c.emoji||emojiFor(k)}</div>`}
        <div class="ic-badge">${cnt}</div>
        <div class="ic-label">${e(c.name)}</div>
      </div>`;
    }
    h+='</div>';
    if(standalone.length){
      h+=`<div class="sec-h"><div class="sec-t">✨ منتجات مميزة</div></div>`;
      h+=renderProdGrid(standalone);
    }
    app.innerHTML=h;
    startBannerAuto((store.banners||[]).length);
  }catch(err){ app.innerHTML=empty('❌'); }
}
function openCat(c){ curCat=c; loadProducts(c); }

function renderProdGrid(prods){
  let h='<div class="prod-grid">';
  for(const p of prods){
    const ok=p.stock>0;
    const finalPrice=Math.ceil(p.price*1.0);
    const pm=p.payment_method||'sb';
    const payBadge = pm==='s' ? '⭐' : pm==='b' ? '🟡' : '⭐/🟡';
    h+=`<div class="prod-tile">
      <div class="pt-img">${p.image?`<img src="${p.image}">`:`<span>${p.emoji||emojiFor(p.id)}</span>`}</div>
      <div class="pt-name">${e(p.name)}</div>
      <div class="pt-price">${pm==='b'?'$'+(p.price_usd||(p.price*STAR_TO_USD_JS).toFixed(2)):'⭐ '+finalPrice}</div>
      <div class="pt-stock">${ok?t('in')+' ('+p.stock+')':t('out')} · ${payBadge}</div>
      <button class="pt-buy" ${!ok?'disabled':''} onclick='startBuy(${JSON.stringify(p).replace(/'/g,"&#39;")})'>${t('buy')}</button>
    </div>`;
  }
  h+='</div>';
  return h;
}
const STAR_TO_USD_JS = 0.013;

async function loadProducts(cat){
  const app=document.getElementById('app'); app.innerHTML=loading();
  try{
    const store=await f('/api/store');
    const prods=(store.products||[]).filter(p=>p.category===cat);
    const ci=(store.categories||{})[cat]||{};
    let h=`<button class="back-btn" onclick="goBack()">← ${t('back')}</button>
      <div class="sec-h"><div class="sec-t">${ci.emoji||'🛍️'} ${ci.name||cat}</div></div>`;
    h+= prods.length ? renderProdGrid(prods) : empty('📭');
    app.innerHTML=h;
  }catch(err){ app.innerHTML=empty('❌'); }
}

async function buyProd(pid){
  if(!user.id) return toast('❌ يجب تسجيل الدخول');
  try{
    const d=await fetch('/api/purchase',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({product_id:pid,user_id:user.id})}).then(r=>r.json());
    if(d.success){
      cachedBal=d.new_balance||0;
      document.getElementById('pTitle').textContent=t('pok');
      document.getElementById('pBody').innerHTML=`
        <p style="color:var(--gray);font-size:13px;margin-bottom:10px">${t('rem')}: ⭐ ${d.new_balance}</p>
        <div style="background:var(--card2);padding:14px;border-radius:13px;font-family:monospace;font-size:12px;white-space:pre-wrap;word-break:break-all;margin:12px 0;border:1px solid var(--bdr)">${e(d.details)}</div>
        <button class="sbtn" onclick="navigator.clipboard.writeText(${JSON.stringify(d.details)});toast('${t('copied')}')">📋 نسخ المحتوى</button>`;
      document.getElementById('purchaseModal').style.display='flex';
      if(curCat) loadProducts(curCat);
      refreshBal();
    } else if(d.error==='insufficient'){ if(confirm(t('insuf'))) showDeposit(); }
    else toast('❌ '+(d.error||''));
  }catch(err){ toast('❌'); }
}

// ── Unified buy entry point: respects product.payment_method (s/b/sb) ──
function startBuy(p){
  const pm = p.payment_method || 'sb';
  if(pm === 's') return buyProd(p.id);
  if(pm === 'b') return buyWithBinance(p);
  // both available -> let user choose
  document.getElementById('pTitle').textContent='💳 اختر طريقة الدفع';
  document.getElementById('pBody').innerHTML=`
    <div style="font-size:13px;color:var(--gray);margin-bottom:14px;text-align:center">${e(p.name)} — ⭐ ${p.price} / $${p.price_usd||(p.price*STAR_TO_USD_JS).toFixed(2)}</div>
    <button class="sbtn" style="margin-bottom:10px" onclick="cModal('purchaseModal');buyProd('${p.id}')">⭐ الدفع برصيد النجوم</button>
    <button class="sbtn bnb" onclick='cModal("purchaseModal");buyWithBinance(${JSON.stringify(p).replace(/'/g,"&#39;")})'>🟡 الدفع بـ Binance Pay</button>`;
  document.getElementById('purchaseModal').style.display='flex';
}

let currentBnbOrderProduct=null;
async function buyWithBinance(p){
  if(!user.id) return toast('❌ يجب تسجيل الدخول');
  try{
    const d=await fetch('/api/purchase_binance/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:user.id,product_id:p.id})}).then(r=>r.json());
    if(!d.success) return toast('❌ '+(d.error||''));
    currentBnbOrderProduct=d.order_id;
    document.getElementById('pTitle').textContent='🟡 الدفع بـ Binance Pay';
    document.getElementById('pBody').innerHTML=`
      <div class="bnb-box">
        <div style="font-weight:800;margin-bottom:8px">${e(p.name)}</div>
        <div>💵 المبلغ: <b style="color:var(--gold)">$${d.usd}</b></div>
        <div style="font-size:11px;color:var(--gray);margin-top:10px">أرسل المبلغ إلى Binance Pay ID:</div>
        <div class="bnb-addr" onclick="navigator.clipboard.writeText('${d.binance_pay_id}');toast('✓ تم النسخ')">${d.binance_pay_id}</div>
        <div style="font-size:11px;color:var(--gray)">⚠️ اكتب هذا الكود في خانة الملاحظة (Remark) ليتم تتبع طلبك تلقائياً:</div>
        <div class="bnb-addr" style="color:#fff" onclick="navigator.clipboard.writeText('${d.order_id}');toast('✓ تم النسخ')">${d.order_id}</div>
      </div>
      <button class="sbtn ghost" style="margin-top:10px" onclick="checkBnbOrderStatus()">🔍 تحقق من حالة الطلب</button>`;
    document.getElementById('purchaseModal').style.display='flex';
  }catch(err){ toast('❌'); }
}
async function checkBnbOrderStatus(){
  if(!currentBnbOrderProduct) return;
  try{
    const d=await f(`/api/purchase_binance/status?order_id=${currentBnbOrderProduct}`);
    if(d.status==='confirmed'){
      toast('✅ تم تأكيد الدفع! تحقق من تبويب طلباتي');
      cModal('purchaseModal'); if(curCat) loadProducts(curCat); loadStore();
    } else toast('⏳ لم يتم تأكيد الدفع من الأدمن بعد');
  }catch(e){ toast('❌'); }
}

// DEPOSIT
function setPayTab(p){
  document.getElementById('ptab-stars').classList.toggle('active',p==='stars');
  document.getElementById('ptab-bnb').classList.toggle('active',p==='bnb');
  document.getElementById('sec-stars').style.display=p==='stars'?'':'none';
  document.getElementById('sec-bnb').style.display=p==='bnb'?'':'none';
}
function setA(n){ document.getElementById('starsAmt').value=n; }
function setBA(n){ document.getElementById('bnbAmt').value=n; }

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

let currentBnbId=null;
async function initBinancePay(){
  const stars=parseInt(document.getElementById('bnbAmt').value);
  if(!stars||stars<1) return toast('❌ أدخل عدد النجوم');
  if(!user.id) return toast('❌ يجب تسجيل الدخول');
  try{
    const d=await fetch('/api/binance/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:user.id,stars})}).then(r=>r.json());
    if(!d.success) return toast('❌ '+(d.error||''));
    currentBnbId=d.payment_id;
    document.getElementById('bnb-usd').textContent='$'+d.usd;
    document.getElementById('bnb-stars').textContent=stars+' ⭐';
    document.getElementById('bnb-payid').textContent=d.binance_pay_id;
    document.getElementById('bnb-memo').textContent=d.payment_id;
    document.getElementById('bnb-result').style.display='';
    toast('✅ أرسل الدفع مع كتابة الكود في الملاحظة');
  }catch(err){ toast('❌'); }
}
function copyBnb(which){
  const el=document.getElementById(which==='id'?'bnb-payid':'bnb-memo');
  navigator.clipboard.writeText(el.textContent).then(()=>toast('✓ تم النسخ'));
}
async function checkBnbStatus(){
  if(!currentBnbId) return;
  try{
    const d=await f(`/api/binance/status?payment_id=${currentBnbId}`);
    if(d.status==='confirmed'){ toast('✅ تم تأكيد الدفع وإضافة الرصيد!'); setTimeout(()=>{refreshBal();cModal('depositModal');},1500); }
    else toast('⏳ لم يتم تأكيد الدفع من الأدمن بعد');
  }catch(e){ toast('❌'); }
}

async function loadPurchases(){
  const app=document.getElementById('app'); app.innerHTML=loading();
  try{
    const purch=await f(`/api/purchases?user_id=${user.id}`);
    let h='';
    if(purch.purchases?.length){
      for(const p of purch.purchases){
        h+=`<div class="purch-item">
          <div style="font-size:26px;flex-shrink:0">${p.emoji||'🛍️'}</div>
          <div style="flex:1">
            <div style="font-weight:700;font-size:14px;margin-bottom:3px">${e(p.product_name)}</div>
            <div style="font-size:11px;color:var(--gray)">⭐ ${p.price} | ${new Date(p.purchase_date||p.timestamp).toLocaleDateString(lang==='en'?'en':'ar')}</div>
          </div>
        </div>`;
      }
    }
    app.innerHTML = h || `<div class="empty">📭<br>${t('noord')}</div>`;
  }catch(err){ app.innerHTML=empty('❌'); }
}

async function loadProfile(){
  const app=document.getElementById('app'); app.innerHTML=loading();
  try{
    const[bal,stats]=await Promise.all([f(`/api/balance/${user.id}`),f(`/api/user_stats/${user.id}`)]);
    let h=buildUID(bal.balance||0);
    h+=`<div style="display:flex;gap:10px;margin-bottom:4px">
      <button class="sbtn" style="flex:1;padding:12px;font-size:14px" onclick="showDeposit()">${t('recharge')}</button>
      <button class="sbtn ghost" style="flex:1;padding:12px;font-size:14px" onclick="showRedeem()">${t('redeem')}</button>
    </div>
    <div class="stat-grid">
      <div class="stat-box"><div class="stat-n">⭐ ${stats.spent||0}</div><div class="stat-l">${t('spent')}</div></div>
      <div class="stat-box"><div class="stat-n">📦 ${stats.purchases||0}</div><div class="stat-l">${t('orders_c')}</div></div>
    </div>`;
    app.innerHTML=h;
  }catch(err){ app.innerHTML=empty('❌'); }
}

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

// ADMIN
async function loadAdmin(){
  if(curAdmin) return loadAdminSec(curAdmin);
  const app=document.getElementById('app'); app.innerHTML=loading();
  try{
    const stats=await f('/api/admin_stats');
    const menus=[
      {id:'products',t:t('madm'),d:'إضافة/تعديل/حذف المنتجات',icon:'🛍️'},
      {id:'categories',t:'إدارة الأقسام',d:'إضافة وحذف أقسام المتجر',icon:'🗂️'},
      {id:'banners',t:'الإعلانات المتحركة',d:'بانرات متحركة بصفحة المتجر',icon:'📢'},
      {id:'users',t:'المستخدمون',d:'عرض وإدارة المستخدمين',icon:'👥'},
      {id:'admins',t:'إدارة الأدمن',d:'إضافة/إزالة الأدمن',icon:'🔧'},
      {id:'redeem_codes',t:t('rcodes'),d:'إضافة وعرض أكواد الشحن',icon:'🎟️'},
      {id:'binance_payments',t:'مدفوعات Binance Pay',d:'تأكيد المدفوعات اليدوية',icon:'🟡'},
      {id:'purchases',t:t('allord'),d:'جميع المعاملات',icon:'📊'},
      {id:'broadcast',t:t('bcast'),d:'إرسال إشعار للجميع',icon:'📢'},
    ];
    let h=`<div class="sec-h"><div class="sec-t">${t('apanel')}</div></div>
      <div class="adm-stats">
        <div class="stat-box"><div class="stat-n">⭐ ${stats.revenue||0}</div><div class="stat-l">${t('revenue')}</div></div>
        <div class="stat-box"><div class="stat-n">📦 ${stats.purchases||0}</div><div class="stat-l">${t('sales')}</div></div>
        <div class="stat-box"><div class="stat-n">👥 ${stats.users||0}</div><div class="stat-l">${t('users')}</div></div>
        <div class="stat-box"><div class="stat-n">🛍️ ${stats.products||0}</div><div class="stat-l">منتجات</div></div>
      </div>
      <div style="background:linear-gradient(135deg,rgba(34,197,94,.12),rgba(21,128,61,.08));border:1px solid rgba(34,197,94,.25);border-radius:16px;padding:14px 18px;margin-bottom:16px;display:flex;align-items:center;justify-content:space-between">
        <div><div style="font-size:11px;color:var(--gray);margin-bottom:4px">💰 إجمالي الإيرادات بالدولار</div>
        <div style="font-size:26px;font-weight:900;color:var(--success)">$${(stats.total_usd||0).toFixed(2)}</div></div>
        <div style="font-size:38px;opacity:.3">💵</div>
      </div>`;
    for(const m of menus){
      h+=`<div class="adm-card" onclick="openAdminSec('${m.id}')">
        <div class="adm-ic">${m.icon}</div>
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
    if(sec==='products'){
      const d=await f('/api/store');
      h+=`<div class="sec-h"><div class="sec-t">${t('madm')}</div></div>
          <div style="margin-bottom:12px"><button class="sbtn" onclick="adminAddProd()" style="padding:12px">${t('addacc')}</button></div>`;
      for(const p of (d.products||[])){
        h+=`<div class="adm-card"><div class="adm-ic">${p.emoji||'🛍️'}</div>
          <div class="adm-info"><div class="adm-t">${e(p.name)}</div><div class="adm-d">⭐${p.price} | Stock:${p.stock} | Sold:${p.sold||0}</div></div>
          <button class="edit-btn" onclick="adminEditProd('${p.id}')">تعديل</button>
          <button class="del-btn" onclick="adminDelProd('${p.id}')">حذف</button>
        </div>`;
      }
    } else if(sec==='categories'){
      const d=await f('/api/store');
      const cats=d.categories||{};
      h+=`<div class="sec-h"><div class="sec-t">🗂️ إدارة الأقسام</div></div>
          <div style="margin-bottom:14px"><button class="sbtn" onclick="adminAddCategoryModal()" style="padding:12px">➕ إضافة قسم جديد</button></div>
          <div class="sec-h" style="margin-top:4px"><div class="sec-t" style="font-size:15px">الأقسام الحالية</div></div>`;
      for(const[k,c] of Object.entries(cats)){
        h+=`<div class="adm-card">
          <div class="adm-ic" style="background:${c.color||'#2f6bff'}22;overflow:hidden">${c.image?`<img src="${c.image}" style="width:100%;height:100%;object-fit:cover">`:(c.emoji||'🗂️')}</div>
          <div class="adm-info"><div class="adm-t">${e(c.name||k)}</div><div class="adm-d" style="font-family:monospace">${k}</div></div>
          <button class="del-btn" onclick="adminDelCategory('${k}')">حذف</button>
        </div>`;
      }
    } else if(sec==='banners'){
      const d=await f(`/api/admin/banners?admin_id=${user.id}`);
      h+=`<div class="sec-h"><div class="sec-t">📢 الإعلانات المتحركة</div></div>
          <div style="margin-bottom:14px"><button class="sbtn" onclick="adminAddBannerModal()" style="padding:12px">➕ إضافة إعلان</button></div>`;
      for(const b of (d.banners||[])){
        h+=`<div class="adm-card">
          <div class="adm-ic" style="overflow:hidden">${b.image?`<img src="${b.image}" style="width:100%;height:100%;object-fit:cover">`:'📢'}</div>
          <div class="adm-info"><div class="adm-t">${e(b.title||'')}</div><div class="adm-d">${e(b.subtitle||'')}</div></div>
          <button class="del-btn" onclick="adminDelBanner('${b.id}')">حذف</button>
        </div>`;
      }
      if(!(d.banners||[]).length) h+=empty('📭 لا توجد إعلانات');
    } else if(sec==='admins'){
      if(!is_owner_check()){ app.innerHTML=h+empty('⛔ للمالك فقط'); return; }
      const admData = await f(`/api/admin/list_admins?admin_id=${user.id}`);
      h+=`<div class="sec-h"><div class="sec-t">إدارة الأدمن</div></div>
          <input type="text" id="newAdminId" class="inp" placeholder="أدخل Telegram ID للأدمن الجديد...">
          <button class="sbtn" style="margin-bottom:16px" onclick="adminAddAdmin()">➕ إضافة أدمن</button>
          <div class="sec-h" style="margin-top:4px"><div class="sec-t" style="font-size:15px">الأدمن الحاليون</div></div>`;
      for(const aid of (admData.admins||[])){
        h+=`<div class="adm-card">
          <div class="adm-ic">🔧</div>
          <div class="adm-info"><div class="adm-t" style="font-family:monospace">${aid}</div><div class="adm-d">أدمن</div></div>
          <button class="del-btn" onclick="adminRemoveAdmin('${aid}')">إزالة</button>
        </div>`;
      }
      if(!(admData.admins||[]).length) h+=`<div class="empty">لا يوجد أدمن حتى الآن</div>`;
    } else if(sec==='binance_payments'){
      const[d,d2]=await Promise.all([f(`/api/admin/binance_payments?admin_id=${user.id}`),f(`/api/admin/binance_orders?admin_id=${user.id}`)]);
      h+=`<div class="sec-h"><div class="sec-t">مدفوعات شحن الرصيد (Binance Pay)</div></div>`;
      const pend = Object.entries(d.payments||{}).filter(([_,p])=>p.status!=='confirmed');
      if(!pend.length) h+=`<div class="empty">📭<br>لا توجد مدفوعات معلقة</div>`;
      for(const[pid,p] of pend){
        h+=`<div class="adm-card">
          <div class="adm-ic">🟡</div>
          <div class="adm-info">
            <div class="adm-t">⭐ ${p.stars} نجمة = $${p.usd}</div>
            <div class="adm-d">UID:${p.user_id} | كود التتبع: <code>${pid}</code></div>
          </div>
          <button class="done-btn" onclick="adminConfirmBnb('${pid}')">تأكيد</button>
        </div>`;
      }
      h+=`<div class="sec-h"><div class="sec-t">طلبات شراء منتجات (Binance Pay)</div></div>`;
      const pend2 = Object.entries(d2.orders||{}).filter(([_,o])=>o.status!=='confirmed');
      if(!pend2.length) h+=`<div class="empty">📭<br>لا توجد طلبات معلقة</div>`;
      for(const[oid,o] of pend2){
        h+=`<div class="adm-card">
          <div class="adm-ic">🟡</div>
          <div class="adm-info">
            <div class="adm-t">${e(o.product_name)} — $${o.usd}</div>
            <div class="adm-d">UID:${o.user_id} | كود التتبع: <code>${oid}</code></div>
          </div>
          <button class="done-btn" onclick="adminConfirmBnbOrder('${oid}')">تأكيد</button>
        </div>`;
      }
    } else if(sec==='users'){
      const us=await f('/api/users');
      h+=`<div class="sec-h"><div class="sec-t">المستخدمون</div></div>`;
      for(const u of (us||[])){
        h+=`<div class="adm-card">
          <div class="adm-ic">👤</div>
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
          <div class="adm-ic">🎟️</div>
          <div class="adm-info"><div class="adm-t" style="font-family:monospace">${e(c)}</div><div class="adm-d">⭐ ${a} نجمة</div></div>
          <button class="del-btn" onclick="adminDelCode('${c}')">حذف</button>
        </div>`;
      }
    } else if(sec==='purchases'){
      const d=await f(`/api/admin/purchases?admin_id=${user.id}`);
      h+=`<div class="sec-h"><div class="sec-t">${t('allord')}</div></div>`;
      for(const p of (d.purchases||[])){
        h+=`<div class="purch-item">
          <div style="font-size:24px">${p.emoji||'🛍️'}</div>
          <div style="flex:1"><div style="font-weight:700;margin-bottom:3px">${e(p.product_name)}</div>
          <div style="font-size:11px;color:var(--gray)">ID:${p.user_id} | ⭐${p.price} | ${p.payment_method||'balance'}</div></div>
        </div>`;
      }
    } else if(sec==='broadcast'){
      h+=`<div class="sec-h"><div class="sec-t">${t('bcast')}</div></div>
          <textarea id="bMsg" class="inp" rows="5" placeholder="اكتب رسالتك..."></textarea>
          <button class="sbtn" onclick="adminBroadcast()" style="margin-top:8px;padding:12px">${t('sendall')}</button>`;
    }
    app.innerHTML=h;
  }catch(err){ app.innerHTML=empty('❌'); }
}

function is_owner_check(){ return false; /* server enforces real check; UI hint only */ }

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
// ── Image upload helper: converts selected file to base64, uploads, stores URL ──
async function handleImgUpload(inputEl, previewId, hiddenId){
  const file=inputEl.files[0]; if(!file) return;
  if(file.size > 4*1024*1024){ toast('❌ الصورة كبيرة جداً (الحد 4MB)'); return; }
  const reader=new FileReader();
  reader.onload=async()=>{
    const base64=reader.result.split(',')[1];
    const ext=(file.name.split('.').pop()||'jpg').toLowerCase();
    document.getElementById(previewId).innerHTML=`<img src="${reader.result}" style="width:100%;height:100%;object-fit:cover">`;
    try{
      const d=await fetch('/api/admin/upload_image',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({admin_id:user.id,data:base64,ext})}).then(r=>r.json());
      if(d.success){ document.getElementById(hiddenId).value=d.url; toast('✓ تم رفع الصورة'); }
      else toast('❌ فشل رفع الصورة: '+(d.error||''));
    }catch(e){ toast('❌ فشل رفع الصورة'); }
  };
  reader.readAsDataURL(file);
}

// ── Payment method selector (s / b / sb) ──
let pf_paymethod='sb';
function setPM(which){
  pf_paymethod=which;
  document.querySelectorAll('.pm-opt').forEach(b=>b.classList.remove('selected'));
  const el=document.getElementById('pm-'+which); if(el) el.classList.add('selected');
}

function openProdModal(prod){
  document.getElementById('prodModalTitle').textContent = prod? '✏️ تعديل منتج':'➕ إضافة منتج';
  document.getElementById('pf_id').value = prod?.id||'';
  document.getElementById('pf_name').value = prod?.name||'';
  document.getElementById('pf_emoji').value = prod?.emoji||'';
  document.getElementById('pf_category').value = prod?.category||'';
  document.getElementById('pf_price').value = prod?.price||'';
  document.getElementById('pf_price_usd').value = prod?.price_usd||'';
  document.getElementById('pf_stock').value = prod?.stock??'';
  document.getElementById('pf_description').value = prod?.description||'';
  document.getElementById('pf_content').value = prod?.content||'';
  document.getElementById('pf_image').value = prod?.image||'';
  document.getElementById('pf_preview').innerHTML = prod?.image?`<img src="${prod.image}" style="width:100%;height:100%;object-fit:cover">`:(prod?.emoji||'🛍️');
  setPM(prod?.payment_method||'sb');
  document.getElementById('prodModal').style.display='flex';
}
function adminAddProd(){ openProdModal(null); }
async function adminEditProd(id){
  const store=await f('/api/store');
  const prod=(store.products||[]).find(p=>p.id===id);
  if(prod) openProdModal(prod);
}
async function saveProduct(){
  const id=document.getElementById('pf_id').value;
  const name=document.getElementById('pf_name').value.trim(); if(!name) return toast('❌ أدخل اسم المنتج');
  const category=document.getElementById('pf_category').value.trim(); // empty = standalone, shown without a category
  const price=parseInt(document.getElementById('pf_price').value)||0;
  const price_usd=parseFloat(document.getElementById('pf_price_usd').value)||0;
  const stock=parseInt(document.getElementById('pf_stock').value);
  const content=document.getElementById('pf_content').value.trim();
  const description=document.getElementById('pf_description').value.trim();
  const emoji=document.getElementById('pf_emoji').value.trim();
  const image=document.getElementById('pf_image').value.trim();
  if(isNaN(stock)) return toast('❌ أدخل الكمية');
  const payload={admin_id:user.id,name,category,price,price_usd,stock,content,description,emoji,image,payment_method:pf_paymethod};
  const url = id? '/api/admin/edit_product' : '/api/admin/add_product';
  if(id) payload.product_id=id, payload.updates={name,category,price,price_usd,stock,content,description,emoji,image,payment_method:pf_paymethod};
  const d=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}).then(r=>r.json());
  toast(d.success?'✅ تم الحفظ':'❌ '+(d.error||''));
  if(d.success){ cModal('prodModal'); loadAdminSec('products'); }
}
async function adminDelProd(id){
  if(!confirm('حذف هذا المنتج؟'))return;
  const d=await fetch('/api/admin/delete_product',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({admin_id:user.id,product_id:id})}).then(r=>r.json());
  toast(d.success?'✅ تم الحذف':'❌'); if(d.success)loadAdminSec('products');
}
async function adminAddStars(uid){
  const amt=parseInt(prompt('عدد النجوم:')); if(!amt)return;
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
function adminAddBannerModal(){
  document.getElementById('bf_title').value='';
  document.getElementById('bf_subtitle').value='';
  document.getElementById('bf_link').value='';
  document.getElementById('bf_image').value='';
  document.getElementById('bf_preview').innerHTML='📢';
  document.getElementById('bannerModal').style.display='flex';
}
async function saveBanner(){
  const title=document.getElementById('bf_title').value.trim(); if(!title) return toast('❌ أدخل عنوان الإعلان');
  const subtitle=document.getElementById('bf_subtitle').value.trim();
  const link=document.getElementById('bf_link').value.trim();
  const image=document.getElementById('bf_image').value.trim();
  const d=await fetch('/api/admin/add_banner',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({admin_id:user.id,title,subtitle,link,image})}).then(r=>r.json());
  toast(d.success?'✅ تمت إضافة الإعلان':'❌ '+(d.error||''));
  if(d.success){ cModal('bannerModal'); loadAdminSec('banners'); }
}
async function adminDelBanner(bid){
  if(!confirm('حذف هذا الإعلان؟'))return;
  const d=await fetch('/api/admin/delete_banner',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({admin_id:user.id,banner_id:bid})}).then(r=>r.json());
  toast(d.success?'✅ تم الحذف':'❌'); if(d.success)loadAdminSec('banners');
}

function adminAddCategoryModal(){
  document.getElementById('cf_key').value='';
  document.getElementById('cf_name').value='';
  document.getElementById('cf_color').value='#2f6bff';
  document.getElementById('cf_emoji').value='';
  document.getElementById('cf_image').value='';
  document.getElementById('cf_preview').innerHTML='🗂️';
  document.getElementById('catModal').style.display='flex';
}
async function saveCategory(){
  const key=document.getElementById('cf_key').value.trim().toLowerCase().replace(/\s+/g,'_'); if(!key)return toast('❌ أدخل مفتاح القسم');
  const name=document.getElementById('cf_name').value.trim(); if(!name)return toast('❌ أدخل اسم القسم');
  const color=document.getElementById('cf_color').value.trim()||'#2f6bff';
  const emoji=document.getElementById('cf_emoji').value.trim()||'';
  const image=document.getElementById('cf_image').value.trim()||'';
  const d=await fetch('/api/admin/add_category',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({admin_id:user.id,key,name,color,emoji,image})}).then(r=>r.json());
  toast(d.success?'✅ تمت إضافة القسم':'❌ '+d.error);
  if(d.success){ cModal('catModal'); loadAdminSec('categories'); }
}
async function adminDelCategory(key){
  if(!confirm('حذف قسم '+key+'؟'))return;
  const d=await fetch('/api/admin/delete_category',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({admin_id:user.id,key})}).then(r=>r.json());
  toast(d.success?'✅ تم الحذف':'❌ '+d.error); if(d.success)loadAdminSec('categories');
}
async function adminConfirmBnb(pid){
  if(!confirm('تأكيد استلام دفعة Binance Pay؟'))return;
  const d=await fetch('/api/admin/confirm_binance',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({admin_id:user.id,payment_id:pid})}).then(r=>r.json());
  toast(d.success?'✅ تم تأكيد الدفع وإضافة الرصيد':'❌ '+d.error); if(d.success)loadAdminSec('binance_payments');
}
async function adminConfirmBnbOrder(oid){
  if(!confirm('تأكيد استلام دفعة المنتج عبر Binance Pay؟'))return;
  const d=await fetch('/api/admin/confirm_binance_order',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({admin_id:user.id,order_id:oid})}).then(r=>r.json());
  toast(d.success?'✅ تم تأكيد الطلب وتسليم المنتج':'❌ '+(d.error||'')); if(d.success)loadAdminSec('binance_payments');
}

function switchTab(tab){
  curTab=tab; curCat=null; curAdmin=null;
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  const el=document.getElementById('tab-'+tab); if(el)el.classList.add('active');
  if(tab==='store') loadStore();
  else if(tab==='purchases') loadPurchases();
  else if(tab==='profile') loadProfile();
  else if(tab==='admin'&&isAdmin) loadAdmin();
  else loadWelcome();
}
function goBack(){ curCat=null; loadStore(); }
function showDeposit(){ document.getElementById('depositModal').style.display='flex'; setPayTab('stars'); document.getElementById('bnb-result').style.display='none'; }
function cModal(id){ document.getElementById(id).style.display='none'; }
function toast(msg){
  const el=document.createElement('div'); el.className='toast'; el.textContent=msg;
  document.body.appendChild(el); setTimeout(()=>el.remove(),2800);
}
const f = url => fetch(url).then(r=>r.json());
function e(s){ if(!s)return''; const d=document.createElement('div'); d.textContent=s; return d.innerHTML; }
function loading(){ return '<div class="loading"><div class="spinner"></div></div>'; }
function empty(ic){ return `<div class="empty">${ic}</div>`; }

const initTabParam=params.get('tab')||'store';
if(['store','profile','purchases'].includes(initTabParam)) switchTab(initTabParam);
else if(initTabParam==='admin') checkAdminStatus().then(()=>{if(isAdmin)switchTab('admin');else loadWelcome();});
else loadWelcome();
</script>
</body>
</html>
'''

# ==================== HTTP HANDLER ====================
class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        q = parse_qs(urlparse(self.path).query)
        try:
            if path == '/':
                self._html(HTML_WEBAPP)
            elif path.startswith('/uploads/'):
                self._serve_upload(path)
            elif path == '/api/store':
                self._json({'products': db.get_products(), 'categories': db.categories,
                            'banners': db.get_banners()})
            elif path == '/api/check_admin':
                uid = int(q.get('user_id', [0])[0])
                self._json({'is_admin': is_admin(uid)})
            elif path.startswith('/api/balance/'):
                self._json({'balance': db.get_balance(int(path.split('/')[-1]))})
            elif path == '/api/purchases':
                uid = str(q.get('user_id', [0])[0])
                self._json({'purchases': db.get_user_purchases(uid, 50)})
            elif path.startswith('/api/user_stats/'):
                self._json(db.get_user_stats(int(path.split('/')[-1])))
            elif path == '/api/users':
                self._json(db.get_all_users())
            elif path == '/api/admin_stats':
                s = db.get_stats()
                self._json({'revenue': s['revenue'], 'purchases': s['total_purchases'],
                             'users': db.get_user_count(), 'products': s['total_products'],
                             'total_usd': round(s.get('total_usd', 0), 2)})
            elif path == '/api/admin/redeem_codes':
                if is_admin(int(q.get('admin_id', [0])[0])):
                    self._json(db.get_redeem_codes())
                else:
                    self._err(403)
            elif path == '/api/admin/purchases':
                if is_admin(int(q.get('admin_id', [0])[0])):
                    self._json({'purchases': db.purchases})
                else:
                    self._err(403)
            elif path == '/api/admin/list_admins':
                if is_owner(int(q.get('admin_id', [0])[0])):
                    self._json(db.get_admins_list())
                else:
                    self._err(403)
            elif path == '/api/admin/binance_payments':
                if is_admin(int(q.get('admin_id', [0])[0])):
                    self._json({'payments': db.binance_pays})
                else:
                    self._err(403)
            elif path == '/api/admin/binance_orders':
                if is_admin(int(q.get('admin_id', [0])[0])):
                    self._json({'orders': db.binance_orders})
                else:
                    self._err(403)
            elif path == '/api/admin/banners':
                if is_admin(int(q.get('admin_id', [0])[0])):
                    self._json({'banners': db.get_banners()})
                else:
                    self._err(403)
            elif path == '/api/purchase_binance/status':
                oid = q.get('order_id', [''])[0]
                o = db.get_binance_order(oid)
                self._json({'status': o.get('status', 'pending') if o else 'not_found'})
            elif path == '/api/deposit':
                uid = int(q.get('user_id', [0])[0])
                amt = int(q.get('amount', [0])[0])
                if uid <= 0 or amt <= 0:
                    return self._json({'success': False, 'error': 'Invalid'})
                amt = max(1, min(100000, amt))
                pid = f"dep_{uid}_{amt}_{uuid.uuid4().hex[:8]}"
                db.add_pending(pid, {'type': 'deposit', 'user_id': uid, 'amount': amt})
                try:
                    inv = bot.create_invoice_link(
                        title=f"⭐ شحن {amt} نجمة",
                        description=f"إضافة {amt} نجمة",
                        payload=pid, provider_token="", currency="XTR",
                        prices=[LabeledPrice(label=f"{amt} نجمة", amount=amt)])
                    self._json({'success': True, 'url': inv})
                except Exception as ex:
                    self._json({'success': False, 'error': str(ex)})
            elif path == '/api/binance/status':
                pid = q.get('payment_id', [''])[0]
                p = db.get_binance_payment(pid)
                self._json({'status': p.get('status', 'pending') if p else 'not_found'})
            elif path == '/api/redeem':
                uid = int(q.get('user_id', [0])[0])
                code = q.get('code', [''])[0]
                amount = db.redeem_code(uid, code)
                if amount > 0:
                    self._json({'success': True, 'amount': amount})
                else:
                    self._json({'success': False, 'error': 'كود غير صحيح أو مستخدم بالفعل'})
            else:
                self._err(404)
        except Exception as ex:
            logger.error(f"GET {path}: {ex}")
            self._err(500)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length).decode()) if length else {}

            if path == '/api/purchase':
                prod = db.get_product(body['product_id'])
                if not prod:
                    return self._json({'success': False, 'error': 'Not found'})
                if prod['stock'] <= 0:
                    return self._json({'success': False, 'error': 'Out of stock'})
                final_price = calc_price_with_commission(prod['price'])
                uid = body['user_id']
                if db.get_balance(uid) < final_price:
                    return self._json({'success': False, 'error': 'insufficient'})
                if db.deduct_balance(uid, final_price):
                    db.update_stock(prod['id'], prod['stock'] - 1)
                    db.increment_sold(prod['id'])
                    db.add_purchase({
                        'user_id': str(uid), 'product_id': prod['id'],
                        'product_name': prod['name'], 'price': final_price,
                        'base_price': prod['price'], 'payment_method': 'balance',
                        'purchase_date': datetime.now().isoformat(),
                        'content': prod.get('content', ''), 'category': prod.get('category', ''),
                        'emoji': prod.get('emoji', '')
                    })
                    db.update_user_stats(uid, final_price)
                    self._json({'success': True, 'details': prod.get('content', ''),
                                 'new_balance': db.get_balance(uid)})
                else:
                    self._json({'success': False, 'error': 'Failed'})

            elif path == '/api/binance/create':
                uid = body.get('user_id')
                stars = int(body.get('stars', 0))
                if not uid or stars < 1:
                    return self._json({'success': False, 'error': 'Invalid'})
                pid, usd = db.create_binance_payment(uid, stars)
                self._json({'success': True, 'payment_id': pid, 'usd': usd,
                             'binance_pay_id': BINANCE_PAY_ID})

            elif path == '/api/admin/confirm_binance':
                if not is_admin(body.get('admin_id', 0)):
                    return self._err(403)
                pid = body.get('payment_id')
                p = db.confirm_binance_payment(pid)
                if p:
                    db.add_balance(p['user_id'], p['stars'])
                    db.add_usd_revenue(p['usd'])
                    try:
                        bot.send_message(int(p['user_id']),
                            f"✅ <b>تم تأكيد دفعتك عبر Binance Pay!</b>\n\n"
                            f"💵 المبلغ: <b>${p['usd']}</b>\n"
                            f"⭐ تمت إضافة <b>{p['stars']} نجمة</b> لرصيدك",
                            parse_mode='HTML')
                    except Exception:
                        pass
                    self._json({'success': True})
                else:
                    self._json({'success': False, 'error': 'Payment not found or already confirmed'})

            elif path == '/api/admin/confirm_binance_order':
                if not is_admin(body.get('admin_id', 0)):
                    return self._err(403)
                oid = body.get('order_id')
                o = db.confirm_binance_order(oid)
                if not o:
                    return self._json({'success': False, 'error': 'Order not found or already confirmed'})
                prod = db.get_product(o['product_id'])
                if prod and prod['stock'] > 0:
                    db.update_stock(prod['id'], prod['stock'] - 1)
                    db.increment_sold(prod['id'])
                db.add_purchase({
                    'user_id': o['user_id'], 'product_id': o['product_id'],
                    'product_name': o['product_name'], 'price': 0, 'price_usd': o['usd'],
                    'payment_method': 'binance', 'purchase_date': datetime.now().isoformat(),
                    'content': prod.get('content', '') if prod else '', 'category': prod.get('category', '') if prod else '',
                    'emoji': prod.get('emoji', '') if prod else ''
                })
                db.add_usd_revenue(o['usd'])
                try:
                    bot.send_message(int(o['user_id']),
                        f"✅ <b>تم تأكيد دفعتك عبر Binance Pay!</b>\n\n"
                        f"🛍️ المنتج: <b>{o['product_name']}</b>\n"
                        f"💵 المبلغ: <b>${o['usd']}</b>\n\n"
                        f"📋 محتوى المنتج:\n<code>{(prod.get('content','') if prod else '')}</code>",
                        parse_mode='HTML')
                except Exception:
                    pass
                self._json({'success': True})

            elif path == '/api/admin/add_admin':
                if not is_owner(body.get('admin_id', 0)):
                    return self._json({'success': False, 'error': 'للمالك فقط'})
                new_id = body.get('new_admin_id')
                if not new_id:
                    return self._json({'success': False, 'error': 'Invalid ID'})
                result = db.add_admin(new_id)
                self._json({'success': result, 'error': 'موجود بالفعل' if not result else ''})

            elif path == '/api/admin/remove_admin':
                if not is_owner(body.get('admin_id', 0)):
                    return self._json({'success': False, 'error': 'للمالك فقط'})
                db.remove_admin(body.get('target_id'))
                self._json({'success': True})

            elif path == '/api/admin/add_category':
                if not is_admin(body.get('admin_id', 0)):
                    return self._err(403)
                key = body.get('key', '').lower().strip().replace(' ', '_')
                if not key:
                    return self._json({'success': False, 'error': 'Invalid key'})
                db.add_category(key, {
                    'name': body.get('name', key), 'color': body.get('color', '#2f6bff'),
                    'emoji': body.get('emoji', ''), 'image': body.get('image', '')
                })
                self._json({'success': True})

            elif path == '/api/admin/delete_category':
                if not is_admin(body.get('admin_id', 0)):
                    return self._err(403)
                self._json({'success': db.delete_category(body.get('key', ''))})

            elif path == '/api/admin/add_product':
                if not is_admin(body.get('admin_id', 0)):
                    return self._err(403)
                db.add_product({
                    'name': body['name'], 'category': body.get('category', ''),
                    'price': int(body.get('price', 0) or 0), 'stock': int(body['stock']),
                    'price_usd': float(body.get('price_usd', 0) or 0),
                    'payment_method': body.get('payment_method', 'sb'),
                    'content': body.get('content', ''), 'description': body.get('description', ''),
                    'emoji': body.get('emoji', ''), 'image': body.get('image', '')
                })
                self._json({'success': True})

            elif path == '/api/admin/edit_product':
                if not is_admin(body.get('admin_id', 0)):
                    return self._err(403)
                self._json({'success': db.update_product(body['product_id'], body['updates'])})

            elif path == '/api/admin/delete_product':
                if not is_admin(body.get('admin_id', 0)):
                    return self._err(403)
                self._json({'success': db.delete_product(body['product_id'])})

            elif path == '/api/admin/add_banner':
                if not is_admin(body.get('admin_id', 0)):
                    return self._err(403)
                bid = db.add_banner({
                    'title': body.get('title', ''), 'subtitle': body.get('subtitle', ''),
                    'link': body.get('link', ''), 'image': body.get('image', '')
                })
                self._json({'success': True, 'banner_id': bid})

            elif path == '/api/admin/delete_banner':
                if not is_admin(body.get('admin_id', 0)):
                    return self._err(403)
                self._json({'success': db.delete_banner(body.get('banner_id', ''))})

            elif path == '/api/admin/upload_image':
                if not is_admin(body.get('admin_id', 0)):
                    return self._err(403)
                ext = body.get('ext', 'jpg').lower()
                if ext not in ('jpg', 'jpeg', 'png', 'gif', 'webp'):
                    ext = 'jpg'
                import base64 as _b64
                os.makedirs('bot_data/uploads', exist_ok=True)
                fname = f"{uuid.uuid4().hex}.{ext}"
                try:
                    with open(f'bot_data/uploads/{fname}', 'wb') as fimg:
                        fimg.write(_b64.b64decode(body.get('data', '')))
                    self._json({'success': True, 'url': f'/uploads/{fname}'})
                except Exception as ex:
                    self._json({'success': False, 'error': str(ex)})

            elif path == '/api/purchase_binance/create':
                uid = body.get('user_id')
                pid = body.get('product_id')
                prod = db.get_product(pid)
                if not uid or not prod:
                    return self._json({'success': False, 'error': 'Invalid'})
                if prod['stock'] <= 0:
                    return self._json({'success': False, 'error': 'Out of stock'})
                usd = prod.get('price_usd') or round(prod.get('price', 0) * STAR_TO_USD, 2)
                oid = db.create_binance_order(uid, pid, usd, prod['name'])
                self._json({'success': True, 'order_id': oid, 'usd': usd, 'binance_pay_id': BINANCE_PAY_ID})

            elif path == '/api/admin/add_redeem_code':
                if not is_admin(body.get('admin_id', 0)):
                    return self._err(403)
                self._json({'success': db.add_redeem_code(body.get('code', ''), int(body.get('amount', 0)))})

            elif path == '/api/admin/remove_redeem_code':
                if not is_admin(body.get('admin_id', 0)):
                    return self._err(403)
                self._json({'success': db.delete_redeem_code(body.get('code', ''))})

            elif path == '/api/admin/add_stars':
                if not is_admin(body.get('admin_id', 0)):
                    return self._err(403)
                db.add_balance(body['user_id'], body['amount'])
                self._json({'success': True})

            elif path == '/api/admin/broadcast':
                if not is_admin(body.get('admin_id', 0)):
                    return self._err(403)
                sent = 0
                for u in db.get_all_users():
                    try:
                        bot.send_message(int(u['id']), body['message'])
                        sent += 1
                        time.sleep(0.05)
                    except Exception:
                        pass
                self._json({'success': True, 'sent': sent})
            else:
                self._err(404)
        except Exception as ex:
            logger.error(f"POST {path}: {ex}")
            self._err(500)

    def _serve_upload(self, path):
        fname = os.path.basename(path)
        fpath = os.path.join('bot_data', 'uploads', fname)
        if not os.path.isfile(fpath):
            return self._err(404)
        ext = fname.rsplit('.', 1)[-1].lower()
        ctype = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
                 'gif': 'image/gif', 'webp': 'image/webp'}.get(ext, 'application/octet-stream')
        with open(fpath, 'rb') as fimg:
            data = fimg.read()
        self.send_response(200)
        self.send_header('Content-type', ctype)
        self.send_header('Cache-Control', 'public, max-age=86400')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(data)

    def _html(self, c):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(c.encode())

    def _json(self, data):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def _err(self, code):
        self.send_response(code)
        self.end_headers()

    def log_message(self, *a):
        pass

# ==================== BOT HANDLERS ====================
@bot.message_handler(commands=['start'])
def start(msg):
    db.get_or_create_user(msg.from_user.id, msg.from_user.username, msg.from_user.first_name)
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🛍️ افتح المتجر", web_app=types.WebAppInfo(url=WEBAPP_URL)))
    if is_admin(msg.from_user.id):
        kb.add(types.InlineKeyboardButton("🔧 لوحة الأدمن", web_app=types.WebAppInfo(url=f"{WEBAPP_URL}?tab=admin")))
    bot.send_message(msg.chat.id,
        f"<b>✨ SoNs</b>\n\n"
        f"👋 مرحباً {msg.from_user.first_name}!\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛍️ متجرك الرقمي الموثوق\n"
        f"⭐ دفع بنجوم تيليجرام\n"
        f"🟡 دفع بـ Binance Pay\n"
        f"⚡ توصيل فوري\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👇 اضغط لفتح التطبيق",
        reply_markup=kb, parse_mode='HTML')

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
    except Exception:
        bot.reply_to(msg, "❌ معرف غير صحيح")

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
    except Exception:
        bot.reply_to(msg, "❌ خطأ")

@bot.pre_checkout_query_handler(func=lambda q: True)
def pre_checkout(q):
    p = db.get_pending(q.invoice_payload)
    bot.answer_pre_checkout_query(q.id, ok=bool(p), error_message="انتهت الجلسة" if not p else None)

@bot.message_handler(content_types=['successful_payment'])
def payment_ok(msg):
    pay = msg.successful_payment
    p = db.get_pending(pay.invoice_payload)
    if not p:
        return
    if p['type'] == 'deposit':
        db.add_balance(p['user_id'], p['amount'])
        bot.send_message(p['user_id'],
            f"✅ <b>تم الشحن!</b>\n⭐ تمت إضافة <b>{p['amount']} نجمة</b>",
            parse_mode='HTML')
    db.remove_pending(pay.invoice_payload)

# ==================== RUN ====================
def run_web():
    HTTPServer((WEBAPP_HOST, WEBAPP_PORT), Handler).serve_forever()

def run_bot():
    while True:
        try:
            bot.remove_webhook()
            bot.infinity_polling(timeout=60)
        except Exception as ex:
            logger.error(f"Bot: {ex}")
            time.sleep(10)

if __name__ == "__main__":
    print("\n" + "═" * 60)
    print("✨  SoNs Store  ✨")
    print(f"🌐  http://localhost:{WEBAPP_PORT}")
    print(f"🟡  Binance Pay ID: {BINANCE_PAY_ID}")
    print(f"🔧  Owner ID: {OWNER_ID}")
    print("═" * 60 + "\n")
    print("📋 خطوات الإعداد:")
    print("   1. عدّل BOT_TOKEN و OWNER_ID و WEBAPP_URL في الأعلى أو كمتغيرات بيئة")
    print("   2. عدّل BINANCE_PAY_ID إلى معرف حسابك الحقيقي على Binance Pay")
    print("   3. أضف منتجاتك من لوحة الأدمن (الاسم، السعر، المخزون، المحتوى، صورة/إيموجي)")
    print("   4. عند استلام دفعة Binance Pay تحقق من كود التتبع (المُلاحظة) وأكّدها من لوحة الأدمن\n")
    threading.Thread(target=run_web, daemon=True).start()
    run_bot()
