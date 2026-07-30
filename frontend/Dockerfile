# 前端镜像：多阶段构建，产物交给 nginx 托管
# 默认配置为兜底，生产建议由 compose 挂载 deploy/nginx.conf 覆盖（冲突声明 C3）
FROM node:20-alpine AS builder

WORKDIR /build
COPY package.json ./
# 内网环境可替换为私有 npm 源：npm config set registry <url>
RUN npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine

COPY --from=builder /build/dist /usr/share/nginx/html
# 兜底 SPA 配置（无 api 上游时仅静态页可用）
RUN printf 'server {\n  listen 80;\n  root /usr/share/nginx/html;\n  location / { try_files $uri $uri/ /index.html; }\n}\n' \
    > /etc/nginx/conf.d/default.conf

EXPOSE 80
