"""浏览会话状态与线程队列，不依赖网页组件。"""

from copy import deepcopy
from dataclasses import dataclass, field
import math
from queue import SimpleQueue
from threading import RLock, Thread
import uuid

import assistant
import conversation_tree as ct


@dataclass
class WebSession:
    state: dict
    messages: list = field(default_factory=list)
    busy: bool = False
    pending_settings: dict | None = None
    pending_action: str | None = None
    last_record: dict | None = None
    tree: dict = field(default_factory=ct.empty_tree)
    view_token: str = field(default_factory=lambda: uuid.uuid4().hex)
    edit_node: str | None = None
    edit_draft: str = ''
    archive_store: object = field(default=None, repr=False)
    archive_id: str | None = None
    archive_revision: int | None = None
    archive_title: str = '新对话'
    archive_error: str = ''
    archive_dirty: bool = False
    lock: RLock = field(default_factory=RLock, repr=False)

    def __deepcopy__(self, memo):
        with self.lock:
            other = WebSession(deepcopy(self.state, memo), deepcopy(self.messages, memo))
            other.state['session_id'] = uuid.uuid4().hex
            other.last_record = deepcopy(self.last_record, memo)
            other.tree = deepcopy(self.tree, memo)
            return other


def new_session(model, extra_body):
    return WebSession({
        'session_id': uuid.uuid4().hex, 'model': model, 'entry': 'web',
        'prompt_version': 'v3', 'system_prompt': assistant.load_system_prompt('v3'),
        'temperature': .3, 'stream': True, 'history_enabled': True, 'history': [],
        'max_chars': 24000, 'max_tokens': 800, 'extra_body': deepcopy(extra_body),
    })


def settings(session):
    return {key: session.state[key] for key in ('prompt_version', 'stream', 'history_enabled', 'temperature')}


def _ensure_idle(session):
    if session.busy:
        raise ValueError('当前请求尚未结束，请稍后操作。')
    if session.edit_node:
        raise ValueError('请先发送或取消正在编辑的消息。')


def ensure_tree(session):
    if not session.tree['nodes'] and session.messages:
        session.tree = ct.from_legacy(session.messages, session.state['history'], session.last_record)


def reset_tree(session):
    session.tree = ct.empty_tree()
    session.edit_node = None
    session.edit_draft = ''
    session.view_token = uuid.uuid4().hex


def tree_view(session):
    if len(session.messages) % 2 == 0:
        ensure_tree(session)
    rows = []
    for node in ct.active_nodes(session.tree):
        versions = ct.sibling_versions(session.tree, node['id'])
        notice = {'error': '此轮请求未完成，未计入上下文。', 'incomplete': '此轮回答不完整，未计入上下文。'}.get(node['status'], '')
        if notice and (node['record'] or {}).get('error_message'):
            notice += ' ' + node['record']['error_message']
        rows.append({'id': node['id'], 'versions': versions, 'index': versions.index(node['id']), 'notice': notice})
    return {'nodes': rows, 'token': session.view_token, 'busy': session.busy,
            'editing': session.edit_node, 'blocked': bool(session.pending_action or session.archive_error),
            'draft': session.edit_draft if session.edit_node else '', 'action': None}


def _apply(session, proposed, reset):
    rules = assistant.load_system_prompt(proposed['prompt_version'])
    session.state.update(proposed)
    session.state['system_prompt'] = rules
    if reset:
        session.state['history'] = []
        session.messages = []
        session.last_record = None
        reset_tree(session)
    session.pending_settings = None
    session.pending_action = None
    _save_archive(session)


def _save_archive(session):
    if session.archive_store is not None:
        from conversation_service import persist_chat
        session.archive_dirty = True
        persist_chat(session)


def request_settings(session, proposed):
    with session.lock:
        _ensure_idle(session)
        current = settings(session)
        if set(proposed) - set(current):
            raise ValueError('不支持的设置。')
        updated = current | proposed
        if updated['prompt_version'] not in ('v1', 'v2', 'v3'):
            raise ValueError('请选择V1、V2或V3。')
        value = float(updated['temperature'])
        if not math.isfinite(value) or not 0 <= value <= 2:
            raise ValueError('温度须在0到2之间。')
        updated['temperature'] = value
        if any(type(updated[key]) is not bool for key in ('stream', 'history_enabled')):
            raise ValueError('开关值无效。')
        reset = any(updated[key] != current[key] for key in ('prompt_version', 'temperature', 'history_enabled'))
        if reset and session.messages:
            session.pending_settings = updated
            session.pending_action = 'settings'
            return 'confirm'
        _apply(session, updated, reset)
        return 'applied'


