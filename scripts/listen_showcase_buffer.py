#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time

from buffer_client import post_text, post_thread
from scripts.discussion_buffer import load_posts


def main() -> None:
    # Validate the newly grounded discussion bank before any public posting.
    posts = load_posts()

    discussion = posts[0]["text"].strip()
    archive = (
        "『国宝』を観たあと、“伝統”と“狂気”の境目がずっと頭から離れませんでした。\n\n"
        "梨園という閉じた世界、芸への執念、美しさと怖さ。リルパルでじっくり話しています。🎭\n"
        "https://listen.style/p/reelpal/t8osyl08\n"
        "#リルパル"
    )
    survey = (
        "次にリルパルで語ってほしい映画、ありますか？🎬\n\n"
        "名作でも怪作でもB級でも大歓迎。『この映画を二人にぶつけたい』という一本を送ってください。\n"
        "https://forms.gle/4PT2GBA7TY8vAoCx7\n"
        "#リルパル"
    )
    digest = [
        "リルパルの沼から、考察が止まらなくなる3本を置いていきます。\n\n宗教、偏見、伝統。映画から別の景色が見えてくる回です。🎬\n#リルパル",
        "①『ウィッチ』\n怪異そのものより、“彼らは何を怖いと信じているのか”を読むと一気に面白くなる。\nhttps://listen.style/p/reelpal/dwmq5bvt",
        "②『ベイブ』『ベイブ都会へいく』\nかわいい子豚のコメディの奥に、偏見・差別・社会の役割を読む。\nhttps://listen.style/p/reelpal/c9trures",
        "③『国宝』\n伝統と狂気は、どこで分かれるのか。芸への執念と美の怖さを語りました。\nhttps://listen.style/p/reelpal/t8osyl08",
    ]

    print("[1/4] posting LISTEN-grounded discussion")
    print(post_text(discussion))
    time.sleep(30)

    print("[2/4] posting archive promo")
    print(post_text(archive))
    time.sleep(30)

    print("[3/4] posting listener participation CTA")
    print(post_text(survey))
    time.sleep(30)

    print("[4/4] posting archive digest thread")
    print(post_thread(digest))
    print("[OK] LISTEN-grounded showcase completed")


if __name__ == "__main__":
    main()
