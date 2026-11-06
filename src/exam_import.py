"""考试资料的确定性解析；不调用模型，不执行表格中的公式。"""

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from zipfile import ZipFile

from assistant import BEIJING


@dataclass
class ImportDraft:
    term: str
    source: str
    digest: str
    kind: str
    rows: list
    duplicates: int = 0
    blank_rows: int = 0


def digest_json(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def valid_term(value):
    value = str(value).strip()
    match = re.fullmatch(r'(20\d{2})-(20\d{2})-([12])', value)
    if not match or int(match[2]) != int(match[1]) + 1:
        raise ValueError('学期格式为2025-2026-2，最后一位1表示第一学期，2表示第二学期。')
    return value


def term_label(value):
    first, second, semester = valid_term(value).split('-')
    return f'{first}—{second}学年' + ('第一学期' if semester == '1' else '第二学期')


def room_label(row):
    """原编号不变；仅解释有资料来源的教室，不从上课地点推测考场。"""
    room = str(row.get('room') or '').strip()
    if not room or not str(row.get('room_source') or '').strip():
        return '未提供'
    # 楼宇简称由项目使用者提供；字母属于教室号，不推断楼层。
    buildings = {'求': '求索楼', '知': '知行楼', '宁': '宁远楼', '博': '博学楼', '诚': '诚信楼'}
    match = re.fullmatch(r'([求知宁博诚])\s*([A-Za-z]?\d+[A-Za-z]?)', room)
    if match:
        return f'{room}（{buildings[match[1]]}{match[2]}）'
    return room


def student_id(value):
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError('学号不能包含小数。')
        value = int(value)
    value = str(value).strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', value):
        raise ValueError('学号须为1—32位字母、数字、短横线或下划线。')
    return value


def parse_time(value):
    match = re.fullmatch(r'(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})\s*[~～—–-]\s*(\d{1,2}:\d{2})', value.strip())
    if not match:
        raise ValueError('考试时间应为完整日期和起止时间，例如2026-07-03 08:00~10:00。')
    try:
        start = datetime.strptime(f'{match[1]} {match[2]}', '%Y-%m-%d %H:%M').replace(tzinfo=BEIJING)
        end = datetime.strptime(f'{match[1]} {match[3]}', '%Y-%m-%d %H:%M').replace(tzinfo=BEIJING)
    except ValueError:
        raise ValueError('考试日期或时间无效。') from None
    if end <= start:
        raise ValueError('考试结束时间必须晚于开始时间。')
    return start.strftime('%Y-%m-%d %H:%M'), end.strftime('%Y-%m-%d %H:%M')


def timestamp(value):
    return int(datetime.strptime(value, '%Y-%m-%d %H:%M').replace(tzinfo=BEIJING).timestamp())


def make_row(sid, course, when, method, source, row_number, room='', code='', section=''):
    course, method, room = str(course).strip(), str(method).strip(), str(room).strip()
    if not course or not method or max(len(course), len(method), len(room)) > 200:
        raise ValueError('课程、考试方式不能为空，各字段不能超过200字符。')
    start, end = parse_time(when)
    return dict(student_id=student_id(sid), course=course, start=start, end=end,
                method=method, room=room, code=str(code).strip(), section=str(section).strip(),
                source=source, source_row=row_number, room_source=source if room else '')


def parse_excel(path, term):
    term = valid_term(term)
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError('请选择20MB以内的Excel文件。')
    suffix = path.suffix.lower()
    raw = path.read_bytes()
    rows = None
    try:
        if suffix == '.xls':
            import xlrd
            book = xlrd.open_workbook(file_contents=raw, on_demand=True)
            try:
                if book.nsheets != 1:
                    raise ValueError('请先将需要导入的工作表单独保存为一个文件。')
                sheet = book.sheet_by_index(0)
                if sheet.nrows > 100001 or sheet.ncols > 50:
                    raise ValueError('表格超出100000行或50列的限制。')
                rows = [sheet.row_values(i) for i in range(sheet.nrows)]
                sheet_name = sheet.name
            finally:
                book.release_resources()
        elif suffix == '.xlsx':
            from openpyxl import load_workbook
            with ZipFile(path) as archive:
                if sum(x.file_size for x in archive.infolist()) > 100 * 1024 * 1024:
                    raise ValueError('表格解压后的内容过大。')
            book = load_workbook(path, read_only=True, data_only=False, keep_links=False)
            try:
                if len(book.worksheets) != 1:
                    raise ValueError('请先将需要导入的工作表单独保存为一个文件。')
                sheet = book.worksheets[0]
                if (sheet.max_row or 0) > 100001 or (sheet.max_column or 0) > 50:
                    raise ValueError('表格超出100000行或50列的限制。')
                rows = []
                for cells in sheet.iter_rows():
                    if len(rows) >= 100001 or len(cells) > 50:
                        raise ValueError('表格超出行列限制。')
                    if any(cell.data_type == 'f' for cell in cells):
                        raise ValueError('请将公式转换为确定的数值后导入。')
                    rows.append([cell.value if cell.value is not None else '' for cell in cells])
                sheet_name = sheet.title
            finally:
                book.close()
        else:
            raise ValueError('只支持.xls和.xlsx文件。')
    except ImportError:
        raise ValueError('缺少Excel依赖，请安装texts/requirements-web.txt。') from None
    except ValueError:
        raise
    except Exception:
        raise ValueError('无法读取Excel，请检查格式、加密情况或文件是否完整。') from None
    if not rows:
        raise ValueError('表格为空。')
    headers = [str(x).strip() for x in rows[0]]
    required = ['学号', '考试课程', '考试时间', '考试方式']
    if any(headers.count(key) != 1 for key in required):
        raise ValueError('第一行须包含且不重复：学号、考试课程、考试时间、考试方式。')
    source = f'{path.name} / {sheet_name}'
    parsed, errors, seen, duplicates, blanks = [], [], set(), 0, 0
    identities = {}
    for number, values in enumerate(rows[1:], 2):
        if all(not str(x).strip() for x in values):
            blanks += 1
            continue
        data = dict(zip(headers, values))
        try:
            row = make_row(data.get('学号', ''), data.get('考试课程', ''), str(data.get('考试时间', '')),
                           data.get('考试方式', ''), source, number, data.get('考场', data.get('考试地点', '')),
                           data.get('课程编号', ''), data.get('课序号', ''))
            fingerprint = digest_json({k:v for k,v in row.items() if k != 'source_row'})
            identity = tuple(row[k] for k in ('student_id', 'code', 'section', 'course', 'start', 'end'))
            if identity in identities and identities[identity] != fingerprint:
                raise ValueError('同一场考试的方式或考场存在冲突，不能同时导入。')
            identities[identity] = fingerprint
            if fingerprint in seen:
                duplicates += 1
            else:
                parsed.append(row)
                seen.add(fingerprint)
        except ValueError as error:
            if len(errors) < 10:
                errors.append(f'第{number}行：{error}')
    if errors:
        raise ValueError('整批未导入，请修正这些行后重试：\n' + '\n'.join(errors))
    if not parsed:
        raise ValueError('未找到有效考试记录。')
    return ImportDraft(term, source, hashlib.sha256(raw).hexdigest(), 'excel', parsed, duplicates, blanks)


def parse_notice(text, sid, term, year):
    term, sid = valid_term(term), student_id(sid)
    if year is None or not str(year).strip().isdigit() or not 2000 <= int(year) <= 2100:
        raise ValueError('个人通知缺少完整年份时，请明确填写考试年份，例如2026。')
    if len(text) > 100000:
        raise ValueError('通知文本请控制在100000字符以内。')
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    rows, consumed_dates = [], set()
    date_pattern = r'(?:(\d{4})-)?(\d{2}-\d{2})\s+(\d{1,2}:\d{2}\s*[~～—–-]\s*\d{1,2}:\d{2})'
    for i, line in enumerate(lines):
        if not re.fullmatch(r'(开卷|闭卷)(笔试|机考|上机考试)?|机考|上机考试|口试|论文|考查', line):
            continue
        if i == 0:
            raise ValueError('考试方式之前缺少课程名。')
        tail = lines[i+1:i+3]
        found = None
        for j, candidate in enumerate(tail):
            match = re.fullmatch(date_pattern, candidate)
            if match:
                if match[1] and int(match[1]) != int(year):
                    raise ValueError('通知中的年份与填写的考试年份冲突。')
                found = make_row(sid, lines[i-1], f'{year}-{match[2]} {match[3]}', line,
                                 '用户提供的个人通知', i, tail[0] if j else '')
                consumed_dates.add(i+1+j)
                break
        if found is None:
            raise ValueError(f'第{i+1}行考试方式后未识别到时间，请核对通知格式。')
        rows.append(found)
    if any(re.fullmatch(date_pattern, line) and i not in consumed_dates for i, line in enumerate(lines)):
        raise ValueError('发现未识别的考试块，可能是考试方式或格式不支持；整批未导入，请核对。')
    if not rows:
        raise ValueError('未识别到考试通知；课程周课表不能作为考试时间导入。')
    unique = {digest_json({k:v for k,v in row.items() if k != 'source_row'}):row for row in rows}
    return ImportDraft(term, '用户提供的个人通知', digest_json([text, sid, term, int(year)]),
                       'notice', list(unique.values()), len(rows)-len(unique))
