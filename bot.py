#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import html
import logging
import os
import re
import sqlite3
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable, Optional

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    TelegramObject,
)


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger("BastionPRO")


# =========================================================
# HELPERS AND CONFIGURATION
# =========================================================

def parse_money(value: str) -> int:
    """
    Converts rubles to kopecks.
    Example: "1 250,50" -> 125050
    """
    normalized = str(value).replace(" ", "").replace(",", ".").strip()

    try:
        amount = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError("Некорректная сумма") from exc

    if not amount.is_finite() or amount <= 0:
        raise ValueError("Сумма должна быть положительной")

    kopecks = (amount * 100).quantize(
        Decimal("1"),
        rounding=ROUND_HALF_UP,
    )

    return int(kopecks)


def format_money(kopecks: int | None) -> str:
    value = Decimal(int(kopecks or 0)) / Decimal("100")
    return f"{value:.2f}"


def parse_rate(name: str, default: str) -> Decimal:
    raw = os.getenv(name, default).strip()

    try:
        rate = Decimal(raw)
    except InvalidOperation as exc:
        raise RuntimeError(f"{name} содержит некорректное значение") from exc

    if rate < 0 or rate >= 1:
        raise RuntimeError(f"{name} должен быть от 0 до 0.9999")

    return rate


def calculate_fee(amount_kopecks: int, rate: Decimal) -> int:
    return int(
        (Decimal(amount_kopecks) * rate).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )


def escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def clean_username(username: Optional[str]) -> str:
    return (username or "").replace("@", "").strip()


def parse_admin_ids(raw: str) -> set[int]:
    result: set[int] = set()

    for item in raw.split(","):
        item = item.strip()
        if item:
            try:
                result.add(int(item))
            except ValueError:
                raise RuntimeError(f"Некорректный ADMIN_ID: {item}")

    if not result:
        raise RuntimeError("Не указан ADMIN_ID или ADMIN_IDS")

    return result


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError(
        "Не задан BOT_TOKEN. Укажите новый токен через переменную окружения."
    )

ADMIN_IDS = parse_admin_ids(
    os.getenv("ADMIN_IDS", os.getenv("ADMIN_ID", "")).strip()
)

DB_FILE = os.getenv("DB_FILE", "bastion_market_v3.db").strip()

SUPPORT_CLEAN = re.sub(
    r"[^A-Za-z0-9_]",
    "",
    os.getenv("SUPPORT_HANDLE", "vladofix97").lstrip("@"),
)
SUPPORT_HANDLE = f"@{SUPPORT_CLEAN}" if SUPPORT_CLEAN else "службу поддержки"
SUPPORT_URL = (
    f"https://t.me/{SUPPORT_CLEAN}"
    if SUPPORT_CLEAN
    else None
)

BUYER_DEPOSIT_RATE = parse_rate("BUYER_DEPOSIT_FEE", "0.30")
SELLER_COMMISSION_RATE = parse_rate("SELLER_COMMISSION_RATE", "0.40")

MIN_WITHDRAW_KOPECKS = parse_money(
    os.getenv("MIN_WITHDRAW", "500")
)
MIN_TOPUP_KOPECKS = parse_money(
    os.getenv("MIN_TOPUP", "50")
)
MIN_PRODUCT_PRICE_KOPECKS = parse_money(
    os.getenv("MIN_PRODUCT_PRICE", "50")
)

# Не является интеграцией с платёжной системой.
# Пример:
# PAYMENT_URL_TEMPLATE=
#   https://example.com/pay?order_id={topup_id}&amount={total}
PAYMENT_URL_TEMPLATE = os.getenv(
    "PAYMENT_URL_TEMPLATE",
    "",
).strip()

ONYX_VPN_URL = os.getenv(
    "ONYX_VPN_URL",
    "https://t.me/vpnonyxxbot",
).strip()


def build_payment_url(topup_id: int, total_kopecks: int) -> Optional[str]:
    if not PAYMENT_URL_TEMPLATE:
        return None

    try:
        return PAYMENT_URL_TEMPLATE.format(
            topup_id=topup_id,
            total=format_money(total_kopecks),
            total_kopecks=total_kopecks,
        )
    except Exception:
        logger.exception("Некорректный PAYMENT_URL_TEMPLATE")
        return None


def rate_percent(rate: Decimal) -> str:
    value = rate * 100
    return f"{value.normalize()}%"


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def mask_requisites(value: str) -> str:
    value = value.strip()
    if len(value) <= 4:
        return "••••"
    return "••••" + value[-4:]


# =========================================================
# CATEGORIES
# =========================================================

CATEGORIES = (
    ("max", "🟣 MAX"),
    ("telegram", "✈️ Telegram"),
    ("tg_premium", "⭐ Telegram Premium"),
    ("instagram", "📷 Instagram"),
    ("youtube", "▶️ YouTube"),
    ("tiktok", "🎵 TikTok"),
    ("vk", "🔷 ВКонтакте"),
    ("wibes", "🌸 Wibes"),
    ("twitch", "👾 Twitch"),
    ("steam", "🎮 Steam"),
    ("discord", "💬 Discord"),
    ("twitter", "✖️ Twitter (X)"),
    ("rutube", "🔴 RuTube"),
    ("likee", "💖 Likee"),
    ("dzen", "✨ Дзен"),
    ("trovo", "🦎 Trovo"),
    ("github", "🐙 Github"),
)

CATEGORIES_DICT = dict(CATEGORIES)

MAIN_MENU_TEXTS = {
    "🛍 Каталог товаров",
    "📦 Мои товары",
    "👛 Кошелёк",
    "📂 Мои сделки",
    "👤 Профиль",
    "💳 Вывод средств",
    "🌐 Подключить OnyxVPN",
    "ℹ️ Помощь / Поддержка",
}


# =========================================================
# DATABASE ENGINE
# =========================================================

db_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="bastion_db",
)

_persistent_conn: Optional[sqlite3.Connection] = None


def _get_worker_connection() -> sqlite3.Connection:
    global _persistent_conn

    if _persistent_conn is None:
        _persistent_conn = sqlite3.connect(
            DB_FILE,
            timeout=30,
            check_same_thread=False,
            isolation_level=None,
        )
        _persistent_conn.row_factory = sqlite3.Row

        _persistent_conn.execute("PRAGMA journal_mode=WAL")
        _persistent_conn.execute("PRAGMA synchronous=NORMAL")
        _persistent_conn.execute("PRAGMA foreign_keys=ON")
        _persistent_conn.execute("PRAGMA busy_timeout=30000")
        _persistent_conn.execute("PRAGMA cache_size=-64000")
        _persistent_conn.execute("PRAGMA temp_store=MEMORY")

    return _persistent_conn


def _worker_call(
    function: Callable[..., Any],
    *args: Any,
) -> Any:
    conn = _get_worker_connection()
    return function(conn, *args)


async def db_call(
    function: Callable[..., Any],
    *args: Any,
) -> Any:
    loop = asyncio.get_running_loop()

    return await loop.run_in_executor(
        db_executor,
        functools.partial(_worker_call, function, *args),
    )


def _select_one(
    conn: sqlite3.Connection,
    query: str,
    params: tuple = (),
) -> Optional[dict[str, Any]]:
    row = conn.execute(query, params).fetchone()
    return dict(row) if row else None


def _select_all(
    conn: sqlite3.Connection,
    query: str,
    params: tuple = (),
) -> list[dict[str, Any]]:
    rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def _execute(
    conn: sqlite3.Connection,
    query: str,
    params: tuple = (),
) -> dict[str, int]:
    cursor = conn.execute(query, params)

    return {
        "rowcount": cursor.rowcount,
        "lastrowid": int(cursor.lastrowid or 0),
    }


async def db_fetchone(
    query: str,
    params: tuple = (),
) -> Optional[dict[str, Any]]:
    return await db_call(_select_one, query, params)


async def db_fetchall(
    query: str,
    params: tuple = (),
) -> list[dict[str, Any]]:
    return await db_call(_select_all, query, params)


async def db_execute(
    query: str,
    params: tuple = (),
) -> dict[str, int]:
    return await db_call(_execute, query, params)


def _transaction(
    conn: sqlite3.Connection,
    function: Callable[..., Any],
    *args: Any,
) -> Any:
    conn.execute("BEGIN IMMEDIATE")

    try:
        result = function(conn, *args)
        conn.execute("COMMIT")
        return result
    except Exception:
        conn.execute("ROLLBACK")
        raise


