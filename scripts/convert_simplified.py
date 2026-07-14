import opencc

# 't2s' means Traditional → Simplified
converter = opencc.OpenCC('t2s')

input_file  = '一世清歡gl.txt'
output_file = 'output_simplified.txt'

with open(input_file, 'r', encoding='utf-8') as f:
    content = f.read()

simplified = converter.convert(content)

with open(output_file, 'w', encoding='utf-8') as f:
    f.write(simplified)

print("Done! Saved to", output_file)