"""按学习主题提供已核对的资源信息；不代替模型判断适合程度。"""
import json
import re

from project_paths import TEXT_DIR


COURSE_TOPICS = {
    'algorithms': ('数据结构', '算法分析', '算法入门', '算法课程', '算法学习', '学算法', '算法网站', '算法资源', '算法教程', '链表', '栈与队列', '二分查找'),
    'os': ('操作系统', 'operating system'),
    'networks': ('计算机网络', 'tcp/ip', 'tcp协议', '网络协议', 'computer networking'),
    'ml': ('机器学习', '深度学习', 'machine learning', 'deep learning'),
    'linear_algebra': ('线性代数', '矩阵', 'linear algebra'),
    'probability': ('概率', '数理统计', '统计学', 'probability'),
    'sql': ('sql', '数据库'),
    'architecture': ('计算机组成', '计算机体系结构', '数字逻辑', 'nand2tetris'),
}


def resource_question(question, history):
    explicit = [w for words in COURSE_TOPICS.values() for w in words] + ['python', '编程', '数学']
    if any(w in question.casefold() for w in explicit):
        return question
    for message in reversed((history or [])[-12:]):
        if message.get('role') != 'user':
            continue
        previous = message.get('content', '')[:1000]
        if re.search(r'校园网|报修|老师|教师|邮箱|提醒|报名|政策|保研|考试时间|考试安排|考场', previous):
            break
        if any(w in previous.casefold() for w in explicit):
            return previous + '\n' + question
    return question


def relevant_resources(question, limit=1550, history=None):
    if not re.search(r'推荐|资源|教材|教程|自学|怎么学|如何学|开始学|入门|复习|学习材料|学习计划|学习任务|小任务|学\s*Python|练习平台|网站|免费|找.*书', question, re.I):
        return ''
    question = resource_question(question, history)
    text = question.casefold()
    primary = [topic for topic, words in COURSE_TOPICS.items() if any(word in text for word in words)]
    goal_text = '，'.join(part for part in re.split(r'[，,。；;！？!?]', text)
                         if re.search(r'推荐|自学|想学|正在学|复习(?!过)|学习网站|学习资料|准备资源', part))
    goal_topics = [topic for topic, words in COURSE_TOPICS.items() if any(word in goal_text for word in words)]
    primary = goal_topics or primary
    if 'ml' in primary and 'algorithms' in primary:
        focus = goal_text if goal_topics else text
        if not any(word in focus for word in ('数据结构', '链表', '栈与队列', '二分查找')):
            primary.remove('algorithms')
    topics = set(primary)
    if 'python' in text or (not primary and any(word in text for word in ('编程', 'c语言'))):
        topics.add('python')
    if not {'linear_algebra', 'probability'}.intersection(topics) and any(word in text for word in ('数学', '代数', '统计')):
        topics.add('math')
    if 'ml' in topics:
        topics.update(('python', 'math'))
    if not topics:
        return ''
    catalog = json.loads((TEXT_DIR / 'learning_resources.json').read_text(encoding='utf-8'))
    candidates = [r for r in catalog['resources'] if topics.intersection(r['topics'])]
    if primary and primary[0] == 'ml':
        needs_basics = re.search(r'薄弱|零基础|基础差|没.*基础|不太会|补基础', text)
        ordered_ids = ('LR07', 'LR05', 'LR01', 'LR02', 'LR03', 'LR13', 'LR06') if needs_basics else (
            'LR13', 'LR07', 'LR14', 'LR15', 'LR03', 'LR01', 'LR05', 'LR02', 'LR06')
        order = {resource_id: rank for rank, resource_id in enumerate(ordered_ids)}
        candidates.sort(key=lambda r: order.get(r['id'], 99))
    elif primary and primary[0] == 'sql':
        order = {'LR04': 0, 'LR16': 1, 'LR08': 2}
        candidates.sort(key=lambda r: order.get(r['id'], 99))
    elif primary:
        order = {topic: rank for rank, topic in enumerate(primary)}
        candidates.sort(key=lambda r: min((order.get(t, 99) for t in r['topics']), default=99))
    result = ('\n\n# 本次学习资源参考\n'
              f"人工核查日期{catalog['checked_on']}；是资料快照，不是小智刚联网或已运行练习。"
              '仅按用户需求选1—2项并给原网址；这些是通用建议，学院教材另依据C01。'
              '外校课表、考核、截止时间不适用于本校；不保证网络可达或全站服务免费。\n')
    note = '\n其余候选未附；不要编造未提供资源的费用、登录和设备条件。'
    blocks = []
    omitted = False
    for r in candidates:
        block = (f"\n[{r['id']}] {r['name']} {r['url']}\n"
                 f"{r['language']}；适合：{r['audience']}。{r['cost']}。{r['account']}。"
                 f"设备：{r['device']}。用法：{r['use']}。\n")
        if len(result) + len(block) <= limit - len(note):
            result += block
            blocks.append(block)
        else:
            omitted = True
    if not blocks:
        return ''
    return result + (note if omitted else '')
