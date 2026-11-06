"""学习资源推荐复测；--course-sites选课程网站题，默认离线，--run才调用模型。"""
import argparse
import csv
from datetime import datetime
import hashlib
import json
from urllib.parse import urlparse

from openai import OpenAI
import assistant
from project_paths import PROJECT_DIR, TEXT_DIR, RESOURCE_DIR
from web_session import new_session


def load_cases(course_sites=False):
    filename = '课程学习网站复测.csv' if course_sites else '学习资源推荐复测.csv'
    with (TEXT_DIR / filename).open(encoding='utf-8', newline='') as f:
        cases = list(csv.DictReader(f))
    count = 8 if course_sites else 5
    if len(cases) != count or len({c['编号'] for c in cases}) != count:
        raise ValueError(f'本轮应有{count}道独立复测题。')
    return cases


def offline_preflight(cases):
    from tokenizers import Tokenizer
    tokenizer = Tokenizer.from_file(str(RESOURCE_DIR / 'deepseek_v4_tokenizer' / 'tokenizer.json'))
    tokenizer.no_truncation()
    sizes = []
    for case in cases:
        messages, _, _ = assistant.prepare_messages(assistant.load_system_prompt('v3'), [], case['问题'], False, 24000, entry='web')
        sizes.append({'id':case['编号'], 'characters':sum(len(m['content']) for m in messages),
                      'local_content_tokens':sum(len(tokenizer.encode(m['content'], add_special_tokens=False).ids) for m in messages)})
    return sizes


def snapshot(folder, course_sites=False):
    files = {'v3-after.txt':TEXT_DIR/'prompts/v3.txt', 'college_knowledge.txt':TEXT_DIR/'college_knowledge.txt',
             'learning_resources.json':TEXT_DIR/'learning_resources.json',
             'cases.csv':TEXT_DIR/('课程学习网站复测.csv' if course_sites else '学习资源推荐复测.csv'),
             'learning_resources.py':PROJECT_DIR/'src/learning_resources.py', 'assistant.py':PROJECT_DIR/'src/assistant.py',
             'curriculum_supplements.json':TEXT_DIR/'curriculum_supplements.json',
             'curriculum_2025.txt':TEXT_DIR/'curriculum_2025.txt', 'teachers.json':TEXT_DIR/'teachers.json'}
    hashes = {}
    for name, source in files.items():
        raw = source.read_bytes(); (folder/name).write_bytes(raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    raw = files['v3-after.txt'].read_bytes()
    start, end = raw.index('# 学习资源推荐\n'.encode()), raw.index('# 技术问题的示例\n'.encode())
    before = raw[:start] + raw[end:]
    expected = '4a869b2f2718047e4f24f2e54a643034d6dd10dbacf6eb719ee013de29a708b4'
    if hashlib.sha256(before).hexdigest() == expected:
        (folder/'v3-before.txt').write_bytes(before)
        hashes['v3-before.txt'] = expected
    else:
        hashes['v3-before-note'] = '当前换行或其他内容与此前哈希不一致，未生成伪造的修改前快照。'
    return hashes


def run_cases(client, model, cases, folder):
    records = []
    for case in cases:
        state = new_session(model, {'thinking': {'type':'disabled'}}).state
        state.update(stream=False, history_enabled=False, temperature=.3, max_tokens=800, test_id=case['编号'])
        print('\n' + case['编号'] + '：' + case['问题'])
        record = assistant.ask(client, state, case['问题'])
        records.append({'case':case, 'record':record, 'evaluation':'待人工逐项评价；success仅表示接口正常完成'})
        (folder/'records.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
        if record['status'] != 'success':
            print('本题未完整成功，停止后续请求，不重试。')
            break
    return records


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--course-sites', action='store_true', help='选择8道课程网站匹配及适用性题')
    parser.add_argument('--only', nargs='+')
    args = parser.parse_args(argv)
    cases = load_cases(args.course_sites)
    if args.only:
        if set(args.only) - {c['编号'] for c in cases}:
            parser.error('--only编号必须属于所选题组；课程网站题需加--course-sites。')
        cases = [c for c in cases if c['编号'] in args.only]
    sizes = offline_preflight(cases)
    print(json.dumps(sizes, ensure_ascii=False, indent=2))
    tokens = sum(x['local_content_tokens'] for x in sizes)
    output_tokens = len(cases)*800
    print(f'正文离线合计约{tokens}tokens，不含包装；最多{len(cases)}×800输出tokens。')
    print(f'按全未命中估计输入+满额输出约{(tokens+output_tokens*4)/1e6:.3f}—{(tokens*2+output_tokens*8)/1e6:.3f}元；不是硬性费用上限。')
    if not args.run:
        print(f'仅离线预检，未读取密钥、未发请求。--run才发送所选{len(cases)}题，遇失败即停，不重试。')
        return 0
    key, base, model = assistant.read_settings()
    if urlparse(base).hostname != 'api.deepseek.com' or model != 'deepseek-flash':
        raise ValueError('模型或服务商与本轮已核对的规格和价格不一致，先核对配置。')
    folder = PROJECT_DIR/'experiment_results'/('learning-resources-' + datetime.now(assistant.BEIJING).strftime('%Y%m%d-%H%M%S-%f'))
    folder.mkdir(parents=True)
    metadata = {'checked_on':'2026-10-15', 'model':model, 'base_host':urlparse(base).hostname,
                'pricing_source':'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/',
                'pricing_cny_per_million_off_peak_peak':{'cache_hit':[.02,.04], 'cache_miss':[1,2], 'output':[4,8]},
                'preflight':sizes, 'selected_case_ids':[c['编号'] for c in cases],
                'case_group':'course_sites' if args.course_sites else 'baseline',
                'snapshots':snapshot(folder, course_sites=args.course_sites),
                'note':'所选题各一次，非流式记录usage。与旧T06的模型版本、知识内容及输出模式并非严格单变量对照；不用于速度因果比较。'}
    (folder/'metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    print('模型：'+model+f'；{len(cases)}次以内，不重试。')
    with OpenAI(api_key=key, base_url=base, timeout=45, max_retries=0) as client:
        records = run_cases(client, model, cases, folder)
    print('记录位置：'+str(folder/'records.json'))
    return 0 if len(records)==len(cases) and all(r['record']['status']=='success' for r in records) else 1


if __name__ == '__main__': raise SystemExit(main())
