import asyncio
import google_auth
import logging
import os
import html
import db
import reminders

from dotenv import load_dotenv
from telegram import (
    BotCommand, Update, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.error import BadRequest
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ConversationHandler,
    MessageHandler, ContextTypes, filters,
)
from first_try import run_sync, ensure_calendar, SyncError


load_dotenv()

logging.basicConfig(level=logging.INFO) 
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.INFO)
logger = logging.getLogger(__name__)

CONSENT, LOGIN, PASSWORD, GOOGLE, CHOICE = range(5)
ADMIN_ID = int(os.environ["ADMIN_ID"])
REMINDER_INPUT = 10

HELP_TEXT = (
    "Что я умею:\n"
    "/sync: обновить расписание в календаре сейчас\n"
    "/status: результат последней синхронизации\n"
    "/reminders: настроить напоминания\n"
    "/start: регистрация или переподключение Google\n"
    "/cancel: отменить текущий ввод\n\n"
    "Расписание обновляется само каждые 2 часа, пока включён компьютер, на котором работает бот. Сам бот (кнопки, команды, уведомления) отвечает только когда он запущен. Если бот не отвечает, расписание в календаре всё равно обновится, как только компьютер включат."
)

COMMANDS = [
    BotCommand("sync", "Обновить расписание сейчас"),
    BotCommand("status", "Результат последней синхронизации"),
    BotCommand("reminders", "Настроить напоминания"),
    BotCommand("help", "Список команд"),
    BotCommand("start", "Регистрация или переподключение"),
    BotCommand("cancel", "Отменить текущий ввод"),
]

MENU_BUTTONS = filters.Regex("^(Синхронизировать|Статус|Напоминания)$")
text_only = filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS

STATUS_TEXTS = {
    "ok": "Расписание обновлено.",
    "partial": "Часть пар обработать не удалось. Попробую снова при следующей синхронизации.",
    "error": "Сайт или сервис сейчас недоступны. Попробую снова при следующей синхронизации.",
    "user_error": (
        "Синхронизация не выполнена. Проверь логин и пароль от ЛК "
        "или переподключи Google: /start"
    ),
}


def main_keyboard():
    return ReplyKeyboardMarkup(
        [["Синхронизировать", "Статус"], ["Напоминания"]],
        resize_keyboard=True,
        is_persistent=True,
    )


def fix_keyboard(with_password=False):
    row = [InlineKeyboardButton("Изменить логин", callback_data="fix:login")]
    if with_password:
        row.append(InlineKeyboardButton("Изменить пароль", callback_data="fix:password"))
    return InlineKeyboardMarkup([row])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user = await asyncio.to_thread(db.get_user, update.effective_user.id)
    if user is not None:
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Изменить логин", callback_data="reg:login"),
                InlineKeyboardButton("Изменить пароль", callback_data="reg:password"),
            ],
            [InlineKeyboardButton("Переподключить Google", callback_data="reg:google")],
            [InlineKeyboardButton("Ничего не менять", callback_data="reg:keep")],
        ])
        await update.message.reply_text(
            f"Ты уже зарегистрирован, логин ЛК: {user['login']}.\n"
            "Что хочешь изменить?",
            reply_markup=keyboard,
        )
        return CHOICE

    keyboard = [["Согласен", "Не согласен"]]
    await update.message.reply_text(
        "Я храню твой логин и пароль от ЛК (пароль в зашифрованном виде), "
        "чтобы обновлять расписание",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True),
    )
    return CONSENT


async def start_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data.split(":")[1]
    if action == "keep":
        await query.edit_message_text("Хорошо, ничего не меняю.")
        return ConversationHandler.END

    context.user_data["mode"] = action
    await query.edit_message_reply_markup(reply_markup=None)
    if action == "login":
        await query.message.reply_text("Введи новый логин от ЛК:")
        return LOGIN
    if action == "password":
        await query.message.reply_text("Введи новый пароль от ЛК:")
        return PASSWORD

    await send_google_link(query.message, context)
    return GOOGLE


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


