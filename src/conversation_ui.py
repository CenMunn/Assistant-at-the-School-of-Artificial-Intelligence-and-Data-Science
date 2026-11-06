"""本地历史会话控件；改变会话不操作邮件、考试或提醒。"""
from html import escape
import sqlite3
import gradio as gr
from datetime import datetime, timedelta
from assistant import BEIJING

from conversation_service import (new_conversation, open_conversation,
                                  delete_conversation, discard_unsaved_conversation)
from response_delivery import delivery_event
from web_session import snapshot
from project_paths import RESOURCE_DIR


def conversation_list(rows, selected=None, now=None):
    items = []
    today = (now or datetime.now(BEIJING)).astimezone(BEIJING).date()
    group = None
    for row in rows:
        try:
            date = datetime.fromisoformat(row.get('updated_at', '')).astimezone(BEIJING).date()
        except (ValueError, TypeError):
            date = None
        heading = '今天' if date == today else ('昨天' if date == today - timedelta(days=1) else '更早')
        if heading != group:
            items.append(f'<h3 class="conversation-time-group">{heading}</h3>')
            group = heading
        identifier, title = escape(row['id'], quote=True), escape(row['title'], quote=True)
        active = row['id'] == selected
        items.append(
            f'<div class="conversation-row{" selected" if active else ""}">'
            f'<button type="button" class="conversation-link" data-action="open" data-id="{identifier}" '
            f'aria-current="{str(active).lower()}" title="{title}"><span>{title}</span></button>'
            f'<details class="conversation-menu"><summary aria-label="管理会话：{title}" title="管理会话">⋯</summary>'
            f'<div class="conversation-menu-items"><button type="button" data-action="rename" data-id="{identifier}">重命名</button>'
            f'<button type="button" class="danger" data-action="delete" data-id="{identifier}">删除</button></div></details></div>')
    return {'html': '<nav aria-label="历史会话">' + ''.join(items) + '</nav>' if items else
            '<div class="conversation-empty">还没有对话<br><span>发出第一条消息，开始与小智交流</span></div>',
            'action': None, 'current': selected}


def mount_conversation_controls(store):
    with gr.Column(visible=store is not None, elem_id='conversation-controls', min_width=0):
        new = gr.Button('＋ 新建对话', elem_id='conversation-new')
        with gr.Row(elem_id='conversation-label'):
            gr.HTML('<h2>历史对话</h2>', js_on_load=None)
            refresh = gr.Button('刷新', min_width=45, size='sm', elem_id='conversation-refresh')
        selector = gr.HTML(conversation_list([]), html_template='${value.html}',
                           js_on_load=(RESOURCE_DIR/'conversation-list.js').read_text(encoding='utf-8'),
                           apply_default_css=False, elem_id='conversation-list')
        status = gr.HTML('', elem_id='conversation-status', js_on_load=None)
        discard = gr.Button('处理未保存的更改', visible=False, elem_id='conversation-discard')
        with gr.Group(visible=False, render=False, elem_id='conversation-confirmation') as confirmation:
            confirm_text = gr.Markdown('')
            name = gr.Textbox(label='会话名称', max_lines=1, visible=False, elem_id='conversation-name')
            rename = gr.Button('保存名称', visible=False, variant='primary', elem_id='conversation-rename')
            confirm = gr.Button('确认操作', variant='primary', elem_id='conversation-confirm')
            cancel = gr.Button('取消', elem_id='conversation-cancel')
    heading = gr.HTML('<h2>新对话</h2>', render=False, elem_id='conversation-heading', js_on_load=None)
    return dict(new=new, selector=selector, refresh=refresh, status=status, name=name,
                rename=rename, discard=discard, confirmation=confirmation, confirm_text=confirm_text,
                heading=heading,
                confirm=confirm, cancel=cancel,
                outputs=[selector, name, status, confirmation, confirm_text, rename, confirm, discard, heading])


