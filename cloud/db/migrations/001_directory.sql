-- 001_directory.sql -- the directory: organisations, people and their roles, sites, adapters and
-- their activations, camera shares, the price list, plans and invoices (DEALER-PANEL-PLAN.md §5).
--
-- Applied by vsz-db-admin as vsz_migrator, in one transaction, recorded in vsz.schema_migrations.
-- A migration that has been applied is never edited: the runner refuses a changed file. Changes
-- come as the next file, 002_….sql.
--
-- Rules kept here, in the database, so that no bug in the code above can break them:
--   - a dealer sits under the platform, a customer under a dealer; a kind never changes;
--   - a role matches the kind of organisation it is held in; staff (platform and dealer roles)
--     hold exactly one membership and no customer one; one holder per customer account (D18);
--   - an adapter has at most one open activation, at a site of the activating customer, and an
--     adapter allocated to a dealer is activated only for that dealer's customers (D15, D19);
--   - a camera is shared only with a customer_user of that account, on one of the account's open
--     adapters, and a release ends the shares (D15, D18).
-- Every table grants vsz_app exactly what the directory's operations need. A table added later
-- grants its own (tests_db checks that none is left without).

CREATE SCHEMA IF NOT EXISTS vsz;

-- A trigger function for "this column names an organisation of that kind".
-- Arguments: the column, the kind.
CREATE FUNCTION vsz.require_org_kind() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  named_org uuid := (to_jsonb(NEW) ->> TG_ARGV[0])::uuid;   -- not "org": that is the table's name
  k         text;
BEGIN
  IF named_org IS NULL THEN
    RETURN NEW;
  END IF;
  SELECT kind INTO k FROM vsz.org WHERE id = named_org;
  IF k IS DISTINCT FROM TG_ARGV[1] THEN
    RAISE EXCEPTION '%.% must be a % (it is: %)', TG_TABLE_NAME, TG_ARGV[0], TG_ARGV[1],
      coalesce(k, 'no organisation') USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

-- Organisations: the platform (one), dealers under it, customers under a dealer --------------------

CREATE TABLE vsz.org (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind       text NOT NULL CHECK (kind IN ('platform', 'dealer', 'customer')),
  parent_id  uuid REFERENCES vsz.org (id),
  name       text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  reference  text CHECK (length(reference) <= 100),   -- a dealer's own reference for a customer
  email      text,
  phone      text,
  address    jsonb,
  status     text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'closed')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK ((kind = 'platform') = (parent_id IS NULL))
);
CREATE UNIQUE INDEX org_one_platform ON vsz.org (kind) WHERE kind = 'platform';
CREATE INDEX org_parent ON vsz.org (parent_id);

CREATE FUNCTION vsz.org_check() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  parent_kind text;
BEGIN
  IF TG_OP = 'UPDATE' AND NEW.kind <> OLD.kind THEN
    RAISE EXCEPTION 'an organisation''s kind never changes' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.parent_id IS NOT NULL THEN
    SELECT kind INTO parent_kind FROM vsz.org WHERE id = NEW.parent_id;
    IF NEW.kind = 'dealer' AND parent_kind IS DISTINCT FROM 'platform'
       OR NEW.kind = 'customer' AND parent_kind IS DISTINCT FROM 'dealer' THEN
      RAISE EXCEPTION 'a % can''t sit under a %', NEW.kind, coalesce(parent_kind, 'missing organisation')
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER org_check BEFORE INSERT OR UPDATE OF kind, parent_id ON vsz.org
  FOR EACH ROW EXECUTE FUNCTION vsz.org_check();

-- People and their roles. Passwords live in Cognito only, never here. ----------------------------

CREATE TABLE vsz.app_user (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  cognito_sub     uuid UNIQUE,                       -- set when the Cognito user is created
  email           text NOT NULL CHECK (email ~ '^[^@\s]+@[^@\s]+$' AND length(email) <= 254),
  name            text CHECK (length(name) <= 200),
  status          text NOT NULL DEFAULT 'invited' CHECK (status IN ('invited', 'active', 'disabled')),
  created_at      timestamptz NOT NULL DEFAULT now(),
  last_sign_in_at timestamptz
);
CREATE UNIQUE INDEX app_user_email ON vsz.app_user (lower(email));

