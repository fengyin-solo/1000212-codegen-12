"""拍摄素材归档保留规则接口。

- GET  /api/retention/rules    规则与冲突优先级说明
- GET  /api/retention/pending  待处理范围（待归档 / 禁止归档 / 待人工确认）
- GET  /api/retention/preview  执行前影响口径（不写数据）
- POST /api/retention/run      按预览同一口径执行归档
- POST /api/retention/{id}/retry  单条失败重试
"""
from __future__ import annotations

from fastapi import APIRouter

from app.schemas import ActionResult
from app.services.retention import RetentionService

router = APIRouter(prefix="/api/retention", tags=["归档保留规则"])

service = RetentionService()


@router.get("/rules")
def get_rules() -> dict:
    """返回保留期限规则与规则冲突时的优先级说明。"""
    return service.rule_book()


@router.get("/pending")
def get_pending() -> dict:
    """返回待处理范围：类型不一致、临近到期、超上限被拦截的素材都在这里。"""
    items = service.pending()
    return {"total": len(items), "items": items}


@router.get("/preview")
def preview() -> dict:
    """执行前先展示影响口径：汇总数量、逐条判定与命中规则，不改动任何数据。"""
    return service.preview()


@router.post("/run")
def run() -> dict:
    """按预览口径执行归档；超上限不归档、已归档不重复、备份未就绪记失败可重试。"""
    return service.run()


@router.post("/{entry_id}/retry", response_model=ActionResult)
def retry_one(entry_id: int) -> ActionResult:
    """对处理失败的素材重试：重新套用同一判定标准，成功才落归档状态。"""
    entry, message = service.retry_one(entry_id)
    if entry is None:
        return ActionResult(ok=False, message=message)
    ok = entry["判定"] == "skip" or message.startswith("重试成功")
    return ActionResult(ok=ok, message=message, entry=entry)
