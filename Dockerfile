FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app


# --------------------------------------------------
# SYSTEM DEPENDENCIES
# --------------------------------------------------

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        build-essential \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*


# --------------------------------------------------
# PYTHON DEPENDENCIES
# --------------------------------------------------

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt


# --------------------------------------------------
# APPLICATION SOURCE
# --------------------------------------------------

COPY . .


# --------------------------------------------------
# APPLICATION
# --------------------------------------------------

EXPOSE 8000

CMD ["uvicorn", "transport.app:app", "--host", "0.0.0.0", "--port", "8000"]