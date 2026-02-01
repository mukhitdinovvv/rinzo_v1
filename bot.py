from openai import OpenAI
import requests
import json
from datetime import datetime
import os
import threading
import time
import re
from dotenv import load_dotenv

load_dotenv()

LAUNCH_TIMESTAMP = int(time.time())
CONVERSATIONS_FILE = "conversations.json"

def load_conversations():
    if os.path.exists(CONVERSATIONS_FILE):
        try:
            with open(CONVERSATIONS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Ошибка загрузки диалогов: {e}")
    return {}

def save_conversations(data):
    try:
        with open(data/CONVERSATIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Ошибка сохранения диалогов: {e}")

# ======================== КОНФИГУРАЦИЯ ========================

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
WHATSAPP_PHONE_ID = os.getenv("WHATSAPP_PHONE_ID")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_TABLE_NAME = os.getenv("AIRTABLE_TABLE_NAME", "Orders")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DROPBOX_ACCESS_TOKEN = os.getenv("DROPBOX_ACCESS_TOKEN")

# Список Telegram ID сотрудников кухни (через запятую)
KITCHEN_STAFF_IDS = os.getenv("KITCHEN_STAFF_IDS", "").split(",")
KITCHEN_STAFF_IDS = [id.strip() for id in KITCHEN_STAFF_IDS if id.strip()]

# ======================== МЕНЮ ФАСТ-ФУДА ========================

MENU = {
    "Бургеры": [
        {"name": "Классический бургер", "price": 1500, "desc": "Говядина, салат, помидор, сыр"},
        {"name": "Чизбургер", "price": 1800, "desc": "Двойной сыр, говядина, соус"},
        {"name": "Курица бургер", "price": 1600, "desc": "Куриная котлета, салат, майонез"}
    ],
    "Пицца": [
        {"name": "Маргарита", "price": 2500, "desc": "Томаты, моцарелла, базилик"},
        {"name": "Пепперони", "price": 3000, "desc": "Колбаса пепперони, сыр"},
        {"name": "4 сыра", "price": 3200, "desc": "Моцарелла, чеддер, пармезан, дор блю"}
    ],
    "Напитки": [
        {"name": "Кола", "price": 500, "desc": "0.5л"},
        {"name": "Фанта", "price": 500, "desc": "0.5л"},
        {"name": "Сок", "price": 600, "desc": "0.5л апельсиновый"}
    ],
    "Допы": [
        {"name": "Картофель фри", "price": 800, "desc": "Средняя порция"},
        {"name": "Наггетсы", "price": 1200, "desc": "6 шт"},
        {"name": "Соусы", "price": 200, "desc": "Кетчуп, майонез, сырный"}
    ]
}

KASPI_PAYMENT_INFO = """
💳 ОПЛАТА ЧЕРЕЗ KASPI:
Номер: +7 777 123 4567
Получатель: ТОО "Доставка"
После оплаты пришлите скриншот чека
"""

# ======================== SYSTEM PROMPT ========================

def generate_menu_text():
    menu_text = ""
    for category, items in MENU.items():
        menu_text += f"\n{category}:\n"
        for item in items:
            menu_text += f"- {item['name']}: {item['price']}₸ ({item['desc']})\n"
    return menu_text

SYSTEM_PROMPT = f"""
Ты — AI-продавец службы доставки фаст-фуда через WhatsApp.

ТВОЯ ЦЕЛЬ — ПРИНЯТЬ И ОФОРМИТЬ ЗАКАЗ.

ПРАВИЛА ОБЩЕНИЯ:
- Автоматически определяй язык клиента (русский/казахский)
- Общайся ТОЛЬКО на одном языке, не смешивай
- Будь вежливым, но говори как обычный человек, не как робот
- Не используй markdown форматирование (жирный, курсив и т.д.), пиши просто текст
- Никогда не обсуждай политику или посторонние темы
- Работаешь только с заказами еды

СЦЕНАРИЙ ПРОДАЖ:

1. ПРИВЕТСТВИЕ
- Просто поздоровайся. Не предлагай сразу меню или помощь, если клиент сам не спросил.
- Пример: "Здравствуйте!", "Привет! Чем могу помочь?"

2. КОНСУЛЬТАЦИЯ
- Помоги выбрать блюда
- Отвечай на вопросы о меню
- Предлагай допы и напитки (ненавязчиво)

3. СБОР ЗАКАЗА
- Уточни состав заказа
- Посчитай общую сумму
- Спроси адрес доставки
- Запроси номер телефона для связи

4. ОПЛАТА
- Предоставь реквизиты Kaspi
- Попроси прислать официальный чек в формате PDF
- Скажи, что заказ будет передан на кухню ТОЛЬКО после получения чека

5. ПОДТВЕРЖДЕНИЕ
После получения ВСЕХ данных (заказ, адрес, телефон) выдай JSON:

```json
{{
  "order_confirmed": true,
  "customer_name": "имя клиента",
  "phone": "+7...",
  "order_items": ["Бургер x2", "Кола x1"],
  "total_price": 3500,
  "delivery_address": "полный адрес"
}}
```

JSON выводи ТОЛЬКО когда:
- Клиент подтвердил заказ
- Указал адрес
- Указал телефон
- Готов оплатить

НАШЕ МЕНЮ:
{generate_menu_text()}

{KASPI_PAYMENT_INFO}

UP-SELL ПРИМЕРЫ:
- "К бургеру добавить картофель фри?"
- "Возьмете напиток?"
- "Соусы нужны?"

Не отпускай клиента без оформления заказа.
"""

client = OpenAI(api_key=PERPLEXITY_API_KEY, base_url="https://api.perplexity.ai")

conversations = load_conversations()
processed_message_ids = set()
processed_lock = threading.Lock()
payment_reminders = {}
checked_records = set()

# ======================== CONVERSATION MANAGEMENT ========================

def ensure_conversation(user_phone):
    if user_phone not in conversations:
        conversations[user_phone] = {
            "messages": [],
            "order_placed": False,
            "waiting_for_receipt": False,
            "airtable_record_id": None,
            "pending_order": None
        }
    return conversations[user_phone]

def remember_user_message_only(user_phone, user_message):
    conv = ensure_conversation(user_phone)
    conv["messages"].append({"role": "user", "content": user_message})
    conv["messages"] = conv["messages"][-20:]
    save_conversations(conversations)
    return conv

def mark_message_processed(msg_id):
    with processed_lock:
        if msg_id in processed_message_ids:
            return False
        processed_message_ids.add(msg_id)
        if len(processed_message_ids) > 2000:
            processed_message_ids.clear()
    return True

# ======================== DROPBOX ========================

def upload_to_dropbox(image_url, filename):
    """Загружает изображение в Dropbox и возвращает публичную ссылку"""
    try:
        # Скачиваем изображение
        img_response = requests.get(image_url, timeout=15)
        if img_response.status_code != 200:
            print(f"❌ Не удалось скачать изображение: {img_response.status_code}")
            return None
        
        image_data = img_response.content
        
        # Загружаем в Dropbox
        dropbox_path = f"/receipts/{filename}"
        upload_url = "https://content.dropboxapi.com/2/files/upload"
        
        headers = {
            "Authorization": f"Bearer {DROPBOX_ACCESS_TOKEN}",
            "Content-Type": "application/octet-stream",
            "Dropbox-API-Arg": json.dumps({
                "path": dropbox_path,
                "mode": "add",
                "autorename": True,
                "mute": False
            })
        }
        
        upload_response = requests.post(upload_url, headers=headers, data=image_data, timeout=30)
        
        if upload_response.status_code != 200:
            print(f"❌ Ошибка загрузки в Dropbox: {upload_response.status_code}")
            return None
        
        # Создаем публичную ссылку
        share_url = "https://api.dropboxapi.com/2/sharing/create_shared_link_with_settings"
        share_headers = {
            "Authorization": f"Bearer {DROPBOX_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        share_data = {
            "path": dropbox_path,
            "settings": {
                "requested_visibility": "public"
            }
        }
        
        share_response = requests.post(share_url, headers=share_headers, json=share_data, timeout=10)
        
        if share_response.status_code == 200:
            share_data = share_response.json()
            shared_link = share_data.get("url", "")
            # Конвертируем в прямую ссылку
            direct_link = shared_link.replace("www.dropbox.com", "dl.dropboxusercontent.com").replace("?dl=0", "")
            print(f"✅ Файл загружен в Dropbox: {direct_link}")
            return direct_link
        elif share_response.status_code == 409:
            # Ссылка уже существует, получаем её
            list_url = "https://api.dropboxapi.com/2/sharing/list_shared_links"
            list_data = {"path": dropbox_path}
            list_response = requests.post(list_url, headers=share_headers, json=list_data, timeout=10)
            
            if list_response.status_code == 200:
                links = list_response.json().get("links", [])
                if links:
                    shared_link = links[0].get("url", "")
                    direct_link = shared_link.replace("www.dropbox.com", "dl.dropboxusercontent.com").replace("?dl=0", "")
                    print(f"✅ Используется существующая ссылка: {direct_link}")
                    return direct_link
        
        print(f"❌ Не удалось создать публичную ссылку")
        return None
        
    except Exception as e:
        print(f"❌ Ошибка при работе с Dropbox: {e}")
        return None

# ======================== AIRTABLE ========================

def create_airtable_record(order_data):
    """Создает запись в Airtable"""
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json"
    }
    
    fields = {
        "Customer_Info": f"{order_data.get('customer_name', 'Клиент')}, {order_data['phone']}",
        "Order_Details": "\n".join(order_data.get('order_items', [])),
        "Total_Price": int(order_data.get('total_price', 0)),
        "Delivery_Address": order_data.get('delivery_address', ''),
        "Is_Paid": False,
        "Kitchen_Status": "Waiting",
        "Payment_Receipt": order_data.get('payment_receipt', [])
    }
    
    data = {"fields": fields}
    
    try:
        r = requests.post(url, headers=headers, json=data, timeout=10)
        if r.status_code in [200, 201]:
            response_data = r.json()
            record_id = response_data['id']
            print(f"✅ Запись создана в Airtable: {record_id}")
            return record_id
        else:
            print(f"❌ Ошибка Airtable: {r.status_code} - {r.text}")
            return None
    except Exception as e:
        print(f"❌ Ошибка при создании записи: {e}")
        return None

def upload_receipt_to_airtable(record_id, image_url):
    """Загружает чек через Dropbox и сохраняет ссылку в Airtable"""
    
    # Генерируем уникальное имя файла
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"receipt_{record_id}_{timestamp}.jpg"
    
    # Загружаем в Dropbox
    dropbox_url = upload_to_dropbox(image_url, filename)
    
    if not dropbox_url:
        return False
    
    # Сохраняем ссылку в Airtable
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}/{record_id}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json"
    }
    
    fields = {
        "Payment_Receipt": [{"url": dropbox_url}]
    }
    
    data = {"fields": fields}
    
    try:
        r = requests.patch(url, headers=headers, json=data, timeout=10)
        if r.status_code == 200:
            print(f"✅ Ссылка на чек сохранена в Airtable")
            return True
        else:
            print(f"❌ Ошибка сохранения ссылки в Airtable: {r.status_code}")
            return False
    except Exception as e:
        print(f"❌ Ошибка при сохранении ссылки: {e}")
        return False

