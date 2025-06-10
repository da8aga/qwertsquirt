#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sqlite3
import telebot
import requests
import asyncio
from datetime import datetime, timedelta

# --- Конфигурация ---
BOT_TOKEN      = os.environ.get("BOT_TOKEN")
ADMIN_CHAT_ID  = int(os.environ.get("ADMIN_CHAT_ID", "0"))
PRICE_RUB      = 199
DB_PATH        = "vpn_bot.db"

bot = telebot.TeleBot(BOT_TOKEN)

# --- Инициализация базы ---
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
    conn.commit()
    conn.close()

init_db()

# --- Вспомогательные функции ---

def create_outline_key(api_url):
    """Создаёт ключ через Outline API, игнорируя self-signed SSL."""
    try:
        r = requests.post(f"{api_url}/access-keys", timeout=10, verify=False)
        r.raise_for_status()
        data = r.json()
        print("✅ Ключ создан:", data, flush=True)
        return data.get("accessUrl", "")
    except Exception as e:
        print("❌ Ошибка создания ключа:", e, flush=True)
        return None

def add_user_if_not_exists(chat_id, referrer_id=None):
    """Добавляет пользователя в users, если его там нет."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT 1 FROM users WHERE chat_id=?", (chat_id,))
    if not c.fetchone():
        c.execute("INSERT INTO users (chat_id, referrer_id) VALUES (?, ?)", (chat_id, referrer_id))
        conn.commit()
    conn.close()

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

@bot.message_handler(commands=["referral"])
def send_referral(message):
    chat_id = message.chat.id
    link = f"https://t.me/{bot.get_me().username}?start=ref{chat_id}"
    bot.send_message(chat_id, f"🔗 Ваша реферальная ссылка:\n{link}")

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
        return

    markup = telebot.types.InlineKeyboardMarkup()
    for (loc,) in rows:
        markup.add(telebot.types.InlineKeyboardButton(loc, callback_data=f"region_{loc}"))
    bot.send_message(chat_id, f"📍 Выберите регион VPN (цена {PRICE_RUB}₽):", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("region_"))
def handle_region(call):
    loc = call.data.split("_",1)[1]
    chat_id = call.message.chat.id

    # Сохраняем заявку на оплату
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO payments (chat_id, plan, amount, server) VALUES (?, ?, ?, ?)",
              (str(chat_id), "Месяц", PRICE_RUB, loc))
    conn.commit()
    conn.close()

    # Предлагаем оплату
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
    c.execute("SELECT id, server FROM payments WHERE chat_id=? AND paid=0 ORDER BY created_at DESC LIMIT 1", (chat_id,))
    row = c.fetchone()
    if not row:
        bot.send_message(chat_id, "❗ Нет неоплаченных заявок.")
        conn.close()
        return

    pay_id, loc = row
    c.execute("SELECT api_url FROM servers WHERE location=?", (loc,))
    srv = c.fetchone()
    if not srv:
        bot.send_message(chat_id, "❗ Сервер не найден.")
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
    c.execute("UPDATE payments SET paid=1 WHERE id=?", (pay_id,))

    # Начисляем комиссию рефереру
    c.execute("SELECT referrer_id FROM users WHERE chat_id=?", (chat_id,))
    ref = c.fetchone()
    if ref and ref[0]:
        comm = int(PRICE_RUB * 0.2)
        c.execute("INSERT INTO commissions (referrer_id, referee_id, amount) VALUES (?, ?, ?)",
                  (ref[0], chat_id, comm))
        bot.send_message(int(ref[0]),
                         f"🎉 Ваш реферал {chat_id} оформил подписку! Ваша комиссия: {comm}₽")

    conn.commit()
    conn.close()

    bot.send_message(chat_id,
                     f"✅ Оплата подтверждена!\n🔗 Ваш ключ:\n`{key}`",
                     parse_mode="Markdown")
    bot.send_message(ADMIN_CHAT_ID,
                     f"✅ Новый клиент {chat_id}, регион: {loc}")

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

async def subscription_checker():
    while True:
        now = datetime.now()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        # Напоминания за 2 дня
        c.execute("SELECT chat_id, subscription, reminder_sent FROM users WHERE subscription IS NOT NULL")
        for cid, sub, sent in c.fetchall():
            try:
                dt = datetime.strptime(sub, "%Y-%m-%d")
                if (dt - now).days <= 2 and not sent:
                    bot.send_message(cid, f"⏳ Ваша подписка истекает {sub}. Продлите её заранее.")
                    c.execute("UPDATE users SET reminder_sent=1 WHERE chat_id=?", (cid,))
            except:
                pass
        # Удаление просроченных
        c.execute("SELECT chat_id, subscription, server FROM users")
        for cid, sub, srv in c.fetchall():
            try:
                if datetime.strptime(sub, "%Y-%m-%d") < now:
                    bot.send_message(cid, "❌ Подписка истекла, доступ отключён.")
                    bot.send_message(ADMIN_CHAT_ID, f"⛔ Клиент {cid} отключён от {srv} (срок истёк).")
                    c.execute("DELETE FROM users WHERE chat_id=?", (cid,))
            except:
                pass
        conn.commit()
        conn.close()
        await asyncio.sleep(86400)

async def main():
    asyncio.create_task(subscription_checker())
    print("🚀 Бот запущен...")
    await bot.polling(non_stop=True)

if __name__ == "__main__":
    asyncio.run(main())
