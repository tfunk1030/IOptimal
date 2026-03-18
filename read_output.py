import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
with open(r'C:\Users\VYRAL\IOptimal\full_pipeline_output_v2.txt', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()
print(content)
print(f"\n--- END OF FILE ({len(content)} chars, {content.count(chr(10))} lines) ---")