CREATE TABLE vsz.membership (                       -- staff: one; customers: one per account (D18)
  user_id    uuid NOT NULL REFERENCES vsz.app_user (id) ON DELETE CASCADE,
  org_id     uuid NOT NULL REFERENCES vsz.org (id),
  role       text NOT NULL CHECK (role IN ('system_admin', 'system_owner', 'dealer',
                                           'customer', 'customer_admin', 'customer_user')),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, org_id)
);
CREATE UNIQUE INDEX membership_one_holder ON vsz.membership (org_id) WHERE role = 'customer';
CREATE INDEX membership_org ON vsz.membership (org_id);

CREATE FUNCTION vsz.membership_check() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  org_kind       text;
  staff          boolean := NEW.role IN ('system_admin', 'system_owner', 'dealer');
  other_staff    int;
  other_customer int;
BEGIN
  -- One person's memberships are checked one change at a time.
  PERFORM 1 FROM vsz.app_user WHERE id = NEW.user_id FOR UPDATE;
  SELECT kind INTO org_kind FROM vsz.org WHERE id = NEW.org_id;
  IF NOT (NEW.role IN ('system_admin', 'system_owner') AND org_kind = 'platform'
          OR NEW.role = 'dealer' AND org_kind = 'dealer'
          OR NEW.role IN ('customer', 'customer_admin', 'customer_user') AND org_kind = 'customer') THEN
    RAISE EXCEPTION 'the role % can''t be held in a % organisation', NEW.role,
      coalesce(org_kind, 'missing') USING ERRCODE = 'check_violation';
  END IF;
  SELECT count(*) FILTER (WHERE role IN ('system_admin', 'system_owner', 'dealer')),
         count(*) FILTER (WHERE role NOT IN ('system_admin', 'system_owner', 'dealer'))
    INTO other_staff, other_customer
    FROM vsz.membership
   WHERE user_id = NEW.user_id AND org_id <> NEW.org_id;
  IF staff AND other_staff + other_customer > 0 OR NOT staff AND other_staff > 0 THEN
    RAISE EXCEPTION 'staff hold exactly one membership and no customer one'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER membership_check BEFORE INSERT OR UPDATE ON vsz.membership
  FOR EACH ROW EXECUTE FUNCTION vsz.membership_check();

-- Sites, adapters and their activations (D15, D19) ------------------------------------------------

CREATE TABLE vsz.site (                             -- a customer's location
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_id uuid NOT NULL REFERENCES vsz.org (id),
  name        text NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
  timezone    text NOT NULL CHECK (timezone ~ '^[A-Za-z_]+(/[A-Za-z0-9_+-]+)*$'),
  address     jsonb,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, customer_id)                          -- an activation's site belongs to its customer
);
CREATE TRIGGER site_customer BEFORE INSERT OR UPDATE OF customer_id ON vsz.site
  FOR EACH ROW EXECUTE FUNCTION vsz.require_org_kind('customer_id', 'customer');

