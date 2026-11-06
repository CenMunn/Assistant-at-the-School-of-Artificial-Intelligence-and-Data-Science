"""按学期和学号查询考试；考试提醒使用独立的持久化任务库。"""

from datetime import datetime
from contextlib import nullcontext
import json
import re
from uuid import uuid4

from assistant import BEIJING
from exam_import import digest_json, valid_term, student_id, timestamp, term_label, room_label
from mail_service import Draft, validate_address
from reminder_service import ReminderStore


PRESETS = ['7天', '3天', '1天', '1小时', '30分钟']


def parse_offsets(presets, custom):
    values = list(presets or []) + [x.strip() for x in re.split(r'[,，;；\n]', custom or '') if x.strip()]
    minutes = set()
    for value in values:
        match = re.fullmatch(r'([0-9]{1,6})\s*(分钟|小时|天)', value.strip())
        if not match:
            raise ValueError('自定义填写正整数和单位，例如2天,6小时,45分钟。')
        total = int(match[1]) * {'分钟': 1, '小时': 60, '天': 1440}[match[2]]
        if not 1 <= total <= 365 * 1440:
            raise ValueError('提前量须在1分钟至365天之间。')
        minutes.add(total)
    if not 1 <= len(minutes) <= 10:
        raise ValueError('请选择1—10个不同的提醒时间点。')
    return sorted(minutes, reverse=True)


