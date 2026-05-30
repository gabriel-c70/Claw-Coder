# Hey there bet you did not know this is a RAG agent
### Let's go over what RAG really is in agentic workflows.
### RAG in simple terms is a way to feed huge chunks of text by breaking each word into a vector to an LLM cause the context window of an LLM is so small the solution was to create RAG.

## SO WHERE DO THESE VECTORS GO
### Well they go into a vector database like chromadb, Fiass-sth like that, and so many others so why do these vectors matter well once they are in the vector db they are considered as vector embeddings and when the user sends a prompt to an AI it carrys out something we call vector searching to find what is most similar to the user's prompt but how - this is possible through cosine similarity search which is finding the most similar meaning of the user's prompt compared to a vector embedding in the vector db.
### I dont want to make this search a boring explanation so this is the simplest explanation i can give you about RAG thank you.
[helo](https://cloud.google.com/discover/what-is-prompt-engineering)

````python
def hello():
    print('hello')
hello()
````

## Billing, Credits, and Render Setup

Claw Coder uses the hosted FastAPI server in `agent_sever.py` to enforce cloud
tool limits. Local tools stay free. Limited tools use the monthly free allowance
first, then paid credits. The paid plan is a `$10/month` Dodo subscription that
adds `100` credits when the subscription starts and on each monthly renewal.
Users can also buy one-time top-up credits if they use their monthly credits
before the next renewal.

### Supabase

1. Open your Supabase project dashboard.
2. Go to **SQL Editor**.
3. Run the full contents of `supabase/schema.sql`.
4. Copy these project values for Render:

```bash
SUPABASE_URL=...
SUPABASE_SERVICE_KEY=...        # service role key, never anon
SUPABASE_ANON_KEY=...           # used by the CLI login helper
```

### Dodo Payments

1. In Dodo Payments, create a recurring monthly product priced at **$10 USD/month**.
2. Copy the product id into Render as `DODO_MONTHLY_PRODUCT_ID`.
3. Create a one-time top-up product, for example **$10 USD** for extra credits.
4. Copy the top-up product id into Render as `DODO_TOPUP_PRODUCT_ID`.
5. Create an API key and set it as `DODO_PAYMENTS_API_KEY`.
6. After Render is deployed, create a webhook endpoint:

```text
https://YOUR-RENDER-SERVICE.onrender.com/webhooks/dodo
```

7. Enable these events:
   - `subscription.active`
   - `subscription.renewed`
   - `subscription.cancelled`
   - `subscription.on_hold`
   - `subscription.failed`
   - `subscription.expired`
   - `payment.succeeded`
8. Copy the webhook signing secret into Render as `DODO_PAYMENTS_WEBHOOK_KEY`.

### Render Environment

Set these environment variables on the Render web service:

```bash
SUPABASE_URL=...
SUPABASE_SERVICE_KEY=...
TAVILY_API_KEY=...
RATE_LIMIT_API_URL=https://YOUR-RENDER-SERVICE.onrender.com

DODO_PAYMENTS_BASE_URL=https://test.dodopayments.com
DODO_PAYMENTS_API_KEY=...
DODO_PAYMENTS_WEBHOOK_KEY=...
DODO_MONTHLY_PRODUCT_ID=...
DODO_MONTHLY_CREDITS=100
DODO_TOPUP_PRODUCT_ID=...
DODO_TOPUP_CREDITS=100
DODO_RETURN_URL=https://YOUR-RENDER-SERVICE.onrender.com/payment-success
```

Use `https://live.dodopayments.com` for `DODO_PAYMENTS_BASE_URL` when you move
from test mode to live mode.

Recommended Render start command:

```bash
uvicorn agent_sever:app --host 0.0.0.0 --port $PORT
```

### User Commands

```bash
claw usage      # monthly usage plus paid credits
claw credits    # current paid credit balance
claw buy        # opens Dodo checkout for the $10/month subscription
claw topup      # opens Dodo checkout for one-time extra credits
```
