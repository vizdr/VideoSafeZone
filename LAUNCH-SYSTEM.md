# LAUNCH-SYSTEM.md — Bringing up VideoSafeZone: the cloud, then the whole system

Step by step, in two parts: **Part 1** brings up the cloud side (AWS: the API, the web panels, the
directory of accounts, the device role), **Part 2** the whole system on top of it (the first people,
then adapters and their cameras). Adapters themselves — a Raspberry Pi 4B prepared to be activated
with its Cloud ID — are `LAUNCH-ADAPTER.md`.

How this fits with the other guides: `LAUNCH.md` is the detailed runbook these steps point into (Part A
a fresh Pi card, Part C verifying, Part F stopping, Part H every deploy and provisioning step and why);
`DEALER-PANEL-PLAN.md` explains the roles, the directory and the Cloud ID (D6, D15–D18);
`Demo-AWS-Video-revCosts4.md` (the guide) holds the original commands for the AWS base layer.

**The two ways the system can start**, both covered in Part 2:
- **A — with a test adapter:** the first Pi, `adapter-01`, is part of the system from the start,
  activated at a demo customer's site, so the web panels show live cameras right away.
- **B — with no adapter:** the cloud and the web panels run with no adapter activated; the system
  owner or an administrator generates adapters in the System console, and each is brought up with
  `LAUNCH-ADAPTER.md` and activated by its customer.

**Who does what:**

| Person | Does | Where |
|---|---|---|
| Operator (you) | Part 1; provisioning and deploys; makes adapters' AWS identities | a machine with the AWS CLI |
| System administrator | the System console: people, dealers, customers, adapters, prices | the web page |
| System owner | the same, except managing the system's own staff and the cameras themselves (D6) | the web page |
| Dealer | its customers, their plans and holders; activates adapters for them | the web page (Dealer portal) |
| Customer (holder, Super Admin) | activates its adapters with the Cloud ID; adds cameras; Live, Events | the web page |
| Technician | prepares an adapter's Pi (`LAUNCH-ADAPTER.md`) | the Pi |

---

## Before you start

- **A machine to run Part 1 from:** the Raspberry Pi itself (after `LAUNCH.md` Part A it has everything
  but Node), or any Linux machine with bash 4 or later, `git`, `python3`, `zip`, `curl`, `openssl` and the
  AWS CLI v2 — plus Node 24 for building the web panel (`LAUNCH.md`, "The web panel needs Node").
- **An AWS login** with administrator rights in the account: `aws login --remote` prints a URL to open
  in any browser (`LAUNCH.md` A6). Check you are in the right account:

  ```bash
  aws sts get-caller-identity --query Account --output text     # 596633517506 for this deployment
  ```

- **The repository:**

  ```bash
  git clone https://github.com/vizdr/VideoSafeZone.git ~/Projects/VideoSafeZone
  cd ~/Projects/VideoSafeZone                  # every command below runs from here
  ```

- **Which case is yours:** the account **596633517506**, where everything was built → Part 1A (check
  and repair, nothing to create). A **different AWS account** → Part 1B.

---

## Part 1 — The cloud

### 1A. This account: bring it up and check it

Everything exists here. These steps check that what is live is this repository's, and put right what
isn't. Each is read-only unless it says it deploys.

**Step 1 — The functions and the page are this tree's.**

```bash
bash cloud/deploy.sh status          # per function and for the page: "this tree", "HEAD", or "neither"
```

"HEAD" means this tree has changes not deployed yet: deploy them with `bash cloud/deploy.sh lambdas`
and `bash cloud/deploy.sh page`. "neither" means someone deployed from elsewhere: look before you
overwrite (`LAUNCH.md` Part H, "Rolling back"; `--force` only when you mean it).

**Step 2 — The directory database is up to date.**

```bash
bash cloud/deploy.sh db-status              # the server, the videosafezone database, its roles, migrations applied
bash cloud/deploy.sh db-migrate --dry-run   # nothing pending
```

**Step 3 — Sign-in and the tenant check are on.**

```bash
bash cloud/deploy.sh tenancy status         # every camera and clip function enforcing; the API takes vsz-users only
```

**Step 4 — The adapters' role is the narrowed one** (each adapter reaches only its own resources,
`DEALER-PANEL-PLAN.md` Phase 10.2f):

```bash
aws iam list-role-policies --role-name KVSAdapterRole --query PolicyNames --output text
# camera-secrets  events  own-rows  registry-route  streams  uploads
```

**Step 5 — The web page signs in.** Open **https://dugyd3kkt36pw.cloudfront.net** and sign in as the
system administrator (or, while the demo directory exists, as `demo-owner@example.com` — its password is
in `~/.local/state/videosafezone/demo-accounts.txt` on the machine that seeded it). The rail shows
*System*; its tabs — Overview, Dealers, Customers, Users, Adapters, Pricing, Billing — load.

**The cloud is up** when step 1 says "this tree" everywhere, step 2 has nothing pending, step 3 enforces,
step 4 lists those six, and step 5 signs in. Go on to Part 2.

