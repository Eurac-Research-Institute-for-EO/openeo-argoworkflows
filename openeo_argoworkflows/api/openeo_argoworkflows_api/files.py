import datetime
import io
import json
import re
from os.path import splitext
from pathlib import Path
from typing import Optional

import fsspec
from fastapi import Depends
from fastapi import File as apiFile
from fastapi import Header, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from openeo_argoworkflows_api.auth import ExtendedAuthenticator
from openeo_argoworkflows_api.jobs import UserWorkspace
from openeo_fastapi.api.models import File, FilesGetResponse, Link
from openeo_fastapi.client.auth import User
from openeo_fastapi.client.files import FilesRegister
from pydantic import field_validator, model_validator
from pydantic.dataclasses import dataclass


# Resolve at call time so tests can patch ExtendedAuthenticator.validate.
def _validate_auth(authorization: str = Header()):
    return ExtendedAuthenticator.validate(authorization)


fs = fsspec.filesystem(protocol="file")


@dataclass
class ByteRange:
    start: int
    end: Optional[int]
    range: int = None

    @field_validator("start", mode="before")
    @classmethod
    def set_start(cls, v):
        if v is None:
            return 0
        return v

    @model_validator(mode="after")
    def set_range(self):
        if self.end is not None:
            if not self.start < self.end:
                raise ValueError(f"{self.end} must be greater than {self.start}")
            self.range = self.end - self.start + 1
        return self


