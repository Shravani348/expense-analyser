import hashlib
import os
import threading
import time
import requests
from dotenv import load_dotenv
from google import genai

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()

MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.6-flash"
).strip() or "gemini-3.6-flash"

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "llama-3.1-8b-instant"
).strip() or "llama-3.1-8b-instant"
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# Allow switching Gemini models without code changes.
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip() or "gemini-3.6-flash"

# ============================================================
# Production-friendly request controls
# ============================================================

# Minimum delay between API calls
MIN_CALL_INTERVAL_S = max(
    4.1,
    float(os.getenv("AI_MIN_CALL_INTERVAL_S", "4.1"))
)

# Cache TTL: minimum 15 minutes
CACHE_TTL_S = max(
    900,
    int(os.getenv("AI_CACHE_TTL_S", "900"))
)

# Cache:
# cache_key -> (expires_at_epoch_seconds, result_dict)
_cache = {}

# In-flight de-duplication:
# cache_key -> threading.Event
_in_flight = {}

# Global rate limiter state
_lock = threading.Lock()
_next_allowed_call_at = 0.0


def _make_cache_key(prompt: str) -> str:
    """Create a unique SHA-256 cache key for the prompt."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _cache_get(cache_key: str):
    """Get cached result if it hasn't expired."""
    now = time.time()
    item = _cache.get(cache_key)

    if not item:
        return None

    expires_at, result = item

    if now >= expires_at:
        _cache.pop(cache_key, None)
        return None

    return result


def _cache_set(cache_key: str, result: dict):
    """Store result in cache."""
    _cache[cache_key] = (
        time.time() + CACHE_TTL_S,
        result
    )


def _throttle_global():
    """
    Global rate limiter.

    Ensures API requests don't happen in bursts and
    enforces MIN_CALL_INTERVAL_S spacing between calls.
    """
    global _next_allowed_call_at

    while True:
        with _lock:
            now = time.time()
            wait = max(0.0, _next_allowed_call_at - now)

            if wait <= 0.0:
                _next_allowed_call_at = now + MIN_CALL_INTERVAL_S
                return

        time.sleep(min(wait, 0.25))


def _classify_error(error_msg: str) -> str:
    """Classify API errors."""
    msg = error_msg.upper()

    if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
        return "rate_limit"

    if "503" in msg or "UNAVAILABLE" in msg:
        return "unavailable"

    return "other"


