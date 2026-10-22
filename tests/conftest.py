import shutil
import subprocess
import tarfile
import textwrap
from pathlib import Path

import pytest

from scripts.config.loader import load_config

REPO = Path(__file__).resolve().parent.parent
needs_gcc = pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not available")


@pytest.fixture
def config(tmp_path):
    cfg = load_config(REPO / "config" / "config.yaml", {"artifact_base_path": str(tmp_path / "base")})
    cfg.defaults.build.env = {}  # keep fake builds independent from the real toolchain flags
    return cfg


def make_tarball(dest: Path, top: str, files: dict) -> Path:
    src = dest.parent / f"_{top}"
    shutil.rmtree(src, ignore_errors=True)
    for rel, content in files.items():
        p = src / top / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    with tarfile.open(dest, "w:gz") as tar:
        tar.add(src / top, arcname=top)
    return dest


def write_recipe(pkg_dir: Path, name: str, body: str) -> None:
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / f"{name}.yaml").write_text(textwrap.dedent(body))


@pytest.fixture
def fake_sources(tmp_path):
    """foo (static lib) and bar (app using foo) as local tarballs."""
    foo = make_tarball(tmp_path / "foo-1.0.tar.gz", "foo-1.0", {
        "foo.h": "int foo_answer(void);\n",
        "foo.c": "int foo_answer(void) { return 42; }\n",
    })
    bar = make_tarball(tmp_path / "bar-2.0.tar.gz", "bar-2.0", {
        "main.c": '#include <stdio.h>\n#include <foo.h>\nint main(void){printf("%d\\n", foo_answer());return 0;}\n',
    })
    return foo, bar


def sha(path: Path) -> str:
    from scripts.core.util import sha256_file
    return sha256_file(path)


@pytest.fixture
def fake_packages(tmp_path, fake_sources):
    foo, bar = fake_sources
    pkgs = tmp_path / "packages"
    write_recipe(pkgs, "foo", f"""
        version: 1.0.0
        schema: distillery-package
        package:
          name: foo
          kind: library
          summary: test lib
          license: MIT
          website: https://example.com
          source:
            url: "file://{foo}"
          versions:
            - version: "1.0"
              sha256: "{sha(foo)}"
              variants: [all]
          steps:
            - name: build
              run: |
                gcc $CFLAGS -c foo.c -o foo.o
                ar rcs libfoo.a foo.o
            - name: install
              run: |
                install -d "$DESTDIR$PREFIX/lib" "$DESTDIR$PREFIX/include"
                install -m644 libfoo.a "$DESTDIR$PREFIX/lib/"
                install -m644 foo.h "$DESTDIR$PREFIX/include/"
        """)
    write_recipe(pkgs, "bar", f"""
        version: 1.0.0
        schema: distillery-package
        package:
          name: bar
          kind: application
          summary: test app
          license: MIT
          website: https://example.com
          depends_on: [foo]
          source:
            url: "file://{bar}"
          versions:
            - version: "2.0"
              sha256: "{sha(bar)}"
              variants: [stable]
          steps:
            - name: build
              run: |
                gcc $CPPFLAGS $CFLAGS main.c -o bar $LDFLAGS -lfoo
            - name: install
              run: |
                install -D bar "$DESTDIR$PREFIX/bin/bar"
          smoke_tests:
            - run: |
                test "$("$ARTIFACT_ROOT/bin/bar")" = 42
        """)
    return pkgs
