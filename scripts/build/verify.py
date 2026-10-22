"""
Post-build checks: is every shipped executable really static?
"""

import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

PT_INTERP = 3
PT_DYNAMIC = 2


@dataclass(frozen=True)
class ElfInfo:
    bits: int
    has_interp: bool
    has_dynamic: bool

    @property
    def is_static(self) -> bool:
        """No program interpreter => runs without ld.so (static or static-pie)."""
        return not self.has_interp


def read_elf(path: Path) -> Optional[ElfInfo]:
    """Parse ELF program headers; None when the file is not an ELF."""
    try:
        with open(path, "rb") as f:
            ident = f.read(16)
            if len(ident) < 16 or ident[:4] != b"\x7fELF":
                return None
            bits = 64 if ident[4] == 2 else 32
            end = "<" if ident[5] == 1 else ">"
            if bits == 64:
                f.seek(0x20)
                (phoff,) = struct.unpack(end + "Q", f.read(8))
                f.seek(0x36)
                phentsize, phnum = struct.unpack(end + "HH", f.read(4))
            else:
                f.seek(0x1C)
                (phoff,) = struct.unpack(end + "I", f.read(4))
                f.seek(0x2A)
                phentsize, phnum = struct.unpack(end + "HH", f.read(4))

            types = set()
            for i in range(phnum):
                f.seek(phoff + i * phentsize)
                (p_type,) = struct.unpack(end + "I", f.read(4))
                types.add(p_type)
            return ElfInfo(bits, PT_INTERP in types, PT_DYNAMIC in types)
    except (OSError, struct.error):
        return None


def iter_files(root: Path):
    for dirpath, _dirs, files in os.walk(root):
        for name in sorted(files):
            p = Path(dirpath) / name
            if not p.is_symlink() and p.is_file():
                yield p


def find_dynamic_executables(root: Path) -> List[str]:
    """Executable ELF files under ``root`` that need a dynamic loader."""
    offenders = []
    for p in iter_files(root):
        if not os.access(p, os.X_OK) or p.suffix in (".so", ".a") or ".so." in p.name:
            continue
        info = read_elf(p)
        if info and not info.is_static:
            offenders.append(str(p.relative_to(root)))
    return offenders


def find_shared_objects(root: Path) -> List[str]:
    return [str(p.relative_to(root)) for p in iter_files(root)
            if p.suffix == ".so" or ".so." in p.name]
