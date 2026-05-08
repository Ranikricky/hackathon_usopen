-- Horizon XL durable JSON store for Supabase.
-- Run this once in Supabase SQL Editor.

create table if not exists public.horizon_kv (
  key text primary key,
  value jsonb not null,
  updated_at timestamptz not null default now()
);

create or replace function public.touch_horizon_kv_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists horizon_kv_touch_updated_at on public.horizon_kv;

create trigger horizon_kv_touch_updated_at
before update on public.horizon_kv
for each row
execute function public.touch_horizon_kv_updated_at();

alter table public.horizon_kv enable row level security;

-- Service-role keys bypass RLS. Do not expose the service-role key in frontend code.
-- If you only use the anon key, add stricter policies before production use.
