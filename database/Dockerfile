FROM postgis/postgis:15-3.4

RUN sed -i 's/http:/https:/g' /etc/apt/sources.list.d/pgdg.list || true \
    && sed -i 's/http:/https:/g' /etc/apt/sources.list || true \
    && apt-get update -o Acquire::Check-Valid-Until=false \
    && apt-get install -y postgresql-15-pgvector \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
