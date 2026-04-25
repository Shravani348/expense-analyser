import hashlib
import os
import threading
import time
import requests
from google import genai

# ============================================================
# Gemini configuration
# ============================================================
# Read from env so keys are not committed to source.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# Allow switching models without code changes.
# Examples: gemini-2.0-flash, gemini-2.0-flash-lite, gemini-1.5-flash
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash").strip() or "gemini-2.0-flash"

# ============================================================
# Production-friendly request controls (rate limit + caching)
# ============================================================
# Minimum delay between API calls (global, across all functions).
# For Gemini free tier (15 RPM), this must be > 4.0s.
MIN_CALL_INTERVAL_S = max(4.1, float(os.getenv("AI_MIN_CALL_INTERVAL_S", "4.1")))

# Cache TTL (strong caching). Requirement: 10–15 minutes.
CACHE_TTL_S = max(900, int(os.getenv("AI_CACHE_TTL_S", "900")))

# _cache maps cache_key -> (expires_at_epoch_seconds, result_dict)
_cache = {}

# In-flight de-duplication: cache_key -> threading.Event.
# If multiple callers request the same prompt at once, only one API call happens.
_in_flight = {}

# Global rate limiter state.
_lock = threading.Lock()
_next_allowed_call_at = 0.0


def _make_cache_key(prompt: str) -> str:
    # Use SHA-256 for lower collision risk than MD5 (still fast).
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _cache_get(cache_key: str):
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
    _cache[cache_key] = (time.time() + CACHE_TTL_S, result)


def _throttle_global():
    """
    Global rate limiter.
    Ensures no burst requests happen by serializing calls and enforcing
    MIN_CALL_INTERVAL_S spacing between them.
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
    msg = error_msg.upper()
    if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
        return "rate_limit"
    if "503" in msg or "UNAVAILABLE" in msg:
        return "unavailable"
    return "other"


def generate(prompt):
    """
    Centralized Gemini call with:
    - strong caching (10–15 min TTL)
    - in-flight de-duplication (same prompt => one API call)
    - global rate limiter (>= 2s between calls)
    - max 2 attempts with exponential backoff
    """
    if client is None and not GROQ_API_KEY:
        return {
            "success": False,
            "error": "AI API key not configured. Set GEMINI_API_KEY or GROQ_API_KEY.",
        }

    cache_key = _make_cache_key(prompt)

    # 1) Strong cache
    with _lock:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        # 2) In-flight de-duplication
        ev = _in_flight.get(cache_key)
        if ev is None:
            ev = threading.Event()
            _in_flight[cache_key] = ev
            is_leader = True
        else:
            is_leader = False

    if not is_leader:
        # Someone else is already calling Gemini for this prompt.
        # Wait for them to finish, then read from cache.
        ev.wait(timeout=60)
        with _lock:
            cached = _cache_get(cache_key)
            if cached is not None:
                return cached
        return {"success": False, "error": "AI request timed out. Please try again."}

    # Leader path: do the API call (max 2 attempts).
    try:
        last_err = None
        for attempt in range(2):
            # Enforce global spacing to avoid bursts.
            _throttle_global()

            try:
                # ── Use Groq API if key is provided ──
                if GROQ_API_KEY:
                    headers = {
                        "Authorization": f"Bearer {GROQ_API_KEY}",
                        "Content-Type": "application/json"
                    }
                    payload = {
                        "model": "llama-3.3-70b-versatile",
                        "messages": [{"role": "user", "content": prompt}],
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
                            f"Could not connect to Groq API. Check your API key and internet connection. ({ce})"
                        )

                    if resp.status_code == 200:
                        data = resp.json()
                        text = data["choices"][0]["message"]["content"]
                        result = {"success": True, "text": text}
                        with _lock:
                            _cache_set(cache_key, result)
                        return result
                    elif resp.status_code == 401:
                        raise Exception("Groq API Error 401: Invalid API key. Please check your GROQ_API_KEY.")
                    elif resp.status_code == 429:
                        raise Exception("429 RESOURCE_EXHAUSTED Groq Rate Limit")
                    else:
                        raise Exception(f"Groq API Error {resp.status_code}: {resp.text}")

                # ── Fallback to Gemini API ──
                else:
                    if not client:
                        return {"success": False, "error": "Neither GEMINI_API_KEY nor GROQ_API_KEY is configured."}
                        
                    response = client.models.generate_content(
                        model=MODEL,
                        contents=prompt,
                    )
                    result = {"success": True, "text": getattr(response, "text", "")}
                    with _lock:
                        _cache_set(cache_key, result)
                    return result

            except Exception as e:
                last_err = str(e)
                kind = _classify_error(last_err)

                # Clean handling for known transient errors.
                if kind == "rate_limit":
                    # Exponential backoff: 2s
                    backoff = 2 * (2**attempt)
                    if attempt < 1:
                        time.sleep(backoff)
                        continue
                    return {
                        "success": False,
                        "error": f"Rate limit reached. Details: {last_err}",
                    }

                if kind == "unavailable":
                    backoff = 2 * (2**attempt)
                    if attempt < 1:
                        time.sleep(backoff)
                        continue
                    return {
                        "success": False,
                        "error": f"AI server is busy. Details: {last_err}",
                    }

                # Non-retryable/unknown errors: fail fast.
                return {"success": False, "error": last_err}

        return {"success": False, "error": last_err or "AI request failed. Please try again."}

    finally:
        # Always release in-flight waiters.
        with _lock:
            ev = _in_flight.pop(cache_key, None)
            if ev:
                ev.set()


def parse_tips(text):
    tips  = []
    lines = text.strip().split('\n')
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if len(line) > 2 and line[0].isdigit() and line[1] in '.):':
            line = line[2:].strip()
        elif len(line) > 3 and line[0].isdigit() and line[2] in '.):':
            line = line[3:].strip()
        if line:
            tips.append(line)
    return tips[:5]


def get_spending_analysis(spending_data):
    lines = []
    for category, data in spending_data.items():
        spent  = data['spent']
        budget = data['budget']
        if budget > 0:
            pct    = round((spent / budget) * 100, 1)
            status = 'OVER BUDGET' if spent > budget else 'within budget'
            lines.append(
                f"- {category}: spent ₹{spent:.0f} of ₹{budget:.0f} "
                f"({pct}% — {status})"
            )
        else:
            lines.append(f"- {category}: spent ₹{spent:.0f} (no budget set)")

    prompt = f"""
