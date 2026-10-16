const assert = require('node:assert/strict');
const {gunzipSync} = require('node:zlib');
const UploadTransfer = require('./web/upload-transport.js');
const MiB = 1024 * 1024;

class Clock {
  constructor() { this.time = 0; this.next = 1; this.tasks = new Map(); }
  setTimeout = (fn, ms) => { const id = this.next++; this.tasks.set(id, {at: this.time + ms, fn}); return id; };
  clearTimeout = id => this.tasks.delete(id);
  tick() {
    const entry = [...this.tasks].sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
    if (!entry) return false;
    this.tasks.delete(entry[0]); this.time = entry[1].at; entry[1].fn(); return true;
  }
}
async function flush() { for (let i = 0; i < 12; i++) await Promise.resolve(); }
async function drive(promise, clock) {
  let done = false, result, failure;
  promise.then(value => { done = true; result = value; }, error => { done = true; failure = error; });
  for (let i = 0; i < 2000 && !done; i++) {
    await flush();
    if (!done && !clock.tick()) throw Error('Test stalled without a timer');
  }
  if (!done) throw Error('Test did not settle');
  if (failure) throw failure;
  return result;
}
const file = size => ({size, slice: (start, end) => ({size: end - start, start, end})});
function harness(script, options = {}) {
  const clock = new Clock(), events = new EventTarget(), requests = [], snapshots = [];
  let online = true, serverOffset = options.initialOffset || 0;
  const h = {clock, events, requests, snapshots, get online() { return online; }, get serverOffset() { return serverOffset; }, set serverOffset(value) { serverOffset = value; }};
  h.setOnline = value => { online = value; events.dispatchEvent(new Event(value ? 'online' : 'offline')); };
  class XHR {
    constructor() { this.upload = {}; this.headers = {}; this.aborted = false; }
    open(method, url) { this.method = method; this.url = url; this.offset = Number(new URL(url, 'http://local').searchParams.get('offset')); }
    setRequestHeader(name, value) { this.headers[name] = value; }
    send(blob) { this.body = blob; requests.push(this); script(this, h); }
    abort() { this.aborted = true; this.onabort?.(); }
    progress(loaded) { if (!this.aborted) this.upload.onprogress?.({loaded}); }
    reply(status, object) { if (!this.aborted) { this.status = status; this.responseText = JSON.stringify(object); this.onload?.(); } }
    fail() { if (!this.aborted) this.onerror?.(); }
  }
  h.transfer = new UploadTransfer({
    url: offset => `/files/left?offset=${offset}`, token: () => 'csrf-token',
    getStatus: async () => ({uploaded: serverOffset}), onProgress: snapshot => snapshots.push(snapshot),
    ...options,
    env: {xhr: () => new XHR(), now: () => clock.time, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
      online: () => online, events, random: () => 0.5, ...(options.env || {})},
  });
  return h;
}
function successful(xhr, h, duration = 3000) {
  for (let part = 1; part <= 3; part++) h.clock.setTimeout(() => xhr.progress(xhr.body.size * part / 3), duration * part / 3);
  h.clock.setTimeout(() => {
    h.serverOffset = xhr.offset + Number(xhr.headers['X-Upload-Size'] || xhr.body.size);
    xhr.reply(200, {uploaded: h.serverOffset});
  }, duration + 1);
}
const tests = [];
function test(name, fn) { tests.push([name, fn]); }

test('resume speed excludes the existing prefix and uses authenticated original offsets', async () => {
  const h = harness(successful, {initialOffset: 5 * MiB});
  const result = await drive(h.transfer.upload(file(7 * MiB), 5 * MiB), h.clock);
  assert.deepEqual(h.requests.map(r => r.offset), [5 * MiB, 6 * MiB]);
  assert(h.requests.every(r => r.headers['X-App-Token'] === 'csrf-token'));
  assert.equal(result.uploaded, 7 * MiB); assert.equal(result.sessionOriginalBytes, 2 * MiB);
  assert(result.bytesPerSecond > 300000 && result.bytesPerSecond < 400000);
  assert.equal(result.etaSeconds, 0); assert.equal(result.wireBytes, 2 * MiB);
  assert(h.snapshots.some(s => s.sent > s.uploaded && s.etaSeconds > 0));
});

