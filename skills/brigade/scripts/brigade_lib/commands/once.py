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


class Once(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        seen = getattr(namespace, "_once_seen", None)
        if seen is None:
            seen = set()
            setattr(namespace, "_once_seen", seen)
        if self.dest in seen:
            flag = option_string or f"--{self.dest}"
            hint = "lease id" if self.dest == "lease" else "comma-separated value"
            raise BrigadeError(f"{flag} given twice. Pass one {hint}.")
        seen.add(self.dest)
        setattr(namespace, self.dest, values)


OncePaths = Once
