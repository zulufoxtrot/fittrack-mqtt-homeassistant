FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY fittrack/ fittrack/

# BLE goes through the host's BlueZ daemon over the system D-Bus socket:
#   docker run -v /var/run/dbus:/var/run/dbus:ro --network host ...
CMD ["python", "-m", "fittrack"]