test('slow chunks continue while progressing, shrink adaptively and have no total XHR timeout', async () => {
  const h = harness((xhr, h) => successful(xhr, h, xhr.body.size / MiB * 60000), {idleMs: 25000});
  await drive(h.transfer.upload(file(2 * MiB)), h.clock);
  assert.deepEqual(h.requests.map(r => r.body.size), [MiB, MiB / 2, MiB / 4, MiB / 4]);
  assert(h.requests.every(r => r.timeout === 0 && !r.aborted));
});

test('fast chunks grow gradually and remain capped at 8 MiB', async () => {
  const h = harness((xhr, h) => successful(xhr, h, 100));
  await drive(h.transfer.upload(file(40 * MiB)), h.clock);
  assert.deepEqual(h.requests.slice(0, 4).map(r => r.body.size), [MiB, 2 * MiB, 4 * MiB, 8 * MiB]);
  assert(h.requests.every(r => r.body.size <= 8 * MiB));
});

test('a lost response reconciles acknowledgement without sending the chunk twice', async () => {
  const h = harness((xhr, h) => {
    if (h.requests.length === 1) h.clock.setTimeout(() => { h.serverOffset = xhr.offset + xhr.body.size; xhr.fail(); }, 100);
    else successful(xhr, h);
  });
  await drive(h.transfer.upload(file(3 * MiB)), h.clock);
  assert.equal(h.requests[1].offset, MiB);
  assert.equal(h.requests.length, 2);
  assert(h.snapshots.some(s => s.state === 'verifying'));
});

test('in-flight status waits for atomic server commit before deciding to resend', async () => {
  let statusCalls = 0;
  const h = harness((xhr, h) => h.clock.setTimeout(() => xhr.fail(), 10), {
    getStatus: async () => ({uploaded: ++statusCalls < 3 ? 0 : MiB, in_flight: statusCalls < 3}),
  });
  await drive(h.transfer.upload(file(MiB)), h.clock);
  assert.equal(statusCalls, 3); assert.equal(h.requests.length, 1);
});

test('fatal client errors are not retried', async () => {
  let statusCalls = 0;
  const h = harness((xhr, h) => h.clock.setTimeout(() => xhr.reply(413, {error: 'Too big'}), 10), {getStatus: async () => { statusCalls++; return {uploaded: 0}; }});
  await assert.rejects(drive(h.transfer.upload(file(MiB)), h.clock), /Too big/);
  assert.equal(h.requests.length, 1); assert.equal(statusCalls, 0);
});

test('idle watchdog aborts stalled requests and stops after six transient retries', async () => {
  const h = harness(() => {}, {idleMs: 100});
  await assert.rejects(drive(h.transfer.upload(file(MiB)), h.clock), /Resume to continue/);
  assert.equal(h.requests.length, 7);
  assert(h.requests.every(r => r.aborted));
  assert.equal(h.clock.tasks.size, 0);
});

test('409 reconciles a partial acknowledged offset and sends only the remainder', async () => {
  const h = harness((xhr, h) => {
    if (h.requests.length === 1) h.clock.setTimeout(() => { h.serverOffset = 512 * 1024; xhr.reply(409, {error: 'Offset changed'}); }, 10);
    else successful(xhr, h);
  });
  await drive(h.transfer.upload(file(MiB)), h.clock);
  assert.equal(h.requests[1].offset, MiB / 2); assert.equal(h.requests[1].body.size, MiB / 2);
});

test('offline waits automatically and wakes on online without consuming retries', async () => {
  const h = harness(successful); h.setOnline(false);
  const promise = h.transfer.upload(file(MiB)); await flush();
  assert.equal(h.requests.length, 0); assert.equal(h.snapshots.at(-1).state, 'offline');
  h.clock.setTimeout(() => h.setOnline(true), 5000);
  await drive(promise, h.clock);
  assert.equal(h.requests.length, 1);
});

test('pause aborts an active chunk immediately', async () => {
  const controller = new AbortController(), h = harness(() => {}, {signal: controller.signal});
  const promise = h.transfer.upload(file(MiB)); await flush();
  assert.equal(h.requests.length, 1); controller.abort();
  await assert.rejects(promise, {name: 'AbortError'});
  assert.equal(h.requests[0].aborted, true); assert.equal(h.clock.tasks.size, 0);
});

