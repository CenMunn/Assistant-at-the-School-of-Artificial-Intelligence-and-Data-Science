from response_delivery import delivery_event
"""本地考试工作区。个人资料仅供本机测试，公开登录另行接入。"""

from datetime import datetime
from html import escape
from uuid import uuid4

import gradio as gr

from assistant import BEIJING
from exam_import import parse_excel, parse_notice, student_id, digest_json, term_label, room_label
from exam_store import PRESETS, parse_offsets
from reminder_service import creation_admission
from exam_session import ExamSession, ImportPreview
from app_session import operation
from session_profile import require_reminder_email


def require_student(app):
    if not app.profile.student_id:
        raise ValueError('请先在左侧“个人资料”中填写学号。姓名可不填。')
    return app.profile.student_id


def query_personal_exams(app, store, term):
    with operation(app):
        return store.query(require_student(app), term)


def _parse_import(app, kind, term, path, text, year):
    if kind == 'personal':
        return parse_notice(text, require_student(app), term, year)
    if kind == 'excel':
        if not path:
            raise ValueError('请选择学校Excel文件。')
        return parse_excel(path, term)
    raise ValueError('不支持的导入类型。')


def preview_import(app, store, kind, term, path='', text='', year=None):
    with operation(app), app.exam.lock:
        app.exam.imports.pop(kind, None)
        draft = _parse_import(app, kind, term, path, text, year)
        signature = digest_json([app.profile.student_id, kind, term, path, text, year])
        preview = ImportPreview(draft, signature, app.profile_revision, app.profile.student_id)
        app.exam.imports[kind] = preview
        return preview


def confirm_import(app, store, kind, term, path='', text='', year=None):
    with operation(app), app.exam.lock:
        preview = app.exam.imports.get(kind)
        signature = digest_json([app.profile.student_id, kind, term, path, text, year])
        if not preview or preview.revision != app.profile_revision or preview.signature != signature:
            raise ValueError('资料、学期或个人资料已改变，请重新预览。')
        draft = _parse_import(app, kind, term, path, text, year)
        if draft.digest != preview.draft.digest:
            raise ValueError('文件内容已改变，请重新预览。')
        result = store.activate(draft)
        app.exam.imports.pop(kind, None)
        app.exam.plan, app.exam.plan_signature, app.exam.token = None, '', ''
        return result


def time_text(value):
    return datetime.fromtimestamp(value, BEIJING).strftime('%Y-%m-%d %H:%M:%S')


def exam_table(rows):
    return [[x['course'], x['start'], x['end'], x['method'], room_label(x),
             x['source'], x['room_source'] or '未提供'] for x in rows]


def task_table(rows):
    labels = {'pending':'待执行', 'sending':'发送中', 'accepted':'服务器已接受',
              'failed':'失败', 'unknown':'状态不明', 'missed':'已过期未补发',
              'invalidated':'资料更新已作废', 'cancelled':'已取消'}
    return [[x['id'], x['item'], f"提前{x['offset_minutes']}分钟", time_text(x['due_at']),
             labels.get(x['status'], x['status']), x['recipient'] or x['owner'], x['detail']] for x in rows]


def preview_html(plan):
    lines = [f"收件邮箱：{plan['recipient']}；有效{len(plan['tasks'])}条，过期跳过{len(plan['expired'])}条。"]
    for label, rows in [('待创建', plan['tasks']), ('已过期', plan['expired'])]:
        lines.extend(f"{label}｜{x['exam']['course']}｜提前{x['offset']}分钟｜{time_text(x['due_at'])}（北京时间）" for x in rows)
    return '<pre class="exam-preview">' + escape('\n'.join(lines)) + '</pre>'