def get_airtable_record(record_id):
    """Получает запись из Airtable"""
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}/{record_id}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}"
    }
    
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception as e:
        print(f"❌ Ошибка получения записи: {e}")
        return None

def check_paid_orders():
    """Проверяет оплаченные заказы и отправляет их на кухню"""
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}"
    }
    
    # Фильтр: Is_Paid = true И Kitchen_Status = Waiting
    params = {
        "filterByFormula": "AND({Is_Paid}=TRUE(), {Kitchen_Status}='Waiting')"
    }
    
    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            records = data.get('records', [])
            
            for record in records:
                record_id = record['id']
                
                # Пропускаем уже обработанные
                if record_id in checked_records:
                    continue
                
                fields = record['fields']
                
                # Формируем данные заказа
                order_data = {
                    'record_id': record_id,
                    'order_id': fields.get('ID', 'N/A'),
                    'customer_info': fields.get('Customer_Info', ''),
                    'order_items': fields.get('Order_Details', '').split('\n'),
                    'total_price': fields.get('Total_Price', 0),
                    'delivery_address': fields.get('Delivery_Address', ''),
                    'payment_receipt': fields.get('Payment_Receipt', [])
                }
                
                # Отправляем на кухню
                if send_to_kitchen(order_data):
                    # Обновляем статус на "Cooking"
                    update_kitchen_status(record_id, "Cooking")
                    checked_records.add(record_id)
                    print(f"✅ Заказ #{order_data['order_id']} отправлен на кухню")
                
    except Exception as e:
        print(f"❌ Ошибка проверки заказов: {e}")

