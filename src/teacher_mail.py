"""教师目录的快捷收件人；仅填表，不发送。"""
from teacher_catalog import load_catalog
from mail_service import cancel_mail


def teacher_choices():
    return [(f"{row['name']} · {row['department']}", row['id'])
            for row in load_catalog()['teachers']]


def teacher_contact(teacher_id):
    for row in load_catalog()['teachers']:
        if row['id'] == teacher_id:
            return row
    raise ValueError('请先选择目录中的教师。')


def fill_teacher_recipient(current, teacher_id, email):
    row = teacher_contact(teacher_id)
    if not row['emails']:
        raise ValueError('该教师尚未收录明确的公开邮箱，请查看官网资料。')
    if email not in row['emails']:
        raise ValueError('请从该教师已收录的公开邮箱中选择。')
    cancel_mail(current.mail)
    return email
