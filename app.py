from ai_helper import (
    get_spending_analysis,
    get_category_advice,
    get_budget_recommendations,
    get_savings_tip,
    get_chat_response,
    build_spending_context,
    get_goal_advice          # ← ADD THIS
)

import uuid


from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response
from werkzeug.security import generate_password_hash, check_password_hash
from database import get_connection, create_tables, get_category_id, get_all_categories
from datetime import datetime, date
import os
import time

app = Flask(__name__)
app.secret_key = 'expense_analyser_secret_key_2026'
create_tables()

# Simple in-memory throttling to reduce Gemini rate-limit hits.
# (Resets on server restart; good enough for a student project.)
_last_ai_call_by_user = {}


def _throttle_ai(user_id, min_interval_s=2):
    """
    Best-effort per-user throttling across ALL AI endpoints.
    Prevents accidental bursts (multiple button clicks / parallel requests).
    """
    if not user_id:
        return None
    now_ts = time.time()
    last_ts = _last_ai_call_by_user.get(user_id, 0)
    if now_ts - last_ts < min_interval_s:
        wait_s = int(min_interval_s - (now_ts - last_ts) + 0.999)
        return f"Please wait {wait_s}s before using AI again (avoids rate limits)."
    _last_ai_call_by_user[user_id] = now_ts
    return None


# ════════════════════════════════════════════
#   CONTEXT PROCESSOR — alert badges
# ════════════════════════════════════════════
@app.context_processor
def inject_alerts():
    if 'user_id' not in session:
        return dict(alert_count=0, warning_count=0, unread_tips=0)

    now = datetime.now()
    conn   = get_connection()
    cursor = conn.cursor()

    # ── Alert counts ───────────────────────────────
    try:
        cursor.execute('''
            SELECT category, total_spent AS spent
            FROM v_monthly_summary
            WHERE user_id = ?
            AND month = ? AND year = ?
        ''', (session['user_id'], str(now.month).zfill(2), str(now.year)))
        spent_rows = cursor.fetchall()

        cursor.execute('''
            SELECT category, amount AS budget
            FROM v_budgets_full
            WHERE user_id = ?
            AND month = ? AND year = ?
        ''', (session['user_id'], now.month, now.year))
        budget_rows = cursor.fetchall()

        budget_map    = {r['category']: r['budget'] for r in budget_rows}
        alert_count   = 0
        warning_count = 0

        for row in spent_rows:
            budget = budget_map.get(row['category'], 0)
            if budget > 0:
                pct = (row['spent'] / budget) * 100
                if pct >= 100:
                    alert_count += 1
                elif pct >= 80:
                    warning_count += 1
    except:
        alert_count   = 0
        warning_count = 0

    # ── Unread saved tips count ────────────────────
    try:
        cursor.execute('''
            SELECT COUNT(*) AS cnt FROM saved_tips
            WHERE user_id = ? AND is_read = 0
        ''', (session['user_id'],))
        unread_tips = cursor.fetchone()['cnt']
    except:
        unread_tips = 0

    conn.close()

    return dict(
        alert_count   = alert_count,
        warning_count = warning_count,
        unread_tips   = unread_tips
    )
# ════════════════════════════════════════════
#   AUTO ADD RECURRING
# ════════════════════════════════════════════
def auto_add_recurring(user_id):
    import calendar
    now           = datetime.now()
    current_month = now.month
    current_year  = now.year
    month_key     = f"{current_year}-{str(current_month).zfill(2)}"

    conn   = get_connection()
    cursor = conn.cursor()

    # Uses v_recurring_full VIEW (has category_id + category name)
    cursor.execute('''
        SELECT * FROM v_recurring_full
        WHERE user_id = ? AND is_active = 1
    ''', (user_id,))
    recurrings = cursor.fetchall()

    for rec in recurrings:
        if rec['last_added'] == month_key:
            continue

        max_day  = calendar.monthrange(current_year, current_month)[1]
        day      = min(rec['day_of_month'], max_day)
        exp_date = date(current_year, current_month, day).strftime('%Y-%m-%d')

        cursor.execute('''
            INSERT INTO expenses (user_id, category_id, amount, date, note)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            user_id,
            rec['category_id'],
            rec['amount'],
            exp_date,
            f"[Auto] {rec['note']}" if rec['note'] else '[Auto] Recurring'
        ))

        cursor.execute('''
            UPDATE recurring_expenses
            SET last_added = ? WHERE id = ?
        ''', (month_key, rec['id']))

    conn.commit()
    conn.close()


# ════════════════════════════════════════════
#   HOME
# ════════════════════════════════════════════
@app.route('/')
def home():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


# ════════════════════════════════════════════
#   REGISTER
# ════════════════════════════════════════════
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        email    = request.form['email'].strip()
        password = request.form['password'].strip()

        if not username or not email or not password:
            flash('All fields are required!', 'error')
            return redirect(url_for('register'))

        if len(password) < 6:
            flash('Password must be at least 6 characters!', 'error')
            return redirect(url_for('register'))

        hashed_password = generate_password_hash(password)
        conn   = get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute('''
                INSERT INTO users (username, email, password)
                VALUES (?, ?, ?)
            ''', (username, email, hashed_password))
            conn.commit()
            flash('Account created! Please login.', 'success')
            return redirect(url_for('login'))
        except Exception:
            flash('Username or Email already exists!', 'error')
            return redirect(url_for('register'))
        finally:
            conn.close()

    return render_template('register.html')


# ════════════════════════════════════════════
#   LOGIN
# ════════════════════════════════════════════
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()

        if not username or not password:
            flash('Please enter username and password!', 'error')
            return redirect(url_for('login'))

        conn   = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
        user = cursor.fetchone()
        conn.close()

        if user is None:
            flash('Username not found!', 'error')
            return redirect(url_for('login'))

        if not check_password_hash(user['password'], password):
            flash('Wrong password!', 'error')
            return redirect(url_for('login'))

        session['user_id']  = user['id']
        session['username'] = user['username']
        flash(f"Welcome back, {user['username']}! 👋", 'success')
        return redirect(url_for('dashboard'))

    return render_template('login.html')


# ════════════════════════════════════════════
#   LOGOUT
# ════════════════════════════════════════════
@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully!', 'success')
    return redirect(url_for('login'))


# ════════════════════════════════════════════
#   DASHBOARD
# ════════════════════════════════════════════
@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    auto_add_recurring(session['user_id'])

    now           = datetime.now()
    current_month = now.month
    current_year  = now.year

    conn   = get_connection()
    cursor = conn.cursor()

    # 1. Total spent — via VIEW
    cursor.execute('''
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM v_expenses_full
        WHERE user_id = ?
        AND strftime('%m', date) = ?
        AND strftime('%Y', date) = ?
    ''', (session['user_id'], str(current_month).zfill(2), str(current_year)))
    total_spent_this_month = cursor.fetchone()['total']

    # 2. Total budget — via VIEW
    cursor.execute('''
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM v_budgets_full
        WHERE user_id = ? AND month = ? AND year = ?
    ''', (session['user_id'], current_month, current_year))
    total_budget_this_month = cursor.fetchone()['total']

    # 3. Category spent — via v_monthly_summary VIEW
    cursor.execute('''
        SELECT category, total_spent AS spent, txn_count
        FROM v_monthly_summary
        WHERE user_id = ?
        AND month = ? AND year = ?
    ''', (session['user_id'], str(current_month).zfill(2), str(current_year)))
    category_spent_rows = cursor.fetchall()

    # 4. Category budgets — via VIEW
    cursor.execute('''
        SELECT category, amount AS budget
        FROM v_budgets_full
        WHERE user_id = ? AND month = ? AND year = ?
    ''', (session['user_id'], current_month, current_year))
    category_budget_rows = cursor.fetchall()

    # 5. Monthly trend — via v_monthly_summary VIEW
    cursor.execute('''
        SELECT month_year, SUM(total_spent) AS total
        FROM v_monthly_summary
        WHERE user_id = ?
        GROUP BY month_year
        ORDER BY month_year DESC
        LIMIT 6
    ''', (session['user_id'],))
    monthly_trend_rows = cursor.fetchall()

    # 6. Recent expenses — direct JOIN query
    cursor.execute('''
        SELECT e.id, e.amount, e.date, e.note,
               c.name AS category
        FROM expenses e
        JOIN categories c ON e.category_id = c.id
        WHERE e.user_id = ?
        ORDER BY e.date DESC
        LIMIT 5
    ''', (session['user_id'],))
    recent_expenses = cursor.fetchall()

    # 7. Total count
    cursor.execute('''
        SELECT COUNT(*) AS count FROM expenses WHERE user_id = ?
    ''', (session['user_id'],))
    total_expense_count = cursor.fetchone()['count']

    conn.close()

    # Build category comparison dict
    category_data = {}
    for row in category_budget_rows:
        category_data[row['category']] = {'budget': row['budget'], 'spent': 0}
    for row in category_spent_rows:
        if row['category'] in category_data:
            category_data[row['category']]['spent'] = row['spent']
        else:
            category_data[row['category']] = {'budget': 0, 'spent': row['spent']}

    alerts = []
    for cat, data in category_data.items():
        if data['budget'] > 0 and data['spent'] > data['budget']:
            alerts.append({
                'category': cat,
                'over_by' : data['spent'] - data['budget'],
                'budget'  : data['budget'],
                'spent'   : data['spent']
            })

    monthly_trend = [
        {'month_year': row['month_year'], 'total': row['total']}
        for row in reversed(monthly_trend_rows)
    ]

    remaining     = total_budget_this_month - total_spent_this_month
    spent_percent = round(
        (total_spent_this_month / total_budget_this_month * 100), 1
    ) if total_budget_this_month > 0 else 0

    return render_template('dashboard.html',
        username            = session['username'],
        current_month       = current_month,
        current_year        = current_year,
        total_spent         = total_spent_this_month,
        total_budget        = total_budget_this_month,
        remaining           = remaining,
        spent_percent       = spent_percent,
        total_expense_count = total_expense_count,
        category_data       = category_data,
        monthly_trend       = monthly_trend,
        recent_expenses     = recent_expenses,
        alerts              = alerts
    )


# ════════════════════════════════════════════
#   BUDGETS — View
# ════════════════════════════════════════════
@app.route('/budgets')
def budgets():
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    conn   = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM v_budgets_full
        WHERE user_id = ?
        ORDER BY year DESC, month DESC
    ''', (session['user_id'],))
    budgets = cursor.fetchall()
    conn.close()

    now = datetime.now()
    return render_template('budgets.html',
        budgets       = budgets,
        current_month = now.month,
        current_year  = now.year
    )


