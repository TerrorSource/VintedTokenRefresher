FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir requests curl_cffi
COPY refresh.py .
CMD ["python", "-u", "refresh.py"]
