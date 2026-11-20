# Сборка на сервере без сети: поверх последнего отправленного образа (faktura-backend:shipped) — только новый код.
# Контекст — каталог backend. Зависимости не переустанавливаются: если они поменялись — deploy.sh с локальной машины.
FROM faktura-backend:shipped
USER root
RUN rm -rf /usr/local/lib/python3.12/site-packages/faktura
COPY src/faktura /usr/local/lib/python3.12/site-packages/faktura
USER app
