FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY robo.py .
ENV TZ=America/Sao_Paulo PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8080
CMD ["python", "robo.py"]
