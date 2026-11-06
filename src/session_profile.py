"""本次会话的自填资料，不代表身份认证。"""

from dataclasses import dataclass, asdict
import unicodedata



@dataclass(frozen=True)
class Profile:
    display_name: str = ''
    school: str = ''
    college: str = ''
    major: str = ''
    year: str = ''
    student_id: str = ''
    reminder_email: str = ''
    allow_personalization: bool = False


LIMITS = {'display_name': 40, 'school': 80, 'college': 80, 'major': 80, 'year': 20, 'student_id': 32, 'reminder_email': 254}
LABELS = dict(zip(LIMITS, ['姓名或称呼', '学校', '学院', '专业', '年级', '学号', '提醒收件邮箱']))
BACKGROUND_FIELDS = ('school', 'college', 'major', 'year')


def validate_profile(values: dict) -> Profile:
    if set(values) - set(asdict(Profile())):
        raise ValueError('含有不支持的资料字段。')
    consent = values.get('allow_personalization', False)
    if type(consent) is not bool:
        raise ValueError('个性化开关值无效。')
    cleaned = {}
    for key, limit in LIMITS.items():
        value = values.get(key, '')
        if not isinstance(value, str) or any(unicodedata.category(c).startswith('C') or c in '\n\r\u2028\u2029' for c in value):
            raise ValueError(f'{LABELS[key]}须为单行文字，不能含控制字符。')
        value = value.strip()
        if len(value) > limit:
            raise ValueError(f'{LABELS[key]}请控制在{limit}字符以内。')
        if key == 'student_id' and value:
            # 考试解析模块复用 assistant 的时区，延后导入避免启动时循环依赖。
            from exam_import import student_id
            value = student_id(value)
        if key == 'reminder_email' and value:
            from mail_service import validate_address
            value = validate_address(value)
        cleaned[key] = value
    return Profile(**cleaned, allow_personalization=consent)


def model_background(profile: Profile) -> dict[str, str]:
    if not profile.allow_personalization:
        return {}
    return {key: getattr(profile, key) for key in BACKGROUND_FIELDS if getattr(profile, key)}


def require_reminder_email(profile: Profile) -> str:
    if not profile.reminder_email:
        raise ValueError('请先在左侧“个人资料”中填写提醒收件邮箱，再创建邮件提醒。')
    from mail_service import validate_address
    return validate_address(profile.reminder_email)
