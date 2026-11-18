# LAUNCH-ADAPTER.md — Preparing a new adapter (Raspberry Pi 4B), ready for its Cloud ID

Step by step, from an adapter generated in the System console to a Pi at the customer's site that its
customer activates with the Cloud ID. The cloud must be up first (`LAUNCH-SYSTEM.md` Part 1). The first
Pi, `adapter-01`, is set up differently — `LAUNCH.md` Part A, its identity from `setup-identity.sh` — and
needs no Cloud ID (`LAUNCH-SYSTEM.md` Part 2, option A).

| Step | Who | Where |
|---|---|---|
| 1. Generate the adapter and its Cloud ID | system owner or administrator | the web page: System → Adapters |
| 2. Make its AWS identity | the operator, with their own AWS login | a machine with the AWS CLI |
| 3–7. Prepare and check the Pi | technician | the Pi |
| 8. Install it, hand over the Cloud ID | technician, dealer or owner | the customer's site |
| 9. Activate it with the Cloud ID | the customer's holder or Super Admin, or its dealer | the web page |
| 10–11. Add its cameras, verify | the customer (web page) or the technician (admin GUI) | the web page or on site |

Working from a **Windows 11 PC** — at the site, or reached over Remote Desktop — see "Working from a
Windows 11 PC" just below before step 1.

What makes an adapter "this one": its **Thing** (`adapter-NNNN`, in `/etc/adapter/adapter.env`) and the
**certificate** attached to it (in `certs/`). Everything else on the card is the same for every adapter.
After the narrowing (`DEALER-PANEL-PLAN.md` Phase 10.2f) an adapter's identity reaches only its own
cameras, streams, secrets and uploads, so one adapter's card can't touch another customer's.

---

## Working from a Windows 11 PC (also over Remote Desktop)

Every step below can be done from a Windows 11 PC: the web page and the AWS sign-in in its browser,
flashing with Raspberry Pi Imager, the Pi over SSH. When you reach that PC with Remote Desktop, the
programs run on the PC itself, so two things have to be physically there:

- **The SD card reader is plugged into the Windows PC.** Remote Desktop can pass a drive through, but not
  a card reader that Imager can write to.
- **The Pi is on the same network as the Windows PC.** SSH reaches the Pi only from inside its network —
  the system opens nothing on the router, by design. Someone at the site moves the flashed card into the
  Pi, plugs in the stick and the network, and powers it on.

### Prepare the Windows PC, once

1. **Raspberry Pi Imager**, from raspberrypi.com/software (installing it, and writing a card, need
   administrator rights on the PC).
2. **The SSH client** is built into Windows 11. In *Terminal* (PowerShell): `ssh -V` prints a version. If
   it doesn't: Settings → System → Optional features → *Add an optional feature* → *OpenSSH Client*.
3. **An SSH key** for logging in to the Pi without a password:

   ```powershell
   ssh-keygen -t ed25519                                        # Enter for the default file; a passphrase is wise
   Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub | Set-Clipboard   # the public key, ready for Imager
   ```

4. **The network profile *Private*** (Settings → Network & internet → the connection → *Private network*):
   on a *Public* network Windows' firewall blocks the lookups that make `vsz-adapter-NNNN.local` work.
5. **Power: never sleep while you work** (Settings → System → Power → *Sleep: Never*, at least for the
   session). Disconnecting Remote Desktop leaves your programs running; a sleeping PC drops every SSH
   session.
6. *Optional:* **VS Code** with the *Remote - SSH* extension — an editor and terminals on the Pi from
   Windows (and, if you use it, the Claude Code extension, which then runs on the Pi).

Remote Desktop's shared clipboard lets you paste commands from your own computer into the PC's Terminal.

### Flashing from Windows (step 4)

