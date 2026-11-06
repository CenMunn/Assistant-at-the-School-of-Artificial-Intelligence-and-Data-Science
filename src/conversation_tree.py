"""同一会话的问答版本树；只处理数据，不调用网络或数据库。"""
from copy import deepcopy
import math
import re
import uuid

RECORD_FIELDS = {'status', 'log_saved', 'first_text_seconds', 'total_seconds', 'dropped_rounds',
                 'finish_reason', 'error_message', 'cleanup_warning'}
STATUSES = {'pending', 'success', 'error', 'incomplete', 'legacy'}


def empty_tree():
    return {'schema': 1, 'nodes': {}, 'selected': {}}


def validate_record(record):
    if record is None:
        return
    if not isinstance(record, dict) or set(record) - RECORD_FIELDS or record.get('status') not in {'success', 'error', 'incomplete'}:
        raise ValueError('会话运行记录无效。')
    for key, value in record.items():
        if key in ('first_text_seconds', 'total_seconds', 'dropped_rounds'):
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                raise ValueError('会话耗时无效。')
        elif key == 'log_saved':
            if type(value) is not bool:
                raise ValueError('日志状态无效。')
        elif value is not None and (not isinstance(value, str) or len(value) > 4000):
            raise ValueError('会话运行文本无效。')


def validate_tree(tree):
    if (not isinstance(tree, dict) or set(tree) != {'schema', 'nodes', 'selected'}
            or type(tree['schema']) is not int or tree['schema'] != 1
            or not isinstance(tree['nodes'], dict) or not isinstance(tree['selected'], dict)
            or len(tree['nodes']) > 500):
        raise ValueError('会话版本结构无效或超过500轮限制。')
    nodes = tree['nodes']
    parents = set()
    for identifier, node in nodes.items():
        if (not isinstance(identifier, str) or not re.fullmatch('[a-f0-9]{32}', identifier)
                or not isinstance(node, dict) or set(node) != {'id', 'parent', 'user', 'assistant', 'status', 'record'}
                or node['id'] != identifier or not isinstance(node['parent'], str)
                or node['parent'] not in {'root', *nodes}
                or not isinstance(node['user'], str) or not node['user'].strip()
                or not isinstance(node['assistant'], str) or node['status'] not in STATUSES):
            raise ValueError('消息版本节点无效。')
        validate_record(node['record'])
        if node['status'] == 'success' and (not node['assistant'].strip() or not node['record'] or node['record']['status'] != 'success'):
            raise ValueError('成功消息缺少完整结果。')
        if node['status'] in ('error', 'incomplete') and (not node['record'] or node['record']['status'] != node['status']):
            raise ValueError('消息结果与状态不一致。')
        parents.add(node['parent'])
        seen = set()
        cursor = identifier
        while cursor != 'root':
            if cursor in seen:
                raise ValueError('会话版本存在循环。')
            seen.add(cursor)
            cursor = nodes[cursor]['parent']
    if set(tree['selected']) != parents:
        raise ValueError('会话分支选择不完整。')
    for parent, child in tree['selected'].items():
        if not isinstance(child, str) or child not in nodes or nodes[child]['parent'] != parent:
            raise ValueError('会话分支选择无效。')


def active_nodes(tree):
    result = []
    cursor = 'root'
    while cursor in tree['selected']:
        cursor = tree['selected'][cursor]
        result.append(tree['nodes'][cursor])
    return result


def project_messages(tree):
    return [message for n in active_nodes(tree) for message in
            ({'role': 'user', 'content': n['user']}, {'role': 'assistant', 'content': n['assistant']})]


def project_history(tree, enabled):
    return [message for n in active_nodes(tree) if n['status'] == 'success' for message in
            ({'role': 'user', 'content': n['user']}, {'role': 'assistant', 'content': n['assistant']})] if enabled else []


def sibling_versions(tree, node_id):
    if node_id not in tree['nodes']:
        raise ValueError('消息已改变，请重新打开会话。')
    parent = tree['nodes'][node_id]['parent']
    return [key for key, n in tree['nodes'].items() if n['parent'] == parent]


def select_version(tree, node_id):
    if node_id not in tree['nodes']:
        raise ValueError('消息已改变，请重新打开会话。')
    cursor = node_id
    while cursor != 'root':
        parent = tree['nodes'][cursor]['parent']
        tree['selected'][parent] = cursor
        cursor = parent


def append_turn(tree, user_text, parent_id=None):
    validate_tree(tree)
    if not isinstance(user_text, str) or not user_text.strip():
        raise ValueError('请输入问题。')
    if len(tree['nodes']) >= 500:
        raise ValueError('会话已达500轮版本限制，请新建对话；旧版本保留。')
    path = active_nodes(tree)
    parent = parent_id if parent_id is not None else (path[-1]['id'] if path else 'root')
    if parent != 'root' and parent not in tree['nodes']:
        raise ValueError('原消息已改变，请重新操作。')
    identifier = uuid.uuid4().hex
    tree['nodes'][identifier] = {'id': identifier, 'parent': parent, 'user': user_text.strip(),
                                'assistant': '', 'status': 'pending', 'record': None}
    select_version(tree, identifier)
    return identifier


def finish_turn(tree, node_id, assistant_text, record):
    node = tree['nodes'][node_id]
    safe = {k: deepcopy(v) for k, v in record.items() if k in RECORD_FIELDS}
    validate_record(safe)
    node.update(assistant=assistant_text, status=safe['status'], record=safe)


def from_legacy(messages, history, record):
    tree = empty_tree()
    proven = set()
    cursor = len(messages) - 2
    if record and record.get('status') in ('error', 'incomplete'):
        cursor -= 2
    for i in range(len(history) - 2, -1, -2):
        while cursor >= 0 and messages[cursor:cursor+2] != history[i:i+2]:
            cursor -= 2
        if cursor < 0:
            raise ValueError('旧会话上下文与消息不一致，不能恢复。')
        proven.add(cursor)
        cursor -= 2
    for i in range(0, len(messages), 2):
        identifier = append_turn(tree, messages[i]['content'])
        node = tree['nodes'][identifier]
        node['assistant'] = messages[i+1]['content']
        node['status'] = 'success' if i in proven and node['assistant'].strip() else 'legacy'
        node['record'] = {'status': 'success'} if node['status'] == 'success' else None
        if i == len(messages)-2 and record:
            safe = {k: deepcopy(v) for k, v in record.items() if k in RECORD_FIELDS}
            validate_record(safe)
            node['record'] = safe
            node['status'] = safe['status']
    validate_tree(tree)
    recovered = [message for index, node in enumerate(active_nodes(tree)) if index * 2 in proven and node['status'] == 'success'
                 for message in ({'role': 'user', 'content': node['user']}, {'role': 'assistant', 'content': node['assistant']})]
    if recovered != history:
        raise ValueError('旧会话成功历史无法完整恢复，原档案未修改。')
    return tree
