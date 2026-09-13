# -*- coding: utf-8 -*-
"""Уведомления и рассылки через TeleBot."""

import os
import sqlite3
import threading
import time as time_lib
from datetime import datetime

import telebot
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)

DB_PATH = "bot_db.sqlite"


def send_notification(user_id, text):
    """Отправка уведомления пользователю."""
    try:
        bot.send_message(user_id, text)
        return True
    except Exception as e:
        print(f"Ошибка отправки пользователю {user_id}: {e}")
        return False


def send_broadcast(message):
    """Рассылка всем пользователям из таблицы users."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users")
    users = cursor.fetchall()
    conn.close()

    sent = 0
    failed = 0
    for (user_id,) in users:
        if send_notification(user_id, message):
            sent += 1
        else:
            failed += 1
        time_lib.sleep(0.05)
    return sent, failed


def _delay_seconds(when):
    if isinstance(when, (int, float)):
        return max(0, when)
    if isinstance(when, datetime):
        return max(0, (when - datetime.now()).total_seconds())
    if isinstance(when, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                target = datetime.strptime(when, fmt)
                return max(0, (target - datetime.now()).total_seconds())
            except ValueError:
                continue
        raise ValueError(f"Неверный формат времени: {when}")
    raise TypeError("time должен быть числом секунд, datetime или строкой")


def schedule_notification(time, message):
    """Отложенное уведомление: рассылка в указанное время."""
    delay = _delay_seconds(time)
    timer = threading.Timer(delay, send_broadcast, args=(message,))
    timer.daemon = True
    timer.start()
    return timer
