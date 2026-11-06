"""考试工作区的未提交状态。"""
from dataclasses import dataclass, field
from threading import RLock


@dataclass(frozen=True)
class ImportPreview:
    draft: object
    signature: str
    revision: int
    student_id: str


@dataclass
class ExamSession:
    imports: dict = field(default_factory=dict)
    plan: dict | None = None
    plan_signature: str = ''
    token: str = ''
    cancel_id: str = ''
    lock: RLock = field(default_factory=RLock, repr=False)

    def __deepcopy__(self, memo):
        return ExamSession()
