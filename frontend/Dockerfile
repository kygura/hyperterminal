FROM node:20-bookworm-slim

ENV NEXT_TELEMETRY_DISABLED=1

WORKDIR /app/frontend

# Development-only container. Production frontend deploys to Vercel.

COPY frontend/package.json ./
RUN npm install --no-fund --no-audit

COPY frontend/ ./

EXPOSE 3000

CMD ["npm", "run", "dev", "--", "--hostname", "0.0.0.0", "--port", "3000"]
