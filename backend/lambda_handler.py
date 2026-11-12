"""AWS Lambda entry point: wraps the FastAPI app so a Lambda Function URL can call it."""
import os
import sys

# The zip keeps the repo layout (backend/app, backend/artifacts, data/), so `app` lives under backend/
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))

from mangum import Mangum  # noqa: E402

from app.main import app  # noqa: E402

handler = Mangum(app, lifespan="off")
