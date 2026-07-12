import asyncio
import logging
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime
from logging.handlers import RotatingFileHandler

from telethon import TelegramClient
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton
)

# =====================================================
# CONFIG (НАСТРОЙКА)
# =====================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_FILE = os.path.join(BASE_DIR, 'accounts.json')

GATEWAY_TOKEN = 20:AAFMJY_4WYgW06AHw7hRYHYQYRJXdhTmtkY'
SPONSOR_CHANNEL = '@bothkm'

BOT_SJ = 'sjgdfj0ghjdhjjegtjjebot'
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
# LOGGING (ЛОГИРОВАНИЕ)
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
# REGEX (РЕГУЛЯРНЫЕ ВЫРАЖЕНИЯ ИЗ ОРИГИНАЛА)
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
ds_rl = RL(2, 1.0)

# =====================================================
# ЗАГРУЗКА АККАУНТОВ ИЗ ACCOUNTS.JSON
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
        logger.error(f"Ошибка загрузки accounts.json: {e}")
        return _acc_cache or []

def accs_by_role(role):
    return [a for a in load_accs() if a.get('role') == role and a.get('status') == 'active']

def mk_client(a):
    return TelegramClient(os.path.join(BASE_DIR, a['session']), a['api_id'], a['api_hash'])

# =====================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
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
    return t.startswith('@') or (t.isalnum() and len(t) >= 5 and not t.isdigit())

def _esc(t):
    return str(t).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def _t(i, tot, lim):
    return '└' if i == min(tot, lim) - 1 else '├'

# =====================================================
# DEEPSEARCH API
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
        logger.warning(f"Ошибка DeepSearch: {e}")
        return None

# =====================================================
# ПОЛНЫЙ ПАРСИНГ ОТВЕТА ОТ СЛИТОГО БОТА (SJ)
# =====================================================
def parse_sj(text):
    c = clean_md(text)
    d = {'phone': None, 'operator': None, 'region': None, 'country': None,
         'telegram': [], 'books': [], 'vk': [], 'ok': []}
    
    d['phone'] = _rx('phone', c)
    d['operator'] = _rx('operator', c)
    d['region'] = _rx('region', c)
    d['country'] = _rx('country', c)
    
    # Парсим Telegram аккаунты связанных лиц/сессий
    for u, uid in RE['tg'].findall(c):
        d['telegram'].append({'username': u, 'id': uid})
        
    # Парсим телефонные книги (имена)
    pb = RE['pb'].search(c)
    if pb:
        raw = re.sub(r'\s+', ' ', pb.group(1)).strip()
        d['books'] = [n.strip() for n in raw.split(',') if n.strip() and len(n.strip()) > 1]
        
    # Парсим ссылки VK
    for n, u in RE['vk'].findall(c):
        d['vk'].append({'name': re.sub(r'\s*\d{2}\.\d{2}\.\d{4}', '', n).strip(), 'url': u.strip()})
        
    # Парсим ссылки OK
    for n, u in RE['ok'].findall(c):
        d['ok'].append({'name': n.strip(), 'url': u.strip()})
        
    return d

# =====================================================
# ВЗАИМОДЕЙСТВИЕ С BOT_SJ ЧЕРЕЗ TELETHON
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
    # Сбрасываем старые зависшие инпуты в SJ
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
    
    # Выбираем режим на основе типа данных
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
    
    # Отправляем сам запрос
    await c.send_message(ent, query)
    r = await _poll_res(c, ent, to=30)
    return r.text if r else None

async def try_sj(query, mode):
    for a in accs_by_role('sj'):
        try:
            await sj_rl.get()
            c = mk_client(a); await c.start()
            r = await _sj_search(c, await c.get_input_entity(BOT_SJ), query, mode)
            await c.disconnect()
            if r: return r
        except Exception as e:
            logger.warning(f"Ошибка на аккаунте {a['name']}: {e}")
            try: await c.disconnect()
            except: pass
    return None

