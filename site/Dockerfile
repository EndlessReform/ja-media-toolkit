# Stage 1: Build
FROM node:26-alpine AS build
WORKDIR /app
COPY site/package*.json ./
RUN npm install
COPY site/ .
RUN npm run build

# Stage 2: Serve
FROM caddy:2-alpine
COPY --from=build /app/dist /usr/share/caddy
COPY deploy/ja-media-services/observability/prometheus-targets.json.tmpl /usr/share/caddy/prometheus-targets.json
COPY site/Caddyfile /etc/caddy/Caddyfile
EXPOSE 80
