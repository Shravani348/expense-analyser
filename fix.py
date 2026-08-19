with open('database.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace(r"\'\'\'", "'''")

with open('database.py', 'w', encoding='utf-8') as f:
    f.write(content)
