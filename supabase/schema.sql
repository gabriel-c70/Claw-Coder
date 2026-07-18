-- Claw Coder billing and credit accounting.
-- Run this in Supabase SQL Editor with the service role/project owner.

create extension if not exists pgcrypto;

create table if not exists public.tool_usage (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  tool_name text not null,
  month_key text not null,
  count integer not null default 0 check (count >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, tool_name, month_key)
);

create table if not exists public.credit_balances (
  user_id uuid not null references auth.users(id) on delete cascade,
  bucket text not null default 'tools',
  balance integer not null default 0 check (balance >= 0),
  updated_at timestamptz not null default now(),
  primary key (user_id, bucket)
);


create table if not exists public.subscriptions (
  user_id uuid primary key references auth.users(id) on delete cascade,
  plan text not null default 'pro',
  status text not null default 'active',
  dodo_subscription_id text unique,
  valid_until timestamptz,
  raw_event jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.credit_ledger (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  amount integer not null check (amount <> 0),
  reason text not null,
  reference_id text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create unique index if not exists credit_ledger_reference_id_unique
  on public.credit_ledger(reference_id)
  where reference_id is not null and amount > 0;

create table if not exists public.dodo_payments (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users(id) on delete set null,
  payment_id text unique,
  checkout_session_id text,
  status text not null,
  amount integer,
  currency text,
  credits integer not null default 0,
  raw_event jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.webhook_events (
  id uuid primary key default gen_random_uuid(),
  webhook_id text unique,
  event_type text,
  data jsonb not null default '{}'::jsonb,
  processed boolean not null default false,
  error_message text,
  created_at timestamptz not null default now(),
  processed_at timestamptz
);

create or replace function public.grant_user_credits(
  p_user_id uuid,
  p_amount integer,
  p_reason text,
  p_reference_id text,
  p_metadata jsonb default '{}'::jsonb,
  p_bucket text default 'tools'
) returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if p_amount <= 0 then
    raise exception 'credit grant amount must be positive';
  end if;

  insert into public.credit_ledger(user_id, amount, reason, reference_id, metadata)
  values (p_user_id, p_amount, p_reason, p_reference_id, coalesce(p_metadata, '{}'::jsonb))
  on conflict do nothing;

  if found then
    insert into public.credit_balances(user_id, bucket, balance)
    values (p_user_id, p_bucket, p_amount)
    on conflict (user_id, bucket) do update
      set balance = public.credit_balances.balance + excluded.balance,
          updated_at = now();
  end if;
end;
$$;

create or replace function public.consume_user_credit(
  p_user_id uuid,
  p_tool_name text,
  p_amount integer default 1,
  p_bucket text default 'tools'
) returns boolean
language plpgsql
security definer
set search_path = public
as $$
begin
  if p_amount <= 0 then
    raise exception 'credit debit amount must be positive';
  end if;

  update public.credit_balances
  set balance = balance - p_amount,
      updated_at = now()
  where user_id = p_user_id
    and bucket = p_bucket
    and balance >= p_amount;

  if not found then
    return false;
  end if;

  insert into public.credit_ledger(user_id, amount, reason, metadata)
  values (
    p_user_id,
    -p_amount,
    'tool_usage',
    jsonb_build_object('tool_name', p_tool_name, 'bucket', p_bucket)
  );

  return true;
end;
$$;

alter table public.tool_usage enable row level security;
alter table public.credit_balances enable row level security;
alter table public.subscriptions enable row level security;
alter table public.credit_ledger enable row level security;
alter table public.dodo_payments enable row level security;
alter table public.webhook_events enable row level security;

drop policy if exists "users can read own tool usage" on public.tool_usage;
create policy "users can read own tool usage"
  on public.tool_usage for select
  using (auth.uid() = user_id);

drop policy if exists "users can read own credit balance" on public.credit_balances;
create policy "users can read own credit balance"
  on public.credit_balances for select
  using (auth.uid() = user_id);

drop policy if exists "users can read own subscription" on public.subscriptions;
create policy "users can read own subscription"
  on public.subscriptions for select
  using (auth.uid() = user_id);

drop policy if exists "users can read own credit ledger" on public.credit_ledger;
create policy "users can read own credit ledger"
  on public.credit_ledger for select
  using (auth.uid() = user_id);

drop policy if exists "users can read own payments" on public.dodo_payments;
create policy "users can read own payments"
  on public.dodo_payments for select
  using (auth.uid() = user_id);
