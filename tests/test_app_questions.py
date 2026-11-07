"""追加の質問の文（app/orchestrator.py の QUESTION_TEMPLATES）のテスト。Claude は呼び出さない。

読み上げる質問は、確認済みの決まった文だけを使う。聞き取りエージェントが別の文を返しても、
読み上げる文が決まった文になることを確かめる。サンプルデータはすべてデモ用の架空の人物。
"""

import unittest

from app import agents, orchestrator
from app.stub_backend import StubBackend, sample_plan
from tools import plan_model as pm

AGENT_QUESTION = "歩行速度は何メートル毎秒ですか。"


def agent_with_own_question(session, role, prompt):
    """欄には何も書き込まず、決まった文と違う質問を返す聞き取りエージェントの代わり。"""
    return agents.AgentRun(role=role, structured={"next_field": "walk_speed", "next_question": AGENT_QUESTION})


class QuestionTemplateTest(unittest.TestCase):
    def setUp(self):
        self.restore = orchestrator.override(backend=StubBackend(), run_agent=agent_with_own_question)

    def tearDown(self):
        self.restore()

    def test_every_field_in_the_order_has_one_question(self):
        self.assertEqual(sorted(orchestrator.QUESTION_ORDER), sorted(orchestrator.QUESTION_TEMPLATES))
        self.assertEqual(len(orchestrator.QUESTION_ORDER), len(set(orchestrator.QUESTION_ORDER)))
        for key in orchestrator.QUESTION_ORDER:
            self.assertIn(key, pm.FIELDS)

    def test_questions_have_no_placeholders(self):
        for key, text in orchestrator.QUESTION_TEMPLATES.items():
            with self.subTest(key=key):
                self.assertTrue(text.strip())
                self.assertNotIn("{", text)
                self.assertNotIn("}", text)

    def test_agent_question_is_not_used(self):
        result = orchestrator.interview(sample_plan(), "Aさんのことを話します。")
        self.assertEqual(result["next_field"], orchestrator.QUESTION_ORDER[0])
        self.assertEqual(result["next_question"], orchestrator.QUESTION_TEMPLATES[result["next_field"]])
        self.assertNotEqual(result["next_question"], AGENT_QUESTION)


if __name__ == "__main__":
    unittest.main()
