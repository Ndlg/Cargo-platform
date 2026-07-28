FROM python:3.12-slim

WORKDIR /app

COPY services/ai-recognition/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY services/ai-recognition/service_app /app/service_app

ENV PYTHONPATH=/app
EXPOSE 8011

CMD ["uvicorn", "service_app.main:app", "--host", "0.0.0.0", "--port", "8011"]