def generate(prompt):
    """
    Centralized AI call with:

    - Strong caching
    - In-flight de-duplication
    - Global rate limiter
    - Maximum 2 attempts with exponential backoff
    - Groq as primary provider
    - Gemini as fallback
    """

    # Check whether at least one API key is configured
    if client is None and not GROQ_API_KEY:
        return {
            "success": False,
            "error": "AI API key not configured. Set GEMINI_API_KEY or GROQ_API_KEY.",
        }

    cache_key = _make_cache_key(prompt)

    # ========================================================
    # 1) Check cache
    # ========================================================

    with _lock:
        cached = _cache_get(cache_key)

        if cached is not None:
            return cached

        # ====================================================
        # 2) In-flight request de-duplication
        # ====================================================

        ev = _in_flight.get(cache_key)

        if ev is None:
            ev = threading.Event()
            _in_flight[cache_key] = ev
            is_leader = True
        else:
            is_leader = False

    # If another request is already processing the same prompt
    if not is_leader:

        ev.wait(timeout=60)

        with _lock:
            cached = _cache_get(cache_key)

            if cached is not None:
                return cached

        return {
            "success": False,
            "error": "AI request timed out. Please try again."
        }

    # ========================================================
    # Leader path: make API call
    # ========================================================

    try:
        last_err = None

        for attempt in range(2):

            # Enforce global request spacing
            _throttle_global()

            try:

                # ====================================================
                # GROQ API
                # ====================================================

                if GROQ_API_KEY:

                    headers = {
                        "Authorization": f"Bearer {GROQ_API_KEY}",
                        "Content-Type": "application/json"
                    }

                    payload = {
                        # FIXED GROQ MODEL
                        "model": "llama-3.1-8b-instant",

                        "messages": [
                            {
                                "role": "user",
                                "content": prompt
                            }
                        ],

                        "temperature": 0.7
                    }

                    try:
                        resp = requests.post(
                            "https://api.groq.com/openai/v1/chat/completions",
                            headers=headers,
                            json=payload,
                            timeout=30
                        )

                    except requests.exceptions.ConnectionError as ce:
                        raise Exception(
                            "Could not connect to Groq API. "
                            f"Check your API key and internet connection. ({ce})"
                        )

                    # ====================================================
                    # Successful response
                    # ====================================================

                    if resp.status_code == 200:

                        data = resp.json()

                        text = data["choices"][0]["message"]["content"]

                        result = {
                            "success": True,
                            "text": text
                        }

                        with _lock:
                            _cache_set(cache_key, result)

                        return result

                    # ====================================================
                    # Authentication error
                    # ====================================================

                    elif resp.status_code == 401:

                        raise Exception(
                            "Groq API Error 401: Invalid API key. "
                            "Please check your GROQ_API_KEY."
                        )

                    # ====================================================
                    # Rate limit
                    # ====================================================

                    elif resp.status_code == 429:

                        raise Exception(
                            "429 RESOURCE_EXHAUSTED Groq Rate Limit"
                        )

                    # ====================================================
                    # Model / other errors
                    # ====================================================

                    else:

                        raise Exception(
                            f"Groq API Error {resp.status_code}: {resp.text}"
                        )

                # ====================================================
                # GEMINI FALLBACK
                # ====================================================

                else:

                    if not client:

                        return {
                            "success": False,
                            "error": (
                                "Neither GEMINI_API_KEY nor "
                                "GROQ_API_KEY is configured."
                            )
                        }

                    response = client.models.generate_content(
                        model=MODEL,
                        contents=prompt,
                    )

                    result = {
                        "success": True,
                        "text": getattr(response, "text", "")
                    }

                    with _lock:
                        _cache_set(cache_key, result)

                    return result

            except Exception as e:

                last_err = str(e)

                kind = _classify_error(last_err)

                # ====================================================
                # Rate limit handling
                # ====================================================

                if kind == "rate_limit":

                    backoff = 2 * (2 ** attempt)

                    if attempt < 1:
                        time.sleep(backoff)
                        continue

                    return {
                        "success": False,
                        "error": f"Rate limit reached. Details: {last_err}",
                    }

                # ====================================================
                # Server unavailable
                # ====================================================

                if kind == "unavailable":

                    backoff = 2 * (2 ** attempt)

                    if attempt < 1:
                        time.sleep(backoff)
                        continue

                    return {
                        "success": False,
                        "error": f"AI server is busy. Details: {last_err}",
                    }

                # ====================================================
                # Other errors
                # ====================================================

                return {
                    "success": False,
                    "error": last_err
                }

        return {
            "success": False,
            "error": last_err or "AI request failed. Please try again."
        }

    finally:

        # Always release waiting requests
        with _lock:

            ev = _in_flight.pop(cache_key, None)

            if ev:
                ev.set()


def parse_tips(text):
    """Convert numbered AI response into a list of tips."""

    tips = []

    lines = text.strip().split('\n')

    for line in lines:

        line = line.strip()

        if not line:
            continue

        # Example:
        # 1. Save money
        if len(line) > 2 and line[0].isdigit() and line[1] in '.):':
            line = line[2:].strip()

        # Example:
        # 10. Save money
        elif (
            len(line) > 3
            and line[0].isdigit()
            and line[2] in '.):'
        ):
            line = line[3:].strip()

        if line:
            tips.append(line)

    return tips[:5]


def get_spending_analysis(spending_data):

    lines = []

    for category, data in spending_data.items():

        spent = data['spent']
        budget = data['budget']

        if budget > 0:

            pct = round((spent / budget) * 100, 1)

            status = (
                'OVER BUDGET'
                if spent > budget
                else 'within budget'
            )

            lines.append(
                f"- {category}: spent ₹{spent:.0f} "
                f"of ₹{budget:.0f} "
                f"({pct}% — {status})"
            )

        else:

            lines.append(
                f"- {category}: spent ₹{spent:.0f} "
                f"(no budget set)"
            )

    prompt = f"""
You are a friendly personal finance advisor for an Indian college student.

Here is their spending this month:
{chr(10).join(lines)}

Give exactly 4 short practical money-saving tips based on this data.
Focus on categories that are over budget.
Keep each tip to 2-3 sentences.
Use simple language.
Use Indian Rupee amounts.
Format as numbered list 1. 2. 3. 4.
No markdown bold or stars, plain text only.
"""

    result = generate(prompt)

    if result['success']:

        return {
            'success': True,
            'tips': parse_tips(result['text'])
        }

    return {
        'success': False,
        'error': result['error'],
        'tips': []
    }


