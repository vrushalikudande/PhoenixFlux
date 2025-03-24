# Use an official lightweight Python image
FROM python:3.10

# Set the working directory
WORKDIR /app

# Copy application files
COPY . .

# Install dependencies
RUN pip install fastapi psycopg2-binary uvicorn

# Default database URL environment variable (this will be set in Kubernetes)
ENV DATABASE_URL=""

# Run FastAPI app
CMD ["python", "app.py"]
