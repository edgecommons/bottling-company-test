# Standalone image for developing/testing the Dallas filling sim in isolation. In the harness the
# sim.py is instead copied into the edge-node image and run from a dedicated /opt/simenv venv
# (see dockerfiles/edge-node.Dockerfile) alongside the filling line's adapters.
# Build: docker build -t dallas-filling-sim:latest sims/dallas-filling-sim
# Run:   docker run --rm -p 4840:4840 -p 5020:5020 dallas-filling-sim:latest
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY sim.py .
EXPOSE 4840 5020
ENTRYPOINT ["python", "sim.py"]
