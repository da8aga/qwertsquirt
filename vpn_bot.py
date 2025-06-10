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

# --- Инициализация базы данных ---
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
        )
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
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            location TEXT UNIQUE,
            api_url TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS commissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id TEXT,
            referee_id TEXT,
            amount INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
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
        )
    ''')
    # Миграция: добавление колонки access_url при необходимости
    c.execute("PRAGMA table_info(payments)")
    cols = [row[1] for row in c.fetchall()]
    if "access_url" not in cols:
        c.execute("ALTER TABLE payments ADD COLUMN access_url TEXT")
        logging.info("Миграция: добавлена колонка access_url в payments")
    conn.commit()
    conn.close()
    logging.info("✅ База данных инициализирована")

init_db()

# --- Вспомогательные функции ---

def create_outline_key(api_url):
    # Создаёт ключ через Outline API, игнорируя self-signed SSL.
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
        logging.info("Добавлен пользователь %s, referrer=%s", chat_id, referrer_id)
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
        "👋 Добро пожаловать! Используйте /buy для покупки VPN.
"
        "Ваш личный кабинет: /myvpn
"
        "Ваша реферальная ссылка: /referral")
    logging.info("Обработан /start для %s, referrer=%s", chat_id, ref_id)

@bot.message_handler(commands=['referral'])
def send_referral(message):
    cid = message.chat.id
    link = f"https://t.me/{bot.get_me().username}?start=ref{cid}"
    bot.send_message(cid, f"🔗 Ваша реферальная ссылка:
{link}")
    logging.info("Реферальная ссылка для %s: %s", cid, link)

# ... (остальной код без изменений) ...

