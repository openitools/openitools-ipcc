from dataclasses import dataclass
from datetime import datetime
from typing import Generic, List, TypedDict, TypeVar, Union

from config import cfg

T = TypeVar("T")
E = TypeVar("E")


class BundleMetadata(TypedDict):
    bundle_name: str
    sha1: str
    file_size: int
    created_at: str


@dataclass
class Ok(Generic[T]):
    value: T


@dataclass
class Error(Generic[E]):
    error: E


Result = Union[Ok[T], Error[E]]


@dataclass
class Firmware:
    identifier: str
    version: str
    buildid: str
    sha1sum: str
    md5sum: str
    filesize: int
    url: str
    releasedate: datetime | None
    uploaddate: datetime | None
    signed: bool

    # a state of whether this firmware was ignored in the repo metadata
    was_ignored: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "Firmware":
        return cls(
            identifier=data["identifier"],
            version=data["version"],
            buildid=data["buildid"],
            sha1sum=data["sha1sum"],
            md5sum=data["md5sum"],
            filesize=data["filesize"],
            url=data["url"],
            releasedate=datetime.fromisoformat(data["releasedate"])
            if data.get("releasedate")
            else None,
            uploaddate=datetime.fromisoformat(data["uploaddate"])
            if data.get("uploaddate")
            else None,
            signed=data["signed"],
        )


@dataclass
class Response:
    name: str
    identifier: str
    firmwares: List[Firmware]
    boardconfig: str
    platform: str
    cpid: int
    bdid: int

    def set_firmwares_skip(self, offset: int) -> None:
        """
        reduces the firmwares by choping off the `offset` amount from the firmwares list starting at the oldest

        usefull if you don't want to deal with old firmwares nor check it at all
        """

        # firmwares are sorted from the API
        #
        # old firmwares are at the end of the list
        if offset > 0:
            self.firmwares = self.firmwares[:-offset]

    def set_min_firmware(self, oldest: int | None) -> None:
        if oldest is None:
            return
        self.firmwares = [
            f for f in self.firmwares if int(f.version.split(".")[0]) >= oldest
        ]

    @classmethod
    def from_dict(cls, data: dict) -> "Response":
        response = cls(
            name=data["name"],
            identifier=data["identifier"],
            firmwares=[Firmware.from_dict(fw) for fw in data["firmwares"]],
            boardconfig=data["boardconfig"],
            platform=data["platform"],
            cpid=data["cpid"],
            bdid=data["bdid"],
        )

        response.set_firmwares_skip(cfg.firmware_skip)
        response.set_min_firmware(cfg.min_firmware)

        if cfg.product is not None:
            response.firmwares = [
                f for f in response.firmwares if f.version == cfg.product
            ]

        return response
