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


# === JSON-логгер (Railway-friendly) ===
class JsonStdoutHandler(logging.StreamHandler):
    def __init__(self):
        super().__init__(stream=sys.stdout)

    def emit(self, record: logging.LogRecord):
        try:
            payload = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "severity": record.levelname,
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


# === mini healthcheck server ===
async def _ping(_):
    return web.Response(text="OK")


async def start_web():
    app = web.Application()
    app.router.add_get("/", _ping)
    app.router.add_get("/health", _ping)
    port = int(os.getenv("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    print(json.dumps({"severity": "INFO", "message": f"🌐 Web healthcheck on port {port}"}))


# === ENV ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPPORT_GROUP_ID_ENV = os.getenv("SUPPORT_GROUP_ID", "").strip()
SUPPORT_GROUP_ID = int(SUPPORT_GROUP_ID_ENV) if SUPPORT_GROUP_ID_ENV else None


# === BOT ===
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

forward_map: dict[int, tuple[int, int]] = {}
states: dict[int, dict] = {}


# === список актуальных выпусков ===
EPISODES_TEXT = (
    "• Или-или: ДИЛАРА, АКУЛИЧ, Мэйби Бэйби, ALISHA\n"
    "• Или-или: Bushido Zho, Frame Tamer, Руслан Усачев, Денис Кукояка\n"
    "• VSRAP Podcast — MADK1D\n"
    "• VSRAP Podcast — Темный принц\n"
    "• VSRAP Podcast — Feduk\n"
    "• VSRAP Podcast — Фломастер\n"
)


# === текст условий ===
TERMS_TEXT = (
    "<b>Важно:</b> нарезки принимаем <u>только по актуальным выпускам</u> подкаста и шоу VSRAP.\n\n"
    "<b>Сейчас участвуют в программе только эти выпуски:</b>\n"
    "<i>Найти их можно на нашем YouTube-канале: https://www.youtube.com/@vsrapru</i>\n\n"
    f"{EPISODES_TEXT}\n\n"
    "<i>Нарезки с других видео могут быть отклонены.</i>\n\n"

    "<b>Чтобы получить вознаграждение:</b>\n\n"
    "1) Укажите ссылку на видео\n"
    "2) Приложите доказательство (лучше всего — скрин(ы) аналитики)\n"
    "3) Укажите реквизиты для выплаты\n\n"
    "<b>Выплаты</b> — только <u>криптовалютой</u> (USDT).\n\n"

    "<blockquote expandable>"
    "<b>▶️ Инструкция по выводу:</b>\n\n"
    "• Проще всего — Telegram-кошелёк <code>@wallet</code>\n"
    "1) Запустить @wallet → пройти верификацию\n"
    "2) Кошелёк → Пополнить → Внешний кошелёк → TRC20/TON\n\n"
    "Порог выплат: TON — от $20, TRC20 — от $100"
    "</blockquote>\n\n"

    "<b>💰 Тарифы:</b>\n\n"
    "• TikTok от 200 000 просмотров — 1 000 ₽\n"
    "• TikTok от 1 000 000 — 4 000 ₽\n"
    "• YouTube Shorts от 100 000 вовлечённых — 700 ₽\n"
    "• Другие площадки от 100 000 — 500 ₽\n\n"

    "<blockquote expandable>"
    "<b>❗️ Важно:</b>\n"
    "• Обязательно: хэштег <code>#vsrapedit</code> и упоминание канала\n"
    "• Без сторонней рекламы\n"
    "</blockquote>\n\n"

    "⬇️ Когда готовы — нажмите «Запросить выплату» и следуйте шагам."
)


# === helpers ===
def user_label(msg: Message) -> str:
    u = msg.from_user
    return f"{u.full_name} (@{u.username or '—'}, id={u.id})"


def has_single_media(msg: Message):
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
        return False, None, "Пришлите один скрин/файл/видео, не только текст."
    return True, media, None


def extract_url_from_message(msg: Message):
    text = (msg.text or msg.caption or "").strip()
    if not text:
        return None
    if msg.entities:
        for e in msg.entities:
            if e.type in ("url", "text_link"):
                if e.type == "text_link" and e.url:
                    return e.url
                try:
                    return text[e.offset:e.offset + e.length]
                except Exception:
                    pass
    if text.startswith(("http://", "https://")):
        parsed = urlparse(text)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            return text
    return None


def terms_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Запросить выплату", callback_data="payout:start")]
    ])


def menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Узнать условия", callback_data="show_terms")],
        [InlineKeyboardButton(text="💸 Запросить выплату", callback_data="payout:start")],
    ])


def again_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Подать ещё одну заявку", callback_data="payout:start")]
    ])


def restart_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔁 В начало", callback_data="restart")]
    ])


# === commands ===
@dp.message(CommandStart(), F.chat.type == "private")
async def start_dm(msg: Message):
    await msg.answer(TERMS_TEXT, reply_markup=menu_keyboard())


@dp.message(Command("videos"))
async def videos(msg: Message):
    await msg.answer("<b>📺 Актуальные выпуски:</b>\n\n" + EPISODES_TEXT)


@dp.message(Command("where"))
async def where(msg: Message):
    await msg.reply(f"Chat ID: <code>{msg.chat.id}</code>")


