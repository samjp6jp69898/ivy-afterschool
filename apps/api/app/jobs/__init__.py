"""背景工作彙整（BACKEND-018 / BACKEND-020）。

各工作模組（BACKEND-066 auth_cleanup、BACKEND-209 outbox_jobs、營運模組的 attendance_jobs /
pickup_jobs）在此 import，讓 ``@scheduled_job`` 在 lifespan 啟動 scheduler 前完成註冊。
"""

from app.jobs import auth_cleanup as auth_cleanup
from app.notifications import outbox_jobs as outbox_jobs
