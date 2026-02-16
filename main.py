import os
import asyncio
import logging
import random
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardRemove
)
from aiogram.filters import CommandStart, Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("vsrap-bot")

# ====== ENV ======
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPPORT_GROUP_ID_ENV = os.getenv("SUPPORT_GROUP_ID", "").strip()
SUPPORT_GROUP_ID = int(SUPPORT_GROUP_ID_ENV) if SUPPORT_GROUP_ID_ENV else None

# ====== Bot ======
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# соответствия: message_id в группе -> user_chat_id
forward_map: dict[int, int] = {}

# states[user_id] = {"mode": "payout"|"contact", "stage": "...", ...}
states: dict[int, dict] = {}

# =======================
#   TEXTS
# =======================

WELCOME_TEXT = (
    "Йо 👋\n"
    "Ты в официальном боте выплат за нарезки подкастов VSRAP.\n\n"
    "Здесь можно стабильно получать выплаты за короткие видео с нашим контентом, "
    "а всё остальное — тарифы, условия и актуальные выпуски — найдёшь ниже.\n\n"
    "Выбирай нужный раздел и поехали."
)

RATES_TEXT = (
    "<b>💰 Тарифы:</b>\n\n"
    "• TikTok от 100 000 просмотров — 500 ₽\n"
    "• TikTok от 200 000 просмотров — 1 000 ₽\n"
    "• TikTok от 1 000 000 — 4 000 ₽\n"
    "• YouTube Shorts от 100 000 вовлечённых — 700 ₽\n"
    "• Другие площадки от 100 000 — 500 ₽\n\n"
    "Если вы ведёте аккаунт, который публикует исключительно контент с видео VSRAP, "
    "мы рассматриваем суммарную статистику аккаунта за месяц и выплачиваем дополнительное вознаграждение.\n\n"
    "• от 500 000 суммарных просмотров за месяц — 1 000 ₽\n"
    "• от 1 000 000 суммарных просмотров за месяц — 3 000 ₽\n"
    "• от 3 000 000 суммарных просмотров за месяц — 5 000 ₽\n"
    "• от 5 000 000 суммарных просмотров за месяц — 10 000 ₽\n\n"
    "❗ <b>Условия для выплаты за короткое видео:</b>\n"
    "• Обязательно: хэштег <code>#vsrapedit</code> и упоминание нашего канала в описании\n"
    "• Без сторонней рекламы\n\n"
    "❗ <b>Условия для месячного вознаграждения:</b>\n"
    "• Аккаунт публикует ТОЛЬКО контент с видео VSRAP\n"
    "• Учитываются только ролики с нашими выпусками\n"
    "• Минимум 5 роликов за месяц\n"
    "• Просмотры считаются суммарно за календарный месяц\n"
    "• Подтверждение — скрин(ы) аналитики аккаунта за период\n"
    "• Модерация вправе отказать, если аккаунт смешанный или данные некорректны"
)

PODCASTS_TEXT = (
    "Важно: нарезки принимаем только по актуальным выпускам подкаста и шоу VSRAP.\n\n"
    "Найти их можно на нашем YouTube-канале: https://www.youtube.com/@vsrapru\n\n"
    "Сейчас участвуют в программе только эти выпуски:\n"
    "• Или-или: ДИЛАРА, АКУЛИЧ, Мэйби Бэйби, ALISHA\n"
    "• Или-или: Bushido Zho, Frame Tamer, Руслан Усачев, Денис Кукояка\n"
    "• VSRAP Podcast — MADK1D\n"
    "• VSRAP Podcast — Темный принц\n"
    "• VSRAP Podcast — Feduk\n"
    "• VSRAP Podcast — Фломастер\n\n"
    "Нарезки с других видео могут быть отклонены."
)

PAYOUT_INFO_TEXT = (
    "Чтобы получить вознаграждение:\n\n"
    "1) Укажите ссылку на видео\n"
    "2) Приложите доказательство (лучше всего — скрин(ы) аналитики)\n"
    "3) Укажите реквизиты для выплаты\n\n"
    "Выплаты — только криптовалютой (USDT).\n"
    "• Порог выплат: TON — от $20, TRC20 — от $100\n\n"
    "⬇️ Когда готовы — нажмите «Подать заявку» и следуйте шагам."
)

CONTACT_TEXT = "Напишите ваш вопрос — мы ответим вам в ближайшее время."

# =======================
#   KEYBOARDS
# =======================

def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Тарифы и условия", callback_data="menu:rates")],
        [InlineKeyboardButton(text="Подкасты для нарезок", callback_data="menu:podcasts")],
        [InlineKeyboardButton(text="Запросить выплату", callback_data="menu:payout")],
        [InlineKeyboardButton(text="Связаться с админом", callback_data="menu:contact")],
    ])

def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")]
    ])

def payout_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подать заявку", callback_data="payout:start")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")],
    ])

def again_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подать заявку", callback_data="payout:start")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")],
    ])

# =======================
#   HELPERS
# =======================

