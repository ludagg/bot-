FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml alembic.ini ./
COPY sih ./sih
COPY migrations ./migrations
RUN pip install --no-cache-dir .
