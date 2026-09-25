"""拍摄素材归档保留规则：保留期限判定、影响口径预览与归档执行都收在这里。

口径只有一份（``evaluate_entry``）：素材列表附加的保留判定、待处理范围、
预览影响口径、实际归档结果与单条重试，全部走同一个函数，避免两套算法对不上。

判定依据：素材类型、拍摄日期、归档状态。
- 同时命中「素材类型 + 归档状态」的规则优先级最高；
- 仅命中「素材类型」的通用规则次之；
- 都不命中说明素材类型与规则不一致，进入待处理范围走人工确认，不自动归档；
- 同级规则冲突时保留期短的优先（从严，先到先处理）。
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from app.store import store

MODULE = "footage"

# 临近到期窗口：距到期日小于等于该天数（含已过期）即进入待处理范围。
EXPIRE_WINDOW_DAYS = 7

# 归档前置条件：备份位置未就绪时执行会失败，状态不变，允许稍后重试。
BACKUP_READY_PREFIX = ("待补", "未挂载", "占位")

# 规则顺序即优先级说明的展开顺序；匹配时先精确（类型+状态）后通用（仅类型）。
RETENTION_RULES: list[dict[str, Any]] = [
    {"id": "R1", "素材类型": "正片素材", "归档状态": "未归档", "保留天数": 180, "大小上限GB": 100,
     "说明": "未归档正片自拍摄日起保留 180 天，到期前转入长期归档"},
    {"id": "R2", "素材类型": "正片素材", "归档状态": "已归档", "保留天数": 1095, "大小上限GB": 100,
     "说明": "已归档正片按 3 年长期留存复核，不重复执行归档动作"},
    {"id": "R3", "素材类型": "花絮素材", "归档状态": None, "保留天数": 90, "大小上限GB": 50,
     "说明": "花絮素材不分归档状态，统一保留 90 天"},
    {"id": "R4", "素材类型": "选角素材", "归档状态": None, "保留天数": 30, "大小上限GB": 20,
     "说明": "选角试镜素材统一保留 30 天，超期前归档备查"},
]

# 兜底口径：类型与全部规则不一致时只给出参考保留期，不允许自动归档。
FALLBACK_RULE: dict[str, Any] = {
    "id": "R0", "素材类型": None, "归档状态": None, "保留天数": 7, "大小上限GB": None,
    "说明": "素材类型与保留规则不一致：仅按 7 天参考期提示，须人工确认后再归档",
}

PRIORITY_NOTES = [
    "1. 同时匹配「素材类型 + 归档状态」的规则优先（如未归档正片走 R1，已归档正片走 R2）；",
    "2. 只匹配「素材类型」的通用规则次之（花絮 R3、选角 R4 与归档状态无关）；",
    "3. 两类规则都不命中时按兜底口径 R0：类型不一致，进入待处理范围，不允许自动归档；",
    "4. 同级规则发生冲突时，保留期短的优先（从严判定，先到期先处理）；",
    "5. 文件大小上限为硬约束：超过上限的素材一律不允许归档，只能人工处置；",
    "6. 已归档素材只做留存复核，不重复处理；已登记丢失的素材不参与归档。",
]

# 判定口径 → 中文说明，列表与执行结果共用同一套文案。
DISPOSITION_TEXT = {
    "archive": "待归档（临近到期，符合归档条件）",
    "block": "禁止归档（超过规则上限或状态不允许）",
    "manual": "待人工确认（素材类型与规则不一致）",
    "skip": "已归档，不重复处理",
    "normal": "保留期内，暂不处理",
}


def _archive_state(status: str) -> str:
    return "已归档" if status == "已归档" else "未归档"


def _parse_size_gb(raw: Any) -> float | None:
    """把「120GB」「500MB」「1.2TB」折算成 GB；无法解析时返回 None。"""
    text = str(raw or "").strip().upper()
    match = re.search(r"(\d+(?:\.\d+)?)\s*(TB|GB|MB)?", text)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2) or "GB"
    factor = {"TB": 1024, "GB": 1, "MB": 1 / 1024}[unit]
    return value * factor


def _parse_date(raw: Any) -> date | None:
    text = str(raw or "").strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _match_rule(material_type: str, archive_state: str) -> tuple[dict[str, Any], bool]:
    """按优先级返回命中的规则，以及是否精确命中（类型+状态）。"""
    exact = [rule for rule in RETENTION_RULES
             if rule["素材类型"] == material_type and rule["归档状态"] == archive_state]
    if exact:
        # 同级冲突从严：保留期最短的优先。
        return min(exact, key=lambda r: r["保留天数"]), True
    generic = [rule for rule in RETENTION_RULES
               if rule["素材类型"] == material_type and rule["归档状态"] is None]
    if generic:
        return min(generic, key=lambda r: r["保留天数"]), True
    return FALLBACK_RULE, False


def evaluate_entry(entry: dict[str, Any], *, today: date | None = None) -> dict[str, Any]:
    """对单条素材套用保留规则，返回归档判定明细。列表、预览、执行、重试共用。"""
    today = today or date.today()
    material_type = str(entry.get("素材类型") or "").strip()
    shoot_raw = entry.get("拍摄日期")
    shoot_day = _parse_date(shoot_raw)
    status = str(entry.get("status") or "").strip()
    archive_state = _archive_state(status)

    rule, matched = _match_rule(material_type, archive_state)
    expire_day = shoot_day + timedelta(days=rule["保留天数"]) if shoot_day else None
    days_left = (expire_day - today).days if expire_day else None

    reasons: list[str] = []
    backup_ready = not str(entry.get("备份位置") or "").strip().startswith(BACKUP_READY_PREFIX) \
        and bool(str(entry.get("备份位置") or "").strip())

    # 1) 状态硬口径优先：已归档不重复处理，已丢失不参与归档。
    if status == "已归档":
        disposition = "skip"
        reasons.append("已归档内容只做留存复核，不重复处理")
    elif status == "已丢失":
        disposition = "block"
        reasons.append("素材已登记丢失，不参与归档")
    elif not matched:
        # 2) 类型与全部规则不一致：进入待处理范围，但只能人工确认。
        disposition = "manual"
        reasons.append(f"素材类型「{material_type or '未填写'}」与保留规则不一致，需人工确认")
    elif shoot_day is None:
        disposition = "manual"
        reasons.append(f"拍摄日期「{shoot_raw or '未填写'}」无法解析，需人工确认")
    else:
        size_gb = _parse_size_gb(entry.get("文件大小"))
        limit_gb = rule["大小上限GB"]
        # 3) 大小上限为硬约束：无论是否到期，超限一律不允许归档。
        if size_gb is not None and limit_gb is not None and size_gb > limit_gb:
            disposition = "block"
            reasons.append(f"文件大小 {size_gb:g}GB 超过规则上限 {limit_gb:g}GB，不允许归档")
        elif days_left is not None and days_left <= EXPIRE_WINDOW_DAYS:
            # 4) 临近到期（含已过期）进入待处理范围；能否一次归档成功看备份是否就绪。
            disposition = "archive"
            if days_left < 0:
                reasons.append(f"已超过保留期限 {-days_left} 天，应立即归档")
            elif days_left == 0:
                reasons.append("今天为保留到期日")
            else:
                reasons.append(f"距保留到期仅剩 {days_left} 天，进入归档窗口")
            if not backup_ready:
                reasons.append("备份位置未就绪，执行会失败并可在补齐后重试")
        else:
            disposition = "normal"
            reasons.append(f"保留期内（剩余 {days_left} 天），暂不处理")

    return {
        "id": entry.get("id"),
        "素材编号": entry.get("素材编号"),
        "素材类型": material_type,
        "拍摄日期": str(shoot_day) if shoot_day else str(shoot_raw or ""),
        "文件大小": entry.get("文件大小"),
        "备份位置": entry.get("备份位置"),
        "当前状态": status,
        "归档状态口径": archive_state,
        "命中规则": rule["id"],
        "规则说明": rule["说明"],
        "保留天数": rule["保留天数"],
        "到期日": str(expire_day) if expire_day else None,
        "距到期天数": days_left,
        "备份就绪": backup_ready,
        "判定": disposition,
        "判定说明": DISPOSITION_TEXT[disposition],
        "待处理": disposition in ("archive", "block", "manual"),
        "原因": reasons,
    }


class RetentionService:
    """保留规则的查询、预览、执行与重试；全部只读 footage 表，执行时才写状态。"""

    def rule_book(self) -> dict[str, Any]:
        return {
            "窗口天数": EXPIRE_WINDOW_DAYS,
            "规则": RETENTION_RULES,
            "兜底规则": FALLBACK_RULE,
            "优先级说明": PRIORITY_NOTES,
        }

    def evaluate_all(self, *, today: date | None = None) -> list[dict[str, Any]]:
        return [evaluate_entry(row, today=today) for row in store.rows(MODULE)]

    def pending(self, *, today: date | None = None) -> list[dict[str, Any]]:
        return [item for item in self.evaluate_all(today=today) if item["待处理"]]

    def preview(self, *, today: date | None = None) -> dict[str, Any]:
        """执行前影响口径：只判定不写数据，供执行前核对范围与冲突。"""
        items = self.evaluate_all(today=today)
        summary = {
            "素材总数": len(items),
            "待归档": sum(1 for i in items if i["判定"] == "archive"),
            "禁止归档": sum(1 for i in items if i["判定"] == "block"),
            "待人工确认": sum(1 for i in items if i["判定"] == "manual"),
            "已归档跳过": sum(1 for i in items if i["判定"] == "skip"),
            "保留期内": sum(1 for i in items if i["判定"] == "normal"),
            "待处理合计": sum(1 for i in items if i["待处理"]),
        }
        return {
            "基准日期": str(today or date.today()),
            "临近窗口天数": EXPIRE_WINDOW_DAYS,
            "规则": RETENTION_RULES,
            "兜底规则": FALLBACK_RULE,
            "优先级说明": PRIORITY_NOTES,
            "汇总": summary,
            "明细": items,
        }

    def run(self, *, today: date | None = None) -> dict[str, Any]:
        """按预览同一口径执行归档：仅处理「待归档」条目，其余原样带回，不做隐式过滤。"""
        results: list[dict[str, Any]] = []
        counters = {"archived": 0, "failed": 0, "blocked": 0, "manual": 0, "skipped": 0, "normal": 0}
        for row in store.rows(MODULE):
            judged = evaluate_entry(row, today=today)
            disposition = judged["判定"]
            result: dict[str, Any] = {
                "id": judged["id"], "素材编号": judged["素材编号"], "判定": disposition,
                "命中规则": judged["命中规则"], "到期日": judged["到期日"],
                "距到期天数": judged["距到期天数"], "原因": list(judged["原因"]),
            }
            if disposition == "skip":
                result["处理结果"] = "skipped"
                result["说明"] = "已归档素材不重复处理"
                counters["skipped"] += 1
            elif disposition == "block":
                result["处理结果"] = "blocked"
                result["说明"] = "超过上限或状态不允许，未执行归档"
                counters["blocked"] += 1
            elif disposition == "manual":
                result["处理结果"] = "manual"
                result["说明"] = "类型不一致等人工口径，未自动归档"
                counters["manual"] += 1
            elif disposition == "normal":
                result["处理结果"] = "normal"
                result["说明"] = "保留期内，本次不处理"
                counters["normal"] += 1
            elif not judged["备份就绪"]:
                # 业务前置条件不满足：不落状态，允许补齐备份位置后重试。
                result["处理结果"] = "failed"
                result["说明"] = "备份位置未就绪，归档失败；补齐后可重试"
                counters["failed"] += 1
            else:
                row["status"] = "已归档"
                row["pending"] = False
                result["处理结果"] = "archived"
                result["说明"] = "已归档并进入长期留存"
                counters["archived"] += 1
            results.append(result)
        return {"基准日期": str(today or date.today()), "汇总": counters, "结果": results}

    def retry_one(self, entry_id: int, *, today: date | None = None) -> tuple[dict[str, Any] | None, str]:
        """单条重试：重新走同一判定标准，成功才落「已归档」，失败仍可继续重试。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"拍摄素材 {entry_id} 不存在"
        judged = evaluate_entry(entry, today=today)
        if judged["判定"] == "skip":
            return judged, "素材已归档，无需重试"
        if judged["判定"] != "archive":
            return judged, f"当前判定为「{judged['判定说明']}」，不在可归档范围，不能重试归档"
        if not judged["备份就绪"]:
            return judged, "备份位置仍未就绪，重试失败；请补齐备份位置后再次重试"
        entry["status"] = "已归档"
        entry["pending"] = False
        judged = evaluate_entry(entry, today=today)
        return judged, "重试成功，素材已归档"
