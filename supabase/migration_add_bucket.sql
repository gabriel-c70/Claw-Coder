-- Migration to add bucket column to credit_balances
-- Run this after updating schema.sql to handle existing data

-- Step 1: Add the bucket column if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'credit_balances' 
        AND column_name = 'bucket'
    ) THEN
        ALTER TABLE public.credit_balances ADD COLUMN bucket text NOT NULL DEFAULT 'tools';
    END IF;
END $$;

-- Step 2: Update existing records to have bucket = 'tools'
UPDATE public.credit_balances SET bucket = 'tools' WHERE bucket IS NULL;

-- Step 3: Drop the old primary key if it exists
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.table_constraints 
        WHERE table_name = 'credit_balances' 
        AND constraint_type = 'PRIMARY KEY'
    ) THEN
        ALTER TABLE public.credit_balances DROP CONSTRAINT credit_balances_pkey;
    END IF;
END $$;

-- Step 4: Add the new composite primary key
ALTER TABLE public.credit_balances ADD PRIMARY KEY (user_id, bucket);

-- Step 5: Update the grant_user_credits function if it doesn't have bucket parameter
CREATE OR REPLACE FUNCTION public.grant_user_credits(
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

-- Step 6: Update the consume_user_credit function if it doesn't have bucket parameter
CREATE OR REPLACE FUNCTION public.consume_user_credit(
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
