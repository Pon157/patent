import os
import sqlite3
import random
import string
import urllib.parse
import time
import difflib
import re
from datetime import datetime
from io import BytesIO

import telebot
from telebot import custom_filters
from telebot.handler_backends import State, StatesGroup
from telebot.storage import StateMemoryStorage
from telebot import apihelper
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

# --- 1. ЗАГРУЗКА НАСТРОЕК ---
# Админ-аккаунты, логины и пароли должны храниться в файле .env
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
PROXY_URL = os.getenv("PROXY_URL")
ADMIN_ID = os.getenv("ADMIN_ID")

if not BOT_TOKEN:
    raise ValueError("Пожалуйста, добавьте BOT_TOKEN в файл .env")

try:
    ADMIN_ID = int(ADMIN_ID) if ADMIN_ID else 0
except ValueError:
    ADMIN_ID = 0

if PROXY_URL:
    apihelper.proxy = {'https': PROXY_URL, 'http': PROXY_URL}

state_storage = StateMemoryStorage()
bot = telebot.TeleBot(BOT_TOKEN, state_storage=state_storage)

bot_info = bot.get_me()
BOT_USERNAME = bot_info.username if bot_info.username else "patent_kmbpbot"

# --- 2. БАЗА ДАННЫХ ---
DB_NAME = "patents.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS patents (
            patent_number TEXT PRIMARY KEY,
            user_id INTEGER,
            name TEXT,
            username TEXT,
            project_name TEXT,
            project_link TEXT,
            proof TEXT,
            proof_type TEXT,
            patent_content TEXT,
            management_links TEXT,
            permission TEXT,
            patent_type TEXT,
            date_created TEXT,
            cert_file_id TEXT,
            status TEXT DEFAULT 'APPROVED'
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS banned_users (
            user_id INTEGER PRIMARY KEY
        )
    ''')
    
    # Миграция: добавляем proof_text для сохранения подписей к фото/документам, если колонки еще нет
    cursor.execute("PRAGMA table_info(patents)")
    columns = [info[1] for info in cursor.fetchall()]
    if 'proof_text' not in columns:
        cursor.execute("ALTER TABLE patents ADD COLUMN proof_text TEXT DEFAULT ''")
        
    conn.commit()
    conn.close()

init_db()

def is_user_banned(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM banned_users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return bool(result)

# --- 3. ПРЕМИУМ ЭМОДЗИ (ДЛЯ ТЕКСТА И КНОПОК) ---
def p_emoji(emoji_char, emoji_id):
    return f'<tg-emoji emoji-id="{emoji_id}">{emoji_char}</tg-emoji>'

E = {
    "star": p_emoji("⭐️", "5438496463044752972"),
    "success": p_emoji("✔️", "5206607081334906820"),
    "error": p_emoji("❌", "5416076321442777828"),
    "warn": p_emoji("⚠️", "5240241223632954241"),
    "flash": p_emoji("⚡️", "5456606106748983383"),
    "eyes": p_emoji("👀", "5240241223632954241"),
    "chart": p_emoji("📈", "5325547803936572038"),
    "arrow": p_emoji("➡️", "5244837092042750681"),
    "green": p_emoji("🟢", "5416081784641168838"),
    "sparkles": p_emoji("✨", "5438496463044752972"),
    "calendar": p_emoji("🗓", "5413879192267805083"),
    "siren": p_emoji("🚨", "5395695537687123235"),
    "ban": p_emoji("⛔️", "5456302074604035284"),
    "pencil": p_emoji("✏️", "5395444784611480792"),
    "flag": p_emoji("🚩", "5460755126761312667"),
    "hourglass": p_emoji("⌛", "5386367538735104399"),
    "id": p_emoji("🆔", "5965485570124681987"),
    "user": p_emoji("👤", "5974048815789903111"),
    "rocket": p_emoji("🚀", "5195033767969839232"),
    "lock": p_emoji("🔒", "5348223165380179822"),
    "tag": p_emoji("🏷", "5215499540538340336")
}

# ID премиум эмодзи для кнопок
BTN_ICONS = {
    "PENCIL": "5395444784611480792",
    "EYES": "5240241223632954241",
    "STAR": "5438496463044752972",
    "CHART": "5325547803936572038",
    "ARROW": "5244837092042750681",
    "CHECK": "5416076321442777828",
    "CROSS": "5456302074604035284",
    "ROCKET": "5195033767969839232",
    "FLASH": "5456606106748983383",
    "TAG": "5215499540538340336",
    "LOCK": "5348223165380179822"
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

# --- 4. УМНЫЙ АНТИПЛАГИАТ ---
def is_similar(a, b, threshold=0.85):
    """Сравнивает две строки. Удаляет пробелы, знаки, приводит к нижнему регистру и ищет схожесть."""
    if not a or not b: return False
    clean_a = re.sub(r'\W+', '', str(a)).lower()
    clean_b = re.sub(r'\W+', '', str(b)).lower()
    if not clean_a or not clean_b: return False
    
    ratio = difflib.SequenceMatcher(None, clean_a, clean_b).ratio()
    return ratio >= threshold

# --- 5. FSM СОСТОЯНИЯ ---
class PatentStates(StatesGroup):
    name = State()
    username = State()
    project_name = State()
    project_link = State()
    patent_type = State()
    patent_content = State()
    proof = State()
    management_links = State()
    check_patent_number = State()

class AdminStates(StatesGroup):
    delete_patent = State()
    ban_user = State()
    unban_user = State()

# --- 6. ГЕНЕРАЦИЯ СЕРТИФИКАТА ---
def generate_certificate(patent_number, name, project_name, patent_type, date_str):
    img = Image.new('RGB', (1000, 700), color=(245, 248, 252))
    draw = ImageDraw.Draw(img)

    for i in range(0, 1000, 40):
        draw.line([(i, 0), (i, 700)], fill=(235, 240, 248), width=1)
    for i in range(0, 700, 40):
        draw.line([(0, i), (1000, i)], fill=(235, 240, 248), width=1)

    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/lato/Lato-Medium.ttf", 65)
        font_text = ImageFont.truetype("/usr/share/fonts/truetype/lato/Lato-Medium.ttf", 30)
        font_highlight = ImageFont.truetype("/usr/share/fonts/truetype/lato/Lato-Medium.ttf", 35)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/lato/Lato-Medium.ttf", 20)
    except:
        font_title = font_text = font_highlight = font_small = ImageFont.load_default()

    draw.rectangle([30, 30, 970, 670], outline=(70, 130, 180), width=12)
    draw.rectangle([45, 45, 955, 655], outline=(176, 196, 222), width=3)
    
    draw.text((500, 110), "ПАТЕНТ КМБП", font=font_title, fill=(25, 25, 112), anchor="mm")
    draw.line([(300, 160), (700, 160)], fill=(70, 130, 180), width=3)

    x_start, y, step = 120, 230, 60
    def draw_line(label, value, y_pos):
        draw.text((x_start, y_pos), label, font=font_text, fill=(100, 100, 100))
        draw.text((x_start + 280, y_pos), str(value), font=font_highlight, fill=(0, 0, 0))

    draw_line("Владелец:", name, y)
    draw_line("Проект:", project_name, y + step)
    draw_line("Категория:", patent_type, y + step*2)
    draw_line("Рег. номер:", patent_number, y + step*3)
    draw_line("Дата выдачи:", date_str, y + step*4)

    draw.text((500, 630), f"Verified by @{BOT_USERNAME}", font=font_small, fill=(150, 160, 170), anchor="mm")

    bio = BytesIO()
    img.save(bio, format='PNG')
    bio.seek(0)
    return bio

# --- ПОКАЗ КАРТОЧКИ ПАТЕНТА ---
def show_patent_card(chat_id, user_id, p_num):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT name, project_name, patent_type, patent_content, proof, proof_type, permission, date_created, cert_file_id, status, proof_text FROM patents WHERE patent_number = ?", (p_num,))
    res = c.fetchone()
    conn.close()

    if not res:
        return bot.send_message(chat_id, f"{E['error']} Патент <code>{p_num}</code> не найден.", parse_mode="HTML")
    
    name, p_name, p_type, p_content, proof, proof_type, perm, date, cert_id, status, proof_text = res
    
    if status == 'PENDING':
        return bot.send_message(chat_id, f"{E['hourglass']} Патент <code>{p_num}</code> находится на модерации и пока недоступен для публичного просмотра.", parse_mode="HTML")

    text = (f"{E['green']} <b>Карточка патента: {p_num}</b>\n\n"
            f"{E['user']} <b>Владелец:</b> {name}\n"
            f"{E['rocket']} <b>Проект:</b> {p_name}\n"
            f"{E['tag']} <b>Тип:</b> {p_type}\n"
            f"{E['pencil']} <b>Суть:</b> <i>{p_content[:500]}</i>\n"
            f"{E['lock']} <b>Право доступа:</b> {perm}\n"
            f"{E['calendar']} <b>Дата:</b> {date}")

    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(StyledInlineKeyboardButton("В главное меню", callback_data="back_main", style="primary", icon_custom_emoji_id=BTN_ICONS["ARROW"]))

    if cert_id != "PENDING_REVIEW":
        bot.send_photo(chat_id, photo=cert_id, caption=text, parse_mode="HTML", reply_markup=markup)
    else:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")

    # Вывод доказательства с текстом (если есть)
    proof_caption = f"{E['siren']} <b>Прикрепленное доказательство</b>"
    if proof_text:
        proof_caption += f"\n\n<b>Описание:</b> {proof_text}"

    if proof_type == "PHOTO" and proof:
        bot.send_photo(chat_id, photo=proof, caption=proof_caption, parse_mode="HTML")
    elif proof_type == "DOC" and proof:
        bot.send_document(chat_id, proof, caption=proof_caption, parse_mode="HTML")
    elif proof_type == "TEXT":
        bot.send_message(chat_id, f"{E['siren']} <b>Прикрепленное доказательство (Текст/Ссылка):</b>\n{proof}", parse_mode="HTML")


# --- 7. ОСНОВНОЕ МЕНЮ И ДИПЛИНКИ ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    if is_user_banned(message.from_user.id):
        return bot.send_message(message.chat.id, f"{E['ban']} Вы заблокированы.")

    # Отправка стикера перед основным сообщением
    sticker_id = "CAACAgIAAxkBAAEEJCRqEej-8vUY010oQBTHo1daHAvmbQACX38AApvFYUtPfKF5IUox3jsE"
    bot.send_sticker(message.chat.id, sticker_id)

    args = message.text.split()
    if len(args) > 1 and args[1].startswith("KMBP-"):
        patent_number = args[1].upper()
        return show_patent_card(message.chat.id, message.from_user.id, patent_number)

    markup = telebot.types.InlineKeyboardMarkup()
    markup.row(
        StyledInlineKeyboardButton("Получить патент", callback_data="start_patent", style="primary", icon_custom_emoji_id=BTN_ICONS["PENCIL"]),
        StyledInlineKeyboardButton("Проверить", callback_data="check_patent", style="success", icon_custom_emoji_id=BTN_ICONS["EYES"])
    )
    markup.row(
        StyledInlineKeyboardButton("Мои патенты", callback_data="my_patents", style="primary", icon_custom_emoji_id=BTN_ICONS["STAR"]),
        StyledInlineKeyboardButton("Последние", callback_data="recent_patents", style="primary", icon_custom_emoji_id=BTN_ICONS["CHART"])
    )
    
    bot.send_message(
        message.chat.id, 
        f"{E['star']} <b>Добро пожаловать в систему Патент КМБП!</b>\n\n"
        f"Здесь вы можете официально запатентовать название и идею вашего проекта, "
        f"а также управлять своими патентами. Выберите действие:", 
        reply_markup=markup, parse_mode="HTML"
    )

# --- 8. АДМИН-ПАНЕЛЬ ---
@bot.message_handler(commands=['adminka'])
def admin_start(message):
    if message.from_user.id != ADMIN_ID:
        return bot.send_message(message.chat.id, f"{E['error']} Отказано в доступе.", parse_mode="HTML")
    show_admin_panel(message.chat.id)

def show_admin_panel(chat_id, message_id=None):
    markup = telebot.types.InlineKeyboardMarkup()
    markup.row(
        StyledInlineKeyboardButton("Статистика", callback_data="adm_stats", style="primary", icon_custom_emoji_id=BTN_ICONS["CHART"]),
        StyledInlineKeyboardButton("Удалить", callback_data="adm_delete", style="danger", icon_custom_emoji_id=BTN_ICONS["CROSS"])
    )
    markup.row(
        StyledInlineKeyboardButton("Забанить", callback_data="adm_ban", style="danger", icon_custom_emoji_id=BTN_ICONS["LOCK"]),
        StyledInlineKeyboardButton("Разбанить", callback_data="adm_unban", style="success", icon_custom_emoji_id=BTN_ICONS["CHECK"])
    )
    markup.row(StyledInlineKeyboardButton("Закрыть панель", callback_data="back_main", style="primary", icon_custom_emoji_id=BTN_ICONS["ARROW"]))
    
    text = f"{E['lock']} <b>Панель администратора</b>\nВыберите действие:"
    if message_id:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode="HTML")
    else:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_"))
def admin_callbacks(call):
    if call.from_user.id != ADMIN_ID:
        return bot.answer_callback_query(call.id, "Доступ закрыт", show_alert=True)

    if call.data == "adm_stats":
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM patents")
        total = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM patents WHERE status='PENDING'")
        pending = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM banned_users")
        banned = c.fetchone()[0]
        conn.close()
        
        markup = telebot.types.InlineKeyboardMarkup()
        markup.add(StyledInlineKeyboardButton("Назад", callback_data="adm_back", style="primary", icon_custom_emoji_id=BTN_ICONS["ARROW"]))
        
        text = (f"{E['chart']} <b>Статистика:</b>\n\n"
                f"{E['pencil']} Всего патентов: <b>{total}</b>\n"
                f"{E['hourglass']} Ожидают проверки: <b>{pending}</b>\n"
                f"{E['ban']} В бане: <b>{banned}</b>")
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="HTML")
        
    elif call.data == "adm_delete":
        bot.set_state(call.from_user.id, AdminStates.delete_patent, call.message.chat.id)
        bot.send_message(call.message.chat.id, f"{E['pencil']} Введите номер патента для удаления:", parse_mode="HTML")
        
    elif call.data == "adm_ban":
        bot.set_state(call.from_user.id, AdminStates.ban_user, call.message.chat.id)
        bot.send_message(call.message.chat.id, f"{E['user']} Введите Telegram ID для бана:", parse_mode="HTML")

    elif call.data == "adm_unban":
        bot.set_state(call.from_user.id, AdminStates.unban_user, call.message.chat.id)
        bot.send_message(call.message.chat.id, f"{E['user']} Введите Telegram ID для разбана:", parse_mode="HTML")

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
        bot.send_message(message.chat.id, f"{E['success']} Удалено!", parse_mode="HTML")
    else:
        bot.send_message(message.chat.id, f"{E['error']} Не найдено.", parse_mode="HTML")
    show_admin_panel(message.chat.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("resolve_"))
def admin_resolve_patent(call):
    if call.from_user.id != ADMIN_ID:
        return
    
    _, action, patent_number = call.data.split("_")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    if action == "approve":
        cursor.execute("UPDATE patents SET status='APPROVED' WHERE patent_number=?", (patent_number,))
        bot.edit_message_text(f"{E['success']} Патент {patent_number} ОДОБРЕН модератором.", call.message.chat.id, call.message.message_id, parse_mode="HTML")
        
        cursor.execute("SELECT user_id FROM patents WHERE patent_number=?", (patent_number,))
        u_id = cursor.fetchone()[0]
        try:
            bot.send_message(u_id, f"{E['success']} Ваш патент <b>{patent_number}</b> успешно прошел проверку модератора и активирован!", parse_mode="HTML")
        except: pass

    elif action == "reject":
        cursor.execute("SELECT user_id FROM patents WHERE patent_number=?", (patent_number,))
        u_id = cursor.fetchone()[0]
        cursor.execute("DELETE FROM patents WHERE patent_number=?", (patent_number,))
        bot.edit_message_text(f"{E['error']} Патент {patent_number} ОТКЛОНЕН.", call.message.chat.id, call.message.message_id, parse_mode="HTML")
        try:
            bot.send_message(u_id, f"{E['error']} К сожалению, ваш запрос на патент был отклонен модератором из-за совпадений с существующими проектами.", parse_mode="HTML")
        except: pass
        
    conn.commit()
    conn.close()

# --- 9. АНКЕТА И АНТИПЛАГИАТ ---
@bot.callback_query_handler(func=lambda call: call.data == "start_patent")
def process_start_patent(call):
    if is_user_banned(call.from_user.id): return
    bot.set_state(call.from_user.id, PatentStates.name, call.message.chat.id)
    bot.edit_message_text(f"{E['pencil']} Начнем! Введите ваше <b>имя</b>:", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML")

@bot.message_handler(state=PatentStates.name)
def get_name(message):
    bot.add_data(message.from_user.id, message.chat.id, name=message.text)
    bot.set_state(message.from_user.id, PatentStates.username, message.chat.id)
    bot.send_message(message.chat.id, f"{E['user']} Укажите ваш <b>юзернейм</b> (@username):", parse_mode="HTML")

@bot.message_handler(state=PatentStates.username)
def get_username(message):
    bot.add_data(message.from_user.id, message.chat.id, username=message.text)
    bot.set_state(message.from_user.id, PatentStates.project_name, message.chat.id)
    bot.send_message(message.chat.id, f"{E['rocket']} Введите <b>название проекта</b>:", parse_mode="HTML")

@bot.message_handler(state=PatentStates.project_name)
def get_project_name(message):
    bot.add_data(message.from_user.id, message.chat.id, project_name=message.text)
    bot.set_state(message.from_user.id, PatentStates.project_link, message.chat.id)
    bot.send_message(message.chat.id, f"{E['arrow']} Отправьте <b>ссылку на проект</b>:", parse_mode="HTML")

@bot.message_handler(state=PatentStates.project_link)
def get_project_link(message):
    bot.add_data(message.from_user.id, message.chat.id, project_link=message.text)
    bot.set_state(message.from_user.id, PatentStates.patent_type, message.chat.id)
    
    markup = telebot.types.InlineKeyboardMarkup()
    markup.row(
        StyledInlineKeyboardButton("Идея", callback_data="type_idea", style="primary", icon_custom_emoji_id=BTN_ICONS["FLASH"]),
        StyledInlineKeyboardButton("Название", callback_data="type_name", style="primary", icon_custom_emoji_id=BTN_ICONS["TAG"])
    )
    bot.send_message(message.chat.id, f"{E['tag']} Выберите тип патента:", reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data in ["type_idea", "type_name"])
def process_patent_type(call):
    p_type = "Идея" if call.data == "type_idea" else "Название"
    bot.add_data(call.from_user.id, call.message.chat.id, patent_type=p_type)
    bot.set_state(call.from_user.id, PatentStates.patent_content, call.message.chat.id)
    bot.edit_message_text(f"Выбрано: <b>{p_type}</b>\n\n{E['pencil']} Напишите идею/название", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML")

@bot.message_handler(state=PatentStates.patent_content)
def get_patent_content(message):
    bot.add_data(message.from_user.id, message.chat.id, patent_content=message.text)
    bot.set_state(message.from_user.id, PatentStates.proof, message.chat.id)
    bot.send_message(message.chat.id, f"{E['siren']} Отправьте <b>доказательства</b> (фото с подписью, документ или текст):", parse_mode="HTML")

@bot.message_handler(state=PatentStates.proof, content_types=['text', 'photo', 'document'])
def get_proof(message):
    # Ловим подпись к фото или файлу, если она есть
    caption_text = message.caption if message.caption else ""
    
    if message.photo:
        bot.add_data(message.from_user.id, message.chat.id, proof=message.photo[-1].file_id, proof_type="PHOTO", proof_text=caption_text)
    elif message.document:
        bot.add_data(message.from_user.id, message.chat.id, proof=message.document.file_id, proof_type="DOC", proof_text=caption_text)
    else:
        bot.add_data(message.from_user.id, message.chat.id, proof=message.text, proof_type="TEXT", proof_text="")

    bot.set_state(message.from_user.id, PatentStates.management_links, message.chat.id)
    bot.send_message(message.chat.id, f"{E['user']} Ссылки на <b>старшее руководство</b>:", parse_mode="HTML")

@bot.message_handler(state=PatentStates.management_links)
def get_management_links(message):
    bot.add_data(message.from_user.id, message.chat.id, management_links=message.text)
    markup = telebot.types.InlineKeyboardMarkup()
    markup.row(
        StyledInlineKeyboardButton("Да, разрешаю", callback_data="perm_yes", style="success", icon_custom_emoji_id=BTN_ICONS["CHECK"]),
        StyledInlineKeyboardButton("Нет, запрещаю", callback_data="perm_no", style="danger", icon_custom_emoji_id=BTN_ICONS["CROSS"])
    )
    bot.send_message(message.chat.id, f"{E['lock']} Разрешаете использовать название другим проектам?", reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data in ["perm_yes", "perm_no"])
def final_generation(call):
    permission = "Да" if call.data == "perm_yes" else "Нет"
    bot.edit_message_text(f"Ожидайте генерации... {E['hourglass']}", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML")

    with bot.retrieve_data(call.from_user.id, call.message.chat.id) as data:
        name, username = data['name'], data['username']
        project_name, project_link = data['project_name'], data['project_link']
        patent_type, patent_content = data['patent_type'], data['patent_content']
        proof, proof_type = data['proof'], data['proof_type']
        proof_text = data.get('proof_text', '') 
        m_links = data['management_links']

    patent_number = "KMBP-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=7))
    date_created = datetime.now().strftime("%d.%m.%Y")

    # --- УМНЫЙ АНТИПЛАГИАТ ЧЕРЕЗ DIFFLIB ---
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT patent_number, project_name, patent_content FROM patents")
    all_patents = cursor.fetchall()

    duplicate_id = None
    for pid, p_name, p_content in all_patents:
        if is_similar(project_name, p_name) or is_similar(patent_content, p_content):
            duplicate_id = pid
            break

    status = 'PENDING' if duplicate_id else 'APPROVED'
    cert_image = generate_certificate(patent_number, name, project_name, patent_type, date_created)
    
    # Кнопка "Поделиться"
    share_text = f"✨ Мы зарегистрировали патент на проект «{project_name}» в боте @{BOT_USERNAME}."
    share_url = f"https://t.me/share/url?url=https://t.me/{BOT_USERNAME}?start={patent_number}&text={urllib.parse.quote(share_text)}"
    
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(StyledInlineKeyboardButton("Поделиться патентом", url=share_url, style="primary", icon_custom_emoji_id=BTN_ICONS["ROCKET"]))
    markup.add(StyledInlineKeyboardButton("В главное меню", callback_data="back_main", style="primary", icon_custom_emoji_id=BTN_ICONS["ARROW"]))

    if status == 'APPROVED':
        caption = (f"{E['sparkles']} <b>Патент зарегистрирован!</b>\n\n"
                   f"{E['id']} Номер: <code>{patent_number}</code>\n"
                   f"{E['rocket']} Проект: <b>{project_name}</b>\n"
                   f"{E['tag']} Тип: {patent_type}")
        sent_msg = bot.send_photo(call.message.chat.id, photo=cert_image, caption=caption, parse_mode="HTML", reply_markup=markup)
        cert_file_id = sent_msg.photo[-1].file_id
    else:
        bot.send_message(call.message.chat.id, f"{E['warn']} Система нашла сильное совпадение с существующим проектом! Ваш патент <code>{patent_number}</code> отправлен на ручную модерацию.", parse_mode="HTML", reply_markup=markup)
        cert_file_id = "PENDING_REVIEW"
        
        adm_markup = telebot.types.InlineKeyboardMarkup()
        adm_markup.row(
            telebot.types.InlineKeyboardButton("Одобрить", callback_data=f"resolve_approve_{patent_number}"),
            telebot.types.InlineKeyboardButton("Отклонить", callback_data=f"resolve_reject_{patent_number}")
        )
        
        admin_text = (f"{E['flag']} <b>ПОДОЗРЕНИЕ НА ПЛАГИАТ (Умный фильтр)</b>\n\n"
                      f"{E['user']} Пользователь: @{username}\n"
                      f"{E['rocket']} Проект: {project_name}\n"
                      f"{E['id']} Совпало с патентом: <code>{duplicate_id}</code>\n"
                      f"{E['pencil']} Контент: <i>{patent_content[:150]}...</i>\n\n"
                      f"{E['siren']} <b>Доказательства:</b>")

        if ADMIN_ID:
            bot.send_message(ADMIN_ID, admin_text, parse_mode="HTML")
            # Отправка самих докв админу
            if proof_type == "PHOTO" and proof:
                bot.send_photo(ADMIN_ID, photo=proof, caption=f"Описание: {proof_text}" if proof_text else None)
            elif proof_type == "DOC" and proof:
                bot.send_document(ADMIN_ID, document=proof, caption=f"Описание: {proof_text}" if proof_text else None)
            elif proof_type == "TEXT":
                bot.send_message(ADMIN_ID, f"Текст доказательства:\n{proof}", parse_mode="HTML")
            
            bot.send_message(ADMIN_ID, "Выберите решение по патенту:", reply_markup=adm_markup)

    cursor.execute('''
        INSERT INTO patents (patent_number, user_id, name, username, project_name, project_link, proof, proof_type, patent_content, management_links, permission, patent_type, date_created, cert_file_id, status, proof_text)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (patent_number, call.from_user.id, name, username, project_name, project_link, proof, proof_type, patent_content, m_links, permission, patent_type, date_created, cert_file_id, status, proof_text))
    
    conn.commit()
    conn.close()
    bot.delete_state(call.from_user.id, call.message.chat.id)

