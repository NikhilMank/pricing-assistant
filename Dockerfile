# One container serves the API and the built frontend (same origin, so no CORS and no API address in the build).

# ---- stage 1: build the frontend
FROM node:20-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- stage 2: backend + built frontend
FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
# statsmodels is only needed to fit the model (precompute.py), not to serve results.json
RUN grep -vE "^(statsmodels|mangum)" backend/requirements.txt > /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt
COPY backend/ backend/
COPY data/ data/
COPY --from=frontend /app/frontend/dist frontend/dist
WORKDIR /app/backend
ENV PORT=8080
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
