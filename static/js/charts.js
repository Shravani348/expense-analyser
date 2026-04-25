// ── CHART.JS CDN is loaded via base.html ──────────
// We receive categoryData and monthlyTrend from dashboard.html

// ── COLOR PALETTE ──────────────────────────────────
const COLORS = [
    '#f5a623', '#e74c3c', '#3498db', '#2ecc71',
    '#9b59b6', '#1abc9c', '#e67e22', '#34495e', '#e91e63'
];

// ════════════════════════════════════════════════════
//   PIE CHART — Spending by Category
// ════════════════════════════════════════════════════
const pieCanvas = document.getElementById('pieChart');

if (pieCanvas && Object.keys(categoryData).length > 0) {

    // Extract labels (category names) and values (spent amounts)
    const pieLabels = Object.keys(categoryData);
    const pieValues = pieLabels.map(cat => categoryData[cat].spent);

    new Chart(pieCanvas, {
        type: 'doughnut',   // doughnut looks cleaner than plain pie
        data: {
            labels  : pieLabels,
            datasets: [{
                data           : pieValues,
                backgroundColor: COLORS.slice(0, pieLabels.length),
                borderWidth    : 3,
                borderColor    : '#fff'
            }]
        },
        options: {
            responsive       : true,
            maintainAspectRatio: true,
            plugins: {
                legend: {
                    position: 'bottom',
                    labels  : {
                        padding  : 16,
                        font     : { size: 13 },
                        boxWidth : 14
                    }
                },
                tooltip: {
                    callbacks: {
                        // Show ₹ symbol in tooltip
                        label: function(context) {
                            const value = context.parsed;
                            const total = context.dataset.data
                                            .reduce((a, b) => a + b, 0);
                            const pct   = ((value / total) * 100).toFixed(1);
                            return ` ₹${value.toFixed(2)}  (${pct}%)`;
                        }
                    }
                }
            }
        }
    });
}

// ════════════════════════════════════════════════════
//   BAR CHART — Monthly Spending Trend
// ════════════════════════════════════════════════════
const barCanvas = document.getElementById('barChart');

if (barCanvas && monthlyTrend.length > 0) {

    // monthlyTrend rows look like: {month_year: "2026-04", total: 3500}
    const barLabels = monthlyTrend.map(row => {
        // Convert "2026-04" → "Apr 2026"
        const [year, month] = row.month_year.split('-');
        const monthNames = ['','Jan','Feb','Mar','Apr','May','Jun',
                            'Jul','Aug','Sep','Oct','Nov','Dec'];
        return `${monthNames[parseInt(month)]} ${year}`;
    });

    const barValues = monthlyTrend.map(row => row.total);

    // Color bars — latest month is highlighted
    const barColors = barValues.map((_, i) =>
        i === barValues.length - 1 ? '#f5a623' : '#3498db'
    );

    new Chart(barCanvas, {
        type: 'bar',
        data: {
            labels  : barLabels,
            datasets: [{
                label          : 'Amount Spent (₹)',
                data           : barValues,
                backgroundColor: barColors,
                borderRadius   : 8,
                borderSkipped  : false
            }]
        },
        options: {
            responsive       : true,
            maintainAspectRatio: true,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            return ` ₹${context.parsed.y.toFixed(2)}`;
                        }
                    }
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    grid: { color: '#f0f0f0' },
                    ticks: {
                        callback: function(value) {
                            return '₹' + value.toLocaleString('en-IN');
                        }
                    }
                },
                x: {
                    grid: { display: false }
                }
            }
        }
    });
}
// ════════════════════════════════════════════════════
//   LINE CHART — Daily Spending Trend (Reports page)
// ════════════════════════════════════════════════════
const dailyCanvas = document.getElementById('dailyChart');

if (dailyCanvas && typeof dailyTrend !== 'undefined' && dailyTrend.length > 0) {

    const dailyLabels = dailyTrend.map(row => row.date);
    const dailyValues = dailyTrend.map(row => row.total);

    new Chart(dailyCanvas, {
        type: 'line',
        data: {
            labels  : dailyLabels,
            datasets: [{
                label          : 'Daily Spending (₹)',
                data           : dailyValues,
                borderColor    : '#f5a623',
                backgroundColor: 'rgba(245,166,35,0.1)',
                borderWidth    : 3,
                pointBackgroundColor: '#f5a623',
                pointRadius    : 5,
                tension        : 0.4,   // smooth curve
                fill           : true
            }]
        },
        options: {
            responsive: true,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            return ` ₹${context.parsed.y.toFixed(2)}`;
                        }
                    }
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    grid: { color: '#f0f0f0' },
                    ticks: {
                        callback: function(value) {
                            return '₹' + value.toLocaleString('en-IN');
                        }
                    }
                },
                x: {
                    grid: { display: false },
                    ticks: {
                        maxTicksLimit: 10  // avoid crowded x-axis labels
                    }
                }
            }
        }
    });
}