def gen_ticket() -> int:
    return random.randint(10000, 99999)

def user_label(msg: Message) -> str:
    u = msg.from_user
    uname = f"@{u.username}" if u.username else "—"
    return f"{u.full_name} ({uname}, id={u.id})"

def extract_url_from_message(msg: Message) -> str | None:
    """Берём URL из текста и валидируем схему/домен."""
    text = (msg.text or msg.caption or "").strip()
    if not text:
        return None
    if text.startswith(("http://", "https://")):
        p = urlparse(text)
        if p.scheme in ("http", "https") and p.netloc:
            return text
        return None
    # допускаем домены без схемы
    if text.startswith(("t.me/", "www.", "youtu.be/", "youtube.com/", "vk.com/", "instagram.com/", "x.com/", "twitter.com/")):
        text2 = "https://" + text
        p = urlparse(text2)
        if p.netloc:
            return text2
    return None

def has_single_media(msg: Message) -> tuple[bool, dict | None, str | None]:
    """
    Ровно одно вложение (фото/док/видео/gif). Альбомы запрещены.
    """
    if msg.media_group_id:
        return False, None, "Пожалуйста, пришлите <b>один</b> скрин/файл, не альбом."
    media = None
    if msg.photo:
        media = {"type": "photo", "file_id": msg.photo[-1].file_id, "caption": msg.caption or ""}
    elif msg.document:
        media = {"type": "document", "file_id": msg.document.file_id, "caption": msg.caption or ""}
    elif msg.video:
        media = {"type": "video", "file_id": msg.video.file_id, "caption": msg.caption or ""}
    elif msg.animation:
        media = {"type": "animation", "file_id": msg.animation.file_id, "caption": msg.caption or ""}
    if not media:
        return False, None, "Это только текст без вложений. Пришлите один скрин/файл/видео."
    return True, media, None

async def send_to_support(header_html: str, user_chat_id: int) -> int:
    """
    Отправляем хедер в группу и сохраняем mapping, чтобы реплаи админов возвращались пользователю.
    """
    sent = await bot.send_message(SUPPORT_GROUP_ID, header_html)
    forward_map[sent.message_id] = user_chat_id
    return sent.message_id

# =======================
#   COMMANDS
# =======================

@dp.message(CommandStart(), F.chat.type == "private")
async def start_handler(msg: Message):
    states.pop(msg.from_user.id, None)
    await msg.answer(WELCOME_TEXT, reply_markup=main_menu_kb())

@dp.message(Command("cancel"), F.chat.type == "private")
async def cancel_handler(msg: Message):
    states.pop(msg.from_user.id, None)
    await msg.answer("Окей, отменил. Возвращаю в меню.", reply_markup=main_menu_kb())

@dp.message(Command("where"))
async def where(msg: Message):
    await msg.reply(f"Этот чат имеет id: <code>{msg.chat.id}</code>")

# =======================
#   MENU
# =======================

@dp.callback_query(F.data.startswith("menu:"))
async def menu_handler(cq: CallbackQuery):
    action = cq.data.split(":")[1]
    uid = cq.from_user.id

    # любой переход в меню сбрасывает режимы (чтобы человек не “застрял”)
    if action in ("main", "rates", "podcasts", "payout"):
        if uid in states and states[uid].get("mode") == "contact":
            states.pop(uid, None)

    if action == "main":
        await cq.message.edit_text("Главное меню", reply_markup=main_menu_kb())

    elif action == "rates":
        await cq.message.edit_text(RATES_TEXT, reply_markup=back_kb())

    elif action == "podcasts":
        await cq.message.edit_text(PODCASTS_TEXT, reply_markup=back_kb())

    elif action == "payout":
        await cq.message.edit_text(PAYOUT_INFO_TEXT, reply_markup=payout_kb())

    elif action == "contact":
        states[uid] = {"mode": "contact"}
        await cq.message.edit_text(CONTACT_TEXT, reply_markup=back_kb())

    await cq.answer()

# =======================
#   PAYOUT FLOW
# =======================

@dp.callback_query(F.data == "payout:start")
async def payout_start(cq: CallbackQuery):
    uid = cq.from_user.id
    ticket = gen_ticket()
    states[uid] = {"mode": "payout", "stage": "link", "ticket": ticket}
    await cq.message.answer(
        f"Заявка <b>#{ticket}</b>\n\nШаг <b>1/3</b> — пришлите <b>ссылку</b> на видео.",
        reply_markup=ReplyKeyboardRemove()
    )
    await cq.answer()

