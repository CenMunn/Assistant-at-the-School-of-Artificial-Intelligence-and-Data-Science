"""信息学院助理的本地网页入口；模型调用仍由assistant.py完成。"""

import base64
from html import escape
import os
from pathlib import Path
from urllib.parse import urlparse

import gradio as gr
from openai import OpenAI

import assistant
from app_session import new_app_session, operation, request_profile, resolve_profile, ensure_chat_mutation
from session_profile import Profile, validate_profile, model_background
from profile_ui import mount_profile_ui, profile_summary, guarded_event
from response_delivery import delivery_event
from dataclasses import asdict
from datetime import datetime
from mail_service import (MailSession, read_mail_settings, preview_mail, cancel_mail,
                          deliver_mail, send_mail, save_mail_log)
from reminder_service import ReminderService, ReminderStore
from reminder_ui import mount_reminder_panel
from exam_store import ExamStore
from exam_ui import mount_exam_panel
from teacher_ui import mount_teacher_panel
from teacher_mail import teacher_choices, teacher_contact, fill_teacher_recipient
from conversation_store import ConversationStore
from conversation_service import bind_history
from conversation_ui import mount_conversation_controls, bind_conversation_events, archive_values
from chat_edit_ui import mount_chat_edit_ui, bind_chat_edit_ui
import sqlite3
from web_session import (new_session, iter_reply, snapshot, request_settings,
                         resolve_settings, clear_session)


from project_paths import PROJECT_DIR, IMAGE_DIR, RESOURCE_DIR
from ui_assets import image_data


def header_html():
    logo = base64.b64encode((IMAGE_DIR/'uibe-logo.png').read_bytes()).decode('ascii')
    return ('<a class="skip-link" href="#module-navigation">跳到功能导航</a>'
            '<header class="college-header">'
            f'<img src="data:image/png;base64,{logo}" width="180" height="129" '
            'alt="对外经济贸易大学校徽及中英文校名">'
            '<div class="college-heading"><h1>信息学院助理</h1>'
            '<p>课程、学习与校园事务</p></div>'
            '<span class="local-label">本地运行</span></header>')


def render_frame(frame):
    record = frame['record'] or {}
    settings = frame['settings']
    busy = frame['busy']
    pending = bool(frame['pending'])
    editing = bool(frame.get('edit_view', {}).get('editing'))
    enabled = not (busy or pending or editing)
    labels = {'success': '回答完成', 'error': '请求未完成', 'incomplete': '回答不完整，未计入历史'}
    status = frame['status']
    if record and not busy and status == '尚未发送请求':
        status = record.get('status', status)
    status = labels.get(status, status)
    if record.get('status') == 'error' and record.get('error_message'):
        status += '：' + record['error_message']
    if record and not record.get('log_saved', False):
        status += '；日志未保存，请检查本地目录权限'
    if record.get('dropped_rounds', 0):
        status += f'；已从模型上下文裁剪最早的{record["dropped_rounds"]}轮历史，页面记录仍保留'
    if record.get('cleanup_warning'):
        status += '；' + record['cleanup_warning']
    if frame.get('archive_error'):
        status += '；' + frame['archive_error']

    def seconds(key):
        value = record.get(key)
        return '—' if value is None else f'{value:.2f}秒'

    timing = (f'{status}　|　首段收到耗时：{seconds("first_text_seconds")}　|　'
              f'总耗时：{seconds("total_seconds")}　|　历史：{frame["history_rounds"]}轮') if record else (
                  status if busy or status != '尚未发送请求' else '')
    return {
        'chat': [dict(message, content=escape(message['content'], quote=False)) if message['role'] == 'user'
                 else message for message in frame['messages']],
        'status': f'<div role="status" aria-live="polite">{escape(timing)}</div>',
        'send': gr.update(interactive=enabled, value='正在回答…' if busy else '发送'),
        'question': gr.update(interactive=enabled),
        'prompt': gr.update(value=settings['prompt_version'], interactive=enabled),
        'stream': gr.update(value=settings['stream'], interactive=enabled),
        'history': gr.update(value=settings['history_enabled'], interactive=enabled),
        'temperature': gr.update(value=settings['temperature'], interactive=enabled),
        'clear': gr.update(interactive=enabled),
        'confirmation': gr.update(visible=pending),
        'edit_tools': frame.get('edit_view', {'nodes': [], 'token': '', 'action': None}),
    }


