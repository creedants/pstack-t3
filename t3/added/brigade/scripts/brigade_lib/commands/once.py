import argparse
import fcntl
import fnmatch
import functools
import hashlib
import io
import json
import os
import posixpath
import re
import runpy
import shlex
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from brigade_lib.shared import BrigadeError


class OncePaths(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, "_paths_seen", False):
            raise BrigadeError("--paths given twice. Pass one comma-separated value.")
        namespace._paths_seen = True
        setattr(namespace, self.dest, values)
