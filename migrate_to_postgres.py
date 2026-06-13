"""
Міграція даних з SQLite (wandr.db) у PostgreSQL.

Використання:
    python migrate_to_postgres.py

Змінні середовища (або значення за замовчуванням):
    DATABASE_URL  — рядок підключення до PostgreSQL
                    за замовчуванням: postgresql://postgres:postgres@localhost:5432/wandr
    SQLITE_PATH   — шлях до wandr.db
                    за замовчуванням: ./wandr.db
"""

import os
import sqlite3
import psycopg2
import psycopg2.extras

SQLITE_PATH  = os.environ.get("SQLITE_PATH", os.path.join(os.path.dirname(__file__), "wandr.db"))
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/wandr")


def get_sqlite():
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_pg():
    return psycopg2.connect(DATABASE_URL)


def create_tables(pg):
    cur = pg.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            interests TEXT DEFAULT '[]',
            created_at TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS saved_routes (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            title TEXT NOT NULL,
            destination TEXT,
            duration TEXT,
            route_data TEXT NOT NULL,
            interests TEXT DEFAULT '[]',
            budget_level TEXT DEFAULT 'mid',
            created_at TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            route_id INTEGER NOT NULL REFERENCES saved_routes(id),
            rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
            comment TEXT,
            created_at TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'),
            UNIQUE(user_id, route_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS password_resets (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            token TEXT UNIQUE NOT NULL,
            expires_at TEXT NOT NULL,
            used INTEGER DEFAULT 0
        )
    """)
    pg.commit()
    cur.close()
    print("Таблиці створено.")


def migrate_table(sqlite_conn, pg, table, columns, conflict="DO NOTHING"):
    """Copy all rows from a SQLite table into PostgreSQL, skipping conflicts."""
    rows = sqlite_conn.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()
    if not rows:
        print(f"  {table}: немає записів.")
        return 0

    col_list   = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    sql = (
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"
        f" ON CONFLICT {conflict}"
    )

    cur = pg.cursor()
    count = 0
    for row in rows:
        try:
            cur.execute(sql, tuple(row[c] for c in columns))
            count += 1
        except Exception as e:
            pg.rollback()
            print(f"  [{table}] пропущено рядок id={row['id']}: {e}")
            cur = pg.cursor()
    pg.commit()
    cur.close()
    return count


def fix_sequences(pg):
    """Reset all SERIAL sequences to max(id)+1 after bulk INSERT with explicit IDs."""
    cur = pg.cursor()
    for table in ("users", "saved_routes", "reviews", "password_resets"):
        cur.execute(f"SELECT MAX(id) FROM {table}")
        max_id = cur.fetchone()[0] or 0
        cur.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), %s)", (max(max_id, 1),))
    pg.commit()
    cur.close()
    print("Послідовності (sequences) оновлено.")


def main():
    if not os.path.exists(SQLITE_PATH):
        print(f"SQLite-файл не знайдено: {SQLITE_PATH}")
        return

    print(f"SQLite: {SQLITE_PATH}")
    print(f"PostgreSQL: {DATABASE_URL}\n")

    sqlite_conn = get_sqlite()
    pg = get_pg()

    print("Створення таблиць...")
    create_tables(pg)

    print("Міграція users...")
    n = migrate_table(
        sqlite_conn, pg, "users",
        ["id", "email", "name", "password_hash", "interests", "created_at"],
        conflict="(email) DO NOTHING",
    )
    print(f"  users: {n} записів перенесено.")

    print("Міграція saved_routes...")
    n = migrate_table(
        sqlite_conn, pg, "saved_routes",
        ["id", "user_id", "title", "destination", "duration",
         "route_data", "interests", "budget_level", "created_at"],
        conflict="DO NOTHING",
    )
    print(f"  saved_routes: {n} записів перенесено.")

    print("Міграція reviews...")
    n = migrate_table(
        sqlite_conn, pg, "reviews",
        ["id", "user_id", "route_id", "rating", "comment", "created_at"],
        conflict="(user_id, route_id) DO NOTHING",
    )
    print(f"  reviews: {n} записів перенесено.")

    print("Міграція password_resets...")
    n = migrate_table(
        sqlite_conn, pg, "password_resets",
        ["id", "user_id", "token", "expires_at", "used"],
        conflict="DO NOTHING",
    )
    print(f"  password_resets: {n} записів перенесено.")

    fix_sequences(pg)

    sqlite_conn.close()
    pg.close()
    print("\nМіграцію завершено успішно.")


if __name__ == "__main__":
    main()
