from __future__ import annotations

import unittest

from maxcli.cli import load_pymax


class AudioCompatibilityTest(unittest.TestCase):
    def test_missing_transcription_status(self) -> None:
        load_pymax()
        from pymax.types import AudioAttach

        attachment = AudioAttach.from_dict(
            {
                "_type": "AUDIO",
                "duration": 1000,
                "audioId": 42,
                "url": "https://example.test/audio",
                "wave": "",
                "token": "sample",
            }
        )

        self.assertEqual(attachment.audio_id, 42)
        self.assertEqual(attachment.transcription_status, "")


if __name__ == "__main__":
    unittest.main()