class ArgoFileRegister(FilesRegister):
    def __init__(self, settings, links) -> None:
        super().__init__(settings, links)

    def compile_byte_ranges(self, byte_range: str) -> list[ByteRange]:
        """Take the byte range string and return list of ranges."""
        if not re.search(r"bytes=", byte_range):
            raise ValueError("Only byte ranges are currently supported, i.e. bytes=")

        r = r"\w+-\w+|\w+-|-\w+"
        bytes_eval = lambda a: int(a) if a != "" else None

        ranges = [
            ByteRange(
                start=bytes_eval(x.split("-")[0]), end=bytes_eval(x.split("-")[1])
            )
            for x in re.findall(r, byte_range)
        ]

        return ranges

    def validate_path(self, path: str, user: User):
        """Does the give path exist in the user workspace?
        If yes, return the absolute path."""

        # Get the user workspace
        user_workspace = self.settings.OPENEO_WORKSPACE_ROOT / str(user.user_id)
        # Does the file exist in the workspace?
        absolute_path = user_workspace / path
        try:
            fs.exists(absolute_path)
        except FileNotFoundError:
            raise HTTPException(
                status_code=404, detail="File not found in user workspace."
            )

        return absolute_path

    def file_header(
        self,
        path: str,
        user: User = Depends(ExtendedAuthenticator.signed_url_or_validate),
    ):
        """Get the headers for a file (or directory served as tar)."""

        absolute_path = self.validate_path(path, user)
        if fs.isfile(absolute_path):
            return Response(
                status_code=200,
                headers={
                    "Accept-Ranges": "bytes",
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(fs.size(absolute_path)),
                },
            )
        # Directory — served as a tar archive
        return Response(
            status_code=200,
            headers={
                "Content-Type": "application/x-tar",
            },
        )

    def download_file(
        self,
        path: str,
        request: Request,
        user: User = Depends(ExtendedAuthenticator.signed_url_or_validate),
    ):
        """
        Download a file from the workspace.
        Directories are streamed as tar archives.
        """

        def iterfile(path: Path, range: ByteRange = None):
            # 1024 * 1024 is roughly 1Mb * this by the number of Mb we want to try and serve
            chunk_size = (1024 * 1024) * 20

            with open(path, mode="rb") as file_like:
                if range:
                    file_like.seek(range.start)
                    if range.end:
                        yield file_like.read(range.range)
                    else:
                        while chunk := file_like.read(chunk_size):
                            yield chunk
                else:
                    while chunk := file_like.read(chunk_size):
                        yield chunk

        def tar_stream(path: Path):
            import tarfile

            chunk_size = (1024 * 1024) * 4
            buffer = io.BytesIO()
            tar = tarfile.open(mode="w", fileobj=buffer)
            try:
                tar.add(path, arcname=path.name)
            finally:
                tar.close()
            buffer.seek(0)
            while chunk := buffer.read(chunk_size):
                yield chunk

        absolute_path = self.validate_path(path, user)

        # Serve directories as tar archives
        if not fs.isfile(absolute_path):
            return StreamingResponse(
                status_code=200,
                content=tar_stream(absolute_path),
                media_type="application/x-tar",
                headers={
                    "Content-Disposition": f'attachment; filename="{path.replace("/", "_")}.tar"',
                },
            )

        # Set media type for the file
        extention = splitext(absolute_path)[1]
        mime_types = {
            ".tif": "image/tiff; application=geotiff; profile=cloud-optimized",
            ".tiff": "image/tiff; application=geotiff",
            ".nc": "application/netcdf",
            ".json": "application/json",
            ".geojson": "application/geo+json",
            ".csv": "text/csv",
            ".txt": "text/plain",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".xml": "application/xml",
            ".zip": "application/zip",
            ".gz": "application/gzip",
        }

        mime_type = mime_types.get(extention, "application/octet-stream")

        range_request = request.headers.get("Range")
        fsize = fs.size(absolute_path)

        if range_request:
            ranges = self.compile_byte_ranges(range_request)
            if len(ranges) != 1:
                raise HTTPException(
                    status_code=416, detail="multipart range request is not supported!"
                )
            else:
                range = ranges[0]
                if range.range and range.range > fsize:
                    # OpenEO Editor doesn't use pre-flight HEAD request, so sometimes a file might be smaller than the default
                    # 65536 byte request that is set by the editor.
                    range.end = fsize
                    range.range = range.end - range.start + 1

                if range.end is None:
                    range.end = fsize

                return StreamingResponse(
                    status_code=206,
                    content=iterfile(absolute_path, range),
                    media_type=mime_type,
                    headers={
                        "Content-Range": f"{range.start}-{range.end}/{fsize}",
                    },
                )
        else:
            return StreamingResponse(
                status_code=200, content=iterfile(absolute_path), media_type=mime_type
            )

    def list_files(self, limit: int = None, user: User = Depends(_validate_auth)):
        """
        List all files in the workspace
        """
        user_workspace = UserWorkspace(
            root_dir=self.settings.OPENEO_WORKSPACE_ROOT, user_id=str(user.user_id)
        )

        files = [
            File(
                # Path wants to be arelative to the files_directory
                path=str(file).removeprefix(str(user_workspace.files_directory))[1:],
                size=fs.size(file),
                modified=fs.modified(file),
            )
            for file in fs.ls(user_workspace.files_directory)
        ]

        if limit:
            files = files[:limit]

        return FilesGetResponse(
            files=files,
            links=[
                Link(
                    href="https://eodc.eu/",
                    rel="about",
                    type="text/html",
                    title="Homepage of the service provider",
                )
            ],
        )

    async def upload_file(
        self, path: str, request: Request, user: User = Depends(_validate_auth)
    ):
        space = UserWorkspace(
            root_dir=self.settings.OPENEO_WORKSPACE_ROOT, user_id=str(user.user_id)
        )

        split_path = [part for part in path.split("/") if "/" in path]

        if split_path:
            subdir = "".join([part + "/" for part in split_path[:-1] if part])
            upload_dir = space.files_directory / subdir
            if not fs.exists(upload_dir):
                fs.mkdir(upload_dir)

        try:
            upload_dest = space.files_directory / path

            form_data = await request.form()

            if len(form_data) == 0:
                form_data = None
                request_body = await request.body()

                with open(upload_dest, "wb") as f:
                    f.write(request_body)

            else:
                for file in form_data.values():
                    with open(upload_dest, "wb") as f:
                        while contents := file.file.read(1024 * 1024):
                            f.write(contents)

            size_bytes = fs.stat(upload_dest)["size"]
            # Formatted for RFC3339
            modified_time = (
                datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[
                    :-4
                ]
                + "Z"
            )

        except Exception:
            raise HTTPException(
                status_code=500,
                detail="The server encountered an error trying to upload the file.",
            )

        return Response(
            status_code=200,
            content=json.dumps(
                {"path": path, "size": size_bytes, "modified": modified_time}
            ),
        )

    async def delete_file(self, path: str, user: User = Depends(_validate_auth)):
        space = UserWorkspace(
            root_dir=self.settings.OPENEO_WORKSPACE_ROOT, user_id=str(user.user_id)
        )

        absolute_path = space.files_directory / path

        if fs.exists(absolute_path):
            fs.rm_file(absolute_path)

            return Response(
                status_code=204,
                content="The file has been successfully deleted at the back-end.",
            )
        return Response(status_code=404, content="File not found.")