async def save_existing(update, context, done_text, login=None, password=None):
    user_id = update.effective_user.id
    existing = await asyncio.to_thread(db.get_user, user_id)
    if existing is None:
        context.user_data.clear()
        await update.message.reply_text("Не нашёл твою запись. Пройди регистрацию: /start")
        return ConversationHandler.END

    google_token = await asyncio.to_thread(db.decrypt_google_token, existing)
    login = login or existing["login"]
    password = password or await asyncio.to_thread(db.get_password, user_id)

    if not google_token:
        context.user_data["login"] = login
        context.user_data["password"] = password
        context.user_data["mode"] = "new"
        await send_google_link(update.message, context, login=login)
        return GOOGLE

    await asyncio.to_thread(
        db.save_user, user_id, login, password, google_token, existing["calendar_id"]
    )
    context.user_data.clear()
    await update.message.reply_text(
        f"{done_text} Проверить можно командой /sync.", reply_markup=main_keyboard()
    )
    return ConversationHandler.END


async def get_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    login = update.message.text
    if context.user_data.get("mode") == "login":
        return await save_existing(update, context, "Логин обновлён.", login=login)
    context.user_data["login"] = login

    if context.user_data.get("flow") is not None:
        await update.message.reply_text(
            f"Логин обновлён: {login}. Пришли адрес из браузера, как раньше.",
            reply_markup=fix_keyboard(with_password=True),
        )
        return GOOGLE

    await update.message.reply_text(
        f"Логин: {login}\nВведи пароль:", reply_markup=fix_keyboard()
    )
    return PASSWORD


async def send_google_link(message, context, login=None):
    flow, url, state = google_auth.start_auth()
    context.user_data["flow"] = flow
    context.user_data["state"] = state

    prefix = ""
    markup = None
    if login is not None:
        prefix = f"Логин ЛК: {login}. Если ошибся, поправь кнопками ниже.\n\n"
        markup = fix_keyboard(with_password=True)

    await message.reply_text(
        prefix +
        "Теперь подключим Google Calendar.\n"
        "1. Открой ссылку из следующего сообщения и выбери свой аккаунт.\n"
        "2. Если Google пишет, что приложение не проверено: «Дополнительные настройки» → "
        "«Перейти на страницу RANEPA Schedule (небезопасно)». Это личный проект, проверку Google он не "
        "проходил. Пароль от Google бот не получает. Он создаст в твоём аккаунте отдельный календарь «РАНХиГС» и будет добавлять в него пары. Остальные твои календари и события бот не трогает.\n"
        "3. Нажми «Продолжить».\n"
        "4. Браузер покажет ошибку открытия страницы localhost. Так и должно "
        "быть. Скопируй весь адрес из адресной строки и пришли мне.",
        reply_markup=markup,
    )
    await message.reply_text(url)


async def get_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["password"] = update.message.text
    await update.message.delete()

    if context.user_data.get("mode") == "password":
        return await save_existing(
            update, context, "Пароль обновлён.", password=context.user_data["password"]
        )

    if context.user_data.get("flow") is not None:
        await update.message.reply_text(
            "Пароль обновлён. Пришли адрес из браузера, как раньше.",
            reply_markup=fix_keyboard(with_password=True),
        )
        return GOOGLE

    await send_google_link(update.message, context, login=context.user_data["login"])
    return GOOGLE


async def fix_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text("Введи логин от ЛК заново:")
    return LOGIN


async def fix_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text("Введи пароль от ЛК заново:")
    return PASSWORD