# ════════════════════════════════════════════
#   BUDGETS — Add
# ════════════════════════════════════════════
@app.route('/budgets/add', methods=['POST'])
def add_budget():
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    category = request.form['category'].strip()
    amount   = request.form['amount'].strip()
    month    = request.form['month'].strip()
    year     = request.form['year'].strip()

    if not category or not amount or not month or not year:
        flash('All fields are required!', 'error')
        return redirect(url_for('budgets'))

    try:
        amount = float(amount)
        month  = int(month)
        year   = int(year)
        if amount <= 0:
            flash('Amount must be greater than 0!', 'error')
            return redirect(url_for('budgets'))
    except ValueError:
        flash('Invalid values!', 'error')
        return redirect(url_for('budgets'))

    conn   = get_connection()
    cursor = conn.cursor()

    category_id = get_category_id(cursor, category)
    if not category_id:
        flash('Invalid category!', 'error')
        conn.close()
        return redirect(url_for('budgets'))

    cursor.execute('''
        SELECT id FROM budgets
        WHERE user_id=? AND category_id=? AND month=? AND year=?
    ''', (session['user_id'], category_id, month, year))

    if cursor.fetchone():
        flash(f'Budget for {category} this month already exists!', 'error')
        conn.close()
        return redirect(url_for('budgets'))

    try:
        cursor.execute('''
            INSERT INTO budgets (user_id, category_id, amount, month, year)
            VALUES (?, ?, ?, ?, ?)
        ''', (session['user_id'], category_id, amount, month, year))
        conn.commit()
        flash(f'✅ Budget for {category} set!', 'success')
    except Exception as e:
        flash(f'Error: {str(e)}', 'error')
    finally:
        conn.close()

    return redirect(url_for('budgets'))


# ════════════════════════════════════════════
#   BUDGETS — Edit
# ════════════════════════════════════════════
@app.route('/budgets/edit/<int:budget_id>')
def edit_budget(budget_id):
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    conn   = get_connection()
    cursor = conn.cursor()

    # Use VIEW to get budget with category name
    cursor.execute('''
        SELECT * FROM v_budgets_full
        WHERE id = ? AND user_id = ?
    ''', (budget_id, session['user_id']))
    budget = cursor.fetchone()

    if budget is None:
        flash('Budget not found!', 'error')
        conn.close()
        return redirect(url_for('budgets'))

    cursor.execute('''
        SELECT * FROM v_budgets_full
        WHERE user_id = ?
        ORDER BY year DESC, month DESC
    ''', (session['user_id'],))
    all_budgets = cursor.fetchall()
    conn.close()

    now = datetime.now()
    return render_template('budgets.html',
        budgets       = all_budgets,
        edit_budget   = budget,
        current_month = now.month,
        current_year  = now.year
    )


# ════════════════════════════════════════════
#   BUDGETS — Update
# ════════════════════════════════════════════
@app.route('/budgets/update/<int:budget_id>', methods=['POST'])
def update_budget(budget_id):
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    category = request.form['category'].strip()
    amount   = request.form['amount'].strip()
    month    = request.form['month'].strip()
    year     = request.form['year'].strip()

    try:
        amount = float(amount)
        month  = int(month)
        year   = int(year)
        if amount <= 0:
            flash('Amount must be greater than 0!', 'error')
            return redirect(url_for('budgets'))
    except ValueError:
        flash('Invalid values!', 'error')
        return redirect(url_for('budgets'))

    conn   = get_connection()
    cursor = conn.cursor()

    # Get category_id from name
    category_id = get_category_id(cursor, category)
    if not category_id:
        flash('Invalid category!', 'error')
        conn.close()
        return redirect(url_for('budgets'))

    cursor.execute('''
        UPDATE budgets
        SET category_id = ?, amount = ?, month = ?, year = ?
        WHERE id = ? AND user_id = ?
    ''', (category_id, amount, month, year, budget_id, session['user_id']))
    conn.commit()
    conn.close()

    flash('✅ Budget updated!', 'success')
    return redirect(url_for('budgets'))


