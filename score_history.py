from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Iterable


DEFAULT_DATABASE_PATH = Path(__file__).with_name("user_scores.db")

# Локально база лежить поруч із кодом.
# На Railway:
# SCORE_DB_PATH=/data/user_scores.db
DATABASE_PATH = Path(
    os.getenv(
        "SCORE_DB_PATH",
        str(DEFAULT_DATABASE_PATH),
    )
).expanduser()

MAX_SCORES_PER_CONTEXT = 5
MAX_NAME_QUERIES_PER_USER = 5
ALL_SPECIALITIES_CONTEXT = "all"


def speciality_context(speciality_key: str) -> str:
    """Повертає ключ історії для обраної спеціальності."""

    return f"speciality:{speciality_key}"


def all_speciality_context(speciality_key: str) -> str:
    """Повертає ключ історії для ОП у режимі «Усі»."""

    return f"all_speciality:{speciality_key}"


def _connect() -> sqlite3.Connection:
    """Створює підключення до SQLite-бази."""

    DATABASE_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    connection = sqlite3.connect(
        DATABASE_PATH,
        timeout=30,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")

    return connection


def _table_exists(
    connection: sqlite3.Connection,
    table_name: str,
) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone()

    return row is not None


def _create_score_history_table(
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS score_history (
            user_id INTEGER NOT NULL,
            context_key TEXT NOT NULL,
            score_milli INTEGER NOT NULL,
            used_at REAL NOT NULL,
            PRIMARY KEY (
                user_id,
                context_key,
                score_milli
            )
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_score_history_latest
        ON score_history (
            user_id,
            context_key,
            used_at DESC
        )
        """
    )


def _migrate_legacy_score_history(
    connection: sqlite3.Connection,
) -> None:
    """
    Мігрує стару таблицю без context_key.

    Старі бали переносяться до історії режиму
    «Усі спеціальності».
    """

    if not _table_exists(connection, "score_history"):
        _create_score_history_table(connection)
        return

    columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(score_history)"
        ).fetchall()
    }

    if "context_key" in columns:
        _create_score_history_table(connection)
        return

    connection.execute(
        "ALTER TABLE score_history "
        "RENAME TO score_history_legacy"
    )

    _create_score_history_table(connection)

    connection.execute(
        """
        INSERT OR IGNORE INTO score_history (
            user_id,
            context_key,
            score_milli,
            used_at
        )
        SELECT
            user_id,
            ?,
            score_milli,
            used_at
        FROM score_history_legacy
        """,
        (ALL_SPECIALITIES_CONTEXT,),
    )

    connection.execute(
        "DROP TABLE score_history_legacy"
    )


def init_database() -> None:
    """Створює або оновлює таблиці історії."""

    with _connect() as connection:
        _migrate_legacy_score_history(connection)

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS name_query_history (
                user_id INTEGER NOT NULL,
                normalized_query TEXT NOT NULL,
                query_text TEXT NOT NULL,
                used_at REAL NOT NULL,
                PRIMARY KEY (
                    user_id,
                    normalized_query
                )
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
                idx_name_query_history_latest
            ON name_query_history (
                user_id,
                used_at DESC
            )
            """
        )


def _to_milli(score: float) -> int:
    """Перетворює 145.630 на 145630."""

    return round(score * 1000)


def save_score(
    user_id: int,
    score: float,
    context_key: str = ALL_SPECIALITIES_CONTEXT,
) -> None:
    """
    Зберігає останні 5 унікальних балів у межах контексту.

    Кожна обрана спеціальність має власну історію.
    Режим «Усі спеціальності» має окрему історію.
    """

    score_milli = _to_milli(score)
    used_at = time.time()

    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO score_history (
                user_id,
                context_key,
                score_milli,
                used_at
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(
                user_id,
                context_key,
                score_milli
            )
            DO UPDATE SET used_at = excluded.used_at
            """,
            (
                user_id,
                context_key,
                score_milli,
                used_at,
            ),
        )

        connection.execute(
            """
            DELETE FROM score_history
            WHERE user_id = ?
              AND context_key = ?
              AND score_milli NOT IN (
                  SELECT score_milli
                  FROM score_history
                  WHERE user_id = ?
                    AND context_key = ?
                  ORDER BY used_at DESC
                  LIMIT ?
              )
            """,
            (
                user_id,
                context_key,
                user_id,
                context_key,
                MAX_SCORES_PER_CONTEXT,
            ),
        )


def get_recent_scores(
    user_id: int,
    context_key: str = ALL_SPECIALITIES_CONTEXT,
    limit: int = MAX_SCORES_PER_CONTEXT,
) -> list[float]:
    """Повертає останні бали конкретного контексту."""

    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT score_milli
            FROM score_history
            WHERE user_id = ?
              AND context_key = ?
            ORDER BY used_at DESC
            LIMIT ?
            """,
            (
                user_id,
                context_key,
                limit,
            ),
        ).fetchall()

    return [
        row["score_milli"] / 1000
        for row in rows
    ]


def get_latest_score(
    user_id: int,
    context_key: str = ALL_SPECIALITIES_CONTEXT,
) -> float | None:
    """Повертає останній бал конкретного контексту."""

    with _connect() as connection:
        row = connection.execute(
            """
            SELECT score_milli
            FROM score_history
            WHERE user_id = ?
              AND context_key = ?
            ORDER BY used_at DESC
            LIMIT 1
            """,
            (
                user_id,
                context_key,
            ),
        ).fetchone()

    if row is None:
        return None

    return row["score_milli"] / 1000


def get_latest_scores(
    user_id: int,
    context_keys: Iterable[str],
) -> dict[str, float]:
    """Повертає останні бали для кількох контекстів."""

    result: dict[str, float] = {}

    for context_key in context_keys:
        score = get_latest_score(
            user_id,
            context_key,
        )

        if score is not None:
            result[context_key] = score

    return result


def normalize_name_query(query_text: str) -> str:
    """Нормалізує запит за ПІБ для перевірки дублікатів."""

    return " ".join(
        query_text.split()
    ).casefold()


def save_name_query(
    user_id: int,
    query_text: str,
) -> None:
    """
    Зберігає один із останніх 5 унікальних пошуків за ПІБ.

    Повторний пошук переноситься на початок історії.
    """

    cleaned_query = " ".join(
        query_text.split()
    ).strip()

    if not cleaned_query:
        return

    normalized_query = normalize_name_query(
        cleaned_query
    )

    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO name_query_history (
                user_id,
                normalized_query,
                query_text,
                used_at
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(
                user_id,
                normalized_query
            )
            DO UPDATE SET
                query_text = excluded.query_text,
                used_at = excluded.used_at
            """,
            (
                user_id,
                normalized_query,
                cleaned_query,
                time.time(),
            ),
        )

        connection.execute(
            """
            DELETE FROM name_query_history
            WHERE user_id = ?
              AND normalized_query NOT IN (
                  SELECT normalized_query
                  FROM name_query_history
                  WHERE user_id = ?
                  ORDER BY used_at DESC
                  LIMIT ?
              )
            """,
            (
                user_id,
                user_id,
                MAX_NAME_QUERIES_PER_USER,
            ),
        )


def get_recent_name_queries(
    user_id: int,
    limit: int = MAX_NAME_QUERIES_PER_USER,
) -> list[str]:
    """Повертає останні запити за ПІБ від нового до старого."""

    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT query_text
            FROM name_query_history
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
        row["query_text"]
        for row in rows
    ]
