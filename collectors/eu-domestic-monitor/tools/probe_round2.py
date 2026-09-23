#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""probe_round2.py — 变体补测（只读）。"""
import time, urllib.request
from probe_sources import fetch, sniff

CANDIDATES = [
    ("lemonde-encont", "https://www.lemonde.fr/rss/en_continu.xml"),
    ("lemonde-en-int", "https://www.lemonde.fr/en/international/rss_full.xml"),
    ("franceinfo", "https://www.francetvinfo.fr/titres.rss"),
    ("france24-en-fra", "https://www.france24.com/en/france/rss"),
    ("lefigaro-econ", "https://www.lefigaro.fr/rss/figaro_economie.xml"),
    ("euronews-fr", "https://fr.euronews.com/rss"),
    ("euronews-it", "https://it.euronews.com/rss"),
    ("bruegel2", "https://www.bruegel.org/rss.xml"),
    ("delors", "https://institutdelors.eu/feed/"),
    ("montaigne", "https://www.institutmontaigne.org/rss.xml"),
    ("consilium2", "https://www.consilium.europa.eu/en/rss"),
    ("consilium-en-news", "https://www.consilium.europa.eu/en/newsroom/rss/"),
    ("elysee2", "https://www.elysee.fr/rss"),
    ("elysee3", "https://www.elysee.fr/feed"),
    ("governo-it", "https://www.governo.it/it/rss"),
    ("governo-it2", "https://www.governo.it/rss.xml"),
    ("corriere", "https://www.corriere.it/rss/homepage.xml"),
    ("euobserver2", "https://euobserver.com/rss.xml"),
    ("parlament-fr", "https://www.europarl.europa.eu/rss/doc/top-stories/fr.xml"),
    ("politicoeu2", "https://www.politico.eu/politics/feed/"),
]

def main():
    print("[proxy]", urllib.request.getproxies())
    for sid, url in CANDIDATES:
        st, body, ms = fetch(url)
        print(f"{sid:18s} {sniff(st, body):42s} {ms:5d}ms  {url[:80]}")
        time.sleep(1.2)

if __name__ == "__main__":
    main()
