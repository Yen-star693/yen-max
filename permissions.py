import json
import os
import tempfile
from config import ALLOWED_USERS_FILE


class PermissionManager:
    """
    Manages user permissions for Yen Max.

    Structure on disk:
    {
        "global": [user_id, ...],           # allowed everywhere
        "servers": {
            "<guild_id>": [user_id, ...]    # allowed only in that server
        },
        "owner_id": user_id_or_null          # bot owner, bypasses all checks
    }

    Writes are atomic: data is written to a temp file in the same
    directory, then moved into place with os.replace, so a crash or
    power loss mid-write can never leave a half-written, corrupted
    JSON file behind.
    """

    def __init__(self, file_path: str = ALLOWED_USERS_FILE):
        self.file_path = file_path
        self._load()

    def _load(self) -> None:
        """Load permission data from file, migrating old flat-list format if found."""
        try:
            with open(self.file_path, "r") as f:
                data = json.load(f)

            if isinstance(data, list):
                # Old format was just a flat list of user IDs (global access)
                self.data = {"global": data, "servers": {}, "owner_id": None}
                self._save()
            elif isinstance(data, dict):
                self.data = {
                    "global": data.get("global", []),
                    "servers": data.get("servers", {}),
                    "owner_id": data.get("owner_id"),
                }
            else:
                self.data = {"global": [], "servers": {}, "owner_id": None}

        except FileNotFoundError:
            self.data = {"global": [], "servers": {}, "owner_id": None}
            self._save()
        except json.JSONDecodeError:
            self.data = {"global": [], "servers": {}, "owner_id": None}

    def _save(self) -> None:
        """
        Atomically save permission data to file.

        Writes to a temp file in the same directory first, then uses
        os.replace to move it into place - this is atomic on both
        POSIX and Windows, so the real file on disk is either the old
        complete version or the new complete version, never a partial write.
        """
        directory = os.path.dirname(os.path.abspath(self.file_path)) or "."

        try:
            fd, temp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(self.data, f, indent=4)
                os.replace(temp_path, self.file_path)
            except Exception:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise
        except IOError as e:
            print(f"Failed to save permissions: {e}")

    # ================= OWNER =================

    def is_owner(self, user_id: int) -> bool:
        """Check if this user is the registered bot owner."""
        return self.data.get("owner_id") == user_id

    def set_owner(self, user_id: int) -> None:
        """Register the bot owner. Owner bypasses all permission checks."""
        self.data["owner_id"] = user_id
        self._save()

    # ================= ACCESS CHECKS =================

    def is_allowed(self, user_id: int, guild_id: int = None) -> bool:
        """
        Check if a user has access, either globally or in the given server.
        The owner always has access.
        """
        if self.is_owner(user_id):
            return True

        if user_id in self.data.get("global", []):
            return True

        if guild_id is not None:
            server_list = self.data.get("servers", {}).get(str(guild_id), [])
            if user_id in server_list:
                return True

        return False

    # ================= GLOBAL GRANTS =================

    def grant(self, user_id: int) -> bool:
        """Grant global access to a user (all servers)."""
        if user_id not in self.data["global"]:
            self.data["global"].append(user_id)
            self._save()
            return True
        return False

    def revoke(self, user_id: int) -> bool:
        """Revoke global access from a user."""
        if user_id in self.data["global"]:
            self.data["global"].remove(user_id)
            self._save()
            return True
        return False

    # ================= PER-SERVER GRANTS =================

    def grant_server(self, user_id: int, guild_id: int) -> bool:
        """Grant access to a user, scoped to a single server."""
        key = str(guild_id)
        self.data["servers"].setdefault(key, [])

        if user_id not in self.data["servers"][key]:
            self.data["servers"][key].append(user_id)
            self._save()
            return True
        return False

    def revoke_server(self, user_id: int, guild_id: int) -> bool:
        """Revoke a user's server-scoped access."""
        key = str(guild_id)
        server_list = self.data["servers"].get(key, [])

        if user_id in server_list:
            server_list.remove(user_id)
            self._save()
            return True
        return False

    def list_all(self) -> list:
        """Get list of all globally allowed users."""
        return self.data.get("global", []).copy()

    def list_server(self, guild_id: int) -> list:
        """Get list of users allowed in a specific server."""
        return self.data.get("servers", {}).get(str(guild_id), []).copy()
