import json
from config import ALLOWED_USERS_FILE


class PermissionManager:
    """Manages user permissions for Yen Max."""

    def __init__(self, file_path: str = ALLOWED_USERS_FILE):
        self.file_path = file_path
        self._load()

    def _load(self) -> None:
        """Load allowed users from file."""
        try:
            with open(self.file_path, "r") as f:
                self.allowed_users = json.load(f)
        except FileNotFoundError:
            self.allowed_users = []
            self._save()
        except json.JSONDecodeError:
            self.allowed_users = []

    def _save(self) -> None:
        """Save allowed users to file."""
        try:
            with open(self.file_path, "w") as f:
                json.dump(self.allowed_users, f, indent=4)
        except IOError as e:
            print(f"Failed to save permissions: {e}")

    def is_allowed(self, user_id: int) -> bool:
        """Check if user has access."""
        return user_id in self.allowed_users

    def grant(self, user_id: int) -> bool:
        """Grant access to a user."""
        if user_id not in self.allowed_users:
            self.allowed_users.append(user_id)
            self._save()
            return True
        return False

    def revoke(self, user_id: int) -> bool:
        """Revoke access from a user."""
        if user_id in self.allowed_users:
            self.allowed_users.remove(user_id)
            self._save()
            return True
        return False

    def list_all(self) -> list:
        """Get list of all allowed users."""
        return self.allowed_users.copy()
