"""Provider routing tests never send network requests."""
import os
import unittest
from unittest.mock import patch


class ProviderTests(unittest.TestCase):
    def test_gemini_uses_its_own_key_endpoint_and_model(self):
        from production_modules.llm_config import llm_options, get_default_model
        with patch.dict(os.environ, {"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "test-secret", "DEFAULT_MODEL": "gemini-3.1-flash-lite"}, clear=True):
            options = llm_options(seed=42)
            self.assertEqual(options["api_key"], "test-secret")
            self.assertEqual(options["base_url"], "https://generativelanguage.googleapis.com/v1beta/openai/")
            self.assertEqual(get_default_model(), "gemini-3.1-flash-lite")
            self.assertNotIn("seed", options)

    def test_stale_openai_model_does_not_override_gemini_default(self):
        from production_modules.llm_config import get_default_model
        with patch.dict(os.environ, {"LLM_PROVIDER": "gemini", "DEFAULT_MODEL": "gpt-4o-mini"}, clear=True):
            self.assertEqual(get_default_model(), "gemini-3.1-flash-lite")

    def test_missing_gemini_key_fails_clearly(self):
        from production_modules.llm_config import llm_options
        with patch.dict(os.environ, {"LLM_PROVIDER": "gemini"}, clear=True):
            with self.assertRaisesRegex(ValueError, "GEMINI_API_KEY"):
                llm_options()

    def test_openai_retains_seed_without_gemini_endpoint(self):
        from production_modules.llm_config import llm_options
        with patch.dict(os.environ, {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "test-key", "OPENAI_MODEL": "gpt-4o-mini"}, clear=True):
            options = llm_options(seed=42)
            self.assertEqual(options["seed"], 42)
            self.assertNotIn("base_url", options)

    def test_openai_model_and_key_are_used(self):
        from production_modules.llm_config import get_default_model, llm_options
        with patch.dict(os.environ, {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "test-key", "OPENAI_MODEL": "gpt-6-luna", "DEFAULT_MODEL": "gemini-3.1-flash-lite"}, clear=True):
            self.assertEqual(get_default_model(), "gpt-6-luna")
            options = llm_options(seed=42)
            self.assertEqual(options["api_key"], "test-key")
            self.assertEqual(options["reasoning_effort"], "none")
            self.assertNotIn("seed", options)

    def test_openai_is_preferred_when_both_keys_exist(self):
        from production_modules.llm_config import get_provider
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "GEMINI_API_KEY": "test-gemini"}, clear=True):
            self.assertEqual(get_provider(), "openai")


if __name__ == "__main__":
    unittest.main()
