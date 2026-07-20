# Dallas Line-2 scenario coordinator — mirrors the Modbus sim's authoritative counts into the
# Kepware writable Live.* tags over OPC UA.
# Build: docker build -t dallas-scenario-coordinator:latest sims/dallas-scenario-coordinator
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY coordinator.py .
ENTRYPOINT ["python", "coordinator.py"]
