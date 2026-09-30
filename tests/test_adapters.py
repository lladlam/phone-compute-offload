import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from pco.adapters.blender import BlenderAdapter
from pco.adapters.compiler import CompilerAdapter
from pco.adapters.ffmpeg import FFmpegAdapter


class AdapterTest(unittest.TestCase):
    def test_frame_split_matches_lambda_remainder_rule(self):
        # 10 frames, 4 workers → last span is longer by the remainder, no empty span.
        spans = BlenderAdapter.plan(1, 10, 4)
        self.assertEqual(spans, [(1, 3), (4, 6), (7, 8), (9, 10)])
        self.assertEqual(BlenderAdapter.plan(0, 2, 8), [(0, 0), (1, 1), (2, 2)])

    def test_compiler_refuses_host_default_target(self):
        with self.assertRaises(SystemExit):
            CompilerAdapter().build(["clang", "-c", "a.c", "-o", "a.o"], inputs=[], outputs=[], timeout_s=10)

    def test_compiler_accepts_explicit_target(self):
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "a.c"
            source.write_text("int a;\n")
            task = CompilerAdapter().build(
                ["clang", "--target=aarch64-linux-android", "-c", str(source), "-o", "a.o"],
                inputs=[],
                outputs=[],
                timeout_s=10,
            )
            self.assertEqual(task.required_executor, "clang")
            self.assertEqual(task.argv[0], "clang")
            self.assertIn("a.c", task.argv)
            self.assertEqual(task.output_globs, ["a.o"])

    def test_compiler_refuses_gradle(self):
        with self.assertRaises(SystemExit):
            CompilerAdapter().build(["gradle", "assemble"], inputs=[], outputs=[], timeout_s=10)

    def test_ffmpeg_rewrites_input_to_basename(self):
        with TemporaryDirectory() as tmp:
            media = Path(tmp) / "clip.mp4"
            media.write_bytes(b"\x00\x00\x00\x18ftyp")
            task = FFmpegAdapter().build(
                ["ffmpeg", "-y", "-i", str(media), "-c:v", "libx264", str(Path(tmp) / "out.mp4")],
                inputs=[],
                outputs=[],
                timeout_s=30,
            )
            self.assertEqual(task.required_executor, "ffmpeg")
            self.assertIn("clip.mp4", task.argv)
            self.assertEqual(task.output_globs, ["out.mp4"])
            self.assertEqual(task.cwd_files[0]["name"], "clip.mp4")

    def test_blender_requires_blender(self):
        task = BlenderAdapter().build(
            ["blender", "-b", "scene.blend", "-s", "1", "-e", "4", "-a"],
            inputs=[],
            outputs=[],
            timeout_s=60,
        )
        self.assertEqual(task.required_executor, "blender")


if __name__ == "__main__":
    unittest.main()
