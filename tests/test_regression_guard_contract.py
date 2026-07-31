from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RegressionGuardContractTests(unittest.TestCase):
    def test_persistent_regression_guard_documents_rules_and_ci_commands(self) -> None:
        requirements = ROOT / "issue" / "regression_guard_requirements.md"
        workflow = ROOT / ".github" / "workflows" / "regression.yml"
        agents = ROOT / "AGENTS.md"

        self.assertTrue(requirements.is_file(), "regression guard requirements must exist")
        self.assertTrue(workflow.is_file(), "regression CI workflow must exist")

        requirement_text = requirements.read_text(encoding="utf-8")
        agents_text = agents.read_text(encoding="utf-8")
        workflow_text = workflow.read_text(encoding="utf-8")

        for value in ("회귀 게이트", "TDD", "중지 조건", "복구", "검증되지 않음"):
            with self.subTest(requirement=value):
                self.assertIn(value, requirement_text)
        for value in ("D:/work/StockerSearcher_v3", "회귀 게이트 검증", "테스트 삭제 또는 완화", "npm test"):
            with self.subTest(agent=value):
                self.assertIn(value, agents_text)
        for value in ("pull_request:", "push:", "npm ci", "npm test", "-m unittest", "test_batch_process_wrapper.py", "test_prediction_filters.py", "yahoo_finance_api2", "PyYAML"):
            with self.subTest(workflow=value):
                self.assertIn(value, workflow_text)


if __name__ == "__main__":
    unittest.main()
