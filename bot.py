# -*- coding: utf-8 -*-
"""AI Star CRM - Telegram бот с CRM"""

import telebot
from telebot import types
import sqlite3
import time
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import threading
from datetime import datetime, timedelta, timezone
from collections import Counter
import pandas as pd
from flask import Flask, render_template_string, send_file
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
GROQ_API_KEY = os.getenv('GROQ_API_KEY')
ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))
JSON_KEY_FILE = os.getenv('JSON_KEY_FILE', 'credentials.json')
SPREADSHEET_NAME = os.getenv('SPREADSHEET_NAME', 'AI Star CRM')

MSK = timezone(timedelta(hours=2))

def get_local_time_str():
    return datetime.now(MSK).strftime("%Y-%m-%d %H:%M")

bot = telebot.TeleBot(BOT_TOKEN, skip_pending=True)

def connect_to_sheets():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_KEY_FILE, scope)
    gc = gspread.authorize(creds)
    return gc.open(SPREADSHEET_NAME).sheet1

conn = sqlite3.connect('bot_db.sqlite', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, phone TEXT, date TEXT)')
conn.commit()

def is_valid_phone(phone):
    """Телефон не пустой и содержит только цифры и +."""
    if phone is None:
        return False
    value = str(phone).strip()
    if not value:
        return False
    return all(c.isdigit() or c == '+' for c in value)

def add_user(user_id, username, first_name, phone=""):
    cursor.execute("SELECT id FROM users WHERE id = ?", (user_id,))
    if cursor.fetchone() is None:
        cursor.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?)", 
                       (user_id, username, first_name, phone, get_local_time_str()))
        conn.commit()
        return True
    return False

def broadcast_notification(text):
    """Отправить уведомление всем пользователям из таблицы users."""
    cursor.execute("SELECT id FROM users")
    users = cursor.fetchall()
    sent = 0
    failed = 0
    for (user_id,) in users:
        try:
            bot.send_message(user_id, text)
            sent += 1
            time.sleep(0.05)
        except Exception:
            failed += 1
    return sent, failed

def save_order(user_id, username, first_name, phone, description, service="Не указана"):
    try:
        ws = connect_to_sheets()
        order_id = len(ws.get_all_values())
        row = [order_id, get_local_time_str(), first_name, 
               f"@{username}" if username else "неизвестно",
               user_id, phone, description, service, "Новая", "", ""]
        ws.append_row(row)
        admin_msg = f"Заявка #{order_id}\n\nОт: @{username}\nТелефон: {phone}\n\n{description}"
        try:
            bot.send_message(ADMIN_ID, admin_msg)
        except:
            pass
        return order_id
    except Exception as e:
        print(f"Ошибка: {e}")
        return None

def check_statuses():
    while True:
        try:
            ws = connect_to_sheets()
            data = ws.get_all_values()
            for i, row in enumerate(data[1:], start=2):
                while len(row) < 11:
                    row.append("")
                if row[0] and row[4] and row[8] and row[8] != row[10]:
                    tg_id = int(row[4])
                    emoji = {"Новая": "📩", "В работе": "⚙️", "Завершена": "🎉", "Отменена": "❌"}.get(row[8], "")
                    text = f"{emoji} Статус заявки #{row[0]}: {row[8]}"
                    try:
                        bot.send_message(tg_id, text)
                    except:
                        pass
                    ws.update_cell(i, 11, row[8])
            time.sleep(300)
        except Exception as e:
            time.sleep(120)

HELP_TEXT = (
    "*AI Star CRM — помощь*\n\n"
    "*Команды:*\n"
    "/start — запустить бота и открыть главное меню\n"
    "/help — показать список команд и описание\n\n"
    "*Кнопки меню:*\n"
    "💰 *Услуги* — список услуг и цены\n"
    "📞 *Контакты* — контактная информация\n"
    "📋 *Заказать* — оформить новую заявку\n"
    "*Статус* — статус последней заявки\n"
    "*Аналитика* — статистика по заявкам\n"
    "👤 *Профиль* — данные вашего профиля\n"
    "*Помощь* — этот список команд\n"
    "*Назад* / *Меню* — вернуться в главное меню"
)

