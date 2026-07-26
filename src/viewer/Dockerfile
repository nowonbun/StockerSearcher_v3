FROM node:22-alpine AS build

WORKDIR /app

COPY package.json ./
# npm 10's peer-dependency resolver can fail with an Arborist `edgesOut`
# null-reference error for this dependency graph.  The viewer has no
# application-level peer override, so use legacy peer resolution during image
# construction until a committed lockfile is introduced.
RUN npm install --legacy-peer-deps --no-audit --no-fund

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
