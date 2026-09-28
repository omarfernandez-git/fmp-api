"""Cliente HTTP para fmpadel.com (ASP.NET WebForms con postbacks)."""
import hashlib
import logging
import time

import requests
from bs4 import BeautifulSoup

from .config import BASE_URL, FMP_PASS, FMP_USER, HTML_CACHE

log = logging.getLogger("fmp.client")


class FMPClient:
    def __init__(self, cache: bool = True, delay: float = 0.15):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = (
            "Mozilla/5.0 (X11; Linux x86_64) fmp-scraper/0.1 (uso personal)"
        )
        self.cache = cache
        self.delay = delay
        self.logged_in = False
        if cache:
            HTML_CACHE.mkdir(parents=True, exist_ok=True)

    # ---------- utilidades ----------
    @staticmethod
    def soup(html: str) -> BeautifulSoup:
        return BeautifulSoup(html, "lxml")

    @staticmethod
    def hidden_fields(soup: BeautifulSoup) -> dict:
        return {
            i["name"]: i.get("value", "")
            for i in soup.select("input[type=hidden]")
            if i.get("name")
        }

    def _cache_path(self, key: str):
        return HTML_CACHE / (hashlib.sha1(key.encode()).hexdigest() + ".html")

    def get(self, path: str, use_cache: bool | None = None) -> str:
        url = path if path.startswith("http") else f"{BASE_URL}/{path}"
        use_cache = self.cache if use_cache is None else use_cache
        cp = self._cache_path("GET " + url)
        if use_cache and cp.exists():
            return cp.read_text(encoding="utf-8")
        time.sleep(self.delay)
        r = self.s.get(url, timeout=60)
        r.raise_for_status()
        if use_cache:
            cp.write_text(r.text, encoding="utf-8")
        return r.text

    def postback(self, path: str, soup: BeautifulSoup, target: str, values: dict,
                 use_cache: bool | None = None) -> str:
        """Simula el __doPostBack de un control (p.ej. un <select> con autopostback)."""
        url = path if path.startswith("http") else f"{BASE_URL}/{path}"
        data = self.hidden_fields(soup)
        data["__EVENTTARGET"] = target
        data["__EVENTARGUMENT"] = ""
        data.update(values)
        use_cache = self.cache if use_cache is None else use_cache
        key = "POST " + url + " " + repr(sorted(values.items())) + " " + target
        cp = self._cache_path(key)
        if use_cache and cp.exists():
            return cp.read_text(encoding="utf-8")
        time.sleep(self.delay)
        r = self.s.post(url, data=data, timeout=60)
        r.raise_for_status()
        if use_cache:
            cp.write_text(r.text, encoding="utf-8")
        return r.text

    # ---------- login (opcional) ----------
    def login(self) -> bool:
        """Inicia sesión en el área privada. No hace falta para los datos públicos."""
        if not FMP_USER or not FMP_PASS:
            log.info("Sin credenciales FMP_USER/FMP_PASS: se continúa sin login")
            return False
        html = self.get("ligas.aspx", use_cache=False)
        soup = self.soup(html)
        data = self.hidden_fields(soup)
        data.update({
            "ctl00$tipoLogin": "",
            "ctl00$tbUsuario": FMP_USER,
            "ctl00$tbPassword": FMP_PASS,
            "ctl00$ButtonLogin": "Ingresar",
        })
        r = self.s.post(f"{BASE_URL}/ligas.aspx", data=data, timeout=60)
        self.logged_in = "tbPassword" not in r.text
        log.info("Login %s", "OK" if self.logged_in else "FALLIDO")
        return self.logged_in