def update_kitchen_status(record_id, status):
    """Обновляет статус заказа на кухне"""
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}/{record_id}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "fields": {
            "Kitchen_Status": status
        }
    }
    
    try:
        r = requests.patch(url, headers=headers, json=data, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"❌ Ошибка обновления статуса: {e}")
        return False

# ======================== TELEGRAM ========================

def send_to_kitchen(order_data):
    """Отправляет красиво оформленный заказ всем сотрудникам кухни"""
    
    # Форматируем состав заказа
    order_items_text = ""
    for item in order_data.get('order_items', []):
        if item.strip():
            order_items_text += f"  • {item}\n"
    
    # Извлекаем телефон из Customer_Info
    customer_info = order_data.get('customer_info', '')
    phone = customer_info.split(', ')[-1] if ', ' in customer_info else 'Не указан'
    
    # Красиво оформленное сообщение
    message = f"""
🔔 <b>НОВЫЙ ЗАКАЗ</b> 🔔

━━━━━━━━━━━━━━━━━━━
📋 <b>Заказ №{order_data.get('order_id', 'N/A')}</b>
━━━━━━━━━━━━━━━━━━━

🍔 <b>СОСТАВ ЗАКАЗА:</b>
{order_items_text}
━━━━━━━━━━━━━━━━━━━

💰 <b>Сумма:</b> {order_data.get('total_price', 0):,}₸
✅ <b>Оплата:</b> Подтверждена

📍 <b>АДРЕС ДОСТАВКИ:</b>
{order_data.get('delivery_address', 'Не указан')}

📞 <b>Телефон:</b> {phone}

━━━━━━━━━━━━━━━━━━━
⏰ <b>Время:</b> {datetime.now().strftime('%H:%M')}
━━━━━━━━━━━━━━━━━━━
"""
    
    success = False
    
    # Отправляем всем сотрудникам кухни
    for staff_id in KITCHEN_STAFF_IDS:
        if send_telegram_message(staff_id, message):
            success = True
            
            # Если есть чек - отправляем и его
            receipts = order_data.get('payment_receipt', [])
            if receipts and len(receipts) > 0:
                receipt_url = receipts[0].get('url') if isinstance(receipts[0], dict) else None
                if receipt_url:
                    send_telegram_photo(staff_id, receipt_url, "📸 Чек оплаты")
    
    return success

