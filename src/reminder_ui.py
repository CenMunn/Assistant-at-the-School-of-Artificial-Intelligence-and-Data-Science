from response_delivery import delivery_event
"""网页提醒区域；聊天只作说明，创建和取消须在此确认。"""

from datetime import datetime, timedelta
from html import escape
import sqlite3

import gradio as gr

from assistant import BEIJING
from reminder_service import (ReminderSession, beijing_now, make_reminder_mail,
    preview_reminder, clear_reminder_preview, confirm_reminder,
    preview_cancellation, confirm_cancellation)


STATUS_NAMES = {'pending': '待执行', 'sending': '发送中', 'accepted': '服务器已接受',
                'failed': '发送失败', 'unknown': '状态不明', 'cancelled': '已取消', 'missed': '已错过'}
CONDITIONS = ('仅在Python服务运行、电脑唤醒且网络可用时尝试发送。关闭网页不停止提醒；'
              '退出程序或关机后无法准时提醒。重启时已过期、或运行中迟到超过60秒的任务不补发；'
              '失败及状态不明均不自动重试。')


def format_time(stamp):
    return datetime.fromtimestamp(stamp, BEIJING).strftime('%Y-%m-%d %H:%M:%S')


def test_reminder_fields(now):
    # 输入精度到分钟，向上取整，避免“两分钟后”实际只剩一分钟。
    due = now.astimezone(BEIJING) + timedelta(minutes=2)
    if due.second or due.microsecond:
        due = due.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return '信息学院助理定时提醒测试', due.strftime('%Y-%m-%d'), due.strftime('%H:%M')


def reminder_preview_html(draft):
    mail = make_reminder_mail({'owner': draft.owner, 'recipient': draft.recipient,
                              'item': draft.item, 'due_at': draft.due_at, 'id': '创建后生成'})
    return ('<section class="reminder-preview"><h3>确认这条提醒</h3>'
            f'<p>收件邮箱：{escape(mail.recipient)}<br>时间：{format_time(draft.due_at)}'
            '（北京时间 UTC+08:00）<br>重复：仅一次</p>'
            f'<p>邮件主题：{escape(mail.subject)}</p><pre>{escape(mail.body)}</pre>'
            f'<p>{CONDITIONS}</p></section>')


def reminder_rows(service):
    if service is None:
        return []
    return [[task['id'], task['item'], task.get('recipient') or task['owner'], format_time(task['due_at']),
             STATUS_NAMES.get(task['status'], '状态不明'), task['detail'],
             format_time(task['updated_at']), task['message_id'],
             '' if task['total_seconds'] is None else f"{task['total_seconds']:.2f}秒"]
            for task in service.store.list_tasks(service.owner)]