async def get_google(update: Update, context: ContextTypes.DEFAULT_TYPE):
    flow = context.user_data.get("flow")
    state = context.user_data.get("state")
    if flow is None:
        await update.message.reply_text(
            "Попытка подключения потерялась. Начни заново: /start"
        )
        return ConversationHandler.END

    try:
        refresh_token = await asyncio.to_thread(
            google_auth.finish_auth, flow, state, update.message.text
        )
    except google_auth.AuthError as e:
        await update.message.reply_text(str(e))
        return GOOGLE

    await update.message.delete()
    user_id = update.effective_user.id
    existing = await asyncio.to_thread(db.get_user, user_id)
    old_calendar_id = existing["calendar_id"] if existing else None

    if context.user_data.get("mode") == "google" and existing:
        login = existing["login"]
        password = await asyncio.to_thread(db.get_password, user_id)
    else:
        login = context.user_data["login"]
        password = context.user_data["password"]

    try:
        calendar_id = await asyncio.to_thread(
            ensure_calendar, refresh_token, old_calendar_id
        )
    except SyncError as e:
        logger.warning(
            "Календарь не подготовлен для %s: %s: %s",
            user_id, type(e).__name__, e.detail,
        )
        context.user_data.clear()
        await update.message.reply_text(
            "Google подключён, но календарь создать не получилось. "
            "Обычно это временный сбой, попробуй ещё раз немного позже: /start"
        )
        return ConversationHandler.END

    await asyncio.to_thread(
        db.save_user,
        user_id,
        login,
        password,
        refresh_token,
        calendar_id,
    )
    context.user_data.clear()
    await update.message.reply_text(
        "Готово, календарь подключён.\n\n" + HELP_TEXT,
        reply_markup=main_keyboard(),
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user = await asyncio.to_thread(db.get_user, update.effective_user.id)
    markup = main_keyboard() if user else ReplyKeyboardRemove()
    await update.message.reply_text("Отменено.", reply_markup=markup)
    return ConversationHandler.END


async def reminders_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "Пришли, за сколько минут до пары напомнить: числа через запятую или "
        "пробел, например 10, 30, 60.\n"
        f"До {reminders.MAX_REMINDERS} значений, каждое от 1 до "
        f"{reminders.MAX_MINUTES} минут. 1 час = 60, 1 день = 1440.\n"
        "Отмена: /cancel"
    )
    return REMINDER_INPUT


async def reminders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await asyncio.to_thread(db.get_user, update.effective_user.id)
    if user is None:
        await update.effective_message.reply_text("Сначала пройди регистрацию: /start")
        return
    text, markup = reminders_menu(user)
    await update.effective_message.reply_text(text, reply_markup=markup)


def reminders_menu(user):
    enabled, minutes = reminders.user_settings(user)
    if enabled:
        text = f"Напоминания включены: за {reminders.describe(minutes)} до начала пары."
    else:
        text = "Напоминания выключены."

    toggle = "Выключить" if enabled else "Включить"
    presets = [
        InlineKeyboardButton(label, callback_data=f"rem:preset:{key}")
        for key, (label, _) in reminders.PRESETS.items()
    ]
    keyboard = [
        [InlineKeyboardButton(toggle, callback_data="rem:toggle")],
        presets[:2],
        presets[2:],
        [InlineKeyboardButton("Свой вариант", callback_data="rem:custom")],
    ]
    return text, InlineKeyboardMarkup(keyboard)


async def reminders_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user = await asyncio.to_thread(db.get_user, user_id)
    if user is None:
        await query.edit_message_text("Сначала пройди регистрацию: /start")
        return

    enabled, minutes = reminders.user_settings(user)
    parts = query.data.split(":")
    if parts[1] == "toggle":
        enabled = not enabled
    elif parts[1] == "preset":
        preset = reminders.PRESETS.get(parts[2])
        if preset is None:
            return
        enabled = True
        minutes = preset[1]

    await asyncio.to_thread(db.save_reminders, user_id, enabled, minutes)
    user = await asyncio.to_thread(db.get_user, user_id)
    text, markup = reminders_menu(user)
    text += "\n\nПрименю при ближайшей синхронизации (до 2 часов). Чтобы сразу, нажми /sync."
    try:
        await query.edit_message_text(text, reply_markup=markup)
    except BadRequest:
        pass


async def reminders_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        minutes = reminders.parse_minutes(update.message.text)
    except reminders.ReminderError as e:
        await update.message.reply_text(f"{e}\nПопробуй ещё раз или /cancel.")
        return REMINDER_INPUT

    await asyncio.to_thread(
        db.save_reminders, update.effective_user.id, True, minutes
    )
    await update.message.reply_text(
        f"Готово: напоминания за {reminders.describe(minutes)} до начала пары. "
        "Применю при ближайшей синхронизации (до 2 часов), чтобы сразу, нажми /sync."
    )
    return ConversationHandler.END


async def reminders_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END


async def reminders_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reminders_command(update, context)
    return ConversationHandler.END