def send_telegram_message(chat_id, text):
    """Отправляет текстовое сообщение в Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    
    try:
        r = requests.post(url, json=data, timeout=10)
        if r.status_code == 200:
            return True
        else:
            print(f"❌ Ошибка отправки в Telegram ({chat_id}): {r.status_code}")
            return False
    except Exception as e:
        print(f"❌ Ошибка Telegram: {e}")
        return False

def send_telegram_photo(chat_id, photo_url, caption):
    """Отправляет фото в Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    data = {
        "chat_id": chat_id,
        "photo": photo_url,
        "caption": caption
    }
    
    try:
        r = requests.post(url, json=data, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"❌ Ошибка отправки фото: {e}")
        return False

# ======================== WHATSAPP ========================

def send_message(to, text):
    url = "https://gate.whapi.cloud/messages/text"
    headers = {"accept": "application/json", "content-type": "application/json"}
    params = {"token": WHATSAPP_TOKEN}
    data = {"to": to, "body": text}
    try:
        r = requests.post(url, params=params, headers=headers, json=data, timeout=10)
        return r.status_code in [200, 201]
    except:
        return False

def send_typing(to):
    url = "https://gate.whapi.cloud/messages/typing"
    headers = {"accept": "application/json", "content-type": "application/json"}
    params = {"token": WHATSAPP_TOKEN}
    data = {"to": to, "duration": 2}
    try:
        requests.post(url, params=params, headers=headers, json=data, timeout=5)
    except:
        pass

