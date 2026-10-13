import io
import random
import unittest
from unittest.mock import patch
import zlib

import run_io


class ShortReads(io.BytesIO):
    def read(self, size=-1):
        return super().read(min(size, 17) if size >= 0 else 17)


class RunIOTests(unittest.TestCase):
    def encode(self, body, compression='auto'):
        target = io.BytesIO()
        writer = run_io.RunWriter(target, compression)
        for start in range(0, len(body), 12347):
            writer.write(body[start:start + 12347])
        writer.finish()
        writer.finish()  # Finishing twice must not append a second end marker.
        with self.assertRaises(ValueError):
            writer.write(b'late data')
        return target.getvalue()

    def decode(self, content, short=False):
        stream = ShortReads(content) if short else io.BytesIO(content)
        reader = run_io.wrap_reader(stream)
        result = bytearray()
        while part := reader.read(1301):
            result.extend(part)
        self.assertEqual(reader.read(0), b'')
        self.assertEqual(reader.read(10), b'')
        return bytes(result)

    def test_multiblock_roundtrip_and_all_codecs(self):
        body = b'\x00["record"]\xff\x1f\r\n' * (run_io.BLOCK_SIZE // 8)
        modes = ['auto', 'zlib', 'raw']
        if run_io._arrow_lz4() is not None:
            modes.append('lz4')
        for mode in modes:
            with self.subTest(mode=mode):
                self.assertEqual(self.decode(self.encode(body, mode)), body)
        self.assertEqual(self.decode(self.encode(body[:4096]), short=True), body[:4096])

    def test_legacy_runs_and_empty_runs(self):
        for body in (b'', b'short', bytes(range(256)) * 100):
            self.assertEqual(self.decode(body, short=True), body)
            self.assertEqual(self.decode(self.encode(body)), body)

    def test_auto_without_arrow_uses_zlib_and_incompressible_is_raw(self):
        with patch('run_io._arrow_lz4', return_value=None):
            content = self.encode(b'repeated value' * 10000)
            self.assertEqual(content[len(run_io.MAGIC)], run_io.ZLIB)
            self.assertEqual(self.decode(content), b'repeated value' * 10000)
        body = random.Random(712).randbytes(65536)
        content = self.encode(body)
        self.assertEqual(content[len(run_io.MAGIC)], run_io.RAW)
        self.assertEqual(self.decode(content), body)

    def test_reads_are_lazy_and_block_bounded(self):
        content = self.encode(b'x' * (run_io.BLOCK_SIZE * 2 + 100), 'raw')
        stream = io.BytesIO(content)
        reader = run_io.wrap_reader(stream)
        self.assertEqual(stream.tell(), len(run_io.MAGIC))
        self.assertEqual(reader.read(1), b'x')
        self.assertEqual(stream.tell(), len(run_io.MAGIC) + run_io.BLOCK.size + run_io.BLOCK_SIZE)
        with self.assertRaises(ValueError):
            reader.read(-1)

    def test_truncation_including_whole_last_block_is_rejected(self):
        content = self.encode(b'a' * 30000)
        for remove in (1, run_io.BLOCK.size, run_io.BLOCK.size + 1):
            with self.subTest(remove=remove), self.assertRaises(ValueError):
                self.decode(content[:-remove])
        with self.assertRaises(ValueError):
            self.decode(run_io.MAGIC)
        with self.assertRaises(ValueError):
            self.decode(content + b'garbage')

    def test_invalid_lengths_codecs_and_decompression_bombs(self):
        cases = [
            (99, 1, 1, b'x'),
            (run_io.RAW, run_io.BLOCK_SIZE + 1, 1, b'x'),
            (run_io.ZLIB, 1, run_io.BLOCK_SIZE + 1, b'x'),
            (run_io.RAW, 3, 1, b'x'),
            (run_io.ZLIB, 3, 1, b'x'),
            (run_io.ZLIB, 3, len(zlib.compress(b'x' * 1000000)), zlib.compress(b'x' * 1000000)),
            (run_io.ZLIB, 3, len(zlib.compress(b'xxx')) + 1, zlib.compress(b'xxx') + b'x'),
        ]
        for codec, raw_size, stored_size, stored in cases:
            content = run_io.MAGIC + run_io.BLOCK.pack(codec, raw_size, stored_size) + stored
            with self.subTest(codec=codec, raw_size=raw_size), self.assertRaises(ValueError):
                self.decode(content)


if __name__ == '__main__':
    unittest.main()
