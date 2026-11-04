import asyncio
import html
import logging
import math
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv

# Перезаписываем переменные окружения из файла .env
load_dotenv(override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# =========================
# CONFIG (.env)
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip().strip('"').strip("'")
DB_PATH = os.getenv("DB_PATH", "dating_bot.sqlite3").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0").strip() or 0)
PAYMENTS_ENABLED = os.getenv("PAYMENTS_ENABLED", "1").strip() == "1"

# Настройки ЮMoney (yoomoney.ru) и комиссии 30%
YOOMONEY_WALLET = os.getenv("YOOMONEY_WALLET", "").strip().strip('"').strip("'")
YOOMONEY_TOKEN = os.getenv("YOOMONEY_TOKEN", "").strip().strip('"').strip("'")
COMMISSION_PERCENT = int(os.getenv("COMMISSION_PERCENT", "30").strip() or 30)

# Партнерская кнопка Velvet VPN
VELVET_VPN_URL = "https://t.me/velvet?start=3SLkWOoF"
VELVET_VPN_TEXT = "💎 Velvet VPN › Доступно всё"

# Лимиты для монетизации
FREE_DAILY_LIKES = 25

# Имя бота (обновляется автоматически при запуске)
BOT_USERNAME = "lovchik_tg_bot"

# Хранилище последнего сообщения бота для каждого пользователя (для автоудаления)
last_bot_messages: Dict[int, int] = {}

# Защита от спама кнопкой "Проверить оплату" (order_id -> timestamp последнего уведомления админу)
admin_notified_orders: Dict[str, int] = {}


# =========================
# PRODUCTS & PRICING (+30% КОМИССИЯ)
# =========================
@dataclass
class Product:
    code: str
    title: str
    description: str
    base_price_rub: int
    emoji: str


PRODUCTS: Dict[str, Product] = {
    "vip_7": Product(
        code="vip_7",
        title="👑 VIP-статус на 7 дней",
        description="Безлимит лайков, просмотр тех, кто тебя лайкнул, приоритет в поиске и +3 Супер-письма в подарок.",
        base_price_rub=290,
        emoji="👑",
    ),
    "vip_30": Product(
        code="vip_30",
        title="👑 VIP-статус на 30 дней (ХИТ)",
        description="Полный доступ ко всем функциям на месяц + 10 Супер-писем и золотой значок в анкете.",
        base_price_rub=690,
        emoji="💎",
    ),
    "boost_24": Product(
        code="boost_24",
        title="🚀 Поднятие в ТОП на 24 часа",
        description="Твоя анкета показывается первой всем пользователям твоего возраста. В 5 раз больше взаимных симпатий!",
        base_price_rub=149,
        emoji="🚀",
    ),
    "super_5": Product(
        code="super_5",
        title="💌 Пакет: 5 Супер-писем",
        description="Пиши личные сообщения понравившимся людям сразу с передачей своего контакта, не дожидаясь взаимного лайка!",
        base_price_rub=199,
        emoji="💌",
    ),
    "reset_skips": Product(
        code="reset_skips",
        title="🔄 Сброс пропущенных анкет",
        description="Верни в поиск всех, кого ты случайно пропустил(а) (нажал 👎), и посмотри их анкеты заново.",
        base_price_rub=99,
        emoji="🔄",
    ),
}


def calc_price_with_commission(base_price: int) -> Tuple[int, int, int]:
    """Возвращает (базовая_цена, сумма_комиссии_30%, итого_к_оплате)."""
    commission = math.ceil(base_price * (COMMISSION_PERCENT / 100.0))
    total = base_price + commission
    return base_price, commission, total


# =========================
# DATABASE & MIGRATIONS
# =========================
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS profiles (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            name TEXT NOT NULL,
            age INTEGER NOT NULL,
            city TEXT NOT NULL,
            bio TEXT NOT NULL,
            photo_id TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            boost_until INTEGER NOT NULL DEFAULT 0,
            vip_until INTEGER NOT NULL DEFAULT 0,
            superlikes INTEGER NOT NULL DEFAULT 1,
            referred_by INTEGER NOT NULL DEFAULT 0,
            ref_count INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL
        )
        """
    )

    cur.execute("PRAGMA table_info(profiles)")
    p_cols = [r["name"] for r in cur.fetchall()]
    if "vip_until" not in p_cols:
        cur.execute("ALTER TABLE profiles ADD COLUMN vip_until INTEGER NOT NULL DEFAULT 0")
    if "superlikes" not in p_cols:
        cur.execute("ALTER TABLE profiles ADD COLUMN superlikes INTEGER NOT NULL DEFAULT 1")
    if "referred_by" not in p_cols:
        cur.execute("ALTER TABLE profiles ADD COLUMN referred_by INTEGER NOT NULL DEFAULT 0")
    if "ref_count" not in p_cols:
        cur.execute("ALTER TABLE profiles ADD COLUMN ref_count INTEGER NOT NULL DEFAULT 0")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_referrals (
            user_id INTEGER PRIMARY KEY,
            inviter_id INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS reactions (
            user_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY (user_id, target_id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS matches (
            user1 INTEGER NOT NULL,
            user2 INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY (user1, user2)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS blocks (
            user_id INTEGER NOT NULL,
            blocked_id INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY (user_id, blocked_id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reporter_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            product TEXT NOT NULL,
            base_amount INTEGER NOT NULL DEFAULT 0,
            commission INTEGER NOT NULL DEFAULT 0,
            amount INTEGER NOT NULL,
            currency TEXT NOT NULL,
            status TEXT NOT NULL,
            telegram_charge_id TEXT,
            created_at INTEGER NOT NULL
        )
        """
    )

    cur.execute("PRAGMA table_info(orders)")
    o_cols = [r["name"] for r in cur.fetchall()]
    if "base_amount" not in o_cols:
        cur.execute("ALTER TABLE orders ADD COLUMN base_amount INTEGER NOT NULL DEFAULT 0")
    if "commission" not in o_cols:
        cur.execute("ALTER TABLE orders ADD COLUMN commission INTEGER NOT NULL DEFAULT 0")

    conn.commit()
    conn.close()


# =========================
# AGE & STATUS HELPERS
# =========================
def now_ts() -> int:
    return int(time.time())


def is_minor_age(age: int) -> bool:
    return 14 <= age <= 17


def is_adult_age(age: int) -> bool:
    return age >= 18


def age_group(age: int) -> str:
    if is_minor_age(age):
        return "minor"
    if is_adult_age(age):
        return "adult"
    return "invalid"


def same_age_group(age1: int, age2: int) -> bool:
    g1 = age_group(age1)
    g2 = age_group(age2)
    return g1 == g2 and g1 in ("minor", "adult")


def is_boosted(row: sqlite3.Row) -> bool:
    return int(row["boost_until"]) > now_ts()


def is_vip(row: sqlite3.Row) -> bool:
    return int(row["vip_until"]) > now_ts()


# =========================
# DB QUERIES & REFERRAL LOGIC
# =========================
def get_profile(user_id: int) -> Optional[sqlite3.Row]:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row