def get_category_advice(
    category,
    spent,
    budget,
    txn_count,
    month_name
):

    if budget > 0:

        pct = round((spent / budget) * 100, 1)

        status = (
            'over budget'
            if spent > budget
            else 'within budget'
        )

        budget_line = (
            f"Budget: ₹{budget:.0f}. "
            f"Used {pct}% — {status}."
        )

    else:

        budget_line = "No budget set for this category."

    prompt = f"""
You are a friendly personal finance advisor for an Indian college student.

Their {category} spending in {month_name}:
- Total spent: ₹{spent:.0f}
- Transactions: {txn_count}
- {budget_line}

Give exactly 3 short specific tips to manage {category} spending better.
2 sentences each.
Encouraging tone.
Use Indian context where relevant.
Format as numbered list 1. 2. 3.
No markdown bold or stars, plain text only.
"""

    result = generate(prompt)

    if result['success']:

        return {
            'success': True,
            'tips': parse_tips(result['text'])
        }

    return {
        'success': False,
        'error': result['error'],
        'tips': []
    }


def get_budget_recommendations(history_data, current_month):

    if not history_data:

        return {
            'success': False,
            'error': 'Not enough data yet',
            'tips': []
        }

    lines = [
        f"- {row['category']}: avg ₹{row['avg_spent']:.0f}/month"
        for row in history_data
    ]

    prompt = f"""
You are a friendly personal finance advisor for an Indian college student.

Their average monthly spending:
{chr(10).join(lines)}

Suggest a realistic budget for next month for each category.
Add 10-15% buffer above average so they can save money.
Format: "Category: ₹amount — one sentence reason."
Use numbered list.
No markdown bold or stars, plain text only.
"""

    result = generate(prompt)

    if result['success']:

        return {
            'success': True,
            'tips': parse_tips(result['text'])
        }

    return {
        'success': False,
        'error': result['error'],
        'tips': []
    }


def get_savings_tip():

    prompt = """
Give one practical money saving tip for an Indian college student.
2-3 sentences only.
Specific, actionable, encouraging.
Mention Indian context like UPI, local shops, mess food where relevant.
No numbering, no markdown, just plain text.
"""

    result = generate(prompt)

    if result['success']:

        return {
            'success': True,
            'tip': result['text'].strip()
        }

    return {
        'success': False,
        'tip': 'Could not load tip. Try again.'
    }


def get_chat_response(
    user_message,
    spending_context,
    chat_history
):

    history_text = ''

    for msg in chat_history[-6:]:

        if msg['role'] == 'user':

            history_text += (
                f"User: {msg['message']}\n"
            )

        else:

            history_text += (
                f"Assistant: {msg['message']}\n"
            )

    prompt = f"""
You are a friendly AI financial advisor inside an Expense Analyser app
for an Indian college student.
You have access to their real spending data.

=== THEIR CURRENT FINANCIAL DATA ===
{spending_context}

=== PREVIOUS CONVERSATION ===
{history_text if history_text else "This is the start of the conversation."}

=== USER'S NEW MESSAGE ===
{user_message}

=== YOUR INSTRUCTIONS ===
- Answer based on their REAL data shown above
- Be friendly, encouraging and specific
- Use Indian Rupee (₹) for all amounts
- Keep response under 150 words
- If they ask something unrelated to finance, politely redirect
- If you reference their data, be specific (mention actual amounts)
- No markdown bold or stars, plain text only
- End with a helpful follow-up question when appropriate
"""

    result = generate(prompt)

    if result['success']:

        return {
            'success': True,
            'response': result['text'].strip()
        }

    return {
        'success': False,
        'error': result.get(
            'error',
            'AI request failed. Please try again.'
        ),
        'response': ''
    }