def resolve_action(session, accepted):
    if session.pending_action == 'clear' and accepted:
        clear_session(session, True)
    else:
        resolve_settings(session, accepted)


def mail_preview_html(draft):
    return ('<section class="mail-preview"><h3>确认这封邮件</h3>'
            f'<p>发件人：{escape(draft.sender)}<br>收件人：{escape(draft.recipient)}<br>'
            f'主题：{escape(draft.subject)}</p><pre>{escape(draft.body)}</pre></section>')


def mail_result_html(record):
    labels = {'accepted': '服务器已接受', 'failed': '发送失败', 'unknown': '发送状态不明'}
    text = labels.get(record['status'], record['status']) + '：' + record['detail']
    if record.get('total_seconds') is not None:
        text += f' 耗时：{record["total_seconds"]:.2f}秒。'
    if record.get('message_id'):
        text += f' 邮件编号：{record["message_id"]}'
    if record.get('log_saved') is False:
        text += ' 本地邮件日志未保存。'
    return f'<div role="status" aria-live="polite">{escape(text)}</div>'


def self_test_fields(mail_settings):
    if mail_settings is None:
        raise ValueError('请先配置发件邮箱。')
    now = datetime.now(assistant.BEIJING).strftime('%Y-%m-%d %H:%M:%S %z')
    return (mail_settings.username, '[实验2] 信息学院助理邮件发送测试',
            '这是一封发给本人邮箱的课程实验测试邮件。\n'
            '用于验证本地网页的邮件编辑、预览确认及 SMTP 发送流程。\n'
            f'测试时间：{now}\n无需回复。')


def switch_workspace(module):
    if module not in ('chat', 'mail', 'reminders', 'exams', 'teachers'):
        raise ValueError('未知功能模块。')
    # 只改变可见性，不重建控件、不读写会话，也不触发发送或提醒操作。
    title = {'chat': '与小智对话', 'mail': '发送邮件', 'reminders': '定时提醒',
             'exams': '考试安排', 'teachers': '教师资料'}[module]
    return [gr.update(visible=True if module == name else 'hidden',
                      elem_classes=[] if module == name else ['module-inactive'])
            for name in ('chat', 'mail', 'reminders', 'exams', 'teachers', 'chat')] + [
        gr.update(variant='primary' if module == name else 'secondary')
        for name in ('chat', 'mail', 'reminders', 'exams', 'teachers')] + [
        f'<span role="status" aria-live="polite" aria-atomic="true">当前功能：{title}</span>']


