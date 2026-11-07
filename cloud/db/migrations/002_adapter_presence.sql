-- 002_adapter_presence.sql -- whether an adapter is connected now (DEALER-PANEL-PLAN.md Phase 6.4).
--
-- AWS IoT publishes a lifecycle event each time an adapter's MQTT session connects or disconnects;
-- an IoT rule hands them to vsz-directory, which keeps the newest here: last_seen_at (001) is that
-- event's time, connected whether it was a connect. NULL: never seen since this column came.
-- vsz_app's table-wide UPDATE on vsz.adapter (001) covers the new column.

ALTER TABLE vsz.adapter ADD COLUMN connected boolean;
