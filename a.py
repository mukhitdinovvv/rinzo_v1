import os
import sys
import json
import time
import re
import threading
from datetime import datetime
from dotenv import load_dotenv

# Исправление кодировки для Windows консоли
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

import requests
from telegram import Update, ForceReply
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from openai import OpenAI

load_dotenv()

# ======================== КОНФИГУРАЦИЯ ========================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_TABLE_NAME = os.getenv("AIRTABLE_TABLE_NAME", "Orders")

# Telegram ID сотрудников кухни
KITCHEN_STAFF_IDS = os.getenv("KITCHEN_STAFF_IDS", "").split(",")
KITCHEN_STAFF_IDS = [id.strip() for id in KITCHEN_STAFF_IDS if id.strip()]

# Файл для сохранения диалогов
CONVERSATIONS_FILE = "telegram_conversations.json"

# ======================== МЕНЮ ========================

MENU = {
    "Бургеры": [
        {"name": "Классический бургер", "price": 1500, "desc": "Говядина, салат, помидор, сыр"},
        {"name": "Чизбургер", "price": 1800, "desc": "Двойной сыр, говядина, соус"},
        {"name": "Курица бургер", "price": 1600, "desc": "Куриная котлета, салат, майонез"},
        {"name": "Фиш бургер", "price": 1700, "desc": "Рыбная котлета, сырный соус"}
    ],
    "Пицца": [
        {"name": "Маргарита", "price": 2500, "desc": "Томаты, моцарелла, базилик"},
        {"name": "Пепперони", "price": 3000, "desc": "Колбаса пепперони, сыр"},
        {"name": "4 сыра", "price": 3200, "desc": "Моцарелла, чеддер, пармезан, дор блю"},
        {"name": "Мясная", "price": 3500, "desc": "Говядина, ветчина, бекон"}
    ],
    "Напитки": [
        {"name": "Кола", "price": 500, "desc": "0.5л"},
        {"name": "Фанта", "price": 500, "desc": "0.5л"},
        {"name": "Спрайт", "price": 500, "desc": "0.5л"},
        {"name": "Сок", "price": 600, "desc": "0.5л апельсиновый"}
    ],
    "Допы": [
        {"name": "Картофель фри", "price": 800, "desc": "Средняя порция"},
        {"name": "Наггетсы", "price": 1200, "desc": "6 шт"},
        {"name": "Луковые кольца", "price": 900, "desc": "10 шт"},
        {"name": "Соусы", "price": 200, "desc": "Кетчуп, майонез, сырный"}
    ]
}

KASPI_PAYMENT_INFO = {
    "ru": "💳 Номер Kaspi: +7 777 123 4567\n👤 Получатель: ТОО 'Доставка'",
    "kk": "💳 Kaspi нөмірі: +7 777 123 4567\n👤 Алушы: 'Жеткізу' ЖШС",
    "en": "💳 Kaspi number: +7 777 123 4567\n👤 Recipient: Delivery LLC"
}

# ======================== ХРАНИЛИЩЕ ДАННЫХ ========================

conversations = {}
checked_records = set()

def load_conversations():
    global conversations
    if os.path.exists(CONVERSATIONS_FILE):
        try:
            with open(CONVERSATIONS_FILE, 'r', encoding='utf-8') as f:
                conversations = json.load(f)
        except Exception as e:
            print(f"⚠️ Ошибка загрузки: {e}")
            conversations = {}
    return conversations

