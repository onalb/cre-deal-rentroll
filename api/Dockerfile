FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    CRE_SKILLS_DIR=/app/skills

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY api/cre_rentroll /app/cre_rentroll
COPY skills /app/skills

EXPOSE 8000
CMD ["uvicorn", "cre_rentroll.main:app", "--host", "0.0.0.0", "--port", "8000"]
