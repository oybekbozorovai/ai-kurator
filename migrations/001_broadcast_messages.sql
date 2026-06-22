-- Supabase Dashboard → SQL Editor da ishga tushiring
CREATE TABLE IF NOT EXISTS broadcast_messages (
  id          BIGSERIAL PRIMARY KEY,
  telegram_id BIGINT NOT NULL,
  message_id  BIGINT NOT NULL,
  broadcast_type TEXT NOT NULL,
  sent_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_broadcast_messages_type
  ON broadcast_messages (broadcast_type);