def download_whatsapp_media(media_id):
    """Скачивает медиафайл из WhatsApp"""
    url = f"https://gate.whapi.cloud/messages/{media_id}/media"
    params = {"token": WHATSAPP_TOKEN}
    headers = {"accept": "application/json"}
    
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        if r.status_code == 200:
            data = r.json()
            return data.get("media_url")
        return None
    except Exception as e:
        print(f"❌ Ошибка скачивания медиа: {e}")
        return None

# ======================== AI ========================

def get_ai_response(user_phone, user_message):
    conv = remember_user_message_only(user_phone, user_message)
    
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + conv["messages"]
    
    response = client.chat.completions.create(
        model="sonar-pro",
        messages=messages,
        temperature=0.7,
        max_tokens=600,
        extra_body={"disable_search": True}
    )
    
    ai_reply = response.choices[0].message.content
    
    # Проверяем наличие JSON с подтверждением заказа
    json_match = re.search(r'```json\s*(\{.*?\})\s*```', ai_reply, re.DOTALL)
    if json_match:
        try:
            order_data = json.loads(json_match.group(1))
            if order_data.get("order_confirmed"):
                # Сохраняем заказ в память, но НЕ создаем запись в Airtable пока нет чека
                conv["pending_order"] = order_data
                conv["waiting_for_receipt"] = True
                save_conversations(conversations)
                
                # Запускаем таймер напоминания
                start_payment_reminder(user_phone)
            
            # Удаляем JSON из ответа пользователю
            ai_reply = re.sub(r'```json\s*\{.*?\}\s*```', '', ai_reply, flags=re.DOTALL).strip()
        except json.JSONDecodeError:
            pass
    
    conv["messages"].append({"role": "assistant", "content": ai_reply})
    conv["messages"] = conv["messages"][-20:]
    
    return ai_reply

# ======================== PAYMENT REMINDER ========================

def start_payment_reminder(user_phone):
    """Запускает таймер напоминания об оплате"""
    def remind():
        time.sleep(900)  # 15 минут
        conv = conversations.get(user_phone)
        if conv and conv.get("waiting_for_receipt"):
            reminder_text = """
⏰ Напоминание об оплате

Мы еще не получили скриншот чека оплаты.
Пожалуйста, пришлите фото чека, чтобы мы начали готовить ваш заказ.

Если возникли вопросы - напишите нам!
"""
            send_message(user_phone, reminder_text)
            print(f"⏰ Отправлено напоминание для {user_phone}")
    
    reminder_thread = threading.Thread(target=remind, daemon=True)
    reminder_thread.start()

# ======================== POLLING ========================

