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
authentication on the database) done 2026-11-06, run by the user. **Phase 1 done 2026-11-07**:
built and tested, provisioned by the user (`provision.sh directory-db`), verified. **Phase 2 done
2026-11-07**: provisioned by the user and verified, the trigger's cold start included (1.09 s,
FoundAndFixed.md #89). **Phase 3 done 2026-11-07**: the tenant check enforces and is verified,
and `kvs-demo-api` accepts only `vsz-users`. **Phase 4 done 2026-11-07**: the directory API and the
System console, deployed and verified by the user. **Phase 5 done 2026-11-07**: the dealer portal,
deployed and verified by the user. **Phase 6 deployed 2026-11-07** (Cloud IDs and activation, with D15
amended: the owner's script makes adapters' AWS identities); the activation flow itself is still to
be tried (its *Verify*). **Phase 7 built 2026-11-07** (adding cameras from the web page), tested, not
yet deployed. Next: its deploy and *Verify*, then Phase 8.

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
project of the micro instance's connections (measured in Phase 1: `max_connections` is 79, so
our 13 leave 66 for the other project and RDS's own `rdsadmin`). Migrations are numbered SQL files in `cloud/db/migrations/`,
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
variable, and `LeadingKeys` on an index, each tried on a test stream / table. *Tried 2026-11-15
(Phase 10.1): all three hold* — streams stay named by camera ID and are scoped by their `adapterId` tag,
created only so tagged; the Pi reads by `Query` on a sparse `adapterId` index, so every row it acts on
must be stamped first; the details for 10.2 are under Phase 10.

**D14. Several customers' adapters and VMS don't share the camera table or the device role.** VMS
reads the same `cameras` table and uses the same role alias, with no tenancy: once a second
adapter serves a real customer, VMS's page would list that customer's cameras, and D13's narrower
device role would break VMS's admin GUI, which writes the table directly. So before a second
adapter goes to a customer, VMS either gets copies of its own (its own table and role alias) or
its card is retired. Until then, a second adapter is for testing only, with test cameras. Settled
2026-11-06 (the user agreed); which of the two is decided when Phase 10 starts.

*Decided 2026-11-14 (the user, at Phase 10): VMS gets its own copies*, built in the VMS repository
(this one never deploys a `pir-*` resource). For D13 to keep one adapter off another's resources,
"its own" has to cover everything the two cards share today (VMS-PORT-PLAN.md D1):
- **its own Thing and certificate** — `adapter-01` becomes this repository's alone (and
  `adapter-identity.sh`'s guard on it can go);
- **its own role alias and role**, whose policy reaches none of this repository's resources: not the
  `cameras` table, not the `cam-*` streams, not `/vsz/adapters/*`, not the `thumbs/` and `clips/`
  keys this repository writes;
- **its own camera table**, which VMS's admin GUI and page (`pir-api`) read and write;
- **its own KVS streams** — camera IDs collide (both have `cam-01` … `cam-04`) and stream names are
  camera IDs, so VMS's streams need names of their own;
- **its clips**: `clip-to-s3` is this repository's and VMS uses it for ONVIF detection clips — VMS
  gets its own, or the shared one stays backward compatible (CLAUDE.md) until it does.
VMS's page is already apart from this repository's sign-in (Phase 3: `kvs-demo-users` is VMS's alone
now). Until VMS runs on its copies, 10.2 builds only what keeps VMS's card working; the narrowed role
and the secrets leaving the rows come after (Phase 10).

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

*Amended 2026-11-07 (the user, at Phase 6): the AWS identity is made by the owner's script, not the
System console.* Making a certificate, attaching it to the Thing policy and to the Thing, and
deactivating it on revoke need IAM permissions on certificates, and IAM can't limit those to this
project's: a certificate's ARN is its random ID. A web function holding them could detach or
deactivate any certificate in the shared account — VMS's `adapter-01`, the pico2w devices. So the
System console makes the adapter (its directory row, its number, its Cloud ID and label), and
`cloud/adapter-identity.sh`, run with the owner's own AWS login, makes the Thing, the certificate
and the folder for that adapter's Pi (`create`), and deactivates the certificate after a revoke
(`revoke`). `vsz-directory-api` gets no certificate permissions — only the retained config publish
and the registry write of a release.

**D16. Cameras are added from the web page, through the adapter.** The browser can't reach the
customer's LAN and WS-Discovery works only on it (*why*: the end of D16), so the page asks the
adapter, over the Pi's own outbound MQTT session — the zero-inbound-ports rule holds:
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