conv = ConversationHandler(
    entry_points=[CommandHandler("start", start)],
    states={
        CHOICE: [
            CallbackQueryHandler(start_choice, pattern="^reg:(login|password|google|keep)$")
        ],
        CONSENT: [MessageHandler(text_only, consent)],
        LOGIN: [MessageHandler(text_only, get_login)],
        PASSWORD: [
            MessageHandler(text_only, get_password),
            CallbackQueryHandler(fix_login, pattern="^fix:login$"),
        ],
        GOOGLE: [
            MessageHandler(text_only, get_google),
            CallbackQueryHandler(fix_login, pattern="^fix:login$"),
            CallbackQueryHandler(fix_password, pattern="^fix:password$"),
        ],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
    allow_reentry=True,
)

reminders_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(reminders_custom, pattern="^rem:custom$")],
    states={REMINDER_INPUT: [MessageHandler(text_only, reminders_input)]},
    fallbacks=[
        CommandHandler("cancel", reminders_cancel),
        CommandHandler("reminders", reminders_restart),
    ],
    allow_reentry=True,
    conversation_timeout=300,
)


async def sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    if user is None:
        await update.effective_message.reply_text("Сначала пройди регистрацию: /start")
        return

    await update.effective_message.reply_text("Синхронизирую, подожди немного...")

    try:
        password = db.get_password(user_id)
        google_token = db.decrypt_google_token(user)
        enabled, minutes = reminders.user_settings(user)
        status, message, detail = await asyncio.to_thread(
            run_sync, user["login"], password, google_token,
            user["calendar_id"], enabled, minutes,
        )
        error = None if status == "ok" else message
        try:
            await asyncio.to_thread(
                db.save_sync_result, user["telegram_id"], status, error, detail
            )
            if status == "user_error":
                await asyncio.to_thread(db.mark_notified, user["telegram_id"])

        except Exception:
            logger.exception("Не записался результат синхронизации для %s", user_id)

        await update.effective_message.reply_text(str(message))

    except db.UserNotFound:
        await update.effective_message.reply_text("Не нашёл твою запись. Пройди регистрацию: /start")

    except Exception:
        logger.exception("Sync failed for user %s", user_id)
        await update.effective_message.reply_text(
            "Не получилось синхронизировать. Календарь не изменён."
        )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await asyncio.to_thread(db.get_user, update.effective_user.id)
    if user is None:
        await update.effective_message.reply_text("Сначала пройди регистрацию: /start")
        return

    if user["last_status"] is None:
        await update.effective_message.reply_text(
            "Синхронизаций пока не было. Запусти первую: /sync"
        )
        return

    text = STATUS_TEXTS.get(user["last_status"], "Статус неизвестен.")
    if user["last_sync_at"]:
        text += f"\n\nПоследняя попытка: {user['last_sync_at']} (МСК)"
    await update.effective_message.reply_text(text)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(HELP_TEXT, reply_markup=main_keyboard())


async def post_init(app):
    try:
        await app.bot.set_my_commands(COMMANDS)
    except Exception:
        logger.exception("Не удалось обновить список команд")


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
        error = html.escape(
            (u["last_error_detail"] or u["last_error"] or "—")[:300]
        )
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

    app = (
        Application.builder()
        .token(os.environ["BOT_TOKEN"])
        .connect_timeout(20)
        .read_timeout(20)
        .write_timeout(20)
        .pool_timeout(20)
        .get_updates_connect_timeout(20)
        .get_updates_read_timeout(20)
        .post_init(post_init)
        .build()
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler("sync", sync))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.Regex("^Синхронизировать$"), sync))
    app.add_handler(MessageHandler(filters.Regex("^Статус$"), status_command))
    app.add_handler(MessageHandler(filters.Regex("^Напоминания$"), reminders_command))
    app.add_handler(reminders_conv)
    app.add_handler(CommandHandler("reminders", reminders_command))
    app.add_handler(
        CallbackQueryHandler(reminders_button, pattern="^rem:(toggle|preset:.+)$")
    )
    app.job_queue.run_repeating(notify_job, interval=600, first=30)
    app.job_queue.run_repeating(admin_job, interval=600, first=20)

    app.run_polling(bootstrap_retries=-1)


if __name__ == "__main__":
    main()