# ════════════════════════════════════════════
#   BUDGETS — Delete
# ════════════════════════════════════════════
@app.route('/budgets/delete/<int:budget_id>')
def delete_budget(budget_id):
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    conn   = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        DELETE FROM budgets WHERE id = ? AND user_id = ?
    ''', (budget_id, session['user_id']))
    conn.commit()
    conn.close()

    flash('🗑️ Budget deleted!', 'success')
    return redirect(url_for('budgets'))


# ════════════════════════════════════════════
#   EXPENSES — View
# ════════════════════════════════════════════
@app.route('/expenses')
def expenses():
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    auto_add_recurring(session['user_id'])

    now = datetime.now()

    filter_category = request.args.get('category', '')
    filter_month    = request.args.get('month', '')
    filter_year     = request.args.get('year', str(now.year))

    conn   = get_connection()
    cursor = conn.cursor()

    # Uses v_expenses_full VIEW — JOIN already done
    query  = 'SELECT * FROM v_expenses_full WHERE user_id = ?'
    params = [session['user_id']]

    if filter_category:
        query  += ' AND category = ?'
        params.append(filter_category)
    if filter_month:
        query  += " AND strftime('%m', date) = ?"
        params.append(filter_month.zfill(2))
    if filter_year:
        query  += " AND strftime('%Y', date) = ?"
        params.append(filter_year)

    query += ' ORDER BY date DESC'

    cursor.execute(query, params)
    all_expenses = cursor.fetchall()
    total_spent  = sum(e['amount'] for e in all_expenses)
    conn.close()

    return render_template('expenses.html',
        expenses        = all_expenses,
        total_spent     = total_spent,
        filter_category = filter_category,
        filter_month    = int(filter_month) if filter_month else '',
        filter_year     = int(filter_year)  if filter_year  else now.year,
        current_month   = now.month,
        current_year    = now.year
    )


# ════════════════════════════════════════════
#   EXPENSES — Add
# ════════════════════════════════════════════
@app.route('/expenses/add', methods=['POST'])
def add_expense():
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    category = request.form['category'].strip()
    amount   = request.form['amount'].strip()
    exp_date = request.form['date'].strip()
    note     = request.form['note'].strip()

    if not category or not amount or not exp_date:
        flash('Category, Amount and Date are required!', 'error')
        return redirect(url_for('expenses'))

    try:
        amount = float(amount)
        if amount <= 0:
            flash('Amount must be greater than 0!', 'error')
            return redirect(url_for('expenses'))
    except ValueError:
        flash('Invalid amount!', 'error')
        return redirect(url_for('expenses'))

    conn   = get_connection()
    cursor = conn.cursor()

    category_id = get_category_id(cursor, category)
    if not category_id:
        flash('Invalid category!', 'error')
        conn.close()
        return redirect(url_for('expenses'))

    try:
        cursor.execute('''
            INSERT INTO expenses (user_id, category_id, amount, date, note)
            VALUES (?, ?, ?, ?, ?)
        ''', (session['user_id'], category_id, amount, exp_date, note))
        conn.commit()
        flash(f'✅ Expense of ₹{amount:.2f} added!', 'success')
    except Exception as e:
        flash(f'Error: {str(e)}', 'error')
    finally:
        conn.close()

    return redirect(url_for('expenses'))


# ════════════════════════════════════════════
#   EXPENSES — Edit
# ════════════════════════════════════════════
@app.route('/expenses/edit/<int:expense_id>')
def edit_expense(expense_id):
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    now = datetime.now()
    conn   = get_connection()
    cursor = conn.cursor()

    # Use VIEW — gets category name too
    cursor.execute('''
        SELECT * FROM v_expenses_full
        WHERE id = ? AND user_id = ?
    ''', (expense_id, session['user_id']))
    expense = cursor.fetchone()

    if expense is None:
        flash('Expense not found!', 'error')
        conn.close()
        return redirect(url_for('expenses'))

    cursor.execute('''
        SELECT * FROM v_expenses_full
        WHERE user_id = ? ORDER BY date DESC
    ''', (session['user_id'],))
    all_expenses = cursor.fetchall()
    total_spent  = sum(e['amount'] for e in all_expenses)
    conn.close()

    return render_template('expenses.html',
        expenses        = all_expenses,
        total_spent     = total_spent,
        edit_expense    = expense,
        filter_category = '',
        filter_month    = '',
        filter_year     = now.year,
        current_month   = now.month,
        current_year    = now.year
    )


# ════════════════════════════════════════════
#   EXPENSES — Update
# ════════════════════════════════════════════
@app.route('/expenses/update/<int:expense_id>', methods=['POST'])
def update_expense(expense_id):
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    category = request.form['category'].strip()
    amount   = request.form['amount'].strip()
    exp_date = request.form['date'].strip()
    note     = request.form['note'].strip()

    try:
        amount = float(amount)
        if amount <= 0:
            flash('Amount must be greater than 0!', 'error')
            return redirect(url_for('expenses'))
    except ValueError:
        flash('Invalid amount!', 'error')
        return redirect(url_for('expenses'))

    conn   = get_connection()
    cursor = conn.cursor()

    # Get category_id from name
    category_id = get_category_id(cursor, category)
    if not category_id:
        flash('Invalid category!', 'error')
        conn.close()
        return redirect(url_for('expenses'))

    cursor.execute('''
        UPDATE expenses
        SET category_id = ?, amount = ?, date = ?, note = ?
        WHERE id = ? AND user_id = ?
    ''', (category_id, amount, exp_date, note, expense_id, session['user_id']))
    conn.commit()
    conn.close()

    flash('✅ Expense updated!', 'success')
    return redirect(url_for('expenses'))


# ════════════════════════════════════════════
#   EXPENSES — Delete
# ════════════════════════════════════════════
@app.route('/expenses/delete/<int:expense_id>')
def delete_expense(expense_id):
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    conn   = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        DELETE FROM expenses WHERE id = ? AND user_id = ?
    ''', (expense_id, session['user_id']))
    conn.commit()
    conn.close()

    flash('🗑️ Expense deleted!', 'success')
    return redirect(url_for('expenses'))


# ════════════════════════════════════════════
#   RECURRING — View
# ════════════════════════════════════════════
@app.route('/recurring')
def recurring():
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    conn   = get_connection()
    cursor = conn.cursor()

    # Use v_recurring_full VIEW — has category name
    cursor.execute('''
        SELECT * FROM v_recurring_full
        WHERE user_id = ?
        ORDER BY id DESC
    ''', (session['user_id'],))
    recurrings = cursor.fetchall()
    conn.close()

    now = datetime.now()
    return render_template('recurring.html',
        recurrings  = recurrings,
        current_day = now.day
    )


