FROM python:3.12-slim

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# SQLite file lives here, on a named volume (see docker-compose.yml) so
# progress survives container restarts.
ENV GRADING_DB_PATH=/data/grading.db
VOLUME /data

EXPOSE 8000
# --no-access-log: uvicorn's own default access log is redundant with (and
# differently-formatted from) the structured "request" log line
# RequestIdMiddleware now emits (issue #72) -- and, until issue #63 moves
# the session token out of query strings, uvicorn's default format would
# log the full request path+query string (including ?token=...) at INFO
# level on every request. Disabling it closes that specific leak vector
# now, ahead of #63's own full fix.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
