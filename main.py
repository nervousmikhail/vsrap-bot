import os
import asyncio
import logging
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command, CommandStart
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("support-bot")

# ====== ENV ======
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPPORT_GROUP_ID_ENV = os.getenv("SUPPORT_GROUP_ID", "").strip()
SUPPORT_GROUP_ID = int(SUPPORT_GROUP_ID_ENV) if SUPPORT_GROUP_ID_ENV not in ("", None) else None

# ====== Bot ======
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# соответствия: id сообщения в группе -> (user_chat_id, user_message_id)
forward_map: dict[int, tuple[int, int]] = {}

# ========= Состояния заявок (по пользователю) =========
# states[user_id] = {"stage": "link"|"proof"|"requisites", "link": str, "media": {type,file_id,caption}}
states: dict[int, dict] = {}

# ====== ТЕКСТ УСЛОВИЙ (с раскрывающимися цитатами) ======
TERMS_TEXT = (
    "<b>Чтобы получить вознаграждение:</b>\n\n"
    "1) Укажите ссылку на видео\n"
    "2) Приложите доказательство, что аккаунт принадлежит вам (лучше всего — скрин(ы) аналитики видео)\n"
    "3) Укажите реквизиты для получения вознаграждения\n\n"
    "<b>Выплаты</b> — только <u>криптовалютой</u> (USDT).\n\n"

    "<blockquote expandable>"
    "<b>▶️ Нажмите, чтобы раскрыть инструкцию по выводу</b>\n\n"
    "<b>Кратко для тех, у кого ещё нет крипто-кошелька:</b>\n\n"
    "• Самый быстрый и удобный вариант — встроенный в Telegram кошелёк <code>@wallet</code>\n\n"
    "1) Запустите бота @wallet — откроется официальное мини-приложение внутри Telegram\n"
    "2) Пройдите верификацию (18+) — это нужно для вывода средств через P2P\n"
    "3) Кошелёк → Пополнить → Внешний кошелёк → Доллары → TRC20 / TON\n\n"
    "<b>Нижний порог суммы одной выплаты:</b>\n"
    "• USDT TON — минимум $20\n"
    "• USDT TRC20 — минимум $100"
    "</blockquote>\n\n"

    "<b>💰 Тарифы вознаграждений:</b>\n\n"
    "• TikTok от 200 000 просмотров — 1 000 ₽\n"
    "• TikTok от 1 000 000 просмотров — 4 000 ₽\n"
    "• YouTube Shorts от 100 000 <u>вовлечённых просмотров</u> "
    "(указаны в аналитике видео в разделе «Взаимодействие») — 700 ₽\n"
    "• Другие площадки от 100 000 просмотров — 500 ₽\n\n"

    "<blockquote expandable>"
    "<b>❗️ Нажмите, чтобы раскрыть важную информацию:</b>\n\n"
    "1) Одна заявка = одно видео и один скрин подтверждения.\n"
    "2) Учитываются только ролики, сделанные по материалам нашего YouTube-канала (<b>VSRAP</b>): "
    "подкасты, шоу «ИЛИ-ИЛИ» и другие видео-форматы.\n"
    "3) Обязательно наличие хэштега <code>#vsrapedit</code> и упоминание нашего YouTube-канала "
    "(например: <code>youtube: vsrapru</code>) в описании или комментариях.\n"
    "4) В рассмотрение идут видео, опубликованные <u>после 10.10.2025</u>.\n"
    "5) Не принимаются ролики со сторонней рекламой, баннерами или упоминаниями других организаций.\n"
    "6) Модерация вправе отказать в выплате, если данные или доказательства некорректны.\n"
    "7) Если ролик выполнен в формате «engaging background» (например, Subway Surf, Minecraft-раннер, "
    "«чистка ковров» и т.п.), сумма выплаты может быть снижена до 50%.\n"
    "8) Разница между датой публикации нашего оригинального видео и датой публикации вашей нарезки "
    "не должна превышать 30 дней. Время набора просмотров далее не ограничено."
    "</blockquote>\n\n"

    "⬇️ Когда будете готовы — нажмите «Запросить выплату» и следуйте шагам (1/3, 2/3, 3/3)."
)

# ====== Helpers ======
def user_label(msg: Message) -> str:
    u = msg.from_user
    uname = f"@{u.username}" if u.username else "—"
    return f"{u.full_name} ({uname}, id={u.id})"

