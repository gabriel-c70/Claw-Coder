-- Migration: add multi-bucket credit support (tools vs workspace)
-- Safe to run against an existing live database. Idempotent — every
-- step checks before acting, so re-running this causes no harm.

-- ── Step 1: credit_balances.bucket ──────────────────────────────────────
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

UPDATE public.credit_balances SET bucket = 'tools' WHERE bucket IS NULL;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'credit_balances'
        AND constraint_type = 'PRIMARY KEY'
        AND constraint_name = 'credit_balances_pkey'
    ) THEN
        IF NOT EXISTS (
            SELECT 1
            FROM information_schema.key_column_usage
            WHERE constraint_name = 'credit_balances_pkey'
            AND column_name = 'bucket'
        ) THEN
            ALTER TABLE public.credit_balances DROP CONSTRAINT credit_balances_pkey;
        END IF;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'credit_balances'
        AND constraint_type = 'PRIMARY KEY'
    ) THEN
        ALTER TABLE public.credit_balances ADD PRIMARY KEY (user_id, bucket);
    END IF;
END $$;

-- ── Step 2: credit_ledger.bucket (this was missing entirely before) ─────
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'credit_ledger'
        AND column_name = 'bucket'
    ) THEN
        ALTER TABLE public.credit_ledger ADD COLUMN bucket text NOT NULL DEFAULT 'tools';
    END IF;
END $$;

UPDATE public.credit_ledger
SET bucket = metadata->>'bucket'
WHERE bucket = 'tools'
  AND metadata ? 'bucket'
  AND metadata->>'bucket' IS DISTINCT FROM 'tools';

-- ── Step 3: grant_user_credits — now writes bucket into credit_ledger too ─
CREATE OR REPLACE FUNCTION public.grant_user_credits(
  p_user_id uuid,
  p_amount integer,
  p_reason text,
  p_reference_id text,
  p_metadata jsonb DEFAULT '{}'::jsonb,
  p_bucket text DEFAULT 'tools'
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  IF p_amount <= 0 THEN
    RAISE EXCEPTION 'credit grant amount must be positive';
  END IF;

  INSERT INTO public.credit_ledger(user_id, amount, reason, reference_id, metadata, bucket)
  VALUES (p_user_id, p_amount, p_reason, p_reference_id, COALESCE(p_metadata, '{}'::jsonb), p_bucket)
  ON CONFLICT DO NOTHING;

  IF FOUND THEN
    INSERT INTO public.credit_balances(user_id, bucket, balance)
    VALUES (p_user_id, p_bucket, p_amount)
    ON CONFLICT (user_id, bucket) DO UPDATE
      SET balance = public.credit_balances.balance + excluded.balance,
          updated_at = now();
  END IF;
END;
$$;

-- ── Step 4: consume_user_credit — bucket as a real column, not just JSON ─
CREATE OR REPLACE FUNCTION public.consume_user_credit(
  p_user_id uuid,
  p_tool_name text,
  p_amount integer DEFAULT 1,
  p_bucket text DEFAULT 'tools'
) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  IF p_amount <= 0 THEN
    RAISE EXCEPTION 'credit debit amount must be positive';
  END IF;

  UPDATE public.credit_balances
  SET balance = balance - p_amount,
      updated_at = now()
  WHERE user_id = p_user_id
    AND bucket = p_bucket
    AND balance >= p_amount;

  IF NOT FOUND THEN
    RETURN false;
  END IF;

  INSERT INTO public.credit_ledger(user_id, amount, reason, metadata, bucket)
  VALUES (
    p_user_id,
    -p_amount,
    'tool_usage',
    jsonb_build_object('tool_name', p_tool_name),
    p_bucket
  );

  RETURN true;
END;
$$;