# ════════════════════════════════════════════
#   RECURRING — Add
# ════════════════════════════════════════════
@app.route('/recurring/add', methods=['POST'])
def add_recurring():
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    category     = request.form['category'].strip()
    amount       = request.form['amount'].strip()
    note         = request.form['note'].strip()
    day_of_month = request.form['day_of_month'].strip()

    if not category or not amount or not day_of_month:
        flash('Category, Amount and Day are required!', 'error')
        return redirect(url_for('recurring'))

    try:
        amount       = float(amount)
        day_of_month = int(day_of_month)
        if amount <= 0:
            flash('Amount must be greater than 0!', 'error')
            return redirect(url_for('recurring'))
        if not (1 <= day_of_month <= 31):
            flash('Day must be between 1 and 31!', 'error')
            return redirect(url_for('recurring'))
    except ValueError:
        flash('Invalid values!', 'error')
        return redirect(url_for('recurring'))

    conn   = get_connection()
    cursor = conn.cursor()

    # Get category_id — normalization
    category_id = get_category_id(cursor, category)
    if not category_id:
        flash('Invalid category!', 'error')
        conn.close()
        return redirect(url_for('recurring'))

    cursor.execute('''
        INSERT INTO recurring_expenses
            (user_id, category_id, amount, note, day_of_month, is_active)
        VALUES (?, ?, ?, ?, ?, 1)
    ''', (session['user_id'], category_id, amount, note, day_of_month))
    conn.commit()
    conn.close()

    flash(f'✅ Recurring added! Auto-adds on day {day_of_month} every month.', 'success')
    return redirect(url_for('recurring'))


# ════════════════════════════════════════════
#   RECURRING — Edit
# ════════════════════════════════════════════
@app.route('/recurring/edit/<int:rec_id>')
def edit_recurring(rec_id):
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    conn   = get_connection()
    cursor = conn.cursor()

    # Use VIEW to get with category name
    cursor.execute('''
        SELECT * FROM v_recurring_full
        WHERE id = ? AND user_id = ?
    ''', (rec_id, session['user_id']))
    edit_rec = cursor.fetchone()

    if edit_rec is None:
        flash('Recurring expense not found!', 'error')
        conn.close()
        return redirect(url_for('recurring'))

    cursor.execute('''
        SELECT * FROM v_recurring_full
        WHERE user_id = ? ORDER BY id DESC
    ''', (session['user_id'],))
    recurrings = cursor.fetchall()
    conn.close()

    now = datetime.now()
    return render_template('recurring.html',
        recurrings  = recurrings,
        edit_rec    = edit_rec,
        current_day = now.day
    )


# ════════════════════════════════════════════
#   RECURRING — Update
# ════════════════════════════════════════════
@app.route('/recurring/update/<int:rec_id>', methods=['POST'])
def update_recurring(rec_id):
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    category     = request.form['category'].strip()
    amount       = request.form['amount'].strip()
    note         = request.form['note'].strip()
    day_of_month = request.form['day_of_month'].strip()

    try:
        amount       = float(amount)
        day_of_month = int(day_of_month)
        if amount <= 0:
            flash('Amount must be greater than 0!', 'error')
            return redirect(url_for('recurring'))
        if not (1 <= day_of_month <= 31):
            flash('Day must be between 1 and 31!', 'error')
            return redirect(url_for('recurring'))
    except ValueError:
        flash('Invalid values!', 'error')
        return redirect(url_for('recurring'))

    conn   = get_connection()
    cursor = conn.cursor()

    # Get category_id from name
    category_id = get_category_id(cursor, category)
    if not category_id:
        flash('Invalid category!', 'error')
        conn.close()
        return redirect(url_for('recurring'))

    # Reset last_added so re-adds this month with new values
    cursor.execute('''
        UPDATE recurring_expenses
        SET category_id  = ?,
            amount       = ?,
            note         = ?,
            day_of_month = ?,
            last_added   = NULL
        WHERE id = ? AND user_id = ?
    ''', (category_id, amount, note, day_of_month, rec_id, session['user_id']))
    conn.commit()
    conn.close()

    flash('✅ Recurring expense updated!', 'success')
    return redirect(url_for('recurring'))


# ════════════════════════════════════════════
#   RECURRING — Toggle
# ════════════════════════════════════════════
@app.route('/recurring/toggle/<int:rec_id>')
def toggle_recurring(rec_id):
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    conn   = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE recurring_expenses
        SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END
        WHERE id = ? AND user_id = ?
    ''', (rec_id, session['user_id']))
    conn.commit()
    conn.close()

    flash('✅ Status updated!', 'success')
    return redirect(url_for('recurring'))


# ════════════════════════════════════════════
#   RECURRING — Delete
# ════════════════════════════════════════════
@app.route('/recurring/delete/<int:rec_id>')
def delete_recurring(rec_id):
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    conn   = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        DELETE FROM recurring_expenses
        WHERE id = ? AND user_id = ?
    ''', (rec_id, session['user_id']))
    conn.commit()
    conn.close()

    flash('🗑️ Recurring deleted!', 'success')
    return redirect(url_for('recurring'))


# ════════════════════════════════════════════
#   SEARCH — Live search using VIEW
# ════════════════════════════════════════════
@app.route('/search')
def search():
    if 'user_id' not in session:
        return jsonify([])

    query = request.args.get('q', '').strip()
    if len(query) < 1:
        return jsonify([])

    conn   = get_connection()
    cursor = conn.cursor()

    # Uses v_expenses_full VIEW — category name available
    cursor.execute('''
        SELECT id, category, amount, date, note
        FROM v_expenses_full
        WHERE user_id = ?
        AND (
            category LIKE ?
            OR note   LIKE ?
            OR CAST(amount AS TEXT) LIKE ?
            OR date   LIKE ?
        )
        ORDER BY date DESC
        LIMIT 10
    ''', (
        session['user_id'],
        f'%{query}%', f'%{query}%',
        f'%{query}%', f'%{query}%'
    ))

    rows = cursor.fetchall()
    conn.close()

    return jsonify([
        {
            'id'      : row['id'],
            'category': row['category'],
            'amount'  : row['amount'],
            'date'    : row['date'],
            'note'    : row['note'] if row['note'] else ''
        }
        for row in rows
    ])


