"""同一会话的编辑分支与版本切换；不调用邮件或提醒。"""
from copy import deepcopy
import uuid
import conversation_tree as ct
from app_session import own_operation_count
from web_session import tree_view, snapshot, iter_reply, ensure_tree, _save_archive


def _guard(app, token, allow_draft=False):
    chat = app.chat
    if token != chat.view_token:
        raise ValueError('消息版本已改变，请刷新后重试。')
    if (chat.busy or app.mail.busy or app.pending_deliveries or
            app.active_operations > own_operation_count(app)):
        raise ValueError('当前操作尚未结束，请稍后操作。')
    if app.pending_profile or app.pending_conversation or chat.pending_action:
        raise ValueError('请先确认或取消当前修改。')
    if chat.archive_error:
        raise ValueError(chat.archive_error)
    if not allow_draft and app.pending_edit:
        raise ValueError('请先发送或取消正在编辑的消息。')
    ensure_tree(chat)


def edit_view(app):
    with app.lock, app.chat.lock:
        return tree_view(app.chat)


def begin_edit(app, node_id, view_token):
    with app.lock, app.chat.lock:
        _guard(app, view_token)
        nodes = {n['id']: n for n in ct.active_nodes(app.chat.tree)}
        if node_id not in nodes:
            raise ValueError('只能编辑当前分支中的用户消息。')
        app.pending_edit = {'node_id': node_id, 'view_token': view_token}
        app.chat.edit_node = node_id
        app.chat.edit_draft = nodes[node_id]['user']
        return nodes[node_id]['user']


def cancel_edit(app, view_token):
    with app.lock, app.chat.lock:
        _guard(app, view_token, True)
        app.pending_edit = None
        app.chat.edit_node = None
        app.chat.edit_draft = ''
        app.chat.view_token = uuid.uuid4().hex


def iter_edited_reply(app, node_id, text, view_token, client_factory, learning_background=None):
    with app.lock, app.chat.lock:
        _guard(app, view_token, True)
        if app.pending_edit != {'node_id': node_id, 'view_token': view_token}:
            raise ValueError('请重新打开要编辑的消息。')
        if not isinstance(text, str) or not text.strip():
            raise ValueError('请输入问题；空消息不会发送。')
        parent = app.chat.tree['nodes'][node_id]['parent']
        app.chat.edit_draft = text
        generator = iter_reply(app.chat, text, client_factory, learning_background,
                               edit_parent=parent, editing=True)
        first = next(generator)
        claimed = ct.active_nodes(app.chat.tree)[-1]['id']
        app.pending_edit = None
    try:
        yield first
        yield from generator
    finally:
        generator.close()
        with app.lock, app.chat.lock:
            if claimed not in app.chat.tree['nodes'] and not app.chat.busy:
                app.chat.edit_node = node_id
                app.chat.edit_draft = text
                app.pending_edit = {'node_id': node_id, 'view_token': app.chat.view_token}


def switch_version(app, node_id, view_token):
    with app.lock, app.chat.lock:
        _guard(app, view_token)
        chat = app.chat
        allowed = {v for n in ct.active_nodes(chat.tree) for v in ct.sibling_versions(chat.tree, n['id'])}
        if node_id not in allowed:
            raise ValueError('请选择当前消息的有效版本。')
        ct.select_version(chat.tree, node_id)
        chat.messages = ct.project_messages(chat.tree)
        chat.state['history'] = ct.project_history(chat.tree, chat.state['history_enabled'])
        nodes = ct.active_nodes(chat.tree)
        chat.last_record = deepcopy(nodes[-1]['record']) if nodes else None
        chat.view_token = uuid.uuid4().hex
        _save_archive(chat)
        return snapshot(chat)
