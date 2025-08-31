from dataclasses import dataclass
from datetime import datetime
from typing import Generic, List, TypedDict, TypeVar, Union

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

    def set_firmwares_offset(self, offset: int) -> None:
        """
        reduces the firmwares by choping off the `offset` amount from the firmwares list starting at the oldest

        usefull if you don't want to deal with old firmwares nor check it at all
        """

        # firmwares are sorted from the API
        # 
        # old firmwares are at the end of the list
        if offset > 0:
            self.firmwares = self.firmwares[:-offset]


    @classmethod
    def from_dict_with_offset(cls, data: dict, offset: int) -> "Response":
        response = cls.from_dict(data)
        response.set_firmwares_offset(offset)

        return response

    @classmethod
    def from_dict(cls, data: dict) -> "Response":
        return cls(
            name=data["name"],
            identifier=data["identifier"],
            firmwares=[Firmware.from_dict(fw) for fw in data["firmwares"]],
            boardconfig=data["boardconfig"],
            platform=data["platform"],
            cpid=data["cpid"],
            bdid=data["bdid"],
        )
