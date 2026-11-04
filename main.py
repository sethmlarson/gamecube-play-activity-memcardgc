import argparse
import io
import socket
import sys

import urllib3
import pypresence
import time
import ftplib
import re
import pathlib
import sqlite3
import tqdm
import colorama

ROOT_DIR = pathlib.Path(__file__).absolute().parent
with (ROOT_DIR / "wiitdb.txt").open(mode="r") as f:
    GAME_ID_TO_NAMES = {
        gid: name
        for gid, name in re.findall(
            r"^([A-Z0-9]{6}) = (.+?)$", f.read(), re.DOTALL | re.MULTILINE
        )
    }


class MemcardGCPresence:
    def __init__(
        self,
        memcardgc_host: str,
        memcardgc_ftp_username: str | None = None,
        memcardgc_ftp_password: str | None = None,
        discord_app_id: str | None = None,
    ):
        self.host = memcardgc_host
        self.http = urllib3.HTTPConnectionPool(memcardgc_host)
        self.active_game_id: str | None = None
        self.active_game_save_size: int | None = None
        self.active_game_session_id: int | None = None
        self.active_game_name: str | None = None
        self.consecutive_errors = 0

        self.ftp_username = memcardgc_ftp_username
        self.ftp_password = memcardgc_ftp_password
        self.ftp_last_modified: dict[str, str] = {}
        self.db = self.maybe_create_database()

        if discord_app_id is not None:
            self.discord = pypresence.Presence(client_id=discord_app_id)
            self.discord.connect()
        else:
            self.discord = None

    @staticmethod
    def maybe_create_database():
        db_path = ROOT_DIR / "memcardgc.sqlite"
        new_db = not db_path.exists()
        db = sqlite3.connect(db_path)
        if new_db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                  id INTEGER PRIMARY KEY,
                  game_id STRING,
                  game_id_with_revision STRING,
                  game_name STRING,
                  started_at INTEGER,
                  duration INTEGER
                );
            """
            )
        return db

    def poll_forever(self):
        try:
            while True:
                poll_start = time.time()
                self.poll_once()
                # Only set Discord status every 15 seconds.
                poll_duration = time.time() - poll_start
                time.sleep(max([0, 15 - poll_duration]))
        finally:
            self.reset()
            self.close()

    def poll_once(self) -> None:
        try:
            resp = self.http.request(
                "GET", "/api/currentState", timeout=3, redirect=False, retries=False
            )
            if resp.status != 200:
                raise ValueError("Invalid HTTP status")
            data = resp.json()
            game_id_with_revision = data["gameID"]
            game_id = game_id_with_revision[:6]
            game_name = data["gameName"]
            self.consecutive_errors = 0
        except (urllib3.exceptions.HTTPError, ValueError, KeyError) as e:
            self.consecutive_errors += 1
            if self.active_game_id and self.consecutive_errors > 3:
                self.reset()
            return

        # Game ID isn't a known game. Might be the default
        # memory card or a ROM hack that we don't know about.
        if game_id not in GAME_ID_TO_NAMES:
            self.reset()
            return

        # New game, set Rich Presence.
        if game_id_with_revision != self.active_game_id:
            print(
                f"[{colorama.Fore.GREEN}⏵{colorama.Style.RESET_ALL}] Started playing {colorama.Fore.LIGHTMAGENTA_EX}{game_id_with_revision}{colorama.Style.RESET_ALL} (“{game_name}”)"
            )
            self.start_session(game_id_with_revision, game_name)
            if self.discord:
                self.discord.update(
                    activity_type=pypresence.ActivityType.PLAYING,
                    state=game_name,
                    start=int(time.time()),
                    large_image=game_id.lower(),
                )
            self.active_game_id = game_id_with_revision
            self.active_game_name = game_name

        self.maybe_update_session()
        self.maybe_transfer_save()

    def start_session(self, game_id: str, game_name: str) -> None:
        assert len(game_id) == 8
        if self.active_game_session_id is not None:
            (active_game_id,) = self.db.execute(
                "SELECT game_id_with_revision FROM sessions WHERE id = ?;",
                (self.active_game_session_id,),
            ).fetchone()
            if game_id == active_game_id:
                return
        cursor = self.db.cursor()
        cursor.execute(
            "INSERT INTO sessions (game_id, game_id_with_revision, game_name, started_at, duration) VALUES (?, ?, ?, 0);",
            (game_id[:6], game_id, game_name, int(time.time())),
        )
        self.db.commit()
        self.active_game_session_id = cursor.lastrowid

    def maybe_update_session(self):
        if self.active_game_session_id:
            self.db.execute(
                "UPDATE sessions SET duration = ROUND(STRFTIME('%s', 'now') - started_at) WHERE id = ?",
                (self.active_game_session_id,),
            )
            self.db.commit()

    def maybe_transfer_save(self):
        if not self.ftp_username or not self.ftp_password or not self.active_game_id:
            return
        try:
            ftp = ftplib.FTP(
                self.host,
                user=self.ftp_username,
                passwd=self.ftp_password,
                timeout=3,
            )
            ftp.cwd(f"MemoryCards/{self.active_game_id}")

            # Check the directory contents and 'last modified'
            # if it hasn't changes since last time we don't
            # transfer the save.
            buffer = io.StringIO()
            ftp.retrlines("LIST", buffer.write)
            last_modified = buffer.getvalue()

            if self.ftp_last_modified.get(self.active_game_id) == last_modified:
                return

            save_path = pathlib.Path(
                f"saves/{self.active_game_id}/{self.active_game_id}-{int(time.time())}.raw"
            )
            save_path.parent.mkdir(exist_ok=True)

            with save_path.open(mode="wb") as f:
                transferred = 0
                # Estimate 2MiB if we don't know yet.
                total = self.active_game_save_size or (2 * 1024 * 1024)
                tq = tqdm.tqdm(
                    unit_divisor=1024,
                    unit_scale=True,
                    unit="B",
                    total=total,
                )

                def write_callback(data: bytes):
                    nonlocal f, tq, transferred
                    transferred += len(data)
                    tq.update(len(data))
                    f.write(data)

                ftp.retrbinary(
                    # TODO: Support 'channel'.
                    f"RETR {self.active_game_id}-1.raw",
                    write_callback,
                    blocksize=10 * 1024 * 1024,
                )

            # Update the cache entry after success.
            self.ftp_last_modified[self.active_game_id] = last_modified
            if self.active_game_save_size is None:
                self.active_game_save_size = transferred

        except (ftplib.Error, EOFError, socket.error, OSError) as e:
            print(
                f"[{colorama.Fore.RED}!{colorama.Style.RESET_ALL}] Failed to retrieve save: {e}"
            )

    def reset(self) -> None:
        if self.active_game_id is not None:
            game_id = self.active_game_id[:6]
            game_name = GAME_ID_TO_NAMES[game_id]
            print(
                f"[{colorama.Fore.RED}⏹{colorama.Style.RESET_ALL}] Stopped playing {colorama.Fore.LIGHTMAGENTA_EX}{game_id}{colorama.Style.RESET_ALL} (“{game_name}”)"
            )
        self.consecutive_errors = 0
        self.active_game_id = None
        self.active_game_save_size = None
        self.active_game_session_id = None
        self.active_game_name = None
        if self.discord:
            self.discord.clear()

    def list_memory_cards(self):
        start = 0
        limit = 100
        memory_cards = []
        while True:
            resp = self.http.request(
                "POST", "/api/query", json={"start": start, "limit": limit}
            )
            if resp.status == 404:
                break
            elif resp.status != 200:
                raise ValueError(f"Invalid HTTP status? {resp.status} {resp.data}")
            results = resp.json()["results"]
            memory_cards.extend(results)
            if len(results) < limit:
                break
            start += limit
        return memory_cards

    def close(self) -> None:
        self.http.close()
        self.http = None
        if self.discord:
            try:
                self.discord.clear()
            except Exception:
                pass
            self.discord.close()
            self.discord = None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--memcardgc-host", type=str, required=True)
    parser.add_argument("--memcardgc-ftp-username", type=str, required=True)
    parser.add_argument("--memcardgc-ftp-password", type=str, required=True)
    parser.add_argument("--discord-app-id", type=str, required=False)
    args = parser.parse_args(sys.argv[1:])

    memcardgc = MemcardGCPresence(
        memcardgc_host=args.memcardgc_host,
        memcardgc_ftp_username=args.memcardgc_ftp_username,
        memcardgc_ftp_password=args.memcardgc_ftp_password,
        discord_app_id=args.discord_app_id,
    )
    memcardgc.poll_forever()


if __name__ == "__main__":
    main()