def build_app(model, extra_body, client_factory, mail_settings=None,
              mail_sender=send_mail, mail_logger=save_mail_log, reminder_service=None,
              exam_store=None, exam_service=None, conversation_store=None, conversation_error=''):
    def initial_session():
        current = new_app_session(model, extra_body)
        bind_history(current.chat, conversation_store)
        return current
    with gr.Blocks(title='信息学院助理', analytics_enabled=False, fill_width=True) as app:
        session = gr.State()
        initialized = app.load(initial_session, [], session, queue=False, api_name=False, show_progress='hidden')
        revision = gr.Number(value=0, visible=False, elem_id='profile-revision')
        gr.HTML(header_html(), elem_id='college-brand')
        if conversation_error:
            gr.HTML('<div role="alert">' + escape(conversation_error) + '</div>', elem_id='history-unavailable')
        with gr.Row(elem_id='workspace'):
            with gr.Column(scale=0, min_width=240, elem_id='settings-panel'):
                gr.Markdown('## 工作台', elem_id='navigation-title')
                with gr.Column(elem_id='module-navigation', min_width=0):
                    nav_chat = gr.Button('与小智对话', variant='primary', elem_id='nav-chat')
                    nav_teachers = gr.Button('教师资料', elem_id='nav-teachers')
                    nav_mail = gr.Button('发送邮件', elem_id='nav-mail')
                    nav_reminders = gr.Button('定时提醒', elem_id='nav-reminders')
                    nav_exams = gr.Button('考试安排', elem_id='nav-exams')
                module_status = gr.HTML(switch_workspace('chat')[-1], elem_id='module-status')
                with gr.Column(min_width=0, elem_id='profile-entry'):
                    profile_status = gr.HTML(profile_summary(Profile()), elem_id='profile-status', js_on_load=None)
                    profile_open = gr.Button('个人资料', elem_id='profile-open')
            with gr.Column(visible=True, scale=0, elem_id='chat-settings', min_width=0) as chat_settings:
                archive = mount_conversation_controls(conversation_store)
            with gr.Column(scale=1, min_width=240, elem_id='main-panel'):
                with gr.Column(visible=True, elem_id='chat-workspace', min_width=0) as chat_workspace:
                    with gr.Row(elem_id='chat-toolbar'):
                        gr.Button('会话', scale=0, min_width=44, elem_id='history-toggle')
                        archive['heading'].render()
                        gr.Button('设置', scale=0, min_width=60, elem_id='chat-options-toggle')
                    with gr.Column(elem_id='chat-options', min_width=0):
                        gr.Markdown('### 对话设置')
                        gr.Textbox(value=model, label='模型', interactive=False, elem_id='model-name')
                        prompt = gr.Dropdown(['v1', 'v2', 'v3'], value='v3', label='Prompt 版本',
                                             allow_custom_value=False, elem_id='prompt-version')
                        stream = gr.Checkbox(value=True, label='流式输出', elem_id='stream-setting')
                        history = gr.Checkbox(value=True, label='携带上下文', elem_id='history-setting')
                        gr.Markdown('控制模型是否参考前文，不影响本机聊天保存。', elem_classes='subtle')
                        temperature = gr.Slider(0, 2, value=.3, step=.1, label='温度', elem_id='temperature-setting')
                        clear = gr.Button('清空当前对话', elem_id='clear-chat')
                        with gr.Accordion('使用与隐私说明', open=False, elem_id='chat-help'):
                            gr.Markdown('更改温度、上下文或 Prompt 前，会确认是否清空对话。'
                                '问答会保存到本机，请勿输入密码或密钥。此列表为本机共用，并非账号隔离。'
                                '\n\n耗时由 Python 端测量，不包含完整的浏览器渲染时间。'
                                '刷新后可从历史列表手动打开已保存对话；个人资料需重新填写。'
                                '关闭页面不等于取消平台处理。清空对话会清空当前会话的全部消息版本，不删除原实验日志。'
                                '\n\n邮件须预览确认后发送；提醒须在独立区域确认创建；实时检索尚未接入。', elem_classes='measurement-note')
                    chat = gr.Chatbot(value=[], label='对话', height=400, elem_id='chat',
                        placeholder=f'<div class="welcome"><img src="{image_data("assistant-avatar.png")}" alt="小智">'
                                    '<h2>你好，我是小智</h2><p>从课程、学习，聊到你的校园生活。</p></div>',
                        avatar_images=tuple({'path': image_data(name), 'url': image_data(name)}
                                            for name in ('user-avatar.png', 'assistant-avatar.png')),
                        show_label=False,
                        sanitize_html=True, allow_tags=False, allow_file_downloads=False,
                        buttons=['copy'], layout='bubble', group_consecutive_messages=False, autoscroll=False)
                    edit_tools = mount_chat_edit_ui()
                    with gr.Group(visible=False, elem_id='reset-confirmation') as confirmation:
                        confirm_text = gr.Markdown('确认清空当前会话的全部消息版本、模型历史及本机聊天档案内容？原实验日志保留。')
                        with gr.Row():
                            confirm = gr.Button('确认清空并继续', variant='primary')
                            cancel = gr.Button('取消，保留会话')
                    with gr.Row(elem_id='composer'):
                        question = gr.Textbox(label='输入问题', show_label=False, placeholder='向小智提问…',
                            lines=2, max_lines=5, scale=1, elem_id='question', interactive=True)
                        send = gr.Button('发送', variant='primary', scale=0, min_width=64, elem_id='send')
                    status = gr.HTML(render_frame(snapshot(new_session(model, extra_body)))['status'], elem_id='run-status')
                with gr.Column(visible='hidden', elem_id='mail-panel', elem_classes=['module-inactive'], min_width=0) as mail_workspace:
                    gr.Markdown('## 发送邮件')
                    mail_session = session
                    gr.Markdown('写好邮件，预览核对后再发送。支持单个收件人。', elem_classes='workspace-intro')
                    gr.Textbox(value=mail_settings.username if mail_settings else '尚未配置发件邮箱',
                               label='发件邮箱', interactive=False, elem_id='mail-from')
                    mail_to = gr.Textbox(label='收件邮箱', placeholder='完整邮箱地址', elem_id='mail-to')
                    with gr.Accordion('快捷填写教师邮箱', open=True, elem_id='teacher-mail-picker'):
                        with gr.Row():
                            mail_teacher = gr.Dropdown(teacher_choices(), label='选择教师', value=None,
                                allow_custom_value=False, filterable=True, elem_id='mail-teacher')
                            teacher_email = gr.Dropdown([], label='公开邮箱', value=None,
                                allow_custom_value=False, interactive=False, elem_id='mail-teacher-email')
                        teacher_source = gr.HTML('', elem_id='mail-teacher-source')
                        teacher_fill = gr.Button('填入收件邮箱', interactive=False, elem_id='mail-teacher-fill')
                    mail_subject = gr.Textbox(label='邮件主题', max_lines=1, elem_id='mail-subject')
                    mail_body = gr.Textbox(label='邮件正文', lines=7, max_lines=16, elem_id='mail-body')
                    with gr.Row():
                        fill_test = gr.Button('填入发给自己的测试邮件', elem_id='mail-self-test')
                        mail_preview = gr.Button('预览邮件', variant='primary', elem_id='mail-preview-button')
                    with gr.Group(visible=False, elem_id='mail-confirmation') as mail_confirmation:
                        mail_preview_text = gr.HTML()
                        with gr.Row():
                            mail_confirm = gr.Button('确认发送邮件', variant='primary', elem_id='mail-confirm')
                            mail_cancel = gr.Button('取消，继续编辑', elem_id='mail-cancel')
                    ready = bool(mail_settings and mail_settings.password)
                    mail_status = gr.HTML('<div role="status">' + (
                        '发件配置已读取，尚未发送邮件。' if ready else
                        '请在本机配置邮箱凭据并重新启动；密码不要填入聊天或邮件正文。') + '</div>', elem_id='mail-status')
                with gr.Column(visible='hidden', elem_id='reminder-workspace', elem_classes=['module-inactive'], min_width=0) as reminder_workspace:
                    gr.Markdown('## 定时提醒')
                    reminder_binding = mount_reminder_panel(reminder_service, app, standalone=True, app_state=session, revision=revision)
                with gr.Column(visible='hidden', elem_id='exam-workspace', elem_classes=['module-inactive'], min_width=0) as exam_workspace:
                    exam_binding = mount_exam_panel(exam_store, exam_service, session, revision)
                with gr.Column(visible='hidden', elem_id='teacher-workspace', elem_classes=['module-inactive'], min_width=0) as teacher_workspace:
                    mount_teacher_panel()
        archive['confirmation'].render()
        profile = mount_profile_ui(session, loading=True)
        gr.HTML('<footer>课程实验 · 非学校官方服务</footer>', elem_id='page-footer')

        navigation_outputs = [chat_workspace, mail_workspace, reminder_workspace, exam_workspace, teacher_workspace, chat_settings,
                              nav_chat, nav_mail, nav_reminders, nav_exams, nav_teachers, module_status]
        for button, module in ((nav_chat, 'chat'), (nav_mail, 'mail'), (nav_reminders, 'reminders'),
                               (nav_exams, 'exams'), (nav_teachers, 'teachers')):
            button.click(lambda selected=module: switch_workspace(selected), [], navigation_outputs,
                         queue=False, api_name=False, show_progress='hidden')

        def preview_email(to, subject, body, current):
            try:
                draft = preview_mail(current.mail, mail_settings, to, subject, body)
                return gr.update(visible=True), mail_preview_html(draft), '请核对收件人、主题和正文，再确认发送。'
            except ValueError as error:
                return gr.update(visible=False), '', escape(str(error))

        def invalidate_email(current):
            try:
                cancel_mail(current.mail)
                return gr.update(visible=False), '草稿已更新，请重新预览。'
            except ValueError as error:
                return gr.update(), escape(str(error))

        def select_teacher(teacher_id, current):
            if teacher_id is None:
                return gr.update(choices=[], value=None, interactive=False), '', gr.update(interactive=False)
            row = teacher_contact(teacher_id)
            emails = row['emails']
            text = '未收录明确的公开邮箱。' if not emails else '选择后点击填入；不会自动发送。'
            if len(emails) > 1:
                text = '该教师有多个公开邮箱，请选择一个，再点击填入。'
            source = (f'<p>{escape(text)} <a href="{escape(row["source_url"], quote=True)}" '
                      'target="_blank" rel="noopener noreferrer">查看官网资料</a></p>')
            return (gr.update(choices=emails, value=emails[0] if len(emails) == 1 else None,
                              interactive=bool(emails)), source, gr.update(interactive=bool(emails)))

        def fill_teacher(teacher_id, address, current):
            address = fill_teacher_recipient(current, teacher_id, address)
            return address, gr.update(visible=False), '', '收件邮箱已填入，请预览并核对后发送。'

        def fill_self_test(current):
            try:
                cancel_mail(current.mail)
                fields = self_test_fields(mail_settings)
                return [*fields, gr.update(visible=False), '测试邮件已填入，请预览确认。']
            except ValueError as error:
                return [gr.update(), gr.update(), gr.update(), gr.update(), escape(str(error))]

        mail_controls = [mail_to, mail_subject, mail_body, fill_test, mail_preview, mail_confirm, mail_cancel]

        def submit_email(to, subject, body, current, version):
            try:
                with operation(current, version):
                    yield ['正在连接邮件服务器，请等待结果…', gr.update()] + [gr.update(interactive=False)] * len(mail_controls)
                    record = deliver_mail(current.mail, mail_settings, to, subject, body,
                                          sender=mail_sender, logger=mail_logger)
                    result = mail_result_html(record)
                    yield [result, gr.update(visible=False)] + [gr.update(interactive=True)] * len(mail_controls)
            except ValueError as error:
                raise gr.Error(str(error)) from None

        mail_inputs = [mail_to, mail_subject, mail_body, mail_session, revision]
        delivery_event(mail_teacher.input, guarded_event(select_teacher), [mail_teacher, session, revision],
                       [teacher_email, teacher_source, teacher_fill], api_name=False, show_progress='hidden')
        delivery_event(teacher_fill.click, guarded_event(fill_teacher), [mail_teacher, teacher_email, session, revision],
                       [mail_to, mail_confirmation, mail_preview_text, mail_status], api_name=False, show_progress='hidden')
        delivery_event(mail_preview.click, guarded_event(preview_email), mail_inputs, [mail_confirmation, mail_preview_text, mail_status],
                           api_name=False, show_progress='hidden')
        delivery_event(fill_test.click, guarded_event(fill_self_test), [mail_session, revision],
                        [mail_to, mail_subject, mail_body, mail_confirmation, mail_status],
                        api_name=False, show_progress='hidden')
        for field in (mail_to, mail_subject, mail_body):
            delivery_event(field.input, guarded_event(invalidate_email), [mail_session, revision], [mail_confirmation, mail_status],
                        api_name=False, show_progress='hidden')
        delivery_event(mail_cancel.click, guarded_event(invalidate_email), [mail_session, revision], [mail_confirmation, mail_status],
                          api_name=False, show_progress='hidden')
        delivery_event(mail_confirm.click, submit_email, mail_inputs, [mail_status, mail_confirmation, *mail_controls],
                           trigger_mode='once', concurrency_limit=1, api_name=False, show_progress='hidden')

        parts = {'chat': chat, 'status': status, 'send': send, 'question': question,
                 'prompt': prompt, 'stream': stream, 'history': history, 'temperature': temperature,
                 'clear': clear, 'confirmation': confirmation, 'edit_tools': edit_tools}
        outputs = list(parts.values())

        def to_outputs(frame, reset_input=False):
            values = render_frame(frame)
            if reset_input:
                values['question']['value'] = ''
            return [values[key] for key in parts]

        def send_message(text, current, version):
            try:
                with operation(current, version):
                    for frame in iter_reply(current.chat, text, client_factory, model_background(current.profile)):
                        yield to_outputs(frame, reset_input=frame['busy'] and frame['record'] is None)
            except ValueError as error:
                raise gr.Error(str(error)) from None

        def change_settings(version, use_stream, use_history, temp, current):
            proposed = dict(prompt_version=version, stream=use_stream, history_enabled=use_history, temperature=temp)
            with current.lock:
                try:
                    ensure_chat_mutation(current)
                    result = request_settings(current.chat, proposed)
                    message = '请确认参数修改；确认后会清空当前会话。' if result == 'confirm' else '设置已更新。'
                except ValueError as error:
                    message = str(error)
            details = f'新设置：Prompt {version}，温度 {temp}，历史{"开启" if use_history else "关闭"}。'
            return to_outputs(snapshot(current.chat, message)) + [details + ' 确认后清空当前会话的全部消息版本、模型历史及本机聊天档案内容，原实验日志保留。']

        def ask_clear(current):
            with current.lock:
                try:
                    ensure_chat_mutation(current)
                    done = clear_session(current.chat, False)
                    message = '会话已清空。' if done else '请确认是否清空当前会话。'
                except ValueError as error:
                    message = str(error)
            return to_outputs(snapshot(current.chat, message)) + ['确认清空当前会话的全部消息版本、模型历史及本机聊天档案内容？原实验日志保留。']

        def confirm_change(current, accepted):
            with current.lock:
                try:
                    ensure_chat_mutation(current)
                    resolve_action(current.chat, accepted)
                    message = '已确认，会话已清空。' if accepted else '已取消，原会话与设置保留。'
                except ValueError as error:
                    message = str(error)
            return to_outputs(snapshot(current.chat, message))

        send_dependency = delivery_event(send.click, send_message, [question, session, revision], outputs, concurrency_limit=None,
                   trigger_mode='once', show_progress='hidden', api_name=False)
        for control in (prompt, stream, history, temperature):
            delivery_event(control.input, guarded_event(change_settings), [prompt, stream, history, temperature, session, revision],
                          outputs + [confirm_text], concurrency_limit=None, api_name=False, show_progress='hidden')
        delivery_event(clear.click, guarded_event(ask_clear), [session, revision], outputs + [confirm_text], api_name=False, show_progress='hidden')
        delivery_event(chat.clear, guarded_event(ask_clear), [session, revision], outputs + [confirm_text], api_name=False, show_progress='hidden')
        delivery_event(confirm.click, guarded_event(lambda current: confirm_change(current, True)), [session, revision], outputs, api_name=False, show_progress='hidden')
        delivery_event(cancel.click, guarded_event(lambda current: confirm_change(current, False)), [session, revision], outputs, api_name=False, show_progress='hidden')
        archive_refresh = bind_conversation_events(archive, conversation_store, app, session, revision, question, outputs, to_outputs)
        bind_chat_edit_ui(edit_tools, session, revision, outputs, to_outputs, client_factory, archive_refresh, archive['outputs'])
        def history_ready():
            return [gr.update(interactive=True) for _ in range(3)]
        if archive_refresh:
            loaded = delivery_event(initialized.then, archive_refresh, [session, revision], archive['outputs'],
                                    api_name=False, show_progress='hidden', queue=False)
            loaded.then(history_ready, [], [profile['save'], profile['skip'], profile['clear']],
                        queue=False, api_name=False, show_progress='hidden')
            delivery_event(send_dependency.then, archive_refresh, [session, revision], archive['outputs'],
                           api_name=False, show_progress='hidden')
        else:
            initialized.then(history_ready, [], [profile['save'], profile['skip'], profile['clear']],
                             queue=False, api_name=False, show_progress='hidden')
        # 资料更新统一输出到各工作区，页面草稿与服务端预览同时清理。
        mail_reset_outputs = [mail_to, mail_subject, mail_body, mail_confirmation, mail_preview_text, mail_status]
        reset_outputs = outputs + mail_reset_outputs + reminder_binding.outputs + exam_binding.outputs + archive['outputs']
        profile_outputs = [profile['dialog'], profile['confirmation'], profile['info'], profile_status, revision,
                           *profile['fields'], profile['consent'], profile['save'], profile['skip'], *reset_outputs]

        def profile_result(current, visible=False, confirm_pending=False, message='', reset=False):
            displayed = current.pending_profile if confirm_pending and current.pending_profile is not None else current.profile
            values = [gr.update(value=value, interactive=not confirm_pending) for value in asdict(displayed).values()]
            updates = [gr.update(visible=visible), gr.update(visible=confirm_pending), escape(message),
                       profile_summary(current.profile), current.revision, *values,
                       gr.update(value='保存资料' if current.entered else '填写资料并进入', interactive=not confirm_pending),
                       gr.update(value='取消，保留原资料' if current.entered else '暂不填写')]
            if reset:
                updates += (to_outputs(snapshot(current.chat, '个人资料已更新，旧对话已清空。'), reset_input=True)
                            + ['', '', '', gr.update(visible=False), '', '草稿已清空，尚未发送邮件。']
                            + reminder_binding.reset_values(current.profile.reminder_email)
                            + exam_binding.reset_values(current.profile.reminder_email)
                            + archive_values(conversation_store, current))
            else:
                updates += [gr.update() for _ in reset_outputs]
            return updates

        def check_profile_version(current, version):
            if current.revision != version:
                raise gr.Error('资料已改变，请重新打开个人资料。')

        def open_profile(current, version):
            with current.lock:
                check_profile_version(current, version)
                return profile_result(current, visible=True, confirm_pending=current.pending_profile is not None)

        def save_profile(*args):
            current, version = args[-2:]
            values = dict(zip(asdict(Profile()), args[:-2]))
            with current.lock:
                check_profile_version(current, version)
                try:
                    proposed = validate_profile(values)
                    result = request_profile(current, proposed, current.entered)
                except ValueError as error:
                    # 保留用户正在编辑的内容，不误显示保存成功。
                    current.pending_profile = None
                    updates = [gr.update() for _ in profile_outputs]
                    updates[1], updates[2] = gr.update(visible=False), escape(str(error))
                    for index in range(5, 5 + len(profile['fields']) + 2):
                        updates[index] = gr.update(interactive=True)
                    return updates
                if result != 'confirm':
                    current.entered = True
                return profile_result(current, visible=result=='confirm', confirm_pending=result=='confirm',
                                      message='请确认清理旧内容。' if result=='confirm' else '资料已保存。',
                                      reset=result=='applied')

        def clear_profile(current, version):
            with current.lock:
                check_profile_version(current, version)
                try:
                    result = request_profile(current, Profile(), current.entered)
                except ValueError as error:
                    raise gr.Error(str(error)) from None
                if result != 'confirm':
                    current.entered = True
                return profile_result(current, visible=result=='confirm', confirm_pending=result=='confirm',
                                      message='请确认清除。' if result=='confirm' else '现在以访客使用。',
                                      reset=result=='applied')

        def finish_profile(current, version, accepted=False):
            with current.lock:
                check_profile_version(current, version)
                try:
                    applied = resolve_profile(current, accepted)
                except ValueError as error:
                    raise gr.Error(str(error)) from None
                current.entered = True
                return profile_result(current, message='资料已更新。' if applied else '继续使用当前资料。', reset=applied)

        def accept_profile(*args):
            current, version = args[-2:]
            with current.lock:
                check_profile_version(current, version)
                try:
                    displayed = validate_profile(dict(zip(asdict(Profile()), args[:-2])))
                    if displayed != current.pending_profile:
                        raise ValueError('资料已修改，请重新保存并确认。')
                except ValueError as error:
                    current.pending_profile = None
                    return profile_result(current, visible=True, message=str(error))
                return finish_profile(current, version, True)

        profile_opts = dict(api_name=False, show_progress='hidden', queue=False, profile_event=True)
        delivery_event(profile_open.click, open_profile, [session, revision], profile_outputs, **profile_opts)
        delivery_event(profile['save'].click, save_profile, [*profile['fields'], profile['consent'], session, revision], profile_outputs, **profile_opts)
        delivery_event(profile['clear'].click, clear_profile, [session, revision], profile_outputs, **profile_opts)
        delivery_event(profile['skip'].click, finish_profile, [session, revision], profile_outputs, **profile_opts)
        delivery_event(profile['accept'].click, accept_profile, [*profile['fields'], profile['consent'], session, revision], profile_outputs, **profile_opts)
        delivery_event(profile['reject'].click, finish_profile, [session, revision], profile_outputs, **profile_opts)
    return app


