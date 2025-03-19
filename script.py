import requests
from typing import List, Generic, TypeVar, Union
from dataclasses import dataclass
from datetime import datetime
from pprint import pprint
from pathlib import Path
from tqdm import tqdm


iphones_product_codes: List[str] = [
    "7,1",
    "7,2",

    "8,1",
    "8,2",
    "8,4",

    "9,1",
    "9,2",
    "9,3",
    "9,4",

    "10,1",
    "10,2",
    "10,4",
    "10,4",
    "10,5",
    "10,6",

    "11,2",
    "11,4",
    "11,6",
    "11,8",

    "12,1",
    "12,3",
    "12,5",
    "12,8",

    "13,1",
    "13,2",
    "13,3",
    "13,4",

    "14,2",
    "14,3",
    "14,4",
    "14,5",
    
    "14,6",
]

@dataclass
class Firmwares:
    identifier: str
    version: str
    buildid: str
    sha1sum: str
    md5sum: str
    filesize: int
    url: str
    releasedate: datetime
    uploaddate: datetime
    signed: bool

    @staticmethod
    def from_dict(data: dict) -> "Firmwares":
        return Firmwares(
            identifier=data["identifier"],
            version=data["version"],
            buildid=data["buildid"],
            sha1sum=data["sha1sum"],
            md5sum=data["md5sum"],
            filesize=data["filesize"],
            url=data["url"],
            releasedate=datetime.fromisoformat(data["releasedate"]),
            uploaddate=datetime.fromisoformat(data["uploaddate"]),
            signed=data["signed"]
        )

@dataclass
class Board:
    boardconfig: str
    platform: str
    cpid: int
    bdid: int

    @staticmethod
    def from_dict(data: dict) -> "Board":
        return Board(
            boardconfig=data["boardconfig"],
            platform=data["platform"],
            cpid=data["cpid"],
            bdid=data["bdid"]
        )

@dataclass
class Response:
    name: str
    identifier: str
    firmwares: List[Firmwares]
    boards: List[Board]
    boardconfig: str
    platform: str
    cpid: int
    bdid: int

    @staticmethod
    def from_dict(data: dict) -> "Response":
        return Response(
            name=data["name"],
            identifier=data["identifier"],
            firmwares=[Firmwares.from_dict(fw) for fw in data["firmwares"]],
            boards=[Board.from_dict(b) for b in data["boards"]],
            boardconfig=data["boardconfig"],
            platform=data["platform"],
            cpid=data["cpid"],
            bdid=data["bdid"]
        )

T = TypeVar("T")
E = TypeVar("E")

@dataclass
class Ok(Generic[T]):
    value: T

@dataclass
class Error(Generic[E]):
    error: E

Result = Union[Ok[T], Error[E]]

def download_file(firmwares: List[Firmwares]) -> Result[None, str]:
    for firmware in firmwares:
        file_name = f"{firmware.identifier.replace(',', '_')}-{firmware.version.replace('.', '_')}.ipsw"
        file_path = Path(file_name).resolve()

        try:
            with requests.get(firmware.url, stream=True, timeout=10) as response:
                if response.status_code != 200:
                    return Error(f"Failed to download {firmware.identifier}: {response.status_code} {response.reason}")

                total_size = int(response.headers.get("Content-Length", 0))

                with open(file_path, "wb") as file, tqdm(
                    total=total_size, unit="B", unit_scale=True, desc=file_name
                ) as progress:
                    for chunk in response.iter_content(chunk_size=8192):
                        file.write(chunk)
                        progress.update(len(chunk))

        except requests.exceptions.RequestException as e:
            file_path.unlink(missing_ok=True)  # remove partially downloaded file on error
            return Error(f"Network error: {e}")

    return Ok(None)

def bake_ipcc(response: "Response") -> Result[None, str]:
    download_file(response.firmwares)
    
    return Ok(None)

response = requests.get("https://api.ipsw.me/v4/device/iPhone8,1", params={"type": "ipsw"})

for iphone_code in iphones_product_codes:
    response = requests.get(f"https://api.ipsw.me/v4/device/iPhone{iphone_code}", params={"type": "ipsw"})

    if response.status_code == 200:
        parsed_data = Response.from_dict(response.json())
        bake_result = bake_ipcc(parsed_data)

        if isinstance(bake_result, Error):
            pprint(f"Error: {bake_result.error}")
            break
    else:
        print(f"something went wrong while baking iPhone{iphone_code}")
        print(f"error: {response.text}")
        break
