# Dealer panel: accounts, roles, tenancy and the dealer / system consoles

**Written:** 2026-11-06, against `master` at `7304279`, after looking at the AWS account read-only
the same day (§2).
**For:** the user, to settle the decisions in §4, and then the sessions that build it.
Update this file as phases complete. When the work is done, fold the outcome into the guide as a
new appendix ("Accounts, roles and tenancy") the way `OUTAGE.md` folds into §16.3c, and keep this
file as the record of why.

**Status:** plan only. Nothing here is built or deployed. **All decisions, D1–D19, were settled
with the user on 2026-11-06:** D1–D10 as proposed; then the amendments to D1 (5-minute tokens), D4
(claims) and D6 (roles), D11–D14 (several adapters, §4b) and D15–D19 (activation with a Cloud ID,
adding cameras from the web page, the sign-in page, the Account page, a tariff plan per adapter —
the user's requirements, §4c), as proposed, with two changes from the user: the screens say
VideoSafeZone (D17), and the Account page has no Opt-ins for now (D18).
**Progress:** Phase 0.1 (sign-up closed, #87 fixed) and 0.3 (deletion protection and IAM
authentication on the database) done 2026-11-06, run by the user. **Phase 1 built and tested
2026-11-06** (not provisioned yet: `bash cloud/provision.sh directory-db`, run by the user).

---

## 1. Goal

Add what requirements §1 calls the Level 1 and Level 2 web panels, beside the Level 3 panel that
exists (`client-app/`, `WEB-PANEL-PLAN.md`):

- **Dealer portal** (Level 2), modelled on Videoloft's Partner Portal (screenshot shared
  2026-11-06): top tabs *Customers · Cloud adapters · Billing · Pricing · Dealer details*; under
  Customers a left menu *Customer list · Plan · Plan history · Cameras · Users*; the **Plan** card
  — one per adapter, since each of a customer's adapters has its own plan (D19) — with camera counts by resolution (2 MP / 4 MP / 8 MP) × recording (motion-triggered / 24/7),
  cloud storage duration on a stepped slider, monthly / annual payment, and the trade price.
- **System console** (Level 1) for system owners and administrators: dealers, all customers,
  users of every role, adapters, the price list, an overview.