def launch_options():
    return dict(server_name='127.0.0.1', server_port=7860, share=False,
                show_error=False, allowed_paths=[], blocked_paths=[str(PROJECT_DIR)],
                strict_cors=True, enable_monitoring=False, mcp_server=False,
                footer_links=[], css_paths=[str(RESOURCE_DIR/'web.css')],
                js=(RESOURCE_DIR/'profile.js').read_text(encoding='utf-8'),
                theme=gr.themes.Base(font=['Microsoft YaHei UI', 'Arial', 'sans-serif'],
                                    font_mono=['Consolas', 'monospace']))


def ensure_local_bypass():
    # 仅本进程的回环地址直连，避免系统代理拦截Gradio启动自检。
    for name in ('NO_PROXY', 'no_proxy'):
        rules = [value.strip() for value in os.environ.get(name, '').split(',') if value.strip()]
        os.environ[name] = ','.join(dict.fromkeys(rules + ['127.0.0.1', 'localhost', '::1']))


def make_reminder_service(settings):
    if settings is None:
        return None
    try:
        store = ReminderStore(RESOURCE_DIR / 'reminders' / 'reminders.sqlite3')
        return ReminderService(store, settings)
    except (OSError, sqlite3.Error):
        return None


def make_conversation_store():
    try:
        return ConversationStore(PROJECT_DIR / 'data' / 'conversations.sqlite3'), ''
    except (OSError, sqlite3.Error):
        return None, '历史会话不可用：本次问答不会持久保存。请检查 data/conversations.sqlite3 与目录权限；原文件未删除。'


