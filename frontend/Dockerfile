# -------- Stage 1: Dependencies --------
FROM node:20-alpine AS deps

WORKDIR /app

COPY package*.json ./
RUN npm ci --prefer-offline

# -------- Stage 2: Build --------
FROM node:20-alpine AS builder

WORKDIR /app

# Build-time env vars — Vite bakes these into the JS bundle at build time.
# Pass values via: docker build --build-arg VITE_API_BASE_URL=...
ARG VITE_API_BASE_URL
ARG VITE_CDN_URL
ARG VITE_ENV
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
ENV VITE_CDN_URL=${VITE_CDN_URL}
ENV VITE_ENV=${VITE_ENV}

COPY --from=deps /app/node_modules ./node_modules
COPY . .

RUN npm run build

# -------- Stage 3: Runtime --------
FROM nginx:1.27-alpine AS runner

RUN rm -rf /usr/share/nginx/html/*

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

RUN chown -R nginx:nginx /usr/share/nginx/html \
    && chmod -R 755 /usr/share/nginx/html \
    && mkdir -p /var/cache/nginx/client_temp \
                /var/cache/nginx/proxy_temp \
                /var/cache/nginx/fastcgi_temp \
                /var/cache/nginx/uwsgi_temp \
                /var/cache/nginx/scgi_temp \
    && chown -R nginx:nginx /var/cache/nginx \
    && chown -R nginx:nginx /var/log/nginx \
    && touch /var/run/nginx.pid \
    && chown nginx:nginx /var/run/nginx.pid \
    && sed -i '/^user /d' /etc/nginx/nginx.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
