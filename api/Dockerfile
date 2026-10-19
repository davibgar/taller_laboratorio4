FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY telemetria.py app.py ./

EXPOSE 8000
# Un solo proceso de uvicorn (1 CPU asignada al contenedor). Sin log de acceso:
# a cientos de peticiones por segundo escribirlo costaría CPU que no es del CRUD.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
