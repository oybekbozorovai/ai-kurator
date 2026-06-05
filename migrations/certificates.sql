-- ============================================================
-- certificates — kurs sertifikatlari.
--
-- Bir marta Supabase SQL Editor'da ishga tushiring (Dashboard > SQL Editor > New query).
-- cohort_id turi users.cohort_id bilan bir xil: uuid.
-- ============================================================

create table if not exists public.certificates (
    id          text        primary key,
    telegram_id bigint      not null,
    full_name   text        not null,
    course      text        not null default 'YouTube AI',
    cohort_id   uuid        not null references public.cohorts(id),
    issued_at   timestamptz not null default now(),
    unique(telegram_id, cohort_id)
);

create index if not exists idx_cert_telegram on public.certificates(telegram_id);
create index if not exists idx_cert_cohort   on public.certificates(cohort_id);
create index if not exists idx_cert_issued   on public.certificates(issued_at desc);
