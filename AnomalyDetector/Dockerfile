# Use Python 3.9 slim image as base
FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Install system dependencies for numpy, pandas, scikit-learn
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
    gfortran \
    libopenblas-dev \
    liblapack-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY anomaly_detector.py .

# Create directory for shared volume
RUN mkdir -p /fluent-bit/etc/shared

# Set Python path
ENV PYTHONPATH=/app

# Run the anomaly detector
ENTRYPOINT ["python", "anomaly_detector.py"]