def save_pending_referral(user_id: int, inviter_id: int) -> bool:
    if user_id == inviter_id or inviter_id <= 0:
        return False
    if get_profile(user_id) is not None:
        return False
    inviter = get_profile(inviter_id)
    if not inviter:
        return False

    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO pending_referrals (user_id, inviter_id, created_at)
        VALUES (?, ?, ?)
        """,
        (user_id, inviter_id, now_ts()),
    )
    conn.commit()
    conn.close()
    return True


def upsert_profile(
    user_id: int,
    username: Optional[str],
    name: str,
    age: int,
    city: str,
    bio: str,
    photo_id: Optional[str],
) -> Tuple[bool, Optional[int], int]:
    conn = db()
    cur = conn.cursor()
    existing = get_profile(user_id)

    if existing:
        cur.execute(
            """
            UPDATE profiles
            SET username = ?, name = ?, age = ?, city = ?, bio = ?, photo_id = ?, active = 1
            WHERE user_id = ?
            """,
            (username, name, age, city, bio, photo_id, user_id),
        )
        conn.commit()
        conn.close()
        return False, None, 0

    cur.execute("SELECT inviter_id FROM pending_referrals WHERE user_id = ?", (user_id,))
    pend = cur.fetchone()
    inviter_id = int(pend["inviter_id"]) if pend else 0

    initial_superlikes = 2 if inviter_id > 0 else 1

    cur.execute(
        """
        INSERT INTO profiles (
            user_id, username, name, age, city, bio, photo_id,
            active, boost_until, vip_until, superlikes, referred_by, ref_count, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, 0, ?, ?, 0, ?)
        """,
        (user_id, username, name, age, city, bio, photo_id, initial_superlikes, inviter_id, now_ts()),
    )

    if pend:
        cur.execute("DELETE FROM pending_referrals WHERE user_id = ?", (user_id,))

    conn.commit()
    conn.close()

    if inviter_id > 0 and get_profile(inviter_id):
        conn2 = db()
        cur2 = conn2.cursor()
        cur2.execute("UPDATE profiles SET ref_count = ref_count + 1 WHERE user_id = ?", (inviter_id,))
        cur2.execute("SELECT ref_count FROM profiles WHERE user_id = ?", (inviter_id,))
        r_row = cur2.fetchone()
        new_ref_count = int(r_row["ref_count"]) if r_row else 1
        conn2.commit()
        conn2.close()

        add_superlikes(inviter_id, 1)
        add_boost_hours(inviter_id, 6)

        if new_ref_count % 3 == 0:
            add_vip_days(inviter_id, 1, bonus_superlikes=1)

        return True, inviter_id, new_ref_count

    return True, None, 0


def get_top_referrers(limit: int = 10) -> List[sqlite3.Row]:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT name, age, city, ref_count, vip_until
        FROM profiles
        WHERE ref_count > 0
        ORDER BY ref_count DESC, created_at ASC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def update_username(user_id: int, username: Optional[str]) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE profiles SET username = ? WHERE user_id = ?", (username, user_id))
    conn.commit()
    conn.close()


def set_active(user_id: int, active: bool) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE profiles SET active = ? WHERE user_id = ?", (1 if active else 0, user_id))
    conn.commit()
    conn.close()


def delete_profile(user_id: int) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute("DELETE FROM profiles WHERE user_id = ?", (user_id,))
    cur.execute("DELETE FROM reactions WHERE user_id = ? OR target_id = ?", (user_id, user_id))
    cur.execute("DELETE FROM matches WHERE user1 = ? OR user2 = ?", (user_id, user_id))
    cur.execute("DELETE FROM blocks WHERE user_id = ? OR blocked_id = ?", (user_id, user_id))
    conn.commit()
    conn.close()


def add_reaction(user_id: int, target_id: int, action: str) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO reactions (user_id, target_id, action, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, target_id, action, now_ts()),
    )
    conn.commit()
    conn.close()


def count_today_likes(user_id: int) -> int:
    since = now_ts() - 86400
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) AS cnt FROM reactions WHERE user_id = ? AND action = 'like' AND created_at >= ?",
        (user_id, since),
    )
    row = cur.fetchone()
    conn.close()
    return int(row["cnt"]) if row else 0


def reset_user_skips(user_id: int) -> int:
    conn = db()
    cur = conn.cursor()
    cur.execute("DELETE FROM reactions WHERE user_id = ? AND action = 'skip'", (user_id,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted


def has_liked(user_id: int, target_id: int) -> bool:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM reactions WHERE user_id = ? AND target_id = ? AND action = 'like'",
        (user_id, target_id),
    )
    row = cur.fetchone()
    conn.close()
    return row is not None


def add_match(u1: int, u2: int) -> None:
    a, b = sorted((u1, u2))
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO matches (user1, user2, created_at)
        VALUES (?, ?, ?)
        """,
        (a, b, now_ts()),
    )
    conn.commit()
    conn.close()


def get_matches(user_id: int) -> List[sqlite3.Row]:
    me = get_profile(user_id)
    if not me:
        return []

    my_age = int(me["age"])
    if is_minor_age(my_age):
        min_age, max_age = 14, 17
    elif is_adult_age(my_age):
        min_age, max_age = 18, 120
    else:
        return []

    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.*
        FROM matches m
        JOIN profiles p
          ON p.user_id = CASE
              WHEN m.user1 = ? THEN m.user2
              ELSE m.user1
          END
        WHERE (m.user1 = ? OR m.user2 = ?)
          AND p.active = 1
          AND p.age BETWEEN ? AND ?
        ORDER BY m.created_at DESC
        LIMIT 25
        """,
        (user_id, user_id, user_id, min_age, max_age),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_incoming_likes(user_id: int) -> List[sqlite3.Row]:
    me = get_profile(user_id)
    if not me:
        return []

    my_age = int(me["age"])
    min_age, max_age = (14, 17) if is_minor_age(my_age) else (18, 120)

    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.*
        FROM reactions r
        JOIN profiles p ON p.user_id = r.user_id
        WHERE r.target_id = ?
          AND r.action = 'like'
          AND p.active = 1
          AND p.age BETWEEN ? AND ?
          AND p.user_id NOT IN (
              SELECT target_id FROM reactions WHERE user_id = ?
          )
          AND p.user_id NOT IN (
              SELECT blocked_id FROM blocks WHERE user_id = ?
          )
        ORDER BY r.created_at DESC
        """,
        (user_id, min_age, max_age, user_id, user_id),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def block_user(user_id: int, blocked_id: int) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO blocks (user_id, blocked_id, created_at)
        VALUES (?, ?, ?)
        """,
        (user_id, blocked_id, now_ts()),
    )
    conn.commit()
    conn.close()


def report_user(reporter_id: int, target_id: int) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO reports (reporter_id, target_id, created_at)
        VALUES (?, ?, ?)
        """,
        (reporter_id, target_id, now_ts()),
    )
    conn.commit()
    conn.close()


def add_boost_hours(user_id: int, hours: int) -> int:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT boost_until FROM profiles WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    now = now_ts()
    current_until = int(row["boost_until"]) if row else 0
    base = max(now, current_until)
    new_until = base + hours * 3600
    cur.execute("UPDATE profiles SET boost_until = ? WHERE user_id = ?", (new_until, user_id))
    conn.commit()
    conn.close()
    return new_until


def add_vip_days(user_id: int, days: int, bonus_superlikes: int = 0) -> int:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT vip_until, superlikes FROM profiles WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    now = now_ts()
    current_until = int(row["vip_until"]) if row else 0
    current_sl = int(row["superlikes"]) if row else 0
    base = max(now, current_until)
    new_until = base + days * 86400
    cur.execute(
        "UPDATE profiles SET vip_until = ?, superlikes = ? WHERE user_id = ?",
        (new_until, current_sl + bonus_superlikes, user_id),
    )
    conn.commit()
    conn.close()
    return new_until


