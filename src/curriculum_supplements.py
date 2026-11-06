"""按问题附加培养方案摘要，避免把整本课程表塞进每次请求。"""
import json
import re
import unicodedata

from project_paths import TEXT_DIR


def normalize(value):
    return re.sub(r'[\W_]+', '', unicodedata.normalize('NFKC', value).casefold())


def relevant_knowledge(question, limit=1800):
    catalog = json.loads((TEXT_DIR / 'curriculum_supplements.json').read_text(encoding='utf-8'))
    text = normalize(question)
    academic = any(normalize(word) in text for word in catalog['academic_terms'])
    matched = []
    for item in catalog['entries']:
        if item.get('academic_only') and not academic:
            continue
        if any(all(normalize(term) in text for term in group) for group in item['matches']):
            matched.append(item)
    if not matched:
        return ''
    header = ('\n\n# 按本次问题提供的补充培养资料\n'
              '以下是用户提供的2024版资料摘要；按项目、教学计划年份及适用对象解释，'
              '不自动套用于2025级方案、当前排课或个人毕业审核。只有下列摘要实际提供的细节可引用。\n')
    result = header
    omitted = []
    for item in matched:
        source = catalog['sources'][item['source']]
        block = f"\n[{item['id']}] {item['title']}\n{item['body']}\n来源：{source['label']}，PDF第{item['pages']}页；{source['path']}。\n"
        if len(result) + len(block) <= limit - 90:
            result += block
        else:
            omitted.append(item['title'])
    if omitted:
        result += '\n本次未附全部匹配摘要；未附部分的学分及条件不可补造，请缩小到具体项目或课程。'
    return result
