from __future__ import annotations

from pathlib import Path

from app.db.connection import get_conn


def main() -> None:
    migrations_dir = Path("app/db/migrations")
    files = sorted(migrations_dir.glob("*.sql"))
    if not files:
        raise RuntimeError("No migrations found")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                  version TEXT PRIMARY KEY,
                  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            for migration_file in files:
                version = migration_file.name
                cur.execute("SELECT 1 FROM schema_migrations WHERE version = %s", (version,))
                if cur.fetchone():
                    continue
                sql = migration_file.read_text()
                cur.execute(sql)
                cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
                print(f"Applied migration: {version}")
        conn.commit()


if __name__ == "__main__":
    main()
