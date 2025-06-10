#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import sqlite3
import telebot
import requests
import asyncio
import logging
from datetime import datetime, timedelta

# --- Настройка логирования ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)

# --- Конфигурация ---
BOT_TOKEN     = os.environ.get("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0"))
PRICE_RUB     = 199
DB_PATH       = "vpn_bot.db"

bot = telebot.TeleBot(BOT_TOKEN)

# --- Инициализация и миграция базы ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Таблицы
    c.execute('''
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT UNIQUE,
    subscription TEXT,
    access_url TEXT,
    server TEXT,
    reminder_sent BOOLEAN DEFAULT 0,
    referrer_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
''')
    c.execute('''
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT,
    plan TEXT,
    amount INTEGER,
    paid BOOLEAN DEFAULT 0,
    server TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
''')
    # Миграция: добавляем access_url в payments, если нет
    c.execute("PRAGMA table_info(payments)")
    cols = [row[1] for row in c.fetchall()]
    if "access_url" not in cols:
        c.execute("ALTER TABLE payments ADD COLUMN access_url TEXT")
        logging.info("Миграция: добавлена колонка access_url в payments")
    c.execute('''
CREATE TABLE IF NOT EXISTS servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location TEXT UNIQUE,
    api_url TEXT
);
''')
    c.execute('''
CREATE TABLE IF NOT EXISTS commissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_id TEXT,
    referee_id TEXT,
    amount INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
''')
    c.execute('''
CREATE TABLE IF NOT EXISTS withdrawals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT,
    amount INTEGER,
    method TEXT,
    account TEXT,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
''')
    conn.commit()
    conn.close()
    logging.info("✅ Инициализация БД завершена")

init_db()

# --- Вспомогательные функции ---

def create_outline_key(api_url):
    try:
        r = requests.post(f"{api_url}/access-keys", timeout=10, verify=False)
        r.raise_for_status()
        data = r.json()
        logging.info("✅ Ключ создан: %s", data)
        return data.get("accessUrl", "")
    except Exception as e:
        logging.error("Ошибка создания ключа: %s", e, exc_info=True)
        return None

