# Pricing Assistant

A small project that turns daily sales data into price recommendations. It shows two years of daily sales (60 products in 3 markets: DE, FR, CH), estimates how sensitive buyers are to price, recommends a profit-maximising price inside a safe range, and lets three AI agents explain and check each recommendation. A small evaluation script tests the agents' output.

**Live demo** (Google Cloud Run, agents run on Gemini on Vertex AI): https://pricing-assistant-817090878834.europe-west3.run.app

An AI analysis takes about a minute when Google's newest models are available, and up to a few minutes when they are busy and the next model in the list has to answer.

## How it works

```
Offline, once (backend/precompute.py):
  CSV -> features -> demand model (per product) -> optimizer (per product-market)
      -> promotion check -> artifacts/results.json

At request time:
  React + Plotly  <->  FastAPI  <-  results.json   (overview, charts, recommendations)
                          |
                          +-> agents (LangGraph) -> language model, chosen by LLM_PROVIDER:
                                                    Vertex AI (Gemini), Bedrock (Claude) or a fake offline model
```

1. **Demand model** (`backend/app/model.py`). One Poisson regression per product: `units ~ log(price) + market + Black Friday + weekday + month + year + log(1 + ad spend)`. The coefficient on log(price) is the price elasticity. Poisson fits small daily counts with many zeros (36% of rows). Robust (HC1) standard errors give an honest margin of error. Markets share one elasticity; market is a control. Products without ad data (9) are fitted without the ad term.
2. **Confidence tier** (high, medium, low) comes from the upper end of the 95% interval of the elasticity (below -1: high, below 0: medium, otherwise low). The final tier is the weakest of three model versions (full, without year, without Black Friday), so a conclusion that depends on one choice of controls is not trusted. Across the 59 live products: 34 high, 20 medium, 5 low.
3. **Optimizer** (`backend/app/optimizer.py`). Tries 41 prices inside an allowed range and picks the highest profit, (price - cost) x predicted units. The allowed range is the overlap of: prices held at least 14 days in a row ("tested"), within 20% of the usual price (median of the last 90 days), and above cost. High tier moves fully to the best price, medium goes half the way (at most 5%, shown as a "test price"), low holds the price.
4. **Promotion check** (`promo_check` in `model.py`). Promotion days bring extra visitors, so each product is fitted a second time without the days priced below 90% of the usual price. The verdict (holds, weaker, noisy) is shown on the product page and passed to the agents. It is informational only: tiers and prices do not change.
5. **Agents** (`backend/app/agents.py`). LangGraph: Analyst, then Strategist, then Risk reviewer. The reviewer sees the data, the analysis and the proposal, and on a repeat review also what it asked before; the analyst and the strategist also see their own earlier attempts that were sent back, with the reviewer's comments. If it asks for changes it also says where the fix starts: the analysis (back to the analyst, then the strategist writes again) or only the proposal (back to the strategist). At most 3 passes (the first one and two revisions) and about 300 seconds, enforced in code; if the reviewer is still not satisfied, the usual price is shown. All three agents think at a high level; the reviewer runs on a stronger model than the other two. Each agent has a list of models (its own first, then the others): if one is busy, too slow or not available to the project, the next one answers (a model that just failed is skipped for a minute), and the page says which model answered. The AI never calculates: all numbers come from the code above. Hard rules (price at or above cost, inside the range, never bolder than the tier allows, rejected means the usual price is shown) are plain Python outside the AI. `get_chat_model()` picks the model (one for the analyst and strategist, one for the reviewer), so the same agents run on Vertex AI, Bedrock or the fake model.
6. **Evaluation** (`backend/evals.py`). Five example cases plus tests with deliberately bad inputs. Checks: output structure, every cited number exists in the input data, the promotion check is mentioned when it matters, price above cost and inside the range, low tier holds, medium moves at most 5%, a rejection gives the usual price, the reviewer sees the analysis, a weak analysis goes back to the analyst and a weak proposal to the strategist, the loop stops at its limits, and the reviewer uses its own model.
7. **Frontend** (`frontend/`). Overview table of all 177 product-markets (sortable, filterable, default order: confidence first), then a product page with price and sales over time, price versus sales, the profit curve (grey dashed where the model has no data), the promotion check and the agent cards.
8. **Custom recalculation** (`#/refit`, opened from the homepage in a new tab; a product picker on the page chooses the product). A separate what-if page: switch parts of the calculation on or off (market, weekday, month, year, Black Friday, ad spend, promotion days, how long a price must be held to count as tested) and recalculate one product or all of them, shown next to the official numbers. It uses the same model and optimizer code, keeps results only in memory and never changes `results.json` or the official pages. `MAX_REFITS` caps the number of recalculations per server process.

