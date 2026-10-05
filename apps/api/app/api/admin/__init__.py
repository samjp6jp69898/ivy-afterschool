"""後台 API 彙整（BACKEND-020）：各 endpoint task 在自己的模組建立 ``router`` 並在此 include。

路由順序：同一 prefix 底下固定路徑（例如 ``/students/import``）必須先於 ``/{student_id}`` 註冊，
由各模組內的宣告順序保證。
"""

from fastapi import APIRouter

from app.api.admin import (
    attendance,
    audit_logs,
    auth,
    classes,
    dashboard,
    exams,
    guardians,
    homework,
    leaves,
    notifications,
    pickup,
    reference,
    roles,
    settings,
    staff_users,
    students,
)

admin_router = APIRouter(prefix="/api/admin")
admin_router.include_router(auth.router)
admin_router.include_router(roles.router)
admin_router.include_router(audit_logs.router)
admin_router.include_router(settings.router)
for _router in reference.reference_routers:
    admin_router.include_router(_router)
admin_router.include_router(students.router)
admin_router.include_router(exams.student_exams_router)
admin_router.include_router(guardians.router)
admin_router.include_router(exams.router)
admin_router.include_router(dashboard.router)
admin_router.include_router(staff_users.router)
admin_router.include_router(classes.router)
admin_router.include_router(notifications.router)
admin_router.include_router(attendance.router)
admin_router.include_router(leaves.router)
admin_router.include_router(homework.router)
admin_router.include_router(pickup.router)

__all__ = ["admin_router"]
