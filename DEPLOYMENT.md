# On-premises Linux deployment (no login)

This mode is for a small team on a trusted internal network. There is no login,
user directory, or authenticated user identity. A random browser cookie separates
workspaces, jobs, uploads, logs, reports, and ignore-key containers. The app does
not use filenames as job identifiers, so five people can upload files with the
same names without overwriting each other.

Different browsers/profiles receive different workspaces. Tabs in the same
browser profile share one workspace. The cookie lasts 180 days and survives
server restarts. Clearing cookies or using another browser loses access to the
old workspace in the UI, although its files remain on the server. Treat the
cookie as a workspace access token; this separation is not a substitute for
network access control. Keep the service off the public internet.

## Install and start

Requirements: Linux, 64-bit Python 3.10+, Bash, `nohup`, and `flock` (util-linux).
CSV, Excel and JSON use the standard library. Parquet requires PyArrow: create `.venv` with `python3 -m venv .venv`, then run `.venv/bin/python -m pip install -r requirements.txt`. `deploy.sh` prefers that environment. Run under an ordinary account with read access to the
application and write access to the data/run directories. Use a local SSD.

```bash
git clone https://github.com/aneesh-dev1/KeyBasedComparetool.git
cd KeyBasedComparetool
cp deploy.env.example deploy.env
chmod 600 deploy.env
```

Edit `deploy.env` with your hostname or server IP and real port:

```bash
PUBLIC_URL="http://10.0.0.25:8765"
BIND_HOST="0.0.0.0"
PORT=8765
DATA_DIR="/srv/key-compare/data"
RUN_DIR="/srv/key-compare/run"
PYTHON_BIN="python3"
MAX_JOBS=1
MAX_SORT_MB=0
```

Create the data/run folders with ownership for the account running the app.
Use the actual server address; `0.0.0.0` is a bind address, not a browser URL.
Allow the port only from your team's internal network according to your network
policy. Then:

```bash
./deploy.sh start
./deploy.sh status
tail -f /srv/key-compare/run/server.log
```

The launcher uses `nohup`, disconnects stdin, writes a PID file and a private log,
and waits for its own process to return a successful health response. It prevents
simultaneous launcher commands. The Python server locks the data directory to
prevent a second server from changing the same jobs. `deploy.env`, runtime files,
and uploaded data are git-ignored.

Users open `PUBLIC_URL`. HTTPS through your existing internal reverse proxy is
recommended when the inputs contain sensitive data. Set `BIND_HOST=127.0.0.1` and
`PUBLIC_URL=https://compare.internal` for a proxy on the same server. Preserve the
public Host header, proxy at the URL root, allow at least 64 MiB request bodies
(JSON requests can exceed the 8 MiB CSV chunk size), and set request timeouts to
accommodate uploads and JSON comparisons. Use a single backend process. Do not
place independent app instances behind a load balancer with shared storage.

The script accepts `COMPARE_CONFIG=/absolute/path/deploy.env`. This is a trusted
shell configuration file and must be editable only by the service administrator.
The launcher binds localhost or all IPv4 interfaces; IPv6 is not configured.

## Five users and resource limits

Five browsers can upload concurrently and have independent comparisons. The
server's heavy-job queue covers CSV comparison, Excel import, report export, and Analysis index preparation.
With `MAX_JOBS=1`, only one of those runs at a time; other jobs show Queued. This
protects memory and disk throughput while keeping the HTTP UI responsive.

Start with `MAX_JOBS=1` and `MAX_SORT_MB=0`. Each worker measures available RAM,
CPU capacity and free workspace storage when it leaves the queue. It chooses its
memory budget, read/sort batch, comparison batch and I/O buffers automatically.
There is no fixed 15 GB ceiling: larger machines can use more RAM. The policy
keeps at least 25% of available RAM, and at least 2 GB where possible, outside the
comparison budget for the OS, decoding and reports. It accounts for configured
concurrent workers. Very low memory uses conservative small batches; less than
128 MB available RAM or free workspace storage prevents a new run.

A bounded hash lookup avoids sorting when the inputs fit; compressed disk sorting
remains the fallback. Source reads and disk sorting are sequential to avoid HDD
seek contention. Additional browsers can upload and queue independently. Free
disk checks are not reservations or a guarantee that dense mismatch output fits.
The effective choices are recorded in each job's log and runtime-settings.json.

