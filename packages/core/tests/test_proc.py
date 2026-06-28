from __future__ import annotations

import unittest
from unittest.mock import patch

from ja_media_core import proc


class RunProcessTest(unittest.TestCase):
    def test_forces_posix_spawn_on_macos(self) -> None:
        with (
            patch.object(proc, "needs_posix_spawn_workaround", return_value=True),
            patch("ja_media_core.proc.subprocess.run") as run,
        ):
            proc.run(["ffmpeg", "-version"], check=True)

        run.assert_called_once()
        self.assertIs(run.call_args.kwargs["close_fds"], False)
        self.assertIs(run.call_args.kwargs["check"], True)

    def test_passes_through_unchanged_off_macos(self) -> None:
        with (
            patch.object(proc, "needs_posix_spawn_workaround", return_value=False),
            patch("ja_media_core.proc.subprocess.run") as run,
        ):
            proc.run(["ffmpeg", "-version"])

        run.assert_called_once()
        self.assertNotIn("close_fds", run.call_args.kwargs)

    def test_respects_explicit_close_fds(self) -> None:
        with (
            patch.object(proc, "needs_posix_spawn_workaround", return_value=True),
            patch("ja_media_core.proc.subprocess.run") as run,
        ):
            proc.run(["ffmpeg"], close_fds=True)

        self.assertIs(run.call_args.kwargs["close_fds"], True)

    def test_bows_out_when_pass_fds_used(self) -> None:
        # pass_fds mandates close_fds=True; we must not override it.
        with (
            patch.object(proc, "needs_posix_spawn_workaround", return_value=True),
            patch("ja_media_core.proc.subprocess.run") as run,
        ):
            proc.run(["ffmpeg"], pass_fds=(5,))

        self.assertNotIn("close_fds", run.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
