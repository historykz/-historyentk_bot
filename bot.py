import logging
import os
from html import escape
from itertools import count
from typing import Final, Optional

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =========================================
# НАСТРОЙКИ
# =========================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMINS_RAW = os.getenv("ADMINS", "").strip()

if not BOT_TOKEN:
    raise ValueError("Не найден BOT_TOKEN в переменных окружения")

if not ADMINS_RAW:
    raise ValueError("Не найден ADMINS в переменных окружения")

try:
    ADMINS: Final[set[int]] = {
        int(admin_id.strip())
        for admin_id in ADMINS_RAW.split(",")
        if admin_id.strip()
    }
except ValueError:
    raise ValueError("ADMINS должен содержать только числовые Telegram ID через запятую")

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# =========================================
# ГЛОБАЛЬНЫЕ ХРАНИЛИЩА
# =========================================
# ticket_id -> dict с данными обращения
TICKETS: dict[int, dict] = {}

# response_id -> dict с данными ответа бота пользователю
RESPONSES: dict[int, dict] = {}

# простой счетчик тикетов и ответов
TICKET_SEQ = count(1001)
RESPONSE_SEQ = count(5001)

# =========================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =========================================

def is_admin(user_id: int) -> bool:
    return user_id in ADMINS


def get_username_or_fallback(user) -> str:
    if user.username:
        return f"@{escape(user.username)}"
    return "без username"


def get_reason_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🔐 A) Вопрос по блокировке аккаунта", callback_data="reason:block")],
        [InlineKeyboardButton("🤝 B) Вопрос по сотрудничеству", callback_data="reason:coop")],
        [InlineKeyboardButton("🧠 C) Покупка тестов", callback_data="reason:tests")],
        [InlineKeyboardButton("📕 Д) свой вопрос", callback_data="reason:other")],
    ]
    return InlineKeyboardMarkup(keyboard)


def reason_title(reason_code: str) -> str:
    return {
        "block": "🔐 Вопрос по блокировке аккаунта",
        "coop": "🤝 Вопрос по сотрудничеству",
        "tests": "🧠 Покупка тестов",
        "other": "📕 Свой вопрос",
    }.get(reason_code, "Не выбрано")


def reason_auto_text(reason_code: str) -> str:
    if reason_code == "block":
        return "Напишите жалобу"

    if reason_code == "coop":
        return (
            "Хорошо ☺️\n"
            "Для размещения рекламы, пожалуйста, ответьте на несколько вопросов:\n\n"
            "1️⃣ Укажите тематику рекламы.\n"
            "2️⃣ Отправьте готовый рекламный пост (обязательно).\n"
            "3️⃣ На какой срок планируете размещение рекламы?\n"
            "4️⃣ Есть ли дополнительные просьбы или условия со стороны рекламодателя?\n\n"
            "📩 После получения информации мы рассчитаем стоимость и предложим подходящие варианты размещения."
        )

    if reason_code == "tests":
        return "платные тесты еще не доступны 🥹"

    return "Напишите ваш вопрос, и мы обязательно ответим вам."


def get_admin_display_name(user) -> str:
    if user.username:
        return f"@{escape(user.username)}"
    first_name = escape(user.first_name or "Admin")
    return first_name


def build_admin_ticket_text(ticket_id: int) -> str:
    ticket = TICKETS[ticket_id]
    user = ticket["user"]
    first_name = escape(user["first_name"] or "Без имени")
    last_name = escape(user["last_name"] or "")
    full_name = f"{first_name} {last_name}".strip()

    username = f"@{escape(user['username'])}" if user["username"] else "нет username"
    user_id = user["id"]
    reason = escape(ticket["reason_title"])
    status_text = ticket["status_text"]

    body = (
        "📩 <b>Новое обращение</b>\n\n"
        f"🧾 <b>Тикет:</b> <code>{ticket_id}</code>\n"
        f"👤 <b>Имя:</b> {full_name}\n"
        f"🔹 <b>Username:</b> {username}\n"
        f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
        f"📌 <b>Причина:</b> {reason}\n"
        f"📍 <b>Статус:</b> {status_text}\n"
    )

    if ticket.get("message_text"):
        body += f"\n💬 <b>Сообщение:</b>\n{escape(ticket['message_text'])}\n"

    if ticket.get("caption"):
        body += f"\n📝 <b>Подпись:</b>\n{escape(ticket['caption'])}\n"

    # Показываем реакции админов
    admin_reacts = ticket.get("admin_reactions", {})
    if admin_reacts:
        reacts_line = " | ".join(f"{emo} {len(users)}" for emo, users in admin_reacts.items())
        body += f"\n🧷 <b>Реакции админов:</b> {reacts_line}\n"

    # Показываем реакцию пользователя на ответ, если есть
    user_reaction = ticket.get("user_reaction")
    if user_reaction:
        body += f"\n🙋 <b>Реакция пользователя на ответ:</b> {escape(user_reaction)}\n"

    return body