# --- 10. ПРОВЕРКА ПАТЕНТА И СПИСКИ ---
@bot.callback_query_handler(func=lambda call: call.data == "check_patent")
def ask_patent_number(call):
    if is_user_banned(call.from_user.id): return
    bot.set_state(call.from_user.id, PatentStates.check_patent_number, call.message.chat.id)
    bot.send_message(call.message.chat.id, f"{E['eyes']} Введите номер патента:", parse_mode="HTML")

@bot.message_handler(state=PatentStates.check_patent_number)
def check_patent_db(message):
    p_num = message.text.strip().upper()
    bot.delete_state(message.from_user.id, message.chat.id)
    show_patent_card(message.chat.id, message.from_user.id, p_num)

@bot.callback_query_handler(func=lambda call: call.data in ["my_patents", "recent_patents"])
def show_lists(call):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    if call.data == "my_patents":
        c.execute("SELECT patent_number, project_name, status FROM patents WHERE user_id = ?", (call.from_user.id,))
        res = c.fetchall()
        title = f"{E['star']} <b>Ваши патенты:</b>\n"
    else:
        c.execute("SELECT patent_number, project_name, status FROM patents WHERE status='APPROVED' ORDER BY rowid DESC LIMIT 5")
        res = c.fetchall()
        title = f"{E['chart']} <b>Последние патенты:</b>\n"
    conn.close()

    markup = telebot.types.InlineKeyboardMarkup().add(StyledInlineKeyboardButton("Назад", callback_data="back_main", style="primary", icon_custom_emoji_id=BTN_ICONS["ARROW"]))
    if not res:
        return bot.edit_message_text(f"{E['eyes']} Пусто.", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="HTML")

    text = title + "\n".join([f"{E['star']} <b>{r[1]}</b> — <code>{r[0]}</code> {'(На модерации)' if r[2]=='PENDING' else ''}" for r in res])
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data == "back_main")
def back_main(call):
    bot.delete_message(call.message.chat.id, call.message.message_id)
    send_welcome(call.message)

bot.add_custom_filter(custom_filters.StateFilter(bot))

if __name__ == "__main__":
    print("Бот Патент КМБП (v4: Умный Антиплагиат + Фото-текст) запущен!")
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60, skip_pending=True)
        except Exception as e:
            print(f"Ошибка сети: {e}. Переподключение через 5 секунд...")
            time.sleep(5)
