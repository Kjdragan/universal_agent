import os
import unittest
from unittest.mock import patch

from universal_agent.logfire_payloads import (
    PayloadLoggingConfig,
    load_payload_logging_config,
    serialize_payload_for_logfire,
)


class TestLogfirePayloads(unittest.TestCase):
    def test_load_payload_logging_config_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            cfg = load_payload_logging_config()
        self.assertFalse(cfg.full_payload_mode)
        self.assertTrue(cfg.redact_sensitive)
        self.assertTrue(cfg.redact_emails)
        self.assertEqual(cfg.max_chars, 50000)

    def test_load_payload_logging_config_bounds(self):
        with patch.dict(
            os.environ,
            {
                "UA_LOGFIRE_FULL_PAYLOAD_MODE": "true",
                "UA_LOGFIRE_FULL_PAYLOAD_REDACT": "false",
                "UA_LOGFIRE_FULL_PAYLOAD_REDACT_EMAILS": "0",
                "UA_LOGFIRE_FULL_PAYLOAD_MAX_CHARS": "999999999",
            },
            clear=True,
        ):
            cfg = load_payload_logging_config()
        self.assertTrue(cfg.full_payload_mode)
        self.assertFalse(cfg.redact_sensitive)
        self.assertFalse(cfg.redact_emails)
        self.assertEqual(cfg.max_chars, 2_000_000)

    def test_serialize_payload_redacts_sensitive_keys_and_emails(self):
        cfg = PayloadLoggingConfig(
            full_payload_mode=True,
            redact_sensitive=True,
            redact_emails=True,
            max_chars=5000,
        )
        payload = {
            "api_key": "sk-abcdef1234567890",
            "nested": {"authorization": "Bearer secret-token-value"},
            "recipient_email": "user@example.com",
            "safe": "hello",
        }
        output, truncated, redacted = serialize_payload_for_logfire(payload, cfg)
        self.assertFalse(truncated)
        self.assertTrue(redacted)
        self.assertIn('"api_key": "[REDACTED]"', output)
        self.assertIn('"authorization": "[REDACTED]"', output)
        self.assertIn('"recipient_email": "[REDACTED]"', output)
        self.assertIn('"safe": "hello"', output)

    def test_serialize_payload_can_keep_emails_when_configured(self):
        cfg = PayloadLoggingConfig(
            full_payload_mode=True,
            redact_sensitive=True,
            redact_emails=False,
            max_chars=5000,
        )
        payload = {"recipient": "user@example.com"}
        output, truncated, redacted = serialize_payload_for_logfire(payload, cfg)
        self.assertFalse(truncated)
        self.assertFalse(redacted)
        self.assertIn("user@example.com", output)

    def test_serialize_payload_truncates(self):
        cfg = PayloadLoggingConfig(
            full_payload_mode=True,
            redact_sensitive=False,
            redact_emails=False,
            max_chars=20,
        )
        output, truncated, redacted = serialize_payload_for_logfire(
            {"blob": "x" * 200},
            cfg,
        )
        self.assertTrue(truncated)
        self.assertFalse(redacted)
        self.assertTrue(output.endswith("...[TRUNCATED]"))


if __name__ == "__main__":
    unittest.main()
