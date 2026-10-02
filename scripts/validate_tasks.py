#!/usr/bin/env python3
"""驗證 docs/tasks/*/tasks.json：JSON 合法、schema、depends_on 完整、循環依賴、
殘留歷史敘述、TDD 欄位、review 與 status 的一致性。

用法：
  python3 scripts/validate_tasks.py [repo_root]
  python3 scripts/validate_tasks.py [repo_root] --ready [AREA]
  python3 scripts/validate_tasks.py [repo_root] --in-review [AREA]
  python3 scripts/validate_tasks.py [repo_root] --blocked [AREA]
  python3 scripts/validate_tasks.py [repo_root] --stats

--ready [AREA]      列出可開始的 task：status=pending 且 depends_on 全部 done/superseded。
--in-review [AREA]  列出等待 reviewer 核可的 task（status=in_review）。
--blocked [AREA]    列出 blocked 的 task 與其 open_design_questions。
--stats             各區域各 status 的數量。
規則見 docs/tasks/README.md。
"""

import collections
import glob
import json
import os
import re
import sys

args = [a for a in sys.argv[1:] if not a.startswith("--")]
flags = {a for a in sys.argv[1:] if a.startswith("--")}
READY_MODE = "--ready" in flags
IN_REVIEW_MODE = "--in-review" in flags
BLOCKED_MODE = "--blocked" in flags
STATS_MODE = "--stats" in flags
LIST_MODE = READY_MODE or IN_REVIEW_MODE or BLOCKED_MODE or STATS_MODE
root = args[0] if args else "."
LIST_AREA = args[1].upper() if LIST_MODE and len(args) > 1 else None

AREAS = {
    "infra": "INFRA",
    "db": "DB",
    "backend": "BACKEND",
    "frontend": "FRONTEND",
    "parent": "PARENT",
}
VALID_STATUS = {"pending", "in_progress", "in_review", "done", "blocked", "superseded"}
VALID_REVIEW = {"pending", "pass", "concern"}
VALID_UNITS = {
    "method",
    "endpoint",
    "component",
    "view",
    "composable",
    "store",
    "api_client",
    "migration",
    "seed",
    "config",
    "script",
    "test",
    "doc",
}
VALID_MARKERS = {"unit", "integration", "vitest", "playwright"}
VALID_MODELS = {"sonnet-5", "opus-5", "fable-5"}
REQ = [
    "id",
    "title",
    "description",
    "granularity_unit",
    "target_path",
    "source_ref",
    "status",
    "assignee_session",
    "depends_on",
    "suggested_model",
    "risk_notes",
    "open_design_questions",
    "tdd",
    "review",
]
BANNED_CASE_PATTERNS = (
    "assert True",
    "is not None",
    "assert_called",
    "不炸",
    "不拋例外即可",
    "呼叫成功即可",
    "toBeTruthy()",
    "toBeDefined()",
)
HISTORY_PATTERNS = ("原設計", "原描述保留", "舊版", "曾考慮", "改為（原", "v2】", "v3】")
FULL_RUN_COMMANDS = {
    "pytest",
    "just test",
    "just lint",
    "just web-test",
    "pnpm test",
    "pnpm vitest run",
    "pnpm lint",
    "ruff check .",
}

files = sorted(glob.glob(os.path.join(root, "docs/tasks/*/tasks.json")))
all_tasks = {}
task_file = {}
errors = []
warns = []
data = {}

