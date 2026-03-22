FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --upgrade pip && pip install -r /tmp/requirements.txt

COPY backend /app/backend
COPY assets /app/assets

WORKDIR /app/backend

EXPOSE 8001

ENV PUBLIC_UI_HOST=0.0.0.0
ENV PUBLIC_UI_PORT=8001

CMD ["uvicorn", "src.public_ui.main:app", "--host", "0.0.0.0", "--port", "8001"]
