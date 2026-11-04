import asyncio
from datetime import datetime, timezone
import html
import logging
import os
import secrets
import sqlite3

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
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from dotenv import load_dotenv

# ============================================================
# НАСТРОЙКИ
# ============================================================
# override=True заставляет брать свежие данные из .env, даже если хостинг закешировал старые
load_dotenv(override=True)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip().strip('"').strip("'")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0").strip() or "0")
DB_PATH = os.getenv("DB_PATH", "dating_bot.sqlite3").strip()
BOOST_PRICE_STARS = int(os.getenv("BOOST_PRICE_STARS", "25").strip() or "25")
PAYMENTS_ENABLED = os.getenv("PAYMENTS_ENABLED", "0").strip() == "1"

if (
    not BOT_TOKEN
    or ":" not in BOT_TOKEN
    or "НОВЫЙ_ТОКЕН" in BOT_TOKEN
    or "вставь_сюда" in BOT_TOKEN
):
    raise SystemExit(
        "❌ ОШИБКА: Укажи настоящий BOT_TOKEN от @BotFather в файле .env или настройках хостинга!"
    )

if BOOST_PRICE_STARS < 1:
    raise SystemExit("❌ ОШИБКА: BOOST_PRICE_STARS должен быть больше нуля")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

dp = Dispatcher()
dp.message.filter(F.chat.type == "private")
dp.callback_query.filter(F.message.chat.type == "private")

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
        amount INTEGER NOT NULL,
        currency TEXT NOT NULL,
        status TEXT NOT NULL,
        telegram_charge_id TEXT UNIQUE,
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_profiles_search ON profiles(active, age, boost_until);
    CREATE INDEX IF NOT EXISTS idx_reactions_user ON reactions(user_id, target_id);
    CREATE INDEX IF NOT EXISTS idx_blocks_pair ON blocks(user_id, blocked_id);
    """
)

# Автоматическое добавление новых колонок, если база осталась от старой версии
existing_columns = {
    row["name"] for row in db.execute("PRAGMA table_info(profiles)").fetchall()
}
if "photo_id" not in existing_columns:
    db.execute("ALTER TABLE profiles ADD COLUMN photo_id TEXT")
if "username" not in existing_columns:
    db.execute("ALTER TABLE profiles ADD COLUMN username TEXT")
db.commit()


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def age_group(age: int) -> str:
    return "minor" if age < 18 else "adult"


def get_profile(user_id: int):
    return db.execute(
        "SELECT * FROM profiles WHERE user_id = ?",
        (user_id,),
    ).fetchone()


def buttons(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=text, callback_data=data)
                for text, data in row
            ]
            for row in rows
        ]
    )


def main_menu() -> InlineKeyboardMarkup:
    return buttons(
        [
            [("🔎 Смотреть анкеты", "browse")],
            [("👤 Моя анкета", "my_profile")],
            [("❤️ Взаимные симпатии", "matches")],
            [("🚀 Поднять анкету", "boost")],
            [("🛡 Безопасность", "safety")],
            [("⚙️ Настройки", "settings")],
        ]
    )


def candidate_menu(target_id: int) -> InlineKeyboardMarkup:
    return buttons(
        [
            [
                ("❤️ Нравится", f"like:{target_id}"),
                ("⏭ Пропустить", f"skip:{target_id}"),
            ],
            [
                ("🚩 Жалоба", f"report:{target_id}"),
                ("⛔ Блокировка", f"block:{target_id}"),
            ],
            [("🏠 Главное меню", "menu")],
        ]
    )


def profile_text(profile: sqlite3.Row) -> str:
    name = html.escape(profile["name"])
    city = html.escape(profile["city"])
    bio = html.escape(profile["bio"])
    boost_badge = (
        " ⚡"
        if profile["boost_until"] > int(datetime.now(timezone.utc).timestamp())
        else ""
    )
    return (
        f"👤 <b>{name}</b>, {profile['age']}{boost_badge}\n"
        f"📍 {city}\n\n"
        f"📝 {bio}"
    )


def user_contact_link(profile: sqlite3.Row) -> str:
    name = html.escape(profile["name"])
    link = f'<a href="tg://user?id={profile["user_id"]}">{name}</a>'
    if profile["username"]:
        return f"{link} (@{html.escape(profile['username'])})"
    return link


async def send_profile_card(
    message: Message,
    profile: sqlite3.Row,
    reply_markup: InlineKeyboardMarkup,
    extra_text: str = "",
):
    text = profile_text(profile)
    if extra_text:
        text += f"\n\n{extra_text}"

    if profile["photo_id"]:
        try:
            await message.answer_photo(
                photo=profile["photo_id"],
                caption=text,
                reply_markup=reply_markup,
            )
            return
        except TelegramAPIError:
            pass
    await message.answer(text, reply_markup=reply_markup)


async def clear_old_markup(callback: CallbackQuery):
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramAPIError:
        pass


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
# СОСТОЯНИЯ (FSM)
# ============================================================
class ProfileForm(StatesGroup):
    name = State()
    age = State()
    city = State()
    bio = State()
    photo = State()


class SupportForm(StatesGroup):
    message = State()


# ============================================================
# РЕГИСТРАЦИЯ И РЕДАКТИРОВАНИЕ АНКЕТЫ
# ============================================================
@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    profile = get_profile(message.from_user.id)
    if profile:
        if profile["username"] != message.from_user.username:
            db.execute(
                "UPDATE profiles SET username = ? WHERE user_id = ?",
                (message.from_user.username, message.from_user.id),
            )
            db.commit()
        await message.answer(
            "👋 С возвращением!\n\nВыбери действие:",
            reply_markup=main_menu(),
        )
        return

    await message.answer(
        "👋 <b>Добро пожаловать!</b>\n\n"
        "Здесь можно создать анкету и находить людей по взаимной симпатии.\n\n"
        "🔒 Пользователи 14–17 лет и взрослые (18+) строго разделены.\n\n"
        "Чтобы начать, создай анкету:",
        reply_markup=buttons([[("✨ Создать анкету", "profile:create")]]),
    )


@dp.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_menu())


@dp.callback_query(F.data == "profile:create")
async def create_profile(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(ProfileForm.name)
    await callback.answer()
    await callback.message.answer(
        "✨ <b>Создадим анкету!</b>\n\n"
        "Как тебя называть? Напиши имя (от 2 до 30 символов).\n"
        "Для отмены отправь /cancel."
    )


@dp.message(ProfileForm.name)
async def form_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not 2 <= len(name) <= 30:
        await message.answer("Имя должно содержать от 2 до 30 символов.")
        return
    await state.update_data(name=name)
    await state.set_state(ProfileForm.age)
    await message.answer("🎂 Сколько тебе лет? (Введи число от 14 до 80)")


@dp.message(ProfileForm.age)
async def form_age(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text.isdigit() or not 14 <= int(text) <= 80:
        await message.answer("Введи корректный возраст числом от 14 до 80.")
        return
    await state.update_data(age=int(text))
    await state.set_state(ProfileForm.city)
    await message.answer("📍 В каком городе ты живёшь?")


@dp.message(ProfileForm.city)
async def form_city(message: Message, state: FSMContext):
    city = (message.text or "").strip()
    if not 2 <= len(city) <= 50:
        await message.answer("Название города: от 2 до 50 символов.")
        return
    await state.update_data(city=city)
    await state.set_state(ProfileForm.bio)
    await message.answer(
        "📝 Расскажи немного о себе и своих интересах (от 5 до 300 символов).\n"
        "<i>Не указывай в тексте личный телефон или домашний адрес.</i>"
    )


@dp.message(ProfileForm.bio)
async def form_bio(message: Message, state: FSMContext):
    bio = (message.text or "").strip()
    if not 5 <= len(bio) <= 300:
        await message.answer("Описание должно содержать от 5 до 300 символов.")
        return
    await state.update_data(bio=bio)
    await state.set_state(ProfileForm.photo)
    await message.answer(
        "📸 <b>Отправь своё фото для анкеты</b>\n\n"
        "Или нажми кнопку ниже, если хочешь анкету без фотографии:",
        reply_markup=buttons([[("⏭ Пропустить фото", "profile:skip_photo")]]),
    )


async def finish_profile_saving(
    message: Message, user, state: FSMContext, photo_id: str | None
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
                (user_id, username, name, age, city, bio, photo_id, active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
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
    await send_profile_card(
        message,
        profile,
        reply_markup=main_menu(),
        extra_text="✅ <b>Анкета успешно сохранена!</b>",
    )


@dp.message(ProfileForm.photo, F.photo)
async def form_photo_received(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    await finish_profile_saving(message, message.from_user, state, photo_id)


@dp.callback_query(ProfileForm.photo, F.data == "profile:skip_photo")
async def form_photo_skipped(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await clear_old_markup(callback)
    await finish_profile_saving(
        callback.message, callback.from_user, state, None
    )


@dp.message(ProfileForm.photo)
async def form_photo_invalid(message: Message):
    await message.answer(
        "Пожалуйста, отправь фотографию (как картинку, а не файл) или нажми кнопку «Пропустить фото»."
    )


# ============================================================
# ПОИСК И СИМПАТИИ
# ============================================================
async def show_candidate(message: Message, viewer_id: int):
    viewer = get_profile(viewer_id)
    if not viewer:
        await message.answer(
            "Сначала создай анкету.",
            reply_markup=buttons([[("✨ Создать анкету", "profile:create")]]),
        )
        return
    if not viewer["active"]:
        await message.answer(
            "Твоя анкета скрыта. Включи её в настройках, чтобы смотреть других.",
            reply_markup=main_menu(),
        )
        return

    min_age, max_age = (14, 17) if viewer["age"] < 18 else (18, 80)
    now_ts = int(datetime.now(timezone.utc).timestamp())

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
            RANDOM()
        LIMIT 1
        """,
        (viewer_id, min_age, max_age, viewer_id, viewer_id, viewer_id, now_ts),
    ).fetchone()

    if not candidate:
        await message.answer(
            "😔 Пока новых анкет нет. Загляни чуть позже!",
            reply_markup=main_menu(),
        )
        return

    await send_profile_card(
        message,
        candidate,
        reply_markup=candidate_menu(candidate["user_id"]),
    )


