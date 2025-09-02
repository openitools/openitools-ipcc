import argparse
from dataclasses import dataclass
from enum import Enum, auto


class _ProxyTarget(Enum):
    Scraping = auto()
    IPSW = auto()
    All = auto()

    def is_ipsw_or_all(self) -> bool:
        return self.is_ipsw() or self.is_all()

    def is_scraping_or_all(self) -> bool:
        return self.is_scrape() or self.is_all()

    def is_scrape(self) -> bool:
        return self.name == "Scraping"

    def is_ipsw(self) -> bool:
        return self.name == "IPSW"

    def is_all(self) -> bool:
        return self.name == "All"

    @classmethod
    def from_str(cls, value: str) -> "_ProxyTarget":
        match value:
            case "scraping":
                return cls.Scraping
            case "ipsw":
                return cls.IPSW
            case "all":
                return cls.All
            case _:
                return cls.All


@dataclass
class _Config:
    upload_github: bool

    http_proxy: str | None
    proxy_target: _ProxyTarget

    jobs: int

    firmware_skip: int
    product_skip: int

    product: str | None
    firmware: str | None

    min_firmware: int | None
    max_firmware: int | None

    retry_ignored: bool
    reprocess: bool

    @staticmethod
    def add_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
        parser.add_argument(
            "-g",
            "--upload-github",
            help="Upload processed files to GitHub (make sure Git is configured).",
            action="store_true",
            default=False,
        )

        parser.add_argument(
            "--http-proxy",
            help="Proxy URL for all HTTP requests.",
            type=str,
            default=None,
        )

        parser.add_argument(
            "--proxy-target",
            choices=["scraping", "ipsw", "all"],
            help="Where to apply the proxy (scraping, ipsw, or all)",
            default="all",
        )

        parser.add_argument(
            "-j",
            "--jobs",
            help="Number of concurrent firmware extraction jobs (default: 3).",
            type=int,
            default=3,
        )

        parser.add_argument(
            "--firmware-skip",
            help="Skip the first N firmwares for each product (default: 0).",
            type=int,
            default=0,
        )

        parser.add_argument(
            "--product-skip",
            help="Skip the first N devices for both iPhones and iPads (default: 0).",
            type=int,
            default=0,
        )

        parser.add_argument(
            "--min-firmware",
            help="Check only firmwares from this version upwards (e.g., 12).",
            type=int,
            default=None,
        )

        parser.add_argument(
            "--max-firmware",
            help="Check only firmwares from this version downwards (e.g., 18).",
            type=int,
            default=None,
        )

        parser.add_argument(
            "--product",
            help="Process only this product (e.g., iPhone9,1). Defaults to all products.",
            type=str,
            default=None,
        )

        parser.add_argument(
            "--firmware",
            help="Process only this firmware version (e.g., 18.0). Defaults to all.",
            type=str,
            default=None,
        )

        parser.add_argument(
            "--retry-ignored",
            help="Retry processing firmwares that were previously ignored.",
            action="store_true",
            default=False,
        )

        parser.add_argument(
            "--reprocess",
            help="Process everything all over again even if already done (usefull with the --product and --firmware)",
            action="store_true",
            default=False,
        )

        return parser

    @classmethod
    def new_from_args(cls):
        parser = argparse.ArgumentParser("OpeniTools-IPCC")

        parser = cls.add_arguments(parser)

        args = parser.parse_args()

        return cls(
            args.upload_github,
            args.http_proxy,
            _ProxyTarget.from_str(args.proxy_target),
            args.jobs,
            args.firmware_skip,
            args.product_skip,
            args.product,
            args.firmware,
            args.min_firmware,
            args.max_firmware,
            args.retry_ignored,
            args.reprocess,
        )


cfg = _Config.new_from_args()
