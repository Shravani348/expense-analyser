# 💰 Expense & Budget Analyser

An AI-powered personal finance tracker built as a DBMS project.

## 🚀 Features

- 👤 User Authentication (Register/Login/Logout)
- 💸 Expense Tracking (Add/Edit/Delete/Filter)
- 💼 Budget Management with alerts
- 🔁 Recurring Expenses (auto-add monthly)
- 📊 Dashboard with Charts & Analytics
- ⚠️ Smart Budget Alerts & Warnings
- 📁 Reports with CSV Export
- 🤖 AI Financial Advisor (Google Gemini)
- 💬 AI Chat with real expense data
- 🎯 Savings Goals Tracker
- 🔖 Save AI Tips
- 🔍 Live Search

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Frontend | HTML, CSS, JavaScript |
| Backend | Python, Flask |
| Database | SQLite |
| AI | Google Gemini API |
| Charts | Chart.js |

## 🗃️ Database Concepts Used

- Tables, Primary Keys, Foreign Keys
- Normalization (Categories table)
- CHECK Constraints
- CASCADE DELETE
- Indexes for performance
- SQL Views (JOINs)
- Aggregate Functions
- Transactions

## ⚙️ Setup Instructions

### 1. Clone the repository
```bash
git clone https://github.com/YourUsername/expense-analyser.git
cd expense-analyser
```

### 2. Create virtual environment
```bash
python -m venv venv
venv\Scripts\activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Add your Gemini API Key
Set an environment variable:

```bash
export GEMINI_API_KEY="YOUR_KEY_HERE"
```

Or (one-off):

```bash
GEMINI_API_KEY="YOUR_KEY_HERE" python app.py
```
Get free key from: https://aistudio.google.com

### 5. Run the app
```bash
python app.py
```

Open browser: http://127.0.0.1:5000

## 📁 Project Structure

```
expense-analyser/
├── app.py              # Flask routes
├── database.py         # DB schema & helpers
├── ai_helper.py        # Gemini AI integration
├── requirements.txt    # Dependencies
├── templates/          # HTML pages
└── static/             # CSS & JS files
```