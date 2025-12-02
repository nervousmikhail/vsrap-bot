import os
import sys
import json
import asyncio
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command, CommandStart
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove


# === JSON-логгер (чтобы Railway не помечал логи как ошибки) ===
class JsonStdoutHandler(logging.StreamHandler):
    def __init__(self):
        super().__init__(stream=sys.stdout)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "severity": record.levelname,      # <-- Railway теперь понимает INFO
                "logger": record.name,
                "message": record.getMessage(),
            }
            self.stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.flush()
        except Exception:
            pass


logging.basicConfig(level=logging.INFO, handlers=[JsonStdoutHandler()], force=True)

for name in ("aiogram", "aiohttp", "asyncio"):
    lg = logging.getLogger(name)
    lg.handlers = []
    lg.propagate = True

log = logging.getLogger("support-bot")


# === Мини веб-сервер для healthcheck ===
async def _ping(_):
    return web.Response(text="OK")


async def start_web():
    app = web.Application()
    app.router.add_get("/", _ping)
    app.router.add_get("/health", _ping)
    port = int(os.getenv("PORT", "8080"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(json.dumps({"severity": "INFO", "message": f"🌐 Web healthcheck on port {port}"}))


# === ENV ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPPORT_GROUP_ID_ENV = os.getenv("SUPPORT_GROUP_ID", "").strip()
SUPPORT_GROUP_ID = int(SUPPORT_GROUP_ID_ENV) if SUPPORT_GROUP_ID_ENV not in ("", None) else None


# === BOT ===
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

forward_map: dict[int, tuple[int, int]] = {}
states: dict[int, dict] = {}


# === Список актуальных выпусков (для текста и /videos) ===
EPISODES_TEXT = (
    "• VSRAP Podcast — MADK1D\n"
    "• Или-или: ДИЛАРА, АКУЛИЧ, Мэйби Бэйби, ALISHA\n"
    "• Или-или: Bushido Zho, Frame Tamer, Руслан Усачев, Денис Кукояка\n"
    "• VSRAP Podcast — Темный принц"
)


# === Текст условий ===
TERMS_TEXT = (
    "<b>Важно:</b> нарезки принимаем <u>не по любым видео</u>, а только по актуальным "
    "выпускам подкаста и шоу VSRAP.\n\n"
    "<b>Сейчас участвуют в программе только эти выпуски:</b>\n"
    f"{EPISODES_TEXT}\n\n"
    "<i>Нарезки с других выпусков могут не быть одобрены и не попасть под выплату.</i>\n\n"

    "<b>Чтобы получить вознаграждение:</b>\n\n"
    "1) Укажите ссылку на видео\n"
    "2) Приложите доказательство (лучше всего — скрин(ы) аналитики)\n"
    "3) Укажите реквизиты для выплаты\n\n"
    "<b>Выплаты</b> — только <u>криптовалютой</u> (USDT).\n\n"

    "<blockquote expandable>"
    "<b>▶️ Инструкция по выводу:</b>\n\n"
    "• Самый простой способ — Telegram-кошелёк <code>@wallet</code>\n"
    "1) Запустите @wallet → пройдите верификацию\n"
    "2) Кошелёк → Пополнить → Внешний кошелёк → Доллары → TRC20 / TON\n\n"
    "Порог выплаты: USDT TON — от $20, TRC20 — от $100"
    "</blockquote>\n\n"

    "<b>💰 Тарифы:</b>\n\n"
    "• TikTok от 200 000 просмотров — 1 000 ₽\n"
    "• TikTok от 1 000 000 — 4 000 ₽\n"
    "• YouTube Shorts от 100 000 вовлечённых — 700 ₽\n"
    "• Другие площадки от 100 000 — 500 ₽\n\n"

    "<blockquote expandable>"
    "<b>❗️ Важно:</b>\n"
    "• Видео должно быть по материалам <b>VSRAP</b>\n"
    "• Обязательно хэштег <code>#vsrapedit</code> и упоминание канала\n"
    "• Публикация не раньше 10.10.2025\n"
    "• Без сторонней рекламы\n"
    "• Разница между оригиналом и вашим видео — не более 30 дней"
    "</blockquote>\n\n"

    "⬇️ Когда готовы — нажмите «Запросить выплату» и следуйте шагам."
)


# === Утилиты ===
def user_label(msg: Message) -> str:
    u = msg.from_user
    uname = f"@{u.username}" if u.username else "—"
    return f"{u.full_name} ({uname}, id={u.id})"


def has_single_media(msg: Message):
    if msg.media_group_id:
        return False, None, "Пришлите один скрин/файл, не альбом."
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
        return False, None, "Отправьте файл или скрин, не только текст."
    return True, media, None


def extract_url_from_message(msg: Message):
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
    if text.startswith(("t.me/", "youtu.be/", "youtube.com/", "vk.com/", "instagram.com/", "x.com/", "twitter.com/")):
        return "https://" + text if not text.startswith("http") else text
    return None


def terms_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Запросить выплату", callback_data="payout:start")]
    ])