test('pause cancels an offline wait and a pending reconciliation', async () => {
  for (const offline of [true, false]) {
    const controller = new AbortController();
    const h = harness(xhr => xhr.fail(), {signal: controller.signal, getStatus: () => new Promise(() => {})});
    if (offline) h.setOnline(false);
    const promise = h.transfer.upload(file(MiB)); await flush(); controller.abort();
    await assert.rejects(promise, {name: 'AbortError'}); assert.equal(h.clock.tasks.size, 0);
  }
});

test('gzip payload is lossless and retains original byte offsets and progress', async () => {
  const raw = new Blob(['id,value\n' + '123,recurring-example\n'.repeat(10000)]);
  const seen = [];
  const h = harness((xhr, h) => { seen.push(xhr.body); successful(xhr, h); }, {
    compress: true, env: {compress: async blob => new Blob([require('node:zlib').gzipSync(Buffer.from(await blob.arrayBuffer()))])},
  });
  // Native Blob promises use the event loop, so allow preparation before advancing fake timers.
  const promise = h.transfer.upload(raw); await new Promise(resolve => setImmediate(resolve));
  const result = await drive(promise, h.clock);
  assert.equal(h.requests[0].headers['Content-Encoding'], 'gzip');
  assert.equal(Number(h.requests[0].headers['X-Upload-Size']), raw.size);
  assert.equal(gunzipSync(Buffer.from(await seen[0].arrayBuffer())).toString(), await raw.text());
  assert.equal(result.sessionOriginalBytes, raw.size); assert.equal(result.uploaded, raw.size);
  assert(result.compressionRatio < 0.15); assert.equal(result.wireBytes, seen[0].size);
});

test('incompressible chunks disable further compression and keep raw upload headers', async () => {
  let compressCalls = 0;
  const h = harness(successful, {compress: true, env: {compress: async blob => { compressCalls++; return {size: blob.size}; }}});
  const result = await drive(h.transfer.upload(file(3 * MiB)), h.clock);
  assert.equal(compressCalls, 1); assert(h.requests.every(r => !r.headers['Content-Encoding']));
  assert.equal(result.compressionRatio, 1);
});

test('compression errors fall back to raw and pause interrupts compression promptly', async () => {
  const fallback = harness(successful, {compress: true, env: {compress: async () => { throw Error('Unsupported compression'); }}});
  await drive(fallback.transfer.upload(file(10)), fallback.clock);
  assert.equal(fallback.requests[0].headers['Content-Encoding'], undefined);
  const controller = new AbortController();
  const h = harness(successful, {signal: controller.signal, compress: true, env: {compress: () => new Promise(() => {})}});
  const promise = h.transfer.upload(file(MiB)); await flush(); controller.abort();
  await assert.rejects(promise, {name: 'AbortError'}); assert.equal(h.requests.length, 0);
});

test('compression time contributes to speed and chunk sizing', async () => {
  let h;
  h = harness((xhr, h) => successful(xhr, h, 100), {compress: true, env: {compress: blob => new Promise(resolve => h.clock.setTimeout(() => resolve({size: blob.size / 10}), 10000))}});
  const result = await drive(h.transfer.upload(file(MiB)), h.clock);
  assert(result.bytesPerSecond < MiB / 5, 'effective speed must include ten seconds spent compressing');
  assert.equal(result.chunkSize, MiB / 2, 'CPU-bound compression must reduce next chunk size');
});

test('losing connectivity aborts the request and resumes only from server acknowledgement', async () => {
  const h = harness((xhr, h) => {
    if (h.requests.length === 1) h.clock.setTimeout(() => h.setOnline(false), 10);
    else successful(xhr, h);
  });
  const promise = h.transfer.upload(file(MiB));
  h.clock.setTimeout(() => h.setOnline(true), 1000);
  await drive(promise, h.clock);
  assert.equal(h.requests[0].aborted, true); assert.equal(h.requests[1].offset, 0);
  assert(h.snapshots.some(snapshot => snapshot.state === 'offline'));
});

