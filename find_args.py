import re
text = open('pipeline/produce.py', 'r', encoding='utf-8').read()
# Find argparse args
for m in re.finditer(r'add_argument\([^)]+\)', text, re.DOTALL):
    print(m.group())
    print('---')
# Also find legal/explore/search references
for line_num, line in enumerate(text.split('\n'), 1):
    for kw in ['legal', 'explore', 'search_mode', 'search_budget', 'space']:
        if kw in line.lower():
            print(f"L{line_num}: {line.strip()}")