@dp.callback_query(F.data == "browse")
async def browse(callback: CallbackQuery):
    await callback.answer()
    await show_candidate(callback.message, callback.from_user.id)


@dp.callback_query(F.data.startswith("skip:"))
async def skip(callback: CallbackQuery):
    user_id = callback.from_user.id
    target_id = int(callback.data.split(":")[1])
    await clear_old_markup(callback)

    if not pair_allowed(user_id, target_id):
        await callback.answer("Анкета недоступна.", show_alert=True)
        await show_candidate(callback.message, user_id)
        return

    db.execute(
        """
        INSERT OR IGNORE INTO reactions (user_id, target_id, action, created_at)
        VALUES (?, ?, 'skip', ?)
        """,
        (user_id, target_id, now_iso()),
    )
    db.commit()
    await callback.answer("Пропущено")
    await show_candidate(callback.message, user_id)


@dp.callback_query(F.data.startswith("like:"))
async def like(callback: CallbackQuery):
    user_id = callback.from_user.id
    target_id = int(callback.data.split(":")[1])
    await clear_old_markup(callback)

    if not pair_allowed(user_id, target_id):
        await callback.answer("Анкета уже недоступна.", show_alert=True)
        await show_candidate(callback.message, user_id)
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
            await callback.answer("Для этой анкеты уже выбран другой ответ.")
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

    await callback.answer("❤️ Симпатия отправлена")

    if new_match:
        target = get_profile(target_id)
        current = get_profile(user_id)

        await callback.message.answer(
            f"🎉 <b>Взаимная симпатия!</b>\n\n"
            f"Тебе ответил(а) взаимностью: {user_contact_link(target)}\n"
            f"Нажми на имя, чтобы начать общение!"
        )
        try:
            await callback.bot.send_message(
                target_id,
                f"🎉 <b>Взаимная симпатия!</b>\n\n"
                f"У тебя мэтч с пользователем: {user_contact_link(current)}\n"
                f"Нажми на имя, чтобы написать сообщение!",
            )
        except TelegramAPIError:
            logging.info("Не удалось уведомить пользователя %s", target_id)

    await show_candidate(callback.message, user_id)


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
        await callback.message.answer(
            "❤️ Пока взаимных симпатий нет. Смотри анкеты и ставь лайки!",
            reply_markup=main_menu(),
        )
        return

    lines = ["❤️ <b>Твои взаимные симпатии:</b>\n"]
    for index, profile in enumerate(visible, 1):
        city = html.escape(profile["city"])
        lines.append(
            f"{index}. {user_contact_link(profile)}, {profile['age']} — {city}"
        )

    await callback.message.answer(
        "\n".join(lines),
        reply_markup=main_menu(),
    )