# ════════════════════════════════════════════
#   ALERTS
# ════════════════════════════════════════════
@app.route('/alerts')
def alerts():
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    now           = datetime.now()
    current_month = now.month
    current_year  = now.year

    conn   = get_connection()
    cursor = conn.cursor()

    # v_monthly_summary VIEW has JOIN + GROUP BY inside
    cursor.execute('''
        SELECT category, total_spent AS spent, txn_count
        FROM v_monthly_summary
        WHERE user_id = ?
        AND month = ? AND year = ?
    ''', (session['user_id'], str(current_month).zfill(2), str(current_year)))
    spent_rows = cursor.fetchall()

    # v_budgets_full VIEW has JOIN inside
    cursor.execute('''
        SELECT category, amount AS budget
        FROM v_budgets_full
        WHERE user_id = ? AND month = ? AND year = ?
    ''', (session['user_id'], current_month, current_year))
    budget_rows = cursor.fetchall()
    conn.close()

    budget_map     = {r['category']: r['budget'] for r in budget_rows}
    danger_alerts  = []
    warning_alerts = []
    safe_list      = []

    for row in spent_rows:
        cat    = row['category']
        spent  = row['spent']
        budget = budget_map.get(cat, 0)
        item   = {
            'category' : cat,
            'spent'    : spent,
            'budget'   : budget,
            'txn_count': row['txn_count'],
            'percent'  : round((spent / budget * 100), 1) if budget > 0 else 0,
            'over_by'  : max(0, spent - budget),
            'remaining': max(0, budget - spent),
        }
        if budget <= 0:
            item['percent'] = 0
            safe_list.append(item)
        elif item['percent'] >= 100:
            danger_alerts.append(item)
        elif item['percent'] >= 80:
            warning_alerts.append(item)
        else:
            safe_list.append(item)

    spent_cats = {r['category'] for r in spent_rows}
    for r in budget_rows:
        if r['category'] not in spent_cats:
            safe_list.append({
                'category' : r['category'],
                'spent'    : 0,
                'budget'   : r['budget'],
                'txn_count': 0,
                'percent'  : 0,
                'over_by'  : 0,
                'remaining': r['budget'],
            })

    months = ['','January','February','March','April','May','June',
              'July','August','September','October','November','December']

    return render_template('alerts.html',
        danger_alerts  = danger_alerts,
        warning_alerts = warning_alerts,
        safe_list      = safe_list,
        current_month  = months[current_month],
        current_year   = current_year
    )


# ════════════════════════════════════════════
#   REPORTS
# ════════════════════════════════════════════
@app.route('/reports')
def reports():
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    now             = datetime.now()
    default_from    = date(now.year, now.month, 1).strftime('%Y-%m-%d')
    default_to      = now.strftime('%Y-%m-%d')
    from_date       = request.args.get('from_date', default_from)
    to_date         = request.args.get('to_date',   default_to)
    filter_category = request.args.get('category',  '')

    conn   = get_connection()
    cursor = conn.cursor()

    # All queries use v_expenses_full VIEW
    query  = '''
        SELECT * FROM v_expenses_full
        WHERE user_id = ? AND date >= ? AND date <= ?
    '''
    params = [session['user_id'], from_date, to_date]
    if filter_category:
        query  += ' AND category = ?'
        params.append(filter_category)
    query += ' ORDER BY date DESC'

    cursor.execute(query, params)
    filtered_expenses = cursor.fetchall()
    total_spent       = sum(e['amount'] for e in filtered_expenses)

    cat_query  = '''
        SELECT category,
               COUNT(*)    AS count,
               SUM(amount) AS total,
               AVG(amount) AS average,
               MAX(amount) AS highest,
               MIN(amount) AS lowest
        FROM v_expenses_full
        WHERE user_id = ? AND date >= ? AND date <= ?
    '''
    cat_params = [session['user_id'], from_date, to_date]
    if filter_category:
        cat_query  += ' AND category = ?'
        cat_params.append(filter_category)
    cat_query += ' GROUP BY category ORDER BY total DESC'

    cursor.execute(cat_query, cat_params)
    category_summary = cursor.fetchall()

    cursor.execute('''
        SELECT date, SUM(amount) AS total
        FROM v_expenses_full
        WHERE user_id = ? AND date >= ? AND date <= ?
        GROUP BY date ORDER BY date ASC
    ''', (session['user_id'], from_date, to_date))
    daily_trend = [
        {'date': r['date'], 'total': r['total']}
        for r in cursor.fetchall()
    ]

    cursor.execute('''
        SELECT DISTINCT
               strftime('%m', date) AS month,
               strftime('%Y', date) AS year
        FROM v_expenses_full
        WHERE user_id = ? AND date >= ? AND date <= ?
    ''', (session['user_id'], from_date, to_date))
    months_in_range = cursor.fetchall()

    total_budget_in_range = 0
    for m in months_in_range:
        cursor.execute('''
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM v_budgets_full
            WHERE user_id = ? AND month = ? AND year = ?
        ''', (session['user_id'], int(m['month']), int(m['year'])))
        total_budget_in_range += cursor.fetchone()['total']

    conn.close()

    remaining     = total_budget_in_range - total_spent
    spent_percent = round(
        (total_spent / total_budget_in_range * 100), 1
    ) if total_budget_in_range > 0 else 0

    return render_template('reports.html',
        from_date         = from_date,
        to_date           = to_date,
        filter_category   = filter_category,
        filtered_expenses = filtered_expenses,
        total_spent       = total_spent,
        total_budget      = total_budget_in_range,
        remaining         = remaining,
        spent_percent     = spent_percent,
        category_summary  = [
            {
                'category': r['category'],
                'count'   : r['count'],
                'total'   : r['total'],
                'average' : round(r['average'], 2),
                'highest' : r['highest'],
                'lowest'  : r['lowest']
            }
            for r in category_summary
        ],
        daily_trend       = daily_trend,
        now_year          = now.year
    )


# ════════════════════════════════════════════
#   EXPORT CSV
# ════════════════════════════════════════════
@app.route('/reports/export')
def export_csv():
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    import csv, io
    now             = datetime.now()
    default_from    = date(now.year, now.month, 1).strftime('%Y-%m-%d')
    default_to      = now.strftime('%Y-%m-%d')
    from_date       = request.args.get('from_date', default_from)
    to_date         = request.args.get('to_date',   default_to)
    filter_category = request.args.get('category',  '')

    conn   = get_connection()
    cursor = conn.cursor()

    query  = '''
        SELECT category, amount, date, note
        FROM v_expenses_full
        WHERE user_id = ? AND date >= ? AND date <= ?
    '''
    params = [session['user_id'], from_date, to_date]
    if filter_category:
        query  += ' AND category = ?'
        params.append(filter_category)
    query += ' ORDER BY date DESC'

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Category', 'Amount (INR)', 'Date', 'Note'])
    total = 0
    for row in rows:
        writer.writerow([
            row['category'],
            f"{row['amount']:.2f}",
            row['date'],
            row['note'] if row['note'] else ''
        ])
        total += row['amount']
    writer.writerow([])
    writer.writerow(['TOTAL', f"{total:.2f}", '', ''])
    writer.writerow(['From', from_date, 'To', to_date])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={
            'Content-Disposition':
            f'attachment; filename=expenses_{from_date}_to_{to_date}.csv'
        }
    )


# ════════════════════════════════════════════
#   PROFILE — View
# ════════════════════════════════════════════
@app.route('/profile')
def profile():
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    conn   = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],))
    user = cursor.fetchone()
    conn.close()
    return render_template('profile.html', user=user)