Fitting all products takes about two minutes, so `backend/precompute.py` does it once and saves `backend/artifacts/results.json` (committed). The API only reads that file.

## Run it locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
cd frontend && npm install && npm run build && cd ..

# Offline, no cloud account needed (a fake model writes the agent texts):
cd backend && LLM_PROVIDER=fake ../.venv/bin/python -m uvicorn app.main:app --port 8000
# open http://localhost:8000

# With a real model, Vertex AI (after: gcloud auth application-default login):
cd backend && GOOGLE_CLOUD_PROJECT=<project> ../.venv/bin/python -m uvicorn app.main:app --port 8000
#   or Bedrock instead (AWS credentials with Bedrock access in the environment):
cd backend && LLM_PROVIDER=bedrock ../.venv/bin/python -m uvicorn app.main:app --port 8000

# Development with hot reload: backend as above, then
cd frontend && npm run dev          # http://localhost:5173, /api is forwarded to port 8000
```

Re-create the results (only needed if the data or model changes): `cd backend && ../.venv/bin/python -W ignore precompute.py`.

Evaluation: `cd backend && ../.venv/bin/python -W ignore evals.py` (offline fake model). With `--live` it uses the real model of the chosen provider: `GOOGLE_CLOUD_PROJECT=<project> ../.venv/bin/python -W ignore evals.py --live` (add `LLM_PROVIDER=bedrock` to use Bedrock). Exit code 1 if any check fails.

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `vertex` | `vertex` (Gemini on Google Cloud), `bedrock` (Claude on AWS, optional) or `fake` (offline, for tests) |
| `VERTEX_MODEL` | `gemini-4-hydrogen` | model used when `LLM_PROVIDER=vertex` |
| `VERTEX_LOCATION` | `global` | Google Cloud location for Vertex AI |
| `VERTEX_REASONING` | `high` | reasoning effort of the analyst and strategist (thinking counts as output tokens) |
| `VERTEX_REVIEWER_MODEL` | `gemini-4-argon` | stronger model used by the risk reviewer |
| `VERTEX_MODEL_HIERARCHY` | `gemini-4-argon,gemini-4-carbon,gemini-4-hydrogen,gemini-3.8-flash` | models tried one after the other, best first, when an agent's own model is busy or not available to the project |
| `VERTEX_REVIEWER_REASONING` | `high` | reasoning effort of the risk reviewer |
| `GOOGLE_CLOUD_PROJECT` | none | Google Cloud project used for Vertex AI |
| `BEDROCK_MODEL_ID` | `eu.anthropic.claude-haiku-4-5-20251001-v1:0` | model used when `LLM_PROVIDER=bedrock` |
| `BEDROCK_REVIEWER_MODEL_ID` | same as `BEDROCK_MODEL_ID` | optional separate model for the risk reviewer on Bedrock |
| `BEDROCK_REGION` | `eu-central-1` | AWS region for Bedrock |
| `CORS_ORIGINS` | `http://localhost:5173` | websites allowed to call the API (comma separated) |
| `MAX_ANALYSES` | `300` | cost guard: AI analyses per server process |
| `MAX_REFITS` | `300` | cost guard: custom recalculations per server process |
| `VITE_API_BASE` | empty | frontend build setting: address of the API when it is not on the same site |