Insert the card into the PC's reader. Imager: *Raspberry Pi 4* → *Raspberry Pi OS (64-bit)* → the card
(check its size — writing erases it) → customisation: the hostname `vsz-adapter-NNNN`, your user name and
password, the site's Wi-Fi **and its country**, the time zone, and under SSH *public-key authentication
only*, with the key from the clipboard (Imager may fill it in from `.ssh` by itself). Write, wait for the
verification, eject. The card goes into the Pi at the site.

### Reaching the Pi from Windows

```powershell
ssh <user>@vsz-adapter-NNNN.local         # the first time: confirm its host key with "yes"
```

- **"Could not resolve hostname":** take its address from the router's list of devices, or
  `ping <the network>.255` then `arp -a` — a Raspberry Pi 4's address starts with `dc-a6-32` or
  `e4-5f-01` — and `ssh <user>@<address>`.
- **"REMOTE HOST IDENTIFICATION HAS CHANGED"** after a card was flashed again: `ssh-keygen -R
  vsz-adapter-NNNN.local`, then connect again.
- The admin GUI opens in the PC's browser: `http://vsz-adapter-NNNN.local:8080`.

**Keep the setup alive if the connection drops:** a command running in an SSH session stops with it
(only the SDK build runs on as a background unit). Run the setup inside `tmux`, and pick it up again
after any drop:

```bash
sudo apt install -y tmux
tmux new -s setup              # then run step 5's commands in here
# after a drop:  ssh <user>@vsz-adapter-NNNN.local   and   tmux attach -t setup
```

Re-running `setup-all.sh` after a drop is always safe too: it carries on where it stopped.

### The identity without carrying a file (steps 2 and 6 together)

From Windows, the simplest is to make the adapter's AWS identity **on the new Pi itself**: nothing to copy
between machines, and the private key never leaves the card. When `setup-all.sh` stops at the device
certificate (step 5), the AWS CLI is installed already:

```bash
cd ~/Projects/VideoSafeZone
aws login --remote                                    # open the URL it prints in the PC's browser; sign in as the operator
bash cloud/adapter-identity.sh create adapter-NNNN --dry-run
bash cloud/adapter-identity.sh create adapter-NNNN
bash setup/setup-identity.sh restore ~/.local/state/videosafezone/adapter-identities/adapter-NNNN
bash setup/setup-identity.sh backup                   # onto this adapter's stick
rm -rf ~/.local/state/videosafezone/adapter-identities/adapter-NNNN
aws logout                                            # no operator credentials left on the adapter
bash setup/setup-all.sh                               # carries on
```

Then go on with step 7. (Made elsewhere instead, a folder on the Windows PC goes across with
`scp -r .\adapter-NNNN <user>@vsz-adapter-NNNN.local:~/adapter-NNNN`, and step 6 as written.)

---

## Step 1 — Generate the adapter (System console)

Signed in as the system owner or an administrator: **System → Adapters → New adapters**.
- **How many**, and a **label** (where it is going, or a batch name).
- *Optional:* the **dealer** — then only that dealer's customers (or the dealer, for one of them) can
  activate it. Without one, any customer can, and it joins the stock of that customer's dealer.
- *Make them.* For each adapter the page shows its Thing name (`adapter-NNNN`) and its **Cloud ID**
  (ten characters, shown as `K7QM4-XRPZ9`) **once**: *Print labels*, or write them down.

A Cloud ID is single use and stored only as a hash. Lost before activation: System → Adapters →
*New code* (the old one stops working).

## Step 2 — Its AWS identity (the operator's machine)

The System console can't make certificates (D15, amended): you do, with your own AWS login, on any
machine with this repository and the AWS CLI — the first Pi will do.

```bash
cd ~/Projects/VideoSafeZone
aws login --remote                                         # if not logged in
bash cloud/adapter-identity.sh pending                     # adapters that still need an identity
bash cloud/adapter-identity.sh create adapter-NNNN --dry-run
bash cloud/adapter-identity.sh create adapter-NNNN
```

