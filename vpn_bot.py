# Сохраняю полный обновлённый код бота в файл для скачивания
full_code = """#!/usr/bin/env python3
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
    # Таблица пользователей
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
        )
    ''')
    # Таблица платежей
    c.execute('''
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT,
            plan TEXT,
            amount INTEGER,
            paid BOOLEAN DEFAULT 0,
            server TEXT,
            access_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Таблица серверов
    c.execute('''
        CREATE TABLE IF NOT EXISTS servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            location TEXT UNIQUE,
            api_url TEXT
        )
    ''')
    # Таблица комиссий
    c.execute('''
        CREATE TABLE IF NOT EXISTS commissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id TEXT,
            referee_id TEXT,
            amount INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Таблица заявок на вывод
    c.execute('''
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT,
            amount INTEGER,
            method TEXT,
            account TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()
    logging.info("✅ База данных и таблицы инициализированы")

init_db()

# --- Вспомогательные функции ---

def create_outline_key(api_url):
    """Создаёт ключ через Outline API, игнорируя самоподписанный SSL."""
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
        logging.info("Добавлен новый пользователь %s с реферером %s", chat_id, referrer_id)
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

# --- Обработчики команд ---

@bot.message_handler(commands=["start"])
def handle_start(message):
    chat_id = str(message.chat.id)
    args = message.text.split()
    ref_id = None
    if len(args) > 1 and args[1].startswith("ref"):
        ref = args[1][3:]
        if ref != chat_id:
            ref_id = ref
    add_user_if_not_exists(chat_id, referrer_id=ref_id)
    bot.send_message(chat_id,
        "👋 Добро пожаловать!\n"
        "Используйте /buy для покупки VPN.\n"
        "Ваш личный кабинет: /myvpn\n"
        "Ваша реферальная ссылка: /referral")
    logging.info("Обработан /start для %s, referrer=%s", chat_id, ref_id)

@bot.message_handler(commands=["referral"])
def send_referral(message):
    chat_id = message.chat.id
    link = f"https://t.me/{bot.get_me().username}?start=ref{chat_id}"
    bot.send_message(chat_id, f"🔗 Ваша реферальная ссылка:\n{link}")
    logging.info("Выдана реферальная ссылка для %s", chat_id)

@bot.message_handler(commands=["buy"])
def handle_buy(message):
    chat_id = message.chat.id
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT location FROM servers")
    rows = c.fetchall()
    conn.close()

    if not rows:
        bot.send_message(chat_id, "⚠️ Нет доступных серверов, попробуйте позже.")
        logging.warning("Нет серверов для /buy у %s", chat_id)
        return

    markup = telebot.types.InlineKeyboardMarkup()
    for (loc,) in rows:
        markup.add(telebot.types.InlineKeyboardButton(loc, callback_data=f"region_{loc}"))
    bot.send_message(chat_id, f"📍 Выберите регион VPN (цена {PRICE_RUB}₽):", reply_markup=markup)
    logging.info("Пользователь %s запросил /buy", chat_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("region_"))
def handle_region(call):
    loc = call.data.split("_",1)[1]
    chat_id = call.message.chat.id
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO payments (chat_id, plan, amount, server) VALUES (?, ?, ?, ?)",
              (str(chat_id), "Месяц", PRICE_RUB, loc))
    conn.commit()
    conn.close()
    logging.info("Создана заявка на оплату для %s, регион %s", chat_id, loc)

    pay_markup = telebot.types.InlineKeyboardMarkup()
    pay_markup.add(telebot.types.InlineKeyboardButton("💳 Оплатить", url="https://yoomoney.ru"))
    bot.send_message(chat_id,
        f"Вы выбрали **{loc}**\n💰 Цена: {PRICE_RUB}₽\nНажмите кнопку для оплаты:",
        parse_mode="Markdown", reply_markup=pay_markup)

@bot.message_handler(commands=["confirm"])
def confirm_payment(message):
    chat_id = str(message.chat.id)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, server FROM payments WHERE chat_id=? AND paid=0 ORDER BY created_at DESC LIMIT 1",
              (chat_id,))
    row = c.fetchone()
    if not row:
        bot.send_message(chat_id, "❗ Нет неоплаченных заявок.")
        logging.warning("/confirm: нет заявок для %s", chat_id)
        conn.close()
        return

    pay_id, loc = row
    c.execute("SELECT api_url FROM servers WHERE location=?", (loc,))
    srv = c.fetchone()
    if not srv:
        bot.send_message(chat_id, "❗ Сервер не найден.")
        logging.error("/confirm: сервер %s не найден для %s", loc, chat_id)
        conn.close()
        return
    api_url = srv[0]

    key = create_outline_key(api_url)
    if not key:
        bot.send_message(chat_id, "❌ Не удалось создать ключ. Попробуйте позже.")
        conn.close()
        return

    sub_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    c.execute("""INSERT OR REPLACE INTO users
                 (chat_id, subscription, access_url, server, reminder_sent)
                 VALUES (?, ?, ?, ?, 0)""",
              (chat_id, sub_date, key, loc))
    c.execute("UPDATE payments SET paid=1, access_url=? WHERE id=?", (key, pay_id))

    c.execute("SELECT referrer_id FROM users WHERE chat_id=?", (chat_id,))
    ref = c.fetchone()
    if ref and ref[0]:
        comm = int(PRICE_RUB * 0.2)
        c.execute("INSERT INTO commissions (referrer_id, referee_id, amount) VALUES (?, ?, ?)",
                  (ref[0], chat_id, comm))
        bot.send_message(int(ref[0]),
                         f"🎉 Ваш реферал {chat_id} оформил подписку! Ваша комиссия: {comm}₽")
        logging.info("Начислена комиссия %s для %s", comm, ref[0])

    conn.commit()
    conn.close()

    bot.send_message(chat_id,
                     f"✅ Оплата подтверждена!\n🔗 Ваш ключ:\n`{key}`",
                     parse_mode="Markdown")
    bot.send_message(ADMIN_CHAT_ID,
                     f"✅ Новый клиент {chat_id}, регион: {loc}")
    logging.info("Оплата подтверждена для %s", chat_id)

@bot.message_handler(commands=["myvpn"])
def handle_myvpn(message):
    chat_id = str(message.chat.id)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT subscription, access_url, server FROM users WHERE chat_id=?", (chat_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        bot.send_message(chat_id, "ℹ️ У вас нет активной подписки.")
        return
    sub, url, srv = row
    bot.send_message(chat_id,
                     f"🌍 Регион: {srv}\n🔗 Ссылка: `{url}`\n⏳ Действует до: {sub}",
                     parse_mode="Markdown")

@bot.message_handler(commands=["balance"])
def handle_balance(message):
    chat_id = str(message.chat.id)
    total_comm, total_with = get_balance(chat_id)
    avail = total_comm - total_with
    bot.send_message(chat_id,
                     f"💰 Всего заработано: {total_comm}₽\n"
                     f"💸 Выведено: {total_with}₽\n"
                     f"🟢 Доступно: {avail}₽")
    logging.info("Проверен баланс для %s: всего=%s, выведено=%s", chat_id, total_comm, total_with)

@bot.message_handler(commands=["withdraw"])
def handle_withdraw(message):
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        bot.send_message(message.chat.id, "Использование: /withdraw <сумма> <счет>")
        return
    chat_id = str(message.chat.id)
    try:
        amount = int(parts[1])
    except:
        bot.send_message(message.chat.id, "Сумма должна быть числом.")
        return
    account = parts[2]
    total_comm, total_with = get_balance(chat_id)
    avail = total_comm - total_with
    if amount > avail:
        bot.send_message(message.chat.id, f"Недостаточно средств. Доступно: {avail}₽")
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO withdrawals (chat_id, amount, method, account) VALUES (?, ?, ?, ?)",
              (chat_id, amount, "manual", account))
    conn.commit()
    conn.close()
    bot.send_message(message.chat.id,
                     f"✅ Заявка на вывод {amount}₽ принята.\nСчет: {account}")
    logging.info("Заявка на вывод %s от %s", amount, chat_id)

@bot.message_handler(commands=["keys"])
def list_keys(message):
    chat_id = str(message.chat.id)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT access_url, server, created_at
                 FROM payments
                 WHERE chat_id=? AND paid=1
                 ORDER BY created_at DESC""", (chat_id,))
    rows = c.fetchall()
    conn.close()
    if not rows:
        bot.send_message(chat_id, "📭 У вас ещё нет активных ключей.")
        return
    text = "🔑 *Ваши VPN-ключи:*\n\n"
    for url, srv, dt in rows:
        date = dt.split()[0]
        text += f"• {srv} ({date}): `{url}`\n"
    bot.send_message(chat_id, text, parse_mode="Markdown")
    logging.info("Выведены ключи для %s", chat_id)

