"""拍摄素材归档保留规则冒烟测试：跑 `python -m unittest discover -s tests`。

只依赖标准库 unittest + FastAPI TestClient，覆盖需求里的关键口径：
- 类型不一致 / 临近到期进入待处理范围；
- 规则冲突按优先级取规则；
- 超过类型大小上限不允许归档；
- 处理失败可重试，已归档内容不重复处理；
- 素材列表与归档结果使用同一判定标准。
"""
from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient


class FootageRetentionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # 每个用例都用全新的 app；store 是模块级单例，setUp 里会重新灌入种子数据
        from app.routers import ROUTERS

        app = FastAPI()
        for module in ROUTERS:
            app.include_router(module.router)
        cls.client = TestClient(app)

    def setUp(self) -> None:
        from app.seed import SEED_ROWS
        from app.store import store

        store._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}

    def test_priority_type_and_status_exact_match(self) -> None:
        """正片素材 + 转码中 精确命中 240 天规则（档位1），而不是通用的 365 天。"""
        items = self.client.get("/api/footage/retention/impact").json()["items"]
        foot_0001 = next(item for item in items if item["素材编号"] == "FOOT-0001")
        self.assertEqual(foot_0001["保留天数"], 240)
        self.assertEqual(foot_0001["命中规则档"], 1)
        self.assertEqual(foot_0001["到期日"], "2026-09-17")

    def test_unknown_type_enters_pending_but_blocked(self) -> None:
        """未配置规则的素材类型（航拍素材）属于类型不一致：进待处理范围且不允许归档。"""
        items = self.client.get("/api/footage/retention/impact").json()["items"]
        aerial = next(item for item in items if item["素材编号"] == "FOOT-0005")
        self.assertTrue(aerial["in_scope"])
        self.assertEqual(aerial["scope"], "block")
        self.assertFalse(aerial["archivable"])
        self.assertIn("未配置保留规则", aerial["block_reason"])

    def test_expiring_soon_enters_pending_scope(self) -> None:
        """距到期 4 天的花絮素材进入待处理范围且可归档；距到期 44 天的不进。"""
        pending = self.client.get("/api/footage?scope=pending").json()["items"]
        ids = {item["id"] for item in pending}
        self.assertIn(6, ids)   # FOOT-0006 剩 4 天
        self.assertNotIn(7, ids)  # FOOT-0007 剩 173 天

    def test_over_size_limit_is_blocked_from_archive(self) -> None:
        """1300GB 花絮超过 1000GB 上限：进入待处理范围、硬拦截，单条动作也不能归档。"""
        result = self.client.post(
            "/api/footage/retention/archive", json={"values": {"ids": [4]}}
        ).json()
        self.assertEqual(result["summary"]["归档成功"], 0)
        blocked = result["blocked"][0]
        self.assertEqual(blocked["id"], 4)
        self.assertIn("归档上限", blocked["原因"])

        action = self.client.post(
            "/api/footage/4/actions", json={"values": {"action": "确认归档"}}
        ).json()
        self.assertFalse(action["ok"])
        self.assertIn("不允许归档", action["message"])

    def test_failed_archive_can_retry_and_archived_never_reprocessed(self) -> None:
        """FOOT-0001 首次处理失败，重试后成功；再次执行时已归档素材只跳过。"""
        first = self.client.post("/api/footage/retention/archive", json={"values": {}}).json()
        self.assertEqual(first["summary"]["处理失败可重试"], 1)
        self.assertEqual(first["failed"][0]["id"], 1)
        self.assertEqual(first["failed"][0]["尝试次数"], 1)

        retry = self.client.post("/api/footage/retention/retry", json={"values": {}}).json()
        self.assertEqual(retry["summary"]["归档成功"], 1)
        self.assertEqual(retry["archived"][0]["id"], 1)
        self.assertEqual(retry["archived"][0]["尝试次数"], 2)

        again = self.client.post("/api/footage/retention/archive", json={"values": {}}).json()
        self.assertEqual(again["summary"]["归档成功"], 0)
        skip_ids = {item["id"] for item in again["skipped"]}
        self.assertIn(1, skip_ids)  # 刚重试成功的不重复处理
        self.assertIn(3, skip_ids)  # 种子里本就已归档的也不重复处理

    def test_list_and_archive_share_one_standard(self) -> None:
        """列表 scope=pending 的口径与影响口径 summary['待处理'] 数量一致。"""
        pending_total = self.client.get("/api/footage?scope=pending&size=200").json()["total"]
        summary = self.client.get("/api/footage/retention/impact").json()["summary"]
        self.assertEqual(pending_total, summary["待处理"])

    def test_bad_as_of_rejected(self) -> None:
        response = self.client.get("/api/footage?as_of=not-a-date")
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
