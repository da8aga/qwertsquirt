#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sqlite3
import telebot
import requests
import asyncio
import threading
from datetime import datetime, timedelta

# --- Конфигурация ---
BOT_TOKEN     = os.environ.get("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0"))
PRICE_RUB     = 199
DB_PATH       = "vpn_bot.db"

bot = telebot.TeleBot(BOT_TOKEN)
# Снимаем любой ранее установленный webhook
bot.remove_webhook()

# --- Инициализация и миграция БД ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Таблица users
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id        TEXT UNIQUE,
            subscription   TEXT,
            access_url     TEXT,
            server         TEXT,
            referral_code  TEXT,
            referred_by    TEXT,
            balance        INTEGER DEFAULT 0,
            reminder_sent  BOOLEAN DEFAULT 0,
            created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    c.execute("PRAGMA table_info(users);")
    cols = [r[1] for r in c.fetchall()]
    if "balance" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN balance INTEGER DEFAULT 0;")
    if "referral_code" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN referral_code TEXT;")
    if "referred_by" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN referred_by TEXT;")
    if "reminder_sent" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN reminder_sent BOOLEAN DEFAULT 0;")

    # Таблица payments
    c.execute('''
        CREATE TABLE IF NOT EXISTS payments (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id    TEXT,
            plan       TEXT,
            amount     INTEGER,
            paid       BOOLEAN DEFAULT 0,
            server     TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    c.execute("PRAGMA table_info(payments);")
    pcols = [r[1] for r in c.fetchall()]
    if "access_url" not in pcols:
        c.execute("ALTER TABLE payments ADD COLUMN access_url TEXT;")

    # Таблица servers
    c.execute('''
        CREATE TABLE IF NOT EXISTS servers (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            location TEXT UNIQUE,
            api_url  TEXT
        );
    ''')

    conn.commit()
    conn.close()

init_db()

# --- Вспомогательная функция создания ключа Outline ---
def create_outline_key(api_url):
    url = f"{api_url}/access-keys"
    print(f"[DEBUG] POST {url}", flush=True)
    try:
        r = requests.post(url, verify=False, timeout=10)
        print(f"[DEBUG] HTTP {r.status_code}: {r.text}", flush=True)
        r.raise_for_status()
        data = r.json()
        print(f"[DEBUG] JSON: {data}", flush=True)
        access_url = data.get("accessUrl")
        if not access_url:
            print("[DEBUG] Поле accessUrl пусто или отсутствует", flush=True)
        return access_url
    except Exception as e:
        print(f"[ERROR] Ошибка создания ключа: {e}", flush=True)
        return None

# --- /start и /help ---
@bot.message_handler(commands=["start", "help"])
def cmd_start(message):
    text = (
        "👋 Добро пожаловать!\n"
        "/buy      — купить VPN\n"
        "/myvpn    — личный кабинет\n"
        "/keys     — список ключей\n"
        "/balance  — баланс реферальных бонусов\n"
        "/withdraw — запрос на вывод\n"
        "/referral — реферальная ссылка"
    )
    bot.send_message(message.chat.id, text)

# --- /buy ---
@bot.message_handler(commands=["buy"])
def cmd_buy(message):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.cursor().execute("SELECT location FROM servers").fetchall()
    conn.close()
    if not rows:
        bot.send_message(message.chat.id, "❗️ Сервера недоступны.")
        return
    markup = telebot.types.InlineKeyboardMarkup()
    for (loc,) in rows:
        markup.add(telebot.types.InlineKeyboardButton(loc, callback_data=f"region_{loc}"))
    bot.send_message(message.chat.id, "Выберите регион:", reply_markup=markup)

# --- выбор региона ---
@bot.callback_query_handler(func=lambda c: c.data.startswith("region_"))
def cmd_region(call):
    loc = call.data.split("_", 1)[1]
    chat_id = call.message.chat.id
    text = (
        f"Вы выбрали регион: {loc}\n"
        f"💰 Цена: {PRICE_RUB}₽\n"
        "Нажмите кнопку «Оплатить», затем /confirm"
    )
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton("💳 Оплатить", url="https://yoomoney.ru"))
    bot.send_message(chat_id, text, reply_markup=markup)

    conn = sqlite3.connect(DB_PATH)
    conn.cursor().execute(
        "INSERT INTO payments (chat_id, plan, amount, server) VALUES (?,?,?,?)",
        (str(chat_id), "Месяц", PRICE_RUB, loc)
    )
    conn.commit()
    conn.close()

# --- /confirm ---
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
        bot.send_message(message.chat.id, "Ошибка создания ключа. Смотрите логи.", parse_mode="Markdown")
        conn.close()
        return
    sub = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")

    c.execute(
        "INSERT OR REPLACE INTO users (chat_id, subscription, access_url, server, reminder_sent) VALUES (?,?,?,?,0)",
        (chat_id, sub, key, loc)
    )
    c.execute("UPDATE payments SET paid=1, access_url=? WHERE id=?", (key, pay_id))
    c.execute("SELECT referred_by FROM users WHERE chat_id=?", (chat_id,))
    ref = c.fetchone()
    if ref and ref[0]:
        bonus = int(PRICE_RUB * 0.2)
        c.execute("UPDATE users SET balance = balance + ? WHERE chat_id=?", (bonus, ref[0]))

    conn.commit()
    conn.close()

    bot.send_message(message.chat.id, f"✅ Оплата подтверждена!\n🔑 `{key}`", parse_mode="Markdown")
    bot.send_message(ADMIN_CHAT_ID, f"Новый клиент {chat_id}, регион {loc}")

# --- /myvpn ---
@bot.message_handler(commands=["myvpn"])
def cmd_myvpn(message):
    chat_id = str(message.chat.id)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT subscription, access_url, server FROM users WHERE chat_id=?", (chat_id,)
    )
    row = c.fetchone()
    conn.close()
    if not row:
        bot.send_message(message.chat.id, "У вас пока нет подписки.")
        return
    sub, key, loc = row
    key_display = key or "—"
    text = (
        f"🌍 Сервер: {loc}\n"
        f"🔑 Ключ: `{key_display}`\n"
        f"⏳ Действует до: {sub}"
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

# --- /keys ---
@bot.message_handler(commands=["keys"])
def cmd_keys(message):
    chat_id = str(message.chat.id)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT access_url, server, created_at FROM payments "
        "WHERE chat_id=? AND paid=1 ORDER BY created_at DESC",
        (chat_id,)
    )
    rows = c.fetchall()
    conn.close()
    if not rows:
        bot.send_message(message.chat.id, "У вас ещё нет оплаченных ключей.")
        return
    for access_url, server, created in rows:
        url_display = access_url or "—"
        date = created.split(" ")[0]
        text = (
            f"🌍 {server}\n"
            f"🔑 `{url_display}`\n"
            f"📅 {date}"
        )
        bot.send_message(message.chat.id, text, parse_mode="Markdown")

# --- /balance ---
@bot.message_handler(commands=["balance"])
def cmd_balance(message):
    chat_id = str(message.chat.id)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE chat_id=?", (chat_id,))
    bal = c.fetchone()
    conn.close()
    amount = bal[0] if bal else 0
    bot.send_message(message.chat.id, f"💰 Баланс: {amount}₽")

# --- /withdraw ---
@bot.message_handler(commands=["withdraw"])
def cmd_withdraw(message):
    bot.send_message(message.chat.id, "Чтобы вывести — напишите администратору.")

# --- /referral ---
@bot.message_handler(commands=["referral"])
def cmd_referral(message):
    chat_id = str(message.chat.id)
    link = f"https://t.me/{bot.get_me().username}?start={chat_id}"
    conn = sqlite3.connect(DB_PATH)
    conn.cursor().execute("UPDATE users SET referral_code=? WHERE chat_id=?", (chat_id, chat_id))
    conn.commit()
    conn.close()
    bot.send_message(
        message.chat.id,
        f"🎁 Реферальная ссылка:\n{link}\n20% бонус за каждого!"
    )

# --- Фоновая проверка подписок ---
async def subscription_checker():
    while True:
        now = datetime.now()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT chat_id, subscription, reminder_sent FROM users WHERE subscription IS NOT NULL")
        for chat_id, sub_date, rem_flag in c.fetchall():
            try:
                dt = datetime.strptime(sub_date, "%Y-%m-%d")
                if dt - now <= timedelta(days=2) and not rem_flag:
                    bot.send_message(chat_id, f"⏳ Подписка истекает {sub_date}")
                    c.execute("UPDATE users SET reminder_sent=1 WHERE chat_id=?", (chat_id,))
                if dt < now:
                    bot.send_message(chat_id, "❌ Подписка завершена.")
                    c.execute("DELETE FROM users WHERE chat_id=?", (chat_id,))
            except:
                pass
        conn.commit()
        conn.close()
        await asyncio.sleep(86400)

# --- Запуск бота ---
if __name__ == "__main__":
    # Снимаем webhook ещё раз
    bot.remove_webhook()
    # Запускаем subscription_checker в фоне
    threading.Thread(target=lambda: asyncio.run(subscription_checker()), daemon=True).start()
    print("Бот запущен", flush=True)
    # Запуск polling
    bot.polling(none_stop=True)
