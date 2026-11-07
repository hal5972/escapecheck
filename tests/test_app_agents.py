"""エージェントの設定（app/agents.py）と作品の Skill のテスト。Claude は呼び出さない。"""

import os
import re
import unittest
from unittest import mock

from app import agents, config
from app.audit import AuditLog
from app.session import PlanSession
from app.stub_backend import StubBackend, sample_plan

FAKE_KEY = "sk-ant-test-" + "k" * 30


def options_for(role):
    session = PlanSession(plan=sample_plan(), role=role, audit=AuditLog(), backend=StubBackend())
    with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": FAKE_KEY}, clear=False):
        return agents.build_options(session, role)


class AgentOptionsTest(unittest.TestCase):
    def test_models_follow_design(self):
        self.assertEqual(options_for("interview").model, "claude-sonnet-5-5")
        self.assertEqual(options_for("redteam").model, "claude-opus-5-5")
        self.assertEqual(options_for("verify").model, "claude-sonnet-5-5")

    def test_builtin_tools_are_removed(self):
        # X-02: 組み込みツール（コマンドの実行、ファイルの読み書き、Web の取得）を持たせない
        for role in ("interview", "redteam", "verify"):
            with self.subTest(role=role):
                options = options_for(role)
                self.assertEqual(options.tools, ["Skill"])
                for name in ("Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebFetch", "WebSearch", "Task"):
                    self.assertIn(name, options.disallowed_tools)
                self.assertEqual(options.permission_mode, "dontAsk")
                self.assertTrue(all(t.startswith("mcp__") for t in options.allowed_tools))
                self.assertTrue(options.strict_mcp_config)

    def test_skills_and_settings(self):
        options = options_for("redteam")
        self.assertEqual(options.cwd, str(config.AGENT_HOME))
        self.assertEqual(options.setting_sources, ["project"])
        self.assertEqual(options.skills, ["red-team-scenarios", "tosa-ben", "yasashii-kotoba"])
        self.assertIn("no-session-persistence", options.extra_args)
        self.assertEqual(options.env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"], "1")

    def test_structured_output_for_interview_and_verify(self):
        self.assertEqual(options_for("interview").output_format["type"], "json_schema")
        self.assertEqual(options_for("verify").output_format["type"], "json_schema")
        self.assertIsNone(options_for("redteam").output_format)

    def test_host_session_env_is_not_passed(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_BASE_URL": "http://127.0.0.1:1", "CLAUDE_EFFORT": "max"}):
            options = options_for("interview")
        self.assertEqual(options.env.get("ANTHROPIC_BASE_URL"), "")
        self.assertEqual(options.env.get("CLAUDE_EFFORT"), "")
        self.assertEqual(options.env["ANTHROPIC_API_KEY"], FAKE_KEY)

    def test_missing_api_key_raises_without_value(self):
        env = {k: v for k, v in os.environ.items() if k not in config.API_KEY_ENV_NAMES}
        session = PlanSession(plan=sample_plan(), role="interview", audit=AuditLog(), backend=StubBackend())
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(agents.AgentUnavailable) as ctx:
                agents.build_options(session, "interview")
        self.assertNotIn("sk-ant", str(ctx.exception))


class SkillFilesTest(unittest.TestCase):
    def test_every_configured_skill_exists_with_matching_name(self):
        names = {name for role in agents.SKILLS.values() for name in role}
        for name in names:
            with self.subTest(skill=name):
                path = config.AGENT_HOME / ".claude" / "skills" / name / "SKILL.md"
                self.assertTrue(path.is_file(), path)
                text = path.read_text(encoding="utf-8")
                self.assertRegex(text, rf"(?m)^name: {re.escape(name)}$")
                self.assertRegex(text, r"(?m)^description: .+")

    def test_red_team_skill_lists_all_scenarios_and_codes(self):
        from tools import plan_model as pm

        text = (config.AGENT_HOME / ".claude/skills/red-team-scenarios/SKILL.md").read_text(encoding="utf-8")
        for key in [*pm.SCENARIOS, *pm.PREMISE_CODES, *pm.HOLE_TYPES, *pm.FIX_TYPES]:
            self.assertIn(key, text)
        from app import placeholders

        for name in placeholders.PLACEHOLDERS:
            self.assertIn("{" + name + "}", text)


if __name__ == "__main__":
    unittest.main()
