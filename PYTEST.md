# PYTEST.md — introducing pytest to the Python code

This file records how the pytest suite was added: the decisions, each step and its reasons,
what the tests cover, and the phases still to come. Like `VMS-PORT-PLAN.md`, it is a working
record. Bugs the tests find go to `FoundAndFixed.md` as usual, and this file cites them as
`#N`.

**Status (2026-10-28):** Phases 1 and 2 are done, and the one bug they found (#84) is fixed.
The suite has 614 tests, all passing, in about 10 s on the Pi; GitHub Actions runs it on every
push. Phase 1 (steps 1–8) set the suite up and tested the pure logic. Phase 2 (its own section,
below) tests behaviour against fakes of AWS, MediaMTX, systemd and the network. Phases 3–4 are
under "Next phases".

```bash
venv-adapter/bin/python -m pytest                 # everything
venv-adapter/bin/python -m pytest tests/cloud     # one folder
venv-adapter/bin/python -m pytest -k ff37         # tests for one FoundAndFixed entry
```

---

## Decisions

| | Decision | Why |
|---|---|---|
| D1 | pytest is installed in `venv-adapter`, not in a separate venv | That venv already has every module the code imports; a second venv would duplicate it and drift |
| D2 | `agent.py` may be refactored so it can be imported (Phase 4) | It connects to AWS IoT at import time, so a test can't load it today |
| D3 | GitHub Actions runs the suite from the start | A broken test is seen on push, not on the next manual run |
| D4 | No moto; fakes and botocore's `Stubber` instead | moto pulls a large dependency tree onto the Pi, and Phase 1 needs no AWS behaviour at all. Reconsider only if Phase 2's conditional writes (`rematch_cameras.registry_update`) need real DynamoDB semantics |
| D5 | The tests never touch the real system, by construction rather than by care | They run on the live Pi: see step 3 |

D1–D3 are the user's (2026-10-28). D4 and D5 were proposed with them and accepted.

---

## Step 1 — the dependencies: `requirements.txt` and `requirements-test.txt`

Before this step, the list of packages `venv-adapter` needs was written out in
`setup/setup-user.sh` and repeated in LAUNCH A4 and guide §7.2. #27 was that list falling out of
date. CI needs the same list, so a third copy would have been the next drift.

- **`requirements.txt`**: the seven runtime packages (boto3, awsiotsdk, onvif-zeep-async,
  WSDiscovery, flask, requests, lxml). They stay unpinned, as the setup has always installed
  them.
- **`requirements-test.txt`**: `pytest>=8`, and nothing else (D4).
- **`setup/setup-user.sh` A4** installs both with `pip install -r … -r …`, and has a new proof,
  "pytest in the venv". LAUNCH A4 and guide §7.2 now point to `requirements.txt` instead of
  listing the packages.

On this Pi: `venv-adapter/bin/pip install pytest` installed pytest 9.2.0. Re-running the new
install line afterwards changes nothing.

## Step 2 — `pytest.ini`

```ini
testpaths = tests
pythonpath = adapter tests
addopts = -ra --strict-markers --strict-config --import-mode=importlib
xfail_strict = true
```

- **`pythonpath = adapter`**: tests import `camera_control` and the other modules by name, as
  the services do. There is no packaging and no `sys.path` code in the tests. `tests` is on the
  path for `loaders.py` (step 4).
- **`--import-mode=importlib`**: test files in different folders may share a name, and the
  `tests/` folders need no `__init__.py`.
- **`xfail_strict`**: a test marked as an expected failure turns the run red once it starts
  passing. That is how a known, open defect is pinned; #84 was pinned this way until it was
  fixed.
- **`.gitignore`** gained `.pytest_cache/`.

## Step 3 — the safety guards (`tests/conftest.py`)

The tests run on the Pi that runs the system:

- MediaMTX answers on `127.0.0.1:9997`.
- `/etc/adapter/adapter.env` names the real AWS account, which is shared with VMS.
- `/etc/adapter/channels/` decides which unit a camera has.
- The sudo rule lets `sudo systemctl start kvs-cam01.service` start a producer that is billed.

A forgotten fake must therefore fail loudly instead of reaching any of these. `conftest.py`
sets up the guards **when it is imported**, before pytest imports any test module, and so
before any project module computes its constants.

| # | Guard | What it does | Why |
|---|---|---|---|
| 1 | Fake identity | `ADAPTER_CONFIG` → `tests/fixtures/adapter.env` (Thing `test-thing`, endpoints under `.invalid`). The six identity keys and `VMS_HOME` are removed from the environment | `config.py` lets the environment override the file, so an exported real value would win over the fake one. With `VMS_HOME` gone, the code finds this checkout from its own location, as under systemd |
| 1 | Fake AWS credentials | `AWS_ACCESS_KEY_ID=testing`…, `AWS_CONFIG_FILE` and `AWS_SHARED_CREDENTIALS_FILE` → `/dev/null`, profile variables removed, instance metadata disabled | Nothing can sign a request to the real account, even when the network guard is bypassed |
| 2 | Throw-away home | `HOME`, `XDG_RUNTIME_DIR`, `XDG_STATE_HOME` → a temporary directory, removed after the run | `aws_state`, `sync_mediamtx_paths` and `outage_buffer` derive state paths from these when imported |
| 3 | No network | `socket.connect`/`connect_ex` on IPv4/IPv6, `sendto` (WS-Discovery's multicast is UDP with no connect) and `getaddrinfo` raise `Blocked` | requests, urllib3 and botocore all end up there |
| 4 | No commands | `subprocess.Popen` (which covers `run`, `check_output` and asyncio subprocesses) and `os.system` raise `Blocked`, except for `ldconfig`/`gcc`/`cc`/`ld`/`objdump` | Those are read-only probes that `ctypes.util.find_library` runs; WS-Discovery's `ifaddr` calls `ldconfig` when it's imported. Everything else is blocked: `systemctl`, `sudo`, `ffmpeg`, `gst-launch-1.0` and the project's own scripts |
| 5 | Paths into `tmp_path` | Around every test, these attributes point into that test's `tmp_path`: `camera_control.CHANNELS_DIR`, `aws_state.STATE_FILE`, the `outage_buffer`/`outage_uploader` buffer and state paths, and `sync_mediamtx_paths`'s cache | A real `/etc/adapter/channels/cam03.env` would change `unit_name()`'s answer on the Pi but not in CI, and the USB stick holds captured outages |
| 6 | Escapes fail the test | Each blocked attempt is recorded, and a test that made one fails at teardown | **`Blocked` is a `RuntimeError`, not an `OSError`, on purpose.** The code under test catches `OSError` and requests' errors as "camera unreachable", and `outage_uploader.ffprobe()` catches every `Exception`. Without the record, a missing fake could turn into a passing test |

The guards don't cover:

- **awscrt's own C networking.** That is why `agent.py`, which opens its MQTT connection when
  imported, isn't imported until Phase 4 makes that possible.
- **Writes to absolute paths that aren't listed in guard 5.** Add a path to `_SANDBOXED_PATHS`
  when a new module gains one.

Shared fakes, also in `conftest.py`:

- **`fake_run(module, answers)`** replaces that module's `subprocess.run`. It records each argv
  and answers from a prefix table, e.g. `{("systemctl", "is-active"): "active\n"}`; a
  `(stdout, returncode)` tuple sets the exit code.
- **`expect_blocked`** is for the guard tests only: it forgives the attempts a test makes on
  purpose.

`tests/test_sandbox.py` (12 tests) checks every guard, including that a swallowed attempt
still fails. **If one of those tests fails, stop:** the other tests may be reading the Pi's
real configuration.

## Step 4 — importing the Lambdas and scripts (`tests/loaders.py`)

`cloud/lambda/*.py` are single files, not a package, and `adapter/bin/*.py` have hyphens in
their names. `load_lambda("record_clip")` and `load_script("gap-fill")` import them from their
path, under a unique module name, once per run.

A Lambda reads `AWS_REGION` and `BUCKET` and builds its boto3 resources when imported.
`conftest.py` has set fake values by then, and building a resource sends nothing, so the
import is offline. Before importing anything, all 32 importable files were loaded under these
guards as a check. Only `agent.py` and `observe_state.py` can't be imported, because they
connect when imported.

## Step 5 — the Phase 1 tests

Phase 1 covers pure logic: functions that compute an answer from their inputs. Where a
function has a dependency (MediaMTX, `systemctl`, an RTSP DESCRIBE, the camera), the test
replaces only that one call, at the module boundary, with `monkeypatch`. **A test named
`test_ffNN_…` reproduces `FoundAndFixed.md #NN`** — `pytest -k ff37` runs that bug's tests.

| File | Tests | What it covers | Bugs |
|---|---:|---|---|
| `adapter/test_camera_control.py` | 24 | Path, unit and live names. Both unit families, decided by a channel file in the sandboxed `CHANNELS_DIR`. `set_stream`/`set_boot_start`/`has_producer` with `fake_run`. IR mode validated before any camera call | #7, #37, #81 |
| `adapter/test_mediamtx_api.py` | 52 | `_dur_secs`/`_dur_eq` on every duration the live MediaMTX v1.20.1 reports (`200ms` … `1d`), other spellings Go accepts, and unreadable input. `recording_conf_matches` against MediaMTX's normalised replies: an empty `recordDeleteAfter`, the defaults' `1d`, and a path with no config already matching "off" | #84 |
| `adapter/test_onvif_events.py` | 36 | `topic_details` and `detection_map` on **cam-02's real `GetEventProperties` reply** (copied from `CAM02-CAPABILITIES.md` §6 into `tests/fixtures/onvif/`), plus another vendor's wording. `_state_field` preference. `parse_notification` with zeep-shaped objects. `state_value`, `is_on` | #68 |
| `adapter/test_onvif_discovery.py` | 32 | `xaddr_host_port`, `_per_kilo`, `_audio`. Basic auth, and digest auth checked against RFC 2617's formula. `_has_login`. `rtsp_login`, with the one RTSP DESCRIBE faked. `stream_audio`'s SDP parsing (rtpmap and static payload types) | #67, #74 |
| `adapter/test_onvif_media2.py` | 38 | `plan_config` (profile, resolution, frame rate, bitrate and GOP moved into the target's range; input left untouched; High preferred for H.264), `fps_choices`, `fps_config` (keyframe interval kept in seconds), `redact_uri`, `same_stream`, `with_credentials`. `stream_profile` and `streams` with a fake camera | (#13's rule) |
| `adapter/test_codec_caps.py` | 18 | `default_codec` (hardware first, tie → H.264), every `unavailable_reason`, `fallback`. `probe` for transcode, stream-only and ONVIF cameras: a camera that claims H264 but sends H.265 gets H.265 | #69 |
| `adapter/test_outage.py` | 22 | `segment_start` reads local time in `Europe/Berlin` (summer and winter), whatever zone the machine is in; mtime fallback. `completed_segments`. `outage_uploader`'s `track_signature`, `video_codec`, `audio_rate`, `chunk_length`, `seg_duration` | #20 |
| `adapter/test_config_and_state.py` | 13 | `config._read_file` (comments, quotes, `=` in values), environment over file, a missing or empty key as an error naming key and file. `aws_state`: write/read round trip, atomic write, and a stale, missing or unreadable heartbeat reading as *unknown*, not *offline* | — |
| `adapter/test_clip_gate.py` | 7 | `ClipGate`: rising edge only, cooldown, suppressed edges not restarting it. **A replay of both recorded cam-02 logs in `measurements/`**, which pins the clip counts `adapter/bin/replay-gate.py` prints today (09-06: 39/42/46; overnight: 687/665/853 for motion/cellMotion/human). COSTS-1.4 §7.2's duty cycle rests on these counts | — |
| `adapter/test_small_helpers.py` | 16 | `sync_mediamtx_paths.redact`, `rematch_cameras.move_url` (only the host moves, even when the password contains the old address), `gap-fill.union_seconds` | #31's rule |
| `cloud/test_lambda_twins.py` | 8 | Every function defined in more than one Lambda has identical code, docstrings aside. Today that is the six clip helpers shared by `record_clip`/`clip_to_s3`, and `unregistered_response` in five Lambdas. A new copy is checked without being listed | — |
| `cloud/test_clip_helpers.py` | 64 | On **both** copies: `utc`, `codec_windows` (split at the switch, not at the edges), `mp4_video_codec` (moov only, never ftyp or mdat; 64-bit and to-end-of-file boxes; malformed input), `mp4_duration_sec` (mvhd v0/v1, rounding, missing or truncated header), `no_fragments`. The MP4s are built box by box, a few bytes each | #45, #54 |
| `cloud/test_lambda_helpers.py` | 12 | `presign_seconds` (a 2 h outage clip can be watched to the end), `list_cameras.video_codec`, `plain` (nested Decimals become JSON), `unregistered_response` | #82 |
| `test_hygiene.py` | 2 | No `/home/` literal in `adapter/`, `cloud/`, `setup/`, `config/` or `mediamtx.yml`, and a check that this list isn't empty | #25 |
| `test_sandbox.py` | 12 | The guards (step 3) | — |

One test was wrong at first, not the code: the 1.1 GB outage-clip case assumed a 6 h presigned
URL, but the formula gives 3.4 h. That is still enough to watch a 2 h clip, which is what
OUTAGE.md 4.6 asks for, and the test now says so.

## Step 6 — proving the tests catch regressions

A test that passes proves little until it has been seen to fail. In a scratch copy of the
repository, each fix below was undone, one at a time, and the matching tests were run:

| Undone in the copy | Result |
|---|---|
| `unit_name()` ignores the channel file (#37) | 3 failed |
| Segment names parsed as UTC (#20) | 2 failed |
| `state_value()` reads only the declared field (#68) | 1 failed |
| `list_cameras.plain()` leaves Decimals (#82) | 1 failed |
| `clip_to_s3.mp4_duration_sec()` truncates instead of rounding (twin drift) | 2 failed (the twin check and the rounding test) |
| `_per_kilo()` passes Hz through (#74) | 3 failed |
| `recordDeleteAfter` compared as the string `"0s"` | 1 failed |
| `ClipGate` fires on every `true`, not on the rising edge | 3 failed (including both replays) |

Every one was caught. The working tree was never changed.

The #84 fix was checked the same way: against the old parser, 18 of the 52 tests in
`test_mediamtx_api.py` fail, `test_ff84_milliseconds_are_not_minutes` among them.

## Step 7 — GitHub Actions (`.github/workflows/tests.yml`)

- **Triggers:** every push, every pull request, and by hand (`workflow_dispatch`). A newer push
  to the same ref cancels the run in progress.
- **Runner:** `ubuntu-24.04` (x86) with Python 3.13, the version on the Pi (Raspberry Pi OS
  trixie) and the Lambdas' runtime. The code under test is plain Python, and every package
  has wheels for both architectures.
- **Steps:** install `requirements.txt` and `requirements-test.txt`, with pip's cache keyed
  on both files, then run `python -m pytest`.
- **Permissions:** `contents: read`. There are no secrets, no AWS credentials and no OIDC role,
  and nothing deploys from CI. The guards make AWS unreachable anyway, and deploys stay
  `cloud/deploy.sh`, run by hand.
- **Action versions:** `actions/checkout@v7` and `actions/setup-python@v7`, the current majors
  (v7 since 2026-07-20).

**Tried before the first push:** CI's conditions were rebuilt on the Pi. A fresh venv was
built from the two requirements files, and the suite ran in a copy of the repository with no
`venv-adapter`, no `certs/` and no `/etc/adapter` config, under `env -i` (only `PATH` and
`HOME` set). Result: 328 passed, 1 xfailed, the same as in the working tree; after the #84
fix, 356 passed. The workflow
itself first runs on GitHub with the push that adds it.

## Step 8 — the documents that changed

- **`CLAUDE.md`**: the "no automated test suite" sentence is replaced. A new section, "Tests
  (pytest)", gives the commands, the guards in brief, and the regression-test rule (below).
  `PYTEST.md` was added to the document list.
- **`README.md`**: `tests/` and `requirements*.txt` added to the repository map, and
  `PYTEST.md` to the documentation table.
- **LAUNCH A4, guide §7.2**: they point to `requirements.txt` instead of listing packages.
- **`FoundAndFixed.md`**: #84, found and then fixed (below).

---

## Phase 2 — behaviour with fakes

Phase 1 tested what functions compute. Phase 2 tests what the code *does* around them: the
decisions the outage supervisor takes over time, the calls made to MediaMTX and the registry,
and every Lambda handler end to end. Everything the code talks to is replaced by a fake that is
installed on the one module under test, never globally.

### P2.1 — the fakes (`tests/fakes.py`, `tests/mp4.py`)

| Fake | Stands in for | How it stays honest |
|---|---|---|
| `ModuleShim` | a module as one module sees it (`time`, `socket`, `os`, `subprocess`) | Only the named attributes change; everything else is the real module, and nothing outside the module under test is touched (pytest's own `time` included) |
| `FakeClock` | `time.monotonic()` | Moves only when told to, so a 60 s grace period takes no time |
| `FakeTable` | a DynamoDB table | Pages scans and queries (with `page_size`, as the 1 MB limit does) and orders queries by the range key. `SET` updates, conditions (`=`, `attribute_[not_]exists`, `AND`) and `ConditionalCheckFailedException` are modelled; anything else raises `NotImplementedError` instead of answering |
| `FakeSession` | `aws_device_creds.get_session()` | Serves the fake `cameras` table to code that reads the registry from the Pi |
| `FakeBoto3` | the `boto3` a Lambda imports | Tables are `FakeTable`s. **Clients are real botocore clients with botocore's own `Stubber`**: every call is checked against the AWS service model and against the parameters the test expects, and is answered from a queue, never sent. A client is built with the Lambda's own arguments (`endpoint_url`), so a presigned URL's host is the Lambda's choice, not the fake's. An answer left unused fails the test |
| `FakeMediaMTX` | MediaMTX's control API, as the `requests` module a module sees | Path configs, runtime state and successive `/v3/paths/list` snapshots. POST on the delete route answers 404, as MediaMTX does (#36). `down=True` refuses every connection |
| `invoke()` | API Gateway calling a Lambda | **Asserts the CORS header on every answer**, errors included. Without it the browser can't read the response, and `fetch()` throws a bare `NetworkError` (#8) |
| `mp4.clip()` | what `GetClip` returns | A tiny MP4 with a real `moov/mvhd` duration and a sample entry for the codec, so the clip rows' `durationSec` and `videoCodec` are read from a file structure |

The fakes are tested too, in `tests/test_fakes.py` (16 tests): pagination, query order,
conditions, the MediaMTX routes, and that an unused queued answer fails. A fake that paged or
checked conditions wrongly would let the tests built on it pass for the wrong reason.

### P2.2 — the fixtures

- **`registry(rows, *modules)`** (in `tests/conftest.py`) serves a `FakeTable` through
  `get_session()`. A module that bound its own `get_session` at import, like `event_watcher`, is
  passed in `modules`.
- **`aws(lambda_module, cameras=…, clips=…)`** (in `tests/cloud/conftest.py`) installs `FakeBoto3`
  on a Lambda, replaces its module-level `cameras_table`, and checks after the test that every
  queued AWS answer was used.

### P2.3 — the tests

| File | Tests | What it covers | Bugs |
|---|---:|---|---|
| `adapter/test_outage_supervisor.py` | 29 | **Connectivity:** offline on an MQTT drop, or on 2 failed probes; a stale heartbeat alone is not an outage; recovery only after 3 good probes in a row, and only if the agent agrees. **ProducerLatch:** a crash-looping unit (`activating`, …) counts as running; disarm only after the grace period. **probe_aws:** the whole probe keeps to its 4 s budget, whatever DNS returns. Stick and disk-floor checks; the registry cache | #22, #23 (#39's design) |
| `adapter/test_mediamtx_api.py` | +21 → 73 | `DELETE`, not POST; add-or-patch; recording on and off; **"flowing" means `bytesReceived` grew**, never `ready`; video codec, picture size, and the audio law read from the wire | #36, #50, #71 |
| `adapter/test_sync_mediamtx_paths.py` | 16 | Adds a missing path, leaves an up-to-date one alone (a patch reconnects readers), **never deletes**, skips transcode cameras, adds the `_live` path. Dry run; no password in the log. Registry → cache (mode 0600, no ONVIF password), cache when AWS is unreachable, nothing without either | #31, #32 |
| `adapter/test_rematch_cameras.py` | 14 | Learns identities, follows a camera to a new address (live-view URL too), never guesses (duplicate identity, address clash, hand-edited URL). Dry run changes nothing. **A Re-register in between is not overwritten** (the conditional write is refused). `main()` end to end: scan → registry → cache → MediaMTX path | — |
| `adapter/test_outage.py` | +8 → 30 | `partition_runs` with `ffprobe` faked: an unreadable segment splits a run and is dropped; a layout change starts a new clip; no empty runs | — |
| `adapter/test_camera_audio.py` | 7 | Audio needs both `audioCapable` and `audioEnabled`. Registry unreachable or wrong usage → `AUDIO=off`, exit 0 | — |
| `adapter/test_codec_caps.py` | +6 → 24 | `refresh`: writes only what changed; an unreachable camera keeps the caps it has; a new one gets the fallback; one bad row doesn't stop the others; dry run | — |
| `adapter/test_unregistered_readers.py` | 5 | **Every adapter reader of the `cameras` table skips an unregistered row:** the outage supervisor, the path sync, the event watcher, `codec_caps`. A new reader belongs here | — |
| `cloud/test_command_lambdas.py` | 64 | `publish-cmd` (each action onto the agent's topic; 400/409 cases; Stop still works for an unregistered camera). `set-camera-mode` (**exactly the four shared `recordingMode` values**; ONVIF and event-list checks; only that one field written). `set-camera-audio`, `set-camera-outage-buffer` (**its limits equal the supervisor's**) | #8, #65 |
| `cloud/test_list_cameras.py` | 6 | **Never returns `rtspUrl`, `liveRtspUrl` or ONVIF credentials** (a unique secret must not appear anywhere in the body). Unregistered cameras left out; every scan page read; Decimals; the fields the page uses | #82 |
| `cloud/test_clip_lambdas.py` | 40 | `list-clips` paging: pages, a page past the end, `last`, 120 clips over two 1 MB pages; validation. `play-clip` (presigned URL on the regional endpoint, lifetime from size; Deep Archive restore). `delete-clip`, `set-clip-tier` | #51 |
| `cloud/test_kvs_lambdas.py` | 26 | `get-hls-url` (`Expires` 3600, fMP4, the `-live` stream, `since` replay and its clamps, 503 for no fragments). `record-clip` and `clip-to-s3`: the exact `GetClip` windows, keys, and **clip rows with the six contract fields**; codec-switch splits, one empty side, no footage → 503, or an exception for the IoT rule | #14, #45, #54, #66 |
| `test_fakes.py` | 16 | The fakes (P2.1) | — |

### P2.4 — proving the new tests catch regressions

As in step 6, each defect below was put back into a scratch copy, one at a time, and the
matching test file was run:

| Put back | Result |
|---|---|
| Recovery after one good probe (#22) | 3 failed |
| Probe timeout per address, no overall budget (#23) | 1 failed |
| Delete a path with POST (#36) | 1 failed |
| A `ready` path counts as flowing (#50) | 2 failed |
| record-clip's no-footage 503 made dead code (#45) | 2 failed |
| publish-cmd's 500 without the CORS header (#8) | 2 failed |
| list-clips paging ignores the offset (#51) | 5 failed |
| list-cameras returns `rtspUrl` | 2 failed |
| Path sync patches an up-to-date path | 1 failed |
| Rematch writes without its condition | 2 failed |
| The event watcher reads unregistered cameras | 1 failed |
| clip-to-s3's split clips share one key | 1 failed |
| Audio on without `audioCapable` | 1 failed |
| The outage-buffer Lambda accepts a boolean | **first 0 failed**, then 1 |

The last row found a gap in the tests, not in the code. `true` was refused even without the
boolean check, because it equals 1, which isn't a valid limit. `false` equals 0, a valid limit
("off"), and it is the value the check exists for. The test didn't include `false`; now it does.

### P2.5 — what else Phase 2 showed

- **No defect in the code under test.** The two tests that failed at first were wrong, not the
  code:
  - A presigned-URL test checked the host of a client the fake had built without the Lambda's
    `endpoint_url`. The fake now builds clients with the Lambda's own arguments.
  - `record-clip` with equal start and end times isn't refused: the 2 s padding on both ends
    makes a 4 s window. That behaviour is now pinned by a test.
- **CI rehearsal** (as in step 7, fresh venv and stripped copy): 614 passed.

## Found while writing the tests, and fixed

**#84: `mediamtx_api._dur_secs("500ms")` returned 30 000 s.** It parsed one character at a
time, so the `m` of `ms` meant minutes. The bug was latent: only `recordDeleteAfter` and
`recordSegmentDuration` are compared, and for an armed path MediaMTX reports both in whole
units. It was first pinned with a strict `xfail` test, then fixed the same day at the user's
request.

- **The fix:** the parser reads term by term, as Go does, and knows MediaMTX's `d` unit. The
  live path defaults report `recordDeleteAfter: "1d"`, which the old parser couldn't read.
- **Checked offline:** 52 tests, 18 of which fail on the old parser.
- **Checked against the live MediaMTX, read-only:** every duration it reports parses
  correctly. `recording_conf_matches` gives the same answer from both parsers for every path,
  whether off or armed is wanted. So the fix changes no decision the outage supervisor makes
  today.
- **Not done:** `kvs-outage-buffer` keeps running the old code until its next restart
  (`systemctl --user restart kvs-outage-buffer`). Nothing is urgent, since its decisions
  don't change.

---

## Conventions for new tests

- **One file per module**, in `tests/adapter/` or `tests/cloud/`. Each file's docstring says
  what it covers and cites `PYTEST.md`.
- **A bug fixed in Python logic gets a regression test**, `test_ffNN_<what>`, in the same
  change as its `FoundAndFixed.md` entry. Check that the test fails without the fix (step 6
  shows how).
- **A known, unfixed defect** gets a strict `xfail` test whose reason cites its number.
- **Fake at the boundary, never loosen a guard.** Use the fakes in `tests/fakes.py` (P2.1)
  and their fixtures: `fake_run` for commands, `registry` for the `cameras` table on the Pi,
  `aws` for a Lambda (queue client answers on its `Stubber`), and `FakeMediaMTX` for MediaMTX.
  For RTSP or the camera, `monkeypatch.setattr(module, "function", …)`. A fake that has to
  model something new gets a test in `test_fakes.py`.
- **Call API Lambdas through `invoke()`**, which checks the CORS header on every answer (#8).
- **Never compare against values that only exist on this Pi** (a real camera address, the
  real Thing name). CI has none of them, and the guards hide them anyway.
- **Fixtures from real hardware** go in `tests/fixtures/`, with credentials removed, and their
  source named in the test's docstring (as `cam02-GetEventProperties.xml` is).

## What the suite deliberately doesn't cover

- **Media.** GStreamer pipelines, kvssink and anything a browser has to decode. CLAUDE.md's
  rule stands: decode a frame and look at it, and "ffmpeg played it" is not enough. The tests
  check the logic around the media, never the media itself.
- **The shell scripts** (`adapter/bin/*.sh`, `setup/*.sh`). A shell test framework (bats)
  would be a separate decision.
- **`client/index.html`**, which is JavaScript.
- **AWS itself.** IAM, the deployed Lambdas, IoT rules. `cloud/deploy.sh status` and
  LAUNCH Part C still verify those.

---

## Next phases

**Phase 3 — the admin GUI routes.** Flask's `test_client()`, with the registry (`registry`,
passing `app` as a module), MediaMTX (`FakeMediaMTX`), `systemctl` (`fake_run`) and
`provision-camera.sh` faked. It covers camera ID validation, 409 for an
unregistered camera, cam-01/cam-02 never re-provisioned, and Unregister disabling the unit
(#80). `app.py` is 1,380 lines, so this is the largest phase.

**Phase 4 — `agent.py` (D2).** Move the connection block at the end of the file into `main()`,
behind `if __name__ == "__main__"`. The service behaves the same, but `on_message` and
`set_live` (the deadline file, unit names, #7) become testable. Verify on the Pi afterwards:
the agent connects, Start/Stop works from the cloud page, and the heartbeat file updates.
`observe_state.py` gets the same change if it is worth testing.

**Later, optional:** `cloud/deploy.sh lambda` could run `tests/cloud` before it uploads.
