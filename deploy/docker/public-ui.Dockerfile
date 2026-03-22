FROM node:22-alpine AS frontend-build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY frontend /app
RUN npm run build

FROM nginx:1.27-alpine
COPY deploy/docker/public-ui.nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend-build /app/dist /usr/share/nginx/html
EXPOSE 8001
CMD ["nginx", "-g", "daemon off;"]