def build_spending_context(user_id, conn):

    from datetime import datetime

    now = datetime.now()

    current_month = now.month
    current_year = now.year

    cursor = conn.cursor()

    # ========================================================
    # Current month spending
    # ========================================================

    cursor.execute(
        '''
        SELECT category,
               total_spent AS spent,
               txn_count
        FROM v_monthly_summary
        WHERE user_id = %s
        AND month = %s
        AND year = %s
        ORDER BY spent DESC
        ''',
        (
            user_id,
            str(current_month).zfill(2),
            str(current_year)
        )
    )

    spent_rows = cursor.fetchall()

    # ========================================================
    # Budgets
    # ========================================================

    cursor.execute(
        '''
        SELECT category,
               amount AS budget
        FROM v_budgets_full
        WHERE user_id = %s
        AND month = %s
        AND year = %s
        ''',
        (
            user_id,
            current_month,
            current_year
        )
    )

    budget_rows = cursor.fetchall()

    # ========================================================
    # Total expenses
    # ========================================================

    cursor.execute(
        '''
        SELECT COUNT(*) AS count,
               COALESCE(SUM(amount), 0) AS total
        FROM expenses
        WHERE user_id = %s
        ''',
        (user_id,)
    )

    totals = cursor.fetchone()

    # ========================================================
    # Recent expenses
    # ========================================================

    cursor.execute(
        '''
        SELECT category,
               amount,
               date,
               note
        FROM v_expenses_full
        WHERE user_id = %s
        ORDER BY date DESC
        LIMIT 3
        ''',
        (user_id,)
    )

    recent = cursor.fetchall()

    # ========================================================
    # Historical averages
    # ========================================================

    cursor.execute(
        '''
        SELECT category,
               AVG(total_spent) AS avg_spent
        FROM v_monthly_summary
        WHERE user_id = %s
        GROUP BY category
        ORDER BY avg_spent DESC
        ''',
        (user_id,)
    )

    averages = cursor.fetchall()

    months = [
        '',
        'January',
        'February',
        'March',
        'April',
        'May',
        'June',
        'July',
        'August',
        'September',
        'October',
        'November',
        'December'
    ]

    month_name = months[current_month]

    budget_map = {
        r['category']: r['budget']
        for r in budget_rows
    }

    lines = [
        f"Month: {month_name} {current_year}"
    ]

    lines.append(
        f"Total expenses recorded: {totals['count']}"
    )

    lines.append(
        f"All-time total spent: ₹{totals['total']:.0f}"
    )

    lines.append("")

    lines.append(
        "This month's spending:"
    )

    # ========================================================
    # Spending by category
    # ========================================================

    for row in spent_rows:

        cat = row['category']
        spent = row['spent']

        budget = budget_map.get(cat, 0)

        txns = row['txn_count']

        if budget > 0:

            pct = round(
                (spent / budget) * 100,
                1
            )

            status = (
                'OVER BUDGET'
                if spent > budget
                else 'ok'
            )

            lines.append(
                f"  {cat}: spent ₹{spent:.0f} "
                f"/ budget ₹{budget:.0f} "
                f"({pct}% — {status}) "
                f"— {txns} transactions"
            )

        else:

            lines.append(
                f"  {cat}: spent ₹{spent:.0f} "
                f"(no budget set) "
                f"— {txns} transactions"
            )

    # ========================================================
    # Historical averages
    # ========================================================

    if averages:

        lines.append("")

        lines.append(
            "Historical monthly averages:"
        )

        for row in averages:

            lines.append(
                f"  {row['category']}: "
                f"avg ₹{row['avg_spent']:.0f}/month"
            )

    # ========================================================
    # Recent expenses
    # ========================================================

    if recent:

        lines.append("")

        lines.append(
            "Most recent expenses:"
        )

        for r in recent:

            note = (
                f" ({r['note']})"
                if r['note']
                else ''
            )

            lines.append(
                f"  {r['date']} — "
                f"{r['category']}: "
                f"₹{r['amount']:.0f}"
                f"{note}"
            )

    return '\n'.join(lines)


def get_goal_advice(
    title,
    target_amount,
    saved_amount,
    deadline,
    spending_context
):

    remaining = target_amount - saved_amount

    pct = (
        round(
            (saved_amount / target_amount) * 100,
            1
        )
        if target_amount > 0
        else 0
    )

    deadline_line = (
        f"Target date: {deadline}"
        if deadline
        else "No deadline set."
    )

    prompt = f"""
You are a friendly financial advisor for an Indian college student.

Their savings goal:
- Goal: {title}
- Target amount: ₹{target_amount:.0f}
- Already saved: ₹{saved_amount:.0f} ({pct}% done)
- Still needed: ₹{remaining:.0f}
- {deadline_line}

Their current spending data:
{spending_context}

Give them a specific, practical plan to achieve this goal.

Include:
1. How much they need to save per month/week
2. Which spending categories they can cut to save faster
3. One motivating tip to stay on track

Keep it under 120 words.
Be encouraging and specific.
Use Indian Rupee amounts.
Plain text, no markdown or stars.
"""

    result = generate(prompt)

    if result['success']:

        return {
            'success': True,
            'advice': result['text'].strip()
        }

    return {
        'success': False,
        'advice': 'Could not generate advice.'
    }