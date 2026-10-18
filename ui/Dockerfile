FROM node:22-alpine AS build
WORKDIR /ui
COPY ui/package.json /ui/package.json
RUN npm install
COPY ui/ /ui/
RUN npm run build

FROM nginx:1.27-alpine
COPY ui/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /ui/dist /usr/share/nginx/html
EXPOSE 80