def again_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Подать ещё одну заявку", callback_data="payout:start")]
    ])


# === Команды ===
@dp.message(CommandStart(), F.chat.type == "private")
async def start_dm(msg: Message):
    await msg.answer(TERMS_TEXT, reply_markup=terms_keyboard())


@dp.message(Command("where"))
async def where(msg: Message):
    await msg.reply(f"Этот чат имеет id: <code>{msg.chat.id}</code>")


@dp.message(Command("videos"))
async def videos(msg: Message):
    await msg.answer(
        "<b>📺 Актуальные выпуски для нарезок:</b>\n\n" + EPISODES_TEXT
    )


# === Логика заявки ===
@dp.callback_query(F.data == "payout:start")
async def payout_start(cq: CallbackQuery):
    user_id = cq.from_user.id
    states[user_id] = {"stage": "link"}
    await cq.message.answer("Шаг <b>1/3</b> — пришлите ссылку на видео.", reply_markup=ReplyKeyboardRemove())
    await cq.answer()


@dp.message(F.chat.type == "private", ~F.from_user.is_bot)
async def handle_user_dm(msg: Message):
    if not SUPPORT_GROUP_ID:
        await msg.answer("Сообщение принято. (SUPPORT_GROUP_ID не настроен.)")
        return

    user_id = msg.from_user.id
    st = states.get(user_id)
    if st:
        stage = st.get("stage")

        # Шаг 1/3 — ссылка
        if stage == "link":
            url = extract_url_from_message(msg)
            if not url:
                await msg.answer("Пришлите корректную ссылку на видео.")
                return
            st["link"] = url
            st["stage"] = "proof"
            await msg.answer("Ссылка принята ✅\nТеперь пришлите один скрин/файл подтверждения.")
            return

        # Шаг 2/3 — пруф
        if stage == "proof":
            ok, media, err = has_single_media(msg)
            if not ok:
                await msg.answer(err)
                return
            st["media"] = media
            st["stage"] = "requisites"
            await msg.answer("Пруф получен ✅\nТеперь укажите реквизиты (кошелёк USDT или контакт).")
            return

        # Шаг 3/3 — реквизиты
        if stage == "requisites":
            text = (msg.caption or msg.text or "").strip() or "—"
            st["requisites"] = text

            header_text = (
                f"🧾 <b>Заявка на выплату</b>\n"
                f"От: {user_label(msg)}\n"
                f"🔗 Ссылка: {st.get('link','—')}\n"
                f"💼 Реквизиты: {st.get('requisites','—')}"
            )
            sent_header = await bot.send_message(SUPPORT_GROUP_ID, header_text)
            forward_map[sent_header.message_id] = (msg.chat.id, msg.message_id)

            m = st.get("media")
            if m:
                cap = m.get("caption") or ""
                t = m["type"]
                if t == "photo":
                    await bot.send_photo(SUPPORT_GROUP_ID, m["file_id"], caption=cap)
                elif t == "document":
                    await bot.send_document(SUPPORT_GROUP_ID, m["file_id"], caption=cap)
                elif t == "video":
                    await bot.send_video(SUPPORT_GROUP_ID, m["file_id"], caption=cap)
                elif t == "animation":
                    await bot.send_animation(SUPPORT_GROUP_ID, m["file_id"], caption=cap)

            await msg.answer(
                "✅ Заявка отправлена модерации.\n\n"
                "Если у вас есть ещё нарезки по этим выпускам — подайте новую заявку.",
                reply_markup=again_keyboard()
            )
            states.pop(user_id, None)
            return

    # обычный саппорт-мост
    header = f"🆕 Сообщение от {user_label(msg)}"
    await bot.send_message(SUPPORT_GROUP_ID, header)
    sent = await msg.copy_to(SUPPORT_GROUP_ID)
    forward_map[sent.message_id] = (msg.chat.id, msg.message_id)


# === Ответы из группы ===
@dp.message(lambda m: SUPPORT_GROUP_ID and m.chat.id == SUPPORT_GROUP_ID)
async def handle_group(msg: Message):
    if not msg.reply_to_message:
        return
    ref = forward_map.get(msg.reply_to_message.message_id)
    if not ref:
        return
    user_chat_id, _ = ref
    if msg.from_user and msg.from_user.is_bot:
        return
    prefix = f"Ответ от админа {msg.from_user.full_name}:\n\n"
    if msg.text:
        await bot.send_message(user_chat_id, prefix + msg.text)
    elif msg.caption:
        await msg.copy_to(user_chat_id, caption=prefix + msg.caption)
    else:
        await msg.copy_to(user_chat_id)


# === Старт ===
async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN.")
    log.info("✅ Bot starting… /where в группе покажет chat_id.")
    await asyncio.gather(start_web(), dp.start_polling(bot))


if __name__ == "__main__":
    asyncio.run(main())
