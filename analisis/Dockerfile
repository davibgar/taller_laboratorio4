FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 MPLCONFIGDIR=/tmp/mpl
WORKDIR /app
RUN pip install requests==2.32.3 matplotlib==3.9.2 tzdata==2024.2
COPY analizar.py .
ENTRYPOINT ["python", "analizar.py"]
