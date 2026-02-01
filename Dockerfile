# Используем официальный образ Python
FROM python:3.10-alpine

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем файлы зависимостей
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем исходный код
COPY . .

# Создаем папку для чеков
RUN mkdir -p receipts

# Запускаем бота
CMD ["python", "a.py"]
