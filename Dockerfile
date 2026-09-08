FROM python:3.12-slim

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code.
COPY app ./app
COPY worker ./worker
COPY scripts ./scripts
COPY tests ./tests
COPY pytest.ini .

# Default command is the API; the worker service overrides this in compose.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
