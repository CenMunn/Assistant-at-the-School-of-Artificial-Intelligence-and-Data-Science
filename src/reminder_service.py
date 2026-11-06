"""本地一次性邮件提醒；任务状态持久保存，发信不自动重试。"""

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import re
import sqlite3
from uuid import uuid4
from threading import RLock, Event, Thread

from assistant import BEIJING
from mail_service import validate_address, Draft, send_mail


def beijing_now():
    return datetime.now(BEIJING)


@dataclass(frozen=True)
class ReminderDraft:
    owner: str
    item: str
    due_at: int
    recipient: str = ''


def parse_reminder(owner, item, date, clock, now, *, recipient=None):
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('当前时间必须包含时区。')
    item, date, clock = item.strip(), date.strip(), clock.strip()
    if not 1 <= len(item) <= 500:
        raise ValueError('提醒事项请填写1—500个字符。')
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', date) or not re.fullmatch(r'[0-9]{2}:[0-9]{2}', clock):
        raise ValueError('日期格式为YYYY-MM-DD，时间为HH:MM（24小时制）。')
    try:
        due = datetime.strptime(f'{date} {clock}', '%Y-%m-%d %H:%M').replace(tzinfo=BEIJING)
    except ValueError:
        raise ValueError('日期或时间无效，请检查月份、天数及小时。') from None
    if due <= now:
        raise ValueError('提醒时间必须晚于当前北京时间，请重新选择。')
    return ReminderDraft(validate_address(owner), item, int(due.timestamp()),
                         validate_address(recipient) if recipient is not None else '')


class ReminderStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, token TEXT UNIQUE NOT NULL,
                    owner TEXT NOT NULL, item TEXT NOT NULL, due_at INTEGER NOT NULL,
                    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                    status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
                    message_id TEXT NOT NULL DEFAULT '', total_seconds REAL
                );
                CREATE INDEX IF NOT EXISTS tasks_due ON tasks(owner,status,due_at);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, task_id TEXT NOT NULL,
                    time INTEGER NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL
                );
            ''')
        # 旧任务原本发给owner。只新增收件字段，不改时间、状态和历史事件。
        with self.connection(write=True) as db:
            if 'recipient' not in {row['name'] for row in db.execute('PRAGMA table_info(tasks)')}:
                db.execute("ALTER TABLE tasks ADD COLUMN recipient TEXT NOT NULL DEFAULT ''")
                db.execute("UPDATE tasks SET recipient=owner")

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def event(db, task_id, now, status, detail):
        db.execute('INSERT INTO events(task_id,time,status,detail) VALUES (?,?,?,?)',
                   (task_id, now, status, detail))

    def create(self, draft, token, now, admission=None):
        with self.connection(write=True) as db:
            # 写锁可能等待数秒。取得写锁后复核时钟与服务状态，提交前保持准入锁。
            with admission() if admission else nullcontext(now) as current_time:
                result = self._create_locked(db, draft, token, current_time)
                db.commit()
                return result

    def _create_locked(self, db, draft, token, now):
        recipient = validate_address(draft.recipient or draft.owner)
        old = db.execute('SELECT * FROM tasks WHERE token=?', (token,)).fetchone()
        if old:
            if (old['owner'], old['item'], old['due_at'], old['recipient'] or old['owner']) != (draft.owner, draft.item, draft.due_at, recipient):
                raise ValueError('确认信息不一致，请重新预览。')
            return dict(old)
        if not token or draft.due_at <= now:
            raise ValueError('提醒时间已到或确认无效，请重新预览。')
        validate_address(draft.owner)
        if not 1 <= len(draft.item.strip()) <= 500:
            raise ValueError('提醒事项请填写1—500个字符。')
        count = db.execute("SELECT count(*) FROM tasks WHERE owner=? AND status='pending'", (draft.owner,)).fetchone()[0]
        if count >= 20:
            raise ValueError('最多保留20个待执行提醒，请先取消不需要的任务。')
        task_id = uuid4().hex
        db.execute('INSERT INTO tasks(id,token,owner,item,due_at,created_at,updated_at,status,recipient) VALUES (?,?,?,?,?,?,?,?,?)',
                   (task_id, token, draft.owner, draft.item, draft.due_at, now, now, 'pending', recipient))
        self.event(db, task_id, now, 'pending', '用户确认创建')
        return dict(db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone())

    def list_tasks(self, owner):
        with self.connection() as db:
            return [dict(row) for row in db.execute('SELECT * FROM tasks WHERE owner=? ORDER BY created_at DESC,id', (owner,))]

    def get(self, owner, task_id):
        with self.connection() as db:
            row = db.execute('SELECT * FROM tasks WHERE owner=? AND id=?', (owner, task_id)).fetchone()
            if row is None:
                raise ValueError('未找到当前邮箱的提醒任务。')
            return dict(row)

    def transition(self, db, task_id, now, status, detail):
        db.execute('UPDATE tasks SET status=?,detail=?,updated_at=? WHERE id=?', (status, detail, now, task_id))
        self.event(db, task_id, now, status, detail)

    def claim_due(self, owner, now):
        with self.connection(write=True) as db:
            late = db.execute("SELECT id FROM tasks WHERE owner=? AND status='pending' AND due_at<?", (owner, now - 60)).fetchall()
            for row in late:
                self.transition(db, row['id'], now, 'missed', '迟到超过60秒，未补发。')
            row = db.execute("SELECT * FROM tasks WHERE owner=? AND status='pending' AND due_at<=? ORDER BY due_at,created_at,id LIMIT 1", (owner, now)).fetchone()
            if row is None:
                return None
            self.transition(db, row['id'], now, 'sending', '已领取，正在提交邮件。')
            return dict(db.execute('SELECT * FROM tasks WHERE id=?', (row['id'],)).fetchone())

    def cancel(self, owner, task_id, now):
        with self.connection(write=True) as db:
            row = db.execute("SELECT id FROM tasks WHERE owner=? AND id=? AND status='pending'", (owner, task_id)).fetchone()
            if row is None:
                return False
            self.transition(db, task_id, now, 'cancelled', '用户确认取消，未发送。')
            return True

    def finish(self, owner, task_id, record, now):
        status = record.get('status', 'unknown')
        if status not in ('accepted', 'failed', 'unknown'):
            status = 'unknown'
        with self.connection(write=True) as db:
            row = db.execute("SELECT id FROM tasks WHERE owner=? AND id=? AND status='sending'", (owner, task_id)).fetchone()
            if row is None:
                raise ValueError('任务不是发送中状态。')
            self.transition(db, task_id, now, status, record.get('detail', '发送状态不明。'))
            db.execute('UPDATE tasks SET message_id=?,total_seconds=? WHERE id=?',
                       (record.get('message_id', ''), record.get('total_seconds'), task_id))

    def recover(self, owner, now):
        with self.connection(write=True) as db:
            rows = db.execute("SELECT id,status FROM tasks WHERE owner=? AND (status='sending' OR (status='pending' AND due_at<=?))", (owner, now)).fetchall()
            for row in rows:
                status = 'unknown' if row['status'] == 'sending' else 'missed'
                detail = '上次发送未正常结束，请先核对收件箱；不自动重试。' if status == 'unknown' else '启动前已过期，未补发。'
                self.transition(db, row['id'], now, status, detail)


@dataclass
class ReminderSession:
    pending: ReminderDraft | None = None
    token: str = ''
    cancellation_id: str = ''
    lock: RLock = field(default_factory=RLock, repr=False)

    def __deepcopy__(self, memo):
        return ReminderSession()


def preview_reminder(session, owner, item, date, clock, now, *, recipient=None):
    draft = parse_reminder(owner, item, date, clock, now, recipient=recipient)
    with session.lock:
        session.pending = draft
        session.token = uuid4().hex
    return draft


def clear_reminder_preview(session):
    with session.lock:
        session.pending, session.token = None, ''


@contextmanager
def creation_admission(service):
    with service.admission_lock:
        if not service.ready:
            raise ValueError('提醒服务已经停止，本次未创建。请检查状态。')
        yield int(service.now().timestamp())


def confirm_reminder(session, service, item, date, clock, *, recipient=None):
    if service is None or not service.ready:
        raise ValueError('提醒服务不可用，请检查网页服务和邮箱配置。')
    with session.lock:
        now = service.now()
        draft = parse_reminder(service.owner, item, date, clock, now, recipient=recipient)
        if session.pending != draft or not session.token:
            raise ValueError('内容未预览或已修改，请重新预览确认。')
        return service.store.create(draft, session.token, int(now.timestamp()),
                                    admission=lambda: creation_admission(service))


def preview_cancellation(session, service, task_id):
    if service is None:
        raise ValueError('提醒服务不可用。')
    with session.lock:
        session.cancellation_id = ''
        task = service.store.get(service.owner, task_id)
        if task['status'] != 'pending':
            raise ValueError('只能取消待执行的任务，请刷新列表。')
        session.cancellation_id = task['id']
        return task


def confirm_cancellation(session, service):
    if service is None:
        raise ValueError('提醒服务不可用。')
    with session.lock:
        if not session.cancellation_id:
            raise ValueError('请先预览要取消的任务。')
        result = service.store.cancel(service.owner, session.cancellation_id, int(service.now().timestamp()))
        session.cancellation_id = ''
        return result


class ProjectLock:
    """Windows项目级非阻塞锁；文件保留，持有句柄期间才算加锁。"""
    def __init__(self, path):
        self.path = Path(path)
        self.handle = None

    def acquire(self):
        import msvcrt
        if self.handle is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open('a+b')
        try:
            if self.path.stat().st_size == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            handle.close()
            return False
        self.handle = handle
        return True

    def release(self):
        import msvcrt
        if self.handle is not None:
            try:
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            finally:
                self.handle.close()
                self.handle = None


def make_reminder_mail(task):
    due = datetime.fromtimestamp(task['due_at'], BEIJING).strftime('%Y-%m-%d %H:%M')
    return Draft(task['owner'], validate_address(task.get('recipient') or task['owner']), '[信息学院助理] 定时提醒',
                 f"提醒事项：{task['item']}\n计划时间：{due}（北京时间 UTC+08:00）\n"
                 f"任务编号：{task['id']}\n这是你在本地网页确认创建的一次性提醒。")


class ReminderService:
    def __init__(self, store, settings, sender=send_mail, now=beijing_now, lock_path=None,
                 draft_factory=make_reminder_mail):
        self.store, self.settings, self.sender, self.now = store, settings, sender, now
        self.draft_factory = draft_factory
        self.owner = settings.username if settings else ''
        self.ready = False
        self.status_text = '提醒服务未启动，请检查邮箱配置。'
        self.project_lock = ProjectLock(lock_path or store.path.with_suffix('.lock'))
        self._stop = Event()
        self._tick_lock = RLock()
        self.admission_lock = RLock()
        self._thread = None

    def start(self):
        if self.ready:
            return True
        if not self.settings or not self.settings.password:
            self.status_text = '提醒不可用：请在本机配置发件邮箱及凭据并重启。'
            return False
        try:
            if not self.project_lock.acquire():
                self.status_text = '提醒不可用：另一个程序正在管理本项目的提醒。'
                return False
            self.store.recover(self.owner, int(self.now().timestamp()))
            self._stop.clear()
            self.ready = True
            self.status_text = '提醒服务运行中；仅在Python服务运行、电脑唤醒且网络可用时尝试发送。'
            self._thread = Thread(target=self._run, name='college-reminders', daemon=True)
            self._thread.start()
            return True
        except (OSError, sqlite3.Error, ValueError, RuntimeError):
            self.ready = False
            self._stop.set()
            self.project_lock.release()
            self.status_text = '提醒不可用：数据库、进程锁或后台线程启动失败，请检查本地目录权限。'
            return False

    def _run(self):
        while not self._stop.is_set():
            if not self.tick():
                self._stop.wait(2)

    def _fault(self):
        with self.admission_lock:
            self.ready = False
            self._stop.set()
            self.status_text = '提醒服务已停止：任务读写未完成。请先核对邮箱及记录，不要直接重复创建。'

    def tick(self):
        with self._tick_lock:
            if not self.ready or self._stop.is_set():
                return
            try:
                task = self.store.claim_due(self.owner, int(self.now().timestamp()))
                if task is None:
                    return
                if task['owner'] != self.owner:
                    self._fault()
                    return
                try:
                    record = self.sender(self.settings, self.draft_factory(task))
                except Exception:
                    record = {'status': 'unknown', 'detail': '发送未正常结束，请先核对收件箱；不会自动重试。'}
                self.store.finish(self.owner, task['id'], record, int(self.now().timestamp()))
                return True
            except (OSError, sqlite3.Error, ValueError, TypeError, AttributeError):
                self._fault()

    def stop(self):
        with self.admission_lock:
            self.ready = False
            self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join()
        # 手动tick或后台发信结束之后才释放项目锁。
        with self._tick_lock:
            self.project_lock.release()
        if '运行中' in self.status_text:
            self.status_text = '提醒服务已停止。'
