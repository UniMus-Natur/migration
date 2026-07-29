"""Unit tests for Unimus media download validation and asset-server upload retries."""

from __future__ import annotations

import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# asset_server_upload imports django at module load time.
if "django" not in sys.modules:
    django = types.ModuleType("django")
    django_conf = types.ModuleType("django.conf")
    django_conf.settings = MagicMock(
        WEB_ATTACHMENT_KEY="test-key",
        WEB_ATTACHMENT_URL="https://example.test/web_asset_store.xml",
    )
    django.conf = django_conf
    sys.modules["django"] = django
    sys.modules["django.conf"] = django_conf

from flows.lib.asset_server_upload import (  # noqa: E402
    AssetServerError,
    _is_retryable_download_status,
    _is_retryable_upload_status,
    build_unimus_original_url,
    download_unimus_original,
    reset_asset_server_cache,
    upload_original_to_asset_server,
    validate_media_bytes,
)


def _mock_response(*, status_code: int, text: str = "Ok.") -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    response.headers = {}
    return response


class AssetServerUploadTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_asset_server_cache()
        self._server_urls_patcher = patch(
            "flows.lib.asset_server_upload._ensure_server_urls",
            return_value={"write": "http://asset-server/fileupload"},
        )
        self._server_urls_patcher.start()

    def tearDown(self) -> None:
        self._server_urls_patcher.stop()
        reset_asset_server_cache()

    def test_validate_rejects_html(self) -> None:
        with self.assertRaises(AssetServerError) as ctx:
            validate_media_bytes(b"<!DOCTYPE HTML><html><body>x</body></html>", source="test")
        self.assertIn("HTML/XML", str(ctx.exception))

    def test_validate_detects_tiff_and_jpeg(self) -> None:
        self.assertEqual(validate_media_bytes(b"II*\x00", source="t"), "tiff")
        self.assertEqual(validate_media_bytes(b"\xff\xd8\xff\xe0", source="j"), "jpeg")

    def test_retryable_status_includes_400_and_503(self) -> None:
        self.assertTrue(_is_retryable_upload_status(400))
        self.assertTrue(_is_retryable_upload_status(503))
        self.assertFalse(_is_retryable_upload_status(403))

    def test_retryable_download_status_includes_503_not_404(self) -> None:
        self.assertTrue(_is_retryable_download_status(503))
        self.assertFalse(_is_retryable_download_status(404))

    def test_build_url_uses_public_host_without_secret(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            # Ensure unset even if shell has it.
            env = {k: v for k, v in os.environ.items() if k != "UNIMUS_IMAGE_API_BASE"}
            with patch.dict("os.environ", env, clear=True):
                url = build_unimus_original_url(media_group_id=42)
        self.assertIn("www.unimus.no", url)
        self.assertIn("id=42", url)
        self.assertIn("type=orig", url)

    def test_build_url_uses_private_base_from_env(self) -> None:
        with patch.dict(
            "os.environ",
            {"UNIMUS_IMAGE_API_BASE": "https://api.unimus.no/img-testdev"},
            clear=False,
        ):
            url = build_unimus_original_url(media_group_id=13081392)
        self.assertEqual(
            url,
            "https://api.unimus.no/img-testdev/web_hent_bilde.php?id=13081392&type=orig",
        )
        self.assertNotIn("www.unimus.no", url)

    def test_build_url_prefers_tiff_filename_on_private_api(self) -> None:
        with patch.dict(
            "os.environ",
            {"UNIMUS_IMAGE_API_BASE": "https://api.unimus.no/img-testdev"},
            clear=False,
        ):
            url = build_unimus_original_url(
                media_group_id=13081392,
                filename="O-V-2011266-01.tif",
            )
        self.assertIn("filename=O-V-2011266-01.tif", url)
        self.assertIn("type=orig", url)
        self.assertNotIn("id=", url)

    def test_build_url_ignores_derivative_jpg_filename(self) -> None:
        with patch.dict(
            "os.environ",
            {"UNIMUS_IMAGE_API_BASE": "https://api.unimus.no/img-testdev"},
            clear=False,
        ):
            url = build_unimus_original_url(
                media_group_id=13081392,
                filename="MUSIT_BOTANIKK_FELLES_FOTO_7357555.jpg",
            )
        self.assertIn("id=13081392", url)
        self.assertNotIn("filename=", url)

    @patch("flows.lib.asset_server_upload.time.sleep")
    @patch("flows.lib.asset_server_upload.requests.get")
    def test_download_retries_premature_response(
        self,
        mock_get: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        import requests
        from urllib3.exceptions import ProtocolError

        ok = _mock_response(status_code=200, text="")
        ok.content = b"II*\x00" + b"x" * 100

        mock_get.side_effect = [
            requests.exceptions.ChunkedEncodingError(ProtocolError("Response ended prematurely")),
            ok,
        ]

        data, _ct = download_unimus_original(
            13085828,
            max_attempts=4,
            retry_backoff_s=(0, 0, 0),
        )

        self.assertTrue(data.startswith(b"II"))
        self.assertEqual(mock_get.call_count, 2)
        mock_sleep.assert_called_once_with(0)

    @patch("flows.lib.asset_server_upload.time.sleep")
    @patch("flows.lib.asset_server_upload.requests.get")
    def test_download_does_not_retry_404(
        self,
        mock_get: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        mock_get.return_value = _mock_response(status_code=404, text="not found")

        with self.assertRaises(AssetServerError) as ctx:
            download_unimus_original(
                999,
                max_attempts=4,
                retry_backoff_s=(0, 0, 0),
            )

        self.assertEqual(mock_get.call_count, 1)
        mock_sleep.assert_not_called()
        self.assertIn("HTTP 404", str(ctx.exception))

    @patch("flows.lib.asset_server_upload.time.sleep")
    @patch("flows.lib.asset_server_upload.requests.post")
    def test_upload_retries_transient_400_then_succeeds(
        self,
        mock_post: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        mock_post.side_effect = [
            _mock_response(status_code=400, text="<html>400 Bad Request</html>"),
            _mock_response(status_code=200, text="Ok."),
        ]

        location = upload_original_to_asset_server(
            file_bytes=b"II*\x00" + b"x" * 100,
            orig_filename="O-V-2000709-01.tif",
            mime_type="image/tiff",
            collection_name="NHM-karplanter",
            max_attempts=4,
            retry_backoff_s=(0, 0, 0),
        )

        self.assertTrue(location.endswith(".tif"))
        self.assertEqual(mock_post.call_count, 2)
        mock_sleep.assert_called_once_with(0)

    @patch("flows.lib.asset_server_upload.time.sleep")
    @patch("flows.lib.asset_server_upload.requests.post")
    def test_upload_does_not_retry_auth_failure(
        self,
        mock_post: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        mock_post.return_value = _mock_response(
            status_code=403,
            text="Auth token timestamp out of range",
        )

        with self.assertRaises(AssetServerError) as ctx:
            upload_original_to_asset_server(
                file_bytes=b"II*\x00",
                orig_filename="test.tif",
                mime_type="image/tiff",
                collection_name="NHM-karplanter",
                max_attempts=4,
                retry_backoff_s=(0, 0, 0),
            )

        self.assertEqual(mock_post.call_count, 1)
        mock_sleep.assert_not_called()
        self.assertIn("403", str(ctx.exception))

    @patch("flows.lib.asset_server_upload.time.sleep")
    @patch("flows.lib.asset_server_upload.requests.post")
    def test_upload_retries_network_error(
        self,
        mock_post: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        import requests

        mock_post.side_effect = [
            requests.ConnectionError("connection reset"),
            _mock_response(status_code=200, text="Ok."),
        ]

        upload_original_to_asset_server(
            file_bytes=b"II*\x00",
            orig_filename="test.tif",
            mime_type="image/tiff",
            collection_name="NHM-karplanter",
            max_attempts=4,
            retry_backoff_s=(0, 0, 0),
        )

        self.assertEqual(mock_post.call_count, 2)
        mock_sleep.assert_called_once_with(0)


if __name__ == "__main__":
    unittest.main()