# ============================================================
# ЖАЛОБЫ И БЛОКИРОВКИ
# ============================================================
@dp.callback_query(F.data.startswith("report:"))
async def report(callback: CallbackQuery):
    user_id = callback.from_user.id
    target_id = int(callback.data.split(":")[1])
    await clear_old_markup(callback)

    if not pair_allowed(user_id, target_id):
        await callback.answer("Анкета недоступна.", show_alert=True)
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

    await callback.answer("Жалоба отправлена")
    await callback.message.answer("🚩 Жалоба отправлена модератору.")

    target = get_profile(target_id)
    target_info = (
        f"{html.escape(target['name'])}, {target['age']} ({target_id})"
        if target
        else str(target_id)
    )
    await notify_admin(
        callback.bot,
        f"🚩 <b>Жалоба на анкету</b>\n\n"
        f"От: <code>{user_id}</code>\n"
        f"На: {target_info}\n"
        f"Текст анкеты: {html.escape(target['bio']) if target else 'Н/Д'}",
    )
    await show_candidate(callback.message, user_id)


@dp.callback_query(F.data.startswith("block:"))
async def block(callback: CallbackQuery):
    user_id = callback.from_user.id
    target_id = int(callback.data.split(":")[1])
    await clear_old_markup(callback)

    if user_id == target_id or not get_profile(target_id):
        await callback.answer("Анкета недоступна.")
        return

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

    await callback.answer("Пользователь заблокирован")
    await show_candidate(callback.message, user_id)