def add_superlikes(user_id: int, count: int) -> int:
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE profiles SET superlikes = superlikes + ? WHERE user_id = ?", (count, user_id))
    cur.execute("SELECT superlikes FROM profiles WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.commit()
    conn.close()
    return int(row["superlikes"]) if row else 0


def use_superlike(user_id: int) -> bool:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT superlikes FROM profiles WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if not row or int(row["superlikes"]) <= 0:
        conn.close()
        return False
    cur.execute("UPDATE profiles SET superlikes = superlikes - 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    return True


def create_order(
    order_id: str,
    user_id: int,
    product: str,
    base_amount: int,
    commission: int,
    total_amount: int,
    currency: str = "RUB",
) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO orders (order_id, user_id, product, base_amount, commission, amount, currency, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        """,
        (order_id, user_id, product, base_amount, commission, total_amount, currency, now_ts()),
    )
    conn.commit()
    conn.close()


def get_order(order_id: str) -> Optional[sqlite3.Row]:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,))
    row = cur.fetchone()
    conn.close()
    return row


def mark_order_paid(order_id: str, charge_id: str) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE orders
        SET status = 'paid', telegram_charge_id = ?
        WHERE order_id = ?
        """,
        (charge_id, order_id),
    )
    conn.commit()
    conn.close()


def apply_order_reward(user_id: int, prod_code: str) -> str:
    """Начисляет привилегию пользователю и возвращает текст уведомления."""
    if prod_code == "vip_7":
        add_vip_days(user_id, 7, bonus_superlikes=3)
        return "👑 <b>VIP-статус на 7 дней и +3 Супер-письма успешно активированы!</b>"
    elif prod_code == "vip_30":
        add_vip_days(user_id, 30, bonus_superlikes=10)
        return "💎 <b>VIP-статус на 30 дней и +10 Супер-писем успешно активированы!</b>"
    elif prod_code == "boost_24":
        add_boost_hours(user_id, 24)
        return "🚀 <b>Твоя анкета поднята в ТОП на 24 часа!</b>"
    elif prod_code == "super_5":
        add_superlikes(user_id, 5)
        return "💌 <b>Тебе начислено +5 Супер-писем!</b>"
    elif prod_code == "reset_skips":
        reset_user_skips(user_id)
        return "🔄 <b>Все пропущенные анкеты возвращены в поиск!</b>"
    return "✅ <b>Оплата прошла успешно! Услуга активирована.</b>"


def get_next_candidate(user_id: int) -> Optional[sqlite3.Row]:
    me = get_profile(user_id)
    if not me:
        return None

    my_age = int(me["age"])
    if is_minor_age(my_age):
        min_age, max_age = 14, 17
    elif is_adult_age(my_age):
        min_age, max_age = 18, 120
    else:
        return None

    now = now_ts()
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.*
        FROM profiles p
        WHERE p.user_id != ?
          AND p.active = 1
          AND p.age BETWEEN ? AND ?
          AND p.user_id NOT IN (
              SELECT target_id FROM reactions WHERE user_id = ?
          )
          AND p.user_id NOT IN (
              SELECT blocked_id FROM blocks WHERE user_id = ?
          )
          AND p.user_id NOT IN (
              SELECT user_id FROM blocks WHERE blocked_id = ?
          )
        ORDER BY
          CASE WHEN LOWER(p.city) = LOWER(?) THEN 0 ELSE 1 END,
          CASE WHEN p.boost_until > ? THEN 0 ELSE 1 END,
          CASE WHEN p.vip_until > ? THEN 0 ELSE 1 END,
          RANDOM()
        LIMIT 1
        """,
        (user_id, min_age, max_age, user_id, user_id, user_id, me["city"], now, now),
    )
    row = cur.fetchone()
    conn.close()
    return row


# =========================
# YOOMONEY API & PAYMENT LINKS
# =========================
def generate_yoomoney_link(order_id: str, total_rub: int, description: str) -> str:
    params = {
        "receiver": YOOMONEY_WALLET,
        "quickpay-form": "button",
        "paymentType": "AC",
        "sum": str(total_rub),
        "label": order_id,
        "targets": description,
    }
    return f"https://yoomoney.ru/quickpay/confirm?{urlencode(params)}"


async def verify_yoomoney_payment(order_id: str, expected_total: int) -> Tuple[bool, str, bool]:
    """
    Проверяет оплату по API ЮMoney.
    Возвращает: (оплачено_ли, сообщение_для_пользователя, нужен_ли_ручной_режим_админа)
    """
    # Если токен не задан или оставлена заглушка из примера
    if not YOOMONEY_TOKEN or "ваш_" in YOOMONEY_TOKEN.lower() or len(YOOMONEY_TOKEN) < 20:
        return (
            False,
            "⏳ Платёж проверяется! Если вы ещё не оплатили — нажмите «Оплатить» выше. Если уже перевели — заявка отправлена администратору, услуга активируется в ближайшее время!",
            True,
        )

    url = "https://yoomoney.ru/api/operation-history"
    headers = {
        "Authorization": f"Bearer {YOOMONEY_TOKEN}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "label": order_id,
        "records": "10",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, data=data, timeout=10) as resp:
                if resp.status == 401:
                    logging.warning("ЮMoney вернул 401 Unauthorized: неверный YOOMONEY_TOKEN в .env. Переключаемся на подтверждение админом.")
                    return (
                        False,
                        "⏳ Заявка на проверку оплаты отправлена! Если вы ещё не оплатили счёт — нажмите кнопку оплаты выше. После перевода услуга будет начислена!",
                        True,
                    )
                if resp.status != 200:
                    return (
                        False,
                        "⏳ Платёж ещё в обработке. Подождите 15 секунд и нажмите «Проверить оплату» снова.",
                        True,
                    )
                result = await resp.json()
                operations = result.get("operations", [])
                for op in operations:
                    if (
                        op.get("status") == "success"
                        and op.get("direction") == "in"
                        and str(op.get("label")) == str(order_id)
                    ):
                        op_amount = float(op.get("amount", 0))
                        if op_amount >= expected_total * 0.95:
                            return True, str(op.get("operation_id", "yoomoney_paid")), False
                return (
                    False,
                    "⏳ Оплата по этому счёту ещё не поступила. Сначала оплатите по кнопке выше, подождите 10–15 секунд и нажмите «Проверить оплату».",
                    False,
                )
    except Exception as e:
        logging.error(f"YooMoney check error: {e}")
        return (
            False,
            "⏳ Проверка платежа... Если вы уже оплатили, заявка передана администратору!",
            True,
        )


# =========================
# SINGLE-WINDOW ENGINE (АВТОУДАЛЕНИЕ СООБЩЕНИЙ)
# =========================
async def safe_delete_message(bot: Bot, chat_id: int, message_id: Optional[int]) -> None:
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def send_screen(
    bot: Bot,
    user_id: int,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    photo_id: Optional[str] = None,
) -> Message:
    prev_msg_id = last_bot_messages.get(user_id)
    if prev_msg_id:
        await safe_delete_message(bot, user_id, prev_msg_id)

    try:
        if photo_id:
            msg = await bot.send_photo(
                chat_id=user_id,
                photo=photo_id,
                caption=text,
                reply_markup=reply_markup,
            )
        else:
            msg = await bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
    except Exception:
        msg = await bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )

    last_bot_messages[user_id] = msg.message_id
    return msg


# =========================
# UI FORMATTING & KEYBOARDS
# =========================
def format_profile_card(row: sqlite3.Row, title: Optional[str] = None, show_status: bool = False) -> str:
    name = html.escape(str(row["name"]))
    age = int(row["age"])
    city = html.escape(str(row["city"]))
    bio = html.escape(str(row["bio"]))

    badges = []
    if is_vip(row):
        badges.append("👑 <b>VIP</b>")
    if is_boosted(row):
        badges.append("⚡️ <b>TOP</b>")
    badge_str = (" · " + " ".join(badges)) if badges else ""

    lines = []
    if title:
        lines.append(f"<b>{title}</b>\n")

    lines.append(f"👤 <b>{name}, {age}</b>  📍 <i>{city}</i>{badge_str}")
    lines.append(f"<blockquote>{bio}</blockquote>")

    if show_status:
        active_str = "🟢 Видна в поиске" if int(row["active"]) == 1 else "🔴 Скрыта из поиска"
        group_str = "🔞 Взрослая лига (18+)" if is_adult_age(age) else "🌱 Молодежная лига (14–17)"
        lines.append(f"\n📊 <b>Статус:</b> {active_str}")
        lines.append(f"🛡 <b>Категория:</b> {group_str}")
        lines.append(f"💌 <b>Супер-писем:</b> {int(row['superlikes'])} шт.")
        lines.append(f"👥 <b>Приглашено друзей:</b> {int(row['ref_count'])} чел.")

        if is_vip(row):
            hours_left = max(1, (int(row["vip_until"]) - now_ts()) // 3600)
            lines.append(f"👑 <b>VIP активен:</b> ещё ~{hours_left} ч.")
        if is_boosted(row):
            mins_left = max(1, (int(row["boost_until"]) - now_ts()) // 60)
            lines.append(f"🚀 <b>ТОП-буст:</b> ещё {mins_left} мин.")

    return "\n".join(lines)


def vpn_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(text=VELVET_VPN_TEXT, url=VELVET_VPN_URL)


def start_no_profile_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✨ Создать анкету", callback_data="profile:create")],
            [vpn_button()],
        ]
    )


def main_menu_kb(user_id: int) -> InlineKeyboardMarkup:
    me = get_profile(user_id)
    incoming_cnt = len(get_incoming_likes(user_id)) if me else 0
    likes_label = f"👀 Кто лайкнул ({incoming_cnt})" if incoming_cnt > 0 else "👀 Кто меня лайкнул"

    rows = [
        [
            InlineKeyboardButton(text="🔥 Смотреть анкеты", callback_data="menu:browse"),
            InlineKeyboardButton(text=likes_label, callback_data="menu:who_liked"),
        ],
        [
            InlineKeyboardButton(text="💞 Взаимные симпатии", callback_data="menu:matches"),
            InlineKeyboardButton(text="🎁 Бесплатный VIP (Рефералка)", callback_data="menu:referral"),
        ],
    ]

    if me and is_adult_age(int(me["age"])) and PAYMENTS_ENABLED:
        rows.append(
            [
                InlineKeyboardButton(text="👑 VIP и Премиум-магазин", callback_data="menu:shop"),
                InlineKeyboardButton(text="🚀 Поднять в ТОП", callback_data="shop:item:boost_24"),
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(text="👤 Моя анкета", callback_data="menu:me"),
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu:settings"),
        ]
    )
    rows.append([vpn_button()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def referral_kb(user_id: int) -> InlineKeyboardMarkup:
    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"
    share_text = (
        "💘 Зацени крутого бота для знакомств Лавчик! "
        "Заходи по моей ссылке и получи +2 Супер-письма в подарок 🎁"
    )
    share_url = f"https://t.me/share/url?url={quote(ref_link)}&text={quote(share_text)}"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📤 Поделиться ссылкой с друзьями", url=share_url)],
            [InlineKeyboardButton(text="🏆 ТОП-10 приглашающих", callback_data="ref:leaderboard")],
            [vpn_button()],
            [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="menu:home")],
        ]
    )


def reg_nav_kb(can_back: bool = True, skip_photo: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if skip_photo:
        rows.append([InlineKeyboardButton(text="⏭ Пропустить фото", callback_data="reg:skip_photo")])

    nav_row = []
    if can_back:
        nav_row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data="reg:back"))
    nav_row.append(InlineKeyboardButton(text="❌ Отмена", callback_data="reg:cancel"))
    rows.append(nav_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def candidate_kb(target_id: int, viewer_age: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="❤️ Лайк", callback_data=f"act:like:{target_id}"),
            InlineKeyboardButton(text="👎 Дальше", callback_data=f"act:skip:{target_id}"),
        ],
    ]
    if is_adult_age(viewer_age):
        rows.append(
            [InlineKeyboardButton(text="💌 Написать сразу (Супер-письмо)", callback_data=f"act:super:{target_id}")]
        )
    rows.append(
        [
            InlineKeyboardButton(text="⚠️ Жалоба", callback_data=f"act:report:{target_id}"),
            InlineKeyboardButton(text="🚫 В ЧС", callback_data=f"act:block:{target_id}"),
        ]
    )
    rows.append([vpn_button()])
    rows.append([InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def who_liked_kb(target_id: int, viewer_is_vip: bool) -> InlineKeyboardMarkup:
    if viewer_is_vip:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="❤️ Ответить взаимностью", callback_data=f"act:like:{target_id}"),
                    InlineKeyboardButton(text="👎 Скрыть", callback_data=f"act:skip:{target_id}"),
                ],
                [vpn_button()],
                [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="menu:home")],
            ]
        )
    else:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="👑 Открыть анкету (Подключить VIP)", callback_data="shop:item:vip_7")],
                [InlineKeyboardButton(text="🎁 Получить VIP за друзей", callback_data="menu:referral")],
                [vpn_button()],
                [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="menu:home")],
            ]
        )


def settings_kb(user_id: int) -> InlineKeyboardMarkup:
    me = get_profile(user_id)
    active = int(me["active"]) == 1 if me else True
    toggle_text = "🙈 Скрыть мою анкету" if active else "👁 Показать анкету в поиске"

    rows = [
        [
            InlineKeyboardButton(text="✏️ Изменить анкету", callback_data="profile:create"),
            InlineKeyboardButton(text=toggle_text, callback_data="settings:toggle"),
        ],
        [InlineKeyboardButton(text="🗑 Удалить анкету навсегда", callback_data="settings:delete")],
        [vpn_button()],
        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="menu:home")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def shop_menu_kb() -> InlineKeyboardMarkup:
    rows = []
    for code, prod in PRODUCTS.items():
        _, _, total = calc_price_with_commission(prod.base_price_rub)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{prod.title} — {total} ₽",
                    callback_data=f"shop:item:{code}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="🎁 Получить VIP бесплатно за друзей", callback_data="menu:referral")])
    rows.append([vpn_button()])
    rows.append([InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_invoice_kb(order_id: str, pay_url: str, product_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить через ЮMoney / Картой", url=pay_url)],
            [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"pay:check:{order_id}")],
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:shop"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="menu:home"),
            ],
        ]
    )


def back_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [vpn_button()],
            [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="menu:home")],
        ]
    )


# =========================
# FSM STATES
# =========================
class RegStates(StatesGroup):
    name = State()
    age = State()
    city = State()
    bio = State()
    photo = State()


class SuperMsgState(StatesGroup):
    waiting_text = State()


class SupportState(StatesGroup):
    waiting_text = State()


class AdminBroadcastState(StatesGroup):
    waiting_post = State()


class AdminGiftState(StatesGroup):
    waiting_user_id = State()


# =========================
# SCREEN RENDERERS
# =========================
async def render_home(bot: Bot, user_id: int, notice: Optional[str] = None) -> None:
    me = get_profile(user_id)
    if not me:
        text = (
            "💘 <b>Добро пожаловать в бот знакомств «Лавчик»!</b>\n\n"
            "Здесь ты быстро найдешь новых друзей, общение или вторую половинку в своем городе.\n\n"
            "🔒 <i>Строгое разделение по возрасту, умный подбор анкет и полная приватность.</i>\n\n"
            "Нажми кнопку ниже, чтобы создать анкету за 1 минуту 👇"
        )
        if notice:
            text = f"{notice}\n\n{text}"
        await send_screen(bot, user_id, text, reply_markup=start_no_profile_kb())
        return

    header = "🏠 <b>Главное меню</b>"
    if notice:
        header = f"{notice}\n\n{header}"

    card = format_profile_card(me, title=header, show_status=True)
    await send_screen(
        bot,
        user_id,
        card,
        reply_markup=main_menu_kb(user_id),
        photo_id=me["photo_id"],
    )


async def render_referral_screen(bot: Bot, user_id: int) -> None:
    me = get_profile(user_id)
    if not me:
        await render_home(bot, user_id)
        return

    ref_cnt = int(me["ref_count"])
    step_in_cycle = ref_cnt % 3
    progress_bar = "🟩" * step_in_cycle + "⬜️" * (3 - step_in_cycle)
    left_for_vip = 3 - step_in_cycle
    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"

    text = (
        "🎁 <b>Партнёрская программа и Бесплатный VIP</b>\n\n"
        "Приглашай друзей по своей личной ссылке и получай жирные бонусы за каждого, кто создаст анкету!\n\n"
        "💎 <b>Твои награды:</b>\n"
        "• <b>За каждого друга:</b> +1 Супер-письмо 💌 и +6 часов ТОП-буста 🚀\n"
        "• <b>За каждых 3 друзей:</b> 👑 <b>VIP-статус на 24 часа</b> автоматически!\n"
        "• <b>Бонус с покупок:</b> если твой друг купит любую услугу в магазине, ты получишь ещё <b>+24ч VIP и +2 Супер-письма</b>!\n"
        "• <b>Другу-новичку:</b> +2 Супер-письма на старте вместо 1!\n\n"
        f"📊 <b>Твоя статистика:</b>\n"
        f"👥 Приглашено друзей (с анкетой): <b>{ref_cnt} чел.</b>\n"
        f"👑 Прогресс до следующего VIP: {progress_bar} (осталось: <b>{left_for_vip}</b>)\n\n"
        f"🔗 <b>Твоя реферальная ссылка (нажми, чтобы скопировать):</b>\n"
        f"<code>{ref_link}</code>"
    )
    await send_screen(bot, user_id, text, reply_markup=referral_kb(user_id))


async def render_next_candidate(bot: Bot, user_id: int, prefix_notice: Optional[str] = None) -> None:
    me = get_profile(user_id)
    if not me:
        await render_home(bot, user_id)
        return

    if int(me["active"]) == 0:
        await render_home(
            bot,
            user_id,
            notice="⚠️ <b>Твоя анкета сейчас скрыта!</b>\nВключи её в «Настройках», чтобы смотреть других людей.",
        )
        return

    cand = get_next_candidate(user_id)
    if not cand:
        text = ""
        if prefix_notice:
            text += f"{prefix_notice}\n\n"
        text += (
            "🔍 <b>Новые анкеты в твоей категории пока закончились!</b>\n\n"
            "Новые пользователи регистрируются каждый час.\n"
            "💡 <i>Хочешь просмотреть заново тех, кого случайно пропустил(а)? Используй «🔄 Сброс пропущенных анкет» или позови друзей по рефералке!</i>"
        )
        kb_rows = []
        if is_adult_age(int(me["age"])) and PAYMENTS_ENABLED:
            kb_rows.append(
                [InlineKeyboardButton(text="🔄 Сбросить пропущенные анкеты", callback_data="shop:item:reset_skips")]
            )
            kb_rows.append(
                [InlineKeyboardButton(text="🚀 Поднять мою анкету в ТОП", callback_data="shop:item:boost_24")]
            )
        kb_rows.append([InlineKeyboardButton(text="🎁 Пригласить друзей (+VIP)", callback_data="menu:referral")])
        kb_rows.append([vpn_button()])
        kb_rows.append([InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="menu:home")])
        await send_screen(bot, user_id, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
        return

    title = "🔥 <b>Подходящая анкета</b>"
    if prefix_notice:
        title = f"{prefix_notice}\n\n{title}"

    caption = format_profile_card(cand, title=title)
    kb = candidate_kb(int(cand["user_id"]), int(me["age"]))
    await send_screen(bot, user_id, caption, reply_markup=kb, photo_id=cand["photo_id"])


async def render_reg_step(bot: Bot, user_id: int, step: str, error_msg: Optional[str] = None) -> None:
    prefix = f"⚠️ <b>{error_msg}</b>\n\n" if error_msg else ""

    if step == "name":
        text = (
            f"{prefix}📝 <b>Шаг 1 из 5 — Как тебя зовут?</b>\n\n"
            "Напиши своё имя (от 2 до 30 символов):"
        )
        await send_screen(bot, user_id, text, reply_markup=reg_nav_kb(can_back=False))
    elif step == "age":
        text = (
            f"{prefix}🎂 <b>Шаг 2 из 5 — Сколько тебе лет?</b>\n\n"
            "Отправь возраст цифрами (от <b>14</b> до <b>80</b>):"
        )
        await send_screen(bot, user_id, text, reply_markup=reg_nav_kb(can_back=True))
    elif step == "city":
        text = (
            f"{prefix}🏙 <b>Шаг 3 из 5 — Из какого ты города?</b>\n\n"
            "Анкеты из твоего города будут показываться тебе в первую очередь:"
        )
        await send_screen(bot, user_id, text, reply_markup=reg_nav_kb(can_back=True))
    elif step == "bio":
        text = (
            f"{prefix}💬 <b>Шаг 4 из 5 — Расскажи немного о себе</b>\n\n"
            "Чем увлекаешься, кого хочешь найти? (до 350 символов):"
        )
        await send_screen(bot, user_id, text, reply_markup=reg_nav_kb(can_back=True))
    elif step == "photo":
        text = (
            f"{prefix}📸 <b>Шаг 5 из 5 — Отправь своё фото</b>\n\n"
            "Анкеты с фотографией получают в 8 раз больше лайков!\n"
            "Отправь фото как картинку или нажми «Пропустить фото»:"
        )
        await send_screen(bot, user_id, text, reply_markup=reg_nav_kb(can_back=True, skip_photo=True))


async def notify_inviter_on_registration(bot: Bot, inviter_id: int, friend_name: str, new_ref_count: int) -> None:
    vip_bonus_str = (
        "\n👑 <b>СУПЕР-БОНУС: Ты пригласил(а) 3 друзей и получаешь +24 часа VIP-статуса и +1 Супер-письмо!</b>"
        if new_ref_count % 3 == 0
        else f"\n👑 До бесплатного VIP осталось пригласить: <b>{3 - (new_ref_count % 3)} чел.</b>"
    )
    try:
        await bot.send_message(
            inviter_id,
            f"🎉 <b>Твой друг {html.escape(friend_name)} создал(а) анкету по твоей ссылке!</b>\n\n"
            f"🎁 Тебе начислено:\n"
            f"• <b>+1 Супер-письмо 💌</b>\n"
            f"• <b>+6 часов поднятия анкеты в ТОП 🚀</b>"
            f"{vip_bonus_str}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🎁 Моя рефералка", callback_data="menu:referral")],
                    [vpn_button()],
                ]
            ),
        )
    except Exception:
        pass


# =========================
# HANDLERS
# =========================
dp = Dispatcher(storage=MemoryStorage())


@dp.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    await safe_delete_message(bot, message.chat.id, message.message_id)

    user_id = message.from_user.id
    args = (command.args or "").strip()

    ref_notice = None
    if args.startswith("ref_"):
        raw_inv = args.replace("ref_", "").strip()
        if raw_inv.isdigit():
            inviter_id = int(raw_inv)
            if save_pending_referral(user_id, inviter_id):
                ref_notice = "🎁 <b>Ты перешёл(ла) по приглашению друга!</b>\nСоздай анкету сейчас и получи <b>2 Супер-письма 💌</b> в подарок!"

    if get_profile(user_id):
        update_username(user_id, message.from_user.username)

    await render_home(bot, user_id, notice=ref_notice)


@dp.message(Command("paysupport"))
async def cmd_paysupport(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.set_state(SupportState.waiting_text)
    await safe_delete_message(bot, message.chat.id, message.message_id)
    text = (
        "🛟 <b>Поддержка по платежам и VIP-услугам</b>\n\n"
        "Опиши свой вопрос, укажи время оплаты или приложи детали — сообщение сразу получит администратор.\n\n"
        "Напиши обращение одним сообщением ниже 👇"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="menu:home")]]
    )
    await send_screen(bot, message.from_user.id, text, reply_markup=kb)


@dp.message(SupportState.waiting_text)
async def handle_support_text(message: Message, state: FSMContext, bot: Bot) -> None:
    await safe_delete_message(bot, message.chat.id, message.message_id)
    await state.clear()

    if ADMIN_ID:
        user_info = f"@{message.from_user.username}" if message.from_user.username else f"ID {message.from_user.id}"
        try:
            await bot.send_message(
                ADMIN_ID,
                f"🆘 <b>Обращение в /paysupport</b>\n"
                f"От: {user_info} (<code>{message.from_user.id}</code>)\n\n"
                f"Текст:\n{html.escape(message.text or '<без текста>')}",
            )
        except Exception:
            pass

    await render_home(bot, message.from_user.id, notice="✅ <b>Твоё обращение отправлено в поддержку!</b>")


# --- АДМИН-ПАНЕЛЬ (/admin) ---
@dp.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext, bot: Bot) -> None:
    await safe_delete_message(bot, message.chat.id, message.message_id)
    if message.from_user.id != ADMIN_ID or ADMIN_ID == 0:
        await render_home(bot, message.from_user.id)
        return

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS cnt FROM profiles")
    total_users = int(cur.fetchone()["cnt"])

    cur.execute("SELECT COUNT(*) AS cnt FROM profiles WHERE vip_until > ?", (now_ts(),))
    vip_users = int(cur.fetchone()["cnt"])

    cur.execute("SELECT COUNT(*) AS cnt FROM profiles WHERE referred_by > 0")
    ref_invited = int(cur.fetchone()["cnt"])

    cur.execute(
        "SELECT COUNT(*) AS cnt, COALESCE(SUM(amount), 0) AS rev, COALESCE(SUM(commission), 0) AS comm FROM orders WHERE status = 'paid'"
    )
    ord_row = cur.fetchone()
    paid_orders = int(ord_row["cnt"])
    total_rev = int(ord_row["rev"])
    total_comm = int(ord_row["comm"])
    conn.close()

    token_ok = bool(YOOMONEY_TOKEN and "ваш_" not in YOOMONEY_TOKEN.lower() and len(YOOMONEY_TOKEN) >= 20)

    text = (
        "👑 <b>Панель управления и Монетизации</b>\n\n"
        f"👥 Всего анкет в базе: <b>{total_users}</b>\n"
        f"🤝 Пришли по рефералке: <b>{ref_invited}</b>\n"
        f"💎 Активных VIP-подписок: <b>{vip_users}</b>\n"
        f"💳 Успешных оплат: <b>{paid_orders}</b>\n"
        f"💰 Общий оборот: <b>{total_rev} ₽</b>\n"
        f"📈 Чистая комиссия ({COMMISSION_PERCENT}%): <b>{total_comm} ₽</b>\n\n"
        f"👛 Кошелек ЮMoney: <code>{YOOMONEY_WALLET or 'Не задан в .env'}</code>\n"
        f"🔑 Режим проверки оплат: <b>{'Авто (API) + Ручной ✅' if token_ok else 'Подтверждение в 1 клик в боте ✅'}</b>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Сделать рассылку по базе", callback_data="admin:broadcast")],
            [InlineKeyboardButton(text="🎁 Выдать VIP на 7 дней по ID", callback_data="admin:gift_vip")],
            [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="menu:home")],
        ]
    )
    await send_screen(bot, message.from_user.id, text, reply_markup=kb)


@dp.callback_query(F.data == "admin:gift_vip")
async def cb_admin_gift_vip(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if callback.from_user.id != ADMIN_ID:
        await callback.answer()
        return
    await callback.answer()
    await state.set_state(AdminGiftState.waiting_user_id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="menu:home")]]
    )
    await send_screen(
        bot,
        callback.from_user.id,
        "🎁 <b>Отправь Telegram ID пользователя, которому нужно начислить 7 дней VIP + 3 Супер-письма:</b>",
        reply_markup=kb,
    )


@dp.message(AdminGiftState.waiting_user_id)
async def handle_admin_gift_vip(message: Message, state: FSMContext, bot: Bot) -> None:
    await safe_delete_message(bot, message.chat.id, message.message_id)
    await state.clear()
    if message.from_user.id != ADMIN_ID:
        return

    raw = (message.text or "").strip()
    if not raw.isdigit() or not get_profile(int(raw)):
        await render_home(bot, message.from_user.id, notice="⚠️ <b>Пользователь с таким ID не найден в базе анкет.</b>")
        return

    target_uid = int(raw)
    add_vip_days(target_uid, 7, bonus_superlikes=3)
    try:
        await bot.send_message(
            target_uid,
            "👑 <b>Администратор начислил тебе VIP-статус на 7 дней и +3 Супер-письма!</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[vpn_button()]]),
        )
    except Exception:
        pass

    await render_home(bot, message.from_user.id, notice=f"✅ <b>VIP на 7 дней выдан пользователю {target_uid}!</b>")


@dp.callback_query(F.data == "admin:broadcast")
async def cb_admin_broadcast(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if callback.from_user.id != ADMIN_ID:
        await callback.answer()
        return
    await callback.answer()
    await state.set_state(AdminBroadcastState.waiting_post)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="menu:home")]]
    )
    await send_screen(
        bot,
        callback.from_user.id,
        "📢 <b>Отправь текст рекламного или информационного поста для рассылки всем пользователям:</b>",
        reply_markup=kb,
    )


@dp.message(AdminBroadcastState.waiting_post)
async def handle_admin_broadcast(message: Message, state: FSMContext, bot: Bot) -> None:
    await safe_delete_message(bot, message.chat.id, message.message_id)
    await state.clear()
    if message.from_user.id != ADMIN_ID or not message.text:
        await render_home(bot, message.from_user.id)
        return

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM profiles")
    users = [int(r["user_id"]) for r in cur.fetchall()]
    conn.close()

    sent = 0
    for uid in users:
        try:
            await bot.send_message(
                uid,
                message.text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[vpn_button()]]),
            )
            sent += 1
            await asyncio.sleep(0.04)
        except Exception:
            pass

    await render_home(bot, message.from_user.id, notice=f"✅ <b>Рассылка завершена! Доставлено: {sent} чел.</b>")


# --- НАВИГАЦИЯ ПО МЕНЮ И РЕФЕРАЛКЕ ---
@dp.callback_query(F.data == "menu:home")
async def cb_home(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    await callback.answer()
    await render_home(bot, callback.from_user.id)


@dp.callback_query(F.data == "menu:me")
async def cb_me(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    await callback.answer()
    await render_home(bot, callback.from_user.id)


@dp.callback_query(F.data == "menu:referral")
async def cb_referral(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    await render_referral_screen(bot, callback.from_user.id)


@dp.callback_query(F.data == "ref:leaderboard")
async def cb_ref_leaderboard(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    top_rows = get_top_referrers(limit=10)

    lines = ["🏆 <b>ТОП-10 амбассадоров «Лавчика»</b>\n"]
    if not top_rows:
        lines.append("<i>Пока никто не пригласил друзей. Стань первым в рейтинге и забери бесплатный VIP!</i>")
    else:
        medals = ["🥇", "🥈", "🥉"]
        for idx, r in enumerate(top_rows, start=1):
            badge = medals[idx - 1] if idx <= 3 else f"{idx}."
            vip_mark = " 👑" if int(r["vip_until"]) > now_ts() else ""
            lines.append(
                f"{badge} <b>{html.escape(str(r['name']))}, {r['age']}</b> ({html.escape(str(r['city']))}){vip_mark} — <b>{r['ref_count']} др.</b>"
            )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад к моей ссылке", callback_data="menu:referral")],
            [InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home")],
        ]
    )
    await send_screen(bot, callback.from_user.id, "\n".join(lines), reply_markup=kb)


@dp.callback_query(F.data == "menu:browse")
async def cb_browse(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    await render_next_candidate(bot, callback.from_user.id)


@dp.callback_query(F.data == "menu:settings")
async def cb_settings(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    me = get_profile(callback.from_user.id)
    if not me:
        await render_home(bot, callback.from_user.id)
        return

    text = format_profile_card(me, title="⚙️ <b>Настройки твоей анкеты</b>", show_status=True)
    await send_screen(
        bot,
        callback.from_user.id,
        text,
        reply_markup=settings_kb(callback.from_user.id),
        photo_id=me["photo_id"],
    )


@dp.callback_query(F.data == "settings:toggle")
async def cb_settings_toggle(callback: CallbackQuery, bot: Bot) -> None:
    me = get_profile(callback.from_user.id)
    if not me:
        await callback.answer()
        await render_home(bot, callback.from_user.id)
        return

    new_active = int(me["active"]) == 0
    set_active(callback.from_user.id, new_active)
    status_msg = "👁 Анкета снова видна в поиске!" if new_active else "🙈 Анкета скрыта из поиска"
    await callback.answer(status_msg)

    me_updated = get_profile(callback.from_user.id)
    text = format_profile_card(me_updated, title="⚙️ <b>Настройки твоей анкеты</b>", show_status=True)
    await send_screen(
        bot,
        callback.from_user.id,
        text,
        reply_markup=settings_kb(callback.from_user.id),
        photo_id=me_updated["photo_id"],
    )


@dp.callback_query(F.data == "settings:delete")
async def cb_settings_delete(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Да, удалить навсегда", callback_data="settings:delete_confirm")],
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:settings"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="menu:home"),
            ],
        ]
    )
    await send_screen(
        bot,
        callback.from_user.id,
        "⚠️ <b>Ты уверен(а), что хочешь удалить анкету?</b>\n\nВсе твои симпатии, матчи и история будут стерты.",
        reply_markup=kb,
    )


@dp.callback_query(F.data == "settings:delete_confirm")
async def cb_settings_delete_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    delete_profile(callback.from_user.id)
    await callback.answer("Анкета удалена")
    await render_home(bot, callback.from_user.id, notice="🗑 <b>Твоя анкета была успешно удалена.</b>")


# --- РЕГИСТРАЦИЯ И РЕДАКТИРОВАНИЕ С КНОПКАМИ "НАЗАД" И "ОТМЕНА" ---
@dp.callback_query(F.data == "profile:create")
async def cb_profile_create(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await callback.answer()
    await state.clear()
    await state.set_state(RegStates.name)
    await render_reg_step(bot, callback.from_user.id, "name")


@dp.callback_query(F.data == "reg:cancel")
async def cb_reg_cancel(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    await callback.answer("Отменено")
    await render_home(bot, callback.from_user.id, notice="❌ <b>Заполнение анкеты отменено.</b>")


@dp.callback_query(F.data == "reg:back")
async def cb_reg_back(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await callback.answer()
    current = await state.get_state()
    if current == RegStates.age.state:
        await state.set_state(RegStates.name)
        await render_reg_step(bot, callback.from_user.id, "name")
    elif current == RegStates.city.state:
        await state.set_state(RegStates.age)
        await render_reg_step(bot, callback.from_user.id, "age")
    elif current == RegStates.bio.state:
        await state.set_state(RegStates.city)
        await render_reg_step(bot, callback.from_user.id, "city")
    elif current == RegStates.photo.state:
        await state.set_state(RegStates.bio)
        await render_reg_step(bot, callback.from_user.id, "bio")
    else:
        await state.clear()
        await render_home(bot, callback.from_user.id)


@dp.message(RegStates.name)
async def reg_name(message: Message, state: FSMContext, bot: Bot) -> None:
    await safe_delete_message(bot, message.chat.id, message.message_id)
    name = (message.text or "").strip()
    if len(name) < 2 or len(name) > 30:
        await render_reg_step(bot, message.from_user.id, "name", error_msg="Имя должно быть от 2 до 30 символов.")
        return

    await state.update_data(name=name)
    await state.set_state(RegStates.age)
    await render_reg_step(bot, message.from_user.id, "age")


@dp.message(RegStates.age)
async def reg_age(message: Message, state: FSMContext, bot: Bot) -> None:
    await safe_delete_message(bot, message.chat.id, message.message_id)
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await render_reg_step(bot, message.from_user.id, "age", error_msg="Введи возраст только цифрами (например: 19).")
        return

    age = int(raw)
    if age < 14 or age > 80:
        await render_reg_step(bot, message.from_user.id, "age", error_msg="Допустимый возраст — от 14 до 80 лет.")
        return

    await state.update_data(age=age)
    await state.set_state(RegStates.city)
    await render_reg_step(bot, message.from_user.id, "city")


@dp.message(RegStates.city)
async def reg_city(message: Message, state: FSMContext, bot: Bot) -> None:
    await safe_delete_message(bot, message.chat.id, message.message_id)
    city = (message.text or "").strip()
    if len(city) < 2 or len(city) > 40:
        await render_reg_step(bot, message.from_user.id, "city", error_msg="Название города должно быть от 2 до 40 символов.")
        return

    await state.update_data(city=city)
    await state.set_state(RegStates.bio)
    await render_reg_step(bot, message.from_user.id, "bio")


@dp.message(RegStates.bio)
async def reg_bio(message: Message, state: FSMContext, bot: Bot) -> None:
    await safe_delete_message(bot, message.chat.id, message.message_id)
    bio = (message.text or "").strip()
    if len(bio) < 5 or len(bio) > 350:
        await render_reg_step(bot, message.from_user.id, "bio", error_msg="Описание должно быть от 5 до 350 символов.")
        return

    await state.update_data(bio=bio)
    await state.set_state(RegStates.photo)
    await render_reg_step(bot, message.from_user.id, "photo")


@dp.callback_query(RegStates.photo, F.data == "reg:skip_photo")
async def reg_skip_photo(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await callback.answer()
    data = await state.get_data()
    is_new, inviter_id, new_ref_cnt = upsert_profile(
        user_id=callback.from_user.id,
        username=callback.from_user.username,
        name=data["name"],
        age=int(data["age"]),
        city=data["city"],
        bio=data["bio"],
        photo_id=None,
    )
    await state.clear()

    if is_new and inviter_id:
        await notify_inviter_on_registration(bot, inviter_id, data["name"], new_ref_cnt)
        await render_home(
            bot,
            callback.from_user.id,
            notice="🎉 <b>Анкета сохранена! Тебе начислено 2 Супер-письма 💌 по приглашению друга!</b>",
        )
    else:
        await render_home(bot, callback.from_user.id, notice="🎉 <b>Анкета успешно сохранена!</b>")


@dp.message(RegStates.photo)
async def reg_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    await safe_delete_message(bot, message.chat.id, message.message_id)
    if not message.photo:
        await render_reg_step(
            bot,
            message.from_user.id,
            "photo",
            error_msg="Пожалуйста, отправь именно фотографию или нажми «Пропустить фото».",
        )
        return

    photo_id = message.photo[-1].file_id
    data = await state.get_data()
    is_new, inviter_id, new_ref_cnt = upsert_profile(
        user_id=message.from_user.id,
        username=message.from_user.username,
        name=data["name"],
        age=int(data["age"]),
        city=data["city"],
        bio=data["bio"],
        photo_id=photo_id,
    )
    await state.clear()

    if is_new and inviter_id:
        await notify_inviter_on_registration(bot, inviter_id, data["name"], new_ref_cnt)
        await render_home(
            bot,
            message.from_user.id,
            notice="🎉 <b>Анкета с фото сохранена! Тебе начислено 2 Супер-письма 💌 по приглашению друга!</b>",
        )
    else:
        await render_home(bot, message.from_user.id, notice="🎉 <b>Анкета с фото успешно сохранена!</b>")


# --- ЛАЙКИ, СУПЕР-ПИСЬМА, МАТЧИ И "КТО ЛАЙКНУЛ" ---
@dp.callback_query(F.data.startswith("act:"))
async def cb_candidate_action(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer()
        return

    action = parts[1]
    target_id = int(parts[2])
    user_id = callback.from_user.id

    me = get_profile(user_id)
    target = get_profile(target_id)
    if not me or not target:
        await callback.answer("Анкета больше недоступна")
        await render_next_candidate(bot, user_id)
        return

    if not same_age_group(int(me["age"]), int(target["age"])):
        await callback.answer("Возрастное ограничение!", show_alert=True)
        await render_next_candidate(bot, user_id)
        return

    if action == "super":
        if int(me["superlikes"]) <= 0:
            await callback.answer("У тебя закончились Супер-письма! Пополни их или пригласи друга.", show_alert=True)
            await render_shop_item(bot, user_id, "super_5")
            return

        await callback.answer()
        await state.set_state(SuperMsgState.waiting_text)
        await state.update_data(target_id=target_id)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="⬅️ Назад к анкете", callback_data="menu:browse"),
                    InlineKeyboardButton(text="❌ Отмена", callback_data="menu:home"),
                ]
            ]
        )
        await send_screen(
            bot,
            user_id,
            f"💌 <b>Супер-письмо для {html.escape(target['name'])}</b>\n\n"
            f"Напиши сообщение (до 300 символов). Мы мгновенно доставим его вместе со ссылкой на твой профиль!\n"
            f"<i>Осталось Супер-писем: {int(me['superlikes'])} шт.</i>",
            reply_markup=kb,
        )
        return

    if action == "skip":
        add_reaction(user_id, target_id, "skip")
        await callback.answer("Пропущено 👎")
        await render_next_candidate(bot, user_id)
        return

    if action == "block":
        block_user(user_id, target_id)
        add_reaction(user_id, target_id, "block")
        await callback.answer("Пользователь добавлен в ЧС 🚫")
        await render_next_candidate(bot, user_id)
        return

    if action == "report":
        report_user(user_id, target_id)
        block_user(user_id, target_id)
        add_reaction(user_id, target_id, "report")
        await callback.answer("Жалоба отправлена модераторам ⚠️")
        if ADMIN_ID:
            try:
                await bot.send_message(
                    ADMIN_ID,
                    f"⚠️ <b>Жалоба на анкету</b>\n"
                    f"От: <code>{user_id}</code>\n"
                    f"На: <code>{target_id}</code> ({html.escape(target['name'])}, {target['age']})",
                )
            except Exception:
                pass
        await render_next_candidate(bot, user_id)
        return

    if action == "like":
        if not is_vip(me) and is_adult_age(int(me["age"])):
            today_cnt = count_today_likes(user_id)
            if today_cnt >= FREE_DAILY_LIKES:
                await callback.answer("Дневной лимит бесплатных лайков исчерпан!", show_alert=True)
                await render_shop_item(
                    bot,
                    user_id,
                    "vip_7",
                    custom_notice=f"🔥 <b>Ты поставил(а) максимум бесплатных лайков за сегодня ({FREE_DAILY_LIKES} из {FREE_DAILY_LIKES})!</b>\nПодключи VIP или пригласи друзей, чтобы лайкать без ограничений:",
                )
                return

        add_reaction(user_id, target_id, "like")

        if has_liked(target_id, user_id):
            add_match(user_id, target_id)
            await callback.answer("🎉 Взаимная симпатия!")

            my_link = (
                f"@{me['username']}"
                if me["username"]
                else f"<a href='tg://user?id={user_id}'>{html.escape(me['name'])}</a>"
            )
            target_link = (
                f"@{target['username']}"
                if target["username"]
                else f"<a href='tg://user?id={target_id}'>{html.escape(target['name'])}</a>"
            )

            try:
                await bot.send_message(
                    target_id,
                    f"💞 <b>У тебя взаимная симпатия!</b>\n\n"
                    f"Тебе ответил(а) взаимностью: <b>{html.escape(me['name'])}, {me['age']}</b> ({html.escape(me['city'])})\n"
                    f"👉 Написать: {my_link}",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[vpn_button()]]),
                )
            except Exception:
                pass

            notice = (
                f"💞 <b>Взаимная симпатия с {html.escape(target['name'])}!</b>\n"
                f"👉 Контакт для общения: {target_link}"
            )
            await render_next_candidate(bot, user_id, prefix_notice=notice)
        else:
            await callback.answer("❤️ Лайк отправлен!")
            try:
                await bot.send_message(
                    target_id,
                    "🔥 <b>Кому-то понравилась твоя анкета!</b>\n"
                    "Зайди в раздел «👀 Кто меня лайкнул», чтобы узнать кто это.",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [InlineKeyboardButton(text="👀 Посмотреть кто лайкнул", callback_data="menu:who_liked")],
                            [vpn_button()],
                        ]
                    ),
                )
            except Exception:
                pass

            await render_next_candidate(bot, user_id)


@dp.message(SuperMsgState.waiting_text)
async def handle_super_msg(message: Message, state: FSMContext, bot: Bot) -> None:
    await safe_delete_message(bot, message.chat.id, message.message_id)
    text_msg = (message.text or "").strip()
    if len(text_msg) < 2 or len(text_msg) > 300:
        await send_screen(
            bot,
            message.from_user.id,
            "⚠️ <b>Сообщение должно быть от 2 до 300 символов. Попробуй ещё раз:</b>",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="menu:browse")]]
            ),
        )
        return

    data = await state.get_data()
    target_id = int(data.get("target_id", 0))
    await state.clear()

    me = get_profile(message.from_user.id)
    target = get_profile(target_id)
    if not me or not target:
        await render_next_candidate(bot, message.from_user.id)
        return

    if not use_superlike(message.from_user.id):
        await render_shop_item(bot, message.from_user.id, "super_5")
        return

    add_reaction(message.from_user.id, target_id, "like")
    my_link = (
        f"@{me['username']}"
        if me["username"]
        else f"<a href='tg://user?id={message.from_user.id}'>{html.escape(me['name'])}</a>"
    )

    try:
        await bot.send_message(
            target_id,
            f"💌 <b>Тебе пришло Супер-письмо!</b>\n\n"
            f"От: <b>{html.escape(me['name'])}, {me['age']}</b> ({html.escape(me['city'])})\n"
            f"<blockquote>{html.escape(text_msg)}</blockquote>\n"
            f"👉 Ответить в ЛС: {my_link}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[vpn_button()]]),
        )
    except Exception:
        pass

    await render_next_candidate(
        bot,
        message.from_user.id,
        prefix_notice=f"✅ <b>Супер-письмо для {html.escape(target['name'])} доставлено!</b>",
    )


# --- КТО МЕНЯ ЛАЙКНУЛ ---
@dp.callback_query(F.data == "menu:who_liked")
async def cb_who_liked(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    user_id = callback.from_user.id
    me = get_profile(user_id)
    if not me:
        await render_home(bot, user_id)
        return

    incoming = get_incoming_likes(user_id)
    if not incoming:
        text = (
            "👀 <b>Новых входящих лайков пока нет</b>\n\n"
            "💡 <i>Совет: подними анкету в ТОП или пригласи друзей, чтобы получать в 5 раз больше симпатий!</i>"
        )
        rows = []
        if is_adult_age(int(me["age"])) and PAYMENTS_ENABLED:
            rows.append([InlineKeyboardButton(text="🚀 Поднять анкету в ТОП", callback_data="shop:item:boost_24")])
        rows.append([InlineKeyboardButton(text="🎁 Бесплатный VIP за друзей", callback_data="menu:referral")])
        rows.append([vpn_button()])
        rows.append([InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="menu:home")])
        await send_screen(bot, user_id, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        return

    adm = incoming[0]
    viewer_vip = is_vip(me) or is_minor_age(int(me["age"]))

    if viewer_vip:
        title = f"😍 <b>Тебя лайкнули ({len(incoming)} чел.)!</b>"
        caption = format_profile_card(adm, title=title)
        await send_screen(
            bot,
            user_id,
            caption,
            reply_markup=who_liked_kb(int(adm["user_id"]), True),
            photo_id=adm["photo_id"],
        )
    else:
        masked_name = html.escape(str(adm["name"])[:1]) + "***"
        city = html.escape(str(adm["city"]))
        text = (
            f"🔥 <b>Твоя анкета понравилась {len(incoming)} чел.!</b>\n\n"
            f"👤 <b>{masked_name}, {adm['age']}</b>  📍 <i>{city}</i>\n"
            f"<blockquote>🔒 Фотография и контакты скрыты. Подключи VIP-статус (или пригласи 3 друзей бесплатно), чтобы сразу видеть всех, кто тебя лайкает!</blockquote>"
        )
        await send_screen(bot, user_id, text, reply_markup=who_liked_kb(int(adm["user_id"]), False))


@dp.callback_query(F.data == "menu:matches")
async def cb_matches(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    user_id = callback.from_user.id
    rows = get_matches(user_id)

    if not rows:
        text = (
            "💞 <b>У тебя пока нет взаимных симпатий</b>\n\n"
            "Ставь лайки в разделе «🔥 Смотреть анкеты» — как только симпатия совпадет, здесь появится ссылка на профиль человека!"
        )
        await send_screen(bot, user_id, text, reply_markup=back_home_kb())
        return

    lines = ["💞 <b>Твои взаимные симпатии:</b>\n"]
    for idx, r in enumerate(rows, start=1):
        name = html.escape(str(r["name"]))
        age = int(r["age"])
        city = html.escape(str(r["city"]))
        link = f"@{r['username']}" if r["username"] else f"<a href='tg://user?id={r['user_id']}'>Написать в ЛС</a>"
        lines.append(f"{idx}. <b>{name}, {age}</b> ({city}) — {link}")

    await send_screen(bot, user_id, "\n".join(lines), reply_markup=back_home_kb())


# =========================
# МАГАЗИН И ОПЛАТА ЮMONEY (+30% КОМИССИЯ + ПОДТВЕРЖДЕНИЕ В 1 КЛИК)
# =========================
async def render_shop_item(
    bot: Bot,
    user_id: int,
    product_code: str,
    custom_notice: Optional[str] = None,
) -> None:
    me = get_profile(user_id)
    if not me:
        await render_home(bot, user_id)
        return

    if is_minor_age(int(me["age"])):
        await render_home(bot, user_id, notice="🔒 <b>Платные услуги доступны только пользователям 18+.</b>")
        return

    prod = PRODUCTS.get(product_code)
    if not prod:
        await render_home(bot, user_id)
        return

    base_p, comm_p, total_p = calc_price_with_commission(prod.base_price_rub)
    order_id = f"{product_code}_{user_id}_{uuid.uuid4().hex[:8]}"
    create_order(order_id, user_id, product_code, base_p, comm_p, total_p, "RUB")

    pay_url = generate_yoomoney_link(order_id, total_p, f"{prod.title} (ID {user_id})")

    header = f"{custom_notice}\n\n" if custom_notice else ""
    text = (
        f"{header}"
        f"{prod.emoji} <b>{prod.title}</b>\n\n"
        f"<blockquote>{prod.description}</blockquote>\n\n"
        f"💳 <b>Детали оплаты (ЮMoney / Банковская карта):</b>\n"
        f"• Стоимость тарифа: <b>{base_p} ₽</b>\n"
        f"• Сервисный сбор ({COMMISSION_PERCENT}%): <b>+{comm_p} ₽</b>\n"
        f"• <b>Итого к оплате: {total_p} ₽</b>\n\n"
        f"1️⃣ Нажми кнопку <b>«Оплатить через ЮMoney / Картой»</b>.\n"
        f"2️⃣ После оплаты вернись сюда и нажми <b>«🔄 Проверить оплату»</b>!"
    )

    if not YOOMONEY_WALLET:
        text += "\n\n⚠️ <i>Внимание администратору: укажите номер кошелька YOOMONEY_WALLET в файле .env!</i>"

    await send_screen(
        bot,
        user_id,
        text,
        reply_markup=payment_invoice_kb(order_id, pay_url, product_code),
    )


@dp.callback_query(F.data == "menu:shop")
async def cb_shop_menu(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    me = get_profile(callback.from_user.id)
    if not me:
        await render_home(bot, callback.from_user.id)
        return

    if is_minor_age(int(me["age"])):
        await render_home(bot, callback.from_user.id, notice="🔒 <b>Магазин доступен только для категории 18+.</b>")
        return

    text = (
        "👑 <b>Премиум-магазин и ускорение анкеты</b>\n\n"
        "Выбери нужную привилегию, чтобы получать максимум внимания, видеть всех поклонников и писать первым без ожидания взаимного лайка:\n\n"
        f"<i>Все платежи защищены и проходят через официальный шлюз ЮMoney (yoomoney.ru). Комиссия сервиса составляет {COMMISSION_PERCENT}%.</i>"
    )
    await send_screen(bot, callback.from_user.id, text, reply_markup=shop_menu_kb())


@dp.callback_query(F.data.startswith("shop:item:"))
async def cb_shop_item(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    product_code = callback.data.split(":")[2]
    await render_shop_item(bot, callback.from_user.id, product_code)


async def complete_and_reward_order(bot: Bot, order_id: str, charge_id: str) -> str:
    """Помечает заказ оплаченным, выдает услугу покупателю и бонус его рефоводу."""
    order = get_order(order_id)
    if not order:
        return "Заказ не найден"

    mark_order_paid(order_id, charge_id)
    prod_code = str(order["product"])
    user_id = int(order["user_id"])

    reward_msg = apply_order_reward(user_id, prod_code)

    me = get_profile(user_id)
    if me and int(me["referred_by"]) > 0:
        inviter_id = int(me["referred_by"])
        if get_profile(inviter_id):
            add_vip_days(inviter_id, 1, bonus_superlikes=2)
            try:
                await bot.send_message(
                    inviter_id,
                    "💎 <b>Партнёрский бонус!</b>\n"
                    "Приглашённый тобой друг совершил покупку в магазине — тебе начислено <b>+24 часа VIP 👑 и +2 Супер-письма 💌</b>!",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[vpn_button()]]),
                )
            except Exception:
                pass

    return reward_msg


@dp.callback_query(F.data.startswith("pay:check:"))
async def cb_pay_check(callback: CallbackQuery, bot: Bot) -> None:
    order_id = callback.data.split(":")[2]
    order = get_order(order_id)
    if not order:
        await callback.answer("Заказ не найден", show_alert=True)
        return

    if order["status"] == "paid":
        await callback.answer("Этот заказ уже оплачен и активирован! 🎉", show_alert=True)
        await render_home(bot, callback.from_user.id)
        return

    ok, info, need_admin_manual = await verify_yoomoney_payment(order_id, int(order["amount"]))

    if not ok:
        # Если токен ЮMoney не настроен (401), отправляем заявку админу с кнопкой выдачи в 1 клик!
        if need_admin_manual and ADMIN_ID:
            last_sent = admin_notified_orders.get(order_id, 0)
            if now_ts() - last_sent > 60:
                admin_notified_orders[order_id] = now_ts()
                prod = PRODUCTS.get(str(order["product"]))
                prod_title = prod.title if prod else str(order["product"])
                buyer_id = int(order["user_id"])
                buyer_prof = get_profile(buyer_id)
                buyer_name = html.escape(buyer_prof["name"]) if buyer_prof else str(buyer_id)

                admin_kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=f"✅ Подтвердить ({order['amount']} ₽) и выдать",
                                callback_data=f"adminpay:approve:{order_id}",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="❌ Оплата не поступила",
                                callback_data=f"adminpay:reject:{order_id}",
                            )
                        ],
                    ]
                )
                try:
                    await bot.send_message(
                        ADMIN_ID,
                        f"🔔 <b>Проверка оплаты заказа!</b>\n\n"
                        f"👤 Покупатель: <b>{buyer_name}</b> (ID: <code>{buyer_id}</code>)\n"
                        f"📦 Услуга: <b>{prod_title}</b>\n"
                        f"💰 Сумма к зачислению: <b>{order['amount']} ₽</b>\n"
                        f"🏷 Метка заказа: <code>{order_id}</code>\n\n"
                        f"<i>Проверьте поступление {order['amount']} ₽ в приложении ЮMoney и нажмите кнопку ниже, чтобы бот мгновенно выдал привилегию:</i>",
                        reply_markup=admin_kb,
                    )
                except Exception:
                    pass

        await callback.answer(info, show_alert=True)
        return

    # Если автопроверка по API ЮMoney прошла успешно
    reward_msg = await complete_and_reward_order(bot, order_id, info)

    if ADMIN_ID:
        try:
            await bot.send_message(
                ADMIN_ID,
                f"💰 <b>Автоматическая оплата ЮMoney!</b>\n"
                f"Пользователь: <code>{callback.from_user.id}</code>\n"
                f"Услуга: <b>{order['product']}</b>\n"
                f"Сумма: <b>{order['amount']} ₽</b> (вкл. комиссию {order['commission']} ₽)",
            )
        except Exception:
            pass

    await callback.answer("Оплата подтверждена! 🎉", show_alert=True)
    await render_home(bot, callback.from_user.id, notice=reward_msg)


@dp.callback_query(F.data.startswith("adminpay:"))
async def cb_admin_payment_decision(callback: CallbackQuery, bot: Bot) -> None:
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступно только администратору", show_alert=True)
        return

    parts = callback.data.split(":")
    decision = parts[1]
    order_id = parts[2]

    order = get_order(order_id)
    if not order:
        await callback.answer("Заказ не найден", show_alert=True)
        return

    buyer_id = int(order["user_id"])

    if decision == "approve":
        if order["status"] == "paid":
            await callback.answer("Заказ уже был подтвержден ранее!", show_alert=True)
            return

        reward_msg = await complete_and_reward_order(bot, order_id, f"admin_approved_{now_ts()}")
        await callback.answer("✅ Услуга успешно выдана покупателю!", show_alert=True)

        try:
            await callback.message.edit_text(
                callback.message.html_text + "\n\n✅ <b>СТАТУС: ПОДТВЕРЖДЕНО И ВЫДАНО!</b>"
            )
        except Exception:
            pass

        try:
            await render_home(bot, buyer_id, notice=f"🎉 <b>Твоя оплата подтверждена!</b>\n{reward_msg}")
        except Exception:
            pass

    elif decision == "reject":
        await callback.answer("❌ Отклонено", show_alert=True)
        try:
            await callback.message.edit_text(
                callback.message.html_text + "\n\n❌ <b>СТАТУС: ОПЛАТА НЕ НАЙДЕНА</b>"
            )
        except Exception:
            pass
        try:
            await bot.send_message(
                buyer_id,
                "⚠️ <b>Платёж по твоему заказу пока не поступил на кошелёк ЮMoney.</b>\n"
                "Если ты уже оплатил(а) заказ, напиши в поддержку /paysupport.",
            )
        except Exception:
            pass


@dp.message()
async def fallback_cleanup(message: Message, bot: Bot) -> None:
    await safe_delete_message(bot, message.chat.id, message.message_id)
    await render_home(bot, message.from_user.id)


# =========================
# ФОНОВЫЙ HTTP-СЕРВЕР (ЗАЩИТА ОТ SIGTERM НА ХОСТИНГЕ)
# =========================
async def health_handler(request: web.Request) -> web.Response:
    return web.Response(text="OK - Lovchik Dating Bot is running")


async def start_dummy_webserver() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "8080"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"🌐 Health-check веб-сервер запущен на порту {port} (защита от SIGTERM)")
    return runner


# =========================
# ENTRYPOINT
# =========================
async def main() -> None:
    global BOT_USERNAME
    if not BOT_TOKEN or ":" not in BOT_TOKEN:
        logging.error("❌ ОШИБКА: BOT_TOKEN не задан или имеет неверный формат! Проверьте файл .env")
        return

    init_db()
    web_runner = await start_dummy_webserver()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    try:
        me = await bot.get_me()
        if me.username:
            BOT_USERNAME = me.username
        logging.info(f"✅ Авторизация успешна: @{BOT_USERNAME} (ID: {me.id})")

        await bot.set_my_commands(
            [
                BotCommand(command="start", description="🏠 Главное меню"),
                BotCommand(command="paysupport", description="🛟 Поддержка по оплате"),
            ]
        )
        logging.info("🚀 Бот запущен и готов к работе!")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except TelegramUnauthorizedError:
        logging.error(
            "❌ ОШИБКА АВТОРИЗАЦИИ (TelegramUnauthorizedError): Токен бота недействителен или отозван!\n"
            "👉 Решение: Откройте @BotFather -> /mybots -> выберите бота -> API Token -> скопируйте новый токен в .env"
        )
    finally:
        await bot.session.close()
        await web_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
