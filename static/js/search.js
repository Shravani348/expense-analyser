// ════════════════════════════════════════════════
//   LIVE SEARCH — instant results as you type
// ════════════════════════════════════════════════

const searchInput   = document.getElementById('liveSearch');
const searchResults = document.getElementById('searchResults');

// Category → emoji map
const EMOJI = {
    'Food'         : '🍔',
    'Transport'    : '🚗',
    'Shopping'     : '🛍️',
    'Entertainment': '🎬',
    'Health'       : '💊',
    'Education'    : '📚',
    'Rent'         : '🏠',
    'Utilities'    : '💡',
    'Other'        : '📦'
};

// Only run if search bar exists on this page
if (searchInput) {

    let debounceTimer = null;   // prevents firing on every keystroke
    let lastQuery     = '';

    searchInput.addEventListener('input', function () {
        const query = this.value.trim();

        // Clear results if empty
        if (query.length === 0) {
            hideResults();
            return;
        }

        // Don't re-search same query
        if (query === lastQuery) return;
        lastQuery = query;

        // Debounce — wait 300ms after user stops typing
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
            performSearch(query);
        }, 300);
    });

    // Hide results when clicking outside
    document.addEventListener('click', function (e) {
        if (!e.target.closest('.search-wrapper')) {
            hideResults();
        }
    });

    // Keyboard navigation
    searchInput.addEventListener('keydown', function (e) {
        const items = searchResults.querySelectorAll('.search-result-item');
        const active = searchResults.querySelector('.search-result-item.highlighted');
        let idx = Array.from(items).indexOf(active);

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            idx = (idx + 1) % items.length;
            highlightItem(items, idx);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            idx = (idx - 1 + items.length) % items.length;
            highlightItem(items, idx);
        } else if (e.key === 'Escape') {
            hideResults();
            searchInput.blur();
        } else if (e.key === 'Enter' && active) {
            e.preventDefault();
            active.click();
        }
    });
}


async function performSearch(query) {
    showLoading();

    try {
        // Call our Flask /search endpoint
        const response = await fetch(`/search?q=${encodeURIComponent(query)}`);
        const results  = await response.json();
        renderResults(results, query);
    } catch (err) {
        showError();
    }
}


function renderResults(results, query) {
    if (results.length === 0) {
        searchResults.innerHTML = `
            <div class="search-no-results">
                😕 No expenses found for "<strong>${escapeHtml(query)}</strong>"
            </div>`;
        showResults();
        return;
    }

    const html = results.map(r => {
        const emoji       = EMOJI[r.category] || '📦';
        const note        = r.note ? escapeHtml(r.note) : 'No note';
        const highlighted = highlightText(r.category, query);

        return `
        <div class="search-result-item"
             onclick="window.location='/expenses/edit/${r.id}'">
            <div class="result-icon">${emoji}</div>
            <div class="result-info">
                <div class="result-category">${highlighted}</div>
                <div class="result-note">${escapeHtml(note)}</div>
            </div>
            <div class="result-right">
                <div class="result-amount">₹${parseFloat(r.amount).toFixed(2)}</div>
                <div class="result-date">${r.date}</div>
            </div>
        </div>`;
    }).join('');

    // Add "View all expenses" footer
    const footer = `
        <div style="padding:10px 16px;text-align:center;border-top:1px solid var(--border)">
            <a href="/expenses" style="font-size:0.82rem;color:var(--primary);font-weight:600">
                View all expenses →
            </a>
        </div>`;

    searchResults.innerHTML = html + footer;
    showResults();
}


function highlightText(text, query) {
    // Wraps matched text in a highlight span
    const escaped = escapeHtml(text);
    const regex   = new RegExp(`(${escapeRegex(query)})`, 'gi');
    return escaped.replace(regex,
        '<mark style="background:#ede9ff;color:#5a52d5;border-radius:3px;padding:0 2px">$1</mark>'
    );
}


function highlightItem(items, idx) {
    items.forEach(i => i.classList.remove('highlighted'));
    if (items[idx]) {
        items[idx].classList.add('highlighted');
        items[idx].style.background = 'var(--bg-page)';
    }
}


function showResults() {
    searchResults.classList.add('visible');
}

function hideResults() {
    searchResults.classList.remove('visible');
    searchResults.innerHTML = '';
    if (searchInput) searchInput.value = '';
    lastQuery = '';
}

function showLoading() {
    searchResults.innerHTML =
        '<div class="search-loading">🔍 Searching...</div>';
    showResults();
}

function showError() {
    searchResults.innerHTML =
        '<div class="search-no-results">❌ Search failed. Try again.</div>';
}

// Security helpers
function escapeHtml(str) {
    const div = document.createElement('div');
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
}

function escapeRegex(str) {
    return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}