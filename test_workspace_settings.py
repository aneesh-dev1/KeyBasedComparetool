import unittest

from workspace_settings import DEFAULT_MEMORY_MB, MEMORY_BUDGETS, PERFORMANCE, validate_settings


class WorkspaceSettingsTests(unittest.TestCase):
    def test_legacy_settings_payload_is_readable_but_not_runtime_policy(self):
        settings = validate_settings({}, DEFAULT_MEMORY_MB)
        self.assertEqual(settings['memory_mb'], 15360)
        self.assertEqual(settings['comparison_engine'], 'auto')
        self.assertIn('comparison_engine', PERFORMANCE)

    def test_explicit_server_cap_and_saved_budget_are_preserved(self):
        for limit in MEMORY_BUDGETS:
            with self.subTest(limit=limit):
                self.assertEqual(validate_settings({}, limit)['memory_mb'], limit)
        self.assertEqual(validate_settings({'memory_mb': 4096}, 15360)['memory_mb'], 4096)
        self.assertEqual(validate_settings({'memory_mb': 15360, 'comparison_engine': 'sort'}, 15360)['comparison_engine'], 'sort')
        with self.assertRaises(ValueError):
            validate_settings({'memory_mb': 15360}, 8192)

    def test_invalid_memory_and_engine_rejected(self):
        for memory in (True, '15360', 15000, 16384):
            with self.subTest(memory=memory), self.assertRaises(ValueError):
                validate_settings({'memory_mb': memory}, 15360)
        for engine in ('hash', '', None, True):
            with self.subTest(engine=engine), self.assertRaises(ValueError):
                validate_settings({'comparison_engine': engine}, 15360)


if __name__ == '__main__':
    unittest.main()
