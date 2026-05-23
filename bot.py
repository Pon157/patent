"""
Бот: Патент КМБП (Премиум + ID Админка)
Версия: 2.0 (Расширенная)
Функционал: Регистрация патентов (Идея/Название), генерация сертификатов, 
админ-панель, система банов, FSM хранилище.
"""

import os
import sqlite3
import random
import string
import urllib.parse
import logging
from datetime import datetime
from io import BytesIO

import telebot
from telebot import custom_filters
from telebot.handler_backends import State, StatesGroup
from telebot.storage import StateMemoryStorage
from telebot import apihelper
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

# --- 1. КОНФИГУРАЦИЯ И ЛОГИРОВАНИЕ ---
load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BOT_TOKEN = os.getenv("BOT_TOKEN")
PROXY_URL = os.getenv("PROXY_URL")
ADMIN_ID = os.getenv("ADMIN_ID")

if not BOT_TOKEN:
    raise ValueError("ОШИБКА: BOT_TOKEN не найден в .env файле!")

try:
    ADMIN_ID = int(ADMIN_ID) if ADMIN_ID else 0
except ValueError:
    ADMIN_ID = 0

# Настройка прокси
if PROXY_URL:
    apihelper.proxy = {'https': PROXY_URL, 'http': PROXY_URL}

# Инициализация бота
state_storage = StateMemoryStorage()
bot = telebot.TeleBot(BOT_TOKEN, state_storage=state_storage, parse_mode="HTML")

# --- 2. БАЗА ДАННЫХ И ХЕЛПЕРЫ ---
DB_NAME = "patents.db"

def init_db():
    """Инициализация базы данных и создание таблиц."""
    logging.info("Инициализация базы данных...")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Таблица патентов с новым полем patent_type
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS patents (
            patent_number TEXT PRIMARY KEY,
            user_id INTEGER,
            name TEXT,
            username TEXT,
            patent_type TEXT,
            project_name TEXT,
            project_link TEXT,
            proof TEXT,
            management_links TEXT,
            permission TEXT,
            date_created TEXT,
            cert_file_id TEXT
        )
    ''')
    # Таблица заблокированных
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS banned_users (
            user_id INTEGER PRIMARY KEY
        )
    ''')
    conn.commit()
    conn.close()
    logging.info("База данных готова.")

init_db()