def main(server_port=7860):
    ensure_local_bypass()
    reminder_service, exam_service, app = None, None, None
    try:
        api_key, base_url, model = assistant.read_settings()
        mail_settings = read_mail_settings()
        if mail_settings and not mail_settings.password:
            import getpass
            import sys
            from dataclasses import replace
            if sys.stdin.isatty():
                password = getpass.getpass('邮箱密码（隐藏输入；直接回车则暂不启用发送）：')
                mail_settings = replace(mail_settings, password=password)
        extra_body = {'thinking': {'type': 'disabled'}} if urlparse(base_url).hostname == 'api.deepseek.com' else {}
        # 密钥只存在于服务端工厂闭包，不进入State、控件或浏览器URL。
        def client_factory():
            return OpenAI(api_key=api_key, base_url=base_url, timeout=45.0, max_retries=0)
        reminder_service = make_reminder_service(mail_settings)
        exam_store = ExamStore(RESOURCE_DIR / 'exams' / 'exams.sqlite3')
        if mail_settings:
            exam_service = ReminderService(exam_store, mail_settings, draft_factory=exam_store.make_mail)
        conversation_store, conversation_error = make_conversation_store()
        app = build_app(model, extra_body, client_factory, mail_settings, reminder_service=reminder_service,
                        exam_store=exam_store, exam_service=exam_service,
                        conversation_store=conversation_store, conversation_error=conversation_error)
        app.queue(max_size=32).launch(**(launch_options() | {'server_port': server_port, 'prevent_thread_lock': True}))
        if reminder_service is not None:
            reminder_service.start()
        if exam_service is not None:
            exam_service.start()
        app.block_thread()
    except (ValueError, OSError, sqlite3.Error):
        print('网页未能启动。请检查项目.env、依赖、资料文件和7860端口；不要公开密钥。')
        return 1
    finally:
        if reminder_service is not None:
            reminder_service.stop()
        if exam_service is not None:
            exam_service.stop()
        if app is not None:
            app.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
