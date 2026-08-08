FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .

# Drop root. Installing as root and running as someone else is the point: the packages land
# in system site-packages (readable by all) while nothing the request path touches can write
# outside /app.
#
# UID 1000 named `user` is Hugging Face's own convention for Spaces — persistent storage, if
# it is ever attached, is mounted owned by that uid, so matching it now avoids a permission
# failure later. /app is chowned because this app writes tracker.sqlite3 next to main.py.
RUN useradd --create-home --uid 1000 user && chown -R user:user /app
USER user
ENV HOME=/home/user

EXPOSE 7860
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
