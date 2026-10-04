FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home bot && mkdir /data && chown bot:bot /data
COPY --chown=bot:bot . .
USER bot
CMD ["python", "app.py", "run"]
