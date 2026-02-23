FROM python:3.10

RUN apt-get update && apt upgrade -y
RUN apt-get install wget -y

WORKDIR /app

COPY requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "server:app", "--host", "0.0.0.0" ,"--port", "8000"]
