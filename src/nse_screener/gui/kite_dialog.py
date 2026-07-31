"""Kite Connect credential dialog.

Two separate things, deliberately kept apart:
  - the API key and secret, issued at developers.kite.trade, which are long-lived and
    stored in Windows Credential Manager;
  - the daily access token, which comes from Zerodha's own browser login flow.

This dialog handles the first. It never asks for a Zerodha password - that is typed at
zerodha.com, in the user's own browser, and only the resulting redirect URL comes back.
"""

from __future__ import annotations

import webbrowser

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from nse_screener.config import _keyring_set, kite_api_key, kite_api_secret, settings
from nse_screener.market.kite import build_login_url, check_token, complete_login


class KiteSettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Kite Connect API")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        blurb = QLabel(
            "Register an app at developers.kite.trade to get an API key and secret "
            "(₹500/month Connect plan).\n\n"
            "These are stored in Windows Credential Manager, never in this project. "
            "Your Zerodha password is never entered here - the daily login happens in "
            "your own browser."
        )
        blurb.setWordWrap(True)
        layout.addWidget(blurb)

        form = QFormLayout()
        self.api_key = QLineEdit(kite_api_key() or "")
        self.api_secret = QLineEdit(kite_api_secret() or "")
        self.api_secret.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("API key", self.api_key)
        form.addRow("API secret", self.api_secret)
        layout.addLayout(form)

        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        actions = QHBoxLayout()
        save = QPushButton("Save credentials")
        save.clicked.connect(self._save)
        actions.addWidget(save)

        self.login = QPushButton("Get today's access token...")
        self.login.clicked.connect(self._daily_login)
        actions.addWidget(self.login)

        check = QPushButton("Test connection")
        check.clicked.connect(self._check)
        actions.addWidget(check)
        actions.addStretch()
        layout.addLayout(actions)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)

        self._check()

    def _save(self) -> None:
        service = settings()["kite"]["keyring_service"]
        key, secret = self.api_key.text().strip(), self.api_secret.text().strip()
        if not key or not secret:
            self.status.setText("Enter both the API key and the secret.")
            return
        ok = _keyring_set(service, "api_key", key) and _keyring_set(service, "api_secret", secret)
        self.status.setText(
            "Saved to Windows Credential Manager."
            if ok
            else "Could not reach Credential Manager - set KITE_API_KEY and KITE_API_SECRET instead."
        )

    def _daily_login(self) -> None:
        url = build_login_url()
        if not url:
            self.status.setText("Save the API key first.")
            return
        webbrowser.open(url)

        from PyQt6.QtWidgets import QInputDialog

        redirect, ok = QInputDialog.getText(
            self, "Kite login", "Paste the full URL you were redirected to after logging in:"
        )
        if ok and redirect.strip():
            self.status.setText(complete_login(redirect.strip()).message)

    def _check(self) -> None:
        result = check_token()
        self.status.setText(result.message)
