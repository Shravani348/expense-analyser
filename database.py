import sqlite3

DATABASE = 'expense.db'


def get_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row

    # ── PRAGMA 1: Enable Foreign Keys ─────────────
    # SQLite disables FK enforcement by default
    # This enables CASCADE DELETE to actually work
    conn.execute('PRAGMA foreign_keys = ON')

    # ── PRAGMA 2: WAL Mode ─────────────────────────
    # Write-Ahead Logging = faster reads & writes
    # Multiple readers can read while one writes
    conn.execute('PRAGMA journal_mode = WAL')

    return conn


def create_tables():
    conn   = get_connection()
    cursor = conn.cursor()

    # ════════════════════════════════════════════
    #   NORMALIZATION — Separate Categories Table
    #   Instead of storing "Food" text repeatedly
    #   in every expense row, we store it once here
    #   and reference by ID → saves space, ensures
    #   consistency, prevents typos
    # ════════════════════════════════════════════
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS categories (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT    NOT NULL UNIQUE
        )
    ''')

    # Insert default categories once
    default_categories = [
        'Food', 'Transport', 'Shopping', 'Entertainment',
        'Health', 'Education', 'Rent', 'Utilities', 'Other'
    ]
    for cat in default_categories:
        cursor.execute('''
            INSERT OR IGNORE INTO categories (name) VALUES (?)
        ''', (cat,))

    # ════════════════════════════════════════════
    #   TABLE 1: users
    #   CHECK CONSTRAINT → username must be
    #   at least 3 characters long
    # ════════════════════════════════════════════
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT    NOT NULL UNIQUE
                            CHECK(length(username) >= 3),
            email    TEXT    NOT NULL UNIQUE,
            password TEXT    NOT NULL,
            created_at TEXT  DEFAULT (datetime('now'))
        )
    ''')
    # CHECK(length(username) >= 3)
    # → database rejects any username shorter than 3 chars
    # → even if Python validation is bypassed

    # ════════════════════════════════════════════
    #   TABLE 2: budgets
    #   CHECK CONSTRAINTS:
    #   → amount must be positive
    #   → month must be 1–12
    #   → year must be reasonable
    #   CASCADE DELETE → deleting a user
    #   automatically deletes all their budgets
    # ════════════════════════════════════════════
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS budgets (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            category_id INTEGER NOT NULL,
            amount      REAL    NOT NULL CHECK(amount > 0),
            month       INTEGER NOT NULL CHECK(month BETWEEN 1 AND 12),
            year        INTEGER NOT NULL CHECK(year BETWEEN 2000 AND 2100),
            FOREIGN KEY (user_id)
                REFERENCES users(id)
                ON DELETE CASCADE,
            FOREIGN KEY (category_id)
                REFERENCES categories(id)
                ON DELETE RESTRICT,
            UNIQUE(user_id, category_id, month, year)
        )
    ''')
    # ON DELETE CASCADE → user deleted = budgets deleted automatically
    # ON DELETE RESTRICT → can't delete a category that has budgets
    # UNIQUE(...) → one budget per category per month per user

    # ════════════════════════════════════════════
    #   TABLE 3: expenses
    #   CHECK CONSTRAINTS:
    #   → amount must be positive
    #   → date must be valid format
    #   CASCADE DELETE → user deleted =
    #   all expenses deleted automatically
    # ════════════════════════════════════════════
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS expenses (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            category_id INTEGER NOT NULL,
            amount      REAL    NOT NULL CHECK(amount > 0),
            date        TEXT    NOT NULL
                                CHECK(date LIKE '____-__-__'),
            note        TEXT    DEFAULT '',
            created_at  TEXT    DEFAULT (datetime('now')),
            FOREIGN KEY (user_id)
                REFERENCES users(id)
                ON DELETE CASCADE,
            FOREIGN KEY (category_id)
                REFERENCES categories(id)
                ON DELETE RESTRICT
        )
    ''')
    # CHECK(date LIKE '____-__-__')
    # → enforces YYYY-MM-DD format at DB level
    # CHECK(amount > 0)
    # → database rejects negative or zero amounts

    # ════════════════════════════════════════════
    #   TABLE 4: recurring_expenses
    #   CASCADE DELETE → user deleted =
    #   recurring entries deleted automatically
    # ════════════════════════════════════════════
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS recurring_expenses (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            category_id  INTEGER NOT NULL,
            amount       REAL    NOT NULL CHECK(amount > 0),
            note         TEXT    DEFAULT '',
            day_of_month INTEGER NOT NULL DEFAULT 1
                                 CHECK(day_of_month BETWEEN 1 AND 31),
            is_active    INTEGER NOT NULL DEFAULT 1
                                 CHECK(is_active IN (0, 1)),
            last_added   TEXT,
            created_at   TEXT    DEFAULT (datetime('now')),
            FOREIGN KEY (user_id)
                REFERENCES users(id)
                ON DELETE CASCADE,
            FOREIGN KEY (category_id)
                REFERENCES categories(id)
                ON DELETE RESTRICT
        )
    ''')

    # ── TABLE 5: ai_chat_history ──────────────────
    # Stores every message in every chat session
    # role = 'user' or 'assistant'
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ai_chat_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            session_id TEXT    NOT NULL,
            role       TEXT    NOT NULL CHECK(role IN ('user','assistant')),
            message    TEXT    NOT NULL,
            created_at TEXT    DEFAULT (datetime('now')),
            FOREIGN KEY (user_id)
                REFERENCES users(id)
                ON DELETE CASCADE
        )
    ''')
    # session_id → groups messages into one conversation
    # role       → who sent it: 'user' or 'assistant'
    # CHECK constraint → only 'user' or 'assistant' allowed

    # Index for fast chat history lookup
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_chat_user_session
        ON ai_chat_history(user_id, session_id)
    ''')
    # CHECK(is_active IN (0,1)) → only 0 or 1 allowed, like a boolean
    # CHECK(day_of_month BETWEEN 1 AND 31) → valid day only

    # ════════════════════════════════════════════
    #   INDEXES — Speed up frequent queries
    #   Without index → full table scan every query
    #   With index    → direct lookup, much faster
    # ════════════════════════════════════════════

    # Most queries filter by user_id — index it
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_expenses_user_id
        ON expenses(user_id)
    ''')

    # Dashboard filters by user_id + date together
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_expenses_user_date
        ON expenses(user_id, date)
    ''')

    # Category lookups in expenses
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_expenses_category
        ON expenses(category_id)
    ''')

    # Budget lookups by user + month + year
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_budgets_user_month
        ON budgets(user_id, month, year)
    ''')

    # Recurring expense lookups
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_recurring_user
        ON recurring_expenses(user_id, is_active)
    ''')

    # ════════════════════════════════════════════
    #   VIEWS — Saved queries as virtual tables
    #   Instead of writing long JOINs every time,
    #   we create a view once and query it simply
    # ════════════════════════════════════════════

    # View 1: expenses with category name joined in
    # Now we can SELECT * FROM v_expenses_full
    # and get category name automatically
    cursor.execute('''
        CREATE VIEW IF NOT EXISTS v_expenses_full AS
        SELECT
            e.id,
            e.user_id,
            e.amount,
            e.date,
            e.note,
            e.created_at,
            c.id   AS category_id,
            c.name AS category
        FROM expenses e
        JOIN categories c ON e.category_id = c.id
    ''')

    # View 2: budgets with category name joined in
    cursor.execute('''
        CREATE VIEW IF NOT EXISTS v_budgets_full AS
        SELECT
            b.id,
            b.user_id,
            b.amount,
            b.month,
            b.year,
            c.id   AS category_id,
            c.name AS category
        FROM budgets b
        JOIN categories c ON b.category_id = c.id
    ''')

    # View 3: monthly spending summary per user
    # Aggregates total spent per month automatically
    cursor.execute('''
        CREATE VIEW IF NOT EXISTS v_monthly_summary AS
        SELECT
            e.user_id,
            strftime('%Y', e.date)       AS year,
            strftime('%m', e.date)       AS month,
            strftime('%Y-%m', e.date)    AS month_year,
            c.name                       AS category,
            COUNT(*)                     AS txn_count,
            SUM(e.amount)                AS total_spent,
            AVG(e.amount)                AS avg_spent,
            MAX(e.amount)                AS max_spent,
            MIN(e.amount)                AS min_spent
        FROM expenses e
        JOIN categories c ON e.category_id = c.id
        GROUP BY e.user_id, month_year, c.name
    ''')

    # View 4: recurring expenses with category name
    cursor.execute('''
        CREATE VIEW IF NOT EXISTS v_recurring_full AS
        SELECT
            r.id,
            r.user_id,
            r.amount,
            r.note,
            r.day_of_month,
            r.is_active,
            r.last_added,
            c.id   AS category_id,
            c.name AS category
        FROM recurring_expenses r
        JOIN categories c ON r.category_id = c.id
    ''')

    # ── TABLE 6: saved_tips ───────────────────────
    # User can save any AI tip they like
    # source = 'advisor' or 'chat'
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS saved_tips (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            tip_text   TEXT    NOT NULL,
            source     TEXT    NOT NULL DEFAULT 'advisor'
                               CHECK(source IN ('advisor','chat')),
            category   TEXT    DEFAULT 'General',
            is_read    INTEGER NOT NULL DEFAULT 0
                               CHECK(is_read IN (0,1)),
            created_at TEXT    DEFAULT (datetime('now')),
            FOREIGN KEY (user_id)
                REFERENCES users(id)
                ON DELETE CASCADE
        )
    ''')
    # source   → where tip came from: advisor page or chat
    # is_read  → 0 = unread, 1 = read (for badge count)
    # category → which spending category this tip is about

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_saved_tips_user
        ON saved_tips(user_id, is_read)
    ''')

    # ── TABLE 7: goals ────────────────────────────
    # Savings goals like "Save ₹15000 for a phone"
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS goals (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL,
            title         TEXT    NOT NULL,
            target_amount REAL    NOT NULL CHECK(target_amount > 0),
            saved_amount  REAL    NOT NULL DEFAULT 0
                                  CHECK(saved_amount >= 0),
            deadline      TEXT,
            category      TEXT    DEFAULT 'General',
            status        TEXT    NOT NULL DEFAULT 'active'
                                  CHECK(status IN ('active','completed','cancelled')),
            ai_advice     TEXT,
            created_at    TEXT    DEFAULT (datetime('now')),
            updated_at    TEXT    DEFAULT (datetime('now')),
            FOREIGN KEY (user_id)
                REFERENCES users(id)
                ON DELETE CASCADE
        )
    ''')
    # target_amount → how much they want to save
    # saved_amount  → how much saved so far
    # deadline      → target date
    # ai_advice     → AI generated advice stored in DB
    # status        → active/completed/cancelled

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_goals_user_status
        ON goals(user_id, status)
    ''')
    conn.commit()
    conn.close()
    print('✅ All tables, indexes, views created successfully!')


def get_category_id(cursor, category_name):
    """
    Helper: get category id from name.
    Used in INSERT queries throughout app.py
    """
    cursor.execute(
        'SELECT id FROM categories WHERE name = ?',
        (category_name,)
    )
    row = cursor.fetchone()
    return row['id'] if row else None


def get_all_categories(cursor):
    """
    Helper: get all categories as list of dicts.
    Used in forms to populate dropdowns.
    """
    cursor.execute('SELECT id, name FROM categories ORDER BY name')
    return cursor.fetchall()


if __name__ == '__main__':
    create_tables()