def save_conversations():
    try:
        with open(CONVERSATIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(conversations, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Ошибка сохранения: {e}")

def ensure_conversation(user_id):
    user_id = str(user_id)
    if user_id not in conversations:
        conversations[user_id] = {
            "messages": [],
            "language": None,
            "order_placed": False,
            "waiting_for_receipt": False,
            "airtable_record_id": None,
            "pending_order": None,
            "user_name": "",
            "phone": "",
            "receipt_file_id": None,
            "receipt_type": None,
            "last_interaction": time.time()
        }
    else:
        conversations[user_id]["last_interaction"] = time.time()
    return conversations[user_id]

# ======================== AI CLIENT ========================

perplexity_client = OpenAI(
    api_key=PERPLEXITY_API_KEY, 
    base_url="https://api.perplexity.ai"
)

# ======================== ГЕНЕРАЦИЯ МЕНЮ ========================

def generate_menu_text(language="ru"):
    if language == "kk":
        menu_text = "\n🍽 МӘЗІР:\n\n"
        categories_kk = {
            "Бургеры": "Бургерлер",
            "Пицца": "Пицца",
            "Напитки": "Сусындар",
            "Допы": "Қосымша"
        }
        for category, items in MENU.items():
            menu_text += f"━━ {categories_kk.get(category, category)} ━━\n"
            for item in items:
                menu_text += f"  • {item['name']}: {item['price']}₸\n    ({item['desc']})\n"
            menu_text += "\n"
    elif language == "en":
        menu_text = "\n🍽 MENU:\n\n"
        for category, items in MENU.items():
            menu_text += f"━━ {category} ━━\n"
            for item in items:
                menu_text += f"  • {item['name']}: {item['price']}₸\n    ({item['desc']})\n"
            menu_text += "\n"
    else:  # ru
        menu_text = "\n🍽 МЕНЮ:\n\n"
        for category, items in MENU.items():
            menu_text += f"━━ {category} ━━\n"
            for item in items:
                menu_text += f"  • {item['name']}: {item['price']}₸\n    ({item['desc']})\n"
            menu_text += "\n"
    return menu_text

# ======================== SYSTEM PROMPT ========================

# ======================== SYSTEM PROMPT ========================

def get_system_prompt(language="ru"):
    menu_text = generate_menu_text(language)
    payment_info = KASPI_PAYMENT_INFO.get(language, KASPI_PAYMENT_INFO["ru"])
    
    common_rules = """
NO MARKDOWN. NO **bold**. NO *italic*. NO `code`.
Just clear, plain text.
Don't be robotic. Be human, friendly and fast.
"""
    
    if language == "kk":
        return f"""Сіз фаст-фуд жеткізу қызметінің сатушысысыз.
{common_rules}
МАҚСАТ: Тапсырысты қабылдау және Kaspi арқылы төлем сұрау.

Сөйлесу мәнері:
- Қысқа әрі нұсқа жауап беріңіз.
- Клиентпен дос сияқты сөйлесіңіз.
- "Жұлдызша" немесе "астын сызу" белгілерін ҚОЛДАНБАҢЫЗ.
- Эмодзи: әр хабарламада 1-2 ғана.

САТУ КЕЗЕҢДЕРІ:
1. Амандасу және таңдауға көмектесу.
2. Тапсырысты нақтылау (комбо ұсыну).
3. Төлем деректерін беру және чекті сұрау.

ТАПСЫРЫС ДАЙЫН БОЛҒАНДА (тауар, баға, адрес, телефон бар):
Келесі JSON форматын ҚОСЫҢЫЗ (клиентке көрсетілмейді, тек жүйе үшін):
{{
  "order_confirmed": true,
  "customer_name": "Аты",
  "phone": "Телефон",
  "order_items": ["Тауар 1", "Тауар 2"],
  "total_price": 5000,
  "delivery_address": "Мекенжай"
}}

МӘЗІР:
{menu_text}

{payment_info}
"""
    
    elif language == "en":
        return f"""You are a fast food delivery seller.
{common_rules}
GOAL: Take the order and request Kaspi payment.

Style:
- Short and clear answers.
- Talk like a friend.
- DO NOT use markdown (*, **).
- Emojis: 1-2 per message max.

STAGES:
1. Greet and help choose.
2. Confirm items (upsell combo).
3. Give payment info and ask for receipt.

WHEN ORDER IS READY (items, price, address, phone are known):
Include this JSON (invisible to user, for system only):
{{
  "order_confirmed": true,
  "customer_name": "Name",
  "phone": "Phone",
  "order_items": ["Item 1", "Item 2"],
  "total_price": 5000,
  "delivery_address": "Address"
}}

MENU:
{menu_text}

{payment_info}
"""
    
    else:  # Russian
        return f"""Ты — продавец доставки фаст-фуда.
{common_rules}
ЦЕЛЬ: Принять заказ и запросить оплату Kaspi.

Стиль общения:
- Пиши кратко и по делу.
- Общайся как друг, тепло и просто.
- ЗАПРЕЩЕНО использовать жирный шрифт (звездочки **) или курсив.
- Эмодзи: 1-2 на сообщение, не больше.

ЭТАПЫ:
1. Приветствие и помощь с выбором.
2. Уточнение заказа (предложи комбо).
3. Выдача реквизитов и просьба скинуть чек.

КОГДА ЗАКАЗ СОБРАН (есть блюда, сумма, адрес, телефон):
Вставь этот JSON (клиент его не увидит, он для системы):
{{
  "order_confirmed": true,
  "customer_name": "Имя",
  "phone": "Телефон",
  "order_items": ["Товар 1", "Товар 2"],
  "total_price": 5000,
  "delivery_address": "Адрес"
}}

МЕНЮ:
{menu_text}

{payment_info}
"""

def clean_markdown(text):
    """Удаляет markdown символы из текста"""
    if not text:
        return ""
    # Удаляем жирный, курсив, код
    text = re.sub(r'\*\*|__|\*|_|`', '', text)
    # Удаляем заголовки
    text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)
    return text.strip()

# ======================== AI ДИАЛОГ ========================

def get_ai_response(user_id, user_message):
    """Получает ответ от AI с правильным форматом сообщений"""
    conv = ensure_conversation(user_id)
    
    # Определяем язык, если еще не определен
    if not conv.get("language"):
        conv["language"] = detect_language(user_message)
        save_conversations()
    
    # Формируем запрос к AI с правильным чередованием
    system_prompt = get_system_prompt(conv["language"])
    
    # Собираем историю с правильным чередованием user/assistant
    messages = [{"role": "system", "content": system_prompt}]
    
    # Добавляем историю, убеждаясь что сообщения чередуются
    history = conv["messages"][-10:]  # Последние 10 сообщений
    
    # Фильтруем и обеспечиваем правильное чередование
    last_role = None
    for msg in history:
        role = msg.get("role")
        # Пропускаем сообщения с одинаковой ролью подряд
        if role in ["user", "assistant"] and role != last_role:
            messages.append(msg)
            last_role = role
    
    # Убеждаемся что последнее сообщение в истории - от assistant
    # Если последнее от user, удаляем его чтобы избежать двух user подряд
    if messages and messages[-1].get("role") == "user":
        messages.pop()
    
    # Добавляем новое сообщение пользователя
    messages.append({"role": "user", "content": user_message})
    
    try:
        response = perplexity_client.chat.completions.create(
            model="sonar-pro",
            messages=messages,
            temperature=0.6,
            max_tokens=600
        )
        
        ai_reply = response.choices[0].message.content
        print(f"🤖 AI Raw: {ai_reply[:50]}...") 
        
        # 1. Поиск JSON (заказа)
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', ai_reply, re.DOTALL)
        if not json_match:
             json_match = re.search(r'(\{[\s\S]*"order_confirmed"[\s\S]*?\})', ai_reply)

        if json_match:
            try:
                json_str = json_match.group(1)
                order_data = json.loads(json_str)
                
                if order_data.get("order_confirmed"):
                    print("✅ Заказ распознан!")
                    conv["pending_order"] = order_data
                    conv["waiting_for_receipt"] = True
                    save_conversations()
                
                # Убираем JSON из текста
                if json_str in ai_reply:
                    ai_reply = ai_reply.replace(json_str, '')
                ai_reply = re.sub(r'```(?:json)?\s*```', '', ai_reply).strip()
                    
            except json.JSONDecodeError:
                pass
        
        # 2. Очистка от Markdown и лишнего мусора
        ai_reply = clean_markdown(ai_reply)
        
        # Сохраняем сообщения
        conv["messages"].append({"role": "user", "content": user_message})
        conv["messages"].append({"role": "assistant", "content": ai_reply})
        conv["messages"] = conv["messages"][-20:]
        save_conversations()
        
        return ai_reply
        
    except Exception as e:
        print(f"❌ Ошибка AI: {e}")
        return "Извините, ошибка связи. Повторите пожалуйста."

# ======================== TELEGRAM HANDLERS ========================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user_id = update.effective_user.id
    conv = ensure_conversation(user_id)
    
    user_language = update.effective_user.language_code
    if user_language and user_language.startswith('kk'):
        conv["language"] = "kk"
    elif user_language and user_language.startswith('en'):
        conv["language"] = "en"
    else:
        conv["language"] = "ru"
    
    conv["user_name"] = update.effective_user.first_name or ""
    save_conversations()
    
    welcome_messages = {
        "ru": f"Привет, {conv['user_name']}! 😊\n\nКакой чудесный день! Я помогу оформить вкусный заказ 🍔\nЧто хотите заказать?",
        "kk": f"Сәлем, {conv['user_name']}! 😊\n\nҚандай керемет күн! Мен дәмді тапсырысты ресімдеуге көмектесемін 🍔\nНе тапсырыс бергіңіз келеді?",
        "en": f"Hello, {conv['user_name']}! 😊\n\nWhat a wonderful day! I'll help you order something delicious 🍔\nWhat would you like to order?"
    }
    
    await update.message.reply_text(welcome_messages.get(conv["language"], welcome_messages["ru"]))

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /status для проверки статуса заказа"""
    user_id = str(update.effective_user.id)
    conv = conversations.get(user_id)
    
    if not conv:
        await update.message.reply_text("У вас пока нет заказов 🤷‍♂️")
        return
    
    record_id = conv.get("airtable_record_id")
    
    if not record_id:
        if conv.get("waiting_for_receipt"):
            await update.message.reply_text(
                "⏳ Ожидаем чек оплаты\n\n"
                "Пришлите фото или PDF чека, чтобы мы начали готовить ваш заказ 📸"
            )
        else:
            await update.message.reply_text("У вас пока нет активных заказов 🤷‍♂️")
        return
    
    # Получаем статус из Airtable
    status = get_order_status(record_id)
    
    if not status:
        await update.message.reply_text("❌ Не удалось проверить статус заказа")
        return
    
    # Формируем ответ
    lang = conv.get("language", "ru")
    
    if status['payment_correct']:
        if status['kitchen_status'] == 'Cooking':
            messages = {
                "ru": "✅ Оплата принята!\n👨‍🍳 Ваш заказ готовится на кухне\n\nСкоро доставим! 🚀",
                "kk": "✅ Төлем қабылданды!\n👨‍🍳 Тапсырысыңыз асханада дайындалуда\n\nЖақын арада жеткіземіз! 🚀",
                "en": "✅ Payment accepted!\n👨‍🍳 Your order is being prepared\n\nWe'll deliver soon! 🚀"
            }
        elif status['kitchen_status'] == 'Ready':
            messages = {
                "ru": "✅ Заказ готов!\n🚗 Курьер уже в пути к вам!",
                "kk": "✅ Тапсырыс дайын!\n🚗 Курьер сізге қарай жолда!",
                "en": "✅ Order is ready!\n🚗 Courier is on the way!"
            }
        else:
            messages = {
                "ru": "✅ Оплата принята!\n⏳ Заказ в обработке",
                "kk": "✅ Төлем қабылданды!\n⏳ Тапсырыс өңделуде",
                "en": "✅ Payment accepted!\n⏳ Order is being processed"
            }
        
        await update.message.reply_text(messages.get(lang, messages["ru"]))
    else:
        # Оплата не принята менеджером
        messages = {
            "ru": "⏳ Чек на проверке у менеджера\n\n"
                  "Если есть проблемы с оплатой, менеджер свяжется с вами.\n"
                  "Обычно проверка занимает 5-10 минут ⏱",
            "kk": "⏳ Чек менеджерде тексерілуде\n\n"
                  "Егер төлеммен проблема болса, менеджер сізбен байланысады.\n"
                  "Әдетте тексеру 5-10 минут алады ⏱",
            "en": "⏳ Receipt is being checked by manager\n\n"
                  "If there are payment issues, manager will contact you.\n"
                  "Usually takes 5-10 minutes ⏱"
        }
        
        await update.message.reply_text(messages.get(lang, messages["ru"]))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений"""
    user_id = update.effective_user.id
    user_message = update.message.text
    
    # Показываем "печатает..."
    await update.message.chat.send_action("typing")
    
    # Получаем ответ от AI
    ai_response = get_ai_response(user_id, user_message)
    
    await update.message.reply_text(ai_response)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик документов (чеков в PDF)"""
    user_id = update.effective_user.id
    conv = ensure_conversation(user_id)
    
    if not conv.get("waiting_for_receipt"):
        messages = {
            "ru": "Сначала оформите заказ, затем пришлите чек 😊",
            "kk": "Алдымен тапсырысты ресімдеңіз, содан кейін чекті жіберіңіз 😊",
            "en": "Please place your order first, then send the receipt 😊"
        }
        await update.message.reply_text(messages.get(conv.get("language", "ru"), messages["ru"]))
        return
    
    await update.message.reply_text("⏳ Сохраняю чек...")
    
    # Получаем file_id документа
    document = update.message.document
    file_id = document.file_id
    
    print(f"📄 Получен документ с file_id: {file_id}")
    
    if conv.get("pending_order"):
        # Сохраняем file_id для отправки на кухню
        conv["pending_order"]["receipt_file_id"] = file_id
        conv["pending_order"]["receipt_type"] = "document"
        
        # Получаем URL файла для Airtable (опционально)
        file_url = get_telegram_file_url(file_id)
        if file_url:
            conv["pending_order"]["payment_receipt"] = [{"url": file_url}]
        
        # СОЗДАЕМ запись в Airtable ТОЛЬКО СЕЙЧАС
        record_id = create_airtable_record(conv["pending_order"])
        
        if record_id:
            conv["waiting_for_receipt"] = False
            conv["order_placed"] = True
            conv["airtable_record_id"] = record_id
            conv["receipt_file_id"] = file_id
            conv["receipt_type"] = "document"
            conv["pending_order"] = None
            save_conversations()
            
            success_messages = {
                "ru": "✅ Чек получен и сохранен!\n\n"
                      "🔍 Менеджер проверит оплату в течение 5-10 минут\n"
                      "✅ После подтверждения заказ уйдет на кухню\n\n"
                      "Проверить статус: /status",
                "kk": "✅ Чек алынды және сақталды!\n\n"
                      "🔍 Менеджер 5-10 минут ішінде төлемді тексереді\n"
                      "✅ Растаудан кейін тапсырыс асханаға кетеді\n\n"
                      "Статусты тексеру: /status",
                "en": "✅ Receipt received and saved!\n\n"
                      "🔍 Manager will check payment in 5-10 minutes\n"
                      "✅ After confirmation order goes to kitchen\n\n"
                      "Check status: /status"
            }
            
            await update.message.reply_text(success_messages.get(conv.get("language", "ru"), success_messages["ru"]))
        else:
            error_messages = {
                "ru": "❌ Ошибка сохранения. Попробуйте позже или свяжитесь с поддержкой.",
                "kk": "❌ Сақтау қатесі. Кейінірек қайталаңыз немесе қолдау қызметіне хабарласыңыз.",
                "en": "❌ Save error. Try later or contact support."
            }
            await update.message.reply_text(error_messages.get(conv.get("language", "ru"), error_messages["ru"]))
    else:
        error_messages = {
            "ru": "❌ Ошибка: заказ не найден. Попробуйте оформить заказ заново.",
            "kk": "❌ Қате: тапсырыс табылмады. Тапсырысты қайта ресімдеңіз.",
            "en": "❌ Error: order not found. Please place your order again."
        }
        await update.message.reply_text(error_messages.get(conv.get("language", "ru"), error_messages["ru"]))

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик фото (чеки в виде картинки)"""
    user_id = update.effective_user.id
    conv = ensure_conversation(user_id)
    
    if not conv.get("waiting_for_receipt"):
        messages = {
            "ru": "Сначала оформите заказ, затем пришлите чек 😊",
            "kk": "Алдымен тапсырысты ресімдеңіз, содан кейін чекті жіберіңіз 😊",
            "en": "Please place your order first, then send the receipt 😊"
        }
        await update.message.reply_text(messages.get(conv.get("language", "ru"), messages["ru"]))
        return
    
    await update.message.reply_text("⏳ Сохраняю чек...")
    
    # Получаем file_id фото
    photo = update.message.photo[-1]
    file_id = photo.file_id
    
    print(f"📸 Получено фото с file_id: {file_id}")
    
    if conv.get("pending_order"):
        # Сохраняем file_id для отправки на кухню
        conv["pending_order"]["receipt_file_id"] = file_id
        conv["pending_order"]["receipt_type"] = "photo"
        
        # Получаем URL файла для Airtable (опционально)
        file_url = get_telegram_file_url(file_id)
        if file_url:
            conv["pending_order"]["payment_receipt"] = [{"url": file_url}]
        
        # СОЗДАЕМ запись в Airtable ТОЛЬКО СЕЙЧАС
        record_id = create_airtable_record(conv["pending_order"])
        
        if record_id:
            conv["waiting_for_receipt"] = False
            conv["order_placed"] = True
            conv["airtable_record_id"] = record_id
            conv["receipt_file_id"] = file_id
            conv["receipt_type"] = "photo"
            conv["pending_order"] = None
            save_conversations()
            
            success_messages = {
                "ru": "✅ Чек получен и сохранен!\n\n"
                      "🔍 Менеджер проверит оплату в течение 5-10 минут\n"
                      "✅ После подтверждения заказ уйдет на кухню\n\n"
                      "Проверить статус: /status",
                "kk": "✅ Чек алынды және сақталды!\n\n"
                      "🔍 Менеджер 5-10 минут ішінде төлемді тексереді\n"
                      "✅ Растаудан кейін тапсырыс асханаға кетеді\n\n"
                      "Статусты тексеру: /status",
                "en": "✅ Receipt received and saved!\n\n"
                      "🔍 Manager will check payment in 5-10 minutes\n"
                      "✅ After confirmation order goes to kitchen\n\n"
                      "Check status: /status"
            }
            
            await update.message.reply_text(success_messages.get(conv.get("language", "ru"), success_messages["ru"]))
        else:
            error_messages = {
                "ru": "❌ Ошибка сохранения. Попробуйте позже или свяжитесь с поддержкой.",
                "kk": "❌ Сақтау қатесі. Кейінірек қайталаңыз немесе қолдау қызметіне хабарласыңыз.",
                "en": "❌ Save error. Try later or contact support."
            }
            await update.message.reply_text(error_messages.get(conv.get("language", "ru"), error_messages["ru"]))
    else:
        error_messages = {
            "ru": "❌ Ошибка: заказ не найден. Попробуйте оформить заказ заново.",
            "kk": "❌ Қате: тапсырыс табылмады. Тапсырысты қайта ресімдеңіз.",
            "en": "❌ Error: order not found. Please place your order again."
        }
        await update.message.reply_text(error_messages.get(conv.get("language", "ru"), error_messages["ru"]))

# ======================== ФОНОВАЯ ПРОВЕРКА ========================
# ======================== ЧЕК ОПЛАЧЕННЫХ ЗАКАЗОВ ========================

def check_paid_orders():
    """
    Проверяет оплату заказов.
    Пока заглушка: выводит в консоль. 
    Здесь должна быть логика проверки Airtable.
    """
    print("🔍 Проверка оплаченных заказов...")
    
    # Пример: помечаем все pending_order как оплаченные
    for user_id, conv in conversations.items():
        if conv.get("pending_order"):
            print(f"Пользователь {user_id} имеет pending_order")

def background_checker():
    """Проверяет оплаченные заказы каждые 15 секунд"""
    while True:
        try:
            check_paid_orders()
            time.sleep(15)
        except Exception as e:
            print(f"Ошибка чекера: {e}")
            time.sleep(15)

# ======================== ЗАПУСК БОТА ========================

def main():
    """Главная функция"""
    print("🚀 Telegram бот запущен")
    print(f"📊 Airtable: {AIRTABLE_BASE_ID}")
    print(f"👨‍🍳 Сотрудников: {len(KITCHEN_STAFF_IDS)}")
    print("━" * 50)
    
    load_conversations()
    
    checker_thread = threading.Thread(target=background_checker, daemon=True)
    checker_thread.start()
    print("✅ Фоновый чекер запущен")
    
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    
    print("🤖 Бот готов к работе!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