async def subscription_checker():
    while True:
        now = datetime.now()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        # напоминания за 2 дня
        c.execute("SELECT chat_id, subscription, reminder_sent FROM users WHERE subscription IS NOT NULL")
        for cid, sub, sent in c.fetchall():
            try:
                dt = datetime.strptime(sub, "%Y-%m-%d")
                if (dt - now).days <= 2 and not sent:
                    bot.send_message(cid, f"⏳ Ваша подписка истекает {sub}. Продлите заранее.")
                    c.execute("UPDATE users SET reminder_sent=1 WHERE chat_id=?", (cid,))
            except:
                pass
        # удаление истекших
        c.execute("SELECT chat_id, subscription, server FROM users")
        for cid, sub, srv in c.fetchall():
            try:
                if datetime.strptime(sub, "%Y-%m-%d") < now:
                    bot.send_message(cid, "❌ Подписка истекла, доступ отключён.")
                    bot.send_message(ADMIN_CHAT_ID, f"⛔ Клиент {cid} отключён от {srv}.")
                    c.execute("DELETE FROM users WHERE chat_id=?", (cid,))
            except:
                pass
        conn.commit()
        conn.close()
        await asyncio.sleep(86400)

async def main():
    asyncio.create_task(subscription_checker())
    logging.info("🚀 Бот запущен...")
    await bot.polling(non_stop=True)

if __name__ == "__main__":
    asyncio.run(main())
"""
with open("/mnt/data/vpn_bot_full.py", "w") as f:
    f.write(full_code)