### 1B. A different AWS account (an outline, not a tested path)

The system was built in one account, and part of its base layer exists only as the guide's commands,
typed once. Rebuilding elsewhere is therefore not one script: this is the order and the inventory to
work through, expecting to adapt as you go.

**Step 0 — Change the identifiers that name this account's resources** before running anything:

| Identifier | Where |
|---|---|
| account `596633517506`, region `eu-central-1` | `cloud/provision.sh`, `cloud/deploy.sh`, `cloud/adapter-identity.sh`, every ARN in `cloud/iam/*.json` and `cloud/iot/*.json` |
| the API `svfvg7xh9g` | `client-app/src/config.ts`, `config/adapter.env.example` (`REGISTRY_URL`), `cloud/iam/kvs-adapter-registry-route-policy.json` |
| the app client `4g53lfqkkriopjntkgf240edte` | `client-app/src/config.ts` (it is made by `provision.sh identity`) |
| the page's bucket `vms-demo-client-…`, distribution `E1B12167KKII6B`, its URL | `cloud/deploy.sh`, `cloud/iam/client-bucket-oac-policy.json`, `setup/setup-launch.sh` |
| the evidence bucket `vms-demo-evidence-…` | `config/adapter.env.example` and the `cloud/iam/*.json` that name it |
| the IoT endpoints | `config/adapter.env.example` (`aws iot describe-endpoint`, its header) |
| the PostgreSQL instance `database-coursera-iot`, its security group `rds-lambda-2` | `cloud/provision.sh` (`DB_INSTANCE`, `directory-db`) |

**Step 1 — The base layer, from the guide** (`Demo-AWS-Video-revCosts4.md`):
1. A budget alarm (§1.1).
2. The device identity's base (§6.1–§6.3): the role `KVSAdapterRole` (trusted by
   `credentials.iot.amazonaws.com`), its role alias `KVSAdapterRoleAlias`, the IoT Thing policy
   `KVSAdapterThingPolicy` from `cloud/iot/kvs-adapter-thing-policy.json`.
3. The registry: DynamoDB tables `cameras` (key `cameraId`) and `clips` (key `cameraId`, sort key
   `startTs`); the evidence bucket with its tier-down lifecycle on `clips/` (§9.1).
4. The camera and clip functions from `cloud/lambda/` with their roles' documents from `cloud/iam/`
   (§8.1, §9.2 and the later sections that added each), `clip-to-s3`'s IoT rule
   (`cloud/iot/clip-to-s3-rule.json`).
5. The REST API `kvs-demo-api` with a Cognito authorizer named `CognitoAuthorizer` and the camera and
   clip routes (§8.3); the page's private bucket behind CloudFront with OAC (§8.5). Every
   `provision.sh` step expects the API and that authorizer to exist, and an authorizer needs a user pool
   when it is made: the guide's pool (§8.2) is the one it was made with; `provision.sh tenancy` then adds
   `vsz-users` beside it, and `bash cloud/deploy.sh tenancy old-pool-off` takes the first away.

**Step 2 — The features, with `provision.sh`, in this order** (each with `--dry-run` first; what each
makes is in `LAUNCH.md` Part H):

```bash
bash cloud/provision.sh user-settings
bash cloud/provision.sh camera-settings
bash cloud/provision.sh thumbnails
bash cloud/provision.sh directory-db        # the directory database: layer, function in the VPC, migrations
bash cloud/provision.sh identity            # the user pool vsz-users, its trigger, its app client
bash cloud/provision.sh tenancy             # vsz-users on the API's authorizer; the tenant check in report mode
bash cloud/provision.sh directory-seed YOU@MAIL   # you as system administrator, the demo directory (Part 2, step 1)
bash cloud/provision.sh directory           # the directory API and the System console's routes
bash cloud/provision.sh portal
bash cloud/provision.sh adapters            # Cloud IDs, activation, the adapters' presence rule
bash cloud/provision.sh cameras             # adding cameras from the web page
bash cloud/provision.sh accounts
bash cloud/provision.sh registry            # the adapters' registry route
bash cloud/provision.sh adapter-index
bash cloud/provision.sh camera-secrets
bash cloud/deploy.sh lambdas && bash cloud/deploy.sh page
bash cloud/deploy.sh tenancy enforce
bash cloud/provision.sh narrow --vms-moved  # the narrowed device role -- no other project shares it here
```

The self-checks of `directory`, `portal`, `cameras` and `accounts` sign in as the demo accounts, which
is why the seed comes before them. Then check the result as in 1A.

---

## Part 2 — The whole system

### Step 1 — The first people

On this account this is done already (System → Users lists you). On a new directory:

```bash
bash cloud/provision.sh directory-seed YOU@MAIL --dry-run
bash cloud/provision.sh directory-seed YOU@MAIL
```