test('status timeouts cancel their fetch and retry with a fresh signal', async () => {
  const signals = [], aborted = [];
  const h = harness((xhr, h) => {
    if (h.requests.length === 1) h.clock.setTimeout(() => xhr.fail(), 10);
    else successful(xhr, h, 30);
  }, {idleMs: 100, getStatus: ({signal}) => {
    signals.push(signal);
    signal.addEventListener('abort', () => aborted.push(signal));
    return signals.length === 1 ? new Promise(() => {}) : Promise.resolve({uploaded: 0});
  }});
  await drive(h.transfer.upload(file(MiB)), h.clock);
  assert.equal(signals.length, 2); assert.notEqual(signals[0], signals[1]);
  assert.equal(signals[0].aborted, true); assert.equal(signals[1].aborted, false);
  assert.deepEqual(aborted, [signals[0]]); assert.equal(h.requests.length, 2);
});

test('transfer pause cancels a pending status fetch and ignores late progress events', async () => {
  const controller = new AbortController(); let statusSignal;
  const h = harness(xhr => xhr.fail(), {signal: controller.signal, getStatus: ({signal}) => {
    statusSignal = signal; return new Promise(() => {});
  }});
  const promise = h.transfer.upload(file(MiB)); await flush();
  assert(statusSignal && !statusSignal.aborted);
  controller.abort(); await assert.rejects(promise, {name: 'AbortError'});
  assert.equal(statusSignal.aborted, true);
  const count = h.snapshots.length;
  h.requests[0].upload.onprogress({loaded: MiB}); h.requests[0].onprogress();
  assert.equal(h.snapshots.length, count); assert.equal(h.clock.tasks.size, 0);
});

test('successful status checks unlink pause listeners without aborting completed fetches', async () => {
  const controller = new AbortController(); let statusSignal;
  const h = harness((xhr, h) => {
    h.clock.setTimeout(() => { h.serverOffset = xhr.offset + xhr.body.size; xhr.fail(); }, 10);
  }, {signal: controller.signal, getStatus: async ({signal}) => { statusSignal = signal; return {uploaded: MiB}; }});
  await drive(h.transfer.upload(file(MiB)), h.clock);
  assert.equal(statusSignal.aborted, false);
  controller.abort();
  assert.equal(statusSignal.aborted, false, 'completed check must not retain the transfer pause listener');
});

test('default timers preserve the browser global receiver', async () => {
  const clock = new Clock(); let timerCalls = 0, clearCalls = 0;
  const context = require('node:vm').createContext({
    AbortController, performance: {now: () => clock.time},
    timerSet: (fn, ms) => { timerCalls++; return clock.setTimeout(fn, ms); },
    timerClear: id => { clearCalls++; return clock.clearTimeout(id); },
  });
  require('node:vm').runInContext(`
    globalThis.setTimeout = function(fn, ms) { 'use strict'; if (this !== globalThis) throw new TypeError('Illegal invocation'); return timerSet(fn, ms); };
    globalThis.clearTimeout = function(id) { 'use strict'; if (this !== globalThis) throw new TypeError('Illegal invocation'); return timerClear(id); };
  ` + require('node:fs').readFileSync('web/upload-transport.js', 'utf8'), context);
  const transfer = new context.UploadTransfer({url: offset => '/files/left?offset=' + offset,
    getStatus: async () => ({uploaded: 0}), env: {xhr: () => ({upload: {}, open() {}, setRequestHeader() {},
      send(blob) { clock.setTimeout(() => { this.status = 200; this.responseText = JSON.stringify({uploaded: blob.size}); this.onload(); }, 10); }, abort() {}})}});
  const result = await drive(transfer.upload(file(100)), clock);
  assert.equal(result.uploaded, 100); assert(timerCalls > 0); assert(clearCalls > 0);
});

test('invalid or unexpectedly advanced status is fatal rather than overwriting another upload', async () => {
  for (const uploaded of [-1, 2 * MiB]) {
    const h = harness(xhr => xhr.fail(), {getStatus: async () => ({uploaded})});
    await assert.rejects(drive(h.transfer.upload(file(3 * MiB)), h.clock), /invalid upload offset|another tab/);
    assert.equal(h.requests.length, 1);
  }
});

(async () => {
  for (const [name, fn] of tests) { await fn(); console.log('PASS ' + name); }
  console.log(`${tests.length} upload transport tests passed`);
})().catch(error => { console.error(error); process.exitCode = 1; });
