import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from openeo_fastapi.client.psql.settings import DataBaseSettings

settings = DataBaseSettings()

os.chdir(Path(settings.ALEMBIC_DIR))
alembic_cfg = Config("alembic.ini")

command.upgrade(alembic_cfg, "head")
