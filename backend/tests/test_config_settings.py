import os
import tempfile
import unittest
from pathlib import Path

from app.config import Settings


class SettingsTests(unittest.TestCase):
    def test_settings_loads_values_from_dotenv_in_working_directory(self) -> None:
        original_cwd = os.getcwd()
        original_gemini_api_key = os.environ.pop("GEMINI_API_KEY", None)

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                os.chdir(temp_dir)
                Path(".env").write_text("GEMINI_API_KEY=dotenv-value\n", encoding="utf-8")

                settings = Settings()

            self.assertEqual(settings.gemini_api_key, "dotenv-value")
        finally:
            os.chdir(original_cwd)
            if original_gemini_api_key is not None:
                os.environ["GEMINI_API_KEY"] = original_gemini_api_key