It makes the Thing, a certificate attached to it and to the Thing policy, and records that the adapter
has an identity, then writes the folder `~/.local/state/videosafezone/adapter-identities/adapter-NNNN/`
(0700): the certificate, its private key (AWS shows it this once), the two CA files and `thing-name`.
**Whoever holds that folder holds the adapter's identity.** Take it to the new Pi (step 5) by `scp` or on
a stick, and delete every copy but the Pi's once it is restored there — or run this step on the new Pi
itself, during step 5, and nothing has to travel ("The identity without carrying a file", above).

## Step 3 — The hardware

- A **Raspberry Pi 4B** and its power supply; a **microSD card of 32 GB or more**.
- *Recommended:* a **USB stick of its own** for this adapter, ext4 labelled `vms-buffer` (`LAUNCH.md` A10:
  `sudo mkfs.ext4 -m 0 -L vms-buffer /dev/sdX1`, after checking the name twice). It holds this adapter's
  identity backup, the SDK build cache and its outage recordings. To save the SDK's 1.5–2.5 hour build,
  copy the cache file from another adapter's stick (`/mnt/vms-buffer/.setup-cache/kvs-sdk-*.tar.gz`) onto
  the new one — it is used only if the new Pi's user name and clone path are the same.
- **Cameras:** ONVIF IP cameras on the site's network, found in step 10. A USB webcam is not supported
  on a new adapter yet — only the first Pi has one (`cam-01`); a USB camera elsewhere has no ID scheme
  (`DEALER-PANEL-PLAN.md` A6).
- **The site's network:** the Pi needs outbound internet only — nothing inbound, no port opened on the
  router. The ONVIF cameras must be on the same network segment as the Pi (discovery is multicast).

## Step 4 — Flash the card and boot

With Raspberry Pi Imager (`LAUNCH.md` Part A, steps 1–2): *Raspberry Pi 4*, *Raspberry Pi OS (64-bit)*
(Debian 13 "trixie"); in the customisation: a hostname of its own (`vsz-adapter-NNNN`), your user name
and password, **the Wi-Fi network of the site where it will run, and its country** (or plan on Ethernet),
the time zone, SSH enabled. Plug in the stick, boot, wait two or three minutes, then
`ssh <user>@vsz-adapter-NNNN.local`.

## Step 5 — Clone, name its Thing, set up

```bash
git --version || sudo apt install -y git
git clone https://github.com/vizdr/VideoSafeZone.git ~/Projects/VideoSafeZone
cd ~/Projects/VideoSafeZone
sudo install -D -m 644 config/adapter.env.example /etc/adapter/adapter.env
sudo sed -i 's/^THING_NAME=.*/THING_NAME=adapter-NNNN/' /etc/adapter/adapter.env
grep -E '^(THING_NAME|ADOPT_UNSTAMPED_ROWS)=' /etc/adapter/adapter.env     # adapter-NNNN, and no
bash setup/setup-all.sh            # as yourself; asks for your password once
```

Setting the Thing *before* the setup matters: `setup-system.sh` installs `adapter.env` only when there
is none, and the identity restored next works only as the Thing it was made for. The setup stops at the
device certificate — this card has none yet — and says so.

## Step 6 — Restore its identity, keep a copy, carry on

With the folder from step 2 copied to the Pi (here `~/adapter-NNNN`):

```bash
bash setup/setup-identity.sh restore ~/adapter-NNNN   # refuses if adapter.env names another Thing
bash setup/setup-identity.sh backup                  # onto this adapter's stick: a fresh card restores it
rm -rf ~/adapter-NNNN                                 # and the operator deletes their copy too
bash setup/setup-all.sh                              # carries on: units, launch, proofs
```

`restore` ends with the identity's proofs: the credentials endpoint hands out credentials for this
Thing, the MQTT endpoint accepts the certificate, the project's code assumes the adapters' role.

## Step 7 — Check it before it leaves

```bash
bash setup/setup-check.sh                                   # every line ok
venv-adapter/bin/python3 adapter/device_access.py check     # its own resources allowed, nothing wider
venv-adapter/bin/python3 adapter/registry_client.py check   # the registry route answers this Thing, refuses another's
journalctl --user -u kvs-agent -n 20 --no-pager              # connected to AWS IoT as adapter-NNNN
```

