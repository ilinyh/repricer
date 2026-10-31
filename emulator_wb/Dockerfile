FROM python:3.12-slim

WORKDIR /app
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    WB_SEED_PATH=/seed/goods.json \
    WB_APPLY_DELAY_SECONDS=3 \
    WB_QUARANTINE_PCT=30

COPY emulator_wb ./emulator_wb

RUN pip install --no-cache-dir fastapi "uvicorn[standard]" pydantic

EXPOSE 8090
CMD ["uvicorn", "emulator_wb.app.main:app", "--host", "0.0.0.0", "--port", "8090"]
