"""サーバー（app/server.py）のテスト。Claude は呼び出さず、エージェントの実行をテスト用の関数に置き換える。

合言葉・トークン（F-01）、上限（X-03）、CSP などのヘッダー、静的ファイルの範囲、API の流れ（話す → 点検 → 承認 → 検証 → 完成）、
エラーの応答に API キーが出ないこと（X-01）を確認する。サンプルデータはすべてデモ用の架空の人物。
"""

import asyncio
import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from app import agents, mcp_inprocess, orchestrator, server
from app.stub_backend import StubBackend, sample_plan

PASS = "test-passphrase"
UTTERANCE = "Aさんは杖ついちゅう。夜は2階で寝ゆう。"
FAKE_KEY = "sk-ant-api03-" + "z" * 40


def fake_runner(session, role, prompt):
    """エージェントの代わりに、MCP ツールの本体を直接呼ぶ（フックと同じ確認を通る）。"""
    tools = mcp_inprocess.make_tools(session)

    def call(server_name, name, args):
        return asyncio.run(tools[server_name][name].handler(args))

    plan = session.plan
    if role == "interview":
        sid = [k for k, s in plan["sources"].items() if s["kind"] == "utterance"][-1]
        call("plan", "set_fields", {"items": [
            {"field": "mobility", "value": "杖を使う", "source_id": sid, "quote": "杖ついちゅう"},
            {"field": "sleep_place", "value": "2階", "source_id": sid, "quote": "夜は2階で寝ゆう"}]})
        return agents.AgentRun(role=role, structured={"next_field": "walk_speed",
                                                       "next_question": "100メートル歩くのに、何分くらいかかりますか。"})
    if role == "redteam":
        sid = [k for k, s in plan["sources"].items() if s["kind"] == "utterance"][-1]
        call("plan", "add_premise", {"premise_code": "SLEEP_UPSTAIRS", "quote": "夜は2階で寝ゆう", "source_id": sid})
        call("hazard", "find_shelters", {"target": "home", "limit": 2})
        pid = plan["premises"][0]["premise_id"]
        call("sim", "run_scenario", {"scenario_id": "S8", "premise_ids": [pid]})
        call("plan", "add_finding", {"scenario_id": "S8", "hole_type": "SLEEP_UPSTAIRS", "source_ids": [sid],
                                     "explanation": "上の階で寝ているので、家を出るまで{leave_min}分かかります。"})
        plan["findings"][0]["result"]["margin_min"] = -2.0  # 改善案の効き目を確かめるため、間に合わない結果にする
        plan["findings"][0]["result"]["verdict"] = "short"
        call("plan", "propose_fix", {"finding_id": plan["findings"][0]["finding_id"], "fix_type": "CHANGE_SHELTER",
                                     "target_id": "shel_stub_hill", "explanation": "{shelter_name}にすると{saved_min}分早く着きます。"})
        return agents.AgentRun(role=role)
    if role == "verify":
        return agents.AgentRun(role=role, structured={"issues": [
            {"field": "sleep_place", "kind": "value_mismatch", "message": "就寝場所の値を確認してください", "severity": "要確認"},
            {"field": "mobility", "kind": "other", "message": "杖は12年前から", "severity": "要確認"}]})
    raise AssertionError(role)


class ServerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.restore = orchestrator.override(backend=StubBackend(), run_agent=fake_runner)
        cls.web = tempfile.TemporaryDirectory()
        web = Path(cls.web.name)
        (web / "index.html").write_text("<!doctype html><title>t</title>", encoding="utf-8")
        (web / "app.js").write_text("console.log(1)", encoding="utf-8")
        (web / ".secret").write_text("x", encoding="utf-8")
        cls.state = server.AppState(PASS, {"127.0.0.1", "localhost"}, web_dir=web, samples_dir=web / "samples")
        cls.httpd = server.make_server("127.0.0.1", 0, cls.state)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.restore()
        cls.web.cleanup()

    def request(self, method, path, body=None, token=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": f"127.0.0.1:{self.port}"}
        if body is not None or raw is not None:
            h["Content-Type"] = "application/json"
        if token:
            h["Authorization"] = f"Bearer {token}"
        h.update(headers or {})
        data = raw if raw is not None else (json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None)
        conn.request(method, path, body=data, headers=h)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        try:
            parsed = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            parsed = payload
        return resp.status, dict(resp.getheaders()), parsed

    def login(self):
        status, _, data = self.request("POST", "/api/login", {"passphrase": PASS})
        self.assertEqual(status, 200)
        return data["token"]


class AuthTest(ServerTestCase):
    def test_wrong_passphrase_is_rejected(self):
        status, _, data = self.request("POST", "/api/login", {"passphrase": "nope"})
        self.assertEqual(status, 401)
        self.assertIn("error", data)

    def test_api_requires_token(self):
        status, _, _ = self.request("POST", "/api/check", {"plan": sample_plan()})
        self.assertEqual(status, 401)
        status, _, _ = self.request("POST", "/api/check", {"plan": sample_plan()}, token="forged")
        self.assertEqual(status, 401)

    def test_logout_revokes_token(self):
        token = self.login()
        self.assertEqual(self.request("POST", "/api/logout", {}, token=token)[0], 200)
        self.assertEqual(self.request("GET", "/api/audit", token=token)[0], 401)

    def test_port_in_use_is_not_shared(self):
        # Windows の SO_REUSEADDR で、使用中のポートに重ねて待ち受けない（要求の横取りを防ぐ）
        with self.assertRaises(OSError):
            server.make_server("127.0.0.1", self.port, self.state)

    def test_repeated_failures_are_rate_limited(self):
        state = server.AppState(PASS, {"127.0.0.1"})
        for _ in range(server.LOGIN_FAILURE_LIMIT):
            state.record_failure()
        self.assertTrue(state.login_blocked())


class HeadersAndLimitsTest(ServerTestCase):
    def test_security_headers_on_static_and_api(self):
        for path in ("/", "/api/health"):
            with self.subTest(path=path):
                status, headers, _ = self.request("GET", path)
                self.assertEqual(status, 200)
                csp = headers["Content-Security-Policy"]
                self.assertIn("default-src 'self'", csp)
                self.assertIn("script-src 'self'", csp)
                self.assertIn("https://cyberjapandata.gsi.go.jp", csp)
                self.assertNotIn("unsafe-eval", csp)
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(headers["X-Frame-Options"], "DENY")

    def test_static_files_stay_inside_web_dir(self):
        self.assertEqual(self.request("GET", "/app.js")[0], 200)
        for path in ("/../app/config.py", "/%2e%2e/app/config.py", "/.secret", "/..\\app\\config.py"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)

    def test_rejects_unknown_host(self):
        status, _, _ = self.request("GET", "/", headers={"Host": "evil.example"})
        self.assertEqual(status, 421)

    def test_rejects_cross_site_post(self):
        status, _, _ = self.request("POST", "/api/login", {"passphrase": PASS}, headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)

    def test_request_size_and_utterance_limits(self):
        token = self.login()
        big = b'{"plan": "' + b"a" * (300 * 1024) + b'"}'
        self.assertEqual(self.request("POST", "/api/interview", raw=big, token=token)[0], 413)
        status, _, data = self.request("POST", "/api/interview",
                                       {"plan": sample_plan(), "utterance": "あ" * 2001}, token=token)
        self.assertEqual(status, 400)
        self.assertIn("2000", data["error"])

    def test_requires_json_content_type(self):
        token = self.login()
        status, _, _ = self.request("POST", "/api/check", raw=b"{}", token=token,
                                    headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 415)

    def test_get_facts_with_query_is_refused(self):
        token = self.login()
        self.assertEqual(self.request("GET", "/api/facts?target=home&lat=33.4", token=token)[0], 405)


class FlowTest(ServerTestCase):
    def test_interview_check_approve_verify_finalize(self):
        token = self.login()
        plan = sample_plan()

        status, _, data = self.request("POST", "/api/interview", {"plan": plan, "utterance": UTTERANCE}, token=token)
        self.assertEqual(status, 200, data)
        self.assertEqual(sorted(data["changed_fields"]), ["mobility", "sleep_place"])
        self.assertEqual(data["next_field"], "walk_speed")
        plan = data["plan"]

        status, _, data = self.request("POST", "/api/check", {"plan": plan}, token=token)
        self.assertEqual(status, 200)
        job_id = data["job_id"]
        for _ in range(50):
            status, _, job = self.request("GET", f"/api/jobs/{job_id}", token=token)
            if job["state"] != "running":
                break
            time.sleep(0.1)
        self.assertEqual(job["state"], "done", job)
        self.assertTrue(any("シナリオ" in e for e in job["events"]))
        plan = job["plan"]
        self.assertEqual(len(plan["findings"]), 1)
        self.assertNotIn("{", plan["findings"][0]["explanation"])
        fix_id = plan["fixes"][0]["fix_id"]

        status, _, data = self.request("POST", "/api/approve", {"plan": plan, "fix_id": fix_id}, token=token)
        self.assertEqual(status, 200, data)
        plan = data["plan"]
        self.assertEqual(plan["approvals"][0]["fix_id"], fix_id)
        self.assertEqual(plan["fields"]["shelter"]["value"]["shelter_id"], "shel_stub_hill")
        self.assertIn("S8", data["results"])

        status, _, data = self.request("POST", "/api/verify", {"plan": plan}, token=token)
        self.assertEqual(status, 200)
        plan = data["plan"]
        messages = [i["message"] for i in data["issues"]]
        self.assertIn("就寝場所の値を確認してください", messages)
        self.assertFalse(any("12" in m for m in messages))  # 検証エージェントの文にも数値を出さない

        status, _, data = self.request("POST", "/api/finalize", {"plan": plan}, token=token)
        self.assertEqual(status, 409)
        self.assertTrue(data["reasons"])  # AC-03: 未解決の項目が残る間は完成にできない

        # 記録の画面用: 承認による反映と、完成の拒否が監査ログに残る（MCP ツールの呼び出しはフックが記録する）
        status, _, entries = self.request("GET", f"/api/audit?plan_id={plan['plan_id']}", token=token)
        self.assertEqual(status, 200)
        self.assertTrue(any(e["tool"] == "plan.apply_fix" for e in entries["entries"]))
        self.assertTrue(any(e["tool"] == "plan.finalize" and e["decision"] == "deny" for e in entries["entries"]))

    def test_approve_unknown_fix_is_400(self):
        token = self.login()
        status, _, data = self.request("POST", "/api/approve", {"plan": sample_plan(), "fix_id": "fix_none"}, token=token)
        self.assertEqual(status, 400)
        self.assertIn("改善案", data["error"])

    def test_errors_do_not_leak_api_key(self):
        token = self.login()

        def failing_runner(session, role, prompt):
            raise RuntimeError(f"connection failed with key {FAKE_KEY}")

        restore = orchestrator.override(run_agent=failing_runner)
        try:
            with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": FAKE_KEY}), \
                    self.assertLogs("escapecheck.server", level="ERROR") as logs:
                status, _, data = self.request("POST", "/api/interview", {"plan": sample_plan(), "utterance": UTTERANCE},
                                               token=token)
        finally:
            restore()
        self.assertEqual(status, 500)
        self.assertNotIn("sk-ant", json.dumps(data))
        self.assertNotIn(FAKE_KEY, "\n".join(logs.output))  # サーバーのログの traceback からもキーを伏せる

    def test_missing_api_key_is_503_without_value(self):
        token = self.login()

        def unavailable(session, role, prompt):
            raise agents.AgentUnavailable("Claude の API キーが環境変数にありません。")

        restore = orchestrator.override(run_agent=unavailable)
        try:
            status, _, data = self.request("POST", "/api/interview", {"plan": sample_plan(), "utterance": UTTERANCE}, token=token)
        finally:
            restore()
        self.assertEqual(status, 503)
        self.assertIn("API キー", data["error"])


if __name__ == "__main__":
    unittest.main()
