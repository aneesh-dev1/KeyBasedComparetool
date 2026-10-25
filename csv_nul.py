"""Python 3.9 and 3.10: let csv.reader read NUL characters, as Python 3.11 and later do.

Before 3.11 the csv module rejects any line containing a NUL character ("line contains NUL"), and mainframe
extracts often contain them (low-values). On those versions csv.reader is wrapped: NUL, and the escape
character itself, are replaced by private-use characters before parsing and restored in every field after,
so each value is read back exactly. Importing this module more than once is harmless; 3.11+ is unchanged.
"""
import csv
import re
import sys

_ESCAPE, _NUL = '', ''
_RESTORE = re.compile(_ESCAPE + '(.)', re.S)


def _unescape(field):
    return _RESTORE.sub(lambda match: '\x00' if match.group(1) == _NUL else match.group(1), field)


class _Reader:
    def __init__(self, factory, lines, args, kwargs):
        self._escaped = False
        self._reader = factory(self._escape(iter(lines)), *args, **kwargs)

    def _escape(self, lines):
        for line in lines:
            if '\x00' in line or _ESCAPE in line:
                self._escaped = True
                line = line.replace(_ESCAPE, _ESCAPE + _ESCAPE).replace('\x00', _ESCAPE + _NUL)
            yield line

    def __iter__(self):
        return self

    def __next__(self):
        row = next(self._reader)
        # The reader never reads past the current record, so the flag belongs to this row.
        if self._escaped:
            self._escaped = False
            row = [_unescape(field) if isinstance(field, str) and _ESCAPE in field else field for field in row]
        return row

    @property
    def line_num(self):
        return self._reader.line_num

    @property
    def dialect(self):
        return self._reader.dialect


if sys.version_info < (3, 11) and not getattr(csv.reader, 'reads_nul', False):
    _original = csv.reader

    def reader(lines, *args, **kwargs):
        return _Reader(_original, lines, args, kwargs)

    reader.reads_nul = True
    csv.reader = reader
