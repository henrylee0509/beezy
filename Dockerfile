FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 BP_DATA_DIR=/data
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY frontend ./frontend
COPY sample_docs ./sample_docs
COPY run.py .
VOLUME ["/data"]
EXPOSE 8000
CMD ["python", "run.py"]
