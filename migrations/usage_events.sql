-- ============================================================
-- usage_events — har bir AI chaqiruvi uchun bitta yozuv.
-- Feature 2 (admin /usage) va Feature 3 (A.Y.P.I dashboard) poydevori.
--
-- Bir marta Supabase SQL editor'da ishga tushiring (Dashboard > SQL Editor > New query).
-- ============================================================

create table if not exists public.usage_events (
  id                bigint generated always as identity primary key,
  telegram_id       bigint,                       -- o'quvchi (null = noma'lum/admin)
  service           text   not null,              -- qa, channel_seo, video_seo, channel_analysis, avatar, banner, thumbnail, grader
  kind              text   not null default 'text', -- 'text' | 'image'
  model             text,                         -- gemini-2.5-flash, flux-schnell, ...
  prompt_tokens     integer not null default 0,
  completion_tokens integer not null default 0,
  total_tokens      integer not null default 0,
  cost_usd          numeric(12,6) not null default 0,  -- taxminiy xarajat
  created_at        timestamptz not null default now()
);

create index if not exists idx_usage_tg      on public.usage_events(telegram_id, created_at desc);
create index if not exists idx_usage_created on public.usage_events(created_at desc);
create index if not exists idx_usage_service on public.usage_events(service);

-- ------------------------------------------------------------
-- Agregatsiya — RPC funksiyalar (millionlab qatorda ham tez,
-- chunki hisoblash Postgres'da bo'ladi, hamma qator tortilmaydi).
-- ------------------------------------------------------------

-- Umumiy xulosa (oxirgi N kun)
create or replace function public.usage_summary(days integer default 30)
returns table(total_events bigint, active_users bigint, total_tokens bigint, total_cost numeric)
language sql stable as $$
  select count(*)::bigint,
         count(distinct telegram_id)::bigint,
         coalesce(sum(total_tokens), 0)::bigint,
         coalesce(sum(cost_usd), 0)
  from public.usage_events
  where created_at >= now() - make_interval(days => days);
$$;

-- Kunlik dinamika
create or replace function public.usage_daily(days integer default 30)
returns table(day date, events bigint, tokens bigint, cost numeric)
language sql stable as $$
  select created_at::date,
         count(*)::bigint,
         coalesce(sum(total_tokens), 0)::bigint,
         coalesce(sum(cost_usd), 0)
  from public.usage_events
  where created_at >= now() - make_interval(days => days)
  group by 1 order by 1 desc;
$$;

-- Eng faol o'quvchilar (xarajat bo'yicha)
create or replace function public.usage_top_users(days integer default 30, lim integer default 20)
returns table(telegram_id bigint, events bigint, tokens bigint, cost numeric)
language sql stable as $$
  select telegram_id,
         count(*)::bigint,
         coalesce(sum(total_tokens), 0)::bigint,
         coalesce(sum(cost_usd), 0)
  from public.usage_events
  where created_at >= now() - make_interval(days => days)
    and telegram_id is not null
  group by telegram_id order by 4 desc limit lim;
$$;

-- Eng ko'p ishlatilgan xizmatlar
create or replace function public.usage_by_service(days integer default 30)
returns table(service text, events bigint, tokens bigint, cost numeric)
language sql stable as $$
  select service,
         count(*)::bigint,
         coalesce(sum(total_tokens), 0)::bigint,
         coalesce(sum(cost_usd), 0)
  from public.usage_events
  where created_at >= now() - make_interval(days => days)
  group by service order by 2 desc;
$$;

-- Bitta o'quvchi bo'yicha tafsilot (oxirgi N kun)
create or replace function public.usage_user_detail(p_telegram_id bigint, days integer default 90)
returns table(service text, events bigint, tokens bigint, cost numeric)
language sql stable as $$
  select service,
         count(*)::bigint,
         coalesce(sum(total_tokens), 0)::bigint,
         coalesce(sum(cost_usd), 0)
  from public.usage_events
  where telegram_id = p_telegram_id
    and created_at >= now() - make_interval(days => days)
  group by service order by 4 desc;
$$;
