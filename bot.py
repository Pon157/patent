import os
import sqlite3
import random
import string
import urllib.parse
from datetime import datetime
from io import BytesIO

import telebot
from telebot import custom_filters
from telebot.handler_backends import State, StatesGroup
from telebot.storage import StateMemoryStorage
from telebot import apihelper
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

# --- 1. ЗАГРУЗКА НАСТРОЕК И ПРОКСИ ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
PROXY_URL = os.getenv("PROXY_URL")
ADMIN_ID = os.getenv("ADMIN_ID")

if not BOT_TOKEN:
    raise ValueError("Пожалуйста, добавьте BOT_TOKEN в файл .env")

# Безопасное преобразование ADMIN_ID в число
try:
    ADMIN_ID = int(ADMIN_ID) if ADMIN_ID else 0
except ValueError:
    ADMIN_ID = 0

if PROXY_URL:
    apihelper.proxy = {'https': PROXY_URL, 'http': PROXY_URL}

state_storage = StateMemoryStorage()
bot = telebot.TeleBot(BOT_TOKEN, state_storage=state_storage)

# --- 2. БАЗА ДАННЫХ ---
DB_NAME = "patents.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Таблица патентов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS patents (
            patent_number TEXT PRIMARY KEY,
            user_id INTEGER,
            name TEXT,
            username TEXT,
            project_name TEXT,
            project_link TEXT,
            proof TEXT,
            management_links TEXT,
            permission TEXT,
            patent_type TEXT,        -- <--- ЭТА СТРОКА НОВАЯ
            date_created TEXT,
            cert_file_id TEXT
        )
    ''')
    # Таблица заблокированных пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS banned_users (
            user_id INTEGER PRIMARY KEY
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# Проверка, забанен ли пользователь
def is_user_banned(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM banned_users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return bool(result)

# --- 3. ПРЕМИУМ КНОПКИ И ЭМОДЗИ ---
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
    name = State()
    username = State()
    patent_type = State()
    project_name = State()
    project_link = State()
    proof = State()
    management_links = State()
    check_patent_number = State()
    patent_content = State()

class AdminStates(StatesGroup):
    delete_patent = State()
    ban_user = State()
    unban_user = State()

# --- 5. ГЕНЕРАЦИЯ СЕРТИФИКАТА ---
def generate_certificate(patent_number, name, project_name, patent_type, date_str):
    img = Image.new('RGB', (1000, 700), color=(240, 248, 255))
    draw = ImageDraw.Draw(img)

    # Используем стандартный шрифт, если кастомные не найдены
    font_title = ImageFont.load_default()
    font_text = ImageFont.load_default()
    font_highlight = ImageFont.load_default()

    # Попытка загрузки шрифта
    try:
        font_title = ImageFont.truetype("arial.ttf", 60)
        font_text = ImageFont.truetype("arial.ttf", 30)
        font_highlight = ImageFont.truetype("arial.ttf", 35)
    except: pass

    # Рамки
    draw.rectangle([30, 30, 970, 670], outline=(70, 130, 180), width=15)
    
    # Заголовок (по центру)
    draw.text((500, 100), "ПАТЕНТ КМБП", font=font_title, fill=(25, 25, 112), anchor="mm")

    # Текст (по левому краю)
    x_start = 100
    y = 200
    step = 50

    def draw_line(label, value, y_pos):
        draw.text((x_start, y_pos), label, font=font_text, fill=(100, 100, 100))
        draw.text((x_start + 300, y_pos), value, font=font_highlight, fill=(0, 0, 0))

    draw_line("Владелец:", name, y)
    draw_line("Проект:", project_name, y + step)
    draw_line("Тип патента:", patent_type, y + step*2) # НОВОЕ ПОЛЕ
    draw_line("Номер:", patent_number, y + step*3)
    draw_line("Дата:", date_str, y + step*4)

    bio = BytesIO()
    img.save(bio, format='PNG')
    bio.seek(0)
    return bio

# --- 6. ОСНОВНОЕ МЕНЮ ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    if is_user_banned(message.from_user.id):
        bot.send_message(message.chat.id, f"{E['ban']} Вы заблокированы и не можете использовать этого бота.")
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
    
    bot.send_message(
        message.chat.id, 
        f"{E['star']} <b>Добро пожаловать в систему Патент КМБП!</b>\n\n"
        f"Здесь вы можете официально запатентовать название и идею вашего проекта, "
        f"а также управлять своими патентами. Выберите действие ниже:", 
        reply_markup=markup,
        parse_mode="HTML"
    )

# --- 7. АДМИН-ПАНЕЛЬ (ID OWNER) ---
@bot.message_handler(commands=['adminka'])
def admin_start(message):
    # Проверка на права администратора
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, f"{E['error']} У вас нет прав для доступа к админ-панели.")
        return
    
    show_admin_panel(message.chat.id)

def show_admin_panel(chat_id, message_id=None):
    markup = telebot.types.InlineKeyboardMarkup()
    markup.row(
        StyledInlineKeyboardButton("Статистика", callback_data="adm_stats", style="primary", icon_custom_emoji_id=BTN_E_PRIMARY),
        StyledInlineKeyboardButton("Удалить патент", callback_data="adm_delete", style="danger", icon_custom_emoji_id=BTN_E_DANGER)
    )
    markup.row(
        StyledInlineKeyboardButton("Забанить", callback_data="adm_ban", style="danger", icon_custom_emoji_id=BTN_E_DANGER),
        StyledInlineKeyboardButton("Разбанить", callback_data="adm_unban", style="success", icon_custom_emoji_id=BTN_E_SUCCESS)
    )
    markup.row(StyledInlineKeyboardButton("Закрыть панель", callback_data="back_main", style="primary", icon_custom_emoji_id=BTN_E_PRIMARY))
    
    text = f"{E['shield']} <b>Панель администратора</b>\nВыберите необходимое действие:"
    if message_id:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode="HTML")
    else:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_"))
def admin_callbacks(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Отказано в доступе.", show_alert=True)
        return

    if call.data == "adm_stats":
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM patents")
        total_patents = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(DISTINCT user_id) FROM patents")
        total_users = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM banned_users")
        total_banned = cursor.fetchone()[0]
        conn.close()
        
        markup = telebot.types.InlineKeyboardMarkup()
        markup.add(StyledInlineKeyboardButton("Назад в админку", callback_data="adm_back", style="primary", icon_custom_emoji_id=BTN_E_PRIMARY))
        
        text = (f"{E['chart']} <b>Статистика системы:</b>\n\n"
                f"📝 Всего патентов: <b>{total_patents}</b>\n"
                f"👤 Пользователей (с патентами): <b>{total_users}</b>\n"
                f"⛔️ В бане: <b>{total_banned}</b>")
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="HTML")
        
    elif call.data == "adm_delete":
        bot.set_state(call.from_user.id, AdminStates.delete_patent, call.message.chat.id)
        bot.send_message(call.message.chat.id, "Введите номер патента для удаления (например, KMBP-1234567):")
        
    elif call.data == "adm_ban":
        bot.set_state(call.from_user.id, AdminStates.ban_user, call.message.chat.id)
        bot.send_message(call.message.chat.id, "Введите <b>Telegram ID</b> пользователя, которого нужно забанить:", parse_mode="HTML")

    elif call.data == "adm_unban":
        bot.set_state(call.from_user.id, AdminStates.unban_user, call.message.chat.id)
        bot.send_message(call.message.chat.id, "Введите <b>Telegram ID</b> пользователя для разбана:", parse_mode="HTML")

    elif call.data == "adm_back":
        show_admin_panel(call.message.chat.id, call.message.message_id)

@bot.message_handler(state=AdminStates.delete_patent)
def process_admin_delete(message):
    patent_number = message.text.strip().upper()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM patents WHERE patent_number = ?", (patent_number,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    
    bot.delete_state(message.from_user.id, message.chat.id)
    if deleted > 0:
        bot.send_message(message.chat.id, f"{E['success']} Патент {patent_number} успешно удален!")
    else:
        bot.send_message(message.chat.id, f"{E['error']} Патент {patent_number} не найден.")
    show_admin_panel(message.chat.id)

@bot.message_handler(state=AdminStates.ban_user)
def process_admin_ban(message):
    bot.delete_state(message.from_user.id, message.chat.id)
    try:
        user_id_to_ban = int(message.text.strip())
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO banned_users (user_id) VALUES (?)", (user_id_to_ban,))
        conn.commit()
        conn.close()
        bot.send_message(message.chat.id, f"{E['success']} Пользователь <code>{user_id_to_ban}</code> заблокирован.", parse_mode="HTML")
    except ValueError:
        bot.send_message(message.chat.id, f"{E['error']} Ошибка! ID должен состоять только из цифр.")
    
    show_admin_panel(message.chat.id)

@bot.message_handler(state=AdminStates.unban_user)
def process_admin_unban(message):
    bot.delete_state(message.from_user.id, message.chat.id)
    try:
        user_id_to_unban = int(message.text.strip())
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM banned_users WHERE user_id = ?", (user_id_to_unban,))
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        if deleted > 0:
            bot.send_message(message.chat.id, f"{E['success']} Пользователь <code>{user_id_to_unban}</code> разбанен.", parse_mode="HTML")
        else:
            bot.send_message(message.chat.id, f"{E['warn']} Пользователь <code>{user_id_to_unban}</code> не был в бане.", parse_mode="HTML")
    except ValueError:
        bot.send_message(message.chat.id, f"{E['error']} Ошибка! ID должен состоять только из цифр.")
    
    show_admin_panel(message.chat.id)


# --- 8. АНКЕТА СОЗДАНИЯ ПАТЕНТА ---
@bot.callback_query_handler(func=lambda call: call.data == "start_patent")
def process_start_patent(call):
    if is_user_banned(call.from_user.id):
        bot.answer_callback_query(call.id, "Вы заблокированы.", show_alert=True)
        return

    bot.set_state(call.from_user.id, PatentStates.name, call.message.chat.id)
    bot.edit_message_text(f"{E['pencil']} Начнем регистрацию!\n\nВведите ваше <b>ФИО</b> (или имя подающего):", 
                          chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML")

@bot.message_handler(state=PatentStates.name)
def get_name(message):
    bot.add_data(message.from_user.id, message.chat.id, name=message.text)
    bot.set_state(message.from_user.id, PatentStates.username, message.chat.id)
    bot.send_message(message.chat.id, f"Отлично {E['success']} Укажите ваш <b>юзернейм</b> (например, @username):", parse_mode="HTML")

@bot.message_handler(state=PatentStates.username)
def get_username(message):
    bot.add_data(message.from_user.id, message.chat.id, username=message.text)
    bot.set_state(message.from_user.id, PatentStates.project_name, message.chat.id)
    bot.send_message(message.chat.id, f"Теперь введите <b>название вашего проекта</b>:", parse_mode="HTML")

@bot.message_handler(state=PatentStates.project_name)
def get_project_name(message):
    bot.add_data(message.from_user.id, message.chat.id, project_name=message.text)
    bot.set_state(message.from_user.id, PatentStates.project_link, message.chat.id)
    bot.send_message(message.chat.id, f"Отправьте <b>ссылку на проект</b> {E['arrow']}", parse_mode="HTML")

# ЭТАП 1: Получаем ссылку и показываем кнопки выбора типа
@bot.message_handler(state=PatentStates.project_link)
def get_project_link(message):
    bot.add_data(message.from_user.id, message.chat.id, project_link=message.text)
    bot.set_state(message.from_user.id, PatentStates.patent_type, message.chat.id)
    
    markup = telebot.types.InlineKeyboardMarkup()
    markup.row(
        telebot.types.InlineKeyboardButton("💡 Идея", callback_data="type_idea"),
        telebot.types.InlineKeyboardButton("🏷 Название", callback_data="type_name")
    )
    bot.send_message(message.chat.id, "Выберите тип патента:", reply_markup=markup)

# ЭТАП 2: Ловим нажатие кнопки и просим ввести текст
@bot.callback_query_handler(func=lambda call: call.data in ["type_idea", "type_name"])
def process_patent_type(call):
    selected_type = "Идея" if call.data == "type_idea" else "Название"
    
    # Сохраняем тип в данные
    bot.add_data(call.from_user.id, call.message.chat.id, patent_type=selected_type)
    
    # Переходим в состояние ввода текста (содержание идеи/названия)
    bot.set_state(call.from_user.id, PatentStates.patent_content, call.message.chat.id)
    
    bot.edit_message_text(
        text=f"Выбрано: <b>{selected_type}</b>\n\nТеперь введите текстом вашу {selected_type.lower()}:", 
        chat_id=call.message.chat.id, 
        message_id=call.message.message_id, 
        parse_mode="HTML"
    )

# ЭТАП 3: Получаем текст идеи/названия и переходим к доказательствам
@bot.message_handler(state=PatentStates.patent_content)
def get_patent_content(message):
    # Сохраняем сам текст, который ввел пользователь
    bot.add_data(message.from_user.id, message.chat.id, patent_content=message.text)
    
    # Переходим к доказательствам
    bot.set_state(message.from_user.id, PatentStates.proof, message.chat.id)
    
    bot.send_message(
        message.chat.id, 
        f"{E['siren']} Отлично! Теперь отправьте <b>доказательства</b> (фото, файл или ссылку), что это именно ваша разработка:", 
        parse_mode="HTML"
    )"
@bot.message_handler(state=PatentStates.proof)
def get_proof(message):
    bot.add_data(message.from_user.id, message.chat.id, proof=message.text)
    bot.set_state(message.from_user.id, PatentStates.management_links, message.chat.id)
    bot.send_message(message.chat.id, f"Укажите ссылки на <b>старшее руководство</b> (через запятую):", parse_mode="HTML")

@bot.message_handler(state=PatentStates.management_links)
def get_management_links(message):
    bot.add_data(message.from_user.id, message.chat.id, management_links=message.text)
    
    markup = telebot.types.InlineKeyboardMarkup()
    markup.row(
        StyledInlineKeyboardButton("Да, разрешаю", callback_data="perm_yes", style="success", icon_custom_emoji_id=BTN_E_SUCCESS),
        StyledInlineKeyboardButton("Нет, запрещаю", callback_data="perm_no", style="danger", icon_custom_emoji_id=BTN_E_DANGER)
    )
    
    bot.send_message(message.chat.id, f"{E['warn']} Можно ли использовать это название <b>с вашего разрешения</b> другим проектов?", 
                     reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data in ["perm_yes", "perm_no"])
@bot.callback_query_handler(func=lambda call: call.data in ["perm_yes", "perm_no"])
def get_permission(call):
    permission = "Да" if call.data == "perm_yes" else "Нет"
    user_id = call.from_user.id
    
    bot.edit_message_text(f"Вы выбрали: {permission} {E['success']}", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML")
    
    # 1. Исправлено: корректное извлечение данных
    with bot.retrieve_data(call.from_user.id, call.message.chat.id) as data:
        name = data['name']
        username = data['username']
        project_name = data['project_name']
        patent_type = data['patent_type']
        project_link = data['project_link']
        proof = data['proof']
        m_links = data['management_links']
        
    patent_number = "KMBP-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=7))
    date_created = datetime.now().strftime("%d.%m.%Y")
    
    bot.send_message(call.message.chat.id, f"{E['flash']} Генерируем ваш сертификат...", parse_mode="HTML")
    
    # 2. Исправлено: передаем 5 аргументов вместо 4
    cert_image = generate_certificate(patent_number, name, project_name, patent_type, date_created)
    
    share_text = f"✨ Я получил патент КМБП на проект «{project_name}»!\n🆔 Номер патента: {patent_number}"
    encoded_share_text = urllib.parse.quote(share_text)
    
    bot_info = bot.get_me()
    bot_username = bot_info.username if bot_info.username else "bot"
    share_url = f"https://t.me/share/url?url=https://t.me/{bot_username}&text={encoded_share_text}"
    
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(StyledInlineKeyboardButton("Поделиться патентом", url=share_url, style="primary", icon_custom_emoji_id=BTN_E_PRIMARY))
    
    # 3. Добавили тип в описание
    caption = (
        f"{E['sparkles']} <b>Ваш патент успешно зарегистрирован!</b>\n\n"
        f"🆔 Номер: <code>{patent_number}</code>\n"
        f"🚀 Проект: <b>{project_name}</b>\n"
        f"🏷 Тип: {patent_type}\n"
        f"👤 Владелец: {name} ({username})\n"
    )
    
    sent_msg = bot.send_photo(call.message.chat.id, photo=cert_image, caption=caption, parse_mode="HTML", reply_markup=markup)
    cert_file_id = sent_msg.photo[-1].file_id

    # 4. Исправлено: добавлен patent_type в запрос INSERT
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO patents (patent_number, user_id, name, username, project_name, project_link, proof, management_links, permission, patent_type, date_created, cert_file_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (patent_number, user_id, name, username, project_name, project_link, proof, m_links, permission, patent_type, date_created, cert_file_id))
    conn.commit()
    conn.close()

    bot.delete_state(call.from_user.id, call.message.chat.id)

# --- 9. ПРОВЕРКА И СПИСКИ ПАТЕНТОВ ---
@bot.callback_query_handler(func=lambda call: call.data == "check_patent")
def ask_patent_number(call):
    if is_user_banned(call.from_user.id):
        bot.answer_callback_query(call.id, "Вы заблокированы.", show_alert=True)
        return

    bot.set_state(call.from_user.id, PatentStates.check_patent_number, call.message.chat.id)
    bot.send_message(call.message.chat.id, f"{E['eyes']} Введите номер патента (например, <code>KMBP-XXXXXXX</code>):", parse_mode="HTML")

@bot.message_handler(state=PatentStates.check_patent_number)
def check_patent_db(message):
    patent_number = message.text.strip().upper()
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT name, project_name, project_link, permission, date_created, cert_file_id FROM patents WHERE patent_number = ?", (patent_number,))
    result = cursor.fetchone()
    conn.close()
    
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(StyledInlineKeyboardButton("В главное меню", callback_data="back_main", style="primary", icon_custom_emoji_id=BTN_E_PRIMARY))

    if result:
        name, project_name, project_link, permission, date_created, cert_file_id = result
        text = (
            f"{E['green']} <b>Патент {patent_number} действителен!</b>\n\n"
            f"👤 <b>Владелец:</b> {name}\n"
            f"🚀 <b>Проект:</b> {project_name}\n"
            f"🔗 <b>Ссылка:</b> {project_link}\n"
            f"🔓 <b>Использование:</b> {permission}\n"
            f"{E['calendar']} <b>Дата:</b> {date_created}"
        )
        if cert_file_id:
            bot.send_photo(message.chat.id, photo=cert_file_id, caption=text, parse_mode="HTML", reply_markup=markup)
        else:
            bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")
    else:
        bot.send_message(message.chat.id, f"{E['error']} Патент <code>{patent_number}</code> не найден в базе.", reply_markup=markup, parse_mode="HTML")
        
    bot.delete_state(message.from_user.id, message.chat.id)

@bot.callback_query_handler(func=lambda call: call.data == "my_patents")
def show_my_patents(call):
    if is_user_banned(call.from_user.id):
        bot.answer_callback_query(call.id, "Вы заблокированы.", show_alert=True)
        return

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT patent_number, project_name FROM patents WHERE user_id = ?", (call.from_user.id,))
    results = cursor.fetchall()
    conn.close()

    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(StyledInlineKeyboardButton("Назад", callback_data="back_main", style="primary", icon_custom_emoji_id=BTN_E_PRIMARY))
    
    if not results:
        bot.edit_message_text(f"{E['warn']} У вас пока нет зарегистрированных патентов.", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="HTML")
        return

    text = f"{E['star']} <b>Ваши патенты:</b>\n\n"
    for idx, row in enumerate(results, 1):
        text += f"{idx}. <b>{row[1]}</b> — <code>{row[0]}</code>\n"
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data == "recent_patents")
def show_recent_patents(call):
    if is_user_banned(call.from_user.id):
        bot.answer_callback_query(call.id, "Вы заблокированы.", show_alert=True)
        return

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT patent_number, project_name, date_created FROM patents ORDER BY rowid DESC LIMIT 3")
    results = cursor.fetchall()
    conn.close()

    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(StyledInlineKeyboardButton("Назад", callback_data="back_main", style="primary", icon_custom_emoji_id=BTN_E_PRIMARY))
    
    if not results:
        bot.edit_message_text("База патентов пока пуста.", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="HTML")
        return

    text = f"{E['chart']} <b>Последние зарегистрированные патенты:</b>\n\n"
    for row in results:
        text += f"{E['rainbow']} <b>{row[1]}</b> (от {row[2]})\n🆔 <code>{row[0]}</code>\n\n"
        
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data == "back_main")
def back_to_main(call):
    bot.delete_state(call.from_user.id, call.message.chat.id)
    bot.delete_message(call.message.chat.id, call.message.message_id)
    send_welcome(call.message)

bot.add_custom_filter(custom_filters.StateFilter(bot))

if __name__ == "__main__":
    print("Бот Патент КМБП (Премиум + ID Админка) запущен!")
    bot.infinity_polling()
