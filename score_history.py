from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path


DEFAULT_DATABASE_PATH = Path(__file__).with_name("user_scores.db")

# Локально база лежить поруч із кодом.
# На Railway потрібно встановити:
# SCORE_DB_PATH=/data/user_scores.db
DATABASE_PATH = Path(
    os.getenv(
        "SCORE_DB_PATH",
        str(DEFAULT_DATABASE_PATH),
    )
).expanduser()

MAX_SCORES_PER_USER = 5


def _connect() -> sqlite3.Connection:
    """Створює підключення до локальної SQLite-бази."""

    DATABASE_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    connection = sqlite3.connect(
        DATABASE_PATH,
        timeout=30,
    )

    connection.row_factory = sqlite3.Row

    return connection


def init_database() -> None:
    """Створює таблицю історії, якщо її ще немає."""

    with _connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS score_history (
                user_id INTEGER NOT NULL,
                score_milli INTEGER NOT NULL,
                used_at REAL NOT NULL,
                PRIMARY KEY (user_id, score_milli)
            )
            """
        )


def _to_milli(score: float) -> int:
    """
    Зберігає бал як ціле число у тисячних.

    Наприклад, 145.630 перетворюється на 145630.
    """

    return round(score * 1000)


def save_score(user_id: int, score: float) -> None:
    """
    Додає бал або переносить уже наявний бал на початок.

    Для кожного користувача зберігаються лише останні
    MAX_SCORES_PER_USER унікальних значень.
    """

    score_milli = _to_milli(score)
    used_at = time.time()

    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO score_history (
                user_id,
                score_milli,
                used_at
            )
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, score_milli)
            DO UPDATE SET used_at = excluded.used_at
            """,
            (
                user_id,
                score_milli,
                used_at,
            ),
        )

        connection.execute(
            """
            DELETE FROM score_history
            WHERE user_id = ?
              AND score_milli NOT IN (
                  SELECT score_milli
                  FROM score_history
                  WHERE user_id = ?
                  ORDER BY used_at DESC
                  LIMIT ?
              )
            """,
            (
                user_id,
                user_id,
                MAX_SCORES_PER_USER,
            ),
        )


def get_recent_scores(
    user_id: int,
    limit: int = MAX_SCORES_PER_USER,
) -> list[float]:
    """Повертає останні бали від нового до старого."""

    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT score_milli
            FROM score_history
            WHERE user_id = ?
            ORDER BY used_at DESC
            LIMIT ?
            """,
            (
                user_id,
                limit,
            ),
        ).fetchall()

    return [
        row["score_milli"] / 1000
        for row in rows
    ]
