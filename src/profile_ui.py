"""会话资料界面及各面板清理输出约定。"""
from dataclasses import dataclass
from typing import Callable
from html import escape
import gradio as gr
from app_session import operation
from ui_assets import image_data


@dataclass
class PanelBinding:
    outputs: list
    reset_values: Callable[[], list]


def guarded_event(fn):
    """最后两个输入必须是会话和浏览器提交时的资料版本。"""
    def guarded(*args):
        app, revision = args[-2:]
        try:
            with operation(app, revision):
                return fn(*args[:-1])
        except ValueError as error:
            raise gr.Error(str(error)) from None
    guarded.__name__ = fn.__name__
    return guarded


def profile_summary(profile):
    name = profile.display_name or ('同学' if any([profile.student_id, profile.major, profile.school, profile.year, profile.college]) else '访客')
    return (f'<span class="profile-chip"><span class="profile-avatar"><img src="{image_data("user-avatar.png")}" alt="用户头像"></span>'
            f'<span class="profile-name">{escape(name)}</span><span class="profile-chevron" aria-hidden="true">⌄</span></span>')


def mount_profile_ui(app_state, loading=False):
    with gr.Column(visible=True, elem_id='profile-dialog', min_width=0) as dialog:
        gr.Markdown('## 个人资料')
        gr.Markdown('可全部留空，也可以稍后填写。资料仅本次网页会话有效，刷新后需重新填写；自填资料不是身份认证。')
        fields = []
        for left, right in [('姓名或称呼', '学校'), ('学院', '专业'), ('年级', '学号')]:
            with gr.Row():
                for label in (left, right):
                    fields.append(gr.Textbox(label=label+'（选填）', max_lines=1))
        fields.append(gr.Textbox(label='提醒收件邮箱（选填）', max_lines=1,
                                 placeholder='填写自己希望接收提醒的邮箱，无需密码', elem_id='profile-email'))
        gr.Markdown('普通提醒和考试提醒共用此邮箱，创建时须预览确认。修改邮箱仅影响新任务，已创建任务仍使用原地址；需要改投时请取消旧任务后重新创建。')
        consent = gr.Checkbox(value=False, label='允许小智使用我的学习背景进行个性化回答', elem_id='profile-consent')
        gr.Markdown('开启后，学校、学院、专业、年级会随问题发送给模型服务商。姓名或称呼、学号和提醒邮箱不会通过此功能自动发送。'
                    '你主动在问题中输入的个人信息仍会随问题发送；问答日志可能包含这些信息。')
        info = gr.HTML('', elem_id='profile-feedback')
        with gr.Row():
            save = gr.Button('填写资料并进入', variant='primary', elem_id='profile-save', interactive=not loading)
            skip = gr.Button('暂不填写', elem_id='profile-skip', interactive=not loading)
            clear = gr.Button('清除个人资料', elem_id='profile-clear', interactive=not loading)
        with gr.Group(visible=False, elem_id='profile-confirmation') as confirmation:
            gr.Markdown('确认修改资料并清空旧对话、个人查询结果、邮件及提醒草稿和导入预览？'
                        '已保存的本机历史会话、已导入的考试数据、日志和已创建提醒仍然保留，已有提醒不会自动取消。')
            with gr.Row():
                accept = gr.Button('确认修改并清空', variant='primary', elem_id='profile-accept')
                reject = gr.Button('取消修改，保留原资料', elem_id='profile-reject')
    return dict(dialog=dialog, fields=fields, consent=consent, info=info, save=save,
                skip=skip, clear=clear, confirmation=confirmation, accept=accept, reject=reject)