def build_admin_ticket_keyboard(ticket_id: int) -> InlineKeyboardMarkup:
    ticket = TICKETS[ticket_id]
    a_count = len(ticket["admin_reactions"].get("👍", set()))
    h_count = len(ticket["admin_reactions"].get("🫶🏻", set()))

    keyboard = [
        [
            InlineKeyboardButton("✍️ Ответить", callback_data=f"reply:{ticket_id}"),
        ],
        [
            InlineKeyboardButton(f"👍 {a_count}", callback_data=f"adminreact:{ticket_id}:👍"),
            InlineKeyboardButton(f"🫶🏻 {h_count}", callback_data=f"adminreact:{ticket_id}:🫶🏻"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_user_response_keyboard(response_id: int, selected: Optional[str] = None) -> InlineKeyboardMarkup:
    left = "👍"
    right = "🫶🏻"

    if selected == "👍":
        left = "✅ 👍"
    if selected == "🫶🏻":
        right = "✅ 🫶🏻"

    keyboard = [
        [
            InlineKeyboardButton(left, callback_data=f"userreact:{response_id}:👍"),
            InlineKeyboardButton(right, callback_data=f"userreact:{response_id}:🫶🏻"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


async def refresh_admin_ticket_messages(context: ContextTypes.DEFAULT_TYPE, ticket_id: int):
    ticket = TICKETS[ticket_id]
    text = build_admin_ticket_text(ticket_id)
    markup = build_admin_ticket_keyboard(ticket_id)

    for pair in ticket["admin_message_refs"]:
        chat_id = pair["chat_id"]
        message_id = pair["message_id"]
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )
        except Exception as e:
            logger.warning(f"Не удалось обновить карточку тикета {ticket_id} у админа {chat_id}: {e}")


async def notify_admins_about_user_reaction(
    context: ContextTypes.DEFAULT_TYPE,
    ticket_id: int,
    response_id: int,
    reaction: str
):
    ticket = TICKETS[ticket_id]
    user = ticket["user"]
    uname = f"@{user['username']}" if user["username"] else f"ID {user['id']}"

    text = (
        "🔔 <b>Новая реакция пользователя</b>\n\n"
        f"🧾 <b>Тикет:</b> <code>{ticket_id}</code>\n"
        f"👤 <b>Пользователь:</b> {escape(uname)}\n"
        f"💬 <b>Реакция на ответ:</b> {escape(reaction)}"
    )

    for admin_id in ADMINS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.warning(f"Не удалось уведомить админа {admin_id} о реакции пользователя: {e}")


# =========================================
# КОМАНДЫ
# =========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not update.message:
        return

    if is_admin(user.id):
        text = (
            "✅ <b>Вы вошли как админ</b>\n\n"
            "Как отвечать:\n"
            "1. Нажмите кнопку <b>✍️ Ответить</b> под обращением\n"
            "2. Отправьте следующее сообщение — оно уйдёт пользователю анонимно\n\n"
            "Команды:\n"
            "• <code>/id</code> — узнать свой ID\n"
            "• <code>/cancel</code> — отменить выбранный ответ\n"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        return

    username = f"@{user.username}" if user.username else escape(user.first_name or "пользователь")

    await update.message.reply_text(
        f"Здравствуйте, {escape(username)}!",
        parse_mode=ParseMode.HTML
    )

    await update.message.reply_text(
        "Выберите причину обращения:",
        reply_markup=get_reason_keyboard()
    )


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"Ваш Telegram ID: <code>{user.id}</code>",
        parse_mode=ParseMode.HTML
    )


async def cancel_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("⛔ Команда доступна только администраторам.")
        return

    context.user_data.pop("reply_ticket_id", None)
    await update.message.reply_text("✅ Режим ответа отменён.")


# =========================================
# ВЫБОР ПРИЧИНЫ ОБРАЩЕНИЯ
# =========================================

async def handle_reason_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user

    if not query:
        return

    await query.answer()

    if is_admin(user.id):
        return

    data = query.data or ""
    if not data.startswith("reason:"):
        return

    reason_code = data.split(":", 1)[1]
    context.user_data["reason_code"] = reason_code
    context.user_data["reason_title"] = reason_title(reason_code)

    auto_text = reason_auto_text(reason_code)

    try:
        await query.edit_message_text(
            text=f"✅ Причина обращения: {context.user_data['reason_title']}"
        )
    except Exception:
        pass

    await query.message.reply_text(auto_text)


# =========================================
# КНОПКИ АДМИНА
# =========================================

async def handle_reply_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    admin = update.effective_user

    if not query:
        return

    await query.answer()

    if not is_admin(admin.id):
        await query.answer("Только для админов", show_alert=True)
        return

    data = query.data or ""
    parts = data.split(":")
    if len(parts) != 2:
        return

    ticket_id = int(parts[1])

    if ticket_id not in TICKETS:
        await query.answer("Тикет не найден", show_alert=True)
        return

    context.user_data["reply_ticket_id"] = ticket_id

    await query.message.reply_text(
        f"✍️ Вы выбрали ответ на тикет <code>{ticket_id}</code>.\n"
        f"Теперь отправьте следующее сообщение — оно уйдёт пользователю анонимно.",
        parse_mode=ParseMode.HTML
    )


async def handle_admin_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    admin = update.effective_user

    if not query:
        return

    await query.answer()

    if not is_admin(admin.id):
        await query.answer("Только для админов", show_alert=True)
        return

    data = query.data or ""
    parts = data.split(":")
    if len(parts) != 3:
        return

    ticket_id = int(parts[1])
    reaction = parts[2]

    if ticket_id not in TICKETS:
        return

    ticket = TICKETS[ticket_id]
    ticket["admin_reactions"].setdefault(reaction, set())

    if admin.id in ticket["admin_reactions"][reaction]:
        ticket["admin_reactions"][reaction].remove(admin.id)
    else:
        # убираем другую реакцию этого же админа
        for emo in ["👍", "🫶🏻"]:
            ticket["admin_reactions"].setdefault(emo, set())
            ticket["admin_reactions"][emo].discard(admin.id)
        ticket["admin_reactions"][reaction].add(admin.id)

    await refresh_admin_ticket_messages(context, ticket_id)


# =========================================
# РЕАКЦИИ ПОЛЬЗОВАТЕЛЯ НА ОТВЕТ
# =========================================

async def handle_user_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user

    if not query:
        return

    await query.answer()

    if is_admin(user.id):
        return

    data = query.data or ""
    parts = data.split(":")
    if len(parts) != 3:
        return

    response_id = int(parts[1])
    reaction = parts[2]

    if response_id not in RESPONSES:
        await query.answer("Ответ не найден", show_alert=True)
        return

    info = RESPONSES[response_id]
    if info["user_id"] != user.id:
        await query.answer("Это не ваше сообщение", show_alert=True)
        return

    info["reaction"] = reaction

    ticket_id = info["ticket_id"]
    if ticket_id in TICKETS:
        TICKETS[ticket_id]["user_reaction"] = reaction
        await refresh_admin_ticket_messages(context, ticket_id)

    try:
        await context.bot.edit_message_reply_markup(
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
            reply_markup=build_user_response_keyboard(response_id, selected=reaction)
        )
    except Exception as e:
        logger.warning(f"Не удалось обновить кнопки реакции у пользователя: {e}")

    await notify_admins_about_user_reaction(context, ticket_id, response_id, reaction)


# =========================================
# ОБРАБОТКА СООБЩЕНИЙ
# =========================================

async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    admin = update.effective_user

    if not message or not is_admin(admin.id):
        return

    ticket_id = context.user_data.get("reply_ticket_id")
    if not ticket_id:
        return

    if ticket_id not in TICKETS:
        context.user_data.pop("reply_ticket_id", None)
        await message.reply_text("❌ Тикет не найден.")
        return

    ticket = TICKETS[ticket_id]
    target_user_id = ticket["user"]["id"]

    text_to_send = message.text or message.caption
    if not text_to_send and not message.effective_attachment:
        await message.reply_text("❌ Отправьте текст или медиа с подписью.")
        return

    admin_name = get_admin_display_name(admin)

    response_text = (
        "📨 <b>Ответ от администрации</b>\n\n"
        f"{escape(text_to_send or '')}"
    )

    sent_user_message = None

    try:
        if message.text:
            sent_user_message = await context.bot.send_message(
                chat_id=target_user_id,
                text=response_text,
                parse_mode=ParseMode.HTML,
            )
        elif message.photo:
            sent_user_message = await context.bot.send_photo(
                chat_id=target_user_id,
                photo=message.photo[-1].file_id,
                caption=response_text,
                parse_mode=ParseMode.HTML,
            )
        elif message.document:
            sent_user_message = await context.bot.send_document(
                chat_id=target_user_id,
                document=message.document.file_id,
                caption=response_text,
                parse_mode=ParseMode.HTML,
            )
        elif message.video:
            sent_user_message = await context.bot.send_video(
                chat_id=target_user_id,
                video=message.video.file_id,
                caption=response_text,
                parse_mode=ParseMode.HTML,
            )
        elif message.voice:
            sent_user_message = await context.bot.send_voice(
                chat_id=target_user_id,
                voice=message.voice.file_id,
                caption=response_text,
                parse_mode=ParseMode.HTML,
            )
        else:
            # fallback: копируем, если формат другой
            copied = await context.bot.copy_message(
                chat_id=target_user_id,
                from_chat_id=message.chat_id,
                message_id=message.message_id,
            )
            sent_user_message = copied

        # Добавляем кнопки-реакции под ответом бота пользователю
        response_id = next(RESPONSE_SEQ)

        if sent_user_message:
            RESPONSES[response_id] = {
                "ticket_id": ticket_id,
                "user_id": target_user_id,
                "message_id": sent_user_message.message_id,
                "chat_id": target_user_id,
                "reaction": None,
            }

            try:
                await context.bot.edit_message_reply_markup(
                    chat_id=target_user_id,
                    message_id=sent_user_message.message_id,
                    reply_markup=build_user_response_keyboard(response_id)
                )
            except Exception as e:
                logger.warning(f"Не удалось добавить кнопки-реакции пользователю: {e}")

        ticket["status"] = "answered"
        ticket["answered_by"] = admin_name
        ticket["status_text"] = f"ОТВЕЧЕНО ✅, админом: {admin_name}"

        await refresh_admin_ticket_messages(context, ticket_id)

        context.user_data.pop("reply_ticket_id", None)
        await message.reply_text("✅ Ответ отправлен анонимно.")

    except Exception as e:
        logger.exception("Ошибка при отправке ответа пользователю")
        await message.reply_text(f"❌ Не удалось отправить сообщение.\n{e}")


async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = update.effective_user

    if not message or is_admin(user.id):
        return

    ticket_id = next(TICKET_SEQ)
    reason_code = context.user_data.get("reason_code", "other")
    chosen_reason_title = context.user_data.get("reason_title", reason_title(reason_code))

    message_text = message.text if message.text else None
    caption = message.caption if message.caption else None

    TICKETS[ticket_id] = {
        "ticket_id": ticket_id,
        "reason_code": reason_code,
        "reason_title": chosen_reason_title,
        "status": "open",
        "status_text": "НЕ ОТВЕЧЕНО ❌",
        "answered_by": None,
        "user_reaction": None,
        "admin_reactions": {"👍": set(), "🫶🏻": set()},
        "user": {
            "id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
        },
        "message_text": message_text,
        "caption": caption,
        "admin_message_refs": [],
    }

    admin_text = build_admin_ticket_text(ticket_id)
    admin_markup = build_admin_ticket_keyboard(ticket_id)

    for admin_id in ADMINS:
        try:
            sent = await context.bot.send_message(
                chat_id=admin_id,
                text=admin_text,
                parse_mode=ParseMode.HTML,
                reply_markup=admin_markup,
            )

            TICKETS[ticket_id]["admin_message_refs"].append({
                "chat_id": admin_id,
                "message_id": sent.message_id
            })

            # если сообщение не текстовое — отдельно пересылаем оригинал
            if not message.text:
                await message.forward(chat_id=admin_id)

        except Exception as e:
            logger.warning(f"Не удалось отправить тикет админу {admin_id}: {e}")

    await message.reply_text("✅ Ваше сообщение отправлено администрации.")


# =========================================
# УНИВЕРСАЛЬНЫЙ CALLBACK ROUTER
# =========================================

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return

    data = query.data

    if data.startswith("reason:"):
        await handle_reason_choice(update, context)
    elif data.startswith("reply:"):
        await handle_reply_button(update, context)
    elif data.startswith("adminreact:"):
        await handle_admin_reaction(update, context)
    elif data.startswith("userreact:"):
        await handle_user_reaction(update, context)
    else:
        await query.answer()


# =========================================
# ЗАПУСК
# =========================================

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", my_id))
    app.add_handler(CommandHandler("cancel", cancel_reply))

    app.add_handler(CallbackQueryHandler(callback_router))

    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_admin_message), group=0)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_user_message), group=1)

    logger.info("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