# =====================================================
# ОБРАБОТКА ПОИСКА И СЛИЯНИЕ ДАННЫХ
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
    sj = ds = None

    if mode == 'phone':
        sj = await try_sj(query, 'phone')
        ds = await asyncio.get_event_loop().run_in_executor(None, deepsearch, query)
    elif mode == 'id':
        sj = await try_sj(query, 'id')
    elif mode == 'username':
        sj = await try_sj(query, 'username')
    elif mode in ('fio', 'email', 'auto'):
        ds = await asyncio.get_event_loop().run_in_executor(None, deepsearch, query)

    # Парсим сырой текстовый ответ от SJ в структурированный вид
    sp = cparse(parse_sj, sj) if sj else {}

    res = {
        'display': query,
        'id': query,
        'usernames': [query.lstrip('@')] if mode == 'username' else [],
        'phone': sp.get('phone'), 'operator': sp.get('operator'),
        'region': sp.get('region'), 'country': sp.get('country'),
        'phonebook': sp.get('books', []),
        'vk': sp.get('vk', []), 'ok': sp.get('ok', []),
        'telegram': sp.get('telegram'),
        'emails': [], 'fios': []
    }

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
# КРАСИВОЕ ОФОРМЛЕНИЕ РЕЗУЛЬТАТОВ ДЛЯ ЮЗЕРА
# =====================================================
def fmt_result(d, q):
    r = d
    if not r or (isinstance(r, dict) and not r.get('id') and not r.get('phone') and not r.get('phonebook') and not r.get('fios')):
        return '<b>Ничего не найдено</b>'
        
    L = [f'<b>🔭 Результат на {r.get("display", q)}</b>', '',
         f'👨‍💻 Запрос: <code>{r.get("id", q)}</code>']
    L.append('')
    
    if r.get('phone'):
        L.append(f'📱 Телефон: <code>{r["phone"]}</code>')
        if r.get('operator'): L.append(f'├ Оператор: <i>{_esc(r["operator"])}</i>')
        if r.get('region'): L.append(f'├ Регион: <i>{_esc(r["region"])}</i>')
        if r.get('country'): L.append(f'└ Страна: <i>{_esc(r["country"])}</i>')
        L.append('')
        
    if r.get('phonebook'):
        n = len(r['phonebook'])
        L.append(f'<b>💾 Телефонная книга ({n}):</b>')
        for i, nm in enumerate(r['phonebook'][:15]): 
            L.append(f'{_t(i,n,15)} <code>{_esc(nm)}</code>')
        L.append('')
        
    if r.get('vk'):
        n = len(r['vk'])
        L.append(f'<b>🌐 ВКонтакте ({n}):</b>')
        for i, p in enumerate(r['vk'][:5]): 
            L.append(f'{_t(i,n,5)} <a href="{p.get("url","")}">{_esc(p["name"])}</a>')
        L.append('')
        
    if r.get('ok'):
        n = len(r['ok'])
        L.append(f'<b>🌐 Одноклассники ({n}):</b>')
        for i, p in enumerate(r['ok'][:5]): 
            L.append(f'{_t(i,n,5)} <a href="{p.get("url","")}">{_esc(p["name"])}</a>')
        L.append('')
        
    tg = r.get('telegram', [])
    if tg:
        if isinstance(tg, list) and tg:
            n = len(tg)
            L.append(f'<b>✈ Telegram ({n}):</b>')
            for i, t in enumerate(tg[:5]):
                if isinstance(t, dict): 
                    L.append(f'{_t(i,n,5)} <code>{t["username"]} (ID: {t["id"]})</code>')
                else: 
                    L.append(f'{_t(i,n,5)} <code>{t}</code>')
        elif isinstance(tg, str): 
            L.append(f'✈ Telegram: <code>{tg}</code>')
        L.append('')
        
    if r.get('emails'):
        n = len(r['emails'])
        L.append(f'<b>📧 E-mail ({n}):</b>')
        for i, e in enumerate(r['emails'][:5]): 
            L.append(f'{_t(i,n,5)} <code>{e}</code>')
        L.append('')
        
    if r.get('fios'): 
        L.append(f'👤 ФИО: <b><code>{_esc(r["fios"][0])}</code></b>')
        L.append('')
        
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
# ИНТЕРФЕЙС ГЛАВНОГО БОТА (КНОПКИ И ТЕКСТЫ)
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

T_MAIN_MENU = "🏠 <b>Вы находитесь в главном меню</b>\n\n> By Umbrella Search"
T_EXAMPLES = """ℹ️ <b>Основные примеры использования сервиса</b>

👤 Для ФИО: Фамилия Имя Отчество
📱 Для контактных данных: Номер в международном формате
📧 Для электронной почты: Полный любой электронный адрес
👁‍🗨 Для телеграм профиля: @логин или tgАЙДИ"""

T_SEARCH_PROMPT = """🔍 <b>Отправьте запрос для поиска</b>

📞 <code>79991099999</code> — номер телефона
📧 <code>@username</code> — Telegram
🆔 <code>1234567890</code> — Telegram ID
👤 <code>ФИО</code> — фамилия имя отчество
📧 <code>email@mail.ru</code> — электронная почта"""

T_SEARCH_LOCK = '<b>🔍 Поиск выполняется</b>\n\nДождитесь завершения текущего поиска.'
T_NOFMT = '<b>❌ Не удалось распознать формат</b>\n\nОтправьте номер, @username, Telegram ID, ФИО или email.'
T_ERR = '<b>❌ Ошибка при поиске</b>\n\nПопробуйте позже.'
T_SEARCH = '<b>🔍 Поиск по базам SJ...</b>\n\nПожалуйста, подождите.'

# =====================================================
# ЗАПУСК AIOGRAM
# =====================================================
async def main():
    bot = Bot(token=GATEWAY_TOKEN)
    dp = Dispatcher()
    search_locks = {}

    @dp.message(Command("start"))
    async def cmd_start(m: Message):
        logger.info(f"start {m.from_user.id} @{m.from_user.username}")
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
        
        # Определяем формат ввода
        if is_phone(txt):
            mode = 'phone'
        elif is_id(txt):
            mode = 'id'
        elif is_uname(txt):
            mode = 'username'
            if not query.startswith('@') and not query.isdigit():
                query = '@' + query
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
        logger.info(f"Запрос от {uid}: режим={mode}, запрос={query}")

        try:
            result, elapsed = await do_search(mode, query)
            txt_r = fmt_result(result, query)
            txt_r += f'\n<i>Время выполнения: {elapsed:.1f}s</i>'
            
            kb = get_btns(result.get('phone'))
            await msg.edit_text(txt_r, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True) if kb else await msg.edit_text(txt_r, parse_mode="HTML", disable_web_page_preview=True)
            logger.info(f"Успешно обработано для {uid} за {elapsed:.2f}s")
        except Exception as e:
            logger.error(f"Ошибка обработки у юзера {uid}: {e}", exc_info=True)
            await msg.edit_text(T_ERR)

        search_locks[uid] = False

    logger.info("Бот запускается...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
