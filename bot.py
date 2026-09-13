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

def main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("💰 Услуги", "📞 Контакты")
    markup.add("📋 Заказать", " Статус")
    markup.add(" Аналитика", "👤 Профиль")
    return markup

user_states = {}
user_phones = {}

@bot.message_handler(commands=['start'])
def start(message):
    add_user(message.chat.id, message.from_user.username, message.from_user.first_name)
    user_states[message.chat.id] = 'main'
    bot.send_message(message.chat.id, f"Привет, {message.from_user.first_name}!", 
                    reply_markup=main_keyboard())

@bot.message_handler(content_types=['contact'])
def handle_contact(message):
    if user_states.get(message.chat.id) == 'phone':
        phone = message.contact.phone_number
        user_phones[message.chat.id] = phone
        user_states[message.chat.id] = 'desc'
        bot.send_message(message.chat.id, f"Телефон: {phone}\n\nОпишите проект:", 
                        reply_markup=types.ReplyKeyboardRemove())

@bot.message_handler(func=lambda m: True)
def handle_text(message):
    if not message.text:
        return
    state = user_states.get(message.chat.id, 'main')
    text = message.text.strip()
    text_lower = text.lower()
    
    if state == 'desc':
        phone = user_phones.get(message.chat.id, "не указан")
        order_id = save_order(message.chat.id, message.from_user.username, 
                             message.from_user.first_name, phone, message.text)
        if order_id:
            bot.send_message(message.chat.id, f"Заявка #{order_id} принята!", 
                           reply_markup=main_keyboard())
        user_states[message.chat.id] = 'main'
        user_phones.pop(message.chat.id, None)
        return
    
    if state == 'phone' and text.replace("+", "").replace(" ", "").replace("-", "").isdigit():
        user_phones[message.chat.id] = text
        user_states[message.chat.id] = 'desc'
        bot.send_message(message.chat.id, f"Телефон: {text}\n\nОпишите проект:", 
                        reply_markup=types.ReplyKeyboardRemove())
        return
    
    if "заказ" in text_lower:
        user_states[message.chat.id] = 'phone'
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add(types.KeyboardButton("Отправить телефон", request_contact=True))
        markup.add("Назад")
        bot.send_message(message.chat.id, "Отправьте телефон:", reply_markup=markup)
        return
    
    if "статус" in text_lower:
        try:
            ws = connect_to_sheets()
            data = ws.get_all_values()
            for row in reversed(data[1:]):
                if len(row) >= 5 and str(row[4]) == str(message.chat.id):
                    bot.send_message(message.chat.id, f"Заявка #{row[0]}\nСтатус: {row[8]}")
                    return
        except:
            pass
        bot.send_message(message.chat.id, "Нет заявок")
        return
    
    if "услуг" in text_lower:
        bot.send_message(message.chat.id, "Услуги:\n1. Чат-бот — 15 000р\n2. AI-ассистент — 30 000р\n3. Автоматизация — 50 000р")
        return
    
    if "назад" in text_lower or "меню" in text_lower:
        user_states[message.chat.id] = 'main'
        user_phones.pop(message.chat.id, None)
        bot.send_message(message.chat.id, "Меню:", reply_markup=main_keyboard())

if __name__ == '__main__':
    print("AI Star CRM запущен!")
    threading.Thread(target=check_statuses, daemon=True).start()
    bot.infinity_polling(timeout=30)
