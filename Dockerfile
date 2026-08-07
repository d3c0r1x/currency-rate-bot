FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# docker run -e CURRENCY_BOT_TOKEN=... -e CURRENCY_DEMO_MODE=1 ...
CMD ["python", "bot.py"]
