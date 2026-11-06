"""考试规则问答复测；默认只展示题目，--run才请求当前配置的模型。"""

import argparse
import csv
from datetime import datetime
import json
from urllib.parse import urlparse

from openai import OpenAI
import assistant
from project_paths import PROJECT_DIR, TEXT_DIR
from web_session import new_session


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    with (TEXT_DIR/'考试规则复测.csv').open(encoding='utf-8', newline='') as handle:
        cases = list(csv.DictReader(handle))
    for case in cases:
        print(case['编号'], case['问题'])
    if not args.run:
        print('仅展示题目，未读取密钥、未发送请求。--run将逐题请求模型，按平台计费。')
        return 0
    key, base, model = assistant.read_settings()
    extra = {'thinking': {'type': 'disabled'}} if urlparse(base).hostname == 'api.deepseek.com' else {}
    folder = PROJECT_DIR/'experiment_results'/('exam-rules-' + datetime.now(assistant.BEIJING).strftime('%Y%m%d-%H%M%S-%f'))
    folder.mkdir(parents=True)
    records = []
    with OpenAI(api_key=key, base_url=base, timeout=45, max_retries=0) as client:
        for case in cases:
            state = new_session(model, extra).state
            state.update(stream=False, history_enabled=False, temperature=.3, max_tokens=500, test_id=case['编号'])
            print('\n', case['编号'], case['问题'])
            record = assistant.ask(client, state, case['问题'])
            records.append({'case':case, 'record':record, 'evaluation':'待逐题核对规则一致性'})
            (folder/'records.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
            if record['status'] != 'success':
                print('本次未正常完成，停止后续请求，不自动重试。')
                break
    print('复测记录：', folder/'records.json')
    return 0 if len(records) == len(cases) and all(x['record']['status']=='success' for x in records) else 1


if __name__ == '__main__':
    raise SystemExit(main())