# ════════════════════════════════════════════
#   PROFILE — Update
# ════════════════════════════════════════════
@app.route('/profile/update', methods=['POST'])
def update_profile():
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    new_username = request.form['username'].strip()
    new_email    = request.form['email'].strip()

    if not new_username or not new_email:
        flash('Username and Email cannot be empty!', 'error')
        return redirect(url_for('profile'))

    conn   = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            UPDATE users SET username = ?, email = ?
            WHERE id = ?
        ''', (new_username, new_email, session['user_id']))
        conn.commit()
        session['username'] = new_username
        flash('✅ Profile updated!', 'success')
    except Exception:
        flash('Username or Email already taken!', 'error')
    finally:
        conn.close()

    return redirect(url_for('profile'))


# ════════════════════════════════════════════
#   PROFILE — Change Password
# ════════════════════════════════════════════
@app.route('/profile/change-password', methods=['POST'])
def change_password():
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    current_password = request.form['current_password'].strip()
    new_password     = request.form['new_password'].strip()
    confirm_password = request.form['confirm_password'].strip()

    if not current_password or not new_password or not confirm_password:
        flash('All fields are required!', 'error')
        return redirect(url_for('profile'))

    if new_password != confirm_password:
        flash('New passwords do not match!', 'error')
        return redirect(url_for('profile'))

    if len(new_password) < 6:
        flash('Password must be at least 6 characters!', 'error')
        return redirect(url_for('profile'))

    conn   = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],))
    user = cursor.fetchone()

    if not check_password_hash(user['password'], current_password):
        flash('Current password is wrong!', 'error')
        conn.close()
        return redirect(url_for('profile'))

    cursor.execute('''
        UPDATE users SET password = ? WHERE id = ?
    ''', (generate_password_hash(new_password), session['user_id']))
    conn.commit()
    conn.close()

    flash('✅ Password changed!', 'success')
    return redirect(url_for('profile'))


# ════════════════════════════════════════════
#   PROFILE — Delete Account
#   CASCADE DELETE handles expenses+budgets
# ════════════════════════════════════════════
@app.route('/profile/delete', methods=['POST'])
def delete_account():
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    conn   = get_connection()
    cursor = conn.cursor()

    # CASCADE DELETE on FK means deleting user
    # automatically removes all expenses, budgets
    # and recurring expenses — no manual deletes needed!
    cursor.execute('DELETE FROM users WHERE id = ?', (session['user_id'],))
    conn.commit()
    conn.close()

    session.clear()
    flash('Your account has been deleted.', 'success')
    return redirect(url_for('register'))




# ════════════════════════════════════════════
#   AI ADVISOR — Main Page
# ════════════════════════════════════════════
@app.route('/ai-advisor')
def ai_advisor():
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    now           = datetime.now()
    current_month = now.month
    current_year  = now.year

    conn   = get_connection()
    cursor = conn.cursor()

    # Get this month's spending via VIEW
    cursor.execute('''
        SELECT category, total_spent AS spent, txn_count
        FROM v_monthly_summary
        WHERE user_id = ?
        AND month = ? AND year = ?
    ''', (session['user_id'],
          str(current_month).zfill(2),
          str(current_year)))
    spent_rows = cursor.fetchall()

    # Get this month's budgets via VIEW
    cursor.execute('''
        SELECT category, amount AS budget
        FROM v_budgets_full
        WHERE user_id = ? AND month = ? AND year = ?
    ''', (session['user_id'], current_month, current_year))
    budget_rows = cursor.fetchall()

    # Get past 3 months average spending for recommendations
    cursor.execute('''
        SELECT category,
               AVG(total_spent) AS avg_spent,
               COUNT(*)         AS months_count
        FROM v_monthly_summary
        WHERE user_id = ?
        GROUP BY category
        ORDER BY avg_spent DESC
    ''', (session['user_id'],))
    history_rows = cursor.fetchall()

    conn.close()

    # Build spending dict for AI
    budget_map    = {r['category']: r['budget'] for r in budget_rows}
    spending_data = {}

    for row in spent_rows:
        cat = row['category']
        spending_data[cat] = {
            'spent'    : row['spent'],
            'budget'   : budget_map.get(cat, 0),
            'txn_count': row['txn_count']
        }

    # Build history list for budget recommendations
    history_data = [
        {
            'category' : row['category'],
            'avg_spent': round(row['avg_spent'], 2)
        }
        for row in history_rows
    ]

    # Month name
    months = ['','January','February','March','April','May','June',
              'July','August','September','October','November','December']
    month_name = months[current_month]

    # Get all categories for deep dive dropdown
    categories_with_data = list(spending_data.keys())

    return render_template('ai_advisor.html',
        spending_data        = spending_data,
        history_data         = history_data,
        month_name           = month_name,
        current_year         = current_year,
        categories_with_data = categories_with_data,
        has_data             = len(spending_data) > 0
    )


# ════════════════════════════════════════════
#   AI ADVISOR — Get Spending Tips (AJAX)
# ════════════════════════════════════════════
@app.route('/ai/spending-tips', methods=['POST'])
def ai_spending_tips():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not logged in'})
    err = _throttle_ai(session.get('user_id'), min_interval_s=2)
    if err:
        return jsonify({'success': False, 'error': err})

    now = datetime.now()
    conn   = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT category, total_spent AS spent, txn_count
        FROM v_monthly_summary
        WHERE user_id = ?
        AND month = ? AND year = ?
    ''', (session['user_id'],
          str(now.month).zfill(2), str(now.year)))
    spent_rows = cursor.fetchall()

    cursor.execute('''
        SELECT category, amount AS budget
        FROM v_budgets_full
        WHERE user_id = ? AND month = ? AND year = ?
    ''', (session['user_id'], now.month, now.year))
    budget_rows = cursor.fetchall()
    conn.close()

    budget_map    = {r['category']: r['budget'] for r in budget_rows}
    spending_data = {
        row['category']: {
            'spent' : row['spent'],
            'budget': budget_map.get(row['category'], 0)
        }
        for row in spent_rows
    }

    result = get_spending_analysis(spending_data)
    return jsonify(result)


# ════════════════════════════════════════════
#   AI ADVISOR — Category Deep Dive (AJAX)
# ════════════════════════════════════════════
@app.route('/ai/category-advice', methods=['POST'])
def ai_category_advice():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not logged in'})
    err = _throttle_ai(session.get('user_id'), min_interval_s=2)
    if err:
        return jsonify({'success': False, 'error': err})

    data     = request.get_json()
    category = data.get('category', '')

    if not category:
        return jsonify({'success': False, 'error': 'No category selected'})

    now = datetime.now()
    months = ['','January','February','March','April','May','June',
              'July','August','September','October','November','December']

    conn   = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT total_spent AS spent, txn_count
        FROM v_monthly_summary
        WHERE user_id = ? AND category = ?
        AND month = ? AND year = ?
    ''', (session['user_id'], category,
          str(now.month).zfill(2), str(now.year)))
    row = cursor.fetchone()

    cursor.execute('''
        SELECT amount AS budget FROM v_budgets_full
        WHERE user_id = ? AND category = ?
        AND month = ? AND year = ?
    ''', (session['user_id'], category, now.month, now.year))
    budget_row = cursor.fetchone()
    conn.close()

    spent     = row['spent']     if row        else 0
    txn_count = row['txn_count'] if row        else 0
    budget    = budget_row['budget'] if budget_row else 0

    result = get_category_advice(
        category   = category,
        spent      = spent,
        budget     = budget,
        txn_count  = txn_count,
        month_name = months[now.month]
    )
    return jsonify(result)


# ════════════════════════════════════════════
#   AI ADVISOR — Budget Recommendations (AJAX)
# ════════════════════════════════════════════
@app.route('/ai/budget-recommendations', methods=['POST'])
def ai_budget_recommendations():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not logged in'})
    err = _throttle_ai(session.get('user_id'), min_interval_s=2)
    if err:
        return jsonify({'success': False, 'error': err})

    now = datetime.now()
    months = ['','January','February','March','April','May','June',
              'July','August','September','October','November','December']

    conn   = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT category, AVG(total_spent) AS avg_spent
        FROM v_monthly_summary
        WHERE user_id = ?
        GROUP BY category
        ORDER BY avg_spent DESC
    ''', (session['user_id'],))
    history_rows = cursor.fetchall()
    conn.close()

    history_data = [
        {'category': r['category'], 'avg_spent': round(r['avg_spent'], 2)}
        for r in history_rows
    ]

    result = get_budget_recommendations(
        history_data  = history_data,
        current_month = months[now.month]
    )
    return jsonify(result)


