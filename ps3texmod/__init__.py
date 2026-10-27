"""ps3texmod - texture modding toolkit for Prototype (PS3) Cement/RCF archives.

Pure standard library. No third-party dependencies.
"""

__version__ = "1.0.0"
FORMAT_NAME = "ps3texmod"

from .rz import is_rz, unpack_rz, pack_rz  # noqa: F401
from .dds import DdsInfo, parse_dds, build_png  # noqa: F401
from .rcf import RcfArchive, RcfEntry, RcfError  # noqa: F401