class ReminderActions:
    def __init__(self, service):
        self.service = service

    def refresh(self):
        try:
            rows = reminder_rows(self.service)
            choices = [(f"{row[3]}　{row[1][:24]}", row[0]) for row in rows if row[4] == '待执行']
            status = self.service.status_text if self.service else '提醒不可用：请检查本机邮箱配置或数据库权限并重启。'
            return rows, gr.update(choices=choices, value=None), escape(status)
        except (sqlite3.Error, OSError, ValueError):
            return [], gr.update(choices=[], value=None), '提醒记录读取失败，请检查目录权限。未执行发送或取消。'

    def preview(self, item, date, clock, state, *, recipient=None):
        clear_reminder_preview(state)
        try:
            if self.service is None or not self.service.ready:
                raise ValueError('提醒不可用，请检查服务状态并重启。')
            if recipient == '':
                raise ValueError('请先在左侧“个人资料”中填写提醒收件邮箱。')
            draft = preview_reminder(state, self.service.owner, item, date, clock, self.service.now(), recipient=recipient)
            return gr.update(visible=True), reminder_preview_html(draft), '请核对收件邮箱、事项和时间，再确认创建。'
        except ValueError as error:
            return gr.update(visible=False), '', escape(str(error))

    def invalidate(self, state):
        clear_reminder_preview(state)
        return gr.update(visible=False), '内容已修改，请重新预览。'

    def fill_test(self, state):
        clear_reminder_preview(state)
        now = self.service.now() if self.service else beijing_now()
        return [*test_reminder_fields(now), gr.update(visible=False),
                '测试内容已填入，尚未创建。时间已向上取整到分钟，请核对后预览。']

    def create(self, item, date, clock, state, *, recipient=None):
        try:
            if recipient == '':
                raise ValueError('请先在左侧“个人资料”中填写提醒收件邮箱。')
            task = confirm_reminder(state, self.service, item, date, clock, recipient=recipient)
            message = f"提醒已创建，编号：{task['id']}；计划时间：{format_time(task['due_at'])}（北京时间）。"
        except ValueError as error:
            message = str(error)
        except (OSError, sqlite3.Error):
            message = '创建结果无法确认，请先刷新任务列表核对，勿重新预览后重复创建。'
        rows, choices, _ = self.refresh()
        return gr.update(visible=False), escape(message), rows, choices

    def preview_cancel(self, task_id, state):
        try:
            if not task_id:
                raise ValueError('请先选择一条待执行提醒。')
            task = preview_cancellation(state, self.service, task_id)
            html = f"<p>取消提醒：{escape(task['item'])}<br>收件邮箱：{escape(task.get('recipient') or task['owner'])}<br>计划时间：{format_time(task['due_at'])}<br>编号：{task['id']}</p>"
            return gr.update(visible=True), html, '确认后不再发送；记录仍保留。'
        except ValueError as error:
            return gr.update(visible=False), '', escape(str(error))
        except (OSError, sqlite3.Error):
            return gr.update(visible=False), '', '未能读取取消目标，请刷新列表。'

    def invalidate_cancel(self, state):
        with state.lock:
            state.cancellation_id = ''
        return gr.update(visible=False)

    def cancel(self, task_id, state):
        try:
            with state.lock:
                if not task_id or task_id != state.cancellation_id:
                    raise ValueError('取消目标已改变，请重新预览取消信息。')
                cancelled = confirm_cancellation(state, self.service)
            message = '提醒已取消，记录保留。' if cancelled else '任务已被领取或不再待执行，未能取消。请查看结果。'
        except ValueError as error:
            message = str(error)
        except (OSError, sqlite3.Error):
            message = '取消结果无法确认，请刷新列表核对。'
        rows, choices, _ = self.refresh()
        return gr.update(visible=False), escape(message), rows, choices


