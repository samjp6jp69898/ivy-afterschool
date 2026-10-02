"""BACKEND-070：權限碼（architecture_decisions §6、domain_spec §3，共 28 碼）。

- 扁平字串權限碼 ``<module>:<action>``；前端 ``apps/web/src/constants/permissions.ts`` 同步一份，
  有一致性測試。
- ``admin`` 角色在 DB 中存 ``{*}``，由計算有效權限的程式把 ``*`` 展開成全部值；
  ``*`` 本身不是合法權限碼。
- ``nfc:manage`` 在 NFC 解除 blocked 時由 BACKEND-510 加入。
- 新增權限碼時：改本 enum、domain_spec §3、DB seed（需要預設授予時）、前端常數。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

WILDCARD: Final = "*"


class Permission(StrEnum):
    DASHBOARD_READ = "dashboard:read"

    STAFF_READ = "staff:read"
    STAFF_WRITE = "staff:write"
    ROLES_READ = "roles:read"
    ROLES_WRITE = "roles:write"
    AUDIT_READ = "audit:read"

    SETTINGS_READ = "settings:read"
    SETTINGS_WRITE = "settings:write"

    CLASSES_READ = "classes:read"
    CLASSES_WRITE = "classes:write"
    STUDENTS_READ = "students:read"
    STUDENTS_WRITE = "students:write"
    STUDENTS_SENSITIVE = "students:sensitive"
    STUDENTS_PURGE = "students:purge"
    GUARDIANS_WRITE = "guardians:write"

    ATTENDANCE_READ = "attendance:read"
    ATTENDANCE_OPERATE = "attendance:operate"
    ATTENDANCE_AMEND = "attendance:amend"

    LEAVES_READ = "leaves:read"
    LEAVES_WRITE = "leaves:write"

    HOMEWORK_READ = "homework:read"
    HOMEWORK_WRITE = "homework:write"

    PICKUP_READ = "pickup:read"
    PICKUP_OPERATE = "pickup:operate"
    PICKUP_OVERRIDE = "pickup:override"

    EXAMS_READ = "exams:read"
    EXAMS_WRITE = "exams:write"
    EXAMS_PUBLISH = "exams:publish"


ALL_PERMISSIONS: Final[frozenset[str]] = frozenset(p.value for p in Permission)

# 說明取自 domain_spec §3「說明」欄
PERMISSION_LABELS: Final[dict[Permission, str]] = {
    Permission.DASHBOARD_READ: "首頁儀表板",
    Permission.STAFF_READ: "查看員工帳號",
    Permission.STAFF_WRITE: "管理員工帳號",
    Permission.ROLES_READ: "查看角色與權限",
    Permission.ROLES_WRITE: "管理角色與權限",
    Permission.AUDIT_READ: "稽核紀錄",
    Permission.SETTINGS_READ: "查看系統設定與參考資料",
    Permission.SETTINGS_WRITE: "修改系統設定與參考資料",
    Permission.CLASSES_READ: "查看班級",
    Permission.CLASSES_WRITE: "管理班級",
    Permission.STUDENTS_READ: "查看學生",
    Permission.STUDENTS_WRITE: "管理學生",
    Permission.STUDENTS_SENSITIVE: "查看與寫入身分證、健康備註",
    Permission.STUDENTS_PURGE: "永久刪除（匿名化）退班學生",
    Permission.GUARDIANS_WRITE: "管理監護人與綁定碼",
    Permission.ATTENDANCE_READ: "查出勤",
    Permission.ATTENDANCE_OPERATE: "到班離班登記",
    Permission.ATTENDANCE_AMEND: "改判已登記的出勤（寫 audit）",
    Permission.LEAVES_READ: "查請假",
    Permission.LEAVES_WRITE: "代登記與取消請假",
    Permission.HOMEWORK_READ: "查看作業進度",
    Permission.HOMEWORK_WRITE: "更新作業進度",
    Permission.PICKUP_READ: "查看接送佇列",
    Permission.PICKUP_OPERATE: "接送回覆、確認、完成",
    Permission.PICKUP_OVERRIDE: "代理接送強制完成",
    Permission.EXAMS_READ: "查看成績",
    Permission.EXAMS_WRITE: "登錄成績",
    Permission.EXAMS_PUBLISH: "發布成績",
}


@dataclass(frozen=True)
class PermissionGroup:
    key: str
    label: str
    permissions: tuple[Permission, ...]


PERMISSION_GROUPS: Final[list[PermissionGroup]] = [
    PermissionGroup("dashboard", "儀表板", (Permission.DASHBOARD_READ,)),
    PermissionGroup(
        "accounts",
        "帳號與權限",
        (
            Permission.STAFF_READ,
            Permission.STAFF_WRITE,
            Permission.ROLES_READ,
            Permission.ROLES_WRITE,
            Permission.AUDIT_READ,
        ),
    ),
    PermissionGroup("settings", "系統設定", (Permission.SETTINGS_READ, Permission.SETTINGS_WRITE)),
    PermissionGroup(
        "students",
        "班級與學生",
        (
            Permission.CLASSES_READ,
            Permission.CLASSES_WRITE,
            Permission.STUDENTS_READ,
            Permission.STUDENTS_WRITE,
            Permission.STUDENTS_SENSITIVE,
            Permission.STUDENTS_PURGE,
            Permission.GUARDIANS_WRITE,
        ),
    ),
    PermissionGroup(
        "attendance",
        "出勤",
        (
            Permission.ATTENDANCE_READ,
            Permission.ATTENDANCE_OPERATE,
            Permission.ATTENDANCE_AMEND,
        ),
    ),
    PermissionGroup("leaves", "請假", (Permission.LEAVES_READ, Permission.LEAVES_WRITE)),
    PermissionGroup("homework", "作業進度", (Permission.HOMEWORK_READ, Permission.HOMEWORK_WRITE)),
    PermissionGroup(
        "pickup",
        "接送",
        (Permission.PICKUP_READ, Permission.PICKUP_OPERATE, Permission.PICKUP_OVERRIDE),
    ),
    PermissionGroup(
        "exams",
        "考試成績",
        (Permission.EXAMS_READ, Permission.EXAMS_WRITE, Permission.EXAMS_PUBLISH),
    ),
]


def is_valid_permission(code: str) -> bool:
    """只認 enum 中的值；``*`` 不是權限碼（展開由有效權限計算負責）。"""
    return code in ALL_PERMISSIONS
