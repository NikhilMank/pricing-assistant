# Pricing Assistant

**Live demo:**

- Google Cloud Run (Gemini on Vertex AI): https://pricing-assistant-817090878834.europe-west3.run.app
- AWS (Lambda + CloudFront, Claude Haiku 4.5 on Bedrock): https://d2oyuycmo2qef6.cloudfront.net

Both serve the same app and data. The first AI analysis after a pause can take a few extra seconds.

A small project that turns daily sales data into price recommendations. It shows two years of daily sales (60 products in 3 markets: DE, FR, CH), estimates how sensitive buyers are to price, recommends a profit-maximising price inside a safe range, and lets three AI agents explain and check each recommendation. A small evaluation script tests the agents' output.

## How it works

```
CSV -> features -> demand model (per product) -> optimizer (per product-market) -> results.json
                                                                                       |
                         React + Plotly  <-  FastAPI  <-  agents (LangGraph, Bedrock)  +
```

1. **Demand model** (`backend/app/model.py`). One Poisson regression per product: `units ~ log(price) + market + Black Friday + weekday + month + year + log(1 + ad spend)`. The coefficient on log(price) is the price elasticity. Poisson fits small daily counts with many zeros (36% of rows). Robust (HC1) standard errors give an honest margin of error. Markets share one elasticity; market is a control.
2. **Confidence tier** (high, medium, low) comes from the upper end of the 95% interval of the elasticity (below -1: high, below 0: medium, otherwise low). The final tier is the weakest of three model versions (full, without year, without Black Friday), so a conclusion that depends on one choice of controls is not trusted.
3. **Optimizer** (`backend/app/optimizer.py`). Tries 41 prices inside an allowed range and picks the highest profit, (price - cost) x predicted units. The allowed range is the overlap of: prices held at least 14 days in a row ("tested"), within 20% of the usual price (median of the last 90 days), and above cost. High tier moves fully to the best price, medium goes half the way (at most 5%, shown as a "test price"), low holds the price.
4. **Agents** (`backend/app/agents.py`). LangGraph: Analyst, then Strategist, then Risk reviewer, with one conditional edge back to the strategist (one revision at most, enforced in code). The AI never calculates: all numbers come from the code above. Hard rules (price at or above cost, inside the range, never bolder than the tier allows, rejected means the usual price is shown) are plain Python outside the AI.
5. **Evaluation** (`backend/evals.py`). Five example cases plus tests with deliberately bad inputs. Checks: output structure, every cited number exists in the input data, price above cost and inside the range, low tier holds, medium moves at most 5%, a rejection gives the usual price, at most one revision.
6. **Frontend** (`frontend/`). Overview table of all 177 product-markets (sortable, filterable, default order: confidence first), then a product page with price and sales over time, price versus sales, the profit curve (grey dashed where the model has no data) and the agent cards.

Fitting all products takes about two minutes, so `backend/precompute.py` does it once and saves `backend/artifacts/results.json` (committed). The API only reads that file.

## Run it locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
cd frontend && npm install && npm run build && cd ..

# Offline, no AWS needed (a fake model writes the agent texts):
cd backend && LLM_PROVIDER=fake ../.venv/bin/python -m uvicorn app.main:app --port 8000
# open http://localhost:8000

# With the real model (AWS credentials with Bedrock access in the environment):
cd backend && ../.venv/bin/python -m uvicorn app.main:app --port 8000

