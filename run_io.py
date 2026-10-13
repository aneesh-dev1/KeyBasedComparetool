"""Bounded, lossless compression for private sort runs; legacy runs still read.

The sink owns physical writes/checksums and the caller owns stream lifetimes.
Records may cross blocks, so their existing byte format is unchanged.
"""
from functools import lru_cache
import struct
import zlib


MAGIC = b'KBC_RUN_V1\r\n\x1a\n'
BLOCK_SIZE = 4 * 1024 * 1024
BLOCK = struct.Struct('<BII')
RAW, ZLIB, LZ4, END = 0, 1, 2, 255


@lru_cache(maxsize=1)
def _arrow_lz4():
    try:
        import pyarrow as pa
    except ImportError:
        return None
    return pa if pa.Codec.is_available('lz4_raw') else None


def _read_up_to(stream, size):
    pieces = []
    while size:
        piece = stream.read(size)
        if not piece:
            break
        pieces.append(piece)
        size -= len(piece)
    return b''.join(pieces)


class RunWriter:
    """Write a framed run to a binary sink without closing that sink."""

    def __init__(self, sink, compression='auto'):
        if compression not in ('auto', 'raw', 'zlib', 'lz4'):
            raise ValueError('Unknown temporary run compression')
        self.sink = sink
        self.compression = compression
        self.buffer = bytearray()
        self.finished = False
        self.sink.write(MAGIC)

    def write(self, data):
        if self.finished:
            raise ValueError('Temporary run writer is already finished')
        view = memoryview(data)
        size = len(view)
        offset = 0
        while offset < size:
            take = min(BLOCK_SIZE - len(self.buffer), size - offset)
            self.buffer.extend(view[offset:offset + take])
            offset += take
            if len(self.buffer) == BLOCK_SIZE:
                self._flush_block()
        return size

    def _flush_block(self):
        if not self.buffer:
            return
        raw = bytes(self.buffer)
        codec, stored = RAW, raw
        if self.compression != 'raw':
            arrow = _arrow_lz4() if self.compression in ('auto', 'lz4') else None
            if self.compression == 'lz4' and arrow is None:
                raise ValueError('Reading/writing this temporary run requires PyArrow LZ4 support')
            candidate = (bytes(arrow.compress(raw, codec='lz4_raw')) if arrow is not None
                         else zlib.compress(raw, level=1))
            if len(candidate) < len(raw) * 0.98:
                codec, stored = (LZ4 if arrow is not None else ZLIB), candidate
        self.sink.write(BLOCK.pack(codec, len(raw), len(stored)))
        self.sink.write(stored)
        self.buffer.clear()

    def finish(self):
        if not self.finished:
            self._flush_block()
            self.sink.write(BLOCK.pack(END, 0, 0))
            self.finished = True


class _LegacyReader:
    def __init__(self, stream, prefix):
        self.stream, self.prefix = stream, prefix

    def read(self, size):
        if size < 0:
            raise ValueError('Temporary runs require bounded reads')
        head, self.prefix = self.prefix[:size], self.prefix[size:]
        return head + _read_up_to(self.stream, size - len(head))


class _BlockReader:
    def __init__(self, stream):
        self.stream = stream
        self.buffer = b''
        self.position = 0
        self.finished = False

    def _next_block(self):
        header = _read_up_to(self.stream, BLOCK.size)
        if len(header) != BLOCK.size:
            raise ValueError('Truncated temporary comparison run block')
        codec, raw_size, stored_size = BLOCK.unpack(header)
        if codec == END:
            if raw_size or stored_size or self.stream.read(1):
                raise ValueError('Invalid temporary comparison run end marker')
            self.finished = True
            self.buffer, self.position = b'', 0
            return
        if (codec not in (RAW, ZLIB, LZ4) or not 1 <= raw_size <= BLOCK_SIZE
                or not 1 <= stored_size <= BLOCK_SIZE
                or (codec == RAW and stored_size != raw_size)):
            raise ValueError('Invalid temporary comparison run block')
        stored = _read_up_to(self.stream, stored_size)
        if len(stored) != stored_size:
            raise ValueError('Truncated temporary comparison run block')
        try:
            if codec == RAW:
                decoded = stored
            elif codec == ZLIB:
                decoder = zlib.decompressobj()
                decoded = decoder.decompress(stored, raw_size + 1)
                if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                    raise ValueError('Invalid compressed data')
            else:
                arrow = _arrow_lz4()
                if arrow is None:
                    raise ValueError('Reading this temporary run requires PyArrow LZ4 support')
                decoded = bytes(arrow.decompress(stored, raw_size, codec='lz4_raw'))
        except Exception as error:
            raise ValueError('Cannot decode temporary comparison run block: ' + str(error)) from error
        if len(decoded) != raw_size:
            raise ValueError('Invalid decoded temporary comparison run size')
        self.buffer, self.position = decoded, 0

    def read(self, size):
        if size < 0:
            raise ValueError('Temporary runs require bounded reads')
        pieces = []
        while size:
            if self.position == len(self.buffer):
                if self.finished:
                    break
                self._next_block()
                if self.finished:
                    break
            take = min(size, len(self.buffer) - self.position)
            pieces.append(self.buffer[self.position:self.position + take])
            self.position += take
            size -= take
        return b''.join(pieces)


def wrap_reader(stream):
    """Return a bounded binary reader, accepting both new and legacy run files."""
    prefix = _read_up_to(stream, len(MAGIC))
    return _BlockReader(stream) if prefix == MAGIC else _LegacyReader(stream, prefix)