What to expect on an adapter that has no camera and isn't activated yet:
- `setup-all.sh` ends without a failed proof; the launch notes "no USB camera in this adapter's registry:
  kvs-camera-init and kvs-camera-publish stay off" — those two units are the first Pi's alone
  (FoundAndFixed.md #99).
- `device_access.py check`: all PASS; its index lists no rows yet, and the comparison with the table says
  SKIP (the role can't scan, as intended).
- `registry_client.py check`: the first two lines PASS; the third, "a harmless write", fails with "this
  Pi has no camera to write to" until it has one — expected.
- The admin GUI (`http://<the Pi's address>:8080`, `hostname -I` prints it) opens, and refuses to register
  a camera: the adapter isn't activated yet.
- In the web page, System → Adapters shows `adapter-NNNN` online, not activated.

## Step 8 — Install it at the site, hand over the Cloud ID

Power and network (the Wi-Fi set in step 4, or Ethernet); every service starts at boot by itself. Give
the customer — or its dealer — the label with the **Cloud ID**. The adapter needn't be online to be
activated, so the order of step 8 and step 9 doesn't matter. Always `sudo poweroff` before cutting the
power, here and later: a cut mid-write can empty files on the card.

## Step 9 — The customer activates it

Signed in on the web page as the account's holder or Super Admin (or the dealer, for that customer, or
an administrator): **Settings → Cloud adapters → Add adapter → Enter Cloud ID**, then the site — one of
the account's, or a new one with its name and time zone.

- A wrong or used code answers "this Cloud ID can't be activated" (no hint which); after five wrong
  codes in an hour from one person (or twenty from one network) activation is refused for the rest of
  the hour.
- Afterwards the page shows the adapter under *Your adapters*. The cloud sends the adapter a retained
  message that it is activated; the agent logs "the cloud says this adapter is activated" when it
  receives it, and the admin GUI registers cameras from then on.

## Step 10 — Add its cameras

- **From the web page** (holder or Super Admin): *Add cameras* — the adapter scans its own network and
  lists what it found; pick a camera, give its login, and the adapter registers it. The password travels
  only to the adapter (it waits in a short-lived request, never in an MQTT message), and is kept in SSM
  under this adapter's Thing.
- **On site**, through the admin GUI (`LAUNCH.md` Part E): *Discover*, then *Register*.

Camera IDs are allocated by the cloud (`cam-NN`, past every adapter's). Each camera gets its producer
installed but not started: **Start** in either GUI sends it to the cloud, **Stop** ends it — a running
producer costs money (`LAUNCH.md` Part F).

## Step 11 — Verify end to end

1. In the web page as the customer: *Live* shows the camera; *Start*; the picture comes up within
   seconds; *Record clip*; the clip is in *Events*; **Stop**.
2. On the Pi: `LAUNCH.md` Part C — `ffprobe` on the camera's local RTSP path, the KVS stream receiving
   media while it runs.
3. System → Adapters: the adapter online, activated, at the customer's site.

---

## Later

| Situation | What to do |
|---|---|
| A fresh card for the same adapter | Steps 4–7 with the same `THING_NAME`; the identity restores from its own stick |
| Lost or stolen | System → Adapters → *Revoke*, then `bash cloud/adapter-identity.sh revoke adapter-NNNN` (deactivates its certificate) |
| Returned or resold | System → Adapters → *Release* (or the customer: Settings → Cloud adapters → *Remove adapter*); its cameras leave the account, a new Cloud ID is issued for the next customer |
| Moved to another network | Change the Wi-Fi (`sudo nmtui`); cameras that change address are followed by themselves (rematch, every 5 minutes) |
| Something doesn't work | `LAUNCH.md` Part B's checklist and "Known traps"; `bash setup/setup-check.sh` |