class ExamStore(ReminderStore):
    def __init__(self, path):
        super().__init__(path)
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS imports (
                    id TEXT PRIMARY KEY, term TEXT NOT NULL, digest TEXT NOT NULL,
                    source TEXT NOT NULL, kind TEXT NOT NULL, count INTEGER NOT NULL,
                    created_at TEXT NOT NULL, UNIQUE(term,digest,kind)
                );
                CREATE TABLE IF NOT EXISTS exams (
                    id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, term TEXT NOT NULL,
                    student_id TEXT NOT NULL, course TEXT NOT NULL, start TEXT NOT NULL,
                    active INTEGER NOT NULL, payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS exams_person ON exams(student_id,term,active,start);
                CREATE TABLE IF NOT EXISTS exam_tasks (
                    task_id TEXT PRIMARY KEY, exam_id TEXT NOT NULL, student_id TEXT NOT NULL,
                    offset_minutes INTEGER NOT NULL, snapshot TEXT NOT NULL
                );
            ''')

    def _invalidate(self, db, ids):
        now = int(datetime.now(BEIJING).timestamp())
        for eid in ids:
            running = db.execute("SELECT t.id FROM tasks t JOIN exam_tasks e ON e.task_id=t.id WHERE e.exam_id=? AND t.status='sending'", (eid,)).fetchone()
            if running:
                raise ValueError('相关提醒正在发送，请待发送结束后再更新资料。')
            pending = db.execute("SELECT t.id FROM tasks t JOIN exam_tasks e ON e.task_id=t.id WHERE e.exam_id=? AND t.status='pending'", (eid,)).fetchall()
            for task in pending:
                self.transition(db, task['id'], now, 'invalidated', '考试资料已更新，请重新查询并确认提醒。')
            db.execute('UPDATE exams SET active=0 WHERE id=?', (eid,))

    def activate(self, draft):
        valid_term(draft.term)
        if not draft.rows or draft.kind not in ('excel', 'notice'):
            raise ValueError('没有可导入的考试资料。')
        with self.connection(write=True) as db:
            old = db.execute('SELECT * FROM imports WHERE term=? AND digest=? AND kind=?',
                             (draft.term, draft.digest, draft.kind)).fetchone()
            if old and draft.kind == 'excel':
                latest = db.execute("SELECT id FROM imports WHERE term=? AND kind='excel' ORDER BY created_at DESC LIMIT 1", (draft.term,)).fetchone()
                if latest and latest['id'] != old['id']:
                    raise ValueError('这份文件是已被替代的旧版本，请核对最新安排，不能再次覆盖。')
                return dict(old) | {'duplicate': True}
            batch_id = old['id'] if old else uuid4().hex
            changed = 0
            if draft.kind == 'excel':
                ids = [x[0] for x in db.execute('SELECT id FROM exams WHERE term=? AND active=1', (draft.term,))]
                self._invalidate(db, ids)
            for row in draft.rows:
                payload = dict(row)
                if draft.kind == 'notice':
                    matches = db.execute('SELECT * FROM exams WHERE term=? AND student_id=? AND course=? AND active=1',
                                         (draft.term, row['student_id'], row['course'])).fetchall()
                    if len(matches) > 1:
                        raise ValueError('同名课程存在多条考试，请核对课程编号和通知后再补充。')
                    if matches:
                        previous = json.loads(matches[0]['payload'])
                        if any(previous[k] != row[k] for k in ('start', 'end', 'method')):
                            raise ValueError('个人通知与已导入的考试时间或方式冲突，请核对后更新学校Excel。')
                        if previous['room'] and row['room'] and previous['room'] != row['room']:
                            raise ValueError('个人通知与已有考场冲突，请核对资料。')
                        if not row['room'] or previous['room'] == row['room']:
                            continue
                        payload = previous | {'room': row['room'], 'room_source': row['room_source']}
                        self._invalidate(db, [matches[0]['id']])
                eid = digest_json([batch_id, payload, uuid4().hex])
                db.execute('INSERT INTO exams VALUES (?,?,?,?,?,?,1,?)',
                           (eid, batch_id, draft.term, row['student_id'], row['course'], row['start'],
                            json.dumps(payload, ensure_ascii=False)))
                changed += 1
            if not old:
                db.execute('INSERT INTO imports VALUES (?,?,?,?,?,?,?)',
                           (batch_id, draft.term, draft.digest, draft.source, draft.kind, len(draft.rows), datetime.now(BEIJING).isoformat()))
            return {'id': batch_id, 'count': len(draft.rows), 'duplicate': changed == 0}

    def query(self, sid, term):
        with self.connection() as db:
            return [json.loads(row['payload']) | {'id': row['id'], 'term': row['term']}
                    for row in db.execute('SELECT * FROM exams WHERE student_id=? AND term=? AND active=1 ORDER BY start,course',
                                          (student_id(sid), valid_term(term)))]

    def preview_tasks(self, sid, term, ids, offsets, recipient, now, *, owner=None):
        recipient = validate_address(recipient)
        owner = validate_address(owner if owner is not None else recipient)
        sid = student_id(sid)
        if not ids or len(set(ids)) > 100:
            raise ValueError('请勾选考试，单次最多100门。')
        if not offsets or len(set(offsets)) > 10 or any(type(x) is not int or not 1 <= x <= 525600 for x in offsets):
            raise ValueError('提醒提前量无效。')
        available = {x['id']: x for x in self.query(sid, term)}
        if any(eid not in available for eid in ids):
            raise ValueError('考试记录已变更或不属于当前学号，请重新查询。')
        tasks, expired = [], []
        for eid in dict.fromkeys(ids):
            row = available[eid]
            for offset in sorted(set(offsets), reverse=True):
                item = {'exam': row, 'offset': offset, 'due_at': timestamp(row['start']) - offset * 60}
                (tasks if item['due_at'] > now else expired).append(item)
        if len(tasks) > 100:
            raise ValueError('单批最多100个有效提醒，请减少勾选。')
        return dict(student_id=sid, term=term, owner=owner, recipient=recipient, tasks=tasks, expired=expired)

    def create_tasks(self, plan, confirmation, now, admission=None):
        # 与普通提醒一致：先取得数据库写锁，再读取准入时钟并保持服务锁至提交。
        with self.connection(write=True) as db:
            with admission() if admission else nullcontext(now) as current:
                result = self._create_tasks_locked(db, plan, confirmation, current)
                db.commit()
                return result

    def _create_tasks_locked(self, db, plan, confirmation, now):
        if not plan['tasks'] or not confirmation:
            raise ValueError('没有未来的有效提醒可创建。历史考试仅供查询。')
        recipient = validate_address(plan['recipient'])
        owner = validate_address(plan.get('owner', recipient))
        result = []
        for item in plan['tasks']:
                exam = item['exam']
                current = db.execute('SELECT * FROM exams WHERE id=? AND student_id=? AND term=? AND active=1',
                                     (exam['id'], plan['student_id'], plan['term'])).fetchone()
                if current is None:
                    raise ValueError('考试资料已更新，请重新查询和预览。')
                payload = json.loads(current['payload'])
                if payload != {k:v for k,v in exam.items() if k not in ('id', 'term')}:
                    raise ValueError('考试内容与预览不一致，请重新预览。')
                due = timestamp(payload['start']) - item['offset'] * 60
                if due != item['due_at'] or due <= now:
                    raise ValueError('部分提醒时间已过，请重新预览。')
                key = digest_json([exam['id'], recipient, item['offset']])
                old = db.execute('SELECT * FROM tasks WHERE token=?', (key,)).fetchone()
                if old:
                    if old['owner'] != owner or (old['recipient'] or old['owner']) != recipient:
                        raise ValueError('已有任务的发件或收件邮箱不同，请先核对原提醒。')
                    if old['status'] != 'pending':
                        state = {'cancelled':'已取消','sending':'发送中','accepted':'服务器已接受',
                                 'failed':'发送失败','unknown':'状态不明','missed':'已错过',
                                 'invalidated':'已作废'}.get(old['status'],'非待执行')
                        raise ValueError(f'该考试、收件邮箱及提前量已有{state}记录，本次整批未创建。'
                                         '请查看原任务；本版本不自动恢复或重发，请移除该提前量后重新预览。')
                    result.append(dict(old))
                    continue
                count = db.execute("SELECT count(*) FROM tasks WHERE owner=? AND status='pending'", (owner,)).fetchone()[0]
                if count >= 100:
                    raise ValueError('待执行考试提醒最多100条，本次整批未创建。')
                tid = uuid4().hex
                db.execute('INSERT INTO tasks(id,token,owner,item,due_at,created_at,updated_at,status,recipient) VALUES (?,?,?,?,?,?,?,?,?)',
                           (tid, key, owner, payload['course'], due, now, now, 'pending', recipient))
                db.execute('INSERT INTO exam_tasks VALUES (?,?,?,?,?)',
                           (tid, exam['id'], plan['student_id'], item['offset'], json.dumps(exam, ensure_ascii=False)))
                self.event(db, tid, now, 'pending', '用户确认考试提醒')
                result.append(dict(db.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()))
        return result

    def claim_due(self, owner, now):
        # 连续运行时允许排队任务在开考前继续发送；重启过期仍由recover标为missed。
        with self.connection(write=True) as db:
            rows = db.execute("SELECT t.*,e.snapshot FROM tasks t JOIN exam_tasks e ON e.task_id=t.id WHERE t.owner=? AND t.status='pending' AND t.due_at<=? ORDER BY t.due_at,t.id", (owner, now)).fetchall()
            for row in rows:
                exam = json.loads(row['snapshot'])
                if timestamp(exam['start']) <= now:
                    self.transition(db, row['id'], now, 'missed', '考试已开始，未发送过期提醒。')
                    continue
                self.transition(db, row['id'], now, 'sending', '已领取，正在提交邮件。')
                return dict(db.execute('SELECT * FROM tasks WHERE id=?', (row['id'],)).fetchone())
        return None

    def personal_tasks(self, sid, recipient=None, *, owner=None):
        with self.connection() as db:
            return [dict(x) for x in db.execute(
                "SELECT t.*,e.offset_minutes FROM tasks t JOIN exam_tasks e ON e.task_id=t.id WHERE e.student_id=? AND (? IS NULL OR COALESCE(NULLIF(t.recipient,''),t.owner)=?) AND (? IS NULL OR t.owner=?) ORDER BY t.due_at,t.id",
                (student_id(sid), recipient, recipient, owner, owner))]

    def cancel_personal(self, sid, recipient, task_id, now, *, owner=None):
        with self.connection(write=True) as db:
            row = db.execute("SELECT t.id FROM tasks t JOIN exam_tasks e ON e.task_id=t.id WHERE t.id=? AND (? IS NULL OR COALESCE(NULLIF(t.recipient,''),t.owner)=?) AND e.student_id=? AND (? IS NULL OR t.owner=?) AND t.status='pending'",
                             (task_id, recipient, recipient, student_id(sid), owner, owner)).fetchone()
            if row is None:
                return False
            self.transition(db, task_id, now, 'cancelled', '用户取消考试提醒，未发送。')
            return True

    def make_mail(self, task):
        with self.connection() as db:
            row = db.execute('SELECT * FROM exam_tasks WHERE task_id=?', (task['id'],)).fetchone()
            if row is None:
                raise ValueError('缺少考试提醒快照。')
            exam = json.loads(row['snapshot'])
        return Draft(task['owner'], validate_address(task.get('recipient') or task['owner']), f"[信息学院助理] 考试提醒：{exam['course']}",
                     f"课程：{exam['course']}\n学期：{term_label(exam['term'])}\n"
                     f"考试时间：{exam['start']} 至 {exam['end']}（北京时间）\n"
                     f"考试方式：{exam['method']}\n考场：{room_label(exam)}\n"
                     f"本次提醒：提前{row['offset_minutes']}分钟\n资料来源：{exam['source']}\n"
                     f"计划提醒时间：{datetime.fromtimestamp(task['due_at'], BEIJING):%Y-%m-%d %H:%M:%S}（发送或送达可能延迟）\n"
                     f"考场来源：{exam['room_source'] or '未提供'}\n"
                     f"最终以教务系统及正式调整通知为准。\n任务编号：{task['id']}")
