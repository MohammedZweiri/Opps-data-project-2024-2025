FROM --platform=linux/amd64 python:3.12-slim

WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install dependencies first (for better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Initialize Playwright
RUN playwright install --with-deps chromium

# Copy application code
COPY app/ ./app/

# Create a volume for database persistence
VOLUME /app/instance

# Expose the port
EXPOSE 5000

# Run with gunicorn
CMD ["flask", "run", "--host=0.0.0.0", "--port=5000"]
