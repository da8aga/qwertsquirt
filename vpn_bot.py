import os
import sqlite3
import telebot
import requests
import asyncio
from datetime import datetime, timedelta

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0"))
PRICE_RUB = 199
REFERRAL_REWARD = 40  # 20% от 199
DB_PATH = "vpn_bot.db"

bot = telebot.TeleBot(BOT_TOKEN)

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
            balance INTEGER DEFAULT 0,
            referral TEXT,
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

def create_outline_key(api_url):
    try:
        response = requests.post(f"{api_url}/access-keys", timeout=10, verify=False)
        response.raise_for_status()
        key_data = response.json()
        return key_data.get("accessUrl", "")
    except Exception as e:
        print("Ошибка создания ключа:", e)
        return None

@bot.message_handler(commands=["start"])
def handle_start(message):
    ref = message.text.split()[1] if len(message.text.split()) > 1 else None
    chat_id = str(message.chat.id)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT chat_id FROM users WHERE chat_id=?", (chat_id,))
    exists = c.fetchone()
    if not exists:
        c.execute("INSERT INTO users (chat_id, referral) VALUES (?, ?)", (chat_id, ref))
    conn.commit()
    conn.close()

    bot.send_message(message.chat.id, "👋 Добро пожаловать!
Используйте /buy для покупки VPN.
Ваш личный кабинет: /myvpn")

@bot.message_handler(commands=["buy"])
def handle_buy(message):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT location FROM servers")
    locations = c.fetchall()
    conn.close()

    if not locations:
        bot.send_message(message.chat.id, "Сервера временно недоступны. Попробуйте позже.")
        return

    markup = telebot.types.InlineKeyboardMarkup()
    for loc in locations:
        markup.add(telebot.types.InlineKeyboardButton(f"{loc[0]}", callback_data=f"region_{loc[0]}"))
    bot.send_message(message.chat.id, "Выберите регион сервера:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("region_"))
def handle_location_selection(call):
    location = call.data.split("_", 1)[1]
    chat_id = call.message.chat.id

    markup = telebot.types.InlineKeyboardMarkup()
    pay_url = "https://yoomoney.ru"
    markup.add(telebot.types.InlineKeyboardButton("💳 Оплатить", url=pay_url))

    message_text = (
        f"Вы выбрали регион: {location}
"
        f"💰 Цена: {PRICE_RUB}₽
"
        "Нажмите кнопку ниже для оплаты, затем используйте /confirm"
    )
    bot.send_message(chat_id, message_text, reply_markup=markup)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO payments (chat_id, plan, amount, server) VALUES (?, ?, ?, ?)",
              (str(chat_id), "Месяц", PRICE_RUB, location))
    conn.commit()
    conn.close()

@bot.message_handler(commands=["confirm"])
def confirm_payment(message):
    chat_id = str(message.chat.id)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, server FROM payments WHERE chat_id=? AND paid=0 ORDER BY created_at DESC LIMIT 1", (chat_id,))
    row = c.fetchone()

    if not row:
        bot.send_message(message.chat.id, "Нет неоплаченных заявок.")
        conn.close()
        return

    payment_id, location = row
    c.execute("SELECT api_url FROM servers WHERE location=?", (location,))
    server_row = c.fetchone()

    if not server_row:
        bot.send_message(message.chat.id, "Ошибка: не найден сервер для региона.")
        conn.close()
        return

    api_url = server_row[0]
    access_url = create_outline_key(api_url)
    if not access_url:
        bot.send_message(message.chat.id, "Ошибка создания ключа. Обратитесь в поддержку.")
        conn.close()
        return

    subscription_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    c.execute("INSERT OR REPLACE INTO users (chat_id, subscription, access_url, server, reminder_sent) VALUES (?, ?, ?, ?, 0)",
              (chat_id, subscription_date, access_url, location))
    c.execute("UPDATE payments SET paid=1, access_url=? WHERE id=?", (access_url, payment_id))
    conn.commit()

    # Реферальная награда
    c.execute("SELECT referral FROM users WHERE chat_id=?", (chat_id,))
    ref_row = c.fetchone()
    if ref_row and ref_row[0]:
        ref_id = ref_row[0]
        c.execute("UPDATE users SET balance = balance + ? WHERE chat_id=?", (REFERRAL_REWARD, ref_id))

    conn.commit()
    conn.close()

    bot.send_message(message.chat.id, f"✅ Оплата подтверждена!
🔗 Ваш ключ:
`{access_url}`", parse_mode="Markdown")
    bot.send_message(ADMIN_CHAT_ID, f"✅ Новый клиент {chat_id}, регион: {location}")

@bot.message_handler(commands=["myvpn"])
def handle_myvpn(message):
    chat_id = str(message.chat.id)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT subscription, access_url, server FROM users WHERE chat_id=?", (chat_id,))
    row = c.fetchone()
    conn.close()

    if not row:
        bot.send_message(message.chat.id, "У вас пока нет активной подписки.")
        return

    subscription, access_url, server = row
    text = (
        f"🌍 Сервер: {server}
"
        f"🔗 Ссылка: `{access_url}`
"
        f"⏳ Подписка до: {subscription}"
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(commands=["keys"])
def list_keys(message):
    chat_id = str(message.chat.id)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT access_url, server, created_at FROM payments WHERE chat_id=? AND paid=1 ORDER BY created_at DESC", (chat_id,))
    keys = c.fetchall()
    conn.close()

    if not keys:
        bot.send_message(message.chat.id, "У вас нет выданных ключей.")
        return

    msg = "🔑 Все ваши ключи:

"
    for url, server, created in keys:
        msg += f"🌍 {server} | 📅 {created.split()[0]}
`{url}`

"
    bot.send_message(message.chat.id, msg, parse_mode="Markdown")

@bot.message_handler(commands=["referral"])
def referral_link(message):
    chat_id = str(message.chat.id)
    link = f"https://t.me/{bot.get_me().username}?start={chat_id}"
    bot.send_message(chat_id, f"🔗 Ваша реферальная ссылка:
{link}")

@bot.message_handler(commands=["balance"])
def show_balance(message):
    chat_id = str(message.chat.id)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE chat_id=?", (chat_id,))
    row = c.fetchone()
    conn.close()

    balance = row[0] if row else 0
    bot.send_message(chat_id, f"💰 Ваш баланс: {balance}₽")

@bot.message_handler(commands=["withdraw"])
def withdraw_request(message):
    chat_id = str(message.chat.id)
    bot.send_message(chat_id, "💸 Для вывода напишите админу: @admin_username")

async def subscription_checker():
    while True:
        now = datetime.now()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        c.execute("SELECT chat_id, subscription, reminder_sent FROM users WHERE subscription IS NOT NULL")
        for chat_id, sub_date, reminder_sent in c.fetchall():
            try:
                sub_dt = datetime.strptime(sub_date, "%Y-%m-%d")
                if sub_dt - now <= timedelta(days=2) and not reminder_sent:
                    bot.send_message(chat_id, f"⏳ Ваша подписка истекает {sub_date}. Продлите её заранее.")
                    c.execute("UPDATE users SET reminder_sent=1 WHERE chat_id=?", (chat_id,))
            except:
                continue

        c.execute("SELECT chat_id, subscription FROM users")
        for chat_id, sub_date in c.fetchall():
            try:
                if datetime.strptime(sub_date, "%Y-%m-%d") < now:
                    bot.send_message(chat_id, "❌ Ваша подписка завершена. Доступ отключён.")
                    bot.send_message(ADMIN_CHAT_ID, f"⛔ Клиент {chat_id} удалён (срок истёк).")
                    c.execute("DELETE FROM users WHERE chat_id=?", (chat_id,))
            except:
                continue

        conn.commit()
        conn.close()
        await asyncio.sleep(86400)

async def main():
    asyncio.create_task(subscription_checker())
    print("Бот запущен...")
    await bot.polling(non_stop=True)

if __name__ == "__main__":
    asyncio.run(main())
