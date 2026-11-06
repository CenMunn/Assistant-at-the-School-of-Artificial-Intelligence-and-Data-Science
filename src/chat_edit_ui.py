"""消息旁编辑工具的事件桥接，稳定节点ID由服务端验证。"""
import gradio as gr
from project_paths import RESOURCE_DIR
from chat_edit_service import begin_edit, cancel_edit, iter_edited_reply, switch_version
from web_session import snapshot
from session_profile import model_background
from response_delivery import delivery_event


def mount_chat_edit_ui():
    return gr.HTML({'nodes': [], 'token': '', 'busy': False, 'editing': None, 'action': None},
        html_template='<span aria-hidden="true"></span>',
        js_on_load=(RESOURCE_DIR / 'chat-keyboard.js').read_text(encoding='utf-8') + '\n' +
                   (RESOURCE_DIR / 'chat-interactions.js').read_text(encoding='utf-8'),
        apply_default_css=False, elem_id='chat-actions')


def bind_chat_edit_ui(bridge, session, revision, outputs, to_outputs, client_factory,
                      archive_refresh=None, archive_outputs=None):
    def chat_action(value, current, version):
        try:
            if current.revision != version:
                raise ValueError('会话或资料已改变，请重新操作。')
            action = value.get('action') if isinstance(value, dict) else None
            if not isinstance(action, dict) or action.get('kind') not in ('begin', 'cancel', 'send', 'switch'):
                raise ValueError('无效的消息操作。')
            kind, node, token = action['kind'], action.get('node_id'), action.get('view_token')
            if not isinstance(node, str) or not isinstance(token, str):
                raise ValueError('消息操作标识无效。')
            if kind == 'begin':
                begin_edit(current, node, token)
            elif kind == 'cancel':
                cancel_edit(current, token)
            elif kind == 'switch':
                switch_version(current, node, token)
            else:
                text = action.get('text')
                if not isinstance(text, str): raise ValueError('消息操作缺少文字。')
                for frame in iter_edited_reply(current, node, text, token, client_factory,
                                                model_background(current.profile)):
                    yield to_outputs(frame)
                return
            yield to_outputs(snapshot(current.chat))
        except ValueError as error:
            yield to_outputs(snapshot(current.chat, str(error)))
    dependency = delivery_event(bridge.input, chat_action, [bridge, session, revision], outputs,
        concurrency_limit=None, trigger_mode='once', api_name=False, show_progress='hidden')
    if archive_refresh:
        delivery_event(dependency.then, archive_refresh, [session, revision], archive_outputs,
                       api_name=False, show_progress='hidden')
    return dependency