def mount_exam_panel(store, service, app_state=None, revision=None):
    from profile_ui import PanelBinding, guarded_event
    if app_state is None:
        from app_session import new_app_session
        app_state = gr.State(lambda: new_app_session('test', {}))
    if revision is None:
        revision = gr.Number(value=0, visible=False)
    gr.Markdown('## 考试安排')
    gr.Markdown('学号统一取自左侧“个人资料”；规则与制度问题仍可在“与小智对话”中提问。')
    if store is None:
        gr.Markdown('考试资料库不可用，请检查本地目录权限后重启。')
        return PanelBinding([], lambda email='': [])
    gr.Markdown('本地验证：自填学号不是身份验证；提醒收件邮箱取自个人资料，创建前请核对。')
    term_choices = [(term_label(c), c) for c in ['2026-2027-1','2025-2026-2','2025-2026-1','2024-2025-2','2024-2025-1']]
    with gr.Row():
        term = gr.Dropdown(choices=term_choices, value='2025-2026-2', label='查询学期', allow_custom_value=True, elem_id='exam-term')
        query = gr.Button('查询我的考试', variant='primary', elem_id='exam-query')
    query_status = gr.HTML('请在个人资料中填写学号，再查询。', elem_id='exam-query-status')
    headers = ['课程','开始时间','结束时间','方式','考场','安排来源','考场来源']
    results = gr.Dataframe(headers=headers, value=[], interactive=False, type='array', elem_id='exam-results')
    gr.Markdown('未提供个人考场时显示“未提供”，不按上课教室推测；楼宇简称按已提供的对应关系解释。')
    selections = gr.CheckboxGroup(choices=[], label='勾选需要提醒的考试', elem_id='exam-selection')
    with gr.Row():
        offsets = gr.CheckboxGroup(choices=PRESETS, value=['1天','1小时'], label='提前提醒（可多选）')
        custom = gr.Textbox(label='自定义提前量', placeholder='例如：2天,6小时,45分钟', elem_id='exam-custom')
    recipient_view = gr.Textbox(value='', label='提醒收件邮箱（在个人资料中修改）', interactive=False, elem_id='exam-recipient')
    preview = gr.Button('预览考试提醒', variant='primary', elem_id='exam-preview')
    with gr.Group(visible=False) as confirmation:
        preview_text = gr.HTML()
        confirm = gr.Button('确认创建这些邮件提醒', variant='primary', elem_id='exam-confirm')
    notice = gr.HTML('历史考试可查询；过去的提醒时间不会创建。')
    gr.Markdown('提醒依赖本地程序运行、电脑唤醒和网络可用。')
    with gr.Accordion('当前学号的考试提醒记录（本地测试）', open=False):
        refresh = gr.Button('刷新提醒记录')
        gr.Markdown('包含当前学号以前确认的收件地址；修改个人资料不会改投已有提醒。')
        tasks = gr.Dataframe(headers=['编号','课程','提前量','计划时间（北京时间）','状态','收件邮箱','说明'], value=[], interactive=False, type='array')
        cancel_choice = gr.Dropdown(choices=[], label='选择待取消提醒')
        cancel_preview = gr.Button('预览取消')
        cancel_text = gr.HTML(elem_id='exam-cancel-text')
        cancel_confirm = gr.Button('确认取消该提醒', visible=False)
    reset_outputs = [results,selections,query_status,confirmation,preview_text,notice,tasks,cancel_choice,cancel_text,cancel_confirm,custom,offsets,recipient_view]
    def base_reset(email=''):
        return [[],gr.update(choices=[],value=[]),'个人资料已更新，请重新查询。',gr.update(visible=False),'','',[],
                gr.update(choices=[],value=None),'',gr.update(visible=False),'',['1天','1小时'],email]
    import_resets = []
    opts = dict(api_name=False, show_progress='hidden', concurrency_id='session-actions')
    def bind(button, fn, inputs, outputs):
        delivery_event(button.click, guarded_event(fn), [*inputs,app_state,revision], outputs, **opts)

    def lookup(selected_term, app):
        rows = query_personal_exams(app, store, selected_term)
        app.exam.plan, app.exam.plan_signature, app.exam.token = None,'',''
        historical = bool(rows) and all(x['start'] < datetime.now(BEIJING).strftime('%Y-%m-%d %H:%M') for x in rows)
        message = f'查询到{len(rows)}门考试。' + ('这些是历史安排。' if historical else '') if rows else '未找到此学号的考试安排，请核对学期及资料。'
        return exam_table(rows),gr.update(choices=[(f"{x['course']} · {x['start']}",x['id']) for x in rows],value=[]),escape(term_label(selected_term)+'：'+message),gr.update(visible=False)

    def reminders_preview(selected_term, ids, preset, extra, app):
        current = app.exam
        current.plan, current.plan_signature, current.token = None,'',''
        if not service or not service.ready:
            raise ValueError('请先配置发信邮箱并启动提醒服务。')
        recipient = require_reminder_email(app.profile)
        sid = require_student(app)
        plan = store.preview_tasks(sid,selected_term,ids,parse_offsets(preset,extra),recipient,int(service.now().timestamp()),owner=service.owner)
        current.plan = plan
        current.plan_signature = digest_json([app.profile_revision,sid,selected_term,ids,preset,extra,recipient,service.owner])
        current.token = uuid4().hex
        return gr.update(visible=True),preview_html(plan),gr.update(interactive=bool(plan['tasks'])),'请核对提醒时间和数量。'

    def reminders_confirm(selected_term, ids, preset, extra, app):
        current = app.exam
        if service is None:
            raise ValueError('考试提醒服务不可用。')
        recipient = require_reminder_email(app.profile)
        signature = digest_json([app.profile_revision,require_student(app),selected_term,ids,preset,extra,recipient,service.owner])
        if current.plan is None or signature != current.plan_signature:
            raise ValueError('选择已修改，请重新预览。')
        if service is None:
            raise ValueError('考试提醒服务不可用。')
        rows = store.create_tasks(current.plan,current.token,int(service.now().timestamp()),admission=lambda: creation_admission(service))
        current.plan, current.token = None,''
        return f'已确认{len(rows)}条提醒，请刷新记录查看。',gr.update(visible=False)

    def refresh_tasks(app):
        if service is None:
            raise ValueError('考试提醒服务不可用。')
        rows = store.personal_tasks(require_student(app),owner=service.owner)
        return task_table(rows),gr.update(choices=[(f"{x['item']} · {time_text(x['due_at'])}",x['id']) for x in rows if x['status']=='pending'],value=None)

    def preview_cancel(tid, app):
        app.exam.cancel_id = ''
        if service is None:
            raise ValueError('考试提醒服务不可用。')
        task = next((x for x in store.personal_tasks(require_student(app),owner=service.owner) if x['id']==tid and x['status']=='pending'),None)
        if task is None:
            raise ValueError('请选择当前学号的待执行提醒。')
        app.exam.cancel_id = tid
        return escape(f"取消：{task['item']}，{time_text(task['due_at'])}，收件邮箱：{task['recipient'] or task['owner']}？"),gr.update(visible=True)

    def confirm_cancel(tid, app):
        if not tid or tid != app.exam.cancel_id:
            raise ValueError('选择已改变，请重新预览取消。')
        if service is None:
            raise ValueError('考试提醒服务不可用。')
        ok = store.cancel_personal(require_student(app),None,tid,int(datetime.now(BEIJING).timestamp()),owner=service.owner)
        app.exam.cancel_id = ''
        return '已取消，未发送。' if ok else '任务已领取或状态改变，请刷新。',gr.update(visible=False)

    bind(query,lookup,[term],[results,selections,query_status,confirmation])
    inputs = [term,selections,offsets,custom]
    bind(preview,reminders_preview,inputs,[confirmation,preview_text,confirm,notice])
    bind(confirm,reminders_confirm,inputs,[notice,confirmation])
    bind(refresh,refresh_tasks,[],[tasks,cancel_choice])
    bind(cancel_preview,preview_cancel,[cancel_choice],[cancel_text,cancel_confirm])
    bind(cancel_confirm,confirm_cancel,[cancel_choice],[cancel_text,cancel_confirm])

    def import_panel(kind):
        personal = kind == 'personal'
        gr.Markdown('粘贴学校发给你的个人考试通知；学号取自统一资料。先预览再确认，不会自动创建提醒。' if personal else
                    '仅本地可信管理员操作；更新整学期版本会使相关未发送提醒作废。此标签不是身份认证。')
        source_term = gr.Dropdown(choices=term_choices,value='2025-2026-2',label='资料所属学期',allow_custom_value=True)
        if personal:
            body = gr.Textbox(label='粘贴我的考试通知',lines=7,elem_id='personal-exam-notice',placeholder='课程名\n闭卷笔试\n求205\n07-03 08:00 - 10:00')
            year = gr.Textbox(label='通知中的考试年份',placeholder='例如2026，请核实后填写')
            inputs = [source_term,body,year]
        else:
            file = gr.File(label='学校考试总表（20MB以内）',file_types=['.xls','.xlsx'],type='filepath')
            inputs = [source_term,file]
        preview_button = gr.Button('预览个人通知' if personal else '预览学校总表',variant='primary')
        status = gr.HTML('',elem_id='personal-import-status' if personal else 'school-import-status')
        table = gr.Dataframe(headers=headers,value=[],interactive=False,type='array',elem_id='personal-import-rows' if personal else 'school-import-rows')
        confirm_button = gr.Button('确认导入个人通知' if personal else '确认更新学校总表',visible=False)
        def parse_args(args):
            return args[0], dict(text=args[1],year=args[2]) if personal else dict(path=args[1])
        def show_import(*args):
            app = args[-1]
            selected_term, params = parse_args(args[:-1])
            p = preview_import(app,store,kind,selected_term,**params)
            mine = [x for x in p.draft.rows if x['student_id']==app.profile.student_id]
            message = f'学期：{term_label(selected_term)}；解析共{len(p.draft.rows)}条。'
            message += f'绑定当前学号：{p.student_id}。' if personal else '确认后更新整学期版本。'
            return escape(message+f'下表仅显示当前学号的{len(mine)}条。'),exam_table(mine),gr.update(visible=True)
        def commit_import(*args):
            app = args[-1]
            selected_term, params = parse_args(args[:-1])
            result = confirm_import(app,store,kind,selected_term,**params)
            message = '资料已存在，无需重复导入。' if result['duplicate'] else f"导入完成，共{result['count']}条；请重新查询。"
            return message,gr.update(visible=False),gr.update(visible=False),[],gr.update(choices=[],value=[])
        def invalidate_import(app):
            app.exam.imports.pop(kind,None)
            return gr.update(visible=False),[],'内容已修改，请重新预览。'
        bind(preview_button,show_import,inputs,[status,table,confirm_button])
        bind(confirm_button,commit_import,inputs,[status,confirm_button,confirmation,results,selections])
        for field in inputs:
            events = [field.upload, field.clear] if isinstance(field, gr.File) else [field.input]
            for event in events:
                delivery_event(event, guarded_event(invalidate_import),[app_state,revision],[confirm_button,table,status],**opts)
        reset_outputs.extend([*inputs,status,table,confirm_button])
        import_resets.extend(['2025-2026-2',*(['',''] if personal else [None]),'',[],gr.update(visible=False)])
    with gr.Group(elem_id='personal-import-panel'):
        gr.Markdown('### 导入我的考试通知')
        import_panel('personal')
    with gr.Accordion('学校考试总表导入（本地管理员）',open=False,elem_id='school-import-panel'):
        import_panel('excel')
    return PanelBinding(reset_outputs,lambda email='': base_reset(email)+list(import_resets))
