# GameCube Play Activity “Memcard Pro GC”

Play monitoring for the Nintendo GameCube using the [MemCard Pro GC](https://8bitmods.com/memcard-pro-gc-for-gamecube-frost-white/)
combined with a "Game ID"-compatible launcher like [Swiss](https://github.com/emukidid/swiss-gc) or the [FlippyDrive](https://docs.flippydrive.com/).
Runs in the background as a service on your personal computer and collects data
on which games you play and for how long.

I wrote a blog post about how to “[set your Discord status from a physical GameCube console](https://sethmlarson.dev/setting-discord-status-from-physical-gamecube-console)”
which uses this tool. The Discord Rich Presence functionality is optional.

```terminal
git clone ssh://git@github.com/sethmlarson/gamecube-play-activity-memcardgc
cd gamecube-play-activity-memcardgc

# Install Python dependencies
python -m venv venv
venv/bin/python -m pip install -r requirements.txt

# Update Wii & GameCube database
curl --output wiitdb.txt https://www.gametdb.com/wiitdb.txt?LANG=ORIG

# Run with commands
python main.py \
  --memcardgc-host=192.168.0.43 \
  --memcardgc-ftp-username=... \
  --memcardgc-ftp-password=... \
  --discord-app-id=...
```

## License

CC BY-NC-SA 4.0
