"""1回の処理（1つのエージェントのセッション）の間だけ保持する状態。

計画は端末が保持し、リクエストごとに送られる。サーバーは処理の間だけ PlanSession に保持し、終われば捨てる（R-03）。
MCP ツールとフックは同じ PlanSession を参照するので、フックが確認した計画と、ツールが書き込む計画は同じものになる。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from app.audit import AUDIT, AuditLog
from tools import plan_model as pm

Role = Literal["interview", "redteam", "verify", "server"]


@dataclass
class PlanSession:
    plan: pm.Plan
    role: Role
    audit: AuditLog = AUDIT
    backend: Any = None  # app.backend.Backend（None のときは呼び出し側が既定の計算を入れる）
    events: list[str] = field(default_factory=list)  # 画面に出す進み具合
    on_event: Callable[[str], None] | None = None
    lock: threading.RLock = field(default_factory=threading.RLock)
    #: hazard.find_shelters で見つかった避難先（shelter_id → Shelter）。改善案の対象の確認に使う
    known_shelters: dict[str, pm.Shelter] = field(default_factory=dict)
    #: sim.run_scenario の直近の結果（scenario_id → ScenarioResult）
    last_results: dict[str, pm.ScenarioResult] = field(default_factory=dict)
    #: 処理中に起きたこと（"tools_not_ready" など）。orchestrator が利用者向けの文に変える
    flags: set[str] = field(default_factory=set)

    @property
    def plan_id(self) -> str:
        return self.plan.get("plan_id", "")

    def add_event(self, text: str) -> None:
        with self.lock:
            if self.events and self.events[-1] == text:
                return
            self.events.append(text)
        if self.on_event is not None:
            self.on_event(text)