# ============================================================
# ПРОФИЛЬ И НАСТРОЙКИ
# ============================================================
@dp.callback_query(F.data == "my_profile")
async def my_profile(callback: CallbackQuery):
    profile = get_profile(callback.from_user.id)
    await callback.answer()
    if not profile:
        await callback.message.answer(
            "У тебя пока нет анкеты.",
            reply_markup=buttons([[("✨ Создать анкету", "profile:create")]]),
        )
        return

    status = "👁 показывается в поиске" if profile["active"] else "🙈 скрыта"
    await send_profile_card(
        callback.message,
        profile,
        reply_markup=buttons(
            [
                [("✏️ Заполнить заново", "profile:create")],
                [("🏠 Главное меню", "menu")],
            ]
        ),
        extra_text=f"Статус: <b>{status}</b>",
    )


@dp.callback_query(F.data == "settings")
async def settings(callback: CallbackQuery):
    profile = get_profile(callback.from_user.id)
    await callback.answer()
    if not profile:
        await callback.message.answer("Сначала создай анкету.")
        return

    toggle_text = (
        "🙈 Скрыть анкету" if profile["active"] else "👁 Показать анкету"
    )
    await callback.message.answer(
        "⚙️ <b>Настройки анкеты</b>",
        reply_markup=buttons(
            [
                [(toggle_text, "profile:toggle")],
                [("🗑 Удалить анкету", "profile:delete_ask")],
                [("🏠 Главное меню", "menu")],
            ]
        ),
    )


@dp.callback_query(F.data == "profile:toggle")
async def toggle_profile(callback: CallbackQuery):
    profile = get_profile(callback.from_user.id)
    if not profile:
        await callback.answer("Анкета не найдена.")
        return

    new_value = 0 if profile["active"] else 1
    db.execute(
        "UPDATE profiles SET active = ? WHERE user_id = ?",
        (new_value, callback.from_user.id),
    )
    db.commit()
    await callback.answer()
    await callback.message.answer(
        "👁 Анкета теперь видна в поиске."
        if new_value
        else "🙈 Анкета скрыта из поиска.",
        reply_markup=main_menu(),
    )


