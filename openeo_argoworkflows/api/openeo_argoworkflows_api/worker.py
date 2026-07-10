import os

from openeo_argoworkflows_api.settings import ExtendedAppSettings
from redis import Redis
from rq import Connection, Queue, Worker

settings = ExtendedAppSettings()

conn = Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT)

if __name__ == "__main__":
    with Connection(conn):
        worker = Worker(map(Queue, ["default"]))
        worker.work()