def archive_values(store, current, message=''):
    try:
        rows = store.list() if store else []
        current.conversation_catalog = {row['id']: row for row in rows}
    except (OSError, sqlite3.Error):
        rows = []
        current.conversation_catalog = {}
        message = '读取本机会话失败，请检查存储。'
    selected = current.chat.archive_id if any(row['id'] == current.chat.archive_id for row in rows) else None
    text = current.chat.archive_error or message
    return [conversation_list(rows, selected),
            gr.update(value=current.chat.archive_title if current.chat.archive_id else '', visible=False),
            f'<div role="status">{escape(text)}</div>' if text else '', gr.update(visible=False), '',
            gr.update(visible=False), gr.update(visible=True),
            gr.update(visible=bool(current.chat.archive_error and current.chat.archive_dirty)),
            f'<h2 title="{escape(current.chat.archive_title, quote=True)}">{escape(current.chat.archive_title)}</h2>']


def bind_conversation_events(binding, store, app, state, revision, question, chat_outputs, to_outputs):
    if store is None: return None
    outputs = [*chat_outputs, *binding['outputs'], revision]

    def check(current, version, allow_edit=False):
        if current.revision != version:
            raise gr.Error('会话或资料已改变，请重新操作。')
        if current.pending_edit and not allow_edit:
            raise gr.Error('请先发送或取消正在编辑的消息，再管理会话。')

    def finish(current, message='', changed=False):
        return [*(to_outputs(snapshot(current.chat), reset_input=True) if changed else [gr.update() for _ in chat_outputs]),
                *archive_values(store, current, message), current.revision]

    def show_confirmation(current, message, rename_title=None):
        values = finish(current)
        start = len(chat_outputs)
        values[start + 3], values[start + 4] = gr.update(visible=True), escape(message)
        if rename_title is not None:
            values[start + 1] = gr.update(value=rename_title, visible=True)
            values[start + 5], values[start + 6] = gr.update(visible=True), gr.update(visible=False)
        return values

    def observed_record(current, identifier):
        row = current.conversation_catalog.get(identifier)
        if current.chat.archive_id == identifier:
            row = {'id': identifier, 'title': current.chat.archive_title, 'revision': current.chat.archive_revision}
        if row is None:
            raise gr.Error('请刷新列表后选择会话。')
        latest = next((item for item in store.list() if item['id'] == identifier), None)
        if latest is None or latest['revision'] != row['revision']:
            raise gr.Error('会话已被另一页面改变或删除，请重新打开检查后再操作。')
        return row

    def perform(action, current):
        changed = False
        if action['kind'] == 'new':
            new_conversation(current, store); changed = True
        elif action['kind'] == 'discard':
            discard_unsaved_conversation(current, store); changed = True
        elif action['kind'] == 'open':
            open_conversation(current, store, action['id']); changed = True
        else:
            changed = current.chat.archive_id == action['id']
            delete_conversation(current, store, action['id'], action['record_revision'])
        return finish(current, '会话已删除；原实验日志保留。' if action['kind'] == 'delete' else '', changed)

    def prepare(kind, identifier, draft, current, version):
        check(current, version)
        if current.pending_conversation: raise gr.Error('请先确认或取消当前会话操作。')
        if current.chat.pending_action or current.pending_profile: raise gr.Error('请先确认或取消当前修改。')
        action = {'kind': kind, 'id': identifier, 'app_revision': current.revision}
        if kind == 'delete':
            row = observed_record(current, identifier)
            action['record_revision'] = row['revision']
            message = f'确认删除“{row["title"]}”？聊天档案将从本机移除，原实验日志不会删除。'
        else:
            if kind == 'open': store.get(identifier)
            message = '输入框中还有未发送的内容。确认切换并清空该草稿？已经保存的对话保留。'
        if kind == 'delete' or draft.strip():
            current.pending_conversation = action
            return show_confirmation(current, message)
        return perform(action, current)

    def archive_new(draft, current, version): return prepare('new', None, draft, current, version)
    def archive_open(identifier, draft, current, version): return prepare('open', identifier, draft, current, version)
    def archive_delete(identifier, draft, current, version): return prepare('delete', identifier, draft, current, version)

    def archive_action(payload, draft, current, version):
        check(current, version)
        action = payload.get('action') if isinstance(payload, dict) else None
        if not isinstance(action, dict) or action.get('kind') not in ('open', 'rename', 'delete'):
            raise gr.Error('无效的会话操作，请刷新列表。')
        identifier = action.get('id')
        if not isinstance(identifier, str) or identifier not in current.conversation_catalog:
            raise gr.Error('会话不在当前列表中，请刷新。')
        if current.pending_conversation or current.pending_profile or current.chat.pending_action:
            raise gr.Error('请先确认或取消当前修改。')
        if action['kind'] == 'open':
            if identifier == current.chat.archive_id:
                latest = store.get(identifier)
                if latest['revision'] == current.chat.archive_revision:
                    return finish(current)
            return archive_open(identifier, draft, current, version)
        if action['kind'] == 'delete':
            return archive_delete(identifier, draft, current, version)
        row = observed_record(current, identifier)
        # 每次打开命名弹窗使用新版本，拒绝上一弹窗迟到的保存请求。
        current.revision += 1
        current.pending_conversation = {'kind': 'rename', 'id': identifier,
                                        'record_revision': row['revision'], 'app_revision': current.revision}
        return show_confirmation(current, '重命名会话', row['title'])

    def archive_discard(current, version):
        check(current, version)
        if current.pending_conversation or current.pending_profile or current.chat.pending_action:
            raise gr.Error('请先确认或取消当前修改。')
        if not current.chat.archive_error or not current.chat.archive_dirty:
            raise gr.Error('当前没有保存失败的更改，无需放弃。')
        current.pending_conversation = {'kind': 'discard', 'app_revision': version}
        return show_confirmation(current,
            '请先复制需要保留的回答。确认放弃本页未保存的更改并进入新对话？'
            '此操作无法恢复未保存内容，不会修改数据库中的旧会话。之后可重新打开最新档案。')

    def archive_confirm(current, version):
        check(current, version)
        action = current.pending_conversation
        if not action or action['app_revision'] != version: raise gr.Error('会话确认已失效。')
        if action['kind'] == 'rename': raise gr.Error('请使用保存名称按钮。')
        current.pending_conversation = None
        return perform(action, current)

    def archive_cancel(current, version):
        check(current, version)
        current.pending_conversation = None
        return finish(current, '已取消，会话保留。')

    def archive_rename(title, current, version):
        check(current, version)
        action = current.pending_conversation
        if not action or action['kind'] != 'rename' or action['app_revision'] != version:
            raise gr.Error('命名窗口已失效，请重新打开后保存。')
        row = store.rename(action['id'], action['record_revision'], title)
        if current.chat.archive_id == action['id']:
            current.chat.archive_revision, current.chat.archive_title = row['revision'], row['title']
        current.pending_conversation = None
        return finish(current, '会话已重命名。')

    def archive_refresh(current, version):
        check(current, version, allow_edit=True)
        return archive_values(store, current)

    opts = dict(profile_event=True, api_name=False, show_progress='hidden')
    for control, fn, prefix in ((binding['new'], archive_new, [question]),
        (binding['rename'], archive_rename, [binding['name']]),
        (binding['discard'], archive_discard, []),
        (binding['confirm'], archive_confirm, []), (binding['cancel'], archive_cancel, [])):
        delivery_event(control.click, fn, [*prefix, state, revision], outputs, **opts)
    delivery_event(binding['selector'].input, archive_action, [binding['selector'], question, state, revision],
                   outputs, **opts)
    delivery_event(binding['refresh'].click, archive_refresh, [state, revision], binding['outputs'],
                   api_name=False, show_progress='hidden')
    return archive_refresh
