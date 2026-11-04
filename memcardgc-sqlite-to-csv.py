import csv
import datetime
import sqlite3

db = sqlite3.connect("memcardgc.sqlite")


def main():
    # Group by day.
    durations = {}
    for game_id, game_name, started_at, duration in db.execute(
        "SELECT game_id, game_name, started_at, duration FROM sessions"
    ):
        started_date = datetime.datetime.fromtimestamp(started_at).strftime("%Y-%m-%d")
        key = (game_id, game_name, started_date)
        durations.setdefault(key, 0)
        durations[key] += duration

    with open("memcardgc.csv", mode="w") as f:
        f.truncate()
        writer = csv.writer(f)
        for (game_id, game_name, started_date), duration in sorted(
            durations.items(), key=lambda kv: kv[0][::-1]
        ):
            writer.writerow((started_date, game_id, game_name, duration))


if __name__ == "__main__":
    main()
