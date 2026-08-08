FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py backlink_data.py ./

# Drop root — see the mail-tracker Space's Dockerfile for the same reasoning. UID 1000 named
# `user` is Hugging Face's convention for Spaces.
#
# HOME has to be set explicitly rather than left to inherit: Streamlit writes its config and
# cache under ~/.streamlit on first run, and a non-root user with no writable home fails at
# startup rather than at some later request.
RUN useradd --create-home --uid 1000 user && chown -R user:user /app
USER user
ENV HOME=/home/user

EXPOSE 8501

CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
