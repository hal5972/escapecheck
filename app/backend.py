"""計算と地理情報の呼び出し口（tools/ の hazard・sim）。

数値はすべて tools/ の関数が出す（S-01）。app/ は値を作らず、ここから呼び出す。
試験では、環境変数 ESCAPECHECK_STUB_TOOLS=1 で
テスト用の仮の値（app/stub_backend.py）に切り替える。仮の値は出典に「テスト用の仮の値」と記載される。
"""

from __future__ import annotations

from typing import Any, Protocol

from app import config
from tools import plan_model as pm


class Backend(Protocol):
    is_stub: bool

    def get_facts(self, plan: pm.Plan, target: str = "home") -> pm.Facts: ...

    def find_shelters(self, plan: pm.Plan, target: str = "home", limit: int = 3) -> list[pm.Shelter]: ...

    def run_scenario(self, plan: pm.Plan, scenario_id: str, premise_ids: list[str] | None = None) -> pm.ScenarioResult: ...

    def try_fix(self, plan: pm.Plan, scenario_id: str, fix_type: str, target_id: str | None = None) -> pm.ScenarioResult: ...


class ToolsBackend:
    """tools.hazard と tools.sim をそのまま呼ぶ（本番）。"""

    is_stub = False

    def get_facts(self, plan: pm.Plan, target: str = "home") -> pm.Facts:
        from tools import hazard

        return hazard.get_facts(plan, target)

    def find_shelters(self, plan: pm.Plan, target: str = "home", limit: int = 3) -> list[pm.Shelter]:
        from tools import hazard

        return hazard.find_shelters(plan, target, limit)

    def run_scenario(self, plan: pm.Plan, scenario_id: str, premise_ids: list[str] | None = None) -> pm.ScenarioResult:
        from tools import sim

        return sim.run_scenario(plan, scenario_id, premise_ids)

    def try_fix(self, plan: pm.Plan, scenario_id: str, fix_type: str, target_id: str | None = None) -> pm.ScenarioResult:
        from tools import sim

        return sim.try_fix(plan, scenario_id, fix_type, target_id)


def default_backend() -> Any:
    if config.use_stub_tools():
        from app.stub_backend import StubBackend

        return StubBackend()
    return ToolsBackend()
