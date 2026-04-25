# ── Expense Analyser Startup Script ──
# Usage: Right-click this file → "Run with PowerShell"
# OR in PowerShell terminal: .\start.ps1

# Load API key from .env file (never hardcode keys here!)
if (Test-Path ".env") {
    Get-Content ".env" | ForEach-Object {
        if ($_ -match "^\s*([^#][^=]+)=(.+)$") {
            [System.Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), "Process")
        }
    }
    Write-Host "✅ Loaded API keys from .env" -ForegroundColor Green
} else {
    Write-Host "❌ .env file not found! Create one with your GROQ_API_KEY." -ForegroundColor Red
    Write-Host "   Copy .env.example to .env and fill in your key." -ForegroundColor Yellow
    exit 1
}

# Activate virtual environment
.\venv\Scripts\Activate.ps1

Write-Host "✅ Virtual environment activated" -ForegroundColor Green
Write-Host "🚀 Starting Expense Analyser at http://127.0.0.1:5000 ..." -ForegroundColor Cyan
Write-Host ""

# Start the app
python app.py
