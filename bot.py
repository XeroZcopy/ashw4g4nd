import asyncio
import logging
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

from telethon import TelegramClient
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    BotCommand
)

# =====================================================
# CONFIG
# =====================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_FILE = os.path.join(BASE_DIR, 'accounts.json')
DB_FILE = os.path.join(BASE_DIR, 'umbrella.db')

GATEWAY_TOKEN = '8705134820:AAFMJY_4WYgW06AHw7hRYHYQYRJXdhTmtkY'
SPONSOR_CHANNEL = '@RouterSCH'
ADMIN_ID = 5277564584

BOT_SJ = 'sjgdfj0ghjdhjjegtjjebot'
BOT_CLONE = 'lolsas_clone_bot'
BOT_MAIN = 'lolsbot'
DS_TOKEN = 'kDJcZkqUS2u6vZCdOMoimHcv5fqQuI7y'
DS_URL = 'https://api.depsearch.sbs/quest={phone}&token=' + DS_TOKEN
DS_TRASH = {'1win', '1win_2', '1win_2024', '1win_2025'}
SJ_MARKERS = [
    'Телефон:', 'Оператор:', 'Регион:', 'Страна:',
    'Телефонные книги', 'VK', 'Одноклассники',
    'Telegram:', 'ok.ru', 'Ничего не найдено',
    'не найдено', '⚠', '❌', 'by @sjgdfj0ghjdhjjegtjjebot'
]

# =====================================================
# LOGGING
# =====================================================
LOG_DIR = os.path.join(BASE_DIR, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger('umbrella')
logger.setLevel(logging.DEBUG)
_fmt = logging.Formatter('%(asctime)s | %(levelname)-8s | %(message)s', datefmt='%H:%M:%S')
_ch = logging.StreamHandler()
_ch.setLevel(logging.INFO)
_ch.setFormatter(_fmt)
logger.addHandler(_ch)
_fh = RotatingFileHandler(
    os.path.join(LOG_DIR, 'umbrella.log'),
    maxBytes=10 * 1024 * 1024, backupCount=5, encoding='utf-8'
)
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter(
    '%(asctime)s | %(levelname)-8s | %(funcName)-20s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
))
logger.addHandler(_fh)

