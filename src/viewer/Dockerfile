FROM node:22-alpine AS build

WORKDIR /app

COPY package.json ./
RUN npm install

COPY . ./

RUN npm run build

FROM node:22-alpine AS runtime

WORKDIR /app
ENV NODE_ENV=production
ENV HOST=0.0.0.0
ENV PORT=8081

COPY --from=build /app/.output ./.output

EXPOSE 8081
CMD ["node", ".output/server/index.mjs"]
