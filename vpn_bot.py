import telebot
import os
import json
import time
import subprocess

# --- Конфигурация ---
BOT_TOKEN = os.environ.get("BOT_TOKEN") or "7395071177:AAGGRZ2XX4Ornb6h9ESAXvOfsc7WdjFuAPA"
bot = telebot.TeleBot(BOT_TOKEN)

# --- База данных ---
DATA_FILE = "data.json"

def load_data():
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w") as f:
            json.dump({"servers": {}, "users": {}}, f)
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# --- Тарифы ---
TARIFFS = {
    "Неделя": {"price": 5, "duration_days": 7, "locations": ["Амстердам"]},
    "Месяц": {"price": 10, "duration_days": 30, "locations": ["Амстердам"]}
}

# --- Генерация ключа Outline ---
def create_outline_key(location):
    data = load_data()
    server = data["servers"].get(location)
    if not server:
        return None
    outline_api_url = server["outline_api_url"]
    try:
        result = subprocess.run(
            ["outline-cli", "createKey", outline_api_url],
            capture_output=True,
            text=True,
            check=True,
        )
        key_data = json.loads(result.stdout)
        return key_data["accessUrl"]
    except subprocess.CalledProcessError as e:
        print(f"Ошибка при создании ключа: {e}")
        return None

# --- Команды бота ---
@bot.message_handler(commands=["start"])
def handle_start(message):
    data = load_data()
    user_id = str(message.chat.id)
    if user_id not in data["users"]:
        data["users"][user_id] = {
            "subscription": None,
            "payment_history": [],
            "reminder_sent": False
        }
        save_data(data)
    bot.send_message(message.chat.id, "👋 Добро пожаловать!\n\n"
                                      "Используйте команду /plans для просмотра тарифов.")

@bot.message_handler(commands=["plans"])
def handle_plans(message):
    text = "📋 *Доступные тарифы и локации:*\n\n"
    for plan_name, details in TARIFFS.items():
        text += f"🔹 *{plan_name}*\n"
        text += f"Цена: {details['price']} USD за {details['duration_days']} дней\n"
        text += f"Локации: {', '.join(details['locations'])}\n\n"
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(commands=["profile"])
def handle_profile(message):
    data = load_data()
    user_id = str(message.chat.id)
    user = data["users"].get(user_id)
    if not user or not user["subscription"]:
        bot.send_message(message.chat.id, "❗ У вас нет активной подписки. Используйте /plans для выбора тарифа.")
        return
    sub = user["subscription"]
    expires_at = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(sub["expires_at"]))
    bot.send_message(message.chat.id, f"🎟️ Ваш профиль:\n\n"
                                      f"Тариф: {sub['plan']}\n"
                                      f"Локация: {sub['location']}\n"
                                      f"Ссылка:\n{sub['access_url']}\n"
                                      f"Действует до: {expires_at}")

@bot.message_handler(commands=["buy"])
def handle_buy(message):
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    for plan_name in TARIFFS.keys():
        markup.add(plan_name)
    bot.send_message(message.chat.id, "Выберите тарифный план:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in TARIFFS.keys())
def handle_plan_selection(message):
    user_id = str(message.chat.id)
    plan_name = message.text
    plan = TARIFFS[plan_name]
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    for loc in plan["locations"]:
        markup.add(loc)
    bot.send_message(message.chat.id, f"Отлично! Теперь выберите локацию для {plan_name}:", reply_markup=markup)

    data = load_data()
    if user_id not in data["users"]:
        data["users"][user_id] = {"subscription": None, "payment_history": [], "reminder_sent": False}
    data["users"][user_id]["pending_plan"] = plan_name
    save_data(data)

@bot.message_handler(func=lambda m: any(m.text in p["locations"] for p in TARIFFS.values()))
def handle_location_selection(message):
    user_id = str(message.chat.id)
    data = load_data()
    plan_name = data["users"][user_id].get("pending_plan")
    if not plan_name:
        bot.send_message(message.chat.id, "❗ Ошибка: выберите тариф сначала.")
        return
    location = message.text
    duration_days = TARIFFS[plan_name]["duration_days"]

    access_url = create_outline_key(location)
    if not access_url:
        bot.send_message(message.chat.id, "❗ Ошибка при создании VPN-ключа. Проверьте серверы.")
        return

    expires_at = int(time.time()) + duration_days * 86400
    data["users"][user_id]["subscription"] = {
        "plan": plan_name,
        "location": location,
        "access_url": access_url,
        "expires_at": expires_at
    }
    data["users"][user_id]["payment_history"].append({
        "plan": plan_name,
        "location": location,
        "amount": TARIFFS[plan_name]["price"],
        "status": "test",
        "timestamp": int(time.time())
    })
    save_data(data)

    bot.send_message(message.chat.id, f"✅ Ваш VPN-ключ создан!\n\n"
                                      f"Тариф: {plan_name}\n"
                                      f"Локация: {location}\n"
                                      f"Ссылка для подключения:\n{access_url}\n\n"
                                      f"Подписка активна {duration_days} дней.")

# --- Запуск бота ---
print("Бот запущен...")
bot.polling(none_stop=True)
