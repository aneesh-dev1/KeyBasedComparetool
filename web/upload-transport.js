/* Resumable, bounded browser uploads. Offsets always refer to original file bytes. */
(function (root, factory) {
  const UploadTransfer = factory();
  if (typeof module === 'object' && module.exports) module.exports = UploadTransfer;
  if (root) root.UploadTransfer = UploadTransfer;
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';
  const MiB = 1024 * 1024, MIN_CHUNK = 256 * 1024, MAX_CHUNK = 8 * MiB;
  const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
  function failure(message, properties) { return Object.assign(new Error(message), properties); }
  function aborted() { return failure('Upload paused. Its saved progress is retained.', {name: 'AbortError'}); }
  function fatal(error) { return error.fatal || (error.status >= 400 && error.status < 500 && error.status !== 409); }

  return class UploadTransfer {
    constructor(options) {
      this.options = options;
      const env = options.env || {};
      this.env = {
        xhr: env.xhr || (() => new XMLHttpRequest()),
        now: env.now || (() => performance.now()),
        setTimeout: env.setTimeout || ((fn, ms) => globalThis.setTimeout(fn, ms)),
        clearTimeout: env.clearTimeout || (id => globalThis.clearTimeout(id)),
        online: env.online || (() => typeof navigator === 'undefined' || navigator.onLine !== false),
        events: env.events || (typeof window !== 'undefined' ? window : null),
        random: env.random || Math.random,
        compress: env.compress || (typeof CompressionStream !== 'undefined' && typeof Response !== 'undefined'
          ? async blob => new Response(blob.stream().pipeThrough(new CompressionStream('gzip'))).blob() : null),
      };
      this.signal = options.signal;
      this.idleMs = options.idleMs || 60000;
      this.chunkSize = MiB;
      this.rate = 0;
      this.wireBytes = 0;
      this.sessionSent = 0;
      this.sessionOriginalBytes = 0;
      this.encodedBytes = 0;
      this.compressionEnabled = Boolean(options.compress && this.env.compress);
      this.active = false;
    }
    check() { if (this.signal?.aborted) throw aborted(); }
    notify(state, extra = {}) {
      const sent = extra.sent ?? this.offset;
      const rate = state === 'offline' ? 0 : this.rate;
      const snapshot = {
        state, uploaded: this.offset, sent, total: this.total,
        bytesPerSecond: rate, etaSeconds: rate > 0 ? Math.max(0, this.total - sent) / rate : null,
        chunkSize: this.chunkSize, attempt: this.attempt || 0, wireBytes: this.wireBytes,
        // Payload savings use acknowledged chunks only; wireBytes also includes observed retry traffic.
        sessionOriginalBytes: this.sessionOriginalBytes, encodedBytes: this.encodedBytes,
        compressionRatio: this.sessionOriginalBytes ? this.encodedBytes / this.sessionOriginalBytes : null,
        compressed: this.compressionEnabled, ...extra,
      };
      this.snapshot = snapshot;
      this.options.onProgress?.(snapshot);
      return snapshot;
    }
    sample(bytes, elapsedMs) {
      if (bytes <= 0 || elapsedMs <= 0) return;
      const rate = bytes * 1000 / elapsedMs;
      const alpha = clamp(1 - Math.exp(-elapsedMs / 3000), 0.15, 0.65);
      this.rate = this.rate ? alpha * rate + (1 - alpha) * this.rate : rate;
    }
    // Cancel the wait immediately even if the supplied status/compression operation cannot be cancelled.
    waitFor(promise, timeoutMs = 0) {
      this.check();
      return new Promise((resolve, reject) => {
        let timer, settled = false;
        const finish = (fn, value) => {
          if (settled) return;
          settled = true;
          if (timer !== undefined) this.env.clearTimeout(timer);
          this.signal?.removeEventListener('abort', cancel);
          fn(value);
        };
        const cancel = () => finish(reject, aborted());
        this.signal?.addEventListener('abort', cancel, {once: true});
        if (timeoutMs) timer = this.env.setTimeout(() => finish(reject, failure('Upload status check timed out.')), timeoutMs);
        Promise.resolve(promise).then(value => finish(resolve, value), error => finish(reject, error));
        if (this.signal?.aborted) cancel();
      });
    }
    sleep(ms) {
      this.check();
      return new Promise((resolve, reject) => {
        const cancel = () => { this.env.clearTimeout(timer); this.signal?.removeEventListener('abort', cancel); reject(aborted()); };
        const timer = this.env.setTimeout(() => { this.signal?.removeEventListener('abort', cancel); resolve(); }, ms);
        this.signal?.addEventListener('abort', cancel, {once: true});
        if (this.signal?.aborted) cancel();
      });
    }
    async online() {
      this.check();
      if (this.env.online()) return;
      this.notify('offline');
      await new Promise((resolve, reject) => {
        const cleanup = () => {
          this.env.events?.removeEventListener('online', connected);
          this.signal?.removeEventListener('abort', cancel);
        };
        const connected = () => { if (this.env.online()) { cleanup(); resolve(); } };
        const cancel = () => { cleanup(); reject(aborted()); };
        this.env.events?.addEventListener('online', connected);
        this.signal?.addEventListener('abort', cancel, {once: true});
        if (this.signal?.aborted) cancel(); else connected();
      });
    }
    async prepare(blob) {
      if (!this.compressionEnabled) return {blob, compressed: false};
      this.notify('uploading', {phase: 'compressing'});
      let encoded;
      try { encoded = await this.waitFor(this.env.compress(blob)); }
      catch (error) {
        if (error.name === 'AbortError') throw error;
        this.compressionEnabled = false;
        return {blob, compressed: false};
      }
      this.check();
      // Do not spend CPU repeatedly compressing data that does not benefit.
      if (encoded.size > blob.size * 0.85) {
        this.compressionEnabled = false;
        return {blob, compressed: false};
      }
      return {blob: encoded, compressed: true};
    }
    send(body, originalSize, started) {
      this.check();
      return new Promise((resolve, reject) => {
        const xhr = this.env.xhr();
        let timer, settled = false, loaded = 0, sampled = 0, sampledAt = started;
        const cleanup = () => {
          if (timer !== undefined) this.env.clearTimeout(timer);
          this.signal?.removeEventListener('abort', cancel);
          this.env.events?.removeEventListener('offline', offline);
        };
        const finish = (fn, value, abortRequest = false) => {
          if (settled) return;
          settled = true; cleanup();
          if (abortRequest) xhr.abort();
          fn(value);
        };
        const cancel = () => finish(reject, aborted(), true);
        const offline = () => { if (!this.env.online()) finish(reject, failure('Connection is offline.'), true); };
        const touch = () => {
          if (settled) return;
          if (timer !== undefined) this.env.clearTimeout(timer);
          timer = this.env.setTimeout(() => finish(reject, failure('Upload connection stopped responding.', {code: 'idle'}), true), this.idleMs);
        };
        const progress = count => {
          if (settled) return;
          const next = clamp(Number(count) || 0, loaded, body.blob.size);
          if (next > loaded) {
            this.wireBytes += next - loaded;
            this.sessionSent += (next - loaded) * originalSize / body.blob.size;
            loaded = next;
            touch();
          }
          const now = this.env.now(), originalLoaded = loaded * originalSize / body.blob.size;
          if (now - sampledAt >= 200 && originalLoaded > sampled) {
            this.sample(originalLoaded - sampled, now - sampledAt);
            sampled = originalLoaded; sampledAt = now;
          }
          this.notify('uploading', {sent: this.offset + originalLoaded, phase: 'transferring'});
        };
        xhr.open('PUT', this.options.url(this.offset));
        const token = typeof this.options.token === 'function' ? this.options.token() : this.options.token;
        if (token) xhr.setRequestHeader('X-App-Token', token);
        if (body.compressed) {
          xhr.setRequestHeader('Content-Encoding', 'gzip');
          xhr.setRequestHeader('X-Upload-Size', String(originalSize));
        }
        // A progressing slow upload is never interrupted by a fixed total timeout.
        xhr.timeout = 0;
        xhr.upload.onprogress = event => progress(event.loaded);
        xhr.onprogress = touch;
        xhr.onerror = () => finish(reject, failure('Upload connection interrupted.'));
        xhr.ontimeout = () => finish(reject, failure('Upload connection stopped responding.'));
        xhr.onabort = () => finish(reject, this.signal?.aborted ? aborted() : failure('Upload connection interrupted.'));
        xhr.onload = () => {
          let result;
          try { result = JSON.parse(xhr.responseText); }
          catch { result = null; }
          if (xhr.status < 200 || xhr.status >= 300) {
            finish(reject, failure(result?.error || `Upload failed (HTTP ${xhr.status}).`, {status: xhr.status}));
          } else if (!result) finish(reject, failure('Invalid upload acknowledgement.'));
          else { progress(body.blob.size); finish(resolve, result); }
        };
        this.signal?.addEventListener('abort', cancel, {once: true});
        this.env.events?.addEventListener('offline', offline);
        if (this.signal?.aborted) { cancel(); return; }
        touch();
        try { xhr.send(body.blob); } catch (error) { finish(reject, error); }
      });
    }
    validateOffset(value) {
      if (!Number.isSafeInteger(value) || value < 0 || value > this.total)
        throw failure('Server returned an invalid upload offset.', {fatal: true});
      return value;
    }
    acknowledge(originalBytes, body, originalSize) {
      if (originalBytes <= 0) return;
      this.sessionOriginalBytes += originalBytes;
      this.encodedBytes += originalBytes * body.blob.size / originalSize;
    }
    async retry(error) {
      if (error.name === 'AbortError' || fatal(error)) throw error;
      if (++this.attempt > 6) throw failure('Upload connection is still unavailable. Resume to continue from the saved progress.', {cause: error});
      this.notify('retrying', {message: error.message});
      await this.online();
      await this.sleep(Math.min(16000, 500 * 2 ** (this.attempt - 1)) * (0.8 + this.env.random() * 0.4));
    }
    async status() {
      // Each timed status check owns its request. A timed-out fetch must not retain
      // a browser connection and block the next retry or a later Resume action.
      const controller = new AbortController();
      const cancel = () => controller.abort();
      this.signal?.addEventListener('abort', cancel, {once: true});
      let completed = false;
      try {
        this.check();
        const result = await this.waitFor(this.options.getStatus({signal: controller.signal}), this.idleMs);
        completed = true;
        return result;
      } finally {
        this.signal?.removeEventListener('abort', cancel);
        if (!completed) controller.abort();
      }
    }
    async reconcile(expectedEnd) {
      const busyStarted = this.env.now();
      for (;;) {
        await this.online();
        this.notify('verifying');
        try {
          const status = await this.status();
          const uploaded = this.validateOffset(status.uploaded);
          if (uploaded > expectedEnd) throw failure('This file is being uploaded by another tab. Resume from its latest saved progress.', {fatal: true});
          if (status.in_flight) {
            if (this.env.now() - busyStarted > this.idleMs * 2)
              throw failure('The server is still saving a previous chunk. Resume after it finishes.', {fatal: true});
            await this.sleep(500);
            continue;
          }
          return uploaded;
        } catch (error) { await this.retry(error); }
      }
    }
    async upload(file, initialOffset = 0) {
      if (this.active) throw failure('This transfer is already running.', {fatal: true});
      this.check();
      this.total = file.size;
      this.offset = this.validateOffset(initialOffset);
      this.attempt = 0; this.active = true;
      try {
        this.notify('uploading');
        while (this.offset < this.total) {
          await this.online();
          const start = this.offset, length = Math.min(this.chunkSize, this.total - start), expectedEnd = start + length;
          const started = this.env.now();
          const body = await this.prepare(file.slice(start, expectedEnd));
          this.check();
          try {
            const result = await this.send(body, length, started);
            if (this.validateOffset(result.uploaded) !== expectedEnd) throw failure('Upload acknowledgement needs verification.');
            this.offset = expectedEnd;
            this.acknowledge(length, body, length);
          } catch (error) {
            if (error.name === 'AbortError' || fatal(error)) throw error;
            this.offset = await this.reconcile(expectedEnd);
            this.acknowledge(this.offset - start, body, length);
            if (this.offset !== expectedEnd) {
              if (this.offset > start) this.attempt = 0;
              await this.retry(error);
              continue;
            }
          }
          const elapsed = Math.max(1, this.env.now() - started);
          this.sample(length, elapsed);
          // Adapt to measured end-to-end throughput, including compression and acknowledgement.
          const target = length / elapsed * 3000;
          this.chunkSize = clamp(Math.round(clamp(target, this.chunkSize / 2, this.chunkSize * 2) / 65536) * 65536, MIN_CHUNK, MAX_CHUNK);
          this.attempt = 0;
          this.notify('uploading');
        }
        return this.notify('complete', {sent: this.total, etaSeconds: 0});
      } finally { this.active = false; }
    }
  };
});
