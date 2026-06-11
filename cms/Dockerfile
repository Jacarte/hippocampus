# Stage 1 — build with bun
FROM oven/bun:1-alpine AS builder

WORKDIR /app

COPY package.json bun.lock ./
RUN bun install --frozen-lockfile

COPY . .
RUN bun run build

# Stage 2 — serve with nginx
FROM nginx:alpine

ARG VERSION=dev
LABEL org.opencontainers.image.version="${VERSION}"

ENV MEM0_SERVER_URL=http://localhost:8000

# nginx-envsubst only substitutes variables matching this filter
ENV NGINX_ENVSUBST_FILTER=MEM0_SERVER_URL

COPY default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD wget --quiet --tries=1 --spider http://localhost:80/ || exit 1