# ════════════════════════════════════════════
#   AI ADVISOR — Savings Tip of the Day (AJAX)
# ════════════════════════════════════════════
@app.route('/ai/savings-tip', methods=['POST'])
def ai_savings_tip():
    if 'user_id' not in session:
        return jsonify({'success': False, 'tip': ''})
    err = _throttle_ai(session.get('user_id'), min_interval_s=2)
    if err:
        return jsonify({'success': False, 'tip': err})

    result = get_savings_tip()
    return jsonify(result)


# ════════════════════════════════════════════
#   AI CHAT — Main Chat Page
# ════════════════════════════════════════════
@app.route('/ai-chat')
def ai_chat():
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    conn   = get_connection()
    cursor = conn.cursor()

    # Get all past chat sessions for this user
    # Each session is a separate conversation
    cursor.execute('''
        SELECT session_id,
               MIN(created_at) AS started_at,
               COUNT(*)        AS message_count,
               MIN(CASE WHEN role='user' THEN message END) AS first_message
        FROM ai_chat_history
        WHERE user_id = ?
        GROUP BY session_id
        ORDER BY started_at DESC
        LIMIT 10
    ''', (session['user_id'],))
    past_sessions = cursor.fetchall()

    # Get current session from URL or create new
    current_session_id = request.args.get('session_id', '')

    current_messages = []
    if current_session_id:
        cursor.execute('''
            SELECT role, message, created_at
            FROM ai_chat_history
            WHERE user_id = ? AND session_id = ?
            ORDER BY created_at ASC
        ''', (session['user_id'], current_session_id))
        current_messages = cursor.fetchall()

    conn.close()

    return render_template('ai_chat.html',
        past_sessions      = past_sessions,
        current_session_id = current_session_id,
        current_messages   = current_messages
    )


# ════════════════════════════════════════════
#   AI CHAT — Start New Session
# ════════════════════════════════════════════
@app.route('/ai-chat/new')
def ai_chat_new():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    # Generate unique session ID
    new_session_id = str(uuid.uuid4())[:8]
    return redirect(url_for('ai_chat', session_id=new_session_id))


# ════════════════════════════════════════════
#   AI CHAT — Send Message (AJAX)
# ════════════════════════════════════════════
@app.route('/ai-chat/send', methods=['POST'])
def ai_chat_send():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not logged in'})

    user_id = session.get('user_id')


    data       = request.get_json()
    user_msg   = data.get('message', '').strip()
    session_id = data.get('session_id', '')

    if not user_msg:
        return jsonify({'success': False, 'error': 'Empty message'})

    if not session_id:
        session_id = str(uuid.uuid4())[:8]

    conn   = get_connection()
    cursor = conn.cursor()

    # ── Save user message to DB ────────────────────
    cursor.execute('''
        INSERT INTO ai_chat_history
            (user_id, session_id, role, message)
        VALUES (?, ?, 'user', ?)
    ''', (session['user_id'], session_id, user_msg))

    # ── Get chat history for context ──────────────
    cursor.execute('''
        SELECT role, message FROM ai_chat_history
        WHERE user_id = ? AND session_id = ?
        ORDER BY created_at ASC
    ''', (session['user_id'], session_id))
    history = [dict(row) for row in cursor.fetchall()]

    # ── Build spending context from DB ─────────────
    # This gives AI knowledge of user's real finances
    spending_context = build_spending_context(
        session['user_id'], conn
    )

    conn.commit()

    # ── Call Gemini AI ─────────────────────────────
    result = get_chat_response(
        user_message     = user_msg,
        spending_context = spending_context,
        chat_history     = history[:-1]  # exclude current message
    )

    if not result.get('success'):
        conn.commit()
        conn.close()
        return jsonify({
            'success': False,
            'error': result.get('error', 'AI request failed. Please try again.')
        })

    ai_response = result['response']

    # ── Save AI response to DB ─────────────────────
    cursor.execute('''
        INSERT INTO ai_chat_history
            (user_id, session_id, role, message)
        VALUES (?, ?, 'assistant', ?)
    ''', (session['user_id'], session_id, ai_response))

    conn.commit()
    conn.close()

    return jsonify({
        'success'   : True,
        'response'  : ai_response,
        'session_id': session_id
    })


# ════════════════════════════════════════════
#   AI CHAT — Delete Session
# ════════════════════════════════════════════
@app.route('/ai-chat/delete/<session_id>')
def ai_chat_delete(session_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn   = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        DELETE FROM ai_chat_history
        WHERE user_id = ? AND session_id = ?
    ''', (session['user_id'], session_id))

    conn.commit()
    conn.close()

    flash('🗑️ Conversation deleted!', 'success')
    return redirect(url_for('ai_chat'))

# ════════════════════════════════════════════
#   SAVED TIPS — View All
# ════════════════════════════════════════════
@app.route('/saved-tips')
def saved_tips():
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    conn   = get_connection()
    cursor = conn.cursor()

    # Mark all as read when page is opened
    cursor.execute('''
        UPDATE saved_tips SET is_read = 1
        WHERE user_id = ? AND is_read = 0
    ''', (session['user_id'],))
    conn.commit()

    # Fetch all saved tips newest first
    cursor.execute('''
        SELECT * FROM saved_tips
        WHERE user_id = ?
        ORDER BY created_at DESC
    ''', (session['user_id'],))
    tips = cursor.fetchall()
    conn.close()

    return render_template('saved_tips.html', tips=tips)


# ════════════════════════════════════════════
#   SAVED TIPS — Save a Tip (AJAX)
# ════════════════════════════════════════════
@app.route('/saved-tips/save', methods=['POST'])
def save_tip():
    if 'user_id' not in session:
        return jsonify({'success': False})

    data     = request.get_json()
    tip_text = data.get('tip_text', '').strip()
    source   = data.get('source', 'advisor')
    category = data.get('category', 'General')

    if not tip_text:
        return jsonify({'success': False, 'error': 'Empty tip'})

    conn   = get_connection()
    cursor = conn.cursor()

    # Check if already saved (avoid duplicates)
    cursor.execute('''
        SELECT id FROM saved_tips
        WHERE user_id = ? AND tip_text = ?
    ''', (session['user_id'], tip_text))

    if cursor.fetchone():
        conn.close()
        return jsonify({'success': False, 'error': 'Already saved!'})

    cursor.execute('''
        INSERT INTO saved_tips (user_id, tip_text, source, category)
        VALUES (?, ?, ?, ?)
    ''', (session['user_id'], tip_text, source, category))

    conn.commit()
    conn.close()

    return jsonify({'success': True, 'message': 'Tip saved!'})


# ════════════════════════════════════════════
#   SAVED TIPS — Delete a Tip
# ════════════════════════════════════════════
@app.route('/saved-tips/delete/<int:tip_id>')
def delete_tip(tip_id):
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    conn   = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        DELETE FROM saved_tips
        WHERE id = ? AND user_id = ?
    ''', (tip_id, session['user_id']))
    conn.commit()
    conn.close()

    flash('🗑️ Tip deleted!', 'success')
    return redirect(url_for('saved_tips'))


