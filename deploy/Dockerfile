FROM node:22-alpine AS uibuild
WORKDIR /ui
COPY ui/package.json /ui/package.json
RUN npm install
COPY ui/ /ui/
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PORT=8080

RUN apt-get update && apt-get install -y --no-install-recommends curl nginx \
    && rm -rf /var/lib/apt/lists/*

COPY api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY api/cre_rentroll /app/cre_rentroll
COPY gold_eval /app/gold_eval
COPY --from=uibuild /ui/dist /usr/share/nginx/html
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
RUN rm -f /etc/nginx/sites-enabled/default

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD curl -fsS http://127.0.0.1:8080/api/health || exit 1
CMD ["sh", "-c", "uvicorn cre_rentroll.main:app --host 127.0.0.1 --port 8000 & exec nginx -g 'daemon off;'"]
