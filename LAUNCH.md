# LAUNCH.md — Cloud Adapter operational runbook

Companion to `Demo-AWS-Video-revCosts4.md` (the narrative build guide). This file is the
short version: what to run to actually get the system up, after everything in the guide
has already been built once. If something here doesn't work, the guide explains why it
is built this way (search it for the section number), and `FoundAndFixed.md` has every
bug found so far, cited here as `#N`.

**Current live values for this deployment** (Account `596633517506`, region
`eu-central-1`) are baked into the commands below. If you rebuild this from scratch on a
different AWS account, every ID here changes — see the guide's §1–§8 for how each one is
created.

---

## Part A — One-time setup

Skip what's already done. On a Pi that has been set up before, this lists what's
missing and which step creates it (needs A2's environment):

```bash
for s in "A3 $KVS_SDK/build/libgstkvssink.so" \
         "A4 $VMS_HOME/venv-adapter/bin/python3" \
         "A5 $VMS_HOME/mediamtx/mediamtx" \
         "A7 /etc/adapter/adapter.env" \
         "A7 $VMS_HOME/certs/adapter.private.key" \
         "A8 $HOME/.config/systemd/user/kvs-agent.service" \
         "A10 /mnt/vms-buffer/.vms-buffer-ok"; do             # A10 is optional
  set -- $s; [ -e "$2" ] && echo "ok       $1  $2" || echo "MISSING  $1  $2"
done
command -v aws >/dev/null && echo "ok       A6  aws CLI" || echo "MISSING  A6  aws CLI"
```

If everything is there, Part A is done: run **Part B** once if the units aren't enabled
yet, otherwise just verify with **Part C** — enabled units start at every boot by
themselves. If the repo was cloned to a new folder, re-run **A8** (then Part B) so the
unit files point at it. A fresh Pi runs
A1 → A10 in order (A10 only if you want outage buffering), except that A3's build runs
for hours in the background, so A4–A7 fit
inside it.

### A1. System stability hardening (§1.4) — do this before anything else

```bash
sudo apt install -y earlyoom
sudo tee /etc/default/earlyoom > /dev/null <<'EOF'
EARLYOOM_ARGS="-r 60 -m 20 -s 95 --avoid '(^|/)(sshd|systemd|systemd-.*|init)$' --prefer '(^|/)(cc1plus|cc1|g\+\+|gcc|cpp|as|ld|make|cmake)$'"
EOF
sudo systemctl enable --now earlyoom

sudo fallocate -l 3G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile
sudo swapon -p 10 /swapfile
echo "/swapfile none swap sw,pri=10 0 0" | sudo tee -a /etc/fstab
echo "vm.swappiness=10" | sudo tee /etc/sysctl.d/99-low-swappiness.conf
sudo sysctl -p /etc/sysctl.d/99-low-swappiness.conf

# Persistent journal (FoundAndFixed.md #33). Pi OS keeps it in RAM, so every `journalctl
# --user -u` check in this file finds nothing. Its own drop-in sets Storage=volatile, so
# creating /var/log/journal alone does nothing: a later /etc drop-in has to override it.
# The size caps keep the SD-card cost bounded.
sudo mkdir -p /etc/systemd/journald.conf.d
sudo tee /etc/systemd/journald.conf.d/90-vms-persistent.conf > /dev/null <<'EOF'
[Journal]
Storage=persistent
SystemMaxUse=200M
SystemMaxFileSize=20M
EOF
sudo systemctl restart systemd-journald
sudo journalctl --flush        # move what's in RAM so far into /var/log/journal

sudo rpi-eeprom-update -a && sudo reboot   # only if an update is actually staged
```

**Proof (journal):**

```bash
systemd-analyze cat-config systemd/journald.conf | grep '^Storage='   # last line: Storage=persistent
ls /var/log/journal/*/                 # system.journal, and user-1000.journal once a user unit logs
journalctl --user -n 1                 # a log line, not "No journal files were found"
journalctl --list-boots | tail -2      # after the next reboot: two boots, the older one still readable
```

Until then, `journalctl _SYSTEMD_USER_UNIT=<unit>.service` reads a user unit's log.

### A2. Environment — persisted to `~/.bashrc`

```bash
cat >> ~/.bashrc <<'EOF'
export VMS_HOME=$HOME/Projects/VideoSafeZone   # wherever you cloned the repo
export KVS_SDK=$VMS_HOME/vendor/amazon-kinesis-video-streams-producer-sdk-cpp
export GST_PLUGIN_PATH=$KVS_SDK/build
export LD_LIBRARY_PATH=$KVS_SDK/open-source/local/lib:$LD_LIBRARY_PATH
export AWS_REGION=eu-central-1
export KVS_STREAM=cam-01
export THING_NAME=adapter-01
EOF
source ~/.bashrc
```

**Proof**, in a **new** terminal (or after `source ~/.bashrc` in the current one):
`echo "$VMS_HOME" && ls "$VMS_HOME/LAUNCH.md"` prints the path, then the file. Once A3
has built the SDK, `gst-inspect-1.0 kvssink` also finds the plugin with no further setup.

These exports reach **interactive shells only**: Raspberry Pi OS's `~/.bashrc` begins
with `case $- in *i*) ;; *) return;; esac`, so a script that runs `source ~/.bashrc`
gets nothing, and neither does any systemd unit.

**Non-interactive shells (systemd units, this file's own scripts) do NOT source
`.bashrc`** — every KVS producer unit in A8 sets `GST_PLUGIN_PATH`/`LD_LIBRARY_PATH`
explicitly for this reason (§4.3). `VMS_HOME` itself is the exception: the adapter's
scripts (`adapter/bin/*.sh`) and Python modules (`adapter/*.py`) use it if exported and
otherwise fall back to the repo root they live in (FoundAndFixed.md #25), so certs, venv and helper paths resolve
without it. Unit files are different — they need **literal** absolute paths; see A8.

### A3. Build the KVS Producer SDK (§4) — the long step, budget 1.5–2.5h

Start this first: it runs detached for hours, and A4–A7 can be done while it builds.

```bash
sudo apt install -y cmake m4 git build-essential pkg-config \
  libssl-dev libcurl4-openssl-dev liblog4cplus-dev \
  gstreamer1.0-plugins-base-apps gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-good gstreamer1.0-plugins-ugly \
  gstreamer1.0-tools libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
  gstreamer1.0-rtsp v4l-utils
# gstreamer1.0-rtsp provides rtspclientsink, which publish-cam01.sh needs to publish into
# MediaMTX -- without it cam-01 never appears (FoundAndFixed.md #40). v4l-utils: v4l2-ctl.
# gstreamer1.0-omx-generic from the original guide text does not exist on
# current Debian trixie — already dropped from this list.

mkdir -p "$VMS_HOME/vendor" && cd "$VMS_HOME/vendor"
[ -d amazon-kinesis-video-streams-producer-sdk-cpp ] || \
  git clone https://github.com/awslabs/amazon-kinesis-video-streams-producer-sdk-cpp.git
mkdir -p "$KVS_SDK/build"
```

**Three source patches** (FoundAndFixed.md #2, #3, #4). Skipping 1 or 2 is not a slow
build, it is a failed one. If `build.log` shows a burst of `cc: fatal error: Terminated
signal terminated program cc1`, earlyoom killed a parallel OpenSSL compile: a patch is
missing, and nothing is wrong with the code.

Patches 1 and 2 — before anything is built:

```bash
P=$KVS_SDK/dependency/libkvscproducer/kvscproducer-src
# 1. nested build_dependency() hardcodes `--parallel`, which -DPARALLEL_BUILD=OFF never reaches
sed -i 's/--build \. --parallel/--build ./' "$P/CMake/Utilities.cmake"
# 2. stop OpenSSL fetching its huge fuzzing/test submodules (boringssl & co.)
grep -q GIT_SUBMODULES "$P/CMake/Dependencies/libopenssl-CMakeLists.txt" || \
  sed -i '/GIT_TAG *OpenSSL_1_1_1t/a\    GIT_SUBMODULES    ""' "$P/CMake/Dependencies/libopenssl-CMakeLists.txt"
```

**Proof:**

```bash
grep -c -- '--parallel' "$P/CMake/Utilities.cmake"                           # 0
grep -n GIT_SUBMODULES "$P/CMake/Dependencies/libopenssl-CMakeLists.txt"     # one line, right after GIT_TAG
```

**Stage 1 — configure.** With `-DBUILD_DEPENDENCIES=ON` the *configure* step is what
compiles log4cplus, OpenSSL, curl etc., and it is also what downloads the kvspic source
that patch 3 edits — so configure alone first, patch, then compile:

```bash
loginctl enable-linger "$USER"   # one-time; lets this survive a lost SSH/VS Code session
cd "$KVS_SDK/build"
systemd-run --user --unit=kvs-build --collect --working-directory="$PWD" \
  taskset -c 1,2 bash -c 'cmake .. -DBUILD_GSTREAMER_PLUGIN=ON -DBUILD_DEPENDENCIES=ON \
    -DPARALLEL_BUILD=OFF -DCMAKE_BUILD_TYPE=Release > build.log 2>&1; \
    echo "CONFIGURE_EXIT=$?" >> build.log'
# check on it: systemctl --user status kvs-build ; tail -f build.log
```

**Proof:**

```bash
grep CONFIGURE_EXIT build.log                                      # CONFIGURE_EXIT=0
journalctl -u earlyoom --since today | grep -c 'sending SIGTERM'   # 0 — any kill means a patch is missing
```

Patch 3 — GCC 14 makes the SDK's implicit `pthread_getname_np` declaration a hard error.
The file exists only now:

```bash
F=$P/dependency/libkvspic/kvspic-src/CMakeLists.txt
grep -n 'project(\|add_definitions' "$F" | head
```

Add, by hand, straight after the `SDK_VERSION`/`DETECTED_GIT_HASH` `add_definitions()`
lines (not inside a multi-line `project(...)` call):

```cmake
if(UNIX AND NOT APPLE)
  add_definitions(-D_GNU_SOURCE)
endif()
```

**Proof:** `grep -n -A1 'UNIX AND NOT APPLE' "$F"` shows the block where you put it.

**Stage 2 — compile:**

```bash
cd "$KVS_SDK/build"
systemd-run --user --unit=kvs-build --collect --working-directory="$PWD" \
  taskset -c 1,2 bash -c 'make -j1 >> build.log 2>&1; echo "EXIT_CODE=$?" >> build.log'
```

**Proof** (each line is stronger evidence than the one before):

```bash
grep EXIT_CODE build.log                 # EXIT_CODE=0
ls -la "$KVS_SDK/build/libgstkvssink.so" # the plugin exists
gst-inspect-1.0 kvssink | head -5        # GStreamer loads it — needs A2's env vars
gst-inspect-1.0 rtspclientsink | head -3 # cam-01's publisher element (gstreamer1.0-rtsp)
```

**Retrying after a failure: don't wipe `build/`.** Finished dependencies install to
`open-source/local/` (next to `build/`, not inside it) and are skipped on the next run, so
fix the cause and re-run the stage that failed. Only delete `open-source/local/lib<name>`
if one dependency is stuck half-built (§4.2).

### A4. Python venv (§7.2)

```bash
python3 -m venv "$VMS_HOME/venv-adapter"
"$VMS_HOME/venv-adapter/bin/pip" install boto3 awsiotsdk onvif-zeep-async WSDiscovery flask requests lxml
```

That is every third-party module `adapter/` imports (the list was once incomplete, FoundAndFixed.md #27): `boto3` (AWS APIs), `awsiotsdk`
(MQTT, `agent.py`), `onvif-zeep-async` + `lxml` (ONVIF), `WSDiscovery` (LAN discovery),
`flask` + `requests` (admin GUI).

**Proof** — imports, and the WSDL path the ONVIF code computes, resolved with `VMS_HOME`
*unset*, the way systemd runs it:

```bash
cd "$VMS_HOME"
venv-adapter/bin/python3 -c "import boto3, awsiot, awscrt, flask, requests, lxml, onvif, wsdiscovery; print('imports OK')"
env -u VMS_HOME venv-adapter/bin/python3 -c "
import sys, os; sys.path.insert(0, 'adapter'); import camera_control as c
print(c.WSDL_DIR, os.path.isdir(c.WSDL_DIR))"          # must end in True
```

### A5. MediaMTX binary (§2.5)

The repo tracks the project's own `mediamtx/mediamtx.yml`; only the binary is downloaded.
**The release tarball also contains a default `mediamtx.yml`**, and a plain `tar xzf`
silently overwrites the project's (FoundAndFixed.md #26), so extract the binary by name:

```bash
cd "$VMS_HOME/mediamtx"
sha256sum mediamtx.yml > /tmp/mediamtx.yml.sha256      # to prove extraction leaves it alone
curl -fL -o mediamtx.tar.gz \
  https://github.com/bluenviron/mediamtx/releases/download/v1.20.1/mediamtx_v1.20.1_linux_arm64.tar.gz
tar xzf mediamtx.tar.gz mediamtx
```

`-f` makes a 404 fail loudly instead of saving a 9-byte "Not Found" as the tarball (the
§2.5 trap). v1.20.1 is the version `mediamtx.yml` was written against — upgrade on purpose,
not by accident.

**Proof:**

```bash
file mediamtx                                    # ELF 64-bit LSB executable, ARM aarch64
./mediamtx --version                             # v1.20.1
sha256sum -c /tmp/mediamtx.yml.sha256            # mediamtx.yml: OK  (the tarball ships its own)
git -C "$VMS_HOME" status --short --ignored mediamtx/   # binary, tarball, and after the first
                                                        # start auto.crt/auto.key: all '!!' (ignored, FoundAndFixed.md #35)
# starts with the project config and the control API answers
# (skip if kvs-mediamtx is already running — the ports would clash):
( timeout 6 ./mediamtx >/dev/null 2>&1 & sleep 3; curl -s http://127.0.0.1:9997/v3/paths/list | head -c 200; echo )
```

### A6. AWS CLI — the operator's tool, not the adapter's

Used on the Pi by this runbook (Part B's `aws iot-data publish`, Part C's cloud-path check,
creating a device certificate in A7) and by the deploy commands in `CLAUDE.md`. **The
adapter's services never use it** — they authenticate with the device certificate (A7).
Not in the Raspberry Pi OS image. Install AWS's own arm64 build **per user** — no sudo
needed, and it is only ever run by you. Verify its signature first: the download is 70 MB
of code that will hold your AWS credentials.

```bash
cd "$(mktemp -d)"
curl -fsSL -o awscliv2.zip https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip
curl -fsSL -o awscliv2.sig https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip.sig
export GNUPGHOME="$PWD/gnupg"; mkdir -m 700 "$GNUPGHOME"      # throwaway keyring
gpg --keyserver hkps://keyserver.ubuntu.com --recv-keys FB5DB77FD5C118B80511ADA8A6310ACC4672475C
gpg --verify awscliv2.sig awscliv2.zip    # must say: Good signature from "AWS CLI Team"
unzip -q awscliv2.zip && ./aws/install -i "$HOME/.local/aws-cli" -b "$HOME/.local/bin"

# ~/.profile adds ~/.local/bin only if it existed at login -- make every new shell see it:
grep -q 'HOME/.local/bin' ~/.bashrc || cat >> ~/.bashrc <<'EOF'
# ~/.local/bin (per-user AWS CLI, LAUNCH.md A6): ~/.profile only adds it if it existed at login
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac
EOF
```

Then in a **new** terminal: `aws --version` → `aws-cli/2.x … exe/aarch64`. Upgrading later:
the same commands with `./aws/install -i "$HOME/.local/aws-cli" -b "$HOME/.local/bin" --update`.
(System-wide instead: `sudo ./aws/install`, no `-i`/`-b`, no `.bashrc` line.)

`gpg` may add `[expired]` / "This key has expired": AWS extends this key's expiry
periodically and a keyserver's copy can lag. What matters is **Good signature** and the
fingerprint `FB5D B77F D5C1 18B8 0511 ADA8 A631 0ACC 4672 475C`; a `BAD signature` means
don't install.

**Credentials — pick one:**

- **`aws login --remote`** — recommended. Signs in with your normal AWS Console login
  and issues short-lived credentials, so no IAM Identity Center setup and no long-lived
  key on the Pi. `--remote` prints a URL to open on any device with a browser; the Pi
  over SSH has none. Re-run it when the session expires.
- **`aws configure sso`** (IAM Identity Center), if the account already uses it.
  Also short-lived, renewed by `aws sso login`.
- **`aws configure`** with an IAM user's access keys — works, but writes a static key to
  `~/.aws/credentials`: exactly the kind of credential this architecture keeps off the
  device. If you use it, give that user only what you run from here and delete the key
  when setup is done.
- **No credentials on the Pi at all** — run the account-level commands in **AWS
  CloudShell** (browser, already authenticated) and skip the credentials step here. The
  cloud-path check in Part C and `aws iot-data publish` in Part B then need CloudShell too.

Set the region either way: `aws configure set region eu-central-1`.

**Proof:**

```bash
aws sts get-caller-identity --query Account --output text   # 596633517506
aws configure get region                                     # eu-central-1
```

### A7. AWS resources and the device certificate

**AWS resources (§3, §6, §8) — once per AWS account.** Already created for this account —
see `$VMS_HOME/cloud/` for every JSON policy document used. In order: KVS streams (`cam-01`,
`cam-02`, 24h retention) → IAM role `KVSAdapterRole` + role alias `KVSAdapterRoleAlias` →
IoT Thing `adapter-01` + X.509 cert + `KVSAdapterThingPolicy` → DynamoDB tables `cameras`
and `clips` → Cognito user pool `kvs-demo-users` → Lambdas (one file each in
`cloud/lambda/`) → API Gateway `kvs-demo-api` → S3 bucket `vms-demo-client-596633517506`
behind CloudFront. Full commands for each are in the guide's §3/§6/§8/§9 — do not re-run
them against this account, they'd fail on "already exists."

**Deployment config — once per Pi.** Which region, IoT Thing, role alias, endpoints and
evidence bucket this adapter uses lives in one machine-wide file, `/etc/adapter/adapter.env`,
read by `adapter/config.py` (Python) and `adapter/bin/adapter-config.sh` (the producer
scripts). There are no built-in defaults: without the file every service exits at startup
with `<KEY> is not set: add it to /etc/adapter/adapter.env`. The repo's template already
carries this account's values:

```bash
sudo install -D -m 644 "$VMS_HOME/config/adapter.env.example" /etc/adapter/adapter.env
# different AWS account or Thing: edit the file — the lookup commands are in its header
```

Environment variables override the file. A2 exports `AWS_REGION` and `THING_NAME`, so an
interactive shell uses *those* while systemd services (no `.bashrc`) use the file — keep
them equal, or a manual run and the service will quietly talk to different things.

**Proof** — both loaders read it, and the same values come out:

```bash
bash -c 'source "$VMS_HOME/adapter/bin/adapter-config.sh" && echo "shell:  $THING_NAME $AWS_REGION $IOT_DATA_ENDPOINT"'
env -u AWS_REGION -u THING_NAME "$VMS_HOME/venv-adapter/bin/python3" -c "
import sys; sys.path.insert(0, '$VMS_HOME/adapter'); import config
print('python:', config.THING_NAME, config.AWS_REGION, config.IOT_DATA_ENDPOINT)"
```

Proofs (b) and (c) below then read their endpoints from this file, so they also confirm
the file points at the right AWS account.

**Device certificate — once per Pi.** `certs/` is gitignored, so a fresh clone has none.
Four files must end up in `$VMS_HOME/certs/`:

| File | What it is | Used by |
|---|---|---|
| `adapter.cert.pem` | device certificate for Thing `adapter-01` | everything |
| `adapter.private.key` | its private key — **cannot be re-downloaded from AWS** | everything |
| `cacert.pem` | Starfield root (`SFSRootCAG2`) — credentials endpoint | `kvssink`, `aws_device_creds.py` |
| `AmazonRootCA1.pem` | Amazon root CA 1 — MQTT data endpoint (FoundAndFixed.md #29) | `agent.py` |

The two CAs are **different** and not interchangeable; the wrong one gives a TLS error
that looks like a permissions problem (§6.4).

**Option A — copy from the previous Pi** (if it still exists; nothing changes in AWS):

```bash
scp -r <old-pi>:<old-VMS_HOME>/certs "$VMS_HOME/"
```

Never run `kvs-agent` on both Pis at the same time: both connect as MQTT client
`adapter-01`, and IoT Core drops the older session each time the other connects.

**Option B — create a new certificate** (old Pi gone, or you want a clean identity). Run
on the Pi with A6's CLI, or in CloudShell and then copy the two files to the Pi:

```bash
umask 077                                  # the private key is born 0600, not chmod-ed later
mkdir -p "$VMS_HOME/certs" && cd "$VMS_HOME/certs"
aws iot list-policies --query 'policies[].policyName'     # confirm KVSAdapterThingPolicy exists
CERT_ARN=$(aws iot create-keys-and-certificate --set-as-active \
  --certificate-pem-outfile adapter.cert.pem \
  --public-key-outfile adapter.public.key \
  --private-key-outfile adapter.private.key \
  --query certificateArn --output text)
echo "$CERT_ARN" > .cert-arn               # which Thing principal is THIS Pi's (retire step)
aws iot attach-policy --policy-name KVSAdapterThingPolicy --target "$CERT_ARN"
aws iot attach-thing-principal --thing-name adapter-01 --principal "$CERT_ARN"
```

Once the proofs below pass, retire the old certificate so there's only one live identity.
Deactivating it cuts off any device still using it, which is the point if the old Pi is
gone, and a surprise if it's still running:

```bash
aws iot list-thing-principals --thing-name adapter-01    # old = the one not in certs/.cert-arn
aws iot update-certificate --certificate-id <old-cert-id> --new-status INACTIVE
```

**Both options — CAs and permissions:**

```bash
cd "$VMS_HOME/certs"
curl -fsS -o cacert.pem        https://www.amazontrust.com/repository/SFSRootCAG2.pem
curl -fsS -o AmazonRootCA1.pem https://www.amazontrust.com/repository/AmazonRootCA1.pem
chmod 700 . && chmod 600 adapter.private.key
```

**Proof** — four layers; (b) is the one that matters most:

```bash
cd "$VMS_HOME/certs"
source "$VMS_HOME/adapter/bin/adapter-config.sh"   # endpoints, Thing, role alias from adapter.env
# a) cert is valid and belongs to this key
openssl x509 -in adapter.cert.pem -noout -enddate
diff <(openssl x509 -in adapter.cert.pem -noout -pubkey) \
     <(openssl pkey -in adapter.private.key -pubout) && echo "key matches"

# b) credentials endpoint (kvssink, boto3): cert active + attached to adapter-01 + allowed to
#    assume KVSAdapterRole. Prints only the expiry, never the secret. 403 = attach step missing.
curl -fsS --cert adapter.cert.pem --key adapter.private.key --cacert cacert.pem \
  -H "x-amzn-iot-thingname: $THING_NAME" \
  "https://$IOT_CRED_ENDPOINT/role-aliases/$IOT_ROLE_ALIAS/credentials" \
  | python3 -c 'import json,sys; print("credentials OK, expire", json.load(sys.stdin)["credentials"]["expiration"])'

# c) MQTT data endpoint (agent.py), with the other CA — TLS handshake only, no MQTT session
openssl s_client -connect "$IOT_DATA_ENDPOINT:8443" \
  -CAfile AmazonRootCA1.pem -cert adapter.cert.pem -key adapter.private.key </dev/null 2>/dev/null \
  | grep 'Verify return code'            # Verify return code: 0 (ok)

# d) end to end through the project's own code (needs A4), VMS_HOME unset like under systemd
cd "$VMS_HOME" && env -u VMS_HOME venv-adapter/bin/python3 -c "
import sys; sys.path.insert(0, 'adapter'); from aws_device_creds import get_session
print(get_session().client('sts').get_caller_identity()['Arn'])"   # …assumed-role/KVSAdapterRole/…
```

### A8. Install the systemd units — once per Pi, again whenever the clone moves

Part B only *enables and starts* units; this step creates their files (FoundAndFixed.md #30). Unit files live
outside the repo and are not in git, so a fresh Pi (or a fresh clone) has none of them
until this step runs.

**Prerequisites — the units point at these, and none of them come with `git clone`:**
the built SDK (A3), the venv (A4), the MediaMTX binary (A5) and the device certificate
(A7), each with its proof passing. Writing the units first is harmless, but a unit whose
target is missing just fails and retries every 5 s until it appears.

**Two managers, two directories.** Which one a unit belongs to decides where its file
goes, which `systemctl` flavour controls it, and where its logs are:

| Unit | Manager | File | Runs |
|---|---|---|---|
| `kvs-camera-init` | user | `~/.config/systemd/user/kvs-camera-init.service` | `adapter/bin/camera-init.sh` (one-shot) |
| `kvs-mediamtx` | user | `~/.config/systemd/user/kvs-mediamtx.service` | `mediamtx/mediamtx` |
| `kvs-camera-publish` | user | `~/.config/systemd/user/kvs-camera-publish.service` | `adapter/bin/publish-cam01.sh` |
| `kvs-agent` | user | `~/.config/systemd/user/kvs-agent.service` | `adapter/agent.py` |
| `onvif-admin` | user | `~/.config/systemd/user/onvif-admin.service` | `adapter/onvif-admin/app.py` |
| `kvs-event-watcher` | user | `~/.config/systemd/user/kvs-event-watcher.service` | `adapter/event_watcher.py` |
| `kvs-outage-buffer` | user | `~/.config/systemd/user/kvs-outage-buffer.service` | `adapter/outage_buffer.py` |
| `kvs-outage-uploader` | user | `~/.config/systemd/user/kvs-outage-uploader.service` | `adapter/outage_uploader.py` |
| `kvs-camera-rematch` | user | `~/.config/systemd/user/kvs-camera-rematch.{service,timer}` | `adapter/rematch_cameras.py`, every 5 min (E3) |
| `kvs-cam01` | **system** | `/etc/systemd/system/kvs-cam01.service` | `adapter/bin/stream-cam01.sh` |
| `kvs-cam02` | **system** | `/etc/systemd/system/kvs-cam02.service` | `adapter/bin/stream-cam02.sh` |
| `kvs-cam@` | **system** | `/etc/systemd/system/kvs-cam@.service` (+ `/etc/adapter/channels/<path>.env` per instance) | `adapter/bin/stream-channel.sh` |

- **user** → `systemctl --user …`, no sudo, logs in `journalctl --user -u <unit>`. Starts
  at boot without a login only because of `loginctl enable-linger` (A3).
- **system** → `sudo systemctl …`, logs in `journalctl -u <unit>`. These are the KVS
  producers — the units that cost money while running (guide §1.2), so they are installed
  but **not enabled**; the agent/GUIs start them on demand. `kvs-cam@<path>` instances are
  created by the ONVIF admin GUI through `adapter/bin/provision-camera.sh` (Part E), never
  by hand.
- A unit in one manager cannot `Requires=`/`After=` a unit in the other — they are separate
  systemd instances (FoundAndFixed.md #12).

**systemd does not expand `$VMS_HOME`** — or `~`, or any shell variable. It never reads
`.bashrc`, and `ExecStart=`, `WorkingDirectory=`, `Environment=` and `EnvironmentFile=` are
taken literally, so `ExecStart=$VMS_HOME/adapter/bin/stream-cam01.sh` fails with
"Executable path is not absolute". Every path in a unit file must be the real absolute
path, e.g. `/home/vladimir/Projects/VideoSafeZone/adapter/bin/stream-cam01.sh`. (The
scripts and Python modules the units launch don't need `VMS_HOME` in their environment —
they find the repo root from their own location, see A2.)

The commands below handle that for you: the heredocs are **unquoted** (`<<EOF`, not
`<<'EOF'` as in the guide), so bash substitutes `${VMS_HOME}` and `${USER}` *while writing
the file*, and the unit on disk contains the literal path. Run them from a shell where A2's
`VMS_HOME` is set, and confirm first:

```bash
echo "$VMS_HOME"; ls "$VMS_HOME/adapter/agent.py"   # must print the path, then the file
```

User units — the first three are the guide's §2.8 units, plus `kvs-mediamtx`'s
`ExecStartPost=` path sync (#32); the other six (five Python daemons and the rematch
timer's pair) were never written down in the guide and are reconstructed from the code —
entry points, working directories, imports (#30). **This step, not the guide, is the
authoritative source for unit files.**

```bash
mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/kvs-camera-init.service <<EOF
[Unit]
Description=Lock PW310 exposure/WB/focus before streaming starts
Before=kvs-mediamtx.service

[Service]
Type=oneshot
ExecStart=${VMS_HOME}/adapter/bin/camera-init.sh
RemainAfterExit=yes
# retry if the camera wasn't there yet (detection itself also waits 30 s, #42)
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

cat > ~/.config/systemd/user/kvs-mediamtx.service <<EOF
[Unit]
Description=MediaMTX RTSP/HLS server for camera capture
After=network.target

[Service]
Type=simple
WorkingDirectory=${VMS_HOME}/mediamtx
ExecStart=${VMS_HOME}/mediamtx/mediamtx
# Network cameras' paths come from the registry after every start (mediamtx.yml has none):
# '-' so an unreachable registry *and* no cache can't stop MediaMTX itself.
ExecStartPost=-${VMS_HOME}/venv-adapter/bin/python3 ${VMS_HOME}/adapter/sync_mediamtx_paths.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

cat > ~/.config/systemd/user/kvs-camera-publish.service <<EOF
[Unit]
Description=PW310 capture/encode -> publish to MediaMTX (rtsp://127.0.0.1:8554/cam01)
After=kvs-camera-init.service kvs-mediamtx.service
# Wants, not Requires, on init: a failed exposure lock must not block the video for good
# -- a dependency failure is never retried (#42). The publisher detects the camera itself.
Wants=kvs-camera-init.service
Requires=kvs-mediamtx.service

[Service]
Type=simple
WorkingDirectory=${VMS_HOME}/adapter/bin
ExecStart=${VMS_HOME}/adapter/bin/publish-cam01.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

# Python daemons: one template, five units. All run under the venv's interpreter
# (a bare python3 wouldn't see awsiotsdk/boto3/onvif), from the directory they live in.
py_unit() {  # name  description  working-dir  script  [extra [Unit] lines]
cat > ~/.config/systemd/user/$1.service <<EOF
[Unit]
Description=$2
$5

[Service]
Type=simple
WorkingDirectory=$3
ExecStart=${VMS_HOME}/venv-adapter/bin/python3 -u $4
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
}
py_unit kvs-agent           "MQTT control agent (adapter-01)" \
        "${VMS_HOME}/adapter"             "${VMS_HOME}/adapter/agent.py"
py_unit onvif-admin         "Local ONVIF admin GUI (port 8080)" \
        "${VMS_HOME}/adapter/onvif-admin" "${VMS_HOME}/adapter/onvif-admin/app.py"
py_unit kvs-event-watcher   "ONVIF detections -> evidence clips" \
        "${VMS_HOME}/adapter"             "${VMS_HOME}/adapter/event_watcher.py"
py_unit kvs-outage-buffer   "Durable outage buffering supervisor (OUTAGE.md)" \
        "${VMS_HOME}/adapter"             "${VMS_HOME}/adapter/outage_buffer.py" \
        "After=kvs-mediamtx.service"
py_unit kvs-outage-uploader "Backfill buffered outage footage to S3 (OUTAGE.md)" \
        "${VMS_HOME}/adapter"             "${VMS_HOME}/adapter/outage_uploader.py"

# Not a daemon: a oneshot on a timer. Follows ONVIF cameras to a new IP address (E3).
cat > ~/.config/systemd/user/kvs-camera-rematch.service <<EOF
[Unit]
Description=Follow ONVIF cameras to a changed IP address (registry + MediaMTX)
After=kvs-mediamtx.service

[Service]
Type=oneshot
WorkingDirectory=${VMS_HOME}/adapter
ExecStart=${VMS_HOME}/venv-adapter/bin/python3 -u ${VMS_HOME}/adapter/rematch_cameras.py
EOF

cat > ~/.config/systemd/user/kvs-camera-rematch.timer <<EOF
[Unit]
Description=Rescan for moved ONVIF cameras every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
```

System units — `sudo tee` writes the file, but the heredoc is still expanded by *your*
shell first, so `${VMS_HOME}` and `${USER}` are yours, not root's:

```bash
KVS_SDK_DIR=${VMS_HOME}/vendor/amazon-kinesis-video-streams-producer-sdk-cpp

for cam in 01 02; do
sudo tee /etc/systemd/system/kvs-cam${cam}.service > /dev/null <<EOF
[Unit]
Description=KVS producer for cam-${cam}
After=network-online.target

[Service]
Type=simple
User=${USER}
Environment=GST_PLUGIN_PATH=${KVS_SDK_DIR}/build
Environment=LD_LIBRARY_PATH=${KVS_SDK_DIR}/open-source/local/lib
ExecStart=${VMS_HOME}/adapter/bin/stream-cam${cam}.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
done

# %i stays literal — it's a systemd specifier (the instance name, e.g. cam03), not a
# shell variable, so it survives the unquoted heredoc untouched.
sudo tee /etc/systemd/system/kvs-cam@.service > /dev/null <<EOF
[Unit]
Description=KVS producer for %i
After=network-online.target

[Service]
Type=simple
User=${USER}
EnvironmentFile=/etc/adapter/channels/%i.env
Environment=GST_PLUGIN_PATH=${KVS_SDK_DIR}/build
Environment=LD_LIBRARY_PATH=${KVS_SDK_DIR}/open-source/local/lib
ExecStart=${VMS_HOME}/adapter/bin/stream-channel.sh
Restart=on-failure
RestartSec=5
CPUAccounting=true
MemoryAccounting=true

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
```

`kvs-cam@.service` above adds `User=` and the two `Environment=` lines that the guide's
§16.6 sketch omits — without them the producer fails with `No such element "kvssink"`
(#30).

**Check what was written:**

```bash
grep -h 'ExecStart\|WorkingDirectory\|Environment' \
  ~/.config/systemd/user/{kvs-,onvif-}*.service /etc/systemd/system/kvs-cam*.service
# every path must be absolute and exist — no '$', no '~', no 'MyProjects'
systemd-analyze --user verify ~/.config/systemd/user/kvs-agent.service
systemctl --user cat kvs-agent        # what systemd actually loaded
sudo -k -n true && echo "NOPASSWD sudo OK"   # see "Privileged commands" below. Without -k a
                                             # recently typed password fakes a pass (FoundAndFixed.md #34)
```

**Privileged commands — a narrow sudo rule.** Two things the services run need root, and
they run it with no terminal to type a password into: producer Start/Stop
(`camera_control.py` → `sudo systemctl start|stop kvs-camNN.service`, or
`kvs-cam@camNN.service` for GUI-registered cameras (#37), used by the agent and both GUIs) and camera provisioning (admin GUI → `sudo provision-camera.sh`). Current
Raspberry Pi OS images don't grant passwordless sudo (FoundAndFixed.md #34), so allow
exactly those two and nothing else:

```bash
cat > /tmp/020_vms-adapter <<EOF
# VideoSafeZone adapter (LAUNCH.md A8): exactly the two privileged actions its services run
# non-interactively -- producer Start/Stop (camera_control.py) and camera provisioning
# (admin GUI -> provision-camera.sh). Regex arguments need sudo >= 1.9.10.
${USER} ALL=(root) NOPASSWD: /usr/bin/systemctl ^(start|stop) kvs-cam(@cam)?[0-9]{2}\\.service\$, \\
    ${VMS_HOME}/adapter/bin/provision-camera.sh ^cam-[0-9]{2} cam[0-9]{2}\$
EOF
/usr/sbin/visudo -cf /tmp/020_vms-adapter     # must say "parsed OK" -- never install it otherwise:
                                              # a broken sudoers file can lock you out of sudo
sudo install -m 0440 -o root -g root /tmp/020_vms-adapter /etc/sudoers.d/020_vms-adapter
sudo visudo -c                                # the whole configuration, all "parsed OK"
```

**Proof.** `-k` ignores any cached password, so this shows the rule and not your last
`sudo`. Unit names that don't exist keep it harmless:

```bash
sudo -k -n systemctl stop kvs-cam99.service   # "Unit kvs-cam99.service not loaded" = allowed
sudo -k -n systemctl restart kvs-cam99.service   # "a password is required" = refused, good
sudo -k -n true                                  # "a password is required": nothing else opened
sudo -k -n -l | tail -2                          # the NOPASSWD line as sudo loaded it
```

This narrows what the services *ask* for; it is not a hard boundary. `provision-camera.sh`
lives in your own, writable checkout, so anyone who is `$USER` could edit it first (the
script says as much). A real boundary would mean installing it root-owned under
`/usr/local/sbin`.

**If the clone moves** (new folder, new Pi, different user): update `VMS_HOME` in
`~/.bashrc`, `source ~/.bashrc`, re-run this whole step (it overwrites the files, and the
sudo rule, which names the clone's path, has to be regenerated too), then
`systemctl --user restart` the running user units. Stale unit paths fail quietly —
`Restart=on-failure` just keeps retrying a missing file — so the `grep` check above is
the quick way to spot them.

### A9. Local camera hardware (cam-01, USB webcam) — detected at every start

Nothing to configure while one USB camera is attached. `camera-init.sh` and
`publish-cam01.sh` find it themselves (`adapter/bin/detect-hw.sh`): the one
MJPG-capable `/dev/v4l/by-id/…` capture node, and the microphone on the **same USB
device**, matched through sysfs rather than by ALSA card name (many webcams all call
theirs "Webcam"). Both are logged at start (`journalctl --user -u kvs-camera-publish`).
Detection never guesses: no camera, or more than one, is an error naming the candidates.

**Proof:**

```bash
"$VMS_HOME/adapter/bin/detect-hw.sh" --print
# video device  : /dev/v4l/by-id/usb-…-video-index0 -> /dev/video0
# audio device  : hw:CARD=…,DEV=0
# buffer mount  : …                (A10's stick, found by LABEL=vms-buffer)
# isolated CPUs : …                (empty = no isolcpus; A3's taskset pinning protects nothing)
```

**Only if needed** — a second USB camera, a different model, or other lighting — create
`/etc/adapter/cameras/cam01.env` from the template (every key is optional; unset keys keep
the PW310 defaults shown in it):

```bash
sudo install -D -m 644 "$VMS_HOME/config/cameras/cam01.env.example" /etc/adapter/cameras/cam01.env
sudoedit /etc/adapter/cameras/cam01.env    # CAM_MATCH / CAM_DEVICE, CAPS, V4L2_*_CTRLS
"$VMS_HOME/adapter/bin/detect-hw.sh" --print                 # shows "(present)" and the result
systemctl --user restart kvs-camera-init kvs-camera-publish  # settings apply on restart
```

A changed `CAPS` or control set is a pipeline change: verify with a decoded frame, not
just a running unit (Part C, and `CLAUDE.md` "Verifying changes").

### A10. USB outage-buffer stick (optional) — once per Pi

Only for durable outage buffering (`OUTAGE.md`); skip it and the outage units just idle
("buffer unavailable … idle", FoundAndFixed.md #39). The stick is found by its filesystem
**label** `vms-buffer`, mounted at `/mnt/vms-buffer` by UUID.

**1. Identify it** — `sdX1` below is a placeholder, never a device name:

```bash
lsblk -o NAME,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINTS    # usually sda, partition sda1
```

**2. Format only a new stick.** If the partition is already `ext4` labelled `vms-buffer`
(a stick moved over from another Pi), **skip this** — formatting erases the footage on it.
Otherwise it erases everything on that partition, so check size and name twice:

```bash
sudo mkfs.ext4 -m 0 -L vms-buffer /dev/sdX1          # X = the letter lsblk showed
```

**3. Mount it by UUID**, with `nofail` so a missing stick never blocks boot:

```bash
UUID=$(lsblk -no UUID /dev/sdX1)                     # or copy it from step 1
sudo mkdir -p /mnt/vms-buffer
echo "UUID=$UUID  /mnt/vms-buffer  ext4  defaults,noatime,nofail,x-systemd.device-timeout=10  0  2" \
  | sudo tee -a /etc/fstab
sudo systemctl daemon-reload                         # systemd builds mount units from fstab
sudo mount /mnt/vms-buffer
ls -la /mnt/vms-buffer        # a moved stick: look first -- old live/ and outage/ captures
                              # are footage; outage/*/state.json not "uploaded" still uploads
```

**4. Hand it to the adapter** — owner, directories, and the sentinel:

```bash
sudo chown "$USER:$USER" /mnt/vms-buffer
mkdir -p /mnt/vms-buffer/live /mnt/vms-buffer/outage
touch /mnt/vms-buffer/.vms-buffer-ok        # sentinel: nothing arms without it
```

The sentinel is not belt-and-braces. If the stick is unplugged but the mountpoint still
exists, recording would land on the SD card — and with tens of GB free it *fits*, which is
worse than failing.

**Proof:**

```bash
findmnt /mnt/vms-buffer                              # SOURCE /dev/sda1, FSTYPE ext4
"$VMS_HOME/adapter/bin/detect-hw.sh" --print | grep buffer   # buffer mount  : /mnt/vms-buffer
journalctl --user -u kvs-outage-buffer -n 3 --no-pager       # "buffer ready" within ~5 s
                                                             # (+ any orphan captures it found)
```

Recording itself stays off until outage buffering is switched on per camera (either GUI),
and arms only while that camera's producer runs — Part C "If outage buffering is enabled".

---

## Part B — Launch (once per Pi; after that everything starts at boot)

**Run this part once, not every session.** `systemctl --user enable --now` does two
things: `--now` starts the units immediately, and `enable` makes each one start at every
boot from then on (`WantedBy=default.target`). Lingering (`loginctl enable-linger`, A3)
starts user units at boot without anyone logging in. So after a reboot the camera
pipeline, MediaMTX (with its camera paths re-added), the agent, admin GUI, event watcher,
outage units and rematch timer all come back **by themselves** — nothing here needs
repeating.

| When | What to do |
|---|---|
| **Once**, after A8 on a new Pi or a moved clone | this Part B |
| **After every reboot** | nothing to start — just verify with **Part C** |
| **Each session you want cloud video** | Start the producer (either GUI's Start, or the `sudo systemctl start` below). The producers (`kvs-cam*`) are deliberately **not** enabled, because they cost money while running (§1.2) — and Part F at the end |

**Proof** that the one-time step is still in effect (e.g. after a reboot):

```bash
loginctl show-user "$USER" -p Linger                 # Linger=yes
systemctl --user is-enabled kvs-camera-init kvs-mediamtx kvs-camera-publish kvs-agent \
  onvif-admin kvs-event-watcher kvs-outage-buffer kvs-outage-uploader kvs-camera-rematch.timer
                                                     # enabled, one line each
systemctl is-enabled kvs-cam01 kvs-cam02             # disabled — on purpose
```

Re-run Part B only if one of those says `disabled`, or after re-running A8 for a moved
clone (then `systemctl --user daemon-reload` first).

Everything below is a proper systemd unit — nothing here needs a manually-run background
process anymore. The unit files themselves are created in **A8**; if `systemctl --user
enable` says `Unit … not found`, that step hasn't been run on this Pi.

**A partial launch fails silently, not obviously** (FoundAndFixed.md #5): leave one unit
out and everything else still reports "active" while producing nothing. Run them all,
then verify with **Part C**, not just `systemctl ... is-active`.

```bash
systemctl --user enable --now kvs-camera-init     # one-shot: locks exposure/WB/focus
systemctl --user enable --now kvs-mediamtx        # RTSP server
systemctl --user enable --now kvs-camera-publish  # camera → rtsp://127.0.0.1:8554/cam01 (§2.8)
systemctl --user enable --now kvs-agent           # MQTT control agent (adapter-01)
systemctl --user enable --now onvif-admin         # local camera admin GUI, port 8080 (Part E)
systemctl --user enable --now kvs-event-watcher   # ONVIF detection -> evidence clips
systemctl --user enable --now kvs-outage-buffer kvs-outage-uploader  # OUTAGE.md; idle unless enabled per camera
systemctl --user enable --now kvs-camera-rematch.timer  # follow ONVIF cameras to a new IP (E3)
```

`onvif-admin` is only needed when you want to discover/register/control cameras (Part E).
`kvs-event-watcher` only acts for a camera whose `recordingMode` is a detection mode, and
the outage units only for a camera with outage buffering switched on; otherwise they idle
at no cost. Same list as README's "Start it".

That's it — the actual KVS producer (`kvs-cam01.service`, a **system** unit, not user) is
deliberately *not* auto-started here. It's controlled on demand by the agent, either via
MQTT or the browser client's Start/Stop buttons — and once `kvs-camera-publish` is up, its
own `Restart=on-failure` will pick it up automatically if it was already crash-looping
against a missing RTSP source. To start it directly without the agent:

```bash
sudo systemctl start kvs-cam01.service   # or: aws iot-data publish --topic adapter/adapter-01/cmd \
                                          #     --cli-binary-format raw-in-base64-out \
                                          #     --payload '{"action":"start"}' --region eu-central-1
```

**If `kvs-camera-init` or `kvs-camera-publish` fails**, run `adapter/bin/detect-hw.sh --print`
first: "no MJPG-capable USB camera" or "2 cameras" there is the whole answer (A9).

**If any unit fails to start**, check in this order: `who -b` / `uptime` (did the Pi just
crash-reboot? see §1.4), `journalctl --user -u <unit> -n 50`, `free -h` (memory
pressure), `sudo systemctl is-active earlyoom nftables` (should both be `active`).

---

## Part C — Verify

**Check in this order — `systemctl ... is-active` alone is not proof of anything.** Every
unit can report `active` while the stream is genuinely dead (FoundAndFixed.md #5). Only
the first two commands below actually prove media is flowing; the systemd check at the
end is a secondary sanity check, not the primary one.

```bash
# 1. camera → RTSP (Checkpoint 1) — the real proof local capture is working
ffprobe -rtsp_transport tcp rtsp://127.0.0.1:8554/cam01

# 2. KVS stream is receiving live media (Checkpoints 4/5) — needs kvs-cam01.service active
EP=$(aws kinesisvideo get-data-endpoint --stream-name cam-01 --region eu-central-1 \
  --api-name GET_HLS_STREAMING_SESSION_URL --query DataEndpoint --output text)
URL=$(aws kinesis-video-archived-media get-hls-streaming-session-url \
  --endpoint-url "$EP" --region eu-central-1 --stream-name cam-01 --playback-mode LIVE \
  --query HLSStreamingSessionURL --output text)
ffprobe "$URL"   # a fresh creation_time in the output is the actual proof, not just HTTP 200

# 3. systemd units — a secondary check, not a substitute for 1 and 2
systemctl --user list-units 'kvs-*' --no-pager
sudo systemctl is-active kvs-cam01.service
```

### If the camera has audio enabled (guide §18)

Audio is off by default. When it is on, two extra checks matter, because both of its
failure modes are **silent** at the level of step 1–3 above — fragments persist, both
tracks appear, and `ffprobe` is happy:

```bash
# 4. frames being rejected? want exactly 0.
#    Anything above zero is the shared-DTS trap (§18.3, FoundAndFixed.md #15): you are losing audio.
journalctl -u kvs-cam01.service --since "-60 s" | grep -c 0x30000005

# 5. is the audio actually all arriving, and is it real?
#    delivered kb/s well below the configured bitrate = frames being dropped;
#    RMS at the noise floor with a high flat factor = a dead or clipping mic.
ffmpeg -i "$URL" -t 20 -c copy -y /tmp/s.mp4
ffprobe /tmp/s.mp4                     # expect BOTH streams
ffmpeg -i /tmp/s.mp4 -vn -af astats=metadata=1 -f null - 2>&1 | grep -E 'RMS|Flat'
```

Note that step 2 is the step that catches codec-private-data errors: a stream can ingest
perfectly and still fail `GetHLSStreamingSessionURL` with
`InvalidCodecPrivateDataException`. And as always, finish in a **browser** — MSE is
stricter than `ffmpeg` and has caught two regressions here that `ffmpeg` passed (#13, #16).

To toggle audio: tick "Record audio with video" in the cloud client, or "with audio" in
the local admin table. It applies on the camera's **next Start**, by design (§18.7).

### If outage buffering is enabled (OUTAGE.md)

Off by default. When on, footage is buffered to the USB stick while AWS is unreachable and
backfilled into **Evidence clips** on recovery. Two user units do this — both must be up:

```bash
systemctl --user is-active kvs-outage-buffer kvs-outage-uploader

# armed only while that camera's producer runs; check what MediaMTX was actually told:
curl -s http://127.0.0.1:9997/v3/config/paths/get/cam02 | python3 -m json.tool | grep record

# the rolling window should stay BOUNDED (~4 segments = 120s / 30s). Growing without
# limit means retention is broken and the stick will fill silently.
ls /mnt/vms-buffer/live/cam02/*.mp4 | wc -l

# captures waiting to upload (empty in steady state)
ls -d /mnt/vms-buffer/outage/*/ 2>/dev/null
```

**The stick must be mounted or nothing is armed** — the supervisor checks `ismount` plus
the `/mnt/vms-buffer/.vms-buffer-ok` sentinel every tick, because an unplugged stick with
the mountpoint still present would send MediaMTX's writes to the SD card, and with tens of
GB free there a long outage *fits*, which is worse than failing.

To test it, use `adapter/bin/awsblock.sh on|off` — **not** §10.2's `iptables` snippet,
which is IPv4-only and silently ineffective here (#18). `awsblock.sh` needs `iptables` and
`ip6tables`, which a fresh Raspberry Pi OS image does not have (it ships `nft` only):
`sudo apt install -y iptables` first — its output then shows both families blocked. Then
`adapter/bin/gap-fill.py --stream cam-02 --last 600`.

---

## Part D — Access the browser client (§8, Checkpoint 7)

**URL:** https://dugyd3kkt36pw.cloudfront.net  (CloudFront + TLS, §8.5.1)

**Login:** username `demo-viewer`, password `DemoViewer2026!`

The old plain-HTTP S3 website URL
(`http://vms-demo-client-596633517506.s3-website.eu-central-1.amazonaws.com`) still
works today — the bucket is still public pending the cutover in §8.5.1's last step
(swap to the OAC-only bucket policy, enable Block Public Access, `delete-bucket-website`).
Until that runs, there is still an unencrypted way to reach the page. Prefer the HTTPS
URL, and expect some browsers to complain about the HTTP one.

This is a genuinely public URL, reachable from anywhere (no VPN, no router changes, no
geographic restriction — that's the point). Sign in, press **Start** if the stream isn't
already live, wait a few seconds for the first HLS segments to land, then **Reload
player** if it doesn't auto-recover from the initial buffering.

---

## Part E — Add an ONVIF camera (local admin GUI)

**URL:** http://192.168.178.53:8080 — LAN only, no login. If it isn't up:
`systemctl --user enable --now onvif-admin`.

**Why this is a separate local app and not part of the browser client:** WS-Discovery is
UDP multicast. It only works from a process on the same LAN segment as the cameras — the
cloud client is served from S3 and reached over the internet, and Lambda has no route to
your LAN at all. Discovery and registration therefore have to run on the Pi (guide
§16.2.1).

**Prerequisite:** the camera must be on the *same broadcast domain* as the Pi. Multicast
does not cross routers or VLANs by design, so a camera on a different subnet will never
answer a scan no matter how long you wait.

### E1. Discover

1. Enter the camera's **ONVIF username / password** (for the existing camera: `admin`).
2. Press **Scan LAN**.

Each device that answers shows its XAddrs, ONVIF scopes, and — because credentials were
supplied — its manufacturer/model, media profile and a real RTSP URL. A camera already in
the registry is labelled **"Registered as cam-NN"** and its button reads *Re-register*
instead of *Register*.

Same thing from the CLI, useful when the GUI is not running:

```bash
venv-adapter/bin/python3 adapter/bin/discover-onvif.py --user admin --password *** --timeout 5
```

### E2. Register

Press **Register this camera**, check the pre-filled fields, and give it an ID matching
`cam-NN` (e.g. `cam-03`). One click then does all of this:

| Step | What happens |
|---|---|
| MediaMTX path | added **live** via its local API — no config rewrite, no restart, so other cameras keep streaming; re-added from the registry after every MediaMTX restart (E3) |
| systemd | `/etc/adapter/channels/camNN.env` written, then `kvs-cam@camNN.service` enabled (templated unit, guide §16.6) |
| KVS | stream `cam-NN` created, 24 h retention |
| Registry | row written to the `cameras` DynamoDB table |

That registry row is the single source of truth: the cloud client, the MQTT control plane
and every camera-aware Lambda read it, so a camera registered here works **everywhere
immediately, with no code change and no redeploy**.

### E3. Re-register an existing camera

Use this when a camera's credentials changed. (A moved IP is followed automatically — see
below; Re-register is only the manual fallback.) It updates the MediaMTX path source and
the registry row — and deliberately **does not touch
systemd**, because `cam-01`/`cam-02` predate the `kvs-cam@` template and re-provisioning
them would start a second, conflicting producer for the same KVS stream.

**The registry is where a network camera's address and credentials live** — `rtspUrl` on
its `cameras` row, nowhere in git (FoundAndFixed.md #31, #32). `mediamtx.yml` has no camera paths: after every
MediaMTX start (crash restarts included) `adapter/sync_mediamtx_paths.py` re-adds them
from the registry, or from a local cache (`~/.local/state/vms-adapter/cameras-cache.json`,
mode 600) when AWS is unreachable. So after changing a camera's password *on the camera*,
Re-register it here; that updates the registry, and the path follows immediately and on
every later restart.

**Proof** — what the sync would do right now, and what MediaMTX has:

```bash
"$VMS_HOME/venv-adapter/bin/python3" "$VMS_HOME/adapter/sync_mediamtx_paths.py" --dry-run
# cam-02: cam02 up to date            <- good
# cam-02: passthrough but no rtspUrl  <- this camera was never registered through the GUI:
#                                        Re-register it, or it has no path after a restart
curl -s http://127.0.0.1:9997/v3/config/paths/list | python3 -c \
  'import json,sys; print([p["name"] for p in json.load(sys.stdin)["items"]])'
journalctl --user -u kvs-mediamtx | grep sync-paths    # each start's sync, credentials masked
```

**A moved camera is followed automatically**, so Re-register is only needed for changed
credentials. Every 5 minutes `kvs-camera-rematch.timer` scans the LAN. It recognises each
camera by its WS-Discovery identity (`onvifEndpointRef`, a `urn:uuid:…` that survives
DHCP changes), and when one answers at a new address it rewrites `onvifHost` and the
host in `rtspUrl` (registry, then MediaMTX path). Cameras registered before this existed
get their identity learned automatically the first time they answer at their registered
address. It never guesses: duplicates and address clashes are logged and skipped.

**Proof:**

```bash
"$VMS_HOME/venv-adapter/bin/python3" "$VMS_HOME/adapter/rematch_cameras.py" --dry-run
# rematch: scan: 1 ONVIF device(s) answered
# rematch: cam-02: learned identity urn:uuid:…    <- first run for an older registration
# rematch: cam-02: moved <old> -> <new>; …        <- what a real move looks like
systemctl --user list-timers kvs-camera-rematch.timer     # next and last run
journalctl --user -u kvs-camera-rematch | grep rematch    # what each run did
```

### E4. Control, from the same table

| Column | Does what |
|---|---|
| **Local preview** | live video straight from MediaMTX (port 8888) — no cloud round trip, works before AWS is involved at all |
| **KVS push** | polls `systemctl is-active` for the producer — actual state, not what a button last claimed |
| **Recording** | `manual` / `motion` / `cellMotion` / `human` — consumed by `kvs-event-watcher` |
| **Start/Stop Remote** | starts/stops the KVS producer, i.e. what costs money |
| **IR Auto/Off/On** | day-night switch, where the camera supports it |

Motion analytics (sensitivity, cell mask, alarm delays) are shown **read-only** in section
3 of the page. That is not a UI shortcut: `SetVideoAnalyticsConfiguration` is a silent
no-op on this camera — it returns success and changes nothing (verified). Change those in
the camera's own web UI.

### E5. Verify — same rule as Part C

`systemctl is-active` is not proof. After registering:

```bash
ffprobe -rtsp_transport tcp rtsp://127.0.0.1:8554/camNN     # is the camera actually feeding MediaMTX?
systemctl is-active kvs-cam@camNN.service                   # is the producer up?
```

Then Part C's KVS check to confirm media is reaching the cloud.

### Gotchas found the hard way

- **Detection recording needs the producer running.** `clip_to_s3` cuts `ts-12s..ts+33s`
  from KVS, and KVS only returns footage it already ingested. Arming a detection mode
  while the stream is stopped produces triggers with no footage behind them — the clip
  fails and the only trace is a Lambda log. Start the stream first.
- **A clip takes ~40 s to appear** after a detection (38 s post-roll so the full window
  exists, plus Lambda time), then up to 30 s more before the browser client announces it.
  Not a fault — pressing Refresh sooner simply finds nothing.
- **Registration is not idempotent against a half-finished attempt.** If provisioning
  fails the MediaMTX path is rolled back (#36), but check `/etc/adapter/channels/` before
  retrying with the same ID.

---

## Part F — Stop everything / cost control

Per §1.2's cost rule — never leave the producer running unattended:

```bash
sudo systemctl stop 'kvs-cam*'   # every producer, GUI-registered kvs-cam@camNN too: stops PutMedia billing
systemctl list-units 'kvs-cam*' --state=active --no-legend   # nothing listed = nothing billing
# camera/MediaMTX/agent can stay running; they cost nothing idle
```

Full teardown (deletes the KVS stream — recreating it takes seconds, see §11):

```bash
"$VMS_HOME/teardown.sh"   # if present; otherwise see guide §11 for the manual steps
```

---

## Known traps not obvious from a cold read

- **`kvssink: no element "kvssink"`** — you're in a shell that never sourced `.bashrc`
  (any systemd unit, most non-interactive contexts). Export `GST_PLUGIN_PATH`/
  `LD_LIBRARY_PATH` explicitly (§4.3).
- **`create-stream`/`ListFragments`/`GetHLSStreamingSessionURL` → AccessDenied** —
  you're using `kvs-demo-producer`'s deliberately scoped-down credentials (or the
  adapter's certificate) for something that needs your own admin AWS identity. `unset
  AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY` to fall back to your default profile.
- **`AWS_ERROR_MQTT_UNEXPECTED_HANGUP` connecting the agent** — check nothing in the
  connection declares a Last Will with `retain=True`; IoT Core rejects the CONNECT for a
  retained LWT (#6).
- **`UnrecognizedClientException` / `security token invalid`** — check for a stray
  `AWS_SESSION_TOKEN` left from an earlier, unrelated credential export in the same
  shell; `unset` it.
- **A sudden reboot mid-build** — see §1.4 in full (#1); the short version is `earlyoom` +
  swap + firmware update + CPU-pinning the build away from the WiFi IRQ cores. Pinning
  only helps on a kernel with `isolcpus` (the first Pi had `1,2`; check yours with
  `detect-hw.sh --print`, A9).
- **"Is the stream alive?" → no, but every `systemctl` check said `active`** —
  `kvs-cam01.service` crash-loops silently against a 404 if `kvs-camera-publish` isn't
  also running; it has no way to tell "no camera feed" apart from any other transient
  failure. Always verify with Part C's `ffprobe` commands, not unit status alone (#5).