CREATE TABLE vsz.adapter (                          -- an IoT Thing running the adapter
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  thing_name     text NOT NULL UNIQUE CHECK (thing_name ~ '^adapter-[0-9]{2,6}$'),  -- = cameras.adapterId
  cloud_id_hmac  bytea UNIQUE,                      -- the Cloud ID, keyed hash only; NULL once used
  status         text NOT NULL DEFAULT 'provisioned'
                 CHECK (status IN ('provisioned', 'activated', 'released', 'revoked')),
  dealer_id      uuid REFERENCES vsz.org (id),      -- whose stock it is (NULL = not allocated)
  certificate_id text,                              -- the IoT certificate made with it
  last_seen_at   timestamptz,                       -- IoT lifecycle events
  label          text CHECK (length(label) <= 100),
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER adapter_dealer BEFORE INSERT OR UPDATE OF dealer_id ON vsz.adapter
  FOR EACH ROW EXECUTE FUNCTION vsz.require_org_kind('dealer_id', 'dealer');

CREATE TABLE vsz.adapter_activation (               -- an adapter at a customer, activation to release
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  adapter_id   uuid NOT NULL REFERENCES vsz.adapter (id),
  customer_id  uuid NOT NULL,
  site_id      uuid NOT NULL,
  name         text CHECK (length(name) <= 60),     -- the customer's name for it ("Garage")
  activated_at timestamptz NOT NULL DEFAULT now(),
  activated_by uuid REFERENCES vsz.app_user (id),
  released_at  timestamptz,
  FOREIGN KEY (site_id, customer_id) REFERENCES vsz.site (id, customer_id),
  CHECK (released_at IS NULL OR released_at >= activated_at)
);
CREATE UNIQUE INDEX adapter_activation_one_open ON vsz.adapter_activation (adapter_id)
  WHERE released_at IS NULL;
CREATE INDEX adapter_activation_customer ON vsz.adapter_activation (customer_id);

CREATE FUNCTION vsz.adapter_activation_check() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  stock_of    uuid;
  dealer_of   uuid;
BEGIN
  SELECT dealer_id INTO stock_of FROM vsz.adapter WHERE id = NEW.adapter_id;
  SELECT parent_id INTO dealer_of FROM vsz.org WHERE id = NEW.customer_id;
  IF stock_of IS NOT NULL AND stock_of IS DISTINCT FROM dealer_of THEN
    RAISE EXCEPTION 'this adapter was allocated to another dealer' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER adapter_activation_check BEFORE INSERT OR UPDATE OF adapter_id, customer_id
  ON vsz.adapter_activation FOR EACH ROW EXECUTE FUNCTION vsz.adapter_activation_check();

CREATE TABLE vsz.activation_attempt (               -- the Cloud ID rate limit (D15)
  at        timestamptz NOT NULL DEFAULT now(),
  user_id   uuid,
  source_ip inet,
  ok        boolean NOT NULL
);
CREATE INDEX activation_attempt_user ON vsz.activation_attempt (user_id, at);
CREATE INDEX activation_attempt_source ON vsz.activation_attempt (source_ip, at);

-- Camera shares: what a customer_user may view (D18) -----------------------------------------------

CREATE TABLE vsz.camera_share (
  account_id uuid NOT NULL,
  user_id    uuid NOT NULL,
  camera_id  text NOT NULL CHECK (camera_id ~ '^cam-[0-9]{2,4}$'),   -- cameras.cameraId
  adapter_id uuid NOT NULL REFERENCES vsz.adapter (id),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, camera_id),
  FOREIGN KEY (user_id, account_id) REFERENCES vsz.membership (user_id, org_id) ON DELETE CASCADE
);
CREATE INDEX camera_share_adapter ON vsz.camera_share (adapter_id, account_id);

CREATE FUNCTION vsz.camera_share_check() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM vsz.membership
                  WHERE user_id = NEW.user_id AND org_id = NEW.account_id AND role = 'customer_user') THEN
    RAISE EXCEPTION 'cameras are shared only with a user (customer_user) of the account'
      USING ERRCODE = 'check_violation';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM vsz.adapter_activation
                  WHERE adapter_id = NEW.adapter_id AND customer_id = NEW.account_id
                    AND released_at IS NULL) THEN
    RAISE EXCEPTION 'the camera''s adapter isn''t one of the account''s'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER camera_share_check BEFORE INSERT OR UPDATE ON vsz.camera_share
  FOR EACH ROW EXECUTE FUNCTION vsz.camera_share_check();

-- A release ends the shares of the adapter's cameras (D15).
CREATE FUNCTION vsz.adapter_release_ends_shares() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  DELETE FROM vsz.camera_share WHERE adapter_id = NEW.adapter_id AND account_id = NEW.customer_id;
  RETURN NEW;
END $$;
CREATE TRIGGER adapter_release_ends_shares AFTER UPDATE OF released_at ON vsz.adapter_activation
  FOR EACH ROW WHEN (OLD.released_at IS NULL AND NEW.released_at IS NOT NULL)
  EXECUTE FUNCTION vsz.adapter_release_ends_shares();

-- The price list, plans (one per adapter activation, D19) and invoices (fake, D9) ------------------

CREATE TABLE vsz.price_camera (                     -- one row per combination
  resolution     text NOT NULL CHECK (resolution IN ('2mp', '4mp', '8mp')),
  recording      text NOT NULL CHECK (recording IN ('motion', 'continuous')),
  retention_days int  NOT NULL CHECK (retention_days BETWEEN 1 AND 3650),
  cents_month    int  NOT NULL CHECK (cents_month >= 0),
  currency       text NOT NULL DEFAULT 'EUR' CHECK (currency ~ '^[A-Z]{3}$'),
  PRIMARY KEY (resolution, recording, retention_days)
);

