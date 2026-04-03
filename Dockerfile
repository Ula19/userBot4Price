FROM python:3.12-slim

WORKDIR /app

# ставим зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# копируем код
COPY bot.py .
COPY price_parser.py .
COPY search.py .
COPY handlers.py .
COPY ai_parser.py .
COPY id_resolver.py .
COPY aliases.py .
COPY examples.py .

# запуск
CMD ["python3", "bot.py"]
