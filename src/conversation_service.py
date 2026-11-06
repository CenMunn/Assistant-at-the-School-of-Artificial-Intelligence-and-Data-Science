"""会话档案与网页状态之间的适配。"""
from copy import deepcopy
import sqlite3

import assistant
import conversation_tree as ct
from conversation_store import RECORD_FIELDS


def bind_history(chat, store):
    chat.archive_store = store


def detach_history(chat):
    chat.archive_id = None
    chat.archive_revision = None
    chat.archive_title = '新对话'
    chat.archive_error = ''
    chat.archive_dirty = False


def persist_chat(chat, strict=False):
    with chat.lock:
        if chat.archive_store is None or not chat.archive_dirty:
            return
        if not chat.messages and chat.archive_id is None:
            chat.archive_dirty = False
            return
        payload = {'messages': deepcopy(chat.messages), 'history': deepcopy(chat.state['history']),
                   'settings': {k: chat.state[k] for k in ('prompt_version', 'temperature', 'stream', 'history_enabled')},
                   'record': {k: v for k, v in chat.last_record.items() if k in RECORD_FIELDS} if chat.last_record else None}
        from web_session import ensure_tree
        ensure_tree(chat)
        payload.update(format_version=2, tree=deepcopy(chat.tree))
        nodes = ct.active_nodes(chat.tree)
        payload['record'] = deepcopy(nodes[-1]['record']) if nodes else None
        try:
            if chat.archive_id is None:
                title = (chat.messages[0]['content'].strip().replace('\n', ' ')[:30] or '新对话')
                row = chat.archive_store.create(payload, title)
            else:
                row = chat.archive_store.save(chat.archive_id, chat.archive_revision, payload)
            chat.archive_id, chat.archive_revision, chat.archive_title = row['id'], row['revision'], row['title']
            chat.archive_dirty = False
            chat.archive_error = ''
        except (ValueError, OSError, sqlite3.Error) as error:
            detail = str(error) if isinstance(error, ValueError) else '请检查本机存储是否可用。'
            chat.archive_error = '历史保存失败：' + detail + ' 当前页面回答仍保留；可先复制，再在管理会话中确认放弃未保存更改。'
            if strict:
                raise ValueError(chat.archive_error + ' 请先复制需要保留的内容，勿直接刷新。') from None


def _idle(app):
    from app_session import _ensure_idle
    _ensure_idle(app)
    if app.pending_profile is not None or app.chat.pending_action is not None or app.pending_conversation is not None or app.pending_edit:
        raise ValueError('请先确认或取消当前修改。')


def _fresh(app, store):
    from web_session import new_session
    chat = new_session(app.chat.state['model'], app.chat.state['extra_body'])
    bind_history(chat, store)
    return chat


def new_conversation(app, store):
    with app.lock:
        _idle(app)
        persist_chat(app.chat, strict=True)
        app.chat = _fresh(app, store)
        app.revision += 1


def discard_unsaved_conversation(app, store):
    with app.lock:
        _idle(app)
        if not app.chat.archive_error or not app.chat.archive_dirty:
            raise ValueError('当前没有保存失败的更改。')
        app.chat = _fresh(app, store)
        app.revision += 1


def open_conversation(app, store, identifier):
    with app.lock:
        _idle(app)
        persist_chat(app.chat, strict=True)
        row = store.get(identifier)
        chat = _fresh(app, store)
        payload = row['payload']
        chat.state.update(deepcopy(payload['settings']))
        chat.state['system_prompt'] = assistant.load_system_prompt(chat.state['prompt_version'])
        chat.state['history'] = deepcopy(payload['history'])
        chat.messages = deepcopy(payload['messages'])
        chat.tree = deepcopy(payload['tree']) if payload.get('format_version') == 2 else ct.from_legacy(
            payload['messages'], payload['history'], payload['record'])
        chat.last_record = deepcopy(payload['record'])
        chat.archive_id, chat.archive_revision, chat.archive_title = row['id'], row['revision'], row['title']
        app.chat = chat
        app.revision += 1


def rename_conversation(app, store, title):
    with app.lock:
        _idle(app)
        persist_chat(app.chat, strict=True)
        if app.chat.archive_id is None: raise ValueError('当前对话尚未保存，请先完成一次问答。')
        row = store.rename(app.chat.archive_id, app.chat.archive_revision, title)
        app.chat.archive_revision, app.chat.archive_title = row['revision'], row['title']


def delete_conversation(app, store, identifier, revision):
    with app.lock:
        _idle(app)
        store.delete(identifier, revision)
        if app.chat.archive_id == identifier:
            app.chat = _fresh(app, store)
            app.revision += 1
