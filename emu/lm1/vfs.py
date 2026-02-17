"""Crystal OS Virtual Filesystem.

In-memory filesystem with directories, files, metadata, and MIME types.
All OS file operations go through the VFS — no direct host filesystem access.

Architecture:
  VFSNode       — base: name, parent, timestamps, permissions
  VFSFile       — content (bytes), MIME type
  VFSDirectory  — ordered children dict
  VFS           — path resolution, CRUD, traversal, default system structure

Path format: UNIX-style, '/' separator, absolute paths from root.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional, Iterator


# ===================================================================
# Filesystem nodes
# ===================================================================

class VFSNode:
    """Base class for filesystem nodes."""

    __slots__ = ('name', 'parent', 'created', 'modified', 'permissions')

    def __init__(self, name: str, parent: Optional[VFSDirectory] = None):
        self.name = name
        self.parent = parent
        now = time.time()
        self.created = now
        self.modified = now
        self.permissions = 0o755

    @property
    def path(self) -> str:
        """Absolute path of this node."""
        parts: list[str] = []
        node: VFSNode | None = self
        while node and node.name:
            parts.append(node.name)
            node = node.parent
        return '/' + '/'.join(reversed(parts)) if parts else '/'

    @property
    def is_dir(self) -> bool:
        return isinstance(self, VFSDirectory)

    @property
    def is_file(self) -> bool:
        return isinstance(self, VFSFile)

    @property
    def size(self) -> int:
        if isinstance(self, VFSFile):
            return len(self.content)
        if isinstance(self, VFSDirectory):
            return len(self.children)
        return 0

    @property
    def extension(self) -> str:
        """File extension (lowercase, without dot), or empty string."""
        if '.' in self.name:
            return self.name.rsplit('.', 1)[1].lower()
        return ''


class VFSFile(VFSNode):
    """A file with in-memory content."""

    __slots__ = ('content', 'mime_type')

    def __init__(self, name: str, parent: Optional[VFSDirectory] = None,
                 content: bytes = b'', mime_type: str = 'application/octet-stream'):
        super().__init__(name, parent)
        self.content = content
        self.mime_type = mime_type

    @property
    def text(self) -> str:
        """Content decoded as UTF-8."""
        return self.content.decode('utf-8', errors='replace')

    @text.setter
    def text(self, value: str) -> None:
        self.content = value.encode('utf-8')
        self.modified = time.time()


class VFSDirectory(VFSNode):
    """A directory containing child nodes."""

    __slots__ = ('children',)

    def __init__(self, name: str, parent: Optional[VFSDirectory] = None):
        super().__init__(name, parent)
        self.children: dict[str, VFSNode] = {}

    def add(self, node: VFSNode) -> VFSNode:
        """Add a child node."""
        node.parent = self
        self.children[node.name] = node
        self.modified = time.time()
        return node

    def remove(self, name: str) -> VFSNode | None:
        """Remove and return a child by name."""
        node = self.children.pop(name, None)
        if node:
            node.parent = None
            self.modified = time.time()
        return node

    def get(self, name: str) -> VFSNode | None:
        return self.children.get(name)

    def list_sorted(self) -> list[VFSNode]:
        """List children: directories first, then files, alphabetical within each."""
        dirs = sorted([n for n in self.children.values() if n.is_dir],
                      key=lambda n: n.name.lower())
        files = sorted([n for n in self.children.values() if n.is_file],
                       key=lambda n: n.name.lower())
        return dirs + files


# ===================================================================
# MIME type inference
# ===================================================================

_MIME_MAP: dict[str, str] = {
    'txt': 'text/plain',
    'md': 'text/markdown',
    'log': 'text/plain',
    'lisp': 'text/x-lisp',
    'lsp': 'text/x-lisp',
    'scm': 'text/x-lisp',
    'py': 'text/x-python',
    'js': 'text/javascript',
    'c': 'text/x-c',
    'h': 'text/x-c',
    'html': 'text/html',
    'css': 'text/css',
    'json': 'application/json',
    'xml': 'application/xml',
    'png': 'image/png',
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'bmp': 'image/bmp',
    'gif': 'image/gif',
    'svg': 'image/svg+xml',
    'wav': 'audio/wav',
    'app': 'application/x-crystal-app',
    'lmfont': 'application/x-lmfont',
    'conf': 'text/plain',
}


def mime_for_name(name: str) -> str:
    """Infer MIME type from filename extension."""
    ext = name.rsplit('.', 1)[1].lower() if '.' in name else ''
    return _MIME_MAP.get(ext, 'application/octet-stream')


# ===================================================================
# Virtual Filesystem
# ===================================================================

class VFS:
    """In-memory virtual filesystem.

    All paths are absolute (start with '/').  Path components are separated
    by '/'.  Trailing slashes are stripped.  '..' and '.' are resolved.
    """

    def __init__(self):
        self.root = VFSDirectory('')

    # ── Path resolution ───────────────────────────────────────────

    def _normalize(self, path: str) -> list[str]:
        """Normalize path to component list, resolving '.' and '..'."""
        parts: list[str] = []
        for p in path.strip('/').split('/'):
            if not p or p == '.':
                continue
            if p == '..':
                if parts:
                    parts.pop()
            else:
                parts.append(p)
        return parts

    def resolve(self, path: str) -> VFSNode | None:
        """Resolve an absolute path to a node, or None."""
        parts = self._normalize(path)
        node: VFSNode = self.root
        for part in parts:
            if not isinstance(node, VFSDirectory):
                return None
            child = node.children.get(part)
            if child is None:
                return None
            node = child
        return node

    def resolve_dir(self, path: str) -> VFSDirectory | None:
        """Resolve path to a directory, or None."""
        node = self.resolve(path)
        return node if isinstance(node, VFSDirectory) else None

    def resolve_file(self, path: str) -> VFSFile | None:
        """Resolve path to a file, or None."""
        node = self.resolve(path)
        return node if isinstance(node, VFSFile) else None

    def _resolve_parent(self, path: str) -> tuple[VFSDirectory, str] | None:
        """Resolve parent directory and leaf name, or None."""
        parts = self._normalize(path)
        if not parts:
            return None
        name = parts[-1]
        parent_parts = parts[:-1]
        node: VFSNode = self.root
        for part in parent_parts:
            if not isinstance(node, VFSDirectory):
                return None
            child = node.children.get(part)
            if child is None:
                return None
            node = child
        if not isinstance(node, VFSDirectory):
            return None
        return node, name

    # ── CRUD operations ───────────────────────────────────────────

    def exists(self, path: str) -> bool:
        return self.resolve(path) is not None

    def mkdir(self, path: str, parents: bool = False) -> VFSDirectory:
        """Create a directory. If parents=True, create intermediate dirs."""
        parts = self._normalize(path)
        node: VFSNode = self.root
        for i, part in enumerate(parts):
            if not isinstance(node, VFSDirectory):
                raise FileNotFoundError(f"Not a directory: {'/'.join(parts[:i])}")
            child = node.children.get(part)
            if child is None:
                if not parents and i < len(parts) - 1:
                    raise FileNotFoundError(
                        f"Parent directory missing: /{'/'.join(parts[:i+1])}")
                child = VFSDirectory(part, node)
                node.children[part] = child
            elif not isinstance(child, VFSDirectory):
                raise FileExistsError(f"Not a directory: /{'/'.join(parts[:i+1])}")
            node = child
        assert isinstance(node, VFSDirectory)
        return node

    def create_file(self, path: str, content: bytes | str = b'',
                    mime_type: str | None = None) -> VFSFile:
        """Create a file. Parent directory must exist."""
        result = self._resolve_parent(path)
        if result is None:
            raise FileNotFoundError(f"Cannot resolve parent: {path}")
        parent, name = result
        if name in parent.children:
            raise FileExistsError(f"Already exists: {path}")
        if isinstance(content, str):
            content = content.encode('utf-8')
        if mime_type is None:
            mime_type = mime_for_name(name)
        f = VFSFile(name, parent, content, mime_type)
        parent.children[name] = f
        return f

    def read(self, path: str) -> bytes:
        """Read file content."""
        f = self.resolve_file(path)
        if f is None:
            raise FileNotFoundError(path)
        return f.content

    def read_text(self, path: str) -> str:
        """Read file content as UTF-8 text."""
        return self.read(path).decode('utf-8', errors='replace')

    def write(self, path: str, data: bytes | str) -> VFSFile:
        """Write content to an existing file, or create it."""
        if isinstance(data, str):
            data = data.encode('utf-8')
        f = self.resolve_file(path)
        if f is not None:
            f.content = data
            f.modified = time.time()
            return f
        return self.create_file(path, data)

    def delete(self, path: str, recursive: bool = False) -> None:
        """Delete a file or empty directory. Use recursive=True for non-empty dirs."""
        node = self.resolve(path)
        if node is None:
            raise FileNotFoundError(path)
        if node is self.root:
            raise PermissionError("Cannot delete root")
        if isinstance(node, VFSDirectory) and node.children and not recursive:
            raise OSError(f"Directory not empty: {path}")
        if node.parent:
            node.parent.remove(node.name)

    def list_dir(self, path: str) -> list[VFSNode]:
        """List directory contents, sorted (dirs first)."""
        d = self.resolve_dir(path)
        if d is None:
            raise FileNotFoundError(path)
        return d.list_sorted()

    def stat(self, path: str) -> dict:
        """Return file/directory metadata."""
        node = self.resolve(path)
        if node is None:
            raise FileNotFoundError(path)
        result = {
            'name': node.name,
            'path': node.path,
            'is_dir': node.is_dir,
            'is_file': node.is_file,
            'size': node.size,
            'created': node.created,
            'modified': node.modified,
            'permissions': node.permissions,
        }
        if isinstance(node, VFSFile):
            result['mime_type'] = node.mime_type
        return result

    def copy(self, src: str, dst: str) -> VFSNode:
        """Copy a file to a new path."""
        node = self.resolve(src)
        if node is None:
            raise FileNotFoundError(src)
        if isinstance(node, VFSFile):
            return self.create_file(dst, node.content, node.mime_type)
        raise NotImplementedError("Directory copy not yet supported")

    def move(self, src: str, dst: str) -> None:
        """Move/rename a file or directory."""
        node = self.resolve(src)
        if node is None:
            raise FileNotFoundError(src)
        result = self._resolve_parent(dst)
        if result is None:
            raise FileNotFoundError(f"Cannot resolve destination parent: {dst}")
        dst_parent, dst_name = result
        # Remove from old parent
        if node.parent:
            node.parent.remove(node.name)
        # Add to new parent
        node.name = dst_name
        dst_parent.add(node)

    def walk(self, path: str = '/') -> Iterator[tuple[str, list[str], list[str]]]:
        """Walk the filesystem tree, yielding (dirpath, dirnames, filenames)."""
        d = self.resolve_dir(path)
        if d is None:
            return
        dirnames = [n.name for n in d.children.values() if n.is_dir]
        filenames = [n.name for n in d.children.values() if n.is_file]
        yield (d.path if d.path != '' else '/', dirnames, filenames)
        for dn in dirnames:
            child_path = f"{path.rstrip('/')}/{dn}"
            yield from self.walk(child_path)

    # ── Default filesystem ────────────────────────────────────────

    def populate_default(self) -> None:
        """Create the default Crystal OS filesystem structure."""

        # System directories
        self.mkdir('/system/fonts', parents=True)
        self.mkdir('/system/config', parents=True)
        self.mkdir('/system/icons', parents=True)

        # Applications
        self.mkdir('/applications', parents=True)
        for app in ['Terminal', 'Calculator', 'Clock', 'Editor',
                     'File Manager', 'Inspector', 'Control Panel']:
            self.create_file(
                f'/applications/{app}.app',
                f'(application :name "{app}" :type crystallite)\n',
                'application/x-crystal-app')

        # User directories
        self.mkdir('/users/default/desktop', parents=True)
        self.mkdir('/users/default/documents', parents=True)
        self.mkdir('/users/default/downloads', parents=True)
        self.mkdir('/users/default/projects', parents=True)

        # System config
        self.create_file('/system/config/desktop.conf',
            '; Crystal Desktop configuration\n'
            '(desktop\n'
            '  (resolution 1024 768)\n'
            '  (theme dark)\n'
            '  (taskbar-position bottom)\n'
            '  (double-click-ms 400))\n')

        self.create_file('/system/about.txt',
            'Crystal Desktop\n'
            'Version 1.0\n'
            '\n'
            'A modern Lisp machine operating system.\n'
            'Running on the LM-1 architecture.\n'
            '\n'
            '64-bit tagged pointers, hardware GC,\n'
            '64-tile parallel processing, message passing.\n')

        # Sample documents
        self.create_file('/users/default/documents/welcome.txt',
            'Welcome to Crystal OS\n'
            '=====================\n'
            '\n'
            'This is the Crystal Desktop environment, a modern\n'
            'operating system built on the LM-1 Lisp Machine.\n'
            '\n'
            'Try opening the Terminal and typing:\n'
            '  (+ 1 2 3)\n'
            '  (fact 10)\n'
            '\n'
            'Or open the Editor to write Lisp programs.\n')

        self.create_file('/users/default/documents/hello.lisp',
            ';; Hello World in Crystal Lisp\n'
            '\n'
            '(defun hello (name)\n'
            '  (print "Hello, ")\n'
            '  (print name)\n'
            '  (newline))\n'
            '\n'
            '(hello "World")\n')

        self.create_file('/users/default/documents/fibonacci.lisp',
            ';; Fibonacci sequence\n'
            '\n'
            '(defun fib (n)\n'
            '  (if (< n 2)\n'
            '      n\n'
            '      (+ (fib (- n 1))\n'
            '         (fib (- n 2)))))\n'
            '\n'
            ';; First 10 Fibonacci numbers\n'
            '(defun show-fibs (i max)\n'
            '  (if (<= i max)\n'
            '      (begin\n'
            '        (print (fib i))\n'
            '        (print " ")\n'
            '        (show-fibs (+ i 1) max))))\n'
            '\n'
            '(show-fibs 0 10)\n'
            '(newline)\n')

        self.create_file('/users/default/documents/notes.txt',
            'Project Notes\n'
            '=============\n'
            '\n'
            '- Implement parallel map using tile messaging\n'
            '- Test garbage collector under heavy allocation\n'
            '- Design actor supervision tree\n'
            '- Profile inline cache hit rates\n')

        self.create_file('/users/default/projects/scratch.lisp',
            ';; Scratch pad — experiment here\n'
            '\n'
            '(defun square (x) (* x x))\n'
            '\n'
            '(defun sum-squares (n)\n'
            '  (if (= n 0)\n'
            '      0\n'
            '      (+ (square n)\n'
            '         (sum-squares (- n 1)))))\n'
            '\n'
            '(sum-squares 10)  ; => 385\n')

        # Temp directory (empty)
        self.mkdir('/tmp', parents=True)
