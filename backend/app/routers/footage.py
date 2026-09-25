"""素材管理接口：维护拍摄素材，覆盖提交转码、确认归档、登记丢失等动作。

归档保留规则相关接口都挂在 /api/footage/retention 下：
- GET  /retention/rules   规则表与冲突优先级说明
- GET  /retention/impact  执行前影响口径（待处理/可归档/拦截/跳过）
- POST /retention/archive 按统一口径执行归档，失败可重试
- POST /retention/retry   仅重试上一次处理失败的素材
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.footage import RULE_PRIORITY_NOTE, FootageService

router = APIRouter(prefix="/api/footage", tags=["素材管理"])

service = FootageService()

LIST_FIELDS = ["素材编号", "素材类型", "拍摄日期", "文件大小", "存储介质", "转码格式", "备份位置", "素材状态"]
STATUSES = ["待转码", "转码中", "已归档", "已丢失"]


def _parse_as_of(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="基准日期格式应为 YYYY-MM-DD")


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按素材编号检索"),
    status: str | None = Query(default=None, description="待转码、转码中、已归档、已丢失"),
    scope: str | None = Query(default=None, description="pending=只看归档待处理范围，其余值不过滤"),
    as_of: str | None = Query(default=None, description="判定保留期的基准日期 YYYY-MM-DD，默认今天"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按素材编号、状态与待处理范围过滤素材列表；判定口径与归档结果完全一致。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword,
        status=status,
        scope=scope,
        page=page,
        size=size,
        as_of=_parse_as_of(as_of),
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/retention/rules")
def retention_rules() -> dict[str, Any]:
    """展示保留规则表，以及规则冲突时的优先级口径。"""
    return {"rule_priority": RULE_PRIORITY_NOTE, "rules": service.retention_rules()}


@router.get("/retention/impact")
def retention_impact(
    as_of: str | None = Query(default=None, description="判定保留期的基准日期 YYYY-MM-DD，默认今天"),
) -> dict[str, Any]:
    """归档执行前的影响口径预览：不写任何数据，只说明会动哪些素材、拦哪些素材。"""
    return service.impact_preview(as_of=_parse_as_of(as_of))


@router.post("/retention/archive")
def retention_archive(payload: EntryPayload | None = None) -> dict[str, Any]:
    """按预览同一口径执行归档；可传 ids 限定素材，未命中待处理范围的会被跳过。"""
    values = payload.values if payload else {}
    raw_ids = values.get("ids") or []
    entry_ids: list[int] = []
    for raw in raw_ids:
        try:
            entry_ids.append(int(raw))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"素材编号「{raw}」不是合法 id")
    return service.run_archive(entry_ids=entry_ids or None, as_of=_parse_as_of(values.get("as_of")))


@router.post("/retention/retry")
def retention_retry() -> dict[str, Any]:
    """只重试上一次处理失败（archive_failed）的素材；已归档内容不会被重复处理。"""
    return service.run_archive(only_failed=True)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出素材管理清单：返回当前过滤条件下的全量数据（含保留期判定标记）。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "footage", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条拍摄素材明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"拍摄素材 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条拍摄素材，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="拍摄素材已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条拍摄素材执行提交转码、确认归档、登记丢失；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
