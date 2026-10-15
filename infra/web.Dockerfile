# Front-end image: Next.js standalone output on Node 24 LTS.
FROM node:24-trixie-slim AS deps
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:24-trixie-slim AS build
WORKDIR /app/web
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/web/node_modules ./node_modules
COPY web/ ./
RUN npm run build

FROM node:24-trixie-slim AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
RUN useradd --create-home --uid 10001 web
COPY --from=build --chown=web /app/web/.next/standalone ./
COPY --from=build --chown=web /app/web/.next/static ./.next/static
COPY --from=build --chown=web /app/web/public ./public
USER web
EXPOSE 3000
CMD ["node", "server.js"]
