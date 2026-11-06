"""邮件草稿、确认发送和 SMTP 调用；凭据只留在服务端。"""

from dataclasses import dataclass, field
from datetime import datetime
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
import hashlib
import json
import os
import smtplib
import ssl
from threading import RLock
import time

from dotenv import load_dotenv

from assistant import BEIJING
from project_paths import PROJECT_DIR


@dataclass(frozen=True)
class MailSettings:
    host: str
    port: int
    username: str
    password: str = field(repr=False)
    security: str = 'ssl'


@dataclass(frozen=True)
class Draft:
    sender: str
    recipient: str
    subject: str
    body: str


@dataclass
class MailSession:
    pending: Draft | None = None
    consumed: bool = False
    busy: bool = False
    lock: RLock = field(default_factory=RLock, repr=False)

    def __deepcopy__(self, memo):
        return MailSession()


def validate_address(value):
    value = value.strip()
    if not value or len(value) > 254 or any(c.isspace() for c in value):
        raise ValueError('请填写一个完整邮箱地址。')
    try:
        parsed = Address(addr_spec=value)
        if not parsed.username or not parsed.domain or '.' not in parsed.domain:
            raise ValueError
        value.encode('ascii')
    except (ValueError, UnicodeError):
        raise ValueError('请填写一个完整邮箱地址，不带姓名、逗号或多个收件人。') from None
    return value


def read_mail_settings():
    load_dotenv(PROJECT_DIR / '.env', override=False)
    username = os.getenv('SMTP_USERNAME', '').strip()
    if not username:
        return None
    host = os.getenv('SMTP_HOST', 'smtp.exmail.qq.com').strip()
    if not host or any(c.isspace() for c in host) or any(c in host for c in '/:@'):
        raise ValueError('请检查 SMTP_HOST，应填写服务器域名。')
    try:
        port = int(os.getenv('SMTP_PORT', '465'))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise ValueError('请检查 SMTP_PORT。') from None
    security = os.getenv('SMTP_SECURITY', 'ssl').strip().lower()
    if security not in ('ssl', 'starttls'):
        raise ValueError('SMTP_SECURITY 只能为 ssl 或 starttls。')
    return MailSettings(host, port, validate_address(username),
                        os.getenv('SMTP_PASSWORD', ''), security)


def make_draft(settings, recipient, subject, body):
    if settings is None:
        raise ValueError('请先在项目 .env 中配置 SMTP_USERNAME、服务器和端口。')
    subject = subject.strip()
    body = body.strip()
    if not subject or len(subject) > 200 or any(c in subject for c in '\r\n'):
        raise ValueError('请填写一行主题，长度不超过200个字符。')
    if not body or len(body) > 20000:
        raise ValueError('请填写正文，长度不超过20000个字符。')
    return Draft(settings.username, validate_address(recipient), subject, body)


def preview_mail(session, settings, recipient, subject, body):
    draft = make_draft(settings, recipient, subject, body)
    with session.lock:
        if session.busy:
            raise ValueError('邮件正在发送，请等待结果。')
        session.pending = draft
        session.consumed = False
    return draft


def cancel_mail(session):
    with session.lock:
        if session.busy:
            raise ValueError('邮件正在发送，请等待结果。')
        session.pending = None
        session.consumed = False


def send_mail(settings, draft):
    message = EmailMessage()
    message['From'] = draft.sender
    message['To'] = draft.recipient
    message['Subject'] = draft.subject
    message['Date'] = formatdate(localtime=True)
    message['Message-ID'] = make_msgid(domain=draft.sender.split('@')[1])
    message.set_content(draft.body)
    started = time.perf_counter()
    client = None
    submission_started = False
    status, detail = 'failed', '邮件未发送。'
    try:
        context = ssl.create_default_context()
        if settings.security == 'ssl':
            client = smtplib.SMTP_SSL(settings.host, settings.port, timeout=20, context=context)
        else:
            client = smtplib.SMTP(settings.host, settings.port, timeout=20)
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        client.login(settings.username, settings.password)
        submission_started = True
        refused = client.send_message(message, from_addr=draft.sender, to_addrs=[draft.recipient])
        if refused:
            status, detail = 'failed', '服务器拒绝了收件人，邮件未完成提交。'
        else:
            status, detail = 'accepted', '邮件服务器已接受提交，请到收件邮箱确认是否收到。'
    except smtplib.SMTPAuthenticationError:
        detail = '邮箱认证失败，请检查邮箱密码或客户端专用密码，以及 SMTP 服务权限。'
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
        detail = '服务器明确拒绝邮件，请检查收发件地址、内容或账号发信权限。'
    except ssl.SSLCertVerificationError:
        detail = '服务器证书校验失败，请检查系统时间及网络环境。'
    except (OSError, smtplib.SMTPException):
        if submission_started:
            status, detail = 'unknown', '提交过程中连接中断，发送状态不明。请核对收件箱和已发送记录，再决定是否重发。'
        else:
            detail = '连接或登录未完成，请检查网络、服务器、端口和加密方式。'
    finally:
        if client is not None:
            try:
                client.close()
            except OSError:
                pass
    return {'time': datetime.now(BEIJING).isoformat(timespec='seconds'),
            'status': status, 'detail': detail, 'message_id': str(message['Message-ID']),
            'sender': draft.sender, 'recipient': draft.recipient, 'subject': draft.subject,
            'body_characters': len(draft.body),
            'body_sha256': hashlib.sha256(draft.body.encode('utf-8')).hexdigest(),
            'total_seconds': round(time.perf_counter() - started, 3)}


MAIL_LOG_LOCK = RLock()


def save_mail_log(record):
    folder = PROJECT_DIR / 'logs' / 'mail'
    path = folder / f'{datetime.now(BEIJING):%Y-%m-%d}.jsonl'
    with MAIL_LOG_LOCK:
        folder.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as file:
            file.write(json.dumps(record, ensure_ascii=False) + '\n')


def deliver_mail(session, settings, recipient, subject, body, sender=send_mail, logger=save_mail_log):
    current = make_draft(settings, recipient, subject, body)
    if not settings.password:
        raise ValueError('未读取到 SMTP_PASSWORD，请在本机 .env 填写，或启动时在终端输入。')
    with session.lock:
        if session.busy:
            raise ValueError('邮件正在发送，请等待结果。')
        if session.pending != current:
            raise ValueError('邮件内容已改变，请重新预览并确认。')
        if session.consumed:
            raise ValueError('这份确认已处理，请查看发送结果，避免重复发送。')
        session.consumed = True
        session.busy = True
    try:
        try:
            record = sender(settings, current)
        except Exception:
            record = {'time': datetime.now(BEIJING).isoformat(timespec='seconds'),
                      'status': 'unknown', 'detail': '发送未正常结束，状态不明。请先核对收件邮箱，勿直接重复发送。',
                      'sender': current.sender, 'recipient': current.recipient,
                      'subject': current.subject, 'total_seconds': None}
        try:
            logger(record)
            record['log_saved'] = True
        except OSError:
            record['log_saved'] = False
        return record
    finally:
        with session.lock:
            session.busy = False
