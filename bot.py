```python
import asyncio
from datetime import datetime, timezone
import html
import logging
import math
import os
import secrets
import sqlite3
from urllib.parse import urlencode

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramUnauthorizedError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
import aiohttp
from dotenv import load_dotenv

# ============================================================
# НАСТРОЙКИ И КОНСТАНТЫ
# ============================================================
load_dotenv(override=True)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip().strip('"').strip("'")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0").strip() or "0")
DB_PATH = os.getenv("DB_PATH", "dating_bot.sqlite3").strip()

PAYMENTS_ENABLED = os.getenv("PAYMENTS_ENABLED", "0").strip() == "1"
COMMISSION_PERCENT = int(os.getenv("COMMISSION_PERCENT", "30").strip() or "30")
YOOMONEY_WALLET = (
    os.getenv("YOOMONEY_WALLET", "").strip().strip('"').strip("'")
)
YOOMONEY_TOKEN = os.getenv("YOOMONEY_TOKEN", "").strip().strip('"').strip("'")

# Лимит бесплатных лайков в сутки (мотивирует покупать VIP)
FREE_DAILY_LIKES = 25

# Партнёрская кнопка Velvet VPN
VPN_BUTTON_TEXT = "💎 Velvet VPN › Доступно всё"
VPN_BUTTON_URL = "https://t.me/velvet?start=3SLkWOoF"
VPN_BUTTON = (VPN_BUTTON_TEXT, VPN_BUTTON_URL)

# Каталог платных услуг (базовая цена в рублях без учёта 30% комиссии)
PRODUCTS = {
    "vip_7": {
        "title": "👑 VIP-статус на 7 дней",
        "base_price": 290,
        "desc": "Безлимит лайков, просмотр тех, кто тебя лайкнул, значок 👑 VIP и +3 Супер-письма.",
    },
    "vip_30": {
        "title": "👑 VIP-статус на 30 дней (ХИТ)",
        "base_price": 690,
        "desc": "Максимальная выгода! Все привилегии VIP на месяц + 10 Супер-писем в подарок.",
    },
    "boost_24": {
        "title": "🚀 Поднятие в ТОП на 24 часа",
        "base_price": 149,
        "desc": "Твоя анкета показывается первой всем пользователям со значком ⚡️ TOP.",
    },
    "super_5": {
        "title": "💌 Пакет: 5 Супер-писем",
        "base_price": 199,
        "desc": "Пиши личные сообщения понравившимся людям сразу, не дожидаясь взаимного лайка!",
    },
    "reset_skips": {
        "title": "🔄 Сброс пропущенных анкет",
        "base_price": 99,
        "desc": "Очищает историю пропусков — ты снова увидишь всех людей, которых пропустил(а) ранее.",
    },
}

if (
    not BOT_TOKEN
    or ":" not in BOT_TOKEN
    or "НОВЫЙ_ТОКЕН" in BOT_TOKEN
    or "ВАШ_ТОКЕН" in BOT_TOKEN
):
    raise SystemExit("❌ ОШИБКА: Укажи настоящий BOT_TOKEN в файле .env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

dp = Dispatcher()
dp.message.filter(F.chat.type == "private")
dp.callback_query.filter(F.message.chat.type == "private")

# Хранилище последнего ID сообщения бота для каждого пользователя (для автоудаления)
last_bot_messages: dict[int, int] = {}

# ============================================================
# БАЗА ДАННЫХ И АВТО-МИГРАЦИЯ
# ============================================================
db = sqlite3.connect(DB_PATH)
db.row_factory = sqlite3.Row

db.executescript(
    """
    PRAGMA journal_mode = WAL;
    PRAGMA synchronous = NORMAL;

    CREATE TABLE IF NOT EXISTS profiles (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        name TEXT NOT NULL,
        age INTEGER NOT NULL CHECK(age BETWEEN 14 AND 80),
        city TEXT NOT NULL,
        bio TEXT NOT NULL,
        photo_id TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        boost_until INTEGER NOT NULL DEFAULT 0,
        vip_until INTEGER NOT NULL DEFAULT 0,
        superlikes INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS reactions (
        user_id INTEGER NOT NULL,
        target_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(user_id, target_id)
    );

    CREATE TABLE IF NOT EXISTS matches (
        user1 INTEGER NOT NULL,
        user2 INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(user1, user2)
    );

    CREATE TABLE IF NOT EXISTS blocks (
        user_id INTEGER NOT NULL,
        blocked_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(user_id, blocked_id)
    );

    CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reporter_id INTEGER NOT NULL,
        target_id INTEGER NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS orders (
        order_id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        product TEXT NOT NULL,
        base_amount INTEGER NOT NULL DEFAULT 0,
        commission INTEGER NOT NULL DEFAULT 0,
        amount INTEGER NOT NULL,
        currency TEXT NOT NULL,
        status TEXT NOT NULL,
        telegram_charge_id TEXT UNIQUE,
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_profiles_search ON profiles(active, age, boost_until);
    CREATE INDEX IF NOT EXISTS idx_reactions_user ON reactions(user_id, target_id);
    CREATE INDEX IF NOT EXISTS idx_reactions_target ON reactions(target_id, action);
    CREATE INDEX IF NOT EXISTS idx_blocks_pair ON blocks(user_id, blocked_id);
    """
)

# Авто-миграция для существующих баз данных
prof_cols = {
    r["name"] for r in db.execute("PRAGMA table_info(profiles)").fetchall()
}
if "photo_id" not in prof_cols:
    db.execute("ALTER TABLE profiles ADD COLUMN photo_id TEXT")
if "username" not in prof_cols:
    db.execute("ALTER TABLE profiles ADD COLUMN username TEXT")
if "vip_until" not in prof_cols:
    db.execute(
        "ALTER TABLE profiles ADD COLUMN vip_until INTEGER NOT NULL DEFAULT 0"
    )
if "superlikes" not in prof_cols:
    db.execute(
        "ALTER TABLE profiles ADD COLUMN superlikes INTEGER NOT NULL DEFAULT 1"
    )

ord_cols = {
    r["name"] for r in db.execute("PRAGMA table_info(orders)").fetchall()
}
if "base_amount" not in ord_cols:
    db.execute(
        "ALTER TABLE orders ADD COLUMN base_amount INTEGER NOT NULL DEFAULT 0"
    )
if "commission" not in ord_cols:
    db.execute(
        "ALTER TABLE orders ADD COLUMN commission INTEGER NOT NULL DEFAULT 0"
    )
db.commit()


# ============================================================
# УПРАВЛЕНИЕ ЧИСТОТОЙ ЧАТА (АВТОУДАЛЕНИЕ СООБЩЕНИЙ)
# ============================================================
async def delete_msg_safe(bot: Bot, chat_id: int, message_id: int | None):
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except TelegramAPIError:
        pass


async def send_screen(
    event: Message | CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    photo_id: str | None = None,
    delete_user_msg: bool = True,
):
    """Удаляет предыдущее сообщение бота (и сообщение пользователя при вводе)

    и отправляет новый чистый экран.
    """
    bot = event.bot
    user_id = event.from_user.id

    if isinstance(event, CallbackQuery):
        chat_id = event.message.chat.id
        await delete_msg_safe(bot, chat_id, event.message.message_id)
        if last_bot_messages.get(user_id) == event.message.message_id:
            last_bot_messages.pop(user_id, None)
    else:
        chat_id = event.chat.id
        if delete_user_msg:
            await delete_msg_safe(bot, chat_id, event.message_id)

    # Удаляем предыдущее сохранённое сообщение бота, если оно осталось
    old_bot_msg = last_bot_messages.get(user_id)
    if old_bot_msg:
        await delete_msg_safe(bot, chat_id, old_bot_msg)

    sent = None
    if photo_id:
        try:
            sent = await bot.send_photo(
                chat_id=chat_id,
                photo=photo_id,
                caption=text,
                reply_markup=reply_markup,
            )
        except TelegramAPIError:
            sent = None

    if not sent:
        sent = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )

    last_bot_messages[user_id] = sent.message_id
    return sent


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ И РАСЧЁТ КОМИССИИ 30%
# ============================================================
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def age_group(age: int) -> str:
    return "minor" if age < 18 else "adult"


def calc_price_with_commission(base_price: int) -> tuple[int, int, int]:
    """Возвращает (базовая_цена, сумма_комиссии_30%, итого_к_оплате)."""
    commission = math.ceil(base_price * (COMMISSION_PERCENT / 100.0))
    total = base_price + commission
    return base_price, commission, total


def get_profile(user_id: int):
    return db.execute(
        "SELECT * FROM profiles WHERE user_id = ?", (user_id,)
    ).fetchone()


def is_vip(profile: sqlite3.Row) -> bool:
    return bool(profile and profile["vip_until"] > now_ts())


def is_boosted(profile: sqlite3.Row) -> bool:
    return bool(profile and profile["boost_until"] > now_ts())


def get_today_likes_count(user_id: int) -> int:
    today_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = db.execute(
        """
        SELECT COUNT(*) as cnt FROM reactions
        WHERE user_id = ? AND action = 'like' AND created_at LIKE ?
        """,
        (user_id, f"{today_prefix}%"),
    ).fetchone()
    return row["cnt"] if row else 0


def count_incoming_likes(user_id: int) -> int:
    viewer = get_profile(user_id)
    if not viewer:
        return 0
    min_age, max_age = (14, 17) if viewer["age"] < 18 else (18, 80)
    row = db.execute(
        """
        SELECT COUNT(*) as cnt
        FROM reactions r
        JOIN profiles p ON p.user_id = r.user_id
        WHERE r.target_id = ?
          AND r.action = 'like'
          AND p.active = 1
          AND p.age BETWEEN ? AND ?
          AND NOT EXISTS (
              SELECT 1 FROM reactions my_r
              WHERE my_r.user_id = ? AND my_r.target_id = p.user_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM blocks b
              WHERE (b.user_id = ? AND b.blocked_id = p.user_id)
                 OR (b.user_id = p.user_id AND b.blocked_id = ?)
          )
        """,
        (user_id, min_age, max_age, user_id, user_id, user_id),
    ).fetchone()
    return row["cnt"] if row else 0


def buttons(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    keyboard = []
    for row in rows:
        kb_row = []
        for text, action in row:
            if action.startswith(("http://", "https://")):
                kb_row.append(InlineKeyboardButton(text=text, url=action))
            else:
                kb_row.append(
                    InlineKeyboardButton(text=text, callback_data=action)
                )
        keyboard.append(kb_row)
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def main_menu(user_id: int | None = None) -> InlineKeyboardMarkup:
    likes_badge = ""
    if user_id:
        inc = count_incoming_likes(user_id)
        if inc > 0:
            likes_badge = f" ({inc})"

    return buttons(
        [
            [("🔥 Смотреть анкеты", "browse")],
            [
                (f"👀 Кто меня лайкнул{likes_badge}", "who_liked"),
                ("💌 Взаимные", "matches"),
            ],
            [("👑 Магазин & VIP", "shop"), ("🚀 Поднять в ТОП", "boost")],
            [("👤 Моя анкета", "my_profile"), ("⚙️ Настройки", "settings")],
            [("🛡 Безопасность", "safety")],
            [VPN_BUTTON],
        ]
    )


def candidate_menu(target_id: int, superlikes: int = 0) -> InlineKeyboardMarkup:
    sl_text = (
        f"💌 Написать сразу ({superlikes})"
        if superlikes > 0
        else "💌 Написать сразу"
    )
    return buttons(
        [
            [
                ("❤️ Нравится", f"like:{target_id}"),
                ("⏭ Следующая", f"skip:{target_id}"),
            ],
            [(sl_text, f"supermsg:{target_id}")],
            [
                ("🚩 Жалоба", f"report:{target_id}"),
                ("⛔ В ЧС", f"block:{target_id}"),
            ],
            [VPN_BUTTON],
            [("⬅️ Назад в меню", "menu")],
        ]
    )


def profile_text(profile: sqlite3.Row) -> str:
    name = html.escape(profile["name"])
    city = html.escape(profile["city"])
    bio = html.escape(profile["bio"])

    badges = []
    if is_vip(profile):
        badges.append("👑 <b>VIP</b>")
    if is_boosted(profile):
        badges.append("⚡️ <b>TOP</b>")
    badge_str = ("  " + " ".join(badges)) if badges else ""

    return (
        f"👤 <b>{name}, {profile['age']}</b>{badge_str}\n"
        f"📍 <i>г. {city}</i>\n\n"
        f"<blockquote>💬 {bio}</blockquote>"
    )


def user_contact_link(profile: sqlite3.Row) -> str:
    name = html.escape(profile["name"])
    link = f'<a href="tg://user?id={profile["user_id"]}"><b>{name}</b></a>'
    if profile["username"]:
        return f"{link} (@{html.escape(profile['username'])})"
    return link


def pair_allowed(user_id: int, target_id: int) -> bool:
    if user_id == target_id:
        return False
    user = get_profile(user_id)
    target = get_profile(target_id)
    if not user or not target:
        return False
    if not user["active"] or not target["active"]:
        return False
    if age_group(user["age"]) != age_group(target["age"]):
        return False
    block = db.execute(
        """
        SELECT 1 FROM blocks
        WHERE (user_id = ? AND blocked_id = ?)
           OR (user_id = ? AND blocked_id = ?)
        """,
        (user_id, target_id, target_id, user_id),
    ).fetchone()
    return block is None


async def notify_admin(bot: Bot, text: str):
    if not ADMIN_ID:
        return
    try:
        await bot.send_message(ADMIN_ID, text)
    except TelegramAPIError:
        logging.exception("Не удалось отправить сообщение администратору")


# ============================================================
# ФУНКЦИИ ОПЛАТЫ YOOMONEY (ЮMONEY)
# ============================================================
def build_yoomoney_url(order_id: str, total_rub: int, title: str) -> str:
    params = {
        "receiver": YOOMONEY_WALLET,
        "quickpay-form": "button",
        "paymentType": "AC",
        "sum": str(total_rub),
        "label": order_id,
        "targets": f"{title} (#{order_id[:8]})",
    }
    return f"https://yoomoney.ru/quickpay/confirm?{urlencode(params)}"


async def verify_yoomoney_payment(
    order_id: str, expected_total: int
) -> str | None:
    url = "https://yoomoney.ru/api/operation-history"
    headers = {
        "Authorization": f"Bearer {YOOMONEY_TOKEN}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {"label": order_id, "records": "5"}

    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, data=data) as resp:
                if resp.status != 200:
                    return None
                result = await resp.json()
    except Exception:
        logging.exception("Ошибка при подключении к ЮMoney API")
        return None

    if "error" in result:
        return None

    for op in result.get("operations", []):
        amount_received = float(op.get("amount", 0))
        if (
            op.get("status") == "success"
            and op.get("direction") == "in"
            and op.get("label") == order_id
            and amount_received >= expected_total * 0.95
        ):
            return str(op.get("operation_id"))
    return None


# ============================================================
# СОСТОЯНИЯ (FSM)
# ============================================================
class ProfileForm(StatesGroup):
    name = State()
    age = State()
    city = State()
    bio = State()
    photo = State()


class SuperMsgForm(StatesGroup):
    text = State()


class SupportForm(StatesGroup):
    message = State()


class BroadcastForm(StatesGroup):
    message = State()


# ============================================================
# ШАГИ АНКЕТЫ С КНОПКАМИ "НАЗАД" И "ОТМЕНА"
# ============================================================
async def prompt_step_name(event: Message | CallbackQuery, state: FSMContext):
    await state.set_state(ProfileForm.name)
    await send_screen(
        event,
        "✨ <b>Шаг 1 из 5 — Твоё имя</b>\n\n"
        "Как тебя называть? Напиши своё имя (от 2 до 30 символов):",
        reply_markup=buttons([[("❌ Отмена", "cancel_action")]]),
    )


async def prompt_step_age(event: Message | CallbackQuery, state: FSMContext):
    await state.set_state(ProfileForm.age)
    await send_screen(
        event,
        "🎂 <b>Шаг 2 из 5 — Возраст</b>\n\n"
        "Сколько тебе лет? Введи число от <b>14</b> до <b>80</b>:",
        reply_markup=buttons(
            [[("⬅️ Назад", "reg_back:name"), ("❌ Отмена", "cancel_action")]]
        ),
    )


async def prompt_step_city(event: Message | CallbackQuery, state: FSMContext):
    await state.set_state(ProfileForm.city)
    await send_screen(
        event,
        "📍 <b>Шаг 3 из 5 — Город</b>\n\n"
        "В каком городе ты живёшь? Напиши название:",
        reply_markup=buttons(
            [[("⬅️ Назад", "reg_back:age"), ("❌ Отмена", "cancel_action")]]
        ),
    )


async def prompt_step_bio(event: Message | CallbackQuery, state: FSMContext):
    await state.set_state(ProfileForm.bio)
    await send_screen(
        event,
        "📝 <b>Шаг 4 из 5 — О себе</b>\n\n"
        "Расскажи о своих интересах, хобби и кого хочешь найти (от 5 до 300 символов).\n\n"
        "<i>🔒 Не указывай телефон и домашний адрес в открытом описании.</i>",
        reply_markup=buttons(
            [[("⬅️ Назад", "reg_back:city"), ("❌ Отмена", "cancel_action")]]
        ),
    )


async def prompt_step_photo(event: Message | CallbackQuery, state: FSMContext):
    await state.set_state(ProfileForm.photo)
    await send_screen(
        event,
        "📸 <b>Шаг 5 из 5 — Фотография</b>\n\n"
        "Отправь своё лучшее фото! Анкеты с фотографией получают в 5 раз больше симпатий.\n\n"
        "<i>Или нажми «Пропустить фото»:</i>",
        reply_markup=buttons(
            [
                [("⏭ Пропустить фото", "profile:skip_photo")],
                [("⬅️ Назад", "reg_back:bio"), ("❌ Отмена", "cancel_action")],
            ]
        ),
    )


# ============================================================
# СТАРТ, ОТМЕНА И РЕГИСТРАЦИЯ
# ============================================================
@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    profile = get_profile(user_id)
    if profile:
        if profile["username"] != message.from_user.username:
            db.execute(
                "UPDATE profiles SET username = ? WHERE user_id = ?",
                (message.from_user.username, user_id),
            )
            db.commit()
        name = html.escape(profile["name"])
        inc = count_incoming_likes(user_id)
        inc_text = (
            f"\n🔥 <b>Тебя лайкнули: {inc} чел.!</b> Проверь раздел «👀 Кто меня лайкнул».\n"
            if inc > 0
            else ""
        )
        await send_screen(
            message,
            f"✨ <b>С возвращением, {name}!</b>\n"
            f"{inc_text}\n"
            "Выбирай нужный раздел в меню ниже 👇",
            reply_markup=main_menu(user_id),
        )
        return

    await send_screen(
        message,
        "💘 <b>Добро пожаловать в бот знакомств!</b>\n\n"
        "Здесь ты легко найдёшь новых друзей, компанию для прогулок или вторую половинку.\n\n"
        "<blockquote>"
        "✨ Удобный интерфейс без лишнего спама\n"
        "💌 Контакты открываются при взаимной симпатии или Супер-письме\n"
        "🎁 <b>Бонус:</b> 1 бесплатное Супер-письмо сразу после регистрации!\n"
        "🔒 Безопасность: пользователи 14–17 и 18+ строго разделены"
        "</blockquote>\n\n"
        "Нажми кнопку ниже, чтобы создать анкету:",
        reply_markup=buttons(
            [
                [("✨ Создать анкету", "profile:create")],
                [VPN_BUTTON],
            ]
        ),
    )


@dp.message(Command("cancel"))
async def cancel_cmd(message: Message, state: FSMContext):
    await state.clear()
    await send_screen(
        message,
        "❌ <b>Действие отменено.</b>\nВыбери нужный раздел:",
        reply_markup=main_menu(message.from_user.id),
    )


@dp.callback_query(F.data == "cancel_action")
async def cancel_cb(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("❌ Отменено")
    profile = get_profile(callback.from_user.id)
    if profile:
        await send_screen(
            callback,
            "🏠 <b>Главное меню</b>\nДействие отменено. Выбирай раздел 👇",
            reply_markup=main_menu(callback.from_user.id),
        )
    else:
        await send_screen(
            callback,
            "💘 <b>Создание анкеты отменено.</b>\nНажми кнопку ниже, когда будешь готов(а) начать:",
            reply_markup=buttons(
                [[("✨ Создать анкету", "profile:create")], [VPN_BUTTON]]
            ),
        )


@dp.callback_query(F.data == "profile:create")
async def create_profile(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await prompt_step_name(callback, state)


@dp.callback_query(F.data.startswith("reg_back:"))
async def reg_back(callback: CallbackQuery, state: FSMContext):
    target_step = callback.data.split(":", 1)[1]
    await callback.answer("⬅️ Назад")
    if target_step == "name":
        await prompt_step_name(callback, state)
    elif target_step == "age":
        await prompt_step_age(callback, state)
    elif target_step == "city":
        await prompt_step_city(callback, state)
    elif target_step == "bio":
        await prompt_step_bio(callback, state)


@dp.message(ProfileForm.name)
async def form_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not 2 <= len(name) <= 30:
        await send_screen(
            message,
            "⚠️ <b>Имя должно содержать от 2 до 30 символов.</b>\nПопробуй ещё раз:",
            reply_markup=buttons([[("❌ Отмена", "cancel_action")]]),
        )
        return
    await state.update_data(name=name)
    await prompt_step_age(message, state)


@dp.message(ProfileForm.age)
async def form_age(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text.isdigit() or not 14 <= int(text) <= 80:
        await send_screen(
            message,
            "⚠️ <b>Введи корректный возраст числом от 14 до 80:</b>",
            reply_markup=buttons(
                [[("⬅️ Назад", "reg_back:name"), ("❌ Отмена", "cancel_action")]]
            ),
        )
        return
    await state.update_data(age=int(text))
    await prompt_step_city(message, state)


@dp.message(ProfileForm.city)
async def form_city(message: Message, state: FSMContext):
    city = (message.text or "").strip()
    if not 2 <= len(city) <= 50:
        await send_screen(
            message,
            "⚠️ <b>Название города должно быть от 2 до 50 символов.</b>\nВведи город ещё раз:",
            reply_markup=buttons(
                [[("⬅️ Назад", "reg_back:age"), ("❌ Отмена", "cancel_action")]]
            ),
        )
        return
    await state.update_data(city=city)
    await prompt_step_bio(message, state)


@dp.message(ProfileForm.bio)
async def form_bio(message: Message, state: FSMContext):
    bio = (message.text or "").strip()
    if not 5 <= len(bio) <= 300:
        await send_screen(
            message,
            "⚠️ <b>Описание должно содержать от 5 до 300 символов.</b>\nНапиши о себе чуть подробнее:",
            reply_markup=buttons(
                [[("⬅️ Назад", "reg_back:city"), ("❌ Отмена", "cancel_action")]]
            ),
        )
        return
    await state.update_data(bio=bio)
    await prompt_step_photo(message, state)


async def finish_profile_saving(
    event: Message | CallbackQuery, user, state: FSMContext, photo_id: str | None
):
    data = await state.get_data()
    user_id = user.id
    old_profile = get_profile(user_id)

    with db:
        if old_profile and age_group(old_profile["age"]) != age_group(
            data["age"]
        ):
            db.execute(
                "DELETE FROM reactions WHERE user_id = ? OR target_id = ?",
                (user_id, user_id),
            )
            db.execute(
                "DELETE FROM matches WHERE user1 = ? OR user2 = ?",
                (user_id, user_id),
            )

        db.execute(
            """
            INSERT INTO profiles
                (user_id, username, name, age, city, bio, photo_id, active, superlikes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                name = excluded.name,
                age = excluded.age,
                city = excluded.city,
                bio = excluded.bio,
                photo_id = excluded.photo_id,
                active = 1
            """,
            (
                user_id,
                user.username,
                data["name"],
                data["age"],
                data["city"],
                data["bio"],
                photo_id,
                now_iso(),
            ),
        )

    await state.clear()
    profile = get_profile(user_id)
    await send_screen(
        event,
        f"{profile_text(profile)}\n\n✅ <b>Твоя анкета успешно сохранена!</b>",
        reply_markup=main_menu(user_id),
        photo_id=profile["photo_id"],
    )


@dp.message(ProfileForm.photo, F.photo)
async def form_photo_received(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    await finish_profile_saving(message, message.from_user, state, photo_id)


@dp.callback_query(ProfileForm.photo, F.data == "profile:skip_photo")
async def form_photo_skipped(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await finish_profile_saving(callback, callback.from_user, state, None)


@dp.message(ProfileForm.photo)
async def form_photo_invalid(message: Message):
    await send_screen(
        message,
        "⚠️ <b>Пожалуйста, отправь фотографию как картинку</b> (не файлом) или нажми «Пропустить фото»:",
        reply_markup=buttons(
            [
                [("⏭ Пропустить фото", "profile:skip_photo")],
                [("⬅️ Назад", "reg_back:bio"), ("❌ Отмена", "cancel_action")],
            ]
        ),
    )


# ============================================================
# ПОИСК АНКЕТ, ЛАЙКИ, СУПЕР-ПИСЬМА И МЭТЧИ
# ============================================================
async def show_candidate(event: Message | CallbackQuery, viewer_id: int):
    viewer = get_profile(viewer_id)
    if not viewer:
        await send_screen(
            event,
            "Сначала создай свою анкету!",
            reply_markup=buttons(
                [[("✨ Создать анкету", "profile:create")], [VPN_BUTTON]]
            ),
        )
        return
    if not viewer["active"]:
        await send_screen(
            event,
            "🙈 <b>Твоя анкета сейчас скрыта.</b>\n"
            "Включи показ анкеты в настройках, чтобы смотреть других пользователей.",
            reply_markup=main_menu(viewer_id),
        )
        return

    min_age, max_age = (14, 17) if viewer["age"] < 18 else (18, 80)
    current_ts = now_ts()

    candidate = db.execute(
        """
        SELECT p.*
        FROM profiles p
        WHERE p.user_id != ?
          AND p.active = 1
          AND p.age BETWEEN ? AND ?
          AND NOT EXISTS (
              SELECT 1 FROM reactions r
              WHERE r.user_id = ? AND r.target_id = p.user_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM blocks b
              WHERE (b.user_id = ? AND b.blocked_id = p.user_id)
                 OR (b.user_id = p.user_id AND b.blocked_id = ?)
          )
        ORDER BY
            CASE WHEN p.boost_until > ? THEN 0 ELSE 1 END,
            CASE WHEN p.vip_until > ? THEN 0 ELSE 1 END,
            RANDOM()
        LIMIT 1
        """,
        (
            viewer_id,
            min_age,
            max_age,
            viewer_id,
            viewer_id,
            viewer_id,
            current_ts,
            current_ts,
        ),
    ).fetchone()

    if not candidate:
        await send_screen(
            event,
            "✨ <b>Ты просмотрел(а) все новые анкеты!</b>\n\n"
            "Хочешь снова посмотреть тех, кого пропустил(а) ранее, или поднять свою анкету в ТОП?",
            reply_markup=buttons(
                [
                    [("🔄 Сбросить пропущенные анкеты", "buy_info:reset_skips")],
                    [
                        ("🚀 Поднять анкету в ТОП", "boost"),
                        ("👑 Магазин & VIP", "shop"),
                    ],
                    [VPN_BUTTON],
                    [("⬅️ Назад в меню", "menu")],
                ]
            ),
        )
        return

    await send_screen(
        event,
        profile_text(candidate),
        reply_markup=candidate_menu(candidate["user_id"], viewer["superlikes"]),
        photo_id=candidate["photo_id"],
    )


@dp.callback_query(F.data == "browse")
async def browse(callback: CallbackQuery):
    await callback.answer()
    await show_candidate(callback, callback.from_user.id)


@dp.callback_query(F.data.startswith("skip:"))
async def skip(callback: CallbackQuery):
    user_id = callback.from_user.id
    target_id = int(callback.data.split(":")[1])

    if pair_allowed(user_id, target_id):
        db.execute(
            """
            INSERT OR IGNORE INTO reactions (user_id, target_id, action, created_at)
            VALUES (?, ?, 'skip', ?)
            """,
            (user_id, target_id, now_iso()),
        )
        db.commit()

    await callback.answer("⏭ Следующая анкета")
    await show_candidate(callback, user_id)


@dp.callback_query(F.data.startswith("like:"))
async def like(callback: CallbackQuery):
    user_id = callback.from_user.id
    target_id = int(callback.data.split(":")[1])

    viewer = get_profile(user_id)
    if not viewer or not pair_allowed(user_id, target_id):
        await callback.answer("Анкета уже недоступна.", show_alert=True)
        await show_candidate(callback, user_id)
        return

    # Проверка дневного лимита лайков для бесплатных аккаунтов
    if not is_vip(viewer) and get_today_likes_count(user_id) >= FREE_DAILY_LIKES:
        await callback.answer("🛑 Дневной лимит лайков исчерпан!", show_alert=True)
        await send_screen(
            callback,
            f"🛑 <b>Дневной лимит ({FREE_DAILY_LIKES} лайков) исчерпан!</b>\n\n"
            "Сними все ограничения с <b>👑 VIP-статусом</b>:\n"
            "• ♾ <b>Безлимитные лайки</b> каждый день\n"
            "• 👀 Просмотр всех, <b>кто тебя лайкнул</b>\n"
            "• 👑 Золотой значок VIP и приоритет в поиске\n"
            "• 💌 Бонусные Супер-письма",
            reply_markup=buttons(
                [
                    [("👑 Подключить VIP-статус", "shop")],
                    [VPN_BUTTON],
                    [("⬅️ Назад в меню", "menu")],
                ]
            ),
        )
        return

    new_match = False
    with db:
        db.execute(
            """
            INSERT OR IGNORE INTO reactions (user_id, target_id, action, created_at)
            VALUES (?, ?, 'like', ?)
            """,
            (user_id, target_id, now_iso()),
        )
        own_reaction = db.execute(
            "SELECT action FROM reactions WHERE user_id = ? AND target_id = ?",
            (user_id, target_id),
        ).fetchone()

        if own_reaction["action"] != "like":
            await callback.answer("Для этой анкеты уже выбран ответ.")
            await show_candidate(callback, user_id)
            return

        reciprocal = db.execute(
            """
            SELECT 1 FROM reactions
            WHERE user_id = ? AND target_id = ? AND action = 'like'
            """,
            (target_id, user_id),
        ).fetchone()

        if reciprocal:
            user1, user2 = sorted((user_id, target_id))
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO matches (user1, user2, created_at)
                VALUES (?, ?, ?)
                """,
                (user1, user2, now_iso()),
            )
            new_match = cursor.rowcount == 1

    await callback.answer("❤️ Лайк отправлен!")

    if new_match:
        target = get_profile(target_id)
        current = get_profile(user_id)

        try:
            await callback.bot.send_message(
                target_id,
                f"💘 <b>У вас взаимная симпатия!</b>\n\n"
                f"Вы понравились друг другу с: {user_contact_link(current)}\n"
                f"📍 Город: <i>{html.escape(current['city'])}</i>\n\n"
                f"👉 Нажми на имя, чтобы начать общение!",
                reply_markup=buttons(
                    [[("💌 Мои симпатии", "matches")], [VPN_BUTTON]]
                ),
            )
        except TelegramAPIError:
            pass

        await send_screen(
            callback,
            f"💘 <b>Ура! Взаимная симпатия!</b>\n\n"
            f"Тебе ответил(а) взаимностью: {user_contact_link(target)}\n"
            f"📍 Город: <i>{html.escape(target['city'])}</i>\n\n"
            f"👉 Нажми на имя выше, чтобы написать сообщение, или продолжай смотреть анкеты!",
            reply_markup=buttons(
                [
                    [("🔥 Смотреть дальше", "browse")],
                    [("💌 Все взаимные симпатии", "matches")],
                    [VPN_BUTTON],
                    [("⬅️ Назад в меню", "menu")],
                ]
            ),
            photo_id=target["photo_id"],
        )
        return
    else:
        # ТРИГГЕР ПРОДАЖ: уведомляем второго пользователя, что его кто-то лайкнул!
        try:
            await callback.bot.send_message(
                target_id,
                "🔥 <b>Кому-то понравилась твоя анкета!</b>\n\n"
                "Открой раздел <b>«👀 Кто меня лайкнул»</b>, чтобы сразу увидеть поклонника без ожидания!",
                reply_markup=buttons(
                    [
                        [("👀 Узнать, кто лайкнул", "who_liked")],
                        [("🔥 Искать в анкетах", "browse")],
                    ]
                ),
            )
        except TelegramAPIError:
            pass

    await show_candidate(callback, user_id)


# ============================================================
# СУПЕР-ПИСЬМО (СООБЩЕНИЕ БЕЗ ВЗАИМНОСТИ)
# ============================================================
@dp.callback_query(F.data.startswith("supermsg:"))
async def supermsg_start(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    target_id = int(callback.data.split(":")[1])
    viewer = get_profile(user_id)
    target = get_profile(target_id)

    if not viewer or not target or not pair_allowed(user_id, target_id):
        await callback.answer("Анкета недоступна.", show_alert=True)
        return

    if viewer["superlikes"] < 1:
        await callback.answer("У тебя закончились Супер-письма!", show_alert=True)
        await send_screen(
            callback,
            "💌 <b>Супер-письма закончились!</b>\n\n"
            "С помощью Супер-письма ты можешь написать человеку <b>напрямую без ожидания взаимного лайка</b> — "
            "он сразу получит твой текст и ссылку на твой профиль!\n\n"
            "Пополни запас Супер-писем или подключи 👑 VIP:",
            reply_markup=buttons(
                [
                    [("💌 Купить 5 Супер-писем", "buy_info:super_5")],
                    [("👑 Магазин & VIP", "shop")],
                    [("⬅️ Назад к анкетам", "browse"), ("❌ Отмена", "menu")],
                ]
            ),
        )
        return

    await state.set_state(SuperMsgForm.text)
    await state.update_data(target_id=target_id)
    await callback.answer()
    await send_screen(
        callback,
        f"💌 <b>Супер-письмо для {html.escape(target['name'])}</b>\n"
        f"<i>Доступно писем: {viewer['superlikes']} шт.</i>\n\n"
        "Напиши сообщение (до 250 символов). Получатель сразу увидит его вместе со ссылкой на твой Telegram:",
        reply_markup=buttons(
            [[("⬅️ Назад к анкетам", "browse"), ("❌ Отмена", "cancel_action")]]
        ),
    )


@dp.message(SuperMsgForm.text)
async def supermsg_send(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not 2 <= len(text) <= 250:
        await send_screen(
            message,
            "⚠️ <b>Сообщение должно быть от 2 до 250 символов.</b>\nНапиши текст ещё раз:",
            reply_markup=buttons(
                [
                    [
                        ("⬅️ Назад к анкетам", "browse"),
                        ("❌ Отмена", "cancel_action"),
                    ]
                ]
            ),
        )
        return

    data = await state.get_data()
    target_id = data.get("target_id")
    user_id = message.from_user.id
    viewer = get_profile(user_id)
    target = get_profile(target_id)

    await state.clear()
    if (
        not viewer
        or not target
        or viewer["superlikes"] < 1
        or not pair_allowed(user_id, target_id)
    ):
        await send_screen(
            message,
            "Не удалось отправить письмо.",
            reply_markup=main_menu(user_id),
        )
        return

    with db:
        db.execute(
            "UPDATE profiles SET superlikes = superlikes - 1 WHERE user_id = ? AND superlikes > 0",
            (user_id,),
        )
        db.execute(
            """
            INSERT OR REPLACE INTO reactions (user_id, target_id, action, created_at)
            VALUES (?, ?, 'like', ?)
            """,
            (user_id, target_id, now_iso()),
        )

    try:
        await message.bot.send_message(
            target_id,
            f"💌 <b>Тебе пришло Супер-письмо!</b>\n\n"
            f"От: {user_contact_link(viewer)}, {viewer['age']} (г. {html.escape(viewer['city'])})\n\n"
            f"<blockquote>💬 {html.escape(text)}</blockquote>\n\n"
            f"👉 Нажми на имя отправителя, чтобы ответить в личные сообщения!",
            reply_markup=buttons(
                [[("🔥 Смотреть анкеты", "browse")], [VPN_BUTTON]]
            ),
        )
    except TelegramAPIError:
        pass

    await send_screen(
        message,
        f"✅ <b>Супер-письмо для {html.escape(target['name'])} доставлено!</b>\n"
        "Твой контакт открыт получателю. Продолжим поиск?",
        reply_markup=buttons(
            [
                [("🔥 Смотреть анкеты дальше", "browse")],
                [("⬅️ Назад в меню", "menu")],
            ]
        ),
    )


# ============================================================
# РАЗДЕЛ "КТО МЕНЯ ЛАЙКНУЛ" (ПЛАТНАЯ ФИЧА VIP) И МЭТЧИ
# ============================================================
@dp.callback_query(F.data == "who_liked")
async def who_liked(callback: CallbackQuery):
    user_id = callback.from_user.id
    viewer = get_profile(user_id)
    await callback.answer()

    if not viewer:
        await send_screen(
            callback,
            "Сначала создай анкету!",
            reply_markup=buttons(
                [[("✨ Создать анкету", "profile:create")]]
            ),
        )
        return

    inc_count = count_incoming_likes(user_id)

    if not is_vip(viewer):
        teaser = (
            f"🔥 <b>Сейчас тобой интересуются: {inc_count} чел.!</b>\n\n"
            if inc_count > 0
            else "👀 <b>Узнавай первым, кому ты нравишься!</b>\n\n"
        )
        await send_screen(
            callback,
            f"{teaser}"
            "Раздел <b>«👀 Кто меня лайкнул»</b> доступен обладателям <b>👑 VIP-статуса</b>.\n\n"
            "<blockquote>"
            "👑 Мгновенный доступ к контактам всех, кто поставил тебе ❤️\n"
            "♾ Безлимитные лайки без дневных ограничений\n"
            "🔝 Приоритетный показ твоей анкеты в поиске\n"
            "💌 Бонусные Супер-письма"
            "</blockquote>",
            reply_markup=buttons(
                [
                    [("👑 Подключить VIP-статус", "shop")],
                    [VPN_BUTTON],
                    [("⬅️ Назад в меню", "menu")],
                ]
            ),
        )
        return

    min_age, max_age = (14, 17) if viewer["age"] < 18 else (18, 80)
    rows = db.execute(
        """
        SELECT p.*
        FROM reactions r
        JOIN profiles p ON p.user_id = r.user_id
        WHERE r.target_id = ?
          AND r.action = 'like'
          AND p.active = 1
          AND p.age BETWEEN ? AND ?
          AND NOT EXISTS (
              SELECT 1 FROM blocks b
              WHERE (b.user_id = ? AND b.blocked_id = p.user_id)
                 OR (b.user_id = p.user_id AND b.blocked_id = ?)
          )
        ORDER BY r.created_at DESC
        LIMIT 25
        """,
        (user_id, min_age, max_age, user_id, user_id),
    ).fetchall()

    if not rows:
        await send_screen(
            callback,
            "👀 <b>Пока новых входящих лайков нет.</b>\n\n"
            "Подними анкету в ТОП, чтобы получить максимум внимания!",
            reply_markup=buttons(
                [
                    [("🚀 Поднять анкету в ТОП", "boost")],
                    [("⬅️ Назад в меню", "menu")],
                ]
            ),
        )
        return

    lines = ["👑 <b>Люди, которым ты понравился(ась):</b>\n"]
    for idx, prof in enumerate(rows, 1):
        lines.append(
            f"{idx}. {user_contact_link(prof)}, {prof['age']} — <i>г. {html.escape(prof['city'])}</i>"
        )
    lines.append("\n👉 <i>Нажми на любое имя, чтобы сразу написать!</i>")

    await send_screen(
        callback,
        "\n".join(lines),
        reply_markup=buttons(
            [
                [("🔥 Смотреть анкеты", "browse")],
                [VPN_BUTTON],
                [("⬅️ Назад в меню", "menu")],
            ]
        ),
    )


@dp.callback_query(F.data == "matches")
async def matches(callback: CallbackQuery):
    user_id = callback.from_user.id
    rows = db.execute(
        """
        SELECT p.*
        FROM matches m
        JOIN profiles p ON p.user_id =
            CASE WHEN m.user1 = ? THEN m.user2 ELSE m.user1 END
        WHERE (m.user1 = ? OR m.user2 = ?)
          AND p.active = 1
        ORDER BY m.created_at DESC
        LIMIT 30
        """,
        (user_id, user_id, user_id),
    ).fetchall()

    visible = [row for row in rows if pair_allowed(user_id, row["user_id"])]
    await callback.answer()

    if not visible:
        await send_screen(
            callback,
            "💌 <b>Список взаимных симпатий пока пуст</b>\n\n"
            "Смотри анкеты и ставь ❤️ — как только кто-то ответит взаимностью, "
            "здесь появится прямая ссылка на его Telegram!",
            reply_markup=buttons(
                [
                    [("🔥 Смотреть анкеты", "browse")],
                    [VPN_BUTTON],
                    [("⬅️ Назад в меню", "menu")],
                ]
            ),
        )
        return

    lines = ["💌 <b>Твои взаимные симпатии:</b>\n"]
    for index, profile in enumerate(visible, 1):
        city = html.escape(profile["city"])
        lines.append(
            f"{index}. {user_contact_link(profile)}, {profile['age']} — <i>г. {city}</i>"
        )

    await send_screen(
        callback,
        "\n".join(lines),
        reply_markup=buttons(
            [
                [("🔥 Смотреть анкеты", "browse")],
                [VPN_BUTTON],
                [("⬅️ Назад в меню", "menu")],
            ]
        ),
    )


# ============================================================
# МАГАЗИН ПРИВИЛЕГИЙ, VIP, БУСТ И ОПЛАТА С КОМИССИЕЙ 30%
# ============================================================
@dp.callback_query(F.data == "shop")
async def shop_menu(callback: CallbackQuery):
    user_id = callback.from_user.id
    profile = get_profile(user_id)
    await callback.answer()

    if not profile:
        await send_screen(
            callback,
            "Сначала создай анкету!",
            reply_markup=buttons(
                [[("✨ Создать анкету", "profile:create")]]
            ),
        )
        return

    vip_status = (
        f"🟢 Активен до {datetime.fromtimestamp(profile['vip_until'], timezone.utc).strftime('%d.%m.%Y %H:%M')} UTC"
        if is_vip(profile)
        else "🔴 Не подключён"
    )

    _, _, p_vip7 = calc_price_with_commission(PRODUCTS["vip_7"]["base_price"])
    _, _, p_vip30 = calc_price_with_commission(PRODUCTS["vip_30"]["base_price"])
    _, _, p_boost = calc_price_with_commission(
        PRODUCTS["boost_24"]["base_price"]
    )
    _, _, p_super = calc_price_with_commission(
        PRODUCTS["super_5"]["base_price"]
    )
    _, _, p_reset = calc_price_with_commission(
        PRODUCTS["reset_skips"]["base_price"]
    )

    await send_screen(
        callback,
        "👑 <b>Магазин привилегий & VIP</b>\n\n"
        f"Твой статус VIP: <b>{vip_status}</b>\n"
        f"Доступно Супер-писем: <b>{profile['superlikes']} шт.</b>\n\n"
        "<blockquote>"
        "👑 <b>VIP-статус</b> открывает просмотр всех, кто тебя лайкнул, даёт безлимитные лайки, корону в профиле и бонусные Супер-письма!\n"
        "💌 <b>Супер-письма</b> позволяют писать людям без ожидания взаимности.\n"
        "🚀 <b>Поднятие в ТОП</b> ставит твою анкету на 1-е место в поиске."
        "</blockquote>\n"
        "Выбери нужную услугу ниже 👇",
        reply_markup=buttons(
            [
                [(f"👑 VIP на 7 дней — {p_vip7} ₽", "buy_info:vip_7")],
                [(f"🔥 VIP на 30 дней (ХИТ) — {p_vip30} ₽", "buy_info:vip_30")],
                [(f"🚀 Поднять в ТОП (24ч) — {p_boost} ₽", "buy_info:boost_24")],
                [(f"💌 5 Супер-писем — {p_super} ₽", "buy_info:super_5")],
                [
                    (
                        f"🔄 Сброс пропущенных — {p_reset} ₽",
                        "buy_info:reset_skips",
                    )
                ],
                [VPN_BUTTON],
                [("⬅️ Назад в меню", "menu")],
            ]
        ),
    )


@dp.callback_query(F.data == "boost")
async def boost_shortcut(callback: CallbackQuery):
    await callback.answer()
    await show_product_details(callback, "boost_24")


@dp.callback_query(F.data.startswith("buy_info:"))
async def buy_info_handler(callback: CallbackQuery):
    product_key = callback.data.split(":", 1)[1]
    await callback.answer()
    await show_product_details(callback, product_key)


async def show_product_details(callback: CallbackQuery, product_key: str):
    profile = get_profile(callback.from_user.id)
    if not profile:
        await send_screen(
            callback,
            "Сначала создай анкету.",
            reply_markup=buttons(
                [[("✨ Создать анкету", "profile:create")]]
            ),
        )
        return
    if profile["age"] < 18:
        await send_screen(
            callback,
            "🔒 Платные функции доступны только пользователям старше 18 лет.",
            reply_markup=buttons([[("⬅️ Назад", "menu")]]),
        )
        return

    product = PRODUCTS.get(product_key)
    if not product:
        return

    base, comm, total = calc_price_with_commission(product["base_price"])

    await send_screen(
        callback,
        f"💎 <b>{product['title']}</b>\n\n"
        f"<blockquote>{product['desc']}</blockquote>\n\n"
        f"🧾 <b>Расчёт стоимости:</b>\n"
        f"• Стоимость услуги: <b>{base} ₽</b>\n"
        f"• Комиссия сервиса ({COMMISSION_PERCENT}%): <b>+{comm} ₽</b>\n"
        f"• Итого к оплате: <b>{total} ₽</b>\n\n"
        f"<i>Оплата банковской картой РФ или кошельком ЮMoney. Покупка разовая, без автосписаний.</i>",
        reply_markup=buttons(
            [
                [(f"💳 Перейти к оплате ({total} ₽)", f"create_order:{product_key}")],
                [("⬅️ Назад в магазин", "shop"), ("❌ Отмена", "menu")],
            ]
        ),
    )


@dp.callback_query(F.data.startswith("create_order:"))
async def create_order_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    product_key = callback.data.split(":", 1)[1]
    profile = get_profile(user_id)
    product = PRODUCTS.get(product_key)

    if not product or not profile or profile["age"] < 18:
        await callback.answer("Недоступно.", show_alert=True)
        return

    if not PAYMENTS_ENABLED or not YOOMONEY_WALLET or not YOOMONEY_TOKEN:
        await callback.answer(
            "Платежи временно настраиваются администратором.", show_alert=True
        )
        return

    base, comm, total = calc_price_with_commission(product["base_price"])
    order_id = secrets.token_hex(12)

    db.execute(
        """
        INSERT INTO orders
            (order_id, user_id, product, base_amount, commission, amount, currency, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'RUB', 'pending', ?)
        """,
        (order_id, user_id, product_key, base, comm, total, now_iso()),
    )
    db.commit()

    pay_url = build_yoomoney_url(order_id, total, product["title"])
    await callback.answer()
    await send_screen(
        callback,
        f"🧾 <b>Счёт #{order_id[:8]} сформирован!</b>\n\n"
        f"Услуга: <b>{product['title']}</b>\n"
        f"Базовая цена: {base} ₽ + комиссия {COMMISSION_PERCENT}% ({comm} ₽)\n"
        f"💰 <b>К оплате: {total} ₽</b>\n\n"
        "1️⃣ Нажми <b>«💳 Оплатить»</b> и заверши перевод на странице ЮMoney.\n"
        "2️⃣ Вернись сюда и нажми <b>«🔄 Я оплатил(а) — Проверить»</b>.",
        reply_markup=buttons(
            [
                [(f"💳 Оплатить {total} ₽ (Карта / ЮMoney)", pay_url)],
                [("🔄 Я оплатил(а) — Проверить", f"check_pay:{order_id}")],
                [
                    ("⬅️ Назад", f"buy_info:{product_key}"),
                    ("❌ Отмена", "cancel_action"),
                ],
            ]
        ),
    )


@dp.callback_query(F.data.startswith("check_pay:"))
async def check_payment_callback(callback: CallbackQuery):
    order_id = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id

    order = db.execute(
        "SELECT * FROM orders WHERE order_id = ? AND user_id = ?",
        (order_id, user_id),
    ).fetchone()

    if not order:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    if order["status"] == "paid":
        await callback.answer("✅ Этот счёт уже оплачен!", show_alert=True)
        await send_screen(
            callback,
            "✅ <b>Заказ уже активирован!</b>",
            reply_markup=main_menu(user_id),
        )
        return

    operation_id = await verify_yoomoney_payment(order_id, order["amount"])
    if not operation_id:
        await callback.answer(
            "⏳ Платёж ещё не поступил.\n"
            "Если ты только что оплатил(а), подожди 10–20 секунд и нажми кнопку проверки снова.",
            show_alert=True,
        )
        return

    profile = get_profile(user_id)
    product_key = order["product"]
    reward_text = "Услуга успешно активирована!"

    try:
        with db:
            cursor = db.execute(
                """
                UPDATE orders
                SET status = 'paid', telegram_charge_id = ?
                WHERE order_id = ? AND status = 'pending'
                """,
                (operation_id, order_id),
            )
            if cursor.rowcount != 1:
                await callback.answer("Заказ уже обработан.")
                return

            current_ts = now_ts()
            if product_key == "vip_7":
                start_ts = max(current_ts, profile["vip_until"])
                new_vip = start_ts + 7 * 86400
                db.execute(
                    "UPDATE profiles SET vip_until = ?, superlikes = superlikes + 3 WHERE user_id = ?",
                    (new_vip, user_id),
                )
                reward_text = "👑 <b>VIP-статус на 7 дней</b> и <b>+3 Супер-письма</b> активированы!"

            elif product_key == "vip_30":
                start_ts = max(current_ts, profile["vip_until"])
                new_vip = start_ts + 30 * 86400
                db.execute(
                    "UPDATE profiles SET vip_until = ?, superlikes = superlikes + 10 WHERE user_id = ?",
                    (new_vip, user_id),
                )
                reward_text = "👑 <b>VIP-статус на 30 дней</b> и <b>+10 Супер-писем</b> активированы!"

            elif product_key == "boost_24":
                start_ts = max(current_ts, profile["boost_until"])
                new_boost = start_ts + 86400
                db.execute(
                    "UPDATE profiles SET boost_until = ? WHERE user_id = ?",
                    (new_boost, user_id),
                )
                reward_text = "🚀 <b>Поднятие анкеты в ТОП на 24 часа</b> активировано!"

            elif product_key == "super_5":
                db.execute(
                    "UPDATE profiles SET superlikes = superlikes + 5 WHERE user_id = ?",
                    (user_id,),
                )
                reward_text = "💌 На твой баланс начислено <b>+5 Супер-писем</b>!"

            elif product_key == "reset_skips":
                db.execute(
                    "DELETE FROM reactions WHERE user_id = ? AND action = 'skip'",
                    (user_id,),
                )
                reward_text = "🔄 <b>Все пропущенные анкеты сброшены!</b> Теперь ты снова увидишь их в поиске."

    except sqlite3.IntegrityError:
        await callback.answer("Этот платёж уже был учтён.")
        return

    await callback.answer("🎉 Оплата подтверждена!")
    await notify_admin(
        callback.bot,
        f"💰 <b>Новая продажа!</b>\n"
        f"Услуга: {PRODUCTS.get(product_key, {}).get('title', product_key)}\n"
        f"Сумма: <b>{order['amount']} ₽</b> (вкл. комиссию {order['commission']} ₽)\n"
        f"Пользователь: <code>{user_id}</code>",
    )
    await send_screen(
        callback,
        f"🎉 <b>Оплата прошла успешно!</b>\n\n{reward_text}",
        reply_markup=main_menu(user_id),
    )


# ============================================================
# ЖАЛОБЫ И БЛОКИРОВКИ
# ============================================================
@dp.callback_query(F.data.startswith("report:"))
async def report(callback: CallbackQuery):
    user_id = callback.from_user.id
    target_id = int(callback.data.split(":")[1])

    if not pair_allowed(user_id, target_id):
        await callback.answer("Анкета недоступна.", show_alert=True)
        await show_candidate(callback, user_id)
        return

    with db:
        db.execute(
            "INSERT INTO reports (reporter_id, target_id, created_at) VALUES (?, ?, ?)",
            (user_id, target_id, now_iso()),
        )
        db.execute(
            """
            INSERT OR IGNORE INTO reactions (user_id, target_id, action, created_at)
            VALUES (?, ?, 'report', ?)
            """,
            (user_id, target_id, now_iso()),
        )

    await callback.answer("🚩 Жалоба отправлена модератору", show_alert=True)
    target = get_profile(target_id)
    if target:
        await notify_admin(
            callback.bot,
            f"🚩 <b>Жалоба на анкету</b>\n"
            f"От: <code>{user_id}</code> | На: <code>{target_id}</code> ({html.escape(target['name'])})\n"
            f"Текст: {html.escape(target['bio'])}",
        )
    await show_candidate(callback, user_id)


@dp.callback_query(F.data.startswith("block:"))
async def block(callback: CallbackQuery):
    user_id = callback.from_user.id
    target_id = int(callback.data.split(":")[1])

    if user_id != target_id and get_profile(target_id):
        user1, user2 = sorted((user_id, target_id))
        with db:
            db.execute(
                "INSERT OR IGNORE INTO blocks (user_id, blocked_id, created_at) VALUES (?, ?, ?)",
                (user_id, target_id, now_iso()),
            )
            db.execute(
                "DELETE FROM matches WHERE user1 = ? AND user2 = ?",
                (user1, user2),
            )

    await callback.answer("⛔ Пользователь заблокирован")
    await show_candidate(callback, user_id)


# ============================================================
# МОЯ АНКЕТА, НАСТРОЙКИ И БЕЗОПАСНОСТЬ
# ============================================================
@dp.callback_query(F.data == "my_profile")
async def my_profile(callback: CallbackQuery):
    user_id = callback.from_user.id
    profile = get_profile(user_id)
    await callback.answer()
    if not profile:
        await send_screen(
            callback,
            "У тебя пока нет анкеты.",
            reply_markup=buttons(
                [[("✨ Создать анкету", "profile:create")], [VPN_BUTTON]]
            ),
        )
        return

    status = "🟢 Видна в поиске" if profile["active"] else "🔴 Скрыта из поиска"
    vip_line = "👑 VIP активен" if is_vip(profile) else "Обычный аккаунт"
    extra = (
        f"Статус: <b>{status}</b>\n"
        f"Привилегии: <b>{vip_line}</b> | 💌 Супер-писем: <b>{profile['superlikes']}</b>"
    )

    await send_screen(
        callback,
        f"{profile_text(profile)}\n\n{extra}",
        reply_markup=buttons(
            [
                [
                    ("✏️ Изменить анкету", "profile:create"),
                    ("👑 Магазин & VIP", "shop"),
                ],
                [VPN_BUTTON],
                [("⬅️ Назад в меню", "menu")],
            ]
        ),
        photo_id=profile["photo_id"],
    )


@dp.callback_query(F.data == "settings")
async def settings(callback: CallbackQuery):
    profile = get_profile(callback.from_user.id)
    await callback.answer()
    if not profile:
        await send_screen(
            callback,
            "Сначала создай анкету.",
            reply_markup=buttons(
                [[("✨ Создать анкету", "profile:create")]]
            ),
        )
        return

    toggle_text = (
        "🙈 Скрыть мою анкету"
        if profile["active"]
        else "👁 Показать мою анкету"
    )
    await send_screen(
        callback,
        "⚙️ <b>Настройки профиля</b>\n\n"
        "Здесь можно временно скрыть анкету из поиска, перезаполнить её или удалить:",
        reply_markup=buttons(
            [
                [(toggle_text, "profile:toggle")],
                [
                    ("✏️ Заполнить заново", "profile:create"),
                    ("🗑 Удалить анкету", "profile:delete_ask"),
                ],
                [VPN_BUTTON],
                [("⬅️ Назад в меню", "menu")],
            ]
        ),
    )


@dp.callback_query(F.data == "profile:toggle")
async def toggle_profile(callback: CallbackQuery):
    user_id = callback.from_user.id
    profile = get_profile(user_id)
    if not profile:
        await callback.answer("Анкета не найдена.")
        return

    new_value = 0 if profile["active"] else 1
    db.execute(
        "UPDATE profiles SET active = ? WHERE user_id = ?", (new_value, user_id)
    )
    db.commit()
    await callback.answer(
        "🟢 Анкета включена" if new_value else "🙈 Анкета скрыта"
    )
    await settings(callback)


@dp.callback_query(F.data == "profile:delete_ask")
async def delete_ask(callback: CallbackQuery):
    await callback.answer()
    await send_screen(
        callback,
        "⚠️ <b>Ты точно хочешь удалить свою анкету?</b>\n\n"
        "Все твои симпатии и матчи будут удалены безвозвратно.",
        reply_markup=buttons(
            [
                [("🗑 Да, удалить навсегда", "profile:delete_confirm")],
                [("⬅️ Назад", "settings"), ("❌ Отмена", "menu")],
            ]
        ),
    )


@dp.callback_query(F.data == "profile:delete_confirm")
async def delete_confirm(callback: CallbackQuery):
    user_id = callback.from_user.id
    with db:
        db.execute(
            "DELETE FROM reactions WHERE user_id = ? OR target_id = ?",
            (user_id, user_id),
        )
        db.execute(
            "DELETE FROM matches WHERE user1 = ? OR user2 = ?",
            (user_id, user_id),
        )
        db.execute(
            "DELETE FROM blocks WHERE user_id = ? OR blocked_id = ?",
            (user_id, user_id),
        )
        db.execute("DELETE FROM profiles WHERE user_id = ?", (user_id,))

    await callback.answer("Анкета удалена")
    await send_screen(
        callback,
        "🗑 <b>Твоя анкета полностью удалена.</b>\n\n"
        "Когда захочешь вернуться — нажми кнопку ниже!",
        reply_markup=buttons(
            [[("✨ Создать анкету", "profile:create")], [VPN_BUTTON]]
        ),
    )


@dp.callback_query(F.data == "safety")
async def safety(callback: CallbackQuery):
    await callback.answer()
    await send_screen(
        callback,
        "🛡 <b>Правила безопасного общения</b>\n\n"
        "<blockquote>"
        "• Никогда не переводите незнакомцам деньги и не сообщайте коды из СМС.\n"
        "• Не публикуйте домашний адрес и данные документов.\n"
        "• Используйте кнопки «🚩 Жалоба» и «⛔ В ЧС» при подозрительном поведении.\n"
        "• Первые встречи назначайте только в людных общественных местах."
        "</blockquote>",
        reply_markup=buttons(
            [[VPN_BUTTON], [("⬅️ Назад в меню", "menu")]]
        ),
    )


@dp.callback_query(F.data == "menu")
async def menu_cb(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await send_screen(
        callback,
        "🏠 <b>Главное меню</b>\nВыбирай нужное действие 👇",
        reply_markup=main_menu(callback.from_user.id),
    )


# ============================================================
# ПОДДЕРЖКА И АДМИН-ПАНЕЛЬ ДЛЯ ВЛАДЕЛЬЦА ПРОЕКТА (/admin)
# ============================================================
@dp.message(Command("paysupport"))
async def payment_support(message: Message, state: FSMContext):
    if not ADMIN_ID:
        await send_screen(
            message,
            "Поддержка пока не настроена.",
            reply_markup=main_menu(message.from_user.id),
        )
        return

    await state.set_state(SupportForm.message)
    await send_screen(
        message,
        "💬 <b>Поддержка по платежам</b>\n\n"
        "Опиши свой вопрос или проблему одним сообщением (укажи номер счёта или время оплаты):",
        reply_markup=buttons(
            [[("⬅️ Назад", "menu"), ("❌ Отмена", "cancel_action")]]
        ),
    )


@dp.message(SupportForm.message)
async def process_support_message(message: Message, state: FSMContext):
    text = (message.text or message.caption or "").strip()
    if not text:
        return

    await state.clear()
    user = message.from_user
    username = f"@{user.username}" if user.username else "без username"

    await notify_admin(
        message.bot,
        f"💳 <b>Обращение в /paysupport</b>\n"
        f"От: <a href='tg://user?id={user.id}'>{html.escape(user.full_name)}</a> ({username}, ID: <code>{user.id}</code>)\n\n"
        f"<blockquote>{html.escape(text)}</blockquote>",
    )
    await send_screen(
        message,
        "✅ <b>Твоё обращение отправлено администратору!</b>\nОжидай ответа в ближайшее время.",
        reply_markup=main_menu(user.id),
    )


@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    total_users = db.execute("SELECT COUNT(*) as c FROM profiles").fetchone()[
        "c"
    ]
    vip_users = db.execute(
        "SELECT COUNT(*) as c FROM profiles WHERE vip_until > ?", (now_ts(),)
    ).fetchone()["c"]
    paid_stats = db.execute(
        """
        SELECT COUNT(*) as cnt,
               COALESCE(SUM(amount), 0) as total_rev,
               COALESCE(SUM(commission), 0) as total_comm
        FROM orders WHERE status = 'paid'
        """
    ).fetchone()

    await send_screen(
        message,
        "📊 <b>Панель управления владельца</b>\n\n"
        f"👥 Всего анкет в базе: <b>{total_users}</b>\n"
        f"👑 Активных VIP-подписок: <b>{vip_users}</b>\n\n"
        f"💰 <b>Финансовая статистика:</b>\n"
        f"• Оплаченных заказов: <b>{paid_stats['cnt']}</b>\n"
        f"• Общий оборот: <b>{paid_stats['total_rev']} ₽</b>\n"
        f"• Из них комиссия ({COMMISSION_PERCENT}%): <b>{paid_stats['total_comm']} ₽</b>",
        reply_markup=buttons(
            [
                [("📢 Сделать рассылку по базе", "admin:broadcast")],
                [("⬅️ В главное меню", "menu")],
            ]
        ),
    )


@dp.callback_query(F.data == "admin:broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await state.set_state(BroadcastForm.message)
    await callback.answer()
    await send_screen(
        callback,
        "📢 <b>Массовая рассылка всем пользователям</b>\n\n"
        "Отправь текст рекламного или информационного сообщения, которое получат все пользователи бота:",
        reply_markup=buttons([[("❌ Отмена", "cancel_action")]]),
    )


@dp.message(BroadcastForm.message)
async def admin_broadcast_send(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    text = (message.text or "").strip()
    if not text:
        return

    await state.clear()
    users = db.execute("SELECT user_id FROM profiles").fetchall()
    sent_count = 0

    for row in users:
        try:
            await message.bot.send_message(
                row["user_id"],
                text,
                reply_markup=buttons(
                    [[("🔥 Открыть бот", "menu")], [VPN_BUTTON]]
                ),
            )
            sent_count += 1
            await asyncio.sleep(0.05)
        except TelegramAPIError:
            pass

    await send_screen(
        message,
        f"✅ <b>Рассылка завершена!</b>\nУспешно доставлено: <b>{sent_count}</b> из {len(users)} пользователей.",
        reply_markup=main_menu(ADMIN_ID),
    )


# ============================================================
# ЗАПУСК БОТА
# ============================================================
async def main():
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        me = await bot.get_me()
        logging.info("✅ Успешный вход: @%s (ID: %s)", me.username, me.id)

        await bot.set_my_commands(
            [
                BotCommand(command="start", description="🏠 Главное меню"),
                BotCommand(command="cancel", description="❌ Отменить действие"),
                BotCommand(
                    command="paysupport", description="💬 Помощь с оплатой"
                ),
            ]
        )
        logging.info("🚀 Бот запущен и готов к работе!")
        await dp.start_polling(bot)

    except TelegramUnauthorizedError:
        masked = (
            f"{BOT_TOKEN[:6]}...{BOT_TOKEN[-4:]}"
            if len(BOT_TOKEN) > 10
            else BOT_TOKEN
        )
        logging.error(
            "❌ ОШИБКА АВТОРИЗАЦИИ: Telegram отклонил токен [%s].\n"
            "Проверь файл .env и настройки переменных (Variables) на хостинге!",
            masked,
        )
    finally:
        await bot.session.close()
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
```