def poll_messages():
    url = "https://gate.whapi.cloud/messages/list"
    headers = {"accept": "application/json"}
    params = {
        "token": WHATSAPP_TOKEN,
        "count": 20,
        "time_from": LAUNCH_TIMESTAMP
    }
    
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        if r.status_code != 200:
            return
        
        data = r.json()
        
        # Словарь для группировки текстовых сообщений: {user_phone: [text1, text2]}
        pending_texts = {}
        
        for msg in reversed(data.get("messages", [])):
            if msg.get("from_me"):
                continue
            
            msg_id = msg.get("id")
            if not mark_message_processed(msg_id):
                continue
            
            user_phone = msg.get("chat_id")
            if user_phone.endswith("@g.us"):
                continue
            
            conv = ensure_conversation(user_phone)
            
            # Обработка документов (PDF чеков)
            if msg.get("type") == "document" and conv.get("waiting_for_receipt"):
                media_id = msg.get("id")
                media_url = download_whatsapp_media(media_id)
                
                if media_url and conv.get("pending_order"):
                    # Загружаем в Dropbox
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    filename = f"receipt_{user_phone}_{timestamp}.pdf"
                    dropbox_url = upload_to_dropbox(media_url, filename)
                    
                    if dropbox_url:
                        # Добавляем чек к заказу
                        conv["pending_order"]["payment_receipt"] = [{"url": dropbox_url}]
                        
                        # Теперь создаем запись в Airtable
                        record_id = create_airtable_record(conv["pending_order"])
                        
                        if record_id:
                            conv["waiting_for_receipt"] = False
                            conv["order_placed"] = True
                            conv["airtable_record_id"] = record_id
                            conv["pending_order"] = None # Очищаем
                            save_conversations(conversations)
                            
                            send_message(user_phone, 
                                "✅ Чек получен! Заказ оформлен и передан на кухню.\n"
                                "Ожидайте доставку!")
                        else:
                            send_message(user_phone, "❌ Ошибка при оформлении заказа. Попробуйте позже.")
                    else:
                        send_message(user_phone, 
                            "❌ Ошибка загрузки чека. Попробуйте еще раз.")
            elif msg.get("type") in ["document", "image"]:
                print(f"⚠️ Игнорирую документ от {user_phone}: не ждем чек (waiting_for_receipt={conv.get('waiting_for_receipt')})")
            
            # Обработка текстовых сообщений - собираем в список
            elif msg.get("type") == "text":
                text = msg.get("text", {}).get("body", "")
                if user_phone not in pending_texts:
                    pending_texts[user_phone] = []
                pending_texts[user_phone].append(text)
        
        # Обрабатываем сгруппированные сообщения
        for user_phone, texts in pending_texts.items():
            # Объединяем сообщения через перенос строки
            full_text = "\n".join(texts)
            
            send_typing(user_phone)
            reply = get_ai_response(user_phone, full_text)
            send_message(user_phone, reply)
                
    except Exception as e:
        print(f"Ошибка при получении сообщений: {e}")

# ======================== BACKGROUND TASKS ========================

def background_checker():
    """Фоновая проверка оплаченных заказов каждые 10 секунд"""
    while True:
        try:
            check_paid_orders()
            time.sleep(10)  # Проверка каждые 10 секунд
        except Exception as e:
            print(f"Ошибка в фоновом чекере: {e}")
            time.sleep(10)

# ======================== START ========================

if __name__ == "__main__":
    print("🚀 AI-Доставка запущена")
    print(f"📅 Timestamp: {LAUNCH_TIMESTAMP}")
    print(f"📊 Airtable Base: {AIRTABLE_BASE_ID}")
    print(f"👨‍🍳 Сотрудников кухни: {len(KITCHEN_STAFF_IDS)}")
    print(f"💬 Telegram Bot активен")
    print(f"📦 Dropbox интеграция активна")
    print("━" * 50)
    
    # Запускаем фоновый чекер заказов
    checker_thread = threading.Thread(target=background_checker, daemon=True)
    checker_thread.start()
    print("✅ Фоновый чекер заказов запущен")
    
    while True:
        try:
            poll_messages()
            time.sleep(3)
        except KeyboardInterrupt:
            print("\n👋 Система остановлена")
            break
        except Exception as e:
            print(f"Критическая ошибка: {e}")
            time.sleep(5)
