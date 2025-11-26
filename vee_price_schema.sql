-- vee_price_schema.sql

CREATE TABLE IF NOT EXISTS vee_price_snapshots (
    id          bigserial PRIMARY KEY,
    ts          timestamptz NOT NULL DEFAULT now(),
    price_usd   numeric(18,8) NOT NULL,
    source      text NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_vee_price_snapshots_ts
    ON vee_price_snapshots (ts DESC);
