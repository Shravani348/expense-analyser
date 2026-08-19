import re
import os

def migrate_database_py():
    with open('database.py', 'r', encoding='utf-8') as f:
        content = f.read()

    # 1. Imports
    content = content.replace('import sqlite3\n', 'import pymysql\nimport os\nfrom dotenv import load_dotenv\n\nload_dotenv()\n')

    # 2. get_connection
    old_conn = '''def get_connection():
    conn = sqlite3.connect(DATABASE, timeout=20)
    conn.row_factory = sqlite3.Row

    # ── PRAGMA 1: Enable Foreign Keys ─────────────
    # SQLite disables FK enforcement by default
    # This enables CASCADE DELETE to actually work
    conn.execute('PRAGMA foreign_keys = ON')

    # ── PRAGMA 2: WAL Mode ─────────────────────────
    # Write-Ahead Logging = faster reads & writes
    # Multiple readers can read while one writes
    conn.execute('PRAGMA journal_mode = WAL')

    return conn'''

    new_conn = '''def get_connection():
    conn = pymysql.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        user=os.getenv('DB_USER', 'root'),
        password=os.getenv('DB_PASSWORD', ''),
        database=os.getenv('DB_NAME', 'expense_db'),
        cursorclass=pymysql.cursors.DictCursor
    )
    return conn'''

    content = content.replace(old_conn, new_conn)

    # 3. SQL Syntax Fixes
    content = content.replace('AUTOINCREMENT', 'AUTO_INCREMENT')
    content = content.replace("datetime('now')", 'CURRENT_TIMESTAMP')
    content = content.replace('INSERT OR IGNORE', 'INSERT IGNORE')
    content = content.replace("CHECK(date LIKE '____-__-__')", '')
    
    # Types that need to be VARCHAR for UNIQUE/INDEX in MySQL
    content = content.replace('name TEXT    NOT NULL UNIQUE', 'name VARCHAR(255) NOT NULL UNIQUE')
    content = content.replace('username TEXT    NOT NULL UNIQUE', 'username VARCHAR(255) NOT NULL UNIQUE')
    content = content.replace('email    TEXT    NOT NULL UNIQUE', 'email VARCHAR(255) NOT NULL UNIQUE')
    
    # Views syntax fixes (strftime)
    content = content.replace("strftime('%Y', e.date)", "DATE_FORMAT(e.date, '%Y')")
    content = content.replace("strftime('%m', e.date)", "DATE_FORMAT(e.date, '%m')")
    content = content.replace("strftime('%Y-%m', e.date)", "DATE_FORMAT(e.date, '%Y-%m')")
    
    # Replace ? with %s
    content = content.replace('(?)', '(%s)')
    content = content.replace('name = ?', 'name = %s')
    
    # Remove DATABASE = 'expense.db'
    content = content.replace("DATABASE = 'expense.db'\n", "")
    
    with open('database.py', 'w', encoding='utf-8') as f:
        f.write(content)

def migrate_app_py():
    with open('app.py', 'r', encoding='utf-8') as f:
        content = f.read()
        
    # Find all ? and replace with %s
    # Wait, we only want to replace ? in SQL strings.
    # Fortunately, there are no generic ? used in python (python doesn't use ? for ternary like JS)
    # The only ? in app.py are for SQL placeholders.
    content = content.replace('?', '%s')
    
    # Also replace strftime with DATE_FORMAT in app.py's queries
    content = content.replace("strftime('%m', date) = %s", "DATE_FORMAT(date, '%m') = %s")
    content = content.replace("strftime('%Y', date) = %s", "DATE_FORMAT(date, '%Y') = %s")
    
    # IntegrityError is pymysql.err.IntegrityError
    if 'sqlite3' in content:
        content = content.replace('import sqlite3', 'import pymysql')
        content = content.replace('sqlite3.IntegrityError', 'pymysql.err.IntegrityError')
        
    with open('app.py', 'w', encoding='utf-8') as f:
        f.write(content)

def migrate_ai_helper_py():
    with open('ai_helper.py', 'r', encoding='utf-8') as f:
        content = f.read()
        
    content = content.replace('?', '%s')
    
    with open('ai_helper.py', 'w', encoding='utf-8') as f:
        f.write(content)

if __name__ == '__main__':
    migrate_database_py()
    migrate_app_py()
    migrate_ai_helper_py()
    print("Migration scripts completed.")
