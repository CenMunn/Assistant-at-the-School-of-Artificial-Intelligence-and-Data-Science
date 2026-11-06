"""只读教师目录工作区；不接触学生资料、邮件或提醒状态。"""
import gradio as gr
from teacher_catalog import load_catalog, render_directory


def mount_teacher_panel():
    data = load_catalog()
    gr.Markdown('## 教师资料\n\n按学院官网所属系浏览，也可以搜索姓名、研究方向或主页列出的课程。')
    with gr.Row(elem_id='teacher-filters'):
        department = gr.Dropdown(['全部', *data['departments']], value='全部', label='所属系',
                                 allow_custom_value=False, scale=1, min_width=140, elem_id='teacher-department')
        query = gr.Textbox(label='搜索教师', placeholder='例如：刘小月、自然语言处理、机器学习',
                           max_lines=1, max_length=100, scale=2, min_width=200, elem_id='teacher-search')
        search = gr.Button('搜索', variant='primary', scale=0, min_width=72, elem_id='teacher-search-button')
        clear = gr.Button('清除筛选', scale=0, min_width=90, elem_id='teacher-clear')
    results = gr.HTML(render_directory(data), elem_id='teacher-results')

    def show(selected, keyword):
        try:
            return render_directory(data, selected, keyword)
        except ValueError as error:
            raise gr.Error(str(error)) from None

    options = dict(queue=False, api_name=False, show_progress='hidden')
    for event in (department.change, query.submit, search.click):
        event(show, [department, query], results, **options)
    clear.click(lambda: ('全部', '', render_directory(data)), [], [department, query, results], **options)
