FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

LABEL org.opencontainers.image.title="Tippy" \
      org.opencontainers.image.description="Community economy in USDC on Base: tips, Polymarket-style on-chain markets, x402" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/ssrjkk/Tippy-on-base"
ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd -r tipbot && useradd -r -g tipbot tipbot

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chown -R tipbot:tipbot /app && chmod +x deploy/entrypoint.sh

USER tipbot

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/python", "/app/deploy/entrypoint.py"]
