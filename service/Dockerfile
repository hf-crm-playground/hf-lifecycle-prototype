# Trip Decision Service: one image for the hourly job, the nightly job and the trigger endpoint.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
WORKDIR /app
RUN pip install --no-cache-dir tzdata==2025.2 && useradd --create-home --uid 10001 nba
COPY src ./src
COPY sql ./sql
COPY examples ./examples
USER nba

# Default: the hourly job in dry-run. Override the command for nightly / serve.
#   docker run --env-file nba.env -v nba-data:/data IMAGE hourly --db /data/nba.db --from-klaviyo --usage /data/usage_daily.csv --mode dry_run
ENTRYPOINT ["python", "-m", "nba"]
CMD ["--help"]