def resolve_settings(session, accepted):
    with session.lock:
        _ensure_idle(session)
        if accepted and session.pending_settings:
            _apply(session, session.pending_settings, True)
        else:
            session.pending_settings = None
            session.pending_action = None


def clear_session(session, confirmed):
    with session.lock:
        _ensure_idle(session)
        if session.messages and not confirmed:
            session.pending_action = 'clear'
            return False
        session.state['history'] = []
        session.messages = []
        session.last_record = None
        reset_tree(session)
        session.pending_action = None
        session.pending_settings = None
        _save_archive(session)
        return True


def snapshot(session, status=None):
    with session.lock:
        record = deepcopy(session.last_record)
        return {'messages': deepcopy(session.messages), 'busy': session.busy,
                'status': status or ('正在回答' if session.busy else '尚未发送请求'),
                'record': record, 'history_rounds': len(session.state['history']) // 2,
                'pending': session.pending_action, 'settings': settings(session),
                'archive_error': session.archive_error, 'archive_title': session.archive_title,
                'archive_id': session.archive_id, 'edit_view': tree_view(session)}


def iter_reply(session, user_text, client_factory, learning_background=None, *, edit_parent=None, editing=False):
    text = user_text.strip()
    with session.lock:
        if session.busy:
            rejected = snapshot(session, '当前请求尚未结束，请勿重复发送。')
        elif session.pending_action or (session.edit_node and not editing):
            rejected = snapshot(session, '请先确认或取消会话重置。')
        elif not text:
            rejected = snapshot(session, '请输入问题。')
        else:
            ensure_tree(session)
            previous_tree = deepcopy(session.tree)
            previous_messages = deepcopy(session.messages)
            previous_record = session.last_record
            previous_history = deepcopy(session.state['history'])
            node_id = ct.append_turn(session.tree, text, edit_parent)
            rejected = None
            session.busy = True
            frozen = deepcopy(session.state)
            frozen['learning_background'] = deepcopy(learning_background or {})
            if editing:
                frozen['history'] = ct.project_history(session.tree, frozen['history_enabled'])
            session.messages = ct.project_messages(session.tree)
            session.view_token = uuid.uuid4().hex
            session.edit_node = None
            session.last_record = None
    if rejected:
        yield rejected
        return

    events = SimpleQueue()
    started = False

    def display(part='', **kwargs):
        if kwargs.get('end') == '' and part:
            with session.lock:
                session.tree['nodes'][node_id]['assistant'] += part
                session.messages = ct.project_messages(session.tree)
                events.put(snapshot(session))

    def worker():
        result = None
        try:
            with client_factory() as client:
                result = assistant.ask(client, frozen, text, display=display)
        except Exception as error:
            if result is not None:
                # ask已返回并落盘；关闭客户端失败不能推翻已完成的问答结果。
                result['cleanup_warning'] = '客户端清理未完成，本轮问答结果已保留；可重启本地程序释放资源。'
            else:
                result = {'status': 'error', 'error_message': '网页调用未完成，请检查本地配置或依赖。',
                          'error_type': type(error).__name__, 'output': '', 'log_saved': False,
                          'first_text_seconds': None, 'total_seconds': None}
        finally:
            with session.lock:
                session.state['history'] = frozen['history']
                session.last_record = result
                node = session.tree['nodes'][node_id]
                content = node['assistant'] or (result or {}).get('error_message', '未收到正文。')
                ct.finish_turn(session.tree, node_id, content, result)
                session.messages = ct.project_messages(session.tree)
                _save_archive(session)
                session.busy = False
                events.put(snapshot(session, (result or {}).get('status', 'error')))
            events.put(None)

    try:
        yield snapshot(session, '正在等待模型响应…')
        thread = Thread(target=worker, daemon=True)
        thread.start()
        started = True
        while True:
            frame = events.get()
            if frame is None:
                break
            yield frame
    finally:
        # 已启动的请求由worker负责释放；关闭页面不等于平台取消处理。
        if not started:
            with session.lock:
                session.tree = previous_tree
                session.messages = previous_messages
                session.last_record = previous_record
                session.state['history'] = previous_history
                session.view_token = uuid.uuid4().hex
                session.busy = False
