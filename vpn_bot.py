import telebot
import os
import json
import time
import asyncio
from datetime import datetime, timedelta
import requests

# --- Конфигурация ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0"))
PRICE_RUB = 199  # цена подписки в рублях

bot = telebot.TeleBot(BOT_TOKEN)

# --- Работа с файлами ---
def load_data():
    try:
        with open("data.json", "r") as f:
            return json.load(f)
    except:
        return {"servers": {}, "users": {}, "pending_payments": {}}

def save_data(data):
    with open("data.json", "w") as f:
        json.dump(data, f, indent=2)

# --- Создание ключа Outline через HTTP ---
def create_outline_key(location):
    data = load_data()
    server = data["servers"].get(location)
    if not server:
        print(f"❌ Сервер {location} не найден.")
        return None

    api_url = server["outline_api_url"].rstrip("/")
    try:
        requests.post(f"{api_url}/access-keys", verify=False, timeout=10)
        keys = requests.get(f"{api_url}/access-keys", verify=False, timeout=10).json()
        if isinstance(keys, list) and keys:
            return keys[-1]["accessUrl"]
        return None
    except Exception as e:
        print(f"❌ Ошибка Outline: {e}")
        return None

# --- Привязка ключа к пользователю ---
def add_user_subscription(chat_id, access_url, server_name, duration_days=7):
    data = load_data()
    data["users"][str(chat_id)] = {
        "subscription": (datetime.utcnow() + timedelta(days=duration_days)).strftime("%Y-%m-%d"),
        "access_url": access_url,
        "server": server_name,
        "reminder_sent": False
    }
    save_data(data)

# --- Команда /myvpn ---
@bot.message_handler(commands=["myvpn"])
def handle_myvpn(message):
    chat_id = str(message.chat.id)
    data = load_data()
    user = data["users"].get(chat_id)
    if not user:
        bot.send_message(message.chat.id, "У вас пока нет активной подписки.")
        return

    text = (
        f"🌍 Сервер: {user['server']}\n"
        f"🔗 Ссылка: `{user['access_url']}`\n"
        f"⏳ До: {user['subscription']}`"
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

# --- Автоудаление просроченных подписок ---
async def auto_cleanup_expired_keys():
    while True:
        data = load_data()
        now = datetime.utcnow().date()
        changed = False
        for chat_id, user in list(data["users"].items()):
            try:
                sub_date = datetime.strptime(user["subscription"], "%Y-%m-%d").date()
                if sub_date < now:
                    api_url = data["servers"][user["server"]]["outline_api_url"].rstrip("/")
                    if "access-keys/" in user["access_url"]:
                        key_id = user["access_url"].split("access-keys/")[-1]
                        requests.delete(f"{api_url}/access-keys/{key_id}", verify=False)
                    del data["users"][chat_id]
                    changed = True
            except:
                continue
        if changed:
            save_data(data)
        await asyncio.sleep(3600)

# --- Команды ---
@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    bot.send_message(message.chat.id, "Добро пожаловать! Используйте /buy для покупки VPN. Ваш личный кабинет: /myvpn")

@bot.message_handler(commands=["buy"])
def buy_key(message):
    data = load_data()
    markup = telebot.types.InlineKeyboardMarkup()
    for location in data.get("servers", {}).keys():
        markup.add(telebot.types.InlineKeyboardButton(location, callback_data=f"buy_{location}"))
    bot.send_message(message.chat.id, f"Выберите регион. Стоимость подписки: {PRICE_RUB}₽", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("buy_"))
def handle_location_selection(call):
    location = call.data.split("_")[1]
    chat_id = call.message.chat.id

    # Здесь заглушка под оплату
    markup = telebot.types.InlineKeyboardMarkup()
    fake_pay_url = "https://yoomoney.ru"  # Временно фейковая ссылка
    markup.add(telebot.types.InlineKeyboardButton("💳 Оплатить", url=fake_pay_url))

    message_text = (
        f"Вы выбрали регион: {location}\n"
        f"💰 Цена: {PRICE_RUB}₽\n"
        "Нажмите кнопку ниже для оплаты:"
    )
    bot.send_message(chat_id, message_text, reply_markup=markup)


    # В реальной интеграции здесь должен быть redirect_url от платёжной системы

# --- Запуск ---
async def main():
    asyncio.create_task(auto_cleanup_expired_keys())
    print("Бот запущен...")
    await bot.polling(non_stop=True)

if __name__ == "__main__":
    asyncio.run(main())
