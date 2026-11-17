from __future__ import annotations

import importlib.util
from pathlib import Path
import struct
import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "ubpet" / "video.py"
spec = importlib.util.spec_from_file_location("ubpet_video_test", MODULE_PATH)
video = importlib.util.module_from_spec(spec)
spec.loader.exec_module(video)

JPEG_A = b"\xff\xd8\xff\xe0" + b"A" * 21 + b"\xff\xd9"
JPEG_B = b"\xff\xd8\xff\xe0" + b"B" * 30 + b"\xff\xd9"


def chunk(fourcc: bytes, body: bytes) -> bytes:
    pad = b"\x00" if len(body) % 2 else b""
    return fourcc + struct.pack("<I", len(body)) + body + pad


def build_avi(frames: list[bytes], micro_per_frame: int = 200_000) -> bytes:
    avih = chunk(b"avih", struct.pack("<I", micro_per_frame) + b"\x00" * 52)
    hdrl = chunk(b"LIST", b"hdrl" + avih)
    audio = chunk(b"01wb", b"\x00" * 7)  # broken audio stream like the real device
    movi = chunk(b"LIST", b"movi" + b"".join(chunk(b"00dc", f) for f in frames) + audio)
    idx1 = chunk(b"idx1", b"\x00" * 16)
    body = b"AVI " + hdrl + movi + idx1
    return b"RIFF" + struct.pack("<I", len(body)) + body


class VideoHelpersTest(unittest.TestCase):
    def test_frames_from_avi_chunks(self) -> None:
        data = build_avi([JPEG_A, JPEG_B, JPEG_A])
        self.assertEqual(video.mjpeg_frames(data), [JPEG_A, JPEG_B, JPEG_A])

    def test_frames_fallback_without_movi(self) -> None:
        self.assertEqual(video.mjpeg_frames(b"junk" + JPEG_A + b"x" + JPEG_B), [JPEG_A, JPEG_B])

    def test_frame_rate(self) -> None:
        self.assertEqual(video.avi_frame_rate(build_avi([JPEG_A])), 5.0)
        self.assertEqual(video.avi_frame_rate(build_avi([JPEG_A], 100_000)), 10.0)
        self.assertEqual(video.avi_frame_rate(b"no header"), video.DEFAULT_FPS)

    def test_allowed_urls(self) -> None:
        ok = "https://airpet-private-eu.oss-eu-central-1.aliyuncs.com/pro/dev/X/video/a.avi?Expires=1"
        self.assertTrue(video.is_allowed_video_url(ok))
        self.assertFalse(video.is_allowed_video_url(ok.replace("https", "http")))
        self.assertFalse(video.is_allowed_video_url("https://evil.example.com/a.avi"))
        self.assertFalse(video.is_allowed_video_url("https://aliyuncs.com.evil.example/a.avi"))
        self.assertFalse(video.is_allowed_video_url(""))
        self.assertFalse(video.is_allowed_video_url(None))

    def test_signed_url_freshness(self) -> None:
        url = "https://x.aliyuncs.com/a.avi?Expires=1000&Signature=s"
        self.assertEqual(video.signed_url_expires_at(url), 1000.0)
        self.assertTrue(video.signed_url_is_fresh(url, now=500))
        self.assertFalse(video.signed_url_is_fresh(url, now=800))
        self.assertTrue(video.signed_url_is_fresh("https://x.aliyuncs.com/a.avi"))

    def test_download_rejects_foreign_host(self) -> None:
        with self.assertRaises(ValueError):
            video.download_video("https://evil.example.com/a.avi")

    def test_summary_has_no_signed_url(self) -> None:
        record = {
            "recordId": 7, "catInfoId": 3, "catNickname": "Carmen", "eventTime": 1794933721000,
            "videoPath": "https://a.aliyuncs.com/v.avi?Expires=1&Signature=secret",
            "icon": "https://a.aliyuncs.com/i.jpg?Signature=secret", "weight": 5.1, "costTime": 41,
            "eventType": 3, "confirmed": 1,
        }
        summary = video.summarize_record(record)
        self.assertEqual(summary["cat"], "Carmen")
        self.assertTrue(summary["video"])
        self.assertNotIn("secret", repr(summary))
        self.assertFalse(video.summarize_record({**record, "videoPath": ""})["video"])


if __name__ == "__main__":
    unittest.main()
