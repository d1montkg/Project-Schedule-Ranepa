import os
import asyncio
import datetime as dt

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

import first_try as ft


load_dotenv()
BOT_TOKEN = os.environ["BOT_TOKEN"]


def run_sync():
    now = dt.datetime.now(tz=ft.moscow_tz)
    start, end = ft.get_window(now)

    try:
        ft.get_schedule(start, end)
        service = ft.get_calendar_service(interactive=False)
        exist_events = ft.read_events(service, start, end)
    except (ft.ScheduleError, ft.NetworkError,
            ft.GoogleAuthError, ft.GoogleHttpError) as e:
        return f"Синхронизация не выполнена: {e}"

    errors = ft.sync_schedule(service, exist_events, now)
    if errors:
        return f"Готово с ошибками, не обработано пар: {len(errors)}"
    return "Готово, расписание обновлено"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Команда /sync обновит расписание.")

async def sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Синхронизирую...")
    result = await asyncio.to_thread(run_sync)
    await update.message.reply_text(result)

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("sync", sync))
    app.run_polling()


if __name__ == "__main__":
    main()