#!/usr/bin/env python3
"""Create a separate login for the CLOUD and print it as one line of text.  (run once, on your PC)

  py -3.11 .\\tools\\make_cloud_session.py

It asks for your phone number and the code Telegram sends you (and your 2-step password if you have one).
It prints a long text line: paste it as the secret TG_SESSION in GitHub. Treat that line like a password -
anyone who has it can use your Telegram account. Nothing is saved to disk.

This is a NEW login, separate from the one your PC already uses, so neither disturbs the other.
Afterwards Telegram shows it under Settings > Devices.
"""

import asyncio

from telethon import TelegramClient
from telethon.sessions import StringSession

from pvt_common import load_config


async def make_session_string(api_id, api_hash, client_factory=TelegramClient):
    client = client_factory(StringSession(), api_id, api_hash)
    await client.start()                      # asks for phone, code, 2-step password on the screen
    try:
        me = await client.get_me()
        return StringSession.save(client.session), me
    finally:
        await client.disconnect()


def main():
    config = load_config(need_destination=False)
    text, me = asyncio.run(make_session_string(config["api_id"], config["api_hash"]))
    print("\nLogged in as user ID", me.id)
    print("\n=========== COPY EVERYTHING BETWEEN THE LINES (one long line) ===========")
    print(text)
    print("=========================================================================")
    print("\nPaste it as the GitHub secret named TG_SESSION. Do not share it or save it in a file.")


if __name__ == "__main__":
    main()