@dp.callback_query(F.data == "profile:delete_ask")
async def delete_ask(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "⚠️ <b>Удалить анкету, симпатии и блокировки?</b>\n\n"
        "Это действие нельзя отменить.",
        reply_markup=buttons(
            [
                [("🗑 Да, удалить навсегда", "profile:delete_confirm")],
                [("↩️ Отмена", "menu")],
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
    await callback.message.answer(
        "🗑 Анкета полностью удалена.\nДля создания новой отправь /start."
    )


@dp.callback_query(F.data == "safety")
async def safety(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "🛡 <b>Правила безопасности</b>\n\n"
        "• Никогда не переводите незнакомцам деньги и не сообщайте коды из СМС.\n"
        "• Не публикуйте домашний адрес и данные документов.\n"
        "• Используйте кнопки «Жалоба» и «Блокировка» при подозрительном поведении.\n"
        "• Первые встречи назначайте только в людных общественных местах.",
        reply_markup=main_menu(),
    )


@dp.callback_query(F.data == "menu")
async def menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await callback.message.answer("🏠 Главное меню", reply_markup=main_menu())


# ============================================================
# ОПЛАТА TELEGRAM STARS И ПОДДЕРЖКА
# ============================================================
@dp.callback_query(F.data == "boost")
async def boost_info(callback: CallbackQuery):
    profile = get_profile(callback.from_user.id)
    await callback.answer()
    if not profile:
        await callback.message.answer("Сначала создай анкету.")
        return
    if profile["age"] < 18:
        await callback.message.answer(
            "Платные функции доступны только пользователям 18+."
        )
        return
    if not profile["active"]:
        await callback.message.answer(
            "Сначала включи показ анкеты в настройках."
        )
        return
    if not PAYMENTS_ENABLED:
        await callback.message.answer(
            "🚀 <b>Поднятие анкеты</b>\n\n"
            "Платежи временно отключены (тестовый режим).",
            reply_markup=main_menu(),
        )
        return

    await callback.message.answer(
        "🚀 <b>Поднятие анкеты</b>\n\n"
        "Твоя анкета получит значок ⚡ и приоритет в поиске на 24 часа.\n"
        f"Стоимость: <b>{BOOST_PRICE_STARS} Telegram Stars</b>.\n\n"
        "Покупка разовая, без автопродления.",
        reply_markup=buttons(
            [
                [("⭐ Купить поднятие", "buy:boost")],
                [("🏠 Главное меню", "menu")],
            ]
        ),
    )


@dp.callback_query(F.data == "buy:boost")
async def buy_boost(callback: CallbackQuery):
    user_id = callback.from_user.id
    profile = get_profile(user_id)
    if not PAYMENTS_ENABLED:
        await callback.answer("Платежи отключены.", show_alert=True)
        return
    if not profile or profile["age"] < 18 or not profile["active"]:
        await callback.answer(
            "Покупка доступна активным анкетам 18+.", show_alert=True
        )
        return

    order_id = secrets.token_hex(16)
    db.execute(
        """
        INSERT INTO orders (order_id, user_id, product, amount, currency, status, created_at)
        VALUES (?, ?, 'boost', ?, 'XTR', 'pending', ?)
        """,
        (order_id, user_id, BOOST_PRICE_STARS, now_iso()),
    )
    db.commit()
    await callback.answer()

    try:
        await callback.bot.send_invoice(
            chat_id=user_id,
            title="Поднятие анкеты на 24 часа",
            description="Приоритет показа анкеты в поиске на 24 часа. Разовая покупка.",
            payload=order_id,
            currency="XTR",
            prices=[
                LabeledPrice(label="Поднятие анкеты", amount=BOOST_PRICE_STARS)
            ],
        )
    except TelegramAPIError:
        logging.exception("Не удалось создать счёт")
        await callback.message.answer(
            "Не получилось создать счёт. Попробуй позже."
        )


@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    order = db.execute(
        "SELECT * FROM orders WHERE order_id = ?",
        (query.invoice_payload,),
    ).fetchone()
    profile = get_profile(query.from_user.id)

    valid = (
        PAYMENTS_ENABLED
        and order is not None
        and order["user_id"] == query.from_user.id
        and order["product"] == "boost"
        and order["status"] == "pending"
        and query.currency == "XTR"
        and query.total_amount == order["amount"]
        and profile is not None
        and profile["age"] >= 18
        and profile["active"]
    )
    if not valid:
        await query.answer(
            ok=False, error_message="Заказ недействителен. Создай новый счёт."
        )
        return
    await query.answer(ok=True)


@dp.message(F.successful_payment)
async def successful_payment(message: Message):
    payment = message.successful_payment
    order_id = payment.invoice_payload
    user_id = message.from_user.id
    charge_id = payment.telegram_payment_charge_id

    order = db.execute(
        "SELECT * FROM orders WHERE order_id = ?", (order_id,)
    ).fetchone()
    if not order:
        await message.answer(
            "Платёж получен, но заказ не найден. Напиши в /paysupport."
        )
        await notify_admin(
            message.bot, f"⚠️ Неизвестный заказ {order_id} от {user_id}"
        )
        return

    if order["status"] == "paid":
        return

    valid = (
        order["status"] == "pending"
        and order["user_id"] == user_id
        and order["product"] == "boost"
        and payment.currency == order["currency"] == "XTR"
        and payment.total_amount == order["amount"]
    )
    if not valid:
        await message.answer("Ошибка проверки платежа. Напиши в /paysupport.")
        await notify_admin(
            message.bot, f"⚠️ Ошибка сверки заказа {order_id} от {user_id}"
        )
        return

    profile = get_profile(user_id)
    try:
        with db:
            cursor = db.execute(
                """
                UPDATE orders SET status = 'paid', telegram_charge_id = ?
                WHERE order_id = ? AND status = 'pending'
                """,
                (charge_id, order_id),
            )
            if cursor.rowcount != 1:
                return

            if profile and profile["age"] >= 18:
                now_ts = int(datetime.now(timezone.utc).timestamp())
                start_ts = max(now_ts, profile["boost_until"])
                boost_until = start_ts + 24 * 60 * 60
                db.execute(
                    "UPDATE profiles SET boost_until = ? WHERE user_id = ?",
                    (boost_until, user_id),
                )
            else:
                boost_until = None
    except sqlite3.IntegrityError:
        return

    if boost_until is None:
        await message.answer(
            "Платёж получен, но услугу не удалось активировать. Напиши в /paysupport."
        )
        await notify_admin(
            message.bot,
            f"⚠️ Ручная проверка заказа {order_id}, юзер {user_id}",
        )
        return

    until = datetime.fromtimestamp(boost_until, timezone.utc)
    await message.answer(
        "⚡ <b>Поднятие анкеты активировано!</b>\n\n"
        f"Действует до: {until.strftime('%d.%m.%Y %H:%M')} UTC.",
        reply_markup=main_menu(),
    )


@dp.message(Command("paysupport"))
async def payment_support(message: Message, state: FSMContext):
    if not ADMIN_ID:
        await message.answer("Поддержка пока не настроена администратором.")
        return

    await state.set_state(SupportForm.message)
    await message.answer(
        "💬 <b>Поддержка по платежам</b>\n\n"
        "Опиши свою проблему одним сообщением, и я перешлю его администратору.\n"
        "Для отмены нажми /cancel."
    )


@dp.message(SupportForm.message)
async def process_support_message(message: Message, state: FSMContext):
    text = (message.text or message.caption or "").strip()
    if not text:
        await message.answer("Пожалуйста, опиши проблему текстом.")
        return

    await state.clear()
    user = message.from_user
    username = f"@{user.username}" if user.username else "без username"

    await notify_admin(
        message.bot,
        f"💳 <b>Обращение в /paysupport</b>\n"
        f"От: <a href='tg://user?id={user.id}'>{html.escape(user.full_name)}</a> ({username}, ID: <code>{user.id}</code>)\n\n"
        f"Текст:\n{html.escape(text)}",
    )
    await message.answer(
        "✅ Твоё обращение отправлено администратору! Ожидай ответа.",
        reply_markup=main_menu(),
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
                BotCommand(command="start", description="Главное меню"),
                BotCommand(command="cancel", description="Отменить ввод"),
                BotCommand(
                    command="paysupport", description="Помощь с оплатой"
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
            "1. Зайди в @BotFather -> /revoke и получи новый токен.\n"
            "2. Вставь новый токен в файл .env И в настройки переменных (Variables) на хостинге!",
            masked,
        )
    finally:
        await bot.session.close()
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
