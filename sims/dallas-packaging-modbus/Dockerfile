# Dallas packaging-line host Modbus scenario simulator.
# Build:  docker build -t dallas-packaging-modbus-sim:latest sims/dallas-packaging-modbus
# Run:    docker run -d --name dallas-packaging-modbus-sim -p 5021:5021 dallas-packaging-modbus-sim:latest
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY sim.py .
EXPOSE 5021
ENTRYPOINT ["python", "sim.py", "--host", "0.0.0.0", "--port", "5021"]
