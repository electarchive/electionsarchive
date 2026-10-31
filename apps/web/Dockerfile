FROM node:26-trixie-slim AS base
RUN npm install --global pnpm@12.11.2
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml tsconfig.json ./
COPY apps/web/package.json apps/web/
COPY packages/analytics/package.json packages/analytics/
COPY packages/db/package.json packages/db/
COPY packages/engine/package.json packages/engine/
COPY packages/view-spec/package.json packages/view-spec/

FROM base AS build
RUN pnpm install --frozen-lockfile --filter "@ea/web..."
COPY packages packages
COPY apps/web apps/web
RUN DATABASE_URL=postgres://build@localhost/build BETTER_AUTH_SECRET=build BETTER_AUTH_URL=http://localhost RESEND_API_KEY=build \
  REDIS_URL=redis://localhost BASE_URL=http://localhost NODE_OPTIONS=--max-old-space-size=5000 pnpm --filter @ea/web build

FROM base
ENV NODE_ENV=production
RUN pnpm install --frozen-lockfile --prod --ignore-scripts --filter "@ea/web..."
COPY packages packages
COPY --from=build /app/apps/web/build apps/web/build
WORKDIR /app/apps/web
USER node
ENV PORT=3000 BODY_SIZE_LIMIT=200M
EXPOSE 3000
CMD ["node", "build/index.js"]