No API keys are stored in the repository. Vertex AI uses the Google identity of the environment (the Cloud Run service account when deployed, `gcloud auth application-default login` locally). Bedrock, if chosen, uses the normal AWS credential chain.

## Deployment

The app runs on Google Cloud Run. A Terraform setup (`terraform/gcp/`) recreates everything from scratch.

The `Dockerfile` in the repository root builds the frontend and the backend into one container. The API serves the built frontend, so there is one address and no CORS setup. Deploy from the repository root with:

```bash
gcloud run deploy pricing-assistant --source . --region europe-west3 --allow-unauthenticated --memory 1Gi --cpu 1 --max-instances 2 --timeout 600 --set-env-vars LLM_PROVIDER=vertex,GOOGLE_CLOUD_PROJECT=<project>,VERTEX_MODEL=gemini-4-hydrogen,VERTEX_LOCATION=global,MAX_ANALYSES=300
```

The service runs as a service account with the Vertex AI User role, so no keys are needed. The Terraform version creates the APIs, the image registry, the service account and the public service, and builds the image with Cloud Build: `cd terraform/gcp && terraform init && terraform apply -var project_id=<project>`.

A GitHub Actions workflow (`.github/workflows/deploy-cloudrun.yml`) deploys this version on every push to `main` that changes the app: it runs the offline evals, builds the image, pushes it to Artifact Registry, updates the Cloud Run service and checks `/api/health`. Only the image changes, so the settings from Terraform stay. It signs in to Google without a key: Terraform also creates a Workload Identity Federation pool that accepts only this repository on the `main` branch, and a deploy service account that can push images and update this one service. One-time setup: add three repository variables, `GCP_PROJECT_ID`, `GCP_WIF_PROVIDER` and `GCP_DEPLOY_SA`. After `terraform apply` the outputs `github_wif_provider` and `github_deploy_service_account` give the values. For a project whose service already exists (created with `gcloud run deploy`), run `bash terraform/gcp/setup-ci-existing-project.sh <project-id>` once instead: it creates the same pool, account and permissions with `gcloud` and prints the three `gh variable set` commands.

## Design choices worth knowing

- **Why not a plain log-log regression?** 36% of daily rows have zero sales, and log(0) does not exist. Poisson handles zeros and keeps the elasticity reading.
- **Why are web sessions not a control?** A price cut itself brings visitors, so sessions are partly the price effect. Ad spend is used where available (50 live products), as an association, not as ROI.
- **Why are recommendations small and often "capped"?** The model only knows prices the shop has tested. About 9 in 10 best prices sit on the edge of the tested range, so the label says "capped at the highest/lowest tested price". The median move is about 2%.
- **Why precompute?** Fitting takes about two minutes, too slow for a request or a cold start. The results file ships inside the container.
- **Why can the AI not calculate?** Models invent numbers. The agents only explain finished numbers, and the evals check that every number they quote exists in their input.
- **Unrestricted optimum.** `cost x b / (1 + b)` is shown only as a small "not recommended" line with a range, because it reacts violently to small changes in b.

## Limitations

- Promotions bring extra traffic and pulled-forward sales, so the elasticity may overstate the response to a lasting price change. It cannot be re-estimated on held prices only (the margin of error becomes about 7 times larger). The promotion check shows how much of the result depends on those days.
- Estimates are approximate and depend on the model's controls. The profit change is a model estimate, measured on peak-season days (Oct to Dec 2025, without Black Friday).
- The ad-spend coefficient is an association, not return on investment.
- The agent cache and the `MAX_ANALYSES` counter live in one server process and are lost when it restarts.
- Vertex AI runs in the `global` location, which does not pin requests to the EU.
- An A/B test is the proper next step before changing real prices.

## Repository layout

```
backend/     API, model, optimizer, agents, evals, precompute script, results.json
frontend/    React + Vite + Plotly app
data/        daily sales CSV
terraform/   gcp/: infrastructure as code for Cloud Run, and the setup script for the GitHub deploy
.github/     the Cloud Run deploy workflow
Dockerfile   one container with API and frontend (Cloud Run)
```
