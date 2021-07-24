import os
from os import path


def find_ffmpeg():
    ffmpeg = path.abspath(path.join(path.dirname(__file__), 'ffmpeg', 'ffmpeg'))
    if os.access(ffmpeg, os.F_OK):
        return ffmpeg
    ffmpeg = path.abspath(path.join(path.dirname(__file__), 'ffmpeg', 'ffmpeg.exe'))
    if os.access(ffmpeg, os.F_OK):
        return ffmpeg
    return 'ffmpeg'  # general system ffmpeg
