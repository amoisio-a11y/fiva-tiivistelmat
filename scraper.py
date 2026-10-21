#!/usr/bin/env python3
"""
Finanssivalvonnan tarkastusten tiivistelmät -> sähköposti.

Tiivistelmälista latautuu Fivan sivulla JavaScriptillä, joten uudet
tiivistelmät tunnistetaan sivuston sitemap.xml:stä: jokainen tiivistelmä on
oma alasivunsa polussa .../tarkastustoiminta/tarkastusten-tiivistelmat/<slug>.

Tila (jo nähdyt URLit) pidetään tiedostossa seen.json, jonka GitHub Actions
commitoi takaisin repoon. Ensimmäinen ajo vain tallentaa nykyiset
tiivistelmät eikä lähetä mitään.

Vain Pythonin standardikirjasto, ei riippuvuuksia.

Ympäristömuuttujat (GitHub Secrets, samat nimet kuin Lounas-repossa):
  GMAIL_APP_PASSWORD  lähettäjätilin Gmail-sovellussalasana
  RECIPIENTS          vastaanottaja(t), useampi pilkulla erotettuna
Valinnaiset:
  GMAIL_USER          lähettäjätili (oletus SENDER alla)
  SMTP_HOST, SMTP_PORT, SMTP_SSL (oletus smtp.gmail.com, 465, 1)
  FIVA_SITEMAP_URL    sitemapin osoite (testausta varten)

Käyttö:
  python scraper.py             normaali ajo
  python scraper.py --dry-run   näyttää mitä lähetettäisiin, ei lähetä eikä tallenna
"""

from __future__ import annotations

import html
import json
import os
import re
import smtplib
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

SITEMAP_URL = os.environ.get("FIVA_SITEMAP_URL", "https://www.finanssivalvonta.fi/sitemap.xml")
LIST_PATH = "/finanssisektorin-toimijalle/finanssivalvonnan-valvontatyo/tarkastustoiminta/tarkastusten-tiivistelmat/"
LIST_PAGE_URL = "https://www.finanssivalvonta.fi" + LIST_PATH
STATE_FILE = Path(__file__).with_name("seen.json")
SENDER = "postimestari777@gmail.com"  # sama lähettäjätili kuin Lounas-repossa

# Jos uusia on kerralla tätä enemmän, lähetetään yksi koosteviesti
# yksittäisten viestien sijaan (suojaa sähköpostitulvalta esim. jos
# sivuston URL-rakenne muuttuu ja kaikki näyttävät "uusilta").
MAX_SINGLE_MAILS = 8

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
TITLE_SUFFIX_RE = re.compile(r"\s*-\s*Tarkastusten tiivistelmät\s*-\s*www\.finanssivalvonta\.fi\s*$", re.I)
DATE_RE = re.compile(r"(?:Julkaistu|Päivitetty)\s*:?\s*(\d{1,2}\.\d{1,2}\.\d{4})", re.I)


# --------------------------------------------------------------------------- #
# Verkko
# --------------------------------------------------------------------------- #

def fetch(url: str, attempts: int = 3) -> str:
    last_err: Exception | None = None
    for i in range(attempts):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "fi-FI,fi;q=0.9"})
            with urlopen(req, timeout=30) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, errors="replace")
        except Exception as e:  # noqa: BLE001
            last_err = e
            if i < attempts - 1:
                time.sleep(5 * (i + 1))
    raise RuntimeError(f"Haku epäonnistui: {url}: {last_err}")


def normalize(url: str) -> str:
    """Yhtenäinen avain: ei kyselyä/fragmenttia, ei loppukauttaviivaa, pienet kirjaimet polussa."""
    parts = urlsplit(url.strip())
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/").lower()
    return urlunsplit(("https", parts.netloc.lower(), path, "", ""))


def is_summary(url: str) -> bool:
    path = re.sub(r"/{2,}", "/", urlsplit(url).path).lower()
    if not path.startswith(LIST_PATH):
        return False
    rest = path[len(LIST_PATH):].strip("/")
    return bool(rest) and "/" not in rest


def fetch_sitemap() -> dict[str, dict]:
    """Palauttaa {normalisoitu_url: {"url": alkuperäinen, "lastmod": str}}."""
    xml_text = fetch(SITEMAP_URL)
    root = ET.fromstring(xml_text.encode("utf-8"))
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

    # Sitemap-indeksi: käy läpi alisitemapit
    if root.tag.endswith("sitemapindex"):
        result: dict[str, dict] = {}
        for loc in root.findall("sm:sitemap/sm:loc", ns):
            sub = ET.fromstring(fetch(loc.text.strip()).encode("utf-8"))
            result.update(_collect(sub, ns))
        return result
    return _collect(root, ns)


