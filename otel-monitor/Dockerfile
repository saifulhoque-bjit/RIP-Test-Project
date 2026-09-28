# The progCoder OTel monitor (monitor.py, copied unchanged), run in Docker for RIP.
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY monitor.py .
ENV MONITOR_HOST=0.0.0.0 MONITOR_PORT=7787 MONITOR_DATA_DIR=/data
EXPOSE 7787
CMD ["python", "monitor.py"]
