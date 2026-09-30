"""Adapters turn a user command into a Task. They do not run it."""

from pco.adapters.blender import BlenderAdapter
from pco.adapters.compiler import CompilerAdapter
from pco.adapters.ffmpeg import FFmpegAdapter
from pco.adapters.generic import GenericAdapter

ADAPTERS = {
    "generic": GenericAdapter(),
    "compiler": CompilerAdapter(),
    "ffmpeg": FFmpegAdapter(),
    "blender": BlenderAdapter(),
}
