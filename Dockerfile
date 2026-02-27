FROM selenium/standalone-chrome:4.24.0

USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip supervisor && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/selgrid
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080 4444
CMD ["supervisord", "-c", "/opt/selgrid/supervisord.conf"]
