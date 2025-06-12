import os
import sqlite3
import telebot
import requests
import asyncio
from datetime import datetime, timedelta

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0"))
PRICE_RUB = 199
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
            reminder_sent BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            balance INTEGER DEFAULT 0,
            referral_code TEXT,
            referred_by TEXT
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
        response = requests.post(f"{api_url}/access-keys", verify=False, timeout=10)
        response.raise_for_status()
        return response.json().get("accessUrl", "")
    except Exception as e:
        print("❌ Ошибка создания ключа:", e)
        return None

@bot.message_handler(commands=["start"])
def start_command(message):
    text = "👋 Добро пожаловать!\n\nИспользуйте /buy для покупки VPN.\nВаш личный кабинет — /myvpn\n💸 Баланс — /balance\n💰 Вывод — /withdraw\n🎁 Рефералка — /referral\n📄 Все ключи — /keys"
    bot.send_message(message.chat.id, text)

@bot.message_handler(commands=["buy"])
def handle_buy(message):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT location FROM servers")
    locations = c.fetchall()
    conn.close()

    if not locations:
        bot.send_message(message.chat.id, "❗️Сервера временно недоступны.")
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
    fake_url = "https://yoomoney.ru"
    markup.add(telebot.types.InlineKeyboardButton("💳 Оплатить", url=fake_url))

    text = (
        f"Вы выбрали регион: {location}\n"
        f"💰 Цена: {PRICE_RUB}₽\n"
        f"Нажмите кнопку ниже для оплаты:"
    )
    bot.send_message(chat_id, text, reply_markup=markup)

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
        bot.send_message(message.chat.id, "Нет неоплаченных заказов.")
        conn.close()
        return

    payment_id, location = row
    c.execute("SELECT api_url FROM servers WHERE location=?", (location,))
    server_row = c.fetchone()

    if not server_row:
        bot.send_message(message.chat.id, "Сервер не найден.")
        conn.close()
        return

    api_url = server_row[0]
    access_url = create_outline_key(api_url)
    if not access_url:
        bot.send_message(message.chat.id, "Ошибка создания ключа. Обратитесь в поддержку.")
        conn.close()
        return

    subscription = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")

    # обновляем пользователя
    c.execute('''INSERT OR REPLACE INTO users (chat_id, subscription, reminder_sent)
                 VALUES (?, ?, 0)''', (chat_id, subscription))

    # сохраняем ключ в таблицу payments
    c.execute("UPDATE payments SET paid=1, access_url=? WHERE id=?", (access_url, payment_id))

    # бонус за реферал
    c.execute("SELECT referred_by FROM users WHERE chat_id=?", (chat_id,))
    ref = c.fetchone()
    if ref and ref[0]:
        ref_id = ref[0]
        c.execute("UPDATE users SET balance = balance + ? WHERE chat_id=?", (int(PRICE_RUB * 0.2), ref_id))

    conn.commit()
    conn.close()

    bot.send_message(message.chat.id, f"✅ Оплата подтверждена!\n🔗 Ваш ключ:\n`{access_url}`", parse_mode="Markdown")
    bot.send_message(ADMIN_CHAT_ID, f"✅ Новый клиент {chat_id}, регион: {location}")

@bot.message_handler(commands=["myvpn"])
def handle_myvpn(message):
    chat_id = str(message.chat.id)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT subscription FROM users WHERE chat_id=?", (chat_id,))
    row = c.fetchone()
    conn.close()

    if not row:
        bot.send_message(message.chat.id, "У вас нет активной подписки.")
        return

    bot.send_message(message.chat.id, f"⏳ Подписка активна до {row[0]}")

@bot.message_handler(commands=["balance"])
def balance(message):
    chat_id = str(message.chat.id)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE chat_id=?", (chat_id,))
    row = c.fetchone()
    conn.close()

    balance = row[0] if row else 0
    bot.send_message(message.chat.id, f"💰 Ваш баланс: {balance}₽")

@bot.message_handler(commands=["withdraw"])
def withdraw(message):
    chat_id = str(message.chat.id)
    bot.send_message(chat_id, "💳 Для вывода средств напишите @YourAdminUsername")

@bot.message_handler(commands=["referral"])
def referral(message):
    chat_id = str(message.chat.id)
    code = str(chat_id)
    link = f"https://t.me/{bot.get_me().username}?start={code}"
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET referral_code=? WHERE chat_id=?", (code, chat_id))
    conn.commit()
    conn.close()
    bot.send_message(chat_id, f"🎁 Ваша реферальная ссылка:\n{link}\nПриглашайте друзей и получайте 20% с каждой покупки!")

@bot.message_handler(commands=["keys"])
def list_keys(message):
    chat_id = str(message.chat.id)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT access_url, server, created_at FROM payments WHERE chat_id=? AND paid=1 ORDER BY created_at DESC", (chat_id,))
    rows = c.fetchall()
    conn.close()

    if not rows:
        bot.send_message(message.chat.id, "🔑 У вас нет активных ключей.")
        return

    for access_url, server, created in rows:
        text = f"🌍 {server}\n📅 {created}\n🔗 `{access_url}`"
        bot.send_message(message.chat.id, text, parse_mode="Markdown")

async def subscription_checker():
    while True:
        now = datetime.now()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        c.execute("SELECT chat_id, subscription, reminder_sent FROM users WHERE subscription IS NOT NULL")
        for chat_id, sub_date, reminded in c.fetchall():
            try:
                dt = datetime.strptime(sub_date, "%Y-%m-%d")
                if dt - now <= timedelta(days=2) and not reminded:
                    bot.send_message(chat_id, f"⏳ Подписка истекает {sub_date}")
                    c.execute("UPDATE users SET reminder_sent=1 WHERE chat_id=?", (chat_id,))
                elif dt < now:
                    bot.send_message(chat_id, "❌ Подписка завершена.")
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
