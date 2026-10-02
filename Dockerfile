FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 RECEIPT_CONFIG=/config/server.toml
WORKDIR /app
COPY pyproject.toml requirements-server.lock ./
COPY receipt ./receipt
RUN pip install --no-cache-dir -r requirements-server.lock \
    && pip install --no-cache-dir --no-deps . \
    && useradd --uid 10001 --create-home receipt \
    && mkdir /data && chown receipt:receipt /data
USER receipt
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
CMD ["receipt-server"]

