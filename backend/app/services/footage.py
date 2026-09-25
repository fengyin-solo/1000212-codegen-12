"""素材管理业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.retention import evaluate_entry
from app.store import store

MODULE = "footage"
REQUIRED_FIELDS = ["素材编号", "素材类型", "拍摄日期"]
STATUS_ORDER = ["待转码", "转码中", "已归档", "已丢失"]
ACTION_RULES = {"提交转码": "转码中", "确认归档": "已归档", "登记丢失": "已丢失"}
NEGATIVE_ACTIONS = []


class FootageService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("素材编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        page_rows = rows[start:start + size]
        # 保留判定与归档执行共用 evaluate_entry，列表与结果同一标准。
        items = []
        for row in page_rows:
            item = dict(row)
            judged = evaluate_entry(row)
            item["保留判定"] = judged["判定说明"]
            item["保留到期日"] = judged["到期日"]
            item["距到期天数"] = judged["距到期天数"]
            item["命中规则"] = judged["命中规则"]
            items.append(item)
        return items, total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"拍摄素材 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于素材管理可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"拍摄素材已{action}"