def add_user_if_not_exists(chat_id, referrer_id=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT 1 FROM users WHERE chat_id=?", (chat_id,))
    if not c.fetchone():
        c.execute("INSERT INTO users (chat_id, referrer_id) VALUES (?, ?)", (chat_id, referrer_id))
        conn.commit()
        logging.info("Добавлен пользователь %s, ref=%s", chat_id, referrer_id)
    conn.close()

def get_balance(chat_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COALESCE(SUM(amount),0) FROM commissions WHERE referrer_id=?", (chat_id,))
    total_comm = c.fetchone()[0]
    c.execute("SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE chat_id=? AND status='paid'", (chat_id,))
    total_with = c.fetchone()[0]
    conn.close()
    return total_comm, total_with

# --- Хендлеры ---

@bot.message_handler(commands=['start'])
def handle_start(message):
    chat_id = str(message.chat.id)
    args = message.text.split()
    ref_id = None
    if len(args) > 1 and args[1].startswith('ref'):
        ref = args[1][3:]
        if ref != chat_id:
            ref_id = ref
    add_user_if_not_exists(chat_id, referrer_id=ref_id)
    bot.send_message(chat_id,
        "👋 Добро пожаловать!
"
        "Используйте /buy для покупки VPN.
"
        "Ваш личный кабинет: /myvpn
"
        "Ваша реферальная ссылка: /referral")
    logging.info("Старт для %s, ref=%s", chat_id, ref_id)

@bot.message_handler(commands=['buy'])
def handle_buy(message):
    chat_id = message.chat.id
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT location FROM servers"); rows = c.fetchall(); conn.close()
    if not rows:
        bot.send_message(chat_id, "⚠️ Нет серверов.")
        return
    markup = telebot.types.InlineKeyboardMarkup()
    for (loc,) in rows:
        markup.add(telebot.types.InlineKeyboardButton(loc, callback_data=f"region_{loc}"))
    bot.send_message(chat_id, f"📍 Выберите регион (цена {PRICE_RUB}₽):", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('region_'))
def handle_region(call):
    loc = call.data.split('_',1)[1]; chat_id = call.message.chat.id
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("INSERT INTO payments (chat_id, plan, amount, server) VALUES (?,?,?,?)",
              (str(chat_id),'Месяц',PRICE_RUB,loc))
    conn.commit(); conn.close()
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton('💳 Оплатить', url='https://yoomoney.ru'))
    bot.send_message(chat_id, f"Вы выбрали **{loc}**
💰 {PRICE_RUB}₽", parse_mode='Markdown', reply_markup=markup)
    logging.info("Заявка создана %s -> %s", chat_id, loc)

@bot.message_handler(commands=['confirm'])
def confirm_payment(message):
    chat_id = str(message.chat.id)
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT id, server FROM payments WHERE chat_id=? AND paid=0 ORDER BY created_at DESC LIMIT 1", (chat_id,))
    row = c.fetchone()
    if not row: bot.send_message(chat_id,"❗ Нет заявок."); conn.close(); return
    pay_id, loc = row
    c.execute("SELECT api_url FROM servers WHERE location=?", (loc,)); srv = c.fetchone()
    if not srv: bot.send_message(chat_id,"❗ Сервер не найден"); conn.close(); return
    key = create_outline_key(srv[0]); 
    if not key: bot.send_message(chat_id,"❌ Ошибка"); conn.close(); return
    sub = (datetime.now()+timedelta(days=30)).strftime('%Y-%m-%d')
    c.execute("INSERT OR REPLACE INTO users(chat_id,subscription,access_url,server,reminder_sent) VALUES(?,?,?,?,0)",
              (chat_id,sub,key,loc))
    c.execute("UPDATE payments SET paid=1,access_url=? WHERE id=?", (key,pay_id))
    c.execute("SELECT referrer_id FROM users WHERE chat_id=?", (chat_id,))
    ref = c.fetchone()
    if ref and ref[0]:
        comm=int(PRICE_RUB*0.2)
        c.execute("INSERT INTO commissions(referrer_id,referee_id,amount) VALUES(?,?,?)",(ref[0],chat_id,comm))
        bot.send_message(int(ref[0]),f"🎉 Комиссия: {comm}₽")
    conn.commit(); conn.close()
    bot.send_message(chat_id,f"✅ Ключ:
`{key}`",parse_mode='Markdown')
    bot.send_message(ADMIN_CHAT_ID,f"👤{chat_id} => {loc}")
    logging.info("Подтверждена оплата %s", chat_id)

@bot.message_handler(commands=['referral'])
def send_referral(message):
    chat_id=message.chat.id
    bot.send_message(chat_id,f"🔗 https://t.me/{bot.get_me().username}?start=ref{chat_id}")

@bot.message_handler(commands=['myvpn'])
def handle_myvpn(msg):
    cid=str(msg.chat.id)
    conn=sqlite3.connect(DB_PATH);c=conn.cursor()
    c.execute("SELECT subscription,access_url,server FROM users WHERE chat_id=?", (cid,))
    row=c.fetchone();conn.close()
    if not row:bot.send_message(cid,"ℹ️ Нет подписки");return
    sub,url,srv=row
    bot.send_message(cid,f"🌍{srv}
🔗`{url}`
⏳{sub}",parse_mode='Markdown')

@bot.message_handler(commands=['balance'])
def handle_balance(msg):
    cid=str(msg.chat.id)
    tc,tw=get_balance(cid)
    bot.send_message(cid,f"💰Всего: {tc}₽
💸Выведено: {tw}₽
🟢Доступно: {tc-tw}₽")

@bot.message_handler(commands=['withdraw'])
def handle_withdraw(msg):
    parts=msg.text.split(maxsplit=2)
    if len(parts)<3:bot.send_message(msg.chat.id,"Исп: /withdraw <сумма> <счет>");return
    cid=str(msg.chat.id)
    try:amt=int(parts[1])
    except:bot.send_message(cid,"Сумма должна быть числом");return
    acct=parts[2]
    tc,tw=get_balance(cid)
    if amt>tc-tw:bot.send_message(cid,f"Недостаточно: {tc-tw}₽");return
    conn=sqlite3.connect(DB_PATH);c=conn.cursor()
    c.execute("INSERT INTO withdrawals(chat_id,amount,method,account) VALUES(?,?,?,?)",(cid,amt,"manual",acct))
    conn.commit();conn.close();bot.send_message(cid,f"✅Заявка на вывод {amt}₽ принята")
    logging.info("Withdraw %s: %s",cid,amt)

@bot.message_handler(commands=['keys'])
def list_keys(msg):
    cid=str(msg.chat.id)
    conn=sqlite3.connect(DB_PATH);c=conn.cursor()
    c.execute("SELECT access_url,server,created_at FROM payments WHERE chat_id=? AND paid=1 ORDER BY created_at DESC",(cid,))
    rows=c.fetchall();conn.close()
    if not rows:bot.send_message(cid,"📭Нет ключей");return
    txt="🔑Ваши ключи:
"
    for url,srv,dt in rows:txt+=f"•{srv}({dt.split()[0]}):`{url}`
"
    bot.send_message(cid,txt,parse_mode='Markdown')

async def subscription_checker():
    while True:
        now=datetime.now()
        conn=sqlite3.connect(DB_PATH);c=conn.cursor()
        c.execute("SELECT chat_id,subscription,reminder_sent FROM users WHERE subscription IS NOT NULL")
        for cid,sub,sent in c.fetchall():
            dt=datetime.strptime(sub,"%Y-%m-%d")
            if (dt-now).days<=2 and not sent:
                bot.send_message(cid,f"⏳Подписка истекает {sub}")
                c.execute("UPDATE users SET reminder_sent=1 WHERE chat_id=?", (cid,))
        c.execute("SELECT chat_id,subscription,server FROM users")
        for cid,sub,srv in c.fetchall():
            if datetime.strptime(sub,"%Y-%m-%d")<now:
                bot.send_message(cid,"❌Подписка истекла")
                bot.send_message(ADMIN_CHAT_ID,f"⛔{cid}@{srv}")
                c.execute("DELETE FROM users WHERE chat_id=?", (cid,))
        conn.commit();conn.close()
        await asyncio.sleep(86400)

async def main():
    asyncio.create_task(subscription_checker())
    logging.info("🚀Запуск")
    await bot.polling(non_stop=True)

if __name__=="__main__":
    asyncio.run(main())