# Development with hot reload: backend as above, then
cd frontend && npm run dev          # http://localhost:5173, /api is forwarded to port 8000
```

Re-create the results (only needed if the data or model changes): `cd backend && ../.venv/bin/python -W ignore precompute.py`.

Evaluation: `cd backend && ../.venv/bin/python -W ignore evals.py` (offline) or `... evals.py --live` (real model on Bedrock). Exit code 1 if any check fails.

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `bedrock` | `bedrock`, `vertex` (Gemini on Google Cloud) or `fake` (offline, for tests) |
| `BEDROCK_MODEL_ID` | `eu.anthropic.claude-haiku-4-5-20251001-v1:0` | model used by the agents |
| `BEDROCK_REGION` | `eu-central-1` | AWS region for Bedrock |
| `VERTEX_MODEL` | `gemini-4-hydrogen` | Gemini model used when `LLM_PROVIDER=vertex` |
| `VERTEX_LOCATION` | `global` | Google Cloud location for Vertex AI |
| `VERTEX_REASONING` | `low` | reasoning effort of the Gemini model (its thinking counts as output tokens) |
| `GOOGLE_CLOUD_PROJECT` | none | Google Cloud project used for Vertex AI |
| `CORS_ORIGINS` | `http://localhost:5173` | websites allowed to call the API (comma separated) |
| `MAX_ANALYSES` | `300` | cost guard: AI analyses per server process |
| `VITE_API_BASE` | empty | frontend build setting: address of the API when it is not on the same site |

No API keys are stored in the repository. Bedrock uses the normal AWS credential chain (an IAM role when deployed). Vertex AI uses the Google identity of the environment (the Cloud Run service account when deployed, `gcloud auth application-default login` locally).

## Design choices worth knowing

- **Why not a plain log-log regression?** 36% of daily rows have zero sales, and log(0) does not exist. Poisson handles zeros and keeps the elasticity reading.
- **Why are web sessions not a control?** A price cut itself brings visitors, so sessions are partly the price effect. Ad spend is used where available (50 live products), as an association, not as ROI.
- **Why are recommendations small and often "capped"?** The model only knows prices the shop has tested. About 9 in 10 best prices sit on the edge of the tested range, so the label says "capped at the highest/lowest tested price". The median move is about 2%.
- **Promotion check.** Promotion days bring extra visitors, so for each product the model is fitted a second time without the days priced below 90% of the usual price. The product page says whether the result holds, gets weaker, or cannot be told without those days (informational only: confidence tiers and prices do not change).
- **Unrestricted optimum.** `cost x b / (1 + b)` is shown only as a small "not recommended" line with a range, because it reacts violently to small changes in b.

## Limitations

- Promotions bring extra traffic and pulled-forward sales, so the elasticity may overstate the response to a lasting price change. It cannot be re-estimated on held prices only (the margin of error becomes about 7 times larger).
- Estimates are approximate and depend on the model's controls. The profit change is a model estimate, measured on peak-season days (Oct to Dec 2025, without Black Friday).
- The ad-spend coefficient is an association, not return on investment.
- The agent cache is lost when a container restarts. An A/B test is the proper next step before changing prices.

## Deployment on Google Cloud Run

The `Dockerfile` in the repository root builds the frontend and the backend into one container (same origin, so no CORS setup). Deploy from the repository root with:

```bash
gcloud run deploy pricing-assistant --source . --region europe-west3 --allow-unauthenticated --memory 1Gi --max-instances 2 --timeout 300 --set-env-vars LLM_PROVIDER=vertex,GOOGLE_CLOUD_PROJECT=<project>,VERTEX_MODEL=gemini-4-hydrogen,VERTEX_LOCATION=global,MAX_ANALYSES=300
```

The service runs as the Cloud Run service account with the Vertex AI User role, so no keys are needed.

The same setup as Terraform (APIs, image build, service account, public service) is in `terraform/gcp/`.

## Deployment on AWS

The AWS deployment (Lambda + CloudFront) is a second working deployment. It runs in eu-central-1: the frontend as static files in S3 behind CloudFront, the backend as a Lambda function with a Function URL, and Bedrock (Claude Haiku 4.5) called through the function's IAM role (no keys anywhere). `backend/build_lambda.py` builds the Lambda zip without Docker. Set `CORS_ORIGINS` to the CloudFront address and build the frontend with `VITE_API_BASE` set to the Function URL.

The whole AWS setup can also be recreated with Terraform (`terraform/aws/`): build the zip, `terraform apply`, then build and upload the frontend with the printed API address.

## Repository layout

```
backend/    API, model, optimizer, agents, evals, precompute script, results.json
frontend/   React + Vite + Plotly app
data/       daily sales CSV
terraform/  aws/ and gcp/: infrastructure as code for both deployments
```
