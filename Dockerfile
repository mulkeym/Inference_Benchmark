FROM node:22-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM python:3.12-slim
RUN useradd -m bench
WORKDIR /app
COPY pyproject.toml ./
COPY bench ./bench
RUN pip install --no-cache-dir . && mkdir -p /data && chown bench:bench /data
COPY --from=frontend /build/dist ./frontend/dist
USER bench
ENV DATA_DIR=/data PORT=8080
EXPOSE 8080
CMD ["python", "-m", "bench.main"]