def _collect(root: ET.Element, ns: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for u in root.findall("sm:url", ns):
        loc = (u.findtext("sm:loc", default="", namespaces=ns) or "").strip()
        if loc and is_summary(loc):
            lastmod = (u.findtext("sm:lastmod", default="", namespaces=ns) or "").strip()
            out[normalize(loc)] = {"url": loc, "lastmod": lastmod}
    return out


# --------------------------------------------------------------------------- #
# Tiivistelmäsivun tiedot
# --------------------------------------------------------------------------- #

def _meta(page: str, key: str) -> str:
    for pattern in (
        rf'<meta[^>]+(?:property|name)=["\']{re.escape(key)}["\'][^>]*content=["\']([^"\']*)["\']',
        rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\']{re.escape(key)}["\']',
    ):
        m = re.search(pattern, page, re.I)
        if m and m.group(1).strip():
            return html.unescape(m.group(1).strip())
    return ""


def _text(fragment: str) -> str:
    fragment = re.sub(r"<(script|style)\b.*?</\1>", " ", fragment, flags=re.S | re.I)
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"[ \t\r\f\v]+", " ", html.unescape(fragment)).strip()


def _lead_paragraphs(page: str, max_chars: int = 700) -> str:
    start = -1
    for marker in ('id="page-main-content"', "<main", "<article", "<h1"):
        start = page.find(marker)
        if start != -1:
            break
    if start == -1:
        return ""
    end = page.find("<footer", start)
    body = page[start:end if end != -1 else None]
    paras: list[str] = []
    total = 0
    for m in re.finditer(r"<p\b[^>]*>(.*?)</p>", body, re.S | re.I):
        t = _text(m.group(1))
        if len(t) < 40:  # ohita lyhyet rivit (päivämäärät, linkit tms.)
            continue
        paras.append(t)
        total += len(t)
        if total >= max_chars:
            break
    lead = "\n\n".join(paras)
    if len(lead) > max_chars:
        lead = lead[:max_chars].rsplit(" ", 1)[0] + " …"
    return lead


def title_from_slug(url: str) -> str:
    slug = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"-(tiivistelma|sammanfattning|summary)$", "", slug)
    return slug.replace("-", " ").capitalize()


def page_details(url: str) -> dict:
    page = fetch(url)
    title = _meta(page, "og:title")
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
        title = _text(m.group(1)) if m else ""
    title = TITLE_SUFFIX_RE.sub("", title).strip() or title_from_slug(url)

    lead = _meta(page, "description") or _meta(page, "og:description") or _lead_paragraphs(page)
    m = DATE_RE.search(_text(page))
    date = m.group(1) if m else ""
    return {"title": title, "lead": lead, "date": date}


# --------------------------------------------------------------------------- #
# Sähköposti
# --------------------------------------------------------------------------- #

def _fmt_lastmod(lastmod: str) -> str:
    try:
        d = datetime.fromisoformat(lastmod.replace("Z", "+00:00"))
        return f"{d.day}.{d.month}.{d.year}"
    except ValueError:
        return ""


def _clean_title(title: str) -> str:
    return re.sub(r"\s*\((tiivistelmä|sammanfattning|summary)\)\s*$", "", title, flags=re.I)


def build_single(item: dict) -> EmailMessage:
    title = item["title"]
    date = item.get("date") or _fmt_lastmod(item.get("lastmod", ""))
    msg = EmailMessage()
    msg["Subject"] = f"Fiva-tiivistelmä: {_clean_title(title)}"

    lines = [title, ""]
    if date:
        lines.append(f"Päivämäärä: {date}")
    if item.get("lead"):
        lines += ["", item["lead"]]
    lines += ["", f"Lue koko tiivistelmä: {item['url']}", "", f"Kaikki tiivistelmät: {LIST_PAGE_URL}"]
    msg.set_content("\n".join(lines))

    e = html.escape
    lead_html = "".join(f"<p>{e(p)}</p>" for p in item.get("lead", "").split("\n\n") if p)
    msg.add_alternative(
        f"""<html><body style="font-family:Arial,sans-serif;font-size:14px;color:#222;max-width:640px">
<p style="color:#666;margin:0 0 4px">Finanssivalvonta – uusi tarkastuksen tiivistelmä{(' · ' + e(date)) if date else ''}</p>
<h2 style="margin:0 0 12px;font-size:18px"><a href="{e(item['url'])}" style="color:#004a8f">{e(title)}</a></h2>
{lead_html}
<p><a href="{e(item['url'])}">Lue koko tiivistelmä →</a></p>
<p style="color:#888;font-size:12px;margin-top:24px">Kaikki tiivistelmät: <a href="{e(LIST_PAGE_URL)}">{e(LIST_PAGE_URL)}</a></p>
</body></html>""",
        subtype="html",
    )
    return msg