def has_single_media(msg: Message) -> tuple[bool, dict | None, str | None]:
    """
    Разрешаем ровно ОДНО вложение (фото/док/видео/гиф) и не принимаем альбомы (media_group).
    Возвращаем (ok, media_dict|None, error_text|None).
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

def extract_url_from_message(msg: Message) -> str | None:
    """Вытаскиваем URL из текста/энтити и валидируем схему/домен."""
    text = (msg.text or msg.caption or "").strip()
    if not text:
        return None
    if msg.entities:
        for ent in msg.entities:
            if ent.type in ("url", "text_link"):
                if ent.type == "text_link" and ent.url:
                    return ent.url
                try:
                    return text[ent.offset: ent.offset + ent.length]
                except Exception:
                    pass
    if text.startswith(("http://", "https://")):
        parsed = urlparse(text)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            return text
    if text.startswith(("t.me/", "www.", "youtu.be/", "youtube.com/", "vk.com/", "instagram.com/", "x.com/", "twitter.com/")):
        return "https://" + text if not text.startswith("http") else text
    return None

def terms_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💸 Запросить выплату", callback_data="payout:start")
    ]])

def again_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="➕ Подать заявку на ещё одну выплату", callback_data="payout:start")
    ]])

# ====== Commands ======
@dp.message(CommandStart(), F.chat.type == "private")
async def start_dm(msg: Message):
    await msg.answer(TERMS_TEXT, reply_markup=terms_keyboard())

@dp.message(Command("help"))
async def help_handler(msg: Message):
    await msg.reply("Заявка подаётся по одной ссылке и одному скрину: 1) ссылка, 2) скрин, 3) реквизиты. /cancel — отмена.")

@dp.message(Command("cancel"))
async def cancel_handler(msg: Message):
    states.pop(msg.from_user.id, None)
    await msg.reply("Окей, отменил процесс. Когда будете готовы — нажмите «Запросить выплату» заново.")

@dp.message(Command("where"))
async def where(msg: Message):
    await msg.reply(f"Этот чат имеет id: <code>{msg.chat.id}</code>")

# ====== Payout flow (3 шага) ======
@dp.callback_query(F.data == "payout:start")
async def payout_start(cq: CallbackQuery):
    user_id = cq.from_user.id
    states[user_id] = {"stage": "link"}  # всегда начинаем заново
    await cq.message.answer(
        "Шаг <b>1/3</b> — пришлите <b>одну ссылку</b> на видео.\n"
        "Пример: https://youtu.be/..., https://tiktok.com/@.../video/...",
        reply_markup=ReplyKeyboardRemove()
    )
    await cq.answer()

@dp.message(F.chat.type == "private", ~F.from_user.is_bot)
async def handle_user_dm(msg: Message):
    if not SUPPORT_GROUP_ID:
        await msg.answer("Сообщение принято. (Предупреждение админам: SUPPORT_GROUP_ID ещё не настроен.)")
        return

    user_id = msg.from_user.id
    st = states.get(user_id)

    if st:
        stage = st.get("stage")

        # === Шаг 1/3: ссылка ===
        if stage == "link":
            url = extract_url_from_message(msg)
            if not url:
                await msg.answer("Это не похоже на ссылку. Пришлите корректный URL (http/https) на ваше видео.")
                return
            st["link"] = url
            st["stage"] = "proof"
            await msg.answer(
                "Ссылка принята ✅\n\n"
                "Шаг <b>2/3</b> — пришлите <b>один</b> скрин/файл подтверждения (фото/документ/PDF/видео). "
                "Альбомы не принимаются."
            )
            return

        # === Шаг 2/3: скрин/медиа ===
        if stage == "proof":
            ok, media, err = has_single_media(msg)
            if not ok:
                await msg.answer(err)
                return
            st["media"] = media  # сохраняем ровно одно вложение
            st["stage"] = "requisites"
            await msg.answer(
                "Пруф получен ✅\n\n"
                "Шаг <b>3/3</b> — укажите реквизиты для выплаты (кошелёк USDT или контакт для связи). "
                "Можно прислать текстом или файлом."
            )
            return

        # === Шаг 3/3: реквизиты ===
        if stage == "requisites":
            text = (msg.caption or msg.text or "").strip() or "—"
            st["requisites"] = text

            # Отправляем в группу: summary + пруф
            header_lines = [
                f"🧾 <b>Заявка на выплату (полная)</b> от {user_label(msg)}",
                f"🔗 Ссылка: {st.get('link','—')}",
                f"💼 Реквизиты: {st.get('requisites','—')}",
            ]
            header_text = "\n".join(header_lines)
            sent_header = await bot.send_message(SUPPORT_GROUP_ID, header_text)
            forward_map[sent_header.message_id] = (msg.chat.id, msg.message_id)

            m = st.get("media")
            if m:
                cap = m.get("caption") or ""
                if m["type"] == "photo":
                    await bot.send_photo(SUPPORT_GROUP_ID, m["file_id"], caption=cap or "Пруф: фото")
                elif m["type"] == "document":
                    await bot.send_document(SUPPORT_GROUP_ID, m["file_id"], caption=cap or "Пруф: документ")
                elif m["type"] == "video":
                    await bot.send_video(SUPPORT_GROUP_ID, m["file_id"], caption=cap or "Пруф: видео")
                elif m["type"] == "animation":
                    await bot.send_animation(SUPPORT_GROUP_ID, m["file_id"], caption=cap or "Пруф: GIF")

            await msg.answer(
                "✅ Заявка отправлена модерации.\n\n"
                "Одна заявка = одно видео и один скрин подтверждения.\n"
                "Если у вас есть ещё видео — подайте новую заявку.",
                reply_markup=again_keyboard()
            )
            states.pop(user_id, None)
            return

    # Обычный саппорт-мост (вне процесса заявки)
    header = f"🆕 Сообщение от {user_label(msg)}"
    await bot.send_message(SUPPORT_GROUP_ID, header)
    sent = await msg.copy_to(SUPPORT_GROUP_ID)
    forward_map[sent.message_id] = (msg.chat.id, msg.message_id)

# ====== Replies from group -> user ======
@dp.message(lambda m: SUPPORT_GROUP_ID is not None and m.chat.id == SUPPORT_GROUP_ID)
async def handle_group(msg: Message):
    if not msg.reply_to_message:
        return
    ref = forward_map.get(msg.reply_to_message.message_id)
    if not ref:
        return
    user_chat_id, _ = ref
    if msg.from_user and msg.from_user.is_bot:
        return

    prefix = f"Ответ от админа: {msg.from_user.full_name}\n\n"
    if msg.text:
        await bot.send_message(user_chat_id, prefix + msg.text)
    elif msg.caption:
        await msg.copy_to(user_chat_id, caption=prefix + msg.caption)
    else:
        await msg.copy_to(user_chat_id)

# ====== Entry point ======
async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN в Environment.")
    log.info("✅ Bot starting… /where в группе покажет chat_id.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())