# Stage 1: Build React Frontend
FROM node:20-alpine AS frontend-builder

# Set working directory for frontend
WORKDIR /app/frontend

# Copy frontend package files and install dependencies
COPY frontend/package.json ./
COPY frontend/package-lock.json ./
# If you use yarn or pnpm, adjust accordingly (e.g., copy yarn.lock or pnpm-lock.yaml and use yarn install or pnpm install)
RUN npm install

# Copy the rest of the frontend source code
COPY frontend/ ./

# Build the frontend
RUN npm run build

# Stage 2: Python Backend (OSS, no LangGraph license required)
FROM python:3.11-slim AS backend

# Install OS tools (curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Copy built frontend
COPY --from=frontend-builder /app/frontend/dist /app/frontend/dist

# Copy backend source
WORKDIR /app/backend
ADD backend/ /app/backend

# Install Python runtime dependencies
RUN pip install --no-cache-dir -U pip setuptools wheel
RUN pip install --no-cache-dir \
    "langgraph>=0.2.6" \
    "langchain>=0.3.19" \
    "langchain-google-genai" \
    "python-dotenv>=1.0.1" \
    "langgraph-sdk>=0.1.57" \
    "langgraph-cli" \
    "fastapi" \
    "uvicorn[standard]>=0.30.0" \
    "google-genai"

# Expose app src on PYTHONPATH and run from src
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend/src
WORKDIR /app/backend/src

# Default command: run server
CMD ["uvicorn", "agent.server:app", "--host", "0.0.0.0", "--port", "8000"]