@dp.message(F.chat.type == "private", ~F.from_user.is_bot)
async def handle_private(msg: Message):
    if not SUPPORT_GROUP_ID:
        await msg.answer("⚠️ SUPPORT_GROUP_ID не настроен. Админам: добавьте переменную в Railway.")
        return

    uid = msg.from_user.id
    st = states.get(uid)

    # ====== CONTACT MODE ======
    if st and st.get("mode") == "contact":
        ticket = gen_ticket()
        header = (
            f"✉️ <b>Обращение #{ticket}</b>\n"
            f"От: {user_label(msg)}\n\n"
            f"{(msg.text or msg.caption or '—').strip()}"
        )
        await send_to_support(header, msg.chat.id)
        await msg.answer(
            f"Ваше обращение зарегистрировано под номером <b>#{ticket}</b>.\n"
            "Мы ответим вам в ближайшее время.",
            reply_markup=main_menu_kb()
        )
        states.pop(uid, None)
        return

    # ====== PAYOUT MODE ======
    if st and st.get("mode") == "payout":
        stage = st.get("stage")
        ticket = st.get("ticket")

        if stage == "link":
            url = extract_url_from_message(msg)
            if not url:
                await msg.answer("Это не похоже на ссылку. Пришлите корректный URL (http/https) на ваше видео.")
                return
            st["link"] = url
            st["stage"] = "proof"
            await msg.answer(
                f"Ссылка принята ✅\n\n"
                f"Заявка <b>#{ticket}</b>\n"
                f"Шаг <b>2/3</b> — пришлите <b>один</b> скрин/файл подтверждения (фото/документ/PDF/видео). "
                "Альбомы не принимаются."
            )
            return

        if stage == "proof":
            ok, media, err = has_single_media(msg)
            if not ok:
                await msg.answer(err)
                return
            st["media"] = media
            st["stage"] = "requisites"
            await msg.answer(
                f"Пруф получен ✅\n\n"
                f"Заявка <b>#{ticket}</b>\n"
                "Шаг <b>3/3</b> — укажите реквизиты для выплаты (USDT TON/TRC20) или другой способ связи."
            )
            return

        if stage == "requisites":
            requisites = (msg.text or msg.caption or "").strip()
            if not requisites:
                requisites = "—"
            st["requisites"] = requisites

            header = (
                f"🧾 <b>Заявка на выплату #{ticket}</b>\n"
                f"От: {user_label(msg)}\n"
                f"🔗 Ссылка: {st.get('link','—')}\n"
                f"💼 Реквизиты: {st.get('requisites','—')}"
            )
            await send_to_support(header, msg.chat.id)

            m = st.get("media")
            if m:
                cap = m.get("caption") or ""
                if m["type"] == "photo":
                    sent = await bot.send_photo(SUPPORT_GROUP_ID, m["file_id"], caption=cap or f"Пруф к заявке #{ticket}")
                    forward_map[sent.message_id] = msg.chat.id
                elif m["type"] == "document":
                    sent = await bot.send_document(SUPPORT_GROUP_ID, m["file_id"], caption=cap or f"Пруф к заявке #{ticket}")
                    forward_map[sent.message_id] = msg.chat.id
                elif m["type"] == "video":
                    sent = await bot.send_video(SUPPORT_GROUP_ID, m["file_id"], caption=cap or f"Пруф к заявке #{ticket}")
                    forward_map[sent.message_id] = msg.chat.id
                elif m["type"] == "animation":
                    sent = await bot.send_animation(SUPPORT_GROUP_ID, m["file_id"], caption=cap or f"Пруф к заявке #{ticket}")
                    forward_map[sent.message_id] = msg.chat.id

            await msg.answer(
                f"✅ Заявка отправлена. Ваш номер: <b>#{ticket}</b>\n"
                "Если нужно уточнить статус — просто напишите номер в чате.\n\n"
                "Если у вас есть ещё видео — подайте новую заявку.",
                reply_markup=again_kb()
            )
            states.pop(uid, None)
            return

    # ====== DEFAULT: ignore or route to support? ======
    # Если человек пишет что-то вне режимов — отправим в группу как обычное сообщение.
    header = f"🆕 Сообщение от {user_label(msg)}"
    await send_to_support(header, msg.chat.id)
    sent = await msg.copy_to(SUPPORT_GROUP_ID)
    forward_map[sent.message_id] = msg.chat.id

# =======================
#   GROUP REPLIES -> USER
# =======================

@dp.message(lambda m: SUPPORT_GROUP_ID is not None and m.chat.id == SUPPORT_GROUP_ID)
async def handle_group(msg: Message):
    if not msg.reply_to_message:
        return

    user_chat_id = forward_map.get(msg.reply_to_message.message_id)
    if not user_chat_id:
        return

    if msg.from_user and msg.from_user.is_bot:
        return

    prefix = f"Ответ от админа: {msg.from_user.full_name}\n\n"

    if msg.text:
        await bot.send_message(user_chat_id, prefix + msg.text)
    elif msg.caption:
        await msg.copy_to(user_chat_id, caption=prefix + msg.caption)
    else:
        await msg.copy_to(user_chat_id)

# =======================
#   ENTRY POINT
# =======================

async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN в Variables.")
    if not SUPPORT_GROUP_ID:
        raise RuntimeError("Не задан SUPPORT_GROUP_ID в Variables.")
    log.info("✅ Bot starting…")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
