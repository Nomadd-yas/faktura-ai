# Сборка на сервере без сети: поверх последнего отправленного образа (faktura-ml:shipped) — только новый код.
# Контекст — каталог ml/pdf-dataset. Если поменялся requirements.txt — deploy.sh --ml с локальной машины.
FROM faktura-ml:shipped
COPY pipeline.py extract_dataset.py strip_tail.py figures.py glmocr.config.yaml api.py entrypoint.sh /app/
RUN chmod +x /app/entrypoint.sh