def mount_reminder_panel(service, app=None, standalone=False, app_state=None, revision=None):
    from profile_ui import PanelBinding, guarded_event
    actions = ReminderActions(service)
    initial_rows, initial_choices, initial_status = actions.refresh()
    panel = (gr.Column(elem_id='reminder-panel', min_width=0) if standalone else
             gr.Accordion('定时提醒', open=False, elem_id='reminder-panel'))
    with panel:
        state = app_state if app_state is not None else gr.State(lambda: ReminderSession())
        gr.Markdown('邮件提醒 · 北京时间 UTC+08:00 · 仅一次。收件邮箱取自左侧个人资料，创建前请核对。下表是本机管理记录（含以前的收件地址），不是认证后的个人账号记录。')
        recipient_view = gr.Textbox(value='' if app_state is not None else (service.owner if service else ''), label='提醒收件邮箱（在个人资料中修改）',
                   interactive=False, elem_id='reminder-owner')
        item = gr.Textbox(label='提醒事项', lines=2, max_lines=6, elem_id='reminder-item')
        with gr.Row():
            date = gr.Textbox(label='日期（YYYY-MM-DD）', placeholder='例如：2026-10-13', elem_id='reminder-date')
            clock = gr.Textbox(label='时间（HH:MM）', placeholder='例如：20:00', elem_id='reminder-time')
        with gr.Row():
            fill = gr.Button('填入两分钟后的测试提醒', elem_id='reminder-test', interactive=service is not None)
            preview = gr.Button('预览提醒', variant='primary', elem_id='reminder-preview-button', interactive=service is not None)
        with gr.Group(visible=False, elem_id='reminder-confirmation') as confirmation:
            preview_html = gr.HTML()
            with gr.Row():
                confirm = gr.Button('确认创建提醒', variant='primary', elem_id='reminder-confirm')
                back = gr.Button('取消，继续编辑')
        status = gr.HTML(initial_status, elem_id='reminder-status')
        gr.Markdown(CONDITIONS, elem_classes='measurement-note')
        gr.Markdown('提醒事项及记录保存在本机；请勿填写密码、密钥等秘密。清空聊天不删除提醒。', elem_classes='subtle')
        table = gr.Dataframe(value=initial_rows,
            headers=['编号', '事项', '收件邮箱', '计划时间（北京时间）', '状态', '结果说明', '结果更新时间', '邮件编号', '发信耗时'],
            datatype=['str'] * 9, interactive=False, wrap=True, label='本机提醒记录', elem_id='reminder-table')
        refresh = gr.Button('刷新提醒列表', elem_id='reminder-refresh')
        selected = gr.Dropdown(choices=initial_choices['choices'], label='选择待执行提醒',
                               allow_custom_value=False, elem_id='reminder-selection')
        ask_cancel = gr.Button('预览取消信息', elem_id='reminder-cancel-preview')
        with gr.Group(visible=False, elem_id='reminder-cancellation') as cancellation:
            cancel_html = gr.HTML()
            with gr.Row():
                cancel = gr.Button('确认取消提醒', elem_id='reminder-cancel')
                keep = gr.Button('保留提醒')
    def adapt(fn, uses_state=True, uses_recipient=False):
        if app_state is None:
            return fn
        def adapted(*args):
            current = args[-1]
            if uses_recipient:
                return fn(*args[:-1], current.reminder, recipient=current.profile.reminder_email)
            return fn(*args[:-1], current.reminder) if uses_state else fn()
        return guarded_event(adapted)
    def bind(event, fn, inputs, outputs, **options):
        if app_state is None:
            return event(fn, inputs, outputs, **options)
        return delivery_event(event, fn, inputs, outputs, **options)
    suffix = [state, revision] if app_state is not None else [state]
    fields = [item, date, clock, *suffix]
    bind(preview.click, adapt(actions.preview, uses_recipient=True), fields, [confirmation, preview_html, status], api_name=False, show_progress='hidden', concurrency_id='session-actions')
    bind(fill.click, adapt(actions.fill_test), suffix, [item, date, clock, confirmation, status], api_name=False, show_progress='hidden', concurrency_id='session-actions')
    for field in (item, date, clock):
        bind(field.input, adapt(actions.invalidate), suffix, [confirmation, status], api_name=False, show_progress='hidden', concurrency_id='session-actions')
    bind(back.click, adapt(actions.invalidate), suffix, [confirmation, status], api_name=False, show_progress='hidden', concurrency_id='session-actions')
    bind(confirm.click, adapt(actions.create, uses_recipient=True), fields, [confirmation, status, table, selected],
                  trigger_mode='once', concurrency_limit=1, api_name=False, show_progress='hidden')
    bind(refresh.click, adapt(actions.refresh, False), suffix if app_state is not None else [], [table, selected, status], api_name=False, show_progress='hidden', concurrency_id='session-actions')
    bind(ask_cancel.click, adapt(actions.preview_cancel), [selected, *suffix], [cancellation, cancel_html, status], api_name=False, show_progress='hidden', concurrency_id='session-actions')
    bind(selected.input, adapt(actions.invalidate_cancel), suffix, [cancellation], api_name=False, show_progress='hidden', concurrency_id='session-actions')
    bind(keep.click, adapt(actions.invalidate_cancel), suffix, [cancellation], api_name=False, show_progress='hidden', concurrency_id='session-actions')
    bind(cancel.click, adapt(actions.cancel), [selected, *suffix], [cancellation, status, table, selected],
                 trigger_mode='once', concurrency_limit=1, api_name=False, show_progress='hidden')
    if app is not None:
        app.load(actions.refresh, [], [table, selected, status], api_name=False, show_progress='hidden')
    return PanelBinding([item, date, clock, confirmation, preview_html, status, selected, cancellation, cancel_html, recipient_view],
                        lambda email='': ['', '', '', gr.update(visible=False), '', '草稿已清空；本机已创建提醒仍保留，收件地址不变。',
                                 gr.update(value=None), gr.update(visible=False), '', email])
