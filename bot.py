import asyncio
import json
import logging
import os
import html

from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, ConversationHandler,
    MessageHandler, ContextTypes, filters,
)

import db
from first_try import run_sync


load_dotenv()

logging.basicConfig(level=logging.INFO) 
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

CONSENT, LOGIN, PASSWORD, CALENDAR = range(4)

with open("secrets/service_account.json", encoding="utf-8") as f:
    ROBOT_EMAIL = json.load(f)["client_email"]

ADMIN_ID = int(os.environ["ADMIN_ID"])

text_only = filters.TEXT & ~filters.COMMAND


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["Согласен", "Не согласен"]]
    await update.message.reply_text(
        "Я храню твой логин и пароль от ЛК (пароль в зашифрованном виде), "
        "чтобы обновлять расписание",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True),
    )
    return CONSENT


async def consent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text != "Согласен":
        await update.message.reply_text(
            "Без согласия не могу продолжить", reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END
    await update.message.reply_text(
        "Введи логин от ЛК:", reply_markup=ReplyKeyboardRemove()
    )
    return LOGIN


async def get_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["login"] = update.message.text
    await update.message.reply_text("Введи пароль:")
    return PASSWORD


async def get_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["password"] = update.message.text
    await update.message.delete()

    await update.message.reply_text(
        "Чтобы я мог добавлять пары в твой календарь, нужно дать мне к нему "
        "доступ. Мой адрес пришлю следующим сообщением, скопируй его."
    )
    await update.message.reply_text(f"<code>{ROBOT_EMAIL}</code>", parse_mode="HTML")
    await update.message.reply_text(
        "Создай отдельный календарь и пришли мне его ID (на компьютере):\n"
        "1. Google Calendar → «+» рядом с «Другие календари» → «Создать календарь»\n"
        "2. Настройки и общий доступ → «Предоставить доступ пользователям или "
        "группам» → «Добавить» → вставь адрес из сообщения выше с правом "
        "«Изменять и просматривать все сведения о мероприятиях» → «Отправить»\n"
        "3. Там же ниже, в разделе «Интеграция календаря», скопируй "
        "«Идентификатор календаря» и пришли его мне"
    )
    return CALENDAR


async def get_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.save_user(
        update.effective_user.id,
        context.user_data["login"],
        context.user_data["password"],
        update.message.text.strip(),
    )
    context.user_data.clear()
    await update.message.reply_text("Готово! Команда /sync запустит синхронизацию.")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Отменено.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


conv = ConversationHandler(
    entry_points=[CommandHandler("start", start)],
    states={
        CONSENT: [MessageHandler(text_only, consent)],
        LOGIN: [MessageHandler(text_only, get_login)],
        PASSWORD: [MessageHandler(text_only, get_password)],
        CALENDAR: [MessageHandler(text_only, get_calendar)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
)


async def sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    if user is None:
        await update.message.reply_text("Сначала пройди регистрацию: /start")
        return

    await update.message.reply_text("Синхронизирую, подожди немного...")

    try:
        password = db.get_password(user_id)
        status, message = await asyncio.to_thread(
            run_sync, user["login"], password, user["calendar_id"]
        )
        error = None if status == "ok" else message
        try:
            await asyncio.to_thread(db.save_sync_result, user["telegram_id"], status, error)
            if status == "user_error":
                await asyncio.to_thread(db.mark_notified, user["telegram_id"])

        except Exception:
            logger.exception("Не записался результат синхронизации для %s", user_id)

        await update.message.reply_text(str(message))

    except db.UserNotFound:
        await update.message.reply_text("Не нашёл твою запись. Пройди регистрацию: /start")

    except Exception:
        logger.exception("Sync failed for user %s", user_id)
        await update.message.reply_text(
            "Не получилось синхронизировать. Календарь не изменён."
        )


async def notify_job(context: ContextTypes.DEFAULT_TYPE):
    users = await asyncio.to_thread(db.get_users_to_notify)
    for u in users:
        text = u["last_error"]
        try:
            await context.bot.send_message(chat_id=u["telegram_id"], text=text)
        except Exception:
            logger.exception("Не отправилось пользователю %s", u["telegram_id"])
            continue
        await asyncio.to_thread(db.mark_notified, u["telegram_id"])


async def admin_job(context: ContextTypes.DEFAULT_TYPE):
    users = await asyncio.to_thread(db.get_users_for_admin)
    if not users:
        return

    shown = users[:10]
    icons = {"error": "🔴", "partial": "🟠", "user_error": "🟡"}

    lines = [f"<b>Проблемы синхронизации: {len(users)}</b>"]
    for u in shown:
        error = html.escape((u["last_error"] or "—")[:150])
        lines.append(
            f"\n{icons.get(u['last_status'], '⚪')} <b>{u['last_status']}</b>\n"
            f"id: <code>{u['telegram_id']}</code>\n"
            f"ЛК: {html.escape(u['login'] or '—')}\n"
            f"{error}"
        )
    if len(users) > len(shown):
        lines.append(f"\nи ещё {len(users) - len(shown)}")
    text = "\n".join(lines)

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID, text=text, parse_mode="HTML"
        )
    except Exception:
        logger.exception("Не отправилась сводка админу")
        return

    ids = [u["telegram_id"] for u in shown]
    await asyncio.to_thread(db.mark_admin_notified, ids)


def main():
    db.init_db()

    app = Application.builder().token(os.environ["BOT_TOKEN"]).build()
    app.add_handler(conv)
    app.add_handler(CommandHandler("sync", sync))
    app.job_queue.run_repeating(notify_job, interval=600, first=30)
    app.job_queue.run_repeating(admin_job, interval=600, first=20)

    app.run_polling()



if __name__ == "__main__":
    main()