async def db_transaction(
    function: Callable[..., Any],
    *args: Any,
) -> Any:
    return await db_call(_transaction, function, *args)


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT NOT NULL DEFAULT '',
            balance_kopecks INTEGER NOT NULL DEFAULT 0
                CHECK(balance_kopecks >= 0),
            rating_sum INTEGER NOT NULL DEFAULT 0
                CHECK(rating_sum >= 0),
            rating_count INTEGER NOT NULL DEFAULT 0
                CHECK(rating_count >= 0),
            pos_reviews INTEGER NOT NULL DEFAULT 0
                CHECK(pos_reviews >= 0),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            price_kopecks INTEGER NOT NULL
                CHECK(price_kopecks > 0),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active', 'sold', 'deleted')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (seller_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS deals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL UNIQUE,
            buyer_id INTEGER NOT NULL,
            seller_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            amount_kopecks INTEGER NOT NULL
                CHECK(amount_kopecks > 0),
            fee_kopecks INTEGER NOT NULL
                CHECK(fee_kopecks >= 0),
            seller_receives_kopecks INTEGER NOT NULL
                CHECK(seller_receives_kopecks >= 0),
            status TEXT NOT NULL DEFAULT 'paid'
                CHECK(status IN (
                    'created',
                    'paid',
                    'completed',
                    'dispute',
                    'cancelled',
                    'refunded'
                )),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            paid_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            FOREIGN KEY (product_id) REFERENCES products(id),
            FOREIGN KEY (buyer_id) REFERENCES users(user_id),
            FOREIGN KEY (seller_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS topups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            credit_kopecks INTEGER NOT NULL
                CHECK(credit_kopecks > 0),
            fee_kopecks INTEGER NOT NULL
                CHECK(fee_kopecks >= 0),
            total_kopecks INTEGER NOT NULL
                CHECK(total_kopecks > 0),
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending', 'paid', 'rejected')),
            check_requested_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount_kopecks INTEGER NOT NULL
                CHECK(amount_kopecks > 0),
            requisites TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending', 'completed', 'rejected')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            processed_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_id INTEGER NOT NULL,
            target_user_id INTEGER NOT NULL,
            author_user_id INTEGER NOT NULL,
            rating INTEGER NOT NULL
                CHECK(rating BETWEEN 1 AND 5),
            comment TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(deal_id, author_user_id),
            FOREIGN KEY (deal_id) REFERENCES deals(id),
            FOREIGN KEY (target_user_id) REFERENCES users(user_id),
            FOREIGN KEY (author_user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS platform_vault (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            fee_kopecks INTEGER NOT NULL
                CHECK(fee_kopecks >= 0),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_type, source_id)
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id INTEGER,
            action TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id INTEGER,
            details TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_products_category_status
            ON products(category, status);

        CREATE INDEX IF NOT EXISTS idx_products_seller_status
            ON products(seller_id, status);

        CREATE INDEX IF NOT EXISTS idx_deals_buyer
            ON deals(buyer_id);

        CREATE INDEX IF NOT EXISTS idx_deals_seller
            ON deals(seller_id);

        CREATE INDEX IF NOT EXISTS idx_withdrawals_status
            ON withdrawals(status);

        CREATE INDEX IF NOT EXISTS idx_topups_status
            ON topups(status);
        """
    )


async def init_database() -> None:
    await db_call(_init_schema)
    logger.info("Database initialized: %s", DB_FILE)


def _ensure_user_tx(
    conn: sqlite3.Connection,
    user_id: int,
    username: str,
) -> dict[str, Any]:
    conn.execute(
        """
        INSERT INTO users(user_id, username)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = CASE
                WHEN excluded.username <> '' THEN excluded.username
                ELSE users.username
            END
        """,
        (user_id, username),
    )

    row = conn.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    return dict(row)


async def get_or_create_user(
    user_id: int,
    username: Optional[str] = "",
) -> dict[str, Any]:
    return await db_transaction(
        _ensure_user_tx,
        user_id,
        clean_username(username),
    )


# =========================================================
# ATOMIC FINANCIAL OPERATIONS
# =========================================================

def _create_deal_tx(
    conn: sqlite3.Connection,
    product_id: int,
    buyer_id: int,
) -> Optional[dict[str, Any]]:
    product_row = conn.execute(
        """
        SELECT *
        FROM products
        WHERE id = ? AND status = 'active'
        """,
        (product_id,),
    ).fetchone()

    if not product_row:
        return None

    product = dict(product_row)
    price = int(product["price_kopecks"])
    seller_fee = calculate_fee(price, SELLER_COMMISSION_RATE)
    seller_receives = price - seller_fee

    balance_update = conn.execute(
        """
        UPDATE users
        SET balance_kopecks = balance_kopecks - ?
        WHERE user_id = ?
          AND balance_kopecks >= ?
        """,
        (price, buyer_id, price),
    )

    if balance_update.rowcount != 1:
        return None

    product_update = conn.execute(
        """
        UPDATE products
        SET status = 'sold'
        WHERE id = ? AND status = 'active'
        """,
        (product_id,),
    )

    if product_update.rowcount != 1:
        return None

    cursor = conn.execute(
        """
        INSERT INTO deals(
            product_id,
            buyer_id,
            seller_id,
            title,
            amount_kopecks,
            fee_kopecks,
            seller_receives_kopecks,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'paid')
        """,
        (
            product_id,
            buyer_id,
            product["seller_id"],
            product["title"],
            price,
            seller_fee,
            seller_receives,
        ),
    )

    deal_id = int(cursor.lastrowid)

    conn.execute(
        """
        INSERT INTO audit_log(
            actor_id,
            action,
            entity_type,
            entity_id,
            details
        )
        VALUES (?, 'deal_paid', 'deal', ?, ?)
        """,
        (buyer_id, deal_id, f"product_id={product_id}"),
    )

    return {
        "deal_id": deal_id,
        "product_id": product_id,
        "buyer_id": buyer_id,
        "seller_id": product["seller_id"],
        "title": product["title"],
        "amount_kopecks": price,
        "fee_kopecks": seller_fee,
        "seller_receives_kopecks": seller_receives,
    }


def _complete_deal_tx(
    conn: sqlite3.Connection,
    deal_id: int,
    buyer_id: int,
) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM deals WHERE id = ?",
        (deal_id,),
    ).fetchone()

    if not row:
        return None

    deal = dict(row)

    if deal["buyer_id"] != buyer_id:
        return None

    if deal["status"] != "paid":
        return None

    updated = conn.execute(
        """
        UPDATE deals
        SET status = 'completed',
            completed_at = CURRENT_TIMESTAMP
        WHERE id = ? AND buyer_id = ? AND status = 'paid'
        """,
        (deal_id, buyer_id),
    )

    if updated.rowcount != 1:
        return None

    conn.execute(
        """
        UPDATE users
        SET balance_kopecks = balance_kopecks + ?
        WHERE user_id = ?
        """,
        (
            deal["seller_receives_kopecks"],
            deal["seller_id"],
        ),
    )

    conn.execute(
        """
        INSERT INTO platform_vault(
            source_type,
            source_id,
            fee_kopecks
        )
        VALUES ('seller_deal_fee', ?, ?)
        """,
        (deal_id, deal["fee_kopecks"]),
    )

    conn.execute(
        """
        INSERT INTO audit_log(
            actor_id,
            action,
            entity_type,
            entity_id,
            details
        )
        VALUES (?, 'deal_completed', 'deal', ?, NULL)
        """,
        (buyer_id, deal_id),
    )

    return deal


def _open_dispute_tx(
    conn: sqlite3.Connection,
    deal_id: int,
    actor_id: int,
) -> tuple[Optional[dict[str, Any]], bool]:
    row = conn.execute(
        "SELECT * FROM deals WHERE id = ?",
        (deal_id,),
    ).fetchone()

    if not row:
        return None, False

    deal = dict(row)

    if actor_id not in (deal["buyer_id"], deal["seller_id"]):
        return None, False

    if deal["status"] == "dispute":
        return deal, False

    if deal["status"] != "paid":
        return deal, False

    updated = conn.execute(
        """
        UPDATE deals
        SET status = 'dispute'
        WHERE id = ? AND status = 'paid'
        """,
        (deal_id,),
    )

    if updated.rowcount != 1:
        return deal, False

    conn.execute(
        """
        INSERT INTO audit_log(
            actor_id,
            action,
            entity_type,
            entity_id,
            details
        )
        VALUES (?, 'dispute_opened', 'deal', ?, NULL)
        """,
        (actor_id, deal_id),
    )

    return deal, True


def _approve_topup_tx(
    conn: sqlite3.Connection,
    topup_id: int,
) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM topups WHERE id = ?",
        (topup_id,),
    ).fetchone()

    if not row or row["status"] != "pending":
        return None

    topup = dict(row)

    updated = conn.execute(
        """
        UPDATE topups
        SET status = 'paid'
        WHERE id = ? AND status = 'pending'
        """,
        (topup_id,),
    )

    if updated.rowcount != 1:
        return None

    conn.execute(
        """
        UPDATE users
        SET balance_kopecks = balance_kopecks + ?
        WHERE user_id = ?
        """,
        (
            topup["credit_kopecks"],
            topup["user_id"],
        ),
    )

    conn.execute(
        """
        INSERT INTO platform_vault(
            source_type,
            source_id,
            fee_kopecks
        )
        VALUES ('buyer_topup_fee', ?, ?)
        """,
        (
            topup_id,
            topup["fee_kopecks"],
        ),
    )

    return topup


def _create_withdrawal_tx(
    conn: sqlite3.Connection,
    user_id: int,
    amount_kopecks: int,
    requisites: str,
) -> Optional[int]:
    updated = conn.execute(
        """
        UPDATE users
        SET balance_kopecks = balance_kopecks - ?
        WHERE user_id = ?
          AND balance_kopecks >= ?
        """,
        (amount_kopecks, user_id, amount_kopecks),
    )

    if updated.rowcount != 1:
        return None

    cursor = conn.execute(
        """
        INSERT INTO withdrawals(
            user_id,
            amount_kopecks,
            requisites,
            status
        )
        VALUES (?, ?, ?, 'pending')
        """,
        (user_id, amount_kopecks, requisites),
    )

    withdrawal_id = int(cursor.lastrowid)

    conn.execute(
        """
        INSERT INTO audit_log(
            actor_id,
            action,
            entity_type,
            entity_id,
            details
        )
        VALUES (?, 'withdrawal_created', 'withdrawal', ?, NULL)
        """,
        (user_id, withdrawal_id),
    )

    return withdrawal_id


def _approve_withdrawal_tx(
    conn: sqlite3.Connection,
    withdrawal_id: int,
) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM withdrawals WHERE id = ?",
        (withdrawal_id,),
    ).fetchone()

    if not row or row["status"] != "pending":
        return None

    updated = conn.execute(
        """
        UPDATE withdrawals
        SET status = 'completed',
            processed_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status = 'pending'
        """,
        (withdrawal_id,),
    )

    if updated.rowcount != 1:
        return None

    return dict(row)


def _reject_withdrawal_tx(
    conn: sqlite3.Connection,
    withdrawal_id: int,
) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM withdrawals WHERE id = ?",
        (withdrawal_id,),
    ).fetchone()

    if not row or row["status"] != "pending":
        return None

    updated = conn.execute(
        """
        UPDATE withdrawals
        SET status = 'rejected',
            processed_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status = 'pending'
        """,
        (withdrawal_id,),
    )

    if updated.rowcount != 1:
        return None

    conn.execute(
        """
        UPDATE users
        SET balance_kopecks = balance_kopecks + ?
        WHERE user_id = ?
        """,
        (
            row["amount_kopecks"],
            row["user_id"],
        ),
    )

    return dict(row)


def _complete_review_tx(
    conn: sqlite3.Connection,
    deal_id: int,
    author_id: int,
    rating: int,
    comment: str,
) -> bool:
    deal_row = conn.execute(
        "SELECT * FROM deals WHERE id = ?",
        (deal_id,),
    ).fetchone()

    if not deal_row:
        return False

    deal = dict(deal_row)

    if deal["buyer_id"] != author_id:
        return False

    if deal["status"] != "completed":
        return False

    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO reviews(
            deal_id,
            target_user_id,
            author_user_id,
            rating,
            comment
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            deal_id,
            deal["seller_id"],
            author_id,
            rating,
            comment,
        ),
    )

    if cursor.rowcount != 1:
        return False

    conn.execute(
        """
        UPDATE users
        SET rating_sum = rating_sum + ?,
            rating_count = rating_count + 1,
            pos_reviews = pos_reviews + ?
        WHERE user_id = ?
        """,
        (
            rating,
            1 if rating >= 4 else 0,
            deal["seller_id"],
        ),
    )

    return True


def _delete_product_tx(
    conn: sqlite3.Connection,
    product_id: int,
    seller_id: int,
) -> bool:
    cursor = conn.execute(
        """
        UPDATE products
        SET status = 'deleted'
        WHERE id = ?
          AND seller_id = ?
          AND status IN ('active', 'sold')
        """,
        (product_id, seller_id),
    )

    return cursor.rowcount == 1


# =========================================================
# FSM
# =========================================================

class AddProductFSM(StatesGroup):
    category = State()
    title = State()
    description = State()
    price = State()


class TopUpFSM(StatesGroup):
    credit_amount = State()


class WithdrawFSM(StatesGroup):
    amount = State()
    requisites = State()


class AskSellerFSM(StatesGroup):
    target_seller_id = State()
    product_id = State()
    question_text = State()


class AnswerBuyerFSM(StatesGroup):
    target_buyer_id = State()
    product_id = State()
    answer_text = State()


class ReviewFSM(StatesGroup):
    deal_id = State()
    rating = State()
    comment = State()


# =========================================================
# BOT AND UI
# =========================================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

BOT_USERNAME = ""


def get_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🛍 Каталог товаров"),
                KeyboardButton(text="📦 Мои товары"),
            ],
            [
                KeyboardButton(text="👛 Кошелёк"),
                KeyboardButton(text="📂 Мои сделки"),
            ],
            [
                KeyboardButton(text="👤 Профиль"),
                KeyboardButton(text="💳 Вывод средств"),
            ],
            [
                KeyboardButton(text="🌐 Подключить OnyxVPN"),
                KeyboardButton(text="ℹ️ Помощь / Поддержка"),
            ],
        ],
        resize_keyboard=True,
    )


def get_categories_keyboard(action: str = "view") -> InlineKeyboardMarkup:
    prefix = "cat_v_" if action == "view" else "cat_a_"

    rows: list[list[InlineKeyboardButton]] = []
    current_row: list[InlineKeyboardButton] = []

    for code, label in CATEGORIES:
        current_row.append(
            InlineKeyboardButton(
                text=label,
                callback_data=f"{prefix}{code}",
            )
        )

        if len(current_row) == 2:
            rows.append(current_row)
            current_row = []

    if current_row:
        rows.append(current_row)

    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 Закрыть меню",
                callback_data="close_menu",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


async def answer_html(
    message: Message,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> Any:
    return await message.answer(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup,
    )


async def send_html(
    chat_id: int,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> Any:
    return await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup,
    )


async def safe_edit_message(
    message: Message,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> None:
    try:
        await message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return

        try:
            await answer_html(message, text, reply_markup)
        except Exception:
            logger.exception("Failed to edit and resend message")
    except Exception:
        logger.exception("Safe message edit failed")

        try:
            await answer_html(message, text, reply_markup)
        except Exception:
            logger.exception("Failed to resend message")


async def answer_callback(
    callback: CallbackQuery,
    text: Optional[str] = None,
    show_alert: bool = False,
) -> None:
    try:
        await callback.answer(
            text=text,
            show_alert=show_alert,
        )
    except Exception:
        pass


def parse_callback_id(data: str, prefix: str) -> Optional[int]:
    if not data.startswith(prefix):
        return None

    try:
        return int(data[len(prefix):])
    except ValueError:
        return None


def shorten(value: str, length: int = 45) -> str:
    value = str(value)
    if len(value) <= length:
        return value
    return value[: length - 1] + "…"


def support_keyboard() -> list[list[InlineKeyboardButton]]:
    if not SUPPORT_URL:
        return []

    return [
        [
            InlineKeyboardButton(
                text=f"👨‍💻 Поддержка {SUPPORT_HANDLE}",
                url=SUPPORT_URL,
            )
        ]
    ]


# =========================================================
# MIDDLEWARE
# =========================================================

class MenuStateCleanerMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.text in MAIN_MENU_TEXTS:
            state: Optional[FSMContext] = data.get("state")
            if state:
                await state.clear()

        return await handler(event, data)


dp.message.middleware(MenuStateCleanerMiddleware())


# =========================================================
# START
# =========================================================

async def get_product_link(product_id: int) -> str:
    global BOT_USERNAME

    if not BOT_USERNAME:
        me = await bot.get_me()
        BOT_USERNAME = me.username or ""

    return f"https://t.me/{BOT_USERNAME}?start=item_{product_id}"


@dp.message(CommandStart())
async def command_start(
    message: Message,
    state: FSMContext,
) -> None:
    await state.clear()

    await get_or_create_user(
        message.from_user.id,
        message.from_user.username,
    )

    args = (message.text or "").split(maxsplit=1)

    if len(args) > 1 and args[1].startswith("item_"):
        try:
            product_id = int(args[1][5:])
            await render_product_card(message, product_id)
            return
        except ValueError:
            pass

    text = (
        "🛡 <b>Добро пожаловать в BastionPRO!</b>\n\n"
        "Платформа безопасной торговли цифровыми товарами.\n\n"
        "🛍 <b>Каталог</b> — просмотр предложений\n"
        "📦 <b>Мои товары</b> — размещение товаров\n"
        "👛 <b>Кошелёк</b> — внутренний баланс\n"
        "📂 <b>Мои сделки</b> — история покупок и продаж\n"
        "💳 <b>Вывод средств</b> — заявки продавцов\n\n"
        f"Поддержка: {escape(SUPPORT_HANDLE)}"
    )

    await answer_html(
        message,
        text,
        get_main_keyboard(),
    )


# =========================================================
# MY PRODUCTS
# =========================================================

async def render_my_products_list(
    target: Message | CallbackQuery,
    user_id: int,
) -> None:
    products = await db_fetchall(
        """
        SELECT *
        FROM products
        WHERE seller_id = ?
          AND status <> 'deleted'
        ORDER BY id DESC
        LIMIT 25
        """,
        (user_id,),
    )

    if not products:
        text = (
            "📦 <b>Ваши товары</b>\n\n"
            "У вас пока нет товаров."
        )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="➕ Добавить товар",
                        callback_data="add_product_start",
                    )
                ]
            ]
        )
    else:
        lines = [f"📦 <b>Ваши товары ({len(products)})</b>\n"]
        buttons: list[list[InlineKeyboardButton]] = []
        has_sold = False

        for product in products:
            category = CATEGORIES_DICT.get(
                product["category"],
                product["category"],
            )

            lines.append(
                f"{'🟢' if product['status'] == 'active' else '🔴'} "
                f"<b>#{product['id']} {escape(product['title'])}</b>\n"
                f"Платформа: {escape(category)}\n"
                f"Цена: <b>{format_money(product['price_kopecks'])} ₽</b>\n"
            )

            if product["status"] == "active":
                buttons.append(
                    [
                        InlineKeyboardButton(
                            text=f"⚙️ Управление #{product['id']}",
                            callback_data=f"manage_p_{product['id']}",
                        )
                    ]
                )
            else:
                has_sold = True
                buttons.append(
                    [
                        InlineKeyboardButton(
                            text=f"🗑 Удалить проданный #{product['id']}",
                            callback_data=f"del_prod_{product['id']}",
                        )
                    ]
                )

        if has_sold:
            buttons.append(
                [
                    InlineKeyboardButton(
                        text="🧹 Очистить проданные",
                        callback_data="clean_all_sold",
                    )
                ]
            )

        buttons.append(
            [
                InlineKeyboardButton(
                    text="➕ Добавить товар",
                    callback_data="add_product_start",
                )
            ]
        )

        text = "\n".join(lines)
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    if isinstance(target, CallbackQuery):
        if target.message:
            await safe_edit_message(
                target.message,
                text,
                keyboard,
            )
    else:
        await answer_html(target, text, keyboard)


@dp.message(F.text == "📦 Мои товары")
async def navigation_my_products(
    message: Message,
    state: FSMContext,
) -> None:
    await state.clear()
    await get_or_create_user(
        message.from_user.id,
        message.from_user.username,
    )
    await render_my_products_list(message, message.from_user.id)


@dp.callback_query(F.data.startswith("manage_p_"))
async def manage_product(callback: CallbackQuery) -> None:
    await answer_callback(callback)

    product_id = parse_callback_id(callback.data, "manage_p_")
    if not product_id or not callback.message:
        return

    product = await db_fetchone(
        "SELECT * FROM products WHERE id = ?",
        (product_id,),
    )

    if (
        not product
        or product["seller_id"] != callback.from_user.id
        or product["status"] == "deleted"
    ):
        await answer_html(callback.message, "⚠️ Товар не найден.")
        return

    status = (
        "🟢 На продаже"
        if product["status"] == "active"
        else "🔴 Продан"
    )
    link = await get_product_link(product_id)

    text = (
        f"⚙️ <b>Управление товаром #{product_id}</b>\n\n"
        f"Название: <b>{escape(product['title'])}</b>\n"
        f"Категория: {escape(CATEGORIES_DICT.get(product['category'], product['category']))}\n"
        f"Цена: <b>{format_money(product['price_kopecks'])} ₽</b>\n"
        f"Статус: {status}\n\n"
        f"Ссылка:\n<code>{escape(link)}</code>"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Удалить товар",
                    callback_data=f"del_prod_{product_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Назад",
                    callback_data="back_to_my_prods",
                )
            ],
        ]
    )

    await safe_edit_message(callback.message, text, keyboard)


@dp.callback_query(F.data.startswith("del_prod_"))
async def delete_product(callback: CallbackQuery) -> None:
    await answer_callback(callback)

    product_id = parse_callback_id(callback.data, "del_prod_")
    if not product_id:
        return

    deleted = await db_transaction(
        _delete_product_tx,
        product_id,
        callback.from_user.id,
    )

    if not deleted:
        await callback.message.answer("⚠️ Товар не найден или недоступен.")
        return

    await answer_callback(callback, "Товар удалён")

    if callback.message:
        await render_my_products_list(
            callback,
            callback.from_user.id,
        )


@dp.callback_query(F.data == "clean_all_sold")
async def clean_sold_products(callback: CallbackQuery) -> None:
    await answer_callback(callback)

    await db_execute(
        """
        UPDATE products
        SET status = 'deleted'
        WHERE seller_id = ?
          AND status = 'sold'
        """,
        (callback.from_user.id,),
    )

    await render_my_products_list(
        callback,
        callback.from_user.id,
    )


@dp.callback_query(F.data == "back_to_my_prods")
async def back_to_my_products(callback: CallbackQuery) -> None:
    await answer_callback(callback)

    await render_my_products_list(
        callback,
        callback.from_user.id,
    )


# =========================================================
# WALLET AND PROFILE
# =========================================================

async def show_wallet(message: Message) -> None:
    user = await get_or_create_user(
        message.from_user.id,
        message.from_user.username,
    )

    keyboard_rows = [
        [
            InlineKeyboardButton(
                text="➕ Пополнить баланс",
                callback_data="wallet_topup",
            )
        ],
        [
            InlineKeyboardButton(
                text="💳 Вывести средства",
                callback_data="wallet_withdraw",
            )
        ],
    ]
    keyboard_rows.extend(support_keyboard())

    text = (
        "👛 <b>Кошелёк</b>\n\n"
        f"ID: <code>{user['user_id']}</code>\n"
        f"Баланс: <b>{format_money(user['balance_kopecks'])} ₽</b>\n\n"
        "<b>Комиссии:</b>\n"
        f"• Пополнение: {rate_percent(BUYER_DEPOSIT_RATE)}\n"
        f"• Сделка продавца: {rate_percent(SELLER_COMMISSION_RATE)}\n"
        f"• Минимальный вывод: {format_money(MIN_WITHDRAW_KOPECKS)} ₽"
    )

    await answer_html(
        message,
        text,
        InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
    )


@dp.message(F.text == "👛 Кошелёк")
async def navigation_wallet(
    message: Message,
    state: FSMContext,
) -> None:
    await state.clear()
    await show_wallet(message)


@dp.callback_query(F.data == "open_wallet")
async def open_wallet_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await answer_callback(callback)
    await state.clear()

    if callback.message:
        await show_wallet(callback.message)


@dp.message(F.text == "👤 Профиль")
async def navigation_profile(
    message: Message,
    state: FSMContext,
) -> None:
    await state.clear()

    user = await get_or_create_user(
        message.from_user.id,
        message.from_user.username,
    )

    completed = await db_fetchone(
        """
        SELECT COUNT(*) AS total
        FROM deals
        WHERE seller_id = ?
          AND status = 'completed'
        """,
        (user["user_id"],),
    )

    if user["rating_count"]:
        average = user["rating_sum"] / user["rating_count"]
        positive = user["pos_reviews"] * 100 // user["rating_count"]
        reputation = (
            f"⭐ {average:.1f}/5.0, "
            f"{positive}% положительных, "
            f"{user['rating_count']} отзывов"
        )
    else:
        reputation = "⭐ Новый участник, отзывов пока нет"

    username = (
        f"@{escape(user['username'])}"
        if user["username"]
        else "не указан"
    )

    rows = [
        [
            InlineKeyboardButton(
                text="👛 Кошелёк",
                callback_data="open_wallet",
            )
        ],
        [
            InlineKeyboardButton(
                text="💳 Вывод средств",
                callback_data="wallet_withdraw",
            )
        ],
    ]
    rows.extend(support_keyboard())

    text = (
        "👤 <b>Профиль</b>\n\n"
        f"Telegram ID: <code>{user['user_id']}</code>\n"
        f"Никнейм: {username}\n"
        f"Баланс: <b>{format_money(user['balance_kopecks'])} ₽</b>\n"
        f"Рейтинг: {escape(reputation)}\n"
        f"Завершённых продаж: <b>{completed['total'] if completed else 0}</b>"
    )

    await answer_html(
        message,
        text,
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


# =========================================================
# WITHDRAWALS
# =========================================================

@dp.message(F.text == "💳 Вывод средств")
async def navigation_withdraw(
    message: Message,
    state: FSMContext,
) -> None:
    await state.clear()

    user = await get_or_create_user(
        message.from_user.id,
        message.from_user.username,
    )

    rows: list[list[InlineKeyboardButton]] = []

    if user["balance_kopecks"] >= MIN_WITHDRAW_KOPECKS:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📤 Создать заявку",
                    callback_data="start_withdraw",
                )
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    text="➕ Пополнить баланс",
                    callback_data="wallet_topup",
                )
            ]
        )

    rows.extend(support_keyboard())

    text = (
        "💳 <b>Вывод средств</b>\n\n"
        f"Доступно: <b>{format_money(user['balance_kopecks'])} ₽</b>\n"
        f"Минимум: <b>{format_money(MIN_WITHDRAW_KOPECKS)} ₽</b>\n\n"
        "Перед созданием заявки сумма временно резервируется. "
        "При отклонении заявки средства возвращаются автоматически."
    )

    await answer_html(
        message,
        text,
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@dp.callback_query(F.data == "wallet_withdraw")
@dp.callback_query(F.data == "start_withdraw")
async def start_withdraw(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await answer_callback(callback)
    await state.clear()

    user = await get_or_create_user(
        callback.from_user.id,
        callback.from_user.username,
    )

    if user["balance_kopecks"] < MIN_WITHDRAW_KOPECKS:
        await callback.message.answer(
            f"⚠️ Минимальная сумма вывода — "
            f"{format_money(MIN_WITHDRAW_KOPECKS)} ₽."
        )
        return

    await callback.message.answer(
        "💳 Введите сумму вывода в рублях:"
    )
    await state.set_state(WithdrawFSM.amount)


@dp.message(WithdrawFSM.amount)
async def process_withdraw_amount(
    message: Message,
    state: FSMContext,
) -> None:
    try:
        amount = parse_money(message.text or "")

        if amount < MIN_WITHDRAW_KOPECKS:
            raise ValueError(
                f"Минимум — {format_money(MIN_WITHDRAW_KOPECKS)} ₽"
            )
    except ValueError as exc:
        await message.answer(f"⚠️ {exc}")
        return

    user = await get_or_create_user(
        message.from_user.id,
        message.from_user.username,
    )

    if amount > user["balance_kopecks"]:
        await message.answer(
            "⚠️ Недостаточно средств на балансе."
        )
        return

    await state.update_data(withdraw_amount_kopecks=amount)

    await message.answer(
        "💳 Введите реквизиты для выплаты:\n"
        "карта, СБП или криптокошелёк."
    )
    await state.set_state(WithdrawFSM.requisites)


@dp.message(WithdrawFSM.requisites)
async def process_withdraw_requisites(
    message: Message,
    state: FSMContext,
) -> None:
    requisites = (message.text or "").strip()

    if len(requisites) < 5 or len(requisites) > 500:
        await message.answer(
            "⚠️ Реквизиты должны содержать от 5 до 500 символов."
        )
        return

    data = await state.get_data()
    amount = int(data.get("withdraw_amount_kopecks", 0))

    if amount <= 0:
        await state.clear()
        await message.answer("⚠️ Сессия устарела. Создайте заявку заново.")
        return

    withdrawal_id = await db_transaction(
        _create_withdrawal_tx,
        message.from_user.id,
        amount,
        requisites,
    )

    if not withdrawal_id:
        await state.clear()
        await message.answer(
            "⚠️ Баланс изменился. Недостаточно средств."
        )
        return

    await state.clear()

    await message.answer(
        f"✅ Заявка <b>#{withdrawal_id}</b> создана.\n\n"
        f"Сумма: <b>{format_money(amount)} ₽</b>\n"
        f"Реквизиты: <code>{escape(mask_requisites(requisites))}</code>\n\n"
        "Заявка ожидает проверки администратором.",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard(),
    )

    user = await get_or_create_user(
        message.from_user.id,
        message.from_user.username,
    )

    admin_text = (
        f"🚨 <b>Новая заявка на вывод #{withdrawal_id}</b>\n\n"
        f"Пользователь: @{escape(user['username']) or 'без username'}\n"
        f"ID: <code>{user['user_id']}</code>\n"
        f"Сумма: <b>{format_money(amount)} ₽</b>\n"
        f"Реквизиты:\n<code>{escape(requisites)}</code>"
    )

    admin_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Выплачено",
                    callback_data=f"adm_w_ok_{withdrawal_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"adm_w_fail_{withdrawal_id}",
                ),
            ]
        ]
    )

    for admin_id in ADMIN_IDS:
        try:
            await send_html(
                admin_id,
                admin_text,
                admin_keyboard,
            )
        except Exception:
            logger.exception(
                "Failed to notify admin about withdrawal"
            )


@dp.callback_query(F.data.startswith("adm_w_ok_"))
async def approve_withdrawal(callback: CallbackQuery) -> None:
    await answer_callback(callback)

    if not is_admin(callback.from_user.id):
        await answer_callback(callback, "Нет доступа", True)
        return

    withdrawal_id = parse_callback_id(
        callback.data,
        "adm_w_ok_",
    )
    if not withdrawal_id:
        return

    withdrawal = await db_transaction(
        _approve_withdrawal_tx,
        withdrawal_id,
    )

    if not withdrawal:
        await callback.message.answer(
            "Заявка уже обработана или не найдена."
        )
        return

    await safe_edit_message(
        callback.message,
        f"✅ Заявка #{withdrawal_id} отмечена как выплаченная.\n"
        f"Сумма: <b>{format_money(withdrawal['amount_kopecks'])} ₽</b>",
    )

    try:
        await send_html(
            withdrawal["user_id"],
            f"✅ Заявка на вывод #{withdrawal_id} исполнена.\n"
            f"Сумма: <b>{format_money(withdrawal['amount_kopecks'])} ₽</b>",
        )
    except Exception:
        logger.exception("Failed to notify user about completed withdrawal")


@dp.callback_query(F.data.startswith("adm_w_fail_"))
async def reject_withdrawal(callback: CallbackQuery) -> None:
    await answer_callback(callback)

    if not is_admin(callback.from_user.id):
        await answer_callback(callback, "Нет доступа", True)
        return

    withdrawal_id = parse_callback_id(
        callback.data,
        "adm_w_fail_",
    )
    if not withdrawal_id:
        return

    withdrawal = await db_transaction(
        _reject_withdrawal_tx,
        withdrawal_id,
    )

    if not withdrawal:
        await callback.message.answer(
            "Заявка уже обработана или не найдена."
        )
        return

    amount = withdrawal["amount_kopecks"]

    await safe_edit_message(
        callback.message,
        f"❌ Заявка #{withdrawal_id} отклонена.\n"
        f"Возвращено: <b>{format_money(amount)} ₽</b>",
    )

    try:
        await send_html(
            withdrawal["user_id"],
            f"❌ Заявка на вывод #{withdrawal_id} отклонена.\n"
            f"Средства возвращены: <b>{format_money(amount)} ₽</b>",
        )
    except Exception:
        logger.exception("Failed to notify user about rejected withdrawal")


# =========================================================
# TOPUPS
# =========================================================

@dp.callback_query(F.data == "wallet_topup")
async def start_topup(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await answer_callback(callback)
    await state.clear()

    await callback.message.answer(
        "👛 Введите сумму, которую нужно зачислить на баланс:"
    )
    await state.set_state(TopUpFSM.credit_amount)


@dp.message(TopUpFSM.credit_amount)
async def process_topup_amount(
    message: Message,
    state: FSMContext,
) -> None:
    try:
        credit = parse_money(message.text or "")

        if credit < MIN_TOPUP_KOPECKS:
            raise ValueError(
                f"Минимум — {format_money(MIN_TOPUP_KOPECKS)} ₽"
            )
    except ValueError as exc:
        await message.answer(f"⚠️ {exc}")
        return

    fee = calculate_fee(credit, BUYER_DEPOSIT_RATE)
    total = credit + fee

    topup_result = await db_execute(
        """
        INSERT INTO topups(
            user_id,
            credit_kopecks,
            fee_kopecks,
            total_kopecks,
            status
        )
        VALUES (?, ?, ?, ?, 'pending')
        """,
        (
            message.from_user.id,
            credit,
            fee,
            total,
        ),
    )

    topup_id = topup_result["lastrowid"]
    payment_url = build_payment_url(topup_id, total)

    rows: list[list[InlineKeyboardButton]] = []

    if payment_url:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"💳 Оплатить {format_money(total)} ₽",
                    url=payment_url,
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="🔄 Я оплатил",
                callback_data=f"check_topup_{topup_id}",
            )
        ]
    )
    rows.extend(support_keyboard())

    if payment_url:
        payment_text = "Нажмите кнопку оплаты ниже."
    else:
        payment_text = (
            "Платёжная ссылка пока не настроена. "
            "Свяжитесь с администратором и укажите номер счёта."
        )

    await state.clear()

    text = (
        f"🧾 <b>Счёт #{topup_id}</b>\n\n"
        f"Зачисление: <b>{format_money(credit)} ₽</b>\n"
        f"Комиссия: <b>{format_money(fee)} ₽</b>\n"
        f"К оплате: <b>{format_money(total)} ₽</b>\n\n"
        f"{payment_text}"
    )

    await answer_html(
        message,
        text,
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@dp.callback_query(F.data.startswith("check_topup_"))
async def check_topup(callback: CallbackQuery) -> None:
    await answer_callback(
        callback,
        "Заявка отправлена администратору",
        show_alert=True,
    )

    topup_id = parse_callback_id(
        callback.data,
        "check_topup_",
    )
    if not topup_id:
        return

    topup = await db_fetchone(
        "SELECT * FROM topups WHERE id = ?",
        (topup_id,),
    )

    if not topup:
        return

    if topup["user_id"] != callback.from_user.id:
        await answer_callback(callback, "Нет доступа", True)
        return

    if topup["status"] != "pending":
        return

    await db_execute(
        """
        UPDATE topups
        SET check_requested_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status = 'pending'
        """,
        (topup_id,),
    )

    user = await get_or_create_user(
        callback.from_user.id,
        callback.from_user.username,
    )

    admin_text = (
        f"🔔 <b>Проверка пополнения #{topup_id}</b>\n\n"
        f"Пользователь: @{escape(user['username']) or 'без username'}\n"
        f"ID: <code>{user['user_id']}</code>\n"
        f"Зачислить: <b>{format_money(topup['credit_kopecks'])} ₽</b>\n"
        f"Комиссия: <b>{format_money(topup['fee_kopecks'])} ₽</b>\n"
        f"К оплате: <b>{format_money(topup['total_kopecks'])} ₽</b>"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Зачислить",
                    callback_data=f"adm_appr_topup_{topup_id}",
                )
            ]
        ]
    )

    for admin_id in ADMIN_IDS:
        try:
            await send_html(admin_id, admin_text, keyboard)
        except Exception:
            logger.exception("Failed to notify admin about topup")


@dp.callback_query(F.data.startswith("adm_appr_topup_"))
async def approve_topup(callback: CallbackQuery) -> None:
    await answer_callback(callback)

    if not is_admin(callback.from_user.id):
        await answer_callback(callback, "Нет доступа", True)
        return

    topup_id = parse_callback_id(
        callback.data,
        "adm_appr_topup_",
    )
    if not topup_id:
        return

    topup = await db_transaction(
        _approve_topup_tx,
        topup_id,
    )

    if not topup:
        await callback.message.answer(
            "Пополнение уже обработано или не найдено."
        )
        return

    await safe_edit_message(
        callback.message,
        f"✅ Пополнение #{topup_id} зачислено.\n"
        f"Баланс увеличен на "
        f"<b>{format_money(topup['credit_kopecks'])} ₽</b>.",
    )

    try:
        await send_html(
            topup["user_id"],
            "🎉 Баланс пополнен.\n"
            f"Зачислено: <b>{format_money(topup['credit_kopecks'])} ₽</b>",
            get_main_keyboard(),
        )
    except Exception:
        logger.exception("Failed to notify user about topup")


# =========================================================
# CATALOG
# =========================================================

@dp.message(F.text == "🛍 Каталог товаров")
async def navigation_catalog(
    message: Message,
    state: FSMContext,
) -> None:
    await state.clear()

    await answer_html(
        message,
        "🛍 <b>Каталог товаров</b>\n\nВыберите категорию:",
        get_categories_keyboard("view"),
    )


@dp.callback_query(F.data.startswith("cat_v_"))
async def view_category(callback: CallbackQuery) -> None:
    await answer_callback(callback)

    if not callback.message:
        return

    category_code = callback.data.replace("cat_v_", "")
    category_title = CATEGORIES_DICT.get(category_code)

    if not category_title:
        await answer_html(callback.message, "⚠️ Категория не найдена.")
        return

    products = await db_fetchall(
        """
        SELECT *
        FROM products
        WHERE category = ?
          AND status = 'active'
        ORDER BY id DESC
        LIMIT 25
        """,
        (category_code,),
    )

    if not products:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="➕ Добавить свой товар",
                        callback_data=f"cat_a_{category_code}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔙 Категории",
                        callback_data="back_to_cats",
                    )
                ],
            ]
        )

        await safe_edit_message(
            callback.message,
            f"{escape(category_title)}\n\n"
            "В этой категории пока нет активных товаров.",
            keyboard,
        )
        return

    rows: list[list[InlineKeyboardButton]] = []

    for product in products:
        rows.append(
            [
                InlineKeyboardButton(
                    text=shorten(
                        f"{product['title']} — "
                        f"{format_money(product['price_kopecks'])} ₽",
                        60,
                    ),
                    callback_data=f"view_prod_{product['id']}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="➕ Добавить товар",
                callback_data=f"cat_a_{category_code}",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 Категории",
                callback_data="back_to_cats",
            )
        ]
    )

    await safe_edit_message(
        callback.message,
        f"{escape(category_title)}\n\n"
        f"Активных предложений: <b>{len(products)}</b>",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@dp.callback_query(F.data == "back_to_cats")
async def back_to_categories(callback: CallbackQuery) -> None:
    await answer_callback(callback)

    await safe_edit_message(
        callback.message,
        "🛍 <b>Каталог товаров</b>\n\nВыберите категорию:",
        get_categories_keyboard("view"),
    )


async def render_product_card(
    target: Message | CallbackQuery,
    product_id: int,
) -> None:
    product = await db_fetchone(
        """
        SELECT *
        FROM products
        WHERE id = ?
          AND status <> 'deleted'
        """,
        (product_id,),
    )

    if not product:
        if isinstance(target, CallbackQuery) and target.message:
            await answer_html(
                target.message,
                "⚠️ Товар не найден или удалён.",
            )
        elif isinstance(target, Message):
            await answer_html(
                target,
                "⚠️ Товар не найден или удалён.",
            )
        return

    seller = await get_or_create_user(product["seller_id"])

    if seller["rating_count"]:
        average = seller["rating_sum"] / seller["rating_count"]
        reputation = (
            f"⭐ {average:.1f}/5.0, "
            f"{seller['rating_count']} отзывов"
        )
    else:
        reputation = "⭐ Новый продавец"

    link = await get_product_link(product_id)
    current_user_id = target.from_user.id

    text = (
        f"📦 <b>{escape(product['title'])}</b>\n"
        f"Категория: "
        f"{escape(CATEGORIES_DICT.get(product['category'], product['category']))}\n"
        f"Цена: <b>{format_money(product['price_kopecks'])} ₽</b>\n\n"
        f"<b>Описание:</b>\n{escape(product['description'])}\n\n"
        f"<b>Продавец:</b> "
        f"@{escape(seller['username']) if seller['username'] else seller['user_id']}\n"
        f"<b>Репутация:</b> {escape(reputation)}\n\n"
        f"Ссылка:\n<code>{escape(link)}</code>"
    )

    rows: list[list[InlineKeyboardButton]] = []

    if current_user_id == product["seller_id"]:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🗑 Удалить товар",
                    callback_data=f"del_prod_{product_id}",
                )
            ]
        )
    elif product["status"] == "active":
        rows.append(
            [
                InlineKeyboardButton(
                    text="💳 Купить через гарант",
                    callback_data=f"buy_item_{product_id}",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="💬 Задать вопрос",
                    callback_data=f"ask_seller_{product_id}",
                )
            ]
        )
    else:
        text += "\n\n⚠️ <b>Товар уже продан.</b>"

    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 Назад в каталог",
                callback_data="back_to_cats",
            )
        ]
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)

    if isinstance(target, CallbackQuery):
        if target.message:
            await safe_edit_message(
                target.message,
                text,
                keyboard,
            )
    else:
        await answer_html(target, text, keyboard)


@dp.callback_query(F.data.startswith("view_prod_"))
async def view_product(callback: CallbackQuery) -> None:
    await answer_callback(callback)

    product_id = parse_callback_id(
        callback.data,
        "view_prod_",
    )
    if not product_id:
        return

    await render_product_card(callback, product_id)


# =========================================================
# ADD PRODUCT
# =========================================================

@dp.callback_query(F.data == "add_product_start")
async def add_product_start(callback: CallbackQuery) -> None:
    await answer_callback(callback)

    await callback.message.answer(
        "➕ Выберите категорию товара:",
        reply_markup=get_categories_keyboard("add"),
    )


@dp.callback_query(F.data.startswith("cat_a_"))
async def select_product_category(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await answer_callback(callback)

    category_code = callback.data.replace("cat_a_", "")
    category_title = CATEGORIES_DICT.get(category_code)

    if not category_title:
        await callback.message.answer("⚠️ Категория не найдена.")
        return

    await state.clear()
    await state.update_data(category=category_code)

    await callback.message.answer(
        f"Выбрано: <b>{escape(category_title)}</b>\n\n"
        "Введите название товара от 3 до 100 символов.",
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(AddProductFSM.title)


@dp.message(AddProductFSM.title)
async def process_product_title(
    message: Message,
    state: FSMContext,
) -> None:
    title = (message.text or "").strip()

    if not 3 <= len(title) <= 100:
        await message.answer(
            "⚠️ Название должно содержать от 3 до 100 символов."
        )
        return

    await state.update_data(title=title)

    await message.answer(
        "Введите подробное описание товара."
    )
    await state.set_state(AddProductFSM.description)


@dp.message(AddProductFSM.description)
async def process_product_description(
    message: Message,
    state: FSMContext,
) -> None:
    description = (message.text or "").strip()

    if not 5 <= len(description) <= 3000:
        await message.answer(
            "⚠️ Описание должно содержать от 5 до 3000 символов."
        )
        return

    await state.update_data(description=description)

    await message.answer(
        "Введите цену товара в рублях."
    )
    await state.set_state(AddProductFSM.price)


@dp.message(AddProductFSM.price)
async def process_product_price(
    message: Message,
    state: FSMContext,
) -> None:
    try:
        price = parse_money(message.text or "")

        if price < MIN_PRODUCT_PRICE_KOPECKS:
            raise ValueError(
                f"Минимальная цена — "
                f"{format_money(MIN_PRODUCT_PRICE_KOPECKS)} ₽"
            )
    except ValueError as exc:
        await message.answer(f"⚠️ {exc}")
        return

    data = await state.get_data()
    category = data.get("category")
    title = data.get("title")
    description = data.get("description")

    if not category or not title or not description:
        await state.clear()
        await message.answer(
            "⚠️ Сессия добавления товара устарела."
        )
        return

    await get_or_create_user(
        message.from_user.id,
        message.from_user.username,
    )

    result = await db_execute(
        """
        INSERT INTO products(
            seller_id,
            category,
            title,
            description,
            price_kopecks,
            status
        )
        VALUES (?, ?, ?, ?, ?, 'active')
        """,
        (
            message.from_user.id,
            category,
            title,
            description,
            price,
        ),
    )

    product_id = result["lastrowid"]
    link = await get_product_link(product_id)
    seller_receives = price - calculate_fee(
        price,
        SELLER_COMMISSION_RATE,
    )

    await state.clear()

    rows = [
        [
            InlineKeyboardButton(
                text="👀 Открыть карточку",
                callback_data=f"view_prod_{product_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text="📦 Мои товары",
                callback_data="back_to_my_prods",
            )
        ],
    ]

    text = (
        f"🎉 <b>Товар #{product_id} опубликован!</b>\n\n"
        f"Название: <b>{escape(title)}</b>\n"
        f"Цена: <b>{format_money(price)} ₽</b>\n"
        f"Ожидаемая выплата: "
        f"<b>{format_money(seller_receives)} ₽</b>\n\n"
        f"Ссылка:\n<code>{escape(link)}</code>"
    )

    await answer_html(
        message,
        text,
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


# =========================================================
# QUESTIONS TO SELLER
# =========================================================

@dp.callback_query(F.data.startswith("ask_seller_"))
async def ask_seller_start(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await answer_callback(callback)

    product_id = parse_callback_id(
        callback.data,
        "ask_seller_",
    )
    if not product_id:
        return

    product = await db_fetchone(
        "SELECT * FROM products WHERE id = ?",
        (product_id,),
    )

    if not product or product["status"] == "deleted":
        await callback.message.answer("⚠️ Товар недоступен.")
        return

    if product["seller_id"] == callback.from_user.id:
        await callback.message.answer(
            "⚠️ Нельзя задавать вопрос самому себе."
        )
        return

    await state.clear()
    await state.update_data(
        target_seller_id=product["seller_id"],
        product_id=product_id,
    )

    await callback.message.answer(
        f"💬 Вопрос по товару "
        f"<b>{escape(product['title'])}</b>\n\n"
        "Введите текст вопроса.",
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(AskSellerFSM.question_text)


@dp.message(AskSellerFSM.question_text)
async def process_seller_question(
    message: Message,
    state: FSMContext,
) -> None:
    question = (message.text or "").strip()

    if not 2 <= len(question) <= 1000:
        await message.answer(
            "⚠️ Вопрос должен содержать от 2 до 1000 символов."
        )
        return

    data = await state.get_data()
    seller_id = int(data.get("target_seller_id", 0))
    product_id = int(data.get("product_id", 0))

    product = await db_fetchone(
        "SELECT * FROM products WHERE id = ?",
        (product_id,),
    )

    if not product or product["seller_id"] != seller_id:
        await state.clear()
        await message.answer("⚠️ Товар больше недоступен.")
        return

    try:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✉️ Ответить покупателю",
                        callback_data=(
                            f"reply_q_{message.from_user.id}_{product_id}"
                        ),
                    )
                ]
            ]
        )

        await send_html(
            seller_id,
            f"🔔 <b>Новый вопрос по товару #{product_id}</b>\n\n"
            f"Товар: <b>{escape(product['title'])}</b>\n\n"
            f"Вопрос:\n{escape(question)}",
            keyboard,
        )

        await message.answer("✅ Вопрос отправлен продавцу.")
    except Exception:
        logger.exception("Failed to send seller question")
        await message.answer(
            "⚠️ Не удалось отправить вопрос продавцу."
        )
    finally:
        await state.clear()


@dp.callback_query(F.data.startswith("reply_q_"))
async def reply_question_start(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await answer_callback(callback)

    parts = callback.data.split("_")

    if len(parts) != 4:
        return

    try:
        buyer_id = int(parts[2])
        product_id = int(parts[3])
    except ValueError:
        return

    product = await db_fetchone(
        "SELECT * FROM products WHERE id = ?",
        (product_id,),
    )

    if not product or product["seller_id"] != callback.from_user.id:
        await callback.message.answer("⚠️ Нет доступа к этому вопросу.")
        return

    await state.clear()
    await state.update_data(
        target_buyer_id=buyer_id,
        product_id=product_id,
    )

    await callback.message.answer(
        "Введите ответ покупателю."
    )
    await state.set_state(AnswerBuyerFSM.answer_text)


@dp.message(AnswerBuyerFSM.answer_text)
async def process_seller_answer(
    message: Message,
    state: FSMContext,
) -> None:
    answer = (message.text or "").strip()

    if not 1 <= len(answer) <= 2000:
        await message.answer(
            "⚠️ Ответ должен содержать от 1 до 2000 символов."
        )
        return

    data = await state.get_data()
    buyer_id = int(data.get("target_buyer_id", 0))
    product_id = int(data.get("product_id", 0))

    product = await db_fetchone(
        "SELECT * FROM products WHERE id = ?",
        (product_id,),
    )

    if not product or product["seller_id"] != message.from_user.id:
        await state.clear()
        await message.answer("⚠️ Нет доступа к этому товару.")
        return

    try:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="👀 Открыть товар",
                        callback_data=f"view_prod_{product_id}",
                    )
                ]
            ]
        )

        await send_html(
            buyer_id,
            f"📩 <b>Ответ продавца</b>\n\n"
            f"Товар: <b>{escape(product['title'])}</b>\n\n"
            f"{escape(answer)}",
            keyboard,
        )

        await message.answer("✅ Ответ отправлен.")
    except Exception:
        logger.exception("Failed to send seller answer")
        await message.answer(
            "⚠️ Не удалось отправить ответ покупателю."
        )
    finally:
        await state.clear()


# =========================================================
# ESCROW DEALS
# =========================================================

@dp.callback_query(F.data.startswith("buy_item_"))
async def buy_item(callback: CallbackQuery) -> None:
    await answer_callback(callback)

    product_id = parse_callback_id(
        callback.data,
        "buy_item_",
    )
    if not product_id:
        return

    product = await db_fetchone(
        "SELECT * FROM products WHERE id = ?",
        (product_id,),
    )

    if not product or product["status"] != "active":
        await callback.message.answer(
            "⚠️ Товар уже недоступен."
        )
        return

    if product["seller_id"] == callback.from_user.id:
        await callback.message.answer(
            "⚠️ Нельзя купить собственный товар."
        )
        return

    buyer = await get_or_create_user(
        callback.from_user.id,
        callback.from_user.username,
    )

    price = product["price_kopecks"]

    if buyer["balance_kopecks"] < price:
        missing = price - buyer["balance_kopecks"]
        fee = calculate_fee(missing, BUYER_DEPOSIT_RATE)

        rows = [
            [
                InlineKeyboardButton(
                    text="👛 Пополнить баланс",
                    callback_data="wallet_topup",
                )
            ]
        ]

        text = (
            "⚠️ <b>Недостаточно средств</b>\n\n"
            f"Цена: <b>{format_money(price)} ₽</b>\n"
            f"Баланс: <b>{format_money(buyer['balance_kopecks'])} ₽</b>\n"
            f"Не хватает: <b>{format_money(missing)} ₽</b>\n"
            f"Комиссия пополнения: <b>{format_money(fee)} ₽</b>\n"
            f"К оплате: <b>{format_money(missing + fee)} ₽</b>"
        )

        await answer_html(
            callback.message,
            text,
            InlineKeyboardMarkup(inline_keyboard=rows),
        )
        return

    rows = [
        [
            InlineKeyboardButton(
                text=f"✅ Подтвердить оплату {format_money(price)} ₽",
                callback_data=f"confirm_pay_{product_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data="close_menu",
            )
        ],
    ]

    text = (
        "🛡 <b>Подтверждение безопасной сделки</b>\n\n"
        f"Товар: <b>{escape(product['title'])}</b>\n"
        f"Сумма: <b>{format_money(price)} ₽</b>\n"
        f"Баланс после оплаты: "
        f"<b>{format_money(buyer['balance_kopecks'] - price)} ₽</b>\n\n"
        "Деньги будут удерживаться до подтверждения получения."
    )

    await answer_html(
        callback.message,
        text,
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@dp.callback_query(F.data.startswith("confirm_pay_"))
async def confirm_payment(callback: CallbackQuery) -> None:
    await answer_callback(callback)

    product_id = parse_callback_id(
        callback.data,
        "confirm_pay_",
    )
    if not product_id:
        return

    await get_or_create_user(
        callback.from_user.id,
        callback.from_user.username,
    )

    deal = await db_transaction(
        _create_deal_tx,
        product_id,
        callback.from_user.id,
    )

    if not deal:
        await callback.message.answer(
            "⚠️ Товар уже куплен или недостаточно средств."
        )
        return

    buyer = await get_or_create_user(
        callback.from_user.id,
        callback.from_user.username,
    )

    buyer_text = (
        f"🎉 <b>Сделка #{deal['deal_id']} создана</b>\n\n"
        f"Товар: <b>{escape(deal['title'])}</b>\n"
        f"Заморожено: <b>{format_money(deal['amount_kopecks'])} ₽</b>\n\n"
        "Ожидайте передачу товара от продавца. "
        "После проверки подтвердите получение."
    )

    buyer_rows = [
        [
            InlineKeyboardButton(
                text="✅ Подтвердить получение",
                callback_data=f"buyer_done_{deal['deal_id']}",
            )
        ],
        [
            InlineKeyboardButton(
                text="⚠️ Открыть спор",
                callback_data=f"deal_dispute_{deal['deal_id']}",
            )
        ]
    ]
    buyer_rows.extend(support_keyboard())

    await safe_edit_message(
        callback.message,
        buyer_text,
        InlineKeyboardMarkup(inline_keyboard=buyer_rows),
    )

    seller_text = (
        f"🚨 <b>Товар оплачен — сделка #{deal['deal_id']}</b>\n\n"
        f"Товар: <b>{escape(deal['title'])}</b>\n"
        f"Сумма: <b>{format_money(deal['amount_kopecks'])} ₽</b>\n"
        f"Ваша выплата: "
        f"<b>{format_money(deal['seller_receives_kopecks'])} ₽</b>\n\n"
        "Передайте данные товара покупателю."
    )

    try:
        await send_html(
            deal["seller_id"],
            seller_text,
        )
    except Exception:
        logger.exception("Failed to notify seller")

    del buyer


@dp.callback_query(F.data.startswith("buyer_done_"))
async def buyer_confirm_delivery(callback: CallbackQuery) -> None:
    await answer_callback(callback)

    deal_id = parse_callback_id(
        callback.data,
        "buyer_done_",
    )
    if not deal_id:
        return

    deal = await db_transaction(
        _complete_deal_tx,
        deal_id,
        callback.from_user.id,
    )

    if not deal:
        await callback.message.answer(
            "⚠️ Сделка уже завершена, отменена или недоступна."
        )
        return

    rows = [
        [
            InlineKeyboardButton(
                text="⭐ 1",
                callback_data=f"rate_{deal_id}_1",
            ),
            InlineKeyboardButton(
                text="⭐ 2",
                callback_data=f"rate_{deal_id}_2",
            ),
            InlineKeyboardButton(
                text="⭐ 3",
                callback_data=f"rate_{deal_id}_3",
            ),
            InlineKeyboardButton(
                text="⭐ 4",
                callback_data=f"rate_{deal_id}_4",
            ),
            InlineKeyboardButton(
                text="⭐ 5",
                callback_data=f"rate_{deal_id}_5",
            ),
        ]
    ]

    await safe_edit_message(
        callback.message,
        f"✅ <b>Сделка #{deal_id} завершена.</b>\n\n"
        "Оцените продавца:",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )

    seller_rows = [
        [
            InlineKeyboardButton(
                text="🗑 Удалить проданный товар",
                callback_data=f"del_prod_{deal['product_id']}",
            )
        ],
        [
            InlineKeyboardButton(
                text="💳 Вывести средства",
                callback_data="wallet_withdraw",
            )
        ],
    ]

    try:
        await send_html(
            deal["seller_id"],
            f"🎉 Сделка #{deal_id} завершена.\n\n"
            f"Зачислено: "
            f"<b>{format_money(deal['seller_receives_kopecks'])} ₽</b>",
            InlineKeyboardMarkup(inline_keyboard=seller_rows),
        )
    except Exception:
        logger.exception("Failed to notify seller about completion")


@dp.callback_query(F.data.startswith("deal_dispute_"))
async def open_deal_dispute(callback: CallbackQuery) -> None:
    await answer_callback(callback)

    deal_id = parse_callback_id(
        callback.data,
        "deal_dispute_",
    )
    if not deal_id:
        return

    deal, changed = await db_transaction(
        _open_dispute_tx,
        deal_id,
        callback.from_user.id,
    )

    if not deal:
        await callback.message.answer(
            "⚠️ Сделка не найдена или у вас нет доступа."
        )
        return

    if not changed:
        await callback.message.answer(
            "⚠️ Спор уже открыт или сделка больше не может быть оспорена."
        )
        return

    rows = []
    rows.extend(support_keyboard())

    await safe_edit_message(
        callback.message,
        f"⚠️ <b>По сделке #{deal_id} открыт спор.</b>\n\n"
        "Средства остаются заблокированными до решения администратора.",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )

    admin_text = (
        f"🚨 <b>Открыт спор по сделке #{deal_id}</b>\n\n"
        f"Товар: <b>{escape(deal['title'])}</b>\n"
        f"Сумма: <b>{format_money(deal['amount_kopecks'])} ₽</b>\n"
        f"Покупатель: <code>{deal['buyer_id']}</code>\n"
        f"Продавец: <code>{deal['seller_id']}</code>"
    )

    for admin_id in ADMIN_IDS:
        try:
            await send_html(admin_id, admin_text)
        except Exception:
            logger.exception("Failed to notify admin about dispute")


# =========================================================
# DEALS LIST AND REVIEWS
# =========================================================

DEAL_STATUS_LABELS = {
    "created": "⏳ Создана",
    "paid": "🔒 Оплачена",
    "completed": "✅ Завершена",
    "dispute": "⚠️ Спор",
    "cancelled": "❌ Отменена",
    "refunded": "↩️ Возвращена",
}


@dp.message(F.text == "📂 Мои сделки")
async def navigation_deals(
    message: Message,
    state: FSMContext,
) -> None:
    await state.clear()

    await get_or_create_user(
        message.from_user.id,
        message.from_user.username,
    )

    deals = await db_fetchall(
        """
        SELECT *
        FROM deals
        WHERE buyer_id = ? OR seller_id = ?
        ORDER BY id DESC
        LIMIT 25
        """,
        (
            message.from_user.id,
            message.from_user.id,
        ),
    )

    if not deals:
        await message.answer(
            "📂 Сделок пока нет."
        )
        return

    lines = ["📂 <b>Мои сделки</b>\n"]
    rows: list[list[InlineKeyboardButton]] = []

    for deal in deals:
        role = (
            "Покупатель"
            if deal["buyer_id"] == message.from_user.id
            else "Продавец"
        )

        lines.append(
            f"🤝 <b>#{deal['id']}</b> — {role}\n"
            f"{escape(deal['title'])}\n"
            f"Сумма: <b>{format_money(deal['amount_kopecks'])} ₽</b>\n"
            f"Статус: {DEAL_STATUS_LABELS.get(deal['status'], deal['status'])}\n"
        )

        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Открыть сделку #{deal['id']}",
                    callback_data=f"deal_open_{deal['id']}",
                )
            ]
        )

    await answer_html(
        message,
        "\n".join(lines),
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@dp.callback_query(F.data.startswith("deal_open_"))
async def open_deal(callback: CallbackQuery) -> None:
    await answer_callback(callback)

    deal_id = parse_callback_id(
        callback.data,
        "deal_open_",
    )
    if not deal_id:
        return

    deal = await db_fetchone(
        "SELECT * FROM deals WHERE id = ?",
        (deal_id,),
    )

    if not deal:
        await callback.message.answer("⚠️ Сделка не найдена.")
        return

    user_id = callback.from_user.id

    if user_id not in (deal["buyer_id"], deal["seller_id"]):
        await callback.message.answer(
            "⚠️ У вас нет доступа к этой сделке."
        )
        return

    role = (
        "Покупатель"
        if deal["buyer_id"] == user_id
        else "Продавец"
    )

    rows: list[list[InlineKeyboardButton]] = []

    if role == "Покупатель" and deal["status"] == "paid":
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить получение",
                    callback_data=f"buyer_done_{deal_id}",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="⚠️ Открыть спор",
                    callback_data=f"deal_dispute_{deal_id}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 Закрыть",
                callback_data="close_menu",
            )
        ]
    )

    text = (
        f"🤝 <b>Сделка #{deal_id}</b>\n\n"
        f"Роль: <b>{role}</b>\n"
        f"Товар: <b>{escape(deal['title'])}</b>\n"
        f"Сумма: <b>{format_money(deal['amount_kopecks'])} ₽</b>\n"
        f"Статус: "
        f"<b>{DEAL_STATUS_LABELS.get(deal['status'], deal['status'])}</b>"
    )

    await answer_html(
        callback.message,
        text,
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@dp.callback_query(F.data.startswith("rate_"))
async def rate_seller(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await answer_callback(callback)

    parts = callback.data.split("_")

    if len(parts) != 3:
        return

    try:
        deal_id = int(parts[1])
        rating = int(parts[2])
    except ValueError:
        return

    if not 1 <= rating <= 5:
        return

    deal = await db_fetchone(
        "SELECT * FROM deals WHERE id = ?",
        (deal_id,),
    )

    if (
        not deal
        or deal["buyer_id"] != callback.from_user.id
        or deal["status"] != "completed"
    ):
        await callback.message.answer(
            "⚠️ Оценка недоступна."
        )
        return

    existing = await db_fetchone(
        """
        SELECT id
        FROM reviews
        WHERE deal_id = ?
          AND author_user_id = ?
        """,
        (
            deal_id,
            callback.from_user.id,
        ),
    )

    if existing:
        await callback.message.answer(
            "Вы уже оценили эту сделку."
        )
        return

    await state.clear()
    await state.update_data(
        deal_id=deal_id,
        rating=rating,
    )

    await safe_edit_message(
        callback.message,
        f"⭐ Вы выбрали оценку <b>{rating}/5</b>.\n\n"
        "Введите комментарий или отправьте символ «-».",
    )
    await state.set_state(ReviewFSM.comment)


@dp.message(ReviewFSM.comment)
async def process_review(
    message: Message,
    state: FSMContext,
) -> None:
    comment = (message.text or "").strip()

    if comment == "-":
        comment = ""

    if len(comment) > 2000:
        await message.answer(
            "⚠️ Отзыв не должен превышать 2000 символов."
        )
        return

    data = await state.get_data()
    deal_id = int(data.get("deal_id", 0))
    rating = int(data.get("rating", 0))

    if not deal_id or not 1 <= rating <= 5:
        await state.clear()
        await message.answer("⚠️ Сессия оценки устарела.")
        return

    saved = await db_transaction(
        _complete_review_tx,
        deal_id,
        message.from_user.id,
        rating,
        comment,
    )

    await state.clear()

    if not saved:
        await message.answer(
            "⚠️ Отзыв уже существует или недоступен."
        )
        return

    await message.answer(
        "🙏 Спасибо за отзыв!",
        reply_markup=get_main_keyboard(),
    )


# =========================================================
# PARTNERS AND HELP
# =========================================================

@dp.message(F.text == "🌐 Подключить OnyxVPN")
async def navigation_onyxvpn(
    message: Message,
    state: FSMContext,
) -> None:
    await state.clear()

    await answer_html(
        message,
        "🌐 <b>OnyxVPN</b>\n\n"
        "Партнёрский сервис VPN.",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🚀 Открыть OnyxVPN",
                        url=ONYX_VPN_URL,
                    )
                ]
            ]
        ),
    )


@dp.message(F.text == "ℹ️ Помощь / Поддержка")
async def navigation_help(
    message: Message,
    state: FSMContext,
) -> None:
    await state.clear()

    rows = []
    rows.extend(support_keyboard())

    text = (
        "ℹ️ <b>Помощь</b>\n\n"
        "<b>Безопасная сделка:</b>\n"
        "1. Покупатель оплачивает товар с баланса.\n"
        "2. Средства блокируются.\n"
        "3. Продавец передаёт товар.\n"
        "4. Покупатель подтверждает получение.\n"
        "5. Продавец получает выплату.\n\n"
        "<b>Комиссии:</b>\n"
        f"• Пополнение: {rate_percent(BUYER_DEPOSIT_RATE)}\n"
        f"• Комиссия продавца: {rate_percent(SELLER_COMMISSION_RATE)}\n"
        f"• Минимальный вывод: "
        f"{format_money(MIN_WITHDRAW_KOPECKS)} ₽"
    )

    await answer_html(
        message,
        text,
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


# =========================================================
# ADMIN
# =========================================================

@dp.message(Command("admin"))
async def admin_dashboard(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    vault = await db_fetchone(
        "SELECT COALESCE(SUM(fee_kopecks), 0) AS total FROM platform_vault"
    )
    topup_fees = await db_fetchone(
        """
        SELECT COALESCE(SUM(fee_kopecks), 0) AS total
        FROM platform_vault
        WHERE source_type = 'buyer_topup_fee'
        """
    )
    deal_fees = await db_fetchone(
        """
        SELECT COALESCE(SUM(fee_kopecks), 0) AS total
        FROM platform_vault
        WHERE source_type = 'seller_deal_fee'
        """
    )
    pending_withdrawals = await db_fetchone(
        """
        SELECT COUNT(*) AS total
        FROM withdrawals
        WHERE status = 'pending'
        """
    )
    active_products = await db_fetchone(
        """
        SELECT COUNT(*) AS total
        FROM products
        WHERE status = 'active'
        """
    )

    text = (
        "👑 <b>Административная панель</b>\n\n"
        f"Казна: <b>{format_money(vault['total'])} ₽</b>\n"
        f"Пополнения: <b>{format_money(topup_fees['total'])} ₽</b>\n"
        f"Сделки: <b>{format_money(deal_fees['total'])} ₽</b>\n\n"
        f"Активных товаров: <b>{active_products['total']}</b>\n"
        f"Заявок на вывод: <b>{pending_withdrawals['total']}</b>\n\n"
        "<code>/addbalance USER_ID AMOUNT</code>"
    )

    await answer_html(message, text)


def _add_balance_tx(
    conn: sqlite3.Connection,
    user_id: int,
    amount_kopecks: int,
) -> bool:
    conn.execute(
        """
        INSERT INTO users(user_id)
        VALUES (?)
        ON CONFLICT(user_id) DO NOTHING
        """,
        (user_id,),
    )

    updated = conn.execute(
        """
        UPDATE users
        SET balance_kopecks = balance_kopecks + ?
        WHERE user_id = ?
        """,
        (amount_kopecks, user_id),
    )

    return updated.rowcount == 1


@dp.message(Command("addbalance"))
async def admin_add_balance(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    args = (message.text or "").split()

    if len(args) != 3:
        await message.answer(
            "Формат: /addbalance USER_ID AMOUNT"
        )
        return

    try:
        target_user_id = int(args[1])
        amount = parse_money(args[2])
    except ValueError:
        await message.answer("⚠️ Некорректные параметры.")
        return

    success = await db_transaction(
        _add_balance_tx,
        target_user_id,
        amount,
    )

    if not success:
        await message.answer("⚠️ Не удалось изменить баланс.")
        return

    await message.answer(
        f"✅ Баланс пользователя {target_user_id} увеличен на "
        f"{format_money(amount)} ₽."
    )

    try:
        await send_html(
            target_user_id,
            f"💰 Администратор начислил вам "
            f"<b>{format_money(amount)} ₽</b>.",
        )
    except Exception:
        logger.exception("Failed to notify user about manual balance credit")


# =========================================================
# GENERAL CALLBACKS
# =========================================================

@dp.callback_query(F.data == "close_menu")
async def close_menu(callback: CallbackQuery) -> None:
    await answer_callback(callback)

    if callback.message:
        try:
            await callback.message.delete()
        except Exception:
            pass


# =========================================================
# STARTUP
# =========================================================

def _close_database(conn: sqlite3.Connection) -> None:
    global _persistent_conn

    if _persistent_conn is not None:
        _persistent_conn.close()
        _persistent_conn = None


async def main() -> None:
    global BOT_USERNAME

    await init_database()

    me = await bot.get_me()
    BOT_USERNAME = me.username or ""

    logger.info(
        "Bot started: @%s, admins=%s",
        BOT_USERNAME,
        sorted(ADMIN_IDS),
    )

    await bot.delete_webhook(drop_pending_updates=True)

    try:
        await dp.start_polling(bot)
    finally:
        try:
            await bot.session.close()
        except Exception:
            pass

        try:
            await db_call(_close_database)
        except Exception:
            logger.exception("Failed to close database")

        db_executor.shutdown(wait=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped")