It makes **you the system administrator** — Cognito emails you a temporary password — and the **demo
directory**: a *Demo dealer*, a *Demo customer* with the site *Home* and `adapter-01` activated there, and
the demo accounts (`demo-owner`, `demo-dealer`, `demo-holder`, `demo-superadmin`, `demo-user`, and two that
must be refused), their passwords in `~/.local/state/videosafezone/demo-accounts.txt`.

### Step 2 — Sign in and set up the platform

1. Open the page, sign in with the emailed password, choose your own.
2. *Optional:* invite the system owner — System → Users → *Invite someone*: the email, organisation
   *Platform*, role *system owner*. Owners can do everything with dealers, customers, adapters, plans and
   billing, but not the cameras themselves or the system's own staff (D6).
3. Check the price list (System → Pricing): what a plan costs per camera class and storage duration.
   Billing is a demo — invoices are real data, no money moves (D9).

### Step 3, option A — Start with the test adapter `adapter-01`

`adapter-01` is the first Pi. Its identity comes from `setup/setup-identity.sh`, not from the System
console, and the seed made it activated at the Demo customer's site, so there is no Cloud ID to enter.

1. **Its Pi:** set it up, or check it, with `LAUNCH.md` Part A (the defaults in `adapter.env` are
   `adapter-01`'s). On the Pi, every line should pass:

   ```bash
   bash setup/setup-check.sh
   venv-adapter/bin/python3 adapter/device_access.py check
   ```

2. **In the page as the demo holder** (`demo-holder@example.com`): *Live* shows its cameras; select one,
   *Start*, watch it come up, *Record clip*, find it in *Events*, then **Stop** (a running producer costs
   money, `LAUNCH.md` Part F). *Settings → Cloud adapters* shows `adapter-01` online.
3. **As yourself:** System → Adapters shows `adapter-01` activated, with its last-seen time.

From here, real dealers, customers and adapters are added exactly as in option B, beside the test one.
To retire the test setup later: System → Adapters → `adapter-01` → *Release* (its cameras' rows are
marked released and leave the demo customer; a new Cloud ID is issued, so the Pi can be activated again
by a real customer), and disable the demo accounts (System → Users → *Disable*).

### Step 3, option B — Start with no adapter

The seed always makes `adapter-01`'s entry (the provisioning self-checks need the demo directory). For a
system with no adapter in use:

1. **If the first Pi doesn't exist** (a new account, or it is retired): nothing to do — its entry is
   just never online. **If it exists but is not to be used:** System → Adapters → `adapter-01` →
   *Release*. Keep the new Cloud ID if it may become a real adapter later.
2. Disable the demo accounts you don't want (System → Users → *Disable*), or keep them for tests.
3. **Dealers:** System → Dealers → *New dealer*; invite its staff from its row. A customer always has a
   dealer; for direct sales, make a dealer of your own (e.g. *Direct*).
4. **Customers:** Dealer portal → Customers → *New customer* (an administrator or owner chooses the
   dealer): the name, the first site and its time zone, and optionally the holder's email — or later,
   the customer's *Users* → *Invite the holder*. Cognito emails the holder a temporary password.
5. **Adapters:** the next step.

### Step 4 — Adapters (both options)

Each new adapter, in short — the details are `LAUNCH-ADAPTER.md`:

1. **System → Adapters → New adapters** (owner or administrator): how many, a label, optionally the dealer
   that may activate them. Each gets its Thing (`adapter-NNNN`) and its **Cloud ID**, shown once: print the
   labels.
2. **The operator makes its AWS identity**, with their own AWS login:
   `bash cloud/adapter-identity.sh create adapter-NNNN`.
3. **The technician prepares the Pi** with that identity (`LAUNCH-ADAPTER.md` steps 3–7).
4. **The customer activates it** with the Cloud ID: Settings → Cloud adapters → *Add adapter* → *Enter
   Cloud ID*, and the site. The Pi learns it within seconds.
5. **The customer adds its cameras:** *Add cameras* on the web page (the adapter scans its own network),
   or a technician on site through the adapter's admin GUI.

### Step 5 — The system is up when

- Part 1's checks pass (1A).
- At least one adapter shows online in System → Adapters.
- Its customer, signed in, sees its cameras in *Live*; *Start* brings up video in the page, *Record clip*
  puts a clip in *Events*, and *Stop* ends it.
- On its Pi, `device_access.py check` passes and `LAUNCH.md` Part C's `ffprobe` checks show video.

### Day to day

- **Verify** an adapter: `LAUNCH.md` Part C. **Stop** what costs money: Part F. **Deploy** a change:
  Part H (`deploy.sh status` first).
- **Lost or stolen adapter:** System → Adapters → *Revoke*, then
  `bash cloud/adapter-identity.sh revoke adapter-NNNN` (deactivates its certificate). **Returned or
  resold:** *Release*.
- **Never cut a Pi's power, or swap its card, without `sudo poweroff` first.** On 2026-11-15 a card lost
  its last git commit — 19 object files left empty — when the power went two seconds after the commit,
  before the data reached the card.
