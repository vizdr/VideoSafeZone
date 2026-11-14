-- 003_invoice_rules.sql -- what an invoice may hold (DEALER-PANEL-PLAN.md Phase 8, D9, D19).
--
-- An invoice is one per customer per period (001's UNIQUE), with a line per adapter plan it bills.
-- The fake billing run writes them; these rules hold whoever writes, so no bug in the run can break
-- them:
--   - a line bills a plan of one of the invoice's own customer's adapter activations, in the
--     invoice's currency;
--   - an invoice bills each adapter activation once;
--   - an invoice's total is the sum of its lines -- checked when the transaction commits, since the
--     lines are written after the invoice they belong to.
-- No new table, so no new grant: 001 gave vsz_app SELECT, INSERT, UPDATE on vsz.invoice and SELECT,
-- INSERT on vsz.invoice_line.

CREATE FUNCTION vsz.invoice_line_check() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  invoice_customer uuid;
  invoice_currency text;
  plan_customer    uuid;
  plan_currency    text;
  plan_activation  uuid;
BEGIN
  -- One invoice's lines are checked one at a time.
  SELECT customer_id, currency INTO invoice_customer, invoice_currency
    FROM vsz.invoice WHERE id = NEW.invoice_id FOR UPDATE;
  SELECT act.customer_id, p.currency, p.activation_id INTO plan_customer, plan_currency, plan_activation
    FROM vsz.plan p JOIN vsz.adapter_activation act ON act.id = p.activation_id
   WHERE p.id = NEW.plan_id;
  IF plan_customer IS DISTINCT FROM invoice_customer THEN
    RAISE EXCEPTION 'an invoice bills only its own customer''s plans' USING ERRCODE = 'check_violation';
  END IF;
  IF plan_currency IS DISTINCT FROM invoice_currency THEN
    RAISE EXCEPTION 'the plan is in % and the invoice in %', plan_currency, invoice_currency
      USING ERRCODE = 'check_violation';
  END IF;
  IF EXISTS (SELECT 1 FROM vsz.invoice_line l JOIN vsz.plan p ON p.id = l.plan_id
              WHERE l.invoice_id = NEW.invoice_id AND p.activation_id = plan_activation
                AND l.plan_id <> NEW.plan_id) THEN
    RAISE EXCEPTION 'an invoice bills each adapter''s plan once' USING ERRCODE = 'unique_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER invoice_line_check BEFORE INSERT OR UPDATE ON vsz.invoice_line
  FOR EACH ROW EXECUTE FUNCTION vsz.invoice_line_check();

-- The total, at commit. Argument: the column that names the invoice (id on vsz.invoice, invoice_id
-- on its lines).
CREATE FUNCTION vsz.invoice_total_check() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  checked uuid := (to_jsonb(NEW) ->> TG_ARGV[0])::uuid;
  total   int;
  summed  bigint;
BEGIN
  SELECT cents INTO total FROM vsz.invoice WHERE id = checked;
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;
  SELECT coalesce(sum(cents), 0) INTO summed FROM vsz.invoice_line WHERE invoice_id = checked;
  IF summed <> total THEN
    RAISE EXCEPTION 'an invoice''s total (%) is the sum of its lines (%)', total, summed
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER invoice_total_of_lines AFTER INSERT OR UPDATE ON vsz.invoice_line
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION vsz.invoice_total_check('invoice_id');
CREATE CONSTRAINT TRIGGER invoice_total AFTER INSERT OR UPDATE OF cents ON vsz.invoice
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION vsz.invoice_total_check('id');

REVOKE ALL ON FUNCTION vsz.invoice_line_check(), vsz.invoice_total_check() FROM PUBLIC;
