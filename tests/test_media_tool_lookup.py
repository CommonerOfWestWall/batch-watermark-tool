import importlib
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


app = importlib.import_module("批量加水印_优化版")


@unittest.skipUnless(os.name == "nt", "Windows portable package")
class MediaToolLookupTest(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.enter = stack.enter_context
        temp = self.enter(tempfile.TemporaryDirectory())
        self.root = Path(temp)
        self.app_dir = self.root / "Portable App"
        self.app_dir.mkdir()
        self.enter(patch.object(sys, "frozen", True, create=True))
        self.enter(patch.object(sys, "_MEIPASS", str(self.root / "bundle"), create=True))
        self.enter(patch.object(sys, "executable", str(self.app_dir / "Watermark.exe")))
        self.enter(patch.dict(os.environ, {"PATH": str(self.root)}))

    def test_exe_sibling_wins_over_path(self):
        (self.root / "ffmpeg.exe").touch()
        ffmpeg = self.app_dir / "ffmpeg.exe"
        ffmpeg.touch()
        self.assertEqual(Path(app.find_media_tool("ffmpeg")), ffmpeg)

    def test_bin_subfolder_works_without_path_entry(self):
        ffprobe = self.app_dir / "bin" / "ffprobe.exe"
        ffprobe.parent.mkdir()
        ffprobe.touch()
        self.assertEqual(Path(app.find_media_tool("ffprobe")), ffprobe)

    def test_falls_back_to_path_and_can_report_missing(self):
        ffmpeg = self.root / "ffmpeg.exe"
        ffmpeg.touch()
        self.assertEqual(Path(app.find_media_tool("ffmpeg")), ffmpeg)
        self.assertIsNone(app.find_media_tool("ffprobe"))

    def test_source_uses_script_directory(self):
        self.enter(patch.object(sys, "frozen", False))
        self.enter(patch.object(app, "__file__", str(self.app_dir / "watermark.py")))
        ffmpeg = self.app_dir / "ffmpeg.exe"
        ffmpeg.touch()
        self.assertEqual(Path(app.find_media_tool("ffmpeg")), ffmpeg)


class EncoderSelectionTest(unittest.TestCase):
    def make_app(self, mode):
        instance = app.WatermarkApp.__new__(app.WatermarkApp)
        instance.accel_var = SimpleNamespace(get=lambda: mode)
        instance._get_available_encoders = Mock(return_value={"h264_nvenc", "h264_qsv"})
        instance._gpu_probe_cache = {}
        instance._log = Mock()
        return instance

    def test_cpu_skips_gpu_detection(self):
        instance = self.make_app("CPU")
        self.assertEqual(instance._select_video_encoder(), "libx264")
        instance._get_available_encoders.assert_not_called()

    def test_listed_but_unusable_gpu_falls_back_to_cpu(self):
        instance = self.make_app("Intel GPU")
        with patch.object(app, "find_media_tool", return_value="ffmpeg.exe"), patch.object(
            app.subprocess, "run", return_value=SimpleNamespace(returncode=1)
        ) as probe:
            self.assertEqual(instance._select_video_encoder(), "libx264")
            self.assertEqual(instance._select_video_encoder(), "libx264")
        probe.assert_called_once()
        args = probe.call_args.args[0]
        self.assertEqual(args[args.index("-c:v") + 1], "h264_qsv")
        instance._log.assert_called()

    def test_auto_skips_unusable_encoder(self):
        instance = self.make_app("自动选择")
        with patch.object(app, "find_media_tool", return_value="ffmpeg.exe"), patch.object(
            app.subprocess, "run", side_effect=[SimpleNamespace(returncode=1), SimpleNamespace(returncode=0)]
        ) as probe:
            self.assertEqual(instance._select_video_encoder(), "h264_qsv")
        self.assertEqual(probe.call_count, 2)
