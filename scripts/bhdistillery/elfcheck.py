"""Static-linking check: a binary declared `static` must not ask for a dynamic loader (no PT_INTERP, no DT_NEEDED)."""

import struct
from pathlib import Path

PT_DYNAMIC, PT_INTERP = 2, 3
DT_NULL, DT_NEEDED = 0, 1


class ElfError(Exception):
    pass


def dynamic_dependencies(path):
    """(interpreter or "", [needed libraries]) of an ELF file. Both empty = linked statically."""
    data = Path(path).read_bytes()
    if data[:4] != b"\x7fELF":
        raise ElfError(f"{path}: not an ELF file")
    bits64 = data[4] == 2
    end = "<" if data[5] == 1 else ">"
    if bits64:
        phoff, = struct.unpack_from(end + "Q", data, 0x20)
        phentsize, phnum = struct.unpack_from(end + "HH", data, 0x36)
    else:
        phoff, = struct.unpack_from(end + "I", data, 0x1C)
        phentsize, phnum = struct.unpack_from(end + "HH", data, 0x2A)
    interp, dynamic, loads = "", None, []
    for i in range(phnum):
        base = phoff + i * phentsize
        if bits64:
            ptype, _, off, vaddr, _, filesz, _, _ = struct.unpack_from(end + "IIQQQQQQ", data, base)
        else:
            ptype, off, vaddr, _, filesz, _, _, _ = struct.unpack_from(end + "IIIIIIII", data, base)
        if ptype == PT_INTERP:
            interp = data[off:off + filesz].split(b"\0")[0].decode(errors="replace")
        elif ptype == PT_DYNAMIC:
            dynamic = (off, filesz)
        elif ptype == 1:
            loads.append((vaddr, off, filesz))
    needed = []
    if dynamic:
        off, size = dynamic
        entry = 16 if bits64 else 8
        fmt = end + ("qQ" if bits64 else "iI")
        offsets, strtab = [], None
        for pos in range(off, off + size, entry):
            tag, val = struct.unpack_from(fmt, data, pos)
            if tag == DT_NULL:
                break
            if tag == DT_NEEDED:
                offsets.append(val)
            elif tag == 5:                                      # DT_STRTAB: a virtual address
                strtab = val
        if offsets and strtab is not None:
            for vaddr, foff, fsz in loads:
                if vaddr <= strtab < vaddr + fsz:
                    base = foff + strtab - vaddr
                    needed = [data[base + o:].split(b"\0")[0].decode(errors="replace") for o in offsets]
                    break
    return interp, needed


def check_static(prefix, binaries):
    """Problems (list of strings) among `binaries`, paths relative to the prefix; empty = all static."""
    problems = []
    for rel in binaries:
        path = Path(prefix) / rel
        if not path.is_file():
            problems.append(f"{rel}: not found")
            continue
        try:
            interp, needed = dynamic_dependencies(path)
        except ElfError as exc:
            problems.append(str(exc))
            continue
        if interp or needed:
            problems.append(f"{rel}: dynamically linked ({interp or 'no interpreter'}; needs {', '.join(needed) or 'nothing'})")
    return problems
