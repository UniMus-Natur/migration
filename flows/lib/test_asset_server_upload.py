"""Unit tests for Unimus media download validation."""

from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import MagicMock

# asset_server_upload imports django at module load time.
if "django" not in sys.modules:
    django = types.ModuleType("django")
    django_conf = types.ModuleType("django.conf")
    django_conf.settings = MagicMock()
    django.conf = django_conf
    sys.modules["django"] = django
    sys.modules["django.conf"] = django_conf

from flows.lib.asset_server_upload import (  # noqa: E402
    AssetServerError,
    validate_media_bytes,
)


class AssetServerUploadTests(unittest.TestCase):
    def test_validate_rejects_html(self) -> None:
        with self.assertRaises(AssetServerError) as ctx:
            validate_media_bytes(b"<!DOCTYPE HTML><html><body>x</body></html>", source="test")
        self.assertIn("HTML/XML", str(ctx.exception))

    def test_validate_detects_tiff_and_jpeg(self) -> None:
        self.assertEqual(validate_media_bytes(b"II*\x00", source="t"), "tiff")
        self.assertEqual(validate_media_bytes(b"\xff\xd8\xff\xe0", source="j"), "jpeg")


if __name__ == "__main__":
    unittest.main()
