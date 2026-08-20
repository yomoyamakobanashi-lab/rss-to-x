#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from buffer_client import post_text

TEXT = """“面白かった”で映画を終わらせたくない人へ。

『Reel Friends in TOKYO』は、まこ×オーマが新作・名作・怪作・B級映画まで、倫理／宗教／歴史／陰謀の視点から切り込む映画ポッドキャスト🎬

観て、語って、沼る。
🎧 https://open.spotify.com/show/4o8l9DJWMuwUht2pvkEytS
#リルパル #ReelPal"""


def main() -> None:
    post_id = post_text(TEXT)
    print(f"[OK] Buffer accepted ReelPal promo post: {post_id}")


if __name__ == "__main__":
    main()
