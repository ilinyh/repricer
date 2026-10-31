FROM python:3.12-slim

WORKDIR /app
ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    REPRICER_DB=/data/repricer.db \
    CSV_PRODUCTS=/data/products.csv \
    CSV_SALES=/data/sales.csv

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir fastapi "uvicorn[standard]" httpx jinja2 python-multipart

EXPOSE 8080
CMD ["uvicorn", "repricer.web.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