for f in files:
    area_dir = os.path.basename(os.path.dirname(f))
    prefix = AREAS.get(area_dir)
    try:
        with open(f, encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception as e:  # noqa: BLE001
        errors.append(f"{f}: JSON 解析失敗 {e}")
        continue
    data[f] = d
    if prefix is None:
        errors.append(f"{f}: 未知區域目錄 {area_dir}（合法：{sorted(AREAS)}）")
        continue
    for k in ("area", "version", "last_updated", "tasks"):
        if k not in d:
            errors.append(f"{f}: 頂層缺 {k}")
    for t in d.get("tasks", []):
        tid = t.get("id")
        if tid in all_tasks:
            errors.append(f"{f}: 重複 id {tid}")
        all_tasks[tid] = t
        task_file[tid] = f
        if LIST_MODE:
            continue

        if not re.fullmatch(rf"{prefix}-\d{{3}}", tid or ""):
            errors.append(f"{f}: id {tid!r} 不符合 {prefix}-NNN")
        for k in REQ:
            if k not in t:
                errors.append(f"{tid}: 缺欄位 {k}")
        status = t.get("status")
        if status not in VALID_STATUS:
            errors.append(f"{tid}: status={status!r} 不合法 {sorted(VALID_STATUS)}")
        if t.get("granularity_unit") not in VALID_UNITS:
            errors.append(f"{tid}: granularity_unit={t.get('granularity_unit')!r} 不合法")
        if t.get("suggested_model") not in VALID_MODELS:
            errors.append(f"{tid}: suggested_model={t.get('suggested_model')!r} 不合法")
        if not (t.get("description") or "").strip():
            errors.append(f"{tid}: description 空白")
        if status in ("pending", "in_progress", "in_review", "done") and "驗收標準" not in (
            t.get("description") or ""
        ):
            errors.append(f"{tid}: description 缺「驗收標準」段落")

        review = t.get("review") or {}
        rst = review.get("status")
        if rst not in VALID_REVIEW:
            errors.append(f"{tid}: review.status={rst!r} 不合法 {sorted(VALID_REVIEW)}")
        if status == "done" and rst != "pass":
            errors.append(f"{tid}: status=done 但 review.status={rst!r}（done 必須由 reviewer 給 pass）")
        if status == "done" and not review.get("reviewer"):
            errors.append(f"{tid}: status=done 但 review.reviewer 空白")
        if status == "blocked" and not (t.get("open_design_questions") or t.get("risk_notes")):
            errors.append(f"{tid}: status=blocked 但沒寫卡在哪（open_design_questions / risk_notes）")

        blob = json.dumps(t, ensure_ascii=False)
        for pat in HISTORY_PATTERNS:
            if pat in blob:
                warns.append(f"{tid}: 疑似殘留歷史敘述 '{pat}'")

        if status != "superseded":
            tdd = t.get("tdd")
            if not isinstance(tdd, dict):
                errors.append(f"{tid}: tdd 欄位缺漏或格式錯誤")
                continue
            for k in ("test_path", "markers", "red_cases", "run", "notes"):
                if k not in tdd:
                    errors.append(f"{tid}: tdd 缺 {k}")
            cases = tdd.get("red_cases") or []
            if tdd.get("test_path") and len(cases) < 2:
                errors.append(f"{tid}: tdd.red_cases 少於 2 條")
            if tdd.get("test_path") and not (tdd.get("run") or "").strip():
                errors.append(f"{tid}: 有 test_path 但 tdd.run 空白")
            if not tdd.get("test_path") and not (tdd.get("notes") or "").strip():
                errors.append(f"{tid}: tdd.test_path 為 null 但 notes 沒說明替代驗收方式")
            for c in cases:
                if any(p in c for p in BANNED_CASE_PATTERNS):
                    warns.append(f"{tid}: red_case 疑似恆真/只測 mock：{c[:60]}")
            run = (tdd.get("run") or "").strip()
            if run in FULL_RUN_COMMANDS:
                errors.append(f"{tid}: tdd.run 是無參數的全量指令：{run}")
            for m in tdd.get("markers") or []:
                if m not in VALID_MARKERS:
                    warns.append(f"{tid}: 未註冊的 marker {m}")


def area_of(tid):
    return tid.split("-")[0]


def sort_key(tid):
    return (area_of(tid), int(tid.split("-")[1]))


def print_by_area(ids, empty_msg, extra=None):
    if not ids:
        print(empty_msg)
        return
    by_area = collections.defaultdict(list)
    for tid in ids:
        by_area[area_of(tid)].append(tid)
    for area in sorted(by_area):
        area_ids = sorted(by_area[area], key=sort_key)
        print(f"{area} ({len(area_ids)}):")
        for tid in area_ids:
            print(f"  {tid}  {all_tasks[tid]['title']}")
            if extra:
                for line in extra(all_tasks[tid]):
                    print(f"      - {line}")


def in_area(tid):
    return not LIST_AREA or area_of(tid) == LIST_AREA


if READY_MODE:

    def satisfied(dep):
        t = all_tasks.get(dep)
        return t is not None and t.get("status") in ("done", "superseded")

    ready = [
        tid
        for tid, t in all_tasks.items()
        if t.get("status") == "pending"
        and in_area(tid)
        and all(satisfied(d) for d in t.get("depends_on", []))
    ]
    print_by_area(ready, "沒有可開始的 task")
    sys.exit(0)

if IN_REVIEW_MODE:
    ids = [tid for tid, t in all_tasks.items() if t.get("status") == "in_review" and in_area(tid)]
    print_by_area(ids, "目前沒有等待 review 的 task")
    sys.exit(0)

if BLOCKED_MODE:
    ids = [tid for tid, t in all_tasks.items() if t.get("status") == "blocked" and in_area(tid)]
    print_by_area(ids, "目前沒有 blocked 的 task", extra=lambda t: t.get("open_design_questions") or [])
    sys.exit(0)

if STATS_MODE:
    counter = collections.defaultdict(collections.Counter)
    for tid, t in all_tasks.items():
        if in_area(tid):
            counter[area_of(tid)][t.get("status")] += 1
    for area in sorted(counter):
        c = counter[area]
        detail = " ".join(f"{s}={c[s]}" for s in sorted(c))
        print(f"{area:<9} total={sum(c.values()):<4} {detail}")
    sys.exit(0)

for tid, t in all_tasks.items():
    for dep in t.get("depends_on", []):
        if dep not in all_tasks:
            errors.append(f"{tid}: depends_on 指向不存在的 {dep}")
        elif dep == tid:
            errors.append(f"{tid}: 自我依賴")
        elif all_tasks[dep].get("status") == "superseded":
            warns.append(f"{tid}: 依賴已 superseded 的 {dep}，應改依賴接手的 task")

color = {}
cycles = []
sys.setrecursionlimit(10000)


def dfs(u, stack):
    color[u] = 1
    stack.append(u)
    for v in all_tasks[u].get("depends_on", []):
        if v not in all_tasks:
            continue
        if color.get(v) == 1:
            cycles.append(stack[stack.index(v) :] + [v])
        elif color.get(v) is None:
            dfs(v, stack)
    stack.pop()
    color[u] = 2


for u in all_tasks:
    if color.get(u) is None:
        dfs(u, [])
for c in cycles:
    errors.append("循環依賴: " + " -> ".join(c))

for f, d in data.items():
    print(f"{f}: version={d.get('version')} last_updated={d.get('last_updated')} tasks={len(d.get('tasks', []))}")
print(f"\n共 {len(all_tasks)} 個 task")
if warns:
    print(f"\nWARN ({len(warns)}):")
    for w in warns:
        print("  ", w)
if errors:
    print(f"\nERROR ({len(errors)}):")
    for err in errors:
        print("  ", err)
    sys.exit(1)
print("\nOK: 無 ERROR")