@dp.callback_query(F.data == "show_terms")
async def show_terms(cq: CallbackQuery):
    await cq.message.answer(TERMS_TEXT, reply_markup=menu_keyboard())
    await cq.answer()


@dp.callback_query(F.data == "restart")
async def restart_flow(cq: CallbackQuery):
    user_id = cq.from_user.id
    states.pop(user_id, None)
    await cq.message.answer(
        "Текущая заявка (если была) отменена.\n\n"
        "Начинаем сначала. Вот подробные условия участия и актуальные выпуски:",
        reply_markup=menu_keyboard()
    )
    await cq.message.answer(TERMS_TEXT, reply_markup=menu_keyboard())
    await cq.answer()


# === main flow (3 шага) ===
@dp.callback_query(F.data == "payout:start")
async def payout_start(cq: CallbackQuery):
    user_id = cq.from_user.id
    states[user_id] = {"stage": "link"}
    await cq.message.answer(
        "Шаг <b>1/3</b> — пришлите <b>одну ссылку</b> на видео.\n"
        "Пример: https://youtu.be/..., https://tiktok.com/@.../video/...",
        reply_markup=ReplyKeyboardRemove()
    )
    await cq.answer()


@dp.message(F.chat.type == "private", ~F.from_user.is_bot)
async def handle_user_dm(msg: Message):
    if not SUPPORT_GROUP_ID:
        await msg.answer("SUPPORT_GROUP_ID не настроен.")
        return

    user_id = msg.from_user.id
    st = states.get(user_id)

    if st:
        stage = st["stage"]

        # === 1. ссылка ===
        if stage == "link":
            url = extract_url_from_message(msg)
            if not url:
                await msg.answer(
                    "Это не похоже на корректную ссылку.\n\n"
                    "Пришлите, пожалуйста, рабочий URL на видео.\n\n"
                    "Если хотите начать всё заново — нажмите «В начало».",
                    reply_markup=restart_keyboard()
                )
                return
            st["link"] = url
            st["stage"] = "proof"
            await msg.answer(
                "Ссылка принята ✅\n\n"
                "Шаг <b>2/3</b> — пришлите <b>один</b> скрин/файл подтверждения "
                "(фото/документ/PDF/видео). Альбомы не принимаются."
            )
            return

        # === 2. пруф ===
        if stage == "proof":
            ok, media, err = has_single_media(msg)
            if not ok:
                await msg.answer(
                    err + "\n\nЕсли хотите начать заново — нажмите «В начало».",
                    reply_markup=restart_keyboard()
                )
                return
            st["media"] = media
            st["stage"] = "requisites"
            await msg.answer(
                "Пруф получен ✅\n\n"
                "Шаг <b>3/3</b> — укажите реквизиты для выплаты "
                "(кошелёк USDT или контакт для связи). Можно прислать текстом или файлом."
            )
            return

        # === 3. реквизиты ===
        if stage == "requisites":
            text = (msg.caption or msg.text or "").strip()

            # простая проверка, чтобы не пропускать совсем пустое / слишком короткое
            if not text or len(text) < 5:
                await msg.answer(
                    "Похоже, реквизиты указаны слишком коротко или пустые.\n\n"
                    "Пришлите, пожалуйста, кошелёк USDT или понятный контакт для связи.\n\n"
                    "Если хотите начать всё заново — нажмите «В начало».",
                    reply_markup=restart_keyboard()
                )
                return

            st["requisites"] = text

            # отправка в группу
            header = (
                f"🧾 <b>Заявка на выплату</b>\n"
                f"От: {user_label(msg)}\n"
                f"🔗 Ссылка: {st['link']}\n"
                f"💼 Реквизиты: {st['requisites']}"
            )
            sent_header = await bot.send_message(SUPPORT_GROUP_ID, header)
            forward_map[sent_header.message_id] = (msg.chat.id, msg.message_id)

            m = st["media"]
            t = m["type"]
            cap = m["caption"] or ""
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
                "Если у вас есть ещё нарезки — подайте новую заявку.",
                reply_markup=again_keyboard()
            )
            states.pop(user_id, None)
            return

    # === НЕ в процессе заявки: ничего в группу не шлём ===
    await msg.answer(
        "Сейчас бот принимает только заявки на выплату за нарезки.\n\n"
        "Чтобы оформить заявку, используйте кнопки ниже.",
        reply_markup=menu_keyboard()
    )


# === replies from group → user (для уже созданных заявок) ===
@dp.message(lambda m: SUPPORT_GROUP_ID and m.chat.id == SUPPORT_GROUP_ID)
async def handle_group(msg: Message):
    if not msg.reply_to_message:
        return
    ref = forward_map.get(msg.reply_to_message.message_id)
    if not ref:
        return
    user_chat_id, _ = ref
    if msg.from_user.is_bot:
        return

    prefix = f"Ответ от админа {msg.from_user.full_name}:\n\n"
    if msg.text:
        await bot.send_message(user_chat_id, prefix + msg.text)
    elif msg.caption:
        await msg.copy_to(user_chat_id, caption=prefix + msg.caption)
    else:
        await msg.copy_to(user_chat_id)


# === entry point ===
async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN.")
    log.info("Bot starting…")
    await asyncio.gather(start_web(), dp.start_polling(bot))


if __name__ == "__main__":
    asyncio.run(main())