CREATE TABLE vsz.price_setting (                    -- annual discount, trial days, currency, ...
  key        text PRIMARY KEY CHECK (key ~ '^[a-z_]+$'),
  value      jsonb NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE vsz.plan (                             -- every change is a new version: plan history
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  activation_id    uuid NOT NULL REFERENCES vsz.adapter_activation (id),
  version          int  NOT NULL CHECK (version >= 1),
  status           text NOT NULL CHECK (status IN ('trial', 'active', 'cancelled', 'ended')),
  retention_days   int  NOT NULL CHECK (retention_days BETWEEN 1 AND 3650),
  billing_interval text NOT NULL CHECK (billing_interval IN ('monthly', 'annual')),
  cents_period     int  NOT NULL CHECK (cents_period >= 0),   -- computed by the server
  currency         text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
  trial_ends_at    timestamptz,
  created_by       uuid REFERENCES vsz.app_user (id),
  created_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (activation_id, version)
);

CREATE TABLE vsz.plan_line (
  plan_id    uuid NOT NULL REFERENCES vsz.plan (id) ON DELETE CASCADE,
  resolution text NOT NULL CHECK (resolution IN ('2mp', '4mp', '8mp')),
  recording  text NOT NULL CHECK (recording IN ('motion', 'continuous')),
  quantity   int  NOT NULL CHECK (quantity BETWEEN 0 AND 100),
  PRIMARY KEY (plan_id, resolution, recording)
);

CREATE TABLE vsz.invoice (                          -- one per customer per period (D19)
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_id  uuid NOT NULL REFERENCES vsz.org (id),
  period_start date NOT NULL,
  period_end   date NOT NULL CHECK (period_end > period_start),
  cents        int  NOT NULL CHECK (cents >= 0),
  currency     text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
  status       text NOT NULL CHECK (status IN ('draft', 'issued', 'paid', 'void')),
  provider     text NOT NULL DEFAULT 'fake',
  provider_ref text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (customer_id, period_start, provider)
);
CREATE TRIGGER invoice_customer BEFORE INSERT OR UPDATE OF customer_id ON vsz.invoice
  FOR EACH ROW EXECUTE FUNCTION vsz.require_org_kind('customer_id', 'customer');

CREATE TABLE vsz.invoice_line (                     -- one per adapter plan billed
  invoice_id uuid NOT NULL REFERENCES vsz.invoice (id) ON DELETE CASCADE,
  plan_id    uuid NOT NULL REFERENCES vsz.plan (id),
  cents      int  NOT NULL CHECK (cents >= 0),
  PRIMARY KEY (invoice_id, plan_id)
);

CREATE TABLE vsz.audit_event (                      -- append-only: who changed what
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  at            timestamptz NOT NULL DEFAULT now(),
  actor_user_id uuid,
  action        text NOT NULL,
  target_kind   text,
  target_id     uuid,
  detail        jsonb
);
CREATE INDEX audit_event_target ON vsz.audit_event (target_kind, target_id, at);

-- What vsz_app may do: exactly what the directory's operations need ------------------------------

REVOKE ALL ON SCHEMA vsz FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA vsz FROM PUBLIC;
GRANT USAGE ON SCHEMA vsz TO vsz_app;
GRANT SELECT, INSERT, UPDATE         ON vsz.org, vsz.site, vsz.adapter, vsz.adapter_activation,
                                        vsz.invoice TO vsz_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON vsz.app_user,             -- a cancelled invitation
                                        vsz.membership,           -- removed from an account
                                        vsz.price_camera, vsz.price_setting TO vsz_app;
GRANT SELECT, INSERT, DELETE         ON vsz.camera_share, vsz.activation_attempt TO vsz_app;
GRANT SELECT, INSERT                 ON vsz.plan, vsz.plan_line,  -- versions are never edited
                                        vsz.invoice_line, vsz.audit_event TO vsz_app;

-- Seeds: the platform, its own dealer for direct sales (D15), the price list's placeholders ------

INSERT INTO vsz.org (kind, name) VALUES ('platform', 'VideoSafeZone');
INSERT INTO vsz.org (kind, parent_id, name)
  SELECT 'dealer', id, 'Direct' FROM vsz.org WHERE kind = 'platform';

INSERT INTO vsz.price_setting (key, value) VALUES
  ('currency', '"EUR"'),
  ('annual_discount_percent', '10'),
  ('trial_days', '30'),
  ('retention_days', '[7, 30, 90]'),            -- requirements §3
  ('placeholder_prices', 'true');               -- until the owner sets real prices

-- Placeholders, not prices: base by resolution, more for 24/7, more for longer storage.
INSERT INTO vsz.price_camera (resolution, recording, retention_days, cents_month)
SELECT r.resolution, c.recording, d.days, (r.base + c.extra) * d.percent / 100
  FROM (VALUES ('2mp', 300), ('4mp', 350), ('8mp', 500)) AS r (resolution, base),
       (VALUES ('motion', 0), ('continuous', 200)) AS c (recording, extra),
       (VALUES (7, 100), (30, 160), (90, 250)) AS d (days, percent);
