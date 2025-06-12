#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sqlite3
import telebot
import requests
import asyncio
from datetime import datetime, timedelta

# --- Конфигурация ---
BOT_TOKEN     = os.environ.get("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0"))
PRICE_RUB     = 199
DB_PATH       = "vpn_bot.db"

bot = telebot.TeleBot(BOT_TOKEN)

# --- Инициализация БД ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT UNIQUE,
            subscription TEXT,
            access_url TEXT,
            server TEXT,
            balance INTEGER DEFAULT 0,
            referral_code TEXT,
            referred_by TEXT,
            reminder_sent BOOLEAN DEFAULT 0,
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
            access_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            location TEXT UNIQUE,
            api_url TEXT
        );
    ''')
    conn.commit()
    conn.close()

init_db()

# --- Вспомогательные функции ---
def create_outline_key(api_url):
    try:
        r = requests.post(f"{api_url}/access-keys", verify=False, timeout=10)
        r.raise_for_status()
        return r.json().get("accessUrl", "")
    except Exception as e:
        print("Ошибка создания ключа:", e)
        return None

# --- Хендлеры ---
@bot.message_handler(commands=["start", "help"])
def cmd_start(message):
    bot.send_message(
        message.chat.id,
        "👋 Добро пожаловать!\n"
        "Используйте /buy для покупки VPN.\n"
        "Ваш кабинет: /myvpn\n"
        "Баланс: /balance\n"
        "Вывод: /withdraw\n"
        "Рефералка: /referral\n"
        "Все ключи: /keys"
    )

@bot.message_handler(commands=["buy"])
def cmd_buy(message):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT location FROM servers")
    rows = c.fetchall()
    conn.close()
    if not rows:
        bot.send_message(message.chat.id, "❗️ Сервера недоступны.")
        return
    markup = telebot.types.InlineKeyboardMarkup()
    for (loc,) in rows:
        markup.add(telebot.types.InlineKeyboardButton(loc, callback_data=f"region_{loc}"))
    bot.send_message(message.chat.id, "Выберите регион:", reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("region_"))
def choose_region(call):
    loc = call.data.split("_",1)[1]
    chat_id = call.message.chat.id
    text = (
        f"Вы выбрали регион: {loc}\n"
        f"💰 Цена: {PRICE_RUB}₽\n"
        "Нажмите оплатить и затем /confirm"
    )
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton("💳 Оплатить", url="https://yoomoney.ru"))
    bot.send_message(chat_id, text, reply_markup=markup)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO payments (chat_id, plan, amount, server) VALUES (?,?,?,?)",
        (str(chat_id), "Месяц", PRICE_RUB, loc)
    )
    conn.commit()
    conn.close()

@bot.message_handler(commands=["confirm"])
def cmd_confirm(message):
    chat_id = str(message.chat.id)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT id, server FROM payments WHERE chat_id=? AND paid=0 ORDER BY created_at DESC LIMIT 1",
        (chat_id,)
    )
    row = c.fetchone()
    if not row:
        bot.send_message(message.chat.id, "Нет неоплаченных заявок.")
        conn.close()
        return
    pay_id, loc = row
    c.execute("SELECT api_url FROM servers WHERE location=?", (loc,))
    srv = c.fetchone()
    if not srv:
        bot.send_message(message.chat.id, "Сервер не найден.")
        conn.close()
        return
    api_url = srv[0]
    key = create_outline_key(api_url)
    if not key:
        bot.send_message(message.chat.id, "Ошибка создания ключа.")
        conn.close()
        return
    sub_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    # сохраняем в users
    c.execute(
        "INSERT OR REPLACE INTO users (chat_id, subscription, access_url, server, reminder_sent) VALUES (?,?,?,?,0)",
        (chat_id, sub_date, key, loc)
    )
    # обновляем payment
    c.execute("UPDATE payments SET paid=1, access_url=? WHERE id=?", (key, pay_id))
    # рефералка
    c.execute("SELECT referred_by FROM users WHERE chat_id=?", (chat_id,))
    ref = c.fetchone()[0]
    if ref:
        c.execute("UPDATE users SET balance = balance + ? WHERE chat_id=?", (int(PRICE_RUB*0.2), ref))
    conn.commit()
    conn.close()
    bot.send_message(message.chat.id, f"✅ Оплачено!\n🔑 {key}", parse_mode="Markdown")
    bot.send_message(ADMIN_CHAT_ID, f"Новый клиент {chat_id}, регион {loc}")

@bot.message_handler(commands=["myvpn"])
def cmd_myvpn(message):
    chat_id = str(message.chat.id)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT subscription, access_url, server FROM users WHERE chat_id=?",
        (chat_id,)
    )
    row = c.fetchone()
    conn.close()
    if not row:
        bot.send_message(message.chat.id, "У вас нет подписки.")
        return
    sub, key, loc = row
    bot.send_message(
        message.chat.id,
        f"🌍 {loc}\n🔑 `{key}`\n⏳ до {sub}",
        parse_mode="Markdown"
    )

@bot.message_handler(commands=["keys"])
def cmd_keys(message):
    chat_id = str(message.chat.id)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT access_url, server, created_at FROM payments WHERE chat_id=? AND paid=1 ORDER BY created_at DESC",
        (chat_id,)
    )
    all_keys = c.fetchall()
    conn.close()
    if not all_keys:
        bot.send_message(message.chat.id, "Ключей нет.")
        return
    for key, loc, dt in all_keys:
        bot.send_message(
            message.chat.id,
            f"🌍 {loc} | 📅 {dt[:10]}\n🔑 `{key}`",
            parse_mode="Markdown"
        )

@bot.message_handler(commands=["balance"])
def cmd_balance(message):
    chat_id = str(message.chat.id)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE chat_id=?", (chat_id,))
    bal = c.fetchone()
    conn.close()
    bot.send_message(
        message.chat.id,
        f"💰 Баланс: {bal[0] if bal else 0}₽"
    )

@bot.message_handler(commands=["withdraw"])
def cmd_withdraw(message):
    bot.send_message(message.chat.id, "Чтобы вывести, напишите администратору.")

@bot.message_handler(commands=["referral"])
def cmd_referral(message):
    chat_id = str(message.chat.id)
    link = f"https://t.me/{bot.get_me().username}?start={chat_id}"
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET referral_code=? WHERE chat_id=?", (chat_id, chat_id))
    conn.commit()
    conn.close()
    bot.send_message(
        message.chat.id,
        f"Реферальная ссылка:\n{link}\n20% бонус за каждого приглашённого!"
    )

async def subscription_checker():
    while True:
        now = datetime.now()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT chat_id, subscription, reminder_sent FROM users WHERE subscription IS NOT NULL")
        for chat_id, sub, rem in c.fetchall():
            try:
                dt = datetime.strptime(sub, "%Y-%m-%d")
                if dt - now <= timedelta(days=2) and not rem:
                    bot.send_message(chat_id, f"⏳ Подписка истекает {sub}")
                    c.execute("UPDATE users SET reminder_sent=1 WHERE chat_id=?", (chat_id,))
                if dt < now:
                    bot.send_message(chat_id, "❌ Подписка истекла.")
                    c.execute("DELETE FROM users WHERE chat_id=?", (chat_id,))
            except:
                pass
        conn.commit()
        conn.close()
        await asyncio.sleep(86400)

async def main():
    asyncio.create_task(subscription_checker())
    print("Бот запущен")
    await bot.polling(none_stop=True)

if __name__ == "__main__":
    asyncio.run(main())