*Why the browser can't reach the customer's LAN* (analysed 2026-11-14, at the user's request; the
cameras' answers measured from the Pi that day). Five barriers:
- **The browser isn't on the LAN.** The page is used from anywhere — a phone on mobile data, an
  office — and adding cameras without being on site is what D16 is for. The cameras have private
  addresses (`192.168.178.x`) behind the customer's router: nothing on the internet routes to them,
  and making a route (a port forward, a VPN into the LAN) is the inbound path the project rules out
  (`OUTBOUND-CLOUD.md` §4); behind CGNAT, as on mobile networks, it isn't even possible. Lambda is
  one more outside client (guide §16.2.1).
- **WS-Discovery stays on the segment.** A probe is UDP multicast to `239.255.255.250:3702`, and
  multicast doesn't cross a router: a VPN that carried the browser's traffic into the LAN still
  wouldn't carry the probe (`NETWORK.md` §2).
- **A web page can't send a probe, even on the LAN.** A page has HTTP(S), WebSocket, WebRTC and
  WebTransport — no UDP of its own. Chrome's Direct Sockets API, multicast included, is offered only
  to Isolated Web Apps (installed, signed apps, today on ChromeOS), never to a page loaded from a URL
  such as CloudFront's.
- **Nor rely on talking to a camera whose address it knows.** ONVIF is SOAP over plain HTTP — none
  of the three cameras answers HTTPS — and the page is HTTPS. Browsers block that as mixed content;
  Chromium-based ones, since Local Network Access (2025), let it through once the person allows the
  site to connect to devices on the local network. Even then the camera has to answer CORS, a SOAP
  POST's preflight included, and that is down to its firmware: cam-02 allows any origin
  (`Access-Control-Allow-Origin: *`), cam-03 refuses the preflight (501), cam-04 closes the
  connection. And no browser speaks RTSP.
- **Registering is the Pi's work, wherever the browser is.** It adds the MediaMTX path through the
  API on `127.0.0.1:9997`, provisions the `kvs-cam@` unit through sudo, and creates the KVS stream
  and the registry row with the Pi's certificate (`camera_registration._create`).
The admin GUI never crossed these lines either: its page talks only to the Pi (plain HTTP, same
origin), and its Flask process does the discovery and the ONVIF calls. So the one party on the
cameras' segment that can probe, talk to any camera and register it is the Pi, and the one channel
to it that needs no inbound port is its own MQTT session — D16's route.

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
| `GET /directory/accounts/{id}/plans` | holder | Plan & Billing: each adapter's plan and history, the total, invoices and this month's estimate, the dealer's contact |
| `GET /directory/customers/{id}/cameras` | same | inventory from the registry by the customer's adapters; no credentials, no thumbnails (D5) |
| `GET /directory/customers/{id}/users` · `POST` | same | users · invite (Cognito `AdminCreateUser`, email with a temporary password) |
| `POST /directory/users/{id}/{disable,enable,reset-password,resend-invite}` | the user's scope (a holder: own account's users) | Cognito admin calls + directory status |
| `GET /directory/adapters` | customer (own), dealer (own), owner, admin | adapters: status, site, last seen |
| `POST /directory/adapters/activate` | holder, Super Admin; dealer, with `customerId`; admin | `{cloudId, siteId \| newSite}` → activated (D15) |
| `POST /directory/adapters` | owner, admin | generate N adapters: Things, certificates, Cloud IDs — codes, labels and identity bundles returned once |
| `POST /directory/adapters/{id}/{allocate,release,revoke,new-code}` | owner, admin | to a dealer or back · leave its customer · and deactivate its certificate · a new Cloud ID |
| `DELETE /directory/adapters/{id}` | holder, Super Admin | remove the adapter from the account — a release (D15) |
| `GET /directory/accounts/{id}/users` · `POST` | holder, Super Admin | Users and Pending invites, and the account's cameras (from the registry) · add one: `{email, superAdmin, cameras[]}` (D18) |
| `PATCH /directory/accounts/{id}/users/{userId}` · `POST …/{userId}/remove` | holder, Super Admin (never on the holder, never on oneself) | the Super Admin switch and the shared cameras · remove from the account (POST: the preflight allows no DELETE, as for adapters) |
| `POST /directory/accounts/{id}/invites/{userId}/{resend,cancel}` | holder, Super Admin | Pending invites |
| `POST /directory/accounts/{id}/holder` | holder | hand the holder role to a Super Admin or user |
| `GET /directory/dealer` · `PATCH` | dealer, admin | own company details |
| `GET /directory/dealers` · `POST` · `PATCH /directory/dealers/{id}` | owner, admin | dealers |
| `GET /directory/users` · `POST` | owner (a dealer's staff, a customer's holder), admin (any role) | users across the system |
| `GET /directory/invoices` | dealer (scope), owner, admin | fake invoices (D9), with their lines; `?dealerId=`, `?customerId=`, `?period=` |
| `GET /directory/customers/{id}/invoices` | dealer (scope), owner, admin | one customer's invoices, and the estimate of its next one (Phase 8) |
| `POST /directory/billing/run-period` | owner, admin | fake billing run for a month (D9; D6's table — first written here as admin only) |
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
- **Invoices** (Phase 8): the month so far, as an estimate, then the months billed, each with a line
  per adapter.
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
run, Phase 1) — the other project's must not be touched. **Done 2026-11-07:** the secret logged in
(the bootstrap used it once); the databases are `postgres` — the default, where the other project's
data lives, untouched — `rdsadmin` (RDS's own) and now `videosafezone`.

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

*Provisioned 2026-11-07* (run by the user, `bash cloud/provision.sh directory-db`), and verified:
- Created: layer `vsz-pg:1`; security group `vsz-db-client` (`sg-024dd79b5279ab901`, no inbound
  rules); on `rds-lambda-2` one added rule, tcp 5432 from it, beside the other project's two;
  role `vsz-db-admin-role` (inline policy equal to `cloud/iam/vsz-db-admin-policy.json`, checked);
  function `vsz-db-admin` in the three subnets, `Active`, live code = this tree.
- Bootstrap: created `vsz_migrator` (limit 3), `vsz_app` (limit 10), database `videosafezone`
  (owner `vsz_migrator`); the master password went to the function once and was used for one
  connection. Migration `001_directory.sql` applied; `db-migrate --dry-run` afterwards: none pending.
- Status, through verified TLS as `vsz_migrator` with an IAM token: PostgreSQL 18.3,
  `max_connections` 79, both roles IAM-only; at the time 3 `rdsadmin` connections, 5 background,
  1 ours. The function's log holds only Lambda's own start/end lines — nothing it printed.
- CI: the first run of the `db` job failed — `tests_db` had passed on the Pi only because the shell
  exported `AWS_REGION` (FoundAndFixed.md #88, fixed the same day; 28 tests now). Its next run is
  also the first on PostgreSQL 18: the Pi has 17, and fetching 18 from outside Debian's own
  repository was refused by the session's safety check.

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

*Built 2026-11-07* (1025 offline pytest, 42 tests_db, 135 Vitest; not provisioned):
- **2.2** `cloud/lambda/vsz_directory.py`. As Cognito's pre-token trigger (event version 1): finds
  the person by sub, at the first sign-in by verified email (linking the sub); refuses — raises,
  which is how a trigger stops a sign-in — the unknown, the disabled, and anyone without an active
  organisation; returns D4's claims; marks an invited person active at their first sign-in, and
  takes a confirmed new email into the directory (unless another person here has it). As the
  operator's tool: `ping`, `claims_for` (what an email would get, without signing in),
  `operator_seed` (one transaction; everything found by its natural key first, so a second run
  changes nothing). One connection while warm, replaced once if it drops; an SQL error isn't
  retried. Its five database helpers are copies of `vsz-db-admin`'s, identical
  (`test_lambda_twins`) — which is why `vsz-db-admin`'s `connect()` changed too (its connection's
  name comes from the function's own), so that function needs `deploy.sh lambda vsz-db-admin`.
  IAM: `cloud/iam/vsz-directory-policy.json`, `rds-db:connect` as `vsz_app` only.
- **2.1** `cloud/cognito/vsz-users-pool.json` and `vsz-web-client.json`, the configuration as
  files beside `cloud/iam/` (Lite tier: ID-token claims work there; password at least 10
  characters with upper and lower case and a number; invitation and code emails say
  VideoSafeZone). `provision.sh identity`: the function in the VPC (256 MB, 5 s — Cognito's
  limit for a trigger), the pool with it as trigger, Cognito's permission to invoke it for this
  pool only, the client; then a `ping` through it. `directory-db` now shares the network part
  with it (`db_network`, `ensure_vpc_function`); its dry run still finds everything in place.
- **2.3** `provision.sh directory-seed YOUR_EMAIL`: you as `system_admin` (Cognito emails a
  temporary password, valid 7 days), and the demo accounts at example.com — owner, dealer,
  holder, Super Admin, user (shares `cam-03` "hall"), and two that must be refused (disabled;
  holder of a suspended customer) — with random passwords in
  `~/.local/state/videosafezone/demo-accounts.txt` (0600). Then the seed, then
  `cloud/sign-in-check.sh` for each demo account: it signs in the way the page does and prints
  the time taken and the `vsz_*` claims, or the refusal.
- **2.4** `client-app/`: the sign-in page of D17 (rendered and looked at), the new-password and
  forgotten-password steps, the Account page with its Account details tab (email and password,
  each with Edit; no camera list beside it), Account on the rail above Exit. The session keeps the
  access token for the account calls, and shows a refusal from the trigger in its own words.
  Nothing is deployed: until Phase 3's switch the page still uses the old pool.
- `tests_db/test_directory.py` (14): every role's claims on the real schema, the refusals, a
  person in two accounts, the cap on shares, linking, first sign-in, a changed address, the seed
  run twice. `tests/cloud/test_directory.py` (17): the trigger's answer and refusal, which events
  are a first sign-in, operator-only operations, the transaction around the seed, reconnecting.

*Provisioned 2026-11-07* (run by the user: `deploy.sh lambda vsz-db-admin`, `provision.sh
identity`, `provision.sh directory-seed`), and verified:
- Pool `vsz-users` (`eu-central-1_5QDHHWlPI`): Lite, accounts by invitation only, email sign-in,
  deletion protection, trigger `vsz-directory` (V1_0); client `vsz-web` (`4g53lfqkkriopjntkgf240edte`):
  no secret, ID and access tokens 5 minutes, refresh 30 days, user-existence errors hidden. 8 users:
  the administrator (invited by email) and the 7 demo accounts. Both directory functions' live code
  is this tree.
- The demo sign-ins, through `cloud/sign-in-check.sh`: owner `system_owner` + the platform's id;
  dealer `dealer` + the dealer's id; holder and Super Admin `customer` with `vsz_adapters`
  `adapter-01`; the user `customer` with `vsz_cameras` `cam-03@adapter-01` only; the disabled
  account and the suspended customer's holder **refused** by the trigger. Each in 0.34–0.43 s.
- **Two defects found:** the trigger's only cold start took 4.26 s (init 284 ms + handler 3974 ms) at
  256 MB, of the 5 s Cognito allows — the demo sign-ins had found it warm (FoundAndFixed.md #89:
  1024 MB) and the refusal read "…account.." (#90). Both fixed and deployed the same day: the cold
  start at 1024 MB took **1.09 s** (init 292 ms + handler 795 ms; 1.68 s end to end through
  Cognito), warm runs 7 ms; the refusal ends in one full stop.
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

*Built 2026-11-07* (1229 offline pytest, 42 tests_db, 135 Vitest):
- **3.1** The admin GUI writes `adapterId` (this Pi's `THING_NAME`) at registration and at
  re-registration, and refuses to re-register a camera whose row names another adapter (D11's
  guard, taken now since the field exists). `rematch_cameras.adopt_unstamped` stamps rows without
  `adapterId` whose MediaMTX path this Pi serves — configured or published, so the USB `cam-01`
  too — one conditional write each, on a Pi whose `adapter.env` says `ADOPT_UNSTAMPED_ROWS=yes`
  (D12, taken now): `config.flag()`, the one kind of key that may be absent, absent meaning no.
- **3.2** Three functions in all twelve camera and clip Lambdas, identical (the twin test now pins
  them): `tenant_access` (control / view / none, from `vsz_adapters` and `vsz_cameras`; a row
  without `adapterId` is an administrator's alone), `tenancy_refusal` (logs each refusal as one
  JSON line; `TENANCY=report` refuses nothing; unset means enforce), `view_only_response` (403).
  A hidden camera gets the route's own unknown-camera answer; the three clip Lambdas, which had
  none, answer 404 "no such clip" and now read the camera row (`CamerasRegistryRead` on their
  roles). `list-cameras` lists only what the caller reaches, each camera with `access`.
  `tests/cloud/test_tenancy.py` (186): every route × holder, Super Admin, a user with `cam-03`
  shared, one with `cam-04` shared, another customer, dealer, owner, administrator, an old pool's
  token, no token; the unstamped row; the list; the logging; report mode; unset = enforce. Breaking
  one route's need, or one copy of a helper, turns it red.
- **3.3 tooling.** `provision.sh tenancy`: the clip roles' policy, `TENANCY=report` on the twelve,
  `vsz-users` added to the authorizer — read back, every old pool still there — and the stage
  deployed. `deploy.sh tenancy status | log [HOURS] | report | enforce | old-pool-off`;
  `old-pool-off` refuses while any function doesn't enforce. Guards: `deploy.sh` won't deploy a
  Lambda with the check to a function without `TENANCY` (unset would enforce at once), and
  `deploy.sh page` won't publish a page whose sign-in pool the authorizer doesn't accept.
  `client-app/src/config.ts` names `vsz-web` now, so the next page deploy is the switch.

*Rollout (3.3), as it goes:*
- 2026-11-07: `ADOPT_UNSTAMPED_ROWS=yes` in the Pi's `/etc/adapter/adapter.env` (the user); the
  next `kvs-camera-rematch` run stamped `cam-01`…`cam-04` with `adapter-01` — checked in the
  registry. (A dry run before had named the same four, the USB camera included.)
- 2026-11-07: `provision.sh tenancy`, `deploy.sh lambdas`, `deploy.sh page` (the user): the page signs
  in to `vsz-users`; the administrator signed in and set a password, and sees all four cameras.
  Report mode checked: at 14:05 `demo-user` listed the cameras and `list-cameras` logged
  `would-refuse hidden` for exactly `cam-01`, `cam-02` and `cam-04` — `cam-03`, the shared one,
  passed — while still showing all four, as report mode must. Enforcing is the next step.
- 2026-11-07: `deploy.sh tenancy enforce` (the user), after a clean `tenancy log`; the page as
  `demo-user` shows only `cam-03`. *Verify*, against the live API with each demo account's own
  token: `GET /cameras` lists `cam-03` (`view`) for demo-user, all four (`control`) for holder and
  Super Admin, nothing for dealer and owner; the dealer's `GET /hls?stream=cam-03` and demo-user's
  for `cam-04` get "unknown stream" — the same words as a camera that doesn't exist (`cam-99`);
  demo-user changing a `cam-03` clip gets 403 "shared with you to view, not to change", while the
  holder passes the check (and S3 then has no such clip — the probe used one that doesn't exist).
  VMS's `pir-api` still accepts only `kvs-demo-users`.
- 2026-11-07, 14:37: `deploy.sh tenancy old-pool-off` (the user, after committing Phase 3):
  `kvs-demo-api`'s authorizer accepts only `vsz-users` now, stage deployed; demo-user still lists
  `cam-03` through it, a bad token gets 401. `pir-api` (VMS) unchanged, on `kvs-demo-users`.

### Phase 4: Directory API and the System console

`vsz-directory-api`, `/directory/{proxy+}` (`provision.sh directory`), the routes of §6 for the
system roles; pytest through `invoke()` with a stubbed Cognito and a fake `vsz-directory`; the
System console screens (§7.2), lazy-loaded; route guards by role in the page (Vitest).

*Verify:* as admin, create a dealer and its first user; the invitation arrives; that user signs in,
sets a password, lands on the dealer portal (empty); as owner, the Users tab can't create an admin.

*Built 2026-11-07* (1253 offline pytest, 63 tests_db, 177 Vitest):
- **`vsz-directory-api`** (`cloud/lambda/vsz_directory_api.py`), outside the VPC, on
  `/directory/{proxy+}` (ANY, behind the Cognito authorizer). It sends `vsz-directory` the token's
  `sub` and nothing else — never the role in the token — and turns each route into one operation:
  `GET me | overview | dealers | customers[?dealerId=] | adapters | users | pricing`, `POST dealers`,
  `PATCH dealers/<id>`, `POST users` (an invitation), `POST users/<id>/disable | enable |
  reset-password | resend-invite`, `PUT pricing`. An invitation is the directory's row first, then
  Cognito's `AdminCreateUser` (the email with a temporary password), then the new `sub` linked; if
  Cognito refuses, the row is cancelled, and an address in use is a plain "can't add this address".
  A disable is the directory's first — the trigger refuses at the next token whatever Cognito
  says — then Cognito's, with every session signed out. Its role may invoke `vsz-directory` and make
  those five Cognito admin calls on `vsz-users`, nothing else (`cloud/iam/vsz-directory-api-policy.json`).
- **`vsz-directory`'s operations for signed-in people** (`USER_OPS`): the person is looked up by
  `sub` on every call (`resolve_actor`; disabled, or in a suspended organisation → 403), writes run
  in one transaction and are audited. D6 as two rules: **`may_give`** (an invitation) — an
  administrator any role, an owner a dealer's staff or a customer's holder, a dealer only the holder
  of its own customers; a holder's Super Admins and users are Additional users (Phase 9).
  **`may_manage`** (disable, enable, reset, resend) — an administrator anyone, an owner everyone
  but the system's staff, a dealer its own customers' people; never one's own account; a person
  in two dealers' customers is neither dealer's alone. The price list is replaced whole, every
  resolution × recording × storage duration priced, or refused.
- **The System console** (`client-app/src/views/system/`), loaded only when opened: Overview
  (counts, plans, memberships by role), Dealers (create, suspend / activate, invite a dealer's
  user), Customers (by dealer, read-only until Phase 5's pages), Users (search; invite, with the
  roles the organisation and the caller's role allow; disable / enable, reset, resend), Adapters
  (read-only until Phase 6), Pricing (the matrix in currency units, durations, annual discount,
  trial; the placeholder warning until saved once). Routes carry their roles: a dealer lands on
  `/dealer` (a placeholder until Phase 5), an owner on the System console, everyone else on Live; a
  route for another role sends you home. The rail shows each role its entries; the camera list and
  camera loading are only for the roles that have cameras.
- **Tests:** `tests/cloud/test_directory_api.py` (the routes, the sub-only rule, the invitation and
  its undo, disable's sign-out, CORS on every answer; Cognito under a Stubber);
  `tests_db/test_directory_ops.py` (21, every operation as each demo person on the real schema);
  `client-app/tests/console.spec.ts` (42: homes and guards per role, the rail per role, each tab
  against a faked API). Each was broken on purpose once — the router's role check, an owner's
  roles, the cents, `may_give`, `may_manage` — and turned red.

*Deployed 2026-11-07* by the user (`deploy.sh lambda vsz-directory`, `provision.sh directory`,
`deploy.sh page`); the user reports the *Verify* above passes.

### Phase 5: Dealer portal

The screens of §7.1 and their routes: customers, per-adapter plans with live price and the
customer total, plan history, cameras,
users, adapters, pricing, dealer details. The pricing function in Python and TypeScript with the
shared vectors.

*Verify:* as the demo dealer: create a customer and invite its holder; set and change the demo
customer's plan for `adapter-01` (its history shows both versions with the price). Activating an
adapter for the new customer waits for Phase 6. `tests_db/`: a plan belongs to one activation;
a version's price is the server's.

*Built 2026-11-07* (1300 offline pytest, 91 tests_db, 218 Vitest):
- **The routes** (§6, on `vsz-directory-api`): `GET/POST customers`, `GET/PATCH customers/<id>`,
  `GET customers/<id>/adapters`, `GET customers/<id>/users`, `PUT adapters/<id>/plan`,
  `POST adapters/<id>/plan/cancel`, `GET adapters/<id>/plans`, `GET/PATCH dealer`. Two of §6's
  are folded in: `customers/<id>/adapters` carries each adapter's cameras and plan vs. actual, so
  `customers/<id>/cameras` isn't a route of its own, and an adapter's current plan comes with it
  (no `GET adapters/<id>/plan`). A customer's holder is invited with `POST customers` (`holder`)
  or `POST users`, as any invitation; adding Super Admins and users stays the holder's (Phase 9).
- **The camera registry is read by `vsz-directory-api`**, since `vsz-directory`, in the VPC
  without a NAT, can't reach DynamoDB: one scan, `ProjectionExpression` naming only the portal's
  fields (no address, user or password ever reaches the function), unregistered rows left out;
  the thumbnails' times by `HeadObject`, never the picture (D5). Its role gets this through
  `provision.sh portal` (`cloud/iam/vsz-directory-api-inventory-policy.json`, and `THUMB_BUCKET`).
  A registry that can't be read leaves the counts out and says so; the rest of the answer stands.
  A camera's class comes from its picture size (`recordSize`; ≤ 2.1 Mpx 2 MP, ≤ 4.2 Mpx 4 MP,
  else 8 MP — unknown when the row has none, as `cam-01`'s), its recording from its mode (`manual`
  → continuous, the detection modes → motion).
- **Plans** (D9, D19): every save is a new version of the adapter's activation's plan, priced by
  the server (`price_plan`; a price the page sends is ignored), its six lines stored whole; the
  activation row is locked while a version is added, and `basedOn` (the version the page edited)
  must still be the newest, or 409. Saving the same plan again adds nothing. A plan saved during
  the trial stays a trial (its end carried over); a trial whose end has passed reads as active
  (`effectiveStatus`). *Cancel subscription* is a version with status `cancelled` at no charge;
  saving a plan starts it again. A customer's total adds up its trial and active plans, an annual
  one as a twelfth of its year.
- **The price, twice, kept the same:** `price_plan` (Python) and `pricePlan` (`client-app/src/
  pricing.ts`) are both tested against `tests/fixtures/pricing-cases.json` (12 cases: rounding
  both ways, the largest plan, no discount and the largest, a missing price). The web job now also
  runs when that file changes.
- **The portal** (`client-app/src/views/dealer/`): tabs Customers · Cloud adapters · Billing ·
  Pricing · Dealer details (a dealer's own). The customer list (search, status; holder, adapters,
  cameras in plans / registered, the monthly total) and *New customer* (first site with its time
  zone, optional holder); per customer Plan (adapter picker, cameras per class with what the
  registry counts beside each, storage, payment, the price as it is edited, plan vs. actual, the
  customer's total, Save plan, Cancel subscription after asking), Plan history (who, when, what
  changed, price), Cameras (by adapter, no pictures), Users (the holder invited if there is none;
  disable, enable, reset, resend where `may_manage` allows), Details (contact, suspend /
  reactivate, sites). Billing shows each customer's total with the demo banner and no payment
  fields; invoices come with Phase 8. **Owners and administrators open the portal too** (D7): the
  rail shows it beside System, its list spans every dealer with a filter, *New customer* asks for
  the dealer (Direct first), and System → Customers is the same list.
- **Tests:** `tests_db/test_portal.py` (28): another dealer's customer is "no such customer" on
  every route, a customer and its first site, suspension signing its people out, versions priced
  by the server, a stale edit refused, the same plan saved twice, what a plan may hold, cancel
  and subscribe again, a trial that has ended, **a plan belongs to one activation** (released, the
  plan is history; at the next customer it starts at version 1), the customer total, a dealer's own
  details. `tests/cloud/test_directory_api.py` (+34): each route with its path, the registry
  counts across pages, plan vs. actual and the classes, a credential never read, an unreadable
  registry, a holder's invitation and its refusal. `tests/cloud/test_pricing.py` and
  `client-app/tests/pricing.spec.ts` (the shared vectors); `client-app/tests/portal.spec.ts` (28).
  Broken on purpose once each, turning red: a dealer opening another's customer, the page's price
  stored, no `basedOn` check, the annual discount dropped (both sides), the scan's field list
  dropped, the registry's second page dropped, `basedOn` always 0, the portal for dealers only.

*Deployed 2026-11-07* by the user (`deploy.sh lambda vsz-directory`, `lambda vsz-directory-api`,
`provision.sh portal`, `deploy.sh page`); the user reports the *Verify* above passes.

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

*Built 2026-11-07* (1335 offline pytest, 117 tests_db, 238 Vitest), with D15 amended (above): the
owner's script makes the AWS identities. Built across two sessions of the same conversation, after
an SSH drop left the first one running unseen; the second took over at a clean point.
- **6.1, the directory** (`vsz-directory`): `generate_adapters` (1–20 at a time, numbered from the
  highest `adapter-NN` under an advisory lock — `adapter-02`, `adapter-03`, … — each with a Cloud ID:
  10 Crockford base32 characters from `secrets`, stored only as HMAC-SHA256 under `CLOUD_ID_KEY`, a
  64-hex key in the function's environment that `provision.sh adapters` makes once and never prints;
  read back with Crockford's rules, O as 0, I and L as 1), `allocate_adapter`, `new_code`,
  `activate_adapter`, `release_adapter` (the activation and its plan end — a last version `ended`,
  lines kept — and a new code is issued), `remove_adapter` (the holder's or a Super Admin's release,
  no code), `revoke_adapter`, `adapter_state`. **Activation:** the target first (a holder or Super
  Admin one of their accounts, `accountId` when they have several; a dealer one of its customers;
  owners and administrators any; a customer's user nobody), then what isn't the code (site, name —
  a mistake there costs no try), then the code: unknown, used, revoked or another dealer's all get
  "this Cloud ID can't be activated" and an `activation_attempt`. Five failures per person or
  twenty per address in the last hour refuse everything with 429, and a refused try isn't counted.
  A refusal that must keep its attempt is a `KeepAndRefuse`, which `run_user_op` commits before
  raising. An adapter in nobody's stock joins the customer's dealer's; the activation starts plan
  version 1 as a trial with six lines at 0. `list_adapters` serves customers too (the adapters of
  accounts they hold or are Super Admin of), and every row says `connected`, `hasCode`, `identity`.
  `me` lists each account's sites. Operator operations `adapters` and `identity_made` serve the
  script; IoT lifecycle events (no actor) are presence. Migration 002 adds `adapter.connected`.
- **6.2, the API** (`vsz-directory-api`): `POST adapters` (the codes, once, and the script command
  to run), `adapters/activate` (the address for the rate limit is API Gateway's, never the body's),
  `adapters/<id>/allocate | new-code | release | remove | revoke | sync`. After a change it publishes
  the adapter's **retained** config (`adapter/<thing>/config`, `{"activated", "at"}`, qos 1); when an
  adapter leaves a customer it releases its camera rows (`SET releasedFrom REMOVE adapterId`,
  conditional on `adapterId`, so a camera rematched meanwhile is left alone) — and at activation too,
  as a backstop against rows left over. A failed follow-up is reported (`registryError`,
  `configUnsent`), never undone; `sync` does both again from the directory's state. Its policy
  `adapters` (`cloud/iam/vsz-directory-api-adapters-policy.json`): `iot:Publish` and
  `iot:RetainPublish` on `topic/adapter/*/config`, `dynamodb:UpdateItem` on `cameras`.
  **`cloud/adapter-identity.sh`** (`pending`, `create THING…`, `revoke THING`, `--dry-run`): `create`
  refuses `adapter-01` (shared with the VMS card), an adapter the directory doesn't know or has
  revoked or already gave an identity, a Thing that already has certificates, and an existing
  folder; it writes `~/.local/state/videosafezone/adapter-identities/<thing>/` (0700: the four files,
  `.cert-arn`, `thing-name`), attaches `KVSAdapterThingPolicy`, proves the result and records the
  certificate in the directory. `setup-identity.sh restore <folder>` refuses a folder whose
  `thing-name` isn't `adapter.env`'s `THING_NAME`, and prints the line that fixes it.
- **6.3, the Pi:** `agent.py` subscribes to its config topic and writes what it says to
  `$XDG_RUNTIME_DIR/vms/activation.json` (`adapter/activation.py`, atomic); the admin GUI refuses to
  register a **new** camera unless it says activated — unknown refuses too, with the reason;
  re-registering a camera that is already there stays possible. Found while there: after an outage
  longer than its MQTT session, the agent came back without its subscriptions (FoundAndFixed.md #91,
  fixed).
- **6.4, last seen:** the IoT rule `vsz_adapter_presence` (`cloud/iot/vsz-adapter-presence-rule.json`)
  hands `$aws/events/presence/+/+` of `adapter-*` clients to `vsz-directory`, which keeps the newest
  event (an older one is ignored): `last_seen_at` and `connected`.
- **6.5, the screens:** System → Adapters (New adapters with count, label and a dealer's stock; the
  codes and the script command shown once, with printable labels; per adapter Allocate, New code,
  Release and Revoke after asking, Sync; connection, code, identity); the dealer portal's Cloud
  adapters tab shows the same list read-only. **Enter Cloud ID** (`ActivateAdapter`: the field
  upper-cases and adds the dash, a drawing of the label's place; then an existing location or a
  new one with its time zone, and a name) for the customer (**Add adapter**, `#/adapters/add`, from
  Live's empty state and Settings → Cloud adapters, which lists the account's adapters and removes
  one after asking) and for a dealer's customer (the customer's Plan page: *Activate an adapter*).
  After activating or removing, the page renews its token and reloads the cameras.
- **Tests:** `tests_db/test_adapters.py` (26): codes kept only as keyed hashes, numbering, the
  reading rules, activation at a new site with its own trial and the next token's claims, a code
  used twice, five wrong codes then the hour's block (and another person still may), the hour's
  window, twenty from one address, another dealer's adapter, mistakes that cost no try, who may
  activate, a person in two accounts, release (plan ended, history kept, new code, the next
  customer afresh), remove, revoke, allocation, presence in and out of order, the script's record,
  what customers see. `tests/cloud/test_directory.py` (+5: the committed refusal, presence, the
  operator's list, the key); `tests/cloud/test_directory_api.py` (+13); `tests/test_activation.py`,
  `tests/adapter/test_agent.py`, `tests/admin/test_admin_registration.py` (the Pi side, #91);
  `client-app/tests/adapters.spec.ts` (20). Broken on purpose once each, turning red: no rate
  limit, another dealer's adapter accepted, the attempt rolled back, a release keeping the old code,
  the body's address trusted, the registry write without its condition (which first went
  unnoticed: the test faked the condition's failure itself, and now moves the camera between scan
  and write instead), no token renewal, the actions shown to a dealer, a wrong code left on the
  location step.

*Deployed 2026-11-07* by the user (migration 002, both directory functions, `provision.sh adapters`,
the Pi's agent and admin GUI restarted, the page). Checked read-only the same evening: the three
functions are live = HEAD, migration 002 applied, `CLOUD_ID_KEY` set (64 characters), the policy
`adapters` matches `cloud/iam/`, the IoT rule `vsz_adapter_presence` enabled with its permission;
the directory recorded `adapter-01` connected at 20:56:02 UTC, three seconds after the agent's
restart (presence works end to end); the agent logged the retained `activated: true`; the page on
CloudFront carries the new build. Not yet tried: the activation flow (only `adapter-01` exists, no
Cloud ID used) — the *Verify* above.

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

*Built 2026-11-07* (1419 offline pytest, 117 tests_db, 251 Vitest), again across two sessions of
the same conversation after another SSH drop; the second took over at a clean point and reviewed
the first's work before going on.
- **7.1:** registration moved out of `app.py` into **`adapter/camera_registration.py`**
  (`register(body, env)` → `(answer, status)`, with an `Env` of collaborators: the table, a KVS
  client, provisioning, the wait for video, reading and recording detection), shared by the admin
  GUI and the agent; behaviour unchanged — the admin tests passed with no test file touched. Its
  collaborators (`provision`, `wait_for_flowing`, `read_detection`, `record_detection`) and
  Unregister (`unregister`) moved with it; `app.py` keeps thin wrappers that look up its own `time`
  and `subprocess` at call time, so its tests' fakes still apply.
- **7.2, D12:** `camera_control.is_mine(row)` — `adapterId` is this Pi's Thing, or none with
  `ADOPT_UNSTAMPED_ROWS` and no `releasedFrom` — and `acts_on(row)` (mine and not unregistered),
  applied in `sync_mediamtx_paths` (and so rematch), `schedule_runner`, `event_watcher`,
  `codec_caps` and the outage buffer's cache (and so thumbnails); the agent ignores another
  adapter's camera but for Stop; the admin GUI lists only its own and answers 409 to every change of
  another's (Stop stays), and the name route treats another adapter's camera as unknown.
  `tests/adapter/test_foreign_rows.py`, `tests/admin/test_admin_adapters.py`.
- **7.2, D11:** a new camera without an ID gets the next one past every adapter's
  (`next_camera_id`; `cam-01` … `cam-9999`, the pattern widened to `^cam-\d{2,4}$` in the admin
  GUI, `provision-camera.sh`, `stream-channel.sh` and the sudo rule in `setup-system.sh` — reinstalled
  by re-running it, needed only from `cam-100`); the admin page no longer asks for an ID. **The ID is
  claimed before anything is made** (`claim_camera_id`: a row of its own, marked unregistered so
  every reader skips it, written with `attribute_not_exists`), and the camera's row is written over
  the claim at the end. The first version claimed the ID only at the end, after the path, the unit
  and the KVS stream: two registrations of one ID on one Pi would have had the loser rewrite the
  winner's channel file, start its unit into the winner's stream and stop it again — found in
  review before anything shipped. A failure after the claim leaves it abandoned (`abandonedAt`,
  still unregistered): the device role may update rows but not delete them, and widening the role,
  shared with the VMS card, isn't worth it; MediaMTX already holding the path is checked before the
  claim, so the commonest failure leaves none. A typed free ID is still accepted.
- **7.3:** the agent's commands **`discover`** (WS-Discovery, each device marked against the
  registry: added here, removed here — adding it brings it back as it was —, another adapter's, or
  new; another adapter's camera ID isn't shown), **`register`** (only a request ID travels; the
  agent reads the request from `camera-requests`, publishes "registering" at once — the IoT rule
  writes the whole item, so the password leaves the table as soon as the agent has it — asks the
  camera with that login (`enrich_sync`), builds what the admin form would send
  (`body_from_discovery`: the largest stream, 10 fps where offered, else 15, the IR and microphone
  facts) and registers through `camera_registration`) and **`unregister`**; the long ones run in a
  thread. Their answers go to `adapter/<thing>/discovery` and `…/registration`; IoT rules
  `vsz_adapter_discovery` and `vsz_camera_registration` (explicit fields, the Thing from the topic,
  `ttl` 15 minutes) store them in **`adapter-discovery`** and **`camera-requests`**. **Lambda
  `adapter-cameras`** on `kvs-demo-api` `/adapters/{proxy+}`: `POST <thing>/discover`,
  `GET <thing>/discovery`, `POST <thing>/cameras`, `GET <thing>/cameras/requests/<id>`,
  `POST <thing>/cameras/<cameraId>/remove` (POST, not DELETE: the route's CORS stays GET and POST);
  only for an adapter in the caller's `vsz_adapters` (or `*`), anyone else is told it doesn't exist;
  no answer, error or log carries a request's login. **`publish-cmd`** sends each command to the
  camera's own adapter (`adapter/<row.adapterId>/cmd`; a row without one keeps the old topic).
  `list-cameras` returns `adapterId`. **The camera ID is allocated by whoever registers** — on the
  Pi, in `camera_registration` — not by the Lambda as D16 step 3 said: the claim makes that safe,
  and the Lambda then never writes the registry. **After a release** the agent, hearing
  `activated: false`, stops the producers and live views of every camera on this Pi that isn't its
  own any more: nobody with access would press Stop (the cost rule).
- **7.4:** **Add cameras** (`#/cameras/add/:thing?`, from Live's empty state and Settings → Cloud
  adapters): the adapter (picked when there is one) looks; the page polls for its own scan's answer
  (a minute, then "is it online?", with when it was last seen); per camera a name and the ONVIF
  login, sent once and not kept; progress until it is registered, then Live and Settings know it.
  **Remove camera** in Settings → Camera, for whoever controls it on an account (not for a shared
  camera, an administrator's own, or the USB camera): the adapter unregisters it; adding it again
  brings it back with its recordings.
- **Tests:** `tests/admin/test_admin_adapters.py` (27: D12 in the admin GUI; D11's allocation, a
  claim taken meanwhile, a typed ID taken meanwhile touching nothing, the ID range, a claim skipped by
  readers), `tests/adapter/test_foreign_rows.py` (11), `tests/adapter/test_agent.py` (+13: discovery
  and its marks, a registration end to end with the password in no MQTT message, another adapter's
  request, a removed camera brought back, a wrong login, unregister, released cameras stopping),
  `tests/cloud/test_adapter_cameras.py` (24), `tests/cloud/test_command_lambdas.py` (+1: routing),
  `client-app/tests/cameras-add.spec.ts` (13). Broken on purpose once each, turning red: no claim,
  the first page only, every row mine, a released row adopted, Stop refused, no "registering"
  report, the request in a report, another adapter's request accepted, anyone controlling any
  adapter, the login in a status answer, `publish-cmd` back to one adapter, the password kept in the
  form, any scan taken for this one, Remove offered for a shared camera, released cameras never
  stopped.

*Added 2026-11-08, at the user's request* (after trying the first deploy): **unregister any camera,
the USB one included; an unregistered camera stays in the camera list, out of Live, and comes back
with Re-register — managed from Cloud adapters → the adapter's Cameras page.** Built: the page
`#/cameras/add/:thing?` became the adapter's **Cameras**: its cameras (USB or network, in Live or
unregistered) with Unregister after asking or Re-register, above *Find new cameras*, where a device
found unregistered offers Re-register too; Settings shows an unregistered camera's Re-register card
only, and its Unregister card covers the USB camera. Behind it: `camera_registration.unregister`
takes the USB camera (its capture `kvs-camera-publish` stopped, and a mark on the Pi,
`$XDG_STATE_HOME/videosafezone/unregistered/cam01`, that `publish-cam01.sh` checks at every start —
the decision must hold with AWS unreachable, and `setup-launch.sh` re-enables user units);
`camera_registration.reregister` brings a camera back from its row (the USB camera's capture
started; a network camera through `register`'s re-registration with its stored login); the agent's
`reregister`, `adapter-cameras`' `POST <thing>/cameras/<id>/reregister`, the admin GUI's `POST
/api/cameras/<id>/reregister` and its unregistered rows with Re-register; **`list-cameras` lists
unregistered cameras, marked** (`unregisteredAt`), and the page keeps them out of Live, out of the
players and out of the Live-view settings. Tests: `tests/admin/test_admin_adapters.py` (+4),
`tests/adapter/test_agent.py` (+1), `tests/cloud/test_adapter_cameras.py` (+1),
`tests/cloud/test_list_cameras.py` (listed, marked), `client-app/tests/cameras-add.spec.ts` (+3) and
`live.spec.ts` (+1); broken on purpose once each and red: no mark for the USB camera, the list
hiding them again, the agent refusing Re-register, Live showing them. Again built across two
sessions after an SSH drop; the second took over at a clean point.

*Deploying, 2026-11-08* (the user): `deploy.sh lambda list-cameras` and `lambda publish-cmd` with
`--force` — Phase 7 was committed first, so the live code (checked: exactly the Phase 6 commit's)
matched neither the tree nor HEAD; `provision.sh cameras` made everything, and its self-check failed
once on a deployment still reaching the edges (FoundAndFixed.md #92, fixed); re-run, verified: the
demo holder reaches `/adapters/adapter-01/discovery` ("no scan yet"), the demo dealer gets "no such
adapter". Left: the Pi's agent and admin GUI restarted, `deploy.sh page`, the *Verify*. Done the
same day: the Lambdas live, the agent and admin GUI restarted, the page deployed.

*Changed 2026-11-08, at the user's request* (after trying it): **an unregistered camera is out of the
camera list too** — only the adapter's Cameras page (Cloud adapters → Cameras) lists it, with
Re-register. The camera list and the default camera of Settings and Events take cameras in use
(`cameras.active`); an old link to an unregistered camera's Settings says so and links to that page,
and the Re-register card in Settings is gone. Tests: `client-app/tests/cameras-add.spec.ts` (the list
leaves it out; the old link), each broken on purpose once. Found in the same try: the PTZ camera had
been added a second time from the web page as `cam-05` — it announced a new WS-Discovery identity,
and the web scan matched by identity alone (FoundAndFixed.md #94: fixed, the duplicate removed).

### Phase 8: Billing seam (fakes)

`BillingProvider` and `FakeBillingProvider` (D9); trial length from `price_setting`; the Billing
tab; the admin's "run billing period (fake)"; invoices listed per dealer and customer.

*Verify:* a fake period run produces one invoice per customer, with a line per adapter's active plan
at its stored price; a trial plan adds no line until its trial ends; the banner is on every billing screen.
In detail, after Phase 8's build: as the demo owner, System → Billing → *Run billing period (fake)*
for the month under way: the demo customer gets no invoice while `adapter-01` is on its trial (the
result says so: "nothing to pay"); after the demo dealer sets a plan and the trial has ended (or on a
customer whose trial has), a run of a later month — or of this one, for a customer not billed yet —
makes its invoice, a line per adapter at the price shown on its Plan page (an annual plan a twelfth
of it); running the same month again makes nothing new. The dealer sees the invoice on its Billing
tab and on the customer's Invoices page, another dealer doesn't; the banner is on all three screens.

*Built 2026-11-14* (1441 offline pytest, 137 tests_db, 267 Vitest; again across two sessions of the
same conversation after an SSH drop — the second took over at a clean point, with the first's account
of every file):
- **Trial length from `price_setting`** was already done: activation (Phase 6) starts the trial
  with `trial_days`, and System → Pricing edits it (Phase 4).
- **The fake run** (`vsz-directory` `run_billing_period`; `POST /directory/billing/run-period
  {period: "2026-11"}`): a billing period is a calendar month in UTC; each customer with something
  to pay gets one invoice, `issued`, `provider = 'fake'`, with a line per adapter plan **as it
  stood at the month's end** — or now, for the month under way: the newest version saved by then,
  billed when it was paid for then (active, or a trial whose end had passed), at the price stored
  with it, never the price list's of the day. An annual plan is billed a twelfth of its year each
  month, as the customer totals count it — a real provider would bill the year at once, behind the
  same seam. An adapter released by then adds nothing. A customer invoiced for the month already
  keeps its invoice (a second run bills only the rest; `ON CONFLICT` covers two runs at once); a
  month that hasn't begun is refused. **Owners and administrators** run it, as D6's table says —
  §6 had said administrators only, and was corrected. The answer counts what was invoiced, what was
  invoiced already, what had nothing to pay, and what was skipped (plans in two currencies).
- **Invoices** (`list_invoices`; `GET /directory/invoices`): a dealer its customers', owners and
  administrators every one or a dealer's; `?customerId=` (another dealer's customer: "no such
  customer"), `?period=`; the newest 500, then `truncated`. **The estimate** (`billing_preview`,
  the provider's quote): what a run would bill a customer for this month now, nothing written;
  `GET /directory/customers/<id>/invoices` gives both.
- **The seam** (`vsz-directory-api`): `BillingProvider` with D9's five — `quote`, `start`, `change`,
  `cancel`, `list_invoices` — and `FakeBillingProvider`, whose `run_period` is the fake's own (a real
  provider runs its own cycles). It sits outside the VPC on purpose: a real provider is an internet
  API, which `vsz-directory`, in the VPC without a NAT, can't reach. The provider is told after the
  directory has changed — activation starts a plan; a saved change changes it; cancel, release,
  removal and a revoke of an activated adapter cancel it — and one that fails is reported
  (`billingUnsent`), never undone. The fake tells no one: the plan's versions are the record.
- **What an invoice may hold, kept by the database** (`003_invoice_rules.sql`): a line bills a plan
  of the invoice's own customer, in the invoice's currency; each adapter activation once per
  invoice; the total is the sum of the lines, checked at commit (deferred constraint triggers).
- **Screens:** System → **Billing** (the banner, *Run billing period (fake)* for a month up to this
  one, what the run made, every invoice with a dealer filter); the portal's Billing tab gains the
  invoices; a customer's pages gain **Invoices** (the month so far, then the months billed). An
  invoice opens to its lines: the adapter, its plan version, cameras, storage, and an annual plan's
  year. The banner ("Demo billing: no payment is taken.") is one component on all three; no card or
  bank field anywhere.
- **Tests:** `tests_db/test_billing.py` (20), `tests/cloud/test_directory_api.py` (+9: the routes and
  their filters, the run's body, the hooks after each change and none after a refusal, a failing
  provider reported, the fake telling no one), `client-app/tests/console.spec.ts` (+5) and
  `portal.spec.ts` (+7). Broken on purpose once each, turning red: the annual line billed whole, the
  trial ignored, the month's end ignored, a released adapter billed, the "invoiced already" check
  dropped (first green: `ON CONFLICT` still stopped the second invoice, so the test now pins the
  count too), a dealer seeing every dealer's invoices, a dealer running billing, each of 003's four
  rules, the hook after a release, a change told when nothing changed, the run passing the whole
  body, the filters dropped, a month to come accepted by the page, the annual line's text, the
  customer column on the customer's own page.

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

*Built 2026-11-14* (1461 offline pytest, 164 tests_db, 290 Vitest):
- **The directory's operations** (`vsz-directory`): `account_plans`, `account_users`, `add_account_user`,
  `update_account_user`, `remove_account_user`, `resend_account_invite`, `hand_over_holder`. An account is
  administered by its holder and Super Admins (and, for support, a system administrator); anyone else —
  a user, a dealer, another account's holder — is told "no such account". **Plan & Billing is the
  holder's alone**: each adapter's current plan and its history, read-only, the total, the invoices and
  this month's estimate (Phase 8's), and the dealer's contact, since the dealer changes plans.
- **The rules** (each with its own `tests_db` test): one holder — a hand-over swaps the two roles in one
  transaction, the old holder staying on as a Super Admin, and only to someone who has signed in;
  **nobody changes the holder's access, or their own**; a share only on one of the account's open
  adapters — checked by the operation, with 001's trigger behind it; **a staff address gets the generic
  "can't add this address"**, the same as Cognito's refusal of a taken one, so it tells nothing; a
  release ends the shares (001's trigger, now also through the operations and the claims); a Super Admin
  sees every camera, so becoming one drops the person's shares; at most 100 cameras shared with a person
  across accounts, since every one goes into their ID token (D18).
- **Add user** (§6's invite flow): an address that has an account gets the membership at once — at their
  next token, within about 3 minutes; a new one is an invited person, whose Cognito account the API
  creates (the email with a temporary password), and whose sub the holder may store (`link_sub` now
  allows that for someone invited to one's own account and not signed in yet); if Cognito refuses, the
  directory's rows are taken out again. **The API checks each camera against the registry** and passes
  the adapter the registry names; the directory then checks that adapter is the account's. **Remove**
  and **Cancel** of a pending invite take the membership and its shares; someone invited who never
  signed in and is in no other account is deleted, and the API deletes their Cognito account
  (`AdminDeleteUser`, a new inline policy, `provision.sh accounts`) — without it the address could never
  be invited again. **Resend** sends Cognito's invitation again. Removal is `POST …/remove`: the
  `/directory` preflight allows no DELETE (§6 corrected, as for adapters).
- **`/directory/me`** gives each account's adapters (Thing, name, site): every one for a holder or Super
  Admin, those of the cameras shared for a user — for the camera list.
- **Screens:** the Account page's tabs follow the person's roles — Account details for everyone; **Plan &
  Billing** for a holder (each adapter's plan as on the dealer's Plan page, read-only, its history, the
  total, the month so far, the invoices, the demo banner; no control or input at all); **Additional
  users** for a holder or Super Admin — Add user (email, the Super Admin switch, the cameras to share
  when it is off, D18's texts), Users (Super Admin, or the cameras shared; Edit, Remove after asking —
  neither on oneself), Pending invites (Resend, Cancel after asking), and the holder's card with *Hand
  the account over…*. An **account picker** appears on both tabs for someone with more than one such
  account. Dealers, owners and administrators keep Account details only and the page never asks the
  directory for them. The **camera list groups by place** — account (when there are several), site,
  the adapter's name — then by tag as before, with no heading when there is one place; an
  administrator's view is unchanged. The month-so-far block is one component on the portal's Invoices
  page and on Plan & Billing.
- **Tests:** `tests_db/test_accounts.py` (27), `tests/cloud/test_directory_api.py` (+20: the routes, the
  registry check before the directory, Cognito only for a new address, the undo when it refuses, the
  sign-in deleted with a cancelled invitation and kept otherwise, a failed deletion reported, the policy
  naming the same pool), `client-app/tests/account.spec.ts` (+17), `shell.spec.ts` (+4) and `places.spec.ts`
  (2: the camera list asks the directory for a customer, not for staff). Broken on purpose once each,
  turning red (26): the holder's access changeable, a staff address added, a share on another account's
  adapter left to the trigger, a new Super Admin keeping shares, a cancelled invitation's person kept,
  a person in another account deleted with it, a new holder keeping shares, a hand-over to someone who
  never signed in, a user managing the account, Plan & Billing for Super Admins, a user's `/me` listing
  every adapter, a holder storing anyone's sub, no limit of 100 shares; in the API a camera the registry
  doesn't serve passed on, Cognito asked for an existing address, a cancelled invitation's sign-in kept,
  another account's cameras offered; on the page a user given Additional users, a Super Admin given Plan
  & Billing, Edit on oneself, a new Super Admin sent cameras, Remove without asking, a hand-over to
  someone invited, one place headed, the account named when there is one — and the camera list never
  asking for the places, which first stayed green (the shell asked, and no test mounts the shell): the
  list now asks itself, and `places.spec.ts` pins it.

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

*D14 decided 2026-11-14: VMS gets its own copies* (§4b). So the order is: 10.1 now; 10.2 built in
steps that keep VMS's card working on the shared table and role — additions only (the secrets read
from SSM first with the row as the fallback, tags on the streams, an adapter index, the registry
path over MQTT); the role narrowed, exported back to `cloud/iam/`, and the secrets taken out of the
rows only once VMS runs on its own copies. Which steps exactly follows the spike's results.

**What a Pi reaches today** (2026-11-14, from the code and `cloud/iam/`; all through
`KVSAdapterRole`, shared by every adapter and by the VMS card) — and D13's aim for each:

| Who on the Pi | Today | D13's aim (spike checks) |
|---|---|---|
| producers (kvssink), registration, live view, `stream-codec.py`, agent | KVS `DescribeStream`, `GetDataEndpoint`, `PutMedia`, `CreateStream`, `TagStream`, `UpdateStream` on every `stream/cam-*` (live streams included) | only streams tagged `adapterId=<own Thing>`; create only so tagged; no re-tagging (#7–#19) |
| every reader of A3 (sync paths, rematch, schedules, outage buffer, event watcher, codec caps, admin GUI, agent) | `cameras` `Scan`, `GetItem` — every customer's rows | `Query` on an `adapterId` index, own rows only (#20–#24) |
| registration, rematch, codec caps, schedules, live view, `stream-codec.py`, admin GUI | `cameras` `PutItem`, `UpdateItem` — any row | over MQTT `adapter/<thing>/registry`, an IoT rule, a Lambda that writes field by field to that Thing's rows only |
| ONVIF passwords, credentials in `rtspUrl` / `liveRtspUrl` | in the rows, so in every adapter's `Scan` | SSM `SecureString` `/vsz/adapters/<thing>/cameras/<cameraId>` (#3–#6) |
| thumbnails | `s3:PutObject` on `thumbs/*` | `thumbs/<thing>/*` (#25–#26) |
| outage uploader | `s3:PutObject`, `GetObject` on `clips/*`; `clips` `PutItem` | `clips/<thing>/*`; the clip row: to decide in 10.2 |
| event watcher | `iot:Publish` on `adapter/adapter-01/event` | `adapter/${credentials-iot:ThingName}/event` (#27–#28) |
| agent, Add cameras (D16) | `camera-requests` `GetItem` — any request, if its random ID is known | to decide in 10.2 (a key that starts with the Thing, with `LeadingKeys`) |

*10.1 built 2026-11-14, results pending* — `cloud/spike-d13.sh` (setup, run, verify, teardown, each
with `--dry-run`) and `cloud/spike_d13.py`, with the policies `cloud/iam/vsz-spike-d13-policy.json`
(D13's shape on test resources) and `cloud/iot/vsz-spike-d13-thing-policy.json`. Everything it makes is
throwaway and named `vsz-spike*`: a Thing `vsz-spike-a` with its own certificate and IoT policy, a role
and role alias, four KVS streams (two tagged for `vsz-spike-a` and an imaginary `vsz-spike-b`, two named
after them), two SSM parameters, a DynamoDB table with an `adapterId` index, an S3 bucket. Nothing
shared changes — not `KVSAdapterRole`, not the Thing policy, not `adapter-01`, not the `cameras` table.
`run` uses the spike certificate alone (what an adapter with that identity could do) and makes 30
checks, its own and the other's resource each time: credentials refused when claiming another Thing's
name; SSM read and write by path; KVS by tag (describe, the PutMedia endpoint, create only tagged as
one's own, no re-tagging another's stream) and, as the fallback the KVS docs show, by stream name;
PutMedia through kvssink with a test picture; `LeadingKeys` on the index (own rows, another's, a scan,
a read by the table's key); S3 by key prefix; IoT publish by topic; and credentials without the Thing
name header, under which the variable is unset. `verify` counts the fragments each test stream received,
with the owner's login. Results (`results.json`, kept by teardown) are to be recorded here, and D13
adjusted to them.

*10.1 results, 2026-11-15 — all three facts hold* (setup by the user; `run` and `verify` after the first
run's tooling faults were fixed, FoundAndFixed.md #95): **30 of 30 checks as expected**, and KVS's own
count — `vsz-spike-tag-a` 30 fragments, `vsz-spike-a.name` 30, `vsz-spike-tag-b` 0, `vsz-spike-b.name` 0:
- **`${credentials-iot:ThingName}`** works in a role policy, in resource ARNs (SSM paths, S3 keys, IoT
  topics, stream names) and in condition values (`aws:ResourceTag`, `dynamodb:LeadingKeys`). Credentials
  claiming another Thing's name are refused (HTTP 403); without the header they are issued but the
  variable is unset, so everything scoped by it is refused — it fails closed.
- **KVS by tag:** `DescribeStream`, `GetDataEndpoint` and **PutMedia through kvssink** follow
  `aws:ResourceTag/adapterId`; `CreateStream` passes only with `aws:RequestTag/adapterId` = one's own Thing
  and no other tag key, and creating with the tag needed no separate `TagStream` grant; re-tagging another
  adapter's stream is refused when `TagStream` requires both the request and the resource tag to be one's
  own. Streams named after the Thing work too, but aren't needed: camera IDs stay the stream names.
- **`dynamodb:LeadingKeys` on a global secondary index:** a `Query` on `adapterId-index` returns one's own
  rows and is refused for another adapter's; a `Scan` and a `GetItem` by the table's key are refused.
- A role alias just made answers 404 for a few minutes (gone within 4) before its credentials are handed
  out (#95).

*D13 adjusted* (§4b), for 10.2: streams keep camera-ID names and are scoped by their `adapterId` tag —
every `CreateStream` on a Pi (registration, live view, the agent) passes `Tags={"adapterId": <thing>}`
and nothing else, and this repository's existing streams are tagged before the role narrows (while the
role still allows `TagStream`, or by the owner); the Pi reads its rows only by `Query` on an `adapterId`
index — **a sparse index**, so every row a Pi acts on must carry `adapterId` first, and
`adopt_unstamped`, which scans, has to have finished (or move to the cloud side) before the role narrows;
reads by key (`GetItem`) become index queries; secrets, S3 keys and the event topic take the Thing-name
variable as tried. If 10.2 makes a new role alias rather than narrowing `KVSAdapterRole` in place, it
waits for the alias to reach the credentials endpoint.

*D13 amended 2026-11-15 (the user, at 10.2): the Pi's registry writes go over a signed HTTPS route, not
MQTT.* Several writes need an answer — claiming a camera ID, rematch's conditional move, registration —
and MQTT gives the writing process none; only the agent has an MQTT session, so every other process
would have had to write through it. Instead the Pi calls `/registry/<thing>/…` on API Gateway, signed
with its own device credentials (still outbound only); IAM lets each Pi call only its own path
(`execute-api:Invoke` on `…/registry/${credentials-iot:ThingName}/*`, the variable the spike proved), and
a Lambda applies the write to that Thing's rows and answers at once, conditions and claimed IDs included.

**10.2, in steps** (each complete, tested and deployable on its own; all but the last keep VMS's card
working on the shared table and role):
- **10.2a Stream tags** — every stream a Pi creates carries `adapterId=<its Thing>`, and each Pi tags
  its older streams. Nothing reads the tag until the role narrows.
- **10.2b The registry route** — API Gateway `/registry/{proxy+}` with IAM authorization, a Lambda that
  writes field by field to the calling Thing's rows (claims, conditional updates, the clip rows), the
  device role allowed its own path; the Pi's writers move to it.
- **10.2c The adapter index** — a sparse `adapterId` index on `cameras`, the role allowed `Query` on it
  with `LeadingKeys`; the Pi's readers read their rows by query, and its reads by key become queries.
  What needs every adapter's rows — the next free camera ID, another adapter's device in a scan — moves
  behind the registry route.
- **10.2d Camera secrets in SSM** — `/vsz/adapters/<thing>/cameras/<cameraId>`, written at registration
  and by rematch's moves beside the row, read first.
- **10.2e S3 keys under the Thing** — thumbnails and outage clips under `<thing>/`, readers following.
- **10.2f Narrowing** (once VMS runs on its own copies) — the role reduced to the above and exported
  back (#41), the event topic by the variable, the secrets out of the rows, VMS's leftovers.

*10.2a built 2026-11-15, and live:* `camera_control.stream_tags()` — `{"adapterId": <this Thing>}`, the
only tag — passed to every `CreateStream` on a Pi (registration's recorded stream, live view's and the
agent's live stream); `rematch_cameras.tag_own_streams` tags the recorded and live streams of every
camera the Pi serves (`is_mine`: unregistered ones too, since Re-register brings them back; not another
adapter's, not a released one, not a row that isn't a camera), remembers each in
`vms-adapter/tagged-streams.json` and asks again after a day — VMS's card shares the stream names until
it moves and could re-make one; a stream not made yet is skipped (it will be made tagged), a refusal is
asked again next run. rematch runs from the clone every 5 minutes, so it went live at once: at 09:42 it
tagged all eight of `adapter-01`'s streams (`cam-01` … `cam-04` and their `-live`), checked with
`list-tags-for-stream`. No IAM change: the role already allows `CreateStream` and `TagStream` on
`cam-*`. Tests: `tests/adapter/test_rematch_cameras.py` (+5) and every `create_stream` stub now expecting
the tag; broken on purpose once each, red: each of the three creations untagged, another adapter's
streams tagged, no day between asks, a refusal remembered, a dry run tagging. Writing the test showed a
stream never tagged counted as "tagged at time 0" — harmless with a real clock, fixed before it ran. To
apply the creation tags: `systemctl --user restart kvs-agent onvif-admin` (long-running processes).

*10.2b, first half, built 2026-11-15 — the route, not yet provisioned:* **Lambda `vsz-registry`**
(`cloud/lambda/vsz_registry.py`) on `/registry/{proxy+}`, IAM-authorized: `GET <thing>/ping`;
`POST <thing>/cameras/claim` (the next free ID past every adapter's, or the one given, D11 — the global
view a Pi won't have after 10.2c); `POST <thing>/cameras/<id>` (SET and REMOVE under `expect` / `exists` /
`missing` conditions, always also "this Thing's row" — so completing a registration and abandoning a
claim are updates that expect its token); `POST <thing>/cameras/<id>/adopt` (the adopting adapter only,
never a released row or one with no mode, #93); `POST <thing>/clips` (the outage backfill's row, for one's
own camera, never over another); `POST <thing>/devices` (which identities are another adapter's cameras,
for the scan's mark). It answers only calls signed by `KVSAdapterRole` (in case the route were left open),
writes only the path's Thing's rows — another adapter's is "no camera", a missing one is never made —
and never a row's key, its owner (`adapterId`, `releasedFrom`) or a claim token; for the adopting adapter
(`ADOPTING_THING=adapter-01`) a row without an owner counts too, as a second conditional write rather
than one condition with an OR. It prints nothing (rows still hold passwords until 10.2d). **The device
role** gets `execute-api:Invoke` on `svfvg7xh9g/prod/*/registry/${credentials-iot:ThingName}/*` alone
(`cloud/iam/kvs-adapter-registry-route-policy.json`, inline `registry-route`). **The Pi's client**
(`adapter/registry_client.py`): each call signed for execute-api with the device credentials, to
`REGISTRY_URL` (a new `adapter.env` key, read only when a call is made — a Pi without it runs on); and
`registry_client.py check` — the route answers this Pi, IAM refuses another Thing's path, and one
harmless write (`registryCheckedAt` on one of this Pi's cameras). **Provisioning**: `provision.sh
registry` (the role, the function with `ADOPTING_THING`, the route — `ensure_method` learnt an IAM-signed
variant — the device role's policy, the stage; checked: an unsigned call gets IAM's 403 and test-invoke
reaches the function, which refuses a call with no identity); its dry run against the account was
clean. Tests: `tests/cloud/test_vsz_registry.py` (39), `tests/adapter/test_registry_client.py` (7); broken
on purpose once each, red (17): any caller answered, another adapter's row written, a missing row made,
a released row written, any adapter writing unstamped rows, ownership settable, conditions ignored, the
next ID past one adapter's rows only, a given taken ID replaced, a row with no mode adopted, any adapter
adopting, a clip for another's camera, a clip over another, own devices marked another's; on the Pi,
signed for another service, always this Pi's path, the check content with any refusal. No writer on the
Pi uses the route yet — that is 10.2b's second half.
*Provisioned and verified 2026-11-15* (the user): `provision.sh registry` made the role, the function, the
route and the device role's `registry-route` policy, deployed the stage and passed its own check; with
`REGISTRY_URL` in `adapter.env`, `registry_client.py check` on `adapter-01` gave three PASS lines — the
route answered `adapter-01` (adopting); **IAM refused `adapter-99`'s path** ("no identity-based policy
allows the execute-api:Invoke action" — so `${credentials-iot:ThingName}` holds for execute-api too, the
one service the spike hadn't tried); and the harmless write landed (`cam-01`'s `registryCheckedAt`, read
back from the table).

*10.2b, second half, built 2026-11-15 — every registry write on the Pi goes through the route.* The
client's typed calls — `update` (set / remove under `expect` / `exists` / `missing`), `claim`, `adopt`,
`add_clip`, `elsewhere` — each raise `ConditionFailed` (409), `NotMine` (404) or `RegistryError` (no status
when the route can't be reached), so each caller handles exactly the refusals it expected; Decimals from
DynamoDB reads go as numbers and come back exact. Moved: the schedule runner's mode (only while the camera
is in use); live view's `liveRtspUrl`; the codec capabilities; `stream-codec.py`'s record (a row of another
adapter, or none, is "no registry row" — the route never makes one, #93); rematch's conditional move
(`expect` the address read) and adoption (the route's `adopt`); the outage uploader's clip row (one there
already: done, never overwritten); registration — the claim (the route allocates past every adapter's
IDs), the abandon and the completion (both expect the claim token), re-registration (an unstamped row
adopted first, the owner never written as a field), the ceiling, detection, unregister and the USB
camera's re-register; and the admin GUI's nine (name, mode, detection tests, audio, outage buffer, codec,
the stream switch and its rollback). The helpers that took a `table` only to write lost it
(`live_view.apply`, `codec_caps.refresh_item`, `record_ceiling`, `registration_ceiling`,
`record_detection`, `apply_live_view`, `claim_camera_id`, `abandon_claim`). Not yet: the reads — the
registration's next-ID scan and the agent's "another adapter's" mark (`elsewhere` is ready) — which are
10.2c's. **Tests** reach the real `vsz-registry` through the `registry` fixture: `FakeRegistryRoute`
(`tests/fakes.py`) hands each signed request to the function on the test's own table, refusing another
Thing's path as IAM does, so a module's tests check the whole path; changed: `test_registry_client` (+5),
`test_schedule_runner` (+1, and offline is now the route unreachable), `test_live_view`, `test_codec_caps`,
`test_rematch_cameras` (the conditions sent, not raw expression strings), `test_outage` (+2: the uploader's
clip row had no test before), `test_admin_adapters` (+2), `test_admin_codec_stream`. Broken on purpose
once each, red (14): unregister skipping the registry, the completion or the abandon without the claim
token, re-registration without adopting, an allocated ID taken with no next, an existing clip taken as a
failure, `stream-codec.py` re-raising "no row", schedules writing a camera unregistered meanwhile,
rematch not expecting the address, the codec caps / live view / the admin GUI's name, audio and stream
rollback not written. Two first stayed green — the completion's token and the schedule's unregistered
check had no test — and got one each. Built across two sessions after an SSH drop (the Pi lost name
resolution at 10:24); the second took over at a clean point with the first's account of every file.
rematch and the schedule runner run from the clone, so their writes went through the route at once; the
agent and the admin GUI take it at their restart.

*10.2c, step 1, built 2026-11-15 — the index and the reads, wired to nothing yet.* Done in two steps
because rematch, the schedules and the path sync run from the clone: a reader moved before the index
exists would fail at its next timer run. **The index**: `provision.sh adapter-index` adds
`adapterId-index` to `cameras` (key `adapterId`, every attribute, on demand as the table) — VMS shares the
table, and the index changes none of its rows, key or reads, only adds a way to read the rows that have an
owner — and gives `KVSAdapterRole` `dynamodb:Query` on that index alone, for `LeadingKeys` =
`${credentials-iot:ThingName}` (`cloud/iam/kvs-adapter-own-rows-policy.json`, inline `own-rows`; the spike
tried exactly this on a GSI). It waits for the index to be ACTIVE and checks, with the operator's login,
that it lists each adapter's rows as the table does. **The reads** (`adapter/registry_reads.py`):
`own_rows(fields)` — the index query for this Thing, plus, on the adopting Pi, `unstamped_rows()`: the
index is sparse, so a row without an owner (adapter-01's until rematch stamps it, D12) is found by a scan,
and a refused scan reads as none (after 10.2f nothing is left to adopt); `own_row(id)` — another adapter's
camera is unknown, as one that doesn't exist; and `registry_reads.py check` on a Pi — the index lists this
Pi's rows, **IAM refuses another Thing's** (only `AccessDeniedException` counts), and `own_rows()` is what
`camera_control.is_mine` keeps of the whole table (skipped once the role can't scan). `FakeTable` learnt
the index (sparse, paged). Tests: `tests/adapter/test_registry_reads.py` (20) — the core one, `own_rows`
equal to `is_mine` over the table, adopting or not, one page or many. Broken on purpose once each, red (8):
a Pi that doesn't adopt scanning, released rows adopted, every scan error read as none, the second page
dropped, the projection without `cameraId`, a skip counted as a failure, any refusal taken for IAM's, the
check comparing with `acts_on`. The refusal check first stayed green — a non-IAM error passed — and got a
test (the check before the index exists). Its dry run against the account was clean. Built across two
sessions again after an SSH drop; the second took over at a clean point with the first's account.
*Provisioned and verified 2026-11-15* (the user): `provision.sh adapter-index` made the index (ACTIVE) and
`own-rows`; `registry_reads.py check` on `adapter-01` gave three PASS lines — the index lists `cam-01` …
`cam-04`, **IAM refused `adapter-99`'s rows** (`AccessDeniedException`), and the index agrees with the
table.

*10.2c, step 2, built 2026-11-15 — every read on the Pi is of its own rows.* `registry_reads.own_rows` /
`own_row` replace every scan and read by key of `cameras` on the Pi: the path sync, the schedule runner,
the outage supervisor, the event watcher, `codec_caps.refresh`, rematch (tagging by `own_rows`, adoption by
`unstamped_rows`), `stream-codec.py`, `camera-audio.py`, `observe_events.py`, the route check's camera, the
agent and the admin GUI (every route), and registration. `own_row` reads one row from the index, and on the
adopting Pi falls back to a read by key — a row without an owner, or one of its own the index hasn't caught
up with yet (a GSI is eventually consistent); re-registration hands `codec_caps` the row as written rather
than reading it back, for the same reason. Moved behind the route: **the next free camera ID** — the Pi no
longer computes one (`next_camera_id` is gone): an allocated registration claims first and the route picks
past every adapter's IDs, then checks the MediaMTX path, giving the claim up (abandoned) on a clash; a typed
ID keeps its order (path, then claim). And **which discovered devices are another adapter's cameras**: the
agent asks `POST <thing>/devices` once per scan, only for the devices not found among its own rows, and
learns yes or no. **Another adapter's camera is now unknown here**, as one that doesn't exist: 404 "unknown
camera" from the admin GUI (was 409 naming the owner — `foreign_error` is gone), ignored by the agent, and
a typed ID that is another adapter's is refused at the claim as "taken" without saying whose. Stop stays
possible on a camera that isn't this Pi's any more: the admin GUI and the agent stop a producer this Pi has
(and the agent a live view whose deadline file is here). The agent now refuses a `camera` that isn't a
camera ID before anything else — it names units, paths and the live view's deadline file, and the registry
row no longer guards it. `stop_released_producers` lists this Pi's producers from its own units (`cam-01`,
`cam-02`, the channel files) and stops each whose camera isn't among its rows — one whose row is gone too.
**Found writing the tests**: the readers moved first read rows without `adapterId`, so every row looked
unstamped — kept on `adapter-01` by adoption, dropped on any other adapter (FoundAndFixed.md #96); fixed:
`own_rows` always reads the fields `acts_on` decides by. Tests (pytest 1547 → 1591): `test_registry_reads`
(`own_row`'s key read, #96), `test_foreign_rows` (every reader on a Pi that doesn't adopt, scan forbidden),
`test_agent` (a `camera` that isn't an ID, Stop and Live-stop of what isn't this Pi's, the scan's question to
the route, released producers from this Pi's units), `test_admin_adapters` (another adapter's camera unknown
on every route; claim first; a path clash gives the claim up), `test_admin_registration`,
`test_rematch_cameras`, `test_stream_codec`, `test_camera_audio`, `test_vsz_registry` (the next free ID's
pages, moved from the Pi). Broken on purpose once each, 35 of 36 red: among them the agent taking any `camera` string, stopping what doesn't run
here, reading by key, stopping its own producers, asking the route about its own devices, not marking
another's; registration not giving up a clashing claim, proposing an ID, reading another adapter's row;
the admin GUI listing or reading by scan or key, stopping what has no producer; each reader back on a scan.
One survived as equivalent (adoption reading `own_rows`, which on the adopting Pi includes the unstamped
rows); one first survived — `stream-codec.py`'s record reading by key, which the route refuses anyway — and
got a test. Checked live, reads only: the index readers, the admin list and status, discovery (three
cameras, all this Pi's), `elsewhere` on the live route, `camera-audio.py`, the route check's camera.
The timers (path sync, rematch, schedules) and the producers' scripts run from the clone and took it at
once; the agent, the admin GUI, the event watcher and the outage supervisor take it at a restart.

*10.2d built 2026-11-15 — camera secrets in SSM, beside the rows; not yet provisioned.* **Where**: one
SecureString per field — `onvifPassword`, `rtspUrl`, `liveRtspUrl` (the URIs carry the camera's login) —
at `/vsz/adapters/<thing>/cameras/<cameraId>/<field>`, so a write of one field never races another and
removing one is deleting its parameter; values are JSON, since SSM refuses an empty value and `cam-04`'s
password is empty; the default key (`aws/ssm`), which needs no KMS grant (as in the spike). **The device
role** gets `ssm:GetParameters`, `PutParameter`, `DeleteParameter` on `parameter/vsz/adapters/${credentials-iot:ThingName}/*`
alone (`cloud/iam/kvs-adapter-camera-secrets-policy.json`, inline `camera-secrets`; `provision.sh
camera-secrets`, its dry run clean). **The row stays what readers use until 10.2f** — a change from
"read first" above: VMS's card shares the table until it moves and may change a row (a password, a
move), and an SSM copy read first would then shadow the newer row; reading SSM first would buy nothing
meanwhile, since the rows still hold the secrets. So (`adapter/camera_secrets.py`): every registry write
on the Pi already goes through `registry_client.update()`, which now writes a secret field to SSM after
the row (`keep_beside`; a refusal or an outage never fails the write, and is logged by field name); rematch
mirrors every row that holds secrets into SSM each run (`mirror`: the older cameras' backfill, and a
change VMS's card made — what differs is written, what the row no longer has deleted; a camera is asked
about again only when its row's secrets change or a day has passed, kept as a salted digest in a 0600
record, never the values); and every reader goes through `registry_reads`, which fills a row from SSM
only when the row holds none (`fill`) — no SSM call now, every passthrough row after 10.2f, when the
secrets are taken out of the rows and nothing else in the code changes. `own_rows(secrets=False)` reads
the table as it is, for the mirror and the check. `camera_secrets.py check` on a Pi: IAM refuses another
Thing's secrets, and each camera's SSM copy is its row's (or, for a row without secrets, SSM has them);
`camera_secrets.py mirror` copies now. No value is ever printed. **Tests** (pytest 1591 → 1612): `tests/adapter/test_camera_secrets.py`
(21) on a stateful fake SSM (`FakeSSM`, refusing another Thing's path as IAM does), and the admin GUI's
registration storing the new camera's secrets; three `codec_caps` tests moved onto the `registry` fixture
(the guard caught them reaching for real credentials once a row could be filled). Broken on purpose once
each, red (24): among them the write hook gone, a refusal failing the write, non-secret fields stored,
values not JSON, a missing parameter's delete raising, a row that holds its secrets filled from SSM (a
stale copy winning) or one without them not filled, every read asking SSM, the projection missing what
`fill` decides by, the mirror re-asking every run or never, deleting nothing, writing in a dry run, its
record readable by others or holding the values, a row without secrets mirrored, the check passing
anything; one first survived — the mirror and the check read filled rows, comparing SSM with itself —
and led to `secrets=False`. Live: rematch runs it from the clone and, with no policy yet, logs "mirroring
camera secrets skipped (AccessDeniedException)" and goes on; the readers make no SSM call (every
passthrough row holds its password).
*Provisioned and verified 2026-11-15* (the user): `provision.sh camera-secrets` (the policy, as the file),
`camera_secrets.py mirror` — nine SecureStrings, `cam-02` … `cam-04` × three fields, at 14:47 — and the
restart (14:48); `camera_secrets.py check` gave four PASS lines: **IAM refused `adapter-99`'s secrets**
(`AccessDeniedException`), and each camera's SSM copy is its row's.

*10.2e built 2026-11-15 — the Pi's S3 keys under its Thing.* Two writers on the Pi put objects into the
evidence bucket. **Thumbnails** go to `thumbs/<thing>/<cameraId>.jpg` (were `thumbs/<cameraId>.jpg`);
their two readers, `list-cameras` (the page's link) and `vsz-directory-api` (the portal's time), have the
row and look under its `adapterId` first, then at the older key — a row without an owner, or a camera
whose adapter hasn't uploaded under its Thing yet (`thumbnail_keys`, identical in both); the older key
and its objects go at 10.2f. **Outage clips** go to `clips/<cameraId>/<thing>/<YYYY/MM/DD>/…-outage.mp4`:
the camera stays second, where every clip reader takes it from (`play-clip`, `set-clip-tier`,
`delete-clip`: `clips/<cameraId>/…`), so none of them changes, and the bucket's lifecycle rule (prefix
`clips/`) still covers them; `vsz-registry` now takes a clip row only with its own camera's key under its
own Thing — or, until 10.2f, the older `clips/<cameraId>/<YYYY>/…` of an uploader not yet restarted.
Clips the Lambdas write (`clip-to-s3`, `record-clip`) are the Lambdas' own and stay as they are (VMS uses
`clip-to-s3`). **No IAM change**: the device role's `thumbs/*` and `clips/*` already allow the new keys;
10.2f narrows them to `thumbs/${credentials-iot:ThingName}/*` and `clips/*/${credentials-iot:ThingName}/*`.
Tests (pytest 1612 → 1621): the uploads' keys (`test_thumbnails`, `test_outage`, `test_registry_client`),
the route's key check (7 cases), each reader asking under the adapter first and the older key after
(`test_list_cameras`, `test_directory_api` — which had passed unchanged only because a mismatched request
fell into the readers' catch-all, now pinned key by key). Broken on purpose once each, red (9): the older
key first or none, in both readers; the route taking another camera's key, any Thing's, or no older key;
each upload back on its old key. **Found on the way**: `kvs-outage-uploader` is a long-running unit too,
and the restart lists of 10.2b and 10.2c missed it — it has run the code of 2026-11-14 since (writing its
clip rows directly, which the role still allows; at 10.2f it would have lost them). The long-running units
that run Python from the clone are five: `kvs-agent`, `onvif-admin`, `kvs-event-watcher`,
`kvs-outage-buffer`, `kvs-outage-uploader` (LAUNCH.md says so now). Live: the thumbnails timer runs from the
clone, so new thumbnails go under the Thing at its next run; until `list-cameras` and `vsz-directory-api`
are deployed the page shows each camera's older thumbnail, frozen. `vsz-registry`'s deploy needs `--force`:
its live code is this tree's of 10.2b, never committed (checked: the live copy differs from the tree only by
the clip-key check).

*10.2f, 2026-11-15 — prepared, not applied: VMS still shares the role.* The account shows it (read
only): one role alias (`KVSAdapterRoleAlias`), one adapter Thing (`adapter-01`), one camera table, only
the `cam-*` streams — and `KVSAdapterRole` even carries VMS's own inline policy `PirLocal`. Narrowing now
would break VMS's card four ways: its admin GUI writes and scans `cameras` directly, its registration
makes streams untagged, its outage uploader writes clips and clip rows directly, and `PirLocal` would go.
So (the user's choice, 2026-11-15): **prepare now, apply once VMS runs on its own copies.**
*Applied now, since nothing of it reaches VMS* (step A): **the web page's camera requests go through the
route** — the open point of the inventory above, decided as `GET <thing>/requests/<requestId>` on
`vsz-registry` (`camera_request`: the request only if its `thing` is the caller's, its seven fields and
nothing else; another adapter's or a missing one is "no such request for this adapter"), rather than a
`LeadingKeys` condition on request IDs that would have to start with the Thing — the route was there,
and the device role then needs no read of `camera-requests` at all; `vsz-registry-role` gets `GetItem` on
it (`cloud/iam/vsz-registry-requests-policy.json`, added by `provision.sh registry`), the agent reads
through `registry_client.camera_request`. And **10.2e's older keys go**: a thumbnail only under its row's
adapter (none for a row without one), a clip row's key only `clips/<cameraId>/<thing>/…`.
*Prepared, applied later* (step B): the target policies, each scoped by the caller's Thing —
`cloud/iam/kvs-adapter-streams-policy.json` (Describe, GetDataEndpoint, PutMedia and UpdateStream by
`aws:ResourceTag/adapterId`; CreateStream only with `aws:RequestTag/adapterId` = its Thing and no other
tag key; TagStream needing both tags and only that key — the spike's shapes; **UpdateStream by tag is
untried**: if it fails, only `stream-codec.py`'s best-effort MediaType record does),
`kvs-adapter-uploads-policy.json` (`thumbs/${Thing}/*`; `clips/*/${Thing}/*`, PutObject and GetObject),
`kvs-adapter-events-policy.json` (`topic/adapter/${Thing}/event`), beside the kept `own-rows`,
`registry-route`, `camera-secrets`; to go: `KVSProducer`, `OutageBackfill`, `Thumbnails`, `camera-requests`,
`PirLocal`. **`provision.sh narrow`** checks first (each a PASS/FAIL line, its dry run any time): `--vms-moved`
given (the operator's word for D14 — nothing in the account can tell); the kept policies as their files;
`vsz-registry` this tree's and reading camera requests; every `cam-*` stream tagged; every camera row
stamped; every row's secrets in SSM. Only with all met: it saves the live policies and `vsz-registry`'s
`ROW_SECRETS` (0600, `~/.local/state/videosafezone/kvs-adapter-role-<time>.json`), puts the three, deletes
the five, sets `ROW_SECRETS=drop`, and checks the role is exactly the files (#41); `provision.sh
narrow-rollback <file>` puts back what was saved. **The rows without secrets**: `vsz-registry`'s
`ROW_SECRETS` (`keep` until then; `drop`; anything else refuses every write) — in `drop`, a secret field
never reaches a row, every write takes away any a row still holds, and a condition on one is refused;
`camera_secrets.py strip` on each Pi then makes one harmless write per row whose SSM copy it has checked
(a row whose copy differs keeps its secrets: mirror first), leaving SSM untouched, and the readers take
them from SSM (10.2d's `fill`). **`adapter/device_access.py check`** on each Pi, afterwards: probes that
change nothing either way — its rows by the index (allowed), a Scan, a row by key, a clip row written
directly (under a condition that can never hold), a camera request read directly (each refused), each
camera's recorded stream (allowed by its tag), a thumbnail under its Thing — then the index's and the
secrets' own checks. Its dry run against the account now: **two conditions not met** — VMS (expected) and
`vsz-registry` not yet this tree's (until step A is deployed) — the other four met (kept policies as the
files, every stream tagged, every row stamped, every row's secrets in SSM); it takes about a minute and a
half (each AWS CLI call is ~3 s on the Pi). Tests (pytest 1621 → 1643): the request route, the client and
the agent through it, the readers with no older key, `ROW_SECRETS` (kept, dropped, conditions, a bad
value), `strip` (whole copy, differing copy, before the switch), `device_access` (narrowed, wide, what a
probe proves), the policy files (every statement scoped by the Thing; the lists in `provision.sh`; the
refusal and the save before any change). Broken on purpose once each, red (17), two after a fix: the
portal's older thumbnail key passed — an extra request fell into its catch-all — until the test asked
`thumbnail_keys` directly; and `drop` still setting a secret it also removed passed because `FakeTable`
applied both, where DynamoDB refuses an update naming one field twice — the fake now refuses it too.
Built across two sessions after two more SSH drops; the second took over with the first's account.


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
  well under Cognito's 5 seconds — **measured 2026-11-07: 4.26 s at 256 MB, 1.09 s at 1024 MB**
  (FoundAndFixed.md #89). Provisioned concurrency isn't available in this account (the concurrency
  note below); should it ever creep up, signing the IAM token without boto3 removes the biggest CPU cost.
- What the instance does to sign-in during its maintenance window and minor-version upgrades.
- `max_connections` on the micro instance against the other project's use — measured 79 (Phase 1);
  our roles are capped at 13.
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
- [x] Phase 0.4: the master secret works; the instance's databases recorded (2026-11-07)
- [x] Phase 1: provisioned (`provision.sh directory-db`, the user) and its *Verify* recorded (2026-11-07)
- [x] Phase 2: `vsz-users`, `vsz-directory` + trigger, seed, the sign-in page, Account details — built and tested (2026-11-07)
- [x] Phase 2: provisioned (`provision.sh identity`, `directory-seed`, the user) and its *Verify* recorded (2026-11-07)
- [x] Phase 2: the trigger at 1024 MB, a cold start measured: 1.09 s (FoundAndFixed.md #89, 2026-11-07)
- [x] Phase 3: `adapterId`, tenant checks report → enforce, pool switch — enforcing and verified (2026-11-07)
- [x] Phase 3: `deploy.sh tenancy old-pool-off` (2026-11-07)
- [ ] Later: delete the old app client `kvs-demo-web-client` from `kvs-demo-users` (unused since the switch)
- [x] Phase 4: directory API, System console — built and tested (2026-11-07)
- [x] Phase 4: deployed (`deploy.sh lambda vsz-directory`, `provision.sh directory`, `deploy.sh page`) and verified by the user (2026-11-07)
- [x] Phase 5: Dealer portal — built and tested (2026-11-07)
- [x] Phase 5: deployed (`deploy.sh lambda vsz-directory`, `lambda vsz-directory-api`, `provision.sh portal`, `deploy.sh page`) and verified by the user (2026-11-07)
- [x] D15 amended: AWS identities by the owner's script, `cloud/adapter-identity.sh` (the user, 2026-11-07)
- [x] Phase 6: Adapters — Cloud IDs, activation, release, last seen — built and tested (2026-11-07)
- [x] Phase 6: deployed (db-migrate 002, both directory functions, `provision.sh adapters`, the Pi's agent and admin GUI restarted, `deploy.sh page`), checked read-only (2026-11-07)
- [ ] Phase 6: its *Verify* — the activation flow (a Cloud ID used, refused, released)
- [x] Phase 7: Adding cameras from the web page (allocated IDs, per-Pi filtering) — built and tested (2026-11-07)
- [x] Phase 7: deployed (`deploy.sh lambda list-cameras`, `lambda publish-cmd`, `provision.sh cameras`, the Pi's agent and admin GUI restarted, `deploy.sh page`, 2026-11-08); the user reports Phase 7 complete (2026-11-14)
- [x] Phase 7 addition (unregister any camera, Re-register): `deploy.sh lambda list-cameras`, `lambda adapter-cameras`, the Pi's agent and admin GUI restarted, `deploy.sh page` (2026-11-08)
- [x] Phase 8: Billing seam with fakes — built and tested (2026-11-14)
- [x] Phase 8: deployed (`deploy.sh lambda vsz-db-admin`, `db-migrate` (003), `lambda vsz-directory`, `lambda vsz-directory-api`, `deploy.sh page`); the user reports billing working (2026-11-14)
- [x] Phase 9: Account page and additional users — built and tested (2026-11-14)
- [ ] Phase 9: deployed (`deploy.sh lambda vsz-directory`, `lambda vsz-directory-api`, `provision.sh accounts`, `deploy.sh page`) and its *Verify* recorded
- [x] D14 decided: VMS gets its own copies, built in the VMS repository (the user, 2026-11-14)
- [x] Phase 10.1: the spike built — `cloud/spike-d13.sh`, `cloud/spike_d13.py` (2026-11-14)
- [x] Phase 10.1: the spike run (setup by the user; run, verify) — 30 of 30, all three facts hold; recorded, D13 adjusted; FoundAndFixed.md #95 in its tooling (2026-11-15)
- [x] Phase 10.1: the spike's teardown (the user, 2026-11-15) — every `vsz-spike*` resource gone (checked); the results kept in `~/.local/state/videosafezone/spike-d13-results.json`
- [x] VMS on its own copies — Thing, role alias and role, camera table, streams, clips (the VMS repository): VMS's `OWN-COPIES-PLAN.md` (branch `own-copies`) Phases 2–3 run and verified on the VMS card, 20:05–20:15 (the user, 2026-11-15) — `pir-adapter-01` and topic root `pir/` (a VMS event under `adapter/` would feed `ClipToS3Rule` with these streams), `PirAdapterRoleAlias` → `pir-adapter-role` (with VMS's `PirLocal` share), `pir-cameras` (a copy) and `pir-clips` (empty), streams `pir-cam-*`, bucket `pir-evidence-596633517506`, `pir-clip-to-s3` on `pir/+/event`. Checked from here at 20:48: all of them there; nothing on this side since the cutover (no fragment in `cam-*` or their `-live`, `cameras` rows' `updatedAt` and `clips`' 63 rows as before); `narrow --dry-run --vms-moved`: all six conditions met
- [x] Phase 10.2a: stream tags — built and live; all eight of `adapter-01`'s streams tagged (2026-11-15)
- [x] Phase 10.2b, first half: the registry route — built, provisioned and verified on `adapter-01` (2026-11-15)
- [x] Phase 10.2b, second half: every registry write on the Pi through the route — built and tested (2026-11-15)
- [x] Phase 10.2b: `systemctl --user restart kvs-agent onvif-admin` (the user, 10:42) — both up, the agent reconnected (its retained config read); every process on `adapter-01` now writes through the route
- [x] Phase 10.2c, step 1: the index's provisioning and `registry_reads.py` — built and tested, wired to nothing yet (2026-11-15)
- [x] Phase 10.2c: `provision.sh adapter-index` (the user), then `registry_reads.py check` on `adapter-01` — three PASS: the index lists cam-01 … cam-04, IAM refuses adapter-99's rows, the index agrees with the table; index ACTIVE and `own-rows` as the file (2026-11-15)
- [x] Phase 10.2c, step 2: every reader on the Pi by query; the next free ID and another adapter's devices through the route; another adapter's camera unknown (404) instead of 409 — built and tested; FoundAndFixed.md #96 (2026-11-15)
- [x] Phase 10.2c: `systemctl --user restart kvs-agent onvif-admin kvs-event-watcher kvs-outage-buffer` (the user, 14:27) — all four up, no restarts; the codec check read all four cameras by the index, the event watcher found none in a detection mode (right: all manual), no timer error since
- [x] Phase 10.2d: camera secrets in SSM beside the rows — built and tested (2026-11-15)
- [x] Phase 10.2d: `provision.sh camera-secrets` (the user), then `camera_secrets.py mirror` and `check` on `adapter-01` — four PASS; restart 14:48 (2026-11-15)
- [x] Phase 10.2e: the Pi's S3 keys under its Thing (thumbnails, outage clips), readers following — built and tested (2026-11-15)
- [x] Phase 10.2e: `deploy.sh lambda list-cameras`, `lambda vsz-directory-api`, `lambda vsz-registry --force` and `systemctl --user restart kvs-outage-uploader` (the user, 15:16) — all three live as this tree; checked: `list-cameras` (invoked as `adapter-01`'s customer) links all four thumbnails under `thumbs/adapter-01/` and each link loads (206, image/jpeg); the live route refuses a clip key under another Thing and one of another camera (400, nothing written); the uploader up on the new code. Not tried live: the portal's thumbnail time (`vsz-directory-api`, the same `thumbnail_keys`), and an outage backfill (needs an outage) (2026-11-15)
- [x] Phase 10.2f, prepared: the narrowed policies, `provision.sh narrow` / `narrow-rollback`, `ROW_SECRETS`, `camera_secrets.py strip`, `device_access.py check`; built and tested; dry run: VMS not moved, `vsz-registry` not yet this tree's (2026-11-15)
- [x] Phase 10.2f, step A (the user, 16:07): `provision.sh registry`, `deploy.sh lambda vsz-registry`, `lambda list-cameras`, `lambda vsz-directory-api`, `systemctl --user restart kvs-agent`; the older thumbnails deleted — checked: `vsz-registry-role`'s `camera-requests` as the file, the three Lambdas this tree's; with the Pi's credentials the request route answers an unknown request 404 (it reads the table), IAM refuses `adapter-99`'s path (403), the older clip key is refused (400); `list-cameras` links all four thumbnails under `thumbs/adapter-01/` and only those exist; `narrow --dry-run`: **5 of 6 met, only VMS's move left** (2026-11-15)
- [x] Phase 10.2f, applied (the user, 21:34–21:38, 2026-11-15): `provision.sh narrow --vms-moved` — the role saved (`kvs-adapter-role-20261115T213425.json`), the three narrowed policies put, the five wide ones (`PirLocal` among them) deleted, `ROW_SECRETS=drop`, the role checked equal to the files; `adapter-identity.sh retire-others` — `5c84e767…` and `20816e9a…` INACTIVE and detached, `1aa189ba…` kept; on the Pi `ADOPT_UNSTAMPED_ROWS=no`, the five units restarted, `device_access.py check` all PASS (the index's comparison SKIP: no scan any more), `camera_secrets.py strip` (cam-02 … cam-04 in SSM only) and `check` all PASS. Checked after: no row holds a secret; the path cache, refreshed after the strip, has every camera's stream addresses (from SSM); MediaMTX's six paths intact; `cam-02`'s producer for 45 s: 7 fragments under the narrowed role, `stream-codec.py`'s record through the route (UpdateStream not reached: the MediaType already matched — still untried). Then: the dropped policies' four files removed from `cloud/iam/`; `provision.sh thumbnails` and `cameras` no longer put `Thumbnails` / `camera-requests` back (a test now refuses any dropped policy on the role); FoundAndFixed.md #97 (a secret SSM refused would have been lost, now SSM first and a failure fails the update); and #98, found reviewing `LAUNCH.md` for a fresh card: `setup-launch.sh`'s producer step still scanned the table and would have reported all done — now by the index, a failed read a FAIL
- [ ] §9 measurements recorded here