# ════════════════════════════════════════════
#   GOALS — View All Goals
# ════════════════════════════════════════════
@app.route('/goals')
def goals():
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    conn   = get_connection()
    cursor = conn.cursor()

    # Active goals
    cursor.execute('''
        SELECT * FROM goals
        WHERE user_id = ? AND status = 'active'
        ORDER BY created_at DESC
    ''', (session['user_id'],))
    active_goals = cursor.fetchall()

    # Completed goals
    cursor.execute('''
        SELECT * FROM goals
        WHERE user_id = ? AND status = 'completed'
        ORDER BY updated_at DESC
    ''', (session['user_id'],))
    completed_goals = cursor.fetchall()

    # Total saved across all active goals
    cursor.execute('''
        SELECT COALESCE(SUM(saved_amount), 0)  AS total_saved,
               COALESCE(SUM(target_amount), 0) AS total_target
        FROM goals
        WHERE user_id = ? AND status = 'active'
    ''', (session['user_id'],))
    totals = cursor.fetchone()

    conn.close()

    return render_template('goals.html',
        active_goals    = active_goals,
        completed_goals = completed_goals,
        total_saved     = totals['total_saved'],
        total_target    = totals['total_target']
    )


# ════════════════════════════════════════════
#   GOALS — Add New Goal
# ════════════════════════════════════════════
@app.route('/goals/add', methods=['POST'])
def add_goal():
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    title         = request.form['title'].strip()
    target_amount = request.form['target_amount'].strip()
    deadline      = request.form['deadline'].strip()
    category      = request.form['category'].strip()

    if not title or not target_amount:
        flash('Title and Target Amount are required!', 'error')
        return redirect(url_for('goals'))

    try:
        target_amount = float(target_amount)
        if target_amount <= 0:
            flash('Target must be greater than 0!', 'error')
            return redirect(url_for('goals'))
    except ValueError:
        flash('Invalid amount!', 'error')
        return redirect(url_for('goals'))

    conn   = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO goals
            (user_id, title, target_amount, deadline, category)
        VALUES (?, ?, ?, ?, ?)
    ''', (
        session['user_id'],
        title,
        target_amount,
        deadline if deadline else None,
        category
    ))
    conn.commit()
    conn.close()

    flash(f'✅ Goal "{title}" created!', 'success')
    return redirect(url_for('goals'))


# ════════════════════════════════════════════
#   GOALS — Add Money to Goal
# ════════════════════════════════════════════
@app.route('/goals/deposit/<int:goal_id>', methods=['POST'])
def deposit_goal(goal_id):
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    amount = request.form['amount'].strip()

    try:
        amount = float(amount)
        if amount <= 0:
            flash('Amount must be greater than 0!', 'error')
            return redirect(url_for('goals'))
    except ValueError:
        flash('Invalid amount!', 'error')
        return redirect(url_for('goals'))

    conn   = get_connection()
    cursor = conn.cursor()

    # Get goal first
    cursor.execute('''
        SELECT * FROM goals
        WHERE id = ? AND user_id = ? AND status = 'active'
    ''', (goal_id, session['user_id']))
    goal = cursor.fetchone()

    if not goal:
        flash('Goal not found!', 'error')
        conn.close()
        return redirect(url_for('goals'))

    new_saved = goal['saved_amount'] + amount

    # Check if goal is now completed
    new_status = 'completed' \
                 if new_saved >= goal['target_amount'] \
                 else 'active'

    cursor.execute('''
        UPDATE goals
        SET saved_amount = ?,
            status       = ?,
            updated_at   = datetime('now')
        WHERE id = ? AND user_id = ?
    ''', (new_saved, new_status, goal_id, session['user_id']))

    conn.commit()
    conn.close()

    if new_status == 'completed':
        flash(f'🎉 Congratulations! Goal "{goal["title"]}" completed!',
              'success')
    else:
        remaining = goal['target_amount'] - new_saved
        flash(f'✅ ₹{amount:.0f} added! ₹{remaining:.0f} remaining.',
              'success')

    return redirect(url_for('goals'))


# ════════════════════════════════════════════
#   GOALS — Get AI Advice for Goal (AJAX)
# ════════════════════════════════════════════
@app.route('/goals/ai-advice/<int:goal_id>', methods=['POST'])
def goal_ai_advice(goal_id):
    if 'user_id' not in session:
        return jsonify({'success': False})

    conn   = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT * FROM goals
        WHERE id = ? AND user_id = ?
    ''', (goal_id, session['user_id']))
    goal = cursor.fetchone()

    if not goal:
        conn.close()
        return jsonify({'success': False, 'error': 'Goal not found'})

    # Build spending context
    spending_context = build_spending_context(session['user_id'], conn)

    # Get AI advice
    result = get_goal_advice(
        title         = goal['title'],
        target_amount = goal['target_amount'],
        saved_amount  = goal['saved_amount'],
        deadline      = goal['deadline'],
        spending_context = spending_context
    )

    if result['success']:
        # Save AI advice to DB for this goal
        cursor.execute('''
            UPDATE goals
            SET ai_advice   = ?,
                updated_at  = datetime('now')
            WHERE id = ? AND user_id = ?
        ''', (result['advice'], goal_id, session['user_id']))
        conn.commit()

    conn.close()
    return jsonify(result)


# ════════════════════════════════════════════
#   GOALS — Delete Goal
# ════════════════════════════════════════════
@app.route('/goals/delete/<int:goal_id>')
def delete_goal(goal_id):
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    conn   = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        DELETE FROM goals WHERE id = ? AND user_id = ?
    ''', (goal_id, session['user_id']))
    conn.commit()
    conn.close()

    flash('🗑️ Goal deleted!', 'success')
    return redirect(url_for('goals'))


# ════════════════════════════════════════════
#   GOALS — Mark as Cancelled
# ════════════════════════════════════════════
@app.route('/goals/cancel/<int:goal_id>')
def cancel_goal(goal_id):
    if 'user_id' not in session:
        flash('Please login first!', 'error')
        return redirect(url_for('login'))

    conn   = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE goals SET status = 'cancelled',
        updated_at = datetime('now')
        WHERE id = ? AND user_id = ?
    ''', (goal_id, session['user_id']))
    conn.commit()
    conn.close()

    flash('Goal cancelled.', 'success')
    return redirect(url_for('goals'))


# ════════════════════════════════════════════
#   CONTEXT PROCESSOR — add tip count badge
# ════════════════════════════════════════════
# Find existing inject_alerts and ADD this inside it
# at the end before return statement:

# ── Run the app ───────────────────────────────────────────────
if __name__ == '__main__':
    app.run(debug=True)