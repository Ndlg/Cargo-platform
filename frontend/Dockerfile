FROM node:20-alpine AS build

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

ARG BUILD_COMMAND=build:tenant
ARG DIST_DIR=dist

RUN npm run ${BUILD_COMMAND} \
    && mkdir -p /out \
    && cp -R "${DIST_DIR}/." /out/

FROM nginx:1.27-alpine

ENV API_PROXY_PASS=http://backend:8000 \
    SPA_FALLBACK=/index.html

COPY docker/nginx.conf.template /etc/nginx/templates/default.conf.template
RUN rm -rf /usr/share/nginx/html/*
COPY --from=build /out /usr/share/nginx/html

EXPOSE 80