# =====================================================
# DATABASE
# =====================================================
def get_db():
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def init_db():
    c = get_db().cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        first_seen TEXT,
        is_subscribed INTEGER DEFAULT 0
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS mirrors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_token TEXT UNIQUE,
        bot_username TEXT,
        created_by INTEGER,
        created_at TEXT,
        is_active INTEGER DEFAULT 1
    )""")
    get_db().commit()
    logger.info("DB ready")

def db_exec(sql, params=(), one=False):
    conn = get_db()
    c = conn.cursor()
    c.execute(sql, params)
    r = c.fetchone() if one else c.fetchall()
    conn.commit()
    conn.close()
    return r

def add_user(uid):
    db_exec("INSERT OR IGNORE INTO users (user_id, first_seen, is_subscribed) VALUES (?, ?, 0)",
            (uid, datetime.now().isoformat()))

def get_user(uid):
    r = db_exec("SELECT user_id, first_seen, is_subscribed FROM users WHERE user_id=?", (uid,), one=True)
    if r:
        return dict(zip(['user_id', 'first_seen', 'is_subscribed'], r))
    return None

def set_subscribed(uid, val=1):
    add_user(uid)
    db_exec("UPDATE users SET is_subscribed=? WHERE user_id=?", (val, uid))

def is_subscribed(uid):
    u = get_user(uid)
    return u and u['is_subscribed'] == 1

def get_all_uids():
    return [r[0] for r in db_exec("SELECT user_id FROM users")]

def add_mirror(token, username, by):
    db_exec("INSERT OR IGNORE INTO mirrors (bot_token, bot_username, created_by, created_at, is_active) VALUES (?,?,?,?,1)",
            (token, username, by, datetime.now().isoformat()))

def get_active_mirrors():
    return db_exec("SELECT id, bot_token, bot_username, created_by FROM mirrors WHERE is_active=1 ORDER BY id DESC LIMIT 2")

def get_all_mirrors():
    return db_exec("SELECT id, bot_token, bot_username, created_by, created_at FROM mirrors WHERE is_active=1 ORDER BY id DESC")

def del_mirror(mid):
    db_exec("UPDATE mirrors SET is_active=0 WHERE id=?", (mid,))

# =====================================================
# REGEX
# =====================================================
RE = {
    'phone': re.compile(r'Телефон:\s*(.+)'),
    'operator': re.compile(r'Оператор:\s*(.+)'),
    'region': re.compile(r'Регион:\s*(.+)'),
    'country': re.compile(r'Страна:\s*(.+)'),
    'tg': re.compile(r'•\s*(@\w+)\s*\(ID:\s*(\d+)\)'),
    'pb': re.compile(r'Телефонные книги\s*\(\d+\):\s*(.+?)(?:\n\n|\n[🧑💬⚠])', re.S),
    'vk': re.compile(r'•\s*(.+?)\s*\((https?://vk\.com/[^\s)]+)\)'),
    'ok': re.compile(r'•\s*(.+?)\s*\((https?://ok\.ru/[^\s)]+)\)'),
    'ls_uid': re.compile(r'user_id[:\s]+`?(\d+)`?'),
    'ls_geo': re.compile(r'geo[:\s]+(.+)'),
    'ls_name_sec': re.compile(r'^name$'),
    'ls_uname_sec': re.compile(r'^username$'),
    'ls_name': re.compile(r'\d+[:\s]+(.+)'),
    'ls_uname': re.compile(r'@(\w+)'),
    'ls_fmsg': re.compile(r'first_msg[:\s]+(.+)'),
    'ls_reg': re.compile(r'registration[:\s]+(.+)'),
    'ls_ban': re.compile(r'lols_ban[:\s]+(.+)'),
    'ls_stats': re.compile(r'stats[:\s]+(.+)'),
    'strip_stars': re.compile(r'\*'),
    'strip_bt': re.compile(r'`'),
    'strip_footer': re.compile(r"\n*by\s+@\S+\s*$"),
    'phone_clean': re.compile(r'[\s\-\(\)\+]'),
}

# =====================================================
# RATE LIMITER
# =====================================================
class RL:
    def __init__(self, n=3, t=1.0):
        self.n, self.t, self.ts, self.lock = n, t, [], asyncio.Lock()
    async def get(self):
        async with self.lock:
            now = time.time()
            self.ts = [x for x in self.ts if now - x < self.t]
            if len(self.ts) >= self.n:
                await asyncio.sleep(self.t - (now - self.ts[0]))
                self.ts = [x for x in self.ts if time.time() - x < self.t]
            self.ts.append(time.time())

sj_rl = RL(5, 1.0)
ls_rl = RL(5, 1.0)
ds_rl = RL(2, 1.0)

# =====================================================
# ACCOUNTS (Telethon)
# =====================================================
_acc_cache = None
_acc_time = 0

def load_accs():
    global _acc_cache, _acc_time
    now = time.time()
    if _acc_cache and now - _acc_time < 60:
        return _acc_cache
    try:
        with open(ACCOUNTS_FILE, 'r', encoding='utf-8') as f:
            _acc_cache = json.load(f)
            _acc_time = now
            return _acc_cache
    except Exception as e:
        logger.error(f"accounts: {e}")
        return _acc_cache or []

def accs_by_role(role):
    return [a for a in load_accs() if a.get('role') == role and a.get('status') == 'active']

def mk_client(a):
    return TelegramClient(a['session'], a['api_id'], a['api_hash'])

# =====================================================
# UTILS
# =====================================================
def _rx(key, text):
    m = RE[key].search(text)
    return m.group(1).strip() if m else None

def clean_md(t):
    c = RE['strip_stars'].sub('', str(t))
    c = RE['strip_bt'].sub('', c)
    return RE['strip_footer'].sub('', c).strip()

async def _fc(msg, btn):
    try:
        await asyncio.wait_for(msg.click(data=btn.data), timeout=0.3)
    except:
        pass

def _fb(msg, txt):
    try:
        if msg.buttons:
            for row in msg.buttons:
                for b in row:
                    if b.text and txt.lower() in b.text.lower():
                        return b
    except:
        pass
    return None

def is_phone(t):
    c = t.replace('+', '').replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
    return c.isdigit() and 7 <= len(c) <= 15

def is_id(t):
    return t.isdigit() and 5 <= len(t) <= 15

def is_uname(t):
    return t.startswith('@') and len(t) > 1

def _esc(t):
    return str(t).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def _t(i, tot, lim):
    return '└' if i == min(tot, lim) - 1 else '├'

# =====================================================
# DEEPSEARCH
# =====================================================
async def deepsearch(query):
    phone = RE['phone_clean'].sub('', query)
    if not phone.startswith('7') and len(phone) == 10:
        phone = '7' + phone
    await ds_rl.get()
    try:
        req = urllib.request.Request(DS_URL.format(phone=phone), headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        logger.warning(f"ds: {e}")
        return None

# =====================================================
# PARSERS
# =====================================================
def parse_sj(text):
    c = clean_md(text)
    d = {'phone': None, 'operator': None, 'region': None, 'country': None,
         'telegram': [], 'books': [], 'vk': [], 'ok': []}
    d['phone'] = _rx('phone', c)
    d['operator'] = _rx('operator', c)
    d['region'] = _rx('region', c)
    d['country'] = _rx('country', c)
    for u, uid in RE['tg'].findall(c):
        d['telegram'].append({'username': u, 'id': uid})
    pb = RE['pb'].search(c)
    if pb:
        raw = re.sub(r'\s+', ' ', pb.group(1)).strip()
        d['books'] = [n.strip() for n in raw.split(',') if n.strip() and len(n.strip()) > 1]
    for n, u in RE['vk'].findall(c):
        d['vk'].append({'name': re.sub(r'\s*\d{2}\.\d{2}\.\d{4}', '', n).strip(), 'url': u.strip()})
    for n, u in RE['ok'].findall(c):
        d['ok'].append({'name': n.strip(), 'url': u.strip()})
    return d

def parse_lolsas(text):
    t = RE['strip_stars'].sub('', text)
    d = {'type': None, 'user_id': None, 'geo': None, 'names': [], 'usernames': [],
         'first_msg': None, 'registration': None, 'lols_ban': None, 'stats': None}
    sec = None
    for line in t.split('\n'):
        cl = line.strip().replace('┊', '').replace('├', '').replace('└', '').strip()
        if cl in ('user', 'bot', 'channel'):
            d['type'] = cl
        m = RE['ls_uid'].search(cl)
        if m: d['user_id'] = m.group(1)
        m = RE['ls_geo'].search(cl)
        if m: d['geo'] = m.group(1).strip()
        if RE['ls_name_sec'].match(cl):
            sec = 'name'; continue
        if RE['ls_uname_sec'].match(cl):
            sec = 'uname'; continue
        if sec == 'name':
            m = RE['ls_name'].search(cl)
            if m: d['names'].append(m.group(1).strip())
            else: sec = None
        if sec == 'uname':
            m = RE['ls_uname'].search(cl)
            if m: d['usernames'].append(m.group(1))
            else: sec = None
        m = RE['ls_fmsg'].search(cl)
        if m: d['first_msg'] = m.group(1).strip(); sec = None
        m = RE['ls_reg'].search(cl)
        if m: d['registration'] = m.group(1).strip()
        m = RE['ls_ban'].search(cl)
        if m: d['lols_ban'] = cl
        m = RE['ls_stats'].search(cl)
        if m: d['stats'] = m.group(1).strip()
    return d

# =====================================================
# TELETHON SEARCH
# =====================================================
async def _poll_btn(c, ent, kw, to=8):
    end = time.time() + to
    while time.time() < end:
        await asyncio.sleep(0.02)
        try:
            for m in await c.get_messages(ent, limit=3):
                if m.buttons and _fb(m, kw):
                    return m
        except: pass
    return None

async def _poll_res(c, ent, to=20):
    end = time.time() + to
    while time.time() < end:
        await asyncio.sleep(0.05)
        try:
            for m in await c.get_messages(ent, limit=3):
                if m.text and len(m.text) > 10 and any(x in m.text for x in SJ_MARKERS):
                    return m
        except: pass
    return None

async def _sj_search(c, ent, query, mode):
    try:
        for m in await c.get_messages(ent, limit=3):
            if m.buttons and _fb(m, 'Отменить'):
                await _fc(m, _fb(m, 'Отменить'))
                await asyncio.sleep(0.1)
                break
    except: pass
    try: await c.send_message(ent, '/start')
    except: pass
    menu = await _poll_btn(c, ent, 'Искать', to=8)
    if not menu: return None
    kw = 'Номер телефона' if mode == 'phone' else 'Telegram'
    await _fc(menu, _fb(menu, 'Искать'))
    svc, oid, otxt = None, menu.id, menu.text
    end = time.time() + 8
    while time.time() < end:
        await asyncio.sleep(0.02)
        try:
            e = await c.get_messages(ent, ids=oid)
            if e and e.text != otxt:
                svc = e; break
        except: pass
        try:
            for m in await c.get_messages(ent, limit=3):
                if m.id != oid and _fb(m, kw):
                    svc = m; break
        except: pass
        if svc: break
    if not svc or not _fb(svc, kw): return None
    await _fc(svc, _fb(svc, kw))
    await asyncio.sleep(0.1)
    await c.send_message(ent, query)
    r = await _poll_res(c, ent, to=30)
    return r.text if r else None

async def _ls_search(c, em, ec, q):
    await c.send_message(em, '/start')
    await asyncio.sleep(0.3)
    await c.send_message(ec, '/start osin')
    await asyncio.sleep(0.5)
    await c.send_message(ec, q)
    for _ in range(3):
        await asyncio.sleep(2)
        for m in await c.get_messages(ec, limit=5):
            if m.buttons:
                for row in m.buttons:
                    for b in row:
                        if b.text and 'Повторить' in b.text:
                            await _fc(m, b); await asyncio.sleep(3); break
        for m in await c.get_messages(ec, limit=5):
            if m.text and ('user_id' in m.text or 'channel' in m.text or 'bot' in m.text):
                return m.text
    return None

async def try_sj(query, mode):
    for a in accs_by_role('sj'):
        try:
            await sj_rl.get()
            c = mk_client(a); await c.start()
            r = await _sj_search(c, await c.get_input_entity(BOT_SJ), query, mode)
            await c.disconnect()
            if r: return r
        except Exception as e:
            logger.warning(f"sj {a['name']}: {e}")
            try: await c.disconnect()
            except: pass
    return None

async def try_lolsas(query):
    for a in accs_by_role('lolsas'):
        try:
            await ls_rl.get()
            c = mk_client(a); await c.start()
            r = await _ls_search(c, await c.get_input_entity(BOT_MAIN), await c.get_input_entity(BOT_CLONE), query)
            await c.disconnect()
            if r: return r
        except Exception as e:
            logger.warning(f"ls {a['name']}: {e}")
            try: await c.disconnect()
            except: pass
    return None

# =====================================================
# CACHE + SEARCH
# =====================================================
_pc = {}
_PCT = 300

def cparse(fn, text):
    if not text: return {}
    k = (fn.__name__, hash(text))
    now = time.time()
    if k in _pc:
        ct, cr = _pc[k]
        if now - ct < _PCT: return cr
    r = fn(text)
    _pc[k] = (now, r)
    if len(_pc) > 5000:
        for ok in sorted(_pc, key=lambda x: _pc[x][0])[:2500]:
            del _pc[ok]
    return r

async def do_search(mode, query):
    t0 = time.time()
    sj = ls = ds = None
    rid = None

    if mode == 'phone':
        sj = await try_sj(query, 'phone')
        ds = await asyncio.get_event_loop().run_in_executor(None, deepsearch, query)
    elif mode == 'id':
        sj = await try_sj(query, 'id')
        ls = await try_lolsas(query)
        if sj:
            p = cparse(parse_sj, sj)
            if p.get('phone'):
                ds = await asyncio.get_event_loop().run_in_executor(None, deepsearch, p['phone'])
    elif mode == 'username':
        ls = await try_lolsas(query)
        lp = cparse(parse_lolsas, ls) if ls else {}
        rid = lp.get('user_id')
        if rid:
            sj = await try_sj(rid, 'id')
            if sj:
                p = cparse(parse_sj, sj)
                if p.get('phone'):
                    ds = await asyncio.get_event_loop().run_in_executor(None, deepsearch, p['phone'])
    elif mode == 'fio':
        ds = await asyncio.get_event_loop().run_in_executor(None, deepsearch, query)
    elif mode == 'email':
        ds = await asyncio.get_event_loop().run_in_executor(None, deepsearch, query)
    elif mode == 'auto':
        ds = await asyncio.get_event_loop().run_in_executor(None, deepsearch, query)

    lp = cparse(parse_lolsas, ls) if ls else {}
    sp = cparse(parse_sj, sj) if sj else {}

    res = {
        'display': query,
        'id': lp.get('user_id') or query,
        'usernames': lp.get('usernames', []),
        'first_msg': lp.get('first_msg'),
        'registration': lp.get('registration'),
        'geo': lp.get('geo'),
        'ban_status': lp.get('lols_ban', ''),
        'phone': sp.get('phone'), 'operator': sp.get('operator'),
        'region': sp.get('region'), 'country': sp.get('country'),
        'phonebook': sp.get('books', []),
        'vk': sp.get('vk', []), 'ok': sp.get('ok', []),
        'telegram': sp.get('telegram'),
        'names': lp.get('names', []),
        'stats': lp.get('stats'),
        'emails': [], 'fios': []
    }

    if mode == 'id' and lp.get('usernames'):
        res['display'] = '@' + lp['usernames'][0]
    elif mode == 'username' and not query.startswith('@'):
        res['display'] = '@' + query

    if ds and 'results' in ds:
        ems, fs = [], []
        for r in ds['results']:
            e = r.get('email')
            if e and '@' in e: ems.append(e)
            f = r.get('fio') or r.get('full_name')
            if f and len(f) > 2 and r.get('data') not in DS_TRASH: fs.append(f)
        res['emails'] = list(dict.fromkeys(ems))
        res['fios'] = list(dict.fromkeys(fs))

    return res, time.time() - t0

# =====================================================
# FORMAT RESULT
# =====================================================
def fmt_result(d, q):
    r = d
    if not r or (isinstance(r, dict) and not r.get('id') and not r.get('phone')):
        return '<b>Ничего не найдено</b>'
    L = [f'<b>🔭 Результат на {r.get("display", q)}</b>', '',
         f'👨‍💻 Айди: <code>{r.get("id", q)}</code>']
    if r.get('usernames'): L.append(f'💬 Юзернейм: <b>@{r["usernames"][0]}</b>')
    if r.get('first_msg'): L.append(f'🕒 Первое сообщение: <i>{_esc(r["first_msg"])}</i>')
    if r.get('registration'): L.append(f'📅 Регистрация: <i>{_esc(r["registration"])}</i>')
    if r.get('geo'): L.append(f'🌐 Geo: {_esc(r["geo"])}')
    ban = r.get('ban_status', '')
    if 'не заблокирован' in ban: L.append('✅ Репутация: <b>Чистый</b>')
    elif 'заблокирован' in ban: L.append('❌ Репутация: <b>Заблокирован</b>')
    L.append('')
    if r.get('phone'):
        L.append(f'📱 Телефон: <code>{r["phone"]}</code>')
        if r.get('operator'): L.append(f'├ Оператор: <i>{_esc(r["operator"])}</i>')
        if r.get('region'): L.append(f'├ Регион: <i>{_esc(r["region"])}</i>')
        if r.get('country'): L.append(f'└ Страна: <i>{_esc(r["country"])}</i>')
        L.append('')
    if r.get('phonebook'):
        n = len(r['phonebook']); L.append(f'<b>💾 Телефонная книга ({n}):</b>')
        for i, nm in enumerate(r['phonebook'][:15]): L.append(f'{_t(i,n,15)} <code>{_esc(nm)}</code>')
        L.append('')
    if r.get('names'):
        n = len(r['names']); L.append(f'<b>ℹ️ История имён ({n}):</b>')
        for i, nm in enumerate(r['names'][:10]): L.append(f'{_t(i,n,10)} {_esc(nm)}')
        L.append('')
    if r.get('usernames') and len(r['usernames']) > 1:
        n = len(r['usernames']); L.append(f'<b>🔄 История юзеров ({n}):</b>')
        for i, u in enumerate(r['usernames'][:10]): L.append(f'{_t(i,n,10)} <code>@{u}</code>')
        L.append('')
    if r.get('vk'):
        n = len(r['vk']); L.append(f'<b>🌐 ВКонтакте ({n}):</b>')
        for i, p in enumerate(r['vk'][:5]): L.append(f'{_t(i,n,5)} <a href="{p.get("url","")}">{_esc(p["name"])}</a>')
        L.append('')
    if r.get('ok'):
        n = len(r['ok']); L.append(f'<b>🌐 Одноклассники ({n}):</b>')
        for i, p in enumerate(r['ok'][:5]): L.append(f'{_t(i,n,5)} <a href="{p.get("url","")}">{_esc(p["name"])}</a>')
        L.append('')
    tg = r.get('telegram', [])
    if tg:
        if isinstance(tg, list) and tg:
            n = len(tg); L.append(f'<b>✈ Telegram ({n}):</b>')
            for i, t in enumerate(tg[:5]):
                if isinstance(t, dict): L.append(f'{_t(i,n,5)} <code>{t["username"]} (ID: {t["id"]})</code>')
                else: L.append(f'{_t(i,n,5)} <code>{t}</code>')
        elif isinstance(tg, str): L.append(f'✈ Telegram: <code>{tg}</code>')
        L.append('')
    if r.get('emails'):
        n = len(r['emails']); L.append(f'<b>📧 E-mail ({n}):</b>')
        for i, e in enumerate(r['emails'][:5]): L.append(f'{_t(i,n,5)} <code>{e}</code>')
        L.append('')
    if r.get('fios'): L.append(f'👤 ФИО: <b><code>{_esc(r["fios"][0])}</code></b>'); L.append('')
    if r.get('stats'): L.append(f'📊 <i>{_esc(r["stats"])}</i>'); L.append('')
    L.append('📱 Найдено через <b>Umbrella Search</b>')
    return '\n'.join(L)

def get_btns(phone):
    if not phone: return None
    c = phone.replace('+', '').replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔵 Max.ru', url=f'https://max.ru/+{c}')],
        [InlineKeyboardButton(text='✈ Telegram', url=f'https://t.me/+{c}'),
         InlineKeyboardButton(text='💬 WhatsApp', url=f'https://wa.me/{c}')]
    ])

# =====================================================
# KEYBOARDS
# =====================================================
def kb_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Поиск", callback_data="search"),
         InlineKeyboardButton(text="📖 Примеры", callback_data="examples")]
    ])

def kb_search_examples():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Поиск", callback_data="search"),
         InlineKeyboardButton(text="🔙 Назад", callback_data="back")]
    ])

def kb_back_main():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back")]
    ])

def kb_subscribe():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться", url=f"https://t.me/{SPONSOR_CHANNEL.lstrip('@')}")],
        [InlineKeyboardButton(text="✅ Проверить", callback_data="check_sub")]
    ])

def kb_gateway_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Поиск", url="https://t.me/umbrella_search_bot?start=search")],
        [InlineKeyboardButton(text="🪞 Зеркала", callback_data="mirrors")],
        [InlineKeyboardButton(text="➕ Создать зеркало", callback_data="create_mirror")]
    ])

def kb_mirrors(mirrors):
    buttons = []
    for m in mirrors:
        mid, token, username, created_by = m
        uname = username or 'bot'
        buttons.append([InlineKeyboardButton(text=f"🪞 @{uname}", url=f"https://t.me/{uname}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_gateway")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def kb_admin():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="a_stats"),
         InlineKeyboardButton(text="🪞 Все зеркала", callback_data="a_all_mirrors")],
        [InlineKeyboardButton(text="➕ Создать зеркало", callback_data="create_mirror")]
    ])

# =====================================================
# TEXTS
# =====================================================
T_WELCOME_SUB = """🔐 <b>Добро пожаловать в Umbrella Search</b>

