FROM python:3.12-slim

WORKDIR /app

COPY services/waybill-parser/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY services/waybill-parser/service_app /app/service_app

ENV PYTHONPATH=/app
EXPOSE 8010

CMD ["uvicorn", "service_app.main:app", "--host", "0.0.0.0", "--port", "8010"]
