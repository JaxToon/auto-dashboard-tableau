# Build from the repository root:  docker build -f apps/api/Dockerfile .
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY apps/api/requirements.txt /srv/apps/api/requirements.txt
RUN pip install --no-cache-dir -r /srv/apps/api/requirements.txt "psycopg[binary]>=3.2"
COPY apps/api /srv/apps/api
COPY data /srv/data
RUN useradd --create-home --uid 10001 app && mkdir -p /srv/var && chown -R app /srv/var
USER app
WORKDIR /srv/apps/api
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
