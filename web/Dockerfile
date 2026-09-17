# web/Dockerfile · TD-006 前端多阶段构建（推荐默认 D2）
# 阶段1：构建静态资源
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci || npm install
COPY . .
RUN npm run build

# 阶段2：Nginx 静态服务 + 反代 /api（含 WebSocket）
FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