You are a friendly personal finance advisor for an Indian college student.

Here is their spending this month:
{chr(10).join(lines)}

Give exactly 4 short practical money-saving tips based on this data.
Focus on categories that are over budget.
Keep each tip to 2-3 sentences. Use simple language.
Use Indian Rupee amounts. Format as numbered list 1. 2. 3. 4.
No markdown bold or stars, plain text only.
"""
    result = generate(prompt)
    if result['success']:
        return {'success': True, 'tips': parse_tips(result['text'])}
    return {'success': False, 'error': result['error'], 'tips': []}


def get_category_advice(category, spent, budget, txn_count, month_name):
    if budget > 0:
        pct         = round((spent / budget) * 100, 1)
        status      = 'over budget' if spent > budget else 'within budget'
        budget_line = f"Budget: ₹{budget:.0f}. Used {pct}% — {status}."
    else:
        budget_line = "No budget set for this category."

    prompt = f"""
You are a friendly personal finance advisor for an Indian college student.

Their {category} spending in {month_name}:
- Total spent: ₹{spent:.0f}
- Transactions: {txn_count}
- {budget_line}

Give exactly 3 short specific tips to manage {category} spending better.
2 sentences each. Encouraging tone. Use Indian context where relevant.
Format as numbered list 1. 2. 3.
No markdown bold or stars, plain text only.
"""
    result = generate(prompt)
    if result['success']:
        return {'success': True, 'tips': parse_tips(result['text'])}
    return {'success': False, 'error': result['error'], 'tips': []}


def get_budget_recommendations(history_data, current_month):
    if not history_data:
        return {'success': False, 'error': 'Not enough data yet', 'tips': []}

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
Use numbered list. No markdown bold or stars, plain text only.
"""
    result = generate(prompt)
    if result['success']:
        return {'success': True, 'tips': parse_tips(result['text'])}
    return {'success': False, 'error': result['error'], 'tips': []}


def get_savings_tip():
    prompt = """
Give one practical money saving tip for an Indian college student.
2-3 sentences only. Specific, actionable, encouraging.
Mention Indian context like UPI, local shops, mess food where relevant.
No numbering, no markdown, just plain text.
"""
    result = generate(prompt)
    if result['success']:
        return {'success': True, 'tip': result['text'].strip()}
    return {'success': False, 'tip': 'Could not load tip. Try again.'}


