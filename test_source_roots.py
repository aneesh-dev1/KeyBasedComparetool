"""source-roots.txt enables server-path input on any platform: one folder per line, comments ignored."""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server import source_roots_file


class SourceRootsFileTests(unittest.TestCase):
    def test_folders_comments_variables_and_missing_entries(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'input').mkdir()
            (root / 'share').mkdir()
            listing = root / 'source-roots.txt'
            listing.write_text('﻿# allowed folders\n\n' + str(root / 'input') + '\n  $DATA_SHARE  \n'
                               + str(root / 'missing') + '\n', encoding='utf-8')
            log = io.StringIO()
            with patch.dict(os.environ, {'DATA_SHARE': str(root / 'share')}), contextlib.redirect_stdout(log):
                roots = source_roots_file(listing)
            self.assertEqual(roots, [(root / 'input').resolve(), (root / 'share').resolve()])
            self.assertIn('missing', log.getvalue())
            self.assertEqual(source_roots_file(root / 'absent.txt'), [])


if __name__ == '__main__':
    unittest.main()
