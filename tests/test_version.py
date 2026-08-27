from __future__ import annotations

import contextlib
import io
import unittest

from maxcli import __version__
from maxcli.cli import build_parser


class VersionTest(unittest.TestCase):
    def test_package_version(self) -> None:
        self.assertEqual(__version__, "0.3.0")

    def test_cli_version(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as exit_context:
                build_parser().parse_args(["--version"])

        self.assertEqual(exit_context.exception.code, 0)
        self.assertEqual(output.getvalue(), "maxcli 0.3.0\n")


if __name__ == "__main__":
    unittest.main()
