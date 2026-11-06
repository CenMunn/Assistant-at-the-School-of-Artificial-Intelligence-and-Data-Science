"""官网教师资料的本地快照：目录与问答共用，不在运行时抓取网页。"""
from datetime import date
from copy import deepcopy
from html import escape
import json
import re
from threading import Lock
from urllib.parse import urlparse

from project_paths import TEXT_DIR

_catalog_snapshot = None
_catalog_lock = Lock()


def _official_url(value):
    address = urlparse(value)
    return (address.scheme == 'https' and address.netloc == 'it.uibe.edu.cn'
            and not address.query and not address.fragment)


def validate_catalog(data):
    """拒绝破损字段和不可信来源链接，空字段明确留空，不自动补造。"""
    try:
        if data['schema_version'] != 1:
            raise ValueError('未知教师资料版本')
        date.fromisoformat(data['checked_on'])
        departments = data['departments']
        if not departments or not all(isinstance(k, str) and _official_url(v)
                                      for k, v in departments.items()):
            raise ValueError('教师系别来源无效')
        seen = set()
        for teacher in data['teachers']:
            for key in ('id', 'name', 'department', 'title', 'research', 'office', 'notes', 'source_url'):
                if not isinstance(teacher[key], str):
                    raise ValueError('教师资料字段必须是文本')
            if not re.fullmatch(r'T\d{2,}', teacher['id']) or teacher['id'] in seen:
                raise ValueError('教师资料编号无效或重复')
            seen.add(teacher['id'])
            if not teacher['name'] or teacher['department'] not in departments or not _official_url(teacher['source_url']):
                raise ValueError('教师姓名、系别或官网链接无效')
            for key in ('emails', 'courses'):
                if not isinstance(teacher[key], list) or not all(isinstance(x, str) and x for x in teacher[key]):
                    raise ValueError('教师邮箱和课程应为文本列表')
            if any(not re.fullmatch(r'[A-Za-z0-9_.+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}', x)
                   for x in teacher['emails']):
                raise ValueError('教师邮箱不是明确的标准地址')
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError('教师资料格式不完整，请检查 texts/teachers.json。') from error
    return data


def load_catalog():
    """每个进程只加载一次，目录与新旧聊天同版本；修改资料后须重启。"""
    global _catalog_snapshot
    with _catalog_lock:
        if _catalog_snapshot is None:
            _catalog_snapshot = validate_catalog(json.loads(
                (TEXT_DIR / 'teachers.json').read_text(encoding='utf-8')))
        # 不暴露内部快照，避免调用方修改它影响其他用户。
        return deepcopy(_catalog_snapshot)


def filter_teachers(data, department='全部', query=''):
    if department != '全部' and department not in data['departments']:
        raise ValueError('请选择目录中已有的系别。')
    keyword = (query or '').strip().casefold()
    return [teacher for teacher in data['teachers']
            if (department == '全部' or teacher['department'] == department)
            and (not keyword or keyword in ' '.join([
                teacher['name'], teacher['research'], teacher['title'],
                *teacher['courses'], *teacher['emails']]).casefold())]


def knowledge_text(data):
    """给现有system消息提供简明教师记录，保留旧T01–T10引用编号。"""
    lines = [f"教师资料核对日期：{data['checked_on']}。来自官网的人工整理本地快照，不实时同步。",
             '系别依据官网师资名录；系别不等同于本科专业，也不表示只给该专业授课。',
             '职称、课程和联系方式均为主页记载；未收录不等于没有。列课不是本学期排课。',
             '研究方向或主页招募说明不保证当前名额、项目招募或回复；须向教师确认。']
    for department, url in data['departments'].items():
        lines.append(f'系别名录：{department} {url}')
    for teacher in data['teachers']:
        lines.extend([f"[{teacher['id']}] {teacher['name']}｜{teacher['department']}｜{teacher['title'] or '职称未收录'}",
                      '方向：' + (teacher['research'] or '未收录'),
                      '公开邮箱：' + ('；'.join(teacher['emails']) or '标准邮箱未收录')])
        if teacher['office']:
            lines.append('地点：' + teacher['office'])
        if teacher['courses']:
            lines.append('主页列课（摘录）：' + '、'.join(teacher['courses']))
        if teacher['notes']:
            lines.append('说明：' + teacher['notes'])
        lines.append('来源：' + teacher['source_url'])
    return '\n'.join(lines)


def render_directory(data, department='全部', query=''):
    """所有文字先转义；链接仅来自经校验的官网地址，不提供自动发送按钮。"""
    validate_catalog(data)
    teachers = filter_teachers(data, department, query)
    html = [f'<div class="teacher-directory-meta"><p class="teacher-count" role="status" aria-live="polite">找到 {len(teachers)} 位教师</p>',
            '<details class="teacher-directory-help"><summary>目录说明与资料来源</summary>'
            f'<p class="teacher-scope">核对日期：{escape(data["checked_on"])}。官网资料的本地快照，非实时同步。'
            '所属系按师资名录分类，不等同于本科专业或当前授课范围；职称以主页记载为准。'
            '课程不是当前排课；招生、项目招募、办公时间请向教师确认。</p></details></div>']
    if not teachers:
        html.append('<div class="teacher-empty">没有匹配的教师。试试姓名或研究方向关键词，或清除筛选查看全部。</div>')
    for name, url in data['departments'].items():
        group = [t for t in teachers if t['department'] == name]
        if not group:
            continue
        html.append(f'<section class="teacher-group"><div class="teacher-group-heading">'
                    f'<h3>{escape(name)} <span>{len(group)} 位</span></h3>'
                    f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">官网系别名录（新窗口）</a>'
                    '</div><div class="teacher-grid">')
        for teacher in group:
            html.append('<article class="teacher-card">'
                        f'<h4>{escape(teacher["name"])}</h4>'
                        f'<p class="teacher-title">{escape(teacher["title"] or "职称未收录")}</p>'
                        f'<p class="teacher-research">{escape(teacher["research"] or "研究方向未收录")}</p>'
                        '<details><summary>联系方式与资料来源</summary><dl>')
            fields = [('所属系', teacher['department']),
                      ('公开邮箱', '；'.join(teacher['emails']) or '标准邮箱未收录，请查看原页'),
                      ('办公地点', teacher['office'] or '本次未收录'),
                      ('主页列课（摘录）', '、'.join(teacher['courses']) or '本次未收录，可查看原页')]
            for label, value in fields:
                html.append(f'<dt>{escape(label)}</dt><dd>{escape(value)}</dd>')
            html.append('</dl>')
            if teacher['notes']:
                html.append(f'<p class="teacher-note">{escape(teacher["notes"])}</p>')
            html.append(f'<p><a href="{escape(teacher["source_url"], quote=True)}" target="_blank" '
                        f'rel="noopener noreferrer">{escape(teacher["name"])}的官网资料（新窗口）</a>'
                        f'<span class="teacher-source">资料编号 {escape(teacher["id"])}</span></p>'
                        '</details></article>')
        html.append('</div></section>')
    return ''.join(html)
