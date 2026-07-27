"""Screener.in credential dialog.

The password is typed into this dialog on the user's own machine and handed straight to
Windows Credential Manager via keyring. It is never written to the repo, never logged,
never echoed back to the UI, and never leaves the machine except in the login POST to
screener.in itself.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from nse_screener.stage2.screener_client import (
    ScreenerAuthError,
    ScreenerClient,
    screener_username,
    store_credentials,
)


class ScreenerLoginDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Screener.in login")
        self.setMinimumWidth(430)

        layout = QVBoxLayout(self)
        blurb = QLabel(
            "Sign in with your own Screener.in Premium account.\n\n"
            "Credentials are stored in Windows Credential Manager, not in this project. "
            "They are sent only to screener.in."
        )
        blurb.setWordWrap(True)
        layout.addWidget(blurb)

        form = QFormLayout()
        self.username = QLineEdit(screener_username() or "")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Email / username", self.username)
        form.addRow("Password", self.password)
        layout.addLayout(form)

        self.remember = QCheckBox("Remember on this machine (Windows Credential Manager)")
        self.remember.setChecked(True)
        layout.addWidget(self.remember)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._verify)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._buttons = buttons

    def _verify(self) -> None:
        username = self.username.text().strip()
        password = self.password.text()
        if not username or not password:
            self.status.setText("Enter both a username and a password.")
            return

        self.status.setText("Signing in...")
        self._buttons.setEnabled(False)
        try:
            client = ScreenerClient()
            client.login(username, password)
        except ScreenerAuthError as exc:
            self.status.setText(str(exc))
            self._buttons.setEnabled(True)
            return
        finally:
            self._buttons.setEnabled(True)

        if self.remember.isChecked() and not store_credentials(username, password):
            self.status.setText(
                "Signed in, but could not save to Credential Manager. "
                "Set SCREENER_USERNAME and SCREENER_PASSWORD instead."
            )
            return
        self.accept()
