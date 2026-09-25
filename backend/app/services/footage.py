"""素材管理业务规则：状态流转、字段校验、归档保留规则判定都收在这里。

保留规则（RETENTION_RULES）同时按「素材类型 + 归档状态」判定保留期限：
- 保留期限从拍摄日期起算，到期日 = 拍摄日期 + 保留天数；
- 类型在规则表里查不到（或拍摄日期不可解析）视为类型/口径不一致，进入待处理范围，
  但不允许自动归档，需要人工补规则或补登记；
- 剩余保留期 <= 临近到期窗口（含已经逾期）时进入待处理范围，允许归档。

规则冲突时的优先级（高 -> 低）：
  1. 素材类型 + 当前归档状态都精确命中；
  2. 只有素材类型命中（状态为空表示不挑状态的通用规则）；
  3. 只有归档状态命中的全局兜底；
同档冲突时取保留天数更长的一条（少删、慎归档）。

素材列表、执行前影响口径、归档结果都走 evaluate_entries 这一个判定函数，
保证三处口径完全一致；已归档内容在批量归档时直接跳过，不重复处理。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from app.store import store

MODULE = "footage"
REQUIRED_FIELDS = ["素材编号", "素材类型", "拍摄日期"]
STATUS_ORDER = ["待转码", "转码中", "已归档", "已丢失"]
ACTION_RULES = {"提交转码": "转码中", "确认归档": "已归档", "登记丢失": "已丢失"}
NEGATIVE_ACTIONS = ["登记丢失"]

# 已归档、已丢失的素材不再进入归档处理范围
ARCHIVABLE_STATUSES = ["待转码", "转码中"]
SKIP_STATUSES = ["已归档", "已丢失"]
ARCHIVED_STATUS = "已归档"

# 临近到期窗口：剩余保留天数小于等于该值（含 0 与已逾期）即进入待处理范围
EXPIRING_SOON_DAYS = 15

# 保留规则：素材类型 / 适用归档状态（空串=不挑状态）/ 保留天数 / 单文件大小上限 / 说明
# 大小统一换算成 GB；归档时文件大小超过上限的素材硬拦截，不允许归档。
RETENTION_RULES: list[dict[str, Any]] = [
    {"素材类型": "正片素材", "归档状态": "转码中", "保留天数": 240, "大小上限GB": 1024, "说明": "转码中的正片留足精修回溯期"},
    {"素材类型": "正片素材", "归档状态": "", "保留天数": 365, "大小上限GB": 1024, "说明": "正片素材默认保留一年"},
    {"素材类型": "花絮物料", "归档状态": "", "保留天数": 90, "大小上限GB": 1000, "说明": "花絮物料保留 90 天"},
    {"素材类型": "采访录音", "归档状态": "", "保留天数": 180, "大小上限GB": 512, "说明": "采访录音保留 180 天"},
]

# 优先级说明：规则冲突或客户质疑口径时直接把这段话带出去
RULE_PRIORITY_NOTE = (
    "规则优先级（高到低）：①素材类型+归档状态精确命中；②仅素材类型命中的通用规则；"
    "③仅归档状态命中的兜底规则；同档冲突取保留天数更长的一条（慎归档原则）。"
    "未配置规则的素材类型按类型不一致处理，进入待处理范围但不自动归档。"
)

_SIZE_UNITS = {"": 1.0, "GB": 1.0, "G": 1.0, "MB": 1 / 1024, "M": 1 / 1024, "TB": 1024.0, "T": 1024.0}


def parse_size_gb(raw: Any) -> float | None:
    """把「820GB」「1.5TB」「500」之类的文件大小解析成 GB；解析不了返回 None。"""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip().upper()
    if not text:
        return None
    unit = ""
    for candidate in sorted(_SIZE_UNITS, key=len, reverse=True):
        if candidate and text.endswith(candidate):
            unit = candidate
            text = text[: -len(candidate)].strip()
            break
    try:
        return round(float(text) * _SIZE_UNITS[unit], 3)
    except ValueError:
        return None


def parse_date(raw: Any) -> date | None:
    """解析拍摄日期；只接受 YYYY-MM-DD，避免把非法口径算进保留期。"""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def match_rule(material_type: str, status: str) -> tuple[dict[str, Any] | None, int]:
    """按优先级找保留规则，返回 (规则, 命中档位 1/2/3)；查不到返回 (None, 0)。"""
    best: dict[str, Any] | None = None
    best_level = 0
    for rule in RETENTION_RULES:
        type_hit = rule["素材类型"] == material_type
        rule_status = str(rule.get("归档状态") or "")
        status_hit = rule_status == "" or rule_status == status
        if type_hit and status_hit:
            level = 1 if rule_status else 2
        elif not type_hit and rule_status and rule_status == status:
            level = 3
        else:
            continue
        # 档位数字越小优先级越高；同档取保留天数更长的一条
        if best is None or level < best_level or (
            level == best_level and int(rule["保留天数"]) > int(best["保留天数"])
        ):
            best, best_level = rule, level
    return best, best_level


class FootageService:
    # ---------- 列表与登记 ----------

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        scope: str | None = None,
        page: int = 1,
        size: int = 20,
        as_of: date | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        today = as_of or date.today()
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("素材编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        annotated = [self._annotate(row, today) for row in rows]
        if scope:
            annotated = [row for row in annotated if row.get("in_scope") == (scope == "pending")]
        total = len(annotated)
        start = max(page - 1, 0) * size
        return annotated[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self._annotate(entry, date.today()) if entry is not None else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in ["文件大小", "存储介质", "转码格式", "备份位置", "素材状态"]:
            entry[field] = values.get(field)
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
        # 确认归档与批量归档共用同一套上限/口径校验，防止从单条动作绕过规则
        if action == "确认归档":
            info, reasons = self._archive_blockers(entry, date.today())
            if info is None or reasons:
                return None, f"拍摄素材不允许归档：{'；'.join(reasons)}"
            entry["status"] = ARCHIVED_STATUS
            entry["pending"] = False
            entry["归档日期"] = date.today().isoformat()
            return entry, "拍摄素材已确认归档"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"拍摄素材已{action}"

    # ---------- 归档保留规则：统一判定口径 ----------

    def retention_rules(self) -> list[dict[str, Any]]:
        """对外暴露规则表（副本），前端影响口径里直接展示。"""
        return [dict(rule) for rule in RETENTION_RULES]

    def evaluate_entries(self, today: date) -> list[dict[str, Any]]:
        """素材列表 / 影响口径 / 归档结果三处共用的判定入口。"""
        return [self._annotate(row, today) for row in store.rows(MODULE)]

    def _annotate(self, row: dict[str, Any], today: date) -> dict[str, Any]:
        """给素材打上保留期限、待处理原因、是否允许归档等判定标记（返回副本，不改原行）。"""
        view = {k: v for k, v in row.items() if not str(k).startswith("_")}
        material_type = str(row.get("素材类型") or "").strip()
        status = str(row.get("status") or "")
        shot_on = parse_date(row.get("拍摄日期"))
        rule, level = match_rule(material_type, status)

        view["保留天数"] = rule["保留天数"] if rule else None
        view["命中规则档"] = level
        due_date = shot_on + timedelta(days=int(rule["保留天数"])) if rule and shot_on else None
        view["到期日"] = due_date.isoformat() if due_date else None
        view["剩余天数"] = (due_date - today).days if due_date else None
        view["大小GB"] = parse_size_gb(row.get("文件大小"))

        scope_reasons: list[str] = []
        if status in SKIP_STATUSES:
            scope_kind = "skip"
        elif shot_on is None:
            scope_kind = "block"
            scope_reasons.append("拍摄日期缺失或格式不是 YYYY-MM-DD，无法计算保留期限（口径不一致）")
        elif rule is None:
            scope_kind = "block"
            scope_reasons.append(f"素材类型「{material_type}」未配置保留规则（类型不一致），请人工补规则")
        elif view["剩余天数"] is not None and view["剩余天数"] <= EXPIRING_SOON_DAYS:
            scope_kind = "ready"
            if view["剩余天数"] < 0:
                scope_reasons.append(f"已超过保留期限 {-int(view['剩余天数'])} 天")
            elif view["剩余天数"] == 0:
                scope_reasons.append("保留期限今日到期")
            else:
                scope_reasons.append(f"距保留到期仅剩 {view['剩余天数']} 天（临近到期窗口 {EXPIRING_SOON_DAYS} 天）")
        else:
            scope_kind = "idle"

        # 拦截口径：类型/日期不一致直接拦截；大小上限、备份位置缺失属于硬性归档条件，
        # 不管是否临近到期都要算出来——超过上限的素材即使未到期也先进待处理范围。
        blockers = list(scope_reasons) if scope_kind == "block" else []
        size_gb = view["大小GB"]
        over_limit = (
            rule is not None
            and size_gb is not None
            and size_gb > float(rule["大小上限GB"])
        )
        if over_limit:
            blockers.append(
                f"文件大小 {size_gb:g}GB 超过「{material_type}」归档上限 {rule['大小上限GB']:g}GB，不允许归档"
            )
            if scope_kind == "idle":
                scope_kind = "block"
                scope_reasons.append(
                    f"文件大小 {size_gb:g}GB 超过归档上限 {rule['大小上限GB']:g}GB（保留期未到也需先处理）"
                )
        if scope_kind in ("ready", "block") and not str(row.get("备份位置") or "").strip():
            blockers.append("备份位置为空，不允许归档（需先完成冷备登记）")

        view["scope"] = scope_kind  # ready=可归档 / block=待处理但拦截 / idle=未到期 / skip=不处理
        view["in_scope"] = scope_kind in ("ready", "block")
        view["archivable"] = scope_kind == "ready" and not blockers
        view["scope_reason"] = "；".join(scope_reasons)
        view["block_reason"] = "；".join(blockers)
        return view

    def _archive_blockers(self, row: dict[str, Any], today: date) -> tuple[dict[str, Any] | None, list[str]]:
        """对原始存储行做一次判定，返回标注视图与拦截原因。"""
        info = self._annotate(row, today)
        return info, [part for part in str(info.get("block_reason") or "").split("；") if part]

    def impact_preview(self, as_of: date | None = None) -> dict[str, Any]:
        """执行前影响口径：把会动到的素材、被拦截的素材、跳过的素材一次讲清楚。"""
        today = as_of or date.today()
        items = self.evaluate_entries(today)
        ready = [item for item in items if item["scope"] == "ready"]
        blocked = [item for item in items if item["scope"] == "block"]
        over_limit = [item for item in items if "归档上限" in str(item.get("block_reason") or "")]
        skipped = [item for item in items if item["scope"] == "skip"]
        idle = [item for item in items if item["scope"] == "idle"]
        can_archive = [item for item in ready if item["archivable"]]
        return {
            "as_of": today.isoformat(),
            "expiring_soon_days": EXPIRING_SOON_DAYS,
            "rule_priority": RULE_PRIORITY_NOTE,
            "rules": self.retention_rules(),
            "summary": {
                "待处理": len(ready) + len(blocked),
                "其中可归档": len(can_archive),
                "其中拦截不归档": len(blocked) + len(ready) - len(can_archive),
                "超过上限": len(over_limit),
                "已归档不重复处理": len([item for item in skipped if item["status"] == ARCHIVED_STATUS]),
                "已丢失不处理": len([item for item in skipped if item["status"] == "已丢失"]),
                "未到期待处理": len(idle),
            },
            "items": items,
        }

    def run_archive(
        self,
        *,
        only_failed: bool = False,
        entry_ids: list[int] | None = None,
        as_of: date | None = None,
    ) -> dict[str, Any]:
        """按统一口径执行批量归档。

        - 已归档/已丢失直接跳过，不重复处理；
        - 超过上限、类型不一致等硬拦截不归档；
        - 存储介质登记为离线会先失败一次（模拟处理失败），保留素材并写失败原因，可重试；
        - only_failed=True 时只重试上一次处理失败的素材。
        """
        today = as_of or date.today()
        id_filter = set(entry_ids) if entry_ids else None
        archived: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []

        for row in store.rows(MODULE):
            entry_id = int(row.get("id", 0))
            if id_filter is not None and entry_id not in id_filter:
                continue
            info = self._annotate(row, today)
            if only_failed and not row.get("archive_failed"):
                continue

            if info["scope"] == "skip":
                skipped.append({"id": entry_id, "素材编号": row.get("素材编号"), "原因": f"{row.get('status')}内容不重复处理"})
                continue
            if not info["in_scope"]:
                skipped.append({"id": entry_id, "素材编号": row.get("素材编号"), "原因": "保留期未到，不在本次处理范围"})
                continue
            if not info["archivable"]:
                blocked.append({
                    "id": entry_id,
                    "素材编号": row.get("素材编号"),
                    "原因": info["block_reason"] or info["scope_reason"],
                })
                continue

            # 模拟一次可重试的处理失败：介质离线 / 标记了首次失败的异常素材，第一次执行报错
            media = str(row.get("存储介质") or "")
            will_fail = ("离线" in media) or (row.get("_flaky_once") and not row.get("archive_attempts"))
            if will_fail:
                row["archive_attempts"] = int(row.get("archive_attempts") or 0) + 1
                row["archive_failed"] = True
                row["archive_error"] = "写入冷备失败：存储通道暂时不可用，请稍后重试"
                row.pop("_flaky_once", None)
                failed.append({
                    "id": entry_id,
                    "素材编号": row.get("素材编号"),
                    "尝试次数": row["archive_attempts"],
                    "原因": row["archive_error"],
                })
                continue

            row["status"] = ARCHIVED_STATUS
            row["pending"] = False
            row["归档日期"] = today.isoformat()
            row["归档保留天数"] = info["保留天数"]
            row["归档到期日"] = info["到期日"]
            row["archive_attempts"] = int(row.get("archive_attempts") or 0) + 1
            row.pop("archive_failed", None)
            row.pop("archive_error", None)
            row.pop("_flaky_once", None)
            archived.append({
                "id": entry_id,
                "素材编号": row.get("素材编号"),
                "归档日期": row["归档日期"],
                "保留到期日": info["到期日"],
                "尝试次数": row["archive_attempts"],
            })

        return {
            "ok": True,
            "as_of": today.isoformat(),
            "rule_priority": RULE_PRIORITY_NOTE,
            "summary": {
                "归档成功": len(archived),
                "拦截未归档": len(blocked),
                "处理失败可重试": len(failed),
                "跳过不重复处理": len(skipped),
            },
            "archived": archived,
            "blocked": blocked,
            "failed": failed,
            "skipped": skipped,
        }
