"""User-provided credentials stay in the OS keychain, never in SQLite or logs."""
from __future__ import annotations

import hashlib
from pathlib import Path


class CredentialVault:
    def __init__(self, data_root: Path):
        self.service = 'LiveCut-' + hashlib.sha256(str(data_root.resolve()).encode()).hexdigest()[:20]

    def _backend(self):
        try:
            import keyring
            # Reject plaintext third-party fallbacks, including keyrings.alt.
            backend = keyring.get_keyring()
            module = type(backend).__module__
            if not module.startswith(('keyring.backends.macOS', 'keyring.backends.Windows', 'keyring.backends.SecretService')):
                raise RuntimeError('系统安全存储不可用，请启用系统钥匙串或凭据管理器')
            return backend
        except ImportError:
            raise RuntimeError('请安装桌面依赖以启用 API Key 安全存储') from None

    def get(self, name: str) -> str:
        try:
            return self._backend().get_password(self.service, name) or ''
        except RuntimeError:
            return ''

    def set(self, name: str, value: str) -> None:
        self._backend().set_password(self.service, name, value)

    def delete(self, name: str) -> None:
        backend = self._backend()
        if backend.get_password(self.service, name):
            backend.delete_password(self.service, name)