- **User management** with four roles — customer, dealer, system owner, system administrator —
  stored in the existing PostgreSQL on AWS (requirements §3: "multi-tenancy: data isolation per
  customer/site, role-based access").
- **Adapter activation with a Cloud ID** (user, 2026-11-06), after Videoloft's *Enter Cloud ID*:
  the system owner generates each adapter with a unique code; a customer, signed in on the
  system's one common sign-in page, activates their adapter with it, and adds cameras only through
  an adapter they activated; their Account page follows Videoloft's (§4c).
- **Billing as an interface only**, with fakes behind it: plans, prices, history and invoices are
  real data; no payment is taken and no payment details are collected.

Copy Videoloft's structure, not its branding (as `WEB-PANEL-PLAN.md` §1 did): every screen says
**VideoSafeZone** where Videoloft's says Videoloft (D17).

What stays the same: static hosting on S3 + CloudFront, API Gateway + Lambda, zero inbound ports on
the Pi, the admin GUI as a separate LAN-only app, the camera registry in DynamoDB as the single
source of truth for cameras.

## 2. What was found in the account (2026-11-06, read-only)

**The PostgreSQL.** One instance, `database-coursera-iot`: RDS for PostgreSQL 18.3 (plain RDS, not
Aurora), `db.t4g.micro`, 20 GB gp2, single-AZ, encrypted, 14-day backups, **deletion protection
off**, **not publicly accessible**, **IAM database authentication off** (both turned on 2026-11-06,
Phase 0.3). It belongs to another
project: the Lambdas `ConnectPostgreeSQL` and `GetTelemetry` use it (both in the VPC, password in
environment variables), and a Secrets Manager secret
`rds-db-credentials/database-coursera-iot/postgres/…` holds the master user's credentials. No
database name was set at creation, so the other project most likely uses the default `postgres`
database. Nothing in this repository or in VMS refers to it.

**Its network.** The account's default VPC (`vpc-0ff0a3c9030f6bbfa`, 172.31.0.0/16, three public
subnets, an internet gateway). **No NAT gateway and no VPC endpoints.** The instance has two
security groups: `rds-lambda-2` (5432 from the two Lambdas' groups `lambda-rds-2` / `lambda-rds-3`)
and `default` (itself, plus 5432 from one /32 address — inert while the instance is private).

Consequence: a Lambda inside that VPC reaches the database and nothing else — not Secrets Manager,
not Cognito, not DynamoDB, not the internet. A Lambda outside the VPC reaches every AWS API but not
the database. Aurora's Data API, which would avoid the VPC, doesn't exist for plain RDS. §4 D3 is
built around this.

**The user pool is shared with VMS, and self sign-up is open.** `kvs-demo-users`
(`eu-central-1_jiAd6UD3S`, Essentials tier, 1 user, no Lambda triggers, MFA off) has two app
clients: this repository's `kvs-demo-web-client` and VMS's `pir-web`; both APIs (`kvs-demo-api`,
`pir-api`) authorize against it. **`AllowAdminCreateUserOnly` is false**: anyone who reads the
client ID from the page's JavaScript can call `SignUp`, confirm the emailed code, sign in, and use
every route of both APIs — every camera, live and recorded. → `FoundAndFixed.md #87`, fixed
2026-11-06 (Phase 0.1). And because pool membership is all either API checks, a customer account created in the
shared pool would also sign in to VMS's page with full access. → D1.

**API.** `kvs-demo-api` (REST, one Cognito authorizer, `Authorization` header = the ID token):
`/cameras`, `/cameras/{audio,mode,outage-buffer,settings}`, `/clips`,
`/clips/{delete,play,record,tier}`, `/cmd`, `/hls`, `/me/settings`. No Lambda checks who the caller
is beyond `user-settings` reading the `sub`.

## 3. Ground rules

- **The registry contract (shared with VMS) holds:** new fields only, written field by field; no
  new value in an existing enum; no credential in any answer; an unregistered camera gets 409; every
  new reader skips unregistered rows.
- **Shared AWS account:** never deploy a `pir-*` resource. New resources get a `vsz-` prefix so it
  is plain who owns them. IAM policy documents in `cloud/iam/` match what is deployed (#41). VMS
  must keep working at every step: nothing here changes `pir-api`, the `pir-web` client, or VMS's
  sign-in, except closing the open sign-up (Phase 0.1), which protects VMS too.
- **The database belongs to another project too.** Our tables go in a database of our own on that
  instance; our logins are capped in connections; we never change the master user (in particular,
  never `GRANT rds_iam TO postgres` — that would force IAM login on the other project and lock it
  out). Changes to the instance itself (IAM auth, deletion protection) only with the user's OK.
- **Passwords live in Cognito only.** PostgreSQL holds the directory — who belongs to which
  organisation, with which role, and the plans — never a password or a token.
- **The server decides, the page only hides.** Every permission is checked in a Lambda; the page
  hides what a role can't use, for convenience, never as the check. Missing or unknown claims mean
  "no access" (fail closed).
- **Cost:** nothing that bills by the hour is added (no NAT gateway, no endpoints, no RDS Proxy,
  no second database instance); nothing here can start a producer.
- **Bugs** go to `FoundAndFixed.md`; a fix in Python logic gets a `test_ffNN_` regression test.

## 4. Decisions (settled 2026-11-06)

Accepted by the user as proposed, all ten.

**D1. A user pool of this repository's own: `vsz-users`.** Not the shared `kvs-demo-users`, for
three reasons found in §2: (a) any user in the shared pool can sign in to VMS's page with full
access, so a customer account there would leak across projects; (b) the role claims come from a
sign-in trigger (D4), and a trigger on the shared pool would put this repository's database on
VMS's sign-in path; (c) the new pool can be created right — admin-created users only, sign-in with
the email address (`UsernameAttributes=email`, which can't be changed after creation), ID tokens of
**5 minutes**, the shortest Cognito allows — the window in which a removed permission still works
and a newly shared camera is still missing; the page renews two minutes before expiry, so changes
show within about 3 minutes (Videoloft says "up to 2"). *Amended 2026-11-06 from 15 minutes, with
D18.* Lite tier is enough: adding claims to the ID token works on every tier; Essentials
adds access-token claims, which this design doesn't use. Invitations use Cognito's own email
(temporary password), which has a low daily limit — enough for a demo; SES later.
`kvs-demo-api`'s authorizer accepts both pools during the switch (Phase 3), then only `vsz-users`.
The old pool stays for VMS, untouched.
*Considered:* the shared pool with a trigger that skips `pir-web` sign-ins — rejected, (a) remains.

**D2. The directory lives in its own database on the existing instance.** `CREATE DATABASE
videosafezone` on `database-coursera-iot`, schema `vsz`. Two login roles, both IAM-authenticated
(D3), neither the master user: `vsz_migrator` (owns the schema, runs migrations) and `vsz_app`
(reads and writes rows, no DDL), with `CONNECTION LIMIT` 10 and 3 so we can't starve the other
project of the micro instance's connections (the default formula gives roughly 100 for 1 GiB;
check `SHOW max_connections` in Phase 1). Migrations are numbered SQL files in `cloud/db/migrations/`,
applied in order and recorded in a `schema_migrations` table.
*Cost:* the instance is already running and paid for, so the directory adds storage only.
*Risk, accepted for a demo:* sign-in now depends on a single-AZ micro instance shared with another
project (maintenance reboots, minor-version upgrades, or someone deleting the course project).
Phase 0.3 proposes deletion protection. A separate instance later is a connection-string change.
*Considered:* DynamoDB for the directory (as `WEB-PANEL-PLAN.md` D4 did for user settings) —
dealer → customers → plans → history are relational questions, and the user asked for the
PostgreSQL; `user-settings` stays in DynamoDB.

**D3. Two Lambdas inside the VPC, everything else outside — no NAT.**

```
 Browser (client-app, one build for every role)
   │  InitiateAuth / RespondToAuthChallenge (USER_PASSWORD_AUTH, refresh)
   ▼
 Cognito pool vsz-users ── pre-token trigger ──►┐
   │  ID token + vsz_role, vsz_org, vsz_adapters │      default VPC (no NAT)
   ▼                                             ▼    ┌──────────────────────────────────────┐
 API Gateway kvs-demo-api (Cognito authorizer)  vsz-directory ──IAM token, TLS──► PostgreSQL │
   │                                             ▲    │  (fixed operations,     db videosafezone
   ├─ /cameras /hls /cmd /clips … ─► camera Lambdas   │   no raw SQL in)                     │
   │      (as today + tenant check, D4)          │    └──────────────────────────────────────┘
   └─ /directory/{proxy+} ─► vsz-directory-api ──┘ lambda:Invoke
                              │ (outside the VPC)
                              ├─► Cognito admin API (invite, disable, sign out, reset)
                              └─► DynamoDB cameras (inventory, read-only, no credentials)
   vsz-db-admin (in the VPC): bootstrap and migrations, invoked only from cloud/provision.sh
```

- **`vsz-directory`** (in the VPC, security group `vsz-db-client`) is the only code that runs SQL
  for the app. It serves two callers: Cognito's pre-token trigger, and `vsz-directory-api` by
  synchronous invoke. It exposes a fixed list of operations (`list_customers`, `save_plan`, …),
  each taking the caller ("actor") and enforcing the scope in its own `WHERE` clauses, with
  parameters only — no SQL text crosses the invoke. Only Cognito (source ARN = the pool) and
  `vsz-directory-api`'s role may invoke it. Its role's `CONNECTION LIMIT` caps its connections.
  (Reserved concurrency was the plan; found in Phase 1: the account's total Lambda concurrency is
  10, and AWS keeps all 10 unreserved, so no function here can reserve any — §9.)
- **`vsz-directory-api`** (outside the VPC) serves `/directory/*`: checks the caller's claims,
  calls the Cognito admin API, reads the camera registry, and asks `vsz-directory` for everything
  in PostgreSQL. CORS headers on every answer, errors included (#8).
- **`vsz-db-admin`** (in the VPC, connects as `vsz_migrator`) applies migrations. It is invoked
  only by `provision.sh` / `deploy.sh` with the user's own credentials.
- **Credentials without Secrets Manager:** IAM database authentication. A Lambda signs a
  15-minute login token with its own role (`rds.generate_db_auth_token` is a local signature, no
  network call, so it works without a NAT) and connects over TLS, verifying against the RDS CA
  bundle shipped in the layer. No password is stored anywhere. This needs IAM auth switched on for
  the instance (additive: the other project's password logins keep working) — the AWS docs don't
  say whether that restarts the instance, so do it at a quiet hour (Phase 0.3).
- **Driver:** `pg8000` (pure Python, so no x86/arm build question), with its dependencies pinned
  by hash, as a Lambda layer `vsz-pg` built by `provision.sh`; the function code stays one file
  each, as `deploy.sh` expects.

*Why not the alternatives:* a NAT gateway (roughly $35–40 a month before data) or interface
endpoints (about $9 a month each per AZ) would let one Lambda do everything from inside the VPC;
RDS Proxy adds tens of dollars a month for connection pooling this load doesn't need; making the
instance public would expose another project's database.

**D4. Who may do what travels in the ID token; every Lambda checks it.** The pre-token trigger
(event V1 — ID token, string claims) looks the user up by `sub` and adds:

| Claim | Value |
|---|---|
| `vsz_role` | `system_admin` · `system_owner` · `dealer` · `customer` — the side a person is on; a customer's role *in each account* is in the directory (D18) |
| `vsz_org` | the platform's or the dealer's UUID; absent for customers, who can belong to several accounts (D18) |
| `vsz_adapters` | full access: comma-separated Thing names of the adapters of every account where the user is holder or Super Admin (`adapter-01`); `*` for `system_admin`; absent otherwise |
| `vsz_cameras` | view only: the cameras shared with the user, as `cameraId@thing` (`cam-07@adapter-0002`), at most 100 (D18) |

A user who isn't in the directory, is disabled, or whose organisation is suspended gets **no
token** — the trigger refuses the sign-in. The trigger runs again on every refresh, so a change
reaches the token within minutes (D1); a disable also calls `AdminUserGlobalSignOut`.

The camera Lambdas don't touch PostgreSQL: each camera row gets a new field **`adapterId`** (the
Thing that serves it), and a check copied into each Lambda — `tenant_allows(claims, row, need)`,
identical everywhere, kept so by `tests/cloud/test_lambda_twins.py`. *View* (list, live, HLS, the
clip list and playing a clip) passes when `row.adapterId` is in `vsz_adapters` or
`cameraId@adapterId` is in `vsz_cameras`; *control* (Start/Stop, IR, every setting, recording,
deleting or re-tiering a clip) only on `vsz_adapters`. Tying a share to its adapter means a
released adapter (D15) ends its shares too. A camera the caller may not see gets **the same answer
as an unknown camera**, so the API doesn't confirm other tenants' camera IDs. `list-cameras`
filters, and says per camera whether the caller may view or control it; the clip routes find the
camera through the clip's row.

`adapterId` is written by the admin GUI at registration and re-registration (it knows its own
Thing name from `adapter.env`), and stamped by `rematch_cameras.py` — conditionally, only where
missing, only on rows whose MediaMTX path this Pi serves — so rows registered from VMS's card get
it too. A row without `adapterId` is visible to system administrators only, as "unassigned".

**D5. Video belongs to the customer.** Customers see their own cameras' live view, recordings and
clips; system administrators see everything (support). **Dealers and system owners don't see
video or clips.** They see the camera inventory of the customers in their scope — name, model,
codec, resolution, recording mode, last thumbnail time — but not the thumbnail itself, since that
is a picture of the customer's premises. A per-customer "allow my dealer to view" switch is a later
addition.
*Alternative:* dealers see their customers' video by default — simpler for support, worse for
privacy, and hard to take back once customers are on.

**D6. Roles.** Four, as asked — and with D18 the customer side in three, after
Videoloft's Additional users: the account **holder** (`customer`), a **Super Admin**
(`customer_admin`), and a **user** who sees only the cameras shared with them (`customer_user`).
The schema keeps a role per membership, so more can be added later (`dealer_staff`) without a
migration of the model.

| Can… | Holder | Super Admin | User | Dealer | System owner | System admin |
|---|---|---|---|---|---|---|
| Live, Events of the account's cameras | ✔ | ✔ | shared ones | — | — | ✔ all |
| Camera settings, Start/Stop, record / delete clips | ✔ | ✔ | — | — | — | ✔ all |
| Own Account details (§4c D18) | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| Activate an adapter (Cloud ID); remove it from the account (§4c) | ✔ | ✔ | — | activate, for its customers | ✔ | ✔ |
| Add and remove cameras through an activated adapter (§4c) | ✔ | ✔ | — | — (on site: the LAN admin GUI) | — | ✔ |
| Additional users: add, Super Admin, share cameras, remove (D18) | ✔ | ✔ (not the holder) | — | — | — | ✔ |
| Plan & Billing: each adapter's plan and history, invoices (D19) | ✔ | — | — | its customers | all | all |
| Edit an adapter's plan (D19) | — | — | — | its customers' | all | all |
| Invite the holder; disable, reset a customer's users | — | — | — | its customers' | all | all |
| See the camera inventory | own | own | — | its customers | all | all |
| Create, edit, suspend customers | — | — | — | its own | all | all |
| Edit own company details | — | — | — | ✔ | — | ✔ |
| Create, suspend dealers; dealer users | — | — | — | — | ✔ | ✔ |
| Edit the price list | — | — | — | read | ✔ | ✔ |
| Overview, billing runs (fake) | — | — | — | own invoices | ✔ | ✔ |
| Create system owners and admins | — | — | — | — | — | ✔ |
| Generate adapters and Cloud IDs; allocate to dealers; release (§4c) | — | — | — | — | ✔ | ✔ |

*Amended 2026-11-06* (settled with §4c): adapters are generated by owners and admins and
activated with their Cloud ID by the customer (or its dealer), instead of being registered by an
admin and installed by the dealer; a customer account has a holder, Super Admins and users with
shared cameras, and a person can belong to several accounts (D18).

**D7. One web app; the consoles are role-gated routes in `client-app/`.** Like Videoloft, where the
Partner Portal is an item on the same rail. One sign-in, one build, one deploy, the same CI and
theme. Rail: *Live · Events · Settings* (customers, admins), *Dealer portal* (dealers, owners,
admins), *System* (owners, admins), *Account*, *Exit*. A dealer lands on the portal. The consoles
are lazy-loaded chunks, so a customer's first load doesn't grow. Hash routes as before
(`#/dealer/customers/…/plan`, `#/system/dealers`). Label "Dealer portal" (Videoloft says "Partner")
— the user's call.

**D8. The directory API is one proxy route.** `/directory/{proxy+}` with `ANY` (Cognito authorizer)
and an explicit `OPTIONS` (mock, for the preflight), into `vsz-directory-api`, which routes inside.
One resource to create instead of about twenty; `provision.sh directory` creates it and checks it
as it does the others.

**D9. Billing: data real, money fake.** Prices, plans, plan history and invoices are tables; plans
are per adapter (D19); the price is computed on the server from the price list and stored with
each plan version. A
`BillingProvider` interface (`quote`, `start`, `change`, `cancel`, `list_invoices`) has one
implementation now, `FakeBillingProvider`, which writes invoices marked `provider = 'fake'` when an
admin runs a billing period. The Billing tab carries a banner — "Demo billing: no payment is
taken". **No card or bank fields at all**, not even fake ones. Plans aren't enforced yet: the
camera counts and the storage duration don't limit anything; the Cameras page shows each
adapter's plan vs. actual as a hint. A real provider (e.g. Stripe Billing) replaces the fake later behind the same
interface.

**D10. Left out for now.** From the Videoloft screen: *Getting started*, *Materials*, Analytics
add-ons (the price model can hold add-ons; none is offered while no analytics exists), "Cancel
subscription" as anything more than a plan status. Also: audit-log screens (struck in the
requirements; a minimal `audit_event` table is still written, §5, because "who changed this plan"
is needed for plan history anyway), MFA (later, for system roles first), white-labelling. Running
several adapters is not left out: it has its own sections, §4b and §4c, and Phases 6, 7 and 10.

## 4b. Several adapters, several dealers (settled 2026-11-06)

The user asked (2026-11-06) whether the plan considers several adapters used by several dealers.
**Partly.** The directory and the permissions do: an adapter is a row with a dealer (whose stock
it is) and a site (where it is installed); a dealer sees only its own adapters and customers; a
customer's token lists all of its adapters (D4), so several sites and adapters per customer work.
What the plan had only as a "Later" line is *running* a second adapter, and the code assumes one
in more places than that line said:

| # | Today | With a second adapter |
|---|---|---|
| A1 | Camera IDs are typed by the installer in the admin GUI, `^cam-\d{2}$` — 100 IDs for the whole system | IDs run out, and two installers pick the same one |
| A2 | Registering an ID that exists takes the **re-register** path, which updates that row's host, RTSP URL and credentials | an installer at customer B silently takes over customer A's camera |
| A3 | Every Pi service reads the whole `cameras` table: `sync_mediamtx_paths`, `rematch_cameras`, `schedule_runner`, `thumbnails`, `outage_buffer`, `event_watcher`, `codec_caps`, the admin GUI | each Pi adds MediaMTX paths for the other Pi's cameras (private addresses that may hit another device on its own LAN), subscribes to their events, lists them |
| A4 | All adapters get one IAM role through `KVSAdapterRoleAlias`: `Scan`/`PutItem`/`UpdateItem` on the whole table, `PutMedia` on every `cam-*` stream, `PutObject` on every thumbnail | any adapter — or anyone who copies a certificate off a Pi at a customer's premises — reads every customer's camera passwords and RTSP URLs, overwrites their rows, pushes into their streams |
| A5 | `publish-cmd` sends every command to `adapter/adapter-01/cmd` (`THING_NAME`); the role's event-publish permission names `adapter-01` | the second adapter's cameras can't be controlled from the cloud |
| A6 | `cam-01` (USB) and `cam-02` run as their own named units, with `publish-cam01.sh` built for one USB camera | a USB camera on a second adapter has no ID scheme or unit |
| A7 | One identity, made by hand (guide, `setup-identity.sh` backs up and restores it) | no way to make the next one from the System console |

Already right: the IoT Thing policy scopes MQTT by `${iot:Connection.Thing.ThingName}`, so one
policy serves every Thing and each Pi only sees its own topics; S3 clip keys and KVS stream names
follow the camera ID, which stays globally unique.

Two AWS facts make per-adapter isolation possible (checked 2026-11-06 in the AWS docs, not yet
tried): credentials from the IoT credentials provider carry `credentials-iot:ThingName` into the
role's IAM policy when the device sends its Thing name (kvssink does), and KVS streams accept
`aws:ResourceTag` conditions. DynamoDB can't do the same for the `cameras` table: IAM can restrict
rows only by their partition key (`dynamodb:LeadingKeys`), and the key is `cameraId`.

**D11. Camera IDs are allocated, not typed.** Whoever registers a camera — the web page's
add-camera route (§4c D16) or the admin GUI — takes the next free ID with a conditional write
(`attribute_not_exists(cameraId)`), so two registrations can't take the same one. The pattern
widens to `^cam-\d{2,4}$` in this repository (admin GUI, `provision-camera.sh`, the sudo rule in
`setup-system.sh`, the Lambdas' checks); the stream names stay inside the `stream/cam-*` IAM
wildcard. Re-register only touches a row whose `adapterId` is this Pi's (fixes A1, A2).

**D12. Each Pi acts only on its own rows.** Every reader in A3 skips rows whose `adapterId` isn't
this Pi's Thing name — the same pattern and the same list as the unregistered rule. Rows without
`adapterId` are adopted only by a Pi whose `adapter.env` says `ADOPT_UNSTAMPED_ROWS=yes`
(`adapter-01`), and stamped as they are adopted. The Thing name comes from `adapter.env`, never
from code (fixes A3).

**D13. Each adapter's credentials reach only its own resources** (fixes A4, A5):
- **Camera secrets leave the table.** ONVIF passwords and the credentials in `rtspUrl` /
  `liveRtspUrl` move to SSM Parameter Store `SecureString`s under
  `/vsz/adapters/<thing>/cameras/<cameraId>`, readable only by that Thing
  (`${credentials-iot:ThingName}` in the role policy). This is the credential storage
  CLAUDE.md already plans as "Phase 2" (`credentialRef`).
- **Streams:** tagged `adapterId=<thing>` at creation; `PutMedia` and the rest only where the tag
  matches the caller's Thing.
- **S3:** thumbnails and outage uploads under a `<thing>/` part of the key, allowed by prefix.
- **Registry writes from the Pi** go over MQTT to `adapter/<thing>/registry` (the Thing policy
  already confines a Pi to its own topics); an IoT rule hands them to a Lambda that applies them
  field by field, only to rows of that Thing. The device role keeps reading only — ideally a
  `Query` on an `adapterId` index restricted with `dynamodb:LeadingKeys`, if that holds for a
  global secondary index (to verify).
- `publish-cmd` sends to `adapter/<row.adapterId>/cmd`.
A short spike comes first: tag-conditioned `PutMedia` from kvssink, the `credentials-iot:ThingName`
variable, and `LeadingKeys` on an index, each tried on a test stream / table.

**D14. Several customers' adapters and VMS don't share the camera table or the device role.** VMS
reads the same `cameras` table and uses the same role alias, with no tenancy: once a second
adapter serves a real customer, VMS's page would list that customer's cameras, and D13's narrower
device role would break VMS's admin GUI, which writes the table directly. So before a second
adapter goes to a customer, VMS either gets copies of its own (its own table and role alias) or
its card is retired. Until then, a second adapter is for testing only, with test cameras. Settled
2026-11-06 (the user agreed); which of the two is decided when Phase 10 starts.

**D15** moved to §4c and was rewritten for activation with a Cloud ID (user, 2026-11-06). New
adapters take ONVIF cameras first; a USB camera on a new adapter (A6) needs its own allocated ID
and a template unit — later.

## 4c. Cloud ID, adding cameras, sign-in, Account, a plan per adapter (settled 2026-11-06)

**The user's requirements (2026-11-06)**, with Videoloft's *Enter Cloud ID*, sign-in and *Account*
screens as the model: every adapter is activated with a unique code that the system owner
generates; users sign in on one common page of the system; after signing in, a user can add
cameras only with the code of their adapter; the user's account looks like Videoloft's Account
page. Sign-in with Microsoft or Apple (SSO) is left out for now, on both pages.

Read as: the Cloud ID is entered **once per adapter**, to link it to the customer's account;
afterwards every camera is added through one of the customer's activated adapters, without the
code again. (If the code should be asked for every time a camera is added, D16 changes in one
place: the add-camera route would take the code.)

```
System owner (System console)        Technician                      Customer (web page)
New adapters ─► Thing, certificate    bundle onto the Pi              signs in (common page)
             ─► Cloud ID K7QM4-XRPZ9  (setup-identity.sh restore)     Add adapter ─► Cloud ID
             ─► label + bundle, once  Pi connects: "last seen"          ─► activated at a site
Allocate to a dealer (optional)                                       Add cameras ─► the adapter
                                                                        scans its LAN ─► pick,
                                                                        credentials ─► registered
```

**D15. Adapters are generated with a Cloud ID and activated with it.** (Replaces §4b's first
D15, where an admin registered adapters and dealers installed them.)
- *New adapters* (system owner or admin, a batch of N): for each, the System console makes the
  Thing (`adapter-NNNN`, numbered by the directory), a certificate attached to it and to the
  existing Thing policy — whose `${iot:Connection.Thing.ThingName}` scoping already fits any
  Thing — and the Cloud ID. It shows the codes, a printable label per adapter and each identity
  bundle (the four files of `setup-identity.sh`, plus the `THING_NAME` line for `adapter.env`)
  **once**; the bundle crosses the API over HTTPS and is stored nowhere, as AWS never shows a
  private key twice either. The technician puts it on the Pi with `setup-identity.sh restore`.
- *The Cloud ID:* 10 characters of Crockford base32 (no I, L, O or U; case doesn't matter), shown
  as `K7QM4-XRPZ9` — about 50 bits, far beyond guessing under the rate limit below. Generated in
  `vsz-directory` and stored only as an HMAC keyed from that function's environment, so a copy of
  the database doesn't reveal the codes. Single use: activation clears it. A lost label gets a new
  code (the old one stops working); a code is never shown twice.
- *Allocation* (optional, owner or admin): an adapter allocated to a dealer is activated only by
  that dealer's customers — or by the dealer, for one of them; one not allocated, by any customer,
  and it joins the stock of that customer's dealer. Customers always belong to a dealer; direct
  sales are a dealer of the platform's own ("Direct").
- *Activation* (the holder or a Super Admin, the dealer for a customer, or admin): the code, then the site — one of
  the customer's, or a new one (name, time zone). The adapter needn't be online. Five wrong codes
  per user per hour, and twenty per source address, then refused for the hour; every failure
  answers the same "this Cloud ID can't be activated", with no hint whether it exists. Afterwards
  the page renews its token, so the adapter and its cameras appear at once (D4's trigger runs on
  every renewal), and the API publishes a **retained** message on `adapter/<thing>/config`
  (`{"activated": true}`), which the Pi receives whenever it connects.
- *Release* (owner or admin; an adapter returned or resold): it leaves its customer; its camera
  rows lose `adapterId` and get `releasedFrom` (visible to admins only, D4; never adopted again
  under D12's `ADOPT_UNSTAMPED_ROWS`), so the next customer starts with no cameras and can't see
  the previous customer's recordings, and the shares of its cameras end (D18); a new code is
  issued. *Remove from the account* (holder or Super Admin) is a release too; activating it again
  takes a new code from an owner or admin. *Revoke* (lost or stolen) also deactivates its
  certificate.
- *Last seen:* IoT lifecycle events (connected / disconnected) through an IoT rule into
  `vsz-directory`.
- `adapter-01` is seeded as activated at the demo customer's site, without a code.
*Considered:* AWS IoT fleet provisioning by claim, where a Pi makes its own certificate on first
boot — no bundle to copy, but more AWS pieces (a provisioning template, a claim certificate, a
pre-provisioning hook); later, behind the same Cloud ID screen.

**D16. Cameras are added from the web page, through the adapter.** The browser can't reach the
customer's LAN and WS-Discovery works only on it, so the page asks the adapter, over the Pi's own
outbound MQTT session — the zero-inbound-ports rule holds:
1. `POST /adapters/<thing>/discover` — Lambda `adapter-cameras` on `kvs-demo-api`, outside the VPC,
   refusing unless `<thing>` is in the caller's `vsz_adapters` — sends `discover` to
   `adapter/<thing>/cmd`.
2. The agent runs the discovery the admin GUI runs (`onvif_discovery.py`) and publishes the result
   to `adapter/<thing>/discovery`; an IoT rule stores it in DynamoDB `adapter-discovery` (key =
   Thing, expiring after 15 minutes); the page polls `GET /adapters/<thing>/discovery`. Cameras
   already registered are marked (by `onvifEndpointRef`). An adapter that doesn't answer within a
   minute is reported as offline.
3. `POST /adapters/<thing>/cameras` with `{endpointRef, user, password, displayName}`: the Lambda
   allocates the camera ID (D11), stores the request — password included — in a short-lived item,
   and sends `register` with only the request's ID. The agent fetches the request, registers the
   camera exactly as the admin GUI does (the registration code moves into
   `adapter/camera_registration.py`, shared by both), deletes the request and reports the result;
   the page shows progress, then the camera in Live and Settings. The password is in no MQTT
   message and no log. Until D13 it ends up in the registry row, as today; after D13, in SSM.
4. `publish-cmd` sends every command to `adapter/<row.adapterId>/cmd` (A5).
5. *Remove a camera* (holder or Super Admin): `DELETE /adapters/<thing>/cameras/<cameraId>` sends
   `unregister`, and the agent unregisters it as the admin GUI does — the soft mark; the row,
   recordings and settings stay, and Re-register in the admin GUI brings it back.
The agent runs as the admin GUI's user, so the sudo rule for `provision-camera.sh` covers it.
**The LAN admin GUI stays** for the technician — local settings, codec, preview, diagnostics — but
registers a camera only once the adapter is activated (it reads the retained config). So every
camera, whichever GUI adds it, belongs to an activated adapter and through it to a customer.
Dealers don't add cameras from the web page (D5); on site, a technician uses the admin GUI.

**D17. One sign-in page for everyone**, laid out like Videoloft's but **VideoSafeZone**'s: the
name VideoSafeZone (today's sign-in page already shows it) and its logo, *Email address*, *Password* with show/hide, *Forgotten password?* (a code by email, then a new password),
*Log in*. The first sign-in of an invited user asks for a new password. After sign-in the page goes
where the role belongs (D7). SSO with Microsoft or Apple is left out: it needs Cognito's hosted
sign-in (the OAuth code flow) and app registrations with both — later, on this page and as the
Account page's "Convert to SSO" card. Accounts are still made by invitation (D1); letting someone
*sign up* with an unused Cloud ID instead would be a later option, and the user's call.
Throughout, every screen says VideoSafeZone where Videoloft's says Videoloft — "a VideoSafeZone
account", never Videoloft's name (user, 2026-11-06).

**D18. The Account page, after Videoloft's.** The rail's *Account*; tabs:
- **Account details** (every role): *Email* with Edit — Cognito sends a code to the new address,
  and until it is confirmed the old one keeps working (`AttributesRequireVerificationBeforeUpdate=email`
  on the pool); the directory takes the confirmed address from the token at the next renewal.
  *Password* `********` with Edit — current and new (`ChangePassword`). No SSO card (D17).
- **Plan & Billing** (account holder): for each of the account's adapters, its current plan,
  read-only (cameras by class, storage duration, payment interval, price), and its history (D19);
  the total; the fake invoices with D9's banner. Changes go through the dealer.
- **Additional users** (holder and Super Admins), after Videoloft's, in three cards:
  - *Add user*: **Email**; **Super Admin** — "full access to all cameras of the account; can also
    add and remove cameras and cloud adapters, and manage users"; with the switch off, the cameras
    to share, ticked from the account's list; *Save* / *Cancel*; "Changes take effect within about
    3 minutes" (D1). Beside the email: "If the person already has a VideoSafeZone account, they get access
    straight away; if not, they get an email asking them to set a password."
  - *Users*: everyone with access — "Super Admin" or the cameras shared — each with *Edit* (the
    switch and the cameras) and *Remove*; "You haven't shared cameras with anyone yet" when empty.
  - *Pending invites*: people invited who haven't signed in yet, with *Resend* and *Cancel*; "No
    pending invites" when empty.
- **Opt-ins** — Videoloft's fourth tab — is left out for now (user, 2026-11-06).
Dealers, owners and admins see Account details only (a dealer's company is in the
portal's Dealer details).
**Three customer roles, several accounts per person** (amends D6). `customer` — the account
holder, one per account, made by the dealer's first invitation: the only one who sees Plan &
Billing; a Super Admin can't remove or change them; the holder can hand the role over.
`customer_admin` — Videoloft's *Super Admin*: everything on the account except Plan & Billing.
`customer_user` — Live and Events of the cameras shared with them, nothing else of the account.
A person can be in several accounts — their own, and others that shared cameras with them; their
camera list shows all of it, grouped by account and site, and the Additional users tab gets an
account picker when they administer more than one. A person is on one side only: an address of a
dealer's or the platform's staff can't be added to a customer account. Per-feature permissions
(live only, no recordings) and an email telling someone they were added (SES) are later.

**D19. One tariff plan per adapter** (user, 2026-11-06). A customer may have several adapters;
each has its own set of cameras and its own, independent plan.
- *Cameras:* a camera belongs to exactly one adapter — the one it was added through (`adapterId`,
  D4, D16). Moving a camera to another adapter means adding it there as a new camera; the old
  one's recordings stay under its old ID until their retention ends.
- *Plans* belong to an adapter's **activation** — new table `adapter_activation`, one row from an
  adapter's activation for a customer until its release — not to the customer, and not to the bare
  adapter. So an adapter resold to another customer starts afresh, and the first customer keeps its
  plan history. Each plan has its own camera counts (class × recording), storage duration, payment
  interval and price: two adapters of one customer can differ in all of them.
- *Lifecycle:* activation starts version 1 as a trial (its length from `price_setting`) with no
  cameras; the dealer (or an owner or admin) sets the plan; *Cancel subscription* ends that
  adapter's plan; a release ends its activation and its plan.
- *Plan vs. actual* is per adapter: its cameras against its plan.
- *Totals:* the dealer's customer list and the customer's Plan & Billing add the adapters up. An
  invoice is one per customer per period, with a line per adapter's plan (`invoice_line`), so a
  customer gets one bill.
- *Names:* the customer names each adapter at activation ("Garage"); it is shown with its site
  wherever adapters are listed — the camera list groups by site, then adapter.
- When plans are enforced (later), that is per adapter too: camera limits count that adapter's
  cameras, and its cameras' KVS retention follows its plan's storage duration.

## 5. Data model (PostgreSQL, schema `vsz`)

First migration, `cloud/db/migrations/001_directory.sql` — the shape, not the final text. **The
migration file is authoritative** (built 2026-11-06): it also holds the triggers that keep the rules
below, `plan.interval` became `billing_interval` (`interval` is a type name in SQL), and the seeds
include the platform's own dealer, "Direct" (D15).

```sql
CREATE TABLE org (                        -- platform → dealers → customers
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind        text NOT NULL CHECK (kind IN ('platform','dealer','customer')),
  parent_id   uuid REFERENCES org(id),    -- customer → its dealer; dealer → the platform
  name        text NOT NULL,
  reference   text,                       -- the dealer's own reference for a customer
  email       text, phone text, address jsonb,
  status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','closed')),
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  CHECK ((kind = 'platform') = (parent_id IS NULL))
);
CREATE TABLE app_user (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  cognito_sub     uuid UNIQUE,            -- set when the Cognito user is created
  email           text NOT NULL,
  name            text,
  status          text NOT NULL DEFAULT 'invited' CHECK (status IN ('invited','active','disabled')),
  created_at      timestamptz NOT NULL DEFAULT now(),
  last_sign_in_at timestamptz
);
CREATE UNIQUE INDEX app_user_email ON app_user (lower(email));
CREATE TABLE membership (                 -- staff: one; customers: one per account (D18)
  user_id  uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
  org_id   uuid NOT NULL REFERENCES org(id),
  role     text NOT NULL CHECK (role IN ('system_admin','system_owner','dealer',
                 'customer','customer_admin','customer_user')),  -- holder, Super Admin, user (D18)
  PRIMARY KEY (user_id, org_id)
);
CREATE UNIQUE INDEX one_holder ON membership (org_id) WHERE role = 'customer';
CREATE TABLE site (                       -- a customer's location
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_id uuid NOT NULL REFERENCES org(id),
  name text NOT NULL, timezone text NOT NULL, address jsonb
);
CREATE TABLE adapter (                    -- an IoT Thing running the adapter
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  thing_name     text NOT NULL UNIQUE,    -- 'adapter-01' = cameras.adapterId
  cloud_id_hmac  bytea UNIQUE,            -- the Cloud ID, keyed hash only; NULL once used (D15)
  status         text NOT NULL DEFAULT 'provisioned'
                 CHECK (status IN ('provisioned','activated','released','revoked')),
  dealer_id      uuid REFERENCES org(id), -- whose stock it is (NULL = not allocated)
  certificate_id text,                    -- the IoT certificate made with it
  last_seen_at   timestamptz,             -- IoT lifecycle events (D15)
  label text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE adapter_activation (         -- an adapter at a customer, activation to release (D19)
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  adapter_id   uuid NOT NULL REFERENCES adapter(id),
  customer_id  uuid NOT NULL REFERENCES org(id),
  site_id      uuid NOT NULL REFERENCES site(id),
  name         text,                      -- the customer's name for it ("Garage")
  activated_at timestamptz NOT NULL DEFAULT now(), activated_by uuid REFERENCES app_user(id),
  released_at  timestamptz
);
CREATE UNIQUE INDEX one_open_activation ON adapter_activation (adapter_id) WHERE released_at IS NULL;
CREATE TABLE activation_attempt (         -- the Cloud ID rate limit (D15)
  at timestamptz NOT NULL DEFAULT now(), user_id uuid, source_ip inet, ok boolean NOT NULL
);
CREATE TABLE camera_share (               -- what a customer_user may view (D18)
  account_id uuid NOT NULL REFERENCES org(id),
  user_id    uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
  camera_id  text NOT NULL,               -- cameras.cameraId
  adapter_id uuid NOT NULL REFERENCES adapter(id),  -- a release ends the share (D15)
  PRIMARY KEY (user_id, camera_id)
);
CREATE TABLE price_camera (               -- the price list: one row per combination
  resolution     text NOT NULL CHECK (resolution IN ('2mp','4mp','8mp')),
  recording      text NOT NULL CHECK (recording IN ('motion','continuous')),
  retention_days int  NOT NULL,
  cents_month    int  NOT NULL,
  currency       text NOT NULL DEFAULT 'EUR',
  PRIMARY KEY (resolution, recording, retention_days)
);
CREATE TABLE price_setting (key text PRIMARY KEY, value jsonb NOT NULL);  -- annual discount, trial days
CREATE TABLE plan (                       -- per adapter activation; every change a new version (D19)
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  activation_id  uuid NOT NULL REFERENCES adapter_activation(id),
  version        int  NOT NULL,
  status         text NOT NULL CHECK (status IN ('trial','active','cancelled','ended')),
  retention_days int  NOT NULL,
  interval       text NOT NULL CHECK (interval IN ('monthly','annual')),
  cents_period   int  NOT NULL,           -- computed by the server from the price list
  currency       text NOT NULL,
  trial_ends_at  timestamptz,
  created_by     uuid REFERENCES app_user(id),
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (activation_id, version)
);
CREATE TABLE plan_line (
  plan_id uuid NOT NULL REFERENCES plan(id) ON DELETE CASCADE,
  resolution text NOT NULL, recording text NOT NULL,
  quantity int NOT NULL CHECK (quantity BETWEEN 0 AND 100),
  PRIMARY KEY (plan_id, resolution, recording)
);
CREATE TABLE invoice (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_id uuid NOT NULL REFERENCES org(id),
  period_start date NOT NULL, period_end date NOT NULL,
  cents int NOT NULL, currency text NOT NULL,
  status text NOT NULL CHECK (status IN ('draft','issued','paid','void')),
  provider text NOT NULL DEFAULT 'fake', provider_ref text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE invoice_line (               -- one per adapter plan billed (D19)
  invoice_id uuid NOT NULL REFERENCES invoice(id) ON DELETE CASCADE,
  plan_id    uuid NOT NULL REFERENCES plan(id),
  cents      int  NOT NULL,
  PRIMARY KEY (invoice_id, plan_id)
);
CREATE TABLE audit_event (                -- append-only; written by vsz-directory on every change
  id bigserial PRIMARY KEY, at timestamptz NOT NULL DEFAULT now(),
  actor_user_id uuid, action text NOT NULL, target_kind text, target_id uuid, detail jsonb
);
```

Rules the code enforces (and the tests check): a customer's parent is a dealer, a dealer's parent
is the platform; a `system_*` membership points at the platform, `dealer` at a dealer, the three
customer roles at a customer, which has exactly one holder (D18); staff have exactly one membership
and no customer one; a share names a camera on one of the account's own adapters (checked against
the registry when it is made), for a `customer_user` of that account; an adapter allocated to a dealer is activated only at a site of one of that dealer's customers (D15), and an
activation's site belongs to its customer (D19). The database keeps them itself — constraints,
triggers, grants — so a bug in the code above can't break them. `vsz_app` gets `SELECT, INSERT,
UPDATE` where rows change, `DELETE` only where D18 removes rows (a cancelled invitation, a
membership, a share, old activation attempts; the price list, which the owner replaces), only
`SELECT, INSERT` on plan versions, invoice lines and the audit log, and nothing on
`vsz.schema_migrations`.

**Price list seed:** placeholder numbers marked as such, with storage durations 7 / 30 / 90 days
(requirements §3); the owner sets the real prices. Videoloft's retail figures are in
`COSTS-1.4.md` §4, which stays authoritative for any cost or bitrate figure — a later Pricing screen
can show the estimated AWS cost per camera beside each price, so nothing is priced below cost.

**DynamoDB, `cameras` (registry):** two new fields, `adapterId` (the Thing name, D4) and
`releasedFrom` (D15); VMS ignores them. **New DynamoDB tables:** `adapter-discovery` (D16, rows
expire after 15 minutes) and `camera-requests` (D16, registration requests, deleted when done).

## 6. API — `/directory/*` (D8)

Every route checks `vsz_role` / `vsz_org` first; "scope" = own for a dealer, any for an owner or
admin (with `?dealerId=` to narrow).

| Route | Who | What |
|---|---|---|
| `GET /directory/me` | all | user, role, organisation; a customer's sites and adapters; a dealer's company |
| `GET /directory/pricing` | dealer, owner, admin | the price list and settings |
| `PUT /directory/pricing` | owner, admin | replace the price list (audited) |
| `GET /directory/customers` · `POST` | dealer (scope), owner, admin | list (search, status) · create, with its first site and optionally a first user |
| `GET /directory/customers/{id}` · `PATCH` | same | details · name, reference, contact, suspend / reactivate |
| `GET /directory/customers/{id}/adapters` | same | the customer's adapters, each with its current plan and plan vs. actual; the total (D19) |
| `GET /directory/adapters/{id}/plan` · `PUT` | same | an adapter's current plan · a new version, priced by the server |
| `GET /directory/adapters/{id}/plans` | same | that adapter's plan history (its current activation; owners and admins can ask for earlier ones) |
| `GET /directory/accounts/{id}/plans` | holder | Plan & Billing: each adapter's plan and history, the total, invoices |
| `GET /directory/customers/{id}/cameras` | same | inventory from the registry by the customer's adapters; no credentials, no thumbnails (D5) |
| `GET /directory/customers/{id}/users` · `POST` | same | users · invite (Cognito `AdminCreateUser`, email with a temporary password) |
| `POST /directory/users/{id}/{disable,enable,reset-password,resend-invite}` | the user's scope (a holder: own account's users) | Cognito admin calls + directory status |
| `GET /directory/adapters` | customer (own), dealer (own), owner, admin | adapters: status, site, last seen |
| `POST /directory/adapters/activate` | holder, Super Admin; dealer, with `customerId`; admin | `{cloudId, siteId \| newSite}` → activated (D15) |
| `POST /directory/adapters` | owner, admin | generate N adapters: Things, certificates, Cloud IDs — codes, labels and identity bundles returned once |
| `POST /directory/adapters/{id}/{allocate,release,revoke,new-code}` | owner, admin | to a dealer or back · leave its customer · and deactivate its certificate · a new Cloud ID |
| `DELETE /directory/adapters/{id}` | holder, Super Admin | remove the adapter from the account — a release (D15) |
| `GET /directory/accounts/{id}/users` · `POST` | holder, Super Admin | Users and Pending invites · add one: `{email, superAdmin, cameras[]}` (D18) |
| `PATCH /directory/accounts/{id}/users/{userId}` · `DELETE` | holder, Super Admin (never on the holder) | the Super Admin switch and the shared cameras · remove from the account |
| `POST /directory/accounts/{id}/invites/{userId}/{resend,cancel}` | holder, Super Admin | Pending invites |
| `POST /directory/accounts/{id}/holder` | holder | hand the holder role to a Super Admin or user |
| `GET /directory/dealer` · `PATCH` | dealer, admin | own company details |
| `GET /directory/dealers` · `POST` · `PATCH /directory/dealers/{id}` | owner, admin | dealers |
| `GET /directory/users` · `POST` | owner (dealer users), admin (any role) | users across the system |
| `GET /directory/invoices` | dealer (scope), owner, admin | fake invoices (D9) |
| `POST /directory/billing/run-period` | admin | fake billing run (D9) |
| `GET /directory/overview` | owner, admin | counts per dealer, plan totals, cameras registered vs. in plans |

Email and password changes go from the page straight to Cognito, with the user's access token
(D18). The camera-adding routes of D16 (`/adapters/<thing>/…`) sit on `kvs-demo-api` beside the
camera routes, not under `/directory`.

**Invite flow** — a dealer inviting a customer's holder, or a holder or Super Admin adding a
user. The API checks each shared camera against the registry (its `adapterId` must be one of the
account's adapters), then asks `vsz-directory` to check the inviter's right and add the membership
and shares. Then:
- **an address that already has a customer-side account:** nothing more — the person sees the
  cameras at their next token renewal ("access straight away", D18);
- **a new address:** an `invited` user, `AdminCreateUser` (email with a temporary password), then
  the returned `sub` stored; listed under Pending invites until the first sign-in. If Cognito
  refuses, the directory rows are removed again; if storing the `sub` fails, the trigger links the
  user by verified email at first sign-in;
- **an address of a dealer's or the platform's staff:** a generic "can't add this address".
Whether a person lands under Users or Pending invites tells the inviter whether the address already
has an account, as on Videoloft — accepted; the staff case stays hidden.

## 7. Screens

### 7.1 Dealer portal

```
┌──────┬──────────────────────────────────────────────────────────────────────────────────┐
│Live  │  Customers   Cloud adapters   Billing   Pricing   Dealer details                 │
│      ├────────────────┬─────────────────────────────────────────────────────────────────┤
│Events│ Customer list >│  Cloud plan.                                    Smith Ltd — Ref │
│      │ Plan          >│  Adapter   [Home · Garage — adapter-0002   v]   (one plan each) │
│Sett. │ Plan history  >│                    Motion triggered   24/7 continuous           │
│      │ Cameras       >│  Cloud cameras 2MP (1080p)   [0 v]        [0 v]                 │
│Dealer│ Users         >│                4MP           [4 v]        [0 v]                 │
│portal│                │                8MP (4K)      [0 v]        [0 v]                 │
│      │                │  Cloud storage  7 days ── 30 days ──●── 90 days                 │
│      │                │  Payment        Monthly (●) Annual (10% off)                    │
│Acct. │                │  Trade price (ex. tax)   €23.56 / month          [Save plan]    │
│Exit  │                │  Plan vs. actual: 4 in plan, 4 on this adapter   (not enforced) │
│      │                │  Customer total: 2 adapters, €41.20 / month                     │
└──────┴────────────────┴─────────────────────────────────────────────────────────────────┘
```

- **Customer list:** search, status filter, columns name · reference · adapters · cameras (in plans
  / registered) · monthly total · created; "New customer" (name, reference, email, site name and time zone,
  optional first user).
- **Plan:** as above, for the adapter picked — each of the customer's adapters has its own plan
  (D19) — ending with the customer's total. The price updates as the controls change — computed in the page from the
  price list for the preview, and by the server on save; both implementations use the same test
  vectors (`tests/fixtures/pricing-cases.json`, read by pytest and Vitest) so they can't drift.
  The server's figure is the one stored. *Cancel subscription* ends that adapter's plan.
- **Plan history:** per adapter: versions with who, when, what changed, price.
- **Cameras:** the customer's inventory (D5), grouped by adapter, with each adapter's plan vs. actual, each camera's resolution mapped
  to a class (≤ 2.1 Mpx → 2 MP, ≤ 4.2 → 4 MP, else 8 MP) and its recording mode mapped to motion /
  continuous — a hint, not a rule.
- **Users:** invite, resend, reset password, disable / enable.
- **Cloud adapters:** adapters allocated to this dealer: in stock / activated at which customer
  site, last seen; *Activate for a customer* (the Enter Cloud ID screen of §7.3, with a customer
  picker).
- **Billing:** the demo banner, each customer's total over its adapters' plans, fake invoices.
- **Pricing:** the price list, read-only. **Dealer details:** company name, contact, address.

### 7.2 System console

Tabs *Overview · Dealers · Customers · Users · Adapters · Pricing · Billing*. Customers reuses the
dealer portal's pages with a dealer filter; Pricing is the editable matrix (resolution × recording
× storage duration) plus the annual discount and trial length; Users can create owners and admins
(admin only); Adapters generates adapters with their Cloud IDs (codes, printable labels and
identity bundles, shown once), allocates them to dealers, releases or revokes them and issues new
codes.

### 7.3 Sign-in, Account, and the customer's pages

- **Sign-in** — the common page of D17: the system's name and logo, email address, password with
  show/hide, *Forgotten password?*, *Log in*; the new-password step after an invitation's temporary
  password (`RespondToAuthChallenge`); the forgotten-password steps (`ForgotPassword` /
  `ConfirmForgotPassword`). The session keeps the access token too (today it keeps only the ID
  token), for the Account page's changes.
- **Account** (D18), for every role, the tabs a role may use:

```
┌──────┬──────────────────────────────────────────────────────────────────────────────────┐
│ Live │  Account details    Plan & Billing    Additional users                            │
│      ├──────────────────────────────────────────────────────────────────────────────────┤
│Events│  Account details.                                                                │
│      │  ─────────────────────────────────────────────────────────────────────────────── │
│Sett. │  Email:     holder@example.com                  Edit                             │
│      │  Password:  ********                            Edit                             │
│      │                                                                                  │
│Acct. │  (no SSO card: D17)                                                              │
│Exit  │                                                                                  │
└──────┴──────────────────────────────────────────────────────────────────────────────────┘
```

  Email → Edit: the new address, then the code sent to it; Password → Edit: current and new.
  Plan & Billing: a card per adapter — site and name, its plan read-only, its history — then the
  total and the fake invoices (one per period, a line per adapter) with the demo banner. Additional users, after
  Videoloft's:

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│  Add user.                                                                               │
│  Email*       [                    ]   Has an account: access straight away. No account: │
│                                        an email asking them to set a password.           │
│  Super Admin  [off]                    All cameras of the account; can add and remove    │
│                                        cameras and cloud adapters, and manage users.     │
│  Cameras      [x] Front gate  [ ] Yard  [x] Desk cam           (when Super Admin is off) │
│  [Save] [Cancel]    Changes take effect within about 3 minutes.                          │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│  Users.            anna@example.com    Super Admin              Edit   Remove            │
│                    ben@example.com     2 cameras shared         Edit   Remove            │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│  Pending invites.  carl@example.com    invited 2026-11-06       Resend Cancel            │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```
- **Add adapter** — *Enter Cloud ID* (D15), after Videoloft's: a picture of the adapter with the
  label's place circled (an own drawing), the Cloud ID field (upper-cased, the dash added as you
  type), *Continue*; then the location — one of the customer's sites, or a new one (name, time zone
  from the browser) — and a name for the adapter ("Garage", D19). It starts on a trial plan until
  the dealer sets one. Reached from Live's empty state ("Add your Cloud Adapter") and from Settings.
- **Add cameras** (D16): the adapter (picked for you when there is one) → "Looking for cameras…"
  → the cameras it found, those already added marked → per camera: name, ONVIF user and password
  → *Add* → progress until it is registered; then it shows in Live and Settings.
- **Camera list** groups by site when a customer has more than one (requirements §5: switching
  between locations).

## 8. Phases

### Phase 0: Close the hole, settle, look at the database

0.1 **Close self sign-up on the shared pool** (`AllowAdminCreateUserOnly=true`) — before anything
else; it protects VMS too. `bash cloud/close-signup.sh --dry-run`, then without. It passes every
current setting back with only this one changed (`update-user-pool` resets what it isn't given),
compares the pool before and after, proves the switch with a refused sign-up on each app client,
and lists the users; `restore FILE` puts back the copy it saves first. **Done 2026-11-06** (run
by the user, 20:30 CET): only the switch changed; sign-up refused on both clients; the one user is
`demo-viewer`. Guide §8.2 corrected; `FoundAndFixed.md #87` fixed.
0.2 The user settles D1–D10 — **done 2026-11-06**, all accepted as proposed. The amendments to D1,
D4 and D6, and D11–D19, were settled the same day as proposed — D17 saying VideoSafeZone, D18
without Opt-ins.
0.3 On the shared instance, with the user's go-ahead (2026-11-06) and run by the user, at a quiet
hour for the other project: **deletion protection** (no restart) and **IAM database
authentication** (§4 D3; the AWS docs don't say whether this one restarts the instance).
Password logins, the other project's included, keep working; only roles granted `rds_iam` (ours,
Phase 1) must use IAM tokens.

```bash
aws rds modify-db-instance --db-instance-identifier database-coursera-iot \
  --deletion-protection --enable-iam-database-authentication --apply-immediately
aws rds wait db-instance-available --db-instance-identifier database-coursera-iot
```

Then: both flags true and nothing pending (`describe-db-instances`); the instance's recent events
(`describe-events`) say whether it restarted — record which here; the other project's
`GetTelemetry` and `ConnectPostgreeSQL` still connect (their CloudWatch logs). To undo:
`--no-deletion-protection`, `--no-enable-iam-database-authentication`.
**Done 2026-11-06** (run by the user, about 20:35 CET): both flags on, nothing pending, and **no
restart** — the instance logged no event at all. The other project's two Lambdas last ran in July
and August, so nothing was using the database at the time.
0.4 Check the master secret still logs in, and which databases exist (from `vsz-db-admin`'s first
run, Phase 1) — the other project's must not be touched.

*Verify:* `aws cognito-idp sign-up` with the client ID is refused ("SignUp is not permitted");
`describe-user-pool` differs from before only in that flag; VMS's page still signs in.

### Phase 1: The database

1.1 Layer `vsz-pg`: pg8000 and its dependencies pinned by hash, plus the RDS CA bundle
(checksum-pinned); built and published by `provision.sh pg-layer`.
1.2 `vsz-db-admin` (VPC, `vsz-db-client` group; one inbound rule added to `rds-lambda-2`: 5432 from
`vsz-db-client` — additive). **Bootstrap**, once: `provision.sh directory-db` reads the master
password from the secret on the Pi and passes it in the invoke payload from a 0600 file (never on
a command line); the Lambda creates the database, `vsz_migrator` and `vsz_app` with `rds_iam` and
connection limits, and forgets the password. **Migrations** afterwards connect as `vsz_migrator`
with an IAM token. `deploy.sh` learns that this one function ships `cloud/db/migrations/*.sql`
beside its file.
1.3 Migration 001 (§5, `adapter_activation` and per-adapter plans included), seed: the platform
organisation, the price-list placeholders.
1.4 **Tests against a real PostgreSQL** in `tests_db/`, a separate pytest root with its own
`conftest.py` — the main suite's network guard stays exactly as it is. In CI, a `postgres:18`
service container; on the Pi, skipped unless `VSZ_TEST_PG_DSN` is set. Migrations apply from empty
twice (idempotent), constraint violations are refused. `PYTEST.md` gets the step. (The scope rules
of operations — dealer A can't read dealer B's customers — come with the operations, Phase 2.)

*Verify:* `provision.sh directory-db --dry-run`, then for real; `vsz-db-admin` reports the applied
migrations; `SHOW max_connections` and the other project's database list recorded here.

*Built 2026-11-06* (1003 offline pytest, 27 tests_db; not provisioned):
- **1.1** `cloud/layers/vsz-pg/requirements.txt`: pg8000 1.31.5, scramp 1.4.6, asn1crypto 1.5.1,
  python-dateutil 2.9.0.post0, six 1.17.0 — each hash-pinned, each months old when chosen (pg8000
  1.31.6 and python-dateutil 4.0.0 were days old and were passed over). `rds-ca.txt` pins the
  eu-central-1 bundle (three roots, the instance's `rds-ca-rsa2048-g1` among them) by sha256.
  `provision.sh pg-layer` builds the same bytes every time (checked: two builds, one hash), so it
  publishes only when the files change.
- **1.2** `cloud/lambda/vsz_db_admin.py` (actions `status`, `bootstrap`, `migrate`). TLS is its own
  context with the CA bundle and the hostname checked — pg8000's default would skip verification
  and even fall back to plain text. One migration run at a time through a PostgreSQL advisory
  lock, since reserved concurrency isn't available (above). `cloud/iam/vsz-db-admin-policy.json`:
  `rds-db:connect` as `vsz_migrator` on this instance only; the managed
  `AWSLambdaVPCAccessExecutionRole` for its network interfaces and logs.
  `cloud/package.sh` (sourced by both scripts) says what a package holds — for `vsz-db-admin`, its
  migrations too — and `deploy.sh` now compares every file of a package (every existing function
  still reads "this tree"). New: `deploy.sh db-status`, `deploy.sh db-migrate [--dry-run]`, which
  refuses unless the live package equals this tree.
- **1.3** `001_directory.sql`, the whole §5 model with its triggers and grants. Phase 6's "migration
  002" isn't needed for the adapter columns: they are in 001.
- **1.4** `tests_db/` (27) and `tests/cloud/test_db_admin.py` (31); CI job `db`. Proved to catch
  regressions; found one bug before any deploy (a trigger variable that shadowed the table name).
- **Dry run against the account:** `provision.sh directory-db --dry-run` lists the layer, the
  security group `vsz-db-client`, the one rule added to `rds-lambda-2`, the role, the function in
  the three subnets, then the bootstrap, migrations and status — and changes nothing.

### Phase 2: Identity

2.1 Pool `vsz-users` and app client `vsz-web` (no secret; `USER_PASSWORD_AUTH`,
`REFRESH_TOKEN_AUTH`; ID token 5 min; refresh 30 days; email as username, the old one kept until
a new one is confirmed; admin-created only),
by `provision.sh identity`.
2.2 `vsz-directory` (VPC, `vsz_app`) with its operations and the pre-token trigger (D4); Cognito
permitted to invoke it.
2.3 Seed through the directory: the user as `system_admin`, a demo dealer, a demo customer with a
site and `adapter-01` activated there (seeded, without a code, with a trial plan), its holder, a Super Admin and a
user with one camera shared
(replacing `demo-viewer`; the saved Live layout starts fresh, since `user-settings` is keyed by
`sub`).
2.4 The common sign-in page (D17) and Account → Account details (D18) in `client-app/`: email and
password with show/hide, forgotten password, the new-password step after an invitation, change of
email (with its code) and of password; the access token kept for those calls. The pool and client
ID move to `config.ts` only at Phase 3's switch.

*Verify:* sign in from the CLI as each seeded user and decode the ID token: the claims are right;
a disabled user and a user of a suspended customer get no token; with the trigger cold, sign-in
finishes well inside Cognito's 5 seconds (measured and recorded here).

### Phase 3: Tenancy on the camera API

3.1 `adapterId` on camera rows: admin GUI writes it at registration and re-registration;
`rematch_cameras.py` stamps it where missing (D4); pytest with the `registry` fixture.
3.2 `tenant_allows` in the twelve camera and clip Lambdas (all of `kvs-demo-api`'s except
`user-settings`), with `TENANCY=report` first: decisions are logged, nothing is refused. A test
matrix through `invoke()`: each route × {holder, Super Admin, user with the camera shared, user
without it, other customer, dealer, owner, admin, no claims}, view and control routes apart.
3.3 The switch: `kvs-demo-api`'s authorizer gets both pools; deploy the Lambdas in report mode;
deploy the page with the new pool; read the logs for a day; set `TENANCY=enforce`; remove the old
pool from `kvs-demo-api`'s authorizer (VMS's `pir-api` keeps it). This repository's old app client
`kvs-demo-web-client` can then be deleted.

*Verify:* as the demo customer, everything on Live / Events / Settings works; a second test
customer with no adapter sees no cameras, and a direct `curl` for `cam-03` with its token gets the
unknown-camera answer; as the dealer, the camera routes refuse; VMS's page unaffected.

### Phase 4: Directory API and the System console

`vsz-directory-api`, `/directory/{proxy+}` (`provision.sh directory`), the routes of §6 for the
system roles; pytest through `invoke()` with a stubbed Cognito and a fake `vsz-directory`; the
System console screens (§7.2), lazy-loaded; route guards by role in the page (Vitest).

*Verify:* as admin, create a dealer and its first user; the invitation arrives; that user signs in,
sets a password, lands on the dealer portal (empty); as owner, the Users tab can't create an admin.

### Phase 5: Dealer portal

The screens of §7.1 and their routes: customers, per-adapter plans with live price and the
customer total, plan history, cameras,
users, adapters, pricing, dealer details. The pricing function in Python and TypeScript with the
shared vectors.

*Verify:* as the demo dealer: create a customer and invite its holder; set and change the demo
customer's plan for `adapter-01` (its history shows both versions with the price). Activating an
adapter for the new customer waits for Phase 6. `tests_db/`: a plan belongs to one activation;
a version's price is the server's.

### Phase 6: Adapters — Cloud IDs and activation (§4c D15)

6.1 Directory: (the adapter columns and `activation_attempt` are in migration 001); operations to generate
(the code made and hashed in `vsz-directory`, the key in its environment), allocate, activate (the
rules and the rate limit of D15), release, revoke and issue a new code. `tests_db/` for every rule:
a wrong code, a used code, another dealer's adapter, the sixth attempt in an hour.
6.2 `vsz-directory-api` makes the Thing, its certificate and the policy attachment — IAM
`iot:CreateThing`, `CreateKeysAndCertificate`, `AttachPolicy`, `AttachThingPrincipal`,
`UpdateCertificate` on `adapter-*` only, and `iot:Publish` + `iot:RetainPublish` on
`adapter/*/config` — returns the codes and identity bundles once, and publishes the retained
config. `setup-identity.sh restore` takes a bundle, `THING_NAME` included.
6.3 The agent reads the retained config and keeps the activation state on the Pi; the admin GUI
refuses to register a camera while the adapter isn't activated (D16).
6.4 Last seen: IoT lifecycle events → IoT rule → `vsz-directory`.
6.5 Screens: System console → Adapters; customer → Add adapter (§7.3); dealer → Activate for a
customer.

*Verify:* generate `adapter-02` (no Pi needed: activation doesn't wait for the adapter); as the demo
customer, wrong Cloud IDs are refused five times, then blocked for the hour; the right one
activates it at a new site and it shows at once (token renewed); the same code again is refused;
as a dealer, activating another dealer's adapter is refused. With `adapter-02` beside `adapter-01`,
the demo customer has two adapters: `adapter-02` starts its own trial plan, setting it leaves
`adapter-01`'s untouched, and the customer list and Plan & Billing show both and their total.
Release it, issue a new code, and activate it for another customer: the first no longer sees it,
keeps its plan history, and the second starts a new one. `adapter-01`'s last seen follows
the Pi going offline and back.

### Phase 7: Adding cameras from the web page (§4c D16, with D11 and D12)

7.1 Registration moves out of `app.py` into `adapter/camera_registration.py`, used by the admin
GUI and the agent, behaviour unchanged (the admin tests pass unchanged).
7.2 Camera IDs allocated, the wider pattern, re-register refused across adapters (D11); every Pi
reader filtered by `adapterId`, `ADOPT_UNSTAMPED_ROWS` on `adapter-01` (D12). Pytest: two
registrations racing for one ID, re-registering another Pi's camera, one test per reader as for
the unregistered rule.
7.3 The agent's `discover` and `register` commands; the IoT rule and `adapter-discovery`;
`camera-requests`; Lambda `adapter-cameras` with D16's routes (checks `vsz_adapters`); `publish-cmd`
routing by `adapterId`.
7.4 Screens: Add cameras (§7.3).

*Verify:* as the demo customer, on `adapter-01`: discovery lists the LAN's cameras, the registered
ones marked; adding a camera (a spare, or one unregistered first in the admin GUI) makes it play in
Live; the password appears in no log (CloudWatch, journal) and no MQTT message; with the Pi's
agent stopped, the page reports the adapter offline instead of waiting forever.

### Phase 8: Billing seam (fakes)

`BillingProvider` and `FakeBillingProvider` (D9); trial length from `price_setting`; the Billing
tab; the admin's "run billing period (fake)"; invoices listed per dealer and customer.

*Verify:* a fake period run produces one invoice per customer, with a line per adapter's active plan
at its stored price; a trial plan adds no line until its trial ends; the banner is on every billing screen.

### Phase 9: Account page and additional users (D18)

The Plan & Billing and Additional users tabs (Account details came in Phase 2.4): the
directory routes for users, Super Admins, shares and pending invites, with `tests_db/` for every
rule — one holder, a Super Admin can't touch the holder, a share outside the account refused, a
staff address refused, a released adapter ending its shares; the account picker; handing over the
holder role; the camera list grouped by account and site. (`vsz_cameras` in the trigger and the
Lambdas' view / control split come with Phases 2 and 3.)

*Verify:* the holder adds a new address as a user with one camera shared: it shows under Pending
invites, the email arrives, the person signs in and sees only that camera, live and recorded — the
others get the unknown-camera answer, and Start/Stop on the shared one is refused. The holder adds
an address that already has an account (another demo customer's holder): it shows under Users at
once, and that person sees the shared camera beside their own within about 3 minutes. A Super
Admin can't remove the holder.

### Phase 10: A second adapter at another customer (§4b)

Must be complete before a second adapter serves a real customer.

10.1 Spike (D13): tag-conditioned `PutMedia` from kvssink, `credentials-iot:ThingName`, and
`dynamodb:LeadingKeys` on an index, each on a test resource; results recorded here, D13 adjusted.
10.2 Device isolation (D13): camera secrets to SSM per Thing, stream tags, S3 prefixes, the
registry-write path over MQTT; the device role narrowed and exported back to `cloud/iam/` (#41).
Only after D14's choice is made — VMS's own copies, or its card retired — since it changes what
VMS can do.

*Verify:* `adapter-02` from Phase 6 on a second Pi (or a spare SD card in a second board),
allocated to a second dealer and activated by that dealer's customer: each customer sees only its
own cameras; neither Pi adds the other's paths; with `adapter-02`'s certificate, reading
`adapter-01`'s camera secrets, pushing into its streams and writing its rows are all refused (tried
from the CLI with that certificate's credentials); commands from the page reach the right Pi.

### Later

Fleet provisioning by claim, behind the same Enter Cloud ID screen (D15); USB cameras on new
adapters (A6); SSO with Microsoft and Apple, on the sign-in page and as the Account page's "Convert
to SSO" card (D17); signing up with an unused Cloud ID (D17, the user's call); per-feature
permissions for users, an email telling someone they were added, and the Account page's Opt-ins
tab (D18). Plan enforcement, per adapter
(camera limits on that adapter; its cameras' KVS retention from its plan via `UpdateDataRetention`,
priced against `COSTS-1.4.md`). The dealer view-access switch (D5). MFA
for system roles. SES for invitation email. A real billing provider. Audit-log screens. A database
of its own if the shared instance's availability becomes the limit.

## 9. To check before promising

- The pre-token trigger's cold start in the VPC (layer import, IAM token, TLS connect, query) stays
  well under Cognito's 5 seconds; if not, provisioned concurrency of 1 (a small fixed cost) or a
  lighter path for the trigger.
- What the instance does to sign-in during its maintenance window and minor-version upgrades.
- `max_connections` on the micro instance against the other project's use.
- **The account's Lambda concurrency is 10 in total** (found in Phase 1), for every function at
  once — the camera API, the directory, the other projects. Enough for a demo; before real
  customers, ask AWS for the quota to be raised (Service Quotas, "Concurrent executions").
- Cognito's default email limit against how many invitations (and email-change codes) a demo
  sends in a day.
- The ID token's size with many shares (100 × about 21 characters) against API Gateway's header
  limit, and the trigger's time for a person in several accounts.
- How long the agent's discovery takes on a busy LAN (WS-Discovery plus the per-device probes),
  against the page's one-minute wait (D16).

## 10. Session checklist

- [x] Phase 0.1: self sign-up closed on the shared pool; `FoundAndFixed.md #87` fixed (2026-11-06)
- [x] Phase 0.2: D1–D10 settled with the user (2026-11-06, all as proposed)
- [x] D11–D14 (several adapters, §4b), D15–D19 (§4c) and the amendments to D1, D4, D6 settled with the user (2026-11-06)
- [x] Phase 0.3: deletion protection and IAM auth on `database-coursera-iot` (2026-11-06, no restart)
- [x] Phase 1: layer, `vsz-db-admin`, migration 001, `tests_db/` in CI — built and tested (2026-11-06)
- [ ] Phase 1: provisioned (`provision.sh directory-db`, the user) and its *Verify* recorded
- [ ] Phase 2: `vsz-users`, `vsz-directory` + trigger, seed, the sign-in page, Account details
- [ ] Phase 3: `adapterId`, tenant checks report → enforce, pool switch
- [ ] Phase 4: directory API, System console
- [ ] Phase 5: Dealer portal
- [ ] Phase 6: Adapters — Cloud IDs, activation, release, last seen
- [ ] Phase 7: Adding cameras from the web page (allocated IDs, per-Pi filtering)
- [ ] Phase 8: Billing seam with fakes
- [ ] Phase 9: Account page and additional users
- [ ] Phase 10: A second adapter at another customer — spike, device isolation
- [ ] §9 measurements recorded here