Для использования бота необходимо подписаться на наши каналы:

📢 <b>{channel}</b>

Нажмите «Подписаться» и перейдите на канал.
После подписки нажмите «Проверить»."""

T_MAIN_MENU = """🏠 <b>Вы находитесь в главном меню</b>

> By Umbrella Search"""

T_EXAMPLES = """ℹ️ <b>Основные примеры использования сервиса</b>

👤 Для ФИО:
Фамилия Имя Отчество
Фамилия Имя Отчество Дата рождения

📱 Для контактных данных:
Номер в международном формате

📧 Для электронной почты:
Полный любой электронный адрес

🚔 Для транспортных средств:
Автомобильный номер
VIN-код

👁‍🗨 Для телеграм профиля:
@логин
tgАЙДИ"""

T_SEARCH_PROMPT = """🔍 <b>Отправьте запрос для поиска</b>

📞 <code>79991099999</code> — номер телефона
📧 <code>@username</code> — Telegram
🆔 <code>1234567890</code> — Telegram ID
👤 <code>ФИО</code> — фамилия имя отчество
📧 <code>email@mail.ru</code> — электронная почта
🚔 <code>А123БВ777</code> — номер авто"""

T_SEARCH_LOCK = '<b>🔍 Поиск выполняется</b>\n\nДождитесь завершения текущего поиска.'
T_NOFMT = '<b>❌ Не удалось распознать формат</b>\n\nОтправьте номер, @username, Telegram ID, ФИО, email или номер авто.'
T_ERR = '<b>❌ Ошибка</b>\n\nПопробуйте позже.'
T_SEARCH = '<b>🔍 Поиск...</b>\n\nПожалуйста подождите.'
T_SUB_OK = '<b>✅ Подписка подтверждена!</b>\n\nДобро пожаловать в Umbrella Search.'
T_SUB_FAIL = '<b>❌ Вы не подписались</b>\n\nПодпишитесь на канал и попробуйте снова.'
T_MIRRORS = '<b>🪞 Активные зеркала</b>\n\nНажмите на зеркало для перехода:'
T_MIRROR_CREATED = '<b>✅ Зеркало создано!</b>\n\nЗапуск: <code>python bot.py {token}</code>'
T_TOKEN_PROMPT = '<b>➕ Создать зеркало</b>\n\nОтправьте токен нового бота:\n<code>123:ABC</code>'

# =====================================================
# GATEWAY BOT (подписка + зеркала)
# =====================================================
async def run_gateway():
    bot = Bot(token=GATEWAY_TOKEN)
    dp = Dispatcher()
    admin_state = {}

    @dp.message(Command("start"))
    async def cmd_start(m: Message):
        uid = m.from_user.id
        add_user(uid)
        logger.info(f"gateway /start {uid} @{m.from_user.username}")

        if uid == ADMIN_ID:
            await m.answer(T_MAIN_MENU, parse_mode="HTML", reply_markup=kb_admin())
            return

        if is_subscribed(uid):
            await m.answer(T_MAIN_MENU, parse_mode="HTML", reply_markup=kb_gateway_menu())
        else:
            await m.answer(
                T_WELCOME_SUB.format(channel=SPONSOR_CHANNEL),
                parse_mode="HTML", reply_markup=kb_subscribe()
            )

    @dp.callback_query(F.data == "check_sub")
    async def cb_check_sub(cb: CallbackQuery):
        uid = cb.from_user.id
        try:
            member = await bot.get_chat_member(SPONSOR_CHANNEL, uid)
            if member.status in ('member', 'administrator', 'creator'):
                set_subscribed(uid)
                await cb.message.edit_text(T_SUB_OK, parse_mode="HTML")
                await asyncio.sleep(1)
                await cb.message.edit_text(T_MAIN_MENU, parse_mode="HTML", reply_markup=kb_gateway_menu())
            else:
                await cb.answer(T_SUB_FAIL, show_alert=True)
        except Exception as e:
            logger.warning(f"sub check: {e}")
            set_subscribed(uid)
            await cb.message.edit_text(T_SUB_OK, parse_mode="HTML")
            await asyncio.sleep(1)
            await cb.message.edit_text(T_MAIN_MENU, parse_mode="HTML", reply_markup=kb_gateway_menu())
        await cb.answer()

    @dp.callback_query(F.data == "back_gateway")
    async def cb_back_gateway(cb: CallbackQuery):
        uid = cb.from_user.id
        if uid == ADMIN_ID:
            await cb.message.edit_text(T_MAIN_MENU, parse_mode="HTML", reply_markup=kb_admin())
        else:
            await cb.message.edit_text(T_MAIN_MENU, parse_mode="HTML", reply_markup=kb_gateway_menu())
        await cb.answer()

    @dp.callback_query(F.data == "mirrors")
    async def cb_mirrors(cb: CallbackQuery):
        uid = cb.from_user.id
        mirrors = get_active_mirrors()
        if mirrors:
            txt = T_MIRRORS
            await cb.message.edit_text(txt, parse_mode="HTML", reply_markup=kb_mirrors(mirrors))
        else:
            await cb.message.edit_text("🪞 <b>Активных зеркал нет</b>\n\nСоздайте зеркало или обратитесь к администратору.", parse_mode="HTML", reply_markup=kb_back_gateway())
        await cb.answer()

    @dp.callback_query(F.data == "create_mirror")
    async def cb_create_mirror(cb: CallbackQuery):
        uid = cb.from_user.id
        if uid != ADMIN_ID:
            await cb.answer("⛔ Только для админа", show_alert=True)
            return
        await cb.message.edit_text(T_TOKEN_PROMPT, parse_mode="HTML")
        admin_state[uid] = 'cmirror'
        await cb.answer()

    @dp.callback_query(F.data == "a_stats")
    async def cb_stats(cb: CallbackQuery):
        if cb.from_user.id != ADMIN_ID:
            await cb.answer(); return
        users = db_exec("SELECT COUNT(*) FROM users", one=True)[0]
        mirrors = db_exec("SELECT COUNT(*) FROM mirrors WHERE is_active=1", one=True)[0]
        subs = db_exec("SELECT COUNT(*) FROM users WHERE is_subscribed=1", one=True)[0]
        await cb.message.edit_text(
            f"📊 <b>Статистика Umbrella</b>\n\n"
            f"👥 Пользователей: {users}\n"
            f"✅ Подписано: {subs}\n"
            f"🪞 Зеркал: {mirrors}",
            parse_mode="HTML", reply_markup=kb_admin()
        )
        await cb.answer()

    @dp.callback_query(F.data == "a_all_mirrors")
    async def cb_all_mirrors(cb: CallbackQuery):
        if cb.from_user.id != ADMIN_ID:
            await cb.answer(); return
        ms = get_all_mirrors()
        txt = "🪞 <b>Все зеркала</b>\n\n"
        if ms:
            for r in ms:
                mid, token, username, created_by, created_at = r
                status = "🟢" if r[4] else "🔴"
                txt += f"{status} @{username or '?'} | ID: {mid}\n"
        else:
            txt += "Нет зеркал."
        await cb.message.edit_text(txt, parse_mode="HTML", reply_markup=kb_admin())
        await cb.answer()

    @dp.message(F.text)
    async def on_text(m: Message):
        uid = m.from_user.id
        txt = m.text.strip()

        if uid == ADMIN_ID and uid in admin_state:
            st = admin_state.pop(uid)
            if st == 'cmirror':
                tkn = txt.strip()
                if ':' in tkn and len(tkn) > 30:
                    try:
                        test = Bot(token=tkn)
                        me = await test.get_me()
                        add_mirror(tkn, me.username, uid)
                        await m.answer(T_MIRROR_CREATED.format(token=tkn), parse_mode="HTML")
                        await test.session.close()
                    except Exception as e:
                        await m.answer(f"❌ {e}")
                else:
                    await m.answer("❌ Неверный токен")
                return

    logger.info("Gateway bot starting...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

# =====================================================
# MIRROR BOT (поиск)
# =====================================================
async def run_mirror(token: str):
    bot = Bot(token=token)
    dp = Dispatcher()
    search_locks = {}

    @dp.message(Command("start"))
    async def cmd_start(m: Message):
        uid = m.from_user.id
        add_user(uid)
        logger.info(f"mirror /start {uid} @{m.from_user.username}")
        await m.answer(T_MAIN_MENU, parse_mode="HTML", reply_markup=kb_main_menu())

    @dp.callback_query(F.data == "back")
    async def cb_back(cb: CallbackQuery):
        await cb.message.edit_text(T_MAIN_MENU, parse_mode="HTML", reply_markup=kb_main_menu())
        await cb.answer()

    @dp.callback_query(F.data == "examples")
    async def cb_examples(cb: CallbackQuery):
        await cb.message.edit_text(T_EXAMPLES, parse_mode="HTML", reply_markup=kb_search_examples())
        await cb.answer()

    @dp.callback_query(F.data == "search")
    async def cb_search(cb: CallbackQuery):
        await cb.message.edit_text(T_SEARCH_PROMPT, parse_mode="HTML", reply_markup=kb_back_main())
        await cb.answer()

    @dp.message(F.text)
    async def on_text(m: Message):
        uid = m.from_user.id
        txt = m.text.strip()

        if txt.startswith('/'):
            return

        if search_locks.get(uid):
            await m.answer(T_SEARCH_LOCK)
            return

        mode = None
        query = txt
        if is_phone(txt):
            mode = 'phone'
        elif is_uname(txt):
            mode = 'username'
            if not query.startswith('@'):
                query = '@' + query
        elif is_id(txt):
            mode = 'id'
        elif re.match(r'^[А-ЯЁа-яё]+\s+[А-ЯЁа-яё]+', txt):
            mode = 'fio'
        elif '@' in txt and '.' in txt:
            mode = 'email'
        elif re.match(r'^[А-ЯЁа-яё]\d{3}[А-ЯЁа-яё]{2}\d{3}$', txt, re.I):
            mode = 'auto'
        else:
            await m.answer(T_NOFMT)
            return

        search_locks[uid] = True
        msg = await m.answer(T_SEARCH)
        logger.info(f"Search: {uid} {mode} {query}")

        try:
            result, elapsed = await do_search(mode, query)
            txt_r = fmt_result(result, query)
            txt_r += f'\n<i>{elapsed:.1f}s</i>'
            kb = get_btns(result.get('phone'))
            await msg.edit_text(txt_r, reply_markup=kb) if kb else await msg.edit_text(txt_r)
            logger.info(f"Done: {uid} {elapsed:.2f}s")
        except Exception as e:
            logger.error(f"Err {uid}: {e}", exc_info=True)
            await msg.edit_text(T_ERR)

        search_locks[uid] = False

    logger.info(f"Mirror bot starting... {token[:15]}...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

# =====================================================
# ENTRY
# =====================================================
async def main():
    init_db()
    if len(sys.argv) > 1:
        await run_mirror(sys.argv[1])
    else:
        await run_gateway()

if __name__ == '__main__':
    asyncio.run(main())