def build_digest(items: list[dict]) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = f"Fiva: {len(items)} uutta tarkastuksen tiivistelmää"
    text = [f"{len(items)} uutta tiivistelmää:", ""]
    rows = []
    e = html.escape
    for it in items:
        date = it.get("date") or _fmt_lastmod(it.get("lastmod", ""))
        text.append(f"- {it['title']}{f' ({date})' if date else ''}\n  {it['url']}")
        rows.append(f'<li style="margin-bottom:8px"><a href="{e(it["url"])}">{e(it["title"])}</a>'
                    f'{f" <span style=color:#666>({e(date)})</span>" if date else ""}</li>')
    text += ["", f"Kaikki tiivistelmät: {LIST_PAGE_URL}"]
    msg.set_content("\n".join(text))
    msg.add_alternative(
        f"""<html><body style="font-family:Arial,sans-serif;font-size:14px;color:#222;max-width:640px">
<h2 style="font-size:18px">{len(items)} uutta Finanssivalvonnan tarkastuksen tiivistelmää</h2>
<ul>{''.join(rows)}</ul>
<p style="color:#888;font-size:12px">Kaikki tiivistelmät: <a href="{e(LIST_PAGE_URL)}">{e(LIST_PAGE_URL)}</a></p>
</body></html>""",
        subtype="html",
    )
    return msg


class Mailer:
    def __init__(self) -> None:
        self.user = os.environ.get("GMAIL_USER") or SENDER
        self.password = os.environ.get("GMAIL_APP_PASSWORD", "")
        self.to = [a.strip() for a in os.environ.get("RECIPIENTS", "").split(",") if a.strip()]
        self.host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
        self.port = int(os.environ.get("SMTP_PORT", "465"))
        self.ssl = os.environ.get("SMTP_SSL", "1") != "0"

    def check(self) -> None:
        missing = [n for n, v in (("GMAIL_APP_PASSWORD", self.password), ("RECIPIENTS", self.to)) if not v]
        if missing:
            raise SystemExit(f"Puuttuvat ympäristömuuttujat: {', '.join(missing)}")

    def send(self, msg: EmailMessage) -> None:
        msg["From"] = f"Fiva-tiivistelmät <{self.user}>"
        msg["To"] = ", ".join(self.to)
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain="fiva-tiivistelmat.local")
        smtp_cls = smtplib.SMTP_SSL if self.ssl else smtplib.SMTP
        with smtp_cls(self.host, self.port, timeout=30) as s:
            if self.password and self.ssl:
                s.login(self.user, self.password)
            s.send_message(msg)


# --------------------------------------------------------------------------- #
# Tila
# --------------------------------------------------------------------------- #

def load_state() -> dict | None:
    if not STATE_FILE.exists():
        return None
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    # Huom: ei korvata state["seen"]-oliota, jotta kutsujan viittaus pysyy voimassa.
    out = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seen": dict(sorted(state["seen"].items())),
    }
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_FILE)


# --------------------------------------------------------------------------- #
# Pääohjelma
# --------------------------------------------------------------------------- #

def main() -> int:
    dry_run = "--dry-run" in sys.argv

    entries = fetch_sitemap()
    print(f"Sitemapissa {len(entries)} tiivistelmää.")
    if not entries:
        # Älä koskaan tyhjennä tilaa: todennäköisesti sivuston rakenne on muuttunut.
        print("VIRHE: yhtään tiivistelmää ei löytynyt – onko sitemapin tai URL-polun rakenne muuttunut?")
        return 1

    state = load_state()
    if state is None:
        print("Ensimmäinen ajo: tallennetaan nykyiset tiivistelmät, ei lähetetä viestejä.")
        if not dry_run:
            save_state({"seen": {k: v["lastmod"] for k, v in entries.items()}})
        return 0

    seen: dict = state.setdefault("seen", {})
    new_keys = sorted((k for k in entries if k not in seen), key=lambda k: entries[k]["lastmod"])
    print(f"Uusia: {len(new_keys)}")
    if not new_keys:
        return 0

    items = []
    for k in new_keys:
        url = entries[k]["url"]
        try:
            details = page_details(url)
        except Exception as e:  # noqa: BLE001 – sivun haku ei saa estää ilmoitusta
            print(f"  varoitus: sivun tietoja ei saatu ({e}); käytetään URLia.")
            details = {"title": title_from_slug(url), "lead": "", "date": ""}
        items.append({"key": k, "url": url, "lastmod": entries[k]["lastmod"], **details})
        print(f"  + {details['title']}")

    if dry_run:
        print("--dry-run: ei lähetetä eikä tallenneta.")
        return 0

    mailer = Mailer()
    mailer.check()
    failed = 0

    if len(items) > MAX_SINGLE_MAILS:
        mailer.send(build_digest(items))
        for it in items:
            seen[it["key"]] = it["lastmod"]
        save_state(state)
        print(f"Lähetettiin koosteviesti ({len(items)} tiivistelmää).")
        return 0

    for it in items:
        try:
            mailer.send(build_single(it))
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  VIRHE lähetyksessä: {it['title']}: {e}")
            continue
        seen[it["key"]] = it["lastmod"]
        save_state(state)  # tallenna heti, ettei osittainen virhe aiheuta tuplaviestejä
        print(f"  lähetetty: {it['title']}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