An administrator can optionally set `MAX_SORT_MB` to a positive MB ceiling.
`--max-memory-mb` is an alias for the existing `--max-sort-mb` server option.
Explicit deployment limits remain effective, while saved browser tuning no longer
controls execution. Interrupted hash runs rebuild their in-memory lookup on
resume; completed disk-sort checkpoints remain reusable.

JSON is processed separately from the CSV/export queue. One JSON comparison runs
at a time; a second concurrent JSON request receives a retry message instead of
building an unbounded in-memory queue. Its existing 5 MiB/input, depth, node and
10,000-difference limits remain in force.

Five pairs of 6 GB uploads alone need approximately **60 GB of uploaded storage**,
plus the original files if stored on this server, scratch space, imported Excel
CSVs, and reports. Heavy jobs can produce enormous mismatch output. Watch free
disk and RSS during a representative pilot. The app checks free space before each
upload chunk, but does not reserve disk space or enforce per-user quotas.
Automatic cleanup runs at startup and hourly, deleting jobs after 7 days of
inactivity by default. Configure `RETENTION_DAYS=7` in deploy.env, or pass
`--retention-days 7` to server.py directly. Set 0 to disable automatic expiry.
This applies to existing jobs too: review/download old results before upgrading.
Upload/configuration activity and newly generated exports reset the retention
clock; simply viewing a job does not. Incomplete uploads and unused drafts also
expire. Running/queued comparisons, imports, exports and active HTTP requests
are protected. Cleanup retries interrupted deletions after a restart.

Users can also **Delete** an unneeded job in Job history after confirming. This
permanently removes its uploaded CSV/Excel copies, imported CSVs, scratch data,
logs, mismatch records and generated reports. Download reports you need first.
Deleting a job does not affect original client files, other browser workspaces,
or reusable ignore-key containers. It removes the job from history. There is no
undo, and files in transit may cause deletion to be deferred on Windows.

For two 6 GB inputs, the uploaded copies account for 12 GB; deleting that job
reclaims those copies plus any remaining scratch/output data. Cleanup is not a
disk quota and cannot guarantee enough free space for a new comparison.

## Stop, restart and update

```bash
./deploy.sh stop
./deploy.sh status
# Once the server has exited:
git pull --ff-only
./deploy.sh start
```

Stop sends SIGTERM after verifying the PID belongs to this app and data directory.
The server stops accepting new requests and drains queued/active heavy work.
The stop command waits up to 30 seconds and reports if draining continues. A
large comparison can take much longer; do not start a replacement until it exits.
If forcibly killed or the machine reboots, unfinished jobs become Error on next
startup, and affected comparisons must be started again. Completed jobs remain.
After restart, refresh browser tabs to obtain the new request token.

`nohup` survives a disconnected terminal; it does **not** restart after a crash or
machine reboot. Rotate `run/server.log` and manage retained files according to
your team's storage policy. Back up the entire data directory with the service
stopped if you need to retain jobs and browser-workspace associations.

## Verification and limits of this review

Run the automated suite before rollout:

```bash
python3 -m unittest discover -q
```

Coverage includes CSV comparison and reordering, Excel imports, header aliases,
case-insensitive headers, scope/overrides, ignored containers, batching, JSON
alignment, uploads/resume, previews, history, Excel/HTML exports, and five
simultaneous browser workspaces. The multi-user test confirms that other
workspaces cannot open a job's metadata, logs, previews, or downloads.

These are correctness tests on small fixtures. This change has not been
benchmarked with five real 6 GB pairs or run on your Linux hardware. The Linux
launcher has shell syntax validation; Python startup, data locking, health checks,
and graceful shutdown are exercised separately. Pilot with representative files,
watch storage/memory, and confirm access through your real hostname/proxy before
opening it to the whole team. This is a bounded small-team service, not a hardened
public multi-tenant web platform.

## Workflow operations

Users can cancel their queued/running tasks in Storage & queue or the relevant
comparison/export/analysis screen. Running work stops cooperatively at a safe
checkpoint; do not terminate the server to cancel one user's job. Cancelled
comparisons/imports need a new comparison; exports and analysis may be retried.

Storage measurement is a snapshot of logical file sizes in the current workspace.
Free disk space is server-wide. Preflight does not reserve disk for concurrent
uploads. Profiles remain in the workspace library after job cleanup; analysis
notes are part of the job and are deleted with it.

Desktop notifications require browser permission and an open page. HTTPS is
generally required for remote hostnames; localhost is an exception in supporting
browsers. No email, external messaging service, or background push server is used.