def send_help(chat_id):
    send_safe(chat_id, HELP_TEXT, parse_mode="Markdown", reply_markup=main_keyboard())

def main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("💰 Услуги", "📞 Контакты")
    markup.add("📋 Заказать", " Статус")
    markup.add(" Аналитика", "👤 Профиль")
    markup.add("Помощь")
    return markup

user_states = {}
user_phones = {}

SERVICES_TEXT = (
    "Услуги:\n"
    "1. Чат-бот — 15 000р\n"
    "2. AI-ассистент — 30 000р\n"
    "3. Автоматизация — 50 000р"
)
PHONE_INVALID_TEXT = (
    "Укажите телефон: поле не должно быть пустым, допустимы только цифры и знак +."
)


def send_safe(chat_id, text, **kwargs):
    """Отправить сообщение в Telegram. При ошибке API логируем и возвращаем False."""
    try:
        bot.send_message(chat_id, text, **kwargs)
        return True
    except Exception as e:
        print(f"Не удалось отправить сообщение в чат {chat_id}: {e}")
        return False


def reset_to_main(chat_id):
    """Сбросить диалог заявки и вернуть пользователя в главное меню."""
    user_states[chat_id] = "main"
    user_phones.pop(chat_id, None)


def send_menu(chat_id, text="Меню:"):
    """Показать главное меню (общий ответ для «Назад» / «Меню»)."""
    reset_to_main(chat_id)
    send_safe(chat_id, text, reply_markup=main_keyboard())


def is_back_to_menu(text_lower):
    """Проверка кнопок возврата в меню."""
    return "назад" in text_lower or "меню" in text_lower


def ask_for_project_description(chat_id, phone):
    """Сохранить телефон и запросить описание проекта."""
    user_phones[chat_id] = phone
    user_states[chat_id] = "desc"
    send_safe(
        chat_id,
        f"Телефон: {phone}\n\nОпишите проект:",
        reply_markup=types.ReplyKeyboardRemove(),
    )


def fetch_sheet_rows():
    """Прочитать все строки таблицы заявок. None — если Google Sheets недоступен."""
    try:
        return connect_to_sheets().get_all_values()
    except Exception as e:
        print(f"Ошибка чтения Google Sheets: {e}")
        return None


def find_last_order_row(chat_id):
    """Найти последнюю заявку пользователя в таблице. None — нет заявок или ошибка."""
    data = fetch_sheet_rows()
    if not data or len(data) < 2:
        return None
    chat_id_str = str(chat_id)
    for row in reversed(data[1:]):
        if len(row) >= 5 and str(row[4]) == chat_id_str:
            return row
    return None


def handle_description_state(message):
    """Состояние desc: текст пользователя — описание проекта, создаём заявку."""
    chat_id = message.chat.id
    phone = user_phones.get(chat_id, "не указан")
    try:
        order_id = save_order(
            chat_id,
            message.from_user.username,
            message.from_user.first_name,
            phone,
            message.text,
        )
    except Exception as e:
        print(f"Ошибка сохранения заявки для {chat_id}: {e}")
        order_id = None

    if order_id:
        send_safe(
            chat_id,
            f"Заявка #{order_id} принята!",
            reply_markup=main_keyboard(),
        )
    else:
        send_safe(
            chat_id,
            "Не удалось сохранить заявку. Попробуйте позже.",
            reply_markup=main_keyboard(),
        )
    reset_to_main(chat_id)


def handle_phone_state(message, text, text_lower):
    """Состояние phone: контакт кнопкой уже обработан отдельно, здесь — ввод текстом."""
    chat_id = message.chat.id
    if is_back_to_menu(text_lower):
        send_menu(chat_id)
        return
    if is_valid_phone(text):
        ask_for_project_description(chat_id, text)
        return
    send_safe(chat_id, PHONE_INVALID_TEXT)