def is_user_banned(user_id):
    """Проверка статуса бана пользователя."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM banned_users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return bool(result)

# --- 3. ЭМОДЗИ И СТИЛИЗАЦИЯ ---
def p_emoji(emoji_char, emoji_id):
    return f'<tg-emoji emoji-id="{emoji_id}">{emoji_char}</tg-emoji>'

E = {
    "star": p_emoji("⭐️", "5438496463044752972"),
    "success": p_emoji("✔️", "5416076321442777828"),
    "error": p_emoji("❌", "5456302074604035284"),
    "warn": p_emoji("⚠️", "5240241223632954241"),
    "flash": p_emoji("⚡️", "5456606106748983383"),
    "eyes": p_emoji("👀", "5240241223632954241"),
    "chart": p_emoji("📈", "5325547803936572038"),
    "arrow": p_emoji("➡️", "5244837092042750681"),
    "green": p_emoji("🟢", "5416081784641168838"),
    "sparkles": p_emoji("✨", "5438496463044752972"),
    "rainbow": p_emoji("🌈", "5409109841538994759"),
    "calendar": p_emoji("🗓", "5413879192267805083"),
    "siren": p_emoji("🚨", "5395695537687123235"),
    "pencil": p_emoji("✏️", "5395444784611480792"),
    "shield": p_emoji("🛡", "5438496463044752972"),
    "ban": p_emoji("⛔️", "5456302074604035284")
}

# Расширенная кнопка
class StyledInlineKeyboardButton(telebot.types.InlineKeyboardButton):
    def __init__(self, text, style=None, icon_custom_emoji_id=None, **kwargs):
        super().__init__(text, **kwargs)
        self.style = style
        self.icon_custom_emoji_id = icon_custom_emoji_id

    def to_dict(self):
        d = super().to_dict()
        if self.style: d['style'] = self.style
        if self.icon_custom_emoji_id: d['icon_custom_emoji_id'] = self.icon_custom_emoji_id
        return d

BTN_E_DANGER = "5310169226856644648"
BTN_E_SUCCESS = "5310076249404621168"
BTN_E_PRIMARY = "5285430309720966085"

# --- 4. FSM СОСТОЯНИЯ ---
class PatentStates(StatesGroup):
    patent_type = State()
    name = State()
    username = State()
    project_name = State()
    project_link = State()
    proof = State()
    management_links = State()
    check_patent_number = State()

class AdminStates(StatesGroup):
    delete_patent = State()
    ban_user = State()
    unban_user = State()

# --- 5. ГЕНЕРАЦИЯ СЕРТИФИКАТА ---
def generate_certificate(patent_number, name, project_name, patent_type, date_str):
    """Генерация изображения сертификата с помощью Pillow."""
    logging.info(f"Генерация сертификата для {patent_number}")
    
    # Создаем холст
    img = Image.new('RGB', (1000, 700), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    # Загрузка шрифтов (проверка доступности)
    font_title = font_text = font_highlight = None
    fonts_to_try = ["arial.ttf", "Ubuntu-R.ttf", "DejaVuSans.ttf", "FreeSans.ttf", "tahoma.ttf"]
    
    for f in fonts_to_try:
        try:
            font_title = ImageFont.truetype(f, 60)
            font_text = ImageFont.truetype(f, 30)
            font_highlight = ImageFont.truetype(f, 35)
            break
        except Exception:
            continue

    if not font_title:
        font_title = ImageFont.load_default()
        font_text = ImageFont.load_default()
        font_highlight = ImageFont.load_default()

    # Рисование рамки
    draw.rectangle([20, 20, 980, 680], outline=(70, 130, 180), width=10)
    draw.rectangle([40, 40, 960, 660], outline=(100, 149, 237), width=3)

    # Текст сертификата
    draw.text((500, 100), "ПАТЕНТ КМБП", font=font_title, fill=(25, 25, 112), anchor="mm")
    
    # Новое поле: Тип патента
    draw.text((500, 170), f"Тип регистрации: {patent_type}", font=font_text, fill=(105, 105, 105), anchor="mm")

    y_start = 280
    spacing = 60
    
    # Информация
    fields = [
        ("Владелец:", name),
        ("Проект:", project_name),
        ("Номер:", patent_number),
        ("Дата выдачи:", date_str)
    ]
    
    for i, (label, val) in enumerate(fields):
        draw.text((100, y_start + (i * spacing)), label, font=font_text, fill=(0, 0, 0))
        draw.text((400, y_start + (i * spacing)), str(val), font=font_highlight, fill=(25, 25, 112))

    bio = BytesIO()
    img.save(bio, format='PNG')
    bio.seek(0)
    return bio

# --- 6. ОБРАБОТЧИКИ СООБЩЕНИЙ ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    if is_user_banned(message.from_user.id):
        bot.send_message(message.chat.id, f"{E['ban']} Вы заблокированы.")
        return

    markup = telebot.types.InlineKeyboardMarkup()
    markup.row(
        StyledInlineKeyboardButton("Получить патент", callback_data="start_patent", style="primary", icon_custom_emoji_id=BTN_E_PRIMARY),
        StyledInlineKeyboardButton("Проверить", callback_data="check_patent", style="success", icon_custom_emoji_id=BTN_E_SUCCESS)
    )
    markup.row(
        StyledInlineKeyboardButton("Мои патенты", callback_data="my_patents", style="primary", icon_custom_emoji_id=BTN_E_PRIMARY),
        StyledInlineKeyboardButton("Последние", callback_data="recent_patents", style="primary", icon_custom_emoji_id=BTN_E_PRIMARY)
    )
    markup.row(
        StyledInlineKeyboardButton("Помощь", callback_data="help", style="primary", icon_custom_emoji_id=BTN_E_PRIMARY)
    )
    
    bot.send_message(
        message.chat.id, 
        f"{E['star']} <b>Добро пожаловать в систему КМБП!</b>\n\n"
        f"Ваш персональный регистратор идей и названий.",
        reply_markup=markup
    )

@bot.message_handler(commands=['help'])
def help_cmd(message):
    bot.send_message(message.chat.id, "Краткая справка:\n1. Получить патент - регистрация.\n2. Проверить - поиск по номеру.\n3. Мои - ваши записи.")

# --- 7. АДМИН-ПАНЕЛЬ ---
@bot.message_handler(commands=['adminka'])
def admin_start(message):
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, f"{E['error']} Доступ запрещен.")
        return
    show_admin_panel(message.chat.id)

def show_admin_panel(chat_id, message_id=None):
    markup = telebot.types.InlineKeyboardMarkup()
    markup.row(
        StyledInlineKeyboardButton("Статистика", callback_data="adm_stats", style="primary", icon_custom_emoji_id=BTN_E_PRIMARY),
        StyledInlineKeyboardButton("Удалить", callback_data="adm_delete", style="danger", icon_custom_emoji_id=BTN_E_DANGER)
    )
    markup.row(
        StyledInlineKeyboardButton("Забанить", callback_data="adm_ban", style="danger", icon_custom_emoji_id=BTN_E_DANGER),
        StyledInlineKeyboardButton("Разбанить", callback_data="adm_unban", style="success", icon_custom_emoji_id=BTN_E_SUCCESS)
    )
    markup.row(StyledInlineKeyboardButton("Назад", callback_data="back_main", style="primary", icon_custom_emoji_id=BTN_E_PRIMARY))
    
    text = f"{E['shield']} <b>Панель управления администратора</b>"
    if message_id: bot.edit_message_text(text, chat_id, message_id, reply_markup=markup)
    else: bot.send_message(chat_id, text, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_"))
def admin_callbacks(call):
    if call.from_user.id != ADMIN_ID: return
    
    if call.data == "adm_stats":
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM patents")
        total = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM banned_users")
        banned = c.fetchone()[0]
        conn.close()
        bot.answer_callback_query(call.id, f"Патентов: {total} | Забанено: {banned}", show_alert=True)
    
    elif call.data == "adm_delete":
        bot.set_state(call.from_user.id, AdminStates.delete_patent, call.message.chat.id)
        bot.edit_message_text("Введите номер патента (например, KMBP-12345):", call.message.chat.id, call.message.message_id)

    elif call.data == "adm_ban":
        bot.set_state(call.from_user.id, AdminStates.ban_user, call.message.chat.id)
        bot.edit_message_text("Введите ID пользователя для бана:", call.message.chat.id, call.message.message_id)

    elif call.data == "adm_unban":
        bot.set_state(call.from_user.id, AdminStates.unban_user, call.message.chat.id)
        bot.edit_message_text("Введите ID пользователя для разбана:", call.message.chat.id, call.message.message_id)
        
    elif call.data == "adm_back":
        show_admin_panel(call.message.chat.id, call.message.message_id)

@bot.message_handler(state=AdminStates.delete_patent)
def delete_patent_proc(message):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM patents WHERE patent_number = ?", (message.text.strip().upper(),))
    conn.commit()
    conn.close()
    bot.delete_state(message.from_user.id, message.chat.id)
    bot.send_message(message.chat.id, f"{E['success']} Операция выполнена.")
    show_admin_panel(message.chat.id)

@bot.message_handler(state=AdminStates.ban_user)
def ban_proc(message):
    try:
        uid = int(message.text.strip())
        conn = sqlite3.connect(DB_NAME)
        conn.cursor().execute("INSERT OR IGNORE INTO banned_users VALUES (?)", (uid,))
        conn.commit()
        conn.close()
        bot.send_message(message.chat.id, "Пользователь заблокирован.")
    except:
        bot.send_message(message.chat.id, "Ошибка ID.")
    bot.delete_state(message.from_user.id, message.chat.id)
    show_admin_panel(message.chat.id)

@bot.message_handler(state=AdminStates.unban_user)
def unban_proc(message):
    try:
        uid = int(message.text.strip())
        conn = sqlite3.connect(DB_NAME)
        conn.cursor().execute("DELETE FROM banned_users WHERE user_id = ?", (uid,))
        conn.commit()
        conn.close()
        bot.send_message(message.chat.id, "Пользователь разблокирован.")
    except:
        bot.send_message(message.chat.id, "Ошибка ID.")
    bot.delete_state(message.from_user.id, message.chat.id)
    show_admin_panel(message.chat.id)

# --- 8. ПРОЦЕСС РЕГИСТРАЦИИ (ANKETA) ---
@bot.callback_query_handler(func=lambda call: call.data == "start_patent")
def start_reg(call):
    # Выбор типа патента
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(
        telebot.types.InlineKeyboardButton("Идея", callback_data="type_Idea"),
        telebot.types.InlineKeyboardButton("Название", callback_data="type_Name")
    )
    bot.edit_message_text("Выберите, что вы регистрируете:", call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("type_"))
def set_type(call):
    p_type = call.data.split("_")[1]
    bot.add_data(call.from_user.id, call.message.chat.id, patent_type=p_type)
    bot.set_state(call.from_user.id, PatentStates.name, call.message.chat.id)
    bot.edit_message_text("Введите ваше ФИО:", call.message.chat.id, call.message.message_id)

@bot.message_handler(state=PatentStates.name)
def ask_username(message):
    bot.add_data(message.from_user.id, message.chat.id, name=message.text)
    bot.set_state(message.from_user.id, PatentStates.username, message.chat.id)
    bot.send_message(message.chat.id, "Введите ваш юзернейм (@username):")

@bot.message_handler(state=PatentStates.username)
def ask_proj_name(message):
    bot.add_data(message.from_user.id, message.chat.id, username=message.text)
    bot.set_state(message.from_user.id, PatentStates.project_name, message.chat.id)
    bot.send_message(message.chat.id, "Введите название проекта:")

@bot.message_handler(state=PatentStates.project_name)
def ask_proj_link(message):
    bot.add_data(message.from_user.id, message.chat.id, project_name=message.text)
    bot.set_state(message.from_user.id, PatentStates.project_link, message.chat.id)
    bot.send_message(message.chat.id, "Введите ссылку на проект:")

@bot.message_handler(state=PatentStates.project_link)
def ask_proof(message):
    bot.add_data(message.from_user.id, message.chat.id, project_link=message.text)
    bot.set_state(message.from_user.id, PatentStates.proof, message.chat.id)
    bot.send_message(message.chat.id, "Докажите авторство (описание или текст):")

@bot.message_handler(state=PatentStates.proof)
def ask_management(message):
    bot.add_data(message.from_user.id, message.chat.id, proof=message.text)
    bot.set_state(message.from_user.id, PatentStates.management_links, message.chat.id)
    bot.send_message(message.chat.id, "Ссылки на руководство (через запятую):")

@bot.message_handler(state=PatentStates.management_links)
def ask_perm(message):
    bot.add_data(message.from_user.id, message.chat.id, management_links=message.text)
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(
        telebot.types.InlineKeyboardButton("Да, разрешаю", callback_data="perm_yes"),
        telebot.types.InlineKeyboardButton("Нет, запрещаю", callback_data="perm_no")
    )
    bot.send_message(message.chat.id, "Разрешаете ли вы использование другим проектам?", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data in ["perm_yes", "perm_no"])
def finish_reg(call):
    perm = "Да" if call.data == "perm_yes" else "Нет"
    
    with bot.retrieve_data(call.from_user.id, call.message.chat.id) as data:
        p_type = data['patent_type']
        name = data['name']
        username = data['username']
        proj_name = data['project_name']
        proj_link = data['project_link']
        proof = data['proof']
        m_links = data['management_links']

    # Генерация ID
    p_num = "KMBP-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=7))
    date_now = datetime.now().strftime("%d.%m.%Y")

    # Генерация изображения
    cert_img = generate_certificate(p_num, name, proj_name, p_type, date_now)

    # Сохранение в базу
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO patents 
        (patent_number, user_id, name, username, patent_type, project_name, project_link, proof, management_links, permission, date_created, cert_file_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (p_num, call.from_user.id, name, username, p_type, proj_name, proj_link, proof, m_links, perm, date_now, "none"))
    conn.commit()
    conn.close()

    bot.delete_state(call.from_user.id, call.message.chat.id)
    
    # Ответ
    caption = f"✅ <b>Патент выдан!</b>\n\nТип: {p_type}\nПроект: {proj_name}\nID: {p_num}"
    bot.send_photo(call.message.chat.id, cert_img, caption=caption)
    bot.send_message(call.message.chat.id, "Вернуться в меню: /start")

# --- 9. ПРОВЕРКА ПАТЕНТОВ ---
@bot.callback_query_handler(func=lambda call: call.data == "check_patent")
def ask_check(call):
    bot.set_state(call.from_user.id, PatentStates.check_patent_number, call.message.chat.id)
    bot.edit_message_text("Введите номер патента (например, KMBP-XXXXXXX):", call.message.chat.id, call.message.message_id)

@bot.message_handler(state=PatentStates.check_patent_number)
def check_logic(message):
    num = message.text.strip().upper()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT patent_type, project_name, name, date_created FROM patents WHERE patent_number = ?", (num,))
    row = c.fetchone()
    conn.close()
    
    if row:
        txt = f"🔎 <b>Результат проверки</b>\n\nНомер: {num}\nТип: {row[0]}\nПроект: {row[1]}\nВладелец: {row[2]}\nДата: {row[3]}"
        bot.send_message(message.chat.id, txt)
    else:
        bot.send_message(message.chat.id, "Патент не найден.")
    
    bot.delete_state(message.from_user.id, message.chat.id)

@bot.callback_query_handler(func=lambda call: call.data == "my_patents")
def my_patents(call):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT patent_number, project_name FROM patents WHERE user_id = ?", (call.from_user.id,))
    rows = c.fetchall()
    conn.close()
    
    if not rows:
        bot.answer_callback_query(call.id, "У вас нет патентов.", show_alert=True)
        return
        
    text = "📂 <b>Ваши патенты:</b>\n"
    for r in rows:
        text += f"• {r[1]} (<code>{r[0]}</code>)\n"
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=telebot.types.InlineKeyboardMarkup().add(
        telebot.types.InlineKeyboardButton("Назад", callback_data="back_main")
    ))

@bot.callback_query_handler(func=lambda call: call.data == "recent_patents")
def recent_patents(call):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT patent_number, project_name FROM patents ORDER BY rowid DESC LIMIT 5")
    rows = c.fetchall()
    conn.close()
    
    text = "✨ <b>Последние 5 регистраций:</b>\n"
    for r in rows:
        text += f"• {r[1]} (<code>{r[0]}</code>)\n"
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=telebot.types.InlineKeyboardMarkup().add(
        telebot.types.InlineKeyboardButton("Назад", callback_data="back_main")
    ))

@bot.callback_query_handler(func=lambda call: call.data == "back_main")
def back_main(call):
    bot.delete_state(call.from_user.id, call.message.chat.id)
    send_welcome(call.message)

# --- 10. ЗАПУСК ---
if __name__ == "__main__":
    logging.info("Бот запущен.")
    bot.add_custom_filter(custom_filters.StateFilter(bot))
    try:
        bot.infinity_polling()
    except Exception as e:
        logging.error(f"Критическая ошибка: {e}")

