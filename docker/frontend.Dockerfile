# The React frontend: a production build served by nginx. Built by docker-compose.yml.
FROM node:24-slim AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund --loglevel=error
COPY frontend/ ./
# The browser calls the backend directly, so its address is baked into the build. Change it with
# JOURNAL_API_URL in docker-compose.yml, then rebuild.
ARG REACT_APP_API_URL=http://localhost:8010
ENV REACT_APP_API_URL=$REACT_APP_API_URL \
    GENERATE_SOURCEMAP=false
RUN npm run build

# Runs as a non-root user and listens on 8080.
FROM nginxinc/nginx-unprivileged:1.29-alpine
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/build /usr/share/nginx/html
EXPOSE 8080
