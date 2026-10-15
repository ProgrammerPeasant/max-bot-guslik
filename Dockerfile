FROM python:3.13-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY festpass ./festpass
RUN pip install --no-cache-dir .

COPY texts.toml ./
# lineup.toml и база монтируются снаружи, чтобы переживать пересборку
ENV FESTPASS_LINEUP=/data/lineup.toml \
    FESTPASS_DB=/data/festpass.db \
    FESTPASS_TEXTS=/app/texts.toml
VOLUME /data

CMD ["festbot", "run"]