def get_chat_response(user_message, spending_context, chat_history):
    history_text = ''
    for msg in chat_history[-6:]:
        if msg['role'] == 'user':
            history_text += f"User: {msg['message']}\n"
        else:
            history_text += f"Assistant: {msg['message']}\n"

    prompt = f"""
You are a friendly AI financial advisor inside an Expense Analyser app
for an Indian college student. You have access to their real spending data.

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
        return {'success': True, 'response': result['text'].strip()}
    # Preserve the underlying error so the caller/UI can show a useful message.
    return {
        'success': False,
        'error': result.get('error', 'AI request failed. Please try again.'),
        'response': ''
    }


def build_spending_context(user_id, conn):
    from datetime import datetime
    now           = datetime.now()
    current_month = now.month
    current_year  = now.year

    cursor = conn.cursor()

    cursor.execute('''
        SELECT category, total_spent AS spent, txn_count
        FROM v_monthly_summary
        WHERE user_id = ?
        AND month = ? AND year = ?
        ORDER BY spent DESC
    ''', (user_id, str(current_month).zfill(2), str(current_year)))
    spent_rows = cursor.fetchall()

    cursor.execute('''
        SELECT category, amount AS budget
        FROM v_budgets_full
        WHERE user_id = ? AND month = ? AND year = ?
    ''', (user_id, current_month, current_year))
    budget_rows = cursor.fetchall()

    cursor.execute('''
        SELECT COUNT(*) AS count,
               COALESCE(SUM(amount), 0) AS total
        FROM expenses WHERE user_id = ?
    ''', (user_id,))
    totals = cursor.fetchone()

    cursor.execute('''
        SELECT category, amount, date, note
        FROM v_expenses_full
        WHERE user_id = ?
        ORDER BY date DESC LIMIT 3
    ''', (user_id,))
    recent = cursor.fetchall()

    cursor.execute('''
        SELECT category, AVG(total_spent) AS avg_spent
        FROM v_monthly_summary
        WHERE user_id = ?
        GROUP BY category
        ORDER BY avg_spent DESC
    ''', (user_id,))
    averages = cursor.fetchall()

    months = ['','January','February','March','April','May','June',
              'July','August','September','October','November','December']
    month_name = months[current_month]
    budget_map = {r['category']: r['budget'] for r in budget_rows}

    lines = [f"Month: {month_name} {current_year}"]
    lines.append(f"Total expenses recorded: {totals['count']}")
    lines.append(f"All-time total spent: ₹{totals['total']:.0f}")
    lines.append("")
    lines.append("This month's spending:")

    for row in spent_rows:
        cat    = row['category']
        spent  = row['spent']
        budget = budget_map.get(cat, 0)
        txns   = row['txn_count']
        if budget > 0:
            pct    = round((spent / budget) * 100, 1)
            status = 'OVER BUDGET' if spent > budget else 'ok'
            lines.append(
                f"  {cat}: spent ₹{spent:.0f} / budget ₹{budget:.0f}"
                f" ({pct}% — {status}) — {txns} transactions"
            )
        else:
            lines.append(
                f"  {cat}: spent ₹{spent:.0f} (no budget set)"
                f" — {txns} transactions"
            )

    if averages:
        lines.append("")
        lines.append("Historical monthly averages:")
        for row in averages:
            lines.append(f"  {row['category']}: avg ₹{row['avg_spent']:.0f}/month")

    if recent:
        lines.append("")
        lines.append("Most recent expenses:")
        for r in recent:
            note = f" ({r['note']})" if r['note'] else ''
            lines.append(f"  {r['date']} — {r['category']}: ₹{r['amount']:.0f}{note}")

    return '\n'.join(lines)


def get_goal_advice(title, target_amount, saved_amount, deadline, spending_context):
    remaining     = target_amount - saved_amount
    pct           = round((saved_amount / target_amount) * 100, 1) if target_amount > 0 else 0
    deadline_line = f"Target date: {deadline}" if deadline else "No deadline set."

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

Keep it under 120 words. Be encouraging and specific.
Use Indian Rupee amounts. Plain text, no markdown or stars.
"""
    result = generate(prompt)
    if result['success']:
        return {'success': True, 'advice': result['text'].strip()}
    return {'success': False, 'advice': 'Could not generate advice.'}