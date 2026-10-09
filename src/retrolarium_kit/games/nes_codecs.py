"""Every game's NES resource codecs, registered with platform/nes/resources.

The platform imports this module (its CODEC_PROVIDERS) the first time a
profile needs a codec, so a game's codecs are available wherever the bundle
is used without the platform importing any game.
"""

from retrolarium_kit.games.castle import codecs as _castle  # noqa: F401
from retrolarium_kit.games.doraemon import codecs as _doraemon  # noqa: F401
from retrolarium_kit.games.pacman import codecs as _pacman  # noqa: F401
from retrolarium_kit.games.smb import codecs as _smb  # noqa: F401