def handle_order_command(message):
    """Команда «Заказать»: запросить телефон."""
    chat_id = message.chat.id
    user_states[chat_id] = "phone"
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("Отправить телефон", request_contact=True))
    markup.add("Назад")
    send_safe(chat_id, "Отправьте телефон:", reply_markup=markup)


def handle_status_command(message):
    """Команда «Статус»: последняя заявка пользователя."""
    chat_id = message.chat.id
    try:
        row = find_last_order_row(chat_id)
    except Exception as e:
        print(f"Ошибка поиска заявки для {chat_id}: {e}")
        row = None

    if row:
        status = row[8] if len(row) > 8 else "неизвестен"
        send_safe(chat_id, f"Заявка #{row[0]}\nСтатус: {status}")
        return
    send_safe(chat_id, "Нет заявок")


def handle_services_command(message):
    """Команда «Услуги»: прайс."""
    send_safe(message.chat.id, SERVICES_TEXT)


def handle_help_command(message):
    """Команда «Помощь» / /help."""
    send_help(message.chat.id)


def handle_menu_command(message):
    """Команды «Назад» и «Меню»."""
    send_menu(message.chat.id)


# Ключевые слова меню → обработчик. Порядок важен: первое совпадение побеждает.
MENU_COMMANDS = (
    ("заказ", handle_order_command),
    ("статус", handle_status_command),
    ("услуг", handle_services_command),
    ("помощ", handle_help_command),
)


def dispatch_menu_command(message, text_lower):
    """Найти обработчик по ключевому слову в тексте кнопки/сообщения."""
    for keyword, handler in MENU_COMMANDS:
        if keyword in text_lower:
            handler(message)
            return True
    if is_back_to_menu(text_lower):
        handle_menu_command(message)
        return True
    return False


@bot.message_handler(commands=["start"])
def start(message):
    try:
        add_user(message.chat.id, message.from_user.username, message.from_user.first_name)
    except Exception as e:
        print(f"Ошибка регистрации пользователя {message.chat.id}: {e}")
    user_states[message.chat.id] = "main"
    send_safe(
        message.chat.id,
        f"Привет, {message.from_user.first_name}!",
        reply_markup=main_keyboard(),
    )


@bot.message_handler(commands=["help"])
def help_command(message):
    handle_help_command(message)


@bot.message_handler(content_types=["contact"])
def handle_contact(message):
    if user_states.get(message.chat.id) != "phone":
        return
    try:
        phone = message.contact.phone_number
    except (AttributeError, TypeError) as e:
        print(f"Контакт без номера телефона: {e}")
        send_safe(message.chat.id, PHONE_INVALID_TEXT)
        return
    if not is_valid_phone(phone):
        send_safe(message.chat.id, PHONE_INVALID_TEXT)
        return
    ask_for_project_description(message.chat.id, phone)


@bot.message_handler(func=lambda m: True)
def handle_text(message):
    """Точка входа: состояние диалога, затем команды меню."""
    if not message.text:
        return

    text = message.text.strip()
    text_lower = text.lower()
    state = user_states.get(message.chat.id, "main")

    try:
        if state == "desc":
            handle_description_state(message)
            return
        if state == "phone":
            handle_phone_state(message, text, text_lower)
            return
        dispatch_menu_command(message, text_lower)
    except Exception as e:
        print(f"Ошибка обработки сообщения от {message.chat.id}: {e}")
        send_safe(message.chat.id, "Произошла ошибка. Попробуйте ещё раз.", reply_markup=main_keyboard())

if __name__ == '__main__':
    print("AI Star CRM запущен!")
    threading.Thread(target=check_statuses, daemon=True).start()
    bot.infinity_polling(timeout=30)
