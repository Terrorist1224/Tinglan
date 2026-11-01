"""路由共用的任务 ID 边界校验；拼接任务目录前先检查 ID 并确认任务存在。"""
from .. import db


def get_task(task_id):
    if len(task_id) != 32 or any(c not in "0123456789abcdef" for c in task_id):
        raise KeyError("任务不存在")
    return db.task(task_id)
