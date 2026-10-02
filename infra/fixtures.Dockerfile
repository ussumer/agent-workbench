# Demo quote site and skill download resource.
#
# It is packaged so the compose network can reach it on the same address the
# execution sandbox will use, instead of depending on a developer's shell.
# Versions are pinned to the same ones the project locks.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FIXTURES_HOST=0.0.0.0 \
    FIXTURES_PORT=8088

WORKDIR /app

RUN pip install --no-cache-dir "fastapi==0.141.1" "uvicorn==0.53.0"

# Only the fixture data and the site implementation are needed.
COPY fixtures/ /app/fixtures/

EXPOSE 8088

CMD ["python", "/app/fixtures/site/server.py"]
