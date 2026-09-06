# Separate image so the primary ingestion path never pays for numpy/scikit-learn unless ML is
# enabled (docs/ml-strategy.md — measured image-size comparison in docs/scalability.md).
FROM python:3.12-slim AS builder
WORKDIR /build
RUN pip install --no-cache-dir --upgrade pip
COPY pyproject.toml README.md ./
COPY uli ./uli
RUN pip install --no-cache-dir --prefix=/install ".[ml]"

FROM python:3.12-slim AS runtime
RUN groupadd -r uli && useradd -r -g uli -d /app uli
COPY --from=builder /install /usr/local
WORKDIR /app
COPY uli ./uli
COPY pyproject.toml README.md ./
RUN mkdir -p /app/models && chown -R uli:uli /app
USER uli
ENV ULI_ML_MODELS_DIR=/app/models
EXPOSE 8090
CMD ["python", "-m", "uli.ml.service"]
