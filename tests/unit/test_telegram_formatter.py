import unittest
from dataclasses import dataclass, field
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from universal_agent.bot.normalization.formatting import format_telegram_response


@dataclass
class MockExecutionResult:
    response_text: str
    execution_time_seconds: float = 0.0
    tool_calls: int = 0
    code_execution_used: bool = False
    trace_id: str = None
    workspace_path: str = ""


class TestTelegramFormatter(unittest.TestCase):
    def test_string_fallback(self):
        result = format_telegram_response("Just a string")
        self.assertEqual(result, "Just a string")

    def test_full_result(self):
        res = MockExecutionResult(
            response_text="Hello world",
            execution_time_seconds=10.5,
            tool_calls=3,
            code_execution_used=True,
            trace_id="abc123trace",
        )
        formatted = format_telegram_response(res)

        # Check components
        self.assertIn("⏱ 10.5s", formatted)
        self.assertIn("🔧 3 tools", formatted)
        self.assertIn("🏭 Code", formatted)
        self.assertIn("Hello world", formatted)
        self.assertIn("abc123trace", formatted)
        # URL is escaped with backslashes for Telegram Markdown V2
        self.assertIn(
            "logfire", formatted
        )  # Domain is present (escaped as logfire\\.pydantic\\.dev)

    def test_minimal_result(self):
        res = MockExecutionResult(response_text="Simple response")
        formatted = format_telegram_response(res)

        self.assertNotIn("⏱", formatted)
        self.assertNotIn("🔧", formatted)
        self.assertIn("Simple response", formatted)


if __name__ == "__main__":
    unittest.main()
