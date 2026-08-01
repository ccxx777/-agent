"""JWT 生产配置的负向测试。"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.infrastructure.security import create_access_token, validate_security_config


class SecurityConfigTests(unittest.TestCase):
    def test_missing_or_short_secret_is_rejected(self):
        with patch.dict(os.environ, {"AUTH_SECRET_KEY": ""}, clear=False):
            with self.assertRaises(RuntimeError):
                validate_security_config()
            with self.assertRaises(RuntimeError):
                create_access_token("user", "name")

    def test_random_secret_can_sign_token(self):
        with patch.dict(os.environ, {"AUTH_SECRET_KEY": "x" * 32}, clear=False):
            validate_security_config()
            token = create_access_token("user", "name")
            self.assertTrue(token)


if __name__ == "__main__":
    unittest.main()
